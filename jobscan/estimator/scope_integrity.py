from __future__ import annotations

from typing import Any

from .decision_proposals import canonicalize_structured_roofing_scope


def evaluate_roofing_scope_integrity(scope: dict[str, Any] | None) -> dict[str, Any]:
    """Reconcile structured roofing areas before retrieval or workbook pricing."""

    source = dict(scope or {})
    template_type = str(
        source.get("template_type") or source.get("division") or ""
    ).strip().lower()
    area_scopes = [
        dict(row)
        for row in source.get("area_scopes") or []
        if isinstance(row, dict)
    ]
    if template_type and "roof" not in template_type:
        return {"status": "not_applicable", "blocking_issues": [], "warnings": []}
    if not area_scopes:
        return {"status": "not_evaluated", "blocking_issues": [], "warnings": []}

    canonical = canonicalize_structured_roofing_scope(source)
    blocking: list[str] = []
    warnings: list[str] = []
    scope_ids = {
        str(row.get("scope_id") or "").strip()
        for row in area_scopes
        if str(row.get("scope_id") or "").strip()
    }
    if len(scope_ids) != len(
        [row for row in area_scopes if str(row.get("scope_id") or "").strip()]
    ):
        blocking.append("Structured roofing scope contains duplicate scope_id values.")
    for row in area_scopes:
        role = str(row.get("scope_role") or "").strip().lower()
        parent_id = str(row.get("parent_scope_id") or "").strip()
        if role == "nested_sub_scope" and not parent_id:
            blocking.append(
                f"Nested area {row.get('scope_id') or row.get('label') or 'unnamed'} requires parent_scope_id."
            )
        if parent_id and parent_id not in scope_ids:
            blocking.append(
                f"Nested area {row.get('scope_id') or row.get('label') or 'unnamed'} references missing parent {parent_id}."
            )

    rows_by_id = {
        str(row.get("scope_id") or "").strip(): row
        for row in area_scopes
        if str(row.get("scope_id") or "").strip()
    }
    for row in area_scopes:
        parent_id = str(row.get("parent_scope_id") or "").strip()
        if not parent_id or parent_id not in rows_by_id:
            continue
        text = " ".join(
            str(row.get(key) or "").lower()
            for key in ("label", "action", "proposed_assembly", "evidence_text")
        )
        if not any(
            token in text
            for token in (
                "deteriorated decking",
                "replace decking",
                "deck replacement",
            )
        ):
            continue
        deck_area = _number(
            row.get("decking_replacement_sqft") or row.get("area_sqft")
        )
        parent_area = _number(rows_by_id[parent_id].get("area_sqft"))
        if deck_area > parent_area + 1.0:
            blocking.append(
                f"Nested deck-replacement area {deck_area:g} sq ft cannot "
                f"exceed parent tear-off area {parent_area:g} sq ft."
            )

    total = _number(canonical.get("canonical_area_total_sqft"))
    exclusive = _number(canonical.get("canonical_exclusive_area_sqft"))
    nested = _number(canonical.get("canonical_nested_area_sqft"))
    if total <= 0 or exclusive <= 0:
        blocking.append("Structured roofing scope requires positive exclusive roof area.")
    if total > 0 and exclusive > 0 and abs(total - exclusive) > 1.0:
        blocking.append(
            f"Exclusive roof sections total {exclusive:g} sq ft but the canonical roof total is {total:g} sq ft."
        )
    for conflict in canonical.get("scope_conflicts") or []:
        message = str(conflict).strip()
        if not message:
            continue
        if message.startswith("Exclusive area scopes total"):
            blocking.append(message)
        else:
            warnings.append(message)

    bases = {
        "foam_basis_sqft": _number(canonical.get("foam_basis_sqft")),
        "coating_basis_sqft": _number(canonical.get("coating_basis_sqft")),
        "board_basis_sqft": _number(canonical.get("board_basis_sqft")),
        "decking_replacement_sqft": _number(
            canonical.get("decking_replacement_sqft")
        ),
    }
    for field in ("foam_basis_sqft", "coating_basis_sqft", "board_basis_sqft"):
        if bases[field] > total + 1.0:
            blocking.append(
                f"{field} cannot exceed the canonical roof area without a separate exclusive area."
            )
    if (
        bases["decking_replacement_sqft"] > 0
        and bases["board_basis_sqft"] > 0
        and bases["decking_replacement_sqft"] > bases["board_basis_sqft"] + 1.0
    ):
        blocking.append(
            "Deck-replacement area cannot exceed the tear-off/board area."
        )

    area_audit = list(canonical.get("canonical_area_audit") or [])
    exclusive_text = " ".join(
        str(row.get("scope_text") or "").lower()
        for row in area_audit
        if isinstance(row, dict) and row.get("included_in_total")
    )
    result = {
        "status": "blocked" if blocking else "valid_with_warnings" if warnings else "valid",
        "blocking_issues": _unique(blocking),
        "warnings": _unique(warnings),
        "canonical_area_total_sqft": total,
        "exclusive_area_sqft": exclusive,
        "nested_area_sqft": nested,
        **bases,
        "requires_tearoff": any(
            token in exclusive_text
            for token in ("full removal", "tear off", "tear-off", "down to wood decking")
        ),
        "area_audit": area_audit,
    }
    return result


def _number(value: Any) -> float:
    try:
        return round(float(value or 0), 3)
    except (TypeError, ValueError):
        return 0.0


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
