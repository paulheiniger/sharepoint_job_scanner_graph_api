from __future__ import annotations

import json
from copy import deepcopy

import pandas as pd

from jobscan.estimator.schemas import EstimateRecommendation, EstimatorData
from jobscan.estimator.workbench import (
    build_edit_history_rows,
    build_estimating_workbench,
    recalculate_workbench_tables,
    summarize_workbench_totals,
    workbench_to_draft_workbook_inputs,
)


def roofing_recommendation() -> EstimateRecommendation:
    return EstimateRecommendation(
        parsed_fields={
            "division": "Roofing",
            "template_type": "roofing",
            "project_type": "roof coating",
            "estimated_sqft": 10000,
            "net_sqft": 10000,
            "coating_type": "silicone",
            "warranty_target_years": 10,
        },
        recommended_scope=[],
        material_plan=[],
        labor_plan=[],
        travel_plan={},
        historical_calibration={},
        similar_examples=[],
        estimate_low=None,
        estimate_target=None,
        estimate_high=None,
        review_flags=[],
        human_review_required=False,
        draft_workbook_inputs={},
    )


def insulation_recommendation() -> EstimateRecommendation:
    return EstimateRecommendation(
        parsed_fields={
            "division": "Insulation",
            "template_type": "insulation",
            "project_type": "spray foam insulation",
            "building_type": "metal building",
            "estimated_sqft": 2400,
            "net_sqft": 2400,
            "net_insulation_area_sqft": 2400,
            "foam_type": "closed_cell",
            "notes": "Spray foam insulation in a metal building.",
        },
        recommended_scope=[],
        material_plan=[],
        labor_plan=[],
        travel_plan={},
        historical_calibration={},
        similar_examples=[],
        estimate_low=None,
        estimate_target=None,
        estimate_high=None,
        review_flags=[],
        human_review_required=False,
        draft_workbook_inputs={},
    )


def insulation_labor_driver_data() -> EstimatorData:
    return EstimatorData(
        template_rows=pd.DataFrame(
            [
                {
                    "job_id": "J1",
                    "document_id": "D1",
                    "template_type": "insulation",
                    "template_bucket": "foam",
                    "line_item_kind": "material",
                    "selected_item_name": "Gaco 2.0 lb.",
                    "area_sqft": 2400,
                    "thickness_inches": 2,
                    "yield_or_coverage": 12000,
                    "estimated_units": 2000,
                    "estimated_sets": 2,
                    "unit_price": 2.4,
                    "estimated_cost": 4800,
                },
                {
                    "job_id": "J1",
                    "document_id": "D1",
                    "template_type": "insulation",
                    "template_bucket": "labor_foam",
                    "line_item_kind": "labor",
                    "days": 1,
                    "crew_size": 3,
                    "total_hours": 12,
                    "hourly_rate": 72,
                    "estimated_cost": 864,
                },
            ]
        )
    )


def roofing_companion_data() -> EstimatorData:
    return EstimatorData(
        relationship_package_cooccurrence=pd.DataFrame(
            [
                {
                    "project_type": "roof coating",
                    "substrate": "metal",
                    "package_a": "coating",
                    "package_b": "primer",
                    "co_occurrence_rate": 0.82,
                    "job_count": 9,
                    "supporting_job_ids": '["J1","J2"]',
                },
                {
                    "project_type": "roof coating",
                    "substrate": "metal",
                    "package_a": "coating",
                    "package_b": "caulk_detail",
                    "co_occurrence_rate": 0.74,
                    "job_count": 7,
                },
                {
                    "project_type": "roof coating",
                    "substrate": "metal",
                    "package_a": "fabric",
                    "package_b": "seam_treatment",
                    "co_occurrence_rate": 0.8,
                    "job_count": 6,
                },
            ]
        )
    )


