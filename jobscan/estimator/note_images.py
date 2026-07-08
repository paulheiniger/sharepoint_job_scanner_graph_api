from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
from pathlib import Path
from typing import Any, Callable, Iterable

try:
    from PIL import Image
except Exception:  # pragma: no cover - optional runtime dependency
    Image = None  # type: ignore

try:
    import pillow_heif
except Exception:  # pragma: no cover - optional HEIC dependency
    pillow_heif = None  # type: ignore


NOTE_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif"}
NOTE_IMAGE_CACHE_VERSION = "note-image-v1"
DEFAULT_NOTE_IMAGE_MODEL = "gpt-4o"
DEFAULT_MAX_NOTE_IMAGES = 3


def stage_note_images(
    uploaded_files: Iterable[Any],
    *,
    upload_key: str,
    storage_root: str | Path = "output/estimator_note_uploads",
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    base_dir = Path(storage_root) / sanitize_upload_key(upload_key)
    original_dir = base_dir / "originals"
    converted_dir = base_dir / "converted"
    original_dir.mkdir(parents=True, exist_ok=True)
    converted_dir.mkdir(parents=True, exist_ok=True)
    seen_hashes: set[str] = set()

    for uploaded in uploaded_files or []:
        file_name = _uploaded_name(uploaded)
        suffix = Path(file_name).suffix.lower()
        if suffix and suffix not in NOTE_IMAGE_EXTENSIONS:
            continue
        data = _uploaded_bytes(uploaded)
        if not data:
            continue
        image_hash = hashlib.sha256(data).hexdigest()
        duplicate = image_hash in seen_hashes
        seen_hashes.add(image_hash)
        safe_name = sanitize_file_name(file_name or f"note-{image_hash[:10]}.jpg")
        original_path = original_dir / f"{image_hash[:16]}-{safe_name}"
        if not original_path.exists():
            original_path.write_bytes(data)

        converted_path = converted_dir / f"{image_hash[:16]}.jpg"
        conversion_error = ""
        if not converted_path.exists():
            conversion_error = convert_note_image_to_jpeg(original_path, converted_path)

        records.append(
            {
                "image_id": image_hash[:16],
                "content_hash": image_hash,
                "file_name": file_name,
                "stored_path": str(original_path),
                "converted_path": str(converted_path) if converted_path.exists() else "",
                "mime_type": mimetypes.guess_type(file_name)[0] or "",
                "duplicate": duplicate,
                "conversion_error": conversion_error,
            }
        )
    return records


def extract_notes_from_images_with_ai(
    records: list[dict[str, Any]],
    *,
    max_images: int = DEFAULT_MAX_NOTE_IMAGES,
    model: str | None = None,
    cache_dir: str | Path = "output/estimator_note_uploads/ai_cache",
    provider: Callable[[list[dict[str, Any]], str], Any] | None = None,
) -> dict[str, Any]:
    usable = [
        record
        for record in records
        if not record.get("duplicate") and (record.get("converted_path") or record.get("stored_path"))
    ][: max(1, max_images)]
    if not usable:
        return {
            "transcribed_text": "",
            "normalized_estimator_notes": "",
            "warnings": ["No readable note images were uploaded."],
            "confidence": 0.0,
            "source_images": [],
        }
    model_name = model or os.getenv("OPENAI_ESTIMATOR_NOTE_IMAGE_MODEL") or DEFAULT_NOTE_IMAGE_MODEL
    cache_path = note_image_cache_path(usable, model=model_name, cache_dir=cache_dir)
    if cache_path.exists():
        payload = json.loads(cache_path.read_text())
        payload["cache_hit"] = True
        return normalize_note_image_payload(payload, records=usable)

    messages = note_image_messages(usable)
    raw = provider(messages, model_name) if provider is not None else call_openai_note_image_extraction(messages, model_name)
    payload = extract_json_object(raw)
    payload["cache_hit"] = False
    payload["source_images"] = [record.get("image_id") for record in usable]
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return normalize_note_image_payload(payload, records=usable)


def note_image_messages(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    local_context = [
        {
            "image_id": record.get("image_id"),
            "file_name": record.get("file_name"),
            "conversion_error": record.get("conversion_error") or "",
        }
        for record in records
    ]
    prompt = (
        "Read the selected handwritten or typed Spray-Tec field-note image(s). "
        "Return strict JSON with keys: transcribed_text, normalized_estimator_notes, measurements, "
        "customer_info, estimator_decision_cues, questions, unreadable_regions, confidence. "
        "The normalized_estimator_notes should be ready to paste into the Estimating Assistant chat. "
        "Preserve dimensions, quantities, R-values, roof/spray foam systems, materials, dates, locations, "
        "access notes, warranty requests, and exclusions. Do not invent missing values; put uncertain items "
        "in questions or unreadable_regions. Do not calculate prices.\n\n"
        f"Local image metadata:\n{json.dumps(local_context, indent=2)}"
    )
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    detail = os.getenv("OPENAI_ESTIMATOR_NOTE_IMAGE_DETAIL", "high").strip().lower() or "high"
    if detail not in {"low", "high", "auto"}:
        detail = "high"
    for record in records:
        data_url = note_image_data_url(record)
        if data_url:
            content.append({"type": "image_url", "image_url": {"url": data_url, "detail": detail}})
    return [
        {
            "role": "system",
            "content": "You transcribe construction estimating notes and return only strict JSON.",
        },
        {"role": "user", "content": content},
    ]


def normalize_note_image_payload(payload: dict[str, Any], *, records: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_notes = str(payload.get("normalized_estimator_notes") or "").strip()
    transcribed = str(payload.get("transcribed_text") or "").strip()
    if not normalized_notes:
        normalized_notes = transcribed
    warnings = _clean_list(payload.get("warnings"))
    conversion_warnings = [
        f"{record.get('file_name')}: {record.get('conversion_error')}"
        for record in records
        if record.get("conversion_error")
    ]
    return {
        "transcribed_text": transcribed,
        "normalized_estimator_notes": normalized_notes,
        "measurements": _clean_list(payload.get("measurements")),
        "customer_info": payload.get("customer_info") if isinstance(payload.get("customer_info"), dict) else {},
        "estimator_decision_cues": _clean_list(payload.get("estimator_decision_cues")),
        "questions": _clean_list(payload.get("questions")),
        "unreadable_regions": _clean_list(payload.get("unreadable_regions")),
        "warnings": [*warnings, *conversion_warnings],
        "confidence": _safe_float(payload.get("confidence"), 0.0),
        "source_images": payload.get("source_images") or [record.get("image_id") for record in records],
        "cache_hit": bool(payload.get("cache_hit")),
    }


def convert_note_image_to_jpeg(source: Path, target: Path) -> str:
    if Image is None:
        return "Pillow is not installed; image could not be converted for note extraction."
    suffix = source.suffix.lower()
    if suffix in {".heic", ".heif"} and pillow_heif is None:
        return "HEIC/HEIF conversion requires pillow-heif in the Streamlit runtime."
    try:
        if pillow_heif is not None:
            pillow_heif.register_heif_opener()
        with Image.open(source) as image:
            image = image.convert("RGB")
            image.thumbnail((2200, 2200))
            target.parent.mkdir(parents=True, exist_ok=True)
            image.save(target, "JPEG", quality=90, optimize=True)
        return ""
    except Exception as exc:
        return f"Could not convert image for note extraction: {type(exc).__name__}: {exc}"


def call_openai_note_image_extraction(messages: list[dict[str, Any]], model: str) -> str:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set")
    try:
        from openai import OpenAI  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("openai package is not installed") from exc
    try:
        timeout_seconds = float(os.getenv("OPENAI_ESTIMATOR_NOTE_IMAGE_TIMEOUT_SECONDS", "60"))
    except (TypeError, ValueError):
        timeout_seconds = 60.0
    client = OpenAI(timeout=timeout_seconds)
    response = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=messages,
    )
    return response.choices[0].message.content or "{}"


def note_image_cache_path(records: list[dict[str, Any]], *, model: str, cache_dir: str | Path) -> Path:
    key_payload = {
        "version": NOTE_IMAGE_CACHE_VERSION,
        "model": model,
        "image_hashes": [record.get("content_hash") for record in records],
    }
    key = hashlib.sha256(json.dumps(key_payload, sort_keys=True).encode("utf-8")).hexdigest()[:24]
    return Path(cache_dir) / f"{key}.json"


def note_image_data_url(record: dict[str, Any]) -> str:
    path = Path(str(record.get("converted_path") or record.get("stored_path") or ""))
    if not path.exists():
        return ""
    mime = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else mimetypes.guess_type(path.name)[0] or "image/jpeg"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def extract_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    text = str(value or "{}").strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return {}
        payload = json.loads(match.group(0))
    return payload if isinstance(payload, dict) else {}


def sanitize_upload_key(value: Any) -> str:
    text = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value or "current")).strip("-")
    return text[:80] or "current"


def sanitize_file_name(value: str) -> str:
    name = Path(value or "image.jpg").name
    cleaned = re.sub(r"[^a-zA-Z0-9_. -]+", "-", name).strip()
    return cleaned[:120] or "image.jpg"


def _uploaded_name(uploaded: Any) -> str:
    return str(getattr(uploaded, "name", "") or getattr(uploaded, "filename", "") or "image")


def _uploaded_bytes(uploaded: Any) -> bytes:
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
        return bytes(data)
    path = Path(str(uploaded))
    return path.read_bytes() if path.exists() else b""


def _clean_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if not value:
        return []
    return [str(value).strip()] if str(value).strip() else []


def _safe_float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, number))
