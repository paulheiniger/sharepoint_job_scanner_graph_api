from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
import shutil
from pathlib import Path
from typing import Any, Iterable


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}

DEFAULT_MAX_REPRESENTATIVE_IMAGES = 8
DEFAULT_MAX_AI_IMAGES = 8
DEFAULT_PHOTO_AI_MODEL = "gpt-4o-mini"
PHOTO_AI_CACHE_VERSION = "photo-ai-v1"

PHOTO_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "wide_overview": ("overview", "wide", "overall", "drone", "aerial", "roof overview", "full roof"),
    "roof_field": ("field", "membrane", "main roof", "roof area", "roof field"),
    "seams": ("seam", "seams", "lap", "joint", "open seam"),
    "drains": ("drain", "scupper", "ponding", "water"),
    "curbs_penetrations": ("curb", "penetration", "pipe", "vent", "unit", "hvac", "flashing", "pitch pocket"),
    "edge_parapet": ("edge", "parapet", "coping", "gutter", "wall"),
    "access": ("access", "ladder", "lift", "parking", "rear", "obstruction", "height"),
    "fasteners_rust": ("rust", "fastener", "screw", "metal roof", "panel"),
    "insulation_detail": ("spray foam", "foam", "wall", "ceiling", "barn", "stud", "joist", "thermal"),
}

PHOTO_SIGNAL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "open_seams": ("open seam", "seam", "lap", "joint"),
    "ponding": ("ponding", "standing water", "water", "drain", "scupper"),
    "curbs": ("curb", "hvac", "unit"),
    "penetrations": ("penetration", "pipe", "vent", "pitch pocket"),
    "edge_detail": ("edge", "parapet", "coping", "gutter"),
    "rusted_fasteners": ("rust", "fastener", "screw"),
    "metal_roof": ("metal roof", "panel", "rib"),
    "membrane_roof": ("tpo", "epdm", "membrane"),
    "coating_wear": ("coating", "wear", "peel", "chalking", "weathered"),
    "blistering": ("blister", "bubble"),
    "access_constraints": ("access", "ladder", "lift", "obstruction", "height"),
    "spray_foam": ("spray foam", "foam", "insulation"),
    "thermal_barrier": ("thermal", "ignition", "dc315", "dc 315"),
    "masking_cleanup": ("masking", "mask", "cleanup", "plastic"),
}

REQUIRED_ROOFING_CATEGORIES = {
    "wide_overview": "wide overview of full roof",
    "drains": "close-up of drains/scuppers and ponding areas",
    "edge_parapet": "edge/parapet condition",
    "curbs_penetrations": "curbs, penetrations, and flashing details",
}

REQUIRED_INSULATION_CATEGORIES = {
    "wide_overview": "wide overview of building/interior",
    "insulation_detail": "wall/ceiling cavity details",
    "access": "access and setup constraints",
}


def stage_uploaded_images(
    uploaded_files: Iterable[Any],
    *,
    upload_key: str,
    storage_root: str | Path = "output/estimator_photo_uploads",
    thumbnail_size: tuple[int, int] = (240, 180),
) -> list[dict[str, Any]]:
    """Persist uploaded images and return cheap local metadata.

    This function intentionally does not call any AI service. It hashes the
    original bytes, stores each unique image once for the upload key, and writes
    a small thumbnail when Pillow is available.
    """

    records: list[dict[str, Any]] = []
    base_dir = Path(storage_root) / sanitize_upload_key(upload_key)
    original_dir = base_dir / "originals"
    thumb_dir = base_dir / "thumbnails"
    original_dir.mkdir(parents=True, exist_ok=True)
    thumb_dir.mkdir(parents=True, exist_ok=True)
    seen_hashes: set[str] = set()

    for uploaded in uploaded_files or []:
        file_name = _uploaded_name(uploaded)
        suffix = Path(file_name).suffix.lower()
        if suffix and suffix not in IMAGE_EXTENSIONS:
            continue
        data = _uploaded_bytes(uploaded)
        if not data:
            continue
        image_hash = hashlib.sha256(data).hexdigest()
        duplicate = image_hash in seen_hashes
        seen_hashes.add(image_hash)
        safe_name = sanitize_file_name(file_name or f"image-{image_hash[:10]}.jpg")
        original_path = original_dir / f"{image_hash[:16]}-{safe_name}"
        if not original_path.exists():
            original_path.write_bytes(data)
        metadata = _image_metadata(original_path, thumb_dir / f"{image_hash[:16]}.jpg", thumbnail_size)
        category, signals = classify_photo(file_name=file_name, metadata=metadata)
        quality_flags = list(metadata.get("quality_flags") or [])
        if duplicate:
            quality_flags.append("duplicate_upload")
        records.append(
            {
                "image_id": image_hash[:16],
                "content_hash": image_hash,
                "file_name": file_name,
                "stored_path": str(original_path),
                "thumbnail_path": metadata.get("thumbnail_path") or "",
                "width": metadata.get("width"),
                "height": metadata.get("height"),
                "brightness": metadata.get("brightness"),
                "category": category,
                "signals": signals,
                "quality_flags": quality_flags,
                "duplicate": duplicate,
                "selected": False,
            }
        )
    selected_hashes = select_representative_images(records)
    for record in records:
        record["selected"] = record["content_hash"] in selected_hashes
    return records


