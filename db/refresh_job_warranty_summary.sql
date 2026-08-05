CREATE TABLE IF NOT EXISTS job_warranty_evidence (
    warranty_evidence_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    source_year INTEGER,
    division TEXT,
    warranty_status TEXT NOT NULL,
    warranty_category TEXT NOT NULL,
    warranty_type TEXT,
    provider TEXT,
    duration_years NUMERIC,
    coverage_summary TEXT,
    coverage_excerpt TEXT,
    explicit_start_date DATE,
    source_kind TEXT NOT NULL,
    source_document_id TEXT,
    source_file TEXT,
    source_url TEXT,
    source_locator TEXT,
    source_modified_at TIMESTAMPTZ,
    extraction_method TEXT NOT NULL,
    extraction_confidence TEXT NOT NULL,
    refreshed_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS job_warranty_summary (
    warranty_summary_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    source_year INTEGER,
    division TEXT,
    customer TEXT,
    job_name TEXT,
    warranty_status TEXT NOT NULL,
    warranty_category TEXT NOT NULL,
    warranty_type TEXT,
    provider TEXT,
    duration_years NUMERIC,
    coverage_summary TEXT,
    coverage_excerpt TEXT,
    start_date DATE,
    start_date_source TEXT,
    start_date_confidence TEXT,
    start_date_is_inferred BOOLEAN NOT NULL DEFAULT TRUE,
    expiration_date DATE,
    source_document_id TEXT,
    source_file TEXT,
    source_url TEXT,
    duration_source_kind TEXT,
    duration_source_document_id TEXT,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    issued_evidence_count INTEGER NOT NULL DEFAULT 0,
    reported_evidence_count INTEGER NOT NULL DEFAULT 0,
    proposed_evidence_count INTEGER NOT NULL DEFAULT 0,
    conflicting_duration_count INTEGER NOT NULL DEFAULT 0,
    has_conflict BOOLEAN NOT NULL DEFAULT FALSE,
    refreshed_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE job_warranty_summary
    ADD COLUMN IF NOT EXISTS reported_evidence_count INTEGER NOT NULL DEFAULT 0;

TRUNCATE TABLE job_warranty_evidence;

INSERT INTO job_warranty_evidence (
    warranty_evidence_id,
    job_id,
    source_year,
    division,
    warranty_status,
    warranty_category,
    warranty_type,
    provider,
    duration_years,
    coverage_summary,
    coverage_excerpt,
    explicit_start_date,
    source_kind,
    source_document_id,
    source_file,
    source_url,
    source_locator,
    source_modified_at,
    extraction_method,
    extraction_confidence,
    refreshed_at
)
WITH document_text AS (
    SELECT
        d.document_id,
        d.job_id,
        d.document_type,
        d.file_name,
        d.sharepoint_url,
        d.modified_at,
        d.file_extension,
        LOWER(COALESCE(d.document_type, '') || ' ' || COALESCE(d.file_name, '')) AS identity_text,
        LOWER(STRING_AGG(COALESCE(dc.normalized_text, dc.text_content, ''), ' ' ORDER BY dc.page_number NULLS LAST, dc.row_number NULLS LAST)) AS source_text,
        (ARRAY_AGG(
            LEFT(REGEXP_REPLACE(COALESCE(dc.text_content, ''), '\s+', ' ', 'g'), 1200)
            ORDER BY
                CASE
                    WHEN LOWER(COALESCE(dc.text_content, '')) LIKE '%cover%' THEN 0
                    WHEN LOWER(COALESCE(dc.text_content, '')) LIKE '%warranty%' THEN 1
                    ELSE 2
                END,
                dc.page_number NULLS LAST,
                dc.row_number NULLS LAST
        ))[1] AS evidence_excerpt,
        (ARRAY_AGG(COALESCE(dc.source_locator, dc.sheet_name, dc.section_name, '') ORDER BY dc.page_number NULLS LAST, dc.row_number NULLS LAST))[1] AS source_locator
    FROM documents d
    JOIN document_content dc ON dc.document_id = d.document_id
    WHERE d.job_id IS NOT NULL
      AND COALESCE(d.deleted_at, TIMESTAMPTZ 'infinity') = TIMESTAMPTZ 'infinity'
      AND (
        LOWER(COALESCE(d.document_type, '') || ' ' || COALESCE(d.file_name, '')) LIKE '%warranty%'
        OR (
            LOWER(COALESCE(d.document_type, '') || ' ' || COALESCE(d.file_name, '')) LIKE '%proposal%'
            AND LOWER(COALESCE(dc.normalized_text, dc.text_content, '')) LIKE '%warranty%'
        )
      )
    GROUP BY d.document_id, d.job_id, d.document_type, d.file_name, d.sharepoint_url, d.modified_at, d.file_extension
),
document_candidates AS (
    SELECT
        dt.*,
        CASE WHEN identity_text LIKE '%proposal%' THEN 'proposed' ELSE 'issued' END AS warranty_status,
        CASE
            WHEN source_text LIKE '%workmanship%' OR source_text LIKE '%labor warranty%' THEN 'workmanship'
            WHEN source_text LIKE '%manufacturer%' OR source_text LIKE '%material warranty%'
              OR source_text LIKE '%system warranty%' OR source_text LIKE '%gaco%'
              OR source_text LIKE '%carlisle%' OR source_text LIKE '%mule-hide%'
              OR source_text LIKE '%mule hide%' OR source_text LIKE '%progressive materials%'
              OR source_text LIKE '%holcim%' THEN 'manufacturer_system'
            ELSE 'unspecified'
        END AS warranty_category,
        CASE
            WHEN source_text LIKE '%gaco%' THEN 'Gaco'
            WHEN source_text LIKE '%carlisle%' THEN 'Carlisle'
            WHEN source_text LIKE '%mule-hide%' OR source_text LIKE '%mule hide%' THEN 'Mule-Hide'
            WHEN source_text LIKE '%progressive materials%' THEN 'Progressive Materials'
            WHEN source_text LIKE '%holcim%' THEN 'Holcim'
            WHEN source_text LIKE '%spray-tec%' OR source_text LIKE '%spray tec%' THEN 'Spray-Tec'
            ELSE NULL
        END AS provider,
        CASE
            WHEN COALESCE(
                NULLIF(SUBSTRING(source_text FROM '([0-9]{1,2})[ -]?year'), '')::NUMERIC,
                NULLIF(SUBSTRING(source_text FROM '([0-9]{1,2})[ -]?yr'), '')::NUMERIC
            ) BETWEEN 1 AND 30
            THEN COALESCE(
                NULLIF(SUBSTRING(source_text FROM '([0-9]{1,2})[ -]?year'), '')::NUMERIC,
                NULLIF(SUBSTRING(source_text FROM '([0-9]{1,2})[ -]?yr'), '')::NUMERIC
            )
            ELSE NULL
        END AS duration_years,
        CASE
            WHEN source_text LIKE '%workmanship%' THEN 'Workmanship coverage'
            WHEN source_text LIKE '%watertight%' OR source_text LIKE '%water tight%' OR source_text LIKE '%leak%' THEN 'Watertightness or leak coverage'
            WHEN source_text LIKE '%material warranty%' THEN 'Manufacturer material coverage'
            WHEN source_text LIKE '%system warranty%' THEN 'Manufacturer system coverage'
            WHEN source_text LIKE '%coating%' THEN 'Specified roof coating system'
            ELSE 'Warranty coverage stated in source document'
        END AS coverage_summary,
        CASE
            WHEN SUBSTRING(source_text FROM '(?:effective|start date|date of completion)[^0-9]{0,24}([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})') IS NOT NULL
                THEN TO_DATE(SUBSTRING(source_text FROM '(?:effective|start date|date of completion)[^0-9]{0,24}([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})'), 'MM/DD/YYYY')
            WHEN SUBSTRING(source_text FROM '(?:effective|start date|date of completion)[^0-9]{0,24}([0-9]{4}-[0-9]{2}-[0-9]{2})') IS NOT NULL
                THEN SUBSTRING(source_text FROM '(?:effective|start date|date of completion)[^0-9]{0,24}([0-9]{4}-[0-9]{2}-[0-9]{2})')::DATE
            ELSE NULL
        END AS explicit_start_date
    FROM document_text dt
    WHERE source_text LIKE '%warranty%'
),
template_candidates AS (
    SELECT
        r.job_id,
        r.document_id,
        COALESCE(MAX(r.source_file), MAX(d.file_name), 'Estimate workbook') AS source_file,
        MAX(d.sharepoint_url) AS source_url,
        MAX(d.modified_at) AS source_modified_at,
        CASE
            WHEN MAX(r.warranty_years) BETWEEN 1 AND 30 THEN MAX(r.warranty_years)
            ELSE NULL
        END AS duration_years,
        STRING_AGG(DISTINCT NULLIF(COALESCE(r.selected_item_name, r.resolved_item_name, r.row_label), ''), ', ') AS warranty_text
    FROM estimate_template_rows r
    LEFT JOIN documents d ON d.document_id = r.document_id
    WHERE r.job_id IS NOT NULL
      AND (
        r.warranty_years IS NOT NULL
        OR LOWER(COALESCE(r.template_bucket, '') || ' ' || COALESCE(r.row_label, '')) LIKE '%warranty%'
      )
    GROUP BY r.job_id, r.document_id
),
staged_candidates AS (
    SELECT
        w.matched_job_id AS job_id,
        CASE WHEN w.source_system = 'sharepoint_warranty_folder' THEN 'issued' ELSE 'reported' END AS warranty_status,
        COALESCE(NULLIF(w.warranty_category, ''), 'unspecified') AS warranty_category,
        COALESCE(NULLIF(w.warranty_type, ''), 'Reported warranty') AS warranty_type,
        w.provider,
        CASE WHEN w.duration_years BETWEEN 1 AND 30 THEN w.duration_years ELSE NULL END AS duration_years,
        COALESCE(NULLIF(w.coverage_summary, ''), 'Warranty reported in staged source') AS coverage_summary,
        w.coverage_excerpt,
        w.start_date AS explicit_start_date,
        w.source_system AS source_kind,
        w.source_record_id AS source_document_id,
        w.source_file,
        w.source_url,
        w.source_locator,
        w.source_modified_at,
        w.extraction_method,
        w.extraction_confidence
    FROM warranty_source_records w
    WHERE w.matched_job_id IS NOT NULL
      AND COALESCE(w.match_review_required, TRUE) = FALSE
),
all_candidates AS (
    SELECT
        dc.job_id,
        dc.warranty_status,
        dc.warranty_category,
        CASE
            WHEN dc.provider IS NOT NULL AND dc.warranty_category = 'workmanship' THEN dc.provider || ' workmanship'
            WHEN dc.provider IS NOT NULL THEN dc.provider || ' manufacturer/system'
            WHEN dc.warranty_category = 'workmanship' THEN 'Workmanship'
            ELSE 'Unspecified warranty'
        END AS warranty_type,
        dc.provider,
        dc.duration_years,
        dc.coverage_summary,
        NULLIF(dc.evidence_excerpt, '') AS coverage_excerpt,
        dc.explicit_start_date,
        CASE WHEN dc.warranty_status = 'issued' THEN 'warranty_document' ELSE 'proposal_document' END AS source_kind,
        dc.document_id AS source_document_id,
        dc.file_name AS source_file,
        dc.sharepoint_url AS source_url,
        NULLIF(dc.source_locator, '') AS source_locator,
        dc.modified_at AS source_modified_at,
        'document_content_rules_v1' AS extraction_method,
        CASE WHEN dc.warranty_status = 'issued' THEN 'high' ELSE 'medium' END AS extraction_confidence
    FROM document_candidates dc

    UNION ALL

    SELECT
        tc.job_id,
        'proposed',
        CASE
            WHEN LOWER(COALESCE(tc.warranty_text, '')) LIKE '%workmanship%' THEN 'workmanship'
            WHEN LOWER(COALESCE(tc.warranty_text, '')) LIKE '%manufacturer%'
              OR LOWER(COALESCE(tc.warranty_text, '')) LIKE '%material%' THEN 'manufacturer_system'
            ELSE 'unspecified'
        END,
        COALESCE(NULLIF(tc.warranty_text, ''), 'Estimate workbook warranty'),
        NULL,
        tc.duration_years,
        'Warranty terms specified in estimate workbook',
        NULLIF(tc.warranty_text, ''),
        NULL,
        'estimate_workbook',
        tc.document_id,
        tc.source_file,
        tc.source_url,
        NULL,
        tc.source_modified_at,
        'estimate_template_rows_v1',
        'high'
    FROM template_candidates tc

    UNION ALL

    SELECT
        sc.job_id,
        sc.warranty_status,
        sc.warranty_category,
        sc.warranty_type,
        sc.provider,
        sc.duration_years,
        sc.coverage_summary,
        sc.coverage_excerpt,
        sc.explicit_start_date,
        sc.source_kind,
        sc.source_document_id,
        sc.source_file,
        sc.source_url,
        sc.source_locator,
        sc.source_modified_at,
        sc.extraction_method,
        sc.extraction_confidence
    FROM staged_candidates sc
)
SELECT
    MD5(CONCAT_WS('|', ac.job_id, ac.source_kind, COALESCE(ac.source_document_id, ''), ac.warranty_category, COALESCE(ac.duration_years::TEXT, ''))) AS warranty_evidence_id,
    ac.job_id,
    NULLIF(j.source_year::TEXT, '')::INTEGER AS source_year,
    j.division,
    ac.warranty_status,
    ac.warranty_category,
    ac.warranty_type,
    ac.provider,
    ac.duration_years,
    ac.coverage_summary,
    ac.coverage_excerpt,
    ac.explicit_start_date,
    ac.source_kind,
    ac.source_document_id,
    ac.source_file,
    ac.source_url,
    ac.source_locator,
    ac.source_modified_at,
    ac.extraction_method,
    ac.extraction_confidence,
    NOW()
FROM all_candidates ac
LEFT JOIN job_board_static_snapshot j ON j.job_id = ac.job_id
ON CONFLICT (warranty_evidence_id) DO UPDATE SET
    coverage_summary = EXCLUDED.coverage_summary,
    coverage_excerpt = EXCLUDED.coverage_excerpt,
    explicit_start_date = EXCLUDED.explicit_start_date,
    source_modified_at = EXCLUDED.source_modified_at,
    refreshed_at = NOW();

TRUNCATE TABLE job_warranty_summary;

INSERT INTO job_warranty_summary (
    warranty_summary_id,
    job_id,
    source_year,
    division,
    customer,
    job_name,
    warranty_status,
    warranty_category,
    warranty_type,
    provider,
    duration_years,
    coverage_summary,
    coverage_excerpt,
    start_date,
    start_date_source,
    start_date_confidence,
    start_date_is_inferred,
    expiration_date,
    source_document_id,
    source_file,
    source_url,
    duration_source_kind,
    duration_source_document_id,
    evidence_count,
    issued_evidence_count,
    reported_evidence_count,
    proposed_evidence_count,
    conflicting_duration_count,
    has_conflict,
    refreshed_at
)
WITH evidence_stats AS (
    SELECT
        job_id,
        warranty_category,
        COUNT(*) AS evidence_count,
        COUNT(*) FILTER (WHERE warranty_status = 'issued') AS issued_count,
        COUNT(*) FILTER (WHERE warranty_status = 'reported') AS reported_count,
        COUNT(*) FILTER (WHERE warranty_status = 'proposed') AS proposed_count,
        COUNT(DISTINCT duration_years) FILTER (WHERE duration_years IS NOT NULL) AS duration_count
    FROM job_warranty_evidence
    GROUP BY job_id, warranty_category
),
ranked AS (
    SELECT
        e.*,
        ROW_NUMBER() OVER (
            PARTITION BY e.job_id, e.warranty_category
            ORDER BY
                CASE e.warranty_status WHEN 'issued' THEN 0 WHEN 'reported' THEN 1 ELSE 2 END,
                (e.duration_years IS NULL),
                e.source_modified_at DESC NULLS LAST,
                e.warranty_evidence_id
        ) AS primary_rank,
        ROW_NUMBER() OVER (
            PARTITION BY e.job_id, e.warranty_category
            ORDER BY
                (e.duration_years IS NULL),
                CASE e.warranty_status WHEN 'issued' THEN 0 WHEN 'reported' THEN 1 ELSE 2 END,
                e.source_modified_at DESC NULLS LAST,
                e.warranty_evidence_id
        ) AS duration_rank
    FROM job_warranty_evidence e
),
selected AS (
    SELECT
        p.*,
        stats.evidence_count,
        stats.issued_count,
        stats.reported_count,
        stats.proposed_count,
        stats.duration_count,
        d.duration_years AS selected_duration_years,
        d.source_kind AS duration_source_kind,
        d.source_document_id AS duration_source_document_id
    FROM ranked p
    JOIN evidence_stats stats
      ON stats.job_id = p.job_id
     AND stats.warranty_category = p.warranty_category
    LEFT JOIN ranked d
      ON d.job_id = p.job_id
     AND d.warranty_category = p.warranty_category
     AND d.duration_rank = 1
    WHERE p.primary_rank = 1
),
date_sources AS (
    SELECT
        j.job_id,
        NULLIF(j.vsimple_completion_date::TEXT, '')::DATE AS completion_date,
        CASE
            WHEN NULLIF(base.raw ->> 'invoice_date', '') ~ '^\d{4}-\d{2}-\d{2}$'
                THEN (base.raw ->> 'invoice_date')::DATE
            ELSE NULL
        END AS invoice_date,
        tracking.actual_last_work_date,
        schedule.estimated_end_date,
        j.proposal_file_modified_at::DATE AS proposal_modified_date,
        CEIL(NULLIF(j.estimate_estimated_duration_days, 0))::INTEGER AS estimated_duration_days
    FROM job_board_static_snapshot j
    LEFT JOIN jobs base ON base.job_id = j.job_id
    LEFT JOIN (
        SELECT job_id, MAX(actual_last_work_date)::DATE AS actual_last_work_date
        FROM job_tracking_summary
        WHERE job_id IS NOT NULL
        GROUP BY job_id
    ) tracking ON tracking.job_id = j.job_id
    LEFT JOIN (
        SELECT job_id, MAX(estimated_end_date)::DATE AS estimated_end_date
        FROM crew_schedule
        WHERE job_id IS NOT NULL
        GROUP BY job_id
    ) schedule ON schedule.job_id = j.job_id
)
SELECT
    MD5(CONCAT_WS('|', s.job_id, s.warranty_category)) AS warranty_summary_id,
    s.job_id,
    s.source_year,
    s.division,
    j.customer,
    j.job_name,
    CASE WHEN s.issued_count > 0 THEN 'issued' WHEN s.reported_count > 0 THEN 'reported' ELSE 'proposed' END,
    s.warranty_category,
    s.warranty_type,
    s.provider,
    s.selected_duration_years,
    s.coverage_summary,
    s.coverage_excerpt,
    CASE
        WHEN s.explicit_start_date IS NOT NULL THEN s.explicit_start_date
        WHEN ds.completion_date IS NOT NULL THEN ds.completion_date
        WHEN s.warranty_status = 'issued' AND s.source_modified_at IS NOT NULL THEN s.source_modified_at::DATE
        WHEN ds.invoice_date IS NOT NULL THEN ds.invoice_date
        WHEN ds.actual_last_work_date IS NOT NULL THEN ds.actual_last_work_date
        WHEN ds.estimated_end_date IS NOT NULL THEN ds.estimated_end_date
        WHEN ds.proposal_modified_date IS NOT NULL AND ds.estimated_duration_days IS NOT NULL
            THEN ds.proposal_modified_date + ds.estimated_duration_days
        ELSE NULL
    END AS start_date,
    CASE
        WHEN s.explicit_start_date IS NOT NULL THEN 'explicit_warranty_date'
        WHEN ds.completion_date IS NOT NULL THEN 'project_completion_date'
        WHEN s.warranty_status = 'issued' AND s.source_modified_at IS NOT NULL THEN 'warranty_file_modified_date'
        WHEN ds.invoice_date IS NOT NULL THEN 'invoice_date'
        WHEN ds.actual_last_work_date IS NOT NULL THEN 'job_tracking_last_work_date'
        WHEN ds.estimated_end_date IS NOT NULL THEN 'scheduled_end_date'
        WHEN ds.proposal_modified_date IS NOT NULL AND ds.estimated_duration_days IS NOT NULL THEN 'proposal_modified_plus_estimated_duration'
        ELSE 'unavailable'
    END AS start_date_source,
    CASE
        WHEN s.explicit_start_date IS NOT NULL OR ds.completion_date IS NOT NULL THEN 'high'
        WHEN (s.warranty_status = 'issued' AND s.source_modified_at IS NOT NULL)
          OR ds.invoice_date IS NOT NULL OR ds.actual_last_work_date IS NOT NULL THEN 'medium'
        WHEN ds.estimated_end_date IS NOT NULL
          OR (ds.proposal_modified_date IS NOT NULL AND ds.estimated_duration_days IS NOT NULL) THEN 'low'
        ELSE 'unavailable'
    END AS start_date_confidence,
    s.explicit_start_date IS NULL AS start_date_is_inferred,
    CASE
        WHEN s.selected_duration_years IS NULL THEN NULL
        ELSE (
            CASE
                WHEN s.explicit_start_date IS NOT NULL THEN s.explicit_start_date
                WHEN ds.completion_date IS NOT NULL THEN ds.completion_date
                WHEN s.warranty_status = 'issued' AND s.source_modified_at IS NOT NULL THEN s.source_modified_at::DATE
                WHEN ds.invoice_date IS NOT NULL THEN ds.invoice_date
                WHEN ds.actual_last_work_date IS NOT NULL THEN ds.actual_last_work_date
                WHEN ds.estimated_end_date IS NOT NULL THEN ds.estimated_end_date
                WHEN ds.proposal_modified_date IS NOT NULL AND ds.estimated_duration_days IS NOT NULL
                    THEN ds.proposal_modified_date + ds.estimated_duration_days
                ELSE NULL
            END + MAKE_INTERVAL(years => s.selected_duration_years::INTEGER)
        )::DATE
    END AS expiration_date,
    s.source_document_id,
    s.source_file,
    s.source_url,
    s.duration_source_kind,
    s.duration_source_document_id,
    s.evidence_count,
    s.issued_count,
    s.reported_count,
    s.proposed_count,
    s.duration_count,
    s.duration_count > 1,
    NOW()
FROM selected s
LEFT JOIN job_board_static_snapshot j ON j.job_id = s.job_id
LEFT JOIN date_sources ds ON ds.job_id = s.job_id;

CREATE INDEX IF NOT EXISTS idx_job_warranty_evidence_job_id ON job_warranty_evidence(job_id);
CREATE INDEX IF NOT EXISTS idx_job_warranty_evidence_status ON job_warranty_evidence(warranty_status);
CREATE INDEX IF NOT EXISTS idx_job_warranty_summary_job_id ON job_warranty_summary(job_id);
CREATE INDEX IF NOT EXISTS idx_job_warranty_summary_expiration ON job_warranty_summary(expiration_date);
CREATE INDEX IF NOT EXISTS idx_job_warranty_summary_source_year ON job_warranty_summary(source_year);

DROP VIEW IF EXISTS warranty_registry_all;

CREATE VIEW warranty_registry_all AS
SELECT
    s.warranty_summary_id,
    s.job_id,
    s.source_year,
    s.division,
    s.customer,
    s.job_name,
    s.warranty_status,
    s.warranty_category,
    s.warranty_type,
    s.provider,
    s.duration_years,
    s.coverage_summary,
    s.coverage_excerpt,
    s.start_date,
    s.start_date_source,
    s.start_date_confidence,
    s.start_date_is_inferred,
    s.expiration_date,
    s.source_document_id,
    s.source_file,
    s.source_url,
    s.duration_source_kind,
    s.duration_source_document_id,
    s.evidence_count,
    s.issued_evidence_count,
    s.reported_evidence_count,
    s.proposed_evidence_count,
    s.conflicting_duration_count,
    s.has_conflict,
    FALSE AS match_review_required,
    NULL::TEXT AS matched_vsimple_id,
    NULL::TEXT AS match_method,
    NULL::TEXT AS match_confidence,
    NULL::NUMERIC AS match_score,
    '[]'::JSONB AS match_candidates,
    NULL::TEXT AS expiration_date_source,
    NULL::TEXT AS site_address,
    s.refreshed_at
FROM job_warranty_summary s

UNION ALL

SELECT
    w.source_record_id,
    NULL::TEXT,
    w.source_year,
    w.division,
    w.reported_customer,
    w.reported_name,
    CASE WHEN w.source_system = 'sharepoint_warranty_folder' THEN 'issued' ELSE 'reported' END,
    COALESCE(NULLIF(w.warranty_category, ''), 'unspecified'),
    COALESCE(NULLIF(w.warranty_type, ''), 'Reported warranty'),
    w.provider,
    w.duration_years,
    w.coverage_summary,
    w.coverage_excerpt,
    w.start_date,
    CASE WHEN w.source_system = 'sharepoint_warranty_folder' THEN 'explicit_warranty_date' ELSE 'legacy_reported_date' END,
    w.extraction_confidence,
    FALSE,
    w.expiration_date,
    w.source_record_id,
    w.source_file,
    w.source_url,
    w.source_system,
    w.source_record_id,
    1,
    CASE WHEN w.source_system = 'sharepoint_warranty_folder' THEN 1 ELSE 0 END,
    CASE WHEN w.source_system <> 'sharepoint_warranty_folder' THEN 1 ELSE 0 END,
    0,
    0,
    w.has_date_conflict,
    w.match_review_required,
    w.matched_vsimple_id,
    w.match_method,
    w.match_confidence,
    w.match_score,
    w.match_candidates,
    w.expiration_date_source,
    w.reported_address,
    w.updated_at
FROM warranty_source_records w
WHERE w.matched_job_id IS NULL OR COALESCE(w.match_review_required, TRUE);
