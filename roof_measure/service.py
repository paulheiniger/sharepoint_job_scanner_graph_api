from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw

from .calibration import clicked_known_length_calibration, detect_google_earth_scale_bar, feet_from_pixels, sqft_from_pixels
from .confidence import area_uncertainty_factor, confidence_components, measurement_warnings
from .geometry import polygon_area_pixels, polygon_perimeter_pixels, repair_polygon
from .image_io import LoadedImage, image_to_array, load_image_bytes
from .models import CalibrationResult, MeasurementReport, MeasurementWarning, RoofMeasurement, RoofMeasureRequest, RoofSection
from .polygonize import section_from_polygon, sections_from_mask
from .segmentation import RoofSegmenter, SegmentationPrompts, choose_segmenter


@dataclass
class RoofMeasureResult:
    report: MeasurementReport
    selected_mask: np.ndarray | None
    candidate_count: int
    applied_footprint_polygons: list[list[tuple[float, float]]] = field(default_factory=list)
    footprint_buffer_pixels: int = 0
    footprint_audit: list[dict[str, object]] = field(default_factory=list)
    deterministic_score: float = 0.0


def measure_roof_from_overhead_image(
    *,
    image_bytes: bytes,
    request: RoofMeasureRequest,
    segmenter: RoofSegmenter | None = None,
    selected_candidate_index: int = 0,
    storage_root: str = "output/roof_measure_uploads",
) -> RoofMeasureResult:
    loaded = load_image_bytes(image_bytes, file_name=request.overhead_image_name, storage_root=storage_root)
    segmenter = segmenter or choose_segmenter(request.segmenter_name)
    prompts = SegmentationPrompts(
        positive_points=request.positive_points,
        negative_points=request.negative_points,
    )
    try:
        segmentation = segmenter.segment(image_to_array(loaded.inference_image), prompts)
    except Exception as exc:
        fallback = choose_segmenter("manual_fallback")
        segmentation = fallback.segment(image_to_array(loaded.inference_image), prompts)
        segmentation.warnings.append(f"Requested segmenter failed: {type(exc).__name__}: {exc}")
    candidates = segmentation.candidates
    selected_mask = None
    applied_footprint_polygons: list[list[tuple[float, float]]] = []
    footprint_buffer_pixels = 0
    sections = []
    segmentation_score = 0.0
    if candidates:
        selected_index = min(max(selected_candidate_index, 0), len(candidates) - 1)
        candidate = candidates[selected_index]
        selected_mask = candidate.mask
        if request.footprint_polygons:
            buffer_pixels = _footprint_buffer_pixels(request)
            footprint_buffer_pixels = buffer_pixels
            constrained_mask = _constrain_mask_to_footprints(
                selected_mask,
                request.footprint_polygons,
                buffer_pixels=buffer_pixels,
            )
            if constrained_mask.any():
                retained_fraction = float(constrained_mask.sum()) / max(float(selected_mask.sum()), 1.0)
                selected_mask = constrained_mask
                applied_footprint_polygons = request.footprint_polygons
                segmentation.warnings.append(
                    "Segmentation constrained to selected building footprint(s) "
                    f"with a {float(request.footprint_buffer_feet):g} ft buffer; retained {retained_fraction:.0%} of mask pixels."
                )
            else:
                segmentation.warnings.append("Selected building footprint(s) did not overlap the segmentation mask; original mask retained.")
        segmentation_score = float(candidate.score)
        sections = sections_from_mask(
            selected_mask,
            simplification_tolerance=request.simplification_tolerance,
            minimum_section_area_pixels=request.minimum_section_area_pixels,
            edge_snap_strength=request.edge_snap_strength,
        )
    calibration = _metadata_calibration(request.metadata_pixels_per_foot)
    if not calibration.pixels_per_foot:
        calibration = clicked_known_length_calibration(
            point_a=request.calibration_point_a,
            point_b=request.calibration_point_b,
            length_feet=request.calibration_length_feet,
        )
    if not calibration.pixels_per_foot:
        calibration = detect_google_earth_scale_bar(
            loaded.inference_image,
            label_hint=request.scale_bar_label_hint,
            use_ai_fallback=request.use_ai_scale_reader,
        )
    for section in sections:
        section.area_sqft = sqft_from_pixels(section.area_pixels, calibration.pixels_per_foot)
        section.perimeter_ft = feet_from_pixels(section.perimeter_pixels, calibration.pixels_per_foot)
    total_area = _sum_optional([section.area_sqft for section in sections])
    total_perimeter = _sum_optional([section.perimeter_ft for section in sections])
    confidence = confidence_components(
        calibration=calibration,
        sections=sections,
        image_metadata=loaded.metadata,
        segmentation_score=segmentation_score,
    )
    uncertainty_factor = area_uncertainty_factor(confidence)
    warnings = measurement_warnings(
        calibration=calibration,
        sections=sections,
        image_metadata=loaded.metadata,
        segmentation_score=segmentation_score,
        segmenter_warnings=segmentation.warnings,
    )
    measurement = RoofMeasurement(
        total_area_sqft=None if total_area is None else round(total_area, 2),
        total_perimeter_ft=None if total_perimeter is None else round(total_perimeter, 2),
        low_area_sqft=None if total_area is None else round(total_area * (1 - uncertainty_factor), 2),
        high_area_sqft=None if total_area is None else round(total_area * (1 + uncertainty_factor), 2),
        sections=sections,
        calibration=calibration,
        confidence=confidence,
        warnings=warnings,
        assumptions=[
            "Area is calculated in plan view from the uploaded overhead image.",
            "Oblique images are advisory only in Milestone 1 and are not used for primary measurement.",
            "This is an estimating-assistance measurement, not a survey-grade measurement.",
        ],
    )
    report = MeasurementReport(
        id=f"roof-measure-{uuid.uuid4().hex[:16]}",
        address=request.address,
        job_id=request.job_id,
        source_images=[loaded.metadata],
        calibration_method=calibration.calibration_type,
        pixels_per_foot=calibration.pixels_per_foot,
        measurement=measurement,
        model_name=segmentation.model_name,
        model_version=segmentation.model_version,
    )
    return RoofMeasureResult(
        report=report,
        selected_mask=selected_mask,
        candidate_count=len(candidates),
        applied_footprint_polygons=applied_footprint_polygons,
        footprint_buffer_pixels=footprint_buffer_pixels,
        footprint_audit=_applied_footprint_audit(request, applied_footprint_polygons, footprint_buffer_pixels),
        deterministic_score=score_roof_result(selected_mask, sections, request.footprint_polygons),
    )


