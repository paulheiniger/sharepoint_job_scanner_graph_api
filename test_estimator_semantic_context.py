from __future__ import annotations

import json

import pytest

from jobscan.estimator.semantic_context import build_semantic_observations
from services.estimator_api.schemas import EstimateContextResponse


@pytest.fixture
def source_links() -> list[dict]:
    return [
        {
            "source_type": "historical_estimate",
            "example_id": "EX-1",
            "job_id": "JOB-1",
            "customer": "Example Customer",
            "job_name": "Example Building",
            "document_id": "DOC-1",
            "file_name": "Estimate JOB-1.xlsx",
            "file_web_url": "https://example.invalid/Estimate-JOB-1.xlsx",
            "job_folder_web_url": "https://example.invalid/JOB-1",
            "folder_path": "Jobs/JOB-1/Estimates",
            "relative_path": "Estimates/Estimate JOB-1.xlsx",
        }
    ]


def test_insulation_material_history_is_semantic_and_source_backed(
    source_links,
) -> None:
    observations = build_semantic_observations(
        template_type="insulation",
        decision_evidence=[
            {
                "decision_id": "insulation_foam_row_19",
                "workbook_row": "19",
                "template_bucket": "foam",
                "line_item": "Closed Cell SPF",
                "sample_inputs": {
                    "basis_sqft": 2226,
                    "thickness_inches": 2,
                    "estimated_sets": 2.4,
                    "yield_or_coverage": 4300,
                    "unit_price": 1800,
                },
                "sample_outputs": {"estimated_cost": 4320},
                "support_count": 2,
                "confidence": 0.84,
                "formula_ready": True,
                "examples": [
                    {
                        "job_id": "JOB-1",
                        "label": "Example Building",
                        "similarity_score": 0.91,
                        "reference_area_sqft": 2200,
                    }
                ],
            }
        ],
        matched_comparables=[],
        source_links=source_links,
    )

    material = observations["historical_material_usage"][0]
    assert material["concept_id"] == "insulation.foam"
    assert material["material_name"] == "Closed Cell SPF"
    assert material["quantity_measurements"] == [
        {"name": "estimated_sets", "value": 2.4, "unit": "set"}
    ]
    assert {"name": "basis_sqft", "value": 2226.0, "unit": "sqft"} in material[
        "basis_measurements"
    ]
    assert {"name": "thickness_inches", "value": 2.0, "unit": "in"} in material[
        "application_parameters"
    ]
    assert material["estimated_cost"] == 4320
    assert material["sources"][0]["file_name"] == "Estimate JOB-1.xlsx"
    assert "row_19" not in json.dumps(material)


def test_roofing_labor_history_preserves_productivity_not_row_identity(
    source_links,
) -> None:
    observations = build_semantic_observations(
        template_type="roofing",
        decision_evidence=[
            {
                "decision_id": "roofing_labor_top_coat_row_124",
                "workbook_row": "124",
                "template_bucket": "labor_top_coat",
                "line_item": "Apply top coat",
                "sample_inputs": {
                    "total_hours": 50,
                    "crew_size": 4,
                    "days": 1.25,
                    "labor_driver_quantity": 144,
                },
                "labor_driver": {
                    "labor_driver_type": "material_quantity",
                    "labor_driver_unit": "gal",
                    "labor_driver_rate_unit": "labor_hours_per_gal",
                    "historical_driver_rate": 50 / 144,
                    "historical_driver_evidence_count": 2,
                },
                "sample_outputs": {"estimated_cost": 2750},
                "support_count": 2,
                "confidence": 0.79,
                "examples": [{"job_id": "JOB-1", "label": "Example Building"}],
            }
        ],
        matched_comparables=[],
        source_links=source_links,
    )

    labor = observations["historical_labor_performance"][0]
    assert labor["concept_id"] == "roofing.labor_top_coat"
    assert labor["total_hours"] == 50
    assert labor["crew_size"] == 4
    assert labor["productivity"] == {
        "driver_type": "material_quantity",
        "driver_quantity": 144.0,
        "driver_unit": "gal",
        "rate": pytest.approx(50 / 144),
        "rate_unit": "labor_hours_per_gal",
        "evidence_count": 2,
    }
    assert "row_124" not in json.dumps(labor)


def test_comparable_becomes_template_neutral_historical_assembly(
    source_links,
) -> None:
    observations = build_semantic_observations(
        template_type="roofing",
        decision_evidence=[],
        matched_comparables=[
            {
                "example_id": "EX-1",
                "job_id": "JOB-1",
                "job_name": "Example Building",
                "source_file": "Estimate JOB-1.xlsx",
                "building_type": "metal building",
                "substrate": "metal",
                "material_system": "acrylic restoration",
                "area_sqft": 12000,
                "historical_decision_categories": [
                    "primer",
                    "coating",
                    "labor_top_coat",
                ],
                "similarity_score": 0.88,
                "match_reasons": ["same substrate", "similar area"],
            }
        ],
        source_links=source_links,
    )

    assembly = observations["historical_assemblies"][0]
    assert assembly["template_type"] == "roofing"
    assert assembly["decision_categories"] == [
        "primer",
        "coating",
        "labor_top_coat",
    ]
    assert assembly["sources"][0]["document_id"] == "DOC-1"


def test_material_observation_identity_survives_template_row_change(
    source_links,
) -> None:
    def observation_for(row: str) -> dict:
        observations = build_semantic_observations(
            template_type="insulation",
            decision_evidence=[
                {
                    "decision_id": f"insulation_foam_row_{row}",
                    "workbook_row": row,
                    "template_bucket": "foam",
                    "line_item": "Closed Cell SPF",
                    "sample_inputs": {
                        "basis_sqft": 2226,
                        "estimated_sets": 2.4,
                    },
                    "examples": [{"job_id": "JOB-1"}],
                }
            ],
            matched_comparables=[],
            source_links=source_links,
        )
        return observations["historical_material_usage"][0]

    original = observation_for("19")
    reordered = observation_for("47")

    assert original["observation_id"] == reordered["observation_id"]
    assert original["concept_id"] == reordered["concept_id"] == "insulation.foam"


def test_phase_a_observations_validate_through_action_schema(
    source_links,
) -> None:
    observations = build_semantic_observations(
        template_type="insulation",
        decision_evidence=[],
        matched_comparables=[],
        source_links=source_links,
    )
    response = EstimateContextResponse.model_validate(
        {
            "schema_version": "spraytec.copilot_estimator_context.v1",
            "scope": {"template_type": "insulation"},
            "template_type": "insulation",
            **observations,
            "warnings": [],
            "retrieval_summary": {},
        }
    )

    assert response.historical_material_usage == []
    assert response.historical_labor_performance == []
