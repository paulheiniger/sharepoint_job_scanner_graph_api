from __future__ import annotations

import importlib
import inspect
from types import SimpleNamespace

import pandas as pd
import pytest

from jobscan.products.product_catalog import ProductKnowledge
from jobscan.repair_estimator.vsimple_loader import RepairTables


def sample_repair_tables() -> RepairTables:
    return RepairTables(
        repair_jobs=pd.DataFrame(
            [
                {
                    "repair_id": "R1",
                    "customer": "Acme",
                    "job_name": "Pipe boot leak repair",
                    "status": "Invoiced",
                    "type_of_repair": "Billable Repair",
                    "roof_type": "TPO",
                    "url": "https://example.test/R1",
                }
            ]
        ),
        repair_material_usage=pd.DataFrame(
            [
                {
                    "repair_material_usage_id": "M1",
                    "repair_id": "R1",
                    "material_package": "caulk_sealant",
                    "material_name": "NP1",
                    "quantity": 2,
                    "unit": "tube",
                    "unit_cost": 9,
                    "total_cost": 18,
                }
            ]
        ),
        repair_labor_usage=pd.DataFrame(
            [
                {
                    "repair_labor_usage_id": "L1",
                    "repair_id": "R1",
                    "labor_role": "aggregate",
                    "labor_hours": 4,
                    "labor_cost": 320,
                    "total_labor_hours": 4,
                }
            ]
        ),
        repair_scope_text=pd.DataFrame(
            [
                {
                    "repair_id": "R1",
                    "scope_of_work": "Pipe boot leak on TPO roof",
                    "work_performed_long_text": "Sealed one pipe boot with NP1 and fabric.",
                    "special_notes": "",
                    "materials_used": "2 tubes NP1",
                    "combined_scope_text": "pipe boot leak tpo roof sealed fabric np1",
                    "work_phrase_patterns": '["leak", "caulk"]',
                }
            ]
        ),
        repair_outcomes=pd.DataFrame(
            [
                {
                    "repair_id": "R1",
                    "status": "Invoiced",
                    "invoice_amount": 1200,
                    "total_bill_amount": 1200,
                    "gross_profit": 450,
                }
            ]
        ),
    )


def test_dashboard_imports_safely() -> None:
    app = importlib.import_module("dashboard.app")

    assert hasattr(app, "estimator_prototype_page")
    assert hasattr(app, "classify_estimate_type_from_notes")
    assert hasattr(app, "route_estimator_request")
    assert hasattr(app, "sales_dashboard_page")
    assert hasattr(app, "operations_dashboard_page")


def test_sales_dashboard_rollups_classify_pipeline_and_gaps() -> None:
    app = importlib.import_module("dashboard.app")
    jobs = pd.DataFrame(
        [
            {
                "job_id": "J1",
                "customer": "ABC Church",
                "job_name": "Roof coating",
                "division": "Roofing",
                "pipeline_status": "Proposal Submitted",
                "estimated_value": 125000,
                "estimator": "Haley",
                "lead_source": "Referral",
            },
            {
                "job_id": "J2",
                "customer": "XYZ Manufacturing",
                "job_name": "Metal restoration",
                "division": "Roofing",
                "pipeline_status": "Closed Won",
                "final_price": 380000,
                "deal_owner": "Paul",
                "lead_source": "Existing Customers",
            },
            {
                "job_id": "J3",
                "customer": "School System",
                "job_name": "Repair",
                "division": "Repairs",
                "pipeline_status": "Closed Lost",
                "estimated_value": 18000,
            },
        ]
    )

    normalized = app.normalize_sales_jobs(jobs)
    pipeline = app.sales_pipeline_rollup(normalized)
    performance = app.sales_performance_rollup(normalized, "project_category")
    kpis = app.estimator_kpi_rollup(normalized)

    assert pipeline.loc[pipeline["stage"] == "Proposal Submitted", "value"].iloc[0] == 125000
    assert pipeline.loc[pipeline["stage"] == "Closed Won", "value"].iloc[0] == 380000
    assert set(normalized["project_category"]) >= {"Roofing Restoration", "Metal Restoration", "Repairs"}
    assert performance.loc[performance["category"] == "Metal Restoration", "win_rate"].iloc[0] == 1
    assert "Not Captured" in set(normalized["lead_source_display"])
    assert kpis.loc[kpis["estimator"] == "Haley", "proposals_sent"].iloc[0] == 1


def test_operations_dashboard_rollups_classify_readiness_and_schedule_health() -> None:
    app = importlib.import_module("dashboard.app")
    jobs = pd.DataFrame(
        [
            {
                "job_id": "J1",
                "customer": "ABC Church",
                "job_name": "Roof coating",
                "division": "Roofing",
                "pipeline_status": "Contracted",
                "estimated_value": 125000,
                "estimate_date": "2026-06-10",
                "schedule_notes": "Ready to schedule",
            },
            {
                "job_id": "J2",
                "customer": "XYZ Manufacturing",
                "job_name": "SPF Roof",
                "division": "Roofing",
                "pipeline_status": "Contracted",
                "estimated_value": 380000,
                "estimate_date": "2026-06-12",
                "blocking_issue": "Waiting on materials",
            },
            {
                "job_id": "J3",
                "customer": "School System",
                "job_name": "Repairs",
                "division": "Repairs",
                "pipeline_status": "Contracted",
                "estimated_value": 18000,
                "estimate_date": "2026-06-14",
                "estimated_start_date": "2026-07-09",
                "estimated_end_date": "2026-07-10",
                "assigned_crew_leader": "Crew A",
            },
        ]
    )

    ops = app.normalize_operations_jobs(jobs)
    summary = app.readiness_summary(ops)

    assert ops.loc[ops["job_id"] == "J1", "readiness_status"].iloc[0] == "Ready To Schedule"
    assert ops.loc[ops["job_id"] == "J2", "readiness_status"].iloc[0] == "Material Hold"
    assert ops.loc[ops["job_id"] == "J3", "schedule_health"].iloc[0] in {"Starting Soon", "On Track"}
    assert summary.loc[summary["status"] == "Ready To Schedule", "revenue"].iloc[0] == 125000
    assert summary.loc[summary["status"] == "Material Hold", "jobs"].iloc[0] == 1


