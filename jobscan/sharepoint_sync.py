from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath, Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.exc import SQLAlchemyError

from .extractors import DOC_EXTS, SPREADSHEET_EXTS
from .graph_client import GraphClient, GraphError, SharePointTarget
from .scan import scan_root, write_csv, write_excel, write_json

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".tif", ".tiff"}
DEFAULT_RELEVANT_EXTS = SPREADSHEET_EXTS | DOC_EXTS | IMAGE_EXTS
DEFAULT_SKIP_DIRS = {"archive", "old", "trash", "temp", "test"}
IMAGE_MANIFEST_NAME = ".image_manifest.json"


@dataclass
class SyncStats:
    folders_seen: int = 0
    files_seen: int = 0
    downloaded_files: int = 0
    files_skipped: int = 0
    skipped_images: int = 0
    folders_created: int = 0
    manifest_files_written: int = 0
    bytes_downloaded: int = 0


@dataclass
class DeltaSyncStats:
    mode: str
    drive_id: str
    pages_processed: int = 0
    items_returned: int = 0
    new_items: int = 0
    modified_items: int = 0
    moved_documents: int = 0
    deleted_items: int = 0
    renamed_folders: int = 0
    documents_reconciled: int = 0
    documents_marked_pending: int = 0
    documents_marked_deleted: int = 0
    jobs_affected: int = 0
    jobs_rescanned: int = 0
    graph_requests: int = 0
    throttling_retries: int = 0
    unresolved_items: int = 0
    delta_token_saved: bool = False
    elapsed_seconds: float = 0.0
    affected_job_ids: list[str] | None = None
    changed_files: list[dict[str, Any]] | None = None
    changed_folders: list[dict[str, Any]] | None = None
    deleted_item_rows: list[dict[str, Any]] | None = None
    partial: bool = False


@dataclass
class DocumentReconciliationStats:
    missing_before: int = 0
    inventory_files_available: int = 0
    matched_by_drive_item_id: int = 0
    matched_by_document_id_drive_item_id: int = 0
    matched_by_exact_url: int = 0
    matched_by_url_path: int = 0
    matched_by_relative_path: int = 0
    matched_by_folder_file: int = 0
    matched_by_parent_name: int = 0
    matched_by_unique_filename: int = 0
    ambiguous_skipped: int = 0
    unmatched: int = 0
    documents_updated: int = 0
    missing_after: int = 0


def _safe_name(name: str) -> str:
    bad = '<>:"/\\|?*'
    cleaned = "".join("_" if c in bad else c for c in name).strip()
    return cleaned or "unnamed"


def is_url(value: Any) -> bool:
    return isinstance(value, str) and value.lower().startswith(("http://", "https://"))


def build_sharepoint_folder_url(site_url: str, library: str, folder_path: str) -> str | None:
    if not site_url or not folder_path:
        return None

    library_url_name = "Shared Documents" if library.lower() == "documents" else library
    encoded_parts = [quote(part) for part in folder_path.strip("/").split("/") if part]
    encoded_path = "/".join(encoded_parts)
    if not encoded_path:
        return f"{site_url.rstrip('/')}/{quote(library_url_name)}"
    return f"{site_url.rstrip('/')}/{quote(library_url_name)}/{encoded_path}"


def site_url_from_target(target: SharePointTarget) -> str:
    return f"https://{target.hostname}{target.site_path}"


def joined_folder_path(root_folder: str, relative_folder: str) -> str:
    parts = [part.strip("/") for part in (root_folder, relative_folder) if part and part != "."]
    return "/".join(part for part in parts if part)


def _load_manifest(path: Path) -> dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _should_download(item: dict[str, Any], max_file_mb: float) -> bool:
    name = item.get("name", "")
    suffix = Path(name).suffix.lower()
    if suffix not in DEFAULT_RELEVANT_EXTS:
        return False
    size = int(item.get("size") or 0)
    return size <= max_file_mb * 1024 * 1024


def _is_image_item(item: dict[str, Any]) -> bool:
    return Path(item.get("name", "")).suffix.lower() in IMAGE_EXTS


def _image_manifest_entry(child: dict[str, Any], relative_path: Path, drive_id: str | None = None) -> dict[str, Any]:
    parent = child.get("parentReference") if isinstance(child.get("parentReference"), dict) else {}
    return {
        "name": child.get("name"),
        "relative_path": str(relative_path),
        "size": child.get("size"),
        "last_modified": child.get("lastModifiedDateTime"),
        "web_url": child.get("webUrl"),
        "drive_id": drive_id or parent.get("driveId"),
        "drive_item_id": child.get("id"),
        "graph_item_id": child.get("id"),
        "parentReference": child.get("parentReference"),
    }


def drive_item_manifest_entry(child: dict[str, Any], relative_path: Path, drive_id: str | None = None) -> dict[str, Any]:
    name = child.get("name") or ""
    parent = child.get("parentReference") if isinstance(child.get("parentReference"), dict) else {}
    item_id = child.get("id")
    return {
        "name": name,
        "web_url": child.get("webUrl"),
        "webUrl": child.get("webUrl"),
        "drive_id": drive_id or parent.get("driveId"),
        "drive_item_id": item_id,
        "graph_item_id": item_id,
        "id": item_id,
        "relative_path": str(relative_path),
        "extension": Path(name).suffix.lower(),
        "document_type": classify_document_type(name),
        "modified_at": child.get("lastModifiedDateTime"),
        "parentReference": child.get("parentReference"),
        "file": child.get("file"),
        "folder": child.get("folder"),
    }


def classify_document_type(name: str) -> str:
    lower = name.lower()
    if any(token in lower for token in ("proposal", "quote", "bid")):
        return "proposal"
    if "invoice" in lower:
        return "invoice"
    if any(token in lower for token in ("contract", "signed", "agreement")):
        return "contract"
    if any(token in lower for token in ("job tracking", "tracking form")):
        return "job_tracking"
    if "warranty" in lower:
        return "warranty"
    if any(token in lower for token in ("aerial", "eagleview", "drone", "satellite")):
        return "aerial"
    if "estimate" in lower or Path(name).suffix.lower() in SPREADSHEET_EXTS:
        return "estimate"
    return "other"


def _name_matches(selected_name: Any, candidate_name: Any) -> bool:
    selected = str(selected_name or "").strip().lower()
    candidate = str(candidate_name or "").strip().lower()
    return bool(selected and candidate and (selected == candidate or selected in candidate or candidate in selected))


def _doc_sort_key(entry: dict[str, Any], selected_names: list[Any], preferred_type: str) -> tuple[int, str]:
    exact = any(_name_matches(selected, entry.get("name")) for selected in selected_names)
    type_match = entry.get("document_type") == preferred_type
    modified = str(entry.get("modified_at") or "")
    return (2 if exact else 1 if type_match else 0, modified)


def select_document_url(entries: list[dict[str, Any]], document_type: str, selected_names: list[Any] | None = None) -> dict[str, Any] | None:
    selected_names = selected_names or []
    candidates = [entry for entry in entries if entry.get("web_url") and entry.get("document_type") == document_type]
    if not candidates and document_type == "proposal":
        candidates = [entry for entry in entries if entry.get("web_url") and entry.get("document_type") == "estimate"]
    if not candidates:
        return None
    return sorted(candidates, key=lambda entry: _doc_sort_key(entry, selected_names, document_type), reverse=True)[0]


