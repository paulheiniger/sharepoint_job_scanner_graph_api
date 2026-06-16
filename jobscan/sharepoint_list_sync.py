from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv

from .graph_client import GraphClient, GraphError, SharePointTarget
from .sharepoint_sync import classify_document_type, is_url, select_document_url

DEFAULT_SITE_URL = "https://aro365531128.sharepoint.com/sites/Data"
DEFAULT_LIST_NAME = "Job Index"
DEFAULT_REPORT_OUT = Path("output/sharepoint_job_index_sync_report.json")
DEFAULT_COLUMNS_OUT = Path("output/job_index_sharepoint_columns.json")

RECOMMENDED_FIELDS = [
    "Title",
    "job_id",
    "division",
    "pipeline_status",
    "status",
    "customer",
    "job_name",
    "job_type",
    "site_address",
    "city",
    "state",
    "zip_code",
    "estimate_date",
    "estimated_sqft",
    "material_subtotal",
    "labor_subtotal",
    "total_job_cost",
    "overhead_pct",
    "overhead_amount",
    "profit_pct",
    "profit_amount",
    "worksheet_price",
    "final_price",
    "price_per_sqft",
    "invoice_number",
    "invoice_amount",
    "invoice_date",
    "has_signed_contract",
    "has_invoice",
    "has_warranty",
    "has_proposal",
    "has_job_spec",
    "has_job_tracking_form",
    "has_aerial",
    "has_notes",
    "photo_count",
    "folder_name",
    "folder_path",
    "folder_url",
    "estimate_file",
    "invoice_file",
    "warnings",
    "last_scanned_at",
    "primary_doc_link",
    "proposal_url",
    "estimate_url",
    "contract_url",
    "invoice_url",
    "job_tracking_url",
    "warranty_url",
    "aerial_url",
    "important_doc_links_json",
    "document_link_count",
    "primary_doc_type",
    "primary_doc_name",
]

SOURCE_ALIASES = {
    "Title": ["Title", "title"],
    "primary_doc_link": ["primary_doc_link"],
    "zip_code": ["zip_code", "zip"],
    "profit_pct": ["profit_pct", "profit_percent"],
}


@dataclass
class ColumnInfo:
    id: str
    name: str
    display_name: str
    hidden: bool
    read_only: bool
    required: bool
    type_name: str
    raw: dict[str, Any]


def normalize_column_name(value: object) -> str:
    text = str(value or "").strip()
    text = re.sub(r"_x([0-9a-fA-F]{4})_", lambda m: chr(int(m.group(1), 16)), text)
    text = re.sub(r"[^0-9a-zA-Z]+", "_", text.lower())
    return re.sub(r"_+", "_", text).strip("_")


def clean_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return None if stripped.lower() in {"", "nan", "none", "null", "nat"} else stripped
    try:
        import pandas as pd

        if pd.isna(value):
            return None
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
    except Exception:
        pass
    try:
        import numpy as np

        if isinstance(value, np.generic):
            return value.item()
    except Exception:
        pass
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def infer_column_type(column: dict[str, Any]) -> str:
    for key in ("text", "multilineText", "number", "currency", "boolean", "dateTime", "hyperlinkOrPicture", "choice", "calculated", "lookup", "personOrGroup"):
        if key in column:
            return key
    return column.get("columnGroup") or "unknown"


def column_infos(columns: list[dict[str, Any]]) -> list[ColumnInfo]:
    return [
        ColumnInfo(
            id=str(col.get("id") or ""),
            name=str(col.get("name") or ""),
            display_name=str(col.get("displayName") or col.get("name") or ""),
            hidden=bool(col.get("hidden")),
            read_only=bool(col.get("readOnly")) or "calculated" in col,
            required=bool(col.get("required")),
            type_name=infer_column_type(col),
            raw=col,
        )
        for col in columns
    ]


def build_column_lookup(columns: list[ColumnInfo]) -> dict[str, ColumnInfo]:
    lookup: dict[str, ColumnInfo] = {}
    for column in columns:
        for value in (column.name, column.display_name):
            key = normalize_column_name(value)
            if key:
                lookup.setdefault(key, column)
    return lookup


def resolve_site_id(client: GraphClient, site_url: str, explicit_site_id: str | None = None) -> str:
    if explicit_site_id:
        return explicit_site_id
    target = SharePointTarget.from_url(site_url)
    site = client.get_site(target.hostname, target.site_path)
    return str(site["id"])


