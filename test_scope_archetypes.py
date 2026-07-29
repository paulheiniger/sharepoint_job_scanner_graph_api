from __future__ import annotations

import json

import pandas as pd

from jobscan.estimator.schemas import EstimatorData
from jobscan.estimator.scope_archetypes import (
    ArchetypeAnalysisConfig,
    analyze_scope_archetypes,
    build_candidate_archetypes,
    build_estimate_observations,
    classify_template_rows,
    mine_association_rules,
    write_scope_archetype_analysis,
)


def _row(
    document_id: str,
    job_id: str,
    row_number: int,
    bucket: str,
    *,
    source_file: str | None = None,
    kind: str = "material",
    **values: object,
) -> dict[str, object]:
    return {
        "template_row_id": f"{document_id}-{row_number}",
        "document_id": document_id,
        "job_id": job_id,
        "source_file": source_file or f"{document_id}.xlsx",
        "template_type": "roofing",
        "sheet_name": "Estimate",
        "row_number": row_number,
        "template_bucket": bucket,
        "line_item_kind": kind,
        **values,
    }


def test_row_classifier_separates_usage_defaults_metadata_and_distinct_coatings() -> None:
    rows = pd.DataFrame(
        [
            _row(
                "D1",
                "J1",
                26,
                "coating",
                resolved_item_name="Base Coat",
                estimated_units=120,
                estimated_cost=4800,
            ),
            _row(
                "D1",
                "J1",
                27,
                "coating",
                resolved_item_name="Top Coat",
                selector_code=11,
                unit_price=42,
            ),
            _row("D1", "J1", 3, "job_name", selected_item_name="Example Job"),
            _row(
                "D1",
                "J1",
                116,
                "labor_prep",
                kind="labor",
                total_hours=44,
            ),
            _row(
                "D1",
                "J1",
                140,
                "custom_detail",
                kind="",
                resolved_item_name="Unmapped custom detail",
            ),
        ]
    )

    classified = classify_template_rows(rows).set_index("row_number")

    assert classified.loc[26, "row_state"] == "included"
    assert classified.loc[26, "decision_key"] == "coating@row_26"
    assert classified.loc[27, "row_state"] == "excluded"
    assert classified.loc[27, "decision_key"] == "coating@row_27"
    assert classified.loc[27, "state_reason"] == "price_or_selector_only_template_default"
    assert classified.loc[3, "row_state"] == "not_applicable"
    assert classified.loc[116, "row_state"] == "included"
    assert classified.loc[140, "row_state"] == "unknown"


def test_estimate_observations_keep_workbooks_separate_and_select_latest_final_revision() -> None:
    data = EstimatorData(
        template_rows=pd.DataFrame(
            [
                _row(
                    "D1",
                    "J1",
                    26,
                    "coating",
                    source_file="Estimate v1.xlsx",
                    estimated_units=100,
                    estimated_cost=4000,
                ),
                _row(
                    "D2",
                    "J1",
                    26,
                    "coating",
                    source_file="Estimate FINAL.xlsx",
                    estimated_units=120,
                    estimated_cost=4800,
                ),
                _row(
                    "D2",
                    "J1",
                    39,
                    "primer",
                    source_file="Estimate FINAL.xlsx",
                    estimated_units=30,
                    estimated_cost=900,
                ),
            ]
        ),
        estimates=pd.DataFrame(
            [
                {
                    "job_id": "J1",
                    "source_file": "Estimate v1.xlsx",
                    "status": "Draft",
                    "updated_at": "2026-01-01T12:00:00Z",
                },
                {
                    "job_id": "J1",
                    "source_file": "Estimate FINAL.xlsx",
                    "status": "Approved",
                    "updated_at": "2026-01-03T12:00:00Z",
                },
            ]
        ),
    )

    observations, classified = build_estimate_observations(data)

    assert len(observations) == 2
    assert classified["observation_id"].nunique() == 2
    selected = observations[observations["training_selected"]]
    assert len(selected) == 1
    assert selected.iloc[0]["document_id"] == "D2"
    assert selected.iloc[0]["revision_selection_reason"] == "latest_final_revision"
    assert json.loads(selected.iloc[0]["included_decisions_json"]) == [
        "coating@row_26",
        "primer@row_39",
    ]


