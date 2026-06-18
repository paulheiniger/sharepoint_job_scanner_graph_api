from __future__ import annotations

import json
from pathlib import Path

from jobscan import incremental_scan as inc
from jobscan import sharepoint_sync as sp
from jobscan.db_loader import parse_args as db_parse_args, selected_inputs
from jobscan.graph_client import SharePointTarget


class FakeClient:
    pass


class FakeEngine:
    pass


def root_rules() -> list[inc.ScanRootRule]:
    return [
        inc.ScanRootRule("2026 ROOFING/PROPOSED", division="Roofing", pipeline_status="Proposed", source_year=2026),
        inc.ScanRootRule("2025 FLOORING/COMPLETED", division="Flooring", pipeline_status="Completed", source_year=2025),
    ]


def test_changed_child_file_maps_to_containing_job_and_root_metadata() -> None:
    root, job_path, job_id = inc.map_path_to_job(
        "2026 ROOFING/PROPOSED/Acme Roof/Estimate.xlsx",
        root_rules(),
    )

    assert root and root.division == "Roofing"
    assert root.pipeline_status == "Proposed"
    assert root.source_year == 2026
    assert job_path == "2026 ROOFING/PROPOSED/Acme Roof"
    assert job_id == inc.stable_job_id(job_path)


def test_historical_year_root_and_configured_root_filtering() -> None:
    root, job_path, _job_id = inc.map_path_to_job(
        "2025 FLOORING/COMPLETED/Old Flooring Job/Invoice.pdf",
        root_rules(),
    )

    assert root and root.source_year == 2025
    assert job_path == "2025 FLOORING/COMPLETED/Old Flooring Job"


def test_processor_classification_for_estimate_tracking_timesheet_and_document() -> None:
    base = {
        "drive_id": "drive",
        "drive_item_id": "item",
        "change_type": "modified",
        "relative_path": "2026 ROOFING/PROPOSED/Job",
        "is_file": True,
    }

    assert inc.processor_for_item(inc.IncrementalItem(name="Estimate.xlsx", **base)) == "estimate"
    assert inc.processor_for_item(inc.IncrementalItem(name="Job Tracking Form.xlsx", **base)) == "job_tracking"
    assert inc.processor_for_item(inc.IncrementalItem(name="Office Timesheet.xlsx", **base)) == "office_timesheet"
    assert inc.processor_for_item(inc.IncrementalItem(name="Proposal.pdf", **base)) == "document"


def test_changeset_routes_changed_files_and_deleted_documents() -> None:
    stats = sp.DeltaSyncStats(
        mode="incremental",
        drive_id="drive",
        changed_files=[
            {
                "drive_id": "drive",
                "drive_item_id": "estimate-1",
                "change_type": "modified",
                "relative_path": "2026 ROOFING/PROPOSED/Acme Roof/Estimate.xlsx",
                "name": "Estimate.xlsx",
                "is_file": True,
            },
            {
                "drive_id": "drive",
                "drive_item_id": "tracking-1",
                "change_type": "modified",
                "relative_path": "2026 ROOFING/PROPOSED/Acme Roof/Job Tracking Form.xlsx",
                "name": "Job Tracking Form.xlsx",
                "is_file": True,
            },
        ],
        deleted_item_rows=[
            {
                "drive_id": "drive",
                "drive_item_id": "doc-1",
                "metadata": {
                    "name": "Old Proposal.pdf",
                    "relative_path": "2026 ROOFING/PROPOSED/Acme Roof/Old Proposal.pdf",
                    "file": {},
                },
            }
        ],
    )

    changeset = inc.changeset_from_delta_stats(stats, root_rules(), "run-1")

    assert len(changeset.affected_job_ids) == 1
    assert "2026 ROOFING/PROPOSED/Acme Roof/Estimate.xlsx" in changeset.affected_estimate_files
    assert "2026 ROOFING/PROPOSED/Acme Roof/Job Tracking Form.xlsx" in changeset.affected_tracking_files
    assert len(changeset.deleted_files) == 1


def test_moved_job_folder_marks_new_job_path() -> None:
    stats = sp.DeltaSyncStats(
        mode="incremental",
        drive_id="drive",
        changed_folders=[
            {
                "drive_id": "drive",
                "drive_item_id": "folder-1",
                "change_type": "moved",
                "relative_path": "2026 ROOFING/PROPOSED/Acme Roof",
                "name": "Acme Roof",
                "is_folder": True,
            }
        ],
    )

    changeset = inc.changeset_from_delta_stats(stats, root_rules(), "run-1")

    assert "2026 ROOFING/PROPOSED/Acme Roof" in changeset.affected_job_paths
    assert changeset.affected_job_ids == {inc.stable_job_id("2026 ROOFING/PROPOSED/Acme Roof")}


def test_merge_rows_preserves_unchanged_jobs_and_replaces_only_changed() -> None:
    existing = [{"job_id": "A", "job_name": "Old"}, {"job_id": "B", "job_name": "Keep"}]
    changed = [{"job_id": "A", "job_name": "New"}]

    merged = inc.merge_rows(existing, changed, "job_id")

    assert {row["job_id"]: row["job_name"] for row in merged} == {"A": "New", "B": "Keep"}


