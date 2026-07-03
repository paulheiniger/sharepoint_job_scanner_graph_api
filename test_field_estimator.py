from __future__ import annotations

import pandas as pd

import jobscan.estimator.field_estimator as field_estimator_module
from jobscan.estimator.field_estimator import build_labor_plan, build_material_plan, estimate_from_field_notes
from jobscan.estimator.field_notes import parse_field_notes, parse_field_sqft
from jobscan.estimator.schemas import EstimatorAssumptions, EstimatorData


TEST_CASE_A_NOTE = (
    "Customer wants to extend the life of a five-year-old standing seam metal roof. "
    "Roof is 90 ft by 70 ft. No deductions. "
    "Roof is in excellent condition with no visible rust and only minor dirt accumulation. "
    "Only one plumbing vent and one HVAC curb. Easy access from parking lot. "
    "Customer requests a 10-year white silicone maintenance coating."
)

INSULATION_EMAIL = (
    "James F. Collins 314 E Aberdeen Drive, Trenton, OH 513-319-2779. "
    "I am wanting to get a quote for getting foam sprayed in a 30x40 metal building with 9' walls. "
    "What I want to have insulated is the outside walls and ceiling of the building. "
    "The building will have two 9ft rollup doors, two 36\" walk-in doors and five 24\"x36\" windows. "
    "The building is being installed beginning to mid-August and I would like the work in September or October."
)


def field_data(*, with_template_rows: bool = True, with_pricing: bool = True, with_fallback: bool = False) -> EstimatorData:
    jobs = pd.DataFrame(
        [
            {
                "job_id": "J1",
                "customer": "Acme",
                "job_name": "Acme metal roof silicone",
                "division": "ROOFING",
                "job_type": "roof coating",
                "estimated_sqft": 12000,
                "estimated_value": 110000,
                "price_per_sqft": 9.17,
                "city": "Louisville",
            }
        ]
    )
    estimates = pd.DataFrame(
        [
            {
                "job_id": "J1",
                "estimate_file": "Estimate.xlsx",
                "coating_type": "silicone",
                "estimated_sqft": 12000,
                "estimated_labor_hours": 220,
            }
        ]
    )
    pricing = (
        pd.DataFrame(
            [
                {
                    "pricing_item_id": "P1",
                    "product_name": "High Solids Silicone",
                    "category": "Coating",
                    "price_per_gallon": 38,
                    "unit_price": 190,
                    "status": "active",
                    "is_current": True,
                    "needs_review": False,
                }
            ]
        )
        if with_pricing
        else pd.DataFrame()
    )
    template_rows = (
        pd.DataFrame(
            [
                {
                    "template_row_id": "R116",
                    "document_id": "D1",
                    "job_id": "J1",
                    "source_file": "Estimate.xlsx",
                    "template_bucket": "labor_prep",
                    "line_item_kind": "labor",
                    "days": 3,
                    "crew_size": 4,
                    "total_hours": 96,
                    "estimated_cost": 7000,
                },
                {
                    "template_row_id": "R120",
                    "document_id": "D1",
                    "job_id": "J1",
                    "source_file": "Estimate.xlsx",
                    "template_bucket": "labor_seam_sealer",
                    "line_item_kind": "labor",
                    "days": 2,
                    "crew_size": 4,
                    "total_hours": 64,
                    "estimated_cost": 4500,
                },
                {
                    "template_row_id": "R122",
                    "document_id": "D1",
                    "job_id": "J1",
                    "source_file": "Estimate.xlsx",
                    "template_bucket": "labor_base",
                    "line_item_kind": "labor",
                    "days": 2,
                    "crew_size": 4,
                    "total_hours": 64,
                    "estimated_cost": 5200,
                },
                {
                    "template_row_id": "R124",
                    "document_id": "D1",
                    "job_id": "J1",
                    "source_file": "Estimate.xlsx",
                    "template_bucket": "labor_top_coat",
                    "line_item_kind": "labor",
                    "days": 2,
                    "crew_size": 5,
                    "total_hours": 80,
                    "estimated_cost": 6400,
                },
                {
                    "template_row_id": "R141",
                    "document_id": "D1",
                    "job_id": "J1",
                    "source_file": "Estimate.xlsx",
                    "template_bucket": "infrared_scan",
                    "line_item_kind": "labor",
                    "days": 1,
                    "crew_size": 2,
                    "total_hours": 16,
                    "estimated_cost": 1800,
                },
                {
                    "template_row_id": "R130",
                    "document_id": "D1",
                    "job_id": "J1",
                    "source_file": "Estimate.xlsx",
                    "template_bucket": "labor_top_coat_granules",
                    "line_item_kind": "labor",
                    "days": 1,
                    "crew_size": 3,
                    "total_hours": 24,
                    "estimated_cost": 2400,
                },
                {
                    "template_row_id": "R134",
                    "document_id": "D1",
                    "job_id": "J1",
                    "source_file": "Estimate.xlsx",
                    "template_bucket": "labor_misc",
                    "line_item_kind": "labor",
                    "days": 5,
                    "crew_size": 6,
                    "total_hours": 300,
                    "estimated_cost": 30000,
                },
                {
                    "template_row_id": "R169",
                    "document_id": "D1",
                    "job_id": "J1",
                    "source_file": "Estimate.xlsx",
                    "template_bucket": "worksheet_price",
                    "line_item_kind": "total",
                    "estimated_cost": 110000,
                },
            ]
        )
        if with_template_rows
        else pd.DataFrame()
    )
    classified = pd.DataFrame([{"job_id": "J1", "template_bucket": "coating", "line_total": 20000}]) if with_fallback else pd.DataFrame()
    line_items = pd.DataFrame([{"job_id": "J1", "line_item_name": "Silicone coating", "extended_cost": 18000, "unit_price": 38}])
    return EstimatorData(jobs=jobs, estimates=estimates, pricing=pricing, template_rows=template_rows, classified_line_items=classified, line_items=line_items)


def test_parse_field_sqft_handles_about_10k() -> None:
    assert parse_field_sqft("about 10k") == 10000
    assert parse_field_sqft("12,000 sqft") == 12000


def test_parse_metal_roof_rust_warranty() -> None:
    parsed = parse_field_notes("Metal roof, about 12,000 sqft, rusted fasteners, wants 15-year warranty, Louisville KY")

    assert parsed.substrate == "metal"
    assert parsed.estimated_sqft == 12000
    assert parsed.warranty_target_years == 15
    assert parsed.roof_condition == "poor/rusted"


def test_clean_standing_seam_maintenance_coating_does_not_infer_rust_or_seam_treatment() -> None:
    recommendation = estimate_from_field_notes(TEST_CASE_A_NOTE, data=field_data())

    assert recommendation.parsed_fields["estimated_sqft"] == 6300
    assert recommendation.parsed_fields["gross_area_sqft"] == 6300
    assert recommendation.parsed_fields["deduction_area_sqft"] == 0
    assert recommendation.parsed_fields["roof_condition"] == "excellent"
    flags = set(recommendation.parsed_fields.get("condition_detail_flags") or [])
    assert "rust" not in flags
    assert "rusted_fasteners" not in flags
    assert "open_seams" not in flags
    assert not any("Rusted fasteners/seams" in flag for flag in recommendation.review_flags)
    seam_rows = [row for row in recommendation.material_plan if row.get("category") == "seam_treatment"]
    assert not any(row.get("included_in_total") is not False and row.get("estimated_cost") for row in seam_rows)
    runtime = recommendation.debug.get("runtime_seconds_by_stage") or {}
    assert runtime.get("select_materials", 999) < 10