def roofing_reference_project_data() -> EstimatorData:
    return EstimatorData(
        template_rows=pd.DataFrame(
            [
                {
                    "job_id": "REF1",
                    "source_file": "Reference Estimate.xlsx",
                    "template_type": "roofing",
                    "project_type": "roof coating",
                    "substrate": "metal",
                    "coating_type": "silicone",
                    "template_bucket": "coating",
                    "line_item_kind": "material",
                    "row_number": 26,
                    "selector_code": "11",
                    "resolved_item_name": "Gaco Silicone",
                    "area_sqft": 10000,
                    "gal_per_100_sqft": 1.4,
                    "waste_factor_pct": 10,
                    "unit_price": 42,
                },
                {
                    "job_id": "REF1",
                    "source_file": "Reference Estimate.xlsx",
                    "template_type": "roofing",
                    "project_type": "roof coating",
                    "substrate": "metal",
                    "coating_type": "silicone",
                    "template_bucket": "primer",
                    "line_item_kind": "material",
                    "row_number": 39,
                    "selector_code": "1",
                    "resolved_item_name": "Gaco E-5320",
                    "area_sqft": 10000,
                    "estimated_units": 10,
                    "unit_price": 120,
                },
                {
                    "job_id": "REF1",
                    "source_file": "Reference Estimate.xlsx",
                    "template_type": "roofing",
                    "project_type": "roof coating",
                    "substrate": "metal",
                    "coating_type": "silicone",
                    "template_bucket": "caulk_detail",
                    "line_item_kind": "material",
                    "row_number": 43,
                    "resolved_item_name": "Silicone Sausage",
                    "area_sqft": 10000,
                    "estimated_units": 5,
                    "unit_price": 30,
                },
                {
                    "job_id": "REF1",
                    "source_file": "Reference Estimate.xlsx",
                    "template_type": "roofing",
                    "project_type": "roof coating",
                    "substrate": "metal",
                    "coating_type": "silicone",
                    "template_bucket": "labor_seam_sealer",
                    "line_item_kind": "labor",
                    "row_number": 120,
                    "area_sqft": 10000,
                    "total_hours": 50,
                    "crew_size": 4,
                    "hourly_rate": 72,
                    "formula_mode": "mixed_formula",
                },
            ]
        )
    )


def test_roofing_workbench_uses_decision_sections_only() -> None:
    workbench = build_estimating_workbench(roofing_recommendation(), EstimatorData())

    assert "materials" not in workbench
    assert "labor" not in workbench
    assert "adders" not in workbench
    assert [row["workbook_row"] for row in workbench["roofing_coating_template_decisions"]] == ["26", "27", "28"]
    assert [row["include"] for row in workbench["roofing_coating_template_decisions"]] == [True, True, False]
    assert workbench["roofing_coating_template_decisions"][0]["decision_id"] == "roofing_coating_system_row_26"
    assert workbench["roofing_coating_template_decisions"][0]["selector_options"]

    draft = workbench_to_draft_workbook_inputs(workbench)
    assert set(draft) == {"template_type", "header", "workbook_decisions"}
    assert draft["template_type"] == "roofing"
    assert "material_rows" not in draft
    assert "labor_rows" not in draft
    coating_decisions = [row for row in draft["workbook_decisions"] if row["template_bucket"] == "coating"]
    assert [row["workbook_row"] for row in coating_decisions] == ["26", "27"]
    assert all(row["row_type"] == "material" for row in coating_decisions)


