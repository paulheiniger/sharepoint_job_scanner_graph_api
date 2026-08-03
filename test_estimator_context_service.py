from __future__ import annotations

import json

import pandas as pd

from jobscan.estimator.context_service import (
    AGENT_CONTEXT_PUBLIC_MAX_BYTES,
    build_copilot_estimator_context,
)
from jobscan.estimator.schemas import EstimatorData


def test_build_copilot_estimator_context_reuses_real_context_assembly() -> None:
    result = build_copilot_estimator_context(
        scope={"template_type": "insulation"},
        raw_notes="30x40 metal building",
        data=EstimatorData(),
    )

    assert result["template_type"] == "insulation"
    assert result["decision_concepts"]
    assert result["calculation_requirements"]
    assert "workbook_row" not in result["decision_concepts"][0]
    assert result["matched_comparables"] == []


def test_build_copilot_estimator_context_cannot_call_openai(monkeypatch) -> None:
    monkeypatch.setattr(
        "jobscan.estimator.chat_assistant._call_openai_chat",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("context endpoint must not call OpenAI")
        ),
    )

    result = build_copilot_estimator_context(
        scope={"template_type": "roofing"},
        raw_notes="Existing roof context only.",
        data=EstimatorData(),
    )

    assert result["template_type"] == "roofing"


def test_context_returns_reviewable_roofing_purchase_guidance() -> None:
    result = build_copilot_estimator_context(
        scope={
            "template_type": "roofing",
            "estimated_sqft": 484,
            "area_scopes": [
                {
                    "scope_id": "repair",
                    "scope_role": "exclusive_area",
                    "area_sqft": 484,
                    "proposed_assembly": "1.5 inch coated foam roof",
                }
            ],
            "area_reconciliation": {"declared_total_area_sqft": 484},
        },
        raw_notes="Small coated foam roof repair.",
        data=EstimatorData(),
    )

    by_category = {
        row["category"]: row for row in result["purchasing_guidance"]
    }
    assert by_category["roofing_foam"]["recommended_purchase_quantity"] == 500
    assert by_category["coating"]["recommended_purchase_quantity"] == 500
    assert {
        row["category"] for row in result["labor_plan_guidance"]
    } == {"labor_prep", "labor_base", "labor_top_coat", "labor_cleanup"}
    assert all(
        row["calibration_status"] == "missing_calibration"
        for row in result["labor_plan_guidance"]
    )
    assert result["retrieval_summary"]["purchasing_guidance_count"] == 2


