from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any


@dataclass(frozen=True)
class ChartSpec:
    title: str
    source_key: str
    chart_type: str
    category_field: str
    series: tuple[tuple[str, str, str], ...]
    default_truth_class: str = "authoritative"
    group_field: str | None = None
    start_field: str | None = None
    end_field: str | None = None


CHART_SPECS: dict[str, ChartSpec] = {
    "sales_pipeline_by_stage": ChartSpec(
        "Sales pipeline by stage",
        "stage_rollup",
        "bar",
        "pipeline_status",
        (("estimated_value", "Pipeline value", "currency"), ("job_count", "Jobs", "count")),
    ),
    "sales_pipeline_by_owner": ChartSpec(
        "Sales pipeline by owner",
        "owner_rollup",
        "bar",
        "owner",
        (("estimated_value", "Pipeline value", "currency"), ("job_count", "Jobs", "count")),
    ),
    "operations_backlog_by_division": ChartSpec(
        "Contracted backlog by division",
        "division_rollup",
        "bar",
        "division",
        (("value", "Backlog value", "currency"), ("jobs", "Jobs", "count")),
    ),
    "operations_backlog_by_readiness": ChartSpec(
        "Contracted backlog by readiness",
        "readiness_rollup",
        "bar",
        "readiness_status",
        (
            ("value", "Backlog value", "currency"),
            ("jobs", "Jobs", "count"),
            ("average_days_waiting", "Average days waiting", "days"),
        ),
    ),
    "operations_schedule_by_crew": ChartSpec(
        "Scheduled workload by crew leader",
        "crew_rollup",
        "bar",
        "crew_leader",
        (("value", "Scheduled value", "currency"), ("jobs", "Jobs", "count")),
    ),
    "operations_schedule_by_health": ChartSpec(
        "Scheduled work by schedule health",
        "schedule_health_rollup",
        "bar",
        "schedule_health",
        (("value", "Scheduled value", "currency"), ("jobs", "Jobs", "count")),
    ),
    "operations_schedule_gantt": ChartSpec(
        "Scheduled projects by crew leader",
        "records",
        "gantt",
        "task_label",
        (("display_duration_days", "Scheduled days in view", "days"),),
        group_field="crew_leader",
        start_field="display_start_date",
        end_field="display_end_date",
    ),
    "office_activity_by_day": ChartSpec(
        "Office activity by day",
        "daily_rollup",
        "line",
        "activity_date",
        (("total_hours", "Captured hours", "hours"), ("touch_count", "Touches", "count")),
        "mixed",
    ),
    "office_activity_by_employee": ChartSpec(
        "Office activity by employee",
        "employee_rollup",
        "bar",
        "employee",
        (("total_hours", "Captured hours", "hours"), ("touch_count", "Touches", "count")),
        "mixed",
    ),
    "office_activity_by_code": ChartSpec(
        "Office activity by work code",
        "code_rollup",
        "bar",
        "code",
        (("total_hours", "Captured hours", "hours"), ("touch_count", "Touches", "count")),
        "mixed",
    ),
    "office_job_progress": ChartSpec(
        "Office activity evidence by project",
        "records",
        "bar",
        "project_label",
        (
            ("captured_hours", "Captured hours", "hours"),
            ("activity_entries", "Activity entries", "count"),
            ("overdue_next_actions", "Overdue next actions", "count"),
        ),
        "mixed",
    ),
    "production_budget_by_job": ChartSpec(
        "Estimate-rate production budget usage by job",
        "records",
        "bar",
        "job_name",
        (
            ("estimated_production_budget", "Estimated production budget", "currency"),
            ("estimated_cost_used_proxy", "Tracked usage at estimate rates", "currency"),
            ("budget_used_pct", "Budget used", "ratio"),
        ),
        "proxy",
    ),
    "production_budget_by_bucket": ChartSpec(
        "Estimate-rate production usage by budget bucket",
        "bucket_rollup",
        "bar",
        "bucket",
        (
            ("estimated_cost", "Estimated cost", "currency"),
            ("estimated_cost_used_proxy", "Tracked usage at estimate rates", "currency"),
            ("jobs_usage_over_plan", "Jobs over plan", "count"),
        ),
        "proxy",
    ),
}