def sanitize_upload_key(value: Any) -> str:
    text = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value or "current")).strip("-")
    return text[:80] or "current"


def sanitize_file_name(value: str) -> str:
    name = Path(value or "image.jpg").name
    cleaned = re.sub(r"[^a-zA-Z0-9_. -]+", "-", name).strip()
    return cleaned[:120] or "image.jpg"


def classify_photo(*, file_name: str, metadata: dict[str, Any] | None = None) -> tuple[str, list[str]]:
    text = normalize_text(file_name)
    metadata = metadata or {}
    categories: list[tuple[int, str]] = []
    for category, terms in PHOTO_CATEGORY_KEYWORDS.items():
        score = sum(1 for term in terms if normalize_text(term) in text)
        if score:
            categories.append((score, category))
    if not categories:
        width = float(metadata.get("width") or 0)
        height = float(metadata.get("height") or 0)
        if width and height and max(width, height) / max(min(width, height), 1) > 1.8:
            categories.append((1, "wide_overview"))
    category = sorted(categories, reverse=True)[0][1] if categories else "unknown"
    signals: list[str] = []
    for signal, terms in PHOTO_SIGNAL_KEYWORDS.items():
        if any(normalize_text(term) in text for term in terms):
            signals.append(signal)
    return category, signals


def select_representative_images(
    records: list[dict[str, Any]],
    *,
    max_images: int = DEFAULT_MAX_REPRESENTATIVE_IMAGES,
) -> set[str]:
    eligible = []
    for record in records:
        quality_flags = record.get("quality_flags") or []
        if record.get("duplicate") or "duplicate_upload" in quality_flags:
            continue
        if "unreadable_image" in quality_flags and not (record.get("signals") or record.get("category") not in {"", None, "unknown"}):
            continue
        eligible.append(record)
    selected: list[dict[str, Any]] = []
    seen_categories: set[str] = set()
    for record in sorted(eligible, key=_record_selection_sort):
        category = str(record.get("category") or "unknown")
        if category in seen_categories and len(seen_categories) < max_images:
            continue
        selected.append(record)
        seen_categories.add(category)
        if len(selected) >= max_images:
            break
    if len(selected) < max_images:
        selected_hashes = {str(record.get("content_hash")) for record in selected}
        for record in sorted(eligible, key=_record_selection_sort):
            if str(record.get("content_hash")) in selected_hashes:
                continue
            selected.append(record)
            selected_hashes.add(str(record.get("content_hash")))
            if len(selected) >= max_images:
                break
    return {str(record.get("content_hash")) for record in selected if record.get("content_hash")}


def build_photo_scope_context(
    records: list[dict[str, Any]],
    *,
    selected_hashes: Iterable[str] | None = None,
    template_type: str = "",
) -> dict[str, Any]:
    selected_set = None if selected_hashes is None else {str(value) for value in selected_hashes if str(value)}
    selected_records = [
        record for record in records if selected_set is None or str(record.get("content_hash")) in selected_set
    ]
    selected_records = [record for record in selected_records if not record.get("duplicate")]
    template = normalize_text(template_type)
    inferred_template = "insulation" if any("insulation" in (record.get("signals") or []) or record.get("category") == "insulation_detail" for record in selected_records) else ""
    if template not in {"roofing", "insulation"}:
        template = inferred_template or "roofing"

    signals = _signal_set(selected_records)
    categories = {str(record.get("category") or "") for record in selected_records}
    missing_photos = _missing_photos(categories, template)
    visible_issues = _visible_issues(signals)
    scope_notes = _scope_notes(signals, template)
    risk_flags = _risk_flags(signals, missing_photos, template)
    confidence = _photo_confidence(selected_records, signals)
    proposals = photo_decision_proposals(signals, selected_records, template_type=template, confidence=confidence)
    scope_updates = photo_scope_updates(signals, template, selected_records, missing_photos)
    note_text = photo_note_text(
        visible_issues=visible_issues,
        scope_notes=scope_notes,
        risk_flags=risk_flags,
        missing_photos=missing_photos,
    )
    return {
        "template_type": template,
        "image_count": len(records),
        "selected_image_count": len(selected_records),
        "selected_image_ids": [record.get("image_id") for record in selected_records],
        "selected_hashes": [record.get("content_hash") for record in selected_records],
        "categories": sorted(category for category in categories if category),
        "signals": sorted(signals),
        "roof_condition": _roof_condition(signals),
        "visible_issues": visible_issues,
        "recommended_scope_notes": scope_notes,
        "risk_flags": risk_flags,
        "missing_photos": missing_photos,
        "confidence": confidence,
        "photo_decision_proposals": proposals,
        "scope_updates": scope_updates,
        "note_text": note_text,
    }


