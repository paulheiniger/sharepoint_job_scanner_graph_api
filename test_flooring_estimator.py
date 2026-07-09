from __future__ import annotations

import openpyxl
import pandas as pd

from jobscan.estimator.schemas import EstimatorData
from jobscan.flooring_estimator.estimator import estimate_flooring_from_notes
from jobscan.flooring_estimator.workbook_writer import (
    generate_flooring_estimate_workbook,
    resolve_flooring_template_path,
)


def test_flooring_estimator_parses_concrete_epoxy_polyaspartic_scope() -> None:
    result = estimate_flooring_from_notes(
        "Flooring job, 2,400 sq ft concrete slab. Grind and patch prep, epoxy 707 base, "
        "polyaspartic top coat, flake broadcast, generator needed.",
    )

    assert result.parsed_scope["area_sqft"] == 2400
    assert result.parsed_scope["template_type"] == "flooring"
    assert result.parsed_scope["system"] == "epoxy_polyaspartic"
    assert result.parsed_scope["substrate"] == "concrete"
    assert result.parsed_scope["prep_required"] is True
    assert result.parsed_scope["flake_broadcast"] is True
    assert result.parsed_scope["generator_required"] is True

    rows = {decision["workbook_row"] for decision in result.workbook_decisions}
    assert {26, 27, 99, 116, 120, 130, 137, 139, 177}.issubset(rows)


def test_flooring_estimator_uses_historical_relationship_defaults() -> None:
    data = EstimatorData(
        relationship_material_qty_ratios=pd.DataFrame(
            [
                {
                    "division": "Flooring",
                    "template_type": "roofing",
                    "project_type": "Floor System",
                    "substrate": "unknown",
                    "package": "coating",
                    "unit": "gal",
                    "median_qty_per_sqft": 0.0256,
                    "median_cost_per_sqft": 1.6,
                    "job_count": 4,
                    "confidence": "medium",
                },
                {
                    "division": "Flooring",
                    "template_type": "flooring",
                    "project_type": "Floor System",
                    "substrate": "unknown",
                    "package": "floor_flake",
                    "median_cost_per_sqft": 0.4,
                    "job_count": 2,
                    "confidence": "low",
                },
            ]
        ),
        relationship_labor_rates=pd.DataFrame(
            [
                {
                    "division": "Flooring",
                    "template_type": "roofing",
                    "project_type": "Floor System",
                    "substrate": "unknown",
                    "package": "labor_base",
                    "median_hours_per_sqft": 0.006,
                    "median_total_hours": 12,
                    "median_crew_size": 4,
                    "median_days": 0.5,
                    "job_count": 4,
                    "confidence": "medium",
                },
                {
                    "division": "Flooring",
                    "template_type": "roofing",
                    "project_type": "Floor System",
                    "substrate": "unknown",
                    "package": "labor_top_coat",
                    "median_hours_per_sqft": 0.004,
                    "median_total_hours": 8,
                    "median_crew_size": 2,
                    "median_days": 0.5,
                    "job_count": 4,
                    "confidence": "medium",
                },
                {
                    "division": "Flooring",
                    "template_type": "roofing",
                    "project_type": "Floor System",
                    "substrate": "unknown",
                    "package": "labor_loading",
                    "median_total_hours": 0.5,
                    "median_crew_size": 1,
                    "median_days": None,
                    "job_count": 4,
                    "confidence": "medium",
                },
                {
                    "division": "Flooring",
                    "template_type": "roofing",
                    "project_type": "Floor System",
                    "substrate": "unknown",
                    "package": "labor_traveling",
                    "median_total_hours": 1.25,
                    "median_crew_size": 3,
                    "median_days": None,
                    "job_count": 4,
                    "confidence": "medium",
                },
            ]
        ),
    )

    result = estimate_flooring_from_notes(
        "Flooring job, 2,000 sq ft concrete slab, epoxy base, polyaspartic top coat, flake.",
        data=data,
    )
    by_row = {decision["workbook_row"]: decision for decision in result.workbook_decisions}

    assert by_row[26]["include_source"] == "historical_flooring_coating_fallback"
    assert by_row[26]["gal_per_100_sqft"] == 1.6
    assert by_row[26]["unit_price"] == 62.5
    assert by_row[27]["gal_per_100_sqft"] == 0.96
    assert by_row[27]["unit_price"] == 62.5
    assert by_row[120]["include_source"] == "historical_labor_rate"
    assert by_row[120]["crew_size"] == 4
    assert by_row[120]["days"] == 0.38
    assert by_row[130]["days"] == 0.5
    assert by_row[137]["hours_per_trip"] == 0.5
    assert by_row[137]["include_source"] == "historical_labor_rate"
    assert by_row[139]["hours_per_trip"] == 1.25
    assert by_row[139]["include_source"] == "historical_labor_rate"
    assert by_row[177]["estimated_cost"] == 800
    assert "Historical coating default" in by_row[26]["historical_evidence_summary"]


