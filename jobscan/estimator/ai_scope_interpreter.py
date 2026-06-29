from __future__ import annotations

import json
import os
import re
from typing import Any, Callable

from .rules import first_nonblank, to_float


AI_SCOPE_FIELDS = (
    "project_type",
    "division",
    "building_type",
    "substrate",
    "gross_area_sqft",
    "deduction_area_sqft",
    "estimated_sqft",
    "coating_type",
    "warranty_target_years",
    "roof_condition",
    "roof_condition_raw_phrase",
    "roof_condition_reason",
    "condition_detail_flags",
    "penetrations_complexity",
    "penetrations_complexity_reason",
    "access_complexity",
    "access_complexity_reason",
    "scope_packages",
    "missing_info",
    "review_flags",
    "confidence_by_field",
)

PACKAGE_DECISION_VALUES = {True, False, "review", "light", "heavy"}
TRUE_ENV_VALUES = {"1", "true", "yes", "y", "on"}


def ai_scope_interpreter_enabled(env: dict[str, str] | None = None) -> bool:
    source = env if env is not None else os.environ
    return str(source.get("ENABLE_AI_SCOPE_INTERPRETER") or "false").strip().lower() in TRUE_ENV_VALUES


def _empty_scope() -> dict[str, Any]:
    return {
        "project_type": "",
        "division": "",
        "building_type": "",
        "substrate": "",
        "gross_area_sqft": None,
        "deduction_area_sqft": None,
        "estimated_sqft": None,
        "coating_type": "",
        "warranty_target_years": None,
        "roof_condition": "",
        "roof_condition_raw_phrase": "",
        "roof_condition_reason": "",
        "condition_detail_flags": [],
        "penetrations_complexity": "",
        "penetrations_complexity_reason": "",
        "access_complexity": "",
        "access_complexity_reason": "",
        "scope_packages": {},
        "missing_info": [],
        "review_flags": [],
        "confidence_by_field": {},
    }


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, (tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _as_number(value: Any) -> float | None:
    number = to_float(value)
    return number if number is not None and number > 0 else None


def _as_int(value: Any) -> int | None:
    number = _as_number(value)
    return int(number) if number is not None else None


def _normalize_complexity(value: Any, *, field: str) -> str:
    text = first_nonblank(value).strip().lower().replace("_", " ").replace("-", " ")
    aliases = {
        "easy": "low",
        "simple": "low",
        "few": "low",
        "low": "low",
        "medium": "medium",
        "moderate": "medium",
        "average": "medium",
        "high": "high",
        "hard": "high",
        "difficult": "high",
        "many": "high",
    }
    if field == "roof_condition":
        if text in {"fair with rusted fasteners", "fair/rusted", "fair rusted"}:
            return "fair_with_rusted_fasteners"
        return text
    return aliases.get(text, text)


def _normalize_package_value(value: Any) -> bool | str | None:
    if isinstance(value, bool):
        return value
    text = first_nonblank(value).strip().lower()
    if text in {"true", "yes", "y", "include", "included", "applies"}:
        return True
    if text in {"false", "no", "n", "exclude", "excluded", "none"}:
        return False
    if text in {"review", "light", "heavy"}:
        return text
    return None


def validate_ai_scope(payload: Any) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(payload, dict):
        raise ValueError("AI scope response must be a JSON object")
    cleaned = _empty_scope()
    warnings: list[str] = []
    for field in AI_SCOPE_FIELDS:
        if field not in payload:
            continue
        value = payload.get(field)
        if field in {"gross_area_sqft", "deduction_area_sqft", "estimated_sqft"}:
            cleaned[field] = _as_number(value)
        elif field == "warranty_target_years":
            cleaned[field] = _as_int(value)
        elif field in {"condition_detail_flags", "missing_info", "review_flags"}:
            cleaned[field] = _as_list(value)
        elif field == "confidence_by_field":
            confidence: dict[str, float] = {}
            if isinstance(value, dict):
                for key, raw_score in value.items():
                    score = to_float(raw_score)
                    if score is not None:
                        confidence[str(key)] = max(0.0, min(1.0, float(score)))
            cleaned[field] = confidence
        elif field == "scope_packages":
            packages: dict[str, bool | str] = {}
            if isinstance(value, dict):
                for package, raw_decision in value.items():
                    decision = _normalize_package_value(raw_decision)
                    if decision in PACKAGE_DECISION_VALUES:
                        packages[str(package)] = decision
                    else:
                        warnings.append(f"Ignored invalid AI package decision for {package}.")
            cleaned[field] = packages
        elif field in {"penetrations_complexity", "access_complexity"}:
            cleaned[field] = _normalize_complexity(value, field=field)
        elif field == "roof_condition":
            cleaned[field] = _normalize_complexity(value, field=field)
        else:
            cleaned[field] = first_nonblank(value)
    cleaned["review_flags"].extend(warnings)
    return cleaned, warnings


def _prompt(notes: str, deterministic_scope: dict[str, Any] | None = None) -> list[dict[str, str]]:
    deterministic_json = json.dumps(deterministic_scope or {}, default=str, sort_keys=True)
    return [
        {
            "role": "system",
            "content": (
                "You interpret Spray-Tec estimator field notes into structured scope JSON. "
                "Return JSON only. Do not estimate prices, unit costs, final cost, or totals. "
                "Do not invent missing facts; use missing_info and review_flags for uncertainty."
            ),
        },
        {
            "role": "user",
            "content": (
                "Return exactly these fields: "
                + ", ".join(AI_SCOPE_FIELDS)
                + ". scope_packages values must be true, false, review, light, or heavy. "
                "Use the deterministic scope only as context; explicit note phrases win.\n\n"
                f"Deterministic scope:\n{deterministic_json}\n\nField notes:\n{notes}"
            ),
        },
    ]


def _call_openai_scope_interpreter(notes: str, deterministic_scope: dict[str, Any] | None = None) -> str:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set")
    try:
        from openai import OpenAI  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on optional dependency
        raise RuntimeError("openai package is not installed") from exc
    model = os.getenv("OPENAI_SCOPE_INTERPRETER_MODEL") or "gpt-4o-mini"
    client = OpenAI()
    response = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=_prompt(notes, deterministic_scope),
    )
    return response.choices[0].message.content or "{}"


