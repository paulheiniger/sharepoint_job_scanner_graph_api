from __future__ import annotations

import json

import pandas as pd

from jobscan.estimator.chat_assistant import run_estimator_chat_turn
from jobscan.estimator.decision_proposals import build_decision_proposals
from jobscan.estimator.reference_answer_key import (
    SCHEMA_VERSION,
    answer_key_to_workbook_decision_preferences,
    build_reference_estimate_answer_key,
    parse_reference_answer_key_text,
)
from jobscan.estimator.schemas import EstimatorData


def test_build_reference_estimate_answer_key_from_template_rows() -> None:
    data = EstimatorData(
        template_rows=pd.DataFrame(
            [
                {
                    "template_row_id": "r19",
                    "document_id": "D1",
                    "job_id": "J1",
                    "source_file": "Estimate Roofing - Example.xlsx",
                    "template_type": "roofing",
                    "row_number": 19,
                    "template_bucket": "foam",
                    "template_section": "materials",
                    "line_item_kind": "material",
                    "selected_item_name": "Gaco Roof 2.7",
                    "resolved_item_name": "Gaco Roof 2.7",
                    "quantity": 960,
                    "unit_price": 2.1,
                    "estimated_units": 1200,
                    "estimated_sets": 1.2,
                    "estimated_cost": 2520,
                    "thickness_inches": 1.5,
                    "yield_or_coverage": 1200,
                    "parsed_confidence": 0.95,
                    "needs_review": False,
                },
                {
                    "template_row_id": "r7",
                    "document_id": "D1",
                    "job_id": "J1",
                    "source_file": "Estimate Roofing - Example.xlsx",
                    "template_type": "roofing",
                    "row_number": 7,
                    "template_bucket": "unknown",
                    "template_section": "header",
                    "line_item_kind": "metadata",
                    "row_label": "Title:",
                    "selected_item_name": "",
                    "estimated_cost": None,
                },
                {
                    "template_row_id": "r47",
                    "document_id": "D1",
                    "job_id": "J1",
                    "source_file": "Estimate Roofing - Example.xlsx",
                    "template_type": "roofing",
                    "row_number": 47,
                    "template_bucket": "seams_misc",
                    "template_section": "materials",
                    "line_item_kind": "material",
                    "row_label": "Misc./Seams",
                    "quantity": 250,
                    "unit_price": 1.5,
                    "estimated_units": 250,
                    "estimated_cost": 375,
                    "parsed_confidence": 0.95,
                    "needs_review": False,
                },
                {
                    "template_row_id": "r26",
                    "document_id": "D1",
                    "job_id": "J1",
                    "source_file": "Estimate Roofing - Example.xlsx",
                    "template_type": "roofing",
                    "row_number": 26,
                    "template_bucket": "coating",
                    "template_section": "materials",
                    "line_item_kind": "material",
                    "selected_item_name": "Gaco Silicone",
                    "resolved_item_name": "Gaco Silicone",
                    "quantity": 9600,
                    "unit_price": 32,
                    "estimated_units": 165.6,
                    "estimated_cost": 5299.2,
                    "cell_values": {"D26": 1.5},
                    "parsed_confidence": 0.95,
                    "needs_review": False,
                },
                {
                    "template_row_id": "r63",
                    "document_id": "D1",
                    "job_id": "J1",
                    "source_file": "Estimate Roofing - Example.xlsx",
                    "template_type": "roofing",
                    "row_number": 63,
                    "template_bucket": "fasteners",
                    "template_section": "materials",
                    "line_item_kind": "material",
                    "selected_item_name": "Fasteners",
                    "quantity": 9600,
                    "unit_price": 250,
                    "estimated_units": 3600,
                    "estimated_cost": 900,
                    "parsed_confidence": 0.95,
                    "needs_review": False,
                },
            ]
        ),
        job_context_profiles=pd.DataFrame(
            [
                {
                    "job_id": "J1",
                    "customer": "Example Customer",
                    "job_name": "Example Roof",
                    "template_type": "roofing",
                    "project_class": "roof restoration",
                    "substrate": "metal",
                    "material_system": "Gaco silicone",
                    "area_sqft": 9600,
                    "scope_summary": "Metal roof restoration with foam repair and coating.",
                }
            ]
        ),
    )

    answer_key = build_reference_estimate_answer_key(data, job_id="J1")

    assert answer_key["schema_version"] == SCHEMA_VERSION
    assert answer_key["source_workbook"]["file_name"] == "Estimate Roofing - Example.xlsx"
    assert answer_key["job_context"]["substrate"] == "metal"
    assert answer_key["summary"]["decision_count"] == 4
    assert answer_key["summary"]["unmapped_count"] == 0

    by_id = {row["decision_id"]: row for row in answer_key["decisions"]}
    assert by_id["roofing_foam_row_19"]["inputs"]["thickness_inches"] == 1.5
    assert by_id["roofing_seams_misc_row_47"]["inputs"]["estimated_units"] == 250
    assert by_id["roofing_coating_system_row_26"]["inputs"]["basis_sqft"] == 9600
    assert by_id["roofing_coating_system_row_26"]["inputs"]["gal_per_100_sqft"] == 1.5
    assert by_id["roofing_fasteners_row_63"]["inputs"]["board_area_sqft"] == 9600
    assert by_id["roofing_fasteners_row_63"]["inputs"]["unit_price_per_thousand"] == 250