def test_workbench_enriches_row_options_from_template_catalogs() -> None:
    data = EstimatorData(
        template_selector_maps=pd.DataFrame(
            [
                {
                    "template_type": "roofing",
                    "template_bucket": "coating",
                    "row_number": 26,
                    "selector_cell": "A26",
                    "selector_code": "99",
                    "resolved_item_name": "Catalog Silicone Alt",
                }
            ]
        ),
        template_product_options=pd.DataFrame(
            [
                {
                    "template_type": "roofing",
                    "template_bucket": "coating",
                    "row_number": 26,
                    "selector_code": "99",
                    "product_name": "Catalog Silicone Pail",
                    "source_values_json": {"unit": "pail", "unit_price": 77},
                }
            ]
        ),
        template_labor_options=pd.DataFrame(
            [
                {
                    "template_type": "roofing",
                    "row_number": 122,
                    "labor_package": "labor_base",
                    "lookup_key": "5",
                    "source_values_json": {"description": "5 person crew", "daily_rate": 3600, "crew_size": 5},
                }
            ]
        ),
    )

    workbench = build_estimating_workbench(roofing_recommendation(), data)
    workbench = recalculate_workbench_tables(workbench)
    coating = next(row for row in workbench["roofing_coating_template_decisions"] if row["workbook_row"] == "26")
    base_labor = next(row for row in workbench["roofing_labor_template_decisions"] if row["template_bucket"] == "labor_base")

    selector_options = json.loads(coating["selector_options_json"])
    item_options = json.loads(coating["item_options_json"])
    crew_options = json.loads(base_labor["crew_selector_options_json"])

    assert any(option["selector_code"] == "99" and option["resolved_template_option"] == "Catalog Silicone Alt" for option in selector_options)
    assert any(option["item_name"] == "Catalog Silicone Pail" and option["unit_price"] == 77 for option in item_options)
    assert any(option.get("selector_code") == "5" and option.get("daily_rate") == 3600 for option in crew_options)


def test_roofing_companion_relationships_suggest_primer_and_detail_rows() -> None:
    workbench = build_estimating_workbench(roofing_recommendation(), roofing_companion_data())

    primer = workbench["roofing_primer_template_decisions"][0]
    sealant = next(row for row in workbench["roofing_detail_template_decisions"] if row["template_bucket"] == "caulk_detail")

    assert primer["include"] is True
    assert primer["proposal_source"] == "historical_companion"
    assert primer["proposal_evidence"]["relationship_package_cooccurrence"]
    assert primer["proposal_review_required"] is True
    assert any("Historical companion suggestion" in warning for warning in primer["compatibility_warnings"])
    assert sealant["include"] is True
    assert sealant["proposal_source"] == "historical_companion"
    assert sealant["why_included"].startswith("Included by historical companion")


def test_fabric_companion_suggests_seam_detail_labor_review_marked() -> None:
    workbench = build_estimating_workbench(roofing_recommendation(), roofing_companion_data())
    fabric = next(row for row in workbench["roofing_detail_template_decisions"] if row["template_bucket"] == "fabric")
    fabric["include"] = True
    fabric["manual_override"] = True
    fabric["include_source"] = "estimator_edit"

    recalculated = recalculate_workbench_tables(workbench)
    seam_labor = next(row for row in recalculated["roofing_labor_template_decisions"] if row["template_bucket"] == "labor_seam_sealer")

    assert seam_labor["include"] is True
    assert seam_labor["proposal_source"] == "historical_companion"
    assert seam_labor["proposal_review_required"] is True
    assert "fabric" in seam_labor["proposal_review_reasons"][0]


def test_full_tearoff_notes_include_board_fasteners_and_disposal_rows() -> None:
    workbench = build_estimating_workbench(
        roofing_recommendation(),
        EstimatorData(),
        scope_override={
            "project_type": "roof replacement",
            "coating_type": "",
            "raw_input_notes": "Full tear off with wet insulation and damaged board; include disposal review.",
            "net_sqft": 12000,
            "estimated_sqft": 12000,
        },
    )

    board = next(row for row in workbench["roofing_board_fastener_template_decisions"] if row["template_bucket"] == "board_stock" and row["workbook_row"] == "58")
    fasteners = next(row for row in workbench["roofing_board_fastener_template_decisions"] if row["template_bucket"] == "fasteners")
    plates = next(row for row in workbench["roofing_board_fastener_template_decisions"] if row["template_bucket"] == "plates")
    dumpster = next(row for row in workbench["roofing_equipment_template_decisions"] if row["template_bucket"] == "dumpster")

    assert board["include"] is True
    assert fasteners["include"] is True
    assert plates["include"] is True
    assert dumpster["include"] is True
    assert board["proposal_review_required"] is True
    assert dumpster["proposal_review_required"] is True


