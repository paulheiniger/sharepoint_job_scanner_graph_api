from __future__ import annotations

import json
import copy
import hashlib
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd

from .estimator_memory import relevant_memory_rows
from .foam_yield_history import build_foam_yield_history_digest
from .job_context_profiles import build_job_context_digest
from .reference_answer_key import (
    answer_key_to_workbook_decision_preferences,
    parse_reference_answer_key_text,
)
from .template_examples import build_similar_answer_key_digest, build_template_example_digest
from .schemas import EstimatorData


DEFAULT_CHAT_ESTIMATOR_MODEL = "gpt-4o"
INSULATION_CHAT_TEMPLATE_DEFAULTS = {
    "foam_yield_or_coverage": 2600.0,
    "foam_unit_price": 2.25,
    "loading_hours_per_day": 0.5,
    "traveling_hours_per_day": 2.5,
    "loading_people_count": 1.0,
    "traveling_people_count": 4.0,
    "loading_hourly_rate": 25.5,
    "traveling_hourly_rate": 13.0,
    "generator_daily_rate": 40.0,
}
DETERMINISTIC_DIMENSION_FIELDS = {
    "building_footprint_length_ft",
    "building_footprint_width_ft",
    "footprint_area_sqft",
    "wall_height_ft",
    "openings",
    "opening_area_known_sqft",
    "deduction_sqft",
    "outside_walls_included",
    "ceiling_included",
    "gross_wall_area_sqft",
    "ceiling_area_sqft",
    "gross_insulation_area_sqft",
    "net_insulation_area_sqft",
    "net_sqft",
    "estimated_sqft",
    "area_calculation_explanation",
}
REFERENCE_TEMPLATE_SOURCE = "reference_template_summary"
ANSWER_KEY_MODE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("teach", r"\b(?:learn|remember|save|store)\b.{0,120}\b(?:answer\s*key|correct\s+template|correct\s+estimate|reviewed\s+template|reference\s+template)\b"),
    ("teach", r"\b(?:answer\s*key|correct\s+template|correct\s+estimate|reviewed\s+template|reference\s+template)\b.{0,120}\b(?:learn|remember|save|store)\b"),
    ("apply", r"\b(?:apply|use|fill|update|patch)\b.{0,120}\b(?:answer\s*key|correct\s+template|correct\s+estimate|reviewed\s+template|reference\s+template)\b"),
    ("apply", r"\b(?:answer\s*key|correct\s+template|correct\s+estimate|reviewed\s+template|reference\s+template)\b.{0,120}\b(?:apply|use|fill|update|patch)\b"),
    ("evaluate", r"\b(?:evaluate|compare|test|check|score)\b.{0,120}\b(?:against\s+)?(?:answer\s*key|correct\s+template|correct\s+estimate|reviewed\s+template|reference\s+template)\b"),
)


@dataclass
class ParsedReferenceTemplateSummary:
    workbook_decision_preferences: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    row_count: int = 0
    mapped_row_count: int = 0


@dataclass
class EstimatorChatResult:
    assistant_message: str
    estimator_notes: str
    scope_overrides: dict[str, Any] = field(default_factory=dict)
    workbook_decision_preferences: list[dict[str, Any]] = field(default_factory=list)
    missing_questions: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    learning_mode: bool = False
    learning_intent: dict[str, Any] = field(default_factory=dict)
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
    attached_reference_answer_key: dict[str, Any] | None = None,
    provider: Callable[[list[dict[str, Any]], str], Any] | None = None,
    model: str | None = None,
) -> EstimatorChatResult:
    raw_message_list = list(messages or [])
    message_list = _clean_messages(raw_message_list)
    learning_intent = detect_estimator_learning_intent(raw_message_list)
    if not message_list:
        return EstimatorChatResult(
            assistant_message="Paste or type project notes and I will turn them into an estimator-ready draft.",
            estimator_notes="",
            confidence=0.0,
            missing_questions=["Project notes are needed before estimating."],
        )
    deterministic_baseline = deterministic_chat_fallback(message_list, template_type_hint=template_type_hint)
    answer_key_mode = detect_reference_answer_key_mode(raw_message_list)
    structured_answer_key = _parse_reference_answer_key_from_messages(raw_message_list)
    reference_summary = _parse_reference_template_summary_from_messages(
        raw_message_list,
        template_type_hint=template_type_hint,
    )
    attached_answer_key = attached_reference_answer_key if isinstance(attached_reference_answer_key, dict) else {}
    if not answer_key_mode and (structured_answer_key.mapped_row_count or reference_summary.mapped_row_count):
        answer_key_mode = "evaluate"
    should_apply_reference = answer_key_mode in {"apply", "teach"}
    attached_answer_key_summary = (
        _reference_answer_key_summary(
            attached_answer_key,
            template_type_hint=template_type_hint,
            warning_label="Applied attached estimator answer key",
        )
        if should_apply_reference and attached_answer_key
        else ParsedReferenceTemplateSummary()
    )
    if structured_answer_key.mapped_row_count and should_apply_reference:
        deterministic_baseline = _merge_reference_template_summary(
            deterministic_baseline,
            structured_answer_key,
            template_type_hint=template_type_hint,
        )
    if reference_summary.mapped_row_count and should_apply_reference:
        deterministic_baseline = _merge_reference_template_summary(
            deterministic_baseline,
            reference_summary,
            template_type_hint=template_type_hint,
        )
    if attached_answer_key_summary.mapped_row_count and should_apply_reference:
        deterministic_baseline = _merge_reference_template_summary(
            deterministic_baseline,
            attached_answer_key_summary,
            template_type_hint=template_type_hint,
        )
    if (structured_answer_key.mapped_row_count or reference_summary.mapped_row_count) and not should_apply_reference:
        deterministic_baseline.warnings.append(
            "Reference answer key/template summary detected but not applied. Say 'apply this answer key' to fill the current workbook, "
            "'learn from this answer key' to save it as memory, or use it only for evaluation."
        )
    deterministic_baseline.scope_overrides = _apply_basis_area_multiplier_from_messages(
        deterministic_baseline.scope_overrides,
        raw_message_list,
    )
    baseline_scope = _apply_basis_area_multiplier_from_messages(
        _merge_chat_scopes(existing_scope or {}, deterministic_baseline.scope_overrides),
        raw_message_list,
    )
    deterministic_baseline.scope_overrides = baseline_scope
    context = estimator_context_summary(data, scope=baseline_scope)
    matched_answer_key_summary = (
        _matched_answer_key_reference_summary(data, context, template_type_hint=template_type_hint)
        if should_apply_reference
        and not (structured_answer_key.mapped_row_count or reference_summary.mapped_row_count or attached_answer_key_summary.mapped_row_count)
        else ParsedReferenceTemplateSummary()
    )
    if matched_answer_key_summary.mapped_row_count:
        deterministic_baseline = _merge_reference_template_summary(
            deterministic_baseline,
            matched_answer_key_summary,
            template_type_hint=template_type_hint,
        )
    model_name = model or os.getenv("OPENAI_ESTIMATOR_CHAT_MODEL") or DEFAULT_CHAT_ESTIMATOR_MODEL
    prompt_messages = _chat_prompt_messages(
        message_list,
        template_type_hint=template_type_hint,
        existing_scope=baseline_scope,
        context=context,
    )
    if provider is not None or os.getenv("OPENAI_API_KEY"):
        try:
            raw = provider(prompt_messages, model_name) if provider is not None else _call_openai_chat(prompt_messages, model_name)
            payload = _extract_json_object(raw)
            result = normalize_chat_payload(
                payload,
                source="ai_chat",
                baseline_scope=baseline_scope,
                baseline_notes=deterministic_baseline.estimator_notes,
            )
            result = _attach_context_retrieval_summary(result, context)
        except Exception as exc:
            deterministic_baseline.warnings.append(f"AI estimator chat failed; used deterministic fallback. {type(exc).__name__}: {exc}")
            return _apply_learning_intent(
                _attach_context_retrieval_summary(deterministic_baseline, context),
                learning_intent,
                _best_learning_reference_summary(
                    reference_summary,
                    structured_answer_key,
                    attached_answer_key_summary,
                    matched_answer_key_summary,
                ),
                answer_key_mode=answer_key_mode,
            )
        if should_apply_reference:
            result = _merge_reference_template_summary(
                result,
                reference_summary,
                template_type_hint=template_type_hint,
            )
            result = _merge_reference_template_summary(
                result,
                structured_answer_key,
                template_type_hint=template_type_hint,
            )
            result = _merge_reference_template_summary(
                result,
                attached_answer_key_summary,
                template_type_hint=template_type_hint,
            )
            result = _merge_reference_template_summary(
                result,
                matched_answer_key_summary,
                template_type_hint=template_type_hint,
            )
        elif structured_answer_key.mapped_row_count or reference_summary.mapped_row_count:
            result.workbook_decision_preferences = []
            result.warnings.append(
                "Reference answer key/template summary detected but not applied. Say 'apply this answer key' to fill the current workbook, "
                "'learn from this answer key' to save it as memory, or use it only for evaluation."
            )
        return _apply_learning_intent(
            result,
            learning_intent,
            _best_learning_reference_summary(
                reference_summary,
                structured_answer_key,
                attached_answer_key_summary,
                matched_answer_key_summary,
            ),
            answer_key_mode=answer_key_mode,
        )
    deterministic_baseline.warnings.append("OPENAI_API_KEY is not configured; used deterministic estimator-chat fallback.")
    return _apply_learning_intent(
        _attach_context_retrieval_summary(deterministic_baseline, context),
        learning_intent,
        _best_learning_reference_summary(
            reference_summary,
            structured_answer_key,
            attached_answer_key_summary,
            matched_answer_key_summary,
        ),
        answer_key_mode=answer_key_mode,
    )


LEARNING_INTENT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("learn_from_this", r"\b(?:learn|remember)\s+(?:from\s+)?this\b"),
    ("save_template_example", r"\b(?:save|store|use)\s+(?:this\s+)?(?:as\s+)?(?:a\s+)?(?:template\s+)?(?:example|training\s+example|memory)\b"),
    ("generate_and_remember", r"\b(?:generate|build|create)\s+(?:the\s+)?(?:workbook|template|estimate).{0,80}\b(?:remember|learn|save)\b"),
    ("remember_and_generate", r"\b(?:remember|learn|save).{0,80}\b(?:generate|build|create)\s+(?:the\s+)?(?:workbook|template|estimate)\b"),
)


def detect_estimator_learning_intent(messages: Iterable[dict[str, Any]]) -> dict[str, Any]:
    user_text = "\n".join(
        str(message.get("content") or "")
        for message in messages
        if isinstance(message, dict) and str(message.get("role") or "") == "user"
    )
    normalized = _clean_string(user_text).lower()
    if not normalized:
        return {}
    matched: list[str] = []
    for label, pattern in LEARNING_INTENT_PATTERNS:
        if re.search(pattern, normalized, re.I | re.S):
            matched.append(label)
    if not matched:
        return {}
    return {
        "explicit": True,
        "triggers": matched,
        "auto_build_workbook": True,
        "auto_save_memory": True,
        "auto_approve_memory": True,
        "source": "explicit_chat_keyword",
    }


def _attach_context_retrieval_summary(result: EstimatorChatResult, context: dict[str, Any]) -> EstimatorChatResult:
    answer_key_context = context.get("historical_answer_key_examples") if isinstance(context, dict) else {}
    matched = (
        answer_key_context.get("matched_answer_keys")
        if isinstance(answer_key_context, dict) and isinstance(answer_key_context.get("matched_answer_keys"), list)
        else []
    )
    if not matched:
        return result
    rows: list[dict[str, Any]] = []
    for example in matched[:5]:
        if not isinstance(example, dict):
            continue
        answer_key = example.get("reference_answer_key") if isinstance(example.get("reference_answer_key"), dict) else {}
        decisions = answer_key.get("decisions") if isinstance(answer_key.get("decisions"), list) else []
        rows.append(
            {
                "job_id": example.get("job_id"),
                "customer": example.get("customer"),
                "job_name": example.get("job_name"),
                "source_file": example.get("source_file"),
                "similarity_score": example.get("similarity_score"),
                "match_reasons": example.get("match_reasons") or [],
                "decision_count_sent": len(decisions),
            }
        )
    if not rows:
        return result
    result.raw_response = {
        **(result.raw_response or {}),
        "historical_answer_key_matches": rows,
    }
    return result


def _best_learning_reference_summary(
    *summaries: ParsedReferenceTemplateSummary | None,
) -> ParsedReferenceTemplateSummary:
    for summary in summaries:
        if summary and summary.mapped_row_count:
            return summary
    return ParsedReferenceTemplateSummary()


def _matched_answer_key_reference_summary(
    data: EstimatorData | None,
    context: dict[str, Any],
    *,
    template_type_hint: str = "",
) -> ParsedReferenceTemplateSummary:
    answer_key_context = context.get("historical_answer_key_examples") if isinstance(context, dict) else {}
    matched = (
        answer_key_context.get("matched_answer_keys")
        if isinstance(answer_key_context, dict) and isinstance(answer_key_context.get("matched_answer_keys"), list)
        else []
    )
    if not matched:
        return ParsedReferenceTemplateSummary()
    preferences: list[dict[str, Any]] = []
    row_count = 0
    warnings: list[str] = []
    for example in matched[:1]:
        if not isinstance(example, dict):
            continue
        answer_key = _full_answer_key_for_matched_example(data, example)
        if not answer_key:
            answer_key = example.get("reference_answer_key") if isinstance(example.get("reference_answer_key"), dict) else {}
        if not answer_key:
            continue
        summary = _reference_answer_key_summary(answer_key, template_type_hint=template_type_hint)
        row_count += summary.row_count
        preferences.extend(summary.workbook_decision_preferences)
        label = _clean_string(example.get("job_name") or example.get("customer") or example.get("source_file") or example.get("job_id"))
        if label:
            warnings.append(f"Applied matched historical answer key: {label}.")
    cleaned = _clean_decision_preferences(preferences, template_type=template_type_hint)
    return ParsedReferenceTemplateSummary(
        workbook_decision_preferences=cleaned,
        warnings=warnings,
        row_count=row_count,
        mapped_row_count=len(cleaned),
    )


