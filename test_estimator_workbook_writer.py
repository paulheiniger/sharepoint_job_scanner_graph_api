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


def test_generate_estimate_workbook_writes_roofing_coating_selector_inputs(tmp_path: Path) -> None:
    template_path = tmp_path / "roofing_template.xlsx"
    workbook = openpyxl.Workbook()
    ws = workbook.active
    ws.title = "Estimate"
    ws["H26"] = "=E26*G26"
    ws["H27"] = "=E27*G27"
    workbook.save(template_path)

    inputs = {
        "template_type": "roofing",
        "header": {
            "C2_job_name": "Roofing Selector Draft",
            "C3_job_type": "roof coating",
            "C12_estimated_sqft": 10000,
        },
        "material_rows": [
            {
                "item": "GAF High Solids Silicone 55 Gal",
                "category": "coating",
                "workbook_row": "26",
                "selector_code": "11",
                "area_sqft": 10000,
                "gal_per_100_sqft": 1.5,
                "waste_factor_pct": 10,
                "unit_price": 38,
                "estimated_gallons": 166.6667,
                "estimated_cost": 6333.33,
            },
            {
                "item": "GAF High Solids Silicone 55 Gal",
                "category": "coating",
                "workbook_row": "27",
                "selector_code": "21",
                "area_sqft": 10000,
                "gal_per_100_sqft": 1.25,
                "unit_price": 38,
                "estimated_gallons": 138.8889,
                "estimated_cost": 5277.78,
            },
        ],
        "labor_rows": [],
        "travel_rows": [],
        "adders_review_rows": [],
    }

    output_path = generate_estimate_workbook(inputs, template_path, tmp_path, "selector_draft.xlsx")
    generated = openpyxl.load_workbook(output_path, data_only=False)
    ws = generated["Estimate"]

    assert ws["A26"].value == 11
    assert ws["C26"].value == 10000
    assert ws["D26"].value == 1.5
    assert ws["E26"].value == 38
    assert ws["A27"].value == 21
    assert ws["C27"].value == 10000
    assert ws["D27"].value == 1.25
    assert ws["E27"].value == 38
    assert ws["A30"].value == 10
    assert str(ws["H26"].value).startswith("=")


def test_generate_estimate_workbook_writes_roofing_primer_selector_inputs(tmp_path: Path) -> None:
    template_path = tmp_path / "roofing_template.xlsx"
    workbook = openpyxl.Workbook()
    ws = workbook.active
    ws.title = "Estimate"
    ws["H39"] = "=E39*G39"
    workbook.save(template_path)

    inputs = {
        "template_type": "roofing",
        "header": {
            "C2_job_name": "Primer Selector Draft",
            "C3_job_type": "roof coating",
            "C12_estimated_sqft": 10000,
        },
        "material_rows": [
            {
                "item": "Epoxy Primer 5 Gal - Clear/Black",
                "category": "primer",
                "workbook_row": "39",
                "selector_code": "1",
                "area_sqft": 1000,
                "coverage_sqft_per_unit": 250,
                "estimated_units": 4,
                "unit_price": 100,
                "estimated_cost": 400,
            }
        ],
        "labor_rows": [],
        "travel_rows": [],
        "adders_review_rows": [],
    }

    output_path = generate_estimate_workbook(inputs, template_path, tmp_path, "primer_selector_draft.xlsx")
    generated = openpyxl.load_workbook(output_path, data_only=False)
    ws = generated["Estimate"]

    assert ws["A39"].value == 1
    assert ws["C39"].value == 1000
    assert ws["E39"].value == 100
    assert str(ws["H39"].value).startswith("=")


