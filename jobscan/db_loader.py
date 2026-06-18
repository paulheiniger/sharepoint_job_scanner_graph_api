"""Load scanner JSON outputs into the SprayTec operations Postgres database."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import MetaData, Table, create_engine, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine, make_url


DEFAULT_OUTPUTS = {
    "jobs": Path("output/job_index.json"),
    "estimates": Path("output/estimate_summary.json"),
    "line_items": Path("output/estimate_line_items.json"),
    "job_tracking_summary": Path("output/job_tracking_summary.json"),
    "job_tracking_daily": Path("output/job_tracking_daily_entries.json"),
    "office_timesheets": Path("output/office_timesheet_entries.json"),
    "crew_schedule": Path("output/crew_schedule_candidates.json"),
}


@dataclass(frozen=True)
class DatasetConfig:
    label: str
    table: str
    primary_key: str


DATASETS = {
    "jobs": DatasetConfig("jobs", "jobs", "job_id"),
    "estimates": DatasetConfig("estimates", "estimates", "estimate_id"),
    "line_items": DatasetConfig("estimate line items", "estimate_line_items", "line_item_id"),
    "job_tracking_summary": DatasetConfig("job tracking summaries", "job_tracking_summary", "tracking_id"),
    "job_tracking_daily": DatasetConfig("job tracking daily entries", "job_tracking_daily_entries", "tracking_entry_id"),
    "office_timesheets": DatasetConfig("office timesheet entries", "office_timesheet_entries", "entry_id"),
    "crew_schedule": DatasetConfig("crew schedule candidates", "crew_schedule", "schedule_id"),
}

DEFAULT_UPSERT_BATCH_SIZE = 1000


NUMERIC_TYPES = {"bigint", "double precision", "integer", "numeric", "real", "smallint"}
DATE_TYPES = {"date", "timestamp without time zone", "timestamp with time zone"}
JSON_TYPES = {"json", "jsonb"}
TEXT_TYPES = {"character varying", "text"}

DATE_COLUMNS = {
    "work_date",
    "estimate_date",
    "invoice_date",
    "estimated_start_date",
    "estimated_end_date",
    "actual_first_work_date",
    "actual_last_work_date",
}

BLANK_DATE_VALUES = {"", "nan", "none", "null", "n/a", "na"}

LINE_ITEM_ID_FIELDS = [
    "estimate_id",
    "job_id",
    "estimate_file",
    "source_path",
    "source_sheet",
    "source_row",
    "section",
    "line_item_category",
    "line_item_name",
    "description",
    "quantity",
    "unit",
    "unit_cost",
    "unit_price",
    "extended_cost",
    "labor_days",
    "crew_size",
    "labor_hours",
]

TRACKING_ID_FIELDS = [
    "job_id",
    "tracking_file",
]

TRACKING_ENTRY_ID_FIELDS = [
    "job_id",
    "tracking_file",
    "source_file",
    "source_path",
    "source_sheet",
    "source_row",
    "work_date",
    "crew",
    "notes",
    "labor_hours",
    "travel_hours",
    "load_hours",
    "os_hours",
    "mileage",
    "materials",
]

TIMESHEET_ID_FIELDS = [
    "employee_folder",
    "employee",
    "year",
    "month_folder",
    "source_path",
    "source_file",
    "source_sheet",
    "source_row",
    "work_date",
    "project_name",
    "code",
    "duration_hours",
    "notes",
    "hubspot_notes",
    "additional_notes",
    "row_type",
]

SCHEDULE_ID_FIELDS = [
    "job_id",
    "division",
    "pipeline_status",
    "customer",
    "job_name",
    "folder_path",
    "folder_name",
]


def stable_id(prefix: str, *parts: Any) -> str:
    """Generate a deterministic, compact ID from identifying row parts."""
    normalized = "|".join("" if part is None else str(part).strip() for part in parts)
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:20]}"


def stable_hash_id(prefix: str, row: dict[str, Any], fields: list[str]) -> str:
    """Generate a deterministic ID from ordered row fields."""
    values = []
    for field in fields:
        value = row.get(field)
        values.append("" if value is None else str(value).strip())
    digest = hashlib.sha1("||".join(values).encode("utf-8")).hexdigest()
    return f"{prefix}{digest[:20]}"


def row_with_aliases(row: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
    enriched = dict(row)
    for canonical, alias in aliases.items():
        if not enriched.get(canonical) and alias in row:
            enriched[canonical] = row.get(alias)
    return enriched


def generate_tracking_id(row: dict[str, Any]) -> str:
    return stable_hash_id("tracking-", row, TRACKING_ID_FIELDS)


def ensure_primary_id(dataset_key: str, row: dict[str, Any]) -> dict[str, Any]:
    """Fill missing primary keys with stable IDs based on available source fields."""
    row = dict(row)

    if dataset_key == "jobs" and not row.get("job_id"):
        row["job_id"] = stable_id("job", row.get("folder_path"), row.get("folder_url"), row.get("customer"), row.get("job_name"))

    if dataset_key == "estimates" and not row.get("estimate_id"):
        row["estimate_id"] = stable_id("estimate", row.get("job_id"), row.get("estimate_file"), row.get("source_path"))

    if dataset_key == "line_items":
        if not row.get("estimate_id"):
            row["estimate_id"] = stable_id("estimate", row.get("job_id"), row.get("estimate_file"), row.get("source_path"))
        if not row.get("line_item_id"):
            row["line_item_id"] = stable_hash_id("lineitem-", row, LINE_ITEM_ID_FIELDS)

    if dataset_key == "crew_schedule" and not row.get("schedule_id"):
        row["schedule_id"] = stable_hash_id("schedule-", row, SCHEDULE_ID_FIELDS)

    if dataset_key == "job_tracking_summary" and not row.get("tracking_id"):
        row["tracking_id"] = generate_tracking_id(row)

    if dataset_key == "job_tracking_daily":
        if not row.get("tracking_id"):
            row["tracking_id"] = generate_tracking_id(row)
        if not row.get("tracking_entry_id"):
            row["tracking_entry_id"] = stable_hash_id("trackingentry-", row, TRACKING_ENTRY_ID_FIELDS)

    if dataset_key == "office_timesheets" and not row.get("entry_id"):
        hash_row = row_with_aliases(row, {"employee": "employee_name", "project_name": "project"})
        row["entry_id"] = stable_hash_id("timesheet-", hash_row, TIMESHEET_ID_FIELDS)

    return row


def has_primary_key_value(row: dict[str, Any], primary_key: str) -> bool:
    value = row.get(primary_key)
    return value is not None and str(value).strip() != ""


def primary_key_diagnostics(dataset_key: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    config = DATASETS[dataset_key]
    primary_key = config.primary_key
    present_before = sum(1 for record in records if has_primary_key_value(record, primary_key))
    generated_ids = [
        ensure_primary_id(dataset_key, record).get(primary_key)
        for record in records
    ]
    generated_ids = [str(value).strip() for value in generated_ids if value is not None and str(value).strip()]
    counts = Counter(generated_ids)
    top_duplicates = [(key, count) for key, count in counts.most_common() if count > 1][:10]
    return {
        "rows_read": len(records),
        "primary_key": primary_key,
        "primary_key_present_before_generation": present_before,
        "primary_key_missing_before_generation": len(records) - present_before,
        "primary_key_unique_after_generation": len(counts),
        "duplicate_primary_keys_after_generation": len(generated_ids) - len(counts),
        "top_duplicate_primary_keys": top_duplicates,
    }


def print_primary_key_diagnostics(dataset_key: str, records: list[dict[str, Any]]) -> None:
    diagnostics = primary_key_diagnostics(dataset_key, records)
    print(f"Rows read: {diagnostics['rows_read']}")
    print(f"Primary key: {diagnostics['primary_key']}")
    print(f"Primary key present before generation: {diagnostics['primary_key_present_before_generation']}")
    print(f"Primary key missing before generation: {diagnostics['primary_key_missing_before_generation']}")
    print(f"Primary key unique after generation: {diagnostics['primary_key_unique_after_generation']}")
    print(f"Duplicate primary keys after generation: {diagnostics['duplicate_primary_keys_after_generation']}")
    top_duplicates = diagnostics["top_duplicate_primary_keys"]
    if top_duplicates:
        print("Top duplicate primary keys:")
        for key, count in top_duplicates:
            print(f"  {key}: {count}")
        print("WARNING: duplicate primary keys remain after generation; continuing with upsert.")


def parse_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "t", "yes", "y", "1"}:
            return True
        if normalized in {"false", "f", "no", "n", "0"}:
            return False
    return None


def parse_number(value: Any) -> int | float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        is_percent = cleaned.endswith("%")
        cleaned = cleaned.replace("$", "").replace(",", "").replace("%", "").strip()
        if cleaned.startswith("(") and cleaned.endswith(")"):
            cleaned = f"-{cleaned[1:-1]}"
        try:
            number = float(cleaned)
        except ValueError:
            return None
        if is_percent and abs(number) > 1:
            number = number / 100
        return int(number) if number.is_integer() else number
    return None


def is_blank_date_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str):
        return value.strip().lower() in BLANK_DATE_VALUES
    return False


def clean_date_value(value: Any) -> str | None:
    """Normalize date-ish values for DATE columns, returning None for invalid text."""
    if is_blank_date_value(value):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        return None

    text_value = value.strip()
    if not text_value:
        return None

    try:
        import pandas as pd

        parsed = pd.to_datetime(text_value, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.date().isoformat()
    except Exception:
        try:
            from dateutil import parser as date_parser

            return date_parser.parse(text_value, fuzzy=False).date().isoformat()
        except Exception:
            return None


def normalize_raw_record(record: dict[str, Any]) -> dict[str, Any]:
    """Ensure raw JSONB payloads contain JSON-serializable values."""
    return json.loads(json.dumps(record, default=str))


def coerce_value(value: Any, data_type: str) -> Any:
    if value == "":
        return None
    if data_type == "boolean":
        return parse_bool(value)
    if data_type in NUMERIC_TYPES:
        return parse_number(value)
    if data_type in DATE_TYPES:
        return None if value in (None, "") else value
    if data_type in JSON_TYPES:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        return value
    if data_type in TEXT_TYPES:
        if isinstance(value, (dict, list)):
            return json.dumps(value, default=str)
        return None if value is None else str(value)
    return value


def get_table_columns(conn: Connection, table_name: str) -> dict[str, str]:
    result = conn.execute(
        text(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :table_name
            """
        ),
        {"table_name": table_name},
    )
    return {row.column_name: row.data_type for row in result}


