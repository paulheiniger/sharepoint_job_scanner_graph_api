from __future__ import annotations

import argparse
import json
import os
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from .evidence_export import sanitize_for_export
from .estimator_memory import normalize_memory_token, upsert_estimator_memory
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


MEMORY_CAPTURE_FIELDS = {
    "include",
    "editable_selector_code",
    "selected_pricing_candidate",
    "basis_sqft",
    "thickness_inches",
    "debris_thickness_inches",
    "tearout_thickness_inches",
    "removed_assembly_thickness_inches",
    "yield_or_coverage",
    "unit_price",
    "estimated_units",
    "gal_per_100_sqft",
    "coverage_sqft_per_unit",
    "feet_per_unit",
    "days",
    "hours_per_day",
    "people_count",
    "trip_count",
    "crew_size",
    "daily_rate",
    "hourly_rate",
    "total_hours",
    "editable_total_hours",
    "round_trip_miles",
    "markup_pct",
}


def _memory_template_type_from_edit(row: dict[str, Any], fallback: str = "") -> str:
    section = normalize_memory_token(row.get("section"))
    if section.startswith("insulation_"):
        return "insulation"
    if section.startswith("roofing_"):
        return "roofing"
    if section.startswith("flooring_"):
        return "flooring"
    if section.startswith("repair_"):
        return "repair"
    return normalize_memory_token(fallback)


def _memory_bucket_from_edit(row: dict[str, Any]) -> str:
    bucket = normalize_memory_token(row.get("package_or_labor_task"))
    if bucket:
        return bucket
    section = normalize_memory_token(row.get("section"))
    for suffix in ("template_decisions", "decisions"):
        if section.endswith(suffix):
            section = section[: -len(suffix)].strip("_")
    if "." in str(row.get("section") or ""):
        return normalize_memory_token(str(row.get("section") or "").split(".", 1)[1])
    return section


def _memory_value_text(value: Any) -> str:
    value = _maybe_json(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, default=str)
    return str(value)


