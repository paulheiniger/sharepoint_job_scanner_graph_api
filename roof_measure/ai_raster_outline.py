from __future__ import annotations

import base64
import os
from collections import deque
from dataclasses import dataclass, field
from io import BytesIO

import numpy as np
from PIL import Image, ImageDraw

from .models import Ring
from .polygonize import sections_from_mask


@dataclass
class RasterOutlineSuggestion:
    polygons: list[Ring] = field(default_factory=list)
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)
    registration_mean_difference: float | None = None
    edited_image: Image.Image | None = None


def suggest_raster_roof_outline(
    image: Image.Image,
    *,
    footprint_polygons: list[Ring] | None = None,
) -> RasterOutlineSuggestion:
    """Ask an image-edit model for a yellow boundary, then repair only credible short gaps."""
    try:
        edited = _edit_image_with_yellow_boundary(image)
    except Exception as exc:
        return RasterOutlineSuggestion(warnings=[f"Raster AI boundary suggestion failed: {type(exc).__name__}: {exc}"])
    polygons, registration, warning = _yellow_boundary_to_polygons(
        image,
        edited,
        footprint_polygons=footprint_polygons,
    )
    if not polygons and warning and "not a closed perimeter" in warning:
        try:
            edited = _repair_yellow_boundary(edited)
            polygons, registration, warning = _yellow_boundary_to_polygons(
                image,
                edited,
                footprint_polygons=footprint_polygons,
            )
        except Exception as exc:
            warning = f"Raster AI boundary repair failed: {type(exc).__name__}: {exc}"
    warnings = [warning] if warning else []
    return RasterOutlineSuggestion(
        polygons=polygons,
        confidence=0.8 if polygons else 0.0,
        warnings=warnings,
        registration_mean_difference=registration,
        edited_image=edited,
    )


def yellow_boundary_overlay(source: Image.Image, edited: Image.Image) -> Image.Image:
    """Transfer only the generated yellow annotation onto registered source pixels."""
    target_size = _edit_size(source.size)
    if edited.size != target_size:
        raise ValueError("Raster boundary image dimensions do not match the expected edit size.")
    yellow = _yellow_mask(edited)
    if not yellow.any():
        raise ValueError("Raster boundary image does not contain a yellow annotation.")
    mask = Image.fromarray(yellow.astype(np.uint8) * 255, mode="L").resize(source.size, Image.Resampling.NEAREST)
    base = source.convert("RGBA")
    band = Image.new("RGBA", source.size, (255, 212, 0, 0))
    band.putalpha(mask)
    return Image.alpha_composite(base, band).convert("RGB")


def _edit_image_with_yellow_boundary(image: Image.Image) -> Image.Image:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set")

    source = image.convert("RGB")
    target_size = _edit_size(source.size)
    edit_source = source.resize(target_size, Image.Resampling.LANCZOS)
    payload = BytesIO()
    edit_source.save(payload, format="PNG")
    payload.seek(0)
    payload.name = "roof-source.png"
    response = _image_edit(
        payload,
        prompt=(
            "Preserve the original image exactly. Draw one continuous 6-pixel bright-yellow (#FFD400) line around the exterior perimeter of the target school building complex. "
            "Treat physically joined roof structures as one building. Do not split at roof seams, shadows, parapet shadows, or narrow roof-to-roof transitions. "
            "Trace the physical outside wall or parapet edge, not the edge of a dark cast shadow. Exclude obvious grass, parking, roads, open courtyards, and detached buildings. "
            "Return to the starting point with no gaps. Do not add text, shading, points, labels, interior lines, or any other changes."
        ),
        size=target_size,
    )
    image_bytes = base64.b64decode(response.data[0].b64_json)
    return Image.open(BytesIO(image_bytes)).convert("RGB")


def _repair_yellow_boundary(edited: Image.Image) -> Image.Image:
    payload = BytesIO()
    edited.save(payload, format="PNG")
    payload.seek(0)
    payload.name = "roof-annotated.png"
    response = _image_edit(
        payload,
        prompt=(
            "Preserve every satellite pixel and every existing correct yellow band segment exactly. Repair only the yellow annotation: "
            "join endpoints into one continuous 6-pixel bright-yellow (#FFD400) closed exterior perimeter around the target school building complex. "
            "Keep physically joined roof structures together; do not split at roof seams or shadows. Keep roof membrane beside cast shadows inside the line and trace the physical outside wall or parapet edge. "
            "Remove yellow interior lines. Do not alter or redraw the satellite image, buildings, colors, crop, labels, or add any marks other than that one closed yellow perimeter."
        ),
        size=edited.size,
    )
    return Image.open(BytesIO(base64.b64decode(response.data[0].b64_json))).convert("RGB")


