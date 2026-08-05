from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from jobscan.document_extraction import (
    SUPPORTED_EXTENSIONS,
    ExtractionResult,
    extract_document_file,
)
from jobscan.graph_client import GraphClient, GraphError
from jobscan.job_search import tokenize_search_text


MAX_LIVE_DOWNLOAD_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_CONTENT_CHARS = 40_000


class SharePointDocumentUnavailableError(RuntimeError):
    pass


class SharePointDocumentNotFoundError(LookupError):
    pass


def _database_engine(database_url: str | None, engine: Engine | None) -> Engine:
    if engine is not None:
        return engine
    if not database_url:
        raise SharePointDocumentUnavailableError(
            "A database URL is required for SharePoint document intelligence."
        )
    return create_engine(database_url, future=True)


def _require_document_relations(engine: Engine) -> None:
    inspector = inspect(engine)
    missing = [
        relation
        for relation in ("documents", "document_content")
        if not inspector.has_table(relation)
    ]
    if missing:
        raise SharePointDocumentUnavailableError(
            "Required SharePoint document relations are unavailable: "
            + ", ".join(missing)
        )


def _document_select() -> str:
    return """
        d.document_id, d.job_id, d.document_type, d.file_name,
        d.sharepoint_url, d.folder_path, d.relative_path, d.mime_type,
        d.file_extension, d.size_bytes, d.modified_at, d.source_year,
        d.source_division, d.drive_id, d.drive_item_id,
        d.extraction_status, d.extraction_method, d.extraction_error,
        d.extracted_at, d.requires_ocr
    """


def _base_document_result(row: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        key: row.get(key)
        for key in (
            "document_id",
            "job_id",
            "document_type",
            "file_name",
            "sharepoint_url",
            "folder_path",
            "relative_path",
            "mime_type",
            "file_extension",
            "size_bytes",
            "modified_at",
            "source_year",
            "source_division",
            "extraction_status",
            "extraction_method",
            "extracted_at",
            "requires_ocr",
        )
    }
    for key in ("modified_at", "extracted_at"):
        value = result.get(key)
        if value is not None and hasattr(value, "isoformat"):
            result[key] = value.isoformat()
    return result


