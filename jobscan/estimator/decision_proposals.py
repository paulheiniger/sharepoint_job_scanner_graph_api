from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

import pandas as pd


SOURCE_PRECEDENCE = {
    "ai_scope": 10,
    "product_guidance": 20,
    "historical_default": 30,
    "historical_answer_key_context": 32,
    "historical_companion": 35,
    "deterministic_rule": 40,
    "reference_project": 45,
    "chat_estimator": 48,
    "reference_template_summary": 49,
    "reference_estimate_answer_key": 49,
    "photo_evidence": 47,
    "explicit_note": 50,
    "estimator_edit": 60,
}


MATERIAL_COMPANION_TARGETS: dict[str, list[dict[str, str]]] = {
    "primer": [
        {
            "section": "roofing_primer_template_decisions",
            "decision_id": "roofing_primer_system_row_39",
            "template_bucket": "primer",
            "workbook_row": "39",
        }
    ],
    "caulk_detail": [
        {
            "section": "roofing_detail_template_decisions",
            "decision_id": "roofing_caulk_sealant_row_43",
            "template_bucket": "caulk_detail",
            "workbook_row": "43",
        }
    ],
    "caulk_sealant": [
        {
            "section": "roofing_detail_template_decisions",
            "decision_id": "roofing_caulk_sealant_row_43",
            "template_bucket": "caulk_detail",
            "workbook_row": "43",
        }
    ],
    "seam_treatment": [
        {
            "section": "roofing_detail_quantity_template_decisions",
            "decision_id": "roofing_seams_misc_row_47",
            "template_bucket": "seams_misc",
            "workbook_row": "47",
        },
        {
            "section": "roofing_labor_template_decisions",
            "decision_id": "roofing_labor_seam_sealer_row_120",
            "template_bucket": "labor_seam_sealer",
            "workbook_row": "120",
        },
    ],
    "fabric": [
        {
            "section": "roofing_detail_template_decisions",
            "decision_id": "roofing_fabric_row_79",
            "template_bucket": "fabric",
            "workbook_row": "79",
        },
        {
            "section": "roofing_labor_template_decisions",
            "decision_id": "roofing_labor_seam_sealer_row_120",
            "template_bucket": "labor_seam_sealer",
            "workbook_row": "120",
        },
    ],
    "board_stock": [
        {
            "section": "roofing_board_fastener_template_decisions",
            "decision_id": "roofing_board_stock_row_58",
            "template_bucket": "board_stock",
            "workbook_row": "58",
        },
        {
            "section": "roofing_board_fastener_template_decisions",
            "decision_id": "roofing_fasteners_row_63",
            "template_bucket": "fasteners",
            "workbook_row": "63",
        },
        {
            "section": "roofing_board_fastener_template_decisions",
            "decision_id": "roofing_plates_row_65",
            "template_bucket": "plates",
            "workbook_row": "65",
        },
    ],
    "fasteners": [
        {
            "section": "roofing_board_fastener_template_decisions",
            "decision_id": "roofing_fasteners_row_63",
            "template_bucket": "fasteners",
            "workbook_row": "63",
        }
    ],
    "plates": [
        {
            "section": "roofing_board_fastener_template_decisions",
            "decision_id": "roofing_plates_row_65",
            "template_bucket": "plates",
            "workbook_row": "65",
        }
    ],
    "dumpster": [
        {
            "section": "roofing_equipment_template_decisions",
            "decision_id": "roofing_dumpsters_row_69",
            "template_bucket": "dumpster",
            "workbook_row": "69",
        }
    ],
    "disposal": [
        {
            "section": "roofing_equipment_template_decisions",
            "decision_id": "roofing_dumpsters_row_69",
            "template_bucket": "dumpster",
            "workbook_row": "69",
        }
    ],
}


PACKAGE_COMPANION_ALIASES = {
    "caulk": "caulk_detail",
    "sealant": "caulk_detail",
    "seams": "seam_treatment",
    "seams_misc": "seam_treatment",
    "seam": "seam_treatment",
    "board": "board_stock",
    "cover_board": "board_stock",
    "iso_board": "board_stock",
    "dumpsters": "dumpster",
    "drum_disposal": "disposal",
    "thermal_barrier": "thermal_barrier_coating",
}


WORKBENCH_MATERIAL_SECTIONS = (
    "roofing_foam_template_decisions",
    "roofing_coating_template_decisions",
    "roofing_primer_template_decisions",
    "roofing_detail_template_decisions",
    "roofing_detail_quantity_template_decisions",
    "roofing_board_fastener_template_decisions",
    "roofing_granules_template_decisions",
    "roofing_equipment_template_decisions",
    "roofing_logistics_expense_template_decisions",
    "roofing_accessory_template_decisions",
    "insulation_foam_template_decisions",
    "insulation_detail_material_template_decisions",
    "insulation_thermal_barrier_template_decisions",
    "insulation_support_material_template_decisions",
    "insulation_equipment_logistics_template_decisions",
    "insulation_logistics_expense_template_decisions",
    "insulation_compliance_template_decisions",
)

REFERENCE_PROJECT_OVERRIDE_FIELDS = {
    "selector_code",
    "editable_selector_code",
    "resolved_template_option",
    "selected_pricing_candidate",
    "basis_sqft",
    "coverage_sqft_per_unit",
    "gal_per_100_sqft",
    "gal_per_sqft",
    "waste_factor_pct",
    "wet_mils_estimate",
    "unit_price",
    "amount",
    "estimated_cost",
    "price_per_square",
    "unit_price_per_thousand",
    "thickness_inches",
    "yield_or_coverage",
    "units",
    "estimated_units",
    "linear_ft",
    "board_area_sqft",
    "days",
    "editable_days",
    "crew_size",
    "crew_people_selection",
    "crew_selector_code",
    "daily_rate",
    "hourly_rate",
    "labor_rate",
    "total_hours",
    "editable_total_hours",
    "historical_driver_rate",
    "historical_driver_source",
    "historical_driver_evidence_count",
    "formula_mode",
    "markup_treatment",
    "template_line",
}

CHAT_ESTIMATOR_OVERRIDE_FIELDS = {
    "selector_code",
    "editable_selector_code",
    "resolved_template_option",
    "selected_pricing_candidate",
    "basis_sqft",
    "area_sqft",
    "thickness_inches",
    "foam_thickness_inches",
    "yield_or_coverage",
    "coverage_sqft_per_unit",
    "gal_per_100_sqft",
    "gal_per_sqft",
    "waste_factor_pct",
    "wet_mils_estimate",
    "unit_price",
    "amount",
    "estimated_cost",
    "price_per_square",
    "unit_price_per_thousand",
    "estimated_units",
    "estimated_sets",
    "linear_ft",
    "units",
    "period",
    "margin_pct",
    "days",
    "editable_days",
    "hours_per_day",
    "people_count",
    "trip_count",
    "round_trip_miles",
    "crew_size",
    "crew_people_selection",
    "crew_selector_code",
    "total_hours",
    "editable_total_hours",
    "daily_rate",
    "hourly_rate",
    "labor_rate",
    "formula_mode",
    "markup_treatment",
    "template_line",
}

INSULATION_REFERENCE_ALLOWED_ROWS: dict[str, set[str]] = {
    "foam": {"19", "20", "21"},
    "membrane": {"24"},
    "primer": {"26"},
    "thermal_barrier_coating": {"30", "31", "32"},
    "thinner": {"37"},
    "caulk_sealant": {"41", "43"},
    "caulk_detail": {"41", "43"},
    "lift": {"47", "48"},
    "delivery_fee": {"50"},
    "generator": {"53"},
    "space_heater": {"55"},
    "misc_materials": {"57", "174", "175"},
    "misc": {"57"},
    "freight": {"59"},
    "abaa_audit": {"61"},
    "abaa_fee": {"63"},
    "drum_disposal": {"65"},
    "sales_inspection_trips": {"68"},
    "truck_expense": {"70"},
    "labor_set_up": {"78"},
    "labor_mask": {"80"},
    "labor_prime": {"82"},
    "labor_membrane": {"84"},
    "labor_foam": {"86"},
    "labor_dc_315": {"88"},
    "labor_misc": {"90"},
    "labor_clean_up": {"92"},
    "labor_loading": {"95"},
    "labor_traveling": {"97"},
    "meals_lodging": {"100"},
    "labor_meals_lodging": {"100"},
}

ROOFING_REFERENCE_ALLOWED_ROWS: dict[str, set[str]] = {
    "foam": {"19", "20", "21"},
    "roofing_foam": {"19", "20", "21"},
    "coating": {"26", "27", "28"},
    "thinner": {"33"},
    "granules": {"36"},
    "primer": {"39"},
    "caulk_detail": {"43", "45"},
    "caulk_sealant": {"43", "45"},
    "seams_misc": {"47"},
    "board_stock": {"58", "59", "60"},
    "fasteners": {"63"},
    "plates": {"65"},
    "dumpster": {"69"},
    "disposal": {"69"},
    "lift": {"73", "74"},
    "delivery_fee": {"76"},
    "fabric": {"79"},
    "edge_metal": {"82"},
    "gutter": {"84"},
    "downspouts": {"86"},
    "roof_hatch": {"88"},
    "scuppers": {"90"},
    "curbs": {"92"},
    "ladders": {"94"},
    "pitch_pockets": {"96"},
    "generator": {"99"},
    "misc": {"101"},
    "misc_materials": {"101", "174", "175"},
    "freight": {"103"},
    "labor_prep": {"116"},
    "labor_prime": {"118"},
    "labor_seam_sealer": {"120"},
    "labor_base": {"122", "124"},
    "labor_caulk": {"126"},
    "labor_details": {"128"},
    "labor_top_coat": {"130"},
    "labor_top_coat_granules": {"130"},
    "labor_cleanup": {"132"},
    "labor_misc": {"134"},
    "labor_loading": {"137", "136"},
    "labor_traveling": {"138"},
    "labor_infrared_scan": {"141"},
    "labor_meals_lodging": {"144"},
    "meals_lodging": {"144"},
}

REFERENCE_NON_DECISION_LABELS = {
    "type",
    "types",
    "types:",
    "margin",
    "margin %",
    "margin pct",
    "linear ft",
    "linear feet",
    "est days",
    "est. days",
    "estimated days",
    "size",
    "period",
    "units",
    "unit",
    "sq ft",
    "sq. ft.",
}


@dataclass(frozen=True)
class DecisionProposal:
    decision_id: str
    template_type: str
    template_bucket: str
    workbook_row: str
    include: bool | None = None
    proposed_values: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    review_required: bool = False
    review_reasons: list[str] = field(default_factory=list)
    evidence: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    source: str = "deterministic_rule"
    section: str = ""

    def key(self) -> tuple[str, str, str, str]:
        return proposal_key(self.template_type, self.section, self.decision_id, self.workbook_row)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def proposal_key(template_type: Any, section: Any, decision_id: Any, workbook_row: Any) -> tuple[str, str, str, str]:
    return (
        str(template_type or "").strip().lower(),
        str(section or "").strip(),
        str(decision_id or "").strip(),
        str(workbook_row or "").strip(),
    )


def row_proposal_key(template_type: Any, row: dict[str, Any], section: str) -> tuple[str, str, str, str]:
    return proposal_key(
        template_type,
        row.get("section") or section,
        row.get("decision_id") or row.get("source_decision_id") or row.get("template_bucket"),
        row.get("workbook_row"),
    )


def row_proposal_alias_keys(template_type: Any, row: dict[str, Any], section: str) -> list[tuple[str, str, str, str]]:
    keys = [row_proposal_key(template_type, row, section)]
    for decision_id in (row.get("source_decision_id"), row.get("template_bucket")):
        if decision_id:
            keys.append(
                proposal_key(
                    template_type,
                    row.get("section") or section,
                    decision_id,
                    row.get("workbook_row"),
                )
            )
    return list(dict.fromkeys(keys))


