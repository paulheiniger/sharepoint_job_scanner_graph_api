from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Callable, Iterable

from .chat_assistant import (
    EstimatorChatResult,
    _bounded_prompt_context,
    _json_character_count,
    _positive_int_environment,
    estimator_context_summary,
    run_estimator_chat_turn,
)
from .model_routing import (
    configured_estimator_models,
    model_call_metadata,
    route_estimator_model,
)
from .readiness import evaluate_estimate_readiness
from .schemas import EstimatorData
from .workbench import WORKBENCH_DECISION_SECTIONS, recalculate_workbench_tables, summarize_workbench_totals

PROMPT_VERSION = "staged_estimator_v2"
REVIEW_PROMPT_VERSION = "staged_estimator_review_v1"
SESSION_SCHEMA_VERSION = 3
DEFAULT_REVIEW_MAX_INPUT_CHARACTERS = 75_000
DEFAULT_REVIEW_MAX_OUTPUT_TOKENS = 6_000

DEPENDENT_BUCKETS = {
    "foam": {"labor_foam", "drum_disposal", "truck_expense"},
    "thermal_barrier_coating": {"labor_dc_315"},
    "primer": {"labor_prime"},
    "membrane": {"labor_membrane"},
    "coating": {"labor_base", "labor_top_coat"},
    "caulk_detail": {"labor_caulk", "labor_details"},
    "caulk_sealant": {"labor_caulk", "labor_details"},
}

CALCULATED_OUTPUT_FIELDS = (
    "estimated_units",
    "estimated_sets",
    "estimated_gallons",
    "estimated_squares",
    "calculated_quantity",
    "calculated_hours",
    "total_hours",
    "days",
    "estimated_cost",
    "calculated_output",
    "calculated_output_summary",
)

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
        "approved_memories_retrieved": [],
        "retrieved_product_knowledge": [],
        "retrieved_pricing_records": [],
        "dependency_state": {},
        "review_state": {},
        "model_metadata": configured_estimator_models(),
        "model_routes": [],
        "model_call_history": [],
        "prompt_version": PROMPT_VERSION,
        "audit_events": [],
        "readiness_state": {},
        "learning_candidates": [],
    }


def advance_estimate_session(
    messages: Iterable[dict[str, Any]],
    *,
    data: EstimatorData | None = None,
    previous_state: dict[str, Any] | None = None,
    template_type_hint: str = "",
    attached_reference_answer_key: dict[str, Any] | None = None,
    visual_evidence: dict[str, Any] | None = None,
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
    estimator_route = route_estimator_model("estimator", explicit_model=model)
    estimator_model = str(estimator_route.get("model") or "").strip()
    normalized_visual = build_staged_visual_evidence(visual_evidence or {})
    existing_scope = _scope_with_visual_evidence(
        state.get("scope_state") if isinstance(state.get("scope_state"), dict) else {},
        normalized_visual,
    )
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
        [
            *(normalized_visual.get("decision_patches") or []),
            *result.workbook_decision_preferences,
        ],
    )
    context = estimator_context_summary(data, scope=scope)
    rejected = _merge_rejected_precedents(state.get("rejected_precedents"), result.raw_response)
    historical_jobs = _historical_jobs(context, rejected)
    comparison = _historical_comparison(
        historical_jobs,
        scope,
        result.raw_response.get("historical_comparison") if isinstance(result.raw_response, dict) else None,
    )
    job_facts = _merge_job_facts(
        _job_facts(scope, history),
        normalized_visual.get("job_facts") or [],
    )
    questions = _unique_strings(
        [
            *result.missing_questions,
            *(normalized_visual.get("questions") or []),
        ]
    )
    assumptions = merge_estimate_assumptions(
        state.get("assumptions"),
        _normalize_assumptions(result.assumptions, result.raw_response),
    )
    retrieved_memories = list(context.get("estimator_memory_guidance") or [])
    decisions, used_memories = attach_approved_memory_evidence(
        [_traceable_decision(row) for row in merged_decisions],
        retrieved_memories,
    )
    scope_changes = _changed_scope_fields(existing_scope, scope)
    calculation_changes = [
        change for change in decision_changes if _decision_calculation_changed(change)
    ]
    decisions, calculation_state = recalculate_dependent_decisions(
        scope=scope,
        decisions=decisions,
        decision_changes=calculation_changes,
        scope_changes=scope_changes,
        previous_calculation_state=state.get("calculation_state"),
        data=data,
    )
    raw_notes = "\n\n".join(
        str(message.get("content") or "").strip()
        for message in history
        if str(message.get("role") or "") == "user" and str(message.get("content") or "").strip()
    )
    status = _session_status(questions, decisions)
    now = datetime.now(UTC).isoformat()
    model_call = (
        result.raw_response.get("_model_call")
        if isinstance(result.raw_response, dict)
        and isinstance(result.raw_response.get("_model_call"), dict)
        else {}
    )
    model_routes = list(state.get("model_routes") or [])
    model_routes.extend(normalized_visual.get("model_routes") or [])
    model_routes.append(estimator_route)
    model_call_history = list(state.get("model_call_history") or [])
    model_call_history.extend(normalized_visual.get("model_calls") or [])
    if model_call:
        model_call_history.append(model_call)
    uploaded_evidence = _merge_uploaded_evidence(
        state.get("uploaded_evidence"),
        normalized_visual.get("records") or [],
    )
    if (
        decision_changes or scope_changes
    ) and isinstance(state.get("review_state"), dict) and state.get("review_state"):
        state["review_state"] = {
            **state["review_state"],
            "status": "stale",
            "stale_at": now,
            "stale_reason": "The estimate changed after this review.",
        }

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
            "uploaded_evidence": uploaded_evidence,
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
            "review_flags": _unique_strings(
                [
                    *result.warnings,
                    *(normalized_visual.get("warnings") or []),
                ]
            ),
            "confidence_summary": {
                "overall": float(result.confidence or 0.0),
                "source": result.source,
                "requires_review": bool(questions or result.warnings),
            },
            "approved_memories_retrieved": retrieved_memories,
            "approved_memories_used": used_memories,
            "retrieved_product_knowledge": list(context.get("product_guidance_digest") or [])[:12],
            "retrieved_pricing_records": list(context.get("pricing_candidates_by_bucket") or [])[:20],
            "calculation_state": calculation_state,
            "dependency_state": {
                "scope_fields_changed": scope_changes,
                "decision_ids_changed": [
                    str(row.get("decision_id") or "") for row in calculation_changes
                ],
                "decision_ids_recalculated": calculation_state.get("affected_decision_ids") or [],
            },
            "model_metadata": {
                **models,
                "estimator_model": estimator_model,
                "response_source": result.source,
            },
            "model_routes": model_routes[-50:],
            "model_call_history": _dedupe_dict_rows(
                model_call_history,
                ("request_id", "completed_at", "requested_model"),
            )[-100:],
            "visual_evidence_summary": normalized_visual.get("summary") or {},
            "prompt_version": PROMPT_VERSION,
        }
    )
    if decision_changes or scope_changes:
        state.pop("approved_at", None)
        state.pop("approved_by", None)
    if decision_changes:
        state.setdefault("decision_change_history", []).append(
            {"created_at": now, "changes": decision_changes}
        )
    state["readiness_state"] = evaluate_estimate_readiness(state)
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
            "memory_ids_used": [
                row.get("memory_id") for row in used_memories if row.get("memory_id")
            ],
            "decision_ids_recalculated": calculation_state.get("affected_decision_ids") or [],
            "visual_evidence_ids": [
                row.get("evidence_id")
                for row in normalized_visual.get("records") or []
                if row.get("evidence_id")
            ],
            "model": estimator_model,
            "model_call": model_call,
            "model_route": estimator_route,
            "prompt_version": PROMPT_VERSION,
        }
    )
    return result, state


