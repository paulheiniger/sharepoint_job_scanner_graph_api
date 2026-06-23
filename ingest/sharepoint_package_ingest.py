from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from jobscan.env import load_project_env
from ingest.package_ingest import (
    PackageInspectionResult,
    PdfCandidate,
    _candidate_id,
    _temp_root,
    classify_document_type,
    guess_default_selected,
)

load_project_env()

SHAREPOINT_NOT_CONFIGURED_MESSAGE = (
    "This is a SharePoint URL, not a local path. Configure Microsoft Graph/SharePoint intake or use a synced local OneDrive path."
)


def sharing_url_to_share_id(url: str) -> str:
    encoded = base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")
    return f"u!{encoded}"


def inspect_sharepoint_url_package(url: str) -> PackageInspectionResult:
    try:
        from jobscan.graph_client import GraphClient, GraphError
    except Exception as exc:
        return PackageInspectionResult(candidates=[], warnings=[f"{SHAREPOINT_NOT_CONFIGURED_MESSAGE} ({type(exc).__name__}: {exc})"], temp_dir=str(_temp_root()))

    try:
        client = GraphClient(max_retries=2)
        root_item = client.get_json(f"/shares/{sharing_url_to_share_id(url)}/driveItem")
    except Exception as exc:
        return PackageInspectionResult(candidates=[], warnings=[f"{SHAREPOINT_NOT_CONFIGURED_MESSAGE} ({type(exc).__name__}: {exc})"], temp_dir=str(_temp_root()))

    candidates: list[PdfCandidate] = []
    warnings: list[str] = []
    try:
        drive_id = (root_item.get("parentReference") or {}).get("driveId") or ""
        items = _collect_sharepoint_items(client, drive_id, root_item)
    except Exception as exc:
        return PackageInspectionResult(candidates=[], warnings=[f"Could not list SharePoint folder contents: {type(exc).__name__}: {exc}"], temp_dir=str(_temp_root()))

    total_size = 0
    for item in items:
        name = str(item.get("name") or "")
        suffix = Path(name).suffix.lower()
        if suffix not in {".pdf", ".zip"}:
            continue
        size = int(item.get("size") or 0)
        total_size += size
        item_drive_id = (item.get("parentReference") or {}).get("driveId") or drive_id
        item_id = str(item.get("id") or "")
        web_url = str(item.get("webUrl") or item.get("web_url") or name)
        fingerprint = hashlib.sha1(f"{item_drive_id}\0{item_id}\0{size}\0{item.get('lastModifiedDateTime')}".encode("utf-8")).hexdigest()
        document_type = classify_document_type(name)
        source_kind = "sharepoint_pdf" if suffix == ".pdf" else "sharepoint_zip"
        candidates.append(
            PdfCandidate(
                candidate_id=_candidate_id(web_url, fingerprint, len(candidates)),
                document_name=name,
                document_type=document_type,
                source_kind=source_kind,
                source_path=web_url,
                compressed_size=size,
                uncompressed_size=size,
                default_selected=guess_default_selected(web_url, document_type),
                file_hash=fingerprint,
                graph_drive_id=item_drive_id,
                graph_item_id=item_id,
            )
        )
    if not candidates:
        warnings.append("No PDF or ZIP files were found in the SharePoint folder.")
    return PackageInspectionResult(candidates=candidates, warnings=warnings, temp_dir=str(_temp_root()), total_upload_size=total_size)


def _collect_sharepoint_items(client: Any, drive_id: str, root_item: dict[str, Any], *, limit: int = 2000) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    queue = [root_item]
    while queue and len(items) < limit:
        item = queue.pop(0)
        if "folder" in item:
            item_id = str(item.get("id") or "")
            if not drive_id or not item_id:
                continue
            children = client.list_children(drive_id, item_id)
            queue.extend(children)
        else:
            items.append(item)
    return items
