from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Callable

from PIL import Image

from .geometry import repair_polygon
from .models import Point, Ring, RoofSection


@dataclass
class RoofPolygonSuggestion:
    polygons: list[Ring] = field(default_factory=list)
    confidence: float = 0.0
    notes: str = ""
    warnings: list[str] = field(default_factory=list)
    model_name: str = "openai_roof_outline"
    model_version: str = ""
    focus_crop: tuple[int, int, int, int] | None = None


AiPolygonProvider = Callable[[Image.Image, str, int, int], dict[str, Any]]
AiPolygonRefinementProvider = Callable[[Image.Image, str, int, int, list[dict[str, Any]]], dict[str, Any]]


def suggest_roof_polygons(
    image: Image.Image,
    *,
    address: str = "",
    reference_images: list[Image.Image] | None = None,
    focus_points: list[Point] | None = None,
    provider: AiPolygonProvider | None = None,
) -> RoofPolygonSuggestion:
    width, height = image.size
    focus_crop = _focus_crop_box(image.size, focus_points or [])
    inference_image = image.crop(focus_crop) if focus_crop else image
    try:
        payload = (
            provider(inference_image, address, inference_image.width, inference_image.height)
            if provider is not None
            else _call_openai_roof_polygon_suggester(
                inference_image,
                address=address,
                reference_images=reference_images,
            )
        )
    except Exception as exc:
        return RoofPolygonSuggestion(warnings=[f"AI roof outline suggestion failed: {type(exc).__name__}: {exc}"])
    suggestion = polygon_suggestion_from_payload(
        payload,
        width=inference_image.width,
        height=inference_image.height,
    )
    if focus_crop:
        x0, y0, _, _ = focus_crop
        suggestion.polygons = [
            [(float(x) + x0, float(y) + y0) for x, y in polygon]
            for polygon in suggestion.polygons
        ]
        suggestion.focus_crop = focus_crop
    return suggestion


def _focus_crop_box(image_size: tuple[int, int], points: list[Point]) -> tuple[int, int, int, int] | None:
    """Return a roof-scale crop around distributed AI prompts, or None for full image."""
    width, height = image_size
    usable = [(float(x), float(y)) for x, y in points if 0 <= float(x) < width and 0 <= float(y) < height]
    if len(usable) < 2:
        return None
    xs, ys = zip(*usable)
    span_x = max(xs) - min(xs)
    span_y = max(ys) - min(ys)
    # Preserve enough context for exterior walls while forcing meaningful pixel scale.
    crop_width = min(width, max(640, int(round(span_x * 1.45))))
    crop_height = min(height, max(640, int(round(span_y * 1.45))))
    center_x = (min(xs) + max(xs)) / 2
    center_y = (min(ys) + max(ys)) / 2
    x0 = max(0, min(width - crop_width, int(round(center_x - crop_width / 2))))
    y0 = max(0, min(height - crop_height, int(round(center_y - crop_height / 2))))
    crop = (x0, y0, x0 + crop_width, y0 + crop_height)
    # Do not create a virtually identical copy of the source image.
    return crop if crop_width * crop_height < width * height * 0.9 else None


def suggest_refined_roof_polygons(
    image: Image.Image,
    current_sections: list[RoofSection],
    *,
    address: str = "",
    reference_images: list[Image.Image] | None = None,
    provider: AiPolygonRefinementProvider | None = None,
) -> RoofPolygonSuggestion:
    width, height = image.size
    current_payload = _sections_payload(current_sections)
    try:
        payload = (
            provider(image, address, width, height, current_payload)
            if provider is not None
            else _call_openai_roof_polygon_refiner(
                image,
                current_payload=current_payload,
                address=address,
                reference_images=reference_images,
            )
        )
    except Exception as exc:
        return RoofPolygonSuggestion(warnings=[f"AI roof outline cleanup failed: {type(exc).__name__}: {exc}"])
    return polygon_suggestion_from_payload(payload, width=width, height=height)


