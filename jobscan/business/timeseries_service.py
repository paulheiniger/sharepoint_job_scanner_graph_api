from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import MetaData, String, Table, and_, cast, func, inspect, or_, select
from sqlalchemy.engine import Engine

from jobscan.business.job_service import (
    JobIntelligenceUnavailableError,
    _resolve_engine,
)


FOAM_LBS_PER_STROKE = 0.625
FOAM_LBS_PER_SET = 1000.0

DATASETS: dict[str, dict[str, Any]] = {
    "job_tracking_daily": {
        "table": "job_tracking_daily_entries",
        "id": "tracking_entry_id",
        "date": "work_date",
        "fields": (
            "tracking_entry_id", "job_id", "work_date", "labor_hours",
            "travel_hours", "load_hours", "os_hours", "mileage", "os_mileage",
            "foam_strokes", "foam_lbs", "foam_thickness_inches", "foam_sqft",
            "foam_yield", "base_coat_1", "base_sqft", "base_gal_per_sq",
            "base_coat_2", "top_sqft", "top_gal_per_sq", "granules",
            "af_buttergrade", "caulk", "primer", "sf", "crew", "notes",
            "source_file", "source_path", "tracking_file", "source_sheet",
            "source_row", "updated_at",
        ),
        "metrics": (
            "labor_hours", "travel_hours", "load_hours", "os_hours", "mileage",
            "os_mileage", "foam_strokes", "foam_lbs", "foam_sets", "foam_sqft",
            "foam_yield", "base_coat_1", "base_sqft", "base_coat_2", "top_sqft",
            "granules", "af_buttergrade", "caulk", "primer", "sf",
        ),
        "text": ("crew", "notes", "source_file", "source_path", "tracking_file"),
    },
    "daily_production": {
        "table": "daily_production_entries",
        "id": "production_entry_id",
        "date": "work_date",
        "fields": (
            "production_entry_id", "job_id", "dispatch_date", "work_date",
            "customer", "job_name", "crew_leader", "crew_members", "start_time",
            "end_time", "labor_hours", "travel_hours", "load_hours", "os_hours",
            "mileage", "os_mileage", "rain_observed", "weather_condition",
            "temperature_f", "wind_mph", "humidity_pct", "interior_temperature_f",
            "substrate_temperature_f", "substrate_moisture", "safety_issues",
            "work_notes", "submitted_by", "submitted_at", "updated_at",
        ),
        "metrics": (
            "labor_hours", "travel_hours", "load_hours", "os_hours", "mileage",
            "os_mileage", "temperature_f", "wind_mph", "humidity_pct",
            "interior_temperature_f", "substrate_temperature_f", "substrate_moisture",
        ),
        "text": (
            "customer", "job_name", "crew_leader", "crew_members",
            "weather_condition", "safety_issues", "work_notes", "submitted_by",
        ),
    },
    "daily_material_usage": {
        "table": "daily_production_material_usage",
        "id": "material_usage_id",
        "date": "work_date",
        "fields": (
            "material_usage_id", "production_entry_id", "job_id", "work_date",
            "material_type", "quantity", "unit", "notes", "updated_at",
        ),
        "metrics": ("quantity",),
        "text": ("material_type", "unit", "notes"),
    },
    "office_activity": {
        "table": "office_timesheet_entries",
        "id": "entry_id",
        "date": "work_date",
        "fields": (
            "entry_id", "job_id", "work_date", "employee", "project_name", "code",
            "duration_hours", "row_type", "start_time", "end_time", "milestone",
            "next_action", "next_action_owner", "source_file", "source_sheet",
            "source_row", "updated_at",
        ),
        "metrics": ("duration_hours",),
        "text": (
            "employee", "project_name", "code", "row_type", "milestone",
            "next_action", "next_action_owner", "source_file",
        ),
    },
    "workflow_events": {
        "table": "job_workflow_events",
        "id": "event_id",
        "date": "created_at",
        "fields": (
            "event_id", "job_id", "event_type", "from_status", "to_status",
            "event_source", "updated_by", "created_at",
        ),
        "metrics": (),
        "text": (
            "event_type", "from_status", "to_status", "event_source", "updated_by",
        ),
    },
}

