from datetime import datetime, timezone

from sqlalchemy import Column, MetaData, Table, Text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from jobscan.db_loader import (
    clean_date_value,
    ensure_primary_id,
    generate_tracking_id,
    minimal_tracking_summary_row,
    prepare_row,
    primary_key_diagnostics,
    stable_hash_id,
    stable_id,
    upsert_rows,
    upsert_update_columns,
)


def test_stable_id_is_deterministic_and_prefixed() -> None:
    first = stable_id("estimate", "job-1", "Estimate.xlsx")
    second = stable_id("estimate", "job-1", "Estimate.xlsx")

    assert first == second
    assert first.startswith("estimate-")


def test_prepare_row_generates_line_item_id_and_coerces_values() -> None:
    columns = {
        "line_item_id": "text",
        "estimate_id": "text",
        "quantity": "numeric",
        "extended_cost": "numeric",
        "source_row": "integer",
        "raw": "jsonb",
        "updated_at": "timestamp with time zone",
    }
    row = prepare_row(
        "line_items",
        {
            "job_id": "job-1",
            "estimate_file": "Estimate.xlsx",
            "source_sheet": "Estimate",
            "source_row": "42",
            "line_item_name": "Lift Rental",
            "quantity": "",
            "extended_cost": "$3,500.00",
        },
        columns,
        datetime(2026, 6, 10, tzinfo=timezone.utc),
    )

    assert row["line_item_id"].startswith("lineitem-")
    assert row["estimate_id"].startswith("estimate-")
    assert row["quantity"] is None
    assert row["extended_cost"] == 3500
    assert row["source_row"] == 42
    assert row["raw"]["extended_cost"] == "$3,500.00"


def test_ensure_primary_id_uses_job_id_for_schedule_id() -> None:
    row = ensure_primary_id("crew_schedule", {"job_id": "job-123", "ready_to_schedule": "yes"})

    assert row["schedule_id"].startswith("schedule-")


def test_stable_hash_id_uses_ordered_row_fields() -> None:
    row = {"a": " first ", "b": None, "c": "third"}

    first = stable_hash_id("thing-", row, ["a", "b", "c"])
    second = stable_hash_id("thing-", {"a": "first", "b": "", "c": "third"}, ["a", "b", "c"])
    different_order = stable_hash_id("thing-", row, ["c", "b", "a"])

    assert first == second
    assert first.startswith("thing-")
    assert len(first) == len("thing-") + 20
    assert first != different_order


def test_generated_timesheet_ids_include_employee_month_and_source_context() -> None:
    base = {
        "source_file": "Timesheet.xlsx",
        "source_sheet": "Sheet1",
        "source_row": 7,
        "employee_name": "Alex",
        "work_date": "2026-06-01",
        "project": "Shop",
        "code": "Admin",
        "duration_hours": 2,
    }

    first = ensure_primary_id(
        "office_timesheets",
        {**base, "employee_folder": "Alex", "year": 2026, "month_folder": "June"},
    )
    second = ensure_primary_id(
        "office_timesheets",
        {**base, "employee_folder": "Alex", "year": 2026, "month_folder": "July"},
    )

    assert first["entry_id"].startswith("timesheet-")
    assert first["entry_id"] != second["entry_id"]


def test_generated_line_item_ids_use_broad_line_item_fields() -> None:
    base = {
        "estimate_id": "estimate-1",
        "job_id": "job-1",
        "estimate_file": "Estimate.xlsx",
        "source_sheet": "Estimate",
        "source_row": 42,
        "section": "Materials",
        "line_item_name": "Foam Kit",
        "quantity": 10,
    }

    first = ensure_primary_id("line_items", base)
    second = ensure_primary_id("line_items", {**base, "quantity": 11})

    assert first["line_item_id"].startswith("lineitem-")
    assert first["line_item_id"] != second["line_item_id"]


def test_job_tracking_summary_and_daily_use_matching_tracking_id() -> None:
    summary = {
        "job_id": "job-1",
        "tracking_file": "Job 1/Job Tracking Form.xlsx",
        "source_file": "Job Tracking Form.xlsx",
        "source_path": "Job 1/Job Tracking Form.xlsx",
        "customer": "Customer",
        "job_name": "Job 1",
    }
    daily = {
        "job_id": "job-1",
        "tracking_file": "Job 1/Job Tracking Form.xlsx",
        "source_sheet": "Sheet1",
        "source_row": 7,
        "work_date": "2026-06-01",
    }

    summary_row = ensure_primary_id("job_tracking_summary", summary)
    daily_row = ensure_primary_id("job_tracking_daily", daily)

    assert generate_tracking_id(summary) == generate_tracking_id(daily)
    assert summary_row["tracking_id"] == daily_row["tracking_id"]


