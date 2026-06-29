from __future__ import annotations

import csv
import math
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .materials import find_current_price
from .rules import first_nonblank, to_float

DEFAULT_HOURLY_RATE = 72.0

MATERIAL_PACKAGES: list[dict[str, Any]] = [
    {"package": "coating", "label": "Silicone", "keywords": ["silicone", "coating"], "default_unit": "gal"},
    {"package": "primer", "label": "Primer", "keywords": ["primer"], "default_unit": "unit"},
    {"package": "seam_treatment", "label": "Seam Treatment", "keywords": ["seam", "sealant", "fabric"], "default_unit": "lf"},
    {"package": "fastener_treatment", "label": "Fastener Treatment", "keywords": ["fastener", "screw"], "default_unit": "ea"},
    {"package": "caulk_detail", "label": "Caulk / Detail", "keywords": ["caulk", "sealant", "detail"], "default_unit": "unit"},
]

LABOR_PACKAGES: list[dict[str, Any]] = [
    {"package": "labor_prep", "label": "Prep"},
    {"package": "labor_prime", "label": "Prime"},
    {"package": "labor_base", "label": "Base Coat"},
    {"package": "labor_top_coat", "label": "Top Coat"},
    {"package": "labor_seam_sealer", "label": "Seam Treatment"},
    {"package": "labor_details", "label": "Details"},
    {"package": "labor_cleanup", "label": "Cleanup"},
    {"package": "labor_loading", "label": "Loading"},
]

ADDER_ROWS: list[dict[str, Any]] = [
    {"adder": "travel", "label": "Travel"},
    {"adder": "lift", "label": "Lift"},
    {"adder": "generator", "label": "Generator"},
    {"adder": "dumpster", "label": "Dumpster"},
    {"adder": "hotel", "label": "Hotel"},
    {"adder": "inspection", "label": "Inspection"},
    {"adder": "infrared", "label": "Infrared"},
    {"adder": "mobilization", "label": "Mobilization"},
    {"adder": "misc", "label": "Misc."},
]


def safe_number(value: Any, default: float = 0.0) -> float:
    number = to_float(value)
    if number is None or not math.isfinite(number):
        return default
    return float(number)


def optional_number(value: Any) -> float | None:
    number = to_float(value)
    if number is None or not math.isfinite(number):
        return None
    return float(number)


def _rec_value(recommendation: Any, key: str, default: Any = None) -> Any:
    if isinstance(recommendation, dict):
        return recommendation.get(key, default)
    return getattr(recommendation, key, default)


def _records(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, pd.DataFrame):
        return value.to_dict(orient="records")
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    return []


def _frame(data: Any, attr: str) -> pd.DataFrame:
    value = getattr(data, attr, pd.DataFrame()) if data is not None else pd.DataFrame()
    return value if isinstance(value, pd.DataFrame) else pd.DataFrame(value)


def _normalized(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("_", " ").replace("-", " ").split())


def _estimate_area(scope: dict[str, Any]) -> float:
    return safe_number(
        first_nonblank(
            scope.get("net_sqft"),
            scope.get("estimated_sqft"),
            scope.get("surface_area_sqft"),
            scope.get("net_area_sqft"),
            scope.get("C12_estimated_sqft"),
        ),
        0.0,
    )


def _scope_from_recommendation(recommendation: Any) -> dict[str, Any]:
    parsed = dict(_rec_value(recommendation, "parsed_fields", {}) or {})
    dimension_summary = parsed.get("dimension_summary") or {}
    return {
        "project_type": first_nonblank(parsed.get("project_type"), "roof coating"),
        "roof_type_substrate": first_nonblank(parsed.get("substrate"), parsed.get("roof_type"), ""),
        "gross_sqft": safe_number(parsed.get("gross_area_sqft") or dimension_summary.get("gross_area_sqft"), 0.0),
        "deduction_sqft": safe_number(parsed.get("deduction_area_sqft") or dimension_summary.get("deduction_area_sqft"), 0.0),
        "net_sqft": safe_number(
            parsed.get("estimated_sqft")
            or parsed.get("surface_area_sqft")
            or parsed.get("net_area_sqft")
            or dimension_summary.get("net_area_sqft"),
            0.0,
        ),
        "warranty_years": safe_number(parsed.get("warranty_target_years") or parsed.get("warranty_years"), 0.0),
        "coating_type": first_nonblank(parsed.get("coating_type"), ""),
        "roof_condition": first_nonblank(parsed.get("roof_condition"), ""),
        "access_complexity": first_nonblank(parsed.get("access_complexity"), ""),
        "penetrations_complexity": first_nonblank(parsed.get("penetrations_complexity"), ""),
    }