def _scope_with_visual_evidence(
    scope: dict[str, Any],
    normalized_visual: dict[str, Any],
) -> dict[str, Any]:
    """Expose current structured image takeoff to the estimator on this turn."""

    merged = dict(scope or {})
    records = [
        row
        for row in normalized_visual.get("records") or []
        if isinstance(row, dict) and row.get("evidence_type") == "annotated_scope_image"
    ]
    if not records:
        return merged
    record = records[-1]
    for field in (
        "area_scopes",
        "linear_scopes",
        "retain_existing",
        "scope_relationships",
        "area_reconciliation",
    ):
        value = record.get(field)
        if value not in (None, "", [], {}):
            merged[field] = value
    header = {
        **(record.get("customer_info") or {}),
        **(record.get("job_header") or {}),
    }
    for source_key, destination_key in (
        ("job_name", "job_name"),
        ("customer", "customer"),
        ("customer_name", "customer"),
        ("site_address", "site_address"),
        ("address", "site_address"),
    ):
        value = header.get(source_key)
        if value not in (None, "", [], {}) and not merged.get(destination_key):
            merged[destination_key] = value
    declared_area = _number(
        header.get("declared_total_area_sqft")
        or (record.get("area_reconciliation") or {}).get("declared_total_area_sqft")
        or (record.get("area_reconciliation") or {}).get("declared_total")
    )
    if declared_area > 0 and not (
        merged.get("estimated_sqft")
        or merged.get("net_sqft")
        or merged.get("area_sqft")
    ):
        merged["estimated_sqft"] = declared_area
    merged["visual_evidence_ids"] = [
        row.get("evidence_id")
        for row in records
        if row.get("evidence_id")
    ]
    return merged


def build_staged_visual_evidence(visual_evidence: dict[str, Any]) -> dict[str, Any]:
    """Normalize selected visual inputs into traceable staged-session evidence."""

    note_result = (
        visual_evidence.get("note_image_result")
        if isinstance(visual_evidence.get("note_image_result"), dict)
        else {}
    )
    photo_context = (
        visual_evidence.get("photo_context")
        if isinstance(visual_evidence.get("photo_context"), dict)
        else {}
    )
    records: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    questions: list[str] = []
    warnings: list[str] = []
    model_calls: list[dict[str, Any]] = []
    model_routes: list[dict[str, Any]] = []
    decision_patches: list[dict[str, Any]] = []

    if note_result:
        source_images = _unique_strings(note_result.get("source_images") or [])
        evidence_id = "visual-note-" + _stable_evidence_token(
            [*source_images, note_result.get("document_type")]
        )
        record = {
            "evidence_id": evidence_id,
            "evidence_type": "annotated_scope_image",
            "document_type": note_result.get("document_type"),
            "source_image_ids": source_images,
            "confidence": _number(note_result.get("confidence")),
            "analysis_method": note_result.get("analysis_method") or "ai_visual_extraction",
            "ai_model": note_result.get("ai_model"),
            "job_header": note_result.get("job_header") or {},
            "customer_info": note_result.get("customer_info") or {},
            "measurements": note_result.get("measurements") or [],
            "area_scopes": note_result.get("area_scopes") or [],
            "linear_scopes": note_result.get("linear_scopes") or [],
            "retain_existing": note_result.get("retain_existing") or [],
            "scope_relationships": note_result.get("scope_relationships") or [],
            "area_reconciliation": note_result.get("area_reconciliation") or {},
            "estimator_decision_cues": note_result.get("estimator_decision_cues") or [],
            "cache_hit": bool(note_result.get("cache_hit")),
        }
        records.append(record)
        facts.extend(_visual_job_facts(record))
        questions.extend(str(value) for value in note_result.get("questions") or [])
        questions.extend(
            f"Clarify unreadable image region: {value}"
            for value in note_result.get("unreadable_regions") or []
        )
        warnings.extend(str(value) for value in note_result.get("warnings") or [])
        if isinstance(note_result.get("model_call"), dict) and note_result.get("model_call"):
            model_calls.append(dict(note_result["model_call"]))
        if isinstance(note_result.get("model_route"), dict) and note_result.get("model_route"):
            model_routes.append(dict(note_result["model_route"]))

    if photo_context:
        ai_analysis = (
            photo_context.get("ai_photo_analysis")
            if isinstance(photo_context.get("ai_photo_analysis"), dict)
            else {}
        )
        selected_ids = _unique_strings(
            photo_context.get("selected_image_ids")
            or [
                row.get("image_id")
                for row in ai_analysis.get("source_images") or []
                if isinstance(row, dict)
            ]
        )
        selected_hashes = _unique_strings(photo_context.get("selected_hashes") or [])
        evidence_id = "site-photo-" + _stable_evidence_token([*selected_ids, *selected_hashes])
        records.append(
            {
                "evidence_id": evidence_id,
                "evidence_type": "site_photo_analysis",
                "source_image_ids": selected_ids,
                "source_hashes": selected_hashes,
                "confidence": _number(photo_context.get("confidence")),
                "analysis_method": (
                    "ai_vision"
                    if photo_context.get("ai_photo_analysis_used")
                    else "local_photo_classification"
                ),
                "signals": photo_context.get("signals") or [],
                "visible_issues": photo_context.get("visible_issues") or [],
                "risk_flags": photo_context.get("risk_flags") or [],
                "missing_photos": photo_context.get("missing_photos") or [],
            }
        )
        questions.extend(
            f"Photo evidence is incomplete: provide {value}."
            for value in photo_context.get("missing_photos") or []
        )
        warnings.extend(str(value) for value in photo_context.get("risk_flags") or [])
        decision_patches.extend(
            {
                **dict(row),
                "review_required": True,
                "source": "photo_evidence",
            }
            for row in photo_context.get("photo_decision_proposals") or []
            if isinstance(row, dict)
        )
        if isinstance(ai_analysis.get("model_call"), dict) and ai_analysis.get("model_call"):
            model_calls.append(dict(ai_analysis["model_call"]))
        if isinstance(ai_analysis.get("model_route"), dict) and ai_analysis.get("model_route"):
            model_routes.append(dict(ai_analysis["model_route"]))

    return {
        "records": records,
        "job_facts": facts,
        "questions": _unique_strings(questions),
        "warnings": _unique_strings(warnings),
        "decision_patches": decision_patches,
        "model_calls": _dedupe_dict_rows(
            model_calls,
            ("request_id", "completed_at", "requested_model"),
        ),
        "model_routes": _dedupe_dict_rows(
            model_routes,
            ("role", "model", "routed_at"),
        ),
        "summary": {
            "evidence_count": len(records),
            "annotated_scope_count": sum(
                1 for row in records if row.get("evidence_type") == "annotated_scope_image"
            ),
            "site_photo_count": sum(
                1 for row in records if row.get("evidence_type") == "site_photo_analysis"
            ),
            "decision_patch_count": len(decision_patches),
        },
    }


