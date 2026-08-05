from __future__ import annotations

import hashlib
from io import BytesIO
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .api_context import (
    RoofMeasureContextError,
    RoofMeasureInputError,
    _context_path,
    _measure_components,
    _point_dicts,
    _ring_tuples,
    _write_json_atomic,
    load_roof_measure_context,
)
from .geometry import polygon_area_pixels, straighten_architectural_ring
from .lidar import kyfromabove_height_grid_for_image
from .map_reference import MapboxReferenceProvider, image_pixel_to_lon_lat
from .polygonize import sections_from_mask
from .segmentation import Sam2RoofSegmenter, SegmentationPrompts
from .visualization import image_png_bytes


@dataclass(frozen=True)
class _LidarGuidance:
    height_grid: np.ndarray
    supported_grid: np.ndarray
    cell_pixels: int
    lidar_points: int
    image_points: int


@dataclass(frozen=True)
class _FittedSegmentationView:
    image: Image.Image
    components: list[dict[str, Any]]
    context: dict[str, Any]
    original_center_x: float
    original_center_y: float
    scale: float


def segment_roof_measure_context(
    *,
    context_id: str,
    selected_footprint_ids: list[str],
    sam2_url: str,
    sam2_api_key: str,
    artifact_dir: Path,
    timeout_seconds: float = 90.0,
    mapbox_token: str = "",
) -> dict[str, Any]:
    """Create reviewable SAM2 candidates from explicitly selected footprints."""
    if not str(sam2_url or "").strip():
        raise RoofMeasureContextError("SAM2 segmentation is not configured.")
    if not selected_footprint_ids:
        raise RoofMeasureInputError(
            "Select at least one reviewed footprint before requesting SAM2 refinement."
        )
    if len(set(selected_footprint_ids)) != len(selected_footprint_ids):
        raise RoofMeasureInputError("selected_footprint_ids must be unique.")

    context = load_roof_measure_context(
        context_id=context_id,
        artifact_dir=artifact_dir,
    )
    candidate_by_id = {
        str(candidate.get("footprint_id") or ""): candidate
        for candidate in context.get("footprint_candidates") or []
    }
    missing = [item for item in selected_footprint_ids if item not in candidate_by_id]
    if missing:
        raise RoofMeasureInputError(
            "Unknown footprint candidate IDs: " + ", ".join(missing)
        )

    context_path = _context_path(artifact_dir, context_id)
    original_image = Image.open(context_path / "satellite.png").convert("RGB")
    selected_candidates = [candidate_by_id[item] for item in selected_footprint_ids]
    original_components = [
        component
        for candidate in selected_candidates
        for component in candidate.get("components") or []
    ]
    fitted_view, fitted_warning = _fit_segmentation_view_to_footprints(
        context=context,
        selected_components=original_components,
        mapbox_token=mapbox_token,
    )
    if fitted_view is None:
        image = original_image
        components = original_components
        segmentation_context = context
    else:
        image = fitted_view.image
        components = fitted_view.components
        segmentation_context = fitted_view.context
        (context_path / "sam2-detail.png").write_bytes(image_png_bytes(image))
    image_array = np.asarray(image)
    footprint_mask = _components_mask(image.size, components)
    if not footprint_mask.any():
        raise RoofMeasureInputError(
            "The selected footprints did not produce a usable image-space prompt."
        )

    buffer_pixels = max(
        6,
        min(
            192,
            int(round(float(segmentation_context["pixels_per_foot"]) * 10.0)),
        ),
    )
    lidar_guidance, lidar_warning = _load_lidar_guidance(
        context=segmentation_context,
        footprint_mask=footprint_mask,
    )
    corridor = _dilate_mask(footprint_mask, radius=buffer_pixels)
    positive_points = _component_prompt_points(image.size, components)
    prompt_box = _mask_box(footprint_mask, padding=buffer_pixels)
    segmenter = Sam2RoofSegmenter(
        url=sam2_url,
        timeout_seconds=timeout_seconds,
        api_key=sam2_api_key,
    )
    segmentation = segmenter.segment(
        image_array,
        SegmentationPrompts(
            positive_points=positive_points,
            box=prompt_box,
            mask_input=footprint_mask,
        ),
    )

    processed: list[dict[str, Any]] = []
    for provider_index, provider_candidate in enumerate(
        segmentation.candidates,
        start=1,
    ):
        raw_mask = np.asarray(provider_candidate.mask, dtype=bool)
        if raw_mask.shape != footprint_mask.shape:
            continue
        constrained_mask = raw_mask & corridor
        variants = [("sam2", constrained_mask)]
        if lidar_guidance is not None:
            trimmed_mask = _constrain_to_lidar_high_band(
                constrained_mask,
                footprint_mask=footprint_mask,
                guidance=lidar_guidance,
            )
            if not np.array_equal(trimmed_mask, constrained_mask):
                variants.append(("sam2_lidar_high_band", trimmed_mask))
        for boundary_refinement, candidate_mask in variants:
            sections = sections_from_mask(
                candidate_mask,
                simplification_tolerance=2.5,
                minimum_section_area_pixels=max(
                    100.0,
                    footprint_mask.sum() * 0.002,
                ),
                edge_snap_strength=0.0,
            )
            if not sections:
                continue
            candidate_components = [
                {
                    "polygon": _point_dicts(section.polygon),
                    "holes": [_point_dicts(hole) for hole in section.holes],
                }
                for section in sections
            ]
            measurement = _measure_components(
                section_id=f"sam2-candidate-{provider_index}",
                source="sam2_candidate",
                components=candidate_components,
                pixels_per_foot=float(segmentation_context["pixels_per_foot"]),
                width=image.width,
                height=image.height,
                pitch_rise_per_12=None,
            )
            metrics = _candidate_metrics(
                candidate_mask,
                footprint_mask,
                model_score=float(provider_candidate.score),
                section_count=len(sections),
                lidar_guidance=lidar_guidance,
            )
            digest = hashlib.sha256(
                b"|".join(
                    [
                        context_id.encode("ascii"),
                        ",".join(selected_footprint_ids).encode("utf-8"),
                        np.packbits(candidate_mask).tobytes(),
                    ]
                )
            ).hexdigest()[:16]
            processed.append(
                {
                    "candidate_id": f"sam2-{digest}",
                    "provider_rank": provider_index,
                    "boundary_refinement": boundary_refinement,
                    "geometry_refinement": "mask_polygon",
                    "geometry_area_drift_fraction": 0.0,
                    "model_name": segmentation.model_name,
                    "model_version": segmentation.model_version,
                    "model_score": round(float(provider_candidate.score), 4),
                    "selection_score": metrics["selection_score"],
                    "footprint_overlap": metrics["footprint_overlap"],
                    "footprint_coverage": metrics["footprint_coverage"],
                    "area_ratio_to_footprint": metrics["area_ratio_to_footprint"],
                    "lidar_roof_support_fraction": metrics.get(
                        "lidar_roof_support_fraction"
                    ),
                    "lidar_sampled_fraction": metrics.get(
                        "lidar_sampled_fraction"
                    ),
                    "lidar_ground_fraction": metrics.get(
                        "lidar_ground_fraction"
                    ),
                    "lidar_elevated_coverage": metrics.get(
                        "lidar_elevated_coverage"
                    ),
                    "lidar_boundary_score": metrics.get(
                        "lidar_boundary_score"
                    ),
                    "lidar_roof_leakage_outside": metrics.get(
                        "lidar_roof_leakage_outside"
                    ),
                    "plan_area_sqft": measurement["plan_area_sqft"],
                    "perimeter_ft": measurement["perimeter_ft"],
                    "section_count": len(sections),
                    "selected_footprint_ids": list(selected_footprint_ids),
                    "components": candidate_components,
                }
            )
    processed.sort(
        key=lambda item: (
            float(item["selection_score"]),
            float(item["model_score"]),
        ),
        reverse=True,
    )
    if not processed:
        raise RoofMeasureContextError(
            "SAM2 returned no usable mask inside the selected footprint corridor."
        )
    best_raw = next(
        (
            item
            for item in processed
            if item["boundary_refinement"] == "sam2"
        ),
        None,
    )
    best_refined = next(
        (
            item
            for item in processed
            if item["boundary_refinement"] != "sam2"
        ),
        None,
    )
    orthogonalized = _orthogonalized_candidate(
        processed[0],
        context=segmentation_context,
        footprint_mask=footprint_mask,
        lidar_guidance=lidar_guidance,
        image_size=image.size,
    )
    selected: list[dict[str, Any]] = []
    for item in [
        orthogonalized,
        processed[0],
        best_raw,
        best_refined,
        *processed,
    ]:
        if item is not None and item not in selected:
            selected.append(item)
        if len(selected) == 3:
            break
    # A guarded orthogonalized variant deliberately leads its source mask so
    # reviewers see the cleaned boundary first. The source mask and best
    # alternate boundary remain immediately available for comparison.
    processed = selected
    for rank, item in enumerate(processed, start=1):
        item["rank"] = rank

    if fitted_view is not None:
        for item in processed:
            item["display_components"] = item["components"]
            item["components"] = _fitted_components_to_original(
                item["components"],
                fitted_view=fitted_view,
                original_image_size=original_image.size,
            )

    recommended_candidate = processed[0]
    asset_name = _write_candidate_contact_sheet(
        context=context,
        candidates=[recommended_candidate],
        artifact_dir=artifact_dir,
        source_asset_name=(
            "sam2-detail.png" if fitted_view is not None else "satellite.png"
        ),
    )
    for item in processed:
        item.pop("display_components", None)
    context["sam2_candidates"] = processed
    context["sam2_candidate_asset_name"] = asset_name
    context["sam2_source_footprint_ids"] = list(selected_footprint_ids)
    _write_json_atomic(context_path / "context.json", context)
    return {
        "schema_version": "spraytec.roof_measure_sam2_candidates.v2",
        "context_id": context_id,
        "selected_footprint_ids": list(selected_footprint_ids),
        "recommended_candidate_id": recommended_candidate["candidate_id"],
        "requires_candidate_confirmation": False,
        "evaluated_candidate_count": len(processed),
        "candidates": [_public_candidate(recommended_candidate)],
        "candidate_overlay_asset_name": asset_name,
        "model_name": segmentation.model_name,
        "model_version": segmentation.model_version,
        "source_view": (
            "footprint_fitted" if fitted_view is not None else "context"
        ),
        "source_zoom": round(float(segmentation_context["zoom"]), 2),
        "source_pixels_per_foot": round(
            float(segmentation_context["pixels_per_foot"]),
            4,
        ),
        "lidar_guidance_used": lidar_guidance is not None,
        "lidar_points": lidar_guidance.lidar_points if lidar_guidance else 0,
        "lidar_image_points": lidar_guidance.image_points if lidar_guidance else 0,
        "lidar_cell_pixels": lidar_guidance.cell_pixels if lidar_guidance else 0,
        "warnings": [
            *segmentation.warnings,
            *([fitted_warning] if fitted_warning else []),
            *([lidar_warning] if lidar_warning else []),
            *(
                [
                    "Connected elevated LiDAR blocks within each bounded SAM2 mask "
                    "formed a roof-support band. Measured low and mixed transition "
                    "blocks were excluded from a guarded alternative; unsampled "
                    "blocks were retained alongside the raw SAM2 boundary."
                ]
                if lidar_guidance is not None
                else []
            ),
            *(
                [
                    "The automatically selected candidate uses guarded dominant-axis "
                    "edge straightening. Alternate candidates remain in the context "
                    "diagnostics."
                ]
                if orthogonalized is not None
                else []
            ),
            (
                "The displayed overlay is the automatically selected top-ranked "
                "SAM2 refinement. Use it for calculation unless the estimator requests "
                "a boundary correction."
            ),
        ],
    }


