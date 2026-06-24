from __future__ import annotations

import json
import zipfile
from io import BytesIO

from foamscope_ui import analyze_documents, build_export_payload
from indexing.progressive_pipeline import _PROGRESSIVE_CACHE, ProgressiveBudgets, candidate_priority, run_progressive_package_analysis
from ingest.package_ingest import (
    PackageInspectionResult,
    PdfCandidate,
    expand_sharepoint_zip_candidates,
    inspect_path_package,
    inspect_uploaded_package,
    ingest_uploaded_package,
    materialize_selected_documents,
    triage_inspection,
    triage_pdf_candidate,
)
from training.completed_takeoff_parser import parse_stack_takeoff_csv


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


def make_pdf_pages(texts: list[str]) -> bytes:
    import fitz

    document = fitz.open()
    for text in texts:
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


def test_progressive_large_low_priority_package_does_not_process_every_page() -> None:
    arch = make_pdf("A-601 Wall Types\nWall Type W3 requires spray foam insulation. See A-101.")
    entries = {"architectural/A-601.pdf": arch}
    for index in range(12):
        entries[f"electrical/E-{100 + index}.pdf"] = make_pdf(f"E-{100 + index} Electrical Plan\nlighting only")
    inspection = triage_inspection(inspect_uploaded_package([FakeUpload("large_bid.zip", make_zip(entries))]))

    result = run_progressive_package_analysis(inspection, budgets=ProgressiveBudgets(max_initial_sample_pages=20), use_cache=False)

    assert result["progress"]["pdf_count"] == 13
    assert result["progress"]["fast_scanned_documents"] < result["progress"]["pdf_count"]
    assert result["progress"]["deferred_pages"] > 0
    assert any(row["priority"] == "low" and row["status"] == "deferred" for row in result["manifest"])


def test_progressive_budget_hit_returns_partial_results() -> None:
    entries = {
        "architectural/A-601.pdf": make_pdf("A-601 Wall Types\nspray foam insulation"),
        "architectural/A-301.pdf": make_pdf("A-301 Section\nWall Type W3"),
    }
    inspection = triage_inspection(inspect_uploaded_package([FakeUpload("budget.zip", make_zip(entries))]))

    result = run_progressive_package_analysis(
        inspection,
        budgets=ProgressiveBudgets(max_initial_sample_pages=1, max_light_index_pages=1, max_deep_analysis_pages=1),
        use_cache=False,
    )

    assert result["partial"] is True
    assert result["progress"]["fast_scanned_pages"] <= 1


def test_full_package_analysis_indexes_all_pages_and_low_priority_docs() -> None:
    entries = {
        "architectural/A-601.pdf": make_pdf_pages(
            [
                "A-601 Wall Types\nWall Type W3 requires spray foam insulation. See A-301.",
                "A-602 Exterior Wall Assemblies\nclosed-cell SPF",
                "A-603 Air Barrier Notes",
            ]
        ),
        "electrical/E-101.pdf": make_pdf_pages(
            [
                "E-101 Electrical Plan\nlighting only",
                "E-102 Electrical Details",
            ]
        ),
    }
    inspection = triage_inspection(inspect_uploaded_package([FakeUpload("full_bid.zip", make_zip(entries))]))

    result = run_progressive_package_analysis(
        inspection,
        budgets=ProgressiveBudgets(
            max_initial_sample_pages=None,
            max_light_index_pages=None,
            max_deep_analysis_pages=100,
            max_runtime_seconds=None,
            include_low_priority_documents=True,
            full_lightweight_index=True,
        ),
        use_cache=False,
    )

    assert result["partial"] is False
    assert result["progress"]["fast_scanned_documents"] == 2
    assert result["progress"]["fast_scanned_pages"] == 5
    assert result["progress"]["deferred_pages"] == 0
    assert any(row["priority"] == "low" and row["status"] == "manifested" for row in result["manifest"])
    assert result["progress"]["full_lightweight_index"] is True


def test_exported_json_is_parseable_and_node_counts_match() -> None:
    inspection = triage_inspection(
        inspect_uploaded_package([FakeUpload("architectural_A-601.pdf", make_pdf("A-601 Wall Types\nspray foam insulation. See A-101."))])
    )
    result = run_progressive_package_analysis(
        inspection,
        budgets=ProgressiveBudgets(max_initial_sample_pages=None, max_light_index_pages=None, max_runtime_seconds=None, full_lightweight_index=True),
        use_cache=False,
        analysis_mode="Full Package Analysis",
    )

    payload = build_export_payload(result, result["pages"], analysis_mode="Full Package Analysis")
    loaded = json.loads(json.dumps(payload, default=str))

    assert loaded["measurement_tree"]["selected_node_count"] == len(loaded["measurement_tree"]["nodes"])
    assert loaded["exported_node_count"] == len(loaded["selected_nodes_exported"])
    assert loaded["scan_completeness"]["analysis_mode"] == "Full Package Analysis"