def _image_edit(image_file: BytesIO, *, prompt: str, size: tuple[int, int]):
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set")
    from openai import OpenAI  # type: ignore

    return OpenAI(timeout=float(os.getenv("OPENAI_ROOF_MEASURE_RASTER_OUTLINE_TIMEOUT_SECONDS", "180"))).images.edit(
        model=os.getenv("OPENAI_ROOF_MEASURE_RASTER_OUTLINE_MODEL", "gpt-image-2"),
        image=image_file,
        prompt=prompt,
        size=f"{size[0]}x{size[1]}",
        quality=os.getenv("OPENAI_ROOF_MEASURE_RASTER_OUTLINE_QUALITY", "medium"),
        output_format="png",
    )


def _yellow_boundary_to_polygons(
    source: Image.Image,
    edited: Image.Image,
    *,
    footprint_polygons: list[Ring] | None = None,
) -> tuple[list[Ring], float | None, str | None]:
    target_size = _edit_size(source.size)
    if edited.size != target_size:
        return [], None, "Raster AI boundary was rejected because the edited image dimensions changed."
    source_scaled = source.convert("RGB").resize(target_size, Image.Resampling.LANCZOS)
    yellow = _yellow_mask(edited)
    if int(yellow.sum()) < 800:
        return [], None, "Raster AI boundary was rejected because it did not contain enough bright-yellow pixels."
    registration = _registration_difference(source_scaled, edited, yellow)
    if registration > 14.0:
        return [], registration, "Raster AI boundary was rejected because the image edit altered non-boundary pixels too much."
    footprint_mask = _scaled_footprint_mask(footprint_polygons or [], source.size, target_size)
    repaired, repairs = _repair_short_boundary_gaps(yellow, footprint_mask=footprint_mask)
    closed = _morphological_close(repaired, iterations=2)
    interior = _filled_interior(closed)
    if float(interior.mean()) < 0.03:
        repair_note = f" after {repairs} constrained endpoint repair(s)" if repairs else ""
        return [], registration, f"Raster AI boundary was rejected because the yellow band was not a closed perimeter{repair_note}."
    sections = sections_from_mask(interior, simplification_tolerance=3.0, minimum_section_area_pixels=400, edge_snap_strength=0.0)
    if not sections:
        return [], registration, "Raster AI boundary was rejected because no valid filled polygon could be extracted."
    scale_x = source.width / target_size[0]
    scale_y = source.height / target_size[1]
    polygons = [[(float(x) * scale_x, float(y) * scale_y) for x, y in section.polygon] for section in sections]
    return polygons, registration, None


def _scaled_footprint_mask(polygons: list[Ring], source_size: tuple[int, int], target_size: tuple[int, int]) -> np.ndarray | None:
    if not polygons:
        return None
    scale_x = target_size[0] / source_size[0]
    scale_y = target_size[1] / source_size[1]
    canvas = Image.new("L", target_size, 0)
    draw = ImageDraw.Draw(canvas)
    for polygon in polygons:
        if len(polygon) >= 3:
            draw.polygon([(float(x) * scale_x, float(y) * scale_y) for x, y in polygon], fill=255)
    mask = np.asarray(canvas) > 0
    if not mask.any():
        return None
    return _morphological_close(mask, iterations=0) | _buffer_mask(mask, iterations=24)


def _buffer_mask(mask: np.ndarray, *, iterations: int) -> np.ndarray:
    buffered = np.asarray(mask, dtype=bool)
    for _ in range(iterations):
        buffered = _dilate(buffered)
    return buffered


def _repair_short_boundary_gaps(yellow: np.ndarray, *, footprint_mask: np.ndarray | None) -> tuple[np.ndarray, int]:
    """Join only short, direction-compatible fragments; never globally bridge a boundary."""
    repaired = np.asarray(yellow, dtype=bool).copy()
    max_gap = int(os.getenv("OPENAI_ROOF_MEASURE_RASTER_MAX_GAP_PIXELS", "40"))
    max_repairs = int(os.getenv("OPENAI_ROOF_MEASURE_RASTER_MAX_GAP_REPAIRS", "8"))
    repairs = 0
    for _ in range(max_repairs):
        skeleton = _thin_boundary(repaired)
        labels, count = _connected_components(skeleton)
        endpoints = _skeleton_endpoints(skeleton)
        if count <= 1 and len(endpoints) != 2:
            break
        candidate = _best_gap_candidate(skeleton, labels, endpoints, max_gap=max_gap, footprint_mask=footprint_mask)
        if candidate is None:
            break
        start, end = candidate
        _draw_line(repaired, start, end, width=8)
        repairs += 1
    return repaired, repairs


