from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
from PIL import Image

from .ai_polygons import _image_data_url
from .ai_raster_outline import _filled_interior, _morphological_close, _yellow_mask
from .models import Ring, RoofSection
from .polygon_editor import apply_polygon_operations
from .polygonize import sections_from_mask
from .visualization import lidar_height_overlay, vertex_editor_overlay


ProgressCallback = Callable[[str, int, list[RoofSection], set[str], int, int], None]


@dataclass
class VertexAgentResult:
    sections: list[RoofSection]
    operations: list[dict[str, Any]] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    confidence: float = 0.0
    model_name: str = "openai_vertex_tool_loop"
    model_version: str = ""


@dataclass(frozen=True)
class _VertexRef:
    polygon_id: str
    hole_id: str | None
    index: int


class _VertexIdentity:
    """Stable labels for one AI run even when vertices are inserted or deleted."""

    def __init__(self, sections: list[RoofSection]):
        self._rings: dict[tuple[str, str | None], list[str]] = {}
        self._counter = 0
        for section in sections:
            self._rings[(section.section_id, None)] = self._new_ids(len(_open_ring(section.polygon)))
            for hole_index, hole in enumerate(section.holes):
                hole_id = f"{section.section_id}:hole:{hole_index}"
                self._rings[(section.section_id, hole_id)] = self._new_ids(len(_open_ring(hole)))

    def _new_ids(self, count: int) -> list[str]:
        values = []
        for _ in range(count):
            self._counter += 1
            values.append(f"v{self._counter}")
        return values

    def resolve(self, vertex_id: str) -> _VertexRef | None:
        for (polygon_id, hole_id), values in self._rings.items():
            if vertex_id in values:
                return _VertexRef(polygon_id, hole_id, values.index(vertex_id))
        return None

    def insert_after(self, ref: _VertexRef) -> str:
        values = self._rings[(ref.polygon_id, ref.hole_id)]
        vertex_id = self._new_ids(1)[0]
        values.insert(ref.index + 1, vertex_id)
        return vertex_id

    def delete(self, ref: _VertexRef) -> None:
        self._rings[(ref.polygon_id, ref.hole_id)].pop(ref.index)

    def stable_id_for_legacy_key(self, key: str) -> str | None:
        for (polygon_id, hole_id), values in self._rings.items():
            prefix = hole_id or polygon_id
            if not key.startswith(f"{prefix}:"):
                continue
            try:
                index = int(key[len(prefix) + 1 :])
            except ValueError:
                continue
            if 0 <= index < len(values):
                return values[index]
        return None

    def overlay_labels(self) -> dict[str, str]:
        labels: dict[str, str] = {}
        for (polygon_id, hole_id), values in self._rings.items():
            prefix = hole_id or polygon_id
            for index, vertex_id in enumerate(values):
                labels[f"{prefix}:{index}"] = vertex_id
        return labels

    def document(self, sections: list[RoofSection]) -> list[dict[str, Any]]:
        document = []
        for section in sections:
            exterior_ids = self._rings[(section.section_id, None)]
            item = {
                "polygon_id": section.section_id,
                "vertices": _vertex_records(_open_ring(section.polygon), exterior_ids),
                "holes": [],
            }
            for hole_index, hole in enumerate(section.holes):
                hole_id = f"{section.section_id}:hole:{hole_index}"
                item["holes"].append(
                    {
                        "hole_id": hole_id,
                        "vertices": _vertex_records(_open_ring(hole), self._rings[(section.section_id, hole_id)]),
                    }
                )
            document.append(item)
        return document


