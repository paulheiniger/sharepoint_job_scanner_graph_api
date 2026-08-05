from __future__ import annotations

import base64
import hashlib
import math
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from jobscan.business.bidscope_service import (
    BidScopeInputError,
    BidScopeUnavailableError,
    _context_path,
    _load_context,
    _write_json_atomic,
)
from roof_measure.geometry import (
    polygon_area_pixels,
    polygon_perimeter_pixels,
    straighten_architectural_ring,
)
from roof_measure.api_context import _ring_self_intersects
from roof_measure.models import RoofSection
from roof_measure.polygonize import section_from_polygon, sections_from_mask
from roof_measure.segmentation import RoofSegmenter, Sam2RoofSegmenter, SegmentationPrompts


_COLORS = (
    (255, 87, 34),
    (0, 150, 136),
    (63, 81, 181),
    (156, 39, 176),
    (255, 152, 0),
    (3, 169, 244),
)
_MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024


def trace_bidscope_regions(
    *,
    measurement_context_id: str,
    regions: list[dict[str, Any]],
    sam2_url: str,
    sam2_api_key: str,
    artifact_dir: Path,
    timeout_seconds: float = 90.0,
    inference_max_side: int = 1600,
    segmenter: RoofSegmenter | None = None,
) -> dict[str, Any]:
    """Trace and deterministically measure estimator-described drawing regions."""
    context = _load_context(
        context_id=measurement_context_id,
        artifact_dir=artifact_dir,
    )
    if context.get("context_type") != "measurement_context":
        raise BidScopeInputError(
            "measurement_context_id must refer to confirmed BidScope measurement pages."
        )
    if not regions:
        raise BidScopeInputError("At least one measurement region is required.")
    region_ids = [str(region.get("region_id") or "").strip() for region in regions]
    if any(not region_id for region_id in region_ids):
        raise BidScopeInputError("Every measurement region requires a region_id.")
    if len(region_ids) != len(set(region_ids)):
        raise BidScopeInputError("Measurement region_id values must be unique.")

    page_by_id = {
        str(page.get("page_id") or ""): page for page in context.get("pages") or []
    }
    missing_pages = sorted(
        {
            str(region.get("page_id") or "")
            for region in regions
            if str(region.get("page_id") or "") not in page_by_id
        }
    )
    if missing_pages:
        raise BidScopeInputError(
            "Unknown measurement page_id: " + ", ".join(missing_pages[:3])
        )

    context_path = _context_path(artifact_dir, measurement_context_id)
    traced: list[dict[str, Any]] = []
    warnings: list[str] = []
    overlays_by_page: dict[str, tuple[Image.Image, list[dict[str, Any]]]] = {}
    active_segmenter = segmenter
    for region in regions:
        page_id = str(region.get("page_id") or "")
        page = page_by_id[page_id]
        calibration = dict(page.get("scale_calibration") or {})
        if calibration.get("status") != "confirmed":
            raise BidScopeInputError(
                f"Page {page.get('sheet_id') or page_id} requires an estimator-confirmed scale before tracing."
            )
        pixels_per_foot = float(calibration.get("rendered_pixels_per_foot") or 0)
        if pixels_per_foot <= 0:
            raise BidScopeInputError(
                f"Page {page.get('sheet_id') or page_id} has no usable scale calibration."
            )
        image_path = context_path / str(page.get("rendered_image_asset_name") or "")
        if not image_path.is_file():
            raise BidScopeUnavailableError(
                f"Tracing image is unavailable for {page.get('sheet_id') or page_id}."
            )
        with Image.open(image_path) as source:
            source_image = source.convert("RGB")
        normalized_polygon = _normalized_polygon(region.get("polygon") or [])
        if normalized_polygon:
            sections = [
                section_from_polygon(
                    str(region["region_id"]),
                    _normalized_to_pixels(normalized_polygon, source_image.size),
                )
            ]
            trace_method = "assistant_supplied_polygon"
            model_name = "none"
            model_version = "none"
            model_score = None
        else:
            positive = _normalized_points(
                region.get("positive_points") or [],
                field_name="positive_points",
            )
            negative = _normalized_points(
                region.get("negative_points") or [],
                field_name="negative_points",
            )
            box = _normalized_box(region.get("box"))
            if not positive and box is None:
                raise BidScopeInputError(
                    f"Region {region['region_id']} requires positive_points, a box, or an explicit polygon."
                )
            inference_image, scale_x, scale_y = _inference_image(
                source_image,
                max_side=inference_max_side,
            )
            positive_pixels = _normalized_to_pixels(positive, inference_image.size)
            negative_pixels = _normalized_to_pixels(negative, inference_image.size)
            box_pixels = _box_to_pixels(box, inference_image.size) if box else None
            if active_segmenter is None:
                if not str(sam2_url or "").strip():
                    raise BidScopeUnavailableError("SAM2 drawing segmentation is not configured.")
                active_segmenter = Sam2RoofSegmenter(
                    url=sam2_url,
                    api_key=sam2_api_key,
                    timeout_seconds=timeout_seconds,
                )
            segmentation = active_segmenter.segment(
                np.asarray(inference_image),
                SegmentationPrompts(
                    positive_points=positive_pixels,
                    negative_points=negative_pixels,
                    box=box_pixels,
                ),
            )
            candidate = _choose_candidate(
                segmentation.candidates,
                positive_points=positive_pixels,
                negative_points=negative_pixels,
                box=box_pixels,
                expected_shape=(inference_image.height, inference_image.width),
            )
            minimum_area = max(100.0, inference_image.width * inference_image.height * 0.00015)
            inference_sections = sections_from_mask(
                candidate.mask,
                simplification_tolerance=2.5,
                minimum_section_area_pixels=minimum_area,
                edge_snap_strength=0.0,
            )
            if not inference_sections:
                raise BidScopeUnavailableError(
                    f"SAM2 returned no usable closed region for {region['region_id']}."
                )
            sections = _sections_to_source(
                inference_sections,
                region_id=str(region["region_id"]),
                scale_x=scale_x,
                scale_y=scale_y,
                architectural_cleanup=bool(region.get("architectural_cleanup", True)),
                pixels_per_foot=pixels_per_foot,
            )
            trace_method = "sam2_prompted_region"
            model_name = segmentation.model_name
            model_version = segmentation.model_version
            model_score = round(float(candidate.score), 4)
            warnings.extend(segmentation.warnings)

        measurement = _measure_region(
            sections=sections,
            pixels_per_foot=pixels_per_foot,
            measurement_type=str(region.get("measurement_type") or "area"),
            height_ft=region.get("height_ft"),
            opening_deduction_sqft=float(region.get("opening_deduction_sqft") or 0),
        )
        public_sections = [
            {
                "section_id": section.section_id,
                "polygon": _normalized_records(section.polygon, source_image.size),
                "holes": [
                    _normalized_records(hole, source_image.size) for hole in section.holes
                ],
            }
            for section in sections
        ]
        trace_id = hashlib.sha256(
            f"{measurement_context_id}:{region['region_id']}:{public_sections}".encode("utf-8")
        ).hexdigest()[:16]
        result = {
            "trace_id": f"trace-{trace_id}",
            "region_id": str(region["region_id"]),
            "label": str(region.get("label") or region["region_id"]),
            "page_id": page_id,
            "sheet_id": str(page.get("sheet_id") or ""),
            "measurement_type": str(region.get("measurement_type") or "area"),
            "trace_method": trace_method,
            "model_name": model_name,
            "model_version": model_version,
            "model_score": model_score,
            "scale_calibration": calibration,
            "measurements": measurement,
            "sections": public_sections,
            "review_status": "requires_estimator_verification",
        }
        traced.append(result)
        page_overlay = overlays_by_page.setdefault(page_id, (source_image, []))
        page_overlay[1].append({"result": result, "sections": sections})

    overlay_bytes = _build_overlay_contact_sheet(overlays_by_page, page_by_id)
    overlay_name = "bidscope_traced_regions.jpg"
    (context_path / overlay_name).write_bytes(overlay_bytes)
    prior_traces = {
        str(item.get("region_id") or ""): item for item in context.get("traces") or []
    }
    for item in traced:
        prior_traces[item["region_id"]] = item
    context["traces"] = list(prior_traces.values())
    context["trace_overlay_asset_name"] = overlay_name
    _write_json_atomic(context_path / "context.json", context)

    warnings.extend(
        [
            "SAM2 is a prompted boundary proposal, not proof that the selected region matches the bid scope.",
            "Boundary length is the complete closed-region perimeter; estimator review must exclude edges that are not in scope.",
            "Returned normalized polygon vertices can be corrected and resubmitted as an explicit polygon.",
        ]
    )
    return {
        "schema_version": "spraytec.bidscope_region_trace.v1",
        "measurement_context_id": measurement_context_id,
        "trace_count": len(traced),
        "traces": traced,
        "review_status": "requires_estimator_verification",
        "segmentation_status": "completed" if any(item["trace_method"].startswith("sam2") for item in traced) else "not_requested",
        "warnings": list(dict.fromkeys(warnings)),
        "openaiFileResponse": [
            {
                "name": overlay_name,
                "mime_type": "image/jpeg",
                "content": base64.b64encode(overlay_bytes).decode("ascii"),
            }
        ],
    }