def test_missing_sqft_triggers_review() -> None:
    recommendation = estimate_from_field_notes("Metal roof, rusted fasteners, silicone coating, Louisville KY", data=field_data())

    assert recommendation.estimate_status == "NEED_MORE_INFORMATION"
    assert recommendation.estimate_low is None
    assert recommendation.estimate_target is None
    assert recommendation.estimate_high is None
    assert recommendation.material_plan == []
    assert recommendation.labor_plan == []
    assert any("estimated_sqft" in flag for flag in recommendation.review_flags)
    assert recommendation.human_review_required is True


def test_no_insulation_condensation_triggers_foam_review() -> None:
    recommendation = estimate_from_field_notes("Metal roof 12000 sqft no insulation condensation silicone coating Louisville KY", data=field_data())

    assert any("Foam or insulation design review required" in flag for flag in recommendation.review_flags)


def test_many_penetrations_and_high_access_increase_labor_modifiers() -> None:
    recommendation = estimate_from_field_notes("Metal roof 12000 sqft silicone coating many RTUs difficult access Louisville KY", data=field_data())

    calibration = recommendation.historical_calibration
    assert calibration["source"] == "estimate_template_rows"
    assert sum(row["estimated_cost"] for row in recommendation.labor_plan) > 11500


def test_warranty_target_changes_material_wet_mil_assumption() -> None:
    ten = estimate_from_field_notes("Metal roof 12000 sqft silicone coating 10 year warranty Louisville KY", data=field_data())
    twenty = estimate_from_field_notes("Metal roof 12000 sqft silicone coating 20 year warranty Louisville KY", data=field_data())

    assert twenty.material_plan[0]["quantity"] > ten.material_plan[0]["quantity"]


def test_travel_origin_and_local_travel() -> None:
    recommendation = estimate_from_field_notes("Metal roof 12000 sqft silicone coating Shelbyville KY", data=field_data())

    assert recommendation.travel_plan["origin_address"] == "1132 Equity Street, Shelbyville, KY"
    assert recommendation.travel_plan["travel_distance_bucket"] == "local"


def test_distant_city_triggers_lodging_review() -> None:
    recommendation = estimate_from_field_notes("Metal roof 12000 sqft silicone coating Indianapolis IN", data=field_data())

    assert recommendation.travel_plan["lodging_required_possible"] is True
    assert any("Travel assumptions require review" in flag for flag in recommendation.review_flags)


def test_material_plan_prefers_pricing_catalog() -> None:
    recommendation = estimate_from_field_notes("Metal roof 12000 sqft silicone coating Louisville KY", data=field_data())

    assert recommendation.material_plan[0]["price_source_type"] == "current_pricing"
    assert recommendation.material_plan[0]["unit_price"] == 38


def test_historical_pricing_fallback_is_marked_review() -> None:
    recommendation = estimate_from_field_notes("Metal roof 12000 sqft silicone coating Louisville KY", data=field_data(with_pricing=False))

    assert recommendation.material_plan[0]["price_source_type"] == "historical_fallback"
    assert recommendation.material_plan[0]["needs_review"] is True


def test_template_rows_labor_calibration_is_used() -> None:
    recommendation = estimate_from_field_notes("Metal roof 12000 sqft silicone coating Louisville KY", data=field_data())

    assert recommendation.historical_calibration["source"] == "estimate_template_rows"
    assert any(row["evidence_count"] > 0 for row in recommendation.labor_plan)


def test_build_labor_plan_handles_nan_historical_labor_values() -> None:
    plan, low, high, crew_size, duration_days, labor_hours = build_labor_plan(
        {"surface_area_sqft": 12000},
        {
            "labor_by_bucket": [
                {
                    "template_bucket": "labor_prep",
                    "median_days": float("nan"),
                    "median_crew_size": float("nan"),
                    "median_total_hours": float("nan"),
                    "median_estimated_cost": float("nan"),
                    "evidence_count": float("nan"),
                }
            ]
        },
        {
            "labor_modifiers": {"combined_labor_multiplier": 1.0, "adjusted_productivity_sqft_per_day": 3000},
            "crew_assumptions": {"recommended_crew_size": float("nan")},
        },
        EstimatorAssumptions(),
    )

    assert plan[0]["crew_size"] == 4
    assert plan[0]["evidence_count"] == 0
    assert "Historical labor calibration was incomplete" in plan[0]["notes"]
    assert low == 0
    assert high == 0
    assert crew_size == 4
    assert duration_days == 1
    assert labor_hours == 40


def test_build_labor_plan_falls_back_when_labor_rows_are_malformed() -> None:
    plan, low, high, crew_size, duration_days, labor_hours = build_labor_plan(
        {"surface_area_sqft": 9536},
        {"labor_by_bucket": [None]},
        {"labor_modifiers": {"combined_labor_multiplier": 1.0}, "crew_assumptions": {"recommended_crew_size": float("nan")}},
        EstimatorAssumptions(),
    )

    assert plan[0]["task"] == "labor_allowance"
    assert plan[0]["crew_size"] == 4
    assert plan[0]["total_hours"] == 40
    assert plan[0]["estimated_cost"] == 0.0
    assert plan[0]["needs_review"] is True
    assert low == 0
    assert high == 0
    assert crew_size == 4
    assert duration_days == 1
    assert labor_hours == 40


def test_field_estimator_does_not_crash_with_nan_template_labor_history() -> None:
    data = field_data()
    data.template_rows.loc[data.template_rows["line_item_kind"] == "labor", ["days", "crew_size", "total_hours", "estimated_cost"]] = float("nan")

    recommendation = estimate_from_field_notes("Metal roof 12000 sqft silicone coating Louisville KY", data=data)

    assert recommendation.estimate_high >= recommendation.estimate_low
    assert any(row["crew_size"] == 4 for row in recommendation.labor_plan)
    assert any(row["evidence_count"] == 1 for row in recommendation.labor_plan)
    assert any("Historical labor calibration was incomplete" in flag for flag in recommendation.review_flags)


def test_field_estimator_uses_full_data_with_messy_template_rows_and_pricing() -> None:
    data = field_data()
    data.template_rows.loc[data.template_rows["line_item_kind"] == "labor", ["days", "crew_size", "total_hours", "estimated_cost"]] = float("nan")
    note = (
        "Roof coating estimate. Metal roof in Louisville KY. Main roof is 120 ft by 80 ft. "
        "Deduct two skylight areas, each 4 ft by 8 ft. Customer wants a 10-year silicone coating system."
    )

    recommendation = estimate_from_field_notes(note, {"estimated_sqft": 0}, data=data)

    assert recommendation.parsed_fields["estimated_sqft"] == 9536
    assert recommendation.draft_workbook_inputs["header"]["C12_estimated_sqft"] == 9536
    assert recommendation.material_plan[0]["price_source_type"] == "current_pricing"
    assert recommendation.material_plan[0]["unit_price"] == 38
    assert recommendation.labor_plan
    assert any("Historical labor calibration was incomplete" in flag for flag in recommendation.review_flags)


