from __future__ import annotations

import json

import pandas as pd

from jobscan.estimator.decision_proposals import (
    DecisionProposal,
    apply_decision_proposals_to_workbench,
    build_decision_proposals,
    canonicalize_structured_roofing_scope,
    compile_deterministic_scope_proposals,
    merge_decision_proposals,
)
from jobscan.estimator.schemas import EstimatorData
from jobscan.estimator.scope_integrity import evaluate_roofing_scope_integrity
from jobscan.estimator.planning_guidance import build_estimator_planning_guidance
from jobscan.estimator.template_examples import build_similar_answer_key_digest


def grossman_structured_scope() -> dict:
    return {
        "template_type": "roofing",
        "division": "Roofing",
        "job_name": "Grossman Tuning",
        "site_address": "830 South 1st Street, Louisville, KY 40203",
        "raw_input_notes": (
            "Approx. 5,136 sq.ft. total. Full removal down to wood decking. "
            "Install 2 inch Resista ISO board and 1.5 inch coated foam roof. "
            "Terra cotta coping to remain; seal seams with caulk. "
            "24' Counter Flashing required."
        ),
        "area_scopes": [
            {
                "scope_id": "area_1",
                "label": "Main Roof Area",
                "scope_role": "exclusive_area",
                "area_sqft": 3120,
                "action": "Full removal down to wood decking",
                "proposed_assembly": '2" Resista ISO board & 1.5" Coated Foam Roof',
            },
            {
                "scope_id": "area_2",
                "label": "Deteriorated Decking",
                "scope_role": "nested_sub_scope",
                "parent_scope_id": "area_1",
                "area_sqft": 320,
                "action": "Remove/replace deteriorated decking",
            },
            {
                "scope_id": "area_3",
                "label": "Secondary Roof Area",
                "scope_role": "exclusive_area",
                "area_sqft": 2016,
                "proposed_assembly": 'New 1.5" Coated Foam over existing roof',
            },
        ],
        "linear_scopes": [
            {"item": "Edge Metal", "size": '3.5"', "linear_ft": 52},
            {"item": "Gutter & Downspouts", "linear_ft": 52},
            {"item": "Foam-Stop Edge Metal", "size": '2"', "linear_ft": 24},
            {
                "item": "Wood Nailer & Foam-Stop Edge Metal",
                "size": '2x10 nailer; 3" foam stop',
                "linear_ft": 52,
            },
        ],
        "retain_existing": [
            {
                "item": "Terra Cotta Coping",
                "action": "Remain",
                "treatment": "seal seams (caulk)",
            }
        ],
        "area_reconciliation": {
            "declared_total_area_sqft": 5136,
            "calculated_total_area_sqft": 5456,
        },
    }


