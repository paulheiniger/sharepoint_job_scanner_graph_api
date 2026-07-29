from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd
import pytest

from jobscan.estimator.schemas import EstimatorData
from jobscan.estimator.scope_archetypes import (
    ArchetypeAnalysisConfig,
    analyze_scope_archetypes,
)
from jobscan.estimator.scope_archetype_review import (
    apply_review_overrides,
    build_stratified_review_queue,
    evaluate_rule_holdouts,
    label_archetype_packets,
    load_review_workbook,
    write_review_validation_artifacts,
    write_review_workbook,
)


def _row(
    document_id: str,
    job_id: str,
    row_number: int,
    bucket: str,
    *,
    source_file: str | None = None,
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
        "line_item_kind": "material",
        **values,
    }


def _review_data() -> EstimatorData:
    return EstimatorData(
        template_rows=pd.DataFrame(
            [
                _row(
                    "D1",
                    "J1",
                    26,
                    "coating",
                    source_file="Estimate v1.xlsx",
                    estimated_units=100,
                    estimated_cost=4_000,
                ),
                _row(
                    "D1",
                    "J1",
                    39,
                    "primer",
                    source_file="Estimate v1.xlsx",
                    unit_price=20,
                ),
                _row(
                    "D2",
                    "J1",
                    26,
                    "coating",
                    source_file="Estimate FINAL.xlsx",
                    estimated_units=120,
                    estimated_cost=4_800,
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
                    "updated_at": "2025-01-01T12:00:00Z",
                    "estimator": "Estimator A",
                },
                {
                    "job_id": "J1",
                    "source_file": "Estimate FINAL.xlsx",
                    "status": "Approved",
                    "updated_at": "2025-01-03T12:00:00Z",
                    "estimator": "Estimator A",
                },
            ]
        ),
    )


def test_review_workbook_is_stratified_and_round_trips(tmp_path) -> None:
    result = analyze_scope_archetypes(
        _review_data(),
        config=ArchetypeAnalysisConfig(min_archetype_jobs=1),
    )
    queue = build_stratified_review_queue(result, target_estimates=2)

    assert len(queue["estimate_review"]) == 2
    assert set(queue["estimate_review"]["document_id"]) == {"D1", "D2"}
    assert not queue["row_review"].empty
    assert "review_row_state" in queue["row_review"].columns

    path = write_review_workbook(queue, tmp_path / "review.xlsx")
    loaded = load_review_workbook(path)

    assert set(loaded) == {
        "estimate_review",
        "row_review",
        "archetype_review",
        "rule_review",
    }
    assert len(loaded["estimate_review"]) == 2


def test_reviewed_row_and_revision_overrides_rebuild_analysis() -> None:
    data = _review_data()
    result = analyze_scope_archetypes(
        data,
        config=ArchetypeAnalysisConfig(min_archetype_jobs=1),
    )
    queue = build_stratified_review_queue(result, target_estimates=2)
    estimates = queue["estimate_review"].copy()
    estimates["review_complete"] = True
    estimates["review_training_selected"] = estimates["document_id"].eq("D1")

    rows = queue["row_review"].copy()
    primer_v1 = rows[
        rows["document_id"].eq("D1") & rows["template_row_id"].eq("D1-39")
    ].index
    rows.loc[primer_v1, "review_row_state"] = "included"
    rows.loc[primer_v1, "review_complete"] = True

    rebuilt, corrections = apply_review_overrides(
        data,
        result,
        {
            "estimate_review": estimates,
            "row_review": rows,
            "archetype_review": queue["archetype_review"],
        },
        config=ArchetypeAnalysisConfig(min_archetype_jobs=1),
    )

    selected = rebuilt["observations"][
        rebuilt["observations"]["training_selected"].astype(bool)
    ].iloc[0]
    assert selected["document_id"] == "D1"
    assert json.loads(selected["included_decisions_json"]) == [
        "coating@row_26",
        "primer@row_39",
    ]
    assert set(corrections["correction_type"]) == {
        "revision_selection",
        "row_state",
    }