def test_manual_uncheck_prevents_companion_proposal_from_rechecking_row() -> None:
    workbench = build_estimating_workbench(roofing_recommendation(), roofing_companion_data())
    primer = workbench["roofing_primer_template_decisions"][0]
    assert primer["include"] is True

    primer["include"] = False
    primer["manual_override"] = True
    primer["include_source"] = "estimator_edit"
    recalculated = recalculate_workbench_tables(workbench)
    recalculated_primer = recalculated["roofing_primer_template_decisions"][0]

    assert recalculated_primer["include"] is False
    assert recalculated_primer["manual_override"] is True
    assert recalculated_primer["proposal_source"] == "historical_companion"
    assert recalculated_primer["proposal_review_required"] is True


def test_reference_project_fills_material_and_labor_pattern_scaled_to_current_area() -> None:
    workbench = build_estimating_workbench(
        roofing_recommendation(),
        roofing_reference_project_data(),
        scope_override={
            "reference_job_ids": "REF1",
            "roof_type_substrate": "metal",
            "net_sqft": 20000,
            "estimated_sqft": 20000,
        },
    )

    primer = workbench["roofing_primer_template_decisions"][0]
    sealant = next(row for row in workbench["roofing_detail_template_decisions"] if row["template_bucket"] == "caulk_detail" and row["workbook_row"] == "43")
    seam_labor = next(row for row in workbench["roofing_labor_template_decisions"] if row["template_bucket"] == "labor_seam_sealer")

    assert primer["include"] is True
    assert primer["proposal_source"] == "reference_project"
    assert primer["basis_sqft"] == 20000
    assert primer["coverage_sqft_per_unit"] == 1000
    assert primer["proposal_evidence"]["reference_project"][0]["job_id"] == "REF1"
    assert primer["reference_project_evidence_summary"].startswith("reference job REF1")
    assert sealant["include"] is True
    assert sealant["units"] == 10
    assert seam_labor["include"] is True
    assert seam_labor["total_hours"] == 100
    assert seam_labor["crew_size"] == 4


def test_reference_project_can_be_selected_by_mentioning_known_job_id_in_notes() -> None:
    workbench = build_estimating_workbench(
        roofing_recommendation(),
        roofing_reference_project_data(),
        scope_override={
            "raw_input_notes": "This roof is like REF1 but at the current site.",
            "roof_type_substrate": "metal",
            "net_sqft": 10000,
            "estimated_sqft": 10000,
        },
    )

    primer = workbench["roofing_primer_template_decisions"][0]

    assert primer["include"] is True
    assert primer["proposal_source"] == "reference_project"
    assert primer["proposal_evidence"]["reference_project"][0]["job_id"] == "REF1"


def test_reference_project_marks_substrate_mismatch_for_review() -> None:
    workbench = build_estimating_workbench(
        roofing_recommendation(),
        roofing_reference_project_data(),
        scope_override={
            "reference_job_ids": ["REF1"],
            "roof_type_substrate": "membrane",
            "net_sqft": 10000,
            "estimated_sqft": 10000,
        },
    )

    primer = workbench["roofing_primer_template_decisions"][0]

    assert primer["include"] is True
    assert primer["proposal_source"] == "reference_project"
    assert primer["proposal_review_required"] is True
    assert any("substrate" in reason for reason in primer["proposal_review_reasons"])


def test_manual_uncheck_prevents_reference_project_from_rechecking_row() -> None:
    workbench = build_estimating_workbench(
        roofing_recommendation(),
        roofing_reference_project_data(),
        scope_override={"reference_job_ids": "REF1", "roof_type_substrate": "metal"},
    )
    primer = workbench["roofing_primer_template_decisions"][0]
    assert primer["include"] is True

    primer["include"] = False
    primer["manual_override"] = True
    primer["include_source"] = "estimator_edit"
    recalculated = recalculate_workbench_tables(workbench)
    recalculated_primer = recalculated["roofing_primer_template_decisions"][0]

    assert recalculated_primer["include"] is False
    assert recalculated_primer["manual_override"] is True
    assert recalculated_primer["proposal_source"] == "reference_project"