def sam2_candidate_sections(
    context: dict[str, Any],
    candidate_id: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    candidate = next(
        (
            item
            for item in context.get("sam2_candidates") or []
            if str(item.get("candidate_id") or "") == candidate_id
        ),
        None,
    )
    if candidate is None:
        raise RoofMeasureInputError("Unknown or expired SAM2 candidate ID.")
    return list(candidate.get("components") or []), list(
        candidate.get("selected_footprint_ids") or []
    )


def _fit_segmentation_view_to_footprints(
    *,
    context: dict[str, Any],
    selected_components: list[dict[str, Any]],
    mapbox_token: str,
    maximum_zoom: float = 22.0,
    target_frame_fraction: float = 0.68,
) -> tuple[_FittedSegmentationView | None, str]:
    """Fetch the tightest safe source view that contains selected footprints."""
    token = str(mapbox_token or "").strip()
    points = [
        point
        for component in selected_components
        for ring in [component.get("polygon") or [], *(component.get("holes") or [])]
        for point in _ring_tuples(ring)
    ]
    if not token or not points:
        return None, ""

    width = int(context["image_width"])
    height = int(context["image_height"])
    min_x = min(point[0] for point in points)
    max_x = max(point[0] for point in points)
    min_y = min(point[1] for point in points)
    max_y = max(point[1] for point in points)
    span_x = max(max_x - min_x, 1.0)
    span_y = max(max_y - min_y, 1.0)
    fit_scale = min(
        width * target_frame_fraction / span_x,
        height * target_frame_fraction / span_y,
    )
    current_zoom = float(context["zoom"])
    fitted_zoom = min(
        float(maximum_zoom),
        current_zoom + math.log2(max(fit_scale, 1.0)),
    )
    if fitted_zoom <= current_zoom + 0.05:
        return None, ""

    center_x = (min_x + max_x) / 2.0
    center_y = (min_y + max_y) / 2.0
    center_longitude, center_latitude = image_pixel_to_lon_lat(
        center_x,
        center_y,
        center_latitude=float(context["latitude"]),
        center_longitude=float(context["longitude"]),
        zoom=current_zoom,
        width=width,
        height=height,
    )
    fetched = MapboxReferenceProvider(token).static_satellite_image_at(
        latitude=center_latitude,
        longitude=center_longitude,
        zoom=fitted_zoom,
        width=width,
        height=height,
    )
    if (
        not fetched.ok
        or not fetched.image_bytes
        or fetched.latitude is None
        or fetched.longitude is None
        or not fetched.pixels_per_foot
    ):
        return (
            None,
            "Footprint-fitted imagery was unavailable; SAM2 used the reviewed "
            "context image instead.",
        )

    try:
        image = Image.open(BytesIO(fetched.image_bytes)).convert("RGB")
    except Exception:
        return (
            None,
            "Footprint-fitted imagery was unreadable; SAM2 used the reviewed "
            "context image instead.",
        )
    resolved_zoom = float(fetched.zoom or fitted_zoom)
    scale = 2 ** (resolved_zoom - current_zoom)
    fitted_components = _transform_components(
        selected_components,
        transform=lambda x, y: (
            image.width / 2.0 + (x - center_x) * scale,
            image.height / 2.0 + (y - center_y) * scale,
        ),
    )
    fitted_context = {
        **context,
        "latitude": float(fetched.latitude),
        "longitude": float(fetched.longitude),
        "zoom": resolved_zoom,
        "image_width": image.width,
        "image_height": image.height,
        "pixels_per_foot": float(fetched.pixels_per_foot),
    }
    return (
        _FittedSegmentationView(
            image=image,
            components=fitted_components,
            context=fitted_context,
            original_center_x=center_x,
            original_center_y=center_y,
            scale=scale,
        ),
        (
            f"SAM2 used a footprint-fitted zoom-{resolved_zoom:.2f} source image "
            "centered on the confirmed footprint, with the full selected bounds "
            "and a safety margin retained."
        ),
    )


def _fitted_components_to_original(
    components: list[dict[str, Any]],
    *,
    fitted_view: _FittedSegmentationView,
    original_image_size: tuple[int, int],
) -> list[dict[str, Any]]:
    detail_width, detail_height = fitted_view.image.size
    original_width, original_height = original_image_size
    return _transform_components(
        components,
        transform=lambda x, y: (
            fitted_view.original_center_x
            + (x - detail_width / 2.0) / fitted_view.scale,
            fitted_view.original_center_y
            + (y - detail_height / 2.0) / fitted_view.scale,
        ),
        bounds=(original_width, original_height),
    )


def _transform_components(
    components: list[dict[str, Any]],
    *,
    transform: Callable[[float, float], tuple[float, float]],
    bounds: tuple[int, int] | None = None,
) -> list[dict[str, Any]]:
    def transform_ring(raw_ring: list[dict[str, Any]]) -> list[dict[str, float]]:
        transformed: list[dict[str, float]] = []
        ring = _ring_tuples(raw_ring)
        if len(ring) > 1 and ring[0] == ring[-1]:
            ring = ring[:-1]
        for x, y in ring:
            next_x, next_y = transform(x, y)
            if bounds is not None:
                next_x = max(0.0, min(float(bounds[0]), next_x))
                next_y = max(0.0, min(float(bounds[1]), next_y))
            transformed.append({"x": round(next_x, 4), "y": round(next_y, 4)})
        return transformed

    return [
        {
            "polygon": transform_ring(component.get("polygon") or []),
            "holes": [
                transform_ring(hole)
                for hole in component.get("holes") or []
            ],
        }
        for component in components
    ]


def _public_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in candidate.items()
        if key not in {
            "components",
            "display_components",
            "selected_footprint_ids",
        }
    }