def _reference_answer_key_summary(
    answer_key: dict[str, Any],
    *,
    template_type_hint: str = "",
    warning_label: str = "",
) -> ParsedReferenceTemplateSummary:
    if not isinstance(answer_key, dict) or not answer_key.get("decisions"):
        return ParsedReferenceTemplateSummary()
    preferences = answer_key_to_workbook_decision_preferences(answer_key)
    cleaned = _clean_decision_preferences(preferences, template_type=template_type_hint)
    label = _clean_string(
        ((answer_key.get("source_workbook") or {}).get("file_name") if isinstance(answer_key.get("source_workbook"), dict) else "")
        or ((answer_key.get("job_context") or {}).get("job_name") if isinstance(answer_key.get("job_context"), dict) else "")
        or ((answer_key.get("job_context") or {}).get("customer") if isinstance(answer_key.get("job_context"), dict) else "")
    )
    warnings: list[str] = []
    if warning_label and label:
        warnings.append(f"{warning_label}: {label}.")
    elif warning_label:
        warnings.append(f"{warning_label}.")
    return ParsedReferenceTemplateSummary(
        workbook_decision_preferences=cleaned,
        warnings=warnings,
        row_count=int((answer_key.get("summary") or {}).get("source_row_count") or len(answer_key.get("decisions") or [])),
        mapped_row_count=len(cleaned),
    )


def _full_answer_key_for_matched_example(data: EstimatorData | None, example: dict[str, Any]) -> dict[str, Any]:
    examples = getattr(data, "template_examples", pd.DataFrame()) if data is not None else pd.DataFrame()
    if not isinstance(examples, pd.DataFrame) or examples.empty or "answer_key_json" not in examples.columns:
        return {}
    frame = examples.fillna("").copy()
    filters: list[pd.Series] = []
    for column in ("example_id", "document_id"):
        value = _clean_string(example.get(column))
        if value and column in frame.columns:
            filters.append(frame[column].fillna("").astype(str).eq(value))
    job_id = _clean_string(example.get("job_id"))
    source_file = _clean_string(example.get("source_file"))
    if job_id and source_file and {"job_id", "source_file"}.issubset(frame.columns):
        filters.append(
            frame["job_id"].fillna("").astype(str).eq(job_id)
            & frame["source_file"].fillna("").astype(str).eq(source_file)
        )
    if job_id and "job_id" in frame.columns:
        filters.append(frame["job_id"].fillna("").astype(str).eq(job_id))
    if source_file and "source_file" in frame.columns:
        filters.append(frame["source_file"].fillna("").astype(str).eq(source_file))
    for mask in filters:
        matched = frame[mask]
        if matched.empty:
            continue
        payload = _json_payload(matched.iloc[0].get("answer_key_json"))
        if isinstance(payload, dict) and payload.get("decisions"):
            return payload
    return {}


def detect_reference_answer_key_mode(messages: Iterable[dict[str, Any]]) -> str:
    user_text = "\n".join(
        str(message.get("content") or "")
        for message in messages
        if isinstance(message, dict) and str(message.get("role") or "") == "user"
    )
    normalized = _clean_string(user_text).lower()
    if not normalized:
        return ""
    for mode, pattern in ANSWER_KEY_MODE_PATTERNS:
        if re.search(pattern, normalized, re.I | re.S):
            return mode
    if re.search(r"\b(?:answer\s*key|correct\s+template|correct\s+estimate|reviewed\s+template|reference\s+template)\b", normalized, re.I):
        return "evaluate"
    return ""


def _apply_learning_intent(
    result: EstimatorChatResult,
    learning_intent: dict[str, Any],
    reference_summary: ParsedReferenceTemplateSummary | None = None,
    *,
    answer_key_mode: str = "",
) -> EstimatorChatResult:
    if not learning_intent:
        if answer_key_mode:
            scope = dict(result.scope_overrides or {})
            scope["reference_answer_key_mode"] = answer_key_mode
            result.scope_overrides = scope
        return result
    scope = dict(result.scope_overrides or {})
    scope["explicit_learning_intent"] = True
    scope["learning_intent_source"] = learning_intent.get("source") or "explicit_chat_keyword"
    scope["reference_answer_key_mode"] = answer_key_mode or "teach"
    if reference_summary and reference_summary.mapped_row_count:
        scope["learning_reference_template_row_count"] = reference_summary.row_count
        scope["learning_reference_template_mapped_row_count"] = reference_summary.mapped_row_count
    assistant_message = result.assistant_message
    learning_line = (
        "Learning mode is on for this message. I will use the mapped decisions as a reviewed example, "
        "build the workbook, and save memory for future similar jobs."
    )
    if learning_line not in assistant_message:
        assistant_message = _clean_string(f"{assistant_message}\n\n{learning_line}")
    return EstimatorChatResult(
        assistant_message=assistant_message,
        estimator_notes=result.estimator_notes,
        scope_overrides=scope,
        workbook_decision_preferences=result.workbook_decision_preferences,
        missing_questions=result.missing_questions,
        assumptions=result.assumptions,
        learning_mode=True,
        learning_intent=learning_intent,
        confidence=result.confidence,
        source=result.source,
        raw_response=result.raw_response,
        warnings=result.warnings,
    )


CHAT_DECISION_MENU: dict[str, list[dict[str, Any]]] = {
    "insulation": [
        {
            "decision_id": "insulation_foam_template_selector",
            "section": "insulation_foam_template_decisions",
            "template_bucket": "foam",
            "workbook_row": "19",
            "label": "Spray foam system",
            "editable_fields": ["include", "foam_type", "basis_sqft", "thickness_inches", "yield_or_coverage", "unit_price"],
            "formula_requirements": ["basis_sqft", "thickness_inches", "yield_or_coverage", "unit_price"],
        },
        {
            "decision_id": "insulation_thermal_barrier_coating",
            "section": "insulation_thermal_barrier_template_decisions",
            "template_bucket": "thermal_barrier_coating",
            "workbook_row": "30",
            "label": "Thermal/ignition barrier coating",
            "editable_fields": ["include", "basis_sqft", "coverage_sqft_per_unit", "unit_price"],
            "formula_requirements": ["basis_sqft", "coverage_sqft_per_unit", "unit_price"],
        },
        {
            "decision_id": "insulation_caulk_sealant",
            "section": "insulation_detail_material_template_decisions",
            "template_bucket": "caulk_sealant",
            "workbook_row": "41",
            "label": "Sealant/detail material",
            "editable_fields": ["include", "linear_ft", "feet_per_unit", "unit_price"],
            "formula_requirements": ["linear_ft", "feet_per_unit", "unit_price"],
        },
        {
            "decision_id": "insulation_labor_foam",
            "section": "insulation_labor_template_decisions",
            "template_bucket": "labor_foam",
            "workbook_row": "80",
            "label": "Foam application labor",
            "editable_fields": ["include", "total_hours", "days", "crew_size", "hourly_rate", "daily_rate"],
            "formula_requirements": ["total_hours and hourly_rate", "or days and daily_rate"],
        },
        {
            "decision_id": "insulation_labor_loading",
            "section": "insulation_logistics_expense_template_decisions",
            "template_bucket": "labor_loading",
            "workbook_row": "95",
            "label": "Loading",
            "editable_fields": ["include", "hours_per_day", "people_count", "trip_count", "unit_price"],
            "formula_requirements": ["hours_per_day", "people_count", "unit_price", "optional trip_count"],
        },
        {
            "decision_id": "insulation_labor_traveling",
            "section": "insulation_logistics_expense_template_decisions",
            "template_bucket": "labor_traveling",
            "workbook_row": "97",
            "label": "Traveling",
            "editable_fields": ["include", "hours_per_day", "people_count", "trip_count", "unit_price"],
            "formula_requirements": ["hours_per_day", "people_count", "unit_price", "optional trip_count"],
        },
        {
            "decision_id": "insulation_infrared_scan",
            "section": "insulation_logistics_expense_template_decisions",
            "template_bucket": "infrared_scan",
            "workbook_row": "99",
            "label": "Infrared Scan",
            "editable_fields": ["include", "hours_per_day", "unit_price"],
            "formula_requirements": ["hours_per_day", "unit_price"],
        },
        {
            "decision_id": "insulation_meals_lodging",
            "section": "insulation_logistics_expense_template_decisions",
            "template_bucket": "meals_lodging",
            "workbook_row": "100",
            "label": "Meals / Lodging",
            "editable_fields": ["include", "days", "people_count", "unit_price"],
            "formula_requirements": ["days", "people_count", "unit_price"],
        },
        {
            "decision_id": "pricing_overhead",
            "section": "pricing_markup_decisions",
            "template_bucket": "overhead",
            "workbook_row": "118",
            "label": "Overhead percentage",
            "editable_fields": ["include", "markup_pct", "percentage"],
            "formula_requirements": ["markup_pct or percentage"],
        },
        {
            "decision_id": "pricing_profit",
            "section": "pricing_markup_decisions",
            "template_bucket": "profit",
            "workbook_row": "120",
            "label": "Profit percentage",
            "editable_fields": ["include", "markup_pct", "percentage"],
            "formula_requirements": ["markup_pct or percentage"],
        },
    ],
    "roofing": [
        {
            "decision_id": "roofing_foam_row_19",
            "section": "roofing_foam_template_decisions",
            "template_bucket": "foam",
            "workbook_row": "19",
            "label": "Roof SPF foam",
            "editable_fields": ["include", "basis_sqft", "thickness_inches", "yield_or_coverage", "unit_price"],
            "formula_requirements": ["basis_sqft", "thickness_inches", "yield_or_coverage", "unit_price"],
        },
        {
            "decision_id": "roofing_coating_system_row_26",
            "section": "roofing_coating_template_decisions",
            "template_bucket": "coating",
            "workbook_row": "26",
            "label": "Roof coating system",
            "editable_fields": ["include", "basis_sqft", "gal_per_100_sqft", "unit_price", "waste_factor_pct"],
            "formula_requirements": ["basis_sqft", "gal_per_100_sqft", "unit_price"],
        },
        {
            "decision_id": "roofing_primer_row_39",
            "section": "roofing_primer_template_decisions",
            "template_bucket": "primer",
            "workbook_row": "39",
            "label": "Primer",
            "editable_fields": ["include", "basis_sqft", "coverage_sqft_per_unit", "unit_price"],
            "formula_requirements": ["basis_sqft", "coverage_sqft_per_unit", "unit_price"],
        },
        {
            "decision_id": "roofing_caulk_detail_row_43",
            "section": "roofing_detail_template_decisions",
            "template_bucket": "caulk_detail",
            "workbook_row": "43",
            "label": "Caulk/detail sealant",
            "editable_fields": ["include", "estimated_units", "unit_price", "linear_ft"],
            "formula_requirements": ["estimated_units", "unit_price"],
        },
        {
            "decision_id": "roofing_fabric_row_79",
            "section": "roofing_detail_template_decisions",
            "template_bucket": "fabric",
            "workbook_row": "79",
            "label": "Fabric/reinforcement",
            "editable_fields": ["include", "linear_ft", "coverage_sqft_per_unit", "unit_price"],
            "formula_requirements": ["linear_ft or basis_sqft", "coverage_sqft_per_unit", "unit_price"],
        },
        {
            "decision_id": "roofing_seams_misc_row_47",
            "section": "roofing_detail_quantity_template_decisions",
            "template_bucket": "seams_misc",
            "workbook_row": "47",
            "label": "Seam quantity",
            "editable_fields": ["include", "linear_ft", "estimated_units", "amount"],
            "formula_requirements": ["linear_ft or estimated_units or amount"],
        },
        {
            "decision_id": "roofing_penetrations_row_49",
            "section": "roofing_detail_quantity_template_decisions",
            "template_bucket": "penetrations",
            "workbook_row": "49",
            "label": "Penetration/detail quantity",
            "editable_fields": ["include", "estimated_units", "units", "amount"],
            "formula_requirements": ["estimated_units or units or amount"],
        },
        {
            "decision_id": "roofing_board_stock_row_58",
            "section": "roofing_board_fastener_template_decisions",
            "template_bucket": "board_stock",
            "workbook_row": "58",
            "label": "Board stock",
            "editable_fields": ["include", "basis_sqft", "thickness_inches", "price_per_square"],
            "formula_requirements": ["basis_sqft", "price_per_square"],
        },
        {
            "decision_id": "roofing_fasteners_row_63",
            "section": "roofing_board_fastener_template_decisions",
            "template_bucket": "fasteners",
            "workbook_row": "63",
            "label": "Fasteners",
            "editable_fields": ["include", "board_area_sqft", "unit_price_per_thousand", "estimated_units"],
            "formula_requirements": ["board_area_sqft", "unit_price_per_thousand"],
        },
        {
            "decision_id": "roofing_plates_row_65",
            "section": "roofing_board_fastener_template_decisions",
            "template_bucket": "plates",
            "workbook_row": "65",
            "label": "Plates",
            "editable_fields": ["include", "board_area_sqft", "unit_price_per_thousand", "estimated_units"],
            "formula_requirements": ["board_area_sqft", "unit_price_per_thousand"],
        },
        {
            "decision_id": "roofing_granules_row_36",
            "section": "roofing_granules_template_decisions",
            "template_bucket": "granules",
            "workbook_row": "36",
            "label": "Granules",
            "editable_fields": ["include", "basis_sqft", "unit_price"],
            "formula_requirements": ["basis_sqft", "unit_price"],
        },
        {
            "decision_id": "roofing_dumpsters_row_69",
            "section": "roofing_equipment_template_decisions",
            "template_bucket": "dumpster",
            "workbook_row": "69",
            "label": "Dumpster/disposal",
            "editable_fields": ["include", "basis_sqft", "thickness_inches", "unit_price", "margin_pct"],
            "formula_requirements": ["basis_sqft", "thickness_inches", "unit_price"],
        },
        {
            "decision_id": "roofing_lift_equipment_row_73",
            "section": "roofing_equipment_template_decisions",
            "template_bucket": "lift",
            "workbook_row": "73",
            "label": "Lift/equipment access",
            "editable_fields": ["include", "period", "unit_price", "margin_pct"],
            "formula_requirements": ["period", "unit_price"],
        },
        {
            "decision_id": "roofing_generator_row_99",
            "section": "roofing_equipment_template_decisions",
            "template_bucket": "generator",
            "workbook_row": "99",
            "label": "Generator",
            "editable_fields": ["include", "days", "unit_price"],
            "formula_requirements": ["days", "unit_price"],
        },
        {
            "decision_id": "roofing_sales_trips_row_106",
            "section": "roofing_travel_freight_template_decisions",
            "template_bucket": "sales_trips",
            "workbook_row": "106",
            "label": "Sales / inspection trips",
            "editable_fields": ["include", "trip_count", "round_trip_miles", "unit_price"],
            "formula_requirements": ["trip_count", "round_trip_miles", "unit_price"],
        },
        {
            "decision_id": "roofing_truck_expense_row_108",
            "section": "roofing_travel_freight_template_decisions",
            "template_bucket": "truck_expense",
            "workbook_row": "108",
            "label": "Truck expense",
            "editable_fields": ["include", "trip_count", "round_trip_miles", "unit_price"],
            "formula_requirements": ["trip_count", "round_trip_miles", "unit_price"],
        },
        {
            "decision_id": "roofing_labor_prep_row_116",
            "section": "roofing_labor_template_decisions",
            "template_bucket": "labor_prep",
            "workbook_row": "116",
            "label": "Prep labor",
            "editable_fields": ["include", "days", "crew_size", "daily_rate", "hourly_rate", "total_hours"],
            "formula_requirements": ["daily_rate and days", "or total_hours and hourly_rate"],
        },
        {
            "decision_id": "roofing_labor_seam_sealer_row_120",
            "section": "roofing_labor_template_decisions",
            "template_bucket": "labor_seam_sealer",
            "workbook_row": "120",
            "label": "Seam/detail labor",
            "editable_fields": ["include", "days", "crew_size", "daily_rate", "hourly_rate", "total_hours"],
            "formula_requirements": ["daily_rate and days", "or total_hours and hourly_rate"],
        },
        {
            "decision_id": "roofing_labor_base_row_122",
            "section": "roofing_labor_template_decisions",
            "template_bucket": "labor_base",
            "workbook_row": "122",
            "label": "Base roofing labor",
            "editable_fields": ["include", "days", "crew_size", "daily_rate", "hourly_rate", "total_hours"],
            "formula_requirements": ["daily_rate and days", "or total_hours and hourly_rate"],
        },
        {
            "decision_id": "roofing_labor_top_coat_row_124",
            "section": "roofing_labor_template_decisions",
            "template_bucket": "labor_top_coat",
            "workbook_row": "124",
            "label": "Top coat labor",
            "editable_fields": ["include", "days", "crew_size", "daily_rate", "hourly_rate", "total_hours"],
            "formula_requirements": ["daily_rate and days", "or total_hours and hourly_rate"],
        },
        {
            "decision_id": "roofing_labor_cleanup_row_132",
            "section": "roofing_labor_template_decisions",
            "template_bucket": "labor_cleanup",
            "workbook_row": "132",
            "label": "Cleanup labor",
            "editable_fields": ["include", "days", "crew_size", "daily_rate", "hourly_rate", "total_hours"],
            "formula_requirements": ["daily_rate and days", "or total_hours and hourly_rate"],
        },
        {
            "decision_id": "roofing_labor_loading_row_136",
            "section": "roofing_logistics_expense_template_decisions",
            "template_bucket": "labor_loading",
            "workbook_row": "136",
            "label": "Loading",
            "editable_fields": ["include", "hours_per_day", "people_count", "trip_count", "unit_price"],
            "formula_requirements": ["hours_per_day", "people_count", "unit_price", "optional trip_count"],
        },
        {
            "decision_id": "roofing_labor_traveling_row_138",
            "section": "roofing_logistics_expense_template_decisions",
            "template_bucket": "labor_traveling",
            "workbook_row": "138",
            "label": "Traveling",
            "editable_fields": ["include", "hours_per_day", "people_count", "trip_count", "unit_price"],
            "formula_requirements": ["hours_per_day", "people_count", "unit_price", "optional trip_count"],
        },
        {
            "decision_id": "roofing_infrared_scan_row_141",
            "section": "roofing_logistics_expense_template_decisions",
            "template_bucket": "infrared_scan",
            "workbook_row": "141",
            "label": "Infrared Scan",
            "editable_fields": ["include", "hours_per_day", "unit_price"],
            "formula_requirements": ["hours_per_day", "unit_price"],
        },
        {
            "decision_id": "roofing_meals_lodging_row_144",
            "section": "roofing_logistics_expense_template_decisions",
            "template_bucket": "meals_lodging",
            "workbook_row": "144",
            "label": "Meals / Hotel",
            "editable_fields": ["include", "days", "people_count", "unit_price"],
            "formula_requirements": ["days", "people_count", "unit_price"],
        },
        {
            "decision_id": "pricing_overhead",
            "section": "pricing_markup_decisions",
            "template_bucket": "overhead",
            "workbook_row": "165",
            "label": "Overhead percentage",
            "editable_fields": ["include", "markup_pct", "percentage"],
            "formula_requirements": ["markup_pct or percentage"],
        },
        {
            "decision_id": "pricing_profit",
            "section": "pricing_markup_decisions",
            "template_bucket": "profit",
            "workbook_row": "167",
            "label": "Profit percentage",
            "editable_fields": ["include", "markup_pct", "percentage"],
            "formula_requirements": ["markup_pct or percentage"],
        },
    ],
}


