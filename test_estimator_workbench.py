from __future__ import annotations

import pandas as pd

from jobscan.estimator.schemas import EstimateRecommendation, EstimatorData
from jobscan.estimator.workbench import (
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
                    "median_qty_per_sqft": 0.02,
                    "evidence_count": 12,
                    "confidence": "high",
                },
                {
                    "division": "Roofing",
                    "template_type": "roofing",
                    "package": "primer",
                    "unit": "pail",
                    "median_qty_per_sqft": 0.001,
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
                    "product_name": "High Solids Silicone Coating",
                    "category": "Coating",
                    "price_per_gallon": 38,
                    "unit_price": 190,
                    "is_current": True,
                }
            ]
        ),
    )


def test_workbench_populates_common_editable_rows_from_relationship_tables() -> None:
    workbench = build_estimating_workbench(sample_recommendation(), sample_data())

    material_packages = {row["package_key"]: row for row in workbench["materials"]}
    labor_packages = {row["package_key"]: row for row in workbench["labor"]}

    assert {"coating", "primer", "seam_treatment", "fastener_treatment", "caulk_detail"}.issubset(material_packages)
    assert material_packages["coating"]["include"] is True
    assert material_packages["coating"]["historical_qty_per_sqft"] == 0.02
    assert material_packages["coating"]["calculated_quantity"] == 200
    assert material_packages["coating"]["estimated_cost"] == 7600
    assert material_packages["primer"]["include"] is False
    assert labor_packages["labor_base"]["include"] is True
    assert labor_packages["labor_base"]["historical_hours_per_1000_sqft"] == 4.5


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


def test_edit_history_flags_large_material_and_labor_changes() -> None:
    original = build_estimating_workbench(sample_recommendation(), sample_data())
    edited = build_estimating_workbench(sample_recommendation(), sample_data())
    for row in edited["materials"]:
        if row["package_key"] == "coating":
            row["editable_qty_per_sqft"] = 0.04
    for row in edited["labor"]:
        if row["package_key"] == "labor_base":
            row["editable_hours_per_1000_sqft"] = 7.0

    rows = build_edit_history_rows(original, edited)
    required = [row for row in rows if row["reason_required"]]

    assert any(row["section"] == "materials.coating" for row in required)
    assert any(row["section"] == "labor.labor_base" for row in required)
