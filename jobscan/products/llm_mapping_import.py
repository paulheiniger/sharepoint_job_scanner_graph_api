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


DEFAULT_MAPPING_PATH = Path("/Users/paulheiniger/Downloads/spraytec_product_mapping_first_pass.jsonl")
DEFAULT_LLM_INPUT_DIR = Path("output/llm_product_mapping_inputs")
DEFAULT_OUT_DIR = Path("output/llm_product_mapping_import")


def build_approved_mapping_import(
    *,
    mapping_path: str | Path = DEFAULT_MAPPING_PATH,
    llm_input_dir: str | Path = DEFAULT_LLM_INPUT_DIR,
    approved_only: bool = True,
) -> dict[str, list[dict[str, Any]]]:
    llm_dir = Path(llm_input_dir)
    mapping_rows = read_mapping_rows(Path(mapping_path))
    if approved_only:
        mapping_rows = [row for row in mapping_rows if clean(row.get("mapping_status")).lower() == "approved"]
    template_options = read_csv_index(llm_dir / "template_options_for_llm.csv", "template_option_key")
    product_knowledge = read_csv_index(llm_dir / "product_knowledge_for_llm.csv", "knowledge_key")
    pricing_candidates = read_csv_index(llm_dir / "pricing_candidates_for_llm.csv", "pricing_candidate_key")

    product_catalog: dict[str, dict[str, Any]] = {}
    product_aliases: dict[str, dict[str, Any]] = {}
    product_decision_links: dict[str, dict[str, Any]] = {}
    template_product_links: dict[str, dict[str, Any]] = {}
    template_pricing_links: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, Any]] = []

    for mapping in mapping_rows:
        option_key = clean(mapping.get("template_option_key"))
        knowledge_key = clean(mapping.get("knowledge_key"))
        template = template_options.get(option_key) or mapping
        knowledge = product_knowledge.get(knowledge_key)
        if not template:
            skipped.append({**mapping, "skip_reason": "template_option_key_not_found"})
            continue
        if not knowledge:
            skipped.append({**mapping, "skip_reason": "knowledge_key_not_found"})
            continue
        if clean(mapping.get("mapping_status")).lower() != "approved":
            skipped.append({**mapping, "skip_reason": "not_approved"})
            continue

        product_id = clean(knowledge.get("knowledge_key"))
        product_catalog[product_id] = product_catalog_row(product_id, knowledge, mapping, template)
        for decision_id in decision_ids_from_knowledge(knowledge):
            link = product_decision_link_row(product_id, decision_id, mapping)
            product_decision_links[link["link_id"]] = link

        alias_values = [
            mapping.get("canonical_template_option"),
            template.get("raw_template_option"),
            template.get("normalized_template_option"),
            *parse_suggested_aliases(mapping.get("suggested_aliases")),
        ]
        for alias in alias_values:
            row = product_alias_row(product_id, alias, mapping)
            if row:
                product_aliases[row["alias_id"]] = row

        for option_id in source_option_ids(template):
            link = template_product_link_row(option_id, product_id, template, mapping)
            template_product_links[link["link_id"]] = link
            pricing_key = clean(mapping.get("pricing_candidate_key"))
            if pricing_key:
                pricing = pricing_candidates.get(pricing_key)
                if pricing:
                    pricing_link = template_pricing_link_row(option_id, pricing_key, pricing, template, mapping)
                    template_pricing_links[pricing_link["link_id"]] = pricing_link
                else:
                    skipped.append({**mapping, "skip_reason": "pricing_candidate_key_not_found", "template_product_option_id": option_id})

    return {
        "product_catalog": sorted(product_catalog.values(), key=lambda row: row["product_id"]),
        "product_aliases": sorted(product_aliases.values(), key=lambda row: row["alias_id"]),
        "product_decision_links": sorted(product_decision_links.values(), key=lambda row: row["link_id"]),
        "template_product_option_links": sorted(template_product_links.values(), key=lambda row: row["link_id"]),
        "template_pricing_option_links": sorted(template_pricing_links.values(), key=lambda row: row["link_id"]),
        "skipped": skipped,
    }


def write_import_preview(rows_by_table: dict[str, list[dict[str, Any]]], out_dir: str | Path = DEFAULT_OUT_DIR) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    summary = {table: len(rows) for table, rows in rows_by_table.items()}
    summary_path = out / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    paths["summary"] = summary_path
    for table, rows in rows_by_table.items():
        path = out / f"{table}.csv"
        pd.DataFrame(rows).to_csv(path, index=False)
        paths[table] = path
    return paths


