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


def image_png_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()

