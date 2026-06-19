from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text

from jobscan.db_connections import create_resilient_engine
from .schemas import DEFAULT_STAGE_FILES, PRICING_CANDIDATES, EstimatorData


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


def load_estimator_data_from_database(database_url: str) -> EstimatorData:
    engine = create_resilient_engine(database_url)
    data = EstimatorData()
    queries = {
        "jobs": "SELECT * FROM jobs",
        "estimates": "SELECT * FROM estimates",
        "line_items": "SELECT * FROM estimate_line_items",
        "template_rows": "SELECT * FROM estimate_template_rows",
        "classified_line_items": "SELECT * FROM estimate_line_item_classifications",
        "tracking_summary": "SELECT * FROM job_tracking_summary",
        "tracking_daily": "SELECT * FROM job_tracking_daily_entries",
        "pricing": "SELECT * FROM pricing_catalog WHERE COALESCE(is_current, true) = true",
    }
    with engine.connect() as connection:
        for attr, query in queries.items():
            setattr(data, attr, _read_sql_dataframe(connection, query))
    data.source_files_used.append("Postgres database")
    if data.template_rows.empty:
        data.warnings.append(
            "estimate_template_rows is empty; run python -m jobscan.estimator.template_rows --parse-existing."
        )
    if data.classified_line_items.empty:
        data.warnings.append(
            "estimate_line_item_classifications is empty; run python -m jobscan.estimator.line_items --classify-existing."
        )
    return data


def load_estimator_data(
    base_dir: Path | str | None = None,
    database_url: str | None = None,
    *,
    prefer_database: bool = False,
) -> EstimatorData:
    root = Path(base_dir or Path.cwd())
    resolved_database_url = database_url or os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL")
    if prefer_database and resolved_database_url:
        try:
            return load_estimator_data_from_database(resolved_database_url)
        except Exception:
            data = _load_estimator_data_from_local_files(root)
            data.warnings.insert(0, "Database estimator load failed; using local staging files.")
            return data
    return _load_estimator_data_from_local_files(root)
