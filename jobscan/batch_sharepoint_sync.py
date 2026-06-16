from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

from .estimate_datasets import (
    ESTIMATE_LINE_ITEM_FIELDS,
    ESTIMATE_SUMMARY_FIELDS,
    scan_estimate_datasets_for_records,
    write_dataset_csv,
    write_dataset_json,
)
from .graph_client import GraphClient, SharePointTarget
from .job_tracking_extractor import (
    JOB_TRACKING_DAILY_FIELDS,
    JOB_TRACKING_SUMMARY_FIELDS,
    scan_job_tracking_for_records,
)
from .models import JobRecord
from .scan import scan_root, write_csv, write_excel, write_json
from .schedule_extractor import finalize_schedule_record
from .sharepoint_sync import SyncStats, attach_document_urls, attach_folder_urls, is_url, sync_sharepoint_folder


CREW_SCHEDULE_FIELDS = [
    "job_id",
    "division",
    "pipeline_status",
    "status",
    "customer",
    "job_name",
    "job_type",
    "crew_leader",
    "assigned_crew_leader",
    "crew_type",
    "suggested_crew_type",
    "suggested_crew_reason",
    "scheduled_sequence",
    "estimated_start_date",
    "estimated_duration_days",
    "estimated_labor_hours",
    "estimated_hours_per_day",
    "estimated_crew_size",
    "estimated_end_date",
    "labor_duration_source",
    "labor_schedule_breakdown",
    "schedule_status",
    "ready_to_schedule",
    "blocking_issue",
    "schedule_notes",
    "schedule_source_file",
    "schedule_confidence",
    "folder_url",
    "warnings",
]


@dataclass(frozen=True)
class BatchScanRoot:
    folder: str
    division: str | None = None
    pipeline_status: str | None = None
    source_year: int | None = None
    site_url: str | None = None
    library: str | None = None


@dataclass(frozen=True)
class StagedOutput:
    path: Path
    temp_path: Path
    label: str


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("Install pyyaml to use batch SharePoint scanning: pip install pyyaml") from exc

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Batch config must be a YAML mapping.")
    return data


def infer_source_year(folder: str) -> int | None:
    match = re.search(r"\b(20\d{2})\b", folder)
    return int(match.group(1)) if match else None


def coerce_year(value: Any, folder: str) -> int | None:
    if value is None:
        return infer_source_year(folder)
    try:
        return int(value)
    except (TypeError, ValueError):
        return infer_source_year(folder)


def load_scan_roots(path: Path) -> tuple[str, str, list[BatchScanRoot]]:
    config = load_yaml(path)
    sharepoint = config.get("sharepoint") or {}
    if not isinstance(sharepoint, dict):
        raise ValueError("Config field 'sharepoint' must be a mapping.")

    default_site_url = sharepoint.get("site_url")
    default_library = sharepoint.get("library") or "Documents"
    if not default_site_url:
        raise ValueError("Config must set sharepoint.site_url.")

    raw_roots = config.get("scan_roots")
    if not isinstance(raw_roots, list):
        raise ValueError("Config must set scan_roots to a list.")

    roots: list[BatchScanRoot] = []
    for index, raw in enumerate(raw_roots, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"scan_roots[{index}] must be a mapping.")
        folder = raw.get("folder")
        if not folder:
            raise ValueError(f"scan_roots[{index}] must set folder.")
        folder_text = str(folder)
        roots.append(
            BatchScanRoot(
                folder=folder_text,
                division=raw.get("division"),
                pipeline_status=raw.get("pipeline_status"),
                source_year=coerce_year(raw.get("source_year"), folder_text),
                site_url=raw.get("site_url"),
                library=raw.get("library"),
            )
        )
    return str(default_site_url), str(default_library), roots


def add_batch_context(record: JobRecord, root: BatchScanRoot) -> None:
    """Attach config context to every record from a batch root.

    Batch roots are business groupings, not necessarily pipeline-status folders.
    Keep unusual statuses such as "Folder Created" exactly as configured.
    """
    record.division = root.division
    record.pipeline_status = root.pipeline_status
    record.scan_root = root.folder
    record.source_year = root.source_year or infer_source_year(root.folder)
    if root.pipeline_status and not record.estimate_file:
        record.status = root.pipeline_status

    status = (root.pipeline_status or "").strip().lower()
    if status == "completed":
        if not record.has_invoice:
            record.warnings.append("Folder is Completed but no invoice found")
        if record.final_price is None:
            record.warnings.append("Folder is Completed but no final price found")
    finalize_schedule_record(record)


