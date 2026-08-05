from __future__ import annotations

from datetime import date
from pathlib import Path

from sqlalchemy import create_engine, text

from jobscan.business.warranty_service import get_warranty_list, get_warranty_summary


def warranty_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE job_warranty_summary (
                    warranty_summary_id TEXT PRIMARY KEY,
                    job_id TEXT,
                    source_year INTEGER,
                    division TEXT,
                    customer TEXT,
                    job_name TEXT,
                    warranty_status TEXT,
                    warranty_category TEXT,
                    warranty_type TEXT,
                    provider TEXT,
                    duration_years NUMERIC,
                    coverage_summary TEXT,
                    coverage_excerpt TEXT,
                    start_date DATE,
                    start_date_source TEXT,
                    start_date_confidence TEXT,
                    start_date_is_inferred BOOLEAN,
                    expiration_date DATE,
                    source_document_id TEXT,
                    source_file TEXT,
                    source_url TEXT,
                    duration_source_kind TEXT,
                    duration_source_document_id TEXT,
                    evidence_count INTEGER,
                    issued_evidence_count INTEGER,
                    reported_evidence_count INTEGER,
                    proposed_evidence_count INTEGER,
                    conflicting_duration_count INTEGER,
                    has_conflict BOOLEAN,
                    refreshed_at TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO job_warranty_summary VALUES
                ('W-1', 'JOB-1', 2026, 'Roofing', 'Acme', 'Acme Roof',
                 'issued', 'manufacturer_system', 'Gaco manufacturer/system',
                 'Gaco', 15, 'Manufacturer system coverage', 'Covers the roof system',
                 '2026-06-01', 'project_completion_date', 'high', 1,
                 '2041-06-01', 'DOC-1', 'Warranty.pdf',
                 'https://example.invalid/warranty', 'warranty_document', 'DOC-1',
                 3, 1, 0, 2, 1, 0, '2026-08-04'),
                ('W-2', 'JOB-2', 2025, 'Insulation', 'Beta', 'Beta Plant',
                 'proposed', 'unspecified', 'Estimate workbook warranty',
                 NULL, NULL, 'Warranty terms specified in estimate workbook', NULL,
                 '2025-03-10', 'proposal_modified_plus_estimated_duration', 'low', 1,
                 NULL, 'DOC-2', 'Proposal.xlsx',
                 'https://example.invalid/proposal', 'estimate_workbook', 'DOC-2',
                 1, 0, 0, 1, 0, 1, '2026-08-04')
                """
            )
        )
    return engine


def warranty_master_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE warranty_master_clean (
                    warranty_master_id TEXT PRIMARY KEY,
                    warranty_status TEXT,
                    has_issued_document_evidence BOOLEAN,
                    evidence_status TEXT,
                    job_id TEXT,
                    vsimple_id TEXT,
                    project_name TEXT,
                    customer_name TEXT,
                    division TEXT,
                    warranty_category TEXT,
                    warranty_type TEXT,
                    warranty_term TEXT,
                    provider TEXT,
                    duration_years NUMERIC,
                    start_date DATE,
                    end_date DATE,
                    contact_names TEXT,
                    contact_emails TEXT,
                    contact_phones TEXT,
                    contact_name_source TEXT,
                    contact_email_source TEXT,
                    contact_phone_source TEXT,
                    contact_source TEXT,
                    contact_source_reference TEXT,
                    contact_follow_up_ready BOOLEAN,
                    needs_review BOOLEAN,
                    job_link TEXT,
                    issued_warranty_link TEXT,
                    issued_warranty_file TEXT,
                    vsimple_url TEXT,
                    source_file TEXT,
                    source_url TEXT,
                    source_kind TEXT,
                    source_document_id TEXT,
                    has_conflict BOOLEAN,
                    match_review_required BOOLEAN,
                    refreshed_at TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO warranty_master_clean (
                    warranty_master_id, warranty_status, has_issued_document_evidence,
                    evidence_status, job_id, vsimple_id, project_name, customer_name,
                    division, warranty_category, warranty_type, warranty_term, provider,
                    duration_years, start_date, end_date, contact_names, contact_emails,
                    contact_phones, contact_name_source, contact_email_source,
                    contact_phone_source, contact_source, contact_source_reference,
                    contact_follow_up_ready, needs_review, job_link, issued_warranty_link,
                    issued_warranty_file, vsimple_url, source_file, source_url, source_kind,
                    source_document_id, has_conflict, match_review_required, refreshed_at
                ) VALUES
                ('vsimple:1', 'issued', 1, 'issued_document', 'JOB-1', '1',
                 'Acme Roof', 'Acme', 'Roofing', 'manufacturer_system',
                 'System warranty', '15-year manufacturer warranty', 'Gaco', 15,
                 '2026-06-01', '2041-06-01', 'Alex Smith', 'alex@example.com', NULL,
                 'vsimple_customer_export', 'vsimple_customer_export', NULL,
                 'vsimple_customer_export', 'https://example.invalid/vsimple',
                 1, 0, 'https://example.invalid/job', 'https://example.invalid/warranty',
                 'Warranty.pdf', 'https://example.invalid/vsimple', 'Warranty.pdf',
                 'https://example.invalid/warranty', 'warranty_document', 'DOC-1', 0, 0,
                 '2026-08-05'),
                ('vsimple:2', 'reported', 0, 'reported_source', NULL, '2',
                 'Legacy Plant', 'Legacy Co', 'Roofing', 'unspecified',
                 'Reported warranty', 'Reported warranty; term not captured', NULL, NULL,
                 NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                 0, 1, NULL, NULL, NULL,
                 'https://example.invalid/vsimple/2', 'Historical.xlsx', NULL,
                 'recent_completed_warranty_list', NULL, 0, 1, '2026-08-05')
                """
            )
        )
    return engine


