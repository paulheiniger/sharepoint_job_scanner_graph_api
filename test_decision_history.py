from __future__ import annotations

import pandas as pd

from jobscan.estimator.decision_history import (
    build_decision_recommendations,
    build_historical_decision_tables,
    recommendation_lookup,
)
from jobscan.estimator.schemas import EstimateRecommendation, EstimatorData
from jobscan.estimator.workbench import build_estimating_workbench


def decision_history_data() -> EstimatorData:
    template_rows = []
    for idx, thickness in enumerate([2.0, 2.0, 3.0, 2.5, 2.0], start=1):
        template_rows.append(
            {
                "job_id": f"IF{idx}",
                "source_file": f"2026/insulation_{idx}.xlsx",
                "division": "Insulation",
                "template_type": "insulation",
                "project_type": "spray foam insulation",
                "building_type": "metal building",
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
                "estimated_units": 2400 * thickness * (1000 / 13500),
                "estimated_sets": (2400 * thickness * (1000 / 13500)) / 1000,
                "estimated_cost": 1200,
                "unit_price": 1.6,
                "formula_model": "foam_sets_from_area_thickness_yield",
            }
        )
    for idx, gal_rate in enumerate([1.0, 1.0, 1.5, 1.0, 2.0], start=1):
        template_rows.append(
            {
                "job_id": f"RC{idx}",
                "source_file": f"2026/roofing_{idx}.xlsx",
                "division": "Roofing",
                "template_type": "roofing",
                "project_type": "roof coating",
                "substrate": "metal",
                "coating_type": "silicone",
                "warranty_years": 10,
                "sheet_name": "Estimate",
                "row_number": 26,
                "template_bucket": "coating",
                "line_item_kind": "material",
                "selector_code": 11,
                "resolved_item_name": "Gaco Silicone",
                "area_sqft": 10000,
                "gal_per_100_sqft": gal_rate,
                "gal_per_sqft": gal_rate / 100,
                "estimated_gallons": 10000 * gal_rate / 100,
                "estimated_cost": 3000,
                "unit_price": 30,
                "formula_model": "coating_gallons_from_area_rate_waste",
            }
        )
    for idx, days in enumerate([1.0, 1.5, 1.0, 2.0, 1.0], start=1):
        template_rows.append(
            {
                "job_id": f"IL{idx}",
                "source_file": f"2026/insulation_labor_{idx}.xlsx",
                "division": "Insulation",
                "template_type": "insulation",
                "project_type": "spray foam insulation",
                "building_type": "metal building",
                "sheet_name": "Estimate",
                "row_number": 86,
                "template_bucket": "labor_foam",
                "line_item_kind": "labor",
                "days": days,
                "crew_size": 3,
                "crew_selector_code": 3,
                "total_hours": days * 30,
                "daily_rate": 1350,
                "hourly_rate": 45,
                "calculated_cost": days * 30 * 45,
                "formula_mode": "mixed_formula",
                "formula_model": "labor_cost_from_days_crew_rate",
            }
        )
    return EstimatorData(template_rows=pd.DataFrame(template_rows))


def test_historical_decision_tables_mine_template_row_decisions() -> None:
    tables = build_historical_decision_tables(decision_history_data())

    assert set(tables) >= {
        "insulation_foam_decision_history",
        "insulation_labor_decision_history",
        "roofing_coating_decision_history",
        "roofing_labor_decision_history",
        "equipment_decision_history",
    }
    foam = tables["insulation_foam_decision_history"]
    assert len(foam) == 5
    assert foam["decision_id"].eq("insulation_foam_system").all()
    assert {"selector_code", "resolved_item_name", "area_basis_sqft", "thickness_inches", "yield_or_coverage", "estimated_units"}.issubset(
        foam.columns
    )

    coating = tables["roofing_coating_decision_history"]
    assert len(coating) == 5
    assert coating["wet_mils_estimate"].median() == 16

    labor = tables["insulation_labor_decision_history"]
    assert labor["template_bucket"].eq("labor_foam").all()
    assert labor["formula_mode"].eq("mixed_formula").all()


def test_decision_recommendations_use_modes_and_medians() -> None:
    recommendations = build_decision_recommendations(
        decision_history_data(),
        filters={"division": "Insulation", "template_type": "insulation", "building_type": "metal building"},
    )
    lookup = recommendation_lookup(recommendations)

    assert lookup[("insulation_foam_system", "resolved_item_name")]["recommended_value"] == "Gaco 2.0 lb."
    assert lookup[("insulation_foam_system", "thickness_inches")]["median"] == 2.0
    assert lookup[("insulation_foam_system", "yield_or_coverage")]["median"] == 13500
    assert lookup[("insulation_labor_foam", "days")]["median"] == 1.0
    assert lookup[("insulation_labor_foam", "crew_size")]["median"] == 3
    assert lookup[("insulation_labor_foam", "formula_mode")]["recommended_value"] == "mixed_formula"


def test_workbench_surfaces_decision_first_defaults() -> None:
    recommendation = EstimateRecommendation(
        parsed_fields={
            "division": "Insulation",
            "template_type": "insulation",
            "project_type": "spray foam insulation",
            "building_type": "metal building",
            "net_insulation_area_sqft": 2400,
            "estimated_sqft": 2400,
            "notes": "Spray foam insulation in metal building.",
        },
        recommended_scope=[],
        material_plan=[{"category": "foam", "included_in_total": True}],
        labor_plan=[{"task": "labor_foam", "included_in_total": True}],
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

    workbench = build_estimating_workbench(recommendation, decision_history_data())
    foam = next(row for row in workbench["materials"] if row["package_key"] == "foam")
    labor = next(row for row in workbench["labor"] if row["package_key"] == "labor_foam")

    assert foam["decision_id"] == "insulation_foam_system"
    assert foam["decision_evidence_count"] >= 5
    assert foam["thickness_inches"] == 2.0
    assert foam["yield_factor"] == 13500
    assert foam["workbook_rows_controlled"] == "19-21"
    assert labor["decision_id"] == "insulation_labor_foam"
    assert labor["days"] == 1.0
    assert labor["crew_people_selection"] == 3
    assert labor["formula_mode"] == "mixed_formula"
