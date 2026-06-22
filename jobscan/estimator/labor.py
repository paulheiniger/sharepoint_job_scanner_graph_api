from __future__ import annotations

import math
from typing import Any

import pandas as pd

from .rules import default_crew_size, first_nonblank, to_float
from .schemas import EstimatorAssumptions


CITY_DISTANCE_MILES = {
    "shelbyville": 0.0,
    "louisville": 31.0,
    "lexington": 48.0,
    "frankfort": 25.0,
    "cincinnati": 100.0,
    "indianapolis": 125.0,
    "nashville": 180.0,
    "columbus": 200.0,
}


def median_positive(values: pd.Series) -> float | None:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    numeric = numeric[numeric > 0]
    if numeric.empty:
        return None
    return float(numeric.median())


def infer_labor_hours_from_subtotal(labor_subtotal: float | None, hourly_rate: float) -> float | None:
    if not labor_subtotal or labor_subtotal <= 0 or hourly_rate <= 0:
        return None
    return labor_subtotal / hourly_rate


def estimate_labor(
    scope: dict[str, Any],
    similar_jobs: pd.DataFrame,
    tracking_summary: pd.DataFrame,
    assumptions: EstimatorAssumptions | None = None,
    decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    assumptions = assumptions or EstimatorAssumptions()
    area = to_float(scope.get("surface_area_sqft")) or to_float(scope.get("wall_area_sqft")) or 0.0
    project_type = first_nonblank(scope.get("project_type"))
    crew_size = int(
        to_float(scope.get("estimated_crew_size"))
        or to_float(((decision or {}).get("crew_assumptions") or {}).get("recommended_crew_size"))
        or default_crew_size(project_type)
    )
    labor_modifiers = (decision or {}).get("labor_modifiers") or {}
    multiplier = to_float(labor_modifiers.get("combined_labor_multiplier")) or 1.0

    labor_hours = median_positive(similar_jobs["estimated_labor_hours"]) if "estimated_labor_hours" in similar_jobs.columns else None
    source_note = "median estimated labor hours from similar estimates"
    if not labor_hours and not tracking_summary.empty and "actual_labor_hours" in tracking_summary.columns:
        similar_ids = set(similar_jobs.get("job_id", pd.Series(dtype=str)).astype(str))
        rows = tracking_summary[tracking_summary["job_id"].astype(str).isin(similar_ids)] if "job_id" in tracking_summary.columns else tracking_summary
        labor_hours = median_positive(rows["actual_labor_hours"])
        source_note = "median actual labor hours from job tracking"
    if not labor_hours and "labor_subtotal" in similar_jobs.columns:
        labor_subtotal = median_positive(similar_jobs["labor_subtotal"])
        labor_hours = infer_labor_hours_from_subtotal(labor_subtotal, assumptions.blended_hourly_rate)
        source_note = "inferred from median labor subtotal and blended hourly rate"

    if labor_hours:
        low_hours = labor_hours * 0.85 * multiplier
        high_hours = labor_hours * 1.2 * multiplier
        if multiplier != 1.0:
            source_note += f"; decision-tree labor multiplier {multiplier:g}"
    elif area > 0:
        adjusted_productivity = to_float(labor_modifiers.get("adjusted_productivity_sqft_per_day"))
        high_productivity = adjusted_productivity or assumptions.crew_productivity_sqft_per_day_high / multiplier
        low_productivity = min(high_productivity, assumptions.crew_productivity_sqft_per_day_low / multiplier)
        low_days = area / max(high_productivity, 1)
        high_days = area / max(low_productivity, 1)
        low_hours = max(low_days, 1) * crew_size * 8
        high_hours = max(high_days, 1) * crew_size * 8
        source_note = "inferred from production-rate assumptions"
        if multiplier != 1.0:
            source_note += f"; decision-tree labor multiplier {multiplier:g}"
    else:
        low_hours = high_hours = 0.0
        source_note = "labor cannot be estimated without area or similar labor history"

    low_days = max(1, math.ceil(low_hours / max(crew_size * 8, 1))) if low_hours else 0
    high_days = max(low_days, math.ceil(high_hours / max(crew_size * 8, 1))) if high_hours else 0
    return {
        "estimated_labor_hours_low": round(low_hours, 1),
        "estimated_labor_hours_high": round(high_hours, 1),
        "recommended_crew_size": crew_size,
        "estimated_duration_days_low": low_days,
        "estimated_duration_days_high": high_days,
        "labor_cost_low": round(low_hours * assumptions.blended_hourly_rate, 2),
        "labor_cost_high": round(high_hours * assumptions.blended_hourly_rate, 2),
        "labor_assumption_notes": source_note,
        "labor_hours_inferred": "inferred" in source_note,
        "labor_modifiers": labor_modifiers,
    }


def _location_text(scope: dict[str, Any]) -> str:
    return first_nonblank(scope.get("destination_address"), scope.get("site_address"), scope.get("location"), scope.get("city"))


def estimate_one_way_miles(scope: dict[str, Any]) -> float | None:
    lat = to_float(scope.get("latitude"))
    lon = to_float(scope.get("longitude"))
    origin_lat = to_float(scope.get("origin_latitude")) or 38.212
    origin_lon = to_float(scope.get("origin_longitude")) or -85.223
    if lat is not None and lon is not None:
        return haversine_miles(origin_lat, origin_lon, lat, lon)
    location = _location_text(scope).lower()
    for city, miles in CITY_DISTANCE_MILES.items():
        if city in location:
            return miles
    return None


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 3958.8
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def travel_bucket(one_way_miles: float | None, assumptions: EstimatorAssumptions) -> str:
    if one_way_miles is None:
        return "unknown"
    if one_way_miles <= assumptions.local_radius_miles:
        return "local"
    if one_way_miles <= assumptions.lodging_review_one_way_miles:
        return "regional"
    return "distant"


def estimate_travel_impact(
    scope: dict[str, Any],
    *,
    recommended_crew_size: int,
    estimated_work_days: int,
    assumptions: EstimatorAssumptions | None = None,
) -> dict[str, Any]:
    assumptions = assumptions or EstimatorAssumptions()
    one_way = estimate_one_way_miles(scope)
    if one_way is None:
        return {
            "origin_address": assumptions.origin_address,
            "destination_address": _location_text(scope),
            "estimated_one_way_miles": None,
            "estimated_round_trip_miles": None,
            "estimated_drive_time_minutes_one_way": None,
            "travel_distance_bucket": "unknown",
            "travel_labor_hours": 0.0,
            "travel_vehicle_cost": 0.0,
            "lodging_required_possible": False,
            "travel_notes": "No usable project location; travel requires review.",
            "needs_travel_review": True,
        }

    round_trip = one_way * 2
    one_way_minutes = one_way / assumptions.average_speed_mph_for_fallback * 60
    round_trip_hours = one_way_minutes * 2 / 60
    crew = max(int(recommended_crew_size or 1), 1)
    lodging = one_way >= assumptions.lodging_review_one_way_miles or one_way_minutes >= assumptions.lodging_review_one_way_minutes
    return {
        "origin_address": assumptions.origin_address,
        "destination_address": _location_text(scope),
        "estimated_one_way_miles": round(one_way, 1),
        "estimated_round_trip_miles": round(round_trip, 1),
        "estimated_drive_time_minutes_one_way": round(one_way_minutes),
        "travel_distance_bucket": travel_bucket(one_way, assumptions),
        "travel_labor_hours": round(round_trip_hours * crew, 1),
        "travel_vehicle_cost": round(round_trip * assumptions.cost_per_mile * max(int(estimated_work_days or 1), 1), 2),
        "lodging_required_possible": lodging,
        "travel_notes": "Distance is bucketed from city/state or staged coordinates; no routing API used.",
        "needs_travel_review": lodging,
    }
