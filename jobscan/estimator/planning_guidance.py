from __future__ import annotations

import math
from typing import Any, Iterable

from .decision_proposals import (
    canonicalize_structured_roofing_scope,
    compile_deterministic_scope_proposals,
)


AREA_PURCHASE_RULES: tuple[tuple[str, str, float, str], ...] = (
    ("roofing_foam", "foam_basis_sqft", 250.0, "SPF production-area increment"),
    ("coating", "coating_basis_sqft", 100.0, "coating production-area increment"),
    ("board_stock", "board_basis_sqft", 32.0, "one 4x8 board sheet"),
)


def build_estimator_planning_guidance(
    *,
    scope: dict[str, Any],
    data: Any = None,
    historical_material_usage: Iterable[Any] = (),
    historical_labor_performance: Iterable[Any] = (),
) -> dict[str, list[dict[str, Any]]]:
    """Build reviewable purchasing and labor candidates from existing evidence."""

    canonical = canonicalize_structured_roofing_scope(scope)
    if not canonical.get("canonical_area_audit"):
        return {"purchasing_guidance": [], "labor_plan_guidance": []}
    purchasing = _purchasing_guidance(
        canonical,
        historical_material_usage=historical_material_usage,
    )
    labor = _labor_guidance(
        canonical,
        data=data,
        historical_labor_performance=historical_labor_performance,
    )
    return {
        "purchasing_guidance": purchasing,
        "labor_plan_guidance": labor,
    }


def _purchasing_guidance(
    scope: dict[str, Any],
    *,
    historical_material_usage: Iterable[Any],
) -> list[dict[str, Any]]:
    observations = [row for row in historical_material_usage if isinstance(row, dict)]
    output: list[dict[str, Any]] = []
    for category, area_field, increment, increment_label in AREA_PURCHASE_RULES:
        measured = _number(scope.get(area_field))
        if measured <= 0:
            continue
        ratio = _historical_area_ratio(observations, category)
        recommended = _round_up(measured, increment)
        reasons = [f"Rounded up to the next {increment:g} sq. ft. {increment_label}."]
        if ratio:
            reasons.insert(
                0,
                f"Historical basis ratio {ratio['ratio']:.4f} supports review but was not compounded with package rounding.",
            )
        output.append(
            {
                "category": category,
                "measured_quantity": measured,
                "recommended_purchase_quantity": recommended,
                "unit": "sqft",
                "package_increment": increment,
                "adjustment_quantity": round(recommended - measured, 3),
                "adjustment_pct": round((recommended / measured - 1) * 100, 3),
                "method": "package_rounding_with_historical_support"
                if ratio
                else "reviewable_package_rounding_default",
                "quantity_adjustment_reason": " ".join(reasons),
                "historical_support_count": ratio["support_count"] if ratio else 0,
                "historical_ratio": ratio["ratio"] if ratio else None,
                "confidence": "medium" if ratio else "review",
                "review_required": True,
                "formula_input_field": "basis_sqft",
            }
        )

    for row in (scope.get("canonical_linear_breakdown") or {}).get("edge_metal") or []:
        if not isinstance(row, dict):
            continue
        measured = _number(row.get("linear_ft"))
        if measured <= 0:
            continue
        recommended = _round_up(measured, 10.0)
        output.append(
            {
                "category": "edge_metal",
                "item": str(row.get("item") or "Edge metal"),
                "size": str(row.get("size") or ""),
                "measured_quantity": measured,
                "recommended_purchase_quantity": recommended,
                "unit": "linear_ft",
                "package_increment": 10.0,
                "adjustment_quantity": round(recommended - measured, 3),
                "adjustment_pct": round((recommended / measured - 1) * 100, 3),
                "method": "reviewable_stock_length_rounding_default",
                "quantity_adjustment_reason": "Rounded to a reviewable 10-foot stock-length increment.",
                "historical_support_count": 0,
                "historical_ratio": None,
                "confidence": "review",
                "review_required": True,
                "formula_input_field": "linear_ft",
                "evidence_text": str(row.get("evidence_text") or ""),
            }
        )
    return output