def build_chart_dataset(dataset: str, result: dict[str, Any]) -> dict[str, Any]:
    spec = CHART_SPECS[dataset]
    if spec.chart_type == "gantt":
        return _build_schedule_gantt_dataset(dataset, spec, result)
    raw_rows = result.get(spec.source_key) or []
    fields = [spec.category_field, *(name for name, _label, _unit in spec.series)]
    rows = [
        {field: _csv_safe_scalar(row.get(field)) for field in fields}
        for row in raw_rows
        if isinstance(row, dict)
    ]
    warnings = [str(value) for value in result.get("warnings") or []]
    if not rows:
        warnings.append("No rows matched the requested chart dataset and filters.")
    return {
        "schema_version": "spraytec.chart_dataset.v1",
        "dataset": dataset,
        "title": spec.title,
        "recommended_chart_type": spec.chart_type,
        "category_field": spec.category_field,
        "group_field": spec.group_field,
        "start_field": spec.start_field,
        "end_field": spec.end_field,
        "series": [
            {"field": name, "label": label, "unit": unit}
            for name, label, unit in spec.series
        ],
        "as_of": result.get("as_of"),
        "truth_class": result.get("truth_class") or spec.default_truth_class,
        "filters_applied": result.get("filters_applied") or {},
        "rows": rows,
        "source_tables": result.get("source_tables") or [],
        "data_freshness": result.get("data_freshness") or {},
        "coverage": result.get("coverage") or {},
        "warnings": warnings,
    }


