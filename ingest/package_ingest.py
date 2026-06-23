from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass
from dataclasses import replace
from pathlib import Path
from typing import Any, BinaryIO

from .pdf_ingest import classify_document_type
from .zip_ingest import extract_zip_pdf_member, inspect_zip_pdfs


MB = 1024 * 1024
GB = 1024 * MB
PACKAGE_WARNING_BYTES = 500 * MB
SELECTED_WARNING_BYTES = 1 * GB
DISK_SAFETY_FRACTION = 0.80


@dataclass
class PdfDocumentInput:
    document_id: str
    document_name: str
    document_type: str
    source_path: str
    file_path: str = ""
    file_hash: str = ""
    compressed_size: int = 0
    uncompressed_size: int = 0
    content: bytes | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("content", None)
        return data


@dataclass
class PdfCandidate:
    candidate_id: str
    document_name: str
    document_type: str
    source_kind: str
    source_path: str
    compressed_size: int
    uncompressed_size: int
    default_selected: bool
    file_hash: str
    file_path: str = ""
    zip_path: str = ""
    zip_member: str = ""
    triage_score: int = 0
    triage_classification: str = "untriaged"
    triage_evidence: list[str] | None = None
    triage_sample_pages: list[int] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PackageInspectionResult:
    candidates: list[PdfCandidate]
    warnings: list[str]
    temp_dir: str
    total_upload_size: int = 0


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


def _sha1_bytes(content: bytes) -> str:
    return hashlib.sha1(content).hexdigest()


def _safe_stem(name: str) -> str:
    stem = "".join(char.lower() if char.isalnum() else "-" for char in Path(name).stem).strip("-")
    return stem or "document"


def _temp_root() -> Path:
    root = Path(tempfile.gettempdir()) / "foamscope_packages"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _stable_document_id(name: str, file_hash: str, index: int) -> str:
    return f"{_safe_stem(name)}-{index + 1}-{file_hash[:12]}"


def _candidate_id(source_path: str, file_hash: str, index: int) -> str:
    digest = hashlib.sha1(f"{source_path}\0{file_hash}\0{index}".encode("utf-8")).hexdigest()[:16]
    return f"candidate-{digest}"


def _stage_bytes(content: bytes, *, filename: str, digest: str, subdir: str) -> Path:
    safe_name = f"{_safe_stem(filename)}-{digest[:12]}.pdf"
    path = _temp_root() / subdir / safe_name
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.stat().st_size != len(content):
        path.write_bytes(content)
    return path


def guess_default_selected(filename: str, document_type: str) -> bool:
    text = filename.lower()
    likely_irrelevant = (
        "electrical",
        "plumbing",
        "mechanical",
        "civil",
        "fire alarm",
        "low voltage",
        " e-",
        "_e-",
        "/e-",
        " p-",
        "_p-",
        "/p-",
        " m-",
        "_m-",
        "/m-",
        " c-",
        "_c-",
        "/c-",
        " fa-",
        "_fa-",
        "/fa-",
    )
    if any(term in text for term in likely_irrelevant):
        return False
    likely_relevant = ("architectural", "spec", "specifications", "project manual", "addendum", " a-", "_a-", "/a-")
    return document_type in {"architectural_drawings", "specifications"} or any(term in text for term in likely_relevant)


def _sheet_ids_from_text(text: str) -> list[str]:
    pattern = re.compile(r"\b([A-Z]{1,3}[A-Z0-9]?[-.]?\d{1,4}(?:\.\d+)?)\b", re.I)
    return sorted({match.group(1).upper().replace(".", "-") for match in pattern.finditer(text or "")})


def _is_only_mep_sheet_set(sheet_ids: list[str]) -> bool:
    if not sheet_ids:
        return False
    prefixes = {sheet_id.split("-", 1)[0].rstrip("0123456789") or sheet_id[:1] for sheet_id in sheet_ids}
    return all(sheet_id.startswith(("E-", "P-", "M-", "FA-", "E", "P", "M", "FA")) for sheet_id in sheet_ids) and not any(
        sheet_id.startswith(("A-", "A")) for sheet_id in sheet_ids
    )


def _triage_target_path(candidate: PdfCandidate) -> Path:
    return _temp_root() / "triage" / f"{_safe_stem(candidate.document_name)}-{candidate.file_hash[:12]}.pdf"


