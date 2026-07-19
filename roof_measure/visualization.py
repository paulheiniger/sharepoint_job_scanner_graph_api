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


def image_png_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
