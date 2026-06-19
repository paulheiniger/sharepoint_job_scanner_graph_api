from __future__ import annotations

import pandas as pd

from jobscan.estimator.field_estimator import estimate_from_field_notes
from jobscan.estimator.field_notes import parse_field_notes, parse_field_sqft
from jobscan.estimator.schemas import EstimatorData


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


def test_missing_sqft_triggers_review() -> None:
    recommendation = estimate_from_field_notes("Metal roof, rusted fasteners, silicone coating, Louisville KY", data=field_data())

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