def sync_sharepoint_folder(
    *,
    client: GraphClient,
    target: SharePointTarget,
    cache_dir: Path,
    max_depth: int = 4,
    max_file_mb: float = 50,
    force: bool = False,
    skip_images: bool = True,
) -> tuple[Path, SyncStats]:
    """Mirror relevant SharePoint job files into a local cache for extraction.

    This does not export the whole document library. It recursively walks the selected SharePoint folder and downloads
    only files the scanner can use: Excel workbooks, docs/PDFs, and, when requested, common image types.
    """
    site = client.get_site(target.hostname, target.site_path)
    drive = client.get_drive_by_name(site["id"], target.library)
    root_item = client.get_root_or_path_item(drive["id"], target.folder_path)

    sync_root = cache_dir / _safe_name(site.get("name") or target.site_path.strip("/").replace("/", "_")) / _safe_name(target.folder_path or "root")
    manifest_path = sync_root / ".jobscan_manifest.json"
    manifest = _load_manifest(manifest_path)
    new_manifest: dict[str, Any] = {
        "site_id": site["id"],
        "drive_id": drive["id"],
        "folder_item_id": root_item["id"],
        "site_url": site_url_from_target(target),
        "library": target.library,
        "items": {},
        "folders": {},
        "documents": [],
    }
    stats = SyncStats()
    image_manifests: dict[Path, list[dict[str, Any]]] = {}

    def ensure_dir(path: Path) -> None:
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            stats.folders_created += 1
        else:
            path.mkdir(parents=True, exist_ok=True)

    def remember_folder(local_dir: Path, item: dict[str, Any], relative_folder: str) -> None:
        graph_folder_path = joined_folder_path(target.folder_path, relative_folder)
        folder_url = item.get("webUrl") or build_sharepoint_folder_url(
            site_url_from_target(target),
            target.library,
            graph_folder_path,
        )
        try:
            local_relative = str(local_dir.relative_to(sync_root))
        except ValueError:
            local_relative = relative_folder or "."
        local_relative = local_relative if local_relative else "."
        new_manifest["folders"][local_relative] = {
            "name": item.get("name"),
            "id": item.get("id"),
            "folder_path": graph_folder_path,
            "webUrl": folder_url,
        }

    def walk(item_id: str, local_dir: Path, depth: int, item: dict[str, Any], relative_folder: str = ".") -> None:
        if depth > max_depth:
            return
        ensure_dir(local_dir)
        remember_folder(local_dir, item, relative_folder)
        children = client.list_children(drive["id"], item_id)
        for child in children:
            name = child.get("name", "")
            if not name:
                continue
            if child.get("folder") is not None:
                if name.strip().lower() in DEFAULT_SKIP_DIRS:
                    continue
                stats.folders_seen += 1
                child_relative = name if relative_folder == "." else f"{relative_folder}/{name}"
                walk(child["id"], local_dir / _safe_name(name), depth + 1, child, child_relative)
                continue

            stats.files_seen += 1
            destination = local_dir / _safe_name(name)
            try:
                relative_path = destination.relative_to(sync_root)
            except ValueError:
                relative_path = destination
            doc_entry = drive_item_manifest_entry(child, relative_path, drive["id"])
            new_manifest["documents"].append(doc_entry)
            if skip_images and _is_image_item(child):
                image_manifests.setdefault(local_dir, []).append(_image_manifest_entry(child, relative_path, drive["id"]))
                stats.files_skipped += 1
                stats.skipped_images += 1
                continue

            if not _should_download(child, max_file_mb=max_file_mb):
                stats.files_skipped += 1
                continue

            item_key = child["id"]
            etag = child.get("eTag") or child.get("cTag")
            old = manifest.get("items", {}).get(item_key, {})
            new_manifest["items"][item_key] = {
                "name": name,
                "drive_id": drive["id"],
                "drive_item_id": child.get("id"),
                "graph_item_id": child.get("id"),
                "id": child.get("id"),
                "etag": etag,
                "size": child.get("size"),
                "webUrl": child.get("webUrl"),
                "parentReference": child.get("parentReference"),
                "file": child.get("file"),
                "lastModifiedDateTime": child.get("lastModifiedDateTime"),
                "local_path": str(destination.relative_to(sync_root)),
                "document_type": doc_entry.get("document_type"),
            }
            if not force and destination.exists() and old.get("etag") == etag:
                stats.files_skipped += 1
                continue

            client.download_item(drive["id"], child["id"], destination)
            stats.downloaded_files += 1
            stats.bytes_downloaded += int(child.get("size") or 0)

    walk(root_item["id"], sync_root, depth=0, item=root_item)
    for stale_manifest in sync_root.rglob(IMAGE_MANIFEST_NAME):
        if stale_manifest.parent not in image_manifests:
            stale_manifest.unlink()
    for local_dir, entries in image_manifests.items():
        manifest_file = local_dir / IMAGE_MANIFEST_NAME
        manifest_file.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        stats.manifest_files_written += 1
    _save_manifest(manifest_path, new_manifest)
    return sync_root, stats


def load_folder_url_map(cache_root: Path) -> dict[str, str]:
    manifest = _load_manifest(cache_root / ".jobscan_manifest.json")
    folders = manifest.get("folders") if isinstance(manifest, dict) else None
    if not isinstance(folders, dict):
        return {}
    out: dict[str, str] = {}
    for folder_path, metadata in folders.items():
        if not isinstance(metadata, dict):
            continue
        folder_url = metadata.get("webUrl") or metadata.get("web_url")
        if folder_url:
            out[str(folder_path)] = str(folder_url)
    return out


def attach_folder_urls(records: list[Any], cache_root: Path) -> None:
    folder_urls = load_folder_url_map(cache_root)
    for record in records:
        folder_path = getattr(record, "folder_path", None)
        if folder_path is None:
            continue
        folder_url = folder_urls.get(str(folder_path)) or folder_urls.get(str(Path(str(folder_path))))
        if folder_url:
            record.folder_url = folder_url


def load_document_manifest(cache_root: Path) -> list[dict[str, Any]]:
    manifest = _load_manifest(cache_root / ".jobscan_manifest.json")
    docs = manifest.get("documents") if isinstance(manifest, dict) else None
    if isinstance(docs, list):
        return [doc for doc in docs if isinstance(doc, dict)]
    items = manifest.get("items") if isinstance(manifest, dict) else None
    if not isinstance(items, dict):
        return []
    out: list[dict[str, Any]] = []
    manifest_drive_id = manifest.get("drive_id") if isinstance(manifest, dict) else None
    for item_id, item in items.items():
        if not isinstance(item, dict):
            continue
        name = item.get("name") or ""
        parent = item.get("parentReference") if isinstance(item.get("parentReference"), dict) else {}
        out.append(
            {
                "name": name,
                "web_url": item.get("webUrl") or item.get("web_url"),
                "drive_id": item.get("drive_id") or parent.get("driveId") or manifest_drive_id,
                "drive_item_id": item.get("drive_item_id") or item.get("graph_item_id") or item.get("id") or item_id,
                "graph_item_id": item.get("graph_item_id") or item.get("drive_item_id") or item.get("id") or item_id,
                "relative_path": item.get("local_path"),
                "extension": Path(name).suffix.lower(),
                "document_type": item.get("document_type") or classify_document_type(name),
                "modified_at": item.get("lastModifiedDateTime"),
                "parentReference": item.get("parentReference"),
            }
        )
    return out


def _docs_for_record(all_docs: list[dict[str, Any]], record: Any) -> list[dict[str, Any]]:
    folder_path = str(getattr(record, "folder_path", "") or "").strip().strip("/")
    folder_name = str(getattr(record, "folder_name", "") or "").strip().lower()
    docs: list[dict[str, Any]] = []
    for doc in all_docs:
        relative_path = str(doc.get("relative_path") or "").strip("/")
        parent_path = str((doc.get("parentReference") or {}).get("path") or "")
        haystack = f"{relative_path} {parent_path}".lower()
        if folder_path and folder_path.lower() in haystack:
            docs.append(doc)
        elif folder_name and folder_name in haystack:
            docs.append(doc)
    return docs


