CREATE TABLE IF NOT EXISTS job_document_signals (
    job_id TEXT PRIMARY KEY,
    document_substrate TEXT,
    document_material_system TEXT,
    document_warranty_type TEXT,
    document_warranty_years NUMERIC,
    signal_document_count INTEGER DEFAULT 0,
    signal_content_row_count INTEGER DEFAULT 0,
    refreshed_at TIMESTAMPTZ DEFAULT NOW()
);

TRUNCATE TABLE job_document_signals;

INSERT INTO job_document_signals (
    job_id,
    document_substrate,
    document_material_system,
    document_warranty_type,
    document_warranty_years,
    signal_document_count,
    signal_content_row_count,
    refreshed_at
)
WITH content AS (
    SELECT
        d.job_id,
        d.document_id,
        LOWER(COALESCE(d.normalized_text, d.text_content, '')) AS source_text
    FROM document_content d
    WHERE d.job_id IS NOT NULL
      AND COALESCE(d.normalized_text, d.text_content, '') <> ''
),
signals AS (
    SELECT
        job_id,
        document_id,
        CASE
            WHEN source_text LIKE '%metal roof%' OR source_text LIKE '%metal panel%' OR source_text LIKE '%standing seam%' THEN 'Metal'
            WHEN source_text LIKE '%epdm%' THEN 'EPDM'
            WHEN source_text LIKE '%tpo%' THEN 'TPO'
            WHEN source_text LIKE '%concrete%' THEN 'Concrete'
            WHEN source_text LIKE '%spray foam%' OR source_text LIKE '%spf%' THEN 'SPF'
            ELSE NULL
        END AS substrate_signal,
        CASE
            WHEN source_text LIKE '%silicone%' THEN 'Silicone'
            WHEN source_text LIKE '%acrylic%' THEN 'Acrylic'
            WHEN source_text LIKE '%open cell%' OR source_text LIKE '%open-cell%' THEN 'Open-cell spray foam'
            WHEN source_text LIKE '%closed cell%' OR source_text LIKE '%closed-cell%' THEN 'Closed-cell spray foam'
            WHEN source_text LIKE '%spray foam%' OR source_text LIKE '%spf%' THEN 'Spray foam'
            ELSE NULL
        END AS material_signal,
        CASE
            WHEN source_text LIKE '%gaco%warranty%' THEN 'Gaco'
            WHEN source_text LIKE '%spray-tec%warranty%' OR source_text LIKE '%spray tec%warranty%' THEN 'Spray-Tec'
            ELSE NULL
        END AS warranty_type_signal,
        NULLIF(SUBSTRING(source_text FROM '([0-9]{1,2})[ -]?year'), '')::NUMERIC AS warranty_year_signal
    FROM content
    WHERE source_text LIKE '%metal%'
       OR source_text LIKE '%tpo%'
       OR source_text LIKE '%epdm%'
       OR source_text LIKE '%concrete%'
       OR source_text LIKE '%silicone%'
       OR source_text LIKE '%acrylic%'
       OR source_text LIKE '%spray foam%'
       OR source_text LIKE '%closed cell%'
       OR source_text LIKE '%open cell%'
       OR source_text LIKE '%warranty%'
)
SELECT
    job_id,
    STRING_AGG(DISTINCT substrate_signal, ', ' ORDER BY substrate_signal)
        FILTER (WHERE substrate_signal IS NOT NULL) AS document_substrate,
    STRING_AGG(DISTINCT material_signal, ', ' ORDER BY material_signal)
        FILTER (WHERE material_signal IS NOT NULL) AS document_material_system,
    STRING_AGG(DISTINCT warranty_type_signal, ', ' ORDER BY warranty_type_signal)
        FILTER (WHERE warranty_type_signal IS NOT NULL) AS document_warranty_type,
    MAX(warranty_year_signal) AS document_warranty_years,
    COUNT(DISTINCT document_id) AS signal_document_count,
    COUNT(*) AS signal_content_row_count,
    NOW() AS refreshed_at
FROM signals
GROUP BY job_id;

CREATE INDEX IF NOT EXISTS idx_job_document_signals_refreshed_at
    ON job_document_signals(refreshed_at);
