from __future__ import annotations

import os
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Callable, Iterable

from .chat_assistant import EstimatorChatResult, estimator_context_summary, run_estimator_chat_turn
from .schemas import EstimatorData

PROMPT_VERSION = "staged_estimator_v1"
SESSION_SCHEMA_VERSION = 1

STAGES = (
    "job_understanding",
    "historical_retrieval",
    "historical_comparison",
    "estimating_plan",
    "decision_proposals",
    "conversational_revision",
)

FACT_LABELS = {
    "division": "Division",
    "template_type": "Template",
    "project_type": "Project type",
    "job_name": "Job name",
    "customer_name": "Customer",
    "site_address": "Site address",
    "building_type": "Building type",
    "substrate": "Substrate",
    "roof_type_substrate": "Roof substrate",
    "foam_type": "Foam type",
    "foam_thickness_inches": "Foam thickness",
    "coating_type": "Coating",
    "warranty_target_years": "Warranty",
    "building_footprint_length_ft": "Building length",
    "building_footprint_width_ft": "Building width",
    "wall_height_ft": "Wall height",
    "gross_wall_area_sqft": "Gross wall area",
    "opening_area_known_sqft": "Opening deductions",
    "ceiling_area_sqft": "Ceiling area",
    "net_insulation_area_sqft": "Net insulation area",
    "estimated_sqft": "Estimated area",
    "requested_timing": "Requested timing",
}


def configured_estimator_models() -> dict[str, str]:
    """Resolve role-specific models without embedding model IDs in workflow code."""

    generic = str(os.getenv("OPENAI_MODEL") or "").strip()
    return {
        "extraction_model": str(os.getenv("OPENAI_EXTRACTION_MODEL") or generic).strip(),
        "estimator_model": str(
            os.getenv("OPENAI_ESTIMATOR_MODEL")
            or os.getenv("OPENAI_ESTIMATOR_CHAT_MODEL")
            or generic
        ).strip(),
        "review_model": str(os.getenv("OPENAI_REVIEW_MODEL") or "").strip(),
    }


def new_estimate_session_state(*, session_id: str = "", template_type: str = "") -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return {
        "schema_version": SESSION_SCHEMA_VERSION,
        "session_id": session_id,
        "created_at": now,
        "updated_at": now,
        "session_status": "collecting_information",
        "current_stage": "job_understanding",
        "template_type": template_type,
        "division": "",
        "job_name": "",
        "site_address": "",
        "raw_user_notes": "",
        "uploaded_evidence": [],
        "job_facts": [],
        "scope_state": {},
        "assumptions": [],
        "unresolved_questions": [],
        "retrieved_historical_jobs": [],
        "historical_comparison": [],
        "rejected_precedents": [],
        "estimating_plan": {},
        "decision_template_state": [],
        "decision_change_history": [],
        "calculation_state": {},
        "workbook_state": {},
        "conversation_history": [],
        "review_flags": [],
        "confidence_summary": {},
        "approved_memories_used": [],
        "retrieved_product_knowledge": [],
        "retrieved_pricing_records": [],
        "model_metadata": configured_estimator_models(),
        "prompt_version": PROMPT_VERSION,
        "audit_events": [],
    }


