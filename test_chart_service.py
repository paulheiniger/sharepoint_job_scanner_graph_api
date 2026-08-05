from __future__ import annotations

import csv
import io

from jobscan.business.chart_service import build_chart_dataset, chart_dataset_csv


def test_build_chart_dataset_preserves_truth_and_chart_semantics() -> None:
    dataset = build_chart_dataset(
        "production_budget_by_job",
        {
            "as_of": "2026-07-31T12:00:00Z",
            "truth_class": "proxy",
            "filters_applied": {"over_plan_only": True},
            "records": [
                {
                    "job_name": "Acme Roof",
                    "estimated_production_budget": 30000,
                    "estimated_cost_used_proxy": 34000,
                    "budget_used_pct": 1.1333,
                    "ignored_detail": "not part of the chart contract",
                }
            ],
            "source_tables": [
                "job_tracking_summary",
                "job_tracking_estimate_budget_snapshot",
            ],
            "data_freshness": {"estimate_budget_as_of": "2026-07-31T11:45:00Z"},
            "warnings": ["Not accounting actual costs."],
        },
    )

    assert dataset["recommended_chart_type"] == "bar"
    assert dataset["truth_class"] == "proxy"
    assert dataset["category_field"] == "job_name"
    assert dataset["display"]["orientation"] == "horizontal"
    assert dataset["display"]["multi_scale_strategy"] == "dual_axis"
    assert dataset["display"]["reference_lines"] == [
        {
            "field": "budget_used_pct",
            "value": 1.0,
            "label": "Estimate-rate plan",
        }
    ]
    assert dataset["series"][0]["number_format"] == "currency_0"
    assert dataset["series"][2]["axis"] == "secondary"
    assert dataset["staging"]["source_storage"] == "hybrid_current_snapshot"
    assert dataset["staging"]["snapshot_tables"] == [
        "job_tracking_estimate_budget_snapshot"
    ]
    assert dataset["staging"]["historical_series_available"] is False
    assert dataset["rows"] == [
        {
            "job_name": "Acme Roof",
            "estimated_production_budget": 30000,
            "estimated_cost_used_proxy": 34000,
            "budget_used_pct": 1.1333,
        }
    ]


def test_chart_dataset_applies_endpoint_owned_order_and_status_colors() -> None:
    dataset = build_chart_dataset(
        "sales_pipeline_by_stage",
        {
            "stage_rollup": [
                {"pipeline_status": "Completed", "estimated_value": 3, "job_count": 1},
                {"pipeline_status": "Custom", "estimated_value": 9, "job_count": 1},
                {"pipeline_status": "Proposed", "estimated_value": 5, "job_count": 2},
                {"pipeline_status": "Contracted", "estimated_value": 7, "job_count": 1},
            ]
        },
    )

    assert [row["pipeline_status"] for row in dataset["rows"]] == [
        "Proposed",
        "Contracted",
        "Completed",
        "Custom",
    ]
    assert dataset["display"]["sort"] == {
        "field": "pipeline_status",
        "direction": "ascending",
        "then_by": [],
    }
    assert dataset["display"]["category_colors"]["Proposed"] == "#2563EB"


def test_chart_dataset_uses_small_multiples_for_three_incompatible_units() -> None:
    dataset = build_chart_dataset(
        "operations_backlog_by_readiness",
        {
            "readiness_rollup": [
                {
                    "readiness_status": "Ready To Schedule",
                    "value": 200000,
                    "jobs": 3,
                    "average_days_waiting": 12.5,
                }
            ],
            "source_tables": ["operations_dashboard_ops_snapshot"],
        },
    )

    assert dataset["display"]["multi_scale_strategy"] == "small_multiples"
    assert {series["panel"] for series in dataset["series"]} == {
        "currency",
        "count",
        "days",
    }
    assert dataset["staging"]["source_storage"] == "current_snapshot"


