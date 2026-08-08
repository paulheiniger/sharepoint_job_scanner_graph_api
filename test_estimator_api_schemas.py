from __future__ import annotations

import pytest
from pydantic import ValidationError

from services.estimator_api.generate_openapi import _validated_server_url
from services.estimator_api.schemas import (
    ChartDatasetRequest,
    EstimateContextRequest,
    EstimateContextResponse,
    EstimateWorkbookRequest,
    EstimateWorkbookOptionsRequest,
    JobSearchRequest,
    OfficeActivityRequest,
    OfficeJobProgressRequest,
    OperationsBacklogRequest,
    OperationsScheduleRequest,
    ProductionBudgetHealthRequest,
    RoofMeasureCalculationRequest,
    RoofMeasureContextRequest,
    SalesFollowupRequest,
    SalesPipelineRequest,
)


def test_roof_measure_requests_are_bounded_and_choose_one_polygon_source() -> None:
    context = RoofMeasureContextRequest(
        address="830 South 1st Street, Louisville, KY 40203"
    )
    assert context.view == "building_detail"
    assert context.include_lidar_coverage is True
    assert RoofMeasureContextRequest(
        address="830 South 1st Street, Louisville, KY 40203",
        view="close_detail",
    ).view == "close_detail"

    selected = RoofMeasureCalculationRequest(
        context_id="a" * 32,
        selected_footprint_ids=["fp-01"],
        pitch_rise_per_12=0,
    )
    assert selected.pitch_rise_per_12 == 0

    with pytest.raises(ValidationError):
        RoofMeasureCalculationRequest(
            context_id="a" * 32,
            selected_footprint_ids=["fp-01"],
            sections=[
                {
                    "section_id": "custom",
                    "polygon": [
                        {"x": 0, "y": 0},
                        {"x": 10, "y": 0},
                        {"x": 10, "y": 10},
                    ],
                }
            ],
        )

    with pytest.raises(ValidationError):
        RoofMeasureCalculationRequest(context_id="a" * 32)

    sam2 = RoofMeasureCalculationRequest(
        context_id="a" * 32,
        sam2_candidate_id="sam2-0123456789abcdef",
    )
    assert sam2.sam2_candidate_id == "sam2-0123456789abcdef"

    with pytest.raises(ValidationError):
        RoofMeasureCalculationRequest(
            context_id="a" * 32,
            selected_footprint_ids=["fp-01"],
            sam2_candidate_id="sam2-0123456789abcdef",
        )


def test_chart_dataset_request_is_typed_and_bounded() -> None:
    request = ChartDatasetRequest.model_validate(
        {
            "dataset": "office_activity_by_day",
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
        }
    )
    assert request.dataset == "office_activity_by_day"

    with pytest.raises(ValidationError):
        ChartDatasetRequest.model_validate({"dataset": "invented_chart"})

    gantt = ChartDatasetRequest.model_validate(
        {"dataset": "operations_schedule_gantt", "gantt_limit": 125}
    )
    assert gantt.gantt_limit == 125
    with pytest.raises(ValidationError):
        ChartDatasetRequest.model_validate(
            {"dataset": "operations_schedule_gantt", "gantt_limit": 126}
        )


def test_context_request_defaults() -> None:
    request = EstimateContextRequest(raw_notes="30x40 metal building")
    assert request.scope == {}
    assert request.reference_job_ids == []
    assert request.exclude_job_ids == []
    assert request.exclude_source_files == []
    assert request.include_source_metadata is False


def test_context_request_caps_reference_jobs() -> None:
    with pytest.raises(ValidationError):
        EstimateContextRequest(
            raw_notes="Job",
            reference_job_ids=[f"JOB-{index}" for index in range(11)],
        )

    with pytest.raises(ValidationError):
        EstimateContextRequest(
            raw_notes="Job",
            exclude_job_ids=[f"JOB-{index}" for index in range(21)],
        )


