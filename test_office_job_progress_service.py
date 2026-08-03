from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine, text

from jobscan.business.office_progress_service import get_office_job_progress


def progress_engine():
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
                    milestone TEXT,
                    next_action TEXT,
                    next_action_owner TEXT,
                    next_action_due DATE,
                    notes TEXT,
                    source_file TEXT,
                    source_drive_id TEXT,
                    source_drive_item_id TEXT,
                    updated_at TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO office_timesheet_entries VALUES
                ('E1', 'Anthony', '2026-07-20', '', 'Acme Manufacturing',
                 'Estimating', 2.5, 'timed_entry', 'Finished Estimate',
                 'Send proposal', 'Anthony', '2026-07-21', 'Estimate complete',
                 'Anthony July.xlsx', 'D1', 'I1', '2026-07-20'),
                ('E2', 'Anthony', '2026-07-21', '', 'Acme Manufacturing',
                 'Proposal', 0, 'activity_only', '', 'Call customer', 'Anthony',
                 '2026-07-22', 'Proposal draft', 'Anthony July.xlsx',
                 'D1', 'I1', '2026-07-21'),
                ('E3', 'Aaron', '2026-07-29', 'JOB-2', 'Beta Plant',
                 'Job Spec', 1.5, 'timed_entry', 'Finished Job Spec', '', '',
                 NULL, 'Job spec finished', 'Aaron July.xlsx',
                 'D1', 'I2', '2026-07-29'),
                ('E4', 'Aaron', '2026-07-28', '', 'General Admin',
                 'Admin', 1, 'timed_entry', '', '', '', NULL, 'Filing',
                 'Aaron July.xlsx', 'D1', 'I2', '2026-07-28'),
                ('E5', 'Aaron', '2026-07-28', '', 'Alpha',
                 'Estimating', 1, 'timed_entry', '', '', '', NULL, 'Review',
                 'Aaron July.xlsx', 'D1', 'I2', '2026-07-28'),
                ('E6', 'Aaron', '2026-07-10', 'JOB-4', 'Closed Project',
                 'Admin', 1, 'timed_entry', '', '', '', NULL, 'Archive',
                 'Aaron July.xlsx', 'D1', 'I2', '2026-07-10')
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE dashboard_jobs (
                    job_id TEXT PRIMARY KEY,
                    customer TEXT,
                    job_name TEXT,
                    division TEXT,
                    job_type TEXT,
                    project_type TEXT,
                    pipeline_status TEXT,
                    status TEXT,
                    estimated_value NUMERIC,
                    final_price NUMERIC,
                    site_address TEXT,
                    city TEXT,
                    folder_path TEXT,
                    folder_url TEXT,
                    folder_link_or_path TEXT,
                    estimate_file TEXT,
                    proposal_file TEXT,
                    contract_file TEXT,
                    updated_at TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO dashboard_jobs VALUES
                ('JOB-1', 'Acme Manufacturing', 'Acme Roof Replacement',
                 'Roofing', 'Roofing', 'Commercial', 'Proposed', 'Open',
                 100000, 0, '1 Main St', 'Dayton', '/Acme',
                 'https://example.invalid/jobs/acme',
                 'https://example.invalid/jobs/acme', 'Acme Estimate.xlsx',
                 'Acme Proposal.pdf', '', '2026-07-29'),
                ('JOB-2', 'Beta Industries', 'Beta Plant', 'Insulation',
                 'Insulation', 'Industrial', 'Contracted', 'Open',
                 50000, 60000, '2 Main St', 'Cincinnati', '/Beta',
                 'https://example.invalid/jobs/beta',
                 'https://example.invalid/jobs/beta', '', '', '',
                 '2026-07-29'),
                ('JOB-3', 'Alpha Roofing', 'Alpha Warehouse', 'Roofing',
                 'Roofing', 'Commercial', 'Proposed', 'Open',
                 20000, 0, '3 Main St', 'Dayton', '/Alpha',
                 'https://example.invalid/jobs/alpha',
                 'https://example.invalid/jobs/alpha', '', '', '',
                 '2026-07-29'),
                ('JOB-4', 'Closed Customer', 'Closed Project', 'Roofing',
                 'Roofing', 'Commercial', 'Completed', 'Invoiced',
                 20000, 20000, '4 Main St', 'Dayton', '/Completed/Closed',
                 'https://example.invalid/jobs/closed',
                 'https://example.invalid/jobs/closed', '', '', '',
                 '2026-07-29')
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


def test_office_job_progress_preserves_authoritative_and_inferred_links() -> None:
    result = get_office_job_progress(
        engine=progress_engine(),
        lookback_days=30,
        stalled_after_days=7,
        limit=10,
        as_of_date=date(2026, 7, 30),
    )
    by_project = {row["project_label"]: row for row in result["records"]}

    assert result["schema_version"] == "spraytec.office_job_progress.v1"
    assert result["truth_class"] == "mixed"
    assert by_project["Beta Plant"]["link_status"] == "authoritative"
    assert by_project["Beta Plant"]["link_truth_class"] == "authoritative"
    assert by_project["Beta Plant"]["job_id"] == "JOB-2"
    assert by_project["Acme Manufacturing"]["link_status"] == "inferred"
    assert by_project["Acme Manufacturing"]["link_truth_class"] == "inferred"
    assert by_project["Acme Manufacturing"]["job_id"] == "JOB-1"
    assert by_project["Acme Manufacturing"]["captured_hours"] == 2.5
    assert by_project["Acme Manufacturing"]["activity_only_entries"] == 1
    assert by_project["Acme Manufacturing"]["is_stalled"] is True
    assert by_project["Acme Manufacturing"]["overdue_next_actions"] == 2
    assert result["owner_priorities"][0]["project_label"] == "Acme Manufacturing"
    assert result["owner_priorities"][0]["owner_priority_score"] > 0


def test_office_job_progress_keeps_review_and_unmatched_out_of_job_attribution() -> None:
    result = get_office_job_progress(
        engine=progress_engine(),
        lookback_days=30,
        limit=10,
        as_of_date=date(2026, 7, 30),
    )
    by_project = {row["project_label"]: row for row in result["records"]}

    assert by_project["Alpha"]["link_status"] == "review"
    assert by_project["Alpha"]["candidate_job_id"] == "JOB-3"
    assert by_project["Alpha"]["job_id"] == ""
    assert by_project["General Admin"]["link_status"] == "unmatched"
    assert by_project["General Admin"]["job_id"] == ""
    assert "Closed Project" not in by_project
    assert result["headline_metrics"]["review_project_labels"] == 1
    assert result["headline_metrics"]["unmatched_project_labels"] == 1
    assert any(
        item["type"] == "job_link_review"
        for item in result["attention_items"]
    )


def test_office_job_progress_supports_stalled_division_and_employee_filters() -> None:
    stalled = get_office_job_progress(
        engine=progress_engine(),
        division="Roofing",
        lookback_days=30,
        stalled_after_days=7,
        stalled_only=True,
        include_unmatched=False,
        limit=10,
        as_of_date=date(2026, 7, 30),
    )
    employee = get_office_job_progress(
        engine=progress_engine(),
        employee="Aaron",
        lookback_days=30,
        limit=10,
        as_of_date=date(2026, 7, 30),
    )

    assert [row["project_label"] for row in stalled["records"]] == [
        "Acme Manufacturing"
    ]
    assert stalled["filters_applied"]["division"] == "Roofing"
    assert employee["headline_metrics"]["activity_entries"] == 3

    closed = get_office_job_progress(
        engine=progress_engine(),
        lookback_days=30,
        include_closed=True,
        limit=10,
        as_of_date=date(2026, 7, 30),
    )
    closed_by_project = {row["project_label"]: row for row in closed["records"]}
    assert closed_by_project["Closed Project"]["is_terminal_job"] is True
    assert closed_by_project["Closed Project"]["is_stalled"] is False


def test_office_job_progress_explains_progress_and_link_limitations() -> None:
    result = get_office_job_progress(
        engine=progress_engine(),
        lookback_days=30,
        limit=2,
        as_of_date=date(2026, 7, 30),
    )

    assert result["coverage"]["results_truncated"] is True
    assert len(result["records"]) == 2
    assert "not percent complete" in result["methodology"]["progress_definition"]
    assert any("inferred" in warning for warning in result["warnings"])
    assert result["source_links"]