def search_sharepoint_documents(
    *,
    database_url: str | None,
    query: str,
    job_id: str = "",
    document_type: str = "",
    limit: int = 10,
    engine: Engine | None = None,
) -> dict[str, Any]:
    resolved_engine = _database_engine(database_url, engine)
    _require_document_relations(resolved_engine)
    tokens = tokenize_search_text(query)
    if not tokens:
        raise ValueError("A searchable word or identifier is required.")

    params: dict[str, Any] = {
        "metadata_limit": limit,
        "content_limit": min(limit * 4, 80),
    }
    document_filters = ["d.deleted_at IS NULL"]
    content_filters = ["d.deleted_at IS NULL"]
    if job_id.strip():
        document_filters.append("d.job_id = :job_id")
        content_filters.append("d.job_id = :job_id")
        params["job_id"] = job_id.strip()
    if document_type.strip() and document_type.strip().lower() != "all":
        document_filters.append("d.document_type = :document_type")
        content_filters.append("d.document_type = :document_type")
        params["document_type"] = document_type.strip().lower()
    for index, token in enumerate(tokens):
        name = f"token_{index}"
        params[name] = f"%{token.lower()}%"
        document_filters.append(
            "(LOWER(COALESCE(d.file_name, '')) LIKE :"
            + name
            + " OR LOWER(COALESCE(d.relative_path, '')) LIKE :"
            + name
            + " OR LOWER(COALESCE(d.folder_path, '')) LIKE :"
            + name
            + ")"
        )
        content_filters.append(
            "LOWER(COALESCE(c.normalized_text, c.text_content, '')) LIKE :" + name
        )

    metadata_sql = text(
        f"""
        SELECT {_document_select()}
        FROM documents d
        WHERE {' AND '.join(document_filters)}
        ORDER BY d.modified_at DESC NULLS LAST, d.file_name
        LIMIT :metadata_limit
        """
    )
    content_sql = text(
        f"""
        SELECT {_document_select()}, c.content_type, c.source_locator,
               c.page_number, c.sheet_name, c.row_number, c.text_content
        FROM document_content c
        JOIN documents d ON d.document_id = c.document_id
        WHERE {' AND '.join(content_filters)}
        ORDER BY d.modified_at DESC NULLS LAST, d.file_name,
                 c.page_number NULLS LAST, c.sheet_name NULLS LAST,
                 c.row_number NULLS LAST
        LIMIT :content_limit
        """
    )

    try:
        with resolved_engine.connect() as connection:
            metadata_rows = [
                dict(row)
                for row in connection.execute(metadata_sql, params).mappings().all()
            ]
            content_rows = [
                dict(row)
                for row in connection.execute(content_sql, params).mappings().all()
            ]
    except Exception as exc:
        raise SharePointDocumentUnavailableError(
            f"SharePoint document search failed ({type(exc).__name__})."
        ) from exc

    by_document: dict[str, dict[str, Any]] = {}
    ordered_ids: list[str] = []
    for row in content_rows:
        document_id = str(row.get("document_id") or "")
        if not document_id:
            continue
        if document_id not in by_document:
            result = _base_document_result(row)
            result.update(
                {
                    "match_sources": ["extracted_content"],
                    "content_matches": [],
                    "text_available": True,
                    "graph_download_available": bool(
                        row.get("drive_id") and row.get("drive_item_id")
                    ),
                }
            )
            by_document[document_id] = result
            ordered_ids.append(document_id)
        matches = by_document[document_id]["content_matches"]
        if len(matches) < 3:
            matches.append(
                {
                    "source_locator": str(row.get("source_locator") or ""),
                    "page_number": row.get("page_number"),
                    "sheet_name": str(row.get("sheet_name") or ""),
                    "row_number": row.get("row_number"),
                    "excerpt": _excerpt(str(row.get("text_content") or ""), tokens),
                }
            )

    for row in metadata_rows:
        document_id = str(row.get("document_id") or "")
        if not document_id:
            continue
        if document_id in by_document:
            by_document[document_id]["match_sources"].append("metadata")
            continue
        result = _base_document_result(row)
        result.update(
            {
                "match_sources": ["metadata"],
                "content_matches": [],
                "text_available": str(row.get("extraction_status") or "").lower()
                == "completed",
                "graph_download_available": bool(
                    row.get("drive_id") and row.get("drive_item_id")
                ),
            }
        )
        by_document[document_id] = result
        ordered_ids.append(document_id)

    records = [by_document[document_id] for document_id in ordered_ids[:limit]]
    return {
        "schema_version": "spraytec.sharepoint_document_search.v1",
        "query": query.strip(),
        "filters_applied": {
            "job_id": job_id.strip(),
            "document_type": document_type.strip().lower(),
            "limit": limit,
        },
        "records": records,
        "coverage": {
            "records_returned": len(records),
            "metadata_matches_considered": len(metadata_rows),
            "content_rows_considered": len(content_rows),
            "truncated": len(ordered_ids) > limit,
            "source": "persisted_sharepoint_index_and_extracted_content",
        },
        "warnings": [
            "Search is bounded to SharePoint documents already discovered by the job scanner."
        ],
    }


def fetch_sharepoint_document(
    *,
    database_url: str | None,
    document_id: str,
    max_chars: int = DEFAULT_MAX_CONTENT_CHARS,
    allow_graph_download: bool = True,
    engine: Engine | None = None,
    graph_client_factory: Callable[[], GraphClient] | None = None,
    extractor: Callable[[Path, dict[str, Any]], ExtractionResult] = extract_document_file,
) -> dict[str, Any]:
    resolved_engine = _database_engine(database_url, engine)
    _require_document_relations(resolved_engine)
    try:
        with resolved_engine.connect() as connection:
            document = connection.execute(
                text(
                    f"""
                    SELECT {_document_select()}
                    FROM documents d
                    WHERE d.document_id = :document_id AND d.deleted_at IS NULL
                    """
                ),
                {"document_id": document_id},
            ).mappings().first()
            if document is None:
                raise SharePointDocumentNotFoundError(document_id)
            content_rows = [
                dict(row)
                for row in connection.execute(
                    text(
                        """
                        SELECT content_type, source_locator, page_number,
                               sheet_name, row_number, section_name, text_content
                        FROM document_content
                        WHERE document_id = :document_id
                        ORDER BY page_number NULLS LAST, sheet_name NULLS LAST,
                                 row_number NULLS LAST, source_locator
                        """
                    ),
                    {"document_id": document_id},
                ).mappings().all()
            ]
    except SharePointDocumentNotFoundError:
        raise
    except Exception as exc:
        raise SharePointDocumentUnavailableError(
            f"SharePoint document fetch failed ({type(exc).__name__})."
        ) from exc

    document_dict = dict(document)
    content_source = "persisted_extracted_content"
    warnings: list[str] = []
    extraction_method = str(document_dict.get("extraction_method") or "")
    requires_ocr = bool(document_dict.get("requires_ocr"))

    if not content_rows and allow_graph_download:
        live_rows, live_method, live_requires_ocr, live_warning = _download_and_extract(
            document_dict,
            graph_client_factory=graph_client_factory,
            extractor=extractor,
        )
        content_rows = live_rows
        if live_method:
            content_source = "live_graph_download"
            extraction_method = live_method
            requires_ocr = live_requires_ocr
        if live_warning:
            warnings.append(live_warning)
    elif not content_rows:
        warnings.append(
            "No extracted text is stored and live Graph download was not requested."
        )

    content, included_sections, truncated = _format_content_rows(
        content_rows,
        max_chars=max_chars,
    )
    if truncated:
        warnings.append(
            f"Document text was truncated to the requested {max_chars:,}-character limit."
        )
    if requires_ocr and not content:
        warnings.append("The document appears to require OCR before text can be returned.")
    if not content:
        warnings.append(
            "Document metadata and its SharePoint source link are available, but readable text is not."
        )

    result = _base_document_result(document_dict)
    result.update(
        {
            "schema_version": "spraytec.sharepoint_document_fetch.v1",
            "content": content,
            "content_source": content_source,
            "content_available": bool(content),
            "included_sections": included_sections,
            "total_sections": len(content_rows),
            "truncated": truncated,
            "extraction_method": extraction_method,
            "requires_ocr": requires_ocr,
            "warnings": warnings,
        }
    )
    return result


