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


def test_job_tracking_material_rollup_collapses_duplicate_job_rows() -> None:
    import dashboard.app as app

    summary = pd.DataFrame(
        [
            {
                "job_id": "ROOF1",
                "project": "LGE Tc Warranty",
                "division": "Roofing",
                "tracking_status": "Recently touched",
                "source_file": "Tracking A.xlsx",
                "actual_base_coat_1": 4,
                "actual_foam_sqft": 100,
                "estimated_base_coat_1": 20,
                "estimated_foam_sqft": 400,
                "estimate_material_rows_used": 8,
            },
            {
                "job_id": "ROOF1",
                "project": "LGE Tc Warranty",
                "division": "Roofing",
                "tracking_status": "Recently touched",
                "source_file": "Tracking B.xlsx",
                "actual_base_coat_1": 6,
                "actual_foam_sqft": 50,
                "estimated_base_coat_1": 20,
                "estimated_foam_sqft": 400,
                "estimate_material_rows_used": 8,
            },
            {
                "job_id": "INS1",
                "project": "Mcdaniel - McDaniel - 224 Hillview Dr.",
                "division": "Insulation",
                "tracking_status": "Recently touched",
                "source_file": "Tracking C.xlsx",
                "actual_foam_sqft": 150,
                "estimated_foam_sqft": 600,
                "estimated_foam_yield": 3500,
            },
            {
                "job_id": "INS1",
                "project": "Mcdaniel - McDaniel - 224 Hillview Dr.",
                "division": "Insulation",
                "tracking_status": "Recently touched",
                "source_file": "Tracking D.xlsx",
                "actual_foam_sqft": 250,
                "estimated_foam_sqft": 600,
                "estimated_foam_yield": 3500,
            },
        ]
    )

    rolled = app.rollup_job_tracking_production_summary(summary)
    roofing_rows, insulation_rows = app.split_tracking_material_rows(rolled)

    assert roofing_rows["project"].tolist() == ["LGE Tc Warranty"]
    assert insulation_rows["project"].tolist() == ["Mcdaniel - McDaniel - 224 Hillview Dr."]

    roof = roofing_rows.iloc[0]
    assert roof["actual_base_coat_1"] == 10
    assert roof["actual_foam_sqft"] == 150
    assert roof["estimated_base_coat_1"] == 20
    assert roof["estimated_foam_sqft"] == 400
    assert roof["base_coat_1_variance"] == -10
    assert roof["foam_sqft_variance"] == -250

    insulation = insulation_rows.iloc[0]
    assert insulation["actual_foam_sqft"] == 400
    assert insulation["estimated_foam_sqft"] == 600
    assert insulation["estimated_foam_yield"] == 3500
    assert insulation["foam_sqft_variance"] == -200


def test_job_tracking_budget_health_estimates_labor_cost_from_hours_without_template_cost(monkeypatch) -> None:
    import dashboard.app as app

    summary = pd.DataFrame(
        [
            {
                "job_id": "JOB1",
                "project": "Tracked labor with no cost baseline",
                "division": "Roofing",
                "tracking_status": "Recently touched",
                "actual_labor_hours": 12,
                "estimated_labor_hours": 10,
            },
            {
                "job_id": "JOB2",
                "project": "Labor rate sample",
                "division": "Roofing",
                "tracking_status": "Recently touched",
                "actual_labor_hours": 5,
                "estimated_labor_hours": 20,
            },
        ]
    )
    budget_enrichment = pd.DataFrame(
        [
            {
                "job_id": "JOB2",
                "budget_bucket": "Labor",
                "estimated_bucket_cost": 2000,
                "estimate_budget_rows_used": 2,
            },
        ]
    )
    monkeypatch.setattr(app, "load_job_tracking_estimate_budget_enrichment", lambda job_ids: budget_enrichment)

    budget_jobs, budget_buckets = app.build_job_tracking_budget_health(summary)

    job = budget_jobs[budget_jobs["job_id"] == "JOB1"].iloc[0]
    assert job["estimated_cost"] == 1000
    assert job["actual_cost"] == 1200
    assert job["budget_status"] == "Over Budget"

    bucket = budget_buckets[(budget_buckets["job_id"] == "JOB1") & (budget_buckets["bucket"] == "Labor")].iloc[0]
    assert bucket["cost_basis"] == "median_labor_hourly_rate"


