CREATE SCHEMA IF NOT EXISTS analytics;

CREATE TABLE IF NOT EXISTS analytics.insulation_foam_decision_history (
    decision_id TEXT,
    decision_node_title TEXT,
    decision_category TEXT,
    job_id TEXT,
    source_file TEXT,
    source_year INTEGER,
    division TEXT,
    template_type TEXT,
    project_type TEXT,
    substrate TEXT,
    building_type TEXT,
    coating_type TEXT,
    warranty_years NUMERIC,
    roof_condition TEXT,
    access_complexity TEXT,
    penetrations_complexity TEXT,
    size_bucket TEXT,
    template_row_id TEXT,
    sheet_name TEXT,
    row_number INTEGER,
    template_bucket TEXT,
    line_item_kind TEXT,
    selector_code NUMERIC,
    product_id TEXT,
    product_match_score NUMERIC,
    selected_option TEXT,
    resolved_item_name TEXT,
    area_basis_sqft NUMERIC,
    thickness_inches NUMERIC,
    foam_density_lb NUMERIC,
    yield_or_coverage NUMERIC,
    yield_factor NUMERIC,
    estimated_units NUMERIC,
    estimated_sets NUMERIC,
    gal_per_100_sqft NUMERIC,
    gal_per_sqft NUMERIC,
    wet_mils_estimate NUMERIC,
    waste_factor_pct NUMERIC,
    unit_price NUMERIC,
    days NUMERIC,
    crew_size NUMERIC,
    crew_selector_code NUMERIC,
    total_hours NUMERIC,
    daily_rate NUMERIC,
    hourly_rate NUMERIC,
    formula_mode TEXT,
    equipment_choice TEXT,
    calculated_output NUMERIC,
    estimated_cost NUMERIC,
    source_table TEXT
);

CREATE TABLE IF NOT EXISTS analytics.insulation_thermal_barrier_decision_history
    (LIKE analytics.insulation_foam_decision_history INCLUDING DEFAULTS);

CREATE TABLE IF NOT EXISTS analytics.insulation_labor_decision_history
    (LIKE analytics.insulation_foam_decision_history INCLUDING DEFAULTS);

CREATE TABLE IF NOT EXISTS analytics.roofing_coating_decision_history
    (LIKE analytics.insulation_foam_decision_history INCLUDING DEFAULTS);

CREATE TABLE IF NOT EXISTS analytics.roofing_scope_decision_history
    (LIKE analytics.insulation_foam_decision_history INCLUDING DEFAULTS);

CREATE TABLE IF NOT EXISTS analytics.roofing_labor_decision_history
    (LIKE analytics.insulation_foam_decision_history INCLUDING DEFAULTS);

CREATE TABLE IF NOT EXISTS analytics.equipment_decision_history
    (LIKE analytics.insulation_foam_decision_history INCLUDING DEFAULTS);

CREATE TABLE IF NOT EXISTS analytics.estimator_decision_recommendations (
    decision_id TEXT,
    field_name TEXT,
    recommended_value TEXT,
    evidence_count INTEGER,
    p25 NUMERIC,
    median NUMERIC,
    p75 NUMERIC,
    mode TEXT,
    confidence TEXT,
    review_warning TEXT,
    source_jobs_count INTEGER,
    filters_applied TEXT,
    filters_relaxed TEXT,
    history_table TEXT,
    template_bucket TEXT
);

CREATE INDEX IF NOT EXISTS idx_insulation_foam_decision_history_decision
    ON analytics.insulation_foam_decision_history (decision_id, template_bucket);
CREATE INDEX IF NOT EXISTS idx_insulation_thermal_barrier_decision_history_decision
    ON analytics.insulation_thermal_barrier_decision_history (decision_id, template_bucket);
CREATE INDEX IF NOT EXISTS idx_insulation_labor_decision_history_decision
    ON analytics.insulation_labor_decision_history (decision_id, template_bucket);
CREATE INDEX IF NOT EXISTS idx_roofing_coating_decision_history_decision
    ON analytics.roofing_coating_decision_history (decision_id, template_bucket);
CREATE INDEX IF NOT EXISTS idx_roofing_scope_decision_history_decision
    ON analytics.roofing_scope_decision_history (decision_id, template_bucket);
CREATE INDEX IF NOT EXISTS idx_roofing_labor_decision_history_decision
    ON analytics.roofing_labor_decision_history (decision_id, template_bucket);
CREATE INDEX IF NOT EXISTS idx_equipment_decision_history_decision
    ON analytics.equipment_decision_history (decision_id, template_bucket);
CREATE INDEX IF NOT EXISTS idx_estimator_decision_recommendations_decision
    ON analytics.estimator_decision_recommendations (decision_id, field_name);

CREATE OR REPLACE VIEW analytics.estimator_decision_recommendation_summary AS
SELECT
    decision_id,
    template_bucket,
    history_table,
    COUNT(*) AS recommendation_field_count,
    MAX(evidence_count) AS max_evidence_count,
    MAX(source_jobs_count) AS max_source_jobs_count,
    MAX(confidence) AS confidence,
    STRING_AGG(field_name || '=' || COALESCE(recommended_value, median::text, mode), '; ' ORDER BY field_name) AS recommendation_summary
FROM analytics.estimator_decision_recommendations
GROUP BY decision_id, template_bucket, history_table;
