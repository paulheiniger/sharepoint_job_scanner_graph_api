from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from .graph_client import GraphClient, GraphError, SharePointTarget
from .job_search import first_nonblank, normalize_search_text, tokenize_search_text

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".xlsm", ".txt", ".csv"}
TEXT_EMPTY_THRESHOLD = 20


class DocumentAcquisitionError(RuntimeError):
    pass


@dataclass
class ExtractedContent:
    content_type: str
    source_locator: str
    text_content: str
    page_number: int | None = None
    sheet_name: str | None = None
    cell_range: str | None = None
    row_number: int | None = None
    section_name: str | None = None


@dataclass
class ExtractionResult:
    rows: list[ExtractedContent]
    extraction_method: str
    requires_ocr: bool = False


def text_or_none(value: Any) -> str | None:
    text_value = str(value or "").strip()
    return text_value or None


def safe_filename(value: Any) -> str:
    name = str(value or "document").strip() or "document"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:180]


def file_sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def content_id_for(document_id: str, row: ExtractedContent) -> str:
    key = "||".join(
        str(part or "")
        for part in [
            document_id,
            row.content_type,
            row.source_locator,
            row.page_number,
            row.sheet_name,
            row.cell_range,
            row.row_number,
            row.text_content,
        ]
    )
    return f"content-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:28]}"


def normalized_content(text_value: str) -> str:
    return normalize_search_text(text_value)


def stable_cache_path(document: dict[str, Any], cache_root: Path) -> Path:
    document_id = first_nonblank(document.get("document_id")) or "document"
    extension = first_nonblank(document.get("file_extension")) or Path(first_nonblank(document.get("file_name"))).suffix
    if extension and not extension.startswith("."):
        extension = "." + extension
    return cache_root / "_document_content_cache" / f"{safe_filename(document_id)}{extension.lower()}"


def is_nonempty_file(path: Path) -> bool:
    return path.exists() and path.is_file() and path.stat().st_size > 0


def looks_like_html_file(path: Path) -> bool:
    try:
        prefix = path.read_bytes()[:512].lstrip().lower()
    except OSError:
        return False
    return prefix.startswith(b"<!doctype html") or prefix.startswith(b"<html") or b"<title>sign in" in prefix


def find_existing_cached_file(document: dict[str, Any], cache_root: Path) -> Path | None:
    cached = text_or_none(document.get("cached_file_path"))
    if cached and is_nonempty_file(Path(cached)):
        return Path(cached)

    relative_path = text_or_none(document.get("relative_path"))
    if relative_path:
        direct = cache_root / relative_path.strip("/")
        if is_nonempty_file(direct):
            return direct
        relative_parts = Path(relative_path).parts
        file_name = Path(relative_path).name
    else:
        relative_parts = ()
        file_name = first_nonblank(document.get("file_name"))

    if not file_name:
        return None
    for candidate in cache_root.rglob(file_name):
        if not is_nonempty_file(candidate):
            continue
        if relative_parts and tuple(candidate.parts[-len(relative_parts) :]) != relative_parts:
            continue
        return candidate
    return None


def ensure_local_document(document: dict[str, Any], cache_root: Path, force_download: bool = False) -> Path:
    if not force_download:
        existing = find_existing_cached_file(document, cache_root)
        if existing:
            return existing

    drive_id = first_nonblank(document.get("drive_id"))
    drive_item_id = first_nonblank(document.get("drive_item_id"))
    if not drive_id or not drive_item_id:
        raise DocumentAcquisitionError("No cached file found and document row does not include drive_id plus drive_item_id for download.")

    destination = stable_cache_path(document, cache_root)
    try:
        GraphClient(max_retries=2).download_item(drive_id, drive_item_id, destination)
    except GraphError as exc:
        raise DocumentAcquisitionError(str(exc)) from exc
    if not is_nonempty_file(destination):
        raise DocumentAcquisitionError("Downloaded file is missing or empty.")
    if looks_like_html_file(destination):
        try:
            destination.unlink()
        except OSError:
            pass
        raise DocumentAcquisitionError("Downloaded file appears to be HTML, not document content.")
    return destination


