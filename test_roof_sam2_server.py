from __future__ import annotations

from fastapi import HTTPException
import pytest
from starlette.requests import Request

from services.roof_sam2.server import _require_api_key


def _request(authorization: str = "") -> Request:
    headers = []
    if authorization:
        headers.append((b"authorization", authorization.encode("utf-8")))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/segment",
            "headers": headers,
        }
    )


def test_sam2_api_key_is_optional_for_loopback_only_service(monkeypatch) -> None:
    monkeypatch.delenv("SAM2_API_KEY", raising=False)

    _require_api_key(_request())


def test_sam2_api_key_rejects_missing_or_invalid_bearer(monkeypatch) -> None:
    monkeypatch.setenv("SAM2_API_KEY", "sam2-test-secret")

    with pytest.raises(HTTPException) as missing:
        _require_api_key(_request())
    with pytest.raises(HTTPException) as invalid:
        _require_api_key(_request("Bearer wrong"))

    assert missing.value.status_code == 401
    assert invalid.value.status_code == 401
    _require_api_key(_request("Bearer sam2-test-secret"))
