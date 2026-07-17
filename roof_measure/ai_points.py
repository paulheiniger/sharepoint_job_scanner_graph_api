from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Callable

from PIL import Image

from .models import Point


@dataclass
class RoofPointSuggestion:
    positive_points: list[Point] = field(default_factory=list)
    negative_points: list[Point] = field(default_factory=list)
    confidence: float = 0.0
    notes: str = ""
    warnings: list[str] = field(default_factory=list)


AiPointProvider = Callable[[Image.Image, str, int, int], dict[str, Any]]


def suggest_roof_prompt_points(
    image: Image.Image,
    *,
    address: str = "",
    reference_images: list[Image.Image] | None = None,
    provider: AiPointProvider | None = None,
) -> RoofPointSuggestion:
    width, height = image.size
    try:
        payload = (
            provider(image, address, width, height)
            if provider is not None
            else _call_openai_roof_point_suggester(image, address=address, reference_images=reference_images)
        )
    except Exception as exc:
        return RoofPointSuggestion(warnings=[f"AI roof point suggestion failed: {type(exc).__name__}: {exc}"])
    return suggestion_from_payload(payload, width=width, height=height)


def suggestion_from_payload(payload: dict[str, Any], *, width: int, height: int) -> RoofPointSuggestion:
    positive = _points_from_payload(payload.get("positive_points"), width=width, height=height)
    if not positive:
        primary = _point_from_payload(payload.get("primary_point"), width=width, height=height)
        if primary is not None:
            positive = [primary]
    negative = _points_from_payload(payload.get("negative_points"), width=width, height=height)
    warnings = [str(item) for item in payload.get("warnings") or [] if str(item).strip()]
    confidence = _safe_confidence(payload.get("confidence"))
    notes = str(payload.get("notes") or "").strip()
    return RoofPointSuggestion(
        positive_points=positive[:12],
        negative_points=negative[:12],
        confidence=confidence,
        notes=notes,
        warnings=warnings,
    )


def _call_openai_roof_point_suggester(
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
    timeout_seconds = float(os.getenv("OPENAI_ROOF_MEASURE_POINTS_TIMEOUT_SECONDS", "30"))
    model = os.getenv("OPENAI_ROOF_MEASURE_POINTS_MODEL") or "gpt-4o"
    client = OpenAI(timeout=timeout_seconds)
    user_content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                f"Primary overhead image size is {width} by {height} pixels. "
                f"Address/site hint: {address or 'not provided'}. "
                "Identify likely roof surfaces for commercial roofing measurement. "
                "Return JSON with positive_points, negative_points, confidence, notes, warnings. "
                "positive_points should be interior points, not boundary points. "
                "Only place positive_points on visible roof membrane/deck surfaces. "
                "Do not place positive_points on pavement, parking lots, sidewalks, roads, grass, courtyards, trees, vehicles, fields, or shadows. "
                "Use one positive point near the center of each major target roof section or connected roof mass. "
                "negative_points should be inside obvious non-roof areas near the target site, such as parking lots, grass, roads, courtyards, shadows, athletic fields, or nearby unrelated buildings. "
                "Add negative_points in large parking lots and open paved areas that touch or surround the target building. "
                "Do not include points on labels, watermarks, attribution, cars, or roads unless they are negative points. "
                "If you are unsure whether a surface is roof or pavement, do not make it a positive point; make it negative or omit it. "
                "Prefer 3 to 8 positive points for a school/campus image, fewer for a single building. "
                "Any reference/oblique images included after the primary overhead image are context only. "
                "All returned pixel coordinates must be in the primary overhead image coordinate system. "
                "Use this schema: {\"positive_points\":[{\"x\":0,\"y\":0,\"reason\":\"...\"}],\"negative_points\":[{\"x\":0,\"y\":0,\"reason\":\"...\"}],\"confidence\":0.0,\"notes\":\"...\",\"warnings\":[\"...\"]}."
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
                    "You help prompt an image segmentation model for roof measurement. "
                    "Return only strict JSON. Coordinates must be pixel coordinates within the provided image."
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
    return payload if isinstance(payload, dict) else {}


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
                    f"Reference image {index}: use this only to understand the target building, roof surfaces, parapets, elevations, "
                    "and false-positive non-roof areas. Do not return coordinates from this image."
                ),
            }
        )
        content.append({"type": "image_url", "image_url": {"url": _image_data_url(reference_image), "detail": "low"}})
    return content


def _points_from_payload(value: Any, *, width: int, height: int) -> list[Point]:
    if not isinstance(value, list):
        return []
    points: list[Point] = []
    for item in value:
        point = _point_from_payload(item, width=width, height=height)
        if point is not None:
            points.append(point)
    return points


def _point_from_payload(value: Any, *, width: int, height: int) -> Point | None:
    if isinstance(value, dict):
        nested = value.get("point") or value.get("coordinates") or value.get("coordinate") or value.get("location")
        if nested is not None:
            nested_point = _point_from_payload(nested, width=width, height=height)
            if nested_point is not None:
                return nested_point
        x_value = value.get("x", value.get("pixel_x", value.get("px")))
        y_value = value.get("y", value.get("pixel_y", value.get("py")))
    elif isinstance(value, (list, tuple)) and len(value) >= 2:
        x_value, y_value = value[0], value[1]
    else:
        return None
    try:
        x = float(x_value)
        y = float(y_value)
    except (TypeError, ValueError):
        return None
    if 0 <= x <= 1 and 0 <= y <= 1:
        x *= max(width - 1, 1)
        y *= max(height - 1, 1)
    if not (0 <= x < width and 0 <= y < height):
        return None
    return x, y


def _safe_confidence(value: Any) -> float:
    try:
        return max(0.0, min(float(value), 1.0))
    except (TypeError, ValueError):
        return 0.0
