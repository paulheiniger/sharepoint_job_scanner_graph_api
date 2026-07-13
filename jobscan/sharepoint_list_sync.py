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

from dotenv import load_dotenv

from .graph_client import GraphClient, GraphError, SharePointTarget
from .sharepoint_sync import classify_document_type, is_url, select_document_url

DEFAULT_SITE_URL = "https://aro365531128.sharepoint.com/sites/Data"
DEFAULT_LIST_NAME = "Job Index"
DEFAULT_REPORT_OUT = Path("output/sharepoint_job_index_sync_report.json")
DEFAULT_COLUMNS_OUT = Path("output/job_index_sharepoint_columns.json")

URL_FIELDS = {
    "folder_url",
    "primary_doc_link",
    "proposal_url",
    "estimate_url",
    "contract_url",
    "invoice_url",
    "job_tracking_url",
    "warranty_url",
    "aerial_url",
}

CRITICAL_FIELDS = {
    "job_id",
    "customer",
    "job_name",
    "folder_url",
    "primary_doc_link",
    "estimate_url",
}

OPTIONAL_FIELDS = {
    "proposal_url",
    "contract_url",
    "invoice_url",
    "job_tracking_url",
    "warranty_url",
    "aerial_url",
    "primary_doc_type",
    "primary_doc_name",
    "important_doc_links_json",
    "document_link_count",
}

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
    "document_link_count",
    "primary_doc_type",
    "primary_doc_name",
]

SOURCE_ALIASES = {
    "Title": ["Title", "title"],
    "primary_doc_link": ["primary_doc_link"],
    "zip_code": ["zip_code", "zip"],
    "profit_pct": ["profit_pct", "profit_percent"],
    "estimated_sqft": ["estimated_sqft", "estimated_square_feet"],
    "price_per_sqft": ["price_per_sqft", "price_per_square_foot"],
}


def default_source_fields(*, include_important_doc_links_json: bool = False) -> list[str]:
    fields = list(RECOMMENDED_FIELDS)
    if include_important_doc_links_json:
        insert_after = fields.index("aerial_url") + 1 if "aerial_url" in fields else len(fields)
        fields.insert(insert_after, "important_doc_links_json")
    return fields


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
    for key in ("text", "multilineText", "note", "number", "currency", "boolean", "dateTime", "hyperlinkOrPicture", "choice", "calculated", "lookup", "personOrGroup"):
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


def desired_column_payload(source: str) -> dict[str, Any]:
    display_name = source
    if source in {"document_link_count", "photo_count"}:
        return {"name": source, "displayName": display_name, "number": {}}
    if source in {"important_doc_links_json", "warnings"}:
        return {"name": source, "displayName": display_name, "multilineText": {"allowMultipleLines": True}}
    if source in URL_FIELDS:
        return {"name": source, "displayName": display_name, "text": {}}
    return {"name": source, "displayName": display_name, "text": {}}


def ensure_missing_columns(
    client: GraphClient,
    site_id: str,
    list_id: str,
    missing: list[str],
    *,
    continue_on_error: bool = True,
) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    for source in missing:
        payload = desired_column_payload(source)
        try:
            client.request("POST", f"/sites/{site_id}/lists/{list_id}/columns", json=payload)
            print(f"Created SharePoint column: {source}")
        except Exception as exc:
            failures.append({"field": source, "error": f"{type(exc).__name__}: {exc}"})
            print(f"Failed creating SharePoint column {source}: {type(exc).__name__}: {exc}")
            if not continue_on_error:
                raise
    if failures:
        print(
            "The app may not have permission to modify the SharePoint list schema. "
            "Add missing columns manually or request elevated permission."
        )
    return failures


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


def is_text_like_column(column: ColumnInfo) -> bool:
    return column.type_name in {"text", "multilineText", "note", "unknown", "Custom Columns"} or column.type_name.lower() in {"custom columns", "custom"}