def attach_document_urls(records: list[Any], cache_root: Path) -> None:
    all_docs = load_document_manifest(cache_root)
    for record in records:
        docs = _docs_for_record(all_docs, record)
        selected = {
            "proposal": [getattr(record, "estimate_file", None), getattr(record, "primary_estimate_file", None)],
            "estimate": [getattr(record, "estimate_file", None), getattr(record, "primary_estimate_file", None)],
            "contract": [],
            "invoice": [getattr(record, "invoice_file", None)],
            "job_tracking": [getattr(record, "job_tracking_file", None)],
            "warranty": [],
            "aerial": [],
        }
        selected_docs: dict[str, dict[str, Any]] = {}
        for doc_type, selected_names in selected.items():
            match = select_document_url(docs, doc_type, selected_names)
            if match:
                selected_docs[doc_type] = match
                setattr(record, f"{doc_type}_url" if doc_type != "job_tracking" else "job_tracking_url", match.get("web_url"))
        if not getattr(record, "proposal_url", None):
            proposal = select_document_url(docs, "proposal", selected["proposal"])
            if proposal:
                record.proposal_url = proposal.get("web_url")
                selected_docs["proposal"] = proposal
        primary_type = None
        primary = None
        for doc_type in ("proposal", "estimate", "contract", "job_tracking"):
            url_attr = f"{doc_type}_url" if doc_type != "job_tracking" else "job_tracking_url"
            url = getattr(record, url_attr, None)
            if url:
                primary_type = doc_type
                primary = selected_docs.get(doc_type)
                record.primary_doc_link = url
                break
        if not record.primary_doc_link and getattr(record, "folder_url", None):
            primary_type = "folder"
            record.primary_doc_link = record.folder_url
        record.primary_doc_type = primary_type
        record.primary_doc_name = primary.get("name") if isinstance(primary, dict) else None
        link_rows = [
            {"type": key, "name": value.get("name"), "url": value.get("web_url")}
            for key, value in selected_docs.items()
            if value.get("web_url")
        ]
        record.document_link_count = len(link_rows)
        record.important_doc_links_json = json.dumps(link_rows, ensure_ascii=False) if link_rows else None


def load_configured_roots(config_path: Path | None) -> list[str]:
    if not config_path or not config_path.exists():
        return []
    try:
        import yaml
    except ImportError:
        return []
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    roots = payload.get("scan_roots") if isinstance(payload, dict) else []
    out: list[str] = []
    if isinstance(roots, list):
        for root in roots:
            if isinstance(root, dict) and root.get("folder"):
                out.append(normalize_drive_path(str(root["folder"])))
    return out


def normalize_drive_path(value: Any) -> str:
    return "/".join(part for part in str(value or "").replace("\\", "/").strip().strip("/").split("/") if part)


def relative_path_from_drive_item(item: dict[str, Any]) -> str:
    name = str(item.get("name") or "").strip("/")
    parent = item.get("parentReference") if isinstance(item.get("parentReference"), dict) else {}
    parent_path = str(parent.get("path") or "")
    if "root:" in parent_path:
        parent_path = parent_path.split("root:", 1)[1]
    parent_path = normalize_drive_path(parent_path)
    return normalize_drive_path(f"{parent_path}/{name}" if name else parent_path)


def parent_path_from_drive_item(item: dict[str, Any]) -> str:
    parent = item.get("parentReference") if isinstance(item.get("parentReference"), dict) else {}
    parent_path = str(parent.get("path") or "")
    if "root:" in parent_path:
        parent_path = parent_path.split("root:", 1)[1]
    return normalize_drive_path(parent_path)


def item_inventory_row(drive_id: str, item: dict[str, Any]) -> dict[str, Any]:
    parent = item.get("parentReference") if isinstance(item.get("parentReference"), dict) else {}
    file_meta = item.get("file") if isinstance(item.get("file"), dict) else {}
    return {
        "drive_id": drive_id,
        "drive_item_id": item.get("id"),
        "parent_item_id": parent.get("id"),
        "name": item.get("name"),
        "web_url": item.get("webUrl"),
        "parent_path": parent_path_from_drive_item(item),
        "relative_path": relative_path_from_drive_item(item),
        "is_folder": item.get("folder") is not None,
        "is_file": item.get("file") is not None,
        "mime_type": file_meta.get("mimeType"),
        "size_bytes": item.get("size"),
        "etag": item.get("eTag"),
        "ctag": item.get("cTag"),
        "last_modified_at": item.get("lastModifiedDateTime"),
        "metadata_json": json.dumps(item, default=str),
    }


def is_relevant_path(relative_path: str, roots: list[str]) -> bool:
    normalized = normalize_drive_path(relative_path).lower()
    if not roots:
        return True
    return any(normalized == root.lower() or normalized.startswith(root.lower().rstrip("/") + "/") for root in roots)


def changed_item_kind(previous: dict[str, Any] | None, row: dict[str, Any]) -> str:
    if previous is None:
        return "new"
    previous_path = normalize_drive_path(previous.get("relative_path"))
    current_path = normalize_drive_path(row.get("relative_path"))
    previous_name = str(previous.get("name") or "")
    current_name = str(row.get("name") or "")
    if previous_path != current_path:
        return "moved"
    if previous_name != current_name:
        return "renamed"
    for field in ("etag", "ctag", "last_modified_at", "size_bytes", "web_url"):
        if str(previous.get(field) or "") != str(row.get(field) or ""):
            return "modified"
    return "unchanged"


def progress(message: str) -> None:
    print(message, flush=True)


def database_host_summary(database_url: str | None) -> str:
    if not database_url:
        return "unknown"
    try:
        url = make_url(database_url)
    except Exception:
        return "unknown"
    host = url.host or "localhost"
    database = url.database or ""
    return f"{host}/{database}" if database else host


def commit_if_possible(connection: Connection) -> None:
    commit = getattr(connection, "commit", None)
    if callable(commit):
        try:
            commit()
        except Exception:
            pass


