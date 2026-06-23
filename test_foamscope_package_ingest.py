from __future__ import annotations

import zipfile
from io import BytesIO

from foamscope_ui import analyze_documents
from ingest.package_ingest import ingest_uploaded_package


class FakeUpload:
    def __init__(self, name: str, content: bytes) -> None:
        self.name = name
        self._content = content

    def getvalue(self) -> bytes:
        return self._content


def make_pdf(text: str) -> bytes:
    import fitz

    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text, fontsize=11)
    payload = document.tobytes()
    document.close()
    return payload


def make_zip(entries: dict[str, bytes | str]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries.items():
            if isinstance(content, str):
                content = content.encode("utf-8")
            archive.writestr(name, content)
    return buffer.getvalue()


def test_single_pdf_package_still_works() -> None:
    pdf = make_pdf("A-101 Floor Plan\nspray foam insulation wall section R-value")
    package = ingest_uploaded_package([FakeUpload("architectural_A-101.pdf", pdf)])

    assert len(package.documents) == 1
    result = analyze_documents(package.documents, depth=1, use_ocr=False)

    assert len(result["pages"]) == 1
    page = result["pages"][0]
    assert page.document_name == "architectural_A-101.pdf"
    assert page.global_page_id.startswith(page.document_id)
    assert page.page_num == 1
    assert page.document_type == "architectural_drawings"


def test_multiple_pdfs_work_as_one_package() -> None:
    pdf_a = make_pdf("A-101 Floor Plan\nspray foam insulation. See 1/A-301.")
    pdf_b = make_pdf("A-301 Wall Section\nclosed-cell spray foam roof section")
    package = ingest_uploaded_package([FakeUpload("A-101.pdf", pdf_a), FakeUpload("A-301.pdf", pdf_b)])

    result = analyze_documents(package.documents, depth=2, use_ocr=False)

    assert len(result["documents"]) == 2
    assert len(result["pages"]) == 2
    assert {page.document_name for page in result["pages"]} == {"A-101.pdf", "A-301.pdf"}


def test_zip_with_pdfs_extracts_pdf_documents() -> None:
    pdf = make_pdf("Project Manual\nspecification section 07 spray foam insulation")
    payload = make_zip({"docs/specifications.pdf": pdf})

    package = ingest_uploaded_package([FakeUpload("bid_package.zip", payload)])

    assert len(package.documents) == 1
    assert package.documents[0].document_name == "specifications.pdf"
    assert package.documents[0].source_path == "bid_package.zip:docs/specifications.pdf"


def test_zip_with_non_pdf_files_skips_with_warnings() -> None:
    pdf = make_pdf("A-101 Floor Plan\nspray foam")
    payload = make_zip(
        {
            "__MACOSX/._ignored.pdf": b"ignored",
            ".DS_Store": b"ignored",
            "notes.txt": "not a pdf",
            "../unsafe.pdf": pdf,
            "drawings/A-101.pdf": pdf,
        }
    )

    package = ingest_uploaded_package([FakeUpload("mixed.zip", payload)])

    assert len(package.documents) == 1
    assert package.documents[0].document_name == "A-101.pdf"
    assert any("non-PDF" in warning for warning in package.warnings)
    assert any("unsafe ZIP path" in warning for warning in package.warnings)


def test_duplicate_sheet_ids_produce_warning() -> None:
    pdf_a = make_pdf("A-101 Floor Plan\nspray foam insulation")
    pdf_b = make_pdf("A-101 Floor Plan\nclosed-cell spray foam wall section")
    package = ingest_uploaded_package([FakeUpload("arch_a.pdf", pdf_a), FakeUpload("arch_b.pdf", pdf_b)])

    result = analyze_documents(package.documents, depth=1, use_ocr=False)

    assert any("Duplicate sheet_id A-101" in warning for warning in result["warnings"])
