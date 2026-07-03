from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from openpyxl import Workbook, load_workbook

from jobscan.estimator.workbench import workbench_to_draft_workbook_inputs
from jobscan.estimator.workbench_export import EXCEL_CELL_LIMIT, export_workbench_review_package
from jobscan.estimator.workbook_writer import generate_estimate_workbook, resolve_default_template_path


def sample_workbench() -> dict:
    return {
        "estimate_id": "review-test",
        "scope": {
            "project_type": "roof coating",
            "net_sqft": 10000,
            "created_at": datetime.now(UTC),
        },
        "historical_filters": {"division": "Roofing", "source_year": None},
        "materials": [
            {
                "include": True,
                "workbook_row": "26-28",
                "package": "Silicone",
                "package_key": "coating",
                "item_name": "GAF High Solids Silicone 55 Gal",
                "editable_basis_sqft": 10000,
                "editable_qty_per_sqft": 0.02,
                "historical_qty_per_sqft": 0.02,
                "calculated_quantity": 200,
                "unit": "gal",
                "current_unit_price": 38,
                "estimated_cost": 7600,
                "evidence_count": 12,
                "confidence": "high",
                "rows_accepted": 12,
                "rows_rejected": 2,
                "rejection_reasons": {"missing_qty": 2},
                "notes": "Historical default from 12 roofing jobs.",
                "explanation": "Used in historical jobs.",
            }
        ],
        "labor": [
            {
                "include": True,
                "workbook_row": "122",
                "labor_package": "Base Coat",
                "package_key": "labor_base",
                "editable_hours_per_1000_sqft": 4,
                "historical_hours_per_1000_sqft": 4,
                "calculated_hours": 40,
                "crew_size": 4,
                "labor_rate": 72,
                "estimated_cost": 2880,
                "evidence_count": 9,
                "confidence": "medium",
                "notes": "Historical default from 9 roofing jobs.",
                "explanation": "Historical median.",
            }
        ],
        "adders": [
            {
                "include": False,
                "workbook_row": "73/74",
                "adder": "Lift",
                "adder_key": "lift",
                "editable_value": 1500,
                "estimated_cost": 0,
                "evidence_count": 3,
                "confidence": "medium",
                "notes": "Shown unchecked.",
            }
        ],
        "similar_jobs": [{"job_id": "J1", "customer": "Acme"}],
        "review_flags": ["Verify substrate."],
    }


def test_export_package_creates_zip_with_expected_files_and_workbook(tmp_path) -> None:
    workbook = Workbook()
    workbook.active["A1"] = "estimate"
    workbook_path = tmp_path / "generated.xlsx"
    workbook.save(workbook_path)

    zip_path = export_workbench_review_package(
        workbench=sample_workbench(),
        input_notes="Roof coating notes",
        output_dir=tmp_path,
        workbook_path=workbook_path,
    )

    assert zip_path.exists()
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
        assert {
            "workbench_summary.json",
            "workbench_summary.xlsx",
            "workbench_debug.json",
            "README.txt",
            "estimator_input.txt",
            "exported_workbook.xlsx",
        }.issubset(names)
        assert not any(name.endswith(".pdf") or name.endswith(".png") for name in names)
        summary = json.loads(archive.read("workbench_summary.json"))
        assert summary["input_notes"] == "Roof coating notes"
        assert summary["parsed_scope"]["project_type"] == "roof coating"
        assert summary["decision_trace"][0]["section"] == "Material"
        readme = archive.read("README.txt").decode("utf-8")
        assert "Decision Trace" in readme
        assert "Product Guidance" in readme

        archive.extract("workbench_summary.xlsx", path=tmp_path)
    summary_workbook = load_workbook(tmp_path / "workbench_summary.xlsx", read_only=True, data_only=True)
    assert {
        "Materials Compact",
        "Labor Compact",
        "Adders Compact",
        "Decision Trace",
        "Product Guidance",
        "Debug Materials",
        "Debug Labor",
        "Debug Adders",
    }.issubset(set(summary_workbook.sheetnames))