def test_roof_coating_filters_labor_calibration_and_travel_hours() -> None:
    note = (
        "Roof coating estimate. Metal roof in Louisville KY. Main roof is 120 ft by 80 ft. "
        "Deduct two skylight areas, each 4 ft by 8 ft. Roof condition is fair with some rusted fasteners. "
        "Customer wants a 10-year silicone coating system. Access is easy. Few penetrations."
    )

    recommendation = estimate_from_field_notes(note, {"estimated_sqft": 0}, data=field_data())
    tasks = {row["task"] for row in recommendation.labor_plan}

    assert recommendation.draft_workbook_inputs["header"]["C12_estimated_sqft"] == 9536
    assert recommendation.material_plan[0]["price_source_type"] == "current_pricing"
    assert {"labor_prep", "labor_seam_sealer", "labor_base", "labor_top_coat", "labor_details", "labor_cleanup", "labor_loading"}.issubset(tasks)
    assert "infrared_scan" not in tasks
    assert "labor_top_coat_granules" not in tasks
    assert "labor_misc" not in tasks
    assert 4 <= recommendation.travel_plan["travel_labor_hours"] <= 8
    assert not any("Tear-off or substrate repair review required" in flag for flag in recommendation.review_flags)
    assert any("Rusted fasteners/seams require detail review" in flag for flag in recommendation.review_flags)


def test_simple_roof_coating_labor_bundle_is_capped() -> None:
    recommendation = estimate_from_field_notes(
        "Roof coating estimate for a commercial metal roof in Louisville KY. Main roof is 120 ft by 80 ft. "
        "Deduct two skylights, each 4 ft by 8 ft. Roof is fair overall but has rusted fasteners and some open seams. "
        "Customer wants a 10-year silicone coating system. Access is easy. Few penetrations.",
        data=field_data(),
    )
    total_hours = sum(float(row.get("total_hours") or 0) for row in recommendation.labor_plan)
    hours_per_1000 = total_hours / recommendation.parsed_fields["estimated_sqft"] * 1000

    assert hours_per_1000 <= 60
    summary = recommendation.debug["labor_calibration"]["selection_summary"]
    assert summary["labor_bundle_after_cap_hours"] <= summary["labor_bundle_cap_hours"]


def test_clean_maintenance_roof_coating_does_not_reuse_prior_scope_or_overstack_labor() -> None:
    note = (
        "Customer wants to extend the life of a five-year-old standing seam metal roof. "
        "Roof is 90 ft by 70 ft. "
        "No deductions. "
        "Roof is in excellent condition with no visible rust and only minor dirt accumulation. "
        "Only one plumbing vent and one HVAC curb. "
        "Easy access. "
        "Customer requests a 10-year silicone maintenance coating."
    )

    recommendation = estimate_from_field_notes(note, data=field_data())
    parsed = recommendation.parsed_fields
    labor_hours = sum(float(row.get("total_hours") or 0) for row in recommendation.labor_plan)
    hours_per_1000 = labor_hours / parsed["estimated_sqft"] * 1000
    labor_tasks = {row.get("task") for row in recommendation.labor_plan}
    review_text = " ".join(recommendation.review_flags).lower()

    assert parsed["estimated_sqft"] == 6300
    assert parsed["dimension_summary"]["gross_area_sqft"] == 6300
    assert parsed["dimension_summary"]["deduction_area_sqft"] == 0
    assert parsed["dimension_summary"]["net_area_sqft"] == 6300
    assert parsed["dimension_summary"]["no_deductions"] is True
    assert parsed["roof_condition"] in {"excellent", "good"}
    assert parsed["access_complexity"] == "low"
    assert parsed["penetrations_complexity"] == "low"
    assert parsed["penetration_count"] == 2
    assert parsed["warranty_target_years"] == 10
    assert not any("rust" in flag for flag in parsed.get("condition_detail_flags") or [])
    assert "labor_prime" not in labor_tasks
    assert hours_per_1000 < 40
    assert "tear-off" not in review_text and "tear off" not in review_text
    assert "high-access" not in review_text and "50+" not in review_text
    assert recommendation.debug["run_integrity"]["stale_source_text_detected"] is False
    assert recommendation.debug["labor_calibration"]["selection_summary"]["labor_bundle_summary"]
    assert "final_labor_hours_per_1000_sqft" in recommendation.debug["labor_calibration"]["selection_summary"]


def test_roof_coating_labor_baseline_fills_missing_tasks_when_history_only_has_prime() -> None:
    data = field_data()
    data.template_rows = pd.DataFrame(
        [
            {
                "template_row_id": "R118",
                "job_id": "J1",
                "template_bucket": "labor_prime",
                "line_item_kind": "labor",
                "days": 1,
                "crew_size": 4,
                "total_hours": 32,
                "estimated_cost": 2400,
            }
        ]
    )

    recommendation = estimate_from_field_notes(
        "Roof coating estimate for a commercial metal roof in Louisville KY. Main roof is 120 ft by 80 ft. "
        "Deduct two skylights, each 4 ft by 8 ft. Roof is fair overall but has rusted fasteners and some open seams. "
        "Customer wants a 10-year silicone coating system. Access is easy. Few penetrations.",
        data=data,
    )
    tasks = {row["task"] for row in recommendation.labor_plan}

    assert "labor_prime" in tasks
    assert {"labor_prep", "labor_seam_sealer", "labor_base", "labor_top_coat", "labor_details", "labor_cleanup", "labor_loading"}.issubset(tasks)
    assert sum(1 for task in tasks if task.startswith("labor_")) > 4
    fallback_rows = [row for row in recommendation.labor_plan if row.get("calibration_method") == "rule_based_fallback"]
    assert fallback_rows
    assert all(row["needs_review"] for row in fallback_rows)


def test_template_rows_used_when_relationship_labor_rates_empty() -> None:
    data = field_data()
    data.relationship_labor_rates = pd.DataFrame()

    recommendation = estimate_from_field_notes(
        "Roof coating estimate for a commercial metal roof in Louisville KY. Main roof is 120 ft by 80 ft. "
        "Deduct two skylights, each 4 ft by 8 ft. Customer wants a 10-year silicone coating system. Access is easy.",
        data=data,
    )
    tasks = {row["task"]: row for row in recommendation.labor_plan}

    assert {"labor_prep", "labor_seam_sealer", "labor_base", "labor_top_coat"}.issubset(tasks)
    assert tasks["labor_prep"]["calibration_method"] in {"historical_calibration", "relaxed_historical_calibration"}
    assert tasks["labor_prep"]["evidence_count"] > 0
    assert recommendation.debug["labor_calibration"]["tasks"]["labor_prep"]["selected_source"] == "estimate_template_rows"


def test_labor_package_normalization_maps_common_row_labels() -> None:
    data = field_data()
    data.template_rows = pd.DataFrame(
        [
            {"job_id": "J1", "row_label": "Pressure Wash / Prep", "line_item_kind": "labor", "days": 2, "crew_size": 4, "total_hours": 64, "estimated_cost": 4000},
            {"job_id": "J1", "row_label": "Seam Treatment", "line_item_kind": "labor", "days": 2, "crew_size": 4, "total_hours": 64, "estimated_cost": 4200},
            {"job_id": "J1", "row_label": "Base Coat", "line_item_kind": "labor", "days": 2, "crew_size": 4, "total_hours": 64, "estimated_cost": 4200},
            {"job_id": "J1", "row_label": "Top Coat", "line_item_kind": "labor", "days": 2, "crew_size": 4, "total_hours": 64, "estimated_cost": 4200},
            {"job_id": "J1", "row_label": "Final Cleanup", "line_item_kind": "labor", "days": 1, "crew_size": 3, "total_hours": 24, "estimated_cost": 1600},
        ]
    )
    data.relationship_labor_rates = pd.DataFrame()

    recommendation = estimate_from_field_notes(
        "Roof coating estimate for a commercial metal roof in Louisville KY. Main roof is 120 ft by 80 ft. "
        "Customer wants a 10-year silicone coating system. Access is easy.",
        data=data,
    )
    rows_by_task = {row["task"]: row for row in recommendation.labor_plan}

    for task in ["labor_prep", "labor_seam_sealer", "labor_base", "labor_top_coat", "labor_cleanup"]:
        assert rows_by_task[task]["calibration_method"] in {"historical_calibration", "relaxed_historical_calibration"}
        assert rows_by_task[task]["evidence_count"] > 0
        assert recommendation.debug["labor_calibration"]["tasks"][task]["selected_source"] == "estimate_template_rows"