def ensure_candidate_file_for_triage(candidate: PdfCandidate) -> tuple[PdfCandidate, list[str]]:
    warnings: list[str] = []
    if candidate.file_path and Path(candidate.file_path).exists():
        return candidate, warnings
    if candidate.source_kind != "zip":
        return candidate, warnings
    target = _triage_target_path(candidate)
    if not target.exists() or target.stat().st_size != candidate.uncompressed_size:
        try:
            extract_zip_pdf_member(Path(candidate.zip_path), candidate.zip_member, target)
        except Exception as exc:
            warnings.append(f"Could not triage {candidate.source_path}: {type(exc).__name__}: {exc}")
            return candidate, warnings
    return replace(candidate, file_path=str(target)), warnings


def sample_pdf_for_triage(file_path: str, *, max_chars: int = 20000) -> tuple[str, list[int], list[str]]:
    warnings: list[str] = []
    sample_parts: list[str] = []
    sampled_pages: list[int] = []
    try:
        import fitz
    except ImportError:
        return "", [], ["PyMuPDF is not installed; filename/path triage only."]

    try:
        document = fitz.open(file_path)
    except Exception as exc:
        return "", [], [f"Could not open PDF for triage: {type(exc).__name__}: {exc}"]

    try:
        page_count = document.page_count
        page_indexes = set(range(min(2, page_count)))
        if page_count > 2:
            page_indexes.add(page_count - 1)
        for index in range(min(page_count, 12)):
            try:
                text = document[index].get_text("text") or ""
            except Exception:
                continue
            lowered = text.lower()
            if "table of contents" in lowered or "sheet index" in lowered or "drawing index" in lowered or lowered.strip().startswith("index"):
                page_indexes.add(index)
        for index in sorted(page_indexes):
            try:
                text = document[index].get_text("text") or ""
            except Exception as exc:
                warnings.append(f"Could not sample page {index + 1}: {type(exc).__name__}")
                continue
            if text.strip():
                sampled_pages.append(index + 1)
                sample_parts.append(text[:6000])
            if sum(len(part) for part in sample_parts) >= max_chars:
                break
    finally:
        document.close()
    return "\n".join(sample_parts)[:max_chars], sampled_pages, warnings


def classify_triage(score: int) -> str:
    if score >= 50:
        return "likely_relevant"
    if score >= 20:
        return "possibly_relevant"
    return "likely_irrelevant"


def triage_pdf_candidate(candidate: PdfCandidate) -> tuple[PdfCandidate, list[str]]:
    candidate, warnings = ensure_candidate_file_for_triage(candidate)
    sample_text, sample_pages, sample_warnings = sample_pdf_for_triage(candidate.file_path) if candidate.file_path else ("", [], [])
    warnings.extend(sample_warnings)
    name = candidate.document_name.lower()
    path = candidate.source_path.lower().replace("\\", "/")
    text = sample_text.lower()
    score = 0
    evidence: list[str] = []

    if "architectural" in name or "/arch" in path:
        score += 30
        evidence.append("filename/path: architectural")
    if "spec" in name or "project manual" in name:
        score += 40
        evidence.append("filename/path: spec/project manual")
    if "addendum" in name or "asi" in name or "bulletin" in name:
        score += 30
        evidence.append("filename/path: addendum/ASI/bulletin")
    if "electrical" in name or "plumbing" in name or "fire alarm" in name:
        score -= 25
        evidence.append("filename/path: likely MEP/fire alarm")

    text_rules = [
        ("spray foam", 50),
        ("thermal insulation", 40),
        ("closed cell", 50),
        ("open cell", 50),
        ("r-value", 20),
        ("wall section", 20),
        ("division 07", 40),
        ("07 21 00", 40),
    ]
    for term, points in text_rules:
        if term in text:
            score += points
            evidence.append(f"text: {term}")

    sheet_ids = _sheet_ids_from_text(f"{candidate.source_path}\n{sample_text}")
    if any(sheet_id.startswith("A-") or re.match(r"^A\d", sheet_id) for sheet_id in sheet_ids):
        score += 25
        evidence.append("sheet ids: architectural A-sheets")
    if _is_only_mep_sheet_set(sheet_ids):
        score -= 20
        evidence.append("sheet ids: only MEP/fire-alarm sheets")

    if "sheet index" in text or "drawing index" in text:
        evidence.append("text: sheet/drawing index")
    classification = classify_triage(score)
    return (
        replace(
            candidate,
            file_path=candidate.file_path,
            document_type=classify_document_type(candidate.document_name, sample_text),
            default_selected=classification == "likely_relevant"
            or (
                classification == "possibly_relevant"
                and any(
                    marker in " ".join(evidence).lower()
                    for marker in ("spray foam", "insulation", "spec", "addendum", "architectural", "a-sheets", "sheet/drawing index")
                )
            ),
            triage_score=score,
            triage_classification=classification,
            triage_evidence=evidence,
            triage_sample_pages=sample_pages,
        ),
        warnings,
    )


