from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd

from .foam_yield_history import build_foam_yield_history_digest
from .schemas import EstimatorData


DEFAULT_CHAT_ESTIMATOR_MODEL = "gpt-4o"
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
    deterministic_baseline = deterministic_chat_fallback(message_list, template_type_hint=template_type_hint)
    baseline_scope = _merge_chat_scopes(existing_scope or {}, deterministic_baseline.scope_overrides)
    context = estimator_context_summary(data, scope=baseline_scope)
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
            return normalize_chat_payload(
                payload,
                source="ai_chat",
                baseline_scope=baseline_scope,
                baseline_notes=deterministic_baseline.estimator_notes,
            )
        except Exception as exc:
            deterministic_baseline.warnings.append(f"AI estimator chat failed; used deterministic fallback. {type(exc).__name__}: {exc}")
            return deterministic_baseline
    deterministic_baseline.warnings.append("OPENAI_API_KEY is not configured; used deterministic estimator-chat fallback.")
    return deterministic_baseline


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
            "decision_id": "roofing_board_stock_row_58",
            "section": "roofing_board_fastener_template_decisions",
            "template_bucket": "board_stock",
            "workbook_row": "58",
            "label": "Board stock",
            "editable_fields": ["include", "basis_sqft", "thickness_inches", "price_per_square"],
            "formula_requirements": ["basis_sqft", "price_per_square"],
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
            "decision_id": "roofing_labor_base_row_122",
            "section": "roofing_labor_template_decisions",
            "template_bucket": "labor_base",
            "workbook_row": "122",
            "label": "Base roofing labor",
            "editable_fields": ["include", "days", "crew_size", "daily_rate", "hourly_rate", "total_hours"],
            "formula_requirements": ["daily_rate and days", "or total_hours and hourly_rate"],
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


def estimator_context_summary(data: EstimatorData | None, *, scope: dict[str, Any] | None = None) -> dict[str, Any]:
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
    ) if template_type == "insulation" else []
    summary["pricing_candidates_by_bucket"] = _pricing_candidates_by_bucket(data, template_type=template_type)
    summary["product_guidance_digest"] = _product_guidance_digest(data, template_type=template_type)
    summary["companion_relationships"] = _companion_relationships(data, template_type=template_type)
    summary["reference_job_decisions"] = _reference_job_decisions(data, scope=scope, template_type=template_type)
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
        "foam_yield_history_digest": [],
        "pricing_candidates_by_bucket": [],
        "product_guidance_digest": [],
        "companion_relationships": [],
        "reference_job_decisions": [],
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
    decision_preferences = _clean_decision_preferences(
        payload.get("workbook_decision_preferences")
        or payload.get("decision_patches")
        or payload.get("row_updates")
        or payload.get("workbook_row_updates")
    )
    return EstimatorChatResult(
        assistant_message=assistant_message,
        estimator_notes=notes,
        scope_overrides=_merge_chat_scopes(baseline_scope or {}, _clean_scope(scope)),
        workbook_decision_preferences=decision_preferences,
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
        "When historical/template context supports a normal choice, make the best reviewed guess instead of leaving the decision blank; "
        "set review_required true, lower confidence, and explain the evidence if the prompt did not explicitly confirm it. "
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
        "Use proposed_values for editable workbook fields such as basis_sqft, thickness_inches, gal_per_100_sqft, unit_price, "
        "estimated_units, linear_ft, days, hours_per_day, people_count, trip_count, crew_size, daily_rate, hourly_rate, "
        "total_hours, editable_total_hours, and formula_mode. "
        "For insulation jobs, include Loading and Traveling as normal checked logistics expense decisions unless evidence says otherwise; "
        "fill hours_per_day, people_count, trip_count, and unit_price from history or reasonable reviewed assumptions. "
        "For insulation foam yield_or_coverage, prefer foam_yield_history_digest entries matching foam type, product/template option, "
        "and thickness band; include that evidence and set review_required when the historical range is wide or evidence is thin. "
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
