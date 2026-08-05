from __future__ import annotations

import base64
import json
import math
import os
import re
import shutil
import tempfile
import time
import uuid
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


class BidScopeContextExpiredError(BidScopeUnavailableError):
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
_CONTEXT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_ARCHITECTURAL_SCALE_RE = re.compile(
    r"(?P<drawing>\d+(?:\.\d+)?|\d+\s*/\s*\d+)\s*[\"″]?\s*=\s*"
    r"(?P<feet>\d+)\s*['′]\s*(?:-\s*(?P<inches>\d+(?:\.\d+)?)\s*[\"″])?",
    flags=re.IGNORECASE,
)
_WORD_SCALE_RE = re.compile(
    r"(?P<drawing>\d+(?:\.\d+)?|\d+\s*/\s*\d+)\s*(?:inch(?:es)?|in\.?)\s*=\s*"
    r"(?P<feet>\d+(?:\.\d+)?)\s*(?:feet|foot|ft\.?)",
    flags=re.IGNORECASE,
)
_RATIO_SCALE_RE = re.compile(r"\b1\s*:\s*(?P<denominator>\d+(?:\.\d+)?)\b")


def build_bidscope_review_packet(
    *,
    sharepoint_url: str,
    trade_type: str = "foam_insulation",
    reference_depth: int = 5,
    max_scan_pages: int = 400,
    max_packet_pages: int = 12,
    artifact_dir: Path | None = None,
    ttl_seconds: int = 900,
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
        artifact_dir=artifact_dir,
        ttl_seconds=ttl_seconds,
    )