def grossman_estimator_data() -> EstimatorData:
    answer_key = {
        "schema_version": "reference_estimate_answer_key.v1",
        "template_type": "roofing",
        "job_context": {"area_sqft": 2200},
        "decisions": [
            {
                "section": "roofing_foam_template_decisions",
                "decision_id": "roofing_foam_row_19",
                "template_bucket": "foam",
                "workbook_row": "19",
                "include": True,
                "inputs": {
                    "basis_sqft": 2200,
                    "thickness_inches": 1.25,
                    "yield_or_coverage": 2700,
                    "unit_price": 2.05,
                },
            },
            {
                "section": "roofing_coating_template_decisions",
                "decision_id": "roofing_coating_system_row_26",
                "template_bucket": "coating",
                "workbook_row": "26",
                "include": True,
                "inputs": {
                    "basis_sqft": 2200,
                    "gal_per_100_sqft": 1.52,
                    "unit_price": 42,
                },
            },
            {
                "section": "roofing_labor_template_decisions",
                "decision_id": "roofing_labor_prep_row_116",
                "template_bucket": "labor_prep",
                "workbook_row": "116",
                "include": True,
                "inputs": {
                    "days": 0.5,
                    "crew_size": 6,
                    "daily_rate": 1894.2,
                    "total_hours": 33,
                },
            },
            {
                "section": "roofing_labor_template_decisions",
                "decision_id": "roofing_labor_base_row_122",
                "template_bucket": "labor_base",
                "workbook_row": "122",
                "include": True,
                "inputs": {
                    "days": 1,
                    "crew_size": 6,
                    "daily_rate": 1894.2,
                    "total_hours": 66,
                },
            },
            {
                "section": "roofing_equipment_template_decisions",
                "decision_id": "roofing_generator_row_99",
                "template_bucket": "generator",
                "workbook_row": "99",
                "include": True,
                "inputs": {"estimated_units": 5, "unit_price": 40},
            },
        ],
    }
    return EstimatorData(
        template_examples=pd.DataFrame(
            [
                {
                    "example_id": "closer-recoat-without-foam-row",
                    "job_id": "recoat-only",
                    "job_name": "Closer Recoat",
                    "source_file": "Estimate Closer Recoat.xlsx",
                    "template_type": "roofing",
                    "project_class": "coated foam roof",
                    "material_packages_json": json.dumps(["foam", "coating"]),
                    "area_sqft": 5136,
                    "scope_summary": "Exact-size coated foam roof with board and caulk metadata.",
                    "answer_key_json": json.dumps(
                        {
                            "schema_version": "reference_estimate_answer_key.v1",
                            "template_type": "roofing",
                            "job_context": {"area_sqft": 5136},
                            "decisions": [
                                {
                                    "section": "roofing_coating_template_decisions",
                                    "decision_id": "roofing_coating_system_row_26",
                                    "template_bucket": "coating",
                                    "workbook_row": "26",
                                    "include": True,
                                    "inputs": {
                                        "basis_sqft": 5136,
                                        "gal_per_100_sqft": 1,
                                        "unit_price": 37,
                                    },
                                }
                            ],
                        }
                    ),
                },
                {
                    "example_id": "wet-reroof-repair",
                    "job_id": "pearl-street",
                    "job_name": "204 Pearl Street Wet RR + Repairs",
                    "source_file": "Estimate 204 Pearl Street Wet RR + Repairs.xlsx",
                    "template_type": "roofing",
                    "project_class": "coated foam roof",
                    "material_packages_json": json.dumps(["foam", "coating"]),
                    "area_sqft": 2200,
                    "scope_summary": "Tear-off, ISO, coated SPF roof, and repairs.",
                    "answer_key_json": json.dumps(answer_key),
                }
            ]
        ),
        template_lookup_tables=pd.DataFrame(
            [
                {
                    "lookup_table_id": "materials-board-2",
                    "sheet_name": "Materials",
                    "table_name": "board",
                    "lookup_key": "Resista ISO",
                    "row_number": 7,
                    "values_json": json.dumps({"A": "Resista ISO", "B": '2" board', "C": 77.47}),
                },
                {
                    "lookup_table_id": "materials-plates",
                    "sheet_name": "Materials",
                    "table_name": "plates",
                    "lookup_key": "Carlisle Plates",
                    "row_number": 19,
                    "values_json": json.dumps({"A": "Carlisle Plates", "B": "1000 count", "C": 79.05}),
                },
            ]
        ),
        template_labor_options=pd.DataFrame(
            [
                {
                    "template_labor_option_id": "people-rate-prep-crew-6",
                    "template_type": "roofing",
                    "template_name": "Current Roofing Template.xlsx",
                    "source_type": "people_daily_rate_selector",
                    "source_table": "people_daily_rate_selector",
                    "row_number": 116,
                    "labor_package": "labor_prep",
                    "lookup_key": "6",
                    "source_values_json": {
                        "crew_size": 6,
                        "daily_rate": 2100,
                    },
                }
            ]
        ),
    )


def test_grossman_scope_canonicalization_preserves_nested_area_and_linear_takeoff() -> None:
    scope = canonicalize_structured_roofing_scope(grossman_structured_scope())

    assert scope["canonical_area_total_sqft"] == 5136
    assert scope["canonical_exclusive_area_sqft"] == 5136
    assert scope["canonical_nested_area_sqft"] == 320
    assert scope["foam_basis_sqft"] == 5136
    assert scope["coating_basis_sqft"] == 5136
    assert scope["board_basis_sqft"] == 3120
    assert scope["decking_replacement_sqft"] == 320
    assert scope["foam_thickness_inches"] == 1.5
    assert scope["board_thickness_inches"] == 2
    assert scope["canonical_linear_totals"] == {
        "edge_metal": 128,
        "gutter": 52,
        "downspouts": 52,
        "wood_nailer": 52,
        "counter_flashing": 24,
    }
    assert any("nested scope" in conflict for conflict in scope["scope_conflicts"])


def test_grossman_scope_integrity_accepts_nested_deck_repair() -> None:
    result = evaluate_roofing_scope_integrity(grossman_structured_scope())

    assert result["status"] == "valid_with_warnings"
    assert result["blocking_issues"] == []
    assert result["canonical_area_total_sqft"] == 5136
    assert result["board_basis_sqft"] == 3120
    assert result["decking_replacement_sqft"] == 320
    assert result["requires_tearoff"] is True