def write_approved_mapping_rows(conn: Connection, rows_by_table: dict[str, list[dict[str, Any]]], *, ensure_schema: bool = True) -> dict[str, int]:
    if ensure_schema:
        schema_path = Path("db/product_knowledge_schema.sql")
        if schema_path.exists():
            for statement in schema_statements(schema_path.read_text(encoding="utf-8")):
                conn.execute(text(statement))
    counts = {
        "product_catalog": upsert_rows(
            conn,
            "product_catalog",
            "product_id",
            rows_by_table.get("product_catalog") or [],
            ["product_id", "manufacturer", "product_family", "product_name", "sku", "category", "subcategory", "unit", "aliases", "active", "extraction_method", "extraction_warnings"],
            json_columns={"aliases", "extraction_warnings"},
        ),
        "product_aliases": upsert_rows(
            conn,
            "product_aliases",
            "alias_id",
            rows_by_table.get("product_aliases") or [],
            ["alias_id", "product_id", "alias", "alias_type", "confidence"],
        ),
        "product_decision_links": upsert_rows(
            conn,
            "product_decision_links",
            "link_id",
            rows_by_table.get("product_decision_links") or [],
            ["link_id", "product_id", "decision_id", "influence_type", "confidence", "reason"],
        ),
        "template_product_option_links": upsert_rows(
            conn,
            "template_product_option_links",
            "link_id",
            rows_by_table.get("template_product_option_links") or [],
            ["link_id", "template_product_option_id", "product_id", "template_type", "template_bucket", "row_number", "selector_code", "product_name", "confidence", "reason", "review_status"],
        ),
        "template_pricing_option_links": upsert_rows(
            conn,
            "template_pricing_option_links",
            "link_id",
            rows_by_table.get("template_pricing_option_links") or [],
            [
                "link_id",
                "template_product_option_id",
                "pricing_candidate_key",
                "pricing_item_id",
                "template_type",
                "template_bucket",
                "row_number",
                "selector_code",
                "template_product_name",
                "canonical_template_option",
                "pricing_product_name",
                "confidence",
                "reason",
                "review_status",
                "source_file",
            ],
        ),
    }
    return counts


def product_catalog_row(product_id: str, knowledge: dict[str, Any], mapping: dict[str, Any], template: dict[str, Any]) -> dict[str, Any]:
    aliases = sorted(
        {
            value
            for value in [
                clean(mapping.get("canonical_template_option")),
                clean(template.get("raw_template_option")),
                *split_semicolon(knowledge.get("aliases")),
            ]
            if value
        }
    )
    return {
        "product_id": product_id,
        "manufacturer": clean(knowledge.get("manufacturer")),
        "product_family": clean(knowledge.get("product_family")),
        "product_name": clean(knowledge.get("raw_product_name") or mapping.get("canonical_template_option") or template.get("raw_template_option")),
        "sku": "",
        "category": clean(knowledge.get("category") or template.get("category_hint")),
        "subcategory": clean(knowledge.get("subcategory")),
        "unit": clean(knowledge.get("unit")),
        "aliases": aliases,
        "active": True,
        "extraction_method": "llm_approved_mapping_seed",
        "extraction_warnings": ["Seeded from approved LLM mapping; product document evidence may still be incomplete."],
    }


def product_alias_row(product_id: str, alias: Any, mapping: dict[str, Any]) -> dict[str, Any] | None:
    alias_text = clean(alias)
    if not alias_text or alias_text.lower() in {"none", "nan", "null"}:
        return None
    return {
        "alias_id": stable_id("alias", product_id, alias_text),
        "product_id": product_id,
        "alias": alias_text,
        "alias_type": "llm_approved_template_mapping",
        "confidence": safe_float(mapping.get("confidence")),
    }


def product_decision_link_row(product_id: str, decision_id: str, mapping: dict[str, Any]) -> dict[str, Any]:
    return {
        "link_id": stable_id("pdlink", product_id, decision_id),
        "product_id": product_id,
        "decision_id": decision_id,
        "influence_type": "candidate_product",
        "confidence": safe_float(mapping.get("confidence")),
        "reason": clean(mapping.get("reason")),
    }


def template_product_link_row(option_id: str, product_id: str, template: dict[str, Any], mapping: dict[str, Any]) -> dict[str, Any]:
    return {
        "link_id": stable_id("tplink", option_id, product_id),
        "template_product_option_id": option_id,
        "product_id": product_id,
        "template_type": clean(template.get("template_type")),
        "template_bucket": clean(template.get("template_bucket")),
        "row_number": safe_int(template.get("row_number")),
        "selector_code": clean(template.get("selector_code")),
        "product_name": clean(template.get("raw_template_option")),
        "confidence": safe_float(mapping.get("confidence")),
        "review_status": "approved",
        "reason": clean(mapping.get("reason")),
    }


