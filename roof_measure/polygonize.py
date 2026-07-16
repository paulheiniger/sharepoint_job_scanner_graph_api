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
        x0, x1 = min(xs), max(xs) + 1
        y0, y1 = min(ys), max(ys) + 1
        polygon = repair_polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
        polygon = simplify_ring(polygon, simplification_tolerance)
        polygon = snap_axis_aligned_edges(polygon, edge_snap_strength)
        sections.append(
            RoofSection(
                section_id=f"section-{index}",
                polygon=polygon,
                area_pixels=area_pixels,
                perimeter_pixels=polygon_perimeter_pixels(polygon),
                confidence=0.45,
            )
        )
    return sections


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

