from __future__ import annotations

import pandas as pd

from jobscan.estimator import estimate_from_field_notes
from test_field_estimator import field_data


ROOF_COATING_BASIC_NOTE = (
    "Roof coating estimate for a commercial metal roof in Louisville KY. "
    "Main roof is 120 ft by 80 ft. Deduct two skylights, each 4 ft by 8 ft. "
    "Roof is fair overall but has rusted fasteners and some open seams. "
    "Customer wants a 10-year silicone coating system. Access is easy. "
    "Few penetrations. Use standard white silicone if available."
)


def data_with_oversized_roof_labor_rows():
    data = field_data()
    data.relationship_labor_rates = pd.DataFrame()
    rows = []
    for index, (bucket, hours, cost) in enumerate(
        [
            ("labor_prep", 300, 21000),
            ("labor_seam_sealer", 220, 15400),
            ("labor_base", 260, 18200),
            ("labor_top_coat", 260, 18200),
            ("labor_details", 170, 11900),
            ("labor_cleanup", 100, 7000),
            ("labor_loading", 60, 4200),
            ("labor_prime", 140, 9800),
            ("infrared_scan", 40, 2800),
            ("labor_top_coat_granules", 80, 5600),
        ],
        start=1,
    ):
        rows.append(
            {
                "template_row_id": f"LAB-{index}",
                "document_id": "D-LAB",
                "job_id": "J-LAB",
                "source_file": "Roofing Estimate.xlsx",
                "template_type": "roofing",
                "project_type": "roof coating",
                "substrate": "metal",
                "warranty_years": 10,
                "template_bucket": bucket,
                "line_item_kind": "labor",
                "days": max(hours / 32, 1),
                "crew_size": 4,
                "total_hours": hours,
                "estimated_cost": cost,
                "historical_sqft": 9536,
            }
        )
    data.template_rows = pd.DataFrame(rows)
    data.job_package_summary = pd.DataFrame()
    return data


def test_roof_coating_labor_selection_caps_overbroad_historical_buckets() -> None:
    recommendation = estimate_from_field_notes(ROOF_COATING_BASIC_NOTE, {"estimated_sqft": 0}, data=data_with_oversized_roof_labor_rows())
    total_hours = sum(float(row.get("total_hours") or 0) for row in recommendation.labor_plan)
    hours_per_1000 = total_hours / recommendation.parsed_fields["estimated_sqft"] * 1000
    tasks = {row["task"] for row in recommendation.labor_plan}

    assert hours_per_1000 <= 80
    assert not any(row.get("calibration_method") == "rule_based_fallback" for row in recommendation.labor_plan)
    assert "labor_top_coat_granules" not in tasks
    assert "infrared_scan" not in tasks
    assert any(row.get("capped_hours") for row in recommendation.labor_plan)


def test_roof_coating_labor_selection_audits_rejected_trigger_only_buckets() -> None:
    recommendation = estimate_from_field_notes(ROOF_COATING_BASIC_NOTE, {"estimated_sqft": 0}, data=data_with_oversized_roof_labor_rows())
    selection_rows = recommendation.debug["labor_calibration"]["selection_rows"]
    rejected_by_task = {row["task"]: row for row in selection_rows if row.get("selected") is False}

    assert rejected_by_task["infrared_scan"]["labor_bucket_role"] == "trigger_only"
    assert "did not request" in rejected_by_task["infrared_scan"]["reason"].lower()
    assert rejected_by_task["labor_top_coat_granules"]["labor_bucket_role"] == "trigger_only"
    assert "did not request" in rejected_by_task["labor_top_coat_granules"]["reason"].lower()


def test_roof_coating_labor_selection_includes_granules_and_infrared_when_requested() -> None:
    note = (
        "Metal roof 12000 sqft silicone coating in Louisville KY with granules broadcast. "
        "Include infrared moisture scan before coating."
    )
    recommendation = estimate_from_field_notes(note, data=data_with_oversized_roof_labor_rows())
    tasks = {row["task"] for row in recommendation.labor_plan}

    assert "labor_top_coat_granules" in tasks
    assert "infrared_scan" in tasks