def _constrain_mask_to_footprints(mask: np.ndarray, polygons: list[list[tuple[float, float]]], *, buffer_pixels: int = 8) -> np.ndarray:
    footprint_mask = footprint_constraint_mask(mask.shape[:2], polygons, buffer_pixels=buffer_pixels)
    return np.asarray(mask, dtype=bool) & footprint_mask


def _footprint_buffer_pixels(request: RoofMeasureRequest) -> int:
    feet = max(0.0, min(float(request.footprint_buffer_feet), 30.0))
    pixels_per_foot = float(request.metadata_pixels_per_foot or 0)
    if pixels_per_foot > 0:
        return max(0, int(round(feet * pixels_per_foot)))
    return 8


def footprint_constraint_mask(
    shape: tuple[int, int],
    polygons: list[list[tuple[float, float]]],
    *,
    buffer_pixels: int = 0,
) -> np.ndarray:
    height, width = shape
    footprint_image = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(footprint_image)
    for polygon in polygons:
        if len(polygon) >= 3:
            draw.polygon([(float(x), float(y)) for x, y in polygon], fill=255)
    footprint_mask = np.asarray(footprint_image, dtype=bool)
    return _dilate_mask(footprint_mask, radius=buffer_pixels) if buffer_pixels > 0 else footprint_mask


def _applied_footprint_audit(
    request: RoofMeasureRequest,
    applied_polygons: list[list[tuple[float, float]]],
    buffer_pixels: int,
) -> list[dict[str, object]]:
    if not applied_polygons:
        return []
    audit: list[dict[str, object]] = []
    for source in request.footprint_source_records:
        image_polygons = source.get("image_polygons") if isinstance(source, dict) else None
        if not isinstance(image_polygons, list):
            continue
        if any(_same_ring(candidate, applied) for candidate in image_polygons for applied in applied_polygons):
            audit.append({
                "footprint_id": str(source.get("footprint_id") or ""),
                "label": str(source.get("label") or ""),
                "provider": str(source.get("provider") or ""),
                "attribution": str(source.get("attribution") or ""),
                "geographic_rings": source.get("geographic_rings") or [],
                "image_polygons": image_polygons,
                "buffer_pixels": buffer_pixels,
                "buffer_feet": request.footprint_buffer_feet,
            })
    return audit


def _same_ring(first: object, second: list[tuple[float, float]]) -> bool:
    if not isinstance(first, list) or len(first) != len(second):
        return False
    try:
        return all(abs(float(a[0]) - float(b[0])) < 0.01 and abs(float(a[1]) - float(b[1])) < 0.01 for a, b in zip(first, second))
    except (TypeError, ValueError, IndexError):
        return False


