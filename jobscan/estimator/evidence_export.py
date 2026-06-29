from __future__ import annotations

import json
import hashlib
import math
import re
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pandas as pd

try:
    import numpy as np
except ImportError:  # pragma: no cover - pandas normally brings numpy, but keep the exporter optional-safe.
    np = None


EVIDENCE_SHEETS = [
    "README",
    "run_integrity",
    "parsed_scope",
    "material_plan",
    "material_evidence",
    "labor_plan",
    "labor_evidence",
    "labor_diagnostics",
    "similar_jobs",
    "relationship_rows",
    "rejected_evidence",
    "estimate_rollup",
]

DEFAULT_EVIDENCE_LIMIT = 50

VERBOSE_EVIDENCE_COLUMNS = {
    "quantity_evidence_diagnostics",
    "rejected_rows",
    "raw",
    "raw_json",
    "cell_values",
    "formula_cells",
    "evidence_line_item_ids",
}

LABOR_TASKS = [
    "labor_prep",
    "labor_prime",
    "labor_seam_sealer",
    "labor_base",
    "labor_top_coat",
    "labor_details",
    "labor_caulk",
    "labor_cleanup",
    "labor_loading",
    "labor_traveling",
]


def _is_missing_scalar(value: Any) -> bool:
    try:
        result = pd.isna(value)
    except Exception:
        return False
    if isinstance(result, bool):
        return result
    return False


def sanitize_for_export(value: Any, *, excel: bool = False) -> Any:
    """Convert nested estimator output into JSON/XLSX-safe scalar values."""

    if value is None:
        return None
    if is_dataclass(value):
        return sanitize_for_export(asdict(value), excel=excel)
    if isinstance(value, pd.DataFrame):
        return sanitize_for_export(value.to_dict(orient="records"), excel=excel)
    if isinstance(value, pd.Series):
        return sanitize_for_export(value.to_dict(), excel=excel)
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        return value.isoformat()
    if np is not None and isinstance(value, np.datetime64):
        timestamp = pd.Timestamp(value)
        if pd.isna(timestamp):
            return None
        return timestamp.isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        if value.is_nan() or value.is_infinite():
            return None
        try:
            return float(value)
        except Exception:
            return str(value)
    if isinstance(value, (UUID, Path)):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if np is not None and isinstance(value, np.floating):
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return None
        return number
    if np is not None and isinstance(value, np.integer):
        return int(value)
    if _is_missing_scalar(value):
        return None
    if isinstance(value, dict):
        return {str(key): sanitize_for_export(item, excel=excel) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_for_export(item, excel=excel) for item in value]
    if isinstance(value, set):
        return [sanitize_for_export(item, excel=excel) for item in sorted(value, key=str)]
    return value


def _jsonable(value: Any) -> Any:
    return sanitize_for_export(value, excel=False)


def _notes_hash(notes: str | None) -> str:
    return hashlib.sha256((notes or "").encode("utf-8")).hexdigest()


