from __future__ import annotations

import json

import pandas as pd

from jobscan.estimator.chat_assistant import (
    detect_estimator_learning_intent,
    detect_reference_answer_key_mode,
    estimator_context_cache_stats,
    estimator_context_summary,
    run_estimator_chat_turn,
)
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
Materials Tax	111	Sales Tax	6% of subtotal materials	6%	$396.87	Calculated on Estimate!H110
Additional Amount w/o Markup	173	Warranty			$600.00	Added after worksheet price
Additional Amount w/o Markup	174	Misc. Miles			$250.00	Added after worksheet price
Additional Amount w/o Markup	175	Misc. Materials / Misc. Insurance / Equipment Rental			$1,500.00	Added after worksheet price
Additional Amount w/o Markup	176	Dumpster - 800			$800.00	Added after worksheet price
Additional Amount w/o Markup	177	1/2 in HD Board = $1514 + tax & ST Mark Up			$1,800.00	Added after worksheet price
Additional Amount w/o Markup	178	3 in ISO Board - $2340 + tax & ST Mark Up			$2,700.00	Added after worksheet price
"""

ROOFING_COMPACT_REFERENCE_TEMPLATE_SUMMARY = (
    "Bill Gatti 3700 Klondike Ln Louisville ~8000 sf metal roof (+ ribs) Metal roof restorable. "
    "Here's how a human estimated this in the past for reference: "
    "Materials 26 Gaco Silicone 9,600 sq ft @ 1.50 gal/sq; 165.6 est. units $32.00 $5,299.20 Top coat material "
    "Materials 39 Gaco E-5320 Primer 9,600 sq ft; 38.4 est. units $33.00 $1,267.20 Primer "
    "Materials 43 Silicone Sausage 96 units $12.00 $1,152.00 Caulk/sealant "
    "Materials 45 Gaco SF-2000 30.0 units $35.00 $1,050.00 Caulk/sealant "
    "Materials 63 Fasteners 2,063 units $250.00 $515.63 Fastener allowance "
    "Materials 99 Generator 7 est. days $50.00 $350.00 Equipment "
    "Materials 106 Sales/Inspect. 10 trips x 65 miles $0.75 $487.50 Mileage "
    "Materials 108 Truck Exp. 14 trips x 65 miles $1.25 $1,137.50 Mileage "
    "Tax 111 Sales Tax 6% of taxable materials 6.00% $675.54 Sales tax "
    "Labor / Subcontractor 116 Set-Up 0.20 days; 5 people; 10.5 hours $1,835.66 $367.13 Labor "
    "Labor / Subcontractor 118 Pwash & Prep 1.50 days; 5 people; 78.8 hours $1,835.66 $2,753.49 Labor "
    "Labor / Subcontractor 120 Prime 1.25 days; 5 people; 65.6 hours $1,835.66 $2,294.58 Labor "
    "Labor / Subcontractor 122 Fasteners/ caulk&SF 2.00 days; 5 people; 105.0 hours $1,835.66 $3,671.33 Labor "
    "Labor / Subcontractor 124 Top Coat 1.00 days; 5 people; 52.5 hours $1,835.66 $1,835.66 Labor "
    "Labor / Subcontractor 130 Misc. 0.50 days; 5 people; 26.3 hours $1,835.66 $917.83 Labor "
    "Labor / Subcontractor 132 Touch/Clean Up 0.55 days; 5 people; 28.9 hours $1,835.66 $1,009.61 Labor "
    "Labor / Subcontractor 137 Loading 1.00 hr/day; 1 person $25.50 $357.00 Labor "
    "Labor / Subcontractor 139 Traveling 2.00 hr/day; 5 people $14.25 $1,995.00 Labor "
    "Warranty / Insurance 154 Warranty 15 years; renew; 8,000 sq ft $0.11 $840.00 Manufacturer warranty "
    "Markup / Add-ons 165 Estimated O/H 35.00% of total job cost 35.00% $9,555.23 Overhead markup "
    "Markup / Add-ons 167 Profit 16.00% after O/H 16.00% $5,896.94 Profit markup "
    "Add-ons w/o Markup 173 Misc. Materials Additional amount without markup $750.00 Added after worksheet price "
    "Add-ons w/o Markup 174 Misc. Insurance Additional amount without markup $1,250.00 Added after worksheet price"
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
        job_context_profiles=pd.DataFrame(
            [
                {
                    "job_id": "I-HIST-1",
                    "customer": "Massey",
                    "job_name": "Pole Barn Insulation",
                    "template_type": "insulation",
                    "project_class": "insulation_pole_barn",
                    "market_segment": "agricultural",
                    "building_type": "pole_barn",
                    "substrate": "metal",
                    "material_system": "Gaco 0.5 lb., DC315",
                    "material_packages": ["foam", "thermal_barrier", "labor_foam"],
                    "material_packages_json": '["foam", "thermal_barrier", "labor_foam"]',
                    "area_sqft": 2226,
                    "area_bucket": "under_5k",
                    "scope_summary": "Pole barn spray foam insulation on walls and ceiling.",
                    "confidence": 0.85,
                }
            ]
        ),
        template_examples=pd.DataFrame(
            [
                {
                    "example_id": "insulation-metal-building-example",
                    "job_id": "I-HIST-1",
                    "customer": "Massey",
                    "job_name": "Pole Barn Insulation",
                    "template_type": "insulation",
                    "project_class": "insulation_pole_barn",
                    "market_segment": "agricultural",
                    "building_type": "pole_barn",
                    "substrate": "metal",
                    "material_system": "Open Cell SPF",
                    "material_packages_json": json.dumps(["foam", "labor_foam"]),
                    "area_sqft": 2226,
                    "area_bucket": "under_5k",
                    "scope_summary": "Pole barn spray foam insulation on walls and ceiling.",
                    "decision_summary": "row 19 foam Open Cell SPF",
                    "answer_key_json": json.dumps(
                        {
                            "schema_version": "reference_estimate_answer_key.v1",
                            "template_type": "insulation",
                            "source_workbook": {"file_name": "Estimate Insulation - Pole Barn.xlsx"},
                            "decisions": [
                                {
                                    "section": "insulation_foam_template_decisions",
                                    "decision_id": "insulation_foam_template_selector",
                                    "template_bucket": "foam",
                                    "workbook_row": "19-21",
                                    "line_item": "Open Cell SPF",
                                    "include": True,
                                    "inputs": {
                                        "basis_sqft": 2226,
                                        "thickness_inches": 5.5,
                                        "yield_or_coverage": 4500,
                                        "unit_price": 1600,
                                    },
                                    "calculated_outputs": {"estimated_cost": 4800},
                                }
                            ],
                            "summary": {"decision_count": 1, "unmapped_count": 0},
                        }
                    ),
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
        assert "historical_job_context" in messages[1]["content"]
        assert "historical_context_decision_guidance" in messages[1]["content"]
        assert "historical_template_examples" in messages[1]["content"]
        assert "historical_answer_key_examples" in messages[1]["content"]
        assert "insulation_thermal_barrier_coating" in messages[1]["content"]
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
    answer_key_examples = context["historical_answer_key_examples"]["matched_answer_keys"]
    assert answer_key_examples
    assert answer_key_examples[0]["job_id"] == "I-HIST-1"
    assert answer_key_examples[0]["reference_answer_key"]["decisions"][0]["decision_id"] == "insulation_foam_template_selector"
    assert context["template_fallback_defaults"]["insulation_foam"]["yield_or_coverage"] == 2600
    assert context["template_fallback_defaults"]["insulation_foam"]["unit_price"] == 2.25
    loading = next(row for row in context["decision_menu"] if row["template_bucket"] == "labor_loading")
    traveling = next(row for row in context["decision_menu"] if row["template_bucket"] == "labor_traveling")
    assert loading["section"] == "insulation_logistics_expense_template_decisions"
    assert traveling["section"] == "insulation_logistics_expense_template_decisions"
    assert "hours_per_day" in loading["editable_fields"]


def test_estimator_chat_marks_explicit_learning_messages() -> None:
    result = run_estimator_chat_turn(
        [
            {
                "role": "user",
                "content": "Learn from this answer key and generate the workbook.\n\n" + ROOFING_REFERENCE_TEMPLATE_SUMMARY,
            }
        ],
        template_type_hint="roofing",
        provider=lambda messages, model: {
            "assistant_message": "Mapped the answer key.",
            "estimator_notes": "Roofing answer key.",
            "scope_overrides": {"template_type": "roofing", "division": "Roofing"},
            "workbook_decision_preferences": [],
            "confidence": 0.8,
        },
        model="test-model",
    )

    assert detect_estimator_learning_intent([{"role": "user", "content": "learn from this"}])["auto_build_workbook"] is True
    assert result.learning_mode is True
    assert result.learning_intent["auto_save_memory"] is True
    assert result.learning_intent["auto_approve_memory"] is True
    assert result.scope_overrides["explicit_learning_intent"] is True
    assert result.scope_overrides["learning_reference_template_mapped_row_count"] > 0
    assert "Learning mode is on" in result.assistant_message


def test_estimator_chat_detects_answer_key_modes() -> None:
    assert detect_reference_answer_key_mode([{"role": "user", "content": "Apply this answer key to the workbook"}]) == "apply"
    assert detect_reference_answer_key_mode([{"role": "user", "content": "Learn from this answer key and remember it"}]) == "teach"
    assert detect_reference_answer_key_mode([{"role": "user", "content": "Evaluate against this answer key"}]) == "evaluate"
    assert detect_reference_answer_key_mode([{"role": "user", "content": "Here is the answer key"}]) == "evaluate"
    assert detect_estimator_learning_intent([{"role": "user", "content": "Here is the answer key"}]) == {}


def test_estimator_context_summary_cache_reports_hit_after_first_build() -> None:
    data = EstimatorData(
        template_rows=pd.DataFrame(
            [
                {
                    "template_type": "insulation",
                    "template_bucket": "foam",
                    "line_item_kind": "material",
                    "row_number": 19,
                    "selected_item_name": "Open Cell SPF",
                }
            ]
        )
    )
    scope = {"template_type": "insulation", "cache_test_marker": "context-cache"}
    before = estimator_context_cache_stats()

    first = estimator_context_summary(data, scope=scope)
    second = estimator_context_summary(data, scope=scope)
    after = estimator_context_cache_stats()

    assert first == second
    assert after["miss"] >= before["miss"] + 1
    assert after["hit"] >= before["hit"] + 1


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


def test_estimator_chat_context_includes_roofing_foam_yield_digest() -> None:
    data = EstimatorData(
        foam_yield_history=pd.DataFrame(
            [
                {
                    "template_type": "roofing",
                    "foam_type": "closed_cell",
                    "product": "Gaco Roof 2.7",
                    "template_option": "Gaco Roof 2.7",
                    "template_option_normalized": "gaco roof 2.7",
                    "thickness_inches": 1.5,
                    "thickness_band": "0-2",
                    "square_feet": 9600,
                    "area_sqft": 9600,
                    "estimated_yield": 17058.8,
                    "yield_or_coverage": 17058.8,
                    "estimated_units": 844.44,
                    "estimated_sets": 0.84444,
                    "unit_price": 2.1,
                    "job_id": "R1",
                }
            ]
        )
    )

    context = estimator_context_summary(
        data,
        scope={"template_type": "roofing", "foam_thickness_inches": 1.5, "raw_input_notes": "Roof SPF with Gaco Roof 2.7"},
    )

    digest = context["foam_yield_history_digest"]
    assert digest
    assert digest[0]["template_option"] == "Gaco Roof 2.7"
    assert digest[0]["median_square_feet"] == 9600
    assert digest[0]["median_estimated_sets"] == 0.84444


def test_estimator_chat_context_retrieves_similar_answer_keys_by_scope_packages() -> None:
    coating_answer_key = {
        "schema_version": "reference_estimate_answer_key.v1",
        "template_type": "roofing",
        "source_workbook": {"file_name": "Estimate - Metal Roof Coating + Foam.xlsx"},
        "decisions": [
            {
                "section": "roofing_accessory_template_decisions",
                "decision_id": "roofing_edge_metal_row_82",
                "template_bucket": "edge_metal",
                "workbook_row": "82",
                "line_item": "Edge Metal",
                "inputs": {"estimated_units": 10},
            },
            {
                "section": "roofing_coating_template_decisions",
                "decision_id": "roofing_coating_system_row_26",
                "template_bucket": "coating",
                "workbook_row": "26",
                "line_item": "Gaco Silicone",
                "inputs": {"basis_sqft": 9600, "gal_per_100_sqft": 1.5, "unit_price": 32},
                "calculated_outputs": {"estimated_cost": 5299.2},
            },
            {
                "section": "roofing_foam_template_decisions",
                "decision_id": "roofing_foam_row_19",
                "template_bucket": "foam",
                "workbook_row": "19",
                "line_item": "Gaco Roof 2.7",
                "inputs": {"basis_sqft": 96, "thickness_inches": 4, "unit_price": 2.1},
                "calculated_outputs": {"estimated_cost": 806.4},
            },
        ],
        "summary": {"decision_count": 3, "unmapped_count": 0},
    }
    repair_answer_key = {
        "schema_version": "reference_estimate_answer_key.v1",
        "template_type": "roofing",
        "source_workbook": {"file_name": "Estimate - Leak Repair.xlsx"},
        "decisions": [
            {
                "section": "roofing_detail_template_decisions",
                "decision_id": "roofing_caulk_sealant_row_43",
                "template_bucket": "caulk_detail",
                "workbook_row": "43",
                "line_item": "Sealant",
                "inputs": {"estimated_units": 2, "unit_price": 12},
                "calculated_outputs": {"estimated_cost": 24},
            }
        ],
        "summary": {"decision_count": 1, "unmapped_count": 0},
    }
    data = EstimatorData(
        template_examples=pd.DataFrame(
            [
                {
                    "example_id": "coating-foam",
                    "job_id": "R-COAT",
                    "customer": "Gatti",
                    "job_name": "Metal Roof Coating",
                    "template_type": "roofing",
                    "project_class": "roof_restoration",
                    "building_type": "commercial",
                    "substrate": "metal",
                    "material_system": "Gaco silicone and roof foam",
                    "material_packages_json": json.dumps(["coating", "foam", "primer", "fasteners"]),
                    "area_sqft": 9600,
                    "scope_summary": "Metal roof restoration with coating and foam repair.",
                    "decision_summary": "coating; foam; primer; fasteners",
                    "answer_key_json": json.dumps(coating_answer_key),
                },
                {
                    "example_id": "repair",
                    "job_id": "R-REPAIR",
                    "customer": "Repair Customer",
                    "job_name": "Small leak repair",
                    "template_type": "roofing",
                    "project_class": "roof_repair",
                    "building_type": "commercial",
                    "substrate": "tpo",
                    "material_system": "sealant",
                    "material_packages_json": json.dumps(["caulk_detail"]),
                    "area_sqft": 200,
                    "scope_summary": "Small leak repair only.",
                    "decision_summary": "caulk detail",
                    "answer_key_json": json.dumps(repair_answer_key),
                },
            ]
        )
    )

    context = estimator_context_summary(
        data,
        scope={
            "template_type": "roofing",
            "estimated_sqft": 9600,
            "substrate": "metal",
            "raw_input_notes": "metal roof coating with foam repair, primer and fasteners",
        },
    )

    matches = context["historical_answer_key_examples"]["matched_answer_keys"]
    assert matches[0]["job_id"] == "R-COAT"
    assert "packages: coating" in "; ".join(matches[0]["match_reasons"])
    decision_ids = [row["decision_id"] for row in matches[0]["reference_answer_key"]["decisions"]]
    assert set(decision_ids[:2]) == {"roofing_coating_system_row_26", "roofing_foam_row_19"}
    assert decision_ids.index("roofing_edge_metal_row_82") > 1


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


def test_estimator_chat_applies_basis_sqft_multiplier_to_existing_scope() -> None:
    def provider(messages, model):
        assert '"estimated_sqft": 9600.0' in messages[1]["content"]
        return {
            "assistant_message": "Updated basis square footage for ribs.",
            "estimator_notes": "Metal roof basis adjusted by 1.2 for ribs.",
            "scope_overrides": {"template_type": "roofing"},
            "workbook_decision_preferences": [
                {
                    "decision_id": "roofing_coating_row_26",
                    "template_bucket": "coating",
                    "include": True,
                    "proposed_values": {"basis_sqft": 9600},
                    "confidence": 0.82,
                }
            ],
            "confidence": 0.82,
        }

    result = run_estimator_chat_turn(
        [{"role": "user", "content": "Bill Gatti ~8000 sf metal roof. Multiply basis sqft by 1.2 to account for ribs."}],
        template_type_hint="roofing",
        existing_scope={"template_type": "roofing", "estimated_sqft": 8000, "net_sqft": 8000},
        provider=provider,
        model="test-model",
    )

    assert result.scope_overrides["estimated_sqft"] == 9600
    assert result.scope_overrides["net_sqft"] == 9600
    assert result.scope_overrides["basis_area_multiplier"] == 1.2


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


def test_estimator_chat_does_not_apply_plain_pasted_roofing_reference_template_summary(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = run_estimator_chat_turn(
        [{"role": "user", "content": ROOFING_REFERENCE_TEMPLATE_SUMMARY}],
        template_type_hint="roofing",
    )

    assert result.workbook_decision_preferences == []
    assert result.scope_overrides["reference_answer_key_mode"] == "evaluate"
    assert any("detected but not applied" in warning for warning in result.warnings)


def test_estimator_chat_blocks_ai_preferences_from_evaluation_only_answer_key() -> None:
    def provider(_messages, _model):
        return {
            "assistant_message": "I found workbook decisions in the pasted answer key.",
            "estimator_notes": "Reference template only.",
            "scope_overrides": {"template_type": "roofing"},
            "workbook_decision_preferences": [
                {
                    "decision_id": "roofing_coating_system_row_26",
                    "template_bucket": "coating",
                    "include": True,
                    "proposed_values": {"basis_sqft": 9600},
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

    assert result.workbook_decision_preferences == []
    assert result.scope_overrides["reference_answer_key_mode"] == "evaluate"
    assert any("detected but not applied" in warning for warning in result.warnings)


def test_estimator_chat_applies_pasted_roofing_reference_template_summary_when_requested(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = run_estimator_chat_turn(
        [{"role": "user", "content": "Apply this answer key to the workbook.\n\n" + ROOFING_REFERENCE_TEMPLATE_SUMMARY}],
        template_type_hint="roofing",
    )

    by_id = {row["decision_id"]: row for row in result.workbook_decision_preferences}

    assert result.scope_overrides["template_type"] == "roofing"
    assert result.scope_overrides["reference_template_summary_present"] is True
    assert result.scope_overrides["reference_template_summary_mapped_row_count"] >= 20
    assert result.scope_overrides["reference_answer_key_mode"] == "apply"

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

    sales_tax = by_id["roofing_free_adder_row_111_sales_tax"]
    assert sales_tax["section"] == "roofing_free_adder_template_decisions"
    assert sales_tax["template_bucket"] == "sales_tax"
    assert sales_tax["proposed_values"]["amount"] == 396.87
    assert sales_tax["proposed_values"]["markup_treatment"] == "post_markup"

    warranty = by_id["roofing_free_adder_row_173_warranty"]
    assert warranty["proposed_values"]["amount"] == 600.0
    assert warranty["proposed_values"]["template_line"] == "Warranty"
    assert warranty["workbook_row"] == "173"
    assert by_id["roofing_free_adder_row_178_3_in_iso_board_2340_tax_st_mark_up"]["proposed_values"]["estimated_cost"] == 2700.0

    assert by_id["roofing_labor_base_row_122"]["proposed_values"]["days"] == 1.25
    assert by_id["roofing_labor_base_row_122"]["proposed_values"]["crew_size"] == 5.0
    assert by_id["roofing_labor_base_row_122"]["proposed_values"]["daily_rate"] == 1605.5
    assert by_id["roofing_labor_top_coat_row_124"]["workbook_row"] == "124"
    assert not any("Warranty" in warning for warning in result.warnings)


def test_estimator_chat_parses_compact_roofing_reference_answer_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = run_estimator_chat_turn(
        [{"role": "user", "content": "Apply this answer key to the workbook.\n\n" + ROOFING_COMPACT_REFERENCE_TEMPLATE_SUMMARY}],
        template_type_hint="roofing",
    )

    by_id = {row["decision_id"]: row for row in result.workbook_decision_preferences}

    assert result.scope_overrides["reference_template_summary_present"] is True
    assert result.scope_overrides["reference_template_summary_row_count"] >= 20
    assert result.scope_overrides["reference_template_summary_mapped_row_count"] >= 20
    assert not [
        warning
        for warning in result.warnings
        if "OPENAI_API_KEY is not configured" not in warning
    ]

    coating = by_id["roofing_coating_system_row_26"]
    assert coating["proposed_values"]["basis_sqft"] == 9600.0
    assert coating["proposed_values"]["estimated_units"] == 165.6
    assert coating["proposed_values"]["gal_per_100_sqft"] == 1.5
    assert coating["proposed_values"]["unit_price"] == 32.0

    primer = by_id["roofing_primer_system_row_39"]
    assert primer["section"] == "roofing_primer_template_decisions"
    assert primer["proposed_values"]["basis_sqft"] == 9600.0
    assert primer["proposed_values"]["estimated_units"] == 38.4
    assert primer["proposed_values"]["unit_price"] == 33.0

    assert by_id["roofing_caulk_sealant_row_43"]["proposed_values"]["estimated_units"] == 96.0
    assert by_id["roofing_caulk_sealant_row_45"]["proposed_values"]["unit_price"] == 35.0
    assert by_id["roofing_fasteners_row_63"]["proposed_values"]["estimated_units"] == 2063.0
    assert by_id["roofing_generator_row_99"]["proposed_values"] == {"days": 7.0, "unit_price": 50.0}
    assert by_id["roofing_sales_trips_row_106"]["proposed_values"] == {
        "trip_count": 10.0,
        "round_trip_miles": 65.0,
        "unit_price": 0.75,
    }
    assert by_id["roofing_truck_expense_row_108"]["proposed_values"] == {
        "trip_count": 14.0,
        "round_trip_miles": 65.0,
        "unit_price": 1.25,
    }

    assert by_id["roofing_labor_prep_row_116"]["proposed_values"]["days"] == 1.5
    assert by_id["roofing_labor_prep_row_116"]["proposed_values"]["crew_size"] == 5.0
    assert by_id["roofing_labor_prep_row_116"]["proposed_values"]["total_hours"] == 78.8
    assert by_id["roofing_labor_prep_row_116"]["proposed_values"]["daily_rate"] == 1835.66
    assert [item["source_row"] for item in by_id["roofing_labor_prep_row_116"]["evidence"]] == ["116", "118"]
    assert "Source row 118 was normalized to current workbook row 116." in by_id["roofing_labor_prep_row_116"]["review_reasons"]
    assert by_id["roofing_labor_prime_row_118"]["proposed_values"]["days"] == 1.25
    assert by_id["roofing_labor_prime_row_118"]["evidence"][0]["source_row"] == "120"
    assert "Source row 120 was normalized to current workbook row 118." in by_id["roofing_labor_prime_row_118"]["review_reasons"]
    assert by_id["roofing_labor_loading_row_136"]["proposed_values"] == {
        "hours_per_day": 1.0,
        "people_count": 1.0,
        "unit_price": 25.5,
    }
    assert by_id["roofing_labor_traveling_row_138"]["proposed_values"] == {
        "hours_per_day": 2.0,
        "people_count": 5.0,
        "unit_price": 14.25,
    }

    overhead = by_id["pricing_overhead"]
    profit = by_id["pricing_profit"]
    assert overhead["section"] == "pricing_markup_decisions"
    assert overhead["proposed_values"]["markup_pct"] == 35.0
    assert overhead["proposed_values"]["overhead_pct"] == 35.0
    assert profit["proposed_values"]["markup_pct"] == 16.0
    assert profit["proposed_values"]["profit_pct"] == 16.0

    sales_tax = by_id["roofing_free_adder_row_111_sales_tax"]
    assert sales_tax["proposed_values"]["amount"] == 675.54
    assert sales_tax["proposed_values"]["template_line"] == "Sales Tax"
    assert by_id["roofing_free_adder_row_154_warranty"]["proposed_values"]["amount"] == 840.0
    assert by_id["roofing_free_adder_row_173_misc_materials"]["proposed_values"]["amount"] == 750.0
    assert by_id["roofing_free_adder_row_174_misc_insurance"]["proposed_values"]["amount"] == 1250.0


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
        [{"role": "user", "content": "Apply this answer key to the workbook.\n\n" + ROOFING_REFERENCE_TEMPLATE_SUMMARY}],
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
