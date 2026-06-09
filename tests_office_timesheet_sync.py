from __future__ import annotations

from jobscan.office_timesheet_sync import (
    _finalize_record,
    build_code_summary,
    build_employee_daily_summary,
    build_project_touch_summary,
    parse_duration_hours,
)


def _hours(value: object) -> float | None:
    hours, _warning = parse_duration_hours(value)
    return hours


def _approx_hours(value: object) -> float | None:
    hours, _warning = parse_duration_hours(value, from_approx_time=True)
    return hours


def test_parse_duration_hours_examples() -> None:
    assert _hours("5hrs") == 5.0
    assert _hours("3hrs") == 3.0
    assert _hours("1.75 hrs") == 1.75
    assert _hours("1.25 hours") == 1.25
    assert _hours("4.5 hours") == 4.5
    assert _hours("3.5 hr") == 3.5
    assert _hours("2.5 hrs") == 2.5
    assert _hours("0.5 hr") == 0.5
    assert _hours("20 min") == 0.3333
    assert _hours("20") == 0.3333
    assert _hours("45") == 0.75
    assert _hours("55") == 0.9167
    assert _hours("1hr 45 min") == 1.75
    assert _hours("1.5 hr 15 min") == 1.75
    assert _hours("1:45") == 1.75
    assert _hours("0:30") == 0.5
    assert _hours(55) == 0.9167
    assert _hours(45) == 0.75
    assert _hours(50) == 0.8333
    assert _hours(4.5) == 4.5

    hours, warning = parse_duration_hours("1145")
    assert hours is None
    assert warning == "invalid duration"


def test_parse_approx_time_fractional_hours() -> None:
    assert _approx_hours(0.75) == 0.75
    assert _approx_hours(0.5) == 0.5
    assert _approx_hours("0.75") == 0.75
    assert _approx_hours("0.5") == 0.5
    assert _approx_hours("20") == 0.3333
    assert _approx_hours("45") == 0.75
    assert _approx_hours("1.25 hours") == 1.25
    assert _approx_hours("2.5 hrs") == 2.5


def test_parse_fractional_text_durations() -> None:
    assert _approx_hours("1/2 hr") == 0.5
    assert _approx_hours("1/2 hour") == 0.5
    assert _approx_hours("1/4 hr") == 0.25
    assert _approx_hours("3/4 hr") == 0.75
    assert _approx_hours("1 1/2 hr") == 1.5
    assert _approx_hours("1/2hr") == 0.5
    assert _approx_hours("1 / 2 hr") == 0.5


def _record(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "employee_name": "Aaron",
        "work_date": "2026-06-01",
        "project": "Smith",
        "code": "EST",
        "hubspot_notes": "",
        "additional_notes": "",
        "_approx_raw": None,
        "_start_raw": None,
        "_end_raw": None,
        "_warnings": [],
    }
    base.update(overrides)
    return _finalize_record(base)


def test_activity_only_rows_do_not_warn_missing_duration() -> None:
    activity = _record(additional_notes="Called customer")

    assert activity["row_type"] == "activity_only"
    assert activity["duration_hours"] is None
    assert "missing duration" not in str(activity["warnings"])


def test_summary_counts_timed_and_activity_rows() -> None:
    timed = _record(_approx_raw="1 hr")
    activity = _record(project="Smith", code="EST", additional_notes="Left voicemail")
    records = [timed, activity]

    employee_daily = build_employee_daily_summary(records)[0]
    code_summary = build_code_summary(records)[0]
    project_touch = build_project_touch_summary(records)[0]

    for summary in (employee_daily, code_summary, project_touch):
        assert summary["total_hours"] == 1.0
        assert summary["line_count"] == 2
        assert summary["timed_entry_count"] == 1
        assert summary["activity_only_count"] == 1


if __name__ == "__main__":
    test_parse_duration_hours_examples()
    test_parse_approx_time_fractional_hours()
    test_parse_fractional_text_durations()
    test_activity_only_rows_do_not_warn_missing_duration()
    test_summary_counts_timed_and_activity_rows()
    print("office timesheet duration parser ok")
