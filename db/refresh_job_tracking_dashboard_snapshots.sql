DROP TABLE IF EXISTS job_tracking_estimated_material_snapshot;

CREATE TABLE job_tracking_estimated_material_snapshot AS
WITH source_rows AS (
    SELECT
        job_id,
        row_number,
        LOWER(
            CONCAT_WS(
                ' ',
                template_bucket,
                original_template_bucket,
                row_label,
                selected_item_name
            )
        ) AS material_text,
        area_sqft,
        thickness_inches,
        COALESCE(NULLIF(yield_or_coverage, 0), NULLIF(yield_factor, 0)) AS foam_yield,
        estimated_cost,
        CASE
            WHEN estimated_gallons > 0 THEN estimated_gallons
            WHEN estimated_units > 0 THEN estimated_units
            WHEN quantity > 0 THEN quantity
            WHEN estimated_sets > 0 THEN estimated_sets
            ELSE NULL
        END AS estimate_quantity
    FROM estimate_template_rows
    WHERE job_id IS NOT NULL
),
classified AS (
    SELECT
        *,
        material_text ~ '(foam|open cell|closed cell|spf|spray foam)' AS is_foam,
        material_text ~ '(primer|e-?5320|mel-?prime)' AS is_primer,
        material_text ~ 'granule' AS is_granules,
        material_text ~ '(sf-?2000|sf ?2000|s2000|liquid flashing)' AS is_sf,
        material_text ~ '(buttergrade|af butter)' AS is_af_buttergrade,
        material_text ~ '(caulk|sausage|sealant)' AS is_caulk_raw
    FROM source_rows
),
material_rows AS (
    SELECT
        *,
        (is_caulk_raw AND NOT is_sf) AS is_caulk,
        (
            material_text ~ '(coating|silicone|acrylic|base coat|top coat|gaco s20|s2000|s2022)'
            AND NOT is_primer
            AND NOT is_granules
            AND NOT is_sf
            AND NOT is_caulk_raw
            AND NOT is_foam
        ) AS is_coating
    FROM classified
),
active_rows AS (
    SELECT *
    FROM material_rows
    WHERE
        COALESCE(estimated_cost, 0) > 0
        OR COALESCE(estimate_quantity, 0) > 0
        OR (
            is_foam
            AND COALESCE(area_sqft, 0) > 0
            AND COALESCE(thickness_inches, 0) > 0
        )
),
coating_rows AS (
    SELECT
        *,
        material_text ~ '(base coat|basecoat|1st coat|first coat)' AS explicit_base,
        material_text ~ '(top coat|topcoat|2nd coat|second coat|finish coat)' AS explicit_top,
        COUNT(*) FILTER (WHERE material_text ~ '(base coat|basecoat|1st coat|first coat)') OVER (PARTITION BY job_id) AS base_count,
        COUNT(*) FILTER (WHERE material_text ~ '(top coat|topcoat|2nd coat|second coat|finish coat)') OVER (PARTITION BY job_id) AS top_count,
        ROW_NUMBER() OVER (PARTITION BY job_id ORDER BY COALESCE(row_number, 999999), material_text) AS coating_order
    FROM active_rows
    WHERE is_coating
),
coating_rollup AS (
    SELECT
        job_id,
        SUM(
            CASE
                WHEN explicit_base THEN COALESCE(estimate_quantity, 0)
                WHEN NOT explicit_top AND base_count = 0 AND top_count = 0 AND coating_order = 1 THEN COALESCE(estimate_quantity, 0)
                WHEN NOT explicit_top AND base_count = 0 THEN COALESCE(estimate_quantity, 0)
                ELSE 0
            END
        ) AS estimated_base_coat_1_from_estimate_rows,
        SUM(
            CASE
                WHEN explicit_top THEN COALESCE(estimate_quantity, 0)
                WHEN NOT explicit_base AND NOT explicit_top AND base_count = 0 AND top_count = 0 AND coating_order > 1 THEN COALESCE(estimate_quantity, 0)
                WHEN NOT explicit_base AND NOT explicit_top AND base_count > 0 THEN COALESCE(estimate_quantity, 0)
                ELSE 0
            END
        ) AS estimated_base_coat_2_from_estimate_rows
    FROM coating_rows
    GROUP BY job_id
),
material_rollup AS (
    SELECT
        job_id,
        COUNT(*) FILTER (WHERE is_foam OR is_primer OR is_granules OR is_sf OR is_af_buttergrade OR is_caulk OR is_coating) AS estimate_material_rows_used,
        SUM(area_sqft) FILTER (WHERE is_foam AND COALESCE(area_sqft, 0) > 0) AS estimated_foam_sqft_from_estimate_rows,
        CASE
            WHEN SUM(area_sqft) FILTER (WHERE is_foam AND COALESCE(area_sqft, 0) > 0 AND COALESCE(thickness_inches, 0) > 0) > 0
            THEN
                SUM(area_sqft * thickness_inches) FILTER (WHERE is_foam AND COALESCE(area_sqft, 0) > 0 AND COALESCE(thickness_inches, 0) > 0)
                / NULLIF(SUM(area_sqft) FILTER (WHERE is_foam AND COALESCE(area_sqft, 0) > 0 AND COALESCE(thickness_inches, 0) > 0), 0)
            ELSE AVG(thickness_inches) FILTER (WHERE is_foam AND COALESCE(thickness_inches, 0) > 0)
        END AS estimated_foam_thickness_inches_from_estimate_rows,
        AVG(foam_yield) FILTER (WHERE is_foam AND COALESCE(foam_yield, 0) > 0) AS estimated_foam_yield_from_estimate_rows,
        SUM(estimate_quantity) FILTER (WHERE is_granules AND COALESCE(estimate_quantity, 0) > 0) AS estimated_granules_from_estimate_rows,
        SUM(estimate_quantity) FILTER (WHERE is_af_buttergrade AND COALESCE(estimate_quantity, 0) > 0) AS estimated_af_buttergrade_from_estimate_rows,
        SUM(estimate_quantity) FILTER (WHERE is_caulk AND COALESCE(estimate_quantity, 0) > 0) AS estimated_caulk_from_estimate_rows,
        SUM(estimate_quantity) FILTER (WHERE is_primer AND COALESCE(estimate_quantity, 0) > 0) AS estimated_primer_from_estimate_rows,
        SUM(estimate_quantity) FILTER (WHERE is_sf AND COALESCE(estimate_quantity, 0) > 0) AS estimated_sf_from_estimate_rows
    FROM active_rows
    GROUP BY job_id
)
SELECT
    m.job_id,
    m.estimate_material_rows_used,
    'job_tracking_estimated_material_snapshot'::TEXT AS estimated_materials_source,
    m.estimated_foam_sqft_from_estimate_rows,
    m.estimated_foam_thickness_inches_from_estimate_rows,
    m.estimated_foam_yield_from_estimate_rows,
    NULLIF(c.estimated_base_coat_1_from_estimate_rows, 0) AS estimated_base_coat_1_from_estimate_rows,
    NULLIF(c.estimated_base_coat_2_from_estimate_rows, 0) AS estimated_base_coat_2_from_estimate_rows,
    m.estimated_granules_from_estimate_rows,
    m.estimated_af_buttergrade_from_estimate_rows,
    m.estimated_caulk_from_estimate_rows,
    m.estimated_primer_from_estimate_rows,
    m.estimated_sf_from_estimate_rows,
    now() AS refreshed_at
