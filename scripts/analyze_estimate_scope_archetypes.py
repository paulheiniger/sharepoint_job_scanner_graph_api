from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
load_dotenv(REPO_ROOT / ".env")

from jobscan.estimator.data_loader import load_estimator_data
from jobscan.estimator.schemas import EstimatorData
from jobscan.estimator.scope_archetypes import (
    ArchetypeAnalysisConfig,
    analyze_scope_archetypes,
    write_scope_archetype_analysis,
)
from jobscan.estimator.scope_archetype_review import (
    apply_review_overrides,
    build_stratified_review_queue,
    label_archetype_packets,
    load_review_workbook,
    merge_review_queue_edits,
    write_review_validation_artifacts,
)
from jobscan.estimator.scope_archetype_catalog import (
    build_advisory_scope_catalog,
    build_approved_scope_catalog,
    write_approved_scope_catalog,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build an offline estimate-level inclusion matrix, association rules, "
            "candidate scope archetypes, and AI review packets."
        )
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("NEON_DATABASE_URL") or os.getenv("DATABASE_URL"),
        help="Postgres URL. Defaults to NEON_DATABASE_URL or DATABASE_URL.",
    )
    parser.add_argument(
        "--template-rows-csv",
        type=Path,
        help="Optional CSV input for offline analysis without a database.",
    )
    parser.add_argument(
        "--estimates-csv",
        type=Path,
        help="Optional estimate-summary CSV used with --template-rows-csv.",
    )
    parser.add_argument(
        "--jobs-csv",
        type=Path,
        help="Optional job-context CSV used with --template-rows-csv.",
    )
    parser.add_argument(
        "--scope-text-csv",
        type=Path,
        help="Optional historical proposal scope-text CSV used with --template-rows-csv.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/estimate_scope_archetypes"),
    )
    parser.add_argument("--min-support-count", type=int, default=3)
    parser.add_argument("--min-archetype-jobs", type=int, default=3)
    parser.add_argument("--jaccard-threshold", type=float, default=0.58)
    parser.add_argument("--max-unknown-decision-rate", type=float, default=0.25)
    parser.add_argument(
        "--review-sample-size",
        type=int,
        default=90,
        help="Number of estimate observations in the stratified review workbook.",
    )
    parser.add_argument(
        "--review-overrides",
        type=Path,
        help="Completed review workbook or CSV whose corrections should be applied.",
    )
    parser.add_argument(
        "--false-positive-weight",
        type=float,
        default=3.0,
        help="Penalty applied to false-positive association predictions in holdouts.",
    )
    parser.add_argument(
        "--run-ai-review",
        action="store_true",
        help="Run bounded, cached AI labeling for the candidate archetype packets.",
    )
    parser.add_argument(
        "--ai-model",
        default=(
            os.getenv("OPENAI_SCOPE_ARCHETYPE_MODEL")
            or os.getenv("OPENAI_ESTIMATOR_MODEL")
            or "gpt-5.5"
        ),
    )
    parser.add_argument(
        "--max-ai-packets",
        type=int,
        help="Optional cap for paid AI review calls. Defaults to all packets.",
    )
    parser.add_argument(
        "--ai-cache-dir",
        type=Path,
        help="Cache directory. Defaults to OUTPUT_DIR/ai_cache.",
    )
    parser.add_argument(
        "--approved-catalog-output",
        type=Path,
        help=(
            "Versioned shadow-only catalog output. Defaults to "
            "OUTPUT_DIR/approved_scope_archetype_catalog.json."
        ),
    )
    parser.add_argument(
        "--advisory-catalog-output",
        type=Path,
        help=(
            "Evidence-only catalog containing AI-labeled archetypes and "
            "holdout-stable relationships. Defaults to "
            "OUTPUT_DIR/scope_pattern_evidence_catalog.json."
        ),
    )
    return parser.parse_args(argv)


def _read_optional(path: Path | None) -> pd.DataFrame:
    return pd.read_csv(path) if path else pd.DataFrame()