def test_context_request_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        EstimateContextRequest(
            raw_notes="Job",
            unexpected_action="write-workbook",
        )


def test_context_response_accepts_bounded_contract() -> None:
    response = EstimateContextResponse.model_validate(
        {
            "schema_version": "spraytec.copilot_estimator_context.v1",
            "scope": {"template_type": "roofing"},
            "template_type": "roofing",
            "warnings": [],
            "retrieval_summary": {"matched_comparable_count": 0},
        }
    )
    assert response.scope["template_type"] == "roofing"
    assert response.source_metadata is None


def test_workbook_request_is_confirmation_gated_and_semantic() -> None:
    request = EstimateWorkbookRequest.model_validate(
        {
            "confirmed": True,
            "header": {"job_name": "Clean Roof", "estimated_sqft": 5000},
            "materials": [
                {
                    "category": "edge_metal",
                    "item": "24 gauge edge metal",
                    "linear_ft": 180,
                    "unit_price": 8.5,
                }
            ],
        }
    )

    assert request.confirmed is True
    assert request.materials[0].category == "edge_metal"

    with pytest.raises(ValidationError):
        EstimateWorkbookRequest.model_validate(
            {
                "header": {"job_name": "Clean Roof", "estimated_sqft": 5000},
                "labor": [{"task": "labor_prep", "days": 1, "crew_size": 9}],
            }
        )


def test_workbook_labor_override_requires_a_review_reason() -> None:
    base = {
        "confirmed": True,
        "template_type": "roofing",
        "header": {"job_name": "Reviewed labor exception", "estimated_sqft": 5000},
        "labor": [{"task": "labor_prep", "days": 1, "crew_size": 5}],
    }
    with pytest.raises(ValidationError, match="labor_override_reason"):
        EstimateWorkbookRequest.model_validate(
            {**base, "labor_plan_mode": "estimator_override"}
        )

    request = EstimateWorkbookRequest.model_validate(
        {
            **base,
            "labor_plan_mode": "estimator_override",
            "labor_override_reason": "Estimator reviewed restricted access and reduced production.",
        }
    )
    assert request.labor_plan_mode == "estimator_override"


def test_workbook_request_accepts_structured_roofing_scope_and_quantity_provenance() -> None:
    request = EstimateWorkbookRequest.model_validate(
        {
            "confirmed": True,
            "template_type": "roofing",
            "structured_scope": {
                "declared_total_area_sqft": 5136,
                "area_scopes": [
                    {
                        "scope_id": "tearoff",
                        "scope_role": "exclusive_area",
                        "area_sqft": 3120,
                        "action": "Full removal down to wood decking",
                        "proposed_assembly": "2 inch ISO and coated foam",
                    },
                    {
                        "scope_id": "deck-repair",
                        "parent_scope_id": "tearoff",
                        "scope_role": "nested_sub_scope",
                        "area_sqft": 320,
                        "action": "Replace deteriorated decking",
                    },
                    {
                        "scope_id": "recover",
                        "scope_role": "exclusive_area",
                        "area_sqft": 2016,
                        "proposed_assembly": "Coated foam over existing roof",
                    },
                ],
            },
            "header": {"job_name": "Grossman", "estimated_sqft": 5136},
            "materials": [
                {
                    "category": "board_stock",
                    "area_sqft": 3120,
                    "basis_sqft": 3136,
                    "quantity_adjustment_reason": "98 full 4x8 sheets",
                }
            ],
        }
    )

    assert request.structured_scope is not None
    assert request.structured_scope.area_scopes[1].parent_scope_id == "tearoff"
    assert request.materials[0].area_sqft == 3120
    assert request.materials[0].basis_sqft == 3136


def test_workbook_request_accepts_roofing_foam_price_per_set() -> None:
    request = EstimateWorkbookRequest.model_validate(
        {
            "confirmed": True,
            "template_type": "roofing",
            "header": {"job_name": "Foam unit contract", "estimated_sqft": 5000},
            "materials": [
                {
                    "category": "roofing_foam",
                    "area_sqft": 5000,
                    "thickness_inches": 1.5,
                    "yield_factor": 2700,
                    "price_per_set": 2150,
                }
            ],
        }
    )

    assert request.materials[0].price_per_set == 2150


