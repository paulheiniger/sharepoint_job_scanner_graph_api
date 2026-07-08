from __future__ import annotations

import io
import json
from pathlib import Path

from PIL import Image

from jobscan.estimator.note_images import (
    extract_notes_from_images_with_ai,
    note_image_messages,
    stage_note_images,
)


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
    assert image_parts
    assert image_parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
