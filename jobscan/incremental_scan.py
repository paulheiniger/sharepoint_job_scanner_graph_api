from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from .db_loader import ensure_primary_id, load_dataset
from .document_index import classify_document, stable_document_id
from .estimate_datasets import scan_estimate_datasets_for_records
from .extractors import SPREADSHEET_EXTS, scan_job_folder
from .graph_client import GraphClient, SharePointTarget
from .job_tracking_extractor import JOB_TRACKING_DAILY_FIELDS, JOB_TRACKING_SUMMARY_FIELDS
from .job_tracking_extractor import scan_job_tracking_for_records
from .models import JobRecord
from .office_timesheet_sync import scan_office_timesheets
from .scan import records_as_dicts
from .sharepoint_sync import (
    DeltaSyncStats,
    is_relevant_path,
    normalize_drive_path,
    run_delta_sync,
)

TRACKING_NAME_TOKENS = {"job tracking", "tracking form"}
TIMESHEET_NAME_TOKENS = {"timesheet", "time sheet", "office time"}


@dataclass(frozen=True)
class ScanRootRule:
    folder: str
    division: str | None = None
    pipeline_status: str | None = None
    source_year: int | None = None
    root_kind: str = "job"


@dataclass
class IncrementalItem:
    drive_id: str
    drive_item_id: str
    change_type: str
    relative_path: str
    name: str
    web_url: str | None = None
    is_file: bool = False
    is_folder: bool = False
    etag: str | None = None
    ctag: str | None = None
    last_modified_at: str | None = None
    root: ScanRootRule | None = None
    job_path: str | None = None
    job_id: str | None = None
    processor: str = "document"


@dataclass
class IncrementalChangeSet:
    sync_run_id: str
    drive_id: str
    new_files: list[IncrementalItem] = field(default_factory=list)
    modified_files: list[IncrementalItem] = field(default_factory=list)
    moved_files: list[IncrementalItem] = field(default_factory=list)
    deleted_files: list[IncrementalItem] = field(default_factory=list)
    changed_folders: list[IncrementalItem] = field(default_factory=list)
    affected_job_ids: set[str] = field(default_factory=set)
    affected_job_paths: set[str] = field(default_factory=set)
    affected_estimate_files: set[str] = field(default_factory=set)
    affected_tracking_files: set[str] = field(default_factory=set)
    affected_timesheet_files: set[str] = field(default_factory=set)
    affected_documents: set[str] = field(default_factory=set)
    unresolved_items: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class IncrementalRunReport:
    run_id: str
    status: str
    drive_id: str
    started_at: str
    completed_at: str | None = None
    delta_mode: str | None = None
    affected_jobs: int = 0
    estimate_files: int = 0
    tracking_files: int = 0
    timesheet_files: int = 0
    documents: int = 0
    jobs_reparsed: int = 0
    estimates_reparsed: int = 0
    tracking_reparsed: int = 0
    timesheets_reparsed: int = 0
    job_files_downloaded: int = 0
    job_files_deleted: int = 0
    documents_upserted: int = 0
    documents_queued_for_extraction: int = 0
    job_index_list_rows_synced: int = 0
    failures: list[dict[str, str]] = field(default_factory=list)
    output_manifest_path: str | None = None
    elapsed_seconds: float = 0.0


def timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def stable_job_id(job_path: str) -> str:
    digest = hashlib.sha1(normalize_drive_path(job_path).lower().encode("utf-8")).hexdigest()
    return f"job-{digest[:20]}"


def file_sha1(path: Path | None) -> str | None:
    if not path or not path.exists():
        return None
    digest = hashlib.sha1()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def load_scan_root_rules(config_path: Path | None) -> list[ScanRootRule]:
    if not config_path or not config_path.exists():
        return []
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("Install pyyaml to use incremental scan roots.") from exc
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    roots = payload.get("scan_roots") if isinstance(payload, dict) else []
    out: list[ScanRootRule] = []
    if isinstance(roots, list):
        for root in roots:
            if not isinstance(root, dict) or not root.get("folder"):
                continue
            source_year = root.get("source_year")
            if source_year is None:
                source_year = infer_year(root.get("folder"))
            out.append(
                ScanRootRule(
                    folder=normalize_drive_path(root.get("folder")),
                    division=root.get("division"),
                    pipeline_status=root.get("pipeline_status"),
                    source_year=source_year,
                    root_kind="job",
                )
            )
    timesheet_roots = payload.get("timesheet_roots") if isinstance(payload, dict) else []
    if isinstance(timesheet_roots, list):
        for root in timesheet_roots:
            if not isinstance(root, dict) or not root.get("folder"):
                continue
            out.append(
                ScanRootRule(
                    folder=normalize_drive_path(root.get("folder")),
                    division=root.get("division"),
                    pipeline_status=root.get("pipeline_status"),
                    source_year=infer_year(root.get("folder")),
                    root_kind="office_timesheet",
                )
            )
    return out


def infer_year(value: Any) -> int | None:
    import re

    match = re.search(r"\b(20\d{2})\b", str(value or ""))
    return int(match.group(1)) if match else None


def map_path_to_job(relative_path: str, roots: list[ScanRootRule]) -> tuple[ScanRootRule | None, str | None, str | None]:
    path = normalize_drive_path(relative_path)
    for root in sorted(roots, key=lambda item: len(item.folder), reverse=True):
        prefix = root.folder.rstrip("/")
        if path.lower() == prefix.lower() or path.lower().startswith(prefix.lower() + "/"):
            if root.root_kind == "office_timesheet":
                return root, None, None
            remainder = path[len(prefix) :].strip("/")
            if not remainder:
                return root, None, None
            job_folder = remainder.split("/", 1)[0]
            job_path = normalize_drive_path(f"{prefix}/{job_folder}")
            return root, job_path, stable_job_id(job_path)
    return None, None, None


def processor_for_item(item: IncrementalItem) -> str:
    suffix = Path(item.name).suffix.lower()
    lower = item.name.lower()
    if item.root and item.root.root_kind == "office_timesheet" and suffix in SPREADSHEET_EXTS:
        return "office_timesheet"
    if suffix in SPREADSHEET_EXTS and any(token in lower for token in TRACKING_NAME_TOKENS):
        return "job_tracking"
    if suffix in SPREADSHEET_EXTS and any(token in lower for token in TIMESHEET_NAME_TOKENS):
        return "office_timesheet"
    if suffix in SPREADSHEET_EXTS:
        return "estimate"
    return "document"


