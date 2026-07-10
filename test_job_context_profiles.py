from __future__ import annotations

import json

import pandas as pd

from jobscan.estimator.data_loader import normalize_estimator_data
from jobscan.estimator.job_context_profiles import build_job_context_digest, build_job_context_profiles
from jobscan.estimator.schemas import EstimatorData
from jobscan.estimator.template_examples import build_template_example_digest, build_template_examples


def profile_data() -> EstimatorData:
    return EstimatorData(
        jobs=pd.DataFrame(
            [
                {"job_id": "R1", "customer": "Acme Manufacturing", "job_name": "Metal Roof Restoration"},
                {"job_id": "I1", "customer": "Massey", "job_name": "Pole Barn Insulation"},
            ]
        ),
        template_rows=pd.DataFrame(
            [
                {
                    "job_id": "R1",
                    "template_type": "roofing",
                    "row_number": 26,
                    "template_bucket": "coating",
                    "line_item_kind": "material",
                    "resolved_item_name": "Gaco Silicone",
                    "area_sqft": 9600,
                    "estimated_units": 165.6,
                    "unit_price": 32.0,
                    "warranty_years": 15,
                    "substrate": "metal",
                },
                {
                    "job_id": "R1",
                    "template_type": "roofing",
                    "row_number": 39,
                    "template_bucket": "primer",
                    "line_item_kind": "material",
                    "resolved_item_name": "Gaco E-5320 Primer",
                    "area_sqft": 9600,
                    "estimated_units": 38.4,
                    "unit_price": 33.0,
                },
                {
                    "job_id": "R1",
                    "template_type": "roofing",
                    "row_number": 63,
                    "template_bucket": "fasteners",
                    "line_item_kind": "material",
                    "resolved_item_name": "Fasteners",
                    "area_sqft": 9600,
                },
                {
                    "job_id": "I1",
                    "template_type": "insulation",
                    "row_number": 19,
                    "template_bucket": "foam",
                    "line_item_kind": "material",
                    "resolved_item_name": "Gaco 0.5 lb.",
                    "area_sqft": 2226,
                    "thickness_inches": 3.5,
                    "estimated_yield": 2600,
                    "building_type": "pole barn",
                },
                {
                    "job_id": "I1",
                    "template_type": "insulation",
                    "row_number": 30,
                    "template_bucket": "thermal_barrier",
                    "line_item_kind": "material",
                    "resolved_item_name": "DC315",
                    "area_sqft": 2226,
                },
            ]
        ),
        historical_scope_texts=pd.DataFrame(
            [
                {
                    "job_id": "R1",
                    "file_name": "Acme Proposal.pdf",
                    "scope_text": "Industrial metal roof restoration with 15-year silicone coating, primer, rusted fasteners, and detail work.",
                },
                {
                    "job_id": "I1",
                    "file_name": "Massey Proposal.pdf",
                    "scope_text": "Pole barn insulation with spray foam on walls and ceiling.",
                },
            ]
        ),
    )


def test_job_context_profiles_classify_roofing_and_insulation_jobs() -> None:
    profiles = build_job_context_profiles(profile_data())

    by_job = {row["job_id"]: row for row in profiles.to_dict(orient="records")}
    roof = by_job["R1"]
    assert roof["template_type"] == "roofing"
    assert roof["project_class"] == "roof_restoration"
    assert roof["market_segment"] == "industrial"
    assert roof["substrate"] == "metal"
    assert roof["warranty_years"] == 15
    assert {"coating", "primer", "fasteners"}.issubset(set(roof["material_packages"]))
    assert "15-year silicone coating" in roof["scope_summary"]

    insulation = by_job["I1"]
    assert insulation["template_type"] == "insulation"
    assert insulation["project_class"] == "insulation_pole_barn"
    assert insulation["building_type"] == "pole_barn"
    assert {"foam", "thermal_barrier"}.issubset(set(insulation["material_packages"]))


