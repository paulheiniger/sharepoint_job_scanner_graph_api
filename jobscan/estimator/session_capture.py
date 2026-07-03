from __future__ import annotations

import argparse
import json
import os
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from .evidence_export import sanitize_for_export
from .workbench import recalculate_workbench_tables, summarize_workbench_totals

DEFAULT_SESSION_EXPORT_DIR = Path("output/estimator_session_exports")
DECISION_GRAPH_VERSION = "decision_graph_v1"

SESSION_TABLES = [
    "estimator_sessions",
    "estimator_scope_interpretations",
    "estimator_decision_proposals",
    "estimator_decision_edits",
    "estimator_final_decisions",
    "estimator_session_artifacts",
]


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid4())


def _dialect_name(engine: Engine | Any) -> str:
    dialect = getattr(engine, "dialect", None)
    if dialect is None and hasattr(engine, "engine"):
        dialect = getattr(engine.engine, "dialect", None)
    return str(getattr(dialect, "name", "") or "")


def _jsonable(value: Any) -> Any:
    return sanitize_for_export(value, excel=False)


def _json_dumps(value: Any) -> str:
    return json.dumps(_jsonable(value if value is not None else {}), sort_keys=True, default=str)


def _json_expr(dialect: str, param_name: str) -> str:
    return f"CAST(:{param_name} AS JSONB)" if dialect.startswith("postgres") else f":{param_name}"


def _maybe_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value in (None, ""):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _rows_from_result(result: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in result.mappings().all()]


