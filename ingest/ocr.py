from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class OcrResult:
    text: str = ""
    used_ocr: bool = False
    warning: str = ""


def is_text_sparse(text: str | None, *, min_chars: int = 80) -> bool:
    return len((text or "").strip()) < min_chars


def ocr_page_image(image_bytes: bytes) -> OcrResult:
    """OCR a rendered page image if optional dependencies are available.

    This deliberately avoids making OCR required for the prototype. If pytesseract
    or the local tesseract binary is unavailable, callers get a warning and can
    continue with embedded PDF text.
    """
    try:
        from io import BytesIO

        from PIL import Image
        import pytesseract
    except Exception as exc:  # pragma: no cover - depends on optional local packages
        return OcrResult(warning=f"OCR unavailable: {type(exc).__name__}")

    try:
        image = Image.open(BytesIO(image_bytes))
        text = pytesseract.image_to_string(image)
        return OcrResult(text=text or "", used_ocr=True)
    except Exception as exc:  # pragma: no cover - depends on local tesseract install
        return OcrResult(warning=f"OCR failed: {type(exc).__name__}")


def merge_text_with_ocr(embedded_text: str, ocr_result: OcrResult) -> str:
    if ocr_result.text and ocr_result.text.strip() not in embedded_text:
        return "\n".join(part for part in (embedded_text, ocr_result.text) if part)
    return embedded_text
