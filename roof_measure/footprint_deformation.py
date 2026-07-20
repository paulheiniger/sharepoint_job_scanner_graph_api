from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw

from .models import Ring


@dataclass
class DeformedFootprintCandidate:
    polygons: list[Ring] = field(default_factory=list)
    aligned_polygons: list[Ring] = field(default_factory=list)
    translation: tuple[float, float] = (0.0, 0.0)
    edge_offsets: list[list[float]] = field(default_factory=list)
    sam_support: float = 0.0
    sam_coverage: float = 0.0
    topology_score: float = 0.0
    score: float = 0.0
    accepted: bool = False
    reason: str = ""


def deform_footprints_to_roof_support(
    polygons: list[Ring],
    *,
    image: np.ndarray,
    sam_mask: np.ndarray | None,
    max_translation_pixels: int = 12,
    max_edge_offset_pixels: int = 12,
) -> DeformedFootprintCandidate:
    """Preserve footprint topology while moving only well-supported exterior edges."""
    if sam_mask is None or not polygons:
        return DeformedFootprintCandidate(reason="requires a SAM mask and selected footprint polygons")
    mask = np.asarray(sam_mask, dtype=bool)
    if mask.ndim != 2 or not mask.any():
        return DeformedFootprintCandidate(reason="requires a non-empty SAM mask")
    source = [_open_ring(polygon) for polygon in polygons if len(_open_ring(polygon)) >= 3]
    if not source:
        return DeformedFootprintCandidate(reason="no valid footprint rings")

    translation, aligned = _best_translation(source, mask, max_translation_pixels)
    luminance = _luminance(image, mask.shape)
    deformed: list[Ring] = []
    edge_offsets: list[list[float]] = []
    for ring in aligned:
        candidate, offsets = _deform_ring(ring, mask, luminance, max_edge_offset_pixels)
        deformed.append(candidate)
        edge_offsets.append(offsets)

    if not _preserves_topology(aligned, deformed):
        return DeformedFootprintCandidate(
            aligned_polygons=aligned,
            translation=translation,
            edge_offsets=edge_offsets,
            reason="edge deformation would close or overlap separate footprint parts",
        )

    candidate_mask = _polygons_mask(mask.shape, deformed)
    sam_support, sam_coverage = _mask_support(candidate_mask, mask)
    mean_displacement = float(np.mean([abs(offset) for offsets in edge_offsets for offset in offsets])) if edge_offsets else 0.0
    topology_score = 1.0
    score = 0.55 * sam_support + 0.25 * sam_coverage + 0.12 * topology_score + 0.08 * max(0.0, 1.0 - mean_displacement / max(max_edge_offset_pixels, 1))
    per_part_support = [_mask_support(_polygons_mask(mask.shape, [polygon]), mask)[0] for polygon in deformed]
    accepted = bool(score >= 0.62 and all(value >= 0.42 for value in per_part_support))
    return DeformedFootprintCandidate(
        polygons=deformed,
        aligned_polygons=aligned,
        translation=translation,
        edge_offsets=edge_offsets,
        sam_support=round(sam_support, 4),
        sam_coverage=round(sam_coverage, 4),
        topology_score=topology_score,
        score=round(score, 4),
        accepted=accepted,
        reason="topology-preserving footprint deformation" if accepted else "insufficient SAM support for a topology-preserving footprint candidate",
    )


def _best_translation(polygons: list[Ring], mask: np.ndarray, maximum: int) -> tuple[tuple[float, float], list[Ring]]:
    best_score = -1.0
    best_translation = (0.0, 0.0)
    best_polygons = polygons
    for dy in range(-maximum, maximum + 1, 3):
        for dx in range(-maximum, maximum + 1, 3):
            translated = [[(x + dx, y + dy) for x, y in polygon] for polygon in polygons]
            footprint = _polygons_mask(mask.shape, translated)
            support, coverage = _mask_support(footprint, mask)
            score = 0.6 * support + 0.4 * coverage - 0.01 * (abs(dx) + abs(dy)) / max(maximum, 1)
            if score > best_score:
                best_score = score
                best_translation = (float(dx), float(dy))
                best_polygons = translated
    return best_translation, best_polygons


def _deform_ring(ring: Ring, mask: np.ndarray, luminance: np.ndarray, maximum: int) -> tuple[Ring, list[float]]:
    offsets: list[float] = []
    for index, start in enumerate(ring):
        end = ring[(index + 1) % len(ring)]
        outward = _outward_normal(ring, start, end)
        baseline = _edge_evidence(start, end, outward, 0.0, mask, luminance, maximum)
        options = [float(value) for value in range(-maximum, maximum + 1, 3)]
        best_offset = 0.0
        best_score = baseline
        for offset in options:
            score = _edge_evidence(start, end, outward, offset, mask, luminance, maximum)
            if score > best_score:
                best_offset, best_score = offset, score
        # Avoid interpreting a hard shadow gradient as a reason to move an edge.
        offsets.append(best_offset if best_score >= baseline + 0.08 else 0.0)
    candidate = _intersect_shifted_edges(ring, offsets)
    return candidate if _is_valid_ring(candidate) else ring, offsets


