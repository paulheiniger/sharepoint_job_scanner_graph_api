from __future__ import annotations

import math
from dataclasses import asdict
from typing import Any

import pandas as pd

from .calibration import calibrate_from_history
from .data_loader import load_estimator_data
from .decision_tree import evaluate_decision_tree
from .field_notes import parse_field_notes, parsed_to_scope
from .line_items import summarize_similar_job_buckets
from .materials import coating_gallons, find_current_price, historical_unit_cost
from .rules import first_nonblank, to_float
from .schemas import EstimateRecommendation, EstimatorAssumptions, EstimatorData, FieldNotesInput
from .similarity import find_similar_jobs
from .travel import build_travel_plan


def is_finite_number(value: Any) -> bool:
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except Exception:
        pass
    number = to_float(value)
    if number is None:
        return False
    return math.isfinite(number)


def is_missing_number(value: Any) -> bool:
    return not is_finite_number(value)


def is_missing_or_bad_number(value: Any) -> bool:
    return not is_finite_number(value)


def safe_float(value: Any, default: float = 0.0) -> float:
    return to_float_or_default(value, default)


def safe_int(value: Any, default: int = 0) -> int:
    return to_int_or_default(value, default)


def to_int_or_default(value: Any, default: int) -> int:
    if not is_finite_number(value):
        return default
    number = to_float(value)
    return int(number) if number is not None else default


def to_float_or_default(value: Any, default: float) -> float:
    if not is_finite_number(value):
        return default
    number = to_float(value)
    return float(number) if number is not None else default


def optional_positive_float(value: Any) -> float | None:
    if not is_finite_number(value):
        return None
    number = to_float(value)
    if number is None:
        return None
    return number if number > 0 else None


def optional_positive_int(value: Any) -> int | None:
    number = optional_positive_float(value)
    return int(number) if number is not None else None


def warranty_wet_mils(warranty_target: Any, coating_type: str) -> float:
    target = to_float(warranty_target)
    if target and target >= 20:
        return 30.0
    if target and target >= 15:
        return 25.0
    if target and target >= 10:
        return 20.0
    return 24.0 if "silicone" in coating_type.lower() else 30.0 if "acrylic" in coating_type.lower() else 24.0


def template_rows_with_job_sqft(data: EstimatorData) -> pd.DataFrame:
    if data.template_rows.empty:
        return pd.DataFrame()
    rows = data.template_rows.copy()
    sqft_by_job: dict[str, float] = {}
    for frame in (data.jobs, data.estimates):
        if frame.empty or "job_id" not in frame.columns:
            continue
        for _, row in frame.iterrows():
            sqft = to_float(row.get("estimated_sqft")) or to_float(row.get("surface_area_sqft"))
            if sqft:
                sqft_by_job[str(row.get("job_id"))] = sqft
    if "job_id" in rows.columns:
        rows["historical_sqft"] = rows["job_id"].astype(str).map(sqft_by_job)
    return rows