def _normalized_points(value: Any, *, field_name: str) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for raw in value or []:
        if isinstance(raw, dict):
            x, y = raw.get("x"), raw.get("y")
        elif isinstance(raw, (list, tuple)) and len(raw) == 2:
            x, y = raw
        else:
            raise BidScopeInputError(f"{field_name} must contain x/y coordinate pairs.")
        point = (float(x), float(y))
        if not all(0 <= coordinate <= 1 for coordinate in point):
            raise BidScopeInputError(f"{field_name} coordinates must be normalized from 0 to 1.")
        points.append(point)
    return points


def _normalized_polygon(value: Any) -> list[tuple[float, float]]:
    if not value:
        return []
    points = _normalized_points(value, field_name="polygon")
    if len(points) < 3:
        raise BidScopeInputError("An explicit polygon requires at least three vertices.")
    return points


def _normalized_box(value: Any) -> tuple[float, float, float, float] | None:
    if not value:
        return None
    if isinstance(value, dict):
        box = (
            float(value.get("x_min")),
            float(value.get("y_min")),
            float(value.get("x_max")),
            float(value.get("y_max")),
        )
    elif isinstance(value, (list, tuple)) and len(value) == 4:
        box = tuple(float(item) for item in value)
    else:
        raise BidScopeInputError("box must contain x_min, y_min, x_max, and y_max.")
    if not all(0 <= coordinate <= 1 for coordinate in box):
        raise BidScopeInputError("box coordinates must be normalized from 0 to 1.")
    if box[0] >= box[2] or box[1] >= box[3]:
        raise BidScopeInputError("box minimum coordinates must be less than maximum coordinates.")
    return box


