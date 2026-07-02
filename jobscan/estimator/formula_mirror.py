from __future__ import annotations

import json
import math
from typing import Any

from .rules import first_nonblank, to_float

DEFAULT_HOURS_PER_DAY = 10.0


def safe_number(value: Any, default: float = 0.0) -> float:
    number = to_float(value)
    if number is None or not math.isfinite(number):
        return default
    return float(number)


def positive_number(*values: Any, default: float = 0.0) -> float:
    for value in values:
        number = safe_number(value, 0.0)
        if number > 0:
            return number
    return default


def decision_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def waste_multiplier(waste_factor_pct: Any = None, margin_pct: Any = None) -> float:
    """Return the denominator multiplier used by workbook coating formulas."""

    waste = safe_number(first_nonblank(waste_factor_pct, margin_pct), 0.0)
    if waste <= 0:
        return 1.0
    if waste >= 100:
        return 1.0
    return (100.0 - waste) / 100.0


def calculate_insulation_foam(
    *,
    area_sqft: Any,
    thickness_inches: Any,
    yield_or_coverage: Any = None,
    unit_price: Any = None,
    units_per_sqft_per_inch: Any = None,
    cost_per_sqft: Any = None,
    cost_per_sqft_per_inch: Any = None,
    include: bool = True,
) -> dict[str, Any]:
    """Mirror insulation foam rows: G=((C/F)*D)*1000 and H=E*G.

    The workbook calls column G "Estimated Sets" in some templates, but the
    extracted history also preserves G as estimated_units and G/1000 as sets.
    Keeping both values here matches the existing parser/audit convention.
    """

    area = safe_number(area_sqft, 0.0)
    thickness = safe_number(thickness_inches, 0.0)
    yield_factor = safe_number(yield_or_coverage, 0.0)
    units_rate = safe_number(units_per_sqft_per_inch, 0.0)
    price = safe_number(unit_price, 0.0)
    if include and area > 0 and thickness > 0 and yield_factor > 0:
        estimated_units = (area / yield_factor) * thickness * 1000.0
        formula_source = "yield_or_coverage"
    elif include and area > 0 and thickness > 0 and units_rate > 0:
        estimated_units = area * thickness * units_rate
        formula_source = "historical_units_per_sqft_per_inch"
    else:
        estimated_units = 0.0
        formula_source = "insufficient_formula_inputs" if include else "not_included"
    estimated_sets = estimated_units / 1000.0 if estimated_units else 0.0
    if include and price > 0 and estimated_units > 0:
        estimated_cost = estimated_units * price
        cost_source = "current_pricing"
    elif include and area > 0 and thickness > 0 and safe_number(cost_per_sqft_per_inch, 0.0) > 0:
        estimated_cost = area * thickness * safe_number(cost_per_sqft_per_inch, 0.0)
        cost_source = "historical_cost_per_sqft_per_inch"
    elif include and area > 0 and safe_number(cost_per_sqft, 0.0) > 0:
        estimated_cost = area * safe_number(cost_per_sqft, 0.0)
        cost_source = "historical_cost_default"
    else:
        estimated_cost = 0.0
        cost_source = "not_included" if not include else "current_pricing_missing"
    return {
        "formula_model": "foam_sets_from_area_thickness_yield",
        "formula_source": formula_source,
        "area_sqft": round(area, 4),
        "thickness_inches": round(thickness, 6),
        "yield_or_coverage": round(yield_factor, 6) if yield_factor else 0.0,
        "estimated_units": round(estimated_units, 6),
        "estimated_sets": round(estimated_sets, 6),
        "estimated_cost": round(estimated_cost, 2),
        "cost_source": cost_source,
        "calculated_output": round(estimated_cost, 2),
    }


