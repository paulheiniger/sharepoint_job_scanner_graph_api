from datetime import date

import pandas as pd


def schedule_row(schedule_id: str, crew: str, customer: str) -> dict[str, object]:
    return {
        "schedule_id": schedule_id,
        "job_id": f"job-{schedule_id}",
        "customer": customer,
        "job_name": "Roof",
        "assigned_crew_leader": crew,
        "estimated_start_date": "2026-08-10",
        "estimated_end_date": "2026-08-12",
        "schedule_status": "Scheduled",
    }


def test_calendar_events_deduplicate_repeated_schedule_ids() -> None:
    import dashboard.app as app

    duplicated = schedule_row("schedule-one", "Santos", "Goodwin")
    events = app.calendar_events_from_schedule(pd.DataFrame([duplicated, duplicated]))

    assert len(events) == 1
    assert events[0]["id"] == "schedule-one"


def test_schedule_swimlanes_round_trip_crew_changes() -> None:
    import dashboard.app as app

    events = app.calendar_events_from_schedule(
        pd.DataFrame(
            [
                schedule_row("schedule-one", "Santos", "Goodwin"),
                schedule_row("schedule-two", "Quin", "Grossman"),
            ]
        )
    )
    rows = app.schedule_board_event_rows(events, today_value=date(2026, 8, 4))
    containers, card_to_event_id, current = app.build_schedule_swimlane_containers(rows)

    assert current == {"schedule-two": "Quin", "schedule-one": "Santos"}
    assert containers[-1] == {"header": "Unassigned", "items": []}

    santos = next(container for container in containers if container["header"] == "Santos")
    quin = next(container for container in containers if container["header"] == "Quin")
    moved_card = santos["items"].pop()
    quin["items"].append(moved_card)

    proposed = app.schedule_crew_assignments_from_swimlanes(containers, card_to_event_id)

    assert proposed == {"schedule-two": "Quin", "schedule-one": "Quin"}


def test_schedule_board_fallback_widget_keys_include_row_identity() -> None:
    import dashboard.app as app

    events = app.calendar_events_from_schedule(
        pd.DataFrame(
            [
                schedule_row("schedule-one", "Santos", "Goodwin"),
                schedule_row("schedule-two", "Santos", "Goodwin"),
            ]
        )
    )
    rows = app.schedule_board_event_rows(events, today_value=date(2026, 8, 4))
    labels = [app.schedule_board_card_label(row) for row in rows]

    assert len(labels) == len(set(labels))
