from __future__ import annotations

import base64
from functools import lru_cache
from io import BytesIO
import os
from pathlib import Path
import sys
import threading
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException
from PIL import Image
from pydantic import BaseModel, Field


app = FastAPI(title="Spray-Tec Roof SAM2 Segmentation", version="0.1.0")
_PREDICT_LOCK = threading.Lock()


class SegmentRequest(BaseModel):
    image_png_base64: str
    positive_points: list[tuple[float, float]] = Field(default_factory=list)
    negative_points: list[tuple[float, float]] = Field(default_factory=list)
    box: tuple[float, float, float, float] | None = None
    max_candidates: int = 3
    multimask_output: bool = True


class MaskCandidateResponse(BaseModel):
    label: str
    score: float
    mask_png_base64: str
    reasons: list[str] = Field(default_factory=list)


class SegmentResponse(BaseModel):
    candidates: list[MaskCandidateResponse]
    model_name: str = "sam2_remote"
    model_version: str = "sam2.1"
    warnings: list[str] = Field(default_factory=list)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "model_config": _model_config(),
        "checkpoint": _checkpoint_path(),
        "device": _requested_device(),
    }


@app.post("/segment", response_model=SegmentResponse)
def segment(request: SegmentRequest) -> SegmentResponse:
    try:
        image = _decode_image(request.image_png_base64)
        predictor, torch, device = _predictor()
        point_coords, point_labels = _prompt_arrays(request, image.shape)
        box = _box_array(request.box, image.shape)
        with _PREDICT_LOCK:
            with torch.inference_mode():
                predictor.set_image(image)
                prediction_kwargs = {
                    "point_coords": point_coords,
                    "point_labels": point_labels,
                    "multimask_output": request.multimask_output,
                }
                if box is not None:
                    prediction_kwargs["box"] = box
                masks, scores, _ = predictor.predict(**prediction_kwargs)
    except Exception as exc:  # pragma: no cover - real SAM2 failures are environment specific.
        raise HTTPException(status_code=500, detail=f"SAM2 segmentation failed: {type(exc).__name__}: {exc}") from exc

    masks = np.asarray(masks)
    scores = np.asarray(scores, dtype=float).reshape(-1)
    if masks.ndim == 2:
        masks = masks[None, :, :]
    order = np.argsort(-scores) if scores.size else np.arange(masks.shape[0])
    candidates: list[MaskCandidateResponse] = []
    for rank, index in enumerate(order[: max(1, request.max_candidates)], start=1):
        mask = masks[int(index)] > 0
        score = float(scores[int(index)]) if int(index) < scores.size else 0.0
        candidates.append(
            MaskCandidateResponse(
                label=f"SAM2 roof candidate {rank}",
                score=score,
                mask_png_base64=_encode_mask(mask),
                reasons=["SAM2 prompt segmentation from uploaded roof image"],
            )
        )
    return SegmentResponse(candidates=candidates, model_version=_model_version(), warnings=_runtime_warnings(device))


@lru_cache(maxsize=1)
def _predictor() -> tuple[Any, Any, str]:
    _configure_sam2_import_path()
    import torch
    from sam2.build_sam import build_sam2
    from sam2.sam2_image_predictor import SAM2ImagePredictor

    device = _resolve_device(torch)
    model = build_sam2(_model_config(), _checkpoint_path(), device=device)
    return SAM2ImagePredictor(model), torch, device


def _configure_sam2_import_path() -> None:
    configured = os.getenv("SAM2_REPO_PATH")
    if configured:
        sam2_repo = Path(configured).expanduser().resolve()
    else:
        sam2_repo = Path(__file__).resolve().parents[2] / "sam2"
    sam2_parent = sam2_repo.parent.resolve()
    cwd = Path.cwd().resolve()
    shadowing_entries = {"", str(sam2_parent)}
    if cwd == sam2_parent:
        shadowing_entries.add(str(cwd))
    sys.path[:] = [entry for entry in sys.path if not _is_shadowing_sam2_checkout(entry, sam2_parent, shadowing_entries)]
    if sam2_repo.exists():
        sys.path.insert(0, str(sam2_repo))


def _is_shadowing_sam2_checkout(entry: str, sam2_parent: Path, shadowing_entries: set[str]) -> bool:
    if entry in shadowing_entries:
        return True
    if not entry:
        return True
    try:
        return Path(entry).resolve() == sam2_parent
    except OSError:
        return False


def _model_config() -> str:
    return os.getenv("SAM2_MODEL_CONFIG") or "configs/sam2.1/sam2.1_hiera_t.yaml"


def _checkpoint_path() -> str:
    configured = os.getenv("SAM2_CHECKPOINT")
    if configured:
        return configured
    return str(Path(__file__).resolve().parents[2] / "sam2" / "checkpoints" / "sam2.1_hiera_tiny.pt")


def _model_version() -> str:
    checkpoint = Path(_checkpoint_path()).name
    return checkpoint.removesuffix(".pt") or "sam2.1"


def _requested_device() -> str:
    return (os.getenv("SAM2_DEVICE") or "auto").strip().lower()


def _resolve_device(torch: Any) -> str:
    requested = _requested_device()
    if requested != "auto":
        return requested
    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _decode_image(image_png_base64: str) -> np.ndarray:
    if not image_png_base64:
        raise ValueError("image_png_base64 is required.")
    raw = base64.b64decode(image_png_base64)
    return np.array(Image.open(BytesIO(raw)).convert("RGB"), copy=True)


def _prompt_arrays(request: SegmentRequest, image_shape: tuple[int, ...]) -> tuple[np.ndarray, np.ndarray]:
    points = list(request.positive_points) + list(request.negative_points)
    labels = [1] * len(request.positive_points) + [0] * len(request.negative_points)
    if not points:
        height, width = image_shape[:2]
        points = [(width / 2, height / 2)]
        labels = [1]
    return np.asarray(points, dtype=np.float32), np.asarray(labels, dtype=np.int32)


def _box_array(box: tuple[float, float, float, float] | None, image_shape: tuple[int, ...]) -> np.ndarray | None:
    if box is None:
        return None
    height, width = image_shape[:2]
    x0, y0, x1, y1 = (float(value) for value in box)
    x0, x1 = sorted((max(0.0, min(x0, width - 1)), max(0.0, min(x1, width - 1))))
    y0, y1 = sorted((max(0.0, min(y0, height - 1)), max(0.0, min(y1, height - 1))))
    if x1 - x0 < 2 or y1 - y0 < 2:
        raise ValueError("SAM2 box prompt must cover at least 2 image pixels in each direction.")
    return np.asarray([x0, y0, x1, y1], dtype=np.float32)


def _encode_mask(mask: np.ndarray) -> str:
    image = Image.fromarray((np.asarray(mask, dtype=bool).astype(np.uint8) * 255), mode="L")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _runtime_warnings(device: str) -> list[str]:
    warnings: list[str] = []
    if device == "cpu":
        warnings.append("SAM2 is running on CPU; segmentation may be slow.")
    if device == "mps":
        warnings.append("SAM2 is running on Apple MPS; verify mask quality before relying on measurements.")
    return warnings


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("services.roof_sam2.server:app", host="127.0.0.1", port=8765, reload=False)