def test_insulation_workbench_uses_decision_sections_only() -> None:
    workbench = build_estimating_workbench(insulation_recommendation(), EstimatorData())

    assert "materials" not in workbench
    assert "labor" not in workbench
    assert "adders" not in workbench

    foam = workbench["insulation_foam_template_decisions"][0]
    assert foam["decision_id"] == "insulation_foam_template_selector"
    assert foam["include"] is True
    assert foam["basis_sqft"] == 2400
    assert foam["selector_options"]

    included_labor = {
        row["template_bucket"]
        for row in workbench["insulation_labor_template_decisions"]
        if row.get("include")
    }
    assert {"labor_set_up", "labor_foam", "labor_clean_up", "labor_loading", "labor_traveling"}.issubset(included_labor)

    draft = workbench_to_draft_workbook_inputs(workbench)
    assert set(draft) == {"template_type", "header", "workbook_decisions"}
    assert draft["template_type"] == "insulation"
    assert "material_rows" not in draft
    assert "labor_rows" not in draft
    assert any(row["row_type"] == "material" and row["template_bucket"] == "foam" for row in draft["workbook_decisions"])
    assert any(row["row_type"] == "labor" and row["template_bucket"] == "labor_foam" for row in draft["workbook_decisions"])


def test_insulation_foam_labor_uses_foam_set_driver_evidence() -> None:
    data = insulation_labor_driver_data()

    workbench = build_estimating_workbench(insulation_recommendation(), data)
    foam = workbench["insulation_foam_template_decisions"][0]
    labor = next(row for row in workbench["insulation_labor_template_decisions"] if row["template_bucket"] == "labor_foam")
    draft = workbench_to_draft_workbook_inputs(workbench)
    draft_labor = next(row for row in draft["workbook_decisions"] if row.get("row_type") == "labor" and row["template_bucket"] == "labor_foam")

    assert labor["labor_driver_applied"] is True
    assert labor["labor_driver_type"] == "material_quantity"
    assert labor["labor_driver_unit"] == "set"
    assert labor["historical_driver_rate"] == 6
    assert labor["labor_driver_quantity"] == foam["estimated_sets"]
    assert labor["total_hours"] == round(foam["estimated_sets"] * 6, 4)
    assert labor["total_hours_source"] == "driver_quantity_history"
    assert draft_labor["labor_driver_summary"].startswith(f"{foam['estimated_sets']:g} set")


def test_insulation_driver_labor_recomputes_when_material_quantity_changes() -> None:
    workbench = build_estimating_workbench(insulation_recommendation(), insulation_labor_driver_data())
    original_labor = next(row for row in workbench["insulation_labor_template_decisions"] if row["template_bucket"] == "labor_foam")

    workbench["insulation_foam_template_decisions"][0]["yield_or_coverage"] = 6000
    recalculated = recalculate_workbench_tables(workbench)
    foam = recalculated["insulation_foam_template_decisions"][0]
    labor = next(row for row in recalculated["insulation_labor_template_decisions"] if row["template_bucket"] == "labor_foam")

    assert foam["estimated_sets"] > workbench["insulation_foam_template_decisions"][0].get("estimated_sets", 0)
    assert labor["total_hours"] != original_labor["total_hours"]
    assert labor["total_hours"] == round(foam["estimated_sets"] * 6, 4)
    assert labor["total_hours_source"] == "driver_quantity_history"
    assert labor["labor_driver_applied"] is True


