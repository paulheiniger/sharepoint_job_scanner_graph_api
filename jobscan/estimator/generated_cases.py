from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from jobscan.env import load_project_env

from . import estimate_from_field_notes
from .data_loader import load_estimator_data
from .schemas import EstimatorData
from .workbench import build_estimating_workbench, workbench_to_draft_workbook_inputs


DEFAULT_OUTPUT_DIR = Path("output/estimator_generated_cases")
DEFAULT_GENERATED_CASE_MODEL = "gpt-5.5-pro"
FALLBACK_GENERATED_CASE_MODEL = "gpt-5.5"
DEFAULT_REASONING_EFFORT = "high"

TEMPLATE_TYPE_ALIASES = {
    "roof": "roofing",
    "roofing": "roofing",
    "insulation": "insulation",
    "foam": "insulation",
}

DECISION_FIELD_CANDIDATES = (
    "selector_code",
    "resolved_item_name",
    "selected_item_name",
    "warranty_years",
    "thickness_inches",
    "foam_density_lb",
    "yield_or_coverage",
    "yield_factor",
    "gal_per_100_sqft",
    "gal_per_sqft",
    "wet_mils_estimate",
    "waste_factor_pct",
    "days",
    "crew_size",
    "crew_selector_code",
    "total_hours",
    "daily_rate",
    "hourly_rate",
    "unit_price",
    "estimated_cost",
)

PROMPT_CONTEXT_EXCLUDED_BUCKETS = {
    "address",
    "city",
    "customer",
    "email",
    "estimate_adder",
    "estimate_date",
    "estimated_square_feet",
    "job_name",
    "job_type",
    "misc_insurance",
    "overhead",
    "permits",
    "phone",
    "profit",
    "site_address",
    "total_job_cost",
    "warranty",
    "worksheet_price",
    "worksheet_price_adjusted",
}
PROMPT_CONTEXT_EXCLUDED_KINDS = {"header", "total", "subtotal", "other"}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    if str(value).strip().lower() in {"nan", "none", "null"}:
        return ""
    return " ".join(str(value or "").strip().split())


def _format_generated_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _lower(value: Any) -> str:
    return _clean_text(value).lower()


def _slug(value: Any) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return slug or "case"


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            return str(value)
    return str(value)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, default=_json_default, ensure_ascii=False)


def _normalize_template_type(value: Any) -> str:
    text = _lower(value)
    return TEMPLATE_TYPE_ALIASES.get(text, text)


