from __future__ import annotations

import json
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
        included = cost is not None and source != "rejected_historical_quantity_ratio" and not sanity.lower().startswith("blocked")
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


def _material_evidence_rows(recommendation: dict[str, Any], data: Any) -> list[dict[str, Any]]:
    material_rows = _material_plan_rows(recommendation)
    packages = {str(row.get("category") or row.get("package") or row.get("item") or "").lower() for row in material_rows}
    rows: list[dict[str, Any]] = []
    calibration = recommendation.get("historical_calibration") or {}
    material_calibration = calibration.get("material_calibration") if isinstance(calibration, dict) else None
    for row in _records(material_calibration):
        rows.append({**row, "evidence_source_table": "recommendation.historical_calibration.material_calibration", "included_as_evidence": True})
    for table_name in ("template_rows", "job_package_summary", "pricing_catalog", "pricing"):
        frame = getattr(data, table_name, pd.DataFrame()) if data is not None else pd.DataFrame()
        for row in _frame_records(frame, source_table=table_name):
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
    return rows


def _labor_evidence_rows(recommendation: dict[str, Any], data: Any) -> list[dict[str, Any]]:
    labor_rows = _labor_plan_rows(recommendation)
    tasks = {str(row.get("task") or row.get("labor_package") or "") for row in labor_rows}
    diagnostics = recommendation.get("debug", {}).get("labor_calibration", {}) if isinstance(recommendation.get("debug"), dict) else {}
    task_details = diagnostics.get("tasks", {}) if isinstance(diagnostics, dict) else {}
    rows: list[dict[str, Any]] = []
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
    for table_name in ("relationship_labor_rates", "job_package_summary", "template_rows"):
        frame = getattr(data, table_name, pd.DataFrame()) if data is not None else pd.DataFrame()
        for row in _frame_records(frame, source_table=table_name):
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
    return rows


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


def build_estimator_evidence_export(
    recommendation: Any,
    data: Any = None,
    notes: str | None = None,
    output_dir: Path | str | None = None,
) -> dict[str, Any]:
    recommendation_dict = _dict_from_object(recommendation)
    parsed_fields = recommendation_dict.get("parsed_fields") or {}
    header = (recommendation_dict.get("draft_workbook_inputs") or {}).get("header") or {}
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    source_files = getattr(data, "source_files_used", []) if data is not None else []
    data_warnings = getattr(data, "warnings", []) if data is not None else []
    parsed_scope = [{**parsed_fields, **{f"header_{key}": value for key, value in header.items()}, "review_flags": "; ".join(recommendation_dict.get("review_flags") or [])}]
    material_plan = _material_plan_rows(recommendation_dict)
    labor_plan = _labor_plan_rows(recommendation_dict)
    run_summary = {
        "generated_at": generated_at,
        "notes": notes or "",
        "project_name": header.get("C2_job_name") or parsed_fields.get("project_type") or "Estimator recommendation",
        "estimated_sqft": parsed_fields.get("estimated_sqft") or parsed_fields.get("surface_area_sqft") or header.get("C12_estimated_sqft"),
        "estimate_low": recommendation_dict.get("estimate_low"),
        "estimate_target": recommendation_dict.get("estimate_target"),
        "estimate_high": recommendation_dict.get("estimate_high"),
        "human_review_required": recommendation_dict.get("human_review_required"),
        "material_rows": len(material_plan),
        "labor_rows": len(labor_plan),
        "similar_jobs": len(_records(recommendation_dict.get("similar_examples"))),
        "review_flags": len(recommendation_dict.get("review_flags") or []),
        "source_files_used": "; ".join(str(item) for item in source_files),
        "data_warnings": "; ".join(str(item) for item in data_warnings),
        "output_dir": str(output_dir) if output_dir else "",
    }
    readme_rows = [
        {"field": "Purpose", "value": "Estimator evidence export for reviewing how the field-notes estimator selected scope, materials, labor, history, and rollup values."},
        {"field": "Generated At", "value": generated_at},
        {"field": "Notes", "value": notes or ""},
        {"field": "Workbook Sheet", "value": "parsed_scope: parsed fields, dimension math, workbook header, review flags."},
        {"field": "Workbook Sheet", "value": "material_plan/material_evidence: material rows and pricing/calibration evidence."},
        {"field": "Workbook Sheet", "value": "labor_plan/labor_evidence/labor_diagnostics: selected labor rows, candidate evidence, and rejected/relaxed calibration diagnostics."},
        {"field": "Workbook Sheet", "value": "similar_jobs/relationship_rows/rejected_evidence/estimate_rollup: supporting history, profiler outputs, rejected rows, and subtotal checks."},
    ]
    export = {
        "run_summary": run_summary,
        "sheets": {
            "README": readme_rows,
            "parsed_scope": parsed_scope,
            "material_plan": material_plan,
            "material_evidence": _material_evidence_rows(recommendation_dict, data),
            "labor_plan": labor_plan,
            "labor_evidence": _labor_evidence_rows(recommendation_dict, data),
            "labor_diagnostics": _flatten_diagnostics(recommendation_dict.get("debug") or {}, labor_plan),
            "similar_jobs": _similar_job_rows(recommendation_dict),
            "relationship_rows": _relationship_rows(data),
            "rejected_evidence": _rejected_evidence_rows(recommendation_dict),
            "estimate_rollup": _estimate_rollup_rows(recommendation_dict),
        },
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
) -> dict[str, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    export = build_estimator_evidence_export(recommendation, data=data, notes=notes, output_dir=output_path)
    project_name = export.get("run_summary", {}).get("project_name")
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    base = _safe_filename(base_filename or project_name or "estimator_evidence")
    json_path = output_path / f"{base}_{timestamp}.json"
    xlsx_path = output_path / f"{base}_{timestamp}.xlsx"
    json_export = sanitize_for_export(export, excel=False)
    json_path.write_text(json.dumps(json_export, indent=2, default=str), encoding="utf-8")
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        for sheet_name in EVIDENCE_SHEETS:
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
