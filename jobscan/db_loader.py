"""Load scanner JSON outputs into the SprayTec operations Postgres database."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
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


NUMERIC_TYPES = {"bigint", "double precision", "integer", "numeric", "real", "smallint"}
DATE_TYPES = {"date", "timestamp without time zone", "timestamp with time zone"}
JSON_TYPES = {"json", "jsonb"}
TEXT_TYPES = {"character varying", "text"}
def stable_id(prefix: str, *parts: Any) -> str:
    """Generate a deterministic, compact ID from identifying row parts."""
    normalized = "|".join("" if part is None else str(part).strip() for part in parts)
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:20]}"


def _first_nonblank(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


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
            row["line_item_id"] = stable_id(
                "line-item",
                row.get("estimate_id"),
                row.get("source_sheet"),
                row.get("source_row"),
                row.get("line_item_name"),
            )

    if dataset_key == "crew_schedule" and not row.get("schedule_id"):
        row["schedule_id"] = row.get("job_id") or stable_id("schedule", row.get("folder_path"), row.get("job_name"))

    if dataset_key == "job_tracking_summary" and not row.get("tracking_id"):
        row["tracking_id"] = stable_id("tracking", row.get("job_id"), _first_nonblank(row, "tracking_file", "source_file", "source_path"))

    if dataset_key == "job_tracking_daily":
        if not row.get("tracking_id"):
            row["tracking_id"] = stable_id("tracking", row.get("job_id"), _first_nonblank(row, "tracking_file", "source_file", "source_path"))
        if not row.get("tracking_entry_id"):
            row["tracking_entry_id"] = stable_id(
                "tracking-entry",
                row.get("tracking_id"),
                row.get("source_sheet"),
                row.get("source_row"),
                row.get("work_date"),
            )

    if dataset_key == "office_timesheets" and not row.get("entry_id"):
        row["entry_id"] = stable_id(
            "timesheet",
            row.get("source_file"),
            row.get("source_sheet"),
            row.get("source_row"),
            row.get("employee"),
            row.get("work_date"),
        )

    return row


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
) -> dict[str, Any]:
    loaded_at = loaded_at or datetime.now(timezone.utc)
    source_record = dict(record)
    row = ensure_primary_id(dataset_key, source_record)
    prepared: dict[str, Any] = {}

    for key, value in row.items():
        if key in table_columns:
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
    update_cols = {key: stmt.excluded[key] for key in row if key != primary_key}
    if update_cols:
        stmt = stmt.on_conflict_do_update(index_elements=[primary_key], set_=update_cols)
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=[primary_key])
    conn.execute(stmt)
    return 1


def load_dataset(engine: Engine, dataset_key: str, path: Path, *, skip_missing: bool = False) -> tuple[int, int]:
    config = DATASETS[dataset_key]
    if not path.exists():
        message = f"Skipped missing file: {path}"
        if skip_missing:
            print(f"Warning: {message}")
            return 0, 0
        raise FileNotFoundError(message)

    records = load_json_records(path)
    print(f"Loading {config.label}: {path}")
    print(f"Rows read: {len(records)}")

    with engine.begin() as conn:
        table_columns = get_table_columns(conn, config.table)
        if not table_columns:
            raise RuntimeError(f"Table not found or has no visible columns: {config.table}")
        table = reflect_table(engine, config.table)
        upserted = 0
        loaded_at = datetime.now(timezone.utc)
        for record in records:
            prepared = prepare_row(dataset_key, record, table_columns, loaded_at)
            upserted += upsert_row(conn, table, config.primary_key, prepared)

    print(f"Rows upserted: {upserted}")
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
    for dataset_key, path, skip_missing in selections:
        read_count, upserted_count = load_dataset(engine, dataset_key, path, skip_missing=skip_missing)
        total_read += read_count
        total_upserted += upserted_count

    print(f"Total rows read: {total_read}")
    print(f"Total rows upserted: {total_upserted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
