from __future__ import annotations

import json

import pandas as pd

from jobscan.estimator.chat_assistant import estimator_context_summary, run_estimator_chat_turn
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
                {
                    "template_type": "insulation",
                    "template_bucket": "foam",
                    "line_item_kind": "material",
                    "selected_item_name": "Open Cell SPF",
                    "area_sqft": 2200,
                    "thickness_inches": 5.5,
                    "yield_or_coverage": 4500,
                    "estimated_units": 2688.8889,
                    "job_id": "I1",
                },
                {
                    "template_type": "insulation",
                    "template_bucket": "foam",
                    "line_item_kind": "material",
                    "selected_item_name": "Open Cell SPF",
                    "area_sqft": 2400,
                    "thickness_inches": 5.25,
                    "yield_or_coverage": 4400,
                    "estimated_units": 2863.6364,
                    "job_id": "I2",
                },
                {"template_type": "insulation", "template_bucket": "labor_foam"},
            ]
        ),
        pricing=pd.DataFrame([{"item_name": "Open Cell SPF"}]),
        template_product_options=pd.DataFrame(
            [
                {
                    "template_type": "insulation",
                    "template_bucket": "foam",
                    "product_name": "Open Cell SPF",
                    "unit": "set",
                    "unit_price": 1600,
                    "yield_or_coverage": 4500,
                }
            ]
        ),
        product_catalog=pd.DataFrame(
            [
                {
                    "product_id": "open-cell",
                    "product_name": "Open Cell SPF",
                    "category": "spray_foam",
                    "recommended_use": "Open-cell insulation where vapor drive and code requirements are reviewed.",
                }
            ]
        ),
        relationship_package_cooccurrence=pd.DataFrame(
            [
                {
                    "template_type": "insulation",
                    "source_package": "foam",
                    "companion_package": "labor_foam",
                    "cooccurrence_rate": 0.94,
                    "evidence_count": 18,
                }
            ]
        ),
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
        assert "decision_menu" in messages[1]["content"]
        assert "formula_requirements" in messages[1]["content"]
        assert "pricing_candidates_by_bucket" in messages[1]["content"]
        assert "product_guidance_digest" in messages[1]["content"]
        assert "companion_relationships" in messages[1]["content"]
        assert "foam_yield_history_digest" in messages[1]["content"]
        assert "yield_or_coverage" in messages[1]["content"]
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


def test_estimator_chat_context_includes_thickness_matched_foam_yield_digest() -> None:
    data = EstimatorData(
        template_rows=pd.DataFrame(
            [
                {
                    "template_type": "insulation",
                    "template_bucket": "foam",
                    "line_item_kind": "material",
                    "selected_item_name": "Gaco 0.5 lb.",
                    "area_sqft": 2200,
                    "thickness_inches": 5.5,
                    "yield_or_coverage": 4500,
                    "estimated_units": 2688.8889,
                    "job_id": "I1",
                },
                {
                    "template_type": "insulation",
                    "template_bucket": "foam",
                    "line_item_kind": "material",
                    "selected_item_name": "Gaco 2.0 lb.",
                    "area_sqft": 2200,
                    "thickness_inches": 2,
                    "yield_or_coverage": 2600,
                    "estimated_units": 1692.3077,
                    "job_id": "I2",
                },
            ]
        )
    )

    context = estimator_context_summary(
        data,
        scope={"template_type": "insulation", "foam_type": "open_cell", "foam_thickness_inches": 5.53},
    )

    digest = context["foam_yield_history_digest"]
    assert digest
    assert digest[0]["foam_type"] == "open_cell"
    assert digest[0]["thickness_band"] == "4-6"
    assert digest[0]["median_yield_or_coverage"] == 4500


