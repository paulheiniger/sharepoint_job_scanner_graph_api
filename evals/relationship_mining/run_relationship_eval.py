from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


DEFAULT_CHECKS_PATH = Path(__file__).with_name("relationship_checks.json")


def load_checks(path: Path = DEFAULT_CHECKS_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def table_exists(engine: Engine, table_name: str) -> bool:
    return inspect(engine).has_table(table_name)


def table_columns(engine: Engine, table_name: str) -> list[str]:
    if not table_exists(engine, table_name):
        return []
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = :table_name
                    ORDER BY ordinal_position
                    """
                ),
                {"table_name": table_name},
            ).fetchall()
        if rows:
            return [str(row[0]) for row in rows]
    except SQLAlchemyError:
        pass
    return [column["name"] for column in inspect(engine).get_columns(table_name)]


def read_sql(engine: Engine, sql: str, params: dict[str, Any] | None = None) -> pd.DataFrame:
    with engine.connect() as connection:
        return pd.read_sql_query(text(sql), connection, params=params)


def count_rows(engine: Engine, table_name: str) -> int | None:
    if not table_exists(engine, table_name):
        return None
    with engine.connect() as connection:
        return int(connection.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar() or 0)


def package_summary(engine: Engine) -> pd.DataFrame:
    if not table_exists(engine, "job_package_summary"):
        return pd.DataFrame()
    columns = set(table_columns(engine, "job_package_summary"))
    select_parts = ["package", "COUNT(*) AS rows"]
    select_parts.append("COUNT(*) FILTER (WHERE area_sqft IS NOT NULL AND area_sqft > 0) AS rows_with_area" if "area_sqft" in columns else "0 AS rows_with_area")
    select_parts.append("COUNT(*) FILTER (WHERE total_hours IS NOT NULL) AS rows_with_hours" if "total_hours" in columns else "0 AS rows_with_hours")
    select_parts.append("COUNT(*) FILTER (WHERE hours_per_sqft IS NOT NULL) AS rows_with_hours_per_sqft" if "hours_per_sqft" in columns else "0 AS rows_with_hours_per_sqft")
    select_parts.append("COUNT(*) FILTER (WHERE cost_per_sqft IS NOT NULL) AS rows_with_cost_per_sqft" if "cost_per_sqft" in columns else "0 AS rows_with_cost_per_sqft")
    return read_sql(
        engine,
        f"""
        SELECT {", ".join(select_parts)}
        FROM job_package_summary
        GROUP BY package
        ORDER BY rows DESC, package
        """,
    )


STANDARD_ROOFING_LABOR_BUCKETS = {
    "labor_prep",
    "labor_prime",
    "labor_seam_sealer",
    "labor_base",
    "labor_top_coat",
    "labor_details",
    "labor_cleanup",
    "labor_loading",
    "labor_caulk",
}

ROOFING_LABOR_LIKE_PATTERN = (
    "prep|prime|primer|seam|base|top coat|cleanup|clean up|caulk|sealant|setup|set up|safety|"
    "flash|curb|pitch pocket|expansion joint|pocket|granule|infrared|ir scan"
)


def roofing_template_labor_health(engine: Engine) -> dict[str, Any]:
    if not table_exists(engine, "estimate_template_rows"):
        return {}
    columns = set(table_columns(engine, "estimate_template_rows"))
    needed = {"template_type", "template_bucket", "line_item_kind", "row_label", "row_number"}
    if not needed.issubset(columns):
        return {"warning": "estimate_template_rows exists but lacks columns needed for roofing labor health checks."}
    rows = read_sql(
        engine,
        """
        SELECT template_type, template_bucket, line_item_kind, row_label, row_number
        FROM estimate_template_rows
        WHERE COALESCE(template_type, '') = 'roofing'
        """,
    )
    if rows.empty:
        return {"roofing_rows": 0}
    bucket_counts = rows["template_bucket"].fillna("").astype(str).value_counts().to_dict()
    standard_counts = {bucket: int(bucket_counts.get(bucket, 0)) for bucket in sorted(STANDARD_ROOFING_LABOR_BUCKETS)}
    standard_total = sum(standard_counts.values())
    unknown_rows = rows[rows["template_bucket"].fillna("").astype(str).isin({"", "unknown"})].copy()
    unknown_ratio = len(unknown_rows) / len(rows) if len(rows) else 0
    labor_like_unknown = unknown_rows[
        unknown_rows["row_label"].fillna("").astype(str).str.lower().str.contains(ROOFING_LABOR_LIKE_PATTERN, regex=True)
    ]
    top_unmapped = (
        labor_like_unknown["row_label"].fillna("").astype(str).value_counts().head(20).to_dict()
        if not labor_like_unknown.empty
        else {}
    )
    return {
        "roofing_rows": int(len(rows)),
        "standard_roofing_labor_bucket_counts": standard_counts,
        "standard_roofing_labor_bucket_total": int(standard_total),
        "unknown_rows": int(len(unknown_rows)),
        "unknown_ratio": float(unknown_ratio),
        "labor_like_unknown_rows": int(len(labor_like_unknown)),
        "top_unmapped_roofing_labor_labels": top_unmapped,
    }


def diagnostic_files_status(output_dir: Path | None, expected_files: list[str]) -> list[dict[str, Any]]:
    if not output_dir:
        return []
    return [
        {
            "file": filename,
            "exists": (output_dir / filename).exists(),
            "path": str(output_dir / filename),
        }
        for filename in expected_files
    ]


def evaluate_relationships(engine: Engine, checks: dict[str, Any], output_dir: Path | None = None, *, strict: bool = False) -> dict[str, Any]:
    failures: list[str] = []
    warnings: list[str] = []
    row_counts: dict[str, int | None] = {}

    for table in checks.get("required_tables", []):
        if not table_exists(engine, table):
            failures.append(f"Required table missing: {table}")

    for table in checks.get("row_count_tables", []):
        row_counts[table] = count_rows(engine, table)

    required_columns = checks.get("job_package_summary_required_columns", [])
    actual_columns = table_columns(engine, "job_package_summary") if table_exists(engine, "job_package_summary") else []
    for column in required_columns:
        if column not in actual_columns:
            failures.append(f"job_package_summary missing required column: {column}")

    jps_count = row_counts.get("job_package_summary")
    if jps_count is not None and jps_count <= 0:
        failures.append("job_package_summary has 0 rows")

    packages = package_summary(engine)
    package_rows = packages.to_dict(orient="records") if not packages.empty else []
    if not packages.empty:
        package_names = set(packages["package"].dropna().astype(str))
        generic_packages = set(checks.get("generic_packages", []))
        specific_packages = package_names - generic_packages
        if not specific_packages:
            warnings.append("No specific packages found beyond generic labor/materials/misc/travel.")
        recommended_missing = sorted(set(checks.get("recommended_packages", [])) - package_names)
        if recommended_missing:
            warnings.append(f"Recommended packages not present in this run: {', '.join(recommended_missing)}")

        total = int(packages["rows"].sum())
        generic_total = int(packages[packages["package"].isin(generic_packages)]["rows"].sum()) if total else 0
        if total and generic_total / total > 0.8:
            warnings.append(f"Generic package rows dominate package summary: {generic_total}/{total}")

        labor_with_hours_area = packages[
            packages["package"].astype(str).str.startswith("labor")
            & (pd.to_numeric(packages["rows_with_hours"], errors="coerce") > 0)
            & (pd.to_numeric(packages["rows_with_area"], errors="coerce") > 0)
        ]
        if row_counts.get("relationship_labor_rates") == 0 and not labor_with_hours_area.empty:
            warnings.append("relationship_labor_rates = 0 while job_package_summary has labor packages with hours and area.")

        material_with_area = packages[
            ~packages["package"].astype(str).str.startswith("labor")
            & (pd.to_numeric(packages["rows_with_area"], errors="coerce") > 0)
            & (pd.to_numeric(packages["rows_with_cost_per_sqft"], errors="coerce") > 0)
        ]
        if row_counts.get("relationship_material_qty_ratios") == 0 and not material_with_area.empty:
            warnings.append("relationship_material_qty_ratios = 0 while material packages have cost/area evidence.")

    template_type_warning = None
    if table_exists(engine, "job_package_summary") and "template_type" in actual_columns:
        template_counts = read_sql(
            engine,
            """
            SELECT COALESCE(NULLIF(template_type, ''), 'null') AS template_type, COUNT(*) AS rows
            FROM job_package_summary
            GROUP BY COALESCE(NULLIF(template_type, ''), 'null')
            """,
        )
        total = int(template_counts["rows"].sum()) if not template_counts.empty else 0
        null_rows = int(template_counts.loc[template_counts["template_type"].eq("null"), "rows"].sum()) if total else 0
        if total and null_rows / total > 0.5:
            template_type_warning = f"template_type mostly null: {null_rows}/{total}"
            warnings.append(template_type_warning)

    roofing_labor_health = roofing_template_labor_health(engine)
    if roofing_labor_health:
        health_warning = roofing_labor_health.get("warning")
        if health_warning:
            warnings.append(str(health_warning))
        roofing_rows = int(roofing_labor_health.get("roofing_rows") or 0)
        standard_total = int(roofing_labor_health.get("standard_roofing_labor_bucket_total") or 0)
        unknown_ratio = float(roofing_labor_health.get("unknown_ratio") or 0)
        labor_like_unknown = int(roofing_labor_health.get("labor_like_unknown_rows") or 0)
        if roofing_rows and standard_total < max(5, roofing_rows * 0.02):
            message = f"Roofing template rows exist but standard roofing labor buckets are very low: {standard_total}/{roofing_rows}"
            (failures if strict else warnings).append(message)
        if roofing_rows and unknown_ratio > 0.5:
            message = f"Unknown rows dominate estimate_template_rows for roofing: {roofing_labor_health.get('unknown_rows')}/{roofing_rows}"
            (failures if strict else warnings).append(message)
        if labor_like_unknown > 0:
            labels = roofing_labor_health.get("top_unmapped_roofing_labor_labels") or {}
            message = f"Found {labor_like_unknown} roofing labor-like labels that are unmapped: {labels}"
            (failures if strict else warnings).append(message)

    diagnostics = diagnostic_files_status(output_dir, checks.get("diagnostic_files", []))
    for row in diagnostics:
        if not row["exists"]:
            warnings.append(f"Expected diagnostic file missing: {row['file']}")

    return {
        "passed": not failures,
        "failures": failures,
        "warnings": warnings,
        "row_counts": row_counts,
        "package_summary": package_rows,
        "roofing_labor_health": roofing_labor_health,
        "diagnostic_files": diagnostics,
    }


def print_report(report: dict[str, Any]) -> None:
    print("Relationship mining eval:", "PASS" if report["passed"] else "FAIL")
    print("Row counts:")
    for table, count in report["row_counts"].items():
        print(f"  {table}: {'missing' if count is None else count}")
    if report["package_summary"]:
        print("Package summary:")
        for row in report["package_summary"][:30]:
            print(
                f"  {row.get('package')}: rows={row.get('rows')} area={row.get('rows_with_area')} "
                f"hours={row.get('rows_with_hours')} hrs/sqft={row.get('rows_with_hours_per_sqft')}"
            )
    if report.get("roofing_labor_health"):
        print("Roofing labor health:")
        print(json.dumps(report["roofing_labor_health"], indent=2, default=str))
    for failure in report["failures"]:
        print(f"failure: {failure}")
    for warning in report["warnings"]:
        print(f"warning: {warning}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate relationship mining database outputs.")
    parser.add_argument("--db-url", default=os.getenv("NEON_DATABASE_URL") or os.getenv("DATABASE_URL"))
    parser.add_argument("--checks", type=Path, default=DEFAULT_CHECKS_PATH)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--strict", action="store_true", help="Fail when parser health warnings indicate suspicious relationship inputs.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.db_url:
        print("Set --db-url, NEON_DATABASE_URL, or DATABASE_URL to run relationship mining eval.")
        return 1
    checks = load_checks(args.checks)
    try:
        engine = create_engine(args.db_url, future=True)
        report = evaluate_relationships(engine, checks, args.output_dir, strict=args.strict)
    except Exception as exc:
        print(f"Relationship mining eval failed to run: {type(exc).__name__}: {exc}")
        return 1
    print_report(report)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"JSON report: {args.json_output}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
