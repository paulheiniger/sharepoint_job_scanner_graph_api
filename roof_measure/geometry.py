from __future__ import annotations

import math
from typing import Any

from .models import Point, Ring


def close_ring(points: Ring) -> Ring:
    if not points:
        return []
    out = [(float(x), float(y)) for x, y in points]
    if out[0] != out[-1]:
        out.append(out[0])
    return out


def polygon_area_pixels(polygon: Ring, holes: list[Ring] | None = None) -> float:
    area = abs(_signed_area(close_ring(polygon)))
    for hole in holes or []:
        area -= abs(_signed_area(close_ring(hole)))
    return max(area, 0.0)


def polygon_perimeter_pixels(polygon: Ring, holes: list[Ring] | None = None) -> float:
    perimeter = ring_length(close_ring(polygon))
    for hole in holes or []:
        perimeter += ring_length(close_ring(hole))
    return perimeter


def ring_length(points: Ring) -> float:
    ring = close_ring(points)
    return sum(math.hypot(ring[index + 1][0] - ring[index][0], ring[index + 1][1] - ring[index][1]) for index in range(len(ring) - 1))


def _signed_area(points: Ring) -> float:
    if len(points) < 4:
        return 0.0
    return 0.5 * sum(
        points[index][0] * points[index + 1][1] - points[index + 1][0] * points[index][1]
        for index in range(len(points) - 1)
    )


def repair_polygon(points: Ring) -> Ring:
    repaired = close_ring([(float(x), float(y)) for x, y in points])
    unique = []
    for point in repaired:
        if not unique or point != unique[-1]:
            unique.append(point)
    if len(unique) >= 2 and unique[0] != unique[-1]:
        unique.append(unique[0])
    if len(unique) < 4:
        return []
    return unique


def simplify_ring(points: Ring, tolerance: float) -> Ring:
    ring = repair_polygon(points)
    if len(ring) <= 5 or tolerance <= 0:
        return ring
    vertices = ring[:-1]
    while len(vertices) > 3:
        distances = [
            _perpendicular_distance(vertex, vertices[index - 1], vertices[(index + 1) % len(vertices)])
            for index, vertex in enumerate(vertices)
        ]
        smallest_distance = min(distances)
        if smallest_distance > tolerance:
            break
        vertices.pop(distances.index(smallest_distance))
    return repair_polygon(vertices)


def _rdp(points: Ring, tolerance: float) -> Ring:
    if len(points) < 3:
        return points
    first = points[0]
    last = points[-1]
    max_distance = -1.0
    split_index = 0
    for index in range(1, len(points) - 1):
        distance = _perpendicular_distance(points[index], first, last)
        if distance > max_distance:
            max_distance = distance
            split_index = index
    if max_distance > tolerance:
        left = _rdp(points[: split_index + 1], tolerance)
        right = _rdp(points[split_index:], tolerance)
        return left[:-1] + right
    return [first, last]


def _perpendicular_distance(point: Point, line_start: Point, line_end: Point) -> float:
    x, y = point
    x1, y1 = line_start
    x2, y2 = line_end
    denominator = math.hypot(x2 - x1, y2 - y1)
    if denominator == 0:
        return math.hypot(x - x1, y - y1)
    return abs((y2 - y1) * x - (x2 - x1) * y + x2 * y1 - y2 * x1) / denominator


def snap_axis_aligned_edges(points: Ring, strength: float = 0.0) -> Ring:
    ring = repair_polygon(points)
    if strength <= 0 or len(ring) < 4:
        return ring
    snapped = ring[:]
    threshold = max(float(strength), 0.0)
    for index in range(len(snapped) - 1):
        x1, y1 = snapped[index]
        x2, y2 = snapped[index + 1]
        if abs(x2 - x1) <= threshold:
            average = (x1 + x2) / 2
            snapped[index] = (average, y1)
            snapped[index + 1] = (average, y2)
        elif abs(y2 - y1) <= threshold:
            average = (y1 + y2) / 2
            snapped[index] = (x1, average)
            snapped[index + 1] = (x2, average)
    return repair_polygon(snapped)


