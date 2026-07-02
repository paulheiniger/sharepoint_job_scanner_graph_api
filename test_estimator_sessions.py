from __future__ import annotations

import json
import zipfile

from sqlalchemy import create_engine, text

from jobscan.estimator.session_capture import (
    create_estimator_session,
    ensure_estimator_session_tables,
    export_estimator_session_package,
    export_training_dataset,
    final_decisions_from_workbench,
    load_estimator_session_payload,
    proposed_decisions_from_workbench,
    save_decision_edits,
    save_decision_proposal,
    save_final_decisions,
    save_scope_interpretation,
    save_session_artifact,
    update_estimator_session,
    workbook_cell_writes_from_inputs,
)


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
        "materials": [
            {
                "include": True,
                "decision_id": "roofing_coating_system",
                "template_bucket": "coating",
                "package_key": "coating",
                "package": "Silicone",
                "workbook_row": "26",
                "item_name": "GAF High Solids Silicone 55 Gal",
                "historical_recommendation": "Historical coating decision from 11 jobs. GAF High Solids Silicone 55 Gal.",
                "calculated_output_summary": "quantity=150, cost=6300",
                "row_traceability": "Estimate rows 26-28",
                "decision_source_tables": "roofing_coating_decision_history",
                "decision_filters_applied": "division, template_type",
                "decision_filters_relaxed": "",
                "recommended_decision_value": "GAF High Solids Silicone 55 Gal",
                "editable_decision_value": "GAF High Solids Silicone 55 Gal",
                "decision_values": {"gal_per_100_sqft": 1.5},
                "editable_basis_sqft": 10000,
                "default_basis_sqft": 10000,
                "historical_qty_per_sqft": 0.015,
                "editable_qty_per_sqft": 0.015,
                "unit": "gal",
                "current_unit_price": 42,
                "evidence_count": 11,
                "decision_evidence_count": 11,
                "decision_source_jobs_count": 9,
                "decision_confidence": "high",
                "confidence": "high",
                "product_id": "prod-gaf-silicone",
                "product_manufacturer": "GAF",
                "product_guidance": "Use as silicone roof coating.",
                "product_warnings": ["Do not apply over wet substrate."],
                "product_source_documents": ["gaf_silicone_pds.pdf"],
                "notes": "Historical default from 11 roofing jobs.",
            }
        ],
        "labor": [
            {
                "include": True,
                "decision_id": "roofing_labor_base",
                "template_bucket": "labor_base",
                "package_key": "labor_base",
                "labor_package": "Base Coat",
                "workbook_row": "122",
                "historical_recommendation": "Historical labor_base decision from 8 jobs. days=2, crew_size=4",
                "calculated_output_summary": "hours=60, cost=4320",
                "row_traceability": "Estimate row 122",
                "recommended_decision_value": "mixed_formula",
                "editable_decision_value": "mixed_formula",
                "decision_values": {"days": 2, "crew_size": 4},
                "historical_hours_per_1000_sqft": 5,
                "editable_hours_per_1000_sqft": 6,
                "crew_size": 4,
                "labor_rate": 72,
                "evidence_count": 8,
                "decision_evidence_count": 8,
                "decision_source_jobs_count": 8,
                "decision_confidence": "medium",
                "confidence": "medium",
                "notes": "Historical labor default.",
            }
        ],
        "adders": [
            {
                "include": False,
                "adder_key": "lift",
                "template_bucket": "lift",
                "workbook_row": "47",
                "adder": "Lift",
                "historical_default_value": 1200,
                "editable_value": 1200,
                "evidence_count": 4,
                "confidence": "medium",
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
        "material_rows": [
            {
                "decision_id": "roofing_coating_system",
                "template_bucket": "coating",
                "workbook_row": "26",
                "row_traceability": "Estimate rows 26-28",
                "item": "GAF High Solids Silicone 55 Gal",
                "category": "coating",
                "quantity": 150,
                "unit": "gal",
                "unit_price": 42,
                "estimated_cost": 6300,
            }
        ],
        "labor_rows": [
            {"task": "labor_base", "crew_size": 4, "total_hours": 60, "adjusted_days": 1.875, "estimated_cost": 4320}
        ],
        "travel_rows": [],
        "adders_review_rows": [],
    }


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
            "section": "labor.labor_base",
            "field_name": "editable_hours_per_1000_sqft",
            "package_or_labor_task": "labor_base",
            "suggested_value": 5,
            "final_value": 6,
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
        calculated_outputs={"totals": {"draft_total": 10620}, "draft_workbook_inputs": workbook_inputs},
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
    assert payload["review"]["final_decisions"]["decisions"][0]["decision_id"] == "roofing_coating_system"
    assert payload["review"]["final_decisions"]["decisions"][0]["final_value"] == "GAF High Solids Silicone 55 Gal"
    assert payload["review"]["final_decisions"]["decisions"][0]["source_evidence"]["decision_source_tables"] == "roofing_coating_decision_history"
    assert payload["review"]["final_decisions"]["decisions"][0]["product_guidance_snapshot"]["source_documents"] == ["gaf_silicone_pds.pdf"]
    assert payload["review"]["calculated_outputs"]["totals"]["draft_total"] == 10620
    material_write = next(row for row in payload["review"]["workbook_cell_writes"] if row.get("section") == "materials")
    assert material_write["decision_id"] == "roofing_coating_system"

    zip_path = export_estimator_session_package(engine, session_id, tmp_path / "session_review.zip")
    assert zip_path.exists()
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
        assert {
            "session_review.json",
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

    jsonl_path = export_training_dataset(engine, tmp_path / "training.jsonl")
    rows = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["raw_input_notes"] == "Roof coating notes from email."
    assert rows[0]["template_type"] == "roofing"
    assert rows[0]["division"] == "Roofing"
    assert rows[0]["estimator_edits"][0]["field_name"] == "editable_hours_per_1000_sqft"
    assert rows[0]["proposed_decisions"][0]["decisions"][0]["decision_id"] == "roofing_coating_system"
    training_material_write = next(row for row in rows[0]["workbook_cell_writes"] if row.get("section") == "materials")
    assert training_material_write["row_traceability"] == "Estimate rows 26-28"
