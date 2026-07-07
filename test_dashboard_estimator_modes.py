from __future__ import annotations

import importlib
import inspect

import pandas as pd
import pytest

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
    assert "Workbook row changes proposed by chat" in source
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
    assert "project_display_frame" in source
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


def test_mode_selector_routes_to_repair_estimator() -> None:
    app = importlib.import_module("dashboard.app")

    route, result = app.route_estimator_request(
        "Active leak around one pipe boot on TPO roof. Patch and seal.",
        app.ESTIMATE_TYPE_REPAIR,
        repair_data=sample_repair_tables(),
    )

    assert route == app.ESTIMATE_TYPE_REPAIR
    assert result.parsed_scope["issue_type"] == "pipe_boot_leak"


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
