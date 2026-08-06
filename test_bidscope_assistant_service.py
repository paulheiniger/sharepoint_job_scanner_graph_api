from __future__ import annotations

import base64
import json
from dataclasses import replace
from pathlib import Path

import fitz
import networkx as nx

from ingest.package_ingest import inspect_uploaded_package
from jobscan.business.bidscope_service import (
    BidScopeInputError,
    _build_seed_reference_trees,
    _detect_drawing_scales,
    _validate_sharepoint_url,
    build_bidscope_review_packet_from_inspection,
    create_bidscope_measurement_context,
)


class FakeUpload:
    def __init__(self, name: str, content: bytes) -> None:
        self.name = name
        self._content = content

    def getvalue(self) -> bytes:
        return self._content


def test_business_ops_prefers_complete_native_package_analysis() -> None:
    instructions = Path("services/estimator_api/chatgpt_instructions.md").read_text(
        encoding="utf-8"
    )
    normalized = " ".join(instructions.split())

    assert "analyze the entire package with native document reasoning" in normalized
    assert "Use `selectBidScopePages` as a fallback" in normalized
    assert "identify the seed sheet and foam note/specification" in normalized
    assert "quantity is not measurable from the available package" in normalized


def _pdf(text: str) -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text, fontsize=11)
    payload = document.tobytes()
    document.close()
    return payload


def test_bidscope_packet_contains_seed_and_reference_linked_measurement_page(tmp_path) -> None:
    uploads = [
        FakeUpload(
            "A-601 Wall Types.pdf",
            _pdf("A-601 Wall Types\nWall Type W3 requires spray foam insulation. See A-301."),
        ),
        FakeUpload(
            "A-301 Building Sections.pdf",
            _pdf("A-301 Building Section\nWall Type W3. See A-101 Floor Plan for dimensions."),
        ),
        FakeUpload(
            "A-101 Floor Plan.pdf",
            _pdf("A-101 Floor Plan\nDimensioned exterior wall layout."),
        ),
    ]
    inspection = inspect_uploaded_package(uploads)
    source_url = "https://spraytec.sharepoint.com/sites/Data/Shared%20Documents/Test"
    inspection = replace(
        inspection,
        candidates=[
            replace(candidate, source_sharepoint_url=source_url)
            for candidate in inspection.candidates
        ],
    )

    result = build_bidscope_review_packet_from_inspection(
        inspection,
        sharepoint_url=source_url,
        max_scan_pages=25,
        max_packet_pages=6,
        artifact_dir=tmp_path,
    )

    assert result["schema_version"] == "spraytec.bidscope_page_selection.v1"
    assert any(row["sheet_id"] == "A-601" for row in result["seed_pages"])
    assert any(row["sheet_id"] == "A-101" for row in result["measurement_candidates"])
    assert any(row["reference_path"] for row in result["measurement_candidates"])
    seed_tree = next(
        tree for tree in result["reference_trees"] if tree["seed_sheet_id"] == "A-601"
    )
    target = next(
        row for row in seed_tree["measurement_targets"] if row["sheet_id"] == "A-101"
    )
    assert target["reference_path"] == ["A-601", "A-301", "A-101"]
    assert len(target["reference_steps"]) == 2
    assert target["assistant_area_description_required"] is True
    assert seed_tree["status"] == "ready_for_visual_measurement_review"
    assert result["scope_gaps"] == []
    assert result["measurement_readiness"]["status"] == "requires_confirmed_pages_and_scale"
    assert result["measurement_readiness"]["evidence_review_required"] is True
    assert result["measurement_readiness"]["segmentation_status"] == "not_run"
    assert "do not run selectBidScopePages again" in result["assistant_review_instruction"]
    assert len(result["context_id"]) == 32
    attachment = result["openaiFileResponse"][0]
    packet = fitz.open(stream=base64.b64decode(attachment["content"]), filetype="pdf")
    try:
        assert packet.page_count == result["packet_page_count"]
    finally:
        packet.close()


def test_confirmed_pages_create_scaled_vector_and_raster_measurement_context(tmp_path) -> None:
    source_url = "https://spraytec.sharepoint.com/sites/Data/Shared%20Documents/Test"
    inspection = inspect_uploaded_package(
        [
            FakeUpload(
                "A-101 Floor Plan.pdf",
                _pdf('A-101 Floor Plan\nSCALE: 1/8" = 1\'-0"\nExterior wall dimensions.'),
            )
        ]
    )
    inspection = replace(
        inspection,
        candidates=[
            replace(candidate, source_sharepoint_url=source_url)
            for candidate in inspection.candidates
        ],
    )
    selection = build_bidscope_review_packet_from_inspection(
        inspection,
        sharepoint_url=source_url,
        max_scan_pages=25,
        max_packet_pages=3,
        artifact_dir=tmp_path,
    )
    page_id = (
        selection["measurement_candidates"]
        or selection["seed_pages"]
        or selection["supporting_reference_pages"]
    )[0]["page_id"]

    unconfirmed = create_bidscope_measurement_context(
        context_id=selection["context_id"],
        confirmed_pages=[{"page_id": page_id}],
        render_dpi=144,
        artifact_dir=tmp_path,
    )
    detected = unconfirmed["pages"][0]["scale_calibration"]
    assert detected["status"] == "detected_requires_confirmation"
    assert detected["scale_inches_per_foot"] == 0.125
    assert unconfirmed["measurement_readiness"]["status"] == "requires_scale_confirmation"

    confirmed = create_bidscope_measurement_context(
        context_id=selection["context_id"],
        confirmed_pages=[
            {"page_id": page_id, "confirmed_scale_text": '1/8" = 1\'-0"'}
        ],
        render_dpi=144,
        artifact_dir=tmp_path,
    )
    calibration = confirmed["pages"][0]["scale_calibration"]
    assert calibration["status"] == "confirmed"
    assert calibration["pdf_points_per_foot"] == 9.0
    assert calibration["rendered_pixels_per_foot"] == 18.0
    assert confirmed["measurement_readiness"]["status"] == "ready_for_tracing"
    assert confirmed["reference_trees"] == selection["reference_trees"]
    assert confirmed["scope_gaps"] == selection["scope_gaps"]
    context_path = tmp_path / confirmed["measurement_context_id"]
    assert (context_path / confirmed["pages"][0]["source_pdf_asset_name"]).is_file()
    assert (context_path / confirmed["pages"][0]["rendered_image_asset_name"]).is_file()
    attachment = confirmed["openaiFileResponse"][0]
    packet = fitz.open(stream=base64.b64decode(attachment["content"]), filetype="pdf")
    try:
        assert packet.page_count == 1
    finally:
        packet.close()