def triage_inspection(inspection: PackageInspectionResult) -> PackageInspectionResult:
    candidates: list[PdfCandidate] = []
    warnings = list(inspection.warnings)
    for candidate in inspection.candidates:
        triaged, triage_warnings = triage_pdf_candidate(candidate)
        candidates.append(triaged)
        warnings.extend(f"{candidate.document_name}: {warning}" for warning in triage_warnings)
    return replace(inspection, candidates=candidates, warnings=warnings)


def normalize_pdf_document(
    name: str,
    content: bytes | None = None,
    *,
    index: int,
    source_path: str | None = None,
    file_path: str | None = None,
    file_hash: str | None = None,
    compressed_size: int | None = None,
    uncompressed_size: int | None = None,
    document_type: str | None = None,
) -> PdfDocumentInput:
    if file_hash is None:
        if content is None and file_path:
            file_hash = _sha1_bytes(Path(file_path).read_bytes())
        else:
            file_hash = _sha1_bytes(content or b"")
    if file_path is None and content is not None:
        file_path = str(_stage_bytes(content, filename=name, digest=file_hash, subdir="direct"))
    return PdfDocumentInput(
        document_id=_stable_document_id(name, file_hash, index),
        document_name=Path(name).name,
        document_type=document_type or classify_document_type(name),
        source_path=source_path or name,
        file_path=file_path or "",
        file_hash=file_hash,
        compressed_size=int(compressed_size if compressed_size is not None else len(content or b"")),
        uncompressed_size=int(uncompressed_size if uncompressed_size is not None else len(content or b"")),
        content=content if file_path is None else None,
    )


def inspect_uploaded_package(uploaded_files: list[Any] | Any) -> PackageInspectionResult:
    if uploaded_files is None:
        return PackageInspectionResult(candidates=[], warnings=[], temp_dir=str(_temp_root()))
    if not isinstance(uploaded_files, list):
        uploaded_files = [uploaded_files]

    candidates: list[PdfCandidate] = []
    warnings: list[str] = []
    total_upload_size = 0
    for upload in uploaded_files:
        name = _upload_name(upload)
        suffix = Path(name).suffix.lower()
        try:
            content = _upload_bytes(upload)
        except Exception as exc:
            warnings.append(f"Skipped {name}: could not read upload ({type(exc).__name__})")
            continue
        total_upload_size += len(content)

        if suffix == ".pdf":
            file_hash = _sha1_bytes(content)
            file_path = _stage_bytes(content, filename=name, digest=file_hash, subdir="direct")
            document_type = classify_document_type(name)
            source_path = name
            candidates.append(
                PdfCandidate(
                    candidate_id=_candidate_id(source_path, file_hash, len(candidates)),
                    document_name=Path(name).name,
                    document_type=document_type,
                    source_kind="pdf",
                    source_path=source_path,
                    compressed_size=len(content),
                    uncompressed_size=len(content),
                    default_selected=guess_default_selected(name, document_type),
                    file_hash=file_hash,
                    file_path=str(file_path),
                )
            )
        elif suffix == ".zip":
            zip_hash = _sha1_bytes(content)
            zip_path = _temp_root() / "zips" / f"{_safe_stem(name)}-{zip_hash[:12]}.zip"
            zip_path.parent.mkdir(parents=True, exist_ok=True)
            if not zip_path.exists() or zip_path.stat().st_size != len(content):
                zip_path.write_bytes(content)
            member_rows, zip_warnings = inspect_zip_pdfs(zip_path)
            warnings.extend(zip_warnings)
            for member in member_rows:
                member_name = str(member["member_name"])
                filename = str(member["filename"])
                file_hash = hashlib.sha1(
                    f"{zip_hash}\0{member_name}\0{member.get('crc')}\0{member.get('uncompressed_size')}".encode("utf-8")
                ).hexdigest()
                document_type = classify_document_type(filename)
                source_path = f"{name}:{member_name}"
                candidates.append(
                    PdfCandidate(
                        candidate_id=_candidate_id(source_path, file_hash, len(candidates)),
                        document_name=filename,
                        document_type=document_type,
                        source_kind="zip",
                        source_path=source_path,
                        compressed_size=int(member["compressed_size"]),
                        uncompressed_size=int(member["uncompressed_size"]),
                        default_selected=guess_default_selected(member_name, document_type),
                        file_hash=file_hash,
                        zip_path=str(zip_path),
                        zip_member=member_name,
                    )
                )
        else:
            warnings.append(f"Skipped unsupported upload: {name}")

    if total_upload_size > PACKAGE_WARNING_BYTES:
        warnings.append(f"Total uploaded package size is {total_upload_size / MB:,.0f} MB; select only needed PDFs before analysis.")
    return PackageInspectionResult(
        candidates=candidates,
        warnings=warnings,
        temp_dir=str(_temp_root()),
        total_upload_size=total_upload_size,
    )