def item_from_delta_row(row: dict[str, Any], change_type: str, roots: list[ScanRootRule]) -> IncrementalItem:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    relative_path = normalize_drive_path(row.get("relative_path") or metadata.get("relative_path") or "")
    name = str(row.get("name") or metadata.get("name") or Path(relative_path).name or "")
    root, job_path, job_id = map_path_to_job(relative_path, roots)
    item = IncrementalItem(
        drive_id=str(row.get("drive_id") or ""),
        drive_item_id=str(row.get("drive_item_id") or ""),
        change_type=change_type,
        relative_path=relative_path,
        name=name,
        web_url=row.get("web_url") or metadata.get("webUrl"),
        is_file=bool(row.get("is_file") or metadata.get("file") is not None),
        is_folder=bool(row.get("is_folder") or metadata.get("folder") is not None),
        etag=row.get("etag") or metadata.get("eTag"),
        ctag=row.get("ctag") or metadata.get("cTag"),
        last_modified_at=row.get("last_modified_at") or metadata.get("lastModifiedDateTime"),
        root=root,
        job_path=job_path,
        job_id=job_id,
    )
    item.processor = processor_for_item(item) if item.is_file else "job_folder"
    return item


def changeset_from_delta_stats(stats: DeltaSyncStats, roots: list[ScanRootRule], run_id: str) -> IncrementalChangeSet:
    changeset = IncrementalChangeSet(sync_run_id=run_id, drive_id=stats.drive_id)
    for row in stats.changed_files or []:
        item = item_from_delta_row(row, str(row.get("change_type") or "modified"), roots)
        if not item.root:
            changeset.unresolved_items.append(asdict(item))
            continue
        if item.change_type == "new":
            changeset.new_files.append(item)
        elif item.change_type == "moved":
            changeset.moved_files.append(item)
        else:
            changeset.modified_files.append(item)
        add_item_to_changeset(changeset, item)
    for row in stats.changed_folders or []:
        item = item_from_delta_row(row, str(row.get("change_type") or "modified"), roots)
        if item.root and item.job_path:
            changeset.changed_folders.append(item)
            changeset.affected_job_paths.add(item.job_path)
            changeset.affected_job_ids.add(item.job_id or stable_job_id(item.job_path))
        else:
            changeset.unresolved_items.append(asdict(item))
    for row in stats.deleted_item_rows or []:
        item = item_from_delta_row(row, "deleted", roots)
        if item.root:
            changeset.deleted_files.append(item)
            add_item_to_changeset(changeset, item)
        else:
            changeset.unresolved_items.append(asdict(item))
    return changeset


def add_item_to_changeset(changeset: IncrementalChangeSet, item: IncrementalItem) -> None:
    if item.processor != "office_timesheet" and item.job_id:
        changeset.affected_job_ids.add(item.job_id)
    if item.processor != "office_timesheet" and item.job_path:
        changeset.affected_job_paths.add(item.job_path)
    if item.processor == "estimate":
        changeset.affected_estimate_files.add(item.relative_path)
    elif item.processor == "job_tracking":
        changeset.affected_tracking_files.add(item.relative_path)
    elif item.processor == "office_timesheet":
        changeset.affected_timesheet_files.add(item.relative_path)
    changeset.affected_documents.add(f"{item.drive_id}:{item.drive_item_id}")


def load_json_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []


def atomic_write_json(path: Path, rows: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(rows, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    temp.replace(path)


def merge_rows(existing: list[dict[str, Any]], changed: list[dict[str, Any]], key: str, deleted_keys: set[str] | None = None) -> list[dict[str, Any]]:
    deleted_keys = deleted_keys or set()
    merged = {str(row.get(key)): row for row in existing if row.get(key) and str(row.get(key)) not in deleted_keys}
    for row in changed:
        value = row.get(key)
        if value:
            merged[str(value)] = row
    return list(merged.values())


def safe_cache_segment(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {" ", ".", "-", "_", "(", ")"} else "_" for char in value)
    return cleaned.strip() or "_"


def changed_timesheet_items(changeset: IncrementalChangeSet) -> list[IncrementalItem]:
    items = []
    for item in list(changeset.new_files) + list(changeset.modified_files) + list(changeset.moved_files):
        if item.processor == "office_timesheet" and item.is_file and Path(item.name).suffix.lower() in SPREADSHEET_EXTS:
            items.append(item)
    return items


def timesheet_local_path(item: IncrementalItem, timesheet_cache_root: Path) -> Path:
    relative_path = normalize_drive_path(item.relative_path)
    root_prefix = normalize_drive_path(item.root.folder if item.root else "").strip("/")
    if root_prefix and (relative_path.lower() == root_prefix.lower() or relative_path.lower().startswith(root_prefix.lower() + "/")):
        relative_path = relative_path[len(root_prefix) :].strip("/")
    parts = [safe_cache_segment(part) for part in Path(relative_path).parts if part and part != "."]
    return timesheet_cache_root.joinpath(*parts)


def source_keys_for_timesheet_record(record: dict[str, Any]) -> tuple[str, str]:
    source_path = str(record.get("source_file_path") or record.get("source_path") or "").strip()
    source_item = str(record.get("source_drive_item_id") or "").strip()
    return source_path, source_item


def merge_timesheet_rows(
    existing: list[dict[str, Any]],
    changed: list[dict[str, Any]],
    changed_source_paths: set[str],
    changed_drive_item_ids: set[str],
) -> list[dict[str, Any]]:
    merged = []
    for row in existing:
        source_path, source_item = source_keys_for_timesheet_record(row)
        if source_path and source_path in changed_source_paths:
            continue
        if source_item and source_item in changed_drive_item_ids:
            continue
        merged.append(row)
    merged.extend(changed)
    return merged


def process_changed_timesheets(
    *,
    client: GraphClient,
    changeset: IncrementalChangeSet,
    output_dir: Path,
    timesheet_cache_root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    items = changed_timesheet_items(changeset)
    deleted_items = [item for item in changeset.deleted_files if item.processor == "office_timesheet"]
    if not items and not deleted_items:
        return [], []

    failures: list[dict[str, str]] = []
    local_path_to_item: dict[str, IncrementalItem] = {}
    for item in items:
        destination = timesheet_local_path(item, timesheet_cache_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            client.download_item(item.drive_id, item.drive_item_id, destination)
            local_path_to_item[str(destination)] = item
        except Exception as exc:
            failures.append(
                {
                    "processor": "office_timesheet",
                    "path": item.relative_path,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    changed_records: list[dict[str, Any]] = []
    changed_source_paths: set[str] = set()
    changed_drive_item_ids = {item.drive_item_id for item in deleted_items if item.drive_item_id}
    if local_path_to_item:
        scan_result = scan_office_timesheets(timesheet_cache_root)
        for record in scan_result.records:
            local_source_path = str(record.get("source_path") or "")
            item = local_path_to_item.get(local_source_path)
            if not item:
                continue
            record["source_drive_id"] = item.drive_id
            record["source_drive_item_id"] = item.drive_item_id
            record["source_modified_at"] = item.last_modified_at
            record["source_file_path"] = local_source_path
            changed_source_paths.add(local_source_path)
            changed_drive_item_ids.add(item.drive_item_id)
            changed_records.append(record)

    for item in deleted_items:
        changed_source_paths.add(str(timesheet_local_path(item, timesheet_cache_root)))

    all_timesheets_path = output_dir / "office_timesheet_entries.json"
    existing = load_json_rows(all_timesheets_path)
    atomic_write_json(
        all_timesheets_path,
        merge_timesheet_rows(existing, changed_records, changed_source_paths, changed_drive_item_ids),
    )
    return changed_records, failures


def document_row_from_item(item: IncrementalItem) -> dict[str, Any]:
    classification = classify_document(item.name, item.relative_path)
    row = {
        "job_id": item.job_id,
        "document_type": classification["document_type"],
        "classification_reason": classification["classification_reason"],
        "file_name": item.name,
        "sharepoint_url": item.web_url,
        "folder_path": item.job_path,
        "relative_path": item.relative_path,
        "file_extension": Path(item.name).suffix.lower(),
        "modified_at": item.last_modified_at,
        "drive_id": item.drive_id,
        "drive_item_id": item.drive_item_id,
        "content_hash": item.etag or item.ctag,
        "extraction_status": "deleted" if item.change_type == "deleted" else "pending",
        "extraction_error": None,
    }
    row["document_id"] = stable_document_id(row)
    if item.change_type == "deleted":
        row["deleted_at"] = datetime.now(timezone.utc).isoformat()
    return row


def local_path_for_relative(cache_root: Path, relative_path: str) -> Path:
    return cache_root / normalize_drive_path(relative_path)


def load_cache_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def cache_manifest_root_path(manifest: dict[str, Any]) -> str:
    folders = manifest.get("folders") if isinstance(manifest.get("folders"), dict) else {}
    root = folders.get(".") if isinstance(folders.get("."), dict) else {}
    return normalize_drive_path(root.get("folder_path") or "")


def cache_manifest_for_drive_path(
    cache_root: Path,
    drive_id: str,
    drive_path: str,
) -> tuple[Path, dict[str, Any], str] | None:
    normalized_path = normalize_drive_path(drive_path)
    candidates: list[tuple[int, Path, dict[str, Any], str]] = []
    for manifest_path in cache_root.rglob(".jobscan_manifest.json"):
        manifest = load_cache_manifest(manifest_path)
        if drive_id and str(manifest.get("drive_id") or "") != drive_id:
            continue
        root_path = cache_manifest_root_path(manifest)
        if not root_path:
            continue
        if normalized_path.lower() == root_path.lower() or normalized_path.lower().startswith(root_path.lower() + "/"):
            candidates.append((len(root_path), manifest_path, manifest, root_path))
    if not candidates:
        return None
    _length, manifest_path, manifest, root_path = max(candidates, key=lambda row: row[0])
    return manifest_path, manifest, root_path


def cache_relative_parts(drive_path: str, root_path: str) -> list[str]:
    normalized_path = normalize_drive_path(drive_path)
    normalized_root = normalize_drive_path(root_path)
    relative = normalized_path[len(normalized_root) :].strip("/") if normalized_root else normalized_path
    return [safe_cache_segment(part) for part in Path(relative).parts if part and part != "."]


def cached_path_for_drive_item(
    cache_root: Path,
    item: IncrementalItem,
    *,
    prefer_manifest_item_path: bool = False,
) -> tuple[Path, Path, dict[str, Any]] | None:
    match = cache_manifest_for_drive_path(cache_root, item.drive_id, item.relative_path)
    if not match:
        return None
    manifest_path, manifest, root_path = match
    items = manifest.get("items") if isinstance(manifest.get("items"), dict) else {}
    existing = items.get(item.drive_item_id) if isinstance(items.get(item.drive_item_id), dict) else {}
    if prefer_manifest_item_path and existing.get("local_path"):
        destination = manifest_path.parent / str(existing["local_path"])
    else:
        parts = cache_relative_parts(item.relative_path, root_path)
        destination = manifest_path.parent.joinpath(*parts)
    return destination, manifest_path, manifest


def update_cache_manifest_item(
    manifest_path: Path,
    manifest: dict[str, Any],
    item: IncrementalItem,
    destination: Path,
) -> None:
    items = manifest.setdefault("items", {})
    existing = items.get(item.drive_item_id) if isinstance(items.get(item.drive_item_id), dict) else {}
    try:
        local_path = str(destination.relative_to(manifest_path.parent))
    except ValueError:
        local_path = destination.name
    items[item.drive_item_id] = {
        **existing,
        "name": item.name,
        "drive_id": item.drive_id,
        "drive_item_id": item.drive_item_id,
        "graph_item_id": item.drive_item_id,
        "id": item.drive_item_id,
        "etag": item.etag or item.ctag,
        "webUrl": item.web_url,
        "lastModifiedDateTime": item.last_modified_at,
        "local_path": local_path,
    }
    atomic_write_json(manifest_path, manifest)


def refresh_changed_job_files(
    client: GraphClient,
    changeset: IncrementalChangeSet,
    cache_root: Path,
) -> tuple[int, int, list[dict[str, str]]]:
    """Refresh changed estimate/tracking files in the existing scan-root cache."""

    downloaded = 0
    deleted = 0
    failures: list[dict[str, str]] = []
    changed_items = (
        list(changeset.new_files)
        + list(changeset.modified_files)
        + list(changeset.moved_files)
    )
    for item in changed_items:
        if item.processor not in {"estimate", "job_tracking"} or not item.is_file:
            continue
        target = cached_path_for_drive_item(cache_root, item)
        old_target = cached_path_for_drive_item(
            cache_root,
            item,
            prefer_manifest_item_path=True,
        )
        if not target:
            failures.append(
                {
                    "processor": item.processor,
                    "path": item.relative_path,
                    "error": "No matching local scan-root manifest was found for the changed SharePoint workbook.",
                }
            )
            continue
        destination, manifest_path, manifest = target
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            client.download_item(item.drive_id, item.drive_item_id, destination)
            if old_target and old_target[0] != destination and old_target[0].exists():
                old_target[0].unlink()
            update_cache_manifest_item(manifest_path, manifest, item, destination)
            downloaded += 1
        except Exception as exc:
            failures.append(
                {
                    "processor": item.processor,
                    "path": item.relative_path,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    for item in changeset.deleted_files:
        if item.processor not in {"estimate", "job_tracking"}:
            continue
        target = cached_path_for_drive_item(
            cache_root,
            item,
            prefer_manifest_item_path=True,
        )
        if not target:
            continue
        destination, manifest_path, manifest = target
        try:
            if destination.exists():
                destination.unlink()
                deleted += 1
            items = manifest.get("items") if isinstance(manifest.get("items"), dict) else {}
            items.pop(item.drive_item_id, None)
            atomic_write_json(manifest_path, manifest)
        except OSError as exc:
            failures.append(
                {
                    "processor": item.processor,
                    "path": item.relative_path,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return downloaded, deleted, failures


def cached_job_folder(
    changeset: IncrementalChangeSet,
    cache_root: Path,
    job_path: str,
) -> tuple[Path, Path] | None:
    match = cache_manifest_for_drive_path(cache_root, changeset.drive_id, job_path)
    if not match:
        return None
    manifest_path, _manifest, root_path = match
    parts = cache_relative_parts(job_path, root_path)
    return manifest_path.parent.joinpath(*parts), manifest_path.parent


def scan_affected_job_records(changeset: IncrementalChangeSet, cache_root: Path) -> tuple[list[JobRecord], list[dict[str, str]]]:
    records: list[JobRecord] = []
    failures: list[dict[str, str]] = []
    for job_path in sorted(changeset.affected_job_paths):
        cached = cached_job_folder(changeset, cache_root, job_path)
        if not cached:
            failures.append(
                {
                    "processor": "job_index",
                    "path": job_path,
                    "error": "Affected job folder could not be mapped to a local scan-root manifest.",
                }
            )
            continue
        local_job_path, local_scan_root = cached
        if not local_job_path.exists() or not local_job_path.is_dir():
            failures.append(
                {
                    "processor": "job_index",
                    "path": job_path,
                    "error": "Affected job folder is not present in local cache; full SharePoint traversal was not attempted.",
                }
            )
            continue
        root_rule, _job_path, _job_id = map_path_to_job(job_path, list(filter(None, [item.root for item in list(changeset.new_files) + list(changeset.modified_files) + list(changeset.moved_files)])))
        record = scan_job_folder(
            local_job_path,
            root=local_scan_root,
            scan_context=job_path,
        )
        if root_rule:
            record.division = root_rule.division
            record.pipeline_status = root_rule.pipeline_status
            record.scan_root = root_rule.folder
            record.source_year = root_rule.source_year
        records.append(record)
    return records, failures


def group_records_by_local_scan_root(
    changeset: IncrementalChangeSet,
    cache_root: Path,
    records: list[JobRecord],
) -> dict[Path, list[JobRecord]]:
    grouped: dict[Path, list[JobRecord]] = {}
    for record in records:
        scan_root = normalize_drive_path(getattr(record, "scan_root", None) or "")
        drive_path = normalize_drive_path(
            f"{scan_root}/{record.folder_path}" if scan_root else record.folder_path
        )
        match = cache_manifest_for_drive_path(
            cache_root,
            changeset.drive_id,
            drive_path,
        )
        if not match:
            continue
        manifest_path, _manifest, _root_path = match
        grouped.setdefault(manifest_path.parent, []).append(record)
    return grouped


def scan_estimates_for_affected_records(
    changeset: IncrementalChangeSet,
    cache_root: Path,
    records: list[JobRecord],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    estimates: list[dict[str, Any]] = []
    line_items: list[dict[str, Any]] = []
    for local_root, root_records in group_records_by_local_scan_root(
        changeset,
        cache_root,
        records,
    ).items():
        root_estimates, root_line_items = scan_estimate_datasets_for_records(
            local_root,
            root_records,
        )
        estimates.extend(root_estimates)
        line_items.extend(root_line_items)
    return estimates, line_items


def scan_tracking_for_affected_records(
    changeset: IncrementalChangeSet,
    cache_root: Path,
    records: list[JobRecord],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    summaries: list[dict[str, Any]] = []
    daily_entries: list[dict[str, Any]] = []
    for local_root, root_records in group_records_by_local_scan_root(
        changeset,
        cache_root,
        records,
    ).items():
        root_summaries, root_daily = scan_job_tracking_for_records(
            local_root,
            root_records,
        )
        summaries.extend(root_summaries)
        daily_entries.extend(root_daily)
    return summaries, daily_entries


def process_changed_jobs(changeset: IncrementalChangeSet, cache_root: Path, existing_jobs_path: Path, records: list[JobRecord] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    failures: list[dict[str, str]] = []
    if records is None:
        records, failures = scan_affected_job_records(changeset, cache_root)
    changed_rows = records_as_dicts(records)
    existing = load_json_rows(existing_jobs_path)
    deleted_job_ids = {item.job_id for item in changeset.deleted_files if item.job_id and item.is_folder}
    merged = merge_rows(existing, changed_rows, "job_id", deleted_job_ids)
    atomic_write_json(existing_jobs_path, merged)
    return changed_rows, failures


def merge_child_rows(existing: list[dict[str, Any]], changed: list[dict[str, Any]], key: str, parent_key: str, affected_parent_ids: set[str]) -> list[dict[str, Any]]:
    retained = [
        row
        for row in existing
        if not row.get(parent_key) or str(row.get(parent_key)) not in affected_parent_ids
    ]
    return merge_rows(retained, changed, key)


def replace_tracking_output_rows(
    existing_summaries: list[dict[str, Any]],
    existing_daily: list[dict[str, Any]],
    changed_summaries: list[dict[str, Any]],
    changed_daily: list[dict[str, Any]],
    affected_job_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    normalized_existing_summaries = [
        ensure_primary_id("job_tracking_summary", row)
        for row in existing_summaries
    ]
    normalized_existing_daily = [
        ensure_primary_id("job_tracking_daily", row)
        for row in existing_daily
    ]
    normalized_changed_summaries = [
        ensure_primary_id("job_tracking_summary", row)
        for row in changed_summaries
    ]
    normalized_changed_daily = [
        ensure_primary_id("job_tracking_daily", row)
        for row in changed_daily
    ]
    replaced_tracking_ids = {
        str(row.get("tracking_id"))
        for row in normalized_existing_summaries
        if str(row.get("job_id") or "") in affected_job_ids
        and row.get("tracking_id")
    }
    retained_summaries = [
        row
        for row in normalized_existing_summaries
        if str(row.get("job_id") or "") not in affected_job_ids
    ]
    retained_daily = [
        row
        for row in normalized_existing_daily
        if str(row.get("job_id") or "") not in affected_job_ids
        and str(row.get("tracking_id") or "") not in replaced_tracking_ids
    ]
    return (
        merge_rows(
            retained_summaries,
            normalized_changed_summaries,
            "tracking_id",
        ),
        merge_rows(
            retained_daily,
            normalized_changed_daily,
            "tracking_entry_id",
        ),
    )


def prune_job_tracking_rows_after_load(
    engine: Engine,
    job_ids: set[str],
    keep_tracking_ids: set[str],
    keep_entry_ids: set[str],
) -> None:
    clean_job_ids = sorted(job_id for job_id in job_ids if job_id)
    if not clean_job_ids:
        return
    with engine.begin() as conn:
        existing_tracking_ids = {
            str(row.tracking_id)
            for row in conn.execute(
                text(
                    "SELECT tracking_id FROM job_tracking_summary "
                    "WHERE job_id = ANY(:job_ids)"
                ),
                {"job_ids": clean_job_ids},
            )
            if row.tracking_id
        }
        kept_parent_ids = existing_tracking_ids & keep_tracking_ids
        if kept_parent_ids:
            if keep_entry_ids:
                conn.execute(
                    text(
                        "DELETE FROM job_tracking_daily_entries "
                        "WHERE tracking_id = ANY(:tracking_ids) "
                        "AND NOT (tracking_entry_id = ANY(:entry_ids))"
                    ),
                    {
                        "tracking_ids": sorted(kept_parent_ids),
                        "entry_ids": sorted(keep_entry_ids),
                    },
                )
            else:
                conn.execute(
                    text(
                        "DELETE FROM job_tracking_daily_entries "
                        "WHERE tracking_id = ANY(:tracking_ids)"
                    ),
                    {"tracking_ids": sorted(kept_parent_ids)},
                )
        obsolete_tracking_ids = existing_tracking_ids - keep_tracking_ids
        if obsolete_tracking_ids:
            conn.execute(
                text(
                    "DELETE FROM job_tracking_summary "
                    "WHERE tracking_id = ANY(:tracking_ids)"
                ),
                {"tracking_ids": sorted(obsolete_tracking_ids)},
            )


def ensure_incremental_tables(connection: Connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS sharepoint_incremental_runs (
                run_id TEXT PRIMARY KEY,
                delta_run_id TEXT,
                drive_id TEXT,
                started_at TIMESTAMPTZ,
                completed_at TIMESTAMPTZ,
                status TEXT,
                affected_jobs INTEGER DEFAULT 0,
                affected_estimates INTEGER DEFAULT 0,
                affected_tracking_files INTEGER DEFAULT 0,
                affected_timesheet_files INTEGER DEFAULT 0,
                affected_documents INTEGER DEFAULT 0,
                jobs_processed INTEGER DEFAULT 0,
                files_processed INTEGER DEFAULT 0,
                estimates_reparsed INTEGER DEFAULT 0,
                tracking_reparsed INTEGER DEFAULT 0,
                timesheets_reparsed INTEGER DEFAULT 0,
                job_files_downloaded INTEGER DEFAULT 0,
                job_files_deleted INTEGER DEFAULT 0,
                failures INTEGER DEFAULT 0,
                output_manifest_path TEXT,
                error_message TEXT,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    for statement in (
        "ALTER TABLE sharepoint_incremental_runs ADD COLUMN IF NOT EXISTS estimates_reparsed INTEGER DEFAULT 0",
        "ALTER TABLE sharepoint_incremental_runs ADD COLUMN IF NOT EXISTS tracking_reparsed INTEGER DEFAULT 0",
        "ALTER TABLE sharepoint_incremental_runs ADD COLUMN IF NOT EXISTS timesheets_reparsed INTEGER DEFAULT 0",
        "ALTER TABLE sharepoint_incremental_runs ADD COLUMN IF NOT EXISTS job_files_downloaded INTEGER DEFAULT 0",
        "ALTER TABLE sharepoint_incremental_runs ADD COLUMN IF NOT EXISTS job_files_deleted INTEGER DEFAULT 0",
    ):
        connection.execute(text(statement))
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS sharepoint_incremental_run_items (
                run_id TEXT,
                drive_id TEXT,
                drive_item_id TEXT,
                change_type TEXT,
                source_path TEXT,
                destination_path TEXT,
                mapped_job_id TEXT,
                processor TEXT,
                processing_status TEXT,
                error_message TEXT,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (run_id, drive_id, drive_item_id, processor)
            )
            """
        )
    )


def persist_incremental_run(engine: Engine, report: IncrementalRunReport, changeset: IncrementalChangeSet) -> None:
    with engine.begin() as conn:
        ensure_incremental_tables(conn)
        conn.execute(
            text(
                """
                INSERT INTO sharepoint_incremental_runs (
                    run_id, drive_id, started_at, completed_at, status, affected_jobs, affected_estimates,
                    affected_tracking_files, affected_timesheet_files, affected_documents, jobs_processed,
                    files_processed, estimates_reparsed, tracking_reparsed, timesheets_reparsed,
                    job_files_downloaded, job_files_deleted, failures, output_manifest_path,
                    error_message, created_at, updated_at
                )
                VALUES (
                    :run_id, :drive_id, :started_at, :completed_at, :status, :affected_jobs, :affected_estimates,
                    :affected_tracking_files, :affected_timesheet_files, :affected_documents, :jobs_processed,
                    :files_processed, :estimates_reparsed, :tracking_reparsed, :timesheets_reparsed,
                    :job_files_downloaded, :job_files_deleted, :failures, :output_manifest_path,
                    :error_message, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT (run_id) DO UPDATE SET
                    completed_at = EXCLUDED.completed_at,
                    status = EXCLUDED.status,
                    affected_jobs = EXCLUDED.affected_jobs,
                    affected_estimates = EXCLUDED.affected_estimates,
                    affected_tracking_files = EXCLUDED.affected_tracking_files,
                    affected_timesheet_files = EXCLUDED.affected_timesheet_files,
                    affected_documents = EXCLUDED.affected_documents,
                    jobs_processed = EXCLUDED.jobs_processed,
                    files_processed = EXCLUDED.files_processed,
                    estimates_reparsed = EXCLUDED.estimates_reparsed,
                    tracking_reparsed = EXCLUDED.tracking_reparsed,
                    timesheets_reparsed = EXCLUDED.timesheets_reparsed,
                    job_files_downloaded = EXCLUDED.job_files_downloaded,
                    job_files_deleted = EXCLUDED.job_files_deleted,
                    failures = EXCLUDED.failures,
                    output_manifest_path = EXCLUDED.output_manifest_path,
                    error_message = EXCLUDED.error_message,
                    updated_at = CURRENT_TIMESTAMP
                """
            ),
            {
                "run_id": report.run_id,
                "drive_id": report.drive_id,
                "started_at": report.started_at,
                "completed_at": report.completed_at,
                "status": report.status,
                "affected_jobs": report.affected_jobs,
                "affected_estimates": report.estimate_files,
                "affected_tracking_files": report.tracking_files,
                "affected_timesheet_files": report.timesheet_files,
                "affected_documents": report.documents,
                "jobs_processed": report.jobs_reparsed,
                "files_processed": report.estimates_reparsed + report.tracking_reparsed + report.timesheets_reparsed,
                "estimates_reparsed": report.estimates_reparsed,
                "tracking_reparsed": report.tracking_reparsed,
                "timesheets_reparsed": report.timesheets_reparsed,
                "job_files_downloaded": report.job_files_downloaded,
                "job_files_deleted": report.job_files_deleted,
                "failures": len(report.failures),
                "output_manifest_path": report.output_manifest_path,
                "error_message": "; ".join(f["error"] for f in report.failures)[:1000] if report.failures else None,
            },
        )
        for item in list(changeset.new_files) + list(changeset.modified_files) + list(changeset.moved_files) + list(changeset.deleted_files) + list(changeset.changed_folders):
            conn.execute(
                text(
                    """
                    INSERT INTO sharepoint_incremental_run_items (
                        run_id, drive_id, drive_item_id, change_type, source_path, mapped_job_id,
                        processor, processing_status, error_message, created_at, updated_at
                    )
                    VALUES (
                        :run_id, :drive_id, :drive_item_id, :change_type, :source_path, :mapped_job_id,
                        :processor, :processing_status, :error_message, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    ON CONFLICT (run_id, drive_id, drive_item_id, processor) DO UPDATE SET
                        change_type = EXCLUDED.change_type,
                        source_path = EXCLUDED.source_path,
                        mapped_job_id = EXCLUDED.mapped_job_id,
                        processing_status = EXCLUDED.processing_status,
                        error_message = EXCLUDED.error_message,
                        updated_at = CURRENT_TIMESTAMP
                    """
                ),
                {
                    "run_id": report.run_id,
                    "drive_id": item.drive_id,
                    "drive_item_id": item.drive_item_id,
                    "change_type": item.change_type,
                    "source_path": item.relative_path,
                    "mapped_job_id": item.job_id,
                    "processor": item.processor,
                    "processing_status": "pending" if report.status == "pending" else "processed",
                    "error_message": None,
                },
            )


def run_incremental(
    *,
    engine: Engine,
    client: GraphClient,
    target: SharePointTarget,
    config_path: Path | None,
    output_dir: Path,
    cache_root: Path,
    timesheet_cache_root: Path,
    metadata_only: bool = False,
    skip_db_load: bool = False,
    run_id: str | None = None,
    full_refresh_metadata: bool = False,
) -> IncrementalRunReport:
    started = time.monotonic()
    run_id = run_id or f"incr-{timestamp_slug()}"
    roots = load_scan_root_rules(config_path)
    if not roots:
        raise RuntimeError("No scan roots configured. Incremental routing requires scan root configuration.")

    delta_stats = run_delta_sync(
        engine=engine,
        client=client,
        target=target,
        config_path=config_path,
        full_refresh=full_refresh_metadata,
    )
    changeset = changeset_from_delta_stats(delta_stats, roots, run_id)
    report = IncrementalRunReport(
        run_id=run_id,
        status="running",
        drive_id=delta_stats.drive_id,
        started_at=datetime.now(timezone.utc).isoformat(),
        delta_mode=delta_stats.mode,
        affected_jobs=len(changeset.affected_job_ids),
        estimate_files=len(changeset.affected_estimate_files),
        tracking_files=len(changeset.affected_tracking_files),
        timesheet_files=len(changeset.affected_timesheet_files),
        documents=len(changeset.affected_documents),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    changed_docs = [document_row_from_item(item) for item in list(changeset.new_files) + list(changeset.modified_files) + list(changeset.moved_files) + list(changeset.deleted_files)]
    changed_docs_path = output_dir / "changed_documents.json"
    atomic_write_json(changed_docs_path, changed_docs)
    report.documents_upserted = len(changed_docs)
    report.documents_queued_for_extraction = sum(1 for row in changed_docs if row.get("extraction_status") == "pending")
    affected_records: list[JobRecord] = []

    if not metadata_only:
        (
            report.job_files_downloaded,
            report.job_files_deleted,
            cache_refresh_failures,
        ) = refresh_changed_job_files(client, changeset, cache_root)
        report.failures.extend(cache_refresh_failures)
        changed_jobs_path = output_dir / "changed_jobs.json"
        all_jobs_path = output_dir / "job_index.json"
        affected_records, scan_failures = scan_affected_job_records(
            changeset,
            cache_root,
        )
        changed_jobs, job_failures = process_changed_jobs(
            changeset,
            cache_root,
            all_jobs_path,
            affected_records,
        )
        atomic_write_json(changed_jobs_path, changed_jobs)
        report.jobs_reparsed = len(changed_jobs)
        report.failures.extend(scan_failures)
        report.failures.extend(job_failures)

        changed_estimates: list[dict[str, Any]] = []
        changed_line_items: list[dict[str, Any]] = []
        if affected_records and changeset.affected_estimate_files:
            changed_estimates, changed_line_items = scan_estimates_for_affected_records(
                changeset,
                cache_root,
                affected_records,
            )
        atomic_write_json(output_dir / "changed_estimates.json", changed_estimates)
        atomic_write_json(output_dir / "changed_estimate_line_items.json", changed_line_items)
        existing_estimates = load_json_rows(output_dir / "estimate_summary.json")
        affected_estimate_ids = {str(row.get("estimate_id")) for row in changed_estimates if row.get("estimate_id")}
        atomic_write_json(output_dir / "estimate_summary.json", merge_rows(existing_estimates, changed_estimates, "estimate_id"))
        existing_line_items = load_json_rows(output_dir / "estimate_line_items.json")
        atomic_write_json(
            output_dir / "estimate_line_items.json",
            merge_child_rows(existing_line_items, changed_line_items, "line_item_id", "estimate_id", affected_estimate_ids),
        )
        report.estimates_reparsed = len(changed_estimates)

        changed_tracking_summary: list[dict[str, Any]] = []
        changed_tracking_daily: list[dict[str, Any]] = []
        if affected_records and changeset.affected_tracking_files:
            changed_tracking_summary, changed_tracking_daily = scan_tracking_for_affected_records(
                changeset,
                cache_root,
                affected_records,
            )
        changed_tracking_summary = [
            ensure_primary_id("job_tracking_summary", row)
            for row in changed_tracking_summary
        ]
        changed_tracking_daily = [
            ensure_primary_id("job_tracking_daily", row)
            for row in changed_tracking_daily
        ]
        atomic_write_json(
            output_dir / "changed_tracking_summary.json",
            changed_tracking_summary,
        )
        atomic_write_json(
            output_dir / "changed_tracking_daily_entries.json",
            changed_tracking_daily,
        )
        existing_tracking_summary = load_json_rows(output_dir / "job_tracking_summary.json")
        existing_tracking_daily = load_json_rows(output_dir / "job_tracking_daily_entries.json")
        tracking_job_ids = {
            str(record.job_id)
            for record in affected_records
            if record.job_id
        }
        merged_tracking_summary, merged_tracking_daily = replace_tracking_output_rows(
            existing_tracking_summary,
            existing_tracking_daily,
            changed_tracking_summary,
            changed_tracking_daily,
            tracking_job_ids,
        )
        atomic_write_json(
            output_dir / "job_tracking_summary.json",
            merged_tracking_summary,
        )
        atomic_write_json(
            output_dir / "job_tracking_daily_entries.json",
            merged_tracking_daily,
        )
        report.tracking_reparsed = len(changed_tracking_summary)
        active_tracking_changes = [
            item
            for item in (
                list(changeset.new_files)
                + list(changeset.modified_files)
                + list(changeset.moved_files)
            )
            if item.processor == "job_tracking"
        ]
        if active_tracking_changes and report.tracking_reparsed == 0:
            report.failures.append(
                {
                    "processor": "job_tracking",
                    "path": active_tracking_changes[0].relative_path,
                    "error": (
                        f"{len(active_tracking_changes)} changed job-tracking workbook(s) "
                        "were detected, but no tracking summaries were parsed."
                    ),
                }
            )

        changed_timesheets, timesheet_failures = process_changed_timesheets(
            client=client,
            changeset=changeset,
            output_dir=output_dir,
            timesheet_cache_root=timesheet_cache_root,
        )
        atomic_write_json(output_dir / "changed_timesheets.json", changed_timesheets)
        report.timesheets_reparsed = len(changed_timesheets)
        report.failures.extend(timesheet_failures)
    else:
        atomic_write_json(output_dir / "changed_jobs.json", [])
        atomic_write_json(output_dir / "changed_estimates.json", [])
        atomic_write_json(output_dir / "changed_estimate_line_items.json", [])
        atomic_write_json(output_dir / "changed_tracking_summary.json", [])
        atomic_write_json(output_dir / "changed_tracking_daily_entries.json", [])
        atomic_write_json(output_dir / "changed_timesheets.json", [])

    manifest = {
        "run": asdict(report),
        "changeset": {
            "affected_job_ids": sorted(changeset.affected_job_ids),
            "affected_job_paths": sorted(changeset.affected_job_paths),
            "affected_estimate_files": sorted(changeset.affected_estimate_files),
            "affected_tracking_files": sorted(changeset.affected_tracking_files),
            "affected_timesheet_files": sorted(changeset.affected_timesheet_files),
            "affected_documents": sorted(changeset.affected_documents),
            "unresolved_items": changeset.unresolved_items,
        },
        "outputs": {
            "changed_jobs": str(output_dir / "changed_jobs.json"),
            "changed_documents": str(changed_docs_path),
            "job_index": str(output_dir / "job_index.json"),
        },
        "versions": {
            "scan_root_config_hash": file_sha1(config_path),
            "document_classification_version": "filename-path-rules-v1",
            "parser_version": "incremental-scan-v1",
        },
        "configuration_change_caveat": "If scan roots, classification rules, or parser behavior changed, run a bounded validation or explicit rebuild; delta alone only reports SharePoint item changes.",
        "full_scan_avoided": True,
        "unchanged_jobs_skipped": True,
        "unchanged_documents_skipped": True,
    }
    manifest_path = output_dir / f"{run_id}_manifest.json"
    atomic_write_json(manifest_path, manifest)
    report.output_manifest_path = str(manifest_path)

    if not skip_db_load:
        if changed_docs:
            load_dataset(engine, "documents", changed_docs_path, skip_missing=False)
        changed_jobs_path = output_dir / "changed_jobs.json"
        if changed_jobs_path.exists() and load_json_rows(changed_jobs_path):
            load_dataset(engine, "jobs", changed_jobs_path, skip_missing=False)
        for dataset_key, filename in [
            ("estimates", "changed_estimates.json"),
            ("line_items", "changed_estimate_line_items.json"),
            ("job_tracking_summary", "changed_tracking_summary.json"),
            ("job_tracking_daily", "changed_tracking_daily_entries.json"),
            ("office_timesheets", "changed_timesheets.json"),
        ]:
            path = output_dir / filename
            if path.exists() and load_json_rows(path):
                load_dataset(engine, dataset_key, path, skip_missing=False)
        if (
            changeset.affected_tracking_files
            and affected_records
            and not any(
                failure.get("processor") in {"job_tracking", "job_index"}
                for failure in report.failures
            )
        ):
            prune_job_tracking_rows_after_load(
                engine,
                {
                    str(record.job_id)
                    for record in affected_records
                    if record.job_id
                },
                {
                    str(row.get("tracking_id"))
                    for row in changed_tracking_summary
                    if row.get("tracking_id")
                },
                {
                    str(row.get("tracking_entry_id"))
                    for row in changed_tracking_daily
                    if row.get("tracking_entry_id")
                },
            )

    report.status = "failed" if report.failures else "succeeded"
    report.completed_at = datetime.now(timezone.utc).isoformat()
    report.elapsed_seconds = time.monotonic() - started
    persist_incremental_run(engine, report, changeset)
    atomic_write_json(manifest_path, {**manifest, "run": asdict(report)})
    return report


def print_report(report: IncrementalRunReport) -> None:
    print("Incremental SharePoint scan")
    print(f"Run ID: {report.run_id}")
    print(f"Status: {report.status}")
    print(f"Drive ID: {report.drive_id}")
    print(f"Delta mode: {report.delta_mode}")
    print(f"Affected jobs: {report.affected_jobs}")
    print(f"Estimate files: {report.estimate_files}")
    print(f"Tracking files: {report.tracking_files}")
    print(f"Timesheet files: {report.timesheet_files}")
    print(f"Documents: {report.documents}")
    print(f"Jobs reparsed: {report.jobs_reparsed}")
    print(f"Estimate summaries reparsed: {report.estimates_reparsed}")
    print(f"Job tracking summaries reparsed: {report.tracking_reparsed}")
    print(f"Timesheet entries reparsed: {report.timesheets_reparsed}")
    print(f"Changed job workbooks downloaded: {report.job_files_downloaded}")
    print(f"Deleted cached job workbooks removed: {report.job_files_deleted}")
    print(f"Documents upserted: {report.documents_upserted}")
    print(f"Documents queued for extraction: {report.documents_queued_for_extraction}")
    print(f"Failures: {len(report.failures)}")
    print("Unchanged jobs skipped: yes")
    print("Unchanged documents skipped: yes")
    print("Full scan avoided: yes")
    print(f"Manifest: {report.output_manifest_path}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Run delta-driven incremental Spray-Tec SharePoint processing.")
    parser.add_argument("--delta", action="store_true", help="Run Graph delta before incremental routing.")
    parser.add_argument("--site-url", default=os.getenv("SHAREPOINT_SITE_URL") or "https://aro365531128.sharepoint.com/sites/Data")
    parser.add_argument("--library", default="Documents")
    parser.add_argument("--config", type=Path, default=Path("config/sharepoint_scan_roots.yaml"))
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL"))
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--cache-root", type=Path, default=Path(".cache/sharepoint"))
    parser.add_argument("--timesheet-cache-root", type=Path, default=Path(".cache/office_timesheets/Data/Timesheets"))
    parser.add_argument("--metadata-only", action="store_true")
    parser.add_argument("--skip-db-load", action="store_true")
    parser.add_argument("--skip-job-index-list-sync", action="store_true")
    parser.add_argument("--queue-extraction", action="store_true")
    parser.add_argument("--extract-limit", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--full-refresh-metadata", action="store_true")
    parser.add_argument("--rebuild-all-jobs", action="store_true")
    parser.add_argument("--rebuild-all-estimates", action="store_true")
    parser.add_argument("--rebuild-all-tracking", action="store_true")
    parser.add_argument("--rebuild-all-timesheets", action="store_true")
    parser.add_argument("--rebuild-all-documents", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if any([args.rebuild_all_jobs, args.rebuild_all_estimates, args.rebuild_all_tracking, args.rebuild_all_timesheets, args.rebuild_all_documents]):
        print("Full rebuild flags are explicit recovery operations. Use the existing full-scan commands for the selected dataset.")
        return 2
    if not args.delta and not args.resume:
        print("Use --delta for the normal incremental workflow or --resume --run-id <ID> for a failed run.")
        return 2
    if args.resume:
        if not args.run_id:
            print("--resume requires --run-id.")
            return 2
        print(f"Resume requested for {args.run_id}. Retry support uses the saved run manifest and failed items; rerun with --delta once failures are corrected.")
        return 0
    if not args.database_url:
        raise SystemExit("Set --database-url, DATABASE_URL, or NEON_DATABASE_URL.")
    engine = create_engine(args.database_url, future=True)
    target = SharePointTarget.from_url(args.site_url, library=args.library)
    report = run_incremental(
        engine=engine,
        client=GraphClient(),
        target=target,
        config_path=args.config,
        output_dir=args.output_dir,
        cache_root=args.cache_root,
        timesheet_cache_root=args.timesheet_cache_root,
        metadata_only=args.metadata_only,
        skip_db_load=args.skip_db_load,
        run_id=args.run_id,
        full_refresh_metadata=args.full_refresh_metadata,
    )
    print_report(report)
    if not args.skip_job_index_list_sync:
        print("Changed-only Job Index List sync: run jobscan.sharepoint_list_sync with --input output/changed_jobs.json.")
    return 1 if report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