def advance_estimate_session(
    messages: Iterable[dict[str, Any]],
    *,
    data: EstimatorData | None = None,
    previous_state: dict[str, Any] | None = None,
    template_type_hint: str = "",
    attached_reference_answer_key: dict[str, Any] | None = None,
    provider: Callable[[list[dict[str, Any]], str], Any] | None = None,
    model: str | None = None,
) -> tuple[EstimatorChatResult, dict[str, Any]]:
    """Run one staged estimator turn and patch the persistent job state.

    The existing chat interpreter remains the model-facing estimator. This
    function separates its output into durable stages and merges decisions by
    identity so a conversational correction cannot erase unrelated choices.
    """

    is_revision = bool(
        isinstance(previous_state, dict)
        and previous_state.get("conversation_history")
    )
    state = deepcopy(previous_state) if isinstance(previous_state, dict) else new_estimate_session_state(
        template_type=template_type_hint
    )
    history = _clean_messages(messages)
    models = configured_estimator_models()
    estimator_model = str(model or models.get("estimator_model") or "").strip()
    existing_scope = state.get("scope_state") if isinstance(state.get("scope_state"), dict) else {}
    existing_decisions = (
        state.get("decision_template_state")
        if isinstance(state.get("decision_template_state"), list)
        else []
    )
    result = run_estimator_chat_turn(
        history,
        data=data,
        template_type_hint=template_type_hint,
        existing_scope=existing_scope,
        existing_decisions=existing_decisions,
        existing_session_state=_compact_state_for_model(state),
        attached_reference_answer_key=attached_reference_answer_key,
        provider=provider,
        model=estimator_model or None,
    )

    scope = dict(result.scope_overrides or {})
    merged_decisions, decision_changes = merge_decision_patches(
        existing_decisions,
        result.workbook_decision_preferences,
    )
    context = estimator_context_summary(data, scope=scope)
    rejected = _merge_rejected_precedents(state.get("rejected_precedents"), result.raw_response)
    historical_jobs = _historical_jobs(context, rejected)
    comparison = _historical_comparison(
        historical_jobs,
        scope,
        result.raw_response.get("historical_comparison") if isinstance(result.raw_response, dict) else None,
    )
    job_facts = _job_facts(scope, history)
    questions = _unique_strings(result.missing_questions)
    assumptions = _normalize_assumptions(result.assumptions, result.raw_response)
    decisions = [_traceable_decision(row) for row in merged_decisions]
    raw_notes = "\n\n".join(
        str(message.get("content") or "").strip()
        for message in history
        if str(message.get("role") or "") == "user" and str(message.get("content") or "").strip()
    )
    status = _session_status(questions, decisions)
    now = datetime.now(UTC).isoformat()

    state.update(
        {
            "updated_at": now,
            "session_status": status,
            "current_stage": "conversational_revision" if is_revision else "decision_proposals",
            "template_type": scope.get("template_type") or state.get("template_type") or template_type_hint,
            "division": scope.get("division") or state.get("division") or "",
            "job_name": scope.get("job_name") or state.get("job_name") or "",
            "site_address": (
                scope.get("site_address")
                or scope.get("address")
                or state.get("site_address")
                or ""
            ),
            "raw_user_notes": raw_notes,
            "job_facts": job_facts,
            "scope_state": scope,
            "assumptions": assumptions,
            "unresolved_questions": questions,
            "retrieved_historical_jobs": historical_jobs,
            "historical_comparison": comparison,
            "rejected_precedents": rejected,
            "estimating_plan": _estimating_plan(scope, decisions, questions, assumptions, result.raw_response),
            "decision_template_state": decisions,
            "conversation_history": history,
            "review_flags": _unique_strings(result.warnings),
            "confidence_summary": {
                "overall": float(result.confidence or 0.0),
                "source": result.source,
                "requires_review": bool(questions or result.warnings),
            },
            "approved_memories_used": list(context.get("estimator_memory_guidance") or []),
            "retrieved_product_knowledge": list(context.get("product_guidance_digest") or [])[:12],
            "retrieved_pricing_records": list(context.get("pricing_candidates_by_bucket") or [])[:20],
            "model_metadata": {
                **models,
                "estimator_model": estimator_model,
                "response_source": result.source,
            },
            "prompt_version": PROMPT_VERSION,
        }
    )
    if decision_changes:
        state.setdefault("decision_change_history", []).append(
            {"created_at": now, "changes": decision_changes}
        )
    state.setdefault("audit_events", []).append(
        {
            "created_at": now,
            "event_type": "estimator_turn",
            "stage": state["current_stage"],
            "status": status,
            "decision_changes": decision_changes,
            "historical_job_ids": [
                row.get("job_id") or row.get("example_id") for row in historical_jobs
            ],
            "model": estimator_model,
            "prompt_version": PROMPT_VERSION,
        }
    )
    return result, state


