from __future__ import annotations

from sqlalchemy import create_engine, text

from jobscan.business.job_service import get_job_context, search_jobs


def job_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE job_board_static_snapshot (
                    job_id TEXT PRIMARY KEY,
                    division TEXT,
                    pipeline_status TEXT,
                    status TEXT,
                    customer TEXT,
                    job_name TEXT,
                    site_address TEXT,
                    estimated_value NUMERIC,
                    folder_url TEXT,
                    proposal_url TEXT,
                    has_proposal BOOLEAN,
                    missing_signed_contract BOOLEAN,
                    warnings TEXT,
                    updated_at TIMESTAMP,
                    refreshed_at TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO job_board_static_snapshot VALUES
                ('JOB-1', 'Roofing', 'Contracted', 'Active', 'Acme',
                 'Acme Warehouse', '100 Main St', 125000,
                 'https://example.invalid/jobs/JOB-1',
                 'https://example.invalid/jobs/JOB-1/proposal',
                 1, 1, 'Review access', '2026-07-29', '2026-07-30'),
                ('JOB-2', 'Insulation', 'Estimating', 'Open', 'Beta',
                 'Beta Plant', '200 Main St', 25000,
                 'https://example.invalid/jobs/JOB-2',
                 NULL, 0, 0, '', '2026-07-28', '2026-07-30')
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE job_workflow_overrides (
                    job_id TEXT,
                    workflow_status TEXT,
                    deal_owner TEXT,
                    assigned_user TEXT,
                    follow_up_date DATE,
                    priority TEXT,
                    internal_notes TEXT,
                    updated_at TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO job_workflow_overrides VALUES
                ('JOB-1', 'Ready to schedule', 'Pat', 'Jordan',
                 '2026-08-01', 'High', 'Confirm mobilization', '2026-07-30')
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE crew_schedule (
                    schedule_id TEXT,
                    job_id TEXT,
                    assigned_crew_leader TEXT,
                    estimated_start_date DATE,
                    estimated_duration_days NUMERIC,
                    estimated_end_date DATE,
                    schedule_status TEXT,
                    ready_to_schedule BOOLEAN,
                    blocking_issue TEXT,
                    priority TEXT,
                    schedule_notes TEXT,
                    updated_at TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO crew_schedule VALUES
                ('S-1', 'JOB-1', 'Alex', '2026-08-03', 3, '2026-08-05',
                 'Scheduled', 1, '', 'High', 'Bring lift', '2026-07-30')
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE job_tracking_summary (
                    tracking_id TEXT,
                    job_id TEXT,
                    actual_last_work_date DATE,
                    actual_labor_hours NUMERIC,
                    tracking_warnings TEXT,
                    updated_at TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO job_tracking_summary VALUES
                ('T-1', 'JOB-1', '2026-07-29', 42, 'Missing lot number', '2026-07-30')
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE job_tracking_daily_entries (
                    tracking_entry_id TEXT,
                    tracking_id TEXT,
                    job_id TEXT,
                    work_date DATE,
                    labor_hours NUMERIC,
                    crew TEXT,
                    notes TEXT,
                    updated_at TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO job_tracking_daily_entries VALUES
                ('D-1', 'T-1', 'JOB-1', '2026-07-29', 14, 'Crew A',
                 'Completed north section', '2026-07-30')
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE documents (
                    document_id TEXT,
                    job_id TEXT,
                    document_type TEXT,
                    file_name TEXT,
                    sharepoint_url TEXT,
                    modified_at TIMESTAMP,
                    extraction_status TEXT,
                    updated_at TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO documents VALUES
                ('DOC-1', 'JOB-1', 'Contract', 'Signed Contract.pdf',
                 'https://example.invalid/jobs/JOB-1/contract',
                 '2026-07-28', 'complete', '2026-07-29')
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE office_timesheet_entries (
                    entry_id TEXT,
                    employee TEXT,
                    work_date DATE,
                    job_id TEXT,
                    project_name TEXT,
                    duration_hours NUMERIC,
                    notes TEXT,
                    updated_at TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO office_timesheet_entries VALUES
                ('O-1', 'Sam', '2026-07-30', NULL, 'Acme Warehouse', 1.5,
                 'Called customer', '2026-07-30')
                """
            )
        )
    return engine


def test_search_jobs_returns_bounded_action_friendly_records() -> None:
    result = search_jobs(
        engine=job_engine(),
        query="Acme",
        pipeline_status="Contracted",
        workflow_status="Ready to schedule",
        owner="Pat",
        needs_attention=True,
        limit=5,
    )

    assert result["schema_version"] == "spraytec.job_search.v1"
    assert result["headline_metrics"]["returned_records"] == 1
    assert result["records"][0]["job_id"] == "JOB-1"
    assert result["records"][0]["assigned_crew_leader"] == "Alex"
    assert result["records"][0]["attention_items"]
    assert result["source_links"][0]["source_type"] == "job_folder"
    assert result["coverage"]["results_truncated"] is False


def test_job_context_combines_authoritative_sources_and_labels_fallback() -> None:
    result = get_job_context("JOB-1", engine=job_engine())

    assert result["schema_version"] == "spraytec.job_context.v1"
    assert result["job"]["workflow_status"] == "Ready to schedule"
    assert result["schedule"]["assigned_crew_leader"] == "Alex"
    assert result["tracking_summary"][0]["actual_labor_hours"] == 42
    assert result["recent_daily_tracking"][0]["crew"] == "Crew A"
    assert result["coverage"]["daily_tracking_total_records"] == 1
    assert result["coverage"]["daily_tracking_records_truncated"] is False
    assert result["documents"][0]["document_id"] == "DOC-1"
    assert result["recent_office_activity"][0]["match_method"] == "exact_project_name"
    assert result["warnings"] == [
        "Office activity is an exact normalized project-name match, not an authoritative job_id link."
    ]
    assert any(
        item["type"] == "tracking_warning" for item in result["attention_items"]
    )
    assert any(
        link["document_id"] == "DOC-1"
        for link in result["source_links"]
        if link["source_type"] == "document"
    )
