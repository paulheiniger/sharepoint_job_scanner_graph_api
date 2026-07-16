from __future__ import annotations

import math

from .models import CalibrationResult, Point


def point_distance(a: Point, b: Point) -> float:
    return math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1]))


def clicked_known_length_calibration(
    *,
    point_a: Point | None,
    point_b: Point | None,
    length_feet: float | None,
    calibration_type: str = "clicked_known_length",
) -> CalibrationResult:
    if point_a is None or point_b is None or not length_feet or length_feet <= 0:
        return CalibrationResult(
            calibration_type="none",
            confidence="none",
            warning="Measurement unavailable until a known length and two calibration points are supplied.",
        )
    pixel_distance = point_distance(point_a, point_b)
    if pixel_distance <= 0:
        return CalibrationResult(
            calibration_type="none",
            confidence="none",
            warning="Calibration points must be different.",
        )
    pixels_per_foot = pixel_distance / float(length_feet)
    confidence = "high" if length_feet >= 10 else "medium"
    return CalibrationResult(
        calibration_type=calibration_type,  # type: ignore[arg-type]
        length_feet=float(length_feet),
        point_a=point_a,
        point_b=point_b,
        pixel_distance=pixel_distance,
        pixels_per_foot=pixels_per_foot,
        confidence=confidence,
    )


def sqft_from_pixels(area_pixels: float, pixels_per_foot: float | None) -> float | None:
    if not pixels_per_foot or pixels_per_foot <= 0:
        return None
    return float(area_pixels) / (float(pixels_per_foot) ** 2)


def feet_from_pixels(distance_pixels: float, pixels_per_foot: float | None) -> float | None:
    if not pixels_per_foot or pixels_per_foot <= 0:
        return None
    return float(distance_pixels) / float(pixels_per_foot)

