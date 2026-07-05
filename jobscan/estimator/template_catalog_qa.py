from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

from jobscan.env import load_project_env

from .data_loader import load_estimator_data
from .schemas import EstimatorData


ACTIONABLE_KINDS = {"material", "labor", "equipment", "travel"}
PRODUCT_KINDS = {"material", "equipment", "travel"}


def _text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def _norm(value: Any) -> str:
    return " ".join(_text(value).lower().replace("_", " ").replace("-", " ").split())


def _records(df: pd.DataFrame | None) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    return df.to_dict(orient="records")


def _safe_int(value: Any) -> int:
    try:
        if pd.isna(value):
            return 0
    except (TypeError, ValueError):
        pass
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not _text(value):
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _table_count(data: EstimatorData, attr: str) -> int:
    frame = getattr(data, attr, pd.DataFrame())
    return len(frame) if isinstance(frame, pd.DataFrame) else 0


def _row_numbers_from_value(value: Any) -> set[int]:
    text = _text(value)
    if not text:
        return set()
    out: set[int] = set()
    for token in text.replace(",", " ").replace("-", " ").replace("/", " ").split():
        number = _safe_int(token)
        if number:
            out.add(number)
    number = _safe_int(value)
    if number:
        out.add(number)
    return out


def _bucket_values(row: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for key in ("template_bucket", "package_key", "category", "labor_package", "bucket"):
        value = _norm(row.get(key))
        if value:
            values.add(value)
    return values


def _template_matches(row: dict[str, Any], template_type: str) -> bool:
    candidate = _norm(row.get("template_type"))
    return not candidate or candidate == _norm(template_type)


def _option_matches_row(option: dict[str, Any], expected: dict[str, Any]) -> bool:
    if not _template_matches(option, _text(expected.get("template_type"))):
        return False
    option_row = _safe_int(option.get("row_number"))
    expected_rows = _row_numbers_from_value(expected.get("row_number") or expected.get("workbook_row"))
    if option_row and option_row in expected_rows:
        return True
    return bool(_bucket_values(option) & _bucket_values(expected))


def _requires_selector_options(row: dict[str, Any]) -> bool:
    formula_model = _norm(row.get("formula_model"))
    if "selector" in formula_model:
        return True
    roles = _json_object(row.get("cell_roles_json") or row.get("cell_roles"))
    role_values = {_norm(value) for value in roles.values()}
    return "selector code" in role_values or "selector" in role_values


def _catalog_missing_reasons(table_name: str, row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if not _text(row.get("template_type")):
        reasons.append("missing_template_type")
    has_row_or_bucket = bool(_safe_int(row.get("row_number")) or _bucket_values(row))
    if table_name in {"template_selector_maps", "template_product_options"} and not has_row_or_bucket:
        reasons.append("missing_row_number_or_template_bucket")
    if table_name == "template_selector_maps":
        if not _text(row.get("selector_code")):
            reasons.append("missing_selector_code")
        if not _text(row.get("resolved_item_name") or row.get("resolved_template_option")):
            reasons.append("missing_resolved_item_name")
    if table_name == "template_product_options":
        values = _json_object(row.get("source_values_json"))
        if not _text(row.get("product_name") or row.get("item_name") or values.get("product_name") or values.get("item_name")):
            reasons.append("missing_product_name")
    if table_name == "template_labor_options":
        values = _json_object(row.get("source_values_json"))
        if not _safe_int(row.get("row_number")) and not _text(row.get("labor_package")) and _norm(row.get("source_type")) != "people daily rate selector":
            reasons.append("missing_row_number_or_labor_package")
        if not _text(row.get("lookup_key") or row.get("selector_code") or values.get("lookup_key") or values.get("selector_code") or values.get("crew_size")):
            reasons.append("missing_lookup_key_or_selector")
    return reasons


def catalog_missing_field_rows(data: EstimatorData) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for table_name in ("template_selector_maps", "template_product_options", "template_labor_options"):
        for idx, row in enumerate(_records(getattr(data, table_name, pd.DataFrame()))):
            reasons = _catalog_missing_reasons(table_name, row)
            if not reasons:
                continue
            rows.append(
                {
                    "table_name": table_name,
                    "row_index": idx,
                    "template_type": row.get("template_type"),
                    "row_number": row.get("row_number"),
                    "template_bucket": row.get("template_bucket") or row.get("labor_package"),
                    "selector_code": row.get("selector_code") or row.get("lookup_key"),
                    "name": row.get("resolved_item_name") or row.get("product_name") or row.get("lookup_key"),
                    "missing_reasons": "; ".join(reasons),
                }
            )
    return rows


def expected_decision_rows(data: EstimatorData) -> list[dict[str, Any]]:
    catalog = getattr(data, "template_row_catalog", pd.DataFrame())
    if isinstance(catalog, pd.DataFrame) and not catalog.empty:
        frame = catalog.copy()
        kind_col = "line_item_kind" if "line_item_kind" in frame.columns else ""
        if kind_col:
            frame = frame[frame[kind_col].fillna("").astype(str).str.lower().isin(ACTIONABLE_KINDS)]
        return frame.to_dict(orient="records")

    rows = getattr(data, "template_rows", pd.DataFrame())
    if not isinstance(rows, pd.DataFrame) or rows.empty:
        return []
    required = [col for col in ("template_type", "row_number", "template_bucket", "line_item_kind") if col in rows.columns]
    if not required:
        return []
    frame = rows.copy()
    if "line_item_kind" in frame.columns:
        frame = frame[frame["line_item_kind"].fillna("").astype(str).str.lower().isin(ACTIONABLE_KINDS)]
    group_cols = [col for col in ("template_type", "row_number", "template_bucket", "line_item_kind", "row_label") if col in frame.columns]
    if not group_cols:
        return []
    return frame[group_cols].drop_duplicates().to_dict(orient="records")


def _historical_option_count(data: EstimatorData, expected: dict[str, Any]) -> int:
    rows = getattr(data, "template_rows", pd.DataFrame())
    if not isinstance(rows, pd.DataFrame) or rows.empty:
        return 0
    frame = rows.copy()
    if "template_type" in frame.columns:
        frame = frame[frame["template_type"].fillna("").astype(str).str.lower().eq(_text(expected.get("template_type")).lower())]
    expected_row = _safe_int(expected.get("row_number"))
    if expected_row and "row_number" in frame.columns:
        by_row = frame[pd.to_numeric(frame["row_number"], errors="coerce").fillna(0).astype(int).eq(expected_row)]
        if not by_row.empty:
            frame = by_row
    bucket = _text(expected.get("template_bucket"))
    if bucket and "template_bucket" in frame.columns:
        by_bucket = frame[frame["template_bucket"].fillna("").astype(str).str.lower().eq(bucket.lower())]
        if not by_bucket.empty:
            frame = by_bucket
    if "selected_item_name" not in frame.columns:
        return 0
    return int(frame["selected_item_name"].fillna("").astype(str).str.strip().replace("", pd.NA).dropna().nunique())


def row_option_coverage_rows(data: EstimatorData) -> list[dict[str, Any]]:
    selector_rows = _records(getattr(data, "template_selector_maps", pd.DataFrame()))
    product_rows = _records(getattr(data, "template_product_options", pd.DataFrame()))
    labor_rows = _records(getattr(data, "template_labor_options", pd.DataFrame()))
    out: list[dict[str, Any]] = []
    for expected in expected_decision_rows(data):
        line_kind = _text(expected.get("line_item_kind")).lower()
        selector_count = sum(1 for row in selector_rows if _option_matches_row(row, expected))
        product_count = sum(1 for row in product_rows if _option_matches_row(row, expected))
        labor_count = sum(1 for row in labor_rows if _option_matches_row(row, expected))
        historical_count = _historical_option_count(data, expected)
        reasons: list[str] = []
        if line_kind in PRODUCT_KINDS and not product_count and not historical_count:
            reasons.append("no_product_options_or_historical_items")
        if line_kind == "labor" and not labor_count:
            reasons.append("no_labor_options")
        if not selector_count and line_kind in PRODUCT_KINDS and _requires_selector_options(expected):
            reasons.append("no_selector_options")
        out.append(
            {
                "template_type": expected.get("template_type"),
                "row_number": expected.get("row_number"),
                "template_bucket": expected.get("template_bucket"),
                "line_item_kind": expected.get("line_item_kind"),
                "row_label": expected.get("row_label"),
                "selector_option_count": selector_count,
                "product_option_count": product_count,
                "labor_option_count": labor_count,
                "historical_item_count": historical_count,
                "coverage_status": "missing" if reasons else "ok",
                "missing_reasons": "; ".join(reasons),
            }
        )
    return out


def unknown_template_row_summary(data: EstimatorData, *, limit: int = 100) -> list[dict[str, Any]]:
    rows = getattr(data, "template_rows", pd.DataFrame())
    if not isinstance(rows, pd.DataFrame) or rows.empty or "template_bucket" not in rows.columns:
        return []
    frame = rows[rows["template_bucket"].fillna("").astype(str).str.lower().eq("unknown")].copy()
    if frame.empty:
        return []
    group_cols = [col for col in ("template_type", "sheet_name", "row_number", "row_label", "selected_item_name", "line_item_kind") if col in frame.columns]
    if not group_cols:
        return []
    summary = frame.groupby(group_cols, dropna=False).size().reset_index(name="row_count")
    return summary.sort_values("row_count", ascending=False).head(limit).to_dict(orient="records")


def unknown_template_row_stats(data: EstimatorData) -> dict[str, int]:
    rows = getattr(data, "template_rows", pd.DataFrame())
    if not isinstance(rows, pd.DataFrame) or rows.empty or "template_bucket" not in rows.columns:
        return {"unknown_row_count": 0, "unknown_group_count": 0}
    frame = rows[rows["template_bucket"].fillna("").astype(str).str.lower().eq("unknown")].copy()
    if frame.empty:
        return {"unknown_row_count": 0, "unknown_group_count": 0}
    group_cols = [col for col in ("template_type", "sheet_name", "row_number", "row_label", "selected_item_name", "line_item_kind") if col in frame.columns]
    group_count = int(frame[group_cols].drop_duplicates().shape[0]) if group_cols else 0
    return {"unknown_row_count": int(len(frame)), "unknown_group_count": group_count}


def historical_product_candidate_rows(data: EstimatorData, *, limit: int = 500) -> list[dict[str, Any]]:
    rows = getattr(data, "template_rows", pd.DataFrame())
    if not isinstance(rows, pd.DataFrame) or rows.empty:
        return []
    required = {"template_type", "template_bucket", "line_item_kind", "selected_item_name"}
    if not required.issubset(rows.columns):
        return []
    frame = rows[
        rows["line_item_kind"].fillna("").astype(str).str.lower().isin(PRODUCT_KINDS)
        & rows["selected_item_name"].fillna("").astype(str).str.strip().ne("")
    ].copy()
    if frame.empty:
        return []
    group_cols = [col for col in ("template_type", "row_number", "template_bucket", "line_item_kind", "selected_item_name", "unit") if col in frame.columns]
    agg = frame.groupby(group_cols, dropna=False).agg(
        job_count=("job_id", "nunique") if "job_id" in frame.columns else ("selected_item_name", "size"),
        row_count=("selected_item_name", "size"),
        median_unit_price=("unit_price", "median") if "unit_price" in frame.columns else ("selected_item_name", "size"),
    )
    return agg.reset_index().sort_values(["job_count", "row_count"], ascending=False).head(limit).to_dict(orient="records")


def build_template_catalog_qa_report(data: EstimatorData, *, limit: int = 100) -> dict[str, Any]:
    coverage = row_option_coverage_rows(data)
    missing_catalog = catalog_missing_field_rows(data)
    unknown_rows = unknown_template_row_summary(data, limit=limit)
    unknown_stats = unknown_template_row_stats(data)
    historical_candidates = historical_product_candidate_rows(data, limit=max(limit, 500))
    return {
        "table_counts": {
            "template_rows": _table_count(data, "template_rows"),
            "template_row_catalog": _table_count(data, "template_row_catalog"),
            "template_selector_maps": _table_count(data, "template_selector_maps"),
            "template_product_options": _table_count(data, "template_product_options"),
            "template_labor_options": _table_count(data, "template_labor_options"),
        },
        "coverage_summary": dict(Counter(row.get("coverage_status") for row in coverage)),
        "missing_catalog_field_summary": dict(Counter(row.get("table_name") for row in missing_catalog)),
        **unknown_stats,
        "row_option_coverage": coverage,
        "missing_catalog_fields": missing_catalog,
        "unknown_template_rows": unknown_rows,
        "historical_product_candidates": historical_candidates,
    }


def write_template_catalog_qa_report(report: dict[str, Any], out_dir: Path | str) -> dict[str, Path]:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    paths = {
        "summary_json": out_path / "template_catalog_qa_summary.json",
        "row_option_coverage_csv": out_path / "row_option_coverage.csv",
        "missing_catalog_fields_csv": out_path / "missing_catalog_fields.csv",
        "unknown_template_rows_csv": out_path / "unknown_template_rows.csv",
        "historical_product_candidates_csv": out_path / "historical_product_candidates.csv",
    }
    paths["summary_json"].write_text(
        json.dumps(
            {
                "table_counts": report.get("table_counts"),
                "coverage_summary": report.get("coverage_summary"),
                "missing_catalog_field_summary": report.get("missing_catalog_field_summary"),
                "unknown_row_count": report.get("unknown_row_count"),
                "unknown_group_count": report.get("unknown_group_count"),
            },
            indent=2,
            sort_keys=True,
            default=str,
        ),
        encoding="utf-8",
    )
    pd.DataFrame(report.get("row_option_coverage") or []).to_csv(paths["row_option_coverage_csv"], index=False)
    pd.DataFrame(report.get("missing_catalog_fields") or []).to_csv(paths["missing_catalog_fields_csv"], index=False)
    pd.DataFrame(report.get("unknown_template_rows") or []).to_csv(paths["unknown_template_rows_csv"], index=False)
    pd.DataFrame(report.get("historical_product_candidates") or []).to_csv(paths["historical_product_candidates_csv"], index=False)
    return paths


def print_summary(report: dict[str, Any], paths: dict[str, Path] | None = None) -> None:
    print("Template catalog QA")
    print("Table counts:")
    for name, count in (report.get("table_counts") or {}).items():
        print(f"  {name}: {count}")
    print("Coverage:")
    for name, count in (report.get("coverage_summary") or {}).items():
        print(f"  {name}: {count}")
    print("Missing catalog fields:")
    for name, count in (report.get("missing_catalog_field_summary") or {}).items():
        print(f"  {name}: {count}")
    print(f"Unknown template rows: {report.get('unknown_row_count', 0)}")
    print(f"Unknown template row groups: {report.get('unknown_group_count', 0)}")
    if paths:
        print("Wrote:")
        for path in paths.values():
            print(f"  {path}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit estimator template catalog option coverage.")
    parser.add_argument("--database-url", default=os.getenv("NEON_DATABASE_URL") or os.getenv("DATABASE_URL"))
    parser.add_argument("--out-dir", default="output/template_catalog_qa")
    parser.add_argument("--limit", type=int, default=100, help="Top unknown/candidate rows to include.")
    parser.add_argument("--no-env", action="store_true", help="Do not load .env before resolving database settings.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.no_env:
        load_project_env()
        if not args.database_url:
            args.database_url = os.getenv("NEON_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not args.database_url:
        raise SystemExit("Set --database-url, NEON_DATABASE_URL, or DATABASE_URL.")
    data = load_estimator_data(database_url=args.database_url, prefer_database=True)
    report = build_template_catalog_qa_report(data, limit=args.limit)
    paths = write_template_catalog_qa_report(report, args.out_dir)
    print_summary(report, paths)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
