from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Iterable

from .chat_assistant import CHAT_DECISION_MENU, FORMULA_INPUT_ALIASES


HARD_COMPATIBILITY_STATUSES = {
    "blocked",
    "error",
    "incompatible",
    "not_compatible",
}
HIGH_IMPACT_ASSUMPTIONS = {
    "critical",
    "high",
    "material",
}
TEXT_DECISION_FIELDS = {
    "foam_type",
    "formula_mode",
    "period",
    "selected_item_name",
    "selector_code",
}


def decision_edit_schema(
    state: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    """Return field metadata for editing one estimator decision."""

    menu_row = _decision_menu_row(state, decision)
    configured_fields = (
        menu_row.get("editable_fields")
        if isinstance(menu_row, dict)
        else None
    )
    if not isinstance(configured_fields, list) or not configured_fields:
        configured_fields = [
            "include",
            *(
                str(field)
                for field in (decision.get("proposed_values") or {})
                if str(field).strip()
            ),
        ]
    fields: list[dict[str, Any]] = []
    for raw_field in configured_fields:
        field = str(raw_field or "").strip()
        if not field or field == "include":
            continue
        fields.append(
            {
                "field": field,
                "label": field.replace("_", " ").title(),
                "input_type": "text" if field in TEXT_DECISION_FIELDS else "number",
            }
        )
    return {
        "decision_id": str(
            decision.get("decision_id")
            or decision.get("template_bucket")
            or ""
        ),
        "fields": fields,
        "formula_requirements": _formula_requirements(state, decision),
    }


def evaluate_estimate_readiness(state: dict[str, Any]) -> dict[str, Any]:
    """Validate estimator-controlled inputs before workbook approval.

    The workbook remains the calculation engine. These checks only determine
    whether the decision inputs are complete and internally coherent enough to
    hand to that engine.
    """

    hard_errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    decisions = [
        dict(row)
        for row in state.get("decision_template_state") or []
        if isinstance(row, dict)
    ]
    included = [row for row in decisions if row.get("include") is True]

    if not decisions:
        hard_errors.append(
            _issue(
                "no_decisions",
                "No estimator decisions are available for workbook generation.",
            )
        )

    for question in state.get("unresolved_questions") or []:
        hard_errors.append(
            _issue(
                "unresolved_question",
                str(question),
            )
        )

    for decision in included:
        hard_errors.extend(_decision_input_errors(state, decision))
        warnings.extend(_decision_warnings(decision))

    hard_errors.extend(_duplicate_selection_errors(included))
    hard_errors.extend(_scope_geometry_errors(state.get("scope_state") or {}))
    warnings.extend(_assumption_issues(state.get("assumptions") or [], hard_errors))

    for flag in state.get("review_flags") or []:
        warnings.append(_issue("review_flag", str(flag)))

    review = state.get("review_state") if isinstance(state.get("review_state"), dict) else {}
    if review.get("status") == "stale":
        warnings.append(
            _issue(
                "stale_review",
                "The independent review is stale because the estimate changed.",
            )
        )
    elif review.get("verdict") == "needs_changes" and not review.get("applied"):
        warnings.append(
            _issue(
                "unapplied_review_changes",
                "The independent review recommends changes that have not been applied.",
            )
        )

    hard_errors = _dedupe_issues(hard_errors)
    warnings = _dedupe_issues(warnings)
    return {
        "report_version": 1,
        "evaluated_at": datetime.now(UTC).isoformat(),
        "ready": not hard_errors,
        "hard_errors": hard_errors,
        "warnings": warnings,
        "counts": {
            "decision_count": len(decisions),
            "included_decision_count": len(included),
            "hard_error_count": len(hard_errors),
            "warning_count": len(warnings),
            "confirmed_assumption_count": sum(
                1
                for row in state.get("assumptions") or []
                if isinstance(row, dict) and row.get("confirmed") is True
            ),
            "unconfirmed_assumption_count": sum(
                1
                for row in state.get("assumptions") or []
                if isinstance(row, dict) and row.get("confirmed") is not True
            ),
        },
        "checks": {
            "has_decisions": bool(decisions),
            "has_included_decisions": bool(included),
            "questions_resolved": not bool(state.get("unresolved_questions")),
            "formula_inputs_complete": not any(
                row.get("code") == "missing_formula_input" for row in hard_errors
            ),
            "geometry_valid": not any(
                str(row.get("code") or "").startswith("geometry_")
                for row in hard_errors
            ),
            "compatibility_valid": not any(
                row.get("code") == "incompatible_selection" for row in hard_errors
            ),
            "workbook_rows_unambiguous": not any(
                row.get("code") == "duplicate_workbook_selection"
                for row in hard_errors
            ),
        },
    }


def _decision_input_errors(
    state: dict[str, Any],
    decision: dict[str, Any],
) -> list[dict[str, Any]]:
    decision_id = str(
        decision.get("decision_id")
        or decision.get("template_bucket")
        or decision.get("workbook_row")
        or "unknown"
    )
    values = _decision_values(decision)
    errors: list[dict[str, Any]] = []
    requirements = _formula_requirements(state, decision)
    for group in _requirement_groups(requirements):
        if any(
            all(_field_present(values, field) for field in alternative)
            for alternative in group
        ):
            continue
        label = " or ".join(" and ".join(fields) for fields in group)
        errors.append(
            _issue(
                "missing_formula_input",
                f"{decision_id} requires {label}.",
                decision_id=decision_id,
                field=label,
            )
        )

    for field, value in values.items():
        if not _numeric_input_field(field):
            continue
        number = _number(value)
        if number is not None and number < 0:
            errors.append(
                _issue(
                    "invalid_negative_input",
                    f"{decision_id} has a negative {field} value.",
                    decision_id=decision_id,
                    field=field,
                )
            )

    compatibility = str(decision.get("compatibility_status") or "").strip().lower()
    if compatibility in HARD_COMPATIBILITY_STATUSES:
        errors.append(
            _issue(
                "incompatible_selection",
                f"{decision_id} is marked {compatibility.replace('_', ' ')}.",
                decision_id=decision_id,
            )
        )
    return errors


def _decision_warnings(decision: dict[str, Any]) -> list[dict[str, Any]]:
    decision_id = str(
        decision.get("decision_id")
        or decision.get("template_bucket")
        or decision.get("workbook_row")
        or "unknown"
    )
    rows: list[dict[str, Any]] = []
    if decision.get("review_required") is True:
        rows.append(
            _issue(
                "decision_review_required",
                f"{decision_id} is still marked for estimator review.",
                decision_id=decision_id,
            )
        )
    if not (decision.get("source_ids") or decision.get("evidence")):
        source_type = str(decision.get("source_type") or decision.get("source") or "")
        if source_type not in {"deterministic_calculation", "explicit_user_note", "explicit_estimator_edit"}:
            rows.append(
                _issue(
                    "decision_without_evidence",
                    f"{decision_id} does not have traceable supporting evidence.",
                    decision_id=decision_id,
                )
            )
    for warning in decision.get("compatibility_warnings") or []:
        rows.append(
            _issue(
                "compatibility_warning",
                f"{decision_id}: {warning}",
                decision_id=decision_id,
            )
        )
    return rows


def _formula_requirements(
    state: dict[str, Any],
    decision: dict[str, Any],
) -> list[str]:
    supplied = decision.get("formula_requirements")
    if isinstance(supplied, list) and supplied:
        return [str(value) for value in supplied if str(value).strip()]
    row = _decision_menu_row(state, decision)
    return [
        str(value)
        for value in row.get("formula_requirements") or []
        if str(value).strip()
    ]


def _decision_menu_row(
    state: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    template_type = str(
        state.get("template_type")
        or (state.get("scope_state") or {}).get("template_type")
        or ""
    ).strip().lower()
    if "roof" in template_type:
        template_type = "roofing"
    elif "insulat" in template_type or "foam" in template_type:
        template_type = "insulation"
    menu = CHAT_DECISION_MENU.get(template_type, [])
    decision_id = str(decision.get("decision_id") or "")
    workbook_row = str(decision.get("workbook_row") or decision.get("row_number") or "")
    bucket = str(decision.get("template_bucket") or "").strip().lower()
    for row in menu:
        if (
            (decision_id and decision_id == str(row.get("decision_id") or ""))
            or (workbook_row and workbook_row == str(row.get("workbook_row") or ""))
            or (bucket and bucket == str(row.get("template_bucket") or "").strip().lower())
        ):
            return dict(row)
    return {}


def _requirement_groups(requirements: Iterable[str]) -> list[list[tuple[str, ...]]]:
    groups: list[list[tuple[str, ...]]] = []
    for raw in requirements:
        text = " ".join(str(raw or "").strip().lower().split())
        if not text or text.startswith("optional "):
            continue
        extends_previous = text.startswith("or ")
        if extends_previous:
            text = text[3:].strip()
        alternatives: list[tuple[str, ...]] = []
        for option in text.split(" or "):
            fields = tuple(
                match
                for part in option.split(" and ")
                if (
                    match := _longest_requirement_field(part)
                )
            )
            if fields:
                alternatives.append(fields)
        if not alternatives:
            continue
        if extends_previous and groups:
            groups[-1].extend(alternatives)
        else:
            groups.append(alternatives)
    return groups


def _longest_requirement_field(text: str) -> str:
    normalized = " " + text.replace("_", " ").replace("-", " ") + " "
    matches = [
        field
        for field in FORMULA_INPUT_ALIASES
        if " " + field.replace("_", " ") + " " in normalized
    ]
    return max(matches, key=len) if matches else ""


def _field_present(values: dict[str, Any], field: str) -> bool:
    aliases = FORMULA_INPUT_ALIASES.get(field, (field,))
    for alias in aliases:
        value = values.get(alias)
        if value in (None, "", [], {}):
            continue
        number = _number(value)
        if number is not None:
            return number > 0
        return True
    return False


def _decision_values(decision: dict[str, Any]) -> dict[str, Any]:
    values = {
        key: value
        for key, value in decision.items()
        if key not in {"proposed_values", "calculated_outputs"}
    }
    if isinstance(decision.get("proposed_values"), dict):
        values.update(decision["proposed_values"])
    if isinstance(decision.get("calculated_outputs"), dict):
        for key, value in decision["calculated_outputs"].items():
            values.setdefault(key, value)
    return values


def _duplicate_selection_errors(
    decisions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[str]] = {}
    for decision in decisions:
        workbook_row = str(decision.get("workbook_row") or decision.get("row_number") or "").strip()
        exclusive_group = str(
            decision.get("exclusive_group")
            or decision.get("mutually_exclusive_group")
            or ""
        ).strip()
        key = (
            "exclusive_group" if exclusive_group else "workbook_row",
            exclusive_group or workbook_row,
        )
        if not key[1]:
            continue
        groups.setdefault(key, []).append(
            str(decision.get("decision_id") or decision.get("template_bucket") or key[1])
        )
    rows = []
    for (group_type, value), decision_ids in groups.items():
        if len(decision_ids) < 2:
            continue
        rows.append(
            _issue(
                "duplicate_workbook_selection",
                f"Multiple included decisions target the same {group_type.replace('_', ' ')} {value}: "
                + ", ".join(decision_ids),
                decision_ids=decision_ids,
                field=value,
            )
        )
    return rows


def _scope_geometry_errors(scope: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    gross_wall = _number(scope.get("gross_wall_area_sqft"))
    openings = _number(
        scope.get("opening_area_known_sqft")
        or scope.get("deduction_area_sqft")
    )
    net_wall = _number(scope.get("net_wall_area_sqft"))
    if gross_wall is not None and gross_wall < 0:
        rows.append(_issue("geometry_negative_area", "Gross wall area cannot be negative."))
    if openings is not None and openings < 0:
        rows.append(_issue("geometry_negative_deduction", "Opening deductions cannot be negative."))
    if gross_wall is not None and openings is not None and openings > gross_wall:
        rows.append(
            _issue(
                "geometry_deduction_exceeds_area",
                "Opening deductions exceed the gross wall area.",
            )
        )
    if gross_wall is not None and openings is not None and net_wall is not None:
        expected = gross_wall - openings
        if abs(net_wall - expected) > max(1.0, abs(expected) * 0.02):
            rows.append(
                _issue(
                    "geometry_net_area_mismatch",
                    "Net wall area does not equal gross wall area less opening deductions.",
                )
            )
    if scope.get("opening_area_missing") is True:
        rows.append(
            _issue(
                "geometry_opening_dimensions_missing",
                "At least one opening still needs dimensions before workbook approval.",
            )
        )
    return rows


def _assumption_issues(
    assumptions: Iterable[Any],
    hard_errors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for index, raw in enumerate(assumptions):
        if not isinstance(raw, dict) or raw.get("confirmed") is True:
            continue
        assumption_id = str(raw.get("assumption_id") or f"assumption-{index + 1}")
        message = str(raw.get("assumption") or raw.get("text") or assumption_id)
        impact = str(raw.get("financial_impact") or raw.get("impact") or "").strip().lower()
        issue = _issue(
            "unconfirmed_assumption",
            message,
            assumption_id=assumption_id,
        )
        if str(raw.get("confirmation_status") or "").lower() == "rejected":
            hard_errors.append(
                {
                    **issue,
                    "code": "rejected_assumption_requires_correction",
                    "message": (
                        f"Rejected assumption requires a scope or decision correction: {message}"
                    ),
                }
            )
        elif impact in HIGH_IMPACT_ASSUMPTIONS:
            hard_errors.append(
                {
                    **issue,
                    "code": "unconfirmed_high_impact_assumption",
                }
            )
        else:
            warnings.append(issue)
    return warnings


def _numeric_input_field(field: str) -> bool:
    return field in FORMULA_INPUT_ALIASES or field in {
        alias
        for aliases in FORMULA_INPUT_ALIASES.values()
        for alias in aliases
    }


def _issue(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        **{
            key: value
            for key, value in details.items()
            if value not in (None, "", [], {})
        },
    }


def _dedupe_issues(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = (
            str(row.get("code") or ""),
            str(row.get("decision_id") or ""),
            str(row.get("message") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None
