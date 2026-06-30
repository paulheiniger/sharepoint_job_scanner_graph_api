from __future__ import annotations

import json
import shutil
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .evidence_export import sanitize_for_export
from .workbench import recalculate_workbench_tables, summarize_workbench_totals

DEFAULT_WORKBENCH_EXPORT_DIR = Path("output/estimator_workbench_exports")
EXCEL_CELL_LIMIT = 32000


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


def _safe_filename_part(value: Any, fallback: str = "workbench") -> str:
    text = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(value or "").strip())
    text = "_".join(part for part in text.split("_") if part)
    return (text or fallback)[:80]


def _truncate_excel_text(value: str) -> str:
    if len(value) <= EXCEL_CELL_LIMIT:
        return value
    suffix = "... [truncated for Excel]"
    return value[: max(EXCEL_CELL_LIMIT - len(suffix), 0)] + suffix


def _excel_value(value: Any) -> Any:
    value = sanitize_for_export(value, excel=True)
    if isinstance(value, (dict, list, tuple, set)):
        return _truncate_excel_text(json.dumps(value, sort_keys=True, default=str))
    if isinstance(value, str):
        return _truncate_excel_text(value)
    return value


def _json_payload(value: Any) -> Any:
    return sanitize_for_export(value, excel=False)


def _table_rows(records: Any) -> list[dict[str, Any]]:
    if records is None:
        return []
    if isinstance(records, pd.DataFrame):
        records = records.to_dict(orient="records")
    if isinstance(records, dict):
        return [{"field": key, "value": value} for key, value in records.items()]
    if isinstance(records, list):
        rows = []
        for item in records:
            if isinstance(item, dict):
                rows.append(item)
            else:
                rows.append({"value": item})
        return rows
    return [{"value": records}]


def _excel_frame(records: Any) -> pd.DataFrame:
    rows = _table_rows(records)
    cleaned = [{str(key): _excel_value(value) for key, value in row.items()} for row in rows]
    return pd.DataFrame(cleaned)


def _write_xlsx(path: Path, sheets: dict[str, Any]) -> None:
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet_name, rows in sheets.items():
            frame = _excel_frame(rows)
            safe_sheet = sheet_name[:31]
            frame.to_excel(writer, sheet_name=safe_sheet, index=False)


def _compact_rows(rows: list[dict[str, Any]], columns: list[str]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for row in rows or []:
        compact.append({column: row.get(column) for column in columns if column in row})
    return compact


def build_workbench_review_payloads(
    *,
    workbench: dict[str, Any],
    input_notes: str | None = None,
    runtime: dict[str, Any] | None = None,
    run_id: str | None = None,
    timestamp: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Build JSON/XLSX payloads for an estimator workbench review package."""

    recalculated = recalculate_workbench_tables(workbench)
    resolved_run_id = run_id or str(recalculated.get("estimate_id") or "workbench")
    resolved_timestamp = timestamp or datetime.now(UTC).isoformat()
    totals = summarize_workbench_totals(recalculated)
    materials = list(recalculated.get("materials") or [])
    labor = list(recalculated.get("labor") or [])
    adders = list(recalculated.get("adders") or [])

    summary = {
        "run_id": resolved_run_id,
        "timestamp": resolved_timestamp,
        "input_notes": input_notes or "",
        "parsed_scope": recalculated.get("scope") or {},
        "historical_filters": recalculated.get("historical_filters") or {},
        "materials_final": _compact_rows(
            materials,
            [
                "include",
                "workbook_row",
                "package",
                "item_name",
                "suggested_by_notes_rules",
                "editable_basis_sqft",
                "editable_qty_per_sqft",
                "calculated_quantity",
                "unit",
                "current_unit_price",
                "estimated_cost",
                "evidence_count",
                "confidence",
                "explanation",
            ],
        ),
        "labor_final": _compact_rows(
            labor,
            [
                "include",
                "workbook_row",
                "labor_package",
                "suggested_by_notes_rules",
                "editable_hours_per_1000_sqft",
                "calculated_hours",
                "crew_size",
                "labor_rate",
                "estimated_cost",
                "evidence_count",
                "confidence",
                "explanation",
            ],
        ),
        "adders_final": _compact_rows(
            adders,
            [
                "include",
                "workbook_row",
                "adder",
                "editable_value",
                "estimated_cost",
                "evidence_count",
                "confidence",
                "notes",
            ],
        ),
        "totals": totals,
        "review_flags": recalculated.get("review_flags") or [],
        "runtime": runtime or recalculated.get("runtime") or {},
    }

    debug = {
        "run_id": resolved_run_id,
        "timestamp": resolved_timestamp,
        "input_notes": input_notes or "",
        "workbench": recalculated,
        "materials_diagnostics": materials,
        "labor_diagnostics": labor,
        "adders_diagnostics": adders,
        "totals": totals,
        "warnings": recalculated.get("review_flags") or [],
    }

    workbook_sheets = {
        "Parsed Scope": summary["parsed_scope"],
        "Historical Filters": summary["historical_filters"],
        "Materials": summary["materials_final"],
        "Labor": summary["labor_final"],
        "Adders": summary["adders_final"],
        "Totals": summary["totals"],
        "Review Flags": [{"review_flag": flag} for flag in summary["review_flags"]] or [{"review_flag": ""}],
        "Similar Jobs": recalculated.get("similar_jobs") or [],
    }

    return _json_payload(summary), _json_payload(debug), workbook_sheets


def export_workbench_review_package(
    *,
    workbench: dict[str, Any],
    input_notes: str | None = None,
    output_dir: str | Path = DEFAULT_WORKBENCH_EXPORT_DIR,
    workbook_path: str | Path | None = None,
    workbook_export_error: str | None = None,
    runtime: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> Path:
    """Create a timestamped Estimator Workbench review ZIP package."""

    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    resolved_run_id = run_id or str(workbench.get("estimate_id") or "workbench")
    stamp = _timestamp()
    folder_name = f"{stamp}_{_safe_filename_part(resolved_run_id)}"
    package_dir = output_root / folder_name
    package_dir.mkdir(parents=True, exist_ok=True)

    summary, debug, workbook_sheets = build_workbench_review_payloads(
        workbench=workbench,
        input_notes=input_notes,
        runtime=runtime,
        run_id=resolved_run_id,
        timestamp=datetime.now(UTC).isoformat(),
    )

    (package_dir / "workbench_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (package_dir / "workbench_debug.json").write_text(json.dumps(debug, indent=2, sort_keys=True), encoding="utf-8")
    (package_dir / "estimator_input.txt").write_text(input_notes or "", encoding="utf-8")
    _write_xlsx(package_dir / "workbench_summary.xlsx", workbook_sheets)

    if workbook_path:
        source = Path(workbook_path)
        if source.exists():
            shutil.copyfile(source, package_dir / "exported_workbook.xlsx")
        else:
            (package_dir / "workbook_export_error.txt").write_text(f"Workbook path does not exist: {source}", encoding="utf-8")
    else:
        (package_dir / "workbook_export_error.txt").write_text(
            workbook_export_error or "Estimate workbook has not been generated in this session.",
            encoding="utf-8",
        )

    zip_path = output_root / f"{folder_name}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(package_dir.iterdir()):
            archive.write(path, arcname=path.name)
    return zip_path
