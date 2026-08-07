from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.engine import Engine

from jobscan.business.job_service import _resolve_engine
from jobscan.quickbooks.repository import (
    connections,
    customers,
    ensure_tables,
    job_links,
    payments,
    sync_state,
    transactions,
)


def get_accounting_summary(
    *,
    database_url: str | None = None,
    engine: Engine | None = None,
    customer_query: str = "",
    limit: int = 15,
) -> dict[str, Any]:
    resolved, owns = _resolve_engine(database_url, engine)
    try:
        ensure_tables(resolved)
        connection_record = _connection_metadata(resolved)
        if not connection_record:
            return _unavailable("QuickBooks has not been authorized.")
        statement = select(transactions).where(
            transactions.c.realm_id == connection_record["realm_id"],
            transactions.c.entity_type == "Invoice",
        )
        if customer_query.strip():
            statement = statement.where(
                transactions.c.customer_name.ilike(f"%{customer_query.strip()}%")
            )
        rows = _rows(resolved, statement)
        today = datetime.now(timezone.utc).date()
        open_rows = [row for row in rows if float(row.get("balance") or 0) > 0]
        overdue = [
            row for row in open_rows
            if row.get("due_date") and _as_date(row["due_date"]) < today
        ]
        recent_payments = _rows(
            resolved,
            select(payments)
            .where(payments.c.realm_id == connection_record["realm_id"])
            .order_by(payments.c.txn_date.desc())
            .limit(max(1, min(int(limit), 25))),
        )
        return {
            "schema_version": "spraytec.quickbooks.accounting_summary.v1",
            "as_of": _iso(connection_record.get("last_sync_at")),
            "connection": _safe_connection(connection_record),
            "filters_applied": {"customer_query": customer_query.strip() or None, "limit": limit},
            "headline_metrics": {
                "invoice_count": len(rows),
                "open_invoice_count": len(open_rows),
                "open_accounts_receivable": round(sum(float(row.get("balance") or 0) for row in open_rows), 2),
                "overdue_invoice_count": len(overdue),
                "overdue_accounts_receivable": round(sum(float(row.get("balance") or 0) for row in overdue), 2),
                "recent_payment_total": round(sum(float(row.get("total_amount") or 0) for row in recent_payments), 2),
            },
            "records": [_transaction_record(row) for row in sorted(open_rows, key=_due_sort)[: max(1, min(limit, 25))]],
            "recent_payments": [_payment_record(row) for row in recent_payments],
            "source_tables": ["quickbooks_connections", "quickbooks_sales_transactions", "quickbooks_payments"],
            "data_freshness": _freshness(resolved, connection_record["realm_id"]),
            "warnings": _freshness_warnings(connection_record),
        }
    finally:
        if owns:
            resolved.dispose()