def interpret_field_notes_with_ai(
    notes: str,
    deterministic_scope: dict | None = None,
    *,
    provider: Callable[[str, dict[str, Any] | None], str | dict[str, Any]] | None = None,
) -> dict:
    fallback = _empty_scope()
    if not ai_scope_interpreter_enabled() and provider is None:
        return fallback
    try:
        raw = provider(notes, deterministic_scope) if provider is not None else _call_openai_scope_interpreter(notes, deterministic_scope)
        payload = json.loads(raw) if isinstance(raw, str) else raw
        cleaned, warnings = validate_ai_scope(payload)
        cleaned["review_flags"].extend(warnings)
        return cleaned
    except Exception as exc:
        cleaned = _empty_scope()
        cleaned["review_flags"] = [f"AI scope interpreter unavailable or invalid; deterministic parser used. ({type(exc).__name__})"]
        return cleaned


def _dimension_math_is_high_confidence(deterministic_scope: dict[str, Any]) -> bool:
    summary = deterministic_scope.get("dimension_summary") or {}
    if not isinstance(summary, dict):
        return False
    net = _as_number(summary.get("net_area_sqft"))
    warnings = summary.get("warnings") or []
    return bool(net and not warnings and (summary.get("included_areas") or summary.get("deducted_areas")))


def _note_has_few_penetrations(notes: str) -> bool:
    return bool(re.search(r"\bfew\s+penetrations?\b", notes, re.I))


def _note_has_easy_access(notes: str) -> bool:
    return bool(re.search(r"\b(?:easy|low)\s+access\b|\baccess\s+(?:is|looks|seems)?\s*(?:easy|low)\b", notes, re.I))


def _note_has_fair_overall(notes: str) -> bool:
    return bool(re.search(r"\bfair\s+(?:overall|condition)\b", notes, re.I))


def _note_allows_poor_condition(notes: str) -> bool:
    return bool(re.search(r"\b(?:poor|severe|widespread|heavy)\b", notes, re.I))


def _condition_flags_from_notes(notes: str) -> list[str]:
    text = notes.lower()
    flags: list[str] = []
    if "rusted fastener" in text or "rusted fasteners" in text:
        flags.append("rusted_fasteners")
    elif "rust" in text or "rusted" in text:
        flags.append("rust")
    if "open seam" in text or "open seams" in text or "seams opening" in text:
        flags.append("open_seams")
    if "ponding" in text:
        flags.append("ponding")
    return flags


