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


def calculate_roofing_primer(
    *,
    area_sqft: Any,
    coverage_sqft_per_unit: Any = 250,
    unit_price: Any = None,
    cost_per_sqft: Any = None,
    include: bool = True,
) -> dict[str, Any]:
    """Mirror roofing primer row 39: G=SUM(C39/250), H=E39*G39."""

    area = safe_number(area_sqft, 0.0)
    coverage = safe_number(coverage_sqft_per_unit, 0.0)
    price = safe_number(unit_price, 0.0)
    if include and area > 0 and coverage > 0:
        estimated_units = area / coverage
        formula_source = "area_coverage"
    else:
        estimated_units = 0.0
        formula_source = "insufficient_formula_inputs" if include else "not_included"
    if include and price > 0 and estimated_units > 0:
        estimated_cost = estimated_units * price
        cost_source = "current_pricing"
    elif include and area > 0 and safe_number(cost_per_sqft, 0.0) > 0:
        estimated_cost = area * safe_number(cost_per_sqft, 0.0)
        cost_source = "historical_cost_default"
    else:
        estimated_cost = 0.0
        cost_source = "not_included" if not include else "current_pricing_missing"
    return {
        "formula_model": "primer_units_from_area_coverage",
        "formula_source": formula_source,
        "area_sqft": round(area, 4),
        "coverage_sqft_per_unit": round(coverage, 6) if coverage else 0.0,
        "estimated_units": round(estimated_units, 6),
        "calculated_quantity": round(estimated_units, 6),
        "estimated_cost": round(estimated_cost, 2),
        "cost_source": cost_source,
        "calculated_output": round(estimated_cost, 2),
    }


def calculate_roofing_granules(
    *,
    area_sqft: Any,
    coverage_lbs_per_100_sqft: Any = 50,
    bag_weight_lbs: Any = 100,
    unit_price: Any = None,
    cost_per_sqft: Any = None,
    include: bool = True,
) -> dict[str, Any]:
    """Mirror roofing granules row 36: G=(((C36/100)*50)/100), H=E36*G36."""

    area = safe_number(area_sqft, 0.0)
    coverage = safe_number(coverage_lbs_per_100_sqft, 0.0)
    bag_weight = safe_number(bag_weight_lbs, 0.0)
    price = safe_number(unit_price, 0.0)
    if include and area > 0 and coverage > 0 and bag_weight > 0:
        estimated_units = ((area / 100.0) * coverage) / bag_weight
        formula_source = "area_coverage_bag_weight"
    else:
        estimated_units = 0.0
        formula_source = "insufficient_formula_inputs" if include else "not_included"
    if include and price > 0 and estimated_units > 0:
        estimated_cost = estimated_units * price
        cost_source = "current_pricing"
    elif include and area > 0 and safe_number(cost_per_sqft, 0.0) > 0:
        estimated_cost = area * safe_number(cost_per_sqft, 0.0)
        cost_source = "historical_cost_default"
    else:
        estimated_cost = 0.0
        cost_source = "not_included" if not include else "current_pricing_missing"
    return {
        "formula_model": "granules_units_from_area_rate",
        "formula_source": formula_source,
        "area_sqft": round(area, 4),
        "coverage_lbs_per_100_sqft": round(coverage, 6) if coverage else 0.0,
        "bag_weight_lbs": round(bag_weight, 6) if bag_weight else 0.0,
        "estimated_units": round(estimated_units, 6),
        "calculated_quantity": round(estimated_units, 6),
        "unit_price": round(price, 6) if price else 0.0,
        "estimated_cost": round(estimated_cost, 2),
        "cost_source": cost_source,
        "calculated_output": round(estimated_cost, 2),
    }


