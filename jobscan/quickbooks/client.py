from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable
from urllib.parse import urlencode

import requests


AUTHORIZATION_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
SCOPE = "com.intuit.quickbooks.accounting"


class QuickBooksAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class OAuthTokens:
    access_token: str
    refresh_token: str
    access_expires_in: int
    refresh_expires_in: int
    token_type: str


def authorization_url(*, state: str) -> str:
    params = {
        "client_id": _required("QUICKBOOKS_CLIENT_ID"),
        "redirect_uri": _required("QUICKBOOKS_REDIRECT_URI"),
        "response_type": "code",
        "scope": SCOPE,
        "state": state,
    }
    return f"{AUTHORIZATION_URL}?{urlencode(params)}"


class QuickBooksClient:
    def __init__(
        self,
        *,
        realm_id: str = "",
        access_token: str = "",
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 30.0,
        environment: str | None = None,
    ) -> None:
        self.realm_id = str(realm_id).strip()
        self.access_token = str(access_token).strip()
        self.session = session or requests.Session()
        self.sleep = sleep
        self.timeout = timeout
        selected = str(environment or os.getenv("QUICKBOOKS_ENVIRONMENT") or "sandbox").lower()
        self.base_url = (
            "https://quickbooks.api.intuit.com"
            if selected == "production"
            else "https://sandbox-quickbooks.api.intuit.com"
        )

    def exchange_code(self, code: str) -> OAuthTokens:
        return self._token_request({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _required("QUICKBOOKS_REDIRECT_URI"),
        })

    def refresh(self, refresh_token: str) -> OAuthTokens:
        return self._token_request({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        })

    def company_info(self) -> dict[str, Any]:
        payload = self._request("GET", f"/v3/company/{self.realm_id}/companyinfo/{self.realm_id}")
        return dict(payload.get("CompanyInfo") or {})

    def query_entities(
        self,
        entity: str,
        *,
        updated_after: str = "",
        page_size: int = 1000,
    ) -> Iterable[dict[str, Any]]:
        start = 1
        size = max(1, min(int(page_size), 1000))
        while True:
            where = ""
            if updated_after:
                escaped = updated_after.replace("'", "\\'")
                where = f" WHERE MetaData.LastUpdatedTime > '{escaped}'"
            query = f"SELECT * FROM {entity}{where} STARTPOSITION {start} MAXRESULTS {size}"
            payload = self._request(
                "GET",
                f"/v3/company/{self.realm_id}/query",
                params={"query": query, "minorversion": "75"},
            )
            query_response = dict(payload.get("QueryResponse") or {})
            rows = list(query_response.get(entity) or [])
            yield from rows
            if len(rows) < size:
                break
            start += len(rows)

    def _token_request(self, data: dict[str, str]) -> OAuthTokens:
        response = self.session.post(
            TOKEN_URL,
            data=data,
            auth=(_required("QUICKBOOKS_CLIENT_ID"), _required("QUICKBOOKS_CLIENT_SECRET")),
            headers={"Accept": "application/json"},
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise QuickBooksAPIError(f"QuickBooks token request failed ({response.status_code}).")
        payload = response.json()
        return OAuthTokens(
            access_token=str(payload["access_token"]),
            refresh_token=str(payload["refresh_token"]),
            access_expires_in=int(payload.get("expires_in") or 3600),
            refresh_expires_in=int(payload.get("x_refresh_token_expires_in") or 8_726_400),
            token_type=str(payload.get("token_type") or "bearer"),
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if not self.realm_id or not self.access_token:
            raise QuickBooksAPIError("QuickBooks realm and access token are required.")
        headers = dict(kwargs.pop("headers", {}))
        headers.update({"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"})
        last_status = 0
        for attempt in range(4):
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                timeout=self.timeout,
                **kwargs,
            )
            last_status = response.status_code
            if response.status_code < 400:
                return dict(response.json())
            if response.status_code not in {429, 500, 502, 503, 504} or attempt == 3:
                break
            retry_after = response.headers.get("Retry-After")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else min(8.0, 2.0**attempt)
            self.sleep(delay)
        raise QuickBooksAPIError(f"QuickBooks request failed ({last_status}).")


def _required(name: str) -> str:
    value = str(os.getenv(name) or "").strip()
    if not value:
        raise QuickBooksAPIError(f"{name} is required.")
    return value
