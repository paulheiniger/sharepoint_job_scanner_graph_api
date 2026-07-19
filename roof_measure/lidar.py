from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np


@dataclass
class LidarMaskAssessment:
    ok: bool
    roof_support_fraction: float = 0.0
    ground_fraction: float = 0.0
    sampled_cells: int = 0
    warning: str = ""

    def as_record(self) -> dict[str, object]:
        return {
            "roof_support_fraction": round(self.roof_support_fraction, 3),
            "ground_fraction": round(self.ground_fraction, 3),
            "sampled_cells": self.sampled_cells,
            "warning": self.warning,
        }


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
    scale_x = mask_bool.shape[1] / source_width
    scale_y = mask_bool.shape[0] / source_height
    try:
        reader = CopcReader.open(asset_url, http_num_threads=4)
        lidar_crs = reader.header.parse_crs()
        if lidar_crs is None:
            return LidarMaskAssessment(ok=False, warning="LiDAR tile does not declare a coordinate reference system.")
        to_lidar = Transformer.from_crs(4326, lidar_crs, always_xy=True)
        to_wgs84 = Transformer.from_crs(lidar_crs, 4326, always_xy=True)
        corners = [
            _image_pixel_to_lon_lat(x, y, center_latitude, center_longitude, zoom, source_width, source_height)
            for x, y in ((0, 0), (source_width, 0), (0, source_height), (source_width, source_height))
        ]
        xs, ys = to_lidar.transform([point[0] for point in corners], [point[1] for point in corners])
        points = reader.query(Bounds((min(xs), min(ys)), (max(xs), max(ys))), resolution=5)
        if len(points) == 0:
            return LidarMaskAssessment(ok=False, warning="LiDAR tile returned no points for the visible image extent.")
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
        height_grid = _height_grid_from_points(
            source_x * scale_x,
            source_y * scale_y,
            np.asarray(points.z),
            np.asarray(points.classification),
            mask_bool.shape,
            cell_pixels=max(2, int(cell_pixels)),
        )
        return assess_mask_against_height_grid(mask_bool, height_grid, cell_pixels=max(2, int(cell_pixels)))
    except Exception as exc:
        return LidarMaskAssessment(ok=False, warning=f"LiDAR height-prior evaluation failed: {type(exc).__name__}: {exc}")


def assess_mask_against_height_grid(mask: np.ndarray, height_grid: np.ndarray, *, cell_pixels: int) -> LidarMaskAssessment:
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
    return LidarMaskAssessment(
        ok=True,
        roof_support_fraction=roof_support,
        ground_fraction=ground_fraction,
        sampled_cells=int(sampled.sum()),
        warning=(
            "LiDAR indicates substantial ground/pavement overlap; verify the target footprint and prompts."
            if ground_fraction > 0.35
            else ""
        ),
    )


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
    ground = np.full(grid_width * grid_height, np.nan)
    ground_mask = classes == 2
    if not ground_mask.any():
        return np.full((grid_height, grid_width), np.nan)
    np.maximum.at(ground, flat[ground_mask], z[ground_mask])
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
