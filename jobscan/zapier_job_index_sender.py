from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


SOURCE = "sharepoint_job_scanner"
EVENT_TYPE = "job_index_upsert"
DEFAULT_INDEX_PATH = Path("output/job_index.json")
DEFAULT_STATE_PATH = Path(".cache/zapier_job_index_sender_state.json")
TIMEOUT_SECONDS = 30
NON_ACTIONABLE_WARNING_TEXTS = {
    "Folder is Contracted but no signed contract found",
    "Folder is Completed but no signed contract found",
    "Completed job has no signed contract",
}


PAYLOAD_FIELDS = [
    "job_id",
    "division",
    "pipeline_status",
    "status",
    "customer",
    "job_name",
    "job_type",
    "site_address",
    "city",
    "state",
    "zip_code",
    "final_price",
    "invoice_amount",
    "has_invoice",
    "has_signed_contract",
    "has_aerial",
    "photo_count",
    "warnings",
    "folder_url",
    "estimate_file",
    "invoice_file",
]


def load_records(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Job index JSON must contain a list of job records.")
    return [record for record in data if isinstance(record, dict)]


def load_state(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def save_state(path: Path, state: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def warning_text(record: dict[str, Any]) -> str:
    warnings = record.get("warnings")
    if isinstance(warnings, list):
        items = [str(item).strip() for item in warnings]
    else:
        items = [item.strip() for item in str(warnings or "").split(";")]
    return "; ".join(item for item in items if item and item not in NON_ACTIONABLE_WARNING_TEXTS)


def normalize_record(record: dict[str, Any], timestamp: str) -> dict[str, Any]:
    payload = {field: record.get(field) for field in PAYLOAD_FIELDS}
    payload["warnings"] = warning_text(record)
    payload["folder_url"] = record.get("folder_url") or record.get("web_url")
    payload["last_scanned_at"] = timestamp
    payload["source"] = SOURCE
    payload["event_type"] = EVENT_TYPE
    payload["sent_at"] = timestamp
    return payload


def stable_payload_hash(payload: dict[str, Any]) -> str:
    stable = {
        key: value
        for key, value in payload.items()
        if key not in {"last_scanned_at", "sent_at"}
    }
    encoded = json.dumps(stable, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def filter_records(
    records: list[dict[str, Any]],
    *,
    status: str | None = None,
    division: str | None = None,
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], int]:
    selected = records
    if status:
        wanted = status.strip().lower()
        selected = [record for record in selected if str(record.get("status") or "").strip().lower() == wanted]
    if division:
        wanted = division.strip().lower()
        selected = [record for record in selected if str(record.get("division") or "").strip().lower() == wanted]
    if limit is not None:
        selected = selected[:limit]
    return selected, len(records) - len(selected)


def send_payload(webhook_url: str, payload: dict[str, Any]) -> requests.Response:
    response = requests.post(webhook_url, json=payload, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    return response


def main() -> None:
    parser = argparse.ArgumentParser(description="Send normalized job index records to a Zapier SharePoint List upsert webhook.")
    parser.add_argument("json_index", nargs="?", type=Path, default=DEFAULT_INDEX_PATH, help="Job index JSON path")
    parser.add_argument("--dry-run", action="store_true", help="Print payloads but do not send")
    parser.add_argument("--limit", type=int, help="Send only the first N matching records")
    parser.add_argument("--status", help="Send only records matching this scanner status")
    parser.add_argument("--division", help="Send only records matching this division")
    parser.add_argument("--only-changed", action="store_true", help="Send only records whose normalized payload changed since the last successful send")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH, help="Local state file used by --only-changed")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be zero or greater")

    load_dotenv()
    webhook_url = os.getenv("ZAPIER_JOB_INDEX_WEBHOOK_URL")
    if not webhook_url and not args.dry_run:
        raise RuntimeError("Missing ZAPIER_JOB_INDEX_WEBHOOK_URL. Set it in the environment or .env.")

    records = load_records(args.json_index)
    selected, skipped_count = filter_records(
        records,
        status=args.status,
        division=args.division,
        limit=args.limit,
    )
    state = load_state(args.state) if args.only_changed else {}
    new_state = dict(state)

    sent_count = 0
    failed_count = 0
    timestamp = datetime.now(timezone.utc).isoformat()
    for record in selected:
        payload = normalize_record(record, timestamp)
        job_id = str(payload.get("job_id") or "").strip()
        payload_hash = stable_payload_hash(payload)

        if args.only_changed and job_id and state.get(job_id) == payload_hash:
            skipped_count += 1
            continue

        if args.dry_run:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            continue

        try:
            send_payload(str(webhook_url), payload)
            sent_count += 1
            if args.only_changed and job_id:
                new_state[job_id] = payload_hash
        except requests.RequestException as exc:
            failed_count += 1
            print(f"Failed to send {job_id or '<unknown>'}: {exc}")

    if args.only_changed and not args.dry_run:
        save_state(args.state, new_state)

    print(f"Sent: {sent_count}")
    print(f"Skipped: {skipped_count}")
    print(f"Failed: {failed_count}")


if __name__ == "__main__":
    main()