def _normalized_to_pixels(
    points: list[tuple[float, float]], size: tuple[int, int]
) -> list[tuple[float, float]]:
    width, height = size
    return [(x * max(width - 1, 1), y * max(height - 1, 1)) for x, y in points]


def _box_to_pixels(
    box: tuple[float, float, float, float], size: tuple[int, int]
) -> tuple[float, float, float, float]:
    points = _normalized_to_pixels([(box[0], box[1]), (box[2], box[3])], size)
    return points[0][0], points[0][1], points[1][0], points[1][1]


def _inference_image(
    image: Image.Image, *, max_side: int
) -> tuple[Image.Image, float, float]:
    bounded = max(512, min(int(max_side), 2400))
    if max(image.size) <= bounded:
        return image.copy(), 1.0, 1.0
    output = image.copy()
    output.thumbnail((bounded, bounded), Image.Resampling.LANCZOS)
    return output, output.width / image.width, output.height / image.height


def _choose_candidate(
    candidates: list[Any],
    *,
    positive_points: list[tuple[float, float]],
    negative_points: list[tuple[float, float]],
    box: tuple[float, float, float, float] | None,
    expected_shape: tuple[int, int],
) -> Any:
    ranked: list[tuple[float, Any]] = []
    for candidate in candidates:
        mask = np.asarray(candidate.mask, dtype=bool)
        if mask.shape != expected_shape:
            continue
        positive_hit = _point_hit_fraction(mask, positive_points, expected=True)
        negative_clear = _point_hit_fraction(mask, negative_points, expected=False)
        outside_fraction = _outside_box_fraction(mask, box)
        score = (
            float(candidate.score) * 0.5
            + positive_hit * 0.35
            + negative_clear * 0.15
            - outside_fraction * 0.25
        )
        ranked.append((score, candidate))
    if not ranked:
        raise BidScopeUnavailableError("SAM2 returned no drawing-region candidates.")
    ranked.sort(key=lambda item: item[0], reverse=True)
    selected = ranked[0][1]
    if positive_points and _point_hit_fraction(
        np.asarray(selected.mask, dtype=bool), positive_points, expected=True
    ) < 0.5:
        raise BidScopeUnavailableError(
            "No SAM2 candidate contained enough of the requested positive points."
        )
    return selected