def test_relaxed_labor_matching_is_used_when_exact_context_is_missing() -> None:
    data = field_data()
    data.jobs["substrate"] = "concrete"
    data.estimates["substrate"] = "concrete"

    recommendation = estimate_from_field_notes(
        "Roof coating estimate for a commercial metal roof in Louisville KY. Main roof is 120 ft by 80 ft. "
        "Customer wants a 10-year silicone coating system. Access is easy.",
        data=data,
    )
    prep = {row["task"]: row for row in recommendation.labor_plan}["labor_prep"]

    assert prep["calibration_method"] == "relaxed_historical_calibration"
    assert "relaxed historical roofing labor evidence" in prep["notes"]
    assert recommendation.debug["labor_calibration"]["tasks"]["labor_prep"]["selection_level"] in {"all_roofing_template_bucket", "all_bucket_rows"}


def test_rule_based_labor_fallback_only_when_no_historical_evidence_exists() -> None:
    data = field_data()
    data.template_rows = pd.DataFrame()
    data.relationship_labor_rates = pd.DataFrame()
    data.job_package_summary = pd.DataFrame()

    recommendation = estimate_from_field_notes(
        "Roof coating estimate for a commercial metal roof in Louisville KY. Main roof is 120 ft by 80 ft. "
        "Customer wants a 10-year silicone coating system. Access is easy.",
        data=data,
    )
    tasks = {row["task"]: row for row in recommendation.labor_plan}

    assert tasks["labor_prep"]["calibration_method"] == "rule_based_fallback"
    assert tasks["labor_prep"]["needs_review"] is True
    assert recommendation.debug["labor_calibration"]["tasks"]["labor_prep"]["selected_source"] == "rule_based_fallback"


def test_invalid_historical_crew_size_is_not_used_as_crew_count() -> None:
    data = field_data()
    data.template_rows.loc[data.template_rows["line_item_kind"] == "labor", "crew_size"] = 30

    recommendation = estimate_from_field_notes("Metal roof 12000 sqft silicone coating Louisville KY", data=data)

    assert all(row["crew_size"] <= 8 for row in recommendation.labor_plan)
    assert any("Historical labor calibration was incomplete" in flag for flag in recommendation.review_flags)


def test_louisville_travel_labor_uses_drive_time_not_production_duration() -> None:
    recommendation = estimate_from_field_notes(
        "Roof coating estimate for a commercial metal roof in Louisville KY. Main roof is 120 ft by 80 ft. "
        "Deduct two skylights, each 4 ft by 8 ft. Roof is fair overall but has rusted fasteners and some open seams. "
        "Customer wants a 10-year silicone coating system. Access is easy. Few penetrations.",
        data=field_data(),
    )

    assert recommendation.travel_plan["estimated_drive_time_minutes_one_way"] == 37
    assert recommendation.travel_plan["travel_labor_hours"] <= 8


def test_insulation_wall_note_parses_opening_deductions_foam_type_and_thickness() -> None:
    recommendation = estimate_from_field_notes(
        "Spray foam insulation estimate in Louisville KY. North wall is 120 ft by 18 ft. "
        "South wall is 120 ft by 18 ft. East wall is 80 ft by 18 ft. West wall is 80 ft by 18 ft. "
        "Deduct 8 overhead doors at 12 ft by 14 ft and 10 windows at 4 ft by 5 ft. "
        "Customer wants closed-cell foam, about 2 inches thick. Access is easy.",
        data=field_data(),
    )

    assert recommendation.parsed_fields["estimated_sqft"] == 5656
    assert recommendation.parsed_fields["dimension_summary"]["gross_area_sqft"] == 7200
    assert recommendation.parsed_fields["dimension_summary"]["deduction_area_sqft"] == 1544
    assert recommendation.parsed_fields["dimension_summary"]["net_area_sqft"] == 5656
    assert recommendation.parsed_fields["foam_type"] == "closed_cell"
    assert recommendation.parsed_fields["foam_thickness_inches"] == 2


def test_roof_coating_allowances_are_priced_and_reviewable() -> None:
    note = (
        "Roof coating estimate. Metal roof in Louisville KY. Main roof is 120 ft by 80 ft. "
        "Deduct two skylight areas, each 4 ft by 8 ft. Roof condition is fair with some rusted fasteners. "
        "Customer wants a 10-year silicone coating system. Access is easy. Few penetrations."
    )

    recommendation = estimate_from_field_notes(note, {"estimated_sqft": 0}, data=field_data())
    allowance_rows = [row for row in recommendation.material_plan if row.get("category") in {"primer", "seam_treatment", "fastener_treatment"}]
    allowance_names = {row["item"] for row in allowance_rows}
    priced_review_allowances = [row for row in allowance_rows if row.get("needs_review") and row.get("estimated_cost") is not None]

    assert {"Primer allowance", "Seam treatment allowance", "Fastener treatment allowance"}.issubset(allowance_names)
    assert len(priced_review_allowances) >= 3
    assert all(row["selected_price_source"] == "rule_based_allowance" for row in priced_review_allowances)
    assert sum(row["estimated_cost"] for row in priced_review_allowances) > 0
    assert sum(row["cost_low"] for row in priced_review_allowances) > 0
    assert sum(row["cost_high"] for row in priced_review_allowances) > sum(row["estimated_cost"] for row in priced_review_allowances)


def test_priced_review_allowances_affect_estimate_range() -> None:
    base = estimate_from_field_notes(
        "Roof coating estimate. Metal roof in Louisville KY. Main roof is 120 ft by 80 ft. "
        "Deduct two skylight areas, each 4 ft by 8 ft. Roof condition is good. "
        "Customer wants a 10-year silicone coating system. Access is easy. Few penetrations.",
        {"estimated_sqft": 0},
        data=field_data(),
    )
    with_allowances = estimate_from_field_notes(
        "Roof coating estimate. Metal roof in Louisville KY. Main roof is 120 ft by 80 ft. "
        "Deduct two skylight areas, each 4 ft by 8 ft. Roof condition is fair with some rusted fasteners. "
        "Customer wants a 10-year silicone coating system. Access is easy. Few penetrations.",
        {"estimated_sqft": 0},
        data=field_data(),
    )

    assert any(row.get("needs_review") and row.get("estimated_cost") for row in with_allowances.material_plan)
    assert with_allowances.estimate_low > base.estimate_low
    assert with_allowances.estimate_high > base.estimate_high


def test_allowances_are_not_generated_when_sqft_missing() -> None:
    recommendation = estimate_from_field_notes("Metal roof rusted fasteners silicone coating Louisville KY", data=field_data())

    assert recommendation.estimate_status == "NEED_MORE_INFORMATION"
    assert recommendation.material_plan == []
    assert recommendation.labor_plan == []
    assert recommendation.estimate_low is None
    assert recommendation.estimate_target is None
    assert recommendation.estimate_high is None
    assert any("Roof area is unknown" in flag for flag in recommendation.review_flags)