def _plan_included_package(recommendation: Any, package: str) -> bool:
    package_text = _normalized(package)
    for row in _records(_rec_value(recommendation, "material_plan", [])):
        text = _normalized(" ".join(str(row.get(key) or "") for key in ("category", "package", "item", "notes")))
        if package_text in text and row.get("included_in_total") is not False:
            return True
        if package == "coating" and "coating" in text and row.get("included_in_total") is not False:
            return True
    return False


def _package_suggestion_status(recommendation: Any, package: str) -> str:
    package_text = _normalized(package)
    for row in _records(_rec_value(recommendation, "material_plan", [])):
        text = _normalized(" ".join(str(row.get(key) or "") for key in ("category", "package", "item", "notes")))
        if package_text in text or (package == "coating" and "coating" in text):
            if row.get("included_in_total") is False or row.get("needs_review") is True or row.get("review_required") is True:
                return "review"
            return "yes"
    if package == "coating" and first_nonblank((_rec_value(recommendation, "parsed_fields", {}) or {}).get("coating_type")):
        return "yes"
    return "no"


def _plan_included_labor(recommendation: Any, package: str) -> bool:
    for row in _records(_rec_value(recommendation, "labor_plan", [])):
        task = str(row.get("task") or row.get("labor_package") or "")
        if task == package and row.get("included_in_total") is not False:
            return True
    return False


def _labor_suggestion_status(recommendation: Any, package: str) -> str:
    for row in _records(_rec_value(recommendation, "labor_plan", [])):
        task = str(row.get("task") or row.get("labor_package") or "")
        if task == package:
            if row.get("included_in_total") is False or row.get("needs_review") is True or row.get("review_required") is True:
                return "review"
            return "yes"
    return "no"


def _suggestion_reason(package: str, scope: dict[str, Any], status: str) -> str:
    condition = _normalized(scope.get("roof_condition"))
    penetrations = _normalized(scope.get("penetrations_complexity"))
    if package == "coating" and status == "yes":
        return "Filled in because the notes describe a coating/restoration scope."
    if package == "primer":
        if status in {"yes", "review"}:
            return "Filled in for estimator review because the notes indicate substrate or condition concerns."
        return "Shown but unchecked because notes do not mention primer, adhesion, rust, bleed-through, or manufacturer primer requirements."
    if package == "seam_treatment":
        if status in {"yes", "review"}:
            return "Filled in because the notes mention seam or detail work."
        return "Shown but unchecked because notes do not mention open seams, failed seams, seam repair, or leaks."
    if package == "fastener_treatment":
        if status in {"yes", "review"}:
            return "Filled in because the notes mention fasteners or exposed-fastener metal roof details."
        return "Shown but unchecked because notes do not mention exposed fasteners or fastener repairs."
    if package == "caulk_detail":
        if status in {"yes", "review"} or "high" in penetrations:
            return "Filled in because the notes indicate detail or penetration work."
        return "Shown but unchecked because notes do not indicate heavy details or penetration repairs."
    if package.startswith("labor_") and status in {"yes", "review"}:
        return "Filled in because this labor package appears in the historical company default set for this scope."
    if package.startswith("labor_prime"):
        return "Shown but unchecked because primer is not currently included."
    if condition in {"excellent", "good"} and package in {"labor_seam_sealer", "labor_details"}:
        return "Shown but unchecked because the described condition is clean/light and does not call for heavy detail labor."
    return "Shown but unchecked; available for estimator adjustment."


