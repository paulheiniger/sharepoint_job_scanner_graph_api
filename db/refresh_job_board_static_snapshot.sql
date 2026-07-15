DROP TABLE IF EXISTS job_board_static_snapshot;

CREATE TABLE job_board_static_snapshot AS
WITH vsimple_enrichment AS (
    SELECT DISTINCT ON (m.job_id)
        m.job_id,
        p.project_type AS vsimple_project_type,
        p.deal_type AS vsimple_deal_type,
        p.lead_source AS vsimple_lead_source,
        p.referral_source AS vsimple_referral_source,
        p.deal_owner AS vsimple_deal_owner,
        p.estimator_salesperson AS vsimple_estimator,
        p.bid_amount AS vsimple_bid_amount,
        p.billing_amount AS vsimple_billing_amount,
        p.gross_profit AS vsimple_gross_profit,
        p.all_costs AS vsimple_all_costs,
        p.estimated_sqft AS vsimple_estimated_sqft,
        p.roof_deck_sqft AS vsimple_roof_deck_sqft,
        p.completion_date AS vsimple_completion_date,
        p.closed_date AS vsimple_closed_date,
        p.spray_tec_system AS vsimple_spray_tec_system,
        p.roof_type AS vsimple_roof_type,
        p.construction_type AS vsimple_construction_type,
        p.building_use AS vsimple_building_use,
        p.scope_summary AS vsimple_scope_summary
    FROM vsimple_sharepoint_job_matches_accepted m
    LEFT JOIN vsimple_projects p ON p.vsimple_id = m.vsimple_id
    WHERE m.job_id IS NOT NULL
    ORDER BY m.job_id
),
template_enrichment AS (
    SELECT
        t.job_id,
        MAX(t.warranty_years) AS template_warranty_years,
        STRING_AGG(DISTINCT NULLIF(t.selected_item_name, ''), ', ' ORDER BY NULLIF(t.selected_item_name, ''))
            FILTER (
                WHERE NULLIF(t.selected_item_name, '') IS NOT NULL
                  AND (
                    LOWER(COALESCE(t.template_bucket, '')) IN (
                        'coating', 'foam', 'roofing_foam', 'thermal_barrier_coating',
                        'primer', 'fabric', 'caulk_sealant', 'membrane'
                    )
                    OR LOWER(COALESCE(t.line_item_kind, '')) = 'material'
                  )
            ) AS template_material_system
    FROM estimate_template_rows t
    WHERE t.job_id IS NOT NULL
    GROUP BY t.job_id
),
typed_documents AS (
    SELECT
        d.job_id,
        CASE
            WHEN LOWER(COALESCE(d.document_type, '')) LIKE '%proposal%'
              OR LOWER(COALESCE(d.file_name, '')) LIKE '%proposal%'
                THEN 'proposal'
            WHEN LOWER(COALESCE(d.document_type, '')) LIKE '%estimate%'
              OR LOWER(COALESCE(d.file_name, '')) LIKE '%estimate%'
                THEN 'estimate'
            ELSE NULL
        END AS document_kind,
        d.file_name,
        d.relative_path,
        NULLIF(s.metadata_json ->> 'createdDateTime', '')::timestamptz AS file_created_at,
        COALESCE(s.last_modified_at, d.modified_at) AS file_modified_at,
        COALESCE(
            NULLIF(s.metadata_json #>> '{lastModifiedBy,user,displayName}', ''),
            NULLIF(s.metadata_json #>> '{lastModifiedBy,user,email}', ''),
            NULLIF(s.metadata_json #>> '{lastModifiedBy,application,displayName}', '')
        ) AS file_modified_by
    FROM documents d
    LEFT JOIN sharepoint_drive_items s
      ON s.drive_id = d.drive_id
     AND s.drive_item_id = d.drive_item_id
    WHERE d.job_id IS NOT NULL
      AND (
        LOWER(COALESCE(d.document_type, '')) LIKE '%proposal%'
        OR LOWER(COALESCE(d.file_name, '')) LIKE '%proposal%'
        OR LOWER(COALESCE(d.document_type, '')) LIKE '%estimate%'
        OR LOWER(COALESCE(d.file_name, '')) LIKE '%estimate%'
      )
),
ranked_documents AS (
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY job_id, document_kind
            ORDER BY
                (NULLIF(file_modified_by, '') IS NULL),
                file_created_at DESC NULLS LAST,
                file_modified_at DESC NULLS LAST,
                file_name
        ) AS rn,
        COUNT(*) OVER (PARTITION BY job_id, document_kind) AS document_count
    FROM typed_documents
    WHERE document_kind IS NOT NULL
),
document_dates AS (
    SELECT
        job_id,
        MAX(file_created_at) FILTER (WHERE document_kind = 'proposal' AND rn = 1) AS proposal_file_created_at,
        MAX(file_modified_at) FILTER (WHERE document_kind = 'proposal' AND rn = 1) AS proposal_file_modified_at,
        MAX(file_modified_by) FILTER (WHERE document_kind = 'proposal' AND rn = 1) AS proposal_file_modified_by,
        MAX(file_name) FILTER (WHERE document_kind = 'proposal' AND rn = 1) AS proposal_file_name,
        MAX(relative_path) FILTER (WHERE document_kind = 'proposal' AND rn = 1) AS proposal_relative_path,
        MAX(document_count) FILTER (WHERE document_kind = 'proposal' AND rn = 1) AS proposal_document_count,
        MAX(file_created_at) FILTER (WHERE document_kind = 'estimate' AND rn = 1) AS estimate_file_created_at,
        MAX(file_modified_at) FILTER (WHERE document_kind = 'estimate' AND rn = 1) AS estimate_file_modified_at,
        MAX(file_modified_by) FILTER (WHERE document_kind = 'estimate' AND rn = 1) AS estimate_file_modified_by,
        MAX(file_name) FILTER (WHERE document_kind = 'estimate' AND rn = 1) AS estimate_file_name,
        MAX(relative_path) FILTER (WHERE document_kind = 'estimate' AND rn = 1) AS estimate_relative_path,
        MAX(document_count) FILTER (WHERE document_kind = 'estimate' AND rn = 1) AS estimate_document_count
    FROM ranked_documents
    WHERE rn = 1
    GROUP BY job_id
),
folder_documents AS (
    SELECT
        CASE
            WHEN LOWER(COALESCE(d.document_type, '')) LIKE '%proposal%'
              OR LOWER(COALESCE(d.file_name, '')) LIKE '%proposal%'
                THEN 'proposal'
            WHEN LOWER(COALESCE(d.document_type, '')) LIKE '%estimate%'
              OR LOWER(COALESCE(d.file_name, '')) LIKE '%estimate%'
                THEN 'estimate'
            ELSE NULL
        END AS document_kind,
        REGEXP_REPLACE(
            LOWER(REGEXP_REPLACE(SPLIT_PART(COALESCE(d.relative_path, ''), '/', 1), '\([^)]*\)', ' ', 'g')),
            '[^a-z0-9]+',
            '',
            'g'
        ) AS folder_match_key,
        d.file_name,
        d.relative_path,
        NULLIF(s.metadata_json ->> 'createdDateTime', '')::timestamptz AS file_created_at,
        COALESCE(s.last_modified_at, d.modified_at) AS file_modified_at,
        COALESCE(
            NULLIF(s.metadata_json #>> '{lastModifiedBy,user,displayName}', ''),
            NULLIF(s.metadata_json #>> '{lastModifiedBy,user,email}', ''),
            NULLIF(s.metadata_json #>> '{lastModifiedBy,application,displayName}', '')
        ) AS file_modified_by
    FROM documents d
    LEFT JOIN sharepoint_drive_items s
      ON s.drive_id = d.drive_id
     AND s.drive_item_id = d.drive_item_id
    WHERE d.relative_path IS NOT NULL
      AND (
        LOWER(COALESCE(d.document_type, '')) LIKE '%proposal%'
        OR LOWER(COALESCE(d.file_name, '')) LIKE '%proposal%'
        OR LOWER(COALESCE(d.document_type, '')) LIKE '%estimate%'
        OR LOWER(COALESCE(d.file_name, '')) LIKE '%estimate%'
      )
),
ranked_folder_documents AS (
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY folder_match_key, document_kind
            ORDER BY
                (NULLIF(file_modified_by, '') IS NULL),
                file_created_at DESC NULLS LAST,
                file_modified_at DESC NULLS LAST,
                file_name
        ) AS rn
    FROM folder_documents
    WHERE document_kind IS NOT NULL
      AND folder_match_key <> ''
),
folder_dates AS (
    SELECT
        folder_match_key,
        MAX(file_created_at) FILTER (WHERE document_kind = 'proposal' AND rn = 1) AS folder_proposal_file_created_at,
        MAX(file_modified_at) FILTER (WHERE document_kind = 'proposal' AND rn = 1) AS folder_proposal_file_modified_at,
        MAX(file_modified_by) FILTER (WHERE document_kind = 'proposal' AND rn = 1) AS folder_proposal_file_modified_by,
        MAX(file_name) FILTER (WHERE document_kind = 'proposal' AND rn = 1) AS folder_proposal_file_name,
        MAX(relative_path) FILTER (WHERE document_kind = 'proposal' AND rn = 1) AS folder_proposal_relative_path,
        MAX(file_created_at) FILTER (WHERE document_kind = 'estimate' AND rn = 1) AS folder_estimate_file_created_at,
        MAX(file_modified_at) FILTER (WHERE document_kind = 'estimate' AND rn = 1) AS folder_estimate_file_modified_at,
        MAX(file_modified_by) FILTER (WHERE document_kind = 'estimate' AND rn = 1) AS folder_estimate_file_modified_by,
        MAX(file_name) FILTER (WHERE document_kind = 'estimate' AND rn = 1) AS folder_estimate_file_name,
        MAX(relative_path) FILTER (WHERE document_kind = 'estimate' AND rn = 1) AS folder_estimate_relative_path
    FROM ranked_folder_documents
    WHERE rn = 1
    GROUP BY folder_match_key
),
dashboard_estimate_labor AS (
    SELECT
        e.job_id,
        MAX(e.estimated_duration_days) AS estimate_estimated_duration_days,
        MAX(e.estimated_labor_hours) AS estimate_estimated_labor_hours,
        MAX(e.estimated_crew_size) AS estimate_estimated_crew_size,
        MAX(e.labor_subtotal) AS estimate_labor_subtotal
    FROM dashboard_estimates e
    WHERE e.job_id IS NOT NULL
    GROUP BY e.job_id
),
template_labor AS (
    WITH labor_by_workbook AS (
        SELECT
            r.job_id,
            r.document_id,
            COALESCE(r.source_file, '') AS source_file,
            SUM(GREATEST(COALESCE(r.days, 0), 0)) AS estimate_estimated_duration_days,
            SUM(GREATEST(COALESCE(r.total_hours, 0), 0)) AS estimate_estimated_labor_hours,
            MAX(NULLIF(r.crew_size, 0)) AS estimate_estimated_crew_size,
            SUM(COALESCE(NULLIF(r.estimated_cost, 0), NULLIF(r.calculated_cost, 0), 0)) AS estimate_labor_subtotal,
            COUNT(*) AS labor_row_count,
            COALESCE(MAX(d.modified_at), TIMESTAMPTZ 'epoch') AS source_modified_at
        FROM estimate_template_rows r
        LEFT JOIN documents d ON d.document_id = r.document_id
        WHERE r.job_id IS NOT NULL
          AND (
            LOWER(COALESCE(r.template_section, '')) LIKE '%labor%'
            OR LOWER(COALESCE(r.template_bucket, '')) LIKE 'labor_%'
            OR LOWER(COALESCE(r.line_item_kind, '')) = 'labor'
          )
          AND LOWER(BTRIM(COALESCE(r.row_label, ''))) NOT IN ('types', 'types:', 'units')
          AND COALESCE(r.days, 0) <= 30
          AND COALESCE(r.total_hours, 0) <= 1000
        GROUP BY r.job_id, r.document_id, COALESCE(r.source_file, '')
    ),
    ranked AS (
        SELECT
            *,
            ROW_NUMBER() OVER (
                PARTITION BY job_id
                ORDER BY
                    CASE WHEN COALESCE(estimate_labor_subtotal, 0) > 0 THEN 1 ELSE 0 END DESC,
                    source_modified_at DESC,
                    COALESCE(estimate_estimated_labor_hours, 0) DESC,
                    COALESCE(estimate_estimated_duration_days, 0) DESC,
                    labor_row_count DESC
            ) AS rank
        FROM labor_by_workbook
        WHERE COALESCE(estimate_estimated_labor_hours, 0) > 0
           OR COALESCE(estimate_estimated_duration_days, 0) > 0
           OR COALESCE(estimate_labor_subtotal, 0) > 0
    )
    SELECT
        job_id,
        estimate_estimated_duration_days,
        estimate_estimated_labor_hours,
        estimate_estimated_crew_size,
        estimate_labor_subtotal
    FROM ranked
    WHERE rank = 1
),
estimate_labor AS (
    SELECT
        COALESCE(d.job_id, t.job_id) AS job_id,
        COALESCE(NULLIF(d.estimate_estimated_duration_days, 0), t.estimate_estimated_duration_days) AS estimate_estimated_duration_days,
        COALESCE(NULLIF(d.estimate_estimated_labor_hours, 0), t.estimate_estimated_labor_hours) AS estimate_estimated_labor_hours,
        COALESCE(NULLIF(d.estimate_estimated_crew_size, 0), t.estimate_estimated_crew_size) AS estimate_estimated_crew_size,
        COALESCE(NULLIF(d.estimate_labor_subtotal, 0), t.estimate_labor_subtotal) AS estimate_labor_subtotal
    FROM dashboard_estimate_labor d
    FULL OUTER JOIN template_labor t ON t.job_id = d.job_id
)
SELECT
    j.*,
    COALESCE(NULLIF(j.folder_url, ''), NULLIF(j.folder_path, '')) AS folder_link_or_path,
    j.estimate_file AS proposal_file,
    v.vsimple_project_type,
    v.vsimple_deal_type,
    v.vsimple_lead_source,
    v.vsimple_referral_source,
    v.vsimple_deal_owner,
    v.vsimple_estimator,
    v.vsimple_bid_amount,
    v.vsimple_billing_amount,
    v.vsimple_gross_profit,
    v.vsimple_all_costs,
    v.vsimple_estimated_sqft,
    v.vsimple_roof_deck_sqft,
    v.vsimple_completion_date,
    v.vsimple_closed_date,
    v.vsimple_spray_tec_system,
    v.vsimple_roof_type,
    v.vsimple_construction_type,
    v.vsimple_building_use,
    v.vsimple_scope_summary,
    t.template_warranty_years,
    t.template_material_system,
    s.document_substrate,
    s.document_material_system,
    s.document_warranty_type,
    s.document_warranty_years,
    COALESCE(dd.proposal_file_created_at, fd.folder_proposal_file_created_at) AS proposal_file_created_at,
    COALESCE(dd.proposal_file_modified_at, fd.folder_proposal_file_modified_at) AS proposal_file_modified_at,
    COALESCE(dd.proposal_file_modified_by, fd.folder_proposal_file_modified_by) AS proposal_file_modified_by,
    COALESCE(dd.proposal_file_name, fd.folder_proposal_file_name) AS proposal_file_name,
    COALESCE(dd.proposal_relative_path, fd.folder_proposal_relative_path) AS proposal_relative_path,
    dd.proposal_document_count,
    COALESCE(dd.estimate_file_created_at, fd.folder_estimate_file_created_at) AS estimate_file_created_at,
    COALESCE(dd.estimate_file_modified_at, fd.folder_estimate_file_modified_at) AS estimate_file_modified_at,
    COALESCE(dd.estimate_file_modified_by, fd.folder_estimate_file_modified_by) AS estimate_file_modified_by,
    COALESCE(dd.estimate_file_name, fd.folder_estimate_file_name) AS estimate_file_name,
    COALESCE(dd.estimate_relative_path, fd.folder_estimate_relative_path) AS estimate_relative_path,
    dd.estimate_document_count,
    l.estimate_estimated_duration_days,
    l.estimate_estimated_labor_hours,
    l.estimate_estimated_crew_size,
    l.estimate_labor_subtotal,
    NOW() AS refreshed_at
FROM dashboard_jobs j
LEFT JOIN vsimple_enrichment v ON v.job_id = j.job_id
LEFT JOIN template_enrichment t ON t.job_id = j.job_id
LEFT JOIN job_document_signals s ON s.job_id = j.job_id
LEFT JOIN document_dates dd ON dd.job_id = j.job_id
LEFT JOIN folder_dates fd
  ON fd.folder_match_key = REGEXP_REPLACE(
        LOWER(REGEXP_REPLACE(SPLIT_PART(COALESCE(j.folder_path, ''), '/', 1), '\([^)]*\)', ' ', 'g')),
        '[^a-z0-9]+',
        '',
        'g'
     )
LEFT JOIN estimate_labor l ON l.job_id = j.job_id;

CREATE UNIQUE INDEX idx_job_board_static_snapshot_job_id
    ON job_board_static_snapshot(job_id);

CREATE INDEX idx_job_board_static_snapshot_refreshed_at
    ON job_board_static_snapshot(refreshed_at);