def test_primer_allowance_absent_without_primer_trigger() -> None:
    recommendation = estimate_from_field_notes(
        "Roof coating estimate. Metal roof in Louisville KY. Main roof is 120 ft by 80 ft. "
        "Deduct two skylight areas, each 4 ft by 8 ft. Roof condition is good. "
        "Customer wants a 10-year silicone coating system. Access is easy. Few penetrations.",
        {"estimated_sqft": 0},
        data=field_data(),
    )

    assert not any(row.get("item") == "Primer allowance" for row in recommendation.material_plan)


def test_flat_membrane_silicone_scope_does_not_add_primer_or_fasteners() -> None:
    note = (
        "Customer has an older flat roof on a small commercial building in Louisville. "
        "Roof is about 12,000 sqft. Existing membrane is weathered but mostly intact. "
        "Some seams opening up and a few ponding areas near drains. They want a silicone coating system if possible. "
        "Access is decent from rear parking lot. Need include power wash, seam treatment, fasteners/details, and coating. "
        "No interior leaks reported."
    )

    recommendation = estimate_from_field_notes(note, data=field_data())
    categories = {row.get("category") for row in recommendation.material_plan}
    labor_tasks = {row.get("task") for row in recommendation.labor_plan}
    packages = recommendation.historical_calibration["work_package_decisions"]

    assert "coating" in categories
    assert "seam_treatment" in categories
    assert "caulk_detail" in categories
    assert "primer" not in categories
    assert "fastener_treatment" not in categories
    assert packages["primer"]["applies"] is False
    assert packages["fastener_treatment"]["applies"] is False
    assert "labor_prime" not in labor_tasks
    assert all("applies_reason" in row for row in recommendation.material_plan)
    assert all("source_type" in row for row in recommendation.material_plan)
    assert all("labor_package" in row for row in recommendation.labor_plan)


def test_secondary_material_allowances_use_historical_ratios_with_current_pricing() -> None:
    data = field_data()
    extra_jobs = pd.DataFrame(
        [
            {"job_id": "J2", "estimated_sqft": 12000, "job_name": "Metal roof 2", "division": "ROOFING"},
            {"job_id": "J3", "estimated_sqft": 8000, "job_name": "Metal roof 3", "division": "ROOFING"},
        ]
    )
    data.jobs = pd.concat([data.jobs, extra_jobs], ignore_index=True)
    material_rows = pd.DataFrame(
        [
            {"job_id": "J1", "selected_item_name": "Rust primer", "line_item_kind": "material", "quantity": 24, "unit": "gal", "estimated_cost": 960},
            {"job_id": "J2", "selected_item_name": "Epoxy primer", "line_item_kind": "material", "quantity": 24, "unit": "gal", "estimated_cost": 960},
            {"job_id": "J3", "selected_item_name": "Primer", "line_item_kind": "material", "quantity": 16, "unit": "gal", "estimated_cost": 640},
            {"job_id": "J1", "selected_item_name": "Seam sealer", "line_item_kind": "material", "quantity": 960, "unit": "lf", "estimated_cost": 2880},
            {"job_id": "J2", "selected_item_name": "Seam tape", "line_item_kind": "material", "quantity": 960, "unit": "lf", "estimated_cost": 2880},
            {"job_id": "J3", "selected_item_name": "Detail tape", "line_item_kind": "material", "quantity": 640, "unit": "lf", "estimated_cost": 1920},
            {"job_id": "J1", "selected_item_name": "Fastener screws", "line_item_kind": "material", "quantity": 600, "unit": "ea", "estimated_cost": 900},
            {"job_id": "J2", "selected_item_name": "Rusted fasteners", "line_item_kind": "material", "quantity": 600, "unit": "ea", "estimated_cost": 900},
            {"job_id": "J3", "selected_item_name": "Washer fastener detail", "line_item_kind": "material", "quantity": 400, "unit": "ea", "estimated_cost": 600},
        ]
    )
    data.template_rows = pd.concat([data.template_rows, material_rows], ignore_index=True)
    data.pricing = pd.concat(
        [
            data.pricing,
            pd.DataFrame(
                [
                    {"pricing_item_id": "P2", "product_name": "Rust Primer", "category": "Primer", "unit_price": 42, "status": "active", "is_current": True, "needs_review": False},
                    {"pricing_item_id": "P3", "product_name": "Seam Sealer", "category": "Seam", "unit_price": 3, "status": "active", "is_current": True, "needs_review": False},
                    {"pricing_item_id": "P4", "product_name": "Fastener Dab", "category": "Fastener", "price_per_unit": 1.75, "status": "active", "is_current": True, "needs_review": False},
                ]
            ),
        ],
        ignore_index=True,
    )
    data.pricing_catalog = data.pricing

    recommendation = estimate_from_field_notes("Metal roof 12000 sqft rusted fasteners silicone coating Louisville KY", data=data)
    rows_by_category = {row["category"]: row for row in recommendation.material_plan}

    assert rows_by_category["primer"]["estimated_cost"] is not None
    assert rows_by_category["primer"]["calibration_method"] == "historical_quantity_ratio"
    assert rows_by_category["primer"]["selected_price_source"] == "current_pricing + historical_quantity_ratio"
    assert rows_by_category["primer"]["needs_review"] is True
    assert rows_by_category["seam_treatment"]["estimated_cost"] is not None
    assert rows_by_category["fastener_treatment"]["estimated_cost"] is not None
    assert recommendation.estimate_low > sum(row["cost_low"] for row in recommendation.material_plan if row.get("cost_low"))


def test_roofing_material_rows_prefer_estimated_units_when_quantity_is_scope_area() -> None:
    data = field_data()
    data.template_rows = pd.concat(
        [
            data.template_rows,
            pd.DataFrame(
                [
                    {
                        "job_id": "J1",
                        "template_type": "roofing",
                        "template_bucket": "primer",
                        "selected_item_name": "Epoxy primer",
                        "line_item_kind": "material",
                        "quantity": 12000,
                        "estimated_units": 24,
                        "unit": "",
                        "unit_price": 26.25,
                        "estimated_cost": 630,
                    },
                    {
                        "job_id": "J1",
                        "template_type": "roofing",
                        "template_bucket": "seam_treatment",
                        "selected_item_name": "Seam treatment",
                        "line_item_kind": "material",
                        "quantity": 12000,
                        "estimated_units": 960,
                        "unit": "",
                        "unit_price": 3,
                        "estimated_cost": 2880,
                    },
                    {
                        "job_id": "J1",
                        "template_type": "roofing",
                        "template_bucket": "fastener_treatment",
                        "selected_item_name": "Fastener treatment",
                        "line_item_kind": "material",
                        "quantity": 12000,
                        "estimated_units": 600,
                        "unit": "",
                        "unit_price": 1.75,
                        "estimated_cost": 1050,
                    },
                ]
            ),
        ],
        ignore_index=True,
    )
    data.pricing = pd.concat(
        [
            data.pricing,
            pd.DataFrame(
                [
                    {"product_name": "Epoxy Primer 5 Gal Pail", "category": "Primer", "unit_price": 26.25, "unit_of_measure": "pail", "status": "active", "is_current": True, "needs_review": False},
                    {"product_name": "Seam Sealer", "category": "Seam", "unit_price": 3, "price_per_lf": 3, "status": "active", "is_current": True, "needs_review": False},
                    {"product_name": "Fastener Dab", "category": "Fastener", "price_per_unit": 1.75, "status": "active", "is_current": True, "needs_review": False},
                ]
            ),
        ],
        ignore_index=True,
    )
    data.pricing_catalog = data.pricing

    recommendation = estimate_from_field_notes("Metal roof 12000 sqft rusted fasteners silicone coating Louisville KY", data=data)
    rows_by_category = {row["category"]: row for row in recommendation.material_plan}

    for category in ("primer", "seam_treatment", "fastener_treatment"):
        row = rows_by_category[category]
        assert row["selected_price_source"] == "current_pricing + historical_quantity_ratio"
        assert row["selected_material_calibration_field"] == "estimated_units"
        assert row["estimated_cost"] is not None
        assert row.get("quantity_evidence_diagnostics")
        assert row["quantity_evidence_diagnostics"][0]["chosen_material_quantity_field"] == "estimated_units"
    assert rows_by_category["primer"]["quantity"] < 30
    assert rows_by_category["seam_treatment"]["quantity"] < 12000


