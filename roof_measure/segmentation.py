from __future__ import annotations

import base64
from dataclasses import dataclass, field
from io import BytesIO
import os
from typing import Protocol

import numpy as np
from PIL import Image
import requests

from .models import Point


@dataclass
class SegmentationPrompts:
    positive_points: list[Point] = field(default_factory=list)
    negative_points: list[Point] = field(default_factory=list)
    box: tuple[float, float, float, float] | None = None
    mask_input: np.ndarray | None = None


@dataclass
class MaskCandidate:
    mask: np.ndarray
    score: float
    label: str
    reasons: list[str] = field(default_factory=list)


@dataclass
class SegmentationResult:
    candidates: list[MaskCandidate]
    model_name: str
    model_version: str
    warnings: list[str] = field(default_factory=list)


class RoofSegmenter(Protocol):
    def segment(self, image: np.ndarray, prompts: SegmentationPrompts | None = None) -> SegmentationResult:
        ...


class Sam2RoofSegmenter:
    """Remote SAM 2 integration.

    SAM 2 should be installed and hosted separately from the Streamlit app. This
    client keeps torch/SAM2 out of the Streamlit dependency set and lets local
    laptops or a Mac Studio provide segmentation over HTTP.
    """

    def __init__(self, url: str | None = None, timeout_seconds: float | None = None):
        self.url = (url or os.getenv("SAM2_SEGMENTATION_URL") or "").strip()
        self.timeout_seconds = timeout_seconds or float(os.getenv("ROOF_MEASURE_SEGMENTATION_TIMEOUT_SECONDS", "90"))

    def segment(self, image: np.ndarray, prompts: SegmentationPrompts | None = None) -> SegmentationResult:
        if not self.url:
            raise RuntimeError("SAM2_SEGMENTATION_URL is not configured.")
        prompts = prompts or SegmentationPrompts()
        payload = {
            "image_png_base64": _array_to_png_base64(image),
            "positive_points": prompts.positive_points,
            "negative_points": prompts.negative_points,
            "box": prompts.box,
            "mask_input_png_base64": (
                _array_to_png_base64(np.asarray(prompts.mask_input, dtype=bool).astype(np.uint8) * 255)
                if prompts.mask_input is not None
                else None
            ),
            "max_candidates": 3,
            "multimask_output": True,
        }
        response = requests.post(self.url, json=payload, timeout=self.timeout_seconds)
        response.raise_for_status()
        data = response.json()
        candidates: list[MaskCandidate] = []
        for index, candidate in enumerate(data.get("candidates") or []):
            mask = _mask_png_base64_to_array(str(candidate.get("mask_png_base64") or ""))
            if mask.shape[:2] != image.shape[:2]:
                raise RuntimeError(
                    f"SAM 2 mask shape {mask.shape[:2]} does not match image shape {image.shape[:2]}."
                )
            candidates.append(
                MaskCandidate(
                    mask=mask,
                    score=float(candidate.get("score") or 0.0),
                    label=str(candidate.get("label") or f"sam2 candidate {index + 1}"),
                    reasons=[str(reason) for reason in candidate.get("reasons") or []],
                )
            )
        if not candidates:
            raise RuntimeError("SAM 2 service returned no mask candidates.")
        return SegmentationResult(
            candidates=candidates,
            model_name=str(data.get("model_name") or "sam2_remote"),
            model_version=str(data.get("model_version") or "unknown"),
            warnings=[str(warning) for warning in data.get("warnings") or []],
        )


class ManualFallbackSegmenter:
    def segment(self, image: np.ndarray, prompts: SegmentationPrompts | None = None) -> SegmentationResult:
        height, width = image.shape[:2]
        prompts = prompts or SegmentationPrompts()
        point = prompts.positive_points[0] if prompts.positive_points else (width / 2, height / 2)
        candidates: list[MaskCandidate] = []
        for index, fraction in enumerate((0.22, 0.32, 0.44), start=1):
            mask = np.zeros((height, width), dtype=bool)
            half_w = max(8, int(width * fraction / 2))
            half_h = max(8, int(height * fraction / 2))
            cx = int(min(max(point[0], 0), width - 1))
            cy = int(min(max(point[1], 0), height - 1))
            x0 = max(0, cx - half_w)
            x1 = min(width, cx + half_w)
            y0 = max(0, cy - half_h)
            y1 = min(height, cy + half_h)
            mask[y0:y1, x0:x1] = True
            candidates.append(
                MaskCandidate(
                    mask=mask,
                    score=0.35 - index * 0.03,
                    label=f"fallback box {index}",
                    reasons=["manual fallback rectangle around prompt point"],
                )
            )
        return SegmentationResult(
            candidates=candidates,
            model_name="manual_fallback_segmenter",
            model_version="roof-measure-mvp1",
            warnings=["SAM 2 is not configured; fallback masks require estimator review."],
        )


class MockRoofSegmenter:
    def __init__(self, masks: list[np.ndarray] | None = None):
        self.masks = masks or []

    def segment(self, image: np.ndarray, prompts: SegmentationPrompts | None = None) -> SegmentationResult:
        if not self.masks:
            height, width = image.shape[:2]
            mask = np.zeros((height, width), dtype=bool)
            mask[height // 4 : height * 3 // 4, width // 4 : width * 3 // 4] = True
            masks = [mask]
        else:
            masks = self.masks
        candidates = [
            MaskCandidate(mask=np.asarray(mask, dtype=bool), score=0.9 - index * 0.1, label=f"mock {index + 1}")
            for index, mask in enumerate(masks)
        ]
        return SegmentationResult(candidates=candidates, model_name="mock_segmenter", model_version="test")


def choose_segmenter(name: str | None) -> RoofSegmenter:
    normalized = (name or os.getenv("ROOF_MEASURE_SEGMENTER") or "").strip().lower()
    if normalized in {"sam2", "sam_2", "sam 2", "sam2_remote", "remote_sam2"}:
        return Sam2RoofSegmenter()
    return ManualFallbackSegmenter()


def _array_to_png_base64(image: np.ndarray) -> str:
    array = np.asarray(image)
    if array.dtype != np.uint8:
        array = np.clip(array, 0, 255).astype(np.uint8)
    if array.ndim == 2:
        pil_image = Image.fromarray(array, mode="L")
    else:
        pil_image = Image.fromarray(array[:, :, :3], mode="RGB")
    buffer = BytesIO()
    pil_image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _mask_png_base64_to_array(mask_png_base64: str) -> np.ndarray:
    if not mask_png_base64:
        raise RuntimeError("SAM 2 service returned a candidate without mask_png_base64.")
    raw = base64.b64decode(mask_png_base64)
    image = Image.open(BytesIO(raw)).convert("L")
    return np.asarray(image) > 0