def _relationship_score(row: pd.Series, scope: dict[str, Any], package: str) -> float:
    score = safe_number(row.get("evidence_count") or row.get("job_count"), 0)
    if _normalized(row.get("package")) == _normalized(package):
        score += 1000
    if _normalized(row.get("division")) == "roofing":
        score += 100
    if _normalized(row.get("template_type")) == "roofing":
        score += 60
    substrate = _normalized(scope.get("roof_type_substrate"))
    if substrate and substrate in _normalized(row.get("substrate")):
        score += 40
    coating_type = _normalized(scope.get("coating_type"))
    if coating_type and coating_type in _normalized(row.get("coating_type")):
        score += 30
    warranty = optional_number(scope.get("warranty_years"))
    row_warranty = optional_number(row.get("warranty_years"))
    if warranty is not None and row_warranty is not None and int(warranty) == int(row_warranty):
        score += 20
    return score


def best_relationship_row(frame: pd.DataFrame, package: str, scope: dict[str, Any]) -> dict[str, Any] | None:
    if frame.empty or "package" not in frame.columns:
        return None
    rows = frame[frame["package"].astype(str).str.lower().eq(str(package).lower())].copy()
    if rows.empty:
        return None
    rows["_workbench_score"] = rows.apply(lambda row: _relationship_score(row, scope, package), axis=1)
    for column in ("evidence_count", "job_count"):
        if column not in rows.columns:
            rows[column] = 0
    rows = rows.sort_values(["_workbench_score", "evidence_count", "job_count"], ascending=False, na_position="last")
    return rows.iloc[0].drop(labels=["_workbench_score"], errors="ignore").to_dict()


def _price_for_package(pricing: pd.DataFrame, package_spec: dict[str, Any], scope: dict[str, Any]) -> tuple[float, str]:
    if pricing.empty:
        return 0.0, ""
    keywords = list(package_spec.get("keywords") or [])
    if package_spec["package"] == "coating" and scope.get("coating_type"):
        keywords.insert(0, str(scope.get("coating_type")))
    preferred = "price_per_gallon" if package_spec["package"] == "coating" else "unit_price"
    price = find_current_price(pricing, keywords, preferred)
    if not price:
        return 0.0, ""
    for column in (preferred, "price_per_unit", "unit_price", "price_per_sqft", "price_per_gallon"):
        number = optional_number(price.get(column))
        if number is not None and number > 0:
            label = first_nonblank(price.get("product_name"), price.get("description"), price.get("pricing_item_id"), "pricing_catalog")
            return number, str(label)
    return 0.0, ""


def _confidence(evidence_count: Any) -> str:
    count = safe_number(evidence_count, 0)
    if count >= 10:
        return "high"
    if count >= 5:
        return "medium"
    if count > 0:
        return "low"
    return "none"


def _historical_usage_rate(data: Any, package: str, scope: dict[str, Any], evidence_count: int) -> float:
    summary = _frame(data, "job_package_summary")
    if summary.empty or "job_id" not in summary.columns or "package" not in summary.columns:
        return 0.0
    rows = summary.copy()
    if "division" in rows.columns:
        roofing = rows["division"].astype(str).str.lower().eq("roofing")
        if roofing.any():
            rows = rows[roofing].copy()
    substrate = _normalized(scope.get("roof_type_substrate"))
    if substrate and "substrate" in rows.columns:
        scoped = rows[rows["substrate"].astype(str).str.lower().str.contains(substrate, na=False)]
        if not scoped.empty:
            rows = scoped
    denominator = rows["job_id"].dropna().astype(str).nunique()
    if denominator <= 0:
        return 0.0
    package_jobs = rows[rows["package"].astype(str).str.lower().eq(str(package).lower())]["job_id"].dropna().astype(str).nunique()
    if package_jobs <= 0 and evidence_count > 0:
        package_jobs = evidence_count
    return round(min(package_jobs / denominator, 1.0), 4)


