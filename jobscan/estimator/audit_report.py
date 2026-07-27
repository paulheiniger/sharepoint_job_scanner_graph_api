from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any


def build_estimate_audit_report(state: dict[str, Any]) -> dict[str, Any]:
    """Build a compact, source-oriented report from persistent session state."""

    decisions = [
        _decision_audit_row(row)
        for row in state.get("decision_template_state") or []
        if isinstance(row, dict)
    ]
    calls = [
        dict(row)
        for row in state.get("model_call_history") or []
        if isinstance(row, dict)
    ]
    usage_totals = _usage_totals(calls)
    evidence = [
        _evidence_audit_row(row)
        for row in state.get("uploaded_evidence") or []
        if isinstance(row, dict)
    ]
    audit_events = [
        dict(row)
        for row in state.get("audit_events") or []
        if isinstance(row, dict)
    ]
    completeness = _audit_completeness(state, decisions, calls)
    return {
        "report_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "session": {
            "session_id": state.get("session_id"),
            "status": state.get("session_status"),
            "current_stage": state.get("current_stage"),
            "created_at": state.get("created_at"),
            "updated_at": state.get("updated_at"),
            "approved_at": state.get("approved_at"),
            "approved_by": state.get("approved_by"),
            "template_type": state.get("template_type"),
            "division": state.get("division"),
            "job_name": state.get("job_name"),
            "site_address": state.get("site_address"),
            "prompt_version": state.get("prompt_version"),
        },
        "intake": {
            "raw_user_notes": state.get("raw_user_notes") or "",
            "job_facts": state.get("job_facts") or [],
            "assumptions": state.get("assumptions") or [],
            "unresolved_questions": state.get("unresolved_questions") or [],
            "uploaded_evidence": evidence,
        },
        "evidence": {
            "historical_jobs": state.get("retrieved_historical_jobs") or [],
            "historical_comparison": state.get("historical_comparison") or [],
            "approved_memories_retrieved": state.get("approved_memories_retrieved") or [],
            "approved_memories_used": state.get("approved_memories_used") or [],
            "product_knowledge": state.get("retrieved_product_knowledge") or [],
            "pricing_records": state.get("retrieved_pricing_records") or [],
            "rejected_precedents": state.get("rejected_precedents") or [],
        },
        "decisions": decisions,
        "calculations": {
            "state": state.get("calculation_state") or {},
            "dependency_trace": state.get("dependency_state") or {},
        },
        "review": state.get("review_state") or {},
        "readiness": state.get("readiness_state") or {},
        "models": {
            "configuration": state.get("model_metadata") or {},
            "routes": state.get("model_routes") or [],
            "calls": calls,
            "usage_totals": usage_totals,
        },
        "changes": state.get("decision_change_history") or [],
        "learning_candidates": state.get("learning_candidates") or [],
        "timeline": sorted(audit_events, key=lambda row: str(row.get("created_at") or "")),
        "warnings": state.get("review_flags") or [],
        "audit_completeness": completeness,
    }


def estimate_audit_report_json(state: dict[str, Any]) -> bytes:
    return json.dumps(
        build_estimate_audit_report(state),
        indent=2,
        sort_keys=True,
        default=str,
    ).encode("utf-8")


def _decision_audit_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "decision_id": row.get("decision_id"),
        "section": row.get("section"),
        "template_bucket": row.get("template_bucket"),
        "workbook_row": row.get("workbook_row"),
        "include": row.get("include"),
        "proposed_value": row.get("proposed_value") or row.get("proposed_values"),
        "calculated_outputs": row.get("calculated_outputs") or {},
        "confidence": row.get("confidence"),
        "review_required": row.get("review_required"),
        "review_status": row.get("review_status"),
        "accepted_at": row.get("accepted_at"),
        "accepted_by": row.get("accepted_by"),
        "reason": row.get("reason"),
        "source_type": row.get("source_type") or row.get("source"),
        "source_ids": row.get("source_ids") or [],
        "evidence": row.get("evidence") or [],
        "assumptions": row.get("assumptions") or [],
    }


def _evidence_audit_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key
        not in {
            "source_hashes",
        }
    }


def _usage_totals(calls: list[dict[str, Any]]) -> dict[str, int]:
    totals = {
        "call_count": len(calls),
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }
    for call in calls:
        usage = call.get("usage") if isinstance(call.get("usage"), dict) else {}
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            try:
                totals[key] += int(usage.get(key) or 0)
            except (TypeError, ValueError):
                pass
    return totals


def _audit_completeness(
    state: dict[str, Any],
    decisions: list[dict[str, Any]],
    calls: list[dict[str, Any]],
) -> dict[str, Any]:
    readiness = (
        state.get("readiness_state")
        if isinstance(state.get("readiness_state"), dict)
        else {}
    )
    decisions_without_sources = [
        row.get("decision_id")
        for row in decisions
        if row.get("include") is True
        and not (row.get("source_ids") or row.get("evidence"))
        and row.get("source_type") not in {"deterministic_calculation", "explicit_user_note"}
    ]
    checks = {
        "has_raw_notes": bool(str(state.get("raw_user_notes") or "").strip()),
        "has_job_facts": bool(state.get("job_facts")),
        "has_decisions": bool(decisions),
        "has_calculation_state": bool(state.get("calculation_state")),
        "has_model_route": bool(state.get("model_routes")),
        "has_model_call_usage": any(call.get("usage") for call in calls),
        "has_prompt_version": bool(state.get("prompt_version")),
        "approved": bool(state.get("approved_at")),
        "readiness_passed": readiness.get("ready") if readiness else None,
        "decisions_without_sources": decisions_without_sources,
    }
    checks["ready_for_final_audit"] = bool(
        checks["has_raw_notes"]
        and checks["has_job_facts"]
        and checks["has_decisions"]
        and checks["has_calculation_state"]
        and checks["has_prompt_version"]
        and not decisions_without_sources
        and checks["readiness_passed"] is not False
    )
    return checks
