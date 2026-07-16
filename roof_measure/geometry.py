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
    simplified = _rdp(ring[:-1], tolerance)
    return repair_polygon(simplified)


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