def calculate_roofing_dumpster(
    *,
    area_sqft: Any,
    thickness_inches: Any,
    selector_code: Any = 3,
    unit_price: Any = None,
    margin_pct: Any = 25,
    include: bool = True,
) -> dict[str, Any]:
    """Mirror roofing dumpster row 69.

    Formula: G=(C*D/12/capacity)*(1+(F/100)), H=G*E
    where selector code 1/2/3 maps to 20/30/40 yard capacities of 700/1000/1400.
    """

    area = safe_number(area_sqft, 0.0)
    thickness = safe_number(thickness_inches, 0.0)
    margin = safe_number(margin_pct, 0.0)
    price = safe_number(unit_price, 0.0)
    selector = str(first_nonblank(selector_code, "3")).strip()
    if selector.endswith(".0"):
        selector = selector[:-2]
    capacity_by_selector = {"1": 700.0, "2": 1000.0, "3": 1400.0}
    capacity = capacity_by_selector.get(selector, 1400.0)
    if include and area > 0 and thickness > 0 and capacity > 0:
        estimated_units = (area * thickness / 12.0 / capacity) * (1.0 + margin / 100.0)
        formula_source = "area_thickness_capacity_margin"
    else:
        estimated_units = 0.0
        formula_source = "insufficient_formula_inputs" if include else "not_included"
    if include and price > 0 and estimated_units > 0:
        estimated_cost = estimated_units * price
        cost_source = "current_pricing"
    else:
        estimated_cost = 0.0
        cost_source = "not_included" if not include else "current_pricing_missing"
    return {
        "formula_model": "dumpster_count_from_area_thickness_margin",
        "formula_source": formula_source,
        "selector_code": selector,
        "area_sqft": round(area, 4),
        "thickness_inches": round(thickness, 6),
        "capacity_factor": capacity,
        "margin_pct": round(margin, 6),
        "unit_price": round(price, 6) if price else 0.0,
        "estimated_units": round(estimated_units, 6),
        "calculated_quantity": round(estimated_units, 6),
        "estimated_cost": round(estimated_cost, 2),
        "cost_source": cost_source,
        "calculated_output": round(estimated_cost, 2),
    }


def calculate_roofing_equipment_cost(
    *,
    period: Any,
    unit_price: Any = None,
    margin_pct: Any = 20,
    include: bool = True,
) -> dict[str, Any]:
    """Mirror roofing lift/equipment rows 73-74: H=D*E*(1+(F/100))."""

    rental_period = safe_number(period, 0.0)
    price = safe_number(unit_price, 0.0)
    margin = safe_number(margin_pct, 0.0)
    if include and rental_period > 0 and price > 0:
        estimated_cost = rental_period * price * (1.0 + margin / 100.0)
        formula_source = "period_price_margin"
    else:
        estimated_cost = 0.0
        formula_source = "insufficient_formula_inputs" if include else "not_included"
    return {
        "formula_model": "equipment_cost_with_margin",
        "formula_source": formula_source,
        "period": round(rental_period, 6),
        "unit_price": round(price, 6) if price else 0.0,
        "margin_pct": round(margin, 6),
        "estimated_cost": round(estimated_cost, 2),
        "cost_source": "current_pricing" if estimated_cost > 0 else ("not_included" if not include else "current_pricing_missing"),
        "calculated_output": round(estimated_cost, 2),
    }


def calculate_roofing_days_rate_cost(
    *,
    days: Any,
    unit_price: Any = None,
    include: bool = True,
) -> dict[str, Any]:
    """Mirror simple roofing day-rate rows such as generator row 99: H=C*E."""

    day_count = safe_number(days, 0.0)
    price = safe_number(unit_price, 0.0)
    if include and day_count > 0 and price > 0:
        estimated_cost = day_count * price
        formula_source = "days_price"
    else:
        estimated_cost = 0.0
        formula_source = "insufficient_formula_inputs" if include else "not_included"
    return {
        "formula_model": "days_rate_cost",
        "formula_source": formula_source,
        "days": round(day_count, 6),
        "unit_price": round(price, 6) if price else 0.0,
        "estimated_cost": round(estimated_cost, 2),
        "cost_source": "current_pricing" if estimated_cost > 0 else ("not_included" if not include else "current_pricing_missing"),
        "calculated_output": round(estimated_cost, 2),
    }


def calculate_roofing_travel_cost(
    *,
    trip_count: Any,
    round_trip_miles: Any,
    unit_price: Any = None,
    include: bool = True,
) -> dict[str, Any]:
    """Mirror roofing travel-style rows 106/108: H=B*C*E."""

    trips = safe_number(trip_count, 0.0)
    miles = safe_number(round_trip_miles, 0.0)
    price = safe_number(unit_price, 0.0)
    if include and trips > 0 and miles > 0 and price > 0:
        estimated_cost = trips * miles * price
        formula_source = "trips_miles_rate"
    else:
        estimated_cost = 0.0
        formula_source = "insufficient_formula_inputs" if include else "not_included"
    return {
        "formula_model": "travel_cost_from_trips_miles_rate",
        "formula_source": formula_source,
        "trip_count": round(trips, 6),
        "round_trip_miles": round(miles, 6),
        "unit_price": round(price, 6) if price else 0.0,
        "estimated_cost": round(estimated_cost, 2),
        "cost_source": "current_pricing" if estimated_cost > 0 else ("not_included" if not include else "current_pricing_missing"),
        "calculated_output": round(estimated_cost, 2),
    }