def test_build_copilot_estimator_context_is_bounded_and_model_neutral(monkeypatch) -> None:
    data = EstimatorData(
        jobs=pd.DataFrame(
            [
                {
                    "job_id": "JOB-1",
                    "folder_url": "https://example.invalid/jobs/JOB-1",
                    "folder_path": "Jobs/2024/JOB-1",
                }
            ]
        ),
        estimates=pd.DataFrame([{"estimate_id": "EST-1"}]),
        template_example_source_links=pd.DataFrame(
            [
                {
                    "example_id": "EXAMPLE-1",
                    "job_id": "JOB-1",
                    "document_id": "DOC-1",
                    "source_file": "Estimate JOB-1.xlsx",
                    "file_name": "Estimate JOB-1.xlsx",
                    "source_url": "https://example.invalid/estimates/JOB-1",
                    "folder_path": "Jobs/2024/JOB-1/Estimates",
                    "relative_path": "Estimates/Estimate JOB-1.xlsx",
                }
            ]
        ),
        pricing_catalog=pd.DataFrame([{"pricing_item_id": "PRICE-1"}]),
        product_catalog=pd.DataFrame([{"product_id": "PRODUCT-1"}]),
        estimator_memory=pd.DataFrame([{"memory_id": "MEMORY-1"}]),
    )
    captured: dict = {}

    def fake_summary(estimator_data, *, scope):
        captured["data"] = estimator_data
        captured["scope"] = scope
        return {
            "template_type": "insulation",
            "route_mileage": {"round_trip_miles": 84.0},
            "historical_evidence_packet": {
                "matched_comparables": [
                    {
                        "example_id": "EXAMPLE-1",
                        "job_id": "JOB-1",
                        "source_file": "Estimate JOB-1.xlsx",
                        "active_decision_keys": [
                            "foam@row_19",
                            "labor_spraying@row_40",
                        ],
                    }
                ],
                "decision_evidence": [
                    {
                        "section": "Materials",
                        "decision_id": "insulation_foam_row_19",
                        "template_bucket": "foam",
                        "workbook_row": "19",
                        "line_item": "Closed-cell foam",
                        "sample_inputs": {
                            "basis_sqft": 2200,
                            "thickness_inches": 2,
                        },
                        "examples": [{"duplicate": "semantic observation"}],
                    }
                ],
                "matched_scope_pattern": {"archetype_id": "metal-building"},
                "validated_relationships": [{"rule_id": "RULE-1"}],
            },
            "estimator_memory_guidance": [{"memory_id": "MEMORY-1"}],
            "pricing_candidates_by_bucket": [{"template_bucket": "foam"}],
            "product_guidance_digest": [{"product_id": "PRODUCT-1"}],
            "foam_yield_history_digest": [
                {
                    "foam_type": "closed_cell",
                    "examples": [{"duplicate": "source evidence"}],
                }
            ],
            "formula_requirements": [
                {"decision_id": "foam", "unavailable_value": float("nan")}
            ],
            "decision_menu": [
                {
                    "decision_id": "insulation_foam_row_19",
                    "template_bucket": "foam",
                    "workbook_row": "19",
                    "label": "Spray foam system",
                    "editable_fields": [
                        "include",
                        "basis_sqft",
                        "thickness_inches",
                        "yield_or_coverage",
                        "unit_price",
                    ],
                    "formula_requirements": [
                        "basis_sqft",
                        "thickness_inches",
                        "yield_or_coverage",
                        "unit_price",
                    ],
                }
            ],
            "_deterministic_latest_historical_unit_prices": [{"private": True}],
        }

    monkeypatch.setattr(
        "jobscan.estimator.context_service.estimator_context_summary",
        fake_summary,
    )

    result = build_copilot_estimator_context(
        scope={"building_type": "metal building"},
        raw_notes="30x40 building",
        template_type_hint="insulation",
        reference_job_ids=["JOB-1", "JOB-1"],
        exclude_job_ids=["TARGET-1", "TARGET-1"],
        exclude_source_files=["Completed Target.xlsx"],
        data=data,
        include_source_metadata=True,
    )

    assert captured["data"] is data
    assert captured["scope"]["template_type"] == "insulation"
    assert captured["scope"]["raw_input_notes"] == "30x40 building"
    assert captured["scope"]["reference_job_ids"] == ["JOB-1"]
    assert captured["scope"]["exclude_job_ids"] == ["TARGET-1"]
    assert captured["scope"]["exclude_source_files"] == [
        "Completed Target.xlsx"
    ]
    assert result["schema_version"] == "spraytec.copilot_estimator_context.v1"
    assert result["scope_integrity"]["status"] == "not_applicable"
    assert result["retrieval_exclusions"] == {
        "job_ids": ["TARGET-1"],
        "source_files": ["Completed Target.xlsx"],
    }
    assert result["retrieval_summary"] == {
        "matched_comparable_count": 1,
        "decision_evidence_count": 1,
        "historical_material_usage_count": 1,
        "historical_labor_performance_count": 0,
        "historical_assembly_count": 1,
        "matched_scope_pattern": True,
        "validated_relationship_count": 1,
        "approved_memory_count": 1,
        "pricing_bucket_count": 1,
        "product_guidance_count": 1,
        "decision_concept_count": 1,
        "calculation_requirement_count": 1,
        "purchasing_guidance_count": 0,
        "labor_plan_guidance_count": 0,
        "source_link_count": 1,
    }
    assert "_deterministic_latest_historical_unit_prices" not in result
    assert result["decision_concepts"][0]["concept_id"] == "insulation.foam"
    assert "workbook_row" not in result["decision_concepts"][0]
    assert "active_decision_keys" not in result["matched_comparables"][0]
    assert result["matched_comparables"][0]["historical_decision_categories"] == [
        "foam",
        "labor_spraying",
    ]
    assert result["decision_evidence"] == [
        {
            "line_item": "Closed-cell foam",
            "concept_id": "insulation.foam",
            "category": "foam",
        }
    ]
    assert result["historical_material_usage"][0]["concept_id"] == "insulation.foam"
    assert "examples" not in result["foam_yield_history"][0]
    assert result["historical_material_usage"][0]["basis_measurements"] == [
        {
            "name": "basis_sqft",
            "value": 2200.0,
            "unit": "sqft",
        }
    ]
    assert result["historical_assemblies"][0]["decision_categories"] == [
        "foam",
        "labor_spraying",
    ]
    assert result["source_links"] == [
        {
            "source_type": "historical_estimate",
            "example_id": "EXAMPLE-1",
            "job_id": "JOB-1",
            "customer": None,
            "job_name": None,
            "document_id": "DOC-1",
            "file_name": "Estimate JOB-1.xlsx",
            "file_web_url": "https://example.invalid/estimates/JOB-1",
            "job_folder_web_url": "https://example.invalid/jobs/JOB-1",
            "folder_path": "Jobs/2024/JOB-1/Estimates",
            "relative_path": "Estimates/Estimate JOB-1.xlsx",
        }
    ]
    assert result["source_metadata"]["row_counts"]["jobs"] == 1


