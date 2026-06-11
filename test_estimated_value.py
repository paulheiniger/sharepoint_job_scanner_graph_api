from jobscan.models import JobRecord, get_estimated_value_info
from jobscan.scan import records_as_dicts
from jobscan.zapier_summary_sender import build_summary


def test_get_estimated_value_prefers_customer_facing_amounts() -> None:
    assert get_estimated_value_info({"final_price": "1,200", "worksheet_price": 900, "total_job_cost": 500}) == (1200, "final_price")
    assert get_estimated_value_info({"final_price": "", "worksheet_price": "$900.00", "total_job_cost": 500}) == (900, "worksheet_price")
    assert get_estimated_value_info({"final_price": None, "worksheet_price": None, "total_job_cost": 500}) == (500, "total_job_cost")


def test_records_as_dicts_outputs_estimated_value_debug_fields() -> None:
    record = JobRecord(
        job_id="job-1",
        folder_name="Job One",
        folder_path="Job One",
        worksheet_price=2500,
        total_job_cost=1500,
    )

    row = records_as_dicts([record])[0]

    assert row["estimated_value"] == 2500
    assert row["estimated_value_source"] == "worksheet_price"
    assert row["total_job_cost"] == 1500


def test_daily_summary_uses_estimated_value_for_totals_and_top_jobs() -> None:
    summary = build_summary(
        [
            {
                "job_name": "Cost Only",
                "division": "Roofing",
                "pipeline_status": "Proposed",
                "total_job_cost": 100,
            },
            {
                "job_name": "Worksheet",
                "division": "Roofing",
                "pipeline_status": "Contracted",
                "worksheet_price": 300,
                "total_job_cost": 200,
            },
            {
                "job_name": "Final",
                "division": "Repairs",
                "pipeline_status": "Completed",
                "final_price": 500,
                "worksheet_price": 400,
                "total_job_cost": 250,
            },
        ]
    )

    assert summary["total_estimated_value"] == 900
    assert summary["total_by_division"] == {"Repairs": 500, "Roofing": 400}
    assert summary["total_by_pipeline_status"] == {"Completed": 500, "Contracted": 300, "Proposed": 100}
    assert summary["top_highest_value_jobs"][0]["job_name"] == "Final"
    assert summary["top_highest_value_jobs"][0]["estimated_value_source"] == "final_price"
