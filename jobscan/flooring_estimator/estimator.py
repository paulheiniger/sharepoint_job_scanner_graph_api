from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import pandas as pd


@dataclass
class FlooringEstimateResult:
    notes: str
    parsed_scope: dict[str, Any]
    workbook_decisions: list[dict[str, Any]]
    review_flags: list[str] = field(default_factory=list)
    missing_info: list[str] = field(default_factory=list)
    confidence: str = "low"
    audit_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def normalize_notes(notes: str | None) -> str:
    return clean_text(notes).lower()


def number_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _frame_from_data(data: Any, attr: str) -> pd.DataFrame:
    if data is None:
        return pd.DataFrame()
    value = data.get(attr) if isinstance(data, dict) else getattr(data, attr, None)
    return value.copy() if isinstance(value, pd.DataFrame) else pd.DataFrame()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _confidence_rank(value: Any) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(_text(value).lower(), 0)


def _flooring_relationship_rows(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    rows = frame.copy()
    for column in ("division", "template_type", "project_type", "package", "package_a", "package_b"):
        if column not in rows.columns:
            rows[column] = ""
    text = (
        rows["division"].fillna("").astype(str)
        + " "
        + rows["template_type"].fillna("").astype(str)
        + " "
        + rows["project_type"].fillna("").astype(str)
        + " "
        + rows["package"].fillna("").astype(str)
        + " "
        + rows["package_a"].fillna("").astype(str)
        + " "
        + rows["package_b"].fillna("").astype(str)
    ).str.lower()
    return rows[text.str.contains("floor|polyaspartic|polyspartic|epoxy flooring|floor system", regex=True, na=False)].copy()


def _relationship_sort_key(row: pd.Series, scope: dict[str, Any]) -> tuple[int, int, int, float]:
    project_type = _text(row.get("project_type")).lower()
    substrate = _text(row.get("substrate")).lower()
    scope_substrate = _text(scope.get("substrate")).lower()
    project_score = 2 if project_type == "floor system" else 1 if "floor" in project_type or "epoxy" in project_type else 0
    substrate_score = 1 if scope_substrate and scope_substrate != "unknown" and scope_substrate in substrate else 0
    confidence_score = _confidence_rank(row.get("confidence"))
    job_count = number_or_none(row.get("job_count")) or number_or_none(row.get("evidence_count")) or 0
    return (project_score, substrate_score, confidence_score, job_count)


def _select_relationship_row(frame: pd.DataFrame, packages: list[str], scope: dict[str, Any]) -> dict[str, Any] | None:
    if frame.empty or "package" not in frame.columns:
        return None
    rows = _flooring_relationship_rows(frame)
    if rows.empty:
        return None
    rows = rows[rows["package"].fillna("").astype(str).isin(packages)].copy()
    if rows.empty:
        return None
    rows["_sort_key"] = rows.apply(lambda row: _relationship_sort_key(row, scope), axis=1)
    rows = rows.sort_values("_sort_key", ascending=False)
    return rows.iloc[0].drop(labels=["_sort_key"], errors="ignore").to_dict()


def _historical_summary(row: dict[str, Any] | None, fallback_note: str = "") -> str:
    if not row:
        return fallback_note
    package = _text(row.get("package"))
    project_type = _text(row.get("project_type")) or "flooring"
    job_count = int(number_or_none(row.get("job_count")) or number_or_none(row.get("evidence_count")) or 0)
    confidence = _text(row.get("confidence")) or "review"
    parts = [f"Historical {package} default from {project_type} ({job_count} job{'s' if job_count != 1 else ''}, {confidence} confidence)."]
    qty = number_or_none(row.get("median_qty_per_sqft"))
    cost = number_or_none(row.get("median_cost_per_sqft"))
    hours = number_or_none(row.get("median_hours_per_1000_sqft"))
    if qty is not None:
        parts.append(f"median_qty_per_sqft={qty:g}.")
    if cost is not None:
        parts.append(f"median_cost_per_sqft={cost:g}.")
    if hours is not None:
        parts.append(f"median_hours_per_1000_sqft={hours:g}.")
    return " ".join(parts)


def _material_defaults(
    material_rows: pd.DataFrame,
    scope: dict[str, Any],
    *,
    exact_packages: list[str],
    generic_row: dict[str, Any] | None,
    default_gal_per_100: float,
    default_unit_price: float,
    generic_share: float = 1.0,
) -> dict[str, Any]:
    exact = _select_relationship_row(material_rows, exact_packages, scope)
    source = exact
    gal_per_100 = default_gal_per_100
    unit_price = default_unit_price
    source_kind = "template_default"
    if exact:
        qty_per_sqft = number_or_none(exact.get("median_qty_per_sqft"))
        cost_per_sqft = number_or_none(exact.get("median_cost_per_sqft"))
        if qty_per_sqft and qty_per_sqft > 0:
            gal_per_100 = qty_per_sqft * 100
        if cost_per_sqft and qty_per_sqft and qty_per_sqft > 0:
            unit_price = cost_per_sqft / qty_per_sqft
        source_kind = "historical_exact_package"
    elif generic_row:
        qty_per_sqft = number_or_none(generic_row.get("median_qty_per_sqft"))
        cost_per_sqft = number_or_none(generic_row.get("median_cost_per_sqft"))
        if qty_per_sqft and qty_per_sqft > 0:
            gal_per_100 = qty_per_sqft * generic_share * 100
        if cost_per_sqft and qty_per_sqft and qty_per_sqft > 0:
            unit_price = cost_per_sqft / qty_per_sqft
        source = generic_row
        source_kind = "historical_flooring_coating_fallback"
    return {
        "gal_per_100_sqft": round(gal_per_100, 4),
        "unit_price": round(unit_price, 2),
        "source": source_kind,
        "relationship_row": source,
        "historical_evidence_summary": _historical_summary(source, "Template default; no flooring relationship row found."),
    }


def _labor_defaults(
    labor_rows: pd.DataFrame,
    scope: dict[str, Any],
    *,
    packages: list[str],
    default_days: float,
    default_crew_size: int,
) -> dict[str, Any]:
    area = number_or_none(scope.get("area_sqft")) or 0
    row = _select_relationship_row(labor_rows, packages, scope)
    days = default_days
    crew_size = default_crew_size
    total_hours = None
    source = "template_default"
    if row:
        crew_size = int(round(number_or_none(row.get("median_crew_size")) or crew_size))
        total_hours = number_or_none(row.get("median_total_hours"))
        hours_per_sqft = number_or_none(row.get("median_hours_per_sqft"))
        if hours_per_sqft and area > 0:
            total_hours = hours_per_sqft * area
        if total_hours and crew_size > 0:
            days = max(0.25, total_hours / (crew_size * 8))
        else:
            days = number_or_none(row.get("median_days")) or days
        source = "historical_labor_rate"
    return {
        "days": round(days, 2),
        "crew_size": max(1, crew_size),
        "total_hours": round(total_hours, 2) if total_hours else None,
        "source": source,
        "relationship_row": row,
        "historical_evidence_summary": _historical_summary(row, "Template default; no flooring labor relationship row found."),
    }


def parse_flooring_area(text: str) -> float | None:
    area_match = re.search(
        r"\b(?P<value>\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:sq\.?\s*ft|sqft|sf|square feet)\b",
        text,
    )
    if area_match:
        return float(area_match.group("value").replace(",", ""))
    dim_match = re.search(
        r"\b(?P<length>\d+(?:\.\d+)?)\s*(?:x|by)\s*(?P<width>\d+(?:\.\d+)?)\s*(?:ft|feet|')?\b",
        text,
    )
    if dim_match:
        return float(dim_match.group("length")) * float(dim_match.group("width"))
    return None


def parse_flooring_scope(notes: str | None, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    overrides = overrides or {}
    text = normalize_notes(notes)
    area = number_or_none(overrides.get("area_sqft")) or parse_flooring_area(text)
    system = "epoxy_polyaspartic"
    if "urethane" in text:
        system = "urethane"
    elif "silicone" in text:
        system = "silicone"
    elif "epoxy" in text or "polyaspartic" in text or "flake" in text:
        system = "epoxy_polyaspartic"
    substrate = "concrete" if any(term in text for term in ["concrete", "slab", "floor"]) else "unknown"
    prep_required = any(term in text for term in ["grind", "prep", "shotblast", "shot blast", "patch", "crack", "spall"])
    flake_broadcast = "flake" in text or "flakes" in text
    primer_required = any(term in text for term in ["primer", "prime", "moisture"])
    generator_required = "generator" in text or "grinder" in text or prep_required
    access = "low" if any(term in text for term in ["easy access", "open access", "first floor"]) else "unknown"
    return {
        "division": "Flooring",
        "template_type": "flooring",
        "job_type": "Floor System",
        "area_sqft": area,
        "system": system,
        "substrate": substrate,
        "prep_required": prep_required,
        "flake_broadcast": flake_broadcast,
        "primer_required": primer_required,
        "generator_required": generator_required,
        "access_complexity": access,
    }


def build_flooring_workbook_decisions(scope: dict[str, Any], data: Any = None) -> list[dict[str, Any]]:
    area = number_or_none(scope.get("area_sqft")) or 0
    material_rows = _frame_from_data(data, "relationship_material_qty_ratios")
    labor_rows = _frame_from_data(data, "relationship_labor_rates")
    generic_coating = _select_relationship_row(material_rows, ["coating", "floor_coating"], scope)
    base_defaults = _material_defaults(
        material_rows,
        scope,
        exact_packages=["floor_base_coat"],
        generic_row=generic_coating,
        default_gal_per_100=1.0,
        default_unit_price=45,
        generic_share=1 / 1.6,
    )
    topcoat_defaults = _material_defaults(
        material_rows,
        scope,
        exact_packages=["floor_topcoat"],
        generic_row=generic_coating,
        default_gal_per_100=0.6,
        default_unit_price=77.1,
        generic_share=0.6 / 1.6,
    )
    prep_labor = _labor_defaults(
        labor_rows,
        scope,
        packages=["labor_floor_grind_patch", "labor_floor_patch_grind", "labor_prep"],
        default_days=1.0 if scope.get("prep_required") else 0.5,
        default_crew_size=3 if area >= 1500 else 2,
    )
    base_labor = _labor_defaults(
        labor_rows,
        scope,
        packages=["labor_floor_prep_base", "labor_floor_base_coat", "labor_base"],
        default_days=max(0.5, min(3.0, area / 4500)) if area else 0.5,
        default_crew_size=3 if area >= 1500 else 2,
    )
    topcoat_labor = _labor_defaults(
        labor_rows,
        scope,
        packages=["labor_floor_topcoat", "labor_top_coat"],
        default_days=max(0.5, min(2.0, area / 6000)) if area else 0.5,
        default_crew_size=3 if area >= 1500 else 2,
    )
    decisions: list[dict[str, Any]] = []
    if area > 0:
        decisions.extend(
            [
                {
                    "decision_id": "flooring_base_707",
                    "row_type": "material",
                    "template_bucket": "floor_base_coat",
                    "workbook_row": 26,
                    "item": "NPI Epoxy 707 base coat",
                    "area_sqft": area,
                    "gal_per_100_sqft": base_defaults["gal_per_100_sqft"],
                    "unit_price": base_defaults["unit_price"],
                    "selector_code": 11,
                    "include_source": base_defaults["source"],
                    "historical_evidence_summary": base_defaults["historical_evidence_summary"],
                },
                {
                    "decision_id": "flooring_polyaspartic_topcoat",
                    "row_type": "material",
                    "template_bucket": "floor_topcoat",
                    "workbook_row": 27,
                    "item": "Polyaspartic top coat",
                    "area_sqft": area,
                    "gal_per_100_sqft": topcoat_defaults["gal_per_100_sqft"],
                    "unit_price": topcoat_defaults["unit_price"],
                    "selector_code": 11,
                    "include_source": topcoat_defaults["source"],
                    "historical_evidence_summary": topcoat_defaults["historical_evidence_summary"],
                },
            ]
        )
        if scope.get("primer_required"):
            primer_defaults = _material_defaults(
                material_rows,
                scope,
                exact_packages=["floor_primer", "primer"],
                generic_row=None,
                default_gal_per_100=0.4,
                default_unit_price=40,
            )
            decisions.append(
                {
                    "decision_id": "flooring_primer",
                    "row_type": "material",
                    "template_bucket": "floor_primer",
                    "workbook_row": 39,
                    "item": "Floor primer",
                    "area_sqft": area,
                    "unit_price": primer_defaults["unit_price"],
                    "selector_code": 1,
                    "include_source": primer_defaults["source"],
                    "historical_evidence_summary": primer_defaults["historical_evidence_summary"],
                }
            )
        if scope.get("flake_broadcast"):
            flake_row = _select_relationship_row(material_rows, ["floor_flake"], scope)
            flake_cost_per_sqft = number_or_none((flake_row or {}).get("median_cost_per_sqft"))
            flake_cost = round(area * flake_cost_per_sqft, 2) if flake_cost_per_sqft else round(area * 0.55, 2)
            decisions.append(
                {
                    "decision_id": "flooring_flake_adder",
                    "row_type": "adder",
                    "template_bucket": "floor_flake",
                    "workbook_row": 177,
                    "item": "Flake broadcast allowance",
                    "estimated_cost": flake_cost,
                    "include_source": "historical_material_ratio" if flake_cost_per_sqft else "template_default",
                    "historical_evidence_summary": _historical_summary(flake_row, "Template default; no flooring flake relationship cost found."),
                }
            )
    decisions.extend(
        [
            {
                "decision_id": "flooring_labor_grind_patch",
                "row_type": "labor",
                "template_bucket": "labor_floor_grind_patch",
                "workbook_row": 116,
                "days": prep_labor["days"],
                "crew_size": prep_labor["crew_size"],
                "total_hours": prep_labor["total_hours"],
                "include_source": prep_labor["source"],
                "historical_evidence_summary": prep_labor["historical_evidence_summary"],
            },
            {
                "decision_id": "flooring_labor_base",
                "row_type": "labor",
                "template_bucket": "labor_floor_prep_base",
                "workbook_row": 120,
                "days": base_labor["days"],
                "crew_size": base_labor["crew_size"],
                "total_hours": base_labor["total_hours"],
                "include_source": base_labor["source"],
                "historical_evidence_summary": base_labor["historical_evidence_summary"],
            },
            {
                "decision_id": "flooring_labor_topcoat",
                "row_type": "labor",
                "template_bucket": "labor_floor_topcoat",
                "workbook_row": 130,
                "days": topcoat_labor["days"],
                "crew_size": topcoat_labor["crew_size"],
                "total_hours": topcoat_labor["total_hours"],
                "include_source": topcoat_labor["source"],
                "historical_evidence_summary": topcoat_labor["historical_evidence_summary"],
            },
            {
                "decision_id": "flooring_loading",
                "row_type": "labor",
                "template_bucket": "labor_loading",
                "workbook_row": 137,
                "hours_per_trip": 0.5,
                "crew_size": 1,
                "hourly_rate": 33,
                "include_source": "template_formula_default",
            },
            {
                "decision_id": "flooring_travel",
                "row_type": "labor",
                "template_bucket": "labor_traveling",
                "workbook_row": 139,
                "hours_per_trip": 1.0,
                "crew_size": int(
                    max(
                        number_or_none(prep_labor.get("crew_size")) or 0,
                        number_or_none(base_labor.get("crew_size")) or 0,
                        number_or_none(topcoat_labor.get("crew_size")) or 0,
                        1,
                    )
                ),
                "hourly_rate": 13,
                "include_source": "template_formula_default",
            },
        ]
    )
    if scope.get("generator_required"):
        decisions.append(
            {
                "decision_id": "flooring_generator",
                "row_type": "equipment",
                "template_bucket": "generator",
                "workbook_row": 99,
                "days": max(1, math.ceil(number_or_none(prep_labor.get("days")) or 1)),
                "unit_price": 40,
                "include_source": "scope_trigger",
            }
        )
    return decisions


def estimate_flooring_from_notes(notes: str, overrides: dict[str, Any] | None = None, data: Any = None) -> FlooringEstimateResult:
    scope = parse_flooring_scope(notes, overrides)
    missing_info: list[str] = []
    review_flags: list[str] = []
    if not scope.get("area_sqft"):
        missing_info.append("area_sqft")
        review_flags.append("Flooring area is missing; fill square footage before quoting.")
    if scope.get("substrate") == "unknown":
        missing_info.append("substrate")
        review_flags.append("Substrate is not explicit; verify concrete/slab condition and moisture requirements.")
    if scope.get("system") == "epoxy_polyaspartic":
        review_flags.append("Assumed epoxy/polyaspartic floor system from flooring template; estimator must verify product/system.")
    confidence = "medium" if scope.get("area_sqft") and len(missing_info) <= 1 else "low"
    return FlooringEstimateResult(
        notes=notes,
        parsed_scope=scope,
        workbook_decisions=build_flooring_workbook_decisions(scope, data=data),
        review_flags=review_flags,
        missing_info=missing_info,
        confidence=confidence,
        audit_metadata={
            "generated_at": datetime.now(UTC).isoformat(),
            "estimator_version": "flooring-estimator-v1",
        },
    )