def analyze_selected_photos_with_ai(
    records: list[dict[str, Any]],
    *,
    selected_hashes: Iterable[str],
    template_type: str,
    notes: str = "",
    max_images: int = DEFAULT_MAX_AI_IMAGES,
    model: str | None = None,
    cache_dir: str | Path = "output/estimator_photo_uploads/vision_cache",
    force: bool = False,
    provider: Any = None,
) -> dict[str, Any]:
    """Run an explicit, bounded vision analysis for selected photos.

    This function is intentionally never called from upload/staging. The caller
    must invoke it from an explicit user action so uploading many images does
    not automatically create API spend.
    """

    selected_set = {str(value) for value in selected_hashes if str(value)}
    selected_records = [
        record
        for record in records
        if str(record.get("content_hash") or "") in selected_set and not record.get("duplicate")
    ][: max(0, int(max_images or DEFAULT_MAX_AI_IMAGES))]
    if not selected_records:
        return {
            "analysis_method": "ai_vision",
            "skipped": True,
            "skip_reason": "no_selected_images",
            "selected_image_count": 0,
        }
    model_name = model or os.getenv("OPENAI_ESTIMATOR_PHOTO_MODEL") or DEFAULT_PHOTO_AI_MODEL
    cache_path = _photo_ai_cache_path(
        selected_records,
        template_type=template_type,
        notes=notes,
        model=model_name,
        cache_dir=cache_dir,
    )
    if cache_path.exists() and not force:
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(cached, dict):
                cached["cache_hit"] = True
                return cached
        except Exception:
            pass

    messages = _photo_ai_messages(selected_records, template_type=template_type, notes=notes)
    raw = provider(messages, model_name) if provider is not None else _call_openai_photo_analysis(messages, model_name)
    payload = _extract_json_object(raw)
    normalized = normalize_photo_ai_payload(payload)
    normalized.update(
        {
            "analysis_method": "ai_vision",
            "ai_model": model_name,
            "cache_hit": False,
            "selected_image_count": len(selected_records),
            "selected_image_ids": [record.get("image_id") for record in selected_records],
            "selected_hashes": [record.get("content_hash") for record in selected_records],
            "source_images": [
                {
                    "image_id": record.get("image_id"),
                    "file_name": record.get("file_name"),
                    "category": record.get("category"),
                    "local_signals": record.get("signals") or [],
                }
                for record in selected_records
            ],
        }
    )
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(normalized, indent=2, sort_keys=True), encoding="utf-8")
    return normalized


