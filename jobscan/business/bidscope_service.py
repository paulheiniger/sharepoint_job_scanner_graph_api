from __future__ import annotations

import base64
import math
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from indexing.progressive_pipeline import (
    ProgressiveBudgets,
    candidate_priority,
    run_progressive_package_analysis,
)
from ingest.package_ingest import PackageInspectionResult, expand_sharepoint_zip_candidates
from ingest.sharepoint_package_ingest import inspect_sharepoint_url_package


class BidScopeInputError(ValueError):
    pass


class BidScopeUnavailableError(RuntimeError):
    pass


_SUPPORTING_ROLES = {
    "assembly_definition",
    "detail_reference",
    "detail_sheet",
    "section_reference",
    "section_sheet",
    "wall_type_schedule",
}
_MAX_DOCUMENTS = 12
_MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024


def build_bidscope_review_packet(
    *,
    sharepoint_url: str,
    trade_type: str = "foam_insulation",
    reference_depth: int = 5,
    max_scan_pages: int = 400,
    max_packet_pages: int = 12,
) -> dict[str, Any]:
    """Build a bounded, read-only page-selection packet from a SharePoint link.

    Page discovery and reference expansion are deterministic. The returned PDF is
    intended for a conversational agent to visually review; this service does not
    call an LLM or calculate takeoff quantities.
    """
    source_url = _validate_sharepoint_url(sharepoint_url)
    inspection = inspect_sharepoint_url_package(source_url)
    if not inspection.candidates:
        detail = "; ".join(inspection.warnings[:3]) or "No PDF or ZIP candidates were found."
        raise BidScopeUnavailableError(detail)
    if any(candidate.source_kind == "sharepoint_zip" for candidate in inspection.candidates):
        inspection = expand_sharepoint_zip_candidates(inspection)
    return build_bidscope_review_packet_from_inspection(
        inspection,
        sharepoint_url=source_url,
        trade_type=trade_type,
        reference_depth=reference_depth,
        max_scan_pages=max_scan_pages,
        max_packet_pages=max_packet_pages,
    )


def build_bidscope_review_packet_from_inspection(
    inspection: PackageInspectionResult,
    *,
    sharepoint_url: str,
    trade_type: str = "foam_insulation",
    reference_depth: int = 5,
    max_scan_pages: int = 400,
    max_packet_pages: int = 12,
) -> dict[str, Any]:
    selected_candidates, deferred_count = _bounded_candidates(inspection)
    if not selected_candidates:
        raise BidScopeUnavailableError("No supported PDF documents were available for page selection.")
    selected_inspection = replace(inspection, candidates=selected_candidates)
    result = run_progressive_package_analysis(
        selected_inspection,
        depth=reference_depth,
        budgets=ProgressiveBudgets(
            max_initial_sample_pages=max_scan_pages,
            max_light_index_pages=max_scan_pages,
            max_deep_analysis_pages=min(max_scan_pages, 150),
            max_ocr_pages=0,
            max_runtime_seconds=75,
            include_low_priority_documents=True,
            full_lightweight_index=True,
        ),
        use_cache=True,
        use_disk_cache=False,
        analysis_mode="Assistant Page Selection",
        trade_type=trade_type,
    )
    pages = list(result.get("pages") or [])
    if not pages:
        raise BidScopeUnavailableError("The selected PDFs could not be read or did not contain indexable pages.")

    tree_nodes = list((result.get("tree") or {}).get("nodes") or [])
    review_nodes = _select_review_nodes(tree_nodes, max_packet_pages=max_packet_pages)
    if not review_nodes:
        review_nodes = _fallback_review_nodes(pages, max_packet_pages=max_packet_pages)
    page_by_id = {page.global_page_id: page for page in pages}
    review_nodes = [
        node
        for node in review_nodes
        if str(node.get("global_page_id") or "") in page_by_id
    ]
    review_pages = [page_by_id[str(node.get("global_page_id") or "")] for node in review_nodes]
    if not review_pages:
        raise BidScopeUnavailableError("No source pages could be assembled into a review packet.")

    document_by_id = {
        str(document.get("document_id") or ""): document
        for document in result.get("documents") or []
    }
    packet_bytes, rasterized = _build_review_pdf(review_pages, document_by_id)
    selection_rows = [_selection_row(node, packet_index=index) for index, node in enumerate(review_nodes, start=1)]
    seed_rows = [row for row in selection_rows if row["selection_tier"] == "seed_page"]
    measurement_rows = [row for row in selection_rows if row["selection_tier"] == "measurement_candidate"]
    support_rows = [
        row
        for row in selection_rows
        if row["selection_tier"] in {"supporting_reference", "connected_context"}
    ]
    scan = dict(result.get("scan_completeness") or {})
    warnings = list(result.get("warnings") or [])
    if deferred_count:
        warnings.append(
            f"{deferred_count} lower-priority document(s) were deferred by the Assistant document limit of {_MAX_DOCUMENTS}."
        )
    if result.get("partial"):
        warnings.append("The page or runtime budget was reached; this is a partial selection, not proof that other relevant pages do not exist.")
    if not seed_rows:
        warnings.append("No high-confidence trade-specific seed page was found; the packet contains the strongest available page candidates.")
    if not measurement_rows:
        warnings.append("No reference-linked measurement page cleared the deterministic threshold; visually review the packet before continuing.")
    if rasterized:
        warnings.append("The review packet was rasterized to keep the ChatGPT Action attachment within its size budget.")

    source_links = _source_links(review_pages, document_by_id)
    return {
        "schema_version": "spraytec.bidscope_page_selection.v1",
        "source_sharepoint_url": sharepoint_url,
        "trade_type": str(result.get("trade_type") or trade_type),
        "trade_name": str(result.get("trade_name") or trade_type.replace("_", " ").title()),
        "selection_method": "deterministic keyword seeds plus bid-document reference graph expansion",
        "assistant_review_instruction": (
            "Inspect the attached pages in packet order. Identify which pages contain the actual measurable plans or elevations, "
            "explain the path from scope seed to each selected measurement page, and ask the estimator to confirm the pages and "
            "drawing scale before any segmentation or quantity calculation."
        ),
        "packet_page_count": len(review_pages),
        "seed_pages": seed_rows,
        "measurement_candidates": measurement_rows,
        "supporting_reference_pages": support_rows,
        "source_links": source_links,
        "coverage": {
            **scan,
            "documents_in_assistant_scan": len(selected_candidates),
            "documents_deferred_by_limit": deferred_count,
            "packet_pages_returned": len(review_pages),
            "packet_page_limit": max_packet_pages,
            "selection_is_partial": bool(result.get("partial") or deferred_count),
        },
        "measurement_readiness": {
            "status": "requires_confirmed_pages_and_scale",
            "segmentation_available_after_review": True,
            "scale_required": True,
            "note": "Segmentation may trace a confirmed scope region, but pixels are not converted to quantities until a drawing scale or known dimension is confirmed.",
        },
        "warnings": sorted(set(warnings)),
        "openaiFileResponse": [
            {
                "name": "bidscope_page_review.pdf",
                "mime_type": "application/pdf",
                "content": base64.b64encode(packet_bytes).decode("ascii"),
            }
        ],
    }


