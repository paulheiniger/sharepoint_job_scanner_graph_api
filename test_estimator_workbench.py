from __future__ import annotations

import pandas as pd

from jobscan.estimator.schemas import EstimateRecommendation, EstimatorData
from jobscan.estimator.workbench import (
    apply_historical_filter_update,
    build_edit_history_rows,
    build_estimating_workbench,
    recalculate_workbench_tables,
    workbench_to_draft_workbook_inputs,
)


def sample_recommendation() -> EstimateRecommendation:
    return EstimateRecommendation(
        parsed_fields={
            "run_id": "test-workbench",
            "project_type": "roof coating",
            "substrate": "metal",
            "estimated_sqft": 10000,
            "gross_area_sqft": 10000,
            "deduction_area_sqft": 0,
            "warranty_target_years": 10,
            "coating_type": "silicone",
            "roof_condition": "fair",
            "access_complexity": "low",
            "penetrations_complexity": "low",
        },
        recommended_scope=[],
        material_plan=[{"category": "coating", "included_in_total": True, "estimated_cost": 1}],
        labor_plan=[{"task": "labor_base", "included_in_total": True, "total_hours": 40}],
        travel_plan={},
        historical_calibration={},
        similar_examples=[],
        estimate_low=0,
        estimate_target=0,
        estimate_high=0,
        review_flags=[],
        human_review_required=False,
        draft_workbook_inputs={"header": {"C12_estimated_sqft": 10000}},
    )