JOB_CONTEXT_FIELDS = ("customer", "job_name", "division", "job_type", "pipeline_status", "status")
SCOPE_EVIDENCE_TABLES: dict[str, tuple[str, ...]] = {
    "jobs": ("customer", "job_name", "job_type", "division"),
    "dashboard_jobs": ("customer", "job_name", "job_type", "division"),
    "job_document_signals": ("document_substrate", "document_material_system"),
    "estimate_template_rows": (
        "template_type", "template_bucket", "template_section", "row_label",
        "raw_text", "selected_item_name", "resolved_item_name", "foam_brand",
    ),
    "job_tracking_daily_entries": ("crew", "notes", "tracking_file", "source_file"),
}


def get_operational_timeseries(
    *,
    database_url: str | None = None,
    engine: Engine | None = None,
    dataset: str,
    start_date: date | None = None,
    end_date: date | None = None,
    job_ids: list[str] | None = None,
    division: str = "",
    query: str = "",
    scope_terms: list[str] | None = None,
    metric: str = "",
    positive_only: bool = False,
    page: int = 1,
    page_size: int = 100,
    sort_order: str = "ascending",
) -> dict[str, Any]:
    if dataset not in DATASETS:
        raise ValueError(f"Unsupported time-series dataset: {dataset}")
    config = DATASETS[dataset]
    if metric and metric not in config["metrics"]:
        raise ValueError(f"Metric {metric!r} is unavailable for {dataset}.")
    if start_date and end_date and start_date > end_date:
        raise ValueError("start_date must be on or before end_date.")

    resolved, owns = _resolve_engine(database_url, engine)
    try:
        relation_names = set(inspect(resolved).get_table_names()) | set(inspect(resolved).get_view_names())
        if config["table"] not in relation_names:
            raise JobIntelligenceUnavailableError(
                f"Required time-series source is unavailable: {config['table']}"
            )
        metadata = MetaData()
        source = Table(config["table"], metadata, autoload_with=resolved)
        jobs = _job_table(resolved, metadata, relation_names)
        date_column = source.c.get(config["date"])
        if date_column is None:
            raise JobIntelligenceUnavailableError(
                f"Time-series source has no {config['date']} column."
            )

        scope_ids: set[str] | None = None
        scope_evidence: dict[str, list[str]] = {}
        clean_scope_terms = [str(term).strip() for term in (scope_terms or []) if str(term).strip()]
        if clean_scope_terms:
            scope_ids, scope_evidence = _scope_matches(resolved, relation_names, clean_scope_terms)

        from_clause = source
        if jobs is not None and source.c.get("job_id") is not None and jobs.c.get("job_id") is not None:
            from_clause = source.outerjoin(jobs, jobs.c.job_id == source.c.job_id)

        selected = [source.c[name] for name in config["fields"] if source.c.get(name) is not None]
        selected.append(date_column.label("event_date"))
        if jobs is not None:
            selected.extend(
                jobs.c[name].label(f"job_{name}")
                for name in JOB_CONTEXT_FIELDS
                if jobs.c.get(name) is not None
            )
        conditions = []
        if start_date:
            conditions.append(date_column >= start_date)
        if end_date:
            conditions.append(date_column <= end_date)
        clean_job_ids = sorted({str(value).strip() for value in (job_ids or []) if str(value).strip()})
        if clean_job_ids:
            if source.c.get("job_id") is None:
                raise ValueError(f"Dataset {dataset} cannot be filtered by job_id.")
            conditions.append(source.c.job_id.in_(clean_job_ids))
        if scope_ids is not None:
            if source.c.get("job_id") is None:
                raise ValueError(f"Dataset {dataset} cannot be filtered by scope evidence.")
            conditions.append(source.c.job_id.in_(scope_ids or {"__no_scope_matches__"}))
        if division.strip():
            if jobs is None or jobs.c.get("division") is None:
                raise JobIntelligenceUnavailableError("Job division context is unavailable.")
            conditions.append(func.lower(jobs.c.division) == division.strip().lower())
        if query.strip():
            searchable = [source.c[name] for name in config["text"] if source.c.get(name) is not None]
            if jobs is not None:
                searchable.extend(
                    jobs.c[name] for name in ("customer", "job_name", "job_type")
                    if jobs.c.get(name) is not None
                )
            pattern = f"%{query.strip().lower()}%"
            conditions.append(or_(*(func.lower(cast(column, String)).like(pattern) for column in searchable)))
        if metric and positive_only:
            conditions.append(_positive_metric_condition(source, metric))

        base = select(*selected).select_from(from_clause)
        count_query = select(func.count()).select_from(from_clause)
        if conditions:
            base = base.where(and_(*conditions))
            count_query = count_query.where(and_(*conditions))
        id_column = source.c.get(config["id"])
        ordering = [date_column.asc() if sort_order == "ascending" else date_column.desc()]
        if id_column is not None:
            ordering.append(id_column.asc() if sort_order == "ascending" else id_column.desc())
        offset = (page - 1) * page_size
        with resolved.connect() as connection:
            total_records = int(connection.execute(count_query).scalar_one())
            rows = [
                _serialize_row(dict(row), scope_evidence)
                for row in connection.execute(base.order_by(*ordering).offset(offset).limit(page_size)).mappings()
            ]
        total_pages = math.ceil(total_records / page_size) if total_records else 0
        has_more = page < total_pages
        warnings = _warnings(dataset, clean_scope_terms, metric, total_records)
        source_tables = [config["table"]] + ([jobs.name] if jobs is not None else [])
        if clean_scope_terms:
            source_tables.extend(name for name in SCOPE_EVIDENCE_TABLES if name in relation_names)
        return {
            "schema_version": "spraytec.operational_timeseries.v1",
            "as_of": datetime.now(timezone.utc).isoformat(),
            "dataset": dataset,
            "date_field": config["date"],
            "available_metrics": list(config["metrics"]),
            "filters_applied": {
                "start_date": start_date.isoformat() if start_date else None,
                "end_date": end_date.isoformat() if end_date else None,
                "job_ids": clean_job_ids,
                "division": division.strip() or None,
                "query": query.strip() or None,
                "scope_terms": clean_scope_terms,
                "metric": metric or None,
                "positive_only": positive_only,
                "sort_order": sort_order,
            },
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total_records": total_records,
                "total_pages": total_pages,
                "has_more": has_more,
                "next_page": page + 1 if has_more else None,
            },
            "records": rows,
            "source_tables": list(dict.fromkeys(source_tables)),
            "calculation_guidance": _calculation_guidance(dataset, metric),
            "warnings": warnings,
        }
    finally:
        if owns:
            resolved.dispose()