def test_grossman_scope_integrity_blocks_orphaned_nested_area() -> None:
    scope = grossman_structured_scope()
    scope["area_scopes"][1]["parent_scope_id"] = "missing-area"

    result = evaluate_roofing_scope_integrity(scope)

    assert result["status"] == "blocked"
    assert any("missing parent" in issue for issue in result["blocking_issues"])


def test_similar_answer_keys_hard_exclude_target_job_and_source_file() -> None:
    data = grossman_estimator_data()
    scope = grossman_structured_scope()
    scope["exclude_job_ids"] = ["recoat-only"]
    scope["exclude_source_files"] = [
        "Estimate 204 Pearl Street Wet RR + Repairs.xlsx"
    ]

    result = build_similar_answer_key_digest(data, scope=scope, limit=5)

    assert result["matched_answer_keys"] == []
    assert result["retrieval"]["excluded_candidate_count"] == 2


def test_grossman_planning_guidance_recommends_reviewable_purchase_rounding_and_labor() -> None:
    result = build_estimator_planning_guidance(
        scope=grossman_structured_scope(),
        data=grossman_estimator_data(),
    )

    purchasing = {
        row["category"]: row
        for row in result["purchasing_guidance"]
        if row["category"] in {"roofing_foam", "coating", "board_stock"}
    }
    assert purchasing["roofing_foam"]["measured_quantity"] == 5136
    assert purchasing["roofing_foam"]["recommended_purchase_quantity"] == 5250
    assert purchasing["coating"]["recommended_purchase_quantity"] == 5200
    assert purchasing["board_stock"]["recommended_purchase_quantity"] == 3136
    assert all(row["review_required"] for row in purchasing.values())

    metal = [
        row
        for row in result["purchasing_guidance"]
        if row["category"] == "edge_metal"
    ]
    assert sorted(row["recommended_purchase_quantity"] for row in metal) == [
        30,
        60,
        60,
    ]

    labor = {row["category"]: row for row in result["labor_plan_guidance"]}
    assert labor["labor_prep"]["recommended_total_hours"] == 77
    assert labor["labor_prep"]["current_people_daily_rate"] == 2100
    assert labor["labor_prep"]["rate_source"] == "current_people_tab"
    assert labor["labor_base"]["recommended_total_hours"] == 154.1
    assert labor["labor_base"]["formula_authority"] == (
        "workbook_people_rate_and_labor_formula"
    )
    assert labor["labor_tearoff"]["calibration_status"] == "missing_calibration"
    assert labor["labor_tearoff"]["blocking_input_required"] is True
    assert labor["labor_board"]["blocking_input_required"] is True
    assert labor["labor_top_coat"]["blocking_input_required"] is True
    assert labor["labor_cleanup"]["blocking_input_required"] is True


def test_grossman_planning_guidance_supplies_reviewable_logistics_baselines() -> None:
    data = grossman_estimator_data()
    result = build_estimator_planning_guidance(
        scope=grossman_structured_scope(),
        data=data,
        route_mileage={
            "estimated_round_trip_miles": 62,
            "duration_minutes_one_way": 38,
            "source": "mapbox_directions",
        },
    )
    logistics = {row["category"]: row for row in result["logistics_guidance"]}

    assert logistics["crew_plan"]["recommended_crew_size"] == 5
    assert logistics["sales_inspection_trips"]["recommended_trip_count"] == 2
    assert logistics["sales_inspection_trips"]["round_trip_miles"] == 62
    assert logistics["truck_expense"]["recommended_trip_count"] == logistics[
        "production_days"
    ]["recommended_days"]
    assert logistics["labor_loading"]["recommended_hours_per_trip"] > 0
    assert logistics["labor_loading"]["recommended_crew_size"] == 2
    assert logistics["labor_traveling"]["recommended_hours_per_trip"] == 1.27
    assert logistics["labor_traveling"]["recommended_crew_size"] == 5
    assert logistics["labor_traveling"]["route_source"] == "mapbox_directions"
    assert logistics["dumpster"]["recommended_size"] == "30-yard"
    assert logistics["generator"]["include"] is True
    assert logistics["generator"]["recommended_days"] == logistics[
        "production_days"
    ]["recommended_days"]
    assert logistics["generator"]["assumption_status"] == "deterministic_scope_rule"