def test_reference_answer_key_drops_implausible_template_row_values() -> None:
    data = EstimatorData(
        template_rows=pd.DataFrame(
            [
                {
                    "template_row_id": "r20",
                    "document_id": "D1",
                    "job_id": "J1",
                    "source_file": "Estimate Insulation - Polluted.xlsx",
                    "template_type": "insulation",
                    "row_number": 20,
                    "template_bucket": "foam",
                    "template_section": "materials",
                    "line_item_kind": "material",
                    "selected_item_name": "Gaco 0.5 lb.",
                    "resolved_item_name": "Gaco 0.5 lb.",
                    "quantity": 1200,
                    "unit_price": 1.6,
                    "estimated_units": 3.2,
                    "estimated_cost": 5120,
                    "thickness_inches": 2733,
                    "yield_or_coverage": 4,
                    "parsed_confidence": 0.95,
                    "needs_review": False,
                },
                {
                    "template_row_id": "r86",
                    "document_id": "D1",
                    "job_id": "J1",
                    "source_file": "Estimate Insulation - Polluted.xlsx",
                    "template_type": "insulation",
                    "row_number": 86,
                    "template_bucket": "labor_foam",
                    "template_section": "labor",
                    "line_item_kind": "labor",
                    "days": 1,
                    "crew_size": 105,
                    "daily_rate": 1200,
                    "estimated_cost": 1200,
                    "parsed_confidence": 0.95,
                    "needs_review": False,
                },
            ]
        )
    )

    answer_key = build_reference_estimate_answer_key(data, job_id="J1")
    by_id = {row["decision_id"]: row for row in answer_key["decisions"]}

    foam_inputs = by_id["insulation_foam_row_20"]["inputs"]
    assert "thickness_inches" not in foam_inputs
    assert "yield_or_coverage" not in foam_inputs
    labor_inputs = by_id["insulation_labor_foam_row_86"]["inputs"]
    assert "crew_size" not in labor_inputs
    assert "crew_selector_code" not in labor_inputs
    assert labor_inputs["daily_rate"] == 1200


def test_answer_key_json_maps_to_chat_decision_preferences() -> None:
    answer_key = {
        "schema_version": SCHEMA_VERSION,
        "template_type": "roofing",
        "source_workbook": {"document_id": "D1", "job_id": "J1", "file_name": "Estimate Roofing - Example.xlsx"},
        "job_context": {"project_type": "roof restoration", "substrate": "metal"},
        "decisions": [
            {
                "source_row": "26",
                "workbook_row": "26",
                "section": "roofing_coating_template_decisions",
                "decision_id": "roofing_coating_system_row_26",
                "template_bucket": "coating",
                "line_item": "Gaco Silicone",
                "include": True,
                "inputs": {"basis_sqft": 9600, "gal_per_100_sqft": 1.5, "unit_price": 32},
                "calculated_outputs": {"estimated_cost": 5299.2},
                "evidence": {"source_row": "26", "line_item": "Gaco Silicone"},
                "confidence": 0.95,
            }
        ],
        "summary": {"decision_count": 1, "source_row_count": 1},
    }

    parsed = parse_reference_answer_key_text(json.dumps(answer_key))
    preferences = answer_key_to_workbook_decision_preferences(parsed or {})

    assert parsed is not None
    assert preferences[0]["source"] == "reference_estimate_answer_key"
    assert preferences[0]["proposed_values"]["gal_per_100_sqft"] == 1.5

    result = run_estimator_chat_turn(
        [{"role": "user", "content": "Learn from this answer key.\n" + json.dumps(answer_key)}],
        template_type_hint="roofing",
    )
    by_id = {row["decision_id"]: row for row in result.workbook_decision_preferences}

    assert result.scope_overrides["reference_template_summary_present"] is True
    assert by_id["roofing_coating_system_row_26"]["source"] == "reference_estimate_answer_key"
    assert by_id["roofing_coating_system_row_26"]["proposed_values"]["unit_price"] == 32

    proposals = build_decision_proposals(
        {
            "template_type": "roofing",
            "estimator_chat": {
                "workbook_decision_preferences": result.workbook_decision_preferences,
            },
        }
    )
    coating_proposal = next(row for row in proposals if row["decision_id"] == "roofing_coating_system_row_26")
    assert coating_proposal["source"] == "reference_estimate_answer_key"
    assert coating_proposal["evidence"]["reference_estimate_answer_key"][0]["source_row"] == "26"


