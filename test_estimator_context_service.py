from __future__ import annotations

import pandas as pd

from jobscan.estimator.context_service import build_copilot_estimator_context
from jobscan.estimator.schemas import EstimatorData


def test_build_copilot_estimator_context_reuses_real_context_assembly() -> None:
    result = build_copilot_estimator_context(
        scope={"template_type": "insulation"},
        raw_notes="30x40 metal building",
        data=EstimatorData(),
    )

    assert result["context"]["template_type"] == "insulation"
    assert result["context"]["decision_menu"]
    assert result["context"]["formula_requirements"]
    assert result["context"]["historical_evidence_packet"]["advisory_only"] is True


def test_build_copilot_estimator_context_is_bounded_and_model_neutral(monkeypatch) -> None:
    data = EstimatorData(
        jobs=pd.DataFrame([{"job_id": "JOB-1"}]),
        estimates=pd.DataFrame([{"estimate_id": "EST-1"}]),
        pricing_catalog=pd.DataFrame([{"pricing_item_id": "PRICE-1"}]),
        product_catalog=pd.DataFrame([{"product_id": "PRODUCT-1"}]),
        estimator_memory=pd.DataFrame([{"memory_id": "MEMORY-1"}]),
    )
    captured: dict = {}

    def fake_summary(estimator_data, *, scope):
        captured["data"] = estimator_data
        captured["scope"] = scope
        return {
            "template_type": "insulation",
            "route_mileage": {"round_trip_miles": 84.0},
            "historical_evidence_packet": {
                "matched_comparables": [{"job_id": "JOB-1"}],
                "decision_evidence": [{"decision_id": "foam"}],
                "matched_scope_pattern": {"archetype_id": "metal-building"},
                "validated_relationships": [{"rule_id": "RULE-1"}],
            },
            "estimator_memory_guidance": [{"memory_id": "MEMORY-1"}],
            "pricing_candidates_by_bucket": [{"template_bucket": "foam"}],
            "product_guidance_digest": [{"product_id": "PRODUCT-1"}],
            "formula_requirements": [
                {"decision_id": "foam", "unavailable_value": float("nan")}
            ],
            "_deterministic_latest_historical_unit_prices": [{"private": True}],
        }

    monkeypatch.setattr(
        "jobscan.estimator.context_service.estimator_context_summary",
        fake_summary,
    )

    result = build_copilot_estimator_context(
        scope={"building_type": "metal building"},
        raw_notes="30x40 building",
        template_type_hint="insulation",
        reference_job_ids=["JOB-1", "JOB-1"],
        data=data,
        include_source_metadata=True,
    )

    assert captured["data"] is data
    assert captured["scope"]["template_type"] == "insulation"
    assert captured["scope"]["raw_input_notes"] == "30x40 building"
    assert captured["scope"]["reference_job_ids"] == ["JOB-1"]
    assert result["schema_version"] == "spraytec.copilot_estimator_context.v1"
    assert result["retrieval_summary"] == {
        "matched_comparable_count": 1,
        "decision_evidence_count": 1,
        "matched_scope_pattern": True,
        "validated_relationship_count": 1,
        "approved_memory_count": 1,
        "pricing_bucket_count": 1,
        "product_guidance_count": 1,
        "formula_requirement_count": 1,
    }
    assert "_deterministic_latest_historical_unit_prices" not in result["context"]
    assert (
        result["context"]["formula_requirements"][0]["unavailable_value"] is None
    )
    assert result["source_metadata"]["row_counts"]["jobs"] == 1


def test_build_copilot_estimator_context_does_not_override_explicit_scope(monkeypatch) -> None:
    data = EstimatorData()

    def fake_summary(_data, *, scope):
        return {"template_type": scope["template_type"]}

    monkeypatch.setattr(
        "jobscan.estimator.context_service.estimator_context_summary",
        fake_summary,
    )

    result = build_copilot_estimator_context(
        scope={
            "template_type": "roofing",
            "raw_input_notes": "Structured note wins.",
        },
        raw_notes="Raw note",
        template_type_hint="insulation",
        data=data,
    )

    assert result["scope"]["template_type"] == "roofing"
    assert result["scope"]["raw_input_notes"] == "Structured note wins."