def prepare_row(
    dataset_key: str,
    record: dict[str, Any],
    table_columns: dict[str, str],
    loaded_at: datetime | None = None,
    coercion_stats: dict[str, int] | None = None,
) -> dict[str, Any]:
    loaded_at = loaded_at or datetime.now(timezone.utc)
    source_record = dict(record)
    row = ensure_primary_id(dataset_key, source_record)
    prepared: dict[str, Any] = {}

    for key, value in row.items():
        if key in table_columns:
            if key in DATE_COLUMNS and table_columns[key] in DATE_TYPES:
                cleaned_value = clean_date_value(value)
                if cleaned_value is None and not is_blank_date_value(value) and coercion_stats is not None:
                    coercion_stats["invalid_date_values"] = coercion_stats.get("invalid_date_values", 0) + 1
                prepared[key] = cleaned_value
            else:
                prepared[key] = coerce_value(value, table_columns[key])

    if "raw" in table_columns:
        prepared["raw"] = normalize_raw_record(source_record)
    if "updated_at" in table_columns:
        prepared["updated_at"] = loaded_at

    return prepared


def load_json_records(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        records = None
        for key in ("records", "rows", "data", "items"):
            if isinstance(payload.get(key), list):
                records = payload[key]
                break
        if records is None:
            records = [payload]
    else:
        raise ValueError(f"{path} must contain a JSON list or object")

    return [record for record in records if isinstance(record, dict)]


def reflect_table(engine: Engine, table_name: str) -> Table:
    metadata = MetaData()
    return Table(table_name, metadata, autoload_with=engine)


def upsert_row(conn: Connection, table: Table, primary_key: str, row: dict[str, Any]) -> int:
    if primary_key not in row or row.get(primary_key) in (None, ""):
        return 0

    stmt = pg_insert(table).values(row)
    update_cols = upsert_update_columns(stmt, row, primary_key)
    if update_cols:
        stmt = stmt.on_conflict_do_update(index_elements=[primary_key], set_=update_cols)
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=[primary_key])
    conn.execute(stmt)
    return 1


