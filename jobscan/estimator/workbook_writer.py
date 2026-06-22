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

COATING_ROWS = [26, 27, 28]
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

    header = draft_workbook_inputs.get("header") or {}
    _write_cell(ws, "C1", date.today())
    for key, cell in HEADER_CELLS.items():
        _write_cell(ws, cell, header.get(key))
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
    coating_row_index = 0
    manual_adders: list[dict[str, Any]] = []
    for row in draft_workbook_inputs.get("material_rows") or []:
        if not isinstance(row, dict):
            continue
        placed, coating_row_index = _write_known_material(ws, row, sqft, coating_row_index)
        if not placed:
            manual_adders.append(row)

    for row in draft_workbook_inputs.get("labor_rows") or []:
        if not isinstance(row, dict):
            continue
        if not _write_labor_row(ws, row):
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