def calculate_roofing_direct_cost(
    *,
    amount: Any,
    include: bool = True,
) -> dict[str, Any]:
    """Mirror direct-cost rows such as freight row 103: H=E."""

    cost = safe_number(amount, 0.0) if include else 0.0
    return {
        "formula_model": "direct_cost",
        "formula_source": "direct_amount" if include and cost > 0 else ("not_included" if not include else "insufficient_formula_inputs"),
        "estimated_cost": round(cost, 2),
        "cost_source": "direct_amount" if cost > 0 else ("not_included" if not include else "current_pricing_missing"),
        "calculated_output": round(cost, 2),
    }


def calculate_insulation_membrane(*, linear_ft: Any, unit_price: Any = None, include: bool = True) -> dict[str, Any]:
    """Mirror insulation membrane row 24: cost = linear feet * unit price."""

    return calculate_roofing_linear_feet_cost(
        linear_ft=linear_ft,
        unit_price=unit_price,
        include=include,
        formula_model="insulation_membrane_cost_from_linear_feet",
    )


def calculate_insulation_primer(
    *,
    area_sqft: Any,
    coverage_sqft_per_unit: Any = 250,
    unit_price: Any = None,
    cost_per_sqft: Any = None,
    include: bool = True,
) -> dict[str, Any]:
    """Mirror insulation primer row 26: estimated units = area / 250."""

    result = calculate_roofing_primer(
        area_sqft=area_sqft,
        coverage_sqft_per_unit=coverage_sqft_per_unit,
        unit_price=unit_price,
        cost_per_sqft=cost_per_sqft,
        include=include,
    )
    result["formula_model"] = "insulation_primer_units_from_area_coverage"
    return result


def calculate_insulation_thinner(*, total_coating_gallons: Any, unit_price: Any = None, include: bool = True) -> dict[str, Any]:
    """Mirror insulation thinner row 37: units = ((G30+G31+G32)/55)*4."""

    result = calculate_roofing_thinner(
        total_coating_gallons=total_coating_gallons,
        unit_price=unit_price,
        include=include,
    )
    result["formula_model"] = "insulation_thinner_units_from_thermal_barrier_gallons"
    return result


def calculate_insulation_caulk_sealant(
    *,
    linear_ft: Any,
    feet_per_unit: Any,
    unit_price: Any = None,
    include: bool = True,
) -> dict[str, Any]:
    """Mirror insulation sealant rows 41/43: units = linear feet / feet per unit."""

    lf = safe_number(linear_ft, 0.0)
    coverage = safe_number(feet_per_unit, 0.0)
    price = safe_number(unit_price, 0.0)
    if include and lf > 0 and coverage > 0:
        units = lf / coverage
        formula_source = "linear_feet_feet_per_unit"
    else:
        units = 0.0
        formula_source = "insufficient_formula_inputs" if include else "not_included"
    if include and units > 0 and price > 0:
        cost = units * price
        cost_source = "current_pricing"
    elif include and units > 0:
        cost = 0.0
        cost_source = "current_pricing_missing"
    else:
        cost = 0.0
        cost_source = "not_included" if not include else "current_pricing_missing"
    return {
        "formula_model": "insulation_sealant_units_from_linear_feet",
        "formula_source": formula_source,
        "linear_ft": round(lf, 6),
        "feet_per_unit": round(coverage, 6) if coverage else 0.0,
        "estimated_units": round(units, 6),
        "calculated_quantity": round(units, 6),
        "unit_price": round(price, 6) if price else 0.0,
        "estimated_cost": round(cost, 2),
        "cost_source": cost_source,
        "calculated_output": round(cost, 2),
    }


def calculate_insulation_equipment_cost(
    *,
    period: Any,
    unit_price: Any = None,
    margin_pct: Any = 0,
    include: bool = True,
) -> dict[str, Any]:
    """Mirror insulation lift rows 47/48: period * unit price * margin."""

    result = calculate_roofing_equipment_cost(
        period=period,
        unit_price=unit_price,
        margin_pct=margin_pct,
        include=include,
    )
    result["formula_model"] = "insulation_equipment_cost_with_margin"
    return result