def test_insulation_driver_labor_preserves_estimator_hour_override() -> None:
    workbench = build_estimating_workbench(insulation_recommendation(), insulation_labor_driver_data())
    labor = next(row for row in workbench["insulation_labor_template_decisions"] if row["template_bucket"] == "labor_foam")
    labor["total_hours"] = 9
    labor["editable_total_hours"] = 9
    labor["total_hours_source"] = "estimator_override"
    labor["manual_labor_hours_override"] = True

    workbench["insulation_foam_template_decisions"][0]["yield_or_coverage"] = 6000
    recalculated = recalculate_workbench_tables(workbench)
    recalculated_labor = next(row for row in recalculated["insulation_labor_template_decisions"] if row["template_bucket"] == "labor_foam")

    assert recalculated_labor["total_hours"] == 9
    assert recalculated_labor["total_hours_source"] == "estimator_override"
    assert recalculated_labor["labor_driver_applied"] is False
    assert "override retained" in recalculated_labor["labor_driver_review_reason"]


def test_recalculate_removes_legacy_flat_rows() -> None:
    workbench = {
        "scope": {"division": "Roofing", "template_type": "roofing", "project_type": "roof coating", "net_sqft": 1000},
        "materials": [{"estimated_cost": 999999}],
        "labor": [{"estimated_cost": 999999}],
        "adders": [{"estimated_cost": 999999}],
        "roofing_coating_template_decisions": [
            {
                "include": True,
                "decision_id": "roofing_coating_system_row_26",
                "template_bucket": "coating",
                "workbook_row": "26",
                "editable_selector_code": "11",
                "basis_sqft": 1000,
                "gal_per_100_sqft": 1,
                "unit_price": 10,
            }
        ],
    }

    recalculated = recalculate_workbench_tables(workbench)

    assert "materials" not in recalculated
    assert "labor" not in recalculated
    assert "adders" not in recalculated
    assert recalculated["roofing_coating_template_decisions"][0]["estimated_cost"] == 100


def test_totals_use_decision_sections_only() -> None:
    workbench = {
        "scope": {"division": "Roofing", "template_type": "roofing", "project_type": "roof coating"},
        "materials": [{"include": True, "estimated_cost": 999999}],
        "labor": [{"include": True, "estimated_cost": 999999}],
        "adders": [{"include": True, "estimated_cost": 999999}],
        "roofing_coating_template_decisions": [
            {
                "include": True,
                "decision_id": "roofing_coating_system_row_26",
                "template_bucket": "coating",
                "workbook_row": "26",
                "editable_selector_code": "11",
                "basis_sqft": 1000,
                "gal_per_100_sqft": 1,
                "unit_price": 10,
            }
        ],
        "roofing_labor_template_decisions": [
            {
                "include": True,
                "decision_id": "roofing_labor_base",
                "template_bucket": "labor_base",
                "workbook_row": "122",
                "days": 1,
                "crew_size": 4,
                "hourly_rate": 25,
                "total_hours": 2,
            }
        ],
        "roofing_equipment_template_decisions": [
            {
                "include": True,
                "decision_id": "roofing_lift_row_73",
                "template_bucket": "lift",
                "workbook_row": "73",
                "editable_selector_code": "1",
                "period": 1,
                "unit_price": 10,
            }
        ],
    }

    totals = summarize_workbench_totals(workbench)

    assert totals == {
        "material_total": 100.0,
        "labor_total": 50.0,
        "adder_total": 12.0,
        "draft_total": 162.0,
    }