def test_fastener_allowance_uses_historical_count_ratio_when_current_price_missing() -> None:
    data = field_data()
    fastener_rows = [
        {
            "job_id": "J1",
            "template_type": "roofing",
            "template_bucket": "fastener_treatment",
            "selected_item_name": f"Fastener treatment {index}",
            "line_item_kind": "material",
            "quantity": 12000,
            "estimated_units": 1164,
            "unit": "",
            "unit_price": 1.75,
            "estimated_cost": 2037,
        }
        for index in range(12)
    ]
    data.template_rows = pd.concat([data.template_rows, pd.DataFrame(fastener_rows)], ignore_index=True)

    recommendation = estimate_from_field_notes("Metal roof 12000 sqft rusted fasteners silicone coating Louisville KY", data=data)
    fastener = {row["category"]: row for row in recommendation.material_plan}["fastener_treatment"]

    assert fastener["selected_price_source"] == "rule_based_unit_price + historical_quantity_ratio"
    assert fastener["calibration_method"] == "historical_quantity_ratio"
    assert fastener["quantity_source"] == "historical_physical_quantity_ratio"
    assert fastener["quantity"] > 1000
    assert fastener["review_required"] is True


def test_secondary_material_allowances_fallback_without_historical_evidence() -> None:
    recommendation = estimate_from_field_notes(
        "Metal roof 12000 sqft rusted fasteners silicone coating Louisville KY",
        data=field_data(),
    )
    rows_by_category = {row["category"]: row for row in recommendation.material_plan}

    assert rows_by_category["primer"]["calibration_method"] == "deterministic_fallback"
    assert rows_by_category["primer"]["estimated_cost"] is not None
    assert rows_by_category["primer"]["needs_review"] is True


def test_bad_primer_pail_ratio_is_rejected_and_capped() -> None:
    data = field_data()
    data.jobs = pd.concat(
        [
            data.jobs,
            pd.DataFrame(
                [
                    {"job_id": "J2", "estimated_sqft": 10000, "job_name": "Bad primer 2", "division": "ROOFING"},
                    {"job_id": "J3", "estimated_sqft": 10000, "job_name": "Bad primer 3", "division": "ROOFING"},
                ]
            ),
        ],
        ignore_index=True,
    )
    data.template_rows = pd.concat(
        [
            data.template_rows,
            pd.DataFrame(
                [
                    {"job_id": "J1", "selected_item_name": "Epoxy primer allowance", "line_item_kind": "material", "quantity": 12000, "unit": "pail", "source_type": "physical_quantity", "physical_quantity_valid": True, "estimated_cost": 315000},
                    {"job_id": "J2", "selected_item_name": "Epoxy primer allowance", "line_item_kind": "material", "quantity": 10000, "unit": "pail", "source_type": "physical_quantity", "physical_quantity_valid": True, "estimated_cost": 262500},
                    {"job_id": "J3", "selected_item_name": "Epoxy primer allowance", "line_item_kind": "material", "quantity": 10000, "unit": "pail", "source_type": "physical_quantity", "physical_quantity_valid": True, "estimated_cost": 262500},
                ]
            ),
        ],
        ignore_index=True,
    )
    data.pricing = pd.concat(
        [
            data.pricing,
            pd.DataFrame([{"product_name": "Epoxy Primer 5 Gal", "category": "Primer", "unit_price": 26.25, "unit_of_measure": "pail", "status": "active", "is_current": True, "needs_review": False}]),
        ],
        ignore_index=True,
    )
    data.pricing_catalog = data.pricing

    recommendation = estimate_from_field_notes(
        "Roof coating estimate for a commercial metal roof in Louisville KY. Main roof is 120 ft by 80 ft. "
        "Deduct two skylights, each 4 ft by 8 ft. Roof is fair overall but has rusted fasteners and some open seams. "
        "Customer wants a 10-year silicone coating system. Access is easy. Few penetrations.",
        data=data,
    )
    rows_by_category = {row["category"]: row for row in recommendation.material_plan}
    primer = rows_by_category["primer"]
    coating = rows_by_category["coating"]

    assert primer["selected_price_source"] != "current_pricing + historical_quantity_ratio"
    assert primer["estimated_cost"] < coating["estimated_cost"]
    assert not (primer.get("unit") == "pail" and (primer.get("quantity") or 0) > 100)
    assert any("Rejected primer historical quantity ratio" in flag for flag in recommendation.review_flags)


def test_valid_primer_pail_ratio_can_be_used() -> None:
    data = field_data()
    data.jobs = pd.concat(
        [
            data.jobs,
            pd.DataFrame(
                [
                    {"job_id": "J2", "estimated_sqft": 10000, "job_name": "Primer 2", "division": "ROOFING"},
                    {"job_id": "J3", "estimated_sqft": 8000, "job_name": "Primer 3", "division": "ROOFING"},
                ]
            ),
        ],
        ignore_index=True,
    )
    data.template_rows = pd.concat(
        [
            data.template_rows,
            pd.DataFrame(
                [
                    {"job_id": "J1", "selected_item_name": "Primer", "line_item_kind": "material", "quantity": 24, "unit": "pail", "source_type": "physical_quantity", "physical_quantity_valid": True, "estimated_cost": 630},
                    {"job_id": "J2", "selected_item_name": "Primer", "line_item_kind": "material", "quantity": 20, "unit": "pail", "source_type": "physical_quantity", "physical_quantity_valid": True, "estimated_cost": 525},
                    {"job_id": "J3", "selected_item_name": "Primer", "line_item_kind": "material", "quantity": 16, "unit": "pail", "source_type": "physical_quantity", "physical_quantity_valid": True, "estimated_cost": 420},
                ]
            ),
        ],
        ignore_index=True,
    )
    data.pricing = pd.concat(
        [
            data.pricing,
            pd.DataFrame([{"product_name": "Primer 5 Gal", "category": "Primer", "unit_price": 26.25, "unit_of_measure": "pail", "status": "active", "is_current": True, "needs_review": False}]),
        ],
        ignore_index=True,
    )
    data.pricing_catalog = data.pricing

    recommendation = estimate_from_field_notes("Metal roof 9536 sqft rusted fasteners silicone coating Louisville KY", data=data)
    primer = {row["category"]: row for row in recommendation.material_plan}["primer"]

    assert primer["selected_price_source"] == "current_pricing + historical_quantity_ratio"
    assert primer["quantity"] < 40
    assert primer["estimated_cost"] is not None


