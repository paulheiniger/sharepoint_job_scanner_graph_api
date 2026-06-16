from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .extractors import DOC_EXTS, SPREADSHEET_EXTS
from .graph_client import GraphClient, SharePointTarget
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


def _image_manifest_entry(child: dict[str, Any], relative_path: Path) -> dict[str, Any]:
    return {
        "name": child.get("name"),
        "relative_path": str(relative_path),
        "size": child.get("size"),
        "last_modified": child.get("lastModifiedDateTime"),
        "web_url": child.get("webUrl"),
    }


def drive_item_manifest_entry(child: dict[str, Any], relative_path: Path) -> dict[str, Any]:
    name = child.get("name") or ""
    return {
        "name": name,
        "web_url": child.get("webUrl"),
        "webUrl": child.get("webUrl"),
        "graph_item_id": child.get("id"),
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
            doc_entry = drive_item_manifest_entry(child, relative_path)
            new_manifest["documents"].append(doc_entry)
            if skip_images and _is_image_item(child):
                image_manifests.setdefault(local_dir, []).append(_image_manifest_entry(child, relative_path))
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
    for item in items.values():
        if not isinstance(item, dict):
            continue
        name = item.get("name") or ""
        out.append(
            {
                "name": name,
                "web_url": item.get("webUrl") or item.get("web_url"),
                "graph_item_id": item.get("id"),
                "relative_path": item.get("local_path"),
                "extension": Path(name).suffix.lower(),
                "document_type": item.get("document_type") or classify_document_type(name),
                "modified_at": item.get("lastModifiedDateTime"),
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync SharePoint job folders through Microsoft Graph and build a job index.")
    parser.add_argument("--sharepoint-url", required=True, help="Site URL, e.g. https://contoso.sharepoint.com/sites/Operations")
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
    args = parser.parse_args()

    target = SharePointTarget.from_url(args.sharepoint_url, library=args.library, folder_path=args.folder)
    client = GraphClient()
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
