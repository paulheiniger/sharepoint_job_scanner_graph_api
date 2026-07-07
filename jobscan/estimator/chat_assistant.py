from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Callable, Iterable

import pandas as pd

from .schemas import EstimatorData


DEFAULT_CHAT_ESTIMATOR_MODEL = "gpt-4o"


@dataclass
class EstimatorChatResult:
    assistant_message: str
    estimator_notes: str
    scope_overrides: dict[str, Any] = field(default_factory=dict)
    workbook_decision_preferences: list[dict[str, Any]] = field(default_factory=list)
    missing_questions: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    confidence: float = 0.0
    source: str = "deterministic_fallback"
    raw_response: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_estimator_chat_turn(
    messages: Iterable[dict[str, str]],
    *,
    data: EstimatorData | None = None,
    template_type_hint: str = "",
    existing_scope: dict[str, Any] | None = None,
    provider: Callable[[list[dict[str, Any]], str], Any] | None = None,
    model: str | None = None,
) -> EstimatorChatResult:
    message_list = _clean_messages(messages)
    if not message_list:
        return EstimatorChatResult(
            assistant_message="Paste or type project notes and I will turn them into an estimator-ready draft.",
            estimator_notes="",
            confidence=0.0,
            missing_questions=["Project notes are needed before estimating."],
        )
    context = estimator_context_summary(data)
    model_name = model or os.getenv("OPENAI_ESTIMATOR_CHAT_MODEL") or DEFAULT_CHAT_ESTIMATOR_MODEL
    prompt_messages = _chat_prompt_messages(
        message_list,
        template_type_hint=template_type_hint,
        existing_scope=existing_scope or {},
        context=context,
    )
    if provider is not None or os.getenv("OPENAI_API_KEY"):
        try:
            raw = provider(prompt_messages, model_name) if provider is not None else _call_openai_chat(prompt_messages, model_name)
            payload = _extract_json_object(raw)
            return normalize_chat_payload(payload, source="ai_chat")
        except Exception as exc:
            fallback = deterministic_chat_fallback(message_list, template_type_hint=template_type_hint)
            fallback.warnings.append(f"AI estimator chat failed; used deterministic fallback. {type(exc).__name__}: {exc}")
            return fallback
    fallback = deterministic_chat_fallback(message_list, template_type_hint=template_type_hint)
    fallback.warnings.append("OPENAI_API_KEY is not configured; used deterministic estimator-chat fallback.")
    return fallback


def estimator_context_summary(data: EstimatorData | None) -> dict[str, Any]:
    if data is None:
        return {}
    summary: dict[str, Any] = {
        "template_rows": _frame_len(data.template_rows),
        "pricing_rows": _frame_len(data.pricing),
        "product_rows": _frame_len(data.product_catalog),
        "decision_recommendation_rows": _frame_len(data.estimator_decision_recommendations),
    }
    if isinstance(data.template_rows, pd.DataFrame) and not data.template_rows.empty:
        rows = data.template_rows.copy()
        for column in ("template_type", "template_bucket"):
            if column not in rows.columns:
                rows[column] = ""
        bucket_counts = (
            rows[["template_type", "template_bucket"]]
            .fillna("")
            .astype(str)
            .groupby(["template_type", "template_bucket"], dropna=False)
            .size()
            .reset_index(name="row_count")
            .sort_values("row_count", ascending=False)
            .head(30)
        )
        summary["common_template_buckets"] = bucket_counts.to_dict(orient="records")
    if isinstance(data.pricing, pd.DataFrame) and not data.pricing.empty:
        name_columns = [column for column in ("item_name", "product_name", "name", "description") if column in data.pricing.columns]
        if name_columns:
            names: list[str] = []
            for column in name_columns:
                names.extend(str(value).strip() for value in data.pricing[column].dropna().head(20) if str(value).strip())
            summary["pricing_name_examples"] = list(dict.fromkeys(names))[:20]
    if isinstance(data.estimator_decision_recommendations, pd.DataFrame) and not data.estimator_decision_recommendations.empty:
        summary["decision_recommendation_examples"] = _context_records(
            data.estimator_decision_recommendations,
            [
                "template_type",
                "template_bucket",
                "decision_id",
                "decision_value",
                "resolved_item_name",
                "selector_code",
                "evidence_count",
                "source_jobs",
                "confidence",
                "history_table",
            ],
            limit=25,
        )
    return summary