def upsert_update_columns(stmt: Any, row: dict[str, Any], primary_key: str) -> dict[str, Any]:
    return {key: stmt.excluded[key] for key in row if key != primary_key}


def upsert_rows(conn: Connection, table: Table, primary_key: str, rows: list[dict[str, Any]]) -> int:
    valid_rows = [row for row in rows if primary_key in row and row.get(primary_key) not in (None, "")]
    if not valid_rows:
        return 0
    deduped_by_primary_key = {str(row[primary_key]).strip(): row for row in valid_rows}
    deduped_rows = list(deduped_by_primary_key.values())
    if len(deduped_rows) == 1:
        return upsert_row(conn, table, primary_key, deduped_rows[0])

    stmt = pg_insert(table).values(deduped_rows)
    update_cols = upsert_update_columns(stmt, deduped_rows[0], primary_key)
    if update_cols:
        stmt = stmt.on_conflict_do_update(index_elements=[primary_key], set_=update_cols)
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=[primary_key])
    conn.execute(stmt)
    return len(deduped_rows)


def flush_prepared_batches(
    conn: Connection,
    table: Table,
    primary_key: str,
    prepared_batches: dict[tuple[str, ...], list[dict[str, Any]]],
) -> int:
    upserted = 0
    for rows in prepared_batches.values():
        upserted += upsert_rows(conn, table, primary_key, rows)
    prepared_batches.clear()
    return upserted