def test_small_roof_planning_rounds_without_large_project_bias() -> None:
    scope = {
        "template_type": "roofing",
        "estimated_sqft": 484,
        "area_scopes": [
            {
                "scope_id": "repair",
                "scope_role": "exclusive_area",
                "area_sqft": 484,
                "action": "Install coated foam over existing roof",
                "proposed_assembly": "1.5 inch coated foam roof",
            }
        ],
        "area_reconciliation": {"declared_total_area_sqft": 484},
    }

    result = build_estimator_planning_guidance(scope=scope)
    purchasing = {
        row["category"]: row for row in result["purchasing_guidance"]
    }

    assert purchasing["roofing_foam"]["recommended_purchase_quantity"] == 500
    assert purchasing["coating"]["recommended_purchase_quantity"] == 500
    assert "board_stock" not in purchasing
    assert purchasing["coating"]["adjustment_pct"] == 3.306


def test_purchase_guidance_uses_supported_history_without_compounding_rounding() -> None:
    scope = grossman_structured_scope()
    result = build_estimator_planning_guidance(
        scope=scope,
        historical_material_usage=[
            {
                "category": "foam",
                "basis_measurements": [
                    {"name": "basis_sqft", "value": 5250, "unit": "sqft"}
                ],
                "support_count": 3,
                "sources": [
                    {"reference_area_sqft": 5000},
                    {"reference_area_sqft": 5000},
                ],
            }
        ],
    )

    foam = next(
        row for row in result["purchasing_guidance"]
        if row["category"] == "roofing_foam"
    )
    assert foam["historical_ratio"] == 1.05
    assert foam["historical_support_count"] == 3
    assert foam["recommended_purchase_quantity"] == 5250
    assert foam["method"] == "package_rounding_with_historical_support"


def test_labor_guidance_fills_required_task_from_semantic_productivity() -> None:
    data = EstimatorData(
        template_labor_options=pd.DataFrame(
            [
                {
                    "template_labor_option_id": "people-top-5",
                    "template_type": "roofing",
                    "source_type": "people_daily_rate_selector",
                    "labor_package": "labor_top_coat",
                    "lookup_key": "5",
                    "source_values_json": {"daily_rate": 2050},
                }
            ]
        )
    )
    result = build_estimator_planning_guidance(
        scope=grossman_structured_scope(),
        data=data,
        historical_labor_performance=[
            {
                "category": "labor_top_coat",
                "total_hours": 50,
                "crew_size": 5,
                "days": 1,
                "daily_rate": 1900,
                "support_count": 3,
                "confidence": 0.8,
                "productivity": {
                    "driver_type": "project_sqft",
                    "rate": 10,
                    "rate_unit": "hours_per_1000_sqft",
                    "evidence_count": 3,
                },
                "sources": [
                    {
                        "job_id": "HIST-1",
                        "file_name": "Historical roof.xlsx",
                        "reference_area_sqft": 5000,
                    }
                ],
            }
        ],
    )

    labor = {row["category"]: row for row in result["labor_plan_guidance"]}
    top = labor["labor_top_coat"]
    assert top["calibration_status"] == "historical_candidate"
    assert top["recommended_total_hours"] == 51.4
    assert top["recommended_crew_size"] == 5
    assert top["recommended_days"] == 1.03
    assert top["current_people_daily_rate"] == 2050
    assert top["rate_source"] == "current_people_tab"
    assert top["estimated_labor_cost_candidate"] == 2111.5
    assert top["blocking_input_required"] is False


