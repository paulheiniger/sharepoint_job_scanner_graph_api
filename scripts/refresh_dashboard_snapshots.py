from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text

os.environ.setdefault("STREAMLIT_LOG_LEVEL", "error")
logging.getLogger("streamlit").setLevel(logging.ERROR)

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dashboard import app as dashboard_app


def json_safe_value(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(value, default=str)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def normalize_for_sql(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for column in out.columns:
        if out[column].dtype == "object":
            out[column] = out[column].map(json_safe_value)
    out["snapshot_refreshed_at"] = pd.Timestamp.now("UTC")
    return out


def write_snapshot(df: pd.DataFrame, table_name: str, *, index_columns: list[str]) -> int:
    engine = dashboard_app.get_engine()
    normalized = normalize_for_sql(df)
    with engine.begin() as conn:
        normalized.to_sql(table_name, conn, if_exists="replace", index=False, method="multi", chunksize=500)
        for column in index_columns:
            if column in normalized.columns:
                safe_index_name = f"idx_{table_name}_{column}".replace("-", "_")[:60]
                conn.execute(text(f'CREATE INDEX IF NOT EXISTS "{safe_index_name}" ON "{table_name}" ("{column}")'))
        if "snapshot_refreshed_at" in normalized.columns:
            conn.execute(text(f'CREATE INDEX IF NOT EXISTS "idx_{table_name}_refreshed" ON "{table_name}" ("snapshot_refreshed_at")'))
    return len(normalized)


def refresh_operations_snapshot() -> None:
    all_jobs, ops = dashboard_app.build_operations_dashboard_prepared_live()
    all_count = write_snapshot(
        all_jobs if isinstance(all_jobs, pd.DataFrame) else pd.DataFrame(),
        "operations_dashboard_all_jobs_snapshot",
        index_columns=["job_id", "sales_stage", "division"],
    )
    ops_count = write_snapshot(
        ops if isinstance(ops, pd.DataFrame) else pd.DataFrame(),
        "operations_dashboard_ops_snapshot",
        index_columns=["job_id", "readiness_status", "project_health", "division"],
    )
    print(f"operations_dashboard_all_jobs_snapshot rows: {all_count}")
    print(f"operations_dashboard_ops_snapshot rows: {ops_count}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh Streamlit dashboard snapshot tables.")
    parser.add_argument("--operations", action="store_true", help="Refresh Operations Dashboard prepared snapshots.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.operations:
        refresh_operations_snapshot()
        return
    refresh_operations_snapshot()


if __name__ == "__main__":
    main()
