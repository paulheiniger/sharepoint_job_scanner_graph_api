from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.engine import Engine

from jobscan.business.job_service import (
    MAX_JOB_SEARCH_RESULTS,
    JobIntelligenceUnavailableError,
    _available_fields,
    _columns,
    _dedupe_links,
    _query_rows,
    _resolve_engine,
    _utc_now,
)


OFFICE_RELATION = "office_timesheet_entries"
MAX_OFFICE_WINDOW_DAYS = 92
MAX_OFFICE_ROLLUP_ROWS = 25

OFFICE_FIELDS = (
    "entry_id",
    "employee",
    "work_date",
    "job_id",
    "project_name",
    "code",
    "duration_hours",
    "row_type",
    "start_time",
    "end_time",
    "milestone",
    "next_action",
    "next_action_owner",
    "next_action_due",
    "notes",
    "source_file",
    "source_file_path",
    "source_app",
    "warnings",
    "updated_at",
)


def get_office_activity(
    *,
    database_url: str | None = None,
    engine: Engine | None = None,
    employee: str = "",
    code: str = "",
    project_query: str = "",
    start_date: date | None = None,
    end_date: date | None = None,
    timed_only: bool = False,
    limit: int = 10,
) -> dict[str, Any]:
    resolved_engine, owns_engine = _resolve_engine(database_url, engine)
    try:
        columns = _columns(resolved_engine, OFFICE_RELATION)
        if "entry_id" not in columns:
            raise JobIntelligenceUnavailableError(
                "The office timesheet source is unavailable."
            )
        resolved_start, resolved_end = _date_window(start_date, end_date)
        conditions, params = _activity_filters(
            columns,
            employee=employee,
            code=code,
            project_query=project_query,
            start_date=resolved_start,
            end_date=resolved_end,
            timed_only=timed_only,
        )
        where_sql = " WHERE " + " AND ".join(conditions) if conditions else ""
        source_summary = _source_summary(resolved_engine, columns)
        summary = _matching_summary(
            resolved_engine,
            columns,
            where_sql,
            params,
        )
        applied_limit = max(1, min(int(limit), MAX_JOB_SEARCH_RESULTS))
        records = _activity_records(
            resolved_engine,
            columns,
            where_sql,
            params,
            applied_limit,
        )
        employee_rollup = _rollup(
            resolved_engine,
            where_sql,
            params,
            expression="COALESCE(NULLIF(TRIM(employee), ''), 'Unknown')",
            label="employee",
        )
        code_rollup = _rollup(
            resolved_engine,
            where_sql,
            params,
            expression="COALESCE(NULLIF(TRIM(code), ''), 'Unknown')",
            label="code",
        )
        project_rollup = _rollup(
            resolved_engine,
            where_sql,
            params,
            expression="COALESCE(NULLIF(TRIM(project_name), ''), '(blank)')",
            label="project_name",
        )
        daily_rollup = _daily_rollup(
            resolved_engine,
            where_sql,
            params,
        )
        warnings = _activity_warnings(summary, source_summary, timed_only)
        as_of = source_summary.get("source_updated_at") or _utc_now()
        return {
            "schema_version": "spraytec.office_activity.v1",
            "as_of": as_of,
            "filters_applied": {
                "employee": employee.strip() or None,
                "code": code.strip() or None,
                "project_query": project_query.strip() or None,
                "start_date": resolved_start.isoformat(),
                "end_date": resolved_end.isoformat(),
                "timed_only": timed_only,
                "limit": applied_limit,
            },
            "headline_metrics": {
                "activity_entries": summary["activity_entries"],
                "total_hours": summary["total_hours"],
                "timed_entries": summary["timed_entries"],
                "activity_only_entries": summary["activity_only_entries"],
                "employee_count": summary["employee_count"],
                "project_count": summary["project_count"],
                "code_count": summary["code_count"],
                "direct_job_id_entries": summary["direct_job_id_entries"],
                "warning_entries": summary["warning_entries"],
            },
            "employee_rollup": employee_rollup,
            "code_rollup": code_rollup,
            "project_rollup": project_rollup,
            "daily_rollup": daily_rollup,
            "records": records,
            "attention_items": _activity_attention_items(
                summary,
                source_summary,
            ),
            "source_links": _activity_source_links(records),
            "source_tables": [
                OFFICE_RELATION,
                *(
                    ["sharepoint_drive_items"]
                    if _columns(resolved_engine, "sharepoint_drive_items")
                    else []
                ),
            ],
            "data_freshness": {
                "source_updated_at": source_summary.get("source_updated_at"),
                "latest_source_work_date": source_summary.get(
                    "latest_source_work_date"
                ),
                "latest_matching_work_date": summary.get(
                    "latest_matching_work_date"
                ),
            },
            "coverage": {
                "source_total_rows": source_summary["source_total_rows"],
                "matching_rows": summary["activity_entries"],
                "result_limit": applied_limit,
                "results_truncated": summary["activity_entries"] > applied_limit,
                "project_rollup_limit": MAX_OFFICE_ROLLUP_ROWS,
                "project_rollup_truncated": summary["project_count"]
                > MAX_OFFICE_ROLLUP_ROWS,
                "direct_job_id_entries": summary["direct_job_id_entries"],
                "direct_job_id_coverage_pct": _percentage(
                    summary["direct_job_id_entries"],
                    summary["activity_entries"],
                ),
                "source_file_url_records": sum(
                    bool(str(row.get("source_file_url") or "").strip())
                    for row in records
                ),
                "invalid_or_missing_work_dates_in_source": source_summary[
                    "invalid_or_missing_work_dates"
                ],
                "employee_identity_is_source_text": True,
                "project_names_are_source_labels": True,
            },
            "warnings": warnings,
            "response_budget": {
                "max_records": MAX_JOB_SEARCH_RESULTS,
                "max_rollup_rows": MAX_OFFICE_ROLLUP_ROWS,
                "max_window_days": MAX_OFFICE_WINDOW_DAYS,
                "returned_records": len(records),
            },
        }
    finally:
        if owns_engine:
            resolved_engine.dispose()


