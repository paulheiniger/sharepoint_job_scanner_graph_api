from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Iterable, Mapping

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

from jobscan.business.job_service import (
    MAX_JOB_SEARCH_RESULTS,
    JobIntelligenceUnavailableError,
    _available_fields,
    _columns,
    _dedupe_links,
    _job_relation,
    _job_source_links,
    _latest_value,
    _query_rows,
    _resolve_engine,
    _select_present,
    _sum_numeric,
    _unique_nonblank,
    _utc_now,
)


MAX_OPERATIONS_SOURCE_ROWS = 500
OPERATIONS_RELATION = "operations_dashboard_ops_snapshot"

OPERATIONS_FIELDS = (
    "job_id",
    "source_year",
    "division",
    "pipeline_status",
    "status",
    "customer",
    "job_name",
    "job_type",
    "project_category",
    "operations_value",
    "estimated_value",
    "estimated_sqft",
    "price_per_sqft",
    "readiness_status",
    "ready_date",
    "days_waiting",
    "has_job_spec",
    "assigned_crew_leader",
    "estimated_start_date",
    "estimated_end_date",
    "estimated_duration_days",
    "estimated_labor_hours",
    "estimated_crew_size",
    "schedule_status",
    "schedule_health",
    "blocking_issue",
    "priority",
    "schedule_notes",
    "tracking_status",
    "first_work_date",
    "last_work_date",
    "estimated_total_hours",
    "actual_total_hours",
    "actual_labor_hours",
    "labor_hours_used_pct",
    "expected_pct_complete",
    "actual_pct_complete",
    "actual_pct_source",
    "progress_delta_pct",
    "project_health",
    "production_risk_summary",
    "material_readiness",
    "equipment_readiness",
    "customer_communication",
    "has_warnings",
    "warnings",
    "folder_link_or_path",
    "folder_url",
    "folder_path",
    "snapshot_refreshed_at",
    "updated_at",
)

PRODUCTION_RISK_HEALTH = {
    "Behind expected progress",
    "Labor overrun risk",
    "Material overrun risk",
}


def get_operations_backlog(
    *,
    database_url: str | None = None,
    engine: Engine | None = None,
    division: str = "",
    job_year: int | None = None,
    readiness_statuses: list[str] | None = None,
    unscheduled_only: bool = False,
    needs_attention: bool | None = None,
    include_completed: bool = False,
    limit: int = 10,
) -> dict[str, Any]:
    resolved_engine, owns_engine = _resolve_engine(database_url, engine)
    try:
        rows = _load_operations_rows(
            resolved_engine,
            division=division,
            readiness_statuses=readiness_statuses or [],
            include_completed=include_completed,
        )
        rows = _filter_operations_job_year(resolved_engine, rows, job_year)
        if unscheduled_only:
            rows = [row for row in rows if not _is_scheduled(row)]
        if needs_attention is not None:
            rows = [
                row
                for row in rows
                if _needs_backlog_attention(row) is needs_attention
            ]
        rows.sort(
            key=lambda row: (
                _needs_backlog_attention(row),
                _number(row.get("operations_value")),
            ),
            reverse=True,
        )
        applied_limit = _bounded_limit(limit)
        selected_rows = rows[:applied_limit]
        as_of = _operations_as_of(rows)
        return {
            "schema_version": "spraytec.operations_backlog.v1",
            "as_of": as_of or _utc_now(),
            "filters_applied": {
                key: value
                for key, value in {
                    "division": division.strip() or None,
                    "job_year": job_year,
                    "readiness_statuses": _unique_nonblank(
                        readiness_statuses or []
                    )
                    or None,
                    "unscheduled_only": unscheduled_only,
                    "needs_attention": needs_attention,
                    "include_completed": include_completed,
                    "limit": applied_limit,
                }.items()
                if value is not None
            },
            "headline_metrics": {
                "backlog_jobs": len(rows),
                "backlog_value": _sum_numeric(
                    row.get("operations_value") for row in rows
                ),
                "scheduled_jobs": sum(_is_scheduled(row) for row in rows),
                "unscheduled_jobs": sum(not _is_scheduled(row) for row in rows),
                "ready_to_schedule_jobs": sum(
                    row.get("readiness_status") == "Ready To Schedule"
                    for row in rows
                ),
                "missing_job_spec_jobs": sum(
                    row.get("readiness_status") == "Missing Job Spec"
                    for row in rows
                ),
                "jobs_needing_attention": sum(
                    _needs_backlog_attention(row) for row in rows
                ),
                "unassigned_scheduled_jobs": sum(
                    _is_scheduled(row)
                    and not str(row.get("assigned_crew_leader") or "").strip()
                    for row in rows
                ),
            },
            "readiness_rollup": _category_rollup(
                rows,
                "readiness_status",
                label="readiness_status",
                include_waiting=True,
            ),
            "division_rollup": _category_rollup(
                rows,
                "division",
                label="division",
            ),
            "records": [_operations_record(row) for row in selected_rows],
            "attention_items": _operations_attention_items(rows)[:25],
            "source_links": _source_links(selected_rows),
            "source_tables": [OPERATIONS_RELATION],
            "data_freshness": {"operations_snapshot_as_of": as_of},
            "coverage": _coverage(rows, applied_limit),
            "warnings": _coverage_warnings(rows),
            "response_budget": {
                "max_records": MAX_JOB_SEARCH_RESULTS,
                "returned_records": len(selected_rows),
            },
        }
    finally:
        if owns_engine:
            resolved_engine.dispose()