def ensure_delta_tables(connection: Connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS sharepoint_delta_state (
                site_id TEXT,
                drive_id TEXT PRIMARY KEY,
                library_name TEXT,
                delta_link TEXT,
                sync_status TEXT,
                sync_started_at TIMESTAMPTZ,
                sync_completed_at TIMESTAMPTZ,
                last_successful_sync_at TIMESTAMPTZ,
                items_seen BIGINT DEFAULT 0,
                changes_applied BIGINT DEFAULT 0,
                error_message TEXT,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS sharepoint_drive_items (
                drive_id TEXT NOT NULL,
                drive_item_id TEXT NOT NULL,
                parent_item_id TEXT,
                name TEXT,
                web_url TEXT,
                parent_path TEXT,
                relative_path TEXT,
                is_folder BOOLEAN,
                is_file BOOLEAN,
                mime_type TEXT,
                size_bytes BIGINT,
                etag TEXT,
                ctag TEXT,
                last_modified_at TIMESTAMPTZ,
                deleted_at TIMESTAMPTZ,
                first_seen_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                metadata_json JSONB,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (drive_id, drive_item_id)
            )
            """
        )
    )
    for stmt in (
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS drive_id TEXT",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS drive_item_id TEXT",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS drive_metadata_match_strategy TEXT",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS drive_metadata_matched_at TIMESTAMPTZ",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS drive_metadata_match_confidence TEXT",
    ):
        try:
            connection.execute(text(stmt))
        except SQLAlchemyError:
            pass


def try_advisory_lock(connection: Connection, drive_id: str) -> bool:
    try:
        locked = bool(connection.execute(text("SELECT pg_try_advisory_lock(hashtext(:drive_id))"), {"drive_id": drive_id}).scalar())
        commit_if_possible(connection)
        return locked
    except Exception:
        commit_if_possible(connection)
        return True


def release_advisory_lock(connection: Connection, drive_id: str) -> None:
    try:
        connection.execute(text("SELECT pg_advisory_unlock(hashtext(:drive_id))"), {"drive_id": drive_id})
        commit_if_possible(connection)
    except Exception:
        commit_if_possible(connection)
        pass


def get_delta_state(connection: Connection, drive_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        text("SELECT * FROM sharepoint_delta_state WHERE drive_id = :drive_id"),
        {"drive_id": drive_id},
    ).mappings().first()
    return dict(row) if row else None


def mark_delta_started(connection: Connection, site_id: str, drive_id: str, library: str, mode: str) -> None:
    connection.execute(
        text(
            """
            INSERT INTO sharepoint_delta_state (
                site_id, drive_id, library_name, sync_status, sync_started_at, error_message, created_at, updated_at
            )
            VALUES (:site_id, :drive_id, :library_name, :sync_status, NOW(), NULL, NOW(), NOW())
            ON CONFLICT (drive_id) DO UPDATE SET
                site_id = EXCLUDED.site_id,
                library_name = EXCLUDED.library_name,
                sync_status = EXCLUDED.sync_status,
                sync_started_at = NOW(),
                error_message = NULL,
                updated_at = NOW()
            """
        ),
        {"site_id": site_id, "drive_id": drive_id, "library_name": library, "sync_status": f"{mode}_running"},
    )


def mark_delta_failed(connection: Connection, drive_id: str, error: str) -> None:
    connection.execute(
        text(
            """
            UPDATE sharepoint_delta_state
            SET sync_status = 'failed',
                error_message = :error_message,
                sync_completed_at = NOW(),
                updated_at = NOW()
            WHERE drive_id = :drive_id
            """
        ),
        {"drive_id": drive_id, "error_message": error[:1000]},
    )


def mark_delta_interrupted(connection: Connection, drive_id: str, message: str = "Delta sync interrupted") -> None:
    connection.execute(
        text(
            """
            UPDATE sharepoint_delta_state
            SET sync_status = 'interrupted',
                error_message = :error_message,
                sync_completed_at = NOW(),
                updated_at = NOW()
            WHERE drive_id = :drive_id
            """
        ),
        {"drive_id": drive_id, "error_message": message[:1000]},
    )


def mark_delta_partial(connection: Connection, drive_id: str, stats: DeltaSyncStats) -> None:
    connection.execute(
        text(
            """
            UPDATE sharepoint_delta_state
            SET sync_status = 'partial_test',
                sync_completed_at = NOW(),
                items_seen = :items_seen,
                changes_applied = :changes_applied,
                error_message = 'Stopped by --limit-pages before final delta page; previous delta state preserved.',
                updated_at = NOW()
            WHERE drive_id = :drive_id
            """
        ),
        {
            "drive_id": drive_id,
            "items_seen": stats.items_returned,
            "changes_applied": stats.new_items + stats.modified_items + stats.deleted_items + stats.moved_documents + stats.renamed_folders,
        },
    )


def mark_delta_succeeded(connection: Connection, drive_id: str, delta_link: str, stats: DeltaSyncStats) -> None:
    connection.execute(
        text(
            """
            UPDATE sharepoint_delta_state
            SET delta_link = :delta_link,
                sync_status = 'succeeded',
                sync_completed_at = NOW(),
                last_successful_sync_at = NOW(),
                items_seen = :items_seen,
                changes_applied = :changes_applied,
                error_message = NULL,
                updated_at = NOW()
            WHERE drive_id = :drive_id
            """
        ),
        {
            "drive_id": drive_id,
            "delta_link": delta_link,
            "items_seen": stats.items_returned,
            "changes_applied": stats.new_items + stats.modified_items + stats.deleted_items + stats.moved_documents + stats.renamed_folders,
        },
    )


def fetch_existing_inventory(connection: Connection, drive_id: str, drive_item_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        text(
            """
            SELECT drive_id, drive_item_id, name, web_url, parent_path, relative_path, etag, ctag,
                   size_bytes, last_modified_at, deleted_at
            FROM sharepoint_drive_items
            WHERE drive_id = :drive_id AND drive_item_id = :drive_item_id
            """
        ),
        {"drive_id": drive_id, "drive_item_id": drive_item_id},
    ).mappings().first()
    return dict(row) if row else None


def upsert_inventory_item(connection: Connection, row: dict[str, Any]) -> str:
    previous = fetch_existing_inventory(connection, row["drive_id"], row["drive_item_id"])
    change_kind = changed_item_kind(previous, row)
    connection.execute(
        text(
            """
            INSERT INTO sharepoint_drive_items (
                drive_id, drive_item_id, parent_item_id, name, web_url, parent_path, relative_path,
                is_folder, is_file, mime_type, size_bytes, etag, ctag, last_modified_at,
                deleted_at, first_seen_at, last_seen_at, metadata_json, created_at, updated_at
            )
            VALUES (
                :drive_id, :drive_item_id, :parent_item_id, :name, :web_url, :parent_path, :relative_path,
                :is_folder, :is_file, :mime_type, :size_bytes, :etag, :ctag, :last_modified_at,
                NULL, NOW(), NOW(), CAST(:metadata_json AS JSONB), NOW(), NOW()
            )
            ON CONFLICT (drive_id, drive_item_id) DO UPDATE SET
                parent_item_id = EXCLUDED.parent_item_id,
                name = EXCLUDED.name,
                web_url = EXCLUDED.web_url,
                parent_path = EXCLUDED.parent_path,
                relative_path = EXCLUDED.relative_path,
                is_folder = EXCLUDED.is_folder,
                is_file = EXCLUDED.is_file,
                mime_type = EXCLUDED.mime_type,
                size_bytes = EXCLUDED.size_bytes,
                etag = EXCLUDED.etag,
                ctag = EXCLUDED.ctag,
                last_modified_at = EXCLUDED.last_modified_at,
                deleted_at = NULL,
                last_seen_at = NOW(),
                metadata_json = EXCLUDED.metadata_json,
                updated_at = NOW()
            """
        ),
        row,
    )
    return change_kind


def soft_delete_inventory_item(connection: Connection, drive_id: str, item: dict[str, Any]) -> None:
    connection.execute(
        text(
            """
            UPDATE sharepoint_drive_items
            SET deleted_at = COALESCE(deleted_at, NOW()),
                last_seen_at = NOW(),
                metadata_json = CAST(:metadata_json AS JSONB),
                updated_at = NOW()
            WHERE drive_id = :drive_id AND drive_item_id = :drive_item_id
            """
        ),
        {"drive_id": drive_id, "drive_item_id": item.get("id"), "metadata_json": json.dumps(item, default=str)},
    )


def is_blank(value: Any) -> bool:
    if value is None:
        return True
    text_value = str(value).strip()
    return not text_value or text_value.lower() in {"nan", "none", "null"}


def normalize_match_text(value: Any) -> str:
    if is_blank(value):
        return ""
    return " ".join(str(value).strip().lower().split())


def normalize_match_path(value: Any) -> str:
    if is_blank(value):
        return ""
    text_value = unquote(str(value)).replace("\\", "/").strip()
    while "//" in text_value:
        text_value = text_value.replace("//", "/")
    return normalize_drive_path(text_value).lower()


def normalize_full_url(value: Any) -> str:
    if is_blank(value):
        return ""
    text_value = unquote(str(value).strip())
    parsed = urlparse(text_value)
    if parsed.scheme and parsed.netloc:
        normalized_path = normalize_match_path(parsed.path)
        query = f"?{parsed.query}" if parsed.query else ""
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}/{normalized_path}{query}".rstrip("/")
    return normalize_match_path(text_value)


def normalize_url_path(value: Any) -> str:
    if is_blank(value):
        return ""
    parsed = urlparse(str(value).strip())
    path = parsed.path if parsed.scheme and parsed.netloc else str(value)
    return normalize_match_path(path)


def document_missing_complete_identifiers(document: dict[str, Any]) -> bool:
    return is_blank(document.get("drive_id")) or is_blank(document.get("drive_item_id"))


def document_id_drive_item_candidate(document: dict[str, Any]) -> str:
    document_id = str(document.get("document_id") or "")
    if document_id.startswith("driveitem-") and len(document_id) > len("driveitem-"):
        return document_id[len("driveitem-") :]
    return ""


def folder_file_path(document: dict[str, Any]) -> str:
    return normalize_match_path(f"{document.get('folder_path') or ''}/{document.get('file_name') or ''}")


def build_unique_lookup(rows: list[dict[str, Any]], key_func) -> tuple[dict[str, dict[str, Any]], set[str]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = key_func(row)
        if key:
            grouped.setdefault(key, []).append(row)
    unique = {key: values[0] for key, values in grouped.items() if len(values) == 1}
    ambiguous = {key for key, values in grouped.items() if len(values) > 1}
    return unique, ambiguous


def reconciliation_update(
    document: dict[str, Any],
    inventory: dict[str, Any],
    *,
    strategy: str,
    confidence: str,
    drive_item_id: str | None = None,
) -> dict[str, Any]:
    return {
        "document_id": document.get("document_id"),
        "drive_id": inventory.get("drive_id"),
        "drive_item_id": drive_item_id or document.get("drive_item_id") or inventory.get("drive_item_id"),
        "web_url": inventory.get("web_url"),
        "mime_type": inventory.get("mime_type"),
        "size_bytes": inventory.get("size_bytes"),
        "modified_at": inventory.get("last_modified_at"),
        "strategy": strategy,
        "confidence": confidence,
    }


def match_document_drive_metadata(
    documents: list[dict[str, Any]],
    inventory_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], DocumentReconciliationStats]:
    stats = DocumentReconciliationStats()
    candidate_documents = [dict(row) for row in documents if document_missing_complete_identifiers(row)]
    inventory_files = [dict(row) for row in inventory_rows if row.get("drive_item_id") and row.get("is_file") is not False]
    stats.missing_before = len(candidate_documents)
    stats.inventory_files_available = len(inventory_files)

    unmatched: dict[Any, dict[str, Any]] = {doc.get("document_id"): doc for doc in candidate_documents if doc.get("document_id")}
    updates: list[dict[str, Any]] = []

    def apply_match(document_id: Any, inventory: dict[str, Any], *, strategy: str, confidence: str, drive_item_id: str | None = None) -> None:
        if is_blank(inventory.get("drive_id")) or is_blank(drive_item_id or inventory.get("drive_item_id")):
            return
        document = unmatched.pop(document_id, None)
        if not document:
            return
        updates.append(reconciliation_update(document, inventory, strategy=strategy, confidence=confidence, drive_item_id=drive_item_id))
        setattr(stats, f"matched_by_{strategy}", getattr(stats, f"matched_by_{strategy}") + 1)

    inventory_by_drive_item_id, ambiguous_drive_item_ids = build_unique_lookup(inventory_files, lambda row: normalize_match_text(row.get("drive_item_id")))
    for document_id, document in list(unmatched.items()):
        key = normalize_match_text(document.get("drive_item_id"))
        if not key:
            continue
        if key in inventory_by_drive_item_id:
            apply_match(document_id, inventory_by_drive_item_id[key], strategy="drive_item_id", confidence="high")
        elif key in ambiguous_drive_item_ids:
            stats.ambiguous_skipped += 1

    for document_id, document in list(unmatched.items()):
        if not is_blank(document.get("drive_item_id")):
            continue
        key = normalize_match_text(document_id_drive_item_candidate(document))
        if not key:
            continue
        if key in inventory_by_drive_item_id:
            apply_match(document_id, inventory_by_drive_item_id[key], strategy="document_id_drive_item_id", confidence="high", drive_item_id=inventory_by_drive_item_id[key].get("drive_item_id"))
        elif key in ambiguous_drive_item_ids:
            stats.ambiguous_skipped += 1

    strategy_specs = [
        ("exact_url", "high", lambda doc: normalize_full_url(doc.get("sharepoint_url")), lambda row: normalize_full_url(row.get("web_url"))),
        ("url_path", "medium", lambda doc: normalize_url_path(doc.get("sharepoint_url")), lambda row: normalize_url_path(row.get("web_url"))),
        ("relative_path", "high", lambda doc: normalize_match_path(doc.get("relative_path")), lambda row: normalize_match_path(row.get("relative_path"))),
        ("folder_file", "high", folder_file_path, lambda row: normalize_match_path(row.get("relative_path"))),
        (
            "parent_name",
            "high",
            lambda doc: f"{normalize_match_path(doc.get('folder_path'))}||{normalize_match_text(doc.get('file_name'))}",
            lambda row: f"{normalize_match_path(row.get('parent_path'))}||{normalize_match_text(row.get('name'))}",
        ),
    ]
    for strategy, confidence, doc_key_func, inventory_key_func in strategy_specs:
        lookup, ambiguous = build_unique_lookup(inventory_files, inventory_key_func)
        for document_id, document in list(unmatched.items()):
            key = doc_key_func(document)
            if not key or key == "||":
                continue
            if key in lookup:
                apply_match(document_id, lookup[key], strategy=strategy, confidence=confidence)
            elif key in ambiguous:
                stats.ambiguous_skipped += 1

    document_filename_counts: dict[str, int] = {}
    for document in candidate_documents:
        key = normalize_match_text(document.get("file_name"))
        if key:
            document_filename_counts[key] = document_filename_counts.get(key, 0) + 1
    inventory_filename_lookup, ambiguous_inventory_filenames = build_unique_lookup(inventory_files, lambda row: normalize_match_text(row.get("name")))
    for document_id, document in list(unmatched.items()):
        key = normalize_match_text(document.get("file_name"))
        if not key:
            continue
        if document_filename_counts.get(key) == 1 and key in inventory_filename_lookup:
            apply_match(document_id, inventory_filename_lookup[key], strategy="unique_filename", confidence="low")
        elif document_filename_counts.get(key, 0) > 1 or key in ambiguous_inventory_filenames:
            stats.ambiguous_skipped += 1

    stats.unmatched = len(unmatched)
    stats.documents_updated = len(updates)
    stats.missing_after = stats.missing_before - stats.documents_updated
    return updates, stats


def table_columns(connection: Connection, table_name: str) -> set[str]:
    try:
        rows = connection.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = :table_name
                """
            ),
            {"table_name": table_name},
        ).fetchall()
        columns = {str(row[0]) for row in rows}
        if columns:
            return columns
    except Exception:
        pass
    return set()