def test_job_tracking_summary_actuals_are_recovered_from_daily_rows() -> None:
    import dashboard.app as app

    summary = pd.DataFrame(
        [
            {
                "job_id": "WTYOUNG",
                "project": "UK WT Young",
                "division": "Roofing",
                "actual_labor_hours": 77.18,
                "estimated_labor_hours": 5600,
                "actual_foam_strokes": None,
                "actual_foam_sqft": None,
                "estimated_foam_sqft": 36500,
            }
        ]
    )
    daily = pd.DataFrame(
        [
            {
                "job_id": "WTYOUNG",
                "project": "UK WT Young",
                "division": "Roofing",
                "source_file": "Job Tracking - Phase 2 WT Young.xlsx",
                "work_date": "2026-07-09",
                "labor_hours": 47.1,
                "foam_strokes": 1205,
                "foam_sqft": 1350,
                "primer": None,
                "notes": "sprayed foam",
            },
            {
                "job_id": "WTYOUNG",
                "project": "UK WT Young",
                "division": "Roofing",
                "source_file": "Job Tracking - Phase 2 WT Young.xlsx",
                "work_date": "2026-07-14",
                "labor_hours": 45,
                "foam_strokes": 1416,
                "foam_sqft": 1976,
                "primer": 2.5,
                "notes": "sprayed foam and primer",
            },
        ]
    )

    recovered = app.merge_job_tracking_summary_actuals_from_daily(summary, daily)
    row = recovered.iloc[0]

    assert row["actual_labor_hours"] == 92.1
    assert row["actual_foam_strokes"] == 2621
    assert row["actual_foam_sqft"] == 3326
    assert row["actual_primer"] == 2.5
    assert row["foam_sqft_variance"] == -33174


def test_job_tracking_rollup_merges_common_address_abbreviations() -> None:
    import dashboard.app as app

    summary = pd.DataFrame(
        [
            {
                "job_id": "MCDANIEL-224-HILLVIEW-DRIVE",
                "project": "McDaniel - 224 Hillview Dr.",
                "source_file": "Job Tracking Form - McDaniel 224 Hillview Dr. (Phase 1 Attic).xlsx",
                "first_work_date": "2026-07-01",
                "last_work_date": "2026-07-02",
                "actual_labor_hours": 78.03,
                "estimated_foam_sqft": 2181,
            },
            {
                "job_id": "MCDANIEL-224-HILLVIEW-DR",
                "project": "McDaniel - 224 Hillview Dr.",
                "source_file": "Job Tracking Form - McDaniel 224 Hillview Dr. (Phase 1 Attic).xlsx",
                "first_work_date": "2026-07-01",
                "last_work_date": "2026-07-02",
                "actual_labor_hours": 78.03,
                "estimated_foam_sqft": 2181,
            },
            {
                "job_id": "MCDANIEL-224-HILLVIEW-DRIVE",
                "project": "McDaniel - 224 Hillview Dr.",
                "source_file": "Job Tracking Form - McDaniel 224 Hillview Dr. (Ph 1 Attic Va + Ph 2 Walls + Roof).xlsx",
                "first_work_date": "2026-07-03",
                "last_work_date": "2026-07-04",
                "actual_labor_hours": 26.0,
                "estimated_foam_sqft": 3361,
            },
        ]
    )

    rolled = app.rollup_job_tracking_production_summary(summary)

    assert len(rolled) == 1
    row = rolled.iloc[0]
    assert row["job_id"] == "MCDANIEL-224-HILLVIEW-DRIVE"
    assert row["actual_labor_hours"] == 104.03
    assert row["estimated_foam_sqft"] == 2181