def test_job_board_dashboard_rows_project_business_fields() -> None:
    app = importlib.import_module("dashboard.app")
    jobs = pd.DataFrame(
        [
            {
                "job_id": "J1",
                "customer": "ABC Church",
                "job_name": "Sanctuary roof restoration",
                "division": "Roofing",
                "pipeline_status": "Closed Won",
                "estimated_value": 125000,
                "estimator": "Haley",
                "lead_source": "Referral",
                "substrate": "Metal",
                "coating_type": "Silicone",
                "warranty_years": "15",
                "warranty_type": "Gaco",
                "estimated_duration_days": 5,
                "estimated_crew_size": 4,
                "estimated_labor_hours": 160,
            }
        ]
    )

    rows = app.prepare_job_board_dashboard_rows(jobs)
    row = rows.iloc[0]

    assert row["customer_display"] == "ABC Church"
    assert row["project"] == "Sanctuary roof restoration"
    assert row["sales_stage"] == "Closed Won"
    assert row["win_loss_status"] == "Won"
    assert row["substrate_display"] == "Metal"
    assert row["material_system_display"] == "Silicone"
    assert row["warranty_display"] == "15 Gaco"
    assert row["labor_plan"] == "5 days / 4 crew / 160 hrs"


def test_job_board_enrichment_fills_business_fields_from_vsimple_and_documents() -> None:
    app = importlib.import_module("dashboard.app")
    jobs = pd.DataFrame(
        [
            {
                "job_id": "J1",
                "customer": "ABC Church",
                "job_name": "Sanctuary roof restoration",
                "division": "Roofing",
                "pipeline_status": "Proposed",
                "estimated_value": None,
                "substrate": "",
                "material_system": "",
                "warranty_years": None,
                "lead_source": "",
            }
        ]
    )
    vsimple = pd.DataFrame(
        [
            {
                "job_id": "J1",
                "vsimple_deal_type": "Coating System over Existing Roof",
                "vsimple_lead_source": "Referral",
                "vsimple_bid_amount": 125000,
                "vsimple_spray_tec_system": "Gaco Silicone",
            }
        ]
    )
    docs = pd.DataFrame(
        [
            {
                "job_id": "J1",
                "document_substrate": "Metal",
                "document_material_system": "Silicone",
                "document_warranty_type": "Gaco",
                "document_warranty_years": 15,
            }
        ]
    )

    enriched = app.merge_job_board_enrichments(jobs, vsimple, docs)
    rows = app.prepare_job_board_dashboard_rows(enriched)
    row = rows.iloc[0]

    assert row["estimated_value"] == 125000
    assert row["lead_source"] == "Referral"
    assert row["substrate_display"] == "Metal"
    assert row["material_system_display"] == "Gaco Silicone"
    assert row["warranty_display"] == "15 Gaco"


def test_pricing_catalog_and_vsimple_tables_are_visible_in_dashboard_views() -> None:
    app = importlib.import_module("dashboard.app")

    assert "pricing_catalog" in app.VIEWS
    assert "vsimple_projects" in app.VIEWS
    assert "vsimple_sharepoint_job_matches_accepted" in app.VIEWS


def test_pricing_catalog_normalizes_product_name_for_edits() -> None:
    app = importlib.import_module("dashboard.app")

    assert app.pricing_product_name_normalized("  GacoFlex S20 - 55 Gal. ") == "gacoflex s20 55 gal"


def test_create_pricing_catalog_row_uses_manual_source_and_stable_id(monkeypatch) -> None:
    app = importlib.import_module("dashboard.app")
    captured = {}

    class FakeConnection:
        def execute(self, statement, params=None):
            captured["sql"] = str(statement)
            captured["params"] = params

    class FakeBegin:
        def __enter__(self):
            return FakeConnection()

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeEngine:
        def begin(self):
            return FakeBegin()

    monkeypatch.setattr(app, "get_engine", lambda: FakeEngine())

    pricing_item_id = app.create_pricing_catalog_row(
        {
            "product_name": "GacoFlex S20",
            "vendor": "Gaco",
            "category": "Roof Coating",
            "unit_price": 42.5,
            "unit_of_measure": "gal",
            "package_size": "55 gal",
            "effective_date": "2026-07-08",
            "is_current": True,
            "needs_review": False,
        }
    )

    assert pricing_item_id.startswith("price-")
    assert "INSERT INTO pricing_catalog" in captured["sql"]
    assert captured["params"]["source_type"] == "manual"
    assert captured["params"]["source_file"] == "dashboard_manual_entry"
    assert captured["params"]["product_name_normalized"] == "gacoflex s20"
    assert captured["params"]["unit_price"] == 42.5
    assert captured["params"]["price_per_unit"] == 42.5


def test_product_knowledge_upload_can_be_retargeted_to_catalog_product() -> None:
    app = importlib.import_module("dashboard.app")
    knowledge = ProductKnowledge(
        product_catalog=[
            {
                "product_id": "parsed_product",
                "manufacturer": "Parsed",
                "product_name": "Parsed PDS Name",
                "aliases": ["Parsed Alias"],
            }
        ],
        product_aliases=[
            {
                "alias_id": "parsed_alias",
                "product_id": "parsed_product",
                "alias": "Parsed Alias",
                "alias_type": "parsed",
                "confidence": 0.8,
            }
        ],
        product_documents=[
            {
                "document_id": "doc1",
                "product_id": "parsed_product",
                "document_type": "PDS",
            }
        ],
        product_properties=[
            {
                "property_id": "prop1",
                "product_id": "parsed_product",
                "property_name": "coverage",
            }
        ],
        product_rules=[
            {
                "rule_id": "rule1",
                "product_id": "parsed_product",
                "rule_type": "limitation",
            }
        ],
        product_decision_links=[
            {
                "link_id": "link1",
                "product_id": "parsed_product",
                "decision_id": "roofing_coating",
            }
        ],
    )

    retargeted = app.retarget_product_knowledge_to_catalog_product(
        knowledge,
        {
            "product_id": "gaco_s20",
            "manufacturer": "Gaco",
            "product_name": "GacoFlex S20",
            "product_family": "GacoFlex Silicone",
            "category": "roof_coating",
            "active": True,
        },
    )

    assert retargeted.product_catalog[0]["product_id"] == "gaco_s20"
    assert retargeted.product_catalog[0]["product_name"] == "GacoFlex S20"
    assert "Parsed Alias" in retargeted.product_catalog[0]["aliases"]
    assert retargeted.product_documents[0]["product_id"] == "gaco_s20"
    assert retargeted.product_properties[0]["product_id"] == "gaco_s20"
    assert retargeted.product_rules[0]["product_id"] == "gaco_s20"
    assert retargeted.product_decision_links[0]["product_id"] == "gaco_s20"
    assert retargeted.product_aliases[0]["product_id"] == "gaco_s20"


