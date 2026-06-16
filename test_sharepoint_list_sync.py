from __future__ import annotations

from jobscan.sharepoint_list_sync import (
    ColumnInfo,
    build_column_lookup,
    build_field_mapping,
    build_payload,
    clean_value,
    dedupe_records,
    ensure_document_links,
    normalize_column_name,
)
from jobscan.sharepoint_sync import classify_document_type, select_document_url


def col(name: str, display: str, type_name: str = "text", read_only: bool = False) -> ColumnInfo:
    return ColumnInfo(
        id=name,
        name=name,
        display_name=display,
        hidden=False,
        read_only=read_only,
        required=False,
        type_name=type_name,
        raw={"name": name, "displayName": display},
    )


def test_column_name_normalization_handles_encoded_spaces() -> None:
    assert normalize_column_name("Pipeline Status") == "pipeline_status"
    assert normalize_column_name("Pipeline_x0020_Status") == "pipeline_status"
    assert normalize_column_name("Pipeline-Status!") == "pipeline_status"


def test_display_and_internal_column_mapping() -> None:
    columns = [col("Pipeline_x0020_Status", "Pipeline Status"), col("job_id", "Job ID")]
    lookup = build_column_lookup(columns)
    assert lookup["pipeline_status"].name == "Pipeline_x0020_Status"
    mapping, missing, skipped = build_field_mapping(columns, ["pipeline_status", "job_id", "missing_field"])
    assert mapping["pipeline_status"].name == "Pipeline_x0020_Status"
    assert mapping["job_id"].name == "job_id"
    assert missing == ["missing_field"]
    assert skipped == []


def test_current_job_index_aliases() -> None:
    columns = [col("zip", "zip"), col("profit_percent", "profit_percent")]
    mapping, missing, skipped = build_field_mapping(columns, ["zip_code", "profit_pct"])
    assert mapping["zip_code"].name == "zip"
    assert mapping["profit_pct"].name == "profit_percent"
    assert missing == []
    assert skipped == []


def test_blank_null_cleaning() -> None:
    assert clean_value("") is None
    assert clean_value("nan") is None
    assert clean_value(" none ") is None
    assert clean_value("value") == "value"


def test_document_classification_and_exact_matching() -> None:
    docs = [
        {"name": "Old Estimate.xlsx", "document_type": "estimate", "web_url": "https://example/old", "modified_at": "2024"},
        {"name": "Customer Proposal.pdf", "document_type": "proposal", "web_url": "https://example/proposal", "modified_at": "2023"},
    ]
    assert classify_document_type("Signed Contract.pdf") == "contract"
    assert classify_document_type("EagleView aerial.pdf") == "aerial"
    assert select_document_url(docs, "proposal", ["Customer Proposal.pdf"])["web_url"] == "https://example/proposal"


def test_primary_doc_link_priority() -> None:
    record = {
        "folder_url": "https://example/folder",
        "estimate_url": "https://example/estimate",
        "proposal_url": "https://example/proposal",
    }
    out = ensure_document_links(record)
    assert out["primary_doc_link"] == "https://example/proposal"


def test_duplicate_job_id_resolution_prefers_complete_linked_record() -> None:
    records = [
        {"job_id": "A", "customer": "One"},
        {"job_id": "A", "customer": "One", "folder_url": "https://example/folder", "final_price": 123},
    ]
    unique, duplicates = dedupe_records(records)
    assert len(unique) == 1
    assert unique[0]["folder_url"] == "https://example/folder"
    assert duplicates["A"] == 2


def test_field_type_conversion_and_payload_generation() -> None:
    columns = [
        col("Title", "Title"),
        col("job_id", "Job ID"),
        col("FinalPrice", "final_price", "currency"),
        col("HasInvoice", "has_invoice", "boolean"),
        col("FolderUrl", "folder_url", "hyperlinkOrPicture"),
    ]
    mapping, missing, skipped = build_field_mapping(columns, ["Title", "job_id", "final_price", "has_invoice", "folder_url"])
    payload = build_payload(
        {
            "job_id": "job-1",
            "job_name": "Test Job",
            "final_price": "$1,234.50",
            "has_invoice": "yes",
            "folder_url": "https://example/folder",
        },
        mapping,
    )
    assert payload["Title"] == "Test Job"
    assert payload["FinalPrice"] == 1234.5
    assert payload["HasInvoice"] is True
    assert payload["FolderUrl"]["Url"] == "https://example/folder"
    assert missing == []
    assert skipped == []