def ensure_estimator_session_tables(engine: Engine) -> None:
    """Create estimator session capture tables if they do not already exist.

    The checked-in migration is Postgres-first. This helper also supports SQLite
    so unit tests and local smoke runs can exercise the same persistence API.
    """

    dialect = _dialect_name(engine)
    if dialect.startswith("postgres"):
        statements = [
            """
            CREATE TABLE IF NOT EXISTS estimator_sessions (
                session_id UUID PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                division TEXT,
                template_type TEXT,
                customer TEXT,
                job_name TEXT,
                site_address TEXT,
                raw_input_notes TEXT,
                input_source_type TEXT,
                photos_present BOOLEAN DEFAULT false,
                source_file_ids JSONB DEFAULT '[]'::jsonb,
                ai_model TEXT,
                estimate_status TEXT,
                exported_workbook_path TEXT,
                exported_at TIMESTAMPTZ
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS estimator_scope_interpretations (
                interpretation_id UUID PRIMARY KEY,
                session_id UUID NOT NULL REFERENCES estimator_sessions(session_id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                parsed_scope JSONB DEFAULT '{}'::jsonb,
                deterministic_scope JSONB DEFAULT '{}'::jsonb,
                assumptions JSONB DEFAULT '{}'::jsonb,
                missing_questions JSONB DEFAULT '[]'::jsonb,
                confidence_by_field JSONB DEFAULT '{}'::jsonb,
                review_flags JSONB DEFAULT '[]'::jsonb
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS estimator_decision_proposals (
                proposal_id UUID PRIMARY KEY,
                session_id UUID NOT NULL REFERENCES estimator_sessions(session_id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                decision_graph_version TEXT,
                template_type TEXT,
                proposed_decisions JSONB DEFAULT '{}'::jsonb,
                proposal_source TEXT,
                evidence_summary JSONB DEFAULT '{}'::jsonb
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS estimator_decision_edits (
                edit_id UUID PRIMARY KEY,
                session_id UUID NOT NULL REFERENCES estimator_sessions(session_id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                decision_id TEXT,
                field_name TEXT,
                old_value JSONB,
                new_value JSONB,
                edited_by TEXT,
                edit_reason TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS estimator_final_decisions (
                final_decision_id UUID PRIMARY KEY,
                session_id UUID NOT NULL REFERENCES estimator_sessions(session_id) ON DELETE CASCADE,
                exported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                decision_graph_version TEXT,
                final_decisions JSONB DEFAULT '{}'::jsonb,
                calculated_outputs JSONB DEFAULT '{}'::jsonb,
                workbook_cell_writes JSONB DEFAULT '[]'::jsonb,
                workbook_export_path TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS estimator_session_artifacts (
                artifact_id UUID PRIMARY KEY,
                session_id UUID NOT NULL REFERENCES estimator_sessions(session_id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                artifact_type TEXT,
                artifact_path TEXT,
                artifact_json JSONB DEFAULT '{}'::jsonb
            )
            """,
        ]
    else:
        statements = [
            """
            CREATE TABLE IF NOT EXISTS estimator_sessions (
                session_id TEXT PRIMARY KEY,
                created_at TIMESTAMP,
                updated_at TIMESTAMP,
                division TEXT,
                template_type TEXT,
                customer TEXT,
                job_name TEXT,
                site_address TEXT,
                raw_input_notes TEXT,
                input_source_type TEXT,
                photos_present BOOLEAN,
                source_file_ids TEXT,
                ai_model TEXT,
                estimate_status TEXT,
                exported_workbook_path TEXT,
                exported_at TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS estimator_scope_interpretations (
                interpretation_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                created_at TIMESTAMP,
                parsed_scope TEXT,
                deterministic_scope TEXT,
                assumptions TEXT,
                missing_questions TEXT,
                confidence_by_field TEXT,
                review_flags TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS estimator_decision_proposals (
                proposal_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                created_at TIMESTAMP,
                decision_graph_version TEXT,
                template_type TEXT,
                proposed_decisions TEXT,
                proposal_source TEXT,
                evidence_summary TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS estimator_decision_edits (
                edit_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                created_at TIMESTAMP,
                decision_id TEXT,
                field_name TEXT,
                old_value TEXT,
                new_value TEXT,
                edited_by TEXT,
                edit_reason TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS estimator_final_decisions (
                final_decision_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                exported_at TIMESTAMP,
                decision_graph_version TEXT,
                final_decisions TEXT,
                calculated_outputs TEXT,
                workbook_cell_writes TEXT,
                workbook_export_path TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS estimator_session_artifacts (
                artifact_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                created_at TIMESTAMP,
                artifact_type TEXT,
                artifact_path TEXT,
                artifact_json TEXT
            )
            """,
        ]
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def create_estimator_session(
    engine: Engine,
    *,
    raw_input_notes: str,
    division: str | None = None,
    template_type: str | None = None,
    customer: str | None = None,
    job_name: str | None = None,
    site_address: str | None = None,
    input_source_type: str = "manual",
    photos_present: bool = False,
    source_file_ids: Any = None,
    ai_model: str | None = None,
    estimate_status: str | None = None,
    session_id: str | None = None,
) -> str:
    ensure_estimator_session_tables(engine)
    dialect = _dialect_name(engine)
    resolved_session_id = session_id or _new_id()
    now = _now()
    params = {
        "session_id": resolved_session_id,
        "created_at": now,
        "updated_at": now,
        "division": division,
        "template_type": template_type,
        "customer": customer,
        "job_name": job_name,
        "site_address": site_address,
        "raw_input_notes": raw_input_notes,
        "input_source_type": input_source_type,
        "photos_present": bool(photos_present),
        "source_file_ids": _json_dumps(source_file_ids if source_file_ids is not None else []),
        "ai_model": ai_model,
        "estimate_status": estimate_status,
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                INSERT INTO estimator_sessions (
                    session_id, created_at, updated_at, division, template_type,
                    customer, job_name, site_address, raw_input_notes, input_source_type,
                    photos_present, source_file_ids, ai_model, estimate_status
                )
                VALUES (
                    :session_id, :created_at, :updated_at, :division, :template_type,
                    :customer, :job_name, :site_address, :raw_input_notes, :input_source_type,
                    :photos_present, {_json_expr(dialect, "source_file_ids")}, :ai_model, :estimate_status
                )
                """
            ),
            params,
        )
    return resolved_session_id


def update_estimator_session(engine: Engine, session_id: str, **fields: Any) -> None:
    ensure_estimator_session_tables(engine)
    allowed = {
        "division",
        "template_type",
        "customer",
        "job_name",
        "site_address",
        "input_source_type",
        "photos_present",
        "source_file_ids",
        "ai_model",
        "estimate_status",
        "exported_workbook_path",
        "exported_at",
    }
    dialect = _dialect_name(engine)
    params: dict[str, Any] = {"session_id": session_id, "updated_at": _now()}
    assignments = ["updated_at = :updated_at"]
    for key, value in fields.items():
        if key not in allowed:
            continue
        params[key] = _json_dumps(value) if key == "source_file_ids" else value
        assignments.append(f"{key} = {_json_expr(dialect, key) if key == 'source_file_ids' else ':' + key}")
    if len(assignments) == 1:
        return
    with engine.begin() as connection:
        connection.execute(
            text(f"UPDATE estimator_sessions SET {', '.join(assignments)} WHERE session_id = :session_id"),
            params,
        )


def save_scope_interpretation(
    engine: Engine,
    session_id: str,
    *,
    parsed_scope: Any,
    deterministic_scope: Any = None,
    assumptions: Any = None,
    missing_questions: Any = None,
    confidence_by_field: Any = None,
    review_flags: Any = None,
) -> str:
    ensure_estimator_session_tables(engine)
    dialect = _dialect_name(engine)
    interpretation_id = _new_id()
    params = {
        "interpretation_id": interpretation_id,
        "session_id": session_id,
        "created_at": _now(),
        "parsed_scope": _json_dumps(parsed_scope),
        "deterministic_scope": _json_dumps(deterministic_scope or {}),
        "assumptions": _json_dumps(assumptions or {}),
        "missing_questions": _json_dumps(missing_questions or []),
        "confidence_by_field": _json_dumps(confidence_by_field or {}),
        "review_flags": _json_dumps(review_flags or []),
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                INSERT INTO estimator_scope_interpretations (
                    interpretation_id, session_id, created_at, parsed_scope,
                    deterministic_scope, assumptions, missing_questions,
                    confidence_by_field, review_flags
                )
                VALUES (
                    :interpretation_id, :session_id, :created_at,
                    {_json_expr(dialect, "parsed_scope")},
                    {_json_expr(dialect, "deterministic_scope")},
                    {_json_expr(dialect, "assumptions")},
                    {_json_expr(dialect, "missing_questions")},
                    {_json_expr(dialect, "confidence_by_field")},
                    {_json_expr(dialect, "review_flags")}
                )
                """
            ),
            params,
        )
    return interpretation_id


