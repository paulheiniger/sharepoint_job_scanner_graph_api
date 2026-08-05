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
    orientation: str = "vertical"
    sort_field: str | None = None
    sort_direction: str = "descending"
    category_order: tuple[str, ...] = ()
    category_colors: tuple[tuple[str, str], ...] = ()
    reference_lines: tuple[tuple[str, float, str], ...] = ()


CHART_SPECS: dict[str, ChartSpec] = {
    "sales_pipeline_by_stage": ChartSpec(
        "Sales pipeline by stage",
        "stage_rollup",
        "bar",
        "pipeline_status",
        (
            ("estimated_value", "Pipeline value", "currency"),
            ("job_count", "Jobs", "count"),
        ),
        sort_field="pipeline_status",
        sort_direction="ascending",
        category_order=(
            "Proposed",
            "Contracted",
            "Contracted Repairs",
            "Completed",
            "Not captured",
        ),
        category_colors=(
            ("Proposed", "#2563EB"),
            ("Contracted", "#059669"),
            ("Contracted Repairs", "#0D9488"),
            ("Completed", "#64748B"),
            ("Not captured", "#94A3B8"),
        ),
    ),
    "sales_pipeline_by_owner": ChartSpec(
        "Sales pipeline by owner",
        "owner_rollup",
        "bar",
        "owner",
        (
            ("estimated_value", "Pipeline value", "currency"),
            ("job_count", "Jobs", "count"),
        ),
        sort_field="estimated_value",
        orientation="horizontal",
    ),
    "operations_backlog_by_division": ChartSpec(
        "Contracted backlog by division",
        "division_rollup",
        "bar",
        "division",
        (("value", "Backlog value", "currency"), ("jobs", "Jobs", "count")),
        sort_field="value",
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
        sort_field="readiness_status",
        sort_direction="ascending",
        category_order=(
            "Missing Job Spec",
            "Not Contracted Folder",
            "Customer Hold",
            "Material Hold",
            "Permit Hold",
            "Weather Window",
            "Ready To Schedule",
            "Scheduled",
            "Not captured",
        ),
        category_colors=(
            ("Missing Job Spec", "#DC2626"),
            ("Not Contracted Folder", "#D97706"),
            ("Customer Hold", "#EA580C"),
            ("Material Hold", "#C2410C"),
            ("Permit Hold", "#A16207"),
            ("Weather Window", "#0891B2"),
            ("Ready To Schedule", "#2563EB"),
            ("Scheduled", "#059669"),
            ("Not captured", "#94A3B8"),
        ),
    ),
    "operations_schedule_by_crew": ChartSpec(
        "Scheduled workload by crew leader",
        "crew_rollup",
        "bar",
        "crew_leader",
        (("value", "Scheduled value", "currency"), ("jobs", "Jobs", "count")),
        sort_field="value",
        orientation="horizontal",
    ),
    "operations_schedule_by_health": ChartSpec(
        "Scheduled work by schedule health",
        "schedule_health_rollup",
        "bar",
        "schedule_health",
        (("value", "Scheduled value", "currency"), ("jobs", "Jobs", "count")),
        sort_field="schedule_health",
        sort_direction="ascending",
        category_order=(
            "Behind / Blocked",
            "Starting Soon",
            "On Track",
            "Awaiting Schedule",
            "Completed",
            "Not captured",
        ),
        category_colors=(
            ("Behind / Blocked", "#DC2626"),
            ("Starting Soon", "#2563EB"),
            ("On Track", "#059669"),
            ("Awaiting Schedule", "#D97706"),
            ("Completed", "#64748B"),
            ("Not captured", "#94A3B8"),
        ),
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
        orientation="timeline",
        sort_field="crew_leader",
        sort_direction="ascending",
    ),
    "office_activity_by_day": ChartSpec(
        "Office activity by day",
        "daily_rollup",
        "line",
        "activity_date",
        (("total_hours", "Captured hours", "hours"), ("touch_count", "Touches", "count")),
        "mixed",
        sort_field="activity_date",
        sort_direction="ascending",
    ),
    "office_activity_by_employee": ChartSpec(
        "Office activity by employee",
        "employee_rollup",
        "bar",
        "employee",
        (("total_hours", "Captured hours", "hours"), ("touch_count", "Touches", "count")),
        "mixed",
        sort_field="total_hours",
        orientation="horizontal",
    ),
    "office_activity_by_code": ChartSpec(
        "Office activity by work code",
        "code_rollup",
        "bar",
        "code",
        (("total_hours", "Captured hours", "hours"), ("touch_count", "Touches", "count")),
        "mixed",
        sort_field="total_hours",
        orientation="horizontal",
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
        sort_field="captured_hours",
        orientation="horizontal",
    ),
    "production_budget_by_job": ChartSpec(
        "Estimate-rate production budget usage by job",
        "records",
        "bar",
        "job_name",
        (
            ("estimated_production_budget", "Estimated production budget", "currency"),
            (
                "estimated_cost_used_proxy",
                "Tracked usage at estimate rates",
                "currency",
            ),
            ("budget_used_pct", "Budget used", "ratio"),
        ),
        "proxy",
        sort_field="budget_used_pct",
        orientation="horizontal",
        reference_lines=(("budget_used_pct", 1.0, "Estimate-rate plan"),),
    ),
    "production_budget_by_bucket": ChartSpec(
        "Estimate-rate production usage by budget bucket",
        "bucket_rollup",
        "bar",
        "bucket",
        (
            ("estimated_cost", "Estimated cost", "currency"),
            (
                "estimated_cost_used_proxy",
                "Tracked usage at estimate rates",
                "currency",
            ),
            ("jobs_usage_over_plan", "Jobs over plan", "count"),
        ),
        "proxy",
        sort_field="bucket",
        sort_direction="ascending",
        category_order=(
            "Labor",
            "Foam / SPF",
            "Coating",
            "Primer / Sealants",
            "Granules",
            "Board / Fasteners / Plates",
            "Equipment / Travel / Lodging",
        ),
    ),
    "sales_pipeline_history": ChartSpec(
        "Sales pipeline history",
        "records",
        "line",
        "snapshot_date",
        (
            ("pipeline_value", "Pipeline value", "currency"),
            ("job_count", "Jobs", "count"),
        ),
        sort_field="snapshot_date",
        sort_direction="ascending",
    ),
    "operations_backlog_history": ChartSpec(
        "Contracted backlog history",
        "records",
        "line",
        "snapshot_date",
        (
            ("backlog_value", "Backlog value", "currency"),
            ("job_count", "Jobs", "count"),
        ),
        sort_field="snapshot_date",
        sort_direction="ascending",
    ),
    "production_budget_history": ChartSpec(
        "Estimate-rate production budget history",
        "records",
        "line",
        "snapshot_date",
        (
            ("estimated_production_budget", "Estimated production budget", "currency"),
            (
                "estimated_cost_used_proxy",
                "Tracked usage at estimate rates",
                "currency",
            ),
            ("jobs_usage_over_plan", "Jobs over plan", "count"),
        ),
        "proxy",
        sort_field="snapshot_date",
        sort_direction="ascending",
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
    rows = _sort_chart_rows(rows, spec)
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
        "series": _series_contract(spec),
        "display": _display_contract(spec, rows),
        "as_of": result.get("as_of"),
        "truth_class": result.get("truth_class") or spec.default_truth_class,
        "filters_applied": result.get("filters_applied") or {},
        "rows": rows,
        "source_tables": result.get("source_tables") or [],
        "data_freshness": result.get("data_freshness") or {},
        "staging": _staging_contract(result),
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
    duplicate_rows = 0
    seen_schedule_rows: set[tuple[str, str, date, date]] = set()
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
        job_id = str(raw.get("job_id") or "").strip()
        job_name = str(raw.get("job_name") or raw.get("customer") or job_id).strip()
        schedule_identity = (
            (job_id or job_name).casefold(),
            crew.casefold(),
            raw_start,
            raw_end,
        )
        if schedule_identity in seen_schedule_rows:
            duplicate_rows += 1
            continue
        seen_schedule_rows.add(schedule_identity)
        unassigned += int(crew == "Unassigned")
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
    if duplicate_rows:
        warnings.append(
            f"{duplicate_rows} exact duplicate schedule row(s) were omitted from "
            "the Gantt chart."
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
            "gantt_duplicate_rows_omitted": duplicate_rows,
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
        "series": _series_contract(spec),
        "display": _display_contract(spec, rows),
        "as_of": result.get("as_of"),
        "truth_class": result.get("truth_class") or spec.default_truth_class,
        "filters_applied": filters,
        "rows": rows,
        "source_tables": result.get("source_tables") or [],
        "data_freshness": result.get("data_freshness") or {},
        "staging": _staging_contract(result),
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


_SERIES_COLORS = (
    "#2563EB",
    "#D97706",
    "#059669",
    "#7C3AED",
    "#0891B2",
)

_NUMBER_FORMATS = {
    "currency": "currency_0",
    "count": "integer",
    "days": "decimal_1",
    "hours": "decimal_1",
    "ratio": "percent_1",
}


def _series_contract(spec: ChartSpec) -> list[dict[str, Any]]:
    distinct_units = list(dict.fromkeys(unit for _field, _label, unit in spec.series))
    multi_scale_strategy = _multi_scale_strategy(spec)
    primary_unit = distinct_units[0] if distinct_units else None
    return [
        {
            "field": field,
            "label": label,
            "unit": unit,
            "number_format": _NUMBER_FORMATS[unit],
            "color": _SERIES_COLORS[index % len(_SERIES_COLORS)],
            "axis": (
                "secondary"
                if multi_scale_strategy == "dual_axis" and unit != primary_unit
                else "primary"
            ),
            "panel": unit if multi_scale_strategy == "small_multiples" else "main",
        }
        for index, (field, label, unit) in enumerate(spec.series)
    ]


def _display_contract(spec: ChartSpec, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "contract_version": "spraytec.chart_display.v1",
        "orientation": spec.orientation,
        "sort": {
            "field": spec.sort_field or spec.category_field,
            "direction": spec.sort_direction,
            "then_by": (
                ["display_start_date", "task_label"]
                if spec.chart_type == "gantt"
                else [spec.category_field]
                if (spec.sort_field or spec.category_field) != spec.category_field
                else []
            ),
        },
        "category_order": list(spec.category_order),
        "category_colors": dict(spec.category_colors),
        "multi_scale_strategy": _multi_scale_strategy(spec),
        "show_legend": len(spec.series) > 1,
        "show_data_labels": len(rows) <= 12,
        "zero_baseline": spec.chart_type != "gantt",
        "reference_lines": [
            {"field": field, "value": value, "label": label}
            for field, value, label in spec.reference_lines
        ],
    }


def _multi_scale_strategy(spec: ChartSpec) -> str:
    unit_count = len({unit for _field, _label, unit in spec.series})
    if unit_count <= 1:
        return "shared_axis"
    if unit_count == 2:
        return "dual_axis"
    return "small_multiples"


def _sort_chart_rows(
    rows: list[dict[str, Any]],
    spec: ChartSpec,
) -> list[dict[str, Any]]:
    if not rows:
        return rows
    sort_field = spec.sort_field or spec.category_field
    if spec.category_order and sort_field == spec.category_field:
        order = {
            value.casefold(): index
            for index, value in enumerate(spec.category_order)
        }
        return sorted(
            rows,
            key=lambda row: (
                order.get(str(row.get(sort_field) or "").casefold(), len(order)),
                str(row.get(sort_field) or "").casefold(),
            ),
        )
    reverse = spec.sort_direction == "descending"
    return sorted(
        rows,
        key=lambda row: _sortable_value(row.get(sort_field), reverse=reverse),
        reverse=reverse,
    )


def _sortable_value(value: Any, *, reverse: bool) -> tuple[int, float | str]:
    if value is None or value == "":
        return (0 if reverse else 1, float("-inf") if reverse else "")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (1 if reverse else 0, float(value))
    return (1 if reverse else 0, str(value).casefold())


def _staging_contract(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("staging"):
        return dict(result["staging"])
    source_tables = [str(value) for value in result.get("source_tables") or []]
    snapshot_tables = [
        value for value in source_tables if "snapshot" in value.casefold()
    ]
    if snapshot_tables and len(snapshot_tables) == len(source_tables):
        source_storage = "current_snapshot"
    elif snapshot_tables:
        source_storage = "hybrid_current_snapshot"
    else:
        source_storage = "operational_query"
    return {
        "aggregation_mode": "endpoint_on_request",
        "source_storage": source_storage,
        "snapshot_tables": snapshot_tables,
        "freshness": result.get("data_freshness") or {},
        "historical_series_available": False,
        "historical_limitation": (
            "Current-state snapshots do not preserve a time series. Use only the "
            "returned period field for trends."
        ),
    }


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
