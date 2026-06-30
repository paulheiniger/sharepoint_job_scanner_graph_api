from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from openpyxl import Workbook, load_workbook

from jobscan.estimator.workbench_export import EXCEL_CELL_LIMIT, export_workbench_review_package


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
            "estimator_input.txt",
            "exported_workbook.xlsx",
        }.issubset(names)
        assert not any(name.endswith(".pdf") or name.endswith(".png") for name in names)
        summary = json.loads(archive.read("workbench_summary.json"))
        assert summary["input_notes"] == "Roof coating notes"
        assert summary["parsed_scope"]["project_type"] == "roof coating"


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
    materials = workbook["Materials"]
    headers = [cell.value for cell in next(materials.iter_rows(min_row=1, max_row=1))]
    explanation_index = headers.index("explanation")
    row = next(materials.iter_rows(min_row=2, max_row=2))
    value = row[explanation_index].value

    assert isinstance(value, str)
    assert len(value) <= EXCEL_CELL_LIMIT
    assert "truncated for Excel" in value


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

