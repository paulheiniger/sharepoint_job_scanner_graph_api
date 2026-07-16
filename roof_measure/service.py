from __future__ import annotations

import uuid
from dataclasses import dataclass

import numpy as np

from .calibration import clicked_known_length_calibration, detect_google_earth_scale_bar, feet_from_pixels, sqft_from_pixels
from .confidence import area_uncertainty_factor, confidence_components, measurement_warnings
from .geometry import polygon_area_pixels, polygon_perimeter_pixels, repair_polygon
from .image_io import LoadedImage, image_to_array, load_image_bytes
from .models import CalibrationResult, MeasurementReport, MeasurementWarning, RoofMeasurement, RoofMeasureRequest, RoofSection
from .polygonize import sections_from_mask
from .segmentation import RoofSegmenter, SegmentationPrompts, choose_segmenter


@dataclass
class RoofMeasureResult:
    report: MeasurementReport
    selected_mask: np.ndarray | None
    candidate_count: int


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
    sections = []
    segmentation_score = 0.0
    if candidates:
        selected_index = min(max(selected_candidate_index, 0), len(candidates) - 1)
        candidate = candidates[selected_index]
        selected_mask = candidate.mask
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
    return RoofMeasureResult(report=report, selected_mask=selected_mask, candidate_count=len(candidates))


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
