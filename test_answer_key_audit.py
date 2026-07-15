from __future__ import annotations

import json

import pandas as pd

from jobscan.estimator.schemas import EstimatorData
from jobscan.estimator.template_examples import _token_overlap_score
from scripts.audit_estimator_answer_keys import _chat_context_cue_provider
from scripts.evaluate_answer_key_context_retrieval import _semantic_context_data, _semantic_notes


def test_chat_context_cue_provider_converts_cues_to_reference_preferences() -> None:
    captures: list[dict] = []
    provider = _chat_context_cue_provider(captures)
    payload = {
        "conversation": [{"role": "user", "content": "Metal roof needs coating and primer."}],
        "existing_scope": {"template_type": "roofing", "roof_area_sqft": 9600},
        "estimator_context": {
            "historical_answer_key_examples": {"matched_answer_keys": [{"case_id": "example"}]},
            "historical_answer_key_decision_cues": [
                {
                    "decision_id": "roofing_coating_system_row_26",
                    "section": "roofing_coating_template_decisions",
                    "template_bucket": "coating",
                    "workbook_row": "26",
                    "sample_inputs": {"basis_sqft": 9600, "gal_per_100_sqft": 1.5, "unit_price": 32},
                    "support_count": 3,
                }
            ],
        },
    }

    result = provider(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": json.dumps(payload)},
        ],
        "context-cue-provider",
    )

    preferences = result["workbook_decision_preferences"]
    assert result["scope_overrides"]["roof_area_sqft"] == 9600
    assert result["estimator_notes"] == "Metal roof needs coating and primer."
    assert len(preferences) == 1
    assert preferences[0]["source"] == "reference_estimate_answer_key"
    assert preferences[0]["evidence"][0]["source"] == "historical_answer_key_decision_cue"
    assert preferences[0]["proposed_values"]["gal_per_100_sqft"] == 1.5
    assert captures[0]["matched_answer_key_count"] == 1
    assert captures[0]["decision_cue_count"] == 1
    assert captures[0]["preference_count"] == 1


def test_semantic_retrieval_context_strips_identity_fields() -> None:
    notes = (
        "Mudd's Furniture Roof B\n"
        "Historical proposal/source: Proposal - Roof B.pdf\n"
        "Site Address: 123 Main St\n"
        "Field notes reconstructed from historical proposal scope:\n"
        "Metal roof coating with primer and 15-year warranty."
    )
    data = EstimatorData(
        template_examples=pd.DataFrame(
            [
                {
                    "customer": "Mudd's Furniture",
                    "job_name": "Mudd's Furniture Roof B",
                    "source_file": "Proposal - Roof B.pdf",
                    "scope_summary": notes,
                }
            ]
        )
    )

    sanitized = _semantic_context_data(data).template_examples.iloc[0].to_dict()

    assert sanitized["customer"] == ""
    assert sanitized["job_name"] == ""
    assert sanitized["source_file"] == ""
    assert "Mudd" not in sanitized["scope_summary"]
    assert "Historical proposal/source" not in sanitized["scope_summary"]
    assert _semantic_notes(notes).startswith("Metal roof coating")


def test_answer_key_text_overlap_ignores_contact_and_date_noise() -> None:
    generic_score, generic_reason = _token_overlap_score(
        "Address 502-555-1212 proposal date 2025 customer contact",
        "Address 502-555-1212 proposal date 2025 customer contact",
    )
    technical_score, technical_reason = _token_overlap_score(
        "Metal roof silicone coating with primer and seam treatment",
        "Metal substrate silicone coating primer seams",
    )

    assert generic_score == 0
    assert generic_reason == ""
    assert technical_score > 0
    assert "silicone" in technical_reason or "coating" in technical_reason