def calculate_insulation_days_rate_cost(*, days: Any, unit_price: Any = None, include: bool = True) -> dict[str, Any]:
    """Mirror insulation generator/space heater day-rate rows."""

    result = calculate_roofing_days_rate_cost(days=days, unit_price=unit_price, include=include)
    result["formula_model"] = "insulation_days_rate_cost"
    return result


def calculate_insulation_travel_cost(
    *,
    trip_count: Any,
    round_trip_miles: Any,
    unit_price: Any = None,
    include: bool = True,
) -> dict[str, Any]:
    """Mirror insulation sales/truck trip rows: trips * miles * rate."""

    result = calculate_roofing_travel_cost(
        trip_count=trip_count,
        round_trip_miles=round_trip_miles,
        unit_price=unit_price,
        include=include,
    )
    result["formula_model"] = "insulation_travel_cost_from_trips_miles_rate"
    return result


def calculate_insulation_direct_cost(*, amount: Any, include: bool = True) -> dict[str, Any]:
    """Mirror direct insulation adders such as freight/misc/manual fees."""

    result = calculate_roofing_direct_cost(amount=amount, include=include)
    result["formula_model"] = "insulation_direct_cost"
    return result


def calculate_insulation_abaa_fee(
    *,
    area_sqft: Any,
    unit_price: Any = None,
    include: bool = True,
) -> dict[str, Any]:
    """Mirror area-based ABAA fee style rows where the workbook uses sqft basis."""

    area = safe_number(area_sqft, 0.0)
    price = safe_number(unit_price, 0.0)
    if include and area > 0 and price > 0:
        cost = area * price
        formula_source = "area_unit_price"
        cost_source = "current_pricing"
    else:
        cost = 0.0
        formula_source = "insufficient_formula_inputs" if include else "not_included"
        cost_source = "not_included" if not include else "current_pricing_missing"
    return {
        "formula_model": "insulation_abaa_fee_from_area_rate",
        "formula_source": formula_source,
        "area_sqft": round(area, 4),
        "unit_price": round(price, 6) if price else 0.0,
        "estimated_cost": round(cost, 2),
        "cost_source": cost_source,
        "calculated_output": round(cost, 2),
    }


def calculate_insulation_drum_disposal(
    *,
    primer_units: Any = 0,
    coating_gallons: Any = 0,
    thinner_units: Any = 0,
    foam_units: Any = 0,
    unit_price: Any = None,
    include: bool = True,
) -> dict[str, Any]:
    """Mirror insulation drum disposal row 65.

    Formula: (((primer + coating + thinner) / 50) + (foam_units / 500)) + 1.
    """

    primer = safe_number(primer_units, 0.0)
    coating = safe_number(coating_gallons, 0.0)
    thinner = safe_number(thinner_units, 0.0)
    foam = safe_number(foam_units, 0.0)
    price = safe_number(unit_price, 0.0)
    if include and (primer > 0 or coating > 0 or thinner > 0 or foam > 0):
        drums = (((primer + coating + thinner) / 50.0) + (foam / 500.0)) + 1.0
        formula_source = "dependent_material_quantities"
    else:
        drums = 0.0
        formula_source = "insufficient_formula_inputs" if include else "not_included"
    if include and drums > 0 and price > 0:
        cost = drums * price
        cost_source = "current_pricing"
    elif include and drums > 0:
        cost = 0.0
        cost_source = "current_pricing_missing"
    else:
        cost = 0.0
        cost_source = "not_included" if not include else "current_pricing_missing"
    return {
        "formula_model": "insulation_drum_disposal_from_material_quantities",
        "formula_source": formula_source,
        "primer_units": round(primer, 6),
        "coating_gallons": round(coating, 6),
        "thinner_units": round(thinner, 6),
        "foam_units": round(foam, 6),
        "estimated_drums": round(drums, 6),
        "estimated_units": round(drums, 6),
        "calculated_quantity": round(drums, 6),
        "unit_price": round(price, 6) if price else 0.0,
        "estimated_cost": round(cost, 2),
        "cost_source": cost_source,
        "calculated_output": round(cost, 2),
    }


