from __future__ import annotations

from datetime import timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine

from jobscan.quickbooks.client import QuickBooksClient
from jobscan.quickbooks.repository import (
    connections,
    customers,
    ensure_tables,
    get_connection,
    parse_date,
    parse_datetime,
    payments,
    serialize_json,
    store_connection,
    sync_state,
    transactions,
    upsert,
    utc_now,
)


SYNC_ENTITIES = ("Customer", "Estimate", "Invoice", "Payment", "CreditMemo")


def sync_quickbooks(
    engine: Engine,
    *,
    realm_id: str = "",
    full: bool = False,
    client: QuickBooksClient | None = None,
) -> dict[str, Any]:
    ensure_tables(engine)
    connection_record = get_connection(engine, realm_id=realm_id)
    if not connection_record:
        raise RuntimeError("QuickBooks is not authorized.")
    qb = client or QuickBooksClient(
        realm_id=connection_record["realm_id"],
        access_token=connection_record["access_token"],
        environment=connection_record["environment"],
    )
    now = utc_now()
    access_expires_at = connection_record.get("access_token_expires_at")
    if access_expires_at is not None and access_expires_at.tzinfo is None:
        access_expires_at = access_expires_at.replace(tzinfo=timezone.utc)
    if access_expires_at and access_expires_at <= now + timedelta(minutes=5):
        refreshed = qb.refresh(connection_record["refresh_token"])
        qb.access_token = refreshed.access_token
        store_connection(
            engine,
            realm_id=connection_record["realm_id"],
            company_name=connection_record["company_name"],
            environment=connection_record["environment"],
            access_token=refreshed.access_token,
            refresh_token=refreshed.refresh_token,
            access_token_expires_at=now + timedelta(seconds=refreshed.access_expires_in),
            refresh_token_expires_at=now + timedelta(seconds=refreshed.refresh_expires_in),
            scope=connection_record["scope"],
        )
    counts: dict[str, int] = {}
    for entity in SYNC_ENTITIES:
        started = utc_now()
        cursor = None if full else _entity_cursor(engine, connection_record["realm_id"], entity)
        if cursor is not None and cursor.tzinfo is None:
            cursor = cursor.replace(tzinfo=timezone.utc)
        # A small overlap makes the cursor robust to equal timestamps and clock skew.
        updated_after = (cursor - timedelta(minutes=5)).isoformat() if cursor else ""
        _write_state(
            engine,
            connection_record["realm_id"],
            entity,
            status="running",
            started=started,
            cursor=cursor,
        )
        processed = 0
        latest = cursor
        try:
            for record in qb.query_entities(entity, updated_after=updated_after):
                _store_entity(engine, connection_record["realm_id"], entity, record)
                source_updated = parse_datetime((record.get("MetaData") or {}).get("LastUpdatedTime"))
                if source_updated and (latest is None or source_updated > latest):
                    latest = source_updated
                processed += 1
            _write_state(
                engine,
                connection_record["realm_id"],
                entity,
                status="complete",
                started=started,
                completed=utc_now(),
                processed=processed,
                cursor=latest,
            )
            counts[entity] = processed
        except Exception as exc:
            _write_state(
                engine,
                connection_record["realm_id"],
                entity,
                status="failed",
                started=started,
                completed=utc_now(),
                processed=processed,
                cursor=latest,
                error=str(exc)[:2000],
            )
            raise
    with engine.begin() as db:
        db.execute(
            connections.update()
            .where(connections.c.realm_id == connection_record["realm_id"])
            .values(last_sync_at=utc_now(), last_error=None, status="connected", updated_at=utc_now())
        )
    return {"realm_id": connection_record["realm_id"], "status": "complete", "records": counts}


def _entity_cursor(engine: Engine, realm_id: str, entity: str):
    with engine.connect() as connection:
        return connection.execute(
            select(sync_state.c.last_source_updated_at).where(
                sync_state.c.realm_id == realm_id,
                sync_state.c.entity_type == entity,
            )
        ).scalar_one_or_none()


def _write_state(
    engine: Engine,
    realm_id: str,
    entity: str,
    *,
    status: str,
    started=None,
    completed=None,
    processed: int = 0,
    cursor=None,
    error: str | None = None,
) -> None:
    upsert(engine, sync_state, {"realm_id": realm_id, "entity_type": entity}, {
        "last_source_updated_at": cursor,
        "last_started_at": started,
        "last_completed_at": completed,
        "status": status,
        "records_processed": processed,
        "last_error": error,
    })


def _store_entity(engine: Engine, realm_id: str, entity: str, record: dict[str, Any]) -> None:
    metadata = record.get("MetaData") or {}
    common = {
        "sync_token": str(record.get("SyncToken") or ""),
        "source_created_at": parse_datetime(metadata.get("CreateTime")),
        "source_updated_at": parse_datetime(metadata.get("LastUpdatedTime")),
        "synced_at": utc_now(),
        "raw_json": serialize_json(record),
    }
    record_id = str(record.get("Id") or "").strip()
    if not record_id:
        return
    currency = str((record.get("CurrencyRef") or {}).get("value") or "")
    customer_ref = record.get("CustomerRef") or {}
    if entity == "Customer":
        upsert(engine, customers, {"realm_id": realm_id, "quickbooks_id": record_id}, {
            **common,
            "display_name": record.get("DisplayName"),
            "company_name": record.get("CompanyName"),
            "fully_qualified_name": record.get("FullyQualifiedName"),
            "parent_ref": str((record.get("ParentRef") or {}).get("value") or ""),
            "active": record.get("Active"),
            "balance": _float(record.get("Balance")),
            "currency": currency,
            "email": (record.get("PrimaryEmailAddr") or {}).get("Address"),
            "phone": (record.get("PrimaryPhone") or {}).get("FreeFormNumber"),
        })
    elif entity == "Payment":
        upsert(engine, payments, {"realm_id": realm_id, "quickbooks_id": record_id}, {
            **common,
            "txn_date": parse_date(record.get("TxnDate")),
            "customer_ref": str(customer_ref.get("value") or ""),
            "customer_name": customer_ref.get("name"),
            "total_amount": _float(record.get("TotalAmt")),
            "unapplied_amount": _float(record.get("UnappliedAmt")),
            "currency": currency,
            "linked_transactions_json": serialize_json(_linked_transactions(record)),
        })
    else:
        upsert(engine, transactions, {
            "realm_id": realm_id,
            "entity_type": entity,
            "quickbooks_id": record_id,
        }, {
            **common,
            "txn_date": parse_date(record.get("TxnDate")),
            "doc_number": record.get("DocNumber"),
            "customer_ref": str(customer_ref.get("value") or ""),
            "customer_name": customer_ref.get("name"),
            "due_date": parse_date(record.get("DueDate")),
            "total_amount": _float(record.get("TotalAmt")),
            "balance": _float(record.get("Balance")),
            "currency": currency,
            "status": record.get("TxnStatus") or record.get("EmailStatus"),
            "linked_transactions_json": serialize_json(_linked_transactions(record)),
        })


def _linked_transactions(record: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for line in record.get("Line") or []:
        result.extend(line.get("LinkedTxn") or [])
    result.extend(record.get("LinkedTxn") or [])
    return result


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