def _material_explanation(
    *,
    package: str,
    relationship: dict[str, Any],
    evidence_count: int,
    qty_per_sqft: float,
    status: str,
    scope: dict[str, Any],
) -> str:
    reason = _suggestion_reason(package, scope, status)
    if evidence_count > 0 and qty_per_sqft > 0:
        return (
            f"Used in {evidence_count} comparable jobs. Median when used: {qty_per_sqft:g} per sqft. "
            f"{reason}"
        )
    if evidence_count > 0:
        return f"Used in {evidence_count} comparable jobs, but no reliable historical quantity was found; left at 0 for estimator review. {reason}"
    return f"No reliable historical quantity found; left at 0 for estimator review. {reason}"


def _labor_explanation(
    *,
    package: str,
    evidence_count: int,
    hours_per_1000: float,
    status: str,
    scope: dict[str, Any],
) -> str:
    reason = _suggestion_reason(package, scope, status)
    if evidence_count > 0 and hours_per_1000 > 0:
        return (
            f"Used in {evidence_count} comparable jobs. Median when used: {hours_per_1000:g} hours per 1,000 sqft. "
            f"{reason}"
        )
    if evidence_count > 0:
        return f"Used in {evidence_count} comparable jobs, but no reliable labor rate was found; left at 0 for estimator review. {reason}"
    return f"No reliable historical labor rate found; left at 0 for estimator review. {reason}"


def material_workbench_rows(recommendation: Any, data: Any, scope: dict[str, Any]) -> list[dict[str, Any]]:
    area = _estimate_area(scope)
    ratios = _frame(data, "relationship_material_qty_ratios")
    pricing = _frame(data, "pricing_catalog")
    if pricing.empty:
        pricing = _frame(data, "pricing")
    rows: list[dict[str, Any]] = []
    for spec in MATERIAL_PACKAGES:
        package = spec["package"]
        relationship = best_relationship_row(ratios, package, scope) or {}
        qty_per_sqft = safe_number(relationship.get("median_qty_per_sqft"), 0.0)
        evidence_count = int(safe_number(relationship.get("evidence_count") or relationship.get("job_count"), 0))
        unit_price, price_source = _price_for_package(pricing, spec, scope)
        status = _package_suggestion_status(recommendation, package)
        include = status == "yes"
        if package == "coating" and scope.get("coating_type"):
            status = "yes"
            include = True
        editable_qty_per_sqft = qty_per_sqft if include else 0.0
        calculated_quantity = editable_qty_per_sqft * area if include and area else 0.0
        rows.append(
            {
                "include": bool(include),
                "package": spec["label"],
                "package_key": package,
                "suggested_by_notes_rules": status,
                "historical_usage_rate": _historical_usage_rate(data, package, scope, evidence_count),
                "historical_qty_per_sqft": round(qty_per_sqft, 6),
                "editable_qty_per_sqft": round(editable_qty_per_sqft, 6),
                "calculated_quantity": round(calculated_quantity, 2),
                "unit": relationship.get("unit") or spec.get("default_unit"),
                "current_unit_price": round(unit_price, 4) if unit_price else 0.0,
                "estimated_cost": round(calculated_quantity * unit_price, 2) if unit_price else 0.0,
                "evidence_count": evidence_count,
                "confidence": relationship.get("confidence") or _confidence(evidence_count),
                "source": "relationship_material_qty_ratios" if relationship else "no_sufficient_evidence",
                "pricing_source": price_source,
                "explanation": _material_explanation(
                    package=package,
                    relationship=relationship,
                    evidence_count=evidence_count,
                    qty_per_sqft=qty_per_sqft,
                    status=status,
                    scope=scope,
                ),
            }
        )
    return rows