def merge_photo_ai_analysis(
    photo_context: dict[str, Any],
    ai_analysis: dict[str, Any] | None,
    *,
    records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not ai_analysis or ai_analysis.get("skipped"):
        return dict(photo_context or {})
    merged = dict(photo_context or {})
    template_type = str(merged.get("template_type") or ai_analysis.get("template_type") or "roofing")
    selected_hashes = {str(value) for value in (merged.get("selected_hashes") or ai_analysis.get("selected_hashes") or []) if str(value)}
    selected_records = [
        record for record in (records or []) if not selected_hashes or str(record.get("content_hash") or "") in selected_hashes
    ]
    local_signals = {str(value) for value in merged.get("signals") or [] if str(value)}
    ai_signals = _signals_from_ai_payload(ai_analysis)
    signals = local_signals | ai_signals

    visible_issues = _merge_lists(merged.get("visible_issues"), ai_analysis.get("visible_issues"))
    scope_notes = _merge_lists(merged.get("recommended_scope_notes"), ai_analysis.get("recommended_scope_notes"), ai_analysis.get("prep_needs"), ai_analysis.get("material_hints"))
    risk_flags = _merge_lists(merged.get("risk_flags"), ai_analysis.get("risk_flags"))
    missing_photos = _merge_lists(merged.get("missing_photos"), ai_analysis.get("missing_photos"))
    confidence = round(max(float(merged.get("confidence") or 0.0), float(ai_analysis.get("confidence") or 0.0)), 2)
    proposals = photo_decision_proposals(signals, selected_records, template_type=template_type, confidence=confidence)
    proposals = _attach_ai_evidence_to_proposals(proposals, ai_analysis)
    scope_updates = photo_scope_updates(signals, template_type, selected_records, missing_photos)
    note_text = photo_note_text(
        visible_issues=visible_issues,
        scope_notes=scope_notes,
        risk_flags=risk_flags,
        missing_photos=missing_photos,
    )
    merged.update(
        {
            "signals": sorted(signals),
            "visible_issues": visible_issues,
            "recommended_scope_notes": scope_notes,
            "risk_flags": risk_flags,
            "missing_photos": missing_photos,
            "confidence": confidence,
            "photo_decision_proposals": proposals,
            "scope_updates": scope_updates,
            "note_text": note_text,
            "ai_photo_analysis": ai_analysis,
            "ai_photo_analysis_used": True,
        }
    )
    return merged


def apply_photo_scope_context(scope: dict[str, Any], photo_context: dict[str, Any] | None) -> dict[str, Any]:
    if not photo_context:
        return dict(scope)
    updated = dict(scope or {})
    scope_updates = dict(photo_context.get("scope_updates") or {})
    for key, value in scope_updates.items():
        if key in {"defects", "scope_triggers"}:
            merged = dict(updated.get(key) or {})
            merged.update(value or {})
            updated[key] = merged
        elif key in {"condition_detail_flags", "missing_info", "missing_questions", "review_flags"}:
            existing = updated.get(key) or []
            existing_list = existing if isinstance(existing, list) else [existing]
            additions = value if isinstance(value, list) else [value]
            updated[key] = list(dict.fromkeys([*existing_list, *additions]))
        elif value not in (None, "", []):
            if not updated.get(key):
                updated[key] = value
    proposals = list(updated.get("photo_decision_proposals") or [])
    proposals.extend(photo_context.get("photo_decision_proposals") or [])
    if proposals:
        updated["photo_decision_proposals"] = _dedupe_proposals(proposals)
    evidence = dict(updated.get("evidence_by_field") or {})
    evidence["photo_evidence"] = photo_context.get("note_text") or ""
    updated["evidence_by_field"] = evidence
    confidence = dict(updated.get("confidence_by_field") or {})
    confidence["photo_evidence"] = photo_context.get("confidence")
    updated["confidence_by_field"] = confidence
    updated["photo_evidence"] = photo_context
    return updated


def photo_decision_proposals(
    signals: set[str],
    selected_records: list[dict[str, Any]],
    *,
    template_type: str,
    confidence: float,
) -> list[dict[str, Any]]:
    evidence = {
        "photo_evidence": [
            {
                "image_id": record.get("image_id"),
                "file_name": record.get("file_name"),
                "category": record.get("category"),
                "signals": record.get("signals") or [],
            }
            for record in selected_records[:12]
        ]
    }
    proposals: list[dict[str, Any]] = []

    def add(section: str, decision_id: str, bucket: str, row: str, reason: str, conf_delta: float = 0.0) -> None:
        proposals.append(
            {
                "decision_id": decision_id,
                "template_type": template_type,
                "template_bucket": bucket,
                "workbook_row": row,
                "include": True,
                "proposed_values": {},
                "confidence": round(max(0.0, min(0.95, confidence + conf_delta)), 4),
                "review_required": True,
                "review_reasons": [reason],
                "evidence": evidence,
                "source": "photo_evidence",
                "section": section,
            }
        )

    if template_type == "insulation":
        if "spray_foam" in signals:
            add("insulation_foam_template_decisions", "insulation_foam_template_selector", "foam", "19", "Photo evidence suggests spray foam/insulation scope; verify surfaces, R-values, and foam type.", 0.05)
        if "thermal_barrier" in signals:
            add("insulation_thermal_barrier_template_decisions", "insulation_thermal_barrier_row_30", "thermal_barrier_coating", "30", "Photo evidence mentions/shows thermal or ignition barrier context; confirm code requirement.")
        if "masking_cleanup" in signals or "access_constraints" in signals:
            add("insulation_detail_material_template_decisions", "insulation_caulk_sealant_row_41", "caulk_sealant", "41", "Photo evidence suggests details, masking, or access constraints; verify sealant/detail scope.", -0.05)
        return _dedupe_proposals(proposals)

    if "coating_wear" in signals or "membrane_roof" in signals or "metal_roof" in signals:
        add("roofing_coating_template_decisions", "roofing_coating_system_row_26", "coating", "26", "Photo evidence suggests roof restoration/coating review; verify substrate, adhesion, and warranty eligibility.", -0.05)
    if "open_seams" in signals:
        add("roofing_detail_quantity_template_decisions", "roofing_seams_misc_row_47", "seams_misc", "47", "Photo evidence suggests open seams; estimator must confirm linear footage.")
        add("roofing_labor_template_decisions", "roofing_labor_seam_sealer_row_120", "labor_seam_sealer", "120", "Photo evidence suggests seam treatment labor; estimator must confirm extent.", -0.05)
    if "curbs" in signals or "penetrations" in signals or "edge_detail" in signals:
        add("roofing_detail_template_decisions", "roofing_caulk_sealant_row_43", "caulk_detail", "43", "Photo evidence suggests curb/penetration/edge detail treatment; verify quantities.")
        add("roofing_detail_quantity_template_decisions", "roofing_penetrations_row_49", "penetrations", "49", "Photo evidence suggests penetration/detail quantities; estimator must count units.", -0.05)
    if "rusted_fasteners" in signals:
        add("roofing_primer_template_decisions", "roofing_primer_system_row_39", "primer", "39", "Photo evidence suggests rust/fastener prep; confirm primer/rust treatment.")
        add("roofing_board_fastener_template_decisions", "roofing_fasteners_row_63", "fasteners", "63", "Photo evidence suggests fastener treatment; verify fastener count and approach.", -0.05)
    if "ponding" in signals:
        add("roofing_detail_template_decisions", "roofing_fabric_row_79", "fabric", "79", "Photo evidence suggests ponding/drain areas; verify reinforcement or repair scope before coating.", -0.1)
    if "access_constraints" in signals:
        add("roofing_equipment_template_decisions", "roofing_lift_equipment_row_73", "lift", "73", "Photo evidence suggests access constraints; verify lift/equipment type and duration.", -0.1)
    return _dedupe_proposals(proposals)


def photo_scope_updates(
    signals: set[str],
    template_type: str,
    selected_records: list[dict[str, Any]],
    missing_photos: list[str],
) -> dict[str, Any]:
    updates: dict[str, Any] = {
        "condition_detail_flags": sorted(signals),
        "missing_info": [f"Missing photo: {item}" for item in missing_photos],
        "missing_questions": [f"Photo review: provide {item}." for item in missing_photos],
        "review_flags": ["Photo evidence is advisory; estimator must confirm quantities, substrate condition, and hidden conditions."],
    }
    if template_type == "insulation":
        if "spray_foam" in signals:
            updates["foam_requested"] = True
            updates["scope_triggers"] = {"travel": True}
        return updates
    defects = {
        "open_seams": "open_seams" in signals,
        "ponding": "ponding" in signals,
        "rusted_fasteners": "rusted_fasteners" in signals,
        "edge_metal_issues": "edge_detail" in signals,
        "curb/flashing_issues": bool({"curbs", "penetrations"} & signals),
    }
    updates["defects"] = {key: value for key, value in defects.items() if value}
    updates["scope_triggers"] = {
        "coating": bool({"coating_wear", "membrane_roof", "metal_roof"} & signals),
        "primer": "rusted_fasteners" in signals,
        "seam_treatment": "open_seams" in signals,
        "fastener_treatment": "rusted_fasteners" in signals,
        "caulk_detail": bool({"curbs", "penetrations", "edge_detail"} & signals),
        "fabric": "ponding" in signals,
        "lift": "access_constraints" in signals,
    }
    if "metal_roof" in signals:
        updates["roof_type_substrate"] = "metal roof"
        updates["substrate"] = "metal roof"
    if "membrane_roof" in signals:
        updates["roof_type_substrate"] = "membrane roof"
        updates["substrate"] = "membrane roof"
    condition = _roof_condition(signals)
    if condition:
        updates["roof_condition"] = condition
    if "access_constraints" in signals:
        updates["access_complexity"] = "review"
    if {"curbs", "penetrations"} & signals:
        updates["penetrations_complexity"] = "review"
    return updates


def photo_note_text(
    *,
    visible_issues: list[str],
    scope_notes: list[str],
    risk_flags: list[str],
    missing_photos: list[str],
) -> str:
    parts: list[str] = []
    if visible_issues:
        parts.append("Photo-visible issues: " + "; ".join(visible_issues) + ".")
    if scope_notes:
        parts.append("Photo-derived scope prompts: " + "; ".join(scope_notes) + ".")
    if risk_flags:
        parts.append("Photo review flags: " + "; ".join(risk_flags) + ".")
    if missing_photos:
        parts.append("Missing useful photos: " + "; ".join(missing_photos) + ".")
    return " ".join(parts)


def combine_notes_with_photo_context(notes: str, photo_context: dict[str, Any] | None) -> str:
    photo_text = (photo_context or {}).get("note_text")
    if not photo_text:
        return notes
    return f"{notes.rstrip()}\n\nPhoto evidence summary: {photo_text}".strip()


def normalize_text(value: Any) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).split())


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
        return bytes(data or b"")
    path = Path(str(uploaded))
    return path.read_bytes() if path.exists() else b""


