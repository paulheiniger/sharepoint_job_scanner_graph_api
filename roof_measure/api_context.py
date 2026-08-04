from __future__ import annotations

import json
import math
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from .calibration import feet_from_pixels, sqft_from_pixels
from .geometry import polygon_area_pixels, polygon_perimeter_pixels, repair_polygon
from .map_reference import (
    BuildingFootprint,
    MapboxReferenceProvider,
    footprint_rings_to_image_pixels,
    kyfromabove_lidar_coverage,
    microsoft_global_building_footprints,
    postgres_building_footprints,
)
from .visualization import image_png_bytes


CONTEXT_ID_RE = re.compile(r"^[a-f0-9]{32}$")
ROOF_ASSET_NAMES = frozenset({"satellite.png", "footprint-overlay.png"})
VIEW_ZOOM = {"whole_site": 17.5, "building_detail": 19.0}
MAX_FOOTPRINT_CANDIDATES = 12


class RoofMeasureContextError(RuntimeError):
    pass


class RoofMeasureContextExpiredError(RoofMeasureContextError):
    pass


class RoofMeasureInputError(ValueError):
    pass


def create_roof_measure_context(
    *,
    address: str,
    job_id: str,
    view: str,
    include_lidar_coverage: bool,
    mapbox_token: str,
    database_url: str,
    artifact_dir: Path,
    expires_at: int,
) -> dict[str, Any]:
    token = str(mapbox_token or "").strip()
    if not token:
        raise RoofMeasureContextError("Mapbox imagery is not configured.")
    zoom = VIEW_ZOOM.get(view)
    if zoom is None:
        raise RoofMeasureInputError(f"Unknown map view: {view}")

    provider = MapboxReferenceProvider(token)
    fetched = provider.static_satellite_image(
        address,
        zoom=zoom,
        width=1280,
        height=1280,
    )
    if (
        not fetched.ok
        or not fetched.image_bytes
        or fetched.latitude is None
        or fetched.longitude is None
        or not fetched.pixels_per_foot
    ):
        raise RoofMeasureContextError(
            fetched.warning or "Calibrated satellite imagery was unavailable."
        )

    image = _load_satellite_image(fetched.image_bytes)
    latitude = float(fetched.latitude)
    longitude = float(fetched.longitude)
    resolved_zoom = float(fetched.zoom or zoom)
    pixels_per_foot = float(fetched.pixels_per_foot)
    warnings: list[str] = []
    attributions = [fetched.attribution]

    lookups = [
        postgres_building_footprints(
            latitude=latitude,
            longitude=longitude,
            radius_meters=500,
            limit=100,
            database_url=database_url,
        ),
        provider.building_footprints(
            latitude=latitude,
            longitude=longitude,
            radius_meters=500,
            limit=50,
        ),
    ]
    raw_candidates: list[dict[str, Any]] = []
    for lookup in lookups:
        if lookup.attribution and lookup.attribution not in attributions:
            attributions.append(lookup.attribution)
        if lookup.warning:
            warnings.append(lookup.warning)
        if not lookup.ok:
            continue
        for footprint in lookup.footprints:
            candidate = _candidate_from_footprint(
                footprint,
                latitude=latitude,
                longitude=longitude,
                zoom=resolved_zoom,
                width=image.width,
                height=image.height,
                pixels_per_foot=pixels_per_foot,
            )
            if candidate is not None:
                raw_candidates.append(candidate)

    if not raw_candidates:
        microsoft_lookup = microsoft_global_building_footprints(
            latitude=latitude,
            longitude=longitude,
            radius_meters=500,
            limit=100,
        )
        if microsoft_lookup.attribution not in attributions:
            attributions.append(microsoft_lookup.attribution)
        if microsoft_lookup.warning:
            warnings.append(microsoft_lookup.warning)
        if microsoft_lookup.ok:
            for footprint in microsoft_lookup.footprints:
                candidate = _candidate_from_footprint(
                    footprint,
                    latitude=latitude,
                    longitude=longitude,
                    zoom=resolved_zoom,
                    width=image.width,
                    height=image.height,
                    pixels_per_foot=pixels_per_foot,
                )
                if candidate is not None:
                    raw_candidates.append(candidate)

    ranked = sorted(
        raw_candidates,
        key=lambda item: (
            float(item["center_distance_pixels"]),
            _provider_priority(str(item["provider"])),
            -float(item["plan_area_sqft"]),
        ),
    )
    candidates = _deduplicate_candidates(ranked)[:MAX_FOOTPRINT_CANDIDATES]
    for index, candidate in enumerate(candidates, start=1):
        candidate["footprint_id"] = f"fp-{index:02d}"
        candidate.pop("center_distance_pixels", None)
        candidate.pop("center_x", None)
        candidate.pop("center_y", None)
        candidate.pop("source_footprint_id", None)

    if not candidates:
        warnings.append(
            "No complete building footprint was visible in the selected image. "
            "Try whole_site view or submit a reviewed custom polygon."
        )

    lidar = {
        "available": False,
        "provider": "kyfromabove",
        "attribution": "Kentucky From Above public LiDAR.",
    }
    if include_lidar_coverage:
        coverage = kyfromabove_lidar_coverage(
            latitude=latitude,
            longitude=longitude,
        )
        lidar.update(
            {
                "available": bool(coverage.ok),
                "collection": coverage.collection,
                "captured_at": coverage.captured_at,
                "point_count": coverage.point_count,
                "provider": coverage.provider,
                "attribution": coverage.attribution,
                "warning": coverage.warning,
            }
        )
        if coverage.warning:
            warnings.append(coverage.warning)

    context_id = uuid.uuid4().hex
    context_path = _context_path(artifact_dir, context_id)
    context_path.mkdir(parents=True, exist_ok=False)
    (context_path / "satellite.png").write_bytes(image_png_bytes(image))
    overlay = _footprint_overlay(image, candidates)
    (context_path / "footprint-overlay.png").write_bytes(image_png_bytes(overlay))

    context = {
        "schema_version": "spraytec.roof_measure_context.v1",
        "context_id": context_id,
        "created_at": int(time.time()),
        "expires_at": int(expires_at),
        "address": address.strip(),
        "job_id": job_id.strip(),
        "latitude": latitude,
        "longitude": longitude,
        "zoom": resolved_zoom,
        "image_width": image.width,
        "image_height": image.height,
        "pixels_per_foot": pixels_per_foot,
        "footprint_candidates": candidates,
        "lidar_coverage": lidar,
        "attributions": _unique_nonempty(attributions),
        "warnings": _unique_nonempty(warnings),
    }
    _write_json_atomic(context_path / "context.json", context)
    return context


