from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable

from dotenv import load_dotenv
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from jobscan.business.job_service import (
    JobIntelligenceUnavailableError,
    _resolve_engine,
)
from jobscan.business.operations_service import get_operations_backlog
from jobscan.business.production_budget_service import get_production_budget_health
from jobscan.business.sales_service import get_sales_pipeline


CHART_HISTORY_RELATION = "reporting_chart_daily_snapshots"
MAX_HISTORY_DAYS = 365
DEFAULT_HISTORY_DAYS = 90


@dataclass(frozen=True)
class HistoryCaptureSpec:
    source_dataset: str
    loader: Callable[..., dict[str, Any]]
    metric_fields: tuple[tuple[str, str], ...]
    truth_class: str = "authoritative"


CAPTURE_SPECS = (
    HistoryCaptureSpec(
        "sales_pipeline_by_stage",
        get_sales_pipeline,
        (("pipeline_value", "pipeline_value"), ("job_count", "job_count")),
    ),
    HistoryCaptureSpec(
        "operations_backlog_by_division",
        get_operations_backlog,
        (("backlog_value", "backlog_value"), ("backlog_jobs", "job_count")),
    ),
    HistoryCaptureSpec(
        "production_budget_by_bucket",
        get_production_budget_health,
        (
            ("estimated_production_budget", "estimated_production_budget"),
            ("estimated_cost_used_proxy", "estimated_cost_used_proxy"),
            ("jobs_usage_over_plan", "jobs_usage_over_plan"),
        ),
        "proxy",
    ),
)

HISTORY_DATASETS = {
    "sales_pipeline_history": "sales_pipeline_by_stage",
    "operations_backlog_history": "operations_backlog_by_division",
    "production_budget_history": "production_budget_by_bucket",
}


def ensure_chart_history_table(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {CHART_HISTORY_RELATION} (
                    snapshot_date DATE NOT NULL,
                    source_dataset TEXT NOT NULL,
                    observation_json TEXT NOT NULL,
                    source_as_of TEXT,
                    truth_class TEXT NOT NULL,
                    captured_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (snapshot_date, source_dataset)
                )
                """
            )
        )
        connection.execute(
            text(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{CHART_HISTORY_RELATION}_dataset_date
                ON {CHART_HISTORY_RELATION}(source_dataset, snapshot_date)
                """
            )
        )


