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
    )
    calls = []

    def provider(messages, model):
        calls.append((messages, model))
        assert "common_template_buckets" in messages[1]["content"]
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