def test_bidscope_seed_tree_reports_missing_referenced_measurement_sheet(tmp_path) -> None:
    source_url = "https://spraytec.sharepoint.com/sites/Data/Shared%20Documents/Test"
    inspection = inspect_uploaded_package(
        [
            FakeUpload(
                "A-601 Wall Types.pdf",
                _pdf(
                    "A-601 Wall Types\nWall Type W3 requires spray foam insulation. "
                    "See exterior elevation A-999 for wall geometry."
                ),
            )
        ]
    )
    inspection = replace(
        inspection,
        candidates=[
            replace(candidate, source_sharepoint_url=source_url)
            for candidate in inspection.candidates
        ],
    )

    result = build_bidscope_review_packet_from_inspection(
        inspection,
        sharepoint_url=source_url,
        max_scan_pages=25,
        max_packet_pages=3,
        artifact_dir=tmp_path,
    )

    seed_tree = next(
        tree for tree in result["reference_trees"] if tree["seed_sheet_id"] == "A-601"
    )
    missing = next(
        gap
        for gap in seed_tree["missing_references"]
        if gap["missing_sheet_id"] == "A-999"
    )
    assert missing["gap_type"] == "referenced_sheet_missing_from_scanned_package"
    assert "not measurable" in missing["impact"]
    assert seed_tree["measurement_targets"] == []
    assert seed_tree["status"] == "missing_referenced_geometry"
    assert any(
        gap["gap_type"] == "no_measurement_page_resolved_from_seed"
        for gap in result["scope_gaps"]
    )
    assert result["measurement_readiness"]["scope_gap_count"] >= 2


def test_reference_tree_payload_bounds_large_unresolved_reference_sets() -> None:
    graph = nx.DiGraph()
    graph.add_node(
        "seed-page",
        node_type="page",
        global_page_id="seed-page",
        sheet_number="A-601",
    )
    for index in range(100):
        missing_id = f"unresolved_sheet::A-{700 + index}"
        graph.add_node(
            missing_id,
            node_type="unresolved_reference",
            sheet_number=f"A-{700 + index}",
        )
        graph.add_edge(
            "seed-page",
            missing_id,
            label=f"A-{700 + index}",
            ref_type="unresolved_sheet",
            context="Exterior wall geometry reference",
        )

    trees, gaps, coverage = _build_seed_reference_trees(
        graph=graph,
        seed_nodes=["seed-page"],
        available_seed_count=1,
        tree_nodes=[
            {
                "node_id": "seed-page",
                "global_page_id": "seed-page",
                "sheet_id": "A-601",
                "sheet_title": "Wall Types",
                "role": "seed_page",
                "foam_specific_evidence": ["Spray foam at exterior wall type W3"],
            }
        ],
        selection_rows=[
            {
                "page_id": "seed-page",
                "packet_page": 1,
                "selection_tier": "seed_page",
            }
        ],
        reference_depth=1,
    )

    assert len(trees) == 1
    assert len(trees[0]["missing_references"]) == 9
    assert trees[0]["missing_references"][-1]["omitted_reference_count"] == 92
    assert len(gaps) <= 24
    assert coverage["truncated"] is True
    assert len(json.dumps({"reference_trees": trees, "scope_gaps": gaps})) < 25_000


def test_measurement_context_rejects_unknown_confirmed_page(tmp_path) -> None:
    context_id = "a" * 32
    context_path = tmp_path / context_id
    context_path.mkdir()
    (context_path / "context.json").write_text(
        '{"context_type":"page_selection","expires_at":4102444800,"pages":[]}',
        encoding="utf-8",
    )

    try:
        create_bidscope_measurement_context(
            context_id=context_id,
            confirmed_pages=[{"page_id": "missing::page_1"}],
            artifact_dir=tmp_path,
        )
    except BidScopeInputError as exc:
        assert "Unknown confirmed page_id" in str(exc)
    else:
        raise AssertionError("Expected an unknown page ID to be rejected")


def test_bidscope_sharepoint_url_validation_rejects_non_sharepoint_hosts() -> None:
    try:
        _validate_sharepoint_url("https://example.com/bid.pdf")
    except BidScopeInputError as exc:
        assert "SharePoint" in str(exc)
    else:
        raise AssertionError("Expected a non-SharePoint URL to be rejected")


def test_drawing_scale_detection_accepts_architectural_words_and_ratio() -> None:
    scales = _detect_drawing_scales('Scale 1/8 inch = 1 foot; metric view 1:100')

    assert [row["scale_inches_per_foot"] for row in scales] == [0.12, 0.125]