def test_answer_key_preferences_drop_implausible_existing_values() -> None:
    answer_key = {
        "schema_version": SCHEMA_VERSION,
        "template_type": "insulation",
        "decisions": [
            {
                "source_row": "19",
                "workbook_row": "19",
                "section": "insulation_foam_template_decisions",
                "decision_id": "insulation_foam_row_19",
                "template_bucket": "foam",
                "include": True,
                "inputs": {
                    "basis_sqft": 1200,
                    "thickness_inches": 2733,
                    "yield_or_coverage": 4,
                    "unit_price": 1.6,
                },
                "calculated_outputs": {"estimated_cost": 5000},
            },
            {
                "source_row": "86",
                "workbook_row": "86",
                "section": "insulation_labor_template_decisions",
                "decision_id": "insulation_labor_foam_row_86",
                "template_bucket": "labor_foam",
                "include": True,
                "inputs": {"days": 1, "crew_size": 105, "daily_rate": 1200},
                "calculated_outputs": {"estimated_cost": 1200},
            },
        ],
    }

    preferences = answer_key_to_workbook_decision_preferences(answer_key)
    by_id = {row["decision_id"]: row for row in preferences}

    foam_values = by_id["insulation_foam_row_19"]["proposed_values"]
    assert "thickness_inches" not in foam_values
    assert "yield_or_coverage" not in foam_values
    labor_values = by_id["insulation_labor_foam_row_86"]["proposed_values"]
    assert "crew_size" not in labor_values
    assert labor_values["daily_rate"] == 1200


def test_reference_answer_key_normalizes_insulation_variant_trip_rows() -> None:
    data = EstimatorData(
        template_rows=pd.DataFrame(
            [
                {
                    "template_row_id": "sales-old-row",
                    "document_id": "D1",
                    "job_id": "J1",
                    "source_file": "Estimate - Insulation - TEMPLATE - Massey Pole Barn.xlsx",
                    "template_type": "insulation",
                    "row_number": 88,
                    "template_bucket": "sales_inspection_trips",
                    "template_section": "travel",
                    "line_item_kind": "travel",
                    "row_label": "Sales/Inspect.",
                    "selected_item_name": "Sales/Inspect.",
                    "resolved_item_name": "Sales/Inspect.",
                    "trips": 2,
                    "round_trip_miles": 105,
                    "cost_per_mile": 0.75,
                    "estimated_cost": 157.5,
                    "parsed_confidence": 0.95,
                    "needs_review": False,
                },
                {
                    "template_row_id": "truck-old-row",
                    "document_id": "D1",
                    "job_id": "J1",
                    "source_file": "Estimate - Insulation - TEMPLATE - Massey Pole Barn.xlsx",
                    "template_type": "insulation",
                    "row_number": 90,
                    "template_bucket": "truck_expense",
                    "template_section": "travel",
                    "line_item_kind": "travel",
                    "row_label": "Truck Exp.",
                    "selected_item_name": "Truck Exp.",
                    "resolved_item_name": "Truck Exp.",
                    "trips": 2,
                    "round_trip_miles": 105,
                    "cost_per_mile": 1.25,
                    "estimated_cost": 262.5,
                    "parsed_confidence": 0.95,
                    "needs_review": False,
                },
            ]
        )
    )

    answer_key = build_reference_estimate_answer_key(data, job_id="J1")
    by_id = {row["decision_id"]: row for row in answer_key["decisions"]}

    sales = by_id["insulation_sales_inspection_trips"]
    assert sales["source_row"] == "88"
    assert sales["workbook_row"] == "68"
    assert sales["inputs"]["trip_count"] == 2
    assert sales["inputs"]["round_trip_miles"] == 105
    assert sales["inputs"]["unit_price"] == 0.75

    truck = by_id["insulation_truck_expense"]
    assert truck["source_row"] == "90"
    assert truck["workbook_row"] == "70"
    assert truck["inputs"]["trip_count"] == 2
    assert truck["inputs"]["round_trip_miles"] == 105
    assert truck["inputs"]["unit_price"] == 1.25