def get_operations_schedule(
    *,
    database_url: str | None = None,
    engine: Engine | None = None,
    division: str = "",
    crew_leader: str = "",
    job_year: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    risk_only: bool = False,
    include_unscheduled: bool = False,
    include_completed: bool = False,
    limit: int = 10,
    max_records: int = MAX_JOB_SEARCH_RESULTS,
) -> dict[str, Any]:
    resolved_engine, owns_engine = _resolve_engine(database_url, engine)
    try:
        rows = _load_operations_rows(
            resolved_engine,
            division=division,
            include_completed=include_completed,
        )
        rows = _filter_operations_job_year(resolved_engine, rows, job_year)
        if crew_leader.strip():
            crew_key = crew_leader.strip().lower()
            rows = [
                row
                for row in rows
                if str(row.get("assigned_crew_leader") or "").strip().lower()
                == crew_key
            ]
        source_rows = list(rows)
        requested_start = start_date
        requested_end = end_date
        if not risk_only and requested_start is None and requested_end is None:
            requested_start = date.today()
            requested_end = requested_start + timedelta(days=14)
        if requested_start and requested_end and requested_end < requested_start:
            raise ValueError("end_date must be on or after start_date.")
        if requested_start or requested_end:
            rows = [
                row
                for row in rows
                if _in_schedule_window(
                    row,
                    start_date=requested_start,
                    end_date=requested_end,
                    include_unscheduled=include_unscheduled,
                )
            ]
        elif not include_unscheduled and not risk_only:
            rows = [row for row in rows if _scheduled_start(row) is not None]
        if risk_only:
            rows = [row for row in rows if _has_production_risk(row)]
        rows.sort(key=_schedule_sort_key)
        applied_limit = _bounded_limit(limit, maximum=max_records)
        selected_rows = rows[:applied_limit]
        as_of = _operations_as_of(source_rows)
        source_schedule_dates = [
            scheduled
            for row in source_rows
            if (scheduled := _scheduled_start(row)) is not None
        ]
        scheduled_outside_window = 0
        if requested_start or requested_end:
            scheduled_outside_window = sum(
                not _in_schedule_window(
                    row,
                    start_date=requested_start,
                    end_date=requested_end,
                    include_unscheduled=False,
                )
                for row in source_rows
                if _scheduled_start(row) is not None
            )
        zero_result_reason = None
        if not rows:
            zero_result_reason = (
                "no_active_production_risks"
                if risk_only
                else "no_jobs_match_requested_schedule_window"
            )
        return {
            "schema_version": "spraytec.operations_schedule.v1",
            "as_of": as_of or _utc_now(),
            "filters_applied": {
                key: value
                for key, value in {
                    "division": division.strip() or None,
                    "crew_leader": crew_leader.strip() or None,
                    "job_year": job_year,
                    "start_date": requested_start.isoformat()
                    if requested_start
                    else None,
                    "end_date": requested_end.isoformat() if requested_end else None,
                    "risk_only": risk_only,
                    "include_unscheduled": include_unscheduled,
                    "include_completed": include_completed,
                    "limit": applied_limit,
                }.items()
                if value is not None
            },
            "headline_metrics": {
                "matching_jobs": len(rows),
                "scheduled_value": _sum_numeric(
                    row.get("operations_value") for row in rows
                ),
                "assigned_jobs": sum(
                    bool(str(row.get("assigned_crew_leader") or "").strip())
                    for row in rows
                ),
                "unassigned_jobs": sum(
                    not str(row.get("assigned_crew_leader") or "").strip()
                    for row in rows
                ),
                "behind_or_blocked_jobs": sum(
                    row.get("schedule_health") == "Behind / Blocked"
                    or bool(str(row.get("blocking_issue") or "").strip())
                    for row in rows
                ),
                "production_risk_jobs": sum(
                    _has_production_risk(row) for row in rows
                ),
                "labor_overrun_risk_jobs": sum(
                    row.get("project_health") == "Labor overrun risk"
                    for row in rows
                ),
                "material_overrun_risk_jobs": sum(
                    row.get("project_health") == "Material overrun risk"
                    for row in rows
                ),
            },
            "schedule_health_rollup": _category_rollup(
                rows,
                "schedule_health",
                label="schedule_health",
            ),
            "project_health_rollup": _category_rollup(
                rows,
                "project_health",
                label="project_health",
            ),
            "crew_rollup": _category_rollup(
                rows,
                "assigned_crew_leader",
                label="crew_leader",
                blank_label="Unassigned",
            ),
            "records": [_operations_record(row) for row in selected_rows],
            "attention_items": _operations_attention_items(rows)[:25],
            "source_links": _source_links(selected_rows),
            "source_tables": [OPERATIONS_RELATION],
            "data_freshness": {
                "operations_snapshot_as_of": as_of,
                "freshness_preserved_when_no_records_match": True,
            },
            "coverage": {
                **_coverage(rows, applied_limit),
                "source_total_jobs": len(source_rows),
                "matching_window_jobs": len(rows),
                "scheduled_outside_window": scheduled_outside_window,
                "past_start_date_jobs": sum(
                    scheduled < date.today() for scheduled in source_schedule_dates
                ),
                "latest_scheduled_start": max(source_schedule_dates).isoformat()
                if source_schedule_dates
                else None,
                "zero_result_reason": zero_result_reason,
            },
            "warnings": [
                *_coverage_warnings(rows),
                *(
                    [
                        "No jobs match the requested schedule window; this does not mean the operations source is empty."
                    ]
                    if zero_result_reason
                    == "no_jobs_match_requested_schedule_window"
                    else []
                ),
            ],
            "response_budget": {
                "max_records": max_records,
                "returned_records": len(selected_rows),
            },
        }
    finally:
        if owns_engine:
            resolved_engine.dispose()