def historical_template_calibration(data: EstimatorData, similar_jobs: pd.DataFrame) -> dict[str, Any]:
    template_rows = template_rows_with_job_sqft(data)
    if template_rows.empty:
        return {
            "source": "estimate_line_item_classifications" if not data.classified_line_items.empty else "none",
            "template_row_count": 0,
            "labor_by_bucket": [],
            "material_by_bucket": [],
            "median_labor_cost_per_sqft": None,
            "median_material_cost_per_sqft": None,
            "worksheet_price_examples": [],
        }
    similar_ids = set(similar_jobs.get("job_id", pd.Series(dtype=str)).dropna().astype(str))
    if similar_ids and "job_id" in template_rows.columns:
        relevant = template_rows[template_rows["job_id"].astype(str).isin(similar_ids)].copy()
        if relevant.empty:
            relevant = template_rows.copy()
    else:
        relevant = template_rows.copy()
    for column in ("estimated_cost", "total_hours", "days", "crew_size", "historical_sqft"):
        if column in relevant.columns:
            relevant[column] = pd.to_numeric(relevant[column], errors="coerce")
    labor_rows = relevant[relevant.get("line_item_kind", pd.Series(dtype=str)).astype(str).eq("labor")].copy()
    material_rows = relevant[relevant.get("line_item_kind", pd.Series(dtype=str)).astype(str).isin(["material", "equipment", "travel"])].copy()
    totals = relevant[relevant.get("template_bucket", pd.Series(dtype=str)).astype(str).eq("worksheet_price")].copy()
    if not labor_rows.empty:
        labor_rows["cost_per_sqft"] = labor_rows["estimated_cost"] / labor_rows["historical_sqft"]
    if not material_rows.empty:
        material_rows["cost_per_sqft"] = material_rows["estimated_cost"] / material_rows["historical_sqft"]
    labor_summary = (
        labor_rows.groupby("template_bucket", dropna=False, as_index=False)
        .agg(
            evidence_count=("template_bucket", "size"),
            median_days=("days", "median"),
            median_crew_size=("crew_size", "median"),
            median_total_hours=("total_hours", "median"),
            median_estimated_cost=("estimated_cost", "median"),
        )
        .to_dict(orient="records")
        if not labor_rows.empty
        else []
    )
    material_summary = (
        material_rows.groupby(["template_bucket", "line_item_kind"], dropna=False, as_index=False)
        .agg(evidence_count=("template_bucket", "size"), median_estimated_cost=("estimated_cost", "median"))
        .to_dict(orient="records")
        if not material_rows.empty
        else []
    )
    return {
        "source": "estimate_template_rows",
        "template_row_count": int(len(relevant)),
        "labor_by_bucket": labor_summary,
        "material_by_bucket": material_summary,
        "median_labor_cost_per_sqft": _median_positive(labor_rows.get("cost_per_sqft", pd.Series(dtype=float))),
        "median_material_cost_per_sqft": _median_positive(material_rows.get("cost_per_sqft", pd.Series(dtype=float))),
        "worksheet_price_examples": totals[["document_id", "job_id", "source_file", "estimated_cost"]].dropna(how="all").head(8).to_dict(orient="records") if not totals.empty else [],
    }


def _median_positive(values: pd.Series) -> float | None:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    numeric = numeric[numeric > 0]
    return float(numeric.median()) if not numeric.empty else None


def build_material_plan(
    scope: dict[str, Any],
    data: EstimatorData,
    calibration: dict[str, Any],
    decision: dict[str, Any],
    assumptions: EstimatorAssumptions,
) -> tuple[list[dict[str, Any]], float, float, list[str]]:
    area = to_float(scope.get("surface_area_sqft")) or 0.0
    coating_type = first_nonblank(scope.get("coating_type"))
    plan: list[dict[str, Any]] = []
    review_flags: list[str] = []
    low_total = 0.0
    high_total = 0.0
    if scope.get("coating_required") and area:
        wet_mils = warranty_wet_mils(scope.get("warranty_target"), coating_type)
        gallons = coating_gallons(area, wet_mils, assumptions.coating_waste_factor)
        price = find_current_price(data.pricing, [coating_type] if coating_type else ["coating"], "price_per_gallon")
        price_source = "current_pricing"
        needs_review = False
        unit_price = to_float(price.get("matched_price")) if price else None
        item_name = first_nonblank(price.get("product_name") if price else "", coating_type, "Roof coating")
        if unit_price is None:
            fallback_psf = to_float(calibration.get("median_material_cost_per_sqft"))
            historical_unit = historical_unit_cost(data.line_items if not data.line_items.empty else data.classified_line_items, [coating_type or "coating"], area)
            price_source = "historical_fallback"
            needs_review = True
            review_flags.append("Historical fallback pricing used for coating.")
            if historical_unit:
                unit_price = historical_unit
                cost_target = gallons * unit_price
            elif fallback_psf:
                cost_target = fallback_psf * area
            else:
                cost_target = 0.0
                review_flags.append("No coating price available.")
        else:
            cost_target = gallons * unit_price
        low = cost_target * 0.9
        high = cost_target * 1.15
        low_total += low
        high_total += high
        plan.append(
            {
                "item": item_name,
                "category": "coating",
                "quantity": round(gallons, 1),
                "unit": "gal",
                "selected_price_source": price_source,
                "price_source_type": price_source,
                "unit_price": unit_price,
                "estimated_cost": round(cost_target, 2),
                "cost_low": round(low, 2),
                "cost_high": round(high, 2),
                "needs_review": needs_review,
                "notes": f"{wet_mils:g} wet mils with {assumptions.coating_waste_factor:.0%} waste factor.",
            }
        )
    material_assumptions = decision.get("material_assumptions", {})
    for flag_name, item_name in [
        ("primer_allowance_recommended", "Primer allowance"),
        ("seam_treatment_recommended", "Seam treatment allowance"),
        ("fastener_treatment_recommended", "Fastener treatment allowance"),
    ]:
        if material_assumptions.get(flag_name):
            plan.append(
                {
                    "item": item_name,
                    "category": "allowance",
                    "quantity": None,
                    "unit": "",
                    "selected_price_source": "review_allowance",
                    "unit_price": None,
                    "estimated_cost": None,
                    "needs_review": True,
                    "notes": "Estimator should price this allowance from current scope details.",
                }
            )
    return plan, round(low_total, 2), round(high_total, 2), review_flags


