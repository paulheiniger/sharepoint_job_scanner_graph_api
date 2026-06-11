from __future__ import annotations

import argparse
import html
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

from .models import get_estimated_value, get_estimated_value_info


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


def money_text(value: Any) -> str:
    amount = number(value)
    return f"${amount:,.2f}" if amount is not None else "No estimated value"


def html_text(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def pipeline_status_matches(record: dict[str, Any], expected: str) -> bool:
    return str(record.get("pipeline_status") or "").strip().lower() == expected


def is_contracted_pipeline(record: dict[str, Any]) -> bool:
    return str(record.get("pipeline_status") or "").strip().lower().startswith("contracted")


def job_identity(record: dict[str, Any]) -> dict[str, Any]:
    estimated_value, estimated_value_source = get_estimated_value_info(record)
    return {
        "customer": record.get("customer"),
        "job_name": record.get("job_name"),
        "division": record.get("division"),
        "pipeline_status": record.get("pipeline_status"),
        "final_price": record.get("final_price"),
        "worksheet_price": record.get("worksheet_price"),
        "total_job_cost": record.get("total_job_cost"),
        "estimated_value": estimated_value,
        "estimated_value_source": estimated_value_source,
    }


def job_title(record: dict[str, Any]) -> str:
    return label(record.get("job_name") or record.get("customer") or record.get("folder_name"))


def division_summary_text(total_by_division: dict[str, float]) -> str:
    if not total_by_division:
        return "No divisions found"
    return "\n".join(
        f"{division}: ${amount:,.2f}"
        for division, amount in sorted(total_by_division.items())
    )


def value_summary_text(values: dict[str, float], empty_text: str) -> str:
    if not values:
        return empty_text
    return "\n".join(f"{label_text}: ${amount:,.2f}" for label_text, amount in sorted(values.items()))


def counter_summary_text(counter: Counter[str]) -> str:
    if not counter:
        return "No pipeline statuses found"
    return "\n".join(f"{status}: {count}" for status, count in sorted(counter.items()))


def warning_jobs_text(jobs: list[dict[str, Any]]) -> str:
    if not jobs:
        return "No warning jobs"
    return "\n".join(
        f"{index}. {job_title(job)} ({label(job.get('division'))}, {label(job.get('pipeline_status'))}) - {money_text(job.get('estimated_value'))}: {job.get('warnings')}"
        for index, job in enumerate(jobs, start=1)
    )


def top_value_jobs_text(jobs: list[dict[str, Any]]) -> str:
    if not jobs:
        return "No jobs with estimated values"
    return "\n".join(
        f"{index}. {job_title(job)} ({label(job.get('division'))}, {label(job.get('pipeline_status'))}) - {money_text(job.get('estimated_value'))}"
        for index, job in enumerate(jobs, start=1)
    )


def lines_to_html(value: str) -> str:
    return "<br>".join(html_text(line) for line in value.splitlines())


def division_summary_html(total_by_division: dict[str, float]) -> str:
    return lines_to_html(division_summary_text(total_by_division))


def pipeline_summary_html(counter: Counter[str]) -> str:
    return lines_to_html(counter_summary_text(counter))


def pipeline_value_summary_html(values: dict[str, float]) -> str:
    return lines_to_html(value_summary_text(values, "No pipeline estimated values found"))


def warning_jobs_html(jobs: list[dict[str, Any]]) -> str:
    if not jobs:
        return "No warning jobs"
    return "<br>".join(
        f"{index}. {html_text(job_title(job))} ({html_text(label(job.get('division')))}, {html_text(label(job.get('pipeline_status')))}) - {html_text(money_text(job.get('estimated_value')))}: {html_text(job.get('warnings'))}"
        for index, job in enumerate(jobs, start=1)
    )


def top_value_jobs_html(jobs: list[dict[str, Any]]) -> str:
    if not jobs:
        return "No jobs with estimated values"
    return "<br>".join(
        f"{index}. {html_text(job_title(job))} ({html_text(label(job.get('division')))}, {html_text(label(job.get('pipeline_status')))}) - {html_text(money_text(job.get('estimated_value')))}"
        for index, job in enumerate(jobs, start=1)
    )


def teams_message_html(payload: dict[str, Any]) -> str:
    quality_items = [
        f"<li><strong>Warnings:</strong> {payload['warning_count']}</li>",
        f"<li><strong>Completed missing invoice:</strong> {payload['completed_missing_invoice_count']}</li>",
        f"<li><strong>Completed missing final price:</strong> {payload['completed_missing_final_price_count']}</li>",
        f"<li><strong>Jobs with aerials:</strong> {payload['jobs_with_aerial_count']}</li>",
        f"<li><strong>Total photos:</strong> {payload['total_photo_count']}</li>",
    ]
    return (
        "<h2>Daily SharePoint Job Scan Summary</h2>"
        f"<p><strong>Total jobs:</strong> {payload['total_jobs']}<br>"
        f"<strong>Total Estimated Value:</strong> {html_text(money_text(payload['total_estimated_value']))}<br>"
        f"<strong>Proposed:</strong> {payload['proposed_count']} &nbsp; "
        f"<strong>Contracted:</strong> {payload['contracted_count']} &nbsp; "
        f"<strong>Completed:</strong> {payload['completed_count']}</p>"
        "<h3>Division Summary</h3>"
        f"<p>{payload['division_summary_html']}</p>"
        "<h3>Pipeline Summary</h3>"
        f"<p>{payload['pipeline_summary_html']}</p>"
        "<h3>Pipeline Estimated Value</h3>"
        f"<p>{payload['pipeline_value_summary_html']}</p>"
        "<h3>Quality Checks</h3>"
        f"<ul>{''.join(quality_items)}</ul>"
        "<h3>Top Warning Jobs</h3>"
        f"<p>{payload['warning_jobs_html']}</p>"
        "<h3>Top Value Jobs</h3>"
        f"<p>{payload['top_value_jobs_html']}</p>"
    )


def build_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    total_by_division: dict[str, float] = defaultdict(float)
    total_by_pipeline_status: dict[str, float] = defaultdict(float)
    count_by_pipeline_status: Counter[str] = Counter()
    count_by_status: Counter[str] = Counter()

    total_estimated_value = 0.0
    total_photo_count = 0
    warning_jobs: list[dict[str, Any]] = []

    for record in records:
        division = label(record.get("division"))
        pipeline_status = label(record.get("pipeline_status"))
        total_by_division[division] += 0.0
        total_by_pipeline_status[pipeline_status] += 0.0
        estimated_value = get_estimated_value(record)
        if estimated_value is not None:
            total_estimated_value += estimated_value
            total_by_division[division] += estimated_value
            total_by_pipeline_status[pipeline_status] += estimated_value

        count_by_pipeline_status[pipeline_status] += 1
        count_by_status[label(record.get("status"))] += 1
        total_photo_count += int(number(record.get("photo_count")) or 0)

        if has_warning(record):
            warning_entry = job_identity(record)
            warning_entry["warnings"] = warning_text(record)
            warning_jobs.append(warning_entry)

    highest_value_jobs = sorted(
        (record for record in records if get_estimated_value(record) is not None),
        key=lambda record: get_estimated_value(record) or 0,
        reverse=True,
    )[:10]
    warning_jobs = sorted(
        warning_jobs,
        key=lambda record: get_estimated_value(record) or 0,
        reverse=True,
    )[:10]
    total_by_division_out = {key: round(value, 2) for key, value in sorted(total_by_division.items())}
    total_by_pipeline_status_out = {key: round(value, 2) for key, value in sorted(total_by_pipeline_status.items())}
    count_by_pipeline_status_out = dict(sorted(count_by_pipeline_status.items()))
    top_highest_value_jobs = [job_identity(record) for record in highest_value_jobs]

    payload = {
        "source": SOURCE,
        "summary_type": SUMMARY_TYPE,
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "total_jobs": len(records),
        "total_estimated_value": round(total_estimated_value, 2),
        "total_final_price": round(total_estimated_value, 2),
        "total_by_division": total_by_division_out,
        "total_estimated_value_by_division": total_by_division_out,
        "total_by_pipeline_status": total_by_pipeline_status_out,
        "total_estimated_value_by_pipeline_status": total_by_pipeline_status_out,
        "count_by_pipeline_status": count_by_pipeline_status_out,
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
        "top_highest_value_jobs": top_highest_value_jobs,
        "division_summary_text": division_summary_text(total_by_division_out),
        "pipeline_summary_text": counter_summary_text(count_by_pipeline_status),
        "pipeline_value_summary_text": value_summary_text(total_by_pipeline_status_out, "No pipeline estimated values found"),
        "warning_jobs_text": warning_jobs_text(warning_jobs),
        "top_value_jobs_text": top_value_jobs_text(top_highest_value_jobs),
        "division_summary_html": division_summary_html(total_by_division_out),
        "pipeline_summary_html": pipeline_summary_html(count_by_pipeline_status),
        "pipeline_value_summary_html": pipeline_value_summary_html(total_by_pipeline_status_out),
        "warning_jobs_html": warning_jobs_html(warning_jobs),
        "top_value_jobs_html": top_value_jobs_html(top_highest_value_jobs),
    }
    payload["teams_message_html"] = teams_message_html(payload)
    return payload


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