def test_roofing_source_allowances_flow_into_template_decisions() -> None:
    recommendation = EstimateRecommendation(
        parsed_fields={
            "division": "Roofing",
            "template_type": "roofing",
            "project_type": "roof coating",
            "net_sqft": 10000,
            "estimated_sqft": 10000,
            "coating_required": True,
            "coating_type": "silicone",
            "roof_condition": "rusted metal with open seams and penetrations",
            "notes": "Metal roof restoration with primer, open seams, penetrations, and detail work.",
        },
        recommended_scope=[],
        material_plan=[
            {
                "category": "primer",
                "item": "Primer allowance",
                "quantity": 10000,
                "unit": "sqft",
                "unit_price": 0.4,
                "estimated_cost": 4000,
                "selected_price_source": "rule_based_allowance",
                "include": True,
            },
            {
                "category": "seam_treatment",
                "item": "Seam treatment allowance",
                "quantity": 500,
                "unit": "lf",
                "unit_price": 3,
                "estimated_cost": 1500,
                "selected_price_source": "rule_based_allowance",
                "include": True,
            },
            {
                "category": "caulk_detail",
                "item": "Silicone Sausage",
                "quantity": 8,
                "unit": "unit",
                "unit_price": 150,
                "estimated_cost": 1200,
                "selected_price_source": "rule_based_allowance",
                "include": True,
            },
        ],
        labor_plan=[],
        travel_plan={},
        historical_calibration={},
        similar_examples=[],
        estimate_low=None,
        estimate_target=None,
        estimate_high=None,
        review_flags=[],
        human_review_required=True,
        draft_workbook_inputs={},
    )

    workbench = build_estimating_workbench(recommendation, EstimatorData())

    primer = workbench["roofing_primer_template_decisions"][0]
    assert primer["include"] is True
    assert primer["estimated_cost"] == 4000
    assert primer["cost_source"] == "historical_cost_default"

    caulk = next(row for row in workbench["roofing_detail_template_decisions"] if row["template_bucket"] == "caulk_detail")
    assert caulk["include"] is True
    assert caulk["estimated_units"] == 8
    assert caulk["estimated_cost"] == 1200

    seam = next(row for row in workbench["roofing_detail_quantity_template_decisions"] if row["template_bucket"] == "seams_misc")
    assert seam["include"] is True
    assert seam["linear_ft"] == 500
    assert seam["estimated_cost"] == 1500

    totals = summarize_workbench_totals(workbench)
    assert totals["material_total"] >= 6700


def test_mixed_formula_labor_exposes_display_hours_without_changing_workbook_hours() -> None:
    formula_workbench = {
        "scope": {"division": "Roofing", "template_type": "roofing", "project_type": "roof coating", "net_sqft": 1000},
        "roofing_labor_template_decisions": [
            {
                "include": True,
                "decision_id": "roofing_labor_base_row_122",
                "template_bucket": "labor_base",
                "workbook_row": "122",
                "days": 1,
                "crew_size": 4,
                "daily_rate": 1600,
                "total_hours": 0,
                "formula_mode": "mixed_formula",
            }
        ],
    }

    recalculated = recalculate_workbench_tables(formula_workbench)
    labor = recalculated["roofing_labor_template_decisions"][0]

    assert labor["estimated_cost"] == 1600
    assert labor["total_hours"] == 0
    assert labor["display_total_hours"] == 40
    assert "workbook_hours=0.0" in labor["calculated_output_summary"]


def test_mixed_formula_labor_uses_hourly_branch_when_hourly_rate_is_present() -> None:
    formula_workbench = {
        "scope": {"division": "Roofing", "template_type": "roofing", "project_type": "roof coating", "net_sqft": 1000},
        "roofing_labor_template_decisions": [
            {
                "include": True,
                "decision_id": "roofing_labor_base_row_122",
                "template_bucket": "labor_base",
                "workbook_row": "122",
                "days": 1,
                "crew_size": 4,
                "daily_rate": 1600,
                "hourly_rate": 72,
                "total_hours": 0,
                "formula_mode": "mixed_formula",
            }
        ],
    }

    recalculated = recalculate_workbench_tables(formula_workbench)
    labor = recalculated["roofing_labor_template_decisions"][0]

    assert labor["estimated_cost"] == 2880
    assert labor["formula_source"] == "hours_hourly_rate"
    assert labor["total_hours"] == 40
    assert labor["total_hours_source"] == "estimated_from_days_crew"
    assert labor["display_total_hours"] == 40