def run_vertex_tool_loop(
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
    progress_callback: ProgressCallback | None = None,
    client=None,
) -> VertexAgentResult:
    if not os.getenv("OPENAI_API_KEY") and client is None:
        raise RuntimeError("OPENAI_API_KEY is not set")
    from openai import OpenAI  # type: ignore

    model = os.getenv("OPENAI_ROOF_MEASURE_POLYGON_EDITOR_MODEL") or os.getenv("OPENAI_ROOF_MEASURE_QA_MODEL") or "gpt-4o"
    max_steps = max(1, min(int(os.getenv("OPENAI_ROOF_MEASURE_VERTEX_TOOL_STEPS", "12")), 30))
    working = [section.model_copy(deep=True) for section in sections]
    identities = _VertexIdentity(working)
    locked_stable = {
        stable_id
        for key in locked_vertices or set()
        if (stable_id := identities.stable_id_for_legacy_key(str(key))) is not None
    }
    operations: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    edited_stable: set[str] = set()

    if stage == "exterior" and boundary_target_image is not None:
        working, initialized = initialize_vertices_toward_boundary(
            image,
            working,
            boundary_target_image,
            identities=identities,
            locked_vertex_ids=locked_stable,
        )
        operations.extend(initialized)
        if initialized and progress_callback is not None:
            progress_callback(stage, 0, working, _legacy_keys(initialized), len(initialized), max_steps)

    analysis_base = (
        boundary_target_image.convert("RGB").resize(image.size, Image.Resampling.LANCZOS)
        if boundary_target_image is not None
        else lidar_height_overlay(image, height_grid=lidar_height_grid, cell_pixels=lidar_cell_pixels)
    )
    api_client = client or OpenAI(timeout=float(os.getenv("OPENAI_ROOF_MEASURE_POLYGON_EDITOR_TIMEOUT_SECONDS", "180")))
    tools = _vertex_tools(stage)
    instructions = _agent_instructions(
        image=image,
        stage=stage,
        address=address,
        locked_ids=locked_stable,
        has_yellow_target=boundary_target_image is not None,
        additional_instructions=additional_instructions,
    )
    initial_overlay = _render_editor_view(
        analysis_base,
        working,
        identities,
        stage=stage,
        locked_stable=locked_stable,
        edited_stable=edited_stable,
    )
    request: dict[str, Any] = {
        "model": model,
        "instructions": instructions,
        "tools": tools,
        "tool_choice": "required",
        "parallel_tool_calls": False,
        "input": [{"role": "user", "content": [
            {"type": "input_text", "text": "Inspect the current registered editor view and begin the bounded vertex-editing task. Current vertex document: " + json.dumps(identities.document(working), separators=(",", ":"))},
            {"type": "input_image", "image_url": _image_data_url(initial_overlay), "detail": "high"},
        ]}],
    }
    reasoning_effort = os.getenv("OPENAI_ROOF_MEASURE_POLYGON_EDITOR_REASONING_EFFORT", "medium").strip()
    if reasoning_effort:
        request["reasoning"] = {"effort": reasoning_effort}

    response = _create_response(api_client, request)
    reasoning_enabled = "reasoning" in request
    accepted = False
    for step_number in range(1, max_steps + 1):
        calls = [item for item in getattr(response, "output", []) if getattr(item, "type", "") == "function_call"]
        if not calls:
            return VertexAgentResult(
                sections=working,
                operations=operations,
                steps=steps,
                warnings=["Vertex tool loop stopped because the model returned no editor tool call."],
                confidence=0.65 if operations else 0.0,
                model_version=model,
            )
        call = calls[0]
        try:
            arguments = json.loads(getattr(call, "arguments", "{}") or "{}")
        except json.JSONDecodeError:
            arguments = {}
        tool_name = str(getattr(call, "name", ""))
        if tool_name == "accept_polygon":
            steps.append({"step": step_number, "tool": tool_name, "accepted": True, "reason": arguments.get("reason")})
            accepted = True
            break

        outcome = _execute_tool(
            tool_name,
            arguments,
            sections=working,
            identities=identities,
            image_size=image.size,
            stage=stage,
            locked_stable=locked_stable,
            edited_stable=edited_stable,
        )
        working = outcome["sections"]
        step_operations = list(outcome.get("operations") or [])
        operation = outcome.get("operation")
        if operation is not None:
            step_operations.append(operation)
        operations.extend(step_operations)
        steps.append({
            "step": step_number,
            "tool": tool_name,
            "accepted": bool(outcome["ok"]),
            "message": outcome["message"],
            "vertex_id": outcome.get("vertex_id"),
        })
        if step_operations and progress_callback is not None:
            progress_callback(stage, step_number, working, _legacy_keys(operations), len(operations), max_steps)

        rendered = _render_editor_view(
            analysis_base,
            working,
            identities,
            stage=stage,
            locked_stable=locked_stable,
            edited_stable=edited_stable,
            focus_vertex_id=outcome.get("focus_vertex_id"),
            radius=int(outcome.get("radius") or 180),
        )
        follow_input = [
            {
                "type": "function_call_output",
                "call_id": getattr(call, "call_id", ""),
                "output": json.dumps(
                    {
                        "ok": bool(outcome["ok"]),
                        "message": outcome["message"],
                        "current_vertex_document": identities.document(working),
                    },
                    separators=(",", ":"),
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "This is the rerendered current editor view after the tool result. Continue with one tool call or accept when complete."},
                    {"type": "input_image", "image_url": _image_data_url(rendered), "detail": "high"},
                ],
            },
        ]
        request = {
            "model": model,
            "instructions": instructions,
            "tools": tools,
            "tool_choice": "required",
            "parallel_tool_calls": False,
            "previous_response_id": getattr(response, "id", None),
            "input": follow_input,
        }
        if reasoning_enabled:
            request["reasoning"] = {"effort": reasoning_effort}
        response = _create_response(api_client, request)
        reasoning_enabled = "reasoning" in request

    warnings = [] if accepted else [f"Vertex tool loop reached its {max_steps}-step limit; retained all validated edits."]
    return VertexAgentResult(
        sections=working,
        operations=operations,
        steps=steps,
        warnings=warnings,
        confidence=0.82 if accepted and operations else 0.7 if operations else 0.0,
        model_version=model,
    )