def _dilate_mask(mask: np.ndarray, *, radius: int) -> np.ndarray:
    expanded = np.asarray(mask, dtype=bool).copy()
    for _ in range(max(0, int(radius))):
        padded = np.pad(expanded, 1, mode="constant", constant_values=False)
        expanded = np.zeros_like(expanded)
        for y_offset in range(3):
            for x_offset in range(3):
                expanded |= padded[y_offset : y_offset + mask.shape[0], x_offset : x_offset + mask.shape[1]]
    return expanded


def score_roof_result(
    mask: np.ndarray | None,
    sections: list[RoofSection],
    footprint_polygons: list[list[tuple[float, float]]],
) -> float:
    """Score only deterministic properties used to reject a weaker correction pass."""
    if mask is None or not np.asarray(mask, dtype=bool).any() or not sections:
        return 0.0
    components = len(sections)
    fragmentation = 1.0 / max(components, 1)
    validity = sum(1 for section in sections if len(repair_polygon(section.polygon)) >= 4) / len(sections)
    regularity = sum(_ring_axis_regularity(section.polygon) for section in sections) / len(sections)
    footprint_overlap = 1.0
    if footprint_polygons:
        footprint = _constrain_mask_to_footprints(np.ones_like(mask, dtype=bool), footprint_polygons, buffer_pixels=0)
        footprint_overlap = float((np.asarray(mask, dtype=bool) & footprint).sum()) / max(float(np.asarray(mask, dtype=bool).sum()), 1.0)
    return round(0.35 * footprint_overlap + 0.30 * validity + 0.20 * regularity + 0.15 * fragmentation, 4)


def _ring_axis_regularity(ring: list[tuple[float, float]]) -> float:
    repaired = repair_polygon(ring)
    edges = list(zip(repaired, repaired[1:]))
    if not edges:
        return 0.0
    aligned = 0
    for (x1, y1), (x2, y2) in edges:
        if min(abs(x2 - x1), abs(y2 - y1)) <= max(abs(x2 - x1), abs(y2 - y1)) * 0.25:
            aligned += 1
    return aligned / len(edges)


def measure_roof_from_outline_polygons(
    *,
    image_bytes: bytes,
    request: RoofMeasureRequest,
    polygons: list[list[tuple[float, float]]],
    model_name: str = "openai_roof_outline",
    model_version: str = "",
    outline_confidence: float = 0.65,
    outline_notes: str = "",
    storage_root: str = "output/roof_measure_uploads",
) -> RoofMeasureResult:
    loaded = load_image_bytes(image_bytes, file_name=request.overhead_image_name, storage_root=storage_root)
    sections: list[RoofSection] = []
    for index, polygon in enumerate(polygons, start=1):
        try:
            section = section_from_polygon(f"section-{index}", polygon)
        except Exception:
            continue
        if section.area_pixels <= 0:
            continue
        section.confidence = max(0.0, min(float(outline_confidence or 0), 1.0))
        sections.append(section)

    calibration = _metadata_calibration(request.metadata_pixels_per_foot)
    if not calibration.pixels_per_foot:
        calibration = clicked_known_length_calibration(
            point_a=request.calibration_point_a,
            point_b=request.calibration_point_b,
            length_feet=request.calibration_length_feet,
        )
    if not calibration.pixels_per_foot:
        calibration = detect_google_earth_scale_bar(
            loaded.inference_image,
            label_hint=request.scale_bar_label_hint,
            use_ai_fallback=request.use_ai_scale_reader,
        )
    for section in sections:
        section.area_sqft = sqft_from_pixels(section.area_pixels, calibration.pixels_per_foot)
        section.perimeter_ft = feet_from_pixels(section.perimeter_pixels, calibration.pixels_per_foot)
    total_area = _sum_optional([section.area_sqft for section in sections])
    total_perimeter = _sum_optional([section.perimeter_ft for section in sections])
    confidence = confidence_components(
        calibration=calibration,
        sections=sections,
        image_metadata=loaded.metadata,
        segmentation_score=max(0.0, min(float(outline_confidence or 0), 1.0)),
    )
    uncertainty_factor = area_uncertainty_factor(confidence)
    warnings = measurement_warnings(
        calibration=calibration,
        sections=sections,
        image_metadata=loaded.metadata,
        segmentation_score=max(0.0, min(float(outline_confidence or 0), 1.0)),
        segmenter_warnings=[],
    )
    warnings.append(
        MeasurementWarning(
            code="ai_outline_review",
            message="AI-suggested roof outline must be reviewed and adjusted before final estimating use.",
            severity="info",
        )
    )
    if outline_notes:
        warnings.append(
            MeasurementWarning(
                code="ai_outline_notes",
                message=outline_notes,
                severity="info",
            )
        )
    measurement = RoofMeasurement(
        total_area_sqft=None if total_area is None else round(total_area, 2),
        total_perimeter_ft=None if total_perimeter is None else round(total_perimeter, 2),
        low_area_sqft=None if total_area is None else round(total_area * (1 - uncertainty_factor), 2),
        high_area_sqft=None if total_area is None else round(total_area * (1 + uncertainty_factor), 2),
        sections=sections,
        calibration=calibration,
        confidence=confidence,
        warnings=warnings,
        assumptions=[
            "Area is calculated in plan view from the uploaded overhead image.",
            "Roof polygons were suggested by AI and require estimator review.",
            "This is an estimating-assistance measurement, not a survey-grade measurement.",
        ],
    )
    report = MeasurementReport(
        id=f"roof-measure-{uuid.uuid4().hex[:16]}",
        address=request.address,
        job_id=request.job_id,
        source_images=[loaded.metadata],
        calibration_method=calibration.calibration_type,
        pixels_per_foot=calibration.pixels_per_foot,
        measurement=measurement,
        model_name=model_name,
        model_version=model_version or "roof-measure-ai-outline-v1",
        user_corrections=[
            {
                "type": "ai_outline_seed",
                "note": outline_notes,
                "section_count": len(sections),
            }
        ],
    )
    return RoofMeasureResult(report=report, selected_mask=None, candidate_count=0)


