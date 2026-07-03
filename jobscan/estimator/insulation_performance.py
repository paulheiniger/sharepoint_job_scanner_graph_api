from __future__ import annotations

import math
import re
from typing import Any

from .rules import first_nonblank, to_float


def safe_number(value: Any, default: float = 0.0) -> float:
    number = to_float(value)
    if number is None or not math.isfinite(number):
        return default
    return float(number)


def optional_number(value: Any) -> float | None:
    number = to_float(value)
    if number is None or not math.isfinite(number):
        return None
    return float(number)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _norm_product(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _values_differ(left: Any, right: Any, tolerance: float = 1.0) -> bool:
    left_num = optional_number(left)
    right_num = optional_number(right)
    if left_num is not None and right_num is not None:
        return abs(left_num - right_num) > tolerance
    return bool(left not in (None, "", [], {}) and right not in (None, "", [], {}) and left != right)


def _trace_row(
    *,
    step: str,
    formula: str,
    inputs: dict[str, Any] | None = None,
    ai_value: Any = None,
    deterministic_value: Any = None,
    selected_value: Any = None,
    source_text: Any = "",
    confidence: str = "",
    notes: str = "",
) -> dict[str, Any]:
    conflict = _values_differ(ai_value, deterministic_value) if ai_value not in (None, "", [], {}) else False
    selected_source = "deterministic" if selected_value == deterministic_value else "ai" if selected_value == ai_value else "manual_or_scope"
    return {
        "include": True,
        "section": "area_calculation_trace",
        "decision_id": f"area_trace_{step}",
        "template_bucket": "area_calculation_trace",
        "step": step,
        "formula": formula,
        "inputs": inputs or {},
        "ai_value": ai_value,
        "deterministic_value": deterministic_value,
        "selected_value": selected_value,
        "selected_source": selected_source,
        "source_text": source_text,
        "confidence": confidence,
        "conflict": conflict,
        "notes": notes or ("AI value differed; deterministic math used." if conflict and selected_source == "deterministic" else ""),
    }


def _ai_value(ai_scope: dict[str, Any] | None, *fields: str) -> Any:
    ai_scope = ai_scope or {}
    for field in fields:
        value = ai_scope.get(field)
        if value not in (None, "", [], {}):
            return value
    return None


def build_area_calculation_trace(
    scope: dict[str, Any],
    *,
    ai_scope: dict[str, Any] | None = None,
    deterministic_scope: dict[str, Any] | None = None,
    merge_decisions: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return a compact audit trail for insulation area math."""

    deterministic_scope = deterministic_scope or scope
    length = first_nonblank(scope.get("building_footprint_length_ft"), scope.get("building_length_ft"))
    width = first_nonblank(scope.get("building_footprint_width_ft"), scope.get("building_width_ft"))
    wall_height = scope.get("wall_height_ft")
    gross_wall = scope.get("gross_wall_area_sqft")
    ceiling = scope.get("ceiling_area_sqft")
    gross = scope.get("gross_insulation_area_sqft") or scope.get("gross_area_sqft")
    deductions = scope.get("opening_area_known_sqft") or scope.get("deduction_area_sqft")
    net = scope.get("net_insulation_area_sqft") or scope.get("net_area_sqft") or scope.get("estimated_sqft")
    evidence = scope.get("evidence_by_field") if isinstance(scope.get("evidence_by_field"), dict) else {}
    rows = [
        _trace_row(
            step="building_footprint",
            formula="building_length_ft * building_width_ft",
            inputs={"length_ft": length, "width_ft": width},
            ai_value=_ai_value(ai_scope, "footprint_area_sqft"),
            deterministic_value=deterministic_scope.get("footprint_area_sqft") or scope.get("footprint_area_sqft"),
            selected_value=scope.get("footprint_area_sqft"),
            source_text=evidence.get("building_footprint") or scope.get("dimension_evidence") or scope.get("notes"),
            confidence="high" if length and width else "low",
        ),
        _trace_row(
            step="wall_area",
            formula="2 * (building_length_ft + building_width_ft) * wall_height_ft",
            inputs={"length_ft": length, "width_ft": width, "wall_height_ft": wall_height},
            ai_value=_ai_value(ai_scope, "gross_wall_area_sqft"),
            deterministic_value=deterministic_scope.get("gross_wall_area_sqft") or gross_wall,
            selected_value=gross_wall,
            source_text=evidence.get("wall_height_ft") or scope.get("notes"),
            confidence="high" if gross_wall else "low",
        ),
        _trace_row(
            step="ceiling_or_roof_area",
            formula="building_length_ft * building_width_ft when ceiling/roof underside is included",
            inputs={"length_ft": length, "width_ft": width, "ceiling_included": scope.get("ceiling_included")},
            ai_value=_ai_value(ai_scope, "ceiling_area_sqft"),
            deterministic_value=deterministic_scope.get("ceiling_area_sqft") or ceiling,
            selected_value=ceiling,
            source_text=scope.get("notes"),
            confidence="high" if ceiling else "low",
        ),
        _trace_row(
            step="opening_deductions",
            formula="sum(quantity * width_ft * height_ft) for known openings",
            inputs={"openings": scope.get("openings") or []},
            ai_value=_ai_value(ai_scope, "opening_area_known_sqft", "deduction_area_sqft", "deduction_sqft"),
            deterministic_value=deterministic_scope.get("opening_area_known_sqft") or deductions,
            selected_value=deductions,
            source_text=evidence.get("openings") or "",
            confidence="medium" if scope.get("opening_area_missing") else "high" if deductions is not None else "low",
            notes="One or more opening dimensions are missing." if scope.get("opening_area_missing") else "",
        ),
        _trace_row(
            step="net_insulation_area",
            formula="gross_insulation_area_sqft - opening_area_known_sqft",
            inputs={"gross_insulation_area_sqft": gross, "opening_area_known_sqft": deductions},
            ai_value=_ai_value(ai_scope, "net_insulation_area_sqft", "estimated_sqft", "net_sqft"),
            deterministic_value=deterministic_scope.get("net_insulation_area_sqft") or deterministic_scope.get("estimated_sqft") or net,
            selected_value=net,
            source_text=scope.get("notes"),
            confidence="high" if net else "low",
        ),
    ]
    for decision in merge_decisions or []:
        if decision.get("field") in {"estimated_sqft", "gross_area_sqft", "deduction_area_sqft", "net_area_sqft"}:
            rows.append(
                _trace_row(
                    step=f"ai_merge_{decision.get('field')}",
                    formula="AI scope merge guardrail",
                    ai_value=decision.get("from"),
                    deterministic_value=decision.get("to"),
                    selected_value=decision.get("to"),
                    confidence="guardrail",
                    notes=decision.get("reason") or "",
                )
            )
    return rows


def _display_number(value: Any) -> str:
    number = optional_number(value)
    if number is None:
        return ""
    if abs(number - round(number)) < 1e-9:
        return f"{round(number):,}"
    return f"{number:,.2f}".rstrip("0").rstrip(".")


def _trace_lookup(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("step") or ""): row for row in rows or [] if isinstance(row, dict)}


def _opening_summary(openings: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for opening in openings or []:
        quantity = opening.get("quantity") or 1
        opening_type = str(opening.get("opening_type") or "opening").replace("_", " ")
        width = _display_number(opening.get("width_ft"))
        height = _display_number(opening.get("height_ft"))
        area = _display_number(opening.get("known_area_sqft"))
        if width and height and area:
            parts.append(f"{quantity} {opening_type} at {width} ft x {height} ft = {area} sq ft")
        elif opening.get("missing_dimensions"):
            missing = ", ".join(str(item).replace("_ft", "") for item in opening.get("missing_dimensions") or [])
            parts.append(f"{quantity} {opening_type} missing {missing}")
    return "; ".join(parts)


def build_area_calculation_explanation(
    scope: dict[str, Any],
    *,
    trace_rows: list[dict[str, Any]] | None = None,
) -> str:
    """Return a short estimator-facing summary of the insulation area math."""

    rows = trace_rows if trace_rows is not None else build_area_calculation_trace(scope)
    by_step = _trace_lookup(rows)
    length = first_nonblank(scope.get("building_footprint_length_ft"), scope.get("building_length_ft"))
    width = first_nonblank(scope.get("building_footprint_width_ft"), scope.get("building_width_ft"))
    wall_height = scope.get("wall_height_ft")

    parts: list[str] = []
    ai_explanation = _clean_text(scope.get("area_calculation_explanation"))
    if ai_explanation:
        parts.append(ai_explanation.rstrip(".") + ".")

    footprint = by_step.get("building_footprint", {})
    if length and width and footprint.get("selected_value"):
        parts.append(
            f"Footprint: {_display_number(length)} ft x {_display_number(width)} ft = "
            f"{_display_number(footprint.get('selected_value'))} sq ft."
        )

    wall = by_step.get("wall_area", {})
    if length and width and wall_height and wall.get("selected_value"):
        parts.append(
            f"Walls: 2 x ({_display_number(length)} + {_display_number(width)}) x "
            f"{_display_number(wall_height)} ft = {_display_number(wall.get('selected_value'))} sq ft."
        )

    ceiling = by_step.get("ceiling_or_roof_area", {})
    if ceiling.get("selected_value"):
        parts.append(f"Ceiling/roof underside: {_display_number(ceiling.get('selected_value'))} sq ft.")

    openings_text = _opening_summary(scope.get("openings") or [])
    deductions = by_step.get("opening_deductions", {})
    if openings_text:
        parts.append(f"Openings: {openings_text}.")
    if deductions.get("selected_value"):
        parts.append(f"Known opening deductions: {_display_number(deductions.get('selected_value'))} sq ft.")

    net = by_step.get("net_insulation_area", {})
    if net.get("selected_value"):
        parts.append(f"Final area used: {_display_number(net.get('selected_value'))} sq ft.")

    conflicts = [str(row.get("step") or "") for row in rows or [] if row.get("conflict")]
    if conflicts:
        parts.append("AI-stated area conflicted with deterministic dimension math, so the deterministic value was used.")
    if scope.get("opening_area_missing"):
        parts.append("Some opening dimensions are still missing, so deductions may need estimator review.")

    return " ".join(part for part in parts if part).strip()


def product_alignment_for_foam_row(row: dict[str, Any]) -> dict[str, Any]:
    historical = first_nonblank(
        row.get("historical_item"),
        (row.get("decision_values") or {}).get("selected_option") if isinstance(row.get("decision_values"), dict) else "",
        row.get("recommended_decision_value"),
    )
    selected = first_nonblank(row.get("item_name"), row.get("current_item"), row.get("foam_product"))
    knowledge = first_nonblank(row.get("product_knowledge_product_name"), row.get("product_name"), row.get("product_id"))
    if not selected:
        status = "manual_review"
        note = "No selected foam product is ready for workbook export."
    elif not row.get("product_id"):
        status = "no_product_knowledge_match"
        note = "Selected foam product has no matched product data sheet guidance yet."
    elif historical and selected and _norm_product(historical) != _norm_product(selected):
        status = "different_current_item"
        note = "Historical recommendation differs from the selected/current item; estimator should confirm product choice."
    else:
        status = "aligned"
        note = "Historical recommendation, selected item, and product guidance are aligned enough for review."
    return {
        "historical_product_decision": historical,
        "selected_current_product": selected,
        "product_knowledge_match": knowledge,
        "alignment_status": status,
        "alignment_note": note,
    }


def product_fit_summary(row: dict[str, Any]) -> dict[str, Any]:
    warnings = []
    for value in row.get("product_warnings") or []:
        if value:
            warnings.append(str(value))
    for value in (row.get("product_limitations"), row.get("product_warning_summary")):
        if value:
            warnings.append(str(value))
    if not row.get("product_id"):
        warnings.append("No product knowledge match; estimator should verify product application and R-value.")
    r_value = first_nonblank(row.get("product_aged_r_value_per_inch"), row.get("product_r_value_per_inch"), row.get("product_initial_r_value_per_inch"))
    r_source = first_nonblank(
        row.get("product_aged_r_value_per_inch_source"),
        row.get("product_r_value_per_inch_source"),
        row.get("product_initial_r_value_per_inch_source"),
    )
    return {
        "product_id": row.get("product_id"),
        "manufacturer": row.get("product_manufacturer"),
        "product_name": first_nonblank(row.get("product_knowledge_product_name"), row.get("item_name")),
        "r_value_per_inch": r_value,
        "r_value_source": r_source,
        "coverage": row.get("product_coverage"),
        "recommended_use": row.get("product_recommended_use"),
        "limitations": row.get("product_limitations"),
        "warnings": list(dict.fromkeys(warnings)),
        "source_documents": row.get("product_source_documents") or [],
        "source_evidence": row.get("product_source_evidence_rows") or [],
        "fit_status": "review" if warnings else "matched" if row.get("product_id") else "no_product_knowledge_match",
    }


def build_insulation_performance_specs(
    *,
    scope: dict[str, Any],
    surface_rows: list[dict[str, Any]],
    foam_row: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    foam_row = foam_row or {}
    alignment = product_alignment_for_foam_row(foam_row)
    fit = product_fit_summary(foam_row)
    surface_outputs = {
        str(row.get("surface_type") or ""): row
        for row in foam_row.get("surface_formula_outputs") or []
        if isinstance(row, dict)
    }
    specs: list[dict[str, Any]] = []
    for surface in surface_rows or []:
        surface_type = str(surface.get("surface_type") or "")
        output = surface_outputs.get(surface_type, {})
        warnings = list(surface.get("review_flags") or []) + list(fit.get("warnings") or [])
        specs.append(
            {
                "include": surface.get("include", True),
                "section": "insulation_performance_specs",
                "decision_id": f"insulation_performance_{surface_type or 'surface'}",
                "template_bucket": "insulation_performance_spec",
                "surface": surface.get("surface"),
                "surface_type": surface_type,
                "application_context": _application_context(scope, surface),
                "net_area_sqft": surface.get("net_area_sqft"),
                "target_r_value": surface.get("target_r_value"),
                "foam_type": first_nonblank(surface.get("foam_type"), scope.get("foam_type")),
                "historical_product_decision": alignment["historical_product_decision"],
                "selected_current_product": alignment["selected_current_product"],
                "product_knowledge_match": alignment["product_knowledge_match"],
                "alignment_status": alignment["alignment_status"],
                "alignment_note": alignment["alignment_note"],
                "product_fit_status": fit["fit_status"],
                "product_id": fit.get("product_id"),
                "product_manufacturer": fit.get("manufacturer"),
                "product_knowledge_product_name": fit.get("product_name"),
                "product_r_value_per_inch": surface.get("product_r_value_per_inch") or fit.get("r_value_per_inch"),
                "r_value_source": surface.get("r_value_source"),
                "r_value_source_text": surface.get("r_value_source_text") or fit.get("r_value_source"),
                "required_thickness_inches": surface.get("required_thickness_inches"),
                "rounded_thickness_inches": surface.get("rounded_thickness_inches"),
                "edited_thickness_inches": surface.get("edited_thickness_inches"),
                "estimated_units": output.get("estimated_units"),
                "estimated_sets": output.get("estimated_sets"),
                "estimated_cost": output.get("estimated_cost"),
                "formula_output": output.get("formula_output") or {},
                "product_guidance": _product_guidance_text(fit),
                "product_warnings": list(dict.fromkeys(str(warning) for warning in warnings if warning)),
                "product_source_documents": fit.get("source_documents") or [],
                "source_evidence": fit.get("source_evidence") or [],
                "notes": _performance_note(surface, alignment, fit),
                "decision_values": {
                    "surface_type": surface_type,
                    "net_area_sqft": surface.get("net_area_sqft"),
                    "target_r_value": surface.get("target_r_value"),
                    "foam_type": first_nonblank(surface.get("foam_type"), scope.get("foam_type")),
                    "selected_product": alignment["selected_current_product"],
                    "product_r_value_per_inch": surface.get("product_r_value_per_inch") or fit.get("r_value_per_inch"),
                    "edited_thickness_inches": surface.get("edited_thickness_inches"),
                },
                "editable_decision_value": {
                    "target_r_value": surface.get("target_r_value"),
                    "edited_thickness_inches": surface.get("edited_thickness_inches"),
                    "selected_product": alignment["selected_current_product"],
                },
                "recommended_decision_value": {
                    "historical_product": alignment["historical_product_decision"],
                    "rounded_thickness_inches": surface.get("rounded_thickness_inches"),
                },
                "calculated_output": output.get("estimated_cost") or surface.get("calculated_output"),
                "calculated_output_summary": (
                    f"{surface.get('surface')}: {surface.get('net_area_sqft')} sqft, "
                    f"R{surface.get('target_r_value') or 'review'}, "
                    f"{surface.get('edited_thickness_inches') or 0} in"
                ),
            }
        )
    return specs


def _application_context(scope: dict[str, Any], surface: dict[str, Any]) -> str:
    bits = [
        first_nonblank(scope.get("building_type")),
        str(surface.get("surface") or ""),
        "roof deck underside" if surface.get("surface_type") == "roof_underside" else "",
    ]
    return " / ".join(bit for bit in bits if bit)


def _product_guidance_text(fit: dict[str, Any]) -> str:
    parts = []
    if fit.get("r_value_per_inch"):
        parts.append(f"R/in: {fit.get('r_value_per_inch')} ({fit.get('r_value_source') or 'source review'})")
    if fit.get("recommended_use"):
        parts.append(str(fit.get("recommended_use")))
    if fit.get("coverage"):
        parts.append(f"Coverage: {fit.get('coverage')}")
    if fit.get("limitations"):
        parts.append(f"Limitations: {fit.get('limitations')}")
    if fit.get("warnings"):
        parts.append("Warnings available.")
    return " ".join(parts)


def _performance_note(surface: dict[str, Any], alignment: dict[str, Any], fit: dict[str, Any]) -> str:
    note = surface.get("notes") or "Review surface performance requirement."
    if alignment.get("alignment_status") != "aligned":
        note = f"{note} {alignment.get('alignment_note')}"
    if fit.get("fit_status") != "matched":
        note = f"{note} Product fit requires review."
    return note.strip()
