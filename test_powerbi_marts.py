from pathlib import Path

from jobscan.analytics import powerbi_marts


EXPECTED_MARTS = {
    "mart_jobs",
    "mart_documents",
    "mart_estimate_template_rows",
    "mart_unknown_template_rows",
    "mart_material_history",
    "mart_labor_history",
    "mart_material_defaults",
    "mart_labor_defaults",
    "mart_pricing_catalog",
    "mart_repairs",
    "mart_repair_materials",
    "mart_repair_labor",
    "mart_repair_defaults",
    "mart_quality_warnings",
    "mart_timesheets",
    "mart_estimator_feedback",
    "mart_rule_candidates",
}


def test_powerbi_mart_names_are_declared():
    assert set(powerbi_marts.MART_NAMES) == EXPECTED_MARTS


def test_powerbi_mart_sql_contains_all_marts_and_semantic_notes():
    sql = Path("db/powerbi_marts.sql").read_text(encoding="utf-8")
    assert "CREATE SCHEMA IF NOT EXISTS analytics" in sql
    assert "analytics.semantic_model_notes" in sql
    for mart in EXPECTED_MARTS:
        assert mart in sql


def test_key_mart_columns_are_declared():
    assert {"job_id", "division", "customer", "final_price"}.issubset(powerbi_marts.KEY_COLUMNS["mart_jobs"])
    assert {"document_id", "file_name", "extraction_status"}.issubset(
        powerbi_marts.KEY_COLUMNS["mart_documents"]
    )
    assert {"package", "median_qty_per_sqft", "job_count"}.issubset(
        powerbi_marts.KEY_COLUMNS["mart_material_defaults"]
    )
    assert {"median_hours_per_1000_sqft", "job_count"}.issubset(
        powerbi_marts.KEY_COLUMNS["mart_labor_defaults"]
    )


def test_select_limit_queries_cover_every_mart():
    queries = powerbi_marts.select_limit_queries()
    assert set(queries) == EXPECTED_MARTS
    assert queries["mart_jobs"] == 'SELECT * FROM analytics."mart_jobs" LIMIT 1'


def test_powerbi_sql_grants_read_only_role_without_raw_schema_grants():
    sql = Path("db/powerbi_marts.sql").read_text(encoding="utf-8")
    assert "powerbi_reader" in sql
    assert "GRANT USAGE ON SCHEMA analytics TO powerbi_reader" in sql
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO powerbi_reader" in sql
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA public TO powerbi_reader" not in sql
    assert "GRANT ALL" not in sql.upper()


def test_powerbi_sql_uses_safe_date_and_timestamp_casts():
    sql = Path("db/powerbi_marts.sql").read_text(encoding="utf-8")
    assert "NULLIF(TRIM(%1$I.%2$I::text)" in sql
    assert "IS NULL THEN NULL" in sql
    assert "^[0-9]{4}-[0-9]{2}-[0-9]{2}" in sql
    assert "^[0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4}" in sql
    assert "r.created_date::date" not in sql
    assert "r.completion_date::date" not in sql


def test_repair_compatibility_aliases_are_declared_and_validated():
    sql = Path("db/powerbi_marts.sql").read_text(encoding="utf-8")
    assert "analytics.mart_repair_jobs" in sql
    assert "analytics.mart_repair_material_usage" in sql
    assert "analytics.mart_repair_labor_usage" in sql
    assert "mart_repair_jobs" in powerbi_marts.VALIDATION_MARTS
    assert "mart_repair_material_usage" in powerbi_marts.VALIDATION_MARTS
    assert "mart_repair_labor_usage" in powerbi_marts.VALIDATION_MARTS
