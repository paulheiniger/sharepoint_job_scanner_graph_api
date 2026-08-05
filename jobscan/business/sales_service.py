from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

from jobscan.business.job_service import (
    MAX_JOB_SEARCH_RESULTS,
    JobIntelligenceUnavailableError,
    _available_fields,
    _columns,
    _dedupe_links,
    _enrich_jobs,
    _job_relation,
    _job_source_links,
    _latest_value,
    _query_rows,
    _resolve_engine,
    _sum_numeric,
    _utc_now,
    _unique_nonblank,
)


MAX_SALES_SOURCE_ROWS = 500

SALES_FIELDS = (
    "job_id",
    "source_year",
    "division",
    "pipeline_status",
    "status",
    "customer",
    "job_name",
    "job_type",
    "estimated_value",
    "estimated_value_source",
    "estimated_sqft",
    "price_per_sqft",
    "has_proposal",
    "has_signed_contract",
    "has_warnings",
    "warnings",
    "estimate_file",
    "proposal_file",
    "proposal_file_created_at",
    "proposal_file_modified_at",
    "proposal_file_modified_by",
    "estimate_file_modified_at",
    "estimate_file_modified_by",
    "vsimple_deal_owner",
    "vsimple_estimator",
    "vsimple_lead_source",
    "vsimple_referral_source",
    "proposal_url",
    "estimate_url",
    "folder_link_or_path",
    "folder_url",
    "folder_path",
    "updated_at",
    "refreshed_at",
)

OPEN_PIPELINE_STATUSES = ("Proposed", "Contracted", "Contracted Repairs")