def merge_decision_proposals(proposals: Iterable[DecisionProposal | dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for proposal in proposals:
        current = proposal.to_dict() if isinstance(proposal, DecisionProposal) else dict(proposal)
        key = proposal_key(
            current.get("template_type"),
            current.get("section"),
            current.get("decision_id"),
            current.get("workbook_row"),
        )
        if not key[2] and not key[3]:
            continue
        existing = merged.get(key)
        if existing is None:
            merged[key] = _normalized_proposal(current)
            continue
        merged[key] = _merge_two_proposals(existing, current)
    return list(merged.values())


def _normalized_proposal(proposal: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(proposal)
    normalized["proposed_values"] = dict(proposal.get("proposed_values") or {})
    normalized["review_reasons"] = list(dict.fromkeys(proposal.get("review_reasons") or []))
    normalized["evidence"] = _merge_evidence({}, proposal.get("evidence") or {})
    normalized["confidence"] = float(proposal.get("confidence") or 0.0)
    normalized["review_required"] = bool(proposal.get("review_required"))
    normalized["source"] = str(proposal.get("source") or "deterministic_rule")
    return normalized


def _merge_two_proposals(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    incoming = _normalized_proposal(incoming)
    existing_rank = SOURCE_PRECEDENCE.get(str(existing.get("source") or ""), 0)
    incoming_rank = SOURCE_PRECEDENCE.get(str(incoming.get("source") or ""), 0)
    chosen = incoming if incoming_rank >= existing_rank else existing
    other = existing if chosen is incoming else incoming
    merged = dict(chosen)
    merged["proposed_values"] = {**(other.get("proposed_values") or {}), **(chosen.get("proposed_values") or {})}
    merged["review_required"] = bool(existing.get("review_required") or incoming.get("review_required"))
    merged["review_reasons"] = list(dict.fromkeys([*(existing.get("review_reasons") or []), *(incoming.get("review_reasons") or [])]))
    merged["evidence"] = _merge_evidence(existing.get("evidence") or {}, incoming.get("evidence") or {})
    merged["confidence"] = max(float(existing.get("confidence") or 0.0), float(incoming.get("confidence") or 0.0))
    return merged


def _merge_evidence(left: dict[str, Any], right: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    merged: dict[str, list[dict[str, Any]]] = {}
    for source in (left, right):
        for key, rows in (source or {}).items():
            values = rows if isinstance(rows, list) else [rows]
            bucket = merged.setdefault(str(key), [])
            for value in values:
                if isinstance(value, dict) and value not in bucket:
                    bucket.append(value)
    return merged


def build_decision_proposals(scope: dict[str, Any], recommendation: Any = None, data: Any = None) -> list[dict[str, Any]]:
    template_type = "insulation" if _is_insulation_scope(scope) else "roofing"
    notes = _note_text(scope)
    proposals: list[DecisionProposal] = []
    proposals.extend(_named_reference_answer_key_proposals(scope, data=data, template_type=template_type, notes=notes))
    proposals.extend(_reference_project_proposals(scope, data=data, template_type=template_type, notes=notes))
    proposals.extend(_photo_scope_proposals(template_type, scope))
    proposals.extend(_chat_estimator_proposals(template_type, scope))
    proposals.extend(_ai_scope_proposals(template_type, _ai_scope_debug(recommendation)))
    return merge_decision_proposals(proposals)


def _named_reference_answer_key_proposals(
    scope: dict[str, Any],
    *,
    data: Any = None,
    template_type: str,
    notes: str,
) -> list[DecisionProposal]:
    from .reference_answer_key import answer_key_to_workbook_decision_preferences

    if not re.search(r"\b(?:similar\s+to|same\s+as|based\s+on|reference)\b", notes, flags=re.IGNORECASE):
        return []
    examples = getattr(data, "template_examples", pd.DataFrame()) if data is not None else pd.DataFrame()
    if not isinstance(examples, pd.DataFrame) or examples.empty or "answer_key_json" not in examples.columns:
        return []
    note_key = _norm(notes)
    matched: list[tuple[int, dict[str, Any]]] = []
    for row in examples.fillna("").to_dict(orient="records"):
        source_file = str(row.get("source_file") or "").strip()
        source_stem = re.sub(r"\.(?:xlsx|xlsm|xls)$", "", source_file, flags=re.IGNORECASE)
        source_key = _norm(source_stem)
        if len(source_key) < 12 or source_key not in note_key:
            continue
        if _norm(row.get("template_type")) and _norm(row.get("template_type")) != _norm(template_type):
            continue
        matched.append((len(source_key), row))
    if not matched:
        return []
    _, example = max(matched, key=lambda item: item[0])
    try:
        answer_key = json.loads(str(example.get("answer_key_json") or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(answer_key, dict):
        return []
    source_file = str(example.get("source_file") or "")
    preferences = answer_key_to_workbook_decision_preferences(answer_key)
    reference_area = _named_reference_area(answer_key, example)
    current_area = _scope_area(scope)
    area_scale = current_area / reference_area if current_area > 0 and reference_area > 0 else 0.0
    context_mismatches = _named_reference_context_mismatches(answer_key, scope)
    proposals: list[DecisionProposal] = []
    for preference in preferences:
        if not _named_reference_preference_authorized(preference, scope, notes):
            continue
        values = dict(preference.get("proposed_values") or {})
        bucket = _canonical_package(preference.get("template_bucket"))
        is_labor = str(preference.get("section") or "") == "roofing_labor_template_decisions"
        if is_labor:
            driver_values = _named_reference_labor_driver_values(
                preference,
                preferences,
                reference_area=reference_area,
            )
            for field in (
                "days",
                "editable_days",
                "crew_size",
                "crew_people_selection",
                "crew_selector_code",
                "daily_rate",
                "hourly_rate",
                "labor_rate",
                "total_hours",
                "editable_total_hours",
                "resolved_template_option",
                "selected_pricing_candidate",
            ):
                values.pop(field, None)
            values.update(driver_values)
        elif area_scale > 0 and bucket not in {"overhead", "profit"}:
            for field in ("estimated_units", "units", "quantity", "linear_ft"):
                amount = _safe_number(values.get(field), 0.0)
                if amount > 0:
                    values[field] = round(amount * area_scale, 4)
            amount = _safe_number(values.get("amount"), 0.0)
            if amount > 0:
                values["amount"] = round(amount * area_scale, 2)
        raw_evidence = preference.get("evidence")
        evidence_rows = [item for item in raw_evidence if isinstance(item, dict)] if isinstance(raw_evidence, list) else []
        evidence_rows.append(
            {
                "document_id": example.get("document_id"),
                "job_id": example.get("job_id"),
                "source_file": source_file,
                "match_method": "explicit_source_file_mention",
                "reference_area_sqft": reference_area or None,
                "current_area_sqft": current_area or None,
                "area_scale_factor": round(area_scale, 6) if area_scale > 0 else None,
                "reference_area_requires_review": bool(context_mismatches),
            }
        )
        reasons = list(preference.get("review_reasons") or [])
        reasons.append(f"Current notes explicitly reference {source_file}; only scope-authorized active answer-key rows were applied.")
        if is_labor:
            reasons.append("Reference labor duration and crew were not copied; labor is recalculated from current material quantities or current area.")
        elif area_scale > 0:
            reasons.append(f"Reference quantity was scaled by current/reference area ({current_area:g}/{reference_area:g} sq ft).")
        elif any(_safe_number(values.get(field), 0.0) > 0 for field in ("estimated_units", "units", "quantity", "linear_ft")):
            reasons.append("Reference area is missing; quantity remains review-required and was not treated as a scalable production rate.")
        if context_mismatches:
            reasons.append(
                "Reference area context conflicts with the current scope "
                f"({'; '.join(context_mismatches)}); verify the reference square footage before export."
            )
        proposals.append(
            DecisionProposal(
                decision_id=str(preference.get("decision_id") or ""),
                template_type=template_type,
                template_bucket=str(preference.get("template_bucket") or ""),
                workbook_row=str(preference.get("workbook_row") or ""),
                include=preference.get("include"),
                proposed_values=values,
                confidence=max(0.0, min(_safe_number(preference.get("confidence"), 0.88), 0.95)),
                review_required=True,
                review_reasons=list(dict.fromkeys(reasons)),
                evidence={"reference_estimate_answer_key": evidence_rows},
                source="reference_estimate_answer_key",
                section=str(preference.get("section") or ""),
            )
        )
    return proposals


def _named_reference_area(answer_key: dict[str, Any], example: dict[str, Any]) -> float:
    for field in ("verified_area_sqft", "reference_area_sqft"):
        area = _safe_number(example.get(field), 0.0)
        if area > 0:
            return area
    context = answer_key.get("job_context") if isinstance(answer_key.get("job_context"), dict) else {}
    return _safe_number(context.get("area_sqft"), 0.0)


def _named_reference_context_mismatches(answer_key: dict[str, Any], scope: dict[str, Any]) -> list[str]:
    context = answer_key.get("job_context") if isinstance(answer_key.get("job_context"), dict) else {}
    mismatches: list[str] = []
    for current_value, reference_value, label in (
        (
            scope.get("roof_type_substrate") or scope.get("substrate"),
            context.get("substrate"),
            "substrate",
        ),
        (scope.get("project_type"), context.get("project_type"), "project type"),
    ):
        current = _norm(current_value)
        reference = _norm(reference_value)
        if current and reference and current != reference and current not in reference and reference not in current:
            mismatches.append(f"{label} {reference_value!s} vs {current_value!s}")
    return mismatches


def _named_reference_labor_driver_values(
    labor_preference: dict[str, Any],
    preferences: list[dict[str, Any]],
    *,
    reference_area: float,
) -> dict[str, Any]:
    values = dict(labor_preference.get("proposed_values") or {})
    hours = _safe_number(values.get("total_hours") or values.get("editable_total_hours"), 0.0)
    bucket = _canonical_package(labor_preference.get("template_bucket"))
    rate = 0.0
    rate_unit = ""
    if bucket in {"labor_prep", "labor_cleanup", "labor_loading"} and hours > 0 and reference_area > 0:
        rate = hours / reference_area * 1000.0
        rate_unit = "hours_per_1000_sqft"
    material_bucket_by_labor = {
        "labor_caulk": {"caulk_detail", "caulk_sealant"},
        "labor_base": {"coating"},
        "labor_top_coat": {"coating"},
        "labor_prime": {"primer"},
        "labor_seam_sealer": {"fabric", "seams_misc"},
    }
    material_buckets = material_bucket_by_labor.get(bucket) or set()
    if hours > 0 and material_buckets:
        material_quantity = 0.0
        for preference in preferences:
            if _canonical_package(preference.get("template_bucket")) not in material_buckets:
                continue
            material_values = preference.get("proposed_values") or {}
            material_quantity += _safe_number(
                material_values.get("estimated_units")
                or material_values.get("units")
                or material_values.get("quantity")
                or material_values.get("estimated_gallons")
                or material_values.get("linear_ft"),
                0.0,
            )
        if material_quantity > 0:
            rate = hours / material_quantity
            rate_unit = "hours_per_material_unit"
    if rate <= 0:
        return {}
    return {
        "historical_driver_rate": round(rate, 6),
        "historical_driver_source": "reference_estimate_answer_key",
        "historical_driver_evidence_count": 1,
        "labor_driver_rate_unit": rate_unit,
    }


def _named_reference_preference_authorized(
    preference: dict[str, Any],
    scope: dict[str, Any],
    notes: str,
) -> bool:
    bucket = _canonical_package(preference.get("template_bucket"))
    package_by_bucket = {
        "coating": "coating",
        "caulk_detail": "caulk_detail",
        "caulk_sealant": "caulk_detail",
        "labor_caulk": "caulk_detail",
        "labor_prep": "prep_powerwash",
        "labor_prime": "primer",
        "labor_seam_sealer": "seam_treatment",
        "fasteners": "fastener_treatment",
        "plates": "fastener_treatment",
    }
    package = package_by_bucket.get(bucket)
    contract = (scope.get("work_package_decisions") or {}).get(package) if package else None
    if isinstance(contract, dict):
        return contract.get("applies") is True
    if bucket in {"labor_base", "labor_top_coat", "labor_cleanup"}:
        coating = (scope.get("work_package_decisions") or {}).get("coating")
        return isinstance(coating, dict) and coating.get("applies") is True
    normalized_notes = _norm(notes)
    explicit_terms = {
        "lift": ("lift", "boom"),
        "generator": ("generator",),
        "sales_trips": ("sales trip", "inspection trip"),
        "sales_inspection_trips": ("sales trip", "inspection trip"),
        "truck_expense": ("truck", "mileage", "miles"),
        "labor_loading": ("loading", "load materials"),
        "labor_traveling": ("travel",),
    }
    terms = explicit_terms.get(bucket)
    return bool(terms and any(term in normalized_notes for term in terms))


def build_material_companion_proposals(workbench: dict[str, Any], data: Any = None) -> list[dict[str, Any]]:
    template_type = _workbench_template_type(workbench, [])
    if template_type != "roofing":
        return []
    included_packages = _included_workbench_packages(workbench)
    if not included_packages:
        return []
    relationship_rows = _relationship_cooccurrence_rows(workbench, data)
    proposals: list[DecisionProposal] = []
    for row in relationship_rows:
        package_a = _canonical_package(_first_value(row, "package_a", "source_package", "antecedent_package", "package"))
        package_b = _canonical_package(_first_value(row, "package_b", "target_package", "consequent_package", "related_package"))
        if not package_a or not package_b:
            continue
        rate = _safe_number(_first_value(row, "co_occurrence_rate", "support", "rate"), 0.0)
        job_count = int(_safe_number(_first_value(row, "job_count", "evidence_count", "supporting_job_count", "count"), 0))
        if rate < 0.5 or job_count < 3:
            continue
        for anchor, target in ((package_a, package_b), (package_b, package_a)):
            if anchor not in included_packages or target in included_packages:
                continue
            for target_spec in MATERIAL_COMPANION_TARGETS.get(target, []):
                if (
                    target_spec.get("section") == "roofing_labor_template_decisions"
                    and anchor not in {"fabric", "seam_treatment", "caulk_detail"}
                ):
                    continue
                proposals.append(_companion_proposal(target_spec, anchor=anchor, target=target, row=row, rate=rate, job_count=job_count))
    return merge_decision_proposals(proposals)


def apply_decision_proposals_to_workbench(
    workbench: dict[str, Any],
    proposals: Iterable[DecisionProposal | dict[str, Any]] | None,
    *,
    decision_sections: Iterable[str],
) -> dict[str, Any]:
    normalized = merge_decision_proposals(proposals or [])
    proposal_by_key = {
        proposal_key(p.get("template_type"), p.get("section"), p.get("decision_id"), p.get("workbook_row")): p
        for p in normalized
    }
    template_type = _workbench_template_type(workbench, normalized)
    duplicate_rows: list[dict[str, Any]] = []
    for section in decision_sections:
        rows = [row for row in workbench.get(section) or [] if isinstance(row, dict)]
        deduped: list[dict[str, Any]] = []
        seen: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for row in rows:
            key = row_proposal_key(template_type, row, section)
            proposal = next(
                (
                    proposal_by_key[alias]
                    for alias in row_proposal_alias_keys(template_type, row, section)
                    if alias in proposal_by_key
                ),
                None,
            )
            annotated = _annotate_row(row, proposal)
            if key in seen:
                duplicate_rows.append(
                    {
                        "section": section,
                        "decision_id": row.get("decision_id"),
                        "workbook_row": row.get("workbook_row"),
                        "template_bucket": row.get("template_bucket"),
                    }
                )
                seen[key] = _merge_duplicate_rows(seen[key], annotated)
                continue
            seen[key] = annotated
            deduped.append(annotated)
        workbench[section] = [seen[row_proposal_key(template_type, row, section)] for row in deduped]
    workbench["decision_proposals"] = normalized
    workbench["duplicate_decision_rows"] = duplicate_rows
    if duplicate_rows:
        flags = list(workbench.get("review_flags") or [])
        warning = f"Duplicate workbench decisions were merged: {len(duplicate_rows)} duplicate row(s)."
        if warning not in flags:
            flags.append(warning)
        workbench["review_flags"] = flags
    return workbench


def _annotate_row(row: dict[str, Any], proposal: dict[str, Any] | None) -> dict[str, Any]:
    updated = dict(row)
    if proposal:
        if proposal.get("include") is not None and not updated.get("manual_override"):
            updated["include"] = bool(proposal.get("include"))
            updated["include_source"] = proposal.get("source")
        for key, value in (proposal.get("proposed_values") or {}).items():
            source = proposal.get("source")
            reference_can_override = (
                source == "reference_project"
                and not updated.get("manual_override")
                and key in REFERENCE_PROJECT_OVERRIDE_FIELDS
            )
            chat_can_override = (
                source == "chat_estimator"
                and not updated.get("manual_override")
                and key in CHAT_ESTIMATOR_OVERRIDE_FIELDS
            )
            answer_key_can_override = (
                source in {"reference_template_summary", "reference_estimate_answer_key"}
                and not updated.get("manual_override")
                and key in REFERENCE_PROJECT_OVERRIDE_FIELDS
            )
            if value is not None and (
                _proposal_value_can_fill(updated.get(key))
                or reference_can_override
                or chat_can_override
                or answer_key_can_override
            ):
                updated[key] = value
        updated["decision_proposal"] = proposal
        updated["proposal_source"] = proposal.get("source")
        updated["proposal_confidence"] = proposal.get("confidence")
        updated["proposal_evidence"] = proposal.get("evidence") or {}
        updated["proposal_review_required"] = bool(proposal.get("review_required"))
        updated["proposal_review_reasons"] = list(proposal.get("review_reasons") or [])
        if updated.get("include") and proposal.get("review_required"):
            warnings = list(updated.get("compatibility_warnings") or [])
            warnings.extend(proposal.get("review_reasons") or [])
            updated["compatibility_warnings"] = list(dict.fromkeys(warnings))
            if str(updated.get("compatibility_status") or "").lower() in {"", "compatible", "not_included"}:
                updated["compatibility_status"] = "review"
    updated.update(_decision_evidence_fields(updated))
    return updated


def _relationship_cooccurrence_rows(workbench: dict[str, Any], data: Any = None) -> list[dict[str, Any]]:
    frame = getattr(data, "relationship_package_cooccurrence", pd.DataFrame()) if data is not None else pd.DataFrame()
    if isinstance(frame, pd.DataFrame) and not frame.empty:
        return frame.to_dict(orient="records")
    rows = workbench.get("relationship_package_cooccurrence") or workbench.get("relationship_package_cooccurrence_rows") or []
    if isinstance(rows, pd.DataFrame):
        return rows.to_dict(orient="records")
    return [dict(row) for row in rows if isinstance(row, dict)]


def _reference_project_proposals(
    scope: dict[str, Any],
    *,
    data: Any = None,
    template_type: str,
    notes: str,
) -> list[DecisionProposal]:
    rows = _reference_template_rows(scope, data=data, notes=notes)
    if rows.empty:
        return []
    proposals: list[DecisionProposal] = []
    for _, row in rows.iterrows():
        row_dict = row.to_dict()
        if _norm(row_dict.get("template_type")) and _norm(row_dict.get("template_type")) != _norm(template_type):
            continue
        if not _reference_row_compatible(row_dict, template_type):
            continue
        target = _reference_target_for_row(row_dict, template_type)
        if not target:
            continue
        values, scale = _reference_values_for_row(row_dict, scope, target)
        reasons = _reference_review_reasons(row_dict, scope, scale)
        evidence = {
            "reference_project": [
                {
                    "job_id": row_dict.get("job_id"),
                    "source_file": row_dict.get("source_file"),
                    "template_bucket": row_dict.get("template_bucket"),
                    "row_number": row_dict.get("row_number"),
                    "selected_item_name": row_dict.get("selected_item_name") or row_dict.get("resolved_item_name"),
                    "reference_area_sqft": _reference_area(row_dict),
                    "current_area_sqft": _scope_area(scope),
                    "scale_factor": scale,
                }
            ]
        }
        proposals.append(
            DecisionProposal(
                decision_id=target["decision_id"],
                template_type=template_type,
                template_bucket=target["template_bucket"],
                workbook_row=target["workbook_row"],
                include=True,
                proposed_values=values,
                confidence=0.88 if not reasons else 0.72,
                review_required=bool(reasons),
                review_reasons=reasons,
                evidence=evidence,
                source="reference_project",
                section=target["section"],
            )
        )
    return proposals


def _reference_row_compatible(row: dict[str, Any], template_type: str) -> bool:
    selected_name = _norm(_first_value(row, "resolved_item_name", "selected_item_name", "item_name"))
    if selected_name in REFERENCE_NON_DECISION_LABELS:
        return False
    bucket = _canonical_package(row.get("template_bucket"))
    row_number = str(int(_safe_number(row.get("row_number"), 0))) if _safe_number(row.get("row_number"), 0) > 0 else ""
    if not row_number:
        return True
    allowed_by_bucket = (
        INSULATION_REFERENCE_ALLOWED_ROWS if _norm(template_type) == "insulation" else ROOFING_REFERENCE_ALLOWED_ROWS
    )
    allowed_rows = allowed_by_bucket.get(bucket)
    if allowed_rows is None:
        if bucket.startswith("labor_"):
            return False
        return True
    if _norm(template_type) == "insulation" and (
        (bucket == "sales_inspection_trips" and row_number == "88")
        or (bucket == "truck_expense" and row_number == "90")
    ):
        return True
    return row_number in allowed_rows


def _reference_template_rows(scope: dict[str, Any], *, data: Any = None, notes: str = "") -> pd.DataFrame:
    frame = getattr(data, "template_rows", pd.DataFrame()) if data is not None else pd.DataFrame()
    if not isinstance(frame, pd.DataFrame) or frame.empty or "job_id" not in frame.columns:
        return pd.DataFrame()
    reference_ids = _reference_job_ids(scope, frame, notes)
    if not reference_ids:
        return pd.DataFrame()
    job_keys = frame["job_id"].fillna("").astype(str).map(str.strip)
    return frame[job_keys.isin(reference_ids)].copy()


def _reference_job_ids(scope: dict[str, Any], template_rows: pd.DataFrame, notes: str) -> list[str]:
    values: list[str] = []
    for key in (
        "reference_job_ids",
        "reference_project_ids",
        "selected_reference_job_ids",
        "selected_reference_jobs",
        "similar_to_job_ids",
        "similar_project_ids",
    ):
        values.extend(_split_reference_values(scope.get(key)))
    note_text = _norm(notes)
    if note_text and "job_id" in template_rows.columns:
        for job_id in sorted({str(item).strip() for item in template_rows["job_id"].dropna().astype(str) if str(item).strip()}):
            if len(job_id) >= 3 and _norm(job_id) in note_text:
                values.append(job_id)
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out


def _split_reference_values(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, dict):
        return _split_reference_values(value.get("job_id") or value.get("id") or value.get("name"))
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            out.extend(_split_reference_values(item))
        return out
    text = str(value)
    for token in ("\n", ";", "|"):
        text = text.replace(token, ",")
    return [part.strip() for part in text.split(",") if part.strip()]


def _reference_target_for_row(row: dict[str, Any], template_type: str) -> dict[str, str] | None:
    bucket = _canonical_package(row.get("template_bucket"))
    kind = _norm(row.get("line_item_kind"))
    row_number = str(int(_safe_number(row.get("row_number"), 0))) if _safe_number(row.get("row_number"), 0) > 0 else ""
    if template_type == "roofing" and (kind == "labor" or bucket.startswith("labor_") or bucket in {"infrared_scan", "meals_lodging"}):
        logistics_rows = {"136", "137", "138", "139", "141", "142", "144", "145"}
        logistics_defaults = {
            "labor_loading": ("roofing_labor_loading_row_136", "labor_loading", "136"),
            "labor_traveling": ("roofing_labor_traveling_row_138", "labor_traveling", "138"),
            "labor_infrared_scan": ("roofing_infrared_scan_row_141", "infrared_scan", "141"),
            "infrared_scan": ("roofing_infrared_scan_row_141", "infrared_scan", "141"),
            "labor_meals_lodging": ("roofing_meals_lodging_row_144", "meals_lodging", "144"),
            "meals_lodging": ("roofing_meals_lodging_row_144", "meals_lodging", "144"),
        }
        production_defaults = {
            "labor_prep": "116",
            "labor_prime": "118",
            "labor_seam_sealer": "120",
            "labor_base": "122",
            "labor_top_coat": "124",
            "labor_caulk": "126",
            "labor_details": "128",
            "labor_top_coat_granules": "130",
            "labor_cleanup": "132",
            "labor_misc": "134",
        }
        if bucket in logistics_defaults and row_number in logistics_rows:
            decision_id, normalized_bucket, resolved_row = logistics_defaults[bucket]
            return {
                "section": "roofing_logistics_expense_template_decisions",
                "decision_id": decision_id,
                "template_bucket": normalized_bucket,
                "workbook_row": resolved_row,
            }
        if bucket in logistics_defaults and row_number not in logistics_rows:
            bucket = "labor_prep" if row_number == "116" else bucket
        if bucket in production_defaults:
            resolved_row = production_defaults[bucket]
            return {
                "section": "roofing_labor_template_decisions",
                "decision_id": f"roofing_{bucket}_row_{resolved_row}",
                "template_bucket": bucket,
                "workbook_row": resolved_row,
            }
    shared_target = _chat_target_for_preference(
        template_type,
        {
            "template_bucket": bucket,
            "workbook_row": row_number,
            "row_number": row_number,
        },
    )
    if shared_target:
        return shared_target
    if template_type == "roofing":
        if bucket in {"foam", "roofing_foam"}:
            row_number = row_number if row_number in {"19", "20", "21"} else "19"
            return {"section": "roofing_foam_template_decisions", "decision_id": f"roofing_foam_row_{row_number}", "template_bucket": "roofing_foam", "workbook_row": row_number}
        if bucket == "coating":
            row_number = row_number if row_number in {"26", "27", "28"} else "26"
            return {"section": "roofing_coating_template_decisions", "decision_id": f"roofing_coating_system_row_{row_number}", "template_bucket": "coating", "workbook_row": row_number}
        if bucket == "primer":
            return {"section": "roofing_primer_template_decisions", "decision_id": "roofing_primer_system_row_39", "template_bucket": "primer", "workbook_row": "39"}
        if bucket in {"caulk_detail", "caulk_sealant"}:
            row_number = row_number if row_number in {"43", "45"} else "43"
            return {"section": "roofing_detail_template_decisions", "decision_id": f"roofing_caulk_sealant_row_{row_number}", "template_bucket": "caulk_detail", "workbook_row": row_number}
        if bucket == "fabric":
            return {"section": "roofing_detail_template_decisions", "decision_id": "roofing_fabric_row_79", "template_bucket": "fabric", "workbook_row": "79"}
        if bucket == "board_stock":
            row_number = row_number if row_number in {"58", "59", "60"} else "58"
            return {"section": "roofing_board_fastener_template_decisions", "decision_id": f"roofing_board_stock_row_{row_number}", "template_bucket": "board_stock", "workbook_row": row_number}
        if bucket == "fasteners":
            return {"section": "roofing_board_fastener_template_decisions", "decision_id": "roofing_fasteners_row_63", "template_bucket": "fasteners", "workbook_row": "63"}
        if bucket == "plates":
            return {"section": "roofing_board_fastener_template_decisions", "decision_id": "roofing_plates_row_65", "template_bucket": "plates", "workbook_row": "65"}
        if bucket == "granules":
            return {"section": "roofing_granules_template_decisions", "decision_id": "roofing_granules_row_36", "template_bucket": "granules", "workbook_row": "36"}
        if bucket in {"dumpster", "disposal"}:
            return {"section": "roofing_equipment_template_decisions", "decision_id": "roofing_dumpsters_row_69", "template_bucket": "dumpster", "workbook_row": "69"}
        if bucket in {"labor_loading", "labor_traveling", "labor_infrared_scan", "infrared_scan", "labor_meals_lodging", "meals_lodging"}:
            row_defaults = {
                "labor_loading": "136",
                "labor_traveling": "138",
                "labor_infrared_scan": "141",
                "infrared_scan": "141",
                "labor_meals_lodging": "144",
                "meals_lodging": "144",
            }
            normalized_bucket = {
                "labor_infrared_scan": "infrared_scan",
                "labor_meals_lodging": "meals_lodging",
            }.get(bucket, bucket)
            resolved_row = row_number if row_number in {"136", "138", "141", "144"} else row_defaults[bucket]
            return {
                "section": "roofing_logistics_expense_template_decisions",
                "decision_id": f"roofing_{normalized_bucket}_row_{resolved_row}",
                "template_bucket": normalized_bucket,
                "workbook_row": resolved_row,
            }
        if kind == "labor" or bucket.startswith("labor_"):
            if not row_number:
                return None
            return {"section": "roofing_labor_template_decisions", "decision_id": f"roofing_{bucket}_row_{row_number}", "template_bucket": bucket, "workbook_row": row_number}
    if template_type == "insulation":
        if bucket == "foam":
            return {"section": "insulation_foam_template_decisions", "decision_id": "insulation_foam_template_selector", "template_bucket": "foam", "workbook_row": "19-21"}
        if bucket == "thermal_barrier_coating":
            row_number = row_number if row_number in {"30", "31", "32"} else "30"
            return {"section": "insulation_thermal_barrier_template_decisions", "decision_id": f"insulation_thermal_barrier_row_{row_number}", "template_bucket": "thermal_barrier_coating", "workbook_row": row_number}
        if bucket in {"caulk_detail", "caulk_sealant"}:
            row_number = row_number if row_number in {"41", "43"} else "41"
            return {"section": "insulation_detail_material_template_decisions", "decision_id": f"insulation_caulk_sealant_row_{row_number}", "template_bucket": "caulk_sealant", "workbook_row": row_number}
        if kind == "labor" or bucket.startswith("labor_"):
            if not row_number:
                return None
            return {"section": "insulation_labor_template_decisions", "decision_id": f"insulation_{bucket}_row_{row_number}", "template_bucket": bucket, "workbook_row": row_number}
    return None


def _reference_values_for_row(row: dict[str, Any], scope: dict[str, Any], target: dict[str, str]) -> tuple[dict[str, Any], float]:
    values: dict[str, Any] = {}
    selected_name = _first_value(row, "resolved_item_name", "selected_item_name", "item_name")
    selector_code = _first_value(row, "selector_code", "editable_selector_code")
    if selector_code not in (None, ""):
        values["selector_code"] = str(selector_code)
        values["editable_selector_code"] = str(selector_code)
    if selected_name not in (None, ""):
        values["resolved_template_option"] = selected_name
        values["selected_pricing_candidate"] = selected_name
    unit_price = _safe_number(_first_value(row, "unit_price", "current_unit_price", "daily_rate"), 0.0)
    if unit_price > 0:
        values["unit_price"] = round(unit_price, 4)

    reference_area = _reference_area(row)
    current_area = _scope_area(scope)
    scale = current_area / reference_area if current_area > 0 and reference_area > 0 else 1.0
    bucket = target["template_bucket"]
    if bucket in {"coating", "primer", "board_stock", "granules"} and current_area > 0:
        values["basis_sqft"] = round(current_area, 2)
    if bucket == "coating":
        for field in ("gal_per_100_sqft", "gal_per_sqft", "waste_factor_pct", "wet_mils_estimate"):
            number = _safe_number(row.get(field), 0.0)
            if number > 0:
                values[field] = round(number, 6)
    elif bucket == "primer":
        estimated_units = _safe_number(_first_value(row, "estimated_units", "quantity"), 0.0)
        if reference_area > 0 and estimated_units > 0:
            values["coverage_sqft_per_unit"] = round(reference_area / estimated_units, 4)
    elif bucket == "caulk_detail":
        quantity = _safe_number(_first_value(row, "estimated_units", "quantity", "calculated_quantity"), 0.0)
        if quantity > 0:
            values["units"] = round(quantity * scale, 4)
            values["estimated_units"] = round(quantity * scale, 4)
    elif bucket == "fabric":
        quantity = _safe_number(_first_value(row, "linear_ft", "estimated_units", "quantity", "calculated_quantity"), 0.0)
        if quantity > 0:
            values["linear_ft"] = round(quantity * scale, 4)
            values["units"] = round(quantity * scale, 4)
            values["estimated_units"] = round(quantity * scale, 4)
    elif bucket == "board_stock":
        thickness = _safe_number(row.get("thickness_inches"), 0.0)
        if thickness > 0:
            values["thickness_inches"] = round(thickness, 4)
        if unit_price > 0:
            values["price_per_square"] = round(unit_price, 4)
    elif bucket in {"fasteners", "plates"}:
        if current_area > 0:
            values["board_area_sqft"] = round(current_area, 2)
        if unit_price > 0:
            values["unit_price_per_thousand"] = round(unit_price, 4)
    elif bucket == "foam":
        if current_area > 0:
            values["basis_sqft"] = round(current_area, 2)
        for field in ("thickness_inches", "yield_or_coverage"):
            number = _safe_number(row.get(field), 0.0)
            if number > 0:
                values[field] = round(number, 4)
    elif bucket.startswith("labor_"):
        crew_size = _safe_number(row.get("crew_size"), 0.0)
        hours = _safe_number(_first_value(row, "total_hours", "labor_hours"), 0.0)
        if crew_size > 0:
            values["crew_size"] = int(crew_size)
            values["crew_people_selection"] = int(crew_size)
            values["crew_selector_code"] = int(crew_size)
        if hours > 0:
            scaled_hours = round(hours * scale, 4)
            values["total_hours"] = scaled_hours
            values["editable_total_hours"] = scaled_hours
            if crew_size > 0:
                values["days"] = round(scaled_hours / max(crew_size * 10.0, 1.0), 4)
                values["editable_days"] = values["days"]
        for field in ("daily_rate", "hourly_rate", "formula_mode"):
            value = row.get(field)
            if value not in (None, ""):
                values[field] = value
                if field == "hourly_rate":
                    values["labor_rate"] = value
    return values, round(scale, 6)


def _reference_review_reasons(row: dict[str, Any], scope: dict[str, Any], scale: float) -> list[str]:
    reasons: list[str] = []
    for field, label in (
        ("project_type", "project type"),
        ("substrate", "substrate"),
        ("coating_type", "coating type"),
    ):
        current_value = scope.get("roof_type_substrate") if field == "substrate" else scope.get(field)
        current = _norm(current_value)
        reference = _norm(row.get(field))
        if current and reference and current != reference and current not in reference and reference not in current:
            reasons.append(f"Reference {label} '{row.get(field)}' differs from current '{current_value}'.")
    if scale and (scale >= 3 or scale <= 0.3333):
        reasons.append(f"Reference job area scale is {scale:.2f}x; verify scaled quantities.")
    return list(dict.fromkeys(reasons))


def _chat_estimator_proposals(template_type: str, scope: dict[str, Any]) -> list[DecisionProposal]:
    chat_payload = scope.get("estimator_chat") if isinstance(scope.get("estimator_chat"), dict) else {}
    raw = chat_payload.get("workbook_decision_preferences") or scope.get("workbook_decision_preferences") or []
    proposals: list[DecisionProposal] = []
    normalized_template_type = _norm(template_type)
    notes = _note_text(scope)
    raw_items = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    insulation_foam_items_without_row = [
        item
        for item in raw_items
        if normalized_template_type == "insulation"
        and _canonical_package(item.get("template_bucket") or item.get("package") or item.get("category")) == "foam"
        and not str(item.get("workbook_row") or item.get("row_number") or "").strip()
        and not _decision_id_row_number(item.get("decision_id"))
    ]
    assign_insulation_foam_rows = len(insulation_foam_items_without_row) > 1
    next_insulation_foam_row = 19
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        item_template_type = _norm(item.get("template_type"))
        if item_template_type and item_template_type != normalized_template_type:
            continue
        item_section = _norm(item.get("section"))
        if normalized_template_type != "insulation" and item_section.startswith("insulation "):
            continue
        if normalized_template_type != "roofing" and item_section.startswith("roofing "):
            continue
        bucket = _canonical_package(item.get("template_bucket") or item.get("package") or item.get("category"))
        if assign_insulation_foam_rows and normalized_template_type == "insulation" and bucket == "foam":
            if (
                not str(item.get("workbook_row") or item.get("row_number") or "").strip()
                and not _decision_id_row_number(item.get("decision_id"))
                and next_insulation_foam_row <= 21
            ):
                item = dict(item)
                item["workbook_row"] = str(next_insulation_foam_row)
                item["decision_id"] = f"insulation_foam_row_{next_insulation_foam_row}"
                next_insulation_foam_row += 1
        target = _chat_target_for_preference(template_type, item)
        if not target:
            continue
        values = _clean_chat_proposed_values(item, template_type=template_type)
        confidence = _safe_number(item.get("confidence"), _safe_number(chat_payload.get("confidence"), 0.62))
        review_required = bool(item.get("review_required", confidence < 0.75))
        reasons = list(item.get("review_reasons") or item.get("review_flags") or [])
        if review_required and not reasons:
            reasons.append("Estimator chat proposal requires estimator confirmation.")
        source = str(item.get("source") or "chat_estimator")
        if source not in {
            "historical_answer_key_context",
            "reference_template_summary",
            "reference_estimate_answer_key",
        }:
            source = "chat_estimator"
        include = item.get("include") if item.get("include") is not None else True
        if source == "historical_answer_key_context":
            include = False
            review_required = True
            historical_reason = "Historical comparable evidence requires current-scope confirmation before inclusion."
            if historical_reason not in reasons:
                reasons.append(historical_reason)
        if bucket == "lift" and re.search(
            r"\b(?:include|add|provide|need|use)\b(?:\W+\w+){0,5}\W+\b(?:boom\s+lift|lift)\b",
            notes,
            flags=re.IGNORECASE,
        ):
            include = True
            source = "explicit_note"
            if not values.get("period") or not (
                values.get("unit_price")
                or values.get("daily_rate")
                or values.get("weekly_rate")
                or values.get("monthly_rate")
            ):
                review_required = True
                missing_lift_basis = (
                    "Lift is explicitly required by the current notes; rental period and price must be supplied before export."
                )
                if missing_lift_basis not in reasons:
                    reasons.append(missing_lift_basis)
        raw_evidence = item.get("evidence")
        if isinstance(raw_evidence, dict):
            evidence = raw_evidence
        elif isinstance(raw_evidence, list) and raw_evidence:
            evidence = {source: [entry for entry in raw_evidence if isinstance(entry, dict)]}
        else:
            evidence = {}
        if not evidence:
            evidence = {
                "chat_estimator": [
                    {
                        "assistant_message": chat_payload.get("assistant_message") or "",
                        "source": chat_payload.get("source") or "estimator_chat",
                    }
                ]
            }
        proposals.append(
            DecisionProposal(
                decision_id=target["decision_id"],
                template_type=template_type,
                template_bucket=target["template_bucket"],
                workbook_row=target["workbook_row"],
                include=include,
                proposed_values=values,
                confidence=max(0.0, min(confidence, 0.95)),
                review_required=review_required,
                review_reasons=reasons,
                evidence=evidence,
                source=source,
                section=target["section"],
            )
        )
    return proposals


def _chat_target_for_preference(template_type: str, item: dict[str, Any]) -> dict[str, str] | None:
    bucket = _canonical_package(item.get("template_bucket") or item.get("package") or item.get("category"))
    decision_id = str(item.get("decision_id") or "").strip()
    workbook_row = str(item.get("workbook_row") or item.get("row_number") or "").strip()
    if not workbook_row:
        workbook_row = _decision_id_row_number(decision_id)
    logistics_alias = _loading_travel_alias(item)
    if not bucket and logistics_alias:
        bucket = logistics_alias
    if bucket in {"overhead", "profit"}:
        default_rows = {
            "insulation": {"overhead": "118", "profit": "120"},
            "roofing": {"overhead": "165", "profit": "167"},
            "flooring": {"overhead": "165", "profit": "167"},
        }
        resolved_row = workbook_row or default_rows.get(template_type, {}).get(bucket, "")
        return {
            "section": "pricing_markup_decisions",
            "decision_id": decision_id or f"pricing_{bucket}",
            "template_bucket": bucket,
            "workbook_row": resolved_row,
        }
    if template_type == "roofing":
        normalized_labor = _roofing_labor_target_for_row(workbook_row)
        if normalized_labor and (
            str(item.get("source") or "") in {"reference_template_summary", "reference_estimate_answer_key"}
            or bucket.startswith("labor_")
        ):
            normalized_decision_id, normalized_bucket, normalized_row = normalized_labor
            return {
                "section": "roofing_labor_template_decisions",
                "decision_id": normalized_decision_id,
                "template_bucket": normalized_bucket,
                "workbook_row": normalized_row,
            }
    if template_type == "insulation":
        if bucket == "foam" or decision_id == "insulation_foam_template_selector":
            resolved_row = workbook_row if workbook_row in {"19", "20", "21"} else ""
            if resolved_row:
                return {
                    "section": "insulation_foam_template_decisions",
                    "decision_id": decision_id if decision_id and decision_id != "insulation_foam_template_selector" else f"insulation_foam_row_{resolved_row}",
                    "template_bucket": "foam",
                    "workbook_row": resolved_row,
                }
            return {
                "section": "insulation_foam_template_decisions",
                "decision_id": "insulation_foam_template_selector",
                "template_bucket": "foam",
                "workbook_row": "19-21",
            }
        if bucket == "thermal_barrier_coating":
            resolved_row = workbook_row if workbook_row in {"30", "31", "32"} else "30"
            return {
                "section": "insulation_thermal_barrier_template_decisions",
                "decision_id": decision_id or f"insulation_thermal_barrier_row_{resolved_row}",
                "template_bucket": "thermal_barrier_coating",
                "workbook_row": resolved_row,
            }
        if bucket in {"membrane", "primer"}:
            resolved_row = workbook_row if workbook_row in {"24", "26"} else ("24" if bucket == "membrane" else "26")
            return {
                "section": "insulation_detail_material_template_decisions",
                "decision_id": decision_id or f"insulation_{bucket}_row_{resolved_row}",
                "template_bucket": bucket,
                "workbook_row": resolved_row,
            }
        if bucket in {"abaa_audit", "abaa_fee"}:
            resolved_row = workbook_row if workbook_row in {"61", "63"} else ("61" if bucket == "abaa_audit" else "63")
            return {
                "section": "insulation_compliance_template_decisions",
                "decision_id": decision_id or f"insulation_{bucket}_row_{resolved_row}",
                "template_bucket": bucket,
                "workbook_row": resolved_row,
            }
        if bucket in {"thinner", "drum_disposal", "disposal", "misc_materials", "misc", "freight", "sales_tax"}:
            row_defaults = {
                "thinner": "37",
                "misc_materials": "57",
                "misc": "57",
                "freight": "59",
                "drum_disposal": "65",
                "disposal": "65",
                "sales_tax": "73",
            }
            resolved_row = workbook_row or row_defaults[bucket]
            normalized_bucket = {
                "misc": "misc_materials",
                "disposal": "drum_disposal",
            }.get(bucket, bucket)
            return {
                "section": "insulation_support_material_template_decisions",
                "decision_id": decision_id or f"insulation_{normalized_bucket}_row_{resolved_row}",
                "template_bucket": normalized_bucket,
                "workbook_row": resolved_row,
            }
        if bucket in {"caulk_detail", "caulk_sealant"}:
            resolved_row = workbook_row if workbook_row in {"41", "43"} else "41"
            return {
                "section": "insulation_detail_material_template_decisions",
                "decision_id": decision_id or f"insulation_caulk_sealant_row_{resolved_row}",
                "template_bucket": "caulk_sealant",
                "workbook_row": resolved_row,
            }
        if bucket in {"lift", "delivery_fee", "generator", "space_heater", "sales_inspection_trips", "truck_expense"}:
            row_defaults = {
                "lift": "47",
                "delivery_fee": "50",
                "generator": "53",
                "space_heater": "55",
                "sales_inspection_trips": "68",
                "truck_expense": "70",
            }
            decision_defaults = {
                "lift": "insulation_lift_equipment",
                "delivery_fee": "insulation_delivery_fee",
                "generator": "insulation_generator",
                "space_heater": "insulation_space_heater",
                "sales_inspection_trips": "insulation_sales_inspection_trips",
                "truck_expense": "insulation_truck_expense",
            }
            resolved_row = workbook_row if workbook_row in INSULATION_REFERENCE_ALLOWED_ROWS.get(bucket, set()) else row_defaults[bucket]
            return {
                "section": "insulation_equipment_logistics_template_decisions",
                "decision_id": decision_id or decision_defaults[bucket],
                "template_bucket": bucket,
                "workbook_row": resolved_row,
            }
        if bucket in {"labor_loading", "labor_traveling", "infrared_scan", "meals_lodging", "labor_meals_lodging"}:
            row_defaults = {
                "labor_loading": "95",
                "labor_traveling": "97",
                "infrared_scan": "99",
                "meals_lodging": "100",
                "labor_meals_lodging": "100",
            }
            decision_defaults = {
                "labor_loading": "insulation_labor_loading_row_95",
                "labor_traveling": "insulation_labor_traveling_row_97",
                "infrared_scan": "insulation_infrared_scan_row_99",
                "meals_lodging": "insulation_meals_lodging_row_100",
                "labor_meals_lodging": "insulation_meals_lodging_row_100",
            }
            normalized_bucket = "meals_lodging" if bucket == "labor_meals_lodging" else bucket
            resolved_row = workbook_row if workbook_row in {"95", "97", "99", "100"} else row_defaults[normalized_bucket]
            return {
                "section": "insulation_logistics_expense_template_decisions",
                "decision_id": decision_id or decision_defaults[bucket],
                "template_bucket": normalized_bucket,
                "workbook_row": resolved_row,
            }
        if bucket.startswith("labor_") and workbook_row:
            return {
                "section": "insulation_labor_template_decisions",
                "decision_id": decision_id or f"insulation_{bucket}_row_{workbook_row}",
                "template_bucket": bucket,
                "workbook_row": workbook_row,
            }
    if template_type == "roofing":
        if bucket in {
            "free_adder",
            "manual_adder",
            "sales_tax",
            "warranty",
            "misc_miles",
            "misc_materials",
            "misc_insurance",
            "permits",
            "misc_materials_misc_insurance_equipment_rental",
        } or (
            item.get("section") == "roofing_free_adder_template_decisions"
        ):
            resolved_row = workbook_row or {
                "sales_tax": "111",
                "warranty": "154",
                "misc_insurance": "156",
                "permits": "158",
                "misc_miles": "174",
                "misc_materials": "175",
                "misc_materials_misc_insurance_equipment_rental": "175",
            }.get(bucket, "173")
            return {
                "section": "roofing_free_adder_template_decisions",
                "decision_id": decision_id or f"roofing_free_adder_row_{resolved_row}",
                "template_bucket": bucket or "free_adder",
                "workbook_row": resolved_row,
            }
        if bucket in {"foam", "roofing_foam"}:
            resolved_row = workbook_row if workbook_row in {"19", "20", "21"} else "19"
            return {
                "section": "roofing_foam_template_decisions",
                "decision_id": f"roofing_foam_row_{resolved_row}",
                "template_bucket": "foam",
                "workbook_row": resolved_row,
            }
        if bucket == "coating":
            resolved_row = workbook_row if workbook_row in {"26", "27", "28"} else "26"
            return {
                "section": "roofing_coating_template_decisions",
                "decision_id": f"roofing_coating_system_row_{resolved_row}",
                "template_bucket": "coating",
                "workbook_row": resolved_row,
            }
        if bucket == "primer":
            return {
                "section": "roofing_primer_template_decisions",
                "decision_id": "roofing_primer_system_row_39",
                "template_bucket": "primer",
                "workbook_row": "39",
            }
        if bucket in {"caulk_detail", "caulk_sealant"}:
            return {
                "section": "roofing_detail_template_decisions",
                "decision_id": decision_id or f"roofing_caulk_sealant_row_{workbook_row or '43'}",
                "template_bucket": "caulk_detail",
                "workbook_row": workbook_row if workbook_row in {"43", "45"} else "43",
            }
        if bucket == "fabric":
            return {
                "section": "roofing_detail_template_decisions",
                "decision_id": decision_id or "roofing_fabric_row_79",
                "template_bucket": "fabric",
                "workbook_row": "79",
            }
        if bucket in {"seams_misc", "seam_treatment"}:
            return {
                "section": "roofing_detail_quantity_template_decisions",
                "decision_id": decision_id or "roofing_seams_misc_row_47",
                "template_bucket": "seams_misc",
                "workbook_row": "47",
            }
        if bucket in {"penetrations", "hvac_units", "drains"}:
            row_defaults = {"penetrations": "49", "hvac_units": "51", "drains": "53"}
            return {
                "section": "roofing_detail_quantity_template_decisions",
                "decision_id": decision_id or f"roofing_{bucket}_row_{row_defaults[bucket]}",
                "template_bucket": bucket,
                "workbook_row": row_defaults[bucket],
            }
        if bucket == "thinner":
            return {
                "section": "roofing_accessory_template_decisions",
                "decision_id": decision_id or "roofing_thinner_row_33",
                "template_bucket": "thinner",
                "workbook_row": "33",
            }
        if bucket in {"edge_metal", "gutter", "downspouts", "roof_hatch", "scuppers", "curbs", "ladders", "pitch_pockets", "misc"}:
            row_defaults = {
                "edge_metal": "82",
                "gutter": "84",
                "downspouts": "86",
                "roof_hatch": "88",
                "scuppers": "90",
                "curbs": "92",
                "ladders": "94",
                "pitch_pockets": "96",
                "misc": "101",
            }
            resolved_row = workbook_row or row_defaults[bucket]
            return {
                "section": "roofing_accessory_template_decisions",
                "decision_id": decision_id or f"roofing_{bucket}_row_{resolved_row}",
                "template_bucket": bucket,
                "workbook_row": resolved_row,
            }
        if bucket == "board_stock":
            resolved_row = workbook_row if workbook_row in {"58", "59", "60"} else "58"
            return {
                "section": "roofing_board_fastener_template_decisions",
                "decision_id": decision_id or f"roofing_board_stock_row_{resolved_row}",
                "template_bucket": "board_stock",
                "workbook_row": resolved_row,
            }
        if bucket == "fasteners":
            return {
                "section": "roofing_board_fastener_template_decisions",
                "decision_id": decision_id or "roofing_fasteners_row_63",
                "template_bucket": "fasteners",
                "workbook_row": "63",
            }
        if bucket == "plates":
            return {
                "section": "roofing_board_fastener_template_decisions",
                "decision_id": decision_id or "roofing_plates_row_65",
                "template_bucket": "plates",
                "workbook_row": "65",
            }
        if bucket == "granules":
            return {
                "section": "roofing_granules_template_decisions",
                "decision_id": decision_id or "roofing_granules_row_36",
                "template_bucket": "granules",
                "workbook_row": "36",
            }
        if bucket in {"dumpster", "disposal"}:
            return {
                "section": "roofing_equipment_template_decisions",
                "decision_id": decision_id or "roofing_dumpsters_row_69",
                "template_bucket": "dumpster",
                "workbook_row": "69",
            }
        if bucket == "lift":
            resolved_row = workbook_row if workbook_row in {"73", "74"} else "73"
            return {
                "section": "roofing_equipment_template_decisions",
                "decision_id": decision_id or f"roofing_lift_equipment_row_{resolved_row}",
                "template_bucket": "lift",
                "workbook_row": resolved_row,
            }
        if bucket == "generator":
            return {
                "section": "roofing_equipment_template_decisions",
                "decision_id": "roofing_generator_row_99",
                "template_bucket": "generator",
                "workbook_row": "99",
            }
        if bucket == "delivery_fee":
            return {
                "section": "roofing_travel_freight_template_decisions",
                "decision_id": decision_id or "roofing_delivery_fee_row_76",
                "template_bucket": "delivery_fee",
                "workbook_row": "76",
            }
        if bucket == "freight":
            return {
                "section": "roofing_travel_freight_template_decisions",
                "decision_id": decision_id or "roofing_freight_row_103",
                "template_bucket": "freight",
                "workbook_row": "103",
            }
        if bucket in {"sales_trips", "sales_inspection_trips"}:
            return {
                "section": "roofing_travel_freight_template_decisions",
                "decision_id": decision_id or "roofing_sales_trips_row_106",
                "template_bucket": "sales_trips",
                "workbook_row": "106",
            }
        if bucket == "truck_expense":
            return {
                "section": "roofing_travel_freight_template_decisions",
                "decision_id": decision_id or "roofing_truck_expense_row_108",
                "template_bucket": "truck_expense",
                "workbook_row": "108",
            }
        if bucket in {"labor_loading", "labor_traveling", "labor_infrared_scan", "infrared_scan", "labor_meals_lodging", "meals_lodging"}:
            row_defaults = {
                "labor_loading": "136",
                "labor_traveling": "138",
                "labor_infrared_scan": "141",
                "infrared_scan": "141",
                "labor_meals_lodging": "144",
                "meals_lodging": "144",
            }
            decision_defaults = {
                "labor_loading": "roofing_labor_loading_row_136",
                "labor_traveling": "roofing_labor_traveling_row_138",
                "labor_infrared_scan": "roofing_infrared_scan_row_141",
                "infrared_scan": "roofing_infrared_scan_row_141",
                "labor_meals_lodging": "roofing_meals_lodging_row_144",
                "meals_lodging": "roofing_meals_lodging_row_144",
            }
            normalized_bucket = {
                "labor_infrared_scan": "infrared_scan",
                "labor_meals_lodging": "meals_lodging",
            }.get(bucket, bucket)
            resolved_row = workbook_row if workbook_row in {"136", "138", "141", "144"} else row_defaults[bucket]
            return {
                "section": "roofing_logistics_expense_template_decisions",
                "decision_id": decision_id or decision_defaults[bucket],
                "template_bucket": normalized_bucket,
                "workbook_row": resolved_row,
            }
        if bucket.startswith("labor_") and workbook_row:
            labor_row_defaults = {
                "labor_prep": "116",
                "labor_prime": "118",
                "labor_seam_sealer": "120",
                "labor_base": "122",
                "labor_top_coat": "124",
                "labor_caulk": "126",
                "labor_details": "128",
                "labor_top_coat_granules": "130",
                "labor_cleanup": "132",
                "labor_misc": "134",
                "labor_loading": "136",
                "labor_traveling": "138",
                "labor_infrared_scan": "141",
                "labor_meals_lodging": "144",
            }
            resolved_row = workbook_row or labor_row_defaults.get(bucket, workbook_row)
            return {
                "section": "roofing_labor_template_decisions",
                "decision_id": decision_id or f"roofing_{bucket}_row_{resolved_row}",
                "template_bucket": bucket,
                "workbook_row": resolved_row,
            }
    if template_type == "flooring":
        if bucket in {
            "foam",
            "floor_base_coat",
            "floor_topcoat",
            "floor_coating",
            "floor_primer",
            "floor_flake",
            "coating",
            "primer",
            "thinner",
            "granules",
            "caulk_detail",
            "caulk_sealant",
            "seams_misc",
            "seam_treatment",
            "penetrations",
            "hvac_units",
            "drains",
            "board_stock",
            "fasteners",
            "plates",
            "fabric",
        }:
            normalized_bucket = {
                "coating": "floor_coating",
                "primer": "floor_primer",
            }.get(bucket, bucket)
            resolved_row = workbook_row or {
                "floor_base_coat": "26",
                "floor_topcoat": "27",
                "floor_coating": "28",
                "floor_primer": "39",
                "thinner": "33",
                "granules": "36",
                "caulk_detail": "43",
                "caulk_sealant": "43",
                "seams_misc": "47",
                "seam_treatment": "47",
                "penetrations": "49",
                "hvac_units": "51",
                "drains": "53",
                "board_stock": "58",
                "fasteners": "63",
                "plates": "65",
                "fabric": "79",
                "foam": "19",
            }.get(normalized_bucket, workbook_row)
            normalized_bucket = "seams_misc" if normalized_bucket == "seam_treatment" else normalized_bucket
            return {
                "section": "flooring_material_template_decisions",
                "decision_id": decision_id or f"flooring_{normalized_bucket}_row_{resolved_row}",
                "template_bucket": normalized_bucket,
                "workbook_row": resolved_row,
            }
        if bucket in {"dumpster", "dumpsters", "disposal", "lift", "delivery_fee", "generator", "freight", "sales_inspection_trips", "truck_expense"}:
            normalized_bucket = "dumpster" if bucket in {"dumpsters", "disposal"} else bucket
            resolved_row = workbook_row or {
                "dumpster": "69",
                "lift": "73",
                "delivery_fee": "76",
                "generator": "99",
                "freight": "103",
                "sales_inspection_trips": "106",
                "truck_expense": "108",
            }.get(normalized_bucket, workbook_row)
            return {
                "section": "flooring_equipment_logistics_template_decisions",
                "decision_id": decision_id or f"flooring_{normalized_bucket}_row_{resolved_row}",
                "template_bucket": normalized_bucket,
                "workbook_row": resolved_row,
            }
        if bucket in {"sales_tax", "warranty", "misc_insurance", "permits", "misc_materials"}:
            resolved_row = workbook_row or {
                "sales_tax": "111",
                "warranty": "154",
                "misc_insurance": "156",
                "permits": "158",
                "misc_materials": "174",
            }.get(bucket, workbook_row)
            return {
                "section": "flooring_free_adder_template_decisions",
                "decision_id": decision_id or f"flooring_{bucket}_row_{resolved_row}",
                "template_bucket": bucket,
                "workbook_row": resolved_row,
            }
        if bucket in {"labor_loading", "labor_traveling", "infrared_scan", "labor_infrared_scan", "meals_lodging", "labor_meals_lodging"}:
            normalized_bucket = {
                "labor_infrared_scan": "infrared_scan",
                "labor_meals_lodging": "meals_lodging",
            }.get(bucket, bucket)
            resolved_row = workbook_row or {
                "labor_loading": "137",
                "labor_traveling": "139",
                "infrared_scan": "142",
                "meals_lodging": "145",
            }.get(normalized_bucket, workbook_row)
            return {
                "section": "flooring_logistics_expense_template_decisions",
                "decision_id": decision_id or f"flooring_{normalized_bucket}_row_{resolved_row}",
                "template_bucket": normalized_bucket,
                "workbook_row": resolved_row,
            }
        if bucket.startswith("labor_") and workbook_row:
            return {
                "section": "flooring_labor_template_decisions",
                "decision_id": decision_id or f"flooring_{bucket}_row_{workbook_row}",
                "template_bucket": bucket,
                "workbook_row": workbook_row,
            }
        if bucket.startswith("labor_"):
            labor_row_defaults = {
                "labor_prep": "116",
                "labor_prime": "118",
                "labor_seam_sealer": "120",
                "labor_base": "122",
                "labor_top_coat": "124",
                "labor_caulk": "126",
                "labor_details": "128",
                "labor_top_coat_granules": "130",
                "labor_cleanup": "132",
                "labor_misc": "134",
                "labor_loading": "136",
                "labor_traveling": "138",
                "labor_infrared_scan": "141",
                "labor_meals_lodging": "144",
            }
            resolved_row = labor_row_defaults.get(bucket)
            if resolved_row:
                return {
                    "section": "roofing_labor_template_decisions",
                    "decision_id": decision_id or f"roofing_{bucket}_row_{resolved_row}",
                    "template_bucket": bucket,
                    "workbook_row": resolved_row,
                }
    if decision_id and workbook_row and item.get("section"):
        return {
            "section": str(item.get("section")),
            "decision_id": decision_id,
            "template_bucket": bucket,
            "workbook_row": workbook_row,
        }
    return None


def _roofing_labor_target_for_row(workbook_row: Any) -> tuple[str, str, str] | None:
    row = str(workbook_row or "").strip()
    if row.endswith(".0"):
        row = row[:-2]
    by_row = {
        "116": ("roofing_labor_prep_row_116", "labor_prep", "116"),
        "118": ("roofing_labor_prime_row_118", "labor_prime", "118"),
        "120": ("roofing_labor_seam_sealer_row_120", "labor_seam_sealer", "120"),
        "122": ("roofing_labor_base_row_122", "labor_base", "122"),
        "124": ("roofing_labor_top_coat_row_124", "labor_top_coat", "124"),
        "126": ("roofing_labor_caulk_row_126", "labor_caulk", "126"),
        "128": ("roofing_labor_details_row_128", "labor_details", "128"),
        "130": ("roofing_labor_top_coat_granules_row_130", "labor_top_coat_granules", "130"),
        "132": ("roofing_labor_cleanup_row_132", "labor_cleanup", "132"),
        "134": ("roofing_labor_misc_row_134", "labor_misc", "134"),
    }
    return by_row.get(row)


def _decision_id_row_number(decision_id: Any) -> str:
    match = re.search(r"(?:^|_)row_(\d{2,3})(?:$|_)", str(decision_id or ""))
    return match.group(1) if match else ""


def _clean_chat_proposed_values(item: dict[str, Any], *, template_type: str = "") -> dict[str, Any]:
    values = dict(item.get("proposed_values") or {})
    for field in CHAT_ESTIMATOR_OVERRIDE_FIELDS:
        if field in item and item.get(field) not in (None, ""):
            values.setdefault(field, item.get(field))
    bucket = _canonical_package(item.get("template_bucket") or item.get("package") or item.get("category"))
    workbook_row = str(item.get("workbook_row") or item.get("row_number") or "").strip()
    logistics_alias = _loading_travel_alias(item)
    if not bucket and logistics_alias:
        bucket = logistics_alias
    loading_travel_rows = {"95", "97"} if template_type == "insulation" else {"136", "138"} if template_type == "roofing" else set()
    if (
        (template_type in {"insulation", "roofing"} and bucket in {"labor_loading", "labor_traveling"})
        or workbook_row in loading_travel_rows
    ):
        row_number = workbook_row or ("95" if bucket == "labor_loading" else "97")
        if template_type == "roofing" and not workbook_row:
            row_number = "136" if bucket == "labor_loading" else "138"
        if values.get("hours_per_day") in (None, ""):
            values["hours_per_day"] = _first_value(values, "hours", "total_hours", "days")
        if values.get("people_count") in (None, ""):
            values["people_count"] = _first_value(values, "crew_size")
        default_hours = 0.5 if row_number in {"95", "136"} else 2.5
        default_people = 1.0 if row_number in {"95", "136"} else 4.0
        default_rate = 25.5 if row_number in {"95", "136"} else 13.0
        max_hours = 2.0 if row_number in {"95", "136"} else 6.0
        hours = _safe_number(values.get("hours_per_day"), 0.0)
        people = _safe_number(values.get("people_count"), 0.0)
        rate = _safe_number(values.get("unit_price"), 0.0)
        values["hours_per_day"] = default_hours if hours <= 0 or hours > max_hours else hours
        values["people_count"] = default_people if people <= 0 else people
        values["unit_price"] = default_rate if rate <= 0 or rate > default_rate * 1.5 else rate
        allowed = {"hours_per_day", "people_count", "trip_count", "unit_price", "round_trip_miles"}
        return {key: value for key, value in values.items() if key in allowed and value is not None}
    if template_type == "insulation" and (bucket == "foam" or workbook_row in {"19", "20", "21", "19-21"}):
        if str(item.get("source") or "") not in {"reference_template_summary", "reference_estimate_answer_key"}:
            values.pop("yield_or_coverage", None)
            values.pop("foam_yield_or_coverage", None)
            values.pop("foam_yield", None)
        values.pop("estimated_units", None)
        values.pop("estimated_sets", None)
        values.pop("estimated_cost", None)
    if template_type in {"insulation", "roofing"} and (
        bucket in {"infrared_scan", "labor_infrared_scan"}
        or (workbook_row in {"99", "141"} and bucket in {"", "infrared_scan", "labor_infrared_scan"})
    ):
        if values.get("hours_per_day") in (None, ""):
            values["hours_per_day"] = _first_value(values, "hours", "total_hours", "days")
        return {key: value for key, value in values.items() if key in {"hours_per_day", "unit_price"} and value is not None}
    if template_type in {"insulation", "roofing"} and (
        bucket in {"meals_lodging", "labor_meals_lodging"} or (workbook_row in {"100", "144"} and bucket in {"", "meals_lodging", "labor_meals_lodging"})
    ):
        if values.get("people_count") in (None, ""):
            values["people_count"] = _first_value(values, "crew_size")
        return {key: value for key, value in values.items() if key in {"days", "people_count", "unit_price"} and value is not None}
    return {key: value for key, value in values.items() if value is not None}


def _scope_area(scope: dict[str, Any]) -> float:
    return _safe_number(
        scope.get("net_sqft")
        or scope.get("estimated_sqft")
        or scope.get("gross_sqft")
        or scope.get("net_insulation_area_sqft")
        or scope.get("gross_insulation_area_sqft"),
        0.0,
    )


def _reference_area(row: dict[str, Any]) -> float:
    return _safe_number(
        row.get("area_sqft")
        or row.get("basis_sqft")
        or row.get("quantity")
        or row.get("estimated_sqft")
        or row.get("area_basis_sqft"),
        0.0,
    )


def _included_workbench_packages(workbench: dict[str, Any]) -> set[str]:
    packages: set[str] = set()
    for section in WORKBENCH_MATERIAL_SECTIONS:
        for row in workbench.get(section) or []:
            if not isinstance(row, dict) or not row.get("include"):
                continue
            for key in ("template_bucket", "package_key", "category", "labor_package", "task"):
                package = _canonical_package(row.get(key))
                if package:
                    packages.add(package)
    return packages


def _loading_travel_alias(item: dict[str, Any]) -> str:
    text = _norm(
        " ".join(
            str(item.get(key) or "")
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
    )
    tokenized = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    if re.search(r"\b(?:labor\s+)?loading\b", text) or "labor_loading" in tokenized:
        return "labor_loading"
    if re.search(r"\b(?:labor\s+)?travel(?:ing)?\b", text) or "labor_traveling" in tokenized:
        return "labor_traveling"
    return ""


def _canonical_package(value: Any) -> str:
    text = _norm(value).replace(" ", "_")
    return PACKAGE_COMPANION_ALIASES.get(text, text)


def _first_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        try:
            if pd.isna(value):
                continue
        except (TypeError, ValueError):
            pass
        if value not in (None, ""):
            return value
    return ""


def _companion_proposal(
    target_spec: dict[str, str],
    *,
    anchor: str,
    target: str,
    row: dict[str, Any],
    rate: float,
    job_count: int,
) -> DecisionProposal:
    reason = (
        f"Historical companion suggestion: {target.replace('_', ' ')} appeared with "
        f"{anchor.replace('_', ' ')} in {rate:.0%} of {job_count} comparable job(s); verify scope and quantity."
    )
    evidence = {
        "relationship_package_cooccurrence": [
            {
                "anchor_package": anchor,
                "suggested_package": target,
                "co_occurrence_rate": rate,
                "job_count": job_count,
                "project_type": row.get("project_type"),
                "substrate": row.get("substrate"),
                "supporting_job_ids": row.get("supporting_job_ids"),
            }
        ]
    }
    confidence = min(0.9, 0.35 + (rate * 0.4) + min(job_count, 20) / 100)
    return DecisionProposal(
        decision_id=target_spec["decision_id"],
        template_type="roofing",
        template_bucket=target_spec["template_bucket"],
        workbook_row=target_spec["workbook_row"],
        # Co-occurrence is evidence, not current-job scope authorization.
        include=None,
        proposed_values={},
        confidence=round(confidence, 4),
        review_required=True,
        review_reasons=[reason],
        evidence=evidence,
        source="historical_companion",
        section=target_spec["section"],
    )


def _proposal_value_can_fill(value: Any) -> bool:
    if value in (None, ""):
        return True
    if isinstance(value, (int, float)):
        return float(value) == 0.0
    return False


def _workbench_template_type(workbench: dict[str, Any], proposals: list[dict[str, Any]]) -> str:
    scope = workbench.get("scope") or {}
    explicit = str(scope.get("template_type") or "").strip().lower()
    if explicit:
        return explicit
    if _is_insulation_scope(scope):
        return "insulation"
    if any(str(p.get("template_type") or "").strip().lower() == "roofing" for p in proposals):
        return "roofing"
    if any(str(key).startswith("roofing_") for key in workbench):
        return "roofing"
    return "insulation" if any(str(key).startswith("insulation_") for key in workbench) else "roofing"


def _merge_duplicate_rows(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    merged["include"] = bool(existing.get("include") or incoming.get("include"))
    merged["compatibility_warnings"] = list(
        dict.fromkeys([*(existing.get("compatibility_warnings") or []), *(incoming.get("compatibility_warnings") or [])])
    )
    merged["proposal_evidence"] = _merge_evidence(existing.get("proposal_evidence") or {}, incoming.get("proposal_evidence") or {})
    merged.update(_decision_evidence_fields(merged))
    return merged


def _decision_evidence_summary(row: dict[str, Any]) -> str:
    return _decision_evidence_fields(row)["decision_evidence_summary"]


def _decision_evidence_fields(row: dict[str, Any]) -> dict[str, Any]:
    evidence_types: list[str] = []
    why_included = _why_included_summary(row)
    reference = _reference_project_evidence_summary(row)
    historical = _historical_evidence_summary(row)
    pricing = _pricing_evidence_summary(row)
    product = _product_evidence_summary(row)
    formula = _formula_evidence_summary(row)
    chat = _chat_estimator_evidence_summary(row)

    if reference:
        evidence_types.append("reference_project")
    if chat:
        evidence_types.append("chat_estimator")
    if _has_note_evidence(row):
        evidence_types.append("note")
    if historical:
        evidence_types.append("historical")
    if pricing:
        evidence_types.append("pricing")
    if product:
        evidence_types.append("product")
    if formula:
        evidence_types.append("formula")

    parts: list[str] = []
    if reference:
        parts.append("reference project evidence")
    if chat:
        parts.append("chat estimator evidence")
    if _has_note_evidence(row):
        parts.append("note evidence")
    if historical:
        count = row.get("decision_evidence_count") or row.get("historical_selector_evidence_count")
        parts.append(f"historical evidence{f' ({count})' if count else ''}")
    if pricing:
        parts.append("pricing/material evidence")
    if product:
        parts.append("product guidance")
    if formula:
        parts.append("formula preview")
    return {
        "decision_evidence_summary": ", ".join(dict.fromkeys(parts)),
        "decision_evidence_types": ", ".join(evidence_types),
        "why_included": why_included,
        "reference_project_evidence_summary": reference,
        "chat_estimator_evidence_summary": chat,
        "historical_evidence_summary": historical,
        "pricing_evidence_summary": pricing,
        "product_evidence_summary": product,
        "formula_evidence_summary": formula,
    }


def _has_note_evidence(row: dict[str, Any]) -> bool:
    evidence = row.get("proposal_evidence") or {}
    return bool(evidence.get("note"))


def _chat_estimator_evidence_summary(row: dict[str, Any]) -> str:
    evidence = row.get("proposal_evidence") or {}
    rows = evidence.get("chat_estimator") or []
    if not rows:
        return ""
    first = rows[0] if isinstance(rows[0], dict) else {}
    message = str(first.get("assistant_message") or "").strip()
    return message[:180] if message else "Estimator chat proposal"


def _why_included_summary(row: dict[str, Any]) -> str:
    if not row.get("include"):
        return ""
    reasons = [str(item) for item in row.get("proposal_review_reasons") or [] if item]
    evidence = row.get("proposal_evidence") or {}
    reference = _reference_project_evidence_summary(row)
    chat = _chat_estimator_evidence_summary(row)
    note_text = _note_evidence_text(evidence)
    companion = _companion_evidence_summary(evidence)
    source = str(row.get("proposal_source") or "").strip().lower()
    parts: list[str] = []
    if source == "estimator_edit" or row.get("manual_override"):
        parts.append("Estimator selected this row.")
    elif note_text:
        parts.append(f"Notes mention: {_shorten(note_text, 120)}.")
    elif chat:
        parts.append(f"Chat requested: {_shorten(chat, 120)}.")
    elif reference:
        parts.append(f"Reference project: {reference}.")
    elif companion:
        parts.append(companion + ".")
    elif row.get("decision_evidence_count") or row.get("historical_selector_evidence_count"):
        recommendation = str(row.get("historical_selector_recommendation") or row.get("historical_recommendation") or "").strip()
        count = int(_safe_number(row.get("decision_evidence_count") or row.get("historical_selector_evidence_count"), 0))
        if recommendation and count:
            parts.append(f"Historical support: {recommendation} from {count} row{'s' if count != 1 else ''}.")
        elif count:
            parts.append(f"Historical support: used in {count} row{'s' if count != 1 else ''}.")
    elif source:
        parts.append("Selected for the current scope.")
    else:
        parts.append("Selected for the current scope.")
    if reasons:
        parts.append("Review: " + "; ".join(_shorten(reason, 140) for reason in reasons[:2]) + ".")
    return " ".join(parts)


def _note_evidence_text(evidence: dict[str, Any]) -> str:
    rows = evidence.get("note") or []
    if not rows:
        return ""
    first = rows[0]
    if isinstance(first, dict):
        return str(first.get("text") or first.get("quote") or first.get("summary") or "").strip()
    return str(first).strip()


def _companion_evidence_summary(evidence: dict[str, Any]) -> str:
    rows = evidence.get("relationship_package_cooccurrence") or []
    if not rows:
        return ""
    first = rows[0] if isinstance(rows[0], dict) else {}
    anchor = str(first.get("anchor_package") or first.get("package_a") or "").replace("_", " ").strip()
    rate = _safe_number(first.get("co_occurrence_rate"), 0)
    jobs = int(_safe_number(first.get("job_count"), 0))
    project_type = str(first.get("project_type") or "").strip()
    parts = []
    if anchor:
        parts.append(f"Often paired with {anchor}")
    else:
        parts.append("Often paired with selected materials")
    if rate > 0:
        parts.append(f"{rate:.0%} of comparable jobs")
    if jobs > 0:
        parts.append(f"{jobs} job{'s' if jobs != 1 else ''}")
    if project_type and project_type.lower() != "unknown":
        parts.append(project_type)
    return "; ".join(parts)


def _historical_evidence_summary(row: dict[str, Any]) -> str:
    count = int(_safe_number(row.get("decision_evidence_count") or row.get("historical_selector_evidence_count"), 0))
    if count <= 0:
        return ""
    confidence = str(row.get("decision_confidence") or row.get("historical_selector_confidence") or "").strip()
    recommendation = str(row.get("historical_selector_recommendation") or row.get("historical_recommendation") or "").strip()
    parts = [f"{count} historical decision row{'s' if count != 1 else ''}"]
    if confidence:
        parts.append(f"confidence {confidence}")
    if recommendation:
        parts.append(f"recommendation {recommendation}")
    return "; ".join(parts)


def _reference_project_evidence_summary(row: dict[str, Any]) -> str:
    evidence = row.get("proposal_evidence") or {}
    rows = evidence.get("reference_project") or []
    if not rows:
        return ""
    item = rows[0] if isinstance(rows[0], dict) else {}
    job_id = str(item.get("job_id") or "").strip()
    bucket = str(item.get("template_bucket") or "").strip()
    scale = _safe_number(item.get("scale_factor"), 0)
    parts = []
    if job_id:
        parts.append(f"reference job {job_id}")
    if bucket:
        parts.append(f"bucket {bucket}")
    if scale > 0:
        parts.append(f"scale {scale:.3g}x")
    return "; ".join(parts)


def _pricing_evidence_summary(row: dict[str, Any]) -> str:
    candidate = str(row.get("selected_pricing_candidate") or row.get("item_name") or "").strip()
    unit_price = _safe_number(row.get("unit_price") or row.get("current_unit_price") or row.get("current_price"), 0)
    source = ""
    for item in _pricing_candidates(row):
        if candidate and _norm(item.get("item_name")) == _norm(candidate):
            source = str(item.get("source") or item.get("why_suggested") or "").strip()
            break
    if unit_price <= 0:
        return ""
    parts = []
    if candidate:
        parts.append(candidate)
    parts.append(f"unit price {unit_price:g}")
    if source:
        parts.append(f"source {source}")
    return "; ".join(parts)


def _product_evidence_summary(row: dict[str, Any]) -> str:
    product_name = str(row.get("product_name") or row.get("product_knowledge_product_name") or "").strip()
    product_id = str(row.get("product_id") or "").strip()
    guidance = str(row.get("product_guidance") or "").strip()
    status = str(row.get("product_guidance_status") or "").strip()
    if not any((product_name, product_id, guidance, status == "matched")):
        return ""
    label = product_name or product_id or "Product guidance matched"
    return label + (f"; {_shorten(guidance, 160)}" if guidance else "")


def _formula_evidence_summary(row: dict[str, Any]) -> str:
    formula = str(row.get("formula_model") or row.get("formula_source") or "").strip()
    output = str(row.get("calculated_output_summary") or "").strip()
    if not formula and not output:
        return ""
    if formula and output:
        return f"{formula}; {output}"
    return formula or output


def _pricing_candidates(row: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(row.get("pricing_candidates"), list):
        return [dict(item) for item in row.get("pricing_candidates") or [] if isinstance(item, dict)]
    candidates: list[dict[str, Any]] = []
    for key in ("pricing_candidates_json", "pricing_options_json", "item_options_json"):
        try:
            parsed = json.loads(row.get(key) or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = []
        for item in parsed:
            if isinstance(item, dict):
                candidates.append(dict(item))
        if candidates:
            break
    return candidates


def _safe_number(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _shorten(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _roofing_scope_proposals(scope: dict[str, Any], notes: str) -> list[DecisionProposal]:
    template_type = "roofing"
    proposals: list[DecisionProposal] = []
    area = _first_positive(scope, "estimated_sqft", "net_sqft", "gross_sqft")
    normalized_notes = _norm(notes)
    coating_scope = bool(
        scope.get("coating_required")
        or scope.get("coating_type")
        or "coating" in _norm(scope.get("project_type"))
        or any(
            phrase in normalized_notes
            for phrase in (
                "coating path",
                "coating restoration",
                "restoration review",
                "restoration seems possible",
                "repairs plus a coating",
            )
        )
    )
    if coating_scope:
        reasons = []
        if scope.get("coating_path_review"):
            reasons.append("Conditional coating/restoration path requires estimator qualification.")
        if not scope.get("coating_type"):
            reasons.append("Coating chemistry/product was not stated.")
        if not scope.get("warranty_target_years") and not scope.get("warranty_years"):
            reasons.append("Warranty duration was not stated.")
        for row in ("26",):
            proposals.append(
                _proposal(
                    template_type,
                    "roofing_coating_template_decisions",
                    f"roofing_coating_system_row_{row}",
                    "coating",
                    row,
                    include=True,
                    values={"basis_sqft": area} if area else {},
                    confidence=0.85 if "coating" in notes else 0.65,
                    review_reasons=reasons,
                    note=_snippet(notes, ["coating", "restoration", "top coat"]),
                    source="explicit_note" if "coating" in notes or "restoration" in notes else "deterministic_rule",
                )
            )
    text = _norm(notes)
    flag_blob = " ".join(str(flag) for flag in scope.get("condition_detail_flags") or [])
    if any(term in text or term in flag_blob for term in ("primer", "prime", "rust", "fastener")):
        primer_review = bool(
            re.search(r"\b(review|verify|evaluate|confirm)\s+(?:\w+\s+){0,6}(primer|priming)\b", text)
            or re.search(r"\b(primer|priming)\s+(?:\w+\s+){0,4}(need|needs|required|requirement|requirements)\b", text)
        )
        proposals.append(
            _proposal(
                template_type,
                "roofing_primer_template_decisions",
                "roofing_primer_system_row_39",
                "primer",
                "39",
                include=True,
                confidence=0.55 if primer_review else 0.75,
                review_reasons=["Primer was mentioned as a review/verification item; estimator must confirm before pricing."] if primer_review else [],
                note=_snippet(notes, ["primer", "rust", "fastener"]),
            )
        )
    if any(term in text or term in flag_blob for term in ("caulk", "sealant", "penetration", "detail")):
        proposals.append(_proposal(template_type, "roofing_detail_template_decisions", "roofing_caulk_sealant_row_43", "caulk_sealant", "43", include=True, confidence=0.75, note=_snippet(notes, ["caulk", "sealant", "penetration", "detail"]), source="explicit_note"))
        proposals.append(_proposal(template_type, "roofing_detail_quantity_template_decisions", "roofing_penetrations_row_49", "penetrations", "49", include=True, confidence=0.7, review_reasons=["Detail quantity requires estimator count if units were not stated."], note=_snippet(notes, ["penetration", "detail"])))
    if any(term in text or term in flag_blob for term in ("seam", "seams")):
        proposals.append(_proposal(template_type, "roofing_detail_quantity_template_decisions", "roofing_seams_misc_row_47", "seams_misc", "47", include=True, confidence=0.7, review_reasons=["Seam quantity requires estimator linear footage if not stated."], note=_snippet(notes, ["seam", "seams"])))
        proposals.append(_proposal(template_type, "roofing_labor_template_decisions", "roofing_labor_seam_sealer_row_120", "labor_seam_sealer", "120", include=True, confidence=0.7, note=_snippet(notes, ["seam", "seams"])))
    if "fabric" in text or "reinforcement" in text:
        proposals.append(_proposal(template_type, "roofing_detail_template_decisions", "roofing_fabric_row_79", "fabric", "79", include=True, confidence=0.7, review_reasons=["Fabric/reinforcement extent requires estimator review."], note=_snippet(notes, ["fabric", "reinforcement"])))
        proposals.append(_proposal(template_type, "roofing_labor_template_decisions", "roofing_labor_seam_sealer_row_120", "labor_seam_sealer", "120", include=True, confidence=0.65, review_reasons=["Fabric/reinforcement usually needs seam/detail labor; verify extent."], note=_snippet(notes, ["fabric", "reinforcement"])))
    if any(term in text for term in ("full tear off", "full tear-off", "tear off", "tear-off", "tearoff", "remove wet", "wet insulation", "damaged board", "replace board")):
        for section, decision_id, bucket, row in (
            ("roofing_board_fastener_template_decisions", "roofing_board_stock_row_58", "board_stock", "58"),
            ("roofing_board_fastener_template_decisions", "roofing_fasteners_row_63", "fasteners", "63"),
            ("roofing_board_fastener_template_decisions", "roofing_plates_row_65", "plates", "65"),
            ("roofing_equipment_template_decisions", "roofing_dumpsters_row_69", "dumpster", "69"),
        ):
            proposals.append(
                _proposal(
                    template_type,
                    section,
                    decision_id,
                    bucket,
                    row,
                    include=True,
                    confidence=0.75,
                    review_reasons=["Tear-off/recover companion row requires estimator confirmation of system, quantity, and disposal needs."],
                    note=_snippet(notes, ["tear off", "tear-off", "wet insulation", "damaged board", "replace board"]),
                )
            )
    if "generator" in text:
        proposals.append(_proposal(template_type, "roofing_equipment_template_decisions", "roofing_generator_row_99", "generator", "99", include=True, confidence=0.8, note=_snippet(notes, ["generator"])))
    if any(term in text for term in ("lift", "equipment access", "access/equipment")):
        proposals.append(_proposal(template_type, "roofing_equipment_template_decisions", "roofing_lift_equipment_row_73", "lift", "73", include=True, confidence=0.65, review_reasons=["Access equipment type/period requires estimator confirmation."], note=_snippet(notes, ["lift", "access", "equipment"]), source="explicit_note"))
    for decision_id, bucket, row, terms in (
        ("roofing_truck_expense_row_108", "truck_expense", "108", ["truck", "truck expense", "miles", "mileage", "round trip"]),
        ("roofing_labor_loading_row_136", "labor_loading", "136", ["loading", "setup", "set up"]),
        ("roofing_labor_traveling_row_138", "labor_traveling", "138", ["travel"]),
        ("roofing_labor_details_row_128", "labor_details", "128", ["details", "detail"]),
        ("roofing_labor_cleanup_row_132", "labor_cleanup", "132", ["cleanup", "clean up"]),
    ):
        if any(term in text for term in terms):
            section = "roofing_travel_freight_template_decisions" if bucket == "truck_expense" else "roofing_labor_template_decisions"
            proposals.append(_proposal(template_type, section, decision_id, bucket, row, include=True, confidence=0.7, note=_snippet(notes, terms)))
    return proposals


def _insulation_scope_proposals(scope: dict[str, Any], notes: str) -> list[DecisionProposal]:
    template_type = "insulation"
    proposals = [
        _proposal(
            template_type,
            "insulation_foam_template_decisions",
            "insulation_foam_template_selector",
            "foam",
            "19-21",
            include=True,
            values={
                "basis_sqft": _first_positive(scope, "estimated_sqft", "net_insulation_area_sqft", "gross_insulation_area_sqft"),
                "thickness_inches": _first_positive(scope, "foam_thickness_inches", "thickness_inches"),
            },
            confidence=0.85,
            review_reasons=[] if scope.get("foam_type") else ["Foam type was not stated; estimator must confirm open-cell vs closed-cell."],
            note=_snippet(notes, ["spray foam", "foam", "insulation"]),
        )
    ]
    text = _norm(notes)
    if any(term in text for term in ("thermal barrier", "ignition barrier", "dc315")):
        proposals.append(_proposal(template_type, "insulation_thermal_barrier_template_decisions", "insulation_thermal_barrier_row_30", "thermal_barrier_coating", "30", include=True, confidence=0.7, review_reasons=["Thermal/ignition barrier requirement requires estimator confirmation."], note=_snippet(notes, ["thermal barrier", "ignition barrier", "dc315"])))
    if any(term in text for term in ("sealant", "sealing", "caulk", "corners", "doors", "detail")):
        proposals.append(_proposal(template_type, "insulation_detail_material_template_decisions", "insulation_caulk_sealant_row_41", "caulk_sealant", "41", include=True, confidence=0.7, review_reasons=["Sealant/detail quantity requires estimator review."], note=_snippet(notes, ["sealant", "sealing", "caulk", "detail"])))
    for decision_id, bucket, row, section, terms in (
        ("insulation_generator_row_53", "generator", "53", "insulation_equipment_logistics_template_decisions", ["generator", "temp power", "temporary power"]),
        ("insulation_lift_equipment_row_47", "lift", "47", "insulation_equipment_logistics_template_decisions", ["lift", "access", "equipment"]),
        ("insulation_truck_expense_row_70", "truck_expense", "70", "insulation_equipment_logistics_template_decisions", ["truck", "truck expense", "miles", "mileage", "round trip"]),
        ("insulation_labor_mask_row_80", "labor_mask", "80", "insulation_labor_template_decisions", ["masking", "mask"]),
        ("insulation_labor_loading_row_95", "labor_loading", "95", "insulation_logistics_expense_template_decisions", ["loading", "setup", "set up"]),
        ("insulation_labor_traveling_row_97", "labor_traveling", "97", "insulation_logistics_expense_template_decisions", ["travel"]),
        ("insulation_infrared_scan_row_99", "infrared_scan", "99", "insulation_logistics_expense_template_decisions", ["infrared", "ir scan", "thermal scan"]),
        ("insulation_meals_lodging_row_100", "meals_lodging", "100", "insulation_logistics_expense_template_decisions", ["meals", "lodging", "hotel"]),
    ):
        if any(term in text for term in terms):
            proposals.append(_proposal(template_type, section, decision_id, bucket, row, include=True, confidence=0.7, note=_snippet(notes, terms)))
    return proposals


def _proposal(
    template_type: str,
    section: str,
    decision_id: str,
    bucket: str,
    row: str,
    *,
    include: bool,
    values: dict[str, Any] | None = None,
    confidence: float,
    review_reasons: list[str] | None = None,
    note: str = "",
    source: str = "deterministic_rule",
) -> DecisionProposal:
    reasons = list(review_reasons or [])
    return DecisionProposal(
        decision_id=decision_id,
        template_type=template_type,
        template_bucket=bucket,
        workbook_row=row,
        include=include,
        proposed_values=values or {},
        confidence=confidence,
        review_required=bool(reasons),
        review_reasons=reasons,
        evidence={"note": [{"text": note}]} if note else {},
        source=source,
        section=section,
    )


def _photo_scope_proposals(template_type: str, scope: dict[str, Any]) -> list[DecisionProposal]:
    raw = scope.get("photo_decision_proposals") or []
    proposals: list[DecisionProposal] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        proposal_template = str(item.get("template_type") or template_type or "").strip().lower()
        if proposal_template and proposal_template != template_type:
            continue
        proposals.append(
            DecisionProposal(
                decision_id=str(item.get("decision_id") or ""),
                template_type=template_type,
                template_bucket=str(item.get("template_bucket") or ""),
                workbook_row=str(item.get("workbook_row") or ""),
                include=item.get("include") if item.get("include") is not None else True,
                proposed_values=dict(item.get("proposed_values") or {}),
                confidence=float(item.get("confidence") or 0.0),
                review_required=True if item.get("review_required") is None else bool(item.get("review_required")),
                review_reasons=list(item.get("review_reasons") or ["Photo evidence requires estimator confirmation."]),
                evidence=item.get("evidence") if isinstance(item.get("evidence"), dict) else {},
                source="photo_evidence",
                section=str(item.get("section") or ""),
            )
        )
    return proposals


def _ai_scope_proposals(template_type: str, ai_debug: dict[str, Any]) -> list[DecisionProposal]:
    parsed = ai_debug.get("ai_parsed_scope") if isinstance(ai_debug, dict) else {}
    if not isinstance(parsed, dict):
        return []
    packages = parsed.get("scope_packages") if isinstance(parsed.get("scope_packages"), dict) else {}
    proposals: list[DecisionProposal] = []
    if template_type == "roofing" and packages.get("coating"):
        for row in ("26",):
            proposals.append(
                _proposal(
                    "roofing",
                    "roofing_coating_template_decisions",
                    f"roofing_coating_system_row_{row}",
                    "coating",
                    row,
                    include=True,
                    confidence=0.45,
                    review_reasons=["AI-only coating package proposal requires estimator review."],
                    note="AI scope package: coating",
                    source="ai_scope",
                )
            )
    return proposals


def _ai_scope_debug(recommendation: Any) -> dict[str, Any]:
    debug = getattr(recommendation, "debug", {}) if recommendation is not None else {}
    if isinstance(recommendation, dict):
        debug = recommendation.get("debug", {})
    return (debug or {}).get("ai_scope_interpreter") or {}


def _is_insulation_scope(scope: dict[str, Any]) -> bool:
    template_type = _norm(scope.get("template_type"))
    estimate_mode = _norm(scope.get("estimate_mode"))
    division = _norm(scope.get("division"))
    project_type = _norm(scope.get("project_type"))
    if template_type == "insulation" or estimate_mode == "insulation":
        return True
    if template_type in {"roofing", "repair", "flooring"} or estimate_mode in {"roofing", "roof restoration", "roof coating", "restoration"}:
        return False
    if division == "insulation":
        return True
    if division == "roofing" or "roof coating" in project_type or "roof restoration" in project_type:
        return False
    text = " ".join(_norm(scope.get(key)) for key in ("division", "template_type", "project_type", "estimate_mode", "building_type"))
    return "insulation" in text or "spray foam" in text


def _note_text(scope: dict[str, Any]) -> str:
    return str(scope.get("notes") or scope.get("raw_input_notes") or scope.get("scope_of_work") or "")


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("_", " ").replace("-", " ").split())


def _first_positive(scope: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        try:
            value = float(scope.get(key))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def _snippet(notes: str, terms: Iterable[str]) -> str:
    if not notes:
        return ""
    lowered = notes.lower()
    for term in terms:
        index = lowered.find(str(term).lower())
        if index >= 0:
            start = max(0, index - 80)
            end = min(len(notes), index + 160)
            return notes[start:end].strip()
    return notes[:220].strip()