def test_insulation_review_package_exports_without_workbook(tmp_path) -> None:
    workbench = {
        "estimate_id": "insulation-review",
        "scope": {
            "division": "Insulation",
            "template_type": "insulation",
            "project_type": "spray foam insulation",
            "gross_insulation_area_sqft": 2460,
            "net_insulation_area_sqft": 2388,
            "opening_area_missing": True,
        },
        "historical_filters": {"division": "Insulation", "template_type": "insulation"},
        "materials": [
            {
                "include": False,
                "workbook_row": "19-21",
                "package": "Foam",
                "package_key": "foam",
                "item_name": "Foam",
                "historical_item": "Closed-cell spray foam",
                "product_id": "gaco_roof_foam_f2780",
                "product_manufacturer": "Gaco",
                "product_knowledge_product_name": "GacoRoofFoam Low GWP F2780",
                "product_aged_r_value_per_inch": 5.7,
                "product_aged_r_value_per_inch_source": "Aged R-value 5.7 per inch.",
                "product_source_documents": ["product_documents/GacoRoofFoam-F2780.pdf"],
                "editable_qty_per_sqft": 0,
                "calculated_quantity": 0,
                "estimated_cost": 0,
                "evidence_count": 0,
                "confidence": "none",
                "notes": "Confirm foam type and thickness.",
            }
        ],
        "insulation_surfaces": [
            {
                "include": True,
                "surface_type": "walls",
                "surface": "Walls",
                "gross_area_sqft": 1260,
                "deduction_area_sqft": 72,
                "net_area_sqft": 1188,
                "target_r_value": 14,
                "foam_type": "closed_cell",
            },
            {
                "include": True,
                "surface_type": "ceiling",
                "surface": "Ceiling",
                "gross_area_sqft": 1200,
                "deduction_area_sqft": 0,
                "net_area_sqft": 1200,
                "target_r_value": 30,
                "foam_type": "closed_cell",
            },
        ],
        "labor": [
            {
                "include": False,
                "workbook_row": "86",
                "labor_package": "Foam",
                "package_key": "labor_foam",
                "editable_hours_per_1000_sqft": 0,
                "calculated_hours": 0,
                "estimated_cost": 0,
                "evidence_count": 0,
                "confidence": "none",
                "notes": "Confirm insulation scope.",
            }
        ],
        "adders": [],
        "similar_jobs": [],
        "review_flags": ["Missing rollup door width."],
    }

    zip_path = export_workbench_review_package(
        workbench=workbench,
        input_notes="Foam sprayed in a 30x40 metal building.",
        output_dir=tmp_path,
        workbook_export_error="Insulation workbook export was not attempted in this test.",
    )

    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
        assert "workbench_summary.json" in names
        assert "workbench_summary.xlsx" in names
        assert "README.txt" in names
        assert "workbook_export_error.txt" in names
        assert "exported_workbook.xlsx" not in names
        summary = json.loads(archive.read("workbench_summary.json"))
        assert summary["historical_filters"]["template_type"] == "insulation"
        assert summary["area_calculation_trace"]
        assert {row["surface"] for row in summary["insulation_performance_specs"]} == {"Walls", "Ceiling"}
        archive.extract("workbench_summary.xlsx", path=tmp_path)

    summary_workbook = load_workbook(tmp_path / "workbench_summary.xlsx", read_only=True, data_only=True)
    assert "Area Calculation Trace" in summary_workbook.sheetnames
    assert "Insulation Performance" in summary_workbook.sheetnames


def test_workbench_output_generates_estimate_workbook_and_review_package_includes_it(tmp_path) -> None:
    workbench = sample_workbench()
    workbench["materials"].extend(
        [
            {
                "include": True,
                "workbook_row": "39",
                "package": "Primer",
                "package_key": "primer",
                "item_name": "Epoxy Primer 5 Gal",
                "editable_basis_sqft": 10000,
                "editable_qty_per_sqft": 0.001,
                "historical_qty_per_sqft": 0.001,
                "calculated_quantity": 10,
                "unit": "pail",
                "current_unit_price": 275,
                "estimated_cost": 2750,
                "evidence_count": 5,
                "confidence": "medium",
                "notes": "Primer included by estimator.",
            },
            {
                "include": False,
                "workbook_row": "43",
                "package": "Caulk / Detail",
                "package_key": "caulk_detail",
                "item_name": "Unchecked sealant",
                "editable_basis_sqft": 10000,
                "editable_qty_per_sqft": 1,
                "historical_qty_per_sqft": 1,
                "calculated_quantity": 10000,
                "unit": "tube",
                "current_unit_price": 999,
                "estimated_cost": 999000,
                "evidence_count": 5,
                "confidence": "medium",
                "notes": "Should not export because unchecked.",
            },
        ]
    )
    draft_inputs = workbench_to_draft_workbook_inputs(workbench)

    assert {row["category"] for row in draft_inputs["material_rows"]} == {"coating", "primer"}
    assert {row["task"] for row in draft_inputs["labor_rows"]} == {"labor_base"}

    workbook_path = generate_estimate_workbook(draft_inputs, resolve_default_template_path(), tmp_path, "workbench.xlsx")
    workbook = load_workbook(workbook_path, data_only=False)
    ws = workbook["Estimate"]

    assert ws["A26"].value == "GAF High Solids Silicone 55 Gal"
    assert ws["C26"].value == 10000
    assert ws["E26"].value == 38
    assert ws["C39"].value == 10
    assert ws["E39"].value == 275
    assert ws["B122"].value == 1.25
    assert ws["C122"].value == 4
    assert ws["A43"].value != "Unchecked sealant"

    zip_path = export_workbench_review_package(
        workbench=workbench,
        input_notes="Roof coating notes",
        output_dir=tmp_path,
        workbook_path=workbook_path,
    )
    with zipfile.ZipFile(zip_path) as archive:
        assert "exported_workbook.xlsx" in archive.namelist()
        assert "workbook_export_error.txt" not in archive.namelist()