def get_sales_pipeline(
    *,
    database_url: str | None = None,
    engine: Engine | None = None,
    division: str = "",
    owner: str = "",
    job_year: int | None = None,
    pipeline_statuses: list[str] | None = None,
    include_completed: bool = False,
    limit: int = 10,
) -> dict[str, Any]:
    resolved_engine, owns_engine = _resolve_engine(database_url, engine)
    try:
        relation, columns = _job_relation(resolved_engine)
        selected = _available_fields(columns, SALES_FIELDS)
        statuses = _unique_nonblank(pipeline_statuses or [])
        conditions: list[str] = []
        params: dict[str, Any] = {"source_limit": MAX_SALES_SOURCE_ROWS}
        if division.strip() and "division" in columns:
            conditions.append("LOWER(COALESCE(j.division, '')) = :division")
            params["division"] = division.strip().lower()
        if job_year is not None:
            if "source_year" not in columns:
                raise JobIntelligenceUnavailableError(
                    "The job source does not expose source_year for job-year filtering."
                )
            conditions.append("CAST(j.source_year AS TEXT) = :job_year")
            params["job_year"] = str(job_year)
        if statuses and "pipeline_status" in columns:
            conditions.append("j.pipeline_status IN :pipeline_statuses")
            params["pipeline_statuses"] = statuses
        elif not include_completed and "pipeline_status" in columns:
            conditions.append("j.pipeline_status IN :pipeline_statuses")
            params["pipeline_statuses"] = list(OPEN_PIPELINE_STATUSES)
        sql = (
            f"SELECT {', '.join(f'j.{field}' for field in selected)} "
            f"FROM {relation} j"
        )
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += (
            " ORDER BY j.estimated_value DESC NULLS LAST"
            if "estimated_value" in columns
            else " ORDER BY j.job_id"
        )
        sql += " LIMIT :source_limit"
        statement = text(sql)
        if "pipeline_statuses" in params:
            statement = statement.bindparams(
                bindparam("pipeline_statuses", expanding=True)
            )
        rows = _enrich_jobs(
            resolved_engine,
            _query_rows(resolved_engine, statement, params),
        )
        for row in rows:
            row["owner"] = _sales_owner(row)
            row["owner_source"] = _sales_owner_source(row)
            row["lead_source"] = _sales_lead_source(row)
        if owner.strip():
            owner_key = owner.strip().lower()
            rows = [
                row
                for row in rows
                if _sales_owner(row).lower() == owner_key
            ]
        applied_limit = max(1, min(int(limit), MAX_JOB_SEARCH_RESULTS))
        top_rows = rows[:applied_limit]
        stage_rollup = _stage_rollup(rows)
        owner_rollup = _owner_rollup(rows)
        as_of = _latest_value(
            row.get(key)
            for row in rows
            for key in ("refreshed_at", "updated_at", "workflow_updated_at")
        )
        records = [_sales_record(row) for row in top_rows]
        return {
            "schema_version": "spraytec.sales_pipeline.v1",
            "as_of": as_of or _utc_now(),
            "filters_applied": {
                key: value
                for key, value in {
                    "division": division.strip() or None,
                    "owner": owner.strip() or None,
                    "job_year": job_year,
                    "pipeline_statuses": statuses or None,
                    "include_completed": include_completed,
                    "limit": applied_limit,
                }.items()
                if value is not None
            },
            "headline_metrics": {
                "job_count": len(rows),
                "pipeline_value": _sum_numeric(
                    row.get("estimated_value") for row in rows
                ),
                "proposed_jobs": sum(
                    row.get("pipeline_status") == "Proposed" for row in rows
                ),
                "proposed_value": _sum_numeric(
                    row.get("estimated_value")
                    for row in rows
                    if row.get("pipeline_status") == "Proposed"
                ),
                "contracted_jobs": sum(
                    row.get("pipeline_status")
                    in {"Contracted", "Contracted Repairs"}
                    for row in rows
                ),
                "contracted_value": _sum_numeric(
                    row.get("estimated_value")
                    for row in rows
                    if row.get("pipeline_status")
                    in {"Contracted", "Contracted Repairs"}
                ),
                "unassigned_jobs": sum(not _sales_owner(row) for row in rows),
                "inferred_owner_jobs": sum(
                    _sales_owner_source(row).endswith("_modified_by")
                    for row in rows
                ),
                "jobs_with_warnings": sum(
                    bool(str(row.get("warnings") or "").strip())
                    or row.get("has_warnings") is True
                    for row in rows
                ),
            },
            "stage_rollup": stage_rollup,
            "owner_rollup": owner_rollup,
            "records": records,
            "attention_items": _pipeline_attention_items(rows)[:25],
            "source_links": _dedupe_links(
                link
                for row in top_rows
                for link in _job_source_links(row, [])
            ),
            "source_tables": [
                relation,
                *(
                    ["job_workflow_overrides"]
                    if _columns(resolved_engine, "job_workflow_overrides")
                    else []
                ),
            ],
            "data_freshness": {"sales_data_as_of": as_of},
            "coverage": {
                "source_row_limit": MAX_SALES_SOURCE_ROWS,
                "source_rows": len(rows),
                "result_limit": applied_limit,
                "results_truncated": len(rows) > applied_limit,
            },
            "warnings": (
                [
                    "Pipeline aggregation reached the bounded source-row limit; narrow filters for a complete total."
                ]
                if len(rows) >= MAX_SALES_SOURCE_ROWS
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


def get_sales_followups(
    *,
    database_url: str | None = None,
    engine: Engine | None = None,
    division: str = "",
    owner: str = "",
    job_year: int | None = None,
    followup_status: str = "",
    overdue_only: bool = False,
    unassigned_only: bool = False,
    limit: int = 10,
) -> dict[str, Any]:
    resolved_engine, owns_engine = _resolve_engine(database_url, engine)
    try:
        relation = "dashboard_sales_followup"
        columns = _columns(resolved_engine, relation)
        if "job_id" not in columns:
            raise JobIntelligenceUnavailableError(
                "The prepared sales follow-up view is unavailable."
            )
        selected = _available_fields(columns, SALES_FIELDS + ("followup_status",))
        conditions: list[str] = []
        params: dict[str, Any] = {"source_limit": MAX_SALES_SOURCE_ROWS}
        if division.strip() and "division" in columns:
            conditions.append("LOWER(COALESCE(division, '')) = :division")
            params["division"] = division.strip().lower()
        if followup_status.strip() and "followup_status" in columns:
            conditions.append(
                "LOWER(COALESCE(followup_status, '')) = :followup_status"
            )
            params["followup_status"] = followup_status.strip().lower()
        sql = f"SELECT {', '.join(selected)} FROM {relation}"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += (
            " ORDER BY estimated_value DESC NULLS LAST"
            if "estimated_value" in columns
            else " ORDER BY job_id"
        )
        sql += " LIMIT :source_limit"
        rows = _enrich_jobs(
            resolved_engine,
            _query_rows(resolved_engine, text(sql), params),
        )
        rows = _sales_snapshot_enrichment(resolved_engine, rows)
        if job_year is not None:
            rows = [
                row
                for row in rows
                if str(row.get("source_year") or "").strip() == str(job_year)
            ]
        today = date.today().isoformat()
        for row in rows:
            row["follow_up_state"] = _follow_up_state(row.get("follow_up_date"), today)
            row["owner"] = _sales_owner(row)
            row["owner_source"] = _sales_owner_source(row)
            row["lead_source"] = _sales_lead_source(row)
            age_days = _proposal_age_days(row, today)
            row["opportunity_age_days"] = age_days
            row["opportunity_freshness"] = _opportunity_freshness(age_days)
        if owner.strip():
            owner_key = owner.strip().lower()
            rows = [
                row
                for row in rows
                if str(row.get("owner") or "").lower() == owner_key
            ]
        if overdue_only:
            rows = [row for row in rows if row["follow_up_state"] == "overdue"]
        if unassigned_only:
            rows = [row for row in rows if not row.get("owner")]
        rows.sort(key=_followup_sort_key)
        applied_limit = max(1, min(int(limit), MAX_JOB_SEARCH_RESULTS))
        selected_rows = rows[:applied_limit]
        records = [_sales_record(row, include_followup=True) for row in selected_rows]
        as_of = _latest_value(
            row.get(key)
            for row in rows
            for key in ("updated_at", "workflow_updated_at")
        )
        return {
            "schema_version": "spraytec.sales_followups.v1",
            "as_of": as_of or _utc_now(),
            "filters_applied": {
                key: value
                for key, value in {
                    "division": division.strip() or None,
                    "owner": owner.strip() or None,
                    "job_year": job_year,
                    "followup_status": followup_status.strip() or None,
                    "overdue_only": overdue_only,
                    "unassigned_only": unassigned_only,
                    "limit": applied_limit,
                }.items()
                if value is not None
            },
            "headline_metrics": {
                "matching_followups": len(rows),
                "proposed_value": _sum_numeric(
                    row.get("estimated_value") for row in rows
                ),
                "overdue_followups": sum(
                    row.get("follow_up_state") == "overdue" for row in rows
                ),
                "followups_due_today": sum(
                    row.get("follow_up_state") == "due_today" for row in rows
                ),
                "missing_followup_date": sum(
                    row.get("follow_up_state") == "no_date" for row in rows
                ),
                "unassigned_followups": sum(not row.get("owner") for row in rows),
                "inferred_owner_followups": sum(
                    str(row.get("owner_source") or "").endswith("_modified_by")
                    for row in rows
                ),
                "data_quality_followups": sum(
                    row.get("followup_status") != "Ready for follow-up"
                    for row in rows
                ),
                "stale_opportunities": sum(
                    row.get("opportunity_freshness") == "stale" for row in rows
                ),
                "opportunities_without_proposal_date": sum(
                    row.get("opportunity_freshness") == "no_proposal_date"
                    for row in rows
                ),
            },
            "stage_rollup": _followup_status_rollup(rows),
            "owner_rollup": _owner_rollup(rows),
            "records": records,
            "attention_items": _followup_attention_items(rows)[:25],
            "source_links": _dedupe_links(
                link
                for row in selected_rows
                for link in _job_source_links(row, [])
            ),
            "source_tables": [
                relation,
                *(
                    ["job_workflow_overrides"]
                    if _columns(resolved_engine, "job_workflow_overrides")
                    else []
                ),
            ],
            "data_freshness": {"sales_followup_as_of": as_of},
            "coverage": {
                "source_row_limit": MAX_SALES_SOURCE_ROWS,
                "source_rows": len(rows),
                "result_limit": applied_limit,
                "results_truncated": len(rows) > applied_limit,
            },
            "warnings": (
                [
                    "Follow-up aggregation reached the bounded source-row limit; narrow filters for a complete queue."
                ]
                if len(rows) >= MAX_SALES_SOURCE_ROWS
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


def _sales_record(
    row: dict[str, Any],
    *,
    include_followup: bool = False,
) -> dict[str, Any]:
    fields = [
        "job_id",
        "source_year",
        "division",
        "pipeline_status",
        "status",
        "customer",
        "job_name",
        "job_type",
        "estimated_value",
        "estimated_value_source",
        "estimated_sqft",
        "price_per_sqft",
        "has_proposal",
        "has_signed_contract",
        "workflow_status",
        "deal_owner",
        "assigned_user",
        "priority",
        "owner",
        "owner_source",
        "lead_source",
        "vsimple_deal_owner",
        "vsimple_estimator",
        "vsimple_lead_source",
        "vsimple_referral_source",
        "warnings",
        "updated_at",
    ]
    if include_followup:
        fields.extend(
            [
                "followup_status",
                "follow_up_date",
                "follow_up_state",
                "owner",
                "opportunity_age_days",
                "opportunity_freshness",
                "internal_notes",
            ]
        )
    return {
        field: row[field]
        for field in fields
        if field in row and row[field] is not None
    }


def _sales_snapshot_enrichment(
    engine: Engine,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not rows:
        return rows
    relation, columns = _job_relation(engine)
    enrichment_fields = _available_fields(
        columns,
        (
            "vsimple_deal_owner",
            "vsimple_estimator",
            "vsimple_lead_source",
            "vsimple_referral_source",
            "proposal_file_created_at",
            "proposal_file_modified_at",
            "proposal_file_modified_by",
            "estimate_file_modified_at",
            "estimate_file_modified_by",
            "proposal_url",
            "estimate_url",
            "source_year",
        ),
    )
    if not enrichment_fields:
        return rows
    job_ids = _unique_nonblank(row.get("job_id") for row in rows)
    statement = text(
        f"SELECT job_id, {', '.join(enrichment_fields)} FROM {relation} "
        "WHERE job_id IN :job_ids"
    ).bindparams(bindparam("job_ids", expanding=True))
    enrichment = {
        str(row["job_id"]): row
        for row in _query_rows(engine, statement, {"job_ids": job_ids})
    }
    for row in rows:
        extra = enrichment.get(str(row.get("job_id") or ""), {})
        for field in enrichment_fields:
            if row.get(field) in (None, "") and extra.get(field) not in (None, ""):
                row[field] = extra[field]
    return rows


def _stage_rollup(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("pipeline_status") or "Not captured")].append(row)
    return [
        {
            "pipeline_status": status,
            "job_count": len(items),
            "estimated_value": _sum_numeric(
                item.get("estimated_value") for item in items
            ),
        }
        for status, items in sorted(grouped.items())
    ]


def _followup_status_rollup(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("followup_status") or "Not captured")].append(row)
    return [
        {
            "followup_status": status,
            "job_count": len(items),
            "estimated_value": _sum_numeric(
                item.get("estimated_value") for item in items
            ),
        }
        for status, items in sorted(grouped.items())
    ]


def _owner_rollup(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_sales_owner(row) or "Unassigned"].append(row)
    return sorted(
        [
            {
                "owner": owner,
                "job_count": len(items),
                "inferred_job_count": sum(
                    _sales_owner_source(item).endswith("_modified_by")
                    for item in items
                ),
                "estimated_value": _sum_numeric(
                    item.get("estimated_value") for item in items
                ),
            }
            for owner, items in grouped.items()
        ],
        key=lambda item: (-float(item["estimated_value"]), item["owner"]),
    )


def _sales_owner(row: dict[str, Any]) -> str:
    source = _sales_owner_source(row)
    return str(row.get(source) or "").strip() if source != "not_captured" else ""


def _sales_owner_source(row: dict[str, Any]) -> str:
    for field in ("deal_owner", "assigned_user"):
        if str(row.get(field) or "").strip():
            return field

    sharepoint_source = _latest_sharepoint_editor_source(row)
    if sharepoint_source:
        return sharepoint_source

    # VSimple is a historical export rather than an actively synchronized source,
    # so use it only after current workflow and SharePoint activity evidence.
    for field in ("vsimple_deal_owner", "vsimple_estimator"):
        if str(row.get(field) or "").strip():
            return field
    return "not_captured"


NON_PERSON_SHAREPOINT_EDITORS = {
    "communications intern",
    "estimating",
    "microsoft sharepoint",
    "sharepoint app",
    "system account",
}


def _latest_sharepoint_editor_source(row: dict[str, Any]) -> str:
    candidates: list[tuple[str, str, str]] = []
    for source_field, timestamp_field in (
        ("proposal_file_modified_by", "proposal_file_modified_at"),
        ("estimate_file_modified_by", "estimate_file_modified_at"),
    ):
        editor = str(row.get(source_field) or "").strip()
        if not editor or editor.lower() in NON_PERSON_SHAREPOINT_EDITORS:
            continue
        timestamp = str(row.get(timestamp_field) or "").strip()
        candidates.append((timestamp, source_field, editor))
    if not candidates:
        return ""
    # ISO date/time values sort chronologically. If timestamps are missing, the
    # proposal editor wins because proposal activity is the stronger sales signal.
    candidates.sort(
        key=lambda item: (bool(item[0]), item[0], item[1] == "proposal_file_modified_by"),
        reverse=True,
    )
    return candidates[0][1]


def _sales_lead_source(row: dict[str, Any]) -> str:
    return str(
        row.get("vsimple_lead_source")
        or row.get("vsimple_referral_source")
        or ""
    ).strip()


def _follow_up_state(value: Any, today: str) -> str:
    normalized = str(value or "").strip()[:10]
    if not normalized:
        return "no_date"
    if normalized < today:
        return "overdue"
    if normalized == today:
        return "due_today"
    return "upcoming"


def _followup_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    state_rank = {
        "overdue": 0,
        "due_today": 1,
        "upcoming": 2,
        "no_date": 3,
    }
    return (
        state_rank.get(str(row.get("follow_up_state")), 4),
        str(row.get("follow_up_date") or "9999-12-31"),
        -float(row.get("estimated_value") or 0),
        str(row.get("job_id") or ""),
    )


def _proposal_age_days(row: dict[str, Any], today: str) -> int | None:
    value = (
        row.get("proposal_file_modified_at")
        or row.get("proposal_file_created_at")
    )
    normalized = str(value or "").strip()[:10]
    if not normalized:
        return None
    try:
        return max(
            (
                datetime.fromisoformat(today)
                - datetime.fromisoformat(normalized)
            ).days,
            0,
        )
    except ValueError:
        return None


def _opportunity_freshness(age_days: int | None) -> str:
    if age_days is None:
        return "no_proposal_date"
    if age_days <= 30:
        return "fresh"
    if age_days <= 90:
        return "aging"
    return "stale"


def _pipeline_attention_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in rows:
        if not _sales_owner(row):
            items.append(
                {
                    "type": "unassigned_opportunity",
                    "severity": "warning",
                    "job_id": row.get("job_id"),
                    "message": "No deal owner or assigned user is captured.",
                }
            )
        if str(row.get("warnings") or "").strip():
            items.append(
                {
                    "type": "sales_warning",
                    "severity": "warning",
                    "job_id": row.get("job_id"),
                    "message": str(row["warnings"]).strip(),
                }
            )
    return items


def _followup_attention_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in rows:
        if row.get("follow_up_state") == "overdue":
            items.append(
                {
                    "type": "overdue_followup",
                    "severity": "warning",
                    "job_id": row.get("job_id"),
                    "message": f"Follow-up was due {row.get('follow_up_date')}.",
                }
            )
        elif row.get("follow_up_state") == "no_date":
            items.append(
                {
                    "type": "missing_followup_date",
                    "severity": "info",
                    "job_id": row.get("job_id"),
                    "message": "No follow-up date is captured.",
                }
            )
        if not row.get("owner"):
            items.append(
                {
                    "type": "unassigned_followup",
                    "severity": "warning",
                    "job_id": row.get("job_id"),
                    "message": "No deal owner or assigned user is captured.",
                }
            )
        if row.get("opportunity_freshness") == "stale":
            items.append(
                {
                    "type": "stale_opportunity",
                    "severity": "warning",
                    "job_id": row.get("job_id"),
                    "message": (
                        "Latest proposal evidence is "
                        f"{row.get('opportunity_age_days')} days old."
                    ),
                }
            )
        if row.get("followup_status") not in (None, "Ready for follow-up"):
            items.append(
                {
                    "type": "followup_data_quality",
                    "severity": "warning",
                    "job_id": row.get("job_id"),
                    "message": str(row.get("followup_status")),
                }
            )
    return items