def test_generate_estimate_workbook_writes_roofing_detail_selector_and_fabric_inputs(tmp_path: Path) -> None:
    template_path = tmp_path / "roofing_template.xlsx"
    workbook = openpyxl.Workbook()
    ws = workbook.active
    ws.title = "Estimate"
    ws["H43"] = "=E43*G43"
    ws["H45"] = "=E45*G45"
    ws["H79"] = "=C79*E79"
    workbook.save(template_path)

    inputs = {
        "template_type": "roofing",
        "header": {
            "C2_job_name": "Detail Selector Draft",
            "C3_job_type": "roof coating",
            "C12_estimated_sqft": 10000,
        },
        "material_rows": [
            {
                "item": "Silicone Sealant Sausage",
                "category": "caulk_detail",
                "workbook_row": "43",
                "selector_code": "2",
                "quantity": 48,
                "estimated_units": 48,
                "unit_price": 12,
                "estimated_cost": 576,
            },
            {
                "item": "Premium Seam Fabric Roll",
                "category": "fabric",
                "workbook_row": "79",
                "quantity": 100,
                "linear_ft": 100,
                "unit_price": 5,
                "estimated_cost": 500,
            },
        ],
        "labor_rows": [],
        "travel_rows": [],
        "adders_review_rows": [],
    }

    output_path = generate_estimate_workbook(inputs, template_path, tmp_path, "detail_selector_draft.xlsx")
    generated = openpyxl.load_workbook(output_path, data_only=False)
    ws = generated["Estimate"]

    assert ws["A43"].value == 2
    assert ws["E43"].value == 12
    assert ws["G43"].value == 48
    assert ws["C79"].value == 100
    assert ws["E79"].value == 5
    assert str(ws["H43"].value).startswith("=")
    assert str(ws["H79"].value).startswith("=")


def test_generate_estimate_workbook_writes_roofing_board_fastener_plate_inputs(tmp_path: Path) -> None:
    template_path = tmp_path / "roofing_template.xlsx"
    workbook = openpyxl.Workbook()
    ws = workbook.active
    ws.title = "Estimate"
    ws["G63"] = "=C58/32*12"
    ws["H58"] = "=C58/100*E58"
    ws["H63"] = "=E63*G63/1000"
    ws["G65"] = "=C58/32*12"
    ws["H65"] = "=E65*G65/1000"
    workbook.save(template_path)

    inputs = {
        "template_type": "roofing",
        "header": {
            "C2_job_name": "Board Selector Draft",
            "C3_job_type": "roof coating",
            "C12_estimated_sqft": 10000,
        },
        "material_rows": [
            {
                "item": "Dens Deck Cover Board 1/2 inch",
                "category": "board_stock",
                "workbook_row": "58",
                "selector_code": "3",
                "basis_sqft": 3200,
                "area_sqft": 3200,
                "thickness_inches": 0.5,
                "price_per_square": 45,
                "unit_price": 45,
                "estimated_cost": 1440,
            },
            {
                "item": "Roofing Fastener Screws",
                "category": "fasteners",
                "workbook_row": "63",
                "estimated_units": 1200,
                "unit_price_per_thousand": 100,
                "unit_price": 100,
                "estimated_cost": 120,
            },
            {
                "item": "Insulation Plates",
                "category": "plates",
                "workbook_row": "65",
                "estimated_units": 1200,
                "unit_price_per_thousand": 80,
                "unit_price": 80,
                "estimated_cost": 96,
            },
        ],
        "labor_rows": [],
        "travel_rows": [],
        "adders_review_rows": [],
    }

    output_path = generate_estimate_workbook(inputs, template_path, tmp_path, "board_selector_draft.xlsx")
    generated = openpyxl.load_workbook(output_path, data_only=False)
    ws = generated["Estimate"]

    assert ws["A58"].value == 3
    assert ws["C58"].value == 3200
    assert ws["D58"].value == 0.5
    assert ws["E58"].value == 45
    assert ws["E63"].value == 100
    assert ws["E65"].value == 80
    assert str(ws["G63"].value).startswith("=")
    assert str(ws["G65"].value).startswith("=")
    assert str(ws["H58"].value).startswith("=")
    assert str(ws["H63"].value).startswith("=")
    assert str(ws["H65"].value).startswith("=")