def build_bidscope_review_packet_from_inspection(
    inspection: PackageInspectionResult,
    *,
    sharepoint_url: str,
    trade_type: str = "foam_insulation",
    reference_depth: int = 5,
    max_scan_pages: int = 400,
    max_packet_pages: int = 12,
    artifact_dir: Path | None = None,
    ttl_seconds: int = 900,
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
    context = _persist_selection_context(
        review_pages=review_pages,
        selection_rows=selection_rows,
        document_by_id=document_by_id,
        source_sharepoint_url=sharepoint_url,
        trade_type=str(result.get("trade_type") or trade_type),
        artifact_dir=artifact_dir or _default_artifact_dir(),
        ttl_seconds=ttl_seconds,
    )
    return {
        "schema_version": "spraytec.bidscope_page_selection.v1",
        "context_id": context["context_id"],
        "expires_at": context["expires_at"],
        "source_sharepoint_url": sharepoint_url,
        "trade_type": str(result.get("trade_type") or trade_type),
        "trade_name": str(result.get("trade_name") or trade_type.replace("_", " ").title()),
        "selection_method": "deterministic keyword seeds plus bid-document reference graph expansion",
        "assistant_review_instruction": (
            "Inspect the attached pages in packet order. Identify which pages contain the actual measurable plans or elevations, "
            "explain the path from scope seed to each selected measurement page, and ask the estimator to confirm the pages and "
            "drawing scale before any segmentation or quantity calculation. Preserve this context_id and the exact returned page_id "
            "for every recommended page. After confirmation, call createBidScopeMeasurementContext with this same context_id; do not "
            "run selectBidScopePages again unless the source link changes or this context expires."
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
            "measurement_context_available_after_review": True,
            "segmentation_status": "not_run",
            "scale_required": True,
            "note": (
                "Confirm page IDs and drawing scales, then create a measurement context from this same selection context. "
                "Do not refresh page selection after confirmation. No segmentation or quantity calculation has run."
            ),
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


def create_bidscope_measurement_context(
    *,
    context_id: str,
    confirmed_pages: list[dict[str, Any]],
    render_dpi: int = 144,
    artifact_dir: Path | None = None,
    ttl_seconds: int = 900,
) -> dict[str, Any]:
    """Prepare estimator-confirmed pages for deterministic tracing.

    Original one-page PDFs remain the coordinate authority. PNGs are rendered at
    a declared DPI for later edge tracing or segmentation; no quantities or masks
    are inferred here.
    """
    root = artifact_dir or _default_artifact_dir()
    selection = _load_context(context_id=context_id, artifact_dir=root)
    if selection.get("context_type") != "page_selection":
        raise BidScopeInputError("context_id must refer to a BidScope page-selection run.")
    if not confirmed_pages:
        raise BidScopeInputError("At least one confirmed page is required.")

    available = {
        str(page.get("page_id") or ""): page
        for page in selection.get("pages") or []
    }
    requested_ids = [str(page.get("page_id") or "").strip() for page in confirmed_pages]
    if len(requested_ids) != len(set(requested_ids)):
        raise BidScopeInputError("confirmed_pages contains a duplicate page_id.")
    missing = [page_id for page_id in requested_ids if page_id not in available]
    if missing:
        raise BidScopeInputError(
            "Unknown confirmed page_id: " + ", ".join(missing[:3])
        )

    measurement_id = uuid.uuid4().hex
    context_path = _context_path(root, measurement_id)
    context_path.mkdir(parents=True, exist_ok=False)
    expires_at = int(time.time()) + min(max(int(ttl_seconds), 60), 14_400)
    output = _new_pdf()
    page_rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    try:
        for index, request_page in enumerate(confirmed_pages, start=1):
            page_id = str(request_page.get("page_id") or "").strip()
            source = available[page_id]
            source_asset = _context_path(root, context_id) / str(source["source_pdf_asset_name"])
            if not source_asset.is_file():
                raise BidScopeUnavailableError(f"Source page asset is unavailable for {page_id}.")
            target_pdf_name = f"measurement_page_{index:02d}.pdf"
            target_pdf = context_path / target_pdf_name
            shutil.copyfile(source_asset, target_pdf)

            pdf = _open_pdf(target_pdf)
            try:
                page = pdf[0]
                output.insert_pdf(pdf)
                pixmap = page.get_pixmap(
                    matrix=_fitz_matrix(render_dpi / 72.0),
                    alpha=False,
                )
                png_name = f"measurement_page_{index:02d}_{render_dpi}dpi.png"
                pixmap.save(str(context_path / png_name))
                vector_content = bool(page.get_drawings()) or bool(page.get_text("text").strip())
                width_points = float(page.rect.width)
                height_points = float(page.rect.height)
            finally:
                pdf.close()

            calibration = _resolve_page_scale(
                detected_scales=list(source.get("detected_scales") or []),
                confirmed_scale_text=str(request_page.get("confirmed_scale_text") or ""),
                confirmed_scale_inches_per_foot=request_page.get("confirmed_scale_inches_per_foot"),
                render_dpi=render_dpi,
            )
            if calibration["status"] != "confirmed":
                warnings.append(
                    f"{source.get('sheet_id') or page_id}: {calibration['warning']}"
                )
            page_rows.append(
                {
                    "page_id": page_id,
                    "packet_page": int(source.get("packet_page") or 0),
                    "document_name": str(source.get("document_name") or ""),
                    "source_page_number": int(source.get("source_page_number") or 0),
                    "sheet_id": str(source.get("sheet_id") or ""),
                    "sheet_title": str(source.get("sheet_title") or ""),
                    "coordinate_authority": "original_vector_pdf_points",
                    "source_pdf_asset_name": target_pdf_name,
                    "rendered_image_asset_name": png_name,
                    "render_dpi": render_dpi,
                    "pdf_width_points": round(width_points, 3),
                    "pdf_height_points": round(height_points, 3),
                    "render_width_pixels": pixmap.width,
                    "render_height_pixels": pixmap.height,
                    "vector_content_detected": vector_content,
                    "scale_calibration": calibration,
                }
            )

        packet_bytes = output.tobytes(garbage=4, deflate=True)
    except Exception:
        shutil.rmtree(context_path, ignore_errors=True)
        raise
    finally:
        output.close()

    if len(packet_bytes) > _MAX_ATTACHMENT_BYTES:
        for max_pixels, quality in ((5000, 82), (3600, 74), (2600, 66), (1800, 58)):
            packet_bytes = _build_context_raster_pdf(
                context_path=context_path,
                page_count=len(page_rows),
                render_dpi=render_dpi,
                max_pixels=max_pixels,
                quality=quality,
            )
            if len(packet_bytes) <= _MAX_ATTACHMENT_BYTES:
                break
        else:
            shutil.rmtree(context_path, ignore_errors=True)
            raise BidScopeUnavailableError(
                "Confirmed pages could not be compressed within the Assistant attachment limit. Confirm fewer pages and retry."
            )
        warnings.append(
            "The Assistant attachment was rasterized to fit its size limit; stored one-page PDFs remain the coordinate authority."
        )
    packet_name = "bidscope_confirmed_measurement_pages.pdf"
    (context_path / packet_name).write_bytes(packet_bytes)
    ready_pages = sum(
        row["scale_calibration"]["status"] == "confirmed" for row in page_rows
    )
    context_payload = {
        "schema_version": "spraytec.bidscope_measurement_context.v1",
        "context_type": "measurement_context",
        "context_id": measurement_id,
        "source_context_id": context_id,
        "created_at": int(time.time()),
        "expires_at": expires_at,
        "trade_type": str(selection.get("trade_type") or ""),
        "source_sharepoint_url": str(selection.get("source_sharepoint_url") or ""),
        "pages": page_rows,
        "packet_asset_name": packet_name,
    }
    _write_json_atomic(context_path / "context.json", context_payload)
    return {
        "schema_version": "spraytec.bidscope_measurement_context.v1",
        "source_context_id": context_id,
        "measurement_context_id": measurement_id,
        "expires_at": expires_at,
        "trade_type": context_payload["trade_type"],
        "confirmed_page_count": len(page_rows),
        "pages": page_rows,
        "measurement_readiness": {
            "status": "ready_for_tracing" if ready_pages == len(page_rows) else "requires_scale_confirmation",
            "pages_ready_for_tracing": ready_pages,
            "pages_requiring_scale_confirmation": len(page_rows) - ready_pages,
            "segmentation_status": "not_run",
            "quantity_status": "not_calculated",
            "next_step": "Trace estimator-confirmed measurement regions on these exact page assets.",
        },
        "warnings": warnings,
        "openaiFileResponse": [
            {
                "name": packet_name,
                "mime_type": "application/pdf",
                "content": base64.b64encode(packet_bytes).decode("ascii"),
            }
        ],
    }


def _default_artifact_dir() -> Path:
    return Path(tempfile.gettempdir()) / "spraytec-estimator-artifacts" / "bidscope"


def _context_path(artifact_dir: Path, context_id: str) -> Path:
    return Path(artifact_dir).expanduser().resolve() / context_id


def _persist_selection_context(
    *,
    review_pages: list[Any],
    selection_rows: list[dict[str, Any]],
    document_by_id: dict[str, dict[str, Any]],
    source_sharepoint_url: str,
    trade_type: str,
    artifact_dir: Path,
    ttl_seconds: int,
) -> dict[str, Any]:
    context_id = uuid.uuid4().hex
    context_path = _context_path(artifact_dir, context_id)
    context_path.mkdir(parents=True, exist_ok=False)
    expires_at = int(time.time()) + min(max(int(ttl_seconds), 60), 14_400)
    rows: list[dict[str, Any]] = []
    open_documents: dict[str, Any] = {}
    try:
        for index, (page, selection_row) in enumerate(
            zip(review_pages, selection_rows), start=1
        ):
            document = document_by_id.get(page.document_id) or {}
            source_path = Path(str(document.get("file_path") or ""))
            if not source_path.is_file():
                raise BidScopeUnavailableError(
                    f"The selected source PDF is no longer available: {page.document_name}."
                )
            source = open_documents.get(str(source_path))
            if source is None:
                source = _open_pdf(source_path)
                open_documents[str(source_path)] = source
            page_index = int(page.page_index)
            if page_index < 0 or page_index >= source.page_count:
                raise BidScopeUnavailableError(
                    f"The selected source page is unavailable: {page.global_page_id}."
                )
            asset_name = f"source_page_{index:02d}.pdf"
            one_page = _new_pdf()
            try:
                one_page.insert_pdf(source, from_page=page_index, to_page=page_index)
                one_page.save(str(context_path / asset_name), garbage=4, deflate=True)
            finally:
                one_page.close()
            rows.append(
                {
                    **selection_row,
                    "source_pdf_asset_name": asset_name,
                    "detected_scales": _detect_drawing_scales(str(page.text or "")),
                }
            )
        payload = {
            "schema_version": "spraytec.bidscope_selection_context.v1",
            "context_type": "page_selection",
            "context_id": context_id,
            "created_at": int(time.time()),
            "expires_at": expires_at,
            "trade_type": trade_type,
            "source_sharepoint_url": source_sharepoint_url,
            "pages": rows,
        }
        _write_json_atomic(context_path / "context.json", payload)
        return payload
    except Exception:
        shutil.rmtree(context_path, ignore_errors=True)
        raise
    finally:
        for source in open_documents.values():
            source.close()


def _load_context(*, context_id: str, artifact_dir: Path) -> dict[str, Any]:
    normalized = str(context_id or "").strip().lower()
    if not _CONTEXT_ID_RE.fullmatch(normalized):
        raise BidScopeInputError("Invalid BidScope context ID.")
    path = _context_path(artifact_dir, normalized) / "context.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BidScopeUnavailableError("BidScope context was not found.") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise BidScopeUnavailableError("BidScope context could not be read.") from exc
    if int(payload.get("expires_at") or 0) < int(time.time()):
        raise BidScopeContextExpiredError("BidScope context has expired.")
    return payload


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, path)


def _fraction(value: str) -> float:
    normalized = value.replace(" ", "")
    if "/" in normalized:
        numerator, denominator = normalized.split("/", 1)
        if float(denominator) == 0:
            raise ValueError("Scale denominator cannot be zero.")
        return float(numerator) / float(denominator)
    return float(normalized)


def _detect_drawing_scales(text: str) -> list[dict[str, Any]]:
    scales: list[dict[str, Any]] = []
    seen: set[float] = set()
    for match in _ARCHITECTURAL_SCALE_RE.finditer(text):
        drawing_inches = _fraction(match.group("drawing"))
        real_feet = float(match.group("feet")) + float(match.group("inches") or 0) / 12.0
        if drawing_inches <= 0 or real_feet <= 0:
            continue
        inches_per_foot = drawing_inches / real_feet
        key = round(inches_per_foot, 8)
        if key in seen:
            continue
        seen.add(key)
        scales.append(
            {
                "scale_text": match.group(0).strip(),
                "scale_inches_per_foot": round(inches_per_foot, 8),
                "detection_method": "printed_text",
            }
        )
    for match in _RATIO_SCALE_RE.finditer(text):
        denominator = float(match.group("denominator"))
        if denominator <= 0:
            continue
        inches_per_foot = 12.0 / denominator
        key = round(inches_per_foot, 8)
        if key in seen:
            continue
        seen.add(key)
        scales.append(
            {
                "scale_text": match.group(0).strip(),
                "scale_inches_per_foot": round(inches_per_foot, 8),
                "detection_method": "printed_text",
            }
        )
    for match in _WORD_SCALE_RE.finditer(text):
        drawing_inches = _fraction(match.group("drawing"))
        real_feet = float(match.group("feet"))
        if drawing_inches <= 0 or real_feet <= 0:
            continue
        inches_per_foot = drawing_inches / real_feet
        key = round(inches_per_foot, 8)
        if key in seen:
            continue
        seen.add(key)
        scales.append(
            {
                "scale_text": match.group(0).strip(),
                "scale_inches_per_foot": round(inches_per_foot, 8),
                "detection_method": "printed_text",
            }
        )
    return scales


def _resolve_page_scale(
    *,
    detected_scales: list[dict[str, Any]],
    confirmed_scale_text: str,
    confirmed_scale_inches_per_foot: Any,
    render_dpi: int,
) -> dict[str, Any]:
    value: float | None = None
    label = confirmed_scale_text.strip()
    source = ""
    if confirmed_scale_inches_per_foot is not None:
        value = float(confirmed_scale_inches_per_foot)
        source = "estimator_confirmed_numeric"
        if label:
            parsed = _detect_drawing_scales(label)
            if len(parsed) != 1:
                raise BidScopeInputError(
                    f"confirmed_scale_text must contain one usable scale, not {label!r}."
                )
            text_value = float(parsed[0]["scale_inches_per_foot"])
            if not math.isclose(value, text_value, rel_tol=1e-6, abs_tol=1e-8):
                raise BidScopeInputError(
                    "confirmed_scale_text conflicts with confirmed_scale_inches_per_foot."
                )
    elif label:
        parsed = _detect_drawing_scales(label)
        if len(parsed) != 1:
            raise BidScopeInputError(
                f"confirmed_scale_text must contain one usable scale, not {label!r}."
            )
        value = float(parsed[0]["scale_inches_per_foot"])
        source = "estimator_confirmed_text"
    if value is not None:
        if not 0 < value <= 12:
            raise BidScopeInputError("Confirmed drawing scale must be greater than 0 and no more than 12 inches per foot.")
        return {
            "status": "confirmed",
            "scale_text": label,
            "scale_inches_per_foot": round(value, 8),
            "pdf_points_per_foot": round(value * 72.0, 6),
            "rendered_pixels_per_foot": round(value * render_dpi, 6),
            "source": source,
            "warning": "Verify one known dimension because PDF printing or resizing can invalidate the title-block scale.",
        }
    if len(detected_scales) == 1:
        detected = detected_scales[0]
        value = float(detected["scale_inches_per_foot"])
        return {
            "status": "detected_requires_confirmation",
            "scale_text": str(detected.get("scale_text") or ""),
            "scale_inches_per_foot": round(value, 8),
            "pdf_points_per_foot": round(value * 72.0, 6),
            "rendered_pixels_per_foot": round(value * render_dpi, 6),
            "source": "printed_text_detected",
            "warning": "Confirm the detected printed scale before tracing quantities.",
        }
    if len(detected_scales) > 1:
        return {
            "status": "ambiguous_requires_confirmation",
            "detected_scales": detected_scales,
            "source": "printed_text_detected",
            "warning": "Multiple printed scales were detected; confirm the scale for the intended view.",
        }
    return {
        "status": "missing_requires_confirmation",
        "source": "none",
        "warning": "No usable printed scale was detected; provide a confirmed scale before tracing quantities.",
    }


def _build_context_raster_pdf(
    *,
    context_path: Path,
    page_count: int,
    render_dpi: int,
    max_pixels: int,
    quality: int,
) -> bytes:
    import fitz
    from PIL import Image

    output = fitz.open()
    try:
        for index in range(1, page_count + 1):
            image_path = context_path / f"measurement_page_{index:02d}_{render_dpi}dpi.png"
            with Image.open(image_path) as source:
                image = source.convert("RGB")
                image.thumbnail((max_pixels, max_pixels), Image.Resampling.LANCZOS)
                buffer = BytesIO()
                image.save(
                    buffer,
                    format="JPEG",
                    quality=quality,
                    optimize=True,
                    progressive=True,
                )
                width, height = image.size
            page = output.new_page(width=width, height=height)
            page.insert_image(page.rect, stream=buffer.getvalue())
        return output.tobytes(garbage=4, deflate=True)
    finally:
        output.close()


def _open_pdf(path: Path) -> Any:
    try:
        import fitz
    except ImportError as exc:
        raise BidScopeUnavailableError("PyMuPDF is required for BidScope page preparation.") from exc
    return fitz.open(str(path))


def _new_pdf() -> Any:
    try:
        import fitz
    except ImportError as exc:
        raise BidScopeUnavailableError("PyMuPDF is required for BidScope page preparation.") from exc
    return fitz.open()


def _fitz_matrix(scale: float) -> Any:
    import fitz

    return fitz.Matrix(scale, scale)


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