def test_workbook_request_accepts_insulation_decisions_and_area_reconciliation() -> None:
    request = EstimateWorkbookRequest.model_validate(
        {
            "confirmed": True,
            "template_type": "insulation",
            "header": {
                "job_name": "Field Notes Test",
                "job_type": "Spray foam insulation",
                "estimated_sqft": 4611,
                "sqft_calculation_rows": [
                    {"description": "Gross walls", "area_sqft": 5727},
                    {"description": "Openings", "area_sqft": -1116},
                ],
            },
            "materials": [
                {
                    "category": "foam",
                    "selector_code": 11,
                    "area_sqft": 4611,
                    "thickness_inches": 2,
                    "yield_factor": 3500,
                    "unit_price": 2.2,
                }
            ],
            "labor": [
                {"task": "labor_foam", "days": 5, "crew_size": 3},
            ],
        }
    )

    assert request.template_type == "insulation"
    assert request.materials[0].category == "foam"
    assert request.labor[0].task == "labor_foam"
    assert sum(row.area_sqft for row in request.header.sqft_calculation_rows) == 4611

    with pytest.raises(ValidationError):
        EstimateWorkbookRequest.model_validate(
            {
                "header": {"job_name": "Clean Roof"},
                "materials": [{"category": "edge_metal", "workbook_row": 82}],
            }
        )


def test_workbook_request_accepts_flooring_template_and_labor() -> None:
    request = EstimateWorkbookRequest.model_validate(
        {
            "confirmed": True,
            "template_type": "flooring",
            "header": {
                "job_name": "Garage Floor",
                "job_type": "Floor system repair",
                "estimated_sqft": 484,
            },
            "materials": [
                {
                    "category": "coating",
                    "item": "NPI 707 Epoxy",
                    "area_sqft": 500,
                    "gal_per_100_sqft": 1,
                }
            ],
            "labor": [
                {
                    "task": "labor_floor_grind_patch",
                    "days": 1,
                    "crew_size": 2,
                }
            ],
        }
    )

    assert request.template_type == "flooring"
    assert request.labor[0].task == "labor_floor_grind_patch"


def test_workbook_request_accepts_localized_roof_repair_inputs() -> None:
    request = EstimateWorkbookRequest.model_validate(
        {
            "confirmed": True,
            "template_type": "roofing",
            "header": {
                "job_name": "Corner Leak Repair",
                "estimated_sqft": 8184,
                "estimated_days": 1,
                "estimated_hours": 20,
                "estimated_crew_size": 2,
                "repair_area_description": "50' x 12'",
                "warranty_description": "2 Yr. Workmanship",
            },
            "materials": [
                {
                    "category": "coating",
                    "area_sqft": 250,
                    "gal_per_100_sqft": 2.5,
                }
            ],
            "labor": [
                {
                    "task": "labor_setup_safety",
                    "label": "Setup/Safety",
                    "days": 0.25,
                    "crew_size": 2,
                }
            ],
        }
    )

    assert request.header.estimated_sqft == 8184
    assert request.header.repair_area_description == "50' x 12'"
    assert request.header.warranty_description == "2 Yr. Workmanship"
    assert request.labor[0].task == "labor_setup_safety"


def test_workbook_options_request_requires_two_unique_complete_options() -> None:
    base_option = {
        "template_type": "roofing",
        "header": {"job_name": "Option Roof", "estimated_sqft": 5000},
        "materials": [{"category": "coating", "item": "Gaco Silicone"}],
    }
    request = EstimateWorkbookOptionsRequest.model_validate(
        {
            "confirmed": True,
            "options": [
                {**base_option, "option_label": "10-year warranty"},
                {**base_option, "option_label": "15-year warranty"},
            ],
        }
    )

    assert [option.option_label for option in request.options] == [
        "10-year warranty",
        "15-year warranty",
    ]

    with pytest.raises(ValidationError):
        EstimateWorkbookOptionsRequest.model_validate(
            {
                "confirmed": True,
                "options": [
                    {**base_option, "option_label": "10-year warranty"},
                    {**base_option, "option_label": "10-Year Warranty"},
                ],
            }
        )


