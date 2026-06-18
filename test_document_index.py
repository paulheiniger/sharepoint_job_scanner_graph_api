import json
from pathlib import Path

from jobscan.document_index import (
    build_document_index_records,
    classify_document,
    list_job_documents,
    search_documents,
    stable_document_id,
)
from jobscan.job_search import get_preferred_job_documents


def test_stable_document_id_prefers_drive_item_id() -> None:
    row = {"drive_item_id": "01ABC", "job_id": "job-1", "sharepoint_url": "https://example/a.pdf"}
    assert stable_document_id(row) == "driveitem-01ABC"


def test_stable_document_id_fallback_is_deterministic() -> None:
    row = {"job_id": "job-1", "relative_path": "Job/Invoice.pdf", "sharepoint_url": "https://example/invoice.pdf"}
    assert stable_document_id(row) == stable_document_id(dict(row))
    assert stable_document_id(row).startswith("doc-")


def test_document_classification_rules() -> None:
    assert classify_document("Estimate Roofing.xlsx")["document_type"] == "estimate"
    assert classify_document("Signed Roof Proposal.pdf")["document_type"] == "proposal"
    assert classify_document("Invoice No. 2026-042.pdf")["document_type"] == "invoice"
    assert classify_document("Job Tracking Form.xlsx")["document_type"] == "job_tracking"
    assert classify_document("EagleView Report.pdf")["document_type"] == "aerial"
    assert classify_document("Field Notes Scan.pdf")["document_type"] == "field_notes"


def test_build_document_index_records_from_existing_manifest_fixture(tmp_path: Path) -> None:
    job_index = tmp_path / "job_index.json"
    cache_root = tmp_path / ".cache"
    manifest_dir = cache_root / "Data" / "2026 FLOORING_COMPLETED"
    manifest_dir.mkdir(parents=True)
    job_index.write_text(
        json.dumps(
            [
                {
                    "job_id": "JOB-DIVEN",
                    "folder_name": "Diven, Clint - Lee Sporting Shop",
                    "folder_path": "Diven, Clint - Lee Sporting Shop",
                    "division": "Flooring",
                    "source_year": "2026",
                }
            ]
        ),
        encoding="utf-8",
    )
    (manifest_dir / ".jobscan_manifest.json").write_text(
        json.dumps(
            {
                "items": {
                    "01X": {
                        "name": "Invoice No. 2026-042.pdf",
                        "size": 123,
                        "webUrl": "https://sharepoint.example/invoice.pdf",
                        "file": {"mimeType": "application/pdf", "fileExtension": ".pdf", "hashes": {"quickXorHash": "abc"}},
                        "lastModifiedDateTime": "2026-01-01T00:00:00Z",
                        "local_path": "Diven, Clint - Lee Sporting Shop/Invoice No. 2026-042.pdf",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    rows = build_document_index_records(job_index_path=job_index, cache_root=cache_root)

    assert len(rows) == 1
    assert rows[0]["document_id"] == "driveitem-01X"
    assert rows[0]["job_id"] == "JOB-DIVEN"
    assert rows[0]["document_type"] == "invoice"
    assert rows[0]["sharepoint_url"] == "https://sharepoint.example/invoice.pdf"


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, stmt, params=None):
        sql = str(stmt)
        rows = self.rows
        if "information_schema.tables" in sql:
            return FakeScalar(True)
        if "COUNT(*) FROM documents" in sql:
            return FakeScalar(len(rows))
        if params and params.get("job_id"):
            rows = [row for row in rows if row["job_id"] == params["job_id"]]
        if params and params.get("document_type"):
            rows = [row for row in rows if row["document_type"] == params["document_type"]]
        for key, value in (params or {}).items():
            if key.startswith("token_"):
                token = value.strip("%").lower()
                rows = [row for row in rows if token in row["file_name"].lower() or token in row.get("relative_path", "").lower()]
        return FakeRows(rows)


class FakeScalar:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value


class FakeRows:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


def test_document_query_helpers_filter_and_preserve_urls() -> None:
    rows = [
        {"document_id": "1", "job_id": "JOB", "document_type": "invoice", "file_name": "Invoice.pdf", "sharepoint_url": "https://example/invoice.pdf", "relative_path": "Job/Invoice.pdf"},
        {"document_id": "2", "job_id": "JOB", "document_type": "estimate", "file_name": "Estimate.xlsx", "sharepoint_url": "https://example/estimate.xlsx", "relative_path": "Job/Estimate.xlsx"},
        {"document_id": "3", "job_id": "JOB", "document_type": "estimate", "file_name": "Estimate copy.xlsx", "sharepoint_url": "https://example/estimate.xlsx", "relative_path": "Job/Estimate copy.xlsx"},
    ]
    conn = FakeConnection(rows)

    assert [row["file_name"] for row in list_job_documents(conn, "JOB", "invoice")] == ["Invoice.pdf"]
    assert [row["file_name"] for row in search_documents(conn, "estimate", job_id="JOB")] == ["Estimate.xlsx"]


def test_preferred_job_documents_uses_documents_table_before_job_level_urls() -> None:
    conn = FakeConnection(
        [
            {"document_id": "1", "job_id": "JOB", "document_type": "invoice", "file_name": "Invoice.pdf", "sharepoint_url": "https://example/indexed-invoice.pdf", "relative_path": "Job/Invoice.pdf"},
        ]
    )
    docs = get_preferred_job_documents(conn, {"job_id": "JOB", "invoice_url": "https://example/job-level.pdf"}, "invoice")

    assert docs[0]["url"] == "https://example/indexed-invoice.pdf"
    assert docs[0]["file_name"] == "Invoice.pdf"