def _image_metadata(path: Path, thumbnail_path: Path, thumbnail_size: tuple[int, int]) -> dict[str, Any]:
    try:
        from PIL import Image, ImageStat
    except Exception:
        return {"quality_flags": ["pillow_unavailable"]}
    try:
        with Image.open(path) as image:
            width, height = image.size
            image = image.convert("RGB")
            grayscale = image.convert("L")
            stat = ImageStat.Stat(grayscale)
            brightness = round(float(stat.mean[0]), 2) if stat.mean else None
            flags: list[str] = []
            if width < 200 or height < 200:
                flags.append("small_image")
            if brightness is not None and brightness < 35:
                flags.append("dark_image")
            thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
            thumb = image.copy()
            thumb.thumbnail(thumbnail_size)
            if thumb.mode != "RGB":
                thumb = thumb.convert("RGB")
            thumb.save(thumbnail_path, format="JPEG", quality=78)
            return {
                "width": width,
                "height": height,
                "brightness": brightness,
                "thumbnail_path": str(thumbnail_path),
                "quality_flags": flags,
            }
    except Exception:
        return {"quality_flags": ["unreadable_image"]}


def _record_selection_sort(record: dict[str, Any]) -> tuple[int, int, int, str]:
    category = str(record.get("category") or "unknown")
    category_rank = 0 if category != "unknown" else 1
    signal_rank = -len(record.get("signals") or [])
    quality_rank = len(record.get("quality_flags") or [])
    return (category_rank, signal_rank, quality_rank, str(record.get("file_name") or ""))


