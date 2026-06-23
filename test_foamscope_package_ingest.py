from __future__ import annotations

import zipfile
from io import BytesIO

from foamscope_ui import analyze_documents
from ingest.package_ingest import inspect_uploaded_package, ingest_uploaded_package, materialize_selected_documents, triage_inspection


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
    assert package.documents[0].file_path


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


def test_zip_inspection_does_not_extract_until_selected() -> None:
    pdf_a = make_pdf("A-101 Floor Plan\nspray foam insulation")
    pdf_e = make_pdf("E-101 Electrical Plan\nlighting panel schedule")
    payload = make_zip({"drawings/A-101.pdf": pdf_a, "electrical/E-101.pdf": pdf_e})

    inspection = inspect_uploaded_package([FakeUpload("package.zip", payload)])

    assert len(inspection.candidates) == 2
    assert all(not candidate.file_path for candidate in inspection.candidates)
    selected = {candidate.candidate_id for candidate in inspection.candidates if candidate.document_name == "A-101.pdf"}
    package = materialize_selected_documents(inspection, selected)

    assert [document.document_name for document in package.documents] == ["A-101.pdf"]
    assert package.documents[0].file_path


def test_default_selection_prefers_architectural_and_unselects_mep() -> None:
    pdf_a = make_pdf("A-101 Floor Plan\nspray foam insulation")
    pdf_m = make_pdf("M-101 Mechanical Plan\nductwork")
    payload = make_zip({"drawings/A-101.pdf": pdf_a, "mechanical/M-101.pdf": pdf_m})

    inspection = inspect_uploaded_package([FakeUpload("package.zip", payload)])
    selected_by_name = {candidate.document_name: candidate.default_selected for candidate in inspection.candidates}

    assert selected_by_name["A-101.pdf"] is True
    assert selected_by_name["M-101.pdf"] is False


def test_triage_classifies_relevant_and_irrelevant_documents() -> None:
    pdf_arch = make_pdf("A-101 Floor Plan\nspray foam insulation wall section R-value")
    pdf_elec = make_pdf("E-101 Electrical Plan\nfire alarm low voltage lighting")
    payload = make_zip({"architectural/A-101.pdf": pdf_arch, "electrical/E-101.pdf": pdf_elec})

    inspection = triage_inspection(inspect_uploaded_package([FakeUpload("bid.zip", payload)]))
    by_name = {candidate.document_name: candidate for candidate in inspection.candidates}

    assert by_name["A-101.pdf"].triage_classification == "likely_relevant"
    assert by_name["A-101.pdf"].default_selected is True
    assert by_name["E-101.pdf"].triage_classification == "likely_irrelevant"
    assert by_name["E-101.pdf"].default_selected is False


def test_possibly_relevant_a_sheet_is_selected() -> None:
    pdf = make_pdf("A-601 Exterior Wall Sections\nbuilding envelope notes")
    inspection = triage_inspection(inspect_uploaded_package([FakeUpload("drawings_A-601.pdf", pdf)]))

    assert inspection.candidates[0].triage_classification in {"possibly_relevant", "likely_relevant"}
    assert inspection.candidates[0].default_selected is True


def test_reference_expansion_includes_measurement_page_without_foam_keywords() -> None:
    wall_types = make_pdf("A-601 Wall Types\nWall Type W3 requires spray foam thermal insulation and air barrier.")
    section = make_pdf("A-301 Building Section\nWall Type W3. See A-101 Floor Plan for dimensions.")
    plan = make_pdf("A-101 Floor Plan\nPlan layout and dimensions for perimeter surfaces.")
    package = ingest_uploaded_package(
        [
            FakeUpload("A-601 Wall Types.pdf", wall_types),
            FakeUpload("A-301 Sections.pdf", section),
            FakeUpload("A-101 Floor Plan.pdf", plan),
        ]
    )

    result = analyze_documents(package.documents, depth=5, use_ocr=False)
    relevant_by_sheet = {row["sheet_id"]: row for row in result["relevant_rows"]}

    assert "A-101" in relevant_by_sheet
    assert relevant_by_sheet["A-101"]["role"] == "measurement_page"
    assert relevant_by_sheet["A-101"]["foam_relevance"] == "low"
    assert "W3" in relevant_by_sheet["A-101"]["inclusion_path"]


def test_references_resolve_across_multiple_pdfs() -> None:
    spec = make_pdf("Project Manual\n07 21 00 spray foam thermal insulation. Applies to Wall Type W3.")
    wall_types = make_pdf("A-601 Wall Types\nWall Type W3 references A-301 Building Section.")
    section = make_pdf("A-301 Building Section\nWall Type W3. See A-101 Floor Plan.")
    plan = make_pdf("A-101 Floor Plan\nExterior elevation and exterior wall layout.")
    package = ingest_uploaded_package(
        [
            FakeUpload("Project Manual.pdf", spec),
            FakeUpload("Wall Types.pdf", wall_types),
            FakeUpload("Sections.pdf", section),
            FakeUpload("Plans.pdf", plan),
        ]
    )

    result = analyze_documents(package.documents, depth=6, use_ocr=False)
    relevant_by_sheet = {row["sheet_id"]: row for row in result["relevant_rows"]}

    assert {"A-601", "A-301", "A-101"}.issubset(relevant_by_sheet)
    assert relevant_by_sheet["A-101"]["role"] == "measurement_page"