def test_reference_answer_key_maps_secondary_roofing_rows_from_history() -> None:
    data = EstimatorData(
        template_rows=pd.DataFrame(
            [
                {
                    "template_row_id": "thin",
                    "document_id": "D2",
                    "job_id": "J2",
                    "source_file": "Estimate Roofing - Secondary.xlsx",
                    "template_type": "roofing",
                    "row_number": 33,
                    "template_bucket": "thinner",
                    "line_item_kind": "material",
                    "resolved_item_name": "Naphtha VM&P",
                    "estimated_units": 4,
                    "unit_price": 12.96,
                    "estimated_cost": 51.84,
                },
                {
                    "template_row_id": "hvac",
                    "document_id": "D2",
                    "job_id": "J2",
                    "source_file": "Estimate Roofing - Secondary.xlsx",
                    "template_type": "roofing",
                    "row_number": 51,
                    "template_bucket": "hvac_units",
                    "line_item_kind": "material",
                    "row_label": "HVAC Units",
                    "quantity": 3,
                },
                {
                    "template_row_id": "drains",
                    "document_id": "D2",
                    "job_id": "J2",
                    "source_file": "Estimate Roofing - Secondary.xlsx",
                    "template_type": "roofing",
                    "row_number": 53,
                    "template_bucket": "drains",
                    "line_item_kind": "material",
                    "row_label": "Drains",
                    "quantity": 2,
                },
                {
                    "template_row_id": "edge",
                    "document_id": "D2",
                    "job_id": "J2",
                    "source_file": "Estimate Roofing - Secondary.xlsx",
                    "template_type": "roofing",
                    "row_number": 82,
                    "template_bucket": "edge_metal",
                    "line_item_kind": "material",
                    "row_label": "Edge Metal",
                    "quantity": 40,
                },
                {
                    "template_row_id": "gutter",
                    "document_id": "D2",
                    "job_id": "J2",
                    "source_file": "Estimate Roofing - Secondary.xlsx",
                    "template_type": "roofing",
                    "row_number": 84,
                    "template_bucket": "gutter",
                    "line_item_kind": "material",
                    "row_label": "Gutter",
                    "quantity": 75,
                },
                {
                    "template_row_id": "downspouts",
                    "document_id": "D2",
                    "job_id": "J2",
                    "source_file": "Estimate Roofing - Secondary.xlsx",
                    "template_type": "roofing",
                    "row_number": 86,
                    "template_bucket": "downspouts",
                    "line_item_kind": "material",
                    "row_label": "Downspouts",
                    "quantity": 4,
                },
                {
                    "template_row_id": "hatch",
                    "document_id": "D2",
                    "job_id": "J2",
                    "source_file": "Estimate Roofing - Secondary.xlsx",
                    "template_type": "roofing",
                    "row_number": 88,
                    "template_bucket": "roof_hatch",
                    "line_item_kind": "material",
                    "row_label": "Roof Hatch",
                    "quantity": 1,
                },
                {
                    "template_row_id": "scuppers",
                    "document_id": "D2",
                    "job_id": "J2",
                    "source_file": "Estimate Roofing - Secondary.xlsx",
                    "template_type": "roofing",
                    "row_number": 90,
                    "template_bucket": "scuppers",
                    "line_item_kind": "material",
                    "row_label": "Scuppers",
                    "quantity": 2,
                },
                {
                    "template_row_id": "misc",
                    "document_id": "D2",
                    "job_id": "J2",
                    "source_file": "Estimate Roofing - Secondary.xlsx",
                    "template_type": "roofing",
                    "row_number": 101,
                    "template_bucket": "misc",
                    "line_item_kind": "material",
                    "row_label": "Misc.",
                    "estimated_cost": 150,
                },
                {
                    "template_row_id": "insurance",
                    "document_id": "D2",
                    "job_id": "J2",
                    "source_file": "Estimate Roofing - Secondary.xlsx",
                    "template_type": "roofing",
                    "row_number": 156,
                    "template_bucket": "misc_insurance",
                    "line_item_kind": "adder",
                    "row_label": "Miscellaneous Insurance",
                    "estimated_cost": 250,
                },
                {
                    "template_row_id": "permits",
                    "document_id": "D2",
                    "job_id": "J2",
                    "source_file": "Estimate Roofing - Secondary.xlsx",
                    "template_type": "roofing",
                    "row_number": 158,
                    "template_bucket": "permits",
                    "line_item_kind": "adder",
                    "row_label": "Describe:",
                    "estimated_cost": 125,
                },
            ]
        )
    )

    answer_key = build_reference_estimate_answer_key(data, job_id="J2")
    by_id = {row["decision_id"]: row for row in answer_key["decisions"]}

    assert answer_key["summary"]["unmapped_count"] == 0
    assert answer_key["summary"]["inactive_mapped_count"] == 5
    assert by_id["roofing_thinner_row_33"]["section"] == "roofing_accessory_template_decisions"
    assert by_id["roofing_hvac_units_row_51"]["inputs"]["estimated_units"] == 3
    assert by_id["roofing_drains_row_53"]["inputs"]["estimated_units"] == 2
    assert "roofing_edge_metal_row_82" not in by_id
    assert "roofing_gutter_row_84" not in by_id
    assert "roofing_downspouts_row_86" not in by_id
    assert "roofing_roof_hatch_row_88" not in by_id
    assert "roofing_scuppers_row_90" not in by_id
    assert by_id["roofing_misc_row_101"]["inputs"]["amount"] == 150
    assert by_id["roofing_free_adder_row_156"]["inputs"]["amount"] == 250
    assert by_id["roofing_free_adder_row_158"]["inputs"]["amount"] == 125


