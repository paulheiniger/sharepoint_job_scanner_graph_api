from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine


MEMORY_TABLE = "estimator_memory"
MEMORY_STATUSES = {"approved", "pending", "disabled"}
DEFAULT_MEMORY_LIMIT = 12


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid4())


def _dialect_name(engine: Engine | Any) -> str:
    dialect = getattr(engine, "dialect", None)
    if dialect is None and hasattr(engine, "engine"):
        dialect = getattr(engine.engine, "dialect", None)
    return str(getattr(dialect, "name", "") or "")


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else [], sort_keys=True, default=str)


def _json_expr(dialect: str, param_name: str) -> str:
    return f"CAST(:{param_name} AS JSONB)" if dialect.startswith("postgres") else f":{param_name}"


def _json_value(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return value
    if value in (None, ""):
        return []
    try:
        return json.loads(str(value))
    except Exception:
        return value


def normalize_memory_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def normalize_memory_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")


def ensure_estimator_memory_table(engine: Engine) -> None:
    dialect = _dialect_name(engine)
    if dialect.startswith("postgres"):
        statements = [
            """
            CREATE TABLE IF NOT EXISTS estimator_memory (
                memory_id UUID PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                status TEXT NOT NULL DEFAULT 'pending',
                priority TEXT NOT NULL DEFAULT 'medium',
                template_type TEXT,
                decision_id TEXT,
                template_bucket TEXT,
                product_or_system TEXT,
                applies_when JSONB DEFAULT '{}'::jsonb,
                guidance TEXT NOT NULL,
                rationale TEXT,
                source_type TEXT,
                source_session_id UUID,
                source_edit_id UUID,
                approved_by TEXT,
                approved_at TIMESTAMPTZ,
                usage_count INTEGER NOT NULL DEFAULT 0,
                last_used_at TIMESTAMPTZ
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_estimator_memory_status_template ON estimator_memory(status, template_type)",
            "CREATE INDEX IF NOT EXISTS idx_estimator_memory_bucket ON estimator_memory(template_bucket)",
        ]
    else:
        statements = [
            """
            CREATE TABLE IF NOT EXISTS estimator_memory (
                memory_id TEXT PRIMARY KEY,
                created_at TIMESTAMP,
                updated_at TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'pending',
                priority TEXT NOT NULL DEFAULT 'medium',
                template_type TEXT,
                decision_id TEXT,
                template_bucket TEXT,
                product_or_system TEXT,
                applies_when TEXT,
                guidance TEXT NOT NULL,
                rationale TEXT,
                source_type TEXT,
                source_session_id TEXT,
                source_edit_id TEXT,
                approved_by TEXT,
                approved_at TIMESTAMP,
                usage_count INTEGER NOT NULL DEFAULT 0,
                last_used_at TIMESTAMP
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_estimator_memory_status_template ON estimator_memory(status, template_type)",
            "CREATE INDEX IF NOT EXISTS idx_estimator_memory_bucket ON estimator_memory(template_bucket)",
        ]
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def upsert_estimator_memory(
    engine: Engine,
    *,
    guidance: str,
    template_type: str = "",
    decision_id: str = "",
    template_bucket: str = "",
    product_or_system: str = "",
    applies_when: dict[str, Any] | None = None,
    rationale: str = "",
    source_type: str = "estimator_correction",
    source_session_id: str | None = None,
    source_edit_id: str | None = None,
    status: str = "pending",
    priority: str = "medium",
    approved_by: str = "",
    memory_id: str | None = None,
) -> str:
    ensure_estimator_memory_table(engine)
    status_value = normalize_memory_token(status) or "pending"
    if status_value not in MEMORY_STATUSES:
        status_value = "pending"
    priority_value = normalize_memory_token(priority) or "medium"
    resolved_id = memory_id or _new_id()
    now = _now()
    approved_at = now if status_value == "approved" and approved_by else None
    dialect = _dialect_name(engine)
    params = {
        "memory_id": resolved_id,
        "created_at": now,
        "updated_at": now,
        "status": status_value,
        "priority": priority_value,
        "template_type": normalize_memory_token(template_type),
        "decision_id": normalize_memory_token(decision_id),
        "template_bucket": normalize_memory_token(template_bucket),
        "product_or_system": normalize_memory_text(product_or_system),
        "applies_when": _json_dumps(applies_when or {}),
        "guidance": normalize_memory_text(guidance),
        "rationale": normalize_memory_text(rationale),
        "source_type": normalize_memory_token(source_type),
        "source_session_id": source_session_id,
        "source_edit_id": source_edit_id,
        "approved_by": approved_by,
        "approved_at": approved_at,
    }
    if not params["guidance"]:
        raise ValueError("Estimator memory guidance is required.")
    if dialect.startswith("postgres"):
        conflict_sql = """
                ON CONFLICT (memory_id) DO UPDATE SET
                    updated_at = EXCLUDED.updated_at,
                    status = EXCLUDED.status,
                    priority = EXCLUDED.priority,
                    template_type = EXCLUDED.template_type,
                    decision_id = EXCLUDED.decision_id,
                    template_bucket = EXCLUDED.template_bucket,
                    product_or_system = EXCLUDED.product_or_system,
                    applies_when = EXCLUDED.applies_when,
                    guidance = EXCLUDED.guidance,
                    rationale = EXCLUDED.rationale,
                    source_type = EXCLUDED.source_type,
                    source_session_id = EXCLUDED.source_session_id,
                    source_edit_id = EXCLUDED.source_edit_id,
                    approved_by = EXCLUDED.approved_by,
                    approved_at = EXCLUDED.approved_at
        """
    else:
        conflict_sql = """
                ON CONFLICT(memory_id) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    status = excluded.status,
                    priority = excluded.priority,
                    template_type = excluded.template_type,
                    decision_id = excluded.decision_id,
                    template_bucket = excluded.template_bucket,
                    product_or_system = excluded.product_or_system,
                    applies_when = excluded.applies_when,
                    guidance = excluded.guidance,
                    rationale = excluded.rationale,
                    source_type = excluded.source_type,
                    source_session_id = excluded.source_session_id,
                    source_edit_id = excluded.source_edit_id,
                    approved_by = excluded.approved_by,
                    approved_at = excluded.approved_at
        """
    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                INSERT INTO estimator_memory (
                    memory_id, created_at, updated_at, status, priority, template_type,
                    decision_id, template_bucket, product_or_system, applies_when,
                    guidance, rationale, source_type, source_session_id, source_edit_id,
                    approved_by, approved_at
                )
                VALUES (
                    :memory_id, :created_at, :updated_at, :status, :priority, :template_type,
                    :decision_id, :template_bucket, :product_or_system, {_json_expr(dialect, "applies_when")},
                    :guidance, :rationale, :source_type, :source_session_id, :source_edit_id,
                    :approved_by, :approved_at
                )
                {conflict_sql}
                """
            ),
            params,
        )
    return resolved_id


def approved_memory_frame(engine: Engine) -> pd.DataFrame:
    ensure_estimator_memory_table(engine)
    with engine.connect() as connection:
        frame = pd.read_sql_query(
            text(
                """
                SELECT *
                FROM estimator_memory
                WHERE status = 'approved'
                ORDER BY
                    CASE priority
                        WHEN 'high' THEN 0
                        WHEN 'medium' THEN 1
                        WHEN 'low' THEN 2
                        ELSE 3
                    END,
                    updated_at DESC
                """
            ),
            connection,
        )
    if "applies_when" in frame.columns:
        frame["applies_when"] = frame["applies_when"].map(_json_value)
    return frame


def estimator_memory_frame(engine: Engine, *, status: str | None = None, limit: int = 500) -> pd.DataFrame:
    ensure_estimator_memory_table(engine)
    params: dict[str, Any] = {"limit": int(limit or 500)}
    where = ""
    if status:
        params["status"] = normalize_memory_token(status)
        where = "WHERE status = :status"
    with engine.connect() as connection:
        frame = pd.read_sql_query(
            text(
                f"""
                SELECT *
                FROM estimator_memory
                {where}
                ORDER BY
                    CASE priority
                        WHEN 'high' THEN 0
                        WHEN 'medium' THEN 1
                        WHEN 'low' THEN 2
                        ELSE 3
                    END,
                    updated_at DESC
                LIMIT :limit
                """
            ),
            connection,
            params=params,
        )
    if "applies_when" in frame.columns:
        frame["applies_when"] = frame["applies_when"].map(_json_value)
    return estimator_memory_from_rows(frame)


def update_estimator_memory_status(
    engine: Engine,
    memory_ids: list[str],
    *,
    status: str,
    approved_by: str = "",
) -> int:
    ensure_estimator_memory_table(engine)
    status_value = normalize_memory_token(status)
    if status_value not in MEMORY_STATUSES:
        raise ValueError(f"Unsupported estimator memory status: {status}")
    ids = [str(memory_id) for memory_id in memory_ids if str(memory_id or "").strip()]
    if not ids:
        return 0
    approved_at = _now() if status_value == "approved" else None
    with engine.begin() as connection:
        count = 0
        for memory_id in ids:
            result = connection.execute(
                text(
                    """
                    UPDATE estimator_memory
                    SET status = :status,
                        updated_at = :updated_at,
                        approved_by = CASE WHEN :approved_by <> '' THEN :approved_by ELSE approved_by END,
                        approved_at = CASE WHEN :status = 'approved' THEN :approved_at ELSE approved_at END
                    WHERE memory_id = :memory_id
                    """
                ),
                {
                    "memory_id": memory_id,
                    "status": status_value,
                    "updated_at": _now(),
                    "approved_by": approved_by,
                    "approved_at": approved_at,
                },
            )
            count += int(result.rowcount or 0)
    return count


def estimator_memory_from_rows(rows: list[dict[str, Any]] | pd.DataFrame | None) -> pd.DataFrame:
    if isinstance(rows, pd.DataFrame):
        frame = rows.copy()
    else:
        frame = pd.DataFrame(rows or [])
    if frame.empty:
        return frame
    for column in ("status", "priority", "template_type", "decision_id", "template_bucket", "source_type"):
        if column in frame.columns:
            frame[column] = frame[column].map(normalize_memory_token)
    if "guidance" in frame.columns:
        frame["guidance"] = frame["guidance"].map(normalize_memory_text)
    return frame


def relevant_memory_rows(
    memory: pd.DataFrame | None,
    *,
    scope: dict[str, Any] | None = None,
    template_type: str = "",
    decision_buckets: list[str] | None = None,
    limit: int = DEFAULT_MEMORY_LIMIT,
) -> list[dict[str, Any]]:
    if memory is None or memory.empty:
        return []
    scope = scope or {}
    resolved_template = normalize_memory_token(template_type or scope.get("template_type") or scope.get("division"))
    buckets = {normalize_memory_token(value) for value in (decision_buckets or []) if normalize_memory_token(value)}
    scope_text = " ".join(str(value or "") for value in scope.values()).lower()
    rows = estimator_memory_from_rows(memory).to_dict(orient="records")
    ranked: list[tuple[tuple[int, int, int, str], dict[str, Any]]] = []
    for row in rows:
        if row.get("status") and row.get("status") != "approved":
            continue
        row_template = normalize_memory_token(row.get("template_type"))
        if row_template and resolved_template and row_template != resolved_template:
            continue
        bucket = normalize_memory_token(row.get("template_bucket"))
        decision_id = normalize_memory_token(row.get("decision_id"))
        product_or_system = str(row.get("product_or_system") or "").lower()
        applies_when = row.get("applies_when") if isinstance(row.get("applies_when"), dict) else {}
        keyword_terms = [str(term).lower() for term in applies_when.get("keywords", [])] if isinstance(applies_when, dict) else []
        bucket_match = bool(bucket and bucket in buckets) or bool(decision_id and decision_id in buckets)
        keyword_match = bool(product_or_system and product_or_system.lower() in scope_text) or any(term and term in scope_text for term in keyword_terms)
        generic_template_rule = bool(row_template and row_template == resolved_template and not bucket and not keyword_terms and not product_or_system)
        if not (bucket_match or keyword_match or generic_template_rule):
            continue
        priority_rank = {"high": 0, "medium": 1, "low": 2}.get(normalize_memory_token(row.get("priority")), 3)
        specificity = 0 if bucket_match else 1 if keyword_match else 2
        updated_at = str(row.get("updated_at") or row.get("created_at") or "")
        ranked.append(((priority_rank, specificity, 0 if row_template else 1, updated_at), row))
    ranked.sort(key=lambda item: item[0])
    out: list[dict[str, Any]] = []
    for _, row in ranked[: max(0, int(limit or DEFAULT_MEMORY_LIMIT))]:
        out.append(
            {
                "memory_id": row.get("memory_id"),
                "priority": row.get("priority") or "medium",
                "template_type": row.get("template_type") or "",
                "decision_id": row.get("decision_id") or "",
                "template_bucket": row.get("template_bucket") or "",
                "product_or_system": row.get("product_or_system") or "",
                "guidance": row.get("guidance") or "",
                "rationale": row.get("rationale") or "",
                "source_type": row.get("source_type") or "",
            }
        )
    return out