def test_minimal_tracking_summary_parent_preserves_original_daily_raw() -> None:
    table_columns = {
        "tracking_id": "text",
        "job_id": "text",
        "tracking_file": "text",
        "raw": "jsonb",
        "updated_at": "timestamp with time zone",
    }
    original = {
        "job_id": "job-1",
        "tracking_file": "Job 1/Job Tracking Form.xlsx",
        "work_date": "2026-06-01",
    }

    parent = minimal_tracking_summary_row(
        original,
        table_columns,
        datetime(2026, 6, 10, tzinfo=timezone.utc),
    )

    assert parent["tracking_id"] == generate_tracking_id(original)
    assert parent["job_id"] == "job-1"
    assert parent["tracking_file"] == "Job 1/Job Tracking Form.xlsx"
    assert parent["raw"] == original


def test_primary_key_diagnostics_reports_duplicates_after_generation() -> None:
    diagnostics = primary_key_diagnostics(
        "jobs",
        [
            {"job_id": "JOB-1", "folder_path": "A"},
            {"job_id": "JOB-1", "folder_path": "B"},
            {"job_id": "", "folder_path": "C"},
        ],
    )

    assert diagnostics["rows_read"] == 3
    assert diagnostics["primary_key_present_before_generation"] == 2
    assert diagnostics["primary_key_missing_before_generation"] == 1
    assert diagnostics["duplicate_primary_keys_after_generation"] == 1
    assert diagnostics["top_duplicate_primary_keys"] == [("JOB-1", 2)]


def test_clean_date_value_normalizes_valid_dates_and_nulls_invalid_text() -> None:
    assert clean_date_value("6/1/2026") == "2026-06-01"
    assert clean_date_value("2026-06-01") == "2026-06-01"
    assert clean_date_value("") is None
    assert clean_date_value("nan") is None
    assert clean_date_value("none") is None
    assert clean_date_value("n/a") is None
    assert clean_date_value("on separate Sheet") is None


def test_prepare_row_cleans_known_date_columns_and_preserves_raw_value() -> None:
    columns = {
        "entry_id": "text",
        "work_date": "date",
        "raw": "jsonb",
        "updated_at": "timestamp with time zone",
    }
    stats: dict[str, int] = {}
    row = prepare_row(
        "office_timesheets",
        {
            "entry_id": "timesheet-1",
            "employee": "Jane",
            "work_date": "on separate Sheet",
            "source_row": 12,
        },
        columns,
        datetime(2026, 6, 10, tzinfo=timezone.utc),
        stats,
    )

    assert row["work_date"] is None
    assert row["raw"]["work_date"] == "on separate Sheet"
    assert stats["invalid_date_values"] == 1


def test_prepare_row_normalizes_valid_known_date_columns() -> None:
    columns = {
        "job_id": "text",
        "estimate_date": "date",
        "invoice_date": "date",
        "estimated_start_date": "date",
        "estimated_end_date": "date",
        "actual_first_work_date": "date",
        "actual_last_work_date": "date",
        "raw": "jsonb",
    }
    stats: dict[str, int] = {}
    row = prepare_row(
        "jobs",
        {
            "job_id": "job-1",
            "estimate_date": "6/1/2026",
            "invoice_date": "2026-06-02",
            "estimated_start_date": "none",
            "estimated_end_date": "on separate Sheet",
            "actual_first_work_date": datetime(2026, 6, 3, tzinfo=timezone.utc),
            "actual_last_work_date": "6/4/2026",
        },
        columns,
        datetime(2026, 6, 10, tzinfo=timezone.utc),
        stats,
    )

    assert row["estimate_date"] == "2026-06-01"
    assert row["invoice_date"] == "2026-06-02"
    assert row["estimated_start_date"] is None
    assert row["estimated_end_date"] is None
    assert row["actual_first_work_date"] == "2026-06-03"
    assert row["actual_last_work_date"] == "2026-06-04"
    assert row["raw"]["estimated_end_date"] == "on separate Sheet"
    assert stats["invalid_date_values"] == 1


