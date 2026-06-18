from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from .job_search import first_nonblank, normalize_search_text, tokenize_search_text

DOCUMENT_TYPES = {
    "estimate",
    "proposal",
    "contract",
    "invoice",
    "warranty",
    "aerial",
    "job_tracking",
    "specification",
    "field_notes",
    "site_notes",
    "photos",
    "drawing",
    "bid_package",
    "safety",
    "change_order",
    "other",
}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".tif", ".tiff"}


def stable_document_id(row: dict[str, Any]) -> str:
    drive_item_id = first_nonblank(row.get("drive_item_id") or row.get("graph_item_id"))
    if drive_item_id:
        return f"driveitem-{drive_item_id}"
    content_hash = first_nonblank(row.get("content_hash"))
    if content_hash:
        return f"dochash-{hashlib.sha1(content_hash.encode('utf-8')).hexdigest()[:24]}"
    parts = [
        first_nonblank(row.get("job_id")),
        first_nonblank(row.get("sharepoint_url") or row.get("web_url") or row.get("webUrl")),
        first_nonblank(row.get("relative_path") or row.get("folder_path")),
        first_nonblank(row.get("file_name") or row.get("name")),
    ]
    digest = hashlib.sha1("||".join(parts).encode("utf-8")).hexdigest()
    return f"doc-{digest[:24]}"


def classify_document(file_name: str, folder_path: str = "") -> dict[str, str]:
    name = normalize_search_text(file_name)
    path = normalize_search_text(folder_path)
    combined = f"{path} {name}".strip()
    suffix = Path(file_name).suffix.lower()

    rules: list[tuple[str, str, bool]] = [
        ("invoice", "filename contains invoice or invoice number", bool(re.search(r"\binvoice\b|\binv\b|\b20\d{2}[- ]?\d{3}\b", combined))),
        ("job_tracking", "filename/path contains job tracking", "job tracking" in combined or "tracking form" in combined),
        ("change_order", "filename/path contains change order", "change order" in combined or re.search(r"\bco\b", name) is not None),
        ("safety", "filename/path contains safety", any(token in combined for token in ("safety", "msds", "sds", "incident report"))),
        ("aerial", "filename/path contains aerial/drone/EagleView term", any(token in combined for token in ("aerial", "drone", "eagleview", "satellite", "uav", "ir scan", "infrared"))),
        ("warranty", "filename/path contains warranty", "warranty" in combined),
        ("contract", "filename/path contains contract/agreement/award/po", any(token in combined for token in ("contract", "agreement", "award letter", "purchase order", " po "))),
        ("proposal", "filename/path contains proposal/quote/bid", any(token in combined for token in ("proposal", "quote", "bid"))),
        ("estimate", "Excel estimate file or filename contains estimate", "estimate" in combined or (suffix in {".xlsx", ".xlsm", ".xls"} and "tracking" not in combined)),
        ("specification", "filename/path contains spec/scope/submittal", any(token in combined for token in ("job spec", "specification", " spec ", "scope of work", "submittal"))),
        ("field_notes", "filename/path contains field/site/inspection/estimator notes", any(token in combined for token in ("field notes", "inspection notes", "estimator notes"))),
        ("site_notes", "filename/path contains site notes or estimate notes", any(token in combined for token in ("site notes", "estimate notes", "notes from"))),
        ("drawing", "filename/path contains drawing/plan/detail", any(token in combined for token in ("drawing", "drawings", "plans", "plan ", "detail"))),
        ("bid_package", "filename/path contains bid package", "bid package" in combined or "bid documents" in combined),
        ("photos", "image extension", suffix in IMAGE_EXTENSIONS),
    ]
    for document_type, reason, matched in rules:
        if matched:
            return {"document_type": document_type, "classification_reason": reason}
    return {"document_type": "other", "classification_reason": "no specific filename/path rule matched"}