def test_ask_spraytec_formats_indexed_document_matches_directly() -> None:
    app = importlib.import_module("dashboard.app")
    interpreted = {"document_type": "all", "search_text": "canadian solar"}

    response = app.indexed_documents_response(
        [
            {
                "document_id": "D1",
                "job_id": "CANADIAN-SOLAR",
                "document_type": "estimate",
                "file_name": "Canadian Solar Estimate.xlsx",
                "sharepoint_url": "https://sharepoint.example/estimate.xlsx",
                "folder_path": "Jobs/Canadian Solar",
                "classification_reason": "Excel estimate file",
            }
        ],
        interpreted=interpreted,
        query="all documents on Canadian Solar",
    )

    assert "I found 1 indexed documents match" in response
    assert "[Canadian Solar Estimate.xlsx](https://sharepoint.example/estimate.xlsx)" in response
    assert "CANADIAN-SOLAR" in response


def test_ask_spraytec_caps_weak_job_candidates() -> None:
    app = importlib.import_module("dashboard.app")
    interpreted = {"document_type": "all", "search_text": "canadian solar"}
    results = [
        {"job_id": f"J{i}", "customer": f"Customer {i}", "job_name": f"Weak Job {i}", "match_score": 20, "match_reason": "Weak similarity"}
        for i in range(6)
    ]

    response = app.concise_job_candidates_response(results, interpreted)

    assert response.count("Weak Job") == 3
    assert "not showing broader weak matches" in response
    assert "Weak Job 5" not in response


def test_ask_spraytec_ranks_document_chunks_and_preserves_source_labels() -> None:
    app = importlib.import_module("dashboard.app")
    chunks = app.rank_document_content_chunks(
        [
            {
                "document_id": "D1",
                "file_name": "General Notes.pdf",
                "page_number": 1,
                "text_content": "Generic project notes.",
            },
            {
                "document_id": "D2",
                "file_name": "Canadian Solar Warranty.pdf",
                "page_number": 3,
                "text_content": "Canadian Solar warranty coating terms and roof restoration scope.",
            },
        ],
        "Canadian Solar warranty",
        limit=1,
    )

    assert len(chunks) == 1
    assert chunks[0]["document_id"] == "D2"
    assert app.source_label_for_chunk(chunks[0], 1) == "S1: Canadian Solar Warranty.pdf, page 3"


def test_ask_spraytec_document_answer_falls_back_without_openai(monkeypatch) -> None:
    app = importlib.import_module("dashboard.app")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    answer = app.llm_grounded_document_answer(
        "Summarize Canadian Solar warranty.",
        [
            {
                "document_id": "D1",
                "file_name": "Canadian Solar Warranty.pdf",
                "page_number": 2,
                "sharepoint_url": "https://sharepoint.example/warranty.pdf",
                "text_content": "Warranty term is referenced but signed warranty document is missing.",
            }
        ],
    )

    assert "AI summarization is not available" in answer
    assert "[S1]" in answer
    assert "Canadian Solar Warranty.pdf" in answer


def test_ask_spraytec_fallback_includes_structured_evidence(monkeypatch) -> None:
    app = importlib.import_module("dashboard.app")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    answer = app.llm_grounded_document_answer(
        "What is the estimate value?",
        [],
        {
            "facts": {
                "jobs": [
                    {
                        "job_id": "J1",
                        "customer": "Canadian Solar",
                        "final_price": 125000,
                    }
                ],
                "estimates": [
                    {
                        "estimate_file": "Estimate.xlsx",
                        "total_job_cost": 90000,
                    }
                ],
            }
        },
    )

    assert "Structured evidence" in answer
    assert "**jobs**" in answer
    assert "Canadian Solar" in answer
    assert "final_price" in answer


def test_ask_spraytec_structured_evidence_lines_are_compact() -> None:
    app = importlib.import_module("dashboard.app")

    lines = app.structured_evidence_lines(
        {
            "facts": {
                "pricing_catalog": [
                    {
                        "product_name": "GacoFlex S20",
                        "unit_price": 42.5,
                        "empty": "",
                    }
                ]
            }
        }
    )

    assert lines[0] == "**pricing_catalog**"
    assert "GacoFlex S20" in lines[1]
    assert "empty" not in lines[1]


def test_ask_spraytec_detects_structured_data_answer_prompts() -> None:
    app = importlib.import_module("dashboard.app")

    assert app.is_data_answer_request("what was the final price for Canadian Solar?")
    assert app.is_data_answer_request("Canadian Solar warranty")
    assert not app.is_data_answer_request("Canadian Solar")


def test_ask_spraytec_query_planner_routes_document_lookup() -> None:
    app = importlib.import_module("dashboard.app")

    interpreted = app.interpret_search_request("all documents on Canadian Solar")
    plan = app.plan_ask_spraytec_query("all documents on Canadian Solar", interpreted)

    assert plan["mode"] == "document_lookup"
    assert "documents" in plan["targets"]
    assert "document_content" in plan["targets"]
    assert "jobs" in plan["targets"]
    assert "pricing_catalog" not in plan["targets"]