def load_missing_document_rows(connection: Connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            text(
                """
                SELECT document_id, job_id, sharepoint_url, folder_path, relative_path, file_name,
                       drive_id, drive_item_id
                FROM documents
                WHERE NULLIF(drive_id, '') IS NULL OR NULLIF(drive_item_id, '') IS NULL
                """
            )
        )
        .mappings()
        .all()
    ]


def load_inventory_file_rows(connection: Connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            text(
                """
                SELECT drive_id, drive_item_id, name, web_url, parent_path, relative_path,
                       is_file, mime_type, size_bytes, last_modified_at
                FROM sharepoint_drive_items
                WHERE NULLIF(drive_item_id, '') IS NOT NULL
                  AND COALESCE(is_file, false) IS TRUE
                  AND deleted_at IS NULL
                """
            )
        )
        .mappings()
        .all()
    ]


def missing_complete_identifier_count(connection: Connection) -> int:
    return int(
        connection.execute(
            text(
                """
                SELECT COUNT(*)
                FROM documents
                WHERE NULLIF(drive_id, '') IS NULL OR NULLIF(drive_item_id, '') IS NULL
                """
            )
        ).scalar()
        or 0
    )


def docs_with_drive_item_missing_drive_count(connection: Connection) -> int:
    return int(
        connection.execute(
            text(
                """
                SELECT COUNT(*)
                FROM documents
                WHERE NULLIF(drive_item_id, '') IS NOT NULL AND NULLIF(drive_id, '') IS NULL
                """
            )
        ).scalar()
        or 0
    )


def inventory_files_available_count(connection: Connection) -> int:
    return int(
        connection.execute(
            text(
                """
                SELECT COUNT(*)
                FROM sharepoint_drive_items
                WHERE NULLIF(drive_item_id, '') IS NOT NULL
                  AND COALESCE(is_file, false) IS TRUE
                  AND deleted_at IS NULL
                """
            )
        ).scalar()
        or 0
    )


