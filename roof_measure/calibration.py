from __future__ import annotations

import base64
import json
import math
import os
import re
from io import BytesIO
from typing import Any, Callable

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
    use_ai_fallback: bool = False,
    ai_provider: Callable[[Image.Image], dict[str, Any]] | None = None,
    crop_fraction: float = 0.35,
) -> CalibrationResult:
    label_text = label_hint or _ocr_scale_text(image, crop_fraction=crop_fraction)
    length_feet = parse_scale_label_feet(label_text or "")
    if not length_feet:
        if use_ai_fallback:
            ai_result = detect_scale_bar_with_ai(image, provider=ai_provider, crop_fraction=crop_fraction)
            if ai_result.pixels_per_foot:
                return ai_result
        return CalibrationResult(
            calibration_type="none",
            confidence="none",
            warning="Could not read a scale label from the uploaded image.",
        )
    bar = detect_horizontal_scale_bar_pixels(image, crop_fraction=crop_fraction)
    if bar is None:
        if use_ai_fallback:
            ai_result = detect_scale_bar_with_ai(image, provider=ai_provider, crop_fraction=crop_fraction, label_hint=label_text)
            if ai_result.pixels_per_foot:
                return ai_result
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


def detect_scale_bar_with_ai(
    image: Image.Image,
    *,
    provider: Callable[[Image.Image], dict[str, Any]] | None = None,
    crop_fraction: float = 0.35,
    label_hint: str | None = None,
) -> CalibrationResult:
    crop, x_offset, y_offset = _bottom_band_crop(image, crop_fraction=crop_fraction)
    try:
        payload = provider(crop) if provider is not None else _call_openai_scale_reader(crop)
    except Exception as exc:
        return CalibrationResult(
            calibration_type="none",
            confidence="none",
            warning=f"AI scale reader failed: {type(exc).__name__}: {exc}",
        )
    length_feet = _length_feet_from_ai_payload(payload, label_hint=label_hint)
    start = _point_from_ai_payload(payload, "bar_start")
    end = _point_from_ai_payload(payload, "bar_end")
    if length_feet is None or start is None or end is None:
        return CalibrationResult(
            calibration_type="none",
            confidence="none",
            warning="AI scale reader did not return a usable scale label and bar endpoints.",
        )
    start_full = (float(start[0]) + x_offset, float(start[1]) + y_offset)
    end_full = (float(end[0]) + x_offset, float(end[1]) + y_offset)
    pixel_distance = point_distance(start_full, end_full)
    if pixel_distance <= 0:
        return CalibrationResult(
            calibration_type="none",
            confidence="none",
            warning="AI scale reader returned identical scale-bar endpoints.",
        )
    ai_confidence = str(payload.get("confidence") or "").strip().lower()
    confidence = "medium" if ai_confidence in {"high", "medium"} else "low"
    label = str(payload.get("scale_label") or label_hint or "").strip()
    return CalibrationResult(
        calibration_type="scale_bar",
        length_feet=length_feet,
        point_a=start_full,
        point_b=end_full,
        pixel_distance=pixel_distance,
        pixels_per_foot=pixel_distance / length_feet,
        confidence=confidence,
        warning=(
            "AI-calibrated from the visible scale bar"
            + (f" ({label}). " if label else ". ")
            + "Verify against a known roof dimension before final use."
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


def _length_feet_from_ai_payload(payload: dict[str, Any], *, label_hint: str | None = None) -> float | None:
    for key in ("length_feet", "scale_length_feet"):
        value = payload.get(key)
        try:
            if value is not None and float(value) > 0:
                return float(value)
        except (TypeError, ValueError):
            pass
    return parse_scale_label_feet(str(payload.get("scale_label") or label_hint or ""))


def _point_from_ai_payload(payload: dict[str, Any], key: str) -> Point | None:
    value = payload.get(key)
    if isinstance(value, dict):
        try:
            return float(value.get("x")), float(value.get("y"))
        except (TypeError, ValueError):
            return None
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return None
    x_value = payload.get(f"{key}_x")
    y_value = payload.get(f"{key}_y")
    try:
        if x_value is not None and y_value is not None:
            return float(x_value), float(y_value)
    except (TypeError, ValueError):
        return None
    return None


def _call_openai_scale_reader(crop: Image.Image) -> dict[str, Any]:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set")
    try:
        from openai import OpenAI  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("openai package is not installed") from exc
    buffer = BytesIO()
    crop.convert("RGB").save(buffer, format="PNG")
    data_url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    try:
        timeout_seconds = float(os.getenv("OPENAI_ROOF_MEASURE_SCALE_TIMEOUT_SECONDS", "20"))
    except (TypeError, ValueError):
        timeout_seconds = 20.0
    model = os.getenv("OPENAI_ROOF_MEASURE_SCALE_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-4o-mini"
    client = OpenAI(timeout=timeout_seconds)
    response = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "You read map scale bars from screenshots. Return only strict JSON. "
                    "Coordinates must be pixel coordinates within the provided image crop."
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Find the visible Google Earth/map scale label and the horizontal scale-bar endpoints. "
                            "Return JSON with: scale_label, length_feet, bar_start {x,y}, bar_end {x,y}, confidence, notes. "
                            "If the label says meters/kilometers/miles, convert length_feet to feet. "
                            "Use the endpoints of the scale bar line itself, not the text."
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
                ],
            },
        ],
    )
    text = response.choices[0].message.content or "{}"
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return {}
        payload = json.loads(match.group(0))
    return payload if isinstance(payload, dict) else {}


