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
    gallons = _number(row.get("quantity"))
    unit_price = _number(row.get("unit_price"))
    _write_cell(ws, f"A{row_number}", item)
    if sqft:
        _write_cell(ws, f"C{row_number}", round(sqft, 2))
    if sqft and gallons:
        _write_cell(ws, f"D{row_number}", round(gallons * 100 / sqft, 4))
    elif gallons:
        _write_cell(ws, f"G{row_number}", round(gallons, 2))
    if unit_price is not None:
        _write_cell(ws, f"E{row_number}", round(unit_price, 4))
    _add_comment(ws, f"A{row_number}", first_nonblank(row.get("notes"), "Generated from estimator material plan."))


def _write_primer_row(ws: Any, row: dict[str, Any], sqft: float | None) -> None:
    quantity = _quantity(row)
    unit_price = _number(row.get("unit_price"))
    if quantity is not None:
        _write_cell(ws, "C39", quantity)
    elif sqft:
        _write_cell(ws, "C39", round(sqft, 2))
    if unit_price is not None:
        _write_cell(ws, "E39", round(unit_price, 4))
    _add_comment(ws, "A39", first_nonblank(row.get("item"), "Primer allowance") + "\n" + first_nonblank(row.get("notes")))


def _write_known_material(ws: Any, row: dict[str, Any], sqft: float | None, coating_row_index: int) -> tuple[bool, int]:
    text = _row_text(row)
    category = str(row.get("category") or "").lower()
    if category == "coating":
        if coating_row_index >= len(COATING_ROWS):
            return False, coating_row_index
        _write_coating_row(ws, COATING_ROWS[coating_row_index], row, sqft)
        return True, coating_row_index + 1
    if "primer" in text:
        _write_primer_row(ws, row, sqft)
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
