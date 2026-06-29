from __future__ import annotations

import json

from jobscan.estimator import ai_scope_interpreter
from jobscan.estimator.field_estimator import estimate_from_field_notes
from test_field_estimator import field_data


ROOF_COATING_NOTE = (
    "Roof coating estimate for a commercial metal roof in Louisville KY. "
    "Main roof is 120 ft by 80 ft. Deduct two skylights, each 4 ft by 8 ft. "
    "Roof is fair overall but has rusted fasteners and some open seams. "
    "Customer wants a 10-year silicone coating system. Access is easy. Few penetrations."
)


def test_ai_scope_interpreter_validates_scope_packages() -> None:
    result = ai_scope_interpreter.interpret_field_notes_with_ai(
        ROOF_COATING_NOTE,
        deterministic_scope={},
        provider=lambda _notes, _scope: {
            "project_type": "roof coating",
            "scope_packages": {"coating": "true", "primer": "banana", "seam_treatment": "heavy"},
            "condition_detail_flags": ["rusted_fasteners"],
        },
    )

    assert result["scope_packages"]["coating"] is True
    assert result["scope_packages"]["seam_treatment"] == "heavy"
    assert "primer" not in result["scope_packages"]
    assert any("invalid AI package decision" in flag for flag in result["review_flags"])


def test_ai_scope_interpreter_invalid_json_falls_back() -> None:
    result = ai_scope_interpreter.interpret_field_notes_with_ai(
        ROOF_COATING_NOTE,
        deterministic_scope={},
        provider=lambda _notes, _scope: "{not valid json",
    )

    assert result["project_type"] == ""
    assert any("AI scope interpreter unavailable or invalid" in flag for flag in result["review_flags"])


def test_estimator_merges_ai_scope_with_deterministic_guardrails(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_AI_SCOPE_INTERPRETER", "true")

    def fake_openai_response(_notes: str, _scope: dict | None = None) -> str:
        return json.dumps(
            {
                "project_type": "roof coating",
                "division": "ROOFING",
                "building_type": "commercial",
                "substrate": "metal",
                "gross_area_sqft": 9999,
                "deduction_area_sqft": 1,
                "estimated_sqft": 9998,
                "coating_type": "silicone",
                "warranty_target_years": 10,
                "roof_condition": "poor/rusted",
                "roof_condition_raw_phrase": "fair overall but has rusted fasteners and some open seams",
                "roof_condition_reason": "Overall condition is fair, with localized rusted fasteners and open seams.",
                "condition_detail_flags": ["rusted_fasteners", "open_seams"],
                "penetrations_complexity": "high",
                "penetrations_complexity_reason": "Bad AI value for this test.",
                "access_complexity": "high",
                "access_complexity_reason": "Bad AI value for this test.",
                "scope_packages": {"coating": True, "primer": "review", "seam_treatment": "heavy"},
                "missing_info": [],
                "review_flags": [],
                "confidence_by_field": {
                    "roof_condition": 0.82,
                    "penetrations_complexity": 0.8,
                    "access_complexity": 0.8,
                },
            }
        )

    monkeypatch.setattr(ai_scope_interpreter, "_call_openai_scope_interpreter", fake_openai_response)

    recommendation = estimate_from_field_notes(ROOF_COATING_NOTE, {"estimated_sqft": 0}, data=field_data())
    parsed = recommendation.parsed_fields

    assert parsed["estimated_sqft"] == 9536
    assert parsed["dimension_summary"]["gross_area_sqft"] == 9600
    assert parsed["dimension_summary"]["deduction_area_sqft"] == 64
    assert parsed["dimension_summary"]["net_area_sqft"] == 9536
    assert parsed["roof_condition"] in {"fair", "fair_with_rusted_fasteners"}
    assert "rusted_fasteners" in parsed["condition_detail_flags"]
    assert "open_seams" in parsed["condition_detail_flags"]
    assert parsed["penetrations_complexity"] == "low"
    assert parsed["access_complexity"] == "low"
    assert parsed["coating_type"] == "silicone"
    assert parsed["warranty_target_years"] == 10

    ai_debug = recommendation.debug["ai_scope_interpreter"]
    assert ai_debug["enabled"] is True
    assert ai_debug["deterministic_parsed_scope"]["estimated_sqft"] == 9536
    assert ai_debug["ai_parsed_scope"]["estimated_sqft"] == 9998
    assert ai_debug["final_merged_scope"]["estimated_sqft"] == 9536
    assert any(row["field"] == "estimated_sqft" and row["decision"] == "rejected" for row in ai_debug["merge_decisions"])

