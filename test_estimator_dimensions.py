from __future__ import annotations

from jobscan.estimator.dimensions import parse_dimensions
from jobscan.estimator.field_estimator import estimate_from_field_notes
from jobscan.estimator.field_notes import parse_field_notes
from dashboard.app import optional_positive_number
from test_field_estimator import field_data


def test_basic_include_dimension_math() -> None:
    summary = parse_dimensions("Main roof is 120 ft by 80 ft.")

    assert summary.gross_area_sqft == 9600
    assert summary.deduction_area_sqft == 0
    assert summary.net_area_sqft == 9600


def test_include_plus_quantity_deduction() -> None:
    summary = parse_dimensions("Main roof is 120 ft by 80 ft. Deduct two skylight areas, each 4 ft by 8 ft.")

    assert summary.gross_area_sqft == 9600
    assert summary.deduction_area_sqft == 64
    assert summary.net_area_sqft == 9536
    assert summary.deducted_areas[0].quantity == 2


def test_multiple_sections_with_deduction() -> None:
    summary = parse_dimensions("Area A is 65 ft x 90 ft. Area B is 40 ft x 55 ft. Deduct 12 ft x 20 ft overhang.")

    assert summary.gross_area_sqft == 8050
    assert summary.deduction_area_sqft == 240
    assert summary.net_area_sqft == 7810


def test_deduct_dimension_list() -> None:
    summary = parse_dimensions("Roof is 200 ft by 160 ft. Deduct 3 roof sections: 20x20, 15x30, and 10x40.")

    assert summary.gross_area_sqft == 32000
    assert summary.deduction_area_sqft == 1250
    assert summary.net_area_sqft == 30750
    assert [area.quantity for area in summary.deducted_areas] == [1, 1, 1]


def test_wall_insulation_with_overhead_door_deductions() -> None:
    summary = parse_dimensions(
        "North wall 120 ft x 18 ft, south wall 120 ft x 18 ft. Deduct 8 overhead doors at 12 ft x 14 ft."
    )

    assert summary.gross_area_sqft == 4320
    assert summary.deduction_area_sqft == 1344
    assert summary.net_area_sqft == 2976


def test_deduct_multiple_opening_quantities() -> None:
    summary = parse_dimensions(
        "North wall is 120 ft by 18 ft. South wall is 120 ft by 18 ft. "
        "East wall is 80 ft by 18 ft. West wall is 80 ft by 18 ft. "
        "Deduct 8 overhead doors at 12 ft by 14 ft and 10 windows at 4 ft by 5 ft."
    )

    assert summary.gross_area_sqft == 7200
    assert summary.deduction_area_sqft == 1544
    assert summary.net_area_sqft == 5656
    assert [area.quantity for area in summary.deducted_areas] == [8, 10]


def test_direct_sqft_still_parses() -> None:
    parsed = parse_field_notes("Roof is about 12,000 sqft.")

    assert parsed.estimated_sqft == 12000


def test_range_sqft_uses_midpoint_and_warns() -> None:
    parsed = parse_field_notes("Roof is around 7-8k sqft.")

    assert parsed.estimated_sqft == 7500
    assert any("range" in warning.lower() for warning in parsed.review_flags)


def test_direct_sqft_and_dimensions_conflict_warns() -> None:
    parsed = parse_field_notes("Roof is 12,000 sqft. Main roof is 100x80.")

    assert parsed.estimated_sqft == 8000
    assert any("differs from stated sqft" in warning for warning in parsed.review_flags)
    assert parsed.dimension_summary["stated_sqft"] == 12000


def test_ui_override_takes_priority_over_dimension_math() -> None:
    recommendation = estimate_from_field_notes(
        "Main roof is 120 ft by 100 ft. Silicone coating Louisville KY.",
        {"estimated_sqft": 15000},
        data=field_data(),
    )

    assert recommendation.parsed_fields["estimated_sqft"] == 15000
    assert recommendation.draft_workbook_inputs["header"]["C12_estimated_sqft"] == 15000
    assert recommendation.draft_workbook_inputs["header"]["net_area_sqft"] == 12000
    assert recommendation.parsed_fields["dimension_summary"]["net_area_sqft"] == 12000
    assert any("override differs from dimension math" in flag.lower() for flag in recommendation.review_flags)


def test_dashboard_optional_positive_number_sanitizes_empty_defaults() -> None:
    assert optional_positive_number(0) is None
    assert optional_positive_number(0.0) is None
    assert optional_positive_number("") is None
    assert optional_positive_number(float("nan")) is None
    assert optional_positive_number(15000) == 15000
