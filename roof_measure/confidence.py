from __future__ import annotations

from .models import CalibrationResult, ImageMetadata, MeasurementWarning, RoofSection


def confidence_components(
    *,
    calibration: CalibrationResult,
    sections: list[RoofSection],
    image_metadata: ImageMetadata,
    segmentation_score: float,
    registration_confidence: float = 0.0,
) -> dict[str, float]:
    calibration_score = {
        "high": 0.9,
        "medium": 0.72,
        "low": 0.45,
        "none": 0.0,
    }.get(calibration.confidence, 0.0)
    polygon_quality = 0.0 if not sections else min(0.85, sum(section.confidence for section in sections) / len(sections))
    image_quality = 0.85
    if "low_resolution" in image_metadata.quality_flags:
        image_quality -= 0.25
    if "very_dark" in image_metadata.quality_flags or "very_bright" in image_metadata.quality_flags:
        image_quality -= 0.2
    image_quality = max(0.1, image_quality)
    overall = round(
        calibration_score * 0.35
        + max(0.0, min(segmentation_score, 1.0)) * 0.25
        + polygon_quality * 0.2
        + image_quality * 0.15
        + registration_confidence * 0.05,
        3,
    )
    return {
        "segmentation": round(max(0.0, min(segmentation_score, 1.0)), 3),
        "calibration": round(calibration_score, 3),
        "polygon_quality": round(polygon_quality, 3),
        "image_quality": round(image_quality, 3),
        "registration": round(registration_confidence, 3),
        "overall_estimating": overall,
    }


def measurement_warnings(
    *,
    calibration: CalibrationResult,
    sections: list[RoofSection],
    image_metadata: ImageMetadata,
    segmenter_warnings: list[str] | None = None,
) -> list[MeasurementWarning]:
    warnings: list[MeasurementWarning] = []
    if not calibration.pixels_per_foot:
        warnings.append(
            MeasurementWarning(
                code="missing_calibration",
                message="No valid pixel-to-feet calibration was supplied, so area and perimeter are unavailable.",
                severity="error",
            )
        )
    elif calibration.confidence != "high":
        warnings.append(
            MeasurementWarning(
                code="calibration_review",
                message="Calibration is not high-confidence; verify the clicked length before using the area.",
            )
        )
    if not sections:
        warnings.append(
            MeasurementWarning(
                code="no_roof_sections",
                message="No roof section polygons were produced from the selected mask.",
                severity="error",
            )
        )
    if image_metadata.quality_flags:
        warnings.append(
            MeasurementWarning(
                code="image_quality",
                message="Image quality flags: " + ", ".join(image_metadata.quality_flags),
            )
        )
    for warning in segmenter_warnings or []:
        warnings.append(MeasurementWarning(code="segmentation_provider", message=warning))
    return warnings


def area_uncertainty_factor(confidence: dict[str, float]) -> float:
    overall = float(confidence.get("overall_estimating") or 0.0)
    if overall >= 0.8:
        return 0.06
    if overall >= 0.65:
        return 0.1
    if overall >= 0.45:
        return 0.18
    return 0.3