def attach_approved_memory_evidence(
    decisions: list[dict[str, Any]],
    memories: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Attach only relevant approved memories to matching decision nodes."""

    updated = deepcopy(decisions)
    used: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for decision in updated:
        decision_id = _token(decision.get("decision_id"))
        bucket = _token(decision.get("template_bucket"))
        for memory in memories:
            if not isinstance(memory, dict):
                continue
            memory_decision = _token(memory.get("decision_id"))
            memory_bucket = _token(memory.get("template_bucket"))
            if not (
                (memory_decision and memory_decision == decision_id)
                or (memory_bucket and memory_bucket == bucket)
            ):
                continue
            evidence = _normalize_decision_evidence(decision.get("evidence"))
            evidence_row = {
                "source_type": "approved_memory",
                "memory_id": memory.get("memory_id"),
                "guidance": memory.get("guidance"),
                "rationale": memory.get("rationale"),
            }
            memory_evidence = evidence.setdefault("approved_memory", [])
            if evidence_row not in memory_evidence:
                memory_evidence.append(evidence_row)
            decision["evidence"] = evidence
            source_ids = list(decision.get("source_ids") or [])
            memory_id = str(memory.get("memory_id") or "")
            if memory_id and memory_id not in source_ids:
                source_ids.append(memory_id)
            decision["source_ids"] = source_ids
            if memory_id not in used_ids:
                used.append(dict(memory))
                used_ids.add(memory_id)
    return updated, used


def recalculate_dependent_decisions(
    *,
    scope: dict[str, Any],
    decisions: list[dict[str, Any]],
    decision_changes: list[dict[str, Any]],
    scope_changes: list[str],
    previous_calculation_state: Any = None,
    data: EstimatorData | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Use the existing workbench formulas and update only affected snapshots."""

    previous = previous_calculation_state if isinstance(previous_calculation_state, dict) else {}
    previous_outputs = (
        deepcopy(previous.get("decision_outputs"))
        if isinstance(previous.get("decision_outputs"), dict)
        else {}
    )
    changed_ids = {
        str(row.get("decision_id") or "")
        for row in decision_changes
        if str(row.get("decision_id") or "")
    }
    first_calculation = not previous_outputs
    affected = _affected_decision_ids(
        decisions,
        changed_ids=changed_ids,
        scope_changes=scope_changes,
        include_all=first_calculation,
    )
    if not affected and previous_outputs:
        return decisions, {
            **previous,
            "affected_decision_ids": [],
            "scope_fields_changed": scope_changes,
        }

    workbench = recalculate_workbench_tables(
        {
            "scope": dict(scope),
            "decision_proposals": [
                {
                    **deepcopy(decision),
                    "original_source": decision.get("source"),
                    "source": "chat_estimator",
                }
                for decision in decisions
            ],
        },
        data=data,
    )
    recalculated_rows = _workbench_decision_rows(workbench)
    recalculated_lookup: dict[str, dict[str, Any]] = {}
    for row in recalculated_rows:
        for key in _calculation_keys(row):
            recalculated_lookup.setdefault(key, row)
    updated_decisions: list[dict[str, Any]] = []
    outputs = previous_outputs
    for decision in decisions:
        decision_id = str(decision.get("decision_id") or "")
        copied = deepcopy(decision)
        if decision_id in affected:
            source = next(
                (
                    recalculated_lookup[key]
                    for key in _calculation_keys(decision)
                    if key in recalculated_lookup
                ),
                {},
            )
            calculated = {
                field: source.get(field)
                for field in CALCULATED_OUTPUT_FIELDS
                if source.get(field) not in (None, "")
            }
            calculated["formula_source"] = "workbench_formula_engine"
            calculated["recalculated_at"] = datetime.now(UTC).isoformat()
            copied["calculated_outputs"] = calculated
            outputs[decision_id or next(iter(_calculation_keys(decision)), "")] = calculated
        elif decision_id in outputs:
            copied["calculated_outputs"] = deepcopy(outputs[decision_id])
        updated_decisions.append(copied)
    return updated_decisions, {
        "decision_outputs": outputs,
        "affected_decision_ids": sorted(affected),
        "scope_fields_changed": scope_changes,
        "totals": summarize_workbench_totals(workbench),
        "formula_engine": "jobscan.estimator.workbench",
        "calculated_at": datetime.now(UTC).isoformat(),
    }


def estimate_review_reasons(state: dict[str, Any], *, user_requested: bool = False) -> list[str]:
    reasons: list[str] = []
    raw_confidence = (state.get("confidence_summary") or {}).get("overall")
    confidence = _number(raw_confidence)
    if user_requested:
        reasons.append("Estimator requested a second review.")
    if raw_confidence is not None and confidence < 0.65:
        reasons.append(f"Overall estimator confidence is {confidence:.0%}.")
    if len(state.get("review_flags") or []) >= 2:
        reasons.append("Multiple review flags remain unresolved.")
    if len(state.get("unresolved_questions") or []) >= 2:
        reasons.append("Multiple material questions remain unresolved.")
    comparisons = state.get("historical_comparison") or []
    if comparisons and all(_number(row.get("influence_confidence")) < 0.55 for row in comparisons):
        reasons.append("Retrieved historical precedents are weak or conflicting.")
    total = _number((state.get("calculation_state") or {}).get("totals", {}).get("draft_total"))
    if total >= 100000:
        reasons.append("Draft value exceeds the high-value review threshold.")
    return _unique_strings(reasons)


def run_estimate_review(
    state: dict[str, Any],
    *,
    provider: Callable[[list[dict[str, Any]], str], Any] | None = None,
    model: str | None = None,
    user_requested: bool = False,
) -> dict[str, Any]:
    reasons = estimate_review_reasons(state, user_requested=user_requested)
    review_route = route_estimator_model(
        "review",
        state=state,
        explicit_model=model,
        user_requested=user_requested,
        trigger_reasons=reasons,
    )
    review_model = str(review_route.get("model") or "").strip()
    if not review_model:
        raise ValueError("OPENAI_REVIEW_MODEL is not configured.")
    prompt = _review_prompt_messages(state, reasons)
    raw = provider(prompt, review_model) if provider is not None else _call_review_model(prompt, review_model)
    payload = _json_payload(raw)
    model_call = payload.get("_model_call") if isinstance(payload.get("_model_call"), dict) else {}
    verdict = str(payload.get("verdict") or "needs_clarification").strip().lower()
    if verdict not in {"approve", "needs_changes", "needs_clarification"}:
        verdict = "needs_clarification"
    review = {
        "status": "completed",
        "reviewed_at": datetime.now(UTC).isoformat(),
        "model": review_model,
        "prompt_version": REVIEW_PROMPT_VERSION,
        "trigger_reasons": reasons,
        "verdict": verdict,
        "summary": str(payload.get("summary") or ""),
        "issues": [
            dict(row)
            for row in payload.get("issues") or []
            if isinstance(row, dict)
        ],
        "confidence": _score_to_confidence(payload.get("confidence")),
        "applied": False,
        "model_route": review_route,
        "model_call": model_call,
    }
    state["review_state"] = review
    state["session_status"] = "estimator_review"
    state["current_stage"] = "independent_review"
    state["updated_at"] = review["reviewed_at"]
    state.setdefault("model_routes", []).append(review_route)
    if model_call:
        state.setdefault("model_call_history", []).append(model_call)
    state.setdefault("audit_events", []).append(
        {
            "created_at": review["reviewed_at"],
            "event_type": "stronger_model_review",
            "model": review_model,
            "verdict": review["verdict"],
            "issue_count": len(review["issues"]),
            "model_call": model_call,
            "model_route": review_route,
            "prompt_version": REVIEW_PROMPT_VERSION,
        }
    )
    return review


def apply_review_recommendations(
    state: dict[str, Any],
    *,
    data: EstimatorData | None = None,
) -> dict[str, Any]:
    updated = deepcopy(state)
    review = updated.get("review_state") if isinstance(updated.get("review_state"), dict) else {}
    if review.get("status") != "completed":
        raise ValueError("Only a current completed review can be applied.")
    patches = [
        row.get("recommended_patch")
        for row in review.get("issues") or []
        if isinstance(row, dict) and isinstance(row.get("recommended_patch"), dict)
    ]
    if not patches:
        raise ValueError("The review did not provide any decision patches.")
    decisions, changes = merge_decision_patches(updated.get("decision_template_state"), patches)
    decisions = [_traceable_decision(row) for row in decisions]
    decisions, calculation = recalculate_dependent_decisions(
        scope=updated.get("scope_state") or {},
        decisions=decisions,
        decision_changes=changes,
        scope_changes=[],
        previous_calculation_state=updated.get("calculation_state"),
        data=data,
    )
    now = datetime.now(UTC).isoformat()
    updated["decision_template_state"] = decisions
    updated["calculation_state"] = calculation
    updated.setdefault("decision_change_history", []).append(
        {"created_at": now, "source": "review_model", "changes": changes}
    )
    updated["review_state"] = {**review, "applied": True, "applied_at": now}
    updated["updated_at"] = now
    updated["session_status"] = "estimator_review"
    updated.setdefault("audit_events", []).append(
        {
            "created_at": now,
            "event_type": "review_recommendations_applied",
            "decision_changes": changes,
        }
    )
    updated.pop("approved_at", None)
    updated.pop("approved_by", None)
    updated["readiness_state"] = evaluate_estimate_readiness(updated)
    return updated


def update_estimate_decision(
    state: dict[str, Any],
    *,
    decision_id: str,
    include: bool | None = None,
    proposed_values: dict[str, Any] | None = None,
    reason: str = "",
    action: str = "edit",
    actor: str = "estimator",
    data: EstimatorData | None = None,
) -> dict[str, Any]:
    """Apply an explicit estimator edit or acceptance to one decision."""

    normalized_action = str(action or "edit").strip().lower()
    if normalized_action not in {"accept", "edit"}:
        raise ValueError("Decision action must be 'accept' or 'edit'.")
    updated = deepcopy(state)
    existing = [
        row
        for row in updated.get("decision_template_state") or []
        if isinstance(row, dict)
    ]
    current = next(
        (
            row
            for row in existing
            if str(row.get("decision_id") or row.get("template_bucket") or "") == decision_id
        ),
        None,
    )
    if current is None:
        raise ValueError(f"Decision not found: {decision_id}")

    now = datetime.now(UTC).isoformat()
    event_id = "estimator-action-" + _stable_evidence_token(
        [updated.get("session_id"), decision_id, normalized_action, now]
    )
    changes: list[dict[str, Any]] = []
    decisions = deepcopy(existing)
    if normalized_action == "edit":
        patch: dict[str, Any] = {
            "decision_id": current.get("decision_id"),
            "template_bucket": current.get("template_bucket"),
            "section": current.get("section"),
            "workbook_row": current.get("workbook_row"),
            "source": "explicit_estimator_edit",
            "source_type": "explicit_estimator_edit",
            "confidence": 1.0,
            "review_required": False,
            "review_status": "edited",
            "reviewed_at": now,
            "reviewed_by": actor,
            "reason": reason or "Edited directly by estimator.",
            "source_ids": [
                *list(current.get("source_ids") or []),
                event_id,
            ],
        }
        if include is not None:
            patch["include"] = bool(include)
        if proposed_values is not None:
            patch["proposed_values"] = dict(proposed_values)
        decisions, changes = merge_decision_patches(existing, [patch])
        decisions = [_traceable_decision(row) for row in decisions]
        decisions, calculation = recalculate_dependent_decisions(
            scope=updated.get("scope_state") or {},
            decisions=decisions,
            decision_changes=changes,
            scope_changes=[],
            previous_calculation_state=updated.get("calculation_state"),
            data=data,
        )
        updated["calculation_state"] = calculation
    else:
        for decision in decisions:
            if str(decision.get("decision_id") or decision.get("template_bucket") or "") != decision_id:
                continue
            decision["review_status"] = "accepted"
            decision["review_required"] = False
            decision["accepted_at"] = now
            decision["accepted_by"] = actor
            if reason:
                decision["acceptance_note"] = reason
            break

    updated["decision_template_state"] = decisions
    if changes:
        updated.setdefault("decision_change_history", []).append(
            {
                "created_at": now,
                "source": "structured_estimator_edit",
                "changes": changes,
            }
        )
    updated.setdefault("audit_events", []).append(
        {
            "created_at": now,
            "event_type": f"decision_{normalized_action}ed",
            "event_id": event_id,
            "decision_id": decision_id,
            "actor": actor,
            "reason": reason,
            "changes": changes,
        }
    )
    if normalized_action == "edit":
        updated.setdefault("learning_candidates", []).append(
            {
                "candidate_id": event_id,
                "candidate_type": "decision_correction",
                "status": "pending",
                "created_at": now,
                "decision_id": decision_id,
                "reason": reason,
                "changes": changes,
            }
        )
    updated.pop("approved_at", None)
    updated.pop("approved_by", None)
    updated["session_status"] = "estimator_review"
    updated["current_stage"] = "structured_review"
    updated["updated_at"] = now
    updated["readiness_state"] = evaluate_estimate_readiness(updated)
    return updated


def confirm_estimate_assumption(
    state: dict[str, Any],
    *,
    assumption_id: str,
    confirmed: bool = True,
    note: str = "",
    actor: str = "estimator",
) -> dict[str, Any]:
    """Persist an estimator confirmation or rejection of one assumption."""

    updated = deepcopy(state)
    assumptions = [
        _traceable_assumption(row)
        for row in updated.get("assumptions") or []
        if isinstance(row, dict)
    ]
    target = next(
        (
            row
            for row in assumptions
            if str(row.get("assumption_id") or "") == assumption_id
        ),
        None,
    )
    if target is None:
        raise ValueError(f"Assumption not found: {assumption_id}")
    now = datetime.now(UTC).isoformat()
    event_id = "assumption-action-" + _stable_evidence_token(
        [updated.get("session_id"), assumption_id, confirmed, now]
    )
    target["confirmed"] = bool(confirmed)
    target["confirmation_status"] = "confirmed" if confirmed else "rejected"
    target["confirmed_at"] = now
    target["confirmed_by"] = actor
    if note:
        target["confirmation_note"] = note
    updated["assumptions"] = assumptions
    updated.setdefault("audit_events", []).append(
        {
            "created_at": now,
            "event_type": "assumption_confirmed" if confirmed else "assumption_rejected",
            "event_id": event_id,
            "assumption_id": assumption_id,
            "actor": actor,
            "note": note,
        }
    )
    updated.setdefault("learning_candidates", []).append(
        {
            "candidate_id": event_id,
            "candidate_type": "assumption_review",
            "status": "pending",
            "created_at": now,
            "assumption_id": assumption_id,
            "assumption": target.get("assumption"),
            "confirmed": bool(confirmed),
            "note": note,
        }
    )
    updated.pop("approved_at", None)
    updated.pop("approved_by", None)
    updated["session_status"] = "estimator_review"
    updated["current_stage"] = "structured_review"
    updated["updated_at"] = now
    updated["readiness_state"] = evaluate_estimate_readiness(updated)
    return updated


def approve_estimate_session(
    state: dict[str, Any],
    *,
    actor: str = "estimator",
) -> dict[str, Any]:
    """Approve a session only after deterministic readiness validation."""

    updated = deepcopy(state)
    readiness = evaluate_estimate_readiness(updated)
    updated["readiness_state"] = readiness
    if not readiness.get("ready"):
        messages = [
            str(row.get("message") or "")
            for row in readiness.get("hard_errors") or []
            if isinstance(row, dict)
        ]
        raise ValueError(
            "Estimate is not ready for workbook approval: "
            + "; ".join(messages[:5])
        )
    now = datetime.now(UTC).isoformat()
    updated["session_status"] = "approved"
    updated["current_stage"] = "approved"
    updated["approved_at"] = now
    updated["approved_by"] = actor
    updated["updated_at"] = now
    updated.setdefault("audit_events", []).append(
        {
            "created_at": now,
            "event_type": "estimate_approved",
            "actor": actor,
            "decision_count": len(updated.get("decision_template_state") or []),
            "readiness": readiness,
        }
    )
    return updated


def latest_correction_memory_edits(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert the latest conversational patch into pending-memory edit rows."""

    history = state.get("decision_change_history") or []
    if not history or not isinstance(history[-1], dict):
        return []
    rows: list[dict[str, Any]] = []
    for change in history[-1].get("changes") or []:
        if not isinstance(change, dict):
            continue
        before = change.get("before") if isinstance(change.get("before"), dict) else {}
        after = change.get("after") if isinstance(change.get("after"), dict) else {}
        if not before or not after:
            continue
        decision_id = str(after.get("decision_id") or change.get("decision_id") or "")
        section = str(after.get("section") or "decision_template_state")
        bucket = str(after.get("template_bucket") or "")
        if before.get("include") != after.get("include"):
            rows.append(
                {
                    "section": f"{section}.{decision_id}",
                    "decision_id": decision_id,
                    "field_name": "include",
                    "package_or_labor_task": bucket or decision_id,
                    "suggested_value": before.get("include"),
                    "final_value": after.get("include"),
                    "reason": "Estimator conversational correction.",
                }
            )
        before_values = before.get("proposed_values") if isinstance(before.get("proposed_values"), dict) else {}
        after_values = after.get("proposed_values") if isinstance(after.get("proposed_values"), dict) else {}
        for field in sorted(set(before_values) | set(after_values)):
            if before_values.get(field) == after_values.get(field):
                continue
            rows.append(
                {
                    "section": f"{section}.{decision_id}",
                    "decision_id": decision_id,
                    "field_name": field,
                    "package_or_labor_task": bucket or decision_id,
                    "suggested_value": before_values.get(field),
                    "final_value": after_values.get(field),
                    "reason": "Estimator conversational correction.",
                }
            )
    return rows


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
    now = datetime.now(UTC).isoformat()
    rejected = list(updated.get("rejected_precedents") or [])
    if precedent_id and not any(str(row.get("precedent_id") or "") == precedent_id for row in rejected):
        rejected.append({"precedent_id": precedent_id, "reason": reason, "rejected_at": now})
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
    updated["updated_at"] = now
    updated.setdefault("audit_events", []).append(
        {
            "created_at": now,
            "event_type": "historical_precedent_rejected",
            "precedent_id": precedent_id,
            "reason": reason,
        }
    )
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
        "visual_evidence": [
            {
                "evidence_id": row.get("evidence_id"),
                "evidence_type": row.get("evidence_type"),
                "confidence": row.get("confidence"),
                "area_scopes": row.get("area_scopes") or [],
                "linear_scopes": row.get("linear_scopes") or [],
                "signals": row.get("signals") or [],
                "risk_flags": row.get("risk_flags") or [],
            }
            for row in state.get("uploaded_evidence") or []
            if isinstance(row, dict)
        ][:8],
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
    before_inputs = (
        base.get("include"),
        deepcopy(base.get("proposed_values")),
        base.get("editable_selector_code"),
        base.get("selected_pricing_candidate"),
    )
    for key, value in patch.items():
        if key == "proposed_values" and isinstance(value, dict):
            merged[key] = {**(merged.get(key) or {}), **value}
        elif key == "evidence":
            evidence = _normalize_decision_evidence(merged.get("evidence"))
            incoming = _normalize_decision_evidence(value)
            for source_type, rows in incoming.items():
                evidence[source_type] = _unique_values(
                    [*(evidence.get(source_type) or []), *rows]
                )
            merged[key] = evidence
        elif key in {"source_ids", "assumptions", "review_reasons"}:
            merged[key] = _unique_values([*(merged.get(key) or []), *(value if isinstance(value, list) else [value])])
        elif value is not None:
            merged[key] = deepcopy(value)
    after_inputs = (
        merged.get("include"),
        deepcopy(merged.get("proposed_values")),
        merged.get("editable_selector_code"),
        merged.get("selected_pricing_candidate"),
    )
    if before_inputs != after_inputs and patch.get("review_status") not in {"accepted", "edited"}:
        for field in ("accepted_at", "accepted_by", "acceptance_note"):
            merged.pop(field, None)
        merged["review_status"] = "proposed"
    return merged


def _traceable_decision(row: dict[str, Any]) -> dict[str, Any]:
    decision = deepcopy(row)
    evidence = _normalize_decision_evidence(decision.get("evidence"))
    decision["evidence"] = evidence
    evidence_rows = [
        item
        for rows in evidence.values()
        for item in rows
        if isinstance(item, dict)
    ]
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


def _normalize_decision_evidence(value: Any) -> dict[str, list[dict[str, Any]]]:
    if isinstance(value, dict):
        normalized: dict[str, list[dict[str, Any]]] = {}
        for source_type, rows in value.items():
            items = rows if isinstance(rows, list) else [rows]
            cleaned = [dict(item) for item in items if isinstance(item, dict)]
            if cleaned:
                normalized[str(source_type)] = cleaned
        return normalized
    if isinstance(value, list):
        cleaned = [dict(item) for item in value if isinstance(item, dict)]
        return {"legacy": cleaned} if cleaned else {}
    return {}


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


def _visual_job_facts(record: dict[str, Any]) -> list[dict[str, Any]]:
    evidence_id = str(record.get("evidence_id") or "")
    confidence = _number(record.get("confidence"))
    rows: list[dict[str, Any]] = []
    header = {
        **(record.get("customer_info") or {}),
        **(record.get("job_header") or {}),
    }
    for field, label in (
        ("job_name", "Job name"),
        ("customer", "Customer"),
        ("customer_name", "Customer"),
        ("site_address", "Site address"),
        ("declared_total_area_sqft", "Declared total area"),
    ):
        if header.get(field) in (None, "", [], {}):
            continue
        rows.append(
            {
                "field": field,
                "label": label,
                "value": header[field],
                "fact_type": "visual_extraction",
                "source_type": "annotated_scope_image",
                "source_ids": [evidence_id],
                "confidence": confidence,
            }
        )
    for index, scope in enumerate(record.get("area_scopes") or [], start=1):
        if not isinstance(scope, dict):
            continue
        scope_id = str(scope.get("scope_id") or f"area_{index}")
        rows.append(
            {
                "field": f"visual_area_scope:{scope_id}",
                "label": str(scope.get("label") or f"Area scope {index}"),
                "value": dict(scope),
                "fact_type": "visual_extraction",
                "source_type": "annotated_scope_image",
                "source_ids": [evidence_id],
                "confidence": _number(scope.get("confidence")) or confidence,
            }
        )
    for index, scope in enumerate(record.get("linear_scopes") or [], start=1):
        if not isinstance(scope, dict):
            continue
        rows.append(
            {
                "field": f"visual_linear_scope:{index}",
                "label": str(scope.get("item") or f"Linear scope {index}"),
                "value": dict(scope),
                "fact_type": "visual_extraction",
                "source_type": "annotated_scope_image",
                "source_ids": [evidence_id],
                "confidence": _number(scope.get("confidence")) or confidence,
            }
        )
    return rows


def _merge_job_facts(*fact_groups: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for group in fact_groups:
        for row in group or []:
            if not isinstance(row, dict):
                continue
            key = (
                str(row.get("field") or ""),
                json.dumps(row.get("value"), sort_keys=True, default=str),
            )
            if key in seen:
                continue
            seen.add(key)
            rows.append(dict(row))
    return rows


def _merge_uploaded_evidence(
    existing: Any,
    additions: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = [dict(row) for row in existing or [] if isinstance(row, dict)]
    positions = {
        str(row.get("evidence_id") or ""): index
        for index, row in enumerate(rows)
        if str(row.get("evidence_id") or "")
    }
    for addition in additions or []:
        if not isinstance(addition, dict):
            continue
        evidence_id = str(addition.get("evidence_id") or "")
        if evidence_id and evidence_id in positions:
            rows[positions[evidence_id]] = dict(addition)
        else:
            if evidence_id:
                positions[evidence_id] = len(rows)
            rows.append(dict(addition))
    return rows


def _stable_evidence_token(values: Iterable[Any]) -> str:
    payload = "|".join(str(value or "") for value in values)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _dedupe_dict_rows(
    rows: Iterable[dict[str, Any]],
    fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        key = tuple(str(row.get(field) or "") for field in fields)
        if not any(key):
            key = (json.dumps(row, sort_keys=True, default=str),)
        if key in seen:
            continue
        seen.add(key)
        output.append(dict(row))
    return output


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
        answer_key = (
            raw.get("reference_answer_key")
            if isinstance(raw.get("reference_answer_key"), dict)
            else {}
        )
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
                "quoted_value": _first_nonblank_value(
                    raw.get("quoted_value"),
                    raw.get("estimate_total"),
                    raw.get("proposal_value"),
                    answer_key.get("quoted_value"),
                    answer_key.get("expected_total"),
                ),
                "final_value": _first_nonblank_value(
                    raw.get("final_value"),
                    raw.get("contract_value"),
                    raw.get("actual_value"),
                    answer_key.get("final_value"),
                ),
                "material_assumptions": (
                    raw.get("material_assumptions")
                    or answer_key.get("material_assumptions")
                    or []
                ),
                "labor_assumptions": (
                    raw.get("labor_assumptions")
                    or answer_key.get("labor_assumptions")
                    or []
                ),
                "reference_answer_key": answer_key,
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
                "material_assumptions": (
                    supplied.get("material_assumptions")
                    or job.get("material_assumptions")
                    or []
                ),
                "labor_assumptions": (
                    supplied.get("labor_assumptions")
                    or job.get("labor_assumptions")
                    or []
                ),
                "exclusions": supplied.get("exclusions") or [],
                "influence_confidence": supplied.get("influence_confidence")
                or _score_to_confidence(job.get("similarity_score")),
            }
        )
    return comparisons


def _first_nonblank_value(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


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
            rows.append(_traceable_assumption(item))
    for item in raw:
        if isinstance(item, dict) and (item.get("assumption") or item.get("text")):
            normalized = _traceable_assumption(item)
            if not any(
                str(row.get("assumption_id") or "") == normalized["assumption_id"]
                for row in rows
            ):
                rows.append(normalized)
            continue
        text = str(item or "").strip()
        if text and not any(str(row.get("assumption") or "") == text for row in rows):
            rows.append(
                _traceable_assumption(
                    {
                        "assumption": text,
                        "confidence": "medium",
                        "financial_impact": "unknown",
                        "confirmed": False,
                    }
                )
            )
    return rows


def merge_estimate_assumptions(
    existing: Any,
    incoming: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge model assumptions without losing prior estimator confirmations."""

    prior = {
        str(row.get("assumption_id") or ""): dict(row)
        for row in existing or []
        if isinstance(row, dict) and row.get("assumption_id")
    }
    rows: list[dict[str, Any]] = []
    for raw in incoming:
        if not isinstance(raw, dict):
            continue
        row = _traceable_assumption(raw)
        before = prior.get(str(row.get("assumption_id") or ""), {})
        for field in (
            "confirmed",
            "confirmation_status",
            "confirmed_at",
            "confirmed_by",
            "confirmation_note",
        ):
            if field in before:
                row[field] = before[field]
        rows.append(row)
    return rows


def _traceable_assumption(raw: dict[str, Any]) -> dict[str, Any]:
    row = dict(raw)
    text = str(row.get("assumption") or row.get("text") or "").strip()
    row["assumption"] = text
    row.setdefault(
        "assumption_id",
        "assumption-" + _stable_evidence_token([text]),
    )
    row.setdefault("confirmed", False)
    row.setdefault("confirmation_status", "unconfirmed")
    return row


def _merge_rejected_precedents(existing: Any, raw_response: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [dict(row) for row in existing or [] if isinstance(row, dict)]
    supplied = raw_response.get("rejected_precedents") if isinstance(raw_response, dict) else []
    for item in supplied if isinstance(supplied, list) else []:
        normalized = item if isinstance(item, dict) else {"precedent_id": str(item)}
        precedent_id = str(normalized.get("precedent_id") or normalized.get("job_id") or "").strip()
        if precedent_id and not any(str(row.get("precedent_id") or "") == precedent_id for row in rows):
            rows.append({**normalized, "precedent_id": precedent_id})
    return rows


def _changed_scope_fields(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    fields = set(before) | set(after)
    return sorted(
        field
        for field in fields
        if before.get(field) != after.get(field)
    )


def _decision_calculation_changed(change: dict[str, Any]) -> bool:
    """Return whether a decision patch can change workbook-derived outputs."""

    before = change.get("before") if isinstance(change.get("before"), dict) else {}
    after = change.get("after") if isinstance(change.get("after"), dict) else {}
    if not before:
        return bool(after)
    if before.get("include") != after.get("include"):
        return True
    if before.get("proposed_values") != after.get("proposed_values"):
        return True
    return any(
        before.get(field) != after.get(field)
        for field in (
            "editable_selector_code",
            "selected_pricing_candidate",
            "workbook_row",
            "template_bucket",
        )
    )


def _affected_decision_ids(
    decisions: list[dict[str, Any]],
    *,
    changed_ids: set[str],
    scope_changes: list[str],
    include_all: bool,
) -> set[str]:
    ids = {
        str(row.get("decision_id") or "")
        for row in decisions
        if str(row.get("decision_id") or "")
    }
    if include_all:
        return ids
    affected = set(changed_ids)
    area_fields = {
        "estimated_sqft",
        "net_sqft",
        "basis_sqft",
        "net_insulation_area_sqft",
        "gross_insulation_area_sqft",
        "foam_thickness_inches",
        "round_trip_miles",
        "estimated_round_trip_miles",
    }
    if set(scope_changes) & area_fields:
        affected.update(ids)
        return affected
    changed_buckets = {
        _token(row.get("template_bucket"))
        for row in decisions
        if str(row.get("decision_id") or "") in changed_ids
    }
    dependent_buckets = {
        dependent
        for bucket in changed_buckets
        for dependent in DEPENDENT_BUCKETS.get(bucket, set())
    }
    affected.update(
        str(row.get("decision_id") or "")
        for row in decisions
        if _token(row.get("template_bucket")) in dependent_buckets
    )
    return {value for value in affected if value}


def _workbench_decision_rows(workbench: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for section in WORKBENCH_DECISION_SECTIONS:
        for row in workbench.get(section) or []:
            if isinstance(row, dict):
                rows.append({**row, "section": row.get("section") or section})
    return rows


def _calculation_keys(row: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for field in ("decision_id", "source_decision_id"):
        decision_id = str(row.get(field) or "").strip()
        if decision_id:
            keys.append(f"id:{decision_id}")
    section = str(row.get("section") or "").strip()
    bucket = _token(row.get("template_bucket"))
    workbook_row = str(row.get("workbook_row") or row.get("row_number") or "").strip()
    if section or bucket or workbook_row:
        keys.append(f"row:{section}:{bucket}:{workbook_row}")
    if workbook_row:
        keys.append(f"workbook_row:{workbook_row}")
    return list(dict.fromkeys(keys))


def _review_prompt_messages(state: dict[str, Any], reasons: list[str]) -> list[dict[str, Any]]:
    instructions = (
        "You are the second-review estimator for Spray-Tec. Review the supplied compact estimate state; "
        "do not rebuild the estimate and do not perform workbook arithmetic. Identify only material scope, "
        "precedent, product, pricing, compatibility, or decision errors. Preserve supported decisions. "
        "Return strict JSON with verdict (approve, needs_changes, or needs_clarification), summary, confidence, "
        "and issues. Each issue must include severity, decision_id when applicable, issue, evidence, and an "
        "optional recommended_patch using the same atomic decision-patch shape as current_decisions. "
        "Do not recommend a patch without evidence from facts, cited precedent IDs, approved memory IDs, "
        "product evidence, pricing evidence, or deterministic calculations."
    )
    compact = {
        "review_reasons": reasons,
        "job_facts": state.get("job_facts") or [],
        "scope_state": state.get("scope_state") or {},
        "assumptions": state.get("assumptions") or [],
        "unresolved_questions": state.get("unresolved_questions") or [],
        "historical_comparison": state.get("historical_comparison") or [],
        "current_decisions": state.get("decision_template_state") or [],
        "calculation_state": state.get("calculation_state") or {},
        "approved_memories_used": state.get("approved_memories_used") or [],
        "review_flags": state.get("review_flags") or [],
    }
    max_input_characters = _positive_int_environment(
        "OPENAI_REVIEW_MAX_INPUT_CHARACTERS",
        DEFAULT_REVIEW_MAX_INPUT_CHARACTERS,
    )
    fixed_messages = [
        {"role": "system", "content": instructions},
        {"role": "user", "content": "{}"},
    ]
    context_budget = max(
        0,
        max_input_characters - _json_character_count(fixed_messages) - 1_000,
    )
    bounded_compact = _bounded_prompt_context(compact, context_budget)
    messages = [
        {"role": "system", "content": instructions},
        {"role": "user", "content": json.dumps(bounded_compact, default=str)},
    ]
    if _json_character_count(messages) > max_input_characters:
        raise ValueError(
            "Review prompt exceeds OPENAI_REVIEW_MAX_INPUT_CHARACTERS "
            "after context compaction."
        )
    return messages


def _call_review_model(messages: list[dict[str, Any]], model: str) -> dict[str, Any]:
    try:
        from openai import OpenAI  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("openai package is not installed") from exc
    try:
        timeout_seconds = float(os.getenv("OPENAI_REVIEW_TIMEOUT_SECONDS", "180"))
    except (TypeError, ValueError):
        timeout_seconds = 180.0
    try:
        max_retries = int(os.getenv("OPENAI_REVIEW_MAX_RETRIES", "1"))
    except (TypeError, ValueError):
        max_retries = 1
    request: dict[str, Any] = {
        "model": model,
        "input": messages,
        "max_output_tokens": _positive_int_environment(
            "OPENAI_REVIEW_MAX_OUTPUT_TOKENS",
            DEFAULT_REVIEW_MAX_OUTPUT_TOKENS,
        ),
        "text": {"format": {"type": "json_object"}},
    }
    input_characters = _json_character_count(messages)
    max_input_characters = _positive_int_environment(
        "OPENAI_REVIEW_MAX_INPUT_CHARACTERS",
        DEFAULT_REVIEW_MAX_INPUT_CHARACTERS,
    )
    if input_characters > max_input_characters:
        raise ValueError(
            f"Review prompt blocked before API dispatch: {input_characters:,} "
            f"characters exceeds the {max_input_characters:,}-character limit."
        )
    reasoning_effort = str(
        os.getenv("OPENAI_REVIEW_REASONING_EFFORT")
        or os.getenv("OPENAI_ESTIMATOR_REASONING_EFFORT")
        or ""
    ).strip()
    if reasoning_effort:
        request["reasoning"] = {"effort": reasoning_effort}
    response = OpenAI(
        timeout=timeout_seconds,
        max_retries=max(0, max_retries),
    ).responses.create(**request)
    payload = _json_payload(getattr(response, "output_text", "") or "{}")
    payload["_model_call"] = {
        **model_call_metadata(
            role="review",
            model=model,
            usage=getattr(response, "usage", None),
            request_id=str(getattr(response, "id", "") or ""),
            response_model=str(getattr(response, "model", "") or ""),
        ),
        "input_characters": input_characters,
        "estimated_input_tokens": (input_characters + 3) // 4,
        "max_input_characters": max_input_characters,
        "max_output_tokens": request["max_output_tokens"],
        "reasoning_effort": reasoning_effort,
    }
    return payload


def _json_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Review model did not return valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Review model response must be a JSON object.")
    return parsed


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


def _token(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


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
