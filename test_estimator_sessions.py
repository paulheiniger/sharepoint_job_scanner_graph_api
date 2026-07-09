from __future__ import annotations

import json
import zipfile

from sqlalchemy import create_engine, text

from jobscan.estimator.session_capture import (
    create_estimator_session,
    ensure_estimator_session_tables,
    estimator_memory_candidates_from_edits,
    export_estimator_session_package,
    export_training_dataset,
    final_decisions_from_workbench,
    load_estimator_session_payload,
    proposed_decisions_from_workbench,
    save_decision_edits,
    save_decision_proposal,
    save_final_decisions,
    save_memory_candidates_from_edits,
    save_scope_interpretation,
    save_session_artifact,
    update_estimator_session,
    workbook_cell_writes_from_inputs,
)
from jobscan.estimator.estimator_memory import approved_memory_frame, estimator_memory_frame, update_estimator_memory_status


def sample_workbench() -> dict:
    return {
        "estimate_id": "session-test",
        "scope": {
            "division": "Roofing",
            "template_type": "roofing",
            "project_type": "roof coating",
            "net_sqft": 10000,
            "job_name": "Session Test Roof",
        },
        "historical_filters": {"division": "Roofing", "template_type": "roofing"},
        "decision_proposals": [
            {
                "decision_id": "roofing_coating_system_row_26",
                "template_type": "roofing",
                "section": "roofing_coating_template_decisions",
                "template_bucket": "coating",
                "workbook_row": "26",
                "include": True,
                "source": "explicit_note",
                "confidence": 0.9,
                "review_required": True,
                "review_reasons": ["Warranty duration was not stated."],
                "evidence": {"note": [{"text": "Customer requested coating."}]},
            }
        ],
        "roofing_coating_template_decisions": [
            {
                "include": True,
                "section": "roofing_coating_template_decisions",
                "decision_id": "roofing_coating_system_row_26",
                "template_bucket": "coating",
                "workbook_row": "26",
                "editable_selector_code": "11",
                "resolved_template_option": "Gaco Silicone",
                "selected_pricing_candidate": "GAF High Solids Silicone 55 Gal",
                "basis_sqft": 10000,
                "gal_per_100_sqft": 1.5,
                "waste_factor_pct": 10,
                "unit_price": 42,
                "estimated_gallons": 166.67,
                "estimated_cost": 7000.14,
                "historical_recommendation": "Historical coating decision from 11 jobs.",
                "decision_evidence_count": 11,
                "decision_source_jobs_count": 9,
                "decision_confidence": "high",
                "decision_source_tables": "roofing_coating_decision_history",
                "product_id": "prod-gaf-silicone",
                "product_manufacturer": "GAF",
                "product_guidance": "Use as silicone roof coating.",
                "product_warnings": ["Do not apply over wet substrate."],
                "product_source_documents": ["gaf_silicone_pds.pdf"],
                "decision_proposal": {
                    "decision_id": "roofing_coating_system_row_26",
                    "source": "explicit_note",
                },
                "proposal_source": "explicit_note",
                "proposal_confidence": 0.9,
                "proposal_evidence": {"note": [{"text": "Customer requested coating."}]},
                "proposal_review_reasons": ["Warranty duration was not stated."],
                "decision_evidence_summary": "note evidence, historical evidence (11), product guidance, formula preview",
                "decision_evidence_types": "note, historical, pricing, product, formula",
                "why_included": "Included by explicit note; review: Warranty duration was not stated.",
                "historical_evidence_summary": "11 historical decision rows; confidence high; recommendation Gaco Silicone",
                "pricing_evidence_summary": "GAF High Solids Silicone 55 Gal; unit price 42",
                "product_evidence_summary": "prod-gaf-silicone; Use as silicone roof coating.",
                "formula_evidence_summary": "gallons=166.67, cost=7000.14",
                "workbook_cell_write_preview": [{"cell": "Estimate!A26", "field": "selector_code", "value": "11"}],
            }
        ],
        "roofing_labor_template_decisions": [
            {
                "include": True,
                "section": "roofing_labor_template_decisions",
                "decision_id": "roofing_labor_base",
                "template_bucket": "labor_base",
                "workbook_row": "122",
                "days": 2,
                "crew_size": 4,
                "hourly_rate": 72,
                "total_hours": 64,
                "estimated_cost": 4608,
            }
        ],
        "review_flags": ["Estimator review required."],
    }