def test_ask_spraytec_query_planner_routes_product_pricing_without_job_search() -> None:
    app = importlib.import_module("dashboard.app")

    interpreted = app.interpret_search_request("what is the current unit price and PDS for Gaco S20?")
    plan = app.plan_ask_spraytec_query("what is the current unit price and PDS for Gaco S20?", interpreted)

    assert plan["mode"] == "structured_answer"
    assert "pricing_catalog" in plan["targets"]
    assert "product_catalog" in plan["targets"]
    assert "jobs" not in plan["targets"]
    assert plan["use_llm_answer"] is True


def test_ask_spraytec_query_planner_routes_schedule_questions() -> None:
    app = importlib.import_module("dashboard.app")

    interpreted = app.interpret_search_request("when is Canadian Solar scheduled to start?")
    plan = app.plan_ask_spraytec_query("when is Canadian Solar scheduled to start?", interpreted)

    assert "crew_schedule" in plan["targets"]
    assert "jobs" in plan["targets"]
    assert plan["requires_job_context"] is True


def test_ask_spraytec_query_planner_routes_attribute_job_search() -> None:
    app = importlib.import_module("dashboard.app")

    prompt = "can you find me roofing jobs that required coating and foam?"
    interpreted = app.interpret_search_request(prompt)
    plan = app.plan_ask_spraytec_query(prompt, interpreted)

    assert plan["mode"] == "attribute_job_search"
    assert plan["attribute_query"]["concepts"] == ["coating", "foam"]
    assert plan["attribute_query"]["division"] == "Roofing"
    assert "estimate_template_rows" in plan["targets"]
    assert "estimate_line_items" in plan["targets"]


def test_ask_spraytec_query_planner_extracts_rich_attribute_filters() -> None:
    app = importlib.import_module("dashboard.app")

    prompt = "find roofing jobs with foam and silicone 15-year warranty on metal roofs over 20k sq ft"
    interpreted = app.interpret_search_request(prompt)
    plan = app.plan_ask_spraytec_query(prompt, interpreted)
    attr = plan["attribute_query"]

    assert plan["mode"] == "attribute_job_search"
    assert attr["concepts"] == ["coating", "foam"]
    assert attr["division"] == "Roofing"
    assert attr["warranty_years"] == 15
    assert attr["substrates"] == ["metal"]
    assert attr["systems"] == ["silicone"]
    assert attr["sqft_filter"] == {"operator": ">=", "value": 20000.0}


def test_ask_spraytec_attribute_matches_require_all_concepts() -> None:
    app = importlib.import_module("dashboard.app")

    matches = app.assemble_attribute_job_matches(
        [
            {
                "job_id": "J1",
                "matched_concept": "coating",
                "source_table": "estimate_template_rows",
                "row_label": "Gaco Silicone",
                "selected_item_name": "Gaco S20",
                "template_type": "Roofing",
            },
            {
                "job_id": "J1",
                "matched_concept": "foam",
                "source_table": "estimate_template_rows",
                "row_label": "Gaco Roof Foam",
                "selected_item_name": "Gaco Roof 2.7",
                "template_type": "Roofing",
            },
            {
                "job_id": "J2",
                "matched_concept": "coating",
                "source_table": "estimate_template_rows",
                "row_label": "Gaco Silicone",
                "selected_item_name": "Gaco S20",
                "template_type": "Roofing",
            },
        ],
        required_concepts=["coating", "foam"],
        job_rows={
            "J1": {"job_id": "J1", "customer": "Acme", "job_name": "Acme Roof", "division": "Roofing"},
            "J2": {"job_id": "J2", "customer": "Beta", "job_name": "Beta Roof", "division": "Roofing"},
        },
        interpreted={"division": "Roofing"},
        attribute_query={"division": "Roofing"},
    )

    assert [match["job_id"] for match in matches] == ["J1"]
    assert matches[0]["match_evidence_count"] == 2


def test_ask_spraytec_attribute_matches_apply_rich_filters() -> None:
    app = importlib.import_module("dashboard.app")

    evidence = [
        {
            "job_id": "J1",
            "matched_concept": "coating",
            "source_table": "estimate_template_rows",
            "row_label": "Gaco Silicone",
            "selected_item_name": "Gaco S20",
            "area_sqft": 25000,
            "warranty_years": 15,
            "template_type": "Roofing",
        },
        {
            "job_id": "J1",
            "matched_concept": "foam",
            "source_table": "estimate_template_rows",
            "row_label": "Gaco Roof Foam",
            "selected_item_name": "Gaco Roof 2.7",
            "area_sqft": 25000,
            "template_type": "Roofing",
        },
        {
            "job_id": "J2",
            "matched_concept": "coating",
            "source_table": "estimate_template_rows",
            "row_label": "Gaco Silicone",
            "selected_item_name": "Gaco S20",
            "area_sqft": 12000,
            "warranty_years": 15,
            "template_type": "Roofing",
        },
        {
            "job_id": "J2",
            "matched_concept": "foam",
            "source_table": "estimate_template_rows",
            "row_label": "Gaco Roof Foam",
            "selected_item_name": "Gaco Roof 2.7",
            "area_sqft": 12000,
            "template_type": "Roofing",
        },
    ]
    matches = app.assemble_attribute_job_matches(
        evidence,
        required_concepts=["coating", "foam"],
        job_rows={
            "J1": {"job_id": "J1", "customer": "Acme", "job_name": "Acme Metal Roof", "division": "Roofing"},
            "J2": {"job_id": "J2", "customer": "Beta", "job_name": "Beta Metal Roof", "division": "Roofing"},
        },
        document_signal_rows={
            "J1": {"job_id": "J1", "document_substrate": "metal", "document_material_system": "silicone", "document_warranty_years": 15},
            "J2": {"job_id": "J2", "document_substrate": "metal", "document_material_system": "silicone", "document_warranty_years": 15},
        },
        interpreted={"division": "Roofing"},
        attribute_query={
            "division": "Roofing",
            "warranty_years": 15,
            "substrates": ["metal"],
            "systems": ["silicone"],
            "sqft_filter": {"operator": ">=", "value": 20000},
        },
    )

    assert [match["job_id"] for match in matches] == ["J1"]
    assert matches[0]["template_warranty_years"] == 15