def _signal_set(records: list[dict[str, Any]]) -> set[str]:
    signals: set[str] = set()
    for record in records:
        signals.update(str(signal) for signal in (record.get("signals") or []) if str(signal))
    return signals


def _missing_photos(categories: set[str], template_type: str) -> list[str]:
    required = REQUIRED_INSULATION_CATEGORIES if template_type == "insulation" else REQUIRED_ROOFING_CATEGORIES
    return [label for category, label in required.items() if category not in categories]


def _visible_issues(signals: set[str]) -> list[str]:
    mapping = {
        "open_seams": "open seams",
        "ponding": "ponding/drainage areas",
        "coating_wear": "coating wear or weathering",
        "curbs": "curb/flashing details",
        "penetrations": "penetration details",
        "edge_detail": "edge/parapet details",
        "rusted_fasteners": "rust or fastener concerns",
        "blistering": "blistering/bubbling",
        "access_constraints": "access constraints",
    }
    return [label for signal, label in mapping.items() if signal in signals]


def _scope_notes(signals: set[str], template_type: str) -> list[str]:
    if template_type == "insulation":
        notes = []
        if "spray_foam" in signals:
            notes.append("verify foam type, surface areas, R-values, and thickness")
        if "thermal_barrier" in signals:
            notes.append("review ignition/thermal barrier requirement")
        if "masking_cleanup" in signals:
            notes.append("include masking, setup, cleanup, and protection")
        return notes
    mapping = {
        "open_seams": "treat seams",
        "curbs": "treat curbs/flashing",
        "penetrations": "treat penetrations",
        "edge_detail": "review edge/parapet treatment",
        "rusted_fasteners": "review rust/fastener treatment and primer",
        "ponding": "review drains/ponding and possible reinforcement",
        "coating_wear": "review coating/restoration path",
        "access_constraints": "review lift/access logistics",
    }
    return [label for signal, label in mapping.items() if signal in signals]


