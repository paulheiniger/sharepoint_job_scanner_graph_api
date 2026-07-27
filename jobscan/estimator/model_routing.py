from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any


DEFAULT_ESTIMATOR_MODEL = "gpt-5.5"

MODEL_ENVIRONMENT_VARIABLES = {
    "extraction": ("OPENAI_EXTRACTION_MODEL", "OPENAI_MODEL"),
    "estimator": (
        "OPENAI_ESTIMATOR_MODEL",
        "OPENAI_ESTIMATOR_CHAT_MODEL",
        "OPENAI_MODEL",
    ),
    "review": ("OPENAI_REVIEW_MODEL",),
}


def configured_estimator_models() -> dict[str, str]:
    """Resolve role-specific model configuration and estimator defaults."""

    configured = {
        f"{role}_model": _first_environment_value(names)
        for role, names in MODEL_ENVIRONMENT_VARIABLES.items()
    }
    configured["estimator_model"] = (
        configured["estimator_model"] or DEFAULT_ESTIMATOR_MODEL
    )
    return configured


def route_estimator_model(
    role: str,
    *,
    state: dict[str, Any] | None = None,
    explicit_model: str | None = None,
    user_requested: bool = False,
    trigger_reasons: list[str] | None = None,
) -> dict[str, Any]:
    normalized_role = str(role or "").strip().lower()
    if normalized_role not in MODEL_ENVIRONMENT_VARIABLES:
        raise ValueError(f"Unsupported estimator model role: {role}")
    models = configured_estimator_models()
    configured = str(models.get(f"{normalized_role}_model") or "").strip()
    selected = str(explicit_model or configured).strip()
    reasons = list(trigger_reasons or [])
    if explicit_model:
        source = "explicit_override"
    elif _first_environment_value(MODEL_ENVIRONMENT_VARIABLES[normalized_role]):
        source = "environment_configuration"
    else:
        source = "default_configuration"
    if normalized_role == "review":
        if user_requested:
            reasons.append("Estimator requested an independent review.")
        if not reasons:
            reasons.extend(_review_routing_reasons(state or {}))
    else:
        reasons.append(f"Use the configured {normalized_role} model for this task.")
    return {
        "role": normalized_role,
        "model": selected,
        "configured": bool(selected),
        "selection_source": source,
        "reasons": _unique_strings(reasons),
        "routed_at": datetime.now(UTC).isoformat(),
    }


def model_call_metadata(
    *,
    role: str,
    model: str,
    usage: Any = None,
    request_id: str = "",
    response_model: str = "",
) -> dict[str, Any]:
    return {
        "role": str(role or ""),
        "requested_model": str(model or ""),
        "response_model": str(response_model or model or ""),
        "request_id": str(request_id or ""),
        "usage": normalize_model_usage(usage),
        "completed_at": datetime.now(UTC).isoformat(),
    }


def normalize_model_usage(usage: Any) -> dict[str, int]:
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        try:
            usage = usage.model_dump()
        except Exception:
            usage = {}
    if not isinstance(usage, dict):
        return {}
    aliases = {
        "input_tokens": ("input_tokens", "prompt_tokens"),
        "output_tokens": ("output_tokens", "completion_tokens"),
        "total_tokens": ("total_tokens",),
    }
    normalized: dict[str, int] = {}
    for output_key, candidates in aliases.items():
        for candidate in candidates:
            value = usage.get(candidate)
            if value in (None, ""):
                continue
            try:
                normalized[output_key] = int(value)
            except (TypeError, ValueError):
                pass
            break
    if "total_tokens" not in normalized and (
        "input_tokens" in normalized or "output_tokens" in normalized
    ):
        normalized["total_tokens"] = (
            normalized.get("input_tokens", 0) + normalized.get("output_tokens", 0)
        )
    return normalized


def _review_routing_reasons(state: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    confidence = _number((state.get("confidence_summary") or {}).get("overall"))
    if confidence is not None and confidence < 0.65:
        reasons.append("Estimate confidence is below the review threshold.")
    if len(state.get("review_flags") or []) >= 2:
        reasons.append("Multiple review flags remain unresolved.")
    if len(state.get("unresolved_questions") or []) >= 2:
        reasons.append("Multiple material questions remain unresolved.")
    if _number((state.get("calculation_state") or {}).get("totals", {}).get("draft_total")) not in (
        None,
        0,
    ):
        draft_total = _number(
            (state.get("calculation_state") or {}).get("totals", {}).get("draft_total")
        )
        if draft_total is not None and draft_total >= 100000:
            reasons.append("Draft value exceeds the high-value review threshold.")
    return reasons


def _first_environment_value(names: tuple[str, ...]) -> str:
    for name in names:
        value = str(os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _unique_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