def _sum_optional(values: list[float | None]) -> float | None:
    present = [float(value) for value in values if value is not None]
    if not present:
        return None
    return sum(present)


def _metadata_calibration(pixels_per_foot: float | None) -> CalibrationResult:
    try:
        value = float(pixels_per_foot or 0)
    except (TypeError, ValueError):
        value = 0.0
    if value <= 0:
        return CalibrationResult(calibration_type="none", confidence="none")
    return CalibrationResult(
        calibration_type="metadata",
        pixels_per_foot=value,
        confidence="medium",
        warning=(
            "Calibrated from map imagery metadata. Verify against a known roof dimension before final use."
        ),
    )


def load_overhead_image_for_overlay(image_bytes: bytes, file_name: str) -> LoadedImage:
    return load_image_bytes(image_bytes, file_name=file_name)


def recalculate_report_from_corrected_sections(
    report: MeasurementReport,
    sections: list[RoofSection],
    *,
    correction_note: str = "Estimator corrected roof polygon vertices.",
) -> MeasurementReport:
    calibration = report.measurement.calibration
    corrected_sections: list[RoofSection] = []
    for section in sections:
        corrected = section.model_copy(deep=True)
        corrected.polygon = repair_polygon(corrected.polygon)
        corrected.holes = [repair_polygon(hole) for hole in corrected.holes if repair_polygon(hole)]
        corrected.area_pixels = polygon_area_pixels(corrected.polygon, corrected.holes)
        corrected.perimeter_pixels = polygon_perimeter_pixels(corrected.polygon, corrected.holes)
        corrected.area_sqft = sqft_from_pixels(corrected.area_pixels, calibration.pixels_per_foot)
        corrected.perimeter_ft = feet_from_pixels(corrected.perimeter_pixels, calibration.pixels_per_foot)
        corrected.confidence = max(float(corrected.confidence or 0), 0.75)
        corrected_sections.append(corrected)

    total_area = _sum_optional([section.area_sqft for section in corrected_sections])
    total_perimeter = _sum_optional([section.perimeter_ft for section in corrected_sections])
    confidence = dict(report.measurement.confidence)
    if corrected_sections:
        confidence["polygon_quality"] = round(
            sum(section.confidence for section in corrected_sections) / len(corrected_sections),
            3,
        )
        if calibration.pixels_per_foot:
            confidence["overall_estimating"] = round(min(0.9, max(float(confidence.get("overall_estimating") or 0), 0.72)), 3)
    uncertainty_factor = area_uncertainty_factor(confidence)
    warnings = [
        warning
        for warning in report.measurement.warnings
        if warning.code not in {"no_roof_sections"}
    ]
    warnings.append(
        MeasurementWarning(
            code="manual_polygon_correction",
            message=correction_note,
            severity="info",
        )
    )
    measurement = report.measurement.model_copy(
        deep=True,
        update={
            "total_area_sqft": None if total_area is None else round(total_area, 2),
            "total_perimeter_ft": None if total_perimeter is None else round(total_perimeter, 2),
            "low_area_sqft": None if total_area is None else round(total_area * (1 - uncertainty_factor), 2),
            "high_area_sqft": None if total_area is None else round(total_area * (1 + uncertainty_factor), 2),
            "sections": corrected_sections,
            "confidence": confidence,
            "warnings": warnings,
        },
    )
    return report.model_copy(
        deep=True,
        update={
            "measurement": measurement,
            "user_corrections": [
                *report.user_corrections,
                {
                    "type": "polygon_vertices",
                    "note": correction_note,
                    "section_count": len(corrected_sections),
                },
            ],
        },
    )
