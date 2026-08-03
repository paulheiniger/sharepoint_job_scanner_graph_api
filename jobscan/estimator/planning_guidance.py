from __future__ import annotations

import math
from statistics import median
from typing import Any, Iterable

from .decision_proposals import (
    canonicalize_structured_roofing_scope,
    compile_deterministic_scope_proposals,
)


AREA_PURCHASE_RULES: tuple[tuple[str, str, float, str], ...] = (
    ("roofing_foam", "foam_basis_sqft", 250.0, "SPF production-area increment"),
    ("coating", "coating_basis_sqft", 100.0, "coating production-area increment"),
    ("board_stock", "board_basis_sqft", 32.0, "one 4x8 board sheet"),
)


def build_estimator_planning_guidance(
    *,
    scope: dict[str, Any],
    data: Any = None,
    historical_material_usage: Iterable[Any] = (),
    historical_labor_performance: Iterable[Any] = (),
    route_mileage: dict[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Build reviewable purchasing and labor candidates from existing evidence."""

    canonical = canonicalize_structured_roofing_scope(scope)
    if not canonical.get("canonical_area_audit"):
        return {
            "purchasing_guidance": [],
            "labor_plan_guidance": [],
            "logistics_guidance": [],
        }
    purchasing = _purchasing_guidance(
        canonical,
        historical_material_usage=historical_material_usage,
    )
    labor = _labor_guidance(
        canonical,
        data=data,
        historical_material_usage=historical_material_usage,
        historical_labor_performance=historical_labor_performance,
    )
    logistics = _logistics_guidance(
        canonical,
        data=data,
        labor_guidance=labor,
        route_mileage=route_mileage or {},
    )
    return {
        "purchasing_guidance": purchasing,
        "labor_plan_guidance": labor,
        "logistics_guidance": logistics,
    }


def _logistics_guidance(
    scope: dict[str, Any],
    *,
    data: Any,
    labor_guidance: list[dict[str, Any]],
    route_mileage: dict[str, Any],
) -> list[dict[str, Any]]:
    area = _number(scope.get("canonical_area_total_sqft"))
    board_area = _number(scope.get("board_basis_sqft"))
    crew_size = int(_number(scope.get("estimated_crew_size"))) or 5
    production_days = _production_days(scope, labor_guidance=labor_guidance)
    round_trip_miles = _number(
        route_mileage.get("estimated_round_trip_miles")
        or route_mileage.get("round_trip_miles")
    )
    one_way_minutes = _number(route_mileage.get("duration_minutes_one_way"))
    route_source = str(route_mileage.get("source") or "route_unavailable")
    if one_way_minutes <= 0 and round_trip_miles > 0:
        one_way_minutes = round_trip_miles / 2 / 50 * 60

    loading_rate = _historical_loading_rate(data)
    loading_people = 2
    loading_person_hours = max(area / 1000 * loading_rate.get("rate", 4.625905), 2.0)
    loading_hours_per_trip = min(
        max(loading_person_hours / max(production_days * loading_people, 1), 0.5),
        4.0,
    )
    loading_people_rate = _current_people_rate(
        data,
        category="labor_loading",
        crew_size=loading_people,
    )
    traveling_people_rate = _current_people_rate(
        data,
        category="labor_traveling",
        crew_size=crew_size,
    )
    travel_hours_per_trip = one_way_minutes * 2 / 60 if one_way_minutes > 0 else 0.0

    output = [
        {
            "category": "crew_plan",
            "recommended_crew_size": crew_size,
            "method": "roofing_baseline_default"
            if not _number(scope.get("estimated_crew_size"))
            else "explicit_scope",
            "assumption_status": "baseline_assumption",
            "review_required": True,
            "reason": "Roofing production defaults to a five-person crew unless current scope or reviewed evidence supports another crew.",
        },
        {
            "category": "production_days",
            "recommended_days": production_days,
            "method": "calibrated_activity_days_plus_setup_cleanup",
            "assumption_status": "baseline_assumption",
            "review_required": True,
            "reason": "Derived from supported prep, tear-off, board, base, and top-coat activity days with a setup/cleanup allowance.",
        },
        {
            "category": "sales_inspection_trips",
            "include": True,
            "recommended_trip_count": 2,
            "round_trip_miles": round_trip_miles or None,
            "route_source": route_source,
            "method": "spraytec_two_trip_baseline",
            "assumption_status": "baseline_assumption",
            "review_required": True,
            "formula_input_mapping": {"trip_count": 2, "round_trip_miles": round_trip_miles or None},
        },
        {
            "category": "truck_expense",
            "include": True,
            "recommended_trip_count": production_days,
            "round_trip_miles": round_trip_miles or None,
            "route_source": route_source,
            "method": "one_production_truck_round_trip_per_site_day",
            "assumption_status": "baseline_assumption",
            "review_required": True,
            "formula_input_mapping": {"trip_count": production_days, "round_trip_miles": round_trip_miles or None},
        },
        {
            "category": "labor_loading",
            "include": True,
            "recommended_trip_count": production_days,
            "recommended_hours_per_trip": round(loading_hours_per_trip, 2),
            "recommended_crew_size": loading_people,
            "estimated_total_person_hours": round(
                loading_hours_per_trip * loading_people * production_days, 1
            ),
            "historical_hours_per_1000_sqft": loading_rate.get("rate"),
            "historical_support_count": loading_rate.get("support_count", 0),
            "current_people_daily_rate": loading_people_rate.get("daily_rate"),
            "estimated_labor_cost_candidate": _per_trip_labor_cost(
                hours_per_trip=loading_hours_per_trip,
                crew_size=loading_people,
                trip_count=production_days,
                current_people_daily_rate=_number(loading_people_rate.get("daily_rate")),
            ),
            "method": "historical_roofing_loading_rate_scaled_by_project_area",
            "assumption_status": "baseline_assumption",
            "review_required": True,
        },
        {
            "category": "labor_traveling",
            "include": bool(travel_hours_per_trip > 0),
            "recommended_trip_count": production_days,
            "recommended_hours_per_trip": round(travel_hours_per_trip, 2)
            if travel_hours_per_trip > 0
            else None,
            "recommended_crew_size": crew_size,
            "estimated_total_person_hours": round(
                travel_hours_per_trip * crew_size * production_days, 1
            )
            if travel_hours_per_trip > 0
            else None,
            "duration_minutes_one_way": round(one_way_minutes, 1)
            if one_way_minutes > 0
            else None,
            "route_source": route_source,
            "current_people_daily_rate": traveling_people_rate.get("daily_rate"),
            "estimated_labor_cost_candidate": _per_trip_labor_cost(
                hours_per_trip=travel_hours_per_trip,
                crew_size=crew_size,
                trip_count=production_days,
                current_people_daily_rate=_number(traveling_people_rate.get("daily_rate")),
            ),
            "method": "mapbox_round_trip_drive_time_per_site_day"
            if route_source == "mapbox_directions"
            else "route_distance_time_fallback_per_site_day",
            "assumption_status": "baseline_assumption",
            "review_required": True,
        },
    ]

    tearoff_required = "labor_tearoff" in _required_labor_categories(scope)
    if tearoff_required:
        dumpster_size = "40-yard" if board_area > 7500 else "30-yard" if board_area > 2000 else "20-yard"
        output.append(
            {
                "category": "dumpster",
                "include": True,
                "recommended_quantity": 1,
                "recommended_size": dumpster_size,
                "basis_sqft": board_area or area,
                "method": "tearoff_scope_baseline",
                "assumption_status": "baseline_assumption",
                "review_required": True,
                "reason": "Tear-off scope normally requires debris disposal; verify debris thickness and container size.",
            }
        )
    if _number(scope.get("foam_basis_sqft")) > 0 or _number(scope.get("coating_basis_sqft")) > 0:
        output.append(
            {
                "category": "generator",
                "include": True,
                "recommended_days": production_days,
                "method": "required_for_roofing_foam_or_coating",
                "assumption_status": "deterministic_scope_rule",
                "review_required": True,
                "reason": "Roofing foam application and coating production require a generator; review the planned days and current daily rate.",
            }
        )
    return output


def _production_days(
    scope: dict[str, Any],
    *,
    labor_guidance: list[dict[str, Any]],
) -> int:
    explicit = _number(
        scope.get("estimated_work_days")
        or scope.get("estimated_days")
        or scope.get("project_days")
        or scope.get("job_days")
    )
    if explicit > 0:
        return max(1, math.ceil(explicit))
    supported_categories = {
        "labor_prep",
        "labor_tearoff",
        "labor_board",
        "labor_base",
        "labor_top_coat",
    }
    supported_days = sum(
        min(_number(row.get("recommended_days")), 3.0)
        for row in labor_guidance
        if row.get("category") in supported_categories
        and row.get("calibration_status") != "missing_calibration"
    )
    if supported_days > 0:
        return max(1, min(math.ceil(supported_days + 0.5), 30))
    area = _number(scope.get("canonical_area_total_sqft"))
    return max(1, min(math.ceil(area / 1800), 30)) if area > 0 else 1


def _historical_loading_rate(data: Any) -> dict[str, Any]:
    frame = getattr(data, "relationship_labor_rates", None) if data is not None else None
    if frame is None or not hasattr(frame, "empty") or frame.empty:
        return {"rate": 4.625905, "support_count": 0}
    candidates: list[dict[str, Any]] = []
    for row in frame.fillna("").to_dict(orient="records"):
        if _slug(row.get("template_type")) != "roofing":
            continue
        if _slug(row.get("labor_package")) != "labor_loading":
            continue
        rate = _number(row.get("median_hours_per_1000_sqft"))
        support = int(
            _number(row.get("job_count") or row.get("evidence_count"))
        )
        if 0.1 <= rate <= 25 and support >= 3:
            candidates.append({"rate": rate, "support_count": support})
    return max(candidates, key=lambda row: row["support_count"]) if candidates else {
        "rate": 4.625905,
        "support_count": 0,
    }


def _per_trip_labor_cost(
    *,
    hours_per_trip: float,
    crew_size: int,
    trip_count: int,
    current_people_daily_rate: float,
) -> float | None:
    if hours_per_trip <= 0 or crew_size <= 0 or trip_count <= 0 or current_people_daily_rate <= 0:
        return None
    burdened_hourly_rate = current_people_daily_rate / (crew_size * 10)
    return round(hours_per_trip * crew_size * trip_count * burdened_hourly_rate, 2)


def _purchasing_guidance(
    scope: dict[str, Any],
    *,
    historical_material_usage: Iterable[Any],
) -> list[dict[str, Any]]:
    observations = [row for row in historical_material_usage if isinstance(row, dict)]
    output: list[dict[str, Any]] = []
    for category, area_field, increment, increment_label in AREA_PURCHASE_RULES:
        measured = _number(scope.get(area_field))
        if measured <= 0:
            continue
        ratio = _historical_area_ratio(observations, category)
        recommended = _round_up(measured, increment)
        reasons = [f"Rounded up to the next {increment:g} sq. ft. {increment_label}."]
        if ratio:
            reasons.insert(
                0,
                f"Historical basis ratio {ratio['ratio']:.4f} supports review but was not compounded with package rounding.",
            )
        output.append(
            {
                "category": category,
                "measured_quantity": measured,
                "recommended_purchase_quantity": recommended,
                "unit": "sqft",
                "package_increment": increment,
                "adjustment_quantity": round(recommended - measured, 3),
                "adjustment_pct": round((recommended / measured - 1) * 100, 3),
                "method": "package_rounding_with_historical_support"
                if ratio
                else "reviewable_package_rounding_default",
                "quantity_adjustment_reason": " ".join(reasons),
                "historical_support_count": ratio["support_count"] if ratio else 0,
                "historical_ratio": ratio["ratio"] if ratio else None,
                "confidence": "medium" if ratio else "review",
                "review_required": True,
                "formula_input_field": "basis_sqft",
            }
        )

    for row in (scope.get("canonical_linear_breakdown") or {}).get("edge_metal") or []:
        if not isinstance(row, dict):
            continue
        measured = _number(row.get("linear_ft"))
        if measured <= 0:
            continue
        recommended = _round_up(measured, 10.0)
        output.append(
            {
                "category": "edge_metal",
                "item": str(row.get("item") or "Edge metal"),
                "size": str(row.get("size") or ""),
                "measured_quantity": measured,
                "recommended_purchase_quantity": recommended,
                "unit": "linear_ft",
                "package_increment": 10.0,
                "adjustment_quantity": round(recommended - measured, 3),
                "adjustment_pct": round((recommended / measured - 1) * 100, 3),
                "method": "reviewable_stock_length_rounding_default",
                "quantity_adjustment_reason": "Rounded to a reviewable 10-foot stock-length increment.",
                "historical_support_count": 0,
                "historical_ratio": None,
                "confidence": "review",
                "review_required": True,
                "formula_input_field": "linear_ft",
                "evidence_text": str(row.get("evidence_text") or ""),
            }
        )
    return output


def _labor_guidance(
    scope: dict[str, Any],
    *,
    data: Any,
    historical_material_usage: Iterable[Any],
    historical_labor_performance: Iterable[Any],
) -> list[dict[str, Any]]:
    proposals = compile_deterministic_scope_proposals(scope, data=data)
    output: list[dict[str, Any]] = []
    for proposal in proposals:
        category = str(proposal.template_bucket or "")
        if not category.startswith("labor_"):
            continue
        values = dict(proposal.proposed_values or {})
        total_hours = _number(
            values.get("total_hours") or values.get("editable_total_hours")
        )
        crew_size = _number(values.get("crew_size"))
        days = _number(values.get("days"))
        if total_hours <= 0 and days <= 0:
            continue
        basis = _labor_basis(scope, category)
        current_daily_rate = _number(values.get("daily_rate"))
        historical_daily_rate = _number(values.get("historical_daily_rate"))
        comparable = next(
            iter((proposal.evidence or {}).get("automatic_comparable") or []),
            {},
        )
        current_people = next(
            iter((proposal.evidence or {}).get("current_people_rate") or []),
            {},
        )
        output.append(
            {
                "category": category,
                "activity": _activity_label(category),
                "basis_quantity": basis,
                "basis_unit": "sqft",
                "recommended_total_hours": total_hours or None,
                "recommended_crew_size": int(crew_size) if crew_size > 0 else None,
                "recommended_days": days or None,
                "hours_per_1000_sqft": round(total_hours / basis * 1000, 3)
                if total_hours > 0 and basis > 0
                else None,
                "current_people_daily_rate": current_daily_rate or None,
                "historical_daily_rate": historical_daily_rate or None,
                "rate_source": "current_people_tab"
                if current_people
                else "historical_comparable_review_required",
                "estimated_labor_cost_candidate": round(days * current_daily_rate, 2)
                if days > 0 and current_daily_rate > 0
                else None,
                "source_job_id": comparable.get("job_id"),
                "source_job_name": comparable.get("job_name"),
                "source_file": comparable.get("source_file"),
                "scale_factor": comparable.get("scale_factor"),
                "confidence": round(float(proposal.confidence or 0), 3),
                "calibration_status": "calibrated_candidate",
                "blocking_input_required": False,
                "review_required": True,
                "review_reasons": list(proposal.review_reasons or []),
                "formula_authority": "workbook_people_rate_and_labor_formula",
            }
        )
    by_category = {row["category"]: row for row in output}
    historical = [
        row for row in historical_labor_performance if isinstance(row, dict)
    ]
    for category in _required_labor_categories(scope):
        if category in by_category:
            continue
        basis = _labor_basis(scope, category)
        driver = _labor_driver(
            scope,
            category=category,
            proposals=proposals,
            historical_material_usage=historical_material_usage,
        )
        historical_candidate = _semantic_task_labor_candidate(
            data,
            scope=scope,
            category=category,
            driver_quantity=driver["quantity"],
            driver_unit=driver["unit"],
        ) or _historical_labor_candidate(
            historical,
            category=category,
            basis=basis,
            driver_quantity=driver["quantity"],
            driver_unit=driver["unit"],
        )
        current_people = _current_people_rate(
            data,
            category=category,
            crew_size=_number(historical_candidate.get("crew_size")),
        )
        row = {
            "category": category,
            "activity": _activity_label(category),
            "basis_quantity": basis,
            "basis_unit": "sqft",
            "driver_type": historical_candidate.get("driver_type")
            or driver["type"],
            "driver_quantity": historical_candidate.get("driver_quantity")
            or driver["quantity"],
            "driver_unit": historical_candidate.get("driver_unit")
            or driver["unit"],
            "historical_driver_rate": historical_candidate.get("driver_rate"),
            "historical_driver_rate_unit": historical_candidate.get(
                "driver_rate_unit"
            ),
            "historical_support_count": historical_candidate.get(
                "support_count", 0
            ),
            "recommended_total_hours": historical_candidate.get("total_hours"),
            "recommended_crew_size": historical_candidate.get("crew_size"),
            "recommended_days": historical_candidate.get("days"),
            "hours_per_1000_sqft": historical_candidate.get(
                "hours_per_1000_sqft"
            ),
            "current_people_daily_rate": current_people.get("daily_rate"),
            "historical_daily_rate": historical_candidate.get("daily_rate"),
            "rate_source": "current_people_tab"
            if current_people
            else "historical_observation_review_required"
            if historical_candidate
            else "unavailable",
            "estimated_labor_cost_candidate": round(
                _number(historical_candidate.get("days"))
                * _number(current_people.get("daily_rate")),
                2,
            )
            if historical_candidate and current_people
            else None,
            "source_job_id": historical_candidate.get("source_job_id"),
            "source_job_name": None,
            "source_file": historical_candidate.get("source_file"),
            "scale_factor": historical_candidate.get("scale_factor"),
            "confidence": historical_candidate.get("confidence", 0.0),
            "calibration_status": "historical_candidate"
            if historical_candidate
            else "missing_calibration",
            "blocking_input_required": not bool(historical_candidate),
            "review_required": True,
            "review_reasons": historical_candidate.get("review_reasons")
            or [
                "The current assembly requires this activity, but no usable historical productivity candidate was retrieved."
            ],
            "formula_authority": "workbook_people_rate_and_labor_formula",
        }
        output.append(row)
    return output


def _required_labor_categories(scope: dict[str, Any]) -> list[str]:
    required = ["labor_prep"]
    exclusive_text = " ".join(
        str(row.get("scope_text") or "").lower()
        for row in scope.get("canonical_area_audit") or []
        if isinstance(row, dict) and row.get("included_in_total")
    )
    if any(
        token in exclusive_text
        for token in ("full removal", "tear off", "tear-off", "down to wood decking")
    ):
        required.append("labor_tearoff")
    if _number(scope.get("board_basis_sqft")) > 0:
        required.append("labor_board")
    if _number(scope.get("coating_basis_sqft")) > 0:
        required.extend(("labor_base", "labor_top_coat"))
    required.append("labor_cleanup")
    return list(dict.fromkeys(required))


def _historical_labor_candidate(
    observations: list[dict[str, Any]],
    *,
    category: str,
    basis: float,
    driver_quantity: float = 0.0,
    driver_unit: str = "sqft",
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for observation in observations:
        if str(observation.get("category") or "") != category:
            continue
        total_hours = _number(observation.get("total_hours"))
        crew_size = _number(observation.get("crew_size"))
        days = _number(observation.get("days"))
        source = next(
            (
                row
                for row in observation.get("sources") or []
                if isinstance(row, dict)
            ),
            {},
        )
        reference_area = _number(source.get("reference_area_sqft"))
        productivity = observation.get("productivity") or {}
        rate = _number(productivity.get("rate"))
        rate_unit = str(productivity.get("rate_unit") or "").lower()
        hours_per_1000 = 0.0
        scaled_hours = 0.0
        resolved_driver_quantity = driver_quantity or basis
        resolved_driver_unit = driver_unit
        driver_rate_unit = ""
        if (
            rate > 0
            and resolved_driver_quantity > 0
            and resolved_driver_unit == "gal"
            and "hour" in rate_unit
            and ("gal" in rate_unit or "unit" in rate_unit)
        ):
            scaled_hours = resolved_driver_quantity * rate
            driver_rate_unit = "hours_per_gal"
        elif rate > 0 and "1000" in rate_unit and "sq" in rate_unit:
            hours_per_1000 = rate
        elif total_hours > 0 and reference_area > 0:
            hours_per_1000 = total_hours / reference_area * 1000
        if scaled_hours <= 0 and (hours_per_1000 <= 0 or basis <= 0):
            continue
        if scaled_hours <= 0:
            scaled_hours = basis / 1000 * hours_per_1000
            resolved_driver_quantity = basis
            resolved_driver_unit = "sqft"
            driver_rate_unit = "hours_per_1000_sqft"
        scale_factor = basis / reference_area if reference_area > 0 else None
        scaled_days = days * scale_factor if days > 0 and scale_factor else 0.0
        support = max(
            int(_number(observation.get("support_count"))),
            int(_number(productivity.get("evidence_count"))),
            1,
        )
        candidates.append(
            {
                "total_hours": round(scaled_hours, 1),
                "crew_size": int(crew_size) if crew_size > 0 else None,
                "days": round(scaled_days, 2) if scaled_days > 0 else None,
                "hours_per_1000_sqft": round(hours_per_1000, 3),
                "driver_type": productivity.get("driver_type")
                or "historical_productivity",
                "driver_quantity": round(resolved_driver_quantity, 3),
                "driver_unit": resolved_driver_unit,
                "driver_rate": round(rate or hours_per_1000, 6),
                "driver_rate_unit": driver_rate_unit,
                "daily_rate": _number(observation.get("daily_rate")) or None,
                "source_job_id": source.get("job_id"),
                "source_file": source.get("file_name"),
                "scale_factor": round(scale_factor, 6) if scale_factor else None,
                "support_count": support,
                "confidence": round(float(observation.get("confidence") or 0), 3),
                "review_reasons": [
                    "Scaled from semantic historical labor productivity; select a current People-tab crew before workbook generation."
                ],
            }
        )
    if not candidates:
        return {}
    return max(candidates, key=lambda row: (row["support_count"], row["confidence"]))


def _semantic_task_labor_candidate(
    data: Any,
    *,
    scope: dict[str, Any],
    category: str,
    driver_quantity: float,
    driver_unit: str,
) -> dict[str, Any]:
    frame = getattr(data, "semantic_labor_task_rates", None) if data is not None else None
    if (
        frame is None
        or not hasattr(frame, "empty")
        or frame.empty
        or driver_quantity <= 0
    ):
        return {}
    exclusions = {
        str(value or "").strip().lower().rsplit("/", 1)[-1]
        for value in scope.get("exclude_source_files") or []
        if str(value or "").strip()
    }
    rows: list[dict[str, Any]] = []
    for row in frame.fillna("").to_dict(orient="records"):
        if str(row.get("category") or "") != category:
            continue
        if str(row.get("driver_unit") or "").lower() != driver_unit:
            continue
        source_file = str(row.get("source_file") or "")
        if source_file.lower().rsplit("/", 1)[-1] in exclusions:
            continue
        rate = _number(row.get("task_rate"))
        reference_quantity = _number(row.get("driver_quantity"))
        if rate <= 0 or reference_quantity <= 0:
            continue
        rows.append({**row, "task_rate": rate, "driver_quantity": reference_quantity})
    if not rows:
        return {}

    nearby = [
        row
        for row in rows
        if 0.4 <= row["driver_quantity"] / driver_quantity <= 2.5
    ]
    cohort = nearby if len(nearby) >= 5 else rows
    if category == "labor_board":
        direct = [row for row in cohort if row.get("driver_type") == "board_area"]
        if len(direct) >= 5:
            cohort = direct
    rate = float(median(row["task_rate"] for row in cohort))
    crew_values = [
        int(round(_number(row.get("crew_size"))))
        for row in cohort
        if _number(row.get("crew_size")) > 0
    ]
    crew_size = int(round(median(crew_values))) if crew_values else 0
    total_hours = (
        driver_quantity * rate
        if driver_unit == "gal"
        else driver_quantity / 1000 * rate
    )
    days = total_hours / (crew_size * 10.5) if crew_size > 0 else 0.0
    representative = min(cohort, key=lambda row: abs(row["task_rate"] - rate))
    confidence = 0.88 if len(cohort) >= 20 else 0.8 if len(cohort) >= 8 else 0.68
    rate_unit = "hours_per_gal" if driver_unit == "gal" else "hours_per_1000_sqft"
    return {
        "total_hours": round(total_hours, 1),
        "crew_size": crew_size or None,
        "days": round(days, 2) if days > 0 else None,
        "hours_per_1000_sqft": round(total_hours / _labor_basis(scope, category) * 1000, 3)
        if _labor_basis(scope, category) > 0
        else None,
        "driver_type": "historical_standalone_task_cohort",
        "driver_quantity": round(driver_quantity, 3),
        "driver_unit": driver_unit,
        "driver_rate": round(rate, 6),
        "driver_rate_unit": rate_unit,
        "source_job_id": representative.get("job_id"),
        "source_file": representative.get("source_file"),
        "support_count": len(cohort),
        "confidence": confidence,
        "review_reasons": [
            f"Median {rate_unit.replace('_', ' ')} from {len(cohort)} standalone historical task observations; composite labor rows were not split."
        ],
    }


def _labor_driver(
    scope: dict[str, Any],
    *,
    category: str,
    proposals: Iterable[Any],
    historical_material_usage: Iterable[Any],
) -> dict[str, Any]:
    basis = _labor_basis(scope, category)
    if category != "labor_top_coat":
        return {"type": "task_area", "quantity": basis, "unit": "sqft"}
    for key in ("top_coat_gallons", "coating_gallons", "estimated_gallons"):
        gallons = _number(scope.get(key))
        if gallons > 0:
            return {"type": "explicit_coating_gallons", "quantity": gallons, "unit": "gal"}
    rates: list[float] = []
    for proposal in proposals:
        if str(getattr(proposal, "template_bucket", "") or "") != "coating":
            continue
        value = _number((getattr(proposal, "proposed_values", {}) or {}).get("gal_per_100_sqft"))
        if 0.1 <= value <= 10:
            rates.append(value)
    for observation in historical_material_usage:
        if not isinstance(observation, dict) or str(observation.get("category") or "") != "coating":
            continue
        value = _measurement(
            observation.get("application_parameters") or [],
            ("gal_per_100_sqft",),
        )
        if 0.1 <= value <= 10:
            rates.append(value)
    if rates and basis > 0:
        gallons = basis / 100 * float(median(rates))
        return {
            "type": "coating_gallons_from_selected_coverage",
            "quantity": round(gallons, 3),
            "unit": "gal",
        }
    return {"type": "coating_area", "quantity": basis, "unit": "sqft"}


def _current_people_rate(
    data: Any,
    *,
    category: str,
    crew_size: float,
) -> dict[str, Any]:
    frame = getattr(data, "template_labor_options", None) if data is not None else None
    if frame is None or not hasattr(frame, "empty") or frame.empty or crew_size <= 0:
        return {}
    candidates: list[dict[str, Any]] = []
    for row in frame.fillna("").to_dict(orient="records"):
        if _slug(row.get("template_type")) != "roofing":
            continue
        if _slug(row.get("source_type")) != "people_daily_rate_selector":
            continue
        if int(_number(row.get("lookup_key"))) != int(crew_size):
            continue
        values = row.get("source_values_json")
        if not isinstance(values, dict):
            continue
        daily_rate = _people_daily_rate(values, crew_size=int(crew_size))
        if daily_rate <= 0:
            continue
        candidates.append(
            {
                "daily_rate": daily_rate,
                "crew_size": int(crew_size),
                "template_labor_option_id": row.get("template_labor_option_id"),
                "template_name": row.get("template_name"),
                "labor_package": row.get("labor_package"),
            }
        )
    exact = [
        row for row in candidates if _slug(row.get("labor_package")) == _slug(category)
    ]
    generic = [row for row in candidates if not _slug(row.get("labor_package"))]
    pool = exact or generic or candidates
    unique_rates = {round(_number(row.get("daily_rate")), 6) for row in pool}
    return pool[0] if len(unique_rates) == 1 else {}


def _people_daily_rate(values: dict[str, Any], *, crew_size: int) -> float:
    direct = _number(values.get("daily_rate") or values.get("rate"))
    if direct > 0:
        return direct
    components = values.get("crew_components") or values.get("values") or []
    if not isinstance(components, list) or crew_size <= 0:
        return 0.0
    hours_per_day = _number(values.get("hours_per_day")) or 10.0
    hourly_burdened = 0.0
    for component in components[:crew_size]:
        if not isinstance(component, dict):
            continue
        wage = _number(component.get("hourly_wage"))
        burden = _number(component.get("burden_rate")) or 1.0
        hourly_burdened += wage * burden
    return hourly_burdened * hours_per_day


def _historical_area_ratio(
    observations: list[dict[str, Any]],
    category: str,
) -> dict[str, Any] | None:
    aliases = {
        "roofing_foam": {"foam", "roofing_foam"},
        "coating": {"coating"},
        "board_stock": {"board_stock"},
    }.get(category, {category})
    weighted: list[tuple[float, int]] = []
    for observation in observations:
        if str(observation.get("category") or "") not in aliases:
            continue
        basis = _measurement(
            observation.get("basis_measurements") or [],
            ("basis_sqft", "area_sqft", "board_area_sqft"),
        )
        references = [
            _number(source.get("reference_area_sqft"))
            for source in observation.get("sources") or []
            if isinstance(source, dict)
        ]
        reference = next((value for value in references if value > 0), 0.0)
        support = max(
            int(_number(observation.get("support_count"))),
            len([value for value in references if value > 0]),
        )
        if basis <= 0 or reference <= 0 or support < 2:
            continue
        ratio = basis / reference
        if 1.0 <= ratio <= 1.25:
            weighted.append((ratio, support))
    if not weighted:
        return None
    total_support = sum(support for _, support in weighted)
    ratio = sum(value * support for value, support in weighted) / total_support
    return {"ratio": round(ratio, 6), "support_count": total_support}


def _measurement(values: Iterable[Any], names: tuple[str, ...]) -> float:
    for row in values:
        if not isinstance(row, dict) or str(row.get("name") or "") not in names:
            continue
        value = _number(row.get("value"))
        if value > 0:
            return value
    return 0.0


def _labor_basis(scope: dict[str, Any], category: str) -> float:
    if category in {"labor_tearoff", "labor_board"}:
        return _number(scope.get("board_basis_sqft"))
    if category in {"labor_base", "labor_top_coat"}:
        return _number(scope.get("coating_basis_sqft"))
    return _number(scope.get("canonical_area_total_sqft"))


def _activity_label(category: str) -> str:
    return category.removeprefix("labor_").replace("_", " ").title()


def _slug(value: Any) -> str:
    return "_".join(str(value or "").strip().lower().replace("-", " ").split())


def _round_up(value: float, increment: float) -> float:
    if value <= 0 or increment <= 0:
        return 0.0
    return round(math.ceil((value - 1e-9) / increment) * increment, 3)


def _number(value: Any) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0
