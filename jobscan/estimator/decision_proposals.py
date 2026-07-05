from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

import pandas as pd


SOURCE_PRECEDENCE = {
    "ai_scope": 10,
    "product_guidance": 20,
    "historical_default": 30,
    "historical_companion": 35,
    "deterministic_rule": 40,
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
    "roofing_accessory_template_decisions",
    "insulation_foam_template_decisions",
    "insulation_detail_material_template_decisions",
    "insulation_thermal_barrier_template_decisions",
    "insulation_support_material_template_decisions",
    "insulation_equipment_logistics_template_decisions",
)


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
    if template_type == "insulation":
        proposals.extend(_insulation_scope_proposals(scope, notes))
    else:
        proposals.extend(_roofing_scope_proposals(scope, notes))
    proposals.extend(_ai_scope_proposals(template_type, _ai_scope_debug(recommendation)))
    return merge_decision_proposals(proposals)


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
            proposal = proposal_by_key.get(key)
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
            if value is not None and _proposal_value_can_fill(updated.get(key)):
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


def _canonical_package(value: Any) -> str:
    text = _norm(value).replace(" ", "_")
    return PACKAGE_COMPANION_ALIASES.get(text, text)


def _first_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
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
        include=True,
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
    historical = _historical_evidence_summary(row)
    pricing = _pricing_evidence_summary(row)
    product = _product_evidence_summary(row)
    formula = _formula_evidence_summary(row)

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
        "historical_evidence_summary": historical,
        "pricing_evidence_summary": pricing,
        "product_evidence_summary": product,
        "formula_evidence_summary": formula,
    }


def _has_note_evidence(row: dict[str, Any]) -> bool:
    evidence = row.get("proposal_evidence") or {}
    return bool(evidence.get("note"))


def _why_included_summary(row: dict[str, Any]) -> str:
    if not row.get("include"):
        return ""
    source = str(row.get("proposal_source") or "").strip()
    if source:
        label = source.replace("_", " ")
        reasons = [str(item) for item in row.get("proposal_review_reasons") or [] if item]
        return f"Included by {label}" + (f"; review: {'; '.join(reasons[:2])}" if reasons else "")
    if row.get("decision_evidence_count") or row.get("historical_selector_evidence_count"):
        return "Included from historical default/workbench rule"
    return "Included by workbench rule or estimator edit"


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


def _pricing_evidence_summary(row: dict[str, Any]) -> str:
    candidate = str(row.get("selected_pricing_candidate") or row.get("item_name") or "").strip()
    unit_price = _safe_number(row.get("unit_price") or row.get("current_unit_price") or row.get("current_price"), 0)
    source = ""
    for item in _pricing_candidates(row):
        if candidate and _norm(item.get("item_name")) == _norm(candidate):
            source = str(item.get("source") or item.get("why_suggested") or "").strip()
            break
    if not candidate and unit_price <= 0:
        return ""
    parts = []
    if candidate:
        parts.append(candidate)
    if unit_price > 0:
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
    try:
        parsed = json.loads(row.get("pricing_candidates_json") or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = []
    return [dict(item) for item in parsed if isinstance(item, dict)]


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
        for row in ("26", "27"):
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
        proposals.append(_proposal(template_type, "roofing_primer_template_decisions", "roofing_primer_system_row_39", "primer", "39", include=True, confidence=0.75, note=_snippet(notes, ["primer", "rust", "fastener"])))
    if any(term in text or term in flag_blob for term in ("caulk", "sealant", "penetration", "detail")):
        proposals.append(_proposal(template_type, "roofing_detail_template_decisions", "roofing_caulk_sealant_row_43", "caulk_sealant", "43", include=True, confidence=0.75, note=_snippet(notes, ["caulk", "sealant", "penetration", "detail"])))
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
        proposals.append(_proposal(template_type, "roofing_equipment_template_decisions", "roofing_lift_equipment_row_73", "lift", "73", include=True, confidence=0.65, review_reasons=["Access equipment type/period requires estimator confirmation."], note=_snippet(notes, ["lift", "access", "equipment"])))
    for decision_id, bucket, row, terms in (
        ("roofing_truck_expense_row_108", "truck_expense", "108", ["truck", "travel"]),
        ("roofing_labor_loading_row_137", "labor_loading", "137", ["loading", "setup", "set up"]),
        ("roofing_labor_traveling_row_139", "labor_traveling", "139", ["travel"]),
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
            "19",
            include=True,
            values={"basis_sqft": _first_positive(scope, "estimated_sqft", "net_insulation_area_sqft", "gross_insulation_area_sqft")},
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
        ("insulation_truck_expense_row_70", "truck_expense", "70", "insulation_equipment_logistics_template_decisions", ["truck", "travel"]),
        ("insulation_labor_mask_row_80", "labor_mask", "80", "insulation_labor_template_decisions", ["masking", "mask"]),
        ("insulation_labor_loading_row_95", "labor_loading", "95", "insulation_labor_template_decisions", ["loading", "setup", "set up"]),
        ("insulation_labor_traveling_row_97", "labor_traveling", "97", "insulation_labor_template_decisions", ["travel"]),
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


def _ai_scope_proposals(template_type: str, ai_debug: dict[str, Any]) -> list[DecisionProposal]:
    parsed = ai_debug.get("ai_parsed_scope") if isinstance(ai_debug, dict) else {}
    if not isinstance(parsed, dict):
        return []
    packages = parsed.get("scope_packages") if isinstance(parsed.get("scope_packages"), dict) else {}
    proposals: list[DecisionProposal] = []
    if template_type == "roofing" and packages.get("coating"):
        for row in ("26", "27"):
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