class _FakeResponses:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def create(self, **request: object) -> SimpleNamespace:
        self.calls.append(request)
        return SimpleNamespace(
            id="response-1",
            model=request["model"],
            usage={"input_tokens": 100, "output_tokens": 50},
            output_text=json.dumps(
                {
                    "proposed_name": "Coated foam restoration",
                    "base_system": "SPF with coating",
                    "required_signals": ["coated foam scope"],
                    "core_decisions": ["foam@row_19", "coating@row_26"],
                    "conditional_modifiers": ["primer when substrate requires it"],
                    "likely_exclusions": ["tear-off without explicit scope"],
                    "ambiguities": [],
                    "review_confidence": 0.88,
                }
            ),
        )


def test_ai_archetype_labels_are_bounded_and_cached(tmp_path) -> None:
    packet = {
        "archetype_id": "A1",
        "statistical_summary": {
            "template_type": "roofing",
            "core_decisions": ["foam@row_19", "coating@row_26"],
        },
        "representative_estimates": [
            {
                "observation_id": "O1",
                "scope_excerpt": "Coated foam restoration " * 1_000,
            }
        ],
    }
    responses = _FakeResponses()
    client = SimpleNamespace(responses=responses)

    first = label_archetype_packets(
        [packet],
        cache_dir=tmp_path,
        model="test-model",
        client=client,
        max_input_characters=2_000,
    )
    second = label_archetype_packets(
        [packet],
        cache_dir=tmp_path,
        model="test-model",
        client=client,
        max_input_characters=2_000,
    )

    assert len(responses.calls) == 1
    assert first[0]["status"] == "completed"
    assert first[0]["input_characters"] <= 2_000
    assert second[0]["cache_hit"] is True
    assert responses.calls[0]["text"]["format"]["strict"] is True


def test_ai_labels_are_visible_in_editable_review_workbook(tmp_path) -> None:
    result = analyze_scope_archetypes(
        _review_data(),
        config=ArchetypeAnalysisConfig(min_archetype_jobs=1),
    )
    queue = build_stratified_review_queue(result, target_estimates=2)
    archetype_id = result["candidate_archetypes"].iloc[0]["archetype_id"]
    labels = [
        {
            "archetype_id": archetype_id,
            "status": "completed",
            "label": {
                "proposed_name": "Reviewed roof coating system",
                "base_system": "coating",
                "required_signals": [],
                "conditional_modifiers": [],
                "likely_exclusions": [],
                "ambiguities": [],
                "review_confidence": 0.9,
            },
        }
    ]

    paths = write_review_validation_artifacts(
        result,
        tmp_path,
        review_queue=queue,
        ai_labels=labels,
    )
    sheet = pd.read_excel(paths["scope_archetype_review.xlsx"], sheet_name="archetype_review")

    assert sheet.iloc[0]["ai_proposed_name"] == "Reviewed roof coating system"
    assert "review_proposed_name" in sheet.columns
    assert "review_approved" in sheet.columns


def test_holdout_scoring_penalizes_false_positive_rules() -> None:
    rows = []
    for index in range(15):
        decisions = ["foam@row_19"]
        if index < 13:
            decisions.append("coating@row_26")
        rows.append(
            {
                "observation_id": f"O{index:02}",
                "template_type": "roofing",
                "training_selected": True,
                "revision_timestamp": f"2025-{index + 1:02}-01T00:00:00Z"
                if index < 12
                else f"2026-0{index - 11}-01T00:00:00Z",
                "source_year": "",
                "estimator": "Estimator A" if index < 8 else "Estimator B",
                "included_decisions_json": json.dumps(decisions),
            }
        )
    candidates = pd.DataFrame(
        [
            {
                "segment_value": "roofing",
                "antecedent": "foam@row_19",
                "consequent": "coating@row_26",
                "support_count": 13,
                "confidence": 13 / 15,
                "lift": 1.0,
            }
        ]
    )

    evaluations, summary = evaluate_rule_holdouts(
        pd.DataFrame(rows),
        candidates,
        false_positive_weight=3,
    )

    temporal = evaluations[evaluations["split_type"].eq("temporal")].iloc[0]
    assert temporal["holdout_true_positives"] == 1
    assert temporal["holdout_false_positives"] == 2
    assert temporal["holdout_false_positive_weighted_precision"] == pytest.approx(1 / 7)
    assert summary.iloc[0]["validation_status"] == "unstable_candidate"