def test_ask_spraytec_attribute_response_shows_evidence() -> None:
    app = importlib.import_module("dashboard.app")

    response = app.attribute_job_search_response(
        [
            {
                "job_id": "J1",
                "customer": "Acme",
                "job_name": "Acme Roof",
                "division": "Roofing",
                "final_price": 125000,
                "match_reason": "Historical estimate rows matched all requested attributes: coating, foam",
                "match_evidence": {
                    "coating": [
                        {
                            "source_table": "estimate_template_rows",
                            "row_number": 22,
                            "row_label": "Gaco Silicone",
                            "selected_item_name": "Gaco S20",
                            "area_sqft": 10000,
                            "source_file": "Estimate Roofing.xlsx",
                        }
                    ],
                    "foam": [
                        {
                            "source_table": "estimate_template_rows",
                            "row_number": 18,
                            "row_label": "Gaco Roof Foam",
                            "selected_item_name": "Gaco Roof 2.7",
                            "area_sqft": 10000,
                            "source_file": "Estimate Roofing.xlsx",
                        }
                    ],
                },
            }
        ],
        {"concepts": ["coating", "foam"]},
    )

    assert "Found 1 job" in response
    assert "Gaco S20" in response
    assert "Gaco Roof 2.7" in response
    assert "$125,000" in response


def test_ask_spraytec_attribute_response_shows_rich_filters() -> None:
    app = importlib.import_module("dashboard.app")

    response = app.attribute_job_search_response(
        [
            {
                "job_id": "J1",
                "customer": "Acme",
                "job_name": "Acme Metal Roof",
                "division": "Roofing",
                "estimated_sqft": 25000,
                "document_substrate": "metal",
                "document_material_system": "silicone",
                "template_warranty_years": 15,
                "match_reason": "Historical estimate rows matched all requested attributes: coating, foam",
                "match_evidence": {
                    "coating": [{"source_table": "estimate_template_rows", "row_label": "Gaco Silicone", "selected_item_name": "Gaco S20"}],
                    "foam": [{"source_table": "estimate_template_rows", "row_label": "Gaco Roof Foam", "selected_item_name": "Gaco Roof 2.7"}],
                },
            }
        ],
        {
            "concepts": ["coating", "foam"],
            "division": "Roofing",
            "warranty_years": 15,
            "substrates": ["metal"],
            "systems": ["silicone"],
            "sqft_filter": {"operator": ">=", "value": 20000},
        },
    )

    assert "Filters applied" in response
    assert "15-year warranty" in response
    assert "substrate metal" in response
    assert "sqft=25,000" in response


def test_ask_spraytec_structured_pack_respects_targets(monkeypatch) -> None:
    app = importlib.import_module("dashboard.app")
    queried_sql: list[str] = []

    def fake_columns(_connection, table_name):
        if table_name == "pricing_catalog":
            return {"product_name", "vendor", "category", "unit_price", "is_current"}
        if table_name == "jobs":
            return {"job_id", "customer", "job_name"}
        return set()

    def fake_query_rows(_connection, sql, params=None):
        queried_sql.append(str(sql))
        return [{"product_name": "GacoFlex S20", "unit_price": 42.5}]

    monkeypatch.setattr(app, "_connection_table_columns", fake_columns)
    monkeypatch.setattr(app, "_query_rows", fake_query_rows)

    evidence = app.build_structured_evidence_pack(
        object(),
        query="Gaco S20 price",
        interpreted={"search_text": "Gaco S20 price"},
        targets={"pricing_catalog"},
    )

    assert list(evidence["facts"]) == ["pricing_catalog"]
    assert any("FROM pricing_catalog" in sql for sql in queried_sql)
    assert not any("FROM jobs" in sql for sql in queried_sql)


def test_operations_dashboard_dates_normalize_timezone_aware_values() -> None:
    app = importlib.import_module("dashboard.app")
    jobs = pd.DataFrame(
        [
            {
                "job_id": "J1",
                "customer": "ABC Church",
                "job_name": "Completed roof",
                "pipeline_status": "Completed",
                "estimated_value": 1000,
                "completion_date": "2026-07-08T12:30:00+00:00",
                "estimated_start_date": "2026-07-07T08:00:00-04:00",
                "estimated_end_date": "2026-07-08T17:00:00-04:00",
            }
        ]
    )

    ops = app.normalize_operations_jobs(jobs)
    today = pd.Timestamp("2026-07-08")

    assert str(ops["completion_date"].dtype) == "datetime64[ns]"
    assert bool((ops["completion_date"].notna() & (ops["completion_date"] >= today - pd.Timedelta(days=30))).iloc[0])


def test_recalculate_workbench_ui_helper_tolerates_legacy_recalculate_signature(monkeypatch) -> None:
    app = importlib.import_module("dashboard.app")
    calls = []

    def legacy_recalculate(workbench):
        calls.append(workbench)
        return {"scope": workbench.get("scope", {}), "legacy": True}

    monkeypatch.setattr(app, "recalculate_workbench_tables", legacy_recalculate)

    result = app.recalculate_workbench_tables_with_optional_data({"scope": {"template_type": "roofing"}}, data=app.EstimatorData())

    assert result["legacy"] is True
    assert calls == [{"scope": {"template_type": "roofing"}}]


def test_merge_editable_rows_marks_labor_hour_override() -> None:
    app = importlib.import_module("dashboard.app")

    merged = app.merge_editable_rows(
        [
            {
                "include": True,
                "template_bucket": "labor_foam",
                "total_hours": 2.4,
                "total_hours_source": "driver_quantity_history",
                "labor_driver_applied": True,
            }
        ],
        [{"include": True, "total_hours": 4.0}],
        {"include", "total_hours"},
    )

    assert merged[0]["total_hours"] == 4.0
    assert merged[0]["manual_labor_hours_override"] is True
    assert merged[0]["total_hours_source"] == "estimator_override"
    assert merged[0]["labor_driver_applied"] is False


