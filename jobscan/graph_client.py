from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import msal
import requests

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
TOKEN_REFRESH_SKEW_SECONDS = 300


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
        max_retries: int = 5,
    ) -> None:
        self.tenant_id = tenant_id or os.getenv("MS_TENANT_ID")
        self.client_id = client_id or os.getenv("MS_CLIENT_ID")
        self.client_secret = client_secret or os.getenv("MS_CLIENT_SECRET")
        self.timeout = timeout
        self.max_retries = max_retries
        missing = [name for name, value in {
            "MS_TENANT_ID": self.tenant_id,
            "MS_CLIENT_ID": self.client_id,
            "MS_CLIENT_SECRET": self.client_secret,
        }.items() if not value]
        if missing:
            raise GraphError(f"Missing Microsoft Graph env vars: {', '.join(missing)}")

        self._token: str | None = None
        self.token_acquired_at: float | None = None
        self.expires_in: int | None = None

    def _token_expiring_soon(self) -> bool:
        if not self._token or self.token_acquired_at is None or self.expires_in is None:
            return True
        expires_at = self.token_acquired_at + self.expires_in
        return time.time() >= expires_at - TOKEN_REFRESH_SKEW_SECONDS

    def token(self, *, force_refresh: bool = False) -> str:
        if self._token and not force_refresh and not self._token_expiring_soon():
            return self._token
        if self._token:
            print("Refreshing Microsoft Graph token")
        app = msal.ConfidentialClientApplication(
            client_id=self.client_id,
            authority=f"https://login.microsoftonline.com/{self.tenant_id}",
            client_credential=self.client_secret,
        )
        result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
        if "access_token" not in result:
            raise GraphError(f"Could not acquire Microsoft Graph token: {json.dumps(result, indent=2)}")
        self._token = result["access_token"]
        self.token_acquired_at = time.time()
        try:
            self.expires_in = int(result.get("expires_in") or 3599)
        except (TypeError, ValueError):
            self.expires_in = 3599
        return self._token

    def _headers(self, *, force_refresh: bool = False) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token(force_refresh=force_refresh)}", "Accept": "application/json"}

    def _request_once(self, method: str, url: str, *, force_refresh: bool = False, **kwargs: Any) -> requests.Response:
        return requests.request(method, url, headers=self._headers(force_refresh=force_refresh), timeout=self.timeout, **kwargs)

    def _is_invalid_auth_token(self, response: requests.Response) -> bool:
        if response.status_code != 401:
            return False
        try:
            detail = response.json()
        except ValueError:
            return "InvalidAuthenticationToken" in response.text
        error = detail.get("error") if isinstance(detail, dict) else None
        if not isinstance(error, dict):
            return False
        return error.get("code") == "InvalidAuthenticationToken"

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        if url.startswith("/"):
            url = GRAPH_ROOT + url
        retry_statuses = {429, 500, 502, 503, 504}
        last_response: requests.Response | None = None
        for attempt in range(self.max_retries + 1):
            response = self._request_once(method, url, **kwargs)
            if self._is_invalid_auth_token(response):
                response.close()
                response = self._request_once(method, url, force_refresh=True, **kwargs)
            if response.status_code not in retry_statuses:
                if response.status_code >= 400:
                    try:
                        detail = response.json()
                    except ValueError:
                        detail = response.text[:1000]
                    raise GraphError(f"Graph {method} {url} failed with {response.status_code}: {detail}")
                return response
            last_response = response
            if attempt >= self.max_retries:
                break
            retry_after = response.headers.get("Retry-After")
            try:
                sleep_seconds = float(retry_after) if retry_after else min(2 ** attempt, 30)
            except ValueError:
                sleep_seconds = min(2 ** attempt, 30)
            response.close()
            time.sleep(sleep_seconds)

        detail: Any
        if last_response is None:
            detail = "No response"
            status_code = "unknown"
        else:
            status_code = last_response.status_code
            try:
                detail = last_response.json()
            except ValueError:
                detail = last_response.text[:1000]
        raise GraphError(f"Graph {method} {url} failed after retries with {status_code}: {detail}")

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
        content_type = response.headers.get("Content-Type", "").lower()
        if "text/html" in content_type:
            response.close()
            raise GraphError("Graph returned HTML instead of file content.")
        with destination.open("wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
