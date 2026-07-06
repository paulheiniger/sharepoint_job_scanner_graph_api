from __future__ import annotations

from jobscan.estimator.decision_proposals import (
    DecisionProposal,
    apply_decision_proposals_to_workbench,
    build_decision_proposals,
    merge_decision_proposals,
)


def test_explicit_note_proposal_includes_coating_rows_with_review_evidence() -> None:
    proposals = build_decision_proposals(
        {
            "template_type": "roofing",
            "project_type": "roof coating",
            "estimated_sqft": 45570.2,
            "coating_required": True,
            "coating_path_review": True,
            "raw_input_notes": "Metal roof/coating restoration seems possible; review before committing to warranty.",
        }
    )

    coating = [row for row in proposals if row["template_bucket"] == "coating"]

    assert {row["workbook_row"] for row in coating} == {"26"}
    assert all(row["include"] is True for row in coating)
    assert all(row["review_required"] is True for row in coating)
    assert all(row["evidence"]["note"] for row in coating)
    assert all(row["proposed_values"]["basis_sqft"] == 45570.2 for row in coating)


def test_weak_ai_only_proposal_is_review_marked() -> None:
    proposals = build_decision_proposals(
        {"template_type": "roofing", "project_type": "roof repair"},
        recommendation={"debug": {"ai_scope_interpreter": {"ai_parsed_scope": {"scope_packages": {"coating": True}}}}},
    )

    coating = [row for row in proposals if row["template_bucket"] == "coating"]

    assert coating
    assert all(row["source"] == "ai_scope" for row in coating)
    assert all(row["review_required"] is True for row in coating)
    assert all(row["confidence"] < 0.5 for row in coating)


def test_historical_only_warranty_is_not_invented_without_prompt_evidence() -> None:
    proposals = build_decision_proposals(
        {
            "template_type": "roofing",
            "project_type": "roof coating",
            "estimated_sqft": 10000,
            "coating_required": True,
            "raw_input_notes": "Coating path if the roof qualifies.",
        }
    )

    assert proposals
    assert not any("warranty_years" in (row.get("proposed_values") or {}) for row in proposals)
    assert any("Warranty duration was not stated." in row.get("review_reasons", []) for row in proposals)


def test_duplicate_proposals_merge_by_precedence_and_evidence() -> None:
    proposals = merge_decision_proposals(
        [
            DecisionProposal(
                decision_id="roofing_coating_system_row_26",
                template_type="roofing",
                section="roofing_coating_template_decisions",
                template_bucket="coating",
                workbook_row="26",
                include=True,
                proposed_values={"basis_sqft": 9000},
                confidence=0.4,
                source="ai_scope",
                review_required=True,
                review_reasons=["AI-only proposal requires review."],
                evidence={"note": [{"text": "AI coating"}]},
            ),
            DecisionProposal(
                decision_id="roofing_coating_system_row_26",
                template_type="roofing",
                section="roofing_coating_template_decisions",
                template_bucket="coating",
                workbook_row="26",
                include=True,
                proposed_values={"basis_sqft": 10000},
                confidence=0.9,
                source="explicit_note",
                evidence={"note": [{"text": "Customer requested coating."}]},
            ),
        ]
    )

    assert len(proposals) == 1
    assert proposals[0]["source"] == "explicit_note"
    assert proposals[0]["proposed_values"]["basis_sqft"] == 10000
    assert proposals[0]["review_required"] is True
    assert len(proposals[0]["evidence"]["note"]) == 2


def test_apply_proposals_dedupes_rows_and_attaches_product_and_formula_evidence() -> None:
    workbench = {
        "scope": {"template_type": "roofing"},
        "roofing_coating_template_decisions": [
            {
                "include": True,
                "section": "roofing_coating_template_decisions",
                "decision_id": "roofing_coating_system_row_26",
                "template_bucket": "coating",
                "workbook_row": "26",
                "formula_model": "roofing_coating",
                "product_id": "prod-1",
            },
            {
                "include": True,
                "section": "roofing_coating_template_decisions",
                "decision_id": "roofing_coating_system_row_26",
                "template_bucket": "coating",
                "workbook_row": "26",
                "formula_model": "roofing_coating",
            },
        ],
    }
    proposals = [
        DecisionProposal(
            decision_id="roofing_coating_system_row_26",
            template_type="roofing",
            section="roofing_coating_template_decisions",
            template_bucket="coating",
            workbook_row="26",
            include=True,
            evidence={"note": [{"text": "coating"}]},
        )
    ]

    updated = apply_decision_proposals_to_workbench(
        workbench,
        proposals,
        decision_sections=("roofing_coating_template_decisions",),
    )

    rows = updated["roofing_coating_template_decisions"]
    assert len(rows) == 1
    assert updated["duplicate_decision_rows"]
    assert rows[0]["proposal_evidence"]["note"]
    assert rows[0]["decision_evidence_summary"] == "note evidence, product guidance, formula preview"
    assert rows[0]["decision_evidence_types"] == "note, product, formula"
    assert rows[0]["why_included"] == "Included by deterministic rule"
    assert rows[0]["product_evidence_summary"] == "prod-1"
    assert rows[0]["formula_evidence_summary"] == "roofing_coating"