def test_estimator_chat_decision_menu_uses_template_catalog_metadata() -> None:
    data = EstimatorData(
        template_row_catalog=pd.DataFrame(
            [
                {
                    "template_type": "roofing",
                    "template_name": "Roofing Template",
                    "sheet_name": "Estimate",
                    "row_number": 26,
                    "section": "Materials",
                    "template_bucket": "coating",
                    "line_item_kind": "material",
                    "formula_model": "selector_lookup",
                    "cell_roles_json": json.dumps({"A": "selector_code", "C": "area_sqft", "G": "unit_price"}),
                },
                {
                    "template_type": "roofing",
                    "template_name": "Roofing Template",
                    "sheet_name": "Estimate",
                    "row_number": 122,
                    "section": "Labor",
                    "template_bucket": "labor_base",
                    "line_item_kind": "labor",
                    "formula_model": "mixed_hours_or_daily",
                    "cell_roles_json": json.dumps({"B": "days", "D": "total_hours", "G": "daily_rate", "J": "hourly_rate"}),
                },
                {
                    "template_type": "roofing",
                    "template_name": "Roofing Template",
                    "sheet_name": "Estimate",
                    "row_number": 163,
                    "section": "Totals",
                    "template_bucket": "total_job_cost",
                    "line_item_kind": "total",
                    "formula_model": "sum_total",
                    "cell_roles_json": "{}",
                },
            ]
        ),
        template_formula_models=pd.DataFrame(
            [
                {
                    "template_type": "roofing",
                    "template_name": "Roofing Template",
                    "sheet_name": "Estimate",
                    "cell_address": "H26",
                    "row_number": 26,
                    "template_bucket": "coating",
                    "formula_model": "coating_gallons_from_area_rate_waste",
                    "dependencies_json": json.dumps(["C26", "E26", "G26"]),
                }
            ]
        ),
        decision_history_tables={
            "decision_nodes": pd.DataFrame(
                [
                    {
                        "template_type": "roofing",
                        "decision_id": "roofing_coating_system",
                        "title": "Roofing Coating System",
                        "category": "product_selection",
                        "rows_controlled": [26],
                    },
                    {
                        "template_type": "roofing",
                        "decision_id": "roofing_labor_base",
                        "title": "Base Roofing Labor",
                        "category": "labor_planning",
                        "rows_controlled": [122],
                    },
                ]
            ),
            "row_traceability": pd.DataFrame(
                [
                    {
                        "template_type": "roofing",
                        "decision_id": "roofing_coating_system",
                        "row_number": 26,
                        "template_bucket": "coating",
                    },
                    {
                        "template_type": "roofing",
                        "decision_id": "roofing_labor_base",
                        "row_number": 122,
                        "template_bucket": "labor_base",
                    },
                ]
            ),
        },
    )

    context = estimator_context_summary(data, scope={"template_type": "roofing"})
    menu = context["decision_menu"]
    coating = next(row for row in menu if row["template_bucket"] == "coating" and row["workbook_row"] == "26")
    labor = next(row for row in menu if row["template_bucket"] == "labor_base" and row["workbook_row"] == "122")

    assert coating["source"] == "template_row_catalog+decision_graph"
    assert coating["decision_id"] == "roofing_coating_system"
    assert coating["label"] == "Roofing Coating System"
    assert coating["graph_category"] == "product_selection"
    assert "selector_code" in coating["editable_fields"]
    assert "basis_sqft" in coating["formula_requirements"]
    assert "unit_price" in coating["formula_requirements"]
    assert labor["decision_id"] == "roofing_labor_base"
    assert labor["section"] == "roofing_labor_template_decisions"
    assert "daily_rate" in labor["editable_fields"]
    assert not any(row["template_bucket"] == "total_job_cost" for row in menu)
    assert any(row["source"] == "curated_fallback" for row in menu)


def test_estimator_chat_sends_multi_turn_history_and_existing_scope_to_provider() -> None:
    calls = []

    def provider(messages, model):
        calls.append((messages, model))
        payload = messages[1]["content"]
        assert "Confirm target R-value" in payload
        assert "closed cell at R-21" in payload
        assert '"existing_scope"' in payload
        assert '"foam_type": "closed_cell"' in payload
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


def test_estimator_chat_preserves_full_takeoff_across_followup_answers() -> None:
    def provider(messages, model):
        payload = messages[1]["content"]
        assert "30x40 metal building" in payload
        assert "open cell foam, r21" in payload
        assert '"net_insulation_area_sqft": 2226.0' in payload
        return {
            "assistant_message": "Using R21 and 3.8 R/in gives about 5.53 inches.",
            "estimator_notes": "Open cell foam at R21, simple access.",
            "scope_overrides": {
                "template_type": "insulation",
                "foam_type": "open_cell",
                "foam_thickness_inches": 5.53,
            },
            "workbook_decision_preferences": [
                {
                    "decision_id": "insulation_foam_template_selector",
                    "template_bucket": "foam",
                    "include": True,
                    "proposed_values": {"foam_type": "open_cell", "thickness_inches": 5.53},
                    "confidence": 0.82,
                    "review_required": True,
                }
            ],
            "confidence": 0.82,
        }

    result = run_estimator_chat_turn(
        [
            {"role": "user", "content": COLLINS_NOTE.replace("Open cell spray foam.", "")},
            {"role": "assistant", "content": "Questions to confirm:\n- What type of foam insulation do you prefer?\n- Do you have specific R-value targets?"},
            {"role": "user", "content": "open cell foam, r21, metal barn with simple access"},
            {"role": "assistant", "content": "Workbook changes proposed:\n- include insulation: foam_type=open_cell, insulation_r_value_targets={'walls': 21, 'ceiling': 21}"},
            {"role": "user", "content": "3.8 R/in can be used to estimate required thickness"},
        ],
        template_type_hint="insulation",
        provider=provider,
        model="test-model",
    )

    scope = result.scope_overrides
    assert scope["template_type"] == "insulation"
    assert scope["foam_type"] == "open_cell"
    assert scope["building_footprint_length_ft"] == 30
    assert scope["building_footprint_width_ft"] == 40
    assert scope["wall_height_ft"] == 9
    assert scope["opening_area_known_sqft"] == 234
    assert scope["net_insulation_area_sqft"] == 2226
    assert scope["estimated_sqft"] == 2226
    assert scope["target_r_value"] == 21
    assert scope["r_value_per_inch_assumption"] == 3.8
    assert scope["foam_thickness_inches"] == 5.53
    assert "30x40 metal building" in result.estimator_notes


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
