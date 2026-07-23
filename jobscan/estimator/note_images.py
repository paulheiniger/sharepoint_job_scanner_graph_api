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
NOTE_IMAGE_CACHE_VERSION = "note-image-v2-annotated-takeoff"
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
        "Read the selected Spray-Tec estimating image(s). They may be handwritten notes, typed notes, sketches, "
        "or annotated aerial/site maps where arrows, colored boundary lines, labels, and callout placement define scope. "
        "Return strict JSON with keys: document_type, transcribed_text, normalized_estimator_notes, measurements, "
        "customer_info, job_header, area_scopes, linear_scopes, retain_existing, scope_relationships, "
        "area_reconciliation, estimator_decision_cues, questions, unreadable_regions, warnings, confidence. "
        "The normalized_estimator_notes must be ready to paste into the Estimating Assistant chat.\n\n"
        "For an annotated aerial takeoff:\n"
        "- Read every visible label and associate each arrow or colored line with the roof edge/region it points to.\n"
        "- area_scopes entries use: scope_id, parent_scope_id, scope_role (exclusive_area, nested_sub_scope, or deduction), "
        "label, area_sqft, action, existing_system, proposed_assembly, "
        "decking_replacement_sqft, thicknesses, location, evidence_text, confidence.\n"
        "- linear_scopes entries use: item, action, linear_ft, size, location, treatment, retain_existing, "
        "evidence_text, confidence.\n"
        "- Preserve separate roof-area scopes. Distinguish exclusive area sections from nested sub-scopes such as "
        "a deteriorated-decking allowance inside a larger tear-off area.\n"
        "- Never add a nested sub-scope to the total roof area. Reconcile exclusive area sections against any declared total "
        "in area_reconciliation and report discrepancies instead of silently changing a number.\n"
        "- Preserve exact action language such as full removal, remove/replace decking, install over existing, remain, "
        "seal seams, new edge metal, counter flashing, gutter, downspouts, nailer, ISO board, foam, and coating.\n"
        "- Treat explicitly printed quantities as source evidence. Do not infer length or area from image scale.\n"
        "- Do not confuse label colors with materials; color is only a callout/region association unless the annotation says otherwise.\n\n"
        "For all images, preserve dimensions, quantities, R-values, roof/spray foam systems, materials, dates, locations, "
        "access notes, warranty requests, exclusions, and items explicitly marked to remain. Do not invent missing values; "
        "put uncertain items in questions or unreadable_regions. Do not calculate prices.\n\n"
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
    job_header = _clean_dict(payload.get("job_header"))
    customer_info = _clean_dict(payload.get("customer_info"))
    area_scopes = _clean_dict_list(payload.get("area_scopes"))
    linear_scopes = _clean_dict_list(payload.get("linear_scopes"))
    retain_existing = _clean_list(payload.get("retain_existing"))
    scope_relationships = _clean_list(payload.get("scope_relationships"))
    area_reconciliation = _clean_dict(payload.get("area_reconciliation"))
    structured_notes = annotated_scope_notes(
        job_header=job_header,
        customer_info=customer_info,
        area_scopes=area_scopes,
        linear_scopes=linear_scopes,
        retain_existing=retain_existing,
        scope_relationships=scope_relationships,
        area_reconciliation=area_reconciliation,
    )
    normalized_notes = str(payload.get("normalized_estimator_notes") or "").strip()
    transcribed = str(payload.get("transcribed_text") or "").strip()
    if not normalized_notes:
        normalized_notes = transcribed
    if structured_notes and structured_notes not in normalized_notes:
        normalized_notes = "\n\n".join(part for part in (normalized_notes, structured_notes) if part)
    warnings = _clean_list(payload.get("warnings"))
    conversion_warnings = [
        f"{record.get('file_name')}: {record.get('conversion_error')}"
        for record in records
        if record.get("conversion_error")
    ]
    return {
        "transcribed_text": transcribed,
        "normalized_estimator_notes": normalized_notes,
        "document_type": str(payload.get("document_type") or "").strip(),
        "measurements": _clean_list(payload.get("measurements")),
        "customer_info": customer_info,
        "job_header": job_header,
        "area_scopes": area_scopes,
        "linear_scopes": linear_scopes,
        "retain_existing": retain_existing,
        "scope_relationships": scope_relationships,
        "area_reconciliation": area_reconciliation,
        "estimator_decision_cues": _clean_list(payload.get("estimator_decision_cues")),
        "questions": _clean_list(payload.get("questions")),
        "unreadable_regions": _clean_list(payload.get("unreadable_regions")),
        "warnings": [*warnings, *conversion_warnings],
        "confidence": _safe_float(payload.get("confidence"), 0.0),
        "source_images": payload.get("source_images") or [record.get("image_id") for record in records],
        "cache_hit": bool(payload.get("cache_hit")),
    }