def sample_data() -> EstimatorData:
    return EstimatorData(
        relationship_material_qty_ratios=pd.DataFrame(
            [
                {
                    "division": "Roofing",
                    "template_type": "roofing",
                    "project_type": "roof coating",
                    "substrate": "metal",
                    "coating_type": "silicone",
                    "warranty_years": 10,
                    "package": "coating",
                    "unit": "gal",
                    "median_qty_per_sqft": None,
                    "evidence_count": 6,
                    "confidence": "high",
                },
                {
                    "division": "Roofing",
                    "template_type": "roofing",
                    "package": "primer",
                    "unit": "pail",
                    "median_qty_per_sqft": None,
                    "evidence_count": 2,
                    "confidence": "low",
                },
            ]
        ),
        relationship_labor_rates=pd.DataFrame(
            [
                {
                    "division": "Roofing",
                    "template_type": "roofing",
                    "project_type": "roof coating",
                    "substrate": "metal",
                    "coating_type": "silicone",
                    "warranty_years": 10,
                    "package": "labor_base",
                    "labor_package": "labor_base",
                    "median_hours_per_1000_sqft": 4.5,
                    "median_crew_size": 4,
                    "evidence_count": 9,
                    "confidence": "medium",
                }
            ]
        ),
        pricing_catalog=pd.DataFrame(
            [
                {
                    "pricing_item_id": "P1",
                    "product_name": "GAF High Solids Silicone 55 Gal - Standard Colors",
                    "category": "Coating",
                    "price_per_gallon": 38,
                    "unit_price": 190,
                    "unit_of_measure": "gal",
                    "price_basis": "gal",
                    "is_current": True,
                },
                {
                    "pricing_item_id": "P2",
                    "product_name": "Premium Silicone Coating 55 Gal",
                    "category": "Coating",
                    "price_per_gallon": 44,
                    "unit_price": 220,
                    "unit_of_measure": "gal",
                    "price_basis": "gal",
                    "is_current": True,
                },
                {
                    "pricing_item_id": "P3",
                    "product_name": "Epoxy Primer 5 Gal - Clear/Black",
                    "category": "Primer",
                    "unit_price": 275,
                    "unit_of_measure": "pail",
                    "is_current": True,
                }
            ]
        ),
        job_package_summary=pd.DataFrame(
            [
                {
                    "job_id": "J1",
                    "division": "Roofing",
                    "template_type": "roofing",
                    "project_type": "roof coating",
                    "substrate": "metal",
                    "coating_type": "silicone",
                    "warranty_years": 10,
                    "package": "coating",
                    "item_name": "GAF High Solids Silicone 55 Gal - Standard Colors",
                    "area_sqft": 10000,
                    "total_quantity": 180,
                    "unit": "gal",
                    "qty_per_sqft": 0.018,
                    "has_physical_quantity": True,
                },
                {
                    "job_id": "J2",
                    "division": "Roofing",
                    "template_type": "roofing",
                    "project_type": "roof coating",
                    "substrate": "metal",
                    "coating_type": "silicone",
                    "warranty_years": 10,
                    "package": "coating",
                    "item_name": "GAF High Solids Silicone 55 Gal - Standard Colors",
                    "area_sqft": 10000,
                    "total_quantity": 220,
                    "unit": "gal",
                    "qty_per_sqft": 0.022,
                    "has_physical_quantity": True,
                },
                {
                    "job_id": "J8",
                    "division": "Roofing",
                    "template_type": "roofing",
                    "project_type": "roof coating",
                    "substrate": "metal",
                    "coating_type": "silicone",
                    "warranty_years": 10,
                    "package": "coating",
                    "item_name": "GAF High Solids Silicone 55 Gal - Standard Colors",
                    "area_sqft": 10000,
                    "total_quantity": 200,
                    "unit": "gal",
                    "qty_per_sqft": 0.02,
                    "has_physical_quantity": True,
                },
                {
                    "job_id": "J2",
                    "division": "Roofing",
                    "template_type": "roofing",
                    "project_type": "roof coating",
                    "substrate": "metal",
                    "coating_type": "silicone",
                    "warranty_years": 10,
                    "package": "primer",
                    "item_name": "Epoxy Primer 5 Gal - Clear/Black",
                    "area_sqft": 10000,
                    "total_quantity": 10,
                    "unit": "pail",
                    "qty_per_sqft": 0.001,
                    "total_cost": 2500,
                    "cost_per_sqft": 0.25,
                    "has_physical_quantity": True,
                },
                {
                    "job_id": "J3",
                    "division": "Roofing",
                    "template_type": "roofing",
                    "project_type": "roof coating",
                    "substrate": "metal",
                    "coating_type": "silicone",
                    "warranty_years": 10,
                    "package": "seam_treatment",
                    "item_name": "6 inch seam fabric and sealant",
                    "area_sqft": 9000,
                    "total_quantity": None,
                    "unit": "",
                    "qty_per_sqft": None,
                    "total_cost": 2700,
                    "cost_per_sqft": 0.3,
                    "has_physical_quantity": False,
                },
                {
                    "job_id": "J4",
                    "division": "Roofing",
                    "template_type": "roofing",
                    "project_type": "roof coating",
                    "substrate": "metal",
                    "coating_type": "silicone",
                    "warranty_years": 10,
                    "package": "fasteners",
                    "item_name": "Fastener treatment",
                    "area_sqft": 8000,
                    "total_quantity": 400,
                    "unit": "ea",
                    "qty_per_sqft": 0.05,
                    "total_cost": 1600,
                    "cost_per_sqft": 0.2,
                    "has_physical_quantity": True,
                },
                {
                    "job_id": "J5",
                    "division": "Roofing",
                    "template_type": "roofing",
                    "project_type": "roof coating",
                    "substrate": "metal",
                    "coating_type": "silicone",
                    "warranty_years": 10,
                    "package": "caulk_sealant",
                    "item_name": "Aldo 399 Caulk 16/case",
                    "area_sqft": 10000,
                    "total_quantity": 20,
                    "unit": "unit",
                    "qty_per_sqft": 0.002,
                    "total_cost": 2200,
                    "cost_per_sqft": 0.22,
                    "has_physical_quantity": True,
                },
                {
                    "job_id": "F1",
                    "division": "Flooring",
                    "template_type": "flooring",
                    "project_type": "floor system",
                    "substrate": "concrete",
                    "package": "coating",
                    "item_name": "Floor Coating",
                    "area_sqft": 10000,
                    "total_quantity": 999,
                    "unit": "gal",
                    "qty_per_sqft": 0.0999,
                    "has_physical_quantity": True,
                },
                {
                    "job_id": "L1",
                    "division": "Roofing",
                    "template_type": "roofing",
                    "project_type": "roof coating",
                    "substrate": "metal",
                    "coating_type": "silicone",
                    "warranty_years": 10,
                    "package": "labor_base",
                    "area_sqft": 10000,
                    "total_hours": 45,
                    "hours_per_sqft": 0.0045,
                    "crew_size": 4,
                },
                {
                    "job_id": "L2",
                    "division": "Roofing",
                    "template_type": "roofing",
                    "project_type": "roof coating",
                    "substrate": "metal",
                    "coating_type": "silicone",
                    "warranty_years": 10,
                    "package": "labor_prep",
                    "area_sqft": 10000,
                    "total_hours": 30,
                    "hours_per_sqft": 0.003,
                    "crew_size": 4,
                },
                {
                    "job_id": "L5",
                    "division": "Roofing",
                    "template_type": "roofing",
                    "project_type": "roof coating",
                    "substrate": "metal",
                    "coating_type": "silicone",
                    "warranty_years": 10,
                    "package": "labor_base",
                    "area_sqft": 10000,
                    "total_hours": 40,
                    "hours_per_sqft": 0.004,
                    "crew_size": 4,
                },
                {
                    "job_id": "L6",
                    "division": "Roofing",
                    "template_type": "roofing",
                    "project_type": "roof coating",
                    "substrate": "metal",
                    "coating_type": "silicone",
                    "warranty_years": 10,
                    "package": "labor_base",
                    "area_sqft": 10000,
                    "total_hours": 50,
                    "hours_per_sqft": 0.005,
                    "crew_size": 4,
                },
                {
                    "job_id": "A1",
                    "division": "Roofing",
                    "template_type": "roofing",
                    "project_type": "roof coating",
                    "substrate": "metal",
                    "package": "lift",
                    "area_sqft": 10000,
                    "total_cost": 1500,
                    "cost_per_sqft": 0.15,
                    "has_allowance": True,
                },
                {
                    "job_id": "A2",
                    "division": "Roofing",
                    "template_type": "roofing",
                    "project_type": "roof coating",
                    "substrate": "metal",
                    "package": "generator",
                    "area_sqft": 12000,
                    "total_cost": 900,
                    "cost_per_sqft": 0.075,
                    "has_allowance": True,
                },
                {
                    "job_id": "J6",
                    "division": "Roofing",
                    "template_type": "roofing",
                    "project_type": "roof coating",
                    "substrate": "membrane",
                    "coating_type": "silicone",
                    "warranty_years": 10,
                    "package": "coating",
                    "item_name": "Premium Silicone Coating 55 Gal",
                    "area_sqft": 20000,
                    "total_quantity": 800,
                    "unit": "gal",
                    "qty_per_sqft": 0.04,
                    "has_physical_quantity": True,
                },
                {
                    "job_id": "J7",
                    "division": "Roofing",
                    "template_type": "roofing",
                    "project_type": "roof coating",
                    "substrate": "membrane",
                    "coating_type": "silicone",
                    "warranty_years": 10,
                    "package": "coating",
                    "item_name": "Premium Silicone Coating 55 Gal",
                    "area_sqft": 20000,
                    "total_quantity": 1200,
                    "unit": "gal",
                    "qty_per_sqft": 0.06,
                    "has_physical_quantity": True,
                },
                {
                    "job_id": "L3",
                    "division": "Roofing",
                    "template_type": "roofing",
                    "project_type": "roof coating",
                    "substrate": "membrane",
                    "coating_type": "silicone",
                    "warranty_years": 10,
                    "package": "labor_base",
                    "area_sqft": 20000,
                    "total_hours": 240,
                    "hours_per_sqft": 0.012,
                    "crew_size": 5,
                },
                {
                    "job_id": "L4",
                    "division": "Roofing",
                    "template_type": "roofing",
                    "project_type": "roof coating",
                    "substrate": "membrane",
                    "coating_type": "silicone",
                    "warranty_years": 10,
                    "package": "labor_base",
                    "area_sqft": 20000,
                    "total_hours": 280,
                    "hours_per_sqft": 0.014,
                    "crew_size": 5,
                },
                {
                    "job_id": "LT1",
                    "division": "Roofing",
                    "template_type": "roofing",
                    "project_type": "roof coating",
                    "substrate": "metal",
                    "coating_type": "silicone",
                    "warranty_years": 10,
                    "package": "labor_top_coat",
                    "area_sqft": 10000,
                    "total_hours": 1,
                    "hours_per_sqft": 0.0001,
                    "crew_size": 4,
                },
                {
                    "job_id": "LT2",
                    "division": "Roofing",
                    "template_type": "roofing",
                    "project_type": "roof coating",
                    "substrate": "metal",
                    "coating_type": "silicone",
                    "warranty_years": 10,
                    "package": "labor_top_coat",
                    "area_sqft": 10000,
                    "total_hours": 100,
                    "hours_per_sqft": 0.01,
                    "crew_size": 4,
                },
                {
                    "job_id": "LT3",
                    "division": "Roofing",
                    "template_type": "roofing",
                    "project_type": "roof coating",
                    "substrate": "metal",
                    "coating_type": "silicone",
                    "warranty_years": 10,
                    "package": "labor_top_coat",
                    "area_sqft": 10000,
                    "total_hours": 500,
                    "hours_per_sqft": 0.05,
                    "crew_size": 4,
                },
            ]
        ),
    )