def test_generate_estimate_workbook_writes_roofing_granules_inputs(tmp_path: Path) -> None:
    template_path = tmp_path / "roofing_template.xlsx"
    workbook = openpyxl.Workbook()
    ws = workbook.active
    ws.title = "Estimate"
    ws["G36"] = "=(((C36/100)*50)/100)"
    ws["H36"] = "=E36*G36"
    workbook.save(template_path)

    inputs = {
        "template_type": "roofing",
        "header": {
            "C2_job_name": "Granules Selector Draft",
            "C3_job_type": "roof coating",
            "C12_estimated_sqft": 12000,
        },
        "material_rows": [
            {
                "item": "SESCO Snow White Roofing Granules",
                "category": "granules",
                "workbook_row": "36",
                "selector_code": "2",
                "basis_sqft": 12000,
                "area_sqft": 12000,
                "estimated_units": 60,
                "quantity": 60,
                "unit_price": 40,
                "estimated_cost": 2400,
            }
        ],
        "labor_rows": [],
        "travel_rows": [],
        "adders_review_rows": [],
    }

    output_path = generate_estimate_workbook(inputs, template_path, tmp_path, "granules_selector_draft.xlsx")
    generated = openpyxl.load_workbook(output_path, data_only=False)
    ws = generated["Estimate"]

    assert ws["A36"].value == 2
    assert ws["C36"].value == 12000
    assert ws["E36"].value == 40
    assert str(ws["G36"].value).startswith("=")
    assert str(ws["H36"].value).startswith("=")


def test_generate_estimate_workbook_writes_roofing_equipment_inputs(tmp_path: Path) -> None:
    template_path = tmp_path / "roofing_template.xlsx"
    workbook = openpyxl.Workbook()
    ws = workbook.active
    ws.title = "Estimate"
    ws["G69"] = "=(IF(A69=1,C69*D69/12/700,IF(A69=2,C69*D69/12/1000,IF(A69=3,C69*D69/12/1400,\"\"))))*(1+(F69/100))"
    ws["H69"] = "=G69*E69"
    ws["H73"] = "=D73*E73*(1+(F73/100))"
    ws["H74"] = "=D74*E74*(1+(F74/100))"
    ws["H99"] = "=C99*E99"
    workbook.save(template_path)

    inputs = {
        "template_type": "roofing",
        "header": {
            "C2_job_name": "Equipment Draft",
            "C3_job_type": "roof coating",
            "C12_estimated_sqft": 14000,
        },
        "material_rows": [
            {
                "item": "40 Yard Dumpster",
                "category": "dumpster",
                "workbook_row": "69",
                "selector_code": "3",
                "basis_sqft": 14000,
                "area_sqft": 14000,
                "thickness_inches": 2,
                "unit_price": 400,
                "margin_pct": 25,
            },
            {
                "item": "Boom Lift",
                "category": "lift",
                "workbook_row": "73",
                "selector_code": "2",
                "size": "60'",
                "period": 5,
                "unit_price": 600,
                "margin_pct": 20,
            },
            {
                "item": "Generator",
                "category": "generator",
                "workbook_row": "99",
                "days": 7,
                "unit_price": 50,
            },
        ],
        "labor_rows": [],
        "travel_rows": [],
        "adders_review_rows": [],
    }

    output_path = generate_estimate_workbook(inputs, template_path, tmp_path, "equipment_selector_draft.xlsx")
    generated = openpyxl.load_workbook(output_path, data_only=False)
    ws = generated["Estimate"]

    assert ws["A69"].value == 3
    assert ws["C69"].value == 14000
    assert ws["D69"].value == 2
    assert ws["E69"].value == 400
    assert ws["F69"].value == 25
    assert ws["A73"].value == 2
    assert ws["C73"].value == "60'"
    assert ws["D73"].value == 5
    assert ws["E73"].value == 600
    assert ws["F73"].value == 20
    assert ws["C99"].value == 7
    assert ws["E99"].value == 50
    assert str(ws["G69"].value).startswith("=")
    assert str(ws["H69"].value).startswith("=")
    assert str(ws["H73"].value).startswith("=")
    assert str(ws["H99"].value).startswith("=")


def test_generate_estimate_workbook_writes_roofing_labor_decision_inputs(tmp_path: Path) -> None:
    template_path = tmp_path / "roofing_template.xlsx"
    workbook = openpyxl.Workbook()
    ws = workbook.active
    ws.title = "Estimate"
    ws["H122"] = "=IF(G122=0,B122*J122,D122*G122)"
    ws["J122"] = "=IF(C122=4,People!$G$12)"
    workbook.save(template_path)

    inputs = {
        "template_type": "roofing",
        "header": {
            "C2_job_name": "Labor Decision Draft",
            "C3_job_type": "roof coating",
            "C12_estimated_sqft": 10000,
        },
        "material_rows": [],
        "labor_rows": [
            {
                "task": "labor_base",
                "base_days": 2,
                "adjusted_days": 2,
                "crew_size": 4,
                "total_hours": 40,
                "hourly_rate": 90,
                "daily_rate": 1600,
                "formula_mode": "mixed_formula",
                "estimated_cost": 3600,
            }
        ],
        "travel_rows": [],
        "adders_review_rows": [],
    }

    output_path = generate_estimate_workbook(inputs, template_path, tmp_path, "labor_decision_draft.xlsx")
    generated = openpyxl.load_workbook(output_path, data_only=False)
    ws = generated["Estimate"]

    assert ws["B122"].value == 2
    assert ws["C122"].value == 4
    assert ws["D122"].value == 90
    assert ws["G122"].value == 40
    assert str(ws["H122"].value).startswith("=")
    assert str(ws["J122"].value).startswith("=")


