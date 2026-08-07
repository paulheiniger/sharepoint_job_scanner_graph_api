from __future__ import annotations

import os
from datetime import timedelta, timezone

from sqlalchemy import select
from sqlalchemy.engine import Engine

from jobscan.quickbooks.client import QuickBooksClient, SCOPE, authorization_url
from jobscan.quickbooks.repository import (
    ensure_tables,
    oauth_states,
    store_connection,
    utc_now,
)
from jobscan.quickbooks.security import (
    QuickBooksStateError,
    build_oauth_state,
    oauth_state_nonce_hash,
    verify_oauth_state,
)


class QuickBooksCompanyMismatchError(ValueError):
    pass


def create_admin_authorization(engine: Engine, *, return_url: str = "") -> str:
    ensure_tables(engine)
    state = build_oauth_state(return_url=return_url)
    payload = verify_oauth_state(state)
    now = utc_now()
    with engine.begin() as connection:
        connection.execute(
            oauth_states.insert().values(
                nonce_hash=oauth_state_nonce_hash(payload["nonce"]),
                expires_at=now + timedelta(seconds=int(payload["exp"]) - int(payload["iat"])),
                consumed_at=None,
                created_at=now,
            )
        )
    return authorization_url(state=state)


def complete_admin_authorization(
    engine: Engine,
    *,
    state: str,
    code: str,
    realm_id: str,
    client: QuickBooksClient | None = None,
) -> dict[str, str]:
    ensure_tables(engine)
    payload = verify_oauth_state(state)
    nonce_hash = oauth_state_nonce_hash(payload["nonce"])
    now = utc_now()
    with engine.begin() as connection:
        row = connection.execute(
            select(oauth_states).where(oauth_states.c.nonce_hash == nonce_hash)
        ).mappings().first()
        expires_at = row["expires_at"] if row else None
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if not row or row["consumed_at"] is not None or not expires_at or expires_at < now:
            raise QuickBooksStateError("QuickBooks OAuth state is expired or already used.")
        connection.execute(
            oauth_states.update()
            .where(oauth_states.c.nonce_hash == nonce_hash)
            .values(consumed_at=now)
        )
    qb = client or QuickBooksClient(realm_id=realm_id)
    tokens = qb.exchange_code(code)
    qb.realm_id = realm_id
    qb.access_token = tokens.access_token
    company = qb.company_info()
    company_name = str(company.get("CompanyName") or company.get("LegalName") or "").strip()
    expected = str(os.getenv("QUICKBOOKS_EXPECTED_COMPANY_NAME") or "Spray-Tec Inc.").strip()
    if expected and _normalized_company_name(company_name) != _normalized_company_name(expected):
        raise QuickBooksCompanyMismatchError(
            f"Authorized QuickBooks company is {company_name or 'unknown'}, not {expected}."
        )
    environment = str(os.getenv("QUICKBOOKS_ENVIRONMENT") or "sandbox").strip().lower()
    store_connection(
        engine,
        realm_id=realm_id,
        company_name=company_name,
        environment=environment,
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        access_token_expires_at=now + timedelta(seconds=tokens.access_expires_in),
        refresh_token_expires_at=now + timedelta(seconds=tokens.refresh_expires_in),
        scope=SCOPE,
    )
    return {
        "realm_id": realm_id,
        "company_name": company_name,
        "status": "connected",
        "return_url": str(payload.get("return_url") or ""),
    }


def _normalized_company_name(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())
