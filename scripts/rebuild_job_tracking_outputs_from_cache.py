from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import fields
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from jobscan.job_tracking_extractor import (
    JOB_TRACKING_DAILY_FIELDS,
    JOB_TRACKING_SUMMARY_FIELDS,
    scan_job_tracking_for_records,
)
from jobscan.models import JobRecord


def load_json_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, list):
        raise ValueError(f"{path} must contain a JSON list")
    return [row for row in payload if isinstance(row, dict)]


def cache_root_for_scan_root(cache_dir: Path, scan_root: str) -> Path:
    return cache_dir / "Data" / scan_root.replace("/", "_")


def job_record_from_row(row: dict[str, Any]) -> JobRecord:
    allowed = {field.name for field in fields(JobRecord)}
    values = {key: value for key, value in row.items() if key in allowed}
    values.setdefault("job_id", "")
    values.setdefault("folder_name", values.get("folder_path") or "")
    values.setdefault("folder_path", values.get("folder_name") or "")
    return JobRecord(**values)


def write_json(rows: list[dict[str, Any]], fields_: list[str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = [{field: row.get(field) for field in fields_ if field in row} for row in rows]
    path.write_text(json.dumps(normalized, indent=2, default=str), encoding="utf-8")


def write_csv(rows: list[dict[str, Any]], fields_: list[str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields_)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields_})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild job tracking output files from the local SharePoint cache.")
    parser.add_argument("--job-index", type=Path, default=Path("output/job_index.json"))
    parser.add_argument("--cache", type=Path, default=Path(".cache/sharepoint"))
    parser.add_argument("--out-dir", type=Path, default=Path("output"))
    parser.add_argument("--scan-root", action="append", default=[], help="Optional scan_root value to rebuild. May be repeated.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = load_json_rows(args.job_index)
    selected_roots = {root.strip() for root in args.scan_root if root and root.strip()}
    records_by_root: dict[str, list[JobRecord]] = {}
    skipped_without_root = 0
    for row in rows:
        scan_root = str(row.get("scan_root") or "").strip()
        if not scan_root:
            skipped_without_root += 1
            continue
        if selected_roots and scan_root not in selected_roots:
            continue
        records_by_root.setdefault(scan_root, []).append(job_record_from_row(row))

    summaries: list[dict[str, Any]] = []
    daily_entries: list[dict[str, Any]] = []
    missing_cache_roots: list[str] = []
    for scan_root, records in sorted(records_by_root.items()):
        cache_root = cache_root_for_scan_root(args.cache, scan_root)
        if not cache_root.exists():
            missing_cache_roots.append(f"{scan_root} -> {cache_root}")
            continue
        root_summaries, root_daily = scan_job_tracking_for_records(cache_root, records)
        summaries.extend(root_summaries)
        daily_entries.extend(root_daily)
        print(
            f"{scan_root}: records={len(records)} tracking_summaries={len(root_summaries)} "
            f"daily_entries={len(root_daily)}"
        )

    write_json(summaries, JOB_TRACKING_SUMMARY_FIELDS, args.out_dir / "job_tracking_summary.json")
    write_json(daily_entries, JOB_TRACKING_DAILY_FIELDS, args.out_dir / "job_tracking_daily_entries.json")
    write_csv(summaries, JOB_TRACKING_SUMMARY_FIELDS, args.out_dir / "job_tracking_summary.csv")
    write_csv(daily_entries, JOB_TRACKING_DAILY_FIELDS, args.out_dir / "job_tracking_daily_entries.csv")

    print(f"Rows from job index: {len(rows)}")
    print(f"Scan roots rebuilt: {len(records_by_root)}")
    print(f"Skipped rows without scan_root: {skipped_without_root}")
    print(f"Missing cache roots: {len(missing_cache_roots)}")
    for item in missing_cache_roots[:10]:
        print(f"  missing: {item}")
    if len(missing_cache_roots) > 10:
        print(f"  ... {len(missing_cache_roots) - 10} more")
    print(f"Job tracking summaries: {len(summaries)}")
    print(f"Job tracking daily entries: {len(daily_entries)}")


if __name__ == "__main__":
    main()