def apply_document_reconciliation_updates(
    connection: Connection,
    updates: list[dict[str, Any]],
    *,
    columns: set[str] | None = None,
) -> int:
    if not updates:
        return 0
    document_columns = columns or table_columns(connection, "documents")
    set_clauses = [
        "drive_id = COALESCE(NULLIF(drive_id, ''), :drive_id)",
        "drive_item_id = COALESCE(NULLIF(drive_item_id, ''), :drive_item_id)",
    ]
    if "sharepoint_url" in document_columns:
        set_clauses.append("sharepoint_url = COALESCE(NULLIF(sharepoint_url, ''), :web_url)")
    if "mime_type" in document_columns:
        set_clauses.append("mime_type = COALESCE(NULLIF(mime_type, ''), :mime_type)")
    if "size_bytes" in document_columns:
        set_clauses.append("size_bytes = COALESCE(size_bytes, :size_bytes)")
    if "modified_at" in document_columns:
        set_clauses.append("modified_at = COALESCE(modified_at, :modified_at)")
    if "drive_metadata_match_strategy" in document_columns:
        set_clauses.append("drive_metadata_match_strategy = :strategy")
    if "drive_metadata_match_confidence" in document_columns:
        set_clauses.append("drive_metadata_match_confidence = :confidence")
    if "drive_metadata_matched_at" in document_columns:
        set_clauses.append("drive_metadata_matched_at = CURRENT_TIMESTAMP")
    if "updated_at" in document_columns:
        set_clauses.append("updated_at = CURRENT_TIMESTAMP")
    statement = text(
        f"""
        UPDATE documents
        SET {", ".join(set_clauses)}
        WHERE document_id = :document_id
          AND (NULLIF(drive_id, '') IS NULL OR NULLIF(drive_item_id, '') IS NULL)
        """
    )
    updated = 0
    for update in updates:
        if is_blank(update.get("drive_id")) or is_blank(update.get("drive_item_id")):
            continue
        result = connection.execute(statement, update)
        updated += int(result.rowcount or 0)
    return updated


def reconcile_document_drive_metadata(
    connection: Connection,
    *,
    inventory_rows: list[dict[str, Any]] | None = None,
    debug: bool = False,
) -> DocumentReconciliationStats:
    ensure_delta_tables(connection)
    missing_before = missing_complete_identifier_count(connection)
    inventory_count = inventory_files_available_count(connection) if inventory_rows is None else len(inventory_rows)
    documents = load_missing_document_rows(connection)
    inventory = inventory_rows if inventory_rows is not None else load_inventory_file_rows(connection)
    updates, stats = match_document_drive_metadata(documents, inventory)
    stats.missing_before = missing_before
    stats.inventory_files_available = inventory_count
    stats.documents_updated = apply_document_reconciliation_updates(connection, updates)
    stats.missing_after = missing_complete_identifier_count(connection)
    if debug:
        print_reconciliation_debug(connection, documents, inventory, updates)
    return stats


def print_reconciliation_debug(
    connection: Connection,
    documents: list[dict[str, Any]],
    inventory: list[dict[str, Any]],
    updates: list[dict[str, Any]],
) -> None:
    matched_ids = {update.get("document_id") for update in updates}
    unmatched = [doc for doc in documents if doc.get("document_id") not in matched_ids][:10]
    progress(f"Documents with drive_item_id but missing drive_id: {docs_with_drive_item_missing_drive_count(connection)}")
    if unmatched:
        progress("Sample unmatched documents:")
        for doc in unmatched:
            progress(
                "  "
                f"document_id={doc.get('document_id')} file_name={doc.get('file_name')} "
                f"drive_item_candidate={doc.get('drive_item_id') or document_id_drive_item_candidate(doc)} "
                f"url_path={normalize_url_path(doc.get('sharepoint_url'))} "
                f"relative_path={normalize_match_path(doc.get('relative_path'))} "
                f"folder_file={folder_file_path(doc)}"
            )
            same_name = [row for row in inventory if normalize_match_text(row.get("name")) == normalize_match_text(doc.get("file_name"))][:5]
            for row in same_name:
                progress(f"    same filename inventory: drive_id={row.get('drive_id')} item={row.get('drive_item_id')} path={row.get('relative_path')}")


def print_document_reconciliation_stats(stats: DocumentReconciliationStats) -> None:
    progress(f"documents missing complete identifiers before: {stats.missing_before}")
    progress(f"inventory files available: {stats.inventory_files_available}")
    progress(f"matched by drive_item_id: {stats.matched_by_drive_item_id}")
    progress(f"matched by document_id-derived drive_item_id: {stats.matched_by_document_id_drive_item_id}")
    progress(f"matched by exact URL: {stats.matched_by_exact_url}")
    progress(f"matched by URL path: {stats.matched_by_url_path}")
    progress(f"matched by relative path: {stats.matched_by_relative_path}")
    progress(f"matched by folder plus filename: {stats.matched_by_folder_file}")
    progress(f"matched by parent path plus filename: {stats.matched_by_parent_name}")
    progress(f"matched by unique filename fallback: {stats.matched_by_unique_filename}")
    progress(f"ambiguous skipped: {stats.ambiguous_skipped}")
    progress(f"unmatched: {stats.unmatched}")
    progress(f"documents updated: {stats.documents_updated}")
    progress(f"documents missing complete identifiers after: {stats.missing_after}")


def reconcile_documents_for_items(connection: Connection, drive_id: str, rows: list[dict[str, Any]]) -> int:
    page_file_rows = [row for row in rows if row.get("is_file") is not False and row.get("drive_item_id")]
    if not page_file_rows:
        return 0
    return reconcile_document_drive_metadata(connection, inventory_rows=page_file_rows).documents_updated


def mark_deleted_documents(connection: Connection, drive_id: str, item_ids: list[str]) -> int:
    if not item_ids:
        return 0
    result = connection.execute(
        text(
            """
            UPDATE documents
            SET extraction_status = 'deleted',
                deleted_at = COALESCE(deleted_at, NOW()),
                updated_at = NOW()
            WHERE drive_id = :drive_id
              AND drive_item_id = ANY(:item_ids)
            """
        ),
        {"drive_id": drive_id, "item_ids": item_ids},
    )
    return int(result.rowcount or 0)


def affected_jobs_for_items(connection: Connection, drive_id: str, rows: list[dict[str, Any]], deleted_ids: list[str]) -> set[str]:
    job_ids: set[str] = set()
    for row in rows:
        matches = connection.execute(
            text(
                """
                SELECT DISTINCT job_id
                FROM documents
                WHERE job_id IS NOT NULL
                  AND (
                    (drive_id = :drive_id AND drive_item_id = :drive_item_id)
                    OR (sharepoint_url IS NOT NULL AND sharepoint_url <> '' AND sharepoint_url = :web_url)
                    OR (relative_path IS NOT NULL AND relative_path <> '' AND relative_path = :relative_path)
                  )
                """
            ),
            {
                "drive_id": drive_id,
                "drive_item_id": row["drive_item_id"],
                "web_url": row.get("web_url"),
                "relative_path": row.get("relative_path"),
            },
        ).fetchall()
        job_ids.update(str(match[0]) for match in matches if match[0])
    if deleted_ids:
        matches = connection.execute(
            text(
                """
                SELECT DISTINCT job_id
                FROM documents
                WHERE drive_id = :drive_id AND drive_item_id = ANY(:item_ids) AND job_id IS NOT NULL
                """
            ),
            {"drive_id": drive_id, "item_ids": deleted_ids},
        ).fetchall()
        job_ids.update(str(match[0]) for match in matches if match[0])
    return job_ids


