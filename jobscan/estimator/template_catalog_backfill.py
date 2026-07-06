from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from jobscan.env import load_project_env

from .data_loader import load_estimator_data
from .schemas import EstimatorData


DEFAULT_INTELLIGENCE_PATHS = (
    Path("output/roofing_template_intelligence.json"),
    Path("output/insulation_template_intelligence.json"),
)
PRODUCT_KINDS = {"material", "equipment", "travel"}


def _text(value: Any) -> str:
    if value is None:
        return ""
    text_value = str(value).strip()
    return "" if text_value.lower() in {"nan", "none", "null"} else text_value


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


def _safe_float(value: Any) -> float | None:
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        text_value = value.strip()
        if not text_value:
            return None
        try:
            return json.loads(text_value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return text_value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _json_text(value: Any) -> str:
    return json.dumps(_json_value(value), sort_keys=True, default=str, allow_nan=False)


def _stable_id(prefix: str, *parts: Any) -> str:
    payload = "|".join(_text(part) for part in parts)
    return f"{prefix}_{hashlib.sha1(payload.encode('utf-8')).hexdigest()[:20]}"


def load_template_intelligence_files(paths: list[Path] | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in paths or list(DEFAULT_INTELLIGENCE_PATHS):
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["_source_path"] = str(path)
        out.append(payload)
    return out


def selector_map_rows(intelligence_docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for doc in intelligence_docs:
        template_type = _text(doc.get("template_type"))
        template_name = _text(doc.get("template_name") or Path(_text(doc.get("template_path"))).name or template_type)
        for row in doc.get("selector_maps") or []:
            sheet_name = _text(row.get("sheet_name") or "Estimate")
            row_number = _safe_int(row.get("row_number"))
            selector_code = _text(row.get("selector_code"))
            resolved_item = _text(row.get("resolved_item_name") or row.get("resolved_template_option"))
            if not template_type or not template_name or not sheet_name or not selector_code or not resolved_item:
                continue
            rows.append(
                {
                    "selector_map_id": _stable_id("selector", template_type, template_name, sheet_name, row_number, selector_code, resolved_item),
                    "template_type": template_type,
                    "template_name": template_name,
                    "sheet_name": sheet_name,
                    "row_number": row_number or None,
                    "formula_cell": _text(row.get("formula_cell") or row.get("resolved_cell")),
                    "selector_cell": _text(row.get("selector_cell")),
                    "template_bucket": _text(row.get("template_bucket")),
                    "selector_code": selector_code,
                    "resolved_item_name": resolved_item,
                    "formula": _text(row.get("formula")),
                }
            )
    return rows


def row_catalog_rows(intelligence_docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for doc in intelligence_docs:
        template_type = _text(doc.get("template_type"))
        template_name = _text(doc.get("template_name") or Path(_text(doc.get("template_path"))).name or template_type)
        for row in doc.get("workbook_row_catalog") or doc.get("row_catalog") or []:
            sheet_name = _text(row.get("sheet_name") or "Estimate")
            row_number = _safe_int(row.get("row_number"))
            if not template_type or not template_name or not sheet_name or not row_number:
                continue
            rows.append(
                {
                    "template_row_catalog_id": _stable_id("rowcat", template_type, template_name, sheet_name, row_number),
                    "template_type": template_type,
                    "template_name": template_name,
                    "sheet_name": sheet_name,
                    "row_number": row_number,
                    "section": _text(row.get("section")),
                    "template_bucket": _text(row.get("template_bucket")),
                    "line_item_kind": _text(row.get("line_item_kind")),
                    "formula_model": _text(row.get("formula_model")),
                    "cell_roles_json": _json_text(row.get("cell_roles") or row.get("cell_roles_json") or {}),
                }
            )
    return rows


def lookup_table_rows(intelligence_docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for doc in intelligence_docs:
        template_type = _text(doc.get("template_type"))
        template_name = _text(doc.get("template_name") or Path(_text(doc.get("template_path"))).name or template_type)
        for row in doc.get("lookup_tables") or []:
            sheet_name = _text(row.get("sheet_name"))
            table_name = _text(row.get("table_name"))
            row_number = _safe_int(row.get("row_number"))
            lookup_key = _text(row.get("lookup_key"))
            if not template_type or not template_name or not sheet_name or not table_name or not row_number:
                continue
            rows.append(
                {
                    "lookup_table_id": _stable_id("lookup", template_type, template_name, sheet_name, table_name, row_number, lookup_key),
                    "template_type": template_type,
                    "template_name": template_name,
                    "sheet_name": sheet_name,
                    "table_name": table_name,
                    "row_number": row_number,
                    "lookup_key": lookup_key,
                    "headers_json": _json_text(row.get("headers") or {}),
                    "values_json": _json_text(row.get("values") or {}),
                }
            )
    return rows


def formula_model_rows(intelligence_docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for doc in intelligence_docs:
        template_type = _text(doc.get("template_type"))
        template_name = _text(doc.get("template_name") or Path(_text(doc.get("template_path"))).name or template_type)
        for row in doc.get("formula_models") or []:
            sheet_name = _text(row.get("sheet_name"))
            cell_address = _text(row.get("cell") or row.get("cell_address"))
            row_number = _safe_int(row.get("row_number"))
            if not template_type or not template_name or not sheet_name or not cell_address:
                continue
            rows.append(
                {
                    "template_formula_model_id": _stable_id("formula", template_type, template_name, sheet_name, cell_address),
                    "template_type": template_type,
                    "template_name": template_name,
                    "sheet_name": sheet_name,
                    "cell_address": cell_address,
                    "row_number": row_number or None,
                    "template_bucket": _text(row.get("template_bucket")),
                    "formula_kind": _text(row.get("formula_kind")),
                    "formula_model": _text(row.get("formula_model")),
                    "formula": _text(row.get("formula")),
                    "dependencies_json": _json_text(row.get("dependencies") or row.get("formula_dependencies") or []),
                    "selector_map_json": _json_text(row.get("selector_map") or {}),
                }
            )
    return rows


def product_option_rows_from_intelligence(intelligence_docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for doc in intelligence_docs:
        template_type = _text(doc.get("template_type"))
        template_name = _text(doc.get("template_name") or Path(_text(doc.get("template_path"))).name or template_type)
        refs = list(doc.get("pricing_product_references") or [])
        if not refs:
            refs = [
                {
                    "source_type": "selector_map",
                    "source_table": "Estimate",
                    "template_bucket": row.get("template_bucket"),
                    "row_number": row.get("row_number"),
                    "selector_code": row.get("selector_code"),
                    "product_name": row.get("resolved_item_name"),
                    "formula": row.get("formula"),
                }
                for row in doc.get("selector_maps") or []
                if _text(row.get("template_bucket")) and _text(row.get("resolved_item_name"))
            ]
        for row in refs:
            product_name = _text(row.get("product_name") or row.get("resolved_item_name") or row.get("lookup_key"))
            if not template_type or not template_name or not product_name:
                continue
            source_type = _text(row.get("source_type") or "template_intelligence")
            source_table = _text(row.get("source_table") or row.get("sheet_name") or "Estimate")
            row_number = _safe_int(row.get("row_number"))
            selector_code = _text(row.get("selector_code"))
            bucket = _text(row.get("template_bucket"))
            rows.append(
                {
                    "template_product_option_id": _stable_id("product", template_type, template_name, source_type, source_table, bucket, row_number, selector_code, product_name),
                    "template_type": template_type,
                    "template_name": template_name,
                    "source_type": source_type,
                    "source_table": source_table,
                    "template_bucket": bucket,
                    "row_number": row_number or None,
                    "selector_code": selector_code,
                    "product_name": product_name,
                    "source_values_json": _json_text(row),
                }
            )
    return rows


def _labor_row_keys(doc: dict[str, Any]) -> list[dict[str, Any]]:
    keys: list[dict[str, Any]] = []
    for row in doc.get("row_catalog") or doc.get("workbook_row_catalog") or []:
        if _text(row.get("line_item_kind")).lower() == "labor":
            keys.append(
                {
                    "row_number": _safe_int(row.get("row_number")),
                    "labor_package": _text(row.get("template_bucket")),
                }
            )
    return keys


def labor_option_rows_from_intelligence(intelligence_docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for doc in intelligence_docs:
        template_type = _text(doc.get("template_type"))
        template_name = _text(doc.get("template_name") or Path(_text(doc.get("template_path"))).name or template_type)
        labor_rows = _labor_row_keys(doc)
        people_options = doc.get("people_rate_table") or []
        for labor_row in labor_rows:
            if not labor_row.get("row_number") or not labor_row.get("labor_package"):
                continue
            for option in people_options:
                lookup_key = _text(option.get("selector_code"))
                if not template_type or not template_name or not lookup_key:
                    continue
                source_values = {**option, "target_labor_row_number": labor_row["row_number"], "target_labor_package": labor_row["labor_package"]}
                rows.append(
                    {
                        "template_labor_option_id": _stable_id("labor", template_type, template_name, labor_row["row_number"], labor_row["labor_package"], lookup_key),
                        "template_type": template_type,
                        "template_name": template_name,
                        "source_type": "people_daily_rate_selector",
                        "source_table": _text(option.get("table_name") or "people_daily_rate_selector"),
                        "row_number": labor_row["row_number"],
                        "labor_package": labor_row["labor_package"],
                        "lookup_key": lookup_key,
                        "source_values_json": _json_text(source_values),
                    }
                )
        for row in doc.get("people_labor_references") or []:
            lookup_key = _text(row.get("lookup_key") or row.get("selector_code"))
            labor_package = _text(row.get("labor_package"))
            row_number = _safe_int(row.get("row_number"))
            if not template_type or not template_name or not lookup_key or (not labor_package and not row_number):
                continue
            rows.append(
                {
                    "template_labor_option_id": _stable_id("laborref", template_type, template_name, row_number, labor_package, lookup_key),
                    "template_type": template_type,
                    "template_name": template_name,
                    "source_type": _text(row.get("source_type") or "people_labor_reference"),
                    "source_table": _text(row.get("source_table")),
                    "row_number": row_number or None,
                    "labor_package": labor_package,
                    "lookup_key": lookup_key,
                    "source_values_json": _json_text(row),
                }
            )
    return rows


def historical_product_option_rows(data: EstimatorData, *, max_rows: int = 2000) -> list[dict[str, Any]]:
    frame = data.template_rows if isinstance(data.template_rows, pd.DataFrame) else pd.DataFrame()
    required = {"template_type", "template_bucket", "line_item_kind", "selected_item_name"}
    if frame.empty or not required.issubset(frame.columns):
        return []
    rows = frame[
        frame["line_item_kind"].fillna("").astype(str).str.lower().isin(PRODUCT_KINDS)
        & frame["selected_item_name"].fillna("").astype(str).str.strip().ne("")
        & ~frame["template_bucket"].fillna("").astype(str).str.lower().isin({"", "unknown", "none", "nan"})
    ].copy()
    if rows.empty:
        return []
    group_cols = [col for col in ("template_type", "row_number", "template_bucket", "line_item_kind", "selected_item_name", "unit", "selector_code") if col in rows.columns]
    grouped = rows.groupby(group_cols, dropna=False).agg(
        job_count=("job_id", "nunique") if "job_id" in rows.columns else ("selected_item_name", "size"),
        row_count=("selected_item_name", "size"),
        median_unit_price=("unit_price", "median") if "unit_price" in rows.columns else ("selected_item_name", "size"),
    )
    out: list[dict[str, Any]] = []
    for record in grouped.reset_index().sort_values(["job_count", "row_count"], ascending=False).head(max_rows).to_dict(orient="records"):
        template_type = _text(record.get("template_type"))
        bucket = _text(record.get("template_bucket"))
        row_number = _safe_int(record.get("row_number"))
        product_name = _text(record.get("selected_item_name"))
        selector_code = _text(record.get("selector_code"))
        if not template_type or not bucket or not product_name:
            continue
        out.append(
            {
                "template_product_option_id": _stable_id("histproduct", template_type, row_number, bucket, selector_code, product_name),
                "template_type": template_type,
                "template_name": "historical_estimate_rows",
                "source_type": "historical_estimate_rows",
                "source_table": "estimate_template_rows",
                "template_bucket": bucket,
                "row_number": row_number or None,
                "selector_code": selector_code,
                "product_name": product_name,
                "source_values_json": _json_text(record),
            }
        )
    return out


def _dedupe(rows: list[dict[str, Any]], id_field: str) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = _text(row.get(id_field))
        if key:
            out[key] = row
    return list(out.values())


def build_template_catalog_backfill(
    *,
    intelligence_docs: list[dict[str, Any]],
    data: EstimatorData | None = None,
    include_historical_products: bool = True,
    historical_product_limit: int = 2000,
) -> dict[str, list[dict[str, Any]]]:
    product_rows = product_option_rows_from_intelligence(intelligence_docs)
    if include_historical_products and data is not None:
        product_rows.extend(historical_product_option_rows(data, max_rows=historical_product_limit))
    return {
        "template_selector_maps": _dedupe(selector_map_rows(intelligence_docs), "selector_map_id"),
        "template_lookup_tables": _dedupe(lookup_table_rows(intelligence_docs), "lookup_table_id"),
        "template_row_catalog": _dedupe(row_catalog_rows(intelligence_docs), "template_row_catalog_id"),
        "template_formula_models": _dedupe(formula_model_rows(intelligence_docs), "template_formula_model_id"),
        "template_product_options": _dedupe(product_rows, "template_product_option_id"),
        "template_labor_options": _dedupe(labor_option_rows_from_intelligence(intelligence_docs), "template_labor_option_id"),
    }


def write_backfill_preview(rows_by_table: dict[str, list[dict[str, Any]]], out_dir: Path | str) -> dict[str, Path]:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    summary = {table: len(rows) for table, rows in rows_by_table.items()}
    summary_path = out_path / "template_catalog_backfill_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    paths["summary"] = summary_path
    for table, rows in rows_by_table.items():
        path = out_path / f"{table}.csv"
        pd.DataFrame(rows).to_csv(path, index=False)
        paths[table] = path
    return paths


def ensure_catalog_schema(conn: Connection) -> None:
    schema_path = Path("db/template_catalog_schema.sql")
    if schema_path.exists():
        conn.execute(text(schema_path.read_text(encoding="utf-8")))


def _upsert_rows(conn: Connection, table: str, id_field: str, rows: list[dict[str, Any]], columns: list[str], json_columns: set[str] | None = None) -> int:
    if not rows:
        return 0
    json_columns = json_columns or set()
    inserted = 0
    update_columns = [column for column in columns if column != id_field]
    assignments = ", ".join(f"{column} = EXCLUDED.{column}" for column in update_columns)
    insert_columns = ", ".join(columns)
    values_sql = ", ".join(
        f"CAST(:{column} AS jsonb)" if column in json_columns else f":{column}"
        for column in columns
    )
    sql = text(
        f"""
        INSERT INTO {table} ({insert_columns})
        VALUES ({values_sql})
        ON CONFLICT ({id_field}) DO UPDATE SET {assignments}
        """
    )
    for row in rows:
        params = {column: row.get(column) for column in columns}
        for column in json_columns:
            params[column] = _json_text(params.get(column))
        conn.execute(sql, params)
        inserted += 1
    return inserted


def write_catalog_rows(conn: Connection, rows_by_table: dict[str, list[dict[str, Any]]], *, ensure_schema: bool = True) -> dict[str, int]:
    if ensure_schema:
        ensure_catalog_schema(conn)
    counts = {
        "template_selector_maps": _upsert_rows(
            conn,
            "template_selector_maps",
            "selector_map_id",
            rows_by_table.get("template_selector_maps") or [],
            ["selector_map_id", "template_type", "template_name", "sheet_name", "row_number", "formula_cell", "selector_cell", "template_bucket", "selector_code", "resolved_item_name", "formula"],
        ),
        "template_row_catalog": _upsert_rows(
            conn,
            "template_row_catalog",
            "template_row_catalog_id",
            rows_by_table.get("template_row_catalog") or [],
            ["template_row_catalog_id", "template_type", "template_name", "sheet_name", "row_number", "section", "template_bucket", "line_item_kind", "formula_model", "cell_roles_json"],
            json_columns={"cell_roles_json"},
        ),
        "template_lookup_tables": _upsert_rows(
            conn,
            "template_lookup_tables",
            "lookup_table_id",
            rows_by_table.get("template_lookup_tables") or [],
            ["lookup_table_id", "template_type", "template_name", "sheet_name", "table_name", "row_number", "lookup_key", "headers_json", "values_json"],
            json_columns={"headers_json", "values_json"},
        ),
        "template_formula_models": _upsert_rows(
            conn,
            "template_formula_models",
            "template_formula_model_id",
            rows_by_table.get("template_formula_models") or [],
            ["template_formula_model_id", "template_type", "template_name", "sheet_name", "cell_address", "row_number", "template_bucket", "formula_kind", "formula_model", "formula", "dependencies_json", "selector_map_json"],
            json_columns={"dependencies_json", "selector_map_json"},
        ),
        "template_product_options": _upsert_rows(
            conn,
            "template_product_options",
            "template_product_option_id",
            rows_by_table.get("template_product_options") or [],
            ["template_product_option_id", "template_type", "template_name", "source_type", "source_table", "template_bucket", "row_number", "selector_code", "product_name", "source_values_json"],
            json_columns={"source_values_json"},
        ),
        "template_labor_options": _upsert_rows(
            conn,
            "template_labor_options",
            "template_labor_option_id",
            rows_by_table.get("template_labor_options") or [],
            ["template_labor_option_id", "template_type", "template_name", "source_type", "source_table", "row_number", "labor_package", "lookup_key", "source_values_json"],
            json_columns={"source_values_json"},
        ),
    }
    return counts


def print_summary(rows_by_table: dict[str, list[dict[str, Any]]], paths: dict[str, Path] | None = None, write_counts: dict[str, int] | None = None) -> None:
    print("Template catalog backfill")
    for table, rows in rows_by_table.items():
        print(f"  {table}: {len(rows)}")
    if write_counts is not None:
        print("DB upserts:")
        for table, count in write_counts.items():
            print(f"  {table}: {count}")
    if paths:
        print("Wrote preview:")
        for path in paths.values():
            print(f"  {path}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill estimator template catalog option tables.")
    parser.add_argument("--database-url", default=os.getenv("NEON_DATABASE_URL") or os.getenv("DATABASE_URL"))
    parser.add_argument("--intelligence-json", action="append", dest="intelligence_json", help="Template intelligence JSON path. May be repeated.")
    parser.add_argument("--out-dir", default="output/template_catalog_backfill")
    parser.add_argument("--historical-product-limit", type=int, default=2000)
    parser.add_argument("--no-historical-products", action="store_true")
    parser.add_argument("--write-db", action="store_true", help="Write/upsert catalog rows to the database.")
    parser.add_argument("--no-env", action="store_true", help="Do not load .env before resolving database settings.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.no_env:
        load_project_env()
        if not args.database_url:
            args.database_url = os.getenv("NEON_DATABASE_URL") or os.getenv("DATABASE_URL")
    paths = [Path(path) for path in args.intelligence_json] if args.intelligence_json else list(DEFAULT_INTELLIGENCE_PATHS)
    docs = load_template_intelligence_files(paths)
    if not docs:
        raise SystemExit("No template intelligence JSON files found.")
    data = None
    if not args.no_historical_products:
        if not args.database_url:
            raise SystemExit("Set --database-url, NEON_DATABASE_URL, or DATABASE_URL to include historical products.")
        data = load_estimator_data(database_url=args.database_url, prefer_database=True)
    rows_by_table = build_template_catalog_backfill(
        intelligence_docs=docs,
        data=data,
        include_historical_products=not args.no_historical_products,
        historical_product_limit=args.historical_product_limit,
    )
    preview_paths = write_backfill_preview(rows_by_table, args.out_dir)
    write_counts = None
    if args.write_db:
        if not args.database_url:
            raise SystemExit("Set --database-url, NEON_DATABASE_URL, or DATABASE_URL for --write-db.")
        engine = create_engine(args.database_url, future=True)
        with engine.begin() as conn:
            write_counts = write_catalog_rows(conn, rows_by_table)
    print_summary(rows_by_table, preview_paths, write_counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
