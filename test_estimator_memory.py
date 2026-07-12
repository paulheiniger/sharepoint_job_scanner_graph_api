from __future__ import annotations

import pandas as pd
from sqlalchemy import create_engine, text

from jobscan.estimator.estimator_memory import (
    approved_memory_frame,
    delete_estimator_memory,
    ensure_estimator_memory_table,
    estimator_memory_frame,
    estimator_memory_from_rows,
    relevant_memory_rows,
    update_estimator_memory_status,
    upsert_estimator_memory,
)


def test_estimator_memory_table_lifecycle_and_relevance_filtering() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    ensure_estimator_memory_table(engine)

    memory_id = upsert_estimator_memory(
        engine,
        guidance="Loading is usually a short setup item; start around 0.5 hours unless notes justify more.",
        template_type="insulation",
        template_bucket="labor_loading",
        priority="high",
        status="approved",
        applies_when={"keywords": ["spray foam"]},
        rationale="Repeated estimator correction.",
        approved_by="test",
    )
    upsert_estimator_memory(
        engine,
        guidance="Pending notes should not be exposed.",
        template_type="insulation",
        template_bucket="labor_loading",
        status="pending",
    )

    frame = approved_memory_frame(engine)
    assert len(frame) == 1
    assert frame.iloc[0]["memory_id"] == memory_id
    assert frame.iloc[0]["applies_when"] == {"keywords": ["spray foam"]}

    relevant = relevant_memory_rows(
        frame,
        scope={"template_type": "insulation", "project_type": "spray foam metal barn"},
        template_type="insulation",
        decision_buckets=["labor_loading"],
    )

    assert len(relevant) == 1
    assert relevant[0]["template_bucket"] == "labor_loading"
    assert "0.5 hours" in relevant[0]["guidance"]


def test_estimator_memory_upsert_updates_existing_guidance() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    memory_id = upsert_estimator_memory(
        engine,
        memory_id="memory-1",
        guidance="Old guidance.",
        template_type="insulation",
        template_bucket="labor_traveling",
        status="approved",
    )

    updated_id = upsert_estimator_memory(
        engine,
        memory_id=memory_id,
        guidance="Traveling should use hours x people x rate x trips, not labor days.",
        template_type="insulation",
        template_bucket="labor_traveling",
        status="approved",
        priority="high",
    )

    assert updated_id == memory_id
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT guidance, priority FROM estimator_memory")).fetchall()
    assert len(rows) == 1
    assert rows[0][0].startswith("Traveling should use hours")
    assert rows[0][1] == "high"


def test_estimator_memory_status_update_controls_approved_frame() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    memory_id = upsert_estimator_memory(
        engine,
        guidance="Pending memory candidate.",
        template_type="insulation",
        template_bucket="foam",
        status="pending",
    )

    assert len(estimator_memory_frame(engine, status="pending")) == 1
    assert approved_memory_frame(engine).empty

    updated = update_estimator_memory_status(engine, [memory_id], status="approved", approved_by="tester")

    assert updated == 1
    approved = approved_memory_frame(engine)
    assert len(approved) == 1
    assert approved.iloc[0]["status"] == "approved"
    assert approved.iloc[0]["approved_by"] == "tester"


def test_estimator_memory_delete_removes_rows() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    memory_id = upsert_estimator_memory(
        engine,
        guidance="Temporary memory candidate.",
        template_type="roofing",
        template_bucket="coating",
        status="pending",
    )

    deleted = delete_estimator_memory(engine, [memory_id])

    assert deleted == 1
    assert estimator_memory_frame(engine, status="pending").empty


def test_estimator_memory_relevance_excludes_wrong_template_and_disabled_rows() -> None:
    memory = estimator_memory_from_rows(
        [
            {
                "status": "approved",
                "priority": "high",
                "template_type": "roofing",
                "template_bucket": "coating",
                "guidance": "Roofing coating guidance.",
            },
            {
                "status": "disabled",
                "priority": "high",
                "template_type": "insulation",
                "template_bucket": "foam",
                "guidance": "Disabled insulation guidance.",
            },
            {
                "status": "approved",
                "priority": "medium",
                "template_type": "insulation",
                "template_bucket": "",
                "guidance": "Generic insulation memory applies.",
            },
        ]
    )

    relevant = relevant_memory_rows(
        memory,
        scope={"template_type": "insulation"},
        template_type="insulation",
        decision_buckets=["foam"],
    )

    assert [row["guidance"] for row in relevant] == ["Generic insulation memory applies."]


def test_estimator_memory_relevance_prioritizes_answer_key_cues() -> None:
    memory = estimator_memory_from_rows(
        [
            {
                "status": "approved",
                "priority": "high",
                "template_type": "roofing",
                "template_bucket": "coating_restoration",
                "source_type": "reference_template_summary",
                "guidance": "Row-level coating memory.",
            },
            {
                "status": "approved",
                "priority": "high",
                "template_type": "roofing",
                "template_bucket": "coating_restoration",
                "source_type": "reference_answer_key_cue",
                "guidance": "Cue-level coating restoration memory.",
            },
        ]
    )

    relevant = relevant_memory_rows(
        memory,
        scope={"template_type": "roofing", "scope_summary": "silicone coating restoration"},
        template_type="roofing",
        decision_buckets=["coating_restoration"],
        limit=2,
    )

    assert relevant[0]["guidance"] == "Cue-level coating restoration memory."


def test_estimator_memory_from_rows_normalizes_tokens_and_text() -> None:
    frame = estimator_memory_from_rows(
        pd.DataFrame(
            [
                {
                    "status": "Approved",
                    "priority": "High",
                    "template_type": "Insulation",
                    "template_bucket": "Labor Loading",
                    "guidance": "  Use short   loading defaults. ",
                }
            ]
        )
    )

    assert frame.iloc[0]["status"] == "approved"
    assert frame.iloc[0]["priority"] == "high"
    assert frame.iloc[0]["template_type"] == "insulation"
    assert frame.iloc[0]["template_bucket"] == "labor_loading"
    assert frame.iloc[0]["guidance"] == "Use short loading defaults."
