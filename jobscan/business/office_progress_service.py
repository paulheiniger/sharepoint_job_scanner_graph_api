from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping

from sqlalchemy import text
from sqlalchemy.engine import Engine

from jobscan.business.job_service import (
    MAX_JOB_SEARCH_RESULTS,
    JobIntelligenceUnavailableError,
    _available_fields,
    _columns,
    _dedupe_links,
    _job_relation,
    _query_rows,
    _resolve_engine,
    _utc_now,
)


OFFICE_RELATION = "office_timesheet_entries"
MAX_LOOKBACK_DAYS = 365
MIN_INFERRED_MATCH_SCORE = 75.0
MIN_REVIEW_MATCH_SCORE = 58.0

OFFICE_FIELDS = (
    "entry_id",
    "employee",
    "work_date",
    "job_id",
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
    "source_drive_id",
    "source_drive_item_id",
    "updated_at",
)

JOB_FIELDS = (
    "job_id",
    "customer",
    "job_name",
    "division",
    "job_type",
    "project_type",
    "pipeline_status",
    "status",
    "estimated_value",
    "final_price",
    "site_address",
    "city",
    "folder_path",
    "folder_url",
    "folder_link_or_path",
    "estimate_file",
    "proposal_file",
    "contract_file",
    "updated_at",
)

STOP_WORDS = {
    "the",
    "and",
    "inc",
    "llc",
    "co",
    "company",
    "corp",
    "corporation",
    "roof",
    "roofing",
    "project",
    "job",
    "estimate",
    "proposal",
    "section",
    "sections",
    "building",
    "bldg",
}


