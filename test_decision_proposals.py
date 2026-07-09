from __future__ import annotations

from jobscan.estimator.decision_proposals import (
    DecisionProposal,
    apply_decision_proposals_to_workbench,
    build_decision_proposals,
    merge_decision_proposals,
)


def test_note_triggered_scope_rules_do_not_create_inclusion_proposals_by_default() -> None:
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

    assert coating == []


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


def test_estimator_chat_preferences_create_canonical_foam_proposal() -> None:
    proposals = build_decision_proposals(
        {
            "template_type": "insulation",
            "division": "Insulation",
            "estimated_sqft": 2226,
            "foam_type": "open_cell",
            "estimator_chat": {
                "source": "ai_chat",
                "confidence": 0.82,
                "assistant_message": "Use 5 inch open-cell foam for the metal building.",
                "workbook_decision_preferences": [
                    {
                        "decision_id": "insulation_foam_template_selector",
                        "template_bucket": "foam",
                        "include": True,
                        "proposed_values": {
                            "basis_sqft": 2226,
                            "thickness_inches": 5,
                            "yield_or_coverage": 4500,
                            "resolved_template_option": "Gaco 0.5 lb.",
                        },
                        "confidence": 0.82,
                    }
                ],
            },
        }
    )

    foam = next(row for row in proposals if row["template_bucket"] == "foam")

    assert foam["source"] == "chat_estimator"
    assert foam["workbook_row"] == "19-21"
    assert foam["proposed_values"]["thickness_inches"] == 5
    assert "yield_or_coverage" not in foam["proposed_values"]
    assert foam["evidence"]["chat_estimator"][0]["assistant_message"].startswith("Use 5 inch")


def test_estimator_chat_loading_travel_preferences_target_logistics_expense_rows() -> None:
    proposals = build_decision_proposals(
        {
            "template_type": "insulation",
            "division": "Insulation",
            "estimator_chat": {
                "source": "ai_chat",
                "confidence": 0.7,
                "assistant_message": "Use loading and travel.",
                "workbook_decision_preferences": [
                    {
                        "template_bucket": "labor_loading",
                        "workbook_row": "95",
                        "include": True,
                        "proposed_values": {"days": 1, "crew_size": 2, "daily_rate": 1685.775},
                    },
                    {
                        "template_bucket": "labor_traveling",
                        "workbook_row": "97",
                        "include": True,
                        "proposed_values": {"hours_per_day": 2.5, "people_count": 4, "unit_price": 13},
                    },
                ],
            },
        }
    )

    loading = next(row for row in proposals if row["template_bucket"] == "labor_loading")
    traveling = next(row for row in proposals if row["template_bucket"] == "labor_traveling")

    assert loading["section"] == "insulation_logistics_expense_template_decisions"
    assert loading["workbook_row"] == "95"
    assert loading["proposed_values"] == {"hours_per_day": 1, "people_count": 2, "unit_price": 25.5}
    assert traveling["section"] == "insulation_logistics_expense_template_decisions"
    assert traveling["workbook_row"] == "97"
    assert traveling["proposed_values"] == {"hours_per_day": 2.5, "people_count": 4, "unit_price": 13}


def test_estimator_chat_free_adder_preferences_target_roofing_free_adder_rows() -> None:
    proposals = build_decision_proposals(
        {
            "template_type": "roofing",
            "division": "Roofing",
            "estimator_chat": {
                "source": "ai_chat",
                "confidence": 0.82,
                "assistant_message": "Add warranty as a post-markup free row.",
                "workbook_decision_preferences": [
                    {
                        "section": "roofing_free_adder_template_decisions",
                        "decision_id": "roofing_free_adder_row_173_warranty",
                        "template_bucket": "warranty",
                        "workbook_row": "173",
                        "include": True,
                        "proposed_values": {
                            "template_line": "Warranty",
                            "amount": 600,
                            "estimated_cost": 600,
                            "markup_treatment": "post_markup",
                        },
                        "confidence": 0.82,
                    }
                ],
            },
        }
    )

    adder = next(row for row in proposals if row["section"] == "roofing_free_adder_template_decisions")

    assert adder["template_bucket"] == "warranty"
    assert adder["workbook_row"] == "173"
    assert adder["proposed_values"]["amount"] == 600
    assert adder["proposed_values"]["markup_treatment"] == "post_markup"


