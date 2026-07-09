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

ROOFING_REFERENCE_TEMPLATE_SUMMARY = """Section	Source Row	Line Item	Basis / Units	Unit Price / Rate	Estimated Cost	Notes
Materials	19	Gaco Roof 2.7	844.44 est. units; 1.50 thickness	$2.10	$1,773.33	Foam
Materials	26	Gaco Silicone	17.83 gal/sq units	$36.00	$641.70	Coating
Materials	27	Gaco Silicone	17.83 gal/sq units	$36.00	$641.70	Coating
Materials	36	3M Granules	7.75 units	$26.00	$201.50	Granules
Materials	43	Silicone Sausage	32 units	$12.00	$384.00	Caulk / sealant
Materials	45	Gaco SF-2000	10 units	$40.00	$400.00	Caulk / sealant
Materials	63	Fasteners	516 units	$250.00 / 1,000	$129.00	5 inch
Materials	65	Plates	516 units	$200.00 / 1,000	$103.20
Materials	96	Dumpster	1 unit	$600.00	$600.00
Materials	99	Generator	3 est. days	$50.00	$150.00
Materials	106	Sales / Inspect.	2 trips x 315 miles	$1.00	$630.00	Mileage
Materials	108	Truck Exp.	3 trips x 320 miles	$1.00	$960.00	Mileage
Labor / Subcontractor	116	Set Up / Safety	0.10 days; 5 people	$1,605.50 daily rate	$160.55
Labor / Subcontractor	118	Tear-out, Board, Foam & base	1.25 days; 5 people	$1,605.50 daily rate	$2,006.88
Labor / Subcontractor	122	Walk + Caulk	0.30 days; 5 people	$1,605.50 daily rate	$481.65
Labor / Subcontractor	126	Top & granules	0.35 days; 5 people	$1,605.50 daily rate	$561.93
Labor / Subcontractor	134	Misc. / Clean Up	0.50 days; 5 people	$1,605.50 daily rate	$802.75
Labor / Subcontractor	137	Loading	2.00 hours; 1 person	$25.50	$153.00	Multiplied by truck trips
Labor / Subcontractor	139	Traveling	5.00 hours; 5 people	$13.00	$975.00	Multiplied by truck trips
Labor / Subcontractor	145	Meals, Lodging Expenses	3 days; 5 people	$175.00	$2,625.00
Additional Amount w/o Markup	173	Warranty			$600.00	Added after worksheet price
"""


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
        estimator_memory=pd.DataFrame(
            [
                {
                    "status": "approved",
                    "priority": "high",
                    "template_type": "insulation",
                    "template_bucket": "labor_loading",
                    "guidance": "For spray foam insulation, Loading is normally a short setup item, not a full labor day.",
                    "rationale": "Estimator correction from prior chat session.",
                    "source_type": "estimator_correction",
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
        assert "template_fallback_defaults" in messages[1]["content"]
        assert "estimator_memory_guidance" in messages[1]["content"]
        assert "Loading is normally a short setup item" in messages[1]["content"]
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

    context = json.loads(calls[0][0][1]["content"])["estimator_context"]
    assert context["estimator_memory_guidance"][0]["template_bucket"] == "labor_loading"
    assert context["template_fallback_defaults"]["insulation_foam"]["yield_or_coverage"] == 2600
    assert context["template_fallback_defaults"]["insulation_foam"]["unit_price"] == 2.25
    loading = next(row for row in context["decision_menu"] if row["template_bucket"] == "labor_loading")
    traveling = next(row for row in context["decision_menu"] if row["template_bucket"] == "labor_traveling")
    assert loading["section"] == "insulation_logistics_expense_template_decisions"
    assert traveling["section"] == "insulation_logistics_expense_template_decisions"
    assert "hours_per_day" in loading["editable_fields"]


def test_estimator_chat_normalizes_loading_travel_to_logistics_formula_fields() -> None:
    def provider(_messages, _model):
        return {
            "assistant_message": "Drafted logistics decisions.",
            "estimator_notes": "Open cell insulation job with loading and travel.",
            "scope_overrides": {"template_type": "insulation"},
            "workbook_decision_preferences": [
                {
                    "template_bucket": "labor_loading",
                    "workbook_row": "95",
                    "include": True,
                    "proposed_values": {"days": 1, "crew_size": 2, "daily_rate": 1685.775},
                },
                {
                    "template_bucket": "labor_traveling",
                    "workbook_row": "97",
                    "include": True,
                    "days": 2,
                    "crew_size": 5,
                    "daily_rate": 1685.775,
                },
            ],
            "confidence": 0.7,
        }

    result = run_estimator_chat_turn(
        [{"role": "user", "content": "30x40 metal building, open cell foam, include loading and travel."}],
        template_type_hint="insulation",
        provider=provider,
        model="test-model",
    )

    loading, traveling = result.workbook_decision_preferences

    assert loading["section"] == "insulation_logistics_expense_template_decisions"
    assert loading["proposed_values"] == {"hours_per_day": 1.0, "people_count": 2.0, "unit_price": 25.5}
    assert "daily_rate" not in loading
    assert traveling["section"] == "insulation_logistics_expense_template_decisions"
    assert traveling["proposed_values"] == {"hours_per_day": 2.0, "people_count": 5.0, "unit_price": 13.0}
    assert "daily_rate" not in traveling


def test_estimator_chat_caps_bad_loading_travel_values() -> None:
    def provider(_messages, _model):
        return {
            "assistant_message": "Drafted logistics decisions.",
            "estimator_notes": "Open cell insulation job with loading and travel.",
            "scope_overrides": {"template_type": "insulation"},
            "workbook_decision_preferences": [
                {
                    "template_bucket": "labor_loading",
                    "workbook_row": "95",
                    "include": True,
                    "proposed_values": {"hours_per_day": 8, "people_count": 2, "unit_price": 50},
                },
                {
                    "template_bucket": "labor_traveling",
                    "workbook_row": "97",
                    "include": True,
                    "proposed_values": {"hours_per_day": 8, "people_count": 2, "unit_price": 50},
                },
            ],
            "confidence": 0.7,
        }

    result = run_estimator_chat_turn(
        [{"role": "user", "content": "30x40 metal building, open cell foam, include loading and travel."}],
        template_type_hint="insulation",
        provider=provider,
        model="test-model",
    )

    loading, traveling = result.workbook_decision_preferences

    assert loading["proposed_values"] == {"hours_per_day": 0.5, "people_count": 2.0, "unit_price": 25.5}
    assert traveling["proposed_values"] == {"hours_per_day": 2.5, "people_count": 2.0, "unit_price": 13.0}


def test_estimator_chat_normalizes_alias_only_loading_travel_rows() -> None:
    def provider(_messages, _model):
        return {
            "assistant_message": "Drafted logistics decisions.",
            "estimator_notes": "Open cell insulation job with loading and travel.",
            "scope_overrides": {"template_type": "insulation"},
            "workbook_decision_preferences": [
                {
                    "decision_id": "labor loading",
                    "include": True,
                    "proposed_values": {"hours_per_day": 8, "people_count": 2, "trip_count": 1, "unit_price": 1685.775},
                },
                {
                    "decision_id": "labor traveling",
                    "include": True,
                    "proposed_values": {"hours_per_day": 8, "people_count": 5, "trip_count": 2, "unit_price": 1685.775},
                },
            ],
            "confidence": 0.7,
        }

    result = run_estimator_chat_turn(
        [{"role": "user", "content": "30x40 metal building, open cell foam, include loading and travel."}],
        template_type_hint="insulation",
        provider=provider,
        model="test-model",
    )

    loading, traveling = result.workbook_decision_preferences

    assert loading["section"] == "insulation_logistics_expense_template_decisions"
    assert loading["template_bucket"] == "labor_loading"
    assert loading["workbook_row"] == "95"
    assert loading["proposed_values"] == {"hours_per_day": 0.5, "people_count": 2.0, "trip_count": 1, "unit_price": 25.5}
    assert traveling["section"] == "insulation_logistics_expense_template_decisions"
    assert traveling["template_bucket"] == "labor_traveling"
    assert traveling["workbook_row"] == "97"
    assert traveling["proposed_values"] == {"hours_per_day": 2.5, "people_count": 5.0, "trip_count": 2, "unit_price": 13.0}


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


def test_estimator_chat_parses_pasted_roofing_reference_template_summary(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = run_estimator_chat_turn(
        [{"role": "user", "content": ROOFING_REFERENCE_TEMPLATE_SUMMARY}],
        template_type_hint="roofing",
    )

    by_id = {row["decision_id"]: row for row in result.workbook_decision_preferences}

    assert result.scope_overrides["template_type"] == "roofing"
    assert result.scope_overrides["reference_template_summary_present"] is True
    assert result.scope_overrides["reference_template_summary_mapped_row_count"] >= 20

    foam = by_id["roofing_foam_row_19"]
    assert foam["source"] == "reference_template_summary"
    assert foam["proposed_values"]["estimated_units"] == 844.44
    assert foam["proposed_values"]["thickness_inches"] == 1.5
    assert foam["proposed_values"]["unit_price"] == 2.1
    assert foam["evidence"][0]["source_row"] == "19"

    assert by_id["roofing_coating_system_row_26"]["proposed_values"]["estimated_units"] == 17.83
    assert by_id["roofing_coating_system_row_27"]["workbook_row"] == "27"
    assert by_id["roofing_caulk_sealant_row_43"]["proposed_values"]["estimated_units"] == 32.0
    assert by_id["roofing_caulk_sealant_row_45"]["proposed_values"]["unit_price"] == 40.0
    assert by_id["roofing_fasteners_row_63"]["proposed_values"]["unit_price_per_thousand"] == 250.0
    assert by_id["roofing_plates_row_65"]["proposed_values"]["unit_price_per_thousand"] == 200.0

    dumpster = by_id["roofing_dumpsters_row_69"]
    assert dumpster["workbook_row"] == "69"
    assert "Source row 96 was normalized to current workbook row 69." in dumpster["review_reasons"]
    assert dumpster["proposed_values"]["estimated_units"] == 1.0
    assert dumpster["proposed_values"]["unit_price"] == 600.0

    loading = by_id["roofing_labor_loading_row_136"]
    assert loading["section"] == "roofing_logistics_expense_template_decisions"
    assert loading["workbook_row"] == "136"
    assert loading["proposed_values"] == {
        "hours_per_day": 2.0,
        "people_count": 1.0,
        "trip_count": 3.0,
        "unit_price": 25.5,
    }
    assert "Source row 137 was normalized to current workbook row 136." in loading["review_reasons"]

    traveling = by_id["roofing_labor_traveling_row_138"]
    assert traveling["proposed_values"] == {
        "hours_per_day": 5.0,
        "people_count": 5.0,
        "trip_count": 3.0,
        "unit_price": 13.0,
    }
    meals = by_id["roofing_meals_lodging_row_144"]
    assert meals["proposed_values"] == {"days": 3.0, "people_count": 5.0, "unit_price": 175.0}

    assert by_id["roofing_labor_base_row_122"]["proposed_values"]["days"] == 1.25
    assert by_id["roofing_labor_base_row_122"]["proposed_values"]["crew_size"] == 5.0
    assert by_id["roofing_labor_base_row_122"]["proposed_values"]["daily_rate"] == 1605.5
    assert by_id["roofing_labor_top_coat_row_124"]["workbook_row"] == "124"
    assert any("Warranty" in warning for warning in result.warnings)


def test_estimator_chat_merges_pasted_reference_summary_after_ai_payload() -> None:
    def provider(_messages, _model):
        return {
            "assistant_message": "Drafted a repair scope.",
            "estimator_notes": "Use the pasted template as the source of truth for worksheet decisions.",
            "scope_overrides": {"template_type": "roofing"},
            "workbook_decision_preferences": [
                {
                    "decision_id": "roofing_labor_loading_row_136",
                    "section": "roofing_logistics_expense_template_decisions",
                    "template_bucket": "labor_loading",
                    "workbook_row": "136",
                    "include": True,
                    "proposed_values": {"hours_per_day": 8, "people_count": 2, "unit_price": 50},
                    "confidence": 0.4,
                }
            ],
            "confidence": 0.7,
        }

    result = run_estimator_chat_turn(
        [{"role": "user", "content": ROOFING_REFERENCE_TEMPLATE_SUMMARY}],
        template_type_hint="roofing",
        provider=provider,
        model="test-model",
    )

    by_id = {row["decision_id"]: row for row in result.workbook_decision_preferences}
    assert result.source == "ai_chat"
    assert by_id["roofing_foam_row_19"]["proposed_values"]["estimated_units"] == 844.44
    assert by_id["roofing_labor_loading_row_136"]["proposed_values"] == {
        "hours_per_day": 2.0,
        "people_count": 1.0,
        "trip_count": 3.0,
        "unit_price": 25.5,
    }
