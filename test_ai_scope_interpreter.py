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

    assert result["project_type"] == "roof coating"
    assert result["estimated_sqft"] == 9536
    assert any("AI scope interpreter unavailable or invalid" in flag for flag in result["review_flags"])


def test_deterministic_ai_fallback_handles_clean_notes() -> None:
    result = ai_scope_interpreter.interpret_field_notes_with_ai(ROOF_COATING_NOTE, deterministic_scope={})

    assert result["estimate_mode"] == "restoration"
    assert result["project_type"] == "roof coating"
    assert result["substrate"] == "metal"
    assert result["coating_type"] == "silicone"
    assert result["warranty_years"] == 10
    assert result["gross_sqft"] == 9600
    assert result["deduction_sqft"] == 64
    assert result["net_sqft"] == 9536
    assert result["defects"]["rusted_fasteners"] is True
    assert result["defects"]["open_seams"] is True
    assert result["scope_triggers"]["coating"] is True
    assert result["scope_triggers"]["seam_treatment"] is True
    assert result["evidence_by_field"]["dimensions"]


def test_deterministic_ai_fallback_handles_conditional_coating_restoration_review() -> None:
    notes = (
        "Pegasus, 39 Pearce Industrial Rd. Various roof repairs/restoration review. "
        "Use working roof area 260 ft x 175.27 ft, about 45,570 sq ft. Notes mention ponding, "
        "penetrations, seams, and multiple repair/detail conditions, so this should not be treated "
        "as a clean simple coating. Metal roof/coating restoration seems possible, but estimator "
        "should review primer, rust/fasteners, seam treatment, caulk/detail, fabric/reinforcement, "
        "and any ponding/wet areas before committing to warranty. Customer likely wants practical "
        "repairs plus a coating path if the roof can qualify."
    )

    result = ai_scope_interpreter.interpret_field_notes_with_ai(notes, deterministic_scope={})

    assert result["estimate_mode"] == "restoration"
    assert result["project_type"] == "roof coating"
    assert result["coating_required"] is True
    assert result["coating_path_review"] is True
    assert result.get("warranty_years") is None
    assert result["net_sqft"] == 45570.2
    assert result["defects"]["ponding"] is True
    assert result["defects"]["open_seams"] is True
    assert result["defects"]["rusted_fasteners"] is True
    assert result["scope_triggers"]["coating"] is True


def test_deterministic_ai_fallback_handles_rambling_correction_notes() -> None:
    notes = (
        "Hey this is for that metal roof, I first thought it was 100 by 80. "
        "Actually scratch that, it's 120 by 80, not 100 by 80. "
        "Use a ten year silicone restoration, access is easy, only a few penetrations."
    )

    result = ai_scope_interpreter.interpret_field_notes_with_ai(notes, deterministic_scope={})

    assert result["net_sqft"] == 9600
    assert result["gross_sqft"] == 9600
    assert result["coating_type"] == "silicone"
    assert result["warranty_years"] == 10
    assert result["access_complexity"] == "low"
    assert result["penetration_complexity"] == "low"


def test_deterministic_ai_fallback_handles_negation() -> None:
    notes = (
        "Standing seam metal roof is 90 ft by 70 ft. No visible rust, no open seams, not leaking. "
        "Customer wants a 10-year silicone maintenance coating. Easy access."
    )

    result = ai_scope_interpreter.interpret_field_notes_with_ai(notes, deterministic_scope={})

    assert result["net_sqft"] == 6300
    assert result["defects"]["rust"] is False
    assert result["defects"]["rusted_fasteners"] is False
    assert result["defects"]["open_seams"] is False
    assert result["defects"]["leaks"] is False
    assert "rust" not in result["condition_flags"]


def test_deterministic_ai_fallback_partial_primer_percentage() -> None:
    notes = (
        "Commercial metal roof. Roof measures 140 ft by 90 ft. Overall roof is in good condition. "
        "Only the south edge has oxidation and rusted fasteners. Prime only south edge, about 20% before coating. "
        "Few penetrations. Easy access. Customer requests a 10-year silicone restoration."
    )

    result = ai_scope_interpreter.interpret_field_notes_with_ai(notes, deterministic_scope={})

    assert result["net_sqft"] == 12600
    assert result["scope_triggers"]["primer"] is True
    assert result["scope_triggers"]["partial_primer"] is True
    assert result["partial_scope"]["primer_basis_sqft"] == 2520


def test_ai_scope_validation_accepts_new_schema_and_rejects_bad_package() -> None:
    cleaned, warnings = ai_scope_interpreter.validate_ai_scope(
        {
            "estimate_mode": "restoration",
            "roof_type": "standing seam metal",
            "warranty_years": 10,
            "net_sqft": 6300,
            "condition": "good",
            "penetration_complexity": "low",
            "defects": {"rust": False, "curb_flashing_issues": True},
            "scope_triggers": {"coating": True, "primer": "yes"},
            "partial_scope": {"primer_basis_sqft": 1260},
            "scope_packages": {"coating": True, "primer": "banana"},
            "evidence_by_field": {"condition": ["good condition"]},
        }
    )

    assert cleaned["roof_type"] == "standing seam metal"
    assert cleaned["estimated_sqft"] == 6300
    assert cleaned["warranty_target_years"] == 10
    assert cleaned["roof_condition"] == "good"
    assert cleaned["penetrations_complexity"] == "low"
    assert cleaned["defects"]["curb/flashing_issues"] is True
    assert cleaned["scope_triggers"]["primer"] is True
    assert cleaned["partial_scope"]["primer_basis_sqft"] == 1260
    assert "primer" not in cleaned["scope_packages"]
    assert warnings


