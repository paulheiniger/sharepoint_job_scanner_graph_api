from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import Ring, RoofSection
from .polygonize import section_from_polygon


@dataclass
class PolygonEditResult:
    sections: list[RoofSection] = field(default_factory=list)
    applied_operations: list[dict[str, Any]] = field(default_factory=list)
    rejected_operations: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""


def sections_to_vertex_document(sections: list[RoofSection]) -> list[dict[str, Any]]:
    """Stable IDs let AI and human editors address the same vertices."""
    document: list[dict[str, Any]] = []
    for section in sections:
        document.append(
            {
                "polygon_id": section.section_id,
                "vertices": _vertices(section.polygon),
                "holes": [
                    {"hole_id": f"{section.section_id}:hole:{index}", "vertices": _vertices(hole)}
                    for index, hole in enumerate(section.holes)
                ],
            }
        )
    return document


def apply_polygon_operations(
    sections: list[RoofSection],
    operations: list[dict[str, Any]],
    *,
    image_size: tuple[int, int],
    max_area_change_ratio: float = 0.35,
) -> PolygonEditResult:
    """Apply atomic edits one at a time, retaining the prior valid geometry."""
    current = _copy_sections(sections)
    applied: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for raw in operations[:80]:
        if not isinstance(raw, dict):
            continue
        operation = str(raw.get("op") or raw.get("operation") or "").strip().lower()
        if operation == "accept":
            break
        candidate = _apply_operation(current, raw, image_size=image_size)
        if candidate is None:
            rejected.append(_operation_record(raw, "invalid operation or unknown polygon/vertex"))
            continue
        valid, reason = _validate_sections(candidate, current, max_area_change_ratio=max_area_change_ratio)
        if not valid:
            rejected.append(_operation_record(raw, reason))
            continue
        current = candidate
        applied.append(_operation_record(raw, "applied"))
    return PolygonEditResult(
        sections=current,
        applied_operations=applied,
        rejected_operations=rejected,
        reason="atomic operations applied" if applied else "no valid polygon operations were applied",
    )


def _apply_operation(sections: list[RoofSection], raw: dict[str, Any], *, image_size: tuple[int, int]) -> list[RoofSection] | None:
    operation = str(raw.get("op") or raw.get("operation") or "").strip().lower()
    polygon_id = str(raw.get("polygon_id") or raw.get("section_id") or "")
    section_index = next((index for index, section in enumerate(sections) if section.section_id == polygon_id), -1)
    if section_index < 0:
        return None
    updated = _copy_sections(sections)
    section = updated[section_index]
    if operation == "create_hole":
        points = _points(raw.get("vertices") or raw.get("points"), image_size)
        if len(points) < 3:
            return None
        section.holes.append(points)
        return _rebuild(updated)
    hole_index = _hole_index(section, raw)
    target = section.polygon if hole_index is None else section.holes[hole_index]
    vertex_index = _integer(raw.get("vertex_index"), default=-1)
    if operation in {"move_vertex", "modify_hole_vertex"}:
        point = _point(raw.get("point") or raw, image_size)
        if point is None or not 0 <= vertex_index < len(target):
            return None
        target[vertex_index] = point
    elif operation in {"insert_vertex", "split_edge"}:
        point = _point(raw.get("point") or raw, image_size)
        edge_index = _integer(raw.get("edge_index"), default=-1)
        if point is None or not 0 <= edge_index < len(target):
            return None
        target.insert(edge_index + 1, point)
    elif operation == "delete_vertex":
        if not 0 <= vertex_index < len(target) or len(target) <= 3:
            return None
        target.pop(vertex_index)
    elif operation == "merge_redundant_vertices":
        first = _integer(raw.get("first_vertex_index"), default=-1)
        second = _integer(raw.get("second_vertex_index"), default=-1)
        if not (0 <= first < len(target) and 0 <= second < len(target)) or len(target) <= 3:
            return None
        target.pop(second)
    elif operation == "delete_hole":
        if hole_index is None:
            return None
        section.holes.pop(hole_index)
    else:
        return None
    return _rebuild(updated)


def _rebuild(sections: list[RoofSection]) -> list[RoofSection] | None:
    rebuilt: list[RoofSection] = []
    for section in sections:
        if len(section.polygon) < 3:
            return None
        rebuilt.append(section_from_polygon(section.section_id, section.polygon, holes=section.holes))
    return rebuilt


def _validate_sections(candidate: list[RoofSection], previous: list[RoofSection], *, max_area_change_ratio: float) -> tuple[bool, str]:
    try:
        from shapely.geometry import Polygon
    except ImportError:
        return True, ""
    previous_by_id = {section.section_id: section for section in previous}
    geometries = []
    for section in candidate:
        geometry = Polygon(section.polygon, holes=section.holes)
        if not geometry.is_valid or geometry.area <= 1:
            return False, "self-intersection or invalid hole topology"
        prior = previous_by_id.get(section.section_id)
        if prior is not None:
            prior_geometry = Polygon(prior.polygon, holes=prior.holes)
            if prior_geometry.area > 1 and abs(geometry.area - prior_geometry.area) / prior_geometry.area > max_area_change_ratio:
                return False, "single atomic edit changes polygon area too much"
        geometries.append(geometry)
    for index, geometry in enumerate(geometries):
        for other in geometries[index + 1 :]:
            if geometry.intersection(other).area > 1:
                return False, "operation would close a known gap between polygon parts"
    return True, ""


def _copy_sections(sections: list[RoofSection]) -> list[RoofSection]:
    return [section.model_copy(deep=True) for section in sections]


def _vertices(ring: Ring) -> list[dict[str, float]]:
    points = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
    return [{"x": round(float(x), 2), "y": round(float(y), 2)} for x, y in points]


def _point(value: Any, image_size: tuple[int, int]) -> tuple[float, float] | None:
    if not isinstance(value, dict):
        return None
    try:
        x, y = float(value["x"]), float(value["y"])
    except (KeyError, TypeError, ValueError):
        return None
    width, height = image_size
    if not 0 <= x < width or not 0 <= y < height:
        return None
    return x, y


def _points(value: Any, image_size: tuple[int, int]) -> Ring:
    return [point for item in value if (point := _point(item, image_size)) is not None] if isinstance(value, list) else []


def _hole_index(section: RoofSection, raw: dict[str, Any]) -> int | None:
    value = raw.get("hole_id")
    if value is None:
        return None
    text = str(value)
    prefix = f"{section.section_id}:hole:"
    if not text.startswith(prefix):
        return None
    index = _integer(text[len(prefix) :], default=-1)
    return index if 0 <= index < len(section.holes) else None


def _integer(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _operation_record(operation: dict[str, Any], result: str) -> dict[str, Any]:
    record = {
        "op": str(operation.get("op") or operation.get("operation") or ""),
        "polygon_id": operation.get("polygon_id"),
        "hole_id": operation.get("hole_id"),
        "vertex_index": operation.get("vertex_index"),
        "edge_index": operation.get("edge_index"),
        "first_vertex_index": operation.get("first_vertex_index"),
        "second_vertex_index": operation.get("second_vertex_index"),
        "result": result,
    }
    return {key: value for key, value in record.items() if value is not None}