def test_labor_guidance_uses_standalone_task_cohorts_and_current_people_rate() -> None:
    data = grossman_estimator_data()
    observations = []
    for category, unit, rates, driver_type, driver_quantity, crew_size in (
        ("labor_tearoff", "sqft", [10, 11, 12, 13, 14], "foam_area_proxy", 3000, 6),
        ("labor_board", "sqft", [15, 16, 17, 18, 19], "board_area", 3000, 6),
        ("labor_top_coat", "gal", [0.4, 0.45, 0.5, 0.55, 0.6], "coating_gallons", 75, 5),
    ):
        for index, rate in enumerate(rates):
            observations.append(
                {
                    "category": category,
                    "driver_unit": unit,
                    "driver_type": driver_type,
                    "driver_quantity": driver_quantity,
                    "task_rate": rate,
                    "crew_size": crew_size,
                    "job_id": f"HIST-{category}-{index}",
                    "source_file": f"Historical {category} {index}.xlsx",
                }
            )
    data.semantic_labor_task_rates = pd.DataFrame(observations)
    people_rows = []
    components = [
        {"hourly_wage": wage, "burden_rate": 1.35}
        for wage in (33, 26, 23.5, 21, 20, 20)
    ]
    for crew_size in (5, 6):
        people_rows.append(
            {
                "template_labor_option_id": f"people-{crew_size}",
                "template_type": "roofing",
                "source_type": "people_daily_rate_selector",
                "labor_package": "",
                "lookup_key": str(crew_size),
                "source_values_json": {
                    "crew_components": components,
                    "crew_size": crew_size,
                    "hours_per_day": 10,
                },
            }
        )
    data.template_labor_options = pd.concat(
        [data.template_labor_options, pd.DataFrame(people_rows)],
        ignore_index=True,
    )

    result = build_estimator_planning_guidance(
        scope=grossman_structured_scope(),
        data=data,
    )
    labor = {row["category"]: row for row in result["labor_plan_guidance"]}

    assert labor["labor_tearoff"]["recommended_total_hours"] == 37.4
    assert labor["labor_board"]["recommended_total_hours"] == 53.0
    assert labor["labor_top_coat"]["recommended_total_hours"] == 39.0
    assert labor["labor_top_coat"]["driver_unit"] == "gal"
    assert labor["labor_top_coat"]["historical_support_count"] == 5
    assert labor["labor_tearoff"]["current_people_daily_rate"] == 1937.25
    assert labor["labor_top_coat"]["current_people_daily_rate"] == 1667.25
    assert all(
        labor[category]["calibration_status"] == "historical_candidate"
        and labor[category]["blocking_input_required"] is False
        for category in ("labor_tearoff", "labor_board", "labor_top_coat")
    )


def test_grossman_scope_compiler_scales_one_comparable_and_prefers_materials_price() -> None:
    proposals = compile_deterministic_scope_proposals(
        grossman_structured_scope(),
        data=grossman_estimator_data(),
    )
    by_bucket = {proposal.template_bucket: proposal for proposal in proposals}

    foam = by_bucket["foam"]
    assert foam.proposed_values["basis_sqft"] == 5136
    assert foam.proposed_values["thickness_inches"] == 1.5
    assert foam.proposed_values["yield_or_coverage"] == 2700
    assert foam.proposed_values["historical_unit_price"] == 2.05
    assert foam.source == "deterministic_scope_compiler"
    assert "automatic_comparable" in foam.evidence
    assert foam.evidence["automatic_comparable"][0]["job_id"] == "pearl-street"

    coating = by_bucket["coating"]
    assert coating.proposed_values["basis_sqft"] == 5136
    assert coating.proposed_values["gal_per_100_sqft"] == 1.52

    board = by_bucket["board_stock"]
    assert board.proposed_values["basis_sqft"] == 3120
    assert board.proposed_values["price_per_square"] == 77.47
    assert board.proposed_values["selected_pricing_candidate"].startswith("Resista ISO")

    plates = by_bucket["plates"]
    assert plates.proposed_values["board_area_sqft"] == 3120
    assert plates.proposed_values["unit_price_per_thousand"] == 79.05
    assert by_bucket["fasteners"].proposed_values["scope_status"] == "recognized_awaiting_price"

    assert by_bucket["edge_metal"].proposed_values["linear_ft"] == 128
    assert by_bucket["gutter"].proposed_values["linear_ft"] == 52
    assert by_bucket["downspouts"].proposed_values["linear_ft"] == 52
    assert by_bucket["wood_nailer"].proposed_values["linear_ft"] == 52
    assert by_bucket["counter_flashing"].proposed_values["linear_ft"] == 24
    assert by_bucket["dumpster"].proposed_values["basis_sqft"] == 3120

    assert by_bucket["labor_prep"].proposed_values["total_hours"] == 77.0
    assert by_bucket["labor_prep"].proposed_values["daily_rate"] == 2100
    assert by_bucket["labor_prep"].proposed_values["historical_daily_rate"] == 1894.2
    assert by_bucket["labor_prep"].evidence["current_people_rate"][0][
        "template_labor_option_id"
    ] == "people-rate-prep-crew-6"
    assert by_bucket["labor_base"].proposed_values["total_hours"] == 154.1
    assert by_bucket["labor_base"].proposed_values["daily_rate"] == 1894.2
    assert by_bucket["generator"].proposed_values["days"] == 12


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


