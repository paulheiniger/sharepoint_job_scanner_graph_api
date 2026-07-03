from __future__ import annotations

import math
import re
from datetime import date
from pathlib import Path
from typing import Any

from .rules import first_nonblank, to_float

DEFAULT_ESTIMATE_TEMPLATE_PATH = Path("templates/Estimate - Full Turnkey.xlsx")
FALLBACK_ESTIMATE_TEMPLATE_PATH = Path("data/estimate_samples/Estimate - Full Turnkey.xlsx")
DEFAULT_ESTIMATE_OUTPUT_DIR = Path("output/estimates")

HEADER_CELLS = {
    "C2_job_name": "C2",
    "C3_job_type": "C3",
    "C4_site_address": "C4",
    "C5_city_state_zip": "C5",
    "C12_estimated_sqft": "C12",
}
INSULATION_HEADER_CELLS = {
    "C2_job_name": "C2",
    "C3_job_type": "C3",
    "C4_site_address": "C4",
    "C5_city_state_zip": "C5",
}

COATING_ROWS = [26, 27, 28]
INSULATION_FOAM_ROWS = [19, 20, 21]
INSULATION_THERMAL_BARRIER_ROWS = [30, 31, 32]
MANUAL_ADDER_ROWS = list(range(173, 181))
LABOR_ROW_BY_TASK = {
    "labor_prep": 116,
    "labor_prime": 118,
    "labor_seam_sealer": 120,
    "labor_base": 122,
    "labor_top_coat": 124,
    "labor_caulk": 126,
    "labor_details": 128,
    "labor_cleanup": 132,
    "labor_loading": 137,
    "labor_traveling": 139,
}
INSULATION_LABOR_ROW_BY_TASK = {
    "labor_set_up": 78,
    "set_up": 78,
    "labor_mask": 80,
    "mask": 80,
    "labor_prime": 82,
    "labor_membrane": 84,
    "labor_foam": 86,
    "foam": 86,
    "labor_dc_315": 88,
    "dc_315": 88,
    "labor_misc": 90,
    "labor_clean_up": 92,
    "labor_cleanup": 92,
    "labor_loading": 95,
    "labor_traveling": 97,
    "meals_lodging": 100,
}


def resolve_default_template_path() -> Path:
    return DEFAULT_ESTIMATE_TEMPLATE_PATH if DEFAULT_ESTIMATE_TEMPLATE_PATH.exists() else FALLBACK_ESTIMATE_TEMPLATE_PATH


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")
    return cleaned[:90] or "estimate_draft"