def test_reference_answer_key_skips_inactive_mapped_template_options() -> None:
    data = EstimatorData(
        template_rows=pd.DataFrame(
            [
                {
                    "template_row_id": "inactive-foam",
                    "document_id": "D5",
                    "job_id": "J5",
                    "source_file": "Estimate - 10YR Coating System.xlsx",
                    "template_type": "roofing",
                    "row_number": 19,
                    "template_bucket": "foam",
                    "line_item_kind": "material",
                    "resolved_item_name": "Gaco Roof 2.7",
                    "unit_price": 2.1,
                    "cell_values": {"D19": 1.5, "F19": 1200},
                },
                {
                    "template_row_id": "active-coating",
                    "document_id": "D5",
                    "job_id": "J5",
                    "source_file": "Estimate - 10YR Coating System.xlsx",
                    "template_type": "roofing",
                    "row_number": 26,
                    "template_bucket": "coating",
                    "line_item_kind": "material",
                    "resolved_item_name": "Gaco Silicone",
                    "quantity": 9600,
                    "unit_price": 32,
                    "estimated_units": 165.6,
                    "estimated_cost": 5299.2,
                    "cell_values": {"D26": 1.5},
                },
                {
                    "template_row_id": "inactive-coating-option",
                    "document_id": "D5",
                    "job_id": "J5",
                    "source_file": "Estimate - 10YR Coating System.xlsx",
                    "template_type": "roofing",
                    "row_number": 27,
                    "template_bucket": "coating",
                    "line_item_kind": "material",
                    "resolved_item_name": "Gaco Silicone",
                    "unit_price": 32,
                    "cell_values": {"D27": 1.5},
                },
                {
                    "template_row_id": "active-primer",
                    "document_id": "D5",
                    "job_id": "J5",
                    "source_file": "Estimate - 10YR Coating System.xlsx",
                    "template_type": "roofing",
                    "row_number": 39,
                    "template_bucket": "primer",
                    "line_item_kind": "material",
                    "resolved_item_name": "Gaco E-5320 Primer",
                    "quantity": 9600,
                    "unit_price": 33,
                    "estimated_units": 38.4,
                    "estimated_cost": 1267.2,
                },
                {
                    "template_row_id": "active-caulk",
                    "document_id": "D5",
                    "job_id": "J5",
                    "source_file": "Estimate - 10YR Coating System.xlsx",
                    "template_type": "roofing",
                    "row_number": 43,
                    "template_bucket": "caulk_sealant",
                    "line_item_kind": "material",
                    "resolved_item_name": "Silicone Sausage",
                    "unit_price": 12,
                    "estimated_units": 96,
                    "estimated_cost": 1152,
                },
                {
                    "template_row_id": "inactive-accessory",
                    "document_id": "D5",
                    "job_id": "J5",
                    "source_file": "Estimate - 10YR Coating System.xlsx",
                    "template_type": "roofing",
                    "row_number": 82,
                    "template_bucket": "edge_metal",
                    "line_item_kind": "material",
                    "row_label": "Edge Metal",
                },
                {
                    "template_row_id": "active-generator",
                    "document_id": "D5",
                    "job_id": "J5",
                    "source_file": "Estimate - 10YR Coating System.xlsx",
                    "template_type": "roofing",
                    "row_number": 99,
                    "template_bucket": "generator",
                    "line_item_kind": "equipment",
                    "row_label": "Generator",
                    "quantity": 7,
                    "unit_price": 50,
                    "estimated_cost": 350,
                },
                {
                    "template_row_id": "active-truck",
                    "document_id": "D5",
                    "job_id": "J5",
                    "source_file": "Estimate - 10YR Coating System.xlsx",
                    "template_type": "roofing",
                    "row_number": 108,
                    "template_bucket": "truck_expense",
                    "line_item_kind": "equipment",
                    "row_label": "Truck Exp.",
                    "trips": 14,
                    "round_trip_miles": 65,
                    "cost_per_mile": 1.25,
                    "estimated_cost": 1137.5,
                },
                {
                    "template_row_id": "active-labor",
                    "document_id": "D5",
                    "job_id": "J5",
                    "source_file": "Estimate - 10YR Coating System.xlsx",
                    "template_type": "roofing",
                    "row_number": 120,
                    "template_bucket": "labor_prime",
                    "line_item_kind": "labor",
                    "row_label": "Prime",
                    "days": 1.25,
                    "crew_size": 5,
                    "daily_rate": 1835.66,
                    "estimated_cost": 2294.58,
                },
                {
                    "template_row_id": "overhead",
                    "document_id": "D5",
                    "job_id": "J5",
                    "source_file": "Estimate - 10YR Coating System.xlsx",
                    "template_type": "roofing",
                    "row_number": 165,
                    "template_bucket": "overhead",
                    "line_item_kind": "pricing",
                    "row_label": "Estimated O/H",
                    "overhead_pct": 35,
                    "estimated_cost": 9555.23,
                },
            ]
        )
    )

    answer_key = build_reference_estimate_answer_key(data, job_id="J5")
    by_id = {row["decision_id"]: row for row in answer_key["decisions"]}

    assert answer_key["summary"]["unmapped_count"] == 0
    assert answer_key["summary"]["inactive_mapped_count"] == 3
    assert "roofing_foam_row_19" not in by_id
    assert "roofing_coating_system_row_27" not in by_id
    assert "roofing_edge_metal_row_82" not in by_id
    assert by_id["roofing_coating_system_row_26"]["inputs"]["basis_sqft"] == 9600
    assert by_id["roofing_primer_system_row_39"]["inputs"]["estimated_units"] == 38.4
    assert by_id["roofing_caulk_sealant_row_43"]["inputs"]["estimated_units"] == 96
    assert by_id["roofing_generator_row_99"]["inputs"]["estimated_units"] == 7
    preferences = answer_key_to_workbook_decision_preferences(answer_key)
    generator_pref = next(row for row in preferences if row["decision_id"] == "roofing_generator_row_99")
    assert generator_pref["proposed_values"]["days"] == 7
    assert by_id["roofing_truck_expense_row_108"]["inputs"]["trip_count"] == 14
    assert by_id["roofing_labor_prime_row_118"]["inputs"]["days"] == 1.25
    assert by_id["pricing_overhead"]["inputs"]["markup_pct"] == 35