def calculate_roof_measurement(
    *,
    context_id: str,
    selected_footprint_ids: list[str],
    sections: list[dict[str, Any]],
    pitch_rise_per_12: float | None,
    artifact_dir: Path,
) -> dict[str, Any]:
    context = load_roof_measure_context(
        context_id=context_id,
        artifact_dir=artifact_dir,
    )
    width = int(context["image_width"])
    height = int(context["image_height"])
    pixels_per_foot = float(context["pixels_per_foot"])
    candidate_by_id = {
        str(candidate["footprint_id"]): candidate
        for candidate in context.get("footprint_candidates") or []
    }

    measurement_sections: list[dict[str, Any]] = []
    if selected_footprint_ids:
        missing = [item for item in selected_footprint_ids if item not in candidate_by_id]
        if missing:
            raise RoofMeasureInputError(
                "Unknown footprint candidate IDs: " + ", ".join(missing)
            )
        for footprint_id in selected_footprint_ids:
            candidate = candidate_by_id[footprint_id]
            measurement_sections.append(
                _measure_components(
                    section_id=footprint_id,
                    source="footprint",
                    components=candidate["components"],
                    pixels_per_foot=pixels_per_foot,
                    width=width,
                    height=height,
                    pitch_rise_per_12=pitch_rise_per_12,
                )
            )
    else:
        for section in sections:
            measurement_sections.append(
                _measure_components(
                    section_id=str(section["section_id"]),
                    source="custom_polygon",
                    components=[
                        {
                            "polygon": section["polygon"],
                            "holes": section.get("holes") or [],
                        }
                    ],
                    pixels_per_foot=pixels_per_foot,
                    width=width,
                    height=height,
                    pitch_rise_per_12=pitch_rise_per_12,
                )
            )

    total_plan = sum(float(item["plan_area_sqft"]) for item in measurement_sections)
    total_perimeter = sum(float(item["perimeter_ft"]) for item in measurement_sections)
    total_surface = (
        sum(float(item["surface_area_sqft"]) for item in measurement_sections)
        if pitch_rise_per_12 is not None
        else None
    )
    warnings = [
        "Building footprints are evidence, not a surveyed roof boundary. Verify "
        "roof edges, overhangs, canopies, penetrations, and excluded areas before quoting."
    ]
    assumptions = [
        "Area and perimeter use the address-calibrated, north-up Mapbox image scale.",
        "No AI model, SAM2 service, or OpenAI call was used by the API.",
    ]
    if pitch_rise_per_12 is None:
        warnings.append(
            "Surface area is not calculated because roof pitch was not supplied; "
            "total_plan_area_sqft is horizontal plan-view area."
        )
    else:
        assumptions.append(
            f"A uniform {pitch_rise_per_12:g}:12 pitch was applied to every section."
        )

    return {
        "schema_version": "spraytec.roof_measure_calculation.v1",
        "context_id": context_id,
        "measurement_basis": "address_calibrated_satellite_plan_view",
        "total_plan_area_sqft": round(total_plan, 1),
        "total_perimeter_ft": round(total_perimeter, 1),
        "pitch_rise_per_12": pitch_rise_per_12,
        "total_surface_area_sqft": (
            round(total_surface, 1) if total_surface is not None else None
        ),
        "sections": measurement_sections,
        "review_status": "requires_estimator_verification",
        "assumptions": assumptions,
        "warnings": warnings,
    }