def test_merge_editable_rows_marks_include_override() -> None:
    app = importlib.import_module("dashboard.app")

    merged = app.merge_editable_rows(
        [{"include": True, "template_bucket": "primer", "include_source": "historical_companion"}],
        [{"include": False}],
        {"include"},
    )

    assert merged[0]["include"] is False
    assert merged[0]["manual_override"] is True
    assert merged[0]["include_source"] == "estimator_edit"


def test_estimator_page_no_longer_shows_structural_override_block() -> None:
    app = importlib.import_module("dashboard.app")

    source = inspect.getsource(app.estimator_prototype_page)

    assert "Optional structured overrides" not in source
    assert "Surface area sqft" not in source
    assert "Sqft override" not in source


def test_estimator_page_exposes_reference_job_ids_scope_field() -> None:
    app = importlib.import_module("dashboard.app")

    source = inspect.getsource(app.estimator_prototype_page)

    assert "Reference Jobs" in source
    assert "Other Reference Job IDs" in source
    assert "st.multiselect" in source
    assert "reference_job_ids" in source


def test_parse_reference_job_ids_accepts_common_separators() -> None:
    app = importlib.import_module("dashboard.app")

    assert app.parse_reference_job_ids("JOB-1; JOB-2|JOB-3\nJOB-4, JOB-5") == [
        "JOB-1",
        "JOB-2",
        "JOB-3",
        "JOB-4",
        "JOB-5",
    ]


def test_estimator_reference_job_options_use_names_and_template_rows() -> None:
    app = importlib.import_module("dashboard.app")
    data = app.EstimatorData(
        jobs=pd.DataFrame(
            [
                {
                    "job_id": "JOB-1",
                    "customer": "Acme",
                    "job_name": "Metal roof restoration",
                    "estimated_sqft": 10000,
                }
            ]
        ),
        template_rows=pd.DataFrame(
            [
                {
                    "job_id": "JOB-1",
                    "template_type": "roofing",
                    "source_file": "Acme Estimate.xlsx",
                },
                {
                    "job_id": "JOB-2",
                    "template_type": "roofing",
                    "source_file": "Library Roof Estimate.xlsx",
                    "project_type": "roof coating",
                },
                {
                    "job_id": "JOB-3",
                    "template_type": "insulation",
                    "source_file": "Pole Barn Insulation.xlsx",
                },
            ]
        ),
    )

    options, labels = app.estimator_reference_job_options(data, template_type="roofing")

    assert set(options) == {"JOB-1", "JOB-2"}
    assert labels["JOB-1"].startswith("Acme - Metal roof restoration (JOB-1)")
    assert labels["JOB-2"].startswith("Library Roof Estimate.xlsx (JOB-2)")
    assert "JOB-3" not in labels


def test_decision_row_option_helpers_parse_row_specific_options() -> None:
    app = importlib.import_module("dashboard.app")
    row = {
        "workbook_row": "26",
        "editable_selector_code": "11",
        "resolved_template_option": "Gaco Silicone",
        "selector_options_json": (
            '[{"selector_code": "11", "resolved_template_option": "Gaco Silicone"},'
            ' {"selector_code": "12", "resolved_template_option": "Acrylic"}]'
        ),
        "item_options_json": (
            '[{"item_name": "Gaco Silicone Roof Coating", "unit_price": 1250},'
            ' {"item_name": "Gaco Silicone Roof Coating", "unit_price": 1250},'
            ' {"item_name": "Alternate Coating", "unit_price": "review"}]'
        ),
        "crew_selector_options_json": '[{"selector_code": "5", "resolved_template_option": "5 person crew", "crew_size": 5, "daily_rate": 3600}]',
    }

    selector_options = app.decision_row_selector_options(row)
    pricing_options = app.decision_row_pricing_options(row)

    assert [option["selector_code"] for option in selector_options] == ["11", "12"]
    assert [option["item_name"] for option in pricing_options] == [
        "Gaco Silicone Roof Coating",
        "Alternate Coating",
    ]
    assert app.decision_row_has_option_editor(row, {"editable_selector_code", "selected_pricing_candidate"})
    assert app.decision_row_has_option_editor(row, {"crew_size", "daily_rate"})
    assert app._matching_option_index(selector_options, ["Acrylic"], ["resolved_template_option"]) == 1
    assert app.pricing_option_label(pricing_options[0]) == "Gaco Silicone Roof Coating - $1,250.00"
    assert app.pricing_option_label(pricing_options[1]) == "Alternate Coating - review"


def test_estimator_page_exposes_optional_row_option_editor() -> None:
    app = importlib.import_module("dashboard.app")

    source = inspect.getsource(app.estimator_prototype_page)

    assert "Show selected-row option editor" in source
    assert "render_decision_row_option_editor" in source


def test_estimator_chat_panel_supports_multi_turn_replies() -> None:
    app = importlib.import_module("dashboard.app")

    source = inspect.getsource(app.render_estimator_chat_draft_panel)
    page_source = inspect.getsource(app.estimator_prototype_page)

    assert "st.chat_input" in source
    assert "estimator_chat_history_" in source
    assert "existing_scope=existing_scope" in source
    assert "estimator_chat_assistant_history_content" in source
    assert "Start a new estimate chat" in source
    assert "Workbook row changes proposed by chat" not in source
    assert "Parsed scope and workbook inputs" not in source
    assert "Workbook decision cues" not in source
    assert "Photos, job header" not in page_source
    assert "render_estimator_photo_upload_panel" not in page_source
    assert "Build / Rebuild Filled Estimate Template" in page_source