def build_labor_plan(
    scope: dict[str, Any],
    calibration: dict[str, Any],
    decision: dict[str, Any],
    assumptions: EstimatorAssumptions,
) -> tuple[list[dict[str, Any]], float, float, int, int, int]:
    area = safe_float(scope.get("surface_area_sqft"), 0.0)
    multiplier = to_float_or_default(decision.get("labor_modifiers", {}).get("combined_labor_multiplier"), 1.0)
    crew_size = safe_int(decision.get("crew_assumptions", {}).get("recommended_crew_size"), 4)
    if crew_size <= 0:
        crew_size = 4
    rows = calibration.get("labor_by_bucket") or []
    plan: list[dict[str, Any]] = []
    incomplete_calibration = False
    skipped_rows: list[str] = []
    total_hours = 0.0
    total_cost = 0.0
    if rows:
        for row in rows:
            try:
                hours_missing = is_missing_or_bad_number(row.get("median_total_hours"))
                days_missing = is_missing_or_bad_number(row.get("median_days"))
                crew_missing = is_missing_or_bad_number(row.get("median_crew_size"))
                cost_missing = is_missing_or_bad_number(row.get("median_estimated_cost"))
                evidence_missing = is_missing_or_bad_number(row.get("evidence_count"))
                row_incomplete = any([hours_missing, days_missing, crew_missing, cost_missing, evidence_missing])
                incomplete_calibration = incomplete_calibration or row_incomplete
                days = 1.0 if days_missing else max(safe_float(row.get("median_days"), 1.0), 0.0)
                row_crew_size = safe_int(row.get("median_crew_size"), crew_size)
                if row_crew_size <= 0:
                    row_crew_size = 4
                hours = None if hours_missing else max(safe_float(row.get("median_total_hours"), 0.0), 0.0)
                cost_value = row.get("median_estimated_cost")
                if is_missing_or_bad_number(cost_value):
                    cost_value = row.get("median_cost")
                if is_missing_or_bad_number(cost_value):
                    cost_missing = True
                    row_incomplete = True
                    incomplete_calibration = True
                    cost = None
                else:
                    cost = max(safe_float(cost_value, 0.0), 0.0)
                adjusted_days = safe_float(days * multiplier, 1.0)
                adjusted_hours = safe_float(hours * multiplier, 0.0) if hours is not None else adjusted_days * row_crew_size * 10
                estimated_cost = safe_float(cost * multiplier, 0.0) if cost is not None else 0.0
                total_hours += adjusted_hours
                total_cost += estimated_cost
                plan.append(
                    {
                        "task": row.get("template_bucket") or "labor_calibration",
                        "base_days": round(days, 2),
                        "adjusted_days": round(adjusted_days, 2),
                        "crew_size": row_crew_size,
                        "total_hours": round(adjusted_hours, 1),
                        "estimated_cost": round(estimated_cost, 2),
                        "evidence_count": safe_int(row.get("evidence_count"), 0),
                        "needs_review": bool(row_incomplete),
                        "notes": (
                            "Historical labor calibration was incomplete for one or more tasks; defaults were used."
                            if row_incomplete
                            else "Calibrated from estimate_template_rows."
                        ),
                    }
                )
            except Exception as err:
                incomplete_calibration = True
                skipped_rows.append(f"Skipped malformed labor calibration row: {type(err).__name__}")
                continue
    if not plan:
        days = 1.0
        total_hours = 40.0
        total_cost = 0.0
        plan.append(
            {
                "task": "labor_allowance",
                "base_days": 1.0,
                "adjusted_days": 1.0,
                "crew_size": 4,
                "total_hours": 40,
                "estimated_cost": 0.0,
                "evidence_count": 0,
                "needs_review": True,
                "notes": "Historical labor calibration unavailable; estimator must price labor manually.",
            }
        )
        crew_size = 4
    low = total_cost * 0.85
    high = total_cost * 1.2
    if skipped_rows:
        plan[0]["notes"] = f"{plan[0].get('notes', '')} {'; '.join(skipped_rows[:3])}".strip()
    duration_total = sum(safe_float(row.get("adjusted_days"), 0.0) for row in plan)
    duration_days = max(1, safe_int(round(duration_total), 1))
    return plan, round(low, 2), round(high, 2), crew_size, duration_days, safe_int(round(total_hours), 0)