def test_job_context_digest_returns_relevant_profiles_and_package_priors() -> None:
    data = normalize_estimator_data(profile_data())

    digest = build_job_context_digest(
        data,
        scope={
            "template_type": "roofing",
            "project_type": "roof restoration",
            "substrate": "metal",
            "coating_type": "silicone",
            "warranty_target_years": 15,
            "estimated_sqft": 9800,
            "raw_input_notes": "Industrial metal roof needs silicone restoration.",
        },
    )

    assert digest["matched_profiles"]
    assert digest["matched_profiles"][0]["job_id"] == "R1"
    assert "coating" in digest["matched_profiles"][0]["material_packages"]
    assert digest["aggregate_priors"]
    assert "primer" in digest["aggregate_priors"][0]["normally_included"]


def test_normalize_estimator_data_derives_job_context_profiles() -> None:
    data = normalize_estimator_data(profile_data())

    assert not data.job_context_profiles.empty
    assert {"R1", "I1"} == set(data.job_context_profiles["job_id"])
    assert not data.template_examples.empty


def test_template_examples_capture_worked_decisions_and_match_scope() -> None:
    data = normalize_estimator_data(profile_data())
    examples = build_template_examples(data)

    roof = examples[examples["job_id"] == "R1"].iloc[0].to_dict()
    assert roof["template_type"] == "roofing"
    assert "Gaco Silicone" in roof["decision_summary"]
    assert "Gaco E-5320 Primer" in roof["decisions_json"]
    answer_key = json.loads(roof["answer_key_json"])
    assert answer_key["schema_version"] == "reference_estimate_answer_key.v1"
    assert any(decision["decision_id"] == "roofing_coating_system_row_26" for decision in answer_key["decisions"])

    digest = build_template_example_digest(
        data,
        scope={
            "template_type": "roofing",
            "project_type": "metal roof restoration",
            "substrate": "metal",
            "coating_type": "silicone",
            "warranty_target_years": 15,
            "estimated_sqft": 9800,
            "raw_input_notes": "Industrial metal roof needs silicone coating and primer.",
        },
    )

    assert digest["matched_examples"]
    assert digest["matched_examples"][0]["job_id"] == "R1"
    assert any(decision.get("template_bucket") == "coating" for decision in digest["matched_examples"][0]["decisions"])
    reference_answer_key = digest["matched_examples"][0]["reference_answer_key"]
    assert reference_answer_key["schema_version"] == "reference_estimate_answer_key.v1"
    assert any(decision["decision_id"] == "roofing_primer_system_row_39" for decision in reference_answer_key["decisions"])
    assert reference_answer_key["decisions"][0]["evidence"]["source"] == "reference_estimate_answer_key"


def test_template_examples_group_history_by_workbook_not_broad_job_folder() -> None:
    data = EstimatorData(
        template_rows=pd.DataFrame(
            [
                {
                    "job_id": "R-FOLDER",
                    "document_id": "D-1",
                    "source_file": "Estimate Roofing - One.xlsx",
                    "template_type": "roofing",
                    "row_number": 26,
                    "template_bucket": "coating",
                    "line_item_kind": "material",
                    "resolved_item_name": "Gaco Silicone",
                    "area_sqft": 1000,
                    "estimated_units": 18,
                    "unit_price": 32,
                },
                {
                    "job_id": "R-FOLDER",
                    "document_id": "D-2",
                    "source_file": "Estimate Roofing - Two.xlsx",
                    "template_type": "roofing",
                    "row_number": 26,
                    "template_bucket": "coating",
                    "line_item_kind": "material",
                    "resolved_item_name": "Gaco Silicone",
                    "area_sqft": 2000,
                    "estimated_units": 36,
                    "unit_price": 32,
                },
            ]
        ),
        job_context_profiles=pd.DataFrame(
            [
                {
                    "job_id": "R-FOLDER",
                    "template_type": "roofing",
                    "project_class": "roof_restoration",
                    "substrate": "metal",
                    "material_system": "Gaco Silicone",
                    "area_sqft": 1500,
                }
            ]
        ),
    )

    examples = build_template_examples(data)

    assert len(examples) == 2
    assert set(examples["document_id"]) == {"D-1", "D-2"}
    assert all(json.loads(value)["summary"]["decision_count"] == 1 for value in examples["answer_key_json"])
