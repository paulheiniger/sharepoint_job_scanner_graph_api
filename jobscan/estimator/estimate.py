from __future__ import annotations

from typing import Any

import pandas as pd

from .calibration import calibrate_from_history
from .decision_tree import evaluate_decision_tree
from .line_items import summarize_similar_job_buckets
from .labor import estimate_labor, estimate_travel_impact
from .materials import aggregate_line_items, estimate_materials
from .rules import extract_scope, first_nonblank, to_float
from .schemas import EstimatorAssumptions, EstimatorData
from .similarity import find_similar_jobs


def estimate_equipment_and_other(line_item_stats: dict[str, Any], area: float | None) -> dict[str, float]:
    totals = line_item_stats.get("category_totals") or {}
    materials = float(totals.get("materials") or 0)
    equipment = float(totals.get("equipment") or 0)
    travel = float(totals.get("travel/lodging") or 0)
    subcontractor = float(totals.get("subcontractor") or 0)
    if materials <= 0:
        basis = max(float(area or 0) * 0.15, 500.0) if area else 500.0
    else:
        basis = materials
    return {
        "equipment_cost_low": round((equipment or basis * 0.04) * 0.75, 2),
        "equipment_cost_high": round((equipment or basis * 0.08) * 1.25, 2),
        "travel_cost_low": round((travel or 0) * 0.75, 2),
        "travel_cost_high": round((travel or 0) * 1.25, 2),
        "subcontractor_cost_low": round(subcontractor * 0.85, 2),
        "subcontractor_cost_high": round(subcontractor * 1.2, 2),
    }


def confidence_label(scope: dict[str, Any], similar_jobs: pd.DataFrame, pricing_review: bool) -> str:
    if scope.get("human_review_required") or pricing_review:
        return "low"
    if len(similar_jobs) >= 3:
        return "medium"
    return "low"


def build_estimate(
    notes: str,
    data: EstimatorData,
    overrides: dict[str, Any] | None = None,
    assumptions: EstimatorAssumptions | None = None,
) -> dict[str, Any]:
    assumptions = assumptions or EstimatorAssumptions()
    scope = extract_scope(notes, overrides)
    similar = find_similar_jobs(data, scope, limit=8)
    similar_job_ids = similar["job_id"].dropna().astype(str).tolist() if "job_id" in similar.columns else []
    line_stats = aggregate_line_items(data.line_items, similar_job_ids)
    classified_source = data.classified_line_items if not data.classified_line_items.empty else data.line_items
    template_line_item_summary = summarize_similar_job_buckets(classified_source, similar)
    calibration = calibrate_from_history(similar, data.line_items, scope)
    decision = evaluate_decision_tree(scope, calibration)
    materials = estimate_materials(scope, data.pricing, data.line_items, assumptions, decision)
    labor = estimate_labor(scope, similar, data.tracking_summary, assumptions, decision)
    area = to_float(scope.get("surface_area_sqft")) or to_float(scope.get("wall_area_sqft"))
    other = estimate_equipment_and_other(line_stats, area)
    travel = estimate_travel_impact(
        scope,
        recommended_crew_size=int(labor.get("recommended_crew_size") or 1),
        estimated_work_days=int(labor.get("estimated_duration_days_high") or 1),
        assumptions=assumptions,
    )
    if travel.get("travel_distance_bucket") != decision.get("condition_flags", {}).get("travel_distance_bucket"):
        decision = evaluate_decision_tree(scope, calibration, travel_distance_bucket=str(travel.get("travel_distance_bucket") or "unknown"))
        materials = estimate_materials(scope, data.pricing, data.line_items, assumptions, decision)
        labor = estimate_labor(scope, similar, data.tracking_summary, assumptions, decision)
        travel = estimate_travel_impact(
            scope,
            recommended_crew_size=int(labor.get("recommended_crew_size") or 1),
            estimated_work_days=int(labor.get("estimated_duration_days_high") or 1),
            assumptions=assumptions,
        )
    other["travel_cost_low"] = max(other["travel_cost_low"], float(travel.get("travel_vehicle_cost") or 0))
    other["travel_cost_high"] = max(other["travel_cost_high"], float(travel.get("travel_vehicle_cost") or 0) * 1.15)

    subtotal_low = (
        materials["material_cost_low"]
        + labor["labor_cost_low"]
        + other["equipment_cost_low"]
        + other["travel_cost_low"]
        + other["subcontractor_cost_low"]
    )
    subtotal_high = (
        materials["material_cost_high"]
        + labor["labor_cost_high"]
        + other["equipment_cost_high"]
        + other["travel_cost_high"]
        + other["subcontractor_cost_high"]
    )
    overhead_profit_low = subtotal_low * 0.18
    overhead_profit_high = subtotal_high * 0.28
    estimate_low = subtotal_low + overhead_profit_low
    estimate_high = subtotal_high + overhead_profit_high

    drivers = []
    if area:
        drivers.append(f"{area:,.0f} sqft quantity basis")
    if first_nonblank(scope.get("coating_type")):
        drivers.append(f"{scope['coating_type']} coating")
    if scope.get("foam_required"):
        drivers.append("foam thickness / board feet")
    if travel.get("lodging_required_possible"):
        drivers.append("possible lodging or distant travel")
    if scope.get("roof_condition"):
        drivers.append(f"{scope['roof_condition']} condition/prep")
    for flag in decision.get("human_review_flags", []):
        drivers.append(flag)

    missing_info = list(scope.get("missing_info") or [])
    pricing_warnings = list(materials.get("pricing_warnings") or [])
    if travel.get("needs_travel_review"):
        pricing_warnings.append("Travel assumptions require review.")
    pricing_warnings.extend(decision.get("human_review_flags", []))

    return {
        "scope": scope,
        "decision_tree": decision,
        "calibration": calibration,
        "similar_jobs": similar,
        "line_item_patterns": line_stats,
        "template_line_item_summary": template_line_item_summary,
        "materials": materials,
        "labor": labor,
        "travel": travel,
        "estimate_range": {
            "material_cost_low": materials["material_cost_low"],
            "material_cost_high": materials["material_cost_high"],
            "labor_cost_low": labor["labor_cost_low"],
            "labor_cost_high": labor["labor_cost_high"],
            "equipment_cost_low": other["equipment_cost_low"],
            "equipment_cost_high": other["equipment_cost_high"],
            "travel_cost_low": other["travel_cost_low"],
            "travel_cost_high": other["travel_cost_high"],
            "subcontractor_cost_low": other["subcontractor_cost_low"],
            "subcontractor_cost_high": other["subcontractor_cost_high"],
            "overhead_profit_low": round(overhead_profit_low, 2),
            "overhead_profit_high": round(overhead_profit_high, 2),
            "estimate_low": round(estimate_low, 2),
            "estimate_high": round(estimate_high, 2),
            "confidence": confidence_label(scope, similar, bool(materials.get("needs_pricing_review"))),
            "major_cost_drivers": drivers,
            "missing_info": missing_info,
            "pricing_warnings": pricing_warnings,
            "human_review_required": bool(
                scope.get("human_review_required")
                or materials.get("needs_pricing_review")
                or travel.get("needs_travel_review")
                or decision.get("human_review_flags")
            ),
        },
        "source_files_used": data.source_files_used,
        "data_warnings": data.warnings,
    }