def stats_as_dict(stats: SyncStats) -> dict[str, Any]:
    return asdict(stats)


def write_summary(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


def timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def temp_output_path(path: Path, run_id: str) -> Path:
    return path.with_name(f".{path.stem}.{run_id}.{os.getpid()}.tmp{path.suffix}")


def stage_output(path: Path, label: str, writer: Callable[[Path], None], run_id: str) -> StagedOutput:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = temp_output_path(path, run_id)
    if temp_path.exists():
        temp_path.unlink()
    writer(temp_path)
    return StagedOutput(path=path, temp_path=temp_path, label=label)


def cleanup_staged_outputs(staged_outputs: list[StagedOutput]) -> None:
    for staged in staged_outputs:
        try:
            if staged.temp_path.exists():
                staged.temp_path.unlink()
        except OSError:
            pass


def replace_staged_outputs(staged_outputs: list[StagedOutput]) -> None:
    for staged in staged_outputs:
        staged.path.parent.mkdir(parents=True, exist_ok=True)
        staged.temp_path.replace(staged.path)


def backup_existing_file(path: Path, run_id: str) -> Path | None:
    if not path.exists():
        return None
    backup_path = path.with_name(f"{path.name}.{run_id}.bak")
    shutil.copy2(path, backup_path)
    return backup_path


def json_row_count(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return len(data) if isinstance(data, list) else None


def append_record_warning(record: JobRecord, warning: str) -> None:
    if warning not in record.warnings:
        record.warnings.append(warning)


def attach_estimate_detail_warnings(records: list[JobRecord], summaries: list[dict[str, Any]]) -> None:
    records_by_job_id = {record.job_id: record for record in records}
    for summary in summaries:
        warnings = str(summary.get("extraction_warnings") or "")
        if "detail extraction failed" not in warnings.lower():
            continue
        job_id = summary.get("job_id")
        record = records_by_job_id.get(job_id)
        if record:
            append_record_warning(record, "Estimate detail extraction failed")


def job_index_overwrite_blockers(
    *,
    new_rows: int,
    previous_rows: int | None,
    scan_roots_failed: int,
    existing_job_index: bool,
    allow_shrink: bool,
    allow_partial: bool,
) -> list[str]:
    blockers: list[str] = []
    if previous_rows is not None and new_rows < previous_rows * 0.8 and not allow_shrink:
        blockers.append("shrink")
    if scan_roots_failed and existing_job_index and not allow_partial:
        blockers.append("partial")
    return blockers


def crew_schedule_rows(records: list[JobRecord], *, tabular: bool = False) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        row = record.to_dict()
        row["warnings"] = "; ".join(row.get("warnings") or [])
        if tabular and isinstance(row.get("labor_schedule_breakdown"), list):
            row["labor_schedule_breakdown"] = json.dumps(row["labor_schedule_breakdown"], ensure_ascii=False)
        rows.append({field: row.get(field) for field in CREW_SCHEDULE_FIELDS})
    return rows


def write_crew_schedule_csv(records: list[JobRecord], path: Path) -> None:
    rows = crew_schedule_rows(records, tabular=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CREW_SCHEDULE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_crew_schedule_json(records: list[JobRecord], path: Path) -> None:
    rows = crew_schedule_rows(records)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")


def print_root_start(root: BatchScanRoot, site_url: str, library: str) -> None:
    print(f"Scanning root: {root.folder}")
    print(f"  site_url: {site_url}")
    print(f"  library: {library}")
    print(f"  division: {root.division or ''}")
    print(f"  pipeline_status: {root.pipeline_status or ''}")


def print_root_done(root: BatchScanRoot, records_found: int) -> None:
    print(f"  records_found: {records_found}")
    if records_found == 0:
        print("  WARNING: Scan root found but no records extracted")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Batch sync multiple SharePoint roots and build one job index.")
    parser.add_argument("--config", type=Path, default=Path("config/sharepoint_scan_roots.yaml"), help="Batch scan YAML config")
    parser.add_argument("--cache", type=Path, default=Path(".cache/sharepoint"), help="Local cache folder")
    parser.add_argument("--max-depth", type=int, default=4, help="Recursive folder depth")
    parser.add_argument("--max-file-mb", type=float, default=50, help="Skip files larger than this")
    parser.add_argument("--force", action="store_true", help="Redownload even when eTag has not changed")
    image_group = parser.add_mutually_exclusive_group()
    image_group.add_argument("--skip-images", dest="skip_images", action="store_true", default=True, help="Skip image downloads and write image manifests. Default: true")
    image_group.add_argument("--include-images", dest="skip_images", action="store_false", help="Download image files for duplicate detection or image analysis")
    parser.add_argument("--out", type=Path, default=Path("output/job_index.csv"))
    parser.add_argument("--json", type=Path, default=Path("output/job_index.json"))
    parser.add_argument("--xlsx", type=Path, default=Path("output/job_index.xlsx"))
    parser.add_argument("--crew-schedule-out", type=Path, default=Path("output/crew_schedule_candidates.csv"))
    parser.add_argument("--crew-schedule-json", type=Path, default=Path("output/crew_schedule_candidates.json"))
    parser.add_argument("--estimate-summary-out", type=Path, default=Path("output/estimate_summary.csv"))
    parser.add_argument("--estimate-summary-json", type=Path, default=Path("output/estimate_summary.json"))
    parser.add_argument("--estimate-line-items-out", type=Path, default=Path("output/estimate_line_items.csv"))
    parser.add_argument("--estimate-line-items-json", type=Path, default=Path("output/estimate_line_items.json"))
    parser.add_argument("--job-tracking-summary-out", type=Path, default=Path("output/job_tracking_summary.csv"))
    parser.add_argument("--job-tracking-summary-json", type=Path, default=Path("output/job_tracking_summary.json"))
    parser.add_argument("--job-tracking-daily-out", type=Path, default=Path("output/job_tracking_daily_entries.csv"))
    parser.add_argument("--job-tracking-daily-json", type=Path, default=Path("output/job_tracking_daily_entries.json"))
    parser.add_argument("--summary", type=Path, default=None, help="Batch scan summary JSON path")
    parser.add_argument("--allow-shrink", action="store_true", help="Allow overwriting an existing job index when the new row count is less than 80%% of the previous row count")
    parser.add_argument("--allow-partial", action="store_true", help="Allow overwriting an existing job index even when one or more scan roots failed")
    parser.add_argument("--sync-job-index-list", action="store_true", help="After successful output replacement, upsert output/job_index.json into the SharePoint Job Index list")
    parser.add_argument("--job-index-list-site-url", default=os.getenv("SHAREPOINT_JOB_INDEX_SITE_URL") or "https://aro365531128.sharepoint.com/sites/Data")
    parser.add_argument("--job-index-list-name", default=os.getenv("SHAREPOINT_JOB_INDEX_LIST_NAME") or "Job Index")
    args = parser.parse_args()

    run_id = timestamp_slug()
    default_site_url, default_library, roots = load_scan_roots(args.config)
    client = GraphClient()
    records: list[JobRecord] = []
    estimate_summaries: list[dict[str, Any]] = []
    estimate_line_items: list[dict[str, Any]] = []
    job_tracking_summaries: list[dict[str, Any]] = []
    job_tracking_daily_entries: list[dict[str, Any]] = []
    scan_errors: list[dict[str, Any]] = []
    root_summaries: list[dict[str, Any]] = []
    contracted_without_signed_contract_count = 0

    for root in roots:
        site_url = root.site_url or default_site_url
        library = root.library or default_library
        print_root_start(root, site_url, library)
        try:
            target = SharePointTarget.from_url(site_url, library=library, folder_path=root.folder)
            cache_root, stats = sync_sharepoint_folder(
                client=client,
                target=target,
                cache_dir=args.cache,
                max_depth=args.max_depth,
                max_file_mb=args.max_file_mb,
                force=args.force,
                skip_images=args.skip_images,
            )
            root_records = scan_root(cache_root, scan_context=root.folder)
            attach_folder_urls(root_records, cache_root)
            attach_document_urls(root_records, cache_root)
            for record in root_records:
                add_batch_context(record, root)
            root_estimate_summaries: list[dict[str, Any]] = []
            root_estimate_line_items: list[dict[str, Any]] = []
            estimate_extraction_error = None
            try:
                root_estimate_summaries, root_estimate_line_items = scan_estimate_datasets_for_records(cache_root, root_records)
                attach_estimate_detail_warnings(root_records, root_estimate_summaries)
            except Exception as exc:
                estimate_extraction_error = f"{type(exc).__name__}: {exc}"
                print(f"  WARNING: Estimate detail extraction failed: {estimate_extraction_error}")
                for record in root_records:
                    append_record_warning(record, "Estimate detail extraction failed")
            estimate_summaries.extend(root_estimate_summaries)
            estimate_line_items.extend(root_estimate_line_items)
            root_tracking_summaries, root_tracking_daily_entries = scan_job_tracking_for_records(cache_root, root_records)
            job_tracking_summaries.extend(root_tracking_summaries)
            job_tracking_daily_entries.extend(root_tracking_daily_entries)
            print_root_done(root, len(root_records))
            if (root.pipeline_status or "").strip().lower() == "contracted":
                contracted_without_signed_contract_count += sum(
                    1 for record in root_records if not record.has_signed_contract
                )
            records.extend(root_records)
            root_summaries.append(
                {
                    "folder": root.folder,
                    "division": root.division,
                    "pipeline_status": root.pipeline_status,
                    "source_year": root.source_year,
                    "cache_root": str(cache_root),
                    "records_found": len(root_records),
                    "records": len(root_records),
                    "estimate_summaries": len(root_estimate_summaries),
                    "estimate_line_items": len(root_estimate_line_items),
                    "estimate_extraction_error": estimate_extraction_error,
                    "job_tracking_summaries": len(root_tracking_summaries),
                    "job_tracking_daily_entries": len(root_tracking_daily_entries),
                    "warning": "Scan root found but no records extracted" if not root_records else None,
                    "stats": stats_as_dict(stats),
                }
            )
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            print(f"Failed scanning {root.folder}: {message}")
            scan_errors.append(
                {
                    "folder": root.folder,
                    "division": root.division,
                    "pipeline_status": root.pipeline_status,
                    "source_year": root.source_year,
                    "error": message,
                }
            )

    summary_path = args.summary or args.json.with_name("batch_scan_summary.json")
    summary = {
        "source": "sharepoint_job_scanner_batch",
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "config": str(args.config),
        "scan_roots": len(roots),
        "roots_completed": len(root_summaries),
        "roots_failed": len(scan_errors),
        "jobs_indexed": len(records),
        "estimate_summaries": len(estimate_summaries),
        "estimate_line_items": len(estimate_line_items),
        "job_tracking_summaries": len(job_tracking_summaries),
        "job_tracking_daily_entries": len(job_tracking_daily_entries),
        "contracted_without_signed_contract_count": contracted_without_signed_contract_count,
        "roots": root_summaries,
        "scan_errors": scan_errors,
        "outputs": {
            "csv": str(args.out),
            "json": str(args.json),
            "xlsx": str(args.xlsx),
            "crew_schedule_csv": str(args.crew_schedule_out),
            "crew_schedule_json": str(args.crew_schedule_json),
            "estimate_summary_csv": str(args.estimate_summary_out),
            "estimate_summary_json": str(args.estimate_summary_json),
            "estimate_line_items_csv": str(args.estimate_line_items_out),
            "estimate_line_items_json": str(args.estimate_line_items_json),
            "job_tracking_summary_csv": str(args.job_tracking_summary_out),
            "job_tracking_summary_json": str(args.job_tracking_summary_json),
            "job_tracking_daily_csv": str(args.job_tracking_daily_out),
            "job_tracking_daily_json": str(args.job_tracking_daily_json),
        },
    }

    previous_job_index_rows = json_row_count(args.json)
    blockers = job_index_overwrite_blockers(
        new_rows=len(records),
        previous_rows=previous_job_index_rows,
        scan_roots_failed=len(scan_errors),
        existing_job_index=args.json.exists(),
        allow_shrink=args.allow_shrink,
        allow_partial=args.allow_partial,
    )

    if "shrink" in blockers:
        print(
            f"WARNING: new job index has {len(records)} rows, previous had {previous_job_index_rows} rows. "
            "Use --allow-shrink to overwrite."
        )
    if "partial" in blockers:
        print(
            f"WARNING: {len(scan_errors)} scan root(s) failed. Use --allow-partial to overwrite existing job_index outputs."
        )

    staged_outputs: list[StagedOutput] = []
    try:
        staged_outputs.extend(
            [
                stage_output(args.out, "job index CSV", lambda path: write_csv(records, path), run_id),
                stage_output(args.json, "job index JSON", lambda path: write_json(records, path), run_id),
                stage_output(args.xlsx, "job index XLSX", lambda path: write_excel(records, path), run_id),
                stage_output(args.crew_schedule_out, "crew schedule CSV", lambda path: write_crew_schedule_csv(records, path), run_id),
                stage_output(args.crew_schedule_json, "crew schedule JSON", lambda path: write_crew_schedule_json(records, path), run_id),
                stage_output(args.estimate_summary_out, "estimate summary CSV", lambda path: write_dataset_csv(estimate_summaries, ESTIMATE_SUMMARY_FIELDS, path), run_id),
                stage_output(args.estimate_summary_json, "estimate summary JSON", lambda path: write_dataset_json(estimate_summaries, ESTIMATE_SUMMARY_FIELDS, path), run_id),
                stage_output(args.estimate_line_items_out, "estimate line items CSV", lambda path: write_dataset_csv(estimate_line_items, ESTIMATE_LINE_ITEM_FIELDS, path), run_id),
                stage_output(args.estimate_line_items_json, "estimate line items JSON", lambda path: write_dataset_json(estimate_line_items, ESTIMATE_LINE_ITEM_FIELDS, path), run_id),
                stage_output(args.job_tracking_summary_out, "job tracking summary CSV", lambda path: write_dataset_csv(job_tracking_summaries, JOB_TRACKING_SUMMARY_FIELDS, path), run_id),
                stage_output(args.job_tracking_summary_json, "job tracking summary JSON", lambda path: write_dataset_json(job_tracking_summaries, JOB_TRACKING_SUMMARY_FIELDS, path), run_id),
                stage_output(args.job_tracking_daily_out, "job tracking daily CSV", lambda path: write_dataset_csv(job_tracking_daily_entries, JOB_TRACKING_DAILY_FIELDS, path), run_id),
                stage_output(args.job_tracking_daily_json, "job tracking daily JSON", lambda path: write_dataset_json(job_tracking_daily_entries, JOB_TRACKING_DAILY_FIELDS, path), run_id),
                stage_output(summary_path, "batch summary JSON", lambda path: write_summary(path, summary), run_id),
            ]
        )
        if blockers:
            cleanup_staged_outputs(staged_outputs)
            print("Outputs not replaced.")
        if not blockers:
            backup_path = backup_existing_file(args.json, run_id)
            replace_staged_outputs(staged_outputs)
            if backup_path:
                print(f"Job index JSON backup: {backup_path}")
    except Exception:
        cleanup_staged_outputs(staged_outputs)
        raise

    if args.sync_job_index_list and not blockers:
        from .sharepoint_list_sync import parse_args as parse_list_sync_args
        from .sharepoint_list_sync import run as run_list_sync

        print("Syncing Job Index SharePoint List...")
        run_list_sync(
            parse_list_sync_args(
                [
                    "--input",
                    str(args.json),
                    "--site-url",
                    args.job_index_list_site_url,
                    "--list-name",
                    args.job_index_list_name,
                ]
            )
        )
    elif args.sync_job_index_list and blockers:
        print("SharePoint Job Index List sync skipped because job_index outputs were not replaced.")

    print(f"Scan roots: {len(roots)}")
    print(f"Roots completed: {len(root_summaries)}")
    print(f"Roots failed: {len(scan_errors)}")
    print(f"Jobs indexed: {len(records)}")
    print(f"Estimate summaries: {len(estimate_summaries)}")
    print(f"Estimate line items: {len(estimate_line_items)}")
    print(f"job_index_rows: {len(records)}")
    print(f"estimate_summary_rows: {len(estimate_summaries)}")
    print(f"estimate_line_item_rows: {len(estimate_line_items)}")
    print(f"scan_roots_completed: {len(root_summaries)}")
    print(f"scan_roots_failed: {len(scan_errors)}")
    jobs_with_folder_url = sum(1 for record in records if is_url(record.folder_url))
    print(f"Jobs with folder_url: {jobs_with_folder_url}")
    print(f"Jobs missing folder_url: {len(records) - jobs_with_folder_url}")
    print(f"Job tracking summaries: {len(job_tracking_summaries)}")
    print(f"Job tracking daily entries: {len(job_tracking_daily_entries)}")
    print(f"Contracted without signed contract: {contracted_without_signed_contract_count}")
    print(f"CSV: {args.out}")
    print(f"JSON: {args.json}")
    print(f"Excel: {args.xlsx}")
    print(f"Crew schedule CSV: {args.crew_schedule_out}")
    print(f"Crew schedule JSON: {args.crew_schedule_json}")
    print(f"Estimate summary CSV: {args.estimate_summary_out}")
    print(f"Estimate summary JSON: {args.estimate_summary_json}")
    print(f"Estimate line items CSV: {args.estimate_line_items_out}")
    print(f"Estimate line items JSON: {args.estimate_line_items_json}")
    print(f"Job tracking summary CSV: {args.job_tracking_summary_out}")
    print(f"Job tracking summary JSON: {args.job_tracking_summary_json}")
    print(f"Job tracking daily CSV: {args.job_tracking_daily_out}")
    print(f"Job tracking daily JSON: {args.job_tracking_daily_json}")
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