def _point_hit_fraction(
    mask: np.ndarray,
    points: list[tuple[float, float]],
    *,
    expected: bool,
) -> float:
    if not points:
        return 1.0
    height, width = mask.shape[:2]
    matches = 0
    for x, y in points:
        ix = min(max(int(round(x)), 0), width - 1)
        iy = min(max(int(round(y)), 0), height - 1)
        matches += bool(mask[iy, ix]) is expected
    return matches / len(points)


def _outside_box_fraction(
    mask: np.ndarray,
    box: tuple[float, float, float, float] | None,
) -> float:
    total = int(mask.sum())
    if box is None or total == 0:
        return 0.0
    x0, y0, x1, y1 = box
    bounded = np.zeros(mask.shape, dtype=bool)
    bounded[
        max(0, int(math.floor(y0))) : min(mask.shape[0], int(math.ceil(y1)) + 1),
        max(0, int(math.floor(x0))) : min(mask.shape[1], int(math.ceil(x1)) + 1),
    ] = True
    return float((mask & ~bounded).sum()) / total


def _sections_to_source(
    sections: list[RoofSection],
    *,
    region_id: str,
    scale_x: float,
    scale_y: float,
    architectural_cleanup: bool,
    pixels_per_foot: float,
) -> list[RoofSection]:
    output: list[RoofSection] = []
    for index, section in enumerate(sections, start=1):
        polygon = [(x / scale_x, y / scale_y) for x, y in section.polygon]
        holes = [
            [(x / scale_x, y / scale_y) for x, y in hole]
            for hole in section.holes
        ]
        if architectural_cleanup:
            polygon = straighten_architectural_ring(
                polygon,
                simplification_tolerance=max(3.0, min(14.0, pixels_per_foot * 0.5)),
                angle_tolerance_degrees=18.0,
                max_area_drift=0.03,
            )
        output.append(section_from_polygon(f"{region_id}-{index}", polygon, holes=holes))
    return output