def _thin_boundary(mask: np.ndarray, *, max_iterations: int = 80) -> np.ndarray:
    """Zhang-Suen thinning implemented with array operations to avoid a heavy CV dependency."""
    image = np.asarray(mask, dtype=bool).copy()
    for _ in range(max_iterations):
        deleted = False
        for phase in (0, 1):
            padded = np.pad(image, 1, constant_values=False)
            p2 = padded[:-2, 1:-1]
            p3 = padded[:-2, 2:]
            p4 = padded[1:-1, 2:]
            p5 = padded[2:, 2:]
            p6 = padded[2:, 1:-1]
            p7 = padded[2:, :-2]
            p8 = padded[1:-1, :-2]
            p9 = padded[:-2, :-2]
            neighbors = (p2.astype(np.uint8) + p3 + p4 + p5 + p6 + p7 + p8 + p9)
            transitions = (
                (~p2 & p3).astype(np.uint8)
                + (~p3 & p4)
                + (~p4 & p5)
                + (~p5 & p6)
                + (~p6 & p7)
                + (~p7 & p8)
                + (~p8 & p9)
                + (~p9 & p2)
            )
            if phase == 0:
                preserve = ~(p2 & p4 & p6) & ~(p4 & p6 & p8)
            else:
                preserve = ~(p2 & p4 & p8) & ~(p2 & p6 & p8)
            remove = image & (neighbors >= 2) & (neighbors <= 6) & (transitions == 1) & preserve
            if remove.any():
                image[remove] = False
                deleted = True
        if not deleted:
            break
    return image


def _connected_components(mask: np.ndarray) -> tuple[np.ndarray, int]:
    labels = np.zeros(mask.shape, dtype=np.int32)
    height, width = mask.shape
    count = 0
    for y, x in np.argwhere(mask):
        if labels[y, x]:
            continue
        count += 1
        labels[y, x] = count
        queue: deque[tuple[int, int]] = deque([(int(y), int(x))])
        while queue:
            current_y, current_x = queue.popleft()
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if not dy and not dx:
                        continue
                    next_y, next_x = current_y + dy, current_x + dx
                    if 0 <= next_y < height and 0 <= next_x < width and mask[next_y, next_x] and not labels[next_y, next_x]:
                        labels[next_y, next_x] = count
                        queue.append((next_y, next_x))
    return labels, count


def _skeleton_endpoints(skeleton: np.ndarray) -> list[tuple[int, int]]:
    padded = np.pad(skeleton, 1, constant_values=False)
    neighbors = np.zeros_like(skeleton, dtype=np.uint8)
    for y_offset in range(3):
        for x_offset in range(3):
            if y_offset != 1 or x_offset != 1:
                neighbors += padded[y_offset : y_offset + skeleton.shape[0], x_offset : x_offset + skeleton.shape[1]]
    return [(int(y), int(x)) for y, x in np.argwhere(skeleton & (neighbors == 1))]


def _best_gap_candidate(
    skeleton: np.ndarray,
    labels: np.ndarray,
    endpoints: list[tuple[int, int]],
    *,
    max_gap: int,
    footprint_mask: np.ndarray | None,
) -> tuple[tuple[int, int], tuple[int, int]] | None:
    best: tuple[float, tuple[int, int], tuple[int, int]] | None = None
    for index, start in enumerate(endpoints):
        for end in endpoints[index + 1 :]:
            # A nearly closed ring is one connected component with exactly two
            # endpoints. Joining those two endpoints is the intended repair.
            if labels[start] == labels[end] and len(endpoints) != 2:
                continue
            distance = float(np.hypot(end[0] - start[0], end[1] - start[1]))
            if distance > max_gap:
                continue
            if not _gap_aligns_with_tangents(skeleton, labels, start, end):
                continue
            if not _gap_path_is_clear(skeleton, start, end):
                continue
            path = _line_pixels(start, end)
            if footprint_mask is not None and path:
                inside_fraction = float(sum(bool(footprint_mask[y, x]) for y, x in path)) / len(path)
                if inside_fraction < 0.65:
                    continue
            score = distance
            if best is None or score < best[0]:
                best = (score, start, end)
    return (best[1], best[2]) if best else None


def _gap_aligns_with_tangents(
    skeleton: np.ndarray,
    labels: np.ndarray,
    start: tuple[int, int],
    end: tuple[int, int],
) -> bool:
    direction = np.asarray((end[0] - start[0], end[1] - start[1]), dtype=float)
    length = float(np.linalg.norm(direction))
    if length == 0:
        return False
    direction /= length
    start_tangent = _endpoint_tangent(skeleton, labels, start)
    end_tangent = _endpoint_tangent(skeleton, labels, end)
    return float(np.dot(start_tangent, direction)) >= 0.45 and float(np.dot(end_tangent, -direction)) >= 0.45


