from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from PIL import Image

from .ai_polygons import _image_data_url, _reference_image_content, _sections_payload
from .models import Point, RoofSection
from .visualization import annotated_overlay


@dataclass
class RoofQaFinding:
    """Semantic QA only. Geometry remains owned by the segmenter and cleanup code."""

    missing_regions: list[Point] = field(default_factory=list)
    extra_regions: list[Point] = field(default_factory=list)
    courtyard_errors: list[Point] = field(default_factory=list)
    ground_gaps: list[Point] = field(default_factory=list)
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
            "ground_gaps": _points_payload(self.ground_gaps),
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
    candidate_mask=None,
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
                candidate_mask=candidate_mask,
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
        ground_gaps=_points_from_payload(payload.get("ground_gaps"), width=width, height=height),
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
    negative = _unique_points([*finding.extra_regions, *finding.courtyard_errors, *finding.ground_gaps])
    return positive, negative


def _call_openai_roof_qa(
    image: Image.Image,
    *,
    current_payload: list[dict[str, Any]],
    address: str = "",
    reference_images: list[Image.Image] | None = None,
    candidate_mask=None,
) -> dict[str, Any]:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set")
    try:
        from openai import OpenAI  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("openai package is not installed") from exc
    width, height = image.size
    model = os.getenv("OPENAI_ROOF_MEASURE_QA_MODEL") or os.getenv("OPENAI_ROOF_MEASURE_POINTS_MODEL") or "gpt-4o"
    client = OpenAI(timeout=float(os.getenv("OPENAI_ROOF_MEASURE_QA_TIMEOUT_SECONDS", "90")))
    overlay = annotated_overlay(image, mask=candidate_mask, sections=_sections_from_payload_for_overlay(current_payload))
    instructions = (
        f"Primary overhead image is {width} by {height} pixels. Site hint: {address or 'not provided'}. "
        "The first image is annotated: translucent red is the current SAM mask and green is the measurement boundary. "
        "The second image is the unannotated satellite source. Review the proposed boundary against visible roof surfaces. "
        "Return only semantic coordinate hints, never polygons. Use missing_regions for unselected roof. Use extra_regions for included pavement, trees, unrelated roofs, or ground. "
        "Use courtyard_errors for enclosed voids. Use ground_gaps for visible grass, pavement, or daylight gaps between separate roof masses; place one or two points deep inside each gap, never on shadows or parapets. "
        "Boundary_errors are for deterministic cleanup only. Be conservative and limit each list to 8 points. "
        "Return JSON: {\"missing_regions\":[{\"x\":0,\"y\":0,\"reason\":\"...\"}],\"extra_regions\":[],\"courtyard_errors\":[],\"ground_gaps\":[],\"boundary_errors\":[],\"confidence\":0.0,\"notes\":\"\",\"warnings\":[]}. "
        "Current roof sections: " + json.dumps(current_payload, separators=(",", ":"))
    )
    try:
        response = client.responses.create(
            model=model,
            reasoning={"effort": os.getenv("OPENAI_ROOF_MEASURE_QA_REASONING_EFFORT", "medium")},
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": instructions},
                        {"type": "input_image", "image_url": _image_data_url(overlay), "detail": "high"},
                        {"type": "input_image", "image_url": _image_data_url(image), "detail": "high"},
                    ],
                }
            ],
        )
        payload = _json_payload(response.output_text or "{}")
        if payload:
            payload.setdefault("model_name", "openai_roof_qa_responses")
            payload.setdefault("model_version", model)
            return payload
    except Exception:
        pass
    return _call_openai_roof_qa_chat_completion(
        client,
        model=model,
        image=image,
        current_payload=current_payload,
        address=address,
        reference_images=reference_images,
    )


def _call_openai_roof_qa_chat_completion(
    client,
    *,
    model: str,
    image: Image.Image,
    current_payload: list[dict[str, Any]],
    address: str,
    reference_images: list[Image.Image] | None,
) -> dict[str, Any]:
    width, height = image.size
    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": "You inspect roof measurement boundaries. Return only strict JSON; do not draw or return polygons."},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Primary overhead image is {width} by {height} pixels. Site hint: {address or 'not provided'}. "
                            "Return semantic defect coordinate hints only. Use missing_regions for roof and extra_regions, courtyard_errors, or ground_gaps for visible ground that must be excluded. "
                            "Do not return polygons. Limit lists to 8 points. Return JSON: {\"missing_regions\":[],\"extra_regions\":[],\"courtyard_errors\":[],\"ground_gaps\":[],\"boundary_errors\":[],\"confidence\":0.0,\"notes\":\"\",\"warnings\":[]}. "
                            "Current roof sections: " + json.dumps(current_payload, separators=(",", ":"))
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": _image_data_url(image), "detail": "high"}},
                    *_reference_image_content(reference_images),
                ],
            },
        ],
    )
    payload = _json_payload(response.choices[0].message.content or "{}")
    if payload:
        payload.setdefault("model_name", "openai_roof_qa_chat_completion")
        payload.setdefault("model_version", model)
    return payload


def _json_payload(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        payload = json.loads(match.group(0)) if match else {}
    return payload if isinstance(payload, dict) else {}


def _sections_from_payload_for_overlay(current_payload: list[dict[str, Any]]) -> list[RoofSection]:
    sections: list[RoofSection] = []
    for index, item in enumerate(current_payload):
        points = item.get("points") if isinstance(item, dict) else None
        if not isinstance(points, list):
            continue
        try:
            polygon = [(float(point["x"]), float(point["y"])) for point in points if isinstance(point, dict)]
        except (KeyError, TypeError, ValueError):
            continue
        if len(polygon) >= 3:
            sections.append(RoofSection(section_id=str(item.get("label") or f"section-{index + 1}"), polygon=polygon, area_pixels=0, perimeter_pixels=0))
    return sections


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