def load_analysis_data(args: argparse.Namespace) -> EstimatorData:
    if args.template_rows_csv:
        return EstimatorData(
            template_rows=pd.read_csv(args.template_rows_csv),
            estimates=_read_optional(args.estimates_csv),
            jobs=_read_optional(args.jobs_csv),
            historical_scope_texts=_read_optional(args.scope_text_csv),
        )
    if not args.database_url:
        raise RuntimeError(
            "Provide --database-url, set NEON_DATABASE_URL/DATABASE_URL, "
            "or provide --template-rows-csv."
        )
    return load_estimator_data(
        REPO_ROOT,
        database_url=args.database_url,
        prefer_database=True,
        load_profile="full",
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.min_support_count < 1 or args.min_archetype_jobs < 1:
        raise ValueError("Support thresholds must be positive integers.")
    if not 0 < args.jaccard_threshold <= 1:
        raise ValueError("--jaccard-threshold must be in (0, 1].")
    if not 0 <= args.max_unknown_decision_rate <= 1:
        raise ValueError("--max-unknown-decision-rate must be in [0, 1].")
    if args.review_sample_size < 1:
        raise ValueError("--review-sample-size must be positive.")
    if args.false_positive_weight < 1:
        raise ValueError("--false-positive-weight must be at least 1.")
    if args.max_ai_packets is not None and args.max_ai_packets < 0:
        raise ValueError("--max-ai-packets cannot be negative.")

    config = ArchetypeAnalysisConfig(
        min_support_count=args.min_support_count,
        min_archetype_jobs=args.min_archetype_jobs,
        jaccard_threshold=args.jaccard_threshold,
        max_unknown_decision_rate=args.max_unknown_decision_rate,
    )
    data = load_analysis_data(args)
    result = analyze_scope_archetypes(data, config=config)
    review_corrections = pd.DataFrame()
    reviewed_queue: dict[str, pd.DataFrame] = {}
    if args.review_overrides:
        reviewed_queue = load_review_workbook(args.review_overrides)
        result, review_corrections = apply_review_overrides(
            data,
            result,
            reviewed_queue,
            config=config,
        )
    paths = write_scope_archetype_analysis(result, args.output_dir)
    review_queue = build_stratified_review_queue(
        result,
        target_estimates=args.review_sample_size,
    )
    review_queue = merge_review_queue_edits(review_queue, reviewed_queue)
    ai_labels: list[dict[str, object]] = []
    if args.run_ai_review:
        cache_dir = args.ai_cache_dir or (args.output_dir / "ai_cache")
        total_input_characters = sum(
            len(str(packet)) for packet in result["ai_review_packets"]
        )
        print(
            "Running bounded AI archetype review: "
            f"{len(result['ai_review_packets'])} packet(s), "
            f"approximately {total_input_characters:,} source characters before compaction."
        )
        ai_labels = label_archetype_packets(
            result["ai_review_packets"],
            cache_dir=cache_dir,
            model=args.ai_model,
            max_packets=args.max_ai_packets,
        )
    review_paths = write_review_validation_artifacts(
        result,
        args.output_dir,
        review_queue=review_queue,
        ai_labels=ai_labels,
        review_corrections=review_corrections,
        false_positive_weight=args.false_positive_weight,
    )
    paths.update(review_paths)
    final_review = load_review_workbook(
        review_paths["scope_archetype_review.xlsx"]
    )
    approved_catalog = build_approved_scope_catalog(
        final_review["archetype_review"],
        final_review["rule_review"],
        source_metadata={
            "analysis_schema_version": "scope_archetype_analysis.v1",
            "review_schema_version": "scope_archetype_review.v1",
            "analysis_summary_sha256": hashlib.sha256(
                paths["analysis_summary.json"].read_bytes()
            ).hexdigest(),
            "review_validation_summary_sha256": hashlib.sha256(
                review_paths["review_validation_summary.json"].read_bytes()
            ).hexdigest(),
        },
    )
    approved_catalog_path = write_approved_scope_catalog(
        approved_catalog,
        args.approved_catalog_output
        or (args.output_dir / "approved_scope_archetype_catalog.json"),
    )
    paths[approved_catalog_path.name] = approved_catalog_path
    advisory_catalog = build_advisory_scope_catalog(
        final_review["archetype_review"],
        pd.read_csv(review_paths["rule_validation_summary.csv"]),
        source_metadata={
            "analysis_schema_version": "scope_archetype_analysis.v1",
            "review_schema_version": "scope_archetype_review.v1",
            "analysis_summary_sha256": hashlib.sha256(
                paths["analysis_summary.json"].read_bytes()
            ).hexdigest(),
            "review_validation_summary_sha256": hashlib.sha256(
                review_paths["review_validation_summary.json"].read_bytes()
            ).hexdigest(),
        },
    )
    advisory_catalog_path = write_approved_scope_catalog(
        advisory_catalog,
        args.advisory_catalog_output
        or (args.output_dir / "scope_pattern_evidence_catalog.json"),
    )
    paths[advisory_catalog_path.name] = advisory_catalog_path

    metrics = {
        str(row["metric"]): row["value"]
        for row in result["diagnostics"].to_dict(orient="records")
    }
    print("Estimate scope archetype analysis complete")
    print(f"Observations: {metrics.get('estimate_observations', 0)}")
    print(f"Training revisions: {metrics.get('training_selected_observations', 0)}")
    print(f"Candidate archetypes: {metrics.get('candidate_archetypes', 0)}")
    print(f"Association rules: {metrics.get('positive_association_rules', 0)}")
    print(f"Review estimates: {len(review_queue['estimate_review'])}")
    if args.run_ai_review:
        completed = sum(label.get("status") == "completed" for label in ai_labels)
        cached = sum(bool(label.get("cache_hit")) for label in ai_labels)
        print(f"AI archetype labels: {completed} completed ({cached} cache hits)")
    print(
        "Approved shadow catalog: "
        f"{approved_catalog['approved_archetype_count']} archetype(s), "
        f"{approved_catalog['approved_rule_count']} rule(s)"
    )
    print(
        "Advisory evidence catalog: "
        f"{advisory_catalog['advisory_archetype_count']} archetype(s), "
        f"{advisory_catalog['advisory_rule_count']} stable relationship(s)"
    )
    print(f"Output directory: {args.output_dir}")
    for filename in sorted(paths):
        print(f"- {filename}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