def _job_table(engine: Engine, metadata: MetaData, relation_names: set[str]) -> Table | None:
    for name in ("dashboard_jobs", "jobs"):
        if name in relation_names:
            return Table(name, metadata, autoload_with=engine)
    return None


def _positive_metric_condition(source: Table, metric: str):
    if metric == "foam_sets":
        available = [source.c[name] > 0 for name in ("foam_lbs", "foam_strokes") if source.c.get(name) is not None]
        if not available:
            raise ValueError("foam_sets cannot be derived from this dataset.")
        return or_(*available)
    column = source.c.get(metric)
    if column is None:
        raise ValueError(f"Metric {metric!r} is not stored by this dataset.")
    return column > 0


def _scope_matches(
    engine: Engine,
    relation_names: set[str],
    terms: list[str],
) -> tuple[set[str], dict[str, list[str]]]:
    groups = [_scope_variants(term) for term in terms]
    evidence: dict[str, list[str]] = defaultdict(list)
    for table_name, configured_columns in SCOPE_EVIDENCE_TABLES.items():
        if table_name not in relation_names:
            continue
        table = Table(table_name, MetaData(), autoload_with=engine)
        if table.c.get("job_id") is None:
            continue
        columns = [table.c[name] for name in configured_columns if table.c.get(name) is not None]
        if not columns:
            continue
        patterns = sorted({variant for group in groups for variant in group})
        match_condition = or_(*(
            func.lower(cast(column, String)).like(f"%{pattern}%")
            for column in columns
            for pattern in patterns
        ))
        statement = select(table.c.job_id, *columns).where(
            table.c.job_id.is_not(None),
            match_condition,
        ).limit(20_000)
        with engine.connect() as connection:
            for row in connection.execute(statement).mappings():
                job_id = str(row.get("job_id") or "").strip()
                snippets = [str(row.get(column.name) or "").strip() for column in columns]
                for snippet in snippets:
                    if snippet and snippet not in evidence[job_id] and len(evidence[job_id]) < 12:
                        evidence[job_id].append(snippet[:500])
    matched = {
        job_id
        for job_id, snippets in evidence.items()
        if all(any(variant in " ".join(snippets).lower() for variant in group) for group in groups)
    }
    return matched, {job_id: evidence[job_id] for job_id in matched}


