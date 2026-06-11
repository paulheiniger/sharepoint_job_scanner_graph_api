from datetime import datetime, timezone

from jobscan.db_loader import ensure_primary_id, prepare_row, stable_id


def test_stable_id_is_deterministic_and_prefixed() -> None:
    first = stable_id("estimate", "job-1", "Estimate.xlsx")
    second = stable_id("estimate", "job-1", "Estimate.xlsx")

    assert first == second
    assert first.startswith("estimate-")


def test_prepare_row_generates_line_item_id_and_coerces_values() -> None:
    columns = {
        "line_item_id": "text",
        "estimate_id": "text",
        "quantity": "numeric",
        "extended_cost": "numeric",
        "source_row": "integer",
        "raw": "jsonb",
        "updated_at": "timestamp with time zone",
    }
    row = prepare_row(
        "line_items",
        {
            "job_id": "job-1",
            "estimate_file": "Estimate.xlsx",
            "source_sheet": "Estimate",
            "source_row": "42",
            "line_item_name": "Lift Rental",
            "quantity": "",
            "extended_cost": "$3,500.00",
        },
        columns,
        datetime(2026, 6, 10, tzinfo=timezone.utc),
    )

    assert row["line_item_id"].startswith("line-item-")
    assert row["estimate_id"].startswith("estimate-")
    assert row["quantity"] is None
    assert row["extended_cost"] == 3500
    assert row["source_row"] == 42
    assert row["raw"]["extended_cost"] == "$3,500.00"


def test_ensure_primary_id_uses_job_id_for_schedule_id() -> None:
    row = ensure_primary_id("crew_schedule", {"job_id": "job-123", "ready_to_schedule": "yes"})

    assert row["schedule_id"] == "job-123"
