from __future__ import annotations

import math
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

from .chat_assistant import estimator_context_summary
from .data_loader import ESTIMATOR_LOAD_PROFILE_CHAT, load_estimator_data
from .schemas import EstimatorData


COPILOT_CONTEXT_KEYS = (
    "template_type",
    "route_mileage",
    "decision_menu",
    "formula_requirements",
    "foam_yield_history_digest",
    "pricing_candidates_by_bucket",
    "template_fallback_defaults",
    "estimator_memory_guidance",
    "historical_evidence_packet",
    "product_guidance_digest",
)


def build_copilot_estimator_context(
    *,
    scope: dict[str, Any],
    raw_notes: str = "",
    template_type_hint: str = "",
    reference_job_ids: list[str] | None = None,
    data: EstimatorData | None = None,
    database_url: str | None = None,
    base_dir: Path | str | None = None,
    include_source_metadata: bool = False,
) -> dict[str, Any]:
    """Build the bounded, model-neutral context supplied to a Copilot agent.

    This function performs retrieval and context assembly only. It deliberately
    does not call an LLM. Copilot remains responsible for interpreting the
    current job and proposing decisions.
    """

    normalized_scope = dict(scope or {})
    if template_type_hint and not normalized_scope.get("template_type"):
        normalized_scope["template_type"] = template_type_hint
    if raw_notes and not normalized_scope.get("raw_input_notes"):
        normalized_scope["raw_input_notes"] = raw_notes
    requested_references = _unique_strings(reference_job_ids or [])
    if requested_references:
        normalized_scope["reference_job_ids"] = requested_references

    estimator_data = data or load_estimator_data(
        base_dir=base_dir,
        database_url=database_url,
        prefer_database=bool(database_url),
        load_profile=ESTIMATOR_LOAD_PROFILE_CHAT,
    )
    full_context = estimator_context_summary(estimator_data, scope=normalized_scope)
    context = {
        key: full_context.get(key)
        for key in COPILOT_CONTEXT_KEYS
        if full_context.get(key) not in (None, "", [], {})
    }
    response: dict[str, Any] = {
        "schema_version": "spraytec.copilot_estimator_context.v1",
        "scope": normalized_scope,
        "context": context,
        "warnings": _unique_strings(estimator_data.warnings),
        "retrieval_summary": _retrieval_summary(context),
    }
    if include_source_metadata:
        response["source_metadata"] = {
            "sources": _unique_strings(estimator_data.source_files_used),
            "row_counts": {
                "jobs": len(estimator_data.jobs),
                "estimates": len(estimator_data.estimates),
                "template_examples": len(estimator_data.template_examples),
                "pricing_catalog": len(estimator_data.pricing_catalog),
                "product_catalog": len(estimator_data.product_catalog),
                "approved_memories": len(estimator_data.estimator_memory),
            },
        }
    return _json_safe(response)


def _retrieval_summary(context: dict[str, Any]) -> dict[str, Any]:
    packet = (
        context.get("historical_evidence_packet")
        if isinstance(context.get("historical_evidence_packet"), dict)
        else {}
    )
    return {
        "matched_comparable_count": len(packet.get("matched_comparables") or []),
        "decision_evidence_count": len(packet.get("decision_evidence") or []),
        "matched_scope_pattern": bool(packet.get("matched_scope_pattern")),
        "validated_relationship_count": len(packet.get("validated_relationships") or []),
        "approved_memory_count": len(context.get("estimator_memory_guidance") or []),
        "pricing_bucket_count": len(context.get("pricing_candidates_by_bucket") or []),
        "product_guidance_count": len(context.get("product_guidance_digest") or []),
        "formula_requirement_count": len(context.get("formula_requirements") or []),
    }


def _unique_strings(values: list[Any]) -> list[str]:
    return list(
        dict.fromkeys(
            str(value).strip()
            for value in values
            if str(value or "").strip()
        )
    )


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (date, datetime, Path)):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    scalar = getattr(value, "item", None)
    if callable(scalar):
        try:
            return _json_safe(scalar())
        except (TypeError, ValueError):
            pass
    return str(value)