def load_roof_measure_context(*, context_id: str, artifact_dir: Path) -> dict[str, Any]:
    if not CONTEXT_ID_RE.fullmatch(str(context_id or "")):
        raise RoofMeasureInputError("Invalid roof measurement context ID.")
    path = _context_path(artifact_dir, context_id) / "context.json"
    try:
        context = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RoofMeasureContextError("Roof measurement context was not found.") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RoofMeasureContextError("Roof measurement context could not be read.") from exc
    if int(context.get("expires_at") or 0) < int(time.time()):
        raise RoofMeasureContextExpiredError("Roof measurement context has expired.")
    return context


def resolve_roof_measure_asset(
    *,
    context_id: str,
    asset_name: str,
    artifact_dir: Path,
) -> Path:
    if asset_name not in ROOF_ASSET_NAMES:
        raise RoofMeasureInputError("Unknown roof measurement asset.")
    load_roof_measure_context(context_id=context_id, artifact_dir=artifact_dir)
    path = _context_path(artifact_dir, context_id) / asset_name
    if not path.is_file():
        raise RoofMeasureContextError("Roof measurement asset was not found.")
    return path


def _candidate_from_footprint(
    footprint: BuildingFootprint,
    *,
    latitude: float,
    longitude: float,
    zoom: float,
    width: int,
    height: int,
    pixels_per_foot: float,
) -> dict[str, Any] | None:
    pixel_rings = footprint_rings_to_image_pixels(
        footprint.rings,
        center_latitude=latitude,
        center_longitude=longitude,
        zoom=zoom,
        width=width,
        height=height,
    )
    components = _components_from_rings(pixel_rings)
    if not components:
        return None
    all_points = [
        point
        for component in components
        for ring in [component["polygon"], *(component.get("holes") or [])]
        for point in ring
    ]
    if not all_points or any(
        point["x"] < 0
        or point["x"] > width
        or point["y"] < 0
        or point["y"] > height
        for point in all_points
    ):
        return None
    area_pixels, perimeter_pixels = _component_totals(components)
    plan_area = sqft_from_pixels(area_pixels, pixels_per_foot)
    perimeter = feet_from_pixels(perimeter_pixels, pixels_per_foot)
    if not plan_area or not perimeter or plan_area < 25:
        return None
    center_x = sum(float(point["x"]) for point in all_points) / len(all_points)
    center_y = sum(float(point["y"]) for point in all_points) / len(all_points)
    return {
        "footprint_id": "",
        "source_footprint_id": footprint.footprint_id,
        "label": footprint.label,
        "provider": footprint.provider,
        "plan_area_sqft": round(plan_area, 1),
        "perimeter_ft": round(perimeter, 1),
        "components": components,
        "center_distance_pixels": math.hypot(center_x - width / 2, center_y - height / 2),
        "center_x": center_x,
        "center_y": center_y,
    }