def resolve_list_id(client: GraphClient, site_id: str, list_name: str, explicit_list_id: str | None = None) -> str:
    if explicit_list_id:
        return explicit_list_id
    escaped_name = list_name.replace("'", "''")
    try:
        data = client.get_json(f"/sites/{site_id}/lists?$filter=displayName eq '{escaped_name}'")
        matches = data.get("value") or []
    except GraphError:
        matches = []
    if not matches:
        matches = client.get_all_pages(f"/sites/{site_id}/lists")
    for item in matches:
        if str(item.get("displayName") or "").strip().lower() == list_name.strip().lower():
            return str(item["id"])
    names = ", ".join(str(item.get("displayName")) for item in matches)
    raise GraphError(f"SharePoint list '{list_name}' not found. Available lists: {names}")


def get_columns(client: GraphClient, site_id: str, list_id: str) -> list[ColumnInfo]:
    return column_infos(client.get_all_pages(f"/sites/{site_id}/lists/{list_id}/columns"))


def print_columns(columns: list[ColumnInfo]) -> None:
    print(f"{'Display Name':32} {'Internal Name':32} {'Type':18} Hidden ReadOnly")
    print("-" * 100)
    for col in columns:
        print(f"{col.display_name[:32]:32} {col.name[:32]:32} {col.type_name[:18]:18} {str(col.hidden):6} {str(col.read_only):8}")


def load_records(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON list.")
    return [row for row in data if isinstance(row, dict)]


def completeness_score(record: dict[str, Any]) -> tuple[int, str]:
    return (
        sum(1 for value in record.values() if clean_value(value) is not None),
        str(clean_value(record.get("last_scanned_at")) or ""),
    )


def dedupe_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        job_id = str(clean_value(record.get("job_id")) or "").strip()
        if not job_id:
            continue
        grouped.setdefault(job_id.lower(), []).append(record)
    duplicates: dict[str, int] = {rows[0].get("job_id", key): len(rows) for key, rows in grouped.items() if len(rows) > 1}
    out: list[dict[str, Any]] = []
    for rows in grouped.values():
        rows = sorted(
            rows,
            key=lambda row: (
                1 if clean_value(row.get("folder_url")) else 0,
                1 if clean_value(row.get("primary_doc_link")) else 0,
                1 if clean_value(row.get("final_price")) else 0,
                *completeness_score(row),
            ),
            reverse=True,
        )
        out.append(rows[0])
    return out, duplicates


def document_entries_from_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    raw = record.get("important_doc_links_json")
    if not raw:
        return []
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("url") or ""
        out.append({"name": name, "web_url": item.get("url") or item.get("web_url"), "document_type": item.get("type") or classify_document_type(str(name)), "modified_at": item.get("modified_at")})
    return out


def ensure_document_links(record: dict[str, Any]) -> dict[str, Any]:
    record = dict(record)
    docs = document_entries_from_record(record)
    for doc_type, field in {
        "proposal": "proposal_url",
        "estimate": "estimate_url",
        "contract": "contract_url",
        "invoice": "invoice_url",
        "job_tracking": "job_tracking_url",
        "warranty": "warranty_url",
        "aerial": "aerial_url",
    }.items():
        if clean_value(record.get(field)):
            continue
        match = select_document_url(docs, doc_type, [record.get("estimate_file"), record.get("invoice_file"), record.get("job_tracking_file")])
        if match:
            record[field] = match.get("web_url")
    if not clean_value(record.get("primary_doc_link")):
        for field in ("proposal_url", "estimate_url", "contract_url", "job_tracking_url", "folder_url"):
            if clean_value(record.get(field)):
                record["primary_doc_link"] = record[field]
                record["primary_doc_type"] = field.replace("_url", "").replace("_link", "")
                break
    return record


def title_for_record(record: dict[str, Any]) -> str:
    return str(clean_value(record.get("job_name")) or clean_value(record.get("customer")) or clean_value(record.get("folder_name")) or clean_value(record.get("job_id")) or "Untitled Job")


def convert_value_for_column(value: Any, column: ColumnInfo) -> Any:
    value = clean_value(value)
    if value is None:
        return None
    if column.type_name in {"number", "currency"}:
        try:
            return float(str(value).replace("$", "").replace(",", ""))
        except (TypeError, ValueError):
            return None
    if column.type_name == "boolean":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "y"}
    if column.type_name == "dateTime":
        parsed = str(value)
        return parsed
    if column.type_name == "hyperlinkOrPicture":
        url = str(value).strip()
        return {"Url": url, "Description": url} if is_url(url) else None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def build_field_mapping(columns: list[ColumnInfo], source_fields: list[str] = RECOMMENDED_FIELDS) -> tuple[dict[str, ColumnInfo], list[str], list[str]]:
    lookup = build_column_lookup(columns)
    mapped: dict[str, ColumnInfo] = {}
    missing: list[str] = []
    skipped: list[str] = []
    for source in source_fields:
        aliases = SOURCE_ALIASES.get(source, [source])
        column = next((lookup.get(normalize_column_name(alias)) for alias in aliases if lookup.get(normalize_column_name(alias))), None)
        if not column:
            missing.append(source)
            continue
        if column.hidden or column.read_only:
            skipped.append(f"{source} -> {column.display_name}")
            continue
        mapped[source] = column
    return mapped, missing, skipped