def test_split_page_pdfs_preserve_original_document_context() -> None:
    package = ingest_uploaded_package([FakeUpload("Original Plan Set.pdf Page 116.pdf", make_pdf("A-116 Wall Section\nspray foam insulation"))])

    assert package.documents[0].original_document_name == "Original Plan Set.pdf"
    assert package.documents[0].original_page_number == 116
    result = analyze_documents(package.documents, depth=1, use_ocr=False)
    page = result["pages"][0]
    assert page.original_document_name == "Original Plan Set.pdf"
    assert page.original_page_number == 116


def test_short_fake_sheet_ids_are_not_trusted() -> None:
    package = ingest_uploaded_package([FakeUpload("notes.pdf", make_pdf("A1\nA2\nA4\nGeneral insulation notes only"))])

    result = analyze_documents(package.documents, depth=1, use_ocr=False)
    page = result["pages"][0]

    assert page.sheet_id == ""
    assert page.sheet_id_confidence < 0.6
    assert any("Untrusted sheet id" in warning for warning in page.warnings)


def test_generic_insulation_alone_does_not_create_high_confidence_seed() -> None:
    package = ingest_uploaded_package([FakeUpload("generic.pdf", make_pdf("A-101 Floor Plan\ninsulation partition type exterior wall assembly"))])

    result = analyze_documents(package.documents, depth=2, use_ocr=False)

    assert result["seed_nodes"] == []
    assert result["tree"]["high_confidence_scope_nodes"] == []
    assert "No foam-specific scope seed found" in result["tree"]["seed_guidance"]


def test_foam_seed_connected_to_floor_plan_produces_measurement_path() -> None:
    wall = make_pdf("A-601 Wall Types\nWall Type W3 requires spray foam insulation. See A-301.")
    section = make_pdf("A-301 Building Section\nWall Type W3. See A-101 Floor Plan.")
    plan = make_pdf("A-101 Floor Plan\nExterior wall layout.")
    package = ingest_uploaded_package(
        [
            FakeUpload("Wall Types.pdf", wall),
            FakeUpload("Sections.pdf", section),
            FakeUpload("Plans.pdf", plan),
        ]
    )

    result = analyze_documents(package.documents, depth=6, use_ocr=False)
    relevant_by_sheet = {row["sheet_id"]: row for row in result["relevant_rows"]}

    assert relevant_by_sheet["A-101"]["role"] == "measurement_page"
    assert "A-101" in relevant_by_sheet["A-101"]["inclusion_path"]
    assert relevant_by_sheet["A-101"]["inclusion_path"]


def test_filename_sheet_id_preferred_over_noisy_extracted_text() -> None:
    package = ingest_uploaded_package([FakeUpload("A6.13.pdf", make_pdf("S130\nA6.13 Wall Section\nspray foam insulation"))])

    result = analyze_documents(package.documents, depth=1, use_ocr=False)
    page = result["pages"][0]

    assert page.filename_sheet_id == "A6-13"
    assert page.canonical_sheet_id == "A6-13"
    assert page.sheet_id == "A6-13"
    assert page.sheet_id_source == "filename"
    assert page.extracted_sheet_id == "S-130"


def test_plumbing_filename_sheet_id_preferred_over_noisy_text() -> None:
    package = ingest_uploaded_package([FakeUpload("P6.02.pdf", make_pdf("CO-300\nP6.02 Plumbing Plan"))])

    result = analyze_documents(package.documents, depth=1, use_ocr=False)
    page = result["pages"][0]

    assert page.filename_sheet_id == "P6-02"
    assert page.canonical_sheet_id == "P6-02"
    assert page.sheet_id == "P6-02"


def test_partition_types_are_not_sheet_references() -> None:
    package = ingest_uploaded_package([FakeUpload("A0.01.pdf", make_pdf("A0.01 Partition Schedule\nPartition Type P01 and P31"))])

    result = analyze_documents(package.documents, depth=1, use_ocr=False)
    page = result["pages"][0]

    assert any(ref["type"] == "partition_type" and ref["label"].upper() in {"P01", "P31"} for ref in page.references)
    assert not any(ref["type"] == "sheet" and ref["target"] in {"P-01", "P-31"} for ref in page.references)


def test_short_unresolved_references_do_not_expand_graph() -> None:
    seed = make_pdf("A6.13 Wall Section\nspray foam insulation. Notes mention H1 J1 A1 D1 E1.")
    package = ingest_uploaded_package([FakeUpload("A6.13.pdf", seed)])

    result = analyze_documents(package.documents, depth=6, use_ocr=False)

    assert not any(row["type"] == "unresolved_sheet" and row["reference"] in {"H1", "J1", "A1", "D1", "E1"} for row in result["edge_rows"])
    assert len(result["selected_nodes"]) <= 1


