from pathlib import Path
import tempfile

from jobscan.estimate_datasets import (
    ESTIMATE_LINE_ITEM_FIELDS,
    ESTIMATE_SUMMARY_FIELDS,
    extract_estimate_dataset,
    scan_estimate_datasets_for_records,
    write_dataset_csv,
    write_dataset_json,
)
from jobscan.models import JobRecord


def write_detail_workbook(path: Path) -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Estimate"
    people = wb.create_sheet("People")

    ws["A1"] = "Job Name:"
    ws["B1"] = "ACRE Bens Bargain"
    ws["A3"] = "Materials"
    ws["A4"] = "Item"
    ws["B4"] = "Quantity"
    ws["C4"] = "Unit"
    ws["D4"] = "Unit Cost"
    ws["E4"] = "Extended Cost"
    ws["A5"] = "Foam Kit"
    ws["B5"] = 12
    ws["C5"] = "set"
    ws["D5"] = 100
    ws["E5"] = 1200
    ws["A6"] = "Fasteners"
    ws["B6"] = 500
    ws["C6"] = "ea"
    ws["D6"] = 0.25
    ws["E6"] = 125

    ws["A112"] = "Labor / Subcontractor"
    ws["B114"] = "Days"
    ws["C114"] = "No. of People"
    ws["D114"] = "Total Hours"
    ws["A115"] = "Set Up/Safety"
    ws["B115"] = 1
    ws["C115"] = 6
    ws["D115"] = 66
    ws["A116"] = "Details"
    ws["B116"] = 2
    ws["C116"] = 5
    ws["D116"] = 110
    ws["A148"] = "Total Hours"
    ws["B148"] = 10930
    ws["C148"] = "Total Days"
    ws["D148"] = 116
    people["A11"] = "Hours /Day"
    people["B11"] = 11
    wb.save(path)


def test_extract_estimate_dataset_summary_and_line_items() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        job = root / "ACRE Bens Bargain"
        job.mkdir()
        path = job / "Estimate Roofing (2026) - ACRE Bens Bargain.xlsx"
        write_detail_workbook(path)
        record = JobRecord(
            job_id="ACRE-BENS-BARGAIN",
            folder_name=job.name,
            folder_path=job.name,
            division="Roofing",
            pipeline_status="Contracted",
            customer="ACRE",
        )

        summary, line_items = extract_estimate_dataset(path, root, record)

    assert summary["job_id"] == "ACRE-BENS-BARGAIN"
    assert summary["estimate_file"] == "ACRE Bens Bargain/Estimate Roofing (2026) - ACRE Bens Bargain.xlsx"
    assert summary["estimated_labor_hours"] == 10930
    assert summary["estimated_duration_days"] == 116
    assert summary["estimated_crew_size"] == 6
    assert summary["estimated_hours_per_day"] == 11
    assert set(summary) == set(ESTIMATE_SUMMARY_FIELDS)

    names = [item["line_item_name"] for item in line_items]
    assert "Foam Kit" in names
    assert "Fasteners" in names
    assert "Set Up/Safety" in names
    assert "Details" in names
    details = next(item for item in line_items if item["line_item_name"] == "Details")
    assert details["section"] == "Labor / Subcontractor"
    assert details["labor_days"] == 2
    assert details["crew_size"] == 5
    assert details["labor_hours"] == 110
    assert set(line_items[0]) == set(ESTIMATE_LINE_ITEM_FIELDS)


def test_scan_estimate_datasets_for_records_and_writers() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        job = root / "ACRE Bens Bargain"
        job.mkdir()
        write_detail_workbook(job / "Estimate Roofing (2026) - ACRE Bens Bargain.xlsx")
        record = JobRecord(
            job_id="ACRE-BENS-BARGAIN",
            folder_name=job.name,
            folder_path=job.name,
            division="Roofing",
            pipeline_status="Contracted",
            customer="ACRE",
        )

        summaries, line_items = scan_estimate_datasets_for_records(root, [record])
        csv_path = root / "estimate_summary.csv"
        json_path = root / "estimate_line_items.json"
        write_dataset_csv(summaries, ESTIMATE_SUMMARY_FIELDS, csv_path)
        write_dataset_json(line_items, ESTIMATE_LINE_ITEM_FIELDS, json_path)

        assert csv_path.exists()
        assert json_path.exists()

    assert len(summaries) == 1
    assert len(line_items) >= 4