def _components_from_rings(
    rings: list[list[tuple[float, float]]],
) -> list[dict[str, Any]]:
    repaired = [repair_polygon(ring) for ring in rings]
    repaired = [
        ring
        for ring in repaired
        if polygon_area_pixels(ring) >= 4 and not _ring_self_intersects(ring)
    ]
    if not repaired:
        return []
    order = sorted(range(len(repaired)), key=lambda index: -polygon_area_pixels(repaired[index]))
    parents: dict[int, int | None] = {}
    for position, index in enumerate(order):
        point = repaired[index][0]
        containers = [
            other
            for other in order[:position]
            if _point_in_ring(point, repaired[other])
        ]
        parents[index] = min(
            containers,
            key=lambda other: polygon_area_pixels(repaired[other]),
            default=None,
        )

    def depth(index: int) -> int:
        parent = parents[index]
        return 0 if parent is None else depth(parent) + 1

    components: list[dict[str, Any]] = []
    component_by_outer: dict[int, dict[str, Any]] = {}
    for index in order:
        if depth(index) % 2 == 0:
            component = {
                "polygon": _point_dicts(repaired[index]),
                "holes": [],
            }
            component_by_outer[index] = component
            components.append(component)
            continue
        parent = parents[index]
        while parent is not None and depth(parent) % 2 != 0:
            parent = parents[parent]
        if parent is not None and parent in component_by_outer:
            component_by_outer[parent]["holes"].append(_point_dicts(repaired[index]))
    return components


def _measure_components(
    *,
    section_id: str,
    source: str,
    components: list[dict[str, Any]],
    pixels_per_foot: float,
    width: int,
    height: int,
    pitch_rise_per_12: float | None,
) -> dict[str, Any]:
    normalized: list[dict[str, Any]] = []
    for component in components:
        polygon = _ring_tuples(component.get("polygon") or [])
        holes = [_ring_tuples(hole) for hole in component.get("holes") or []]
        if (
            not polygon
            or _ring_self_intersects(polygon)
            or any(not hole or _ring_self_intersects(hole) for hole in holes)
            or any(not _point_in_ring(hole[0], polygon) for hole in holes)
        ):
            raise RoofMeasureInputError(f"Section {section_id} has an invalid polygon.")
        for x, y in [*polygon, *(point for hole in holes for point in hole)]:
            if x < 0 or x > width or y < 0 or y > height:
                raise RoofMeasureInputError(
                    f"Section {section_id} contains a point outside the context image."
                )
        normalized.append(
            {
                "polygon": _point_dicts(polygon),
                "holes": [_point_dicts(hole) for hole in holes],
            }
        )
    area_pixels, perimeter_pixels = _component_totals(normalized)
    plan_area = sqft_from_pixels(area_pixels, pixels_per_foot)
    perimeter = feet_from_pixels(perimeter_pixels, pixels_per_foot)
    if not plan_area or not perimeter:
        raise RoofMeasureInputError(f"Section {section_id} has zero measurable area.")
    surface = None
    if pitch_rise_per_12 is not None:
        surface = plan_area * math.sqrt(1 + (float(pitch_rise_per_12) / 12) ** 2)
    return {
        "section_id": section_id,
        "source": source,
        "plan_area_sqft": round(plan_area, 1),
        "perimeter_ft": round(perimeter, 1),
        "surface_area_sqft": round(surface, 1) if surface is not None else None,
    }


def _component_totals(components: list[dict[str, Any]]) -> tuple[float, float]:
    area = 0.0
    perimeter = 0.0
    for component in components:
        polygon = _ring_tuples(component.get("polygon") or [])
        holes = [_ring_tuples(hole) for hole in component.get("holes") or []]
        area += polygon_area_pixels(polygon, holes)
        perimeter += polygon_perimeter_pixels(polygon, holes)
    return area, perimeter