def capture_daily_chart_history(
    *,
    database_url: str | None = None,
    engine: Engine | None = None,
    snapshot_date: date | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    resolved_engine, owns_engine = _resolve_engine(database_url, engine)
    effective_date = snapshot_date or date.today()
    try:
        observations = [
            _capture_observation(resolved_engine, effective_date, spec)
            for spec in CAPTURE_SPECS
        ]
        if not dry_run:
            ensure_chart_history_table(resolved_engine)
            with resolved_engine.begin() as connection:
                for observation in observations:
                    connection.execute(
                        text(
                            f"""
                            INSERT INTO {CHART_HISTORY_RELATION} (
                                snapshot_date,
                                source_dataset,
                                observation_json,
                                source_as_of,
                                truth_class,
                                captured_at
                            ) VALUES (
                                :snapshot_date,
                                :source_dataset,
                                :observation_json,
                                :source_as_of,
                                :truth_class,
                                CURRENT_TIMESTAMP
                            )
                            ON CONFLICT (snapshot_date, source_dataset) DO UPDATE SET
                                observation_json = excluded.observation_json,
                                source_as_of = excluded.source_as_of,
                                truth_class = excluded.truth_class,
                                captured_at = CURRENT_TIMESTAMP
                            """
                        ),
                        {
                            "snapshot_date": effective_date.isoformat(),
                            "source_dataset": observation["source_dataset"],
                            "observation_json": json.dumps(
                                observation,
                                default=str,
                                separators=(",", ":"),
                            ),
                            "source_as_of": observation.get("source_as_of"),
                            "truth_class": observation["truth_class"],
                        },
                    )
        return {
            "snapshot_date": effective_date.isoformat(),
            "dry_run": dry_run,
            "datasets_considered": len(CAPTURE_SPECS),
            "datasets_written": 0 if dry_run else len(observations),
            "observations": observations,
        }
    finally:
        if owns_engine:
            resolved_engine.dispose()


def get_chart_history(
    dataset: str,
    *,
    database_url: str | None = None,
    engine: Engine | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
    if dataset not in HISTORY_DATASETS:
        raise ValueError(f"Unsupported historical chart dataset: {dataset}")
    resolved_engine, owns_engine = _resolve_engine(database_url, engine)
    effective_end = end_date or date.today()
    effective_start = start_date or (
        effective_end - timedelta(days=DEFAULT_HISTORY_DAYS - 1)
    )
    if effective_end < effective_start:
        raise ValueError("end_date must be on or after start_date.")
    requested_days = (effective_end - effective_start).days + 1
    if requested_days > MAX_HISTORY_DAYS:
        raise ValueError(
            f"Historical chart windows are limited to {MAX_HISTORY_DAYS} days."
        )
    try:
        source_dataset = HISTORY_DATASETS[dataset]
        records = []
        relation_available = inspect(resolved_engine).has_table(
            CHART_HISTORY_RELATION
        )
        if relation_available:
            with resolved_engine.connect() as connection:
                records = connection.execute(
                    text(
                        f"""
                        SELECT snapshot_date, observation_json, captured_at
                        FROM {CHART_HISTORY_RELATION}
                        WHERE source_dataset = :source_dataset
                          AND snapshot_date BETWEEN :start_date AND :end_date
                        ORDER BY snapshot_date
                        """
                    ),
                    {
                        "source_dataset": source_dataset,
                        "start_date": effective_start.isoformat(),
                        "end_date": effective_end.isoformat(),
                    },
                ).mappings().all()
        rows: list[dict[str, Any]] = []
        source_as_of_values: list[str] = []
        truth_classes: set[str] = set()
        warnings: list[str] = []
        latest_capture: str | None = None
        for record in records:
            observation = json.loads(str(record["observation_json"]))
            snapshot_value = record["snapshot_date"]
            snapshot_text = (
                snapshot_value.isoformat()
                if hasattr(snapshot_value, "isoformat")
                else str(snapshot_value)
            )
            rows.append({"snapshot_date": snapshot_text, **observation["metrics"]})
            if observation.get("source_as_of"):
                source_as_of_values.append(str(observation["source_as_of"]))
            truth_classes.add(str(observation.get("truth_class") or "authoritative"))
            latest_capture = str(record["captured_at"])
            warnings.extend(str(value) for value in observation.get("warnings") or [])
        if len(rows) < 2:
            warnings.append(
                "Fewer than two daily observations are available; a trend cannot "
                "yet be established."
            )
        if not relation_available:
            warnings.append(
                "Daily chart history storage has not been initialized. Run the "
                "scheduled chart-history capture before requesting a trend."
            )
        return {
            "schema_version": "spraytec.chart_history.v1",
            "as_of": max(source_as_of_values, default=latest_capture),
            "truth_class": (
                next(iter(truth_classes)) if len(truth_classes) == 1 else "mixed"
            ),
            "filters_applied": {
                "start_date": effective_start.isoformat(),
                "end_date": effective_end.isoformat(),
            },
            "records": rows,
            "source_tables": [CHART_HISTORY_RELATION],
            "data_freshness": {"history_last_captured_at": latest_capture},
            "staging": {
                "aggregation_mode": "staged_daily_snapshot",
                "source_storage": "append_only_history",
                "snapshot_tables": [CHART_HISTORY_RELATION],
                "freshness": {"history_last_captured_at": latest_capture},
                "historical_series_available": len(rows) >= 2,
                "historical_limitation": (
                    "Daily observations begin when this history capture is deployed; "
                    "earlier current-state snapshots cannot be reconstructed."
                ),
            },
            "coverage": {
                "requested_calendar_days": requested_days,
                "available_snapshot_days": len(rows),
                "first_snapshot_date": rows[0]["snapshot_date"] if rows else None,
                "last_snapshot_date": rows[-1]["snapshot_date"] if rows else None,
            },
            "warnings": list(dict.fromkeys(warnings)),
        }
    finally:
        if owns_engine:
            resolved_engine.dispose()


def chart_history_status(
    *,
    database_url: str | None = None,
    engine: Engine | None = None,
) -> list[dict[str, Any]]:
    resolved_engine, owns_engine = _resolve_engine(database_url, engine)
    try:
        if not inspect(resolved_engine).has_table(CHART_HISTORY_RELATION):
            return []
        with resolved_engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    text(
                        f"""
                        SELECT
                            source_dataset,
                            COUNT(*) AS snapshot_days,
                            MIN(snapshot_date) AS first_snapshot_date,
                            MAX(snapshot_date) AS last_snapshot_date,
                            MAX(captured_at) AS last_captured_at
                        FROM {CHART_HISTORY_RELATION}
                        GROUP BY source_dataset
                        ORDER BY source_dataset
                        """
                    )
                ).mappings()
            ]
    finally:
        if owns_engine:
            resolved_engine.dispose()


def _capture_observation(
    engine: Engine,
    snapshot_date: date,
    spec: HistoryCaptureSpec,
) -> dict[str, Any]:
    result = spec.loader(engine=engine, limit=25)
    headline = result.get("headline_metrics") or {}
    return {
        "schema_version": "spraytec.chart_history_observation.v1",
        "snapshot_date": snapshot_date.isoformat(),
        "source_dataset": spec.source_dataset,
        "source_as_of": result.get("as_of"),
        "truth_class": result.get("truth_class") or spec.truth_class,
        "metrics": {
            output_field: headline.get(source_field)
            for source_field, output_field in spec.metric_fields
        },
        "source_tables": result.get("source_tables") or [],
        "data_freshness": result.get("data_freshness") or {},
        "coverage": result.get("coverage") or {},
        "warnings": result.get("warnings") or [],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture or inspect append-only daily chart history."
    )
    parser.add_argument("--database-url", default="")
    parser.add_argument("--snapshot-date", type=date.fromisoformat)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--status", action="store_true")
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()
    database_url = (
        args.database_url
        or os.getenv("DATABASE_URL")
        or os.getenv("NEON_DATABASE_URL")
    )
    if not database_url:
        raise JobIntelligenceUnavailableError("A database URL is required.")
    if args.status:
        print(json.dumps(chart_history_status(database_url=database_url), default=str))
        return 0
    result = capture_daily_chart_history(
        database_url=database_url,
        snapshot_date=args.snapshot_date,
        dry_run=args.dry_run,
    )
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "snapshot_date",
                    "dry_run",
                    "datasets_considered",
                    "datasets_written",
                )
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