def test_workbench_populates_common_editable_rows_from_relationship_tables() -> None:
    workbench = build_estimating_workbench(sample_recommendation(), sample_data())

    material_packages = {row["package_key"]: row for row in workbench["materials"]}
    labor_packages = {row["package_key"]: row for row in workbench["labor"]}

    assert {"coating", "primer", "seam_treatment", "fastener_treatment", "caulk_detail"}.issubset(material_packages)
    assert material_packages["coating"]["include"] is True
    assert material_packages["coating"]["suggested_by_notes_rules"] == "yes"
    assert material_packages["coating"]["historical_usage_rate"] > 0
    assert material_packages["coating"]["historical_qty_per_sqft"] == 0.02
    assert material_packages["coating"]["calculated_quantity"] == 200
    assert material_packages["coating"]["estimated_cost"] == 7600
    assert material_packages["coating"]["source"] == "job_package_summary_filtered"
    assert material_packages["coating"]["historical_jobs_found"] == 6
    assert material_packages["coating"]["rows_accepted"] == 3
    assert "division_not_roofing" in material_packages["coating"]["rejection_reasons"]
    assert material_packages["primer"]["include"] is False
    assert material_packages["primer"]["editable_qty_per_sqft"] == 0.001
    assert material_packages["primer"]["calculated_quantity"] == 0
    assert "Used in 1 historical Roofing jobs" in material_packages["primer"]["explanation"]
    assert "Shown unchecked. Historical default is prefilled" in material_packages["primer"]["explanation"]
    assert "Shown but unchecked" in material_packages["primer"]["explanation"]
    assert labor_packages["labor_base"]["include"] is True
    assert labor_packages["labor_base"]["suggested_by_notes_rules"] == "yes"
    assert labor_packages["labor_base"]["historical_hours_per_1000_sqft"] == 4.5
    assert labor_packages["labor_base"]["source"] == "job_package_summary_full_corpus"
    assert "Used in 3 historical Roofing jobs" in labor_packages["labor_base"]["explanation"]
    assert labor_packages["labor_prep"]["include"] is False
    assert labor_packages["labor_prep"]["historical_hours_per_1000_sqft"] == 3
    assert labor_packages["labor_prep"]["editable_hours_per_1000_sqft"] == 3
    assert not any("AI estimated" in str(row) or "AI chose" in str(row) or "Automatically determined" in str(row) for row in workbench["materials"] + workbench["labor"])


