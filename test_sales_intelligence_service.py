from __future__ import annotations

from sqlalchemy import create_engine, text

from jobscan.business.sales_service import (
    get_sales_followups,
    get_sales_pipeline,
)


def sales_engine():
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
                    estimated_value NUMERIC,
                    estimated_sqft NUMERIC,
                    price_per_sqft NUMERIC,
                    has_proposal BOOLEAN,
                    has_signed_contract BOOLEAN,
                    has_warnings BOOLEAN,
                    warnings TEXT,
                    folder_url TEXT,
                    updated_at TIMESTAMP,
                    refreshed_at TIMESTAMP,
                    proposal_file_modified_at TIMESTAMP,
                    proposal_file_modified_by TEXT,
                    estimate_file_modified_at TIMESTAMP,
                    estimate_file_modified_by TEXT,
                    vsimple_deal_owner TEXT,
                    vsimple_estimator TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO job_board_static_snapshot (
                    job_id, division, pipeline_status, status, customer, job_name,
                    estimated_value, estimated_sqft, price_per_sqft, has_proposal,
                    has_signed_contract, has_warnings, warnings, folder_url,
                    updated_at, refreshed_at
                ) VALUES
                ('JOB-P1', 'Roofing', 'Proposed', 'Open', 'Acme', 'Acme Roof',
                 90000, 10000, 9, 1, 0, 0, '',
                 'https://example.invalid/JOB-P1', '2026-07-30', '2026-07-30'),
                ('JOB-P2', 'Insulation', 'Proposed', 'Open', 'Beta', 'Beta Plant',
                 30000, NULL, NULL, 1, 0, 1, 'Missing takeoff',
                 'https://example.invalid/JOB-P2', '2026-07-29', '2026-07-30'),
                ('JOB-C1', 'Roofing', 'Contracted', 'Active', 'Cedar', 'Cedar Hall',
                 150000, 20000, 7.5, 1, 1, 0, '',
                 'https://example.invalid/JOB-C1', '2026-07-28', '2026-07-30'),
                ('JOB-D1', 'Roofing', 'Completed', 'Complete', 'Delta', 'Delta Shop',
                 50000, 5000, 10, 1, 1, 0, '',
                 'https://example.invalid/JOB-D1', '2026-07-27', '2026-07-30')
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE job_board_static_snapshot
                SET proposal_file_modified_at = '2026-07-31',
                    proposal_file_modified_by = 'Alex',
                    estimate_file_modified_at = '2026-07-30',
                    estimate_file_modified_by = 'Taylor',
                    vsimple_deal_owner = 'Legacy Owner'
                WHERE job_id = 'JOB-P2'
                """
            )
        )
        connection.execute(text("ALTER TABLE job_board_static_snapshot ADD COLUMN source_year INTEGER"))
        connection.execute(
            text(
                "UPDATE job_board_static_snapshot SET source_year = "
                "CASE WHEN job_id = 'JOB-P2' THEN 2025 ELSE 2026 END"
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
                ('JOB-P1', 'Follow-Up / Negotiation', 'Pat', '', '2026-07-01',
                 'High', 'Call decision maker', '2026-07-30'),
                ('JOB-C1', 'Ready to schedule', 'Jordan', '', NULL,
                 'Normal', '', '2026-07-29')
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE dashboard_sales_followup (
                    job_id TEXT,
                    division TEXT,
                    pipeline_status TEXT,
                    status TEXT,
                    customer TEXT,
                    job_name TEXT,
                    estimated_value NUMERIC,
                    estimated_sqft NUMERIC,
                    price_per_sqft NUMERIC,
                    has_warnings BOOLEAN,
                    warnings TEXT,
                    folder_url TEXT,
                    followup_status TEXT,
                    updated_at TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO dashboard_sales_followup VALUES
                ('JOB-P1', 'Roofing', 'Proposed', 'Open', 'Acme', 'Acme Roof',
                 90000, 10000, 9, 0, '', 'https://example.invalid/JOB-P1',
                 'Ready for follow-up', '2026-07-30'),
                ('JOB-P2', 'Insulation', 'Proposed', 'Open', 'Beta', 'Beta Plant',
                 30000, NULL, NULL, 1, 'Missing takeoff',
                 'https://example.invalid/JOB-P2',
                 'Missing square footage', '2026-07-29')
                """
            )
        )
    return engine