def test_explicit_roofing_template_type_wins_over_spray_foam_text() -> None:
    proposals = build_decision_proposals(
        {
            "template_type": "roofing",
            "division": "Roofing",
            "project_type": "roofing estimate with spray foam repair",
            "estimator_chat": {
                "source": "answer_key_audit",
                "workbook_decision_preferences": [
                    {
                        "template_type": "roofing",
                        "section": "roofing_coating_template_decisions",
                        "decision_id": "roofing_coating_system_row_27",
                        "template_bucket": "coating",
                        "workbook_row": "27",
                        "include": True,
                        "source": "reference_estimate_answer_key",
                        "proposed_values": {"basis_sqft": 46000, "gal_per_100_sqft": 1.725, "unit_price": 36},
                    }
                ],
            },
        }
    )

    assert len(proposals) == 1
    assert proposals[0]["section"] == "roofing_coating_template_decisions"
    assert proposals[0]["workbook_row"] == "27"


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


def test_historical_answer_key_context_stays_excluded_and_keeps_provenance() -> None:
    proposals = build_decision_proposals(
        {
            "template_type": "roofing",
            "division": "Roofing",
            "estimated_sqft": 1000,
            "raw_input_notes": "CMU wall repair: pressure wash, scrape, and seal cracks.",
            "estimator_chat": {
                "source": "ai_chat",
                "workbook_decision_preferences": [
                    {
                        "decision_id": "roofing_coating_system_row_26",
                        "template_bucket": "coating",
                        "workbook_row": "26",
                        "include": True,
                        "source": "historical_answer_key_context",
                        "proposed_values": {
                            "basis_sqft": 12100,
                            "gal_per_100_sqft": 1.335,
                            "unit_price": 36,
                        },
                    }
                ],
            },
        }
    )

    coating = next(row for row in proposals if row["decision_id"] == "roofing_coating_system_row_26")
    assert coating["include"] is False
    assert coating["source"] == "historical_answer_key_context"


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


def test_reference_answer_key_proposal_overrides_fallback_labor_values() -> None:
    workbench = {
        "scope": {"template_type": "roofing", "division": "Roofing"},
        "roofing_labor_template_decisions": [
            {
                "decision_id": "roofing_labor_prep_row_116",
                "template_bucket": "labor_prep",
                "workbook_row": "116",
                "include": True,
                "days": 0.5,
                "crew_size": 4,
                "daily_rate": 1500,
                "total_hours": 16,
                "include_source": "historical_default",
            }
        ],
    }
    proposal = DecisionProposal(
        decision_id="roofing_labor_prep_row_116",
        template_type="roofing",
        template_bucket="labor_prep",
        workbook_row="116",
        section="roofing_labor_template_decisions",
        include=True,
        proposed_values={
            "days": 1.7,
            "editable_days": 1.7,
            "crew_size": 5,
            "daily_rate": 1835.66,
            "total_hours": 89.25,
        },
        confidence=0.9,
        source="reference_estimate_answer_key",
    )

    updated = apply_decision_proposals_to_workbench(
        workbench,
        [proposal],
        decision_sections=["roofing_labor_template_decisions"],
    )
    labor = updated["roofing_labor_template_decisions"][0]

    assert labor["days"] == 1.7
    assert labor["editable_days"] == 1.7
    assert labor["crew_size"] == 5
    assert labor["daily_rate"] == 1835.66
    assert labor["total_hours"] == 89.25
    assert labor["proposal_source"] == "reference_estimate_answer_key"