def test_ai_cannot_invent_square_footage_without_note_evidence() -> None:
    ai_scope, _warnings = ai_scope_interpreter.validate_ai_scope(
        {
            "project_type": "roof coating",
            "estimated_sqft": 12000,
            "coating_type": "silicone",
            "warranty_target_years": 10,
        }
    )

    final_scope, decisions, review_flags = ai_scope_interpreter.merge_ai_scope_with_deterministic(
        "Commercial metal roof. Customer wants a silicone coating, but dimensions are unknown.",
        {"notes": "Commercial metal roof. Customer wants a silicone coating, but dimensions are unknown."},
        ai_scope,
    )

    assert final_scope.get("estimated_sqft") is None
    assert any(row["field"] == "estimated_sqft" and row["decision"] == "rejected" for row in decisions)
    assert any("without note evidence" in flag for flag in review_flags)


def test_estimator_ai_fallback_without_api_key_still_uses_deterministic_parser(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_AI_SCOPE_INTERPRETER", "true")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    recommendation = estimate_from_field_notes(
        "Commercial metal roof. Roof is 90 ft by 70 ft. Customer wants a 10-year silicone coating system.",
        data=field_data(),
    )

    assert recommendation.estimate_status == "READY_TO_ESTIMATE"
    assert recommendation.parsed_fields["estimated_sqft"] == 6300
    ai_debug = recommendation.debug["ai_scope_interpreter"]
    assert ai_debug["enabled"] is True
    assert ai_debug["ai_parsed_scope"]["net_sqft"] == 6300


def test_ai_interpreter_is_preferred_when_openai_key_exists(monkeypatch) -> None:
    monkeypatch.delenv("ENABLE_AI_SCOPE_INTERPRETER", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    assert ai_scope_interpreter.ai_scope_interpreter_enabled() is True

    monkeypatch.setenv("DISABLE_AI_SCOPE_INTERPRETER", "true")
    assert ai_scope_interpreter.ai_scope_interpreter_enabled() is False


def test_ai_interpreter_explicit_false_overrides_openai_key(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_AI_SCOPE_INTERPRETER", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    assert ai_scope_interpreter.ai_scope_interpreter_enabled() is False


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


def test_ai_insulation_geometry_is_structured_and_deterministically_computed() -> None:
    notes = (
        "I need foam sprayed in a 30x40 metal building with 9' walls. "
        "Insulate the outside walls and ceiling. There are two 9ftX10ft rollup doors, "
        "two 7ftX36\" walk-in doors, and five 24\"x36\" windows."
    )

    result = ai_scope_interpreter.interpret_field_notes_with_ai(
        notes,
        deterministic_scope={},
        provider=lambda _notes, _scope: {
            "estimate_mode": "insulation",
            "division": "Insulation",
            "project_type": "spray foam insulation",
            "building_type": "metal building",
            "building_length_ft": 30,
            "building_width_ft": 40,
            "wall_height_ft": 9,
            "ceiling_included": True,
            "outside_walls_included": True,
            "gross_insulation_area_sqft": 9999,
            "openings": [
                {"opening_type": "rollup_door", "quantity": 2, "width_ft": 9, "height_ft": 10, "source_text": "two 9ftX10ft rollup doors"},
                {"opening_type": "walk_in_door", "quantity": 2, "width_ft": 3, "height_ft": 7, "source_text": "two 7ftX36\" walk-in doors"},
                {"opening_type": "window", "quantity": 5, "width_ft": 2, "height_ft": 3, "source_text": "five 24\"x36\" windows"},
            ],
        },
    )

    assert result["ceiling_area_sqft"] == 1200
    assert result["gross_wall_area_sqft"] == 1260
    assert result["gross_insulation_area_sqft"] == 2460
    assert result["opening_area_known_sqft"] == 252
    assert result["net_insulation_area_sqft"] == 2208
    assert result["estimated_sqft"] == 2208
    assert any("conflicted with deterministic insulation math" in flag for flag in result["review_flags"])


def test_ai_insulation_geometry_keeps_missing_rollup_width() -> None:
    notes = "Foam sprayed in a 30x40 metal building with 9' walls, outside walls and ceiling. Two 9 ft rollup doors."

    result = ai_scope_interpreter.interpret_field_notes_with_ai(
        notes,
        deterministic_scope={},
        provider=lambda _notes, _scope: {
            "estimate_mode": "insulation",
            "division": "Insulation",
            "project_type": "spray foam insulation",
            "building_length_ft": 30,
            "building_width_ft": 40,
            "wall_height_ft": 9,
            "ceiling_included": True,
            "outside_walls_included": True,
            "openings": [
                {"opening_type": "rollup_door", "quantity": 2, "height_ft": 9, "source_text": "Two 9 ft rollup doors"},
            ],
        },
    )

    assert result["gross_insulation_area_sqft"] == 2460
    assert result["opening_area_known_sqft"] == 0
    assert result["net_insulation_area_sqft"] == 2460
    assert result["opening_area_missing"] is True
    assert "Rollup door width?" in result["missing_questions"]
