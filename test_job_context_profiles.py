from __future__ import annotations

import pandas as pd

from jobscan.estimator.data_loader import normalize_estimator_data
from jobscan.estimator.job_context_profiles import build_job_context_digest, build_job_context_profiles
from jobscan.estimator.schemas import EstimatorData


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
                    "template_bucket": "coating",
                    "line_item_kind": "material",
                    "resolved_item_name": "Gaco Silicone",
                    "area_sqft": 9600,
                    "warranty_years": 15,
                    "substrate": "metal",
                },
                {
                    "job_id": "R1",
                    "template_type": "roofing",
                    "template_bucket": "primer",
                    "line_item_kind": "material",
                    "resolved_item_name": "Gaco E-5320 Primer",
                    "area_sqft": 9600,
                },
                {
                    "job_id": "R1",
                    "template_type": "roofing",
                    "template_bucket": "fasteners",
                    "line_item_kind": "material",
                    "resolved_item_name": "Fasteners",
                    "area_sqft": 9600,
                },
                {
                    "job_id": "I1",
                    "template_type": "insulation",
                    "template_bucket": "foam",
                    "line_item_kind": "material",
                    "resolved_item_name": "Gaco 0.5 lb.",
                    "area_sqft": 2226,
                    "building_type": "pole barn",
                },
                {
                    "job_id": "I1",
                    "template_type": "insulation",
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
