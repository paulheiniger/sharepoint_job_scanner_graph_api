from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class QuickBooksConfigurationError(RuntimeError):
    pass


class QuickBooksStateError(ValueError):
    pass


def _required_env(name: str) -> str:
    value = str(os.getenv(name) or "").strip()
    if not value:
        raise QuickBooksConfigurationError(f"{name} is required.")
    return value


def encrypt_secret(value: str) -> str:
    key = _required_env("QUICKBOOKS_TOKEN_ENCRYPTION_KEY").encode("ascii")
    try:
        return Fernet(key).encrypt(value.encode("utf-8")).decode("ascii")
    except (ValueError, TypeError) as exc:
        raise QuickBooksConfigurationError(
            "QUICKBOOKS_TOKEN_ENCRYPTION_KEY must be a valid Fernet key."
        ) from exc


def decrypt_secret(value: str) -> str:
    key = _required_env("QUICKBOOKS_TOKEN_ENCRYPTION_KEY").encode("ascii")
    try:
        return Fernet(key).decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as exc:
        raise QuickBooksConfigurationError("Stored QuickBooks token cannot be decrypted.") from exc


def build_oauth_state(*, return_url: str = "", ttl_seconds: int = 900) -> str:
    now = int(time.time())
    payload = {
        "nonce": secrets.token_urlsafe(24),
        "iat": now,
        "exp": now + max(60, min(int(ttl_seconds), 1800)),
        "return_url": return_url,
    }
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(
        _required_env("QUICKBOOKS_OAUTH_STATE_SECRET").encode("utf-8"),
        body.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{body}.{_b64url(signature)}"


def verify_oauth_state(state: str, *, now: int | None = None) -> dict[str, Any]:
    try:
        body, supplied_signature = state.split(".", 1)
        expected = hmac.new(
            _required_env("QUICKBOOKS_OAUTH_STATE_SECRET").encode("utf-8"),
            body.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(_b64url(expected), supplied_signature):
            raise QuickBooksStateError("QuickBooks OAuth state signature is invalid.")
        payload = json.loads(_b64url_decode(body))
    except QuickBooksStateError:
        raise
    except Exception as exc:
        raise QuickBooksStateError("QuickBooks OAuth state is invalid.") from exc
    current = int(time.time()) if now is None else int(now)
    if current > int(payload.get("exp") or 0):
        raise QuickBooksStateError("QuickBooks OAuth state has expired.")
    if not str(payload.get("nonce") or "").strip():
        raise QuickBooksStateError("QuickBooks OAuth state has no nonce.")
    return payload


def oauth_state_nonce_hash(nonce: str) -> str:
    return hashlib.sha256(nonce.encode("utf-8")).hexdigest()


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