def test_generic_plan_only_becomes_measurement_when_connected_to_foam_seed() -> None:
    seed = make_pdf("A6.13 Wall Section\nspray foam insulation. See A2.01.")
    plan = make_pdf("A2.01 Floor Plan\ninsulation partition type exterior wall assembly")
    package = ingest_uploaded_package([FakeUpload("A6.13.pdf", seed), FakeUpload("A2.01.pdf", plan)])

    result = analyze_documents(package.documents, depth=3, use_ocr=False)
    relevant_by_sheet = {row["sheet_id"]: row for row in result["relevant_rows"]}

    assert relevant_by_sheet["A2-01"]["role"] == "measurement_page"
    assert relevant_by_sheet["A2-01"]["inclusion_path"]


def test_progressive_cache_resume_avoids_reprocessing() -> None:
    inspection = triage_inspection(
        inspect_uploaded_package([FakeUpload("architectural_A-101.pdf", make_pdf("A-101 Floor Plan\nspray foam insulation"))])
    )
    budgets = ProgressiveBudgets(max_initial_sample_pages=10)

    first = run_progressive_package_analysis(inspection, budgets=budgets, use_cache=True)
    second = run_progressive_package_analysis(inspection, budgets=budgets, use_cache=True)

    assert first["cache_hit"] is False
    assert second["cache_hit"] is True


