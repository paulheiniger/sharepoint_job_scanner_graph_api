from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    and_,
    select,
)
from sqlalchemy.engine import Engine

from jobscan.quickbooks.security import decrypt_secret, encrypt_secret


metadata = MetaData()

connections = Table(
    "quickbooks_connections",
    metadata,
    Column("realm_id", String(100), primary_key=True),
    Column("company_name", String(300), nullable=False, default=""),
    Column("environment", String(20), nullable=False),
    Column("access_token_encrypted", Text, nullable=False),
    Column("refresh_token_encrypted", Text, nullable=False),
    Column("access_token_expires_at", DateTime(timezone=True)),
    Column("refresh_token_expires_at", DateTime(timezone=True)),
    Column("scope", Text, nullable=False, default=""),
    Column("status", String(40), nullable=False, default="connected"),
    Column("last_sync_at", DateTime(timezone=True)),
    Column("last_error", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

oauth_states = Table(
    "quickbooks_oauth_states",
    metadata,
    Column("nonce_hash", String(64), primary_key=True),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("consumed_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

customers = Table(
    "quickbooks_customers",
    metadata,
    Column("realm_id", String(100), primary_key=True),
    Column("quickbooks_id", String(100), primary_key=True),
    Column("sync_token", String(50)),
    Column("display_name", String(500)),
    Column("company_name", String(500)),
    Column("fully_qualified_name", String(1000)),
    Column("parent_ref", String(100)),
    Column("active", Boolean),
    Column("balance", Float),
    Column("currency", String(20)),
    Column("email", String(500)),
    Column("phone", String(100)),
    Column("source_created_at", DateTime(timezone=True)),
    Column("source_updated_at", DateTime(timezone=True)),
    Column("synced_at", DateTime(timezone=True), nullable=False),
    Column("raw_json", Text, nullable=False),
)

transactions = Table(
    "quickbooks_sales_transactions",
    metadata,
    Column("realm_id", String(100), primary_key=True),
    Column("entity_type", String(30), primary_key=True),
    Column("quickbooks_id", String(100), primary_key=True),
    Column("sync_token", String(50)),
    Column("txn_date", DateTime(timezone=True)),
    Column("doc_number", String(100)),
    Column("customer_ref", String(100)),
    Column("customer_name", String(500)),
    Column("due_date", DateTime(timezone=True)),
    Column("total_amount", Float),
    Column("balance", Float),
    Column("currency", String(20)),
    Column("status", String(80)),
    Column("linked_transactions_json", Text, nullable=False, default="[]"),
    Column("source_created_at", DateTime(timezone=True)),
    Column("source_updated_at", DateTime(timezone=True)),
    Column("synced_at", DateTime(timezone=True), nullable=False),
    Column("raw_json", Text, nullable=False),
)

payments = Table(
    "quickbooks_payments",
    metadata,
    Column("realm_id", String(100), primary_key=True),
    Column("quickbooks_id", String(100), primary_key=True),
    Column("sync_token", String(50)),
    Column("txn_date", DateTime(timezone=True)),
    Column("customer_ref", String(100)),
    Column("customer_name", String(500)),
    Column("total_amount", Float),
    Column("unapplied_amount", Float),
    Column("currency", String(20)),
    Column("linked_transactions_json", Text, nullable=False, default="[]"),
    Column("source_created_at", DateTime(timezone=True)),
    Column("source_updated_at", DateTime(timezone=True)),
    Column("synced_at", DateTime(timezone=True), nullable=False),
    Column("raw_json", Text, nullable=False),
)

sync_state = Table(
    "quickbooks_sync_state",
    metadata,
    Column("realm_id", String(100), primary_key=True),
    Column("entity_type", String(30), primary_key=True),
    Column("last_source_updated_at", DateTime(timezone=True)),
    Column("last_started_at", DateTime(timezone=True)),
    Column("last_completed_at", DateTime(timezone=True)),
    Column("status", String(30), nullable=False),
    Column("records_processed", Integer, nullable=False, default=0),
    Column("last_error", Text),
)

job_links = Table(
    "quickbooks_job_links",
    metadata,
    Column("realm_id", String(100), primary_key=True),
    Column("quickbooks_customer_id", String(100), primary_key=True),
    Column("job_id", String(200), primary_key=True),
    Column("match_method", String(50), nullable=False),
    Column("confidence", Float),
    Column("reviewed", Boolean, nullable=False, default=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

webhook_events = Table(
    "quickbooks_webhook_events",
    metadata,
    Column("event_hash", String(64), primary_key=True),
    Column("realm_id", String(100)),
    Column("payload_json", Text, nullable=False),
    Column("status", String(30), nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
    Column("processed_at", DateTime(timezone=True)),
    Column("last_error", Text),
)


def ensure_tables(engine: Engine) -> None:
    metadata.create_all(engine)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def upsert(engine: Engine, table: Table, keys: dict[str, Any], values: dict[str, Any]) -> None:
    condition = and_(*(table.c[name] == value for name, value in keys.items()))
    with engine.begin() as connection:
        exists = connection.execute(select(table).where(condition).limit(1)).first()
        if exists:
            connection.execute(table.update().where(condition).values(**values))
        else:
            connection.execute(table.insert().values(**keys, **values))


def store_connection(
    engine: Engine,
    *,
    realm_id: str,
    company_name: str,
    environment: str,
    access_token: str,
    refresh_token: str,
    access_token_expires_at: datetime,
    refresh_token_expires_at: datetime,
    scope: str,
) -> None:
    now = utc_now()
    existing = get_connection(engine, realm_id=realm_id, decrypt_tokens=False)
    upsert(
        engine,
        connections,
        {"realm_id": realm_id},
        {
            "company_name": company_name,
            "environment": environment,
            "access_token_encrypted": encrypt_secret(access_token),
            "refresh_token_encrypted": encrypt_secret(refresh_token),
            "access_token_expires_at": access_token_expires_at,
            "refresh_token_expires_at": refresh_token_expires_at,
            "scope": scope,
            "status": "connected",
            "last_error": None,
            "created_at": existing.get("created_at") if existing else now,
            "updated_at": now,
        },
    )


def get_connection(engine: Engine, *, realm_id: str = "", decrypt_tokens: bool = True) -> dict[str, Any]:
    statement = select(connections)
    if realm_id:
        statement = statement.where(connections.c.realm_id == realm_id)
    statement = statement.order_by(connections.c.updated_at.desc()).limit(1)
    with engine.connect() as connection:
        row = connection.execute(statement).mappings().first()
    result = dict(row or {})
    if result and decrypt_tokens:
        result["access_token"] = decrypt_secret(result.pop("access_token_encrypted"))
        result["refresh_token"] = decrypt_secret(result.pop("refresh_token_encrypted"))
    return result


def serialize_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)


def parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_date(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