def merge_decision_patches(
    existing: Iterable[dict[str, Any]] | None,
    patches: Iterable[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    merged: dict[tuple[str, ...], dict[str, Any]] = {}
    order: list[tuple[str, ...]] = []
    for row in existing or []:
        if not isinstance(row, dict):
            continue
        key = _decision_key(row)
        if key not in merged:
            order.append(key)
        merged[key] = deepcopy(row)

    changes: list[dict[str, Any]] = []
    for patch in patches or []:
        if not isinstance(patch, dict):
            continue
        key = _decision_key(patch)
        before = deepcopy(merged.get(key, {}))
        after = _merge_decision(before, patch)
        if key not in merged:
            order.append(key)
        merged[key] = after
        if before != after:
            changes.append(
                {
                    "decision_id": after.get("decision_id") or after.get("template_bucket") or key[-1],
                    "before": before,
                    "after": deepcopy(after),
                }
            )
    return [merged[key] for key in order], changes


def reject_historical_precedent(
    state: dict[str, Any],
    *,
    precedent_id: str,
    reason: str = "",
) -> dict[str, Any]:
    updated = deepcopy(state)
    rejected = list(updated.get("rejected_precedents") or [])
    if precedent_id and not any(str(row.get("precedent_id") or "") == precedent_id for row in rejected):
        rejected.append({"precedent_id": precedent_id, "reason": reason, "rejected_at": datetime.now(UTC).isoformat()})
    updated["rejected_precedents"] = rejected
    updated["retrieved_historical_jobs"] = [
        row
        for row in updated.get("retrieved_historical_jobs") or []
        if str(row.get("precedent_id") or row.get("job_id") or row.get("example_id") or "") != precedent_id
    ]
    updated["historical_comparison"] = [
        row
        for row in updated.get("historical_comparison") or []
        if str(row.get("precedent_id") or "") != precedent_id
    ]
    return updated


def _clean_messages(messages: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    cleaned: list[dict[str, str]] = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip().lower()
        content = str(message.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            cleaned.append({"role": role, "content": content})
    return cleaned[-30:]


def _compact_state_for_model(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_status": state.get("session_status"),
        "job_facts": state.get("job_facts") or [],
        "assumptions": state.get("assumptions") or [],
        "unresolved_questions": state.get("unresolved_questions") or [],
        "rejected_precedents": state.get("rejected_precedents") or [],
        "estimating_plan": state.get("estimating_plan") or {},
    }


def _decision_key(row: dict[str, Any]) -> tuple[str, ...]:
    decision_id = str(row.get("decision_id") or "").strip()
    if decision_id:
        return ("decision_id", decision_id)
    return (
        "row",
        str(row.get("section") or "").strip(),
        str(row.get("template_bucket") or row.get("package") or "").strip(),
        str(row.get("workbook_row") or row.get("row_number") or "").strip(),
    )


def _merge_decision(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in patch.items():
        if key == "proposed_values" and isinstance(value, dict):
            merged[key] = {**(merged.get(key) or {}), **value}
        elif key in {"evidence", "source_ids", "assumptions", "review_reasons"}:
            merged[key] = _unique_values([*(merged.get(key) or []), *(value if isinstance(value, list) else [value])])
        elif value is not None:
            merged[key] = deepcopy(value)
    return merged


def _traceable_decision(row: dict[str, Any]) -> dict[str, Any]:
    decision = deepcopy(row)
    evidence = decision.get("evidence")
    evidence_rows = evidence if isinstance(evidence, list) else ([evidence] if isinstance(evidence, dict) else [])
    source_ids = list(decision.get("source_ids") or [])
    for item in evidence_rows:
        if not isinstance(item, dict):
            continue
        for key in ("job_id", "example_id", "memory_id", "product_id", "pricing_id", "source_file"):
            value = item.get(key)
            if value not in (None, "") and str(value) not in source_ids:
                source_ids.append(str(value))
    decision.setdefault("proposed_value", decision.get("proposed_values") or decision.get("include"))
    decision.setdefault("source_type", decision.get("source") or "model_inference")
    decision["source_ids"] = source_ids
    decision.setdefault("relationship_type", "supports")
    decision.setdefault("assumptions", [])
    decision.setdefault("review_required", float(decision.get("confidence") or 0.0) < 0.7)
    decision.setdefault("reason", "; ".join(str(value) for value in decision.get("review_reasons") or []))
    return decision


def _job_facts(scope: dict[str, Any], history: list[dict[str, str]]) -> list[dict[str, Any]]:
    user_text = " ".join(message["content"].lower() for message in history if message["role"] == "user")
    rows: list[dict[str, Any]] = []
    for field, label in FACT_LABELS.items():
        value = scope.get(field)
        if value in (None, "", [], {}):
            continue
        literal = str(value).lower().replace("_", " ")
        explicit = literal in user_text or field in {
            "building_footprint_length_ft",
            "building_footprint_width_ft",
            "wall_height_ft",
            "openings",
        }
        rows.append(
            {
                "field": field,
                "label": label,
                "value": value,
                "fact_type": "explicit_or_measured" if explicit else "interpreted",
                "source_type": "user_conversation" if explicit else "scope_interpretation",
            }
        )
    return rows


def _historical_jobs(context: dict[str, Any], rejected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rejected_ids = {str(row.get("precedent_id") or "") for row in rejected}
    answer_keys = (context.get("historical_answer_key_examples") or {}).get("matched_answer_keys") or []
    examples = (context.get("historical_template_examples") or {}).get("matched_examples") or []
    profiles = (context.get("historical_job_context") or {}).get("matched_profiles") or []
    candidates = answer_keys or examples or profiles
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in candidates:
        if not isinstance(raw, dict):
            continue
        precedent_id = str(raw.get("job_id") or raw.get("example_id") or raw.get("source_file") or "").strip()
        if not precedent_id or precedent_id in rejected_ids or precedent_id in seen:
            continue
        seen.add(precedent_id)
        rows.append(
            {
                "precedent_id": precedent_id,
                "job_id": raw.get("job_id"),
                "example_id": raw.get("example_id"),
                "job_name": raw.get("job_name"),
                "customer": raw.get("customer"),
                "year": raw.get("year") or raw.get("estimate_year"),
                "division": raw.get("division") or raw.get("template_type"),
                "job_type": raw.get("project_class") or raw.get("building_type"),
                "square_feet": raw.get("area_sqft"),
                "scope_summary": raw.get("scope_summary"),
                "material_system": raw.get("material_system"),
                "warranty_years": raw.get("warranty_years"),
                "similarity_score": raw.get("similarity_score"),
                "match_reasons": raw.get("match_reasons") or [],
                "source_file": raw.get("source_file"),
                "source_url": raw.get("source_url") or raw.get("folder_url") or raw.get("web_url"),
                "reference_answer_key": raw.get("reference_answer_key") or {},
                "decisions": raw.get("decisions") or [],
            }
        )
        if len(rows) >= 7:
            break
    return rows


def _historical_comparison(
    historical_jobs: list[dict[str, Any]],
    scope: dict[str, Any],
    ai_comparison: Any,
) -> list[dict[str, Any]]:
    ai_rows = ai_comparison if isinstance(ai_comparison, list) else []
    ai_by_id = {
        str(row.get("precedent_id") or row.get("job_id") or ""): row
        for row in ai_rows
        if isinstance(row, dict)
    }
    comparisons: list[dict[str, Any]] = []
    current_area = _number(scope.get("estimated_sqft") or scope.get("net_insulation_area_sqft"))
    for job in historical_jobs:
        precedent_id = str(job.get("precedent_id") or "")
        supplied = ai_by_id.get(precedent_id, {})
        differences = list(supplied.get("differences") or [])
        historical_area = _number(job.get("square_feet"))
        if current_area and historical_area:
            delta = round(current_area - historical_area, 1)
            if abs(delta) > max(100.0, historical_area * 0.05):
                differences.append(f"Current area differs by {delta:+,.0f} sq ft.")
        for field, label in (
            ("material_system", "material system"),
            ("warranty_years", "warranty"),
        ):
            current = scope.get(field)
            historical = job.get(field)
            if current and historical and str(current).lower() != str(historical).lower():
                differences.append(f"Different {label}: current {current}; precedent {historical}.")
        comparisons.append(
            {
                "precedent_id": precedent_id,
                "why_relevant": supplied.get("why_relevant")
                or "; ".join(str(value) for value in job.get("match_reasons") or [])
                or "Retrieved from normalized historical estimate similarity.",
                "similarities": supplied.get("similarities") or job.get("match_reasons") or [],
                "differences": _unique_strings(differences),
                "template_selections": supplied.get("template_selections") or [],
                "material_assumptions": supplied.get("material_assumptions") or [],
                "labor_assumptions": supplied.get("labor_assumptions") or [],
                "exclusions": supplied.get("exclusions") or [],
                "influence_confidence": supplied.get("influence_confidence")
                or _score_to_confidence(job.get("similarity_score")),
            }
        )
    return comparisons


def _estimating_plan(
    scope: dict[str, Any],
    decisions: list[dict[str, Any]],
    questions: list[str],
    assumptions: list[dict[str, Any]],
    raw_response: dict[str, Any],
) -> dict[str, Any]:
    supplied = raw_response.get("estimating_plan") if isinstance(raw_response, dict) else {}
    if isinstance(supplied, dict) and supplied:
        return supplied
    included = [row for row in decisions if row.get("include") is True]
    return {
        "template_bucket": scope.get("template_type") or scope.get("division"),
        "proposed_scope": scope.get("project_type") or scope.get("scope_summary"),
        "takeoff_basis_sqft": scope.get("net_insulation_area_sqft") or scope.get("estimated_sqft"),
        "recommended_systems": _unique_strings(
            [
                scope.get("foam_type"),
                scope.get("coating_type"),
                *[row.get("template_bucket") for row in included],
            ]
        ),
        "likely_adders": [
            row.get("template_bucket") for row in included if "adder" in str(row.get("section") or "")
        ],
        "assumptions": assumptions,
        "unresolved_questions": questions,
        "decision_count": len(decisions),
    }


def _normalize_assumptions(values: Any, raw_response: dict[str, Any]) -> list[dict[str, Any]]:
    raw = values if isinstance(values, list) else []
    supplied = raw_response.get("assumption_details") if isinstance(raw_response, dict) else []
    rows: list[dict[str, Any]] = []
    for item in supplied if isinstance(supplied, list) else []:
        if isinstance(item, dict) and item.get("assumption"):
            rows.append(dict(item))
    for item in raw:
        text = str(item or "").strip()
        if text and not any(str(row.get("assumption") or "") == text for row in rows):
            rows.append(
                {
                    "assumption": text,
                    "confidence": "medium",
                    "financial_impact": "unknown",
                    "confirmed": False,
                }
            )
    return rows


def _merge_rejected_precedents(existing: Any, raw_response: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [dict(row) for row in existing or [] if isinstance(row, dict)]
    supplied = raw_response.get("rejected_precedents") if isinstance(raw_response, dict) else []
    for item in supplied if isinstance(supplied, list) else []:
        normalized = item if isinstance(item, dict) else {"precedent_id": str(item)}
        precedent_id = str(normalized.get("precedent_id") or normalized.get("job_id") or "").strip()
        if precedent_id and not any(str(row.get("precedent_id") or "") == precedent_id for row in rows):
            rows.append({**normalized, "precedent_id": precedent_id})
    return rows


def _session_status(questions: list[str], decisions: list[dict[str, Any]]) -> str:
    blocking = any(
        token in question.lower()
        for question in questions
        for token in ("required", "before", "address", "thickness", "system", "warranty")
    )
    if blocking:
        return "awaiting_clarification"
    if decisions:
        return "draft_ready"
    return "collecting_information"


def _score_to_confidence(value: Any) -> float:
    score = _number(value)
    if score <= 1:
        return round(max(0.0, min(score, 0.95)), 2)
    return round(max(0.0, min(score / 200.0, 0.95)), 2)


def _number(value: Any) -> float:
    try:
        return float(str(value or "").replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _unique_strings(values: Iterable[Any] | None) -> list[str]:
    return [str(value).strip() for value in _unique_values(values or []) if str(value).strip()]


def _unique_values(values: Iterable[Any]) -> list[Any]:
    rows: list[Any] = []
    for value in values:
        if value in (None, "", [], {}):
            continue
        if value not in rows:
            rows.append(value)
    return rows