def estimator_memory_candidates_from_edits(
    edit_rows: list[dict[str, Any]],
    *,
    session_id: str = "",
    template_type: str = "",
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in edit_rows or []:
        if not isinstance(row, dict):
            continue
        field_name = normalize_memory_token(row.get("field_name") or row.get("field"))
        if field_name not in MEMORY_CAPTURE_FIELDS:
            continue
        old_value = row.get("suggested_value", row.get("historical_default"))
        new_value = row.get("final_value")
        if _memory_value_text(old_value) == _memory_value_text(new_value):
            continue
        if new_value in (None, ""):
            continue
        decision_id = normalize_memory_token(_decision_id_from_edit(row))
        bucket = _memory_bucket_from_edit(row)
        resolved_template_type = _memory_template_type_from_edit(row, template_type)
        final_text = _memory_value_text(new_value)
        old_text = _memory_value_text(old_value)
        reason = str(row.get("reason") or row.get("edit_reason") or "").strip()
        if field_name == "include":
            guidance = (
                f"For {resolved_template_type or 'estimator'} {bucket or decision_id}, estimator set include={final_text}. "
                "Use this as a reviewed prior for similar jobs, but keep current job evidence authoritative."
            )
        else:
            guidance = (
                f"For {resolved_template_type or 'estimator'} {bucket or decision_id}, estimator changed {field_name} "
                f"from {old_text or 'blank'} to {final_text}. Use this as a reviewed prior for similar jobs."
            )
        if reason:
            guidance = f"{guidance} Reason: {reason}"
        signature = "|".join([session_id, resolved_template_type, decision_id, field_name, final_text])
        if signature in seen:
            continue
        seen.add(signature)
        candidates.append(
            {
                "memory_id": str(uuid5(NAMESPACE_URL, f"spraytec-estimator-memory|{signature}")),
                "guidance": guidance,
                "template_type": resolved_template_type,
                "decision_id": decision_id,
                "template_bucket": bucket,
                "applies_when": {
                    "source_session_id": session_id,
                    "field_name": field_name,
                    "previous_value": old_text,
                    "final_value": final_text,
                },
                "rationale": "Pending memory candidate generated from estimator workbook edit.",
                "source_type": "estimator_edit",
                "source_session_id": session_id or None,
                "status": "pending",
                "priority": "medium",
            }
        )
    return candidates


REFERENCE_MEMORY_VALUE_FIELDS = {
    "selector_code",
    "editable_selector_code",
    "resolved_template_option",
    "selected_pricing_candidate",
    "basis_sqft",
    "area_sqft",
    "thickness_inches",
    "foam_thickness_inches",
    "yield_or_coverage",
    "coverage_sqft_per_unit",
    "gal_per_100_sqft",
    "gal_per_sqft",
    "waste_factor_pct",
    "wet_mils_estimate",
    "unit_price",
    "price_per_square",
    "unit_price_per_thousand",
    "estimated_units",
    "estimated_gallons",
    "estimated_sets",
    "linear_ft",
    "units",
    "period",
    "margin_pct",
    "days",
    "hours_per_day",
    "people_count",
    "trip_count",
    "round_trip_miles",
    "crew_size",
    "crew_people_selection",
    "crew_selector_code",
    "total_hours",
    "editable_total_hours",
    "daily_rate",
    "hourly_rate",
    "labor_rate",
    "formula_mode",
}


def estimator_memory_candidates_from_reference_template(
    decision_rows: list[dict[str, Any]],
    *,
    session_id: str = "",
    template_type: str = "",
    scope_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    scope_memory_context = _reference_scope_memory_context(scope_context or {})
    for row in decision_rows or []:
        if not isinstance(row, dict):
            continue
        if normalize_memory_token(row.get("source")) not in {"reference_template_summary", "reference_estimate_answer_key"}:
            continue
        if row.get("include") is not True:
            continue
        decision_id = normalize_memory_token(row.get("decision_id"))
        bucket = normalize_memory_token(row.get("template_bucket"))
        resolved_template_type = _memory_template_type_from_edit(row, template_type)
        proposed_values = _reference_memory_values(row.get("proposed_values") or {})
        if not decision_id or not bucket or not proposed_values:
            continue
        evidence = row.get("evidence") if isinstance(row.get("evidence"), list) else []
        source_evidence = evidence[0] if evidence and isinstance(evidence[0], dict) else {}
        source_row = str(source_evidence.get("source_row") or row.get("workbook_row") or "")
        line_item = str(source_evidence.get("line_item") or row.get("label") or decision_id)
        value_text = ", ".join(f"{key}={_memory_value_text(value)}" for key, value in proposed_values.items())
        normalized_row = str(row.get("workbook_row") or "")
        scope_phrase = _reference_scope_guidance_phrase(scope_memory_context)
        guidance = (
            f"For {resolved_template_type or 'estimator'} {bucket}, a reviewed reference template included "
            f"{line_item} mapped to workbook row {normalized_row} with {value_text}. "
            f"{scope_phrase}"
            "Use this as historical guidance for similar jobs, but keep current job evidence and workbook formulas authoritative."
        )
        signature_payload = {
            "session_id": session_id,
            "template_type": resolved_template_type,
            "decision_id": decision_id,
            "bucket": bucket,
            "row": normalized_row,
            "values": proposed_values,
            "source_row": source_row,
            "line_item": line_item,
        }
        signature = json.dumps(signature_payload, sort_keys=True, default=str)
        if signature in seen:
            continue
        seen.add(signature)
        candidates.append(
            {
                "memory_id": str(uuid5(NAMESPACE_URL, f"spraytec-reference-template-memory|{signature}")),
                "guidance": guidance,
                "template_type": resolved_template_type,
                "decision_id": decision_id,
                "template_bucket": bucket,
                "product_or_system": line_item,
                "applies_when": {
                    "source_session_id": session_id,
                    "source_type": normalize_memory_token(row.get("source")) or "reference_template_summary",
                    "source_row": source_row,
                    "normalized_workbook_row": normalized_row,
                    "line_item": line_item,
                    "proposed_values": proposed_values,
                    "evidence": source_evidence,
                    **scope_memory_context,
                },
                "rationale": "Pending memory candidate generated from reviewed reference estimate answer key.",
                "source_type": normalize_memory_token(row.get("source")) or "reference_template_summary",
                "source_session_id": session_id or None,
                "status": "pending",
                "priority": "high",
            }
        )
    return candidates


REFERENCE_CUE_GROUPS: dict[str, dict[str, Any]] = {
    "roofing_coating_restoration": {
        "template_type": "roofing",
        "template_bucket": "coating_restoration",
        "cue_terms": ["coating", "restoration", "silicone", "acrylic", "warranty", "top coat", "primer"],
        "buckets": {"coating", "primer", "granules", "warranty"},
        "label": "roof coating/restoration path",
    },
    "roofing_detail_repairs": {
        "template_type": "roofing",
        "template_bucket": "detail_repairs",
        "cue_terms": ["seams", "fasteners", "penetrations", "curbs", "flashing", "ponding", "caulk", "repair"],
        "buckets": {"caulk_detail", "caulk_sealant", "fabric", "fasteners", "plates", "seams_misc", "drains", "hvac_units"},
        "label": "roof detail and repair work",
    },
    "roofing_foam_repair": {
        "template_type": "roofing",
        "template_bucket": "foam_repair",
        "cue_terms": ["foam", "blister", "saturated", "tear out", "board", "wet", "repair"],
        "buckets": {"foam", "roofing_foam", "board", "iso_board", "membrane", "dumpster", "disposal"},
        "label": "roof foam/board repair",
    },
    "roofing_labor_plan": {
        "template_type": "roofing",
        "template_bucket": "labor_plan",
        "cue_terms": ["labor", "crew", "prep", "prime", "caulk", "top coat", "cleanup", "tear out"],
        "buckets": {
            "labor_setup",
            "labor_base",
            "labor_prime",
            "labor_caulk",
            "labor_fasteners",
            "labor_topcoat",
            "labor_misc",
            "labor_cleanup",
        },
        "bucket_prefixes": ["labor_"],
        "label": "roofing labor plan",
    },
    "roofing_logistics": {
        "template_type": "roofing",
        "template_bucket": "logistics",
        "cue_terms": ["travel", "loading", "generator", "miles", "lodging", "truck", "sales", "inspection"],
        "buckets": {"generator", "truck_expense", "sales_inspection", "sales_trips", "labor_loading", "labor_traveling", "meals_lodging"},
        "label": "roofing logistics and trip costs",
    },
    "insulation_foam_scope": {
        "template_type": "insulation",
        "template_bucket": "foam_scope",
        "cue_terms": ["spray foam", "open cell", "closed cell", "r-value", "metal building", "pole barn", "ceiling", "walls"],
        "buckets": {"foam", "wall_foam", "ceiling_foam", "thermal_barrier", "labor_foam", "labor_mask"},
        "label": "insulation foam scope",
    },
    "insulation_logistics": {
        "template_type": "insulation",
        "template_bucket": "logistics",
        "cue_terms": ["travel", "loading", "generator", "miles", "lodging", "truck", "sales", "inspection"],
        "buckets": {"generator", "truck_expense", "sales_inspection", "labor_loading", "labor_traveling", "meals_lodging"},
        "label": "insulation logistics and support costs",
    },
}


def estimator_cue_memory_candidates_from_reference_template(
    decision_rows: list[dict[str, Any]],
    *,
    session_id: str = "",
    template_type: str = "",
    scope_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    scope_memory_context = _reference_scope_memory_context(scope_context or {})
    scope_text = " ".join(str(value or "") for value in (scope_context or {}).values()).lower()
    resolved_template_filter = normalize_memory_token(template_type)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in decision_rows or []:
        if not isinstance(row, dict):
            continue
        if normalize_memory_token(row.get("source")) not in {"reference_template_summary", "reference_estimate_answer_key"}:
            continue
        if row.get("include") is not True:
            continue
        row_template_type = _memory_template_type_from_edit(row, template_type)
        if resolved_template_filter and row_template_type and row_template_type != resolved_template_filter:
            continue
        bucket = normalize_memory_token(row.get("template_bucket"))
        if not bucket:
            continue
        for group_id, group in REFERENCE_CUE_GROUPS.items():
            if group.get("template_type") != row_template_type:
                continue
            group_buckets = set(group.get("buckets") or set())
            group_prefixes = tuple(str(value) for value in group.get("bucket_prefixes") or [])
            if bucket in group_buckets or any(bucket.startswith(prefix) for prefix in group_prefixes):
                grouped.setdefault(group_id, []).append(row)
    candidates: list[dict[str, Any]] = []
    for group_id, rows in grouped.items():
        group = REFERENCE_CUE_GROUPS[group_id]
        cue_terms = [term for term in group.get("cue_terms", []) if term in scope_text]
        if not cue_terms:
            cue_terms = [str(term) for term in group.get("cue_terms", [])[:4]]
        line_items: list[str] = []
        source_rows: list[str] = []
        decision_ids: list[str] = []
        value_snippets: list[str] = []
        for row in rows:
            evidence = row.get("evidence") if isinstance(row.get("evidence"), list) else []
            source_evidence = evidence[0] if evidence and isinstance(evidence[0], dict) else {}
            line_item = str(source_evidence.get("line_item") or row.get("label") or row.get("decision_id") or "").strip()
            if line_item and line_item not in line_items:
                line_items.append(line_item)
            source_row = str(source_evidence.get("source_row") or row.get("workbook_row") or "").strip()
            if source_row and source_row not in source_rows:
                source_rows.append(source_row)
            decision_id = normalize_memory_token(row.get("decision_id"))
            if decision_id and decision_id not in decision_ids:
                decision_ids.append(decision_id)
            values = _reference_memory_values(row.get("proposed_values") or {})
            if values and len(value_snippets) < 8:
                value_snippets.append(f"{line_item or decision_id}: " + ", ".join(f"{key}={_memory_value_text(value)}" for key, value in values.items()))
        if not line_items:
            continue
        label = str(group.get("label") or group_id.replace("_", " "))
        item_text = ", ".join(line_items[:10])
        cue_text = ", ".join(cue_terms[:8])
        value_text = "; ".join(value_snippets[:5])
        guidance = (
            f"When field notes point to {label}"
            f"{f' ({cue_text})' if cue_text else ''}, similar reviewed estimates included: {item_text}. "
        )
        if value_text:
            guidance += f"Typical answer-key values from the reviewed example: {value_text}. "
        guidance += "Use this as cue-linked historical evidence, then apply current job quantities, pricing, and workbook formulas."
        signature_payload = {
            "session_id": session_id,
            "template_type": group.get("template_type"),
            "group_id": group_id,
            "decision_ids": sorted(decision_ids),
            "source_rows": sorted(source_rows),
        }
        signature = json.dumps(signature_payload, sort_keys=True, default=str)
        applies_when = {
            "source_session_id": session_id,
            "source_type": "reference_answer_key_cue",
            "cue_group": group_id,
            "cue_terms": cue_terms,
            "keywords": sorted(set((scope_memory_context.get("keywords") or []) + cue_terms))[:20],
            "line_items": line_items[:20],
            "source_rows": source_rows[:20],
            "decision_ids": decision_ids[:40],
            **scope_memory_context,
        }
        candidates.append(
            {
                "memory_id": str(uuid5(NAMESPACE_URL, f"spraytec-reference-answer-key-cue|{signature}")),
                "guidance": guidance,
                "template_type": str(group.get("template_type") or ""),
                "decision_id": group_id,
                "template_bucket": str(group.get("template_bucket") or ""),
                "product_or_system": label,
                "applies_when": applies_when,
                "rationale": "Grouped cue memory generated from a reviewed answer key and its field-note cues.",
                "source_type": "reference_answer_key_cue",
                "source_session_id": session_id or None,
                "status": "pending",
                "priority": "high",
            }
        )
    if not candidates:
        candidates.extend(
            _generic_reference_answer_key_memory_candidates(
                decision_rows,
                session_id=session_id,
                template_type=template_type,
                scope_memory_context=scope_memory_context,
            )
        )
    return candidates


def _generic_reference_answer_key_memory_candidates(
    decision_rows: list[dict[str, Any]],
    *,
    session_id: str = "",
    template_type: str = "",
    scope_memory_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    scope_memory_context = scope_memory_context or {}
    resolved_template_type = normalize_memory_token(template_type)
    included_rows: list[dict[str, Any]] = []
    line_items: list[str] = []
    source_rows: list[str] = []
    decision_ids: list[str] = []
    buckets: list[str] = []
    value_snippets: list[str] = []
    for row in decision_rows or []:
        if not isinstance(row, dict):
            continue
        if normalize_memory_token(row.get("source")) not in {"reference_template_summary", "reference_estimate_answer_key"}:
            continue
        if row.get("include") is not True:
            continue
        row_template_type = _memory_template_type_from_edit(row, template_type)
        if resolved_template_type and row_template_type and row_template_type != resolved_template_type:
            continue
        included_rows.append(row)
        evidence = row.get("evidence") if isinstance(row.get("evidence"), list) else []
        source_evidence = evidence[0] if evidence and isinstance(evidence[0], dict) else {}
        line_item = str(source_evidence.get("line_item") or row.get("label") or row.get("decision_id") or "").strip()
        if line_item and line_item not in line_items:
            line_items.append(line_item)
        source_row = str(source_evidence.get("source_row") or row.get("workbook_row") or "").strip()
        if source_row and source_row not in source_rows:
            source_rows.append(source_row)
        decision_id = normalize_memory_token(row.get("decision_id"))
        if decision_id and decision_id not in decision_ids:
            decision_ids.append(decision_id)
        bucket = normalize_memory_token(row.get("template_bucket"))
        if bucket and bucket not in buckets:
            buckets.append(bucket)
        values = _reference_memory_values(row.get("proposed_values") or {})
        if values and len(value_snippets) < 8:
            value_snippets.append(f"{line_item or decision_id}: " + ", ".join(f"{key}={_memory_value_text(value)}" for key, value in values.items()))
    if not included_rows:
        return []
    resolved_template_type = resolved_template_type or _memory_template_type_from_edit(included_rows[0], template_type) or "estimator"
    item_text = ", ".join(line_items[:12]) if line_items else ", ".join(decision_ids[:12])
    value_text = "; ".join(value_snippets[:5])
    guidance = (
        f"Reviewed {resolved_template_type} answer key included {len(included_rows)} mapped decision rows"
        f"{f': {item_text}' if item_text else ''}. "
    )
    if value_text:
        guidance += f"Representative values: {value_text}. "
    guidance += "Use this full reviewed example as historical context for similar jobs, but keep current job evidence and workbook formulas authoritative."
    signature_payload = {
        "session_id": session_id,
        "template_type": resolved_template_type,
        "decision_ids": sorted(decision_ids),
        "source_rows": sorted(source_rows),
    }
    signature = json.dumps(signature_payload, sort_keys=True, default=str)
    return [
        {
            "memory_id": str(uuid5(NAMESPACE_URL, f"spraytec-reference-answer-key-generic|{signature}")),
            "guidance": guidance,
            "template_type": resolved_template_type,
            "decision_id": f"{resolved_template_type}_reviewed_answer_key_example",
            "template_bucket": "reviewed_answer_key_example",
            "product_or_system": "Reviewed answer key example",
            "applies_when": {
                "source_session_id": session_id,
                "source_type": "reference_answer_key_cue",
                "cue_group": "reviewed_answer_key_example",
                "keywords": sorted(set(scope_memory_context.get("keywords") or []))[:20],
                "line_items": line_items[:30],
                "source_rows": source_rows[:30],
                "decision_ids": decision_ids[:60],
                "template_buckets": buckets[:30],
                **scope_memory_context,
            },
            "rationale": "Generic reviewed answer-key memory created because no specific cue group matched the mapped rows.",
            "source_type": "reference_answer_key_cue",
            "source_session_id": session_id or None,
            "status": "pending",
            "priority": "high",
        }
    ]


def _reference_memory_values(values: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(values, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in values.items():
        token = normalize_memory_token(key)
        if token not in REFERENCE_MEMORY_VALUE_FIELDS:
            continue
        if value in (None, "", [], {}):
            continue
        cleaned[token] = _maybe_json(value)
    return cleaned


def save_memory_candidates_from_reference_template(
    engine: Engine,
    session_id: str,
    decision_rows: list[dict[str, Any]],
    *,
    template_type: str = "",
    scope_context: dict[str, Any] | None = None,
) -> list[str]:
    if not decision_rows:
        return []
    ensure_estimator_session_tables(engine)
    resolved_template_type = template_type
    if not resolved_template_type:
        with engine.connect() as connection:
            resolved_template_type = str(
                connection.execute(
                    text("SELECT template_type FROM estimator_sessions WHERE session_id = :session_id"),
                    {"session_id": session_id},
                ).scalar_one_or_none()
                or ""
            )
    candidates = estimator_memory_candidates_from_reference_template(
        decision_rows,
        session_id=session_id,
        template_type=resolved_template_type,
        scope_context=scope_context,
    )
    memory_ids: list[str] = []
    for candidate in candidates:
        memory_ids.append(
            upsert_estimator_memory(
                engine,
                memory_id=candidate["memory_id"],
                guidance=candidate["guidance"],
                template_type=candidate["template_type"],
                decision_id=candidate["decision_id"],
                template_bucket=candidate["template_bucket"],
                product_or_system=candidate["product_or_system"],
                applies_when=candidate["applies_when"],
                rationale=candidate["rationale"],
                source_type=candidate["source_type"],
                source_session_id=candidate["source_session_id"],
                status=candidate["status"],
                priority=candidate["priority"],
            )
        )
    return memory_ids


def save_cue_memory_candidates_from_reference_template(
    engine: Engine,
    session_id: str,
    decision_rows: list[dict[str, Any]],
    *,
    template_type: str = "",
    scope_context: dict[str, Any] | None = None,
) -> list[str]:
    if not decision_rows:
        return []
    ensure_estimator_session_tables(engine)
    resolved_template_type = template_type
    if not resolved_template_type:
        with engine.connect() as connection:
            resolved_template_type = str(
                connection.execute(
                    text("SELECT template_type FROM estimator_sessions WHERE session_id = :session_id"),
                    {"session_id": session_id},
                ).scalar_one_or_none()
                or ""
            )
    candidates = estimator_cue_memory_candidates_from_reference_template(
        decision_rows,
        session_id=session_id,
        template_type=resolved_template_type,
        scope_context=scope_context,
    )
    memory_ids: list[str] = []
    for candidate in candidates:
        memory_ids.append(
            upsert_estimator_memory(
                engine,
                memory_id=candidate["memory_id"],
                guidance=candidate["guidance"],
                template_type=candidate["template_type"],
                decision_id=candidate["decision_id"],
                template_bucket=candidate["template_bucket"],
                product_or_system=candidate["product_or_system"],
                applies_when=candidate["applies_when"],
                rationale=candidate["rationale"],
                source_type=candidate["source_type"],
                source_session_id=candidate["source_session_id"],
                status=candidate["status"],
                priority=candidate["priority"],
            )
        )
    return memory_ids


def _reference_scope_memory_context(scope_context: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(scope_context, dict) or not scope_context:
        return {}
    key_map = {
        "project_type": "project_type",
        "project_class": "project_class",
        "building_type": "building_type",
        "market_segment": "market_segment",
        "substrate": "substrate",
        "roof_type_substrate": "substrate",
        "coating_type": "coating_type",
        "foam_type": "foam_type",
        "material_system": "material_system",
        "warranty_target_years": "warranty_years",
        "warranty_years": "warranty_years",
    }
    context: dict[str, Any] = {}
    keywords: list[str] = []
    for source_key, target_key in key_map.items():
        value = scope_context.get(source_key)
        if value in (None, "", [], {}):
            continue
        cleaned = _memory_value_text(value)
        if not cleaned:
            continue
        context[target_key] = cleaned
        for token in str(cleaned).replace("_", " ").replace("-", " ").split():
            token = token.strip().lower()
            if len(token) >= 4 and token not in keywords:
                keywords.append(token)
    notes_text = " ".join(
        str(scope_context.get(key) or "")
        for key in ("raw_input_notes", "notes", "estimator_notes", "scope_summary")
    )
    for term in (
        "silicone",
        "acrylic",
        "metal",
        "foam",
        "coating",
        "restoration",
        "repair",
        "pole barn",
        "metal building",
        "industrial",
        "residential",
        "commercial",
        "fasteners",
        "seams",
        "ponding",
    ):
        if term in notes_text.lower() and term not in keywords:
            keywords.append(term)
    if keywords:
        context["keywords"] = keywords[:16]
    return context


def _reference_scope_guidance_phrase(scope_memory_context: dict[str, Any]) -> str:
    if not scope_memory_context:
        return ""
    descriptors = [
        str(scope_memory_context.get(key) or "")
        for key in ("project_class", "project_type", "building_type", "substrate", "coating_type", "foam_type", "material_system")
        if scope_memory_context.get(key)
    ]
    if not descriptors:
        return ""
    return f"Applies-when context: {', '.join(descriptors[:5])}. "


def save_memory_candidates_from_edits(
    engine: Engine,
    session_id: str,
    edit_rows: list[dict[str, Any]],
    *,
    template_type: str = "",
) -> list[str]:
    if not edit_rows:
        return []
    ensure_estimator_session_tables(engine)
    resolved_template_type = template_type
    if not resolved_template_type:
        with engine.connect() as connection:
            resolved_template_type = str(
                connection.execute(
                    text("SELECT template_type FROM estimator_sessions WHERE session_id = :session_id"),
                    {"session_id": session_id},
                ).scalar_one_or_none()
                or ""
            )
    candidates = estimator_memory_candidates_from_edits(
        edit_rows,
        session_id=session_id,
        template_type=resolved_template_type,
    )
    memory_ids: list[str] = []
    for candidate in candidates:
        memory_ids.append(
            upsert_estimator_memory(
                engine,
                memory_id=candidate["memory_id"],
                guidance=candidate["guidance"],
                template_type=candidate["template_type"],
                decision_id=candidate["decision_id"],
                template_bucket=candidate["template_bucket"],
                applies_when=candidate["applies_when"],
                rationale=candidate["rationale"],
                source_type=candidate["source_type"],
                source_session_id=candidate["source_session_id"],
                status=candidate["status"],
                priority=candidate["priority"],
            )
        )
    return memory_ids


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
    *,
    include_full_payload: bool = True,
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
    if include_full_payload:
        files["session_payload.json"] = json.dumps(payload, indent=2, sort_keys=True, default=str)
    else:
        files["session_payload_omitted.txt"] = (
            "Full session_payload.json was omitted for fast Streamlit export. "
            "Run export_estimator_session_package(..., include_full_payload=True) for full audit payload."
        )
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
        "decision_proposal": row.get("decision_proposal"),
        "proposal_source": row.get("proposal_source"),
        "proposal_confidence": row.get("proposal_confidence"),
        "proposal_evidence": row.get("proposal_evidence") or {},
        "proposal_review_reasons": row.get("proposal_review_reasons") or [],
        "decision_evidence_types": row.get("decision_evidence_types"),
        "why_included": row.get("why_included"),
        "historical_evidence_summary": row.get("historical_evidence_summary"),
        "pricing_evidence_summary": row.get("pricing_evidence_summary"),
        "product_evidence_summary": row.get("product_evidence_summary"),
        "formula_evidence_summary": row.get("formula_evidence_summary"),
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
        "proposal_source": row.get("proposal_source"),
        "proposal_confidence": row.get("proposal_confidence"),
        "proposal_review_required": row.get("proposal_review_required"),
        "proposal_review_reasons": row.get("proposal_review_reasons") or [],
        "decision_evidence_summary": row.get("decision_evidence_summary"),
        "decision_evidence_types": row.get("decision_evidence_types"),
        "why_included": row.get("why_included"),
        "historical_evidence_summary": row.get("historical_evidence_summary"),
        "pricing_evidence_summary": row.get("pricing_evidence_summary"),
        "product_evidence_summary": row.get("product_evidence_summary"),
        "formula_evidence_summary": row.get("formula_evidence_summary"),
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
    for section in (
        "area_calculation_trace",
        "insulation_surfaces",
        "insulation_foam_template_decisions",
        "insulation_performance_specs",
        "insulation_detail_material_template_decisions",
        "insulation_thermal_barrier_template_decisions",
        "insulation_support_material_template_decisions",
        "insulation_equipment_logistics_template_decisions",
        "insulation_compliance_template_decisions",
        "insulation_labor_template_decisions",
        "insulation_pricing_template_decisions",
        "roofing_foam_template_decisions",
        "roofing_coating_template_decisions",
        "roofing_primer_template_decisions",
        "roofing_detail_template_decisions",
        "roofing_detail_quantity_template_decisions",
        "roofing_board_fastener_template_decisions",
        "roofing_granules_template_decisions",
        "roofing_equipment_template_decisions",
        "roofing_travel_freight_template_decisions",
        "roofing_accessory_template_decisions",
        "roofing_labor_template_decisions",
    ):
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
    for section in (
        "area_calculation_trace",
        "insulation_surfaces",
        "insulation_foam_template_decisions",
        "insulation_performance_specs",
        "insulation_detail_material_template_decisions",
        "insulation_thermal_barrier_template_decisions",
        "insulation_support_material_template_decisions",
        "insulation_equipment_logistics_template_decisions",
        "insulation_compliance_template_decisions",
        "insulation_labor_template_decisions",
        "insulation_pricing_template_decisions",
        "roofing_foam_template_decisions",
        "roofing_coating_template_decisions",
        "roofing_primer_template_decisions",
        "roofing_detail_template_decisions",
        "roofing_detail_quantity_template_decisions",
        "roofing_board_fastener_template_decisions",
        "roofing_granules_template_decisions",
        "roofing_equipment_template_decisions",
        "roofing_travel_freight_template_decisions",
        "roofing_accessory_template_decisions",
        "roofing_labor_template_decisions",
    ):
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
    for index, row in enumerate(draft_workbook_inputs.get("workbook_decisions") or []):
        if not isinstance(row, dict):
            continue
        category = str(row.get("category") or row.get("template_bucket") or "")
        writes.append(
            {
                "section": row.get("section") or row.get("source_section") or "workbook_decisions",
                "template_type": template_type,
                "row_index": index,
                "row_type": row.get("row_type"),
                "decision_id": row.get("decision_id"),
                "template_bucket": row.get("template_bucket") or category,
                "workbook_row": row.get("workbook_row"),
                "row_traceability": row.get("row_traceability"),
                "category": category,
                "item": row.get("item"),
                "quantity": row.get("quantity"),
                "unit_price": row.get("unit_price"),
                "estimated_cost": row.get("estimated_cost"),
                "task": row.get("task"),
                "crew_size": row.get("crew_size"),
                "total_hours": row.get("total_hours"),
                "adjusted_days": row.get("adjusted_days"),
                "formula_mode": row.get("formula_mode"),
                "formula_model": row.get("formula_model"),
                "formula_source": row.get("formula_source"),
                "calculated_output_summary": row.get("calculated_output_summary"),
                "workbook_cell_write_preview": row.get("workbook_cell_write_preview") or [],
                "target_hint": row.get("workbook_row") or category,
            }
        )
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