def save_decision_proposal(
    engine: Engine,
    session_id: str,
    *,
    proposed_decisions: Any,
    template_type: str | None = None,
    decision_graph_version: str = DECISION_GRAPH_VERSION,
    proposal_source: str = "historical_defaults",
    evidence_summary: Any = None,
) -> str:
    ensure_estimator_session_tables(engine)
    dialect = _dialect_name(engine)
    proposal_id = _new_id()
    params = {
        "proposal_id": proposal_id,
        "session_id": session_id,
        "created_at": _now(),
        "decision_graph_version": decision_graph_version,
        "template_type": template_type,
        "proposed_decisions": _json_dumps(proposed_decisions),
        "proposal_source": proposal_source,
        "evidence_summary": _json_dumps(evidence_summary or {}),
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                INSERT INTO estimator_decision_proposals (
                    proposal_id, session_id, created_at, decision_graph_version,
                    template_type, proposed_decisions, proposal_source, evidence_summary
                )
                VALUES (
                    :proposal_id, :session_id, :created_at, :decision_graph_version,
                    :template_type, {_json_expr(dialect, "proposed_decisions")},
                    :proposal_source, {_json_expr(dialect, "evidence_summary")}
                )
                """
            ),
            params,
        )
    return proposal_id


def _decision_id_from_edit(row: dict[str, Any]) -> str:
    section = str(row.get("section") or "")
    task = str(row.get("package_or_labor_task") or "")
    if task:
        return task
    if "." in section:
        return section.split(".", 1)[1]
    return section


def save_decision_edits(
    engine: Engine,
    session_id: str,
    edit_rows: list[dict[str, Any]],
    *,
    edited_by: str = "estimator",
) -> list[str]:
    ensure_estimator_session_tables(engine)
    dialect = _dialect_name(engine)
    saved_ids: list[str] = []
    now = _now()
    with engine.begin() as connection:
        for row in edit_rows or []:
            if not row:
                continue
            edit_id = _new_id()
            saved_ids.append(edit_id)
            params = {
                "edit_id": edit_id,
                "session_id": session_id,
                "created_at": now,
                "decision_id": _decision_id_from_edit(row),
                "field_name": row.get("field_name") or row.get("field"),
                "old_value": _json_dumps(row.get("suggested_value", row.get("historical_default"))),
                "new_value": _json_dumps(row.get("final_value")),
                "edited_by": edited_by,
                "edit_reason": row.get("reason") or row.get("edit_reason"),
            }
            connection.execute(
                text(
                    f"""
                    INSERT INTO estimator_decision_edits (
                        edit_id, session_id, created_at, decision_id, field_name,
                        old_value, new_value, edited_by, edit_reason
                    )
                    VALUES (
                        :edit_id, :session_id, :created_at, :decision_id, :field_name,
                        {_json_expr(dialect, "old_value")}, {_json_expr(dialect, "new_value")},
                        :edited_by, :edit_reason
                    )
                    """
                ),
                params,
            )
    return saved_ids


def save_final_decisions(
    engine: Engine,
    session_id: str,
    *,
    final_decisions: Any,
    calculated_outputs: Any,
    workbook_cell_writes: Any,
    workbook_export_path: str | None = None,
    decision_graph_version: str = DECISION_GRAPH_VERSION,
) -> str:
    ensure_estimator_session_tables(engine)
    dialect = _dialect_name(engine)
    final_decision_id = _new_id()
    exported_at = _now()
    params = {
        "final_decision_id": final_decision_id,
        "session_id": session_id,
        "exported_at": exported_at,
        "decision_graph_version": decision_graph_version,
        "final_decisions": _json_dumps(final_decisions),
        "calculated_outputs": _json_dumps(calculated_outputs),
        "workbook_cell_writes": _json_dumps(workbook_cell_writes),
        "workbook_export_path": workbook_export_path,
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                INSERT INTO estimator_final_decisions (
                    final_decision_id, session_id, exported_at, decision_graph_version,
                    final_decisions, calculated_outputs, workbook_cell_writes, workbook_export_path
                )
                VALUES (
                    :final_decision_id, :session_id, :exported_at, :decision_graph_version,
                    {_json_expr(dialect, "final_decisions")},
                    {_json_expr(dialect, "calculated_outputs")},
                    {_json_expr(dialect, "workbook_cell_writes")},
                    :workbook_export_path
                )
                """
            ),
            params,
        )
    update_estimator_session(
        engine,
        session_id,
        exported_workbook_path=workbook_export_path,
        exported_at=exported_at,
    )
    return final_decision_id