def calculate_coating_gallons(
    *,
    area_sqft: Any,
    gal_per_100_sqft: Any = None,
    gal_per_sqft: Any = None,
    waste_factor_pct: Any = None,
    margin_pct: Any = None,
    unit_price: Any = None,
    cost_per_sqft: Any = None,
    include: bool = True,
    formula_model: str = "coating_gallons_from_area_rate_waste",
) -> dict[str, Any]:
    area = safe_number(area_sqft, 0.0)
    gal_rate = positive_number(gal_per_100_sqft, safe_number(gal_per_sqft, 0.0) * 100.0, default=0.0)
    multiplier = waste_multiplier(waste_factor_pct, margin_pct)
    price = safe_number(unit_price, 0.0)
    if include and area > 0 and gal_rate > 0:
        gallons = ((area / 100.0) * gal_rate) / multiplier
        formula_source = "gal_per_100_sqft"
    else:
        gallons = 0.0
        formula_source = "insufficient_formula_inputs" if include else "not_included"
    if include and price > 0 and gallons > 0:
        estimated_cost = gallons * price
        cost_source = "current_pricing"
    elif include and area > 0 and safe_number(cost_per_sqft, 0.0) > 0:
        estimated_cost = area * safe_number(cost_per_sqft, 0.0)
        cost_source = "historical_cost_default"
    else:
        estimated_cost = 0.0
        cost_source = "not_included" if not include else "current_pricing_missing"
    return {
        "formula_model": formula_model,
        "formula_source": formula_source,
        "area_sqft": round(area, 4),
        "gal_per_100_sqft": round(gal_rate, 6),
        "gal_per_sqft": round(gal_rate / 100.0, 8) if gal_rate else 0.0,
        "waste_factor_pct": safe_number(first_nonblank(waste_factor_pct, margin_pct), 0.0),
        "estimated_gallons": round(gallons, 6),
        "calculated_quantity": round(gallons, 6),
        "wet_mils_estimate": round(gal_rate * 16.0, 4) if gal_rate else 0.0,
        "estimated_cost": round(estimated_cost, 2),
        "cost_source": cost_source,
        "calculated_output": round(estimated_cost, 2),
    }


def calculate_insulation_thermal_barrier(**kwargs: Any) -> dict[str, Any]:
    return calculate_coating_gallons(
        **kwargs,
        formula_model="coating_gallons_from_area_rate_waste",
    )


def calculate_roofing_coating(**kwargs: Any) -> dict[str, Any]:
    return calculate_coating_gallons(
        **kwargs,
        formula_model="coating_gallons_from_area_rate_waste",
    )


def calculate_mixed_labor(
    *,
    days: Any = None,
    crew_size: Any = None,
    total_hours: Any = None,
    hours_per_1000_sqft: Any = None,
    area_sqft: Any = None,
    daily_rate: Any = None,
    hourly_rate: Any = None,
    formula_mode: str | None = None,
    include: bool = True,
    hours_per_day: float = DEFAULT_HOURS_PER_DAY,
) -> dict[str, Any]:
    mode = str(formula_mode or "mixed_formula")
    area = safe_number(area_sqft, 0.0)
    hours = safe_number(total_hours, 0.0)
    if hours <= 0 and include and area > 0 and safe_number(hours_per_1000_sqft, 0.0) > 0:
        hours = safe_number(hours_per_1000_sqft, 0.0) * area / 1000.0
    days_value = safe_number(days, 0.0)
    crew = safe_number(crew_size, 0.0)
    if days_value <= 0 and hours > 0 and crew > 0:
        days_value = hours / (crew * hours_per_day)
    daily = safe_number(daily_rate, 0.0)
    hourly = safe_number(hourly_rate, 0.0)
    if daily <= 0 and hourly > 0 and crew > 0:
        daily = hourly * crew * hours_per_day
    if hourly <= 0 and daily > 0 and crew > 0 and hours_per_day > 0:
        hourly = daily / (crew * hours_per_day)

    if not include:
        cost = 0.0
        cost_basis = "not_included"
    elif mode == "days_based":
        cost = days_value * daily if days_value > 0 and daily > 0 else 0.0
        cost_basis = "days_daily_rate" if cost else "missing_days_or_daily_rate"
        if hours <= 0 and days_value > 0 and crew > 0:
            hours = days_value * crew * hours_per_day
    elif mode == "hours_based":
        cost = hours * hourly if hours > 0 and hourly > 0 else 0.0
        cost_basis = "hours_hourly_rate" if cost else "missing_hours_or_hourly_rate"
    else:
        if hours <= 0 and days_value > 0 and daily > 0:
            cost = days_value * daily
            cost_basis = "days_daily_rate"
        elif hours > 0 and hourly > 0:
            cost = hours * hourly
            cost_basis = "hours_hourly_rate"
        else:
            cost = 0.0
            cost_basis = "missing_labor_formula_inputs"
    return {
        "formula_model": "labor_cost_from_days_crew_rate",
        "formula_mode": mode,
        "formula_source": cost_basis,
        "days": round(days_value, 6),
        "crew_size": round(crew, 6),
        "total_hours": round(hours, 6) if include else 0.0,
        "daily_rate": round(daily, 6),
        "hourly_rate": round(hourly, 6),
        "estimated_cost": round(cost, 2),
        "calculated_output": round(cost, 2),
    }


