from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import math

import numpy as np


@dataclass
class LidarMaskAssessment:
    ok: bool
    roof_support_fraction: float = 0.0
    ground_fraction: float = 0.0
    sampled_cells: int = 0
    lidar_points: int = 0
    image_points: int = 0
    elevated_core_retention: float | None = None
    warning: str = ""

    def as_record(self) -> dict[str, object]:
        return {
            "roof_support_fraction": round(self.roof_support_fraction, 3),
            "ground_fraction": round(self.ground_fraction, 3),
            "sampled_cells": self.sampled_cells,
            "lidar_points": self.lidar_points,
            "image_points": self.image_points,
            "elevated_core_retention": None if self.elevated_core_retention is None else round(self.elevated_core_retention, 3),
            "warning": self.warning,
        }


@dataclass
class LidarHeightGrid:
    """Cached image-aligned height-above-ground grid for local edge scoring."""

    height_grid: np.ndarray | None = None
    cell_pixels: int = 8
    lidar_points: int = 0
    image_points: int = 0
    warning: str = ""

    @property
    def ok(self) -> bool:
        return self.height_grid is not None and bool(np.isfinite(self.height_grid).any())


def kyfromabove_height_grid_for_image(
    *,
    asset_url: str,
    center_latitude: float,
    center_longitude: float,
    zoom: float,
    source_width: int,
    source_height: int,
    image_width: int,
    image_height: int,
    cell_pixels: int = 8,
) -> LidarHeightGrid:
    """Load one image-aligned LiDAR grid for several local geometry decisions.

    The cached COPC query is shared with final mask validation.  Callers should
    use this as soft evidence rather than rejecting geometry when it is absent.
    """
    if not asset_url:
        return LidarHeightGrid(warning="No LiDAR asset is available for this image.")
    try:
        height_grid, lidar_points, image_points = _cached_kyfromabove_height_grid(
            asset_url,
            float(center_latitude),
            float(center_longitude),
            float(zoom),
            int(source_width),
            int(source_height),
            int(image_width),
            int(image_height),
            max(2, int(cell_pixels)),
        )
        return LidarHeightGrid(
            height_grid=height_grid,
            cell_pixels=max(2, int(cell_pixels)),
            lidar_points=lidar_points,
            image_points=image_points,
        )
    except Exception as exc:
        return LidarHeightGrid(warning=f"LiDAR edge evidence was unavailable: {type(exc).__name__}: {exc}")


def assess_mask_against_kyfromabove_lidar(
    *,
    asset_url: str,
    mask: np.ndarray,
    center_latitude: float,
    center_longitude: float,
    zoom: float,
    source_width: int | None = None,
    source_height: int | None = None,
    cell_pixels: int = 8,
    candidate_mask: np.ndarray | None = None,
) -> LidarMaskAssessment:
    """Compare a selected image mask with public LiDAR roof-versus-ground evidence.

    The COPC query is limited to the visible image extent and uses a coarse grid.
    It is deliberately a validation signal, not a replacement roof polygon.
    """
    try:
        from laspy.copc import Bounds, CopcReader
        from pyproj import Transformer
    except Exception as exc:  # pragma: no cover - optional local capability
        return LidarMaskAssessment(ok=False, warning=f"LiDAR dependencies are unavailable: {type(exc).__name__}: {exc}")
    mask_bool = np.asarray(mask, dtype=bool)
    if mask_bool.ndim != 2 or not mask_bool.any():
        return LidarMaskAssessment(ok=False, warning="LiDAR validation requires a non-empty segmentation mask.")
    source_width = int(source_width or mask_bool.shape[1])
    source_height = int(source_height or mask_bool.shape[0])
    try:
        grid = kyfromabove_height_grid_for_image(
            asset_url=asset_url,
            center_latitude=float(center_latitude),
            center_longitude=float(center_longitude),
            zoom=float(zoom),
            source_width=int(source_width),
            source_height=int(source_height),
            image_width=int(mask_bool.shape[1]),
            image_height=int(mask_bool.shape[0]),
            cell_pixels=max(2, int(cell_pixels)),
        )
        if not grid.ok or grid.height_grid is None:
            return LidarMaskAssessment(ok=False, warning=grid.warning or "LiDAR height grid is unavailable.")
        assessment = assess_mask_against_height_grid(
            mask_bool,
            grid.height_grid,
            cell_pixels=grid.cell_pixels,
            candidate_mask=candidate_mask,
        )
        assessment.lidar_points = grid.lidar_points
        assessment.image_points = grid.image_points
        return assessment
    except Exception as exc:
        return LidarMaskAssessment(ok=False, warning=f"LiDAR height-prior evaluation failed: {type(exc).__name__}: {exc}")