def _load_operations_rows(
    engine: Engine,
    *,
    division: str = "",
    readiness_statuses: list[str] | None = None,
    include_completed: bool,
) -> list[dict[str, Any]]:
    columns = _columns(engine, OPERATIONS_RELATION)
    if "job_id" not in columns:
        raise JobIntelligenceUnavailableError(
            "The prepared operations snapshot is unavailable."
        )
    selected = _available_fields(columns, OPERATIONS_FIELDS)
    conditions: list[str] = []
    params: dict[str, Any] = {"source_limit": MAX_OPERATIONS_SOURCE_ROWS}
    if division.strip() and "division" in columns:
        conditions.append("LOWER(COALESCE(division, '')) = :division")
        params["division"] = division.strip().lower()
    statuses = _unique_nonblank(readiness_statuses or [])
    if statuses and "readiness_status" in columns:
        conditions.append("readiness_status IN :readiness_statuses")
        params["readiness_statuses"] = statuses
    if not include_completed and "schedule_health" in columns:
        conditions.append(
            "LOWER(COALESCE(schedule_health, '')) <> 'completed'"
        )
    sql = f"SELECT {', '.join(selected)} FROM {OPERATIONS_RELATION}"
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " LIMIT :source_limit"
    statement = text(sql)
    if "readiness_statuses" in params:
        statement = statement.bindparams(
            bindparam("readiness_statuses", expanding=True)
        )
    return _query_rows(engine, statement, params)


