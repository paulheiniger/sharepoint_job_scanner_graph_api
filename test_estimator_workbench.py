from __future__ import annotations

import pandas as pd

from jobscan.estimator.insulation_diagnostics import build_insulation_history_diagnostics, write_insulation_history_diagnostics
from jobscan.estimator.schemas import EstimateRecommendation, EstimatorData
from jobscan.estimator.workbench import (
    apply_historical_filter_update,
    build_edit_history_rows,
    build_estimating_workbench,
    historical_filters_from_scope,
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
    assert labor_packages["labor_prime"]["include"] is False
    assert labor_packages["labor_prime"]["estimated_cost"] == 0

    totals = recalculate_workbench_tables(workbench)
    total_cost = sum(row["estimated_cost"] for row in totals["materials"] if row["include"])
    total_labor = sum(row["estimated_cost"] for row in totals["labor"] if row["include"])
    assert total_cost == material_packages["coating"]["estimated_cost"]
    assert total_labor > 0
    assert total_labor >= labor_packages["labor_base"]["estimated_cost"]


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