def cell_preview_for_material(row: dict[str, Any]) -> list[dict[str, Any]]:
    workbook_row = str(row.get("workbook_row") or "")
    first_row = int(workbook_row.split("-")[0].split("/")[0]) if workbook_row and workbook_row[0].isdigit() else None
    if first_row is None:
        return []
    package = str(row.get("package_key") or row.get("template_bucket") or "")
    if package == "foam":
        return [
            {"cell": f"Estimate!C{first_row}", "field": "area_sqft", "value": row.get("editable_basis_sqft")},
            {"cell": f"Estimate!D{first_row}", "field": "thickness_inches", "value": row.get("thickness_inches")},
            {"cell": f"Estimate!E{first_row}", "field": "unit_price", "value": row.get("current_unit_price")},
            {"cell": f"Estimate!F{first_row}", "field": "yield_or_coverage", "value": row.get("yield_factor")},
            {"cell": f"Estimate!G{first_row}", "field": "estimated_units_formula_output", "value": row.get("estimated_units")},
        ]
    if package in {"coating", "thermal_barrier_coating"}:
        return [
            {"cell": f"Estimate!C{first_row}", "field": "area_sqft", "value": row.get("editable_basis_sqft")},
            {"cell": f"Estimate!D{first_row}", "field": "gal_per_100_sqft", "value": row.get("gal_per_100_sqft")},
            {"cell": f"Estimate!E{first_row}", "field": "unit_price", "value": row.get("current_unit_price")},
            {"cell": f"Estimate!G{first_row}", "field": "estimated_gallons_formula_output", "value": row.get("estimated_gallons")},
        ]
    return [
        {"cell": f"Estimate!C{first_row}", "field": "quantity", "value": row.get("calculated_quantity")},
        {"cell": f"Estimate!E{first_row}", "field": "unit_price", "value": row.get("current_unit_price")},
    ]


def cell_preview_for_labor(row: dict[str, Any]) -> list[dict[str, Any]]:
    workbook_row = str(row.get("workbook_row") or "")
    first_row = int(workbook_row.split("-")[0].split("/")[0]) if workbook_row and workbook_row[0].isdigit() else None
    if first_row is None:
        return []
    return [
        {"cell": f"Estimate!B{first_row}", "field": "days", "value": row.get("days")},
        {"cell": f"Estimate!C{first_row}", "field": "crew_size", "value": row.get("crew_size")},
        {"cell": f"Estimate!D{first_row}", "field": "hourly_rate", "value": row.get("hourly_rate")},
        {"cell": f"Estimate!G{first_row}", "field": "total_hours", "value": row.get("calculated_hours")},
        {"cell": f"Estimate!J{first_row}", "field": "daily_rate", "value": row.get("daily_rate")},
    ]
