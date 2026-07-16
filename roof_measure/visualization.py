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
