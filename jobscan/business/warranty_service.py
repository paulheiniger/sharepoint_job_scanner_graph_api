from __future__ import annotations

from collections import defaultdict
from datetime import date
import json
from typing import Any, Iterable, Mapping

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

from jobscan.business.job_service import (
    MAX_JOB_SEARCH_RESULTS,
    JobIntelligenceUnavailableError,
    _columns,
    _dedupe_links,
    _latest_value,
    _query_rows,
    _resolve_engine,
    _utc_now,
    _unique_nonblank,
)


WARRANTY_RELATION = "job_warranty_summary"
MAX_WARRANTY_SOURCE_ROWS = 500

WARRANTY_FIELDS = (
    "warranty_summary_id",
    "job_id",
    "source_year",
    "division",
    "customer",
    "job_name",
    "warranty_status",
    "warranty_category",
    "warranty_type",
    "provider",
    "duration_years",
    "coverage_summary",
    "coverage_excerpt",
    "start_date",
    "start_date_source",
    "start_date_confidence",
    "start_date_is_inferred",
    "expiration_date",
    "source_document_id",
    "source_file",
    "source_url",
    "duration_source_kind",
    "duration_source_document_id",
    "evidence_count",
    "issued_evidence_count",
    "reported_evidence_count",
    "proposed_evidence_count",
    "conflicting_duration_count",
    "has_conflict",
    "refreshed_at",
)