def initialize_vertices_toward_boundary(
    image: Image.Image,
    sections: list[RoofSection],
    boundary_target_image: Image.Image,
    *,
    identities: _VertexIdentity | None = None,
    locked_vertex_ids: set[str] | None = None,
    max_distance_pixels: float | None = None,
) -> tuple[list[RoofSection], list[dict[str, Any]]]:
    """Move nearby exterior footprint vertices to the registered yellow band."""
    working = [section.model_copy(deep=True) for section in sections]
    identities = identities or _VertexIdentity(working)
    locked_vertex_ids = locked_vertex_ids or set()
    max_distance = float(max_distance_pixels or os.getenv("OPENAI_ROOF_MEASURE_VERTEX_INITIAL_SNAP_PIXELS", "24"))
    target = boundary_target_image.convert("RGB").resize(image.size, Image.Resampling.LANCZOS)
    yellow = _yellow_mask(target)
    if not yellow.any():
        return working, []
    target_sections = sections_from_mask(
        _filled_interior(_morphological_close(yellow, iterations=2)),
        simplification_tolerance=3.0,
        minimum_section_area_pixels=400,
        edge_snap_strength=0.0,
    )
    target_vertices = [point for section in target_sections for point in _open_ring(section.polygon)]
    if not target_vertices:
        return working, []
    yellow_points = np.asarray(target_vertices, dtype=float)
    operations: list[dict[str, Any]] = []
    for section in list(working):
        vertex_ids = identities._rings[(section.section_id, None)]
        for index, point in enumerate(_open_ring(section.polygon)):
            vertex_id = vertex_ids[index]
            if vertex_id in locked_vertex_ids:
                continue
            deltas = yellow_points - np.asarray(point, dtype=float)
            distances_sq = np.einsum("ij,ij->i", deltas, deltas)
            nearest_index = int(np.argmin(distances_sq))
            distance = math.sqrt(float(distances_sq[nearest_index]))
            if distance < 1.5 or distance > max_distance:
                continue
            x, y = yellow_points[nearest_index]
            operation = {
                "op": "move_vertex",
                "polygon_id": section.section_id,
                "vertex_index": index,
                "x": float(x),
                "y": float(y),
                "source": "yellow_boundary_initialization",
                "stable_vertex_id": vertex_id,
            }
            applied = apply_polygon_operations(working, [operation], image_size=image.size, max_area_change_ratio=0.18)
            if applied.applied_operations:
                working = applied.sections
                operations.append(operation)
    return working, operations


