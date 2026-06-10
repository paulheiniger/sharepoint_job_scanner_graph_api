from pathlib import Path
import tempfile

from jobscan.batch_sharepoint_sync import BatchScanRoot, add_batch_context, crew_schedule_rows
from jobscan.scan import records_as_dicts, scan_root
from jobscan.schedule_extractor import add_business_days, parse_duration_days, parse_start_date


def test_parse_schedule_dates_and_durations() -> None:
    assert parse_start_date("scheduled start: 06.15.26") == "2026-06-15"
    assert parse_start_date("mobilize 6/15 in 2026") == "2026-06-15"
    assert parse_duration_days("install days: 2") == 2
    assert parse_duration_days("estimated duration 1 week") == 5
    assert add_business_days("2026-06-12", 3) == "2026-06-16"


def test_scan_root_adds_schedule_fields_from_job_spec() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        job = root / "Pence UofL"
        job.mkdir()
        (job / "Job Spec.txt").write_text(
            "Crew Leader: Santos\nstart date: 6/15/26\nproduction days: 7\n",
            encoding="utf-8",
        )

        record = scan_root(root, scan_context="2026 Roofing/Contracted")[0]
        add_batch_context(record, BatchScanRoot(folder="2026 Roofing/Contracted", pipeline_status="Contracted"))
        row = records_as_dicts([record])[0]

    assert row["crew_leader"] == "Santos"
    assert row["estimated_start_date"] == "2026-06-15"
    assert row["estimated_duration_days"] == 7
    assert row["estimated_end_date"] == "2026-06-23"
    assert row["schedule_status"] == "Scheduled"
    assert row["ready_to_schedule"] is True
    assert row["blocking_issue"] is None
    assert row["schedule_source_file"] == "Pence UofL/Job Spec.txt"
    assert row["schedule_confidence"] == "high"


def test_crew_schedule_rows_surface_missing_schedule_info() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        job = root / "Folder Only Job"
        job.mkdir()

        record = scan_root(root, scan_context="2026 Roofing/Folder Created")[0]
        add_batch_context(record, BatchScanRoot(folder="2026 Roofing/Folder Created", pipeline_status="Folder Created"))
        row = crew_schedule_rows([record])[0]

    assert row["schedule_status"] == "Unscheduled"
    assert row["ready_to_schedule"] is False
    assert "Missing estimated duration" in row["blocking_issue"]
    assert "Missing estimated start date" in row["blocking_issue"]
    assert "Missing crew leader" in row["blocking_issue"]
    assert "Missing job spec" in row["blocking_issue"]
