from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from evals.estimator.run_staged_estimator_eval import (
    load_staged_cases,
    promote_reviewed_cases,
    run_staged_benchmark,
    run_staged_case,
    score_staged_state,
)
from jobscan.estimator.audit_report import build_estimate_audit_report
from jobscan.estimator.chat_assistant import (
    _call_openai_chat,
    _chat_prompt_messages,
    _json_character_count,
)
from jobscan.estimator.readiness import decision_edit_schema, evaluate_estimate_readiness
from jobscan.estimator.staged_session import (
    _call_review_model,
    _historical_jobs,
    _merge_decision,
    _review_prompt_messages,
    approve_estimate_session,
    confirm_estimate_assumption,
    merge_estimate_assumptions,
    new_estimate_session_state,
    update_estimate_decision,
)
from jobscan.estimator.template_examples import _compact_answer_key


def _ready_roofing_state() -> dict:
    state = new_estimate_session_state(template_type="roofing")
    state["scope_state"] = {
        "template_type": "roofing",
        "estimated_sqft": 5000,
    }
    state["decision_template_state"] = [
        {
            "decision_id": "roofing_coating_system_row_26",
            "section": "roofing_coating_template_decisions",
            "template_bucket": "coating",
            "workbook_row": "26",
            "include": True,
            "proposed_values": {
                "basis_sqft": 5000,
                "gal_per_100_sqft": 1.5,
                "unit_price": 42,
            },
            "source_type": "explicit_user_note",
            "source_ids": ["note-1"],
            "confidence": 0.92,
        }
    ]
    return state


def test_readiness_blocks_missing_workbook_inputs_and_questions() -> None:
    state = _ready_roofing_state()
    state["decision_template_state"][0]["proposed_values"].pop("unit_price")
    state["unresolved_questions"] = ["Confirm roof area."]

    readiness = evaluate_estimate_readiness(state)

    assert readiness["ready"] is False
    assert {
        row["code"]
        for row in readiness["hard_errors"]
    } == {"missing_formula_input", "unresolved_question"}
    with pytest.raises(ValueError, match="not ready"):
        approve_estimate_session(state)


def test_readiness_allows_warnings_but_blocks_bad_geometry() -> None:
    state = _ready_roofing_state()
    state["assumptions"] = [
        {
            "assumption_id": "assumption-1",
            "assumption": "Existing membrane is suitable for coating.",
            "financial_impact": "medium",
            "confirmed": False,
        }
    ]

    warning_only = evaluate_estimate_readiness(state)
    assert warning_only["ready"] is True
    assert warning_only["warnings"][0]["code"] == "unconfirmed_assumption"

    state["scope_state"].update(
        {
            "gross_wall_area_sqft": 1000,
            "opening_area_known_sqft": 1200,
        }
    )
    invalid = evaluate_estimate_readiness(state)
    assert invalid["ready"] is False
    assert any(
        row["code"] == "geometry_deduction_exceeds_area"
        for row in invalid["hard_errors"]
    )


def test_assumption_confirmation_persists_and_creates_learning_candidate() -> None:
    merged = merge_estimate_assumptions(
        [],
        [
            {
                "assumption": "Use normal access.",
                "financial_impact": "medium",
            }
        ],
    )
    state = _ready_roofing_state()
    state["assumptions"] = merged
    assumption_id = merged[0]["assumption_id"]

    confirmed = confirm_estimate_assumption(
        state,
        assumption_id=assumption_id,
        note="Estimator verified access.",
    )
    rerun_merge = merge_estimate_assumptions(
        confirmed["assumptions"],
        [{"assumption": "Use normal access.", "financial_impact": "medium"}],
    )

    assert confirmed["assumptions"][0]["confirmed"] is True
    assert confirmed["audit_events"][-1]["event_type"] == "assumption_confirmed"
    assert confirmed["learning_candidates"][-1]["candidate_type"] == "assumption_review"
    assert rerun_merge[0]["confirmed"] is True


def test_rejected_assumption_blocks_approval_until_corrected() -> None:
    state = _ready_roofing_state()
    state["assumptions"] = merge_estimate_assumptions(
        [],
        [{"assumption": "No primer is required.", "financial_impact": "medium"}],
    )

    rejected = confirm_estimate_assumption(
        state,
        assumption_id=state["assumptions"][0]["assumption_id"],
        confirmed=False,
    )

    assert rejected["readiness_state"]["ready"] is False
    assert rejected["readiness_state"]["hard_errors"][0]["code"] == (
        "rejected_assumption_requires_correction"
    )


