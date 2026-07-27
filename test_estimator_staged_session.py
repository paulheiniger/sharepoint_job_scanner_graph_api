from __future__ import annotations

from jobscan.estimator.staged_session import (
    _decision_calculation_changed,
    advance_estimate_session,
    apply_review_recommendations,
    attach_approved_memory_evidence,
    latest_correction_memory_edits,
    merge_decision_patches,
    new_estimate_session_state,
    recalculate_dependent_decisions,
    reject_historical_precedent,
    run_estimate_review,
)


def test_calculation_change_filter_ignores_evidence_only_updates() -> None:
    before = {
        "decision_id": "foam",
        "include": True,
        "proposed_values": {"estimated_units": 3},
        "evidence": [],
    }

    assert not _decision_calculation_changed(
        {
            "before": before,
            "after": {
                **before,
                "evidence": [{"source_type": "approved_memory", "memory_id": "memory-1"}],
                "confidence": 0.92,
            },
        }
    )
    assert _decision_calculation_changed(
        {
            "before": before,
            "after": {
                **before,
                "proposed_values": {"estimated_units": 4},
            },
        }
    )


def test_decision_patch_preserves_unaffected_decisions() -> None:
    existing = [
        {
            "decision_id": "foam",
            "include": True,
            "proposed_values": {"foam_type": "open_cell", "estimated_units": 2.5},
            "confidence": 0.8,
        },
        {
            "decision_id": "thermal_barrier",
            "include": True,
            "proposed_values": {"basis_sqft": 1200},
        },
    ]

    merged, changes = merge_decision_patches(
        existing,
        [
            {
                "decision_id": "foam",
                "proposed_values": {"foam_type": "closed_cell"},
                "source": "explicit_user_note",
            }
        ],
    )

    assert len(merged) == 2
    foam = next(row for row in merged if row["decision_id"] == "foam")
    barrier = next(row for row in merged if row["decision_id"] == "thermal_barrier")
    assert foam["proposed_values"] == {"foam_type": "closed_cell", "estimated_units": 2.5}
    assert barrier == existing[1]
    assert [row["decision_id"] for row in changes] == ["foam"]


def test_staged_turn_patches_existing_decision_state() -> None:
    state = new_estimate_session_state(template_type="insulation")
    state["scope_state"] = {
        "template_type": "insulation",
        "division": "Insulation",
        "estimated_sqft": 2200,
    }
    state["decision_template_state"] = [
        {
            "decision_id": "insulation_foam_template_selector",
            "template_bucket": "foam",
            "include": True,
            "proposed_values": {
                "foam_type": "open_cell",
                "basis_sqft": 2200,
                "thickness_inches": 5.5,
                "yield_or_coverage": 4500,
                "unit_price": 1.6,
            },
        },
        {
            "decision_id": "insulation_thermal_barrier",
            "template_bucket": "thermal_barrier_coating",
            "include": True,
            "proposed_values": {"basis_sqft": 2200, "unit_price": 0.9},
        },
    ]
    state["conversation_history"] = [{"role": "user", "content": "Initial metal building notes."}]

    def provider(messages, model):
        payload = messages[-1]["content"]
        assert "current_decision_template_state" in payload
        return {
            "assistant_message": "Updated foam chemistry only.",
            "estimator_notes": "Use closed cell foam.",
            "scope_overrides": {
                "template_type": "insulation",
                "division": "Insulation",
                "foam_type": "closed_cell",
            },
            "workbook_decision_preferences": [
                {
                    "decision_id": "insulation_foam_template_selector",
                    "template_bucket": "foam",
                    "include": True,
                    "proposed_values": {
                        "foam_type": "closed_cell",
                        "basis_sqft": 2200,
                        "thickness_inches": 2.0,
                        "yield_or_coverage": 3100,
                        "unit_price": 2.25,
                    },
                    "source": "explicit_user_note",
                    "confidence": 0.95,
                }
            ],
            "missing_questions": [],
            "assumptions": [],
            "warnings": [],
            "confidence": 0.9,
        }

    _, updated = advance_estimate_session(
        [
            {"role": "user", "content": "Initial metal building notes."},
            {"role": "assistant", "content": "Drafted open cell."},
            {"role": "user", "content": "Use closed cell instead."},
        ],
        previous_state=state,
        template_type_hint="insulation",
        provider=provider,
        model="test-estimator",
    )

    assert len(updated["decision_template_state"]) == 2
    foam = next(
        row
        for row in updated["decision_template_state"]
        if row["decision_id"] == "insulation_foam_template_selector"
    )
    barrier = next(
        row
        for row in updated["decision_template_state"]
        if row["decision_id"] == "insulation_thermal_barrier"
    )
    assert foam["proposed_values"]["foam_type"] == "closed_cell"
    assert barrier["include"] is True
    assert updated["current_stage"] == "conversational_revision"
    assert updated["decision_change_history"][-1]["changes"][0]["decision_id"] == "insulation_foam_template_selector"


def test_rejected_precedent_is_retained_and_removed_from_active_results() -> None:
    state = new_estimate_session_state()
    state["retrieved_historical_jobs"] = [
        {"precedent_id": "job-1", "job_name": "Wrong job"},
        {"precedent_id": "job-2", "job_name": "Useful job"},
    ]
    state["historical_comparison"] = [
        {"precedent_id": "job-1"},
        {"precedent_id": "job-2"},
    ]

    updated = reject_historical_precedent(state, precedent_id="job-1", reason="Different substrate.")

    assert [row["precedent_id"] for row in updated["retrieved_historical_jobs"]] == ["job-2"]
    assert updated["rejected_precedents"][0]["precedent_id"] == "job-1"
    assert state["rejected_precedents"] == []