def test_generate_estimate_workbook_writes_roofing_travel_freight_inputs(tmp_path: Path) -> None:
    template_path = tmp_path / "roofing_template.xlsx"
    workbook = openpyxl.Workbook()
    ws = workbook.active
    ws.title = "Estimate"
    ws["H76"] = "=E76*G76"
    ws["H103"] = "=E103"
    ws["H106"] = "=B106*C106*E106"
    ws["H108"] = "=B108*C108*E108"
    workbook.save(template_path)

    inputs = {
        "template_type": "roofing",
        "header": {
            "C2_job_name": "Travel Freight Draft",
            "C3_job_type": "roof coating",
            "C12_estimated_sqft": 10000,
        },
        "material_rows": [
            {"item": "Delivery Fee", "category": "delivery_fee", "workbook_row": "76", "estimated_units": 2, "unit_price": 150},
            {"item": "Freight", "category": "freight", "workbook_row": "103", "amount": 425, "estimated_cost": 425},
            {
                "item": "Sales / Inspection Trips",
                "category": "sales_trips",
                "workbook_row": "106",
                "trip_count": 3,
                "round_trip_miles": 40,
                "unit_price": 0.75,
            },
            {
                "item": "Truck Expense",
                "category": "truck_expense",
                "workbook_row": "108",
                "trip_count": 4,
                "round_trip_miles": 50,
                "unit_price": 1.25,
            },
        ],
        "labor_rows": [],
        "travel_rows": [],
        "adders_review_rows": [],
    }

    output_path = generate_estimate_workbook(inputs, template_path, tmp_path, "travel_freight_draft.xlsx")
    generated = openpyxl.load_workbook(output_path, data_only=False)
    ws = generated["Estimate"]

    assert ws["E76"].value == 150
    assert ws["G76"].value == 2
    assert ws["E103"].value == 425
    assert ws["B106"].value == 3
    assert ws["C106"].value == 40
    assert ws["E106"].value == 0.75
    assert ws["B108"].value == 4
    assert ws["C108"].value == 50
    assert ws["E108"].value == 1.25
    assert str(ws["H76"].value).startswith("=")
    assert str(ws["H103"].value).startswith("=")
    assert str(ws["H106"].value).startswith("=")
    assert str(ws["H108"].value).startswith("=")


def test_generate_estimate_workbook_writes_roofing_accessory_inputs(tmp_path: Path) -> None:
    template_path = tmp_path / "roofing_template.xlsx"
    workbook = openpyxl.Workbook()
    ws = workbook.active
    ws.title = "Estimate"
    ws["G33"] = "=((G26+G27+G28)/55)*4"
    ws["H33"] = "=E33*G33"
    ws["H82"] = "=C82*E82"
    ws["H88"] = "=G88*E88"
    ws["H101"] = "=E101"
    workbook.save(template_path)

    inputs = {
        "template_type": "roofing",
        "header": {
            "C2_job_name": "Accessory Draft",
            "C3_job_type": "roof coating",
            "C12_estimated_sqft": 10000,
        },
        "material_rows": [
            {"item": "Xylene", "category": "thinner", "workbook_row": "33", "selector_code": "3", "unit_price": 12.5},
            {"item": "Edge Metal", "category": "edge_metal", "workbook_row": "82", "linear_ft": 100, "unit_price": 15},
            {"item": "Roof Hatch", "category": "roof_hatch", "workbook_row": "88", "estimated_units": 2, "unit_price": 300},
            {"item": "Misc.", "category": "misc", "workbook_row": "101", "amount": 275, "estimated_cost": 275},
        ],
        "labor_rows": [],
        "travel_rows": [],
        "adders_review_rows": [],
    }

    output_path = generate_estimate_workbook(inputs, template_path, tmp_path, "accessory_draft.xlsx")
    generated = openpyxl.load_workbook(output_path, data_only=False)
    ws = generated["Estimate"]

    assert ws["A33"].value == 3
    assert ws["E33"].value == 12.5
    assert ws["C82"].value == 100
    assert ws["E82"].value == 15
    assert ws["G88"].value == 2
    assert ws["E88"].value == 300
    assert ws["E101"].value == 275
    assert str(ws["G33"].value).startswith("=")
    assert str(ws["H33"].value).startswith("=")
    assert str(ws["H82"].value).startswith("=")
    assert str(ws["H88"].value).startswith("=")
    assert str(ws["H101"].value).startswith("=")