def process_delta_page(
    connection: Connection,
    *,
    drive_id: str,
    page: dict[str, Any],
    roots: list[str],
    stats: DeltaSyncStats,
) -> dict[str, Any]:
    page_items = page.get("value", [])
    page_counts = {
        "items": 0,
        "folders": 0,
        "files": 0,
        "deleted": 0,
        "upserted": 0,
        "documents_reconciled": 0,
    }
    changed_rows: list[dict[str, Any]] = []
    changed_folder_rows: list[dict[str, Any]] = []
    page_file_rows: list[dict[str, Any]] = []
    deleted_ids: list[str] = []
    deleted_rows: list[dict[str, Any]] = []
    for item in page_items:
        if not isinstance(item, dict) or not item.get("id"):
            stats.unresolved_items += 1
            continue
        stats.items_returned += 1
        page_counts["items"] += 1
        if item.get("deleted") is not None:
            soft_delete_inventory_item(connection, drive_id, item)
            deleted_ids.append(str(item["id"]))
            deleted_rows.append({"drive_id": drive_id, "drive_item_id": item.get("id"), "change_type": "deleted", "metadata": item})
            stats.deleted_items += 1
            page_counts["deleted"] += 1
            continue
        row = item_inventory_row(drive_id, item)
        if row.get("is_folder"):
            page_counts["folders"] += 1
        if row.get("is_file"):
            page_counts["files"] += 1
        if not row["drive_item_id"]:
            stats.unresolved_items += 1
            continue
        change_kind = upsert_inventory_item(connection, row)
        page_counts["upserted"] += 1
        if row.get("is_file"):
            page_file_rows.append(row)
        if not is_relevant_path(str(row.get("relative_path") or ""), roots):
            continue
        if change_kind == "new":
            stats.new_items += 1
        elif change_kind == "modified":
            stats.modified_items += 1
        elif change_kind == "moved":
            stats.moved_documents += 1
        elif change_kind == "renamed":
            if row.get("is_folder"):
                stats.renamed_folders += 1
            else:
                stats.moved_documents += 1
        if change_kind != "unchanged":
            row["change_type"] = change_kind
            if row.get("is_file"):
                changed_rows.append(row)
            elif row.get("is_folder"):
                changed_folder_rows.append(row)
    page_counts["documents_reconciled"] = reconcile_documents_for_items(connection, drive_id, page_file_rows)
    stats.documents_reconciled += page_counts["documents_reconciled"]
    stats.documents_marked_deleted += mark_deleted_documents(connection, drive_id, deleted_ids)
    page_job_ids = affected_jobs_for_items(connection, drive_id, changed_rows, deleted_ids)
    existing_job_ids = set(stats.affected_job_ids or [])
    stats.affected_job_ids = sorted(existing_job_ids | page_job_ids)
    stats.jobs_affected = len(stats.affected_job_ids)
    stats.jobs_rescanned = len(stats.affected_job_ids)
    current_files = list(stats.changed_files or [])
    current_files.extend(changed_rows)
    stats.changed_files = current_files
    current_folders = list(stats.changed_folders or [])
    current_folders.extend(changed_folder_rows)
    stats.changed_folders = current_folders
    current_deleted = list(stats.deleted_item_rows or [])
    current_deleted.extend(deleted_rows)
    stats.deleted_item_rows = current_deleted
    return page_counts


def graph_delta_pages(client: GraphClient, start_url: str) -> tuple[list[dict[str, Any]], str, int]:
    pages: list[dict[str, Any]] = []
    url = start_url
    requests = 0
    while url:
        requests += 1
        page = client.get_json(url)
        pages.append(page)
        url = page.get("@odata.nextLink")
    delta_link = pages[-1].get("@odata.deltaLink") if pages else None
    if not delta_link:
        raise RuntimeError("Graph delta response did not include a final deltaLink.")
    return pages, delta_link, requests


def run_delta_sync(
    *,
    engine: Engine,
    client: GraphClient,
    target: SharePointTarget,
    config_path: Path | None = None,
    full_refresh: bool = False,
    limit_pages: int | None = None,
    debug_progress: bool = False,
    database_url: str | None = None,
) -> DeltaSyncStats:
    start = time.monotonic()
    progress("Starting Microsoft Graph delta sync")
    progress(f"Site URL: {site_url_from_target(target)}")
    progress(f"Library: {target.library}")
    progress(f"Database target: {database_host_summary(database_url)}")
    site = client.get_site(target.hostname, target.site_path)
    drive = client.get_drive_by_name(site["id"], target.library)
    drive_id = drive["id"]
    progress(f"Drive ID: {drive_id}")
    roots = load_configured_roots(config_path)

    with engine.begin() as conn:
        ensure_delta_tables(conn)
    lock_conn = engine.connect()
    locked = False
    stats = DeltaSyncStats(mode="initial", drive_id=drive_id)
    try:
        locked = try_advisory_lock(lock_conn, drive_id)
        if not locked:
            progress(f"Delta sync lock not acquired for drive {drive_id}; another sync is already running.")
            raise RuntimeError(f"Another delta synchronization is already running for drive {drive_id}.")
        progress(f"Delta sync lock acquired for drive {drive_id}")
        with engine.begin() as conn:
            state = get_delta_state(conn, drive_id)
            mode = "full_refresh" if full_refresh else "incremental" if state and state.get("delta_link") else "initial"
            stats.mode = mode
            mark_delta_started(conn, site["id"], drive_id, target.library, mode)
        progress(f"Mode: {stats.mode} delta")
        progress(f"Previous delta state exists: {'yes' if state and state.get('delta_link') else 'no'}")
        start_url = f"/drives/{drive_id}/root/delta" if full_refresh or not state or not state.get("delta_link") else str(state["delta_link"])
        url: str | None = start_url
        delta_link: str | None = None
        stopped_by_limit = False
        retried_after_410 = False
        while url:
            if limit_pages is not None and stats.pages_processed >= max(limit_pages, 0):
                stopped_by_limit = True
                break
            stats.graph_requests += 1
            try:
                page = client.get_json(url)
            except GraphError as exc:
                if not retried_after_410 and "410" in str(exc):
                    progress("Saved delta state expired; starting fresh full delta enumeration.")
                    with engine.begin() as conn:
                        mark_delta_started(conn, site["id"], drive_id, target.library, "token_expired_full_refresh")
                    stats.mode = "token_expired_full_refresh"
                    url = f"/drives/{drive_id}/root/delta"
                    retried_after_410 = True
                    continue
                raise
            stats.pages_processed += 1
            with engine.begin() as conn:
                page_counts = process_delta_page(conn, drive_id=drive_id, page=page, roots=roots, stats=stats)
            stats.documents_marked_pending += page_counts["documents_reconciled"]
            elapsed = time.monotonic() - start
            progress(
                "Delta page "
                f"{stats.pages_processed}: items={page_counts['items']}, cumulative={stats.items_returned}, "
                f"folders={page_counts['folders']}, files={page_counts['files']}, deleted={page_counts['deleted']}, "
                f"sharepoint_drive_items_upserted={page_counts['upserted']}, "
                f"documents_reconciled={page_counts['documents_reconciled']}, elapsed={elapsed:.1f}s"
            )
            if debug_progress:
                progress(f"  next page available: {'yes' if page.get('@odata.nextLink') else 'no'}")
                progress(f"  final delta state on this page: {'yes' if page.get('@odata.deltaLink') else 'no'}")
            delta_link = page.get("@odata.deltaLink") or delta_link
            url = page.get("@odata.nextLink")

        if stopped_by_limit and url:
            stats.partial = True
            with engine.begin() as conn:
                mark_delta_partial(conn, drive_id, stats)
            progress(f"Stopped after --limit-pages={limit_pages}; final delta state was not saved.")
            return stats
        if not delta_link:
            raise RuntimeError("Graph delta response did not include a final deltaLink.")
        with engine.begin() as conn:
            mark_delta_succeeded(conn, drive_id, delta_link, stats)
        stats.delta_token_saved = True
    except Exception as exc:
        with engine.begin() as conn:
            ensure_delta_tables(conn)
            mark_delta_failed(conn, drive_id, str(exc))
        raise
    except KeyboardInterrupt:
        progress("Delta sync interrupted")
        with engine.begin() as conn:
            ensure_delta_tables(conn)
            mark_delta_interrupted(conn, drive_id)
        raise
    finally:
        if locked:
            release_advisory_lock(lock_conn, drive_id)
            progress(f"Delta sync lock released for drive {drive_id}")
        lock_conn.close()
        stats.elapsed_seconds = time.monotonic() - start
    return stats


