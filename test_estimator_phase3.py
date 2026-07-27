from __future__ import annotations

import json

from evals.estimator.run_staged_estimator_eval import (
    load_staged_cases,
    run_staged_benchmark,
)
from jobscan.estimator.audit_report import build_estimate_audit_report
from jobscan.estimator.model_routing import (
    configured_estimator_models,
    normalize_model_usage,
    route_estimator_model,
)
from jobscan.estimator.staged_session import (
    advance_estimate_session,
    build_staged_visual_evidence,
)


def test_model_routing_uses_role_specific_environment(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_EXTRACTION_MODEL", "extract-model")
    monkeypatch.setenv("OPENAI_ESTIMATOR_MODEL", "estimate-model")
    monkeypatch.setenv("OPENAI_REVIEW_MODEL", "review-model")

    assert configured_estimator_models() == {
        "extraction_model": "extract-model",
        "estimator_model": "estimate-model",
        "review_model": "review-model",
    }
    route = route_estimator_model(
        "review",
        state={"confidence_summary": {"overall": 0.4}},
    )

    assert route["model"] == "review-model"
    assert any("confidence" in reason.lower() for reason in route["reasons"])
    assert normalize_model_usage({"prompt_tokens": 10, "completion_tokens": 4}) == {
        "input_tokens": 10,
        "output_tokens": 4,
        "total_tokens": 14,
    }


def test_visual_evidence_preserves_annotated_scope_and_photo_proposals() -> None:
    visual = build_staged_visual_evidence(
        {
            "note_image_result": {
                "document_type": "annotated_aerial_takeoff",
                "source_images": ["image-1"],
                "confidence": 0.94,
                "job_header": {
                    "job_name": "Visual Roof",
                    "declared_total_area_sqft": 5136,
                },
                "area_scopes": [
                    {
                        "scope_id": "recover",
                        "label": "Recover area",
                        "area_sqft": 2016,
                        "confidence": 0.92,
                    }
                ],
                "linear_scopes": [
                    {"item": "edge metal", "linear_ft": 52, "confidence": 0.9}
                ],
                "questions": ["Confirm decking allowance."],
                "model_call": {
                    "role": "extraction",
                    "requested_model": "extract-model",
                    "request_id": "call-1",
                },
            },
            "photo_context": {
                "selected_image_ids": ["image-2"],
                "selected_hashes": ["hash-2"],
                "signals": ["open_seams"],
                "risk_flags": ["Wet insulation is not visible."],
                "photo_decision_proposals": [
                    {
                        "decision_id": "roofing_seams",
                        "template_bucket": "seams_misc",
                        "include": True,
                    }
                ],
            },
        }
    )

    assert visual["summary"]["evidence_count"] == 2
    assert visual["records"][0]["area_scopes"][0]["area_sqft"] == 2016
    assert any(row["field"] == "declared_total_area_sqft" for row in visual["job_facts"])
    assert visual["decision_patches"][0]["review_required"] is True
    assert visual["decision_patches"][0]["source"] == "photo_evidence"
    assert visual["model_calls"][0]["request_id"] == "call-1"


def test_staged_turn_persists_visual_evidence_and_photo_decision() -> None:
    def provider(messages, model):
        return {
            "assistant_message": "Drafted from annotated scope and photo evidence.",
            "estimator_notes": "Roof restoration.",
            "scope_overrides": {
                "template_type": "roofing",
                "division": "Roofing",
                "estimated_sqft": 5000,
            },
            "workbook_decision_preferences": [],
            "missing_questions": [],
            "assumptions": [],
            "warnings": [],
            "confidence": 0.85,
        }

    _result, state = advance_estimate_session(
        [{"role": "user", "content": "Use the uploaded scope."}],
        model="estimator-test",
        provider=provider,
        visual_evidence={
            "note_image_result": {
                "document_type": "annotated_aerial_takeoff",
                "source_images": ["annotated-1"],
                "job_header": {"declared_total_area_sqft": 5000},
                "confidence": 0.9,
            },
            "photo_context": {
                "selected_image_ids": ["photo-1"],
                "photo_decision_proposals": [
                    {
                        "decision_id": "roofing_primer",
                        "section": "roofing_primer_template_decisions",
                        "template_bucket": "primer",
                        "include": True,
                        "confidence": 0.7,
                    }
                ],
            },
        },
    )

    assert len(state["uploaded_evidence"]) == 2
    primer = next(
        row
        for row in state["decision_template_state"]
        if row["decision_id"] == "roofing_primer"
    )
    assert primer["source_type"] == "photo_evidence"
    assert primer["review_required"] is True
    assert state["audit_events"][-1]["visual_evidence_ids"]


def test_audit_report_summarizes_sources_changes_and_usage() -> None:
    report = build_estimate_audit_report(
        {
            "session_id": "session-1",
            "session_status": "approved",
            "approved_at": "2026-07-27T12:00:00+00:00",
            "raw_user_notes": "Coat 5,000 sq ft.",
            "job_facts": [{"field": "estimated_sqft", "value": 5000}],
            "decision_template_state": [
                {
                    "decision_id": "coating",
                    "template_bucket": "coating",
                    "include": True,
                    "source_type": "explicit_user_note",
                    "source_ids": ["note-1"],
                }
            ],
            "calculation_state": {"totals": {"draft_total": 50000}},
            "model_routes": [{"role": "estimator", "model": "estimate-model"}],
            "model_call_history": [
                {
                    "role": "estimator",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 25,
                        "total_tokens": 125,
                    },
                }
            ],
            "audit_events": [{"created_at": "2026-07-27T11:00:00+00:00", "event_type": "estimator_turn"}],
            "prompt_version": "staged-estimator-test",
        }
    )

    assert report["models"]["usage_totals"]["total_tokens"] == 125
    assert report["audit_completeness"]["ready_for_final_audit"] is True
    assert report["decisions"][0]["source_ids"] == ["note-1"]
    assert report["timeline"][0]["event_type"] == "estimator_turn"


