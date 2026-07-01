from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

import pandas as pd

from .data_loader import load_estimator_data
from jobscan.env import load_project_env
from .rules import first_nonblank, to_float
from .schemas import EstimatorData


INSULATION_TEXT_SIGNALS = ("insulation", "spray foam", "foam sprayed", "closed-cell", "open-cell", "dc315", "thermal barrier")
AREA_COLUMNS = ("area_sqft", "estimated_sqft", "surface_area_sqft", "gross_area_sqft", "net_area_sqft")
QUANTITY_COLUMNS = ("qty_per_sqft", "total_quantity", "quantity", "estimated_units", "calculated_quantity")
COST_COLUMNS = ("cost_per_sqft", "total_cost", "estimated_cost", "line_total", "extended_cost")
SOURCE_COLUMNS = ("source_file", "file_name", "estimate_file", "document_name", "source_document_id")


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("_", " ").replace("-", " ").split())


def _positive_series(frame: pd.DataFrame, columns: tuple[str, ...]) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=bool)
    mask = pd.Series([False] * len(frame), index=frame.index)
    for column in columns:
        if column in frame.columns:
            values = pd.to_numeric(frame[column], errors="coerce")
            mask = mask | (values.notna() & (values > 0))
    return mask


def _first_existing(frame: pd.DataFrame, columns: tuple[str, ...]) -> str | None:
    for column in columns:
        if column in frame.columns:
            return column
    return None


def _distinct_file_count(frame: pd.DataFrame) -> int:
    column = _first_existing(frame, SOURCE_COLUMNS)
    if column:
        values = frame[column].dropna().astype(str).map(str.strip)
        count = values[values.ne("")].nunique()
        if count:
            return int(count)
    if "job_id" in frame.columns:
        return int(frame["job_id"].dropna().astype(str).nunique())
    return int(len(frame))