def test_stored_answer_key_preferences_skip_legacy_inactive_decisions() -> None:
    answer_key = {
        "schema_version": SCHEMA_VERSION,
        "template_type": "roofing",
        "source_workbook": {"document_id": "D5", "job_id": "J5", "file_name": "Estimate - 10YR Coating System.xlsx"},
        "decisions": [
            {
                "source_row": "19",
                "workbook_row": "19",
                "section": "roofing_foam_template_decisions",
                "decision_id": "roofing_foam_row_19",
                "template_bucket": "roofing_foam",
                "line_item": "Gaco Roof 2.7",
                "include": True,
                "inputs": {"thickness_inches": 1.5, "yield_or_coverage": 1200, "unit_price": 2.1},
                "calculated_outputs": {},
                "evidence": {"source_row": "19", "line_item": "Gaco Roof 2.7"},
            },
            {
                "source_row": "26",
                "workbook_row": "26",
                "section": "roofing_coating_template_decisions",
                "decision_id": "roofing_coating_system_row_26",
                "template_bucket": "coating",
                "line_item": "Gaco Silicone",
                "include": True,
                "inputs": {"basis_sqft": 9600, "gal_per_100_sqft": 1.5, "unit_price": 32},
                "calculated_outputs": {"estimated_cost": 5299.2},
                "evidence": {"source_row": "26", "line_item": "Gaco Silicone"},
            },
            {
                "source_row": "108",
                "workbook_row": "108",
                "section": "roofing_travel_freight_template_decisions",
                "decision_id": "roofing_truck_expense_row_108",
                "template_bucket": "truck_expense",
                "line_item": "Truck Exp.",
                "include": True,
                "inputs": {"trip_count": 14, "round_trip_miles": 65, "unit_price": 1.25},
                "calculated_outputs": {"estimated_cost": 1137.5},
                "evidence": {"source_row": "108", "line_item": "Truck Exp."},
            },
        ],
    }

    preferences = answer_key_to_workbook_decision_preferences(answer_key)
    by_id = {row["decision_id"]: row for row in preferences}

    assert "roofing_foam_row_19" not in by_id
    assert by_id["roofing_coating_system_row_26"]["proposed_values"]["unit_price"] == 32
    assert by_id["roofing_truck_expense_row_108"]["proposed_values"]["round_trip_miles"] == 65


def test_answer_key_preferences_merge_duplicate_canonical_labor_targets() -> None:
    answer_key = {
        "schema_version": SCHEMA_VERSION,
        "template_type": "roofing",
        "source_workbook": {"document_id": "D6", "job_id": "J6", "file_name": "Estimate - Variant Labor.xlsx"},
        "decisions": [
            {
                "source_row": "116",
                "workbook_row": "116",
                "section": "roofing_labor_template_decisions",
                "decision_id": "roofing_labor_prep_row_116",
                "template_bucket": "labor_prep",
                "line_item": "Set-Up",
                "include": True,
                "inputs": {"days": 0.2, "editable_days": 0.2, "crew_size": 5, "daily_rate": 1835.66, "total_hours": 10.5},
                "calculated_outputs": {"estimated_cost": 367.13},
                "evidence": {"source_row": "116", "line_item": "Set-Up"},
            },
            {
                "source_row": "118",
                "workbook_row": "116",
                "section": "roofing_labor_template_decisions",
                "decision_id": "roofing_labor_prep_row_116",
                "template_bucket": "labor_prep",
                "line_item": "Pwash & Prep",
                "include": True,
                "inputs": {"days": 1.5, "editable_days": 1.5, "crew_size": 5, "daily_rate": 1835.66, "total_hours": 78.75},
                "calculated_outputs": {"estimated_cost": 2753.49},
                "evidence": {"source_row": "118", "line_item": "Pwash & Prep"},
            },
        ],
    }

    preferences = answer_key_to_workbook_decision_preferences(answer_key)

    assert len(preferences) == 1
    preference = preferences[0]
    assert preference["decision_id"] == "roofing_labor_prep_row_116"
    assert preference["proposed_values"]["days"] == 1.7
    assert preference["proposed_values"]["total_hours"] == 89.25
    assert len(preference["evidence"]) == 2


