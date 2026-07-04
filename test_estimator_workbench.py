from __future__ import annotations

from copy import deepcopy

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