def test_historical_filter_calculation_updates_material_and_labor_defaults() -> None:
    filtered = build_estimating_workbench(
        sample_recommendation(),
        sample_data(),
        historical_filters={
            "division": "Roofing",
            "template_type": "roofing",
            "project_type": "roof coating",
            "substrate": "membrane",
            "area_bucket": "15k_50k",
            "min_evidence_count": 1,
        },
    )

    materials = {row["package_key"]: row for row in filtered["materials"]}
    labor = {row["package_key"]: row for row in filtered["labor"]}

    assert materials["coating"]["historical_qty_per_sqft"] == 0.05
    assert materials["coating"]["editable_qty_per_sqft"] == 0.05
    assert labor["labor_base"]["historical_hours_per_1000_sqft"] == 13
    assert labor["labor_base"]["editable_hours_per_1000_sqft"] == 13
    assert "substrate=membrane" in materials["coating"]["filters_applied"]


def test_material_row_preserves_actual_item_name_from_current_pricing() -> None:
    workbench = build_estimating_workbench(sample_recommendation(), sample_data())
    coating = next(row for row in workbench["materials"] if row["package_key"] == "coating")

    assert coating["item_name"] == "GAF High Solids Silicone 55 Gal - Standard Colors"
    assert coating["item_source"] == "current_pricing_plus_historical_usage"
    assert "GAF High Solids Silicone" in coating["explanation"]
    assert coating["unit"] == "gal"