_ESTIMATOR_CONTEXT_CACHE: dict[str, dict[str, Any]] = {}
_ESTIMATOR_CONTEXT_CACHE_STATS = {"hit": 0, "miss": 0}


def _frame_signature(frame: Any) -> tuple[int, tuple[str, ...]]:
    if not isinstance(frame, pd.DataFrame):
        return (0, ())
    return (len(frame), tuple(str(column) for column in frame.columns))


def _estimator_data_signature(data: EstimatorData | None) -> str:
    if data is None:
        return "none"
    signature = {
        "id": id(data),
        "source_files": tuple(str(value) for value in (getattr(data, "source_files_used", None) or [])),
        "template_rows": _frame_signature(getattr(data, "template_rows", None)),
        "template_row_catalog": _frame_signature(getattr(data, "template_row_catalog", None)),
        "template_formula_models": _frame_signature(getattr(data, "template_formula_models", None)),
        "template_product_options": _frame_signature(getattr(data, "template_product_options", None)),
        "pricing": _frame_signature(getattr(data, "pricing", None)),
        "pricing_catalog": _frame_signature(getattr(data, "pricing_catalog", None)),
        "product_catalog": _frame_signature(getattr(data, "product_catalog", None)),
        "product_properties": _frame_signature(getattr(data, "product_properties", None)),
        "foam_yield_history": _frame_signature(getattr(data, "foam_yield_history", None)),
        "job_context_profiles": _frame_signature(getattr(data, "job_context_profiles", None)),
        "template_examples": _frame_signature(getattr(data, "template_examples", None)),
        "decision_recommendations": _frame_signature(getattr(data, "estimator_decision_recommendations", None)),
        "relationships": _frame_signature(getattr(data, "relationship_package_cooccurrence", None)),
        "estimator_memory": _frame_signature(getattr(data, "estimator_memory", None)),
    }
    return hashlib.sha1(json.dumps(signature, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _context_cache_key(data: EstimatorData | None, scope: dict[str, Any] | None) -> str:
    payload = {
        "data": _estimator_data_signature(data),
        "scope": scope or {},
        "template_type": _template_type_for_scope(scope or {}),
    }
    return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def estimator_context_summary(data: EstimatorData | None, *, scope: dict[str, Any] | None = None) -> dict[str, Any]:
    key = _context_cache_key(data, scope)
    cached = _ESTIMATOR_CONTEXT_CACHE.get(key)
    if cached is not None:
        _ESTIMATOR_CONTEXT_CACHE_STATS["hit"] += 1
        return copy.deepcopy(cached)
    _ESTIMATOR_CONTEXT_CACHE_STATS["miss"] += 1
    summary = _build_estimator_context_summary(data, scope=scope)
    if len(_ESTIMATOR_CONTEXT_CACHE) > 24:
        _ESTIMATOR_CONTEXT_CACHE.clear()
    _ESTIMATOR_CONTEXT_CACHE[key] = copy.deepcopy(summary)
    return summary


def estimator_context_cache_stats() -> dict[str, int]:
    return dict(_ESTIMATOR_CONTEXT_CACHE_STATS)


def _build_estimator_context_summary(data: EstimatorData | None, *, scope: dict[str, Any] | None = None) -> dict[str, Any]:
    if data is None:
        return _empty_chat_decision_context(scope)
    scope = scope or {}
    template_type = _template_type_for_scope(scope)
    decision_menu = _build_template_decision_menu(data, template_type=template_type) or CHAT_DECISION_MENU.get(template_type, [])
    summary: dict[str, Any] = {
        "template_rows": _frame_len(data.template_rows),
        "pricing_rows": _frame_len(data.pricing),
        "product_rows": _frame_len(data.product_catalog),
        "decision_recommendation_rows": _frame_len(data.estimator_decision_recommendations),
        **_empty_chat_decision_context(scope),
    }
    summary["decision_menu"] = decision_menu
    summary["formula_requirements"] = [
        {
            "decision_id": row["decision_id"],
            "template_bucket": row["template_bucket"],
            "workbook_row": row.get("workbook_row", ""),
            "required_inputs": row.get("formula_requirements") or [],
        }
        for row in decision_menu
    ]
    summary["estimator_memory_guidance"] = relevant_memory_rows(
        getattr(data, "estimator_memory", pd.DataFrame()),
        scope=scope,
        template_type=template_type,
        decision_buckets=[
            str(value)
            for row in decision_menu
            for value in (row.get("template_bucket"), row.get("decision_id"))
            if value
        ],
        limit=12,
    )
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
    summary["historical_decision_evidence"] = _historical_decision_evidence(data, template_type=template_type)
    summary["foam_yield_history_digest"] = build_foam_yield_history_digest(
        data,
        scope=scope,
        template_type=template_type,
        limit=8,
    ) if template_type in {"insulation", "roofing"} else []
    summary["template_fallback_defaults"] = (
        {
            "insulation_foam": {
                "yield_or_coverage": INSULATION_CHAT_TEMPLATE_DEFAULTS["foam_yield_or_coverage"],
                "unit_price": INSULATION_CHAT_TEMPLATE_DEFAULTS["foam_unit_price"],
                "use_when": "Use only as a review-marked template fallback when scope indicates spray foam but matching history/pricing is missing or thin.",
            },
            "insulation_logistics": {
                "loading_hourly_rate": INSULATION_CHAT_TEMPLATE_DEFAULTS["loading_hourly_rate"],
                "traveling_hourly_rate": INSULATION_CHAT_TEMPLATE_DEFAULTS["traveling_hourly_rate"],
                "generator_daily_rate": INSULATION_CHAT_TEMPLATE_DEFAULTS["generator_daily_rate"],
            },
        }
        if template_type == "insulation"
        else {}
    )
    summary["pricing_candidates_by_bucket"] = _pricing_candidates_by_bucket(data, template_type=template_type)
    summary["product_guidance_digest"] = _product_guidance_digest(data, template_type=template_type)
    summary["companion_relationships"] = _companion_relationships(data, template_type=template_type)
    summary["reference_job_decisions"] = _reference_job_decisions(data, scope=scope, template_type=template_type)
    summary["historical_job_context"] = build_job_context_digest(data, scope=scope, limit=5)
    summary["historical_context_decision_guidance"] = _historical_context_decision_guidance(
        summary["historical_job_context"],
        decision_menu,
        template_type=template_type,
    )
    summary["historical_template_examples"] = build_template_example_digest(data, scope=scope, limit=3)
    summary["historical_answer_key_examples"] = build_similar_answer_key_digest(
        data,
        scope=scope,
        limit=5,
        decisions_per_example=30,
        decision_menu=decision_menu,
    )
    return summary


def _empty_chat_decision_context(scope: dict[str, Any] | None) -> dict[str, Any]:
    template_type = _template_type_for_scope(scope or {})
    decision_menu = CHAT_DECISION_MENU.get(template_type, [])
    return {
        "template_type": template_type,
        "decision_menu": decision_menu,
        "formula_requirements": [
            {
                "decision_id": row["decision_id"],
                "template_bucket": row["template_bucket"],
                "required_inputs": row["formula_requirements"],
            }
            for row in decision_menu
        ],
        "historical_decision_evidence": [],
        "estimator_memory_guidance": [],
        "foam_yield_history_digest": [],
        "template_fallback_defaults": (
            {
                "insulation_foam": {
                    "yield_or_coverage": INSULATION_CHAT_TEMPLATE_DEFAULTS["foam_yield_or_coverage"],
                    "unit_price": INSULATION_CHAT_TEMPLATE_DEFAULTS["foam_unit_price"],
                    "use_when": "Use only as a review-marked template fallback when scope indicates spray foam but matching history/pricing is missing or thin.",
                },
                "insulation_logistics": {
                    "loading_hourly_rate": INSULATION_CHAT_TEMPLATE_DEFAULTS["loading_hourly_rate"],
                    "traveling_hourly_rate": INSULATION_CHAT_TEMPLATE_DEFAULTS["traveling_hourly_rate"],
                    "generator_daily_rate": INSULATION_CHAT_TEMPLATE_DEFAULTS["generator_daily_rate"],
                },
            }
            if template_type == "insulation"
            else {}
        ),
        "pricing_candidates_by_bucket": [],
        "product_guidance_digest": [],
        "companion_relationships": [],
        "reference_job_decisions": [],
        "historical_job_context": {"matched_profiles": [], "aggregate_priors": []},
        "historical_context_decision_guidance": [],
        "historical_template_examples": {"matched_examples": []},
        "historical_answer_key_examples": {"matched_answer_keys": []},
    }


def _build_template_decision_menu(data: EstimatorData, *, template_type: str) -> list[dict[str, Any]]:
    catalog = data.template_row_catalog
    if not isinstance(catalog, pd.DataFrame) or catalog.empty:
        return []
    rows = _filter_template_frame(catalog, template_type)
    if rows.empty:
        return []
    formula_rows = data.template_formula_models if isinstance(data.template_formula_models, pd.DataFrame) else pd.DataFrame()
    formula_lookup = _formula_metadata_lookup(_filter_template_frame(formula_rows, template_type))
    graph_lookup = _decision_graph_lookup(data, template_type=template_type)
    menu: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows.fillna("").to_dict(orient="records"):
        line_item_kind = _clean_string(row.get("line_item_kind")).lower()
        bucket = _clean_string(row.get("template_bucket"))
        row_number = _safe_row_number(row.get("row_number"))
        formula_model = _clean_string(row.get("formula_model"))
        if not bucket or not row_number:
            continue
        if line_item_kind in {"header", "total", "subtotal", "metadata", "other"}:
            continue
        if not formula_model and line_item_kind not in {"material", "labor", "equipment", "adder", "pricing"}:
            continue
        formula_meta = formula_lookup.get((row_number, bucket.lower())) or formula_lookup.get((row_number, "")) or {}
        formula_model = _clean_string(formula_meta.get("formula_model") or formula_model)
        roles = _json_payload(row.get("cell_roles_json") or row.get("cell_roles") or {})
        dependencies = _json_payload(formula_meta.get("dependencies_json") or formula_meta.get("dependencies") or [])
        editable_fields = _editable_fields_from_template_metadata(
            line_item_kind=line_item_kind,
            bucket=bucket,
            formula_model=formula_model,
            roles=roles,
            dependencies=dependencies,
        )
        formula_requirements = _formula_requirements_from_template_metadata(
            line_item_kind=line_item_kind,
            bucket=bucket,
            formula_model=formula_model,
            roles=roles,
            dependencies=dependencies,
        )
        graph_details = graph_lookup.get((row_number, bucket.lower())) or graph_lookup.get((row_number, "")) or {}
        decision_id = _clean_string(row.get("decision_id") or graph_details.get("decision_id")) or _decision_id_for_template_row(
            template_type,
            bucket,
            row_number,
        )
        key = (decision_id, bucket.lower(), row_number)
        if key in seen:
            continue
        seen.add(key)
        source = "template_row_catalog+decision_graph" if graph_details.get("decision_id") else "template_row_catalog"
        menu.append(
            {
                "decision_id": decision_id,
                "section": _section_for_template_row(template_type, line_item_kind, bucket),
                "template_bucket": bucket,
                "workbook_row": row_number,
                "label": _clean_string(graph_details.get("title")) or _label_from_bucket(bucket),
                "line_item_kind": line_item_kind,
                "formula_model": formula_model,
                "editable_fields": editable_fields,
                "formula_requirements": formula_requirements,
                "graph_category": _clean_string(graph_details.get("category")),
                "rows_controlled": graph_details.get("rows_controlled") or [],
                "source": source,
            }
        )
        if len(menu) >= 35:
            break
    return _merge_decision_menus(menu, CHAT_DECISION_MENU.get(template_type, []))


def _formula_metadata_lookup(frame: pd.DataFrame) -> dict[tuple[str, str], dict[str, Any]]:
    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return lookup
    for row in frame.fillna("").to_dict(orient="records"):
        row_number = _safe_row_number(row.get("row_number"))
        if not row_number:
            continue
        bucket = _clean_string(row.get("template_bucket")).lower()
        lookup[(row_number, bucket)] = row
        lookup.setdefault((row_number, ""), row)
    return lookup


def _decision_graph_lookup(data: EstimatorData, *, template_type: str) -> dict[tuple[str, str], dict[str, Any]]:
    graph = _decision_graph_payload_from_data(data, template_type=template_type) or _decision_graph_payload_from_output(template_type)
    if not graph:
        return {}
    nodes = graph.get("decision_nodes") or []
    node_by_id = {str(node.get("decision_id") or ""): node for node in nodes if isinstance(node, dict)}
    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for row in graph.get("row_traceability") or []:
        if not isinstance(row, dict):
            continue
        row_number = _safe_row_number(row.get("row_number"))
        decision_id = _clean_string(row.get("decision_id"))
        if not row_number or not decision_id:
            continue
        bucket = _clean_string(row.get("template_bucket")).lower()
        node = node_by_id.get(decision_id, {})
        details = {
            "decision_id": decision_id,
            "title": node.get("title") or node.get("label") or "",
            "category": node.get("category") or "",
            "rows_controlled": node.get("rows_controlled") or [],
            "input_fields": node.get("input_fields") or [],
        }
        lookup[(row_number, bucket)] = details
        lookup.setdefault((row_number, ""), details)
    for node in nodes:
        if not isinstance(node, dict):
            continue
        decision_id = _clean_string(node.get("decision_id"))
        if not decision_id:
            continue
        details = {
            "decision_id": decision_id,
            "title": node.get("title") or node.get("label") or "",
            "category": node.get("category") or "",
            "rows_controlled": node.get("rows_controlled") or [],
            "input_fields": node.get("input_fields") or [],
        }
        for row_number_value in node.get("rows_controlled") or []:
            row_number = _safe_row_number(row_number_value)
            if row_number:
                lookup.setdefault((row_number, ""), details)
    return lookup


def _decision_graph_payload_from_data(data: EstimatorData, *, template_type: str) -> dict[str, Any]:
    tables = data.decision_history_tables if isinstance(data.decision_history_tables, dict) else {}
    if not tables:
        return {}
    nodes = _graph_records_from_tables(
        tables,
        names=("decision_nodes", "template_decision_nodes", f"{template_type}_decision_nodes"),
        template_type=template_type,
    )
    trace = _graph_records_from_tables(
        tables,
        names=("row_traceability", "decision_row_traceability", "template_decision_row_traceability", f"{template_type}_row_traceability"),
        template_type=template_type,
    )
    return {"decision_nodes": nodes, "row_traceability": trace} if nodes or trace else {}


def _graph_records_from_tables(tables: dict[str, Any], *, names: tuple[str, ...], template_type: str) -> list[dict[str, Any]]:
    for name in names:
        frame = tables.get(name)
        if isinstance(frame, pd.DataFrame) and not frame.empty:
            rows = _filter_template_frame(frame, template_type)
            if rows.empty:
                rows = frame
            return rows.fillna("").to_dict(orient="records")
        if isinstance(frame, list):
            records = [row for row in frame if isinstance(row, dict)]
            return [
                row
                for row in records
                if not row.get("template_type") or str(row.get("template_type") or "").lower() == template_type
            ]
    return []


def _decision_graph_payload_from_output(template_type: str) -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "output" / f"template_decision_graph_{template_type}.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    if str(payload.get("template_type") or "").lower() not in {"", template_type}:
        return {}
    return payload


def _merge_decision_menus(primary: list[dict[str, Any]], fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = list(primary)
    seen_keys = {
        (
            str(row.get("template_bucket") or "").lower(),
            str(row.get("workbook_row") or ""),
        )
        for row in merged
    }
    for row in fallback:
        key = (str(row.get("template_bucket") or "").lower(), str(row.get("workbook_row") or ""))
        if key in seen_keys:
            continue
        merged.append(dict(row, source=row.get("source") or "curated_fallback"))
        seen_keys.add(key)
        if len(merged) >= 40:
            break
    return merged


def _decision_id_for_template_row(template_type: str, bucket: str, row_number: str) -> str:
    bucket_key = re.sub(r"[^a-z0-9]+", "_", bucket.lower()).strip("_") or "decision"
    return f"{template_type}_{bucket_key}_row_{row_number}"


def _section_for_template_row(template_type: str, line_item_kind: str, bucket: str) -> str:
    bucket_key = bucket.lower()
    if bucket_key in {"overhead", "profit"} or line_item_kind == "pricing":
        return "pricing_markup_decisions"
    if template_type == "insulation":
        if bucket_key in {"labor_loading", "labor_traveling", "infrared_scan", "meals_lodging", "labor_meals_lodging"}:
            return "insulation_logistics_expense_template_decisions"
        if line_item_kind == "labor":
            return "insulation_labor_template_decisions"
        if "thermal" in bucket_key:
            return "insulation_thermal_barrier_template_decisions"
        if bucket_key in {"caulk_sealant", "sealant", "detail"}:
            return "insulation_detail_material_template_decisions"
        if bucket_key in {"foam", "spray_foam"}:
            return "insulation_foam_template_decisions"
        if line_item_kind in {"equipment", "adder"}:
            return "insulation_equipment_logistics_template_decisions"
        return "insulation_support_material_template_decisions"
    if line_item_kind == "labor":
        return "roofing_labor_template_decisions"
    if bucket_key == "coating":
        return "roofing_coating_template_decisions"
    if bucket_key == "primer":
        return "roofing_primer_template_decisions"
    if bucket_key in {"caulk_detail", "fabric", "sealant", "reinforcement"}:
        return "roofing_detail_template_decisions"
    if bucket_key in {"board_stock", "fasteners", "plates"}:
        return "roofing_board_fastener_template_decisions"
    if line_item_kind in {"equipment", "adder"}:
        return "roofing_equipment_template_decisions"
    return "roofing_accessory_template_decisions"


def _label_from_bucket(bucket: str) -> str:
    return _clean_string(bucket.replace("_", " ").title())


def _editable_fields_from_template_metadata(
    *,
    line_item_kind: str,
    bucket: str,
    formula_model: str,
    roles: Any,
    dependencies: Any,
) -> list[str]:
    fields = ["include"]
    role_values = _role_values(roles)
    fields.extend(_field_names_from_roles(role_values))
    fields.extend(_field_names_from_formula_model(formula_model, line_item_kind=line_item_kind, bucket=bucket))
    fields.extend(_field_names_from_dependencies(dependencies, roles))
    return _dedupe_fields(fields)[:12]


def _formula_requirements_from_template_metadata(
    *,
    line_item_kind: str,
    bucket: str,
    formula_model: str,
    roles: Any,
    dependencies: Any,
) -> list[str]:
    fields = _field_names_from_formula_model(formula_model, line_item_kind=line_item_kind, bucket=bucket)
    fields.extend(_field_names_from_dependencies(dependencies, roles))
    return [field for field in _dedupe_fields(fields) if field != "include"][:10]


def _role_values(roles: Any) -> list[str]:
    if isinstance(roles, dict):
        return [_clean_string(value) for value in roles.values() if _clean_string(value)]
    if isinstance(roles, list):
        values: list[str] = []
        for item in roles:
            if isinstance(item, dict):
                values.extend(_clean_string(value) for value in item.values() if _clean_string(value))
            elif _clean_string(item):
                values.append(_clean_string(item))
        return values
    return []


def _field_names_from_roles(role_values: list[str]) -> list[str]:
    fields: list[str] = []
    for role in role_values:
        normalized = role.lower()
        if "selector" in normalized:
            fields.append("selector_code")
        elif "product" in normalized or "item" in normalized:
            fields.append("selected_pricing_candidate")
        elif "unit price" in normalized or "cost" in normalized or "rate" in normalized:
            fields.append("unit_price")
        elif "quantity" in normalized or normalized in {"qty", "units"}:
            fields.append("estimated_units")
        elif "area" in normalized or "sqft" in normalized:
            fields.append("basis_sqft")
        elif "hour" in normalized:
            fields.append("total_hours")
        elif "day" in normalized:
            fields.append("days")
    return fields


def _field_names_from_formula_model(formula_model: str, *, line_item_kind: str, bucket: str) -> list[str]:
    text = f"{formula_model} {line_item_kind} {bucket}".lower()
    fields: list[str] = []
    if "foam" in text or "thickness" in text:
        fields.extend(["basis_sqft", "thickness_inches", "yield_or_coverage", "unit_price"])
    if "coating" in text or "gallon" in text:
        fields.extend(["basis_sqft", "gal_per_100_sqft", "unit_price"])
    if "primer" in text or "coverage" in text:
        fields.extend(["basis_sqft", "coverage_sqft_per_unit", "unit_price"])
    if "linear" in text or "sealant" in text or "caulk" in text:
        fields.extend(["linear_ft", "feet_per_unit", "unit_price"])
    if "labor" in text or "daily" in text or "hours" in text:
        fields.extend(["days", "crew_size", "daily_rate", "hourly_rate", "total_hours"])
    if "markup" in text or bucket.lower() in {"overhead", "profit"}:
        fields.extend(["markup_pct", "percentage"])
    if not fields:
        if line_item_kind == "labor":
            fields.extend(["days", "crew_size", "daily_rate", "hourly_rate", "total_hours"])
        elif line_item_kind in {"material", "equipment", "adder"}:
            fields.extend(["estimated_units", "unit_price"])
    return fields


def _field_names_from_dependencies(dependencies: Any, roles: Any) -> list[str]:
    if not dependencies:
        return []
    role_map = roles if isinstance(roles, dict) else {}
    fields: list[str] = []
    deps = dependencies if isinstance(dependencies, list) else [dependencies]
    for dep in deps:
        dep_text = _clean_string(dep)
        cell_column = re.match(r"([A-Za-z]+)", dep_text)
        if cell_column and role_map:
            role_value = role_map.get(cell_column.group(1).upper()) or role_map.get(cell_column.group(1))
            fields.extend(_field_names_from_roles([_clean_string(role_value)]))
    return fields


def _dedupe_fields(fields: list[str]) -> list[str]:
    deduped: list[str] = []
    for field in fields:
        field = _clean_string(field)
        if field and field not in deduped:
            deduped.append(field)
    return deduped


def _template_type_for_scope(scope: dict[str, Any]) -> str:
    text = " ".join(
        str(scope.get(key) or "")
        for key in ("template_type", "division", "project_type", "raw_input_notes", "notes", "foam_type", "coating_type")
    ).lower()
    if "insulation" in text or "foam" in text and "roof" not in text:
        return "insulation"
    return "roofing"


def _historical_decision_evidence(data: EstimatorData, *, template_type: str) -> list[dict[str, Any]]:
    frame = data.estimator_decision_recommendations
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    rows = _filter_template_frame(frame, template_type)
    if rows.empty:
        rows = frame
    preferred = [
        "template_type",
        "section",
        "template_bucket",
        "decision_id",
        "field_name",
        "decision_value",
        "recommended_value",
        "resolved_item_name",
        "selector_code",
        "evidence_count",
        "source_jobs_count",
        "source_jobs",
        "confidence",
        "history_table",
    ]
    result = _context_records(_sort_by_evidence(rows), preferred, limit=30)
    return result


def _pricing_candidates_by_bucket(data: EstimatorData, *, template_type: str) -> list[dict[str, Any]]:
    frames: list[tuple[str, pd.DataFrame]] = [
        ("template_product_options", data.template_product_options),
        ("pricing_catalog", data.pricing_catalog if not data.pricing_catalog.empty else data.pricing),
        ("template_rows", data.template_rows),
    ]
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for source_name, frame in frames:
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            continue
        filtered = _filter_template_frame(frame, template_type)
        if filtered.empty:
            filtered = frame
        for row in filtered.head(120).fillna("").to_dict(orient="records"):
            bucket = _clean_string(
                row.get("template_bucket")
                or row.get("material_package")
                or row.get("category")
                or row.get("product_type")
                or row.get("line_item_kind")
            )
            name = _clean_string(
                row.get("product_name")
                or row.get("item_name")
                or row.get("selected_item_name")
                or row.get("current_item")
                or row.get("description")
                or row.get("row_label")
            )
            if not bucket or not name:
                continue
            key = (bucket.lower(), name.lower(), source_name)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "template_bucket": bucket,
                    "candidate_name": name,
                    "unit": _clean_string(row.get("unit") or row.get("uom")),
                    "unit_price": _safe_number_or_blank(
                        row.get("unit_price"),
                        row.get("current_unit_price"),
                        row.get("current_price"),
                        row.get("price"),
                    ),
                    "yield_or_coverage": _safe_number_or_blank(
                        row.get("yield_or_coverage"),
                        row.get("coverage_sqft_per_unit"),
                        row.get("yield"),
                    ),
                    "source": source_name,
                }
            )
            if len(candidates) >= 40:
                return candidates
    return candidates


def _product_guidance_digest(data: EstimatorData, *, template_type: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    product_frame = data.product_catalog
    if isinstance(product_frame, pd.DataFrame) and not product_frame.empty:
        for row in product_frame.head(40).fillna("").to_dict(orient="records"):
            name = _clean_string(row.get("product_name") or row.get("name"))
            if not name:
                continue
            text = " ".join(str(row.get(key) or "") for key in ("category", "product_type", "recommended_use", "description")).lower()
            if template_type == "insulation" and not any(term in text or term in name.lower() for term in ("foam", "thermal", "barrier", "dc315", "insulation")):
                continue
            if template_type == "roofing" and not any(term in text or term in name.lower() for term in ("roof", "coating", "primer", "sealant", "fabric")):
                continue
            rows.append(
                {
                    "product_id": _clean_string(row.get("product_id")),
                    "manufacturer": _clean_string(row.get("manufacturer") or row.get("vendor")),
                    "product_name": name,
                    "category": _clean_string(row.get("category") or row.get("product_type")),
                    "guidance": _clean_string(row.get("recommended_use") or row.get("description") or row.get("notes")),
                }
            )
            if len(rows) >= 20:
                return rows
    property_frame = data.product_properties
    if isinstance(property_frame, pd.DataFrame) and not property_frame.empty and len(rows) < 20:
        for row in property_frame.head(40).fillna("").to_dict(orient="records"):
            product_name = _clean_string(row.get("product_name") or row.get("product_id"))
            property_name = _clean_string(row.get("property_name") or row.get("name") or row.get("key"))
            value = _clean_string(row.get("property_value") or row.get("value"))
            if product_name and property_name and value:
                rows.append({"product_name": product_name, "property": property_name, "value": value, "source": "product_properties"})
            if len(rows) >= 20:
                break
    return rows


def _companion_relationships(data: EstimatorData, *, template_type: str) -> list[dict[str, Any]]:
    frame = data.relationship_package_cooccurrence
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    rows = _filter_template_frame(frame, template_type)
    if rows.empty:
        rows = frame
    preferred = [
        "template_type",
        "source_package",
        "source_template_bucket",
        "package",
        "companion_package",
        "target_package",
        "template_bucket",
        "companion_template_bucket",
        "cooccurrence_rate",
        "lift",
        "evidence_count",
        "source_jobs_count",
    ]
    return _context_records(_sort_by_evidence(rows), preferred, limit=25)


def _reference_job_decisions(data: EstimatorData, *, scope: dict[str, Any], template_type: str) -> list[dict[str, Any]]:
    reference_ids = scope.get("reference_job_ids") or []
    if isinstance(reference_ids, str):
        reference_ids = [reference_ids]
    reference_ids = [str(value).strip() for value in reference_ids if str(value).strip()]
    if not reference_ids:
        return []
    frame = data.template_rows
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    rows = _filter_template_frame(frame, template_type)
    if rows.empty:
        rows = frame
    id_columns = [column for column in ("job_id", "document_id", "estimate_id", "source_job_id") if column in rows.columns]
    if id_columns:
        mask = pd.Series(False, index=rows.index)
        for column in id_columns:
            mask = mask | rows[column].fillna("").astype(str).isin(reference_ids)
        rows = rows[mask]
    preferred = [
        "job_id",
        "document_id",
        "template_type",
        "section",
        "template_bucket",
        "row_number",
        "selected_item_name",
        "resolved_template_option",
        "selector_code",
        "estimated_units",
        "estimated_cost",
        "total_hours",
        "days",
        "crew_size",
    ]
    return _context_records(rows, preferred, limit=35)


def _historical_context_decision_guidance(
    historical_context: dict[str, Any],
    decision_menu: list[dict[str, Any]],
    *,
    template_type: str,
    limit: int = 18,
) -> list[dict[str, Any]]:
    if not isinstance(historical_context, dict) or not decision_menu:
        return []
    package_support: dict[str, dict[str, Any]] = {}
    for profile in historical_context.get("matched_profiles") or []:
        if not isinstance(profile, dict):
            continue
        job_label = _clean_string(profile.get("job_name") or profile.get("customer") or profile.get("job_id"))
        similarity = _safe_number_or_blank(profile.get("similarity_score"))
        for package in profile.get("material_packages") or []:
            bucket = _historical_package_to_decision_bucket(package, template_type=template_type)
            if not bucket:
                continue
            entry = package_support.setdefault(
                bucket,
                {
                    "matched_jobs": [],
                    "support_count": 0,
                    "profile_conditions": set(),
                    "source": "matched_historical_job_profiles",
                },
            )
            entry["support_count"] += 1
            if job_label:
                label = f"{job_label} ({similarity})" if similarity != "" else job_label
                if label not in entry["matched_jobs"]:
                    entry["matched_jobs"].append(label)
            condition = " + ".join(
                part
                for part in (
                    _clean_string(profile.get("project_class")),
                    _clean_string(profile.get("substrate")),
                    _clean_string(profile.get("market_segment")),
                )
                if part and part != "unknown"
            )
            if condition:
                entry["profile_conditions"].add(condition)
    for prior in historical_context.get("aggregate_priors") or []:
        if not isinstance(prior, dict):
            continue
        evidence_count = _safe_numeric(prior.get("evidence_count"), 0.0)
        for package in prior.get("normally_included") or []:
            bucket = _historical_package_to_decision_bucket(package, template_type=template_type)
            if not bucket:
                continue
            entry = package_support.setdefault(
                bucket,
                {
                    "matched_jobs": [],
                    "support_count": 0,
                    "profile_conditions": set(),
                    "source": "aggregate_historical_profile_priors",
                },
            )
            entry["support_count"] += int(evidence_count or 1)
            condition = _clean_string(prior.get("condition"))
            if condition:
                entry["profile_conditions"].add(condition)
    guidance: list[dict[str, Any]] = []
    seen_decisions: set[str] = set()
    for menu_row in decision_menu:
        bucket = _clean_string(menu_row.get("template_bucket")).lower().replace(" ", "_").replace("-", "_")
        support = package_support.get(bucket)
        if not support:
            continue
        decision_id = _clean_string(menu_row.get("decision_id"))
        if not decision_id or decision_id in seen_decisions:
            continue
        seen_decisions.add(decision_id)
        guidance.append(
            {
                "decision_id": decision_id,
                "template_bucket": bucket,
                "workbook_row": _safe_row_number(menu_row.get("workbook_row")),
                "label": _clean_string(menu_row.get("label")),
                "historical_support_count": support["support_count"],
                "matched_jobs": support["matched_jobs"][:5],
                "profile_conditions": sorted(support["profile_conditions"])[:5],
                "recommended_action": "consider_include_when_current_scope_matches",
                "review_rule": "If included mainly from historical context, set review_required true and explain the matched-job evidence.",
                "formula_requirements": menu_row.get("formula_requirements") or [],
            }
        )
        if len(guidance) >= limit:
            break
    return guidance


def _historical_package_to_decision_bucket(package: Any, *, template_type: str) -> str:
    token = _clean_string(package).lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "roofing_foam": "foam",
        "thermal_barrier": "thermal_barrier_coating",
        "thermal_barrier_coating": "thermal_barrier_coating",
        "caulk_sealant": "caulk_detail" if template_type == "roofing" else "caulk_sealant",
        "caulk_detail": "caulk_detail" if template_type == "roofing" else "caulk_sealant",
        "sales_inspection_trips": "sales_trips",
        "sales_inspect": "sales_trips",
        "sales_inspection": "sales_trips",
        "labor_coating": "labor_top_coat",
        "labor_foam": "labor_foam" if template_type == "insulation" else "labor_base",
    }
    return aliases.get(token, token)


def _safe_numeric(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number else default


def _filter_template_frame(frame: pd.DataFrame, template_type: str) -> pd.DataFrame:
    if "template_type" not in frame.columns:
        return frame
    mask = frame["template_type"].fillna("").astype(str).str.lower().eq(template_type)
    return frame[mask]


def _sort_by_evidence(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    sorted_frame = frame.copy()
    sort_columns: list[str] = []
    for column in ("evidence_count", "source_jobs_count", "row_count", "support_count"):
        if column in sorted_frame.columns:
            sorted_frame[column] = pd.to_numeric(sorted_frame[column], errors="coerce").fillna(0)
            sort_columns.append(column)
    if sort_columns:
        sorted_frame = sorted_frame.sort_values(sort_columns, ascending=False)
    return sorted_frame


def _safe_number_or_blank(*values: Any) -> float | str:
    for value in values:
        if value in (None, ""):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number == number:
            return round(number, 6)
    return ""


def _safe_row_number(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _clean_string(value)
    if number != number:
        return ""
    return str(int(number)) if number.is_integer() else str(number)


def _json_payload(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value in (None, ""):
        return {}
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def normalize_chat_payload(
    payload: dict[str, Any],
    *,
    source: str,
    baseline_scope: dict[str, Any] | None = None,
    baseline_notes: str = "",
) -> EstimatorChatResult:
    scope = payload.get("scope_overrides") if isinstance(payload.get("scope_overrides"), dict) else {}
    notes = _clean_string(payload.get("estimator_notes") or payload.get("filled_estimator_notes"))
    assistant_message = _clean_string(payload.get("assistant_message") or payload.get("summary"))
    if not notes:
        notes = assistant_message
    notes = _merge_chat_notes(baseline_notes, notes)
    if not assistant_message:
        assistant_message = notes or "I drafted estimator notes from the conversation."
    cleaned_scope = _clean_scope(scope)
    template_type = _clean_string(
        cleaned_scope.get("template_type")
        or (baseline_scope or {}).get("template_type")
        or (baseline_scope or {}).get("division")
    ).lower()
    decision_preferences = _clean_decision_preferences(
        payload.get("workbook_decision_preferences")
        or payload.get("decision_patches")
        or payload.get("row_updates")
        or payload.get("workbook_row_updates"),
        template_type=template_type,
    )
    return EstimatorChatResult(
        assistant_message=assistant_message,
        estimator_notes=notes,
        scope_overrides=_merge_chat_scopes(baseline_scope or {}, cleaned_scope),
        workbook_decision_preferences=decision_preferences,
        missing_questions=_clean_list(payload.get("missing_questions")),
        assumptions=_clean_list(payload.get("assumptions")),
        confidence=_bounded_confidence(payload.get("confidence")),
        source=source,
        raw_response=payload,
        warnings=_clean_list(payload.get("warnings")),
    )


def _merge_reference_template_summary(
    result: EstimatorChatResult,
    reference_summary: ParsedReferenceTemplateSummary,
    *,
    template_type_hint: str = "",
) -> EstimatorChatResult:
    if not reference_summary.mapped_row_count:
        return result
    merged_preferences = _merge_decision_preferences(
        result.workbook_decision_preferences,
        reference_summary.workbook_decision_preferences,
    )
    reference_area = _basis_sqft_from_decision_preferences(merged_preferences)
    template_type = "roofing" if _clean_string(template_type_hint).lower() == "roofing" else ""
    if not template_type:
        template_type = "roofing"
    scope_payload = {
        **_clean_scope(result.scope_overrides),
        "template_type": template_type,
        "division": "Roofing" if template_type == "roofing" else template_type.title(),
        "project_type": "roofing estimate" if template_type == "roofing" else template_type,
        "reference_template_summary_present": True,
        "reference_template_summary_row_count": reference_summary.row_count,
        "reference_template_summary_mapped_row_count": reference_summary.mapped_row_count,
    }
    if reference_area > 0:
        scope_payload.setdefault("estimated_sqft", reference_area)
        scope_payload.setdefault("net_sqft", reference_area)
        scope_payload.setdefault("basis_sqft", reference_area)
    scope = _clean_scope(scope_payload)
    warnings = list(result.warnings)
    for warning in reference_summary.warnings:
        if warning not in warnings:
            warnings.append(warning)
    summary_line = (
        f"Mapped {reference_summary.mapped_row_count} pasted template-summary rows to current "
        f"{template_type or 'estimating'} workbook decisions for review."
    )
    assistant_message = result.assistant_message
    if summary_line not in assistant_message:
        assistant_message = _clean_string(f"{assistant_message}\n\n{summary_line}")
    return EstimatorChatResult(
        assistant_message=assistant_message,
        estimator_notes=result.estimator_notes,
        scope_overrides=scope,
        workbook_decision_preferences=merged_preferences,
        missing_questions=result.missing_questions,
        assumptions=result.assumptions,
        confidence=max(result.confidence, 0.72),
        source=result.source,
        raw_response=result.raw_response,
        warnings=warnings,
    )


def _basis_sqft_from_decision_preferences(preferences: list[dict[str, Any]]) -> float:
    candidates: list[float] = []
    for preference in preferences or []:
        if not isinstance(preference, dict):
            continue
        values = preference.get("proposed_values") if isinstance(preference.get("proposed_values"), dict) else {}
        bucket = _clean_string(preference.get("template_bucket")).lower()
        if bucket not in {"foam", "coating", "primer", "board_stock", "granules", "thermal_barrier_coating"}:
            continue
        area = _safe_positive_number(values.get("basis_sqft") or values.get("area_sqft"))
        if area > 0:
            candidates.append(area)
    return max(candidates) if candidates else 0.0


def _merge_decision_preferences(
    existing: list[dict[str, Any]],
    overrides: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    order: list[tuple[str, str, str, str]] = []
    for preference in list(existing or []) + list(overrides or []):
        if not isinstance(preference, dict):
            continue
        key = (
            _clean_string(preference.get("section")),
            _clean_string(preference.get("template_bucket")),
            _safe_row_number(preference.get("workbook_row")),
            _clean_string(preference.get("decision_id")),
        )
        if key not in merged:
            merged[key] = dict(preference)
            order.append(key)
            continue
        base = merged[key]
        proposed_values = dict(base.get("proposed_values") or {})
        proposed_values.update(dict(preference.get("proposed_values") or {}))
        evidence = list(base.get("evidence") or [])
        for item in preference.get("evidence") or []:
            if item not in evidence:
                evidence.append(item)
        review_reasons = list(base.get("review_reasons") or [])
        for reason in preference.get("review_reasons") or []:
            if reason not in review_reasons:
                review_reasons.append(reason)
        base.update(preference)
        base["proposed_values"] = proposed_values
        if evidence:
            base["evidence"] = evidence
        if review_reasons:
            base["review_reasons"] = review_reasons
    return [merged[key] for key in order]


def _parse_reference_template_summary_from_messages(
    messages: Iterable[dict[str, str]],
    *,
    template_type_hint: str = "",
) -> ParsedReferenceTemplateSummary:
    user_text = "\n".join(str(message.get("content") or "") for message in messages if message.get("role") == "user")
    return _parse_reference_template_summary(user_text, template_type_hint=template_type_hint)


def _parse_reference_answer_key_from_messages(
    messages: Iterable[dict[str, str]],
) -> ParsedReferenceTemplateSummary:
    user_text = "\n".join(str(message.get("content") or "") for message in messages if message.get("role") == "user")
    answer_key = parse_reference_answer_key_text(user_text)
    if not answer_key:
        return ParsedReferenceTemplateSummary()
    preferences = answer_key_to_workbook_decision_preferences(answer_key)
    return ParsedReferenceTemplateSummary(
        workbook_decision_preferences=preferences,
        warnings=[],
        row_count=int((answer_key.get("summary") or {}).get("source_row_count") or len(answer_key.get("decisions") or [])),
        mapped_row_count=len(preferences),
    )


def _parse_reference_template_summary(text: str, *, template_type_hint: str = "") -> ParsedReferenceTemplateSummary:
    if not _looks_like_reference_template_summary(text):
        return ParsedReferenceTemplateSummary()
    table_rows = _reference_summary_table_rows(text)
    if not table_rows:
        return ParsedReferenceTemplateSummary()
    truck_trip_count = _reference_summary_truck_trip_count(table_rows)
    preferences: list[dict[str, Any]] = []
    warnings: list[str] = []
    for table_row in table_rows:
        mapped = _reference_summary_row_to_preference(table_row, truck_trip_count=truck_trip_count)
        if mapped:
            preferences.append(mapped)
            continue
        if _clean_string(table_row.get("line_item")):
            warnings.append(
                "Pasted template row was not mapped to a current decision row: "
                f"source row {table_row.get('source_row') or '?'} {_clean_string(table_row.get('line_item'))}."
            )
    cleaned = _clean_decision_preferences(preferences, template_type=template_type_hint)
    return ParsedReferenceTemplateSummary(
        workbook_decision_preferences=cleaned,
        warnings=warnings[:12],
        row_count=len(table_rows),
        mapped_row_count=len(cleaned),
    )


def _looks_like_reference_template_summary(text: str) -> bool:
    lowered = str(text or "").lower()
    if "source row" in lowered and "line item" in lowered:
        return True
    row_markers = len(re.findall(r"(?:^|\n)\s*(?:materials?|labor|labor / subcontractor|additional amount)", lowered))
    if row_markers >= 3 and any(term in lowered for term in ("unit price", "estimated cost", "basis / units")):
        return True
    compact_markers = len(
        re.findall(
            r"\b(?:materials?|tax|labor\s*/\s*subcontractor|warranty\s*/\s*insurance|markup\s*/\s*add-ons|add-ons\s+w/o\s+markup)\s+\d{2,3}\b",
            lowered,
        )
    )
    return compact_markers >= 3 and any(term in lowered for term in ("estimated", "reference", "human estimated", "answer key"))


def _reference_summary_table_rows(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in str(text or "").splitlines():
        columns = _split_reference_summary_line(line)
        if len(columns) < 5:
            continue
        if _reference_summary_is_header(columns):
            continue
        section = _clean_string(columns[0])
        source_row = _safe_row_number(columns[1] if len(columns) > 1 else "")
        if not section or not source_row:
            continue
        line_item = _clean_string(columns[2] if len(columns) > 2 else "")
        if not line_item:
            continue
        row = {
            "section": section,
            "source_row": source_row,
            "line_item": line_item,
            "basis_units": _clean_string(columns[3] if len(columns) > 3 else ""),
            "unit_price_rate": _clean_string(columns[4] if len(columns) > 4 else ""),
            "estimated_cost": _clean_string(columns[5] if len(columns) > 5 else ""),
            "notes": _clean_string(" ".join(columns[6:]) if len(columns) > 6 else ""),
        }
        rows.append(row)
    if rows:
        return rows
    return _reference_summary_compact_rows(text)


REFERENCE_SUMMARY_COMPACT_SECTIONS = (
    "Labor / Subcontractor",
    "Warranty / Insurance",
    "Markup / Add-ons",
    "Add-ons w/o Markup",
    "Materials Tax",
    "Additional Amount w/o Markup",
    "Materials",
    "Material",
    "Tax",
)


def _reference_summary_compact_rows(text: str) -> list[dict[str, Any]]:
    compact = _clean_string(text)
    if not compact:
        return []
    section_pattern = "|".join(re.escape(section) for section in REFERENCE_SUMMARY_COMPACT_SECTIONS)
    matches = list(re.finditer(rf"\b(?P<section>{section_pattern})\s+(?P<source_row>\d{{2,3}})\s+", compact, re.I))
    rows: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(compact)
        segment = compact[match.end() : end].strip()
        row = _parse_reference_summary_compact_segment(
            section=_clean_string(match.group("section")),
            source_row=_safe_row_number(match.group("source_row")),
            segment=segment,
        )
        if row:
            rows.append(row)
    return rows


def _parse_reference_summary_compact_segment(*, section: str, source_row: str, segment: str) -> dict[str, Any] | None:
    if not section or not source_row or not segment:
        return None
    money_matches = list(re.finditer(r"\$\s*-?\d(?:[\d,]*)(?:\.\d+)?", segment))
    cost_match = money_matches[-1] if money_matches else None
    estimated_cost = cost_match.group(0) if cost_match else ""
    prefix = segment[: cost_match.start()].strip() if cost_match else segment
    notes = segment[cost_match.end() :].strip() if cost_match else ""
    rate = ""
    if len(money_matches) >= 2:
        rate_match = money_matches[-2]
        rate = rate_match.group(0)
        prefix = segment[: rate_match.start()].strip()
    elif cost_match:
        percent_matches = list(re.finditer(r"\d+(?:\.\d+)?\s*%", prefix))
        if percent_matches:
            rate_match = percent_matches[-1]
            rate = rate_match.group(0)
            prefix = prefix[: rate_match.start()].strip()
    line_item, basis_units = _split_reference_compact_item_and_basis(prefix)
    if not line_item:
        return None
    return {
        "section": section,
        "source_row": source_row,
        "line_item": line_item,
        "basis_units": basis_units,
        "unit_price_rate": rate,
        "estimated_cost": estimated_cost,
        "notes": notes,
    }


def _split_reference_compact_item_and_basis(prefix: str) -> tuple[str, str]:
    prefix = _clean_string(prefix)
    if not prefix:
        return "", ""
    basis_match = re.search(
        r"\b(?:\d(?:[\d,]*)(?:\.\d+)?\s*(?:sq\s*ft|est\.\s*units?|units?|trips?|est\.\s*days?|days?|hours?|hrs?|hr/day|hrs/day|years?|miles?)|additional amount|discount|allowance|\d+(?:\.\d+)?\s*%\s+of)\b",
        prefix,
        re.I,
    )
    if not basis_match:
        return prefix, ""
    line_item = prefix[: basis_match.start()].strip()
    basis = prefix[basis_match.start() :].strip()
    return line_item, basis


def _split_reference_summary_line(line: str) -> list[str]:
    stripped = str(line or "").strip()
    if not stripped:
        return []
    if re.fullmatch(r"[-:|\s]+", stripped):
        return []
    if "\t" in stripped:
        return [_clean_string(part) for part in stripped.split("\t")]
    if "|" in stripped:
        parts = [part for part in stripped.strip("|").split("|")]
        return [_clean_string(part) for part in parts]
    return [_clean_string(part) for part in re.split(r"\s{2,}", stripped)]


def _reference_summary_is_header(columns: list[str]) -> bool:
    normalized = " ".join(_clean_string(column).lower() for column in columns[:4])
    return "source row" in normalized and "line item" in normalized


def _reference_summary_truck_trip_count(table_rows: list[dict[str, Any]]) -> float | None:
    for row in table_rows:
        line_item = _clean_string(row.get("line_item")).lower()
        if "truck" not in line_item:
            continue
        values = _reference_summary_values(row)
        trip_count = values.get("trip_count")
        if isinstance(trip_count, (int, float)) and trip_count > 0:
            return float(trip_count)
    return None


def _reference_summary_row_to_preference(row: dict[str, Any], *, truck_trip_count: float | None = None) -> dict[str, Any] | None:
    target = _reference_summary_target(row)
    if not target:
        return None
    values = _reference_summary_values(row)
    if target["section"] == "pricing_markup_decisions":
        pct = values.get("markup_pct") or values.get("percentage") or values.get("unit_price")
        if pct not in (None, ""):
            values["markup_pct"] = pct
            values["percentage"] = pct
            if target["template_bucket"] == "overhead":
                values["overhead_pct"] = pct
            elif target["template_bucket"] == "profit":
                values["profit_pct"] = pct
        for stale_key in ("unit_price", "estimated_units"):
            values.pop(stale_key, None)
    if target["section"] == "roofing_labor_template_decisions":
        if values.get("people_count") not in (None, "") and values.get("crew_size") in (None, ""):
            values["crew_size"] = values["people_count"]
        if values.get("unit_price") not in (None, "") and values.get("daily_rate") in (None, ""):
            values["daily_rate"] = values["unit_price"]
        if values.get("total_hours") in (None, "") and values.get("hours_per_day") not in (None, ""):
            values["total_hours"] = values.get("hours_per_day")
        values.pop("people_count", None)
        values.pop("unit_price", None)
        values.pop("hours_per_day", None)
    if target["template_bucket"] in {"labor_loading", "labor_traveling"} and "trip_count" not in values:
        if truck_trip_count and "truck trip" in _clean_string(row.get("notes")).lower():
            values["trip_count"] = truck_trip_count
        elif truck_trip_count and "multiplied by" in _clean_string(row.get("notes")).lower():
            values["trip_count"] = truck_trip_count
    evidence = [
        {
            "source": REFERENCE_TEMPLATE_SOURCE,
            "source_row": row.get("source_row"),
            "section": row.get("section"),
            "line_item": row.get("line_item"),
            "basis_units": row.get("basis_units"),
            "unit_price_rate": row.get("unit_price_rate"),
            "estimated_cost": row.get("estimated_cost"),
            "notes": row.get("notes"),
        }
    ]
    source_row = _safe_row_number(row.get("source_row"))
    workbook_row = _safe_row_number(target["workbook_row"])
    review_reasons = ["Mapped from pasted correct-template summary; verify against the current workbook before export."]
    if source_row and source_row != workbook_row:
        review_reasons.append(f"Source row {source_row} was normalized to current workbook row {workbook_row}.")
    return {
        **target,
        "include": True,
        "proposed_values": values,
        "confidence": 0.86,
        "review_required": True,
        "review_reasons": review_reasons,
        "evidence": evidence,
        "source": REFERENCE_TEMPLATE_SOURCE,
    }


def _reference_summary_target(row: dict[str, Any]) -> dict[str, str] | None:
    source_row = _safe_row_number(row.get("source_row"))
    line_item = _clean_string(row.get("line_item")).lower()
    section = _clean_string(row.get("section")).lower()
    is_labor_section = "labor" in section
    if "warranty" in section:
        return _reference_summary_free_adder_target(source_row, line_item, template_bucket="warranty")
    if "add-ons w/o markup" in section or "additional amount" in section:
        return _reference_summary_free_adder_target(source_row, line_item)
    if "materials tax" in section or section == "tax" or "sales tax" in line_item:
        return _reference_summary_free_adder_target(source_row, line_item, template_bucket="sales_tax")
    if "markup" in section or line_item in {"estimated o/h", "estimated oh", "overhead", "o/h"} or line_item == "profit":
        bucket = "profit" if "profit" in line_item else "overhead"
        return {
            "decision_id": f"pricing_{bucket}",
            "section": "pricing_markup_decisions",
            "template_bucket": bucket,
            "workbook_row": "167" if bucket == "profit" else "165",
        }
    if is_labor_section:
        return _reference_summary_labor_target(source_row, line_item)
    if source_row in {"26", "27", "28"} or any(term in line_item for term in ("silicone", "coating", "acrylic")):
        if "sausage" not in line_item and "sf-2000" not in line_item and "seal" not in line_item:
            row_number = source_row if source_row in {"26", "27", "28"} else "26"
            return {
                "decision_id": f"roofing_coating_system_row_{row_number}",
                "section": "roofing_coating_template_decisions",
                "template_bucket": "coating",
                "workbook_row": row_number,
            }
    if source_row in {"19", "20", "21"} or any(term in line_item for term in ("roof foam", "roof 2.7", "spf", "foam")):
        row_number = source_row if source_row in {"19", "20", "21"} else "19"
        return {
            "decision_id": f"roofing_foam_row_{row_number}",
            "section": "roofing_foam_template_decisions",
            "template_bucket": "foam",
            "workbook_row": row_number,
        }
    if source_row == "39" or "primer" in line_item or "e-5320" in line_item or "e5320" in line_item:
        return {
            "decision_id": "roofing_primer_system_row_39",
            "section": "roofing_primer_template_decisions",
            "template_bucket": "primer",
            "workbook_row": "39",
        }
    if source_row == "36" or "granule" in line_item:
        return {
            "decision_id": "roofing_granules_row_36",
            "section": "roofing_granules_template_decisions",
            "template_bucket": "granules",
            "workbook_row": "36",
        }
    if source_row in {"43", "45"} or any(term in line_item for term in ("sausage", "sf-2000", "caulk", "sealant", "mastic", "flashing")):
        row_number = source_row if source_row in {"43", "45"} else "43"
        return {
            "decision_id": f"roofing_caulk_sealant_row_{row_number}",
            "section": "roofing_detail_template_decisions",
            "template_bucket": "caulk_detail",
            "workbook_row": row_number,
        }
    if source_row == "63" or "fastener" in line_item:
        return {
            "decision_id": "roofing_fasteners_row_63",
            "section": "roofing_board_fastener_template_decisions",
            "template_bucket": "fasteners",
            "workbook_row": "63",
        }
    if source_row == "65" or "plate" in line_item:
        return {
            "decision_id": "roofing_plates_row_65",
            "section": "roofing_board_fastener_template_decisions",
            "template_bucket": "plates",
            "workbook_row": "65",
        }
    if "dumpster" in line_item or "disposal" in line_item:
        return {
            "decision_id": "roofing_dumpsters_row_69",
            "section": "roofing_equipment_template_decisions",
            "template_bucket": "dumpster",
            "workbook_row": "69",
        }
    if "generator" in line_item:
        return {
            "decision_id": "roofing_generator_row_99",
            "section": "roofing_equipment_template_decisions",
            "template_bucket": "generator",
            "workbook_row": "99",
        }
    if source_row == "106" or "sales" in line_item or "inspect" in line_item:
        return {
            "decision_id": "roofing_sales_trips_row_106",
            "section": "roofing_travel_freight_template_decisions",
            "template_bucket": "sales_trips",
            "workbook_row": "106",
        }
    if source_row == "108" or "truck" in line_item:
        return {
            "decision_id": "roofing_truck_expense_row_108",
            "section": "roofing_travel_freight_template_decisions",
            "template_bucket": "truck_expense",
            "workbook_row": "108",
        }
    if "loading" in line_item:
        return {
            "decision_id": "roofing_labor_loading_row_136",
            "section": "roofing_logistics_expense_template_decisions",
            "template_bucket": "labor_loading",
            "workbook_row": "136",
        }
    if "travel" in line_item:
        return {
            "decision_id": "roofing_labor_traveling_row_138",
            "section": "roofing_logistics_expense_template_decisions",
            "template_bucket": "labor_traveling",
            "workbook_row": "138",
        }
    if "infrared" in line_item or "ir scan" in line_item:
        return {
            "decision_id": "roofing_infrared_scan_row_141",
            "section": "roofing_logistics_expense_template_decisions",
            "template_bucket": "infrared_scan",
            "workbook_row": "141",
        }
    if "meal" in line_item or "lodging" in line_item or "hotel" in line_item:
        return {
            "decision_id": "roofing_meals_lodging_row_144",
            "section": "roofing_logistics_expense_template_decisions",
            "template_bucket": "meals_lodging",
            "workbook_row": "144",
        }
    if "set up" in line_item or "setup" in line_item or "safety" in line_item:
        return {
            "decision_id": "roofing_labor_prep_row_116",
            "section": "roofing_labor_template_decisions",
            "template_bucket": "labor_prep",
            "workbook_row": "116",
        }
    if "tear-out" in line_item or "tear out" in line_item or "foam & base" in line_item or "foam and base" in line_item:
        return {
            "decision_id": "roofing_labor_base_row_122",
            "section": "roofing_labor_template_decisions",
            "template_bucket": "labor_base",
            "workbook_row": "122",
        }
    if "walk" in line_item or "caulk" in line_item:
        return {
            "decision_id": "roofing_labor_seam_sealer_row_120",
            "section": "roofing_labor_template_decisions",
            "template_bucket": "labor_seam_sealer",
            "workbook_row": "120",
        }
    if "top" in line_item or "granule" in line_item:
        return {
            "decision_id": "roofing_labor_top_coat_row_124",
            "section": "roofing_labor_template_decisions",
            "template_bucket": "labor_top_coat",
            "workbook_row": "124",
        }
    if "clean" in line_item or "misc" in line_item:
        return {
            "decision_id": "roofing_labor_cleanup_row_132",
            "section": "roofing_labor_template_decisions",
            "template_bucket": "labor_cleanup",
            "workbook_row": "132",
        }
    return None


def _reference_summary_free_adder_target(source_row: str, line_item: str, *, template_bucket: str | None = None) -> dict[str, str]:
    row_number = source_row or "173"
    normalized_label = re.sub(r"[^a-z0-9]+", "_", line_item).strip("_") or "free_adder"
    bucket = template_bucket or normalized_label
    return {
        "decision_id": f"roofing_free_adder_row_{row_number}_{normalized_label}",
        "section": "roofing_free_adder_template_decisions",
        "template_bucket": bucket,
        "workbook_row": row_number,
    }


def _reference_summary_labor_target(source_row: str, line_item: str) -> dict[str, str] | None:
    if "loading" in line_item:
        return {
            "decision_id": "roofing_labor_loading_row_136",
            "section": "roofing_logistics_expense_template_decisions",
            "template_bucket": "labor_loading",
            "workbook_row": "136",
        }
    if "travel" in line_item:
        return {
            "decision_id": "roofing_labor_traveling_row_138",
            "section": "roofing_logistics_expense_template_decisions",
            "template_bucket": "labor_traveling",
            "workbook_row": "138",
        }
    if "infrared" in line_item or "ir scan" in line_item:
        return {
            "decision_id": "roofing_infrared_scan_row_141",
            "section": "roofing_logistics_expense_template_decisions",
            "template_bucket": "infrared_scan",
            "workbook_row": "141",
        }
    if "meal" in line_item or "lodging" in line_item or "hotel" in line_item:
        return {
            "decision_id": "roofing_meals_lodging_row_144",
            "section": "roofing_logistics_expense_template_decisions",
            "template_bucket": "meals_lodging",
            "workbook_row": "144",
        }
    if (
        source_row == "116"
        or "set up" in line_item
        or "setup" in line_item
        or "safety" in line_item
        or "pwash" in line_item
        or "power wash" in line_item
        or "pressure wash" in line_item
    ):
        return {
            "decision_id": "roofing_labor_prep_row_116",
            "section": "roofing_labor_template_decisions",
            "template_bucket": "labor_prep",
            "workbook_row": "116",
        }
    if source_row == "118" or "tear-out" in line_item or "tear out" in line_item or "foam & base" in line_item or "foam and base" in line_item:
        return {
            "decision_id": "roofing_labor_base_row_122",
            "section": "roofing_labor_template_decisions",
            "template_bucket": "labor_base",
            "workbook_row": "122",
        }
    if source_row == "120" or "prime" in line_item:
        return {
            "decision_id": "roofing_labor_prime_row_118",
            "section": "roofing_labor_template_decisions",
            "template_bucket": "labor_prime",
            "workbook_row": "118",
        }
    if source_row == "122" or "walk" in line_item or "caulk" in line_item or "fastener" in line_item or "sf" in line_item:
        return {
            "decision_id": "roofing_labor_seam_sealer_row_120",
            "section": "roofing_labor_template_decisions",
            "template_bucket": "labor_seam_sealer",
            "workbook_row": "120",
        }
    if source_row == "124" or "top" in line_item or "granule" in line_item:
        return {
            "decision_id": "roofing_labor_top_coat_row_124",
            "section": "roofing_labor_template_decisions",
            "template_bucket": "labor_top_coat",
            "workbook_row": "124",
        }
    if source_row == "130" or "misc" in line_item:
        return {
            "decision_id": "roofing_labor_details_row_128",
            "section": "roofing_labor_template_decisions",
            "template_bucket": "labor_details",
            "workbook_row": "128",
        }
    if source_row == "132" or "clean" in line_item:
        return {
            "decision_id": "roofing_labor_cleanup_row_132",
            "section": "roofing_labor_template_decisions",
            "template_bucket": "labor_cleanup",
            "workbook_row": "132",
        }
    return None


def _reference_summary_values(row: dict[str, Any]) -> dict[str, Any]:
    basis = _clean_string(row.get("basis_units"))
    rate = _clean_string(row.get("unit_price_rate"))
    cost = _clean_string(row.get("estimated_cost"))
    text = " ".join(part for part in (basis, rate, cost, _clean_string(row.get("notes"))) if part)
    values: dict[str, Any] = {}
    for key, pattern in (
        ("basis_sqft", r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:sq\s*ft|sqft|sf)\b"),
        ("estimated_units", r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:est\.\s*)?units?\b"),
        ("thickness_inches", r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:inch(?:es)?|in\b|thickness\b)"),
        ("days", r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:est\.\s*)?days?\b"),
        ("total_hours", r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*hours?\b"),
        ("hours_per_day", r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:hr/day|hrs/day|hours?/day)\b"),
        ("people_count", r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:people|person)\b"),
        ("warranty_years", r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*years?\b"),
    ):
        parsed = _first_number_match(basis, pattern)
        if parsed is not None:
            values[key] = parsed
    trips_miles = re.search(
        r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*trips?\s*(?:x|×|\*)\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*miles?",
        text,
        re.I,
    )
    if trips_miles:
        values["trip_count"] = _parse_reference_number(trips_miles.group(1))
        values["round_trip_miles"] = _parse_reference_number(trips_miles.group(2))
    unit_price = _parse_reference_money(rate)
    if unit_price is not None:
        if "/ 1,000" in rate or "/1,000" in rate or "per 1,000" in rate.lower():
            values["unit_price_per_thousand"] = unit_price
        else:
            values["unit_price"] = unit_price
    else:
        percent = _parse_reference_percent(rate)
        if percent is not None:
            values["markup_pct"] = percent
            values["percentage"] = percent
    if _clean_string(row.get("line_item")).lower() in {"warranty", "misc. miles"} and "unit_price" not in values:
        parsed_cost = _parse_reference_money(cost)
        if parsed_cost is not None:
            values["unit_price"] = parsed_cost
    if "gal" in basis.lower():
        gallons = values.get("estimated_units")
        if gallons in (None, ""):
            gallons = _first_number_match(
                basis,
                r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:est\.\s*)?units?\b",
            )
        if gallons in (None, "") and "@" not in basis:
            gallons = _first_number_match(basis, r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*gal")
        if gallons not in (None, ""):
            values["estimated_units"] = gallons
            values["estimated_gallons"] = gallons
        gal_per = _first_number_match(basis, r"@\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*gal")
        if gal_per is not None:
            values["gal_per_100_sqft"] = gal_per
    row_section = _clean_string(row.get("section")).lower()
    row_line_item = _clean_string(row.get("line_item")).lower()
    if (
        "additional amount" in row_section
        or "add-ons w/o markup" in row_section
        or "warranty" in row_section
        or "sales tax" in row_line_item
    ):
        parsed_cost = _parse_reference_money(cost)
        if parsed_cost is not None:
            values["amount"] = parsed_cost
            values["estimated_cost"] = parsed_cost
            values.setdefault("estimated_units", 1.0)
        values["template_line"] = _clean_string(row.get("line_item"))
        values["markup_treatment"] = "post_markup"
    return {key: value for key, value in values.items() if value not in (None, "", 0)}


def _first_number_match(text: str, pattern: str) -> float | None:
    match = re.search(pattern, str(text or ""), re.I)
    if not match:
        return None
    return _parse_reference_number(match.group(1))


def _parse_reference_money(text: str) -> float | None:
    match = re.search(r"\$?\s*(-?\d+(?:,\d{3})*(?:\.\d+)?)", str(text or ""))
    if not match:
        return None
    return _parse_reference_number(match.group(1))


def _parse_reference_percent(text: str) -> float | None:
    match = re.search(r"(-?\d+(?:,\d{3})*(?:\.\d+)?)\s*%", str(text or ""))
    if not match:
        return None
    return _parse_reference_number(match.group(1))


def _parse_reference_number(value: Any) -> float | None:
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return round(number, 6)


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
    site_address = _parse_site_address(text)
    if site_address:
        scope["site_address"] = site_address
        scope["address"] = site_address
        scope["destination_address"] = site_address

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
    target_r_value = _parse_target_r_value(text)
    if target_r_value:
        scope["target_r_value"] = target_r_value
        if scope.get("outside_walls_included") and scope.get("ceiling_included"):
            scope["insulation_r_value_targets"] = {"walls": target_r_value, "ceiling": target_r_value}
    r_value_per_inch = _parse_r_value_per_inch(text)
    if target_r_value and r_value_per_inch and not scope.get("foam_thickness_inches"):
        scope["r_value_per_inch_assumption"] = r_value_per_inch
        scope["foam_thickness_inches"] = round(target_r_value / r_value_per_inch, 2)
        questions = [question for question in questions if "r-value" not in question.lower() and "thickness" not in question.lower()]
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
        "If estimator_context.estimator_memory_guidance is present, treat those approved correction notes as shared estimator memory: "
        "use them to avoid repeating prior bad assumptions, unless the current user message explicitly says otherwise. "
        "Estimator memory outranks AI inference but does not override current-session user instructions, manual estimator edits, or workbook formulas. "
        "When historical/template context supports a normal choice, make the best reviewed guess instead of leaving the decision blank; "
        "set review_required true, lower confidence, and explain the evidence if the prompt did not explicitly confirm it. "
        "If estimator_context.historical_job_context has matched_profiles or aggregate_priors, use those to judge which historical jobs "
        "are relevant by project class, market segment, building type, substrate, material system, warranty, and area bucket. "
        "If estimator_context.historical_context_decision_guidance is present, it maps those historical profiles to allowed workbook "
        "decision IDs; use it to propose likely included rows when the current scope is similar. "
        "If estimator_context.historical_template_examples has matched_examples, treat them as compact worked examples from prior "
        "estimates: compare the current job to each example, reuse normal decision patterns when the scope matches, and cite the "
        "example in evidence. If a matched example includes reference_answer_key.decisions, those are normalized historical workbook "
        "decisions from the prior estimate; use their decision_id, template_bucket, workbook_row, line_item, inputs, and calculated_outputs "
        "as evidence for similar current decisions. Do not copy example quantities blindly when the current area, thickness, warranty, or substrate differs. "
        "If estimator_context.historical_answer_key_examples has matched_answer_keys, prioritize those over generic examples: they are "
        "the most similar historical estimate answer keys found for this scope. Use match_reasons and reference_answer_key.decisions "
        "as evidence for included rows, product/system choices, labor/logistics patterns, markup/warranty assumptions, and typical "
        "formula inputs. Still scale quantities to the current job and mark review_required when the prompt evidence is incomplete. "
        "Use matched profiles as evidence for normal package inclusion and scope assumptions, but do not invent values that are not "
        "supported by the current prompt, workbook history, product guidance, or estimator memory. "
        "Ask questions only when the answer materially changes scope, safety/code compliance, system selection, warranty eligibility, or price. "
        "If the user gives a command such as remove fabric, use closed cell R-21, make labor 2.5 days, change units, or change price, "
        "treat it as a workbook decision patch and return it in workbook_decision_preferences. "
        "Return strict JSON only with keys: assistant_message, estimator_notes, scope_overrides, workbook_decision_preferences, "
        "missing_questions, assumptions, warnings, confidence. "
        "scope_overrides should use workbook-facing field names such as template_type, division, project_type, foam_type, "
        "foam_thickness_inches, building_footprint_length_ft, building_footprint_width_ft, wall_height_ft, openings, "
        "outside_walls_included, ceiling_included, net_insulation_area_sqft, estimated_sqft, insulation_surface_areas, "
        "insulation_r_value_targets, coating_type, warranty_target_years, substrate, roof_condition, access_complexity, "
        "scope_triggers, and reference_job_ids. "
        "workbook_decision_preferences should be a list of decisions with decision_id, template_bucket, include, proposed_values, "
        "evidence, confidence, and review_required. Include section and workbook_row when known. "
        "Critical calculation rule: if include is true, proposed_values must provide the row's required calculation inputs from "
        "estimator_context.workbook_decision_menu, or cite a specific historical/template value that supplies them. "
        "Do not check a row based only on relationship/history/product evidence when quantity, area, rate, yield, or unit price is missing. "
        "In that case set include false, review_required true, and explain which calculation fields are needed. "
        "Use proposed_values for editable workbook fields such as basis_sqft, thickness_inches, gal_per_100_sqft, unit_price, "
        "estimated_units, linear_ft, days, hours_per_day, people_count, trip_count, crew_size, daily_rate, hourly_rate, "
        "total_hours, editable_total_hours, and formula_mode. "
        "For seam/detail quantity rows such as roofing seams_misc row 47 or penetrations row 49, include true requires linear_ft, "
        "estimated_units, units, or amount. If you only know that seams/penetrations exist, leave the quantity row unchecked and "
        "put the issue in review_required/warnings while still including priced sealant/fabric/labor rows when their basis is available. "
        "For insulation jobs, include Loading and Traveling as normal checked logistics expense decisions unless evidence says otherwise; "
        "for Loading row 95 and Traveling row 97 do not use days, crew_size, daily_rate, hourly_rate, or total_hours; "
        "use only hours_per_day, people_count, trip_count, and unit_price because the workbook formula is hours x people x rate x trips. "
        "For insulation support and logistics rows, never include a row unless the calculation will produce nonzero cost: "
        "Caulk / Sealant rows require linear_ft or estimated_units plus unit_price; Drum Disposal requires estimated_units/foam dependency plus unit_price; "
        "Sales / Inspection Trips and Truck Expense require trip_count, round_trip_miles, and unit_price. "
        "If address or mileage is missing, do not include Sales / Inspection Trips or Truck Expense; ask for/flag mileage instead. "
        "If the notes mention seal voids or masking but do not provide a measurable basis, either estimate a defensible quantity from openings with evidence "
        "or leave the material row unchecked with review_required true. "
        "For roofing and insulation foam decisions, prefer foam_yield_history_digest entries matching template type, foam type, "
        "product/template option, and thickness band. The digest is mined from historical estimates and includes product, thickness, "
        "square feet, estimated yield, and estimated sets examples; use it as evidence for yield_or_coverage and product selection. "
        "Include that evidence and set review_required when the historical range is wide or evidence is thin. "
        "If no matching insulation foam history is available but template_fallback_defaults includes insulation foam yield/unit price, use those as "
        "review-marked template fallbacks instead of saying yield or unit price is unavailable. "
        "Do not assume a generic standard foam thickness such as 2 inches; derive thickness from explicit thickness, target R-value with "
        "product R-per-inch evidence, or matching historical template evidence, otherwise ask or mark thickness review_required. "
        "For roofing jobs, use roofing workbook buckets directly: foam for roof SPF, coating, primer, caulk_detail, fabric, seams_misc, "
        "penetrations, board_stock, fasteners, plates, granules, dumpster, lift, generator, sales_trips, truck_expense, and roofing labor buckets. "
        "For roofing Loading row 136 and Traveling row 138, do not use days, crew_size, daily_rate, hourly_rate, or total_hours; "
        "use only hours_per_day, people_count, trip_count, and unit_price because those rows are logistics expense rows. "
        "For roofing Infrared row 141 use hours_per_day and unit_price; for Meals / Hotel row 144 use days, people_count, and unit_price. "
        "Roofing labor rows use the mixed labor formula: daily_rate and days when a daily rate is available, otherwise total_hours and hourly_rate. "
        "Roofing sales/truck travel rows use trip_count x round_trip_miles x unit_price. "
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


def _apply_basis_area_multiplier_from_messages(
    scope: dict[str, Any],
    messages: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    text = "\n".join(str(message.get("content") or "") for message in messages if isinstance(message, dict))
    multiplier = _basis_area_multiplier_from_text(text)
    if multiplier <= 0:
        return dict(scope or {})
    current = _safe_positive_number(
        (scope or {}).get("net_sqft")
        or (scope or {}).get("estimated_sqft")
        or (scope or {}).get("gross_sqft")
    )
    if current <= 0:
        return dict(scope or {})
    adjusted = round(current * multiplier, 2)
    if adjusted <= 0 or abs(adjusted - current) < 0.01:
        return dict(scope or {})
    updated = dict(scope or {})
    updated["estimated_sqft"] = adjusted
    updated["net_sqft"] = adjusted
    updated["basis_sqft"] = adjusted
    updated["basis_area_multiplier"] = multiplier
    updated["measured_sqft_before_multiplier"] = current
    return updated


def _basis_area_multiplier_from_text(text: str) -> float:
    normalized = _clean_string(text).lower().replace("_", " ")
    if not normalized:
        return 0.0
    patterns = [
        r"\b(?:multiply|multiplied|adjust|adjusted|increase|increased)\b.{0,80}\b(?:basis|area|sq\s*ft|sqft|square\s*feet)\b.{0,40}\b(?:by|x|times)\s*(\d+(?:\.\d+)?)\b",
        r"\b(?:basis|area|sq\s*ft|sqft|square\s*feet)\b.{0,80}\b(?:multiply|multiplied|adjust|adjusted|increase|increased)\b.{0,40}\b(?:by|x|times)\s*(\d+(?:\.\d+)?)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized, re.I)
        if not match:
            continue
        multiplier = _safe_positive_number(match.group(1))
        if 0.1 < multiplier < 10:
            return multiplier
    percent_patterns = [
        r"\b(?:increase|increased|add|added)\b.{0,80}\b(?:basis|area|sq\s*ft|sqft|square\s*feet)\b.{0,40}\b(?:by\s*)?(\d+(?:\.\d+)?)\s*(?:%|percent)\b",
        r"\b(?:basis|area|sq\s*ft|sqft|square\s*feet)\b.{0,80}\b(?:increase|increased|add|added)\b.{0,40}\b(?:by\s*)?(\d+(?:\.\d+)?)\s*(?:%|percent)\b",
    ]
    for pattern in percent_patterns:
        match = re.search(pattern, normalized, re.I)
        if not match:
            continue
        pct = _safe_positive_number(match.group(1))
        if 0 < pct < 900:
            return round(1 + pct / 100.0, 6)
    return 0.0


def _safe_positive_number(value: Any) -> float:
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0
    return number if number > 0 else 0.0


def _sanitize_logistics_loading_travel_values(values: dict[str, Any], *, row_number: str) -> dict[str, Any]:
    cleaned = dict(values or {})
    if cleaned.get("hours_per_day") in (None, ""):
        cleaned["hours_per_day"] = _first_present(cleaned, "hours", "total_hours", "days")
    if cleaned.get("people_count") in (None, ""):
        cleaned["people_count"] = _first_present(cleaned, "crew_size")
    is_loading = row_number in {"95", "136"}
    default_hours = (
        INSULATION_CHAT_TEMPLATE_DEFAULTS["loading_hours_per_day"]
        if is_loading
        else INSULATION_CHAT_TEMPLATE_DEFAULTS["traveling_hours_per_day"]
    )
    default_people = (
        INSULATION_CHAT_TEMPLATE_DEFAULTS["loading_people_count"]
        if is_loading
        else INSULATION_CHAT_TEMPLATE_DEFAULTS["traveling_people_count"]
    )
    default_rate = (
        INSULATION_CHAT_TEMPLATE_DEFAULTS["loading_hourly_rate"]
        if is_loading
        else INSULATION_CHAT_TEMPLATE_DEFAULTS["traveling_hourly_rate"]
    )
    max_hours = 2.0 if is_loading else 6.0
    hours = _safe_positive_number(cleaned.get("hours_per_day"))
    people = _safe_positive_number(cleaned.get("people_count"))
    rate = _safe_positive_number(cleaned.get("unit_price"))
    cleaned["hours_per_day"] = default_hours if hours <= 0 or hours > max_hours else hours
    cleaned["people_count"] = default_people if people <= 0 else people
    cleaned["unit_price"] = default_rate if rate <= 0 or rate > default_rate * 1.5 else rate
    return {
        key: cleaned.get(key)
        for key in ("hours_per_day", "people_count", "trip_count", "unit_price", "round_trip_miles")
        if cleaned.get(key) not in (None, "")
    }


def _clean_decision_preferences(value: Any, *, template_type: str = "") -> list[dict[str, Any]]:
    rows = value if isinstance(value, list) else []
    cleaned_rows: list[dict[str, Any]] = []
    normalized_template_type = _clean_string(template_type).lower()
    for row in rows:
        if not isinstance(row, dict):
            continue
        cleaned = dict(row)
        proposed_values = dict(cleaned.get("proposed_values") or {})
        for key in (
            "basis_sqft",
            "thickness_inches",
            "foam_thickness_inches",
            "yield_or_coverage",
            "gal_per_100_sqft",
            "coverage_sqft_per_unit",
            "unit_price",
            "price_per_square",
            "estimated_units",
            "linear_ft",
            "period",
            "margin_pct",
            "days",
            "hours",
            "hours_per_day",
            "people_count",
            "trip_count",
            "round_trip_miles",
            "crew_size",
            "daily_rate",
            "hourly_rate",
            "total_hours",
            "editable_total_hours",
            "formula_mode",
        ):
            if key in cleaned and cleaned.get(key) not in (None, ""):
                proposed_values.setdefault(key, cleaned.get(key))
        bucket = _clean_string(cleaned.get("template_bucket") or cleaned.get("package") or cleaned.get("category")).lower()
        bucket = bucket.replace(" ", "_").replace("-", "_")
        row_number = _safe_row_number(cleaned.get("workbook_row") or cleaned.get("row_number"))
        logistics_alias = _loading_travel_alias(cleaned)
        if not bucket and logistics_alias:
            bucket = logistics_alias
        if not row_number and logistics_alias:
            if normalized_template_type == "roofing":
                row_number = "136" if logistics_alias == "labor_loading" else "138"
            else:
                row_number = "95" if logistics_alias == "labor_loading" else "97"
        if row_number in {"95", "97", "136", "138"}:
            proposed_values = _sanitize_logistics_loading_travel_values(proposed_values, row_number=row_number)
            is_roofing_row = row_number in {"136", "138"}
            cleaned["section"] = "roofing_logistics_expense_template_decisions" if is_roofing_row else "insulation_logistics_expense_template_decisions"
            cleaned["template_bucket"] = bucket or ("labor_loading" if row_number in {"95", "136"} else "labor_traveling")
            cleaned["workbook_row"] = row_number
            for stale_key in ("days", "crew_size", "daily_rate", "hourly_rate", "total_hours", "editable_total_hours"):
                cleaned.pop(stale_key, None)
        elif bucket == "foam" or row_number in {"19", "20", "21"}:
            proposed_values.pop("yield_or_coverage", None)
            proposed_values.pop("foam_yield_or_coverage", None)
            proposed_values.pop("foam_yield", None)
        elif row_number in {"99", "141"} and bucket in {"infrared_scan", "labor_infrared_scan", ""}:
            if proposed_values.get("hours_per_day") in (None, ""):
                proposed_values["hours_per_day"] = _first_present(proposed_values, "hours", "total_hours", "days")
            proposed_values = {
                key: proposed_values.get(key)
                for key in ("hours_per_day", "unit_price")
                if proposed_values.get(key) not in (None, "")
            }
            cleaned["section"] = "roofing_logistics_expense_template_decisions" if row_number == "141" else "insulation_logistics_expense_template_decisions"
            cleaned["template_bucket"] = "infrared_scan"
            cleaned["workbook_row"] = row_number
            for stale_key in ("days", "crew_size", "daily_rate", "hourly_rate", "total_hours", "editable_total_hours"):
                cleaned.pop(stale_key, None)
        elif row_number in {"100", "144"}:
            if proposed_values.get("people_count") in (None, ""):
                proposed_values["people_count"] = _first_present(proposed_values, "crew_size")
            proposed_values = {
                key: proposed_values.get(key)
                for key in ("days", "people_count", "unit_price")
                if proposed_values.get(key) not in (None, "")
            }
            cleaned["section"] = "roofing_logistics_expense_template_decisions" if row_number == "144" else "insulation_logistics_expense_template_decisions"
            cleaned["template_bucket"] = "meals_lodging"
            cleaned["workbook_row"] = row_number
            for stale_key in ("crew_size", "daily_rate", "hourly_rate", "total_hours", "editable_total_hours"):
                cleaned.pop(stale_key, None)
        cleaned["proposed_values"] = proposed_values
        cleaned_rows.append(cleaned)
    return cleaned_rows


def _loading_travel_alias(row: dict[str, Any]) -> str:
    text = _clean_string(
        " ".join(
            str(row.get(key) or "")
            for key in (
                "decision_id",
                "template_bucket",
                "package",
                "category",
                "label",
                "target",
                "line_item",
                "section",
                "description",
            )
        )
    ).lower()
    tokenized = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    if re.search(r"\b(?:labor\s+)?loading\b", text) or "labor_loading" in tokenized:
        return "labor_loading"
    if re.search(r"\b(?:labor\s+)?travel(?:ing)?\b", text) or "labor_traveling" in tokenized:
        return "labor_traveling"
    return ""


def _first_present(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


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


def _merge_chat_scopes(baseline_scope: dict[str, Any], ai_scope: dict[str, Any]) -> dict[str, Any]:
    merged = {**_clean_scope(baseline_scope or {}), **_clean_scope(ai_scope or {})}
    baseline = _clean_scope(baseline_scope or {})
    if baseline.get("template_type") == "insulation" and (
        baseline.get("net_insulation_area_sqft") or baseline.get("gross_insulation_area_sqft") or baseline.get("foam_type")
    ):
        for key in ("template_type", "division", "project_type"):
            if baseline.get(key):
                merged[key] = baseline[key]
    for key in DETERMINISTIC_DIMENSION_FIELDS:
        if baseline.get(key) not in (None, "", [], {}):
            merged[key] = baseline[key]
    for key in ("requested_timing", "r_value_per_inch_assumption"):
        if baseline.get(key) not in (None, "", [], {}) and not merged.get(key):
            merged[key] = baseline[key]
    if baseline.get("foam_type") and not merged.get("foam_type"):
        merged["foam_type"] = baseline["foam_type"]
    if baseline.get("target_r_value") and not merged.get("target_r_value"):
        merged["target_r_value"] = baseline["target_r_value"]
    if baseline.get("insulation_r_value_targets") and not merged.get("insulation_r_value_targets"):
        merged["insulation_r_value_targets"] = baseline["insulation_r_value_targets"]
    if baseline.get("foam_thickness_inches") and not merged.get("foam_thickness_inches"):
        merged["foam_thickness_inches"] = baseline["foam_thickness_inches"]
    return _clean_scope(merged)


def _merge_chat_notes(baseline_notes: str, ai_notes: str) -> str:
    baseline = _clean_string(baseline_notes)
    notes = _clean_string(ai_notes)
    if not baseline:
        return notes
    if not notes:
        return baseline
    if baseline in notes or notes in baseline:
        return notes if len(notes) >= len(baseline) else baseline
    return f"{baseline}\n\nEstimator chat update: {notes}"


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


def _parse_target_r_value(text: str) -> float | None:
    match = re.search(r"\bR[-\s]?(\d+(?:\.\d+)?)\b", text, re.I)
    return float(match.group(1)) if match else None


def _parse_r_value_per_inch(text: str) -> float | None:
    match = re.search(r"\b(\d+(?:\.\d+)?)\s*R\s*/\s*in(?:ch)?\b", text, re.I)
    if not match:
        match = re.search(r"\bR[- ]?value\s+per\s+inch\s+(?:is|=|of)?\s*(\d+(?:\.\d+)?)\b", text, re.I)
    return float(match.group(1)) if match else None


def _parse_site_address(text: str) -> str:
    match = re.search(
        r"\b(?:site\s+address|address)\s*(?:is|:)?\s*([^\n.;]+(?:,\s*[A-Z]{2})?(?:\s+\d{5}(?:-\d{4})?)?)",
        text,
        re.I,
    )
    if not match:
        return ""
    address = _clean_string(match.group(1))
    if len(address) < 8 or not re.search(r"\d", address):
        return ""
    return address


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