def detect_horizontal_scale_bar_pixels(
    image: Image.Image,
    *,
    crop_fraction: float = 0.35,
) -> tuple[Point, Point, float] | None:
    crop, x_offset, y_offset = _bottom_band_crop(image, crop_fraction=crop_fraction)
    gray = np.asarray(crop.convert("L"))
    candidates = [
        *_horizontal_bar_run_candidates(gray > 210),
        *_horizontal_bar_candidates(gray < 70),
        *_horizontal_bar_candidates(gray > 235),
    ]
    crop_width = gray.shape[1]
    candidates = [
        candidate
        for candidate in candidates
        if candidate[0] > 5 and candidate[2] < crop_width - 5
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


def _bottom_band_crop(image: Image.Image, *, crop_fraction: float) -> tuple[Image.Image, int, int]:
    width, height = image.size
    crop_height = max(1, int(height * max(0.1, min(crop_fraction, 0.8))))
    y0 = max(0, height - crop_height)
    return image.crop((0, y0, width, height)), 0, y0


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


def _horizontal_bar_run_candidates(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
    height, width = mask.shape
    candidates: list[tuple[int, int, int, int]] = []
    for y in range(height):
        runs: list[tuple[int, int]] = []
        x = 0
        while x < width:
            if not mask[y, x]:
                x += 1
                continue
            x0 = x
            while x < width and mask[y, x]:
                x += 1
            x1 = x
            if x1 - x0 >= 20:
                runs.append((x0, x1))
        if not runs:
            continue
        for index, (x0, x1) in enumerate(runs):
            candidates.append((x0, max(0, y - 1), x1, min(height, y + 2)))
            merged_x0 = x0
            merged_x1 = x1
            for next_x0, next_x1 in runs[index + 1 :]:
                if next_x0 - merged_x1 > 100:
                    break
                merged_x1 = next_x1
                if merged_x1 - merged_x0 >= 80:
                    candidates.append((merged_x0, max(0, y - 1), merged_x1, min(height, y + 2)))
    image_width = width
    return [
        candidate
        for candidate in candidates
        if 30 <= candidate[2] - candidate[0] <= image_width * 0.4
    ]


def _ocr_scale_text(image: Image.Image, *, crop_fraction: float) -> str:
    try:
        import pytesseract
    except ImportError:
        return ""
    crops = _scale_label_ocr_crops(image, crop_fraction=crop_fraction)
    texts: list[str] = []
    try:
        for crop in crops:
            gray = crop.convert("L")
            for config in ("--psm 6", "--psm 11", "--psm 7"):
                text = str(pytesseract.image_to_string(gray, config=config) or "")
                if text:
                    texts.append(text)
    except Exception:
        return ""
    return "\n".join(texts)


def _scale_label_ocr_crops(image: Image.Image, *, crop_fraction: float) -> list[Image.Image]:
    width, height = image.size
    bottom, _, _ = _bottom_band_crop(image, crop_fraction=crop_fraction)
    return [
        bottom,
        image.crop((int(width * 0.50), int(height * (1 - max(0.1, min(crop_fraction, 0.8)))), width, height)),
        image.crop((int(width * 0.70), int(height * 0.84), width, height)),
    ]