def existing_tracking_summary_ids(conn: Connection, tracking_ids: set[str]) -> set[str]:
    if not tracking_ids:
        return set()
    result = conn.execute(
        text("SELECT tracking_id FROM job_tracking_summary WHERE tracking_id = ANY(:tracking_ids)"),
        {"tracking_ids": list(tracking_ids)},
    )
    return {str(row.tracking_id) for row in result}


def minimal_tracking_summary_row(record: dict[str, Any], table_columns: dict[str, str], loaded_at: datetime) -> dict[str, Any]:
    row = ensure_primary_id("job_tracking_daily", record)
    summary = {
        "tracking_id": row.get("tracking_id"),
        "job_id": row.get("job_id"),
        "tracking_file": row.get("tracking_file"),
        "source_file": row.get("source_file"),
        "source_path": row.get("source_path"),
        "raw": normalize_raw_record(record),
        "updated_at": loaded_at,
    }
    return {key: value for key, value in summary.items() if key in table_columns}


def ensure_tracking_summary_parents(
    conn: Connection,
    records: list[dict[str, Any]],
    loaded_at: datetime,
) -> int:
    prepared_records = [ensure_primary_id("job_tracking_daily", record) for record in records]
    tracking_ids = {
        str(record["tracking_id"]).strip()
        for record in prepared_records
        if record.get("tracking_id") is not None and str(record.get("tracking_id")).strip()
    }
    existing_ids = existing_tracking_summary_ids(conn, tracking_ids)
    missing_ids = sorted(tracking_ids - existing_ids)

    print(f"Daily tracking rows: {len(records)}")
    print(f"Unique daily tracking_id values: {len(tracking_ids)}")
    print(f"Tracking IDs missing from job_tracking_summary: {len(missing_ids)}")
    if missing_ids:
        print("First missing tracking_ids:")
        for tracking_id in missing_ids[:10]:
            print(f"  {tracking_id}")

    if not missing_ids:
        return 0

    first_record_by_tracking_id: dict[str, dict[str, Any]] = {}
    for original_record, prepared_record in zip(records, prepared_records):
        tracking_id = prepared_record.get("tracking_id")
        if tracking_id in missing_ids and tracking_id not in first_record_by_tracking_id:
            first_record_by_tracking_id[tracking_id] = original_record

    summary_columns = get_table_columns(conn, DATASETS["job_tracking_summary"].table)
    summary_table = reflect_table(conn.engine, DATASETS["job_tracking_summary"].table)
    created = 0
    for tracking_id in missing_ids:
        source_record = first_record_by_tracking_id.get(tracking_id)
        if not source_record:
            continue
        parent_row = minimal_tracking_summary_row(source_record, summary_columns, loaded_at)
        created += upsert_row(conn, summary_table, DATASETS["job_tracking_summary"].primary_key, parent_row)
    print(f"Minimal job tracking summary parents created: {created}")
    return created


