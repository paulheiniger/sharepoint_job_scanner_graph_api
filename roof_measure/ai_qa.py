from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from PIL import Image

from .ai_polygons import _image_data_url, _reference_image_content, _sections_payload
from .models import Point, RoofSection


@dataclass
class RoofQaFinding:
    """Semantic QA only. Geometry remains owned by the segmenter and cleanup code."""

    missing_regions: list[Point] = field(default_factory=list)
    extra_regions: list[Point] = field(default_factory=list)
    courtyard_errors: list[Point] = field(default_factory=list)
    boundary_errors: list[Point] = field(default_factory=list)
    confidence: float = 0.0
    notes: str = ""
    warnings: list[str] = field(default_factory=list)
    completed: bool = True
    model_name: str = "openai_roof_qa"
    model_version: str = ""

    def as_record(self) -> dict[str, Any]:
        return {
            "missing_regions": _points_payload(self.missing_regions),
            "extra_regions": _points_payload(self.extra_regions),
            "courtyard_errors": _points_payload(self.courtyard_errors),
            "boundary_errors": _points_payload(self.boundary_errors),
            "confidence": self.confidence,
            "notes": self.notes,
            "warnings": self.warnings,
            "completed": self.completed,
            "model_name": self.model_name,
            "model_version": self.model_version,
        }


AiRoofQaProvider = Callable[[Image.Image, str, int, int, list[dict[str, Any]]], dict[str, Any]]


def suggest_roof_qa(
    image: Image.Image,
    current_sections: list[RoofSection],
    *,
    address: str = "",
    reference_images: list[Image.Image] | None = None,
    provider: AiRoofQaProvider | None = None,
) -> RoofQaFinding:
    width, height = image.size
    current_payload = _sections_payload(current_sections)
    try:
        payload = (
            provider(image, address, width, height, current_payload)
            if provider is not None
            else _call_openai_roof_qa(
                image,
                current_payload=current_payload,
                address=address,
                reference_images=reference_images,
            )
        )
    except Exception as exc:
        return RoofQaFinding(
            completed=False,
            warnings=[f"AI roof QA failed: {type(exc).__name__}: {exc}"],
        )
    return qa_finding_from_payload(payload, width=width, height=height)


def qa_finding_from_payload(payload: dict[str, Any], *, width: int, height: int) -> RoofQaFinding:
    return RoofQaFinding(
        missing_regions=_points_from_payload(payload.get("missing_regions"), width=width, height=height),
        extra_regions=_points_from_payload(payload.get("extra_regions"), width=width, height=height),
        courtyard_errors=_points_from_payload(payload.get("courtyard_errors"), width=width, height=height),
        boundary_errors=_points_from_payload(payload.get("boundary_errors"), width=width, height=height),
        confidence=_safe_confidence(payload.get("confidence")),
        notes=str(payload.get("notes") or "").strip(),
        warnings=[str(item) for item in payload.get("warnings") or [] if str(item).strip()],
        model_name=str(payload.get("model_name") or "openai_roof_qa"),
        model_version=str(payload.get("model_version") or ""),
    )


def qa_corrections_to_prompts(finding: RoofQaFinding) -> tuple[list[Point], list[Point]]:
    """Translate semantic defects to SAM prompts; boundary defects stay deterministic."""
    positive = _unique_points(finding.missing_regions)
    negative = _unique_points([*finding.extra_regions, *finding.courtyard_errors])
    return positive, negative


def _call_openai_roof_qa(
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
    model = os.getenv("OPENAI_ROOF_MEASURE_QA_MODEL") or os.getenv("OPENAI_ROOF_MEASURE_POINTS_MODEL") or "gpt-4o"
    client = OpenAI(timeout=float(os.getenv("OPENAI_ROOF_MEASURE_QA_TIMEOUT_SECONDS", "30")))
    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": "You inspect roof measurement boundaries. Return only strict JSON; do not draw or return polygons.",
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Primary overhead image is {width} by {height} pixels. Site hint: {address or 'not provided'}. "
                            "Inspect the proposed roof boundary against the visible roof. Return only semantic defects as coordinate hints. "
                            "Use missing_regions only for unselected roof areas (positive SAM prompts). Use extra_regions for pavement, trees, or unrelated roofs "
                            "and courtyard_errors for filled voids (negative SAM prompts). Use boundary_errors only for edges that should be handled by deterministic straightening. "
                            "Do not propose replacement polygons or trace geometry. Limit every list to at most 8 points. "
                            "Return JSON: {\"missing_regions\":[{\"x\":0,\"y\":0,\"reason\":\"...\"}],\"extra_regions\":[],\"courtyard_errors\":[],\"boundary_errors\":[],\"confidence\":0.0,\"notes\":\"...\",\"warnings\":[]}. "
                            "Current roof sections: " + json.dumps(current_payload, separators=(",", ":"))
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": _image_data_url(image), "detail": "high"}},
                    *_reference_image_content(reference_images),
                ],
            },
        ],
    )
    text = response.choices[0].message.content or "{}"
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        payload = json.loads(match.group(0)) if match else {}
    if isinstance(payload, dict):
        payload.setdefault("model_name", "openai_roof_qa")
        payload.setdefault("model_version", model)
        return payload
    return {}


def _points_from_payload(value: Any, *, width: int, height: int) -> list[Point]:
    points: list[Point] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            point = (float(item.get("x")), float(item.get("y")))
        except (TypeError, ValueError):
            continue
        if 0 <= point[0] < width and 0 <= point[1] < height:
            points.append(point)
    return _unique_points(points)[:8]


def _unique_points(points: list[Point]) -> list[Point]:
    unique: list[Point] = []
    for point in points:
        if point not in unique:
            unique.append(point)
    return unique


def _points_payload(points: list[Point]) -> list[dict[str, float]]:
    return [{"x": round(x, 2), "y": round(y, 2)} for x, y in points]


def _safe_confidence(value: Any) -> float:
    try:
        return max(0.0, min(float(value), 1.0))
    except (TypeError, ValueError):
        return 0.0