def get_office_job_progress(
    *,
    database_url: str | None = None,
    engine: Engine | None = None,
    division: str = "",
    employee: str = "",
    project_query: str = "",
    lookback_days: int = 90,
    stalled_after_days: int = 7,
    stalled_only: bool = False,
    include_unmatched: bool = True,
    include_closed: bool = False,
    limit: int = 10,
    as_of_date: date | None = None,
) -> dict[str, Any]:
    """Summarize office activity by project and qualified job-link inference.

    A stable timesheet job_id is authoritative. Text matches are explicitly
    inferred and review candidates never become job attribution.
    """

    resolved_engine, owns_engine = _resolve_engine(database_url, engine)
    try:
        office_columns = _columns(resolved_engine, OFFICE_RELATION)
        if "entry_id" not in office_columns or "work_date" not in office_columns:
            raise JobIntelligenceUnavailableError(
                "The office timesheet source is unavailable."
            )
        resolved_lookback = max(7, min(int(lookback_days), MAX_LOOKBACK_DAYS))
        resolved_stalled = max(1, min(int(stalled_after_days), 90))
        resolved_as_of = as_of_date or date.today()
        start_date = resolved_as_of - timedelta(days=resolved_lookback - 1)
        applied_limit = max(1, min(int(limit), MAX_JOB_SEARCH_RESULTS))

        office_rows = _office_rows(
            resolved_engine,
            office_columns,
            start_date=start_date,
            end_date=resolved_as_of,
            employee=employee,
            project_query=project_query,
        )
        job_rows = _job_rows(resolved_engine)
        job_lookup = {
            str(row.get("job_id") or "").strip(): row
            for row in job_rows
            if str(row.get("job_id") or "").strip()
        }
        candidates, token_index = _job_candidates(job_rows)
        projects = [
            _project_record(
                project_name,
                rows,
                job_lookup=job_lookup,
                candidates=candidates,
                token_index=token_index,
                as_of_date=resolved_as_of,
                stalled_after_days=resolved_stalled,
            )
            for project_name, rows in _group_projects(office_rows).items()
        ]

        normalized_division = division.strip().lower()
        if normalized_division:
            projects = [
                row
                for row in projects
                if str(row.get("division") or "").strip().lower()
                == normalized_division
            ]
        terminal_linked_projects = sum(
            row["is_terminal_job"] for row in projects
        )
        if not include_closed:
            projects = [row for row in projects if not row["is_terminal_job"]]
        all_filtered = [
            row
            for row in projects
            if (include_unmatched or row["link_status"] not in {"unmatched", "review"})
        ]
        if stalled_only:
            all_filtered = [row for row in all_filtered if row["is_stalled"]]
        all_filtered.sort(key=_progress_sort_key)
        records = all_filtered[:applied_limit]
        owner_priorities = [
            _owner_priority_summary(row)
            for row in sorted(
                all_filtered,
                key=lambda row: (
                    -float(row.get("owner_priority_score") or 0),
                    str(row.get("project_label") or ""),
                ),
            )[:5]
            if float(row.get("owner_priority_score") or 0) > 0
        ]
        source_links = _source_links(records)
        latest_work_date = _latest_date(
            row.get("last_activity_date") for row in projects
        )
        latest_updated_at = _latest_value(
            row.get("updated_at") for row in office_rows
        )
        headline = _headline_metrics(all_filtered)
        attention_items = _attention_items(all_filtered)[:25]
        return {
            "schema_version": "spraytec.office_job_progress.v1",
            "as_of": latest_updated_at or _utc_now(),
            "truth_class": "mixed",
            "methodology": {
                "progress_definition": (
                    "Recent office activity, captured hours, milestones, and "
                    "next actions; not percent complete."
                ),
                "authoritative_link": (
                    "A stable job_id stored on an office timesheet entry."
                ),
                "inferred_link": (
                    f"Project-label similarity score of at least "
                    f"{MIN_INFERRED_MATCH_SCORE:.0f}; requires business review."
                ),
                "review_candidate": (
                    f"Similarity score from {MIN_REVIEW_MATCH_SCORE:.0f} to "
                    f"{MIN_INFERRED_MATCH_SCORE - 0.1:.1f}; not attributed to a job."
                ),
                "stalled_definition": (
                    f"No office activity for more than {resolved_stalled} days "
                    "on an authoritative or strong inferred active-job link."
                ),
            },
            "filters_applied": {
                "division": division.strip() or None,
                "employee": employee.strip() or None,
                "project_query": project_query.strip() or None,
                "lookback_days": resolved_lookback,
                "start_date": start_date.isoformat(),
                "end_date": resolved_as_of.isoformat(),
                "stalled_after_days": resolved_stalled,
                "stalled_only": stalled_only,
                "include_unmatched": include_unmatched,
                "include_closed": include_closed,
                "limit": applied_limit,
            },
            "headline_metrics": headline,
            "link_status_rollup": _status_rollup(all_filtered),
            "records": records,
            "owner_priorities": owner_priorities,
            "attention_items": attention_items,
            "source_links": source_links,
            "source_tables": [
                OFFICE_RELATION,
                _job_relation(resolved_engine)[0],
                *(
                    ["sharepoint_drive_items"]
                    if _columns(resolved_engine, "sharepoint_drive_items")
                    else []
                ),
            ],
            "data_freshness": {
                "latest_matching_work_date": latest_work_date,
                "office_rows_updated_at": latest_updated_at,
            },
            "coverage": {
                "matching_activity_entries": len(office_rows),
                "project_labels_evaluated": len(projects),
                "job_candidates_evaluated": len(candidates),
                "returned_records": len(records),
                "result_limit": applied_limit,
                "results_truncated": len(all_filtered) > applied_limit,
                "direct_job_id_project_labels": sum(
                    row["link_status"] == "authoritative" for row in projects
                ),
                "inferred_project_labels": sum(
                    row["link_status"] == "inferred" for row in projects
                ),
                "review_project_labels": sum(
                    row["link_status"] == "review" for row in projects
                ),
                "unmatched_project_labels": sum(
                    row["link_status"] == "unmatched" for row in projects
                ),
                "terminal_linked_project_labels_excluded": (
                    terminal_linked_projects if not include_closed else 0
                ),
            },
            "warnings": _warnings(projects),
            "response_budget": {
                "max_records": MAX_JOB_SEARCH_RESULTS,
                "max_lookback_days": MAX_LOOKBACK_DAYS,
                "returned_records": len(records),
            },
        }
    finally:
        if owns_engine:
            resolved_engine.dispose()


