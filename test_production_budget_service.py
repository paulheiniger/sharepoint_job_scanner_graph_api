from __future__ import annotations

from sqlalchemy import create_engine, text

from jobscan.business.production_budget_service import get_production_budget_health


def production_budget_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE job_board_static_snapshot (
                    job_id TEXT PRIMARY KEY,
                    customer TEXT,
                    job_name TEXT,
                    division TEXT,
                    pipeline_status TEXT,
                    status TEXT,
                    estimated_value NUMERIC,
                    final_price NUMERIC,
                    folder_url TEXT,
                    folder_path TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO job_board_static_snapshot VALUES
                ('JOB-OVER', 'Acme', 'Acme Roof', 'Roofing', 'Contracted',
                 'Active', 150000, 155000, 'https://example.invalid/JOB-OVER', ''),
                ('JOB-OK', 'Beta', 'Beta Plant', 'Insulation', 'Contracted',
                 'Active', 80000, NULL, 'https://example.invalid/JOB-OK', ''),
                ('JOB-NO-ACTUAL', 'Cedar', 'Cedar Shop', 'Roofing', 'Contracted',
                 'Open', 40000, NULL, 'https://example.invalid/JOB-NO-ACTUAL', ''),
                ('JOB-AMBIGUOUS', 'Delta', 'Delta Portfolio', 'Roofing',
                 'Contracted', 'Active', 90000, NULL,
                 'https://example.invalid/JOB-AMBIGUOUS', '')
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE job_tracking_summary (
                    job_id TEXT,
                    tracking_id TEXT,
                    tracking_status TEXT,
                    tracking_file TEXT,
                    source_file TEXT,
                    source_path TEXT,
                    actual_first_work_date DATE,
                    actual_last_work_date DATE,
                    updated_at TIMESTAMP,
                    actual_labor_hours NUMERIC,
                    estimated_labor_hours NUMERIC,
                    actual_foam_sqft NUMERIC,
                    estimated_foam_sqft NUMERIC,
                    actual_foam_strokes NUMERIC,
                    estimated_foam_strokes NUMERIC,
                    actual_foam_lbs NUMERIC,
                    estimated_foam_lbs NUMERIC,
                    actual_base_coat_1 NUMERIC,
                    estimated_base_coat_1 NUMERIC,
                    actual_base_coat_2 NUMERIC,
                    estimated_base_coat_2 NUMERIC,
                    actual_primer NUMERIC,
                    estimated_primer NUMERIC,
                    actual_sf NUMERIC,
                    estimated_sf NUMERIC,
                    actual_caulk NUMERIC,
                    estimated_caulk NUMERIC,
                    actual_af_buttergrade NUMERIC,
                    estimated_af_buttergrade NUMERIC,
                    actual_granules NUMERIC,
                    estimated_granules NUMERIC
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO job_tracking_summary (
                    job_id, tracking_id, tracking_status, tracking_file,
                    source_file, source_path, actual_first_work_date,
                    actual_last_work_date, updated_at, actual_labor_hours,
                    estimated_labor_hours, actual_foam_sqft, estimated_foam_sqft
                ) VALUES
                ('JOB-OVER', 'T1', 'Recently touched', 'over.xlsx', 'over.xlsx',
                 '', '2026-07-01', '2026-07-29', '2026-07-30', 120, 100,
                 1100, 1000),
                ('JOB-OK', 'T2', 'Recently touched', 'ok.xlsx', 'ok.xlsx',
                 '', '2026-07-02', '2026-07-28', '2026-07-30', 50, 100,
                 NULL, NULL),
                ('JOB-NO-ACTUAL', 'T3', 'Awaiting actuals', 'none.xlsx',
                 'none.xlsx', '', NULL, NULL, '2026-07-30', NULL, 80,
                 NULL, NULL)
                """
            )
        )
        for index in range(11):
            connection.execute(
                text(
                    """
                    INSERT INTO job_tracking_summary (
                        job_id, tracking_id, tracking_status, tracking_file,
                        source_file, source_path, actual_first_work_date,
                        actual_last_work_date, updated_at, actual_labor_hours,
                        estimated_labor_hours
                    ) VALUES (
                        'JOB-AMBIGUOUS', :tracking_id, 'Recently touched',
                        :tracking_file, :tracking_file, '', '2026-07-01',
                        '2026-07-29', '2026-07-30', 10, 10
                    )
                    """
                ),
                {
                    "tracking_id": f"T-AMB-{index}",
                    "tracking_file": f"nested-job-{index}.xlsx",
                },
            )
        connection.execute(
            text(
                """
                CREATE TABLE job_tracking_estimate_budget_snapshot (
                    job_id TEXT,
                    budget_bucket TEXT,
                    estimated_bucket_cost NUMERIC,
                    estimate_budget_rows_used NUMERIC,
                    refreshed_at TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO job_tracking_estimate_budget_snapshot VALUES
                ('JOB-OVER', 'Labor', 10000, 1, '2026-07-30'),
                ('JOB-OVER', 'Foam / SPF', 20000, 2, '2026-07-30'),
                ('JOB-OK', 'Labor', 10000, 1, '2026-07-30'),
                ('JOB-NO-ACTUAL', 'Labor', 8000, 1, '2026-07-30'),
                ('JOB-AMBIGUOUS', 'Labor', 10000, 1, '2026-07-30')
                """
            )
        )
    return engine


def test_production_budget_health_calculates_estimate_rate_usage_proxy() -> None:
    result = get_production_budget_health(
        engine=production_budget_engine(),
        include_no_actuals=True,
        limit=10,
    )

    assert result["schema_version"] == "spraytec.production_budget_health.v1"
    assert result["truth_class"] == "proxy"
    assert result["headline_metrics"]["jobs_with_budget_signal"] == 3
    assert result["headline_metrics"]["jobs_usage_over_plan"] == 1
    assert result["portfolio_rankings"]["strongest_cost_position"][0]["job_id"] == "JOB-OK"
    assert result["portfolio_rankings"]["weakest_cost_position"][0]["job_id"] == "JOB-OVER"
    over = next(row for row in result["records"] if row["job_id"] == "JOB-OVER")
    assert over["estimated_production_budget"] == 30000
    assert over["estimated_cost_used_proxy"] == 34000
    assert over["estimated_cost_variance_proxy"] == 4000
    assert over["budget_used_pct"] == 34000 / 30000
    assert over["budget_status"] == "Usage Over Plan"
    assert over["usage_over_plan_buckets"] == ["Labor", "Foam / SPF"]
    assert any("not accounting actual costs" in warning for warning in result["warnings"])


def test_production_budget_health_filters_and_excludes_ambiguous_tracking_ids() -> None:
    result = get_production_budget_health(
        engine=production_budget_engine(),
        division="Roofing",
        over_plan_only=True,
        limit=10,
    )

    assert [row["job_id"] for row in result["records"]] == ["JOB-OVER"]
    assert result["coverage"]["ambiguous_tracking_job_ids_excluded"] == 1
    assert result["coverage"]["ambiguous_tracking_job_id_sample"] == [
        "JOB-AMBIGUOUS"
    ]


def test_production_budget_health_omits_no_actual_jobs_by_default() -> None:
    result = get_production_budget_health(
        engine=production_budget_engine(),
        limit=10,
    )

    assert {row["job_id"] for row in result["records"]} == {
        "JOB-OVER",
        "JOB-OK",
    }
    assert result["coverage"]["jobs_without_comparable_actuals"] == 1


def test_mixed_unit_bucket_is_never_valued_as_one_quantity() -> None:
    engine = production_budget_engine()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE job_tracking_summary
                SET actual_primer = 10, estimated_primer = 8,
                    actual_caulk = 4, estimated_caulk = 5
                WHERE job_id = 'JOB-OVER'
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO job_tracking_estimate_budget_snapshot VALUES
                ('JOB-OVER', 'Primer / Sealants', 5000, 2, '2026-07-30')
                """
            )
        )

    result = get_production_budget_health(engine=engine, job_ids=["JOB-OVER"])
    bucket = next(
        row
        for row in result["bucket_details"]
        if row["bucket"] == "Primer / Sealants"
    )

    assert bucket["budget_status"] == "Mixed Units / Review"
    assert bucket["estimated_cost_used_proxy"] is None
    assert bucket["comparable_for_cost_proxy"] is False


def test_foam_strokes_are_converted_to_pounds_for_like_unit_comparison() -> None:
    engine = production_budget_engine()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO job_board_static_snapshot VALUES
                ('JOB-FOAM', 'Foam Co', 'Foam Roof', 'Roofing', 'Contracted',
                 'Active', 75000, NULL, 'https://example.invalid/JOB-FOAM', '')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO job_tracking_summary (
                    job_id, tracking_id, tracking_status, tracking_file,
                    source_file, source_path, actual_first_work_date,
                    actual_last_work_date, updated_at, actual_foam_sqft,
                    estimated_foam_sqft, actual_foam_strokes,
                    estimated_foam_lbs
                ) VALUES (
                    'JOB-FOAM', 'T-FOAM', 'Recently touched', 'foam.xlsx',
                    'foam.xlsx', '', '2026-07-01', '2026-07-30',
                    '2026-07-30', NULL, 11000, 11350, 16100
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO job_tracking_estimate_budget_snapshot VALUES
                ('JOB-FOAM', 'Foam / SPF', 20000, 2, '2026-07-30')
                """
            )
        )

    result = get_production_budget_health(engine=engine, job_ids=["JOB-FOAM"])
    bucket = next(
        row
        for row in result["bucket_details"]
        if row["bucket"] == "Foam / SPF"
    )

    assert bucket["quantity_unit"] == "foam lbs"
    assert bucket["actual_quantity"] == 7093.75
    assert bucket["estimated_quantity"] == 16100
    assert bucket["quantity_pct_used"] == 7093.75 / 16100
    assert bucket["budget_used_pct"] == 7093.75 / 16100
    assert bucket["quantity_derivations"] == [
        {
            "field": "actual_foam_lbs",
            "source_field": "actual_foam_strokes",
            "formula": "strokes * 0.625 lb/stroke",
        },
        {
            "field": "estimated_foam_strokes",
            "source_field": "estimated_foam_lbs",
            "formula": "pounds / 0.625 lb/stroke",
        },
    ]


def test_foam_comparison_does_not_pair_strokes_with_square_feet() -> None:
    engine = production_budget_engine()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE job_tracking_summary
                SET actual_foam_strokes = 1000, estimated_foam_sqft = 5000
                WHERE job_id = 'JOB-OK'
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO job_tracking_estimate_budget_snapshot VALUES
                ('JOB-OK', 'Foam / SPF', 20000, 1, '2026-07-30')
                """
            )
        )

    result = get_production_budget_health(engine=engine, job_ids=["JOB-OK"])
    bucket = next(
        row
        for row in result["bucket_details"]
        if row["bucket"] == "Foam / SPF"
    )

    assert bucket["actual_quantity"] == 625
    assert bucket["estimated_quantity"] is None
    assert bucket["quantity_unit"] == "foam lbs"
    assert bucket["comparable_for_cost_proxy"] is False
    assert bucket["budget_status"] == "Incomplete Quantity Baseline"