def sample_workbook_inputs() -> dict:
    return {
        "template_type": "roofing",
        "header": {
            "C2_job_name": "Session Test Roof",
            "C3_job_type": "roof coating",
            "C12_estimated_sqft": 10000,
        },
        "workbook_decisions": [
            {
                "row_type": "material",
                "section": "roofing_coating_template_decisions",
                "decision_id": "roofing_coating_system_row_26",
                "template_bucket": "coating",
                "workbook_row": "26",
                "row_traceability": "Estimate row 26",
                "item": "Gaco Silicone",
                "category": "coating",
                "quantity": 166.67,
                "unit_price": 42,
                "estimated_cost": 7000.14,
                "workbook_cell_write_preview": [{"cell": "Estimate!A26", "field": "selector_code", "value": "11"}],
            },
            {
                "row_type": "labor",
                "section": "roofing_labor_template_decisions",
                "decision_id": "roofing_labor_base",
                "template_bucket": "labor_base",
                "workbook_row": "122",
                "task": "labor_base",
                "crew_size": 4,
                "total_hours": 64,
                "adjusted_days": 2,
                "estimated_cost": 4608,
            },
        ],
    }


def test_session_decision_helpers_are_decision_only() -> None:
    workbench = sample_workbench()

    proposed = proposed_decisions_from_workbench(workbench)
    final = final_decisions_from_workbench(workbench)

    proposed_sections = {row["section"] for row in proposed["decisions"]}
    final_sections = {row["section"] for row in final["decisions"]}
    assert "roofing_coating_template_decisions" in proposed_sections
    assert "roofing_labor_template_decisions" in proposed_sections
    assert "roofing_coating_template_decisions" in final_sections
    assert "materials" not in proposed_sections
    assert "labor" not in proposed_sections
    assert "adders" not in proposed_sections

    coating = next(row for row in final["decisions"] if row["decision_id"] == "roofing_coating_system_row_26")
    assert coating["template_bucket"] == "coating"
    assert coating["proposal_source"] == "explicit_note"
    assert coating["source_evidence"]["proposal_evidence"]["note"][0]["text"] == "Customer requested coating."
    assert coating["proposal_review_reasons"] == ["Warranty duration was not stated."]
    assert coating["decision_evidence_types"] == "note, historical, pricing, product, formula"
    assert coating["historical_evidence_summary"].startswith("11 historical decision rows")
    assert coating["pricing_evidence_summary"].startswith("GAF High Solids Silicone")
    assert coating["product_guidance_snapshot"]["source_documents"] == ["gaf_silicone_pds.pdf"]
    assert coating["source_evidence"]["decision_source_tables"] == "roofing_coating_decision_history"
    assert coating["source_evidence"]["why_included"].startswith("Included by explicit note")


def test_workbook_cell_writes_use_decision_native_payload() -> None:
    writes = workbook_cell_writes_from_inputs(sample_workbook_inputs())

    assert any(row["section"] == "header" and row["cell"] == "Estimate!C2" for row in writes)
    decision_writes = [row for row in writes if row.get("section") != "header"]
    assert {row["section"] for row in decision_writes} == {
        "roofing_coating_template_decisions",
        "roofing_labor_template_decisions",
    }
    assert any(row["decision_id"] == "roofing_coating_system_row_26" for row in decision_writes)
    assert not any(row.get("section") == "materials" for row in decision_writes)


def test_estimator_memory_candidates_from_edits_are_pending_until_approved() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    ensure_estimator_session_tables(engine)
    session_id = create_estimator_session(
        engine,
        raw_input_notes="Insulation notes.",
        division="Insulation",
        template_type="insulation",
    )
    edits = [
        {
            "section": "insulation_logistics_expense_template_decisions.labor_loading",
            "field_name": "hours_per_day",
            "package_or_labor_task": "labor_loading",
            "suggested_value": 8,
            "final_value": 0.5,
            "reason": "Loading is not a full day.",
        },
        {
            "section": "insulation_logistics_expense_template_decisions.labor_loading",
            "field_name": "hours_per_day",
            "package_or_labor_task": "labor_loading",
            "suggested_value": 8,
            "final_value": 0.5,
            "reason": "Loading is not a full day.",
        },
        {
            "section": "insulation_logistics_expense_template_decisions.labor_loading",
            "field_name": "notes",
            "package_or_labor_task": "labor_loading",
            "suggested_value": "",
            "final_value": "Estimator comment",
        },
    ]

    candidates = estimator_memory_candidates_from_edits(edits, session_id=session_id, template_type="insulation")
    assert len(candidates) == 1
    assert candidates[0]["status"] == "pending"
    assert candidates[0]["template_bucket"] == "labor_loading"
    assert "hours_per_day from 8 to 0.5" in candidates[0]["guidance"]

    memory_ids = save_memory_candidates_from_edits(engine, session_id, edits)
    assert len(memory_ids) == 1
    assert len(estimator_memory_frame(engine, status="pending")) == 1
    assert approved_memory_frame(engine).empty

    update_estimator_memory_status(engine, memory_ids, status="approved", approved_by="tester")
    assert len(approved_memory_frame(engine)) == 1


