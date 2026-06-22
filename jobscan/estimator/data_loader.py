from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text

from jobscan.db_connections import create_resilient_engine
from .schemas import DEFAULT_STAGE_FILES, PRICING_CANDIDATES, EstimatorData


TEMPLATE_ROW_COLUMNS = [
    "template_row_id",
    "document_id",
    "job_id",
    "source_file",
    "sheet_name",
    "row_number",
    "template_bucket",
    "template_section",
    "line_item_kind",
    "row_label",
    "selected_item_name",
    "quantity",
    "unit",
    "unit_price",
    "estimated_units",
    "estimated_cost",
    "days",
    "crew_size",
    "total_hours",
    "daily_rate",
    "trips",
    "round_trip_miles",
    "cost_per_mile",
    "warranty_years",
    "overhead_pct",
    "profit_pct",
    "needs_review",
]


def _records_from_json(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        for key in ("rows", "records", "data", "items"):
            rows = value.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    return []


def read_json_dataframe(path: Path) -> pd.DataFrame:
    value = json.loads(path.read_text(encoding="utf-8"))
    return pd.DataFrame(_records_from_json(value))


def read_csv_dataframe(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)


def _load_estimator_data_from_local_files(root: Path) -> EstimatorData:
    root = Path(root)
    data = EstimatorData()

    for attr, relative_path in DEFAULT_STAGE_FILES.items():
        path = root / relative_path
        if not path.exists():
            data.warnings.append(f"Missing staging file: {relative_path}")
            continue
        try:
            setattr(data, attr, read_json_dataframe(path))
            data.source_files_used.append(str(relative_path))
        except Exception as exc:
            data.warnings.append(f"Could not read {relative_path}: {exc}")

    for relative_path in PRICING_CANDIDATES:
        path = root / relative_path
        if not path.exists():
            continue
        try:
            data.pricing = read_csv_dataframe(path)
            data.source_files_used.append(str(relative_path))
            break
        except Exception as exc:
            data.warnings.append(f"Could not read {relative_path}: {exc}")

    if data.pricing.empty:
        data.warnings.append("No current pricing export found.")
    if not data.line_items.empty:
        try:
            from .line_items import classify_line_items

            data.classified_line_items = classify_line_items(data.line_items)
        except Exception as exc:
            data.warnings.append(f"Could not classify local estimate line items: {type(exc).__name__}")
    return data


def _read_sql_dataframe(connection: Any, query: str) -> pd.DataFrame:
    return pd.read_sql_query(text(query), connection)


def relation_columns(connection: Any, relation_name: str) -> list[str]:
    try:
        result = connection.execute(text(f"SELECT * FROM {relation_name} LIMIT 0"))
        return list(result.keys())
    except Exception:
        return []


def relation_exists(connection: Any, relation_name: str) -> bool:
    return bool(relation_columns(connection, relation_name))


def read_relation_columns(connection: Any, relation_name: str, columns: list[str] | None = None, where: str = "") -> pd.DataFrame:
    available = relation_columns(connection, relation_name)
    if not available:
        return pd.DataFrame()
    selected = [column for column in (columns or available) if column in available]
    if not selected:
        return pd.DataFrame()
    sql = f"SELECT {', '.join(selected)} FROM {relation_name} {where}".strip()
    return _read_sql_dataframe(connection, sql)


def load_current_pricing(connection: Any, data: EstimatorData) -> pd.DataFrame:
    columns = relation_columns(connection, "pricing_catalog")
    if not columns:
        data.warnings.append("pricing_catalog table not found; current material pricing is unavailable.")
        return pd.DataFrame()
    where = "WHERE is_current = true" if "is_current" in columns else ""
    try:
        pricing = _read_sql_dataframe(connection, f"SELECT * FROM pricing_catalog {where}".strip())
    except Exception as exc:
        data.warnings.append(f"Could not load pricing_catalog: {type(exc).__name__}")
        return pd.DataFrame()
    data.source_files_used.append("database: pricing_catalog")
    return pricing


def load_estimator_data_from_database(database_url: str) -> EstimatorData:
    engine = create_resilient_engine(database_url)
    data = EstimatorData()
    with engine.connect() as connection:
        if relation_exists(connection, "dashboard_jobs"):
            data.jobs = _read_sql_dataframe(connection, "SELECT * FROM dashboard_jobs")
            data.source_files_used.append("database: dashboard_jobs")
        elif relation_exists(connection, "jobs"):
            data.jobs = _read_sql_dataframe(connection, "SELECT * FROM jobs")
            data.source_files_used.append("database: jobs")
        else:
            data.warnings.append("dashboard_jobs/jobs table not found; similar-job matching is limited.")

        if relation_exists(connection, "estimates"):
            data.estimates = _read_sql_dataframe(connection, "SELECT * FROM estimates")
            data.source_files_used.append("database: estimates")
        else:
            data.warnings.append("estimates table not found; estimate summary history is unavailable.")

        if relation_exists(connection, "estimate_line_items"):
            data.line_items = _read_sql_dataframe(connection, "SELECT * FROM estimate_line_items")
            data.source_files_used.append("database: estimate_line_items")
        else:
            data.warnings.append("estimate_line_items table not found; using estimate_template_rows only.")

        if relation_exists(connection, "estimate_template_rows"):
            data.template_rows = read_relation_columns(connection, "estimate_template_rows", TEMPLATE_ROW_COLUMNS)
            data.source_files_used.append("database: estimate_template_rows")
        else:
            data.warnings.append(
                "estimate_template_rows table not found; run python -m jobscan.estimator.template_rows --parse-existing."
            )

        if relation_exists(connection, "estimate_line_item_classifications"):
            data.classified_line_items = _read_sql_dataframe(connection, "SELECT * FROM estimate_line_item_classifications")
            data.line_item_classifications = data.classified_line_items
            data.source_files_used.append("database: estimate_line_item_classifications")
        else:
            data.warnings.append("estimate_line_item_classifications table not found; using estimate_template_rows only")

        if relation_exists(connection, "job_tracking_summary"):
            data.tracking_summary = _read_sql_dataframe(connection, "SELECT * FROM job_tracking_summary")
            data.source_files_used.append("database: job_tracking_summary")

        if relation_exists(connection, "job_tracking_daily_entries"):
            data.tracking_daily = _read_sql_dataframe(connection, "SELECT * FROM job_tracking_daily_entries")
            data.source_files_used.append("database: job_tracking_daily_entries")

        data.pricing_catalog = load_current_pricing(connection, data)
        data.pricing = data.pricing_catalog
    if not data.source_files_used:
        raise RuntimeError("No estimator database tables were available.")
    data.source_files_used.append("Postgres database")
    if data.template_rows.empty:
        data.warnings.append(
            "estimate_template_rows is empty; run python -m jobscan.estimator.template_rows --parse-existing."
        )
    if data.pricing_catalog.empty:
        data.warnings.append("pricing_catalog is empty; current material pricing is limited.")
    return data


def load_estimator_data(
    base_dir: Path | str | None = None,
    database_url: str | None = None,
    *,
    prefer_database: bool = False,
) -> EstimatorData:
    root = Path(base_dir or Path.cwd())
    resolved_database_url = database_url or os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL")
    if resolved_database_url:
        try:
            return load_estimator_data_from_database(resolved_database_url)
        except Exception as exc:
            data = _load_estimator_data_from_local_files(root)
            data.warnings.insert(0, f"Database estimator load failed; using local staging files. ({type(exc).__name__})")
            return data
    return _load_estimator_data_from_local_files(root)