def calculate_insulation_bond(
    *,
    project_total: Any,
    include: bool = True,
) -> dict[str, Any]:
    """Mirror the insulation performance/payment bond tier formula."""

    total = safe_number(project_total, 0.0)
    if not include or total <= 0:
        cost = 0.0
        source = "not_included" if not include else "insufficient_formula_inputs"
    elif total <= 100000:
        cost = total * 0.0225
        source = "tier_le_100k"
    elif total <= 500000:
        cost = ((total - 100000) * 0.015) + 2250
        source = "tier_100k_500k"
    else:
        cost = ((total - 500000) * 0.0105) + 8250
        source = "tier_500k_2_5m"
    return {
        "formula_model": "insulation_bond_tier_formula",
        "formula_source": source,
        "project_total": round(total, 2),
        "estimated_cost": round(cost, 2),
        "cost_source": "bond_formula" if cost > 0 else source,
        "calculated_output": round(cost, 2),
    }


def calculate_roofing_thinner(
    *,
    total_coating_gallons: Any,
    unit_price: Any = None,
    include: bool = True,
) -> dict[str, Any]:
    """Mirror roofing thinner row 33: G=((G26+G27+G28)/55)*4, H=E33*G33."""

    gallons = safe_number(total_coating_gallons, 0.0)
    price = safe_number(unit_price, 0.0)
    if include and gallons > 0:
        estimated_units = (gallons / 55.0) * 4.0
        formula_source = "coating_gallons"
    else:
        estimated_units = 0.0
        formula_source = "insufficient_formula_inputs" if include else "not_included"
    if include and estimated_units > 0 and price > 0:
        estimated_cost = estimated_units * price
        cost_source = "current_pricing"
    else:
        estimated_cost = 0.0
        cost_source = "not_included" if not include else "current_pricing_missing"
    return {
        "formula_model": "thinner_units_from_coating_gallons",
        "formula_source": formula_source,
        "total_coating_gallons": round(gallons, 6),
        "estimated_units": round(estimated_units, 6),
        "calculated_quantity": round(estimated_units, 6),
        "unit_price": round(price, 6) if price else 0.0,
        "estimated_cost": round(estimated_cost, 2),
        "cost_source": cost_source,
        "calculated_output": round(estimated_cost, 2),
    }


def calculate_roofing_linear_feet_cost(
    *,
    linear_ft: Any,
    unit_price: Any = None,
    include: bool = True,
    formula_model: str = "linear_feet_unit_cost",
) -> dict[str, Any]:
    result = calculate_roofing_units_cost(
        units=linear_ft,
        unit_price=unit_price,
        include=include,
        formula_model=formula_model,
    )
    result["linear_ft"] = result["units"]
    return result


def calculate_roofing_detail_quantity(
    *,
    quantity: Any,
    amount: Any = None,
    include: bool = True,
    quantity_role: str = "units",
) -> dict[str, Any]:
    """Mirror roofing detail quantity rows 47/49/51/53.

    These workbook rows preserve estimator-entered quantities/counts. The
    template intelligence exposes estimated_cost as an output field, but the
    source workbook does not provide a reusable rate formula for these rows.
    """

    qty = safe_number(quantity, 0.0)
    cost = safe_number(amount, 0.0) if include else 0.0
    if include and qty > 0:
        formula_source = "manual_detail_quantity"
    else:
        formula_source = "insufficient_formula_inputs" if include else "not_included"
    return {
        "formula_model": "manual_detail_quantity_cost",
        "formula_source": formula_source,
        quantity_role: round(qty, 6),
        "estimated_units": round(qty, 6),
        "calculated_quantity": round(qty, 6),
        "estimated_cost": round(cost, 2),
        "cost_source": "manual_amount" if cost > 0 else ("not_included" if not include else "manual_amount_missing"),
        "calculated_output": round(cost, 2),
    }


def calculate_roofing_units_cost(
    *,
    units: Any,
    unit_price: Any = None,
    include: bool = True,
    formula_model: str = "manual_units_cost",
) -> dict[str, Any]:
    quantity = safe_number(units, 0.0)
    price = safe_number(unit_price, 0.0)
    if include and quantity > 0 and price > 0:
        cost = quantity * price
        formula_source = "units_unit_price"
        cost_source = "current_pricing"
    elif include and quantity > 0:
        cost = 0.0
        formula_source = "units_unit_price"
        cost_source = "current_pricing_missing"
    else:
        cost = 0.0
        formula_source = "not_included" if not include else "insufficient_formula_inputs"
        cost_source = "not_included" if not include else "current_pricing_missing"
    return {
        "formula_model": formula_model,
        "formula_source": formula_source,
        "units": round(quantity, 6),
        "calculated_quantity": round(quantity, 6),
        "estimated_cost": round(cost, 2),
        "cost_source": cost_source,
        "calculated_output": round(cost, 2),
    }


