from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol


SUPPORTED_DOCUMENT_TYPES = {
    "pds": "PDS",
    "product data": "PDS",
    "product data sheet": "PDS",
    "sds": "SDS",
    "safety data": "SDS",
    "safety data sheet": "SDS",
    "application guide": "Application Guide",
    "application": "Application Guide",
    "technical bulletin": "Technical Bulletin",
    "installation guide": "Installation Guide",
}


@dataclass
class ProductDocumentText:
    path: Path
    document_type: str
    source_type: str
    text: str
    page_texts: list[tuple[int, str]]
    text_hash: str


class ProductDocumentSource(Protocol):
    """Future extension point for product document sources.

    Local PDFs are implemented now. Manufacturer websites, SharePoint, vendor
    APIs, and manual upload can implement this same interface later without
    changing the catalog/rules/matching layers.
    """

    source_type: str

    def iter_documents(self) -> Iterable[Path]:
        ...


@dataclass
class LocalProductDocumentSource:
    root: Path
    source_type: str = "local_pdf"

    def iter_documents(self) -> Iterable[Path]:
        return iter_local_product_documents(self.root)


def infer_document_type(path: Path, text: str = "") -> str:
    haystack = f"{path.name}\n{text[:2000]}".lower()
    for marker, document_type in SUPPORTED_DOCUMENT_TYPES.items():
        if marker in haystack:
            return document_type
    return "Technical Bulletin"


def iter_local_product_documents(pdf_dir: str | Path) -> Iterable[Path]:
    root = Path(pdf_dir)
    if not root.exists():
        return []
    suffixes = {".pdf", ".txt", ".md", ".text"}
    return sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in suffixes)


def _read_pdf(path: Path) -> list[tuple[int, str]]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - requirements include pypdf.
        raise RuntimeError("Install pypdf to ingest product PDFs.") from exc
    pages: list[tuple[int, str]] = []
    reader = PdfReader(str(path))
    for index, page in enumerate(reader.pages, start=1):
        try:
            pages.append((index, page.extract_text() or ""))
        except Exception:
            pages.append((index, ""))
    return pages


def extract_local_document_text(path: str | Path) -> ProductDocumentText:
    resolved = Path(path)
    if resolved.suffix.lower() == ".pdf":
        page_texts = _read_pdf(resolved)
        source_type = "local_pdf"
    else:
        page_texts = [(1, resolved.read_text(encoding="utf-8", errors="ignore"))]
        source_type = "local_text"
    text = "\n\n".join(page_text for _, page_text in page_texts)
    text_hash = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
    return ProductDocumentText(
        path=resolved,
        document_type=infer_document_type(resolved, text),
        source_type=source_type,
        text=text,
        page_texts=page_texts,
        text_hash=text_hash,
    )