def test_progressive_disk_cache_resume_avoids_reprocessing(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    inspection = triage_inspection(
        inspect_uploaded_package([FakeUpload("architectural_A-601.pdf", make_pdf("A-601 Wall Types\nspray foam insulation"))])
    )
    budgets = ProgressiveBudgets(
        max_initial_sample_pages=None,
        max_light_index_pages=None,
        max_runtime_seconds=None,
        include_low_priority_documents=True,
        full_lightweight_index=True,
    )

    first = run_progressive_package_analysis(inspection, budgets=budgets, use_cache=True, use_disk_cache=True)
    _PROGRESSIVE_CACHE.clear()
    second = run_progressive_package_analysis(inspection, budgets=budgets, use_cache=True, use_disk_cache=True)

    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert (tmp_path / ".cache" / "foamscope_progressive").exists()


def test_path_based_zip_intake_works_without_file_uploader(tmp_path) -> None:
    zip_path = tmp_path / "server_bid.zip"
    zip_path.write_bytes(make_zip({"architectural/A-101.pdf": make_pdf("A-101 Floor Plan\nspray foam insulation")}))

    inspection = inspect_path_package(zip_path)
    result = run_progressive_package_analysis(inspection, budgets=ProgressiveBudgets(max_initial_sample_pages=10), use_cache=False)

    assert len(inspection.candidates) == 1
    assert inspection.candidates[0].source_kind == "zip"
    assert result["progress"]["pdf_count"] == 1
    assert result["progress"]["fast_scanned_documents"] == 1


def test_folder_intake_finds_pdfs_and_zips(tmp_path) -> None:
    (tmp_path / "drawings").mkdir()
    (tmp_path / "drawings" / "A-101.pdf").write_bytes(make_pdf("A-101 Floor Plan\nspray foam"))
    (tmp_path / "specs.zip").write_bytes(make_zip({"specs/project_manual.pdf": make_pdf("Project Manual\n07 21 00 thermal insulation")}))

    inspection = inspect_path_package(tmp_path)
    names = {candidate.document_name for candidate in inspection.candidates}

    assert {"A-101.pdf", "project_manual.pdf"}.issubset(names)
    assert len(inspection.candidates) == 2


def test_large_zip_manifest_inspected_without_extracting_all_files(tmp_path) -> None:
    zip_path = tmp_path / "large_manifest.zip"
    entries = {f"electrical/E-{index}.pdf": make_pdf(f"E-{index} Electrical Plan") for index in range(20)}
    zip_path.write_bytes(make_zip(entries))

    inspection = inspect_path_package(zip_path)

    assert len(inspection.candidates) == 20
    assert all(candidate.source_kind == "zip" for candidate in inspection.candidates)
    assert all(not candidate.file_path for candidate in inspection.candidates)


def test_sharepoint_zip_container_is_pending_manifest_not_irrelevant() -> None:
    candidate = PdfCandidate(
        candidate_id="zip-1",
        document_name="Structural Export.zip",
        document_type="stack_export_zip",
        source_kind="sharepoint_zip",
        source_path="https://example.sharepoint.com/sites/Data/Structural%20Export.zip",
        compressed_size=100,
        uncompressed_size=100,
        default_selected=True,
        file_hash="abc123",
        graph_drive_id="drive",
        graph_item_id="item",
        source_sharepoint_url="https://example.sharepoint.com/sites/Data/Structural%20Export.zip",
        source_zip_name="Structural Export.zip",
    )

    triaged, warnings = triage_pdf_candidate(candidate)

    assert warnings == []
    assert triaged.document_type == "stack_export_zip"
    assert triaged.triage_classification == "pending_manifest"
    assert triaged.default_selected is True
    assert candidate_priority(triaged) == "high"


def test_sharepoint_zip_with_pdfs_processes_without_manual_extraction(tmp_path, monkeypatch) -> None:
    zip_path = tmp_path / "stack_export.zip"
    zip_path.write_bytes(make_zip({"Plans/A3.01.pdf": make_pdf("A3.01 Roof Plan\nTPO roof replacement")}))
    candidate = PdfCandidate(
        candidate_id="zip-1",
        document_name="STACK Export.zip",
        document_type="stack_export_zip",
        source_kind="sharepoint_zip",
        source_path="https://example.sharepoint.com/:u:/stack-export",
        compressed_size=zip_path.stat().st_size,
        uncompressed_size=zip_path.stat().st_size,
        default_selected=True,
        file_hash="ziphash",
        source_sharepoint_url="https://example.sharepoint.com/:u:/stack-export",
        source_zip_name="STACK Export.zip",
    )
    inspection = PackageInspectionResult(candidates=[candidate], warnings=[], temp_dir=str(tmp_path), total_upload_size=zip_path.stat().st_size)
    monkeypatch.setattr("ingest.package_ingest._download_sharepoint_zip_to_cache", lambda _candidate: zip_path)

    expanded = expand_sharepoint_zip_candidates(inspection)
    package = materialize_selected_documents(expanded, {expanded.candidates[0].candidate_id})
    result = analyze_documents(package.documents, depth=1, use_ocr=False, trade_type="roofing")

    assert len(expanded.candidates) == 1
    assert expanded.candidates[0].document_name == "A3.01.pdf"
    assert package.documents[0].source_sharepoint_url == candidate.source_sharepoint_url
    assert package.documents[0].source_zip_name == "STACK Export.zip"
    assert package.documents[0].internal_zip_path == "Plans/A3.01.pdf"
    assert result["pages"][0].document_name == "A3.01.pdf"


def test_sharepoint_zip_with_takeoff_csv_routes_to_parser(tmp_path, monkeypatch) -> None:
    takeoff_csv = "\n".join(
        [
            "Takeoff Name,Takeoff Description,Sq Ft,Ln Ft,Cu Yd,EA,Drop Count,Takeoff Quantity,Takeoff Unit,Scale,Plan Name",
            "Roof Area,TPO roof area,1200,,,,,1200,Sq Ft,1/8\"=1',A3.01.pdf",
        ]
    )
    zip_path = tmp_path / "stack_export.zip"
    zip_path.write_bytes(make_zip({"Takeoff Quantity.csv": takeoff_csv, "Plans/A3.01.pdf": make_pdf("A3.01 Roof Plan")}))
    candidate = PdfCandidate(
        candidate_id="zip-1",
        document_name="STACK Export.zip",
        document_type="stack_export_zip",
        source_kind="sharepoint_zip",
        source_path="https://example.sharepoint.com/:u:/stack-export",
        compressed_size=zip_path.stat().st_size,
        uncompressed_size=zip_path.stat().st_size,
        default_selected=True,
        file_hash="ziphash",
        source_sharepoint_url="https://example.sharepoint.com/:u:/stack-export",
        source_zip_name="STACK Export.zip",
    )
    inspection = PackageInspectionResult(candidates=[candidate], warnings=[], temp_dir=str(tmp_path), total_upload_size=zip_path.stat().st_size)
    monkeypatch.setattr("ingest.package_ingest._download_sharepoint_zip_to_cache", lambda _candidate: zip_path)

    expanded = expand_sharepoint_zip_candidates(inspection)
    labels = parse_stack_takeoff_csv(expanded.takeoff_csvs[0]["file_path"], trade_type="roofing")

    assert len(expanded.candidates) == 1
    assert len(expanded.takeoff_csvs or []) == 1
    assert expanded.takeoff_csvs[0]["internal_zip_path"] == "Takeoff Quantity.csv"
    assert expanded.takeoff_csvs[0]["source_sharepoint_url"] == candidate.source_sharepoint_url
    assert labels[0].canonical_sheet_id == "A3-01"
    assert labels[0].trade_type == "roofing"


def test_zip_provenance_is_preserved_for_uploaded_zip() -> None:
    payload = make_zip({"Plans/A2.00.pdf": make_pdf("A2.00 Floor Plan\nspray foam")})

    package = ingest_uploaded_package([FakeUpload("stack_export.zip", payload)])

    assert package.documents[0].source_zip_name == "stack_export.zip"
    assert package.documents[0].internal_zip_path == "Plans/A2.00.pdf"