def _office_rows(
    engine: Engine,
    columns: set[str],
    *,
    start_date: date,
    end_date: date,
    employee: str,
    project_query: str,
) -> list[dict[str, Any]]:
    selected = _available_fields(columns, OFFICE_FIELDS)
    conditions = ["work_date >= :start_date", "work_date <= :end_date"]
    params: dict[str, Any] = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }
    if employee.strip() and "employee" in columns:
        conditions.append("LOWER(TRIM(COALESCE(employee, ''))) = :employee")
        params["employee"] = employee.strip().lower()
    if project_query.strip() and "project_name" in columns:
        conditions.append("LOWER(COALESCE(project_name, '')) LIKE :project_query")
        params["project_query"] = f"%{project_query.strip().lower()}%"
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
    return _query_rows(
        engine,
        text(
            f"SELECT {', '.join(f't.{field}' for field in selected)}, "
            f"{source_url_expr} AS source_file_url "
            f"FROM {OFFICE_RELATION} t WHERE {' AND '.join(conditions)}"
        ),
        params,
    )


def _job_rows(engine: Engine) -> list[dict[str, Any]]:
    relation, columns = _job_relation(engine)
    selected = _available_fields(columns, JOB_FIELDS)
    return _query_rows(
        engine,
        text(f"SELECT {', '.join(selected)} FROM {relation}"),
    )


def _group_projects(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for original in rows:
        row = dict(original)
        project_name = str(row.get("project_name") or "").strip() or "(blank)"
        grouped[project_name].append(row)
    return dict(grouped)


def _job_candidates(
    jobs: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, list[int]]]:
    candidates: list[dict[str, Any]] = []
    token_index: dict[str, list[int]] = defaultdict(list)
    for job in jobs:
        text_parts = [
            job.get(field)
            for field in (
                "customer",
                "job_name",
                "job_id",
                "site_address",
                "city",
                "folder_path",
                "folder_link_or_path",
                "estimate_file",
                "proposal_file",
                "contract_file",
            )
        ]
        match_text = _match_text(" ".join(str(value or "") for value in text_parts))
        tokens = set(match_text.split())
        if not tokens:
            continue
        candidate = {**dict(job), "match_text": match_text, "match_tokens": tokens}
        candidate_index = len(candidates)
        candidates.append(candidate)
        for token in tokens:
            token_index[token].append(candidate_index)
    return candidates, dict(token_index)


def _project_record(
    project_name: str,
    rows: list[dict[str, Any]],
    *,
    job_lookup: Mapping[str, Mapping[str, Any]],
    candidates: list[dict[str, Any]],
    token_index: Mapping[str, list[int]],
    as_of_date: date,
    stalled_after_days: int,
) -> dict[str, Any]:
    direct_ids = sorted(
        {
            str(row.get("job_id") or "").strip()
            for row in rows
            if str(row.get("job_id") or "").strip()
        }
    )
    matched_job: Mapping[str, Any] = {}
    candidate_job: Mapping[str, Any] = {}
    score = 0.0
    reason = ""
    link_status = "unmatched"
    link_truth_class = "inferred"
    if len(direct_ids) == 1:
        matched_job = job_lookup.get(direct_ids[0], {"job_id": direct_ids[0]})
        score = 100.0
        reason = "Stable job_id stored on the source timesheet."
        link_status = "authoritative"
        link_truth_class = "authoritative"
    elif len(direct_ids) > 1:
        reason = "Project label contains conflicting direct job IDs."
        link_status = "review"
    else:
        candidate_job, score, reason = _best_candidate(
            project_name,
            candidates,
            token_index,
        )
        if score >= MIN_INFERRED_MATCH_SCORE:
            matched_job = candidate_job
            link_status = "inferred"
        elif score >= MIN_REVIEW_MATCH_SCORE:
            link_status = "review"

    work_dates = [
        parsed for row in rows if (parsed := _as_date(row.get("work_date")))
    ]
    first_activity = min(work_dates) if work_dates else None
    last_activity = max(work_dates) if work_dates else None
    days_since = (
        (as_of_date - last_activity).days if last_activity is not None else None
    )
    is_terminal_job = _is_terminal_job(matched_job)
    is_stalled = bool(
        link_status in {"authoritative", "inferred"}
        and not is_terminal_job
        and days_since is not None
        and days_since > stalled_after_days
    )
    total_hours = round(
        sum(_number(row.get("duration_hours")) for row in rows),
        2,
    )
    employees = _unique_text(row.get("employee") for row in rows)
    codes = _unique_text(row.get("code") for row in rows)
    milestones = _recent_unique(rows, "milestone")
    next_actions = _recent_unique(rows, "next_action")
    latest_notes = _recent_unique(rows, "notes", limit=3)
    overdue_actions = sum(
        bool(
            str(row.get("next_action") or "").strip()
            and (due := _as_date(row.get("next_action_due")))
            and due < as_of_date
        )
        for row in rows
    )
    result = {
        "project_label": project_name,
        "job_id": str(matched_job.get("job_id") or ""),
        "candidate_job_id": (
            str(candidate_job.get("job_id") or "")
            if link_status == "review"
            else ""
        ),
        "customer": str(matched_job.get("customer") or ""),
        "job_name": str(matched_job.get("job_name") or ""),
        "division": str(matched_job.get("division") or ""),
        "pipeline_status": str(matched_job.get("pipeline_status") or ""),
        "status": str(matched_job.get("status") or ""),
        "link_status": link_status,
        "link_truth_class": link_truth_class,
        "match_score": round(score, 1),
        "match_reason": reason,
        "needs_link_review": link_status in {"review", "unmatched"},
        "activity_entries": len(rows),
        "captured_hours": total_hours,
        "activity_only_entries": sum(
            str(row.get("row_type") or "") == "activity_only" for row in rows
        ),
        "first_activity_date": first_activity.isoformat() if first_activity else None,
        "last_activity_date": last_activity.isoformat() if last_activity else None,
        "days_since_last_activity": days_since,
        "is_stalled": is_stalled,
        "is_terminal_job": is_terminal_job,
        "employees": employees,
        "work_codes": codes,
        "milestones": milestones,
        "next_actions": next_actions,
        "overdue_next_actions": overdue_actions,
        "latest_notes": latest_notes,
        "job_value": _job_value(matched_job),
        "folder_url": _job_folder_url(matched_job),
        "source_files": _unique_text(row.get("source_file") for row in rows),
        "source_urls": _unique_text(
            row.get("source_file_url") for row in rows if _is_url(row.get("source_file_url"))
        ),
    }
    priority_score, priority_reasons = _owner_priority(result)
    result["owner_priority_score"] = priority_score
    result["owner_priority_reasons"] = priority_reasons
    return result


