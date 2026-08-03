from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine, text

from jobscan.business.office_service import get_office_activity


def office_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE office_timesheet_entries (
                    entry_id TEXT PRIMARY KEY,
                    employee TEXT,
                    work_date DATE,
                    job_id TEXT,
                    project_name TEXT,
                    code TEXT,
                    duration_hours NUMERIC,
                    row_type TEXT,
                    start_time TEXT,
                    end_time TEXT,
                    milestone TEXT,
                    next_action TEXT,
                    next_action_owner TEXT,
                    next_action_due DATE,
                    notes TEXT,
                    source_file TEXT,
                    source_file_path TEXT,
                    source_app TEXT,
                    source_drive_id TEXT,
                    source_drive_item_id TEXT,
                    warnings TEXT,
                    updated_at TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO office_timesheet_entries VALUES
                ('E1', 'Anthony', '2026-07-28', '', 'Acme Roof', 'Estimating',
                 1.5, 'timed_entry', '', '', '', '', '', NULL, 'Estimate review',
                 'Anthony July.xlsx', '', '', 'D1', 'I1', '', '2026-07-30'),
                ('E2', 'Anthony', '2026-07-28', '', 'Acme Roof', 'Sales Call',
                 0, 'activity_only', '', '', '', '', '', NULL, 'Called customer',
                 'Anthony July.xlsx', '', '', 'D1', 'I1', '', '2026-07-30'),
                ('E3', 'Aaron', '2026-07-29', 'JOB-2', 'Beta Plant', 'Estimating',
                 2, 'timed_entry', '', '', '', '', '', NULL, 'Proposal revision',
                 'Aaron July.xlsx', '', '', 'D1', 'I2', 'Review note',
                 '2026-07-30'),
                ('E4', 'Aaron', '2026-06-01', '', 'Old Project', 'Admin',
                 3, 'timed_entry', '', '', '', '', '', NULL, 'Archive',
                 'Aaron June.xlsx', '', '', 'D1', 'I3', '', '2026-06-02'),
                ('E5', '', '0190-01-01', '', '', '', 0, 'activity_only',
                 '', '', '', '', '', NULL, '', 'Bad Date.xlsx', '', '',
                 'D1', 'I4', '', '2026-07-30')
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE sharepoint_drive_items (
                    drive_id TEXT,
                    drive_item_id TEXT,
                    web_url TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO sharepoint_drive_items VALUES
                ('D1', 'I1', 'https://example.invalid/timesheets/anthony'),
                ('D1', 'I2', 'https://example.invalid/timesheets/aaron')
                """
            )
        )
    return engine


def test_office_activity_returns_complete_rollups_and_bounded_records() -> None:
    result = get_office_activity(
        engine=office_engine(),
        start_date=date(2026, 7, 28),
        end_date=date(2026, 7, 29),
        limit=2,
    )

    assert result["schema_version"] == "spraytec.office_activity.v1"
    assert result["headline_metrics"]["activity_entries"] == 3
    assert result["headline_metrics"]["total_hours"] == 3.5
    assert result["headline_metrics"]["timed_entries"] == 2
    assert result["headline_metrics"]["activity_only_entries"] == 1
    assert result["headline_metrics"]["direct_job_id_entries"] == 1
    assert len(result["records"]) == 2
    assert result["coverage"]["results_truncated"] is True
    assert result["coverage"]["invalid_or_missing_work_dates_in_source"] == 1
    assert result["source_links"]
    assert any(
        row["employee"] == "Anthony" and row["touch_count"] == 2
        for row in result["employee_rollup"]
    )


def test_office_activity_supports_employee_code_project_and_timed_filters() -> None:
    result = get_office_activity(
        engine=office_engine(),
        employee="Anthony",
        project_query="Acme",
        timed_only=True,
        start_date=date(2026, 7, 28),
        end_date=date(2026, 7, 29),
        limit=10,
    )
    estimating = get_office_activity(
        engine=office_engine(),
        code="Estimating",
        start_date=date(2026, 7, 28),
        end_date=date(2026, 7, 29),
        limit=10,
    )

    assert result["headline_metrics"]["activity_entries"] == 1
    assert result["headline_metrics"]["total_hours"] == 1.5
    assert result["records"][0]["entry_id"] == "E1"
    assert estimating["headline_metrics"]["activity_entries"] == 2
    assert estimating["headline_metrics"]["total_hours"] == 3.5


def test_office_activity_explains_touch_and_job_link_coverage() -> None:
    result = get_office_activity(
        engine=office_engine(),
        employee="Anthony",
        start_date=date(2026, 7, 28),
        end_date=date(2026, 7, 29),
    )

    assert result["coverage"]["direct_job_id_coverage_pct"] == 0
    assert any("touches, not worked hours" in warning for warning in result["warnings"])
    assert any("authoritative job_id" in warning for warning in result["warnings"])


def test_office_activity_rejects_invalid_or_excessive_windows() -> None:
    with pytest.raises(ValueError):
        get_office_activity(
            engine=office_engine(),
            start_date=date(2026, 7, 29),
            end_date=date(2026, 7, 28),
        )
    with pytest.raises(ValueError):
        get_office_activity(
            engine=office_engine(),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 7, 29),
        )
