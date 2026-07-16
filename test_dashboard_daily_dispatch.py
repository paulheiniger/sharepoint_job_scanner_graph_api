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


def test_daily_production_material_records_skip_empty_values() -> None:
    import dashboard.app as app

    production_entry = {
        "production_entry_id": "production-1",
        "job_id": "job-1",
        "work_date": date(2026, 7, 16),
        "foam_lbs": 1200,
        "primer": 2.5,
        "caulk": 0,
        "sf": None,
    }

    records = app.production_material_records(production_entry)

    assert [record["material_type"] for record in records] == ["foam_lbs", "primer"]
    assert records[0]["quantity"] == 1200
    assert records[0]["unit"] == "lbs"
    assert records[1]["quantity"] == 2.5
    assert records[1]["unit"] == "gal"


def test_daily_production_ids_are_stable() -> None:
    import dashboard.app as app

    first = app.production_entry_id_for(date(2026, 7, 16), "job-1", "Carlos")
    second = app.production_entry_id_for(date(2026, 7, 16), "job-1", "Carlos")
    different = app.production_entry_id_for(date(2026, 7, 16), "job-1", "Santos")

    assert first == second
    assert first != different
    assert first.startswith("production-")


def test_weather_time_parser_handles_common_dispatch_times() -> None:
    import dashboard.app as app

    assert app.parse_weather_time("7:30 AM") == (7, 30)
    assert app.parse_weather_time("3 pm") == (15, 0)
    assert app.parse_weather_time("12:15 AM") == (0, 15)
    assert app.parse_weather_time("TBD") == (12, 0)


def test_weather_values_from_open_meteo_hourly_uses_nearest_hour() -> None:
    import dashboard.app as app

    hourly = {
        "time": ["2026-07-16T06:00", "2026-07-16T07:00", "2026-07-16T08:00"],
        "temperature_2m": [70.2, 72.8, 75.1],
        "relative_humidity_2m": [80, 77, 73],
        "wind_speed_10m": [4.5, 5.2, 6.7],
    }

    values = app.weather_values_from_open_meteo_hourly(
        hourly,
        app.weather_target_datetime(date(2026, 7, 16), "7:20 AM"),
    )

    assert values["observed_at"] == "2026-07-16T07:00"
    assert values["temperature_f"] == 72.8
    assert values["humidity_pct"] == 77
    assert values["wind_mph"] == 5.2


def test_daily_production_checklist_options_round_trip() -> None:
    import dashboard.app as app

    stored = app.option_text(["Roof Edges", "", "Power Tools"])

    assert stored == "Roof Edges, Power Tools"
    assert app.selected_options(stored, app.DAILY_PRODUCTION_SAFETY_OPTIONS) == ["Roof Edges", "Power Tools"]
    assert "Lockers Secured/Stocked" in app.DAILY_PRODUCTION_TRAILER_CLOSEOUT_OPTIONS


def test_daily_production_schema_supports_second_equipment_row() -> None:
    import dashboard.app as app

    assert "truck_number_2 TEXT" in app.DAILY_PRODUCTION_ENTRIES_TABLE_SQL
    assert any("truck_number_2" in statement for statement in app.DAILY_PRODUCTION_EXTRA_COLUMNS_SQL)
    assert any("odometer_in_2" in statement for statement in app.DAILY_PRODUCTION_EXTRA_COLUMNS_SQL)


def test_daily_production_hours_from_times_use_crew_hours() -> None:
    import dashboard.app as app

    hours = app.calculate_daily_production_hours_from_times(
        crew_leader="Carlos",
        crew_members="Santos, Mariano",
        outbound_departure_time="7:00",
        jobsite_arrival_time="8:00",
        lunch_start_time="12:00",
        lunch_end_time="12:30",
        return_departure_time="4:00",
        return_arrival_time="5:00",
    )

    assert hours["ok"] is True
    assert hours["crew_count"] == 3
    assert hours["onsite_hours"] == 7.5
    assert hours["travel_duration_hours"] == 2
    assert hours["labor_hours"] == 22.5
    assert hours["travel_hours"] == 6


def test_daily_production_hours_roll_forward_for_afternoon_without_meridiem() -> None:
    import dashboard.app as app

    hours = app.calculate_daily_production_hours_from_times(
        crew_leader="Mariano",
        crew_members="Erik\nJose",
        outbound_departure_time="7:05",
        jobsite_arrival_time="7:50",
        lunch_start_time="12:00",
        lunch_end_time="12:40",
        return_departure_time="6:15",
        return_arrival_time="7:00",
    )

    assert hours["crew_count"] == 3
    assert hours["onsite_hours"] == 9.75
    assert hours["travel_duration_hours"] == 1.5
    assert hours["labor_hours"] == 29.25
    assert hours["travel_hours"] == 4.5