def _scope_variants(term: str) -> tuple[str, ...]:
    normalized = " ".join(term.lower().replace("-", " ").split())
    if normalized in {"vertical", "wall", "walls", "vertical surface"}:
        return ("vertical", "wall", "sidewall", "side wall", "gable", "rim joist")
    if normalized in {"closed cell", "closed cell foam", "closed cell spray foam"}:
        return ("closed cell", "closed-cell")
    if normalized in {"open cell", "open cell foam", "open cell spray foam"}:
        return ("open cell", "open-cell")
    return (normalized,)


def _serialize_row(row: dict[str, Any], scope_evidence: dict[str, list[str]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, (datetime, date)):
            result[key] = value.isoformat()
        elif isinstance(value, Decimal):
            result[key] = float(value)
        else:
            result[key] = value
    pounds = _positive_number(result.get("foam_lbs"))
    strokes = _positive_number(result.get("foam_strokes"))
    if pounds is not None:
        result["foam_sets"] = round(pounds / FOAM_LBS_PER_SET, 6)
        result["foam_sets_basis"] = "foam_lbs / 1000 lb per set"
    elif strokes is not None:
        result["foam_sets"] = round(strokes * FOAM_LBS_PER_STROKE / FOAM_LBS_PER_SET, 6)
        result["foam_sets_basis"] = "foam_strokes * 0.625 lb per stroke / 1000 lb per set"
    job_id = str(result.get("job_id") or "")
    if job_id in scope_evidence:
        result["scope_evidence"] = scope_evidence[job_id][:8]
    return result


def _positive_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _calculation_guidance(dataset: str, metric: str) -> list[str]:
    guidance = [
        "Retrieve every page before calculating a portfolio average; page-level records are not a complete sample.",
        "Aggregate duplicate rows by job_id and event_date before calculating per-day statistics.",
    ]
    if dataset == "job_tracking_daily" and metric == "foam_sets":
        guidance.extend([
            "For spraying-day productivity, include only grouped job-days with foam_sets greater than zero.",
            "foam_sets uses recorded pounds when available; otherwise it derives pounds from strokes at 0.625 lb per stroke, then divides by 1000 lb per set.",
            "Report the qualifying job count, spraying-day count, date range, mean, median, and scope-evidence limitations.",
        ])
    return guidance


def _warnings(dataset: str, scope_terms: list[str], metric: str, total_records: int) -> list[str]:
    warnings: list[str] = []
    if not total_records:
        warnings.append("No records matched the requested filters; broaden the dates or review the scope terms.")
    if scope_terms:
        warnings.append(
            "Scope terms are deterministic text-evidence filters, not authoritative field classifications; review returned scope_evidence before using the cohort."
        )
    if dataset == "job_tracking_daily":
        warnings.append(
            "Daily tracking rows reflect available Job Tracking workbooks and may omit undocumented field days or incomplete material entries."
        )
    if metric == "foam_sets":
        warnings.append(
            "The endpoint derives sets only for rows with positive recorded foam pounds or strokes; it does not infer spraying from labor hours alone."
        )
    return warnings