def _filter_operations_job_year(
    engine: Engine,
    rows: list[dict[str, Any]],
    job_year: int | None,
) -> list[dict[str, Any]]:
    if job_year is None or not rows:
        return rows
    if not all(str(row.get("source_year") or "").strip() for row in rows):
        relation, columns = _job_relation(engine)
        if "source_year" not in columns:
            raise JobIntelligenceUnavailableError(
                "The job source does not expose source_year for job-year filtering."
            )
        job_ids = _unique_nonblank(row.get("job_id") for row in rows)
        statement = text(
            f"SELECT job_id, source_year FROM {relation} WHERE job_id IN :job_ids"
        ).bindparams(bindparam("job_ids", expanding=True))
        years_by_job = {
            str(item["job_id"]): item.get("source_year")
            for item in _query_rows(engine, statement, {"job_ids": job_ids})
        }
        for row in rows:
            if not str(row.get("source_year") or "").strip():
                row["source_year"] = years_by_job.get(str(row.get("job_id") or ""))
    requested = str(job_year)
    return [
        row
        for row in rows
        if str(row.get("source_year") or "").strip() == requested
    ]


def _operations_record(row: Mapping[str, Any]) -> dict[str, Any]:
    fields = tuple(
        field
        for field in OPERATIONS_FIELDS
        if field not in {"folder_url", "folder_path", "snapshot_refreshed_at"}
    )
    record = _select_present(row, fields)
    record["attention_items"] = _row_attention_items(row)
    return record


