from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd

from jobscan.estimator.context_service import (
    AGENT_CONTEXT_PUBLIC_MAX_BYTES,
    build_copilot_estimator_context,
)
from jobscan.estimator import context_service
from jobscan.estimator import chat_assistant
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


def test_context_reuses_read_only_estimator_data_within_ttl(monkeypatch) -> None:
    calls = []
    context_service._ESTIMATOR_DATA_CACHE.clear()
    monkeypatch.setenv("ESTIMATOR_CONTEXT_DATA_CACHE_TTL_SECONDS", "900")
    monkeypatch.setattr(
        context_service,
        "load_estimator_data",
        lambda **kwargs: calls.append(kwargs) or EstimatorData(),
    )

    for notes in ("First request", "Second request"):
        build_copilot_estimator_context(
            scope={"template_type": "roofing"},
            raw_notes=notes,
            database_url="postgresql://example.invalid/test",
        )

    assert len(calls) == 1
    assert calls[0]["load_profile"] == "chat"
    context_service._ESTIMATOR_DATA_CACHE.clear()


def test_route_context_preserves_mapbox_drive_time(monkeypatch) -> None:
    monkeypatch.setattr(
        chat_assistant,
        "mapbox_route_distance",
        lambda origin, destination: SimpleNamespace(
            one_way_miles=31.0,
            round_trip_miles=62.0,
            duration_minutes_one_way=38.0,
            source="mapbox_directions",
        ),
    )

    route = chat_assistant._route_mileage_context(
        {"site_address": "830 South 1st Street, Louisville, KY 40203"}
    )

    assert route["estimated_round_trip_miles"] == 62.0
    assert route["duration_minutes_one_way"] == 38.0
    assert route["source"] == "mapbox_directions"


def test_scope_pricing_prioritizes_required_grossman_categories_and_excludes_target_copy() -> None:
    scope = {
        "template_type": "roofing",
        "raw_input_notes": (
            "2 inch ISO board, replace deteriorated wood decking, coated foam, "
            "edge metal, gutter, caulk"
        ),
        "linear_scopes": [
            {"item": "Edge Metal", "linear_ft": 52},
            {"item": "Gutter & Downspouts", "linear_ft": 52},
        ],
        "exclude_source_files": ["Estimate Grossman (1).xlsx"],
    }
    planning = {
        "purchasing_guidance": [
            {"category": "roofing_foam"},
            {"category": "coating"},
            {"category": "board_stock"},
        ],
        "logistics_guidance": [
            {"category": "dumpster", "include": True},
            {"category": "generator", "include": True},
        ],
    }
    full_context = {
        "pricing_candidates_by_bucket": [
            {
                "decision_bucket": "board_stock",
                "candidate_name": 'ISO board 2"',
                "thickness_inches": 2,
                "unit_price": 77.47,
                "source": "template_lookup_materials",
            },
            {
                "decision_bucket": "foam",
                "candidate_name": "Accufoam Closed Cell Insulation",
                "unit_price": 2.42,
                "source": "pricing_catalog",
            },
        ],
        "_deterministic_latest_historical_unit_prices": [
            {
                "template_bucket": "foam",
                "item_name": "Gaco Roof 2.7",
                "unit_price": 1.99,
                "source_file": "Estimate Roof Reference.xlsx",
                "source_effective_at": "2026-07-31T00:00:00Z",
            },
            {
                "template_bucket": "dumpsters",
                "item_name": "20 Yard",
                "unit_price": 600,
                "source_file": "Estimate Grossman.xlsx",
                "source_effective_at": "2026-08-01T00:00:00Z",
            },
            {
                "template_bucket": "dumpsters",
                "item_name": "30 Yard",
                "unit_price": 650,
                "source_file": "Estimate Other Roof.xlsx",
                "source_effective_at": "2026-07-30T00:00:00Z",
            },
            {
                "template_bucket": "generator",
                "item_name": "Generator",
                "unit_price": 50,
                "source_file": "Estimate Grossman.xlsx",
                "source_effective_at": "2026-08-01T00:00:00Z",
                "historical_observation_count": 947,
                "fallback_unit_price": 50,
                "fallback_source_document_id": "DOC-GENERATOR-2",
                "fallback_source_job_id": "JOB-GENERATOR-2",
                "fallback_source_file": "Estimate Other Generator Roof.xlsx",
                "fallback_source_effective_at": "2026-07-31T00:00:00Z",
            },
        ],
    }

    rows = context_service._public_pricing_candidates(
        full_context,
        scope=scope,
        planning_guidance=planning,
    )
    by_bucket = {}
    for row in rows:
        by_bucket.setdefault(row["decision_bucket"], []).append(row)

    assert by_bucket["board_stock"][0]["candidate_name"] == 'ISO board 2"'
    assert by_bucket["foam"][0]["candidate_name"] == "Gaco Roof 2.7"
    assert by_bucket["dumpster"][0]["candidate_name"] == "30 Yard"
    assert by_bucket["generator"][0]["unit_price"] == 50
    assert by_bucket["generator"][0]["source_file"] == "Estimate Other Generator Roof.xlsx"
    assert by_bucket["generator"][0]["fallback_from_excluded_latest"] is True
    assert all(row.get("source_file") != "Estimate Grossman.xlsx" for row in rows)