def similar_examples(similar: pd.DataFrame) -> list[dict[str, Any]]:
    if similar.empty:
        return []
    keep = [
        "job_id",
        "customer",
        "job_name",
        "estimated_sqft",
        "estimated_value",
        "price_per_sqft",
        "estimate_file",
        "folder_url",
        "similarity_score",
        "reason_matched",
    ]
    return similar[[column for column in keep if column in similar.columns]].head(8).to_dict(orient="records")


def draft_workbook_inputs(field_input: FieldNotesInput, scope: dict[str, Any], material_plan: list[dict[str, Any]], labor_plan: list[dict[str, Any]], travel_plan: dict[str, Any], review_flags: list[str]) -> dict[str, Any]:
    city_state_zip = " ".join(
        part
        for part in [
            ", ".join(part for part in (scope.get("city"), scope.get("state")) if part),
            field_input.zip_code or "",
        ]
        if part
    )
    return {
        "header": {
            "C2_job_name": first_nonblank(field_input.job_name, scope.get("project_type"), "Field Notes Estimate Draft"),
            "C3_job_type": scope.get("project_type"),
            "C4_site_address": field_input.site_address,
            "C5_city_state_zip": city_state_zip,
            "C12_estimated_sqft": scope.get("surface_area_sqft"),
            "gross_area_sqft": scope.get("gross_area_sqft"),
            "deduction_area_sqft": scope.get("deduction_area_sqft"),
            "dimension_notes": scope.get("dimension_warnings") or [],
        },
        "material_rows": material_plan,
        "labor_rows": labor_plan,
        "travel_rows": [travel_plan],
        "adders_review_rows": [{"flag": flag} for flag in review_flags],
    }


