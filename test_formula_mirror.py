from __future__ import annotations

from jobscan.estimator.formula_mirror import (
    calculate_insulation_foam,
    calculate_insulation_thermal_barrier,
    calculate_mixed_labor,
    calculate_roofing_coating,
)


def test_insulation_foam_formula_mirrors_template_sets_and_cost() -> None:
    result = calculate_insulation_foam(
        area_sqft=2800,
        thickness_inches=4.25,
        yield_or_coverage=13500,
        unit_price=1.63,
    )

    assert round(result["estimated_units"], 2) == 881.48
    assert round(result["estimated_sets"], 6) == 0.881481
    assert result["estimated_cost"] == 1436.81
    assert result["formula_model"] == "foam_sets_from_area_thickness_yield"


def test_insulation_thermal_barrier_formula_mirrors_dc315_gallons() -> None:
    result = calculate_insulation_thermal_barrier(
        area_sqft=2400,
        gal_per_100_sqft=1.5,
        waste_factor_pct=20,
        unit_price=52,
    )

    assert result["estimated_gallons"] == 45
    assert result["estimated_cost"] == 2340
    assert result["formula_model"] == "coating_gallons_from_area_rate_waste"


def test_roofing_coating_formula_calculates_gallons_wet_mils_and_cost() -> None:
    result = calculate_roofing_coating(
        area_sqft=10000,
        gal_per_100_sqft=1.5,
        unit_price=42,
    )

    assert result["estimated_gallons"] == 150
    assert result["wet_mils_estimate"] == 24
    assert result["estimated_cost"] == 6300


def test_mixed_labor_formula_uses_days_when_hours_are_zero() -> None:
    days_based = calculate_mixed_labor(
        days=2,
        crew_size=4,
        total_hours=0,
        daily_rate=1200,
        hourly_rate=75,
        formula_mode="mixed_formula",
    )
    hours_based = calculate_mixed_labor(
        days=2,
        crew_size=4,
        total_hours=50,
        daily_rate=1200,
        hourly_rate=75,
        formula_mode="mixed_formula",
    )

    assert days_based["estimated_cost"] == 2400
    assert days_based["formula_source"] == "days_daily_rate"
    assert hours_based["estimated_cost"] == 3750
    assert hours_based["formula_source"] == "hours_hourly_rate"