def _normalized_text(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def _collect_source_text_fields(value: Any, path: str = "") -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if str(key) == "source_text" and item:
                rows.append({"field": child_path, "source_text": str(item)})
            rows.extend(_collect_source_text_fields(item, child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            rows.extend(_collect_source_text_fields(item, f"{path}[{index}]"))
    return rows


def _stale_source_text_rows(parsed_fields: dict[str, Any], notes: str | None) -> list[dict[str, str]]:
    normalized_notes = _normalized_text(notes)
    stale: list[dict[str, str]] = []
    for row in _collect_source_text_fields(parsed_fields):
        source = _normalized_text(row.get("source_text"))
        if source and source not in normalized_notes:
            stale.append(row)
    return stale


def _dict_from_object(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return _jsonable(value)
    return _jsonable(vars(value)) if hasattr(value, "__dict__") else {}


def _records(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, pd.DataFrame):
        return _jsonable(value.to_dict(orient="records"))
    if isinstance(value, list):
        rows = []
        for item in value:
            if isinstance(item, dict):
                rows.append(_jsonable(item))
            elif is_dataclass(item):
                rows.append(_jsonable(asdict(item)))
            else:
                rows.append({"value": _jsonable(item)})
        return rows
    if isinstance(value, dict):
        return [_jsonable(value)]
    return [{"value": _jsonable(value)}]


def _scope_type_for_export(recommendation: dict[str, Any]) -> str:
    parsed = recommendation.get("parsed_fields") or {}
    text = " ".join(str(value or "") for value in parsed.values()).lower()
    if any(term in text for term in ("spray foam", "closed-cell", "closed cell", "open-cell", "open cell", "insulation")):
        return "insulation"
    if any(term in text for term in ("roof", "roofing", "coating", "silicone", "acrylic", "metal")):
        return "roofing"
    return ""


def _row_template_type(row: dict[str, Any]) -> str:
    value = str(row.get("template_type") or row.get("job_template_type") or row.get("template_name") or "").strip().lower()
    if value in {"roof", "roofing", "roof coating"}:
        return "roofing"
    if value in {"insulation", "foam", "spray foam"}:
        return "insulation"
    return "" if value in {"unknown", "none", "null"} else value


def _row_source_text(row: dict[str, Any]) -> str:
    return " ".join(
        str(row.get(column) or "").lower()
        for column in ("source_file", "folder_path", "relative_path", "job_name", "customer", "estimate_file", "document_name", "division")
    )


def _normal_export_row_allowed(row: dict[str, Any], *, scope_type: str, debug_evidence: bool) -> bool:
    if debug_evidence:
        return True
    template_bucket = str(row.get("template_bucket") or row.get("package") or row.get("matched_package") or "").strip().lower()
    if template_bucket == "unknown":
        return False
    if scope_type == "roofing":
        row_type = _row_template_type(row)
        if row_type == "insulation":
            return False
        source_text = _row_source_text(row)
        if any(term in source_text for term in ("insulation", "spray foam", "closed-cell", "closed cell", "open-cell", "open cell")):
            return False
    return True


def _evidence_package_key(row: dict[str, Any]) -> str:
    return str(
        row.get("requested_package")
        or row.get("package")
        or row.get("category")
        or row.get("matched_package")
        or row.get("task")
        or row.get("evidence_source_table")
        or "unknown"
    )


def _count_serialized_items(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (list, tuple, set)):
        return len(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0
        try:
            parsed = json.loads(text)
        except Exception:
            return 1
        if isinstance(parsed, (list, tuple, set)):
            return len(parsed)
        return 1
    return 1


def _compact_evidence_row(row: dict[str, Any], *, debug_evidence: bool) -> dict[str, Any]:
    if debug_evidence:
        return row
    compact = dict(row)
    if "evidence_line_item_ids" in compact:
        compact["evidence_line_item_count"] = _count_serialized_items(compact.get("evidence_line_item_ids"))
    for column in VERBOSE_EVIDENCE_COLUMNS:
        compact.pop(column, None)
    return compact


def _limit_evidence_rows(rows: list[dict[str, Any]], *, evidence_limit: int, debug_evidence: bool) -> list[dict[str, Any]]:
    if evidence_limit <= 0 or debug_evidence:
        return [_compact_evidence_row(row, debug_evidence=debug_evidence) for row in rows]
    counts: dict[str, int] = {}
    limited: list[dict[str, Any]] = []
    for row in rows:
        package = _evidence_package_key(row)
        counts[package] = counts.get(package, 0) + 1
        if counts[package] <= evidence_limit:
            limited.append(_compact_evidence_row(row, debug_evidence=debug_evidence))
    return limited


def _frame_records(frame: pd.DataFrame | None, *, source_table: str, limit: int = 5000) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    rows = frame.head(limit).copy()
    rows["evidence_source_table"] = source_table
    return _records(rows)


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _truthy_cost(value: Any) -> bool:
    number = _safe_float(value)
    return number is not None and number > 0


def _safe_filename(value: str | None, default: str = "estimator_evidence") -> str:
    text = str(value or default).strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text[:80] or default


def _row_matches_package(row: dict[str, Any], package: str) -> bool:
    text = " ".join(
        str(row.get(column) or "")
        for column in (
            "template_bucket",
            "package",
            "category",
            "item",
            "selected_item_name",
            "row_label",
            "line_item_kind",
            "labor_package",
            "task",
        )
    ).lower()
    package_key = package.lower().replace(" ", "_")
    return package_key in text.replace(" ", "_") or package_key.replace("labor_", "") in text.replace(" ", "_")


def _flatten_diagnostics(debug: dict[str, Any], labor_plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    diagnostics = debug.get("labor_calibration", {}) if isinstance(debug, dict) else {}
    tasks = diagnostics.get("tasks", {}) if isinstance(diagnostics, dict) else {}
    plan_by_task = {str(row.get("task") or ""): row for row in labor_plan if isinstance(row, dict)}
    rows: list[dict[str, Any]] = []
    for task, detail in tasks.items():
        detail = detail if isinstance(detail, dict) else {}
        plan_row = plan_by_task.get(str(task), {})
        selected_level = detail.get("selection_level")
        rows.append(
            {
                "task": task,
                "selected_method": detail.get("selected_source"),
                "selection_level": selected_level,
                "selected_evidence_count": detail.get("selected_calibration_rows", 0),
                "exact_match_count": detail.get("selected_calibration_rows", 0) if selected_level == "exact" else 0,
                "relaxed_warranty_count": detail.get("selected_calibration_rows", 0) if selected_level == "relaxed_warranty" else 0,
                "relaxed_project_count": detail.get("selected_calibration_rows", 0) if selected_level == "relaxed_project" else 0,
                "roofing_template_count": detail.get("selected_calibration_rows", 0) if selected_level == "all_roofing_template_bucket" else 0,
                "all_history_count": detail.get("candidate_historical_rows", 0),
                "relationship_rows": detail.get("candidate_relationship_rows", 0),
                "package_summary_rows": detail.get("candidate_package_rows", 0),
                "valid_numeric_count": detail.get("after_numeric_validation", 0),
                "rejected_numeric_count": len(detail.get("rejected_rows") or []),
                "fallback_used": detail.get("selected_source") == "rule_based_fallback",
                "fallback_reason": "No valid historical evidence selected." if detail.get("selected_source") == "rule_based_fallback" else "",
                "final_hours": plan_row.get("total_hours"),
                "final_cost": plan_row.get("estimated_cost"),
                "final_crew_size": plan_row.get("crew_size"),
                "final_notes": plan_row.get("notes"),
            }
        )
    if not rows:
        rows.append({"task": "", "diagnostic": "No labor calibration diagnostics were attached to the recommendation."})
    return rows


def _material_plan_rows(recommendation: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in _records(recommendation.get("material_plan")):
        cost = _safe_float(row.get("estimated_cost"))
        source = str(row.get("selected_price_source") or row.get("price_source_type") or "")
        sanity = str(row.get("sanity_check_status") or "")
        included = (
            row.get("included_in_total") is not False
            and cost is not None
            and source not in {"rejected_historical_quantity_ratio", "historical_cost_ratio_fallback", "review_allowance"}
            and not sanity.lower().startswith("blocked")
        )
        rows.append(
            {
                **row,
                "package": row.get("category") or row.get("package") or row.get("item"),
                "included_in_total": included,
                "review_required": bool(row.get("needs_review") or row.get("review_required")),
                "evidence_count": row.get("evidence_count", row.get("matched_comparable_job_count")),
                "calibration_method": row.get("calibration_method") or row.get("selected_price_source") or row.get("price_source_type"),
                "rejected_reason": row.get("rejected_reason") or row.get("sanity_check_reason"),
            }
        )
    return rows


def _labor_plan_rows(recommendation: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in _records(recommendation.get("labor_plan")):
        rows.append(
            {
                **row,
                "labor_package": row.get("labor_package") or row.get("task"),
                "included_in_total": _truthy_cost(row.get("estimated_cost")),
                "review_required": bool(row.get("needs_review") or row.get("review_required")),
                "source_type": row.get("source_type") or row.get("calibration_method"),
                "sanity_check_status": row.get("sanity_check_status") or "not_checked",
            }
        )
    return rows


def _material_evidence_rows(
    recommendation: dict[str, Any],
    data: Any,
    *,
    evidence_limit: int = DEFAULT_EVIDENCE_LIMIT,
    debug_evidence: bool = False,
    fast: bool = False,
) -> list[dict[str, Any]]:
    material_rows = _material_plan_rows(recommendation)
    packages = {str(row.get("category") or row.get("package") or row.get("item") or "").lower() for row in material_rows}
    rows: list[dict[str, Any]] = []
    scope_type = _scope_type_for_export(recommendation)
    calibration = recommendation.get("historical_calibration") or {}
    material_calibration = calibration.get("material_calibration") if isinstance(calibration, dict) else None
    if isinstance(material_calibration, dict):
        for package, detail in material_calibration.items():
            if isinstance(detail, dict):
                selected_item = detail.get("selected_current_price_item") or {}
                rows.append(
                    {
                        "package": package,
                        "current_pricing_item_selected": selected_item.get("product_name") if isinstance(selected_item, dict) else "",
                        "current_unit_price": detail.get("selected_current_unit_price"),
                        "current_price_unit": detail.get("unit"),
                        "historical_physical_quantity_rows_considered": detail.get("historical_physical_quantity_rows_considered"),
                        "historical_cost_fallback_rows_considered": detail.get("historical_cost_fallback_rows_considered"),
                        "candidate_physical_rows_count": detail.get("candidate_physical_rows_count"),
                        "rejected_physical_rows_count": detail.get("rejected_physical_rows_count"),
                        "valid_quantity_ratio_count": detail.get("valid_quantity_ratio_count"),
                        "median_quantity_per_sqft": detail.get("median_quantity_per_sqft"),
                        "p25_quantity_per_sqft": detail.get("p25_quantity_per_sqft"),
                        "p75_quantity_per_sqft": detail.get("p75_quantity_per_sqft"),
                        "median_cost_per_sqft": detail.get("median_cost_per_sqft"),
                        "historical_cost_ratio_was_used": any(
                            row.get("category") == package and row.get("calibration_method") == "historical_cost_ratio_fallback"
                            for row in material_rows
                        ),
                        "fallback_reason": next((row.get("fallback_reason") for row in material_rows if row.get("category") == package), ""),
                        "quantity_ratio_rejection_reasons": "; ".join(str(reason) for reason in detail.get("quantity_ratio_rejection_reasons") or []),
                        "evidence_source_table": "recommendation.historical_calibration.material_calibration",
                        "included_as_evidence": True,
                    }
                )
    if not fast:
        for table_name in ("template_rows", "job_package_summary", "pricing_catalog", "pricing"):
            frame = getattr(data, table_name, pd.DataFrame()) if data is not None else pd.DataFrame()
            for row in _frame_records(frame, source_table=table_name, limit=max(evidence_limit * max(len(packages), 1) * 4, evidence_limit)):
                if not _normal_export_row_allowed(row, scope_type=scope_type, debug_evidence=debug_evidence):
                    continue
                if table_name == "job_package_summary" and _safe_float(row.get("area_sqft")) is None:
                    continue
                if not packages or any(_row_matches_package(row, package) for package in packages):
                    rows.append(
                        {
                            **row,
                            "included_as_evidence": True,
                            "filter_stage": "package_keyword_match",
                            "match_reason": "Matched material package/category text.",
                        }
                    )
    if not rows:
        rows.append({"evidence_source_table": "", "message": "No material evidence rows were available for export."})
    return _limit_evidence_rows(rows, evidence_limit=evidence_limit, debug_evidence=debug_evidence)


def _labor_evidence_rows(
    recommendation: dict[str, Any],
    data: Any,
    *,
    evidence_limit: int = DEFAULT_EVIDENCE_LIMIT,
    debug_evidence: bool = False,
    fast: bool = False,
) -> list[dict[str, Any]]:
    labor_rows = _labor_plan_rows(recommendation)
    tasks = {str(row.get("task") or row.get("labor_package") or "") for row in labor_rows}
    diagnostics = recommendation.get("debug", {}).get("labor_calibration", {}) if isinstance(recommendation.get("debug"), dict) else {}
    task_details = diagnostics.get("tasks", {}) if isinstance(diagnostics, dict) else {}
    plan_by_task = {str(row.get("task") or ""): row for row in labor_rows}
    scope_type = _scope_type_for_export(recommendation)
    rows: list[dict[str, Any]] = []
    for task, plan_row in plan_by_task.items():
        rows.append(
            {
                "package": task,
                "requested_package": task,
                "historical_labels_matched": "",
                "evidence_count": plan_row.get("evidence_count"),
                "median_hours_per_sqft": (_safe_float(plan_row.get("total_hours")) / _safe_float(recommendation.get("parsed_fields", {}).get("estimated_sqft") or recommendation.get("parsed_fields", {}).get("surface_area_sqft")))
                if _safe_float(plan_row.get("total_hours")) and _safe_float(recommendation.get("parsed_fields", {}).get("estimated_sqft") or recommendation.get("parsed_fields", {}).get("surface_area_sqft"))
                else None,
                "estimated_hours": plan_row.get("total_hours"),
                "selected_crew_size": plan_row.get("crew_size"),
                "estimated_days": plan_row.get("adjusted_days") or plan_row.get("crew_days"),
                "current_default_labor_rate": 72.0,
                "estimated_cost": plan_row.get("estimated_cost"),
                "fallback_used": plan_row.get("calibration_method") == "rule_based_fallback",
                "fallback_reason": plan_row.get("fallback_reason") or ("Fallback labor assumption." if plan_row.get("calibration_method") == "rule_based_fallback" else ""),
                "evidence_source_table": "recommendation.labor_plan",
                "included_as_evidence": True,
            }
        )
    if debug_evidence and not fast:
        for task, detail in task_details.items():
            detail = detail if isinstance(detail, dict) else {}
            for rejected in detail.get("rejected_rows") or []:
                rows.append(
                    {
                        "requested_package": task,
                        "evidence_source_table": rejected.get("source"),
                        "included_as_evidence": False,
                        "rejected_reason": rejected.get("reason"),
                        "filter_stage": "numeric_validation",
                        "match_reason": "",
                        "evidence_weight": 0,
                    }
                )
    if not fast:
        for table_name in ("relationship_labor_rates", "job_package_summary", "template_rows"):
            frame = getattr(data, table_name, pd.DataFrame()) if data is not None else pd.DataFrame()
            for row in _frame_records(frame, source_table=table_name, limit=max(evidence_limit * max(len(tasks), 1) * 4, evidence_limit)):
                if not _normal_export_row_allowed(row, scope_type=scope_type, debug_evidence=debug_evidence):
                    continue
                if table_name == "job_package_summary" and _safe_float(row.get("area_sqft")) is None:
                    continue
                matched = [task for task in tasks if task and _row_matches_package(row, task)]
                if matched:
                    rows.append(
                        {
                            "requested_package": matched[0],
                            "matched_package": row.get("template_bucket") or row.get("package") or row.get("labor_package"),
                            "included_as_evidence": True,
                            "rejected_reason": "",
                            "filter_stage": "task_keyword_match",
                            "match_reason": "Matched requested labor task.",
                            "evidence_weight": 1,
                            **row,
                        }
                    )
    if not rows:
        rows.append({"evidence_source_table": "", "message": "No labor evidence rows were available for export."})
    return _limit_evidence_rows(rows, evidence_limit=evidence_limit, debug_evidence=debug_evidence)


def _similar_job_rows(recommendation: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in _records(recommendation.get("similar_examples")):
        price_per_sqft = _safe_float(row.get("price_per_sqft"))
        job_text = " ".join(str(row.get(key) or "") for key in ("customer", "job_name", "reason_matched")).lower()
        outlier_reasons = []
        if price_per_sqft is not None and (price_per_sqft < 1 or price_per_sqft > 75):
            outlier_reasons.append("price_per_sqft_outside_expected_range")
        if any(term in job_text for term in ("all trades", "facade", "façade", "tenant improvement")):
            outlier_reasons.append("scope_mismatch_keyword")
        rows.append(
            {
                **row,
                "outlier_flag": bool(outlier_reasons),
                "outlier_reason": "; ".join(outlier_reasons),
                "used_for_material_calibration": "",
                "used_for_labor_calibration": "",
            }
        )
    if not rows:
        rows.append({"message": "No similar jobs were attached to the recommendation."})
    return rows


def _relationship_rows(data: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for table_name in (
        "relationship_labor_rates",
        "job_package_summary",
        "relationship_material_qty_ratios",
        "relationship_package_cooccurrence",
        "relationship_warranty_coating",
    ):
        frame = getattr(data, table_name, pd.DataFrame()) if data is not None else pd.DataFrame()
        table_rows = _frame_records(frame, source_table=table_name)
        if table_rows:
            rows.extend(table_rows)
        else:
            rows.append({"evidence_source_table": table_name, "message": "No rows loaded for this relationship source."})
    return rows


def _rejected_evidence_rows(recommendation: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for flag in recommendation.get("review_flags") or []:
        if any(term in str(flag).lower() for term in ("reject", "blocked", "invalid", "implausible", "missing")):
            rows.append({"evidence_type": "review_flag", "severity": "warning", "reason": flag})
    for row in _material_plan_rows(recommendation):
        source = str(row.get("selected_price_source") or "")
        sanity = str(row.get("sanity_check_status") or "")
        if source == "rejected_historical_quantity_ratio" or sanity.lower().startswith("blocked"):
            rows.append(
                {
                    "evidence_type": "material",
                    "package": row.get("category") or row.get("package"),
                    "item": row.get("item"),
                    "severity": "blocker",
                    "rejected_quantity": row.get("quantity"),
                    "rejected_estimated_cost": row.get("estimated_cost"),
                    "reason": row.get("rejected_reason") or row.get("notes") or sanity,
                }
            )
    diagnostics = recommendation.get("debug", {}).get("labor_calibration", {}) if isinstance(recommendation.get("debug"), dict) else {}
    for task, detail in (diagnostics.get("tasks", {}) if isinstance(diagnostics, dict) else {}).items():
        for rejected in (detail or {}).get("rejected_rows") or []:
            rows.append(
                {
                    "evidence_type": "labor",
                    "package": task,
                    "severity": "info",
                    "reason": rejected.get("reason"),
                    "source": rejected.get("source"),
                }
            )
    if not rows:
        rows.append({"evidence_type": "", "severity": "", "reason": "No rejected evidence was recorded."})
    return rows


def _estimate_rollup_rows(recommendation: dict[str, Any]) -> list[dict[str, Any]]:
    material_rows = _material_plan_rows(recommendation)
    labor_rows = _labor_plan_rows(recommendation)
    material_total = sum(_safe_float(row.get("estimated_cost")) or 0 for row in material_rows if row.get("included_in_total"))
    labor_total = sum(_safe_float(row.get("estimated_cost")) or 0 for row in labor_rows if row.get("included_in_total"))
    review_total = sum(
        _safe_float(row.get("estimated_cost")) or 0
        for row in material_rows + labor_rows
        if row.get("included_in_total") and row.get("review_required")
    )
    travel = recommendation.get("travel_plan") or {}
    travel_total = (_safe_float(travel.get("travel_vehicle_cost")) or 0) + (_safe_float(travel.get("travel_labor_cost")) or 0)
    return [
        {
            "bucket": "materials",
            "target": round(material_total, 2),
            "row_count_included": sum(1 for row in material_rows if row.get("included_in_total")),
            "row_count_excluded": sum(1 for row in material_rows if not row.get("included_in_total")),
        },
        {
            "bucket": "labor",
            "target": round(labor_total, 2),
            "row_count_included": sum(1 for row in labor_rows if row.get("included_in_total")),
            "row_count_excluded": sum(1 for row in labor_rows if not row.get("included_in_total")),
        },
        {"bucket": "travel", "target": round(travel_total, 2), "row_count_included": 1 if travel else 0, "row_count_excluded": 0},
        {"bucket": "review_allowances_included", "target": round(review_total, 2), "row_count_included": "", "row_count_excluded": ""},
        {
            "bucket": "estimate",
            "low": recommendation.get("estimate_low"),
            "target": recommendation.get("estimate_target"),
            "high": recommendation.get("estimate_high"),
            "human_review_required": recommendation.get("human_review_required"),
            "warnings": "; ".join(str(flag) for flag in recommendation.get("review_flags") or []),
        },
    ]


def _run_integrity_rows(recommendation: dict[str, Any], notes: str | None) -> list[dict[str, Any]]:
    parsed_fields = recommendation.get("parsed_fields") or {}
    debug = recommendation.get("debug") or {}
    existing = debug.get("run_integrity") if isinstance(debug, dict) else {}
    existing = existing if isinstance(existing, dict) else {}
    input_hash = _notes_hash(notes)
    parsed_hash = parsed_fields.get("input_notes_hash") or existing.get("parsed_scope_notes_hash")
    stale_rows = _stale_source_text_rows(parsed_fields, notes)
    hash_mismatch = bool(parsed_hash and parsed_hash != input_hash)
    warnings = list(existing.get("warnings") or [])
    if stale_rows and "Possible stale parse/cache contamination." not in warnings:
        warnings.append("Possible stale parse/cache contamination.")
    if hash_mismatch:
        warnings.append("Recommendation notes hash does not match export notes hash; exported recommendation may belong to previous notes.")
    return [
        {
            "run_id": existing.get("run_id") or parsed_fields.get("run_id"),
            "input_notes_hash": input_hash,
            "parsed_scope_notes_hash": parsed_hash,
            "stale_source_text_detected": bool(stale_rows or existing.get("stale_source_text_detected")),
            "stale_fields_detected": json.dumps(stale_rows or existing.get("stale_fields_detected") or [], default=str, sort_keys=True),
            "prior_cache_used": bool(existing.get("prior_cache_used")),
            "hash_mismatch": hash_mismatch,
            "warnings": "; ".join(str(item) for item in warnings),
        }
    ]


def build_estimator_evidence_export(
    recommendation: Any,
    data: Any = None,
    notes: str | None = None,
    output_dir: Path | str | None = None,
    *,
    evidence_limit: int = DEFAULT_EVIDENCE_LIMIT,
    fast: bool = False,
    debug_evidence: bool = False,
) -> dict[str, Any]:
    export_started = datetime.now(UTC)
    recommendation_dict = _dict_from_object(recommendation)
    parsed_fields = recommendation_dict.get("parsed_fields") or {}
    header = (recommendation_dict.get("draft_workbook_inputs") or {}).get("header") or {}
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    source_files = getattr(data, "source_files_used", []) if data is not None else []
    data_warnings = getattr(data, "warnings", []) if data is not None else []
    run_integrity = _run_integrity_rows(recommendation_dict, notes)
    parsed_scope = [{**parsed_fields, **{f"header_{key}": value for key, value in header.items()}, "review_flags": "; ".join(recommendation_dict.get("review_flags") or [])}]
    material_plan = _material_plan_rows(recommendation_dict)
    labor_plan = _labor_plan_rows(recommendation_dict)
    estimated_sqft = parsed_fields.get("estimated_sqft") or parsed_fields.get("surface_area_sqft") or header.get("C12_estimated_sqft")
    estimated_sqft_number = _safe_float(estimated_sqft)
    total_labor_hours = sum(_safe_float(row.get("total_hours")) or 0 for row in labor_plan)
    total_labor_cost = sum(_safe_float(row.get("estimated_cost")) or 0 for row in labor_plan if row.get("included_in_total"))
    crew_days_total = sum(_safe_float(row.get("adjusted_days")) or _safe_float(row.get("crew_days")) or 0 for row in labor_plan)
    labor_debug = recommendation_dict.get("debug", {}).get("labor_calibration", {}) if isinstance(recommendation_dict.get("debug"), dict) else {}
    labor_selection_summary = labor_debug.get("selection_summary", {}) if isinstance(labor_debug, dict) else {}
    material_evidence = _material_evidence_rows(
        recommendation_dict,
        data,
        evidence_limit=evidence_limit,
        debug_evidence=debug_evidence,
        fast=fast,
    )
    labor_evidence = _labor_evidence_rows(
        recommendation_dict,
        data,
        evidence_limit=evidence_limit,
        debug_evidence=debug_evidence,
        fast=fast,
    )
    runtime_seconds_by_stage = dict((recommendation_dict.get("debug") or {}).get("runtime_seconds_by_stage") or {})
    runtime_seconds_by_stage["export_evidence"] = round((datetime.now(UTC) - export_started).total_seconds(), 4)
    material_total = sum(_safe_float(row.get("estimated_cost")) or 0 for row in material_plan if row.get("included_in_total"))
    labor_total = sum(_safe_float(row.get("estimated_cost")) or 0 for row in labor_plan if row.get("included_in_total"))
    gross_area = parsed_fields.get("gross_area_sqft") or header.get("gross_area_sqft")
    deduction_area = parsed_fields.get("deduction_area_sqft") or header.get("deduction_area_sqft")
    run_summary = {
        "generated_at": generated_at,
        "run_id": run_integrity[0].get("run_id"),
        "input_notes_hash": run_integrity[0].get("input_notes_hash"),
        "parsed_scope_notes_hash": run_integrity[0].get("parsed_scope_notes_hash"),
        "stale_source_text_detected": run_integrity[0].get("stale_source_text_detected"),
        "notes": notes or "",
        "project_name": header.get("C2_job_name") or parsed_fields.get("project_type") or "Estimator recommendation",
        "estimated_sqft": estimated_sqft,
        "gross_area_sqft": gross_area,
        "deduction_area_sqft": deduction_area,
        "roof_condition": parsed_fields.get("roof_condition"),
        "condition_detail_flags": parsed_fields.get("condition_detail_flags"),
        "material_total": round(material_total, 2),
        "labor_total": round(labor_total, 2),
        "estimate_low": recommendation_dict.get("estimate_low"),
        "estimate_target": recommendation_dict.get("estimate_target"),
        "estimate_high": recommendation_dict.get("estimate_high"),
        "human_review_required": recommendation_dict.get("human_review_required"),
        "material_rows": len(material_plan),
        "labor_rows": len(labor_plan),
        "similar_jobs": len(_records(recommendation_dict.get("similar_examples"))),
        "review_flags": len(recommendation_dict.get("review_flags") or []),
        "total_labor_hours": round(total_labor_hours, 2),
        "labor_hours_per_1000_sqft": round(total_labor_hours / estimated_sqft_number * 1000, 2) if estimated_sqft_number else None,
        "labor_cost_per_sqft": round(total_labor_cost / estimated_sqft_number, 2) if estimated_sqft_number else None,
        "crew_days_total": round(crew_days_total, 2),
        "labor_bundle_summary": labor_selection_summary.get("labor_bundle_summary"),
        "labor_cap_applied": labor_selection_summary.get("labor_cap_applied"),
        "labor_overlap_adjustment": labor_selection_summary.get("labor_overlap_adjustment"),
        "evidence_rows_exported": len(material_evidence) + len(labor_evidence),
        "evidence_limit": evidence_limit,
        "fast_mode": fast,
        "debug_evidence": debug_evidence,
        "runtime_seconds_by_stage": runtime_seconds_by_stage,
        "source_files_used": "; ".join(str(item) for item in source_files),
        "data_warnings": "; ".join(str(item) for item in data_warnings),
        "output_dir": str(output_dir) if output_dir else "",
    }
    readme_rows = [
        {"field": "Purpose", "value": "Estimator evidence export for reviewing how the field-notes estimator selected scope, materials, labor, history, and rollup values."},
        {"field": "Generated At", "value": generated_at},
        {"field": "Notes", "value": notes or ""},
        {"field": "Workbook Sheet", "value": "run_integrity: run id, input notes hash, stale source-text/cache contamination checks."},
        {"field": "Workbook Sheet", "value": "parsed_scope: parsed fields, dimension math, workbook header, review flags."},
        {"field": "Workbook Sheet", "value": "material_plan/material_evidence: material rows and pricing/calibration evidence."},
        {"field": "Workbook Sheet", "value": "labor_plan/labor_evidence/labor_diagnostics: selected labor rows, candidate evidence, and rejected/relaxed calibration diagnostics."},
        {"field": "Workbook Sheet", "value": "similar_jobs/relationship_rows/rejected_evidence/estimate_rollup: supporting history, profiler outputs, rejected rows, and subtotal checks."},
    ]
    labor_diagnostics = (
        _flatten_diagnostics(recommendation_dict.get("debug") or {}, labor_plan)
        if not fast or debug_evidence
        else [{"message": "Verbose diagnostics skipped in fast evidence mode."}]
    )
    sheets = {
        "README": readme_rows,
        "run_integrity": run_integrity,
        "parsed_scope": parsed_scope,
        "material_plan": material_plan,
        "material_evidence": material_evidence,
        "labor_plan": labor_plan,
        "labor_evidence": labor_evidence,
        "labor_diagnostics": labor_diagnostics,
        "estimate_rollup": _estimate_rollup_rows(recommendation_dict),
    }
    if debug_evidence:
        sheets["similar_jobs"] = _similar_job_rows(recommendation_dict)
        sheets["relationship_rows"] = _relationship_rows(data)
        sheets["rejected_evidence"] = _rejected_evidence_rows(recommendation_dict)
    export = {
        "run_summary": run_summary,
        "sheets": sheets,
    }
    return sanitize_for_export(export, excel=False)


def _sheet_dataframe(rows: list[dict[str, Any]]) -> pd.DataFrame:
    excel_rows = [sanitize_for_export(row, excel=True) for row in rows]
    frame = pd.DataFrame(excel_rows)
    if frame.empty:
        return pd.DataFrame([{"message": "No rows"}])
    for column in frame.columns:
        if frame[column].dtype == "object":
            frame[column] = frame[column].map(lambda value: json.dumps(value, default=str, sort_keys=True) if isinstance(value, (dict, list, tuple, set)) else value)
    return frame


def write_estimator_evidence_export(
    recommendation: Any,
    data: Any = None,
    notes: str | None = None,
    output_dir: Path | str = "output/estimator_evidence",
    base_filename: str | None = None,
    *,
    evidence_limit: int = DEFAULT_EVIDENCE_LIMIT,
    fast: bool = False,
    debug_evidence: bool = False,
) -> dict[str, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    export = build_estimator_evidence_export(
        recommendation,
        data=data,
        notes=notes,
        output_dir=output_path,
        evidence_limit=evidence_limit,
        fast=fast,
        debug_evidence=debug_evidence,
    )
    project_name = export.get("run_summary", {}).get("project_name")
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    base = _safe_filename(base_filename or project_name or "estimator_evidence")
    json_path = output_path / f"{base}_{timestamp}.json"
    xlsx_path = output_path / f"{base}_{timestamp}.xlsx"
    json_export = sanitize_for_export(export, excel=False)
    json_path.write_text(json.dumps(json_export, indent=2, default=str), encoding="utf-8")
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        sheet_names = [sheet for sheet in EVIDENCE_SHEETS if sheet in export.get("sheets", {})]
        for extra_sheet in export.get("sheets", {}):
            if extra_sheet not in sheet_names:
                sheet_names.append(extra_sheet)
        for sheet_name in sheet_names:
            rows = sanitize_for_export(export.get("sheets", {}).get(sheet_name, []), excel=True)
            frame = _sheet_dataframe(rows)
            frame.to_excel(writer, sheet_name=sheet_name[:31], index=False)
            worksheet = writer.sheets[sheet_name[:31]]
            worksheet.freeze_panes = "A2"
            for column_cells in worksheet.columns:
                header = str(column_cells[0].value or "")
                width = min(max(len(header) + 2, 12), 48)
                worksheet.column_dimensions[column_cells[0].column_letter].width = width
    return {"json": json_path, "xlsx": xlsx_path}
