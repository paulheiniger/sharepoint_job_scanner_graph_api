from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


SOURCE = "sharepoint_job_scanner"
SUMMARY_TYPE = "daily_job_summary"
DEFAULT_INDEX_PATH = Path("output/job_index.json")
TIMEOUT_SECONDS = 30
NON_ACTIONABLE_WARNING_TEXTS = {
    "Folder is Contracted but no signed contract found",
    "Folder is Completed but no signed contract found",
    "Completed job has no signed contract",
}


def load_records(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Job index JSON must contain a list of job records.")
    return [record for record in data if isinstance(record, dict)]


def number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("$", "").replace(",", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def warning_items(record: dict[str, Any]) -> list[str]:
    warnings = record.get("warnings")
    if isinstance(warnings, list):
        items = [str(item).strip() for item in warnings]
    else:
        items = [item.strip() for item in str(warnings or "").split(";")]
    return [item for item in items if item and item not in NON_ACTIONABLE_WARNING_TEXTS]


def warning_text(record: dict[str, Any]) -> str:
    return "; ".join(warning_items(record))


def has_warning(record: dict[str, Any]) -> bool:
    return bool(warning_text(record))


def label(value: Any) -> str:
    text = str(value or "").strip()
    return text or "Unknown"


def pipeline_status_matches(record: dict[str, Any], expected: str) -> bool:
    return str(record.get("pipeline_status") or "").strip().lower() == expected


def is_contracted_pipeline(record: dict[str, Any]) -> bool:
    return str(record.get("pipeline_status") or "").strip().lower().startswith("contracted")


def job_identity(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "customer": record.get("customer"),
        "job_name": record.get("job_name"),
        "division": record.get("division"),
        "pipeline_status": record.get("pipeline_status"),
        "final_price": record.get("final_price"),
    }


def build_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    total_by_division: dict[str, float] = defaultdict(float)
    count_by_pipeline_status: Counter[str] = Counter()
    count_by_status: Counter[str] = Counter()

    total_final_price = 0.0
    total_photo_count = 0
    warning_jobs: list[dict[str, Any]] = []

    for record in records:
        division = label(record.get("division"))
        total_by_division[division] += 0.0
        final_price = number(record.get("final_price"))
        if final_price is not None:
            total_final_price += final_price
            total_by_division[division] += final_price

        count_by_pipeline_status[label(record.get("pipeline_status"))] += 1
        count_by_status[label(record.get("status"))] += 1
        total_photo_count += int(number(record.get("photo_count")) or 0)

        if has_warning(record):
            warning_entry = job_identity(record)
            warning_entry["warnings"] = warning_text(record)
            warning_jobs.append(warning_entry)

    highest_value_jobs = sorted(
        (record for record in records if number(record.get("final_price")) is not None),
        key=lambda record: number(record.get("final_price")) or 0,
        reverse=True,
    )[:10]
    warning_jobs = sorted(
        warning_jobs,
        key=lambda record: number(record.get("final_price")) or 0,
        reverse=True,
    )[:10]

    return {
        "source": SOURCE,
        "summary_type": SUMMARY_TYPE,
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "total_jobs": len(records),
        "total_final_price": round(total_final_price, 2),
        "total_by_division": {key: round(value, 2) for key, value in sorted(total_by_division.items())},
        "count_by_pipeline_status": dict(sorted(count_by_pipeline_status.items())),
        "count_by_status": dict(sorted(count_by_status.items())),
        "warning_count": len([record for record in records if has_warning(record)]),
        "completed_missing_invoice_count": sum(
            1 for record in records if pipeline_status_matches(record, "completed") and not record.get("has_invoice")
        ),
        "completed_missing_final_price_count": sum(
            1 for record in records if pipeline_status_matches(record, "completed") and number(record.get("final_price")) is None
        ),
        "jobs_with_aerial_count": sum(1 for record in records if bool(record.get("has_aerial"))),
        "total_photo_count": total_photo_count,
        "proposed_count": sum(1 for record in records if pipeline_status_matches(record, "proposed")),
        "contracted_count": sum(1 for record in records if is_contracted_pipeline(record)),
        "completed_count": sum(1 for record in records if pipeline_status_matches(record, "completed")),
        "top_warning_jobs": warning_jobs,
        "top_highest_value_jobs": [job_identity(record) for record in highest_value_jobs],
    }


def send_summary(webhook_url: str, payload: dict[str, Any]) -> requests.Response:
    response = requests.post(webhook_url, json=payload, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    return response


def main() -> None:
    parser = argparse.ArgumentParser(description="Send one daily SharePoint job scan summary to Zapier.")
    parser.add_argument("json_index", nargs="?", type=Path, default=DEFAULT_INDEX_PATH, help="Job index JSON path")
    parser.add_argument("--dry-run", action="store_true", help="Print the summary payload but do not send")
    args = parser.parse_args()

    load_dotenv()
    webhook_url = os.getenv("ZAPIER_DAILY_SUMMARY_WEBHOOK_URL")
    if not webhook_url and not args.dry_run:
        raise RuntimeError("Missing ZAPIER_DAILY_SUMMARY_WEBHOOK_URL. Set it in the environment or .env.")

    payload = build_summary(load_records(args.json_index))
    if args.dry_run:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    send_summary(str(webhook_url), payload)
    print("Sent daily summary: 1")


if __name__ == "__main__":
    main()