def _category_rollup(
    rows: Iterable[Mapping[str, Any]],
    field: str,
    *,
    label: str,
    blank_label: str = "Not Captured",
    include_waiting: bool = False,
) -> list[dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        value = str(row.get(field) or "").strip() or blank_label
        groups[value].append(row)
    result: list[dict[str, Any]] = []
    for value, grouped in groups.items():
        item: dict[str, Any] = {
            label: value,
            "jobs": len(grouped),
            "value": _sum_numeric(
                row.get("operations_value") for row in grouped
            ),
        }
        if include_waiting:
            waiting = [
                _number(row.get("days_waiting"))
                for row in grouped
                if row.get("days_waiting") not in (None, "")
            ]
            item["average_days_waiting"] = (
                round(sum(waiting) / len(waiting), 1) if waiting else None
            )
        result.append(item)
    return sorted(
        result,
        key=lambda item: (item["value"], item["jobs"]),
        reverse=True,
    )


def _operations_attention_items(
    rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    items = [item for row in rows for item in _row_attention_items(row)]
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    return sorted(
        items,
        key=lambda item: (
            severity_order.get(str(item.get("severity")), 3),
            -_number(item.get("operations_value")),
        ),
    )


def _row_attention_items(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    job_id = str(row.get("job_id") or "")
    value = _number(row.get("operations_value"))
    items: list[dict[str, Any]] = []
    if row.get("readiness_status") == "Missing Job Spec":
        items.append(
            _attention_item(
                "missing_job_spec",
                "Job specification is missing.",
                job_id,
                value,
            )
        )
    if row.get("readiness_status") == "Not Contracted Folder":
        items.append(
            _attention_item(
                "folder_status_mismatch",
                "Contracted pipeline status does not match the folder classification.",
                job_id,
                value,
            )
        )
    blocker = str(row.get("blocking_issue") or "").strip()
    if blocker:
        items.append(
            _attention_item(
                "schedule_blocker",
                blocker,
                job_id,
                value,
                severity="critical",
            )
        )
    project_health = str(row.get("project_health") or "").strip()
    if project_health in PRODUCTION_RISK_HEALTH:
        message = (
            str(row.get("production_risk_summary") or "").strip()
            or project_health
        )
        items.append(
            _attention_item(
                "production_risk",
                message,
                job_id,
                value,
                severity="critical",
            )
        )
    if _is_scheduled(row) and not str(
        row.get("assigned_crew_leader") or ""
    ).strip():
        items.append(
            _attention_item(
                "missing_crew_leader",
                "Scheduled job has no captured crew leader.",
                job_id,
                value,
            )
        )
    warning = str(row.get("warnings") or "").strip()
    if warning:
        items.append(
            _attention_item("source_warning", warning, job_id, value)
        )
    return items


def _attention_item(
    item_type: str,
    message: str,
    job_id: str,
    operations_value: float,
    *,
    severity: str = "warning",
) -> dict[str, Any]:
    return {
        "type": item_type,
        "severity": severity,
        "message": message,
        "job_id": job_id,
        "operations_value": operations_value,
    }


def _needs_backlog_attention(row: Mapping[str, Any]) -> bool:
    return bool(_row_attention_items(row)) or row.get("readiness_status") not in {
        "Ready To Schedule",
        "Scheduled",
    }


def _has_production_risk(row: Mapping[str, Any]) -> bool:
    blocker_on_active_work = bool(
        str(row.get("blocking_issue") or "").strip()
    ) and (
        _is_scheduled(row)
        or str(row.get("tracking_status") or "").strip() == "Recently touched"
    )
    return (
        str(row.get("project_health") or "") in PRODUCTION_RISK_HEALTH
        or str(row.get("schedule_health") or "") == "Behind / Blocked"
        or blocker_on_active_work
    )


def _is_scheduled(row: Mapping[str, Any]) -> bool:
    return (
        row.get("readiness_status") == "Scheduled"
        or _scheduled_start(row) is not None
    )


def _scheduled_start(row: Mapping[str, Any]) -> date | None:
    return _parse_date(row.get("estimated_start_date"))


def _scheduled_end(row: Mapping[str, Any]) -> date | None:
    explicit = _parse_date(row.get("estimated_end_date"))
    if explicit is not None:
        return explicit
    start = _scheduled_start(row)
    if start is None:
        return None
    duration = max(1, int(_number(row.get("estimated_duration_days")) or 1))
    return start + timedelta(days=duration - 1)


def _in_schedule_window(
    row: Mapping[str, Any],
    *,
    start_date: date | None,
    end_date: date | None,
    include_unscheduled: bool,
) -> bool:
    scheduled_start = _scheduled_start(row)
    if scheduled_start is None:
        return include_unscheduled
    scheduled_end = _scheduled_end(row) or scheduled_start
    if start_date and scheduled_end < start_date:
        return False
    if end_date and scheduled_start > end_date:
        return False
    return True


def _schedule_sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    scheduled = _scheduled_start(row)
    return (
        0 if _has_production_risk(row) else 1,
        scheduled or date.max,
        -_number(row.get("operations_value")),
    )


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    normalized = str(value or "").strip()
    if not normalized:
        return None
    try:
        return date.fromisoformat(normalized[:10])
    except ValueError:
        return None


def _source_links(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return _dedupe_links(
        link for row in rows for link in _job_source_links(row, [])
    )


def _operations_as_of(rows: Iterable[Mapping[str, Any]]) -> str | None:
    return _latest_value(
        row.get(field)
        for row in rows
        for field in ("snapshot_refreshed_at", "updated_at")
    )


def _coverage(rows: list[dict[str, Any]], limit: int) -> dict[str, Any]:
    return {
        "source_row_limit": MAX_OPERATIONS_SOURCE_ROWS,
        "source_rows": len(rows),
        "result_limit": limit,
        "results_truncated": len(rows) > limit,
    }


def _coverage_warnings(rows: list[dict[str, Any]]) -> list[str]:
    if len(rows) >= MAX_OPERATIONS_SOURCE_ROWS:
        return [
            "Operations aggregation reached the bounded source-row limit; narrow filters for a complete total."
        ]
    return []


def _bounded_limit(
    limit: int,
    *,
    maximum: int = MAX_JOB_SEARCH_RESULTS,
) -> int:
    return max(1, min(int(limit), max(1, int(maximum))))


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