def load_dataset(
    engine: Engine,
    dataset_key: str,
    path: Path,
    *,
    skip_missing: bool = False,
    batch_size: int = DEFAULT_UPSERT_BATCH_SIZE,
) -> tuple[int, int]:
    config = DATASETS[dataset_key]
    if not path.exists():
        message = f"Skipped missing file: {path}"
        if skip_missing:
            print(f"Warning: {message}")
            return 0, 0
        raise FileNotFoundError(message)

    records = load_json_records(path)
    print(f"Loading {config.label}: {path}")
    print_primary_key_diagnostics(dataset_key, records)

    with engine.begin() as conn:
        table_columns = get_table_columns(conn, config.table)
        if not table_columns:
            raise RuntimeError(f"Table not found or has no visible columns: {config.table}")
        table = reflect_table(engine, config.table)
        upserted = 0
        loaded_at = datetime.now(timezone.utc)
        coercion_stats: dict[str, int] = {}
        if dataset_key == "job_tracking_daily":
            ensure_tracking_summary_parents(conn, records, loaded_at)
        prepared_batches: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        for index, record in enumerate(records, start=1):
            prepared = prepare_row(dataset_key, record, table_columns, loaded_at, coercion_stats)
            column_signature = tuple(prepared.keys())
            prepared_batches.setdefault(column_signature, []).append(prepared)
            pending_rows = sum(len(rows) for rows in prepared_batches.values())
            if pending_rows >= batch_size:
                upserted += flush_prepared_batches(conn, table, config.primary_key, prepared_batches)
                print(f"Rows upserted so far: {upserted}/{len(records)}")
        upserted += flush_prepared_batches(conn, table, config.primary_key, prepared_batches)

    print(f"Rows upserted: {upserted}")
    invalid_date_values = coercion_stats.get("invalid_date_values", 0)
    if invalid_date_values:
        print(f"Invalid date values coerced to null: {invalid_date_values}")
    return len(records), upserted


def get_database_url() -> str:
    load_dotenv()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set. Add it to .env or the shell environment.")
    return database_url


def create_db_engine(database_url: str) -> Engine:
    return create_engine(database_url, future=True)


def print_connection_summary(database_url: str) -> None:
    url = make_url(database_url)
    host = url.host or "localhost"
    port = f":{url.port}" if url.port else ""
    database = url.database or ""
    print(f"Connected database target: {host}{port}/{database}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load scanner JSON outputs into Postgres.")
    parser.add_argument("--jobs", type=Path, help="Path to output/job_index.json")
    parser.add_argument("--estimates", type=Path, help="Path to output/estimate_summary.json")
    parser.add_argument("--line-items", type=Path, help="Path to output/estimate_line_items.json")
    parser.add_argument("--job-tracking-summary", type=Path, help="Path to output/job_tracking_summary.json")
    parser.add_argument("--job-tracking-daily", type=Path, help="Path to output/job_tracking_daily_entries.json")
    parser.add_argument("--office-timesheets", type=Path, help="Path to output/office_timesheet_entries.json")
    parser.add_argument("--crew-schedule", type=Path, help="Path to output/crew_schedule_candidates.json")
    parser.add_argument("--all", action="store_true", help="Load all default output JSON files, skipping missing files.")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_UPSERT_BATCH_SIZE, help="Rows per batched upsert statement.")
    return parser.parse_args(argv)


def selected_inputs(args: argparse.Namespace) -> list[tuple[str, Path, bool]]:
    selections: list[tuple[str, Path, bool]] = []
    if args.all:
        selections.extend((key, path, True) for key, path in DEFAULT_OUTPUTS.items())

    explicit = {
        "jobs": args.jobs,
        "estimates": args.estimates,
        "line_items": args.line_items,
        "job_tracking_summary": args.job_tracking_summary,
        "job_tracking_daily": args.job_tracking_daily,
        "office_timesheets": args.office_timesheets,
        "crew_schedule": args.crew_schedule,
    }
    selections.extend((key, path, False) for key, path in explicit.items() if path is not None)
    return selections


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    selections = selected_inputs(args)
    if not selections:
        print("No inputs selected. Use --all or pass one or more JSON file options.")
        return 2

    database_url = get_database_url()
    print_connection_summary(database_url)
    engine = create_db_engine(database_url)

    total_read = 0
    total_upserted = 0
    batch_size = max(args.batch_size, 1)
    for dataset_key, path, skip_missing in selections:
        read_count, upserted_count = load_dataset(engine, dataset_key, path, skip_missing=skip_missing, batch_size=batch_size)
        total_read += read_count
        total_upserted += upserted_count

    print(f"Total rows read: {total_read}")
    print(f"Total rows upserted: {total_upserted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