def _archetype_data() -> EstimatorData:
    rows: list[dict[str, object]] = []
    for index in range(1, 5):
        document_id = f"D{index}"
        job_id = f"J{index}"
        rows.append(
            _row(
                document_id,
                job_id,
                26,
                "coating",
                estimated_units=100 + index,
                estimated_cost=4000 + index,
            )
        )
        if index <= 3:
            rows.extend(
                [
                    _row(
                        document_id,
                        job_id,
                        39,
                        "primer",
                        estimated_units=20 + index,
                        estimated_cost=800 + index,
                    ),
                    _row(
                        document_id,
                        job_id,
                        116,
                        "labor_prep",
                        kind="labor",
                        total_hours=40 + index,
                    ),
                ]
            )
    return EstimatorData(template_rows=pd.DataFrame(rows))


def test_directed_rules_report_conditional_confidence_and_lift() -> None:
    observations, _ = build_estimate_observations(_archetype_data())

    rules, negatives = mine_association_rules(
        observations,
        config=ArchetypeAnalysisConfig(min_support_count=3),
    )

    primer_to_coating = rules[
        rules["antecedent"].eq("primer@row_39")
        & rules["consequent"].eq("coating@row_26")
        & rules["segment_type"].eq("template_type")
    ].iloc[0]
    assert primer_to_coating["support_count"] == 3
    assert primer_to_coating["confidence"] == 1.0
    assert primer_to_coating["lift"] == 1.0
    assert primer_to_coating["confidence_wilson_lower"] > 0
    assert isinstance(negatives, pd.DataFrame)


def test_candidate_archetype_preserves_core_and_review_examples() -> None:
    observations, _ = build_estimate_observations(_archetype_data())

    archetypes, packets = build_candidate_archetypes(
        observations,
        config=ArchetypeAnalysisConfig(
            min_archetype_jobs=3,
            jaccard_threshold=0.58,
        ),
    )

    assert len(archetypes) == 1
    archetype = archetypes.iloc[0]
    assert archetype["observation_count"] == 3
    assert set(json.loads(archetype["core_decisions_json"])) == {
        "coating@row_26",
        "primer@row_39",
    }
    assert json.loads(archetype["execution_decisions_json"]) == [
        "labor_prep@row_116"
    ]
    assert packets[0]["archetype_id"] == archetype["archetype_id"]
    assert len(packets[0]["representative_estimates"]) == 3
    assert packets[0]["requested_ai_review"]["constraints"]


def test_analysis_writes_reviewable_offline_artifacts(tmp_path) -> None:
    result = analyze_scope_archetypes(
        _archetype_data(),
        config=ArchetypeAnalysisConfig(
            min_support_count=3,
            min_archetype_jobs=3,
        ),
    )

    paths = write_scope_archetype_analysis(result, tmp_path)

    expected = {
        "analysis_summary.json",
        "ai_review_packets.jsonl",
        "association_rules.csv",
        "candidate_archetypes.csv",
        "decision_matrix.csv",
        "diagnostics.csv",
        "estimate_observations.csv",
        "negative_associations.csv",
        "report.md",
        "rule_candidates.csv",
        "row_classifications.csv",
    }
    assert expected.issubset(paths)
    summary = json.loads(paths["analysis_summary.json"].read_text(encoding="utf-8"))
    assert summary["runtime_activation"] is False
    assert summary["metrics"]["candidate_archetypes"] == 1
    assert paths["ai_review_packets.jsonl"].read_text(encoding="utf-8").strip()