def _date_window(
    start_date: date | None,
    end_date: date | None,
) -> tuple[date, date]:
    resolved_end = end_date or date.today()
    resolved_start = start_date or (resolved_end - timedelta(days=6))
    if resolved_end < resolved_start:
        raise ValueError("end_date must be on or after start_date.")
    if (resolved_end - resolved_start).days + 1 > MAX_OFFICE_WINDOW_DAYS:
        raise ValueError(
            f"Office activity windows may not exceed {MAX_OFFICE_WINDOW_DAYS} days."
        )
    return resolved_start, resolved_end


def _activity_filters(
    columns: set[str],
    *,
    employee: str,
    code: str,
    project_query: str,
    start_date: date,
    end_date: date,
    timed_only: bool,
) -> tuple[list[str], dict[str, Any]]:
    conditions = ["work_date >= :start_date", "work_date <= :end_date"]
    params: dict[str, Any] = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }
    if employee.strip() and "employee" in columns:
        conditions.append("LOWER(TRIM(COALESCE(employee, ''))) = :employee")
        params["employee"] = employee.strip().lower()
    if code.strip() and "code" in columns:
        conditions.append("LOWER(TRIM(COALESCE(code, ''))) = :code")
        params["code"] = code.strip().lower()
    if project_query.strip() and "project_name" in columns:
        conditions.append(
            "LOWER(COALESCE(project_name, '')) LIKE :project_query"
        )
        params["project_query"] = f"%{project_query.strip().lower()}%"
    if timed_only and "duration_hours" in columns:
        conditions.append("COALESCE(duration_hours, 0) > 0")
    return conditions, params


def _source_summary(engine: Engine, columns: set[str]) -> dict[str, Any]:
    updated_expr = "MAX(updated_at)" if "updated_at" in columns else "NULL"
    date_expr = "MAX(work_date)" if "work_date" in columns else "NULL"
    invalid_expr = (
        "SUM(CASE WHEN work_date IS NULL OR work_date < '2000-01-01' "
        "THEN 1 ELSE 0 END)"
        if "work_date" in columns
        else "COUNT(*)"
    )
    rows = _query_rows(
        engine,
        text(
            f"""
            SELECT
                COUNT(*) AS source_total_rows,
                {updated_expr} AS source_updated_at,
                {date_expr} AS latest_source_work_date,
                {invalid_expr} AS invalid_or_missing_work_dates
            FROM {OFFICE_RELATION}
            """
        ),
    )
    return rows[0] if rows else {
        "source_total_rows": 0,
        "source_updated_at": None,
        "latest_source_work_date": None,
        "invalid_or_missing_work_dates": 0,
    }