def _owner_priority(row: Mapping[str, Any]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    overdue = int(row.get("overdue_next_actions") or 0)
    if overdue:
        score += 50 + min(overdue, 3) * 10
        reasons.append(f"{overdue} overdue next action(s)")
    if row.get("is_stalled"):
        days = int(row.get("days_since_last_activity") or 0)
        score += 30 + min(days, 60) / 3
        reasons.append(f"no captured office activity for {days} days")
    job_value = float(row.get("job_value") or 0)
    if job_value > 0:
        score += min(15.0, job_value / 100_000)
        reasons.append(f"linked job value ${job_value:,.0f}")
    if row.get("link_status") == "authoritative":
        score += 5
    return round(score, 1), reasons


def _owner_priority_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "project_label": row.get("project_label"),
        "job_id": row.get("job_id"),
        "link_truth_class": row.get("link_truth_class"),
        "owner_priority_score": row.get("owner_priority_score"),
        "owner_priority_reasons": row.get("owner_priority_reasons"),
        "days_since_last_activity": row.get("days_since_last_activity"),
        "overdue_next_actions": row.get("overdue_next_actions"),
        "job_value": row.get("job_value"),
    }


def _best_candidate(
    project_name: str,
    candidates: list[dict[str, Any]],
    token_index: Mapping[str, list[int]],
) -> tuple[dict[str, Any], float, str]:
    project_tokens = set(_match_text(project_name).split())
    candidate_indices = sorted(
        {
            index
            for token in project_tokens
            for index in token_index.get(token, [])
        }
    )
    best: dict[str, Any] = {}
    best_score = 0.0
    second_score = 0.0
    best_reason = "No strong job-label overlap."
    for index in candidate_indices:
        score, reason = _score(project_name, candidates[index])
        if score > best_score:
            second_score = best_score
            best = candidates[index]
            best_score = score
            best_reason = reason
        elif score > second_score:
            second_score = score
    if (
        best_score >= MIN_INFERRED_MATCH_SCORE
        and second_score >= MIN_INFERRED_MATCH_SCORE
        and best_score - second_score <= 2.0
    ):
        return (
            best,
            MIN_INFERRED_MATCH_SCORE - 0.1,
            f"{best_reason}; multiple job candidates scored within 2 points",
        )
    return best, best_score, best_reason