def labor_workbench_rows(recommendation: Any, data: Any, scope: dict[str, Any], hourly_rate: float = DEFAULT_HOURLY_RATE) -> list[dict[str, Any]]:
    area = _estimate_area(scope)
    rates = _frame(data, "relationship_labor_rates")
    rows: list[dict[str, Any]] = []
    for spec in LABOR_PACKAGES:
        package = spec["package"]
        relationship = best_relationship_row(rates, package, scope) or {}
        hours_per_1000 = safe_number(relationship.get("median_hours_per_1000_sqft"), 0.0)
        evidence_count = int(safe_number(relationship.get("evidence_count") or relationship.get("job_count"), 0))
        status = _labor_suggestion_status(recommendation, package)
        include = status == "yes"
        editable_hours_per_1000 = hours_per_1000 if include else 0.0
        calculated_hours = editable_hours_per_1000 * area / 1000 if include and area else 0.0
        crew_size = int(safe_number(relationship.get("median_crew_size"), 4) or 4)
        rows.append(
            {
                "include": bool(include),
                "labor_package": spec["label"],
                "package_key": package,
                "suggested_by_notes_rules": status,
                "historical_hours_per_1000_sqft": round(hours_per_1000, 4),
                "editable_hours_per_1000_sqft": round(editable_hours_per_1000, 4),
                "calculated_hours": round(calculated_hours, 2),
                "crew_size": crew_size,
                "labor_rate": hourly_rate,
                "estimated_cost": round(calculated_hours * hourly_rate, 2),
                "evidence_count": evidence_count,
                "confidence": relationship.get("confidence") or _confidence(evidence_count),
                "source": "relationship_labor_rates" if relationship else "no_sufficient_evidence",
                "explanation": _labor_explanation(
                    package=package,
                    evidence_count=evidence_count,
                    hours_per_1000=hours_per_1000,
                    status=status,
                    scope=scope,
                ),
            }
        )
    return rows


def adder_workbench_rows(recommendation: Any) -> list[dict[str, Any]]:
    travel = _rec_value(recommendation, "travel_plan", {}) or {}
    travel_cost = safe_number(travel.get("travel_vehicle_cost"), 0.0) + safe_number(travel.get("travel_labor_cost"), 0.0)
    rows = []
    for spec in ADDER_ROWS:
        is_travel = spec["adder"] == "travel"
        rows.append(
            {
                "include": bool(is_travel and travel_cost > 0),
                "adder": spec["label"],
                "adder_key": spec["adder"],
                "editable_value": round(travel_cost, 2) if is_travel else 0.0,
                "estimated_cost": round(travel_cost, 2) if is_travel else 0.0,
                "confidence": "review" if is_travel else "none",
                "source": "travel_plan" if is_travel and travel_cost > 0 else "manual",
                "notes": first_nonblank(travel.get("travel_notes"), "") if is_travel else "",
            }
        )
    return rows


def build_estimating_workbench(recommendation: Any, data: Any = None, scope_override: dict[str, Any] | None = None) -> dict[str, Any]:
    scope = {**_scope_from_recommendation(recommendation), **(scope_override or {})}
    estimate_id = first_nonblank((_rec_value(recommendation, "parsed_fields", {}) or {}).get("run_id"), f"estimate-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}")
    return {
        "estimate_id": estimate_id,
        "scope": scope,
        "materials": material_workbench_rows(recommendation, data, scope),
        "labor": labor_workbench_rows(recommendation, data, scope),
        "adders": adder_workbench_rows(recommendation),
        "similar_jobs": _records(_rec_value(recommendation, "similar_examples", [])),
        "review_flags": list(_rec_value(recommendation, "review_flags", []) or []),
        "suggested_rules": [
            {
                "rule": "Suggested rules are collected for future approval dashboards.",
                "status": "placeholder",
                "applied_automatically": False,
            }
        ],
    }


def _records_from_editor(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, pd.DataFrame):
        return value.to_dict(orient="records")
    return _records(value)