def test_action_server_url_requires_https_origin() -> None:
    assert (
        _validated_server_url("https://example.ngrok.app/")
        == "https://example.ngrok.app"
    )
    with pytest.raises(ValueError):
        _validated_server_url("http://127.0.0.1:8770")
    with pytest.raises(ValueError):
        _validated_server_url("https://example.ngrok.app/api")


def test_job_search_request_is_bounded_and_read_only() -> None:
    request = JobSearchRequest(
        query="Acme",
        needs_attention=True,
        material_system="silicone",
        page=2,
        page_size=100,
    )
    assert request.page == 2
    assert request.page_size == 100
    assert request.material_system == "silicone"

    with pytest.raises(ValidationError):
        JobSearchRequest(page_size=101)
    with pytest.raises(ValidationError):
        JobSearchRequest(query="Acme", update_status="Completed")


def test_sales_requests_are_bounded_and_reject_mutations() -> None:
    assert SalesPipelineRequest(limit=25).include_completed is False
    assert SalesFollowupRequest(overdue_only=True).overdue_only is True

    with pytest.raises(ValidationError):
        SalesPipelineRequest(limit=26)
    with pytest.raises(ValidationError):
        SalesFollowupRequest(assign_to="Pat")


def test_operations_requests_are_bounded_and_reject_mutations() -> None:
    backlog = OperationsBacklogRequest(unscheduled_only=True, limit=25)
    schedule = OperationsScheduleRequest(
        start_date="2026-08-01",
        end_date="2026-08-14",
        risk_only=True,
    )

    assert backlog.unscheduled_only is True
    assert schedule.start_date.isoformat() == "2026-08-01"
    assert schedule.risk_only is True

    with pytest.raises(ValidationError):
        OperationsBacklogRequest(limit=26)
    with pytest.raises(ValidationError):
        OperationsScheduleRequest(assign_crew="Carlos")


def test_office_activity_request_is_bounded_and_rejects_mutations() -> None:
    request = OfficeActivityRequest(
        employee="Anthony P",
        start_date="2026-07-24",
        end_date="2026-07-30",
        timed_only=True,
        limit=25,
    )

    assert request.employee == "Anthony P"
    assert request.start_date.isoformat() == "2026-07-24"
    assert request.timed_only is True

    with pytest.raises(ValidationError):
        OfficeActivityRequest(limit=26)
    with pytest.raises(ValidationError):
        OfficeActivityRequest(create_entry=True)


def test_office_job_progress_request_is_bounded_and_rejects_mutations() -> None:
    request = OfficeJobProgressRequest(
        division="Roofing",
        lookback_days=90,
        stalled_after_days=7,
        stalled_only=True,
        limit=25,
    )

    assert request.division == "Roofing"
    assert request.stalled_only is True

    with pytest.raises(ValidationError):
        OfficeJobProgressRequest(lookback_days=366)
    with pytest.raises(ValidationError):
        OfficeJobProgressRequest(assign_job_id="JOB-1")


def test_production_budget_request_is_bounded_and_rejects_mutations() -> None:
    request = ProductionBudgetHealthRequest(
        job_ids=["JOB-1"],
        over_plan_only=True,
        limit=25,
    )

    assert request.job_ids == ["JOB-1"]
    assert request.over_plan_only is True

    with pytest.raises(ValidationError):
        ProductionBudgetHealthRequest(
            job_ids=[f"JOB-{index}" for index in range(26)]
        )
    with pytest.raises(ValidationError):
        ProductionBudgetHealthRequest(update_actual_cost=1000)