def test_answer_key_preferences_normalize_roofing_labor_by_workbook_row() -> None:
    answer_key = {
        "schema_version": SCHEMA_VERSION,
        "template_type": "roofing",
        "source_workbook": {"document_id": "D7", "job_id": "J7", "file_name": "Estimate FINAL- Recoat 15 YR.xlsx"},
        "decisions": [
            {
                "source_row": "116",
                "workbook_row": "136",
                "section": "roofing_logistics_expense_template_decisions",
                "decision_id": "roofing_labor_loading_row_136",
                "template_bucket": "labor_loading",
                "line_item": "Set Up/Safety",
                "include": True,
                "inputs": {"days": 0.15, "crew_size": 5, "daily_rate": 1667.25, "total_hours": 7.5},
                "calculated_outputs": {"estimated_cost": 250.09},
                "evidence": {"source_row": "116", "line_item": "Set Up/Safety"},
            },
            {
                "source_row": "118",
                "workbook_row": "118",
                "section": "roofing_labor_template_decisions",
                "decision_id": "roofing_labor_prep_row_118",
                "template_bucket": "labor_prep",
                "line_item": "PW/Prep",
                "include": True,
                "inputs": {"days": 1.0, "crew_size": 5, "daily_rate": 1667.25, "total_hours": 50.0},
                "calculated_outputs": {"estimated_cost": 1667.25},
                "evidence": {"source_row": "118", "line_item": "PW/Prep"},
            },
            {
                "source_row": "130",
                "workbook_row": "130",
                "section": "roofing_labor_template_decisions",
                "decision_id": "roofing_labor_top_coat_row_130",
                "template_bucket": "labor_top_coat",
                "line_item": "Top Coat/Gran",
                "include": True,
                "inputs": {"days": 1.4, "crew_size": 5, "daily_rate": 1667.25, "total_hours": 70.0},
                "calculated_outputs": {"estimated_cost": 2334.15},
                "evidence": {"source_row": "130", "line_item": "Top Coat/Gran"},
            },
            {
                "source_row": "134",
                "workbook_row": "134",
                "section": "roofing_labor_template_decisions",
                "decision_id": "roofing_labor_misc_row_134",
                "template_bucket": "labor_misc",
                "line_item": "Misc.",
                "include": True,
                "inputs": {"days": 0.65, "crew_size": 5, "daily_rate": 1667.25, "total_hours": 32.5},
                "calculated_outputs": {"estimated_cost": 1083.71},
                "evidence": {"source_row": "134", "line_item": "Misc."},
            },
        ],
    }

    preferences = answer_key_to_workbook_decision_preferences(answer_key)
    by_id = {row["decision_id"]: row for row in preferences}

    assert by_id["roofing_labor_prep_row_116"]["template_bucket"] == "labor_prep"
    assert by_id["roofing_labor_prep_row_116"]["proposed_values"]["days"] == 0.15
    assert by_id["roofing_labor_prime_row_118"]["template_bucket"] == "labor_prime"
    assert by_id["roofing_labor_prime_row_118"]["proposed_values"]["days"] == 1.0
    assert by_id["roofing_labor_top_coat_granules_row_130"]["template_bucket"] == "labor_top_coat_granules"
    assert by_id["roofing_labor_top_coat_granules_row_130"]["proposed_values"]["total_hours"] == 70.0
    assert by_id["roofing_labor_misc_row_134"]["proposed_values"]["days"] == 0.65


def test_reference_answer_key_maps_insulation_support_rows_from_history() -> None:
    data = EstimatorData(
        template_rows=pd.DataFrame(
            [
                {
                    "template_row_id": "membrane",
                    "document_id": "D3",
                    "job_id": "J3",
                    "source_file": "Estimate Insulation - Support.xlsx",
                    "template_type": "insulation",
                    "row_number": 24,
                    "template_bucket": "membrane",
                    "line_item_kind": "material",
                    "row_label": "Membrane",
                    "linear_ft": 120,
                    "unit_price": 1.25,
                },
                {
                    "template_row_id": "primer",
                    "document_id": "D3",
                    "job_id": "J3",
                    "source_file": "Estimate Insulation - Support.xlsx",
                    "template_type": "insulation",
                    "row_number": 26,
                    "template_bucket": "primer",
                    "line_item_kind": "material",
                    "row_label": "Primer",
                    "estimated_units": 5,
                    "unit_price": 30,
                },
                {
                    "template_row_id": "thinner",
                    "document_id": "D3",
                    "job_id": "J3",
                    "source_file": "Estimate Insulation - Support.xlsx",
                    "template_type": "insulation",
                    "row_number": 37,
                    "template_bucket": "thinner",
                    "line_item_kind": "material",
                    "resolved_item_name": "Naphtha VM&P",
                    "estimated_units": 2,
                    "unit_price": 15,
                },
                {
                    "template_row_id": "drum",
                    "document_id": "D3",
                    "job_id": "J3",
                    "source_file": "Estimate Insulation - Support.xlsx",
                    "template_type": "insulation",
                    "row_number": 65,
                    "template_bucket": "drum_disposal",
                    "line_item_kind": "equipment",
                    "row_label": "Drum Disp.",
                    "estimated_units": 3,
                    "unit_price": 50,
                },
                {
                    "template_row_id": "tax",
                    "document_id": "D3",
                    "job_id": "J3",
                    "source_file": "Estimate Insulation - Support.xlsx",
                    "template_type": "insulation",
                    "row_number": 73,
                    "template_bucket": "sales_tax",
                    "line_item_kind": "total",
                    "row_label": "Sales Tax",
                    "estimated_cost": 2583.92,
                },
                {
                    "template_row_id": "oh",
                    "document_id": "D3",
                    "job_id": "J3",
                    "source_file": "Estimate Insulation - Support.xlsx",
                    "template_type": "insulation",
                    "row_number": 118,
                    "template_bucket": "overhead",
                    "line_item_kind": "pricing",
                    "row_label": "Estimated O/H",
                    "overhead_pct": 35,
                    "estimated_cost": 16627.71,
                },
                {
                    "template_row_id": "profit",
                    "document_id": "D3",
                    "job_id": "J3",
                    "source_file": "Estimate Insulation - Support.xlsx",
                    "template_type": "insulation",
                    "row_number": 120,
                    "template_bucket": "profit",
                    "line_item_kind": "pricing",
                    "row_label": "Profit",
                    "profit_pct": 16,
                    "estimated_cost": 8313.86,
                },
            ]
        )
    )

    answer_key = build_reference_estimate_answer_key(data, job_id="J3")
    by_id = {row["decision_id"]: row for row in answer_key["decisions"]}

    assert answer_key["summary"]["unmapped_count"] == 0
    assert by_id["insulation_membrane_row_24"]["inputs"]["linear_ft"] == 120
    assert by_id["insulation_primer_row_26"]["section"] == "insulation_detail_material_template_decisions"
    assert by_id["insulation_thinner_row_37"]["section"] == "insulation_support_material_template_decisions"
    assert by_id["insulation_drum_disposal_row_65"]["inputs"]["estimated_units"] == 3
    assert by_id["insulation_sales_tax_row_73"]["inputs"]["amount"] == 2583.92
    assert by_id["pricing_overhead"]["inputs"]["markup_pct"] == 35
    assert by_id["pricing_profit"]["inputs"]["markup_pct"] == 16


