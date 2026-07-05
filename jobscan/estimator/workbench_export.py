from __future__ import annotations

import json
import shutil
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .evidence_export import sanitize_for_export
from .workbench import recalculate_workbench_tables, summarize_workbench_totals, workbench_to_draft_workbook_inputs

DEFAULT_WORKBENCH_EXPORT_DIR = Path("output/estimator_workbench_exports")
EXCEL_CELL_LIMIT = 32000
PROPOSAL_EVIDENCE_COLUMNS = [
    "decision_evidence_summary",
    "proposal_source",
    "proposal_confidence",
    "proposal_review_required",
    "proposal_review_reasons",
    "proposal_evidence",
]

INSULATION_DECISION_SECTION_COLUMNS = {
    "insulation_detail_material_template_decisions": [
        "include",
        "workbook_row",
        "template_line",
        "editable_selector_code",
        "resolved_template_option",
        "basis_sqft",
        "linear_ft",
        "quantity",
        "feet_per_unit",
        "unit_price",
        "estimated_units",
        "estimated_cost",
        "selected_pricing_candidate",
        "compatibility_status",
        "compatibility_warnings",
        "product_guidance",
        "notes",
    ],
    "insulation_thermal_barrier_template_decisions": [
        "include",
        "workbook_row",
        "template_line",
        "editable_selector_code",
        "resolved_template_option",
        "basis_sqft",
        "gal_per_100_sqft",
        "waste_factor_pct",
        "unit_price",
        "estimated_gallons",
        "estimated_cost",
        "selected_pricing_candidate",
        "compatibility_status",
        "compatibility_warnings",
        "product_guidance",
        "notes",
    ],
    "insulation_support_material_template_decisions": [
        "include",
        "workbook_row",
        "template_line",
        "editable_selector_code",
        "resolved_template_option",
        "quantity",
        "estimated_drums",
        "unit_price",
        "estimated_units",
        "estimated_cost",
        "selected_pricing_candidate",
        "compatibility_status",
        "compatibility_warnings",
        "product_guidance",
        "notes",
    ],
    "insulation_equipment_logistics_template_decisions": [
        "include",
        "workbook_row",
        "template_line",
        "editable_selector_code",
        "resolved_template_option",
        "days",
        "period",
        "trip_count",
        "round_trip_miles",
        "unit_price",
        "margin_pct",
        "estimated_units",
        "estimated_cost",
        "selected_pricing_candidate",
        "compatibility_status",
        "compatibility_warnings",
        "notes",
    ],
    "insulation_compliance_template_decisions": [
        "include",
        "workbook_row",
        "template_line",
        "resolved_template_option",
        "quantity",
        "unit_price",
        "estimated_units",
        "estimated_cost",
        "compatibility_status",
        "compatibility_warnings",
        "notes",
    ],
    "insulation_labor_template_decisions": [
        "include",
        "workbook_row",
        "labor_task",
        "days",
        "crew_size",
        "daily_rate",
        "hourly_rate",
        "total_hours",
        "formula_mode",
        "estimated_cost",
        "compatibility_status",
        "compatibility_warnings",
        "notes",
    ],
    "insulation_pricing_template_decisions": [
        "include",
        "workbook_row",
        "template_line",
        "resolved_template_option",
        "quantity",
        "unit_price",
        "margin_pct",
        "estimated_cost",
        "compatibility_status",
        "compatibility_warnings",
        "notes",
    ],
}


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


def _safe_filename_part(value: Any, fallback: str = "workbench") -> str:
    text = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(value or "").strip())
    text = "_".join(part for part in text.split("_") if part)
    return (text or fallback)[:80]


def _truncate_excel_text(value: str) -> str:
    if len(value) <= EXCEL_CELL_LIMIT:
        return value
    suffix = "... [truncated for Excel]"
    return value[: max(EXCEL_CELL_LIMIT - len(suffix), 0)] + suffix


def _excel_value(value: Any) -> Any:
    value = sanitize_for_export(value, excel=True)
    if isinstance(value, (dict, list, tuple, set)):
        return _truncate_excel_text(json.dumps(value, sort_keys=True, default=str))
    if isinstance(value, str):
        return _truncate_excel_text(value)
    return value


def _json_payload(value: Any) -> Any:
    return sanitize_for_export(value, excel=False)


