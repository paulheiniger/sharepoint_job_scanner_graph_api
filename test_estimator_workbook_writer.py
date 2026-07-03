from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from jobscan.estimator.workbook_writer import generate_estimate_workbook, resolve_default_template_path


pytest.importorskip("openpyxl")
import openpyxl


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sample_draft_workbook_inputs() -> dict:
    return {
        "header": {
            "C2_job_name": "Louisville Metal Roof",
            "C3_job_type": "roof coating",
            "C4_site_address": "123 Main St",
            "C5_city_state_zip": "Louisville, KY 40202",
            "C12_estimated_sqft": 9536,
            "gross_area_sqft": 9600,
            "deduction_area_sqft": 64,
            "net_area_sqft": 9536,
            "dimension_notes": ["Two skylights deducted."],
        },
        "material_rows": [
            {
                "item": "High Solids Silicone",
                "category": "coating",
                "quantity": 133.2,
                "unit": "gal",
                "unit_price": 38,
                "estimated_cost": 5061.6,
                "notes": "20 wet mils with waste factor.",
            },
            {
                "item": "Primer allowance",
                "category": "allowance",
                "quantity": 9536,
                "unit": "sqft",
                "unit_price": 0.25,
                "estimated_cost": 2384,
                "needs_review": True,
                "notes": "Rule-based primer allowance.",
            },
            {
                "item": "Seam treatment allowance",
                "category": "allowance",
                "quantity": 780,
                "unit": "lf",
                "unit_price": 3,
                "estimated_cost": 2340,
                "needs_review": True,
                "notes": "Estimator should verify seam layout.",
            },
        ],
        "labor_rows": [
            {
                "task": "labor_prep",
                "base_days": 2,
                "adjusted_days": 2.25,
                "crew_size": 4,
                "total_hours": 90,
                "estimated_cost": 7200,
            }
        ],
        "travel_rows": [
            {
                "travel_labor_hours": 5.0,
                "travel_vehicle_cost": 46.5,
                "crew_size": 4,
                "travel_notes": "Louisville distance bucket.",
            }
        ],
        "adders_review_rows": [{"flag": "Estimator should verify primer and seam assumptions."}],
    }


def test_generate_estimate_workbook_creates_output_and_preserves_template(tmp_path: Path) -> None:
    template_path = resolve_default_template_path()
    original_hash = file_hash(template_path)

    output_path = generate_estimate_workbook(sample_draft_workbook_inputs(), template_path, tmp_path)

    assert output_path.exists()
    assert output_path.suffix == ".xlsx"
    assert file_hash(template_path) == original_hash
    openpyxl.load_workbook(output_path, data_only=False)


def test_generate_estimate_workbook_fills_header_and_rows(tmp_path: Path) -> None:
    template_path = resolve_default_template_path()

    output_path = generate_estimate_workbook(sample_draft_workbook_inputs(), template_path, tmp_path, "draft.xlsx")
    workbook = openpyxl.load_workbook(output_path, data_only=False)
    ws = workbook["Estimate"]

    assert ws["C2"].value == "Louisville Metal Roof"
    assert ws["C3"].value == "roof coating"
    assert ws["C4"].value == "123 Main St"
    assert ws["C5"].value == "Louisville, KY 40202"
    assert ws["C12"].value == 9536
    assert "Two skylights deducted" in ws["C12"].comment.text

    assert ws["A26"].value == "High Solids Silicone"
    assert ws["C26"].value == 9536
    assert ws["E26"].value == 38
    assert str(ws["H26"].value).startswith("=")

    assert ws["B116"].value == 2.25
    assert ws["C116"].value == 4
    assert str(ws["H116"].value).startswith("=")

    manual_labels = [ws[f"A{row}"].value for row in range(173, 181)]
    assert any("Seam treatment allowance" in str(value) for value in manual_labels)
    assert any("Travel / vehicle cost allowance" in str(value) for value in manual_labels)


def test_generate_insulation_workbook_uses_sqft_calculation_and_insulation_rows(tmp_path: Path) -> None:
    template_path = tmp_path / "Estimate Insulation Template.xlsx"
    workbook = openpyxl.Workbook()
    ws = workbook.active
    ws.title = "Estimate"
    workbook.create_sheet("People")
    workbook.create_sheet("Materials")
    workbook.create_sheet("General")
    sqft_ws = workbook.create_sheet("Sq Ft Calculation")
    workbook.create_sheet("Performance & Payment Bonds")
    ws["C3"] = "Insulation"
    ws["D12"] = "='Sq Ft Calculation'!F15"
    ws["H19"] = "=E19*G19"
    ws["H86"] = "=IF(G86=0,B86*J86,D86*G86)"
    sqft_ws["E4"] = "=C4*D4"
    sqft_ws["F15"] = "=SUM(E4:E15)"
    workbook.save(template_path)

    inputs = {
        "template_type": "insulation",
        "header": {
            "C2_job_name": "McCall Residence",
            "C3_job_type": "Insulation - Walls Only",
            "C4_site_address": "2333 Todds Point Rd.",
            "C5_city_state_zip": "Simpsonville, KY",
            "C12_estimated_sqft": 2637,
        },
        "material_rows": [
            {
                "item": "Gaco 2.0 lb.",
                "category": "foam",
                "quantity": 2637,
                "selector_code": 11,
                "area_sqft": 2637,
                "thickness_inches": 3.0,
                "yield_factor": 13500,
                "unit_price": 1.63,
                "estimated_cost": 4298.31,
            },
            {"item": "DC 315 thermal barrier", "category": "thermal_barrier_coating", "quantity": 2637, "unit_price": 52},
        ],
        "labor_rows": [
            {"task": "labor_foam", "adjusted_days": 1.5, "crew_size": 3, "total_hours": 36, "estimated_cost": 1200}
        ],
        "travel_rows": [],
        "adders_review_rows": [],
    }

    output_path = generate_estimate_workbook(inputs, template_path, tmp_path, "insulation_draft.xlsx")
    generated = openpyxl.load_workbook(output_path, data_only=False)
    ws = generated["Estimate"]
    sqft_ws = generated["Sq Ft Calculation"]

    assert ws["C2"].value == "McCall Residence"
    assert ws["C3"].value == "Insulation - Walls Only"
    assert ws["C4"].value == "2333 Todds Point Rd."
    assert ws["C5"].value == "Simpsonville, KY"
    assert ws["D12"].value == "='Sq Ft Calculation'!F15"
    assert sqft_ws["B4"].value == "Estimated area from field notes"
    assert sqft_ws["C4"].value == 1
    assert sqft_ws["D4"].value == 2637
    assert ws["A19"].value == 11
    assert ws["C19"].value == 2637
    assert ws["D19"].value == 3
    assert ws["E19"].value == 1.63
    assert ws["F19"].value == 13500
    assert ws["C86"].value == 3
