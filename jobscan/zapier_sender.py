from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


SOURCE = "sharepoint_job_scanner"
DEFAULT_INDEX_PATH = Path("output/job_index.json")
TIMEOUT_SECONDS = 30


def load_records(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Job index JSON must contain a list of job records.")
    return [record for record in data if isinstance(record, dict)]


def has_warnings(record: dict[str, Any]) -> bool:
    warnings = record.get("warnings")
    if isinstance(warnings, list):
        return any(str(item).strip() for item in warnings)
    return bool(str(warnings or "").strip())


def filter_records(
    records: list[dict[str, Any]],
    *,
    only_warnings: bool = False,
    status: str | None = None,
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], int]:
    selected = records
    if only_warnings:
        selected = [record for record in selected if has_warnings(record)]
    if status:
        wanted = status.strip().lower()
        selected = [record for record in selected if str(record.get("status") or "").strip().lower() == wanted]
    if limit is not None:
        selected = selected[:limit]
    return selected, len(records) - len(selected)


def build_payload(record: dict[str, Any]) -> dict[str, Any]:
    payload = dict(record)
    payload["source"] = SOURCE
    payload["sent_at"] = datetime.now(timezone.utc).isoformat()
    return payload


def send_payload(webhook_url: str, payload: dict[str, Any]) -> requests.Response:
    response = requests.post(webhook_url, json=payload, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    return response


def main() -> None:
    parser = argparse.ArgumentParser(description="Send job index records to a Zapier Catch Hook webhook.")
    parser.add_argument("json_index", nargs="?", type=Path, default=DEFAULT_INDEX_PATH, help="Job index JSON path")
    parser.add_argument("--dry-run", action="store_true", help="Print payloads but do not send")
    parser.add_argument("--only-warnings", action="store_true", help="Send only records with warnings")
    parser.add_argument("--status", help="Send only records matching this status")
    parser.add_argument("--limit", type=int, help="Send only the first N matching records")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be zero or greater")

    load_dotenv()
    webhook_url = os.getenv("ZAPIER_WEBHOOK_URL")
    if not webhook_url and not args.dry_run:
        raise RuntimeError("Missing ZAPIER_WEBHOOK_URL. Set it in the environment or .env.")

    records = load_records(args.json_index)
    selected, skipped_count = filter_records(
        records,
        only_warnings=args.only_warnings,
        status=args.status,
        limit=args.limit,
    )

    sent_count = 0
    failed_count = 0
    for record in selected:
        payload = build_payload(record)
        if args.dry_run:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            continue

        try:
            send_payload(str(webhook_url), payload)
            sent_count += 1
        except requests.RequestException as exc:
            failed_count += 1
            job_id = record.get("job_id") or record.get("folder_name") or "<unknown>"
            print(f"Failed to send {job_id}: {exc}")

    print(f"Sent: {sent_count}")
    print(f"Skipped: {skipped_count}")
    print(f"Failed: {failed_count}")


if __name__ == "__main__":
    main()