def test_named_reference_uses_only_scope_authorized_active_answer_key_rows() -> None:
    answer_key = {
        "template_type": "roofing",
        "job_context": {"area_sqft": 2000, "substrate": "metal", "project_type": "roof_spf"},
        "summary": {"source_row_count": 2},
        "decisions": [
            {
                "decision_id": "roofing_caulk_sealant_row_43",
                "section": "roofing_detail_template_decisions",
                "template_bucket": "caulk_detail",
                "workbook_row": "43",
                "include": True,
                "inputs": {
                    "estimated_units": 24,
                    "unit_price": 12.5,
                    "resolved_template_option": "Urethane Sausage",
                },
            },
            {
                "decision_id": "roofing_generator_row_99",
                "section": "roofing_equipment_template_decisions",
                "template_bucket": "generator",
                "workbook_row": "99",
                "include": True,
                "inputs": {"estimated_units": 1, "unit_price": 50},
            },
            {
                "decision_id": "roofing_labor_prep_row_116",
                "section": "roofing_labor_template_decisions",
                "template_bucket": "labor_prep",
                "workbook_row": "116",
                "include": True,
                "inputs": {
                    "days": 0.25,
                    "crew_size": 3,
                    "daily_rate": 1044,
                    "total_hours": 7.5,
                },
            },
            {
                "decision_id": "roofing_labor_caulk_row_126",
                "section": "roofing_labor_template_decisions",
                "template_bucket": "labor_caulk",
                "workbook_row": "126",
                "include": True,
                "inputs": {"days": 1, "crew_size": 3, "daily_rate": 1044, "total_hours": 30},
            },
        ],
    }
    data = EstimatorData(
        template_examples=pd.DataFrame(
            [
                {
                    "document_id": "doc-1",
                    "job_id": "job-1",
                    "source_file": "Estimate CMU Wall Repair - Preston Animal Hospital (Sec. 1 Rear Wall).xlsx",
                    "template_type": "roofing",
                    "answer_key_json": json.dumps(answer_key),
                }
            ]
        )
    )
    proposals = build_decision_proposals(
        {
            "template_type": "roofing",
            "estimated_sqft": 1000,
            "substrate": "cmu",
            "project_type": "wall repair",
            "notes": "Similar to Estimate CMU Wall Repair - Preston Animal Hospital (Sec. 1 Rear Wall). Apply sealant.",
            "work_package_decisions": {
                "caulk_detail": {"applies": True},
                "prep_powerwash": {"applies": True},
                "coating": {"applies": True},
            },
            "explicit_labor_hourly_rate": 54,
            "explicit_labor_crew_size": 2,
        },
        data=data,
    )

    assert {proposal["template_bucket"] for proposal in proposals} == {
        "caulk_detail",
        "labor_caulk",
        "labor_prep",
    }
    caulk = next(proposal for proposal in proposals if proposal["template_bucket"] == "caulk_detail")
    assert caulk["source"] == "reference_estimate_answer_key"
    assert caulk["proposed_values"]["estimated_units"] == 12
    assert caulk["proposed_values"]["unit_price"] == 12.5
    assert caulk["proposed_values"]["resolved_template_option"] == "Urethane Sausage"
    assert any("Reference area context conflicts" in reason for reason in caulk["review_reasons"])
    prep = next(proposal for proposal in proposals if proposal["template_bucket"] == "labor_prep")
    assert prep["proposed_values"]["historical_driver_rate"] == 3.75
    assert "days" not in prep["proposed_values"]
    assert "total_hours" not in prep["proposed_values"]
    assert "crew_size" not in prep["proposed_values"]
    assert "daily_rate" not in prep["proposed_values"]
    caulk_labor = next(proposal for proposal in proposals if proposal["template_bucket"] == "labor_caulk")
    assert caulk_labor["proposed_values"]["historical_driver_rate"] == 1.25
    assert "days" not in caulk_labor["proposed_values"]
    assert "total_hours" not in caulk_labor["proposed_values"]


def test_insulation_answer_key_scope_wins_over_stale_roofing_division() -> None:
    proposals = build_decision_proposals(
        {
            "division": "Roofing",
            "template_type": "insulation",
            "project_type": "spray foam insulation",
            "workbook_decision_preferences": [
                {
                    "template_type": "insulation",
                    "section": "insulation_foam_template_decisions",
                    "decision_id": "insulation_foam_template_selector",
                    "template_bucket": "foam",
                    "workbook_row": "19-21",
                    "include": True,
                    "source": "reference_estimate_answer_key",
                    "proposed_values": {
                        "basis_sqft": 3600.0,
                        "thickness_inches": 8.75,
                        "unit_price": 1.6,
                        "yield_or_coverage": 17500,
                        "estimated_units": 1800,
                    },
                }
            ],
        }
    )

    foam = [row for row in proposals if row["template_bucket"] == "foam"]

    assert foam
    assert {row["section"] for row in foam} == {"insulation_foam_template_decisions"}
    assert all(row["template_type"] == "insulation" for row in foam)
    assert all(row["proposed_values"].get("yield_or_coverage") == 17500 for row in foam)
    assert all("estimated_units" not in row["proposed_values"] for row in foam)


def test_multiple_insulation_foam_chat_preferences_are_assigned_to_distinct_rows() -> None:
    proposals = build_decision_proposals(
        {
            "division": "Insulation",
            "template_type": "insulation",
            "project_type": "spray foam insulation",
            "workbook_decision_preferences": [
                {
                    "template_type": "insulation",
                    "section": "insulation_foam_template_decisions",
                    "template_bucket": "foam",
                    "include": True,
                    "source": "chat_estimator",
                    "proposed_values": {"basis_sqft": 2853.0, "thickness_inches": 5.5, "unit_price": 1.6},
                },
                {
                    "template_type": "insulation",
                    "section": "insulation_foam_template_decisions",
                    "template_bucket": "foam",
                    "include": True,
                    "source": "chat_estimator",
                    "proposed_values": {"basis_sqft": 3491.0, "thickness_inches": 8.0, "unit_price": 1.6},
                },
            ],
        }
    )

    foam = [row for row in proposals if row["section"] == "insulation_foam_template_decisions"]

    assert [row["workbook_row"] for row in foam] == ["19", "20"]
    assert [row["decision_id"] for row in foam] == ["insulation_foam_row_19", "insulation_foam_row_20"]