def test_warranty_list_returns_contacts_links_and_review_filters() -> None:
    result = get_warranty_list(
        engine=warranty_master_engine(),
        query="Acme",
        evidence_status="issued_document",
        has_contact=True,
        needs_review=False,
        limit=25,
    )

    assert result["schema_version"] == "spraytec.warranty_list.v1"
    assert result["headline_metrics"]["warranty_records"] == 1
    assert result["headline_metrics"]["issued_document_warranties"] == 1
    assert result["records"][0]["contact_emails"] == "alex@example.com"
    assert result["records"][0]["contact_source"] == "vsimple_customer_export"
    assert result["records"][0]["contact_source_reference"].endswith("/vsimple")
    assert result["records"][0]["issued_warranty_link"].endswith("/warranty")
    assert {link["source_type"] for link in result["source_links"]} == {
        "issued_warranty_document",
        "sharepoint_job",
        "vsimple_project",
    }


def test_warranty_list_can_return_missing_contact_review_queue() -> None:
    result = get_warranty_list(
        engine=warranty_master_engine(),
        needs_review=True,
        has_contact=False,
        limit=25,
    )

    assert [row["project_name"] for row in result["records"]] == ["Legacy Plant"]
    assert result["headline_metrics"]["missing_contact"] == 1


def test_warranty_summary_filters_and_preserves_provenance() -> None:
    result = get_warranty_summary(
        engine=warranty_engine(),
        job_year=2026,
        warranty_status="issued",
        expiring_after=date(2040, 1, 1),
        expiring_before=date(2042, 1, 1),
        limit=10,
    )

    assert result["schema_version"] == "spraytec.warranty_summary.v2"
    assert result["filters_applied"]["job_year"] == 2026
    assert result["filters_applied"]["expiring_after"] == "2040-01-01"
    assert result["headline_metrics"]["issued_warranties"] == 1
    assert result["records"][0]["job_id"] == "JOB-1"
    assert result["records"][0]["start_date_source"] == "project_completion_date"
    assert result["records"][0]["start_date_is_inferred"] is True
    assert result["source_links"][0]["document_id"] == "DOC-1"
    assert any("Proposed warranty terms" in warning for warning in result["warnings"])


def test_warranty_summary_needs_review_finds_conflicts_and_uncertain_dates() -> None:
    result = get_warranty_summary(
        engine=warranty_engine(),
        needs_review=True,
        limit=10,
    )

    assert [row["job_id"] for row in result["records"]] == ["JOB-2"]
    assert {item["type"] for item in result["attention_items"]} == {
        "warranty_conflict",
        "missing_warranty_duration",
        "uncertain_warranty_start",
    }