def _execute_tool(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    sections: list[RoofSection],
    identities: _VertexIdentity,
    image_size: tuple[int, int],
    stage: str,
    locked_stable: set[str],
    edited_stable: set[str],
) -> dict[str, Any]:
    working = [section.model_copy(deep=True) for section in sections]
    if tool_name == "move_vertices_relative":
        edits = arguments.get("edits") if isinstance(arguments.get("edits"), list) else []
        accepted_operations: list[dict[str, Any]] = []
        messages = []
        focus_vertex_id = None
        for edit in edits[:8]:
            if not isinstance(edit, dict):
                continue
            child = _execute_tool(
                "move_vertex_relative",
                edit,
                sections=working,
                identities=identities,
                image_size=image_size,
                stage=stage,
                locked_stable=locked_stable,
                edited_stable=edited_stable,
            )
            working = child["sections"]
            if child.get("operation") is not None:
                accepted_operations.append(child["operation"])
            messages.append(child["message"])
            focus_vertex_id = child.get("focus_vertex_id") or focus_vertex_id
        return _tool_outcome(
            working,
            bool(accepted_operations),
            " ".join(messages) or "No relative vertex moves were supplied.",
            operations=accepted_operations,
            focus_vertex_id=focus_vertex_id,
        )
    vertex_id = str(arguments.get("vertex_id") or arguments.get("edge_start_vertex_id") or "")
    ref = identities.resolve(vertex_id)
    if ref is None:
        return _tool_outcome(working, False, "Unknown stable vertex ID; inspect the current rerendered labels.")
    if (stage == "exterior") != (ref.hole_id is None):
        return _tool_outcome(working, False, f"{vertex_id} is outside the active {stage} stage.", focus_vertex_id=vertex_id)
    if vertex_id in locked_stable:
        return _tool_outcome(working, False, f"{vertex_id} is a locked estimator anchor.", focus_vertex_id=vertex_id)
    if tool_name == "inspect_region":
        radius = max(80, min(int(arguments.get("radius_pixels") or 180), 360))
        return _tool_outcome(working, True, f"Rendered a local region centered on {vertex_id}.", focus_vertex_id=vertex_id, radius=radius)
    if tool_name == "move_vertex_relative" and vertex_id in edited_stable:
        return _tool_outcome(working, False, f"{vertex_id} was already moved in this run; inspect another unresolved vertex.", focus_vertex_id=vertex_id)

    if tool_name == "move_vertex_relative":
        point = _point_for_ref(working, ref)
        dx = _bounded_delta(arguments.get("dx"))
        dy = _bounded_delta(arguments.get("dy"))
        if dx is None or dy is None:
            return _tool_outcome(working, False, "Relative movement must be within 64 pixels per axis.", focus_vertex_id=vertex_id)
        operation = {
            "op": "modify_hole_vertex" if ref.hole_id else "move_vertex",
            "polygon_id": ref.polygon_id,
            **({"hole_id": ref.hole_id} if ref.hole_id else {}),
            "vertex_index": ref.index,
            "x": point[0] + dx,
            "y": point[1] + dy,
            "stable_vertex_id": vertex_id,
            "source": "vertex_tool_loop",
        }
        applied = apply_polygon_operations(working, [operation], image_size=image_size, max_area_change_ratio=0.18)
        if not applied.applied_operations:
            reason = applied.rejected_operations[0].get("result") if applied.rejected_operations else "topology validation failed"
            return _tool_outcome(working, False, str(reason), focus_vertex_id=vertex_id)
        edited_stable.add(vertex_id)
        return _tool_outcome(applied.sections, True, f"Moved {vertex_id} by ({dx:.1f}, {dy:.1f}) pixels.", operation=operation, vertex_id=vertex_id, focus_vertex_id=vertex_id)

    if tool_name == "insert_vertex_relative":
        ring = _ring_for_ref(working, ref)
        if len(ring) < 2:
            return _tool_outcome(working, False, "The selected ring has no editable edge.", focus_vertex_id=vertex_id)
        end = ring[(ref.index + 1) % len(ring)]
        start = ring[ref.index]
        dx = _bounded_delta(arguments.get("dx"))
        dy = _bounded_delta(arguments.get("dy"))
        if dx is None or dy is None:
            return _tool_outcome(working, False, "Relative insertion offset must be within 64 pixels per axis.", focus_vertex_id=vertex_id)
        operation = {
            "op": "split_edge",
            "polygon_id": ref.polygon_id,
            **({"hole_id": ref.hole_id} if ref.hole_id else {}),
            "edge_index": ref.index,
            "x": (start[0] + end[0]) / 2.0 + dx,
            "y": (start[1] + end[1]) / 2.0 + dy,
            "source": "vertex_tool_loop",
        }
        applied = apply_polygon_operations(working, [operation], image_size=image_size, max_area_change_ratio=0.18)
        if not applied.applied_operations:
            reason = applied.rejected_operations[0].get("result") if applied.rejected_operations else "topology validation failed"
            return _tool_outcome(working, False, str(reason), focus_vertex_id=vertex_id)
        inserted_id = identities.insert_after(ref)
        operation["stable_vertex_id"] = inserted_id
        edited_stable.add(inserted_id)
        return _tool_outcome(applied.sections, True, f"Inserted {inserted_id} after {vertex_id}.", operation=operation, vertex_id=inserted_id, focus_vertex_id=inserted_id)

    if tool_name == "delete_vertex":
        ring = _ring_for_ref(working, ref)
        if len(ring) <= 3:
            return _tool_outcome(working, False, "A polygon ring must retain at least three vertices.", focus_vertex_id=vertex_id)
        operation = {
            "op": "delete_vertex",
            "polygon_id": ref.polygon_id,
            **({"hole_id": ref.hole_id} if ref.hole_id else {}),
            "vertex_index": ref.index,
            "stable_vertex_id": vertex_id,
            "source": "vertex_tool_loop",
        }
        applied = apply_polygon_operations(working, [operation], image_size=image_size, max_area_change_ratio=0.18)
        if not applied.applied_operations:
            reason = applied.rejected_operations[0].get("result") if applied.rejected_operations else "topology validation failed"
            return _tool_outcome(working, False, str(reason), focus_vertex_id=vertex_id)
        identities.delete(ref)
        edited_stable.discard(vertex_id)
        return _tool_outcome(applied.sections, True, f"Deleted redundant vertex {vertex_id}.", operation=operation, vertex_id=vertex_id)

    return _tool_outcome(working, False, f"Unsupported editor tool: {tool_name}.", focus_vertex_id=vertex_id)


