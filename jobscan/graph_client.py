from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import msal
import requests

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"


class GraphError(RuntimeError):
    pass


@dataclass(frozen=True)
class SharePointTarget:
    hostname: str
    site_path: str
    library: str = "Documents"
    folder_path: str = ""

    @classmethod
    def from_url(cls, url: str, library: str = "Documents", folder_path: str = "") -> "SharePointTarget":
        parsed = urlparse(url)
        if not parsed.netloc:
            raise ValueError("SharePoint URL must include a hostname, e.g. https://contoso.sharepoint.com/sites/Operations")
        # Graph expects /sites/Name or /teams/Name as the site path.
        path_parts = [p for p in parsed.path.split("/") if p]
        if len(path_parts) < 2:
            raise ValueError("SharePoint URL should include a site path, e.g. /sites/Operations")
        site_path = "/" + "/".join(path_parts[:2])
        return cls(hostname=parsed.netloc, site_path=site_path, library=library, folder_path=folder_path)


class GraphClient:
    """Small Microsoft Graph REST client for SharePoint document libraries.

    Auth mode: Azure app registration with client credentials.
    Required app permissions for read-only scanning:
      - Sites.Read.All or Files.Read.All
    Add Sites.ReadWrite.All only if you later want this tool to write back to SharePoint lists/files.
    """

    def __init__(
        self,
        tenant_id: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        timeout: int = 60,
    ) -> None:
        self.tenant_id = tenant_id or os.getenv("MS_TENANT_ID")
        self.client_id = client_id or os.getenv("MS_CLIENT_ID")
        self.client_secret = client_secret or os.getenv("MS_CLIENT_SECRET")
        self.timeout = timeout
        missing = [name for name, value in {
            "MS_TENANT_ID": self.tenant_id,
            "MS_CLIENT_ID": self.client_id,
            "MS_CLIENT_SECRET": self.client_secret,
        }.items() if not value]
        if missing:
            raise GraphError(f"Missing Microsoft Graph env vars: {', '.join(missing)}")

        self._token: str | None = None

    def token(self) -> str:
        if self._token:
            return self._token
        app = msal.ConfidentialClientApplication(
            client_id=self.client_id,
            authority=f"https://login.microsoftonline.com/{self.tenant_id}",
            client_credential=self.client_secret,
        )
        result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
        if "access_token" not in result:
            raise GraphError(f"Could not acquire Microsoft Graph token: {json.dumps(result, indent=2)}")
        self._token = result["access_token"]
        return self._token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token()}", "Accept": "application/json"}

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        if url.startswith("/"):
            url = GRAPH_ROOT + url
        response = requests.request(method, url, headers=self._headers(), timeout=self.timeout, **kwargs)
        if response.status_code >= 400:
            try:
                detail = response.json()
            except ValueError:
                detail = response.text[:1000]
            raise GraphError(f"Graph {method} {url} failed with {response.status_code}: {detail}")
        return response

    def get_json(self, url: str) -> dict[str, Any]:
        return self.request("GET", url).json()

    def get_all_pages(self, url: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        next_url: str | None = url
        while next_url:
            data = self.get_json(next_url)
            items.extend(data.get("value", []))
            next_url = data.get("@odata.nextLink")
        return items

    def get_site(self, hostname: str, site_path: str) -> dict[str, Any]:
        encoded_path = quote(site_path.strip("/"), safe="/")
        return self.get_json(f"/sites/{hostname}:/{encoded_path}")

    def list_drives(self, site_id: str) -> list[dict[str, Any]]:
        return self.get_all_pages(f"/sites/{site_id}/drives?$select=id,name,webUrl,driveType")

    def get_drive_by_name(self, site_id: str, library_name: str) -> dict[str, Any]:
        drives = self.list_drives(site_id)
        target = library_name.strip().lower()
        for drive in drives:
            if drive.get("name", "").strip().lower() == target:
                return drive
        names = ", ".join(d.get("name", "<unnamed>") for d in drives)
        raise GraphError(f"Document library '{library_name}' not found. Available libraries: {names}")

    def get_root_or_path_item(self, drive_id: str, folder_path: str) -> dict[str, Any]:
        cleaned = folder_path.strip("/")
        if not cleaned:
            return self.get_json(f"/drives/{drive_id}/root")
        encoded = quote(cleaned, safe="/")
        return self.get_json(f"/drives/{drive_id}/root:/{encoded}")

    def list_children(self, drive_id: str, item_id: str) -> list[dict[str, Any]]:
        return self.get_all_pages(
            f"/drives/{drive_id}/items/{item_id}/children?$select=id,name,size,eTag,cTag,webUrl,file,folder,parentReference,lastModifiedDateTime"
        )

    def download_item(self, drive_id: str, item_id: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        response = self.request("GET", f"/drives/{drive_id}/items/{item_id}/content", stream=True)
        with destination.open("wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
