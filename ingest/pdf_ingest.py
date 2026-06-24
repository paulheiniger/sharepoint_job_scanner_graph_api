from __future__ import annotations

import tempfile
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

from .ocr import is_text_sparse, merge_text_with_ocr, ocr_page_image


@dataclass
class PageRecord:
    document_id: str
    document_name: str
    document_type: str
    source_path: str
    global_page_id: str
    page_index: int
    page_num: int
    page_number: int
    text: str
    word_count: int
    width: float
    height: float
    sheet_number: str = ""
    sheet_title: str = ""
    references: list[dict[str, Any]] = field(default_factory=list)
    relevance_score: float = 0.0
    relevance_level: str = "low"
    role: str = "irrelevant"
    evidence: list[str] = field(default_factory=list)
    used_ocr: bool = False
    warnings: list[str] = field(default_factory=list)
    processing_status: str = "manifested"
    original_document_name: str = ""
    original_page_number: int | None = None
    sheet_id_confidence: float = 0.0
    sheet_id_source: str = ""
    filename_sheet_id: str = ""
    extracted_sheet_id: str = ""
    canonical_sheet_id: str = ""
    foam_seed_level: str = "none"
    foam_specific_evidence: list[str] = field(default_factory=list)
    generic_evidence: list[str] = field(default_factory=list)
    seed_evidence_score: float = 0.0
    measurement_likelihood_score: float = 0.0
    final_selection_score: float = 0.0
    graph_distance_from_seed: int | None = None
    connected_seed_pages: list[str] = field(default_factory=list)
    inclusion_path: list[str] = field(default_factory=list)
    trade_type: str = "foam_insulation"
    trade_name: str = "Foam Insulation"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def sheet_id(self) -> str:
        return self.canonical_sheet_id or self.sheet_number

    @property
    def page_type(self) -> str:
        return self.role if self.role not in {"unknown", "irrelevant", "candidate_only"} else self.document_type

    @property
    def foam_relevance(self) -> str:
        return self.relevance_level


def _bytes_from_upload(upload: bytes | BinaryIO | Path | str) -> bytes:
    if isinstance(upload, bytes):
        return upload
    if isinstance(upload, (str, Path)):
        return Path(upload).read_bytes()
    if hasattr(upload, "getvalue"):
        return upload.getvalue()
    return upload.read()


def classify_document_type(document_name: str, text: str = "") -> str:
    haystack = f"{document_name}\n{text}".lower()
    if any(term in haystack for term in ("spec", "specification", "project manual")):
        return "specifications"
    filename = Path(document_name or "").stem.upper().replace(".", "-").replace("_", "-")
    if filename.startswith("A") or re.search(r"\bA\d?-\d{2,4}\b", filename):
        return "architectural_drawings"
    if filename.startswith("S") or re.search(r"\bS\d?-\d{2,4}\b", filename):
        return "structural_drawings"
    if filename.startswith("M") or re.search(r"\bM\d?-\d{2,4}\b", filename):
        return "mechanical_drawings"
    if filename.startswith("P") or re.search(r"\bP\d?-\d{2,4}\b", filename):
        return "plumbing_drawings"
    if filename.startswith("E") or re.search(r"\bE\d?-\d{2,4}\b", filename):
        return "electrical_drawings"
    if filename.startswith(("FP", "FA")):
        return "fire_protection_drawings"
    if filename.startswith("C") or re.search(r"\bC\d?-\d{2,4}\b", filename):
        return "civil_drawings"
    if filename.startswith("L") or re.search(r"\bL\d?-\d{2,4}\b", filename):
        return "landscape_drawings"
    if any(term in haystack for term in ("architectural", "floor plan", "wall section")) or "a-" in haystack:
        return "architectural_drawings"
    if "structural" in haystack or "s-" in haystack:
        return "structural_drawings"
    return "unknown_pdf"


def _extract_pdfplumber_words(path: Path) -> dict[int, list[dict[str, Any]]]:
    try:
        import pdfplumber
    except Exception:
        return {}
    words_by_page: dict[int, list[dict[str, Any]]] = {}
    try:
        with pdfplumber.open(str(path)) as pdf:
            for index, page in enumerate(pdf.pages):
                words_by_page[index] = page.extract_words() or []
    except Exception:
        return words_by_page
    return words_by_page


def _render_page_png(page: Any, *, dpi: int = 160) -> bytes:
    import fitz

    pixmap = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
    return pixmap.tobytes("png")


def ingest_pdf(
    upload: bytes | BinaryIO | Path | str,
    *,
    ocr_sparse_pages: bool = True,
    document_id: str | None = None,
    document_name: str | None = None,
    document_type: str | None = None,
    source_path: str | None = None,
    original_document_name: str | None = None,
    original_page_number: int | None = None,
) -> list[PageRecord]:
    """Split a PDF into page records with text and basic geometry."""
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("Install PyMuPDF to ingest PDFs: pip install PyMuPDF") from exc

    pdf_bytes = _bytes_from_upload(upload)
    if document_name is None:
        document_name = Path(upload).name if isinstance(upload, (str, Path)) else "uploaded.pdf"
    if document_id is None:
        safe_name = "".join(char.lower() if char.isalnum() else "-" for char in document_name).strip("-")
        document_id = safe_name or "document"
    if source_path is None:
        source_path = str(upload) if isinstance(upload, (str, Path)) else document_name

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = Path(tmp.name)

    words_by_page = _extract_pdfplumber_words(tmp_path)
    records: list[PageRecord] = []
    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
        for index, page in enumerate(document):
            text = page.get_text("text") or ""
            warnings: list[str] = []
            used_ocr = False
            if ocr_sparse_pages and is_text_sparse(text):
                ocr_result = ocr_page_image(_render_page_png(page))
                used_ocr = ocr_result.used_ocr
                if ocr_result.warning:
                    warnings.append(ocr_result.warning)
                text = merge_text_with_ocr(text, ocr_result)
            words = words_by_page.get(index) or []
            word_count = len(words) if words else len(text.split())
            rect = page.rect
            records.append(
                PageRecord(
                    document_id=document_id,
                    document_name=document_name,
                    document_type=document_type or "unknown_pdf",
                    source_path=source_path,
                    global_page_id=f"{document_id}::page_{index + 1}",
                    page_index=index,
                    page_num=index + 1,
                    page_number=index + 1,
                    text=text,
                    word_count=word_count,
                    width=float(rect.width),
                    height=float(rect.height),
                    used_ocr=used_ocr,
                    warnings=warnings,
                    original_document_name=original_document_name or document_name,
                    original_page_number=original_page_number,
                )
            )
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass
    detected_type = document_type or classify_document_type(document_name, "\n".join(page.text[:2000] for page in records[:3]))
    for record in records:
        record.document_type = detected_type
    return records
