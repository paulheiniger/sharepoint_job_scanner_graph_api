from __future__ import annotations

import inspect

import pandas as pd

from dashboard.job_board_kanban import build_job_board_kanban_payload, component_event


def test_kanban_payload_groups_cards_and_deduplicates_jobs() -> None:
    rows = [
        {
            "job_id": "job-1",
            "sales_stage": "Proposal Submitted",
            "project": "Grossman Tuning",
            "customer_display": "Grossman",
            "sales_value": 125000,
            "deal_owner": "Paul",
            "priority": "High",
            "warning_count": 2,
        },
        {
            "job_id": "job-2",
            "sales_stage": "Contract Pending",
            "project": "Elsby Repair",
            "sales_value": 8500,
        },
        {
            "job_id": "job-1",
            "sales_stage": "Proposal Submitted",
            "project": "Duplicate row",
            "sales_value": 1,
        },
    ]

    payload = build_job_board_kanban_payload(
        rows,
        ["Lead Received", "Proposal Submitted", "Contract Pending"],
        selected_job_id="job-1",
        group_field="sales_stage",
    )

    lanes = {lane["status"]: lane for lane in payload["lanes"]}
    assert payload["selected_job_id"] == "job-1"
    assert lanes["Lead Received"]["cards"] == []
    assert lanes["Proposal Submitted"]["count"] == 1
    assert lanes["Proposal Submitted"]["total_value"] == 125000
    assert lanes["Proposal Submitted"]["cards"][0]["warning_count"] == 2
    assert lanes["Contract Pending"]["cards"][0]["job_id"] == "job-2"


def test_component_event_accepts_only_complete_known_events() -> None:
    assert component_event({"event": {"event_id": "event-1", "type": "select", "job_id": "job-1"}}) == {
        "event_id": "event-1",
        "type": "select",
        "job_id": "job-1",
    }
    assert component_event({"event": {"type": "select", "job_id": "job-1"}}) is None
    assert component_event({"event": {"event_id": "event-1", "type": "unknown", "job_id": "job-1"}}) is None


def test_process_kanban_move_uses_current_status_and_selects_job(monkeypatch) -> None:
    import dashboard.app as app

    session_state: dict[str, object] = {}
    saved: list[dict[str, object]] = []
    monkeypatch.setattr(app.st, "session_state", session_state)
    monkeypatch.setattr(app, "save_job_board_status_move", lambda **kwargs: saved.append(kwargs) or True)
    jobs = pd.DataFrame(
        [
            {
                "job_id": "job-1",
                "workflow_status": "Proposed",
                "sales_stage": "Proposal Submitted",
                "pipeline_status": "Estimate In Progress",
            }
        ]
    )

    action = app.process_job_board_kanban_event(
        {
            "event_id": "move-1",
            "type": "move",
            "job_id": "job-1",
            "from_status": "Lead Created",
            "to_status": "Contract Pending",
        },
        jobs,
        ["Proposal Submitted", "Contract Pending"],
    )

    assert action == "move"
    assert saved[0]["from_status"] == "Proposal Submitted"
    assert saved[0]["to_status"] == "Contract Pending"
    assert session_state["selected_job_board_job_id"] == "job-1"
    assert session_state["job_board_last_kanban_event_id"] == "move-1"


def test_process_kanban_event_does_not_reapply_duplicate(monkeypatch) -> None:
    import dashboard.app as app

    monkeypatch.setattr(app.st, "session_state", {"job_board_last_kanban_event_id": "move-1"})
    jobs = pd.DataFrame([{"job_id": "job-1", "workflow_status": "Proposed"}])

    action = app.process_job_board_kanban_event(
        {
            "event_id": "move-1",
            "type": "move",
            "job_id": "job-1",
            "to_status": "Contracted",
        },
        jobs,
        ["Proposed", "Contracted"],
    )

    assert action == "duplicate"


def test_status_move_rejects_unknown_lane_before_database_write(monkeypatch) -> None:
    import dashboard.app as app

    monkeypatch.setattr(app, "ensure_job_workflow_overrides_table", lambda: None)

    try:
        app.save_job_board_status_move(
            event_id="event-1",
            job_id="job-1",
            from_status="Proposed",
            to_status="Not A Lane",
        )
    except ValueError as exc:
        assert "Unsupported job board status" in str(exc)
    else:
        raise AssertionError("Unknown board lanes must be rejected")


def test_status_move_writes_audit_event_and_only_updates_status(monkeypatch) -> None:
    import dashboard.app as app

    executed: list[tuple[str, dict[str, object]]] = []

    class Result:
        def __init__(self, scalar_value: object = None) -> None:
            self.scalar_value = scalar_value

        def scalar(self) -> object:
            return self.scalar_value

    class Connection:
        def execute(self, statement, record):
            sql = str(statement)
            executed.append((sql, dict(record)))
            return Result("event-1" if "INSERT INTO job_workflow_events" in sql else None)

    class Transaction:
        def __enter__(self):
            return Connection()

        def __exit__(self, exc_type, exc, traceback):
            return False

    class Engine:
        def begin(self):
            return Transaction()

    monkeypatch.setattr(app, "ensure_job_workflow_overrides_table", lambda: None)
    monkeypatch.setattr(app, "get_engine", lambda: Engine())
    monkeypatch.setattr(app.st.cache_data, "clear", lambda: None)

    saved = app.save_job_board_status_move(
        event_id="event-1",
        job_id="job-1",
        from_status="Proposal Submitted",
        to_status="Contract Pending",
        updated_by="tester",
    )

    assert saved is True
    assert len(executed) == 2
    assert "INSERT INTO job_workflow_events" in executed[0][0]
    assert "INSERT INTO job_workflow_overrides" in executed[1][0]
    assert "deal_owner" not in executed[1][0]
    assert executed[1][1]["to_status"] == "Contract Pending"


def test_job_detail_is_rendered_once_below_kanban_with_collapsible_sections() -> None:
    import dashboard.app as app

    page_source = inspect.getsource(app.job_board_page)
    panel_source = inspect.getsource(app.render_job_board_detail_panel)

    assert page_source.count("render_job_board_detail_panel") == 1
    assert 'st.header("Job Detail")' not in page_source
    for section in [
        "Overview and commercial details",
        "Estimate, proposal, and scope evidence",
        "Documents and source files",
        "Workflow, ownership, and internal notes",
        "Operational readiness and warnings",
        "Scheduling and crew plan",
        "Copyable job summary",
    ]:
        assert section in panel_source