def _risk_flags(signals: set[str], missing_photos: list[str], template_type: str) -> list[str]:
    flags: list[str] = []
    if template_type == "roofing":
        if "ponding" in signals:
            flags.append("confirm wet areas/moisture before coating")
        if "coating_wear" in signals or "membrane_roof" in signals:
            flags.append("verify adhesion and substrate qualification")
        if "rusted_fasteners" in signals:
            flags.append("confirm rust severity and fastener replacement needs")
    if missing_photos:
        flags.append("photo set is incomplete; estimator should request missing views")
    return flags


def _roof_condition(signals: set[str]) -> str:
    severe = {"ponding", "blistering", "rusted_fasteners", "open_seams"}
    if len(severe & signals) >= 3:
        return "weathered with multiple review issues"
    if severe & signals:
        return "weathered/serviceable pending review"
    if "coating_wear" in signals:
        return "weathered but serviceable"
    return ""


def _photo_confidence(records: list[dict[str, Any]], signals: set[str]) -> float:
    if not records:
        return 0.0
    base = 0.35 + min(len(records), DEFAULT_MAX_REPRESENTATIVE_IMAGES) * 0.035 + min(len(signals), 8) * 0.035
    low_quality = sum(1 for record in records if record.get("quality_flags"))
    base -= low_quality * 0.03
    return round(max(0.25, min(base, 0.82)), 2)


def _dedupe_proposals(proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for proposal in proposals:
        key = (
            str(proposal.get("template_type") or ""),
            str(proposal.get("section") or ""),
            str(proposal.get("decision_id") or ""),
            str(proposal.get("workbook_row") or ""),
        )
        if key not in deduped:
            deduped[key] = proposal
    return list(deduped.values())


def normalize_photo_ai_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "template_type": normalize_text(payload.get("template_type")),
        "roof_condition": _clean_string(payload.get("roof_condition")),
        "existing_system": _clean_string(payload.get("existing_system")),
        "access_notes": _clean_list(payload.get("access_notes")),
        "visible_issues": _clean_list(payload.get("visible_issues")),
        "recommended_scope_notes": _clean_list(payload.get("recommended_scope_notes")),
        "prep_needs": _clean_list(payload.get("prep_needs")),
        "risk_flags": _clean_list(payload.get("risk_flags")),
        "material_hints": _clean_list(payload.get("material_hints")),
        "missing_photos": _clean_list(payload.get("missing_photos")),
        "decision_cues": _clean_list(payload.get("decision_cues")),
        "confidence": _bounded_confidence(payload.get("confidence")),
    }
    return normalized


def _photo_ai_messages(records: list[dict[str, Any]], *, template_type: str, notes: str) -> list[dict[str, Any]]:
    local_context = [
        {
            "image_id": record.get("image_id"),
            "file_name": record.get("file_name"),
            "local_category": record.get("category"),
            "local_signals": record.get("signals") or [],
        }
        for record in records
    ]
    prompt = (
        "Analyze the selected Spray-Tec estimating photos only for estimator decision support. "
        "Return strict JSON with keys: template_type, roof_condition, existing_system, visible_issues, "
        "recommended_scope_notes, prep_needs, risk_flags, material_hints, access_notes, missing_photos, "
        "decision_cues, confidence. Keep every list concise. Do not calculate prices, quantities, areas, "
        "or warranty years. Do not identify people. If evidence is weak, say what the estimator should confirm. "
        "Focus decision cues on template-relevant work such as coating path, primer/rust treatment, seams, "
        "penetrations, curbs/flashing, fabric/reinforcement, fasteners, wet-area review, lift/access, "
        "spray foam surfaces, R-value/thickness confirmation, masking, and thermal/ignition barrier review.\n\n"
        f"Estimate type hint: {template_type or 'unknown'}\n"
        f"Field notes:\n{notes[:6000]}\n\n"
        f"Local image metadata:\n{json.dumps(local_context, indent=2)}"
    )
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for record in records:
        data_url = _image_data_url(record)
        if data_url:
            content.append({"type": "image_url", "image_url": {"url": data_url, "detail": "low"}})
    return [
        {
            "role": "system",
            "content": "You inspect construction site photos for estimating support and return only strict JSON.",
        },
        {"role": "user", "content": content},
    ]


def _call_openai_photo_analysis(messages: list[dict[str, Any]], model: str) -> str:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set")
    try:
        from openai import OpenAI  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("openai package is not installed") from exc
    try:
        timeout_seconds = float(os.getenv("OPENAI_ESTIMATOR_PHOTO_TIMEOUT_SECONDS", "45"))
    except (TypeError, ValueError):
        timeout_seconds = 45.0
    client = OpenAI(timeout=timeout_seconds)
    response = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=messages,
    )
    return response.choices[0].message.content or "{}"


