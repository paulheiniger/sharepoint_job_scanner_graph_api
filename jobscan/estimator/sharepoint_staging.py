from __future__ import annotations

import base64
import os
from pathlib import Path
from urllib.parse import quote

from jobscan.graph_client import GraphClient, GraphError, SharePointTarget


class EstimateSharePointStagingUnavailable(RuntimeError):
    pass


def stage_estimate_workbook(
    path: Path,
    *,
    graph_client_factory=None,
) -> str:
    """Upload a validated draft to one explicitly configured staging folder."""
    folder_url = str(
        os.getenv("ESTIMATOR_SHAREPOINT_STAGING_FOLDER_URL") or ""
    ).strip()
    site_url = str(os.getenv("ESTIMATOR_SHAREPOINT_STAGING_SITE_URL") or "").strip()
    folder_path = str(os.getenv("ESTIMATOR_SHAREPOINT_STAGING_FOLDER") or "").strip(" /")
    library = str(
        os.getenv("ESTIMATOR_SHAREPOINT_STAGING_LIBRARY") or "Documents"
    ).strip()
    if not folder_url and (not site_url or not folder_path):
        raise EstimateSharePointStagingUnavailable(
            "SharePoint estimate staging is not configured. Set "
            "ESTIMATOR_SHAREPOINT_STAGING_FOLDER_URL, or set both the staging "
            "site URL and folder path."
        )

    source = Path(path).resolve()
    if not source.is_file() or source.suffix.lower() != ".xlsx":
        raise EstimateSharePointStagingUnavailable(
            "Only a validated XLSX workbook can be staged to SharePoint."
        )
    client = (graph_client_factory or (lambda: GraphClient(max_retries=2)))()
    try:
        if folder_url:
            share_id = "u!" + base64.urlsafe_b64encode(
                folder_url.encode("utf-8")
            ).decode("ascii").rstrip("=")
            folder = client.get_json(f"/shares/{share_id}/driveItem")
            drive_id = str((folder.get("parentReference") or {}).get("driveId") or "")
            folder_id = str(folder.get("id") or "")
            if not drive_id or not folder_id or "folder" not in folder:
                raise ValueError("Configured SharePoint sharing URL is not a folder.")
            encoded_name = quote(source.name, safe="")
            upload_url = (
                f"/drives/{drive_id}/items/{folder_id}:/{encoded_name}:/content"
            )
        else:
            target = SharePointTarget.from_url(
                site_url,
                library=library,
                folder_path=folder_path,
            )
            site = client.get_site(target.hostname, target.site_path)
            drive = client.get_drive_by_name(str(site["id"]), target.library)
            # Require the staging folder to exist so a typo cannot create content
            # in an unintended SharePoint location.
            client.get_root_or_path_item(str(drive["id"]), target.folder_path)
            encoded_path = quote(
                f"{target.folder_path.strip('/')}/{source.name}",
                safe="/",
            )
            upload_url = f"/drives/{drive['id']}/root:/{encoded_path}:/content"
        response = client.request("PUT", upload_url, data=source.read_bytes())
        item = response.json()
    except (GraphError, KeyError, ValueError, OSError) as exc:
        raise EstimateSharePointStagingUnavailable(
            f"SharePoint estimate staging failed: {type(exc).__name__}."
        ) from exc
    web_url = str(item.get("webUrl") or "").strip()
    if not web_url.startswith("https://"):
        raise EstimateSharePointStagingUnavailable(
            "SharePoint staged the workbook but did not return a usable web URL."
        )
    return web_url