def get_customer_context(
    *,
    database_url: str | None = None,
    engine: Engine | None = None,
    customer_query: str = "",
    job_id: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    if not customer_query.strip() and not job_id.strip():
        raise ValueError("customer_query or job_id is required.")
    resolved, owns = _resolve_engine(database_url, engine)
    try:
        ensure_tables(resolved)
        connection_record = _connection_metadata(resolved)
        if not connection_record:
            return _unavailable("QuickBooks has not been authorized.")
        realm_id = connection_record["realm_id"]
        customer_ids: set[str] = set()
        customer_statement = select(customers).where(customers.c.realm_id == realm_id)
        if customer_query.strip():
            pattern = f"%{customer_query.strip()}%"
            customer_statement = customer_statement.where(or_(
                customers.c.display_name.ilike(pattern),
                customers.c.company_name.ilike(pattern),
                customers.c.fully_qualified_name.ilike(pattern),
            ))
        elif job_id.strip():
            linked = _rows(resolved, select(job_links).where(
                job_links.c.realm_id == realm_id,
                job_links.c.job_id == job_id.strip(),
            ))
            customer_ids.update(str(row["quickbooks_customer_id"]) for row in linked)
            customer_statement = customer_statement.where(customers.c.quickbooks_id.in_(customer_ids or {"__none__"}))
        matched_customers = _rows(resolved, customer_statement.limit(25))
        customer_ids.update(str(row["quickbooks_id"]) for row in matched_customers)
        tx_rows = _rows(resolved, select(transactions).where(
            transactions.c.realm_id == realm_id,
            transactions.c.customer_ref.in_(customer_ids or {"__none__"}),
        ).order_by(transactions.c.txn_date.desc()).limit(max(1, min(limit, 50))))
        payment_rows = _rows(resolved, select(payments).where(
            payments.c.realm_id == realm_id,
            payments.c.customer_ref.in_(customer_ids or {"__none__"}),
        ).order_by(payments.c.txn_date.desc()).limit(max(1, min(limit, 50))))
        return {
            "schema_version": "spraytec.quickbooks.customer_context.v1",
            "as_of": _iso(connection_record.get("last_sync_at")),
            "connection": _safe_connection(connection_record),
            "filters_applied": {"customer_query": customer_query.strip() or None, "job_id": job_id.strip() or None},
            "headline_metrics": {
                "matched_customers": len(matched_customers),
                "transaction_count": len(tx_rows),
                "payment_count": len(payment_rows),
                "open_balance": round(sum(float(row.get("balance") or 0) for row in tx_rows if row.get("entity_type") == "Invoice"), 2),
            },
            "customers": [_customer_record(row) for row in matched_customers],
            "records": [_transaction_record(row) for row in tx_rows],
            "payments": [_payment_record(row) for row in payment_rows],
            "source_tables": ["quickbooks_customers", "quickbooks_sales_transactions", "quickbooks_payments", "quickbooks_job_links"],
            "data_freshness": _freshness(resolved, realm_id),
            "warnings": ([] if matched_customers else ["No matching QuickBooks customer was found. Try the legal company name or create a reviewed job link."]),
        }
    finally:
        if owns:
            resolved.dispose()


def get_accounting_exceptions(
    *,
    database_url: str | None = None,
    engine: Engine | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    summary = get_accounting_summary(database_url=database_url, engine=engine, limit=limit)
    overdue = [record for record in summary.get("records", []) if record.get("is_overdue")]
    summary["schema_version"] = "spraytec.quickbooks.accounting_exceptions.v1"
    summary["records"] = overdue[: max(1, min(limit, 25))]
    summary["headline_metrics"]["exception_count"] = summary["headline_metrics"].get(
        "overdue_invoice_count", len(overdue)
    )
    return summary


def _connection_metadata(engine: Engine) -> dict[str, Any]:
    rows = _rows(engine, select(
        connections.c.realm_id,
        connections.c.company_name,
        connections.c.environment,
        connections.c.status,
        connections.c.last_sync_at,
        connections.c.last_error,
        connections.c.updated_at,
    ).order_by(connections.c.updated_at.desc()).limit(1))
    return rows[0] if rows else {}


def _rows(engine: Engine, statement) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(statement).mappings().all()]


def _safe_connection(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _iso(value) if isinstance(value, datetime) else value for key, value in row.items()}


def _freshness(engine: Engine, realm_id: str) -> dict[str, Any]:
    rows = _rows(engine, select(sync_state).where(sync_state.c.realm_id == realm_id))
    return {
        "entity_sync": [
            {
                "entity_type": row["entity_type"],
                "status": row["status"],
                "last_completed_at": _iso(row.get("last_completed_at")),
                "records_processed": row.get("records_processed") or 0,
            }
            for row in rows
        ]
    }


def _freshness_warnings(connection_record: dict[str, Any]) -> list[str]:
    if not connection_record.get("last_sync_at"):
        return ["QuickBooks is authorized but has not completed its initial synchronization."]
    return []


def _customer_record(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in ("quickbooks_id", "display_name", "company_name", "fully_qualified_name", "active", "balance", "currency", "email", "phone")}


def _transaction_record(row: dict[str, Any]) -> dict[str, Any]:
    due = row.get("due_date")
    balance = float(row.get("balance") or 0)
    return {
        "quickbooks_id": row.get("quickbooks_id"),
        "entity_type": row.get("entity_type"),
        "doc_number": row.get("doc_number"),
        "customer_ref": row.get("customer_ref"),
        "customer_name": row.get("customer_name"),
        "txn_date": _iso(row.get("txn_date")),
        "due_date": _iso(due),
        "total_amount": row.get("total_amount"),
        "balance": row.get("balance"),
        "currency": row.get("currency"),
        "status": row.get("status"),
        "is_overdue": bool(due and balance > 0 and _as_date(due) < datetime.now(timezone.utc).date()),
    }


def _payment_record(row: dict[str, Any]) -> dict[str, Any]:
    return {key: (_iso(row.get(key)) if key == "txn_date" else row.get(key)) for key in ("quickbooks_id", "txn_date", "customer_ref", "customer_name", "total_amount", "unapplied_amount", "currency")}


def _unavailable(message: str) -> dict[str, Any]:
    return {
        "schema_version": "spraytec.quickbooks.unavailable.v1",
        "as_of": "",
        "connection": {"status": "awaiting_administrator_authorization"},
        "filters_applied": {},
        "headline_metrics": {},
        "records": [],
        "source_tables": ["quickbooks_connections"],
        "data_freshness": {},
        "warnings": [message],
    }


def _iso(value: Any) -> str:
    if not value:
        return ""
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _as_date(value: Any):
    if isinstance(value, datetime):
        return value.date()
    return datetime.fromisoformat(str(value)).date()


def _due_sort(row: dict[str, Any]):
    return row.get("due_date") or datetime.max.replace(tzinfo=timezone.utc)