def _render_editor_view(
    image: Image.Image,
    sections: list[RoofSection],
    identities: _VertexIdentity,
    *,
    stage: str,
    locked_stable: set[str],
    edited_stable: set[str],
    focus_vertex_id: str | None = None,
    radius: int = 180,
) -> Image.Image:
    labels = identities.overlay_labels()
    locked_legacy = {legacy for legacy, stable in labels.items() if stable in locked_stable}
    edited_legacy = {legacy for legacy, stable in labels.items() if stable in edited_stable}
    overlay = vertex_editor_overlay(
        image,
        sections=sections,
        stage=stage,
        labels=True,
        locked_vertices=locked_legacy,
        edited_vertices=edited_legacy,
        vertex_labels=labels,
    )
    ref = identities.resolve(focus_vertex_id or "")
    if ref is None:
        return overlay
    x, y = _point_for_ref(sections, ref)
    left = max(0, int(round(x - radius)))
    top = max(0, int(round(y - radius)))
    right = min(overlay.width, int(round(x + radius)))
    bottom = min(overlay.height, int(round(y + radius)))
    crop = overlay.crop((left, top, right, bottom))
    if crop.width < 2 or crop.height < 2:
        return overlay
    scale = min(3.0, 768 / max(crop.width, crop.height))
    if scale > 1.0:
        crop = crop.resize((int(round(crop.width * scale)), int(round(crop.height * scale))), Image.Resampling.LANCZOS)
    return crop


def _vertex_tools(stage: str) -> list[dict[str, Any]]:
    ring_description = "exterior polygon" if stage == "exterior" else "courtyard or hole"
    return [
        _strict_tool(
            "inspect_region",
            f"Render a zoomed local view around one stable vertex in the active {ring_description} stage before deciding an uncertain edit.",
            {
                "vertex_id": {"type": "string"},
                "radius_pixels": {"type": "integer", "minimum": 80, "maximum": 360},
            },
        ),
        _strict_tool(
            "move_vertices_relative",
            "Move up to eight independent stable vertices by relative pixel offsets, then receive one validated rerender. Use negative dx for left and negative dy for up.",
            {
                "edits": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "properties": {
                            "vertex_id": {"type": "string"},
                            "dx": {"type": "number", "minimum": -64, "maximum": 64},
                            "dy": {"type": "number", "minimum": -64, "maximum": 64},
                        },
                        "required": ["vertex_id", "dx", "dy"],
                        "additionalProperties": False,
                    },
                },
            },
        ),
        _strict_tool(
            "accept_polygon",
            "Finish this stage when the visible boundary is aligned and no further safe local edit is needed.",
            {"reason": {"type": "string"}},
        ),
    ]