def test_structured_decision_edit_and_accept_are_audited() -> None:
    state = _ready_roofing_state()

    edited = update_estimate_decision(
        state,
        decision_id="roofing_coating_system_row_26",
        include=True,
        proposed_values={
            "basis_sqft": 5200,
            "gal_per_100_sqft": 1.5,
            "unit_price": 42,
        },
        reason="Estimator corrected takeoff.",
        action="edit",
    )
    accepted = update_estimate_decision(
        edited,
        decision_id="roofing_coating_system_row_26",
        reason="Inputs verified.",
        action="accept",
    )

    decision = accepted["decision_template_state"][0]
    assert decision["proposed_values"]["basis_sqft"] == 5200
    assert decision["review_status"] == "accepted"
    assert decision["review_required"] is False
    assert edited["learning_candidates"][-1]["candidate_type"] == "decision_correction"
    assert accepted["audit_events"][-1]["event_type"] == "decision_accepted"


def test_decision_edit_schema_uses_workbook_decision_metadata() -> None:
    state = _ready_roofing_state()

    schema = decision_edit_schema(state, state["decision_template_state"][0])

    assert [row["field"] for row in schema["fields"]] == [
        "basis_sqft",
        "gal_per_100_sqft",
        "unit_price",
        "waste_factor_pct",
    ]
    assert all(row["input_type"] == "number" for row in schema["fields"])
    assert schema["formula_requirements"] == [
        "basis_sqft",
        "gal_per_100_sqft",
        "unit_price",
    ]


def test_later_model_patch_clears_stale_decision_acceptance() -> None:
    base = {
        "decision_id": "coating",
        "include": True,
        "proposed_values": {"basis_sqft": 5000},
        "review_status": "accepted",
        "accepted_at": "2026-07-27T12:00:00+00:00",
    }

    updated = _merge_decision(
        base,
        {"decision_id": "coating", "proposed_values": {"basis_sqft": 5500}},
    )

    assert updated["review_status"] == "proposed"
    assert "accepted_at" not in updated


def test_approval_records_readiness_snapshot() -> None:
    approved = approve_estimate_session(_ready_roofing_state(), actor="estimator-1")

    assert approved["session_status"] == "approved"
    assert approved["approved_by"] == "estimator-1"
    assert approved["audit_events"][-1]["readiness"]["ready"] is True


def test_historical_precedent_preserves_value_and_operating_assumptions() -> None:
    rows = _historical_jobs(
        {
            "historical_answer_key_examples": {
                "matched_answer_keys": [
                    {
                        "job_id": "job-1",
                        "job_name": "Comparable roof",
                        "area_sqft": 5000,
                        "quoted_value": 87500,
                        "contract_value": 86000,
                        "material_assumptions": [{"system": "silicone"}],
                        "labor_assumptions": [{"crew_size": 5}],
                        "source_url": "https://example.invalid/job-1",
                    }
                ]
            }
        },
        [],
    )

    assert rows[0]["quoted_value"] == 87500
    assert rows[0]["final_value"] == 86000
    assert rows[0]["material_assumptions"] == [{"system": "silicone"}]
    assert rows[0]["labor_assumptions"] == [{"crew_size": 5}]


def test_audit_report_includes_readiness_and_learning_candidates() -> None:
    state = _ready_roofing_state()
    state["readiness_state"] = evaluate_estimate_readiness(state)
    state["learning_candidates"] = [{"candidate_id": "candidate-1", "status": "pending"}]

    report = build_estimate_audit_report(state)

    assert report["readiness"]["ready"] is True
    assert report["learning_candidates"][0]["candidate_id"] == "candidate-1"