def _table_rows(records: Any) -> list[dict[str, Any]]:
    if records is None:
        return []
    if isinstance(records, pd.DataFrame):
        records = records.to_dict(orient="records")
    if isinstance(records, dict):
        return [{"field": key, "value": value} for key, value in records.items()]
    if isinstance(records, list):
        rows = []
        for item in records:
            if isinstance(item, dict):
                rows.append(item)
            else:
                rows.append({"value": item})
        return rows
    return [{"value": records}]


def _excel_frame(records: Any) -> pd.DataFrame:
    rows = _table_rows(records)
    cleaned = [{str(key): _excel_value(value) for key, value in row.items()} for row in rows]
    return pd.DataFrame(cleaned)


def _write_xlsx(path: Path, sheets: dict[str, Any]) -> None:
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet_name, rows in sheets.items():
            frame = _excel_frame(rows)
            safe_sheet = sheet_name[:31]
            frame.to_excel(writer, sheet_name=safe_sheet, index=False)


def _compact_rows(rows: list[dict[str, Any]], columns: list[str]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    requested_columns = list(dict.fromkeys([*columns, *PROPOSAL_EVIDENCE_COLUMNS]))
    for row in rows or []:
        compact.append({column: row.get(column) for column in requested_columns if column in row})
    return compact


def _decision_trace_rows(
    performance_specs: list[dict[str, Any]] | None = None,
    foam_template_decisions: list[dict[str, Any]] | None = None,
    roofing_foam_template_decisions: list[dict[str, Any]] | None = None,
    roofing_coating_template_decisions: list[dict[str, Any]] | None = None,
    roofing_primer_template_decisions: list[dict[str, Any]] | None = None,
    roofing_detail_template_decisions: list[dict[str, Any]] | None = None,
    roofing_detail_quantity_template_decisions: list[dict[str, Any]] | None = None,
    roofing_board_fastener_template_decisions: list[dict[str, Any]] | None = None,
    roofing_granules_template_decisions: list[dict[str, Any]] | None = None,
    roofing_equipment_template_decisions: list[dict[str, Any]] | None = None,
    roofing_travel_freight_template_decisions: list[dict[str, Any]] | None = None,
    roofing_accessory_template_decisions: list[dict[str, Any]] | None = None,
    roofing_labor_template_decisions: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for section, section_rows in (
        ("Roofing SPF Foam", roofing_foam_template_decisions or []),
        ("Roof Coating System", roofing_coating_template_decisions or []),
        ("Roofing Primer System", roofing_primer_template_decisions or []),
        ("Roofing Fabric / Sealant System", roofing_detail_template_decisions or []),
        ("Roofing Detail Quantities", roofing_detail_quantity_template_decisions or []),
        ("Roofing Board / Fasteners", roofing_board_fastener_template_decisions or []),
        ("Roofing Granules System", roofing_granules_template_decisions or []),
        ("Roofing Equipment / Dumpster", roofing_equipment_template_decisions or []),
        ("Roofing Travel / Freight", roofing_travel_freight_template_decisions or []),
        ("Roofing Accessories / Support", roofing_accessory_template_decisions or []),
        ("Roofing Labor Planning", roofing_labor_template_decisions or []),
        ("Insulation Foam Template", foam_template_decisions or []),
        ("Insulation Performance", performance_specs or []),
    ):
        for row in section_rows or []:
            rows.append(
                {
                    "section": section,
                    "include": row.get("include"),
                    "decision_id": row.get("decision_id") or row.get("package_key") or row.get("adder_key"),
                    "template_bucket": row.get("template_bucket") or row.get("package_key") or row.get("adder_key"),
                    "workbook_row": row.get("workbook_row"),
                    "item_or_task": row.get("resolved_template_option") or row.get("surface") or row.get("item_name") or row.get("labor_package") or row.get("adder") or row.get("package"),
                    "historical_recommendation": row.get("historical_recommendation"),
                    "editable_value": row.get("editable_value") or row.get("editable_decision_value"),
                    "calculated_output": row.get("calculated_output_summary") or row.get("calculated_output"),
                    "estimated_cost": row.get("estimated_cost"),
                    "evidence_count": row.get("decision_evidence_count") or row.get("evidence_count"),
                    "confidence": row.get("decision_confidence") or row.get("confidence"),
                    "decision_evidence_summary": row.get("decision_evidence_summary"),
                    "proposal_source": row.get("proposal_source"),
                    "proposal_confidence": row.get("proposal_confidence"),
                    "proposal_review_required": row.get("proposal_review_required"),
                    "proposal_review_reasons": row.get("proposal_review_reasons"),
                    "row_traceability": row.get("row_traceability"),
                    "notes": row.get("notes"),
                }
            )
    return rows


def _product_guidance_rows(
    performance_specs: list[dict[str, Any]] | None = None,
    foam_template_decisions: list[dict[str, Any]] | None = None,
    roofing_foam_template_decisions: list[dict[str, Any]] | None = None,
    roofing_coating_template_decisions: list[dict[str, Any]] | None = None,
    roofing_primer_template_decisions: list[dict[str, Any]] | None = None,
    roofing_detail_template_decisions: list[dict[str, Any]] | None = None,
    roofing_detail_quantity_template_decisions: list[dict[str, Any]] | None = None,
    roofing_board_fastener_template_decisions: list[dict[str, Any]] | None = None,
    roofing_granules_template_decisions: list[dict[str, Any]] | None = None,
    roofing_equipment_template_decisions: list[dict[str, Any]] | None = None,
    roofing_travel_freight_template_decisions: list[dict[str, Any]] | None = None,
    roofing_accessory_template_decisions: list[dict[str, Any]] | None = None,
    roofing_labor_template_decisions: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in [
        *(roofing_foam_template_decisions or []),
        *(roofing_coating_template_decisions or []),
        *(roofing_primer_template_decisions or []),
        *(roofing_detail_template_decisions or []),
        *(roofing_detail_quantity_template_decisions or []),
        *(roofing_board_fastener_template_decisions or []),
        *(roofing_granules_template_decisions or []),
        *(roofing_equipment_template_decisions or []),
        *(roofing_travel_freight_template_decisions or []),
        *(roofing_accessory_template_decisions or []),
        *(roofing_labor_template_decisions or []),
        *(foam_template_decisions or []),
        *(performance_specs or []),
    ]:
        if not any(
            row.get(key)
            for key in (
                "product_id",
                "product_guidance",
                "product_warning_summary",
                "product_warnings",
                "product_source_documents",
                "source_evidence",
                "product_source_evidence_rows",
                "pricing_candidates",
            )
        ):
            continue
        rows.append(
            {
                "include": row.get("include"),
                "decision_id": row.get("decision_id") or row.get("package_key"),
                "workbook_row": row.get("workbook_row"),
                "package": row.get("package") or row.get("surface") or row.get("resolved_template_option"),
                "item_name": row.get("item_name") or row.get("selected_pricing_candidate"),
                "product_id": row.get("product_id"),
                "manufacturer": row.get("product_manufacturer"),
                "guidance": row.get("product_guidance"),
                "warnings": row.get("product_warning_summary") or row.get("product_warnings") or row.get("compatibility_warnings"),
                "coverage": row.get("product_coverage"),
                "source_documents": row.get("product_source_documents") or row.get("product_source_evidence"),
                "source_evidence": row.get("product_source_evidence_rows") or row.get("source_evidence"),
                "match_score": row.get("product_match_score"),
            }
        )
    return rows


def _readme_text(summary: dict[str, Any], *, workbook_path: str | Path | None = None, workbook_export_error: str | None = None) -> str:
    totals = summary.get("totals") or {}
    review_flags = summary.get("review_flags") or []
    workbook_status = "included as exported_workbook.xlsx" if workbook_path else f"not included: {workbook_export_error or 'not generated'}"
    return "\n".join(
        [
            "Estimating Assistant Review Package",
            "",
            f"Run ID: {summary.get('run_id')}",
            f"Timestamp: {summary.get('timestamp')}",
            f"Workbook: {workbook_status}",
            "",
            "Draft Totals",
            f"- Materials: {totals.get('material_total')}",
            f"- Labor: {totals.get('labor_total')}",
            f"- Adders: {totals.get('adder_total')}",
            f"- Draft Total: {totals.get('draft_total')}",
            "",
            "How to Review",
            "- Start with workbench_summary.xlsx.",
            "- Use Decision Trace to see each editable estimator decision, historical recommendation, calculated output, evidence, and workbook row.",
            "- Use Product Guidance for manufacturer guidance and warnings. Product sheets are advisory only and do not override estimator decisions.",
            "- Use Debug Decision JSON only when troubleshooting evidence, filters, or workbook writes.",
            "",
            "Review Flags",
            *(f"- {flag}" for flag in review_flags),
            "",
        ]
    )


def build_workbench_review_payloads(
    *,
    workbench: dict[str, Any],
    input_notes: str | None = None,
    runtime: dict[str, Any] | None = None,
    run_id: str | None = None,
    timestamp: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Build JSON/XLSX payloads for an estimator workbench review package."""

    recalculated = recalculate_workbench_tables(workbench)
    resolved_run_id = run_id or str(recalculated.get("estimate_id") or "workbench")
    resolved_timestamp = timestamp or datetime.now(UTC).isoformat()
    totals = summarize_workbench_totals(recalculated)
    draft_inputs = workbench_to_draft_workbook_inputs(recalculated)
    workbook_decisions = list(draft_inputs.get("workbook_decisions") or [])
    performance_specs = list(recalculated.get("insulation_performance_specs") or [])
    foam_template_decisions = list(recalculated.get("insulation_foam_template_decisions") or [])
    insulation_decision_sections = {
        "insulation_detail_material_template_decisions": list(recalculated.get("insulation_detail_material_template_decisions") or []),
        "insulation_thermal_barrier_template_decisions": list(recalculated.get("insulation_thermal_barrier_template_decisions") or []),
        "insulation_support_material_template_decisions": list(recalculated.get("insulation_support_material_template_decisions") or []),
        "insulation_equipment_logistics_template_decisions": list(recalculated.get("insulation_equipment_logistics_template_decisions") or []),
        "insulation_compliance_template_decisions": list(recalculated.get("insulation_compliance_template_decisions") or []),
        "insulation_labor_template_decisions": list(recalculated.get("insulation_labor_template_decisions") or []),
        "insulation_pricing_template_decisions": list(recalculated.get("insulation_pricing_template_decisions") or []),
    }
    roofing_foam_template_decisions = list(recalculated.get("roofing_foam_template_decisions") or [])
    roofing_coating_template_decisions = list(recalculated.get("roofing_coating_template_decisions") or [])
    roofing_primer_template_decisions = list(recalculated.get("roofing_primer_template_decisions") or [])
    roofing_detail_template_decisions = list(recalculated.get("roofing_detail_template_decisions") or [])
    roofing_detail_quantity_template_decisions = list(recalculated.get("roofing_detail_quantity_template_decisions") or [])
    roofing_board_fastener_template_decisions = list(recalculated.get("roofing_board_fastener_template_decisions") or [])
    roofing_granules_template_decisions = list(recalculated.get("roofing_granules_template_decisions") or [])
    roofing_equipment_template_decisions = list(recalculated.get("roofing_equipment_template_decisions") or [])
    roofing_travel_freight_template_decisions = list(recalculated.get("roofing_travel_freight_template_decisions") or [])
    roofing_accessory_template_decisions = list(recalculated.get("roofing_accessory_template_decisions") or [])
    roofing_labor_template_decisions = list(recalculated.get("roofing_labor_template_decisions") or [])
    area_trace = list(recalculated.get("area_calculation_trace") or [])
    area_explanation = recalculated.get("area_calculation_explanation") or ""
    decision_trace = _decision_trace_rows(
        performance_specs,
        foam_template_decisions,
        roofing_foam_template_decisions,
        roofing_coating_template_decisions,
        roofing_primer_template_decisions,
        roofing_detail_template_decisions,
        roofing_detail_quantity_template_decisions,
        roofing_board_fastener_template_decisions,
        roofing_granules_template_decisions,
        roofing_equipment_template_decisions,
        roofing_travel_freight_template_decisions,
        roofing_accessory_template_decisions,
        roofing_labor_template_decisions,
    )
    product_guidance = _product_guidance_rows(
        performance_specs,
        foam_template_decisions,
        roofing_foam_template_decisions,
        roofing_coating_template_decisions,
        roofing_primer_template_decisions,
        roofing_detail_template_decisions,
        roofing_detail_quantity_template_decisions,
        roofing_board_fastener_template_decisions,
        roofing_granules_template_decisions,
        roofing_equipment_template_decisions,
        roofing_travel_freight_template_decisions,
        roofing_accessory_template_decisions,
        roofing_labor_template_decisions,
    )
    insulation_decision_trace: list[dict[str, Any]] = []
    insulation_product_guidance: list[dict[str, Any]] = []
    for section_name, section_rows in insulation_decision_sections.items():
        readable_section = section_name.replace("_template_decisions", "").replace("insulation_", "Insulation ").replace("_", " ").title()
        for row in section_rows:
            insulation_decision_trace.append(
                {
                    "section": readable_section,
                    "include": row.get("include"),
                    "decision_id": row.get("decision_id"),
                    "template_bucket": row.get("template_bucket"),
                    "workbook_row": row.get("workbook_row"),
                    "item_or_task": row.get("resolved_template_option") or row.get("template_line") or row.get("labor_task"),
                    "historical_recommendation": row.get("historical_recommendation"),
                    "editable_value": row.get("editable_decision_value"),
                    "calculated_output": row.get("calculated_output_summary") or row.get("calculated_output"),
                    "estimated_cost": row.get("estimated_cost"),
                    "evidence_count": row.get("decision_evidence_count") or row.get("evidence_count"),
                    "confidence": row.get("decision_confidence") or row.get("confidence"),
                    "decision_evidence_summary": row.get("decision_evidence_summary"),
                    "proposal_source": row.get("proposal_source"),
                    "proposal_confidence": row.get("proposal_confidence"),
                    "proposal_review_required": row.get("proposal_review_required"),
                    "proposal_review_reasons": row.get("proposal_review_reasons"),
                    "row_traceability": row.get("row_traceability"),
                    "notes": row.get("notes"),
                }
            )
            if any(row.get(key) for key in ("product_id", "product_guidance", "product_warning_summary", "compatibility_warnings", "product_source_documents")):
                insulation_product_guidance.append(
                    {
                        "include": row.get("include"),
                        "decision_id": row.get("decision_id"),
                        "workbook_row": row.get("workbook_row"),
                        "package": row.get("template_bucket"),
                        "item_name": row.get("selected_pricing_candidate") or row.get("resolved_template_option"),
                        "product_id": row.get("product_id"),
                        "manufacturer": row.get("product_manufacturer"),
                        "guidance": row.get("product_guidance"),
                        "warnings": row.get("product_warning_summary") or row.get("compatibility_warnings"),
                        "source_documents": row.get("product_source_documents"),
                        "match_score": row.get("product_match_score"),
                    }
                )
    decision_trace.extend(insulation_decision_trace)
    product_guidance.extend(insulation_product_guidance)
    insulation_compact_columns = [
        "include",
        "workbook_row",
        "template_line",
        "labor_task",
        "editable_selector_code",
        "resolved_template_option",
        "selected_pricing_candidate",
        "basis_sqft",
        "linear_ft",
        "quantity",
        "days",
        "period",
        "trip_count",
        "round_trip_miles",
        "gal_per_100_sqft",
        "waste_factor_pct",
        "feet_per_unit",
        "unit_price",
        "margin_pct",
        "estimated_units",
        "estimated_gallons",
        "estimated_drums",
        "total_hours",
        "crew_size",
        "daily_rate",
        "hourly_rate",
        "formula_mode",
        "estimated_cost",
        "compatibility_status",
        "compatibility_warnings",
        "product_guidance",
        "notes",
    ]

    summary = {
        "run_id": resolved_run_id,
        "timestamp": resolved_timestamp,
        "input_notes": input_notes or "",
        "parsed_scope": recalculated.get("scope") or {},
        "area_calculation_explanation": area_explanation,
        "area_calculation_trace": area_trace,
        "roofing_foam_template_decisions": _compact_rows(
            roofing_foam_template_decisions,
            [
                "include",
                "workbook_row",
                "editable_selector_code",
                "resolved_template_option",
                "historical_selector_recommendation",
                "historical_selector_evidence_count",
                "basis_sqft",
                "thickness_inches",
                "yield_or_coverage",
                "unit_price",
                "estimated_units",
                "estimated_sets",
                "estimated_cost",
                "selected_pricing_candidate",
                "compatibility_status",
                "compatibility_warnings",
                "product_guidance",
                "notes",
            ],
        ),
        "roofing_coating_template_decisions": _compact_rows(
            roofing_coating_template_decisions,
            [
                "include",
                "workbook_row",
                "editable_selector_code",
                "resolved_template_option",
                "historical_selector_recommendation",
                "historical_selector_evidence_count",
                "basis_sqft",
                "gal_per_100_sqft",
                "waste_factor_pct",
                "wet_mils_estimate",
                "unit_price",
                "estimated_gallons",
                "estimated_cost",
                "selected_pricing_candidate",
                "compatibility_status",
                "compatibility_warnings",
                "product_guidance",
                "notes",
            ],
        ),
        "roofing_primer_template_decisions": _compact_rows(
            roofing_primer_template_decisions,
            [
                "include",
                "workbook_row",
                "editable_selector_code",
                "resolved_template_option",
                "historical_selector_recommendation",
                "historical_selector_evidence_count",
                "basis_sqft",
                "coverage_sqft_per_unit",
                "unit_price",
                "estimated_units",
                "estimated_cost",
                "selected_pricing_candidate",
                "compatibility_status",
                "compatibility_warnings",
                "product_guidance",
                "notes",
            ],
        ),
        "roofing_detail_template_decisions": _compact_rows(
            roofing_detail_template_decisions,
            [
                "include",
                "workbook_row",
                "editable_selector_code",
                "resolved_template_option",
                "historical_selector_recommendation",
                "historical_selector_evidence_count",
                "units",
                "linear_ft",
                "unit_price",
                "estimated_units",
                "estimated_cost",
                "selected_pricing_candidate",
                "compatibility_status",
                "compatibility_warnings",
                "product_guidance",
                "notes",
            ],
        ),
        "roofing_detail_quantity_template_decisions": _compact_rows(
            roofing_detail_quantity_template_decisions,
            [
                "include",
                "workbook_row",
                "resolved_template_option",
                "linear_ft",
                "units",
                "estimated_units",
                "amount",
                "estimated_cost",
                "compatibility_status",
                "compatibility_warnings",
                "notes",
            ],
        ),
        "roofing_board_fastener_template_decisions": _compact_rows(
            roofing_board_fastener_template_decisions,
            [
                "include",
                "workbook_row",
                "editable_selector_code",
                "resolved_template_option",
                "historical_selector_recommendation",
                "historical_selector_evidence_count",
                "basis_sqft",
                "board_area_sqft",
                "thickness_inches",
                "price_per_square",
                "unit_price_per_thousand",
                "unit_price",
                "estimated_squares",
                "estimated_units",
                "estimated_cost",
                "selected_pricing_candidate",
                "compatibility_status",
                "compatibility_warnings",
                "product_guidance",
                "notes",
            ],
        ),
        "roofing_granules_template_decisions": _compact_rows(
            roofing_granules_template_decisions,
            [
                "include",
                "workbook_row",
                "editable_selector_code",
                "resolved_template_option",
                "historical_selector_recommendation",
                "historical_selector_evidence_count",
                "basis_sqft",
                "coverage_lbs_per_100_sqft",
                "bag_weight_lbs",
                "unit_price",
                "estimated_units",
                "estimated_cost",
                "selected_pricing_candidate",
                "compatibility_status",
                "compatibility_warnings",
                "product_guidance",
                "notes",
            ],
        ),
        "roofing_equipment_template_decisions": _compact_rows(
            roofing_equipment_template_decisions,
            [
                "include",
                "workbook_row",
                "editable_selector_code",
                "resolved_template_option",
                "historical_selector_recommendation",
                "historical_selector_evidence_count",
                "basis_sqft",
                "thickness_inches",
                "size",
                "period",
                "days",
                "unit_price",
                "margin_pct",
                "estimated_units",
                "estimated_cost",
                "compatibility_status",
                "compatibility_warnings",
                "notes",
            ],
        ),
        "roofing_travel_freight_template_decisions": _compact_rows(
            roofing_travel_freight_template_decisions,
            [
                "include",
                "workbook_row",
                "resolved_template_option",
                "estimated_units",
                "amount",
                "trip_count",
                "round_trip_miles",
                "unit_price",
                "estimated_cost",
                "compatibility_status",
                "compatibility_warnings",
                "notes",
            ],
        ),
        "roofing_accessory_template_decisions": _compact_rows(
            roofing_accessory_template_decisions,
            [
                "include",
                "workbook_row",
                "editable_selector_code",
                "resolved_template_option",
                "total_coating_gallons",
                "linear_ft",
                "estimated_units",
                "amount",
                "unit_price",
                "estimated_cost",
                "compatibility_status",
                "compatibility_warnings",
                "notes",
            ],
        ),
        "roofing_labor_template_decisions": _compact_rows(
            roofing_labor_template_decisions,
            [
                "include",
                "workbook_row",
                "labor_task",
                "days",
                "crew_people_selection",
                "crew_selection",
                "selected_daily_rate_cell",
                "daily_rate",
                "hourly_rate",
                "editable_hours_per_1000_sqft",
                "total_hours",
                "formula_mode",
                "estimated_cost",
                "historical_selector_evidence_count",
                "decision_confidence",
                "compatibility_status",
                "compatibility_warnings",
                "notes",
            ],
        ),
        "insulation_foam_template_decisions": _compact_rows(
            foam_template_decisions,
            [
                "include",
                "workbook_row",
                "editable_selector_code",
                "resolved_template_option",
                "historical_selector_recommendation",
                "historical_selector_evidence_count",
                "basis_sqft",
                "thickness_inches",
                "yield_or_coverage",
                "unit_price",
                "estimated_units",
                "estimated_sets",
                "estimated_cost",
                "selected_pricing_candidate",
                "compatibility_status",
                "compatibility_warnings",
                "product_guidance",
                "notes",
            ],
        ),
        "insulation_performance_specs": _compact_rows(
            performance_specs,
            [
                "include",
                "surface",
                "application_context",
                "net_area_sqft",
                "target_r_value",
                "foam_type",
                "historical_product_decision",
                "selected_current_product",
                "product_knowledge_match",
                "alignment_status",
                "product_fit_status",
                "product_r_value_per_inch",
                "r_value_source",
                "required_thickness_inches",
                "edited_thickness_inches",
                "estimated_units",
                "estimated_sets",
                "estimated_cost",
                "product_guidance",
                "product_warnings",
                "notes",
            ],
        ),
        **{
            section_name: _compact_rows(
                section_rows,
                INSULATION_DECISION_SECTION_COLUMNS.get(section_name, insulation_compact_columns),
            )
            for section_name, section_rows in insulation_decision_sections.items()
        },
        "insulation_decisions_summary": _compact_rows(
            [row for section_rows in insulation_decision_sections.values() for row in section_rows],
            [
                "include",
                "section",
                "workbook_row",
                "template_bucket",
                "template_line",
                "labor_task",
                "resolved_template_option",
                "calculated_output_summary",
                "estimated_cost",
                "compatibility_status",
                "notes",
            ],
        ),
        "historical_filters": recalculated.get("historical_filters") or {},
        "workbook_decisions": workbook_decisions,
        "decision_trace": decision_trace,
        "product_guidance": product_guidance,
        "totals": totals,
        "review_flags": recalculated.get("review_flags") or [],
        "runtime": runtime or recalculated.get("runtime") or {},
    }

    debug = {
        "run_id": resolved_run_id,
        "timestamp": resolved_timestamp,
        "input_notes": input_notes or "",
        "workbench": recalculated,
        "area_calculation_trace": area_trace,
        "roofing_foam_template_decisions": roofing_foam_template_decisions,
        "roofing_coating_template_decisions": roofing_coating_template_decisions,
        "roofing_primer_template_decisions": roofing_primer_template_decisions,
        "roofing_detail_template_decisions": roofing_detail_template_decisions,
        "roofing_detail_quantity_template_decisions": roofing_detail_quantity_template_decisions,
        "roofing_board_fastener_template_decisions": roofing_board_fastener_template_decisions,
        "roofing_granules_template_decisions": roofing_granules_template_decisions,
        "roofing_equipment_template_decisions": roofing_equipment_template_decisions,
        "roofing_travel_freight_template_decisions": roofing_travel_freight_template_decisions,
        "roofing_accessory_template_decisions": roofing_accessory_template_decisions,
        "roofing_labor_template_decisions": roofing_labor_template_decisions,
        "insulation_foam_template_decisions": foam_template_decisions,
        "insulation_performance_specs": performance_specs,
        **insulation_decision_sections,
        "workbook_decisions": workbook_decisions,
        "decision_trace": decision_trace,
        "product_guidance": product_guidance,
        "totals": totals,
        "warnings": recalculated.get("review_flags") or [],
    }

    workbook_sheets = {
        "Parsed Scope": summary["parsed_scope"],
        "Area Explanation": [{"area_calculation_explanation": area_explanation}] if area_explanation else [],
        "Area Calculation Trace": area_trace,
        "Roofing SPF Foam": summary["roofing_foam_template_decisions"],
        "Roof Coating System": summary["roofing_coating_template_decisions"],
        "Roofing Primer System": summary["roofing_primer_template_decisions"],
        "Roofing Fabric Sealant": summary["roofing_detail_template_decisions"],
        "Roof Detail Quantities": summary["roofing_detail_quantity_template_decisions"],
        "Roof Board Fasteners": summary["roofing_board_fastener_template_decisions"],
        "Roofing Granules": summary["roofing_granules_template_decisions"],
        "Roof Equipment": summary["roofing_equipment_template_decisions"],
        "Roof Travel Freight": summary["roofing_travel_freight_template_decisions"],
        "Roof Accessories": summary["roofing_accessory_template_decisions"],
        "Roofing Labor Plan": summary["roofing_labor_template_decisions"],
        "Insulation Foam Template": summary["insulation_foam_template_decisions"],
        "Insulation Performance": summary["insulation_performance_specs"],
        "Insulation Decisions Summary": summary["insulation_decisions_summary"],
        "Insulation Details": summary["insulation_detail_material_template_decisions"],
        "Insulation Thermal Barrier": summary["insulation_thermal_barrier_template_decisions"],
        "Insulation Support Materials": summary["insulation_support_material_template_decisions"],
        "Insulation Equipment": summary["insulation_equipment_logistics_template_decisions"],
        "Insulation Compliance": summary["insulation_compliance_template_decisions"],
        "Insulation Labor Plan": summary["insulation_labor_template_decisions"],
        "Insulation Pricing": summary["insulation_pricing_template_decisions"],
        "Historical Filters": summary["historical_filters"],
        "Workbook Decisions": summary["workbook_decisions"],
        "Decision Trace": decision_trace,
        "Product Guidance": product_guidance or [{"message": "No matched product guidance in this workbench."}],
        "Totals": summary["totals"],
        "Review Flags": [{"review_flag": flag} for flag in summary["review_flags"]] or [{"review_flag": ""}],
        "Similar Jobs": recalculated.get("similar_jobs") or [],
    }

    return _json_payload(summary), _json_payload(debug), workbook_sheets


def export_workbench_review_package(
    *,
    workbench: dict[str, Any],
    input_notes: str | None = None,
    output_dir: str | Path = DEFAULT_WORKBENCH_EXPORT_DIR,
    workbook_path: str | Path | None = None,
    workbook_export_error: str | None = None,
    runtime: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> Path:
    """Create a timestamped Estimator Workbench review ZIP package."""

    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    resolved_run_id = run_id or str(workbench.get("estimate_id") or "workbench")
    stamp = _timestamp()
    folder_name = f"{stamp}_{_safe_filename_part(resolved_run_id)}"
    package_dir = output_root / folder_name
    package_dir.mkdir(parents=True, exist_ok=True)

    summary, debug, workbook_sheets = build_workbench_review_payloads(
        workbench=workbench,
        input_notes=input_notes,
        runtime=runtime,
        run_id=resolved_run_id,
        timestamp=datetime.now(UTC).isoformat(),
    )

    (package_dir / "workbench_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (package_dir / "workbench_debug.json").write_text(json.dumps(debug, indent=2, sort_keys=True), encoding="utf-8")
    (package_dir / "estimator_input.txt").write_text(input_notes or "", encoding="utf-8")
    (package_dir / "README.txt").write_text(
        _readme_text(summary, workbook_path=workbook_path, workbook_export_error=workbook_export_error),
        encoding="utf-8",
    )
    _write_xlsx(package_dir / "workbench_summary.xlsx", workbook_sheets)

    if workbook_path:
        source = Path(workbook_path)
        if source.exists():
            shutil.copyfile(source, package_dir / "exported_workbook.xlsx")
        else:
            (package_dir / "workbook_export_error.txt").write_text(f"Workbook path does not exist: {source}", encoding="utf-8")
    else:
        (package_dir / "workbook_export_error.txt").write_text(
            workbook_export_error or "Estimate workbook has not been generated in this session.",
            encoding="utf-8",
        )

    zip_path = output_root / f"{folder_name}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(package_dir.iterdir()):
            archive.write(path, arcname=path.name)
    return zip_path