def _score(project_name: str, candidate: Mapping[str, Any]) -> tuple[float, str]:
    project_text = _match_text(project_name)
    project_tokens = set(project_text.split())
    job_text = str(candidate.get("match_text") or "")
    job_tokens = set(candidate.get("match_tokens") or set())
    if not project_tokens or not job_tokens:
        return 0.0, "Blank or non-distinctive project label."
    overlap = project_tokens & job_tokens
    overlap_ratio = len(overlap) / len(project_tokens)
    reverse_ratio = len(overlap) / len(job_tokens)
    sequence_bonus = 0.0
    reasons: list[str] = []
    if project_text in job_text:
        sequence_bonus = 35.0
        reasons.append("project phrase appears in job context")
    elif job_text in project_text:
        sequence_bonus = 25.0
        reasons.append("job phrase appears in project label")
    if overlap:
        reasons.append(f"token overlap: {', '.join(sorted(overlap)[:6])}")
    score = min(100.0, sequence_bonus + overlap_ratio * 50.0 + reverse_ratio * 20.0)
    if len(project_tokens) == 1 and len(overlap) == 1:
        score = min(score, 72.0)
        reasons.append("single-token label capped below inferred threshold")
    if len(project_tokens) <= 2 and score > 88.0:
        score = 88.0
        reasons.append("short label capped")
    return round(score, 3), "; ".join(reasons) or "No strong job-label overlap."


def _match_text(value: Any) -> str:
    normalized = re.sub(r"&", " and ", str(value or "").lower())
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return " ".join(
        token
        for token in normalized.split()
        if len(token) >= 2 and token not in STOP_WORDS
    )


def _headline_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "project_labels": len(rows),
        "linked_project_labels": sum(
            row["link_status"] in {"authoritative", "inferred"} for row in rows
        ),
        "authoritative_project_labels": sum(
            row["link_status"] == "authoritative" for row in rows
        ),
        "inferred_project_labels": sum(
            row["link_status"] == "inferred" for row in rows
        ),
        "review_project_labels": sum(
            row["link_status"] == "review" for row in rows
        ),
        "unmatched_project_labels": sum(
            row["link_status"] == "unmatched" for row in rows
        ),
        "stalled_project_labels": sum(row["is_stalled"] for row in rows),
        "activity_entries": sum(row["activity_entries"] for row in rows),
        "captured_hours": round(sum(row["captured_hours"] for row in rows), 2),
        "overdue_next_actions": sum(row["overdue_next_actions"] for row in rows),
    }