def test_warranty_summary_includes_unmatched_reported_sources() -> None:
    engine = warranty_engine()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE warranty_source_records (
                    source_record_id TEXT PRIMARY KEY,
                    source_system TEXT,
                    source_file TEXT,
                    source_url TEXT,
                    source_locator TEXT,
                    source_year INTEGER,
                    division TEXT,
                    reported_name TEXT,
                    reported_customer TEXT,
                    reported_address TEXT,
                    warranty_category TEXT,
                    warranty_type TEXT,
                    provider TEXT,
                    duration_years NUMERIC,
                    start_date DATE,
                    expiration_date DATE,
                    expiration_date_source TEXT,
                    has_date_conflict BOOLEAN,
                    coverage_summary TEXT,
                    coverage_excerpt TEXT,
                    matched_vsimple_id TEXT,
                    matched_job_id TEXT,
                    match_method TEXT,
                    match_confidence TEXT,
                    match_score NUMERIC,
                    match_candidates TEXT,
                    match_review_required BOOLEAN,
                    extraction_confidence TEXT,
                    updated_at TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO warranty_source_records (
                    source_record_id, source_system, source_file, source_url, source_locator,
                    source_year, division, reported_name, reported_customer, reported_address,
                    warranty_category, warranty_type, provider, duration_years, start_date,
                    expiration_date, expiration_date_source, has_date_conflict, coverage_summary,
                    coverage_excerpt, matched_vsimple_id, matched_job_id, match_method,
                    match_confidence, match_score, match_candidates, match_review_required,
                    extraction_confidence, updated_at
                ) VALUES (
                    'SRC-1', 'legacy_customer_list', 'Customer list.xlsx', NULL,
                    '2023 Roofing!A3', 2023, 'Roofing', 'Legacy Roof', 'Legacy',
                    '1 Main St', 'unspecified', 'Legacy-reported warranty', NULL,
                    15, '2023-06-01', '2038-06-01', 'start_plus_reported_duration', 0,
                    'Reported master-list warranty',
                    NULL, NULL, NULL, 'ambiguous_candidate', 'low', 0.75,
                    :match_candidates, 1,
                    'medium', '2026-08-04'
                )
                """
            ),
            {
                "match_candidates": '[{"vsimple_id":"V-1","job_id":"JOB-CANDIDATE","name":"Legacy Roof","score":0.75}]'
            },
        )

    result = get_warranty_summary(
        engine=engine,
        job_year=2023,
        warranty_status="reported",
        limit=10,
    )

    assert result["headline_metrics"]["reported_warranties"] == 1
    assert result["records"][0]["job_id"] is None
    assert result["records"][0]["match_review_required"] is True
    assert result["records"][0]["match_candidates"][0]["job_id"] == "JOB-CANDIDATE"
    assert result["attention_items"][0]["type"] == "warranty_job_match_review"
    assert result["schema_version"] == "spraytec.warranty_summary.v2"
    assert result["review_queue_summary"]["job_match_reviews"] == 1
    assert result["data_quality_tasks"][0]["candidate_matches"][0]["vsimple_id"] == "V-1"


def test_warranty_refresh_preserves_status_and_date_precedence() -> None:
    sql = Path("db/refresh_job_warranty_summary.sql").read_text(encoding="utf-8")

    assert "CASE WHEN identity_text LIKE '%proposal%' THEN 'proposed' ELSE 'issued' END" in sql
    assert "BETWEEN 1 AND 30" in sql
    precedence = [
        "explicit_warranty_date",
        "project_completion_date",
        "warranty_file_modified_date",
        "invoice_date",
        "job_tracking_last_work_date",
        "scheduled_end_date",
        "proposal_modified_plus_estimated_duration",
    ]
    source_block = sql[
        sql.index("WHEN s.explicit_start_date IS NOT NULL THEN 'explicit_warranty_date'") :
        sql.index("END AS start_date_source")
    ]
    positions = [source_block.index(value) for value in precedence]
    assert positions == sorted(positions)
