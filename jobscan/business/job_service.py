from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from typing import Any

from sqlalchemy import bindparam, inspect, text
from sqlalchemy.engine import Engine

from jobscan.db_connections import create_resilient_engine


MAX_JOB_SEARCH_RESULTS = 25
MAX_JOB_SEARCH_CANDIDATES = 100
MAX_CONTEXT_DOCUMENTS = 20
MAX_CONTEXT_DAILY_ENTRIES = 10
MAX_CONTEXT_TIMESHEET_ENTRIES = 10

JOB_FIELDS = (
    "job_id",
    "division",
    "pipeline_status",
    "status",
    "customer",
    "job_name",
    "job_type",
    "site_address",
    "city",
    "state",
    "zip_code",
    "estimated_sqft",
    "estimated_value",
    "estimated_value_source",
    "final_price",
    "total_job_cost",
    "price_per_sqft",
    "material_subtotal",
    "labor_subtotal",
    "has_signed_contract",
    "has_invoice",
    "has_warranty",
    "has_proposal",
    "has_job_spec",
    "has_aerial",
    "has_notes",
    "photo_count",
    "folder_name",
    "folder_path",
    "folder_url",
    "folder_link_or_path",
    "primary_doc_link",
    "primary_doc_type",
    "primary_doc_name",
    "proposal_url",
    "estimate_url",
    "contract_url",
    "invoice_url",
    "job_tracking_url",
    "warranty_url",
    "aerial_url",
    "estimate_file",
    "warnings",
    "has_warnings",
    "completed_missing_invoice",
    "completed_missing_final_price",
    "missing_signed_contract",
    "missing_job_spec",
    "last_scanned_at",
    "updated_at",
    "refreshed_at",
)

WORKFLOW_FIELDS = (
    "workflow_status",
    "deal_owner",
    "assigned_user",
    "follow_up_date",
    "priority",
    "internal_notes",
    "closed_did_not_get",
    "review_mark_contracted",
    "review_mark_completed",
    "updated_at",
)

SCHEDULE_FIELDS = (
    "schedule_id",
    "assigned_crew_leader",
    "suggested_crew_type",
    "estimated_start_date",
    "estimated_duration_days",
    "estimated_end_date",
    "schedule_status",
    "ready_to_schedule",
    "blocking_issue",
    "priority",
    "schedule_notes",
    "updated_at",
)

TRACKING_SUMMARY_FIELDS = (
    "tracking_id",
    "tracking_file",
    "actual_first_work_date",
    "actual_last_work_date",
    "actual_work_day_count",
    "actual_labor_hours",
    "actual_travel_hours",
    "actual_load_hours",
    "actual_mileage",
    "actual_foam_strokes",
    "actual_foam_lbs",
    "actual_foam_thickness_inches",
    "actual_foam_sqft",
    "actual_foam_yield",
    "actual_base_coat_1",
    "actual_base_coat_2",
    "actual_granules",
    "actual_caulk",
    "actual_primer",
    "actual_sf",
    "estimated_labor_hours",
    "estimated_travel_hours",
    "estimated_load_hours",
    "estimated_mileage",
    "estimated_foam_strokes",
    "estimated_foam_lbs",
    "estimated_foam_thickness_inches",
    "estimated_foam_sqft",
    "estimated_foam_yield",
    "estimated_base_coat_1",
    "estimated_base_coat_2",
    "estimated_granules",
    "estimated_caulk",
    "estimated_primer",
    "estimated_sf",
    "labor_hours_variance",
    "tracking_notes",
    "tracking_warnings",
    "source_file",
    "source_path",
    "updated_at",
)

DAILY_ENTRY_FIELDS = (
    "tracking_entry_id",
    "tracking_id",
    "tracking_file",
    "work_date",
    "labor_hours",
    "travel_hours",
    "load_hours",
    "mileage",
    "foam_strokes",
    "foam_lbs",
    "foam_thickness_inches",
    "foam_sqft",
    "foam_yield",
    "base_coat_1",
    "base_coat_2",
    "granules",
    "caulk",
    "primer",
    "sf",
    "crew",
    "notes",
    "source_sheet",
    "source_row",
    "updated_at",
)