def test_primer_sqft_rows_use_cost_or_rule_not_physical_pail_quantity() -> None:
    data = field_data()
    data.jobs = pd.concat(
        [
            data.jobs,
            pd.DataFrame(
                [
                    {"job_id": "J2", "estimated_sqft": 10000, "job_name": "Primer sqft 2", "division": "ROOFING"},
                    {"job_id": "J3", "estimated_sqft": 8000, "job_name": "Primer sqft 3", "division": "ROOFING"},
                ]
            ),
        ],
        ignore_index=True,
    )
    data.template_rows = pd.concat(
        [
            data.template_rows,
            pd.DataFrame(
                [
                    {"job_id": "J1", "selected_item_name": "Primer allowance", "line_item_kind": "material", "quantity": 12000, "unit": "sqft", "source_type": "cost_allowance", "estimated_cost": 3000},
                    {"job_id": "J2", "selected_item_name": "Primer allowance", "line_item_kind": "material", "quantity": 10000, "unit": "sqft", "source_type": "cost_allowance", "estimated_cost": 2500},
                    {"job_id": "J3", "selected_item_name": "Primer allowance", "line_item_kind": "material", "quantity": 8000, "unit": "sqft", "source_type": "cost_allowance", "estimated_cost": 2000},
                ]
            ),
        ],
        ignore_index=True,
    )
    data.pricing = pd.concat(
        [
            data.pricing,
            pd.DataFrame([{"product_name": "Epoxy Primer 5 Gal", "category": "Primer", "unit_price": 26.25, "unit_of_measure": "pail", "status": "active", "is_current": True, "needs_review": False}]),
        ],
        ignore_index=True,
    )
    data.pricing_catalog = data.pricing

    recommendation = estimate_from_field_notes("Metal roof 9536 sqft rusted fasteners silicone coating Louisville KY", data=data)
    primer = {row["category"]: row for row in recommendation.material_plan}["primer"]

    assert primer["selected_price_source"] == "rule_based_allowance"
    assert primer["calibration_method"] == "deterministic_fallback"
    assert primer["quantity_source"] == "deterministic_rule"
    assert primer["unit_price_source"] == "rule_based_allowance"
    assert primer["quantity"] == 9536
    assert primer["estimated_cost"] is not None
    assert primer["estimated_cost"] < {row["category"]: row for row in recommendation.material_plan}["coating"]["estimated_cost"]
    assert any("Current pricing exists" in flag and "no valid physical quantity evidence" in flag for flag in recommendation.review_flags)


def test_sanity_rejected_historical_quantity_row_is_non_cost_bearing() -> None:
    data = EstimatorData(
        pricing=pd.DataFrame(
            [
                {"product_name": "High Solids Silicone", "category": "Coating", "price_per_gallon": 38, "status": "active", "is_current": True, "needs_review": False},
                {"product_name": "Epoxy Primer 5 Gal", "category": "Primer", "unit_price": 26.25, "unit_of_measure": "pail", "status": "active", "is_current": True, "needs_review": False},
            ]
        )
    )
    data.pricing_catalog = data.pricing
    scope = {
        "surface_area_sqft": 9536,
        "estimated_sqft": 9536,
        "coating_required": True,
        "coating_type": "silicone",
        "substrate": "metal",
        "notes": "Metal roof rusted fasteners silicone coating Louisville KY",
    }
    decision = {
        "work_package_decisions": {
            "coating": {"package_name": "coating", "applies": True, "review_required": False, "reason": "coating"},
            "primer": {"package_name": "primer", "applies": True, "review_required": True, "reason": "primer"},
            "seam_treatment": {"package_name": "seam_treatment", "applies": False, "review_required": False},
            "fastener_treatment": {"package_name": "fastener_treatment", "applies": False, "review_required": False},
            "caulk_detail": {"package_name": "caulk_detail", "applies": False, "review_required": False},
        }
    }
    calibration = {
        "material_calibration": {
            "primer": {
                "evidence_count": 3,
                "median_quantity_per_sqft": 0.003,
                "selected_current_unit_price": 26.25,
                "selected_current_price_column": "unit_price",
                "selected_current_price_item": {"product_name": "Epoxy Primer 5 Gal", "unit_of_measure": "pail"},
                "unit": "pail",
            }
        }
    }

    material_plan, low, high, review_flags = build_material_plan(scope, data, calibration, decision, EstimatorAssumptions())
    primer = {row["category"]: row for row in material_plan}["primer"]

    assert primer["selected_price_source"] == "rejected_historical_quantity_ratio"
    assert primer["quantity"] is None
    assert primer["estimated_cost"] is None
    assert primer["cost_low"] is None
    assert primer["cost_high"] is None
    assert primer["rejected_quantity"] is not None
    assert all("262438" not in str(value) for value in primer.values())
    assert low > 0
    assert high > 0


def test_roof_coating_includes_ir_scan_when_requested() -> None:
    recommendation = estimate_from_field_notes(
        "Metal roof 12000 sqft silicone coating Louisville KY include IR scan",
        data=field_data(),
    )

    assert "infrared_scan" in {row["task"] for row in recommendation.labor_plan}


def test_roof_coating_includes_granules_when_requested() -> None:
    recommendation = estimate_from_field_notes(
        "Metal roof 12000 sqft silicone coating with granules Louisville KY",
        data=field_data(),
    )

    assert "labor_top_coat_granules" in {row["task"] for row in recommendation.labor_plan}


def test_field_estimator_handles_sample_dimension_note_with_zero_sqft_override() -> None:
    note = (
        "Roof coating estimate. Metal roof in Louisville KY. Main roof is 120 ft by 80 ft. "
        "Deduct two skylight areas, each 4 ft by 8 ft. Roof condition is fair with some rusted fasteners. "
        "Customer wants a 10-year silicone coating system. Access is easy. Few penetrations."
    )

    recommendation = estimate_from_field_notes(note, {"estimated_sqft": 0, "warranty_target_years": 0}, data=field_data())
    dimensions = recommendation.parsed_fields["dimension_summary"]

    assert dimensions["gross_area_sqft"] == 9600
    assert dimensions["deduction_area_sqft"] == 64
    assert dimensions["net_area_sqft"] == 9536
    assert recommendation.parsed_fields["estimated_sqft"] == 9536
    assert recommendation.parsed_fields.get("surface_area_sqft") == 9536
    assert recommendation.draft_workbook_inputs["header"]["C12_estimated_sqft"] == 9536
    assert recommendation.draft_workbook_inputs["header"]["gross_area_sqft"] == 9600
    assert recommendation.draft_workbook_inputs["header"]["deduction_area_sqft"] == 64
    assert recommendation.draft_workbook_inputs["header"]["net_area_sqft"] == 9536
    assert not any(flag == "Missing: estimated_sqft" for flag in recommendation.review_flags)


def test_field_estimator_sample_note_works_with_data_none(monkeypatch) -> None:
    monkeypatch.setattr(field_estimator_module, "load_estimator_data", lambda *args, **kwargs: EstimatorData())
    note = (
        "Roof coating estimate. Metal roof in Louisville KY. Main roof is 120 ft by 80 ft. "
        "Deduct two skylight areas, each 4 ft by 8 ft. Roof condition is fair with some rusted fasteners. "
        "Customer wants a 10-year silicone coating system. Access is easy. Few penetrations."
    )

    recommendation = estimate_from_field_notes(note, {"estimated_sqft": 0, "warranty_target_years": 0}, data=None)

    assert recommendation.parsed_fields["estimated_sqft"] == 9536
    assert recommendation.parsed_fields["dimension_summary"]["gross_area_sqft"] == 9600
    assert recommendation.parsed_fields["dimension_summary"]["deduction_area_sqft"] == 64
    assert recommendation.parsed_fields["dimension_summary"]["net_area_sqft"] == 9536
    assert any("Historical labor calibration unavailable or incomplete" in flag for flag in recommendation.review_flags)
    assert recommendation.draft_workbook_inputs["header"]["C12_estimated_sqft"] == 9536
    assert not any(flag == "Missing: estimated_sqft" for flag in recommendation.review_flags)