def _orthogonalized_candidate(
    candidate: dict[str, Any],
    *,
    context: dict[str, Any],
    footprint_mask: np.ndarray,
    lidar_guidance: _LidarGuidance | None,
    image_size: tuple[int, int],
    maximum_area_drift: float = 0.015,
) -> dict[str, Any] | None:
    original_components = list(candidate.get("components") or [])
    pixels_per_foot = float(context["pixels_per_foot"])
    simplification_tolerance = _architectural_simplification_tolerance_pixels(
        pixels_per_foot=pixels_per_foot,
        lidar_cell_pixels=(
            lidar_guidance.cell_pixels
            if lidar_guidance is not None
            and candidate.get("boundary_refinement") == "sam2_lidar_high_band"
            else None
        ),
    )
    cleaned_components = _orthogonalize_components(
        original_components,
        simplification_tolerance=simplification_tolerance,
        maximum_area_drift=maximum_area_drift,
    )
    if cleaned_components is None:
        return None

    original_area = _components_area_pixels(original_components)
    cleaned_area = _components_area_pixels(cleaned_components)
    if original_area <= 0 or cleaned_area <= 0:
        return None
    area_drift = abs(cleaned_area - original_area) / original_area
    if area_drift > maximum_area_drift:
        return None

    try:
        measurement = _measure_components(
            section_id=f"{candidate['candidate_id']}-orthogonal",
            source="sam2_candidate",
            components=cleaned_components,
            pixels_per_foot=pixels_per_foot,
            width=image_size[0],
            height=image_size[1],
            pitch_rise_per_12=None,
        )
    except RoofMeasureInputError:
        return None

    cleaned_mask = _components_mask(image_size, cleaned_components)
    metrics = _candidate_metrics(
        cleaned_mask,
        footprint_mask,
        model_score=float(candidate["model_score"]),
        section_count=len(cleaned_components),
        lidar_guidance=lidar_guidance,
    )
    digest = hashlib.sha256(
        json.dumps(
            cleaned_components,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:16]
    refined = {
        **candidate,
        "candidate_id": f"sam2-{digest}",
        "geometry_refinement": "dominant_orthogonal",
        "geometry_simplification_tolerance_pixels": round(
            simplification_tolerance,
            2,
        ),
        "geometry_area_drift_fraction": round(area_drift, 4),
        "selection_score": metrics["selection_score"],
        "footprint_overlap": metrics["footprint_overlap"],
        "footprint_coverage": metrics["footprint_coverage"],
        "area_ratio_to_footprint": metrics["area_ratio_to_footprint"],
        "lidar_roof_support_fraction": metrics.get(
            "lidar_roof_support_fraction"
        ),
        "lidar_sampled_fraction": metrics.get("lidar_sampled_fraction"),
        "lidar_ground_fraction": metrics.get("lidar_ground_fraction"),
        "lidar_elevated_coverage": metrics.get("lidar_elevated_coverage"),
        "lidar_boundary_score": metrics.get("lidar_boundary_score"),
        "lidar_roof_leakage_outside": metrics.get(
            "lidar_roof_leakage_outside"
        ),
        "plan_area_sqft": measurement["plan_area_sqft"],
        "perimeter_ft": measurement["perimeter_ft"],
        "section_count": len(cleaned_components),
        "components": cleaned_components,
    }
    return refined


def _orthogonalize_components(
    components: list[dict[str, Any]],
    *,
    simplification_tolerance: float = 4.0,
    maximum_area_drift: float = 0.015,
) -> list[dict[str, Any]] | None:
    cleaned: list[dict[str, Any]] = []
    for component in components:
        polygon = _ring_tuples(component.get("polygon") or [])
        holes = [_ring_tuples(hole) for hole in component.get("holes") or []]
        cleaned_polygon = straighten_architectural_ring(
            polygon,
            simplification_tolerance=simplification_tolerance,
            angle_tolerance_degrees=15.0,
            max_area_drift=maximum_area_drift,
        )
        cleaned_holes = [
            straighten_architectural_ring(
                hole,
                simplification_tolerance=simplification_tolerance,
                angle_tolerance_degrees=15.0,
                max_area_drift=maximum_area_drift,
            )
            for hole in holes
        ]
        cleaned.append(
            {
                "polygon": _point_dicts(cleaned_polygon),
                "holes": [_point_dicts(hole) for hole in cleaned_holes],
            }
        )
    if not cleaned or json.dumps(cleaned, sort_keys=True) == json.dumps(
        components,
        sort_keys=True,
    ):
        return None
    original_area = _components_area_pixels(components)
    cleaned_area = _components_area_pixels(cleaned)
    if original_area <= 0 or cleaned_area <= 0:
        return None
    if abs(cleaned_area - original_area) / original_area > maximum_area_drift:
        return None
    return cleaned


def _architectural_simplification_tolerance_pixels(
    *,
    pixels_per_foot: float,
    lidar_cell_pixels: int | None = None,
) -> float:
    """Return a scale-aware tolerance that suppresses mask and LiDAR stair steps.

    The final edge still comes from the segmented image boundary. Raw SAM uses
    a conservative four-foot detail scale. A LiDAR-trimmed mask uses an
    eight-foot scale (bounded in pixels) so nearest-neighbor height cells do
    not survive as a chain of architectural corners.
    """
    scale = max(float(pixels_per_foot), 0.01)
    tolerance = max(4.0, min(scale * 4.0, 16.0))
    if lidar_cell_pixels is not None and lidar_cell_pixels > 0:
        lidar_tolerance = max(
            float(lidar_cell_pixels) * 0.8,
            scale * 8.0,
        )
        tolerance = max(tolerance, lidar_tolerance)
    return min(tolerance, 20.0)


def _components_area_pixels(components: list[dict[str, Any]]) -> float:
    return sum(
        polygon_area_pixels(
            _ring_tuples(component.get("polygon") or []),
            [
                _ring_tuples(hole)
                for hole in component.get("holes") or []
            ],
        )
        for component in components
    )


def _components_mask(
    image_size: tuple[int, int],
    components: list[dict[str, Any]],
) -> np.ndarray:
    mask_image = Image.new("L", image_size, 0)
    draw = ImageDraw.Draw(mask_image)
    for component in components:
        polygon = _ring_tuples(component.get("polygon") or [])
        if len(polygon) < 3:
            continue
        draw.polygon(polygon, fill=255)
        for hole in component.get("holes") or []:
            hole_ring = _ring_tuples(hole)
            if len(hole_ring) >= 3:
                draw.polygon(hole_ring, fill=0)
    return np.asarray(mask_image, dtype=bool)


def _component_prompt_points(
    image_size: tuple[int, int],
    components: list[dict[str, Any]],
) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for component in components[:12]:
        mask = _components_mask(image_size, [component])
        ys, xs = np.where(mask)
        if not len(xs):
            continue
        center_x = float((xs.min() + xs.max()) / 2.0)
        center_y = float((ys.min() + ys.max()) / 2.0)
        nearest = int(np.argmin((xs - center_x) ** 2 + (ys - center_y) ** 2))
        points.append((float(xs[nearest]), float(ys[nearest])))
    return points


def _load_lidar_guidance(
    *,
    context: dict[str, Any],
    footprint_mask: np.ndarray,
) -> tuple[_LidarGuidance | None, str]:
    coverage = dict(context.get("lidar_coverage") or {})
    asset_url = str(coverage.get("asset_url") or "").strip()
    if not asset_url:
        return None, ""
    grid = kyfromabove_height_grid_for_image(
        asset_url=asset_url,
        center_latitude=float(context["latitude"]),
        center_longitude=float(context["longitude"]),
        zoom=float(context["zoom"]),
        source_width=int(context["image_width"]),
        source_height=int(context["image_height"]),
        image_width=int(footprint_mask.shape[1]),
        image_height=int(footprint_mask.shape[0]),
        cell_pixels=8,
    )
    if not grid.ok or grid.height_grid is None:
        return None, grid.warning or "LiDAR height blocks were unavailable."

    height_grid = np.asarray(grid.height_grid, dtype=float)
    elevated_grid = np.isfinite(height_grid) & (height_grid >= 8.0)
    footprint_grid = _mask_to_grid(
        footprint_mask,
        grid_shape=height_grid.shape,
        cell_pixels=grid.cell_pixels,
    )
    near_footprint = _dilate_grid(footprint_grid, radius=1)
    supported_grid = np.zeros_like(elevated_grid)
    for component in _grid_components(elevated_grid):
        if (component & footprint_grid).any() or (
            int(component.sum()) >= 2 and (component & near_footprint).any()
        ):
            supported_grid |= component
    if not supported_grid.any():
        return (
            None,
            "LiDAR coverage was available but no elevated block connected to the "
            "reviewed footprints.",
        )

    return (
        _LidarGuidance(
            height_grid=height_grid,
            supported_grid=supported_grid,
            cell_pixels=int(grid.cell_pixels),
            lidar_points=int(grid.lidar_points),
            image_points=int(grid.image_points),
        ),
        "",
    )


def _mask_to_grid(
    mask: np.ndarray,
    *,
    grid_shape: tuple[int, int],
    cell_pixels: int,
) -> np.ndarray:
    grid = np.zeros(grid_shape, dtype=bool)
    ys, xs = np.where(np.asarray(mask, dtype=bool))
    if len(xs):
        grid[
            np.minimum(ys // cell_pixels, grid_shape[0] - 1),
            np.minimum(xs // cell_pixels, grid_shape[1] - 1),
        ] = True
    return grid


def _dilate_grid(mask: np.ndarray, *, radius: int) -> np.ndarray:
    if radius <= 0:
        return np.asarray(mask, dtype=bool)
    image = Image.fromarray(np.asarray(mask, dtype=np.uint8) * 255, mode="L")
    return np.asarray(
        image.filter(ImageFilter.MaxFilter(size=radius * 2 + 1)),
        dtype=np.uint8,
    ) > 0


def _erode_grid(mask: np.ndarray, *, radius: int) -> np.ndarray:
    if radius <= 0:
        return np.asarray(mask, dtype=bool)
    inverted = ~np.asarray(mask, dtype=bool)
    return ~_dilate_grid(inverted, radius=radius)


def _constrain_to_lidar_high_band(
    mask: np.ndarray,
    *,
    footprint_mask: np.ndarray,
    guidance: _LidarGuidance,
    minimum_footprint_coverage: float = 0.90,
    maximum_removed_fraction: float = 0.12,
) -> np.ndarray:
    candidate = np.asarray(mask, dtype=bool)
    candidate_grid = _mask_to_grid(
        candidate,
        grid_shape=guidance.height_grid.shape,
        cell_pixels=guidance.cell_pixels,
    )
    # The connected elevated support established when loading guidance is the
    # roof-height signal. Exclude measured low and mixed transition blocks, but
    # retain unsampled cells: missing LiDAR is not evidence that roof is absent.
    excluded_grid = (
        candidate_grid
        & np.isfinite(guidance.height_grid)
        & (guidance.height_grid < 8.0)
    )
    if not excluded_grid.any():
        return candidate
    excluded_image = Image.fromarray(
        excluded_grid.astype(np.uint8) * 255,
        mode="L",
    ).resize(
        (candidate.shape[1], candidate.shape[0]),
        Image.Resampling.NEAREST,
    )
    constrained = candidate & ~(np.asarray(excluded_image, dtype=np.uint8) > 0)
    if np.array_equal(constrained, candidate):
        return candidate
    removed_fraction = float((candidate & ~constrained).sum()) / max(
        float(candidate.sum()),
        1.0,
    )
    footprint_coverage = float((constrained & footprint_mask).sum()) / max(
        float(footprint_mask.sum()),
        1.0,
    )
    if (
        removed_fraction < 0.005
        or removed_fraction > maximum_removed_fraction
        or footprint_coverage < minimum_footprint_coverage
    ):
        return candidate
    return constrained


def _grid_components(mask: np.ndarray) -> list[np.ndarray]:
    source = np.asarray(mask, dtype=bool)
    visited = np.zeros_like(source)
    components: list[np.ndarray] = []
    for start_y, start_x in zip(*np.where(source & ~visited)):
        if visited[start_y, start_x]:
            continue
        component = np.zeros_like(source)
        stack = [(int(start_y), int(start_x))]
        visited[start_y, start_x] = True
        while stack:
            y, x = stack.pop()
            component[y, x] = True
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if not (dx or dy):
                        continue
                    ny, nx = y + dy, x + dx
                    if (
                        0 <= ny < source.shape[0]
                        and 0 <= nx < source.shape[1]
                        and source[ny, nx]
                        and not visited[ny, nx]
                    ):
                        visited[ny, nx] = True
                        stack.append((ny, nx))
        components.append(component)
    return components


def _mask_box(mask: np.ndarray, *, padding: int) -> tuple[float, float, float, float]:
    ys, xs = np.where(mask)
    height, width = mask.shape
    return (
        float(max(0, int(xs.min()) - padding)),
        float(max(0, int(ys.min()) - padding)),
        float(min(width - 1, int(xs.max()) + padding)),
        float(min(height - 1, int(ys.max()) + padding)),
    )


def _dilate_mask(mask: np.ndarray, *, radius: int) -> np.ndarray:
    size = max(3, int(radius) * 2 + 1)
    if size % 2 == 0:
        size += 1
    image = Image.fromarray(np.asarray(mask, dtype=np.uint8) * 255, mode="L")
    return np.asarray(image.filter(ImageFilter.MaxFilter(size=size))) > 0


def _candidate_metrics(
    mask: np.ndarray,
    footprint_mask: np.ndarray,
    *,
    model_score: float,
    section_count: int,
    lidar_guidance: _LidarGuidance | None = None,
) -> dict[str, float]:
    candidate_area = max(float(mask.sum()), 1.0)
    footprint_area = max(float(footprint_mask.sum()), 1.0)
    intersection = float((mask & footprint_mask).sum())
    overlap = intersection / candidate_area
    coverage = intersection / footprint_area
    f1 = 2.0 * overlap * coverage / max(overlap + coverage, 1e-9)
    area_ratio = candidate_area / footprint_area
    area_agreement = min(area_ratio, 1.0 / max(area_ratio, 1e-9))
    fragmentation = 1.0 / max(section_count, 1)
    base_selection_score = (
        0.40 * f1
        + 0.30 * area_agreement
        + 0.20 * max(0.0, min(model_score, 1.0))
        + 0.10 * fragmentation
    )
    result = {
        "selection_score": round(base_selection_score, 4),
        "footprint_overlap": round(overlap, 4),
        "footprint_coverage": round(coverage, 4),
        "area_ratio_to_footprint": round(area_ratio, 4),
    }
    if lidar_guidance is None:
        return result

    candidate_grid = _mask_to_grid(
        mask,
        grid_shape=lidar_guidance.height_grid.shape,
        cell_pixels=lidar_guidance.cell_pixels,
    )
    sampled = candidate_grid & np.isfinite(lidar_guidance.height_grid)
    if not sampled.any():
        return result
    values = lidar_guidance.height_grid[sampled]
    sampled_fraction = float(sampled.sum()) / max(float(candidate_grid.sum()), 1.0)
    roof_support = float(np.mean(values >= 8.0))
    ground_fraction = float(np.mean(values < 4.0))
    elevated_coverage = float(
        (candidate_grid & lidar_guidance.supported_grid).sum()
    ) / max(float(lidar_guidance.supported_grid.sum()), 1.0)
    inside_edge = candidate_grid & ~_erode_grid(candidate_grid, radius=1)
    outside_edge = _dilate_grid(candidate_grid, radius=1) & ~candidate_grid
    inside_sampled = inside_edge & np.isfinite(lidar_guidance.height_grid)
    outside_sampled = outside_edge & np.isfinite(lidar_guidance.height_grid)
    roof_inside = (
        float(np.mean(lidar_guidance.height_grid[inside_sampled] >= 8.0))
        if inside_sampled.any()
        else roof_support
    )
    ground_outside = (
        float(np.mean(lidar_guidance.height_grid[outside_sampled] < 4.0))
        if outside_sampled.any()
        else 1.0 - ground_fraction
    )
    roof_leakage_outside = (
        float(np.mean(lidar_guidance.height_grid[outside_sampled] >= 8.0))
        if outside_sampled.any()
        else 0.0
    )
    boundary_score = (
        0.50 * roof_inside
        + 0.35 * ground_outside
        + 0.15 * (1.0 - roof_leakage_outside)
    )
    lidar_score = (
        0.65 * boundary_score
        + 0.20 * roof_support
        + 0.15 * elevated_coverage
    )
    result.update(
        {
            "selection_score": round(
                0.70 * base_selection_score + 0.30 * lidar_score,
                4,
            ),
            "lidar_roof_support_fraction": round(roof_support, 4),
            "lidar_sampled_fraction": round(sampled_fraction, 4),
            "lidar_ground_fraction": round(ground_fraction, 4),
            "lidar_elevated_coverage": round(elevated_coverage, 4),
            "lidar_boundary_score": round(boundary_score, 4),
            "lidar_roof_leakage_outside": round(
                roof_leakage_outside,
                4,
            ),
        }
    )
    return result


def _write_candidate_contact_sheet(
    *,
    context: dict[str, Any],
    candidates: list[dict[str, Any]],
    artifact_dir: Path,
    source_asset_name: str = "satellite.png",
) -> str:
    context_path = _context_path(artifact_dir, str(context["context_id"]))
    source = Image.open(context_path / source_asset_name).convert("RGBA")
    display_candidates = [
        {
            **candidate,
            "components": candidate.get("display_components")
            or candidate.get("components")
            or [],
        }
        for candidate in candidates
    ]
    review_crop = _candidate_review_crop_box(source.size, display_candidates)
    panels: list[Image.Image] = []
    colors = [
        (255, 80, 60, 255),
        (0, 180, 125, 255),
        (255, 183, 0, 255),
    ]
    for candidate, color in zip(display_candidates, colors):
        panel = source.copy()
        overlay = Image.new("RGBA", panel.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        for component in candidate.get("components") or []:
            polygon = _ring_tuples(component.get("polygon") or [])
            if len(polygon) < 3:
                continue
            draw.polygon(polygon, fill=(*color[:3], 70))
            draw.line([*polygon, polygon[0]], fill=color, width=7)
        panel = Image.alpha_composite(panel, overlay).crop(review_crop).convert("RGB")
        target_width = 1280
        target_height = max(1, int(round(panel.height * target_width / panel.width)))
        panel = panel.resize(
            (target_width, target_height),
            Image.Resampling.LANCZOS,
        )
        panel_draw = ImageDraw.Draw(panel, "RGBA")
        panel_draw.rectangle((0, 0, panel.width, 58), fill=(0, 0, 0, 175))
        refinement_label = str(
            candidate.get("boundary_refinement") or "sam2"
        ).replace("_", " ")
        if candidate.get("geometry_refinement") == "dominant_orthogonal":
            refinement_label += " + right-angle cleanup"
        panel_draw.text(
            (16, 12),
            (
                f"AI-selected roof boundary | "
                f"{float(candidate['plan_area_sqft']):,.0f} sq ft | "
                f"score {float(candidate['selection_score']):.2f} | "
                f"{refinement_label}"
            ),
            fill=(255, 255, 255, 255),
        )
        panels.append(panel)
    width = max(panel.width for panel in panels)
    height = sum(panel.height for panel in panels)
    sheet = Image.new("RGB", (width, height), "white")
    offset = 0
    for panel in panels:
        sheet.paste(panel, (0, offset))
        offset += panel.height
    digest = hashlib.sha256(
        json.dumps(
            [item["candidate_id"] for item in candidates],
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:16]
    asset_name = f"sam2-candidates-{digest}.png"
    (context_path / asset_name).write_bytes(image_png_bytes(sheet))
    return asset_name


def _candidate_review_crop_box(
    image_size: tuple[int, int],
    candidates: list[dict[str, Any]],
    *,
    minimum_side_pixels: int = 320,
    padding_ratio: float = 0.30,
) -> tuple[int, int, int, int]:
    width, height = image_size
    points = [
        point
        for candidate in candidates
        for component in candidate.get("components") or []
        for point in _ring_tuples(component.get("polygon") or [])
    ]
    if not points:
        return (0, 0, width, height)

    min_x = min(point[0] for point in points)
    max_x = max(point[0] for point in points)
    min_y = min(point[1] for point in points)
    max_y = max(point[1] for point in points)
    span = max(max_x - min_x, max_y - min_y, 1.0)
    maximum_side = min(width, height)
    side = min(
        maximum_side,
        max(minimum_side_pixels, int(round(span * (1.0 + 2.0 * padding_ratio)))),
    )
    center_x = (min_x + max_x) / 2.0
    center_y = (min_y + max_y) / 2.0
    left = max(0, min(width - side, int(round(center_x - side / 2.0))))
    top = max(0, min(height - side, int(round(center_y - side / 2.0))))
    return (left, top, left + side, top + side)