@lru_cache(maxsize=8)
def _cached_kyfromabove_height_grid(
    asset_url: str,
    center_latitude: float,
    center_longitude: float,
    zoom: float,
    source_width: int,
    source_height: int,
    image_width: int,
    image_height: int,
    cell_pixels: int,
) -> tuple[np.ndarray, int, int]:
    from laspy.copc import Bounds, CopcReader
    from pyproj import Transformer

    reader = CopcReader.open(asset_url, http_num_threads=4)
    lidar_crs = reader.header.parse_crs()
    if lidar_crs is None:
        raise ValueError("LiDAR tile does not declare a coordinate reference system.")
    to_lidar = Transformer.from_crs(4326, lidar_crs, always_xy=True)
    to_wgs84 = Transformer.from_crs(lidar_crs, 4326, always_xy=True)
    corners = [
        _image_pixel_to_lon_lat(x, y, center_latitude, center_longitude, zoom, source_width, source_height)
        for x, y in ((0, 0), (source_width, 0), (0, source_height), (source_width, source_height))
    ]
    xs, ys = to_lidar.transform([point[0] for point in corners], [point[1] for point in corners])
    points = reader.query(Bounds((min(xs), min(ys)), (max(xs), max(ys))), resolution=5)
    if len(points) == 0:
        raise ValueError("LiDAR tile returned no points for the visible image extent.")
    longitudes, latitudes = to_wgs84.transform(np.asarray(points.x), np.asarray(points.y))
    source_x, source_y = _lon_lat_to_image_pixels(
        np.asarray(longitudes),
        np.asarray(latitudes),
        center_latitude,
        center_longitude,
        zoom,
        source_width,
        source_height,
    )
    scale_x = image_width / source_width
    scale_y = image_height / source_height
    image_valid = (
        (source_x * scale_x >= 0)
        & (source_x * scale_x < image_width)
        & (source_y * scale_y >= 0)
        & (source_y * scale_y < image_height)
    )
    height_grid = _height_grid_from_points(
        source_x * scale_x,
        source_y * scale_y,
        np.asarray(points.z),
        np.asarray(points.classification),
        (image_height, image_width),
        cell_pixels=cell_pixels,
    )
    return height_grid, len(points), int(image_valid.sum())


def assess_mask_against_height_grid(
    mask: np.ndarray,
    height_grid: np.ndarray,
    *,
    cell_pixels: int,
    candidate_mask: np.ndarray | None = None,
) -> LidarMaskAssessment:
    """Evaluate whether mask cells are elevated above local classified-ground points."""
    mask_bool = np.asarray(mask, dtype=bool)
    height = np.asarray(height_grid, dtype=float)
    if height.ndim != 2 or height.size == 0:
        return LidarMaskAssessment(ok=False, warning="LiDAR height grid is empty.")
    grid_mask = _mask_to_grid(mask_bool, height.shape, cell_pixels)
    sampled = grid_mask & np.isfinite(height)
    if not sampled.any():
        return LidarMaskAssessment(ok=False, warning="LiDAR has no sampled elevation cells inside the segmentation mask.")
    values = height[sampled]
    # Kentucky roof decks should be materially above adjacent classified ground.
    roof_support = float(np.mean(values >= 8.0))
    ground_fraction = float(np.mean(values < 4.0))
    core_retention = None
    if candidate_mask is not None:
        candidate = np.asarray(candidate_mask, dtype=bool)
        if candidate.shape != mask_bool.shape:
            raise ValueError("LiDAR candidate mask must match the source segmentation mask shape.")
        elevated_source = grid_mask & np.isfinite(height) & (height >= 8.0)
        if elevated_source.any():
            candidate_grid = _mask_to_grid(candidate, height.shape, cell_pixels)
            weights = _grid_core_weights(elevated_source)
            core_retention = float(weights[candidate_grid].sum()) / max(float(weights.sum()), 1.0)
    return LidarMaskAssessment(
        ok=True,
        roof_support_fraction=roof_support,
        ground_fraction=ground_fraction,
        sampled_cells=int(sampled.sum()),
        elevated_core_retention=core_retention,
        warning=(
            "LiDAR indicates substantial ground/pavement overlap; verify the target footprint and prompts."
            if ground_fraction > 0.35
            else ""
        ),
    )


