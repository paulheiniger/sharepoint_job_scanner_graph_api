from __future__ import annotations

from jobscan.estimator.workbook_recommendations import (
    apply_api_planning_guidance,
    normalize_template_material_pricing,
)


def test_roofing_foam_set_price_is_converted_to_template_price_per_pound() -> None:
    payload = {
        "template_type": "roofing",
        "materials": [
            {
                "category": "roofing_foam",
                "item": "Gaco Roof 2.7",
                "unit_price": 2_100,
            },
            {"category": "coating", "item": "Silicone", "unit_price": 51.5},
        ],
    }

    prepared, warnings = normalize_template_material_pricing(payload)

    assert prepared["materials"][0]["unit_price"] == 2.1
    assert prepared["materials"][0]["price_per_set"] == 2_100
    assert prepared["materials"][1]["unit_price"] == 51.5
    assert any("$2,100.00/set" in warning for warning in warnings)


def test_explicit_roofing_foam_set_price_overrides_template_unit_price() -> None:
    prepared, _warnings = normalize_template_material_pricing(
        {
            "template_type": "roofing",
            "materials": [
                {
                    "category": "roofing_foam",
                    "unit_price": 9_999,
                    "price_per_set": 2_150,
                }
            ],
        }
    )

    assert prepared["materials"][0]["unit_price"] == 2.15


def test_api_plan_replaces_agent_labor_drift_and_duplicate_catch_all() -> None:
    payload = {
        "template_type": "roofing",
        "labor": [
            {
                "task": "labor_full_repair",
                "days": 1.25,
                "crew_size": 5,
            },
            {"task": "labor_prep", "days": 2, "crew_size": 5},
            {"task": "labor_tearoff", "days": 2, "crew_size": 6},
            {"task": "labor_board", "days": 2, "crew_size": 6},
            {"task": "labor_base", "days": 2, "crew_size": 5},
            {"task": "labor_top_coat", "days": 1, "crew_size": 5},
            {"task": "labor_cleanup", "days": 0.5, "crew_size": 5},
            {"task": "labor_details", "days": 1, "crew_size": 5},
            {
                "task": "labor_loading",
                "hours_per_trip": 4,
                "crew_size": 4,
            },
            {
                "task": "labor_traveling",
                "hours_per_trip": 2,
                "crew_size": 6,
            },
        ],
        "logistics": [
            {
                "category": "sales_inspection_trips",
                "item": "Sales travel",
                "trip_count": 3,
                "round_trip_miles": 62,
            },
            {
                "category": "truck_expense",
                "item": "Truck travel",
                "trip_count": 9,
                "round_trip_miles": 62,
            },
        ],
    }
    context = {
        "labor_plan_guidance": [
            _labor_guidance("labor_prep", 0.52, 5, 26.2),
            _labor_guidance("labor_tearoff", 1.2, 6, 75.6),
            _labor_guidance("labor_board", 1.24, 6, 78),
            _labor_guidance("labor_base", 1.4, 5, 69.8),
            _labor_guidance("labor_top_coat", 0.7, 5, 34.9),
            _labor_guidance("labor_cleanup", 0.17, 5, 8.7),
        ],
        "logistics_guidance": [
            {
                "category": "sales_inspection_trips",
                "include": True,
                "recommended_trip_count": 2,
                "round_trip_miles": 62,
            },
            {
                "category": "truck_expense",
                "include": True,
                "recommended_trip_count": 6,
                "round_trip_miles": 62,
            },
            {
                "category": "labor_loading",
                "include": True,
                "recommended_hours_per_trip": 1.98,
                "recommended_crew_size": 2,
                "estimated_total_person_hours": 23.8,
            },
            {
                "category": "labor_traveling",
                "include": True,
                "recommended_hours_per_trip": 1.33,
                "recommended_crew_size": 5,
                "estimated_total_person_hours": 39.9,
            },
        ],
    }

    prepared, warnings = apply_api_planning_guidance(payload, context)

    labor = {row["task"]: row for row in prepared["labor"]}
    assert "labor_full_repair" not in labor
    assert labor["labor_prep"]["days"] == 0.52
    assert labor["labor_tearoff"]["days"] == 1.2
    assert labor["labor_board"]["days"] == 1.24
    assert labor["labor_base"]["days"] == 1.4
    assert labor["labor_top_coat"]["days"] == 0.7
    assert labor["labor_cleanup"]["days"] == 0.17
    assert labor["labor_loading"]["hours_per_trip"] == 1.98
    assert labor["labor_loading"]["crew_size"] == 2
    assert labor["labor_traveling"]["hours_per_trip"] == 1.33
    assert labor["labor_traveling"]["crew_size"] == 5
    assert labor["labor_details"]["days"] == 1

    logistics = {row["category"]: row for row in prepared["logistics"]}
    assert logistics["sales_inspection_trips"]["trip_count"] == 2
    assert logistics["truck_expense"]["trip_count"] == 6
    assert any("catch-all" in warning for warning in warnings)


def test_logistics_only_guidance_does_not_remove_production_labor() -> None:
    payload = {
        "template_type": "roofing",
        "labor": [
            {"task": "labor_full_repair", "days": 1, "crew_size": 3},
            {
                "task": "labor_loading",
                "hours_per_trip": 2,
                "crew_size": 2,
            },
        ],
        "logistics": [],
    }
    context = {
        "labor_plan_guidance": [],
        "logistics_guidance": [
            {
                "category": "labor_loading",
                "include": True,
                "recommended_hours_per_trip": 1.5,
                "recommended_crew_size": 2,
            }
        ],
    }

    prepared, _warnings = apply_api_planning_guidance(payload, context)

    labor = {row["task"]: row for row in prepared["labor"]}
    assert labor["labor_full_repair"]["days"] == 1
    assert labor["labor_loading"]["hours_per_trip"] == 1.5


def _labor_guidance(
    category: str,
    days: float,
    crew_size: int,
    hours: float,
) -> dict:
    return {
        "category": category,
        "activity": category.removeprefix("labor_").replace("_", " ").title(),
        "recommended_days": days,
        "recommended_crew_size": crew_size,
        "recommended_total_hours": hours,
        "current_people_daily_rate": 1_500,
        "estimated_labor_cost_candidate": days * 1_500,
        "calibration_status": "calibrated_candidate",
    }