def test_reference_answer_key_maps_flooring_template_rows_from_history() -> None:
    data = EstimatorData(
        template_rows=pd.DataFrame(
            [
                {
                    "template_row_id": "base",
                    "document_id": "D4",
                    "job_id": "J4",
                    "source_file": "Estimate Floor Repair.xlsx",
                    "template_type": "flooring",
                    "row_number": 26,
                    "template_bucket": "floor_base_coat",
                    "line_item_kind": "material",
                    "resolved_item_name": "Floor Base Coat",
                    "area_sqft": 2000,
                    "estimated_units": 12,
                    "unit_price": 80,
                },
                {
                    "template_row_id": "top",
                    "document_id": "D4",
                    "job_id": "J4",
                    "source_file": "Estimate Floor Repair.xlsx",
                    "template_type": "flooring",
                    "row_number": 130,
                    "template_bucket": "labor_floor_topcoat",
                    "line_item_kind": "labor",
                    "row_label": "Top Coat",
                    "days": 1.5,
                    "crew_size": 3,
                    "daily_rate": 1500,
                },
                {
                    "template_row_id": "seams",
                    "document_id": "D4",
                    "job_id": "J4",
                    "source_file": "Estimate Floor Repair.xlsx",
                    "template_type": "flooring",
                    "row_number": 47,
                    "template_bucket": "seams_misc",
                    "line_item_kind": "material",
                    "row_label": "Misc./Seams",
                    "quantity": 100,
                },
                {
                    "template_row_id": "warranty",
                    "document_id": "D4",
                    "job_id": "J4",
                    "source_file": "Estimate Floor Repair.xlsx",
                    "template_type": "flooring",
                    "row_number": 154,
                    "template_bucket": "warranty",
                    "line_item_kind": "adder",
                    "row_label": "Warranty",
                    "estimated_cost": 600,
                },
                {
                    "template_row_id": "insurance",
                    "document_id": "D4",
                    "job_id": "J4",
                    "source_file": "Estimate Floor Repair.xlsx",
                    "template_type": "flooring",
                    "row_number": 156,
                    "template_bucket": "misc_insurance",
                    "line_item_kind": "adder",
                    "row_label": "Miscellaneous Insurance",
                    "estimated_cost": 250,
                },
                {
                    "template_row_id": "permits",
                    "document_id": "D4",
                    "job_id": "J4",
                    "source_file": "Estimate Floor Repair.xlsx",
                    "template_type": "flooring",
                    "row_number": 158,
                    "template_bucket": "permits",
                    "line_item_kind": "adder",
                    "row_label": "Describe:",
                    "estimated_cost": 125,
                },
                {
                    "template_row_id": "floor-oh",
                    "document_id": "D4",
                    "job_id": "J4",
                    "source_file": "Estimate Floor Repair.xlsx",
                    "template_type": "flooring",
                    "row_number": 165,
                    "template_bucket": "overhead",
                    "line_item_kind": "pricing",
                    "row_label": "Estimated O/H",
                    "overhead_pct": 35,
                    "estimated_cost": 675.84,
                },
                {
                    "template_row_id": "patch-materials",
                    "document_id": "D4",
                    "job_id": "J4",
                    "source_file": "Estimate Floor Repair.xlsx",
                    "template_type": "flooring",
                    "row_number": 174,
                    "template_bucket": "misc_materials",
                    "line_item_kind": "adder",
                    "row_label": "Patch Materials - See Side Breakdown",
                    "estimated_cost": 925,
                },
            ]
        )
    )

    answer_key = build_reference_estimate_answer_key(data, job_id="J4")
    by_id = {row["decision_id"]: row for row in answer_key["decisions"]}

    assert answer_key["summary"]["decision_count"] == 8
    assert answer_key["summary"]["unmapped_count"] == 0
    assert by_id["flooring_floor_base_coat_row_26"]["section"] == "flooring_material_template_decisions"
    assert by_id["flooring_labor_floor_topcoat_row_130"]["inputs"]["days"] == 1.5
    assert by_id["flooring_seams_misc_row_47"]["inputs"]["estimated_units"] == 100
    assert by_id["flooring_warranty_row_154"]["inputs"]["amount"] == 600
    assert by_id["flooring_misc_insurance_row_156"]["inputs"]["amount"] == 250
    assert by_id["flooring_permits_row_158"]["inputs"]["amount"] == 125
    assert by_id["pricing_overhead"]["inputs"]["markup_pct"] == 35
    assert by_id["flooring_misc_materials_row_174"]["inputs"]["amount"] == 925