def template_pricing_link_row(option_id: str, pricing_key: str, pricing: dict[str, Any], template: dict[str, Any], mapping: dict[str, Any]) -> dict[str, Any]:
    return {
        "link_id": stable_id("tplprice", option_id, pricing_key),
        "template_product_option_id": option_id,
        "pricing_candidate_key": pricing_key,
        "pricing_item_id": clean(pricing.get("pricing_item_id")),
        "template_type": clean(template.get("template_type")),
        "template_bucket": clean(template.get("template_bucket")),
        "row_number": safe_int(template.get("row_number")),
        "selector_code": clean(template.get("selector_code")),
        "template_product_name": clean(template.get("raw_template_option")),
        "canonical_template_option": clean(mapping.get("canonical_template_option")),
        "pricing_product_name": clean(pricing.get("raw_pricing_name")),
        "confidence": safe_float(mapping.get("confidence")),
        "review_status": "approved",
        "reason": clean(mapping.get("reason")),
        "source_file": clean(pricing.get("source_file")),
    }


def decision_ids_from_knowledge(knowledge: dict[str, Any]) -> list[str]:
    value = clean(knowledge.get("decision_links"))
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [clean(item) for item in parsed if clean(item)]
    except json.JSONDecodeError:
        pass
    return [part for part in split_semicolon(value) if part]


def source_option_ids(template: dict[str, Any]) -> list[str]:
    return [part for part in split_semicolon(template.get("source_option_ids")) if part]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def read_mapping_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path).fillna("").to_dict(orient="records")
    return read_jsonl(path)


def read_csv_index(path: Path, key_field: str) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    frame = pd.read_csv(path).fillna("")
    return {clean(row.get(key_field)): row for row in frame.to_dict(orient="records") if clean(row.get(key_field))}


def schema_statements(sql_text: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    for line in sql_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        current.append(line)
        if stripped.endswith(";"):
            statements.append("\n".join(current).strip().rstrip(";"))
            current = []
    if current:
        statements.append("\n".join(current).strip())
    return statements


def upsert_rows(
    conn: Connection,
    table: str,
    id_field: str,
    rows: list[dict[str, Any]],
    columns: list[str],
    *,
    json_columns: set[str] | None = None,
) -> int:
    if not rows:
        return 0
    json_columns = json_columns or set()
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
            params[column] = json.dumps(params.get(column) or [], sort_keys=True, default=str)
        conn.execute(sql, params)
    return len(rows)


def split_semicolon(value: Any) -> list[str]:
    return [part.strip() for part in clean(value).split(";") if part.strip()]


def parse_suggested_aliases(value: Any) -> list[str]:
    if isinstance(value, list):
        return [clean(item) for item in value if clean(item)]
    text_value = clean(value)
    if not text_value or text_value in {"[]", "None", "null"}:
        return []
    try:
        parsed = json.loads(text_value)
        if isinstance(parsed, list):
            return [clean(item) for item in parsed if clean(item)]
    except json.JSONDecodeError:
        pass
    if text_value.startswith("[") and text_value.endswith("]"):
        text_value = text_value[1:-1]
    return [
        part.strip().strip("'\"")
        for part in text_value.split(",")
        if part.strip().strip("'\"")
    ]


def clean(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return " ".join(str(value).strip().split())


def safe_int(value: Any) -> int | None:
    text_value = clean(value)
    if not text_value:
        return None
    try:
        return int(float(text_value))
    except (TypeError, ValueError):
        return None


def safe_float(value: Any) -> float | None:
    text_value = clean(value)
    if not text_value:
        return None
    try:
        return float(text_value)
    except (TypeError, ValueError):
        return None


def stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(clean(part).lower() for part in parts)
    return f"{prefix}_{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:20]}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import approved LLM template/product/pricing mappings.")
    parser.add_argument("--mapping", default=str(DEFAULT_MAPPING_PATH))
    parser.add_argument("--llm-input-dir", default=str(DEFAULT_LLM_INPUT_DIR))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--database-url", default=os.getenv("NEON_DATABASE_URL") or os.getenv("DATABASE_URL"))
    parser.add_argument("--write-db", action="store_true")
    parser.add_argument("--no-env", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.no_env:
        load_project_env()
        if not args.database_url:
            args.database_url = os.getenv("NEON_DATABASE_URL") or os.getenv("DATABASE_URL")
    rows = build_approved_mapping_import(mapping_path=args.mapping, llm_input_dir=args.llm_input_dir)
    paths = write_import_preview(rows, args.out_dir)
    counts = {name: len(value) for name, value in rows.items()}
    print(json.dumps({"preview": {name: str(path) for name, path in paths.items()}, "counts": counts}, indent=2, sort_keys=True))
    if args.write_db:
        if not args.database_url:
            raise SystemExit("Set --database-url, NEON_DATABASE_URL, or DATABASE_URL for --write-db.")
        engine = create_engine(args.database_url)
        with engine.begin() as conn:
            write_counts = write_approved_mapping_rows(conn, rows)
        print(json.dumps({"db_upserts": write_counts}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
