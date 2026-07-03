from __future__ import annotations

import pandas as pd

from jobscan.estimator.insulation_diagnostics import build_insulation_history_diagnostics, write_insulation_history_diagnostics
from jobscan.estimator.insulation_performance import build_area_calculation_explanation, build_area_calculation_trace
from jobscan.estimator.insulation_surfaces import build_insulation_surface_decisions, parse_r_value_targets
from jobscan.estimator.schemas import EstimateRecommendation, EstimatorData
from jobscan.estimator.workbench import (
    apply_historical_filter_update,
    build_edit_history_rows,
    build_estimating_workbench,
    historical_filters_from_scope,
    recalculate_workbench_tables,
    summarize_workbench_totals,
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


def sample_insulation_recommendation() -> EstimateRecommendation:
    return EstimateRecommendation(
        parsed_fields={
            "run_id": "test-insulation-workbench",
            "division": "Insulation",
            "template_type": "insulation",
            "project_type": "spray foam insulation",
            "building_type": "metal building",
            "substrate": "metal",
            "estimated_sqft": 2388,
            "gross_insulation_area_sqft": 2460,
            "gross_wall_area_sqft": 1260,
            "ceiling_area_sqft": 1200,
            "opening_area_known_sqft": 72,
            "opening_area_missing": True,
            "net_insulation_area_sqft": 2388,
            "missing_questions": ["What foam type: open-cell or closed-cell?", "Rollup door width?"],
            "notes": "Foam sprayed in a 30x40 metal building with 9' walls. Insulate outside walls and ceiling.",
        },
        recommended_scope=[],
        material_plan=[
            {"category": "foam", "package": "foam", "included_in_total": False, "needs_review": True},
            {"category": "thermal_barrier_coating", "package": "thermal_barrier_coating", "included_in_total": False, "needs_review": True},
        ],
        labor_plan=[{"task": "labor_foam", "included_in_total": False, "needs_review": True}],
        travel_plan={},
        historical_calibration={},
        similar_examples=[],
        estimate_low=None,
        estimate_target=None,
        estimate_high=None,
        review_flags=[],
        human_review_required=True,
        draft_workbook_inputs={"header": {"C12_estimated_sqft": 2388}},
    )


def sample_insulation_data() -> EstimatorData:
    rows = []
    for idx, qty_rate in enumerate([0.08, 0.085, 0.09, 0.095, 0.1], start=1):
        rows.append(
            {
                "job_id": f"IF{idx}",
                "division": "Insulation",
                "template_type": "insulation",
                "project_type": "spray foam insulation",
                "substrate": "metal building",
                "package": "foam",
                "item_name": "Closed-cell spray foam",
                "area_sqft": 2500,
                "total_quantity": qty_rate * 2500,
                "unit": "set",
                "qty_per_sqft": qty_rate,
                "total_cost": 2500 * 2.6,
                "cost_per_sqft": 2.6,
                "has_physical_quantity": True,
            }
        )
    for package, hours in {
        "labor_set_up": [1.8, 2.0, 2.2, 2.0, 2.1],
        "labor_foam": [10.0, 11.0, 12.0, 11.5, 12.5],
        "labor_clean_up": [1.2, 1.5, 1.4, 1.6, 1.3],
        "labor_loading": [0.8, 1.0, 1.1, 1.0, 0.9],
        "labor_traveling": [2.5, 3.0, 3.1, 2.8, 3.2],
    }.items():
        for idx, hrs_per_1000 in enumerate(hours, start=1):
            rows.append(
                {
                    "job_id": f"IL{package}{idx}",
                    "division": "Insulation",
                    "template_type": "insulation",
                    "project_type": "spray foam insulation",
                    "substrate": "metal building",
                    "package": package,
                    "area_sqft": 2500,
                    "total_hours": hrs_per_1000 * 2.5,
                    "hours_per_sqft": hrs_per_1000 / 1000,
                    "crew_size": 3,
                    "crew_selector_code": 3,
                    "days": (hrs_per_1000 * 2.5) / 30,
                    "daily_rate": 1350,
                    "hourly_rate": 45,
                    "calculated_cost": hrs_per_1000 * 2.5 * 45,
                    "formula_mode": "mixed_formula",
                }
            )
    for idx, cost in enumerate([450, 475, 500, 525, 550], start=1):
        rows.append(
            {
                "job_id": f"IT{idx}",
                "division": "Insulation",
                "template_type": "insulation",
                "project_type": "spray foam insulation",
                "substrate": "metal building",
                "package": "travel",
                "area_sqft": 2500,
                "total_cost": cost,
                "cost_per_sqft": cost / 2500,
            }
        )
    for idx in range(1, 3):
        rows.append(
            {
                "job_id": f"IH{idx}",
                "division": "Insulation",
                "template_type": "insulation",
                "project_type": "spray foam insulation",
                "substrate": "metal building",
                "package": "hotel",
                "area_sqft": 2500,
                "total_cost": 55500,
                "cost_per_sqft": 22.2,
            }
        )
    template_rows = []
    for idx in range(1, 13):
        template_rows.append(
            {
                "job_id": f"TF{idx}",
                "source_file": f"insulation_estimate_{idx}.xlsx",
                "division": "Insulation",
                "template_type": "insulation",
                "template_bucket": "foam",
                "line_item_kind": "material",
                "selected_item_name": "Closed-cell spray foam" if idx % 2 else "Spray foam insulation",
                "quantity": 2400 if idx <= 8 else None,
                "estimated_units": 210 if idx <= 5 else None,
                "estimated_cost": 6200 if idx <= 10 else None,
                "area_sqft": 2400 if idx <= 5 else None,
            }
        )
    return EstimatorData(job_package_summary=pd.DataFrame(rows), template_rows=pd.DataFrame(template_rows))


def sample_insulation_template_option_data() -> EstimatorData:
    template_rows = []
    for idx, thickness in enumerate([4.0, 4.25, 4.25, 4.5, 4.25], start=1):
        template_rows.append(
            {
                "job_id": f"TFD{idx}",
                "source_file": f"insulation_decision_{idx}.xlsx",
                "division": "Insulation",
                "template_type": "insulation",
                "project_type": "spray foam insulation",
                "sheet_name": "Estimate",
                "row_number": 19,
                "template_bucket": "foam",
                "line_item_kind": "material",
                "selector_code": 11,
                "resolved_item_name": "Gaco 2.0 lb.",
                "area_sqft": 2400,
                "thickness_inches": thickness,
                "yield_or_coverage": 13500,
                "yield_factor": 13500,
                "estimated_units": ((2400 / 13500) * thickness) * 1000,
                "estimated_sets": ((2400 / 13500) * thickness),
                "estimated_cost": 1200,
                "unit_price": 1.63,
                "formula_model": "foam_sets_from_area_thickness_yield",
            }
        )
    return EstimatorData(
        template_rows=pd.DataFrame(template_rows),
        pricing_catalog=pd.DataFrame(
            [
                {
                    "pricing_item_id": "OPEN",
                    "product_name": "AccuFoam Open Cell AAF1",
                    "category": "Spray Foam",
                    "unit_price": 1.2,
                    "unit_of_measure": "unit",
                    "is_current": True,
                },
                {
                    "pricing_item_id": "CLOSED",
                    "product_name": "NCFI Closed Cell InsulBloc OptiMaxx",
                    "category": "Spray Foam",
                    "unit_price": 2.4,
                    "unit_of_measure": "unit",
                    "is_current": True,
                },
            ]
        ),
        product_catalog=pd.DataFrame(
            [
                {
                    "product_id": "ncfi_optimaxx",
                    "manufacturer": "NCFI",
                    "product_name": "NCFI Closed Cell InsulBloc OptiMaxx",
                    "product_family": "InsulBloc OptiMaxx",
                    "category": "spray_foam",
                    "active": True,
                }
            ]
        ),
        product_properties=pd.DataFrame(
            [
                {
                    "product_id": "ncfi_optimaxx",
                    "property_name": "r_value",
                    "property_value": "Aged R-value 6.2 per inch",
                    "numeric_value": 6.2,
                    "unit": "R/in",
                    "source_text": "Aged R-value 6.2 per inch.",
                }
            ]
        ),
        product_rules=pd.DataFrame(
            [
                {
                    "product_id": "ncfi_optimaxx",
                    "rule_type": "recommended_use",
                    "rule_value": "Closed-cell spray foam for insulation applications.",
                    "severity": "info",
                    "source_text": "Recommended for insulation applications.",
                }
            ]
        ),
        product_documents=pd.DataFrame(
            [
                {
                    "product_id": "ncfi_optimaxx",
                    "source_path": "product_documents/ncfi_optimaxx.pdf",
                }
            ]
        ),
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
    assert labor_packages["labor_base"]["formula_mode"] == "mixed_formula"
    assert labor_packages["labor_base"]["crew_people_selection"] == labor_packages["labor_base"]["crew_size"]
    assert labor_packages["labor_base"]["total_hours"] == labor_packages["labor_base"]["calculated_hours"]
    assert labor_packages["labor_base"]["hourly_rate"] == labor_packages["labor_base"]["labor_rate"]
    assert labor_packages["labor_base"]["daily_rate"] > 0
    assert labor_packages["labor_base"]["source"] == "job_package_summary_full_corpus"
    assert "Used in 3 historical Roofing jobs" in labor_packages["labor_base"]["explanation"]
    assert labor_packages["labor_prep"]["include"] is True
    assert labor_packages["labor_prep"]["historical_hours_per_1000_sqft"] == 3
    assert labor_packages["labor_prep"]["editable_hours_per_1000_sqft"] == 3
    assert not any("AI estimated" in str(row) or "AI chose" in str(row) or "Automatically determined" in str(row) for row in workbench["materials"] + workbench["labor"])


def test_insulation_workbench_uses_insulation_filters_and_rows_only() -> None:
    workbench = build_estimating_workbench(sample_insulation_recommendation(), sample_insulation_data())

    assert workbench["historical_filters"]["division"] == "Insulation"
    assert workbench["historical_filters"]["template_type"] == "insulation"
    assert workbench["historical_filters"]["project_type"] == ""
    assert workbench["historical_filters"]["substrate"] == ""
    assert workbench["historical_filters"]["area_bucket"] == ""
    assert workbench["scope"]["net_insulation_area_sqft"] == 2388

    material_keys = {row["package_key"] for row in workbench["materials"]}
    labor_keys = {row["package_key"] for row in workbench["labor"]}

    assert {"foam", "thermal_barrier_coating", "membrane", "caulk_sealant"}.issubset(material_keys)
    assert {"labor_foam", "labor_set_up", "labor_clean_up", "labor_dc_315", "labor_mask"}.issubset(labor_keys)
    assert "coating" not in material_keys
    assert "seam_treatment" not in material_keys
    assert "fastener_treatment" not in material_keys
    assert "labor_base" not in labor_keys

    draft = workbench_to_draft_workbook_inputs(workbench)
    assert draft["template_type"] == "insulation"
    assert draft["header"]["C12_estimated_sqft"] == 2388

    materials = {row["package_key"]: row for row in workbench["materials"]}
    labor = {row["package_key"]: row for row in workbench["labor"]}
    foam = materials["foam"]
    assert foam["include"] is True
    assert foam["editable_basis_sqft"] == 2388
    assert foam["editable_qty_per_sqft"] > 0
    assert foam["calculated_quantity"] > 0
    assert foam["estimated_cost"] > 0
    assert foam["total_insulation_rows_for_bucket"] > foam["accepted_qty_per_sqft_rows"]
    assert foam["distinct_insulation_files_for_bucket"] > foam["evidence_count"]
    assert "appears in" in foam["explanation"]
    assert "clean quantity-per-sqft evidence" in foam["explanation"]
    assert "Default is based on those rows" in foam["notes"]
    assert labor["labor_foam"]["include"] is True
    assert labor["labor_set_up"]["include"] is True
    assert labor["labor_clean_up"]["include"] is True
    assert labor["labor_loading"]["include"] is True
    assert labor["labor_traveling"]["suggested_by_notes_rules"] == "review"
    assert labor["labor_foam"]["editable_hours_per_1000_sqft"] > 0
    assert labor["labor_foam"]["calculated_hours"] > 0
    assert labor["labor_foam"]["formula_mode"] == "mixed_formula"
    assert labor["labor_foam"]["crew_people_selection"] == 3
    assert labor["labor_foam"]["daily_rate"] == 1350
    assert labor["labor_foam"]["hourly_rate"] == 45
    assert labor["labor_foam"]["days"] > 0

    totals = recalculate_workbench_tables(workbench)
    assert sum(row["estimated_cost"] for row in totals["labor"] if row["include"]) > 0
    assert sum(row["estimated_cost"] for row in totals["materials"] if row["include"]) > 0


def test_insulation_product_selection_rejects_cross_bucket_products() -> None:
    data = sample_insulation_data()
    data.pricing_catalog = pd.DataFrame(
        [
            {
                "pricing_item_id": "P1",
                "product_name": "A4121 Black Foam Primer",
                "category": "Primer",
                "unit_price": 180,
                "unit_of_measure": "pail",
                "is_current": True,
            },
            {
                "pricing_item_id": "P2",
                "product_name": "DC 315 Thermal Barrier Coating",
                "category": "Thermal Barrier",
                "unit_price": 52,
                "unit_of_measure": "gal",
                "is_current": True,
            },
            {
                "pricing_item_id": "P3",
                "product_name": "White Silicone Roof Coating 55 Gal Drum",
                "category": "Coating",
                "unit_price": 40,
                "unit_of_measure": "gal",
                "is_current": True,
            },
            {
                "pricing_item_id": "P4",
                "product_name": "Drum Disposal Fee",
                "category": "Disposal",
                "unit_price": 95,
                "unit_of_measure": "each",
                "is_current": True,
            },
            {
                "pricing_item_id": "P5",
                "product_name": "Roofing Foam Repair Kit",
                "category": "Foam",
                "unit_price": 12,
                "unit_of_measure": "unit",
                "is_current": True,
            },
            {
                "pricing_item_id": "P6",
                "product_name": "Closed Cell Spray Foam Insulation",
                "category": "Foam",
                "unit_price": 2.85,
                "unit_of_measure": "sqft",
                "is_current": True,
            },
        ]
    )

    workbench = build_estimating_workbench(sample_insulation_recommendation(), data)
    materials = {row["package_key"]: row for row in workbench["materials"]}

    assert materials["thermal_barrier_coating"]["item_name"] == "DC 315 Thermal Barrier Coating"
    assert "Primer" not in materials["thermal_barrier_coating"]["item_name"]
    assert materials["drum_disposal"]["item_name"] == "Drum Disposal Fee"
    assert "Silicone" not in materials["drum_disposal"]["item_name"]
    assert materials["foam"]["item_name"] == "Closed Cell Spray Foam Insulation"
    assert "Roofing Foam" not in materials["foam"]["item_name"]


def test_insulation_low_clean_quantity_uses_cost_fallback() -> None:
    data = EstimatorData(
        job_package_summary=pd.DataFrame(
            [
                {
                    "job_id": f"CF{idx}",
                    "division": "Insulation",
                    "template_type": "insulation",
                    "package": "foam",
                    "area_sqft": 2400,
                    "total_quantity": None,
                    "qty_per_sqft": None,
                    "unit": "",
                    "total_cost": 6000 + idx * 100,
                    "cost_per_sqft": 2.5 + idx * 0.01,
                    "has_physical_quantity": False,
                }
                for idx in range(1, 7)
            ]
        )
    )

    workbench = build_estimating_workbench(sample_insulation_recommendation(), data)
    foam = next(row for row in workbench["materials"] if row["package_key"] == "foam")

    assert foam["include"] is True
    assert foam["historical_qty_per_sqft"] == 0
    assert foam["historical_cost_per_sqft"] > 0
    assert foam["historical_cost_evidence_count"] >= 5
    assert foam["estimated_cost"] > 0
    assert foam["price_source"] == "historical_cost_default"


def test_recalculate_workbench_uses_formula_mirror_for_edited_foam_and_labor() -> None:
    workbench = {
        "scope": {
            "division": "Insulation",
            "template_type": "insulation",
            "net_insulation_area_sqft": 1000,
        },
        "materials": [
            {
                "include": True,
                "decision_id": "insulation_foam_system",
                "package_key": "foam",
                "template_bucket": "foam",
                "package": "Foam",
                "workbook_row": "19-21",
                "item_name": "Closed Cell Spray Foam",
                "editable_basis_sqft": 1000,
                "default_basis_sqft": 1000,
                "historical_qty_per_sqft": 0.004,
                "editable_qty_per_sqft": 0.004,
                "thickness_inches": 2,
                "yield_factor": 500000,
                "current_unit_price": 100,
            }
        ],
        "labor": [
            {
                "include": True,
                "decision_id": "insulation_labor_foam",
                "package_key": "labor_foam",
                "template_bucket": "labor_foam",
                "labor_package": "Foam",
                "workbook_row": "86",
                "historical_hours_per_1000_sqft": 10,
                "editable_hours_per_1000_sqft": 10,
                "days": 1,
                "crew_size": 3,
                "daily_rate": 1350,
                "hourly_rate": 45,
                "labor_rate": 45,
                "formula_mode": "mixed_formula",
            }
        ],
        "adders": [],
    }

    baseline = recalculate_workbench_tables(workbench)
    foam = baseline["materials"][0]
    labor = baseline["labor"][0]

    assert foam["estimated_units"] == 4
    assert foam["estimated_sets"] == 0.004
    assert foam["estimated_cost"] == 400
    assert "Estimate!G19" in {cell["cell"] for cell in foam["workbook_cell_write_preview"]}
    assert labor["calculated_hours"] == 10
    assert labor["estimated_cost"] == 450

    edited = baseline
    edited["materials"][0]["thickness_inches"] = 3
    edited["labor"][0]["editable_hours_per_1000_sqft"] = 20
    recalculated = recalculate_workbench_tables(edited)

    assert recalculated["materials"][0]["estimated_units"] == 6
    assert recalculated["materials"][0]["estimated_cost"] == 600
    assert recalculated["labor"][0]["calculated_hours"] == 20
    assert recalculated["labor"][0]["estimated_cost"] == 900
    assert recalculated["labor"][0]["formula_source"] == "hours_hourly_rate"

    draft = workbench_to_draft_workbook_inputs(recalculated)
    assert draft["material_rows"][0]["formula_model"] == "foam_sets_from_area_thickness_yield"
    assert draft["material_rows"][0]["estimated_sets"] == 0.006
    assert draft["material_rows"][0]["workbook_cell_write_preview"]
    assert draft["labor_rows"][0]["formula_model"] == "labor_cost_from_days_crew_rate"
    assert draft["labor_rows"][0]["workbook_cell_write_preview"]


def test_insulation_r_value_targets_drive_surface_thickness_and_foam_aggregate() -> None:
    targets = parse_r_value_targets("Set roof/ceiling target R30 and walls R14 with closed-cell foam.")
    assert {row["surface_type"]: row["target_r_value"] for row in targets}["ceiling"] == 30
    assert {row["surface_type"]: row["target_r_value"] for row in targets}["walls"] == 14

    workbench = {
        "scope": {
            "division": "Insulation",
            "template_type": "insulation",
            "foam_type": "closed_cell",
            "notes": "Set ceiling target R30 and walls R14 with closed-cell foam.",
        },
        "insulation_surfaces": [
            {
                "include": True,
                "surface_type": "walls",
                "surface": "Walls",
                "gross_area_sqft": 1260,
                "deduction_area_sqft": 72,
                "net_area_sqft": 1188,
                "target_r_value": 14,
                "foam_type": "closed_cell",
            },
            {
                "include": True,
                "surface_type": "ceiling",
                "surface": "Ceiling",
                "gross_area_sqft": 1200,
                "deduction_area_sqft": 0,
                "net_area_sqft": 1200,
                "target_r_value": 30,
                "foam_type": "closed_cell",
            },
        ],
        "materials": [
            {
                "include": True,
                "decision_id": "insulation_foam_system",
                "package_key": "foam",
                "template_bucket": "foam",
                "package": "Foam",
                "workbook_row": "19-21",
                "item_name": "GacoRoofFoam Low GWP F2780",
                "editable_basis_sqft": 2388,
                "default_basis_sqft": 2388,
                "historical_qty_per_sqft": 0,
                "editable_qty_per_sqft": 0,
                "thickness_inches": 2,
                "yield_factor": 500000,
                "current_unit_price": 100,
                "historical_item": "NCFI Closed Cell Foam",
                "product_id": "gaco_roof_foam_f2780",
                "product_manufacturer": "Gaco",
                "product_knowledge_product_name": "GacoRoofFoam Low GWP F2780",
                "product_aged_r_value_per_inch": 5.7,
                "product_aged_r_value_per_inch_source": "Aged R-value 5.7 per inch.",
                "product_warnings": ["Verify application conditions and pass thickness."],
                "product_source_documents": ["product_documents/GacoRoofFoam-F2780.pdf"],
                "product_source_evidence_rows": [{"field": "aged_r_value", "source_page": 2}],
            }
        ],
        "labor": [],
        "adders": [],
    }

    recalculated = recalculate_workbench_tables(workbench)
    surfaces = {row["surface_type"]: row for row in recalculated["insulation_surfaces"]}
    assert surfaces["walls"]["edited_thickness_inches"] == 2.5
    assert surfaces["ceiling"]["edited_thickness_inches"] == 5.5
    assert surfaces["walls"]["r_value_source"] == "product_knowledge"

    foam = recalculated["materials"][0]
    assert foam["formula_model"] == "surface_weighted_foam_sets_from_r_value_thickness"
    assert foam["editable_basis_sqft"] == 2388
    assert foam["surface_weighted_thickness_inches"] > 4
    assert foam["estimated_units"] == 19.14
    assert foam["estimated_sets"] == 0.01914
    assert foam["estimated_cost"] == 1914
    assert len(foam["surface_formula_outputs"]) == 2

    performance = {row["surface_type"]: row for row in recalculated["insulation_performance_specs"]}
    assert performance["walls"]["product_knowledge_match"] == "GacoRoofFoam Low GWP F2780"
    assert performance["walls"]["alignment_status"] == "different_current_item"
    assert performance["walls"]["product_r_value_per_inch"] == 5.7
    assert performance["walls"]["edited_thickness_inches"] == 2.5
    assert performance["walls"]["estimated_cost"] > 0
    assert "Historical recommendation differs" in performance["walls"]["notes"]
    assert performance["ceiling"]["required_thickness_inches"] > performance["walls"]["required_thickness_inches"]


def test_insulation_area_trace_shows_ai_conflict_and_selected_deterministic_value() -> None:
    rows = build_area_calculation_trace(
        {
            "division": "Insulation",
            "template_type": "insulation",
            "building_footprint_length_ft": 30,
            "building_footprint_width_ft": 40,
            "wall_height_ft": 9,
            "footprint_area_sqft": 1200,
            "gross_wall_area_sqft": 1260,
            "ceiling_area_sqft": 1200,
            "gross_insulation_area_sqft": 2460,
            "opening_area_known_sqft": 72,
            "net_insulation_area_sqft": 2388,
            "notes": "30x40 metal building with 9 ft walls.",
        },
        ai_scope={"gross_wall_area_sqft": 1300, "net_insulation_area_sqft": 2400},
        deterministic_scope={"gross_wall_area_sqft": 1260, "net_insulation_area_sqft": 2388},
    )

    by_step = {row["step"]: row for row in rows}
    assert by_step["wall_area"]["conflict"] is True
    assert by_step["wall_area"]["selected_value"] == 1260
    assert by_step["wall_area"]["selected_source"] == "deterministic"
    assert by_step["net_insulation_area"]["conflict"] is True
    assert by_step["net_insulation_area"]["selected_value"] == 2388

    explanation = build_area_calculation_explanation(
        {
            "division": "Insulation",
            "template_type": "insulation",
            "building_footprint_length_ft": 30,
            "building_footprint_width_ft": 40,
            "wall_height_ft": 9,
            "footprint_area_sqft": 1200,
            "gross_wall_area_sqft": 1260,
            "ceiling_area_sqft": 1200,
            "gross_insulation_area_sqft": 2460,
            "opening_area_known_sqft": 72,
            "net_insulation_area_sqft": 2388,
            "notes": "30x40 metal building with 9 ft walls.",
        },
        trace_rows=rows,
    )
    assert "Footprint: 30 ft x 40 ft = 1,200 sq ft." in explanation
    assert "Final area used: 2,388 sq ft." in explanation
    assert "deterministic value was used" in explanation


def test_insulation_performance_specs_handle_missing_product_knowledge() -> None:
    workbench = {
        "scope": {"division": "Insulation", "template_type": "insulation", "foam_type": "closed_cell"},
        "insulation_surfaces": [
            {
                "include": True,
                "surface_type": "walls",
                "surface": "Walls",
                "net_area_sqft": 1000,
                "target_r_value": 14,
                "foam_type": "closed_cell",
            }
        ],
        "materials": [
            {
                "include": True,
                "decision_id": "insulation_foam_system",
                "package_key": "foam",
                "template_bucket": "foam",
                "package": "Foam",
                "item_name": "Manual closed-cell foam",
                "yield_factor": 500000,
                "current_unit_price": 100,
            }
        ],
        "labor": [],
        "adders": [],
    }

    recalculated = recalculate_workbench_tables(workbench)
    spec = recalculated["insulation_performance_specs"][0]

    assert spec["alignment_status"] == "no_product_knowledge_match"
    assert spec["product_fit_status"] == "review"
    assert any("No product knowledge match" in warning for warning in spec["product_warnings"])


def test_insulation_foam_template_decision_preserves_selector_and_separates_pricing_candidates() -> None:
    workbench = build_estimating_workbench(sample_insulation_recommendation(), sample_insulation_template_option_data())

    foam_decision = workbench["insulation_foam_template_decisions"][0]
    option_labels = {option["resolved_template_option"] for option in foam_decision["selector_options"]}
    candidate_names = {candidate["item_name"] for candidate in foam_decision["pricing_candidates"]}

    assert "Gaco 2.0 lb." in option_labels
    assert "NCFI 0.5 lb." in option_labels
    assert foam_decision["historical_selector_recommendation"] == "Gaco 2.0 lb."
    assert foam_decision["editable_selector_code"] == "11"
    assert foam_decision["resolved_template_option"] == "Gaco 2.0 lb."
    assert "AccuFoam Open Cell AAF1" in candidate_names
    assert "NCFI Closed Cell InsulBloc OptiMaxx" in candidate_names
    assert foam_decision["selected_pricing_candidate"] != foam_decision["resolved_template_option"]
    assert foam_decision["selected_pricing_candidate"] == "NCFI Closed Cell InsulBloc OptiMaxx"
    assert not any("Foam type mismatch" in warning for warning in foam_decision["compatibility_warnings"])

    expected_units = round(((foam_decision["basis_sqft"] / foam_decision["yield_or_coverage"]) * foam_decision["thickness_inches"]) * 1000, 6)
    assert foam_decision["formula_model"] == "foam_sets_from_area_thickness_yield"
    assert foam_decision["estimated_units"] == expected_units

    foam = next(row for row in workbench["materials"] if row["package_key"] == "foam")
    assert foam["selector_code"] == "11"
    assert any(cell["cell"] == "Estimate!A19" and cell["value"] == "11" for cell in foam["workbook_cell_write_preview"])


def test_insulation_foam_template_decision_warns_for_open_cell_selection_on_closed_cell_template() -> None:
    workbench = build_estimating_workbench(sample_insulation_recommendation(), sample_insulation_template_option_data())
    workbench["insulation_foam_template_decisions"][0]["selected_pricing_candidate"] = "AccuFoam Open Cell AAF1"

    recalculated = recalculate_workbench_tables(workbench)
    foam_decision = recalculated["insulation_foam_template_decisions"][0]

    assert foam_decision["selected_pricing_candidate"] == "AccuFoam Open Cell AAF1"
    assert foam_decision["compatibility_status"] == "spec_mismatch"
    assert any("Foam type mismatch" in warning for warning in foam_decision["compatibility_warnings"])


def test_insulation_foam_template_decision_does_not_default_to_roof_repair_foam() -> None:
    data = sample_insulation_template_option_data()
    roof_repair_row = {
        "pricing_item_id": "ROOF_REPAIR",
        "product_name": "TNF Roof Repair 3 LB Foam 120 Kit",
        "category": "Spray Foam",
        "unit_price": 0.5,
        "unit_of_measure": "unit",
        "is_current": True,
    }
    data.pricing_catalog = pd.concat([pd.DataFrame([roof_repair_row]), data.pricing_catalog], ignore_index=True)
    workbench = build_estimating_workbench(sample_insulation_recommendation(), data)

    foam_decision = workbench["insulation_foam_template_decisions"][0]

    assert "TNF Roof Repair 3 LB Foam 120 Kit" in {candidate["item_name"] for candidate in foam_decision["pricing_candidates"]}
    assert foam_decision["selected_pricing_candidate"] != "TNF Roof Repair 3 LB Foam 120 Kit"


def test_insulation_foam_template_decision_does_not_warn_for_manufacturer_mismatch_alone() -> None:
    workbench = build_estimating_workbench(sample_insulation_recommendation(), sample_insulation_template_option_data())
    workbench["insulation_foam_template_decisions"][0]["selected_pricing_candidate"] = "NCFI Closed Cell InsulBloc OptiMaxx"

    recalculated = recalculate_workbench_tables(workbench)
    foam_decision = recalculated["insulation_foam_template_decisions"][0]

    assert foam_decision["resolved_template_option"] == "Gaco 2.0 lb."
    assert foam_decision["selected_pricing_candidate"] == "NCFI Closed Cell InsulBloc OptiMaxx"
    assert not any("manufacturer" in warning.lower() for warning in foam_decision["compatibility_warnings"])
    assert not any("Foam type mismatch" in warning for warning in foam_decision["compatibility_warnings"])
    assert foam_decision["compatibility_status"] in {"compatible", "review"}
    assert "Closed-cell spray foam" in foam_decision["product_guidance"]


def test_insulation_foam_template_decision_feeds_draft_workbook_selector_inputs() -> None:
    workbench = build_estimating_workbench(sample_insulation_recommendation(), sample_insulation_template_option_data())
    workbench["insulation_foam_template_decisions"][0].update(
        {
            "editable_selector_code": "21",
            "basis_sqft": 1200,
            "thickness_inches": 3,
            "yield_or_coverage": 12000,
            "unit_price": 2.4,
            "selected_pricing_candidate": "NCFI Closed Cell InsulBloc OptiMaxx",
        }
    )

    draft = workbench_to_draft_workbook_inputs(workbench)
    foam = next(row for row in draft["material_rows"] if row["category"] == "foam")

    assert foam["selector_code"] == "21"
    assert foam["basis_sqft"] == 1200
    assert foam["thickness_inches"] == 3
    assert foam["yield_factor"] == 12000
    assert foam["unit_price"] == 2.4
    assert foam["estimated_units"] == 300
    assert any(cell["cell"] == "Estimate!A19" and cell["value"] == "21" for cell in foam["workbook_cell_write_preview"])


def test_roofing_detail_template_decisions_preserve_caulk_selector_and_fabric_rows() -> None:
    data = sample_data()
    data.pricing_catalog = pd.concat(
        [
            data.pricing_catalog,
            pd.DataFrame(
                [
                    {
                        "pricing_item_id": "S1",
                        "product_name": "Silicone Sealant Sausage",
                        "category": "Sealant",
                        "unit_price": 12,
                        "unit_of_measure": "sausage",
                        "is_current": True,
                    },
                    {
                        "pricing_item_id": "F1",
                        "product_name": "Premium Seam Fabric Roll",
                        "category": "Fabric",
                        "unit_price": 5,
                        "unit_of_measure": "lf",
                        "is_current": True,
                    },
                ]
            ),
        ],
        ignore_index=True,
    )

    workbench = build_estimating_workbench(
        sample_recommendation(),
        data,
        scope_override={"notes": "Open seams need silicone sausage sealant and fabric reinforcement."},
    )
    details = {str(row["workbook_row"]): row for row in workbench["roofing_detail_template_decisions"]}

    assert {"43", "45", "79"}.issubset(details)
    assert details["43"]["include"] is True
    assert details["79"]["include"] is True
    assert "Silicone Sausage" in {option["resolved_template_option"] for option in details["43"]["selector_options"]}
    assert details["43"]["resolved_template_option"] != details["43"]["selected_pricing_candidate"]
    assert "Sealant" in details["43"]["selected_pricing_candidate"]
    assert "Fabric" in details["79"]["selected_pricing_candidate"]


def test_roofing_detail_template_decisions_recalculate_and_feed_workbook_inputs() -> None:
    workbench = build_estimating_workbench(
        sample_recommendation(),
        sample_data(),
        scope_override={"notes": "Open seams need sealant and fabric reinforcement."},
    )
    for row in workbench["roofing_detail_template_decisions"]:
        if row["workbook_row"] == "43":
            row.update({"include": True, "editable_selector_code": "2", "units": 48, "unit_price": 12, "selected_pricing_candidate": "Silicone Sealant Sausage"})
        elif row["workbook_row"] == "79":
            row.update({"include": True, "linear_ft": 100, "unit_price": 5, "selected_pricing_candidate": "Premium Seam Fabric Roll"})
        else:
            row["include"] = False

    recalculated = recalculate_workbench_tables(workbench)
    details = {str(row["workbook_row"]): row for row in recalculated["roofing_detail_template_decisions"]}

    assert details["43"]["estimated_cost"] == 576
    assert details["43"]["estimated_units"] == 48
    assert details["79"]["estimated_cost"] == 500
    assert details["79"]["linear_ft"] == 100

    draft = workbench_to_draft_workbook_inputs(recalculated)
    detail_rows = {str(row["workbook_row"]): row for row in draft["material_rows"] if row["category"] in {"caulk_detail", "fabric"}}

    assert detail_rows["43"]["selector_code"] == "2"
    assert detail_rows["43"]["quantity"] == 48
    assert detail_rows["79"]["linear_ft"] == 100
    assert any(cell["cell"] == "Estimate!A43" and cell["value"] == "2" for cell in detail_rows["43"]["workbook_cell_write_preview"])
    assert any(cell["cell"] == "Estimate!C79" and cell["value"] == 100 for cell in detail_rows["79"]["workbook_cell_write_preview"])


def test_insulation_surface_builder_uses_default_r_per_inch_when_product_missing() -> None:
    rows = build_insulation_surface_decisions(
        {
            "division": "Insulation",
            "template_type": "insulation",
            "foam_type": "closed_cell",
            "gross_wall_area_sqft": 1260,
            "ceiling_area_sqft": 1200,
            "opening_area_known_sqft": 72,
            "openings": [{"opening_type": "window", "quantity": 5, "known_area_sqft": 30}],
            "notes": "Walls R14 and ceiling R30.",
        },
        notes="Walls R14 and ceiling R30.",
    )
    by_surface = {row["surface_type"]: row for row in rows}
    assert by_surface["walls"]["product_r_value_per_inch"] == 5.7
    assert by_surface["walls"]["r_value_source"] == "estimator_default_by_foam_type"
    assert by_surface["ceiling"]["rounded_thickness_inches"] == 5.5


def test_insulation_history_diagnostics_workbook_explains_clean_qty_gap(tmp_path) -> None:
    data = sample_insulation_data()

    sheets = build_insulation_history_diagnostics(data)
    summary = sheets["Summary"]
    foam = summary[summary["template_bucket"].astype(str).eq("foam")].iloc[0]

    assert foam["total_files"] > foam["clean_qty_per_sqft_rows"]
    assert foam["total_rows"] > foam["clean_qty_per_sqft_rows"]
    assert foam["rows_with_quantity"] >= foam["clean_qty_per_sqft_rows"]
    assert "missing_area" in foam["top_rejection_reasons"]

    output = tmp_path / "insulation_history_diagnostics.xlsx"
    write_insulation_history_diagnostics(data, output)

    assert output.exists()
    assert output.stat().st_size > 0


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


def test_roof_coating_item_selection_rejects_sealant_tube_for_main_coating() -> None:
    data = EstimatorData(
        pricing_catalog=pd.DataFrame(
            [
                {
                    "pricing_item_id": "BAD",
                    "product_name": "Silicone Sealant Flashing Grade - 11 oz tube",
                    "category": "Sealant",
                    "unit_price": 8,
                    "unit_of_measure": "tube",
                    "is_current": True,
                },
                {
                    "pricing_item_id": "GOOD",
                    "product_name": "GAF High Solids Silicone 55 Gal - Standard Colors",
                    "category": "Coating",
                    "price_per_gallon": 38,
                    "unit_price": 2090,
                    "unit_of_measure": "gal",
                    "is_current": True,
                },
            ]
        )
    )

    workbench = build_estimating_workbench(sample_recommendation(), data)
    coating = next(row for row in workbench["materials"] if row["package_key"] == "coating")

    assert "Sealant" not in coating["item_name"]
    assert "tube" not in coating["item_name"].lower()
    assert "High Solids Silicone" in coating["item_name"]
    assert "roof coating product signal" in coating["selected_item_reason"]
    assert any("Sealant" in str(item.get("item_name")) for item in coating["top_rejected_item_reasons"])


def test_roofing_coating_template_decision_uses_selector_options_not_pricing_skus() -> None:
    workbench = build_estimating_workbench(sample_recommendation(), sample_data())
    decisions = workbench["roofing_coating_template_decisions"]

    assert [row["workbook_row"] for row in decisions] == ["26", "27", "28"]
    assert decisions[0]["include"] is True
    assert decisions[1]["include"] is True
    assert decisions[2]["include"] is False
    assert decisions[0]["editable_selector_code"] == "11"
    assert decisions[0]["resolved_template_option"] == "Gaco Silicone"
    assert any(option["resolved_template_option"] == "BASF Acrylic" for option in decisions[0]["selector_options"])
    assert decisions[0]["selected_pricing_candidate"] == "GAF High Solids Silicone 55 Gal - Standard Colors"
    assert decisions[0]["selected_pricing_candidate"] != decisions[0]["resolved_template_option"]


def test_roofing_coating_template_decision_excludes_sealant_tube_candidates() -> None:
    data = EstimatorData(
        pricing_catalog=pd.DataFrame(
            [
                {
                    "pricing_item_id": "BAD",
                    "product_name": "Silicone Sealant Flashing Grade - 11 oz tube",
                    "category": "Sealant",
                    "unit_price": 8,
                    "unit_of_measure": "tube",
                    "is_current": True,
                },
                {
                    "pricing_item_id": "GOOD",
                    "product_name": "GAF High Solids Silicone 55 Gal - Standard Colors",
                    "category": "Coating",
                    "price_per_gallon": 38,
                    "unit_price": 38,
                    "unit_of_measure": "gal",
                    "is_current": True,
                },
            ]
        )
    )

    workbench = build_estimating_workbench(sample_recommendation(), data)
    decision = workbench["roofing_coating_template_decisions"][0]

    assert "Sealant" not in decision["selected_pricing_candidate"]
    assert "High Solids Silicone" in decision["selected_pricing_candidate"]
    assert any("Silicone Sealant" in candidate["item_name"] for candidate in decision["pricing_candidates"])


def test_roofing_coating_template_decision_recalculates_formula_outputs() -> None:
    workbench = build_estimating_workbench(sample_recommendation(), sample_data())
    first = workbench["roofing_coating_template_decisions"][0]
    first["editable_selector_code"] = "21"
    first["basis_sqft"] = 1000
    first["gal_per_100_sqft"] = 1.5
    first["waste_factor_pct"] = 10
    first["unit_price"] = 50
    first["include"] = True
    workbench["roofing_coating_template_decisions"][1]["include"] = False

    edited = recalculate_workbench_tables(workbench)
    decision = edited["roofing_coating_template_decisions"][0]

    assert decision["resolved_template_option"] == "BASF Silicone"
    assert round(decision["estimated_gallons"], 4) == round(((1000 / 100) * 1.5) / 0.9, 4)
    assert round(decision["estimated_cost"], 2) == round(decision["estimated_gallons"] * 50, 2)
    assert decision["wet_mils_estimate"] == 24


def test_roofing_coating_template_decisions_feed_workbook_inputs() -> None:
    workbench = build_estimating_workbench(sample_recommendation(), sample_data())
    first = workbench["roofing_coating_template_decisions"][0]
    first["editable_selector_code"] = "21"
    first["basis_sqft"] = 1000
    first["gal_per_100_sqft"] = 1.5
    first["waste_factor_pct"] = 10
    first["unit_price"] = 50
    workbench["roofing_coating_template_decisions"][1]["include"] = False

    draft = workbench_to_draft_workbook_inputs(workbench)
    coating_rows = [row for row in draft["material_rows"] if row["category"] == "coating"]

    assert len(coating_rows) == 1
    coating = coating_rows[0]
    assert coating["workbook_row"] == "26"
    assert coating["selector_code"] == "21"
    assert coating["basis_sqft"] == 1000
    assert coating["gal_per_100_sqft"] == 1.5
    assert coating["waste_factor_pct"] == 10
    assert any(write["cell"] == "Estimate!A26" and write["value"] == "21" for write in coating["workbook_cell_write_preview"])


def test_roofing_foam_template_decision_uses_template_options_and_roofing_candidates() -> None:
    recommendation = sample_recommendation()
    recommendation.parsed_fields["notes"] = "Install SPF roofing foam in low areas before coating."
    data = sample_data()
    data.pricing_catalog = pd.concat(
        [
            data.pricing_catalog,
            pd.DataFrame(
                [
                    {
                        "pricing_item_id": "RF1",
                        "product_name": "GacoRoofFoam F2733RHFO Roofing Foam",
                        "category": "Spray Foam",
                        "unit_price": 2.5,
                        "unit_of_measure": "set",
                        "is_current": True,
                    },
                    {
                        "pricing_item_id": "IF1",
                        "product_name": "NCFI InsulBloc OptiMaxx Closed Cell Spray Foam",
                        "category": "Spray Foam",
                        "unit_price": 2.0,
                        "unit_of_measure": "set",
                        "is_current": True,
                    },
                ]
            ),
        ],
        ignore_index=True,
    )

    workbench = build_estimating_workbench(recommendation, data)
    decisions = workbench["roofing_foam_template_decisions"]

    assert [row["workbook_row"] for row in decisions] == ["19", "20", "21"]
    assert decisions[0]["include"] is True
    assert decisions[1]["include"] is False
    assert decisions[2]["include"] is False
    assert decisions[0]["editable_selector_code"] == "11"
    assert decisions[0]["resolved_template_option"] == "Gaco Roof 2.7"
    assert any(option["resolved_template_option"] == "BASF Roof 2.7" for option in decisions[0]["selector_options"])
    assert decisions[0]["selected_pricing_candidate"] == "GacoRoofFoam F2733RHFO Roofing Foam"
    assert decisions[0]["selected_pricing_candidate"] != decisions[0]["resolved_template_option"]
    assert any(
        candidate["item_name"] == "NCFI InsulBloc OptiMaxx Closed Cell Spray Foam"
        and candidate["compatibility_status"] == "spec_mismatch"
        for candidate in decisions[0]["pricing_candidates"]
    )
    assert decisions[0]["estimated_units"] > 0


def test_roofing_foam_template_decision_recalculates_and_feeds_workbook_inputs() -> None:
    recommendation = sample_recommendation()
    recommendation.parsed_fields["notes"] = "Install SPF roofing foam before coating."
    data = sample_data()
    data.pricing_catalog = pd.concat(
        [
            data.pricing_catalog,
            pd.DataFrame(
                [
                    {
                        "pricing_item_id": "RF1",
                        "product_name": "GacoRoofFoam F2733RHFO Roofing Foam",
                        "category": "Spray Foam",
                        "unit_price": 2.5,
                        "unit_of_measure": "set",
                        "is_current": True,
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    workbench = build_estimating_workbench(recommendation, data)
    first = workbench["roofing_foam_template_decisions"][0]
    first["editable_selector_code"] = "21"
    first["basis_sqft"] = 865
    first["thickness_inches"] = 1.5
    first["yield_or_coverage"] = 2600
    first["unit_price"] = 2.25
    first["include"] = True
    workbench["roofing_foam_template_decisions"][1]["include"] = False
    workbench["roofing_foam_template_decisions"][2]["include"] = False

    edited = recalculate_workbench_tables(workbench)
    decision = edited["roofing_foam_template_decisions"][0]

    assert decision["resolved_template_option"] == "BASF Roof 2.7"
    assert round(decision["estimated_units"], 6) == round(((865 / 2600) * 1.5) * 1000, 6)
    assert round(decision["estimated_cost"], 2) == 1122.84

    draft = workbench_to_draft_workbook_inputs(edited)
    foam_rows = [row for row in draft["material_rows"] if row["category"] == "roofing_foam"]

    assert len(foam_rows) == 1
    foam = foam_rows[0]
    assert foam["workbook_row"] == "19"
    assert foam["selector_code"] == "21"
    assert foam["basis_sqft"] == 865
    assert foam["thickness_inches"] == 1.5
    assert foam["yield_factor"] == 2600
    assert any(write["cell"] == "Estimate!A19" and write["value"] == "21" for write in foam["workbook_cell_write_preview"])
    assert any(write["cell"] == "Estimate!C19" and write["value"] == 865 for write in foam["workbook_cell_write_preview"])


def test_roofing_primer_template_decision_uses_selector_options_and_explicit_include() -> None:
    recommendation = sample_recommendation()
    recommendation.parsed_fields["notes"] = "Metal roof with scattered rust. Include primer before coating."

    workbench = build_estimating_workbench(recommendation, sample_data())
    decision = workbench["roofing_primer_template_decisions"][0]

    assert decision["include"] is True
    assert decision["workbook_row"] == "39"
    assert decision["editable_selector_code"] == "2"
    assert decision["resolved_template_option"] == "Red Zinc Oxide"
    assert decision["basis_sqft"] == 10000
    assert any(option["resolved_template_option"] == "Gaco E-5320" for option in decision["selector_options"])
    assert decision["selected_pricing_candidate"] == "Epoxy Primer 5 Gal - Clear/Black"
    assert decision["selected_pricing_candidate"] != decision["resolved_template_option"]


def test_roofing_primer_template_decision_recalculates_formula_outputs_and_guidance() -> None:
    recommendation = sample_recommendation()
    recommendation.parsed_fields["notes"] = "Prime the rusted metal before silicone coating."
    data = sample_data()
    data.product_catalog = pd.DataFrame(
        [
            {
                "product_id": "gaf_epoxy_primer",
                "manufacturer": "GAF",
                "product_name": "Epoxy Primer 5 Gal - Clear/Black",
                "category": "primer",
                "active": True,
            }
        ]
    )
    data.product_rules = pd.DataFrame(
        [
            {
                "product_id": "gaf_epoxy_primer",
                "rule_type": "recommended_use",
                "rule_value": "Primer for metal and coating restoration applications.",
                "severity": "info",
                "source_text": "Recommended for metal restoration primer use.",
            },
            {
                "product_id": "gaf_epoxy_primer",
                "rule_type": "limitation",
                "rule_value": "Confirm substrate preparation before applying primer.",
                "severity": "warning",
                "source_text": "Substrate must be clean and prepared.",
            },
        ]
    )
    data.product_properties = pd.DataFrame(
        [
            {
                "product_id": "gaf_epoxy_primer",
                "property_name": "coverage_sqft_per_gallon",
                "property_value": "250",
                "numeric_value": 250,
                "unit": "sqft/gal",
                "source_text": "Coverage: 250 square feet per gallon.",
            }
        ]
    )
    data.product_documents = pd.DataFrame(
        [{"product_id": "gaf_epoxy_primer", "source_path": "product_documents/gaf_epoxy_primer.pdf"}]
    )
    data.product_decision_links = pd.DataFrame(
        [{"product_id": "gaf_epoxy_primer", "decision_id": "roofing_primer", "confidence": "high"}]
    )

    workbench = build_estimating_workbench(recommendation, data)
    workbench["roofing_primer_template_decisions"][0].update(
        {
            "include": True,
            "editable_selector_code": "1",
            "basis_sqft": 1000,
            "coverage_sqft_per_unit": 250,
            "unit_price": 100,
            "selected_pricing_candidate": "Epoxy Primer 5 Gal - Clear/Black",
        }
    )
    edited = recalculate_workbench_tables(workbench)
    decision = edited["roofing_primer_template_decisions"][0]

    assert decision["resolved_template_option"] == "Gaco E-5320"
    assert decision["estimated_units"] == 4
    assert decision["estimated_cost"] == 400
    assert "Primer for metal" in decision["product_guidance"]
    assert decision["product_guidance_status"] == "matched"
    assert any("Estimate!A39" == write["cell"] and write["value"] == "1" for write in decision["workbook_cell_write_preview"])


def test_roofing_primer_template_decisions_feed_workbook_inputs() -> None:
    recommendation = sample_recommendation()
    recommendation.parsed_fields["notes"] = "Prime the metal roof before coating."
    workbench = build_estimating_workbench(recommendation, sample_data())
    workbench["roofing_primer_template_decisions"][0].update(
        {
            "include": True,
            "editable_selector_code": "1",
            "basis_sqft": 1000,
            "coverage_sqft_per_unit": 250,
            "unit_price": 100,
        }
    )

    draft = workbench_to_draft_workbook_inputs(workbench)
    primer_rows = [row for row in draft["material_rows"] if row["category"] == "primer"]

    assert len(primer_rows) == 1
    primer = primer_rows[0]
    assert primer["workbook_row"] == "39"
    assert primer["selector_code"] == "1"
    assert primer["basis_sqft"] == 1000
    assert primer["coverage_sqft_per_unit"] == 250
    assert primer["estimated_units"] == 4
    assert any(write["cell"] == "Estimate!A39" and write["value"] == "1" for write in primer["workbook_cell_write_preview"])


def test_roof_coating_item_selection_does_not_fall_back_to_sealant_when_only_bad_candidate_exists() -> None:
    data = EstimatorData(
        pricing_catalog=pd.DataFrame(
            [
                {
                    "pricing_item_id": "BAD",
                    "product_name": "Silicone Sealant Flashing Grade - 11 oz tube",
                    "category": "Sealant",
                    "unit_price": 8,
                    "unit_of_measure": "tube",
                    "is_current": True,
                },
            ]
        )
    )

    workbench = build_estimating_workbench(sample_recommendation(), data)
    coating = next(row for row in workbench["materials"] if row["package_key"] == "coating")

    assert coating["item_name"] == "Manual roof coating item"
    assert coating["item_source"] == "manual"
    assert "Sealant" not in coating["item_name"]
    assert "sealant/tube candidates were rejected" in coating["selected_item_reason"]
    assert any("Sealant" in str(item.get("item_name")) for item in coating["top_rejected_item_reasons"])


def test_detail_buckets_can_select_sealant_products() -> None:
    data = EstimatorData(
        pricing_catalog=pd.DataFrame(
            [
                {
                    "pricing_item_id": "S1",
                    "product_name": "Silicone Sealant Flashing Grade - 11 oz tube",
                    "category": "Sealant",
                    "unit_price": 8,
                    "unit_of_measure": "tube",
                    "is_current": True,
                },
            ]
        )
    )

    workbench = build_estimating_workbench(sample_recommendation(), data)
    seam = next(row for row in workbench["materials"] if row["package_key"] == "seam_treatment")
    caulk = next(row for row in workbench["materials"] if row["package_key"] == "caulk_detail")

    assert "Sealant" in seam["item_name"]
    assert "Sealant" in caulk["item_name"]


def test_historical_filters_populate_from_parsed_scope_values() -> None:
    workbench = build_estimating_workbench(sample_recommendation(), sample_data())
    filters = workbench["historical_filters"]

    assert filters["division"] == "Roofing"
    assert filters["template_type"] == "roofing"
    assert filters["project_type"] == "roof coating"
    assert filters["substrate"] == "metal"
    assert filters["coating_type"] == "silicone"
    assert filters["warranty_years"] == 10
    assert filters["roof_condition"] == "fair"
    assert filters["access_complexity"] == "low"
    assert filters["penetrations_complexity"] == "low"
    assert filters["area_bucket"] == "5k_15k"
    assert filters["source_year"] is None

    empty_source = historical_filters_from_scope({"net_sqft": 10000})
    assert empty_source["source_year"] is None
    assert empty_source["warranty_years"] is None


def test_test3_scope_populates_historical_filters_and_partial_primer_basis() -> None:
    recommendation = sample_recommendation()
    recommendation.parsed_fields.update(
        {
            "notes": (
                "Commercial metal roof. Roof measures 140 ft by 90 ft. Overall roof is in good condition. "
                "Only the south edge has oxidation and rusted fasteners. Approximately twenty percent of the roof "
                "requires primer before coating. Few penetrations. Easy access. Customer requests a 10-year silicone restoration."
            ),
            "project_type": "roof coating",
            "substrate": "metal",
            "estimated_sqft": 12600,
            "gross_area_sqft": 12600,
            "deduction_area_sqft": 0,
            "net_area_sqft": 12600,
            "warranty_target_years": 10,
            "coating_type": "silicone",
            "roof_condition": "good",
            "access_complexity": "low",
            "penetrations_complexity": "low",
        }
    )

    workbench = build_estimating_workbench(recommendation, sample_data())
    filters = workbench["historical_filters"]
    primer = next(row for row in workbench["materials"] if row["package_key"] == "primer")
    labor = {row["package_key"]: row for row in workbench["labor"]}
    totals = recalculate_workbench_tables(workbench)

    assert filters["coating_type"] == "silicone"
    assert filters["warranty_years"] == 10
    assert filters["roof_condition"] == "good"
    assert filters["access_complexity"] == "low"
    assert filters["penetrations_complexity"] == "low"
    assert filters["source_year"] is None
    assert primer["suggested_by_notes_rules"] == "review"
    assert primer["include"] is False
    assert primer["editable_basis_sqft"] == 2520
    assert primer["estimated_cost"] == 0
    assert labor["labor_prep"]["include"] is True
    assert labor["labor_base"]["include"] is True
    assert labor["labor_top_coat"]["include"] is True
    assert labor["labor_cleanup"]["include"] is True
    assert labor["labor_loading"]["include"] is True
    assert labor["labor_seam_sealer"]["include"] is False
    primer["include"] = True
    recalculated = recalculate_workbench_tables({"scope": workbench["scope"], "materials": [primer], "labor": [], "adders": []})
    assert recalculated["materials"][0]["calculated_quantity"] == 2.52
    assert recalculated["materials"][0]["estimated_cost"] == 693
    assert totals["scope"]["net_sqft"] == 12600
    assert sum(row["estimated_cost"] for row in totals["labor"] if row["include"]) > 0


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
    coating_rows = [row for row in draft["material_rows"] if row["category"] == "coating"]

    assert sum(row["quantity"] for row in coating_rows) == 500
    assert {row["workbook_row"] for row in coating_rows} == {"26", "27"}


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
    coating_rows = [row for row in draft["material_rows"] if row["category"] == "coating"]
    labor = next(row for row in draft["labor_rows"] if row["task"] == "labor_base")
    assert sum(row["quantity"] for row in coating_rows) == 300
    assert sum(row["estimated_cost"] for row in coating_rows) == 11400
    assert labor["total_hours"] == 50
    assert labor["estimated_cost"] == 3600


def test_unchecked_rows_keep_defaults_but_do_not_contribute_until_included() -> None:
    workbench = build_estimating_workbench(sample_recommendation(), sample_data())
    material_packages = {row["package_key"]: row for row in workbench["materials"]}
    labor_packages = {row["package_key"]: row for row in workbench["labor"]}

    assert material_packages["primer"]["include"] is False
    assert material_packages["primer"]["editable_qty_per_sqft"] == 0.001
    assert material_packages["primer"]["estimated_cost"] == 0
    assert labor_packages["labor_prime"]["include"] is False
    assert labor_packages["labor_prime"]["estimated_cost"] == 0

    totals = recalculate_workbench_tables(workbench)
    total_cost = sum(row["estimated_cost"] for row in totals["materials"] if row["include"])
    total_labor = sum(row["estimated_cost"] for row in totals["labor"] if row["include"])
    assert total_cost == material_packages["coating"]["estimated_cost"]
    assert total_labor > 0
    assert total_labor >= labor_packages["labor_base"]["estimated_cost"]


def test_summarize_totals_prefers_roofing_decision_sections_over_flat_rows() -> None:
    workbench = build_estimating_workbench(sample_recommendation(), sample_data())
    for row in workbench["materials"]:
        if row.get("package_key") == "coating":
            row["include"] = True
            row["estimated_cost"] = 999000
    for row in workbench["labor"]:
        if row.get("package_key") in {"labor_prep", "labor_base", "labor_top_coat"}:
            row["include"] = True
            row["estimated_cost"] = 999000

    totals = summarize_workbench_totals(workbench)
    recalculated = recalculate_workbench_tables(workbench)
    expected_material = sum(
        row["estimated_cost"]
        for section in (
            "roofing_foam_template_decisions",
            "roofing_coating_template_decisions",
            "roofing_primer_template_decisions",
            "roofing_detail_template_decisions",
            "roofing_detail_quantity_template_decisions",
            "roofing_board_fastener_template_decisions",
            "roofing_granules_template_decisions",
            "roofing_accessory_template_decisions",
        )
        for row in recalculated.get(section, [])
        if row.get("include")
    )
    expected_labor = sum(row["estimated_cost"] for row in recalculated["roofing_labor_template_decisions"] if row.get("include"))

    assert totals["material_total"] == expected_material
    assert totals["labor_total"] == expected_labor
    assert totals["material_total"] != 999000
    assert totals["labor_total"] != 999000


def test_summarize_totals_uses_insulation_performance_decisions_before_flat_foam_row() -> None:
    workbench = build_estimating_workbench(sample_insulation_recommendation(), sample_insulation_data())
    for row in workbench["materials"]:
        if row.get("package_key") == "foam":
            row["include"] = True
            row["estimated_cost"] = 999000

    totals = summarize_workbench_totals(workbench)
    recalculated = recalculate_workbench_tables(workbench)
    expected_material = sum(
        row["estimated_cost"]
        for row in recalculated.get("insulation_performance_specs", [])
        if row.get("include")
    )

    assert expected_material > 0
    assert totals["material_total"] == round(expected_material, 2)
    assert totals["material_total"] != 999000


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


def test_low_evidence_adders_are_not_prefilled() -> None:
    workbench = build_estimating_workbench(sample_recommendation(), sample_data())
    adders = {row["adder_key"]: row for row in workbench["adders"]}

    assert adders["lift"]["include"] is False
    assert adders["lift"]["editable_value"] == 0
    assert adders["lift"]["estimated_cost"] == 0
    assert adders["lift"]["evidence_count"] == 1
    assert "Insufficient reliable history" in adders["lift"]["notes"]
    assert adders["generator"]["include"] is False
    assert adders["generator"]["editable_value"] == 0

    adders["lift"]["include"] = True
    recalculated = recalculate_workbench_tables({"scope": workbench["scope"], "materials": [], "labor": [], "adders": [adders["lift"]]})
    assert recalculated["adders"][0]["estimated_cost"] == 0


def test_insulation_adders_use_insulation_wording_and_suppress_hotel_outlier() -> None:
    workbench = build_estimating_workbench(sample_insulation_recommendation(), sample_insulation_data())
    adders = {row["adder_key"]: row for row in workbench["adders"]}

    assert "historical Insulation jobs" in adders["travel"]["notes"]
    assert "historical Roofing jobs" not in str(workbench["adders"])
    assert adders["hotel"]["median_cost_when_used"] == 55500
    assert adders["hotel"]["editable_value"] == 0
    assert adders["hotel"]["confidence"] == "low"
    assert "Insufficient reliable history" in adders["hotel"]["notes"]


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


def board_fastener_sample_data() -> EstimatorData:
    data = sample_data()
    data.pricing_catalog = pd.concat(
        [
            data.pricing_catalog,
            pd.DataFrame(
                [
                    {
                        "pricing_item_id": "BOARD1",
                        "product_name": "Dens Deck Cover Board 1/2 inch",
                        "category": "Board Stock",
                        "unit_price": 45,
                        "unit_of_measure": "square",
                        "is_current": True,
                    },
                    {
                        "pricing_item_id": "FAST1",
                        "product_name": "Roofing Fastener Screws",
                        "category": "Fasteners",
                        "unit_price": 100,
                        "unit_of_measure": "M",
                        "is_current": True,
                    },
                    {
                        "pricing_item_id": "PLATE1",
                        "product_name": "Insulation Plates",
                        "category": "Plates",
                        "unit_price": 80,
                        "unit_of_measure": "M",
                        "is_current": True,
                    },
                ]
            ),
        ],
        ignore_index=True,
    )
    data.job_package_summary = pd.concat(
        [
            data.job_package_summary,
            pd.DataFrame(
                [
                    {
                        "job_id": "B1",
                        "division": "Roofing",
                        "template_type": "roofing",
                        "project_type": "roof coating",
                        "substrate": "metal",
                        "package": "board_stock",
                        "item_name": "Dens Deck Cover Board 1/2 inch",
                        "area_sqft": 3200,
                        "total_quantity": 32,
                        "unit": "square",
                        "qty_per_sqft": 0.01,
                        "has_physical_quantity": True,
                    },
                    {
                        "job_id": "B1",
                        "division": "Roofing",
                        "template_type": "roofing",
                        "project_type": "roof coating",
                        "substrate": "metal",
                        "package": "fasteners",
                        "item_name": "Roofing Fastener Screws",
                        "area_sqft": 3200,
                        "total_quantity": 1200,
                        "unit": "ea",
                        "qty_per_sqft": 0.375,
                        "has_physical_quantity": True,
                    },
                    {
                        "job_id": "B1",
                        "division": "Roofing",
                        "template_type": "roofing",
                        "project_type": "roof coating",
                        "substrate": "metal",
                        "package": "plates",
                        "item_name": "Insulation Plates",
                        "area_sqft": 3200,
                        "total_quantity": 1200,
                        "unit": "ea",
                        "qty_per_sqft": 0.375,
                        "has_physical_quantity": True,
                    },
                ]
            ),
        ],
        ignore_index=True,
    )
    return data


def test_roofing_board_fastener_template_decisions_preserve_selector_and_follow_board_scope() -> None:
    recommendation = sample_recommendation()
    recommendation.parsed_fields["notes"] = "Replace wet insulation with Dens Deck cover board and use screws and plates."
    workbench = build_estimating_workbench(recommendation, board_fastener_sample_data())
    rows = workbench["roofing_board_fastener_template_decisions"]

    assert {row["workbook_row"] for row in rows} == {"58", "59", "60", "63", "65"}
    board_row = next(row for row in rows if row["workbook_row"] == "58")
    fastener_row = next(row for row in rows if row["workbook_row"] == "63")
    plate_row = next(row for row in rows if row["workbook_row"] == "65")

    assert board_row["include"] is True
    assert fastener_row["include"] is True
    assert plate_row["include"] is True
    assert any(option["resolved_template_option"] == "Dens Deck" for option in board_row["selector_options"])
    assert "Dens Deck" in board_row["selected_pricing_candidate"]
    assert "Fastener" in fastener_row["selected_pricing_candidate"]
    assert "Plate" in plate_row["selected_pricing_candidate"]


def test_roofing_board_fastener_template_decisions_recalculate_and_feed_workbook_inputs() -> None:
    recommendation = sample_recommendation()
    recommendation.parsed_fields["notes"] = "Replace wet insulation with cover board."
    workbench = build_estimating_workbench(recommendation, board_fastener_sample_data())
    for row in workbench["roofing_board_fastener_template_decisions"]:
        if row["workbook_row"] == "58":
            row.update(
                {
                    "include": True,
                    "editable_selector_code": "3",
                    "basis_sqft": 3200,
                    "thickness_inches": 0.5,
                    "price_per_square": 45,
                    "unit_price": 45,
                    "selected_pricing_candidate": "Dens Deck Cover Board 1/2 inch",
                }
            )
        elif row["workbook_row"] in {"59", "60"}:
            row["include"] = False
        elif row["workbook_row"] == "63":
            row.update(
                {
                    "include": True,
                    "board_area_sqft": 3200,
                    "unit_price": 100,
                    "unit_price_per_thousand": 100,
                    "selected_pricing_candidate": "Roofing Fastener Screws",
                }
            )
        elif row["workbook_row"] == "65":
            row.update(
                {
                    "include": True,
                    "board_area_sqft": 3200,
                    "unit_price": 80,
                    "unit_price_per_thousand": 80,
                    "selected_pricing_candidate": "Insulation Plates",
                }
            )

    recalculated = recalculate_workbench_tables(workbench)
    rows = {row["workbook_row"]: row for row in recalculated["roofing_board_fastener_template_decisions"]}

    assert rows["58"]["estimated_cost"] == 1440
    assert rows["63"]["estimated_units"] == 1200
    assert rows["63"]["estimated_cost"] == 120
    assert rows["65"]["estimated_units"] == 1200
    assert rows["65"]["estimated_cost"] == 96

    draft = workbench_to_draft_workbook_inputs(recalculated)
    material_by_row = {row["workbook_row"]: row for row in draft["material_rows"]}
    assert material_by_row["58"]["selector_code"] == "3"
    assert material_by_row["58"]["basis_sqft"] == 3200
    assert material_by_row["63"]["unit_price_per_thousand"] == 100
    assert material_by_row["65"]["unit_price_per_thousand"] == 80
    assert any(write["cell"] == "Estimate!A58" for write in material_by_row["58"]["workbook_cell_write_preview"])


def granules_sample_data() -> EstimatorData:
    data = sample_data()
    data.pricing_catalog = pd.concat(
        [
            data.pricing_catalog,
            pd.DataFrame(
                [
                    {
                        "pricing_item_id": "GRAN1",
                        "product_name": "3M LR9300 Roofing Granules",
                        "category": "Granules",
                        "unit_price": 42,
                        "unit_of_measure": "bag",
                        "is_current": True,
                    },
                    {
                        "pricing_item_id": "GRAN2",
                        "product_name": "SESCO Snow White Roofing Granules",
                        "category": "Granules",
                        "unit_price": 39,
                        "unit_of_measure": "bag",
                        "is_current": True,
                    },
                    {
                        "pricing_item_id": "BADGRAN",
                        "product_name": "White Silicone Roof Coating 55 Gal",
                        "category": "Coating",
                        "unit_price": 190,
                        "unit_of_measure": "gal",
                        "is_current": True,
                    },
                ]
            ),
        ],
        ignore_index=True,
    )
    data.job_package_summary = pd.concat(
        [
            data.job_package_summary,
            pd.DataFrame(
                [
                    {
                        "job_id": "G1",
                        "division": "Roofing",
                        "template_type": "roofing",
                        "project_type": "roof coating",
                        "substrate": "metal",
                        "package": "granules",
                        "item_name": "3M LR9300 Roofing Granules",
                        "area_sqft": 10000,
                        "total_quantity": 50,
                        "unit": "bag",
                        "qty_per_sqft": 0.005,
                        "has_physical_quantity": True,
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    return data


def test_roofing_granules_template_decision_uses_selector_options_and_separate_candidates() -> None:
    recommendation = sample_recommendation()
    recommendation.parsed_fields["notes"] = "Add broadcast granules to the coating walkway area."
    workbench = build_estimating_workbench(recommendation, granules_sample_data())

    decisions = workbench["roofing_granules_template_decisions"]
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision["include"] is True
    assert decision["workbook_row"] == "36"
    assert {option["resolved_template_option"] for option in decision["selector_options"]} >= {"3M", "SESCO"}
    assert decision["resolved_template_option"] in {"3M", "SESCO"}
    assert "Granules" in decision["selected_pricing_candidate"]
    assert "Silicone Roof Coating" not in decision["selected_pricing_candidate"]
    assert decision["selected_pricing_candidate"] != decision["resolved_template_option"]


def test_roofing_granules_template_decision_recalculates_and_feeds_workbook_inputs() -> None:
    recommendation = sample_recommendation()
    recommendation.parsed_fields["notes"] = "Broadcast SESCO granules over the coating."
    workbench = build_estimating_workbench(recommendation, granules_sample_data())
    workbench["roofing_granules_template_decisions"][0].update(
        {
            "include": True,
            "editable_selector_code": "2",
            "basis_sqft": 12000,
            "coverage_lbs_per_100_sqft": 50,
            "bag_weight_lbs": 100,
            "unit_price": 40,
            "selected_pricing_candidate": "SESCO Snow White Roofing Granules",
        }
    )

    recalculated = recalculate_workbench_tables(workbench)
    decision = recalculated["roofing_granules_template_decisions"][0]
    assert decision["resolved_template_option"] == "SESCO"
    assert decision["estimated_units"] == 60
    assert decision["estimated_cost"] == 2400

    draft = workbench_to_draft_workbook_inputs(recalculated)
    granules_rows = [row for row in draft["material_rows"] if row["category"] == "granules"]
    assert len(granules_rows) == 1
    granules = granules_rows[0]
    assert granules["workbook_row"] == "36"
    assert granules["selector_code"] == "2"
    assert granules["basis_sqft"] == 12000
    assert granules["coverage_lbs_per_100_sqft"] == 50
    assert granules["bag_weight_lbs"] == 100
    assert granules["quantity"] == 60
    assert any(write["cell"] == "Estimate!A36" and write["value"] == "2" for write in granules["workbook_cell_write_preview"])


def test_roofing_equipment_template_decisions_use_selector_options_and_note_triggers() -> None:
    recommendation = sample_recommendation()
    recommendation.parsed_fields["notes"] = "Tear off wet insulation, include 40 yard dumpster, boom lift, and generator."
    workbench = build_estimating_workbench(recommendation, sample_data())

    rows = {row["workbook_row"]: row for row in workbench["roofing_equipment_template_decisions"]}
    assert {"69", "73", "74", "99"}.issubset(rows)
    assert rows["69"]["include"] is True
    assert rows["69"]["resolved_template_option"] == "40 Yard"
    assert {option["resolved_template_option"] for option in rows["69"]["selector_options"]} >= {"20 Yard", "30 Yard", "40 Yard"}
    assert rows["73"]["include"] is True
    assert rows["73"]["resolved_template_option"] == "Boom"
    assert {option["resolved_template_option"] for option in rows["73"]["selector_options"]} >= {"Forklift", "Boom", "Scissor", "Articulating"}
    assert rows["74"]["include"] is False
    assert rows["99"]["include"] is True


def test_roofing_equipment_template_decisions_recalculate_and_feed_workbook_inputs() -> None:
    recommendation = sample_recommendation()
    recommendation.parsed_fields["notes"] = "Tear off wet insulation, include dumpster, lift, and generator."
    workbench = build_estimating_workbench(recommendation, sample_data())
    for row in workbench["roofing_equipment_template_decisions"]:
        if row["workbook_row"] == "69":
            row.update(
                {
                    "include": True,
                    "editable_selector_code": "3",
                    "basis_sqft": 14000,
                    "thickness_inches": 2,
                    "unit_price": 400,
                    "margin_pct": 25,
                }
            )
        elif row["workbook_row"] == "73":
            row.update(
                {
                    "include": True,
                    "editable_selector_code": "2",
                    "size": "60'",
                    "period": 5,
                    "unit_price": 600,
                    "margin_pct": 20,
                }
            )
        elif row["workbook_row"] == "74":
            row["include"] = False
        elif row["workbook_row"] == "99":
            row.update({"include": True, "days": 7, "unit_price": 50})

    recalculated = recalculate_workbench_tables(workbench)
    rows = {row["workbook_row"]: row for row in recalculated["roofing_equipment_template_decisions"]}
    assert rows["69"]["estimated_units"] == 2.083333
    assert rows["69"]["estimated_cost"] == 833.33
    assert rows["73"]["estimated_cost"] == 3600
    assert rows["99"]["estimated_cost"] == 350

    draft = workbench_to_draft_workbook_inputs(recalculated)
    equipment_rows = {row["workbook_row"]: row for row in draft["material_rows"] if row["category"] in {"dumpster", "lift", "generator"}}
    assert {"69", "73", "99"}.issubset(equipment_rows)
    assert equipment_rows["69"]["selector_code"] == "3"
    assert equipment_rows["69"]["basis_sqft"] == 14000
    assert equipment_rows["69"]["thickness_inches"] == 2
    assert equipment_rows["73"]["selector_code"] == "2"
    assert equipment_rows["73"]["period"] == 5
    assert equipment_rows["99"]["days"] == 7
    assert not any(row["category"] in {"dumpster", "lift", "generator"} for row in draft["adders_review_rows"])
    assert any(write["cell"] == "Estimate!A69" and write["value"] == "3" for write in equipment_rows["69"]["workbook_cell_write_preview"])


def test_roofing_travel_freight_template_decisions_recalculate_and_feed_workbook_inputs() -> None:
    recommendation = sample_recommendation()
    recommendation.parsed_fields["notes"] = "Include delivery fee, freight, sales inspection trips, and truck expense miles."
    workbench = build_estimating_workbench(recommendation, sample_data())
    for row in workbench["roofing_travel_freight_template_decisions"]:
        if row["workbook_row"] == "76":
            row.update({"include": True, "estimated_units": 2, "unit_price": 150})
        elif row["workbook_row"] == "103":
            row.update({"include": True, "amount": 425, "unit_price": 425})
        elif row["workbook_row"] == "106":
            row.update({"include": True, "trip_count": 3, "round_trip_miles": 40, "unit_price": 0.75})
        elif row["workbook_row"] == "108":
            row.update({"include": True, "trip_count": 4, "round_trip_miles": 50, "unit_price": 1.25})

    recalculated = recalculate_workbench_tables(workbench)
    rows = {row["workbook_row"]: row for row in recalculated["roofing_travel_freight_template_decisions"]}
    assert rows["76"]["estimated_cost"] == 300
    assert rows["103"]["estimated_cost"] == 425
    assert rows["106"]["estimated_cost"] == 90
    assert rows["108"]["estimated_cost"] == 250

    draft = workbench_to_draft_workbook_inputs(recalculated)
    travel_rows = {
        row["workbook_row"]: row
        for row in draft["material_rows"]
        if row["category"] in {"delivery_fee", "freight", "sales_trips", "truck_expense"}
    }
    assert {"76", "103", "106", "108"}.issubset(travel_rows)
    assert travel_rows["76"]["estimated_units"] == 2
    assert travel_rows["103"]["amount"] == 425
    assert travel_rows["106"]["trip_count"] == 3
    assert travel_rows["106"]["round_trip_miles"] == 40
    assert travel_rows["108"]["unit_price"] == 1.25
    assert not any(
        row["category"] in {"delivery_fee", "freight", "sales_trips", "inspection", "truck_expense", "travel"}
        for row in draft["adders_review_rows"]
    )
    assert any(write["cell"] == "Estimate!B106" and write["value"] == 3 for write in travel_rows["106"]["workbook_cell_write_preview"])
    assert any(write["cell"] == "Estimate!C108" and write["value"] == 50 for write in travel_rows["108"]["workbook_cell_write_preview"])


def test_roofing_accessory_template_decisions_recalculate_and_feed_workbook_inputs() -> None:
    recommendation = sample_recommendation()
    recommendation.parsed_fields["notes"] = "Use xylene thinner, replace edge metal, include roof hatch, and add misc allowance."
    workbench = build_estimating_workbench(recommendation, sample_data())
    for row in workbench["roofing_accessory_template_decisions"]:
        if row["workbook_row"] == "33":
            row.update({"include": True, "editable_selector_code": "3", "total_coating_gallons": 220, "unit_price": 12.5})
        elif row["workbook_row"] == "82":
            row.update({"include": True, "linear_ft": 100, "unit_price": 15})
        elif row["workbook_row"] == "88":
            row.update({"include": True, "estimated_units": 2, "unit_price": 300})
        elif row["workbook_row"] == "101":
            row.update({"include": True, "amount": 275, "unit_price": 275})
        else:
            row["include"] = False

    recalculated = recalculate_workbench_tables(workbench)
    rows = {row["workbook_row"]: row for row in recalculated["roofing_accessory_template_decisions"]}
    assert rows["33"]["resolved_template_option"] == "Xylene"
    assert rows["33"]["estimated_units"] == 16
    assert rows["33"]["estimated_cost"] == 200
    assert rows["82"]["estimated_cost"] == 1500
    assert rows["88"]["estimated_cost"] == 600
    assert rows["101"]["estimated_cost"] == 275

    draft = workbench_to_draft_workbook_inputs(recalculated)
    accessory_rows = {
        row["workbook_row"]: row
        for row in draft["material_rows"]
        if row["category"] in {"thinner", "edge_metal", "roof_hatch", "misc"}
    }
    assert {"33", "82", "88", "101"}.issubset(accessory_rows)
    assert accessory_rows["33"]["selector_code"] == "3"
    assert accessory_rows["33"]["total_coating_gallons"] == 220
    assert accessory_rows["82"]["linear_ft"] == 100
    assert accessory_rows["88"]["estimated_units"] == 2
    assert accessory_rows["101"]["amount"] == 275
    assert any(write["cell"] == "Estimate!A33" and write["value"] == "3" for write in accessory_rows["33"]["workbook_cell_write_preview"])
    assert any(write["cell"] == "Estimate!C82" and write["value"] == 100 for write in accessory_rows["82"]["workbook_cell_write_preview"])


def test_roofing_detail_quantity_template_decisions_recalculate_and_feed_workbook_inputs() -> None:
    recommendation = sample_recommendation()
    recommendation.parsed_fields["notes"] = "Open seams, 12 penetrations, two HVAC units, and 4 roof drains."
    workbench = build_estimating_workbench(recommendation, sample_data())

    rows = {row["workbook_row"]: row for row in workbench["roofing_detail_quantity_template_decisions"]}
    assert {"47", "49", "51", "53"}.issubset(rows)
    assert rows["47"]["include"] is True
    assert rows["49"]["include"] is True
    assert rows["51"]["include"] is True
    assert rows["53"]["include"] is True

    for row in workbench["roofing_detail_quantity_template_decisions"]:
        if row["workbook_row"] == "47":
            row.update({"include": True, "linear_ft": 240, "amount": 1200})
        elif row["workbook_row"] == "49":
            row.update({"include": True, "units": 12, "amount": 600})
        elif row["workbook_row"] == "51":
            row.update({"include": True, "units": 2, "amount": 300})
        elif row["workbook_row"] == "53":
            row.update({"include": True, "units": 4, "amount": 400})

    recalculated = recalculate_workbench_tables(workbench)
    rows = {row["workbook_row"]: row for row in recalculated["roofing_detail_quantity_template_decisions"]}
    assert rows["47"]["linear_ft"] == 240
    assert rows["47"]["estimated_cost"] == 1200
    assert rows["49"]["estimated_units"] == 12
    assert rows["51"]["estimated_units"] == 2
    assert rows["53"]["estimated_units"] == 4
    assert rows["49"]["formula_model"] == "manual_detail_quantity_cost"

    draft = workbench_to_draft_workbook_inputs(recalculated)
    detail_rows = {
        row["workbook_row"]: row
        for row in draft["material_rows"]
        if row["category"] in {"seams_misc", "penetrations", "hvac_units", "drains"}
    }
    assert {"47", "49", "51", "53"}.issubset(detail_rows)
    assert detail_rows["47"]["linear_ft"] == 240
    assert detail_rows["49"]["estimated_units"] == 12
    assert detail_rows["51"]["estimated_units"] == 2
    assert detail_rows["53"]["estimated_units"] == 4
    assert any(write["cell"] == "Estimate!C47" and write["value"] == 240 for write in detail_rows["47"]["workbook_cell_write_preview"])
    assert any(write["cell"] == "Estimate!D49" and write["value"] == 12 for write in detail_rows["49"]["workbook_cell_write_preview"])
    assert any(write["cell"] == "Estimate!D51" and write["value"] == 2 for write in detail_rows["51"]["workbook_cell_write_preview"])
    assert any(write["cell"] == "Estimate!D53" and write["value"] == 4 for write in detail_rows["53"]["workbook_cell_write_preview"])


def test_roofing_labor_template_decisions_expose_people_selector_options() -> None:
    workbench = build_estimating_workbench(sample_recommendation(), sample_data())

    decisions = {row["template_bucket"]: row for row in workbench["roofing_labor_template_decisions"]}
    assert "labor_base" in decisions
    labor_base = decisions["labor_base"]
    assert labor_base["workbook_row"] == "122"
    assert labor_base["include"] is True
    assert {option["selector_code"] for option in labor_base["crew_selector_options"]} >= {"1", "4", "8"}
    assert "person crew daily rate" in labor_base["crew_selection"]
    assert labor_base["selected_daily_rate_cell"].startswith("People!")


def test_roofing_labor_template_decisions_recalculate_and_feed_workbook_inputs() -> None:
    workbench = build_estimating_workbench(sample_recommendation(), sample_data())
    for row in workbench["roofing_labor_template_decisions"]:
        if row["template_bucket"] == "labor_base":
            row.update(
                {
                    "include": True,
                    "days": 2,
                    "crew_size": 4,
                    "crew_people_selection": 4,
                    "daily_rate": 1600,
                    "hourly_rate": 90,
                    "total_hours": 40,
                    "editable_total_hours": 40,
                    "formula_mode": "mixed_formula",
                }
            )
        else:
            row["include"] = False

    recalculated = recalculate_workbench_tables(workbench)
    decision = next(row for row in recalculated["roofing_labor_template_decisions"] if row["template_bucket"] == "labor_base")
    assert decision["estimated_cost"] == 3600
    assert decision["formula_source"] == "hours_hourly_rate"
    assert any(write["cell"] == "Estimate!D122" and write["value"] == 90 for write in decision["workbook_cell_write_preview"])
    assert any(write["cell"] == "Estimate!G122" and write["value"] == 40 for write in decision["workbook_cell_write_preview"])

    draft = workbench_to_draft_workbook_inputs(recalculated)
    labor_rows = {row["task"]: row for row in draft["labor_rows"]}
    assert labor_rows["labor_base"]["base_days"] == 2
    assert labor_rows["labor_base"]["adjusted_days"] == 2
    assert labor_rows["labor_base"]["crew_size"] == 4
    assert labor_rows["labor_base"]["hourly_rate"] == 90
    assert labor_rows["labor_base"]["total_hours"] == 40
    assert labor_rows["labor_base"]["estimated_cost"] == 3600