def _labor_guidance(
    scope: dict[str, Any],
    *,
    data: Any,
    historical_labor_performance: Iterable[Any],
) -> list[dict[str, Any]]:
    proposals = compile_deterministic_scope_proposals(scope, data=data)
    output: list[dict[str, Any]] = []
    for proposal in proposals:
        category = str(proposal.template_bucket or "")
        if not category.startswith("labor_"):
            continue
        values = dict(proposal.proposed_values or {})
        total_hours = _number(
            values.get("total_hours") or values.get("editable_total_hours")
        )
        crew_size = _number(values.get("crew_size"))
        days = _number(values.get("days"))
        if total_hours <= 0 and days <= 0:
            continue
        basis = _labor_basis(scope, category)
        current_daily_rate = _number(values.get("daily_rate"))
        historical_daily_rate = _number(values.get("historical_daily_rate"))
        comparable = next(
            iter((proposal.evidence or {}).get("automatic_comparable") or []),
            {},
        )
        current_people = next(
            iter((proposal.evidence or {}).get("current_people_rate") or []),
            {},
        )
        output.append(
            {
                "category": category,
                "activity": _activity_label(category),
                "basis_quantity": basis,
                "basis_unit": "sqft",
                "recommended_total_hours": total_hours or None,
                "recommended_crew_size": int(crew_size) if crew_size > 0 else None,
                "recommended_days": days or None,
                "hours_per_1000_sqft": round(total_hours / basis * 1000, 3)
                if total_hours > 0 and basis > 0
                else None,
                "current_people_daily_rate": current_daily_rate or None,
                "historical_daily_rate": historical_daily_rate or None,
                "rate_source": "current_people_tab"
                if current_people
                else "historical_comparable_review_required",
                "estimated_labor_cost_candidate": round(days * current_daily_rate, 2)
                if days > 0 and current_daily_rate > 0
                else None,
                "source_job_id": comparable.get("job_id"),
                "source_job_name": comparable.get("job_name"),
                "source_file": comparable.get("source_file"),
                "scale_factor": comparable.get("scale_factor"),
                "confidence": round(float(proposal.confidence or 0), 3),
                "calibration_status": "calibrated_candidate",
                "blocking_input_required": False,
                "review_required": True,
                "review_reasons": list(proposal.review_reasons or []),
                "formula_authority": "workbook_people_rate_and_labor_formula",
            }
        )
    by_category = {row["category"]: row for row in output}
    historical = [
        row for row in historical_labor_performance if isinstance(row, dict)
    ]
    for category in _required_labor_categories(scope):
        if category in by_category:
            continue
        basis = _labor_basis(scope, category)
        historical_candidate = _historical_labor_candidate(
            historical,
            category=category,
            basis=basis,
        )
        current_people = _current_people_rate(
            data,
            category=category,
            crew_size=_number(historical_candidate.get("crew_size")),
        )
        row = {
            "category": category,
            "activity": _activity_label(category),
            "basis_quantity": basis,
            "basis_unit": "sqft",
            "recommended_total_hours": historical_candidate.get("total_hours"),
            "recommended_crew_size": historical_candidate.get("crew_size"),
            "recommended_days": historical_candidate.get("days"),
            "hours_per_1000_sqft": historical_candidate.get(
                "hours_per_1000_sqft"
            ),
            "current_people_daily_rate": current_people.get("daily_rate"),
            "historical_daily_rate": historical_candidate.get("daily_rate"),
            "rate_source": "current_people_tab"
            if current_people
            else "historical_observation_review_required"
            if historical_candidate
            else "unavailable",
            "estimated_labor_cost_candidate": round(
                _number(historical_candidate.get("days"))
                * _number(current_people.get("daily_rate")),
                2,
            )
            if historical_candidate and current_people
            else None,
            "source_job_id": historical_candidate.get("source_job_id"),
            "source_job_name": None,
            "source_file": historical_candidate.get("source_file"),
            "scale_factor": historical_candidate.get("scale_factor"),
            "confidence": historical_candidate.get("confidence", 0.0),
            "calibration_status": "historical_candidate"
            if historical_candidate
            else "missing_calibration",
            "blocking_input_required": not bool(historical_candidate),
            "review_required": True,
            "review_reasons": historical_candidate.get("review_reasons")
            or [
                "The current assembly requires this activity, but no usable historical productivity candidate was retrieved."
            ],
            "formula_authority": "workbook_people_rate_and_labor_formula",
        }
        output.append(row)
    return output


def _required_labor_categories(scope: dict[str, Any]) -> list[str]:
    required = ["labor_prep"]
    exclusive_text = " ".join(
        str(row.get("scope_text") or "").lower()
        for row in scope.get("canonical_area_audit") or []
        if isinstance(row, dict) and row.get("included_in_total")
    )
    if any(
        token in exclusive_text
        for token in ("full removal", "tear off", "tear-off", "down to wood decking")
    ):
        required.append("labor_tearoff")
    if _number(scope.get("board_basis_sqft")) > 0:
        required.append("labor_board")
    if _number(scope.get("coating_basis_sqft")) > 0:
        required.extend(("labor_base", "labor_top_coat"))
    required.append("labor_cleanup")
    return list(dict.fromkeys(required))