def normalize_chat_payload(payload: dict[str, Any], *, source: str) -> EstimatorChatResult:
    scope = payload.get("scope_overrides") if isinstance(payload.get("scope_overrides"), dict) else {}
    notes = _clean_string(payload.get("estimator_notes") or payload.get("filled_estimator_notes"))
    assistant_message = _clean_string(payload.get("assistant_message") or payload.get("summary"))
    if not notes:
        notes = assistant_message
    if not assistant_message:
        assistant_message = notes or "I drafted estimator notes from the conversation."
    return EstimatorChatResult(
        assistant_message=assistant_message,
        estimator_notes=notes,
        scope_overrides=_clean_scope(scope),
        workbook_decision_preferences=_clean_decision_preferences(payload.get("workbook_decision_preferences")),
        missing_questions=_clean_list(payload.get("missing_questions")),
        assumptions=_clean_list(payload.get("assumptions")),
        confidence=_bounded_confidence(payload.get("confidence")),
        source=source,
        raw_response=payload,
        warnings=_clean_list(payload.get("warnings")),
    )


def deterministic_chat_fallback(
    messages: Iterable[dict[str, str]],
    *,
    template_type_hint: str = "",
) -> EstimatorChatResult:
    text = "\n".join(str(message.get("content") or "") for message in messages if message.get("role") != "assistant")
    scope: dict[str, Any] = {}
    assumptions: list[str] = []
    questions: list[str] = []
    template_hint = template_type_hint.lower()
    if "insulation" in template_hint or re.search(r"\bfoam|spray|insulat", text, re.I):
        scope["template_type"] = "insulation"
        scope["division"] = "Insulation"
        scope["project_type"] = "spray foam insulation"
    if re.search(r"\bopen[- ]?cell\b", text, re.I):
        scope["foam_type"] = "open_cell"
    elif re.search(r"\bclosed[- ]?cell\b", text, re.I):
        scope["foam_type"] = "closed_cell"

    length, width = _parse_footprint(text)
    wall_height = _parse_wall_height(text)
    if length and width:
        scope["building_footprint_length_ft"] = length
        scope["building_footprint_width_ft"] = width
        scope["footprint_area_sqft"] = round(length * width, 2)
    if wall_height:
        scope["wall_height_ft"] = wall_height
    openings, deduction_area = _parse_openings(text)
    if openings:
        scope["openings"] = openings
        scope["opening_area_known_sqft"] = round(deduction_area, 2)
        scope["deduction_sqft"] = round(deduction_area, 2)
    if length and width and wall_height:
        wall_area = 2 * (length + width) * wall_height
        roof_area = length * width
        net_wall = max(wall_area - deduction_area, 0)
        scope.update(
            {
                "outside_walls_included": True,
                "ceiling_included": True,
                "gross_wall_area_sqft": round(wall_area, 2),
                "ceiling_area_sqft": round(roof_area, 2),
                "gross_insulation_area_sqft": round(wall_area + roof_area, 2),
                "net_insulation_area_sqft": round(net_wall + roof_area, 2),
                "net_sqft": round(net_wall + roof_area, 2),
                "estimated_sqft": round(net_wall + roof_area, 2),
                "area_calculation_explanation": (
                    f"Walls: 2 x ({length:g} + {width:g}) x {wall_height:g} = {wall_area:,.0f} sq ft. "
                    f"Openings deducted: {deduction_area:,.0f} sq ft. "
                    f"Ceiling/roof deck: {length:g} x {width:g} = {roof_area:,.0f} sq ft. "
                    f"Total spray area: {net_wall + roof_area:,.0f} sq ft."
                ),
            }
        )
    else:
        questions.append("Confirm building length, width, wall height, and whether roof deck/ceiling is included.")

    if not scope.get("foam_type"):
        questions.append("Confirm open-cell vs closed-cell foam.")
    thickness = _parse_thickness(text)
    if thickness:
        scope["foam_thickness_inches"] = thickness
    else:
        questions.append("Confirm target R-value or foam thickness.")
    timing = _parse_timing(text)
    if timing:
        scope["requested_timing"] = timing
    assumptions.append("Deterministic fallback extracted obvious dimensions only; estimator should verify AI draft before quoting.")
    notes = _fallback_estimator_notes(text, scope)
    assistant = _fallback_assistant_message(scope, questions)
    return EstimatorChatResult(
        assistant_message=assistant,
        estimator_notes=notes,
        scope_overrides=scope,
        missing_questions=questions,
        assumptions=assumptions,
        confidence=0.62 if scope.get("estimated_sqft") else 0.35,
        source="deterministic_fallback",
    )