def load_json_records(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("records", "rows", "data", "items"):
            if isinstance(payload.get(key), list):
                return [row for row in payload[key] if isinstance(row, dict)]
        return [payload]
    return []


def normalize_path(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip().strip("/")


def job_match_key(record: dict[str, Any]) -> str:
    return normalize_search_text(first_nonblank(record.get("folder_path"), record.get("folder_name")))


def match_job_for_document(document: dict[str, Any], jobs: list[dict[str, Any]]) -> dict[str, Any] | None:
    relative_path = normalize_path(document.get("relative_path") or document.get("local_path"))
    parent_path = normalize_path((document.get("parentReference") or {}).get("path"))
    haystack = normalize_search_text(f"{relative_path} {parent_path}")
    best: tuple[int, dict[str, Any]] | None = None
    for job in jobs:
        for candidate in (job.get("folder_path"), job.get("folder_name")):
            key = normalize_search_text(candidate)
            if not key:
                continue
            if key in haystack:
                score = len(key)
                if best is None or score > best[0]:
                    best = (score, job)
    return best[1] if best else None


def _manifest_documents(manifest_path: Path) -> list[dict[str, Any]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = manifest.get("items") if isinstance(manifest, dict) else {}
    docs = manifest.get("documents") if isinstance(manifest, dict) else []
    by_id: dict[str, dict[str, Any]] = {}
    if isinstance(items, dict):
        for item_id, item in items.items():
            if not isinstance(item, dict):
                continue
            row = dict(item)
            row["graph_item_id"] = row.get("graph_item_id") or row.get("id") or item_id
            row["relative_path"] = row.get("relative_path") or row.get("local_path")
            by_id[str(row["graph_item_id"])] = row
    if isinstance(docs, list):
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            item_id = str(doc.get("graph_item_id") or doc.get("id") or "")
            row = {**by_id.get(item_id, {}), **doc}
            if item_id:
                by_id[item_id] = row
            else:
                by_id[stable_document_id(doc)] = row
    return list(by_id.values())


def _image_documents(cache_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in cache_root.rglob(".image_manifest.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "name": item.get("name"),
                    "relative_path": item.get("relative_path"),
                    "size": item.get("size"),
                    "modified_at": item.get("last_modified"),
                    "web_url": item.get("web_url"),
                    "document_type": "photos",
                }
            )
    return rows


def build_document_index_records(
    *,
    job_index_path: Path = Path("output/job_index.json"),
    cache_root: Path = Path(".cache/sharepoint"),
) -> list[dict[str, Any]]:
    jobs = load_json_records(job_index_path)
    rows: list[dict[str, Any]] = []
    source_docs: list[dict[str, Any]] = []
    for manifest_path in cache_root.rglob(".jobscan_manifest.json"):
        source_docs.extend(_manifest_documents(manifest_path))
    source_docs.extend(_image_documents(cache_root))

    for doc in source_docs:
        job = match_job_for_document(doc, jobs)
        if not job:
            continue
        file_name = first_nonblank(doc.get("name"), doc.get("file_name"))
        relative_path = normalize_path(doc.get("relative_path") or doc.get("local_path"))
        parent = doc.get("parentReference") or {}
        file_meta = doc.get("file") or {}
        file_hashes = file_meta.get("hashes") if isinstance(file_meta.get("hashes"), dict) else {}
        sharepoint_url = first_nonblank(doc.get("web_url"), doc.get("webUrl"), doc.get("sharepoint_url"))
        folder_path = first_nonblank(job.get("folder_path"), parent.get("path"))
        classification = classify_document(file_name, f"{folder_path} {relative_path}")
        row = {
            "job_id": job.get("job_id"),
            "document_type": classification["document_type"],
            "classification_reason": classification["classification_reason"],
            "file_name": file_name,
            "sharepoint_url": sharepoint_url or None,
            "folder_path": folder_path or None,
            "relative_path": relative_path or None,
            "mime_type": file_meta.get("mimeType"),
            "file_extension": file_meta.get("fileExtension") or Path(file_name).suffix.lower(),
            "size_bytes": doc.get("size"),
            "modified_at": doc.get("modified_at") or doc.get("lastModifiedDateTime"),
            "source_year": job.get("source_year"),
            "source_division": job.get("division"),
            "drive_item_id": doc.get("graph_item_id") or doc.get("id"),
            "content_hash": file_hashes.get("quickXorHash"),
            "extraction_status": "not_started",
            "extraction_error": None,
        }
        row["document_id"] = stable_document_id(row)
        rows.append(row)

    deduped = {row["document_id"]: row for row in rows if row.get("document_id") and row.get("job_id") and row.get("file_name")}
    return list(deduped.values())


def write_document_index(records: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def documents_table_available(connection: Connection) -> bool:
    row = connection.execute(
        text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = 'documents'
            )
            """
        )
    ).scalar()
    return bool(row)


def documents_table_count(connection: Connection) -> int:
    if not documents_table_available(connection):
        return 0
    return int(connection.execute(text("SELECT COUNT(*) FROM documents")).scalar() or 0)


def dedupe_document_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen_urls: set[str] = set()
    seen_ids: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        url = first_nonblank(row.get("sharepoint_url"))
        document_id = first_nonblank(row.get("document_id"))
        if url:
            if url in seen_urls:
                continue
            seen_urls.add(url)
        elif document_id:
            if document_id in seen_ids:
                continue
            seen_ids.add(document_id)
        out.append(row)
    return out


def _connection(obj: Connection | Engine):
    return obj.connect() if isinstance(obj, Engine) else None


def list_job_documents(connection: Connection | Engine, job_id: str, document_type: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    manager = _connection(connection)
    conn = manager.__enter__() if manager else connection
    try:
        if not documents_table_available(conn):
            return []
        params: dict[str, Any] = {"job_id": str(job_id), "limit": limit}
        where = ["job_id = :job_id"]
        if document_type and document_type != "all":
            where.append("document_type = :document_type")
            params["document_type"] = document_type
        sql = f"""
            SELECT document_id, job_id, document_type, classification_reason, file_name, sharepoint_url,
                   folder_path, relative_path, mime_type, file_extension, size_bytes, modified_at,
                   source_year, source_division, drive_item_id, content_hash
            FROM documents
            WHERE {' AND '.join(where)}
            ORDER BY document_type, modified_at DESC NULLS LAST, file_name
            LIMIT :limit
        """
        return dedupe_document_rows([dict(row) for row in conn.execute(text(sql), params).mappings().all()])
    finally:
        if manager:
            manager.__exit__(None, None, None)


def search_documents(
    connection: Connection | Engine,
    query: str,
    job_id: str | None = None,
    document_type: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    manager = _connection(connection)
    conn = manager.__enter__() if manager else connection
    try:
        if not documents_table_available(conn):
            return []
        tokens = tokenize_search_text(query)
        params: dict[str, Any] = {"limit": limit}
        where: list[str] = []
        if job_id:
            where.append("job_id = :job_id")
            params["job_id"] = str(job_id)
        if document_type and document_type != "all":
            where.append("document_type = :document_type")
            params["document_type"] = document_type
        for index, token in enumerate(tokens):
            param = f"token_{index}"
            where.append(
                f"(LOWER(COALESCE(file_name, '')) LIKE :{param} OR "
                f"LOWER(COALESCE(relative_path, '')) LIKE :{param} OR "
                f"LOWER(COALESCE(folder_path, '')) LIKE :{param})"
            )
            params[param] = f"%{token.lower()}%"
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        sql = f"""
            SELECT document_id, job_id, document_type, classification_reason, file_name, sharepoint_url,
                   folder_path, relative_path, mime_type, file_extension, size_bytes, modified_at,
                   source_year, source_division, drive_item_id, content_hash
            FROM documents
            {where_sql}
            ORDER BY modified_at DESC NULLS LAST, file_name
            LIMIT :limit
        """
        return dedupe_document_rows([dict(row) for row in conn.execute(text(sql), params).mappings().all()])
    finally:
        if manager:
            manager.__exit__(None, None, None)


def summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "rows": len(records),
        "classification_counts": dict(Counter(row.get("document_type") or "other" for row in records)),
        "rows_missing_job_id": sum(1 for row in records if not row.get("job_id")),
        "rows_missing_url": sum(1 for row in records if not row.get("sharepoint_url")),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Build and query Spray-Tec document index metadata.")
    parser.add_argument("--build", action="store_true", help="Build output/document_index.json from cached SharePoint manifests.")
    parser.add_argument("--job-index", type=Path, default=Path("output/job_index.json"))
    parser.add_argument("--cache-root", type=Path, default=Path(".cache/sharepoint"))
    parser.add_argument("--out", type=Path, default=Path("output/document_index.json"))
    parser.add_argument("--job-id")
    parser.add_argument("--query")
    parser.add_argument("--document-type")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL"))
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.build:
        records = build_document_index_records(job_index_path=args.job_index, cache_root=args.cache_root)
        write_document_index(records, args.out)
        summary = summarize_records(records)
        print(f"Document index written: {args.out}")
        print(f"Rows: {summary['rows']}")
        print(f"Rows missing URL: {summary['rows_missing_url']}")
        print("Classification counts:")
        for key, count in sorted(summary["classification_counts"].items()):
            print(f"  {key}: {count}")
        return 0

    if not args.database_url:
        raise SystemExit("Set --database-url, DATABASE_URL, or NEON_DATABASE_URL.")
    engine = create_engine(args.database_url, future=True)
    if args.job_id:
        docs = list_job_documents(engine, args.job_id, args.document_type, args.limit)
    else:
        docs = search_documents(engine, args.query or "", document_type=args.document_type, limit=args.limit)
    print(f"Document count: {len(docs)}")
    for doc in docs:
        print(f"{doc.get('document_type') or '-'} | {doc.get('file_name') or '-'} | {doc.get('sharepoint_url') or '-'}")
        if args.debug:
            print(f"  reason: {doc.get('classification_reason') or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