def test_primer_basis_sqft_can_be_lower_than_net_without_changing_scope() -> None:
    workbench = build_estimating_workbench(sample_recommendation(), sample_data())
    original_area = workbench["scope"]["net_sqft"]
    primer = next(row for row in workbench["materials"] if row["package_key"] == "primer")

    primer["include"] = True
    primer["editable_basis_sqft"] = 2000
    recalculated = recalculate_workbench_tables({"scope": workbench["scope"], "materials": [primer], "labor": [], "adders": []})
    recalculated_primer = recalculated["materials"][0]

    assert workbench["scope"]["net_sqft"] == original_area
    assert recalculated_primer["calculated_quantity"] == 2
    assert recalculated_primer["estimated_cost"] == 550


def test_changing_item_updates_unit_price_from_row_options() -> None:
    workbench = build_estimating_workbench(sample_recommendation(), sample_data())
    coating = next(row for row in workbench["materials"] if row["package_key"] == "coating")

    coating["item_name"] = "Premium Silicone Coating 55 Gal"
    coating["include"] = True
    recalculated = recalculate_workbench_tables({"scope": workbench["scope"], "materials": [coating], "labor": [], "adders": []})
    recalculated_coating = recalculated["materials"][0]

    assert recalculated_coating["current_unit_price"] == 44
    assert recalculated_coating["item_source"] == "current_pricing"
    assert recalculated_coating["estimated_cost"] == 8800


def test_added_material_line_exports_to_workbook_with_item_name() -> None:
    workbench = build_estimating_workbench(sample_recommendation(), sample_data())
    manual = {
        "include": True,
        "package": "Manual",
        "package_key": "misc_material",
        "item_name": "Custom Walk Pad",
        "item_source": "manual",
        "editable_qty_per_sqft": 0.001,
        "historical_qty_per_sqft": 0,
        "editable_basis_sqft": 10000,
        "default_basis_sqft": 0,
        "unit": "ea",
        "current_unit_price": 125,
        "historical_cost_per_sqft": 0,
        "item_options_json": "[]",
    }
    workbench["materials"].append(manual)
    draft = workbench_to_draft_workbook_inputs(workbench)

    assert any(row["item"] == "Custom Walk Pad" and row["estimated_cost"] == 1250 for row in draft["material_rows"])


def test_historical_item_level_defaults_fall_back_to_package_level_when_weak() -> None:
    data = sample_data()
    data.job_package_summary.loc[data.job_package_summary["package"].eq("primer"), "qty_per_sqft"] = None
    data.job_package_summary.loc[data.job_package_summary["package"].eq("primer"), "total_quantity"] = None
    workbench = build_estimating_workbench(sample_recommendation(), data)
    primer = next(row for row in workbench["materials"] if row["package_key"] == "primer")

    assert primer["item_name"] == "Epoxy Primer 5 Gal - Clear/Black"
    assert primer["item_level_qty_per_sqft"] == 0
    assert primer["historical_qty_per_sqft"] == 0
    assert primer["historical_cost_per_sqft"] == 0.25


