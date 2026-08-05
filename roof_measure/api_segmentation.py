from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
from .lidar import kyfromabove_height_grid_for_image
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


def segment_roof_measure_context(
    *,
    context_id: str,
    selected_footprint_ids: list[str],
    sam2_url: str,
    sam2_api_key: str,
    artifact_dir: Path,
    timeout_seconds: float = 90.0,
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
    image = Image.open(context_path / "satellite.png").convert("RGB")
    image_array = np.asarray(image)
    selected_candidates = [candidate_by_id[item] for item in selected_footprint_ids]
    components = [
        component
        for candidate in selected_candidates
        for component in candidate.get("components") or []
    ]
    footprint_mask = _components_mask(image.size, components)
    if not footprint_mask.any():
        raise RoofMeasureInputError(
            "The selected footprints did not produce a usable image-space prompt."
        )

    buffer_pixels = max(
        6,
        min(48, int(round(float(context["pixels_per_foot"]) * 10.0))),
    )
    lidar_guidance, lidar_warning = _load_lidar_guidance(
        context=context,
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
                pixels_per_foot=float(context["pixels_per_foot"]),
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
    selected: list[dict[str, Any]] = []
    for item in [processed[0], best_raw, best_refined, *processed]:
        if item is not None and item not in selected:
            selected.append(item)
        if len(selected) == 3:
            break
    processed = sorted(
        selected,
        key=lambda item: (
            float(item["selection_score"]),
            float(item["model_score"]),
        ),
        reverse=True,
    )
    for rank, item in enumerate(processed, start=1):
        item["rank"] = rank

    asset_name = _write_candidate_contact_sheet(
        context=context,
        candidates=processed,
        artifact_dir=artifact_dir,
    )
    context["sam2_candidates"] = processed
    context["sam2_candidate_asset_name"] = asset_name
    context["sam2_source_footprint_ids"] = list(selected_footprint_ids)
    _write_json_atomic(context_path / "context.json", context)
    return {
        "schema_version": "spraytec.roof_measure_sam2_candidates.v1",
        "context_id": context_id,
        "selected_footprint_ids": list(selected_footprint_ids),
        "recommended_candidate_id": processed[0]["candidate_id"],
        "requires_candidate_confirmation": True,
        "candidates": [_public_candidate(item) for item in processed],
        "candidate_overlay_asset_name": asset_name,
        "model_name": segmentation.model_name,
        "model_version": segmentation.model_version,
        "lidar_guidance_used": lidar_guidance is not None,
        "lidar_points": lidar_guidance.lidar_points if lidar_guidance else 0,
        "lidar_image_points": lidar_guidance.image_points if lidar_guidance else 0,
        "lidar_cell_pixels": lidar_guidance.cell_pixels if lidar_guidance else 0,
        "warnings": [
            *segmentation.warnings,
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
            (
                "SAM2 candidates are model-derived refinements of reviewed building "
                "footprints. Display the candidate overlay and obtain estimator "
                "confirmation before calculating area."
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


def _public_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in candidate.items()
        if key not in {"components", "selected_footprint_ids"}
    }


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
) -> str:
    context_path = _context_path(artifact_dir, str(context["context_id"]))
    source = Image.open(context_path / "satellite.png").convert("RGBA")
    panels: list[Image.Image] = []
    colors = [
        (255, 80, 60, 255),
        (0, 180, 125, 255),
        (255, 183, 0, 255),
    ]
    for candidate, color in zip(candidates, colors):
        panel = source.copy()
        overlay = Image.new("RGBA", panel.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        for component in candidate.get("components") or []:
            polygon = _ring_tuples(component.get("polygon") or [])
            if len(polygon) < 3:
                continue
            draw.polygon(polygon, fill=(*color[:3], 70))
            draw.line([*polygon, polygon[0]], fill=color, width=7)
        draw.rectangle((0, 0, panel.width, 58), fill=(0, 0, 0, 175))
        draw.text(
            (16, 12),
            (
                f"Candidate {candidate['rank']} | "
                f"{float(candidate['plan_area_sqft']):,.0f} sq ft | "
                f"score {float(candidate['selection_score']):.2f} | "
                f"{str(candidate.get('boundary_refinement') or 'sam2').replace('_', ' ')}"
            ),
            fill=(255, 255, 255, 255),
        )
        panel = Image.alpha_composite(panel, overlay).convert("RGB")
        panel.thumbnail((640, 640), Image.Resampling.LANCZOS)
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