def test_build_copilot_estimator_context_does_not_override_explicit_scope(monkeypatch) -> None:
    data = EstimatorData()

    def fake_summary(_data, *, scope):
        return {"template_type": scope["template_type"]}

    monkeypatch.setattr(
        "jobscan.estimator.context_service.estimator_context_summary",
        fake_summary,
    )

    result = build_copilot_estimator_context(
        scope={
            "template_type": "roofing",
            "raw_input_notes": "Structured note wins.",
        },
        raw_notes="Raw note",
        template_type_hint="insulation",
        site_address="Ignored address",
        data=data,
    )

    assert result["scope"]["template_type"] == "roofing"
    assert result["scope"]["raw_input_notes"] == "Structured note wins."


def test_grossman_roofing_context_stays_under_action_budget(monkeypatch) -> None:
    verbose_text = "Historically supported roofing evidence. " * 24
    sources = [
        {
            "job_id": f"ROOF-JOB-{index}",
            "example_id": f"ROOF-EXAMPLE-{index}",
            "document_id": f"DOC-{index}",
            "label": verbose_text,
            "file_name": f"Estimate Roofing {index}.xlsx",
            "file_web_url": f"https://example.invalid/roofing/{index}",
            "folder_path": f"Jobs/Roofing/{index}",
            "relative_path": f"Estimate Roofing {index}.xlsx",
            "similarity_score": 0.9 - (index * 0.05),
            "match_reasons": [verbose_text],
            "reference_area_sqft": 5_136,
        }
        for index in range(2)
    ]
    material_categories = [
        "board_stock",
        "foam",
        "coating",
        "primer",
        "caulk_detail",
        "edge_metal",
        "gutter",
        "downspouts",
        "dumpsters",
        "misc_materials",
    ]
    labor_categories = [
        "labor_tearoff",
        "labor_decking",
        "labor_board",
        "labor_foam",
        "labor_coating",
        "labor_details",
        "labor_loading",
        "labor_traveling",
        "labor_cleanup",
    ]

    def fake_summary(_data, *, scope):
        decision_evidence = []
        for index, category in enumerate(material_categories + labor_categories):
            decision_evidence.append(
                {
                    "decision_id": f"roofing_{category}_{index}",
                    "template_bucket": category,
                    "line_item": category.replace("_", " "),
                    "sample_inputs": {"notes": verbose_text},
                    "sample_outputs": {"notes": verbose_text},
                    "why_suggested": verbose_text,
                    "support_count": 2,
                    "confidence": 0.9,
                }
            )
        return {
            "template_type": "roofing",
            "route_mileage": {"round_trip_miles": 12.0},
            "historical_evidence_packet": {
                "matched_comparables": [
                    {
                        "example_id": f"ROOF-EXAMPLE-{index}",
                        "job_id": f"ROOF-JOB-{index}",
                        "source_file": f"Estimate Roofing {index}.xlsx",
                    }
                    for index in range(2)
                ],
                "decision_evidence": decision_evidence,
                "matched_scope_pattern": {
                    "archetype_id": "foam-roof-restoration",
                    "notes": verbose_text * 3,
                },
                "validated_relationships": [
                    {"rule_id": f"RULE-{index}", "explanation": verbose_text}
                    for index in range(8)
                ],
            },
            "estimator_memory_guidance": [
                {
                    "memory_id": f"MEMORY-{index}",
                    "template_bucket": (
                        "coating_restoration" if index % 2 else "detail_repairs"
                    ),
                    "guidance": verbose_text,
                }
                for index in range(12)
            ],
            "pricing_candidates_by_bucket": [
                {
                    "template_bucket": [
                        "board_stock",
                        "foam",
                        "coating",
                        "fasteners",
                        "fabric",
                    ][index % 5],
                    "candidate_name": f"Candidate {index}",
                    "source": verbose_text,
                }
                for index in range(40)
            ],
            "product_guidance_digest": [
                {
                    "product_id": f"PRODUCT-{index}",
                    "category": [
                        "roof_coating",
                        "primer",
                        "spray_foam",
                        "fabric",
                    ][index % 4],
                    "guidance": verbose_text,
                }
                for index in range(20)
            ],
            "foam_yield_history_digest": [
                {
                    "foam_type": "roofing",
                    "template_option": f"Yield {index}",
                    "notes": verbose_text,
                }
                for index in range(8)
            ],
            "decision_menu": [
                {
                    "template_bucket": f"decision_{index}",
                    "label": f"Decision {index}",
                    "editable_fields": ["include", "quantity", "unit_price"],
                    "formula_requirements": ["quantity", "unit_price"],
                }
                for index in range(32)
            ],
        }

    def fake_semantic_observations(**_kwargs):
        return {
            "historical_material_usage": [
                {
                    "observation_id": f"MATERIAL-{index}",
                    "concept_id": f"roofing.{category}",
                    "category": category,
                    "material_name": category.replace("_", " "),
                    "support_count": 2,
                    "sources": sources,
                }
                for index, category in enumerate(material_categories)
            ],
            "historical_labor_performance": [
                {
                    "observation_id": f"LABOR-{index}",
                    "concept_id": f"roofing.{category}",
                    "category": category,
                    "activity": category.replace("_", " "),
                    "productivity": {},
                    "support_count": 2,
                    "sources": sources,
                }
                for index, category in enumerate(labor_categories)
            ],
            "historical_assemblies": [
                {
                    "observation_id": f"ASSEMBLY-{index}",
                    "template_type": "roofing",
                    "label": f"Roof assembly {index}",
                    "scope_summary": verbose_text,
                    "sources": sources,
                }
                for index in range(2)
            ],
        }

    monkeypatch.setattr(
        "jobscan.estimator.context_service.estimator_context_summary",
        fake_summary,
    )
    monkeypatch.setattr(
        "jobscan.estimator.context_service.build_semantic_observations",
        fake_semantic_observations,
    )
    monkeypatch.setattr(
        "jobscan.estimator.context_service._source_links",
        lambda *_args, **_kwargs: [
            {
                "source_type": "historical_estimate",
                "job_id": f"ROOF-JOB-{index}",
                "file_name": f"Estimate Roofing {index}.xlsx",
                "file_web_url": f"https://example.invalid/roofing/{index}",
            }
            for index in range(2)
        ],
    )

    result = build_copilot_estimator_context(
        scope={
            "project_name": "Grossman Tuning",
            "estimated_sqft": 5_136,
            "full_removal_sqft": 3_120,
            "foam_over_existing_sqft": 2_016,
        },
        raw_notes="Grossman roofing scope. " * 3_000,
        template_type_hint="roofing",
        data=EstimatorData(),
    )
    serialized_bytes = len(
        json.dumps(result, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )

    assert serialized_bytes <= AGENT_CONTEXT_PUBLIC_MAX_BYTES
    assert result["historical_material_usage"]
    assert result["historical_labor_performance"]
    assert len(result["source_links"]) == 2
    assert result["calculation_requirements"]
    assert result["scope"]["raw_input_notes_truncated"] is True
    assert result["response_budget"]["truncated"] is True
    assert result["response_budget"]["within_public_limit"] is True
    assert "pricing_candidates" in result["response_budget"]["truncated_fields"]