def _chat_prompt_messages(
    messages: list[dict[str, str]],
    *,
    template_type_hint: str,
    existing_scope: dict[str, Any],
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    today = date.today().isoformat()
    instructions = (
        "You are a senior Spray-Tec estimator working inside an estimating assistant. "
        "Use the conversation, historical/template context, and product/pricing context to produce an estimator-ready draft. "
        "Think like an estimator: extract takeoff, infer likely template decisions, explain assumptions, and ask only material missing questions. "
        "When historical/template context supports a normal choice, make the best reviewed guess instead of leaving the decision blank; "
        "set review_required true, lower confidence, and explain the evidence if the prompt did not explicitly confirm it. "
        "Ask questions only when the answer materially changes scope, safety/code compliance, system selection, warranty eligibility, or price. "
        "Return strict JSON only with keys: assistant_message, estimator_notes, scope_overrides, workbook_decision_preferences, "
        "missing_questions, assumptions, warnings, confidence. "
        "scope_overrides should use workbook-facing field names such as template_type, division, project_type, foam_type, "
        "foam_thickness_inches, building_footprint_length_ft, building_footprint_width_ft, wall_height_ft, openings, "
        "outside_walls_included, ceiling_included, net_insulation_area_sqft, estimated_sqft, insulation_surface_areas, "
        "insulation_r_value_targets, coating_type, warranty_target_years, substrate, roof_condition, access_complexity, "
        "scope_triggers, and reference_job_ids. "
        "workbook_decision_preferences should be a list of decisions with decision_id, template_bucket, include, proposed_values, "
        "evidence, confidence, and review_required. "
        "You may do takeoff math from explicit dimensions and deductions. Do not invent hidden warranty years, exact proprietary products, "
        "or final quote totals when evidence is weak. Use review_required for assumptions. "
        "Workbook formulas remain authoritative for final costs."
    )
    user_payload = {
        "today": today,
        "template_type_hint": template_type_hint,
        "existing_scope": existing_scope,
        "estimator_context": context,
        "conversation": messages,
    }
    return [
        {"role": "system", "content": instructions},
        {"role": "user", "content": json.dumps(user_payload, indent=2, default=str)},
    ]


def _call_openai_chat(messages: list[dict[str, Any]], model: str) -> str:
    try:
        from openai import OpenAI  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("openai package is not installed") from exc
    try:
        timeout_seconds = float(os.getenv("OPENAI_ESTIMATOR_CHAT_TIMEOUT_SECONDS", "60"))
    except (TypeError, ValueError):
        timeout_seconds = 60.0
    client = OpenAI(timeout=timeout_seconds)
    response = client.chat.completions.create(
        model=model,
        temperature=0.2,
        response_format={"type": "json_object"},
        messages=messages,
    )
    return response.choices[0].message.content or "{}"


def _clean_messages(messages: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    cleaned: list[dict[str, str]] = []
    for message in messages or []:
        role = str(message.get("role") or "").strip().lower()
        content = _clean_string(message.get("content"))
        if role not in {"user", "assistant", "system"} or not content:
            continue
        cleaned.append({"role": role, "content": content})
    return cleaned


def _extract_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    text = str(value or "{}").strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return {}
        payload = json.loads(match.group(0))
    return payload if isinstance(payload, dict) else {}


def _clean_scope(scope: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in (scope or {}).items():
        if value in (None, "", [], {}):
            continue
        cleaned[str(key)] = value
    return cleaned


def _clean_decision_preferences(value: Any) -> list[dict[str, Any]]:
    rows = value if isinstance(value, list) else []
    return [row for row in rows if isinstance(row, dict)]


def _clean_list(value: Any) -> list[str]:
    if value is None:
        return []
    rows = value if isinstance(value, list) else [value]
    return [_clean_string(row) for row in rows if _clean_string(row)]


def _clean_string(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _bounded_confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    if number > 1:
        number = number / 100.0
    return round(max(0.0, min(number, 0.95)), 2)


def _frame_len(frame: Any) -> int:
    return int(len(frame)) if isinstance(frame, pd.DataFrame) else 0


def _context_records(frame: pd.DataFrame, preferred_columns: list[str], *, limit: int) -> list[dict[str, Any]]:
    columns = [column for column in preferred_columns if column in frame.columns]
    if not columns:
        columns = list(frame.columns[:8])
    if not columns:
        return []
    rows = frame[columns].head(limit).fillna("").to_dict(orient="records")
    return [{str(key): value for key, value in row.items() if value not in ("", None, [], {})} for row in rows]


def _parse_footprint(text: str) -> tuple[float | None, float | None]:
    match = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:'|ft|feet)?\s*[xX]\s*(\d+(?:\.\d+)?)\s*(?:'|ft|feet)?", text)
    if not match:
        return None, None
    return float(match.group(1)), float(match.group(2))


def _parse_wall_height(text: str) -> float | None:
    match = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:'|ft|foot|feet)\s+walls?\b", text, re.I)
    if not match:
        match = re.search(r"\bwalls?\s*(?:are|at|of|=)?\s*(\d+(?:\.\d+)?)\s*(?:'|ft|foot|feet)\b", text, re.I)
    return float(match.group(1)) if match else None


def _parse_thickness(text: str) -> float | None:
    match = re.search(r"\b(?:foam|spray foam|thickness|target)\b[^.;,\n]{0,30}?(\d+(?:\.\d+)?)\s*(?:\"|in|inch|inches)\b", text, re.I)
    if not match:
        match = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:\"|in|inch|inches)\s+(?:open|closed|spray|foam)", text, re.I)
    return float(match.group(1)) if match else None


def _parse_openings(text: str) -> tuple[list[dict[str, Any]], float]:
    openings: list[dict[str, Any]] = []
    total = 0.0
    quantity_pattern = r"(?:\((\d+)\)|\b(\d+)\b|\b(one|two|three|four|five|six|seven|eight|nine|ten)\b)"
    for qty_a, qty_b, qty_word, label, width, height in re.findall(
        rf"{quantity_pattern}\s+[^.,;\n]{{0,20}}?(rollup|roll-up|overhead)[^.,;\n]{{0,40}}?(\d+(?:\.\d+)?)\s*(?:ft|')?\s*[xX]\s*(\d+(?:\.\d+)?)",
        text,
        re.I,
    ):
        qty = _quantity_value(qty_a, qty_b, qty_word)
        w = float(width)
        h = float(height)
        area = qty * w * h
        total += area
        openings.append({"opening_type": label.lower(), "quantity": qty, "width_ft": w, "height_ft": h, "total_area_sqft": area})
    for qty_a, qty_b, qty_word, size_ft, label in re.findall(
        rf"{quantity_pattern}\s+(\d+(?:\.\d+)?)\s*(?:ft|')\s+(rollup|roll-up|overhead)\s+doors?",
        text,
        re.I,
    ):
        qty = _quantity_value(qty_a, qty_b, qty_word)
        size = float(size_ft)
        area = qty * size * size
        total += area
        openings.append(
            {
                "opening_type": label.lower(),
                "quantity": qty,
                "width_ft": size,
                "height_ft": size,
                "total_area_sqft": area,
                "assumption_used": ["Single roll-up door dimension assumed square."],
            }
        )
    for qty_a, qty_b, qty_word, width_in, height_in in re.findall(
        rf"{quantity_pattern}\s+(\d+(?:\.\d+)?)\s*(?:\"|in|inch|inches)\s*[xX]\s*(\d+(?:\.\d+)?)\s*(?:\"|in|inch|inches)\s+windows?",
        text,
        re.I,
    ):
        qty = _quantity_value(qty_a, qty_b, qty_word)
        w = float(width_in) / 12
        h = float(height_in) / 12
        area = qty * w * h
        total += area
        openings.append({"opening_type": "window", "quantity": qty, "width_ft": w, "height_ft": h, "total_area_sqft": area})
    walk_match = re.search(rf"{quantity_pattern}\s+36\s*(?:\"|in|inch|inches)\s+walk[- ]?in doors?", text, re.I)
    if walk_match:
        qty = _quantity_value(walk_match.group(1), walk_match.group(2), walk_match.group(3))
        area = qty * 3 * 7
        total += area
        openings.append(
            {
                "opening_type": "walk_door",
                "quantity": qty,
                "width_ft": 3,
                "height_ft": 7,
                "total_area_sqft": area,
                "assumption_used": ["36 inch walk door assumed 3 ft x 7 ft"],
            }
        )
    return openings, total


def _quantity_value(*values: Any) -> float:
    words = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
    }
    for value in values:
        text = str(value or "").strip().lower()
        if not text:
            continue
        if text in words:
            return float(words[text])
        try:
            return float(text)
        except ValueError:
            continue
    return 1.0


def _parse_timing(text: str) -> str:
    match = re.search(r"\b(?:september|october|november|december|january|february|march|april|may|june|july|august)(?:\s*(?:or|-|to)\s*(?:september|october|november|december|january|february|march|april|may|june|july|august))?(?:\s+\d{4})?", text, re.I)
    return _clean_string(match.group(0)) if match else ""


def _fallback_estimator_notes(raw_text: str, scope: dict[str, Any]) -> str:
    parts = [_clean_string(raw_text)]
    if scope.get("area_calculation_explanation"):
        parts.append(f"Takeoff: {scope['area_calculation_explanation']}")
    if scope.get("foam_type"):
        parts.append(f"Foam type indicated: {str(scope['foam_type']).replace('_', ' ')}.")
    if scope.get("foam_thickness_inches"):
        parts.append(f"Target thickness indicated: {scope['foam_thickness_inches']} inches.")
    if scope.get("requested_timing"):
        parts.append(f"Requested timing: {scope['requested_timing']}.")
    return "\n\n".join(part for part in parts if part)


def _fallback_assistant_message(scope: dict[str, Any], questions: list[str]) -> str:
    area = scope.get("estimated_sqft")
    if area:
        message = f"I drafted a workbook-ready insulation scope with about {area:,.0f} sq ft of spray area."
    else:
        message = "I drafted preliminary estimator notes, but key dimensions are still missing."
    if questions:
        message += " Remaining questions: " + "; ".join(questions)
    return message
