from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from .pdf_ingest import classify_document_type
from .zip_ingest import extract_zip_pdfs


@dataclass
class PdfDocumentInput:
    document_id: str
    document_name: str
    document_type: str
    source_path: str
    content: bytes

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("content", None)
        return data


@dataclass
class PackageIngestResult:
    documents: list[PdfDocumentInput]
    warnings: list[str]


def _upload_name(upload: Any) -> str:
    return str(getattr(upload, "name", "uploaded_file"))


def _upload_bytes(upload: bytes | BinaryIO | Any) -> bytes:
    if isinstance(upload, bytes):
        return upload
    if hasattr(upload, "getvalue"):
        return upload.getvalue()
    return upload.read()


def _stable_document_id(name: str, content: bytes, index: int) -> str:
    digest = hashlib.sha1(name.encode("utf-8") + b"\0" + content[:4096]).hexdigest()[:12]
    stem = "".join(char.lower() if char.isalnum() else "-" for char in Path(name).stem).strip("-")
    return f"{stem or 'document'}-{index + 1}-{digest}"


def is_safe_zip_pdf_member(member_name: str) -> tuple[bool, str | None]:
    path = PurePosixPath(member_name)
    parts = path.parts
    if not parts or member_name.endswith("/"):
        return False, None
    if any(part in {"", ".", ".."} for part in parts) or path.is_absolute():
        return False, f"Skipped unsafe ZIP path: {member_name}"
    if any(part == "__MACOSX" for part in parts) or path.name == ".DS_Store":
        return False, None
    if path.suffix.lower() != ".pdf":
        return False, f"Skipped non-PDF ZIP member: {member_name}"
    return True, None


def normalize_pdf_document(name: str, content: bytes, *, index: int, source_path: str | None = None) -> PdfDocumentInput:
    document_type = classify_document_type(name)
    return PdfDocumentInput(
        document_id=_stable_document_id(name, content, index),
        document_name=Path(name).name,
        document_type=document_type,
        source_path=source_path or name,
        content=content,
    )


def ingest_uploaded_package(uploaded_files: list[Any] | Any) -> PackageIngestResult:
    """Normalize Streamlit UploadedFile objects into PDF document inputs."""
    if uploaded_files is None:
        return PackageIngestResult(documents=[], warnings=[])
    if not isinstance(uploaded_files, list):
        uploaded_files = [uploaded_files]

    documents: list[PdfDocumentInput] = []
    warnings: list[str] = []
    for upload in uploaded_files:
        name = _upload_name(upload)
        suffix = Path(name).suffix.lower()
        try:
            content = _upload_bytes(upload)
        except Exception as exc:
            warnings.append(f"Skipped {name}: could not read upload ({type(exc).__name__})")
            continue

        if suffix == ".pdf":
            documents.append(normalize_pdf_document(name, content, index=len(documents)))
        elif suffix == ".zip":
            extracted_docs, zip_warnings = extract_zip_pdfs(name, content, starting_index=len(documents))
            documents.extend(extracted_docs)
            warnings.extend(zip_warnings)
        else:
            warnings.append(f"Skipped unsupported upload: {name}")

    return PackageIngestResult(documents=documents, warnings=warnings)