def test_labor_cost_summary_makes_current_people_rates_explicit() -> None:
    summary = context_service._labor_cost_summary(
        [
            {
                "category": "labor_tearoff",
                "current_people_daily_rate": 1937.25,
                "estimated_labor_cost_candidate": 2324.70,
            },
            {
                "category": "labor_top_coat",
                "current_people_daily_rate": 1667.25,
                "estimated_labor_cost_candidate": 1017.02,
            },
        ]
    )

    assert summary["current_people_rates_available"] is True
    assert summary["costed_activity_count"] == 2
    assert summary["estimated_production_labor_cost_candidate"] == 3341.72
    assert summary["uncosted_categories"] == []


def test_commercial_guidance_uses_common_template_percentages() -> None:
    data = EstimatorData(
        commercial_markup_history=pd.DataFrame(
            [
                {"template_type": "roofing", "category": "overhead", "percentage": 35, "document_count": 850},
                {"template_type": "roofing", "category": "overhead", "percentage": 30, "document_count": 69},
                {"template_type": "roofing", "category": "profit", "percentage": 15, "document_count": 547},
                {"template_type": "roofing", "category": "profit", "percentage": 10, "document_count": 151},
            ]
        )
    )

    guidance = context_service._commercial_guidance(
        data,
        template_type="roofing",
    )

    assert guidance["overhead_pct"] == 35
    assert guidance["profit_pct"] == 15
    assert guidance["blocks_workbook_generation"] is False
    assert guidance["evidence"]["overhead"]["supporting_document_count"] == 850


def test_labor_focus_returns_labor_without_unrelated_context(monkeypatch) -> None:
    monkeypatch.setattr(
        context_service,
        "build_estimator_planning_guidance",
        lambda **_kwargs: {
            "purchasing_guidance": [{"category": "roofing_foam"}],
            "labor_plan_guidance": [
                {
                    "category": "labor_prep",
                    "current_people_daily_rate": 1900,
                    "estimated_labor_cost_candidate": 1900,
                }
            ],
            "logistics_guidance": [],
        },
    )

    result = build_copilot_estimator_context(
        scope={"template_type": "roofing", "estimated_sqft": 1000},
        data=EstimatorData(),
        focus="labor",
    )

    assert result["focus"] == "labor"
    assert result["labor_plan_guidance"]
    assert result["labor_cost_summary"]["current_people_rates_available"] is True
    assert result["purchasing_guidance"] == []
    assert result["pricing_candidates"] == []
    assert result["response_budget"]["focus"] == "labor"


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
            "_deterministic_latest_historical_unit_prices": [
                {
                    "template_bucket": "foam",
                    "item_name": "Historical closed-cell foam",
                    "unit": "set",
                    "unit_price": 2450,
                    "source_job_id": "PRICE-JOB-1",
                    "source_file": "Estimate PRICE-JOB-1.xlsx",
                    "source_effective_at": "2026-07-01T00:00:00Z",
                    "historical_observation_count": 3,
                }
            ],
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
        "pricing_bucket_count": 2,
        "product_guidance_count": 1,
        "decision_concept_count": 1,
        "calculation_requirement_count": 1,
        "purchasing_guidance_count": 0,
        "labor_plan_guidance_count": 0,
        "logistics_guidance_count": 0,
        "source_link_count": 1,
    }
    assert "_deterministic_latest_historical_unit_prices" not in result
    historical_price = next(
        row
        for row in result["pricing_candidates"]
        if row.get("source") == "latest_historical_estimate"
    )
    assert historical_price["unit_price"] == 2450
    assert historical_price["price_authority"] == "fallback_if_current_unavailable"
    assert historical_price["source_file"] == "Estimate PRICE-JOB-1.xlsx"
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
    assert len(result["pricing_candidates"]) <= 12