def test_approved_memory_is_attached_only_to_matching_decision() -> None:
    decisions = [
        {"decision_id": "foam", "template_bucket": "foam", "evidence": []},
        {"decision_id": "labor", "template_bucket": "labor_foam", "evidence": []},
    ]
    memories = [
        {
            "memory_id": "memory-foam",
            "template_bucket": "foam",
            "guidance": "Use field yield rather than theoretical yield.",
            "rationale": "Approved correction.",
        },
        {
            "memory_id": "memory-primer",
            "template_bucket": "primer",
            "guidance": "Primer rule.",
        },
    ]

    updated, used = attach_approved_memory_evidence(decisions, memories)

    assert [row["memory_id"] for row in used] == ["memory-foam"]
    assert updated[0]["evidence"][0]["source_type"] == "approved_memory"
    assert updated[0]["source_ids"] == ["memory-foam"]
    assert updated[1]["evidence"] == []


def test_dependency_recalculation_preserves_unaffected_snapshot() -> None:
    decisions = [
        {
            "decision_id": "foam",
            "template_bucket": "foam",
            "include": True,
            "proposed_values": {"basis_sqft": 1000, "thickness_inches": 2, "yield_or_coverage": 3000},
        },
        {
            "decision_id": "labor",
            "template_bucket": "labor_foam",
            "include": True,
            "proposed_values": {},
        },
        {
            "decision_id": "unrelated",
            "template_bucket": "lift",
            "include": True,
            "calculated_outputs": {"sentinel": "keep"},
        },
    ]
    previous = {
        "decision_outputs": {
            "foam": {"old": True},
            "labor": {"old": True},
            "unrelated": {"sentinel": "keep"},
        }
    }

    updated, calculation = recalculate_dependent_decisions(
        scope={"template_type": "insulation", "division": "Insulation", "estimated_sqft": 1000},
        decisions=decisions,
        decision_changes=[{"decision_id": "foam"}],
        scope_changes=[],
        previous_calculation_state=previous,
    )

    assert calculation["affected_decision_ids"] == ["foam", "labor"]
    assert next(row for row in updated if row["decision_id"] == "unrelated")["calculated_outputs"] == {
        "sentinel": "keep"
    }


def test_review_model_is_advisory_until_patch_is_applied() -> None:
    state = new_estimate_session_state(template_type="insulation")
    state["scope_state"] = {"template_type": "insulation", "estimated_sqft": 1000}
    state["decision_template_state"] = [
        {
            "decision_id": "foam",
            "template_bucket": "foam",
            "include": True,
            "proposed_values": {"estimated_units": 2.5},
        },
        {
            "decision_id": "thermal_barrier",
            "template_bucket": "thermal_barrier_coating",
            "include": False,
            "proposed_values": {},
        },
    ]

    def provider(messages, model):
        assert model == "review-test-model"
        assert "current_decisions" in messages[-1]["content"]
        return {
            "verdict": "needs_changes",
            "summary": "Carry three foam sets.",
            "confidence": 0.9,
            "issues": [
                {
                    "severity": "medium",
                    "decision_id": "foam",
                    "issue": "Field yield supports rounding up.",
                    "evidence": [{"source_type": "historical_estimate", "source_id": "job-1"}],
                    "recommended_patch": {
                        "decision_id": "foam",
                        "template_bucket": "foam",
                        "proposed_values": {"estimated_units": 3},
                        "source": "review_model",
                    },
                }
            ],
        }

    review = run_estimate_review(
        state,
        provider=provider,
        model="review-test-model",
        user_requested=True,
    )

    assert review["verdict"] == "needs_changes"
    assert state["decision_template_state"][0]["proposed_values"]["estimated_units"] == 2.5

    updated = apply_review_recommendations(state)
    foam = next(row for row in updated["decision_template_state"] if row["decision_id"] == "foam")
    barrier = next(row for row in updated["decision_template_state"] if row["decision_id"] == "thermal_barrier")
    assert foam["proposed_values"]["estimated_units"] == 3
    assert barrier["include"] is False
    assert updated["review_state"]["applied"] is True


def test_latest_correction_becomes_pending_memory_edit_rows() -> None:
    state = new_estimate_session_state()
    state["decision_change_history"] = [
        {
            "changes": [
                {
                    "decision_id": "foam",
                    "before": {
                        "decision_id": "foam",
                        "section": "insulation_foam_template_decisions",
                        "template_bucket": "foam",
                        "include": True,
                        "proposed_values": {"estimated_units": 2.5},
                    },
                    "after": {
                        "decision_id": "foam",
                        "section": "insulation_foam_template_decisions",
                        "template_bucket": "foam",
                        "include": True,
                        "proposed_values": {"estimated_units": 3},
                    },
                }
            ]
        }
    ]

    edits = latest_correction_memory_edits(state)

    assert len(edits) == 1
    assert edits[0]["decision_id"] == "foam"
    assert edits[0]["field_name"] == "estimated_units"
    assert edits[0]["suggested_value"] == 2.5
    assert edits[0]["final_value"] == 3