def test_only_explicitly_reviewed_cases_are_promoted(tmp_path) -> None:
    source_path = tmp_path / "cases.jsonl"
    source_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "case_id": "reviewed",
                        "generated_notes": "Reviewed notes.",
                        "template_type": "roofing",
                        "expected_scope_fields": {"estimated_sqft": 5000},
                        "promotion_status": "approved",
                    }
                ),
                json.dumps(
                    {
                        "case_id": "pending",
                        "generated_notes": "Pending notes.",
                        "template_type": "roofing",
                        "promotion_status": "needs_review",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    cases = load_staged_cases(source_path)
    output_path = tmp_path / "curated.json"

    report = promote_reviewed_cases(cases, output_path)
    persisted = json.loads(output_path.read_text(encoding="utf-8"))
    reloaded = load_staged_cases(output_path)

    assert report["case_count"] == 1
    assert persisted["cases"][0]["case_id"] == "reviewed"
    assert persisted["benchmark_status"] == "curated"
    assert reloaded[0]["source_metadata"]["promotion_status"] == "approved"
    assert reloaded[0]["expected_scope"]["estimated_sqft"] == 5000


def test_required_only_benchmark_does_not_penalize_unspecified_decisions() -> None:
    case = {
        "expected_scope": {},
        "expected_decisions": [
            {
                "template_bucket": "coating",
                "line_item_kind": "material",
                "expected_include": True,
            }
        ],
        "source_metadata": {"selection_policy": "required_only"},
    }
    state = {
        "decision_template_state": [
            {"template_bucket": "coating", "include": True},
            {"template_bucket": "sales_trips", "include": True},
        ]
    }

    score = score_staged_state(case, state)

    assert score["metrics"]["template_selection_precision"] is None
    assert score["metrics"]["template_selection_recall"] == 1.0
    assert score["metrics"]["template_selection_f1"] == 1.0
    assert score["differences"]["unexpected_decision_buckets"] == []


def test_staged_case_report_includes_model_diagnostics() -> None:
    case = {
        "case_id": "diagnostic",
        "notes": "Coat a 5,000 sqft metal roof.",
        "template_type": "roofing",
        "expected_scope": {"estimated_sqft": 5000},
        "expected_decisions": [
            {
                "template_bucket": "coating",
                "line_item_kind": "material",
                "expected_include": True,
            }
        ],
        "source_metadata": {"selection_policy": "required_only"},
    }

    result = run_staged_case(
        case,
        model="test-model",
        provider=lambda _messages, _model: {
            "assistant_message": "Drafted the roof estimate.",
            "estimator_notes": "Metal roof coating.",
            "scope_overrides": {
                "template_type": "roofing",
                "division": "Roofing",
                "estimated_sqft": 5000,
            },
            "workbook_decision_preferences": [
                {
                    "decision_id": "roofing_coating_system_row_26",
                    "template_bucket": "coating",
                    "include": True,
                    "proposed_values": {
                        "basis_sqft": 5000,
                        "gal_per_100_sqft": 1.5,
                        "unit_price": 42,
                    },
                }
            ],
            "confidence": 0.8,
            "_model_call": {
                "role": "estimator",
                "model": "test-model",
                "input_tokens": 100,
                "output_tokens": 20,
            },
        },
    )

    assert result["session_summary"]["response_source"] == "ai_chat"
    assert result["session_summary"]["assistant_message"] == (
        "Drafted the roof estimate."
    )
    assert result["session_summary"]["decision_count"] == 1
    assert result["score"]["metrics"]["template_selection_recall"] == 1.0
    assert result["session_summary"]["model_call_history"][0]["input_tokens"] == 100


def test_estimator_prompt_context_is_bounded_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_ESTIMATOR_MAX_INPUT_CHARACTERS", "30000")
    context = {
        "historical_answer_key_examples": {
            "matched_answer_keys": [
                {
                    "example_id": f"example-{index}",
                    "decisions": [
                        {
                            "decision_id": f"decision-{decision_index}",
                            "evidence": {"raw_text": "x" * 5000},
                        }
                        for decision_index in range(40)
                    ],
                }
                for index in range(5)
            ]
        }
    }

    messages = _chat_prompt_messages(
        [{"role": "user", "content": "Draft the estimate."}],
        template_type_hint="roofing",
        existing_scope={},
        existing_decisions=[],
        existing_session_state={},
        context=context,
    )
    prompt_payload = json.loads(messages[1]["content"])
    budget = prompt_payload["estimator_context"]["_prompt_context_budget"]

    assert _json_character_count(messages) <= 30000
    assert budget["truncated"] is True
    assert "historical_answer_key_examples" in budget["omitted_keys"]


def test_model_calls_block_oversized_prompts_before_api_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict] = []

    class FakeResponses:
        def create(self, **kwargs):
            requests.append(kwargs)
            return SimpleNamespace(output_text="{}", usage={})

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.responses = FakeResponses()

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    monkeypatch.setenv("OPENAI_ESTIMATOR_MAX_INPUT_CHARACTERS", "100")
    monkeypatch.setenv("OPENAI_REVIEW_MAX_INPUT_CHARACTERS", "100")
    oversized = [{"role": "user", "content": "x" * 1000}]

    with pytest.raises(ValueError, match="blocked before API dispatch"):
        _call_openai_chat(oversized, "test-estimator")
    with pytest.raises(ValueError, match="blocked before API dispatch"):
        _call_review_model(oversized, "test-review")

    assert requests == []


def test_review_prompt_context_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_REVIEW_MAX_INPUT_CHARACTERS", "12000")
    state = {
        "decision_template_state": [
            {
                "decision_id": f"decision-{index}",
                "evidence": "x" * 2000,
            }
            for index in range(30)
        ],
        "historical_comparison": [{"raw_text": "y" * 30000}],
    }

    messages = _review_prompt_messages(state, ["independent review"])
    prompt_payload = json.loads(messages[1]["content"])

    assert _json_character_count(messages) <= 12000
    assert prompt_payload["_prompt_context_budget"]["truncated"] is True


def test_compact_answer_key_omits_raw_evidence_text() -> None:
    compact = _compact_answer_key(
        {
            "decisions": [
                {
                    "decision_id": "decision-1",
                    "template_bucket": "coating",
                    "inputs": {
                        "basis_sqft": 5000,
                        "gal_per_100_sqft": 1.5,
                        "unit_price": 42,
                    },
                    "evidence": {
                        "source": "workbook",
                        "source_row": 26,
                        "raw_text": "x" * 50000,
                    },
                }
            ]
        }
    )

    assert compact["decisions"][0]["evidence"] == {
        "source": "workbook",
        "source_row": 26,
    }


def test_compact_answer_key_omits_unused_material_and_labor_rows() -> None:
    compact = _compact_answer_key(
        {
            "decisions": [
                {
                    "decision_id": "used-coating",
                    "template_bucket": "coating",
                    "inputs": {
                        "basis_sqft": 5000,
                        "gal_per_100_sqft": 1.5,
                        "unit_price": 42,
                    },
                },
                {
                    "decision_id": "unused-primer",
                    "template_bucket": "primer",
                    "inputs": {"basis_sqft": 0, "unit_price": 40},
                },
                {
                    "decision_id": "unused-labor",
                    "template_bucket": "labor_base",
                    "section": "roofing_labor_template_decisions",
                    "inputs": {
                        "days": 0,
                        "crew_size": 5,
                        "daily_rate": 2000,
                    },
                },
                {
                    "decision_id": "used-labor",
                    "template_bucket": "labor_base",
                    "section": "roofing_labor_template_decisions",
                    "inputs": {
                        "days": 2,
                        "crew_size": 5,
                        "daily_rate": 2000,
                    },
                },
            ]
        }
    )

    assert [row["decision_id"] for row in compact["decisions"]] == [
        "used-coating",
        "used-labor",
    ]
    assert compact["summary"]["omitted_inactive_decision_count"] == 2


def test_pro_estimator_prompt_uses_stricter_input_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_ESTIMATOR_MAX_INPUT_CHARACTERS", "100000")
    monkeypatch.setenv("OPENAI_ESTIMATOR_PRO_MAX_INPUT_CHARACTERS", "20000")
    messages = _chat_prompt_messages(
        [{"role": "user", "content": "Draft the estimate."}],
        template_type_hint="roofing",
        existing_scope={},
        existing_decisions=[],
        existing_session_state={},
        context={"historical_answer_key_examples": {"raw_text": "x" * 100000}},
        model="gpt-5.5-pro",
    )

    assert _json_character_count(messages) <= 20000


def test_persistent_estimator_models_use_responses_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict] = []

    class FakeResponses:
        def create(self, **kwargs):
            requests.append(kwargs)
            return SimpleNamespace(
                output_text='{"assistant_message":"ok"}',
                usage={"input_tokens": 10, "output_tokens": 4},
                id="response-1",
                model=kwargs["model"],
            )

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.responses = FakeResponses()

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    monkeypatch.setenv("OPENAI_ESTIMATOR_REASONING_EFFORT", "medium")
    monkeypatch.setenv("OPENAI_ESTIMATOR_MAX_OUTPUT_TOKENS", "321")
    monkeypatch.setenv("OPENAI_REVIEW_MAX_OUTPUT_TOKENS", "123")

    estimator = _call_openai_chat(
        [{"role": "user", "content": "Draft estimate."}],
        "test-estimator",
    )
    review = _call_review_model(
        [{"role": "user", "content": "Review estimate."}],
        "test-review",
    )

    assert estimator["assistant_message"] == "ok"
    assert review["assistant_message"] == "ok"
    assert len(requests) == 2
    assert all(
        request["text"] == {"format": {"type": "json_object"}}
        for request in requests
    )
    assert all(
        request["reasoning"] == {"effort": "medium"}
        for request in requests
    )
    assert [request["model"] for request in requests] == [
        "test-estimator",
        "test-review",
    ]
    assert [request["max_output_tokens"] for request in requests] == [321, 123]


def test_staged_benchmark_stops_after_terminal_model_error() -> None:
    cases = [
        {
            "case_id": f"case-{index}",
            "notes": "Coat a 5,000 sqft roof.",
            "template_type": "roofing",
            "expected_scope": {"estimated_sqft": 5000},
            "expected_decisions": [],
            "source_metadata": {},
        }
        for index in range(2)
    ]

    report = run_staged_benchmark(
        cases,
        models=["test-model"],
        provider=lambda _messages, _model: (_ for _ in ()).throw(
            RuntimeError("insufficient_quota")
        ),
    )
    comparison = report["comparisons"][0]

    assert len(report["results"]) == 1
    assert comparison["attempted_case_count"] == 1
    assert comparison["successful_model_case_count"] == 0
    assert comparison["fallback_case_count"] == 1
    assert comparison["unattempted_case_count"] == 1
    assert comparison["mean_model_score"] is None