FROM material_rollup m
LEFT JOIN coating_rollup c ON c.job_id = m.job_id
WHERE
    COALESCE(m.estimate_material_rows_used, 0) > 0
    OR COALESCE(m.estimated_foam_sqft_from_estimate_rows, 0) > 0
    OR COALESCE(c.estimated_base_coat_1_from_estimate_rows, 0) > 0
    OR COALESCE(c.estimated_base_coat_2_from_estimate_rows, 0) > 0;

CREATE UNIQUE INDEX idx_job_tracking_estimated_material_snapshot_job_id
    ON job_tracking_estimated_material_snapshot(job_id);

CREATE INDEX idx_job_tracking_estimated_material_snapshot_refreshed_at
    ON job_tracking_estimated_material_snapshot(refreshed_at);

DROP TABLE IF EXISTS job_tracking_estimate_budget_snapshot;

CREATE TABLE job_tracking_estimate_budget_snapshot AS
WITH source_rows AS (
    SELECT
        job_id,
        LOWER(CONCAT_WS(' ', template_bucket, original_template_bucket)) AS bucket_text,
        LOWER(CONCAT_WS(' ', row_label, selected_item_name)) AS label_text,
        estimated_cost,
        days,
        total_hours,
        hourly_rate,
        daily_rate
    FROM estimate_template_rows
    WHERE job_id IS NOT NULL
),
classified AS (
    SELECT
        *,
        CONCAT_WS(' ', bucket_text, label_text) AS source_text,
        CASE
            WHEN bucket_text LIKE '%labor%' THEN 'Labor'
            WHEN CONCAT_WS(' ', bucket_text, label_text) ~ '(foam|spf|open cell|closed cell)' THEN 'Foam / SPF'
            WHEN CONCAT_WS(' ', bucket_text, label_text) ~ '(coating|silicone|acrylic|base coat|top coat|u91|u92)' THEN 'Coating'
            WHEN CONCAT_WS(' ', bucket_text, label_text) ~ '(primer|caulk|sealant|sausage|sf|s2000|sf-2000|buttergrade|membrane)' THEN 'Primer / Sealants'
            WHEN CONCAT_WS(' ', bucket_text, label_text) ~ 'granule' THEN 'Granules'
            WHEN CONCAT_WS(' ', bucket_text, label_text) ~ '(board|densdeck|iso|fastener|plate)' THEN 'Board / Fasteners / Plates'
            WHEN CONCAT_WS(' ', bucket_text, label_text) ~ '(travel|loading|truck|sales|inspect|generator|lodging|meal|lift)' THEN 'Equipment / Travel / Lodging'
            WHEN label_text ~ '(set up|setup|prep|power wash|p wash|pwash|tear|spray labor|prime labor|top coat labor|clean up|cleanup)' THEN 'Labor'
            ELSE ''
        END AS budget_bucket
    FROM source_rows
),
costed AS (
    SELECT
        job_id,
        budget_bucket,
        CASE
            WHEN COALESCE(estimated_cost, 0) > 0 THEN estimated_cost
            WHEN budget_bucket = 'Labor' AND COALESCE(days, 0) > 0 AND COALESCE(daily_rate, 0) > 0 THEN days * daily_rate
            WHEN budget_bucket = 'Labor' AND COALESCE(total_hours, 0) > 0 AND COALESCE(hourly_rate, 0) > 0 THEN total_hours * hourly_rate
            ELSE NULL
        END AS budget_cost
    FROM classified
    WHERE budget_bucket <> ''
)
SELECT
    job_id,
    budget_bucket,
    SUM(budget_cost) AS estimated_bucket_cost,
    COUNT(*) AS estimate_budget_rows_used,
    now() AS refreshed_at
FROM costed
WHERE COALESCE(budget_cost, 0) > 0
GROUP BY job_id, budget_bucket;

CREATE UNIQUE INDEX idx_job_tracking_estimate_budget_snapshot_job_bucket
    ON job_tracking_estimate_budget_snapshot(job_id, budget_bucket);

CREATE INDEX idx_job_tracking_estimate_budget_snapshot_refreshed_at
    ON job_tracking_estimate_budget_snapshot(refreshed_at);