def test_estimator_chat_decision_change_rows_summarize_structured_patches() -> None:
    app = importlib.import_module("dashboard.app")

    rows = app.estimator_chat_decision_change_rows(
        [
            {
                "decision_id": "roofing_fabric_row_79",
                "section": "roofing_detail_template_decisions",
                "template_bucket": "fabric",
                "workbook_row": "79",
                "include": False,
                "confidence": 0.82,
                "review_required": True,
                "review_reasons": ["Only include fabric where seams are open."],
            },
            {
                "decision_id": "roofing_labor_seam_sealer_row_120",
                "template_bucket": "labor_seam_sealer",
                "workbook_row": "120",
                "include": True,
                "proposed_values": {"days": 0.5, "crew_size": 2},
                "confidence": 0.7,
            },
        ]
    )

    assert rows[0]["action"] == "remove"
    assert rows[0]["workbook_row"] == "79"
    assert "fabric" in rows[0]["target"]
    assert "Only include fabric" in rows[0]["why"]
    assert rows[1]["action"] == "include"
    assert "days=0.5" in rows[1]["field_changes"]
    assert "crew_size=2" in rows[1]["field_changes"]


def test_estimator_chat_decision_change_rows_sanitize_alias_only_logistics() -> None:
    app = importlib.import_module("dashboard.app")

    rows = app.estimator_chat_decision_change_rows(
        [
            {
                "decision_id": "labor loading",
                "include": True,
                "proposed_values": {"hours_per_day": 8, "people_count": 2, "trip_count": 1, "unit_price": 1685.775},
            },
            {
                "decision_id": "labor traveling",
                "include": True,
                "proposed_values": {"hours_per_day": 8, "people_count": 5, "trip_count": 2, "unit_price": 1685.775},
            },
        ]
    )

    assert rows[0]["target"] == "labor loading"
    assert "hours_per_day=0.5" in rows[0]["field_changes"]
    assert "people_count=2" in rows[0]["field_changes"]
    assert "unit_price=25.5" in rows[0]["field_changes"]
    assert "1685.775" not in rows[0]["field_changes"]
    assert rows[1]["target"] == "labor traveling"
    assert "hours_per_day=2.5" in rows[1]["field_changes"]
    assert "people_count=5" in rows[1]["field_changes"]
    assert "unit_price=13.0" in rows[1]["field_changes"]
    assert "1685.775" not in rows[1]["field_changes"]


def test_reference_template_memory_capture_skips_when_helper_unavailable(monkeypatch) -> None:
    app = importlib.import_module("dashboard.app")

    monkeypatch.setattr(app, "estimator_sessions", SimpleNamespace())

    app.capture_reference_template_memory_candidates(
        "session-1",
        {
            "workbook_decision_preferences": [
                {
                    "source": "reference_template_summary",
                    "decision_id": "roofing_labor_loading_row_136",
                    "template_bucket": "labor_loading",
                    "include": True,
                    "proposed_values": {"hours_per_day": 0.5},
                }
            ]
        },
        template_type="roofing",
    )


def test_estimator_assistant_exposes_memory_review_and_persistent_chat_state() -> None:
    app = importlib.import_module("dashboard.app")
    source = inspect.getsource(app.estimator_prototype_page)
    chat_source = inspect.getsource(app.render_estimator_chat_draft_panel)

    assert "Estimator Memory Review" in source
    assert "render_estimator_memory_admin()" in source
    assert "estimator_chat_history_active" in chat_source
    assert "estimator_chat_result_active" in chat_source


def test_roofing_free_adder_section_uses_edited_scope_template_type() -> None:
    app = importlib.import_module("dashboard.app")
    source = inspect.getsource(app.estimator_prototype_page)

    assert "if not is_insulation:" not in source
    assert "roofing_free_adder_template_decisions" in source
    assert 'edited_scope.get("template_type")' in source


def test_estimator_workbench_uses_compact_columns_by_default() -> None:
    app = importlib.import_module("dashboard.app")

    assert {"include", "workbook_row", "package", "estimated_cost", app.CHOICE_SUMMARY_COLUMN, "product_guidance"}.issubset(
        set(app.MATERIAL_WORKBENCH_COMPACT_COLUMNS)
    )
    assert {"include", "workbook_row", "labor_package", "calculated_hours", "estimated_cost", app.CHOICE_SUMMARY_COLUMN}.issubset(
        set(app.LABOR_WORKBENCH_COMPACT_COLUMNS)
    )
    assert "decision_evidence_count" not in app.MATERIAL_WORKBENCH_COMPACT_COLUMNS
    assert "decision_evidence_count" not in app.LABOR_WORKBENCH_COMPACT_COLUMNS
    assert {"include", "workbook_row", "hours_per_day", "people_count", "trip_count", "unit_price"}.issubset(
        set(app.ROOFING_LOGISTICS_EXPENSE_TEMPLATE_COMPACT_COLUMNS)
    )
    assert app.ADDER_WORKBENCH_COMPACT_COLUMNS == [
        "include",
        "workbook_row",
        "adder",
        "editable_value",
        "evidence_count",
        "confidence",
        app.CHOICE_SUMMARY_COLUMN,
        "notes",
    ]
    source = inspect.getsource(app.estimator_prototype_page)
    assert "Show detailed row diagnostics" in source
    assert "workbench_display_frame_from_records" in source
    assert app.INSULATION_DECISION_SECTION_COLUMNS["insulation_labor_template_decisions"] == [
        "include",
        "workbook_row",
        "labor_task",
        app.CHOICE_SUMMARY_COLUMN,
        "days",
        "crew_size",
        "daily_rate",
        "hourly_rate",
        "total_hours",
        "labor_driver_summary",
        "formula_mode",
        "estimated_cost",
        "compatibility_status",
        "notes",
    ]
    assert "gal_per_100_sqft" not in app.INSULATION_DECISION_SECTION_COLUMNS["insulation_labor_template_decisions"]
    assert "total_hours" not in app.INSULATION_DECISION_SECTION_COLUMNS["insulation_detail_material_template_decisions"]