def convert_value_for_column(value: Any, column: ColumnInfo, *, source_field: str | None = None, url_fields_as_text: bool = False) -> Any:
    value = clean_value(value)
    if value is None:
        return None
    if source_field in URL_FIELDS:
        url = str(value).strip()
        if url_fields_as_text or is_text_like_column(column):
            return url
        if column.type_name == "hyperlinkOrPicture":
            return {"Url": url, "Description": url} if is_url(url) else None
        return url
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


def classify_missing_columns(missing: list[str]) -> tuple[list[str], list[str], list[str]]:
    critical = [field for field in missing if field in CRITICAL_FIELDS]
    optional = [field for field in missing if field in OPTIONAL_FIELDS]
    other = [field for field in missing if field not in CRITICAL_FIELDS and field not in OPTIONAL_FIELDS]
    return critical, optional, other


def build_payload(
    record: dict[str, Any],
    mapping: dict[str, ColumnInfo],
    *,
    url_fields_as_text: bool = False,
    url_text_columns: set[str] | None = None,
    omitted_fields: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    record = ensure_document_links(record)
    url_text_columns = url_text_columns or set()
    fields: dict[str, Any] = {}
    for source, column in mapping.items():
        value = title_for_record(record) if source == "Title" else record.get(source)
        if source == "warnings" and isinstance(value, list):
            value = "\n".join(str(item) for item in value)
        if source == "important_doc_links_json" and not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False)
        if source == "important_doc_links_json" and column.type_name == "text":
            text_value = str(clean_value(value) or "")
            if len(text_value) > 255:
                print(
                    "WARNING: important_doc_links_json omitted because the destination SharePoint column "
                    f"'{column.name}' is single-line text and the value is {len(text_value)} characters."
                )
                if omitted_fields is not None:
                    omitted_fields.append(
                        {
                            "field": source,
                            "column": column.name,
                            "reason": "single-line text limit",
                            "length": len(text_value),
                        }
                    )
                continue
        converted = convert_value_for_column(
            value,
            column,
            source_field=source,
            url_fields_as_text=url_fields_as_text or column.name in url_text_columns,
        )
        if converted is None:
            continue
        fields[column.name] = converted
    return fields


def write_list_item_fields(
    client: GraphClient,
    site_id: str,
    list_id: str,
    item_id: str | None,
    fields: dict[str, Any],
) -> None:
    if item_id:
        client.request("PATCH", f"/sites/{site_id}/lists/{list_id}/items/{item_id}/fields", json=fields)
    else:
        client.request("POST", f"/sites/{site_id}/lists/{list_id}/items", json={"fields": fields})