def test_chat_preferences_ignore_opposite_template_rows() -> None:
    proposals = build_decision_proposals(
        {
            "division": "Roofing",
            "template_type": "roofing",
            "project_type": "roof restoration",
            "workbook_decision_preferences": [
                {
                    "template_type": "insulation",
                    "section": "insulation_foam_template_decisions",
                    "decision_id": "insulation_foam_template_selector",
                    "template_bucket": "foam",
                    "workbook_row": "19-21",
                    "include": True,
                    "source": "reference_estimate_answer_key",
                    "proposed_values": {"basis_sqft": 3600.0, "thickness_inches": 8.75},
                }
            ],
        }
    )

    assert all(row["section"] != "roofing_foam_template_decisions" for row in proposals)


def test_estimator_chat_roofing_shorthand_decision_ids_are_canonicalized() -> None:
    proposals = build_decision_proposals(
        {
            "template_type": "roofing",
            "division": "Roofing",
            "estimated_sqft": 8000,
            "estimator_chat": {
                "source": "ai_chat",
                "confidence": 0.8,
                "assistant_message": "Multiply the metal roof basis by 1.2.",
                "workbook_decision_preferences": [
                    {
                        "decision_id": "roofing_coating_row_27",
                        "template_bucket": "coating",
                        "include": True,
                        "proposed_values": {"basis_sqft": 9600, "unit_price": 36},
                    },
                    {
                        "decision_id": "roofing_primer_row_39",
                        "template_bucket": "primer",
                        "include": True,
                        "proposed_values": {"basis_sqft": 9600},
                    },
                ],
            },
        }
    )

    by_row = {(row["section"], row["workbook_row"]): row for row in proposals}

    coating = by_row[("roofing_coating_template_decisions", "27")]
    primer = by_row[("roofing_primer_template_decisions", "39")]
    assert coating["decision_id"] == "roofing_coating_system_row_27"
    assert coating["proposed_values"]["basis_sqft"] == 9600
    assert primer["decision_id"] == "roofing_primer_system_row_39"
    assert primer["proposed_values"]["basis_sqft"] == 9600


def test_estimator_chat_assigns_repeated_rowless_roof_coatings_to_distinct_rows() -> None:
    proposals = build_decision_proposals(
        {
            "template_type": "roofing",
            "division": "Roofing",
            "estimated_sqft": 8000,
            "estimator_chat": {
                "source": "ai_chat",
                "confidence": 0.8,
                "assistant_message": "Use separate base and top coating passes from the comparable.",
                "workbook_decision_preferences": [
                    {
                        "template_bucket": "coating",
                        "include": True,
                        "proposed_values": {
                            "basis_sqft": 8000,
                            "gal_per_100_sqft": 1.5,
                            "unit_price": 36,
                            "selected_pricing_candidate": "Base Coating",
                        },
                    },
                    {
                        "template_bucket": "coating",
                        "include": True,
                        "proposed_values": {
                            "basis_sqft": 8000,
                            "gal_per_100_sqft": 1.0,
                            "unit_price": 42,
                            "selected_pricing_candidate": "Top Coating",
                        },
                    },
                ],
            },
        }
    )

    coatings = sorted(
        (
            proposal
            for proposal in proposals
            if proposal["section"] == "roofing_coating_template_decisions"
        ),
        key=lambda proposal: proposal["workbook_row"],
    )

    assert [proposal["workbook_row"] for proposal in coatings] == ["26", "27"]
    assert [proposal["decision_id"] for proposal in coatings] == [
        "roofing_coating_system_row_26",
        "roofing_coating_system_row_27",
    ]
    assert coatings[0]["proposed_values"]["selected_pricing_candidate"] == "Base Coating"
    assert coatings[1]["proposed_values"]["selected_pricing_candidate"] == "Top Coating"


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
    assert rows[0]["why_included"] == "Notes mention: coating."
    assert rows[0]["product_evidence_summary"] == "prod-1"
    assert rows[0]["formula_evidence_summary"] == "roofing_coating"
