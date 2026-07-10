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
    assert by_id["roofing_thinner_row_33"]["section"] == "roofing_accessory_template_decisions"
    assert by_id["roofing_hvac_units_row_51"]["inputs"]["estimated_units"] == 3
    assert by_id["roofing_drains_row_53"]["inputs"]["estimated_units"] == 2
    assert by_id["roofing_edge_metal_row_82"]["inputs"]["estimated_units"] == 40
    assert by_id["roofing_gutter_row_84"]["section"] == "roofing_accessory_template_decisions"
    assert by_id["roofing_downspouts_row_86"]["inputs"]["estimated_units"] == 4
    assert by_id["roofing_roof_hatch_row_88"]["inputs"]["estimated_units"] == 1
    assert by_id["roofing_scuppers_row_90"]["inputs"]["estimated_units"] == 2
    assert by_id["roofing_misc_row_101"]["inputs"]["amount"] == 150
    assert by_id["roofing_free_adder_row_156"]["inputs"]["amount"] == 250
    assert by_id["roofing_free_adder_row_158"]["inputs"]["amount"] == 125


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
