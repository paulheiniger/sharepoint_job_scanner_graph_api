from __future__ import annotations

from copy import deepcopy
from typing import Any


PRODUCTION_LABOR_TASKS = {
    "labor_prep",
    "labor_tearoff",
    "labor_board",
    "labor_base",
    "labor_caulk",
    "labor_details",
    "labor_top_coat",
    "labor_cleanup",
}
PER_TRIP_LABOR_TASKS = {"labor_loading", "labor_traveling"}
PLANNED_TRAVEL_CATEGORIES = {"sales_inspection_trips", "truck_expense"}
ROOFING_FOAM_SET_WEIGHT_LBS = 1_000.0


def normalize_template_material_pricing(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Normalize semantic material prices to the template row's price unit."""

    prepared = deepcopy(payload)
    if str(prepared.get("template_type") or "roofing").lower() != "roofing":
        return prepared, []
    warnings: list[str] = []
    for item in prepared.get("materials") or []:
        if not isinstance(item, dict) or item.get("category") != "roofing_foam":
            continue
        price_per_set = _positive_number(item.get("price_per_set"))
        unit_price = _positive_number(item.get("unit_price"))
        source = "price_per_set"
        if price_per_set is None and unit_price is not None and unit_price >= 100:
            price_per_set = unit_price
            source = "legacy unit_price interpreted as price per set"
        if price_per_set is None:
            continue
        price_per_lb = round(price_per_set / ROOFING_FOAM_SET_WEIGHT_LBS, 4)
        item["unit_price"] = price_per_lb
        item["price_per_set"] = price_per_set
        item["notes"] = (
            f"{str(item.get('notes') or '').strip()} Roofing foam price normalized "
            f"from ${price_per_set:,.2f} per 1,000-pound set to "
            f"${price_per_lb:,.4f} per pound for the template."
        ).strip()
        warnings.append(
            "Roofing foam pricing was converted from "
            f"{source} (${price_per_set:,.2f}/set) to the template input "
            f"(${price_per_lb:,.4f}/lb)."
        )
    return prepared, warnings


def apply_api_planning_guidance(
    payload: dict[str, Any],
    planning_context: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Apply the API's deterministic labor plan before workbook generation.

    The conversational agent may explain or review this plan, but it must not
    silently replace calibrated activities with unrelated days and crew sizes.
    Explicit, reasoned estimator overrides are handled by the request schema and
    bypass this function in the HTTP service.
    """

    prepared = deepcopy(payload)
    if str(prepared.get("template_type") or "roofing").lower() != "roofing":
        return prepared, []

    labor_guidance = [
        row
        for row in planning_context.get("labor_plan_guidance") or []
        if isinstance(row, dict)
    ]
    logistics_guidance = [
        row
        for row in planning_context.get("logistics_guidance") or []
        if isinstance(row, dict)
    ]
    submitted_labor = [
        dict(row) for row in prepared.get("labor") or [] if isinstance(row, dict)
    ]
    submitted_by_task = {
        str(row.get("task") or "").strip(): row for row in submitted_labor
    }

    recommended_by_task: dict[str, dict[str, Any]] = {}
    for guidance in labor_guidance:
        task = str(guidance.get("category") or "").strip()
        if task not in PRODUCTION_LABOR_TASKS:
            continue
        days = _positive_number(guidance.get("recommended_days"))
        crew_size = _supported_crew_size(guidance.get("recommended_crew_size"))
        if days is None or crew_size is None:
            continue
        recommended_by_task[task] = _recommended_labor_row(
            submitted_by_task.get(task),
            guidance,
            task=task,
            days=days,
            crew_size=crew_size,
        )

    for guidance in logistics_guidance:
        task = str(guidance.get("category") or "").strip()
        if task not in PER_TRIP_LABOR_TASKS:
            continue
        hours_per_trip = _positive_number(
            guidance.get("recommended_hours_per_trip")
        )
        crew_size = _supported_crew_size(guidance.get("recommended_crew_size"))
        if hours_per_trip is None or crew_size is None:
            continue
        row = dict(submitted_by_task.get(task) or {})
        row.update(
            {
                "concept_id": row.get("concept_id") or task,
                "task": task,
                "label": row.get("label") or _activity_label(task),
                "include": bool(guidance.get("include", True)),
                "crew_size": crew_size,
                "hours_per_trip": round(hours_per_trip, 2),
                "total_hours": guidance.get("estimated_total_person_hours"),
                "estimated_cost": guidance.get("estimated_labor_cost_candidate"),
                "notes": _guidance_note(row.get("notes"), guidance),
            }
        )
        recommended_by_task[task] = row

    if not recommended_by_task:
        return prepared, [
            "API labor preservation was requested, but no complete labor "
            "recommendations were available; submitted labor was retained."
        ]

    has_component_labor_plan = any(
        task in PRODUCTION_LABOR_TASKS for task in recommended_by_task
    )
    output_labor: list[dict[str, Any]] = []
    inserted: set[str] = set()
    dropped_full_repair = False
    for row in submitted_labor:
        task = str(row.get("task") or "").strip()
        if task == "labor_full_repair" and has_component_labor_plan:
            dropped_full_repair = True
            continue
        if task in recommended_by_task:
            if task not in inserted:
                output_labor.append(recommended_by_task[task])
                inserted.add(task)
            continue
        output_labor.append(row)
    for task, row in recommended_by_task.items():
        if task not in inserted:
            output_labor.append(row)
    prepared["labor"] = output_labor

    changed_logistics: list[str] = []
    submitted_logistics = [
        dict(row)
        for row in prepared.get("logistics") or []
        if isinstance(row, dict)
    ]
    logistics_by_category = {
        str(row.get("category") or "").strip(): row for row in submitted_logistics
    }
    for guidance in logistics_guidance:
        category = str(guidance.get("category") or "").strip()
        if category not in PLANNED_TRAVEL_CATEGORIES:
            continue
        trip_count = _positive_number(guidance.get("recommended_trip_count"))
        if trip_count is None:
            continue
        row = logistics_by_category.get(category)
        if row is None:
            row = {
                "concept_id": category,
                "category": category,
                "item": _activity_label(category),
            }
            submitted_logistics.append(row)
            logistics_by_category[category] = row
        row["include"] = bool(guidance.get("include", True))
        row["trip_count"] = round(trip_count, 2)
        round_trip_miles = _positive_number(guidance.get("round_trip_miles"))
        if round_trip_miles is not None:
            row["round_trip_miles"] = round(round_trip_miles, 2)
        row["notes"] = _guidance_note(row.get("notes"), guidance)
        changed_logistics.append(category)
    prepared["logistics"] = submitted_logistics

    guided_tasks = ", ".join(sorted(recommended_by_task))
    warnings = [
        "API labor recommendations were applied during workbook generation "
        f"for: {guided_tasks}."
    ]
    if changed_logistics:
        warnings.append(
            "API trip recommendations were applied for: "
            + ", ".join(sorted(changed_logistics))
            + "."
        )
    if dropped_full_repair:
        warnings.append(
            "The catch-all labor_full_repair row was removed because component "
            "labor recommendations were available; this prevents duplicate labor."
        )
    return prepared, warnings


def _recommended_labor_row(
    submitted: dict[str, Any] | None,
    guidance: dict[str, Any],
    *,
    task: str,
    days: float,
    crew_size: int,
) -> dict[str, Any]:
    row = dict(submitted or {})
    row.update(
        {
            "concept_id": row.get("concept_id") or task,
            "task": task,
            "label": row.get("label") or str(guidance.get("activity") or _activity_label(task)),
            "include": True,
            "days": round(days, 3),
            "crew_size": crew_size,
            "total_hours": guidance.get("recommended_total_hours"),
            "daily_rate": guidance.get("current_people_daily_rate"),
            "estimated_cost": guidance.get("estimated_labor_cost_candidate"),
            "notes": _guidance_note(row.get("notes"), guidance),
        }
    )
    return row


def _guidance_note(existing: Any, guidance: dict[str, Any]) -> str:
    note = str(existing or "").strip()
    method = str(guidance.get("method") or guidance.get("calibration_status") or "").strip()
    marker = "API planning recommendation applied"
    if method:
        marker += f" ({method})"
    return f"{note} {marker}.".strip()


def _positive_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _supported_crew_size(value: Any) -> int | None:
    number = _positive_number(value)
    if number is None or int(number) != number:
        return None
    crew_size = int(number)
    return crew_size if 1 <= crew_size <= 8 else None


def _activity_label(value: str) -> str:
    return value.removeprefix("labor_").replace("_", " ").title()
