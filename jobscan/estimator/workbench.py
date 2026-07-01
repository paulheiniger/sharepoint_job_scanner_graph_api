from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .materials import find_current_price
from .rules import first_nonblank, to_float

DEFAULT_HOURLY_RATE = 72.0
DEFAULT_MIN_EVIDENCE_COUNT = 3
HIGH_VARIABILITY_THRESHOLD = 1.0

FILTER_RELAXATION_ORDER = [
    "penetrations_complexity",
    "access_complexity",
    "roof_condition",
    "source_year",
    "warranty_years",
    "coating_type",
    "area_bucket",
    "substrate",
    "project_type",
    "pipeline_status",
]

PROTECTED_FILTER_FIELDS = ["division", "template_type"]

MATERIAL_PACKAGES: list[dict[str, Any]] = [
    {"package": "coating", "label": "Silicone", "keywords": ["silicone", "coating"], "default_unit": "gal", "workbook_row": "26-28"},
    {"package": "primer", "label": "Primer", "keywords": ["primer"], "default_unit": "unit", "workbook_row": "39"},
    {"package": "seam_treatment", "label": "Seam Treatment", "keywords": ["seam", "sealant", "fabric"], "default_unit": "lf", "workbook_row": "47"},
    {"package": "fastener_treatment", "label": "Fastener Treatment", "keywords": ["fastener", "screw"], "default_unit": "ea", "workbook_row": "63"},
    {"package": "caulk_detail", "label": "Caulk / Detail", "keywords": ["caulk", "sealant", "detail"], "default_unit": "unit", "workbook_row": "43/45"},
    {"package": "fabric", "label": "Fabric", "keywords": ["fabric"], "default_unit": "roll", "workbook_row": "79"},
    {"package": "board_stock", "label": "Board Stock", "keywords": ["board", "cover board", "iso"], "default_unit": "board", "workbook_row": "58-60"},
    {"package": "plates", "label": "Plates", "keywords": ["plate", "plates"], "default_unit": "ea", "workbook_row": "65"},
    {"package": "edge_metal", "label": "Edge Metal", "keywords": ["edge metal", "coping", "metal"], "default_unit": "lf", "workbook_row": "82"},
    {"package": "gutter_downspouts", "label": "Gutter / Downspouts", "keywords": ["gutter", "downspout"], "default_unit": "lf", "workbook_row": "84/86"},
    {"package": "granules", "label": "Granules", "keywords": ["granules", "broadcast"], "default_unit": "bag", "workbook_row": "36"},
]

LABOR_PACKAGES: list[dict[str, Any]] = [
    {"package": "labor_prep", "label": "Prep", "workbook_row": "116"},
    {"package": "labor_prime", "label": "Prime", "workbook_row": "118"},
    {"package": "labor_base", "label": "Base Coat", "workbook_row": "122"},
    {"package": "labor_top_coat", "label": "Top Coat", "workbook_row": "124"},
    {"package": "labor_seam_sealer", "label": "Seam Treatment", "workbook_row": "120"},
    {"package": "labor_details", "label": "Details", "workbook_row": "128"},
    {"package": "labor_caulk", "label": "Caulk", "workbook_row": "126"},
    {"package": "labor_cleanup", "label": "Cleanup", "workbook_row": "132"},
    {"package": "labor_loading", "label": "Loading", "workbook_row": "136"},
    {"package": "labor_traveling", "label": "Travel", "workbook_row": "138"},
    {"package": "labor_meals_lodging", "label": "Meals / Hotel", "workbook_row": "144"},
    {"package": "labor_infrared_scan", "label": "Infrared", "workbook_row": "141"},
]

ADDER_ROWS: list[dict[str, Any]] = [
    {"adder": "travel", "label": "Travel", "workbook_row": "106/108"},
    {"adder": "lift", "label": "Lift", "workbook_row": "73/74"},
    {"adder": "generator", "label": "Generator", "workbook_row": "99"},
    {"adder": "dumpster", "label": "Dumpster", "workbook_row": "69"},
    {"adder": "hotel", "label": "Hotel", "workbook_row": "144"},
    {"adder": "inspection", "label": "Inspection", "workbook_row": "106"},
    {"adder": "infrared", "label": "Infrared", "workbook_row": "141"},
    {"adder": "mobilization", "label": "Mobilization", "workbook_row": "136/138"},
    {"adder": "freight", "label": "Freight", "workbook_row": "103"},
    {"adder": "truck_expense", "label": "Truck Expense", "workbook_row": "108"},
    {"adder": "sales_trips", "label": "Sales Trips", "workbook_row": "106"},
    {"adder": "misc", "label": "Misc.", "workbook_row": "101"},
]

PACKAGE_ALIASES: dict[str, set[str]] = {
    "coating": {"coating", "silicone", "roof coating", "acrylic coating"},
    "primer": {"primer", "prime"},
    "seam_treatment": {"seam_treatment", "seam treatment", "labor_seam_sealer", "seam sealer", "seams_misc", "misc_seams", "fabric"},
    "fastener_treatment": {"fastener_treatment", "fastener treatment", "fasteners", "screws", "plates"},
    "caulk_detail": {"caulk_detail", "caulk detail", "caulk_sealant", "caulk", "sealant", "details", "penetrations"},
    "fabric": {"fabric", "scrim"},
    "board_stock": {"board_stock", "board stock", "cover board", "iso", "insulation board", "board"},
    "plates": {"plates", "plate"},
    "edge_metal": {"edge_metal", "edge metal", "coping", "flashing"},
    "gutter_downspouts": {"gutter_downspouts", "gutter", "gutters", "downspout", "downspouts"},
    "granules": {"granules", "broadcast"},
    "labor_prep": {"labor_prep", "prep", "powerwash", "power wash", "set_up"},
    "labor_prime": {"labor_prime", "prime", "labor_prime"},
    "labor_base": {"labor_base", "base coat", "base"},
    "labor_top_coat": {"labor_top_coat", "top coat", "finish coat"},
    "labor_seam_sealer": {"labor_seam_sealer", "seam sealer", "seam treatment", "labor_seam"},
    "labor_details": {"labor_details", "details"},
    "labor_caulk": {"labor_caulk", "caulk", "caulk_sealant"},
    "labor_cleanup": {"labor_cleanup", "clean_up", "cleanup", "touch_cleanup", "touch up"},
    "labor_loading": {"labor_loading", "loading"},
    "labor_traveling": {"labor_traveling", "traveling", "travel labor"},
    "labor_meals_lodging": {"labor_meals_lodging", "meals_lodging", "meals lodging", "hotel", "lodging"},
    "labor_infrared_scan": {"labor_infrared_scan", "infrared_scan", "infrared", "ir scan", "thermal scan"},
    "travel": {"travel", "sales_inspection_trips", "sales inspection travel", "truck_expense", "truck expense", "labor_traveling", "traveling"},
    "lift": {"lift", "lifts", "rental"},
    "generator": {"generator"},
    "dumpster": {"dumpster", "dumpsters", "disposal", "drum_disposal"},
    "hotel": {"hotel", "lodging", "meals_lodging", "meals lodging"},
    "inspection": {"inspection", "sales_inspection_trips", "sales inspection travel"},
    "infrared": {"infrared", "infrared_scan", "ir scan", "thermal scan"},
    "mobilization": {"mobilization", "loading", "labor_loading"},
    "freight": {"freight"},
    "truck_expense": {"truck_expense", "truck expense"},
    "sales_trips": {"sales_trips", "sales trips", "sales_inspection_trips", "sales inspection travel"},
    "misc": {"misc", "miscellaneous", "estimate_adder", "estimate_adder_no_markup", "misc_materials", "misc_equipment", "misc_insurance"},
}

NUMBER_WORDS: dict[str, float] = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "hundred": 100,
}

BASELINE_COATING_LABOR = {"labor_prep", "labor_base", "labor_top_coat", "labor_cleanup", "labor_loading"}

COATING_REQUIRED_POSITIVE_SIGNALS = [
    "roof coating",
    "coating",
    "high solids",
    "gaf high solids",
    "gaco",
    "ge enduris",
    "enduris",
    "unisil",
]

COATING_UNIT_SIGNALS = ["55 gal", "5 gal", "gallon", "gal", "pail", "bucket", "drum"]

COATING_FORBIDDEN_SIGNALS = [
    "sealant",
    "caulk",
    "flashing grade",
    "sausage",
    "tube",
    "cartridge",
    " oz",
    "oz ",
    "fabric",
    "fastener",
    "screw",
    "washer",
    "plate",
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


def _parsed_fields(recommendation: Any) -> dict[str, Any]:
    value = _rec_value(recommendation, "parsed_fields", {}) or {}
    return value if isinstance(value, dict) else {}


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


def _package_aliases(package: str) -> set[str]:
    aliases = set(PACKAGE_ALIASES.get(package, set()))
    aliases.add(package)
    return {_normalized(alias) for alias in aliases if _normalized(alias)}


def _package_match_series(frame: pd.DataFrame, package: str) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=bool)
    aliases = _package_aliases(package)
    candidates = []
    for column in ("package", "labor_package", "template_bucket", "line_item_kind", "item_name", "selected_item_name", "row_label"):
        if column in frame.columns:
            candidates.append(frame[column].map(_normalized).isin(aliases))
    if not candidates:
        return pd.Series([False] * len(frame), index=frame.index)
    mask = candidates[0]
    for candidate in candidates[1:]:
        mask = mask | candidate
    return mask


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    number = optional_number(value)
    if number is not None:
        return number != 0
    return _normalized(value) in {"true", "yes", "y", "included", "physical quantity"}