def test_prepare_job_row_populates_document_link_fields_and_count() -> None:
    columns = {
        "job_id": "text",
        "primary_doc_link": "text",
        "primary_doc_type": "text",
        "primary_doc_name": "text",
        "proposal_url": "text",
        "estimate_url": "text",
        "contract_url": "text",
        "invoice_url": "text",
        "job_tracking_url": "text",
        "warranty_url": "text",
        "aerial_url": "text",
        "document_link_count": "integer",
        "raw": "jsonb",
    }
    row = prepare_row(
        "jobs",
        {
            "job_id": "job-docs",
            "primary_doc_link": "https://sharepoint.example/primary",
            "primary_doc_type": "proposal",
            "primary_doc_name": "Proposal.pdf",
            "proposal_url": "https://sharepoint.example/proposal",
            "estimate_url": "https://sharepoint.example/estimate",
            "contract_url": "https://sharepoint.example/contract",
            "invoice_url": "https://sharepoint.example/invoice",
            "job_tracking_url": "https://sharepoint.example/tracking",
            "warranty_url": "https://sharepoint.example/warranty",
            "aerial_url": "https://sharepoint.example/aerial",
            "document_link_count": "7",
        },
        columns,
        datetime(2026, 6, 10, tzinfo=timezone.utc),
    )

    assert row["primary_doc_link"] == "https://sharepoint.example/primary"
    assert row["proposal_url"] == "https://sharepoint.example/proposal"
    assert row["estimate_url"] == "https://sharepoint.example/estimate"
    assert row["contract_url"] == "https://sharepoint.example/contract"
    assert row["invoice_url"] == "https://sharepoint.example/invoice"
    assert row["job_tracking_url"] == "https://sharepoint.example/tracking"
    assert row["warranty_url"] == "https://sharepoint.example/warranty"
    assert row["aerial_url"] == "https://sharepoint.example/aerial"
    assert row["document_link_count"] == 7
    assert row["raw"]["invoice_url"] == "https://sharepoint.example/invoice"


def test_prepare_job_row_blanks_document_urls_to_null_and_absent_fields_are_not_updated() -> None:
    columns = {
        "job_id": "text",
        "proposal_url": "text",
        "invoice_url": "text",
        "document_link_count": "integer",
    }
    row = prepare_row(
        "jobs",
        {
            "job_id": "job-docs",
            "proposal_url": "",
            "document_link_count": "",
        },
        columns,
        datetime(2026, 6, 10, tzinfo=timezone.utc),
    )

    assert row["proposal_url"] is None
    assert row["document_link_count"] is None
    assert "invoice_url" not in row


def test_upsert_update_columns_include_document_links() -> None:
    table = Table(
        "jobs",
        MetaData(),
        Column("job_id", Text, primary_key=True),
        Column("invoice_url", Text),
        Column("estimate_url", Text),
    )
    row = {
        "job_id": "job-docs",
        "invoice_url": "https://sharepoint.example/invoice",
        "estimate_url": "https://sharepoint.example/estimate",
    }
    stmt = pg_insert(table).values(row)
    update_cols = upsert_update_columns(stmt, row, "job_id")

    assert set(update_cols) == {"invoice_url", "estimate_url"}


def test_upsert_rows_batches_valid_rows_with_matching_columns() -> None:
    table = Table(
        "office_timesheet_entries",
        MetaData(),
        Column("entry_id", Text, primary_key=True),
        Column("employee", Text),
    )

    class FakeConnection:
        def __init__(self) -> None:
            self.executed = []

        def execute(self, stmt):
            self.executed.append(stmt)

    conn = FakeConnection()
    count = upsert_rows(
        conn,
        table,
        "entry_id",
        [
            {"entry_id": "timesheet-1", "employee": "Alex"},
            {"entry_id": "", "employee": "Missing"},
            {"entry_id": "timesheet-2", "employee": "Jane"},
            {"entry_id": "timesheet-2", "employee": "Jane Updated"},
        ],
    )

    assert count == 2
    assert len(conn.executed) == 1


def test_prepare_document_row_handles_missing_optional_metadata_and_url_blanks() -> None:
    columns = {
        "document_id": "text",
        "job_id": "text",
        "document_type": "text",
        "file_name": "text",
        "sharepoint_url": "text",
        "size_bytes": "bigint",
        "modified_at": "timestamp with time zone",
        "raw": "jsonb",
        "updated_at": "timestamp with time zone",
    }
    row = prepare_row(
        "documents",
        {
            "document_id": "driveitem-1",
            "job_id": "JOB",
            "document_type": "invoice",
            "file_name": "Invoice.pdf",
            "sharepoint_url": "",
            "size_bytes": "123",
            "modified_at": "2026-01-01T00:00:00Z",
        },
        columns,
        datetime(2026, 6, 10, tzinfo=timezone.utc),
    )

    assert row["sharepoint_url"] is None
    assert row["size_bytes"] == 123
    assert row["modified_at"] == "2026-01-01T00:00:00Z"
    assert row["raw"]["sharepoint_url"] == ""
