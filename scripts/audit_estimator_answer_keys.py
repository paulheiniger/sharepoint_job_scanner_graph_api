from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from jobscan.estimator import estimate_from_field_notes
from jobscan.estimator.chat_assistant import run_estimator_chat_turn
from jobscan.estimator.data_loader import load_estimator_data
from jobscan.estimator.reference_answer_key import (
    answer_key_to_workbook_decision_preferences,
    build_reference_estimate_answer_key,
)
from jobscan.estimator.schemas import EstimatorData
from jobscan.estimator.workbench import build_estimating_workbench, recalculate_workbench_tables


DEFAULT_CASES_DIR = Path("output/estimator_generated_cases/cases")
DEFAULT_OUT_DIR = Path("output/estimator_generated_cases/answer_key_audit")
EXCLUDED_KINDS = {"header", "total", "subtotal", "metadata", "other"}
EXCLUDED_BUCKETS = {
    "address",
    "customer",
    "email",
    "estimate_date",
    "estimated_square_feet",
    "job_name",
    "phone",
    "total_job_cost",
    "worksheet_price",
}


_CHAT_CONTEXT_DATA_CACHE: dict[tuple[str, str, str], EstimatorData] = {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: Any) -> str:
    return _text(value).lower().replace(" ", "_").replace("-", "_")


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def answer_key_pollution_reasons(row: dict[str, Any]) -> list[str]:
    bucket = _norm(row.get("template_bucket"))
    reasons: list[str] = []
    thickness = _safe_float(row.get("thickness_inches"))
    if bucket in {"foam", "roofing_foam"} and thickness is not None and not 0.01 <= thickness <= 24.0:
        reasons.append(f"implausible foam thickness {thickness:g}")
    yield_value = _safe_float(row.get("yield_or_coverage") or row.get("yield_factor"))
    if bucket in {"foam", "roofing_foam"} and yield_value is not None and not 100.0 <= yield_value <= 20000.0:
        reasons.append(f"implausible foam yield/coverage {yield_value:g}")
    for field in ("crew_size", "crew_selector_code", "people_count"):
        crew = _safe_float(row.get(field))
        if crew is not None and not 0.01 <= crew <= 20.0:
            reasons.append(f"implausible {field} {crew:g}")
    return reasons


def is_actionable_answer_key_row(row: dict[str, Any]) -> bool:
    bucket = _norm(row.get("template_bucket"))
    kind = _norm(row.get("line_item_kind"))
    if kind in EXCLUDED_KINDS or bucket in EXCLUDED_BUCKETS:
        return False
    return bool(bucket and bucket != "unknown")


def _decision_row_id(row: dict[str, Any]) -> str:
    value = row.get("workbook_row") or row.get("row_number") or row.get("source_row")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return _text(value)


def _case_template_type(case_dir: Path, decisions: list[dict[str, Any]]) -> str:
    for row in decisions:
        template_type = _norm(row.get("template_type"))
        if template_type in {"roofing", "insulation", "flooring"}:
            return template_type
    case_id = case_dir.name.lower()
    if "insulation" in case_id:
        return "insulation"
    if "flooring" in case_id:
        return "flooring"
    return "roofing"