def _ensure_columns(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    out = frame.copy()
    for column in columns:
        if column not in out.columns:
            out[column] = None
    return out


def _frame(data: EstimatorData | Any, attr: str) -> pd.DataFrame:
    value = getattr(data, attr, pd.DataFrame()) if data is not None else pd.DataFrame()
    return value.copy() if isinstance(value, pd.DataFrame) else pd.DataFrame(value)


def _merge_job_context(rows: pd.DataFrame, data: EstimatorData | Any) -> pd.DataFrame:
    if rows.empty or "job_id" not in rows.columns:
        return rows.copy()
    jobs = _frame(data, "jobs")
    if jobs.empty or "job_id" not in jobs.columns:
        return rows.copy()
    keep = [
        column
        for column in (
            "job_id",
            "customer",
            "job_name",
            "site_address",
            "address",
            "city_state_zip",
            "division",
            "project_type",
            "substrate",
            "building_type",
            "coating_type",
            "warranty_years",
            "roof_condition",
            "access_complexity",
            "penetrations_complexity",
            "pipeline_status",
            "status",
            "estimated_sqft",
            "area_sqft",
            "source_year",
        )
        if column in jobs.columns
    ]
    if keep == ["job_id"]:
        return rows.copy()
    merged = rows.merge(jobs[keep].drop_duplicates("job_id"), on="job_id", how="left", suffixes=("", "_job"))
    for column in keep:
        if column == "job_id":
            continue
        job_column = f"{column}_job"
        if job_column not in merged.columns:
            continue
        if column not in merged.columns:
            merged[column] = merged[job_column]
        else:
            original = merged[column]
            merged[column] = original.where(original.notna() & original.astype(str).str.strip().ne(""), merged[job_column])
        merged = merged.drop(columns=[job_column])
    return merged


def _row_has_decision(row: pd.Series) -> bool:
    for field in DECISION_FIELD_CANDIDATES:
        if field in row.index and str(row.get(field) or "").strip():
            return True
    return False


def _area_from_group(group: pd.DataFrame) -> float:
    for column in ("area_sqft", "estimated_sqft", "quantity"):
        if column not in group.columns:
            continue
        values = pd.to_numeric(group[column], errors="coerce")
        values = values[values.gt(0)]
        if not values.empty:
            return float(values.max())
    return 0.0


def _decision_id_for_row(template_type: str, bucket: str, line_kind: str) -> str:
    bucket = _slug(bucket)
    if template_type == "insulation":
        if bucket == "foam":
            return "insulation_foam_system"
        if bucket == "thermal_barrier_coating":
            return "insulation_thermal_barrier"
        if line_kind == "labor" or bucket.startswith("labor_"):
            return f"insulation_{bucket}"
        return f"insulation_{bucket}"
    if bucket == "coating":
        return "roofing_coating_system"
    if bucket == "primer":
        return "roofing_primer"
    if bucket in {"caulk_detail", "seam_treatment", "caulk_sealant"}:
        return "roofing_caulk_sealant"
    if bucket == "fabric":
        return "roofing_fabric"
    if bucket == "board_stock":
        return "roofing_board_stock"
    if bucket == "fasteners":
        return "roofing_fasteners"
    if bucket == "plates":
        return "roofing_plates"
    if bucket == "granules":
        return "roofing_granules"
    if line_kind == "labor" or bucket.startswith("labor_"):
        return f"roofing_{bucket}"
    if bucket in {"lift", "generator", "dumpster", "travel", "freight", "truck_expense"}:
        return f"equipment_{bucket}"
    return f"roofing_{bucket}"


def _decision_key(decision: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(decision.get("decision_id") or ""),
        str(decision.get("template_bucket") or ""),
        str(decision.get("workbook_row") or ""),
    )


def _value_present(value: Any) -> bool:
    if value in (None, ""):
        return False
    if isinstance(value, float) and math.isnan(value):
        return False
    if str(value).strip().lower() in {"", "nan", "none", "null"}:
        return False
    return True


def _merge_expected_decision(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in incoming.items():
        if _value_present(value) and not _value_present(merged.get(key)):
            merged[key] = value
    return merged


def _dedupe_expected_decisions(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for decision in decisions:
        key = _decision_key(decision)
        if key in merged:
            merged[key] = _merge_expected_decision(merged[key], decision)
        else:
            merged[key] = dict(decision)
    out = list(merged.values())
    out.sort(key=lambda item: (str(item.get("decision_id") or ""), int(_safe_float(item.get("workbook_row"), 99999))))
    return out


def _prompt_decision_context(decisions: list[dict[str, Any]], *, limit: int = 24) -> list[dict[str, Any]]:
    context: list[dict[str, Any]] = []
    for row in _dedupe_expected_decisions(decisions):
        bucket = _clean_text(row.get("template_bucket")).lower()
        line_kind = _clean_text(row.get("line_item_kind")).lower()
        if bucket in PROMPT_CONTEXT_EXCLUDED_BUCKETS or line_kind in PROMPT_CONTEXT_EXCLUDED_KINDS:
            continue
        item = {
            "decision_id": row.get("decision_id"),
            "template_bucket": row.get("template_bucket"),
            "line_item_kind": row.get("line_item_kind"),
            "workbook_row": row.get("workbook_row"),
            "resolved_item_name": row.get("resolved_item_name") or row.get("selected_item_name"),
        }
        context.append({key: value for key, value in item.items() if _value_present(value)})
        if len(context) >= limit:
            break
    return context


def _expected_decisions_from_rows(group: pd.DataFrame, template_type: str) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    rows = _ensure_columns(
        group,
        [
            "template_row_id",
            "template_bucket",
            "line_item_kind",
            "row_number",
            "selector_code",
            "resolved_item_name",
            "selected_item_name",
            "area_sqft",
            "quantity",
            "thickness_inches",
            "yield_or_coverage",
            "yield_factor",
            "estimated_units",
            "estimated_sets",
            "gal_per_100_sqft",
            "gal_per_sqft",
            "warranty_years",
            "wet_mils_estimate",
            "waste_factor_pct",
            "days",
            "crew_size",
            "total_hours",
            "unit_price",
            "estimated_cost",
        ],
    )
    for _, row in rows.iterrows():
        bucket = _clean_text(row.get("template_bucket"))
        if not bucket or bucket == "unknown":
            continue
        if not _row_has_decision(row):
            continue
        line_kind = _lower(row.get("line_item_kind"))
        payload = {
            "decision_id": _decision_id_for_row(template_type, bucket, line_kind),
            "template_bucket": bucket,
            "line_item_kind": line_kind,
            "workbook_row": int(row["row_number"]) if pd.notna(row.get("row_number")) else None,
            "template_row_id": row.get("template_row_id"),
            "expected_include": True,
        }
        for field in DECISION_FIELD_CANDIDATES + ("area_sqft", "quantity", "estimated_units", "estimated_sets"):
            if field in row.index:
                value = row.get(field)
                if value not in (None, "") and not (isinstance(value, float) and math.isnan(value)):
                    payload[field] = value
        decisions.append(payload)
    return _dedupe_expected_decisions(decisions)


def select_historical_candidates(
    data: EstimatorData | Any,
    *,
    template_types: list[str] | tuple[str, ...] = ("roofing", "insulation"),
    limit: int = 10,
    min_decision_count: int = 2,
    seed: int = 0,
) -> list[dict[str, Any]]:
    rows = _frame(data, "template_rows")
    if rows.empty:
        return []
    rows = _merge_job_context(rows, data)
    rows = _ensure_columns(
        rows,
        [
            "job_id",
            "source_file",
            "template_type",
            "division",
            "customer",
            "job_name",
            "site_address",
            "address",
            "template_bucket",
            "line_item_kind",
            "row_number",
            "area_sqft",
            "estimated_sqft",
            "quantity",
            *DECISION_FIELD_CANDIDATES,
        ],
    )
    wanted = {_normalize_template_type(value) for value in template_types}
    rows["template_type_normalized"] = rows["template_type"].map(_normalize_template_type)
    rows = rows[rows["template_type_normalized"].isin(wanted)].copy()
    if rows.empty:
        return []
    rows["_has_decision"] = rows.apply(_row_has_decision, axis=1)
    grouped_columns = ["template_type_normalized", "job_id", "source_file"]
    candidates: list[dict[str, Any]] = []
    rng = random.Random(seed)
    for (template_type, job_id, source_file), group in rows.groupby(grouped_columns, dropna=False):
        decision_rows = group[group["_has_decision"]]
        decision_count = int(decision_rows["template_bucket"].fillna("").astype(str).str.strip().ne("").sum())
        if decision_count < min_decision_count:
            continue
        area = _area_from_group(group)
        expected_decisions = _expected_decisions_from_rows(decision_rows, str(template_type))
        if not expected_decisions:
            continue
        unique_buckets = {str(row.get("template_bucket") or "") for row in expected_decisions}
        labor_count = sum(1 for row in expected_decisions if str(row.get("line_item_kind") or "") == "labor")
        selector_count = sum(1 for row in expected_decisions if str(row.get("selector_code") or "").strip())
        score = decision_count * 10 + len(unique_buckets) * 6 + selector_count * 4 + labor_count * 3 + (5 if area > 0 else 0)
        first = group.iloc[0]
        candidates.append(
            {
                "candidate_id": _slug(f"{template_type}_{job_id}_{source_file}")[:90],
                "template_type": str(template_type),
                "division": "Insulation" if template_type == "insulation" else "Roofing",
                "job_id": _clean_text(job_id),
                "source_file": _clean_text(source_file),
                "customer": _clean_text(first.get("customer")),
                "job_name": _clean_text(first.get("job_name")),
                "site_address": _clean_text(first.get("site_address") or first.get("address")),
                "area_sqft": area,
                "decision_count": decision_count,
                "selector_count": selector_count,
                "labor_count": labor_count,
                "template_buckets": sorted(bucket for bucket in unique_buckets if bucket),
                "expected_decisions": expected_decisions,
                "score": score + rng.random(),
            }
        )
    if not candidates:
        return []
    candidates.sort(key=lambda item: (-float(item["score"]), item["candidate_id"]))
    selected: list[dict[str, Any]] = []
    by_type = {template_type: [item for item in candidates if item["template_type"] == template_type] for template_type in wanted}
    per_type = max(1, limit // max(1, len(wanted)))
    for template_type in sorted(wanted):
        selected.extend(by_type.get(template_type, [])[:per_type])
    selected_ids = {item["candidate_id"] for item in selected}
    for candidate in candidates:
        if len(selected) >= limit:
            break
        if candidate["candidate_id"] not in selected_ids:
            selected.append(candidate)
            selected_ids.add(candidate["candidate_id"])
    return selected[:limit]


def _rectangle_for_area(area: float) -> dict[str, Any]:
    if area <= 0:
        area = 10000.0
    deduction = 0.0
    target_gross = area
    if area >= 1500:
        deduction = round(min(500.0, max(100.0, area * 0.015)), 2)
        target_gross = area + deduction
    length = max(20.0, round(math.sqrt(target_gross * 1.5) / 5) * 5)
    width = round(target_gross / length, 2)
    gross = round(length * width, 2)
    deduction = round(max(gross - area, 0.0), 2)
    net = round(gross - deduction, 2)
    formula = f"{_format_generated_number(length)} ft x {_format_generated_number(width)} ft"
    if deduction:
        formula = (
            f"gross roof: {formula}; deduct {_format_generated_number(deduction)} sq ft for curbs/equipment; "
            f"net {_format_generated_number(net)} sq ft"
        )
    return {
        "length_ft": length,
        "width_ft": width,
        "gross_area_sqft": gross,
        "deduction_area_sqft": deduction,
        "net_area_sqft": net,
        "formula": formula,
    }


def _insulation_surfaces_for_area(area: float) -> dict[str, Any]:
    if area <= 0:
        area = 3000.0
    deduction = 200.0 if area >= 1500 else 0.0
    gross_target = area + deduction
    walls = round(gross_target * 0.58, 2)
    ceiling = round(gross_target - walls, 2)
    length = 60.0
    width = round(ceiling / length, 2) if ceiling > 0 else 40.0
    perimeter = 2 * (length + width)
    wall_height = round(walls / perimeter, 2) if perimeter > 0 else 12.0
    gross_wall_area = round(perimeter * wall_height, 2)
    ceiling_area = round(length * width, 2)
    gross_area = round(gross_wall_area + ceiling_area, 2)
    if not deduction:
        ceiling_area = round(max(area - gross_wall_area, 0.0), 2)
        gross_area = round(gross_wall_area + ceiling_area, 2)
    deduction = round(max(gross_area - area, 0.0), 2)
    net = round(area, 2)
    formula = (
        f"walls: 2 x ({_format_generated_number(length)} + {_format_generated_number(width)}) x "
        f"{_format_generated_number(wall_height)}; ceiling: {_format_generated_number(length)} x "
        f"{_format_generated_number(width)}"
    )
    if deduction:
        formula = (
            f"{formula}; deduct {_format_generated_number(deduction)} sq ft for doors/openings; "
            f"net {_format_generated_number(net)} sq ft"
        )
    return {
        "building_length_ft": length,
        "building_width_ft": width,
        "wall_height_ft": wall_height,
        "gross_area_sqft": gross_area,
        "gross_wall_area_sqft": gross_wall_area,
        "ceiling_area_sqft": ceiling_area,
        "deduction_area_sqft": deduction,
        "net_area_sqft": net,
        "formula": formula,
        "surfaces": [
            {"surface_type": "walls", "gross_area_sqft": gross_wall_area, "deduction_area_sqft": deduction, "net_area_sqft": round(gross_wall_area - deduction, 2)},
            {"surface_type": "ceiling", "gross_area_sqft": ceiling_area, "deduction_area_sqft": 0.0, "net_area_sqft": ceiling_area},
        ],
    }


def _first_decision_value(decisions: list[dict[str, Any]], *fields: str, buckets: set[str] | None = None) -> Any:
    for decision in decisions:
        if buckets and str(decision.get("template_bucket") or "") not in buckets:
            continue
        for field in fields:
            value = decision.get(field)
            if value not in (None, ""):
                return value
    return None


def build_case_facts(candidate: dict[str, Any], *, seed: int = 0) -> dict[str, Any]:
    template_type = str(candidate.get("template_type") or "")
    decisions = list(candidate.get("expected_decisions") or [])
    area = _safe_float(candidate.get("area_sqft"), 0.0)
    case_id = _slug(f"hist_{template_type}_{candidate.get('job_id') or candidate.get('candidate_id')}")[:80]
    if template_type == "insulation":
        area_trace = _insulation_surfaces_for_area(area)
        foam_name = _first_decision_value(decisions, "resolved_item_name", "selected_item_name", buckets={"foam"})
        thickness = _first_decision_value(decisions, "thickness_inches", buckets={"foam"})
        explicit_note_facts = {
            "project_type": "spray foam insulation",
            "building_type": "metal building",
            "dimensions": area_trace["formula"],
            "area_sqft": area_trace["net_area_sqft"],
        }
        inference_clues = [
            "The customer is asking about insulating walls and ceiling/roof underside.",
            "Mention closed-cell if the historical foam choice or density suggests 2 lb foam.",
            "Include a practical clue about access, timing, or masking if labor/logistics decisions exist.",
        ]
        if thickness:
            inference_clues.append(f"Hint at desired performance that could lead to about {float(thickness):g} inches, without saying every workbook input.")
        hidden = {"historical_foam_product": foam_name, "historical_thickness_inches": thickness}
        expected_scope = {
            "project_type_contains": ["insulation", "foam"],
            "estimated_sqft": area_trace["net_area_sqft"],
        }
    else:
        area_trace = _rectangle_for_area(area)
        coating = _first_decision_value(decisions, "resolved_item_name", "selected_item_name", buckets={"coating"})
        warranty = _first_decision_value(decisions, "warranty_years", buckets={"coating"}) or _first_decision_value(decisions, "warranty_years")
        explicit_note_facts = {
            "project_type": "roof coating/restoration",
            "substrate": "metal roof unless source context says otherwise",
            "dimensions": area_trace["formula"],
            "area_sqft": area_trace["net_area_sqft"],
        }
        inference_clues = [
            "Use condition clues such as rusted fasteners, open seams, ponding, or failed coating to trigger estimator decisions.",
            "Mention access/penetration complexity when equipment or labor decisions exist.",
            "Imply coating chemistry or warranty need naturally, without listing every workbook selector.",
        ]
        hidden = {"historical_coating_system": coating, "historical_warranty_years": warranty}
        expected_scope = {
            "project_type_contains": ["roof", "coating"],
            "estimated_sqft": area_trace["net_area_sqft"],
        }
        if warranty:
            expected_scope["warranty_years"] = warranty
    protected_numbers = [
        {"field": "net_area_sqft", "value": area_trace["net_area_sqft"]},
    ]
    return {
        "case_id": case_id,
        "source_job_id": candidate.get("job_id"),
        "source_file": candidate.get("source_file"),
        "template_type": template_type,
        "division": candidate.get("division"),
        "customer": candidate.get("customer"),
        "job_name": candidate.get("job_name"),
        "site_address": candidate.get("site_address"),
        "deterministic_facts": {
            "explicit_note_facts": explicit_note_facts,
            "hidden_source_decisions": hidden,
            "decision_count": candidate.get("decision_count"),
            "template_buckets": candidate.get("template_buckets") or [],
        },
        "area_trace": area_trace,
        "expected_scope_fields": expected_scope,
        "expected_decisions": decisions,
        "expected_workbook_rows": sorted(
            {
                int(row["workbook_row"])
                for row in decisions
                if row.get("workbook_row") not in (None, "") and _safe_float(row.get("workbook_row"), -1) >= 0
            }
        ),
        "inference_clues": inference_clues,
        "protected_numbers": protected_numbers,
        "seed": seed,
    }


def build_ai_case_prompt(case_facts: dict[str, Any]) -> str:
    payload = {
        "role": "You are writing realistic Spray-Tec estimator notes for live testing.",
        "task": (
            "Write messy but plausible field notes/email text. Include deterministic dimensions and a few natural clues "
            "that an estimator or AI could use to infer scope decisions. Do not list hidden expected workbook decisions."
        ),
        "hard_rules": [
            "Return strict JSON with keys generated_notes, note_style, included_inference_clues, warnings.",
            "Do not change deterministic dimensions, areas, customer/job/source facts, or protected numbers.",
            "Do not mention selector codes or workbook rows.",
            "Do not simply list every hidden source decision or product option.",
            "It is okay to imply decisions through condition, performance, access, timing, substrate, or customer intent.",
        ],
        "source_metadata": {
            "customer": case_facts.get("customer"),
            "job_name": case_facts.get("job_name"),
            "site_address": case_facts.get("site_address"),
            "template_type": case_facts.get("template_type"),
        },
        "explicit_note_facts": (case_facts.get("deterministic_facts") or {}).get("explicit_note_facts") or {},
        "inference_clues": case_facts.get("inference_clues") or [],
        "hidden_expected_decisions_do_not_list": (case_facts.get("deterministic_facts") or {}).get("hidden_source_decisions") or {},
        "expected_decision_summary_for_context_only": _prompt_decision_context(case_facts.get("expected_decisions") or []),
        "expected_decision_context_note": (
            "Filtered and deduped actionable estimator decisions only. Use these to shape clues, not as text to list."
        ),
    }
    return json.dumps(payload, default=_json_default, indent=2)


def _extract_json_object(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise
        payload = json.loads(match.group(0))
    return payload if isinstance(payload, dict) else {}


def _call_openai_notes(prompt: str, *, model: str, reasoning_effort: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set")
    try:
        from openai import OpenAI  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("openai package is not installed") from exc
    client = OpenAI(timeout=float(os.getenv("OPENAI_GENERATED_CASES_TIMEOUT_SECONDS", "90")))
    attempted = [model]
    try:
        response = client.responses.create(
            model=model,
            reasoning={"effort": reasoning_effort},
            input=prompt,
        )
    except Exception:
        if model != DEFAULT_GENERATED_CASE_MODEL:
            raise
        attempted.append(FALLBACK_GENERATED_CASE_MODEL)
        response = client.responses.create(
            model=FALLBACK_GENERATED_CASE_MODEL,
            reasoning={"effort": reasoning_effort},
            input=prompt,
        )
        model = FALLBACK_GENERATED_CASE_MODEL
    text = getattr(response, "output_text", "") or ""
    return _extract_json_object(text), {"model": model, "attempted_models": attempted, "reasoning_effort": reasoning_effort}


def deterministic_notes_from_facts(case_facts: dict[str, Any]) -> dict[str, Any]:
    facts = (case_facts.get("deterministic_facts") or {}).get("explicit_note_facts") or {}
    area_trace = case_facts.get("area_trace") or {}
    customer = _clean_text(case_facts.get("customer")) or "Customer"
    job_name = _clean_text(case_facts.get("job_name")) or _clean_text(case_facts.get("source_job_id"))
    address = _clean_text(case_facts.get("site_address"))
    if case_facts.get("template_type") == "insulation":
        notes = (
            f"{customer} asked for a spray foam insulation quote"
            f"{' at ' + address if address else ''}. Job reference {job_name}. "
            f"Metal building scope: {facts.get('dimensions')}. "
            "They are talking walls plus ceiling/underside and want a durable foam system. "
            "Please review foam type, thickness/R-value, masking, setup, cleanup, loading, and any travel/logistics."
        )
    else:
        notes = (
            f"{customer} roof restoration note"
            f"{' for ' + address if address else ''}. Job reference {job_name}. "
            f"Roof dimensions: {facts.get('dimensions')}. "
            "Existing metal roof has enough age and detail work that rusted fasteners, open seams, primer need, access, "
            "and coating warranty should be reviewed. Customer wants a practical coating option, not a tearoff, if conditions allow."
        )
    return {
        "generated_notes": notes,
        "note_style": "deterministic_template",
        "included_inference_clues": case_facts.get("inference_clues") or [],
        "warnings": ["AI not used; deterministic template notes generated."],
        "area_trace": area_trace,
    }


def validate_ai_case_output(ai_payload: dict[str, Any], case_facts: dict[str, Any]) -> dict[str, Any]:
    notes = _clean_text(ai_payload.get("generated_notes"))
    errors: list[str] = []
    warnings: list[str] = []
    if not notes:
        errors.append("AI output did not include generated_notes.")
    area = _safe_float((case_facts.get("area_trace") or {}).get("net_area_sqft"), 0.0)
    for match in re.finditer(r"\b(?:roof|area|building|scope|walls?|ceiling)[^.\n]{0,60}?([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:sq\s*ft|sqft|sf)\b", notes, re.I):
        if re.search(r"\b(deduct|deduction|less|minus|opening|openings|curbs?|equipment)\b", match.group(0), re.I):
            continue
        stated = _safe_float(match.group(1).replace(",", ""), 0.0)
        if area > 0 and stated > 0 and abs(stated - area) / area > 0.08:
            errors.append(f"AI-stated area {stated:g} sqft conflicts with deterministic area {area:g}.")
            break
    hidden_names = []
    for row in case_facts.get("expected_decisions") or []:
        name = _clean_text(row.get("resolved_item_name") or row.get("selected_item_name"))
        if name and len(name) > 4:
            hidden_names.append(name)
    leaked = [name for name in sorted(set(hidden_names)) if name.lower() in notes.lower()]
    if len(leaked) >= max(3, math.ceil(len(set(hidden_names)) * 0.6)):
        errors.append("Generated notes appear to list too many hidden expected product/decision names.")
    for protected in case_facts.get("protected_numbers") or []:
        value = _safe_float(protected.get("value"), 0.0)
        if value and str(int(value)) not in notes and f"{value:g}" not in notes:
            warnings.append(f"Protected value {protected.get('field')}={value:g} is not explicitly present in notes.")
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings + list(ai_payload.get("warnings") or []),
    }


def generate_notes_for_case(
    case_facts: dict[str, Any],
    *,
    use_ai: bool = False,
    model: str | None = None,
    reasoning_effort: str = DEFAULT_REASONING_EFFORT,
) -> tuple[dict[str, Any], dict[str, Any]]:
    prompt = build_ai_case_prompt(case_facts)
    if not use_ai:
        payload = deterministic_notes_from_facts(case_facts)
        metadata = {"generation_method": "deterministic_template", "prompt": prompt}
        return payload, metadata
    selected_model = model or os.getenv("OPENAI_GENERATED_CASES_MODEL") or DEFAULT_GENERATED_CASE_MODEL
    payload, metadata = _call_openai_notes(prompt, model=selected_model, reasoning_effort=reasoning_effort)
    metadata.update({"generation_method": "openai_responses", "prompt": prompt})
    return payload, metadata


def validate_generated_case(case: dict[str, Any], data: EstimatorData | Any) -> dict[str, Any]:
    notes = case.get("generated_notes") or ""
    failures: list[str] = []
    warnings: list[str] = []
    try:
        recommendation = estimate_from_field_notes(notes, {}, data=data)
        workbench = build_estimating_workbench(recommendation, data)
        draft = workbench_to_draft_workbook_inputs(workbench)
    except Exception as exc:
        return {
            "status": "failed_generation",
            "failures": [f"Estimator validation failed: {type(exc).__name__}: {exc}"],
            "warnings": warnings,
        }
    parsed = getattr(recommendation, "parsed_fields", {}) if not isinstance(recommendation, dict) else recommendation.get("parsed_fields", {})
    expected_scope = case.get("expected_scope_fields") or {}
    if expected_scope.get("estimated_sqft"):
        actual_area = _safe_float(parsed.get("estimated_sqft") or parsed.get("net_sqft") or ((parsed.get("dimension_summary") or {}).get("net_area_sqft") if isinstance(parsed.get("dimension_summary"), dict) else None), 0.0)
        expected_area = _safe_float(expected_scope.get("estimated_sqft"), 0.0)
        if expected_area and actual_area and abs(actual_area - expected_area) / expected_area > 0.12:
            warnings.append(f"Parsed area {actual_area:g} differs from generated target {expected_area:g}.")
    expected_rows = set(int(row) for row in case.get("expected_workbook_rows") or [] if _safe_float(row, -1) >= 0)
    actual_rows = {
        int(_safe_float(row.get("workbook_row"), -1))
        for row in draft.get("workbook_decisions") or []
        if isinstance(row, dict) and _safe_float(row.get("workbook_row"), -1) >= 0
    }
    decision_keys = [
        (
            str(row.get("section") or ""),
            str(row.get("decision_id") or ""),
            str(row.get("workbook_row") or ""),
        )
        for row in draft.get("workbook_decisions") or []
        if isinstance(row, dict)
    ]
    duplicate_decision_row_count = len(decision_keys) - len(set(decision_keys))
    if expected_rows:
        overlap = expected_rows & actual_rows
        if not overlap:
            warnings.append("No expected workbook rows were reproduced by decision workbench.")
        elif len(overlap) / max(1, len(expected_rows)) < 0.35:
            warnings.append("Low overlap between historical workbook rows and generated workbench rows.")
    status = "ready_for_live_test" if not failures and not warnings else "needs_review"
    return {
        "status": status,
        "failures": failures,
        "warnings": warnings,
        "parsed_scope": parsed,
        "actual_workbook_rows": sorted(actual_rows),
        "decision_count": len(draft.get("workbook_decisions") or []),
        "decision_proposal_count": len(workbench.get("decision_proposals") or []),
        "duplicate_decision_row_count": duplicate_decision_row_count + len(workbench.get("duplicate_decision_rows") or []),
    }


def generate_cases(
    data: EstimatorData | Any,
    *,
    limit: int = 10,
    template_types: list[str] | tuple[str, ...] = ("roofing", "insulation"),
    min_decision_count: int = 2,
    seed: int = 0,
    use_ai: bool = False,
    model: str | None = None,
    reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    validate: bool = True,
) -> list[dict[str, Any]]:
    candidates = select_historical_candidates(
        data,
        template_types=template_types,
        limit=limit,
        min_decision_count=min_decision_count,
        seed=seed,
    )
    cases: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, start=1):
        facts = build_case_facts(candidate, seed=seed + index)
        ai_payload, metadata = generate_notes_for_case(
            facts,
            use_ai=use_ai,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        ai_validation = validate_ai_case_output(ai_payload, facts)
        generated_notes = _clean_text(ai_payload.get("generated_notes"))
        case = {
            **facts,
            "generated_notes": generated_notes,
            "ai_generation_metadata": metadata,
            "ai_generation_warnings": ai_validation.get("warnings") or [],
            "ai_generation_errors": ai_validation.get("errors") or [],
            "promotion_status": "needs_review",
        }
        if not ai_validation.get("ok"):
            case["validation_result"] = {
                "status": "failed_generation",
                "failures": ai_validation.get("errors") or [],
                "warnings": ai_validation.get("warnings") or [],
            }
        elif validate:
            case["validation_result"] = validate_generated_case(case, data)
        else:
            case["validation_result"] = {"status": "not_validated", "failures": [], "warnings": []}
        case["promotion_status"] = case["validation_result"].get("status") or "needs_review"
        cases.append(case)
    return cases


def _case_summary_row(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": case.get("case_id"),
        "promotion_status": case.get("promotion_status"),
        "template_type": case.get("template_type"),
        "source_job_id": case.get("source_job_id"),
        "customer": case.get("customer"),
        "job_name": case.get("job_name"),
        "source_file": case.get("source_file"),
        "generated_notes": case.get("generated_notes"),
        "expected_decision_count": len(case.get("expected_decisions") or []),
        "expected_workbook_rows": ",".join(str(row) for row in case.get("expected_workbook_rows") or []),
        "validation_warnings": "; ".join(case.get("validation_result", {}).get("warnings") or []),
        "validation_failures": "; ".join(case.get("validation_result", {}).get("failures") or []),
    }


def write_generated_case_outputs(cases: list[dict[str, Any]], out_dir: str | Path) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    jsonl_path = out / "generated_live_cases.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(_json_dumps(case) + "\n")
    eval_cases = [
        {
            "case_id": case.get("case_id"),
            "notes": case.get("generated_notes"),
            "expected": case.get("expected_scope_fields") or {},
            "metadata": {
                "source_job_id": case.get("source_job_id"),
                "source_file": case.get("source_file"),
                "promotion_status": case.get("promotion_status"),
            },
        }
        for case in cases
    ]
    eval_path = out / "eval_candidate_cases.json"
    eval_path.write_text(json.dumps(eval_cases, indent=2, default=_json_default), encoding="utf-8")
    xlsx_path = out / "generated_live_cases.xlsx"
    with pd.ExcelWriter(xlsx_path) as writer:
        pd.DataFrame([_case_summary_row(case) for case in cases]).to_excel(writer, sheet_name="Cases", index=False)
        pd.DataFrame(
            [{"case_id": case.get("case_id"), "generated_notes": case.get("generated_notes")} for case in cases]
        ).to_excel(writer, sheet_name="Generated Notes", index=False)
        decision_rows = []
        area_rows = []
        validation_rows = []
        warning_rows = []
        for case in cases:
            case_id = case.get("case_id")
            for row in case.get("expected_decisions") or []:
                decision_rows.append({"case_id": case_id, **row})
            area_rows.append({"case_id": case_id, **(case.get("area_trace") or {})})
            validation_rows.append({"case_id": case_id, **(case.get("validation_result") or {})})
            for warning in (case.get("ai_generation_warnings") or []) + ((case.get("validation_result") or {}).get("warnings") or []):
                warning_rows.append({"case_id": case_id, "warning": warning})
        pd.DataFrame(decision_rows).to_excel(writer, sheet_name="Expected Decisions", index=False)
        pd.DataFrame(area_rows).to_excel(writer, sheet_name="Area Trace", index=False)
        pd.DataFrame(validation_rows).to_excel(writer, sheet_name="Validation Results", index=False)
        pd.DataFrame([_case_summary_row(case) for case in cases if case.get("promotion_status") == "ready_for_live_test"]).to_excel(
            writer,
            sheet_name="Promotion Candidates",
            index=False,
        )
        pd.DataFrame(warning_rows).to_excel(writer, sheet_name="Generation Warnings", index=False)
    cases_dir = out / "cases"
    cases_dir.mkdir(exist_ok=True)
    for case in cases:
        case_dir = cases_dir / _slug(case.get("case_id"))
        case_dir.mkdir(exist_ok=True)
        (case_dir / "notes.txt").write_text(case.get("generated_notes") or "", encoding="utf-8")
        (case_dir / "source_decisions.json").write_text(
            json.dumps(case.get("expected_decisions") or [], indent=2, default=_json_default),
            encoding="utf-8",
        )
        (case_dir / "validation.json").write_text(
            json.dumps(case.get("validation_result") or {}, indent=2, default=_json_default),
            encoding="utf-8",
        )
        (case_dir / "ai_prompt.txt").write_text((case.get("ai_generation_metadata") or {}).get("prompt") or "", encoding="utf-8")
        (case_dir / "ai_output.json").write_text(
            json.dumps(
                {
                    "warnings": case.get("ai_generation_warnings") or [],
                    "errors": case.get("ai_generation_errors") or [],
                    "metadata": case.get("ai_generation_metadata") or {},
                },
                indent=2,
                default=_json_default,
            ),
            encoding="utf-8",
        )
    return {"jsonl": jsonl_path, "xlsx": xlsx_path, "eval_candidates": eval_path, "cases_dir": cases_dir}


def _parse_template_types(value: str) -> list[str]:
    return [_normalize_template_type(part) for part in value.split(",") if part.strip()]


def main(argv: list[str] | None = None) -> int:
    load_project_env()
    parser = argparse.ArgumentParser(description="Generate live-review Estimating Assistant cases from historical estimates.")
    parser.add_argument("--db-url", default=os.getenv("NEON_DATABASE_URL") or "", help="Estimator database URL.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUTPUT_DIR), help="Output directory.")
    parser.add_argument("--limit", type=int, default=10, help="Maximum generated cases.")
    parser.add_argument("--template-types", default="roofing,insulation", help="Comma-separated template types.")
    parser.add_argument("--use-ai", action="store_true", help="Use OpenAI Responses API for note generation.")
    parser.add_argument("--model", default=os.getenv("OPENAI_GENERATED_CASES_MODEL") or DEFAULT_GENERATED_CASE_MODEL)
    parser.add_argument("--reasoning-effort", default=os.getenv("OPENAI_GENERATED_CASES_REASONING_EFFORT") or DEFAULT_REASONING_EFFORT)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true", help="Generate deterministic notes without OpenAI.")
    parser.add_argument("--min-decision-count", type=int, default=2)
    parser.add_argument("--skip-validation", action="store_true", help="Skip estimator/workbench validation.")
    args = parser.parse_args(argv)

    if not args.db_url:
        raise SystemExit("--db-url is required unless tests provide a monkeypatched data loader.")
    data = load_estimator_data(database_url=args.db_url, prefer_database=True)
    cases = generate_cases(
        data,
        limit=args.limit,
        template_types=_parse_template_types(args.template_types),
        min_decision_count=args.min_decision_count,
        seed=args.seed,
        use_ai=bool(args.use_ai and not args.dry_run),
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        validate=not args.skip_validation,
    )
    paths = write_generated_case_outputs(cases, args.out_dir)
    status_counts = pd.Series([case.get("promotion_status") for case in cases]).value_counts().to_dict() if cases else {}
    print(f"Generated {len(cases)} live-review estimator cases")
    for label, path in paths.items():
        print(f"  {label}: {path}")
    print(f"  statuses: {status_counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
