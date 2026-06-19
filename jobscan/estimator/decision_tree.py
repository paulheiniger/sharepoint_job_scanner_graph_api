from __future__ import annotations

from typing import Any

from .rules import default_crew_size, first_nonblank, to_float


BASE_PRODUCTIVITY_BY_PROJECT_TYPE = {
    "roof coating": 2600.0,
    "coated foam roof": 1800.0,
    "spray foam insulation": 2200.0,
    "roof repair": 1200.0,
    "wall insulation": 2400.0,
}

ACCESS_MULTIPLIERS = {"low": 1.0, "medium": 1.12, "high": 1.28}
DETAIL_MULTIPLIERS = {"": 1.0, "low": 1.0, "medium": 1.12, "high": 1.3}
CONDITION_MULTIPLIERS = {"": 1.0, "good": 1.0, "fair": 1.12, "poor/rusted": 1.35}
TRAVEL_SETUP_MULTIPLIERS = {"local": 1.0, "regional": 1.08, "distant": 1.18, "unknown": 1.1}


def warranty_wet_mils(warranty_target: Any, coating_type: str) -> float:
    target = to_float(warranty_target)
    key = coating_type.lower()
    if target and target >= 20:
        return 30.0 if "silicone" in key else 36.0
    if target and target >= 15:
        return 26.0 if "silicone" in key else 32.0
    if target and target >= 10:
        return 24.0 if "silicone" in key else 30.0
    return 24.0 if "silicone" in key else 30.0 if "acrylic" in key else 22.0 if "urethane" in key else 24.0


def condition_flags(scope: dict[str, Any], travel_distance_bucket: str = "unknown") -> dict[str, Any]:
    return {
        "substrate": first_nonblank(scope.get("substrate")),
        "roof_condition": first_nonblank(scope.get("roof_condition")),
        "insulation_present": bool(scope.get("insulation_present")),
        "insulation_missing": bool(scope.get("insulation_missing")),
        "condensation_risk": bool(scope.get("condensation_risk")),
        "warranty_target": scope.get("warranty_target"),
        "access_complexity": first_nonblank(scope.get("access_complexity")),
        "penetrations_complexity": first_nonblank(scope.get("penetrations_complexity")),
        "rust_level": first_nonblank(scope.get("rust_level")),
        "tearoff_likely": bool(scope.get("tearoff_likely")),
        "coating_type": first_nonblank(scope.get("coating_type")),
        "foam_thickness_inches": scope.get("foam_thickness_inches"),
        "travel_distance_bucket": travel_distance_bucket,
    }


def evaluate_decision_tree(
    scope: dict[str, Any],
    calibration: dict[str, Any] | None = None,
    *,
    travel_distance_bucket: str = "unknown",
) -> dict[str, Any]:
    calibration = calibration or {}
    flags = condition_flags(scope, travel_distance_bucket)
    project_type = first_nonblank(scope.get("project_type")) or "roof coating"
    coating_type = flags["coating_type"]
    recommended_scope: list[str] = []
    review_flags: list[str] = []

    if flags["substrate"].lower() == "metal" and (flags["rust_level"] or "rust" in flags["roof_condition"].lower()):
        recommended_scope.extend(["Treat fasteners", "Treat seams", "Evaluate rust primer"])
    if flags["insulation_missing"] or flags["condensation_risk"]:
        recommended_scope.append("Review foam thickness / condensation control")
        review_flags.append("Foam or insulation design review required")
    if flags["warranty_target"]:
        recommended_scope.append(f"Design coating system for {flags['warranty_target']}-year warranty target")
    if flags["roof_condition"] == "poor/rusted":
        recommended_scope.append("Include repair/restoration allowance")
        review_flags.append("Tear-off or substrate repair review required")
        flags["tearoff_likely"] = True if scope.get("tearoff_likely") else flags["tearoff_likely"]
    if flags["penetrations_complexity"] == "high":
        recommended_scope.append("Increase detail labor for penetrations")
    if flags["access_complexity"] == "high":
        recommended_scope.append("Include setup/equipment allowance for difficult access")

    wet_mils = warranty_wet_mils(flags["warranty_target"], coating_type)
    foam_thickness = to_float(flags["foam_thickness_inches"])
    if (flags["insulation_missing"] or flags["condensation_risk"]) and not foam_thickness:
        foam_thickness = 1.5

    base_productivity = BASE_PRODUCTIVITY_BY_PROJECT_TYPE.get(project_type.lower(), 2200.0)
    access_multiplier = ACCESS_MULTIPLIERS.get(flags["access_complexity"].lower(), 1.0)
    detail_multiplier = DETAIL_MULTIPLIERS.get(flags["penetrations_complexity"].lower(), 1.0)
    condition_multiplier = CONDITION_MULTIPLIERS.get(flags["roof_condition"].lower(), 1.0)
    travel_multiplier = TRAVEL_SETUP_MULTIPLIERS.get(travel_distance_bucket, 1.1)
    combined_multiplier = round(access_multiplier * detail_multiplier * condition_multiplier * travel_multiplier, 3)
    adjusted_productivity = round(base_productivity / combined_multiplier, 1) if combined_multiplier else base_productivity

    if not recommended_scope:
        recommended_scope.append("Base scope from parsed project type and similar-job evidence")
    return {
        "condition_flags": flags,
        "recommended_scope": recommended_scope,
        "material_assumptions": {
            "coating_wet_mils": wet_mils,
            "coating_gallons_formula": "surface_area_sqft * wet_mils / 1604 * (1 + waste_factor)",
            "foam_board_feet_formula": "surface_area_sqft * foam_thickness_inches",
            "foam_thickness_inches": foam_thickness,
            "primer_allowance_recommended": "Evaluate rust primer" in recommended_scope,
            "seam_treatment_recommended": "Treat seams" in recommended_scope,
            "fastener_treatment_recommended": "Treat fasteners" in recommended_scope,
        },
        "labor_modifiers": {
            "base_productivity_sqft_per_day": base_productivity,
            "access_multiplier": access_multiplier,
            "penetration_detail_multiplier": detail_multiplier,
            "prep_condition_multiplier": condition_multiplier,
            "travel_setup_multiplier": travel_multiplier,
            "combined_labor_multiplier": combined_multiplier,
            "adjusted_productivity_sqft_per_day": adjusted_productivity,
        },
        "crew_assumptions": {
            "recommended_crew_size": default_crew_size(project_type),
            "crew_basis": f"default crew size for {project_type}",
        },
        "calibration_summary": calibration,
        "human_review_flags": review_flags,
    }