def _rows_for_answer_key(decisions: list[dict[str, Any]], template_type: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(decisions):
        if not isinstance(row, dict):
            continue
        copied = dict(row)
        copied.setdefault("template_type", template_type)
        copied.setdefault("row_number", copied.get("workbook_row"))
        copied.setdefault("template_row_id", copied.get("template_row_id") or f"source-decision-{index}")
        copied.setdefault("line_item_kind", copied.get("line_item_kind") or "material")
        rows.append(copied)
    return pd.DataFrame(rows)


def _case_answer_key(case_dir: Path, decisions: list[dict[str, Any]], template_type: str) -> dict[str, Any]:
    return build_reference_estimate_answer_key(
        _rows_for_answer_key(decisions, template_type),
        job_context={
            "job_id": case_dir.name,
            "job_name": case_dir.name.replace("_", " "),
            "template_type": template_type,
            "project_type": "roofing estimate" if template_type == "roofing" else template_type,
        },
    )


def _decision_packages(decisions: list[dict[str, Any]]) -> list[str]:
    packages = [
        _norm(row.get("template_bucket"))
        for row in decisions
        if isinstance(row, dict) and _norm(row.get("template_bucket")) and _norm(row.get("template_bucket")) != "unknown"
    ]
    return sorted(set(packages))


def _decision_area(decisions: list[dict[str, Any]]) -> float:
    candidates: list[float] = []
    for row in decisions:
        if not isinstance(row, dict):
            continue
        for field in ("basis_sqft", "area_sqft", "estimated_sqft", "square_feet", "quantity"):
            value = _safe_float(row.get(field))
            if value and 10 <= value <= 10_000_000:
                candidates.append(value)
    return max(candidates) if candidates else 0.0


def _note_area(notes: str) -> float:
    candidates: list[tuple[float, float]] = []
    for match in re.finditer(r"\b(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:sq\.?\s*ft\.?|square\s+feet|sqft|\bsf\b)", str(notes or ""), re.I):
        value = _safe_float(match.group(1))
        if not value or not (20 <= value <= 10_000_000):
            continue
        context = str(notes or "")[max(0, match.start() - 70) : min(len(str(notes or "")), match.end() + 70)].lower()
        if re.search(r"\b(?:deduct|less|subtract|opening|openings|door|doors|window|windows|curb|curbs|equipment)\b", context):
            if not re.search(r"\b(?:net|total|spray|roof|surface|work|project)\s+(?:area|sq|sf)\b", context):
                continue
        score = 1.0
        if re.search(r"\bnet\b", context):
            score += 60.0
        if re.search(r"\b(?:total|spray|roof|surface|work|project|measured|estimated)\s+(?:area|sq|sf)\b", context):
            score += 45.0
        if re.search(r"\b(?:use|carry|working|approx)\b", context):
            score += 25.0
        candidates.append((score, value))
    if not candidates:
        return 0.0
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return round(candidates[0][1], 2)


def _case_note_identity(notes: str, case_dir: Path) -> dict[str, str]:
    lines = [_text(line) for line in str(notes or "").splitlines() if _text(line)]
    header = ""
    source_file = ""
    for line in lines[:8]:
        if line.lower().startswith("historical proposal/source:"):
            source_file = _text(line.split(":", 1)[1] if ":" in line else "")
            continue
        if line.lower().startswith(("site address:", "field notes reconstructed")):
            continue
        if not header:
            header = line[:180]
    fallback = case_dir.name.replace("_", " ")
    job_name = header or fallback
    customer = _text(job_name.split("/", 1)[0]) if "/" in job_name else job_name
    return {
        "job_name": job_name,
        "customer": customer,
        "source_file": source_file or str(case_dir / "source_decisions.json"),
    }


def _case_scope_metadata(notes: str, packages: list[str], template_type: str) -> dict[str, Any]:
    text = str(notes or "").lower()
    substrate = ""
    for value, pattern in (
        ("metal", r"\b(?:metal|standing seam|r panel|corrugated)\b"),
        ("tpo", r"\btpo\b"),
        ("epdm", r"\bepdm\b"),
        ("concrete", r"\bconcrete\b"),
        ("cmu", r"\bcmu|block wall|masonry\b"),
        ("spray foam", r"\b(?:spf|spray foam|foam roof)\b"),
    ):
        if re.search(pattern, text):
            substrate = value
            break
    building_type = ""
    for value, pattern in (
        ("tank", r"\btank\b"),
        ("pole barn", r"\bpole barn\b"),
        ("metal building", r"\bmetal building\b"),
        ("church", r"\bchurch\b"),
        ("library", r"\blibrary\b"),
        ("industrial", r"\bindustrial|warehouse|plant|factory\b"),
        ("residential", r"\bresidence|residential|home\b"),
    ):
        if re.search(pattern, text):
            building_type = value
            break
    warranty_match = re.search(r"\b(\d{1,2})\s*(?:year|yr)\b.{0,30}\bwarranty\b|\bwarranty\b.{0,30}\b(\d{1,2})\s*(?:year|yr)\b", text)
    warranty_years = ""
    if warranty_match:
        warranty_years = warranty_match.group(1) or warranty_match.group(2) or ""
    market_segment = ""
    if re.search(r"\bresidence|residential|home\b", text):
        market_segment = "residential"
    elif re.search(r"\bchurch|library|school|university|commercial|industrial|warehouse|plant|factory\b", text):
        market_segment = "commercial"
    project_class = "roofing_restoration" if template_type == "roofing" else "spray_foam_insulation"
    if template_type == "roofing" and "foam" in packages and "coating" in packages:
        project_class = "roofing_spf_coating"
    elif template_type == "roofing" and "coating" in packages:
        project_class = "roofing_coating"
    elif template_type == "insulation" and re.search(r"\bopen[- ]?cell\b", text):
        project_class = "open_cell_insulation"
    elif template_type == "insulation" and re.search(r"\bclosed[- ]?cell\b", text):
        project_class = "closed_cell_insulation"
    return {
        "project_class": project_class,
        "market_segment": market_segment,
        "building_type": building_type,
        "substrate": substrate,
        "warranty_years": warranty_years,
    }


def _case_template_example_row(case_dir: Path) -> dict[str, Any] | None:
    source_path = case_dir / "source_decisions.json"
    notes_path = case_dir / "notes.txt"
    if not source_path.exists():
        return None
    try:
        decisions = json.loads(source_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(decisions, list):
        decisions = []
    template_type = _case_template_type(case_dir, decisions)
    answer_key = _case_answer_key(case_dir, decisions, template_type)
    if not answer_key.get("decisions"):
        return None
    notes = notes_path.read_text(encoding="utf-8") if notes_path.exists() else ""
    packages = _decision_packages(decisions)
    identity = _case_note_identity(notes, case_dir)
    metadata = _case_scope_metadata(notes, packages, template_type)
    return {
        "example_id": case_dir.name,
        "job_id": case_dir.name,
        "document_id": case_dir.name,
        "source_file": identity["source_file"],
        "customer": identity["customer"],
        "job_name": identity["job_name"],
        "template_type": template_type,
        "project_class": metadata["project_class"],
        "market_segment": metadata["market_segment"],
        "building_type": metadata["building_type"],
        "substrate": metadata["substrate"],
        "material_system": ", ".join(packages[:8]),
        "material_packages_json": json.dumps(packages),
        "warranty_years": metadata["warranty_years"],
        "area_sqft": _decision_area(decisions) or _note_area(notes),
        "area_bucket": "",
        "scope_summary": notes[:1200],
        "decision_summary": ", ".join(packages[:12]),
        "answer_key_json": json.dumps(answer_key, default=str),
        "source": "generated_case_source_decisions",
        "confidence": 0.85,
    }


def _estimator_data_from_case_examples(cases_dir: Path) -> EstimatorData:
    rows = [
        row
        for row in (_case_template_example_row(path) for path in sorted(cases_dir.iterdir()) if path.is_dir())
        if isinstance(row, dict)
    ]
    return EstimatorData(template_examples=pd.DataFrame(rows))


def _chat_context_data(
    cases_dir: Path,
    *,
    source: str = "cases",
    database_url: str = "",
    load_profile: str = "chat",
) -> EstimatorData:
    cache_key = (str(cases_dir.resolve()), source, database_url or "")
    if cache_key in _CHAT_CONTEXT_DATA_CACHE:
        return _CHAT_CONTEXT_DATA_CACHE[cache_key]
    if source == "database":
        if not database_url:
            raise ValueError("--database-url is required when --chat-context-source=database")
        data = load_estimator_data(REPO_ROOT, database_url=database_url, prefer_database=True, load_profile=load_profile)
    elif source == "empty":
        data = EstimatorData()
    else:
        data = _estimator_data_from_case_examples(cases_dir)
    _CHAT_CONTEXT_DATA_CACHE[cache_key] = data
    return data


def _row_cost(row: dict[str, Any]) -> float:
    for field in ("estimated_cost", "calculated_cost", "amount", "calculated_output"):
        value = _safe_float(row.get(field))
        if value is not None:
            return value
    return 0.0


def _included_decision_rows(workbench: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for section, value in workbench.items():
        if section == "decision_proposals":
            continue
        if not isinstance(value, list):
            continue
        for row in value:
            if not isinstance(row, dict) or not row.get("include"):
                continue
            workbook_row = _decision_row_id(row)
            if not workbook_row:
                continue
            copied = dict(row)
            copied.setdefault("section", section)
            copied["workbook_row"] = workbook_row
            rows.append(copied)
    return rows


def _workbench_diagnostics(workbench: dict[str, Any], expected_rows: set[str]) -> dict[str, Any]:
    rows = _included_decision_rows(workbench)
    actual_rows = {_text(row.get("workbook_row")) for row in rows if _text(row.get("workbook_row"))}
    row_counts = Counter(_text(row.get("workbook_row")) for row in rows if _text(row.get("workbook_row")))
    duplicate_rows = sorted(row for row, count in row_counts.items() if count > 1)
    raw_zero_cost_rows = [
        {
            "workbook_row": row.get("workbook_row"),
            "template_bucket": row.get("template_bucket"),
            "label": row.get("template_line") or row.get("resolved_template_option") or row.get("label"),
            "section": row.get("section"),
        }
        for row in rows
        if _row_cost(row) <= 0 and _norm(row.get("template_bucket")) not in {"sales_tax", "overhead", "profit"}
    ]
    zero_cost_rows = [row for row in raw_zero_cost_rows if _text(row.get("workbook_row")) in expected_rows]
    extra_zero_cost_rows = [row for row in raw_zero_cost_rows if _text(row.get("workbook_row")) not in expected_rows]
    return {
        "actual_included_row_count": len(actual_rows),
        "matched_scoreable_row_count": len(expected_rows & actual_rows),
        "decision_row_overlap_ratio": round(len(expected_rows & actual_rows) / len(expected_rows), 4) if expected_rows else 0.0,
        "missing_scoreable_rows": sorted(expected_rows - actual_rows, key=lambda item: (len(item), item)),
        "extra_actual_rows": sorted(actual_rows - expected_rows, key=lambda item: (len(item), item)),
        "duplicate_decision_row_count": len(duplicate_rows),
        "duplicate_decision_rows": duplicate_rows,
        "included_zero_cost_count": len(zero_cost_rows),
        "included_zero_cost_rows": zero_cost_rows[:25],
        "extra_included_zero_cost_count": len(extra_zero_cost_rows),
        "extra_included_zero_cost_rows": extra_zero_cost_rows[:25],
        "raw_included_zero_cost_count": len(raw_zero_cost_rows),
    }


def _chat_context_cue_provider(captures: list[dict[str, Any]] | None = None):
    def provider(messages: list[dict[str, Any]], model: str) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if len(messages) > 1:
            try:
                payload = json.loads(str(messages[1].get("content") or "{}"))
            except json.JSONDecodeError:
                payload = {}
        context = payload.get("estimator_context") if isinstance(payload.get("estimator_context"), dict) else {}
        existing_scope = payload.get("existing_scope") if isinstance(payload.get("existing_scope"), dict) else {}
        conversation = payload.get("conversation") if isinstance(payload.get("conversation"), list) else []
        notes = "\n\n".join(str(row.get("content") or "") for row in conversation if isinstance(row, dict) and row.get("role") == "user")
        cues = context.get("historical_answer_key_decision_cues") if isinstance(context.get("historical_answer_key_decision_cues"), list) else []
        preferences: list[dict[str, Any]] = []
        for cue in cues:
            if not isinstance(cue, dict):
                continue
            decision_id = _text(cue.get("decision_id"))
            workbook_row = _text(cue.get("workbook_row"))
            section = _text(cue.get("section"))
            if not section or not (decision_id or workbook_row):
                continue
            suggested = cue.get("suggested_preference") if isinstance(cue.get("suggested_preference"), dict) else {}
            proposed_values = dict(cue.get("sample_inputs") or {})
            if isinstance(suggested.get("proposed_values"), dict):
                proposed_values.update({key: value for key, value in suggested["proposed_values"].items() if value not in (None, "", [], {})})
            if not proposed_values and isinstance(cue.get("sample_outputs"), dict):
                proposed_values = {
                    key: value
                    for key, value in cue["sample_outputs"].items()
                    if key in {"amount", "estimated_cost", "calculated_cost"}
                }
            preferences.append(
                {
                    "section": section,
                    "decision_id": decision_id,
                    "template_bucket": cue.get("template_bucket"),
                    "workbook_row": workbook_row,
                    "include": True,
                    "proposed_values": proposed_values,
                    "confidence": _safe_float(suggested.get("confidence")) or min(0.9, 0.55 + min(int(cue.get("support_count") or 1), 5) * 0.07),
                    "review_required": True,
                    "review_reasons": [
                        _text(cue.get("why_suggested"))
                        or "Proposed by deterministic audit provider from similar historical answer-key decision cues."
                    ],
                    "evidence": [
                        {
                            "source": "historical_answer_key_decision_cue",
                            "support_count": cue.get("support_count"),
                            "examples": cue.get("examples") or [],
                            "sample_outputs": cue.get("sample_outputs") or {},
                        }
                    ],
                    "source": "reference_estimate_answer_key",
                }
            )
        if captures is not None:
            captures.append(
                {
                    "model": model,
                    "prompt_bytes": sum(len(str(message.get("content") or "")) for message in messages),
                    "matched_answer_key_count": len((context.get("historical_answer_key_examples") or {}).get("matched_answer_keys") or []),
                    "decision_cue_count": len(cues),
                    "preference_count": len(preferences),
                    "top_cues": [
                        {
                            "decision_id": cue.get("decision_id"),
                            "template_bucket": cue.get("template_bucket"),
                            "workbook_row": cue.get("workbook_row"),
                            "support_count": cue.get("support_count"),
                            "best_similarity_score": cue.get("best_similarity_score"),
                        }
                        for cue in cues[:20]
                        if isinstance(cue, dict)
                    ],
                }
            )
        return {
            "assistant_message": f"Context-cue audit provider proposed {len(preferences)} workbook decision preference(s).",
            "estimator_notes": notes,
            "scope_overrides": existing_scope,
            "workbook_decision_preferences": preferences,
            "missing_questions": [],
            "assumptions": ["Historical answer-key decision cues were used for audit only."],
            "warnings": [],
            "confidence": 0.72 if preferences else 0.0,
        }

    return provider


def _run_estimator_diagnostics(
    notes: str,
    *,
    expected_rows: set[str],
    answer_key_preferences: list[dict[str, Any]] | None = None,
    answer_key_template_type: str = "",
) -> dict[str, Any]:
    original_mapbox_setting = os.environ.get("MAPBOX_ROUTING_ENABLED")
    os.environ["MAPBOX_ROUTING_ENABLED"] = "0"
    recommendation = estimate_from_field_notes(notes, {}, data=EstimatorData())
    try:
        scope_override: dict[str, Any] | None = None
        if answer_key_preferences:
            scope = dict(getattr(recommendation, "parsed_fields", {}) or {})
            if answer_key_template_type:
                scope["template_type"] = answer_key_template_type
                scope["estimate_mode"] = answer_key_template_type
                if answer_key_template_type == "insulation":
                    scope["division"] = "Insulation"
                    scope["project_type"] = "spray foam insulation"
                elif answer_key_template_type == "roofing":
                    scope["division"] = "Roofing"
                    scope.setdefault("project_type", "roofing estimate")
                elif answer_key_template_type == "flooring":
                    scope["division"] = "Flooring"
            scope["estimator_chat"] = {
                "source": "answer_key_audit",
                "workbook_decision_preferences": answer_key_preferences,
            }
            recommendation = replace(recommendation, parsed_fields=scope)
            scope_override = scope
        workbench = build_estimating_workbench(recommendation, EstimatorData(), scope_override=scope_override)
        workbench = recalculate_workbench_tables(workbench)
        return _workbench_diagnostics(workbench, expected_rows)
    finally:
        if original_mapbox_setting is None:
            os.environ.pop("MAPBOX_ROUTING_ENABLED", None)
        else:
            os.environ["MAPBOX_ROUTING_ENABLED"] = original_mapbox_setting


def _run_chat_context_diagnostics(
    notes: str,
    *,
    expected_rows: set[str],
    template_type: str,
    data: EstimatorData,
    save_prompt_path: Path | None = None,
) -> dict[str, Any]:
    original_mapbox_setting = os.environ.get("MAPBOX_ROUTING_ENABLED")
    os.environ["MAPBOX_ROUTING_ENABLED"] = "0"
    captures: list[dict[str, Any]] = []
    try:
        result = run_estimator_chat_turn(
            [{"role": "user", "content": notes}],
            data=data,
            template_type_hint=template_type,
            existing_scope={
                "template_type": template_type,
                "division": "Roofing" if template_type == "roofing" else template_type.title(),
                "project_type": "roofing estimate" if template_type == "roofing" else template_type,
                "estimate_mode": template_type,
                "template_type_locked": True,
            },
            provider=_chat_context_cue_provider(captures),
            model="context-cue-provider",
        )
        if save_prompt_path is not None and captures:
            save_prompt_path.parent.mkdir(parents=True, exist_ok=True)
            save_prompt_path.write_text(json.dumps(captures[-1], indent=2, default=str), encoding="utf-8")
        recommendation = estimate_from_field_notes(
            result.estimator_notes or notes,
            {"disable_ai_scope_interpreter": True},
            data=EstimatorData(),
        )
        scope = dict(result.scope_overrides or {})
        scope["template_type"] = template_type
        scope["estimate_mode"] = template_type
        scope["division"] = "Roofing" if template_type == "roofing" else template_type.title()
        scope["estimator_chat"] = {
            "source": "chat_context_cue_audit",
            "assistant_message": result.assistant_message,
            "confidence": result.confidence,
            "workbook_decision_preferences": result.workbook_decision_preferences,
        }
        recommendation = replace(recommendation, parsed_fields=scope)
        workbench = build_estimating_workbench(recommendation, data, scope_override=scope)
        workbench = recalculate_workbench_tables(workbench)
        diagnostics = _workbench_diagnostics(workbench, expected_rows)
        diagnostics["chat_context"] = captures[-1] if captures else {}
        diagnostics["chat_preference_count"] = len(result.workbook_decision_preferences or [])
        diagnostics["chat_source"] = result.source
        diagnostics["chat_confidence"] = result.confidence
        return diagnostics
    finally:
        if original_mapbox_setting is None:
            os.environ.pop("MAPBOX_ROUTING_ENABLED", None)
        else:
            os.environ["MAPBOX_ROUTING_ENABLED"] = original_mapbox_setting


def audit_case(
    case_dir: Path,
    *,
    notes_filename: str = "notes.txt",
    run_estimator: bool = False,
    apply_answer_key: bool = False,
    run_chat_context: bool = False,
    chat_context_data: EstimatorData | None = None,
    chat_prompt_dir: Path | None = None,
) -> dict[str, Any]:
    source_path = case_dir / "source_decisions.json"
    notes_path = case_dir / notes_filename
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    decisions = json.loads(source_path.read_text(encoding="utf-8"))
    if not isinstance(decisions, list):
        decisions = []
    polluted = []
    actionable_rows = []
    scoreable_rows = []
    for row in decisions:
        if not isinstance(row, dict):
            continue
        if not is_actionable_answer_key_row(row):
            continue
        row_id = _decision_row_id(row)
        actionable_rows.append(row_id)
        reasons = answer_key_pollution_reasons(row)
        if reasons:
            polluted.append(
                {
                    "workbook_row": row_id,
                    "template_bucket": row.get("template_bucket"),
                    "line_item": row.get("resolved_item_name") or row.get("selected_item_name") or row.get("line_item"),
                    "reasons": reasons,
                }
            )
            continue
        scoreable_rows.append(row_id)

    result = {
        "case_id": case_dir.name,
        "source_decision_count": len(decisions),
        "actionable_expected_row_count": len(set(filter(None, actionable_rows))),
        "polluted_expected_row_count": len(polluted),
        "scoreable_expected_row_count": len(set(filter(None, scoreable_rows))),
        "polluted_expected_rows": polluted,
        "notes_path": str(notes_path) if notes_path.exists() else "",
    }
    if run_estimator and notes_path.exists():
        notes = notes_path.read_text(encoding="utf-8")
        scoreable = set(filter(None, scoreable_rows))
        template_type = _case_template_type(case_dir, decisions)
        source_rows = _rows_for_answer_key(decisions, template_type)
        answer_key = build_reference_estimate_answer_key(source_rows, job_context={"template_type": template_type})
        preferences = answer_key_to_workbook_decision_preferences(answer_key)
        normalized_scoreable = {
            str(row.get("workbook_row") or "").strip()
            for row in preferences
            if isinstance(row, dict) and str(row.get("workbook_row") or "").strip()
        }
        if normalized_scoreable:
            result["normalized_scoreable_expected_row_count"] = len(normalized_scoreable)
            scoreable = normalized_scoreable
        result["baseline_estimator"] = _run_estimator_diagnostics(notes, expected_rows=scoreable)
        if apply_answer_key:
            result["answer_key_preference_count"] = len(preferences)
            result["answer_key_applied_estimator"] = _run_estimator_diagnostics(
                notes,
                expected_rows=scoreable,
                answer_key_preferences=preferences,
                answer_key_template_type=template_type,
            )
        if run_chat_context:
            result["chat_context_estimator"] = _run_chat_context_diagnostics(
                notes,
                expected_rows=scoreable,
                template_type=template_type,
                data=chat_context_data or EstimatorData(),
                save_prompt_path=(chat_prompt_dir / f"{case_dir.name}.json") if chat_prompt_dir else None,
            )
    return result


def audit_cases(
    cases_dir: Path,
    *,
    case_id: str | None = None,
    notes_filename: str = "notes.txt",
    limit: int = 0,
    run_estimator: bool = False,
    apply_answer_key: bool = False,
    run_chat_context: bool = False,
    chat_context_data: EstimatorData | None = None,
    chat_prompt_dir: Path | None = None,
) -> list[dict[str, Any]]:
    case_dirs = [path for path in sorted(cases_dir.iterdir()) if path.is_dir() and (path / "source_decisions.json").exists()]
    if case_id:
        case_dirs = [path for path in case_dirs if path.name == case_id]
    if limit > 0:
        case_dirs = case_dirs[:limit]
    return [
        audit_case(
            path,
            notes_filename=notes_filename,
            run_estimator=run_estimator,
            apply_answer_key=apply_answer_key,
            run_chat_context=run_chat_context,
            chat_context_data=chat_context_data,
            chat_prompt_dir=chat_prompt_dir,
        )
        for path in case_dirs
    ]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit generated estimator answer keys for polluted source decisions.")
    parser.add_argument("--cases-dir", type=Path, default=DEFAULT_CASES_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--case-id")
    parser.add_argument("--notes-filename", default="notes.txt")
    parser.add_argument("--limit", type=int, default=0, help="Maximum cases to audit; 0 means all.")
    parser.add_argument("--run-estimator", action="store_true", help="Also run the current estimator path against each case note.")
    parser.add_argument(
        "--apply-answer-key",
        action="store_true",
        help="When running estimator diagnostics, apply source_decisions as reference answer-key preferences.",
    )
    parser.add_argument(
        "--run-chat-context",
        action="store_true",
        help="Run the notes through estimator chat context using a deterministic context-cue provider.",
    )
    parser.add_argument(
        "--chat-context-source",
        choices=["cases", "database", "empty"],
        default="cases",
        help="Context source for --run-chat-context. cases builds lightweight examples from the generated cases.",
    )
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL") or "")
    parser.add_argument("--chat-load-profile", default="chat", choices=["chat", "interactive", "full"])
    parser.add_argument(
        "--save-chat-context-prompts",
        action="store_true",
        help="Write compact prompt/context diagnostics per case under the audit output directory.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    chat_context_data = (
        _chat_context_data(
            args.cases_dir,
            source=args.chat_context_source,
            database_url=args.database_url,
            load_profile=args.chat_load_profile,
        )
        if args.run_chat_context
        else None
    )
    chat_prompt_dir = args.out_dir / "chat_context_prompts" if args.save_chat_context_prompts else None
    results = audit_cases(
        args.cases_dir,
        case_id=args.case_id,
        notes_filename=args.notes_filename,
        limit=args.limit,
        run_estimator=args.run_estimator,
        apply_answer_key=args.apply_answer_key,
        run_chat_context=args.run_chat_context,
        chat_context_data=chat_context_data,
        chat_prompt_dir=chat_prompt_dir,
    )
    (args.out_dir / "answer_key_audit.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    pd.DataFrame(
        [
            {
                key: value
                for key, value in row.items()
                if key
                not in {
                    "polluted_expected_rows",
                    "baseline_estimator",
                    "answer_key_applied_estimator",
                    "chat_context_estimator",
                }
            }
            for row in results
        ]
    ).to_csv(args.out_dir / "answer_key_audit.csv", index=False)
    polluted_count = sum(int(row.get("polluted_expected_row_count") or 0) for row in results)
    if args.run_estimator:
        baseline_overlap = [
            float((row.get("baseline_estimator") or {}).get("decision_row_overlap_ratio") or 0.0)
            for row in results
        ]
        answer_key_overlap = [
            float((row.get("answer_key_applied_estimator") or {}).get("decision_row_overlap_ratio") or 0.0)
            for row in results
            if row.get("answer_key_applied_estimator")
        ]
        chat_context_overlap = [
            float((row.get("chat_context_estimator") or {}).get("decision_row_overlap_ratio") or 0.0)
            for row in results
            if row.get("chat_context_estimator")
        ]
        if baseline_overlap:
            print(f"Baseline estimator avg row overlap: {sum(baseline_overlap) / len(baseline_overlap):.3f}")
        if answer_key_overlap:
            print(f"Answer-key-applied avg row overlap: {sum(answer_key_overlap) / len(answer_key_overlap):.3f}")
        if chat_context_overlap:
            print(f"Chat-context cue avg row overlap: {sum(chat_context_overlap) / len(chat_context_overlap):.3f}")
    print(f"Audited {len(results)} estimator answer keys")
    print(f"Polluted expected rows: {polluted_count}")
    print(f"json: {args.out_dir / 'answer_key_audit.json'}")
    print(f"csv: {args.out_dir / 'answer_key_audit.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