def calculate_roofing_fabric(*, linear_ft: Any, unit_price: Any = None, include: bool = True) -> dict[str, Any]:
    result = calculate_roofing_units_cost(
        units=linear_ft,
        unit_price=unit_price,
        include=include,
        formula_model="fabric_cost_from_linear_feet",
    )
    result["linear_ft"] = result["units"]
    return result


def calculate_roofing_board_stock(
    *,
    area_sqft: Any,
    price_per_square: Any = None,
    thickness_inches: Any = None,
    include: bool = True,
) -> dict[str, Any]:
    area = safe_number(area_sqft, 0.0)
    price = safe_number(price_per_square, 0.0)
    thickness = safe_number(thickness_inches, 0.0)
    if include and area > 0 and price > 0:
        cost = (area / 100.0) * price
        formula_source = "area_price_per_square"
        cost_source = "current_pricing"
    elif include and area > 0:
        cost = 0.0
        formula_source = "area_price_per_square"
        cost_source = "current_pricing_missing"
    else:
        cost = 0.0
        formula_source = "not_included" if not include else "insufficient_formula_inputs"
        cost_source = "not_included" if not include else "current_pricing_missing"
    return {
        "formula_model": "board_cost_from_squares",
        "formula_source": formula_source,
        "area_sqft": round(area, 4),
        "thickness_inches": round(thickness, 6) if thickness else 0.0,
        "price_per_square": round(price, 6) if price else 0.0,
        "estimated_squares": round(area / 100.0, 6) if area else 0.0,
        "estimated_cost": round(cost, 2),
        "cost_source": cost_source,
        "calculated_output": round(cost, 2),
    }