def test_export_excel_handles_timezone_datetimes_and_long_text(tmp_path) -> None:
    workbench = sample_workbench()
    long_text = "x" * (EXCEL_CELL_LIMIT + 1000)
    workbench["materials"][0]["explanation"] = long_text
    workbench["materials"][0]["debug_payload"] = {
        "when": datetime.now(UTC),
        "uuid": uuid4(),
        "amount": Decimal("12.34"),
        "items": [long_text],
    }

    zip_path = export_workbench_review_package(workbench=workbench, input_notes="Notes", output_dir=tmp_path)

    with zipfile.ZipFile(zip_path) as archive:
        archive.extract("workbench_summary.xlsx", path=tmp_path)
    workbook = load_workbook(tmp_path / "workbench_summary.xlsx", read_only=True, data_only=True)
    materials = workbook["Debug Materials"]
    headers = [cell.value for cell in next(materials.iter_rows(min_row=1, max_row=1))]
    explanation_index = headers.index("explanation")
    row = next(materials.iter_rows(min_row=2, max_row=2))
    value = row[explanation_index].value

    assert isinstance(value, str)
    assert len(value) <= EXCEL_CELL_LIMIT
    assert "truncated for Excel" in value


def test_review_package_includes_product_guidance_sheet(tmp_path) -> None:
    workbench = sample_workbench()
    workbench["materials"][0].update(
        {
            "decision_id": "roofing_coating_system",
            "estimator_decision": "Silicone coating",
            "historical_recommendation": "Historical coating decision from 12 jobs.",
            "editable_value": "item=GAF High Solids Silicone",
            "calculated_output_summary": "quantity=200, cost=7600",
            "row_traceability": "Estimate rows 26-28",
            "decision_evidence_count": 12,
            "decision_confidence": "high",
            "product_id": "gaf_high_solids_silicone",
            "product_manufacturer": "GAF",
            "product_guidance": "Use as silicone roof coating.",
            "product_warning_summary": "Do not apply over wet substrate.",
            "product_source_documents": ["product_documents/GAF Silicone.pdf"],
            "product_source_evidence_rows": [
                {
                    "field": "limitation",
                    "source_page": 2,
                    "source_text": "Do not apply over wet substrate.",
                }
            ],
            "product_match_score": 0.95,
        }
    )

    zip_path = export_workbench_review_package(workbench=workbench, input_notes="Notes", output_dir=tmp_path)

    with zipfile.ZipFile(zip_path) as archive:
        summary = json.loads(archive.read("workbench_summary.json"))
        assert summary["product_guidance"][0]["product_id"] == "gaf_high_solids_silicone"
        assert "wet substrate" in summary["product_guidance"][0]["warnings"]
        archive.extract("workbench_summary.xlsx", path=tmp_path)

    workbook = load_workbook(tmp_path / "workbench_summary.xlsx", read_only=True, data_only=True)
    assert "Product Guidance" in workbook.sheetnames


def test_missing_workbook_export_does_not_crash_package_export(tmp_path) -> None:
    zip_path = export_workbench_review_package(
        workbench=sample_workbench(),
        input_notes="No workbook yet",
        output_dir=tmp_path,
        workbook_export_error="Workbook generation failed in test.",
    )

    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
        assert "workbook_export_error.txt" in names
        assert "exported_workbook.xlsx" not in names
        assert b"Workbook generation failed" in archive.read("workbook_export_error.txt")