def _build_schedule_gantt_dataset(
    dataset: str,
    spec: ChartSpec,
    result: dict[str, Any],
) -> dict[str, Any]:
    filters = dict(result.get("filters_applied") or {})
    window_start = _date_value(filters.get("start_date"))
    window_end = _date_value(filters.get("end_date"))
    raw_rows = result.get(spec.source_key) or []
    rows: list[dict[str, Any]] = []
    missing_start = 0
    inferred_end = 0
    clipped_before = 0
    clipped_after = 0
    unassigned = 0
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        raw_start = _date_value(raw.get("estimated_start_date"))
        if raw_start is None:
            missing_start += 1
            continue
        raw_end = _date_value(raw.get("estimated_end_date"))
        end_source = "estimated_end_date"
        if raw_end is None:
            duration = max(1, int(_number(raw.get("estimated_duration_days")) or 1))
            raw_end = raw_start + timedelta(days=duration - 1)
            end_source = "estimated_duration_days"
            inferred_end += 1
        if raw_end < raw_start:
            raw_end = raw_start
            end_source = "start_date_floor"
        display_start = max(raw_start, window_start) if window_start else raw_start
        display_end = min(raw_end, window_end) if window_end else raw_end
        if display_end < display_start:
            continue
        before = bool(window_start and raw_start < window_start)
        after = bool(window_end and raw_end > window_end)
        clipped_before += int(before)
        clipped_after += int(after)
        crew = str(raw.get("assigned_crew_leader") or "").strip() or "Unassigned"
        unassigned += int(crew == "Unassigned")
        job_id = str(raw.get("job_id") or "").strip()
        job_name = str(raw.get("job_name") or raw.get("customer") or job_id).strip()
        task_label = f"{job_name} ({job_id})" if job_id else job_name
        rows.append(
            {
                "task_label": _csv_safe_scalar(task_label),
                "job_id": _csv_safe_scalar(job_id),
                "job_name": _csv_safe_scalar(job_name),
                "customer": _csv_safe_scalar(raw.get("customer")),
                "crew_leader": _csv_safe_scalar(crew),
                "raw_start_date": raw_start.isoformat(),
                "raw_end_date": raw_end.isoformat(),
                "display_start_date": display_start.isoformat(),
                "display_end_date": display_end.isoformat(),
                "display_duration_days": (display_end - display_start).days + 1,
                "end_date_source": end_source,
                "continues_before_window": before,
                "continues_after_window": after,
                "schedule_health": _csv_safe_scalar(raw.get("schedule_health")),
                "project_health": _csv_safe_scalar(raw.get("project_health")),
                "blocking_issue": _csv_safe_scalar(raw.get("blocking_issue")),
                "division": _csv_safe_scalar(raw.get("division")),
                "operations_value": _csv_safe_scalar(raw.get("operations_value")),
                "folder_link_or_path": _csv_safe_scalar(
                    raw.get("folder_link_or_path")
                ),
            }
        )
    rows.sort(
        key=lambda row: (
            str(row.get("crew_leader") or "").casefold(),
            str(row.get("display_start_date") or ""),
            str(row.get("task_label") or "").casefold(),
        )
    )
    warnings = [str(value) for value in result.get("warnings") or []]
    warnings.append(
        "Gantt bars are clipped to the requested date window so unusually long "
        "projects do not distort the time scale; raw dates and continuation flags "
        "are retained in each row."
    )
    source_coverage = result.get("coverage") or {}
    if source_coverage.get("results_truncated"):
        warnings.append(
            "The matching schedule exceeded the requested Gantt project limit; "
            "narrow the date range or crew filter for a complete view."
        )
    if inferred_end:
        warnings.append(
            f"{inferred_end} project end date(s) were inferred from estimated duration."
        )
    if missing_start:
        warnings.append(
            f"{missing_start} row(s) without a scheduled start were omitted from the Gantt chart."
        )
    if unassigned:
        warnings.append(
            f"{unassigned} scheduled project(s) are grouped under Unassigned."
        )
    coverage = dict(source_coverage)
    coverage.update(
        {
            "gantt_window_start": window_start.isoformat() if window_start else None,
            "gantt_window_end": window_end.isoformat() if window_end else None,
            "gantt_rows": len(rows),
            "gantt_missing_start_rows_omitted": missing_start,
            "gantt_inferred_end_rows": inferred_end,
            "gantt_clipped_before_window_rows": clipped_before,
            "gantt_clipped_after_window_rows": clipped_after,
            "gantt_unassigned_rows": unassigned,
        }
    )
    return {
        "schema_version": "spraytec.chart_dataset.v1",
        "dataset": dataset,
        "title": spec.title,
        "recommended_chart_type": spec.chart_type,
        "category_field": spec.category_field,
        "group_field": spec.group_field,
        "start_field": spec.start_field,
        "end_field": spec.end_field,
        "series": [
            {"field": name, "label": label, "unit": unit}
            for name, label, unit in spec.series
        ],
        "as_of": result.get("as_of"),
        "truth_class": result.get("truth_class") or spec.default_truth_class,
        "filters_applied": filters,
        "rows": rows,
        "source_tables": result.get("source_tables") or [],
        "data_freshness": result.get("data_freshness") or {},
        "coverage": coverage,
        "warnings": warnings,
    }


def chart_dataset_csv(dataset: dict[str, Any]) -> str:
    output = io.StringIO(newline="")
    rows = dataset.get("rows") or []
    series_fields = [item["field"] for item in dataset.get("series") or []]
    preferred_fields = [
        dataset.get("category_field"),
        dataset.get("group_field"),
        dataset.get("start_field"),
        dataset.get("end_field"),
        *series_fields,
    ]
    row_fields = [key for row in rows if isinstance(row, dict) for key in row]
    data_fields = list(
        dict.fromkeys(
            field for field in [*preferred_fields, *row_fields] if field
        )
    )
    fieldnames = ["dataset", "as_of", "truth_class", *data_fields]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                "dataset": dataset.get("dataset"),
                "as_of": dataset.get("as_of"),
                "truth_class": dataset.get("truth_class"),
                **row,
            }
        )
    return output.getvalue()


def _csv_safe_scalar(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    text = str(value)
    if text.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def _date_value(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    normalized = str(value or "").strip()
    if not normalized:
        return None
    try:
        return date.fromisoformat(normalized[:10])
    except ValueError:
        return None


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