def test_chart_dataset_csv_is_file_ready_and_formula_safe() -> None:
    dataset = build_chart_dataset(
        "sales_pipeline_by_owner",
        {
            "as_of": "2026-07-31T12:00:00Z",
            "owner_rollup": [
                {"owner": "=HYPERLINK(\"bad\")", "estimated_value": 10, "job_count": 1}
            ],
        },
    )

    rows = list(csv.DictReader(io.StringIO(chart_dataset_csv(dataset))))

    assert rows[0]["dataset"] == "sales_pipeline_by_owner"
    assert rows[0]["owner"].startswith("'=")
    assert rows[0]["estimated_value"] == "10"


def test_schedule_gantt_groups_crews_and_clips_long_projects_to_window() -> None:
    dataset = build_chart_dataset(
        "operations_schedule_gantt",
        {
            "as_of": "2026-08-01T12:00:00Z",
            "filters_applied": {
                "start_date": "2026-08-01",
                "end_date": "2026-08-31",
            },
            "records": [
                {
                    "job_id": "JOB-LONG",
                    "job_name": "Long Roof",
                    "assigned_crew_leader": "Carlos",
                    "estimated_start_date": "2026-07-15",
                    "estimated_end_date": "2026-10-15",
                    "schedule_health": "On schedule",
                },
                {
                    "job_id": "JOB-DURATION",
                    "job_name": "Duration Only",
                    "assigned_crew_leader": "",
                    "estimated_start_date": "2026-08-10",
                    "estimated_duration_days": 5,
                },
            ],
            "source_tables": ["operations_dashboard_ops_snapshot"],
            "coverage": {"results_truncated": True},
        },
    )

    assert dataset["recommended_chart_type"] == "gantt"
    assert dataset["group_field"] == "crew_leader"
    assert dataset["start_field"] == "display_start_date"
    assert dataset["end_field"] == "display_end_date"
    by_job = {row["job_id"]: row for row in dataset["rows"]}
    assert by_job["JOB-LONG"]["display_start_date"] == "2026-08-01"
    assert by_job["JOB-LONG"]["display_end_date"] == "2026-08-31"
    assert by_job["JOB-LONG"]["continues_before_window"] is True
    assert by_job["JOB-LONG"]["continues_after_window"] is True
    assert by_job["JOB-DURATION"]["crew_leader"] == "Unassigned"
    assert by_job["JOB-DURATION"]["raw_end_date"] == "2026-08-14"
    assert by_job["JOB-DURATION"]["end_date_source"] == "estimated_duration_days"
    assert dataset["coverage"]["gantt_clipped_after_window_rows"] == 1
    assert dataset["coverage"]["gantt_inferred_end_rows"] == 1
    assert any("exceeded" in warning for warning in dataset["warnings"])

    csv_rows = list(csv.DictReader(io.StringIO(chart_dataset_csv(dataset))))
    assert csv_rows[0]["crew_leader"]
    assert csv_rows[0]["raw_start_date"]


def test_schedule_gantt_omits_only_exact_duplicate_bars() -> None:
    common = {
        "job_id": "JOB-1",
        "job_name": "Example Roof",
        "assigned_crew_leader": "Santos",
        "estimated_start_date": "2026-08-17",
        "estimated_end_date": "2026-08-22",
    }
    dataset = build_chart_dataset(
        "operations_schedule_gantt",
        {
            "filters_applied": {
                "start_date": "2026-08-01",
                "end_date": "2026-08-31",
            },
            "records": [
                common,
                dict(common),
                {**common, "estimated_start_date": "2026-08-24"},
                {**common, "assigned_crew_leader": "Gustavo"},
            ],
        },
    )

    assert len(dataset["rows"]) == 3
    assert dataset["coverage"]["gantt_duplicate_rows_omitted"] == 1
    assert any(
        "1 exact duplicate schedule row" in warning
        for warning in dataset["warnings"]
    )