def _outward_normal(ring: Ring, start: tuple[float, float], end: tuple[float, float]) -> np.ndarray:
    edge = np.asarray((end[0] - start[0], end[1] - start[1]), dtype=float)
    norm = float(np.linalg.norm(edge))
    if norm == 0:
        return np.zeros(2)
    normal = np.asarray((-edge[1] / norm, edge[0] / norm), dtype=float)
    midpoint = np.asarray(((start[0] + end[0]) / 2, (start[1] + end[1]) / 2), dtype=float)
    centroid = np.asarray(np.mean(np.asarray(ring, dtype=float), axis=0), dtype=float)
    return normal if float(np.dot(normal, midpoint - centroid)) >= 0 else -normal


def _edge_evidence(
    start: tuple[float, float],
    end: tuple[float, float],
    outward: np.ndarray,
    offset: float,
    mask: np.ndarray,
    luminance: np.ndarray,
    maximum: int,
) -> float:
    midpoint = np.asarray(((start[0] + end[0]) / 2, (start[1] + end[1]) / 2), dtype=float) + outward * offset
    inside = _sample_disk(mask, midpoint - outward * 5.0, radius=4)
    outside = _sample_disk(mask, midpoint + outward * 5.0, radius=4)
    edge_delta = abs(_sample_disk(luminance, midpoint - outward * 2.0, radius=2).mean() - _sample_disk(luminance, midpoint + outward * 2.0, radius=2).mean())
    roof_support = float(inside.mean()) if inside.size else 0.0
    ground_exclusion = 1.0 - float(outside.mean()) if outside.size else 0.0
    image_edge = min(edge_delta / 80.0, 1.0)
    displacement = abs(offset) / max(float(maximum), 1.0)
    return 0.55 * roof_support + 0.25 * ground_exclusion + 0.10 * image_edge + 0.10 * (1.0 - displacement)


def _intersect_shifted_edges(ring: Ring, offsets: list[float]) -> Ring:
    shifted: list[tuple[np.ndarray, np.ndarray]] = []
    for index, start in enumerate(ring):
        end = ring[(index + 1) % len(ring)]
        normal = _outward_normal(ring, start, end)
        shifted.append((np.asarray(start, dtype=float) + normal * offsets[index], np.asarray(end, dtype=float) + normal * offsets[index]))
    vertices: Ring = []
    for index in range(len(shifted)):
        previous = shifted[index - 1]
        current = shifted[index]
        intersection = _line_intersection(previous[0], previous[1], current[0], current[1])
        fallback = (previous[1] + current[0]) / 2.0
        point = intersection if intersection is not None else fallback
        vertices.append((float(point[0]), float(point[1])))
    return vertices


def _line_intersection(a: np.ndarray, b: np.ndarray, c: np.ndarray, d: np.ndarray) -> np.ndarray | None:
    first = b - a
    second = d - c
    determinant = first[0] * second[1] - first[1] * second[0]
    if abs(float(determinant)) < 1e-6:
        return None
    delta = c - a
    t = (delta[0] * second[1] - delta[1] * second[0]) / determinant
    return a + t * first


def _preserves_topology(source: list[Ring], candidate: list[Ring]) -> bool:
    try:
        from shapely.geometry import Polygon
    except ImportError:
        return False
    source_shapes = [Polygon(ring) for ring in source]
    candidate_shapes = [Polygon(ring) for ring in candidate]
    if any(not shape.is_valid or shape.is_empty or shape.area <= 0 for shape in candidate_shapes):
        return False
    for index, first in enumerate(candidate_shapes):
        for other_index in range(index + 1, len(candidate_shapes)):
            original_overlap = source_shapes[index].intersection(source_shapes[other_index]).area
            candidate_overlap = first.intersection(candidate_shapes[other_index]).area
            if original_overlap < 1.0 and candidate_overlap > 1.0:
                return False
    return True


def _is_valid_ring(ring: Ring) -> bool:
    try:
        from shapely.geometry import Polygon
    except ImportError:
        return False
    shape = Polygon(ring)
    return bool(shape.is_valid and not shape.is_empty and shape.area > 0)


def _polygons_mask(shape: tuple[int, int], polygons: list[Ring]) -> np.ndarray:
    canvas = Image.new("L", (shape[1], shape[0]), 0)
    draw = ImageDraw.Draw(canvas)
    for polygon in polygons:
        if len(polygon) >= 3:
            draw.polygon(polygon, fill=255)
    return np.asarray(canvas, dtype=bool)


def _mask_support(candidate: np.ndarray, mask: np.ndarray) -> tuple[float, float]:
    intersection = float((candidate & mask).sum())
    support = intersection / max(float(candidate.sum()), 1.0)
    coverage = intersection / max(float(mask.sum()), 1.0)
    return support, coverage


def _luminance(image: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    array = np.asarray(image)
    if array.shape[:2] != shape:
        array = np.asarray(Image.fromarray(array.astype(np.uint8)).resize((shape[1], shape[0])))
    if array.ndim == 2:
        return array.astype(float)
    return (0.2126 * array[..., 0] + 0.7152 * array[..., 1] + 0.0722 * array[..., 2]).astype(float)


def _sample_disk(array: np.ndarray, point: np.ndarray, *, radius: int) -> np.ndarray:
    x, y = int(round(float(point[0]))), int(round(float(point[1])))
    x0, x1 = max(0, x - radius), min(array.shape[1], x + radius + 1)
    y0, y1 = max(0, y - radius), min(array.shape[0], y + radius + 1)
    return array[y0:y1, x0:x1]


def _open_ring(polygon: Ring) -> Ring:
    return polygon[:-1] if len(polygon) > 1 and polygon[0] == polygon[-1] else polygon