def print_delta_stats(stats: DeltaSyncStats) -> None:
    print(f"Synchronization mode: {stats.mode}")
    print(f"Drive ID: {stats.drive_id}")
    print(f"Pages processed: {stats.pages_processed}")
    print(f"Items returned: {stats.items_returned}")
    print(f"New items: {stats.new_items}")
    print(f"Modified items: {stats.modified_items}")
    print(f"Moved documents: {stats.moved_documents}")
    print(f"Deleted items: {stats.deleted_items}")
    print(f"Renamed folders: {stats.renamed_folders}")
    print(f"Documents reconciled: {stats.documents_reconciled}")
    print(f"Documents marked pending: {stats.documents_marked_pending}")
    print(f"Documents marked deleted: {stats.documents_marked_deleted}")
    print(f"Jobs affected: {stats.jobs_affected}")
    print(f"Jobs rescanned: {stats.jobs_rescanned}")
    print(f"Graph requests: {stats.graph_requests}")
    print(f"Throttling/retries: {stats.throttling_retries}")
    print(f"Unresolved items: {stats.unresolved_items}")
    print(f"Final delta state saved: {'yes' if stats.delta_token_saved else 'no'}")
    print(f"Elapsed seconds: {stats.elapsed_seconds:.2f}")
    if stats.affected_job_ids:
        print("Affected job IDs:")
        for job_id in stats.affected_job_ids[:50]:
            print(f"  {job_id}")


def print_delta_status(engine: Engine) -> None:
    with engine.begin() as conn:
        ensure_delta_tables(conn)
        rows = conn.execute(
            text(
                """
                SELECT drive_id, library_name, sync_status, sync_started_at, sync_completed_at,
                       last_successful_sync_at, items_seen, changes_applied,
                       CASE WHEN delta_link IS NULL OR delta_link = '' THEN false ELSE true END AS has_delta_link
                FROM sharepoint_delta_state
                ORDER BY updated_at DESC
                """
            )
        ).mappings().all()
    if not rows:
        print("No SharePoint delta state found.")
        return
    for row in rows:
        print(f"Drive ID: {row['drive_id']}")
        print(f"  Library: {row['library_name']}")
        print(f"  Status: {row['sync_status']}")
        print(f"  Last successful sync: {row['last_successful_sync_at']}")
        print(f"  Items seen: {row['items_seen']}")
        print(f"  Changes applied: {row['changes_applied']}")
        print(f"  Delta state stored: {'yes' if row['has_delta_link'] else 'no'}")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Sync SharePoint job folders through Microsoft Graph and build a job index.")
    parser.add_argument("--sharepoint-url", help="Site URL, e.g. https://contoso.sharepoint.com/sites/Operations")
    parser.add_argument("--site-url", help="Alias for --sharepoint-url used by delta sync.")
    parser.add_argument("--library", default="Documents", help="SharePoint document library name. Default: Documents")
    parser.add_argument("--folder", default="", help="Folder path inside the library, e.g. Estimates/2026")
    parser.add_argument("--cache", type=Path, default=Path(".cache/sharepoint"), help="Local cache folder")
    parser.add_argument("--max-depth", type=int, default=4, help="Recursive folder depth")
    parser.add_argument("--max-file-mb", type=float, default=50, help="Skip files larger than this")
    parser.add_argument("--force", action="store_true", help="Redownload even when eTag has not changed")
    image_group = parser.add_mutually_exclusive_group()
    image_group.add_argument("--skip-images", dest="skip_images", action="store_true", default=True, help="Skip image downloads and write image manifests. Default: true")
    image_group.add_argument("--include-images", dest="skip_images", action="store_false", help="Download image files for duplicate detection or image analysis")
    parser.add_argument("--out", type=Path, default=Path("output/job_index.csv"))
    parser.add_argument("--json", type=Path, default=Path("output/job_index.json"))
    parser.add_argument("--xlsx", type=Path, default=Path("output/job_index.xlsx"))
    parser.add_argument("--delta", action="store_true", help="Use Microsoft Graph delta sync for metadata discovery.")
    parser.add_argument("--full-refresh", action="store_true", help="Force a fresh full delta enumeration without discarding existing inventory first.")
    parser.add_argument("--limit-pages", type=int, help="Process only the first N delta pages for testing; does not save final delta state unless final page is reached.")
    parser.add_argument("--debug-progress", action="store_true", help="Print additional per-page delta progress details.")
    parser.add_argument("--delta-status", action="store_true", help="Show saved SharePoint delta synchronization state.")
    parser.add_argument("--reconcile-documents", action="store_true", help="Populate missing documents drive identifiers from sharepoint_drive_items inventory.")
    parser.add_argument("--debug-reconciliation", action="store_true", help="Print sample unmatched reconciliation candidates.")
    parser.add_argument("--config", type=Path, help="Batch scan roots YAML used to filter relevant delta items.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL"))
    args = parser.parse_args()

    if args.delta_status:
        if not args.database_url:
            raise SystemExit("Set --database-url, DATABASE_URL, or NEON_DATABASE_URL.")
        print_delta_status(create_engine(args.database_url, future=True))
        return

    if args.reconcile_documents:
        if not args.database_url:
            raise SystemExit("Set --database-url, DATABASE_URL, or NEON_DATABASE_URL.")
        engine = create_engine(args.database_url, future=True)
        progress("Reconciling documents from SharePoint drive inventory")
        progress(f"Database target: {database_host_summary(args.database_url)}")
        with engine.begin() as conn:
            stats = reconcile_document_drive_metadata(conn, debug=args.debug_reconciliation)
        print_document_reconciliation_stats(stats)
        return

    site_url = args.site_url or args.sharepoint_url
    if not site_url:
        raise SystemExit("Set --site-url or --sharepoint-url.")
    target = SharePointTarget.from_url(site_url, library=args.library, folder_path=args.folder)
    client = GraphClient()
    if args.delta:
        if not args.database_url:
            raise SystemExit("Set --database-url, DATABASE_URL, or NEON_DATABASE_URL.")
        engine = create_engine(args.database_url, future=True)
        try:
            stats = run_delta_sync(
                engine=engine,
                client=client,
                target=target,
                config_path=args.config,
                full_refresh=args.full_refresh,
                limit_pages=args.limit_pages,
                debug_progress=args.debug_progress,
                database_url=args.database_url,
            )
        except KeyboardInterrupt:
            print("Delta synchronization interrupted. Previous valid delta token was preserved.")
            raise SystemExit(130)
        print_delta_stats(stats)
        return

    cache_root, stats = sync_sharepoint_folder(
        client=client,
        target=target,
        cache_dir=args.cache,
        max_depth=args.max_depth,
        max_file_mb=args.max_file_mb,
        force=args.force,
        skip_images=args.skip_images,
    )
    records = scan_root(cache_root, scan_context=target.folder_path)
    attach_folder_urls(records, cache_root)
    attach_document_urls(records, cache_root)
    write_csv(records, args.out)
    write_json(records, args.json)
    write_excel(records, args.xlsx)
    jobs_with_folder_url = sum(1 for record in records if is_url(record.folder_url))

    print(f"SharePoint cache: {cache_root}")
    print(f"Folders seen: {stats.folders_seen}")
    print(f"Files seen: {stats.files_seen}")
    print(f"Files downloaded: {stats.downloaded_files}")
    print(f"Files skipped: {stats.files_skipped}")
    print(f"Skipped images: {stats.skipped_images}")
    print(f"Folders created: {stats.folders_created}")
    print(f"Image manifest files written: {stats.manifest_files_written}")
    print(f"Jobs indexed: {len(records)}")
    print(f"Jobs with folder_url: {jobs_with_folder_url}")
    print(f"Jobs missing folder_url: {len(records) - jobs_with_folder_url}")
    print(f"CSV: {args.out}")
    print(f"JSON: {args.json}")
    print(f"Excel: {args.xlsx}")


if __name__ == "__main__":
    main()
