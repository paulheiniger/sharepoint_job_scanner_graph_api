from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import Column, Date, DateTime, Float, MetaData, String, Table, create_engine

from jobscan.business.timeseries_service import get_operational_timeseries


def build_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata = MetaData()
    jobs = Table(
        "jobs", metadata,
        Column("job_id", String, primary_key=True),
        Column("customer", String),
        Column("job_name", String),
        Column("division", String),
        Column("job_type", String),
        Column("pipeline_status", String),
        Column("status", String),
    )
    signals = Table(
        "job_document_signals", metadata,
        Column("job_id", String, primary_key=True),
        Column("document_substrate", String),
        Column("document_material_system", String),
    )
    estimates = Table(
        "estimate_template_rows", metadata,
        Column("template_row_id", String, primary_key=True),
        Column("job_id", String),
        Column("template_type", String),
        Column("template_bucket", String),
        Column("row_label", String),
        Column("raw_text", String),
        Column("resolved_item_name", String),
    )
    tracking = Table(
        "job_tracking_daily_entries", metadata,
        Column("tracking_entry_id", String, primary_key=True),
        Column("job_id", String),
        Column("work_date", Date),
        Column("foam_strokes", Float),
        Column("foam_lbs", Float),
        Column("labor_hours", Float),
        Column("crew", String),
        Column("notes", String),
        Column("source_row", Float),
        Column("updated_at", DateTime(timezone=True)),
    )
    workflow = Table(
        "job_workflow_events", metadata,
        Column("event_id", String, primary_key=True),
        Column("job_id", String),
        Column("event_type", String),
        Column("from_status", String),
        Column("to_status", String),
        Column("event_source", String),
        Column("updated_by", String),
        Column("created_at", DateTime(timezone=True)),
    )
    metadata.create_all(engine)
    now = datetime(2026, 8, 7, tzinfo=timezone.utc)
    with engine.begin() as connection:
        connection.execute(jobs.insert(), [
            {"job_id": "CC-WALL", "customer": "Acme", "job_name": "Warehouse Walls", "division": "Insulation", "job_type": "Spray Foam", "pipeline_status": "Completed", "status": "Complete"},
            {"job_id": "OPEN-CEILING", "customer": "Beta", "job_name": "Ceiling", "division": "Insulation", "job_type": "Spray Foam", "pipeline_status": "Completed", "status": "Complete"},
        ])
        connection.execute(signals.insert(), [
            {"job_id": "CC-WALL", "document_substrate": "Metal", "document_material_system": "Closed-cell spray foam"},
            {"job_id": "OPEN-CEILING", "document_substrate": "Metal", "document_material_system": "Open-cell spray foam"},
        ])
        connection.execute(estimates.insert(), [
            {"template_row_id": "r1", "job_id": "CC-WALL", "template_type": "insulation", "template_bucket": "foam", "row_label": "Wall foam", "raw_text": "Install at exterior walls", "resolved_item_name": "2 lb closed cell"},
            {"template_row_id": "r2", "job_id": "OPEN-CEILING", "template_type": "insulation", "template_bucket": "foam", "row_label": "Ceiling foam", "raw_text": "Roof underside", "resolved_item_name": "Open cell"},
        ])
        connection.execute(tracking.insert(), [
            {"tracking_entry_id": "t1", "job_id": "CC-WALL", "work_date": date(2026, 7, 1), "foam_strokes": 1600, "foam_lbs": None, "labor_hours": 8, "crew": "A", "notes": "Sprayed west wall", "source_row": 10, "updated_at": now},
            {"tracking_entry_id": "t2", "job_id": "CC-WALL", "work_date": date(2026, 7, 1), "foam_strokes": None, "foam_lbs": 500, "labor_hours": 4, "crew": "A", "notes": "Sprayed east wall", "source_row": 11, "updated_at": now},
            {"tracking_entry_id": "t3", "job_id": "CC-WALL", "work_date": date(2026, 7, 2), "foam_strokes": None, "foam_lbs": 1000, "labor_hours": 8, "crew": "A", "notes": "Sprayed walls", "source_row": 12, "updated_at": now},
            {"tracking_entry_id": "t4", "job_id": "OPEN-CEILING", "work_date": date(2026, 7, 2), "foam_strokes": None, "foam_lbs": 2000, "labor_hours": 8, "crew": "B", "notes": "Ceiling", "source_row": 5, "updated_at": now},
        ])
        connection.execute(workflow.insert(), [
            {"event_id": "e1", "job_id": "CC-WALL", "event_type": "status_change", "from_status": "Proposal Submitted", "to_status": "Contract Signed", "event_source": "dashboard", "updated_by": "Estimator", "created_at": now},
        ])
    return engine


def test_tracking_timeseries_filters_scope_derives_sets_and_paginates() -> None:
    engine = build_engine()

    first = get_operational_timeseries(
        engine=engine,
        dataset="job_tracking_daily",
        scope_terms=["closed cell", "wall"],
        metric="foam_sets",
        positive_only=True,
        page=1,
        page_size=2,
    )

    assert first["pagination"] == {
        "page": 1,
        "page_size": 2,
        "total_records": 3,
        "total_pages": 2,
        "has_more": True,
        "next_page": 2,
    }
    assert [row["tracking_entry_id"] for row in first["records"]] == ["t1", "t2"]
    assert first["records"][0]["foam_sets"] == 1.0
    assert first["records"][0]["foam_sets_basis"].startswith("foam_strokes")
    assert first["records"][1]["foam_sets"] == 0.5
    assert "Closed-cell spray foam" in first["records"][0]["scope_evidence"]

    second = get_operational_timeseries(
        engine=engine,
        dataset="job_tracking_daily",
        scope_terms=["closed cell", "vertical"],
        metric="foam_sets",
        positive_only=True,
        page=2,
        page_size=2,
    )
    assert [row["tracking_entry_id"] for row in second["records"]] == ["t3"]
    assert second["records"][0]["foam_sets"] == 1.0
    assert second["pagination"]["has_more"] is False


def test_workflow_events_use_same_paginated_contract() -> None:
    result = get_operational_timeseries(
        engine=build_engine(),
        dataset="workflow_events",
        division="Insulation",
        page_size=10,
    )

    assert result["pagination"]["total_records"] == 1
    assert result["records"][0]["event_type"] == "status_change"
    assert result["records"][0]["job_customer"] == "Acme"
    assert result["records"][0]["event_date"].startswith("2026-08-07")