def test_filter_relaxation_reports_relaxed_filters_when_pool_is_too_narrow() -> None:
    filtered = build_estimating_workbench(
        sample_recommendation(),
        sample_data(),
        historical_filters={
            "division": "Roofing",
            "template_type": "roofing",
            "project_type": "roof coating",
            "substrate": "nonexistent substrate",
            "min_evidence_count": 2,
        },
    )

    coating = next(row for row in filtered["materials"] if row["package_key"] == "coating")

    assert coating["historical_qty_per_sqft"] > 0
    assert "substrate" in coating["filters_relaxed"]
    assert coating["minimum_evidence_count"] == 2


def test_manual_override_preserved_when_filters_change_and_reset_restores_default() -> None:
    original = build_estimating_workbench(sample_recommendation(), sample_data())
    edited = recalculate_workbench_tables(original)
    for row in edited["materials"]:
        if row["package_key"] == "coating":
            row["editable_qty_per_sqft"] = 0.099
    edited = recalculate_workbench_tables(edited)

    membrane_defaults = build_estimating_workbench(
        sample_recommendation(),
        sample_data(),
        historical_filters={
            "division": "Roofing",
            "template_type": "roofing",
            "project_type": "roof coating",
            "substrate": "membrane",
            "area_bucket": "15k_50k",
            "min_evidence_count": 1,
        },
    )
    merged = apply_historical_filter_update(edited, membrane_defaults)
    coating = next(row for row in merged["materials"] if row["package_key"] == "coating")
    labor_base = next(row for row in merged["labor"] if row["package_key"] == "labor_base")

    assert coating["editable_qty_per_sqft"] == 0.099
    assert coating["manual_override"] is True
    assert labor_base["editable_hours_per_1000_sqft"] == 13

    coating["reset_to_historical_default"] = True
    reset = recalculate_workbench_tables({"scope": merged["scope"], "materials": [coating], "labor": [], "adders": []})
    assert reset["materials"][0]["editable_qty_per_sqft"] == coating["historical_qty_per_sqft"]
    assert reset["materials"][0]["manual_override"] is False


def test_high_variability_warning_is_shown_for_wide_labor_ranges() -> None:
    workbench = build_estimating_workbench(sample_recommendation(), sample_data())
    top_coat = next(row for row in workbench["labor"] if row["package_key"] == "labor_top_coat")

    assert top_coat["relative_range_width"] >= 1
    assert "Wide historical range" in top_coat["variability_warning"]


def test_workbook_export_uses_filtered_final_values() -> None:
    workbench = build_estimating_workbench(
        sample_recommendation(),
        sample_data(),
        historical_filters={
            "division": "Roofing",
            "template_type": "roofing",
            "project_type": "roof coating",
            "substrate": "membrane",
            "area_bucket": "15k_50k",
            "min_evidence_count": 1,
        },
    )
    for row in workbench["materials"]:
        if row["package_key"] == "coating":
            row["include"] = True
    draft = workbench_to_draft_workbook_inputs(workbench)
    coating = next(row for row in draft["material_rows"] if row["category"] == "coating")

    assert coating["quantity"] == 500


def test_edited_workbench_values_populate_workbook_inputs() -> None:
    workbench = build_estimating_workbench(sample_recommendation(), sample_data())
    for row in workbench["materials"]:
        if row["package_key"] == "coating":
            row["editable_qty_per_sqft"] = 0.03
    for row in workbench["labor"]:
        if row["package_key"] == "labor_base":
            row["editable_hours_per_1000_sqft"] = 5.0

    edited = recalculate_workbench_tables(workbench)
    draft = workbench_to_draft_workbook_inputs(edited)

    assert draft["header"]["C12_estimated_sqft"] == 10000
    coating = next(row for row in draft["material_rows"] if row["category"] == "coating")
    labor = next(row for row in draft["labor_rows"] if row["task"] == "labor_base")
    assert coating["quantity"] == 300
    assert coating["estimated_cost"] == 11400
    assert labor["total_hours"] == 50
    assert labor["estimated_cost"] == 3600