def materialize_selected_documents(
    inspection: PackageInspectionResult,
    selected_candidate_ids: set[str] | list[str] | None = None,
) -> PackageIngestResult:
    selected_ids = set(selected_candidate_ids or [])
    candidates = [candidate for candidate in inspection.candidates if not selected_ids or candidate.candidate_id in selected_ids]
    warnings = list(inspection.warnings)
    selected_uncompressed = sum(candidate.uncompressed_size for candidate in candidates)
    if selected_uncompressed > SELECTED_WARNING_BYTES:
        warnings.append(f"Selected PDFs total {selected_uncompressed / GB:,.2f} GB, which may be slow to analyze.")

    disk = shutil.disk_usage(_temp_root())
    if selected_uncompressed > disk.free * DISK_SAFETY_FRACTION:
        warnings.append(
            f"Selected ZIP contents may exceed the disk safety threshold: {selected_uncompressed / GB:,.2f} GB selected, "
            f"{disk.free / GB:,.2f} GB free."
        )

    documents: list[PdfDocumentInput] = []
    for candidate in candidates:
        if candidate.source_kind == "zip":
            if candidate.file_path and Path(candidate.file_path).exists():
                file_path = candidate.file_path
                file_hash = candidate.file_hash
            else:
                target = _temp_root() / "extracted" / f"{_safe_stem(candidate.document_name)}-{candidate.file_hash[:12]}.pdf"
                try:
                    content = extract_zip_pdf_member(Path(candidate.zip_path), candidate.zip_member, target)
                    actual_hash = _sha1_bytes(content)
                    file_path = str(target)
                    file_hash = actual_hash
                except Exception as exc:
                    warnings.append(f"Skipped {candidate.source_path}: could not extract selected PDF ({type(exc).__name__}: {exc})")
                    continue
        else:
            file_path = candidate.file_path
            file_hash = candidate.file_hash

        documents.append(
            normalize_pdf_document(
                candidate.document_name,
                index=len(documents),
                source_path=candidate.source_path,
                file_path=file_path,
                file_hash=file_hash,
                compressed_size=candidate.compressed_size,
                uncompressed_size=candidate.uncompressed_size,
                document_type=candidate.document_type,
            )
        )
    return PackageIngestResult(documents=documents, warnings=warnings)


def ingest_uploaded_package(uploaded_files: list[Any] | Any) -> PackageIngestResult:
    """Compatibility wrapper: inspect uploads and materialize all PDFs.

    FoamScope keeps every discovered PDF in the lightweight global index by default;
    manual selection is an advanced override in the Streamlit UI.
    """
    inspection = triage_inspection(inspect_uploaded_package(uploaded_files))
    selected = {candidate.candidate_id for candidate in inspection.candidates}
    return materialize_selected_documents(inspection, selected)
