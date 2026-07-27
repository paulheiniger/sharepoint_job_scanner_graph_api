from __future__ import annotations

from jobscan.estimator.staged_session import (
    advance_estimate_session,
    merge_decision_patches,
    new_estimate_session_state,
    reject_historical_precedent,
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
    foam = next(row for row in updated["decision_template_state"] if row["decision_id"] == "insulation_foam_template_selector")
    barrier = next(row for row in updated["decision_template_state"] if row["decision_id"] == "insulation_thermal_barrier")
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