def test_project_display_frame_removes_hidden_compact_columns() -> None:
    app = importlib.import_module("dashboard.app")
    frame = pd.DataFrame(
        [
            {
                "include": True,
                "workbook_row": "86",
                "labor_task": "Foam",
                "total_hours": 12,
                "labor_driver_summary": "2 set x 6 hours_per_foam_set",
                "gal_per_100_sqft": 1.5,
                "feet_per_unit": 10,
            }
        ]
    )

    projected = app.project_display_frame(
        frame,
        app.INSULATION_DECISION_SECTION_COLUMNS["insulation_labor_template_decisions"],
    )

    assert list(projected.columns) == ["include", "workbook_row", "labor_task", "total_hours", "labor_driver_summary"]
    assert "gal_per_100_sqft" not in projected.columns
    assert "feet_per_unit" not in projected.columns


def test_project_display_frame_keeps_calculation_and_choice_summary_not_raw_evidence() -> None:
    app = importlib.import_module("dashboard.app")
    records = app.display_safe_records(
        [
            {
                "include": True,
                "workbook_row": "42",
                "resolved_template_option": "Gaco Silicone",
                "basis_sqft": 10000,
                "gal_per_100_sqft": 1.5,
                "unit_price": 1200,
                "estimated_cost": 18000,
                "decision_evidence_summary": "Included because coating path was requested.",
                "historical_selector_evidence_count": 12,
                "compatibility_warnings": "Verify substrate qualification.",
                "product_guidance": "Confirm adhesion and dry substrate.",
            }
        ]
    )
    frame = pd.DataFrame(records)

    projected = app.project_display_frame(frame, app.ROOFING_COATING_TEMPLATE_COMPACT_COLUMNS)

    assert app.CHOICE_SUMMARY_COLUMN in projected.columns
    assert "decision_evidence_summary" not in projected.columns
    assert "historical_selector_evidence_count" not in projected.columns
    assert "compatibility_warnings" not in projected.columns
    assert {"basis_sqft", "gal_per_100_sqft", "unit_price", "estimated_cost", "product_guidance"}.issubset(projected.columns)
    assert "Included because coating path was requested." in projected[app.CHOICE_SUMMARY_COLUMN].iloc[0]
    assert "Verify substrate qualification." in projected[app.CHOICE_SUMMARY_COLUMN].iloc[0]


def test_display_safe_dataframe_handles_mixed_proposed_values_for_streamlit() -> None:
    app = importlib.import_module("dashboard.app")
    pa = pytest.importorskip("pyarrow")

    frame = app.display_safe_dataframe(
        [
            {
                "decision_id": "foam_type",
                "proposed_values": 2,
                "proposal_confidence": 0.8,
            },
            {
                "decision_id": "foam_system",
                "proposed_values": "Closed-cell spray foam",
                "proposal_confidence": 0.7,
            },
            {
                "decision_id": "scope",
                "proposed_values": {"surface": "walls", "area_sqft": 1200},
                "proposal_confidence": 0.9,
            },
        ]
    )

    assert frame["proposed_values"].tolist() == [
        "2",
        "Closed-cell spray foam",
        '{"area_sqft": 1200, "surface": "walls"}',
    ]
    pa.Table.from_pandas(frame)


def test_auto_detect_classifies_pipe_boot_leak_as_repair() -> None:
    app = importlib.import_module("dashboard.app")

    mode = app.classify_estimate_type_from_notes("Active leak around one pipe boot on TPO roof. Patch and seal.")

    assert mode == app.ESTIMATE_TYPE_REPAIR


def test_auto_detect_classifies_silicone_sqft_as_restoration() -> None:
    app = importlib.import_module("dashboard.app")

    mode = app.classify_estimate_type_from_notes(
        "10-year silicone coating system over 9,500 sqft metal roof. Need warranty restoration."
    )

    assert mode == app.ESTIMATE_TYPE_RESTORATION


def test_auto_detect_classifies_spray_foam_building_email_as_insulation() -> None:
    app = importlib.import_module("dashboard.app")

    mode = app.classify_estimate_type_from_notes(
        "I need a quote for foam sprayed in a 30x40 metal building with 9' walls. "
        "Insulate outside walls and ceiling with spray foam."
    )

    assert mode == app.ESTIMATE_TYPE_INSULATION


def test_auto_detect_classifies_concrete_floor_coating_as_flooring() -> None:
    app = importlib.import_module("dashboard.app")

    mode = app.classify_estimate_type_from_notes(
        "2,400 sq ft concrete floor system, grind and patch prep, epoxy base, polyaspartic top coat, flake broadcast."
    )

    assert mode == app.ESTIMATE_TYPE_FLOORING


def test_mode_selector_routes_to_repair_estimator() -> None:
    app = importlib.import_module("dashboard.app")

    route, result = app.route_estimator_request(
        "Active leak around one pipe boot on TPO roof. Patch and seal.",
        app.ESTIMATE_TYPE_REPAIR,
        repair_data=sample_repair_tables(),
    )

    assert route == app.ESTIMATE_TYPE_REPAIR
    assert result.parsed_scope["issue_type"] == "pipe_boot_leak"


def test_mode_selector_routes_to_flooring_estimator() -> None:
    app = importlib.import_module("dashboard.app")

    route, result = app.route_estimator_request(
        "Flooring job, 2,400 sq ft concrete slab. Grind prep, epoxy base, polyaspartic top coat.",
        app.ESTIMATE_TYPE_FLOORING,
    )

    assert route == app.ESTIMATE_TYPE_FLOORING
    assert result.parsed_scope["template_type"] == "flooring"
    assert result.parsed_scope["area_sqft"] == 2400


def test_repair_mode_does_not_call_roof_coating_estimator() -> None:
    app = importlib.import_module("dashboard.app")

    def fail_roof_estimator(*args, **kwargs):
        raise AssertionError("roof coating estimator should not be called for repair mode")

    route, result = app.route_estimator_request(
        "Active leak around one pipe boot on TPO roof. Patch and seal.",
        app.ESTIMATE_TYPE_REPAIR,
        repair_data=sample_repair_tables(),
        field_estimator_fn=fail_roof_estimator,
    )

    assert route == app.ESTIMATE_TYPE_REPAIR
    assert result.estimated_invoice_target is not None
