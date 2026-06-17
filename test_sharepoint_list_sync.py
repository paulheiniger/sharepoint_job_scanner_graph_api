from __future__ import annotations

from jobscan.sharepoint_list_sync import (
    ColumnInfo,
    build_column_lookup,
    build_field_mapping,
    build_payload,
    classify_missing_columns,
    clean_value,
    default_source_fields,
    dedupe_records,
    ensure_document_links,
    normalize_column_name,
    sync_records,
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
    columns = [
        col("zip", "zip"),
        col("profit_percent", "profit_percent"),
        col("estimated_square_feet", "estimated_sqft"),
        col("price_per_square_foot", "price_per_sqft"),
    ]
    mapping, missing, skipped = build_field_mapping(columns, ["zip_code", "profit_pct", "estimated_sqft", "price_per_sqft"])
    assert mapping["zip_code"].name == "zip"
    assert mapping["profit_pct"].name == "profit_percent"
    assert mapping["estimated_sqft"].name == "estimated_square_feet"
    assert mapping["price_per_sqft"].name == "price_per_square_foot"
    assert missing == []
    assert skipped == []


def test_blank_null_cleaning() -> None:
    assert clean_value("") is None
    assert clean_value("nan") is None
    assert clean_value(" none ") is None
    assert clean_value("value") == "value"


def test_important_doc_links_json_not_default_field() -> None:
    assert "important_doc_links_json" not in default_source_fields()
    assert "important_doc_links_json" in default_source_fields(include_important_doc_links_json=True)


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


def test_url_field_written_as_text_column() -> None:
    columns = [col("folder_url", "folder_url", "text")]
    mapping, _, _ = build_field_mapping(columns, ["folder_url"])
    payload = build_payload({"folder_url": "https://example/folder"}, mapping)
    assert payload["folder_url"] == "https://example/folder"


def test_url_field_written_as_custom_or_unknown_column() -> None:
    for type_name in ("Custom Columns", "unknown"):
        columns = [col("primary_doc_link", "primary_doc_link", type_name)]
        mapping, _, _ = build_field_mapping(columns, ["primary_doc_link"])
        payload = build_payload({"primary_doc_link": "https://example/doc"}, mapping)
        assert payload["primary_doc_link"] == "https://example/doc"


class FallbackClient:
    def __init__(self) -> None:
        self.requests = []

    def request(self, method: str, url: str, **kwargs):
        self.requests.append((method, url, kwargs))
        body = kwargs.get("json") or {}
        fields = body.get("fields") if "fields" in body else body
        if isinstance(fields.get("folder_url"), dict):
            raise RuntimeError("hyperlink payload rejected")
        return object()


def test_url_field_fallback_from_hyperlink_to_text_and_cache() -> None:
    client = FallbackClient()
    columns = [col("folder_url", "folder_url", "hyperlinkOrPicture"), col("job_id", "job_id")]
    mapping, _, _ = build_field_mapping(columns, ["job_id", "folder_url"])
    stats = sync_records(
        client=client,
        site_id="site",
        list_id="list",
        records=[
            {"job_id": "one", "folder_url": "https://example/one"},
            {"job_id": "two", "folder_url": "https://example/two"},
        ],
        mapping=mapping,
        existing={},
        dry_run=False,
        create_only=False,
        update_only=False,
        continue_on_error=False,
    )
    assert stats["creates_succeeded"] == 2
    assert stats["url_hyperlink_fallbacks"] == 1
    assert isinstance(client.requests[0][2]["json"]["fields"]["folder_url"], dict)
    assert client.requests[1][2]["json"]["fields"]["folder_url"] == "https://example/one"
    assert client.requests[2][2]["json"]["fields"]["folder_url"] == "https://example/two"


def test_sync_continues_when_optional_columns_are_missing() -> None:
    critical, optional, other = classify_missing_columns(["proposal_url", "contract_url", "customer"])
    assert critical == ["customer"]
    assert optional == ["proposal_url", "contract_url"]
    assert other == []

    client = FallbackClient()
    columns = [col("job_id", "job_id"), col("customer", "customer")]
    mapping, missing, _ = build_field_mapping(columns, ["job_id", "customer", "proposal_url"])
    stats = sync_records(
        client=client,
        site_id="site",
        list_id="list",
        records=[{"job_id": "one", "customer": "Customer", "proposal_url": "https://example/proposal"}],
        mapping=mapping,
        existing={},
        dry_run=False,
        create_only=False,
        update_only=False,
        continue_on_error=False,
    )
    assert missing == ["proposal_url"]
    assert stats["creates_succeeded"] == 1


def test_oversized_important_doc_links_json_omitted_from_single_line_text() -> None:
    columns = [
        col("job_id", "job_id"),
        col("primary_doc_link", "primary_doc_link", "text"),
        col("document_link_count", "document_link_count", "number"),
        col("important_doc_links_json", "important_doc_links_json", "text"),
    ]
    mapping, missing, skipped = build_field_mapping(
        columns,
        ["job_id", "primary_doc_link", "document_link_count", "important_doc_links_json"],
    )
    omitted = []
    payload = build_payload(
        {
            "job_id": "one",
            "primary_doc_link": "https://example/doc",
            "document_link_count": 2,
            "important_doc_links_json": "x" * 300,
        },
        mapping,
        omitted_fields=omitted,
    )
    assert payload["job_id"] == "one"
    assert payload["primary_doc_link"] == "https://example/doc"
    assert payload["document_link_count"] == 2.0
    assert "important_doc_links_json" not in payload
    assert omitted[0]["field"] == "important_doc_links_json"
    assert missing == []
    assert skipped == []


def test_oversized_important_doc_links_json_omitted_without_blocking_sync() -> None:
    client = FallbackClient()
    columns = [
        col("job_id", "job_id"),
        col("primary_doc_link", "primary_doc_link", "text"),
        col("important_doc_links_json", "important_doc_links_json", "text"),
    ]
    mapping, _, _ = build_field_mapping(columns, ["job_id", "primary_doc_link", "important_doc_links_json"])
    stats = sync_records(
        client=client,
        site_id="site",
        list_id="list",
        records=[
            {
                "job_id": "one",
                "primary_doc_link": "https://example/doc",
                "important_doc_links_json": "x" * 300,
            }
        ],
        mapping=mapping,
        existing={},
        dry_run=False,
        create_only=False,
        update_only=False,
        continue_on_error=False,
    )
    sent_fields = client.requests[0][2]["json"]["fields"]
    assert stats["creates_succeeded"] == 1
    assert sent_fields["job_id"] == "one"
    assert sent_fields["primary_doc_link"] == "https://example/doc"
    assert "important_doc_links_json" not in sent_fields
    assert stats["omitted_fields"][0]["field"] == "important_doc_links_json"