def test_field_estimator_sample_note_without_overrides_uses_dimension_sqft(monkeypatch) -> None:
    monkeypatch.setattr(field_estimator_module, "load_estimator_data", lambda *args, **kwargs: EstimatorData())
    note = (
        "Roof coating estimate. Metal roof in Louisville KY. Main roof is 120 ft by 80 ft. "
        "Deduct two skylight areas, each 4 ft by 8 ft. Roof condition is fair with some rusted fasteners. "
        "Customer wants a 10-year silicone coating system. Access is easy. Few penetrations."
    )

    recommendation = estimate_from_field_notes(note, data=None)

    assert recommendation.parsed_fields["estimated_sqft"] == 9536
    assert recommendation.draft_workbook_inputs["header"]["C12_estimated_sqft"] == 9536
    assert not any(flag == "Missing: estimated_sqft" for flag in recommendation.review_flags)


def test_field_estimator_returns_recommendation_when_labor_plan_raises(monkeypatch) -> None:
    def broken_labor_plan(*args, **kwargs):
        raise ValueError("bad labor history")

    monkeypatch.setattr(field_estimator_module, "build_labor_plan", broken_labor_plan)

    recommendation = estimate_from_field_notes("Metal roof 12000 sqft silicone coating Louisville KY", data=field_data())

    assert recommendation.parsed_fields["estimated_sqft"] == 12000
    assert recommendation.labor_plan[0]["task"] == "labor_allowance"
    assert recommendation.labor_plan[0]["needs_review"] is True
    assert any("Historical labor calibration failed" in flag for flag in recommendation.review_flags)


def test_insulation_email_routes_and_parses_building_dimensions() -> None:
    recommendation = estimate_from_field_notes(INSULATION_EMAIL, data=EstimatorData())
    parsed = recommendation.parsed_fields

    assert parsed["division"] == "Insulation"
    assert parsed["template_type"] == "insulation"
    assert parsed["project_type"] == "spray foam insulation"
    assert parsed["building_type"] == "metal building"
    assert parsed["building_footprint_length_ft"] == 30
    assert parsed["building_footprint_width_ft"] == 40
    assert parsed["wall_height_ft"] == 9
    assert parsed["ceiling_included"] is True
    assert parsed["outside_walls_included"] is True
    assert parsed["ceiling_area_sqft"] == 1200
    assert parsed["gross_wall_area_sqft"] == 1260
    assert parsed["gross_insulation_area_sqft"] == 2460
    assert parsed["opening_area_known_sqft"] == 72
    assert parsed["opening_area_missing"] is True
    assert parsed["net_insulation_area_sqft"] == 2388
    assert parsed["estimated_sqft"] == 2388
    walk_in = next(opening for opening in parsed["openings"] if opening["opening_type"] == "walk_in_door")
    windows = next(opening for opening in parsed["openings"] if opening["opening_type"] == "window")
    rollup = next(opening for opening in parsed["openings"] if opening["opening_type"] == "rollup_door")
    assert walk_in["known_area_sqft"] == 42
    assert walk_in["height_ft"] == 7
    assert windows["known_area_sqft"] == 30
    assert "width_ft" in rollup["missing_dimensions"]
    assert any("Walk-in door height assumed 7 ft" in item for item in parsed["assumptions"])
    assert parsed["requested_timing"] == "September or October"
    assert parsed["building_installation_timing"] == "beginning to mid-August"
    assert parsed["customer_name"] == "James F. Collins"
    assert parsed["phone"] == "513-319-2779"
    assert parsed["address"] == "314 E Aberdeen Drive, Trenton, OH"
    assert any("Rollup door width" in question for question in recommendation.required_questions)
    assert not any("Walk-in door height" in question for question in recommendation.required_questions)
    assert any("foam type" in question.lower() for question in recommendation.required_questions)
    assert any("thickness or R-value" in question for question in recommendation.required_questions)
    assert recommendation.estimate_status == "READY_TO_ESTIMATE"
    assert recommendation.estimate_low is None
    assert not recommendation.material_plan or all(row.get("category") != "coating" for row in recommendation.material_plan)


def test_insulation_explicit_opening_dimensions_compute_net_area() -> None:
    notes = (
        "Need foam sprayed in a 30x40 metal building with 9' walls. "
        "Insulate the outside walls and ceiling. "
        "The building has two 9ftX10ft rollup doors, two 7ftX36\" walk-in doors, and five 24\"x36\" windows."
    )

    recommendation = estimate_from_field_notes(notes, data=EstimatorData())
    parsed = recommendation.parsed_fields

    assert parsed["ceiling_area_sqft"] == 1200
    assert parsed["gross_wall_area_sqft"] == 1260
    assert parsed["gross_insulation_area_sqft"] == 2460
    assert parsed["opening_area_known_sqft"] == 252
    assert parsed["opening_area_missing"] is False
    assert parsed["net_insulation_area_sqft"] == 2208
    assert parsed["estimated_sqft"] == 2208


def test_insulation_notes_parse_surface_r_value_targets() -> None:
    notes = (
        "Need closed-cell foam in a 30x40 metal building with 9' walls. "
        "Insulate the outside walls and ceiling. Walls target R14 and ceiling target R30. "
        "No deductions."
    )
    recommendation = estimate_from_field_notes(notes, data=EstimatorData())
    parsed = recommendation.parsed_fields

    targets = {row["surface_type"]: row["target_r_value"] for row in parsed["insulation_r_value_targets"]}
    assert targets["walls"] == 14
    assert targets["ceiling"] == 30
    surfaces = {row["surface_type"]: row for row in parsed["insulation_surface_areas"]}
    assert surfaces["walls"]["gross_area_sqft"] == 1260
    assert surfaces["ceiling"]["gross_area_sqft"] == 1200
    assert parsed["foam_type"] == "closed_cell"
    assert not any("thickness or R-value" in question for question in recommendation.required_questions)


def test_line_item_classification_fallback_remains_available() -> None:
    recommendation = estimate_from_field_notes(
        "Metal roof 12000 sqft silicone coating Louisville KY",
        data=field_data(with_template_rows=False, with_fallback=True),
    )

    assert any("estimate_line_item_classifications fallback" in flag for flag in recommendation.review_flags)


def test_field_estimator_returns_low_target_high_and_workbook_inputs() -> None:
    recommendation = estimate_from_field_notes(
        "Metal roof, about 12,000 sqft, rusted fasteners, wants 15-year warranty, lots of rooftop units, medium access, Louisville KY.",
        {"job_name": "Test Job", "site_address": "123 Main St", "city": "Louisville", "state": "KY"},
        data=field_data(),
    )

    assert recommendation.estimate_low > 0
    assert recommendation.estimate_low < recommendation.estimate_target < recommendation.estimate_high
    assert recommendation.draft_workbook_inputs["header"]["C2_job_name"] == "Test Job"
    assert recommendation.similar_examples