def annotated_scope_notes(
    *,
    job_header: dict[str, Any],
    customer_info: dict[str, Any],
    area_scopes: list[dict[str, Any]],
    linear_scopes: list[dict[str, Any]],
    retain_existing: list[str],
    scope_relationships: list[str],
    area_reconciliation: dict[str, Any],
) -> str:
    if not any((job_header, customer_info, area_scopes, linear_scopes, retain_existing, scope_relationships, area_reconciliation)):
        return ""
    lines = ["Structured scope extracted from annotated estimating image:"]
    header = {**customer_info, **job_header}
    header_parts = [
        f"{label}: {header.get(key)}"
        for key, label in (("job_name", "Job"), ("customer", "Customer"), ("site_address", "Address"))
        if header.get(key) not in (None, "", [])
    ]
    if header_parts:
        lines.append("- " + "; ".join(header_parts))
    if header.get("declared_total_area_sqft") not in (None, "", []):
        lines.append(f"- Declared total roof area: {header['declared_total_area_sqft']} sq ft")
    for index, scope in enumerate(area_scopes, start=1):
        parts = [
            str(scope.get("label") or scope.get("scope_id") or f"Area {index}"),
            f"scope role: {scope.get('scope_role')}" if scope.get("scope_role") else "",
            f"parent scope: {scope.get('parent_scope_id')}" if scope.get("parent_scope_id") else "",
            _quantity_text(scope.get("area_sqft"), "sq ft"),
            str(scope.get("action") or ""),
            str(scope.get("proposed_assembly") or ""),
            _quantity_text(scope.get("decking_replacement_sqft"), "sq ft decking replacement"),
            _dict_or_list_text(scope.get("thicknesses")),
            str(scope.get("location") or ""),
        ]
        lines.append("- Area scope: " + "; ".join(part for part in parts if part))
    for scope in linear_scopes:
        parts = [
            str(scope.get("action") or ""),
            str(scope.get("item") or ""),
            str(scope.get("size") or ""),
            _quantity_text(scope.get("linear_ft"), "linear ft"),
            str(scope.get("location") or ""),
            str(scope.get("treatment") or ""),
            "retain existing" if scope.get("retain_existing") is True else "",
        ]
        if any(parts):
            lines.append("- Linear/detail scope: " + "; ".join(part for part in parts if part))
    for item in retain_existing:
        lines.append(f"- Existing item to remain: {item}")
    for item in scope_relationships:
        lines.append(f"- Scope relationship: {item}")
    if area_reconciliation:
        reconciliation = "; ".join(
            f"{str(key).replace('_', ' ')}: {_dict_or_list_text(value)}"
            for key, value in area_reconciliation.items()
            if value not in (None, "", [])
        )
        if reconciliation:
            lines.append("- Area reconciliation: " + reconciliation)
    return "\n".join(lines)


def _clean_dict(value: Any) -> dict[str, Any]:
    return {str(key): item for key, item in value.items() if item not in (None, "", [])} if isinstance(value, dict) else {}


def _clean_dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in value:
        cleaned = _clean_dict(item)
        if cleaned:
            rows.append(cleaned)
    return rows


def _quantity_text(value: Any, unit: str) -> str:
    if value in (None, ""):
        return ""
    return f"{value} {unit}"


def _dict_or_list_text(value: Any) -> str:
    if value in (None, "", []):
        return ""
    if isinstance(value, dict):
        return ", ".join(f"{key}: {item}" for key, item in value.items())
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value)
    return str(value)


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