def estimate_from_field_notes(
    raw_notes: str,
    optional_overrides: dict[str, Any] | None = None,
    database_url: str | None = None,
    *,
    data: EstimatorData | None = None,
    assumptions: EstimatorAssumptions | None = None,
) -> EstimateRecommendation:
    assumptions = assumptions or EstimatorAssumptions()
    optional_overrides = optional_overrides or {}
    field_input = FieldNotesInput(
        raw_notes=raw_notes,
        job_name=optional_overrides.get("job_name"),
        site_address=optional_overrides.get("site_address"),
        city=optional_overrides.get("city"),
        state=optional_overrides.get("state"),
        zip_code=optional_overrides.get("zip_code"),
        estimated_sqft=optional_positive_float(optional_overrides.get("estimated_sqft")),
        substrate=optional_overrides.get("substrate"),
        roof_condition=optional_overrides.get("roof_condition"),
        coating_type=optional_overrides.get("coating_type"),
        warranty_target_years=optional_positive_int(optional_overrides.get("warranty_target_years")),
        access_complexity=optional_overrides.get("access_complexity"),
        penetrations_complexity=optional_overrides.get("penetrations_complexity"),
        insulation_present=optional_overrides.get("insulation_present"),
        condensation_risk=optional_overrides.get("condensation_risk"),
    )
    if data is None:
        data = load_estimator_data(database_url=database_url, prefer_database=bool(database_url))
    parsed = parse_field_notes(field_input)
    scope = parsed_to_scope(parsed, field_input)
    similar = find_similar_jobs(data, scope, limit=8)
    legacy_calibration = calibrate_from_history(similar, data.line_items, scope)
    template_calibration = historical_template_calibration(data, similar)
    calibration = {**legacy_calibration, **template_calibration}
    decision = evaluate_decision_tree(scope, calibration)
    material_plan, material_low, material_high, material_review_flags = build_material_plan(scope, data, calibration, decision, assumptions)
    labor_review_flags: list[str] = []
    try:
        labor_plan, labor_low, labor_high, crew_size, duration_days, _labor_hours = build_labor_plan(scope, calibration, decision, assumptions)
    except Exception as err:
        labor_plan = [
            {
                "task": "labor_allowance",
                "base_days": 1.0,
                "adjusted_days": 1.0,
                "crew_size": 4,
                "total_hours": 40,
                "estimated_cost": 0.0,
                "needs_review": True,
                "notes": f"Labor calibration failed; manual labor pricing required. Error: {type(err).__name__}",
            }
        ]
        labor_low = 0.0
        labor_high = 0.0
        crew_size = 4
        duration_days = 1
        labor_review_flags = ["Historical labor calibration failed; manual labor pricing required."]
    travel_plan = build_travel_plan(scope, recommended_crew_size=crew_size, estimated_work_days=duration_days, assumptions=assumptions)
    equipment_low = sum(to_float(row.get("estimated_cost")) or 0 for row in material_plan if row.get("category") == "equipment") * 0.85
    equipment_high = equipment_low * 1.25
    travel_low = to_float(travel_plan.get("travel_vehicle_cost")) or 0
    travel_high = travel_low * 1.15
    subtotal_low = material_low + labor_low + equipment_low + travel_low
    subtotal_high = material_high + labor_high + equipment_high + travel_high
    estimate_low = subtotal_low * 1.18
    estimate_high = subtotal_high * 1.28
    estimate_target = (estimate_low + estimate_high) / 2
    review_flags = []
    review_flags.extend(f"Missing: {item}" for item in parsed.missing_info)
    review_flags.extend(parsed.review_flags)
    review_flags.extend(decision.get("human_review_flags") or [])
    review_flags.extend(material_review_flags)
    review_flags.extend(labor_review_flags)
    if any("Historical labor calibration was incomplete" in str(row.get("notes") or "") for row in labor_plan):
        review_flags.append("Historical labor calibration was incomplete for one or more tasks.")
    if any("Historical labor calibration unavailable" in str(row.get("notes") or "") for row in labor_plan):
        review_flags.append("Historical labor calibration unavailable or incomplete.")
    if travel_plan.get("needs_travel_review"):
        review_flags.append("Travel assumptions require review.")
    if data.template_rows.empty:
        review_flags.append("estimate_template_rows unavailable or empty; template calibration is limited.")
    if data.pricing.empty:
        review_flags.append("pricing_catalog unavailable or empty; current material pricing is limited.")
    if data.template_rows.empty and not data.classified_line_items.empty:
        review_flags.append("Using estimate_line_item_classifications fallback evidence.")
    return EstimateRecommendation(
        parsed_fields=asdict(parsed),
        recommended_scope=decision.get("recommended_scope") or [],
        material_plan=material_plan,
        labor_plan=labor_plan,
        travel_plan=travel_plan,
        historical_calibration=calibration,
        similar_examples=similar_examples(similar),
        estimate_low=round(estimate_low, 2),
        estimate_target=round(estimate_target, 2),
        estimate_high=round(estimate_high, 2),
        review_flags=review_flags,
        human_review_required=bool(review_flags),
        draft_workbook_inputs=draft_workbook_inputs(field_input, scope, material_plan, labor_plan, travel_plan, review_flags),
    )