def _insulation_rows(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return rows.copy()
    out = rows.copy()
    mask = pd.Series([False] * len(out), index=out.index)
    for column in ("division", "template_type"):
        if column in out.columns:
            mask = mask | out[column].map(_norm).eq("insulation")
    text_columns = [
        column
        for column in ("source_file", "job_name", "selected_item_name", "row_label", "template_bucket", "package", "sheet_name")
        if column in out.columns
    ]
    if text_columns:
        text = out[text_columns].fillna("").astype(str).agg(" ".join, axis=1).str.lower()
        mask = mask | text.map(lambda value: any(signal in value for signal in INSULATION_TEXT_SIGNALS))
    return out[mask].copy()


def _bucket_series(rows: pd.DataFrame) -> pd.Series:
    if rows.empty:
        return pd.Series(dtype=str)
    for column in ("template_bucket", "package", "material_package", "labor_package"):
        if column in rows.columns:
            return rows[column].fillna("").astype(str).replace("", "unknown")
    return pd.Series(["unknown"] * len(rows), index=rows.index)


def _bucket_summary_rows(template_rows: pd.DataFrame, package_rows: pd.DataFrame) -> pd.DataFrame:
    buckets = sorted(
        set(_bucket_series(template_rows).dropna().astype(str))
        | set(_bucket_series(package_rows).dropna().astype(str))
    )
    rows: list[dict[str, Any]] = []
    for bucket in buckets:
        template_bucket_rows = template_rows[_bucket_series(template_rows).astype(str).eq(bucket)].copy()
        package_bucket_rows = package_rows[_bucket_series(package_rows).astype(str).eq(bucket)].copy()
        has_area = _positive_series(template_bucket_rows, AREA_COLUMNS)
        has_quantity = _positive_series(template_bucket_rows, QUANTITY_COLUMNS)
        has_cost = _positive_series(template_bucket_rows, COST_COLUMNS)
        clean_qty = _positive_series(package_bucket_rows, ("qty_per_sqft",))
        if "physical_quantity_valid" in package_bucket_rows.columns:
            physical = package_bucket_rows["physical_quantity_valid"].astype(str).str.lower().isin({"true", "1", "yes"})
            clean_qty = clean_qty & physical
        clean_cost = _positive_series(package_bucket_rows, ("cost_per_sqft",))
        units = []
        if "unit" in template_bucket_rows.columns:
            units = [
                f"{index or '(blank)'} ({count})"
                for index, count in template_bucket_rows["unit"].fillna("").astype(str).replace("", "(blank)").value_counts().head(8).items()
            ]
        item_names = []
        for column in ("selected_item_name", "row_label"):
            if column in template_bucket_rows.columns:
                item_names.extend(
                    f"{index} ({count})"
                    for index, count in template_bucket_rows[column].fillna("").astype(str).replace("", "(blank)").value_counts().head(5).items()
                )
                break
        rejection_counts = {
            "missing_area": int((~has_area).sum()) if not template_bucket_rows.empty else 0,
            "missing_quantity": int((~has_quantity).sum()) if not template_bucket_rows.empty else 0,
            "missing_cost": int((~has_cost).sum()) if not template_bucket_rows.empty else 0,
            "missing_unit": int(template_bucket_rows.get("unit", pd.Series("", index=template_bucket_rows.index)).fillna("").astype(str).str.strip().eq("").sum()) if not template_bucket_rows.empty else 0,
        }
        top_rejections = " | ".join(f"{key}: {value}" for key, value in rejection_counts.items() if value)
        rows.append(
            {
                "template_bucket": bucket,
                "total_files": _distinct_file_count(template_bucket_rows),
                "total_rows": int(len(template_bucket_rows)),
                "rows_with_area": int(has_area.sum()) if not template_bucket_rows.empty else 0,
                "rows_with_quantity": int(has_quantity.sum()) if not template_bucket_rows.empty else 0,
                "rows_with_cost": int(has_cost.sum()) if not template_bucket_rows.empty else 0,
                "clean_qty_per_sqft_rows": int(clean_qty.sum()) if not package_bucket_rows.empty else 0,
                "clean_cost_per_sqft_rows": int(clean_cost.sum()) if not package_bucket_rows.empty else 0,
                "top_units": " | ".join(units),
                "top_item_names": " | ".join(item_names[:8]),
                "top_rejection_reasons": top_rejections,
                "sample_source_files": _sample_source_files(template_bucket_rows),
            }
        )
    return pd.DataFrame(rows)


def _sample_source_files(rows: pd.DataFrame, limit: int = 6) -> str:
    column = _first_existing(rows, SOURCE_COLUMNS)
    if not column:
        return ""
    values = [str(value) for value in rows[column].dropna().astype(str).unique() if str(value).strip()]
    return " | ".join(values[:limit])


def _row_rejection_reason(row: pd.Series) -> str:
    reasons = []
    if not any((to_float(row.get(column)) or 0) > 0 for column in AREA_COLUMNS):
        reasons.append("missing_area")
    if not any((to_float(row.get(column)) or 0) > 0 for column in QUANTITY_COLUMNS):
        reasons.append("missing_quantity")
    if not any((to_float(row.get(column)) or 0) > 0 for column in COST_COLUMNS):
        reasons.append("missing_cost")
    unit = first_nonblank(row.get("unit")).strip().lower()
    if not unit:
        reasons.append("missing_unit")
    elif unit in {"sf", "sqft", "sq ft", "square feet"} and not (to_float(row.get("estimated_units")) or 0) > 0:
        reasons.append("bad_units_area_only")
    return " | ".join(reasons) or "clean_or_review"


def build_insulation_history_diagnostics(data: EstimatorData) -> dict[str, pd.DataFrame]:
    template_rows = _insulation_rows(data.template_rows)
    package_rows = _insulation_rows(data.job_package_summary)
    if not template_rows.empty:
        template_rows = template_rows.copy()
        template_rows["diagnostic_bucket"] = _bucket_series(template_rows)
        template_rows["rejection_reason"] = template_rows.apply(_row_rejection_reason, axis=1)
    if not package_rows.empty:
        package_rows = package_rows.copy()
        package_rows["diagnostic_bucket"] = _bucket_series(package_rows)

    summary = _bucket_summary_rows(template_rows, package_rows)
    foam_rows = template_rows[template_rows["diagnostic_bucket"].astype(str).str.lower().str.contains("foam", na=False)].copy() if not template_rows.empty else pd.DataFrame()
    rejected_foam = foam_rows[foam_rows["rejection_reason"].astype(str).ne("clean_or_review")].copy() if not foam_rows.empty else pd.DataFrame()

    def rows_matching(reason: str) -> pd.DataFrame:
        if template_rows.empty:
            return pd.DataFrame()
        return template_rows[template_rows["rejection_reason"].astype(str).str.contains(reason, na=False)].copy()

    source_example_rows: list[dict[str, Any]] = []
    if not template_rows.empty:
        for bucket, group in template_rows.groupby("diagnostic_bucket", dropna=False):
            source_example_rows.append(
                {
                    "diagnostic_bucket": bucket,
                    "sample_source_files": _sample_source_files(group),
                    "row_count": int(len(group)),
                    "file_count": _distinct_file_count(group),
                }
            )
    source_examples = pd.DataFrame(
        source_example_rows,
        columns=["diagnostic_bucket", "sample_source_files", "row_count", "file_count"],
    )
    return {
        "Summary": summary,
        "Foam Rows": foam_rows,
        "Rejected Foam Rows": rejected_foam,
        "Missing Area": rows_matching("missing_area"),
        "Missing Quantity": rows_matching("missing_quantity"),
        "Missing Unit": rows_matching("missing_unit"),
        "Missing Cost": rows_matching("missing_cost"),
        "Bad Units": rows_matching("bad_units"),
        "Source Examples": source_examples,
    }


def _truncate_for_excel(frame: pd.DataFrame, limit: int = 32000) -> pd.DataFrame:
    if frame.empty:
        return frame
    out = frame.copy()
    for column in out.columns:
        if out[column].dtype == "object":
            out[column] = out[column].map(lambda value: str(value)[:limit] if isinstance(value, (dict, list, tuple, set)) else value)
            out[column] = out[column].map(lambda value: value[:limit] if isinstance(value, str) and len(value) > limit else value)
    return out


def write_insulation_history_diagnostics(data: EstimatorData, output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    sheets = build_insulation_history_diagnostics(data)
    with pd.ExcelWriter(output) as writer:
        for sheet_name, frame in sheets.items():
            _truncate_for_excel(frame).to_excel(writer, sheet_name=sheet_name[:31], index=False)
    return output


def main(argv: list[str] | None = None) -> int:
    load_project_env()
    parser = argparse.ArgumentParser(description="Export insulation historical parsing diagnostics.")
    parser.add_argument("--db-url", default=os.getenv("NEON_DATABASE_URL") or os.getenv("DATABASE_URL"))
    parser.add_argument("--out", default="output/insulation_history_diagnostics.xlsx")
    args = parser.parse_args(argv)
    if not args.db_url:
        raise SystemExit("--db-url or NEON_DATABASE_URL is required")
    data = load_estimator_data(database_url=args.db_url, prefer_database=True)
    output = write_insulation_history_diagnostics(data, args.out)
    print(f"Wrote insulation diagnostics: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
