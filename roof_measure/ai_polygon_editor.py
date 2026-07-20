from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from PIL import Image

from .ai_polygons import _image_data_url
from .models import RoofSection
from .polygon_editor import sections_to_vertex_document
from .visualization import lidar_height_overlay, vertex_editor_overlay


@dataclass
class PolygonEditSuggestion:
    operations: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)
    model_name: str = "openai_polygon_editor"
    model_version: str = ""


AiPolygonEditProvider = Callable[[Image.Image, list[dict[str, Any]], str], dict[str, Any]]


def suggest_polygon_operations(
    image: Image.Image,
    sections: list[RoofSection],
    *,
    stage: str,
    address: str = "",
    locked_vertices: set[str] | None = None,
    lidar_height_grid=None,
    lidar_cell_pixels: int = 8,
    provider: AiPolygonEditProvider | None = None,
) -> PolygonEditSuggestion:
    document = sections_to_vertex_document(sections)
    try:
        payload = (
            provider(image, document, stage)
            if provider
            else _call_openai_polygon_editor(
                image,
                document,
                stage=stage,
                address=address,
                locked_vertices=locked_vertices or set(),
                lidar_height_grid=lidar_height_grid,
                lidar_cell_pixels=lidar_cell_pixels,
            )
        )
    except Exception as exc:
        return PolygonEditSuggestion(warnings=[f"AI polygon editor failed: {type(exc).__name__}: {exc}"])
    operations = [item for item in payload.get("operations") or [] if isinstance(item, dict)]
    return PolygonEditSuggestion(
        operations=operations[:32],
        confidence=_confidence(payload.get("confidence")),
        warnings=[str(item) for item in payload.get("warnings") or [] if str(item).strip()],
        model_name=str(payload.get("model_name") or "openai_polygon_editor"),
        model_version=str(payload.get("model_version") or ""),
    )


def _call_openai_polygon_editor(
    image: Image.Image,
    document: list[dict[str, Any]],
    *,
    stage: str,
    address: str,
    locked_vertices: set[str],
    lidar_height_grid,
    lidar_cell_pixels: int,
) -> dict[str, Any]:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set")
    from openai import OpenAI  # type: ignore

    model = os.getenv("OPENAI_ROOF_MEASURE_POLYGON_EDITOR_MODEL") or os.getenv("OPENAI_ROOF_MEASURE_QA_MODEL") or "gpt-4o"
    client = OpenAI(timeout=float(os.getenv("OPENAI_ROOF_MEASURE_POLYGON_EDITOR_TIMEOUT_SECONDS", "120")))
    stage_instruction = (
        "Adjust only exterior roof/parapet boundaries. Do not create, remove, or modify holes in this pass."
        if stage == "exterior"
        else "Review only visible gaps, courtyards, and internal holes. Do not change unrelated exterior edges in this pass."
    )
    analysis_image = lidar_height_overlay(image, height_grid=lidar_height_grid, cell_pixels=lidar_cell_pixels)
    overlay = vertex_editor_overlay(analysis_image, sections=sections_from_document(sections=document), stage=stage)
    instructions = (
        f"The analysis image is {image.width} by {image.height} pixels. Site hint: {address or 'not provided'}. "
        f"{stage_instruction} The colored outline is the current geometry. Every vertex label is polygon_id:vertex_index; hole labels include polygon_id:hole:index:vertex_index. "
        "Edit the current polygon only. Prefer the visible roof/parapet edge over a cast shadow. Preserve separate building parts and visible ground gaps. "
        "Move a vertex as far as the visible edge requires; do not make a token tiny adjustment. Prefer vertices at real wall inflection points, remove redundant short jogs, and add a corner only at a clear direction change. "
        "Return one complete batch of up to 32 independent edits. Return accept only when no further safe edit is needed. "
        "Do not edit a locked vertex unless a topology-validity repair is impossible without it. Use as few edits as possible. Do not return full polygons, prose, coordinates without an operation, or markdown. "
        "Return JSON only: {\"operations\":[{\"op\":\"move_vertex\",\"polygon_id\":\"section-1\",\"vertex_index\":0,\"x\":0,\"y\":0}],\"confidence\":0.0,\"warnings\":[]}. "
        "Allowed operations are move_vertex, insert_vertex, delete_vertex, split_edge, merge_redundant_vertices, create_hole, modify_hole_vertex, delete_hole, accept. "
        "Locked vertices: " + json.dumps(sorted(locked_vertices), separators=(",", ":")) + ". "
        "When visible, cyan means LiDAR-elevated roof support and amber means low ground support. It is supporting evidence only. "
        "Current vertex JSON: " + json.dumps(document, separators=(",", ":"))
    )
    request = {
        "model": model,
        "input": [{"role": "user", "content": [
            {"type": "input_text", "text": instructions},
            {"type": "input_image", "image_url": _image_data_url(overlay), "detail": "high"},
        ]}],
    }
    reasoning_effort = os.getenv("OPENAI_ROOF_MEASURE_POLYGON_EDITOR_REASONING_EFFORT", "medium").strip()
    if reasoning_effort:
        request["reasoning"] = {"effort": reasoning_effort}
    try:
        response = client.responses.create(**request)
    except Exception as exc:
        # Some otherwise suitable vision models support Responses images but not
        # the optional reasoning control. Retry without changing the task.
        if "reasoning.effort" not in str(exc):
            raise
        request.pop("reasoning", None)
        response = client.responses.create(**request)
    payload = _json_payload(response.output_text or "{}")
    payload.setdefault("model_name", "openai_polygon_editor_responses")
    payload.setdefault("model_version", model)
    return payload


def sections_from_document(*, sections: list[dict[str, Any]]) -> list[RoofSection]:
    converted: list[RoofSection] = []
    for item in sections:
        vertices = item.get("vertices") if isinstance(item, dict) else []
        points = []
        for point in vertices if isinstance(vertices, list) else []:
            try:
                points.append((float(point["x"]), float(point["y"])))
            except (KeyError, TypeError, ValueError):
                continue
        if len(points) >= 3:
            converted.append(RoofSection(section_id=str(item.get("polygon_id") or "polygon"), polygon=points, area_pixels=0, perimeter_pixels=0))
    return converted


def _json_payload(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        payload = json.loads(match.group(0)) if match else {}
    return payload if isinstance(payload, dict) else {}


def _confidence(value: Any) -> float:
    try:
        return max(0.0, min(float(value), 1.0))
    except (TypeError, ValueError):
        return 0.0