def test_estimator_session_lifecycle_and_exports(tmp_path) -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    ensure_estimator_session_tables(engine)

    session_id = create_estimator_session(
        engine,
        raw_input_notes="Roof coating notes from email.",
        division="Roofing",
        template_type="roofing",
        job_name="Session Test Roof",
        input_source_type="email",
        source_file_ids=["doc-1"],
        estimate_status="PARSING",
    )
    update_estimator_session(engine, session_id, estimate_status="READY_TO_ESTIMATE")

    save_scope_interpretation(
        engine,
        session_id,
        parsed_scope={"project_type": "roof coating", "estimated_sqft": 10000},
        deterministic_scope={"estimated_sqft": 10000},
        assumptions={"source": "dimension parser"},
        missing_questions=[],
        confidence_by_field={"estimated_sqft": "high"},
        review_flags=["Estimator review required."],
    )
    proposal_id = save_decision_proposal(
        engine,
        session_id,
        proposed_decisions=proposed_decisions_from_workbench(sample_workbench()),
        template_type="roofing",
        evidence_summary={"historical_filters": {"division": "Roofing"}},
    )
    edits = [
        {
            "section": "roofing_labor_template_decisions.roofing_labor_base",
            "field_name": "total_hours",
            "package_or_labor_task": "labor_base",
            "suggested_value": 60,
            "final_value": 64,
            "reason": "Estimator adjusted production.",
        }
    ]
    edit_ids = save_decision_edits(engine, session_id, edits)
    workbook_inputs = sample_workbook_inputs()
    writes = workbook_cell_writes_from_inputs(workbook_inputs)
    final_id = save_final_decisions(
        engine,
        session_id,
        final_decisions=final_decisions_from_workbench(sample_workbench()),
        calculated_outputs={"totals": {"draft_total": 11608.14}, "draft_workbook_inputs": workbook_inputs},
        workbook_cell_writes=writes,
        workbook_export_path="output/estimates/session_test.xlsx",
    )
    artifact_id = save_session_artifact(
        engine,
        session_id,
        artifact_type="workbook",
        artifact_path="output/estimates/session_test.xlsx",
        artifact_json={"final_decision_id": final_id},
    )

    assert proposal_id
    assert edit_ids
    assert artifact_id

    with engine.connect() as connection:
        raw_notes = connection.execute(
            text("SELECT raw_input_notes FROM estimator_sessions WHERE session_id = :session_id"),
            {"session_id": session_id},
        ).scalar_one()
    assert raw_notes == "Roof coating notes from email."

    payload = load_estimator_session_payload(engine, session_id)
    assert payload["review"]["parsed_scope"]["estimated_sqft"] == 10000
    assert payload["review"]["calculated_outputs"]["totals"]["draft_total"] == 11608.14
    assert not any(row.get("section") == "materials" for row in payload["review"]["workbook_cell_writes"])
    coating_write = next(
        row
        for row in payload["review"]["workbook_cell_writes"]
        if row.get("decision_id") == "roofing_coating_system_row_26"
    )
    assert coating_write["row_traceability"] == "Estimate row 26"

    zip_path = export_estimator_session_package(engine, session_id, tmp_path / "session_review.zip")
    assert zip_path.exists()
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
        assert {
            "session_review.json",
            "session_payload.json",
            "raw_notes.txt",
            "parsed_scope.json",
            "proposed_decisions.json",
            "estimator_edits.json",
            "final_decisions.json",
            "calculated_outputs.json",
            "workbook_export_path.txt",
            "workbook_cell_writes.json",
        }.issubset(names)
        review = json.loads(archive.read("session_review.json"))
        assert review["raw_input_notes"] == "Roof coating notes from email."
        assert review["workbook_export_path"] == "output/estimates/session_test.xlsx"

    compact_zip_path = export_estimator_session_package(
        engine,
        session_id,
        tmp_path / "session_review_compact.zip",
        include_full_payload=False,
    )
    with zipfile.ZipFile(compact_zip_path) as archive:
        names = set(archive.namelist())
        assert "session_review.json" in names
        assert "session_payload.json" not in names
        assert "session_payload_omitted.txt" in names

    jsonl_path = export_training_dataset(engine, tmp_path / "training.jsonl")
    rows = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["raw_input_notes"] == "Roof coating notes from email."
    assert rows[0]["template_type"] == "roofing"
    assert rows[0]["division"] == "Roofing"
    assert rows[0]["estimator_edits"][0]["field_name"] == "total_hours"
    proposed_decisions = rows[0]["proposed_decisions"][0]["decisions"]
    assert any(
        str(decision.get("decision_id") or "").startswith("roofing_coating_system_row_")
        for decision in proposed_decisions
    )
    assert not any(row.get("section") == "materials" for row in rows[0]["workbook_cell_writes"])
