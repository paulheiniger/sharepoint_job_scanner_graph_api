BEGIN;

CREATE SCHEMA IF NOT EXISTS analytics;

CREATE TABLE IF NOT EXISTS analytics.estimator_latest_historical_unit_prices (
    historical_price_id TEXT PRIMARY KEY,
    template_type TEXT NOT NULL,
    template_bucket TEXT NOT NULL,
    template_section TEXT,
    line_item_kind TEXT,
    workbook_row INTEGER NOT NULL,
    selector_code NUMERIC,
    item_name TEXT NOT NULL,
    item_name_normalized TEXT NOT NULL,
    unit TEXT,
    unit_price NUMERIC NOT NULL,
    source_document_id TEXT NOT NULL,
    source_job_id TEXT,
    source_file TEXT,
    source_sheet TEXT,
    source_row INTEGER,
    source_sharepoint_url TEXT,
    source_modified_at TIMESTAMPTZ,
    source_year INTEGER,
    source_effective_at TIMESTAMPTZ NOT NULL,
    source_date_basis TEXT NOT NULL,
    usage_evidence TEXT NOT NULL,
    historical_observation_count INTEGER NOT NULL,
    refreshed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TEMP TABLE estimator_latest_historical_unit_prices_refresh
ON COMMIT DROP
AS
WITH used_prices AS (
    SELECT
        t.template_type,
        t.template_bucket,
        t.template_section,
        t.line_item_kind,
        t.row_number AS workbook_row,
        t.selector_code,
        COALESCE(
            NULLIF(BTRIM(t.resolved_item_name), ''),
            NULLIF(BTRIM(t.selected_item_name), ''),
            NULLIF(BTRIM(t.row_label), ''),
            NULLIF(BTRIM(t.template_bucket), '')
        ) AS item_name,
        LOWER(
            REGEXP_REPLACE(
                COALESCE(
                    NULLIF(BTRIM(t.resolved_item_name), ''),
                    NULLIF(BTRIM(t.selected_item_name), ''),
                    NULLIF(BTRIM(t.row_label), ''),
                    NULLIF(BTRIM(t.template_bucket), '')
                ),
                '[^a-z0-9]+',
                ' ',
                'g'
            )
        ) AS item_name_normalized,
        t.unit,
        t.unit_price,
        t.document_id AS source_document_id,
        t.job_id AS source_job_id,
        t.source_file,
        t.sheet_name AS source_sheet,
        t.row_number AS source_row,
        d.sharepoint_url AS source_sharepoint_url,
        d.modified_at AS source_modified_at,
        COALESCE(d.source_year, EXTRACT(YEAR FROM d.modified_at)::INTEGER) AS source_year,
        COALESCE(
            d.modified_at,
            CASE
                WHEN d.source_year BETWEEN 1900 AND 2200
                    THEN MAKE_TIMESTAMPTZ(d.source_year, 12, 31, 23, 59, 59, 'UTC')
                ELSE NULL
            END,
            t.updated_at,
            t.created_at
        ) AS source_effective_at,
        CASE
            WHEN d.modified_at IS NOT NULL THEN 'document_modified_at'
            WHEN d.source_year BETWEEN 1900 AND 2200 THEN 'document_source_year'
            ELSE 'template_row_updated_at'
        END AS source_date_basis,
        CONCAT_WS(
            ', ',
            CASE WHEN COALESCE(t.quantity, 0) > 0 THEN 'quantity' END,
            CASE WHEN COALESCE(t.estimated_units, 0) > 0 THEN 'estimated_units' END,
            CASE WHEN COALESCE(t.estimated_cost, 0) > 0 THEN 'estimated_cost' END,
            CASE WHEN COALESCE(t.calculated_cost, 0) > 0 THEN 'calculated_cost' END,
            CASE WHEN COALESCE(t.area_sqft, 0) > 0 THEN 'area_sqft' END,
            CASE WHEN COALESCE(t.linear_ft, 0) > 0 THEN 'linear_ft' END,
            CASE WHEN COALESCE(t.trips, 0) > 0 THEN 'trips' END,
            CASE WHEN COALESCE(t.days, 0) > 0 THEN 'days' END
        ) AS usage_evidence
    FROM estimate_template_rows t
    LEFT JOIN documents d ON d.document_id = t.document_id
    WHERE t.unit_price > 0
      AND COALESCE(t.template_type, '') <> ''
      AND COALESCE(t.template_bucket, '') <> ''
      AND t.row_number IS NOT NULL
      AND LOWER(COALESCE(t.line_item_kind, '')) IN (
          'material',
          'equipment',
          'travel',
          'adder',
          'pricing'
      )
      AND (
          COALESCE(t.quantity, 0) > 0
          OR COALESCE(t.estimated_units, 0) > 0
          OR COALESCE(t.estimated_cost, 0) > 0
          OR COALESCE(t.calculated_cost, 0) > 0
          OR COALESCE(t.area_sqft, 0) > 0
          OR COALESCE(t.linear_ft, 0) > 0
          OR COALESCE(t.trips, 0) > 0
          OR COALESCE(t.days, 0) > 0
      )
),
ranked AS (
    SELECT
        used_prices.*,
        COUNT(*) OVER (
            PARTITION BY
                template_type,
                template_bucket,
                workbook_row,
                item_name_normalized
        ) AS historical_observation_count,
        ROW_NUMBER() OVER (
            PARTITION BY
                template_type,
                template_bucket,
                workbook_row,
                item_name_normalized
            ORDER BY
                source_effective_at DESC,
                source_document_id DESC
        ) AS recency_rank
    FROM used_prices
)
SELECT
    MD5(
        CONCAT_WS(
            '|',
            template_type,
            template_bucket,
            workbook_row::TEXT,
            item_name_normalized
        )
    ) AS historical_price_id,
    template_type,
    template_bucket,
    template_section,
    line_item_kind,
    workbook_row,
    selector_code,
    item_name,
    item_name_normalized,
    unit,
    unit_price,
    source_document_id,
    source_job_id,
    source_file,
    source_sheet,
    source_row,
    source_sharepoint_url,
    source_modified_at,
    source_year,
    source_effective_at,
    source_date_basis,
    usage_evidence,
    historical_observation_count,
    NOW() AS refreshed_at
FROM ranked
WHERE recency_rank = 1;

TRUNCATE analytics.estimator_latest_historical_unit_prices;

INSERT INTO analytics.estimator_latest_historical_unit_prices (
    historical_price_id,
    template_type,
    template_bucket,
    template_section,
    line_item_kind,
    workbook_row,
    selector_code,
    item_name,
    item_name_normalized,
    unit,
    unit_price,
    source_document_id,
    source_job_id,
    source_file,
    source_sheet,
    source_row,
    source_sharepoint_url,
    source_modified_at,
    source_year,
    source_effective_at,
    source_date_basis,
    usage_evidence,
    historical_observation_count,
    refreshed_at
)
SELECT
    historical_price_id,
    template_type,
    template_bucket,
    template_section,
    line_item_kind,
    workbook_row,
    selector_code,
    item_name,
    item_name_normalized,
    unit,
    unit_price,
    source_document_id,
    source_job_id,
    source_file,
    source_sheet,
    source_row,
    source_sharepoint_url,
    source_modified_at,
    source_year,
    source_effective_at,
    source_date_basis,
    usage_evidence,
    historical_observation_count,
    refreshed_at
FROM estimator_latest_historical_unit_prices_refresh;

CREATE INDEX IF NOT EXISTS idx_estimator_latest_historical_price_template_row
    ON analytics.estimator_latest_historical_unit_prices (
        template_type,
        template_bucket,
        workbook_row
    );

CREATE INDEX IF NOT EXISTS idx_estimator_latest_historical_price_item
    ON analytics.estimator_latest_historical_unit_prices (
        template_type,
        item_name_normalized
    );

COMMIT;