def calculate_roofing_board_fasteners(
    *,
    board_area_sqft: Any,
    unit_price_per_thousand: Any = None,
    include: bool = True,
) -> dict[str, Any]:
    area = safe_number(board_area_sqft, 0.0)
    price = safe_number(unit_price_per_thousand, 0.0)
    if include and area > 0:
        units = (area / 32.0) * 12.0
        formula_source = "board_area_fastener_pattern"
    else:
        units = 0.0
        formula_source = "not_included" if not include else "insufficient_formula_inputs"
    if include and units > 0 and price > 0:
        cost = price * units / 1000.0
        cost_source = "current_pricing"
    elif include and units > 0:
        cost = 0.0
        cost_source = "current_pricing_missing"
    else:
        cost = 0.0
        cost_source = "not_included" if not include else "current_pricing_missing"
    return {
        "formula_model": "fastener_units_from_board_area",
        "formula_source": formula_source,
        "board_area_sqft": round(area, 4),
        "estimated_units": round(units, 6),
        "calculated_quantity": round(units, 6),
        "unit_price_per_thousand": round(price, 6) if price else 0.0,
        "estimated_cost": round(cost, 2),
        "cost_source": cost_source,
        "calculated_output": round(cost, 2),
    }


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
    hourly_input = safe_number(hourly_rate, 0.0)
    hourly = hourly_input
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
        # Roofing/insulation templates use a mixed cost formula like:
        # IF(G=0, B*J, D*G). Column G is the total-hours input, so the
        # workbook uses daily mode only when hours are blank/zero.
        if hours <= 0 and days_value > 0 and daily > 0:
            cost = days_value * daily
            cost_basis = "days_daily_rate"
        elif hours > 0 and hourly_input > 0:
            cost = hours * hourly_input
            cost_basis = "hours_hourly_rate"
        else:
            cost = 0.0
            cost_basis = "missing_labor_formula_inputs"
    display_hours = hours
    if display_hours <= 0 and days_value > 0 and crew > 0 and hours_per_day > 0:
        display_hours = days_value * crew * hours_per_day
    return {
        "formula_model": "labor_cost_from_days_crew_rate",
        "formula_mode": mode,
        "formula_source": cost_basis,
        "days": round(days_value, 6),
        "crew_size": round(crew, 6),
        "total_hours": round(hours, 6) if include else 0.0,
        "display_total_hours": round(display_hours, 6) if include else 0.0,
        "crew_labor_hours": round(display_hours, 6) if include else 0.0,
        "daily_rate": round(daily, 6),
        "hourly_rate": round(hourly_input, 6),
        "derived_hourly_rate": round(hourly, 6),
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
            {"cell": f"Estimate!A{first_row}", "field": "selector_code", "value": row.get("selector_code")},
            {"cell": f"Estimate!C{first_row}", "field": "area_sqft", "value": row.get("editable_basis_sqft")},
            {"cell": f"Estimate!D{first_row}", "field": "thickness_inches", "value": row.get("thickness_inches")},
            {"cell": f"Estimate!E{first_row}", "field": "unit_price", "value": row.get("current_unit_price")},
            {"cell": f"Estimate!F{first_row}", "field": "yield_or_coverage", "value": row.get("yield_factor")},
            {"cell": f"Estimate!G{first_row}", "field": "estimated_units_formula_output", "value": row.get("estimated_units")},
        ]
    if package in {"coating", "thermal_barrier_coating"}:
        return [
            {"cell": f"Estimate!A{first_row}", "field": "selector_code", "value": row.get("selector_code")},
            {"cell": f"Estimate!C{first_row}", "field": "area_sqft", "value": row.get("editable_basis_sqft")},
            {"cell": f"Estimate!D{first_row}", "field": "gal_per_100_sqft", "value": row.get("gal_per_100_sqft")},
            {"cell": f"Estimate!E{first_row}", "field": "unit_price", "value": row.get("current_unit_price")},
            {"cell": f"Estimate!G{first_row}", "field": "estimated_gallons_formula_output", "value": row.get("estimated_gallons")},
        ]
    if package == "primer":
        return [
            {"cell": f"Estimate!A{first_row}", "field": "selector_code", "value": row.get("selector_code")},
            {"cell": f"Estimate!C{first_row}", "field": "area_sqft", "value": row.get("editable_basis_sqft")},
            {"cell": f"Estimate!E{first_row}", "field": "unit_price", "value": row.get("current_unit_price")},
            {"cell": f"Estimate!G{first_row}", "field": "estimated_units_formula_output", "value": row.get("estimated_units")},
        ]
    if package == "granules":
        return [
            {"cell": f"Estimate!A{first_row}", "field": "selector_code", "value": row.get("selector_code")},
            {"cell": f"Estimate!C{first_row}", "field": "area_sqft", "value": row.get("editable_basis_sqft")},
            {"cell": f"Estimate!E{first_row}", "field": "unit_price", "value": row.get("current_unit_price")},
            {"cell": f"Estimate!G{first_row}", "field": "estimated_units_formula_output", "value": row.get("estimated_units")},
        ]
    if package in {"caulk_detail", "caulk_sealant"}:
        return [
            {"cell": f"Estimate!A{first_row}", "field": "selector_code", "value": row.get("selector_code")},
            {"cell": f"Estimate!E{first_row}", "field": "unit_price", "value": row.get("current_unit_price")},
            {"cell": f"Estimate!G{first_row}", "field": "units", "value": row.get("calculated_quantity")},
        ]
    if package == "fabric":
        return [
            {"cell": f"Estimate!C{first_row}", "field": "linear_ft", "value": row.get("calculated_quantity")},
            {"cell": f"Estimate!E{first_row}", "field": "unit_price", "value": row.get("current_unit_price")},
        ]
    if package == "board_stock":
        return [
            {"cell": f"Estimate!A{first_row}", "field": "selector_code", "value": row.get("selector_code")},
            {"cell": f"Estimate!C{first_row}", "field": "area_sqft", "value": row.get("editable_basis_sqft")},
            {"cell": f"Estimate!D{first_row}", "field": "thickness_inches", "value": row.get("thickness_inches")},
            {"cell": f"Estimate!E{first_row}", "field": "price_per_square", "value": row.get("current_unit_price")},
        ]
    if package in {"fasteners", "fastener_treatment", "plates"}:
        return [
            {"cell": f"Estimate!E{first_row}", "field": "unit_price_per_thousand", "value": row.get("current_unit_price")},
            {"cell": f"Estimate!G{first_row}", "field": "estimated_units_formula_output", "value": row.get("calculated_quantity")},
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
