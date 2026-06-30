from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


MART_NAMES = [
    "mart_jobs",
    "mart_documents",
    "mart_estimate_template_rows",
    "mart_unknown_template_rows",
    "mart_material_history",
    "mart_labor_history",
    "mart_material_defaults",
    "mart_labor_defaults",
    "mart_pricing_catalog",
    "mart_repairs",
    "mart_repair_materials",
    "mart_repair_labor",
    "mart_repair_defaults",
    "mart_quality_warnings",
    "mart_timesheets",
    "mart_estimator_feedback",
    "mart_rule_candidates",
]

VALIDATION_MARTS = [
    "mart_jobs",
    "mart_documents",
    "mart_repairs",
    "mart_repair_jobs",
    "mart_repair_labor",
    "mart_repair_labor_usage",
    "mart_repair_materials",
    "mart_repair_material_usage",
    "mart_repair_defaults",
]

KEY_COLUMNS: dict[str, set[str]] = {
    "mart_jobs": {"job_id", "division", "pipeline_status", "customer", "job_name", "final_price"},
    "mart_documents": {"document_id", "job_id", "file_name", "extraction_status"},
    "mart_estimate_template_rows": {"template_row_id", "job_id", "template_bucket", "line_item_kind"},
    "mart_material_history": {"job_id", "package", "qty_per_sqft", "cost_per_sqft"},
    "mart_labor_history": {"job_id", "package", "total_hours", "hours_per_1000_sqft"},
    "mart_material_defaults": {"package", "median_qty_per_sqft", "job_count", "confidence"},
    "mart_labor_defaults": {"median_hours_per_1000_sqft", "job_count", "confidence"},
    "mart_pricing_catalog": {"pricing_item_id", "product_name", "unit_price", "is_current"},
    "mart_repairs": {"repair_id", "customer", "type_of_repair", "invoice_amount"},
}


@dataclass(frozen=True)
class MartSummaryRow:
    mart_name: str
    row_count: int | None
    status: str
    error: str | None = None


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_sql_path() -> Path:
    return project_root() / "db" / "powerbi_marts.sql"


def create_db_engine(db_url: str) -> Engine:
    return create_engine(db_url, pool_pre_ping=True)


def apply_mart_sql(engine: Engine, sql_path: Path | None = None) -> None:
    path = sql_path or default_sql_path()
    sql = path.read_text(encoding="utf-8")
    # Use the raw driver without bind parameters so PostgreSQL format strings
    # like format('%I.%I::%s AS %I', ...) are not mistaken for DBAPI
    # placeholders by psycopg2.
    raw_connection = engine.raw_connection()
    try:
        with raw_connection.cursor() as cursor:
            cursor.execute(sql)
        raw_connection.commit()
    except Exception:
        raw_connection.rollback()
        raise
    finally:
        raw_connection.close()


def refresh_materialized_marts(engine: Engine) -> list[str]:
    refreshed: list[str] = []
    with engine.begin() as connection:
        rows = connection.execute(
            text(
                """
                SELECT matviewname
                FROM pg_matviews
                WHERE schemaname = 'analytics'
                ORDER BY matviewname
                """
            )
        ).fetchall()
        for row in rows:
            name = row[0]
            connection.execute(text(f'REFRESH MATERIALIZED VIEW analytics."{name}"'))
            refreshed.append(name)
    return refreshed


