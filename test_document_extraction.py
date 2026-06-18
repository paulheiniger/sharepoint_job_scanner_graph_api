import sys
import types
import zipfile
from pathlib import Path

import pytest

from jobscan import document_extraction as de


class FakeScalar:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value


class FakeRows:
    def __init__(self, rows):
        self.rows = rows
        self.rowcount = len(rows) if isinstance(rows, list) else 0

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def fetchall(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class FakeConnection:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.deleted = False
        self.updated_failure = False
        self.inserted = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "information_schema.tables" in sql:
            return FakeScalar(True)
        if "DELETE FROM document_content" in sql:
            self.deleted = True
        if "INSERT INTO document_content" in sql:
            self.inserted.append(params)
        if "extraction_status = 'failed'" in sql:
            self.updated_failure = True
        if "UPDATE documents" in sql:
            return FakeRows([params or {}])
        return FakeRows(self.rows)


class FakeContext:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeEngine:
    def __init__(self, docs):
        self.docs = docs

    def connect(self):
        return FakeContext(FakeConnection(self.docs))

    def begin(self):
        return FakeContext(FakeConnection(self.docs))


def make_docx(path: Path) -> None:
    xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Scope</w:t></w:r></w:p>
    <w:p><w:r><w:t>Install roof coating.</w:t></w:r></w:p>
    <w:tbl>
      <w:tr><w:tc><w:p><w:r><w:t>Item</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Cost</w:t></w:r></w:p></w:tc></w:tr>
      <w:tr><w:tc><w:p><w:r><w:t>Labor</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>1000</w:t></w:r></w:p></w:tc></w:tr>
    </w:tbl>
  </w:body>
</w:document>"""
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("word/document.xml", xml)


def test_cache_path_is_stable_and_existing_cache_is_reused(tmp_path: Path) -> None:
    cache_root = tmp_path / ".cache"
    existing = cache_root / "Jobs" / "Job A" / "Estimate.xlsx"
    existing.parent.mkdir(parents=True)
    existing.write_text("not empty", encoding="utf-8")
    document = {"document_id": "doc/unsafe", "relative_path": "Jobs/Job A/Estimate.xlsx", "file_extension": ".xlsx"}

    assert de.stable_cache_path(document, cache_root).name == "doc_unsafe.xlsx"
    assert de.ensure_local_document(document, cache_root) == existing


def test_download_failure_is_reported_without_live_sharepoint(tmp_path: Path) -> None:
    with pytest.raises(de.DocumentAcquisitionError):
        de.ensure_local_document({"document_id": "doc-1", "file_name": "Missing.pdf"}, tmp_path)


def test_pdf_page_extraction_and_image_only_detection(monkeypatch, tmp_path: Path) -> None:
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF fake")

    class TextPage:
        def __init__(self, value):
            self.value = value

        def extract_text(self):
            return self.value

    class Reader:
        def __init__(self, _path):
            self.pages = [TextPage("Page one text"), TextPage("Page two text")]

    fake = types.ModuleType("pypdf")
    fake.PdfReader = Reader
    monkeypatch.setitem(sys.modules, "pypdf", fake)
    result = de.extract_pdf(pdf)
    assert [row.page_number for row in result.rows] == [1, 2]
    assert result.requires_ocr is False

    class EmptyReader:
        def __init__(self, _path):
            self.pages = [TextPage("")]

    fake.PdfReader = EmptyReader
    result = de.extract_pdf(pdf)
    assert result.rows == []
    assert result.requires_ocr is True


def test_docx_paragraph_heading_and_table_extraction(tmp_path: Path) -> None:
    path = tmp_path / "scope.docx"
    make_docx(path)

    rows = de.extract_docx(path).rows
    assert [row.content_type for row in rows] == ["docx_heading", "docx_paragraph", "docx_table_row", "docx_table_row"]
    assert rows[1].section_name == "Scope"
    assert rows[-1].text_content == "Labor | 1000"


def test_xlsx_worksheet_rows_include_cell_references(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    path = tmp_path / "estimate.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Estimate"
    ws["A1"] = "Item"
    ws["B1"] = "Cost"
    ws["A2"] = "Labor"
    ws["B2"] = 25
    wb.save(path)

    rows = de.extract_xlsx(path).rows
    assert rows[0].sheet_name == "Estimate"
    assert rows[0].cell_range == "A1:B1"
    assert rows[1].source_locator == "Estimate!A2:B2"
    assert "B2: 25" in rows[1].text_content


def test_content_id_prevents_duplicate_chunks() -> None:
    row = de.ExtractedContent(content_type="pdf_page", source_locator="page 1", page_number=1, text_content="Same")
    assert de.content_id_for("DOC", row) == de.content_id_for("DOC", row)


def test_hash_skip_logic_skips_success_and_failure_until_forced() -> None:
    document = {"content_hash": "abc", "extraction_status": "failed"}
    assert de.should_skip_extraction(document, "abc") is True
    assert de.should_skip_extraction(document, "abc", force=True) is False
    assert de.should_skip_extraction(document, "changed") is False


def test_failed_extraction_preserves_prior_content(tmp_path: Path) -> None:
    path = tmp_path / "unsupported.bin"
    path.write_bytes(b"content")
    conn = FakeConnection()
    status, count = de.extract_one_document(
        conn,
        {"document_id": "DOC", "job_id": "JOB", "cached_file_path": str(path), "file_extension": ".bin"},
        tmp_path,
    )

    assert status == "failed"
    assert count == 0
    assert conn.updated_failure is True
    assert conn.deleted is False


def test_successful_replacement_deletes_then_inserts_content(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("Hello Spray-Tec", encoding="utf-8")
    conn = FakeConnection()
    status, count = de.extract_one_document(
        conn,
        {"document_id": "DOC", "job_id": "JOB", "cached_file_path": str(path), "file_extension": ".txt"},
        tmp_path,
    )

    assert status == "extracted"
    assert count == 1
    assert conn.deleted is True
    assert conn.inserted[0]["job_id"] == "JOB"
    assert conn.inserted[0]["normalized_text"] == "hello spray tec"


def test_search_results_include_source_metadata_and_exact_url() -> None:
    rows = [
        {
            "document_id": "DOC",
            "job_id": "JOB",
            "file_name": "Invoice.pdf",
            "document_type": "invoice",
            "sharepoint_url": "https://sharepoint.example/invoice.pdf",
            "content_type": "pdf_page",
            "source_locator": "page 2",
            "page_number": 2,
            "sheet_name": None,
            "row_number": None,
            "text_content": "Invoice total for Diven is 100 dollars.",
        }
    ]
    results = de.search_extracted_text(FakeConnection(rows), "Diven", job_id="JOB")
    assert results[0]["sharepoint_url"] == "https://sharepoint.example/invoice.pdf"
    assert results[0]["source_locator"] == "page 2"
    assert "Diven" in results[0]["excerpt"]


def test_one_document_and_one_job_cli_routes(monkeypatch, capsys) -> None:
    docs = [{"document_id": "DOC", "job_id": "JOB", "file_name": "Estimate.xlsx", "file_extension": ".xlsx"}]
    monkeypatch.setattr(de, "create_engine", lambda _url, future=True: FakeEngine(docs))
    monkeypatch.setattr(de, "extract_one_document", lambda conn, document, cache_root, force=False: ("extracted", 3))

    assert de.main(["--document-id", "DOC", "--database-url", "postgresql://example"]) == 0
    assert "[1/1] Estimate.xlsx — extracted 3 content rows" in capsys.readouterr().out

    assert de.main(["--job-id", "JOB", "--database-url", "postgresql://example"]) == 0
    assert "[1/1] Estimate.xlsx — extracted 3 content rows" in capsys.readouterr().out


def test_cached_metadata_backfill_reads_manifest_and_updates_rows(tmp_path: Path) -> None:
    manifest_dir = tmp_path / ".cache" / "Site" / "Root"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / ".jobscan_manifest.json").write_text(
        """
        {
          "drive_id": "drive-root",
          "documents": [
            {
              "name": "Invoice.pdf",
              "drive_item_id": "item-1",
              "webUrl": "https://sharepoint.example/invoice.pdf",
              "relative_path": "Job/Invoice.pdf"
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    rows = de.manifest_metadata_rows(tmp_path / ".cache")
    assert rows == [
        {
            "drive_id": "drive-root",
            "drive_item_id": "item-1",
            "sharepoint_url": "https://sharepoint.example/invoice.pdf",
            "relative_path": "Job/Invoice.pdf",
            "file_name": "Invoice.pdf",
        }
    ]
    assert de.backfill_document_drive_metadata(FakeConnection(), tmp_path / ".cache") == 1


class FakeGraphClient:
    def __init__(self):
        self.downloads = []

    def get_site(self, hostname, site_path):
        return {"id": "site-1"}

    def get_drive_by_name(self, site_id, library):
        return {"id": "drive-1"}

    def get_root_or_path_item(self, drive_id, path):
        return {"id": "item-1", "name": "Estimate.xlsx", "webUrl": f"https://sharepoint.example/{path}"}

    def download_item(self, drive_id, item_id, destination):
        self.downloads.append((drive_id, item_id, destination))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("downloaded content", encoding="utf-8")


def test_graph_path_resolution_preserves_exact_url() -> None:
    metadata = de.resolve_graph_metadata_for_document(
        FakeGraphClient(),
        {"document_id": "DOC", "relative_path": "Jobs/Estimate.xlsx", "sharepoint_url": "https://old.example/doc"},
        site_url="https://contoso.sharepoint.com/sites/Ops",
        library="Documents",
        root_folder="Shared",
    )

    assert metadata["drive_id"] == "drive-1"
    assert metadata["drive_item_id"] == "item-1"
    assert metadata["sharepoint_url"] == "https://sharepoint.example/Shared/Jobs/Estimate.xlsx"


def test_graph_content_download_uses_drive_identifiers(monkeypatch, tmp_path: Path) -> None:
    fake_client = FakeGraphClient()
    monkeypatch.setattr(de, "GraphClient", lambda max_retries=2: fake_client)

    path = de.ensure_local_document(
        {"document_id": "DOC", "drive_id": "drive-1", "drive_item_id": "item-1", "file_extension": ".pdf"},
        tmp_path,
        force_download=True,
    )

    assert path.read_text(encoding="utf-8") == "downloaded content"
    assert fake_client.downloads[0][0:2] == ("drive-1", "item-1")


def test_html_error_download_is_rejected(monkeypatch, tmp_path: Path) -> None:
    class HtmlClient(FakeGraphClient):
        def download_item(self, drive_id, item_id, destination):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text("<html><title>Sign in</title></html>", encoding="utf-8")

    monkeypatch.setattr(de, "GraphClient", lambda max_retries=2: HtmlClient())

    with pytest.raises(de.DocumentAcquisitionError):
        de.ensure_local_document(
            {"document_id": "DOC", "drive_id": "drive-1", "drive_item_id": "item-1", "file_extension": ".pdf"},
            tmp_path,
            force_download=True,
        )