def test_generate_estimate_workbook_writes_roofing_detail_quantity_inputs(tmp_path: Path) -> None:
    template_path = tmp_path / "roofing_template.xlsx"
    workbook = openpyxl.Workbook()
    ws = workbook.active
    ws.title = "Estimate"
    workbook.save(template_path)

    inputs = {
        "template_type": "roofing",
        "header": {
            "C2_job_name": "Detail Quantity Draft",
            "C3_job_type": "roof coating",
            "C12_estimated_sqft": 10000,
        },
        "material_rows": [
            {"item": "Misc. / Seams", "category": "seams_misc", "workbook_row": "47", "linear_ft": 240, "amount": 1200},
            {"item": "Penetrations", "category": "penetrations", "workbook_row": "49", "estimated_units": 12, "amount": 600},
            {"item": "HVAC Units", "category": "hvac_units", "workbook_row": "51", "estimated_units": 2, "amount": 300},
            {"item": "Drains", "category": "drains", "workbook_row": "53", "estimated_units": 4, "amount": 400},
        ],
        "labor_rows": [],
        "travel_rows": [],
        "adders_review_rows": [],
    }

    output_path = generate_estimate_workbook(inputs, template_path, tmp_path, "detail_quantity_draft.xlsx")
    generated = openpyxl.load_workbook(output_path, data_only=False)
    ws = generated["Estimate"]

    assert ws["C47"].value == 240
    assert ws["H47"].value == 1200
    assert ws["D49"].value == 12
    assert ws["H49"].value == 600
    assert ws["D51"].value == 2
    assert ws["H51"].value == 300
    assert ws["D53"].value == 4
    assert ws["H53"].value == 400


def test_generate_estimate_workbook_writes_roofing_foam_inputs(tmp_path: Path) -> None:
    template_path = tmp_path / "roofing_template.xlsx"
    workbook = openpyxl.Workbook()
    ws = workbook.active
    ws.title = "Estimate"
    ws["G19"] = "=(C19/F19)*D19*1000"
    ws["H19"] = "=E19*G19"
    workbook.save(template_path)

    inputs = {
        "template_type": "roofing",
        "header": {
            "C2_job_name": "Roofing Foam Draft",
            "C3_job_type": "roof coating",
            "C12_estimated_sqft": 10000,
        },
        "material_rows": [
            {
                "item": "GacoRoofFoam F2733RHFO Roofing Foam",
                "category": "roofing_foam",
                "workbook_row": "19",
                "selector_code": "21",
                "area_sqft": 865,
                "basis_sqft": 865,
                "thickness_inches": 1.5,
                "yield_factor": 2600,
                "unit_price": 2.25,
                "estimated_units": 499.038462,
                "estimated_cost": 1122.84,
            }
        ],
        "labor_rows": [],
        "travel_rows": [],
        "adders_review_rows": [],
    }

    output_path = generate_estimate_workbook(inputs, template_path, tmp_path, "roofing_foam_draft.xlsx")
    generated = openpyxl.load_workbook(output_path, data_only=False)
    ws = generated["Estimate"]

    assert ws["A19"].value == 21
    assert ws["C19"].value == 865
    assert ws["D19"].value == 1.5
    assert ws["E19"].value == 2.25
    assert ws["F19"].value == 2600
    assert str(ws["G19"].value).startswith("=")
    assert str(ws["H19"].value).startswith("=")


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