def _status_rollup(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rollup: dict[str, dict[str, Any]] = {}
    for row in rows:
        status = row["link_status"]
        bucket = rollup.setdefault(
            status,
            {
                "link_status": status,
                "project_labels": 0,
                "activity_entries": 0,
                "captured_hours": 0.0,
                "stalled_project_labels": 0,
            },
        )
        bucket["project_labels"] += 1
        bucket["activity_entries"] += row["activity_entries"]
        bucket["captured_hours"] += row["captured_hours"]
        bucket["stalled_project_labels"] += int(row["is_stalled"])
    for bucket in rollup.values():
        bucket["captured_hours"] = round(bucket["captured_hours"], 2)
    order = {"authoritative": 0, "inferred": 1, "review": 2, "unmatched": 3}
    return sorted(rollup.values(), key=lambda row: order.get(row["link_status"], 99))


def _attention_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in rows:
        if row["is_stalled"]:
            items.append(
                {
                    "type": "stalled_office_activity",
                    "severity": "warning",
                    "project_label": row["project_label"],
                    "job_id": row["job_id"],
                    "link_truth_class": row["link_truth_class"],
                    "days_since_last_activity": row["days_since_last_activity"],
                    "message": (
                        f"No captured office activity for "
                        f"{row['days_since_last_activity']} days."
                    ),
                }
            )
        if row["link_status"] in {"review", "unmatched"}:
            items.append(
                {
                    "type": "job_link_review",
                    "severity": "info",
                    "project_label": row["project_label"],
                    "candidate_job_id": row["candidate_job_id"],
                    "match_score": row["match_score"],
                    "message": "Project label needs an authoritative job selection.",
                }
            )
        if row["overdue_next_actions"]:
            items.append(
                {
                    "type": "overdue_next_action",
                    "severity": "warning",
                    "project_label": row["project_label"],
                    "job_id": row["job_id"],
                    "count": row["overdue_next_actions"],
                    "message": "One or more recorded next actions are overdue.",
                }
            )
    items.sort(
        key=lambda item: (
            item["severity"] != "warning",
            -int(item.get("days_since_last_activity") or 0),
            str(item.get("project_label") or ""),
        )
    )
    return items


def _source_links(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    for row in rows:
        job_id = str(row.get("job_id") or "")
        folder_url = str(row.get("folder_url") or "")
        if _is_url(folder_url):
            links.append(
                {
                    "source_type": "job_folder",
                    "job_id": job_id,
                    "label": "Job Folder",
                    "url": folder_url,
                }
            )
        for index, url in enumerate(row.get("source_urls") or []):
            if _is_url(url):
                links.append(
                    {
                        "source_type": "office_timesheet",
                        "job_id": job_id,
                        "label": (
                            (row.get("source_files") or ["Office Timesheet"])[
                                min(index, len(row.get("source_files") or []) - 1)
                            ]
                        ),
                        "url": str(url),
                    }
                )
    return _dedupe_links(links)


def _warnings(rows: list[dict[str, Any]]) -> list[str]:
    warnings = [
        "Office progress is activity evidence, not percent complete or proof that a deliverable is finished.",
        "Text-based job links are inferred and must not be presented as authoritative.",
        "Activity-only entries are touches; captured_hours includes only recorded durations.",
    ]
    if any(row["link_status"] in {"review", "unmatched"} for row in rows):
        warnings.append(
            "Some project labels could not be safely attributed to a job and remain in the review queue."
        )
    return warnings


def _progress_sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    link_order = {"authoritative": 0, "inferred": 1, "review": 2, "unmatched": 3}
    return (
        -float(row.get("owner_priority_score") or 0),
        not bool(row.get("is_stalled")),
        link_order.get(str(row.get("link_status")), 99),
        -int(row.get("days_since_last_activity") or 0),
        str(row.get("project_label") or ""),
    )


def _recent_unique(
    rows: list[dict[str, Any]],
    field: str,
    *,
    limit: int = 5,
) -> list[str]:
    ordered = sorted(
        rows,
        key=lambda row: (
            _as_date(row.get("work_date")) or date.min,
            str(row.get("updated_at") or ""),
        ),
        reverse=True,
    )
    return _unique_text((row.get(field) for row in ordered), limit=limit)


def _unique_text(values: Iterable[Any], *, limit: int = 8) -> list[str]:
    result: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in result:
            continue
        result.append(normalized)
        if len(result) >= limit:
            break
    return result


def _job_value(job: Mapping[str, Any]) -> float:
    return round(
        _number(job.get("final_price")) or _number(job.get("estimated_value")),
        2,
    )


def _job_folder_url(job: Mapping[str, Any]) -> str:
    for field in ("folder_url", "folder_link_or_path"):
        value = str(job.get(field) or "").strip()
        if _is_url(value):
            return value
    return ""


def _is_terminal_job(job: Mapping[str, Any]) -> bool:
    status_text = " ".join(
        str(job.get(field) or "")
        for field in ("pipeline_status", "status", "folder_path")
    ).lower()
    normalized = set(re.sub(r"[^a-z0-9]+", " ", status_text).split())
    if {"did", "not", "get"}.issubset(normalized):
        return True
    return bool(
        normalized
        & {
            "completed",
            "complete",
            "invoiced",
            "invoice",
            "cancelled",
            "canceled",
            "lost",
        }
    )


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text_value = str(value or "").strip()
    if not text_value:
        return None
    try:
        return date.fromisoformat(text_value[:10])
    except ValueError:
        return None


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _is_url(value: Any) -> bool:
    return str(value or "").lower().startswith(("http://", "https://"))


def _latest_date(values: Iterable[Any]) -> str | None:
    dates = [parsed for value in values if (parsed := _as_date(value))]
    return max(dates).isoformat() if dates else None


def _latest_value(values: Iterable[Any]) -> str | None:
    normalized = [str(value) for value in values if value not in (None, "")]
    return max(normalized) if normalized else None