def _grid_core_weights(mask: np.ndarray, *, max_depth: int = 8) -> np.ndarray:
    remaining = np.asarray(mask, dtype=bool).copy()
    weights = np.zeros(remaining.shape, dtype=float)
    for _ in range(max_depth):
        if not remaining.any():
            break
        weights[remaining] += 1.0
        padded = np.pad(remaining, 1, mode="constant", constant_values=False)
        eroded = np.ones_like(remaining)
        for y_offset in range(3):
            for x_offset in range(3):
                eroded &= padded[y_offset : y_offset + remaining.shape[0], x_offset : x_offset + remaining.shape[1]]
        remaining = eroded
    return weights


def _height_grid_from_points(
    image_x: np.ndarray,
    image_y: np.ndarray,
    elevations: np.ndarray,
    classifications: np.ndarray,
    image_shape: tuple[int, int],
    *,
    cell_pixels: int,
) -> np.ndarray:
    height, width = image_shape
    grid_width = max(1, math.ceil(width / cell_pixels))
    grid_height = max(1, math.ceil(height / cell_pixels))
    ix = np.floor(image_x / cell_pixels).astype(int)
    iy = np.floor(image_y / cell_pixels).astype(int)
    valid = (ix >= 0) & (ix < grid_width) & (iy >= 0) & (iy < grid_height)
    flat = iy[valid] * grid_width + ix[valid]
    z = elevations[valid]
    classes = classifications[valid]
    dsm = np.full(grid_width * grid_height, -np.inf)
    np.maximum.at(dsm, flat, z)
    ground = np.full(grid_width * grid_height, -np.inf)
    ground_mask = classes == 2
    if not ground_mask.any():
        return np.full((grid_height, grid_width), np.nan)
    np.maximum.at(ground, flat[ground_mask], z[ground_mask])
    ground[~np.isfinite(ground)] = np.nan
    dtm = _fill_nearest_ground(ground.reshape(grid_height, grid_width))
    dsm = dsm.reshape(grid_height, grid_width)
    return np.where(np.isfinite(dsm) & np.isfinite(dtm), dsm - dtm, np.nan)


def _fill_nearest_ground(grid: np.ndarray) -> np.ndarray:
    filled = np.asarray(grid, dtype=float).copy()
    for _ in range(filled.shape[0] + filled.shape[1]):
        missing = ~np.isfinite(filled)
        if not missing.any():
            break
        candidates = []
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            shifted = np.full_like(filled, np.nan)
            source_y = slice(max(0, -dy), filled.shape[0] - max(0, dy))
            source_x = slice(max(0, -dx), filled.shape[1] - max(0, dx))
            target_y = slice(max(0, dy), filled.shape[0] - max(0, -dy))
            target_x = slice(max(0, dx), filled.shape[1] - max(0, -dx))
            shifted[target_y, target_x] = filled[source_y, source_x]
            candidates.append(shifted)
        propagated = np.nanmean(np.stack(candidates), axis=0)
        changed = missing & np.isfinite(propagated)
        if not changed.any():
            break
        filled[changed] = propagated[changed]
    return filled


def _mask_to_grid(mask: np.ndarray, grid_shape: tuple[int, int], cell_pixels: int) -> np.ndarray:
    grid = np.zeros(grid_shape, dtype=bool)
    ys, xs = np.where(mask)
    if len(xs):
        grid[np.minimum(ys // cell_pixels, grid_shape[0] - 1), np.minimum(xs // cell_pixels, grid_shape[1] - 1)] = True
    return grid


def _lon_lat_to_image_pixels(longitude, latitude, center_latitude, center_longitude, zoom, width, height):
    world_size = 512 * (2**float(zoom))
    center_x, center_y = _mercator_world_pixels(center_longitude, center_latitude, world_size)
    world_x, world_y = _mercator_world_pixels(longitude, latitude, world_size)
    return width / 2 + world_x - center_x, height / 2 + world_y - center_y


def _image_pixel_to_lon_lat(x, y, center_latitude, center_longitude, zoom, width, height):
    world_size = 512 * (2**float(zoom))
    center_x, center_y = _mercator_world_pixels(center_longitude, center_latitude, world_size)
    world_x = center_x + float(x) - width / 2
    world_y = center_y + float(y) - height / 2
    longitude = world_x / world_size * 360 - 180
    latitude = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * world_y / world_size))))
    return longitude, latitude


def _mercator_world_pixels(longitude, latitude, world_size):
    latitude = np.clip(latitude, -85.05112878, 85.05112878)
    x = (np.asarray(longitude) + 180.0) / 360.0 * world_size
    latitude_radians = np.radians(latitude)
    y = (1.0 - np.arcsinh(np.tan(latitude_radians)) / math.pi) / 2.0 * world_size
    return x, y
