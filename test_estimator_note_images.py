from __future__ import annotations

import io
import json
from pathlib import Path

from PIL import Image

from jobscan.estimator.field_estimator import estimate_from_field_notes
from jobscan.estimator.note_images import (
    extract_notes_from_images_with_ai,
    note_image_messages,
    normalize_note_image_payload,
    stage_note_images,
)
from jobscan.estimator.schemas import EstimatorData


class UploadedBytes:
    def __init__(self, name: str, data: bytes) -> None:
        self.name = name
        self._data = data

    def getvalue(self) -> bytes:
        return self._data


def png_bytes() -> bytes:
    image = Image.new("RGB", (120, 80), color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_stage_note_images_converts_to_jpeg(tmp_path: Path) -> None:
    uploaded = UploadedBytes("field-note.png", png_bytes())

    records = stage_note_images([uploaded], upload_key="case-1", storage_root=tmp_path)

    assert len(records) == 1
    assert records[0]["file_name"] == "field-note.png"
    assert records[0]["conversion_error"] == ""
    assert Path(records[0]["converted_path"]).exists()


def test_extract_notes_from_images_with_provider_and_cache(tmp_path: Path) -> None:
    uploaded = UploadedBytes("field-note.png", png_bytes())
    records = stage_note_images([uploaded], upload_key="case-2", storage_root=tmp_path / "uploads")
    calls = []

    def provider(messages, model):
        calls.append((messages, model))
        return json.dumps(
            {
                "transcribed_text": "30 x 40 metal building, 9 ft walls",
                "normalized_estimator_notes": "Insulate 30x40 metal building walls and ceiling. 9 ft walls.",
                "questions": ["Confirm foam type."],
                "confidence": 0.82,
            }
        )

    first = extract_notes_from_images_with_ai(
        records,
        cache_dir=tmp_path / "cache",
        provider=provider,
        model="test-model",
    )
    second = extract_notes_from_images_with_ai(
        records,
        cache_dir=tmp_path / "cache",
        provider=provider,
        model="test-model",
    )

    assert first["normalized_estimator_notes"].startswith("Insulate 30x40")
    assert first["questions"] == ["Confirm foam type."]
    assert first["confidence"] == 0.82
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert len(calls) == 1


def test_note_image_messages_include_estimator_transcription_prompt(tmp_path: Path) -> None:
    uploaded = UploadedBytes("field-note.png", png_bytes())
    records = stage_note_images([uploaded], upload_key="case-3", storage_root=tmp_path)

    messages = note_image_messages(records)
    text_parts = [
        part["text"]
        for part in messages[1]["content"]
        if isinstance(part, dict) and part.get("type") == "text"
    ]
    image_parts = [
        part
        for part in messages[1]["content"]
        if isinstance(part, dict) and part.get("type") == "image_url"
    ]

    assert "normalized_estimator_notes" in text_parts[0]
    assert "Do not calculate prices" in text_parts[0]
    assert "annotated aerial" in text_parts[0]
    assert "Never add a nested sub-scope to the total roof area" in text_parts[0]
    assert image_parts
    assert image_parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_annotated_aerial_scope_is_normalized_without_double_counting_nested_repairs() -> None:
    records = [{"image_id": "aerial-1", "file_name": "Aerial Map - For Estimating AI.png"}]
    payload = {
        "document_type": "annotated_aerial_takeoff",
        "transcribed_text": "Grossman Tuning, 830 South 1st Street, Louisville KY 40203",
        "job_header": {
            "job_name": "Grossman Tuning",
            "site_address": "830 South 1st Street, Louisville, KY 40203",
            "declared_total_area_sqft": 5136,
        },
        "area_scopes": [
            {
                "scope_id": "tear_off",
                "scope_role": "exclusive_area",
                "label": "Full removal area",
                "area_sqft": 3120,
                "action": "Full removal down to wood decking; remove/replace deteriorated decking.",
                "proposed_assembly": "2 in Resista ISO board and 1.5 in coated foam roof",
                "decking_replacement_sqft": 320,
            },
            {
                "scope_id": "recover",
                "scope_role": "exclusive_area",
                "label": "Foam over existing roof",
                "area_sqft": 2016,
                "action": "Install over existing roof",
                "proposed_assembly": "1.5 in coated foam",
            },
        ],
        "linear_scopes": [
            {"item": "counter flashing", "action": "new", "linear_ft": 24},
            {"item": "edge metal, gutter, and downspouts", "action": "new", "size": "3.5 in", "linear_ft": 52},
            {"item": "wood nailer", "action": "new", "size": "2x10", "linear_ft": 52},
            {"item": "foam-stop edge metal", "action": "new", "size": "3 in", "linear_ft": 52},
            {"item": "foam-stop edge metal", "action": "new", "size": "2 in", "linear_ft": 24},
        ],
        "retain_existing": ["Terra cotta coping; seal seams with caulk."],
        "scope_relationships": ["The 320 sq ft decking replacement is nested within the 3,120 sq ft full-removal area."],
        "area_reconciliation": {
            "declared_total_area_sqft": 5136,
            "exclusive_scope_total_sqft": 5136,
            "nested_sub_scope_sqft": 320,
            "difference_sqft": 0,
        },
        "confidence": 0.94,
    }

    result = normalize_note_image_payload(payload, records=records)
    notes = result["normalized_estimator_notes"]

    assert result["document_type"] == "annotated_aerial_takeoff"
    assert len(result["area_scopes"]) == 2
    assert result["area_reconciliation"]["exclusive_scope_total_sqft"] == 5136
    assert "Declared total roof area: 5136 sq ft" in notes
    assert "3120 sq ft" in notes
    assert "320 sq ft decking replacement" in notes
    assert "2016 sq ft" in notes
    assert "52 linear ft" in notes
    assert "Existing item to remain: Terra cotta coping" in notes
    assert "nested within the 3,120 sq ft full-removal area" in notes

    recommendation = estimate_from_field_notes(
        notes,
        {"disable_ai_scope_interpreter": True},
        data=EstimatorData(),
    )
    assert recommendation.parsed_fields["division"] == "ROOFING"
    assert recommendation.parsed_fields["estimated_sqft"] == 5136
