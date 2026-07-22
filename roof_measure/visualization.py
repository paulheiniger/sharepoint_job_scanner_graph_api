from __future__ import annotations

from io import BytesIO

import numpy as np
from PIL import Image, ImageDraw

from .models import RoofSection


def annotated_overlay(
    image: Image.Image,
    *,
    mask: np.ndarray | None = None,
    sections: list[RoofSection] | None = None,
    alpha: int = 90,
) -> Image.Image:
    base = image.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    if mask is not None:
        mask_bool = np.asarray(mask, dtype=bool)
        mask_image = Image.fromarray((mask_bool.astype("uint8") * alpha), mode="L").resize(base.size)
        red = Image.new("RGBA", base.size, (229, 40, 40, 0))
        red.putalpha(mask_image)
        overlay = Image.alpha_composite(overlay, red)
        draw = ImageDraw.Draw(overlay)
    for section in sections or []:
        polygon = [(float(x), float(y)) for x, y in section.polygon]
        if len(polygon) >= 3:
            draw.line(polygon, fill=(0, 151, 96, 255), width=4, joint="curve")
            draw.text(polygon[0], section.section_id, fill=(0, 80, 60, 255))
    return Image.alpha_composite(base, overlay).convert("RGB")


def boundary_residual_overlay(
    image: Image.Image,
    *,
    mask: np.ndarray,
    sections: list[RoofSection],
    tolerance_pixels: int = 3,
) -> Image.Image:
    """Highlight mask edges missed by the polygon and unsupported polygon edges."""
    from .service import sections_mask

    source = _mask_boundary(np.asarray(mask, dtype=bool))
    candidate = _mask_boundary(sections_mask(source.shape, sections))
    nearby_source = _dilate(source, max(0, int(tolerance_pixels)))
    nearby_candidate = _dilate(candidate, max(0, int(tolerance_pixels)))
    missed_source = source & ~nearby_candidate
    unsupported_candidate = candidate & ~nearby_source
    base = image.convert("RGBA")
    for residual, color in (
        (missed_source, (216, 27, 96, 230)),
        (unsupported_candidate, (0, 188, 212, 230)),
    ):
        alpha = Image.fromarray((residual.astype("uint8") * color[3]), mode="L").resize(base.size, Image.Resampling.NEAREST)
        layer = Image.new("RGBA", base.size, (*color[:3], 0))
        layer.putalpha(alpha)
        base = Image.alpha_composite(base, layer)
    return base.convert("RGB")


def _mask_boundary(mask: np.ndarray) -> np.ndarray:
    padded = np.pad(mask, 1, mode="constant", constant_values=False)
    eroded = np.ones_like(mask, dtype=bool)
    for y_offset in range(3):
        for x_offset in range(3):
            eroded &= padded[y_offset : y_offset + mask.shape[0], x_offset : x_offset + mask.shape[1]]
    return mask & ~eroded


def _dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    expanded = np.asarray(mask, dtype=bool).copy()
    for _ in range(radius):
        padded = np.pad(expanded, 1, mode="constant", constant_values=False)
        expanded = np.zeros_like(expanded)
        for y_offset in range(3):
            for x_offset in range(3):
                expanded |= padded[y_offset : y_offset + mask.shape[0], x_offset : x_offset + mask.shape[1]]
    return expanded


def prompt_points_overlay(
    image: Image.Image,
    *,
    positive_points: list[tuple[float, float]] | None = None,
    negative_points: list[tuple[float, float]] | None = None,
) -> Image.Image:
    base = image.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for index, point in enumerate(positive_points or [], start=1):
        _draw_point(draw, point, fill=(0, 151, 96, 235), outline=(255, 255, 255, 255), label=f"R{index}")
    for index, point in enumerate(negative_points or [], start=1):
        _draw_point(draw, point, fill=(229, 40, 40, 235), outline=(255, 255, 255, 255), label=f"X{index}")
    return Image.alpha_composite(base, overlay).convert("RGB")


def vertex_editor_overlay(
    image: Image.Image,
    *,
    sections: list[RoofSection],
    stage: str,
    labels: bool = True,
    edited_vertices: set[str] | None = None,
    locked_vertices: set[str] | None = None,
    vertex_labels: dict[str, str] | None = None,
) -> Image.Image:
    """A sparse numbered vertex view for the AI polygon editor."""
    base = image.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    edited_vertices = edited_vertices or set()
    locked_vertices = locked_vertices or set()
    vertex_labels = vertex_labels or {}
    for section in sections:
        exterior = _open_ring(section.polygon)
        if len(exterior) >= 3:
            draw.line([*exterior, exterior[0]], fill=(0, 229, 153, 255), width=4, joint="curve")
            for index, point in enumerate(exterior):
                vertex_id = f"{section.section_id}:{index}"
                _draw_vertex(
                    draw,
                    point,
                    vertex_labels.get(vertex_id, vertex_id),
                    fill=(33, 150, 243, 255) if vertex_id in locked_vertices else (255, 152, 0, 255) if vertex_id in edited_vertices else (0, 229, 153, 255),
                    show_label=labels,
                )
        if stage != "holes":
            continue
        for hole_index, hole in enumerate(section.holes):
            vertices = _open_ring(hole)
            if len(vertices) < 3:
                continue
            draw.line([*vertices, vertices[0]], fill=(255, 193, 7, 255), width=3, joint="curve")
            for vertex_index, point in enumerate(vertices):
                vertex_id = f"{section.section_id}:hole:{hole_index}:{vertex_index}"
                _draw_vertex(
                    draw,
                    point,
                    vertex_labels.get(vertex_id, vertex_id),
                    fill=(33, 150, 243, 255) if vertex_id in locked_vertices else (255, 152, 0, 255) if vertex_id in edited_vertices else (255, 193, 7, 255),
                    show_label=labels,
                )
    return Image.alpha_composite(base, overlay).convert("RGB")


