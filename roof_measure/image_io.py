from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps

from .models import ImageMetadata


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}


@dataclass(frozen=True)
class LoadedImage:
    metadata: ImageMetadata
    image: Image.Image
    inference_image: Image.Image


def safe_file_name(value: str) -> str:
    name = Path(value or "roof-image.jpg").name
    cleaned = re.sub(r"[^A-Za-z0-9_. -]+", "-", name).strip()
    return cleaned[:120] or "roof-image.jpg"


def uploaded_file_bytes(uploaded: Any) -> bytes:
    if isinstance(uploaded, (bytes, bytearray)):
        return bytes(uploaded)
    if hasattr(uploaded, "getvalue"):
        return bytes(uploaded.getvalue())
    if hasattr(uploaded, "read"):
        position = None
        try:
            position = uploaded.tell()
        except Exception:
            position = None
        data = uploaded.read()
        if position is not None:
            try:
                uploaded.seek(position)
            except Exception:
                pass
        return bytes(data or b"")
    return Path(str(uploaded)).read_bytes()


def image_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_image_bytes(
    data: bytes,
    *,
    file_name: str = "roof-image.jpg",
    storage_root: str | Path = "output/roof_measure_uploads",
    inference_max_side: int = 1200,
    seen_hashes: set[str] | None = None,
) -> LoadedImage:
    if not data:
        raise ValueError("No image bytes were supplied.")
    suffix = Path(file_name).suffix.lower()
    if suffix and suffix not in IMAGE_EXTENSIONS:
        raise ValueError(f"Unsupported roof image type: {suffix}")

    digest = image_hash(data)
    duplicate = digest in (seen_hashes or set())
    if seen_hashes is not None:
        seen_hashes.add(digest)
    image_id = digest[:16]
    safe_name = safe_file_name(file_name)
    base_dir = Path(storage_root)
    original_dir = base_dir / "originals"
    original_dir.mkdir(parents=True, exist_ok=True)
    stored_path = original_dir / f"{image_id}-{safe_name}"
    if not stored_path.exists():
        stored_path.write_bytes(data)

    raw_image = Image.open(BytesIO(data))
    normalized = ImageOps.exif_transpose(raw_image).convert("RGB")
    exif_orientation_applied = normalized.size != raw_image.size or raw_image.getexif().get(274) not in (None, 1)

    width, height = normalized.size
    scale = min(1.0, inference_max_side / max(width, height))
    if scale < 1.0:
        inference_size = (max(1, round(width * scale)), max(1, round(height * scale)))
        inference_image = normalized.resize(inference_size, Image.Resampling.LANCZOS)
    else:
        inference_image = normalized.copy()
    inference_width, inference_height = inference_image.size
    metadata = ImageMetadata(
        image_id=image_id,
        file_name=file_name,
        stored_path=str(stored_path),
        width=width,
        height=height,
        inference_width=inference_width,
        inference_height=inference_height,
        scale_x=inference_width / width if width else 1.0,
        scale_y=inference_height / height if height else 1.0,
        content_hash=digest,
        duplicate=duplicate,
        exif_orientation_applied=exif_orientation_applied,
        quality_flags=image_quality_flags(normalized),
    )
    return LoadedImage(metadata=metadata, image=normalized, inference_image=inference_image)


def image_to_array(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("RGB"))


def image_quality_flags(image: Image.Image) -> list[str]:
    width, height = image.size
    flags: list[str] = []
    if width < 400 or height < 400:
        flags.append("low_resolution")
    ratio = max(width, height) / max(min(width, height), 1)
    if ratio > 4:
        flags.append("extreme_aspect_ratio")
    arr = image_to_array(image)
    brightness = float(arr.mean())
    if brightness < 35:
        flags.append("very_dark")
    elif brightness > 235:
        flags.append("very_bright")
    return flags


def strip_exif_png_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    return buffer.getvalue()