def get_warranty_summary(
    *,
    database_url: str | None = None,
    engine: Engine | None = None,
    job_ids: Iterable[str] = (),
    job_year: int | None = None,
    division: str = "",
    warranty_status: str = "",
    expiring_after: date | None = None,
    expiring_before: date | None = None,
    needs_review: bool | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    resolved_engine, owns_engine = _resolve_engine(database_url, engine)
    try:
        columns = _columns(resolved_engine, WARRANTY_RELATION)
        if "job_id" not in columns:
            raise JobIntelligenceUnavailableError(
                "The refreshed warranty summary is unavailable."
            )
        selected_fields = [field for field in WARRANTY_FIELDS if field in columns]
        requested_job_ids = _unique_nonblank(job_ids)[:MAX_JOB_SEARCH_RESULTS]
        conditions: list[str] = []
        params: dict[str, Any] = {"source_limit": MAX_WARRANTY_SOURCE_ROWS}
        if requested_job_ids:
            conditions.append("job_id IN :job_ids")
            params["job_ids"] = requested_job_ids
        if job_year is not None:
            if "source_year" not in columns:
                raise JobIntelligenceUnavailableError(
                    "The warranty summary does not expose source_year."
                )
            conditions.append("CAST(source_year AS TEXT) = :job_year")
            params["job_year"] = str(job_year)
        if division.strip() and "division" in columns:
            conditions.append("LOWER(COALESCE(division, '')) = :division")
            params["division"] = division.strip().lower()
        normalized_status = warranty_status.strip().lower()
        if normalized_status and "warranty_status" in columns:
            conditions.append("LOWER(COALESCE(warranty_status, '')) = :warranty_status")
            params["warranty_status"] = normalized_status
        if expiring_before is not None and "expiration_date" in columns:
            conditions.append("expiration_date <= :expiring_before")
            params["expiring_before"] = expiring_before
        if expiring_after is not None and "expiration_date" in columns:
            conditions.append("expiration_date >= :expiring_after")
            params["expiring_after"] = expiring_after
        if needs_review is not None:
            review_parts = []
            if "has_conflict" in columns:
                review_parts.append("COALESCE(has_conflict, FALSE)")
            if "duration_years" in columns:
                review_parts.append("duration_years IS NULL")
            if "start_date_confidence" in columns:
                review_parts.append(
                    "LOWER(COALESCE(start_date_confidence, 'unavailable')) IN ('low', 'unavailable')"
                )
            if review_parts:
                review_expression = "(" + " OR ".join(review_parts) + ")"
                conditions.append(review_expression if needs_review else f"NOT {review_expression}")

        sql = f"SELECT {', '.join(selected_fields)} FROM {WARRANTY_RELATION}"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        if "expiration_date" in columns:
            sql += " ORDER BY expiration_date ASC NULLS LAST, job_id"
        else:
            sql += " ORDER BY job_id"
        sql += " LIMIT :source_limit"
        statement = text(sql)
        if requested_job_ids:
            statement = statement.bindparams(bindparam("job_ids", expanding=True))
        rows = _query_rows(resolved_engine, statement, params)
        source_rows = _unmatched_source_rows(
            resolved_engine,
            job_ids=requested_job_ids,
            job_year=job_year,
            division=division,
            warranty_status=normalized_status,
            expiring_after=expiring_after,
            expiring_before=expiring_before,
            needs_review=needs_review,
        )
        rows.extend(source_rows)
        rows.sort(
            key=lambda row: (
                row.get("expiration_date") is None,
                str(row.get("expiration_date") or ""),
                str(row.get("job_id") or row.get("warranty_summary_id") or ""),
            )
        )
        rows = rows[:MAX_WARRANTY_SOURCE_ROWS]
        for row in rows:
            for field in ("start_date_is_inferred", "has_conflict"):
                if field in row and row[field] is not None:
                    row[field] = _truthy(row[field])
        applied_limit = max(1, min(int(limit), MAX_JOB_SEARCH_RESULTS))
        selected_rows = rows[:applied_limit]
        as_of = _latest_value(row.get("refreshed_at") for row in rows)
        attention_items = _attention_items(rows)[:25]
        data_quality_tasks = _data_quality_tasks(rows)[:25]
        return {
            "schema_version": "spraytec.warranty_summary.v2",
            "as_of": as_of or _utc_now(),
            "filters_applied": {
                key: value
                for key, value in {
                    "job_ids": requested_job_ids or None,
                    "job_year": job_year,
                    "division": division.strip() or None,
                    "warranty_status": normalized_status or None,
                    "expiring_after": expiring_after.isoformat()
                    if expiring_after
                    else None,
                    "expiring_before": expiring_before.isoformat()
                    if expiring_before
                    else None,
                    "needs_review": needs_review,
                    "limit": applied_limit,
                }.items()
                if value is not None
            },
            "headline_metrics": {
                "warranty_records": len(rows),
                "issued_warranties": sum(
                    str(row.get("warranty_status") or "").lower() == "issued"
                    for row in rows
                ),
                "reported_warranties": sum(
                    str(row.get("warranty_status") or "").lower() == "reported"
                    for row in rows
                ),
                "proposed_warranties": sum(
                    str(row.get("warranty_status") or "").lower() == "proposed"
                    for row in rows
                ),
                "inferred_start_dates": sum(
                    row.get("start_date_is_inferred") is True for row in rows
                ),
                "low_confidence_or_missing_start_dates": sum(
                    str(row.get("start_date_confidence") or "unavailable").lower()
                    in {"low", "unavailable"}
                    for row in rows
                ),
                "missing_duration": sum(
                    row.get("duration_years") in (None, "") for row in rows
                ),
                "conflicting_warranties": sum(
                    row.get("has_conflict") is True for row in rows
                ),
            },
            "status_rollup": _rollup(rows, "warranty_status"),
            "category_rollup": _rollup(rows, "warranty_category"),
            "records": selected_rows,
            "attention_items": attention_items,
            "review_queue_summary": _review_queue_summary(rows),
            "data_quality_tasks": data_quality_tasks,
            "source_links": _dedupe_links(_source_links(selected_rows)),
            "source_tables": [WARRANTY_RELATION, "job_warranty_evidence", "warranty_source_records"],
            "data_freshness": {"warranty_summary_as_of": as_of},
            "coverage": {
                "source_row_limit": MAX_WARRANTY_SOURCE_ROWS,
                "matching_records_before_result_limit": len(rows),
                "result_limit": applied_limit,
                "results_truncated": len(rows) > applied_limit,
            },
            "warnings": [
                "Proposed warranty terms are not evidence that a warranty was issued.",
                "Reported records come from legacy customer/VSimple snapshots and remain below issued warranty documents in source priority.",
                "Dates marked inferred use the reported start_date_source and confidence hierarchy.",
                *(
                    [
                        "Warranty aggregation reached the bounded source-row limit; narrow the filters for complete totals."
                    ]
                    if len(rows) >= MAX_WARRANTY_SOURCE_ROWS
                    else []
                ),
            ],
            "response_budget": {
                "max_records": MAX_JOB_SEARCH_RESULTS,
                "returned_records": len(selected_rows),
            },
        }
    finally:
        if owns_engine:
            resolved_engine.dispose()


def _rollup(rows: Iterable[Mapping[str, Any]], field: str) -> list[dict[str, Any]]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        label = str(row.get(field) or "Not captured").strip() or "Not captured"
        counts[label] += 1
    return [
        {field: label, "warranty_count": count}
        for label, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _unmatched_source_rows(
    engine: Engine,
    *,
    job_ids: list[str],
    job_year: int | None,
    division: str,
    warranty_status: str,
    expiring_after: date | None,
    expiring_before: date | None,
    needs_review: bool | None,
) -> list[dict[str, Any]]:
    if job_ids or "source_record_id" not in _columns(engine, "warranty_source_records"):
        return []
    conditions = ["(matched_job_id IS NULL OR COALESCE(match_review_required, TRUE))"]
    params: dict[str, Any] = {"source_limit": MAX_WARRANTY_SOURCE_ROWS}
    if job_year is not None:
        conditions.append("source_year = :source_year")
        params["source_year"] = job_year
    if division.strip():
        conditions.append("LOWER(COALESCE(division, '')) = :division")
        params["division"] = division.strip().lower()
    if warranty_status:
        if warranty_status == "issued":
            conditions.append("source_system = 'sharepoint_warranty_folder'")
        elif warranty_status == "reported":
            conditions.append("source_system <> 'sharepoint_warranty_folder'")
        else:
            return []
    if expiring_before is not None:
        conditions.append("expiration_date <= :expiring_before")
        params["expiring_before"] = expiring_before
    if expiring_after is not None:
        conditions.append("expiration_date >= :expiring_after")
        params["expiring_after"] = expiring_after
    if needs_review is not None:
        review_expression = "(COALESCE(match_review_required, TRUE) OR duration_years IS NULL OR start_date IS NULL)"
        conditions.append(review_expression if needs_review else f"NOT {review_expression}")
    query = text(
        """
        SELECT source_record_id, source_system, source_file, source_url, source_locator,
               source_year, division, reported_name, reported_customer, reported_address,
               warranty_category, warranty_type, provider, duration_years, start_date,
               expiration_date, expiration_date_source, has_date_conflict,
               coverage_summary, coverage_excerpt, matched_vsimple_id,
               match_method, match_confidence, match_score, match_candidates, match_review_required,
               extraction_confidence, updated_at
        FROM warranty_source_records
        WHERE """
        + " AND ".join(conditions)
        + " ORDER BY expiration_date ASC NULLS LAST, source_record_id LIMIT :source_limit"
    )
    source_rows = _query_rows(engine, query, params)
    out: list[dict[str, Any]] = []
    for row in source_rows:
        issued = row.get("source_system") == "sharepoint_warranty_folder"
        review_required = _truthy(row.get("match_review_required"))
        out.append(
            {
                "warranty_summary_id": row.get("source_record_id"),
                "job_id": None,
                "source_year": row.get("source_year"),
                "division": row.get("division"),
                "customer": row.get("reported_customer"),
                "job_name": row.get("reported_name"),
                "warranty_status": "issued" if issued else "reported",
                "warranty_category": row.get("warranty_category"),
                "warranty_type": row.get("warranty_type"),
                "provider": row.get("provider"),
                "duration_years": row.get("duration_years"),
                "coverage_summary": row.get("coverage_summary"),
                "coverage_excerpt": row.get("coverage_excerpt"),
                "start_date": row.get("start_date"),
                "start_date_source": "explicit_warranty_date" if issued else "legacy_reported_date",
                "start_date_confidence": row.get("extraction_confidence") or "medium",
                "start_date_is_inferred": False,
                "expiration_date": row.get("expiration_date"),
                "source_document_id": row.get("source_record_id"),
                "source_file": row.get("source_file"),
                "source_url": row.get("source_url"),
                "duration_source_kind": row.get("source_system"),
                "duration_source_document_id": row.get("source_record_id"),
                "evidence_count": 1,
                "issued_evidence_count": 1 if issued else 0,
                "reported_evidence_count": 0 if issued else 1,
                "proposed_evidence_count": 0,
                "conflicting_duration_count": 0,
                "has_conflict": _truthy(row.get("has_date_conflict")),
                "expiration_date_source": row.get("expiration_date_source"),
                "match_review_required": review_required,
                "matched_vsimple_id": row.get("matched_vsimple_id"),
                "match_method": row.get("match_method"),
                "match_confidence": row.get("match_confidence"),
                "match_score": row.get("match_score"),
                "match_candidates": _json_list(row.get("match_candidates")),
                "site_address": row.get("reported_address"),
                "refreshed_at": row.get("updated_at"),
            }
        )
    return out


def _attention_items(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in rows:
        job_id = str(row.get("job_id") or "")
        if row.get("match_review_required") is True:
            items.append(
                {
                    "type": "warranty_job_match_review",
                    "severity": "review",
                    "job_id": job_id,
                    "source_record_id": row.get("warranty_summary_id"),
                    "message": "Warranty source is not confidently linked to an authoritative job record.",
                }
            )
        if row.get("has_conflict") is True:
            items.append(
                {
                    "type": "warranty_conflict",
                    "severity": "warning",
                    "job_id": job_id,
                    "message": "Warranty evidence contains conflicting durations.",
                }
            )
        if row.get("duration_years") in (None, ""):
            items.append(
                {
                    "type": "missing_warranty_duration",
                    "severity": "warning",
                    "job_id": job_id,
                    "message": "Warranty duration was not found in available evidence.",
                }
            )
        confidence = str(row.get("start_date_confidence") or "unavailable").lower()
        if confidence in {"low", "unavailable"}:
            items.append(
                {
                    "type": "uncertain_warranty_start",
                    "severity": "review",
                    "job_id": job_id,
                    "message": "Warranty start date is low confidence or unavailable.",
                }
            )
    return items


def _review_queue_summary(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    return {
        "records_needing_review": sum(_row_needs_review(row) for row in rows),
        "job_match_reviews": sum(row.get("match_review_required") is True for row in rows),
        "date_conflicts": sum(row.get("has_conflict") is True for row in rows),
        "missing_duration": sum(row.get("duration_years") in (None, "") for row in rows),
        "missing_or_uncertain_start": sum(
            str(row.get("start_date_confidence") or "unavailable").lower()
            in {"low", "unavailable"}
            for row in rows
        ),
    }


def _data_quality_tasks(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for row in rows:
        missing_fields: list[str] = []
        if row.get("duration_years") in (None, ""):
            missing_fields.append("duration_years")
        confidence = str(row.get("start_date_confidence") or "unavailable").lower()
        if confidence in {"low", "unavailable"}:
            missing_fields.append("start_date")
        if row.get("expiration_date") in (None, ""):
            missing_fields.append("expiration_date")

        task_types: list[str] = []
        if row.get("match_review_required") is True:
            task_types.append("confirm_job_match")
        if row.get("has_conflict") is True:
            task_types.append("resolve_date_or_duration_conflict")
        if missing_fields:
            task_types.append("find_missing_warranty_terms")
        if not task_types:
            continue

        candidates = _json_list(row.get("match_candidates"))[:3]
        tasks.append(
            {
                "source_record_id": row.get("warranty_summary_id"),
                "job_id": row.get("job_id"),
                "job_name": row.get("job_name"),
                "customer": row.get("customer"),
                "site_address": row.get("site_address"),
                "task_types": task_types,
                "missing_fields": missing_fields,
                "candidate_matches": candidates,
                "source_file": row.get("source_file"),
                "source_url": row.get("source_url"),
                "recommended_next_evidence": _recommended_evidence(task_types, missing_fields),
            }
        )
    return tasks


def _recommended_evidence(task_types: list[str], missing_fields: list[str]) -> list[str]:
    evidence: list[str] = []
    if "confirm_job_match" in task_types:
        evidence.extend(["Compare customer and site address to candidate jobs", "Confirm the authoritative SharePoint job folder"])
    if "duration_years" in missing_fields or "expiration_date" in missing_fields:
        evidence.extend(["Issued warranty document", "Signed proposal warranty terms"])
    if "start_date" in missing_fields:
        evidence.extend(["Warranty issue date", "Final invoice date", "Last job-tracking work date"])
    if "resolve_date_or_duration_conflict" in task_types:
        evidence.insert(0, "Issued warranty document takes precedence over legacy lists and proposals")
    return list(dict.fromkeys(evidence))


def _row_needs_review(row: Mapping[str, Any]) -> bool:
    return (
        row.get("match_review_required") is True
        or row.get("has_conflict") is True
        or row.get("duration_years") in (None, "")
        or str(row.get("start_date_confidence") or "unavailable").lower()
        in {"low", "unavailable"}
    )


def _json_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []
    return []


def _source_links(rows: Iterable[Mapping[str, Any]]) -> Iterable[dict[str, Any]]:
    for row in rows:
        url = str(row.get("source_url") or "").strip()
        if not url:
            continue
        yield {
            "source_type": "warranty_document",
            "job_id": str(row.get("job_id") or ""),
            "label": str(row.get("source_file") or "Warranty source"),
            "url": url,
            "document_id": row.get("source_document_id"),
        }


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}