def _measure_region(
    *,
    sections: list[RoofSection],
    pixels_per_foot: float,
    measurement_type: str,
    height_ft: Any,
    opening_deduction_sqft: float,
) -> dict[str, Any]:
    if measurement_type not in {"area", "boundary_length", "wall_area"}:
        raise BidScopeInputError(
            "measurement_type must be area, boundary_length, or wall_area."
        )
    area_pixels = sum(
        polygon_area_pixels(section.polygon, section.holes) for section in sections
    )
    for section in sections:
        if _ring_self_intersects(section.polygon) or any(
            _ring_self_intersects(hole) for hole in section.holes
        ):
            raise BidScopeInputError("A traced region contains a self-intersecting polygon.")
    perimeter_pixels = sum(
        polygon_perimeter_pixels(section.polygon, section.holes) for section in sections
    )
    if area_pixels <= 0 or perimeter_pixels <= 0:
        raise BidScopeInputError("A traced region must enclose a non-zero measurable area.")
    area_sqft = area_pixels / (pixels_per_foot**2)
    perimeter_ft = perimeter_pixels / pixels_per_foot
    result: dict[str, Any] = {
        "enclosed_area_sqft": round(area_sqft, 1),
        "closed_boundary_length_ft": round(perimeter_ft, 1),
    }
    if measurement_type == "area":
        result["quantity"] = round(area_sqft, 1)
        result["unit"] = "sqft"
    elif measurement_type == "boundary_length":
        result["quantity"] = round(perimeter_ft, 1)
        result["unit"] = "linear_ft"
    else:
        if height_ft is None or float(height_ft) <= 0:
            raise BidScopeInputError("wall_area regions require a positive height_ft.")
        if opening_deduction_sqft < 0:
            raise BidScopeInputError("opening_deduction_sqft cannot be negative.")
        gross = perimeter_ft * float(height_ft)
        net = gross - opening_deduction_sqft
        if net <= 0:
            raise BidScopeInputError(
                "opening_deduction_sqft must be less than the gross wall area."
            )
        result.update(
            {
                "height_ft": round(float(height_ft), 2),
                "gross_wall_area_sqft": round(gross, 1),
                "opening_deduction_sqft": round(opening_deduction_sqft, 1),
                "quantity": round(net, 1),
                "unit": "sqft",
            }
        )
    return result


def _normalized_records(
    points: list[tuple[float, float]], size: tuple[int, int]
) -> list[dict[str, float]]:
    width, height = size
    return [
        {
            "x": round(float(x) / max(width - 1, 1), 7),
            "y": round(float(y) / max(height - 1, 1), 7),
        }
        for x, y in points
    ]


def _build_overlay_contact_sheet(
    overlays_by_page: dict[str, tuple[Image.Image, list[dict[str, Any]]]],
    page_by_id: dict[str, dict[str, Any]],
) -> bytes:
    rendered: list[Image.Image] = []
    for page_id, (image, region_rows) in overlays_by_page.items():
        overlay = image.convert("RGBA")
        drawing = Image.new("RGBA", overlay.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(drawing)
        for index, row in enumerate(region_rows):
            color = _COLORS[index % len(_COLORS)]
            for section in row["sections"]:
                polygon = [(float(x), float(y)) for x, y in section.polygon]
                if len(polygon) < 3:
                    continue
                draw.polygon(polygon, fill=(*color, 55))
                draw.line(polygon, fill=(*color, 255), width=6, joint="curve")
                draw.text(
                    polygon[0],
                    str(row["result"]["label"]),
                    fill=(255, 255, 255, 255),
                    stroke_width=3,
                    stroke_fill=(0, 0, 0, 255),
                )
        composite = Image.alpha_composite(overlay, drawing).convert("RGB")
        composite.thumbnail((4000, 4000), Image.Resampling.LANCZOS)
        header_height = 44
        framed = Image.new("RGB", (composite.width, composite.height + header_height), "white")
        framed.paste(composite, (0, header_height))
        header = ImageDraw.Draw(framed)
        page = page_by_id[page_id]
        header.text(
            (14, 13),
            f"{page.get('sheet_id') or page_id} | estimator review required",
            fill="black",
        )
        rendered.append(framed)
    width = max(image.width for image in rendered)
    height = sum(image.height for image in rendered)
    contact = Image.new("RGB", (width, height), "white")
    offset = 0
    for image in rendered:
        contact.paste(image, (0, offset))
        offset += image.height
    contact.thumbnail((4000, 8000), Image.Resampling.LANCZOS)
    for max_size, quality in (((4000, 8000), 84), ((3200, 6400), 76), ((2400, 4800), 68)):
        bounded = contact.copy()
        bounded.thumbnail(max_size, Image.Resampling.LANCZOS)
        buffer = BytesIO()
        bounded.save(
            buffer,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=True,
        )
        payload = buffer.getvalue()
        if len(payload) <= _MAX_ATTACHMENT_BYTES:
            return payload
    raise BidScopeUnavailableError(
        "The traced-region overlay could not be compressed within the Assistant attachment limit. Trace fewer pages and retry."
    )