def _matching_summary(
    engine: Engine,
    columns: set[str],
    where_sql: str,
    params: Mapping[str, Any],
) -> dict[str, Any]:
    duration = "COALESCE(duration_hours, 0)" if "duration_hours" in columns else "0"
    row_type = "LOWER(COALESCE(row_type, ''))" if "row_type" in columns else "''"
    employee = "NULLIF(TRIM(COALESCE(employee, '')), '')" if "employee" in columns else "NULL"
    project = "NULLIF(TRIM(COALESCE(project_name, '')), '')" if "project_name" in columns else "NULL"
    code = "NULLIF(TRIM(COALESCE(code, '')), '')" if "code" in columns else "NULL"
    job_id = "NULLIF(TRIM(COALESCE(job_id, '')), '')" if "job_id" in columns else "NULL"
    warnings = "NULLIF(TRIM(COALESCE(warnings, '')), '')" if "warnings" in columns else "NULL"
    latest_date = "MAX(work_date)" if "work_date" in columns else "NULL"
    rows = _query_rows(
        engine,
        text(
            f"""
            SELECT
                COUNT(*) AS activity_entries,
                COALESCE(SUM({duration}), 0) AS total_hours,
                SUM(CASE WHEN {duration} > 0 THEN 1 ELSE 0 END) AS timed_entries,
                SUM(CASE WHEN {row_type} = 'activity_only' THEN 1 ELSE 0 END)
                    AS activity_only_entries,
                COUNT(DISTINCT {employee}) AS employee_count,
                COUNT(DISTINCT {project}) AS project_count,
                COUNT(DISTINCT {code}) AS code_count,
                SUM(CASE WHEN {job_id} IS NOT NULL THEN 1 ELSE 0 END)
                    AS direct_job_id_entries,
                SUM(CASE WHEN {warnings} IS NOT NULL THEN 1 ELSE 0 END)
                    AS warning_entries,
                {latest_date} AS latest_matching_work_date
            FROM {OFFICE_RELATION}
            {where_sql}
            """
        ),
        params,
    )
    row = rows[0] if rows else {}
    return {
        "activity_entries": int(row.get("activity_entries") or 0),
        "total_hours": round(float(row.get("total_hours") or 0), 2),
        "timed_entries": int(row.get("timed_entries") or 0),
        "activity_only_entries": int(row.get("activity_only_entries") or 0),
        "employee_count": int(row.get("employee_count") or 0),
        "project_count": int(row.get("project_count") or 0),
        "code_count": int(row.get("code_count") or 0),
        "direct_job_id_entries": int(row.get("direct_job_id_entries") or 0),
        "warning_entries": int(row.get("warning_entries") or 0),
        "latest_matching_work_date": row.get("latest_matching_work_date"),
    }


def _rollup(
    engine: Engine,
    where_sql: str,
    params: Mapping[str, Any],
    *,
    expression: str,
    label: str,
) -> list[dict[str, Any]]:
    rows = _query_rows(
        engine,
        text(
            f"""
            SELECT
                {expression} AS {label},
                COUNT(*) AS touch_count,
                COALESCE(SUM(COALESCE(duration_hours, 0)), 0) AS total_hours,
                SUM(CASE WHEN COALESCE(duration_hours, 0) > 0 THEN 1 ELSE 0 END)
                    AS timed_entries,
                COUNT(DISTINCT NULLIF(TRIM(COALESCE(project_name, '')), ''))
                    AS project_count,
                COUNT(DISTINCT NULLIF(TRIM(COALESCE(employee, '')), ''))
                    AS employee_count,
                MIN(work_date) AS first_touch,
                MAX(work_date) AS last_touch
            FROM {OFFICE_RELATION}
            {where_sql}
            GROUP BY {expression}
            ORDER BY touch_count DESC, total_hours DESC
            LIMIT {MAX_OFFICE_ROLLUP_ROWS}
            """
        ),
        params,
    )
    for row in rows:
        row["total_hours"] = round(float(row.get("total_hours") or 0), 2)
    return rows