def planned_acquisition_method(document: dict[str, Any], cache_root: Path, force_download: bool = False) -> str:
    if not force_download and find_existing_cached_file(document, cache_root):
        return "cached_file"
    if first_nonblank(document.get("drive_id")) and first_nonblank(document.get("drive_item_id")):
        return "graph_content_download"
    return "unavailable"


def manifest_metadata_rows(cache_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for manifest_path in cache_root.rglob(".jobscan_manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, dict):
            continue
        manifest_drive_id = manifest.get("drive_id")
        docs = manifest.get("documents") if isinstance(manifest.get("documents"), list) else []
        items = manifest.get("items") if isinstance(manifest.get("items"), dict) else {}
        for item_id, item in items.items():
            if not isinstance(item, dict):
                continue
            parent = item.get("parentReference") if isinstance(item.get("parentReference"), dict) else {}
            rows.append(
                {
                    "drive_id": item.get("drive_id") or parent.get("driveId") or manifest_drive_id,
                    "drive_item_id": item.get("drive_item_id") or item.get("graph_item_id") or item.get("id") or item_id,
                    "sharepoint_url": item.get("webUrl") or item.get("web_url"),
                    "relative_path": item.get("relative_path") or item.get("local_path"),
                    "file_name": item.get("name"),
                }
            )
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            parent = doc.get("parentReference") if isinstance(doc.get("parentReference"), dict) else {}
            rows.append(
                {
                    "drive_id": doc.get("drive_id") or parent.get("driveId") or manifest_drive_id,
                    "drive_item_id": doc.get("drive_item_id") or doc.get("graph_item_id") or doc.get("id"),
                    "sharepoint_url": doc.get("webUrl") or doc.get("web_url") or doc.get("sharepoint_url"),
                    "relative_path": doc.get("relative_path") or doc.get("local_path"),
                    "file_name": doc.get("name") or doc.get("file_name"),
                }
            )
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for row in rows:
        if not row.get("drive_id") or not row.get("drive_item_id"):
            continue
        key = (
            str(row.get("drive_id") or ""),
            str(row.get("drive_item_id") or ""),
            str(row.get("sharepoint_url") or ""),
            str(row.get("relative_path") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def backfill_document_drive_metadata(connection: Connection, cache_root: Path, *, limit: int | None = None, job_id: str | None = None) -> int:
    candidates = manifest_metadata_rows(cache_root)
    if limit is not None:
        candidates = candidates[: max(limit, 0)]
    updated = 0
    for candidate in candidates:
        params = {
            "drive_id": candidate.get("drive_id"),
            "drive_item_id": candidate.get("drive_item_id"),
            "sharepoint_url": candidate.get("sharepoint_url"),
            "relative_path": candidate.get("relative_path"),
            "file_name": candidate.get("file_name"),
            "job_id": job_id,
        }
        result = connection.execute(
            text(
                """
                UPDATE documents
                SET drive_id = COALESCE(NULLIF(drive_id, ''), :drive_id),
                    drive_item_id = COALESCE(NULLIF(drive_item_id, ''), :drive_item_id),
                    updated_at = NOW()
                WHERE (:job_id IS NULL OR job_id = :job_id)
                  AND (drive_id IS NULL OR drive_id = '' OR drive_item_id IS NULL OR drive_item_id = '')
                  AND (
                    (sharepoint_url IS NOT NULL AND sharepoint_url <> '' AND sharepoint_url = :sharepoint_url)
                    OR (relative_path IS NOT NULL AND relative_path <> '' AND relative_path = :relative_path)
                  )
                """
            ),
            params,
        )
        updated += int(result.rowcount or 0)
    return updated


def resolve_graph_metadata_for_document(
    client: GraphClient,
    document: dict[str, Any],
    *,
    site_url: str,
    library: str,
    root_folder: str = "",
) -> dict[str, Any]:
    target = SharePointTarget.from_url(site_url, library=library, folder_path=root_folder)
    site = client.get_site(target.hostname, target.site_path)
    drive = client.get_drive_by_name(site["id"], library)
    relative_path = first_nonblank(document.get("relative_path"))
    file_name = first_nonblank(document.get("file_name"))
    if not relative_path and not file_name:
        raise DocumentAcquisitionError("Document row has no relative_path or file_name for Graph path resolution.")
    path_parts = [part.strip("/") for part in [root_folder, relative_path or file_name] if part and part.strip("/")]
    graph_path = "/".join(path_parts)
    item = client.get_root_or_path_item(drive["id"], graph_path)
    return {
        "document_id": document.get("document_id"),
        "drive_id": drive["id"],
        "drive_item_id": item.get("id"),
        "sharepoint_url": item.get("webUrl") or document.get("sharepoint_url"),
        "file_name": item.get("name") or document.get("file_name"),
    }


def update_document_drive_metadata(connection: Connection, metadata: dict[str, Any]) -> int:
    result = connection.execute(
        text(
            """
            UPDATE documents
            SET drive_id = :drive_id,
                drive_item_id = :drive_item_id,
                sharepoint_url = COALESCE(NULLIF(sharepoint_url, ''), :sharepoint_url),
                updated_at = NOW()
            WHERE document_id = :document_id
            """
        ),
        metadata,
    )
    return int(result.rowcount or 0)


def extract_pdf(path: Path) -> ExtractionResult:
    try:
        try:
            from pypdf import PdfReader
        except ImportError:
            from PyPDF2 import PdfReader  # type: ignore[no-redef]
    except ImportError as exc:
        raise RuntimeError("Install pypdf to extract PDF text: pip install pypdf") from exc

    reader = PdfReader(str(path))
    rows: list[ExtractedContent] = []
    total_chars = 0
    for index, page in enumerate(reader.pages, start=1):
        page_text = (page.extract_text() or "").strip()
        if not page_text:
            continue
        total_chars += len(page_text)
        rows.append(
            ExtractedContent(
                content_type="pdf_page",
                source_locator=f"page {index}",
                page_number=index,
                text_content=page_text,
            )
        )
    return ExtractionResult(rows=rows, extraction_method="pypdf", requires_ocr=total_chars < TEXT_EMPTY_THRESHOLD)


def _docx_text(node: ET.Element, ns: dict[str, str]) -> str:
    return "".join(text_node.text or "" for text_node in node.findall(".//w:t", ns)).strip()


def _docx_paragraph_style(node: ET.Element, ns: dict[str, str]) -> str:
    style = node.find("./w:pPr/w:pStyle", ns)
    if style is None:
        return ""
    return style.attrib.get(f"{{{ns['w']}}}val", "")


def extract_docx(path: Path) -> ExtractionResult:
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with zipfile.ZipFile(path) as package:
        xml = package.read("word/document.xml")
    root = ET.fromstring(xml)
    body = root.find("w:body", ns)
    if body is None:
        return ExtractionResult(rows=[], extraction_method="docx-xml")

    rows: list[ExtractedContent] = []
    current_section: str | None = None
    paragraph_count = 0
    table_count = 0
    for child in list(body):
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            content = _docx_text(child, ns)
            if not content:
                continue
            paragraph_count += 1
            style = _docx_paragraph_style(child, ns)
            if style.lower().startswith("heading"):
                current_section = content
                content_type = "docx_heading"
            else:
                content_type = "docx_paragraph"
            rows.append(
                ExtractedContent(
                    content_type=content_type,
                    source_locator=f"paragraph {paragraph_count}",
                    section_name=current_section,
                    text_content=content,
                )
            )
        elif tag == "tbl":
            table_count += 1
            for row_number, row in enumerate(child.findall(".//w:tr", ns), start=1):
                cell_values = [_docx_text(cell, ns) for cell in row.findall("./w:tc", ns)]
                cell_values = [value for value in cell_values if value]
                if not cell_values:
                    continue
                rows.append(
                    ExtractedContent(
                        content_type="docx_table_row",
                        source_locator=f"table {table_count} row {row_number}",
                        row_number=row_number,
                        section_name=current_section,
                        text_content=" | ".join(cell_values),
                    )
                )
    return ExtractionResult(rows=rows, extraction_method="docx-xml")


def extract_xlsx(path: Path) -> ExtractionResult:
    try:
        import openpyxl
        from openpyxl.utils import get_column_letter
    except ImportError as exc:
        raise RuntimeError("Install openpyxl to extract Excel content: pip install openpyxl") from exc

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    rows: list[ExtractedContent] = []
    for ws in wb.worksheets:
        if getattr(ws, "sheet_state", "visible") != "visible":
            continue
        for row in ws.iter_rows():
            values: list[tuple[int, str]] = []
            row_number = row[0].row if row else None
            for cell in row:
                value = text_or_none(cell.value)
                if value:
                    values.append((cell.column, value))
            if not values or row_number is None:
                continue
            start_col = get_column_letter(values[0][0])
            end_col = get_column_letter(values[-1][0])
            cell_range = f"{start_col}{row_number}:{end_col}{row_number}"
            text_content = " | ".join(f"{get_column_letter(col)}{row_number}: {value}" for col, value in values)
            rows.append(
                ExtractedContent(
                    content_type="xlsx_row",
                    source_locator=f"{ws.title}!{cell_range}",
                    sheet_name=ws.title,
                    cell_range=cell_range,
                    row_number=row_number,
                    text_content=text_content,
                )
            )
    return ExtractionResult(rows=rows, extraction_method="openpyxl")


def extract_text_file(path: Path) -> ExtractionResult:
    text_content = path.read_text(encoding="utf-8", errors="replace").strip()
    rows = [ExtractedContent(content_type="text_file", source_locator="file", text_content=text_content)] if text_content else []
    return ExtractionResult(rows=rows, extraction_method="plain-text")


def extract_document_file(path: Path, document: dict[str, Any]) -> ExtractionResult:
    extension = (first_nonblank(document.get("file_extension")) or path.suffix).lower()
    if extension == ".pdf":
        return extract_pdf(path)
    if extension == ".docx":
        return extract_docx(path)
    if extension in {".xlsx", ".xlsm"}:
        return extract_xlsx(path)
    if extension in {".txt", ".csv"}:
        return extract_text_file(path)
    raise RuntimeError(f"Unsupported document type for extraction: {extension or path.suffix or 'unknown'}")


def document_content_table_available(connection: Connection) -> bool:
    return bool(
        connection.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = 'document_content'
                )
                """
            )
        ).scalar()
    )


def documents_table_available(connection: Connection) -> bool:
    try:
        return bool(
            connection.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = 'public' AND table_name = 'documents'
                    )
                    """
                )
            ).scalar()
        )
    except SQLAlchemyError:
        try:
            connection.execute(text("SELECT 1 FROM documents LIMIT 1"))
            return True
        except SQLAlchemyError:
            return False


def update_document_failure(connection: Connection, document_id: str, error: str, cached_file_path: str | None = None) -> None:
    connection.execute(
        text(
            """
            UPDATE documents
            SET extraction_status = 'failed',
                extraction_error = :error,
                cached_file_path = COALESCE(:cached_file_path, cached_file_path),
                updated_at = NOW()
            WHERE document_id = :document_id
            """
        ),
        {"document_id": document_id, "error": error[:1000], "cached_file_path": cached_file_path},
    )


def should_skip_extraction(document: dict[str, Any], current_hash: str, force: bool = False) -> bool:
    if force:
        return False
    status = first_nonblank(document.get("extraction_status"))
    previous_hash = first_nonblank(document.get("content_hash"))
    return bool(previous_hash and previous_hash == current_hash and status in {"succeeded", "failed", "ocr_required"})


def replace_document_content(
    connection: Connection,
    document: dict[str, Any],
    path: Path,
    result: ExtractionResult,
    current_hash: str,
) -> int:
    document_id = first_nonblank(document.get("document_id"))
    job_id = text_or_none(document.get("job_id"))
    now = datetime.now(timezone.utc)
    connection.execute(text("DELETE FROM document_content WHERE document_id = :document_id"), {"document_id": document_id})
    for row in result.rows:
        connection.execute(
            text(
                """
                INSERT INTO document_content (
                    content_id, document_id, job_id, content_type, source_locator, page_number,
                    sheet_name, cell_range, row_number, section_name, text_content, normalized_text,
                    extraction_method, content_hash, created_at, updated_at
                )
                VALUES (
                    :content_id, :document_id, :job_id, :content_type, :source_locator, :page_number,
                    :sheet_name, :cell_range, :row_number, :section_name, :text_content, :normalized_text,
                    :extraction_method, :content_hash, :created_at, :updated_at
                )
                ON CONFLICT (content_id) DO UPDATE SET
                    text_content = EXCLUDED.text_content,
                    normalized_text = EXCLUDED.normalized_text,
                    updated_at = EXCLUDED.updated_at
                """
            ),
            {
                "content_id": content_id_for(document_id, row),
                "document_id": document_id,
                "job_id": job_id,
                "content_type": row.content_type,
                "source_locator": row.source_locator,
                "page_number": row.page_number,
                "sheet_name": row.sheet_name,
                "cell_range": row.cell_range,
                "row_number": row.row_number,
                "section_name": row.section_name,
                "text_content": row.text_content,
                "normalized_text": normalized_content(row.text_content),
                "extraction_method": result.extraction_method,
                "content_hash": current_hash,
                "created_at": now,
                "updated_at": now,
            },
        )
    connection.execute(
        text(
            """
            UPDATE documents
            SET extraction_status = 'succeeded',
                extraction_method = :extraction_method,
                extraction_error = NULL,
                extracted_at = :extracted_at,
                content_hash = :content_hash,
                cached_file_path = :cached_file_path,
                requires_ocr = :requires_ocr,
                updated_at = NOW()
            WHERE document_id = :document_id
            """
        ),
        {
            "document_id": document_id,
            "extraction_method": result.extraction_method,
            "extracted_at": now,
            "content_hash": current_hash,
            "cached_file_path": str(path),
            "requires_ocr": bool(result.requires_ocr),
        },
    )
    return len(result.rows)


def mark_ocr_required(connection: Connection, document: dict[str, Any], path: Path, current_hash: str, result: ExtractionResult) -> None:
    connection.execute(
        text(
            """
            UPDATE documents
            SET extraction_status = 'ocr_required',
                extraction_method = :extraction_method,
                extraction_error = 'No extractable text found; OCR is required.',
                extracted_at = :extracted_at,
                content_hash = :content_hash,
                cached_file_path = :cached_file_path,
                requires_ocr = TRUE,
                updated_at = NOW()
            WHERE document_id = :document_id
            """
        ),
        {
            "document_id": document["document_id"],
            "extraction_method": result.extraction_method,
            "extracted_at": datetime.now(timezone.utc),
            "content_hash": current_hash,
            "cached_file_path": str(path),
        },
    )


def extract_one_document(connection: Connection, document: dict[str, Any], cache_root: Path, *, force: bool = False) -> tuple[str, int]:
    document_id = first_nonblank(document.get("document_id"))
    if not document_id:
        raise RuntimeError("Document row is missing document_id.")
    try:
        path = ensure_local_document(document, cache_root, force_download=force)
        current_hash = file_sha1(path)
        if should_skip_extraction(document, current_hash, force=force):
            return "skipped", 0
        result = extract_document_file(path, document)
        if result.requires_ocr and not result.rows:
            mark_ocr_required(connection, document, path, current_hash, result)
            return "ocr_required", 0
        row_count = replace_document_content(connection, document, path, result, current_hash)
        return "extracted", row_count
    except Exception as exc:
        update_document_failure(connection, document_id, str(exc))
        return "failed", 0


def document_selection_sql(*, document_id: str | None, job_id: str | None, pending: bool, document_type: str | None) -> tuple[str, dict[str, Any]]:
    where: list[str] = []
    params: dict[str, Any] = {}
    if document_id:
        where.append("document_id = :document_id")
        params["document_id"] = document_id
    if job_id:
        where.append("job_id = :job_id")
        params["job_id"] = job_id
    if document_type:
        where.append("document_type = :document_type")
        params["document_type"] = document_type
    if pending:
        where.append("(extraction_status IS NULL OR extraction_status IN ('not_started', 'pending'))")
    where.append("LOWER(COALESCE(file_extension, '')) = ANY(:extensions)")
    params["extensions"] = sorted(SUPPORTED_EXTENSIONS)
    where_sql = "WHERE " + " AND ".join(where) if where else ""
    return where_sql, params


def load_documents_for_extraction(
    connection: Connection,
    *,
    document_id: str | None = None,
    job_id: str | None = None,
    pending: bool = False,
    document_type: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    where_sql, params = document_selection_sql(
        document_id=document_id,
        job_id=job_id,
        pending=pending,
        document_type=document_type,
    )
    params["limit"] = limit
    sql = f"""
        SELECT *
        FROM documents
        {where_sql}
        ORDER BY updated_at NULLS FIRST, file_name
        LIMIT :limit
    """
    return [dict(row) for row in connection.execute(text(sql), params).mappings().all()]


def list_document_content(connection: Connection | Engine, document_id: str) -> list[dict[str, Any]]:
    manager = connection.connect() if isinstance(connection, Engine) else None
    conn = manager.__enter__() if manager else connection
    try:
        if not document_content_table_available(conn):
            return []
        rows = conn.execute(
            text(
                """
                SELECT *
                FROM document_content
                WHERE document_id = :document_id
                ORDER BY page_number NULLS LAST, sheet_name NULLS LAST, row_number NULLS LAST, source_locator
                """
            ),
            {"document_id": document_id},
        ).mappings()
        return [dict(row) for row in rows]
    finally:
        if manager:
            manager.__exit__(None, None, None)


def excerpt(text_value: str, tokens: list[str], width: int = 240) -> str:
    text_value = " ".join(str(text_value or "").split())
    if not text_value:
        return ""
    normalized = normalize_search_text(text_value)
    position = -1
    for token in tokens:
        position = normalized.find(token.lower())
        if position >= 0:
            break
    if position < 0:
        return text_value[:width]
    start = max(0, position - width // 3)
    return text_value[start : start + width]


def search_extracted_text(
    connection: Connection | Engine,
    query: str,
    job_id: str | None = None,
    document_type: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    manager = connection.connect() if isinstance(connection, Engine) else None
    conn = manager.__enter__() if manager else connection
    try:
        if not document_content_table_available(conn):
            return []
        tokens = tokenize_search_text(query)
        params: dict[str, Any] = {"limit": limit}
        where: list[str] = []
        if job_id:
            where.append("c.job_id = :job_id")
            params["job_id"] = str(job_id)
        if document_type and document_type != "all":
            where.append("d.document_type = :document_type")
            params["document_type"] = document_type
        for index, token in enumerate(tokens):
            key = f"token_{index}"
            where.append("c.normalized_text LIKE :" + key)
            params[key] = f"%{token.lower()}%"
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        rows = conn.execute(
            text(
                f"""
                SELECT c.document_id, c.job_id, d.file_name, d.document_type, d.sharepoint_url,
                       c.content_type, c.source_locator, c.page_number, c.sheet_name, c.row_number,
                       c.text_content
                FROM document_content c
                JOIN documents d ON d.document_id = c.document_id
                {where_sql}
                ORDER BY d.file_name, c.page_number NULLS LAST, c.sheet_name NULLS LAST, c.row_number NULLS LAST
                LIMIT :limit
                """
            ),
            params,
        ).mappings()
        return [{**dict(row), "excerpt": excerpt(str(row.get("text_content") or ""), tokens)} for row in rows]
    finally:
        if manager:
            manager.__exit__(None, None, None)


def print_status(connection: Connection) -> None:
    if not documents_table_available(connection):
        print("Documents table not found. Apply db/add_documents_table.sql first.")
        return
    rows = connection.execute(
        text(
            """
            SELECT COALESCE(extraction_status, 'not_started') AS status, COUNT(*) AS count
            FROM documents
            GROUP BY COALESCE(extraction_status, 'not_started')
            ORDER BY status
            """
        )
    ).fetchall()
    print("Document extraction status:")
    for status, count in rows:
        print(f"  {status}: {count}")


def print_identifier_status(connection: Connection) -> None:
    if not documents_table_available(connection):
        print("Documents table not found. Apply db/add_documents_table.sql first.")
        return
    row = connection.execute(
        text(
            """
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE drive_id IS NOT NULL AND drive_id <> '' AND drive_item_id IS NOT NULL AND drive_item_id <> '') AS with_identifiers,
                COUNT(*) FILTER (WHERE drive_id IS NULL OR drive_id = '' OR drive_item_id IS NULL OR drive_item_id = '') AS missing_identifiers
            FROM documents
            """
        )
    ).mappings().one()
    print("Document acquisition identifiers:")
    print(f"  documents_total: {row['total']}")
    print(f"  documents_with_drive_identifiers: {row['with_identifiers']}")
    print(f"  documents_missing_drive_identifiers: {row['missing_identifiers']}")


def load_documents_missing_drive_metadata(
    connection: Connection,
    *,
    job_id: str | None = None,
    limit: int = 10,
    document_type: str | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"limit": limit}
    where = ["(drive_id IS NULL OR drive_id = '' OR drive_item_id IS NULL OR drive_item_id = '')"]
    if job_id:
        where.append("job_id = :job_id")
        params["job_id"] = job_id
    if document_type:
        where.append("document_type = :document_type")
        params["document_type"] = document_type
    sql = f"""
        SELECT *
        FROM documents
        WHERE {' AND '.join(where)}
        ORDER BY updated_at NULLS FIRST, file_name
        LIMIT :limit
    """
    return [dict(row) for row in connection.execute(text(sql), params).mappings().all()]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Extract source-aware searchable text from indexed Spray-Tec documents.")
    parser.add_argument("--document-id")
    parser.add_argument("--job-id")
    parser.add_argument("--pending", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--identifier-status", action="store_true", help="Show document rows with and without Graph drive identifiers.")
    parser.add_argument("--backfill-metadata", action="store_true", help="Backfill drive identifiers from cached SharePoint manifests.")
    parser.add_argument("--resolve-metadata", action="store_true", help="Resolve missing drive identifiers from Graph by site/library/path without downloading content.")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--document-type")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--cache-root", type=Path, default=Path(".cache/sharepoint"))
    parser.add_argument("--site-url", help="SharePoint site URL for --resolve-metadata.")
    parser.add_argument("--library", default="Documents", help="SharePoint library name for --resolve-metadata.")
    parser.add_argument("--root-folder", default="", help="Optional library-relative root folder prepended to document relative_path for --resolve-metadata.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL"))
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.database_url:
        raise SystemExit("Set --database-url, DATABASE_URL, or NEON_DATABASE_URL.")
    engine = create_engine(args.database_url, future=True)
    if args.status:
        with engine.connect() as conn:
            print_status(conn)
        return 0
    if args.identifier_status:
        with engine.connect() as conn:
            print_identifier_status(conn)
        return 0
    if args.backfill_metadata:
        with engine.begin() as conn:
            updated = backfill_document_drive_metadata(conn, args.cache_root, limit=args.limit, job_id=args.job_id)
        print(f"Drive identifiers backfilled from cached manifests: {updated}")
        return 0
    if args.resolve_metadata:
        if not args.site_url:
            raise SystemExit("--resolve-metadata requires --site-url.")
        client = GraphClient(max_retries=2)
        with engine.connect() as conn:
            documents = load_documents_missing_drive_metadata(
                conn,
                job_id=args.job_id,
                limit=args.limit,
                document_type=args.document_type,
            )
        resolved = 0
        failed = 0
        total = len(documents)
        for index, document in enumerate(documents, start=1):
            label = first_nonblank(document.get("file_name"), document.get("document_id"))
            try:
                metadata = resolve_graph_metadata_for_document(
                    client,
                    document,
                    site_url=args.site_url,
                    library=args.library,
                    root_folder=args.root_folder,
                )
                with engine.begin() as conn:
                    resolved += update_document_drive_metadata(conn, metadata)
                print(f"[{index}/{total}] {label} — identifiers resolved")
            except Exception as exc:
                failed += 1
                print(f"[{index}/{total}] {label} — metadata resolution failed: {str(exc)[:240]}")
        print(f"Identifiers resolved: {resolved}")
        print(f"Identifier resolution failures: {failed}")
        return 0
    if not any([args.document_id, args.job_id, args.pending]):
        raise SystemExit("Choose --document-id, --job-id, --pending, or --status.")

    with engine.connect() as conn:
        documents = load_documents_for_extraction(
            conn,
            document_id=args.document_id,
            job_id=args.job_id,
            pending=args.pending,
            document_type=args.document_type,
            limit=args.limit,
        )
    total = len(documents)
    for index, document in enumerate(documents, start=1):
        with engine.begin() as conn:
            status, count = extract_one_document(conn, document, args.cache_root, force=args.force)
        label = first_nonblank(document.get("file_name"), document.get("document_id"))
        acquisition_method = planned_acquisition_method(document, args.cache_root, force_download=args.force)
        print(f"[{index}/{total}] {label} — {status} {count} content rows — acquisition: {acquisition_method}")
        if args.debug:
            print(f"  document_id: {document.get('document_id')}")
            print(f"  sharepoint_url: {document.get('sharepoint_url') or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
