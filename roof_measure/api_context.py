from __future__ import annotations

import json
import hashlib
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
SELECTION_ASSET_RE = re.compile(r"^selected-footprints-[a-f0-9]{16}\.png$")
SAM2_ASSET_RE = re.compile(r"^sam2-candidates-[a-f0-9]{16}\.png$")
ROOF_ASSET_NAMES = frozenset({"satellite.png", "footprint-overlay.png"})
VIEW_ZOOM = {
    "whole_site": 16.5,
    "building_detail": 19.0,
    "close_detail": 20.0,
}
MAX_FOOTPRINT_CANDIDATES = 12
CAMPUS_SITE_TYPES = frozenset(
    {
        "school",
        "campus",
        "hospital",
        "industrial complex",
        "government complex",
        "multi-building facility",
    }
)
CAMPUS_BUILDING_GAP_FEET = 85.0


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
    site_name: str = "",
    site_type: str = "",
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

    candidates = _bounded_candidate_set(raw_candidates)
    for index, candidate in enumerate(candidates, start=1):
        candidate["footprint_id"] = f"fp-{index:02d}"

    candidate_groups = _build_candidate_groups(
        candidates,
        pixels_per_foot=pixels_per_foot,
        image_width=image.width,
        image_height=image.height,
    )
    site_resolution = _site_resolution_guidance(
        candidate_groups,
        site_name=site_name,
        site_type=site_type,
    )
    for candidate in candidates:
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
                "asset_url": coverage.asset_url,
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
    overlay = _footprint_overlay(
        image,
        candidates,
        candidate_groups=candidate_groups,
        recommended_group_id=str(
            site_resolution.get("recommended_candidate_group_id") or ""
        ),
    )
    (context_path / "footprint-overlay.png").write_bytes(image_png_bytes(overlay))

    context = {
        "schema_version": "spraytec.roof_measure_context.v1",
        "context_id": context_id,
        "created_at": int(time.time()),
        "expires_at": int(expires_at),
        "address": address.strip(),
        "job_id": job_id.strip(),
        "site_name": site_name.strip(),
        "site_type": site_type.strip(),
        "latitude": latitude,
        "longitude": longitude,
        "zoom": resolved_zoom,
        "image_width": image.width,
        "image_height": image.height,
        "pixels_per_foot": pixels_per_foot,
        "footprint_candidates": candidates,
        "candidate_groups": candidate_groups,
        **site_resolution,
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
    sam2_candidate_id: str = "",
    mapbox_token: str = "",
    normalized_sections: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized_sections = list(normalized_sections or [])
    source_count = sum(
        (
            bool(selected_footprint_ids),
            bool(sections),
            bool(normalized_sections),
            bool(sam2_candidate_id),
        )
    )
    if source_count != 1:
        raise RoofMeasureInputError(
            "Provide exactly one of selected_footprint_ids, pixel sections, "
            "normalized sections, or sam2_candidate_id."
        )
    context = load_roof_measure_context(
        context_id=context_id,
        artifact_dir=artifact_dir,
    )
    width = int(context["image_width"])
    height = int(context["image_height"])
    pixels_per_foot = float(context["pixels_per_foot"])
    assistant_sections = bool(normalized_sections)
    effective_sections = (
        _normalized_sections_to_pixels(
            normalized_sections,
            width=width,
            height=height,
        )
        if assistant_sections
        else list(sections)
    )
    candidate_by_id = {
        str(candidate["footprint_id"]): candidate
        for candidate in context.get("footprint_candidates") or []
    }

    measurement_sections: list[dict[str, Any]] = []
    overlay_sections = list(effective_sections)
    overlay_footprint_ids = list(selected_footprint_ids)
    overlay_context = context
    overlay_source_image: Image.Image | None = None
    source_view = "context"
    source_zoom = float(context["zoom"])
    source_pixels_per_foot = pixels_per_foot
    fitted_view_warning = ""
    if sam2_candidate_id:
        from .api_segmentation import sam2_candidate_sections

        candidate_components, source_footprint_ids = sam2_candidate_sections(
            context,
            sam2_candidate_id,
        )
        measurement_sections.append(
            _measure_components(
                section_id=sam2_candidate_id,
                source="sam2_candidate",
                components=candidate_components,
                pixels_per_foot=pixels_per_foot,
                width=width,
                height=height,
                pitch_rise_per_12=pitch_rise_per_12,
            )
        )
        overlay_footprint_ids = source_footprint_ids
        overlay_sections = [
            {
                "section_id": f"{sam2_candidate_id}-{index}",
                "polygon": component.get("polygon") or [],
                "holes": component.get("holes") or [],
            }
            for index, component in enumerate(candidate_components, start=1)
        ]
    elif selected_footprint_ids:
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
        _validate_nonoverlapping_sections(effective_sections)
        for section in effective_sections:
            measurement_sections.append(
                _measure_components(
                    section_id=str(section["section_id"]),
                    source=(
                        "assistant_polygon" if assistant_sections else "custom_polygon"
                    ),
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
        if str(mapbox_token or "").strip():
            from .api_segmentation import _fit_segmentation_view_to_footprints

            fitted_view, fitted_view_warning = _fit_segmentation_view_to_footprints(
                context=context,
                selected_components=[
                    {
                        "polygon": section["polygon"],
                        "holes": section.get("holes") or [],
                    }
                    for section in effective_sections
                ],
                mapbox_token=mapbox_token,
            )
            if fitted_view is not None:
                overlay_sections = [
                    {
                        "section_id": section["section_id"],
                        "polygon": component["polygon"],
                        "holes": component.get("holes") or [],
                    }
                    for section, component in zip(
                        effective_sections,
                        fitted_view.components,
                        strict=True,
                    )
                ]
                overlay_context = {
                    **fitted_view.context,
                    "footprint_candidates": [],
                }
                overlay_source_image = fitted_view.image
                source_view = "custom_boundary_fitted"
                source_zoom = float(fitted_view.context["zoom"])
                source_pixels_per_foot = float(
                    fitted_view.context["pixels_per_foot"]
                )

    total_plan = sum(float(item["plan_area_sqft"]) for item in measurement_sections)
    total_perimeter = sum(float(item["perimeter_ft"]) for item in measurement_sections)
    total_surface = (
        sum(float(item["surface_area_sqft"]) for item in measurement_sections)
        if pitch_rise_per_12 is not None
        else None
    )
    warnings = [
        (
            "SAM2 produced a model-derived roof boundary from reviewed footprint "
            "prompts. Verify every edge, overhang, canopy, penetration, courtyard, "
            "and excluded area before quoting."
            if sam2_candidate_id
            else (
                "The Assistant visually traced this roof boundary on the context "
                "image. Verify every edge, overhang, canopy, penetration, courtyard, "
                "and excluded area before quoting."
                if assistant_sections
                else "Building footprints are evidence, not a surveyed roof boundary. "
                "Verify roof edges, overhangs, canopies, penetrations, and excluded "
                "areas before quoting."
            )
        )
    ]
    assumptions = [
        "Area and perimeter use the address-calibrated, north-up Mapbox image scale.",
        (
            "SAM2 refined the estimator-selected footprint evidence; no OpenAI API "
            "call was used."
            if sam2_candidate_id
            else (
                "The API deterministically validated and measured normalized polygons "
                "drawn by the conversational Assistant; the API called no AI service."
                if assistant_sections
                else "No AI model, SAM2 service, or OpenAI call was used by the API."
            )
        ),
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
    if fitted_view_warning:
        warnings.append(fitted_view_warning)
    if source_view == "custom_boundary_fitted":
        assumptions.append(
            f"The final custom-boundary overlay was re-centered and retrieved at "
            f"zoom {source_zoom:.2f}; its geometry and measurement remain tied to "
            "the original address-calibrated context."
        )

    recommended_sam2_candidate_id = str(
        (context.get("sam2_candidates") or [{}])[0].get("candidate_id") or ""
    )
    recommended_sam2_overlay = str(
        context.get("sam2_candidate_asset_name") or ""
    )
    if (
        sam2_candidate_id
        and sam2_candidate_id == recommended_sam2_candidate_id
        and recommended_sam2_overlay
        and (artifact_dir / context_id / recommended_sam2_overlay).is_file()
    ):
        selected_overlay_asset_name = recommended_sam2_overlay
    else:
        selected_overlay_asset_name = _write_selected_footprint_overlay(
            context=overlay_context,
            selected_footprint_ids=overlay_footprint_ids,
            sections=overlay_sections,
            artifact_dir=artifact_dir,
            source_image=overlay_source_image,
            source_identity=f"{source_view}:{source_zoom:.4f}",
        )

    return {
        "schema_version": "spraytec.roof_measure_calculation.v1",
        "context_id": context_id,
        "measurement_basis": (
            "sam2_refined_address_calibrated_satellite_plan_view"
            if sam2_candidate_id
            else (
                "assistant_traced_address_calibrated_satellite_plan_view"
                if assistant_sections
                else "address_calibrated_satellite_plan_view"
            )
        ),
        "source_view": source_view,
        "source_zoom": round(source_zoom, 2),
        "source_pixels_per_foot": round(source_pixels_per_foot, 4),
        "total_plan_area_sqft": round(total_plan, 1),
        "total_perimeter_ft": round(total_perimeter, 1),
        "pitch_rise_per_12": pitch_rise_per_12,
        "total_surface_area_sqft": (
            round(total_surface, 1) if total_surface is not None else None
        ),
        "sections": measurement_sections,
        "selected_overlay_asset_name": selected_overlay_asset_name,
        "review_status": "requires_estimator_verification",
        "assumptions": assumptions,
        "warnings": warnings,
    }


def focus_roof_measure_context(
    *,
    context_id: str,
    selected_footprint_ids: list[str],
    artifact_dir: Path,
    mapbox_token: str,
) -> dict[str, Any]:
    """Create a clean, calibrated image centered on reviewed footprint bounds."""
    if not selected_footprint_ids:
        raise RoofMeasureInputError("Select at least one footprint to center the image.")
    if len(set(selected_footprint_ids)) != len(selected_footprint_ids):
        raise RoofMeasureInputError("selected_footprint_ids must be unique.")

    source_context = load_roof_measure_context(
        context_id=context_id,
        artifact_dir=artifact_dir,
    )
    candidate_by_id = {
        str(candidate.get("footprint_id") or ""): candidate
        for candidate in source_context.get("footprint_candidates") or []
    }
    missing = [item for item in selected_footprint_ids if item not in candidate_by_id]
    if missing:
        raise RoofMeasureInputError(
            "Unknown footprint candidate IDs: " + ", ".join(missing)
        )
    selected_components = [
        component
        for footprint_id in selected_footprint_ids
        for component in candidate_by_id[footprint_id].get("components") or []
    ]

    from .api_segmentation import _fit_segmentation_view_to_footprints

    fitted_view, _ = _fit_segmentation_view_to_footprints(
        context=source_context,
        selected_components=selected_components,
        mapbox_token=mapbox_token,
    )
    source_path = _context_path(artifact_dir, context_id) / "satellite.png"
    if fitted_view is None:
        image = Image.open(source_path).convert("RGB")
        focused_values = dict(source_context)
        focus_warning = (
            "A tighter source image was unavailable or unnecessary; the clean "
            "original context image was retained."
        )
    else:
        image = fitted_view.image
        focused_values = dict(fitted_view.context)
        focus_warning = (
            f"Retrieved clean imagery at zoom {float(fitted_view.context['zoom']):.2f}, "
            "centered on the selected footprint bounds with a safety margin."
        )

    focused_context_id = uuid.uuid4().hex
    focused_path = _context_path(artifact_dir, focused_context_id)
    focused_path.mkdir(parents=True, exist_ok=False)
    (focused_path / "satellite.png").write_bytes(image_png_bytes(image))
    focused_context = {
        **focused_values,
        "schema_version": "spraytec.roof_measure_visual_context.v1",
        "context_id": focused_context_id,
        "created_at": int(time.time()),
        "footprint_candidates": [],
        "candidate_groups": [],
        "site_resolution_status": "review_required",
        "recommended_candidate_group_id": "",
        "site_resolution_reason": "",
        "requires_site_confirmation": False,
        "focus_source_context_id": context_id,
        "focus_footprint_ids": list(selected_footprint_ids),
        "warnings": _unique_nonempty(
            [*(source_context.get("warnings") or []), focus_warning]
        ),
    }
    _write_json_atomic(focused_path / "context.json", focused_context)
    return focused_context


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
    if (
        asset_name not in ROOF_ASSET_NAMES
        and not SELECTION_ASSET_RE.fullmatch(str(asset_name or ""))
        and not SAM2_ASSET_RE.fullmatch(str(asset_name or ""))
    ):
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


def _normalized_sections_to_pixels(
    sections: list[dict[str, Any]],
    *,
    width: int,
    height: int,
) -> list[dict[str, Any]]:
    def convert_ring(points: list[dict[str, Any]]) -> list[dict[str, float]]:
        return [
            {
                "x": round(float(point["x"]) * width, 3),
                "y": round(float(point["y"]) * height, 3),
            }
            for point in points
        ]

    return [
        {
            "section_id": str(section["section_id"]),
            "polygon": convert_ring(section.get("polygon") or []),
            "holes": [
                convert_ring(hole) for hole in section.get("holes") or []
            ],
        }
        for section in sections
    ]


def _validate_nonoverlapping_sections(sections: list[dict[str, Any]]) -> None:
    """Reject invalid or overlapping additive sections before they can double count."""
    try:
        from shapely.geometry import Polygon
    except ImportError:
        return

    geometries: list[tuple[str, Any]] = []
    for section in sections:
        section_id = str(section.get("section_id") or "unnamed")
        polygon = _ring_tuples(section.get("polygon") or [])
        holes = [_ring_tuples(hole) for hole in section.get("holes") or []]
        geometry = Polygon(polygon, holes=holes)
        if not geometry.is_valid or geometry.area <= 1:
            raise RoofMeasureInputError(
                f"Section {section_id} has invalid or self-intersecting geometry."
            )
        for prior_id, prior in geometries:
            if geometry.intersection(prior).area > 1:
                raise RoofMeasureInputError(
                    f"Sections {prior_id} and {section_id} overlap and would double-count area."
                )
        geometries.append((section_id, geometry))


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


def _bounded_candidate_set(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep nearby structures while ensuring large site buildings are not dropped."""
    nearest_ranked = sorted(
        candidates,
        key=lambda item: (
            float(item["center_distance_pixels"]),
            _provider_priority(str(item["provider"])),
            -float(item["plan_area_sqft"]),
        ),
    )
    deduplicated = _deduplicate_candidates(nearest_ranked)
    nearest = deduplicated[:8]
    largest = sorted(
        deduplicated,
        key=lambda item: (
            -float(item["plan_area_sqft"]),
            float(item["center_distance_pixels"]),
        ),
    )[:4]
    selected: list[dict[str, Any]] = []
    for candidate in [*nearest, *largest, *deduplicated]:
        if candidate in selected:
            continue
        selected.append(candidate)
        if len(selected) >= MAX_FOOTPRINT_CANDIDATES:
            break
    return selected


def _build_candidate_groups(
    candidates: list[dict[str, Any]],
    *,
    pixels_per_foot: float,
    image_width: int = 1280,
    image_height: int = 1280,
) -> list[dict[str, Any]]:
    """Group nearby, non-duplicate buildings into reviewable site assemblies."""
    if not candidates:
        return []
    max_gap_pixels = CAMPUS_BUILDING_GAP_FEET * float(pixels_per_foot)
    boxes = [_candidate_bounds(candidate) for candidate in candidates]
    neighbors: dict[int, set[int]] = {index: set() for index in range(len(candidates))}
    for left in range(len(candidates)):
        for right in range(left + 1, len(candidates)):
            if _bounds_gap_pixels(boxes[left], boxes[right]) <= max_gap_pixels:
                neighbors[left].add(right)
                neighbors[right].add(left)

    components: list[list[int]] = []
    remaining = set(range(len(candidates)))
    while remaining:
        start = min(remaining)
        stack = [start]
        component: list[int] = []
        remaining.remove(start)
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbor in sorted(neighbors[current]):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    stack.append(neighbor)
        components.append(sorted(component))

    groups: list[dict[str, Any]] = []
    for indexes in components:
        members = [candidates[index] for index in indexes]
        center_distance_pixels = min(
            float(member.get("center_distance_pixels") or 0.0) for member in members
        )
        groups.append(
            {
                "group_id": "",
                "label": "",
                "footprint_ids": [str(member["footprint_id"]) for member in members],
                "building_count": len(members),
                "plan_area_sqft": round(
                    sum(float(member["plan_area_sqft"]) for member in members), 1
                ),
                "perimeter_ft": round(
                    sum(float(member["perimeter_ft"]) for member in members), 1
                ),
                "distance_from_address_point_ft": round(
                    center_distance_pixels / float(pixels_per_foot), 1
                ),
                "contains_address_point": any(
                    _candidate_contains_point(
                        member,
                        (float(image_width) / 2.0, float(image_height) / 2.0),
                    )
                    for member in members
                ),
            }
        )
    groups.sort(
        key=lambda group: (
            -int(group["building_count"] > 1),
            -float(group["plan_area_sqft"]),
            float(group["distance_from_address_point_ft"]),
        )
    )
    for index, group in enumerate(groups, start=1):
        group["group_id"] = f"site-{index:02d}"
        group["label"] = (
            f"Site group {index}: {group['building_count']} building section"
            f"{'s' if group['building_count'] != 1 else ''}"
        )
    return groups


def _site_resolution_guidance(
    groups: list[dict[str, Any]],
    *,
    site_name: str,
    site_type: str,
) -> dict[str, Any]:
    site_descriptor = " ".join(
        f"{site_name or ''} {site_type or ''}".lower().split()
    )
    campus_expected = any(
        campus_type in site_descriptor for campus_type in CAMPUS_SITE_TYPES
    )
    multi_building = [group for group in groups if int(group["building_count"]) > 1]
    recommended: dict[str, Any] | None = None
    reason = ""
    if campus_expected and multi_building:
        recommended = max(multi_building, key=lambda group: float(group["plan_area_sqft"]))
        reason = (
            "The supplied site type indicates a multi-building facility, so the "
            "largest nearby building assembly is suggested for visual review."
        )
    elif groups:
        containing = [group for group in groups if group["contains_address_point"]]
        recommended = min(
            containing or groups,
            key=lambda group: float(group["distance_from_address_point_ft"]),
        )
        reason = (
            "The nearest site group is suggested for visual review; the address "
            "point is a search center and is not proof of the roof boundary."
        )
    return {
        "site_resolution_status": (
            "candidate_group_suggested" if recommended else "review_required"
        ),
        "recommended_candidate_group_id": (
            str(recommended["group_id"]) if recommended else ""
        ),
        "site_resolution_reason": reason,
        "requires_site_confirmation": bool(len(groups) > 1 or site_name or campus_expected),
    }


def _candidate_bounds(candidate: dict[str, Any]) -> tuple[float, float, float, float]:
    points = [
        point
        for component in candidate.get("components") or []
        for point in component.get("polygon") or []
    ]
    if not points:
        return (0.0, 0.0, 0.0, 0.0)
    xs = [float(point["x"]) for point in points]
    ys = [float(point["y"]) for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _bounds_gap_pixels(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    horizontal = max(0.0, left[0] - right[2], right[0] - left[2])
    vertical = max(0.0, left[1] - right[3], right[1] - left[3])
    return math.hypot(horizontal, vertical)


def _candidate_contains_point(
    candidate: dict[str, Any],
    point: tuple[float, float],
) -> bool:
    return any(
        _point_in_ring(point, _ring_tuples(component.get("polygon") or []))
        and not any(
            _point_in_ring(point, _ring_tuples(hole))
            for hole in component.get("holes") or []
        )
        for component in candidate.get("components") or []
    )


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


def _footprint_overlay(
    image: Image.Image,
    candidates: list[dict[str, Any]],
    *,
    candidate_groups: list[dict[str, Any]] | None = None,
    recommended_group_id: str = "",
) -> Image.Image:
    base = image.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    recommended_ids = {
        str(footprint_id)
        for group in candidate_groups or []
        if str(group.get("group_id") or "") == recommended_group_id
        for footprint_id in group.get("footprint_ids") or []
    }
    recommended_label_drawn = False
    for candidate in candidates:
        footprint_id = str(candidate["footprint_id"])
        is_recommended = footprint_id in recommended_ids
        line_color = (255, 153, 0, 255) if is_recommended else (38, 126, 198, 255)
        fill_color = (255, 153, 0, 60) if is_recommended else (38, 126, 198, 45)
        line_width = 6 if is_recommended else 4
        label_point: tuple[float, float] | None = None
        for component in candidate["components"]:
            polygon = _ring_tuples(component["polygon"])
            if len(polygon) < 3:
                continue
            draw.polygon(polygon, fill=fill_color)
            draw.line([*polygon, polygon[0]], fill=line_color, width=line_width)
            label_point = label_point or polygon[0]
            for hole in component.get("holes") or []:
                hole_points = _ring_tuples(hole)
                if len(hole_points) >= 3:
                    draw.polygon(hole_points, fill=(0, 0, 0, 0))
                    draw.line([*hole_points, hole_points[0]], fill=(255, 193, 7, 255), width=3)
        if label_point:
            draw.text(
                (label_point[0] + 6, label_point[1] + 6),
                footprint_id,
                fill=(255, 255, 255, 255),
                stroke_width=3,
                stroke_fill=(145, 75, 0, 255) if is_recommended else (0, 60, 110, 255),
            )
            if is_recommended and not recommended_label_drawn:
                draw.text(
                    (label_point[0] + 6, label_point[1] + 30),
                    f"{recommended_group_id} SUGGESTED",
                    fill=(255, 255, 255, 255),
                    stroke_width=4,
                    stroke_fill=(145, 75, 0, 255),
                )
                recommended_label_drawn = True
    return Image.alpha_composite(base, overlay).convert("RGB")


def _write_selected_footprint_overlay(
    *,
    context: dict[str, Any],
    selected_footprint_ids: list[str],
    sections: list[dict[str, Any]],
    artifact_dir: Path,
    source_image: Image.Image | None = None,
    source_identity: str = "context",
) -> str:
    selection_payload = json.dumps(
        {
            "selected_footprint_ids": selected_footprint_ids,
            "sections": sections,
            "source_identity": source_identity,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(selection_payload).hexdigest()[:16]
    asset_name = f"selected-footprints-{digest}.png"
    context_path = _context_path(artifact_dir, str(context["context_id"]))
    image = (
        source_image.convert("RGBA")
        if source_image is not None
        else Image.open(context_path / "satellite.png").convert("RGBA")
    )
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    selected = set(selected_footprint_ids)

    for candidate in context.get("footprint_candidates") or []:
        footprint_id = str(candidate.get("footprint_id") or "")
        is_selected = footprint_id in selected
        line_color = (235, 61, 52, 255) if is_selected else (38, 126, 198, 150)
        fill_color = (235, 61, 52, 75) if is_selected else (38, 126, 198, 22)
        line_width = 7 if is_selected else 2
        label_point: tuple[float, float] | None = None
        for component in candidate.get("components") or []:
            polygon = _ring_tuples(component.get("polygon") or [])
            if len(polygon) < 3:
                continue
            draw.polygon(polygon, fill=fill_color)
            draw.line([*polygon, polygon[0]], fill=line_color, width=line_width)
            label_point = label_point or polygon[0]
        if label_point:
            label = f"{footprint_id}{' SELECTED' if is_selected else ''}"
            draw.text(
                (label_point[0] + 5, label_point[1] + 5),
                label,
                fill=(255, 255, 255, 255),
                stroke_width=3,
                stroke_fill=(130, 25, 20, 255) if is_selected else (0, 60, 110, 255),
            )

    for section in sections:
        polygon = _ring_tuples(section.get("polygon") or [])
        if len(polygon) < 3:
            continue
        draw.polygon(polygon, fill=(255, 193, 7, 70))
        draw.line(
            [*polygon, polygon[0]],
            fill=(255, 193, 7, 255),
            width=7,
        )
        draw.text(
            (polygon[0][0] + 5, polygon[0][1] + 5),
            f"{section.get('section_id') or 'custom'} SELECTED",
            fill=(255, 255, 255, 255),
            stroke_width=3,
            stroke_fill=(125, 85, 0, 255),
        )

    rendered = Image.alpha_composite(image, overlay).convert("RGB")
    (context_path / asset_name).write_bytes(image_png_bytes(rendered))
    return asset_name


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