def _historical_labor_candidate(
    observations: list[dict[str, Any]],
    *,
    category: str,
    basis: float,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for observation in observations:
        if str(observation.get("category") or "") != category:
            continue
        total_hours = _number(observation.get("total_hours"))
        crew_size = _number(observation.get("crew_size"))
        days = _number(observation.get("days"))
        source = next(
            (
                row
                for row in observation.get("sources") or []
                if isinstance(row, dict)
            ),
            {},
        )
        reference_area = _number(source.get("reference_area_sqft"))
        productivity = observation.get("productivity") or {}
        rate = _number(productivity.get("rate"))
        rate_unit = str(productivity.get("rate_unit") or "").lower()
        hours_per_1000 = 0.0
        if rate > 0 and "1000" in rate_unit and "sq" in rate_unit:
            hours_per_1000 = rate
        elif total_hours > 0 and reference_area > 0:
            hours_per_1000 = total_hours / reference_area * 1000
        if hours_per_1000 <= 0 or basis <= 0:
            continue
        scaled_hours = basis / 1000 * hours_per_1000
        scale_factor = basis / reference_area if reference_area > 0 else None
        scaled_days = days * scale_factor if days > 0 and scale_factor else 0.0
        support = max(
            int(_number(observation.get("support_count"))),
            int(_number(productivity.get("evidence_count"))),
            1,
        )
        candidates.append(
            {
                "total_hours": round(scaled_hours, 1),
                "crew_size": int(crew_size) if crew_size > 0 else None,
                "days": round(scaled_days, 2) if scaled_days > 0 else None,
                "hours_per_1000_sqft": round(hours_per_1000, 3),
                "daily_rate": _number(observation.get("daily_rate")) or None,
                "source_job_id": source.get("job_id"),
                "source_file": source.get("file_name"),
                "scale_factor": round(scale_factor, 6) if scale_factor else None,
                "support_count": support,
                "confidence": round(float(observation.get("confidence") or 0), 3),
                "review_reasons": [
                    "Scaled from semantic historical labor productivity; select a current People-tab crew before workbook generation."
                ],
            }
        )
    if not candidates:
        return {}
    return max(candidates, key=lambda row: (row["support_count"], row["confidence"]))


def _current_people_rate(
    data: Any,
    *,
    category: str,
    crew_size: float,
) -> dict[str, Any]:
    frame = getattr(data, "template_labor_options", None) if data is not None else None
    if frame is None or not hasattr(frame, "empty") or frame.empty or crew_size <= 0:
        return {}
    candidates: list[dict[str, Any]] = []
    for row in frame.fillna("").to_dict(orient="records"):
        if _slug(row.get("template_type")) != "roofing":
            continue
        if _slug(row.get("source_type")) != "people_daily_rate_selector":
            continue
        if _slug(row.get("labor_package")) not in {"", _slug(category)}:
            continue
        if int(_number(row.get("lookup_key"))) != int(crew_size):
            continue
        values = row.get("source_values_json")
        if not isinstance(values, dict):
            continue
        daily_rate = _number(values.get("daily_rate") or values.get("rate"))
        if daily_rate <= 0:
            continue
        candidates.append(
            {
                "daily_rate": daily_rate,
                "crew_size": int(crew_size),
                "template_labor_option_id": row.get("template_labor_option_id"),
                "template_name": row.get("template_name"),
            }
        )
    return candidates[0] if len(candidates) == 1 else {}


def _historical_area_ratio(
    observations: list[dict[str, Any]],
    category: str,
) -> dict[str, Any] | None:
    aliases = {
        "roofing_foam": {"foam", "roofing_foam"},
        "coating": {"coating"},
        "board_stock": {"board_stock"},
    }.get(category, {category})
    weighted: list[tuple[float, int]] = []
    for observation in observations:
        if str(observation.get("category") or "") not in aliases:
            continue
        basis = _measurement(
            observation.get("basis_measurements") or [],
            ("basis_sqft", "area_sqft", "board_area_sqft"),
        )
        references = [
            _number(source.get("reference_area_sqft"))
            for source in observation.get("sources") or []
            if isinstance(source, dict)
        ]
        reference = next((value for value in references if value > 0), 0.0)
        support = max(
            int(_number(observation.get("support_count"))),
            len([value for value in references if value > 0]),
        )
        if basis <= 0 or reference <= 0 or support < 2:
            continue
        ratio = basis / reference
        if 1.0 <= ratio <= 1.25:
            weighted.append((ratio, support))
    if not weighted:
        return None
    total_support = sum(support for _, support in weighted)
    ratio = sum(value * support for value, support in weighted) / total_support
    return {"ratio": round(ratio, 6), "support_count": total_support}


def _measurement(values: Iterable[Any], names: tuple[str, ...]) -> float:
    for row in values:
        if not isinstance(row, dict) or str(row.get("name") or "") not in names:
            continue
        value = _number(row.get("value"))
        if value > 0:
            return value
    return 0.0


def _labor_basis(scope: dict[str, Any], category: str) -> float:
    if category in {"labor_tearoff", "labor_board"}:
        return _number(scope.get("board_basis_sqft"))
    if category in {"labor_base", "labor_top_coat"}:
        return _number(scope.get("coating_basis_sqft"))
    return _number(scope.get("canonical_area_total_sqft"))


def _activity_label(category: str) -> str:
    return category.removeprefix("labor_").replace("_", " ").title()


def _slug(value: Any) -> str:
    return "_".join(str(value or "").strip().lower().replace("-", " ").split())


def _round_up(value: float, increment: float) -> float:
    if value <= 0 or increment <= 0:
        return 0.0
    return round(math.ceil((value - 1e-9) / increment) * increment, 3)


def _number(value: Any) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0
