import json
from pathlib import Path

from jobscan.batch_sharepoint_sync import (
    attach_estimate_detail_warnings,
    backup_existing_file,
    job_index_overwrite_blockers,
    replace_staged_outputs,
    stage_output,
)
from jobscan.models import JobRecord


def test_job_index_overwrite_blockers_require_explicit_shrink_and_partial_flags() -> None:
    assert job_index_overwrite_blockers(
        new_rows=72,
        previous_rows=297,
        scan_roots_failed=0,
        existing_job_index=True,
        allow_shrink=False,
        allow_partial=False,
    ) == ["shrink"]

    assert job_index_overwrite_blockers(
        new_rows=297,
        previous_rows=297,
        scan_roots_failed=1,
        existing_job_index=True,
        allow_shrink=False,
        allow_partial=False,
    ) == ["partial"]

    assert job_index_overwrite_blockers(
        new_rows=72,
        previous_rows=297,
        scan_roots_failed=1,
        existing_job_index=True,
        allow_shrink=True,
        allow_partial=True,
    ) == []


def test_staged_output_replaces_after_backup(tmp_path: Path) -> None:
    output = tmp_path / "job_index.json"
    output.write_text(json.dumps([{"job_id": "OLD"}]), encoding="utf-8")

    staged = stage_output(
        output,
        "job index JSON",
        lambda path: path.write_text(json.dumps([{"job_id": "NEW"}]), encoding="utf-8"),
        "20260612T120000Z",
    )
    backup = backup_existing_file(output, "20260612T120000Z")
    replace_staged_outputs([staged])

    assert output.exists()
    assert json.loads(output.read_text(encoding="utf-8")) == [{"job_id": "NEW"}]
    assert backup is not None
    assert json.loads(backup.read_text(encoding="utf-8")) == [{"job_id": "OLD"}]
    assert not staged.temp_path.exists()


def test_estimate_detail_failure_warning_attaches_to_job_record() -> None:
    record = JobRecord(job_id="JOB-1", folder_name="Job 1", folder_path="Job 1")
    summaries = [
        {
            "job_id": "JOB-1",
            "estimate_id": "EST-1",
            "extraction_warnings": "detail extraction failed: BadZipFile: File is not a zip file",
        }
    ]

    attach_estimate_detail_warnings([record], summaries)

    assert record.warnings == ["Estimate detail extraction failed"]
