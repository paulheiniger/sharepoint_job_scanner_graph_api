from __future__ import annotations

from collections import deque

import numpy as np

from .geometry import polygon_area_pixels, polygon_perimeter_pixels, repair_polygon, simplify_ring, snap_axis_aligned_edges
from .models import RoofSection


def clean_mask(mask: np.ndarray, *, minimum_area: int = 25) -> np.ndarray:
    mask_bool = np.asarray(mask, dtype=bool)
    components = connected_components(mask_bool)
    cleaned = np.zeros(mask_bool.shape, dtype=bool)
    for component in components:
        if len(component) >= minimum_area:
            ys, xs = zip(*component)
            cleaned[list(ys), list(xs)] = True
    return cleaned


def connected_components(mask: np.ndarray) -> list[list[tuple[int, int]]]:
    mask_bool = np.asarray(mask, dtype=bool)
    height, width = mask_bool.shape
    visited = np.zeros(mask_bool.shape, dtype=bool)
    components: list[list[tuple[int, int]]] = []
    for y in range(height):
        for x in range(width):
            if not mask_bool[y, x] or visited[y, x]:
                continue
            component: list[tuple[int, int]] = []
            queue: deque[tuple[int, int]] = deque([(y, x)])
            visited[y, x] = True
            while queue:
                cy, cx = queue.popleft()
                component.append((cy, cx))
                for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                    if 0 <= ny < height and 0 <= nx < width and mask_bool[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = True
                        queue.append((ny, nx))
            components.append(component)
    return sorted(components, key=len, reverse=True)


def sections_from_mask(
    mask: np.ndarray,
    *,
    simplification_tolerance: float = 2.0,
    minimum_section_area_pixels: float = 400.0,
    edge_snap_strength: float = 0.0,
) -> list[RoofSection]:
    cleaned = clean_mask(mask, minimum_area=max(1, int(minimum_section_area_pixels / 20)))
    sections: list[RoofSection] = []
    for index, component in enumerate(connected_components(cleaned), start=1):
        area_pixels = float(len(component))
        if area_pixels < minimum_section_area_pixels:
            continue
        ys, xs = zip(*component)
        component_width = max(xs) - min(xs) + 1
        component_height = max(ys) - min(ys) + 1
        effective_tolerance = min(
            max(0.0, float(simplification_tolerance)),
            max(1.0, min(component_width, component_height) * 0.15),
        )
        polygon, holes = polygon_from_component(component)
        polygon = simplify_ring(polygon, effective_tolerance)
        polygon = snap_axis_aligned_edges(polygon, edge_snap_strength)
        minimum_hole_area = max(100.0, float(minimum_section_area_pixels) * 0.5)
        holes = [
            snap_axis_aligned_edges(simplify_ring(hole, effective_tolerance), edge_snap_strength)
            for hole in holes
        ]
        holes = [
            hole
            for hole in holes
            if _is_measurement_hole(hole, minimum_area_pixels=minimum_hole_area)
        ]
        measured_area_pixels = polygon_area_pixels(polygon, holes)
        sections.append(
            RoofSection(
                section_id=f"section-{index}",
                polygon=polygon,
                holes=holes,
                area_pixels=measured_area_pixels,
                perimeter_pixels=polygon_perimeter_pixels(polygon, holes),
                confidence=0.45,
            )
        )
    return sections


def _is_measurement_hole(
    hole: list[tuple[float, float]],
    *,
    minimum_area_pixels: float,
    minimum_span_pixels: float = 6.0,
) -> bool:
    """Keep credible open courtyards while filling mask speckle and roof seams."""
    if not hole or polygon_area_pixels(hole) < minimum_area_pixels:
        return False
    vertices = hole[:-1] if len(hole) > 1 and hole[0] == hole[-1] else hole
    if len(vertices) < 3:
        return False
    width = max(point[0] for point in vertices) - min(point[0] for point in vertices)
    height = max(point[1] for point in vertices) - min(point[1] for point in vertices)
    return min(width, height) >= minimum_span_pixels


def polygon_from_component(component: list[tuple[int, int]]) -> tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]:
    cells = set(component)
    edges: list[tuple[tuple[int, int], tuple[int, int]]] = []
    for y, x in cells:
        if (y - 1, x) not in cells:
            edges.append(((x, y), (x + 1, y)))
        if (y, x + 1) not in cells:
            edges.append(((x + 1, y), (x + 1, y + 1)))
        if (y + 1, x) not in cells:
            edges.append(((x + 1, y + 1), (x, y + 1)))
        if (y, x - 1) not in cells:
            edges.append(((x, y + 1), (x, y)))
    loops = _loops_from_edges(edges)
    if not loops:
        ys, xs = zip(*component)
        x0, x1 = min(xs), max(xs) + 1
        y0, y1 = min(ys), max(ys) + 1
        return repair_polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)]), []
    repaired = [repair_polygon([(float(x), float(y)) for x, y in loop]) for loop in loops]
    repaired = [loop for loop in repaired if loop]
    if not repaired:
        return [], []
    outer = max(repaired, key=lambda ring: polygon_area_pixels(ring))
    holes = [ring for ring in repaired if ring is not outer and polygon_area_pixels(ring) > 0]
    return outer, holes


def _loops_from_edges(
    edges: list[tuple[tuple[int, int], tuple[int, int]]],
) -> list[list[tuple[int, int]]]:
    starts: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for start, end in edges:
        starts.setdefault(start, []).append(end)
    loops: list[list[tuple[int, int]]] = []
    while starts:
        start = next(iter(starts))
        first_candidates = starts[start]
        first_end = first_candidates.pop()
        if not first_candidates:
            starts.pop(start)
        current = first_end
        previous_direction = _edge_direction(start, first_end)
        loop = [start, first_end]
        guard = 0
        while guard <= len(edges) + 1:
            guard += 1
            next_points = starts.get(current)
            if not next_points:
                break
            next_point = _choose_boundary_edge(current, next_points, previous_direction)
            next_points.remove(next_point)
            if not next_points:
                starts.pop(current, None)
            loop.append(next_point)
            previous_direction = _edge_direction(current, next_point)
            current = next_point
            if current == start:
                break
        if len(loop) >= 4 and loop[0] == loop[-1]:
            loops.append(loop)
    return loops


def _edge_direction(start: tuple[int, int], end: tuple[int, int]) -> int:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    if (dx, dy) == (1, 0):
        return 0  # East
    if (dx, dy) == (0, 1):
        return 1  # South
    if (dx, dy) == (-1, 0):
        return 2  # West
    if (dx, dy) == (0, -1):
        return 3  # North
    raise ValueError(f"Expected a unit grid edge, received {start!r} -> {end!r}.")


def _choose_boundary_edge(
    start: tuple[int, int],
    candidates: list[tuple[int, int]],
    previous_direction: int,
) -> tuple[int, int]:
    """Keep filled pixels on the right while walking clockwise mask contours."""
    priorities = {1: 0, 0: 1, 3: 2, 2: 3}
    return min(
        candidates,
        key=lambda end: priorities[(_edge_direction(start, end) - previous_direction) % 4],
    )


def section_from_polygon(section_id: str, polygon: list[tuple[float, float]], holes: list[list[tuple[float, float]]] | None = None) -> RoofSection:
    repaired = repair_polygon(polygon)
    holes = holes or []
    return RoofSection(
        section_id=section_id,
        polygon=repaired,
        holes=holes,
        area_pixels=polygon_area_pixels(repaired, holes),
        perimeter_pixels=polygon_perimeter_pixels(repaired, holes),
        confidence=0.8,
    )
