from __future__ import annotations

import json
from copy import deepcopy

import pandas as pd

import jobscan.estimator.workbench as workbench_module
from jobscan.estimator.field_estimator import estimate_from_field_notes
from jobscan.estimator.schemas import EstimateRecommendation, EstimatorData
from jobscan.estimator.workbench import (
    build_edit_history_rows,
    build_estimating_workbench,
    recalculate_workbench_tables,
    summarize_workbench_totals,
    workbench_to_draft_workbook_inputs,
)


def test_insulation_template_type_wins_over_stale_roofing_division() -> None:
    assert workbench_module._is_insulation_scope(
        {
            "division": "Roofing",
            "template_type": "insulation",
            "estimate_mode": "insulation",
            "project_type": "spray foam insulation",
        }
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


def roofing_catalog_pricing_and_people_data() -> EstimatorData:
    active_components = [
        {"role": "Foreman", "hourly_wage": 40, "burden_rate": 1.0, "component_formula": "=B3*C3"},
        {"role": "Sprayer", "hourly_wage": 30, "burden_rate": 1.0, "component_formula": "=B4*C4"},
        {"role": "Laborer", "hourly_wage": 20, "burden_rate": 1.0, "component_formula": "=B5*C5"},
        {"role": "Laborer", "hourly_wage": 10, "burden_rate": 1.0, "component_formula": "=B6*C6"},
    ]
    labor_rows = []
    for row_number, package in (
        (116, "labor_prep"),
        (122, "labor_base"),
        (124, "labor_top_coat"),
        (132, "labor_cleanup"),
        (136, "labor_loading"),
        (120, "labor_seam_sealer"),
    ):
        labor_rows.append(
            {
                "template_type": "roofing",
                "row_number": row_number,
                "labor_package": package,
                "lookup_key": "4",
                "source_values_json": {
                    "crew_size": 4,
                    "hours_per_day": 10,
                    "crew_components": active_components,
                },
            }
        )
    return EstimatorData(
        pricing_catalog=pd.DataFrame(
            [
                {
                    "pricing_item_id": "foam",
                    "product_name": "Gaco Roof Foam 2733",
                    "category": "Roof Spray Foam",
                    "unit_price": 1.99,
                    "status": "active",
                    "is_current": True,
                    "needs_review": False,
                },
                {
                    "pricing_item_id": "coating",
                    "product_name": "Gaco Silicone",
                    "category": "Coatings",
                    "price_per_gallon": 42,
                    "unit_price": 210,
                    "status": "active",
                    "is_current": True,
                    "needs_review": False,
                },
            ]
        ),
        template_labor_options=pd.DataFrame(labor_rows),
    )


def roofing_primer_detail_pricing_data() -> EstimatorData:
    return EstimatorData(
        pricing_catalog=pd.DataFrame(
            [
                {
                    "pricing_item_id": "primer",
                    "product_name": "Gaco E-5320 Primer",
                    "category": "Primer",
                    "unit_price": 33,
                    "status": "active",
                    "is_current": True,
                    "needs_review": False,
                },
                {
                    "pricing_item_id": "silicone-sausage",
                    "product_name": "Silicone Sausage",
                    "category": "Sealant",
                    "unit_price": 12,
                    "status": "active",
                    "is_current": True,
                    "needs_review": False,
                },
                {
                    "pricing_item_id": "fabric",
                    "product_name": "GacoFlex Fabric",
                    "category": "Reinforcement Fabric",
                    "unit_price": 1,
                    "status": "active",
                    "is_current": True,
                    "needs_review": False,
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


def mismatched_insulation_reference_project_data() -> EstimatorData:
    return EstimatorData(
        template_rows=pd.DataFrame(
            [
                {
                    "job_id": "KU-BELT",
                    "source_file": "Estimate Insulation - KU 4G Belt Ramp (Both Sides 1,400').xlsx",
                    "template_type": "insulation",
                    "template_bucket": "labor_caulk",
                    "line_item_kind": "labor",
                    "row_number": 126,
                    "days": 1,
                    "crew_size": 4,
                    "total_hours": 42,
                    "hourly_rate": 80,
                },
                {
                    "job_id": "KU-BELT",
                    "source_file": "Estimate Insulation - KU 4G Belt Ramp (Both Sides 1,400').xlsx",
                    "template_type": "insulation",
                    "template_bucket": "thermal_barrier_coating",
                    "line_item_kind": "material",
                    "row_number": 30,
                    "selected_item_name": "Margin %",
                    "unit_price": 30,
                    "estimated_units": 30,
                },
                {
                    "job_id": "KU-BELT",
                    "source_file": "Estimate Insulation - KU 4G Belt Ramp (Both Sides 1,400').xlsx",
                    "template_type": "insulation",
                    "template_bucket": "foam",
                    "line_item_kind": "material",
                    "row_number": 19,
                    "selected_item_name": "Gaco Roof 2.7",
                    "area_sqft": 1400,
                    "thickness_inches": 2.5,
                    "yield_or_coverage": 2600,
                    "unit_price": 2.2,
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
    assert [row["include"] for row in workbench["roofing_coating_template_decisions"]] == [True, False, False]
    assert workbench["roofing_coating_template_decisions"][0]["decision_id"] == "roofing_coating_system_row_26"
    assert workbench["roofing_coating_template_decisions"][0]["selector_options"]

    draft = workbench_to_draft_workbook_inputs(workbench)
    assert set(draft) == {"template_type", "header", "pricing", "workbook_decisions"}
    assert draft["template_type"] == "roofing"
    assert "material_rows" not in draft
    assert "labor_rows" not in draft
    coating_decisions = [row for row in draft["workbook_decisions"] if row["template_bucket"] == "coating"]
    assert [row["workbook_row"] for row in coating_decisions] == ["26"]
    assert all(row["row_type"] == "material" for row in coating_decisions)


def test_roofing_spf_patch_uses_catalog_materials_and_people_sheet_labor_drivers() -> None:
    notes = "Foam blister, saturated needs to be torn out. Foam on 96 sqft of roof, 4inch thickness, need coating"
    data = roofing_catalog_pricing_and_people_data()
    recommendation = estimate_from_field_notes(notes, {"disable_ai_scope_interpreter": True}, data=data)

    workbench = recalculate_workbench_tables(build_estimating_workbench(recommendation, data))

    assert recommendation.parsed_fields["coating_required"] is True
    foam = next(row for row in workbench["roofing_foam_template_decisions"] if row["workbook_row"] == "19")
    coating = next(row for row in workbench["roofing_coating_template_decisions"] if row["workbook_row"] == "26")
    assert foam["include"] is True
    assert foam["basis_sqft"] == 96
    assert foam["thickness_inches"] == 4
    assert foam["unit_price"] == 1.99
    assert foam["estimated_cost"] > 0
    assert coating["include"] is True
    assert coating["basis_sqft"] == 96
    assert coating["unit_price"] == 42
    assert coating["estimated_cost"] > 0

    labor = {row["template_bucket"]: row for row in workbench["roofing_labor_template_decisions"]}
    for bucket in ("labor_prep", "labor_base", "labor_top_coat", "labor_cleanup"):
        assert labor[bucket]["include"] is True
        assert labor[bucket]["daily_rate"] == 1000
        assert labor[bucket]["days"] == 0.25
        assert labor[bucket]["estimated_cost"] == 250
        assert labor[bucket]["labor_driver_applied"] is True
    assert "labor_loading" not in labor
    logistics = {row["template_bucket"]: row for row in workbench["roofing_logistics_expense_template_decisions"]}
    assert logistics["labor_loading"]["include"] is True
    assert logistics["labor_loading"]["workbook_row"] == "136"
    assert logistics["labor_loading"]["formula_model"] == "insulation_hours_people_rate_trip_count"
    assert logistics["labor_traveling"]["include"] is True
    assert logistics["labor_traveling"]["workbook_row"] == "138"
    assert labor["labor_base"]["labor_driver_quantity"] == coating["estimated_gallons"]
    assert labor["labor_top_coat"]["labor_driver_quantity"] == coating["estimated_gallons"]
    assert labor["labor_seam_sealer"]["include"] is False


def test_roofing_checked_primer_detail_and_dumpster_rows_get_preview_costs() -> None:
    data = roofing_primer_detail_pricing_data()
    recommendation = roofing_recommendation()
    recommendation.parsed_fields.update(
        {
            "net_sqft": 10478,
            "estimated_sqft": 10478,
            "deduction_sqft": 580,
            "foam_thickness_inches": 1.25,
            "notes": (
                "Existing coated foam roof. IR survey found 580 sqft saturated foam to remove. "
                "Pressure wash, primer review, silicone sealant at penetrations and transitions, "
                "reinforced fabric details, coating, and dumpster disposal."
            ),
        }
    )
    workbench = build_estimating_workbench(recommendation, data)
    for section in (
        "roofing_primer_template_decisions",
        "roofing_detail_template_decisions",
        "roofing_equipment_template_decisions",
    ):
        for row in workbench.get(section) or []:
            if row.get("template_bucket") in {"primer", "caulk_detail", "fabric", "dumpster"}:
                row["include"] = True

    recalculated = recalculate_workbench_tables(workbench, data=data)
    primer = recalculated["roofing_primer_template_decisions"][0]
    details = {
        (row["template_bucket"], row["workbook_row"]): row
        for row in recalculated["roofing_detail_template_decisions"]
    }
    dumpster = next(row for row in recalculated["roofing_equipment_template_decisions"] if row["template_bucket"] == "dumpster")

    assert primer["include"] is True
    assert primer["basis_sqft"] > 0
    assert primer["unit_price"] == 33
    assert primer["estimated_cost"] > 0
    assert details[("caulk_detail", "43")]["unit_price"] == 12
    assert details[("caulk_detail", "43")]["estimated_units"] > 0
    assert details[("caulk_detail", "43")]["estimated_cost"] > 0
    assert details[("fabric", "79")]["unit_price"] == 1
    assert details[("fabric", "79")]["estimated_cost"] > 0
    assert dumpster["basis_sqft"] == 580
    assert dumpster["debris_thickness_inches"] == 1.25
    assert dumpster["debris_thickness_source"] == "foam_thickness_fallback"
    assert dumpster["thickness_inches"] == 1.25
    assert any("foam repair/replacement thickness" in warning for warning in dumpster["compatibility_warnings"])
    assert dumpster["estimated_cost"] > 0


def test_roofing_dumpster_uses_explicit_debris_thickness_when_available() -> None:
    data = roofing_primer_detail_pricing_data()
    recommendation = roofing_recommendation()
    recommendation.parsed_fields.update(
        {
            "net_sqft": 10478,
            "estimated_sqft": 10478,
            "deduction_sqft": 580,
            "foam_thickness_inches": 1.25,
            "tearout_thickness_inches": 2.0,
            "notes": "IR survey found 580 sqft saturated roof assembly to tear out. Include dumpster disposal.",
        }
    )
    workbench = build_estimating_workbench(recommendation, data)
    for row in workbench.get("roofing_equipment_template_decisions") or []:
        if row.get("template_bucket") == "dumpster":
            row["include"] = True

    recalculated = recalculate_workbench_tables(workbench, data=data)
    dumpster = next(row for row in recalculated["roofing_equipment_template_decisions"] if row["template_bucket"] == "dumpster")

    assert dumpster["basis_sqft"] == 580
    assert dumpster["debris_thickness_inches"] == 2.0
    assert dumpster["debris_thickness_source"] == "explicit_debris_thickness"
    assert dumpster["thickness_inches"] == 2.0
    assert not any("foam repair/replacement thickness" in warning for warning in dumpster["compatibility_warnings"])
    assert dumpster["estimated_cost"] > 0


def test_roofing_unchecked_rows_still_show_available_unit_prices() -> None:
    data = roofing_primer_detail_pricing_data()
    recommendation = roofing_recommendation()
    recommendation.parsed_fields.update(
        {
            "net_sqft": 10478,
            "estimated_sqft": 10478,
            "notes": "Roof restoration with coating. No mileage row selected yet.",
        }
    )
    workbench = recalculate_workbench_tables(build_estimating_workbench(recommendation, data), data=data)

    primer = workbench["roofing_primer_template_decisions"][0]
    caulk = next(row for row in workbench["roofing_detail_template_decisions"] if row["workbook_row"] == "43")
    fabric = next(row for row in workbench["roofing_detail_template_decisions"] if row["workbook_row"] == "79")
    truck = next(row for row in workbench["roofing_travel_freight_template_decisions"] if row["template_bucket"] == "truck_expense")

    assert primer["include"] is False
    assert primer["unit_price"] == 33
    assert caulk["include"] is False
    assert caulk["unit_price"] == 12
    assert fabric["include"] is False
    assert fabric["unit_price"] == 1
    assert truck["include"] is False
    assert truck["unit_price"] == 1.0
    assert truck["estimated_cost"] == 0


def test_roofing_chat_preferences_fill_rows_without_exact_workbook_metadata() -> None:
    recommendation = roofing_recommendation()
    data = roofing_catalog_pricing_and_people_data()

    workbench = build_estimating_workbench(
        recommendation,
        data,
        scope_override={
            "estimated_sqft": 96,
            "net_sqft": 96,
            "foam_required": True,
            "coating_required": True,
            "raw_input_notes": "Foam blister, saturated needs to be torn out. Foam on 96 sqft of roof, 4 inch thickness, need coating.",
            "estimator_chat": {
                "source": "ai_chat",
                "confidence": 0.78,
                "assistant_message": "Include roof foam, coating, truck expense, and loading labor.",
                "workbook_decision_preferences": [
                    {"template_bucket": "foam", "include": True, "proposed_values": {"basis_sqft": 96, "thickness_inches": 4}},
                    {"template_bucket": "coating", "include": True, "proposed_values": {"basis_sqft": 96}},
                    {"template_bucket": "truck_expense", "include": True, "proposed_values": {"trip_count": 1, "round_trip_miles": 50, "unit_price": 0.75}},
                    {"template_bucket": "labor_loading", "include": True, "proposed_values": {"days": 0.25, "crew_size": 4}},
                ],
            },
        },
    )

    foam = next(row for row in workbench["roofing_foam_template_decisions"] if row["workbook_row"] == "19")
    coating = next(row for row in workbench["roofing_coating_template_decisions"] if row["workbook_row"] == "26")
    truck = next(row for row in workbench["roofing_travel_freight_template_decisions"] if row["template_bucket"] == "truck_expense")
    loading = next(row for row in workbench["roofing_logistics_expense_template_decisions"] if row["template_bucket"] == "labor_loading")

    assert foam["proposal_source"] == "chat_estimator"
    assert foam["include"] is True
    assert foam["basis_sqft"] == 96
    assert foam["thickness_inches"] == 4
    assert foam["unit_price"] == 1.99
    assert foam["estimated_cost"] > 0
    assert coating["include"] is True
    assert coating["estimated_cost"] > 0
    assert truck["include"] is True
    assert truck["trip_count"] == 1
    assert truck["round_trip_miles"] == 50
    assert truck["estimated_cost"] == 37.5
    assert loading["include"] is True
    assert loading["workbook_row"] == "136"
    assert loading["hours_per_day"] == 0.25
    assert loading["people_count"] == 4
    assert not loading.get("daily_rate")
    assert loading["unit_price"] == 25.5
    assert loading["estimated_cost"] == 25.5


def test_workbench_to_draft_inputs_can_skip_recalculation(monkeypatch) -> None:
    recalculated = recalculate_workbench_tables(build_estimating_workbench(roofing_recommendation(), EstimatorData()))

    def fail_recalculate(_workbench):
        raise AssertionError("workbench_to_draft_workbook_inputs recalculated despite recalculate=False")

    monkeypatch.setattr(workbench_module, "recalculate_workbench_tables", fail_recalculate)

    draft = workbench_to_draft_workbook_inputs(recalculated, recalculate=False)

    assert draft["template_type"] == "roofing"
    assert any(row["template_bucket"] == "coating" for row in draft["workbook_decisions"])


def test_relationship_package_payload_filters_to_companion_eligible_rows() -> None:
    data = EstimatorData(
        relationship_package_cooccurrence=pd.DataFrame(
            [
                {"package_a": "coating", "package_b": "primer", "co_occurrence_rate": 0.8, "job_count": 12},
                {"package_a": "coating", "package_b": "weak", "co_occurrence_rate": 0.2, "job_count": 40},
                {"package_a": "coating", "package_b": "thin", "co_occurrence_rate": 0.9, "job_count": 1},
                {"package_a": "coating", "package_b": "sealant", "co_occurrence_rate": 0.7, "job_count": 8},
            ]
        )
    )

    rows = workbench_module._relationship_package_cooccurrence_payload(data, limit=1)

    assert len(rows) == 1
    assert rows[0]["package_b"] == "primer"
    assert rows[0]["job_count"] == 12


def test_chat_loading_travel_preferences_apply_to_logistics_expense_rows() -> None:
    recommendation = insulation_recommendation()
    recommendation.parsed_fields.update(
        {
            "estimator_chat": {
                "source": "ai_chat",
                "confidence": 0.7,
                "assistant_message": "Use loading and travel.",
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
                        "proposed_values": {"hours_per_day": 2.5, "people_count": 4, "unit_price": 13},
                    },
                ],
            }
        }
    )

    workbench = build_estimating_workbench(recommendation, EstimatorData())
    rows = {row["template_bucket"]: row for row in workbench["insulation_logistics_expense_template_decisions"]}

    assert rows["labor_loading"]["include"] is True
    assert rows["labor_loading"]["hours_per_day"] == 1
    assert rows["labor_loading"]["people_count"] == 2
    assert not rows["labor_loading"].get("daily_rate")
    assert rows["labor_loading"]["proposal_source"] == "chat_estimator"
    assert rows["labor_traveling"]["hours_per_day"] == 2.5
    assert rows["labor_traveling"]["people_count"] == 4
    assert rows["labor_traveling"]["unit_price"] == 13


def test_roofing_loading_travel_scan_and_meals_are_logistics_expense_rows() -> None:
    recommendation = roofing_recommendation()
    recommendation.parsed_fields["notes"] = "Roof coating project. Include loading, traveling, infrared scan, and meals lodging."
    recommendation.parsed_fields["raw_input_notes"] = recommendation.parsed_fields["notes"]
    recommendation.parsed_fields["estimator_chat"] = {
        "source": "ai_chat",
        "confidence": 0.72,
        "workbook_decision_preferences": [
            {
                "template_bucket": "labor_loading",
                "workbook_row": "136",
                "include": True,
                "proposed_values": {"days": 8, "crew_size": 2, "daily_rate": 1685},
            },
            {
                "template_bucket": "labor_traveling",
                "workbook_row": "138",
                "include": True,
                "proposed_values": {"hours_per_day": 2.5, "people_count": 2, "unit_price": 13},
            },
            {
                "template_bucket": "infrared_scan",
                "workbook_row": "141",
                "include": True,
                "proposed_values": {"hours_per_day": 1, "unit_price": 75},
            },
            {
                "template_bucket": "meals_lodging",
                "workbook_row": "144",
                "include": True,
                "proposed_values": {"days": 1, "people_count": 2, "unit_price": 125},
            },
        ],
    }

    workbench = build_estimating_workbench(recommendation, EstimatorData())
    labor_buckets = {row["template_bucket"] for row in workbench["roofing_labor_template_decisions"]}
    logistics = {row["template_bucket"]: row for row in workbench["roofing_logistics_expense_template_decisions"]}

    assert not {"labor_loading", "labor_traveling", "infrared_scan", "meals_lodging"}.intersection(labor_buckets)
    assert {"labor_loading", "labor_traveling", "infrared_scan", "meals_lodging"}.issubset(logistics)
    assert logistics["labor_loading"]["include"] is True
    assert logistics["labor_loading"]["hours_per_day"] == 0.5
    assert logistics["labor_loading"]["people_count"] == 2
    assert logistics["labor_loading"]["unit_price"] == 25.5
    assert not logistics["labor_loading"].get("daily_rate")
    assert logistics["labor_traveling"]["estimated_cost"] == 65
    assert logistics["infrared_scan"]["estimated_cost"] == 75
    assert logistics["meals_lodging"]["estimated_cost"] == 250

    draft = workbench_to_draft_workbook_inputs(workbench)
    assert any(row["template_bucket"] == "labor_loading" and row["workbook_row"] == "136" for row in draft["workbook_decisions"])
    assert any(row["template_bucket"] == "meals_lodging" and row["workbook_row"] == "144" for row in draft["workbook_decisions"])


def test_roofing_free_adders_are_post_markup_decisions_and_export_as_adders() -> None:
    recommendation = roofing_recommendation()
    recommendation.parsed_fields["estimator_chat"] = {
        "source": "ai_chat",
        "confidence": 0.86,
        "workbook_decision_preferences": [
            {
                "decision_id": "roofing_free_adder_row_173_warranty",
                "section": "roofing_free_adder_template_decisions",
                "template_bucket": "warranty",
                "workbook_row": "173",
                "include": True,
                "proposed_values": {
                    "template_line": "Warranty",
                    "amount": 600.0,
                    "estimated_cost": 600.0,
                    "markup_treatment": "post_markup",
                },
                "confidence": 0.86,
                "review_required": True,
            }
        ],
    }

    workbench = build_estimating_workbench(recommendation, EstimatorData())
    row = workbench["roofing_free_adder_template_decisions"][0]
    totals = summarize_workbench_totals(workbench)
    draft = workbench_to_draft_workbook_inputs(workbench)
    draft_row = next(row for row in draft["workbook_decisions"] if row.get("row_type") == "adder")

    assert row["include"] is True
    assert row["template_line"] == "Warranty"
    assert row["estimated_cost"] == 600.0
    assert row["markup_treatment"] == "post_markup"
    assert totals["post_markup_adder_total"] == 600.0
    assert totals["draft_total"] == totals["worksheet_price"] + 600.0
    assert draft_row["template_bucket"] == "warranty"
    assert draft_row["estimated_cost"] == 600.0


def test_roofing_reference_quantity_cost_is_preserved_when_area_is_missing() -> None:
    recommendation = roofing_recommendation()
    recommendation.parsed_fields.pop("estimated_sqft", None)
    recommendation.parsed_fields.pop("net_sqft", None)
    recommendation.parsed_fields["estimator_chat"] = {
        "source": "ai_chat",
        "confidence": 0.86,
        "workbook_decision_preferences": [
            {
                "decision_id": "roofing_coating_system_row_26",
                "section": "roofing_coating_template_decisions",
                "template_bucket": "coating",
                "workbook_row": "26",
                "include": True,
                "proposed_values": {
                    "estimated_units": 17.83,
                    "estimated_gallons": 17.83,
                    "unit_price": 36.0,
                    "estimated_cost": 641.70,
                },
                "confidence": 0.86,
                "review_required": True,
            }
        ],
    }

    workbench = build_estimating_workbench(recommendation, EstimatorData())
    coating = next(row for row in workbench["roofing_coating_template_decisions"] if row["workbook_row"] == "26")

    assert coating["include"] is True
    assert coating["basis_sqft"] == 0
    assert coating["estimated_gallons"] == 17.83
    assert coating["estimated_cost"] == 641.7
    assert coating["formula_source"] == "reference_direct_quantity"


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


def test_workbench_prefers_approved_template_pricing_link_for_material_candidate() -> None:
    data = EstimatorData(
        template_product_options=pd.DataFrame(
            [
                {
                    "template_product_option_id": "tpl_gaco_2",
                    "template_type": "insulation",
                    "template_bucket": "foam",
                    "row_number": 21,
                    "product_name": "Gaco 2.0 lb.",
                    "unit": "unit",
                    "unit_price": 0,
                }
            ]
        ),
        template_pricing_option_links=pd.DataFrame(
            [
                {
                    "link_id": "map_gaco_2_enverge",
                    "template_product_option_id": "tpl_gaco_2",
                    "pricing_candidate_key": "price_enverge",
                    "template_type": "insulation",
                    "template_bucket": "foam",
                    "row_number": 21,
                    "template_product_name": "Gaco 2.0 lb.",
                    "canonical_template_option": "Enverge OnePass",
                    "pricing_product_name": "Enverge Closed Cell OnePass",
                    "confidence": 0.96,
                    "reason": "Approved mapping from LLM review.",
                    "review_status": "approved",
                }
            ]
        ),
        pricing_catalog=pd.DataFrame(
            [
                {
                    "pricing_item_id": "price_wrong",
                    "product_name": "GacoRoofFoam F2733",
                    "product_name_normalized": "gacorooffoam f2733",
                    "unit_price": 99,
                    "is_current": True,
                    "status": "active",
                },
                {
                    "pricing_item_id": "price-current-enverge",
                    "product_name": "Enverge Closed Cell OnePass",
                    "product_name_normalized": "enverge closed cell onepass",
                    "unit_price": 6.1,
                    "is_current": True,
                    "status": "active",
                },
            ]
        ),
    )

    workbench = build_estimating_workbench(
        insulation_recommendation(),
        data,
        scope_override={"foam_type": "closed_cell", "raw_input_notes": "Use closed-cell wall foam."},
    )
    foam = workbench["insulation_foam_template_decisions"][0]

    assert foam["selected_pricing_candidate"] == "Enverge Closed Cell OnePass"
    assert foam["unit_price"] == 6.1
    assert foam["selected_price_source"] == "template_pricing_option_link"
    assert "template_pricing_option_link" in foam["pricing_evidence_summary"]
    selected_candidate = next(
        candidate for candidate in foam["pricing_candidates"] if candidate["item_name"] == "Enverge Closed Cell OnePass"
    )
    assert selected_candidate["source"] == "template_pricing_option_link"
    assert "Approved mapping" in selected_candidate["why_suggested"]


def test_open_cell_scope_does_not_select_closed_cell_template_pricing_link() -> None:
    recommendation = insulation_recommendation()
    recommendation.parsed_fields.update(
        {
            "foam_type": "open_cell",
            "notes": "30x40 metal building with 9 ft walls. Use open cell foam at R21.",
        }
    )
    data = EstimatorData(
        template_product_options=pd.DataFrame(
            [
                {
                    "template_product_option_id": "tpl_gaco_2",
                    "template_type": "insulation",
                    "template_bucket": "foam",
                    "row_number": 21,
                    "product_name": "Gaco 2.0 lb.",
                    "unit": "unit",
                    "unit_price": 0,
                },
                {
                    "template_product_option_id": "tpl_gaco_half",
                    "template_type": "insulation",
                    "template_bucket": "foam",
                    "row_number": 19,
                    "product_name": "Gaco 0.5 lb.",
                    "unit": "unit",
                    "unit_price": 2.15,
                },
            ]
        ),
        template_pricing_option_links=pd.DataFrame(
            [
                {
                    "link_id": "map_gaco_2_enverge",
                    "template_product_option_id": "tpl_gaco_2",
                    "pricing_candidate_key": "price_enverge",
                    "template_type": "insulation",
                    "template_bucket": "foam",
                    "row_number": 21,
                    "template_product_name": "Gaco 2.0 lb.",
                    "canonical_template_option": "Enverge OnePass",
                    "pricing_product_name": "Enverge Closed Cell OnePass",
                    "confidence": 0.96,
                    "reason": "Approved mapping from LLM review.",
                    "review_status": "approved",
                }
            ]
        ),
        pricing_catalog=pd.DataFrame(
            [
                {
                    "pricing_item_id": "price-current-enverge",
                    "product_name": "Enverge Closed Cell OnePass",
                    "product_name_normalized": "enverge closed cell onepass",
                    "unit_price": 2.05,
                    "is_current": True,
                    "status": "active",
                },
                {
                    "pricing_item_id": "price-open-cell",
                    "product_name": "Gaco 0.5 lb. Open Cell",
                    "product_name_normalized": "gaco 0.5 lb open cell",
                    "unit_price": 2.15,
                    "is_current": True,
                    "status": "active",
                },
            ]
        ),
    )

    workbench = build_estimating_workbench(recommendation, data)
    foam = workbench["insulation_foam_template_decisions"][0]

    assert foam["resolved_template_option"] == "Gaco 0.5 lb."
    assert "Closed Cell" not in foam["selected_pricing_candidate"]
    assert foam["selected_pricing_candidate"] in {"Gaco 0.5 lb.", "Gaco 0.5 lb. Open Cell"}
    assert foam["unit_price"] == 2.15
    assert not any(
        candidate["item_name"] == "Enverge Closed Cell OnePass" and candidate["compatibility_status"] == "compatible"
        for candidate in foam["pricing_candidates"]
    )


def test_open_cell_scope_prefers_same_family_historical_selector_before_gaco_default() -> None:
    recommendation = insulation_recommendation()
    recommendation.parsed_fields.update(
        {
            "foam_type": "open_cell",
            "notes": "30x40 metal building with 9 ft walls. Use open cell foam at R21.",
        }
    )
    data = EstimatorData(
        estimator_decision_recommendations=pd.DataFrame(
            [
                {
                    "decision_id": "insulation_foam_system",
                    "field_name": "selector_code",
                    "recommended_value": "22",
                    "evidence_count": 5,
                    "source_jobs_count": 5,
                    "confidence": "medium",
                    "history_table": "test",
                },
                {
                    "decision_id": "insulation_foam_system",
                    "field_name": "resolved_item_name",
                    "recommended_value": "NCFI 0.5 lb.",
                    "evidence_count": 5,
                    "source_jobs_count": 5,
                    "confidence": "medium",
                    "history_table": "test",
                },
                {
                    "decision_id": "insulation_foam_system",
                    "field_name": "unit_price",
                    "recommended_value": 1.95,
                    "evidence_count": 5,
                    "source_jobs_count": 5,
                    "confidence": "medium",
                    "history_table": "test",
                },
            ]
        )
    )

    workbench = build_estimating_workbench(recommendation, data)
    foam = workbench["insulation_foam_template_decisions"][0]

    assert foam["editable_selector_code"] == "22"
    assert foam["resolved_template_option"] == "NCFI 0.5 lb."


def test_workbench_uses_materials_lookup_pricing_for_board_and_fabric() -> None:
    data = EstimatorData(
        template_lookup_tables=pd.DataFrame(
            [
                {
                    "lookup_table_id": "lookup_iso",
                    "template_type": "roofing",
                    "template_name": "Roofing Template",
                    "sheet_name": "Materials",
                    "table_name": "board",
                    "row_number": 18,
                    "lookup_key": "ISO board",
                    "values_json": '{"A": "ISO board", "B": "1\\"", "C": 47.38, "D": 42.25, "E": "Square"}',
                },
                {
                    "lookup_table_id": "lookup_fabric",
                    "template_type": "roofing",
                    "template_name": "Roofing Template",
                    "sheet_name": "Materials",
                    "table_name": "fabric",
                    "row_number": 9,
                    "lookup_key": "",
                    "values_json": '{"A": null, "B": "12\\"", "C": 53.03, "D": 300}',
                },
            ]
        )
    )

    workbench = build_estimating_workbench(
        roofing_recommendation(),
        data,
        scope_override={
            "project_type": "roof replacement",
            "raw_input_notes": "Full tear off with damaged ISO board and fabric reinforcement at open seams.",
            "net_sqft": 10000,
            "estimated_sqft": 10000,
        },
    )
    board = next(
        row
        for row in workbench["roofing_board_fastener_template_decisions"]
        if row["template_bucket"] == "board_stock" and row["workbook_row"] == "58"
    )
    fabric = next(row for row in workbench["roofing_detail_template_decisions"] if row["template_bucket"] == "fabric")

    assert board["unit_price"] == 47.38
    assert board["selected_pricing_candidate"].startswith("ISO board")
    assert any(candidate["source"] == "template_lookup_materials" for candidate in board["pricing_candidates"])
    assert fabric["unit_price"] == round(53.03 / 300, 4)
    assert any(candidate["source"] == "template_lookup_materials" for candidate in fabric["pricing_candidates"])


def test_roofing_companion_relationships_suggest_primer_and_detail_rows() -> None:
    data = roofing_companion_data()
    data.pricing_catalog = roofing_primer_detail_pricing_data().pricing_catalog
    workbench = build_estimating_workbench(
        roofing_recommendation(),
        data,
        scope_override={
            "net_sqft": 8000,
            "estimated_sqft": 8000,
            "notes": "Roof coating with primer and caulk/detail sealant around penetrations.",
        },
    )

    primer = workbench["roofing_primer_template_decisions"][0]
    sealant = next(row for row in workbench["roofing_detail_template_decisions"] if row["template_bucket"] == "caulk_detail")

    assert primer["include"] is False
    assert primer["estimated_cost"] == 0
    assert primer["proposal_source"] == "historical_companion"
    assert any("no calculable cost" in warning for warning in primer["compatibility_warnings"])
    assert primer["proposal_evidence"]["relationship_package_cooccurrence"]
    assert primer["proposal_review_required"] is True
    assert any("Historical companion suggestion" in warning for warning in primer["compatibility_warnings"])
    assert sealant["include"] is True
    assert sealant["estimated_cost"] > 0
    seams = next(row for row in workbench["roofing_detail_quantity_template_decisions"] if row["template_bucket"] == "seams_misc")
    assert seams["include"] is False


def test_historical_companion_detail_quantity_without_basis_is_not_included() -> None:
    formula_workbench = {
        "scope": {
            "division": "Roofing",
            "template_type": "roofing",
            "project_type": "coated foam roof",
            "net_sqft": 11058,
            "notes": "Coated foam roof with silicone coating, fabric, fasteners, plates, granules, and minor seams.",
        },
        "roofing_detail_quantity_template_decisions": [
            {
                "include": True,
                "proposal_source": "historical_companion",
                "template_bucket": "seams_misc",
                "workbook_row": "47",
                "resolved_template_option": "Misc. / Seams",
                "linear_ft": 0,
                "estimated_units": 0,
                "amount": 0,
                "estimated_cost": 0,
            }
        ],
    }

    recalculated = recalculate_workbench_tables(formula_workbench)
    seams = next(row for row in recalculated["roofing_detail_quantity_template_decisions"] if row["workbook_row"] == "47")

    assert seams["include"] is False
    assert seams["estimated_cost"] == 0


def test_fabric_companion_suggests_seam_detail_labor_review_marked() -> None:
    workbench = build_estimating_workbench(roofing_recommendation(), roofing_companion_data())
    fabric = next(row for row in workbench["roofing_detail_template_decisions"] if row["template_bucket"] == "fabric")
    fabric["include"] = True
    fabric["manual_override"] = True
    fabric["include_source"] = "estimator_edit"

    recalculated = recalculate_workbench_tables(workbench)
    seam_labor = next(row for row in recalculated["roofing_labor_template_decisions"] if row["template_bucket"] == "labor_seam_sealer")

    assert seam_labor["include"] is False
    assert seam_labor["proposal_source"] == "historical_companion"
    assert seam_labor["proposal_review_required"] is True
    assert "fabric" in seam_labor["proposal_review_reasons"][0]
    assert any("no calculable cost" in warning for warning in seam_labor["compatibility_warnings"])


def test_open_seams_do_not_auto_check_fabric_without_quantity_or_explicit_fabric() -> None:
    workbench = build_estimating_workbench(
        roofing_recommendation(),
        EstimatorData(),
        scope_override={
            "project_type": "roof coating",
            "raw_input_notes": "Metal roof with open seams, bad caulk, and penetrations. Seal seams and penetrations.",
            "net_sqft": 8000,
            "estimated_sqft": 8000,
        },
    )

    fabric = next(row for row in workbench["roofing_detail_template_decisions"] if row["template_bucket"] == "fabric")

    assert fabric["include"] is False
    assert fabric["estimated_cost"] == 0.0


def test_no_gutters_and_no_edge_metal_prevent_auto_checked_zero_cost_accessories() -> None:
    workbench = build_estimating_workbench(
        roofing_recommendation(),
        EstimatorData(),
        scope_override={
            "project_type": "roof coating",
            "raw_input_notes": "Metal roof coating scope. No gutters. No edge metal.",
            "net_sqft": 8000,
            "estimated_sqft": 8000,
        },
    )

    accessories = {row["template_bucket"]: row for row in workbench["roofing_accessory_template_decisions"]}

    assert accessories["edge_metal"]["include"] is False
    assert accessories["gutter"]["include"] is False


def test_roofing_chat_shorthand_basis_updates_apply_to_workbench_rows() -> None:
    recommendation = roofing_recommendation()
    recommendation.parsed_fields = {
        **recommendation.parsed_fields,
        "estimated_sqft": 8000,
        "net_sqft": 8000,
        "estimator_chat": {
            "source": "ai_chat",
            "confidence": 0.8,
            "assistant_message": "Multiply basis sqft by 1.2 for ribs.",
            "workbook_decision_preferences": [
                {
                    "decision_id": "roofing_coating_row_26",
                    "template_bucket": "coating",
                    "include": True,
                    "proposed_values": {"basis_sqft": 9600, "unit_price": 36},
                },
                {
                    "decision_id": "roofing_primer_row_39",
                    "template_bucket": "primer",
                    "include": True,
                    "proposed_values": {"basis_sqft": 9600},
                },
            ],
        },
    }

    workbench = build_estimating_workbench(recommendation, EstimatorData())
    coating = next(row for row in workbench["roofing_coating_template_decisions"] if row["workbook_row"] == "26")
    primer = next(row for row in workbench["roofing_primer_template_decisions"] if row["workbook_row"] == "39")

    assert coating["basis_sqft"] == 9600
    assert coating["unit_price"] == 36
    assert coating["proposal_source"] == "chat_estimator"
    assert primer["basis_sqft"] == 9600
    assert primer["proposal_source"] == "chat_estimator"


def test_full_tearoff_notes_do_not_auto_include_board_fasteners_and_disposal_rows() -> None:
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

    assert board["include"] is False
    assert fasteners["include"] is False
    assert plates["include"] is False
    assert dumpster["include"] is False
    assert board.get("proposal_source") != "deterministic_rule"
    assert dumpster.get("proposal_source") != "deterministic_rule"


def test_board_fastener_attention_notes_are_review_only_not_full_board_scope() -> None:
    workbench = build_estimating_workbench(
        roofing_recommendation(),
        EstimatorData(),
        scope_override={
            "project_type": "roof coating",
            "raw_input_notes": "Coating restoration review; some areas may need board, fastener, or plate attention before coating.",
            "net_sqft": 12000,
            "estimated_sqft": 12000,
        },
    )

    board = next(row for row in workbench["roofing_board_fastener_template_decisions"] if row["template_bucket"] == "board_stock" and row["workbook_row"] == "58")
    fasteners = next(row for row in workbench["roofing_board_fastener_template_decisions"] if row["template_bucket"] == "fasteners")
    plates = next(row for row in workbench["roofing_board_fastener_template_decisions"] if row["template_bucket"] == "plates")

    assert board["include"] is False
    assert fasteners["include"] is False
    assert plates["include"] is False
    assert board["compatibility_status"] == "review"
    assert any("review-only" in warning for warning in board["compatibility_warnings"])


def test_roofing_fastener_plate_units_calculate_from_board_area_pattern() -> None:
    workbench = {
        "scope": {"division": "Roofing", "template_type": "roofing", "project_type": "roof coating", "net_sqft": 1000},
        "roofing_board_fastener_template_decisions": [
            {
                "include": True,
                "decision_id": "roofing_fasteners_row_63",
                "template_bucket": "fasteners",
                "workbook_row": "63",
                "resolved_template_option": "Fasteners",
                "board_area_sqft": 960,
                "unit_price_per_thousand": 250,
            },
            {
                "include": True,
                "decision_id": "roofing_plates_row_65",
                "template_bucket": "plates",
                "workbook_row": "65",
                "resolved_template_option": "Plates",
                "board_area_sqft": 960,
                "unit_price_per_thousand": 200,
            },
        ],
    }

    recalculated = recalculate_workbench_tables(workbench)
    rows = {row["template_bucket"]: row for row in recalculated["roofing_board_fastener_template_decisions"]}

    assert rows["fasteners"]["estimated_units"] == 360
    assert rows["fasteners"]["formula_source"] == "board_area_sqft_div_32_times_12"
    assert rows["fasteners"]["estimated_cost"] == 90
    assert rows["plates"]["estimated_units"] == 360
    assert rows["plates"]["formula_source"] == "board_area_sqft_div_32_times_12"
    assert rows["plates"]["estimated_cost"] == 72


def test_manual_uncheck_prevents_companion_proposal_from_rechecking_row() -> None:
    workbench = build_estimating_workbench(roofing_recommendation(), roofing_companion_data())
    primer = workbench["roofing_primer_template_decisions"][0]
    assert primer["include"] is False

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


def test_insulation_reference_project_ignores_rows_from_roofing_spf_layout() -> None:
    workbench = build_estimating_workbench(
        insulation_recommendation(),
        mismatched_insulation_reference_project_data(),
        scope_override={
            "reference_job_ids": "KU-BELT",
            "net_sqft": 1750,
            "estimated_sqft": 1750,
            "net_insulation_area_sqft": 1750,
            "notes": "Closed-cell foam on walls and ceiling; review thermal barrier.",
        },
    )

    foam = workbench["insulation_foam_template_decisions"][0]
    thermal = workbench["insulation_thermal_barrier_template_decisions"][0]
    reference_labor = [
        row
        for row in workbench["insulation_labor_template_decisions"]
        if row.get("proposal_source") == "reference_project"
    ]

    assert foam["proposal_source"] == "reference_project"
    assert thermal.get("resolved_template_option") != "Margin %"
    assert not reference_labor


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
    assert {"labor_set_up", "labor_foam", "labor_clean_up"}.issubset(included_labor)
    assert "labor_loading" not in included_labor
    assert "labor_traveling" not in included_labor
    equipment_buckets = {row["template_bucket"] for row in workbench["insulation_equipment_logistics_template_decisions"]}
    assert not {"labor_loading", "labor_traveling", "infrared_scan", "meals_lodging"}.intersection(equipment_buckets)
    logistics_expense_rows = {
        row["template_bucket"]: row for row in workbench["insulation_logistics_expense_template_decisions"]
    }
    assert {"labor_loading", "labor_traveling", "infrared_scan", "meals_lodging"}.issubset(logistics_expense_rows)
    assert logistics_expense_rows["labor_loading"]["include"] is True
    assert logistics_expense_rows["labor_traveling"]["include"] is True
    assert logistics_expense_rows["infrared_scan"]["include"] is False
    assert logistics_expense_rows["meals_lodging"]["include"] is False

    draft = workbench_to_draft_workbook_inputs(workbench)
    assert set(draft) == {"template_type", "header", "pricing", "workbook_decisions"}
    assert draft["template_type"] == "insulation"
    assert "material_rows" not in draft
    assert "labor_rows" not in draft
    assert any(row["row_type"] == "material" and row["template_bucket"] == "foam" for row in draft["workbook_decisions"])
    assert any(row["row_type"] == "material" and row["template_bucket"] == "labor_loading" for row in draft["workbook_decisions"])
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


def test_estimator_chat_foam_preference_fills_thickness_and_estimated_units() -> None:
    workbench = build_estimating_workbench(
        insulation_recommendation(),
        EstimatorData(),
        scope_override={
            "estimated_sqft": 2226,
            "net_sqft": 2226,
            "net_insulation_area_sqft": 2226,
            "foam_type": "open_cell",
            "foam_thickness_inches": 5,
            "estimator_chat": {
                "source": "ai_chat",
                "confidence": 0.82,
                "assistant_message": "Use 5 inch open-cell foam for the 2,226 sq ft metal building.",
                "workbook_decision_preferences": [
                    {
                        "decision_id": "insulation_foam_template_selector",
                        "template_bucket": "foam",
                        "include": True,
                        "proposed_values": {
                            "basis_sqft": 2226,
                            "thickness_inches": 5,
                            "yield_or_coverage": 4500,
                            "resolved_template_option": "Gaco 0.5 lb.",
                        },
                        "confidence": 0.82,
                    }
                ],
            },
        },
    )

    foam = workbench["insulation_foam_template_decisions"][0]

    assert foam["proposal_source"] == "chat_estimator"
    assert foam["include"] is True
    assert foam["thickness_inches"] == 5
    assert foam["yield_or_coverage"] == 2600
    assert foam["yield_or_coverage_source"] == "template_default"
    assert foam["estimated_units"] == 4280.769231
    assert foam["estimated_sets"] == 4.280769
    assert foam["estimated_cost"] > 0
    assert "chat_estimator" in foam["decision_evidence_types"]


def test_estimator_chat_foam_recalculation_restores_pricing_and_yield_from_template_data() -> None:
    recommendation = insulation_recommendation()
    data = EstimatorData(
        template_product_options=pd.DataFrame(
            [
                {
                    "template_product_option_id": "tpl_gaco_half",
                    "template_type": "insulation",
                    "template_bucket": "foam",
                    "row_number": 19,
                    "product_name": "Gaco 0.5 lb.",
                    "unit": "unit",
                    "unit_price": 0,
                }
            ]
        ),
        template_pricing_option_links=pd.DataFrame(
            [
                {
                    "link_id": "map_gaco_half_open_cell",
                    "template_product_option_id": "tpl_gaco_half",
                    "pricing_candidate_key": "price_open_cell",
                    "template_type": "insulation",
                    "template_bucket": "foam",
                    "row_number": 19,
                    "template_product_name": "Gaco 0.5 lb.",
                    "canonical_template_option": "Gaco 0.5 lb.",
                    "pricing_product_name": "Enverge Open Cell EasySeal",
                    "confidence": 0.98,
                    "reason": "Approved open-cell successor mapping.",
                    "review_status": "approved",
                }
            ]
        ),
        pricing_catalog=pd.DataFrame(
            [
                {
                    "pricing_item_id": "price_open_cell",
                    "product_name": "Enverge Open Cell EasySeal",
                    "product_name_normalized": "enverge open cell easyseal",
                    "unit_price": 1.6,
                    "is_current": True,
                    "status": "active",
                }
            ]
        ),
    )
    workbench = build_estimating_workbench(
        recommendation,
        data,
        scope_override={
            "estimated_sqft": 2226,
            "net_sqft": 2226,
            "net_insulation_area_sqft": 2226,
            "foam_type": "open_cell",
            "foam_thickness_inches": 3.68,
            "estimator_chat": {
                "source": "ai_chat",
                "confidence": 0.82,
                "assistant_message": "Use open-cell foam for the 2,226 sq ft metal building.",
                "workbook_decision_preferences": [
                    {
                        "decision_id": "insulation_foam_template_selector",
                        "template_bucket": "foam",
                        "include": True,
                        "proposed_values": {
                            "basis_sqft": 2226,
                            "thickness_inches": 3.68,
                            "resolved_template_option": "Gaco 0.5 lb.",
                        },
                        "confidence": 0.82,
                    }
                ],
            },
        },
    )

    workbench["insulation_foam_template_decisions"][0]["yield_or_coverage"] = 0
    workbench["insulation_foam_template_decisions"][0]["unit_price"] = 0
    recalculated = recalculate_workbench_tables(workbench, data=data)
    foam = recalculated["insulation_foam_template_decisions"][0]

    assert foam["proposal_source"] == "chat_estimator"
    assert foam["resolved_template_option"] == "Gaco 0.5 lb."
    assert foam["selected_pricing_candidate"] == "Enverge Open Cell EasySeal"
    assert foam["yield_or_coverage"] == 2600
    assert foam["unit_price"] == 1.6
    assert foam["estimated_units"] == 3150.646154
    assert foam["estimated_cost"] == 5041.03


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


def test_roofing_coating_recalculate_preserves_edited_gallons_per_100_sqft() -> None:
    workbench = {
        "scope": {"division": "Roofing", "template_type": "roofing", "project_type": "roof coating", "net_sqft": 10000},
        "source_material_plan": [
            {
                "category": "coating",
                "item": "Gaco Silicone",
                "quantity": 150,
                "unit": "gal",
                "unit_price": 32,
                "estimated_cost": 4800,
                "historical_qty_per_sqft": 0.01,
            }
        ],
        "roofing_coating_template_decisions": [
            {
                "include": True,
                "decision_id": "roofing_coating_system_row_26",
                "template_bucket": "coating",
                "workbook_row": "26",
                "editable_selector_code": "11",
                "basis_sqft": 10000,
                "gal_per_100_sqft": 1,
                "unit_price": 32,
            }
        ],
    }

    recalculated = recalculate_workbench_tables(workbench)
    coating = recalculated["roofing_coating_template_decisions"][0]

    assert coating["gal_per_100_sqft"] == 1
    assert coating["estimated_gallons"] == 100
    assert coating["estimated_cost"] == 3200


def test_roofing_caulk_recalculate_uses_edited_expected_units() -> None:
    workbench = {
        "scope": {
            "division": "Roofing",
            "template_type": "roofing",
            "project_type": "roof coating",
            "net_sqft": 10000,
            "notes": "Open seams and penetrations need sealant.",
        },
        "roofing_detail_template_decisions": [
            {
                "include": True,
                "decision_id": "roofing_caulk_sealant_row_43",
                "template_bucket": "caulk_detail",
                "workbook_row": "43",
                "editable_selector_code": "1",
                "resolved_template_option": "Silicone Sausage",
                "estimated_units": 30,
                "unit_price": 12,
            }
        ],
    }

    recalculated = recalculate_workbench_tables(workbench)
    caulk = recalculated["roofing_detail_template_decisions"][0]

    assert caulk["units"] == 30
    assert caulk["estimated_units"] == 30
    assert caulk["unit_price"] == 12
    assert caulk["estimated_cost"] == 360


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

    assert totals["material_total"] == 100.0
    assert totals["labor_total"] == 50.0
    assert totals["adder_total"] == 12.0
    assert totals["pre_markup_total"] == 162.0
    assert totals["overhead_amount"] == 0.0
    assert totals["profit_amount"] == 0.0
    assert totals["draft_total"] == 162.0


def test_pricing_markup_defaults_are_inferred_from_template_rows() -> None:
    data = EstimatorData(
        template_rows=pd.DataFrame(
            [
                {"template_type": "roofing", "template_bucket": "overhead", "row_number": 165, "overhead_pct": 35},
                {"template_type": "roofing", "template_bucket": "overhead", "row_number": 165, "overhead_pct": 33},
                {"template_type": "roofing", "template_bucket": "overhead", "row_number": 165, "overhead_pct": 37},
                {"template_type": "roofing", "template_bucket": "profit", "row_number": 167, "profit_pct": 25.5},
                {"template_type": "roofing", "template_bucket": "profit", "row_number": 167, "profit_pct": 24},
                {"template_type": "roofing", "template_bucket": "profit", "row_number": 167, "profit_pct": 27},
            ]
        )
    )

    workbench = build_estimating_workbench(roofing_recommendation(), data)
    overhead = next(row for row in workbench["pricing_markup_decisions"] if row["template_bucket"] == "overhead")
    profit = next(row for row in workbench["pricing_markup_decisions"] if row["template_bucket"] == "profit")

    assert overhead["include"] is True
    assert overhead["workbook_row"] == "165"
    assert overhead["markup_pct"] == 35
    assert overhead["historical_selector_evidence_count"] == 3
    assert profit["markup_pct"] == 25.5
    assert profit["percentage_cell"] == "F167"


def test_pricing_markup_recalculates_from_estimator_overrides() -> None:
    workbench = {
        "scope": {"division": "Roofing", "template_type": "roofing", "project_type": "roof coating", "net_sqft": 1000},
        "roofing_coating_template_decisions": [
            {
                "include": True,
                "decision_id": "roofing_coating_system_row_26",
                "template_bucket": "coating",
                "workbook_row": "26",
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
        "pricing_markup_decisions": [
            {"include": True, "decision_id": "pricing_overhead", "template_bucket": "overhead", "workbook_row": "165", "markup_pct": 10},
            {"include": True, "decision_id": "pricing_profit", "template_bucket": "profit", "workbook_row": "167", "markup_pct": 20},
        ],
    }

    recalculated = recalculate_workbench_tables(workbench)
    totals = summarize_workbench_totals(recalculated)
    draft_inputs = workbench_to_draft_workbook_inputs(recalculated)

    assert totals["pre_markup_total"] == 177
    assert totals["overhead_amount"] == 17.7
    assert totals["profit_amount"] == 38.94
    assert totals["draft_total"] == 233.64
    assert draft_inputs["pricing"]["overhead_pct"] == 10
    assert draft_inputs["pricing"]["profit_pct"] == 20


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


def test_mixed_formula_labor_keeps_daily_branch_when_daily_rate_is_present() -> None:
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

    assert labor["estimated_cost"] == 1600
    assert labor["formula_source"] == "days_daily_rate"
    assert labor["total_hours"] == 40
    assert labor["total_hours_source"] == "estimated_from_days_crew"
    assert labor["display_total_hours"] == 40


def test_mixed_formula_labor_uses_hourly_branch_when_daily_rate_is_absent() -> None:
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
                "daily_rate": 0,
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


def test_roofing_labor_template_label_survives_generic_source_task() -> None:
    formula_workbench = {
        "scope": {"division": "Roofing", "template_type": "roofing", "project_type": "roof coating", "net_sqft": 1000},
        "source_labor_plan": [
            {
                "task": "coating",
                "template_bucket": "labor_base",
                "days": 1,
                "crew_size": 4,
                "daily_rate": 1600,
                "total_hours": 40,
            }
        ],
        "roofing_labor_template_decisions": [
            {
                "include": True,
                "decision_id": "roofing_labor_base_row_122",
                "template_bucket": "labor_base",
                "workbook_row": "122",
                "days": 1,
                "crew_size": 4,
                "daily_rate": 1600,
                "total_hours": 40,
                "formula_mode": "mixed_formula",
            }
        ],
    }

    recalculated = recalculate_workbench_tables(formula_workbench)
    labor = recalculated["roofing_labor_template_decisions"][0]

    assert labor["labor_task"] == "Base Coat"
    assert labor["source_labor_task"] == "coating"


def test_roofing_labor_derives_hourly_rate_from_cost_and_hours_when_rate_missing() -> None:
    formula_workbench = {
        "scope": {"division": "Roofing", "template_type": "roofing", "project_type": "roof coating", "net_sqft": 1000},
        "source_labor_plan": [
            {
                "task": "labor_base",
                "template_bucket": "labor_base",
                "total_hours": 40,
                "estimated_cost": 2880,
            }
        ],
        "roofing_labor_template_decisions": [
            {
                "include": True,
                "decision_id": "roofing_labor_base_row_122",
                "template_bucket": "labor_base",
                "workbook_row": "122",
                "days": 1,
                "crew_size": 4,
                "daily_rate": 0,
                "hourly_rate": 0,
                "total_hours": 40,
                "formula_mode": "mixed_formula",
            }
        ],
    }

    recalculated = recalculate_workbench_tables(formula_workbench)
    labor = recalculated["roofing_labor_template_decisions"][0]

    assert labor["hourly_rate"] == 72
    assert labor["hourly_rate_source"] == "derived_from_labor_cost_and_hours"
    assert labor["estimated_cost"] == 2880
    assert labor["formula_source"] == "hours_hourly_rate"


def test_roofing_workbench_defaults_one_sales_inspection_trip() -> None:
    workbench = build_estimating_workbench(roofing_recommendation(), EstimatorData())
    trips = next(row for row in workbench["roofing_travel_freight_template_decisions"] if row["template_bucket"] == "sales_trips")

    assert trips["include"] is True
    assert trips["trip_count"] == 1
    assert trips["estimated_cost"] == 15


def test_roofing_truck_expense_recalculates_when_unit_price_changes() -> None:
    workbench = build_estimating_workbench(roofing_recommendation(), EstimatorData())
    truck = next(
        row
        for row in workbench["roofing_travel_freight_template_decisions"]
        if row["template_bucket"] == "truck_expense"
    )
    truck.update(
        {
            "include": True,
            "trip_count": 2,
            "round_trip_miles": 100,
            "unit_price": 1.0,
        }
    )

    first = recalculate_workbench_tables(workbench)
    first_truck = next(
        row
        for row in first["roofing_travel_freight_template_decisions"]
        if row["template_bucket"] == "truck_expense"
    )
    first_truck["unit_price"] = 1.25

    second = recalculate_workbench_tables(first)
    second_truck = next(
        row
        for row in second["roofing_travel_freight_template_decisions"]
        if row["template_bucket"] == "truck_expense"
    )

    assert first_truck["estimated_cost"] == 200
    assert second_truck["estimated_cost"] == 250
    assert second_truck["formula_source"] == "trips_miles_rate"


def test_review_only_primer_prices_checked_row_but_keeps_review_warning() -> None:
    formula_workbench = {
        "scope": {
            "division": "Roofing",
            "template_type": "roofing",
            "project_type": "roof coating",
            "net_sqft": 10000,
            "raw_input_notes": "Review rust-inhibitive primer need before coating.",
        },
        "roofing_primer_template_decisions": [
            {
                "include": True,
                "decision_id": "roofing_primer_system_row_39",
                "template_bucket": "primer",
                "workbook_row": "39",
                "proposal_review_required": True,
                "basis_sqft": 10000,
                "coverage_sqft_per_unit": 250,
                "unit_price": 40,
            }
        ],
    }

    recalculated = recalculate_workbench_tables(formula_workbench)
    primer = recalculated["roofing_primer_template_decisions"][0]

    assert primer["include"] is True
    assert primer["basis_sqft"] == 10000
    assert primer["unit_price"] == 40
    assert primer["estimated_cost"] == 1600
    assert primer["compatibility_status"] in {"review", "spec_mismatch"}
    assert any("verify primer scope" in warning.lower() for warning in primer["compatibility_warnings"])


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


def test_roofing_answer_key_labor_rows_118_130_134_apply_to_workbench() -> None:
    recommendation = roofing_recommendation()
    recommendation.parsed_fields["estimator_chat"] = {
        "source": "ai_chat",
        "confidence": 0.9,
        "assistant_message": "Apply the attached reference estimate answer key.",
        "workbook_decision_preferences": [
            {
                "section": "roofing_labor_template_decisions",
                "decision_id": "roofing_labor_prep_row_118",
                "template_bucket": "labor_prep",
                "workbook_row": "118",
                "include": True,
                "source": "reference_estimate_answer_key",
                "proposed_values": {
                    "days": 1.0,
                    "editable_days": 1.0,
                    "crew_size": 5,
                    "crew_people_selection": 5,
                    "daily_rate": 1667.25,
                    "total_hours": 50.0,
                },
            },
            {
                "section": "roofing_labor_template_decisions",
                "decision_id": "roofing_labor_top_coat_row_130",
                "template_bucket": "labor_top_coat",
                "workbook_row": "130",
                "include": True,
                "source": "reference_estimate_answer_key",
                "proposed_values": {
                    "days": 1.4,
                    "editable_days": 1.4,
                    "crew_size": 5,
                    "crew_people_selection": 5,
                    "daily_rate": 1667.25,
                    "total_hours": 70.0,
                },
            },
            {
                "section": "roofing_labor_template_decisions",
                "decision_id": "roofing_labor_misc_row_134",
                "template_bucket": "labor_misc",
                "workbook_row": "134",
                "include": True,
                "source": "reference_estimate_answer_key",
                "proposed_values": {
                    "days": 0.65,
                    "editable_days": 0.65,
                    "crew_size": 5,
                    "crew_people_selection": 5,
                    "daily_rate": 1667.25,
                    "total_hours": 32.5,
                },
            },
        ],
    }

    workbench = recalculate_workbench_tables(build_estimating_workbench(recommendation, EstimatorData()))
    labor = {row["template_bucket"]: row for row in workbench["roofing_labor_template_decisions"]}

    assert labor["labor_prime"]["include"] is True
    assert labor["labor_prime"]["workbook_row"] == "118"
    assert labor["labor_prime"]["estimated_cost"] == 1667.25
    assert labor["labor_top_coat_granules"]["include"] is True
    assert labor["labor_top_coat_granules"]["workbook_row"] == "130"
    assert labor["labor_top_coat_granules"]["estimated_cost"] == 2334.15
    assert labor["labor_misc"]["include"] is True
    assert labor["labor_misc"]["workbook_row"] == "134"
    assert labor["labor_misc"]["estimated_cost"] == 1083.71


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


def test_insulation_thermal_barrier_uses_formula_compatible_historical_unit_price() -> None:
    recommendation = insulation_recommendation()
    recommendation.parsed_fields["notes"] = "Spray foam insulation with DC315 thermal barrier."
    data = EstimatorData(
        template_rows=pd.DataFrame(
            [
                {
                    "template_row_id": "hist-dc315-1",
                    "job_id": "I1",
                    "template_type": "insulation",
                    "sheet_name": "Estimate",
                    "row_number": 30,
                    "template_bucket": "thermal_barrier_coating",
                    "line_item_kind": "material",
                    "selected_item_name": "DC 315 Thermal Barrier",
                    "unit": "gal",
                    "area_sqft": 2400,
                    "estimated_gallons": 24,
                    "estimated_units": 24,
                    "estimated_cost": 1440,
                }
            ]
        )
    )

    workbench = build_estimating_workbench(recommendation, data)
    thermal = workbench["insulation_thermal_barrier_template_decisions"][0]

    assert thermal["include"] is True
    assert thermal["unit_price"] == 60
    assert thermal["estimated_gallons"] == 24
    assert thermal["estimated_cost"] == 1440
    assert thermal["cost_source"] == "historical_formula_unit_price"
    assert thermal["selected_pricing_candidate"] == "DC 315 Thermal Barrier"
    assert "estimate_template_rows_formula_compatible" in thermal["pricing_evidence_summary"]
    assert any(option["item_name"] == "DC 315 Thermal Barrier" for option in json.loads(thermal["item_options_json"]))


def test_insulation_template_product_option_supplies_unit_price_before_historical_fallback() -> None:
    recommendation = insulation_recommendation()
    recommendation.parsed_fields["notes"] = "Spray foam insulation with DC315 thermal barrier."
    data = EstimatorData(
        template_product_options=pd.DataFrame(
            [
                {
                    "template_type": "insulation",
                    "row_number": 30,
                    "template_bucket": "thermal_barrier_coating",
                    "product_name": "DC 315 Current",
                    "unit": "gal",
                    "unit_price": 62,
                }
            ]
        ),
        template_rows=pd.DataFrame(
            [
                {
                    "template_row_id": "hist-dc315-1",
                    "job_id": "I1",
                    "template_type": "insulation",
                    "row_number": 30,
                    "template_bucket": "thermal_barrier_coating",
                    "line_item_kind": "material",
                    "selected_item_name": "DC 315 Historical",
                    "unit": "gal",
                    "estimated_gallons": 24,
                    "estimated_cost": 1200,
                }
            ]
        ),
    )

    workbench = build_estimating_workbench(recommendation, data)
    thermal = workbench["insulation_thermal_barrier_template_decisions"][0]

    assert thermal["unit_price"] == 62
    assert thermal["estimated_cost"] == 1488
    assert thermal["cost_source"] == "current_pricing"
    assert thermal["selected_pricing_candidate"] == "DC 315 Current"
    assert thermal["selected_price_source"] == "template_product_options"


def test_insulation_sealant_uses_opening_and_corner_linear_feet_without_duplicate_row() -> None:
    recommendation = insulation_recommendation()
    recommendation.parsed_fields.update(
        {
            "notes": "Spray foam insulation. Seal around overhead door openings and corners.",
            "wall_height_ft": 11.25,
            "openings": [
                {
                    "opening_type": "overhead_door",
                    "quantity": 2,
                    "width_ft": 10,
                    "height_ft": 10,
                    "known_area_sqft": 200,
                }
            ],
        }
    )
    data = EstimatorData(
        template_rows=pd.DataFrame(
            [
                {
                    "template_row_id": "hist-sealant-1",
                    "job_id": "I1",
                    "template_type": "insulation",
                    "row_number": 41,
                    "template_bucket": "caulk_sealant",
                    "line_item_kind": "material",
                    "selected_item_name": "Liquid Flashing",
                    "unit": "unit",
                    "linear_ft": 100,
                    "estimated_units": 10,
                    "estimated_cost": 200,
                }
            ]
        )
    )

    workbench = build_estimating_workbench(recommendation, data)
    sealant_rows = workbench["insulation_detail_material_template_decisions"]
    first = next(row for row in sealant_rows if row["workbook_row"] == "41")
    second = next(row for row in sealant_rows if row["workbook_row"] == "43")

    assert first["include"] is False
    assert first["linear_ft"] == 125
    assert first["estimated_units"] == 0
    assert first["unit_price"] == 20
    assert first["estimated_cost"] == 0
    assert first["cost_source"] == "not_included"
    assert second["include"] is False


def test_insulation_drum_disposal_rejects_polluted_plate_history() -> None:
    recommendation = insulation_recommendation()
    recommendation.parsed_fields["notes"] = "Spray foam insulation with DC315 thermal barrier."
    data = EstimatorData(
        template_rows=pd.DataFrame(
            [
                {
                    "template_row_id": "hist-dc315-1",
                    "job_id": "I1",
                    "template_type": "insulation",
                    "row_number": 30,
                    "template_bucket": "thermal_barrier_coating",
                    "line_item_kind": "material",
                    "row_label": "DC315",
                    "selected_item_name": "DC 315 TB",
                    "unit": "gal",
                    "estimated_gallons": 24,
                    "estimated_cost": 1248,
                },
                {
                    "template_row_id": "hist-drum-good-quantity",
                    "job_id": "I1",
                    "template_type": "insulation",
                    "row_number": 65,
                    "template_bucket": "drum_disposal",
                    "line_item_kind": "equipment",
                    "row_label": "Drum Disp.",
                    "estimated_units": 2,
                },
                {
                    "template_row_id": "hist-drum-polluted-price",
                    "job_id": "I2",
                    "template_type": "insulation",
                    "row_number": 65,
                    "template_bucket": "drum_disposal",
                    "line_item_kind": "equipment",
                    "row_label": "Plates",
                    "unit_price": 80,
                },
            ]
        )
    )

    workbench = build_estimating_workbench(recommendation, data)
    drum = next(row for row in workbench["insulation_support_material_template_decisions"] if row["template_bucket"] == "drum_disposal")

    assert drum["include"] is True
    assert drum["unit_price"] == 0
    assert drum["estimated_cost"] == 0
    assert drum["selected_pricing_candidate"] == "Drum Disposal"
    assert "Plates" not in drum["pricing_evidence_summary"]


def test_insulation_foam_rolls_up_r_value_thickness_and_template_yield() -> None:
    recommendation = insulation_recommendation()
    recommendation.parsed_fields.update(
        {
            "notes": "Spray outside walls and flat ceiling. Walls target R-21. Ceiling target R-30.",
            "gross_wall_area_sqft": 1855.65,
            "opening_area_known_sqft": 200,
            "ceiling_area_sqft": 1344,
            "net_insulation_area_sqft": 2999.65,
            "foam_type": "closed_cell",
            "insulation_r_value_targets": [
                {"surface_type": "walls", "target_r_value": 21},
                {"surface_type": "ceiling", "target_r_value": 30},
            ],
        }
    )
    data = EstimatorData(
        template_rows=pd.DataFrame(
            [
                {
                    "template_row_id": "hist-foam-1",
                    "job_id": "I1",
                    "template_type": "insulation",
                    "row_number": 19,
                    "template_bucket": "foam",
                    "line_item_kind": "material",
                    "selected_item_name": "GACO 2.0",
                    "unit_price": 2.25,
                    "estimated_units": 1000,
                    "estimated_cost": 2250,
                }
            ]
        ),
        product_catalog=pd.DataFrame(
            [
                {
                    "product_id": "gaco_onepass",
                    "manufacturer": "Gaco",
                    "product_name": "GacoOnePass Closed Cell Spray Foam",
                    "category": "spray_foam",
                    "active": True,
                }
            ]
        ),
        product_aliases=pd.DataFrame(
            [
                {
                    "product_id": "gaco_onepass",
                    "alias": "GACO 2.0",
                    "alias_type": "historical_template_row",
                    "confidence": 0.95,
                }
            ]
        ),
    )

    workbench = build_estimating_workbench(recommendation, data)
    foam = workbench["insulation_foam_template_decisions"][0]

    assert foam["include"] is True
    assert foam["thickness_inches"] == 4.6721
    assert foam["yield_or_coverage"] == 2600
    assert foam["estimated_units"] > 0
    assert foam["estimated_cost"] > 0
    assert foam["unit_price"] == 2.25
    assert foam["product_id"] == "gaco_onepass"
    assert foam["product_guidance_status"] == "matched"
    assert "Product guidance documents have not been ingested yet" in foam["product_guidance"]
    assert foam["thickness_source"]
    assert foam["yield_or_coverage_source"]

    workbench["insulation_foam_template_decisions"][0]["unit_price"] = 0
    recalculated = recalculate_workbench_tables(workbench)
    recalculated_foam = recalculated["insulation_foam_template_decisions"][0]

    assert recalculated_foam["unit_price"] == 2.25
    assert recalculated_foam["estimated_cost"] > 0
    assert recalculated_foam["pricing_evidence_summary"]


def test_insulation_foam_uses_thickness_matched_yield_history_before_template_default() -> None:
    recommendation = insulation_recommendation()
    recommendation.parsed_fields.update(
        {
            "notes": "Spray outside walls and ceiling with open-cell foam at R-21.",
            "net_insulation_area_sqft": 2226,
            "estimated_sqft": 2226,
            "foam_type": "open_cell",
            "foam_thickness_inches": 5.5,
        }
    )
    data = EstimatorData(
        template_rows=pd.DataFrame(
            [
                {
                    "template_row_id": "hist-open-yield-1",
                    "job_id": "I1",
                    "template_type": "insulation",
                    "row_number": 19,
                    "template_bucket": "foam",
                    "line_item_kind": "material",
                    "selected_item_name": "Gaco 0.5 lb.",
                    "area_sqft": 2200,
                    "thickness_inches": 5.5,
                    "yield_or_coverage": 4500,
                    "estimated_units": 2688.8889,
                    "unit_price": 1.9,
                },
                {
                    "template_row_id": "hist-closed-yield-1",
                    "job_id": "I2",
                    "template_type": "insulation",
                    "row_number": 21,
                    "template_bucket": "foam",
                    "line_item_kind": "material",
                    "selected_item_name": "Gaco 2.0 lb.",
                    "area_sqft": 2200,
                    "thickness_inches": 2,
                    "yield_or_coverage": 2600,
                    "estimated_units": 1692.3077,
                    "unit_price": 2.25,
                },
            ]
        )
    )

    workbench = build_estimating_workbench(recommendation, data)
    foam = workbench["insulation_foam_template_decisions"][0]

    assert foam["resolved_template_option"] == "Gaco 0.5 lb."
    assert foam["yield_or_coverage"] == 4500
    assert foam["yield_or_coverage_source"] == "historical_yield_by_scope"
    assert foam["unit_price"] == 1.9
    assert foam["unit_price_source"] in {"estimate_template_rows_formula_compatible", "historical_foam_yield_history"}
    assert foam["cost_source"] in {"historical_formula_unit_price", "historical_foam_yield_unit_price"}
    assert foam["yield_history_evidence_count"] == 1
    assert foam["estimated_units"] == 2720.666667
    assert foam["estimated_cost"] > 0


def test_insulation_foam_recalc_treats_persisted_zero_thickness_and_yield_as_missing() -> None:
    recommendation = insulation_recommendation()
    recommendation.parsed_fields.update(
        {
            "notes": "Spray outside walls and flat ceiling. Walls target R-21. Ceiling target R-30.",
            "gross_wall_area_sqft": 1855.65,
            "opening_area_known_sqft": 200,
            "ceiling_area_sqft": 1344,
            "net_insulation_area_sqft": 2999.65,
            "foam_type": "closed_cell",
            "insulation_r_value_targets": [
                {"surface_type": "walls", "target_r_value": 21},
                {"surface_type": "ceiling", "target_r_value": 30},
            ],
        }
    )
    workbench = build_estimating_workbench(recommendation, EstimatorData())
    workbench["insulation_foam_template_decisions"][0]["thickness_inches"] = 0
    workbench["insulation_foam_template_decisions"][0]["yield_or_coverage"] = 0
    workbench["insulation_foam_template_decisions"][0]["unit_price"] = 2.25

    recalculated = recalculate_workbench_tables(workbench)
    foam = recalculated["insulation_foam_template_decisions"][0]

    assert foam["thickness_inches"] == 4.6721
    assert foam["yield_or_coverage"] == 2600
    assert foam["estimated_cost"] > 0


def test_insulation_foam_recalc_marks_stored_weak_product_match_for_review() -> None:
    recommendation = insulation_recommendation()
    workbench = build_estimating_workbench(recommendation, EstimatorData())
    candidate = {
        "item_name": "GACO 2.0",
        "unit_price": 2.25,
        "source": "estimate_template_rows_formula_compatible",
        "compatibility_status": "compatible",
        "product_id": "weak_roofing_product",
        "product_name": "Weak Roofing Product",
        "product_match_score": 0.57,
        "product_match_strategy": "fuzzy_product_name",
        "product_guidance": "Roof coating guidance that should not be trusted for foam.",
    }
    foam = workbench["insulation_foam_template_decisions"][0]
    foam["selected_pricing_candidate"] = "GACO 2.0"
    foam["unit_price"] = 0
    foam["pricing_candidates"] = [candidate]
    foam["pricing_candidates_json"] = json.dumps([candidate])

    recalculated = recalculate_workbench_tables(workbench)
    recalculated_foam = recalculated["insulation_foam_template_decisions"][0]

    assert recalculated_foam["unit_price"] == 2.25
    assert recalculated_foam["product_guidance_status"] == "review"
    assert "Weak product match" in recalculated_foam["product_guidance"]
    assert any("weak product match" in warning.lower() for warning in recalculated_foam["compatibility_warnings"])


def test_insulation_foam_prefers_current_family_pricing_over_unrelated_history() -> None:
    recommendation = insulation_recommendation()
    recommendation.parsed_fields.update(
        {
            "notes": "Spray outside walls and flat ceiling. Walls target R-21. Ceiling target R-30.",
            "gross_wall_area_sqft": 1855.65,
            "opening_area_known_sqft": 200,
            "ceiling_area_sqft": 1344,
            "net_insulation_area_sqft": 2999.65,
            "foam_type": "closed_cell",
            "insulation_r_value_targets": [
                {"surface_type": "walls", "target_r_value": 21},
                {"surface_type": "ceiling", "target_r_value": 30},
            ],
        }
    )
    data = EstimatorData(
        pricing_catalog=pd.DataFrame(
            [
                {
                    "pricing_item_id": "P-ONEPASS",
                    "product_name": "Enverge Closed Cell OnePass",
                    "category": "Foam",
                    "unit_price": 2.05,
                    "status": "active",
                    "is_current": True,
                    "needs_review": False,
                }
            ]
        ),
        template_rows=pd.DataFrame(
            [
                {
                    "template_row_id": "hist-profill",
                    "job_id": "I1",
                    "template_type": "insulation",
                    "row_number": 19,
                    "template_bucket": "foam",
                    "line_item_kind": "material",
                    "selected_item_name": "ProFill",
                    "unit_price": 2.0,
                    "estimated_units": 1000,
                    "estimated_cost": 2000,
                }
            ]
        ),
    )

    workbench = build_estimating_workbench(recommendation, data)
    foam = workbench["insulation_foam_template_decisions"][0]

    assert foam["resolved_template_option"] == "Gaco 2.0 lb."
    assert foam["selected_pricing_candidate"] == "Enverge Closed Cell OnePass"
    assert foam["unit_price"] == 2.05
    assert foam["cost_source"] == "current_pricing"
    assert foam["product_guidance_status"] == "mapped"
    assert "Mapped product family" in foam["product_guidance"]


def test_insulation_loading_travel_scan_and_lodging_are_logistics_expense_rows() -> None:
    recommendation = insulation_recommendation()
    recommendation.parsed_fields["notes"] = "Spray foam insulation. Include loading, travel, infrared scan, and lodging."
    data = EstimatorData(
        estimator_decision_recommendations=pd.DataFrame(
            [
                {
                    "decision_id": "insulation_labor_loading",
                    "field_name": "hours_per_day",
                    "recommended_value": 0.5,
                    "evidence_count": 4,
                    "source_jobs_count": 4,
                    "confidence": "medium",
                    "history_table": "test",
                },
                {
                    "decision_id": "insulation_labor_loading",
                    "field_name": "crew_size",
                    "recommended_value": 1,
                    "evidence_count": 4,
                    "source_jobs_count": 4,
                    "confidence": "medium",
                    "history_table": "test",
                },
                {
                    "decision_id": "insulation_labor_loading",
                    "field_name": "unit_price",
                    "recommended_value": 25.5,
                    "evidence_count": 4,
                    "source_jobs_count": 4,
                    "confidence": "medium",
                    "history_table": "test",
                },
                {
                    "decision_id": "insulation_labor_traveling",
                    "field_name": "days",
                    "recommended_value": 2.5,
                    "evidence_count": 4,
                    "source_jobs_count": 4,
                    "confidence": "medium",
                    "history_table": "test",
                },
                {
                    "decision_id": "insulation_labor_traveling",
                    "field_name": "crew_size",
                    "recommended_value": 4,
                    "evidence_count": 4,
                    "source_jobs_count": 4,
                    "confidence": "medium",
                    "history_table": "test",
                },
                {
                    "decision_id": "insulation_labor_traveling",
                    "field_name": "unit_price",
                    "recommended_value": 13,
                    "evidence_count": 4,
                    "source_jobs_count": 4,
                    "confidence": "medium",
                    "history_table": "test",
                },
                {
                    "decision_id": "insulation_infrared_scan",
                    "field_name": "hours",
                    "recommended_value": 2,
                    "evidence_count": 4,
                    "source_jobs_count": 4,
                    "confidence": "medium",
                    "history_table": "test",
                },
                {
                    "decision_id": "insulation_infrared_scan",
                    "field_name": "unit_price",
                    "recommended_value": 75,
                    "evidence_count": 4,
                    "source_jobs_count": 4,
                    "confidence": "medium",
                    "history_table": "test",
                },
                {
                    "decision_id": "insulation_meals_lodging",
                    "field_name": "days",
                    "recommended_value": 2,
                    "evidence_count": 4,
                    "source_jobs_count": 4,
                    "confidence": "medium",
                    "history_table": "test",
                },
                {
                    "decision_id": "insulation_meals_lodging",
                    "field_name": "crew_size",
                    "recommended_value": 2,
                    "evidence_count": 4,
                    "source_jobs_count": 4,
                    "confidence": "medium",
                    "history_table": "test",
                },
                {
                    "decision_id": "insulation_meals_lodging",
                    "field_name": "unit_price",
                    "recommended_value": 125,
                    "evidence_count": 4,
                    "source_jobs_count": 4,
                    "confidence": "medium",
                    "history_table": "test",
                },
            ]
        )
    )

    workbench = build_estimating_workbench(recommendation, data)
    logistics = {
        row["template_bucket"]: row
        for row in workbench["insulation_logistics_expense_template_decisions"]
    }

    assert "labor_loading" not in {row["template_bucket"] for row in workbench["insulation_labor_template_decisions"]}
    assert logistics["labor_loading"]["estimated_cost"] == 12.75
    assert logistics["labor_loading"]["formula_model"] == "insulation_hours_people_rate_trip_count"
    assert logistics["labor_traveling"]["estimated_cost"] == 130
    assert logistics["infrared_scan"]["estimated_cost"] == 150
    assert logistics["infrared_scan"]["formula_model"] == "insulation_hours_rate_cost"
    assert logistics["meals_lodging"]["estimated_cost"] == 500
    assert logistics["meals_lodging"]["formula_model"] == "insulation_days_people_rate_cost"
    assert {"hours_per_day", "people_count", "unit_price", "estimated_cost"}.issubset(
        {entry["field"] for entry in logistics["labor_traveling"]["workbook_cell_write_preview"]}
    )


def test_insulation_spray_foam_defaults_truck_sales_and_generator_with_review_if_miles_missing() -> None:
    recommendation = insulation_recommendation()
    recommendation.parsed_fields["notes"] = "Spray foam insulation. Include setup, loading, and travel."

    workbench = build_estimating_workbench(recommendation, EstimatorData())
    equipment = {
        row["template_bucket"]: row
        for row in workbench["insulation_equipment_logistics_template_decisions"]
    }
    sales = equipment["sales_inspection_trips"]
    truck = next(
        row
        for row in workbench["insulation_equipment_logistics_template_decisions"]
        if row["template_bucket"] == "truck_expense"
    )
    traveling = next(
        row
        for row in workbench["insulation_logistics_expense_template_decisions"]
        if row["template_bucket"] == "labor_traveling"
    )

    assert traveling["include"] is True
    assert traveling["unit_price"] == 13
    assert traveling["estimated_cost"] > 0
    assert equipment["generator"]["include"] is True
    assert equipment["generator"]["days"] >= 1
    assert equipment["generator"]["unit_price"] == 40
    assert equipment["generator"]["estimated_cost"] > 0
    assert sales["include"] is True
    assert sales["trip_count"] >= 1
    assert sales["unit_price"] == 0.75
    assert truck["include"] is True
    assert truck["trip_count"] >= 1
    assert truck["unit_price"] == 0.75
    assert truck["estimated_cost"] == 0


def test_insulation_spray_foam_travel_uses_round_trip_miles_from_address() -> None:
    recommendation = insulation_recommendation()
    recommendation.parsed_fields["address"] = "Cincinnati, OH"

    workbench = build_estimating_workbench(recommendation, EstimatorData())
    equipment = {
        row["template_bucket"]: row
        for row in workbench["insulation_equipment_logistics_template_decisions"]
    }

    assert equipment["sales_inspection_trips"]["round_trip_miles"] == 200
    assert equipment["truck_expense"]["round_trip_miles"] == 200
    assert equipment["truck_expense"]["estimated_cost"] > 0


def test_insulation_spray_foam_travel_can_use_route_miles_from_site_address(monkeypatch) -> None:
    import jobscan.estimator.workbench as workbench_module

    def fake_estimate_one_way_miles(scope):
        assert scope["destination_address"] == "314 E Aberdeen Drive, Trenton, OH"
        return 86.2

    monkeypatch.setattr(workbench_module, "estimate_one_way_miles", fake_estimate_one_way_miles)
    recommendation = insulation_recommendation()
    recommendation.parsed_fields["site_address"] = "314 E Aberdeen Drive, Trenton, OH"

    workbench = build_estimating_workbench(recommendation, EstimatorData())
    equipment = {
        row["template_bucket"]: row
        for row in workbench["insulation_equipment_logistics_template_decisions"]
    }

    assert equipment["sales_inspection_trips"]["round_trip_miles"] == 172.4
    assert equipment["truck_expense"]["round_trip_miles"] == 172.4


def test_insulation_logistics_recalc_replaces_stale_daily_rate_sized_unit_prices() -> None:
    workbench = build_estimating_workbench(insulation_recommendation(), EstimatorData())
    for row in workbench["insulation_logistics_expense_template_decisions"]:
        if row["template_bucket"] in {"labor_loading", "labor_traveling"}:
            row["unit_price"] = 1685.775

    recalculated = recalculate_workbench_tables(workbench)
    rows = {row["template_bucket"]: row for row in recalculated["insulation_logistics_expense_template_decisions"]}

    assert rows["labor_loading"]["unit_price"] == 25.5
    assert rows["labor_traveling"]["unit_price"] == 13
    assert rows["labor_loading"]["estimated_cost"] < 1000
    assert rows["labor_traveling"]["estimated_cost"] < 1000


def test_insulation_logistics_recalc_caps_bad_chat_hours_and_rates() -> None:
    workbench = build_estimating_workbench(insulation_recommendation(), EstimatorData())
    for row in workbench["insulation_logistics_expense_template_decisions"]:
        if row["template_bucket"] in {"labor_loading", "labor_traveling"}:
            row["include"] = True
            row["include_source"] = "chat_estimator"
            row["proposal_source"] = "chat_estimator"
            row["hours_per_day"] = 8
            row["people_count"] = 2
            row["unit_price"] = 50

    recalculated = recalculate_workbench_tables(workbench)
    rows = {row["template_bucket"]: row for row in recalculated["insulation_logistics_expense_template_decisions"]}

    assert rows["labor_loading"]["hours_per_day"] == 0.5
    assert rows["labor_loading"]["people_count"] == 2
    assert rows["labor_loading"]["unit_price"] == 25.5
    assert rows["labor_loading"]["estimated_cost"] == 25.5
    assert rows["labor_traveling"]["hours_per_day"] == 2.5
    assert rows["labor_traveling"]["people_count"] == 2
    assert rows["labor_traveling"]["unit_price"] == 13
    assert rows["labor_traveling"]["estimated_cost"] == 65


def test_insulation_mask_labor_included_when_openings_need_masking() -> None:
    recommendation = insulation_recommendation()
    recommendation.parsed_fields.update(
        {
            "opening_area_known_sqft": 234,
            "openings": [
                {"quantity": 2, "width_ft": 9, "height_ft": 9, "opening_type": "rollup door"},
                {"quantity": 5, "width_ft": 2, "height_ft": 3, "opening_type": "window"},
            ],
        }
    )

    workbench = build_estimating_workbench(recommendation, EstimatorData())
    mask = next(row for row in workbench["insulation_labor_template_decisions"] if row["template_bucket"] == "labor_mask")

    assert mask["include"] is True


def test_insulation_foam_missing_type_and_thickness_is_review_required_not_roof_default() -> None:
    recommendation = insulation_recommendation()
    recommendation.parsed_fields.pop("foam_type", None)
    recommendation.parsed_fields["notes"] = "Spray foam insulation for apartment walls."

    workbench = build_estimating_workbench(recommendation, EstimatorData())
    foam = workbench["insulation_foam_template_decisions"][0]

    assert foam["yield_or_coverage"] == 2600
    assert foam["yield_or_coverage_source"] == "template_default"
    assert foam["unit_price"] == 2.25
    assert foam["unit_price_source"] == "template_default"
    assert foam["estimated_cost"] == 0
    assert foam["compatibility_status"] == "review"
    assert any("Foam type is not evidenced" in warning for warning in foam["compatibility_warnings"])


def test_insulation_loading_travel_default_include_recovers_from_stale_auto_uncheck() -> None:
    workbench = build_estimating_workbench(insulation_recommendation(), EstimatorData())
    loading = next(
        row
        for row in workbench["insulation_logistics_expense_template_decisions"]
        if row["template_bucket"] == "labor_loading"
    )
    traveling = next(
        row
        for row in workbench["insulation_logistics_expense_template_decisions"]
        if row["template_bucket"] == "labor_traveling"
    )
    loading["include"] = False
    loading["formula_source"] = "not_included"
    traveling["include"] = False
    traveling["formula_source"] = "not_included"

    recalculated = recalculate_workbench_tables(workbench)
    rows = {row["template_bucket"]: row for row in recalculated["insulation_logistics_expense_template_decisions"]}

    assert rows["labor_loading"]["include"] is True
    assert rows["labor_traveling"]["include"] is True
    assert rows["labor_loading"]["formula_source"] == "hours_people_rate_trip_count"
    assert rows["labor_traveling"]["formula_source"] == "hours_people_rate_trip_count"


def test_insulation_loading_travel_manual_uncheck_is_preserved() -> None:
    workbench = build_estimating_workbench(insulation_recommendation(), EstimatorData())
    for row in workbench["insulation_logistics_expense_template_decisions"]:
        if row["template_bucket"] in {"labor_loading", "labor_traveling"}:
            row["include"] = False
            row["manual_override"] = True
            row["include_source"] = "estimator_edit"

    recalculated = recalculate_workbench_tables(workbench)
    rows = {row["template_bucket"]: row for row in recalculated["insulation_logistics_expense_template_decisions"]}

    assert rows["labor_loading"]["include"] is False
    assert rows["labor_traveling"]["include"] is False
    assert rows["labor_loading"]["manual_override"] is True
    assert rows["labor_traveling"]["manual_override"] is True


def test_insulation_stale_markup_proposal_unchecked_without_percentage_basis() -> None:
    recommendation = insulation_recommendation()
    recommendation.parsed_fields["notes"] = "Spray foam insulation. Deduct two overhead door openings."
    workbench = build_estimating_workbench(recommendation, EstimatorData())
    workbench["decision_proposals"].append(
        {
                "decision_id": "pricing_overhead",
            "template_type": "insulation",
            "template_bucket": "overhead",
            "workbook_row": "118",
                "section": "pricing_markup_decisions",
            "include": True,
            "proposed_values": {},
            "confidence": 0.7,
            "review_required": False,
            "review_reasons": [],
            "evidence": {},
            "source": "deterministic_rule",
        }
    )

    recalculated = recalculate_workbench_tables(workbench)
    overhead = next(row for row in recalculated["pricing_markup_decisions"] if row["template_bucket"] == "overhead")

    assert overhead["include"] is False
    assert overhead["include_source"] == "calculation_basis_guard"
    assert overhead["estimated_cost"] == 0
    assert overhead["formula_source"] == "not_included"


def test_insulation_foam_driver_hours_use_default_hourly_rate_when_hourly_missing() -> None:
    data = insulation_labor_driver_data()
    data.template_rows.loc[data.template_rows["template_bucket"].eq("labor_foam"), "hourly_rate"] = None

    workbench = build_estimating_workbench(insulation_recommendation(), data)
    labor = next(row for row in workbench["insulation_labor_template_decisions"] if row["template_bucket"] == "labor_foam")

    assert labor["total_hours"] > 0
    assert labor["hourly_rate"] == 72
    assert labor["estimated_cost"] > 0
    assert labor["formula_source"] == "hours_hourly_rate"


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