def merge_ai_scope_with_deterministic(
    notes: str,
    deterministic_scope: dict[str, Any],
    ai_scope: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    ai_scope = ai_scope or _empty_scope()
    final_scope = dict(deterministic_scope)
    merge_decisions: list[dict[str, Any]] = []
    ai_review_flags = list(ai_scope.get("review_flags") or [])

    def set_field(field: str, value: Any, reason: str) -> None:
        old = final_scope.get(field)
        if value in (None, "", [], {}):
            return
        if old == value:
            return
        final_scope[field] = value
        merge_decisions.append({"field": field, "from": old, "to": value, "decision": "accepted", "reason": reason})

    # Deterministic dimension math is source of truth when dimensions were parsed confidently.
    if _dimension_math_is_high_confidence(deterministic_scope):
        merge_decisions.append(
            {
                "field": "estimated_sqft",
                "from": ai_scope.get("estimated_sqft"),
                "to": final_scope.get("estimated_sqft"),
                "decision": "rejected",
                "reason": "Deterministic dimension math has high confidence.",
            }
        )
    elif _as_number(ai_scope.get("estimated_sqft")):
        set_field("estimated_sqft", ai_scope.get("estimated_sqft"), "AI filled missing or low-confidence sqft.")
        set_field("surface_area_sqft", ai_scope.get("estimated_sqft"), "AI filled missing or low-confidence sqft.")

    for field in ("project_type", "division", "building_type", "substrate", "coating_type"):
        if ai_scope.get(field):
            set_field(field, ai_scope[field], f"AI interpreted {field}.")

    if ai_scope.get("warranty_target_years"):
        set_field("warranty_target_years", ai_scope["warranty_target_years"], "AI interpreted warranty target.")
        set_field("warranty_target", ai_scope["warranty_target_years"], "AI interpreted warranty target.")

    for field in ("gross_area_sqft", "deduction_area_sqft"):
        if not final_scope.get(field) and ai_scope.get(field):
            set_field(field, ai_scope[field], f"AI filled {field}.")

    ai_condition = first_nonblank(ai_scope.get("roof_condition"))
    if ai_condition:
        if ai_condition.startswith("poor") and _note_has_fair_overall(notes) and not _note_allows_poor_condition(notes):
            merge_decisions.append(
                {
                    "field": "roof_condition",
                    "from": ai_condition,
                    "to": final_scope.get("roof_condition"),
                    "decision": "rejected",
                    "reason": "Note says fair overall; AI cannot classify poor without explicit severe/poor language.",
                }
            )
        else:
            set_field("roof_condition", ai_condition, "AI interpreted roof condition from explicit note phrase.")
    elif _note_has_fair_overall(notes) and "rust" in notes.lower():
        set_field("roof_condition", "fair_with_rusted_fasteners", "Explicit note says fair overall with rusted fasteners.")

    if _note_has_fair_overall(notes) and "rust" in notes.lower() and str(final_scope.get("roof_condition") or "").startswith("poor"):
        old = final_scope.get("roof_condition")
        final_scope["roof_condition"] = "fair_with_rusted_fasteners"
        merge_decisions.append(
            {
                "field": "roof_condition",
                "from": old,
                "to": "fair_with_rusted_fasteners",
                "decision": "guardrail_override",
                "reason": "Explicit 'fair overall' phrase prevents rusted fasteners from making the whole roof poor.",
            }
        )

    ai_penetrations = first_nonblank(ai_scope.get("penetrations_complexity"))
    if _note_has_few_penetrations(notes):
        old = final_scope.get("penetrations_complexity")
        final_scope["penetrations_complexity"] = "low"
        merge_decisions.append(
            {
                "field": "penetrations_complexity",
                "from": ai_penetrations or old,
                "to": "low",
                "decision": "guardrail_override",
                "reason": "Explicit note phrase says few penetrations.",
            }
        )
    elif ai_penetrations:
        set_field("penetrations_complexity", ai_penetrations, "AI interpreted penetrations complexity.")

    ai_access = first_nonblank(ai_scope.get("access_complexity"))
    if _note_has_easy_access(notes):
        old = final_scope.get("access_complexity")
        final_scope["access_complexity"] = "low"
        merge_decisions.append(
            {
                "field": "access_complexity",
                "from": ai_access or old,
                "to": "low",
                "decision": "guardrail_override",
                "reason": "Explicit note phrase says easy access.",
            }
        )
    elif ai_access:
        set_field("access_complexity", ai_access, "AI interpreted access complexity.")

    flags = sorted(set(_condition_flags_from_notes(notes) + _as_list(ai_scope.get("condition_detail_flags"))))
    if flags:
        final_scope["condition_detail_flags"] = flags
        merge_decisions.append({"field": "condition_detail_flags", "to": flags, "decision": "accepted", "reason": "Merged note and AI condition detail flags."})

    for field in ("roof_condition_raw_phrase", "roof_condition_reason", "penetrations_complexity_reason", "access_complexity_reason"):
        if ai_scope.get(field):
            final_scope[field] = ai_scope[field]

    if isinstance(ai_scope.get("scope_packages"), dict):
        final_scope["ai_scope_packages"] = ai_scope["scope_packages"]

    return final_scope, merge_decisions, ai_review_flags
