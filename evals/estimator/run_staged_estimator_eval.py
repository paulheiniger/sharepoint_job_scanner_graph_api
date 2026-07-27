from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evals.estimator.run_estimator_eval import load_data_for_eval
from jobscan.estimator.model_routing import configured_estimator_models
from jobscan.estimator.staged_session import advance_estimate_session


DEFAULT_CASES_PATH = (
    REPO_ROOT
    / "evals"
    / "estimator"
    / "curated_staged_cases.json"
)
ACTIONABLE_KINDS = {
    "material",
    "labor",
    "equipment",
    "travel",
    "overhead_profit",
    "tax",
}
GENERIC_ITEM_NAMES = {
    "",
    "product",
    "type",
    "days",
    "job name:",
    "job type:",
    "site address:",
    "today's date:",
}
PROMOTABLE_STATUSES = {
    "approved",
    "promoted",
    "reviewed",
}


def load_staged_cases(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Staged estimator cases not found: {path}")
    if path.suffix.lower() == ".jsonl":
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload if isinstance(payload, list) else payload.get("cases") or []
    return [
        normalize_staged_case(row)
        for row in rows
        if isinstance(row, dict)
        and _promotion_status(row) != "rejected"
    ]


def _promotion_status(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    source_metadata = (
        row.get("source_metadata")
        if isinstance(row.get("source_metadata"), dict)
        else {}
    )
    return str(
        row.get("promotion_status")
        or metadata.get("promotion_status")
        or source_metadata.get("promotion_status")
        or ""
    ).strip().lower()


def normalize_staged_case(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    source_metadata = (
        row.get("source_metadata")
        if isinstance(row.get("source_metadata"), dict)
        else {}
    )
    return {
        "case_id": row.get("case_id"),
        "notes": (
            row.get("generated_notes")
            or row.get("notes")
            or row.get("generated_notes_original")
            or ""
        ),
        "template_type": row.get("template_type") or metadata.get("template_type") or "",
        "expected_scope": (
            row.get("expected_scope_fields")
            or row.get("expected_scope")
            or row.get("expected")
            or {}
        ),
        "expected_decisions": [
            dict(item)
            for item in row.get("expected_decisions") or []
            if isinstance(item, dict)
        ],
        "expected_workbook_rows": row.get("expected_workbook_rows") or [],
        "source_metadata": {
            "source_job_id": (
                row.get("source_job_id")
                or metadata.get("source_job_id")
                or source_metadata.get("source_job_id")
            ),
            "source_file": (
                row.get("source_file")
                or metadata.get("source_file")
                or source_metadata.get("source_file")
            ),
            "promotion_status": (
                row.get("promotion_status")
                or metadata.get("promotion_status")
                or source_metadata.get("promotion_status")
            ),
            "review_method": (
                metadata.get("review_method")
                or source_metadata.get("review_method")
            ),
            "selection_policy": (
                metadata.get("selection_policy")
                or source_metadata.get("selection_policy")
                or "exact"
            ),
        },
    }


def promote_reviewed_cases(
    cases: Iterable[dict[str, Any]],
    output_path: Path,
) -> dict[str, Any]:
    """Write only explicitly reviewed cases to a curated benchmark artifact."""

    promoted = [
        dict(case)
        for case in cases
        if str(
            (case.get("source_metadata") or {}).get("promotion_status") or ""
        ).strip().lower()
        in PROMOTABLE_STATUSES
    ]
    payload = {
        "schema_version": 1,
        "benchmark_status": "curated",
        "case_count": len(promoted),
        "cases": promoted,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return payload


def run_staged_case(
    case: dict[str, Any],
    *,
    model: str,
    data: Any = None,
    provider: Callable[[list[dict[str, Any]], str], Any] | None = None,
) -> dict[str, Any]:
    started_at = time.monotonic()
    result, state = advance_estimate_session(
        [{"role": "user", "content": str(case.get("notes") or "")}],
        data=data,
        template_type_hint=str(case.get("template_type") or ""),
        provider=provider,
        model=model,
    )
    score = score_staged_state(case, state)
    elapsed_seconds = round(time.monotonic() - started_at, 3)
    return {
        "case_id": case.get("case_id"),
        "model": model,
        "source_metadata": case.get("source_metadata") or {},
        "score": score,
        "session_summary": {
            "status": state.get("session_status"),
            "confidence": (state.get("confidence_summary") or {}).get("overall"),
            "response_source": result.source,
            "scope": state.get("scope_state") or {},
            "decision_count": len(state.get("decision_template_state") or []),
            "question_count": len(state.get("unresolved_questions") or []),
            "questions": list(state.get("unresolved_questions") or []),
            "assumptions": list(state.get("assumptions") or []),
            "warning_count": len(state.get("review_flags") or []),
            "warnings": list(state.get("review_flags") or []),
            "assistant_message": str(result.assistant_message or ""),
            "elapsed_seconds": elapsed_seconds,
            "included_decision_buckets": sorted(
                {
                    _token(row.get("template_bucket"))
                    for row in state.get("decision_template_state") or []
                    if isinstance(row, dict)
                    and row.get("include") is True
                    and _token(row.get("template_bucket"))
                }
            ),
            "model_routes": state.get("model_routes") or [],
            "model_call_history": state.get("model_call_history") or [],
        },
    }


def run_staged_benchmark(
    cases: Iterable[dict[str, Any]],
    *,
    models: Iterable[str],
    data: Any = None,
    provider: Callable[[list[dict[str, Any]], str], Any] | None = None,
    stop_on_terminal_error: bool = True,
) -> dict[str, Any]:
    selected_models = list(dict.fromkeys(str(model).strip() for model in models if str(model).strip()))
    if not selected_models:
        raise ValueError("At least one estimator model is required.")
    case_rows = list(cases)
    results: list[dict[str, Any]] = []
    for model in selected_models:
        for case in case_rows:
            result = run_staged_case(
                case,
                model=model,
                data=data,
                provider=provider,
            )
            results.append(result)
            if stop_on_terminal_error and _terminal_model_error(result):
                break
    comparisons = []
    for model in selected_models:
        model_results = [row for row in results if row["model"] == model]
        successful_results = [
            row
            for row in model_results
            if (row.get("session_summary") or {}).get("response_source") == "ai_chat"
        ]
        comparisons.append(
            {
                "model": model,
                "selected_case_count": len(case_rows),
                "attempted_case_count": len(model_results),
                "successful_model_case_count": len(successful_results),
                "fallback_case_count": len(model_results) - len(successful_results),
                "unattempted_case_count": max(0, len(case_rows) - len(model_results)),
                "mean_score": _mean(
                    row["score"].get("overall_score")
                    for row in model_results
                ),
                "mean_model_score": _optional_mean(
                    row["score"].get("overall_score")
                    for row in successful_results
                ),
                "mean_decision_f1": _mean(
                    (row["score"].get("metrics") or {}).get(
                        "template_selection_f1"
                    )
                    for row in successful_results
                ),
                "mean_area_score": _mean(
                    (row["score"].get("metrics") or {}).get("square_footage_score")
                    for row in successful_results
                ),
                "total_unnecessary_questions": sum(
                    int((row["score"].get("counts") or {}).get("unnecessary_questions") or 0)
                    for row in successful_results
                ),
                "total_unsupported_assumptions": sum(
                    int((row["score"].get("counts") or {}).get("unsupported_assumptions") or 0)
                    for row in successful_results
                ),
            }
        )
    benchmark_status = (
        "curated"
        if case_rows
        and all(
            str((row.get("source_metadata") or {}).get("promotion_status") or "")
            .strip()
            .lower()
            in PROMOTABLE_STATUSES
            for row in case_rows
        )
        else "review_only"
    )
    return {
        "report_version": 1,
        "benchmark_status": benchmark_status,
        "case_count": len(case_rows),
        "models": selected_models,
        "comparisons": sorted(
            comparisons,
            key=lambda row: float(row.get("mean_score") or 0),
            reverse=True,
        ),
        "results": results,
    }


def score_staged_state(case: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    expected_rows = [
        row
        for row in case.get("expected_decisions") or []
        if _token(row.get("line_item_kind")) in ACTIONABLE_KINDS
    ]
    expected_included = {
        _token(row.get("template_bucket"))
        for row in expected_rows
        if row.get("expected_include", True) is True and _token(row.get("template_bucket"))
    }
    expected_excluded = {
        _token(row.get("template_bucket"))
        for row in expected_rows
        if row.get("expected_include") is False and _token(row.get("template_bucket"))
    }
    actual_rows = [
        row
        for row in state.get("decision_template_state") or []
        if isinstance(row, dict)
    ]
    actual_included = {
        _token(row.get("template_bucket"))
        for row in actual_rows
        if row.get("include") is True and _token(row.get("template_bucket"))
    }
    selection_policy = str(
        (case.get("source_metadata") or {}).get("selection_policy") or "exact"
    ).strip().lower()
    if selection_policy == "required_only":
        precision = None
        recall = _recall(expected_included, actual_included)
        f1 = recall
    else:
        precision, recall, f1 = _set_scores(expected_included, actual_included)
    material_score = _material_choice_score(expected_rows, actual_rows)
    labor_expected = {
        _token(row.get("template_bucket"))
        for row in expected_rows
        if _token(row.get("line_item_kind")) == "labor"
        and row.get("expected_include", True) is True
    }
    labor_score = _recall(labor_expected, actual_included)
    exclusion_score = _exclusion_score(expected_excluded, actual_included)
    area_score, area_error = _area_score(case.get("expected_scope") or {}, state)
    thickness_score = _numeric_decision_score(
        expected_rows,
        actual_rows,
        fields=("thickness_inches",),
        tolerance_ratio=0.1,
    )
    warranty_score = _warranty_score(case.get("expected_scope") or {}, state)
    pricing_score = _pricing_score(case, state)
    unnecessary_questions = _unnecessary_question_count(case, state)
    unsupported_assumptions = _unsupported_assumption_count(state)
    metrics = {
        "template_selection_precision": precision,
        "template_selection_recall": recall,
        "template_selection_f1": f1,
        "material_choice_score": material_score,
        "labor_assumption_score": labor_score,
        "exclusion_score": exclusion_score,
        "square_footage_score": area_score,
        "thickness_score": thickness_score,
        "warranty_score": warranty_score,
        "pricing_range_score": pricing_score,
        "question_efficiency_score": max(0.0, 1.0 - unnecessary_questions * 0.1),
        "assumption_support_score": max(0.0, 1.0 - unsupported_assumptions * 0.15),
    }
    available_metrics = [value for value in metrics.values() if value is not None]
    overall = round(sum(available_metrics) / len(available_metrics), 4) if available_metrics else 0.0
    return {
        "overall_score": overall,
        "metrics": metrics,
        "counts": {
            "expected_decision_buckets": len(expected_included),
            "actual_decision_buckets": len(actual_included),
            "unnecessary_questions": unnecessary_questions,
            "unsupported_assumptions": unsupported_assumptions,
        },
        "differences": {
            "missing_decision_buckets": sorted(expected_included - actual_included),
            "unexpected_decision_buckets": (
                []
                if selection_policy == "required_only"
                else sorted(actual_included - expected_included)
            ),
            "violated_exclusions": sorted(expected_excluded & actual_included),
            "square_footage_relative_error": area_error,
        },
    }


def _material_choice_score(
    expected_rows: list[dict[str, Any]],
    actual_rows: list[dict[str, Any]],
) -> float | None:
    expected = {
        _token(row.get("selected_item_name"))
        for row in expected_rows
        if _token(row.get("line_item_kind")) == "material"
        and _token(row.get("selected_item_name")) not in GENERIC_ITEM_NAMES
    }
    if not expected:
        return None
    actual_text = " ".join(
        json.dumps(
            {
                "resolved": row.get("resolved_template_option"),
                "selected": row.get("selected_item_name"),
                "values": row.get("proposed_values"),
            },
            default=str,
        ).lower()
        for row in actual_rows
        if row.get("include") is True
    )
    matched = sum(1 for value in expected if value.replace("_", " ") in actual_text)
    return round(matched / len(expected), 4)


def _numeric_decision_score(
    expected_rows: list[dict[str, Any]],
    actual_rows: list[dict[str, Any]],
    *,
    fields: tuple[str, ...],
    tolerance_ratio: float,
) -> float | None:
    expected_values = [
        _number(row.get(field))
        for row in expected_rows
        for field in fields
        if _number(row.get(field)) is not None
    ]
    if not expected_values:
        return None
    actual_values = [
        _number((row.get("proposed_values") or {}).get(field))
        for row in actual_rows
        for field in fields
        if _number((row.get("proposed_values") or {}).get(field)) is not None
    ]
    if not actual_values:
        return 0.0
    matches = sum(
        1
        for expected in expected_values
        if any(abs(actual - expected) <= max(0.1, abs(expected) * tolerance_ratio) for actual in actual_values)
    )
    return round(matches / len(expected_values), 4)


def _area_score(
    expected_scope: dict[str, Any],
    state: dict[str, Any],
) -> tuple[float | None, float | None]:
    expected = _number(
        expected_scope.get("estimated_sqft")
        or expected_scope.get("net_area_sqft")
        or expected_scope.get("surface_area_sqft")
    )
    if not expected or expected <= 0:
        return None, None
    scope = state.get("scope_state") or {}
    actual = _number(
        scope.get("estimated_sqft")
        or scope.get("net_insulation_area_sqft")
        or scope.get("net_area_sqft")
        or scope.get("surface_area_sqft")
    )
    if actual is None:
        return 0.0, None
    error = abs(actual - expected) / expected
    return round(max(0.0, 1.0 - error), 4), round(error, 4)


def _warranty_score(expected_scope: dict[str, Any], state: dict[str, Any]) -> float | None:
    expected = _number(
        expected_scope.get("warranty_years")
        or expected_scope.get("warranty_target_years")
    )
    if expected is None:
        return None
    scope = state.get("scope_state") or {}
    actual = _number(
        scope.get("warranty_years")
        or scope.get("warranty_target_years")
        or scope.get("warranty_target")
    )
    if actual is None:
        return 0.0
    return 1.0 if abs(actual - expected) <= 0.5 else 0.0


def _pricing_score(
    case: dict[str, Any],
    state: dict[str, Any],
) -> float | None:
    expected_scope = case.get("expected_scope") or {}
    expected_cost = _number(
        expected_scope.get("quoted_value")
        or expected_scope.get("final_value")
        or expected_scope.get("expected_total")
        or expected_scope.get("pricing_target")
    )
    if expected_cost is None or expected_cost <= 0:
        return None
    totals = (state.get("calculation_state") or {}).get("totals") or {}
    actual_cost = _number(
        totals.get("draft_total")
        or totals.get("estimated_total")
        or totals.get("total")
    )
    if actual_cost is None:
        return 0.0
    ratio = actual_cost / expected_cost
    return 1.0 if 0.75 <= ratio <= 1.25 else max(0.0, 1.0 - abs(1.0 - ratio))


def _unnecessary_question_count(case: dict[str, Any], state: dict[str, Any]) -> int:
    expected = case.get("expected_scope") or {}
    expected_missing = {
        _token(value)
        for value in expected.get("missing_questions") or []
        if str(value).strip()
    }
    questions = state.get("unresolved_questions") or []
    if not expected_missing:
        return len(questions)
    return sum(
        1
        for question in questions
        if not any(token and token in _token(question) for token in expected_missing)
    )


def _unsupported_assumption_count(state: dict[str, Any]) -> int:
    return sum(
        1
        for assumption in state.get("assumptions") or []
        if isinstance(assumption, dict)
        and not (
            assumption.get("source_ids")
            or assumption.get("evidence")
            or assumption.get("source_type")
        )
    )


def _set_scores(expected: set[str], actual: set[str]) -> tuple[float, float, float]:
    if not expected and not actual:
        return 1.0, 1.0, 1.0
    overlap = len(expected & actual)
    precision = overlap / len(actual) if actual else 0.0
    recall = overlap / len(expected) if expected else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return round(precision, 4), round(recall, 4), round(f1, 4)


def _recall(expected: set[str], actual: set[str]) -> float | None:
    if not expected:
        return None
    return round(len(expected & actual) / len(expected), 4)


def _exclusion_score(expected_excluded: set[str], actual: set[str]) -> float | None:
    if not expected_excluded:
        return None
    return round(1.0 - len(expected_excluded & actual) / len(expected_excluded), 4)


def _mean(values: Iterable[Any]) -> float:
    numbers = [float(value) for value in values if value is not None]
    return round(sum(numbers) / len(numbers), 4) if numbers else 0.0


def _optional_mean(values: Iterable[Any]) -> float | None:
    numbers = [float(value) for value in values if value is not None]
    return round(sum(numbers) / len(numbers), 4) if numbers else None


def _terminal_model_error(result: dict[str, Any]) -> bool:
    warnings = " ".join(
        str(value)
        for value in (result.get("session_summary") or {}).get("warnings") or []
    ).lower()
    return any(
        marker in warnings
        for marker in (
            "insufficient_quota",
            "authenticationerror",
            "invalid api key",
            "not a chat model",
            "model_not_found",
        )
    )


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _token(value: Any) -> str:
    return "_".join(str(value or "").strip().lower().replace("-", " ").replace("_", " ").split())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate the persistent staged Estimating Assistant against historical review cases."
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--model", action="append", default=[])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--database-url", default=os.getenv("NEON_DATABASE_URL"))
    parser.add_argument("--json-output", type=Path)
    parser.add_argument(
        "--promote-reviewed-output",
        type=Path,
        help="Write cases explicitly marked reviewed/approved/promoted to a curated benchmark JSON file.",
    )
    parser.add_argument(
        "--promote-only",
        action="store_true",
        help="Promote reviewed cases without running model evaluation.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate case selection and model configuration without making model calls.",
    )
    parser.add_argument(
        "--continue-after-model-error",
        action="store_true",
        help="Continue after terminal authentication, quota, or model configuration errors.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cases = load_staged_cases(args.cases)
    if args.case_id:
        selected = set(args.case_id)
        cases = [case for case in cases if str(case.get("case_id")) in selected]
    if args.limit > 0:
        cases = cases[: args.limit]
    if args.promote_reviewed_output:
        promoted = promote_reviewed_cases(cases, args.promote_reviewed_output)
        print(
            f"Promoted {promoted['case_count']} reviewed case(s) to "
            f"{args.promote_reviewed_output}"
        )
        if args.promote_only:
            return 0 if promoted["case_count"] else 1
    models = args.model or [configured_estimator_models().get("estimator_model")]
    models = [str(model) for model in models if str(model or "").strip()]
    if not cases:
        print("No staged estimator cases selected.")
        return 1
    if not models:
        print("No estimator model configured. Set OPENAI_ESTIMATOR_MODEL or pass --model.")
        return 1
    if args.dry_run:
        print(f"Staged estimator eval dry run: {len(cases)} case(s), models={models}")
        return 0
    if not os.getenv("OPENAI_API_KEY"):
        print(
            "OPENAI_API_KEY is required for a live staged model comparison. "
            "Use --dry-run to validate case selection without model calls."
        )
        return 1
    data = load_data_for_eval(args.database_url)
    report = run_staged_benchmark(
        cases,
        models=models,
        data=data,
        stop_on_terminal_error=not args.continue_after_model_error,
    )
    print(
        "Staged estimator eval: "
        + ", ".join(
            f"{row['model']} end_to_end={row['mean_score']:.3f} "
            f"model={row['mean_model_score'] if row['mean_model_score'] is not None else 'n/a'} "
            f"successful={row['successful_model_case_count']}/{row['attempted_case_count']}"
            for row in report["comparisons"]
        )
    )
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(report, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        print(f"JSON report: {args.json_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