def recalculate_workbench_tables(workbench: dict[str, Any], hourly_rate: float = DEFAULT_HOURLY_RATE) -> dict[str, Any]:
    updated = deepcopy(workbench)
    scope = updated.setdefault("scope", {})
    area = _estimate_area(scope)
    for row in updated.get("materials") or []:
        include = bool(row.get("include"))
        qty_per_sqft = safe_number(row.get("editable_qty_per_sqft"), 0.0)
        unit_price = safe_number(row.get("current_unit_price"), 0.0)
        quantity = qty_per_sqft * area if include and area else 0.0
        row["calculated_quantity"] = round(quantity, 2)
        row["estimated_cost"] = round(quantity * unit_price, 2)
    for row in updated.get("labor") or []:
        include = bool(row.get("include"))
        hours_per_1000 = safe_number(row.get("editable_hours_per_1000_sqft"), 0.0)
        hours = hours_per_1000 * area / 1000 if include and area else 0.0
        row["calculated_hours"] = round(hours, 2)
        row["estimated_cost"] = round(hours * hourly_rate, 2)
    for row in updated.get("adders") or []:
        row["estimated_cost"] = round(safe_number(row.get("editable_value"), 0.0), 2) if row.get("include") else 0.0
    return updated


def workbench_to_draft_workbook_inputs(workbench: dict[str, Any]) -> dict[str, Any]:
    workbench = recalculate_workbench_tables(workbench)
    scope = workbench.get("scope") or {}
    material_rows = []
    for row in workbench.get("materials") or []:
        if not row.get("include"):
            continue
        material_rows.append(
            {
                "item": row.get("package"),
                "category": row.get("package_key"),
                "quantity": safe_number(row.get("calculated_quantity"), 0.0),
                "unit": row.get("unit"),
                "unit_price": safe_number(row.get("current_unit_price"), 0.0),
                "estimated_cost": safe_number(row.get("estimated_cost"), 0.0),
                "notes": f"Workbench edited value; source={row.get('source')}; evidence_count={row.get('evidence_count')}",
            }
        )
    labor_rows = []
    for row in workbench.get("labor") or []:
        if not row.get("include"):
            continue
        crew_size = max(1, int(safe_number(row.get("crew_size"), 1)))
        hours = safe_number(row.get("calculated_hours"), 0.0)
        labor_rows.append(
            {
                "task": row.get("package_key"),
                "crew_size": crew_size,
                "total_hours": hours,
                "adjusted_days": round(hours / (crew_size * 8), 3) if crew_size else 0,
                "estimated_cost": safe_number(row.get("estimated_cost"), 0.0),
                "notes": f"Workbench edited value; source={row.get('source')}; evidence_count={row.get('evidence_count')}",
            }
        )
    adders = [row for row in workbench.get("adders") or [] if row.get("include")]
    travel_rows = []
    adders_review_rows = []
    for row in adders:
        payload = {
            "item": row.get("adder"),
            "category": row.get("adder_key"),
            "estimated_cost": safe_number(row.get("estimated_cost"), 0.0),
            "notes": row.get("notes"),
        }
        if row.get("adder_key") == "travel":
            travel_rows.append({"travel_vehicle_cost": payload["estimated_cost"], "travel_notes": payload.get("notes")})
        else:
            adders_review_rows.append(payload)
    return {
        "template_type": "roofing",
        "header": {
            "C2_job_name": first_nonblank(scope.get("job_name"), "Estimating Assistant Draft"),
            "C3_job_type": scope.get("project_type"),
            "C4_site_address": scope.get("site_address"),
            "C5_city_state_zip": scope.get("city_state_zip"),
            "C12_estimated_sqft": _estimate_area(scope),
            "gross_area_sqft": safe_number(scope.get("gross_sqft"), 0.0),
            "deduction_area_sqft": safe_number(scope.get("deduction_sqft"), 0.0),
            "net_area_sqft": _estimate_area(scope),
            "dimension_notes": [],
        },
        "material_rows": material_rows,
        "labor_rows": labor_rows,
        "travel_rows": travel_rows,
        "adders_review_rows": adders_review_rows,
    }


def summarize_workbench_totals(workbench: dict[str, Any]) -> dict[str, float]:
    workbench = recalculate_workbench_tables(workbench)
    material_total = sum(safe_number(row.get("estimated_cost"), 0.0) for row in workbench.get("materials") or [] if row.get("include"))
    labor_total = sum(safe_number(row.get("estimated_cost"), 0.0) for row in workbench.get("labor") or [] if row.get("include"))
    adder_total = sum(safe_number(row.get("estimated_cost"), 0.0) for row in workbench.get("adders") or [] if row.get("include"))
    return {
        "material_total": round(material_total, 2),
        "labor_total": round(labor_total, 2),
        "adder_total": round(adder_total, 2),
        "draft_total": round(material_total + labor_total + adder_total, 2),
    }


