from __future__ import annotations

import json
import math
import re
from typing import Any

import pandas as pd

from .decision_proposals import _reference_target_for_row


SCHEMA_VERSION = "reference_estimate_answer_key.v1"
ANSWER_KEY_SOURCE = "reference_estimate_answer_key"

INPUT_VALUE_FIELDS = (
    "selector_code",
    "editable_selector_code",
    "resolved_template_option",
    "selected_pricing_candidate",
    "basis_sqft",
    "area_sqft",
    "board_area_sqft",
    "quantity",
    "linear_ft",
    "units",
    "estimated_units",
    "estimated_sets",
    "estimated_gallons",
    "thickness_inches",
    "yield_or_coverage",
    "coverage_sqft_per_unit",
    "gal_per_100_sqft",
    "gal_per_sqft",
    "waste_factor_pct",
    "unit_price",
    "price_per_square",
    "unit_price_per_thousand",
    "days",
    "editable_days",
    "hours_per_day",
    "people_count",
    "crew_size",
    "crew_selector_code",
    "daily_rate",
    "hourly_rate",
    "total_hours",
    "editable_total_hours",
    "formula_mode",
    "trip_count",
    "round_trip_miles",
    "cost_per_mile",
    "markup_pct",
    "overhead_pct",
    "profit_pct",
    "warranty_years",
    "amount",
    "template_line",
    "markup_treatment",
)

OUTPUT_VALUE_FIELDS = (
    "estimated_cost",
    "calculated_cost",
    "formula_model",
    "formula_source",
)