def save_session_artifact(
    engine: Engine,
    session_id: str,
    *,
    artifact_type: str,
    artifact_path: str | Path | None = None,
    artifact_json: Any = None,
) -> str:
    ensure_estimator_session_tables(engine)
    dialect = _dialect_name(engine)
    artifact_id = _new_id()
    params = {
        "artifact_id": artifact_id,
        "session_id": session_id,
        "created_at": _now(),
        "artifact_type": artifact_type,
        "artifact_path": str(artifact_path or ""),
        "artifact_json": _json_dumps(artifact_json or {}),
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                INSERT INTO estimator_session_artifacts (
                    artifact_id, session_id, created_at, artifact_type, artifact_path, artifact_json
                )
                VALUES (
                    :artifact_id, :session_id, :created_at, :artifact_type,
                    :artifact_path, {_json_expr(dialect, "artifact_json")}
                )
                """
            ),
            params,
        )
    return artifact_id


def _latest(rows: list[dict[str, Any]], timestamp_field: str) -> dict[str, Any]:
    if not rows:
        return {}
    return sorted(rows, key=lambda row: str(row.get(timestamp_field) or ""))[-1]


def load_estimator_session_payload(engine: Engine, session_id: str) -> dict[str, Any]:
    ensure_estimator_session_tables(engine)
    with engine.connect() as connection:
        session_rows = _rows_from_result(
            connection.execute(text("SELECT * FROM estimator_sessions WHERE session_id = :session_id"), {"session_id": session_id})
        )
        if not session_rows:
            raise ValueError(f"Estimator session not found: {session_id}")
        payload = {"session": session_rows[0]}
        for key, table in (
            ("scope_interpretations", "estimator_scope_interpretations"),
            ("decision_proposals", "estimator_decision_proposals"),
            ("decision_edits", "estimator_decision_edits"),
            ("final_decisions", "estimator_final_decisions"),
            ("artifacts", "estimator_session_artifacts"),
        ):
            payload[key] = _rows_from_result(
                connection.execute(text(f"SELECT * FROM {table} WHERE session_id = :session_id"), {"session_id": session_id})
            )
    for row in [payload["session"]]:
        row["source_file_ids"] = _maybe_json(row.get("source_file_ids"))
    for row in payload["scope_interpretations"]:
        for column in ("parsed_scope", "deterministic_scope", "assumptions", "missing_questions", "confidence_by_field", "review_flags"):
            row[column] = _maybe_json(row.get(column))
    for row in payload["decision_proposals"]:
        for column in ("proposed_decisions", "evidence_summary"):
            row[column] = _maybe_json(row.get(column))
    for row in payload["decision_edits"]:
        for column in ("old_value", "new_value"):
            row[column] = _maybe_json(row.get(column))
    for row in payload["final_decisions"]:
        for column in ("final_decisions", "calculated_outputs", "workbook_cell_writes"):
            row[column] = _maybe_json(row.get(column))
    for row in payload["artifacts"]:
        row["artifact_json"] = _maybe_json(row.get("artifact_json"))
    latest_scope = _latest(payload["scope_interpretations"], "created_at")
    latest_final = _latest(payload["final_decisions"], "exported_at")
    payload["review"] = {
        "session_id": session_id,
        "raw_input_notes": payload["session"].get("raw_input_notes"),
        "parsed_scope": latest_scope.get("parsed_scope") or {},
        "assumptions": latest_scope.get("assumptions") or {},
        "missing_questions": latest_scope.get("missing_questions") or [],
        "proposed_decisions": [_maybe_json(row.get("proposed_decisions")) for row in payload["decision_proposals"]],
        "estimator_edits": payload["decision_edits"],
        "final_decisions": latest_final.get("final_decisions") or {},
        "calculated_outputs": latest_final.get("calculated_outputs") or {},
        "workbook_cell_writes": latest_final.get("workbook_cell_writes") or [],
        "workbook_export_path": latest_final.get("workbook_export_path") or payload["session"].get("exported_workbook_path"),
    }
    return _jsonable(payload)


def export_estimator_session_package(
    engine: Engine,
    session_id: str,
    out: str | Path | None = None,
) -> Path:
    payload = load_estimator_session_payload(engine, session_id)
    output = Path(out) if out else DEFAULT_SESSION_EXPORT_DIR / f"estimator_session_{session_id}.zip"
    if output.suffix.lower() != ".zip":
        output.mkdir(parents=True, exist_ok=True)
        output = output / f"estimator_session_{session_id}.zip"
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
    review = payload["review"]
    files = {
        "session_review.json": json.dumps(review, indent=2, sort_keys=True, default=str),
        "session_payload.json": json.dumps(payload, indent=2, sort_keys=True, default=str),
        "raw_notes.txt": review.get("raw_input_notes") or "",
        "parsed_scope.json": json.dumps(review.get("parsed_scope") or {}, indent=2, sort_keys=True, default=str),
        "assumptions.json": json.dumps(review.get("assumptions") or {}, indent=2, sort_keys=True, default=str),
        "missing_questions.json": json.dumps(review.get("missing_questions") or [], indent=2, sort_keys=True, default=str),
        "proposed_decisions.json": json.dumps(review.get("proposed_decisions") or [], indent=2, sort_keys=True, default=str),
        "estimator_edits.json": json.dumps(review.get("estimator_edits") or [], indent=2, sort_keys=True, default=str),
        "final_decisions.json": json.dumps(review.get("final_decisions") or {}, indent=2, sort_keys=True, default=str),
        "calculated_outputs.json": json.dumps(review.get("calculated_outputs") or {}, indent=2, sort_keys=True, default=str),
        "workbook_cell_writes.json": json.dumps(review.get("workbook_cell_writes") or [], indent=2, sort_keys=True, default=str),
        "workbook_export_path.txt": str(review.get("workbook_export_path") or ""),
    }
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename, content in files.items():
            archive.writestr(filename, content)
    save_session_artifact(engine, session_id, artifact_type="session_review_package", artifact_path=output)
    return output


def export_training_dataset(engine: Engine, out: str | Path) -> Path:
    ensure_estimator_session_tables(engine)
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with engine.connect() as connection:
        sessions = _rows_from_result(connection.execute(text("SELECT session_id FROM estimator_sessions ORDER BY created_at")))
    with out_path.open("w", encoding="utf-8") as handle:
        for session in sessions:
            payload = load_estimator_session_payload(engine, str(session["session_id"]))
            review = payload["review"]
            latest_scope = _latest(payload["scope_interpretations"], "created_at")
            row = {
                "raw_input_notes": review.get("raw_input_notes"),
                "parsed_scope": review.get("parsed_scope") or {},
                "proposed_decisions": review.get("proposed_decisions") or [],
                "final_decisions": review.get("final_decisions") or {},
                "estimator_edits": review.get("estimator_edits") or [],
                "calculated_outputs": review.get("calculated_outputs") or {},
                "workbook_cell_writes": review.get("workbook_cell_writes") or [],
                "template_type": payload["session"].get("template_type"),
                "division": payload["session"].get("division"),
                "estimate_status": payload["session"].get("estimate_status"),
                "source_metadata": {
                    "session_id": payload["session"].get("session_id"),
                    "created_at": payload["session"].get("created_at"),
                    "input_source_type": payload["session"].get("input_source_type"),
                    "photos_present": payload["session"].get("photos_present"),
                    "source_file_ids": payload["session"].get("source_file_ids") or [],
                    "ai_model": payload["session"].get("ai_model"),
                    "confidence_by_field": latest_scope.get("confidence_by_field") or {},
                    "review_flags": latest_scope.get("review_flags") or [],
                },
            }
            handle.write(json.dumps(_jsonable(row), sort_keys=True, default=str) + "\n")
    return out_path


def _product_guidance_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "product_id": row.get("product_id"),
        "manufacturer": row.get("product_manufacturer"),
        "guidance": row.get("product_guidance"),
        "recommended_use": row.get("product_recommended_use"),
        "manufacturer_guidance": row.get("product_manufacturer_guidance"),
        "coverage": row.get("product_coverage"),
        "limitations": row.get("product_limitations"),
        "warnings": row.get("product_warnings") or row.get("product_warning_summary"),
        "source_documents": row.get("product_source_documents") or row.get("product_source_evidence"),
        "source_evidence": row.get("product_source_evidence_rows") or row.get("source_evidence") or [],
        "confidence": row.get("product_context_confidence"),
        "match_score": row.get("product_match_score"),
    }


def _source_evidence_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "row_traceability": row.get("row_traceability"),
        "workbook_rows_controlled": row.get("workbook_rows_controlled") or row.get("workbook_row"),
        "decision_recommendation": _maybe_json(row.get("decision_recommendation_json")),
        "decision_source_tables": row.get("decision_source_tables"),
        "decision_filters_applied": row.get("decision_filters_applied"),
        "decision_filters_relaxed": row.get("decision_filters_relaxed"),
        "evidence_summary": row.get("evidence_summary"),
        "notes": row.get("notes"),
        "source_text": row.get("source_text") or row.get("target_r_source_text"),
    }


def _decision_record_from_workbench_row(row: dict[str, Any], section: str, *, final: bool = False) -> dict[str, Any]:
    decision_id = row.get("decision_id") or row.get("package_key") or row.get("adder_key")
    template_bucket = row.get("template_bucket") or row.get("package_key") or row.get("adder_key")
    editable_value = row.get("editable_decision_value")
    if editable_value in (None, "", [], {}):
        editable_value = row.get("item_name") or row.get("labor_package") or row.get("editable_value")
    record = {
        "section": section,
        "decision_id": decision_id,
        "template_bucket": template_bucket,
        "workbook_row": row.get("workbook_row"),
        "workbook_traceability": row.get("row_traceability"),
        "item_or_task": row.get("resolved_template_option") or row.get("surface") or row.get("step") or row.get("item_name") or row.get("labor_package") or row.get("adder"),
        "include": bool(row.get("include")),
        "suggested_by_notes_rules": row.get("suggested_by_notes_rules"),
        "historical_recommendation": row.get("historical_recommendation"),
        "recommended_value": row.get("recommended_decision_value"),
        "editable_value": editable_value,
        "decision_values": row.get("decision_values"),
        "calculated_output": row.get("calculated_output"),
        "calculated_output_summary": row.get("calculated_output_summary"),
        "quantity": row.get("calculated_quantity"),
        "hours": row.get("calculated_hours"),
        "cost": row.get("estimated_cost"),
        "evidence_count": row.get("evidence_count"),
        "decision_evidence_count": row.get("decision_evidence_count"),
        "decision_source_jobs_count": row.get("decision_source_jobs_count"),
        "confidence": row.get("confidence"),
        "decision_confidence": row.get("decision_confidence"),
        "source_evidence": _source_evidence_snapshot(row),
        "product_guidance_snapshot": _product_guidance_snapshot(row),
    }
    if final:
        record["final_value"] = editable_value
        record["final_decision_value"] = editable_value
    return _jsonable(record)


def proposed_decisions_from_workbench(workbench: dict[str, Any]) -> dict[str, Any]:
    recalculated = recalculate_workbench_tables(workbench)
    rows: list[dict[str, Any]] = []
    for section in ("area_calculation_trace", "insulation_surfaces", "insulation_foam_template_decisions", "insulation_performance_specs", "materials", "labor", "adders"):
        for row in recalculated.get(section) or []:
            rows.append(_decision_record_from_workbench_row(row, section, final=False))
    return {
        "decision_graph_version": DECISION_GRAPH_VERSION,
        "area_calculation_explanation": recalculated.get("area_calculation_explanation") or "",
        "decisions": rows,
    }


def final_decisions_from_workbench(workbench: dict[str, Any]) -> dict[str, Any]:
    recalculated = recalculate_workbench_tables(workbench)
    decisions: list[dict[str, Any]] = []
    for section in ("area_calculation_trace", "insulation_surfaces", "insulation_foam_template_decisions", "insulation_performance_specs", "materials", "labor", "adders"):
        for row in recalculated.get(section) or []:
            if not row.get("include"):
                continue
            decisions.append(_decision_record_from_workbench_row(row, section, final=True))
    return {
        "decision_graph_version": DECISION_GRAPH_VERSION,
        "scope": recalculated.get("scope") or {},
        "historical_filters": recalculated.get("historical_filters") or {},
        "area_calculation_explanation": recalculated.get("area_calculation_explanation") or "",
        "decisions": decisions,
    }


def workbook_cell_writes_from_inputs(draft_workbook_inputs: dict[str, Any]) -> list[dict[str, Any]]:
    """Return an audit-friendly preview of workbook cells/rows controlled by the export."""

    template_type = str(draft_workbook_inputs.get("template_type") or "roofing").lower()
    writes: list[dict[str, Any]] = []
    header = draft_workbook_inputs.get("header") or {}
    for key, cell in {
        "C2_job_name": "Estimate!C2",
        "C3_job_type": "Estimate!C3",
        "C4_site_address": "Estimate!C4",
        "C5_city_state_zip": "Estimate!C5",
        "C12_estimated_sqft": "Estimate!C12",
    }.items():
        if key in header:
            writes.append({"section": "header", "cell": cell, "field": key, "value": header.get(key)})
    material_rows = draft_workbook_inputs.get("material_rows") or []
    for index, row in enumerate(material_rows):
        category = str(row.get("category") or "")
        writes.append(
            {
                "section": "materials",
                "template_type": template_type,
                "row_index": index,
                "decision_id": row.get("decision_id"),
                "template_bucket": row.get("template_bucket") or category,
                "workbook_row": row.get("workbook_row"),
                "row_traceability": row.get("row_traceability"),
                "category": category,
                "item": row.get("item"),
                "quantity": row.get("quantity"),
                "unit_price": row.get("unit_price"),
                "estimated_cost": row.get("estimated_cost"),
                "formula_model": row.get("formula_model"),
                "formula_source": row.get("formula_source"),
                "calculated_output_summary": row.get("calculated_output_summary"),
                "workbook_cell_write_preview": row.get("workbook_cell_write_preview") or [],
                "target_hint": row.get("workbook_row") or category,
            }
        )
    for index, row in enumerate(draft_workbook_inputs.get("labor_rows") or []):
        writes.append(
            {
                "section": "labor",
                "template_type": template_type,
                "row_index": index,
                "decision_id": row.get("decision_id"),
                "template_bucket": row.get("template_bucket") or row.get("task"),
                "workbook_row": row.get("workbook_row"),
                "row_traceability": row.get("row_traceability"),
                "task": row.get("task"),
                "crew_size": row.get("crew_size"),
                "total_hours": row.get("total_hours"),
                "adjusted_days": row.get("adjusted_days"),
                "estimated_cost": row.get("estimated_cost"),
                "formula_mode": row.get("formula_mode"),
                "formula_model": row.get("formula_model"),
                "formula_source": row.get("formula_source"),
                "calculated_output_summary": row.get("calculated_output_summary"),
                "workbook_cell_write_preview": row.get("workbook_cell_write_preview") or [],
            }
        )
    for section in ("travel_rows", "adders_review_rows"):
        for index, row in enumerate(draft_workbook_inputs.get(section) or []):
            writes.append({"section": section, "row_index": index, **row})
    return _jsonable(writes)


def _engine_from_url(db_url: str | None) -> Engine:
    resolved = db_url or os.getenv("NEON_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not resolved:
        raise SystemExit("Missing --db-url or NEON_DATABASE_URL/DATABASE_URL.")
    return create_engine(resolved, future=True)


def main_export(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export an Estimating Assistant session review package.")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--db-url", default=os.getenv("NEON_DATABASE_URL") or os.getenv("DATABASE_URL"))
    args = parser.parse_args(argv)
    path = export_estimator_session_package(_engine_from_url(args.db_url), args.session_id, args.out)
    print(f"Wrote estimator session review package: {path}")
    return 0


def main_training_export(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export Estimating Assistant sessions as JSONL training data.")
    parser.add_argument("--out", required=True)
    parser.add_argument("--db-url", default=os.getenv("NEON_DATABASE_URL") or os.getenv("DATABASE_URL"))
    args = parser.parse_args(argv)
    path = export_training_dataset(_engine_from_url(args.db_url), args.out)
    print(f"Wrote estimator training dataset: {path}")
    return 0