def mart_exists(engine: Engine, mart_name: str) -> bool:
    with engine.connect() as connection:
        return bool(
            connection.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.views
                        WHERE table_schema = 'analytics'
                          AND table_name = :mart_name
                        UNION ALL
                        SELECT 1
                        FROM pg_matviews
                        WHERE schemaname = 'analytics'
                          AND matviewname = :mart_name
                    )
                    """
                ),
                {"mart_name": mart_name},
            ).scalar()
        )


def select_limit_queries(mart_names: Iterable[str] = MART_NAMES) -> dict[str, str]:
    return {name: f'SELECT * FROM analytics."{name}" LIMIT 1' for name in mart_names}


def count_mart_rows(engine: Engine, mart_name: str) -> int:
    with engine.connect() as connection:
        value = connection.execute(text(f'SELECT COUNT(*) FROM analytics."{mart_name}"')).scalar()
    return int(value or 0)


def summarize_marts(engine: Engine, mart_names: Iterable[str] = MART_NAMES) -> list[MartSummaryRow]:
    rows: list[MartSummaryRow] = []
    for mart_name in mart_names:
        try:
            if not mart_exists(engine, mart_name):
                rows.append(MartSummaryRow(mart_name=mart_name, row_count=None, status="missing"))
                continue
            rows.append(MartSummaryRow(mart_name=mart_name, row_count=count_mart_rows(engine, mart_name), status="ok"))
        except Exception as err:  # pragma: no cover - exercised against real DB.
            rows.append(MartSummaryRow(mart_name=mart_name, row_count=None, status="error", error=str(err)))
    return rows


def validate_marts(engine: Engine, mart_names: Iterable[str] = VALIDATION_MARTS) -> list[MartSummaryRow]:
    rows: list[MartSummaryRow] = []
    for mart_name in mart_names:
        try:
            if not mart_exists(engine, mart_name):
                rows.append(MartSummaryRow(mart_name=mart_name, row_count=None, status="missing"))
                continue
            with engine.connect() as connection:
                connection.execute(text(f'SELECT * FROM analytics."{mart_name}" LIMIT 1')).fetchall()
            rows.append(MartSummaryRow(mart_name=mart_name, row_count=count_mart_rows(engine, mart_name), status="ok"))
        except Exception as err:
            rows.append(MartSummaryRow(mart_name=mart_name, row_count=None, status="error", error=str(err)))
    return rows


def print_validation(engine: Engine) -> bool:
    print("Power BI mart validation")
    print("Schema: analytics")
    print()
    ok = True
    for row in validate_marts(engine):
        if row.status == "ok":
            print(f"{row.mart_name}: ok ({row.row_count} rows)")
        elif row.status == "missing":
            ok = False
            print(f"{row.mart_name}: missing")
        else:
            ok = False
            print(f"{row.mart_name}: error - {row.error}")
    return ok


def role_summary(engine: Engine) -> dict[str, object]:
    with engine.connect() as connection:
        role_exists = bool(
            connection.execute(
                text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'powerbi_reader')")
            ).scalar()
        )
        if not role_exists:
            return {"powerbi_reader_exists": False}
        schema_usage = bool(
            connection.execute(
                text("SELECT has_schema_privilege('powerbi_reader', 'analytics', 'USAGE')")
            ).scalar()
        )
        mart_jobs_select = bool(
            connection.execute(
                text("SELECT has_table_privilege('powerbi_reader', 'analytics.mart_jobs', 'SELECT')")
            ).scalar()
        )
    return {
        "powerbi_reader_exists": True,
        "analytics_schema_usage": schema_usage,
        "mart_jobs_select": mart_jobs_select,
    }


def print_summary(engine: Engine) -> None:
    print("Power BI analytics mart summary")
    print("Schema: analytics")
    print()
    for row in summarize_marts(engine):
        if row.status == "ok":
            print(f"{row.mart_name}: {row.row_count} rows")
        elif row.status == "missing":
            print(f"{row.mart_name}: missing")
        else:
            print(f"{row.mart_name}: error - {row.error}")
    print()
    role = role_summary(engine)
    print("Security:")
    for key, value in role.items():
        print(f"- {key}: {value}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh and inspect Spray-Tec Power BI analytics marts.")
    parser.add_argument("--db-url", default=os.environ.get("NEON_DATABASE_URL"), help="Database URL. Prefer NEON_DATABASE_URL.")
    parser.add_argument("--sql-path", type=Path, default=default_sql_path(), help="Path to db/powerbi_marts.sql.")
    parser.add_argument("--refresh", action="store_true", help="Apply mart SQL and refresh any materialized marts.")
    parser.add_argument("--summary", action="store_true", help="Print mart row counts and reader-role status.")
    parser.add_argument("--validate", action="store_true", help="Run SELECT smoke checks against key Power BI marts.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)
    if not args.db_url:
        raise SystemExit("NEON_DATABASE_URL or --db-url is required.")
    engine = create_db_engine(args.db_url)
    if args.refresh:
        apply_mart_sql(engine, args.sql_path)
        refreshed = refresh_materialized_marts(engine)
        if refreshed:
            print("Refreshed materialized marts:")
            for name in refreshed:
                print(f"- {name}")
        else:
            print("Applied Power BI mart SQL. No materialized marts to refresh.")
    if args.validate:
        if not print_validation(engine):
            return 1
    if args.summary or (not args.refresh and not args.validate):
        print_summary(engine)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