def _daily_rollup(
    engine: Engine,
    where_sql: str,
    params: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows = _query_rows(
        engine,
        text(
            f"""
            SELECT
                work_date AS activity_date,
                COUNT(*) AS touch_count,
                COALESCE(SUM(COALESCE(duration_hours, 0)), 0) AS total_hours,
                COUNT(DISTINCT NULLIF(TRIM(COALESCE(employee, '')), ''))
                    AS employee_count,
                COUNT(DISTINCT NULLIF(TRIM(COALESCE(project_name, '')), ''))
                    AS project_count
            FROM {OFFICE_RELATION}
            {where_sql}
            GROUP BY work_date
            ORDER BY work_date
            """
        ),
        params,
    )
    for row in rows:
        row["total_hours"] = round(float(row.get("total_hours") or 0), 2)
    return rows


def _activity_records(
    engine: Engine,
    columns: set[str],
    where_sql: str,
    params: Mapping[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    selected = _available_fields(columns, OFFICE_FIELDS)
    select_sql = ", ".join(f"t.{field}" for field in selected)
    drive_columns = _columns(engine, "sharepoint_drive_items")
    source_url_expr = "NULL"
    if (
        {"source_drive_id", "source_drive_item_id"}.issubset(columns)
        and {"drive_id", "drive_item_id", "web_url"}.issubset(drive_columns)
    ):
        source_url_expr = (
            "(SELECT MAX(s.web_url) FROM sharepoint_drive_items s "
            "WHERE s.drive_id = t.source_drive_id "
            "AND s.drive_item_id = t.source_drive_item_id)"
        )
    statement_params = {**params, "result_limit": limit}
    return _query_rows(
        engine,
        text(
            f"""
            SELECT {select_sql}, {source_url_expr} AS source_file_url
            FROM {OFFICE_RELATION} t
            {where_sql}
            ORDER BY t.work_date DESC NULLS LAST, t.updated_at DESC NULLS LAST
            LIMIT :result_limit
            """
        ),
        statement_params,
    )


def _activity_source_links(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    links = []
    for row in records:
        url = str(row.get("source_file_url") or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        links.append(
            {
                "source_type": "office_timesheet",
                "job_id": str(row.get("job_id") or ""),
                "label": str(row.get("source_file") or "Office timesheet"),
                "url": url,
            }
        )
    return _dedupe_links(links)


def _activity_warnings(
    summary: Mapping[str, Any],
    source_summary: Mapping[str, Any],
    timed_only: bool,
) -> list[str]:
    warnings: list[str] = []
    if summary.get("activity_entries") and not summary.get(
        "direct_job_id_entries"
    ):
        warnings.append(
            "No matching office activity is linked by authoritative job_id; project names are source labels and must not be presented as confirmed job matches."
        )
    if summary.get("activity_only_entries") and not timed_only:
        warnings.append(
            "Activity-only rows are touches, not worked hours; use total_hours only for captured durations."
        )
    if source_summary.get("invalid_or_missing_work_dates"):
        warnings.append(
            "Some source rows have missing or invalid work dates and are excluded from normal date-window answers."
        )
    return warnings


def _activity_attention_items(
    summary: Mapping[str, Any],
    source_summary: Mapping[str, Any],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if summary.get("activity_entries") and not summary.get(
        "direct_job_id_entries"
    ):
        items.append(
            {
                "type": "missing_job_id_coverage",
                "severity": "warning",
                "message": "Matching office activity has no authoritative job_id links.",
            }
        )
    invalid = int(source_summary.get("invalid_or_missing_work_dates") or 0)
    if invalid:
        items.append(
            {
                "type": "invalid_work_dates",
                "severity": "warning",
                "message": f"{invalid} source rows have missing or invalid work dates.",
            }
        )
    return items


def _percentage(numerator: Any, denominator: Any) -> float:
    try:
        denominator_value = float(denominator or 0)
        if denominator_value <= 0:
            return 0.0
        return round((float(numerator or 0) / denominator_value) * 100, 1)
    except (TypeError, ValueError):
        return 0.0
