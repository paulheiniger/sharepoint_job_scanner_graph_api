from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable


SOURCE_PRECEDENCE = {
    "ai_scope": 10,
    "product_guidance": 20,
    "historical_default": 30,
    "deterministic_rule": 40,
    "explicit_note": 50,
    "estimator_edit": 60,
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
    updated["decision_evidence_summary"] = _decision_evidence_summary(updated)
    return updated


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
    merged["decision_evidence_summary"] = _decision_evidence_summary(merged)
    return merged


def _decision_evidence_summary(row: dict[str, Any]) -> str:
    parts: list[str] = []
    evidence = row.get("proposal_evidence") or {}
    if evidence.get("note"):
        parts.append("note evidence")
    if evidence.get("historical") or row.get("decision_evidence_count"):
        count = row.get("decision_evidence_count") or row.get("historical_selector_evidence_count")
        parts.append(f"historical evidence{f' ({count})' if count else ''}")
    if evidence.get("product_guidance") or row.get("product_id") or row.get("product_guidance"):
        parts.append("product guidance")
    if row.get("formula_model"):
        parts.append("formula preview")
    return ", ".join(dict.fromkeys(parts))


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
        proposals.append(_proposal(template_type, "roofing_primer_template_decisions", "roofing_primer_row_39", "primer", "39", include=True, confidence=0.75, note=_snippet(notes, ["primer", "rust", "fastener"])))
    if any(term in text or term in flag_blob for term in ("caulk", "sealant", "penetration", "detail")):
        proposals.append(_proposal(template_type, "roofing_detail_template_decisions", "roofing_caulk_sealant_row_43", "caulk_sealant", "43", include=True, confidence=0.75, note=_snippet(notes, ["caulk", "sealant", "penetration", "detail"])))
        proposals.append(_proposal(template_type, "roofing_detail_quantity_template_decisions", "roofing_penetrations_row_49", "penetrations", "49", include=True, confidence=0.7, review_reasons=["Detail quantity requires estimator count if units were not stated."], note=_snippet(notes, ["penetration", "detail"])))
    if any(term in text or term in flag_blob for term in ("seam", "seams")):
        proposals.append(_proposal(template_type, "roofing_detail_quantity_template_decisions", "roofing_seams_misc_row_47", "seams_misc", "47", include=True, confidence=0.7, review_reasons=["Seam quantity requires estimator linear footage if not stated."], note=_snippet(notes, ["seam", "seams"])))
        proposals.append(_proposal(template_type, "roofing_labor_template_decisions", "roofing_labor_seam_sealer_row_120", "labor_seam_sealer", "120", include=True, confidence=0.7, note=_snippet(notes, ["seam", "seams"])))
    if "fabric" in text or "reinforcement" in text:
        proposals.append(_proposal(template_type, "roofing_detail_template_decisions", "roofing_fabric_row_79", "fabric", "79", include=True, confidence=0.7, review_reasons=["Fabric/reinforcement extent requires estimator review."], note=_snippet(notes, ["fabric", "reinforcement"])))
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