def test_job_tracking_rollup_merges_date_suffixed_same_job_ids() -> None:
    import dashboard.app as app

    summary = pd.DataFrame(
        [
            {
                "job_id": "GRAVES-COUNTY-ATHLETIC-MULTIPURPOSE-FACILITY-07-01-26",
                "project": "Graves County Athletic - Athletic Multipurpose Facility-Graves County Highschool",
                "source_file": "UPDATED Job Tracking Form - Graves County Athletic Multipurpose Facility.xlsx",
                "first_work_date": "2026-07-01",
                "last_work_date": "2026-07-02",
                "actual_labor_hours": 843.64,
                "estimated_labor_hours": 205.5,
            },
            {
                "job_id": "GRAVES-COUNTY-ATHLETIC-MULTIPURPOSE-FACILITY",
                "project": "Graves County Athletic - Athletic Multipurpose Facility-Graves County Highschool",
                "source_file": "Job Tracking Form - Graves County Athletic Multipurpose Facility.xlsx",
                "first_work_date": "2026-07-03",
                "last_work_date": "2026-07-04",
                "actual_labor_hours": 821.0,
                "estimated_labor_hours": 205.5,
            },
        ]
    )

    rolled = app.rollup_job_tracking_production_summary(summary)

    assert len(rolled) == 1
    row = rolled.iloc[0]
    assert row["job_id"] == "GRAVES-COUNTY-ATHLETIC-MULTIPURPOSE-FACILITY-07-01-26"
    assert round(row["actual_labor_hours"], 2) == 1664.64
    assert row["estimated_labor_hours"] == 205.5


def test_job_tracking_rollup_merges_etown_variants() -> None:
    import dashboard.app as app

    summary = pd.DataFrame(
        [
            {
                "job_id": "KSP-KENTUCKY-STATE-POLICE-POST-4-E-TOWN",
                "project": "Ksp (Kentucky State - KSP Post 4 Etown HVAC Upgrades",
                "source_file": "Job Tracking Form - KSP Post 4 Etown (Insulation 2026).xlsx",
                "first_work_date": "2026-07-01",
                "last_work_date": "2026-07-02",
                "estimated_labor_hours": 840,
                "estimated_foam_sqft": 23500,
            },
            {
                "job_id": "KSP-KENTUCKY-STATE-POLICE-POST-4-ETOWN",
                "project": "Ksp (Kentucky State - KSP Post 4 Etown HVAC Upgrades",
                "source_file": "Job Tracking Form - KSP Post 4 Etown (Insulation 2026).xlsx",
                "first_work_date": "2026-07-01",
                "last_work_date": "2026-07-02",
                "estimated_labor_hours": 840,
                "estimated_foam_sqft": 23500,
            },
        ]
    )

    rolled = app.rollup_job_tracking_production_summary(summary)

    assert len(rolled) == 1
    row = rolled.iloc[0]
    assert row["job_id"] == "KSP-KENTUCKY-STATE-POLICE-POST-4-E-TOWN"
    assert row["estimated_labor_hours"] == 840
    assert row["estimated_foam_sqft"] == 23500


def test_job_tracking_budget_health_disambiguates_repeated_project_labels(monkeypatch) -> None:
    import dashboard.app as app

    summary = pd.DataFrame(
        [
            {
                "job_id": "SCHAEFER-SERVICE-SOLUTIONS-1900-WATTERSON",
                "project": "Schaefer Service Solutions",
                "folder_path": "Schaefer Service Solutions 1900 Watterson Trail Bldg. Wall Insulation",
                "source_file": "Job Tracking Form - 1900 Watterson Trail Bldg. Insulation.xlsx",
                "estimated_labor_hours": 60,
                "actual_labor_hours": 40,
            },
            {
                "job_id": "SCHAEFER-SERVICE-SOLUTIONS-RYAN-FIRE-PROTECTION",
                "project": "Schaefer Service Solutions",
                "folder_path": "Schaefer Service Solutions Ryan Fire Protection",
                "source_file": "Job Tracking Form - Schaefer - Ryan Fire Protection (2026).xlsx",
                "estimated_labor_hours": 36,
                "actual_labor_hours": 20,
            },
        ]
    )
    monkeypatch.setattr(app, "load_job_tracking_estimate_budget_enrichment", lambda job_ids: pd.DataFrame())

    budget_jobs, budget_buckets = app.build_job_tracking_budget_health(summary)

    assert len(budget_jobs) == 2
    assert budget_jobs["project"].tolist() == ["Schaefer Service Solutions", "Schaefer Service Solutions"]
    assert budget_jobs["job_display"].nunique() == 2
    assert all("Schaefer Service Solutions" in value for value in budget_jobs["job_display"])
    assert budget_buckets["job_display"].nunique() == 2