NON_ACTIONABLE_UNMAPPED_LABELS = {
    "",
    "*",
    "unknown",
    "title",
    "title:",
    "type",
    "types",
    "types:",
    "mfg",
    "mfg:",
    "margin",
    "margin %",
    "est square feet",
    "est. square feet",
    "est. square feet:",
    "estimated square feet",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", _text(value).lower().replace("-", "_").replace(" ", "_")).strip("_")


def _number(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _optional_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clean_value(value: Any) -> Any:
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return round(value, 6)
    if isinstance(value, (list, dict)):
        return value
    if value in (None, "", [], {}):
        return None
    return value


def _frame(data: Any, attr: str) -> pd.DataFrame:
    value = getattr(data, attr, pd.DataFrame()) if data is not None else pd.DataFrame()
    if isinstance(value, pd.DataFrame):
        return value.copy()
    if isinstance(value, list):
        return pd.DataFrame(value)
    return pd.DataFrame()


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not _text(value):
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _cell_value(row: dict[str, Any], column: str) -> Any:
    row_number = str(row.get("row_number") or row.get("workbook_row") or "").strip()
    cell_values = _json_dict(row.get("cell_values"))
    return cell_values.get(f"{column}{row_number}") or cell_values.get(column)


def _first_present(*values: Any) -> Any:
    for value in values:
        cleaned = _clean_value(value)
        if cleaned is not None:
            return cleaned
    return None


def _rows_for_reference(
    data_or_rows: Any,
    *,
    job_id: str | None = None,
    document_id: str | None = None,
    source_file: str | None = None,
) -> pd.DataFrame:
    rows = data_or_rows if isinstance(data_or_rows, pd.DataFrame) else _frame(data_or_rows, "template_rows")
    if rows.empty:
        return rows
    filtered = rows.copy()
    if job_id and "job_id" in filtered.columns:
        filtered = filtered[filtered["job_id"].fillna("").astype(str).eq(str(job_id))]
    if document_id and "document_id" in filtered.columns:
        filtered = filtered[filtered["document_id"].fillna("").astype(str).eq(str(document_id))]
    if source_file and "source_file" in filtered.columns:
        needle = str(source_file).strip().lower()
        filtered = filtered[filtered["source_file"].fillna("").astype(str).str.lower().str.contains(re.escape(needle), na=False)]
    if "row_number" in filtered.columns:
        filtered = filtered.sort_values("row_number")
    return filtered


def _profile_for_rows(data: Any, rows: pd.DataFrame) -> dict[str, Any]:
    profiles = _frame(data, "job_context_profiles")
    if profiles.empty or rows.empty or "job_id" not in rows.columns or "job_id" not in profiles.columns:
        return {}
    job_ids = [str(item).strip() for item in rows["job_id"].dropna().astype(str).unique() if str(item).strip()]
    if not job_ids:
        return {}
    matched = profiles[profiles["job_id"].fillna("").astype(str).isin(job_ids)]
    if matched.empty:
        return {}
    return matched.fillna("").iloc[0].to_dict()


def _template_type_for_rows(rows: pd.DataFrame, fallback: str = "") -> str:
    if "template_type" not in rows.columns or rows.empty:
        return fallback
    values = [str(item).strip().lower() for item in rows["template_type"].dropna().astype(str) if str(item).strip()]
    return values[0] if values else fallback


def _source_workbook(rows: pd.DataFrame) -> dict[str, Any]:
    if rows.empty:
        return {}
    first = rows.fillna("").iloc[0].to_dict()
    return {
        "document_id": _text(first.get("document_id")),
        "job_id": _text(first.get("job_id")),
        "file_name": _text(first.get("source_file")),
        "worksheet": "Estimate",
    }


def _job_context(profile: dict[str, Any], rows: pd.DataFrame) -> dict[str, Any]:
    context = {
        "job_id": _text(profile.get("job_id")) or _text(rows.iloc[0].get("job_id") if not rows.empty else ""),
        "customer": _text(profile.get("customer")),
        "job_name": _text(profile.get("job_name")),
        "project_type": _text(profile.get("project_class") or profile.get("project_type")),
        "market_segment": _text(profile.get("market_segment")),
        "building_type": _text(profile.get("building_type")),
        "substrate": _text(profile.get("substrate")),
        "material_system": _text(profile.get("material_system")),
        "scope_summary": _text(profile.get("scope_summary")),
        "area_sqft": _optional_number(profile.get("area_sqft")),
        "warranty_years": _optional_number(profile.get("warranty_years")),
    }
    return {key: value for key, value in context.items() if value not in (None, "", [], {})}


def _row_area(row: dict[str, Any], target: dict[str, str]) -> float:
    bucket = target.get("template_bucket") or _norm(row.get("template_bucket"))
    if bucket in {
        "coating",
        "primer",
        "board_stock",
        "granules",
        "foam",
        "roofing_foam",
        "floor_base_coat",
        "floor_topcoat",
        "floor_coating",
        "floor_primer",
        "floor_flake",
    }:
        return _number(_first_present(row.get("area_sqft"), row.get("basis_sqft"), row.get("quantity"), _cell_value(row, "C")), 0.0)
    return _number(_first_present(row.get("area_sqft"), row.get("quantity"), _cell_value(row, "C")), 0.0)


def _proposed_values(row: dict[str, Any], target: dict[str, str]) -> dict[str, Any]:
    bucket = target.get("template_bucket") or _norm(row.get("template_bucket"))
    selected_name = _first_present(row.get("resolved_item_name"), row.get("selected_item_name"), row.get("row_label"))
    values: dict[str, Any] = {}
    if selected_name:
        values["resolved_template_option"] = selected_name
        values["selected_pricing_candidate"] = selected_name
    selector_code = _first_present(row.get("selector_code"), _cell_value(row, "A"))
    if selector_code is not None:
        values["selector_code"] = selector_code
        values["editable_selector_code"] = selector_code
    area = _row_area(row, target)
    if area > 0 and bucket in {
        "coating",
        "primer",
        "board_stock",
        "granules",
        "foam",
        "roofing_foam",
        "thermal_barrier_coating",
        "floor_base_coat",
        "floor_topcoat",
        "floor_coating",
        "floor_primer",
        "floor_flake",
    }:
        values["basis_sqft"] = area
    if bucket in {"fasteners", "plates"} and area > 0:
        values["board_area_sqft"] = area
    if bucket in {
        "coating",
        "primer",
        "granules",
        "foam",
        "roofing_foam",
        "thermal_barrier_coating",
        "membrane",
        "thinner",
        "caulk_detail",
        "caulk_sealant",
        "fabric",
        "seams_misc",
        "seam_treatment",
        "penetrations",
        "hvac_units",
        "drains",
        "floor_base_coat",
        "floor_topcoat",
        "floor_coating",
        "floor_primer",
        "floor_flake",
        "dumpster",
        "dumpsters",
        "lift",
        "delivery_fee",
        "generator",
        "space_heater",
        "misc_materials",
        "misc",
        "freight",
        "abaa_audit",
        "abaa_fee",
        "drum_disposal",
        "disposal",
        "edge_metal",
        "gutter",
        "downspouts",
        "roof_hatch",
        "scuppers",
        "curbs",
        "ladders",
        "pitch_pockets",
        "sales_tax",
        "warranty",
        "misc_insurance",
        "permits",
    }:
        quantity = _first_present(row.get("estimated_units"), row.get("quantity"), _cell_value(row, "G"))
        if quantity is not None:
            values["estimated_units"] = quantity
    if bucket in {"coating", "floor_base_coat", "floor_topcoat", "floor_coating"}:
        gallons = _first_present(row.get("estimated_gallons"), row.get("estimated_units"), _cell_value(row, "G"))
        if gallons is not None:
            values["estimated_gallons"] = gallons
        gal_per_100 = _first_present(row.get("gal_per_100_sqft"), _cell_value(row, "D"))
        if gal_per_100 is None and area > 0 and gallons is not None:
            gal_per_100 = _number(gallons) / area * 100.0
        if gal_per_100 is not None:
            values["gal_per_100_sqft"] = gal_per_100
    if bucket in {"foam", "roofing_foam", "board_stock"}:
        thickness = _first_present(row.get("thickness_inches"), _cell_value(row, "D"))
        if thickness is not None:
            values["thickness_inches"] = thickness
    if bucket in {"foam", "roofing_foam"}:
        yield_value = _first_present(row.get("yield_or_coverage"), row.get("yield_factor"), _cell_value(row, "F"))
        if yield_value is not None:
            values["yield_or_coverage"] = yield_value
        estimated_sets = _first_present(row.get("estimated_sets"))
        if estimated_sets is not None:
            values["estimated_sets"] = estimated_sets
    if bucket == "primer":
        coverage = _first_present(row.get("coverage_sqft_per_unit"))
        estimated_units = _number(values.get("estimated_units"), 0.0)
        if coverage is None and area > 0 and estimated_units > 0:
            coverage = area / estimated_units
        if coverage is not None:
            values["coverage_sqft_per_unit"] = coverage
    if bucket == "floor_primer":
        estimated_units = _number(values.get("estimated_units"), 0.0)
        if area > 0 and estimated_units > 0:
            values["coverage_sqft_per_unit"] = area / estimated_units
    if bucket in {"caulk_detail", "caulk_sealant"}:
        units = _first_present(row.get("estimated_units"), row.get("quantity"), _cell_value(row, "G"))
        if units is not None:
            values["units"] = units
            values["estimated_units"] = units
    if bucket in {"fabric", "membrane"}:
        linear_ft = _first_present(row.get("linear_ft"), row.get("quantity"), row.get("estimated_units"), _cell_value(row, "C"))
        if linear_ft is not None:
            values["linear_ft"] = linear_ft
            values["units"] = linear_ft
            values["estimated_units"] = linear_ft
    if bucket == "board_stock":
        price = _first_present(row.get("price_per_square"), row.get("unit_price"), _cell_value(row, "E"))
        if price is not None:
            values["price_per_square"] = price
    if bucket in {"fasteners", "plates"}:
        price = _first_present(row.get("unit_price_per_thousand"), row.get("unit_price"), _cell_value(row, "E"))
        if price is not None:
            values["unit_price_per_thousand"] = price
    if bucket.startswith("labor_") and bucket not in {"labor_loading", "labor_traveling", "labor_meals_lodging"}:
        for source, target_key in (
            ("days", "days"),
            ("days", "editable_days"),
            ("crew_size", "crew_size"),
            ("crew_size", "crew_people_selection"),
            ("crew_selector_code", "crew_selector_code"),
            ("total_hours", "total_hours"),
            ("total_hours", "editable_total_hours"),
            ("daily_rate", "daily_rate"),
            ("hourly_rate", "hourly_rate"),
            ("formula_mode", "formula_mode"),
        ):
            value = row.get(source)
            if value not in (None, ""):
                values[target_key] = value
    if bucket in {"labor_loading", "labor_traveling", "infrared_scan", "meals_lodging"}:
        if bucket == "meals_lodging":
            field_map = {"days": "days", "crew_size": "people_count", "unit_price": "unit_price"}
        elif bucket == "infrared_scan":
            field_map = {"total_hours": "hours_per_day", "unit_price": "unit_price"}
        else:
            field_map = {"total_hours": "hours_per_day", "crew_size": "people_count", "unit_price": "unit_price"}
        for source, target_key in field_map.items():
            value = row.get(source)
            if value not in (None, ""):
                values[target_key] = value
    if bucket in {"lift", "delivery_fee", "generator", "space_heater"}:
        for source, target_key in (("days", "days"), ("quantity", "quantity"), ("estimated_units", "estimated_units"), ("trips", "trip_count")):
            value = row.get(source)
            if value not in (None, ""):
                values[target_key] = value
    if _norm(row.get("template_bucket")) in {"sales_inspection_trips", "truck_expense"}:
        for source, target_key in (("trips", "trip_count"), ("round_trip_miles", "round_trip_miles"), ("cost_per_mile", "unit_price")):
            value = row.get(source)
            if value not in (None, ""):
                values[target_key] = value
    if target.get("section") == "pricing_markup_decisions":
        pct = _first_present(row.get("overhead_pct"), row.get("profit_pct"), row.get("markup_pct"), _cell_value(row, "F"))
        if pct is not None:
            values["markup_pct"] = pct
    if bucket == "warranty":
        for source, target_key in (("warranty_years", "warranty_years"), ("quantity", "quantity")):
            value = row.get(source)
            if value not in (None, ""):
                values[target_key] = value
    if str(target.get("section") or "").endswith("_free_adder_template_decisions"):
        values.setdefault("template_line", _first_present(row.get("row_label"), row.get("selected_item_name"), target.get("template_bucket")))
        values.setdefault("markup_treatment", "post_markup")
    amount = _first_present(row.get("amount"), row.get("estimated_cost"), _cell_value(row, "H"), _cell_value(row, "F"))
    if amount is not None and (
        str(target.get("section") or "").endswith("_free_adder_template_decisions")
        or (target.get("section") == "roofing_accessory_template_decisions" and bucket == "misc")
        or bucket in {"sales_tax", "warranty", "misc_insurance", "permits"}
    ):
        values["amount"] = amount
    unit_price = _first_present(row.get("unit_price"), _cell_value(row, "E"))
    if unit_price is not None and "unit_price" not in values and target.get("section") != "pricing_markup_decisions":
        values["unit_price"] = unit_price
    return {key: value for key, value in ((_k, _clean_value(_v)) for _k, _v in values.items()) if value is not None}


def _calculated_outputs(row: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for field in OUTPUT_VALUE_FIELDS:
        value = _clean_value(row.get(field))
        if value is not None:
            output[field] = value
    return output


def _line_item(row: dict[str, Any]) -> str:
    return _text(_first_present(row.get("resolved_item_name"), row.get("selected_item_name"), row.get("row_label"), row.get("template_bucket")))


def _has_numeric_signal(row: dict[str, Any]) -> bool:
    for field in (
        "quantity",
        "unit_price",
        "estimated_units",
        "estimated_sets",
        "estimated_gallons",
        "estimated_cost",
        "calculated_cost",
        "area_sqft",
        "basis_sqft",
        "linear_ft",
        "days",
        "crew_size",
        "total_hours",
        "daily_rate",
        "hourly_rate",
        "trips",
        "round_trip_miles",
        "cost_per_mile",
        "warranty_years",
        "overhead_pct",
        "profit_pct",
    ):
        if _number(row.get(field), 0.0) != 0:
            return True
    return False


def _positive_value(value: Any) -> float:
    return _number(value, 0.0)


def _first_positive_value(*values: Any) -> float:
    for value in values:
        number = _positive_value(value)
        if number > 0:
            return number
    return 0.0


def _decision_has_active_formula_basis(target: dict[str, str], values: dict[str, Any], outputs: dict[str, Any]) -> bool:
    if _first_positive_value(outputs.get("estimated_cost"), outputs.get("calculated_cost")) > 0:
        return True
    if _positive_value(values.get("amount")) > 0:
        return True
    bucket = _norm(target.get("template_bucket"))
    section = _text(target.get("section"))
    unit_rate = _first_positive_value(
        values.get("unit_price"),
        values.get("price_per_square"),
        values.get("unit_price_per_thousand"),
        values.get("daily_rate"),
        values.get("hourly_rate"),
    )
    physical_units = _first_positive_value(
        values.get("estimated_units"),
        values.get("units"),
        values.get("estimated_sets"),
        values.get("estimated_gallons"),
        values.get("linear_ft"),
        values.get("quantity"),
    )
    if bucket in {"sales_trips", "sales_inspection_trips", "truck_expense"}:
        return (
            _positive_value(values.get("trip_count")) > 0
            and _positive_value(values.get("round_trip_miles")) > 0
            and unit_rate > 0
        )
    if bucket in {"labor_loading", "labor_traveling"}:
        return (
            _positive_value(values.get("hours_per_day")) > 0
            and _positive_value(values.get("people_count")) > 0
            and unit_rate > 0
        )
    if bucket in {"infrared_scan"}:
        return _positive_value(values.get("hours_per_day")) > 0 and unit_rate > 0
    if bucket in {"meals_lodging", "labor_meals_lodging"}:
        return _positive_value(values.get("days")) > 0 and _positive_value(values.get("people_count")) > 0 and unit_rate > 0
    if section == "pricing_markup_decisions":
        return _positive_value(values.get("markup_pct")) > 0
    if section in {
        "roofing_detail_quantity_template_decisions",
        "insulation_detail_quantity_template_decisions",
        "flooring_detail_quantity_template_decisions",
    } or bucket in {"penetrations", "hvac_units", "drains", "seams_misc", "seam_treatment"}:
        return physical_units > 0
    if bucket.startswith("labor_"):
        return (
            _positive_value(values.get("days")) > 0
            and _positive_value(values.get("crew_size") or values.get("crew_people_selection")) > 0
            and unit_rate > 0
        ) or (_positive_value(values.get("total_hours") or values.get("editable_total_hours")) > 0 and unit_rate > 0)
    if bucket == "board_stock":
        return _positive_value(values.get("basis_sqft") or values.get("board_area_sqft")) > 0 and _positive_value(values.get("price_per_square")) > 0
    if bucket in {"fasteners", "plates"}:
        return _positive_value(values.get("estimated_units")) > 0 and _positive_value(values.get("unit_price_per_thousand")) > 0
    if bucket == "coating":
        return (
            _positive_value(values.get("basis_sqft")) > 0
            and _first_positive_value(values.get("gal_per_100_sqft"), values.get("gal_per_sqft"), values.get("estimated_gallons")) > 0
            and unit_rate > 0
        )
    if bucket in {"primer", "granules", "thermal_barrier_coating"}:
        return (_positive_value(values.get("basis_sqft")) > 0 and unit_rate > 0) or (physical_units > 0 and unit_rate > 0)
    if bucket in {"foam", "roofing_foam", "floor_base_coat", "floor_topcoat", "floor_coating", "floor_primer", "floor_flake"}:
        return _positive_value(values.get("basis_sqft")) > 0 and unit_rate > 0
    if bucket in {"delivery_fee", "lift", "generator", "space_heater", "dumpster", "disposal", "drum_disposal"}:
        return physical_units > 0 and unit_rate > 0
    return physical_units > 0 and unit_rate > 0


def _merge_duplicate_answer_key_preferences(preferences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    labor_additive_fields = {"days", "editable_days", "total_hours", "editable_total_hours"}
    for preference in preferences:
        key = (
            _text(preference.get("section")),
            _text(preference.get("decision_id")),
            _text(preference.get("workbook_row")),
        )
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = preference
            merged.append(preference)
            continue
        existing_values = existing.setdefault("proposed_values", {})
        incoming_values = preference.get("proposed_values") if isinstance(preference.get("proposed_values"), dict) else {}
        is_labor = _text(existing.get("section")).endswith("labor_template_decisions") or _norm(existing.get("template_bucket")).startswith("labor_")
        for field, value in incoming_values.items():
            if is_labor and field in labor_additive_fields:
                combined = _positive_value(existing_values.get(field)) + _positive_value(value)
                if combined > 0:
                    existing_values[field] = round(combined, 6)
                continue
            if existing_values.get(field) in (None, "", 0, 0.0):
                existing_values[field] = value
        existing["confidence"] = max(_number(existing.get("confidence"), 0.0), _number(preference.get("confidence"), 0.0))
        existing["review_required"] = bool(existing.get("review_required")) or bool(preference.get("review_required"))
        existing_reasons = list(existing.get("review_reasons") or [])
        for reason in preference.get("review_reasons") or []:
            if reason not in existing_reasons:
                existing_reasons.append(reason)
        existing["review_reasons"] = existing_reasons
        existing_evidence = list(existing.get("evidence") or [])
        for evidence in preference.get("evidence") or []:
            if evidence not in existing_evidence:
                existing_evidence.append(evidence)
        existing["evidence"] = existing_evidence
    return merged


def _is_actionable_unmapped_row(row: dict[str, Any]) -> bool:
    bucket = _norm(row.get("template_bucket"))
    kind = _norm(row.get("line_item_kind"))
    label = _norm(_line_item(row)).replace("_", " ")
    if kind in {"header", "total", "subtotal", "metadata", "other"}:
        return False
    if bucket in {"", "unknown"} and label in NON_ACTIONABLE_UNMAPPED_LABELS and not _has_numeric_signal(row):
        return False
    if bucket in {"", "unknown"} and not _has_numeric_signal(row):
        return False
    return bool(bucket or label or _has_numeric_signal(row))


def _decision_from_row(row: dict[str, Any], *, template_type: str) -> dict[str, Any] | None:
    target = _reference_target_for_row(row, template_type)
    if not target:
        return None
    values = _proposed_values(row, target)
    outputs = _calculated_outputs(row)
    if not _decision_has_active_formula_basis(target, values, outputs):
        return None
    line_item = _line_item(row)
    source_row = _text(row.get("row_number") or row.get("workbook_row"))
    evidence = {
        "source": ANSWER_KEY_SOURCE,
        "source_row": source_row,
        "source_template_row_id": _text(row.get("template_row_id")),
        "source_document_id": _text(row.get("document_id")),
        "source_file": _text(row.get("source_file")),
        "line_item": line_item,
        "template_bucket": _text(row.get("template_bucket")),
        "raw_text": _text(row.get("raw_text")),
    }
    return {
        **target,
        "source_row": source_row,
        "line_item": line_item,
        "include": True,
        "template_option": _text(_first_present(row.get("resolved_item_name"), row.get("selected_item_name"))),
        "inputs": values,
        "calculated_outputs": outputs,
        "formula": {
            "formula_model": _text(row.get("formula_model")),
            "formula_source": "estimate_template_rows",
        },
        "evidence": evidence,
        "confidence": _number(row.get("parsed_confidence"), 0.8),
        "needs_review": bool(row.get("needs_review")),
    }


def build_reference_estimate_answer_key(
    data_or_rows: Any,
    *,
    job_id: str | None = None,
    document_id: str | None = None,
    source_file: str | None = None,
    job_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows = _rows_for_reference(data_or_rows, job_id=job_id, document_id=document_id, source_file=source_file)
    template_type = _template_type_for_rows(rows, fallback=_text((job_context or {}).get("template_type")).lower())
    profile = _profile_for_rows(data_or_rows, rows)
    context = {**_job_context(profile, rows), **(job_context or {})}
    decisions: list[dict[str, Any]] = []
    unmapped: list[dict[str, Any]] = []
    inactive_mapped_count = 0
    for row in rows.fillna("").to_dict(orient="records"):
        if _norm(row.get("line_item_kind")) in {"header", "total", "subtotal", "metadata"} and not _reference_target_for_row(row, template_type):
            continue
        target = _reference_target_for_row(row, template_type)
        decision = _decision_from_row(row, template_type=template_type)
        if decision:
            decisions.append(decision)
        elif target:
            inactive_mapped_count += 1
        elif _is_actionable_unmapped_row(row):
            unmapped.append(
                {
                    "source_row": _text(row.get("row_number")),
                    "template_bucket": _text(row.get("template_bucket")),
                    "line_item": _line_item(row),
                    "estimated_cost": _clean_value(row.get("estimated_cost")),
                    "reason": "No current decision mapping was found.",
                }
            )
    return {
        "schema_version": SCHEMA_VERSION,
        "template_type": template_type,
        "source_workbook": _source_workbook(rows),
        "job_context": context,
        "decisions": decisions,
        "unmapped_rows": unmapped,
        "summary": {
            "decision_count": len(decisions),
            "inactive_mapped_count": inactive_mapped_count,
            "unmapped_count": len(unmapped),
            "source_row_count": int(len(rows)),
        },
    }


def answer_key_to_workbook_decision_preferences(answer_key: dict[str, Any]) -> list[dict[str, Any]]:
    preferences: list[dict[str, Any]] = []
    if not isinstance(answer_key, dict):
        return preferences
    template_type = _text(answer_key.get("template_type")).lower()
    for decision in answer_key.get("decisions") or []:
        if not isinstance(decision, dict):
            continue
        proposed_values = dict(decision.get("inputs") or decision.get("proposed_values") or {})
        target = {
            "section": decision.get("section"),
            "decision_id": decision.get("decision_id"),
            "template_bucket": decision.get("template_bucket"),
            "workbook_row": str(decision.get("normalized_workbook_row") or decision.get("workbook_row") or ""),
        }
        if not target["section"] or not target["decision_id"]:
            continue
        outputs = dict(decision.get("calculated_outputs") or {})
        if not _decision_has_active_formula_basis(target, proposed_values, outputs):
            continue
        source_row = _text(decision.get("source_row"))
        workbook_row = _text(target["workbook_row"])
        review_reasons = [
            "Mapped from structured reference estimate answer key; verify against the current workbook before export."
        ]
        if source_row and workbook_row and source_row != workbook_row:
            review_reasons.append(f"Source row {source_row} was normalized to current workbook row {workbook_row}.")
        evidence = decision.get("evidence") if isinstance(decision.get("evidence"), dict) else {}
        evidence.setdefault("source", ANSWER_KEY_SOURCE)
        preferences.append(
            {
                **target,
                "template_type": template_type,
                "include": bool(decision.get("include", True)),
                "proposed_values": proposed_values,
                "confidence": _number(decision.get("confidence"), 0.9),
                "review_required": bool(decision.get("needs_review")) or bool(review_reasons),
                "review_reasons": review_reasons,
                "evidence": [evidence],
                "source": ANSWER_KEY_SOURCE,
            }
        )
    return _merge_duplicate_answer_key_preferences(preferences)


def _extract_json_candidates(text: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    raw = str(text or "")
    for match in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.I | re.S):
        try:
            parsed = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            candidates.append(parsed)
    decoder = json.JSONDecoder()
    for index, char in enumerate(raw):
        if char != "{":
            continue
        try:
            parsed, _end = decoder.raw_decode(raw[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            candidates.append(parsed)
    return candidates


def parse_reference_answer_key_text(text: str) -> dict[str, Any] | None:
    for candidate in _extract_json_candidates(text):
        schema = _text(candidate.get("schema_version"))
        if schema == SCHEMA_VERSION:
            return candidate
        if {"decisions", "source_workbook"}.issubset(candidate.keys()):
            candidate = dict(candidate)
            candidate.setdefault("schema_version", SCHEMA_VERSION)
            return candidate
    return None