def test_unchecked_rows_keep_defaults_but_do_not_contribute_until_included() -> None:
    workbench = build_estimating_workbench(sample_recommendation(), sample_data())
    material_packages = {row["package_key"]: row for row in workbench["materials"]}
    labor_packages = {row["package_key"]: row for row in workbench["labor"]}

    assert material_packages["primer"]["include"] is False
    assert material_packages["primer"]["editable_qty_per_sqft"] == 0.001
    assert material_packages["primer"]["estimated_cost"] == 0
    assert labor_packages["labor_prep"]["include"] is False
    assert labor_packages["labor_prep"]["editable_hours_per_1000_sqft"] == 3
    assert labor_packages["labor_prep"]["estimated_cost"] == 0

    totals = recalculate_workbench_tables(workbench)
    total_cost = sum(row["estimated_cost"] for row in totals["materials"] if row["include"])
    total_labor = sum(row["estimated_cost"] for row in totals["labor"] if row["include"])
    assert total_cost == material_packages["coating"]["estimated_cost"]
    assert total_labor == labor_packages["labor_base"]["estimated_cost"]


def test_missing_current_price_uses_historical_cost_default_when_included() -> None:
    workbench = build_estimating_workbench(sample_recommendation(), sample_data())
    material_packages = {row["package_key"]: row for row in workbench["materials"]}

    for package, expected_psf in {
        "seam_treatment": 0.3,
        "fastener_treatment": 0.2,
        "caulk_detail": 0.22,
    }.items():
        row = material_packages[package]
        assert row["current_unit_price"] == 0
        assert row["historical_cost_per_sqft"] == expected_psf
        row["include"] = True
        recalculated = recalculate_workbench_tables({"scope": workbench["scope"], "materials": [row], "labor": [], "adders": []})
        recalculated_row = recalculated["materials"][0]
        assert recalculated_row["price_source"] == "historical_cost_default"
        assert recalculated_row["needs_review"] is True
        assert recalculated_row["estimated_cost"] == expected_psf * 10000


def test_adders_populate_historical_defaults_without_contributing_until_included() -> None:
    workbench = build_estimating_workbench(sample_recommendation(), sample_data())
    adders = {row["adder_key"]: row for row in workbench["adders"]}

    assert adders["lift"]["include"] is False
    assert adders["lift"]["editable_value"] == 1500
    assert adders["lift"]["estimated_cost"] == 0
    assert adders["lift"]["evidence_count"] == 1
    assert adders["generator"]["include"] is False
    assert adders["generator"]["editable_value"] == 900

    adders["lift"]["include"] = True
    recalculated = recalculate_workbench_tables({"scope": workbench["scope"], "materials": [], "labor": [], "adders": [adders["lift"]]})
    assert recalculated["adders"][0]["estimated_cost"] == 1500


def test_edit_history_flags_large_material_and_labor_changes() -> None:
    original = build_estimating_workbench(sample_recommendation(), sample_data())
    edited = build_estimating_workbench(sample_recommendation(), sample_data())
    for row in edited["materials"]:
        if row["package_key"] == "coating":
            row["editable_qty_per_sqft"] = 0.04
    for row in edited["labor"]:
        if row["package_key"] == "labor_base":
            row["editable_hours_per_1000_sqft"] = 7.0
        if row["package_key"] == "labor_prime":
            row["include"] = True

    rows = build_edit_history_rows(original, edited)
    required = [row for row in rows if row["reason_required"]]

    assert any(row["section"] == "materials.coating" for row in required)
    assert any(row["section"] == "labor.labor_base" for row in required)
    assert any(row["section"] == "labor.labor_prime" and row["field_name"] == "include" for row in required)
    assert all("suggested_value" in row and "difference_pct" in row for row in rows)