def _download_and_extract(
    document: dict[str, Any],
    *,
    graph_client_factory: Callable[[], GraphClient] | None,
    extractor: Callable[[Path, dict[str, Any]], ExtractionResult],
) -> tuple[list[dict[str, Any]], str, bool, str]:
    drive_id = str(document.get("drive_id") or "").strip()
    drive_item_id = str(document.get("drive_item_id") or "").strip()
    extension = str(document.get("file_extension") or "").strip().lower()
    size_bytes = int(document.get("size_bytes") or 0)
    if not drive_id or not drive_item_id:
        return [], "", False, "Stored Graph drive/item identifiers are unavailable."
    if extension not in SUPPORTED_EXTENSIONS:
        return [], "", False, f"On-demand text extraction does not support {extension or 'this file type'}."
    if size_bytes > MAX_LIVE_DOWNLOAD_BYTES:
        return [], "", False, (
            f"The source file exceeds the {MAX_LIVE_DOWNLOAD_BYTES // (1024 * 1024)} MB "
            "on-demand download limit."
        )

    factory = graph_client_factory or (lambda: GraphClient(max_retries=2))
    try:
        with TemporaryDirectory(prefix="spraytec-sharepoint-") as temp_dir:
            path = Path(temp_dir) / (str(document.get("file_name") or "document")[:180])
            factory().download_item(drive_id, drive_item_id, path)
            extracted = extractor(path, document)
    except (GraphError, OSError, RuntimeError) as exc:
        return [], "", False, (
            "The indexed SharePoint source could not be downloaded or read through Graph "
            f"({type(exc).__name__})."
        )
    rows = [
        {
            "content_type": row.content_type,
            "source_locator": row.source_locator,
            "page_number": row.page_number,
            "sheet_name": row.sheet_name,
            "row_number": row.row_number,
            "section_name": row.section_name,
            "text_content": row.text_content,
        }
        for row in extracted.rows
    ]
    return rows, extracted.extraction_method, extracted.requires_ocr, ""


def _format_content_rows(
    rows: list[dict[str, Any]],
    *,
    max_chars: int,
) -> tuple[str, int, bool]:
    sections: list[str] = []
    used = 0
    truncated = False
    for row in rows:
        body = str(row.get("text_content") or "").strip()
        if not body:
            continue
        label_parts = [str(row.get("source_locator") or "").strip()]
        if row.get("page_number") is not None:
            label_parts.append(f"page {row['page_number']}")
        if str(row.get("sheet_name") or "").strip():
            label_parts.append(f"sheet {row['sheet_name']}")
        if row.get("row_number") is not None:
            label_parts.append(f"row {row['row_number']}")
        label = " | ".join(part for part in label_parts if part)
        section = f"[{label}]\n{body}" if label else body
        separator_size = 2 if sections else 0
        remaining = max_chars - used - separator_size
        if remaining <= 0:
            truncated = True
            break
        if len(section) > remaining:
            sections.append(section[:remaining].rstrip())
            used = max_chars
            truncated = True
            break
        sections.append(section)
        used += len(section) + separator_size
    return "\n\n".join(sections), len(sections), truncated


def _excerpt(value: str, tokens: list[str], width: int = 360) -> str:
    compact = " ".join(value.split())
    if not compact:
        return ""
    lowered = compact.lower()
    positions = [lowered.find(token.lower()) for token in tokens]
    positions = [position for position in positions if position >= 0]
    start = max(0, (min(positions) if positions else 0) - width // 4)
    return compact[start : start + width]
