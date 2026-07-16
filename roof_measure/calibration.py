from __future__ import annotations

import math
import re

import numpy as np
from PIL import Image

from .models import CalibrationResult, Point


SCALE_LABEL_RE = re.compile(
    r"(?P<value>\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<unit>ft|feet|foot|mi|mile|miles|m|meter|meters|km|kilometer|kilometers)\b",
    re.IGNORECASE,
)


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


def detect_google_earth_scale_bar(
    image: Image.Image,
    *,
    label_hint: str | None = None,
    crop_fraction: float = 0.35,
) -> CalibrationResult:
    label_text = label_hint or _ocr_scale_text(image, crop_fraction=crop_fraction)
    length_feet = parse_scale_label_feet(label_text or "")
    if not length_feet:
        return CalibrationResult(
            calibration_type="none",
            confidence="none",
            warning="Could not read a scale label from the uploaded image.",
        )
    bar = detect_horizontal_scale_bar_pixels(image, crop_fraction=crop_fraction)
    if bar is None:
        return CalibrationResult(
            calibration_type="none",
            confidence="none",
            warning=f"Read scale label as {length_feet:g} ft, but could not find the horizontal scale bar.",
        )
    point_a, point_b, pixel_distance = bar
    confidence = "medium" if label_hint else "low"
    return CalibrationResult(
        calibration_type="scale_bar",
        length_feet=length_feet,
        point_a=point_a,
        point_b=point_b,
        pixel_distance=pixel_distance,
        pixels_per_foot=pixel_distance / length_feet,
        confidence=confidence,
        warning=(
            "Auto-calibrated from the image scale bar. Verify against a known roof dimension before final use."
            if not label_hint
            else "Calibrated from detected scale bar pixels using the supplied scale label hint."
        ),
    )


def parse_scale_label_feet(text: str) -> float | None:
    match = SCALE_LABEL_RE.search(text or "")
    if not match:
        return None
    value = float(match.group("value").replace(",", ""))
    unit = match.group("unit").lower()
    if unit in {"ft", "feet", "foot"}:
        return value
    if unit in {"mi", "mile", "miles"}:
        return value * 5280.0
    if unit in {"m", "meter", "meters"}:
        return value * 3.280839895
    if unit in {"km", "kilometer", "kilometers"}:
        return value * 3280.839895
    return None


def detect_horizontal_scale_bar_pixels(
    image: Image.Image,
    *,
    crop_fraction: float = 0.35,
) -> tuple[Point, Point, float] | None:
    crop, x_offset, y_offset = _bottom_left_crop(image, crop_fraction=crop_fraction)
    gray = np.asarray(crop.convert("L"))
    candidates = [
        *_horizontal_bar_candidates(gray < 70),
        *_horizontal_bar_candidates(gray > 235),
    ]
    if not candidates:
        return None
    x0, y0, x1, y1 = max(candidates, key=lambda item: item[2] - item[0])
    y = y_offset + (y0 + y1) / 2
    point_a = (float(x_offset + x0), float(y))
    point_b = (float(x_offset + x1), float(y))
    pixel_distance = float(x1 - x0)
    if pixel_distance <= 0:
        return None
    return point_a, point_b, pixel_distance


def _bottom_left_crop(image: Image.Image, *, crop_fraction: float) -> tuple[Image.Image, int, int]:
    width, height = image.size
    crop_width = max(1, int(width * 0.65))
    crop_height = max(1, int(height * max(0.1, min(crop_fraction, 0.8))))
    y0 = max(0, height - crop_height)
    return image.crop((0, y0, crop_width, height)), 0, y0


def _horizontal_bar_candidates(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
    height, width = mask.shape
    visited = np.zeros(mask.shape, dtype=bool)
    candidates: list[tuple[int, int, int, int]] = []
    for y in range(height):
        for x in range(width):
            if not mask[y, x] or visited[y, x]:
                continue
            stack = [(y, x)]
            visited[y, x] = True
            min_x = max_x = x
            min_y = max_y = y
            pixel_count = 0
            while stack:
                cy, cx = stack.pop()
                pixel_count += 1
                min_x = min(min_x, cx)
                max_x = max(max_x, cx)
                min_y = min(min_y, cy)
                max_y = max(max_y, cy)
                for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                    if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = True
                        stack.append((ny, nx))
            component_width = max_x - min_x + 1
            component_height = max_y - min_y + 1
            if component_width < 30:
                continue
            if component_height > max(16, component_width * 0.18):
                continue
            density = pixel_count / max(component_width * component_height, 1)
            if density < 0.12:
                continue
            candidates.append((min_x, min_y, max_x + 1, max_y + 1))
    return candidates


def _ocr_scale_text(image: Image.Image, *, crop_fraction: float) -> str:
    try:
        import pytesseract
    except ImportError:
        return ""
    crop, _, _ = _bottom_left_crop(image, crop_fraction=crop_fraction)
    try:
        return str(pytesseract.image_to_string(crop, config="--psm 6") or "")
    except Exception:
        return ""