def diagnose_rejected_fields(
    *,
    client: GraphClient,
    site_id: str,
    list_id: str,
    item_id: str | None,
    fields: dict[str, Any],
) -> list[dict[str, Any]]:
    rejected: list[dict[str, Any]] = []
    for field_name, value in fields.items():
        try:
            write_list_item_fields(client, site_id, list_id, item_id, {field_name: value})
        except Exception as exc:
            rejected.append(
                {
                    "column": field_name,
                    "value_preview": str(value)[:200],
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return rejected


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
    url_fields_as_text: bool = False,
    diagnose_field_errors: bool = False,
    omit_rejected_fields: bool = False,
) -> dict[str, Any]:
    stats = Counter()
    errors: list[dict[str, Any]] = []
    omitted_fields: list[dict[str, Any]] = []
    url_text_columns: set[str] = set()
    url_column_names = {column.name for source, column in mapping.items() if source in URL_FIELDS}
    for record in records:
        job_id = str(clean_value(record.get("job_id")) or "").strip()
        if not job_id:
            stats["skipped"] += 1
            continue
        fields = build_payload(
            record,
            mapping,
            url_fields_as_text=url_fields_as_text,
            url_text_columns=url_text_columns,
            omitted_fields=omitted_fields,
        )
        item_id = existing.get(job_id.lower())
        try:
            if item_id:
                if create_only:
                    stats["skipped"] += 1
                    continue
                stats["updates_attempted"] += 1
                if not dry_run:
                    write_list_item_fields(client, site_id, list_id, item_id, fields)
                stats["updates_succeeded"] += 1
            else:
                if update_only:
                    stats["skipped"] += 1
                    continue
                stats["creates_attempted"] += 1
                if not dry_run:
                    write_list_item_fields(client, site_id, list_id, None, fields)
                stats["creates_succeeded"] += 1
        except Exception as exc:
            if not url_fields_as_text and url_column_names:
                try:
                    retry_fields = build_payload(record, mapping, url_fields_as_text=True, omitted_fields=omitted_fields)
                    if item_id:
                        if not dry_run:
                            write_list_item_fields(client, site_id, list_id, item_id, retry_fields)
                        stats["updates_succeeded"] += 1
                    else:
                        if not dry_run:
                            write_list_item_fields(client, site_id, list_id, None, retry_fields)
                        stats["creates_succeeded"] += 1
                    url_text_columns.update(url_column_names)
                    stats["url_hyperlink_fallbacks"] += 1
                    continue
                except Exception as retry_exc:
                    rejected_fields = []
                    if diagnose_field_errors and not dry_run:
                        rejected_fields = diagnose_rejected_fields(
                            client=client,
                            site_id=site_id,
                            list_id=list_id,
                            item_id=item_id,
                            fields=retry_fields,
                        )
                        if rejected_fields and omit_rejected_fields:
                            rejected_column_names = {str(field["column"]) for field in rejected_fields}
                            sanitized_fields = {key: value for key, value in retry_fields.items() if key not in rejected_column_names}
                            try:
                                write_list_item_fields(client, site_id, list_id, item_id, sanitized_fields)
                                url_text_columns.update(url_column_names)
                                stats["rejected_fields_omitted"] += len(rejected_fields)
                                if item_id:
                                    stats["updates_succeeded"] += 1
                                else:
                                    stats["creates_succeeded"] += 1
                                continue
                            except Exception:
                                pass
                    errors.append(
                        {
                            "job_id": job_id,
                            "error": f"{type(exc).__name__}: {exc}",
                            "text_retry_error": f"{type(retry_exc).__name__}: {retry_exc}",
                            "rejected_fields": rejected_fields,
                        }
                    )
                    stats["failures"] += 1
                    if not continue_on_error:
                        raise retry_exc from exc
            else:
                rejected_fields = []
                if diagnose_field_errors and not dry_run:
                    rejected_fields = diagnose_rejected_fields(
                        client=client,
                        site_id=site_id,
                        list_id=list_id,
                        item_id=item_id,
                        fields=fields,
                    )
                    if rejected_fields and omit_rejected_fields:
                        rejected_column_names = {str(field["column"]) for field in rejected_fields}
                        sanitized_fields = {key: value for key, value in fields.items() if key not in rejected_column_names}
                        try:
                            write_list_item_fields(client, site_id, list_id, item_id, sanitized_fields)
                            stats["rejected_fields_omitted"] += len(rejected_fields)
                            if item_id:
                                stats["updates_succeeded"] += 1
                            else:
                                stats["creates_succeeded"] += 1
                            continue
                        except Exception:
                            pass
                stats["failures"] += 1
                errors.append({"job_id": job_id, "error": f"{type(exc).__name__}: {exc}", "rejected_fields": rejected_fields})
                if not continue_on_error:
                    raise
    return {**stats, "errors": errors, "url_text_columns": sorted(url_text_columns), "omitted_fields": omitted_fields}


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
    source_fields = default_source_fields(include_important_doc_links_json=args.include_important_doc_links_json)
    mapping, missing, skipped = build_field_mapping(columns, source_fields)
    critical_missing, optional_missing, other_missing = classify_missing_columns(missing)
    ensure_column_failures: list[dict[str, str]] = []
    if args.ensure_columns and missing:
        ensure_column_failures = ensure_missing_columns(client, site_id, list_id, missing)
        if ensure_column_failures and not args.ensure_columns_only:
            columns = get_columns(client, site_id, list_id)
            mapping, missing, skipped = build_field_mapping(columns, source_fields)
            critical_missing, optional_missing, other_missing = classify_missing_columns(missing)
    if "primary_doc_link" in missing:
        print("primary_doc_link missing: Copilot/BizChat document lookup will be less reliable.")
    if args.columns_only or args.ensure_columns_only:
        completed_at = datetime.now(timezone.utc).isoformat()
        report = {
            "started_at": started_at,
            "completed_at": completed_at,
            "site_id": site_id,
            "list_id": list_id,
            "list_name": args.list_name,
            "columns": len(columns),
            "mapped_columns": {source: column.name for source, column in mapping.items()},
            "missing_columns": missing,
            "critical_missing_columns": critical_missing,
            "optional_missing_columns": optional_missing,
            "other_missing_columns": other_missing,
            "skipped_read_only_columns": skipped,
            "ensure_column_failures": ensure_column_failures,
            "errors": [],
            "dry_run": args.dry_run,
        }
        write_json(args.report_out, report)
        if args.ensure_columns_only and ensure_column_failures:
            raise SystemExit(1)
        return report

    records = load_records(args.input)
    if args.limit:
        records = records[: args.limit]
    unique_records, duplicates = dedupe_records(records)
    print("Mapped columns:")
    for source, column in mapping.items():
        print(f"  {source} -> {column.display_name} ({column.name})")
    print("Critical missing columns:", ", ".join(critical_missing) if critical_missing else "none")
    print("Optional missing columns:", ", ".join(optional_missing) if optional_missing else "none")
    print("Other missing recommended columns:", ", ".join(other_missing) if other_missing else "none")
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
        url_fields_as_text=args.url_fields_as_text,
        diagnose_field_errors=args.diagnose_field_errors,
        omit_rejected_fields=args.omit_rejected_fields,
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
        "critical_missing_columns": critical_missing,
        "optional_missing_columns": optional_missing,
        "other_missing_columns": other_missing,
        "skipped_read_only_columns": skipped,
        "ensure_column_failures": ensure_column_failures,
        "url_hyperlink_fallbacks": sync_stats.get("url_hyperlink_fallbacks", 0),
        "rejected_fields_omitted": sync_stats.get("rejected_fields_omitted", 0),
        "url_text_columns": sync_stats.get("url_text_columns", []),
        "omitted_fields": sync_stats.get("omitted_fields", []),
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
    print(f"Rejected fields omitted: {report['rejected_fields_omitted']}")
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
    parser.add_argument("--ensure-columns", action="store_true", help="Attempt to create missing SharePoint columns, then continue syncing existing columns if creation fails.")
    parser.add_argument("--ensure-columns-only", action="store_true", help="Only attempt to create missing columns and write a report.")
    parser.add_argument("--url-fields-as-text", action="store_true", help="Force URL fields to be written as plain strings regardless of detected SharePoint column type.")
    parser.add_argument("--diagnose-field-errors", action="store_true", help="On SharePoint 400s, try each field separately and write rejected field details to the report.")
    parser.add_argument("--omit-rejected-fields", action="store_true", help="With --diagnose-field-errors, retry the item after omitting fields rejected by SharePoint.")
    parser.add_argument("--include-important-doc-links-json", action="store_true", help="Include important_doc_links_json in SharePoint writes. Omitted by default because long JSON can exceed single-line text limits.")
    parser.add_argument("--columns-out", type=Path, default=DEFAULT_COLUMNS_OUT)
    parser.add_argument("--report-out", type=Path, default=DEFAULT_REPORT_OUT)
    parser.add_argument("--concurrency", type=int, default=4, help="Reserved for future batched writes; writes are currently conservative/sequential.")
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args(argv)
    if args.create_only and args.update_only:
        parser.error("--create-only and --update-only cannot be combined")
    if args.ensure_columns_only:
        args.ensure_columns = True
    return args


def main() -> None:
    run(parse_args())


if __name__ == "__main__":
    main()