def lidar_height_overlay(image: Image.Image, *, height_grid: np.ndarray | None, cell_pixels: int) -> Image.Image:
    """Show elevated LiDAR support in cyan and low ground support in amber."""
    if height_grid is None:
        return image.convert("RGB")
    height = np.asarray(height_grid, dtype=float)
    if height.ndim != 2 or not np.isfinite(height).any():
        return image.convert("RGB")
    roof = np.isfinite(height) & (height >= 8.0)
    ground = np.isfinite(height) & (height < 4.0)
    size = (image.width, image.height)
    roof_mask = Image.fromarray((roof.astype("uint8") * 110), mode="L").resize(size, Image.Resampling.NEAREST)
    ground_mask = Image.fromarray((ground.astype("uint8") * 80), mode="L").resize(size, Image.Resampling.NEAREST)
    composite = image.convert("RGBA")
    roof_layer = Image.new("RGBA", size, (0, 188, 212, 0))
    roof_layer.putalpha(roof_mask)
    ground_layer = Image.new("RGBA", size, (255, 152, 0, 0))
    ground_layer.putalpha(ground_mask)
    return Image.alpha_composite(Image.alpha_composite(composite, ground_layer), roof_layer).convert("RGB")


def footprint_overlay(
    image: Image.Image,
    *,
    polygons: list[list[tuple[float, float]]],
    fill: tuple[int, int, int, int] = (38, 126, 198, 50),
    outline: tuple[int, int, int, int] = (38, 126, 198, 255),
) -> Image.Image:
    base = image.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for polygon in polygons:
        if len(polygon) < 3:
            continue
        draw.polygon([(float(x), float(y)) for x, y in polygon], fill=fill)
        draw.line(
            [(float(x), float(y)) for x, y in [*polygon, polygon[0]]],
            fill=outline,
            width=4,
            joint="curve",
        )
    return Image.alpha_composite(base, overlay).convert("RGB")


def footprint_constraint_overlay(
    image: Image.Image,
    *,
    polygons: list[list[tuple[float, float]]],
    constraint_mask: np.ndarray,
) -> Image.Image:
    """Show the raw footprint in blue and the actual buffered search region in orange."""
    base = image.convert("RGBA")
    mask = np.asarray(constraint_mask, dtype=bool)
    if mask.shape != (base.height, base.width):
        mask = np.asarray(Image.fromarray(mask.astype("uint8") * 255).resize(base.size), dtype=bool)
    buffered = Image.new("RGBA", base.size, (244, 130, 32, 0))
    buffered.putalpha(Image.fromarray(mask.astype("uint8") * 70, mode="L"))
    composite = Image.alpha_composite(base, buffered).convert("RGB")
    return footprint_overlay(composite, polygons=polygons)


def outline_prior_overlay(
    image: Image.Image,
    *,
    polygons: list[list[tuple[float, float]]],
    constraint_mask: np.ndarray | None = None,
) -> Image.Image:
    """Render the AI roof outline prior in yellow and its buffered region softly."""
    base = image.convert("RGBA")
    if constraint_mask is not None:
        mask = np.asarray(constraint_mask, dtype=bool)
        if mask.shape != (base.height, base.width):
            mask = np.asarray(Image.fromarray(mask.astype("uint8") * 255).resize(base.size), dtype=bool)
        buffered = Image.new("RGBA", base.size, (255, 193, 7, 0))
        buffered.putalpha(Image.fromarray(mask.astype("uint8") * 45, mode="L"))
        base = Image.alpha_composite(base, buffered)
    return footprint_overlay(
        base.convert("RGB"),
        polygons=polygons,
        fill=(255, 193, 7, 32),
        outline=(255, 193, 7, 255),
    )


def _draw_point(
    draw: ImageDraw.ImageDraw,
    point: tuple[float, float],
    *,
    fill: tuple[int, int, int, int],
    outline: tuple[int, int, int, int],
    label: str,
) -> None:
    x, y = float(point[0]), float(point[1])
    radius = 9
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill, outline=outline, width=3)
    draw.text((x + radius + 4, y - radius), label, fill=outline)


def _draw_vertex(
    draw: ImageDraw.ImageDraw,
    point: tuple[float, float],
    label: str,
    *,
    fill: tuple[int, int, int, int],
    show_label: bool,
) -> None:
    x, y = float(point[0]), float(point[1])
    radius = 6
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill, outline=(0, 0, 0, 255), width=2)
    if show_label:
        draw.text((x + radius + 3, y - radius - 4), label, fill=(255, 255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0, 255))


def _open_ring(ring: list[tuple[float, float]]) -> list[tuple[float, float]]:
    return ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring


def image_png_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