def _deduplicate_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    accepted: list[dict[str, Any]] = []
    for candidate in candidates:
        if any(_same_building(candidate, existing) for existing in accepted):
            continue
        accepted.append(candidate)
    return accepted


def _same_building(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_area = float(left["plan_area_sqft"])
    right_area = float(right["plan_area_sqft"])
    area_ratio = min(left_area, right_area) / max(left_area, right_area)
    return (
        area_ratio >= 0.72
        and math.hypot(
            float(left["center_x"]) - float(right["center_x"]),
            float(left["center_y"]) - float(right["center_y"]),
        )
        <= 18
    )


def _footprint_overlay(image: Image.Image, candidates: list[dict[str, Any]]) -> Image.Image:
    base = image.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for candidate in candidates:
        label_point: tuple[float, float] | None = None
        for component in candidate["components"]:
            polygon = _ring_tuples(component["polygon"])
            if len(polygon) < 3:
                continue
            draw.polygon(polygon, fill=(38, 126, 198, 45))
            draw.line([*polygon, polygon[0]], fill=(38, 126, 198, 255), width=4)
            label_point = label_point or polygon[0]
            for hole in component.get("holes") or []:
                hole_points = _ring_tuples(hole)
                if len(hole_points) >= 3:
                    draw.polygon(hole_points, fill=(0, 0, 0, 0))
                    draw.line([*hole_points, hole_points[0]], fill=(255, 193, 7, 255), width=3)
        if label_point:
            draw.text(
                (label_point[0] + 6, label_point[1] + 6),
                str(candidate["footprint_id"]),
                fill=(255, 255, 255, 255),
                stroke_width=3,
                stroke_fill=(0, 60, 110, 255),
            )
    return Image.alpha_composite(base, overlay).convert("RGB")


def _point_in_ring(point: tuple[float, float], ring: list[tuple[float, float]]) -> bool:
    x, y = point
    inside = False
    closed = repair_polygon(ring)
    for index in range(len(closed) - 1):
        x1, y1 = closed[index]
        x2, y2 = closed[index + 1]
        if (y1 > y) != (y2 > y):
            intersect_x = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < intersect_x:
                inside = not inside
    return inside


def _ring_self_intersects(ring: list[tuple[float, float]]) -> bool:
    closed = repair_polygon(ring)
    segment_count = len(closed) - 1
    for left in range(segment_count):
        for right in range(left + 1, segment_count):
            if right in {left, left + 1}:
                continue
            if left == 0 and right == segment_count - 1:
                continue
            if _segments_intersect(
                closed[left],
                closed[left + 1],
                closed[right],
                closed[right + 1],
            ):
                return True
    return False


def _segments_intersect(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> bool:
    def orientation(
        p: tuple[float, float],
        q: tuple[float, float],
        r: tuple[float, float],
    ) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    return orientation(a, b, c) * orientation(a, b, d) < 0 and orientation(
        c, d, a
    ) * orientation(c, d, b) < 0


def _ring_tuples(points: list[Any]) -> list[tuple[float, float]]:
    tuples: list[tuple[float, float]] = []
    for point in points:
        if isinstance(point, dict):
            tuples.append((float(point["x"]), float(point["y"])))
        else:
            tuples.append((float(point[0]), float(point[1])))
    return repair_polygon(tuples)


def _point_dicts(points: list[tuple[float, float]]) -> list[dict[str, float]]:
    ring = repair_polygon(points)
    if len(ring) > 1 and ring[0] == ring[-1]:
        ring = ring[:-1]
    return [{"x": round(x, 2), "y": round(y, 2)} for x, y in ring]


def _provider_priority(provider: str) -> int:
    return {"postgres": 0, "mapbox": 1, "microsoft_global_ml": 2}.get(provider, 9)


def _load_satellite_image(raw: bytes) -> Image.Image:
    try:
        from io import BytesIO

        image = Image.open(BytesIO(raw))
        image.load()
        return image.convert("RGB")
    except Exception as exc:
        raise RoofMeasureContextError("Satellite imagery was not a readable image.") from exc


def _context_path(artifact_dir: Path, context_id: str) -> Path:
    return Path(artifact_dir).resolve() / context_id


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, path)


def _unique_nonempty(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))
