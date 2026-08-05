from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, text

from jobscan.business.sharepoint_document_service import (
    fetch_sharepoint_document,
    search_sharepoint_documents,
)
from jobscan.document_extraction import ExtractionResult


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE documents (
                    document_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    document_type TEXT,
                    file_name TEXT NOT NULL,
                    sharepoint_url TEXT,
                    folder_path TEXT,
                    relative_path TEXT,
                    mime_type TEXT,
                    file_extension TEXT,
                    size_bytes INTEGER,
                    modified_at TEXT,
                    source_year INTEGER,
                    source_division TEXT,
                    drive_id TEXT,
                    drive_item_id TEXT,
                    extraction_status TEXT,
                    extraction_method TEXT,
                    extraction_error TEXT,
                    extracted_at TEXT,
                    requires_ocr BOOLEAN DEFAULT FALSE,
                    deleted_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE document_content (
                    content_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    job_id TEXT,
                    content_type TEXT,
                    source_locator TEXT,
                    page_number INTEGER,
                    sheet_name TEXT,
                    row_number INTEGER,
                    section_name TEXT,
                    text_content TEXT NOT NULL,
                    normalized_text TEXT
                )
                """
            )
        )
    return engine


def _insert_document(engine, **overrides) -> None:
    values = {
        "document_id": "DOC-1",
        "job_id": "JOB-1",
        "document_type": "warranty",
        "file_name": "Acme Warranty.pdf",
        "sharepoint_url": "https://tenant.sharepoint.com/sites/Data/Acme-Warranty.pdf",
        "folder_path": "2026 ROOFING/COMPLETED/Acme",
        "relative_path": "2026 ROOFING/COMPLETED/Acme/Acme Warranty.pdf",
        "mime_type": "application/pdf",
        "file_extension": ".pdf",
        "size_bytes": 1200,
        "modified_at": "2026-08-04T12:00:00+00:00",
        "source_year": 2026,
        "source_division": "Roofing",
        "drive_id": "DRIVE-1",
        "drive_item_id": "ITEM-1",
        "extraction_status": "completed",
        "extraction_method": "pypdf",
        "extraction_error": None,
        "extracted_at": "2026-08-04T12:01:00+00:00",
        "requires_ocr": False,
        "deleted_at": None,
    }
    values.update(overrides)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO documents (
                    document_id, job_id, document_type, file_name,
                    sharepoint_url, folder_path, relative_path, mime_type,
                    file_extension, size_bytes, modified_at, source_year,
                    source_division, drive_id, drive_item_id, extraction_status,
                    extraction_method, extraction_error, extracted_at,
                    requires_ocr, deleted_at
                ) VALUES (
                    :document_id, :job_id, :document_type, :file_name,
                    :sharepoint_url, :folder_path, :relative_path, :mime_type,
                    :file_extension, :size_bytes, :modified_at, :source_year,
                    :source_division, :drive_id, :drive_item_id, :extraction_status,
                    :extraction_method, :extraction_error, :extracted_at,
                    :requires_ocr, :deleted_at
                )
                """
            ),
            values,
        )


def test_search_sharepoint_documents_combines_metadata_and_content_matches() -> None:
    engine = _engine()
    _insert_document(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO document_content (
                    content_id, document_id, job_id, content_type,
                    source_locator, page_number, text_content, normalized_text
                ) VALUES (
                    'CONTENT-1', 'DOC-1', 'JOB-1', 'pdf_page',
                    'page:2', 2, 'Acme issued a 20 year material warranty.',
                    'acme issued a 20 year material warranty'
                )
                """
            )
        )

    result = search_sharepoint_documents(
        database_url=None,
        query="Acme warranty",
        job_id="JOB-1",
        limit=10,
        engine=engine,
    )

    assert result["schema_version"] == "spraytec.sharepoint_document_search.v1"
    assert len(result["records"]) == 1
    assert result["records"][0]["document_id"] == "DOC-1"
    assert result["records"][0]["match_sources"] == [
        "extracted_content",
        "metadata",
    ]
    assert "20 year" in result["records"][0]["content_matches"][0]["excerpt"]


def test_fetch_sharepoint_document_prefers_persisted_extracted_content() -> None:
    engine = _engine()
    _insert_document(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO document_content (
                    content_id, document_id, job_id, content_type,
                    source_locator, page_number, text_content, normalized_text
                ) VALUES (
                    'CONTENT-1', 'DOC-1', 'JOB-1', 'pdf_page',
                    'page:1', 1, 'Issued warranty details.', 'issued warranty details'
                )
                """
            )
        )

    result = fetch_sharepoint_document(
        database_url=None,
        document_id="DOC-1",
        engine=engine,
    )

    assert result["content_source"] == "persisted_extracted_content"
    assert result["content_available"] is True
    assert "[page:1 | page 1]" in result["content"]
    assert "Issued warranty details." in result["content"]
    assert result["warnings"] == []


def test_fetch_sharepoint_document_uses_stored_graph_ids_when_text_is_missing() -> None:
    engine = _engine()
    _insert_document(
        engine,
        document_id="DOC-2",
        file_name="Field Notes.txt",
        file_extension=".txt",
        mime_type="text/plain",
        extraction_status="not_started",
        extraction_method=None,
        extracted_at=None,
        drive_item_id="ITEM-2",
    )

    class FakeGraphClient:
        def download_item(self, drive_id: str, item_id: str, destination: Path) -> None:
            assert (drive_id, item_id) == ("DRIVE-1", "ITEM-2")
            destination.write_text("Downloaded field note content.", encoding="utf-8")

    result = fetch_sharepoint_document(
        database_url=None,
        document_id="DOC-2",
        engine=engine,
        graph_client_factory=FakeGraphClient,
    )

    assert result["content_source"] == "live_graph_download"
    assert result["extraction_method"] == "plain-text"
    assert result["content"] == "[file]\nDownloaded field note content."
    assert result["warnings"] == []


def test_fetch_sharepoint_document_labels_empty_live_ocr_result_as_graph_source() -> None:
    engine = _engine()
    _insert_document(
        engine,
        document_id="DOC-3",
        extraction_status="not_started",
        extraction_method=None,
        extracted_at=None,
        drive_item_id="ITEM-3",
    )

    class FakeGraphClient:
        def download_item(self, _drive_id: str, _item_id: str, destination: Path) -> None:
            destination.write_bytes(b"%PDF-1.4")

    result = fetch_sharepoint_document(
        database_url=None,
        document_id="DOC-3",
        engine=engine,
        graph_client_factory=FakeGraphClient,
        extractor=lambda _path, _document: ExtractionResult(
            rows=[],
            extraction_method="pypdf",
            requires_ocr=True,
        ),
    )

    assert result["content_source"] == "live_graph_download"
    assert result["content_available"] is False
    assert result["requires_ocr"] is True
    assert any("OCR" in warning for warning in result["warnings"])
