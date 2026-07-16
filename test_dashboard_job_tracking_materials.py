import pandas as pd


def test_job_tracking_material_enrichment_and_split(monkeypatch) -> None:
    import dashboard.app as app

    summary = pd.DataFrame(
        [
            {
                "job_id": "ROOF1",
                "division": "Roofing",
                "project": "Roofing foam and coating",
                "actual_foam_sqft": 250,
                "actual_base_coat_1": 8,
                "estimated_foam_sqft": None,
                "estimated_base_coat_1": None,
                "estimated_base_coat_2": None,
            },
            {
                "job_id": "INS1",
                "division": "Insulation",
                "project": "Insulation foam",
                "actual_foam_sqft": 400,
                "estimated_foam_sqft": None,
            },
        ]
    )
    enrichment = pd.DataFrame(
        [
            {
                "job_id": "ROOF1",
                "estimated_materials_source": "estimate_template_rows",
                "estimate_material_rows_used": 4,
                "estimated_foam_sqft_from_estimate_rows": 1000,
                "estimated_foam_yield_from_estimate_rows": 3000,
                "estimated_base_coat_1_from_estimate_rows": 10,
                "estimated_base_coat_2_from_estimate_rows": 20,
                "estimated_primer_from_estimate_rows": 5,
            },
            {
                "job_id": "INS1",
                "estimated_materials_source": "estimate_template_rows",
                "estimate_material_rows_used": 1,
                "estimated_foam_sqft_from_estimate_rows": 1200,
                "estimated_foam_yield_from_estimate_rows": 3500,
            },
        ]
    )
    monkeypatch.setattr(app, "load_job_tracking_estimated_material_enrichment", lambda job_ids: enrichment)

    enriched = app.enrich_job_tracking_summary_with_estimated_materials(summary)

    roof = enriched[enriched["job_id"] == "ROOF1"].iloc[0]
    assert roof["estimated_foam_sqft"] == 1000
    assert roof["estimated_base_coat_1"] == 10
    assert roof["estimated_base_coat_2"] == 20
    assert roof["estimated_primer"] == 5
    assert roof["foam_sqft_variance"] == -750
    assert roof["base_coat_1_variance"] == -2

    insulation = enriched[enriched["job_id"] == "INS1"].iloc[0]
    assert insulation["estimated_foam_sqft"] == 1200
    assert insulation["estimated_foam_yield"] == 3500

    roofing_rows, insulation_rows = app.split_tracking_material_rows(enriched)

    assert roofing_rows["job_id"].tolist() == ["ROOF1"]
    assert insulation_rows["job_id"].tolist() == ["INS1"]
    assert "estimated_foam_sqft" in roofing_rows.columns
    assert "estimated_foam_sqft" in insulation_rows.columns


def test_job_tracking_budget_health_uses_estimate_cost_baselines(monkeypatch) -> None:
    import dashboard.app as app

    summary = pd.DataFrame(
        [
            {
                "job_id": "ROOF1",
                "project": "Roof coating and foam repair",
                "division": "Roofing",
                "tracking_status": "Recently touched",
                "actual_labor_hours": 12,
                "estimated_labor_hours": 10,
                "actual_foam_sqft": 80,
                "estimated_foam_sqft": 100,
                "actual_base_coat_1": 15,
                "estimated_base_coat_1": 10,
                "estimated_value": 10000,
            },
            {
                "job_id": "INS1",
                "project": "Insulation foam",
                "division": "Insulation",
                "tracking_status": "Recently touched",
                "actual_labor_hours": 4,
                "estimated_labor_hours": 8,
                "actual_foam_sqft": 50,
                "estimated_foam_sqft": 100,
                "estimated_value": 5000,
            },
        ]
    )
    budget_enrichment = pd.DataFrame(
        [
            {
                "job_id": "ROOF1",
                "budget_bucket": "Labor",
                "estimated_bucket_cost": 1000,
                "estimate_budget_rows_used": 3,
            },
            {
                "job_id": "ROOF1",
                "budget_bucket": "Foam / SPF",
                "estimated_bucket_cost": 500,
                "estimate_budget_rows_used": 1,
            },
            {
                "job_id": "ROOF1",
                "budget_bucket": "Coating",
                "estimated_bucket_cost": 300,
                "estimate_budget_rows_used": 1,
            },
            {
                "job_id": "INS1",
                "budget_bucket": "Labor",
                "estimated_bucket_cost": 800,
                "estimate_budget_rows_used": 2,
            },
            {
                "job_id": "INS1",
                "budget_bucket": "Foam / SPF",
                "estimated_bucket_cost": 1200,
                "estimate_budget_rows_used": 1,
            },
        ]
    )
    monkeypatch.setattr(app, "load_job_tracking_estimate_budget_enrichment", lambda job_ids: budget_enrichment)

    budget_jobs, budget_buckets = app.build_job_tracking_budget_health(summary)

    roof_job = budget_jobs[budget_jobs["job_id"] == "ROOF1"].iloc[0]
    assert roof_job["budget_status"] == "Over Budget"
    assert roof_job["estimated_cost"] == 1800
    assert roof_job["actual_cost"] == 2050
    assert roof_job["budget_variance"] == 250
    assert roof_job["over_budget_buckets"] == 2

    roof_buckets = budget_buckets[budget_buckets["job_id"] == "ROOF1"].set_index("bucket")
    assert roof_buckets.loc["Labor", "actual_cost"] == 1200
    assert roof_buckets.loc["Labor", "budget_status"] == "Over Budget"
    assert roof_buckets.loc["Foam / SPF", "actual_cost"] == 400
    assert roof_buckets.loc["Coating", "actual_cost"] == 450
    assert roof_buckets.loc["Coating", "budget_status"] == "Over Budget"

    insulation_job = budget_jobs[budget_jobs["job_id"] == "INS1"].iloc[0]
    assert insulation_job["budget_status"] == "On Track"
    assert insulation_job["actual_cost"] == 1000
    assert insulation_job["estimated_cost"] == 2000