def _strict_tool(name: str, description: str, properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
        "strict": True,
    }


def _agent_instructions(
    *,
    image: Image.Image,
    stage: str,
    address: str,
    locked_ids: set[str],
    has_yellow_target: bool,
    additional_instructions: str,
) -> str:
    target = (
        "A bright-yellow registered boundary is visible. Treat its centerline as the primary visual target, but use the satellite evidence to avoid obvious target mistakes."
        if has_yellow_target
        else "No yellow target is available; use visible wall and parapet evidence directly."
    )
    return " ".join(
        part
        for part in (
            f"You are operating a bounded roof polygon editor on a {image.width} by {image.height} source image for {address or 'an unspecified site'}.",
            f"Active stage: {stage}. {target}",
            "Use stable vertex labels shown in the image and current vertex document. Inspect a local region when an edge is uncertain.",
            "Move existing vertices relatively in small batches onto visible physical wall or parapet inflection points, not cast-shadow edges. Vertex insertion and deletion are disabled; deterministic fitting owns polygon complexity.",
            "Every mutation is applied and topology-validated by the application, then you receive a fresh rerender. Never regenerate a complete polygon.",
            "Do not move a vertex more than once in this run. Preserve separate footprint parts, gaps, courtyards, and holes. Accept when further changes are uncertain.",
            "Locked estimator anchors are authoritative and cannot be changed: " + json.dumps(sorted(locked_ids)) + ".",
            "Additional estimator instructions: " + additional_instructions.strip() if additional_instructions.strip() else "",
        )
        if part
    )


def _create_response(client, request: dict[str, Any]):
    try:
        return client.responses.create(**request)
    except Exception as exc:
        if "reasoning.effort" not in str(exc):
            raise
        request.pop("reasoning", None)
        return client.responses.create(**request)


def _tool_outcome(
    sections: list[RoofSection],
    ok: bool,
    message: str,
    *,
    operation: dict[str, Any] | None = None,
    operations: list[dict[str, Any]] | None = None,
    vertex_id: str | None = None,
    focus_vertex_id: str | None = None,
    radius: int | None = None,
) -> dict[str, Any]:
    return {
        "sections": sections,
        "ok": ok,
        "message": message,
        "operation": operation,
        "operations": operations or [],
        "vertex_id": vertex_id,
        "focus_vertex_id": focus_vertex_id,
        "radius": radius,
    }


def _ring_for_ref(sections: list[RoofSection], ref: _VertexRef) -> Ring:
    section = next(section for section in sections if section.section_id == ref.polygon_id)
    if ref.hole_id is None:
        return _open_ring(section.polygon)
    hole_index = int(ref.hole_id.rsplit(":", 1)[1])
    return _open_ring(section.holes[hole_index])


def _point_for_ref(sections: list[RoofSection], ref: _VertexRef) -> tuple[float, float]:
    return _ring_for_ref(sections, ref)[ref.index]


def _bounded_delta(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and abs(number) <= 64 else None


def _open_ring(ring: Ring) -> Ring:
    return list(ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring)


def _vertex_records(points: Ring, vertex_ids: list[str]) -> list[dict[str, Any]]:
    return [
        {"vertex_id": vertex_id, "x": round(float(point[0]), 2), "y": round(float(point[1]), 2)}
        for vertex_id, point in zip(vertex_ids, points, strict=False)
    ]


def _legacy_keys(operations: list[dict[str, Any]]) -> set[str]:
    keys = set()
    for operation in operations:
        polygon_id = str(operation.get("polygon_id") or "")
        prefix = str(operation.get("hole_id") or polygon_id)
        try:
            keys.add(f"{prefix}:{int(operation['vertex_index'])}")
        except (KeyError, TypeError, ValueError):
            continue
    return keys