def _endpoint_tangent(skeleton: np.ndarray, labels: np.ndarray, endpoint: tuple[int, int]) -> np.ndarray:
    y, x = endpoint
    component = labels[y, x]
    candidates: list[tuple[int, int]] = []
    for radius in range(1, 13):
        for next_y in range(max(0, y - radius), min(skeleton.shape[0], y + radius + 1)):
            for next_x in range(max(0, x - radius), min(skeleton.shape[1], x + radius + 1)):
                if labels[next_y, next_x] == component and (next_y, next_x) != endpoint:
                    candidates.append((next_y, next_x))
        if candidates:
            break
    if not candidates:
        return np.zeros(2)
    furthest = max(candidates, key=lambda point: (point[0] - y) ** 2 + (point[1] - x) ** 2)
    tangent = np.asarray((y - furthest[0], x - furthest[1]), dtype=float)
    norm = float(np.linalg.norm(tangent))
    return tangent / norm if norm else np.zeros(2)


def _gap_path_is_clear(skeleton: np.ndarray, start: tuple[int, int], end: tuple[int, int]) -> bool:
    for y, x in _line_pixels(start, end):
        y0, y1 = max(0, y - 2), min(skeleton.shape[0], y + 3)
        x0, x1 = max(0, x - 2), min(skeleton.shape[1], x + 3)
        if skeleton[y0:y1, x0:x1].any() and min(np.hypot(y - start[0], x - start[1]), np.hypot(y - end[0], x - end[1])) > 4:
            return False
    return True


def _line_pixels(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    length = max(abs(end[0] - start[0]), abs(end[1] - start[1]))
    if not length:
        return [start]
    return [
        (int(round(start[0] + (end[0] - start[0]) * step / length)), int(round(start[1] + (end[1] - start[1]) * step / length)))
        for step in range(length + 1)
    ]


def _draw_line(mask: np.ndarray, start: tuple[int, int], end: tuple[int, int], *, width: int) -> None:
    canvas = Image.fromarray(mask.astype(np.uint8) * 255)
    ImageDraw.Draw(canvas).line([(start[1], start[0]), (end[1], end[0])], fill=255, width=width)
    mask[:] = np.asarray(canvas) > 0


def _edit_size(size: tuple[int, int]) -> tuple[int, int]:
    width, height = size
    scale = max(1024 / min(width, height), 1.0)
    scaled_width = int(round(width * scale / 16)) * 16
    scaled_height = int(round(height * scale / 16)) * 16
    return max(16, scaled_width), max(16, scaled_height)


def _yellow_mask(image: Image.Image) -> np.ndarray:
    hsv = np.asarray(image.convert("HSV"))
    return (hsv[..., 0] >= 25) & (hsv[..., 0] <= 55) & (hsv[..., 1] >= 130) & (hsv[..., 2] >= 150)


def _registration_difference(source: Image.Image, edited: Image.Image, yellow: np.ndarray) -> float:
    source_array = np.asarray(source, dtype=np.int16)
    edited_array = np.asarray(edited, dtype=np.int16)
    diff = np.abs(source_array - edited_array).mean(axis=2)
    non_boundary = ~_dilate(yellow)
    return float(diff[non_boundary].mean()) if non_boundary.any() else float(diff.mean())


def _morphological_close(mask: np.ndarray, *, iterations: int) -> np.ndarray:
    closed = np.asarray(mask, dtype=bool)
    for _ in range(iterations):
        closed = _dilate(closed)
    for _ in range(iterations):
        closed = _erode(closed)
    return closed


def _dilate(mask: np.ndarray) -> np.ndarray:
    padded = np.pad(mask, 1, constant_values=False)
    result = np.zeros_like(mask)
    for y_offset in range(3):
        for x_offset in range(3):
            result |= padded[y_offset : y_offset + mask.shape[0], x_offset : x_offset + mask.shape[1]]
    return result


def _erode(mask: np.ndarray) -> np.ndarray:
    padded = np.pad(mask, 1, constant_values=True)
    result = np.ones_like(mask)
    for y_offset in range(3):
        for x_offset in range(3):
            result &= padded[y_offset : y_offset + mask.shape[0], x_offset : x_offset + mask.shape[1]]
    return result


def _filled_interior(boundary: np.ndarray) -> np.ndarray:
    open_space = ~boundary
    outside = np.zeros_like(open_space)
    queue: deque[tuple[int, int]] = deque()
    height, width = open_space.shape
    for x in range(width):
        for y in (0, height - 1):
            if open_space[y, x] and not outside[y, x]:
                outside[y, x] = True
                queue.append((y, x))
    for y in range(height):
        for x in (0, width - 1):
            if open_space[y, x] and not outside[y, x]:
                outside[y, x] = True
                queue.append((y, x))
    while queue:
        y, x = queue.popleft()
        for next_y, next_x in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= next_y < height and 0 <= next_x < width and open_space[next_y, next_x] and not outside[next_y, next_x]:
                outside[next_y, next_x] = True
                queue.append((next_y, next_x))
    return (~outside) & (~boundary)