def polygon_suggestion_from_payload(payload: dict[str, Any], *, width: int, height: int) -> RoofPolygonSuggestion:
    polygons: list[Ring] = []
    for item in payload.get("roof_polygons") or payload.get("polygons") or []:
        points_value = item.get("points") if isinstance(item, dict) else item
        polygon = _polygon_from_payload(points_value, width=width, height=height)
        if polygon:
            polygons.append(polygon)
    warnings = [str(item) for item in payload.get("warnings") or [] if str(item).strip()]
    confidence = _safe_confidence(payload.get("confidence"))
    notes = str(payload.get("notes") or "").strip()
    return RoofPolygonSuggestion(
        polygons=polygons[:20],
        confidence=confidence,
        notes=notes,
        warnings=warnings,
        model_name=str(payload.get("model_name") or "openai_roof_outline"),
        model_version=str(payload.get("model_version") or ""),
    )


def _call_openai_roof_polygon_suggester(
    image: Image.Image,
    *,
    address: str = "",
    reference_images: list[Image.Image] | None = None,
) -> dict[str, Any]:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set")
    try:
        from openai import OpenAI  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("openai package is not installed") from exc
    width, height = image.size
    data_url = _image_data_url(image)
    timeout_seconds = float(os.getenv("OPENAI_ROOF_MEASURE_POLYGONS_TIMEOUT_SECONDS", "120"))
    model = os.getenv("OPENAI_ROOF_MEASURE_POLYGONS_MODEL") or os.getenv("OPENAI_ROOF_MEASURE_POINTS_MODEL") or "gpt-4o"
    client = OpenAI(timeout=timeout_seconds)
    user_content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                f"The primary overhead image is {width} by {height} pixels. "
                f"Target/site hint: {address or 'not provided'}. "
                "Draw ONE continuous line around the outside perimeter of the target connected building roof complex in this image. "
                "Follow the visible exterior roof edge/eave/parapet, including real outside notches and connected wings. "
                "Every connected target roof mass must be inside this one perimeter. "
                "Do not draw separate boxes around roof wings, do not cut across the middle of the building, and do not use internal roof seams, parapets, shadows, or elevation changes as boundaries. "
                "Keep parking lots, roads, sidewalks, grass, trees, vehicles, athletic fields, and detached buildings outside the perimeter. "
                "Use 12 to 32 corners only where the exterior edge changes direction; do not round corners or trace roof texture. "
                "Return exactly one polygon unless the target truly consists of detached buildings separated by open ground. "
                "The reference/oblique images are context only. All coordinates must refer to the primary overhead image. "
                "Return JSON with this schema: "
                "{\"roof_polygons\":[{\"label\":\"main roof\",\"points\":[{\"x\":0,\"y\":0},{\"x\":10,\"y\":0},{\"x\":10,\"y\":10},{\"x\":0,\"y\":10}],\"reason\":\"...\"}],"
                "\"confidence\":0.0,\"notes\":\"...\",\"warnings\":[\"...\"]}."
            ),
        },
        {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
        *_reference_image_content(reference_images),
    ]
    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "You identify roof measurement polygons from overhead satellite imagery. "
                    "Return only strict JSON with pixel coordinates in the provided image."
                ),
            },
            {
                "role": "user",
                "content": user_content,
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
    if isinstance(payload, dict):
        payload.setdefault("model_name", "openai_roof_outline")
        payload.setdefault("model_version", model)
        return payload
    return {}


def _call_openai_roof_polygon_refiner(
    image: Image.Image,
    *,
    current_payload: list[dict[str, Any]],
    address: str = "",
    reference_images: list[Image.Image] | None = None,
) -> dict[str, Any]:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set")
    try:
        from openai import OpenAI  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("openai package is not installed") from exc
    width, height = image.size
    data_url = _image_data_url(image)
    timeout_seconds = float(os.getenv("OPENAI_ROOF_MEASURE_POLYGONS_TIMEOUT_SECONDS", "120"))
    model = os.getenv("OPENAI_ROOF_MEASURE_POLYGONS_MODEL") or os.getenv("OPENAI_ROOF_MEASURE_POINTS_MODEL") or "gpt-4o"
    client = OpenAI(timeout=timeout_seconds)
    user_content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                f"Primary overhead image size is {width} by {height} pixels. "
                f"Address/site hint: {address or 'not provided'}. "
                "A segmentation model produced the current roof polygons below. Start from these polygons; do not ignore them. "
                "Move, remove, or add vertices only where needed to make the outline follow visible roof edges. "
                "Prefer straight, simple building boundary edges over jagged texture-following edges. "
                "Remove obvious false-positive polygons on parking lots, grass, vehicles, roads, trees, or shadows. "
                "Preserve separate roof masses when they are real. Use the same order as the current polygons when possible. "
                "Do not invent roof sections that are not visible. Keep polygons conservative when uncertain. "
                "Any reference/oblique images included after the primary overhead image are context only; do not return coordinates from those images. "
                "All returned pixel coordinates must be in the primary overhead image coordinate system. "
                "Return JSON with this schema: "
                "{\"roof_polygons\":[{\"label\":\"section-1\",\"points\":[{\"x\":0,\"y\":0},{\"x\":10,\"y\":0},{\"x\":10,\"y\":10},{\"x\":0,\"y\":10}],\"reason\":\"...\"}],"
                "\"confidence\":0.0,\"notes\":\"...\",\"warnings\":[\"...\"]}. "
                "Current polygons: "
                + json.dumps(current_payload, separators=(",", ":"))
            ),
        },
        {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
        *_reference_image_content(reference_images),
    ]
    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "You refine roof measurement polygons from overhead satellite imagery. "
                    "Return only strict JSON with pixel coordinates in the provided image."
                ),
            },
            {
                "role": "user",
                "content": user_content,
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
    if isinstance(payload, dict):
        payload.setdefault("model_name", "openai_roof_outline_refine")
        payload.setdefault("model_version", model)
        return payload
    return {}


def _polygon_from_payload(value: Any, *, width: int, height: int) -> Ring:
    if not isinstance(value, list):
        return []
    points: list[Point] = []
    for item in value:
        point = _point_from_payload(item, width=width, height=height)
        if point is not None:
            points.append(point)
    repaired = repair_polygon(points)
    if len(repaired) < 4:
        return []
    return repaired


def _point_from_payload(value: Any, *, width: int, height: int) -> Point | None:
    if isinstance(value, dict):
        x_value = value.get("x")
        y_value = value.get("y")
    elif isinstance(value, (list, tuple)) and len(value) >= 2:
        x_value, y_value = value[0], value[1]
    else:
        return None
    try:
        x = float(x_value)
        y = float(y_value)
    except (TypeError, ValueError):
        return None
    if not (0 <= x < width and 0 <= y < height):
        return None
    return x, y


def _sections_payload(sections: list[RoofSection]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for section in sections:
        points = section.polygon[:-1] if section.polygon and section.polygon[0] == section.polygon[-1] else section.polygon
        payload.append(
            {
                "label": section.section_id,
                "area_pixels": section.area_pixels,
                "points": [
                    {"x": round(float(x), 2), "y": round(float(y), 2)}
                    for x, y in points
                ],
            }
        )
    return payload


def _safe_confidence(value: Any) -> float:
    try:
        return max(0.0, min(float(value), 1.0))
    except (TypeError, ValueError):
        return 0.0


def _image_data_url(image: Image.Image) -> str:
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=85, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _reference_image_content(reference_images: list[Image.Image] | None) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    for index, reference_image in enumerate((reference_images or [])[:4], start=1):
        content.append(
            {
                "type": "text",
                "text": (
                    f"Reference image {index}: use this only to understand the target building, roof edges, parapets, elevations, "
                    "and possible false positives. Do not measure from this image."
                ),
            }
        )
        content.append({"type": "image_url", "image_url": {"url": _image_data_url(reference_image), "detail": "low"}})
    return content