def _numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series([math.nan] * len(frame), index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def _text_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series([""] * len(frame), index=frame.index, dtype=object)
    return frame[column].fillna("").astype(str)


def _item_name_from_row(row: pd.Series | dict[str, Any]) -> str:
    return str(
        first_nonblank(
            row.get("item_name") if isinstance(row, dict) else row.get("item_name"),
            row.get("line_item_name") if isinstance(row, dict) else row.get("line_item_name"),
            row.get("selected_item_name") if isinstance(row, dict) else row.get("selected_item_name"),
            row.get("normalized_item_name") if isinstance(row, dict) else row.get("normalized_item_name"),
            row.get("product_name") if isinstance(row, dict) else row.get("product_name"),
            row.get("row_label") if isinstance(row, dict) else row.get("row_label"),
            "",
        )
        or ""
    ).strip()


def _price_value_from_row(row: pd.Series | dict[str, Any], preferred: str = "unit_price") -> float:
    for column in (preferred, "matched_price", "price_per_unit", "unit_price", "price_per_gallon", "price_per_sqft"):
        value = row.get(column) if isinstance(row, dict) else row.get(column)
        number = optional_number(value)
        if number is not None and number > 0:
            return number
    return 0.0


def _unit_from_row(row: pd.Series | dict[str, Any], default_unit: str = "unit") -> str:
    return str(
        first_nonblank(
            row.get("unit") if isinstance(row, dict) else row.get("unit"),
            row.get("unit_of_measure") if isinstance(row, dict) else row.get("unit_of_measure"),
            row.get("price_basis") if isinstance(row, dict) else row.get("price_basis"),
            default_unit,
        )
        or default_unit
    )


def _positive_percentile(values: pd.Series, q: float) -> float:
    numeric = pd.to_numeric(values, errors="coerce")
    numeric = numeric[numeric.notna() & (numeric > 0)]
    if numeric.empty:
        return 0.0
    return float(numeric.quantile(q))


def _job_count(frame: pd.DataFrame) -> int:
    if frame.empty:
        return 0
    if "job_id" in frame.columns:
        return int(frame["job_id"].dropna().astype(str).nunique())
    return int(len(frame))


def _add_reason(reasons: dict[str, int], reason: str, count: int) -> None:
    if count > 0:
        reasons[reason] = reasons.get(reason, 0) + int(count)


def _format_reasons(reasons: dict[str, int]) -> str:
    if not reasons:
        return ""
    return "; ".join(f"{reason}: {count}" for reason, count in sorted(reasons.items()))


def _scope_filter_diagnostics(
    package_rows: pd.DataFrame,
    filters: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, dict[str, int]]:
    rows = package_rows.copy()
    reasons: dict[str, int] = {}
    filters = filters or {}
    division_filter = _normalized(_clean_filter_value(filters.get("division")) or "Roofing")
    template_filter = _normalized(_clean_filter_value(filters.get("template_type")) or "roofing")
    if "division" in rows.columns:
        wrong_division = ~rows["division"].map(_normalized).eq(division_filter)
        _add_reason(reasons, f"division_not_{division_filter or 'selected'}", int(wrong_division.sum()))
        rows = rows[~wrong_division].copy()
    else:
        _add_reason(reasons, "missing_division_column", len(rows))
    if "template_type" in rows.columns:
        wrong_template = ~rows["template_type"].map(_normalized).eq(template_filter)
        _add_reason(reasons, f"template_not_{template_filter or 'selected'}", int(wrong_template.sum()))
        rows = rows[~wrong_template].copy()
    else:
        _add_reason(reasons, "missing_template_type_column", len(rows))
    return rows, reasons


def _filter_field_mask(rows: pd.DataFrame, field: str, value: Any) -> pd.Series:
    cleaned = _clean_filter_value(value)
    if cleaned is None:
        return pd.Series([True] * len(rows), index=rows.index)
    if field == "area_bucket":
        expected_text = _normalized(cleaned)
        direct = rows["area_bucket"].map(_normalized).eq(expected_text) if "area_bucket" in rows.columns else pd.Series([False] * len(rows), index=rows.index)
        if "area_sqft" in rows.columns:
            by_area = _numeric_series(rows, "area_sqft").map(_area_bucket_for_sqft).map(_normalized).eq(expected_text)
            return direct | by_area
        return direct
    if field not in rows.columns:
        return pd.Series([True] * len(rows), index=rows.index)
    if field in {"warranty_years", "source_year"}:
        expected = optional_number(cleaned)
        if expected is None:
            return pd.Series([True] * len(rows), index=rows.index)
        actual = _numeric_series(rows, field)
        return actual.notna() & (actual.astype(float).round(4) == float(expected))
    expected_text = _normalized(cleaned)
    actual = rows[field].map(_normalized)
    return actual.eq(expected_text) | actual.str.contains(re.escape(expected_text), na=False) | actual.map(lambda item: item in expected_text if item else False)


def _contains_filter_mask(rows: pd.DataFrame, field: str, value: Any) -> pd.Series:
    if field not in rows.columns:
        return pd.Series([True] * len(rows), index=rows.index)
    cleaned = _clean_filter_value(value)
    if cleaned is None:
        return pd.Series([True] * len(rows), index=rows.index)
    expected_text = _normalized(cleaned)
    if not expected_text:
        return pd.Series([True] * len(rows), index=rows.index)
    actual = rows[field].map(_normalized)
    return actual.eq(expected_text) | actual.str.contains(re.escape(expected_text), na=False) | actual.map(lambda item: item in expected_text if item else False)


def _apply_one_filter(rows: pd.DataFrame, field: str, value: Any) -> pd.DataFrame:
    if rows.empty or _clean_filter_value(value) is None:
        return rows
    if field in {"project_type", "substrate", "coating_type", "roof_condition", "access_complexity", "penetrations_complexity", "pipeline_status"}:
        mask = _contains_filter_mask(rows, field, value)
    else:
        mask = _filter_field_mask(rows, field, value)
    return rows[mask].copy()


def _apply_non_relaxed_filters(rows: pd.DataFrame, filters: dict[str, Any] | None) -> tuple[pd.DataFrame, dict[str, int]]:
    filters = filters or {}
    filtered = rows.copy()
    reasons: dict[str, int] = {}
    if not bool(filters.get("include_repairs", True)):
        text_columns = [column for column in ("project_type", "package", "job_name", "scope_of_work") if column in filtered.columns]
        if text_columns:
            combined = pd.Series([""] * len(filtered), index=filtered.index)
            for column in text_columns:
                combined = combined + " " + filtered[column].fillna("").astype(str)
            repair_mask = combined.map(_normalized).str.contains("repair", na=False)
            _add_reason(reasons, "repairs_excluded_by_filter", int(repair_mask.sum()))
            filtered = filtered[~repair_mask].copy()
    if bool(filters.get("completed_only")):
        status_columns = [column for column in ("pipeline_status", "status") if column in filtered.columns]
        if status_columns:
            completed = pd.Series([False] * len(filtered), index=filtered.index)
            for column in status_columns:
                completed = completed | filtered[column].map(_normalized).str.contains("completed", na=False)
            _add_reason(reasons, "not_completed", int((~completed).sum()))
            filtered = filtered[completed].copy()
    return filtered, reasons


def _active_context_filters(filters: dict[str, Any] | None) -> dict[str, Any]:
    filters = filters or {}
    active: dict[str, Any] = {}
    for field in [*PROTECTED_FILTER_FIELDS, *FILTER_RELAXATION_ORDER]:
        value = _clean_filter_value(filters.get(field))
        if value is not None:
            active[field] = value
    return active


def _filter_rows_with_relaxation(
    rows: pd.DataFrame,
    filters: dict[str, Any] | None,
    accepted_count_fn,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    filters = filters or {}
    min_count = max(0, int(safe_number(filters.get("min_evidence_count"), DEFAULT_MIN_EVIDENCE_COUNT)))
    active = _active_context_filters(filters)
    filtered, fixed_reasons = _apply_non_relaxed_filters(rows, filters)
    relaxed: list[str] = []

    def apply_active(active_filters: dict[str, Any]) -> pd.DataFrame:
        result = filtered.copy()
        for field, value in active_filters.items():
            result = _apply_one_filter(result, field, value)
        return result

    current = apply_active(active)
    for field in FILTER_RELAXATION_ORDER:
        if accepted_count_fn(current) >= min_count:
            break
        if field not in active:
            continue
        relaxed.append(field)
        active.pop(field, None)
        current = apply_active(active)

    summary = {
        "filters_applied": {key: value for key, value in active.items()},
        "filters_requested": _active_context_filters(filters),
        "filters_relaxed": relaxed,
        "minimum_evidence_count": min_count,
        "fixed_filter_rejections": fixed_reasons,
        "filter_hash": historical_filter_hash(filters),
    }
    return current, summary


def _range_stats(p25: Any, median: Any, p75: Any) -> dict[str, Any]:
    p25_num = safe_number(p25, 0.0)
    median_num = safe_number(median, 0.0)
    p75_num = safe_number(p75, 0.0)
    width = max(p75_num - p25_num, 0.0)
    relative = width / median_num if median_num > 0 else 0.0
    return {
        "range_width": width,
        "relative_range_width": relative,
        "variability_warning": "Wide historical range. Consider tightening filters or estimator review."
        if relative >= HIGH_VARIABILITY_THRESHOLD
        else "",
    }


def _with_distribution_metadata(distribution: dict[str, Any], filter_summary: dict[str, Any] | None = None) -> dict[str, Any]:
    enriched = dict(distribution)
    enriched.update(_range_stats(enriched.get("p25"), enriched.get("median"), enriched.get("p75")))
    if filter_summary:
        enriched["filters_applied"] = ", ".join(f"{key}={value}" for key, value in filter_summary.get("filters_applied", {}).items())
        enriched["filters_relaxed"] = ", ".join(filter_summary.get("filters_relaxed", []))
        enriched["minimum_evidence_count"] = filter_summary.get("minimum_evidence_count", DEFAULT_MIN_EVIDENCE_COUNT)
        enriched["filter_hash"] = filter_summary.get("filter_hash", "")
        fixed = filter_summary.get("fixed_filter_rejections") or {}
        if fixed:
            existing = str(enriched.get("rejection_reasons") or "")
            fixed_text = _format_reasons(fixed)
            enriched["rejection_reasons"] = "; ".join(part for part in [existing, fixed_text] if part)
    return enriched


def _evidence_count_from_rows(rows: pd.DataFrame) -> int:
    if rows.empty:
        return 0
    for column in ("evidence_count", "job_count", "supporting_job_count", "n_jobs", "count"):
        if column in rows.columns:
            total = pd.to_numeric(rows[column], errors="coerce").fillna(0).sum()
            if total > 0:
                return int(total)
    return _job_count(rows)


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


def _area_bucket_for_sqft(area: Any) -> str:
    sqft = safe_number(area, 0.0)
    if sqft <= 0:
        return ""
    if sqft < 5_000:
        return "under_5k"
    if sqft < 15_000:
        return "5k_15k"
    if sqft < 50_000:
        return "15k_50k"
    return "50k_plus"


def _clean_filter_value(value: Any) -> Any:
    number = optional_number(value)
    if number is not None and number == 0:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def historical_filter_hash(filters: dict[str, Any] | None) -> str:
    payload = {key: _clean_filter_value(value) for key, value in (filters or {}).items()}
    payload = {key: value for key, value in payload.items() if value is not None}
    return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:10]


def historical_filters_from_scope(scope: dict[str, Any] | None) -> dict[str, Any]:
    scope = scope or {}
    warranty_years = optional_number(first_nonblank(scope.get("warranty_years"), scope.get("warranty_target_years")))
    return {
        "division": "Roofing",
        "template_type": "roofing",
        "project_type": first_nonblank(scope.get("project_type"), "roof coating"),
        "substrate": first_nonblank(scope.get("roof_type_substrate"), scope.get("substrate"), ""),
        "coating_type": first_nonblank(scope.get("coating_type"), ""),
        "warranty_years": warranty_years if warranty_years and warranty_years > 0 else None,
        "roof_condition": first_nonblank(scope.get("roof_condition"), ""),
        "access_complexity": first_nonblank(scope.get("access_complexity"), ""),
        "penetrations_complexity": first_nonblank(scope.get("penetrations_complexity"), scope.get("penetration_complexity"), ""),
        "area_bucket": _area_bucket_for_sqft(_estimate_area(scope)),
        "source_year": None,
        "pipeline_status": "",
        "completed_only": False,
        "include_repairs": True,
        "min_evidence_count": DEFAULT_MIN_EVIDENCE_COUNT,
    }


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
        "penetrations_complexity": first_nonblank(parsed.get("penetrations_complexity"), parsed.get("penetration_complexity"), ""),
        "penetration_count": parsed.get("penetration_count"),
        "notes": first_nonblank(parsed.get("notes"), parsed.get("raw_notes"), parsed.get("field_notes"), parsed.get("input_notes"), ""),
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


def _package_suggestion_status(recommendation: Any, package: str, scope: dict[str, Any] | None = None) -> str:
    package_text = _normalized(package)
    for row in _records(_rec_value(recommendation, "material_plan", [])):
        text = _normalized(" ".join(str(row.get(key) or "") for key in ("category", "package", "item", "notes")))
        if package_text in text or (package == "coating" and "coating" in text):
            if row.get("included_in_total") is False or row.get("needs_review") is True or row.get("review_required") is True:
                return "review"
            return "yes"
    notes = _scope_note_text(recommendation, scope)
    note_text = _normalized(notes)
    if package == "coating" and first_nonblank((_rec_value(recommendation, "parsed_fields", {}) or {}).get("coating_type")):
        return "yes"
    if package == "primer" and _has_positive_note_signal(note_text, ["primer", "prime", "priming", "rust", "oxidation", "adhesion"]):
        return "review"
    if package == "seam_treatment" and _has_positive_note_signal(note_text, ["open seam", "open seams", "seam repair", "failed seam", "separate", "separating"]):
        return "review"
    if package == "fastener_treatment" and _has_positive_note_signal(note_text, ["fastener", "fasteners", "screw", "screws", "exposed fastener"]):
        return "review"
    if package == "caulk_detail" and _has_positive_note_signal(note_text, ["curb", "penetration", "pipe boot", "pitch pocket", "detail", "caulk", "sealant"]):
        return "review"
    return "no"


def _plan_included_labor(recommendation: Any, package: str) -> bool:
    for row in _records(_rec_value(recommendation, "labor_plan", [])):
        task = str(row.get("task") or row.get("labor_package") or "")
        if task == package and row.get("included_in_total") is not False:
            return True
    return False


def _labor_suggestion_status(recommendation: Any, package: str, scope: dict[str, Any] | None = None) -> str:
    notes = _scope_note_text(recommendation, scope)
    note_text = _normalized(notes)
    if scope is not None and package in BASELINE_COATING_LABOR and _is_coating_scope(scope, notes):
        return "yes"
    if package == "labor_prime":
        return "review" if _has_positive_note_signal(note_text, ["primer", "prime", "priming", "rust", "oxidation", "adhesion"]) else "no"
    if package == "labor_seam_sealer":
        return "review" if _has_positive_note_signal(note_text, ["open seam", "open seams", "seam repair", "failed seam", "separate", "separating"]) else "no"
    if package == "labor_details":
        return "review" if _has_positive_note_signal(note_text, ["curb", "penetration", "pipe boot", "pitch pocket", "detail"]) else "no"
    if package == "labor_caulk":
        return "review" if _has_positive_note_signal(note_text, ["caulk", "sealant", "detail"]) else "no"
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


def _material_distribution_from_relationships(
    data: Any,
    package: str,
    default_unit: str,
    reasons: dict[str, int],
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ratios = _frame(data, "relationship_material_qty_ratios")
    if ratios.empty:
        return {}
    package_rows = ratios[_package_match_series(ratios, package)].copy()
    if package_rows.empty:
        return {}
    eligible, scoped_reasons = _scope_filter_diagnostics(package_rows, filters)
    for reason, count in scoped_reasons.items():
        _add_reason(reasons, f"relationship_{reason}", count)

    def accepted_count(candidate_rows: pd.DataFrame) -> int:
        values = _numeric_series(candidate_rows, "median_qty_per_sqft")
        accepted_rows = candidate_rows[values.notna() & (values > 0)].copy()
        return _evidence_count_from_rows(accepted_rows)

    eligible, filter_summary = _filter_rows_with_relaxation(eligible, filters, accepted_count)
    median_values = _numeric_series(eligible, "median_qty_per_sqft")
    accepted = eligible[median_values.notna() & (median_values > 0)].copy()
    cost_values = _numeric_series(eligible, "median_cost_per_sqft")
    cost_rows = eligible[cost_values.notna() & (cost_values > 0)].copy()
    _add_reason(reasons, "relationship_missing_qty_per_sqft", len(eligible) - len(accepted))
    if accepted.empty:
        return _with_distribution_metadata(
            {
            "median": 0.0,
            "p25": 0.0,
            "p75": 0.0,
            "median_cost_per_sqft": _positive_percentile(cost_rows.get("median_cost_per_sqft", pd.Series(dtype=float)), 0.5),
            "historical_cost_evidence_count": _evidence_count_from_rows(cost_rows),
            "source": "relationship_material_qty_ratios_full_corpus",
            "historical_jobs_found": _evidence_count_from_rows(package_rows),
            "rows_accepted": 0,
            "rows_rejected": len(package_rows),
            "rejection_reasons": _format_reasons(reasons),
            },
            filter_summary,
        )
    evidence_count = _evidence_count_from_rows(accepted)
    unit = first_nonblank(next((value for value in accepted.get("unit", pd.Series(dtype=object)).dropna().astype(str) if value.strip()), ""), default_unit)
    return _with_distribution_metadata(
        {
            "median": _positive_percentile(accepted["median_qty_per_sqft"], 0.5),
            "p25": _positive_percentile(accepted.get("p25_qty_per_sqft", accepted["median_qty_per_sqft"]), 0.5) or _positive_percentile(accepted["median_qty_per_sqft"], 0.25),
            "p75": _positive_percentile(accepted.get("p75_qty_per_sqft", accepted["median_qty_per_sqft"]), 0.5) or _positive_percentile(accepted["median_qty_per_sqft"], 0.75),
            "median_cost_per_sqft": _positive_percentile(cost_rows.get("median_cost_per_sqft", pd.Series(dtype=float)), 0.5),
            "historical_cost_evidence_count": _evidence_count_from_rows(cost_rows),
            "evidence_count": evidence_count,
            "historical_jobs_found": _evidence_count_from_rows(package_rows),
            "rows_accepted": len(accepted),
            "rows_rejected": len(package_rows) - len(accepted),
            "rejection_reasons": _format_reasons(reasons),
            "unit": unit,
            "confidence": _confidence(evidence_count),
            "source": "relationship_material_qty_ratios_full_corpus",
        },
        filter_summary,
    )


def material_sizing_distribution(
    data: Any,
    package: str,
    default_unit: str,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = _frame(data, "job_package_summary")
    reasons: dict[str, int] = {}
    if summary.empty:
        fallback = _material_distribution_from_relationships(data, package, default_unit, reasons, filters)
        if fallback:
            return fallback
        return _with_distribution_metadata(
            {
            "median": 0.0,
            "p25": 0.0,
            "p75": 0.0,
            "evidence_count": 0,
            "historical_jobs_found": 0,
            "rows_accepted": 0,
            "rows_rejected": 0,
            "rejection_reasons": "job_package_summary_empty",
            "unit": default_unit,
            "confidence": "none",
            "source": "no_sufficient_evidence",
            },
        )
    package_rows = summary[_package_match_series(summary, package)].copy()
    if package_rows.empty:
        fallback = _material_distribution_from_relationships(data, package, default_unit, reasons, filters)
        if fallback:
            fallback.setdefault("median", 0.0)
            fallback.setdefault("p25", 0.0)
            fallback.setdefault("p75", 0.0)
            fallback.setdefault("evidence_count", 0)
            fallback.setdefault("unit", default_unit)
            fallback.setdefault("confidence", _confidence(fallback.get("evidence_count", 0)))
            return fallback
        return {
            "median": 0.0,
            "p25": 0.0,
            "p75": 0.0,
            "evidence_count": 0,
            "historical_jobs_found": 0,
            "rows_accepted": 0,
            "rows_rejected": 0,
            "rejection_reasons": "no_package_rows_found",
            "unit": default_unit,
            "confidence": "none",
            "source": "no_sufficient_evidence",
        }

    eligible, scoped_reasons = _scope_filter_diagnostics(package_rows, filters)
    reasons.update(scoped_reasons)

    def quantity_evidence_count(candidate_rows: pd.DataFrame) -> int:
        area = _numeric_series(candidate_rows, "area_sqft")
        total_quantity = _numeric_series(candidate_rows, "total_quantity")
        qty_per_sqft = _numeric_series(candidate_rows, "qty_per_sqft")
        computed_qty_per_sqft = total_quantity / area
        candidate_qty = qty_per_sqft.where(qty_per_sqft.notna() & (qty_per_sqft > 0), computed_qty_per_sqft)
        if "has_physical_quantity" in candidate_rows.columns:
            physical_mask = candidate_rows["has_physical_quantity"].map(_truthy)
        elif "physical_quantity_valid" in candidate_rows.columns:
            physical_mask = candidate_rows["physical_quantity_valid"].map(_truthy)
        else:
            physical_mask = (candidate_qty > 0) | (total_quantity > 0)
        bad_units = _text_series(candidate_rows, "unit").map(_normalized).isin({"mixed", "allowance", "usd", "$", "dollar", "dollars"})
        accepted_rows = candidate_rows[physical_mask & ~bad_units & candidate_qty.notna() & (candidate_qty > 0)].copy()
        return _job_count(accepted_rows)

    eligible, filter_summary = _filter_rows_with_relaxation(eligible, filters, quantity_evidence_count)
    area = _numeric_series(eligible, "area_sqft")
    total_quantity = _numeric_series(eligible, "total_quantity")
    qty_per_sqft = _numeric_series(eligible, "qty_per_sqft")
    computed_qty_per_sqft = total_quantity / area
    eligible["_workbench_qty_per_sqft"] = qty_per_sqft.where(qty_per_sqft.notna() & (qty_per_sqft > 0), computed_qty_per_sqft)
    cost_per_sqft = _numeric_series(eligible, "cost_per_sqft")
    if "has_physical_quantity" in eligible.columns:
        physical_mask = eligible["has_physical_quantity"].map(_truthy)
    elif "physical_quantity_valid" in eligible.columns:
        physical_mask = eligible["physical_quantity_valid"].map(_truthy)
    else:
        physical_mask = (eligible["_workbench_qty_per_sqft"] > 0) | (total_quantity > 0)
    bad_units = _text_series(eligible, "unit").map(_normalized).isin({"mixed", "allowance", "usd", "$", "dollar", "dollars"})
    missing_sqft = area.isna() | (area <= 0)
    missing_quantity = ~physical_mask | ((total_quantity.isna() | (total_quantity <= 0)) & (eligible["_workbench_qty_per_sqft"].isna() | (eligible["_workbench_qty_per_sqft"] <= 0)))
    missing_qty_per_sqft = eligible["_workbench_qty_per_sqft"].isna() | (eligible["_workbench_qty_per_sqft"] <= 0)
    _add_reason(reasons, "mixed_or_allowance_unit", int(bad_units.sum()))
    _add_reason(reasons, "missing_sqft", int(missing_sqft.sum()))
    _add_reason(reasons, "missing_physical_quantity", int(missing_quantity.sum()))
    _add_reason(reasons, "missing_qty_per_sqft", int(missing_qty_per_sqft.sum()))
    accepted = eligible[physical_mask & ~bad_units & ~missing_qty_per_sqft].copy()
    cost_rows = eligible[cost_per_sqft.notna() & (cost_per_sqft > 0)].copy()
    if accepted.empty:
        fallback = _material_distribution_from_relationships(data, package, default_unit, reasons, filters)
        if fallback and (safe_number(fallback.get("median"), 0) > 0 or safe_number(fallback.get("median_cost_per_sqft"), 0) > 0):
            return fallback
        historical_jobs = _job_count(package_rows)
        return _with_distribution_metadata(
            {
                "median": 0.0,
                "p25": 0.0,
                "p75": 0.0,
                "median_cost_per_sqft": _positive_percentile(cost_rows.get("cost_per_sqft", pd.Series(dtype=float)), 0.5),
                "historical_cost_evidence_count": _job_count(cost_rows),
                "evidence_count": 0,
                "historical_jobs_found": historical_jobs,
                "rows_accepted": 0,
                "rows_rejected": len(package_rows),
                "rejection_reasons": _format_reasons(reasons),
                "unit": default_unit,
                "confidence": "none",
                "source": "no_sufficient_evidence",
            },
            filter_summary,
        )
    evidence_count = _job_count(accepted)
    unit = first_nonblank(next((value for value in accepted.get("unit", pd.Series(dtype=object)).dropna().astype(str) if value.strip() and _normalized(value) != "mixed"), ""), default_unit)
    return _with_distribution_metadata(
        {
            "median": _positive_percentile(accepted["_workbench_qty_per_sqft"], 0.5),
            "p25": _positive_percentile(accepted["_workbench_qty_per_sqft"], 0.25),
            "p75": _positive_percentile(accepted["_workbench_qty_per_sqft"], 0.75),
            "median_cost_per_sqft": _positive_percentile(cost_rows.get("cost_per_sqft", pd.Series(dtype=float)), 0.5),
            "historical_cost_evidence_count": _job_count(cost_rows),
            "evidence_count": evidence_count,
            "historical_jobs_found": _job_count(package_rows),
            "rows_accepted": len(accepted),
            "rows_rejected": len(package_rows) - len(accepted),
            "rejection_reasons": _format_reasons(reasons),
            "unit": unit,
            "confidence": _confidence(evidence_count),
            "source": "job_package_summary_filtered",
        },
        filter_summary,
    )


def _labor_distribution_from_relationships(
    data: Any,
    package: str,
    reasons: dict[str, int],
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rates = _frame(data, "relationship_labor_rates")
    if rates.empty:
        return {}
    package_rows = rates[_package_match_series(rates, package)].copy()
    if package_rows.empty:
        return {}
    eligible, scoped_reasons = _scope_filter_diagnostics(package_rows, filters)
    for reason, count in scoped_reasons.items():
        _add_reason(reasons, f"relationship_{reason}", count)

    def accepted_count(candidate_rows: pd.DataFrame) -> int:
        values = _numeric_series(candidate_rows, "median_hours_per_1000_sqft")
        accepted_rows = candidate_rows[values.notna() & (values > 0)].copy()
        return _evidence_count_from_rows(accepted_rows)

    eligible, filter_summary = _filter_rows_with_relaxation(eligible, filters, accepted_count)
    median_values = _numeric_series(eligible, "median_hours_per_1000_sqft")
    accepted = eligible[median_values.notna() & (median_values > 0)].copy()
    _add_reason(reasons, "relationship_missing_hours_per_1000_sqft", len(eligible) - len(accepted))
    if accepted.empty:
        return _with_distribution_metadata(
            {
            "source": "relationship_labor_rates_full_corpus",
            "historical_jobs_found": _evidence_count_from_rows(package_rows),
            "rows_accepted": 0,
            "rows_rejected": len(package_rows),
            "rejection_reasons": _format_reasons(reasons),
            },
            filter_summary,
        )
    evidence_count = _evidence_count_from_rows(accepted)
    return _with_distribution_metadata(
        {
            "median": _positive_percentile(accepted["median_hours_per_1000_sqft"], 0.5),
            "p25": _positive_percentile(accepted.get("p25_hours_per_1000_sqft", accepted["median_hours_per_1000_sqft"]), 0.5) or _positive_percentile(accepted["median_hours_per_1000_sqft"], 0.25),
            "p75": _positive_percentile(accepted.get("p75_hours_per_1000_sqft", accepted["median_hours_per_1000_sqft"]), 0.5) or _positive_percentile(accepted["median_hours_per_1000_sqft"], 0.75),
            "evidence_count": evidence_count,
            "historical_jobs_found": _evidence_count_from_rows(package_rows),
            "rows_accepted": len(accepted),
            "rows_rejected": len(package_rows) - len(accepted),
            "rejection_reasons": _format_reasons(reasons),
            "median_crew_size": safe_number(accepted.get("median_crew_size", pd.Series([4])).median(), 4),
            "confidence": _confidence(evidence_count),
            "source": "relationship_labor_rates_full_corpus",
        },
        filter_summary,
    )


def labor_sizing_distribution(data: Any, package: str, filters: dict[str, Any] | None = None) -> dict[str, Any]:
    summary = _frame(data, "job_package_summary")
    reasons: dict[str, int] = {}
    if summary.empty:
        fallback = _labor_distribution_from_relationships(data, package, reasons, filters)
        if fallback:
            return fallback
        return {
            "median": 0.0,
            "p25": 0.0,
            "p75": 0.0,
            "evidence_count": 0,
            "historical_jobs_found": 0,
            "rows_accepted": 0,
            "rows_rejected": 0,
            "rejection_reasons": "job_package_summary_empty",
            "median_crew_size": 4,
            "confidence": "none",
            "source": "no_sufficient_evidence",
        }
    package_rows = summary[_package_match_series(summary, package)].copy()
    if package_rows.empty:
        fallback = _labor_distribution_from_relationships(data, package, reasons, filters)
        if fallback:
            fallback.setdefault("median", 0.0)
            fallback.setdefault("p25", 0.0)
            fallback.setdefault("p75", 0.0)
            fallback.setdefault("evidence_count", 0)
            fallback.setdefault("median_crew_size", 4)
            fallback.setdefault("confidence", _confidence(fallback.get("evidence_count", 0)))
            return fallback
        return {
            "median": 0.0,
            "p25": 0.0,
            "p75": 0.0,
            "evidence_count": 0,
            "historical_jobs_found": 0,
            "rows_accepted": 0,
            "rows_rejected": 0,
            "rejection_reasons": "no_package_rows_found",
            "median_crew_size": 4,
            "confidence": "none",
            "source": "no_sufficient_evidence",
        }
    eligible, scoped_reasons = _scope_filter_diagnostics(package_rows, filters)
    reasons.update(scoped_reasons)

    def labor_evidence_count(candidate_rows: pd.DataFrame) -> int:
        area = _numeric_series(candidate_rows, "area_sqft")
        total_hours = _numeric_series(candidate_rows, "total_hours")
        hours_per_sqft = _numeric_series(candidate_rows, "hours_per_sqft")
        computed_hours_per_sqft = total_hours / area
        hours_per_1000 = hours_per_sqft.where(hours_per_sqft.notna() & (hours_per_sqft > 0), computed_hours_per_sqft) * 1000
        accepted_rows = candidate_rows[hours_per_1000.notna() & (hours_per_1000 > 0)].copy()
        return _job_count(accepted_rows)

    eligible, filter_summary = _filter_rows_with_relaxation(eligible, filters, labor_evidence_count)
    area = _numeric_series(eligible, "area_sqft")
    total_hours = _numeric_series(eligible, "total_hours")
    hours_per_sqft = _numeric_series(eligible, "hours_per_sqft")
    computed_hours_per_sqft = total_hours / area
    eligible["_workbench_hours_per_1000"] = hours_per_sqft.where(hours_per_sqft.notna() & (hours_per_sqft > 0), computed_hours_per_sqft) * 1000
    missing_sqft = area.isna() | (area <= 0)
    missing_hours = (total_hours.isna() | (total_hours <= 0)) & (hours_per_sqft.isna() | (hours_per_sqft <= 0))
    missing_hours_rate = eligible["_workbench_hours_per_1000"].isna() | (eligible["_workbench_hours_per_1000"] <= 0)
    _add_reason(reasons, "missing_sqft", int(missing_sqft.sum()))
    _add_reason(reasons, "missing_hours", int(missing_hours.sum()))
    _add_reason(reasons, "missing_hours_per_1000_sqft", int(missing_hours_rate.sum()))
    accepted = eligible[~missing_hours_rate].copy()
    if accepted.empty:
        fallback = _labor_distribution_from_relationships(data, package, reasons, filters)
        if fallback and safe_number(fallback.get("median"), 0) > 0:
            return fallback
        return _with_distribution_metadata(
            {
                "median": 0.0,
                "p25": 0.0,
                "p75": 0.0,
                "evidence_count": 0,
                "historical_jobs_found": _job_count(package_rows),
                "rows_accepted": 0,
                "rows_rejected": len(package_rows),
                "rejection_reasons": _format_reasons(reasons),
                "median_crew_size": 4,
                "confidence": "none",
                "source": "no_sufficient_evidence",
            },
            filter_summary,
        )
    evidence_count = _job_count(accepted)
    crew = _numeric_series(accepted, "crew_size")
    crew = crew[crew.notna() & (crew > 0)]
    return _with_distribution_metadata(
        {
            "median": _positive_percentile(accepted["_workbench_hours_per_1000"], 0.5),
            "p25": _positive_percentile(accepted["_workbench_hours_per_1000"], 0.25),
            "p75": _positive_percentile(accepted["_workbench_hours_per_1000"], 0.75),
            "evidence_count": evidence_count,
            "historical_jobs_found": _job_count(package_rows),
            "rows_accepted": len(accepted),
            "rows_rejected": len(package_rows) - len(accepted),
            "rejection_reasons": _format_reasons(reasons),
            "median_crew_size": float(crew.median()) if not crew.empty else 4,
            "confidence": _confidence(evidence_count),
            "source": "job_package_summary_full_corpus",
        },
        filter_summary,
    )


def adder_sizing_distribution(data: Any, adder: str, area: float = 0.0, filters: dict[str, Any] | None = None) -> dict[str, Any]:
    summary = _frame(data, "job_package_summary")
    if summary.empty:
        return {
            "historical_usage_rate": 0.0,
            "median_cost_when_used": 0.0,
            "median_cost_per_sqft": 0.0,
            "editable_default": 0.0,
            "evidence_count": 0,
            "confidence": "none",
            "source": "no_sufficient_evidence",
            "rejection_reasons": "job_package_summary_empty",
        }
    package_rows = summary[_package_match_series(summary, adder)].copy()
    if package_rows.empty:
        return {
            "historical_usage_rate": 0.0,
            "median_cost_when_used": 0.0,
            "median_cost_per_sqft": 0.0,
            "editable_default": 0.0,
            "evidence_count": 0,
            "confidence": "none",
            "source": "no_sufficient_evidence",
            "rejection_reasons": "no_package_rows_found",
        }
    eligible, reasons = _scope_filter_diagnostics(package_rows, filters)

    def adder_evidence_count(candidate_rows: pd.DataFrame) -> int:
        total_cost = _numeric_series(candidate_rows, "total_cost")
        return _job_count(candidate_rows[total_cost.notna() & (total_cost > 0)])

    eligible, filter_summary = _filter_rows_with_relaxation(eligible, filters, adder_evidence_count)
    total_cost = _numeric_series(eligible, "total_cost")
    cost_per_sqft = _numeric_series(eligible, "cost_per_sqft")
    cost_rows = eligible[total_cost.notna() & (total_cost > 0)].copy()
    psf_rows = eligible[cost_per_sqft.notna() & (cost_per_sqft > 0)].copy()
    _add_reason(reasons, "missing_total_cost", len(eligible) - len(cost_rows))
    median_cost = _positive_percentile(cost_rows.get("total_cost", pd.Series(dtype=float)), 0.5)
    median_psf = _positive_percentile(psf_rows.get("cost_per_sqft", pd.Series(dtype=float)), 0.5)
    editable_default = median_cost if median_cost > 0 else median_psf * area if area and median_psf > 0 else 0.0
    all_roofing = summary.copy()
    if "division" in all_roofing.columns:
        roofing = all_roofing["division"].map(_normalized).eq("roofing")
        if roofing.any():
            all_roofing = all_roofing[roofing].copy()
    denominator = _job_count(all_roofing)
    evidence_count = _job_count(cost_rows)
    usage_rate = round(min(_job_count(eligible) / denominator, 1.0), 4) if denominator else 0.0
    return _with_distribution_metadata(
        {
            "historical_usage_rate": usage_rate,
            "median_cost_when_used": median_cost,
            "median_cost_per_sqft": median_psf,
            "editable_default": editable_default,
            "evidence_count": evidence_count,
            "historical_jobs_found": _job_count(package_rows),
            "rows_accepted": len(cost_rows),
            "rows_rejected": len(package_rows) - len(cost_rows),
            "confidence": _confidence(evidence_count),
            "source": "job_package_summary_full_corpus" if evidence_count else "no_sufficient_evidence",
            "rejection_reasons": _format_reasons(reasons),
            "median": median_cost,
            "p25": _positive_percentile(cost_rows.get("total_cost", pd.Series(dtype=float)), 0.25),
            "p75": _positive_percentile(cost_rows.get("total_cost", pd.Series(dtype=float)), 0.75),
        },
        filter_summary,
    )


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


def _current_pricing_rows(pricing: pd.DataFrame) -> pd.DataFrame:
    if pricing.empty:
        return pricing
    rows = pricing.copy()
    if "is_current" in rows.columns:
        rows = rows[rows["is_current"].map(_truthy)].copy()
    if "status" in rows.columns:
        active = rows["status"].fillna("").astype(str).str.lower().isin({"", "active", "current"})
        rows = rows[active].copy()
    if "needs_review" in rows.columns:
        rows = rows[~rows["needs_review"].map(_truthy)].copy()
    return rows


def _pricing_options_for_package(pricing: pd.DataFrame, package_spec: dict[str, Any], scope: dict[str, Any]) -> list[dict[str, Any]]:
    current = _current_pricing_rows(pricing)
    if current.empty:
        return []
    package = str(package_spec.get("package") or "")
    preferred = "price_per_gallon" if package == "coating" else "unit_price"
    keywords = [str(keyword) for keyword in package_spec.get("keywords") or [] if str(keyword or "").strip()]
    if package == "coating" and scope.get("coating_type"):
        keywords.insert(0, str(scope.get("coating_type")))
    aliases = list(_package_aliases(package))
    haystack = current.apply(
        lambda row: _normalized(" ".join(str(row.get(column) or "") for column in ("product_name", "description", "category", "price_basis", "unit_of_measure"))),
        axis=1,
    )
    search_terms = {_normalized(term) for term in [*keywords, *aliases] if _normalized(term)}
    mask = pd.Series([False] * len(current), index=current.index)
    for term in search_terms:
        mask = mask | haystack.str.contains(re.escape(term), na=False)
    candidates = current[mask].copy()
    if candidates.empty and package == "coating":
        candidates = current[haystack.str.contains("coating|silicone|acrylic", regex=True, na=False)].copy()
    options: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _, row in candidates.iterrows():
        item_name = first_nonblank(row.get("product_name"), row.get("description"), row.get("pricing_item_id"), "")
        if not item_name:
            continue
        key = _normalized(item_name)
        if key in seen:
            continue
        seen.add(key)
        unit_price = _price_value_from_row(row, preferred)
        options.append(
            {
                "item_name": str(item_name),
                "unit": _unit_from_row(row, str(package_spec.get("default_unit") or "unit")),
                "unit_price": unit_price,
                "pricing_item_id": row.get("pricing_item_id"),
                "source": "current_pricing",
            }
        )
    options.sort(key=lambda option: (0 if option.get("unit_price") else 1, safe_number(option.get("unit_price"), 0), option.get("item_name") or ""))
    return options


def _contains_any_text(text: str, terms: list[str]) -> bool:
    normalized = _normalized(text)
    return any(term and term in normalized for term in terms)


def _number_token_value(value: str | None) -> float | None:
    if not value:
        return None
    text = _normalized(value).replace(",", "")
    number = optional_number(text)
    if number is not None:
        return number
    if text in NUMBER_WORDS:
        return NUMBER_WORDS[text]
    parts = [part for part in re.split(r"[\s-]+", text) if part]
    if not parts:
        return None
    total = 0.0
    for part in parts:
        if part not in NUMBER_WORDS:
            return None
        total += NUMBER_WORDS[part]
    return total or None


def _scope_note_text(recommendation: Any | None, scope: dict[str, Any] | None = None) -> str:
    scope = scope or {}
    parsed = _parsed_fields(recommendation) if recommendation is not None else {}
    return str(
        first_nonblank(
            scope.get("notes"),
            scope.get("raw_notes"),
            parsed.get("notes"),
            parsed.get("raw_notes"),
            parsed.get("field_notes"),
            parsed.get("input_notes"),
            "",
        )
        or ""
    )


def _is_coating_scope(scope: dict[str, Any], notes: str = "") -> bool:
    project_type = _normalized(scope.get("project_type"))
    coating_type = _normalized(scope.get("coating_type"))
    text = _normalized(notes)
    return bool(
        coating_type
        or "coating" in project_type
        or "restoration" in project_type
        or "coating" in text
        or "restoration" in text
        or "restore" in text
    )


def _partial_primer_basis_sqft(notes: str, area: float) -> float:
    if not notes or area <= 0:
        return 0.0
    text = _normalized(notes)
    if not re.search(r"\b(primer|prime|priming)\b", text):
        return 0.0

    number_word_pattern = (
        r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|"
        r"fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
        r"eighty|ninety)(?:[-\s](?:one|two|three|four|five|six|seven|eight|nine))?"
    )
    numeric_or_word = rf"(?:\d+(?:\.\d+)?|{number_word_pattern})"
    for match in re.finditer(rf"(?:approximately|about|around|roughly)?\s*(?P<value>{numeric_or_word})\s*(?:%|percent)\b", text):
        window = text[max(0, match.start() - 120) : min(len(text), match.end() + 120)]
        if not re.search(r"\b(primer|prime|priming)\b", window):
            continue
        percent = _number_token_value(match.group("value"))
        if percent is not None and 0 < percent <= 100:
            return round(area * percent / 100, 2)

    percent_patterns = [
        rf"(?:approximately|about|around|roughly)?\s*(?P<value>{numeric_or_word})\s*(?:%|percent)\b.{0,100}\b(?:primer|prime|priming)\b",
        rf"\b(?:primer|prime|priming)\b.{0,100}(?:approximately|about|around|roughly)?\s*(?P<value>{numeric_or_word})\s*(?:%|percent)\b",
    ]
    for pattern in percent_patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        percent = _number_token_value(match.group("value"))
        if percent is not None and 0 < percent <= 100:
            return round(area * percent / 100, 2)

    sqft_patterns = [
        r"\b(?:primer|prime|priming)\b.{0,60}(?P<value>\d[\d,]*(?:\.\d+)?)\s*(?:sq\s*ft|sqft|square feet)\b",
        r"(?P<value>\d[\d,]*(?:\.\d+)?)\s*(?:sq\s*ft|sqft|square feet)\b.{0,60}\b(?:primer|prime|priming)\b",
    ]
    for pattern in sqft_patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        sqft = _number_token_value(match.group("value"))
        if sqft is not None and sqft > 0:
            return round(min(sqft, area), 2)
    return 0.0


def _has_positive_note_signal(notes: str, terms: list[str]) -> bool:
    text = _normalized(notes)
    return any(term in text for term in terms)


def _is_forbidden_coating_option(option: dict[str, Any]) -> bool:
    text = _normalized(" ".join(str(option.get(key) or "") for key in ("item_name", "unit", "price_basis", "category")))
    return _contains_any_text(f" {text} ", COATING_FORBIDDEN_SIGNALS)


def _is_valid_coating_option(option: dict[str, Any]) -> bool:
    text = _normalized(" ".join(str(option.get(key) or "") for key in ("item_name", "unit", "price_basis", "category")))
    if _is_forbidden_coating_option(option):
        return False
    return _contains_any_text(text, COATING_REQUIRED_POSITIVE_SIGNALS) and _contains_any_text(text, COATING_UNIT_SIGNALS)


def _is_selectable_package_item(package: str, option: dict[str, Any], scope: dict[str, Any]) -> bool:
    if package != "coating":
        return True
    return _is_valid_coating_option(option)


def _package_item_fit_details(package: str, option: dict[str, Any], scope: dict[str, Any]) -> tuple[float, list[str]]:
    """Score whether an item belongs in a template bucket.

    This keeps broad keywords like "silicone" from letting flashing-grade sealants win the
    main coating row while still allowing those products on seam/detail buckets.
    """
    name = _normalized(option.get("item_name"))
    unit = _normalized(option.get("unit"))
    combined = f"{name} {unit}"
    score = 0.0
    reasons: list[str] = []
    coating_type = _normalized(scope.get("coating_type"))
    if coating_type and coating_type in combined:
        score += 25
        reasons.append(f"matches coating type {coating_type}")

    if package == "coating":
        if _contains_any_text(combined, COATING_REQUIRED_POSITIVE_SIGNALS):
            score += 160
            reasons.append("roof coating product signal")
        if _contains_any_text(combined, COATING_UNIT_SIGNALS):
            score += 120
            reasons.append("coating unit/package signal")
        if _contains_any_text(combined, COATING_FORBIDDEN_SIGNALS + ["detail", "seam"]):
            score -= 5000
            reasons.append("rejected as coating: sealant/detail/fastener signal")
        if _contains_any_text(combined, ["11 oz", "10 oz", "20 oz", "oz", "tube", "sausage", "cartridge"]):
            score -= 3000
            reasons.append("rejected as coating: small cartridge/tube unit")
    elif package == "primer":
        if _contains_any_text(combined, ["primer", "prime", "rust inhibitive", "epoxy primer", "acrylic primer"]):
            score += 250
            reasons.append("primer product signal")
        if _contains_any_text(combined, ["sealant", "caulk", "tube", "sausage", "fabric", "granule"]):
            score -= 500
            reasons.append("rejected as primer: conflicting sealant/fabric/granule signal")
    elif package == "seam_treatment":
        if _contains_any_text(combined, ["seam", "sealant", "flashing grade", "fabric", "caulk"]):
            score += 180
            reasons.append("seam/detail product signal")
        if _contains_any_text(combined, ["roof coating", "primer", "granule"]):
            score -= 250
            reasons.append("less suitable for seam treatment")
    elif package == "caulk_detail":
        if _contains_any_text(combined, ["caulk", "sealant", "flashing grade", "detail", "tube", "sausage"]):
            score += 220
            reasons.append("caulk/detail product signal")
        if _contains_any_text(combined, ["roof coating", "primer", "granule"]):
            score -= 250
            reasons.append("less suitable for caulk/detail")
    elif package == "fastener_treatment":
        if _contains_any_text(combined, ["fastener", "screw", "washer", "plate"]):
            score += 220
            reasons.append("fastener-specific product signal")
        if _contains_any_text(combined, ["sealant", "caulk"]):
            score -= 50
            reasons.append("sealant fallback only; no fastener-specific signal")
        if _contains_any_text(combined, ["roof coating", "primer", "granule"]):
            score -= 250
            reasons.append("less suitable for fastener treatment")
    elif package == "fabric":
        if _contains_any_text(combined, ["fabric", "roll", "seam fabric"]):
            score += 250
            reasons.append("fabric product signal")
        if _contains_any_text(combined, ["roof coating", "primer", "granule"]):
            score -= 250
            reasons.append("less suitable for fabric")
    elif package == "granules":
        if _contains_any_text(combined, ["granule", "granules", "ceramic granules", "broadcast", "bag"]):
            score += 250
            reasons.append("granules product signal")
        if _contains_any_text(combined, ["roof coating", "primer", "sealant", "caulk"]):
            score -= 250
            reasons.append("less suitable for granules")
    elif package in {"board_stock", "plates", "edge_metal", "gutter_downspouts"}:
        for term in _package_aliases(package):
            if term and term in combined:
                score += 120
                reasons.append(f"matches {package} signal")
        if _contains_any_text(combined, ["roof coating", "primer", "sealant", "caulk", "granule"]):
            score -= 200
            reasons.append(f"less suitable for {package}")
    if not reasons:
        reasons.append("weak item/package match")
    return score, reasons


def _package_item_fit_score(package: str, option: dict[str, Any], scope: dict[str, Any]) -> float:
    return _package_item_fit_details(package, option, scope)[0]


def _historical_item_options(
    data: Any,
    package: str,
    filters: dict[str, Any] | None,
    default_unit: str,
) -> list[dict[str, Any]]:
    summary = _frame(data, "job_package_summary")
    if summary.empty:
        return []
    package_rows = summary[_package_match_series(summary, package)].copy()
    if package_rows.empty:
        return []
    eligible, _ = _scope_filter_diagnostics(package_rows, filters)

    def accepted_count(candidate_rows: pd.DataFrame) -> int:
        named = candidate_rows[candidate_rows.apply(lambda row: bool(_item_name_from_row(row)), axis=1)].copy()
        return _job_count(named)

    eligible, _ = _filter_rows_with_relaxation(eligible, filters, accepted_count)
    if eligible.empty:
        return []
    eligible["_workbench_item_name"] = eligible.apply(_item_name_from_row, axis=1)
    eligible = eligible[eligible["_workbench_item_name"].astype(str).str.strip().ne("")].copy()
    if eligible.empty:
        return []
    area = _numeric_series(eligible, "area_sqft")
    total_quantity = _numeric_series(eligible, "total_quantity")
    qty_per_sqft = _numeric_series(eligible, "qty_per_sqft")
    eligible["_workbench_qty_per_sqft"] = qty_per_sqft.where(qty_per_sqft.notna() & (qty_per_sqft > 0), total_quantity / area)
    cost_per_sqft = _numeric_series(eligible, "cost_per_sqft")
    options: list[dict[str, Any]] = []
    for item_name, group in eligible.groupby("_workbench_item_name", dropna=False):
        quantity_values = _numeric_series(group, "_workbench_qty_per_sqft")
        cost_values = cost_per_sqft.loc[group.index] if not cost_per_sqft.empty else pd.Series(dtype=float)
        unit = first_nonblank(next((value for value in group.get("unit", pd.Series(dtype=object)).dropna().astype(str) if value.strip()), ""), default_unit)
        evidence_count = _job_count(group)
        options.append(
            {
                "item_name": str(item_name),
                "unit": unit,
                "median_qty_per_sqft": _positive_percentile(quantity_values, 0.5),
                "median_cost_per_sqft": _positive_percentile(cost_values, 0.5),
                "evidence_count": evidence_count,
                "source": "historical_most_common_item",
            }
        )
    options.sort(key=lambda option: (safe_number(option.get("evidence_count"), 0), safe_number(option.get("median_qty_per_sqft"), 0)), reverse=True)
    return options


def _select_material_item(
    package: str,
    pricing_options: list[dict[str, Any]],
    historical_options: list[dict[str, Any]],
    scope: dict[str, Any],
    fallback_label: str,
    default_unit: str,
) -> dict[str, Any]:
    historical_by_name = {_normalized(option.get("item_name")): option for option in historical_options}
    note_terms = _normalized(" ".join(str(scope.get(key) or "") for key in ("coating_type", "project_type", "roof_type_substrate")))
    if pricing_options:
        scored = []
        for option in pricing_options:
            name = _normalized(option.get("item_name"))
            score, reasons = _package_item_fit_details(package, option, scope)
            if name in historical_by_name:
                score += 1000 + safe_number(historical_by_name[name].get("evidence_count"), 0)
                reasons.append("used historically for this package")
            if note_terms and any(term and term in name for term in note_terms.split()):
                score += 10
                reasons.append("matches parsed scope wording")
            scored.append((score, option, reasons))
        scored.sort(key=lambda item: (item[0], -safe_number(item[1].get("unit_price"), 0)), reverse=True)
        selectable = [item for item in scored if _is_selectable_package_item(package, item[1], scope)]
        if package == "coating" and not selectable:
            bad_reasons = [
                {
                    "item_name": option.get("item_name"),
                    "score": round(float(score), 2),
                    "reason": "; ".join(reasons),
                }
                for score, option, reasons in scored[:6]
            ]
            if historical_options:
                historical_scored = []
                for option in historical_options:
                    score, reasons = _package_item_fit_details(package, option, scope)
                    historical_scored.append((score, option, reasons))
                historical_scored.sort(key=lambda item: (item[0], safe_number(item[1].get("evidence_count"), 0)), reverse=True)
                historical_selectable = [item for item in historical_scored if _is_selectable_package_item(package, item[1], scope)]
                if historical_selectable:
                    selected = dict(historical_selectable[0][1])
                    selected["unit_price"] = 0.0
                    selected["item_source"] = "historical_most_common_item" if safe_number(selected.get("median_qty_per_sqft"), 0) > 0 else "historical_cost_default"
                    selected["item_median_qty_per_sqft"] = selected.get("median_qty_per_sqft", 0.0)
                    selected["item_median_cost_per_sqft"] = selected.get("median_cost_per_sqft", 0.0)
                    selected["item_evidence_count"] = selected.get("evidence_count", 0)
                    selected["selected_item_reason"] = "Selected from historical roof coating usage because no suitable current pricing item matched."
                    selected["selected_item_score"] = round(float(historical_selectable[0][0]), 2)
                    selected["top_rejected_item_reasons"] = bad_reasons
                    return selected
            return {
                "item_name": "Manual roof coating item",
                "unit": default_unit,
                "unit_price": 0.0,
                "item_source": "manual",
                "item_median_qty_per_sqft": 0.0,
                "item_median_cost_per_sqft": 0.0,
                "item_evidence_count": 0,
                "selected_item_reason": "No suitable roof coating pricing item matched; sealant/tube candidates were rejected for the main coating row.",
                "selected_item_score": 0.0,
                "top_rejected_item_reasons": bad_reasons,
            }
        selected_tuple = selectable[0] if selectable else scored[0]
        selected = dict(selected_tuple[1])
        selected["item_source"] = "current_pricing_plus_historical_usage" if _normalized(selected.get("item_name")) in historical_by_name else "current_pricing"
        selected["selected_item_reason"] = "; ".join(selected_tuple[2])
        selected["selected_item_score"] = round(float(selected_tuple[0]), 2)
        selected["top_rejected_item_reasons"] = [
            {
                "item_name": option.get("item_name"),
                "score": round(float(score), 2),
                "reason": "; ".join(reasons),
            }
            for score, option, reasons in scored
            if option is not selected_tuple[1]
        ]
        selected["top_rejected_item_reasons"] = selected["top_rejected_item_reasons"][:5]
        historical = historical_by_name.get(_normalized(selected.get("item_name")), {})
        selected["item_median_qty_per_sqft"] = historical.get("median_qty_per_sqft", 0.0)
        selected["item_median_cost_per_sqft"] = historical.get("median_cost_per_sqft", 0.0)
        selected["item_evidence_count"] = historical.get("evidence_count", 0)
        return selected
    if historical_options:
        historical_scored = []
        for option in historical_options:
            score, reasons = _package_item_fit_details(package, option, scope)
            historical_scored.append((score, option, reasons))
        historical_scored.sort(key=lambda item: (item[0], safe_number(item[1].get("evidence_count"), 0)), reverse=True)
        selectable = [item for item in historical_scored if _is_selectable_package_item(package, item[1], scope)]
        if package == "coating" and not selectable:
            return {
                "item_name": "Manual roof coating item",
                "unit": default_unit,
                "unit_price": 0.0,
                "item_source": "manual",
                "item_median_qty_per_sqft": 0.0,
                "item_median_cost_per_sqft": 0.0,
                "item_evidence_count": 0,
                "selected_item_reason": "No suitable historical roof coating item matched; manual item review required.",
                "selected_item_score": 0.0,
                "top_rejected_item_reasons": [
                    {"item_name": option.get("item_name"), "score": round(float(score), 2), "reason": "; ".join(reasons)}
                    for score, option, reasons in historical_scored[:5]
                ],
            }
        selected_tuple = selectable[0] if selectable else historical_scored[0]
        selected = dict(selected_tuple[1])
        selected["unit_price"] = 0.0
        selected["item_source"] = "historical_most_common_item" if safe_number(selected.get("median_qty_per_sqft"), 0) > 0 else "historical_cost_default"
        selected["item_median_qty_per_sqft"] = selected.get("median_qty_per_sqft", 0.0)
        selected["item_median_cost_per_sqft"] = selected.get("median_cost_per_sqft", 0.0)
        selected["item_evidence_count"] = selected.get("evidence_count", 0)
        selected["selected_item_reason"] = "Selected from historical package usage because no current pricing item matched."
        selected["selected_item_score"] = round(float(selected_tuple[0]), 2)
        selected["top_rejected_item_reasons"] = []
        return selected
    return {
        "item_name": fallback_label,
        "unit": default_unit,
        "unit_price": 0.0,
        "item_source": "manual",
        "item_median_qty_per_sqft": 0.0,
        "item_median_cost_per_sqft": 0.0,
        "item_evidence_count": 0,
        "selected_item_reason": "No current pricing or historical item matched; manual item review required.",
        "selected_item_score": 0.0,
        "top_rejected_item_reasons": [],
    }


def _item_options_payload(pricing_options: list[dict[str, Any]], historical_options: list[dict[str, Any]], selected: dict[str, Any]) -> str:
    options: dict[str, dict[str, Any]] = {}
    for option in [*historical_options, *pricing_options, selected]:
        name = str(option.get("item_name") or "").strip()
        if not name:
            continue
        existing = options.get(name, {})
        merged = {**existing, **option}
        options[name] = {
            "item_name": name,
            "unit": merged.get("unit"),
            "unit_price": safe_number(merged.get("unit_price"), 0.0),
            "item_source": merged.get("item_source") or merged.get("source") or "manual",
            "item_median_qty_per_sqft": safe_number(merged.get("item_median_qty_per_sqft") or merged.get("median_qty_per_sqft"), 0.0),
            "item_median_cost_per_sqft": safe_number(merged.get("item_median_cost_per_sqft") or merged.get("median_cost_per_sqft"), 0.0),
            "item_evidence_count": int(safe_number(merged.get("item_evidence_count") or merged.get("evidence_count"), 0)),
        }
    return json.dumps(list(options.values()), sort_keys=True, default=str)


def _pricing_option_for_item(row: dict[str, Any]) -> dict[str, Any] | None:
    item_name = _normalized(row.get("item_name"))
    if not item_name:
        return None
    try:
        options = json.loads(row.get("item_options_json") or "[]")
    except (TypeError, ValueError):
        options = []
    for option in options:
        if _normalized(option.get("item_name")) == item_name:
            return option
    return None


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
    package_jobs = rows[_package_match_series(rows, package)]["job_id"].dropna().astype(str).nunique()
    if package_jobs <= 0 and evidence_count > 0:
        package_jobs = evidence_count
    return round(min(package_jobs / denominator, 1.0), 4)


def _material_explanation(
    *,
    package: str,
    sizing: dict[str, Any],
    evidence_count: int,
    qty_per_sqft: float,
    status: str,
    scope: dict[str, Any],
    unit_price: float = 0.0,
    historical_cost_per_sqft: float = 0.0,
) -> str:
    reason = _suggestion_reason(package, scope, status)
    historical_jobs = int(safe_number(sizing.get("historical_jobs_found"), 0))
    accepted = int(safe_number(sizing.get("rows_accepted"), 0))
    rejected = int(safe_number(sizing.get("rows_rejected"), 0))
    diagnostics = f" Sizing pool accepted {accepted} rows and rejected {rejected}."
    rejection_reasons = str(sizing.get("rejection_reasons") or "")
    if rejection_reasons:
        diagnostics += f" Rejections: {rejection_reasons}."
    if evidence_count > 0 and qty_per_sqft > 0:
        text = (
            f"Used in {evidence_count} historical Roofing jobs. Median when used: {qty_per_sqft:g} per sqft."
            f"{diagnostics} {reason}"
        )
        if status != "yes":
            text += " Shown unchecked. Historical default is prefilled so estimator can include it if needed."
        if unit_price <= 0 and historical_cost_per_sqft > 0:
            text += " Current price not found; using historical cost default when included."
        elif unit_price <= 0:
            text += " Historical quantity exists but current price is missing."
        return text
    if historical_jobs > 0:
        text = (
            f"Found {historical_jobs} historical Roofing/package jobs, but accepted 0 for physical quantity sizing; "
            f"left quantity at 0 for estimator review.{diagnostics} {reason}"
        )
        if historical_cost_per_sqft > 0:
            text += " Historical usage exists, but physical quantity could not be normalized; using historical cost/sqft when included."
        return text
    if evidence_count > 0:
        return f"Used in {evidence_count} historical Roofing jobs, but no reliable historical quantity was found; left quantity at 0 for estimator review.{diagnostics} {reason}"
    if historical_cost_per_sqft > 0:
        return f"No historical quantity evidence found; using historical cost/sqft when included.{diagnostics} {reason}"
    return f"No historical quantity or cost evidence found.{diagnostics} {reason}"


def _labor_explanation(
    *,
    package: str,
    sizing: dict[str, Any],
    evidence_count: int,
    hours_per_1000: float,
    status: str,
    scope: dict[str, Any],
) -> str:
    reason = _suggestion_reason(package, scope, status)
    historical_jobs = int(safe_number(sizing.get("historical_jobs_found"), 0))
    accepted = int(safe_number(sizing.get("rows_accepted"), 0))
    rejected = int(safe_number(sizing.get("rows_rejected"), 0))
    diagnostics = f" Sizing pool accepted {accepted} rows and rejected {rejected}."
    rejection_reasons = str(sizing.get("rejection_reasons") or "")
    if rejection_reasons:
        diagnostics += f" Rejections: {rejection_reasons}."
    if evidence_count > 0 and hours_per_1000 > 0:
        text = (
            f"Used in {evidence_count} historical Roofing jobs. Median when used: {hours_per_1000:g} hours per 1,000 sqft."
            f"{diagnostics} {reason}"
        )
        if status != "yes":
            text += " Shown unchecked. Historical default is prefilled so estimator can include it if needed."
        return text
    if historical_jobs > 0:
        return (
            f"Found {historical_jobs} historical Roofing/package jobs, but accepted 0 for labor sizing; "
            f"left at 0 for estimator review.{diagnostics} {reason}"
        )
    if evidence_count > 0:
        return f"Used in {evidence_count} historical Roofing jobs, but no reliable labor rate was found; left at 0 for estimator review.{diagnostics} {reason}"
    return f"No historical Roofing labor evidence found; left at 0 for estimator review.{diagnostics} {reason}"


def _short_material_note(
    *,
    package: str,
    evidence_count: int,
    qty_per_sqft: float,
    status: str,
    unit_price: float,
    historical_cost_per_sqft: float,
    sizing: dict[str, Any],
    scope: dict[str, Any],
) -> str:
    notes: list[str] = []
    if evidence_count > 0 and qty_per_sqft > 0:
        notes.append(f"Historical default from {evidence_count} roofing jobs. Median when used: {qty_per_sqft:.4g}/sqft.")
    elif historical_cost_per_sqft > 0:
        notes.append("No normalized quantity found; using historical cost default if included.")
    else:
        notes.append("No reliable historical quantity or cost found.")
    notes.append(_suggestion_reason(package, scope, status))
    if status != "yes":
        notes.append("Shown unchecked. Historical default is prefilled if needed.")
    if unit_price > 0:
        notes.append("Current price found in pricing catalog.")
    elif historical_cost_per_sqft > 0:
        notes.append("No current price found; using historical cost default.")
    if sizing.get("variability_warning"):
        notes.append("Wide historical range; estimator should review.")
    return " ".join(part for part in notes if part)


def _short_labor_note(
    *,
    package: str,
    evidence_count: int,
    hours_per_1000: float,
    status: str,
    sizing: dict[str, Any],
    scope: dict[str, Any],
) -> str:
    notes: list[str] = []
    if evidence_count > 0 and hours_per_1000 > 0:
        notes.append(f"Historical default from {evidence_count} roofing jobs. Median when used: {hours_per_1000:.4g} hrs/1,000 sqft.")
    else:
        notes.append("No reliable historical labor default found.")
    notes.append(_suggestion_reason(package, scope, status))
    if status != "yes":
        notes.append("Shown unchecked. Historical default is prefilled if needed.")
    if sizing.get("variability_warning"):
        notes.append("Wide historical range; estimator should review.")
    return " ".join(part for part in notes if part)


def material_workbench_rows(
    recommendation: Any,
    data: Any,
    scope: dict[str, Any],
    historical_filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    area = _estimate_area(scope)
    notes = _scope_note_text(recommendation, scope)
    pricing = _frame(data, "pricing_catalog")
    if pricing.empty:
        pricing = _frame(data, "pricing")
    rows: list[dict[str, Any]] = []
    for spec in MATERIAL_PACKAGES:
        package = spec["package"]
        default_unit = str(spec.get("default_unit") or "unit")
        sizing = material_sizing_distribution(data, package, str(spec.get("default_unit") or "unit"), historical_filters)
        pricing_options = _pricing_options_for_package(pricing, spec, scope)
        historical_options = _historical_item_options(data, package, historical_filters, default_unit)
        selected_item = _select_material_item(package, pricing_options, historical_options, scope, str(spec.get("label") or package), default_unit)
        item_qty_per_sqft = safe_number(selected_item.get("item_median_qty_per_sqft"), 0.0)
        item_evidence_count = int(safe_number(selected_item.get("item_evidence_count"), 0))
        min_evidence = int(safe_number(sizing.get("minimum_evidence_count"), DEFAULT_MIN_EVIDENCE_COUNT))
        qty_per_sqft = item_qty_per_sqft if item_qty_per_sqft > 0 and item_evidence_count >= min_evidence else safe_number(sizing.get("median"), 0.0)
        historical_cost_per_sqft = safe_number(sizing.get("median_cost_per_sqft"), 0.0)
        if historical_cost_per_sqft <= 0:
            historical_cost_per_sqft = safe_number(selected_item.get("item_median_cost_per_sqft"), 0.0)
        historical_cost_evidence_count = int(safe_number(sizing.get("historical_cost_evidence_count"), 0))
        evidence_count = int(safe_number(sizing.get("evidence_count"), 0))
        unit_price = safe_number(selected_item.get("unit_price"), 0.0)
        price_source = str(selected_item.get("item_name") or "")
        status = _package_suggestion_status(recommendation, package, scope)
        include = status == "yes"
        if package == "coating" and scope.get("coating_type"):
            status = "yes"
            include = True
        editable_qty_per_sqft = qty_per_sqft
        scope_partial = scope.get("partial_scope") if isinstance(scope.get("partial_scope"), dict) else {}
        partial_basis_sqft = 0.0
        if package == "primer":
            partial_basis_sqft = safe_number(scope_partial.get("primer_basis_sqft"), 0.0) or _partial_primer_basis_sqft(notes, area)
        if include:
            editable_basis_sqft = partial_basis_sqft if partial_basis_sqft > 0 else area
        elif package == "coating":
            editable_basis_sqft = area
        elif partial_basis_sqft > 0:
            editable_basis_sqft = partial_basis_sqft
        else:
            editable_basis_sqft = 0.0
        calculated_quantity = editable_qty_per_sqft * editable_basis_sqft if include and editable_basis_sqft else 0.0
        if include and unit_price > 0:
            estimated_cost = calculated_quantity * unit_price
            selected_price_source = "current_pricing"
        elif include and historical_cost_per_sqft > 0 and editable_basis_sqft:
            estimated_cost = historical_cost_per_sqft * editable_basis_sqft
            selected_price_source = "historical_cost_default"
        else:
            estimated_cost = 0.0
            selected_price_source = "current_pricing_missing" if historical_cost_per_sqft <= 0 and unit_price <= 0 else "not_included"
        needs_review = bool(unit_price <= 0 and historical_cost_per_sqft > 0)
        item_source = str(selected_item.get("item_source") or "manual")
        item_name = str(selected_item.get("item_name") or spec["label"])
        explanation = _material_explanation(
            package=package,
            sizing=sizing,
            evidence_count=evidence_count,
            qty_per_sqft=qty_per_sqft,
            status=status,
            scope=scope,
            unit_price=unit_price,
            historical_cost_per_sqft=historical_cost_per_sqft,
        )
        if item_source == "current_pricing_plus_historical_usage":
            explanation += f" Default item selected from current pricing and historical usage: {item_name}."
        elif item_source == "current_pricing":
            explanation += f" Default item selected from current pricing: {item_name}."
        elif item_source.startswith("historical"):
            explanation += f" Default item selected from historical usage/cost evidence: {item_name}."
        else:
            explanation += " Item can be entered manually if the estimator wants a different product."
        short_note = _short_material_note(
            package=package,
            evidence_count=evidence_count,
            qty_per_sqft=qty_per_sqft,
            status=status,
            scope=scope,
            unit_price=unit_price,
            historical_cost_per_sqft=historical_cost_per_sqft,
            sizing=sizing,
        )
        rows.append(
            {
                "include": bool(include),
                "package": spec["label"],
                "package_key": package,
                "template_bucket": package,
                "workbook_row": str(spec.get("workbook_row") or ""),
                "item_name": item_name,
                "current_item": item_name,
                "historical_item": item_name if item_source.startswith("historical") else first_nonblank(selected_item.get("historical_item"), ""),
                "selected_item_reason": selected_item.get("selected_item_reason") or "",
                "selected_item_score": selected_item.get("selected_item_score") or 0.0,
                "top_rejected_item_reasons": selected_item.get("top_rejected_item_reasons") or [],
                "item_source": item_source,
                "item_options": " | ".join(option.get("item_name") for option in [*pricing_options, *historical_options] if option.get("item_name")),
                "item_options_json": _item_options_payload(pricing_options, historical_options, selected_item),
                "suggested_by_notes_rules": status,
                "historical_usage_rate": _historical_usage_rate(data, package, scope, evidence_count),
                "historical_qty_per_basis_sqft": round(qty_per_sqft, 6),
                "historical_qty_per_sqft": round(qty_per_sqft, 6),
                "historical_median": round(qty_per_sqft, 6),
                "item_level_qty_per_sqft": round(item_qty_per_sqft, 6),
                "item_level_evidence_count": item_evidence_count,
                "editable_basis_sqft": round(editable_basis_sqft, 2),
                "default_basis_sqft": round(editable_basis_sqft, 2),
                "p25_qty_per_sqft": round(safe_number(sizing.get("p25"), 0.0), 6),
                "p75_qty_per_sqft": round(safe_number(sizing.get("p75"), 0.0), 6),
                "editable_qty_per_sqft": round(editable_qty_per_sqft, 6),
                "editable_default": round(editable_qty_per_sqft, 6),
                "calculated_quantity": round(calculated_quantity, 2),
                "unit": selected_item.get("unit") or sizing.get("unit") or spec.get("default_unit"),
                "current_unit_price": round(unit_price, 4) if unit_price else 0.0,
                "current_price": round(unit_price, 4) if unit_price else 0.0,
                "historical_cost_per_sqft": round(historical_cost_per_sqft, 4),
                "historical_cost_default": round(historical_cost_per_sqft, 4),
                "estimated_cost": round(estimated_cost, 2),
                "evidence_count": evidence_count,
                "historical_cost_evidence_count": historical_cost_evidence_count,
                "historical_jobs_found": int(safe_number(sizing.get("historical_jobs_found"), 0)),
                "rows_accepted": int(safe_number(sizing.get("rows_accepted"), 0)),
                "rows_rejected": int(safe_number(sizing.get("rows_rejected"), 0)),
                "rejection_reasons": sizing.get("rejection_reasons") or "",
                "range_width": round(safe_number(sizing.get("range_width"), 0.0), 6),
                "relative_range_width": round(safe_number(sizing.get("relative_range_width"), 0.0), 4),
                "variability_warning": sizing.get("variability_warning") or "",
                "filters_applied": sizing.get("filters_applied") or "",
                "filters_relaxed": sizing.get("filters_relaxed") or "",
                "minimum_evidence_count": int(safe_number(sizing.get("minimum_evidence_count"), DEFAULT_MIN_EVIDENCE_COUNT)),
                "filter_hash": sizing.get("filter_hash") or historical_filter_hash(historical_filters),
                "manual_override": False,
                "reset_to_historical_default": False,
                "confidence": sizing.get("confidence") or _confidence(evidence_count),
                "source": sizing.get("source") or "no_sufficient_evidence",
                "pricing_source": price_source or selected_price_source,
                "price_source": selected_price_source,
                "needs_review": needs_review,
                "notes": short_note,
                "explanation": explanation,
            }
        )
    return rows


def labor_workbench_rows(
    recommendation: Any,
    data: Any,
    scope: dict[str, Any],
    hourly_rate: float = DEFAULT_HOURLY_RATE,
    historical_filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    area = _estimate_area(scope)
    rows: list[dict[str, Any]] = []
    for spec in LABOR_PACKAGES:
        package = spec["package"]
        sizing = labor_sizing_distribution(data, package, historical_filters)
        hours_per_1000 = safe_number(sizing.get("median"), 0.0)
        evidence_count = int(safe_number(sizing.get("evidence_count"), 0))
        status = _labor_suggestion_status(recommendation, package, scope)
        include = status == "yes"
        editable_hours_per_1000 = hours_per_1000
        calculated_hours = editable_hours_per_1000 * area / 1000 if include and area else 0.0
        crew_size = int(safe_number(sizing.get("median_crew_size"), 4) or 4)
        explanation = _labor_explanation(
            package=package,
            sizing=sizing,
            evidence_count=evidence_count,
            hours_per_1000=hours_per_1000,
            status=status,
            scope=scope,
        )
        rows.append(
            {
                "include": bool(include),
                "labor_package": spec["label"],
                "package_key": package,
                "template_bucket": package,
                "workbook_row": str(spec.get("workbook_row") or ""),
                "suggested_by_notes_rules": status,
                "historical_hours_per_1000_sqft": round(hours_per_1000, 4),
                "historical_median": round(hours_per_1000, 4),
                "p25_hours_per_1000_sqft": round(safe_number(sizing.get("p25"), 0.0), 4),
                "p75_hours_per_1000_sqft": round(safe_number(sizing.get("p75"), 0.0), 4),
                "editable_hours_per_1000_sqft": round(editable_hours_per_1000, 4),
                "editable_default": round(editable_hours_per_1000, 4),
                "calculated_hours": round(calculated_hours, 2),
                "crew_size": crew_size,
                "labor_rate": hourly_rate,
                "estimated_cost": round(calculated_hours * hourly_rate, 2),
                "evidence_count": evidence_count,
                "historical_jobs_found": int(safe_number(sizing.get("historical_jobs_found"), 0)),
                "rows_accepted": int(safe_number(sizing.get("rows_accepted"), 0)),
                "rows_rejected": int(safe_number(sizing.get("rows_rejected"), 0)),
                "rejection_reasons": sizing.get("rejection_reasons") or "",
                "range_width": round(safe_number(sizing.get("range_width"), 0.0), 4),
                "relative_range_width": round(safe_number(sizing.get("relative_range_width"), 0.0), 4),
                "variability_warning": sizing.get("variability_warning") or "",
                "filters_applied": sizing.get("filters_applied") or "",
                "filters_relaxed": sizing.get("filters_relaxed") or "",
                "minimum_evidence_count": int(safe_number(sizing.get("minimum_evidence_count"), DEFAULT_MIN_EVIDENCE_COUNT)),
                "filter_hash": sizing.get("filter_hash") or historical_filter_hash(historical_filters),
                "manual_override": False,
                "reset_to_historical_default": False,
                "confidence": sizing.get("confidence") or _confidence(evidence_count),
                "source": sizing.get("source") or "no_sufficient_evidence",
                "notes": _short_labor_note(
                    package=package,
                    sizing=sizing,
                    evidence_count=evidence_count,
                    hours_per_1000=hours_per_1000,
                    status=status,
                    scope=scope,
                ),
                "explanation": explanation,
            }
        )
    return rows


def adder_workbench_rows(
    recommendation: Any,
    data: Any = None,
    scope: dict[str, Any] | None = None,
    historical_filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    scope = scope or {}
    area = _estimate_area(scope)
    travel = _rec_value(recommendation, "travel_plan", {}) or {}
    travel_cost = safe_number(travel.get("travel_vehicle_cost"), 0.0) + safe_number(travel.get("travel_labor_cost"), 0.0)
    rows = []
    for spec in ADDER_ROWS:
        is_travel = spec["adder"] == "travel"
        sizing = adder_sizing_distribution(data, spec["adder"], area, historical_filters)
        historical_default = safe_number(sizing.get("editable_default"), 0.0)
        editable_value = travel_cost if is_travel and travel_cost > 0 else historical_default
        include = bool(is_travel and travel_cost > 0)
        estimated_cost = editable_value if include else 0.0
        notes = first_nonblank(travel.get("travel_notes"), "") if is_travel else ""
        if not notes and historical_default > 0:
            notes = (
                f"Shown unchecked. Historical default is prefilled so estimator can include it if needed. "
                f"Median when used: ${historical_default:,.2f} from {int(safe_number(sizing.get('evidence_count'), 0))} historical Roofing jobs."
            )
        rows.append(
            {
                "include": include,
                "adder": spec["label"],
                "adder_key": spec["adder"],
                "template_bucket": spec["adder"],
                "workbook_row": str(spec.get("workbook_row") or ""),
                "historical_usage_rate": safe_number(sizing.get("historical_usage_rate"), 0.0),
                "median_cost_when_used": round(safe_number(sizing.get("median_cost_when_used"), 0.0), 2),
                "median_cost_per_sqft": round(safe_number(sizing.get("median_cost_per_sqft"), 0.0), 4),
                "historical_median": round(safe_number(sizing.get("median_cost_when_used"), 0.0), 2),
                "historical_default_value": round(historical_default, 2),
                "editable_value": round(editable_value, 2),
                "editable_default": round(editable_value, 2),
                "estimated_cost": round(estimated_cost, 2),
                "evidence_count": int(safe_number(sizing.get("evidence_count"), 0)),
                "range_width": round(safe_number(sizing.get("range_width"), 0.0), 2),
                "relative_range_width": round(safe_number(sizing.get("relative_range_width"), 0.0), 4),
                "variability_warning": sizing.get("variability_warning") or "",
                "filters_applied": sizing.get("filters_applied") or "",
                "filters_relaxed": sizing.get("filters_relaxed") or "",
                "minimum_evidence_count": int(safe_number(sizing.get("minimum_evidence_count"), DEFAULT_MIN_EVIDENCE_COUNT)),
                "filter_hash": sizing.get("filter_hash") or historical_filter_hash(historical_filters),
                "manual_override": False,
                "reset_to_historical_default": False,
                "confidence": "review" if is_travel and travel_cost > 0 else sizing.get("confidence") or "none",
                "source": "travel_plan" if is_travel and travel_cost > 0 else sizing.get("source") or "manual",
                "needs_review": bool(editable_value > 0),
                "notes": notes,
            }
        )
    return rows


def build_estimating_workbench(
    recommendation: Any,
    data: Any = None,
    scope_override: dict[str, Any] | None = None,
    historical_filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scope = {**_scope_from_recommendation(recommendation), **(scope_override or {})}
    filters = {**historical_filters_from_scope(scope), **(historical_filters or {})}
    estimate_id = first_nonblank((_rec_value(recommendation, "parsed_fields", {}) or {}).get("run_id"), f"estimate-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}")
    return {
        "estimate_id": estimate_id,
        "scope": scope,
        "historical_filters": filters,
        "historical_filter_hash": historical_filter_hash(filters),
        "materials": material_workbench_rows(recommendation, data, scope, filters),
        "labor": labor_workbench_rows(recommendation, data, scope, historical_filters=filters),
        "adders": adder_workbench_rows(recommendation, data, scope, filters),
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
        if row.get("reset_to_historical_default"):
            row["editable_qty_per_sqft"] = row.get("historical_qty_per_sqft", 0.0)
            row["editable_basis_sqft"] = row.get("default_basis_sqft", row.get("editable_basis_sqft", 0.0))
            row["reset_to_historical_default"] = False
        matched_item = _pricing_option_for_item(row)
        if matched_item:
            row["unit"] = matched_item.get("unit") or row.get("unit")
            row["current_unit_price"] = round(safe_number(matched_item.get("unit_price"), 0.0), 4)
            row["item_source"] = matched_item.get("item_source") or row.get("item_source") or "manual"
        row["current_item"] = first_nonblank(row.get("item_name"), row.get("current_item"), row.get("package"))
        include = bool(row.get("include"))
        qty_per_sqft = safe_number(row.get("editable_qty_per_sqft"), 0.0)
        historical_qty = safe_number(row.get("historical_qty_per_sqft"), 0.0)
        basis_sqft = safe_number(row.get("editable_basis_sqft"), 0.0)
        if include and basis_sqft <= 0 and row.get("package_key") != "primer":
            basis_sqft = area
            row["editable_basis_sqft"] = round(basis_sqft, 2)
        default_basis_sqft = safe_number(row.get("default_basis_sqft"), 0.0)
        row["manual_override"] = abs(qty_per_sqft - historical_qty) > 1e-9 or abs(basis_sqft - default_basis_sqft) > 1e-9
        unit_price = safe_number(row.get("current_unit_price"), 0.0)
        if unit_price <= 0:
            unit_price = safe_number(row.get("current_price"), 0.0)
            row["current_unit_price"] = round(unit_price, 4) if unit_price else 0.0
        row["current_price"] = round(unit_price, 4) if unit_price else 0.0
        historical_cost_per_sqft = safe_number(row.get("historical_cost_per_sqft"), 0.0)
        quantity = qty_per_sqft * basis_sqft if include and basis_sqft else 0.0
        row["historical_median"] = round(historical_qty, 6)
        row["editable_default"] = round(qty_per_sqft, 6)
        row["calculated_quantity"] = round(quantity, 2)
        if include and unit_price > 0:
            row["estimated_cost"] = round(quantity * unit_price, 2)
            row["price_source"] = "current_pricing"
        elif include and historical_cost_per_sqft > 0 and basis_sqft:
            row["estimated_cost"] = round(historical_cost_per_sqft * basis_sqft, 2)
            row["price_source"] = "historical_cost_default"
            row["needs_review"] = True
        else:
            row["estimated_cost"] = 0.0
            row["price_source"] = "not_included" if not include else "current_pricing_missing"
    for row in updated.get("labor") or []:
        if row.get("reset_to_historical_default"):
            row["editable_hours_per_1000_sqft"] = row.get("historical_hours_per_1000_sqft", 0.0)
            row["reset_to_historical_default"] = False
        include = bool(row.get("include"))
        hours_per_1000 = safe_number(row.get("editable_hours_per_1000_sqft"), 0.0)
        historical_hours = safe_number(row.get("historical_hours_per_1000_sqft"), 0.0)
        row["manual_override"] = abs(hours_per_1000 - historical_hours) > 1e-9
        hours = hours_per_1000 * area / 1000 if include and area else 0.0
        row["historical_median"] = round(historical_hours, 4)
        row["editable_default"] = round(hours_per_1000, 4)
        row["calculated_hours"] = round(hours, 2)
        row["estimated_cost"] = round(hours * hourly_rate, 2)
    for row in updated.get("adders") or []:
        if row.get("reset_to_historical_default"):
            row["editable_value"] = row.get("historical_default_value", row.get("median_cost_when_used", 0.0))
            row["reset_to_historical_default"] = False
        historical_default = safe_number(row.get("historical_default_value"), 0.0)
        editable_value = safe_number(row.get("editable_value"), 0.0)
        row["historical_median"] = round(safe_number(row.get("median_cost_when_used"), historical_default), 2)
        row["editable_default"] = round(editable_value, 2)
        row["manual_override"] = abs(editable_value - historical_default) > 1e-9
        row["estimated_cost"] = round(safe_number(row.get("editable_value"), 0.0), 2) if row.get("include") else 0.0
    return updated


def _material_row_is_edited(row: dict[str, Any]) -> bool:
    return (
        bool(row.get("manual_override"))
        or abs(safe_number(row.get("editable_qty_per_sqft"), 0.0) - safe_number(row.get("historical_qty_per_sqft"), 0.0)) > 1e-9
        or abs(safe_number(row.get("editable_basis_sqft"), 0.0) - safe_number(row.get("default_basis_sqft"), 0.0)) > 1e-9
    )


def _labor_row_is_edited(row: dict[str, Any]) -> bool:
    return bool(row.get("manual_override")) or abs(safe_number(row.get("editable_hours_per_1000_sqft"), 0.0) - safe_number(row.get("historical_hours_per_1000_sqft"), 0.0)) > 1e-9


def _adder_row_is_edited(row: dict[str, Any]) -> bool:
    return bool(row.get("manual_override")) or abs(safe_number(row.get("editable_value"), 0.0) - safe_number(row.get("historical_default_value"), 0.0)) > 1e-9


def apply_historical_filter_update(previous_workbench: dict[str, Any] | None, filtered_workbench: dict[str, Any]) -> dict[str, Any]:
    """Merge a new filtered default pool with prior estimator edits.

    Filter changes should refresh historical medians for untouched rows, but they should not erase an
    estimator's edited quantity, labor rate, include checkbox, or adder amount.
    """
    if not previous_workbench:
        return filtered_workbench
    updated = deepcopy(filtered_workbench)

    previous_materials = {row.get("package_key"): row for row in previous_workbench.get("materials") or []}
    for row in updated.get("materials") or []:
        previous = previous_materials.get(row.get("package_key"))
        if not previous:
            continue
        row["include"] = previous.get("include", row.get("include"))
        row["current_unit_price"] = previous.get("current_unit_price", row.get("current_unit_price"))
        row["item_name"] = previous.get("item_name", row.get("item_name"))
        row["unit"] = previous.get("unit", row.get("unit"))
        if previous.get("reset_to_historical_default"):
            row["editable_qty_per_sqft"] = row.get("historical_qty_per_sqft", 0.0)
            row["editable_basis_sqft"] = row.get("default_basis_sqft", row.get("editable_basis_sqft", 0.0))
        elif _material_row_is_edited(previous):
            row["editable_qty_per_sqft"] = previous.get("editable_qty_per_sqft", row.get("editable_qty_per_sqft"))
            row["editable_basis_sqft"] = previous.get("editable_basis_sqft", row.get("editable_basis_sqft"))
            row["manual_override"] = True

    previous_labor = {row.get("package_key"): row for row in previous_workbench.get("labor") or []}
    for row in updated.get("labor") or []:
        previous = previous_labor.get(row.get("package_key"))
        if not previous:
            continue
        row["include"] = previous.get("include", row.get("include"))
        row["crew_size"] = previous.get("crew_size", row.get("crew_size"))
        row["labor_rate"] = previous.get("labor_rate", row.get("labor_rate"))
        if previous.get("reset_to_historical_default"):
            row["editable_hours_per_1000_sqft"] = row.get("historical_hours_per_1000_sqft", 0.0)
        elif _labor_row_is_edited(previous):
            row["editable_hours_per_1000_sqft"] = previous.get("editable_hours_per_1000_sqft", row.get("editable_hours_per_1000_sqft"))
            row["manual_override"] = True

    previous_adders = {row.get("adder_key"): row for row in previous_workbench.get("adders") or []}
    for row in updated.get("adders") or []:
        previous = previous_adders.get(row.get("adder_key"))
        if not previous:
            continue
        row["include"] = previous.get("include", row.get("include"))
        if previous.get("reset_to_historical_default"):
            row["editable_value"] = row.get("historical_default_value", row.get("editable_value"))
        elif _adder_row_is_edited(previous):
            row["editable_value"] = previous.get("editable_value", row.get("editable_value"))
            row["manual_override"] = True

    return recalculate_workbench_tables(updated)


def manual_material_workbench_row(scope: dict[str, Any] | None = None, *, item_name: str = "Manual custom item") -> dict[str, Any]:
    scope = scope or {}
    return {
        "include": False,
        "package": "Manual",
        "package_key": "manual",
        "template_bucket": "manual",
        "workbook_row": "",
        "item_name": item_name,
        "current_item": item_name,
        "historical_item": "",
        "item_source": "manual",
        "item_options": item_name,
        "item_options_json": _item_options_payload([], [], {"item_name": item_name, "unit": "unit", "unit_price": 0, "item_source": "manual"}),
        "suggested_by_notes_rules": "review",
        "historical_usage_rate": 0.0,
        "historical_qty_per_basis_sqft": 0.0,
        "historical_qty_per_sqft": 0.0,
        "historical_median": 0.0,
        "item_level_qty_per_sqft": 0.0,
        "item_level_evidence_count": 0,
        "editable_basis_sqft": 0.0,
        "default_basis_sqft": 0.0,
        "p25_qty_per_sqft": 0.0,
        "p75_qty_per_sqft": 0.0,
        "editable_qty_per_sqft": 0.0,
        "editable_default": 0.0,
        "calculated_quantity": 0.0,
        "unit": "unit",
        "current_unit_price": 0.0,
        "current_price": 0.0,
        "historical_cost_per_sqft": 0.0,
        "historical_cost_default": 0.0,
        "estimated_cost": 0.0,
        "evidence_count": 0,
        "historical_cost_evidence_count": 0,
        "historical_jobs_found": 0,
        "rows_accepted": 0,
        "rows_rejected": 0,
        "rejection_reasons": "",
        "range_width": 0.0,
        "relative_range_width": 0.0,
        "variability_warning": "",
        "filters_applied": "",
        "filters_relaxed": "",
        "minimum_evidence_count": DEFAULT_MIN_EVIDENCE_COUNT,
        "filter_hash": "",
        "manual_override": False,
        "reset_to_historical_default": False,
        "confidence": "manual",
        "source": "manual",
        "pricing_source": "manual",
        "price_source": "manual",
        "needs_review": True,
        "notes": "Manual material line. Enter item, basis, quantity rate, unit, and unit price.",
        "explanation": "Manual material line. Enter item, basis, quantity rate, unit, and unit price.",
    }


def workbench_to_draft_workbook_inputs(workbench: dict[str, Any]) -> dict[str, Any]:
    workbench = recalculate_workbench_tables(workbench)
    scope = workbench.get("scope") or {}
    material_rows = []
    for row in workbench.get("materials") or []:
        if not row.get("include"):
            continue
        material_rows.append(
            {
                "item": first_nonblank(row.get("item_name"), row.get("package")),
                "category": row.get("package_key"),
                "quantity": safe_number(row.get("calculated_quantity"), 0.0),
                "unit": row.get("unit"),
                "unit_price": safe_number(row.get("current_unit_price"), 0.0),
                "estimated_cost": safe_number(row.get("estimated_cost"), 0.0),
                "notes": (
                    f"Workbench edited value; item_source={row.get('item_source')}; "
                    f"source={row.get('source')}; evidence_count={row.get('evidence_count')}; "
                    f"basis_sqft={row.get('editable_basis_sqft')}"
                ),
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