def test_document_row_queues_changed_files_and_marks_deleted() -> None:
    changed = inc.IncrementalItem(
        drive_id="drive",
        drive_item_id="doc-1",
        change_type="modified",
        relative_path="2026 ROOFING/PROPOSED/Acme Roof/Proposal.pdf",
        name="Proposal.pdf",
        web_url="https://sharepoint/doc",
        is_file=True,
        job_path="2026 ROOFING/PROPOSED/Acme Roof",
        job_id="JOB",
        etag="abc",
    )
    deleted = inc.IncrementalItem(**{**changed.__dict__, "change_type": "deleted"})

    assert inc.document_row_from_item(changed)["extraction_status"] == "pending"
    assert inc.document_row_from_item(deleted)["extraction_status"] == "deleted"


def test_no_change_incremental_run_writes_empty_changed_outputs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        inc,
        "run_delta_sync",
        lambda **_kwargs: sp.DeltaSyncStats(mode="incremental", drive_id="drive"),
    )
    monkeypatch.setattr(inc, "persist_incremental_run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(inc, "load_dataset", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("db load should not run")))
    config = tmp_path / "roots.yaml"
    config.write_text(
        "scan_roots:\n  - folder: 2026 ROOFING/PROPOSED\n    division: Roofing\n    pipeline_status: Proposed\n",
        encoding="utf-8",
    )

    report = inc.run_incremental(
        engine=FakeEngine(),
        client=FakeClient(),
        target=SharePointTarget("example.sharepoint.com", "/sites/Data"),
        config_path=config,
        output_dir=tmp_path,
        cache_root=tmp_path / ".cache",
        metadata_only=True,
        skip_db_load=False,
        run_id="run-1",
    )

    assert report.status == "succeeded"
    assert json.loads((tmp_path / "changed_documents.json").read_text(encoding="utf-8")) == []
    assert json.loads((tmp_path / "changed_jobs.json").read_text(encoding="utf-8")) == []


def test_changed_only_loader_flags_select_dataset_paths() -> None:
    args = db_parse_args(
        [
            "--jobs-changed",
            "output/changed_jobs.json",
            "--documents-changed",
            "output/changed_documents.json",
        ]
    )

    assert selected_inputs(args) == [
        ("jobs", Path("output/changed_jobs.json"), False),
        ("documents", Path("output/changed_documents.json"), False),
    ]


def test_changed_estimate_merges_without_dropping_unrelated_outputs(monkeypatch, tmp_path: Path) -> None:
    config = tmp_path / "roots.yaml"
    config.write_text(
        "scan_roots:\n  - folder: 2026 ROOFING/PROPOSED\n    division: Roofing\n    pipeline_status: Proposed\n",
        encoding="utf-8",
    )
    (tmp_path / "estimate_summary.json").write_text(
        json.dumps(
            [
                {"estimate_id": "old-estimate", "job_id": "OLD"},
                {"estimate_id": "changed-estimate", "job_id": "JOB", "estimated_value": 1},
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "estimate_line_items.json").write_text(
        json.dumps(
            [
                {"line_item_id": "old-line", "estimate_id": "old-estimate"},
                {"line_item_id": "changed-old-line", "estimate_id": "changed-estimate"},
            ]
        ),
        encoding="utf-8",
    )
    stats = sp.DeltaSyncStats(
        mode="incremental",
        drive_id="drive",
        changed_files=[
            {
                "drive_id": "drive",
                "drive_item_id": "estimate-1",
                "change_type": "modified",
                "relative_path": "2026 ROOFING/PROPOSED/Acme Roof/Estimate.xlsx",
                "name": "Estimate.xlsx",
                "is_file": True,
            }
        ],
    )
    fake_record = inc.JobRecord(job_id="JOB", folder_name="Acme Roof", folder_path="2026 ROOFING/PROPOSED/Acme Roof")
    monkeypatch.setattr(inc, "run_delta_sync", lambda **_kwargs: stats)
    monkeypatch.setattr(inc, "persist_incremental_run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(inc, "scan_affected_job_records", lambda *_args, **_kwargs: ([fake_record], []))
    monkeypatch.setattr(inc, "process_changed_jobs", lambda *_args, **_kwargs: ([{"job_id": "JOB", "folder_path": "2026 ROOFING/PROPOSED/Acme Roof"}], []))
    monkeypatch.setattr(
        inc,
        "scan_estimate_datasets_for_records",
        lambda *_args, **_kwargs: (
            [{"estimate_id": "changed-estimate", "job_id": "JOB", "estimated_value": 2}],
            [{"line_item_id": "changed-new-line", "estimate_id": "changed-estimate"}],
        ),
    )

    report = inc.run_incremental(
        engine=FakeEngine(),
        client=FakeClient(),
        target=SharePointTarget("example.sharepoint.com", "/sites/Data"),
        config_path=config,
        output_dir=tmp_path,
        cache_root=tmp_path / ".cache",
        skip_db_load=True,
        run_id="run-estimate",
    )

    estimates = {row["estimate_id"]: row for row in json.loads((tmp_path / "estimate_summary.json").read_text(encoding="utf-8"))}
    line_items = {row["line_item_id"]: row for row in json.loads((tmp_path / "estimate_line_items.json").read_text(encoding="utf-8"))}
    assert report.estimates_reparsed == 1
    assert estimates["old-estimate"]["job_id"] == "OLD"
    assert estimates["changed-estimate"]["estimated_value"] == 2
    assert "old-line" in line_items
    assert "changed-old-line" not in line_items
    assert "changed-new-line" in line_items
