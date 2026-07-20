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
    boundary_target_image: Image.Image | None = None,
    additional_instructions: str = "",
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
                boundary_target_image=boundary_target_image,
                additional_instructions=additional_instructions,
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
    boundary_target_image: Image.Image | None,
    additional_instructions: str,
) -> dict[str, Any]:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set")
    from openai import OpenAI  # type: ignore

    model = os.getenv("OPENAI_ROOF_MEASURE_POLYGON_EDITOR_MODEL") or os.getenv("OPENAI_ROOF_MEASURE_QA_MODEL") or "gpt-4o"
    client = OpenAI(timeout=float(os.getenv("OPENAI_ROOF_MEASURE_POLYGON_EDITOR_TIMEOUT_SECONDS", "120")))
    stage_instruction = (
        "Adjust only exterior roof/parapet boundaries using move_vertex operations. Do not insert, delete, merge, or modify holes in this pass."
        if stage == "exterior"
        else "Review only existing gaps, courtyards, and internal holes using modify_hole_vertex operations. Do not change unrelated exterior edges in this pass."
    )
    if boundary_target_image is not None:
        analysis_image = boundary_target_image.convert("RGB").resize(image.size, Image.Resampling.LANCZOS)
        target_instruction = (
            "The bright-yellow band is the target roof perimeter generated in a separate visual pass. "
            "Move unlocked perimeter vertices onto the center of that yellow band and make connected edges follow it. "
        )
    else:
        analysis_image = lidar_height_overlay(image, height_grid=lidar_height_grid, cell_pixels=lidar_cell_pixels)
        target_instruction = "No yellow target is available; use visible wall and parapet evidence directly. "
    overlay = vertex_editor_overlay(
        analysis_image,
        sections=sections_from_document(sections=document),
        stage=stage,
        locked_vertices=locked_vertices,
    )
    user_guidance = additional_instructions.strip()
    instructions = "".join(
        (
            f"The analysis image is {image.width} by {image.height} pixels. Site hint: {address or 'not provided'}. ",
            f"{stage_instruction} The colored outline is the current geometry. Every vertex label is polygon_id:vertex_index; hole labels include polygon_id:hole:index:vertex_index. ",
            target_instruction,
            "Edit the current polygon only. Prefer the visible roof/parapet edge over a cast shadow. Preserve separate building parts and visible ground gaps. ",
            "Blue vertices are authoritative estimator-set anchors. Never move, delete, merge, or replace them. Use them as examples of the correct local boundary and align neighboring unlocked geometry consistently with them. ",
            "Move each existing unlocked vertex as far as the target boundary requires; do not make token tiny adjustments. Place existing corner vertices at real wall inflection points and keep vertices that already match the target unchanged. ",
            "Return one complete batch of up to 32 independent edits. Return accept only when no further safe edit is needed. ",
            "Locked vertices are a hard constraint. Use as many edits as needed to align the remaining perimeter, but do not edit a locked vertex. Do not return full polygons, prose, coordinates without an operation, or markdown. ",
            "Return JSON only: {\"operations\":[{\"op\":\"move_vertex\",\"polygon_id\":\"section-1\",\"vertex_index\":0,\"x\":0,\"y\":0}],\"confidence\":0.0,\"warnings\":[]}. ",
            "Allowed operations in this pass are move_vertex and accept. " if stage == "exterior" else "Allowed operations in this pass are modify_hole_vertex and accept. ",
            "Locked vertices: " + json.dumps(sorted(locked_vertices), separators=(",", ":")) + ". ",
            "When visible, cyan means LiDAR-elevated roof support and amber means low ground support. It is supporting evidence only. ",
            "Additional estimator instructions: " + user_guidance + ". " if user_guidance else "",
            "Current vertex JSON: " + json.dumps(document, separators=(",", ":")),
        )
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