def build_edit_history_rows(
    original_workbench: dict[str, Any],
    edited_workbench: dict[str, Any],
    *,
    estimator: str = "",
    reason_map: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    reason_map = reason_map or {}
    timestamp = datetime.now(UTC).isoformat()
    estimate_id = first_nonblank(edited_workbench.get("estimate_id"), original_workbench.get("estimate_id"), "")
    rows: list[dict[str, Any]] = []

    def add_row(section: str, field: str, default: Any, final: Any, threshold: float | None = None, *, require_when_changed: bool = False) -> None:
        default_number = optional_number(default)
        final_number = optional_number(final)
        difference = None
        percent_difference = None
        reason_required = False
        if default_number is not None and final_number is not None:
            difference = final_number - default_number
            if require_when_changed and default != final:
                reason_required = True
            if abs(default_number) > 0:
                percent_difference = difference / default_number
                if threshold is not None and abs(percent_difference) > threshold:
                    reason_required = True
        elif default != final:
            difference = str(final)
            reason_required = require_when_changed
        rows.append(
            {
                "estimate_id": estimate_id,
                "timestamp": timestamp,
                "estimator": estimator,
                "section": section,
                "field": field,
                "field_name": field,
                "package_or_labor_task": section.split(".", 1)[1] if "." in section else "",
                "historical_default": default,
                "suggested_value": default,
                "final_value": final,
                "difference": difference,
                "percent_difference": percent_difference,
                "difference_pct": percent_difference,
                "reason_required": reason_required,
                "reason": reason_map.get(f"{section}.{field}", ""),
            }
        )

    for key, default in (original_workbench.get("scope") or {}).items():
        add_row("scope", key, default, (edited_workbench.get("scope") or {}).get(key))
    original_materials = {row.get("package_key"): row for row in original_workbench.get("materials") or []}
    for row in edited_workbench.get("materials") or []:
        package = row.get("package_key")
        original = original_materials.get(package, {})
        add_row(f"materials.{package}", "include", original.get("include"), row.get("include"), require_when_changed=True)
        add_row(f"materials.{package}", "editable_qty_per_sqft", original.get("editable_qty_per_sqft"), row.get("editable_qty_per_sqft"), 0.5)
    original_labor = {row.get("package_key"): row for row in original_workbench.get("labor") or []}
    for row in edited_workbench.get("labor") or []:
        package = row.get("package_key")
        original = original_labor.get(package, {})
        add_row(f"labor.{package}", "include", original.get("include"), row.get("include"), require_when_changed=True)
        add_row(f"labor.{package}", "editable_hours_per_1000_sqft", original.get("editable_hours_per_1000_sqft"), row.get("editable_hours_per_1000_sqft"), 0.3)
    original_adders = {row.get("adder_key"): row for row in original_workbench.get("adders") or []}
    for row in edited_workbench.get("adders") or []:
        adder = row.get("adder_key")
        original = original_adders.get(adder, {})
        add_row(f"adders.{adder}", "include", original.get("include"), row.get("include"), require_when_changed=True)
        add_row(f"adders.{adder}", "editable_value", original.get("editable_value"), row.get("editable_value"), 0.5)
    return rows


def append_edit_history(rows: list[dict[str, Any]], output_dir: Path | str = "output/estimator_feedback") -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "estimator_edit_history.csv"
    columns = [
        "estimate_id",
        "timestamp",
        "estimator",
        "section",
        "field",
        "field_name",
        "package_or_labor_task",
        "historical_default",
        "suggested_value",
        "final_value",
        "difference",
        "percent_difference",
        "difference_pct",
        "reason_required",
        "reason",
    ]
    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column) for column in columns})
    return path