def test_roofing_labor_workbench_preserves_driver_evidence_from_recommendation() -> None:
    recommendation = roofing_recommendation()
    recommendation.labor_plan = [
        {
            "task": "labor_base",
            "template_bucket": "labor_base",
            "total_hours": 40,
            "adjusted_days": 1.25,
            "crew_size": 4,
            "hourly_rate": 72,
            "estimated_cost": 2880,
            "labor_driver_type": "material_quantity",
            "labor_driver_quantity": 200,
            "labor_driver_unit": "gal",
            "historical_driver_rate": 0.2,
            "historical_driver_evidence_count": 3,
            "labor_driver_applied": True,
            "labor_driver_summary": "200 gal x 0.2 hours_per_gallon from 3 paired historical jobs.",
        }
    ]

    workbench = build_estimating_workbench(recommendation, EstimatorData())
    base = next(row for row in workbench["roofing_labor_template_decisions"] if row["template_bucket"] == "labor_base")
    draft = workbench_to_draft_workbook_inputs(workbench)
    draft_base = next(row for row in draft["workbook_decisions"] if row.get("row_type") == "labor" and row["template_bucket"] == "labor_base")

    assert base["labor_driver_applied"] is True
    assert base["labor_driver_quantity"] == 200
    assert base["historical_driver_rate"] == 0.2
    assert draft_base["labor_driver_summary"].startswith("200 gal")


def test_roofing_coating_uses_formula_compatible_historical_unit_price() -> None:
    recommendation = EstimateRecommendation(
        parsed_fields={
            "division": "Roofing",
            "template_type": "roofing",
            "project_type": "roof coating",
            "net_sqft": 10000,
            "estimated_sqft": 10000,
            "coating_required": True,
            "coating_type": "silicone",
        },
        recommended_scope=[],
        material_plan=[
            {
                "category": "coating",
                "item": "Gaco Silicone Roof Coating",
                "quantity": 100,
                "unit": "gal",
                "unit_price": None,
                "estimated_cost": 0,
                "selected_price_source": "historical_fallback",
                "include": True,
            }
        ],
        labor_plan=[],
        travel_plan={},
        historical_calibration={},
        similar_examples=[],
        estimate_low=None,
        estimate_target=None,
        estimate_high=None,
        review_flags=[],
        human_review_required=True,
        draft_workbook_inputs={},
    )
    data = EstimatorData(
        template_rows=pd.DataFrame(
            [
                {
                    "template_row_id": "hist-coating-1",
                    "job_id": "J1",
                    "template_type": "roofing",
                    "sheet_name": "Estimate",
                    "row_number": 26,
                    "template_bucket": "coating",
                    "line_item_kind": "material",
                    "selected_item_name": "Gaco Silicone Roof Coating",
                    "unit": "gal",
                    "area_sqft": 10000,
                    "gal_per_100_sqft": 1.0,
                    "estimated_gallons": 100,
                    "estimated_units": 100,
                    "estimated_cost": 5000,
                }
            ]
        )
    )

    workbench = build_estimating_workbench(recommendation, data)
    coating = workbench["roofing_coating_template_decisions"][0]
    included_coatings = [row for row in workbench["roofing_coating_template_decisions"] if row.get("include")]

    assert coating["unit_price"] == 50
    assert sum(row["estimated_cost"] for row in included_coatings) == 5000
    assert coating["cost_source"] == "historical_formula_unit_price"
    assert coating["selected_pricing_candidate"] == "Gaco Silicone Roof Coating"


def test_edit_history_tracks_decision_rows_not_flat_rows() -> None:
    original = {
        "estimate_id": "edit-test",
        "scope": {"project_type": "roof coating"},
        "materials": [{"package_key": "coating", "unit_price": 999}],
        "roofing_coating_template_decisions": [
            {
                "decision_id": "roofing_coating_system_row_26",
                "workbook_row": "26",
                "include": True,
                "unit_price": 10,
                "gal_per_100_sqft": 1,
            }
        ],
    }
    edited = deepcopy(original)
    edited["roofing_coating_template_decisions"][0]["unit_price"] = 12
    edited["roofing_coating_template_decisions"][0]["include"] = False

    rows = build_edit_history_rows(original, edited, estimator="tester")

    assert any(row["section"] == "roofing_coating_template_decisions.roofing_coating_system_row_26" for row in rows)
    assert {row["field_name"] for row in rows} >= {"unit_price", "include"}
    assert not any(row["section"].startswith("materials.") for row in rows)