def _extract_json_object(value: Any) -> dict[str, Any]:
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


def _photo_ai_cache_path(
    records: list[dict[str, Any]],
    *,
    template_type: str,
    notes: str,
    model: str,
    cache_dir: str | Path,
) -> Path:
    key_payload = {
        "version": PHOTO_AI_CACHE_VERSION,
        "model": model,
        "template_type": normalize_text(template_type),
        "notes_hash": hashlib.sha256(str(notes or "").encode("utf-8")).hexdigest(),
        "image_hashes": [record.get("content_hash") for record in records],
    }
    key = hashlib.sha256(json.dumps(key_payload, sort_keys=True).encode("utf-8")).hexdigest()[:24]
    return Path(cache_dir) / f"{key}.json"


def _image_data_url(record: dict[str, Any]) -> str:
    path = Path(str(record.get("stored_path") or ""))
    if not path.exists():
        return ""
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def _signals_from_ai_payload(payload: dict[str, Any]) -> set[str]:
    parts: list[str] = []
    for key in (
        "roof_condition",
        "existing_system",
        "visible_issues",
        "recommended_scope_notes",
        "prep_needs",
        "risk_flags",
        "material_hints",
        "access_notes",
        "decision_cues",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
        elif value:
            parts.append(str(value))
    text = normalize_text(" ".join(parts))
    signals: set[str] = set()
    for signal, terms in PHOTO_SIGNAL_KEYWORDS.items():
        if any(normalize_text(term) in text for term in terms):
            signals.add(signal)
    if any(term in text for term in ("restoration", "silicone", "acrylic", "coat", "coating")):
        signals.add("coating_wear")
    if any(term in text for term in ("open seams", "seam treatment", "laps", "lap seams")):
        signals.add("open_seams")
    if any(term in text for term in ("wet area", "wet insulation", "standing water", "drainage")):
        signals.add("ponding")
    if any(term in text for term in ("curb", "flashing")):
        signals.add("curbs")
    if any(term in text for term in ("penetration", "pipe boot", "pitch pocket")):
        signals.add("penetrations")
    if any(term in text for term in ("lift", "ladder", "access", "height", "obstruction")):
        signals.add("access_constraints")
    if any(term in text for term in ("foam", "spray foam", "insulation")):
        signals.add("spray_foam")
    return signals


def _attach_ai_evidence_to_proposals(proposals: list[dict[str, Any]], ai_analysis: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = {
        "confidence": ai_analysis.get("confidence"),
        "visible_issues": ai_analysis.get("visible_issues") or [],
        "decision_cues": ai_analysis.get("decision_cues") or [],
        "source_images": ai_analysis.get("source_images") or [],
    }
    updated: list[dict[str, Any]] = []
    for proposal in proposals:
        row = dict(proposal)
        proposal_evidence = dict(row.get("evidence") or {})
        proposal_evidence.setdefault("photo_ai", []).append(evidence)
        row["evidence"] = proposal_evidence
        reasons = list(row.get("review_reasons") or [])
        if "AI photo analysis is advisory; estimator must confirm before quoting." not in reasons:
            reasons.append("AI photo analysis is advisory; estimator must confirm before quoting.")
        row["review_reasons"] = reasons
        updated.append(row)
    return updated


def _merge_lists(*values: Any) -> list[str]:
    merged: list[str] = []
    for value in values:
        items = value if isinstance(value, list) else [value] if value else []
        for item in items:
            text = _clean_string(item)
            if text and text not in merged:
                merged.append(text)
    return merged


def _clean_list(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    return [text for text in (_clean_string(item) for item in values) if text]


def _clean_string(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _bounded_confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    if number > 1:
        number = number / 100.0
    return round(max(0.0, min(number, 0.95)), 2)


def copy_selected_images_for_ai(records: list[dict[str, Any]], selected_hashes: Iterable[str], target_dir: str | Path) -> list[Path]:
    """Utility for a future explicit vision-model call.

    The first slice does not call an AI service. This helper gives the next
    slice a bounded, reviewed image set to send if the estimator clicks an
    Analyze Selected Photos button.
    """

    selected = {str(value) for value in selected_hashes}
    out_dir = Path(target_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for record in records:
        if str(record.get("content_hash")) not in selected:
            continue
        source = Path(str(record.get("stored_path") or ""))
        if not source.exists():
            continue
        target = out_dir / source.name
        if not target.exists():
            shutil.copy2(source, target)
        paths.append(target)
    return paths