def test_sales_pipeline_returns_rollups_and_top_opportunities() -> None:
    result = get_sales_pipeline(engine=sales_engine(), limit=3)

    assert result["schema_version"] == "spraytec.sales_pipeline.v1"
    assert result["headline_metrics"]["job_count"] == 3
    assert result["headline_metrics"]["pipeline_value"] == 270000
    assert result["headline_metrics"]["proposed_jobs"] == 2
    assert result["headline_metrics"]["contracted_jobs"] == 1
    assert result["headline_metrics"]["inferred_owner_jobs"] == 1
    assert result["headline_metrics"]["unassigned_jobs"] == 0
    assert result["records"][0]["job_id"] == "JOB-C1"
    assert result["records"][1]["deal_owner"] == "Pat"
    inferred = next(row for row in result["records"] if row["job_id"] == "JOB-P2")
    assert inferred["owner"] == "Alex"
    assert inferred["owner_source"] == "proposal_file_modified_by"
    assert next(
        row for row in result["owner_rollup"] if row["owner"] == "Alex"
    )["inferred_job_count"] == 1
    assert result["coverage"]["results_truncated"] is False
    assert result["source_links"]


def test_sales_pipeline_can_include_completed() -> None:
    result = get_sales_pipeline(
        engine=sales_engine(),
        include_completed=True,
        limit=10,
    )

    assert result["headline_metrics"]["job_count"] == 4
    assert any(
        row["pipeline_status"] == "Completed" for row in result["stage_rollup"]
    )


def test_sales_pipeline_and_followups_filter_by_source_job_year() -> None:
    pipeline = get_sales_pipeline(engine=sales_engine(), job_year=2026, limit=10)
    followups = get_sales_followups(engine=sales_engine(), job_year=2026, limit=10)

    assert pipeline["filters_applied"]["job_year"] == 2026
    assert {row["job_id"] for row in pipeline["records"]} == {"JOB-P1", "JOB-C1"}
    assert [row["job_id"] for row in followups["records"]] == ["JOB-P1"]
    assert all(row["source_year"] == 2026 for row in pipeline["records"])


def test_sales_followups_prioritize_overdue_and_explain_missing_data() -> None:
    result = get_sales_followups(engine=sales_engine(), limit=10)

    assert result["schema_version"] == "spraytec.sales_followups.v1"
    assert result["headline_metrics"]["matching_followups"] == 2
    assert result["headline_metrics"]["overdue_followups"] == 1
    assert result["headline_metrics"]["unassigned_followups"] == 0
    assert result["headline_metrics"]["inferred_owner_followups"] == 1
    assert result["records"][0]["job_id"] == "JOB-P1"
    assert result["records"][0]["follow_up_state"] == "overdue"
    assert result["records"][1]["followup_status"] == "Missing square footage"
    assert any(
        item["type"] == "followup_data_quality"
        for item in result["attention_items"]
    )


def test_sales_followups_support_owner_and_queue_filters() -> None:
    owned = get_sales_followups(
        engine=sales_engine(),
        owner="Pat",
        overdue_only=True,
        limit=10,
    )
    unassigned = get_sales_followups(
        engine=sales_engine(),
        unassigned_only=True,
        limit=10,
    )

    assert [row["job_id"] for row in owned["records"]] == ["JOB-P1"]
    assert unassigned["records"] == []


def test_sales_owner_prefers_current_sharepoint_activity_over_vsimple_export() -> None:
    result = get_sales_pipeline(engine=sales_engine(), limit=10)
    row = next(record for record in result["records"] if record["job_id"] == "JOB-P2")

    assert row["owner"] == "Alex"
    assert row["owner_source"] == "proposal_file_modified_by"


def test_sales_owner_uses_latest_person_editor_and_ignores_generic_accounts() -> None:
    engine = sales_engine()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE job_board_static_snapshot
                SET proposal_file_modified_at = '2026-07-31',
                    proposal_file_modified_by = 'Estimating',
                    estimate_file_modified_at = '2026-08-01',
                    estimate_file_modified_by = 'Morgan',
                    vsimple_deal_owner = 'Legacy Owner'
                WHERE job_id = 'JOB-P2'
                """
            )
        )

    result = get_sales_pipeline(engine=engine, limit=10)
    row = next(record for record in result["records"] if record["job_id"] == "JOB-P2")

    assert row["owner"] == "Morgan"
    assert row["owner_source"] == "estimate_file_modified_by"