def is_formula(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("=")


def _write_cell(ws: Any, cell: str, value: Any) -> bool:
    if value is None or value == "":
        return False
    if is_formula(ws[cell].value):
        return False
    ws[cell] = value
    return True


def _add_comment(ws: Any, cell: str, text: str) -> None:
    if not text:
        return
    from openpyxl.comments import Comment

    ws[cell].comment = Comment(text[:30000], "Estimator")


def _number(value: Any) -> float | None:
    number = to_float(value)
    if number is None or not math.isfinite(number):
        return None
    return number


def _quantity(row: dict[str, Any]) -> float | int | None:
    value = _number(row.get("quantity"))
    if value is None:
        return None
    return int(value) if float(value).is_integer() else value


def _estimated_sqft(draft_workbook_inputs: dict[str, Any]) -> float | None:
    header = draft_workbook_inputs.get("header") or {}
    return _number(header.get("C12_estimated_sqft") or header.get("estimated_sqft") or header.get("surface_area_sqft"))


def _template_type(draft_workbook_inputs: dict[str, Any], template_path: Path | None = None) -> str:
    explicit = first_nonblank(draft_workbook_inputs.get("template_type"), (draft_workbook_inputs.get("header") or {}).get("template_type")).lower()
    job_type = first_nonblank((draft_workbook_inputs.get("header") or {}).get("C3_job_type")).lower()
    path_text = str(template_path or "").lower()
    if explicit in {"insulation", "roofing"}:
        return explicit
    if "insulation" in job_type or "insulation" in path_text:
        return "insulation"
    return "roofing"


def _row_text(row: dict[str, Any]) -> str:
    return " ".join(str(row.get(key) or "") for key in ("item", "category", "notes", "task")).lower()


def _manual_adder_label(row: dict[str, Any]) -> str:
    label = first_nonblank(row.get("item"), row.get("task"), row.get("flag"), "Review allowance")
    if row.get("needs_review") is True and "review" not in label.lower():
        return f"{label} - REVIEW"
    return label


def _write_manual_adder(ws: Any, row_number: int, row: dict[str, Any]) -> None:
    label = _manual_adder_label(row)
    amount = _number(row.get("estimated_cost"))
    _write_cell(ws, f"A{row_number}", label)
    if amount is not None:
        _write_cell(ws, f"F{row_number}", round(amount, 2))
    notes = first_nonblank(row.get("notes"), row.get("flag"))
    status = "REVIEW" if row.get("needs_review") or amount is None else ""
    _write_cell(ws, f"G{row_number}", " - ".join(part for part in (status, notes) if part))


def _write_coating_row(ws: Any, row_number: int, row: dict[str, Any], sqft: float | None) -> None:
    item = first_nonblank(row.get("item"), "Roof coating")
    selector_code = _number(row.get("selector_code"))
    gallons = _number(row.get("estimated_gallons") or row.get("quantity"))
    unit_price = _number(row.get("unit_price"))
    area_sqft = _number(row.get("area_sqft") or row.get("basis_sqft")) or sqft
    gal_per_100_sqft = _number(row.get("gal_per_100_sqft"))
    waste_factor_pct = _number(row.get("waste_factor_pct"))
    if selector_code is not None:
        _write_cell(ws, f"A{row_number}", int(selector_code) if float(selector_code).is_integer() else selector_code)
    else:
        _write_cell(ws, f"A{row_number}", item)
    if area_sqft:
        _write_cell(ws, f"C{row_number}", round(area_sqft, 2))
    if gal_per_100_sqft is not None:
        _write_cell(ws, f"D{row_number}", round(gal_per_100_sqft, 4))
    elif area_sqft and gallons:
        _write_cell(ws, f"D{row_number}", round(gallons * 100 / area_sqft, 4))
    elif gallons:
        _write_cell(ws, f"G{row_number}", round(gallons, 2))
    if unit_price is not None:
        _write_cell(ws, f"E{row_number}", round(unit_price, 4))
    if waste_factor_pct is not None:
        _write_cell(ws, "A30", round(waste_factor_pct, 4))
    _add_comment(ws, f"A{row_number}", first_nonblank(row.get("notes"), "Generated from estimator material plan."))


def _write_primer_row(ws: Any, row: dict[str, Any], sqft: float | None) -> None:
    selector_code = _number(row.get("selector_code"))
    area_sqft = _number(row.get("area_sqft") or row.get("basis_sqft"))
    quantity = _quantity(row)
    unit_price = _number(row.get("unit_price"))
    if selector_code is not None:
        _write_cell(ws, "A39", int(selector_code) if float(selector_code).is_integer() else selector_code)
        if area_sqft is not None:
            _write_cell(ws, "C39", round(area_sqft, 2))
        elif quantity is not None:
            _write_cell(ws, "C39", quantity)
    elif quantity is not None:
        _write_cell(ws, "C39", quantity)
    elif sqft:
        _write_cell(ws, "C39", round(sqft, 2))
    if unit_price is not None:
        _write_cell(ws, "E39", round(unit_price, 4))
    _add_comment(ws, "A39", first_nonblank(row.get("item"), "Primer allowance") + "\n" + first_nonblank(row.get("notes")))


def _write_caulk_sealant_row(ws: Any, row: dict[str, Any]) -> None:
    explicit_row = _number(row.get("workbook_row"))
    row_number = int(explicit_row) if explicit_row is not None and int(explicit_row) in {43, 45} else 43
    selector_code = _number(row.get("selector_code"))
    quantity = _number(row.get("estimated_units") or row.get("quantity"))
    unit_price = _number(row.get("unit_price"))
    if selector_code is not None:
        _write_cell(ws, f"A{row_number}", int(selector_code) if float(selector_code).is_integer() else selector_code)
    if unit_price is not None:
        _write_cell(ws, f"E{row_number}", round(unit_price, 4))
    if quantity is not None:
        _write_cell(ws, f"G{row_number}", round(quantity, 4))
    _add_comment(ws, f"A{row_number}", first_nonblank(row.get("item"), "Caulk / sealant allowance") + "\n" + first_nonblank(row.get("notes")))


def _write_fabric_row(ws: Any, row: dict[str, Any]) -> None:
    linear_ft = _number(row.get("linear_ft") or row.get("quantity") or row.get("estimated_units"))
    unit_price = _number(row.get("unit_price"))
    if linear_ft is not None:
        _write_cell(ws, "C79", round(linear_ft, 4))
    if unit_price is not None:
        _write_cell(ws, "E79", round(unit_price, 4))
    _add_comment(ws, "A79", first_nonblank(row.get("item"), "Fabric allowance") + "\n" + first_nonblank(row.get("notes")))


def _write_board_stock_row(ws: Any, row: dict[str, Any]) -> None:
    explicit_row = _number(row.get("workbook_row"))
    row_number = int(explicit_row) if explicit_row is not None and int(explicit_row) in {58, 59, 60} else 58
    selector_code = _number(row.get("selector_code"))
    area_sqft = _number(row.get("area_sqft") or row.get("basis_sqft"))
    thickness = _number(row.get("thickness_inches"))
    price_per_square = _number(row.get("price_per_square") or row.get("unit_price"))
    if selector_code is not None:
        _write_cell(ws, f"A{row_number}", int(selector_code) if float(selector_code).is_integer() else selector_code)
    if area_sqft is not None:
        _write_cell(ws, f"C{row_number}", round(area_sqft, 2))
    if thickness is not None:
        _write_cell(ws, f"D{row_number}", round(thickness, 4))
    if price_per_square is not None:
        _write_cell(ws, f"E{row_number}", round(price_per_square, 4))
    _add_comment(ws, f"A{row_number}", first_nonblank(row.get("item"), "Board stock allowance") + "\n" + first_nonblank(row.get("notes")))


def _write_board_fastener_or_plate_row(ws: Any, row: dict[str, Any]) -> None:
    category = str(row.get("category") or row.get("template_bucket") or "").lower()
    explicit_row = _number(row.get("workbook_row"))
    if explicit_row is not None and int(explicit_row) in {63, 65}:
        row_number = int(explicit_row)
    else:
        row_number = 65 if category == "plates" else 63
    unit_price = _number(row.get("unit_price_per_thousand") or row.get("unit_price"))
    quantity = _number(row.get("estimated_units") or row.get("quantity"))
    if unit_price is not None:
        _write_cell(ws, f"E{row_number}", round(unit_price, 4))
    if quantity is not None:
        _write_cell(ws, f"G{row_number}", round(quantity, 4))
    _add_comment(ws, f"A{row_number}", first_nonblank(row.get("item"), "Board fastener/plate allowance") + "\n" + first_nonblank(row.get("notes")))


def _write_granules_row(ws: Any, row: dict[str, Any]) -> None:
    selector_code = _number(row.get("selector_code"))
    area_sqft = _number(row.get("area_sqft") or row.get("basis_sqft"))
    unit_price = _number(row.get("unit_price"))
    quantity = _number(row.get("estimated_units") or row.get("quantity"))
    if selector_code is not None:
        _write_cell(ws, "A36", int(selector_code) if float(selector_code).is_integer() else selector_code)
    if area_sqft is not None:
        _write_cell(ws, "C36", round(area_sqft, 2))
    if unit_price is not None:
        _write_cell(ws, "E36", round(unit_price, 4))
    if quantity is not None:
        _write_cell(ws, "G36", round(quantity, 4))
    _add_comment(ws, "A36", first_nonblank(row.get("item"), "Granules allowance") + "\n" + first_nonblank(row.get("notes")))


def _write_dumpster_row(ws: Any, row: dict[str, Any]) -> None:
    selector_code = _number(row.get("selector_code"))
    area_sqft = _number(row.get("area_sqft") or row.get("basis_sqft"))
    thickness = _number(row.get("thickness_inches"))
    unit_price = _number(row.get("unit_price"))
    margin_pct = _number(row.get("margin_pct"))
    if selector_code is not None:
        _write_cell(ws, "A69", int(selector_code) if float(selector_code).is_integer() else selector_code)
    if area_sqft is not None:
        _write_cell(ws, "C69", round(area_sqft, 2))
    if thickness is not None:
        _write_cell(ws, "D69", round(thickness, 4))
    if unit_price is not None:
        _write_cell(ws, "E69", round(unit_price, 4))
    if margin_pct is not None:
        _write_cell(ws, "F69", round(margin_pct, 4))
    _add_comment(ws, "A69", first_nonblank(row.get("item"), "Dumpster allowance") + "\n" + first_nonblank(row.get("notes")))


def _write_lift_row(ws: Any, row: dict[str, Any]) -> None:
    explicit_row = _number(row.get("workbook_row"))
    row_number = int(explicit_row) if explicit_row is not None and int(explicit_row) in {73, 74} else 73
    selector_code = _number(row.get("selector_code"))
    size = first_nonblank(row.get("size"))
    period = _number(row.get("period"))
    unit_price = _number(row.get("unit_price"))
    margin_pct = _number(row.get("margin_pct"))
    if selector_code is not None:
        _write_cell(ws, f"A{row_number}", int(selector_code) if float(selector_code).is_integer() else selector_code)
    if size:
        _write_cell(ws, f"C{row_number}", size)
    if period is not None:
        _write_cell(ws, f"D{row_number}", round(period, 4))
    if unit_price is not None:
        _write_cell(ws, f"E{row_number}", round(unit_price, 4))
    if margin_pct is not None:
        _write_cell(ws, f"F{row_number}", round(margin_pct, 4))
    _add_comment(ws, f"A{row_number}", first_nonblank(row.get("item"), "Lift allowance") + "\n" + first_nonblank(row.get("notes")))


def _write_generator_row(ws: Any, row: dict[str, Any]) -> None:
    days = _number(row.get("days") or row.get("period"))
    unit_price = _number(row.get("unit_price"))
    if days is not None:
        _write_cell(ws, "C99", round(days, 4))
    if unit_price is not None:
        _write_cell(ws, "E99", round(unit_price, 4))
    _add_comment(ws, "A99", first_nonblank(row.get("item"), "Generator allowance") + "\n" + first_nonblank(row.get("notes")))


def _write_delivery_fee_row(ws: Any, row: dict[str, Any]) -> None:
    units = _number(row.get("estimated_units") or row.get("units") or row.get("quantity"))
    unit_price = _number(row.get("unit_price"))
    if unit_price is not None:
        _write_cell(ws, "E76", round(unit_price, 4))
    if units is not None:
        _write_cell(ws, "G76", round(units, 4))
    _add_comment(ws, "A76", first_nonblank(row.get("item"), "Delivery fee") + "\n" + first_nonblank(row.get("notes")))


def _write_freight_row(ws: Any, row: dict[str, Any]) -> None:
    amount = _number(row.get("amount") or row.get("estimated_cost") or row.get("unit_price"))
    if amount is not None:
        _write_cell(ws, "E103", round(amount, 2))
    _add_comment(ws, "A103", first_nonblank(row.get("item"), "Freight") + "\n" + first_nonblank(row.get("notes")))


def _write_roofing_travel_cost_row(ws: Any, row: dict[str, Any]) -> None:
    explicit_row = _number(row.get("workbook_row"))
    row_number = int(explicit_row) if explicit_row is not None and int(explicit_row) in {106, 108} else 108
    trips = _number(row.get("trip_count") or row.get("trips"))
    miles = _number(row.get("round_trip_miles") or row.get("miles"))
    unit_price = _number(row.get("unit_price") or row.get("rate"))
    if trips is not None:
        _write_cell(ws, f"B{row_number}", round(trips, 4))
    if miles is not None:
        _write_cell(ws, f"C{row_number}", round(miles, 4))
    if unit_price is not None:
        _write_cell(ws, f"E{row_number}", round(unit_price, 4))
    _add_comment(ws, f"A{row_number}", first_nonblank(row.get("item"), "Travel / truck expense") + "\n" + first_nonblank(row.get("notes")))


def _write_thinner_row(ws: Any, row: dict[str, Any]) -> None:
    selector_code = _number(row.get("selector_code"))
    unit_price = _number(row.get("unit_price"))
    if selector_code is not None:
        _write_cell(ws, "A33", int(selector_code) if float(selector_code).is_integer() else selector_code)
    if unit_price is not None:
        _write_cell(ws, "E33", round(unit_price, 4))
    _add_comment(ws, "A33", first_nonblank(row.get("item"), "Thinner") + "\n" + first_nonblank(row.get("notes")))


def _write_roofing_accessory_row(ws: Any, row: dict[str, Any]) -> None:
    explicit_row = _number(row.get("workbook_row"))
    if explicit_row is None:
        return
    row_number = int(explicit_row)
    category = str(row.get("category") or row.get("template_bucket") or "").lower()
    unit_price = _number(row.get("unit_price"))
    amount = _number(row.get("amount") or row.get("estimated_cost"))
    quantity = _number(row.get("estimated_units") or row.get("units") or row.get("quantity"))
    linear_ft = _number(row.get("linear_ft") or row.get("quantity"))
    if row_number in {82, 84, 86}:
        if linear_ft is not None:
            _write_cell(ws, f"C{row_number}", round(linear_ft, 4))
        if unit_price is not None:
            _write_cell(ws, f"E{row_number}", round(unit_price, 4))
    elif row_number in {88, 90, 92, 94, 96}:
        if unit_price is not None:
            _write_cell(ws, f"E{row_number}", round(unit_price, 4))
        if quantity is not None:
            _write_cell(ws, f"G{row_number}", round(quantity, 4))
    elif row_number == 101:
        if amount is not None:
            _write_cell(ws, "E101", round(amount, 2))
    else:
        if unit_price is not None:
            _write_cell(ws, f"E{row_number}", round(unit_price, 4))
        if quantity is not None:
            _write_cell(ws, f"G{row_number}", round(quantity, 4))
    _add_comment(ws, f"A{row_number}", first_nonblank(row.get("item"), category, "Roof accessory") + "\n" + first_nonblank(row.get("notes")))


def _write_roofing_detail_quantity_row(ws: Any, row: dict[str, Any]) -> None:
    explicit_row = _number(row.get("workbook_row"))
    if explicit_row is None or int(explicit_row) not in {47, 49, 51, 53}:
        return
    row_number = int(explicit_row)
    linear_ft = _number(row.get("linear_ft") or row.get("quantity"))
    units = _number(row.get("estimated_units") or row.get("units") or row.get("quantity"))
    amount = _number(row.get("amount") or row.get("estimated_cost"))

    if row_number == 47:
        if linear_ft is not None:
            _write_cell(ws, "C47", round(linear_ft, 4))
    elif units is not None:
        _write_cell(ws, f"D{row_number}", round(units, 4))

    if amount is not None and amount > 0:
        _write_cell(ws, f"H{row_number}", round(amount, 2))

    _add_comment(
        ws,
        f"A{row_number}",
        first_nonblank(row.get("item"), row.get("template_bucket"), "Roof detail quantity") + "\n" + first_nonblank(row.get("notes")),
    )


def _write_roofing_foam_row(ws: Any, row: dict[str, Any]) -> None:
    explicit_row = _number(row.get("workbook_row"))
    if explicit_row is None or int(explicit_row) not in {19, 20, 21}:
        return
    row_number = int(explicit_row)
    selector_code = _number(row.get("selector_code") or row.get("editable_selector_code"))
    area_sqft = _number(row.get("area_sqft") or row.get("basis_sqft"))
    thickness = _number(row.get("thickness_inches"))
    unit_price = _number(row.get("unit_price"))
    yield_factor = _number(row.get("yield_factor") or row.get("yield_or_coverage"))

    if selector_code is not None:
        _write_cell(ws, f"A{row_number}", int(selector_code))
    if area_sqft is not None:
        _write_cell(ws, f"C{row_number}", round(area_sqft, 2))
    if thickness is not None:
        _write_cell(ws, f"D{row_number}", round(thickness, 4))
    if unit_price is not None:
        _write_cell(ws, f"E{row_number}", round(unit_price, 4))
    if yield_factor is not None:
        _write_cell(ws, f"F{row_number}", round(yield_factor, 4))

    _add_comment(
        ws,
        f"A{row_number}",
        first_nonblank(row.get("item"), row.get("template_bucket"), "Roofing SPF foam")
        + "\n"
        + first_nonblank(row.get("notes")),
    )


def _write_known_material(ws: Any, row: dict[str, Any], sqft: float | None, coating_row_index: int) -> tuple[bool, int]:
    text = _row_text(row)
    category = str(row.get("category") or "").lower()
    explicit_row = _number(row.get("workbook_row"))
    if category in {"roofing_foam", "foam"} or (explicit_row is not None and int(explicit_row) in {19, 20, 21}):
        _write_roofing_foam_row(ws, row)
        return True, coating_row_index
    if category in {"seams_misc", "penetrations", "hvac_units", "drains"} or (
        explicit_row is not None and int(explicit_row) in {47, 49, 51, 53}
    ):
        _write_roofing_detail_quantity_row(ws, row)
        return True, coating_row_index
    if category in {"dumpster", "dumpsters"} or (explicit_row is not None and int(explicit_row) == 69):
        _write_dumpster_row(ws, row)
        return True, coating_row_index
    if category == "lift" or (explicit_row is not None and int(explicit_row) in {73, 74}):
        _write_lift_row(ws, row)
        return True, coating_row_index
    if category == "generator" or (explicit_row is not None and int(explicit_row) == 99):
        _write_generator_row(ws, row)
        return True, coating_row_index
    if category == "delivery_fee" or (explicit_row is not None and int(explicit_row) == 76):
        _write_delivery_fee_row(ws, row)
        return True, coating_row_index
    if category == "freight" or (explicit_row is not None and int(explicit_row) == 103):
        _write_freight_row(ws, row)
        return True, coating_row_index
    if category in {"sales_trips", "sales_inspection_trips", "truck_expense"} or (
        explicit_row is not None and int(explicit_row) in {106, 108}
    ):
        _write_roofing_travel_cost_row(ws, row)
        return True, coating_row_index
    if category == "thinner" or (explicit_row is not None and int(explicit_row) == 33):
        _write_thinner_row(ws, row)
        return True, coating_row_index
    if category in {"edge_metal", "gutter", "downspouts", "roof_hatch", "scuppers", "curbs", "ladders", "pitch_pockets", "misc"} or (
        explicit_row is not None and int(explicit_row) in {82, 84, 86, 88, 90, 92, 94, 96, 101}
    ):
        _write_roofing_accessory_row(ws, row)
        return True, coating_row_index
    if category == "coating":
        if explicit_row is not None and int(explicit_row) in COATING_ROWS:
            target_row = int(explicit_row)
            _write_coating_row(ws, target_row, row, sqft)
            return True, max(coating_row_index, COATING_ROWS.index(target_row) + 1)
        if coating_row_index >= len(COATING_ROWS):
            return False, coating_row_index
        _write_coating_row(ws, COATING_ROWS[coating_row_index], row, sqft)
        return True, coating_row_index + 1
    if "primer" in text:
        _write_primer_row(ws, row, sqft)
        return True, coating_row_index
    if category in {"caulk_detail", "caulk_sealant"} or (row.get("workbook_row") and int(_number(row.get("workbook_row")) or 0) in {43, 45}):
        _write_caulk_sealant_row(ws, row)
        return True, coating_row_index
    if category == "fabric" or (row.get("workbook_row") and int(_number(row.get("workbook_row")) or 0) == 79) or ("fabric" in text and "coating" not in text):
        _write_fabric_row(ws, row)
        return True, coating_row_index
    if category == "board_stock" or (row.get("workbook_row") and int(_number(row.get("workbook_row")) or 0) in {58, 59, 60}):
        _write_board_stock_row(ws, row)
        return True, coating_row_index
    if category in {"fasteners", "fastener_treatment", "plates"} or (row.get("workbook_row") and int(_number(row.get("workbook_row")) or 0) in {63, 65}):
        _write_board_fastener_or_plate_row(ws, row)
        return True, coating_row_index
    if category == "granules" or (row.get("workbook_row") and int(_number(row.get("workbook_row")) or 0) == 36):
        _write_granules_row(ws, row)
        return True, coating_row_index
    if "caulk" in text or "sealant" in text:
        quantity = _quantity(row)
        unit_price = _number(row.get("unit_price"))
        if quantity is not None:
            _write_cell(ws, "G43", quantity)
        if unit_price is not None:
            _write_cell(ws, "E43", round(unit_price, 4))
        _add_comment(ws, "A43", first_nonblank(row.get("item"), "Caulk / sealant allowance") + "\n" + first_nonblank(row.get("notes")))
        return True, coating_row_index
    return False, coating_row_index


def _write_insulation_sqft_calculation(workbook: Any, header: dict[str, Any], sqft: float | None) -> None:
    if "Sq Ft Calculation" not in workbook.sheetnames or sqft is None:
        return
    ws = workbook["Sq Ft Calculation"]
    dimensions = header.get("sqft_calculation_rows") or header.get("dimension_rows") or []
    if isinstance(dimensions, list) and dimensions:
        start_row = 4
        for offset, item in enumerate(dimensions[:12]):
            if not isinstance(item, dict):
                continue
            row_number = start_row + offset
            _write_cell(ws, f"B{row_number}", first_nonblank(item.get("description"), item.get("label"), "Area"))
            _write_cell(ws, f"C{row_number}", _number(item.get("height")))
            _write_cell(ws, f"D{row_number}", _number(item.get("width")))
        return
    _write_cell(ws, "B4", "Estimated area from field notes")
    _write_cell(ws, "C4", 1)
    _write_cell(ws, "D4", round(sqft, 2))


def _write_insulation_material(ws: Any, row: dict[str, Any], indexes: dict[str, int]) -> bool:
    text = _row_text(row)
    category = str(row.get("category") or "").lower()
    quantity = _quantity(row)
    unit_price = _number(row.get("unit_price"))
    target_row: int | None = None
    if category == "foam" or "foam" in text:
        if indexes["foam"] >= len(INSULATION_FOAM_ROWS):
            return False
        target_row = INSULATION_FOAM_ROWS[indexes["foam"]]
        indexes["foam"] += 1
    elif "primer" in text:
        target_row = 26
    elif category in {"coating", "thermal_barrier_coating"} or any(term in text for term in ("thermal", "dc 315", "noburn", "coating")):
        if indexes["thermal"] >= len(INSULATION_THERMAL_BARRIER_ROWS):
            return False
        target_row = INSULATION_THERMAL_BARRIER_ROWS[indexes["thermal"]]
        indexes["thermal"] += 1
    elif "membrane" in text:
        target_row = 24
    elif "thinner" in text:
        target_row = 37
    elif "caulk" in text or "sealant" in text:
        target_row = 41 if indexes["caulk"] == 0 else 43
        indexes["caulk"] += 1
    elif "lift" in text:
        target_row = 47 if indexes["lift"] == 0 else 48
        indexes["lift"] += 1
    elif "delivery" in text:
        target_row = 50
    elif "generator" in text:
        target_row = 53
    elif "space heater" in text:
        target_row = 55
    elif "freight" in text:
        target_row = 59
    elif "drum" in text:
        target_row = 65
    elif "sales" in text or "inspection" in text:
        target_row = 68
    elif "truck" in text:
        target_row = 70
    elif "misc" in text:
        target_row = 57
    if target_row is None:
        return False
    if category == "foam" or "foam" in text:
        selector_code = _number(row.get("selector_code"))
        area_sqft = _number(row.get("area_sqft") or row.get("basis_sqft"))
        thickness = _number(row.get("thickness_inches"))
        yield_factor = _number(row.get("yield_factor") or row.get("yield_or_coverage"))
        if selector_code is not None:
            _write_cell(ws, f"A{target_row}", int(selector_code))
        if area_sqft is not None:
            _write_cell(ws, f"C{target_row}", round(area_sqft, 2))
        elif quantity is not None:
            _write_cell(ws, f"C{target_row}", quantity)
        if thickness is not None:
            _write_cell(ws, f"D{target_row}", round(thickness, 4))
        if unit_price is not None:
            _write_cell(ws, f"E{target_row}", round(unit_price, 4))
        if yield_factor is not None:
            _write_cell(ws, f"F{target_row}", round(yield_factor, 4))
    elif category in {"coating", "thermal_barrier_coating"} or any(term in text for term in ("thermal", "dc 315", "noburn", "coating")):
        area_sqft = _number(row.get("area_sqft") or row.get("basis_sqft"))
        gal_per_100 = _number(row.get("gal_per_100_sqft"))
        if area_sqft is not None:
            _write_cell(ws, f"C{target_row}", round(area_sqft, 2))
        elif quantity is not None:
            _write_cell(ws, f"C{target_row}", quantity)
        if gal_per_100 is not None:
            _write_cell(ws, f"D{target_row}", round(gal_per_100, 4))
        if unit_price is not None:
            _write_cell(ws, f"E{target_row}", round(unit_price, 4))
    else:
        if quantity is not None:
            _write_cell(ws, f"C{target_row}", quantity)
        if unit_price is not None:
            _write_cell(ws, f"E{target_row}", round(unit_price, 4))
    estimated_cost = _number(row.get("estimated_cost"))
    if estimated_cost is not None:
        _add_comment(ws, f"A{target_row}", f"{first_nonblank(row.get('item'), row.get('category'))}\nEstimator estimated cost: ${estimated_cost:,.2f}\n{first_nonblank(row.get('notes'))}")
    return True


def _write_labor_row(ws: Any, row: dict[str, Any]) -> bool:
    task = first_nonblank(row.get("task")).strip()
    row_number = LABOR_ROW_BY_TASK.get(task)
    if row_number is None:
        return False
    days = _number(row.get("adjusted_days") or row.get("base_days"))
    crew_size = _number(row.get("crew_size"))
    total_hours = _number(row.get("total_hours"))
    hourly_rate = _number(row.get("hourly_rate"))
    estimated_cost = _number(row.get("estimated_cost"))
    if task in {"labor_loading", "labor_traveling"}:
        if total_hours is not None and crew_size:
            _write_cell(ws, f"C{row_number}", round(total_hours / max(crew_size, 1), 2))
        if crew_size is not None:
            _write_cell(ws, f"E{row_number}", int(crew_size))
    else:
        if days is not None:
            _write_cell(ws, f"B{row_number}", round(days, 2))
        if crew_size is not None:
            _write_cell(ws, f"C{row_number}", int(crew_size))
    if hourly_rate is not None:
        _write_cell(ws, f"D{row_number}", round(hourly_rate, 4))
    if total_hours is not None:
        _write_cell(ws, f"G{row_number}", round(total_hours, 4))
    if estimated_cost is not None:
        _add_comment(ws, f"A{row_number}", f"Estimator estimated cost: ${estimated_cost:,.2f}")
    return True


def _write_insulation_labor_row(ws: Any, row: dict[str, Any]) -> bool:
    task = first_nonblank(row.get("task"), row.get("labor_package")).strip()
    row_number = INSULATION_LABOR_ROW_BY_TASK.get(task)
    if row_number is None:
        return False
    days = _number(row.get("adjusted_days") or row.get("base_days") or row.get("crew_days"))
    crew_size = _number(row.get("crew_size"))
    total_hours = _number(row.get("total_hours") or row.get("labor_hours"))
    estimated_cost = _number(row.get("estimated_cost"))
    if row_number in {95, 97}:
        if total_hours is not None and crew_size:
            _write_cell(ws, f"C{row_number}", round(total_hours / max(crew_size, 1), 2))
        if crew_size is not None:
            _write_cell(ws, f"E{row_number}", int(crew_size))
    elif row_number == 100:
        if days is not None:
            _write_cell(ws, "C100", round(days, 2))
        if crew_size is not None:
            _write_cell(ws, "E100", int(crew_size))
    else:
        if days is not None:
            _write_cell(ws, f"B{row_number}", round(days, 2))
        if crew_size is not None:
            _write_cell(ws, f"C{row_number}", int(crew_size))
    if estimated_cost is not None:
        _add_comment(ws, f"A{row_number}", f"Estimator estimated cost: ${estimated_cost:,.2f}")
    return True


def _write_travel_row(ws: Any, row: dict[str, Any]) -> dict[str, Any] | None:
    crew_size = _number(row.get("recommended_crew_size") or row.get("crew_size"))
    hours = _number(row.get("travel_labor_hours"))
    if hours is not None:
        if crew_size:
            _write_cell(ws, "C139", round(hours / max(crew_size, 1), 2))
            _write_cell(ws, "E139", int(crew_size))
        else:
            _write_cell(ws, "C139", round(hours, 2))
    vehicle_cost = _number(row.get("travel_vehicle_cost"))
    if vehicle_cost:
        return {
            "item": "Travel / vehicle cost allowance",
            "estimated_cost": vehicle_cost,
            "needs_review": bool(row.get("needs_travel_review")),
            "notes": first_nonblank(row.get("travel_notes"), "Generated from estimator travel plan."),
        }
    return None


def _output_filename(draft_workbook_inputs: dict[str, Any], output_filename: str | None) -> str:
    if output_filename:
        return output_filename if output_filename.lower().endswith(".xlsx") else f"{output_filename}.xlsx"
    header = draft_workbook_inputs.get("header") or {}
    job_name = first_nonblank(header.get("C2_job_name"), "estimate_draft")
    sqft = _estimated_sqft(draft_workbook_inputs)
    suffix = f"_{int(sqft)}sqft" if sqft else ""
    return f"estimate_draft_{safe_filename(job_name)}{suffix}.xlsx"


def generate_estimate_workbook(
    draft_workbook_inputs: dict,
    template_path: Path,
    output_dir: Path,
    output_filename: str | None = None,
) -> Path:
    try:
        import openpyxl
    except ImportError as exc:
        raise RuntimeError("Install openpyxl to generate estimate workbooks.") from exc

    template_path = Path(template_path)
    if not template_path.exists():
        raise FileNotFoundError(f"Estimate template workbook not found: {template_path}")
    workbook = openpyxl.load_workbook(template_path, data_only=False)
    if "Estimate" not in workbook.sheetnames:
        raise ValueError("Estimate template workbook is missing the 'Estimate' sheet.")
    ws = workbook["Estimate"]
    template_type = _template_type(draft_workbook_inputs, template_path)

    header = draft_workbook_inputs.get("header") or {}
    _write_cell(ws, "C1", date.today())
    header_cells = INSULATION_HEADER_CELLS if template_type == "insulation" else HEADER_CELLS
    for key, cell in header_cells.items():
        _write_cell(ws, cell, header.get(key))
    if template_type == "roofing":
        _write_cell(ws, "C12", header.get("C12_estimated_sqft"))
    dimension_lines = [
        f"Gross area: {header.get('gross_area_sqft')}",
        f"Deduction area: {header.get('deduction_area_sqft')}",
        f"Net area: {header.get('net_area_sqft')}",
    ]
    dimension_notes = header.get("dimension_notes") or []
    if isinstance(dimension_notes, str):
        dimension_notes = [dimension_notes]
    _add_comment(ws, "C12", "\n".join([line for line in dimension_lines if not line.endswith("None")] + list(dimension_notes)))

    sqft = _estimated_sqft(draft_workbook_inputs)
    if template_type == "insulation":
        _write_insulation_sqft_calculation(workbook, header, sqft)
    coating_row_index = 0
    insulation_indexes = {"foam": 0, "thermal": 0, "caulk": 0, "lift": 0}
    manual_adders: list[dict[str, Any]] = []
    for row in draft_workbook_inputs.get("material_rows") or []:
        if not isinstance(row, dict):
            continue
        if template_type == "insulation":
            placed = _write_insulation_material(ws, row, insulation_indexes)
        else:
            placed, coating_row_index = _write_known_material(ws, row, sqft, coating_row_index)
        if not placed:
            manual_adders.append(row)

    for row in draft_workbook_inputs.get("labor_rows") or []:
        if not isinstance(row, dict):
            continue
        placed = _write_insulation_labor_row(ws, row) if template_type == "insulation" else _write_labor_row(ws, row)
        if not placed:
            manual_adders.append(row)

    for row in draft_workbook_inputs.get("travel_rows") or []:
        if isinstance(row, dict):
            vehicle_row = _write_travel_row(ws, row)
            if vehicle_row:
                manual_adders.append(vehicle_row)

    for row in draft_workbook_inputs.get("adders_review_rows") or []:
        if isinstance(row, dict):
            manual_adders.append(row)

    for row_number, row in zip(MANUAL_ADDER_ROWS, manual_adders):
        _write_manual_adder(ws, row_number, row)

    workbook.calculation.fullCalcOnLoad = True
    workbook.calculation.forceFullCalc = True
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / _output_filename(draft_workbook_inputs, output_filename)
    workbook.save(output_path)
    return output_path