def straighten_architectural_ring(
    points: Ring,
    *,
    simplification_tolerance: float = 12.0,
    angle_tolerance_degrees: float = 20.0,
    max_area_drift: float = 0.03,
) -> Ring:
    """Fit near-orthogonal roof edges to the ring's dominant building axes."""
    ring = simplify_ring(points, simplification_tolerance)
    vertices = ring[:-1]
    if len(vertices) < 4:
        return ring
    original_area = polygon_area_pixels(ring)
    if original_area <= 0:
        return ring

    tolerance = math.radians(max(0.0, min(float(angle_tolerance_degrees), 44.0)))
    dominant_angle = _dominant_orthogonal_angle(vertices)
    vertices = _collapse_same_axis_vertices(vertices, dominant_angle, tolerance)
    if len(vertices) < 4:
        return ring
    dominant_angle = _dominant_orthogonal_angle(vertices)
    fitted_lines: list[tuple[Point, float]] = []
    classifications: list[int | None] = []
    for index, start in enumerate(vertices):
        end = vertices[(index + 1) % len(vertices)]
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = math.hypot(dx, dy)
        if length <= 1e-6:
            return ring
        angle = math.atan2(dy, dx)
        axis_index, difference = _nearest_orthogonal_axis(angle, dominant_angle)
        if difference > tolerance:
            direction = (dx / length, dy / length)
            classifications.append(None)
        else:
            axis_angle = dominant_angle + axis_index * math.pi / 2
            direction = (math.cos(axis_angle), math.sin(axis_angle))
            classifications.append(axis_index)
        normal = (-direction[1], direction[0])
        midpoint = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
        fitted_lines.append((normal, normal[0] * midpoint[0] + normal[1] * midpoint[1]))

    if sum(value is not None for value in classifications) < max(3, len(vertices) // 2):
        return ring

    fitted_vertices: Ring = []
    for index, original_vertex in enumerate(vertices):
        previous_line = fitted_lines[index - 1]
        next_line = fitted_lines[index]
        intersection = _line_intersection(previous_line, next_line)
        fitted_vertices.append(intersection or original_vertex)
    fitted = repair_polygon(fitted_vertices)
    fitted_area = polygon_area_pixels(fitted)
    if not fitted or fitted_area <= 0:
        return ring

    drift = abs(fitted_area - original_area) / original_area
    if drift > max(0.0, float(max_area_drift)):
        fitted = _scale_ring_to_area(fitted, target_area=original_area)
        fitted_area = polygon_area_pixels(fitted)
        drift = abs(fitted_area - original_area) / original_area if fitted_area > 0 else math.inf
    return fitted if drift <= max(0.0, float(max_area_drift)) else ring


def _collapse_same_axis_vertices(vertices: Ring, dominant_angle: float, tolerance: float) -> Ring:
    kept: Ring = []
    count = len(vertices)
    for index, vertex in enumerate(vertices):
        previous = vertices[(index - 1) % count]
        following = vertices[(index + 1) % count]
        incoming_angle = math.atan2(vertex[1] - previous[1], vertex[0] - previous[0])
        outgoing_angle = math.atan2(following[1] - vertex[1], following[0] - vertex[0])
        incoming_axis, incoming_difference = _nearest_orthogonal_axis(incoming_angle, dominant_angle)
        outgoing_axis, outgoing_difference = _nearest_orthogonal_axis(outgoing_angle, dominant_angle)
        if (
            incoming_axis == outgoing_axis
            and incoming_difference <= tolerance
            and outgoing_difference <= tolerance
        ):
            continue
        kept.append(vertex)
    return kept


def _dominant_orthogonal_angle(vertices: Ring) -> float:
    sin_sum = 0.0
    cos_sum = 0.0
    for index, start in enumerate(vertices):
        end = vertices[(index + 1) % len(vertices)]
        dx, dy = end[0] - start[0], end[1] - start[1]
        weight = math.hypot(dx, dy)
        angle = math.atan2(dy, dx)
        sin_sum += weight * math.sin(4 * angle)
        cos_sum += weight * math.cos(4 * angle)
    return (math.atan2(sin_sum, cos_sum) / 4) % (math.pi / 2)


def _nearest_orthogonal_axis(angle: float, dominant_angle: float) -> tuple[int, float]:
    candidates = [dominant_angle, dominant_angle + math.pi / 2]
    differences = [abs((angle - candidate + math.pi / 2) % math.pi - math.pi / 2) for candidate in candidates]
    axis_index = 0 if differences[0] <= differences[1] else 1
    return axis_index, differences[axis_index]


def _line_intersection(
    first: tuple[Point, float],
    second: tuple[Point, float],
) -> Point | None:
    (a1, b1), c1 = first
    (a2, b2), c2 = second
    determinant = a1 * b2 - a2 * b1
    if abs(determinant) <= 1e-8:
        return None
    return (c1 * b2 - c2 * b1) / determinant, (a1 * c2 - a2 * c1) / determinant


def _scale_ring_to_area(points: Ring, *, target_area: float) -> Ring:
    ring = repair_polygon(points)
    current_area = polygon_area_pixels(ring)
    vertices = ring[:-1]
    if current_area <= 0 or not vertices:
        return ring
    center_x = sum(point[0] for point in vertices) / len(vertices)
    center_y = sum(point[1] for point in vertices) / len(vertices)
    scale = math.sqrt(target_area / current_area)
    return repair_polygon(
        [
            (center_x + (x - center_x) * scale, center_y + (y - center_y) * scale)
            for x, y in vertices
        ]
    )


def polygon_to_geojson_feature(polygon: Ring, holes: list[Ring] | None = None, properties: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "type": "Feature",
        "properties": properties or {},
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [[float(x), float(y)] for x, y in close_ring(polygon)],
                *[
                    [[float(x), float(y)] for x, y in close_ring(hole)]
                    for hole in (holes or [])
                ],
            ],
        },
    }


def feature_collection(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}
