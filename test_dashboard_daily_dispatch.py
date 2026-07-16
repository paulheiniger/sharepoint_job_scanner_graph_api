from datetime import date

import pandas as pd


def test_generate_dispatch_message_includes_time_range_address_and_crew() -> None:
    import dashboard.app as app

    dispatch = pd.DataFrame(
        [
            {
                "customer": "UK",
                "job_name": "WT Young",
                "site_address": "Lexington, KY",
                "start_time": "7:00 AM",
                "end_time": "3:30 PM",
                "crew_leader": "Quin",
                "crew_members": "Carlos, Santos",
                "work_notes": "Finish roof detail.",
                "equipment_notes": "Lift",
                "material_notes": "Silicone",
                "safety_notes": "Library access constraints",
            }
        ]
    )

    message = app.generate_dispatch_message(dispatch, date(2026, 7, 16))

    assert "Daily Crew Dispatch - Thursday, July 16, 2026" in message
    assert "Quin" in message
    assert "7:00 AM-3:30 PM | UK - WT Young" in message
    assert "Site: Lexington, KY" in message
    assert "Crew: Carlos, Santos" in message
    assert "Equipment: Lift" in message
    assert "Materials: Silicone" in message
    assert "Safety: Library access constraints" in message


def test_dispatch_swimlane_containers_round_trip_to_job_ids() -> None:
    import dashboard.app as app

    jobs = pd.DataFrame(
        [
            {
                "job_id": "job-uk-wt-young",
                "job_display": "UK - WT Young",
                "crew_members": "Carlos",
            },
            {
                "job_id": "job-pegasus-39",
                "job_display": "Pegasus - 39 Pearce",
                "crew_members": "",
            },
        ]
    )

    containers, header_to_job_id = app.build_dispatch_swimlane_containers(
        jobs,
        ["Carlos", "Santos", "Quin"],
        {"job-pegasus-39": ["Santos"]},
    )

    assert containers[0] == {"header": app.UNASSIGNED_CREW_LANE_HEADER, "items": ["Quin"]}
    assert header_to_job_id[containers[1]["header"]] == "job-uk-wt-young"
    assert header_to_job_id[containers[2]["header"]] == "job-pegasus-39"

    moved = [
        containers[0],
        {"header": containers[1]["header"], "items": ["Carlos", "Quin"]},
        {"header": containers[2]["header"], "items": ["Santos"]},
    ]

    selected = app.selected_assignments_from_swimlane_containers(moved, header_to_job_id)

    assert selected == {
        "job-uk-wt-young": ["Carlos", "Quin"],
        "job-pegasus-39": ["Santos"],
    }