def test_job_id_variants_and_change_order_tracking_are_not_double_counted() -> None:
    engine = production_budget_engine()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO job_board_static_snapshot VALUES
                ('JOB-VARIANT', 'Echo', 'Echo Roof', 'Roofing', 'Contracted',
                 'Active', 50000, NULL, 'https://example.invalid/JOB-VARIANT', '')
                """
            )
        )
        for job_id, source_file, actual, estimated in (
            ("JOB-VARIANT", "Job Tracking Form - Echo.xlsx", 100, 100),
            (
                "JOB-VARIANT-07-01-26",
                "Job Tracking Form (+ CO1) - Echo.xlsx",
                120,
                100,
            ),
            (
                "JOB-VARIANT-07-01-26",
                "Job Tracking Form - Echo.xlsx",
                100,
                100,
            ),
        ):
            connection.execute(
                text(
                    """
                    INSERT INTO job_tracking_summary (
                        job_id, tracking_id, tracking_status, tracking_file,
                        source_file, source_path, actual_first_work_date,
                        actual_last_work_date, updated_at, actual_labor_hours,
                        estimated_labor_hours
                    ) VALUES (
                        :job_id, :source_file, 'Recently touched', :source_file,
                        :source_file, '', '2026-07-01', '2026-07-29',
                        '2026-07-30', :actual, :estimated
                    )
                    """
                ),
                {
                    "job_id": job_id,
                    "source_file": source_file,
                    "actual": actual,
                    "estimated": estimated,
                },
            )
        connection.execute(
            text(
                """
                INSERT INTO job_tracking_estimate_budget_snapshot VALUES
                ('JOB-VARIANT', 'Labor', 10000, 1, '2026-07-30')
                """
            )
        )

    result = get_production_budget_health(
        engine=engine,
        job_ids=["JOB-VARIANT-07-01-26"],
        limit=10,
    )

    assert len(result["records"]) == 1
    assert result["records"][0]["job_id"] == "JOB-VARIANT"
    assert result["records"][0]["estimated_cost_used_proxy"] == 12000
    assert result["records"][0]["budget_used_pct"] == 1.2
