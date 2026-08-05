from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine, text

from jobscan.business.operations_service import (
    get_operations_backlog,
    get_operations_schedule,
)


def operations_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE operations_dashboard_ops_snapshot (
                    job_id TEXT PRIMARY KEY,
                    division TEXT,
                    pipeline_status TEXT,
                    status TEXT,
                    customer TEXT,
                    job_name TEXT,
                    operations_value NUMERIC,
                    readiness_status TEXT,
                    ready_date DATE,
                    days_waiting NUMERIC,
                    has_job_spec BOOLEAN,
                    assigned_crew_leader TEXT,
                    estimated_start_date DATE,
                    estimated_end_date DATE,
                    estimated_duration_days NUMERIC,
                    estimated_labor_hours NUMERIC,
                    schedule_status TEXT,
                    schedule_health TEXT,
                    blocking_issue TEXT,
                    tracking_status TEXT,
                    project_health TEXT,
                    production_risk_summary TEXT,
                    folder_url TEXT,
                    snapshot_refreshed_at TIMESTAMP,
                    updated_at TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO operations_dashboard_ops_snapshot VALUES
                ('JOB-READY', 'Roofing', 'Contracted', 'Open', 'Acme',
                 'Acme Roof', 100000, 'Ready To Schedule', '2026-07-01', 29,
                 1, '', NULL, NULL, 3, 120, '', 'Awaiting Schedule', '',
                 '', 'Needs schedule / actuals', 'Ready for scheduling',
                 'https://example.invalid/JOB-READY', '2026-07-30', '2026-07-30'),
                ('JOB-SPEC', 'Insulation', 'Contracted', 'Open', 'Beta',
                 'Beta Plant', 200000, 'Missing Job Spec', '2026-07-02', 28,
                 0, '', NULL, NULL, 4, 160, '', 'Awaiting Schedule', '',
                 '', 'Needs schedule / actuals', 'Job spec required',
                 'https://example.invalid/JOB-SPEC', '2026-07-30', '2026-07-30'),
                ('JOB-RISK', 'Roofing', 'Contracted', 'Active', 'Cedar',
                 'Cedar Hall', 300000, 'Scheduled', '2026-06-20', 40,
                 1, '', '2026-08-01', '2026-08-05', 5, 200, 'Scheduled',
                 'Behind / Blocked', 'Waiting on material', 'Recently touched',
                 'Labor overrun risk', 'Labor hours exceed estimate',
                 'https://example.invalid/JOB-RISK', '2026-07-30', '2026-07-30'),
                ('JOB-DONE', 'Roofing', 'Contracted', 'Complete', 'Delta',
                 'Delta Shop', 400000, 'Scheduled', '2026-05-01', 90,
                 1, 'Carlos', '2026-07-01', '2026-07-03', 3, 100, 'Complete',
                 'Completed', '', 'Complete', 'Completed', 'Complete',
                 'https://example.invalid/JOB-DONE', '2026-07-30', '2026-07-30')
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE job_board_static_snapshot (
                    job_id TEXT PRIMARY KEY,
                    source_year INTEGER,
                    updated_at TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO job_board_static_snapshot VALUES
                ('JOB-READY', 2026, '2026-07-30'),
                ('JOB-SPEC', 2025, '2026-07-30'),
                ('JOB-RISK', 2026, '2026-07-30'),
                ('JOB-DONE', 2025, '2026-07-30')
                """
            )
        )
    return engine


def test_operations_backlog_returns_readiness_rollups_and_attention() -> None:
    result = get_operations_backlog(engine=operations_engine(), limit=10)

    assert result["schema_version"] == "spraytec.operations_backlog.v1"
    assert result["headline_metrics"]["backlog_jobs"] == 3
    assert result["headline_metrics"]["backlog_value"] == 600000
    assert result["headline_metrics"]["scheduled_jobs"] == 1
    assert result["headline_metrics"]["missing_job_spec_jobs"] == 1
    assert result["records"][0]["job_id"] == "JOB-RISK"
    assert any(
        item["type"] == "missing_job_spec"
        for item in result["attention_items"]
    )
    assert result["source_links"]


def test_operations_backlog_supports_unscheduled_and_status_filters() -> None:
    unscheduled = get_operations_backlog(
        engine=operations_engine(),
        unscheduled_only=True,
        limit=10,
    )
    ready = get_operations_backlog(
        engine=operations_engine(),
        readiness_statuses=["Ready To Schedule"],
        needs_attention=False,
        limit=10,
    )

    assert {row["job_id"] for row in unscheduled["records"]} == {
        "JOB-READY",
        "JOB-SPEC",
    }
    assert [row["job_id"] for row in ready["records"]] == ["JOB-READY"]


def test_operations_backlog_and_schedule_filter_by_source_job_year() -> None:
    backlog = get_operations_backlog(
        engine=operations_engine(),
        job_year=2026,
        limit=10,
    )
    schedule = get_operations_schedule(
        engine=operations_engine(),
        job_year=2026,
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 14),
        limit=10,
    )

    assert backlog["filters_applied"]["job_year"] == 2026
    assert {row["job_id"] for row in backlog["records"]} == {"JOB-READY", "JOB-RISK"}
    assert schedule["filters_applied"]["job_year"] == 2026
    assert [row["job_id"] for row in schedule["records"]] == ["JOB-RISK"]
    assert all(row["source_year"] == 2026 for row in backlog["records"])


def test_operations_schedule_returns_window_and_production_risk() -> None:
    scheduled = get_operations_schedule(
        engine=operations_engine(),
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 14),
        limit=10,
    )
    risks = get_operations_schedule(
        engine=operations_engine(),
        risk_only=True,
        limit=10,
    )

    assert scheduled["schema_version"] == "spraytec.operations_schedule.v1"
    assert [row["job_id"] for row in scheduled["records"]] == ["JOB-RISK"]
    assert scheduled["headline_metrics"]["unassigned_jobs"] == 1
    assert scheduled["headline_metrics"]["production_risk_jobs"] == 1
    assert scheduled["crew_rollup"][0]["crew_leader"] == "Unassigned"
    assert [row["job_id"] for row in risks["records"]] == ["JOB-RISK"]


def test_operations_schedule_rejects_reversed_date_window() -> None:
    with pytest.raises(ValueError):
        get_operations_schedule(
            engine=operations_engine(),
            start_date=date(2026, 8, 14),
            end_date=date(2026, 8, 1),
        )


def test_operations_schedule_preserves_freshness_for_empty_window() -> None:
    result = get_operations_schedule(
        engine=operations_engine(),
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 14),
        limit=10,
    )

    assert result["headline_metrics"]["matching_jobs"] == 0
    assert (
        result["data_freshness"]["operations_snapshot_as_of"]
        == "2026-07-30"
    )
    assert result["coverage"]["source_total_jobs"] == 3
    assert result["coverage"]["matching_window_jobs"] == 0
    assert result["coverage"]["scheduled_outside_window"] == 1
    assert (
        result["coverage"]["zero_result_reason"]
        == "no_jobs_match_requested_schedule_window"
    )
    assert result["warnings"]


def test_operations_schedule_window_includes_projects_already_in_progress() -> None:
    engine = operations_engine()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE operations_dashboard_ops_snapshot
                SET assigned_crew_leader = 'Carlos',
                    estimated_start_date = '2026-07-28',
                    estimated_end_date = '2026-08-03'
                WHERE job_id = 'JOB-RISK'
                """
            )
        )

    result = get_operations_schedule(
        engine=engine,
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 14),
        limit=10,
    )

    assert [row["job_id"] for row in result["records"]] == ["JOB-RISK"]


def test_operations_schedule_can_raise_internal_chart_record_cap() -> None:
    result = get_operations_schedule(
        engine=operations_engine(),
        include_unscheduled=True,
        limit=60,
        max_records=125,
    )

    assert result["filters_applied"]["limit"] == 60
    assert result["response_budget"]["max_records"] == 125