def _validate_sharepoint_url(value: str) -> str:
    normalized = str(value or "").strip()
    parsed = urlsplit(normalized)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not hostname.endswith(".sharepoint.com"):
        raise BidScopeInputError("sharepoint_url must be an HTTPS Microsoft SharePoint link.")
    return normalized


def _bounded_candidates(inspection: PackageInspectionResult) -> tuple[list[Any], int]:
    candidates = list(inspection.candidates)
    ranked = sorted(
        enumerate(candidates),
        key=lambda item: (
            0 if item[1].default_selected else 1,
            {"high": 0, "medium": 1, "low": 2}.get(candidate_priority(item[1]), 3),
            item[0],
        ),
    )
    selected = [candidate for _, candidate in ranked[:_MAX_DOCUMENTS]]
    return selected, max(0, len(candidates) - len(selected))


def _node_score(node: dict[str, Any]) -> float:
    return float(node.get("final_selection_score") or 0.0)


def _node_tier(node: dict[str, Any]) -> str:
    role = str(node.get("role") or "")
    if role == "measurement_page" and float(node.get("measurement_likelihood_score") or 0) >= 20:
        return "measurement_candidate"
    if str(node.get("foam_seed_level") or "") == "high" or role in {"seed_page", "spec_definition"}:
        return "seed_page"
    if role in _SUPPORTING_ROLES:
        return "supporting_reference"
    return "connected_context"


def _select_review_nodes(nodes: list[dict[str, Any]], *, max_packet_pages: int) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for node in nodes:
        page_id = str(node.get("global_page_id") or "")
        if page_id:
            unique.setdefault(page_id, node)
    groups = {
        tier: sorted(
            (node for node in unique.values() if _node_tier(node) == tier),
            key=_node_score,
            reverse=True,
        )
        for tier in ("seed_page", "measurement_candidate", "supporting_reference", "connected_context")
    }
    selected: list[dict[str, Any]] = []
    seed_reserve = min(4, max(1, math.ceil(max_packet_pages / 4)))
    selected.extend(groups["seed_page"][:seed_reserve])
    for tier in ("measurement_candidate", "supporting_reference", "seed_page", "connected_context"):
        for node in groups[tier]:
            if len(selected) >= max_packet_pages:
                break
            if node not in selected:
                selected.append(node)
    return selected


