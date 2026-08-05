from __future__ import annotations

import base64
from dataclasses import replace

import fitz

from ingest.package_ingest import inspect_uploaded_package
from jobscan.business.bidscope_service import (
    BidScopeInputError,
    _validate_sharepoint_url,
    build_bidscope_review_packet_from_inspection,
)


class FakeUpload:
    def __init__(self, name: str, content: bytes) -> None:
        self.name = name
        self._content = content

    def getvalue(self) -> bytes:
        return self._content


def _pdf(text: str) -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text, fontsize=11)
    payload = document.tobytes()
    document.close()
    return payload


def test_bidscope_packet_contains_seed_and_reference_linked_measurement_page() -> None:
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
    )

    assert result["schema_version"] == "spraytec.bidscope_page_selection.v1"
    assert any(row["sheet_id"] == "A-601" for row in result["seed_pages"])
    assert any(row["sheet_id"] == "A-101" for row in result["measurement_candidates"])
    assert any(row["reference_path"] for row in result["measurement_candidates"])
    assert result["measurement_readiness"]["status"] == "requires_confirmed_pages_and_scale"
    attachment = result["openaiFileResponse"][0]
    packet = fitz.open(stream=base64.b64decode(attachment["content"]), filetype="pdf")
    try:
        assert packet.page_count == result["packet_page_count"]
    finally:
        packet.close()


def test_bidscope_sharepoint_url_validation_rejects_non_sharepoint_hosts() -> None:
    try:
        _validate_sharepoint_url("https://example.com/bid.pdf")
    except BidScopeInputError as exc:
        assert "SharePoint" in str(exc)
    else:
        raise AssertionError("Expected a non-SharePoint URL to be rejected")
