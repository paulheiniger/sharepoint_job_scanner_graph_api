from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .graph_client import GraphClient, SharePointTarget
from .models import JobRecord
from .scan import scan_root, write_csv, write_excel, write_json
from .sharepoint_sync import SyncStats, sync_sharepoint_folder


@dataclass(frozen=True)
class BatchScanRoot:
    folder: str
    division: str | None = None
    pipeline_status: str | None = None
    source_year: int | None = None
    site_url: str | None = None
    library: str | None = None


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


def stats_as_dict(stats: SyncStats) -> dict[str, Any]:
    return asdict(stats)


def write_summary(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


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
    parser.add_argument("--summary", type=Path, default=None, help="Batch scan summary JSON path")
    args = parser.parse_args()

    load_dotenv()
    default_site_url, default_library, roots = load_scan_roots(args.config)
    client = GraphClient()
    records: list[JobRecord] = []
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
            for record in root_records:
                add_batch_context(record, root)
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

    write_csv(records, args.out)
    write_json(records, args.json)
    write_excel(records, args.xlsx)

    summary_path = args.summary or args.json.with_name("batch_scan_summary.json")
    summary = {
        "source": "sharepoint_job_scanner_batch",
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "config": str(args.config),
        "scan_roots": len(roots),
        "roots_completed": len(root_summaries),
        "roots_failed": len(scan_errors),
        "jobs_indexed": len(records),
        "contracted_without_signed_contract_count": contracted_without_signed_contract_count,
        "roots": root_summaries,
        "scan_errors": scan_errors,
        "outputs": {
            "csv": str(args.out),
            "json": str(args.json),
            "xlsx": str(args.xlsx),
        },
    }
    write_summary(summary_path, summary)

    print(f"Scan roots: {len(roots)}")
    print(f"Roots completed: {len(root_summaries)}")
    print(f"Roots failed: {len(scan_errors)}")
    print(f"Jobs indexed: {len(records)}")
    print(f"Contracted without signed contract: {contracted_without_signed_contract_count}")
    print(f"CSV: {args.out}")
    print(f"JSON: {args.json}")
    print(f"Excel: {args.xlsx}")
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