DOCUMENT_FIELDS = (
    "document_id",
    "document_type",
    "file_name",
    "sharepoint_url",
    "folder_path",
    "relative_path",
    "modified_at",
    "extraction_status",
    "extraction_method",
    "updated_at",
)

TIMESHEET_FIELDS = (
    "entry_id",
    "employee",
    "work_date",
    "project_name",
    "code",
    "duration_hours",
    "row_type",
    "milestone",
    "next_action",
    "next_action_owner",
    "next_action_due",
    "notes",
    "source_file",
    "source_file_path",
    "warnings",
    "updated_at",
)


class JobNotFoundError(LookupError):
    """Raised when a stable job identifier cannot be found."""


class JobIntelligenceUnavailableError(RuntimeError):
    """Raised when the operational job source is unavailable."""


def search_jobs(
    *,
    database_url: str | None = None,
    engine: Engine | None = None,
    query: str = "",
    job_ids: Iterable[str] = (),
    division: str = "",
    pipeline_status: str = "",
    workflow_status: str = "",
    owner: str = "",
    needs_attention: bool | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    resolved_engine, owns_engine = _resolve_engine(database_url, engine)
    try:
        relation, columns = _job_relation(resolved_engine)
        applied_limit = max(1, min(int(limit), MAX_JOB_SEARCH_RESULTS))
        requested_job_ids = _unique_nonblank(job_ids)[:MAX_JOB_SEARCH_RESULTS]
        params: dict[str, Any] = {}
        conditions: list[str] = []

        normalized_query = str(query or "").strip()
        if normalized_query:
            searchable = [
                field
                for field in (
                    "job_id",
                    "customer",
                    "job_name",
                    "site_address",
                    "city",
                    "folder_name",
                )
                if field in columns
            ]
            if searchable:
                conditions.append(
                    "("
                    + " OR ".join(
                        f"LOWER(COALESCE(j.{field}, '')) LIKE :query"
                        for field in searchable
                    )
                    + ")"
                )
                params["query"] = f"%{normalized_query.lower()}%"

        if requested_job_ids:
            conditions.append("j.job_id IN :job_ids")
            params["job_ids"] = requested_job_ids
        for field, value in (
            ("division", division),
            ("pipeline_status", pipeline_status),
        ):
            normalized = str(value or "").strip()
            if normalized and field in columns:
                conditions.append(f"LOWER(COALESCE(j.{field}, '')) = :{field}")
                params[field] = normalized.lower()

        if workflow_status and _has_relation(resolved_engine, "job_workflow_overrides"):
            conditions.append(
                "EXISTS (SELECT 1 FROM job_workflow_overrides o "
                "WHERE o.job_id = j.job_id "
                "AND LOWER(COALESCE(o.workflow_status, '')) = :workflow_status)"
            )
            params["workflow_status"] = workflow_status.strip().lower()
        if owner and _has_relation(resolved_engine, "job_workflow_overrides"):
            conditions.append(
                "EXISTS (SELECT 1 FROM job_workflow_overrides o "
                "WHERE o.job_id = j.job_id AND ("
                "LOWER(COALESCE(o.deal_owner, '')) = :owner OR "
                "LOWER(COALESCE(o.assigned_user, '')) = :owner))"
            )
            params["owner"] = owner.strip().lower()
        if needs_attention is not None:
            attention_parts = _attention_sql_parts(columns)
            if attention_parts:
                expression = "(" + " OR ".join(attention_parts) + ")"
                conditions.append(expression if needs_attention else f"NOT {expression}")

        selected_fields = _available_fields(columns, JOB_FIELDS)
        sql = f"SELECT {', '.join(f'j.{field}' for field in selected_fields)} FROM {relation} j"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY j.updated_at DESC NULLS LAST" if "updated_at" in columns else " ORDER BY j.job_id"
        sql += " LIMIT :candidate_limit"
        params["candidate_limit"] = MAX_JOB_SEARCH_CANDIDATES
        statement = text(sql)
        if requested_job_ids:
            statement = statement.bindparams(bindparam("job_ids", expanding=True))
        base_rows = _query_rows(resolved_engine, statement, params)
        enriched = _enrich_jobs(resolved_engine, base_rows)
        records = [_job_search_record(row) for row in enriched[:applied_limit]]
        attention_items = [
            item
            for row in enriched[:applied_limit]
            for item in _attention_items(row)
        ][:25]
        sources = [relation]
        for optional in ("job_workflow_overrides", "crew_schedule"):
            if _has_relation(resolved_engine, optional):
                sources.append(optional)
        as_of = _latest_value(
            row.get(key)
            for row in enriched
            for key in ("refreshed_at", "updated_at", "last_scanned_at")
        )
        return {
            "schema_version": "spraytec.job_search.v1",
            "as_of": as_of or _utc_now(),
            "filters_applied": {
                key: value
                for key, value in {
                    "query": normalized_query or None,
                    "job_ids": requested_job_ids or None,
                    "division": division.strip() or None,
                    "pipeline_status": pipeline_status.strip() or None,
                    "workflow_status": workflow_status.strip() or None,
                    "owner": owner.strip() or None,
                    "needs_attention": needs_attention,
                    "limit": applied_limit,
                }.items()
                if value is not None
            },
            "headline_metrics": {
                "matching_candidates": len(enriched),
                "returned_records": len(records),
                "estimated_value": _sum_numeric(
                    row.get("estimated_value") for row in records
                ),
                "records_needing_attention": sum(
                    bool(_attention_items(row)) for row in enriched[:applied_limit]
                ),
                "scheduled_records": sum(
                    bool(row.get("estimated_start_date"))
                    for row in enriched[:applied_limit]
                ),
            },
            "records": records,
            "attention_items": attention_items,
            "source_links": _dedupe_links(
                link
                for row in enriched[:applied_limit]
                for link in _job_source_links(row, [])
            ),
            "source_tables": sources,
            "data_freshness": {"job_data_as_of": as_of},
            "coverage": {
                "candidate_limit": MAX_JOB_SEARCH_CANDIDATES,
                "result_limit": applied_limit,
                "results_truncated": len(enriched) > applied_limit,
            },
            "warnings": (
                [
                    "Search reached the bounded candidate limit; narrow the filters for complete results."
                ]
                if len(base_rows) >= MAX_JOB_SEARCH_CANDIDATES
                else []
            ),
            "response_budget": {
                "max_records": MAX_JOB_SEARCH_RESULTS,
                "returned_records": len(records),
            },
        }
    finally:
        if owns_engine:
            resolved_engine.dispose()


def get_job_context(
    job_id: str,
    *,
    database_url: str | None = None,
    engine: Engine | None = None,
) -> dict[str, Any]:
    normalized_job_id = str(job_id or "").strip()
    if not normalized_job_id:
        raise JobNotFoundError("A nonblank job_id is required.")
    resolved_engine, owns_engine = _resolve_engine(database_url, engine)
    try:
        relation, columns = _job_relation(resolved_engine)
        selected_fields = _available_fields(columns, JOB_FIELDS)
        job_rows = _query_rows(
            resolved_engine,
            text(
                f"SELECT {', '.join(selected_fields)} FROM {relation} "
                "WHERE job_id = :job_id LIMIT 1"
            ),
            {"job_id": normalized_job_id},
        )
        if not job_rows:
            raise JobNotFoundError(f"Job {normalized_job_id!r} was not found.")
        job = _enrich_jobs(resolved_engine, job_rows)[0]

        tracking = _related_rows(
            resolved_engine,
            "job_tracking_summary",
            normalized_job_id,
            TRACKING_SUMMARY_FIELDS,
            limit=5,
            order_fields=("updated_at", "actual_last_work_date"),
        )
        daily_entries = _related_rows(
            resolved_engine,
            "job_tracking_daily_entries",
            normalized_job_id,
            DAILY_ENTRY_FIELDS,
            limit=MAX_CONTEXT_DAILY_ENTRIES,
            order_fields=("work_date", "updated_at"),
        )
        daily_entry_total = _related_row_count(
            resolved_engine,
            "job_tracking_daily_entries",
            normalized_job_id,
        )
        documents = _related_rows(
            resolved_engine,
            "documents",
            normalized_job_id,
            DOCUMENT_FIELDS,
            limit=MAX_CONTEXT_DOCUMENTS,
            order_fields=("modified_at", "updated_at"),
        )
        office_activity, office_match_method = _office_activity(
            resolved_engine,
            job,
            normalized_job_id,
        )
        source_links = _job_source_links(job, documents)
        attention_items = _attention_items(job)
        for summary in tracking:
            if summary.get("tracking_warnings"):
                attention_items.append(
                    {
                        "type": "tracking_warning",
                        "severity": "warning",
                        "message": str(summary["tracking_warnings"]),
                        "job_id": normalized_job_id,
                    }
                )
        data_freshness = {
            "job_data_as_of": _latest_value(
                job.get(key)
                for key in ("refreshed_at", "updated_at", "last_scanned_at")
            ),
            "schedule_as_of": _latest_value(
                [job.get("schedule_updated_at")]
            )
            if job.get("schedule_id")
            else None,
            "tracking_as_of": _latest_value(
                row.get("updated_at") for row in tracking + daily_entries
            ),
            "documents_as_of": _latest_value(
                row.get("modified_at") or row.get("updated_at")
                for row in documents
            ),
            "office_activity_as_of": _latest_value(
                row.get("work_date") or row.get("updated_at")
                for row in office_activity
            ),
        }
        source_tables = [relation]
        for name, rows in (
            ("job_workflow_overrides", [job] if job.get("workflow_status") else []),
            ("crew_schedule", [job] if job.get("schedule_id") else []),
            ("job_tracking_summary", tracking),
            ("job_tracking_daily_entries", daily_entries),
            ("documents", documents),
            ("office_timesheet_entries", office_activity),
        ):
            if rows:
                source_tables.append(name)
        warnings: list[str] = []
        if office_match_method == "exact_project_name":
            warnings.append(
                "Office activity is an exact normalized project-name match, not an authoritative job_id link."
            )
        return {
            "schema_version": "spraytec.job_context.v1",
            "as_of": _latest_value(data_freshness.values()) or _utc_now(),
            "job_id": normalized_job_id,
            "job": _job_search_record(job),
            "workflow": _select_present(job, WORKFLOW_FIELDS),
            "schedule": _schedule_payload(job),
            "tracking_summary": tracking,
            "recent_daily_tracking": daily_entries,
            "recent_office_activity": [
                {**row, "match_method": office_match_method}
                for row in office_activity
            ],
            "documents": documents,
            "attention_items": attention_items[:25],
            "source_links": source_links,
            "source_tables": source_tables,
            "data_freshness": data_freshness,
            "coverage": {
                "tracking_summary_records": len(tracking),
                "daily_tracking_records": len(daily_entries),
                "daily_tracking_total_records": daily_entry_total,
                "daily_tracking_limit": MAX_CONTEXT_DAILY_ENTRIES,
                "daily_tracking_records_truncated": (
                    daily_entry_total > len(daily_entries)
                ),
                "document_records": len(documents),
                "document_limit": MAX_CONTEXT_DOCUMENTS,
                "office_activity_records": len(office_activity),
                "office_activity_limit": MAX_CONTEXT_TIMESHEET_ENTRIES,
                "office_activity_match_method": office_match_method,
            },
            "warnings": warnings,
            "response_budget": {
                "max_documents": MAX_CONTEXT_DOCUMENTS,
                "max_daily_tracking_records": MAX_CONTEXT_DAILY_ENTRIES,
                "max_office_activity_records": MAX_CONTEXT_TIMESHEET_ENTRIES,
            },
        }
    finally:
        if owns_engine:
            resolved_engine.dispose()


def _resolve_engine(
    database_url: str | None,
    engine: Engine | None,
) -> tuple[Engine, bool]:
    if engine is not None:
        return engine, False
    if not str(database_url or "").strip():
        raise JobIntelligenceUnavailableError("A database URL is required.")
    return _shared_engine(str(database_url)), False


@lru_cache(maxsize=2)
def _shared_engine(database_url: str) -> Engine:
    """Reuse the resilient read pool and relation metadata across API calls."""
    return create_resilient_engine(database_url)


def _relation_names(engine: Engine) -> set[str]:
    cached = getattr(engine, "_spraytec_relation_names", None)
    if cached is not None:
        return set(cached)
    inspector = inspect(engine)
    names = set(inspector.get_table_names()) | set(inspector.get_view_names())
    setattr(engine, "_spraytec_relation_names", names)
    return names


def _has_relation(engine: Engine, relation: str) -> bool:
    return relation in _relation_names(engine)


def _columns(engine: Engine, relation: str) -> set[str]:
    if relation not in _relation_names(engine):
        return set()
    cache = getattr(engine, "_spraytec_relation_columns", None)
    if cache is None:
        cache = {}
        setattr(engine, "_spraytec_relation_columns", cache)
    if relation not in cache:
        cache[relation] = {
            str(column["name"]) for column in inspect(engine).get_columns(relation)
        }
    return set(cache[relation])


def _job_relation(engine: Engine) -> tuple[str, set[str]]:
    names = _relation_names(engine)
    for relation in ("job_board_static_snapshot", "dashboard_jobs"):
        if relation not in names:
            continue
        columns = _columns(engine, relation)
        if "job_id" not in columns:
            continue
        count = _query_rows(
            engine,
            text(f"SELECT COUNT(*) AS row_count FROM {relation}"),
        )
        if count and int(count[0].get("row_count") or 0) > 0:
            return relation, columns
    raise JobIntelligenceUnavailableError(
        "No populated job board relation with stable job_id values is available."
    )


def _query_rows(
    engine: Engine,
    statement: Any,
    params: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        rows = connection.execute(statement, dict(params or {})).mappings().all()
    return [_json_record(row) for row in rows]


def _related_rows(
    engine: Engine,
    relation: str,
    job_id: str,
    fields: Iterable[str],
    *,
    limit: int,
    order_fields: Iterable[str],
) -> list[dict[str, Any]]:
    columns = _columns(engine, relation)
    if "job_id" not in columns:
        return []
    selected = _available_fields(columns, fields)
    if not selected:
        return []
    order = [field for field in order_fields if field in columns]
    order_sql = (
        " ORDER BY "
        + ", ".join(f"{field} DESC NULLS LAST" for field in order)
        if order
        else ""
    )
    return _query_rows(
        engine,
        text(
            f"SELECT {', '.join(selected)} FROM {relation} "
            f"WHERE job_id = :job_id{order_sql} LIMIT :row_limit"
        ),
        {"job_id": job_id, "row_limit": limit},
    )


def _related_row_count(engine: Engine, relation: str, job_id: str) -> int:
    columns = _columns(engine, relation)
    if "job_id" not in columns:
        return 0
    rows = _query_rows(
        engine,
        text(
            f"SELECT COUNT(*) AS row_count FROM {relation} "
            "WHERE job_id = :job_id"
        ),
        {"job_id": job_id},
    )
    return int(rows[0].get("row_count") or 0) if rows else 0


def _enrich_jobs(
    engine: Engine,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not rows:
        return []
    job_ids = [str(row["job_id"]) for row in rows if row.get("job_id")]
    workflow = _related_map(
        engine,
        "job_workflow_overrides",
        job_ids,
        WORKFLOW_FIELDS,
    )
    schedules = _related_map(
        engine,
        "crew_schedule",
        job_ids,
        SCHEDULE_FIELDS,
        order_fields=("estimated_start_date", "updated_at"),
    )
    enriched: list[dict[str, Any]] = []
    for original in rows:
        row = dict(original)
        job_id = str(row.get("job_id") or "")
        for key, value in workflow.get(job_id, {}).items():
            row[key if key != "updated_at" else "workflow_updated_at"] = value
        for key, value in schedules.get(job_id, {}).items():
            row[key if key != "updated_at" else "schedule_updated_at"] = value
        enriched.append(row)
    return enriched


def _related_map(
    engine: Engine,
    relation: str,
    job_ids: list[str],
    fields: Iterable[str],
    *,
    order_fields: Iterable[str] = ("updated_at",),
) -> dict[str, dict[str, Any]]:
    columns = _columns(engine, relation)
    if not job_ids or "job_id" not in columns:
        return {}
    selected = _available_fields(columns, fields)
    order = [field for field in order_fields if field in columns]
    order_sql = (
        " ORDER BY job_id, "
        + ", ".join(f"{field} DESC NULLS LAST" for field in order)
        if order
        else " ORDER BY job_id"
    )
    statement = text(
        f"SELECT job_id, {', '.join(selected)} FROM {relation} "
        f"WHERE job_id IN :job_ids{order_sql}"
    ).bindparams(bindparam("job_ids", expanding=True))
    rows = _query_rows(engine, statement, {"job_ids": job_ids})
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        job_id = str(row.pop("job_id", "") or "")
        result.setdefault(job_id, row)
    return result


def _office_activity(
    engine: Engine,
    job: Mapping[str, Any],
    job_id: str,
) -> tuple[list[dict[str, Any]], str]:
    relation = "office_timesheet_entries"
    columns = _columns(engine, relation)
    selected = _available_fields(columns, TIMESHEET_FIELDS)
    if not selected:
        return [], "none"
    order_field = "work_date" if "work_date" in columns else "updated_at"
    if "job_id" in columns:
        direct = _query_rows(
            engine,
            text(
                f"SELECT {', '.join(selected)} FROM {relation} "
                f"WHERE job_id = :job_id ORDER BY {order_field} DESC NULLS LAST "
                "LIMIT :row_limit"
            ),
            {"job_id": job_id, "row_limit": MAX_CONTEXT_TIMESHEET_ENTRIES},
        )
        if direct:
            return direct, "job_id"
    if "project_name" not in columns:
        return [], "none"
    job_name = str(job.get("job_name") or "").strip()
    if not job_name:
        return [], "none"
    job_relation, job_columns = _job_relation(engine)
    if "job_name" not in job_columns:
        return [], "none"
    matching_jobs = _query_rows(
        engine,
        text(
            f"SELECT COUNT(*) AS row_count FROM {job_relation} "
            "WHERE LOWER(TRIM(job_name)) = :project_name"
        ),
        {"project_name": job_name.lower()},
    )
    if not matching_jobs or int(matching_jobs[0].get("row_count") or 0) != 1:
        return [], "none"
    statement = text(
        f"SELECT {', '.join(selected)} FROM {relation} "
        "WHERE LOWER(TRIM(project_name)) IN :project_names "
        f"ORDER BY {order_field} DESC NULLS LAST LIMIT :row_limit"
    ).bindparams(bindparam("project_names", expanding=True))
    rows = _query_rows(
        engine,
        statement,
        {
            "project_names": [job_name.lower()],
            "row_limit": MAX_CONTEXT_TIMESHEET_ENTRIES,
        },
    )
    return rows, "exact_project_name" if rows else "none"


def _job_search_record(row: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "job_id",
        "division",
        "pipeline_status",
        "status",
        "workflow_status",
        "customer",
        "job_name",
        "job_type",
        "site_address",
        "city",
        "state",
        "estimated_sqft",
        "estimated_value",
        "estimated_value_source",
        "final_price",
        "price_per_sqft",
        "has_proposal",
        "has_signed_contract",
        "has_invoice",
        "has_job_spec",
        "has_aerial",
        "photo_count",
        "deal_owner",
        "assigned_user",
        "follow_up_date",
        "priority",
        "assigned_crew_leader",
        "estimated_start_date",
        "estimated_end_date",
        "schedule_status",
        "blocking_issue",
        "warnings",
        "updated_at",
        "refreshed_at",
    )
    record = _select_present(row, fields)
    record["attention_items"] = _attention_items(row)
    return record


def _schedule_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    payload = _select_present(row, SCHEDULE_FIELDS)
    if "schedule_updated_at" in row:
        payload["updated_at"] = row["schedule_updated_at"]
    return payload


def _attention_items(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    job_id = str(row.get("job_id") or "")
    items: list[dict[str, Any]] = []
    for field, label in (
        ("completed_missing_invoice", "Completed job is missing an invoice."),
        ("completed_missing_final_price", "Completed job is missing a final price."),
        ("missing_signed_contract", "Signed contract is missing."),
        ("missing_job_spec", "Job specification is missing."),
    ):
        if row.get(field) is True:
            items.append(
                {
                    "type": field,
                    "severity": "warning",
                    "message": label,
                    "job_id": job_id,
                }
            )
    if str(row.get("warnings") or "").strip():
        items.append(
            {
                "type": "source_warning",
                "severity": "warning",
                "message": str(row["warnings"]).strip(),
                "job_id": job_id,
            }
        )
    if str(row.get("blocking_issue") or "").strip():
        items.append(
            {
                "type": "schedule_blocker",
                "severity": "warning",
                "message": str(row["blocking_issue"]).strip(),
                "job_id": job_id,
            }
        )
    return items


def _job_source_links(
    job: Mapping[str, Any],
    documents: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    job_id = str(job.get("job_id") or "")
    candidates = (
        ("job_folder", job.get("folder_url") or job.get("folder_link_or_path")),
        ("primary_document", job.get("primary_doc_link")),
        ("proposal", job.get("proposal_url")),
        ("estimate", job.get("estimate_url")),
        ("contract", job.get("contract_url")),
        ("invoice", job.get("invoice_url")),
        ("job_tracking", job.get("job_tracking_url")),
        ("warranty", job.get("warranty_url")),
        ("aerial", job.get("aerial_url")),
    )
    for source_type, url in candidates:
        if str(url or "").lower().startswith(("http://", "https://")):
            links.append(
                {
                    "source_type": source_type,
                    "job_id": job_id,
                    "label": source_type.replace("_", " ").title(),
                    "url": str(url),
                }
            )
    for document in documents:
        url = str(document.get("sharepoint_url") or "")
        if not url.lower().startswith(("http://", "https://")):
            continue
        links.append(
            {
                "source_type": "document",
                "job_id": job_id,
                "document_id": str(document.get("document_id") or ""),
                "label": str(document.get("file_name") or "Document"),
                "url": url,
            }
        )
    return _dedupe_links(links)


def _dedupe_links(
    links: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for original in links:
        link = dict(original)
        url = str(link.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        result.append(link)
    return result


def _attention_sql_parts(columns: set[str]) -> list[str]:
    parts: list[str] = []
    for field in (
        "has_warnings",
        "completed_missing_invoice",
        "completed_missing_final_price",
        "missing_signed_contract",
        "missing_job_spec",
    ):
        if field in columns:
            parts.append(f"COALESCE(j.{field}, FALSE) = TRUE")
    if "warnings" in columns:
        parts.append("COALESCE(TRIM(j.warnings), '') <> ''")
    return parts


def _available_fields(
    columns: set[str],
    requested: Iterable[str],
) -> list[str]:
    return [field for field in requested if field in columns]


def _select_present(
    row: Mapping[str, Any],
    fields: Iterable[str],
) -> dict[str, Any]:
    return {
        field: row[field]
        for field in fields
        if field in row and row[field] is not None
    }


def _unique_nonblank(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _json_record(row: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): _json_value(value) for key, value in row.items()}


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _latest_value(values: Iterable[Any]) -> str | None:
    normalized = [str(value) for value in values if value not in (None, "")]
    return max(normalized) if normalized else None


def _sum_numeric(values: Iterable[Any]) -> float:
    total = 0.0
    for value in values:
        try:
            total += float(value or 0)
        except (TypeError, ValueError):
            continue
    return round(total, 2)


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