def test_estimator_chat_alias_only_loading_travel_preferences_are_sanitized() -> None:
    proposals = build_decision_proposals(
        {
            "template_type": "insulation",
            "division": "Insulation",
            "estimator_chat": {
                "source": "ai_chat",
                "confidence": 0.7,
                "assistant_message": "Use loading and travel.",
                "workbook_decision_preferences": [
                    {
                        "decision_id": "labor loading",
                        "include": True,
                        "proposed_values": {"hours_per_day": 8, "people_count": 2, "trip_count": 1, "unit_price": 1685.775},
                    },
                    {
                        "decision_id": "labor traveling",
                        "include": True,
                        "proposed_values": {"hours_per_day": 8, "people_count": 5, "trip_count": 2, "unit_price": 1685.775},
                    },
                ],
            },
        }
    )

    by_bucket = {row["template_bucket"]: row for row in proposals}

    assert by_bucket["labor_loading"]["section"] == "insulation_logistics_expense_template_decisions"
    assert by_bucket["labor_loading"]["workbook_row"] == "95"
    assert by_bucket["labor_loading"]["proposed_values"] == {
        "hours_per_day": 0.5,
        "people_count": 2.0,
        "trip_count": 1,
        "unit_price": 25.5,
    }
    assert by_bucket["labor_traveling"]["section"] == "insulation_logistics_expense_template_decisions"
    assert by_bucket["labor_traveling"]["workbook_row"] == "97"
    assert by_bucket["labor_traveling"]["proposed_values"] == {
        "hours_per_day": 2.5,
        "people_count": 5.0,
        "trip_count": 2,
        "unit_price": 13.0,
    }


def test_estimator_chat_roofing_preferences_target_workbook_rows_without_row_numbers() -> None:
    proposals = build_decision_proposals(
        {
            "template_type": "roofing",
            "division": "Roofing",
            "estimated_sqft": 96,
            "estimator_chat": {
                "source": "ai_chat",
                "confidence": 0.78,
                "assistant_message": "Patch roof SPF, coat it, add fabric, plates, truck expense, and loading labor.",
                "workbook_decision_preferences": [
                    {"template_bucket": "foam", "include": True, "proposed_values": {"basis_sqft": 96, "thickness_inches": 4}},
                    {"template_bucket": "coating", "include": True, "proposed_values": {"basis_sqft": 96}},
                    {"template_bucket": "fabric", "include": True},
                    {"template_bucket": "seams_misc", "include": True},
                    {"template_bucket": "fasteners", "include": True},
                    {"template_bucket": "plates", "include": True},
                    {"template_bucket": "truck_expense", "include": True, "proposed_values": {"trip_count": 1}},
                    {"template_bucket": "labor_loading", "include": True, "proposed_values": {"days": 0.25, "crew_size": 4}},
                ],
            },
        }
    )

    by_bucket = {row["template_bucket"]: row for row in proposals}

    assert by_bucket["foam"]["section"] == "roofing_foam_template_decisions"
    assert by_bucket["foam"]["workbook_row"] == "19"
    assert by_bucket["coating"]["workbook_row"] == "26"
    assert by_bucket["fabric"]["section"] == "roofing_detail_template_decisions"
    assert by_bucket["seams_misc"]["section"] == "roofing_detail_quantity_template_decisions"
    assert by_bucket["fasteners"]["workbook_row"] == "63"
    assert by_bucket["plates"]["workbook_row"] == "65"
    assert by_bucket["truck_expense"]["section"] == "roofing_travel_freight_template_decisions"
    assert by_bucket["truck_expense"]["workbook_row"] == "108"
    assert by_bucket["labor_loading"]["section"] == "roofing_logistics_expense_template_decisions"
    assert by_bucket["labor_loading"]["workbook_row"] == "136"
    assert by_bucket["labor_loading"]["proposed_values"]["hours_per_day"] == 0.25
    assert by_bucket["labor_loading"]["proposed_values"]["people_count"] == 4


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

    assert proposals == []
    assert not any("warranty_years" in (row.get("proposed_values") or {}) for row in proposals)


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
