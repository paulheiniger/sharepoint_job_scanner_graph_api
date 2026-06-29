from __future__ import annotations

import pytest

import jobscan.estimator.field_estimator as field_estimator
from jobscan.estimator.field_estimator import estimate_from_field_notes


INCOMPLETE_RESTORATION_NOTE = (
    "Commercial metal roof. "
    "Roof is approximately twenty years old. "
    "There are scattered rusted fasteners throughout. "
    "Several seams are beginning to separate. "
    "No active leaks. "
    "Customer originally requested repairs but is interested in knowing whether a full silicone restoration would make more sense."
)


def test_missing_roof_area_stops_before_estimating(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("Readiness gate should skip calibration and pricing when sqft is missing.")

    monkeypatch.setattr(field_estimator, "load_estimator_data", fail_if_called)
    monkeypatch.setattr(field_estimator, "find_similar_jobs", fail_if_called)
    monkeypatch.setattr(field_estimator, "build_material_plan", fail_if_called)
    monkeypatch.setattr(field_estimator, "build_labor_plan", fail_if_called)

    recommendation = estimate_from_field_notes(INCOMPLETE_RESTORATION_NOTE, data=None)

    assert recommendation.estimate_status == "NEED_MORE_INFORMATION"
    assert recommendation.parsed_fields.get("estimated_sqft") is None
    assert recommendation.estimate_low is None
    assert recommendation.estimate_target is None
    assert recommendation.estimate_high is None
    assert recommendation.material_plan == []
    assert recommendation.labor_plan == []
    assert recommendation.similar_examples == []
    assert recommendation.historical_calibration == {}
    assert "Roof area is unknown" in recommendation.estimate_reason
    assert any("Approximate roof square footage" in question for question in recommendation.required_questions)
    assert any("Roof dimensions" in question for question in recommendation.required_questions)
    assert any("Request roof measurements" in action for action in recommendation.recommended_next_actions)
    assert any("repair and restoration" in action.lower() for action in recommendation.recommended_next_actions)


def test_ready_roof_coating_with_dimensions_continues_to_estimate() -> None:
    recommendation = estimate_from_field_notes(
        "Commercial metal roof. Roof is 90 ft by 70 ft. Customer wants a 10-year silicone coating system.",
        data=field_estimator.EstimatorData(),
    )

    assert recommendation.estimate_status == "READY_TO_ESTIMATE"
    assert recommendation.parsed_fields["estimated_sqft"] == 6300
