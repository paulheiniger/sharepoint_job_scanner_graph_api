from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from .models import Point


@dataclass
class SegmentationPrompts:
    positive_points: list[Point] = field(default_factory=list)
    negative_points: list[Point] = field(default_factory=list)


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
    """SAM 2 integration placeholder.

    SAM 2 should be installed and hosted separately from the Streamlit app in
    production. This class keeps the provider boundary explicit without making
    SAM 2 a hard dependency for local/cloud MVP runs.
    """

    def segment(self, image: np.ndarray, prompts: SegmentationPrompts | None = None) -> SegmentationResult:
        raise RuntimeError("SAM 2 roof segmentation is not configured in this runtime.")


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
    normalized = (name or "").strip().lower()
    if normalized in {"sam2", "sam_2", "sam 2"}:
        return Sam2RoofSegmenter()
    return ManualFallbackSegmenter()