def _fallback_review_nodes(pages: list[Any], *, max_packet_pages: int) -> list[dict[str, Any]]:
    ranked = sorted(pages, key=lambda page: float(page.relevance_score or 0), reverse=True)
    return [
        {
            "global_page_id": page.global_page_id,
            "document_name": page.document_name,
            "page_num": page.page_num,
            "canonical_sheet_id": page.canonical_sheet_id,
            "sheet_title": page.sheet_title,
            "page_type": page.page_type,
            "role": page.role,
            "foam_seed_level": page.foam_seed_level,
            "seed_evidence_score": page.seed_evidence_score,
            "measurement_likelihood_score": page.measurement_likelihood_score,
            "final_selection_score": page.final_selection_score,
            "inclusion_path": page.inclusion_path,
            "evidence": page.evidence,
            "measurement_guidance": "Visually review this fallback candidate.",
        }
        for page in ranked[:max_packet_pages]
    ]


def _selection_row(node: dict[str, Any], *, packet_index: int) -> dict[str, Any]:
    return {
        "packet_page": packet_index,
        "page_id": str(node.get("global_page_id") or node.get("node_id") or ""),
        "document_name": str(node.get("document_name") or ""),
        "source_page_number": int(node.get("page_num") or node.get("page_number") or 0),
        "sheet_id": str(node.get("canonical_sheet_id") or node.get("sheet_id") or ""),
        "sheet_title": str(node.get("sheet_title") or ""),
        "page_type": str(node.get("page_type") or ""),
        "role": str(node.get("role") or ""),
        "selection_tier": _node_tier(node),
        "seed_evidence": list(node.get("foam_specific_evidence") or node.get("evidence") or [])[:12],
        "reference_path": list(node.get("inclusion_path") or [])[:10],
        "seed_evidence_score": float(node.get("seed_evidence_score") or 0),
        "measurement_likelihood_score": float(node.get("measurement_likelihood_score") or 0),
        "selection_score": _node_score(node),
        "measurement_guidance": str(node.get("measurement_guidance") or ""),
    }


def _source_links(review_pages: list[Any], document_by_id: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for page in review_pages:
        document = document_by_id.get(page.document_id) or {}
        url = str(document.get("source_sharepoint_url") or page.source_path or "")
        if not url or url in seen:
            continue
        seen.add(url)
        links.append({"label": str(page.document_name or "Bid document"), "url": url})
    return links


def _build_review_pdf(review_pages: list[Any], document_by_id: dict[str, dict[str, Any]]) -> tuple[bytes, bool]:
    try:
        import fitz
    except ImportError as exc:
        raise BidScopeUnavailableError("PyMuPDF is required to build the BidScope review packet.") from exc

    output = fitz.open()
    open_documents: dict[str, Any] = {}
    try:
        for page in review_pages:
            document = document_by_id.get(page.document_id) or {}
            source_path = Path(str(document.get("file_path") or ""))
            if not source_path.exists():
                continue
            source = open_documents.get(str(source_path))
            if source is None:
                source = fitz.open(str(source_path))
                open_documents[str(source_path)] = source
            if 0 <= int(page.page_index) < source.page_count:
                output.insert_pdf(source, from_page=int(page.page_index), to_page=int(page.page_index))
        if output.page_count == 0:
            raise BidScopeUnavailableError("The selected source PDF pages were no longer available in temporary storage.")
        payload = output.tobytes(garbage=4, deflate=True)
    finally:
        output.close()
        for source in open_documents.values():
            source.close()
    if len(payload) <= _MAX_ATTACHMENT_BYTES:
        return payload, False
    for max_pixels, quality in ((3000, 80), (2200, 70), (1600, 60)):
        payload = _build_raster_review_pdf(
            review_pages,
            document_by_id,
            max_pixels=max_pixels,
            quality=quality,
        )
        if len(payload) <= _MAX_ATTACHMENT_BYTES:
            return payload, True
    raise BidScopeUnavailableError(
        "The selected review pages could not be compressed within the action attachment limit. Reduce max_packet_pages and retry."
    )


def _build_raster_review_pdf(
    review_pages: list[Any],
    document_by_id: dict[str, dict[str, Any]],
    *,
    max_pixels: int,
    quality: int,
) -> bytes:
    import fitz
    from PIL import Image

    output = fitz.open()
    open_documents: dict[str, Any] = {}
    try:
        for page in review_pages:
            document = document_by_id.get(page.document_id) or {}
            source_path = Path(str(document.get("file_path") or ""))
            if not source_path.exists():
                continue
            source = open_documents.get(str(source_path))
            if source is None:
                source = fitz.open(str(source_path))
                open_documents[str(source_path)] = source
            source_page = source[int(page.page_index)]
            scale = min(
                3.0,
                max_pixels
                / max(float(source_page.rect.width), float(source_page.rect.height)),
            )
            pixmap = source_page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            buffer = BytesIO()
            image.save(
                buffer,
                format="JPEG",
                quality=quality,
                optimize=True,
                progressive=True,
            )
            target = output.new_page(width=source_page.rect.width, height=source_page.rect.height)
            target.insert_image(target.rect, stream=buffer.getvalue())
        return output.tobytes(garbage=4, deflate=True)
    finally:
        output.close()
        for source in open_documents.values():
            source.close()