def test_staged_benchmark_compares_models_and_scores_decisions(tmp_path) -> None:
    case_path = tmp_path / "cases.jsonl"
    case_path.write_text(
        json.dumps(
            {
                "case_id": "roof-case",
                "generated_notes": "Coat 5,000 sq ft with silicone.",
                "template_type": "roofing",
                "expected_scope_fields": {"estimated_sqft": 5000},
                "expected_decisions": [
                    {
                        "template_bucket": "coating",
                        "line_item_kind": "material",
                        "expected_include": True,
                        "selected_item_name": "Silicone",
                    },
                    {
                        "template_bucket": "labor_base",
                        "line_item_kind": "labor",
                        "expected_include": True,
                    },
                ],
                "promotion_status": "needs_review",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    cases = load_staged_cases(case_path)

    def provider(messages, model):
        if model == "strong-model":
            return {
                "assistant_message": "Complete draft.",
                "estimator_notes": "Coat 5,000 sq ft with silicone.",
                "scope_overrides": {
                    "template_type": "roofing",
                    "division": "Roofing",
                    "estimated_sqft": 5000,
                },
                "workbook_decision_preferences": [
                        {
                            "decision_id": "coating",
                            "section": "roofing_coating_template_decisions",
                            "template_bucket": "coating",
                            "workbook_row": "26",
                            "include": True,
                            "resolved_template_option": "Silicone",
                            "proposed_values": {
                                "basis_sqft": 5000,
                                "gal_per_100_sqft": 1.5,
                                "unit_price": 42,
                            },
                        },
                        {
                            "decision_id": "labor",
                            "section": "roofing_labor_template_decisions",
                            "template_bucket": "labor_base",
                            "workbook_row": "122",
                            "include": True,
                            "proposed_values": {
                                "days": 3,
                                "crew_size": 5,
                                "daily_rate": 2000,
                            },
                        },
                ],
                "missing_questions": [],
                "assumptions": [],
                "warnings": [],
                "confidence": 0.9,
            }
        return {
            "assistant_message": "Incomplete draft.",
            "estimator_notes": "Roof work.",
            "scope_overrides": {
                "template_type": "roofing",
                "division": "Roofing",
                "estimated_sqft": 2500,
            },
            "workbook_decision_preferences": [],
            "missing_questions": ["What is the area?"],
            "assumptions": [],
            "warnings": [],
            "confidence": 0.4,
        }

    report = run_staged_benchmark(
        cases,
        models=["weak-model", "strong-model"],
        provider=provider,
    )

    assert report["benchmark_status"] == "review_only"
    assert report["comparisons"][0]["model"] == "strong-model"
    assert report["comparisons"][0]["mean_score"] > report["comparisons"][1]["mean_score"]
    assert report["results"][1]["score"]["metrics"]["template_selection_f1"] == 1.0