def build_payload(record: dict[str, Any], mapping: dict[str, ColumnInfo]) -> dict[str, Any]:
    record = ensure_document_links(record)
    fields: dict[str, Any] = {}
    for source, column in mapping.items():
        value = title_for_record(record) if source == "Title" else record.get(source)
        if source == "warnings" and isinstance(value, list):
            value = "\n".join(str(item) for item in value)
        if source == "important_doc_links_json" and not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False)
        converted = convert_value_for_column(value, column)
        fields[column.name] = converted
    return fields


def get_existing_items(client: GraphClient, site_id: str, list_id: str) -> list[dict[str, Any]]:
    return client.get_all_pages(f"/sites/{site_id}/lists/{list_id}/items?$expand=fields")


def existing_job_id_map(items: list[dict[str, Any]], job_id_column: ColumnInfo | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not job_id_column:
        return out
    for item in items:
        fields = item.get("fields") or {}
        job_id = str(clean_value(fields.get(job_id_column.name)) or "").strip().lower()
        if job_id:
            out[job_id] = str(item["id"])
    return out


def sync_records(
    *,
    client: GraphClient,
    site_id: str,
    list_id: str,
    records: list[dict[str, Any]],
    mapping: dict[str, ColumnInfo],
    existing: dict[str, str],
    dry_run: bool,
    create_only: bool,
    update_only: bool,
    continue_on_error: bool,
) -> dict[str, Any]:
    stats = Counter()
    errors: list[dict[str, Any]] = []
    for record in records:
        job_id = str(clean_value(record.get("job_id")) or "").strip()
        if not job_id:
            stats["skipped"] += 1
            continue
        fields = build_payload(record, mapping)
        item_id = existing.get(job_id.lower())
        try:
            if item_id:
                if create_only:
                    stats["skipped"] += 1
                    continue
                stats["updates_attempted"] += 1
                if not dry_run:
                    client.request("PATCH", f"/sites/{site_id}/lists/{list_id}/items/{item_id}/fields", json=fields)
                stats["updates_succeeded"] += 1
            else:
                if update_only:
                    stats["skipped"] += 1
                    continue
                stats["creates_attempted"] += 1
                if not dry_run:
                    client.request("POST", f"/sites/{site_id}/lists/{list_id}/items", json={"fields": fields})
                stats["creates_succeeded"] += 1
        except Exception as exc:
            stats["failures"] += 1
            errors.append({"job_id": job_id, "error": f"{type(exc).__name__}: {exc}"})
            if not continue_on_error:
                raise
    return {**stats, "errors": errors}


def url_count(records: list[dict[str, Any]], field: str) -> int:
    return sum(1 for record in records if is_url(clean_value(record.get(field))))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).isoformat()
    client = GraphClient(timeout=args.timeout)
    site_id = resolve_site_id(client, args.site_url, args.site_id)
    list_id = resolve_list_id(client, site_id, args.list_name, args.list_id)
    columns = get_columns(client, site_id, list_id)
    if args.print_columns:
        print_columns(columns)
    write_json(args.columns_out, [column.raw for column in columns])
    if args.columns_only:
        return {"site_id": site_id, "list_id": list_id, "columns": len(columns)}

    records = load_records(args.input)
    if args.limit:
        records = records[: args.limit]
    unique_records, duplicates = dedupe_records(records)
    mapping, missing, skipped = build_field_mapping(columns)
    print("Mapped columns:")
    for source, column in mapping.items():
        print(f"  {source} -> {column.display_name} ({column.name})")
    print("Missing recommended columns:", ", ".join(missing) if missing else "none")
    print("Skipped read-only columns:", ", ".join(skipped) if skipped else "none")
    existing_items = get_existing_items(client, site_id, list_id)
    job_id_column = mapping.get("job_id")
    existing = existing_job_id_map(existing_items, job_id_column)
    sync_stats = sync_records(
        client=client,
        site_id=site_id,
        list_id=list_id,
        records=unique_records,
        mapping=mapping,
        existing=existing,
        dry_run=args.dry_run,
        create_only=args.create_only,
        update_only=args.update_only,
        continue_on_error=args.continue_on_error,
    )
    completed_at = datetime.now(timezone.utc).isoformat()
    report = {
        "started_at": started_at,
        "completed_at": completed_at,
        "site_id": site_id,
        "list_id": list_id,
        "list_name": args.list_name,
        "input_rows": len(records),
        "unique_job_ids": len(unique_records),
        "duplicate_job_ids": duplicates,
        "existing_list_items": len(existing_items),
        "creates_attempted": sync_stats.get("creates_attempted", 0),
        "creates_succeeded": sync_stats.get("creates_succeeded", 0),
        "updates_attempted": sync_stats.get("updates_attempted", 0),
        "updates_succeeded": sync_stats.get("updates_succeeded", 0),
        "skipped": sync_stats.get("skipped", 0),
        "failures": sync_stats.get("failures", 0),
        "jobs_with_folder_url": url_count(unique_records, "folder_url"),
        "jobs_with_primary_doc_link": url_count(unique_records, "primary_doc_link"),
        "jobs_with_estimate_url": url_count(unique_records, "estimate_url"),
        "jobs_with_proposal_url": url_count(unique_records, "proposal_url"),
        "jobs_with_invoice_url": url_count(unique_records, "invoice_url"),
        "mapped_columns": {source: column.name for source, column in mapping.items()},
        "missing_columns": missing,
        "truncated_values": [],
        "errors": sync_stats.get("errors", []),
        "dry_run": args.dry_run,
    }
    write_json(args.report_out, report)
    print("SharePoint Job Index sync")
    print(f"Input jobs: {len(records)}")
    print(f"Unique job IDs: {len(unique_records)}")
    print(f"Existing items: {len(existing_items)}")
    print(f"Created: {report['creates_succeeded']}")
    print(f"Updated: {report['updates_succeeded']}")
    print(f"Failed: {report['failures']}")
    print(f"Jobs with folder URL: {report['jobs_with_folder_url']}")
    print(f"Jobs with primary document link: {report['jobs_with_primary_doc_link']}")
    print(f"Jobs missing primary document link: {len(unique_records) - report['jobs_with_primary_doc_link']}")
    print(f"Report: {args.report_out}")
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Upsert scanner job_index JSON rows into a SharePoint List through Microsoft Graph.")
    parser.add_argument("--input", type=Path, default=Path("output/job_index.json"))
    parser.add_argument("--site-url", default=os.getenv("SHAREPOINT_JOB_INDEX_SITE_URL") or DEFAULT_SITE_URL)
    parser.add_argument("--list-name", default=os.getenv("SHAREPOINT_JOB_INDEX_LIST_NAME") or DEFAULT_LIST_NAME)
    parser.add_argument("--site-id", default=os.getenv("SHAREPOINT_JOB_INDEX_SITE_ID") or "")
    parser.add_argument("--list-id", default=os.getenv("SHAREPOINT_JOB_INDEX_LIST_ID") or "")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--create-only", action="store_true")
    parser.add_argument("--update-only", action="store_true")
    parser.add_argument("--print-columns", action="store_true")
    parser.add_argument("--columns-only", action="store_true")
    parser.add_argument("--columns-out", type=Path, default=DEFAULT_COLUMNS_OUT)
    parser.add_argument("--report-out", type=Path, default=DEFAULT_REPORT_OUT)
    parser.add_argument("--concurrency", type=int, default=4, help="Reserved for future batched writes; writes are currently conservative/sequential.")
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args(argv)
    if args.create_only and args.update_only:
        parser.error("--create-only and --update-only cannot be combined")
    return args


def main() -> None:
    run(parse_args())


if __name__ == "__main__":
    main()
