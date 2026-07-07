from __future__ import annotations

import pandas as pd

from jobscan.estimator.chat_assistant import run_estimator_chat_turn
from jobscan.estimator.schemas import EstimatorData


COLLINS_NOTE = (
    "Hello, I am wanting to get a quote for getting foam sprayed in a 30x40 metal building "
    "with 9' walls. What I want to have insulated is the outside walls and ceiling of the building. "
    "The building will have two 9ft rollup doors, two 36\" walk-in doors and five 24\"x36\" windows. "
    "The plan is to have the foam done around September or October. Open cell spray foam."
)


def test_estimator_chat_fallback_extracts_insulation_takeoff_without_inventing_thickness(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = run_estimator_chat_turn(
        [{"role": "user", "content": COLLINS_NOTE}],
        template_type_hint="insulation",
    )

    scope = result.scope_overrides

    assert result.source == "deterministic_fallback"
    assert scope["template_type"] == "insulation"
    assert scope["foam_type"] == "open_cell"
    assert scope["gross_wall_area_sqft"] == 1260
    assert scope["opening_area_known_sqft"] == 234
    assert scope["ceiling_area_sqft"] == 1200
    assert scope["net_insulation_area_sqft"] == 2226
    assert "foam_thickness_inches" not in scope
    assert any("thickness" in question.lower() for question in result.missing_questions)


def test_estimator_chat_uses_provider_payload_and_context_summary() -> None:
    data = EstimatorData(
        template_rows=pd.DataFrame(
            [
                {"template_type": "insulation", "template_bucket": "foam"},
                {"template_type": "insulation", "template_bucket": "foam"},
                {"template_type": "insulation", "template_bucket": "labor_foam"},
            ]
        ),
        pricing=pd.DataFrame([{"item_name": "Open Cell SPF"}]),
        estimator_decision_recommendations=pd.DataFrame(
            [
                {
                    "template_type": "insulation",
                    "template_bucket": "foam",
                    "decision_id": "insulation_foam_system",
                    "decision_value": "open_cell",
                    "evidence_count": 14,
                    "confidence": "high",
                }
            ]
        ),
    )
    calls = []

    def provider(messages, model):
        calls.append((messages, model))
        assert "common_template_buckets" in messages[1]["content"]
        assert "decision_recommendation_examples" in messages[1]["content"]
        return {
            "assistant_message": "Drafted the insulation estimate.",
            "estimator_notes": "30x40 metal building, open-cell foam, 2226 sq ft.",
            "scope_overrides": {
                "template_type": "insulation",
                "division": "Insulation",
                "estimated_sqft": 2226,
                "foam_type": "open_cell",
            },
            "workbook_decision_preferences": [
                {
                    "decision_id": "insulation_foam_template_selector",
                    "template_bucket": "foam",
                    "include": True,
                    "confidence": 0.8,
                }
            ],
            "missing_questions": ["Confirm thickness."],
            "confidence": 0.82,
        }

    result = run_estimator_chat_turn(
        [{"role": "user", "content": COLLINS_NOTE}],
        data=data,
        template_type_hint="insulation",
        provider=provider,
        model="test-model",
    )

    assert calls[0][1] == "test-model"
    assert result.source == "ai_chat"
    assert result.confidence == 0.82
    assert result.scope_overrides["estimated_sqft"] == 2226
    assert result.workbook_decision_preferences[0]["template_bucket"] == "foam"


def test_estimator_chat_sends_multi_turn_history_and_existing_scope_to_provider() -> None:
    calls = []

    def provider(messages, model):
        calls.append((messages, model))
        payload = messages[1]["content"]
        assert "Confirm target R-value" in payload
        assert "closed cell at R-21" in payload
        assert '"existing_scope"' in payload
        assert '"foam_type": "open_cell"' in payload
        return {
            "assistant_message": "Updated the foam selection and marked thickness for review.",
            "estimator_notes": "Use closed cell at R-21; thickness should be verified by product R-value.",
            "scope_overrides": {
                "template_type": "insulation",
                "foam_type": "closed_cell",
                "target_r_value": 21,
            },
            "workbook_decision_preferences": [
                {
                    "decision_id": "insulation_foam_system",
                    "template_bucket": "foam",
                    "include": True,
                    "proposed_values": {"foam_type": "closed_cell", "target_r_value": 21},
                    "confidence": 0.74,
                    "review_required": True,
                }
            ],
            "missing_questions": ["Confirm thermal barrier requirements."],
            "confidence": 0.74,
        }

    result = run_estimator_chat_turn(
        [
            {"role": "user", "content": "30x40 metal building, outside walls and ceiling."},
            {"role": "assistant", "content": "Questions to confirm:\n- Confirm target R-value or foam thickness."},
            {"role": "user", "content": "closed cell at R-21"},
        ],
        template_type_hint="insulation",
        existing_scope={"template_type": "insulation", "foam_type": "open_cell"},
        provider=provider,
        model="test-model",
    )

    assert calls
    assert result.scope_overrides["foam_type"] == "closed_cell"
    assert result.workbook_decision_preferences[0]["review_required"] is True


def test_estimator_chat_normalizes_decision_patch_aliases() -> None:
    def provider(messages, model):
        return {
            "assistant_message": "Removed fabric and updated seam labor.",
            "estimator_notes": "No fabric unless open seams are confirmed.",
            "scope_overrides": {"template_type": "roofing"},
            "decision_patches": [
                {
                    "decision_id": "roofing_fabric_row_79",
                    "template_bucket": "fabric",
                    "workbook_row": "79",
                    "include": False,
                    "confidence": 0.81,
                    "review_required": False,
                },
                {
                    "decision_id": "roofing_labor_seam_sealer_row_120",
                    "template_bucket": "labor_seam_sealer",
                    "workbook_row": "120",
                    "include": True,
                    "proposed_values": {"days": 0.5},
                    "confidence": 0.7,
                    "review_required": True,
                },
            ],
            "confidence": 0.78,
        }

    result = run_estimator_chat_turn(
        [{"role": "user", "content": "Remove fabric but keep half day for seam sealer."}],
        template_type_hint="roofing",
        provider=provider,
        model="test-model",
    )

    assert [row["decision_id"] for row in result.workbook_decision_preferences] == [
        "roofing_fabric_row_79",
        "roofing_labor_seam_sealer_row_120",
    ]
    assert result.workbook_decision_preferences[0]["include"] is False
    assert result.workbook_decision_preferences[1]["proposed_values"]["days"] == 0.5
