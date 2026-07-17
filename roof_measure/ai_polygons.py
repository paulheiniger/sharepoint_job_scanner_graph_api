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
from .models import Point, Ring


@dataclass
class RoofPolygonSuggestion:
    polygons: list[Ring] = field(default_factory=list)
    confidence: float = 0.0
    notes: str = ""
    warnings: list[str] = field(default_factory=list)
    model_name: str = "openai_roof_outline"
    model_version: str = ""


AiPolygonProvider = Callable[[Image.Image, str, int, int], dict[str, Any]]


def suggest_roof_polygons(
    image: Image.Image,
    *,
    address: str = "",
    provider: AiPolygonProvider | None = None,
) -> RoofPolygonSuggestion:
    width, height = image.size
    try:
        payload = provider(image, address, width, height) if provider is not None else _call_openai_roof_polygon_suggester(image, address=address)
    except Exception as exc:
        return RoofPolygonSuggestion(warnings=[f"AI roof outline suggestion failed: {type(exc).__name__}: {exc}"])
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


def _call_openai_roof_polygon_suggester(image: Image.Image, *, address: str = "") -> dict[str, Any]:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set")
    try:
        from openai import OpenAI  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("openai package is not installed") from exc
    width, height = image.size
    data_url = _image_data_url(image)
    timeout_seconds = float(os.getenv("OPENAI_ROOF_MEASURE_POLYGONS_TIMEOUT_SECONDS", "45"))
    model = os.getenv("OPENAI_ROOF_MEASURE_POLYGONS_MODEL") or os.getenv("OPENAI_ROOF_MEASURE_POINTS_MODEL") or "gpt-4o"
    client = OpenAI(timeout=timeout_seconds)
    response = client.chat.completions.create(
        model=model,
        temperature=0,
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
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Image size is {width} by {height} pixels. "
                            f"Address/site hint: {address or 'not provided'}. "
                            "Draw approximate straight-line polygons around visible roof surfaces for commercial roofing measurement. "
                            "Use boundary/corner points, not interior points. "
                            "Prefer simple rectilinear polygons with straight edges. Do not trace every texture, shadow, gravel pattern, tree edge, vehicle, or parking stripe. "
                            "Only include roof surfaces for the target site/building. Exclude parking lots, roads, sidewalks, fields, courtyards, grass, trees, vehicles, and unrelated nearby buildings. "
                            "If a roof has several distinct connected masses, return separate polygons. "
                            "Use enough corners to represent the building outline, usually 4 to 12 points per roof mass. "
                            "Do not invent notches that are not visible. When uncertain, make the polygon slightly conservative and add a warning. "
                            "Return JSON with this schema: "
                            "{\"roof_polygons\":[{\"label\":\"main roof\",\"points\":[{\"x\":0,\"y\":0},{\"x\":10,\"y\":0},{\"x\":10,\"y\":10},{\"x\":0,\"y\":10}],\"reason\":\"...\"}],"
                            "\"confidence\":0.0,\"notes\":\"...\",\"warnings\":[\"...\"]}."
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
    if isinstance(payload, dict):
        payload.setdefault("model_name", "openai_roof_outline")
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


def _safe_confidence(value: Any) -> float:
    try:
        return max(0.0, min(float(value), 1.0))
    except (TypeError, ValueError):
        return 0.0


def _image_data_url(image: Image.Image) -> str:
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=85, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