def test_flooring_logistics_ignores_stale_full_day_history() -> None:
    data = EstimatorData(
        relationship_labor_rates=pd.DataFrame(
            [
                {
                    "division": "Flooring",
                    "template_type": "roofing",
                    "project_type": "Floor System",
                    "substrate": "unknown",
                    "package": "labor_loading",
                    "median_total_hours": 8,
                    "median_crew_size": 1,
                    "job_count": 2,
                    "confidence": "low",
                },
                {
                    "division": "Flooring",
                    "template_type": "roofing",
                    "project_type": "Floor System",
                    "substrate": "unknown",
                    "package": "labor_traveling",
                    "median_total_hours": 8,
                    "median_crew_size": 3,
                    "job_count": 2,
                    "confidence": "low",
                },
            ]
        )
    )

    result = estimate_flooring_from_notes("Flooring job, 2,000 sq ft concrete slab.", data=data)
    by_row = {decision["workbook_row"]: decision for decision in result.workbook_decisions}

    assert by_row[137]["hours_per_trip"] == 0.5
    assert by_row[137]["include_source"] == "template_formula_default"
    assert by_row[139]["hours_per_trip"] == 1.0
    assert by_row[139]["include_source"] == "template_formula_default"


def test_generate_flooring_estimate_workbook_fills_template_inputs(tmp_path) -> None:
    result = estimate_flooring_from_notes(
        "Flooring job, 2,400 sq ft concrete slab. Grind and patch prep, epoxy 707 base, "
        "polyaspartic top coat, flake broadcast, generator needed.",
    )

    output_path = generate_flooring_estimate_workbook(
        result,
        template_path=resolve_flooring_template_path(),
        output_dir=tmp_path,
        output_filename="flooring_filled.xlsx",
        job_name="Lee Sporting Shop Flooring",
        site_address="1 Main St",
        city_state_zip="Louisville, KY",
        contact_name="Jane Customer",
        contact_phone="555-0100",
        contact_email="jane@example.com",
        estimator="Estimator One",
        round_trip_miles=22,
    )

    assert output_path.exists()
    filled = openpyxl.load_workbook(output_path, data_only=False)
    estimate = filled["Estimate"]

    assert estimate["C2"].value == "Lee Sporting Shop Flooring"
    assert estimate["C3"].value == "Floor System"
    assert estimate["C4"].value == "1 Main St"
    assert estimate["C5"].value == "Louisville, KY"
    assert estimate["C12"].value == 2400
    assert estimate["C26"].value == 2400
    assert estimate["D26"].value == 1
    assert estimate["E26"].value == 45
    assert estimate["C27"].value == 2400
    assert estimate["D27"].value == 0.6
    assert estimate["E27"].value == 77.1
    assert estimate["C99"].value >= 1
    assert estimate["B108"].value >= 1
    assert estimate["C108"].value == 22
    assert estimate["B116"].value >= 1
    assert estimate["C116"].value == 3
    assert estimate["B120"].value >= 0.5
    assert estimate["B130"].value >= 0.5
    assert estimate["F177"].value == 1320
    assert estimate["H169"].value == "=H111+H163+H165+H167"
    assert estimate["B184"].value == "=H170/C12"
