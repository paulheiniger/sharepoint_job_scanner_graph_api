from __future__ import annotations

import importlib
import inspect

import pandas as pd

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


def test_estimator_page_no_longer_shows_structural_override_block() -> None:
    app = importlib.import_module("dashboard.app")

    source = inspect.getsource(app.estimator_prototype_page)

    assert "Optional structured overrides" not in source
    assert "Surface area sqft" not in source
    assert "Sqft override" not in source


def test_estimator_workbench_uses_compact_columns_by_default() -> None:
    app = importlib.import_module("dashboard.app")

    assert app.MATERIAL_WORKBENCH_COMPACT_COLUMNS == [
        "include",
        "workbook_row",
        "package",
        "estimator_decision",
        "historical_recommendation",
        "editable_value",
        "calculated_output_summary",
        "item_name",
        "suggested_by_notes_rules",
        "editable_basis_sqft",
        "editable_qty_per_sqft",
        "calculated_quantity",
        "unit",
        "current_unit_price",
        "estimated_cost",
        "decision_evidence_count",
        "decision_confidence",
        "product_guidance",
        "product_warning_summary",
        "row_traceability",
        "notes",
    ]
    assert app.LABOR_WORKBENCH_COMPACT_COLUMNS == [
        "include",
        "workbook_row",
        "labor_package",
        "estimator_decision",
        "historical_recommendation",
        "editable_value",
        "calculated_output_summary",
        "suggested_by_notes_rules",
        "days",
        "crew_people_selection",
        "daily_rate",
        "formula_mode",
        "editable_hours_per_1000_sqft",
        "calculated_hours",
        "crew_size",
        "labor_rate",
        "estimated_cost",
        "decision_evidence_count",
        "decision_confidence",
        "row_traceability",
        "notes",
    ]
    assert app.ADDER_WORKBENCH_COMPACT_COLUMNS == [
        "include",
        "workbook_row",
        "adder",
        "editable_value",
        "evidence_count",
        "confidence",
        "notes",
    ]
    source = inspect.getsource(app.estimator_prototype_page)
    assert "Show detailed row diagnostics" in source
    assert "project_display_frame" in source
    assert app.INSULATION_DECISION_SECTION_COLUMNS["insulation_labor_template_decisions"] == [
        "include",
        "workbook_row",
        "labor_task",
        "days",
        "crew_size",
        "daily_rate",
        "hourly_rate",
        "total_hours",
        "formula_mode",
        "estimated_cost",
        "compatibility_status",
        "compatibility_warnings",
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
                "gal_per_100_sqft": 1.5,
                "feet_per_unit": 10,
            }
        ]
    )

    projected = app.project_display_frame(
        frame,
        app.INSULATION_DECISION_SECTION_COLUMNS["insulation_labor_template_decisions"],
    )

    assert list(projected.columns) == ["include", "workbook_row", "labor_task", "total_hours"]
    assert "gal_per_100_sqft" not in projected.columns
    assert "feet_per_unit" not in projected.columns


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
