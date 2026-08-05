CREATE TABLE IF NOT EXISTS vsimple_customers_clean (
    customer_id TEXT PRIMARY KEY,
    record_id TEXT,
    display_name TEXT,
    first_name TEXT,
    last_name TEXT,
    company_name TEXT,
    job_title TEXT,
    email TEXT,
    mobile_phone TEXT,
    phone TEXT,
    address TEXT,
    city TEXT,
    state TEXT,
    postal_code TEXT,
    vsimple_url TEXT,
    source_file TEXT NOT NULL,
    source_sheet TEXT,
    source_row INTEGER,
    raw JSONB NOT NULL DEFAULT '{}'::JSONB,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vsimple_customers_clean_record_id
    ON vsimple_customers_clean(record_id);
CREATE INDEX IF NOT EXISTS idx_vsimple_customers_clean_email
    ON vsimple_customers_clean(LOWER(email));
CREATE INDEX IF NOT EXISTS idx_vsimple_customers_clean_name
    ON vsimple_customers_clean(LOWER(display_name));
CREATE INDEX IF NOT EXISTS idx_vsimple_customers_clean_company
    ON vsimple_customers_clean(LOWER(company_name));

CREATE TABLE IF NOT EXISTS vsimple_warranty_projects_clean (
    vsimple_id TEXT PRIMARY KEY,
    project_name TEXT,
    customer_name TEXT,
    project_status TEXT,
    division TEXT,
    site_address TEXT,
    city TEXT,
    state TEXT,
    postal_code TEXT,
    warranty_term_raw TEXT,
    warranty_type TEXT,
    provider TEXT,
    duration_years NUMERIC,
    completion_date DATE,
    start_date DATE,
    expiration_date DATE,
    expiration_date_source TEXT,
    warranty_number TEXT,
    reported_contact_name TEXT,
    reported_contact_email TEXT,
    reported_contact_phone TEXT,
    vsimple_url TEXT,
    sharepoint_url TEXT,
    source_file TEXT NOT NULL,
    source_sheet TEXT,
    source_row INTEGER,
    raw JSONB NOT NULL DEFAULT '{}'::JSONB,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vsimple_warranty_projects_clean_name
    ON vsimple_warranty_projects_clean(LOWER(project_name));
CREATE INDEX IF NOT EXISTS idx_vsimple_warranty_projects_clean_expiration
    ON vsimple_warranty_projects_clean(expiration_date);

CREATE TABLE IF NOT EXISTS vsimple_project_contacts_clean (
    vsimple_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    relationship_source TEXT NOT NULL,
    source_file TEXT NOT NULL,
    source_sheet TEXT,
    source_row INTEGER,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (vsimple_id, customer_id)
);

CREATE INDEX IF NOT EXISTS idx_vsimple_project_contacts_clean_customer
    ON vsimple_project_contacts_clean(customer_id);

DROP VIEW IF EXISTS warranty_master_clean;

CREATE VIEW warranty_master_clean AS
WITH direct_contacts AS (
    SELECT
        pc.vsimple_id,
        COUNT(DISTINCT pc.customer_id) AS contact_count,
        STRING_AGG(DISTINCT NULLIF(BTRIM(c.display_name), ''), ' | ' ORDER BY NULLIF(BTRIM(c.display_name), ''))
            FILTER (WHERE NULLIF(BTRIM(c.display_name), '') IS NOT NULL) AS contact_names,
        STRING_AGG(DISTINCT NULLIF(BTRIM(c.email), ''), ' | ' ORDER BY NULLIF(BTRIM(c.email), ''))
            FILTER (WHERE NULLIF(BTRIM(c.email), '') IS NOT NULL) AS contact_emails,
        STRING_AGG(
            DISTINCT COALESCE(NULLIF(BTRIM(c.mobile_phone), ''), NULLIF(BTRIM(c.phone), '')),
            ' | ' ORDER BY COALESCE(NULLIF(BTRIM(c.mobile_phone), ''), NULLIF(BTRIM(c.phone), ''))
        ) FILTER (
            WHERE COALESCE(NULLIF(BTRIM(c.mobile_phone), ''), NULLIF(BTRIM(c.phone), '')) IS NOT NULL
        ) AS contact_phones,
        JSONB_AGG(
            DISTINCT JSONB_BUILD_OBJECT(
                'customer_id', c.customer_id,
                'name', c.display_name,
                'company', c.company_name,
                'email', NULLIF(BTRIM(c.email), ''),
                'mobile_phone', NULLIF(BTRIM(c.mobile_phone), ''),
                'phone', NULLIF(BTRIM(c.phone), ''),
                'vsimple_url', c.vsimple_url
            )
        ) AS contacts
    FROM vsimple_project_contacts_clean pc
    JOIN vsimple_customers_clean c ON c.customer_id = pc.customer_id
    GROUP BY pc.vsimple_id
),
general_vsimple_contacts AS (
    SELECT
        p.vsimple_id,
        COALESCE(NULLIF(BTRIM(c.display_name), ''), NULLIF(BTRIM(p.contact_name), '')) AS contact_name,
        COALESCE(NULLIF(BTRIM(c.email), ''), NULLIF(BTRIM(p.contact_email), '')) AS contact_email,
        COALESCE(
            NULLIF(BTRIM(c.mobile_phone), ''),
            NULLIF(BTRIM(c.phone), ''),
            NULLIF(BTRIM(p.contact_phone), '')
        ) AS contact_phone,
        CASE
            WHEN NULLIF(BTRIM(c.display_name), '') IS NOT NULL THEN 'vsimple_customer_export'
            WHEN NULLIF(BTRIM(p.contact_name), '') IS NOT NULL THEN 'vsimple_project_export'
        END AS contact_name_source,
        CASE
            WHEN NULLIF(BTRIM(c.email), '') IS NOT NULL THEN 'vsimple_customer_export'
            WHEN NULLIF(BTRIM(p.contact_email), '') IS NOT NULL THEN 'vsimple_project_export'
        END AS contact_email_source,
        CASE
            WHEN COALESCE(NULLIF(BTRIM(c.mobile_phone), ''), NULLIF(BTRIM(c.phone), '')) IS NOT NULL
                THEN 'vsimple_customer_export'
            WHEN NULLIF(BTRIM(p.contact_phone), '') IS NOT NULL THEN 'vsimple_project_export'
        END AS contact_phone_source
    FROM vsimple_projects p
    LEFT JOIN vsimple_customers_clean c
        ON c.customer_id = NULLIF(
            BTRIM(REGEXP_REPLACE(COALESCE(p.associated_contact_id, ''), '[.]0$', '')),
            ''
        )
),
job_contacts AS (
    SELECT
        j.job_id,
        NULLIF(BTRIM(j.raw ->> 'contact_name'), '') AS contact_name,
        NULLIF(BTRIM(j.raw ->> 'contact_email'), '') AS contact_email,
        NULLIF(BTRIM(j.raw ->> 'contact_phone'), '') AS contact_phone,
        NULLIF(BTRIM(j.folder_url), '') AS folder_url,
        NULLIF(BTRIM(j.estimate_url), '') AS estimate_url,
        COALESCE(NULLIF(BTRIM(j.primary_estimate_file), ''), NULLIF(BTRIM(j.estimate_file), ''))
            AS estimate_file
    FROM jobs j
),
job_to_vsimple AS (
    SELECT DISTINCT ON (m.job_id)
        m.job_id,
        m.vsimple_id,
        m.match_score
    FROM vsimple_sharepoint_job_matches m
    WHERE m.match_status = 'matched'
      AND NULLIF(BTRIM(m.job_id), '') IS NOT NULL
    ORDER BY m.job_id, m.match_score DESC NULLS LAST, m.vsimple_id
),
warranty_source_to_vsimple AS (
    SELECT DISTINCT ON (w.matched_job_id)
        w.matched_job_id AS job_id,
        w.matched_vsimple_id AS vsimple_id
    FROM warranty_source_records w
    WHERE NULLIF(BTRIM(w.matched_job_id), '') IS NOT NULL
      AND NULLIF(BTRIM(w.matched_vsimple_id), '') IS NOT NULL
      AND COALESCE(w.match_review_required, FALSE) = FALSE
    ORDER BY
        w.matched_job_id,
        CASE w.source_system
            WHEN 'recent_completed_warranty_list' THEN 0
            WHEN 'vsimple_project_warranty_export' THEN 1
            WHEN 'legacy_vsimple_warranty_export' THEN 2
            ELSE 3
        END,
        w.match_score DESC NULLS LAST,
        w.source_record_id
),
issued_documents AS (
    SELECT DISTINCT ON (e.job_id)
        e.job_id,
        e.source_url AS issued_document_url,
        e.source_file AS issued_document_file
    FROM job_warranty_evidence e
    WHERE e.warranty_status = 'issued'
      AND e.source_kind = 'warranty_document'
    ORDER BY e.job_id, e.source_modified_at DESC NULLS LAST, e.warranty_evidence_id
),
registry_enriched AS (
    SELECT
        r.*,
        COALESCE(
            NULLIF(BTRIM(r.matched_vsimple_id), ''),
            NULLIF(BTRIM(w.vsimple_id), ''),
            wsj.vsimple_id,
            jv.vsimple_id
        )
            AS resolved_vsimple_id,
        w.source_system,
        w.raw AS source_raw
    FROM warranty_registry_all r
    LEFT JOIN job_to_vsimple jv ON jv.job_id = r.job_id
    LEFT JOIN warranty_source_to_vsimple wsj ON wsj.job_id = r.job_id
    LEFT JOIN warranty_source_records w ON w.source_record_id = r.warranty_summary_id
    WHERE r.warranty_status IN ('issued', 'reported')
),
identity_candidates AS (
    SELECT
        r.*,
        COALESCE(
            'vsimple:' || NULLIF(BTRIM(r.resolved_vsimple_id), ''),
            'job:' || NULLIF(BTRIM(r.job_id), ''),
            'source:' || r.warranty_summary_id
        ) AS fallback_identity_key,
        CASE
            WHEN r.warranty_status = 'reported'
              AND NULLIF(BTRIM(r.resolved_vsimple_id), '') IS NULL
              AND NULLIF(BTRIM(r.job_id), '') IS NULL
              AND COALESCE(NULLIF(BTRIM(r.job_name), ''), NULLIF(BTRIM(r.customer), '')) IS NOT NULL
              AND REGEXP_REPLACE(
                  LOWER(BTRIM(COALESCE(NULLIF(r.job_name, ''), NULLIF(r.customer, '')))),
                  '[^a-z0-9]+',
                  '',
                  'g'
              ) NOT IN ('foldername', 'projectname', 'customername')
              AND r.source_system IN (
                  'manufacturer_warranty_list',
                  'recent_completed_warranty_list',
                  'vsimple_project_warranty_export',
                  'legacy_vsimple_warranty_export',
                  'legacy_customer_list'
              )
            THEN 'reported:' || MD5(CONCAT_WS(
                '|',
                'reported-warranty-v1',
                COALESCE(LOWER(BTRIM(r.source_system)), ''),
                COALESCE(LOWER(BTRIM(r.source_file)), ''),
                COALESCE(REGEXP_REPLACE(LOWER(BTRIM(r.job_name)), '[^a-z0-9]+', '', 'g'), ''),
                COALESCE(REGEXP_REPLACE(LOWER(BTRIM(r.customer)), '[^a-z0-9]+', '', 'g'), ''),
                COALESCE(REGEXP_REPLACE(LOWER(BTRIM(r.site_address)), '[^a-z0-9]+', '', 'g'), ''),
                COALESCE(LOWER(BTRIM(r.division)), ''),
                COALESCE(r.source_year::TEXT, ''),
                COALESCE(LOWER(BTRIM(r.warranty_category)), ''),
                COALESCE(LOWER(BTRIM(r.warranty_type)), ''),
                COALESCE(LOWER(BTRIM(r.provider)), ''),
                COALESCE(r.duration_years::TEXT, ''),
                COALESCE(r.start_date::TEXT, ''),
                COALESCE(r.expiration_date::TEXT, ''),
                COALESCE(LOWER(BTRIM(r.coverage_summary)), ''),
                COALESCE(LOWER(BTRIM(r.coverage_excerpt)), ''),
                COALESCE(REGEXP_REPLACE(LOWER(BTRIM(COALESCE(
                    NULLIF(r.source_raw ->> 'Contact Name', ''),
                    NULLIF(r.source_raw ->> 'bill_to_contact', ''),
                    CONCAT_WS(
                        ' ',
                        NULLIF(r.source_raw ->> 'contact_first_name', ''),
                        NULLIF(r.source_raw ->> 'contact_last_name', '')
                    )
                ))), '[^a-z0-9]+', '', 'g'), ''),
                COALESCE(LOWER(BTRIM(COALESCE(
                    NULLIF(r.source_raw ->> 'Contact Email', ''),
                    NULLIF(r.source_raw ->> 'bill_to_email_address', ''),
                    NULLIF(r.source_raw ->> 'contact_email', '')
                ))), ''),
                COALESCE(REGEXP_REPLACE(BTRIM(COALESCE(
                    NULLIF(r.source_raw ->> 'Contact Phone', ''),
                    NULLIF(r.source_raw ->> 'bill_to_phone', ''),
                    NULLIF(r.source_raw ->> 'contact_phone', '')
                )), '[^0-9]+', '', 'g'), '')
            ))
        END AS reported_semantic_key
    FROM registry_enriched r
),
identity_resolved AS (
    SELECT
        r.*,
        CASE
            WHEN r.reported_semantic_key IS NOT NULL
              AND COUNT(*) OVER (PARTITION BY r.reported_semantic_key) > 1
            THEN r.reported_semantic_key
            ELSE r.fallback_identity_key
        END AS identity_key
    FROM identity_candidates r
),
ranked AS (
    SELECT
        r.*,
        p.project_name AS vsimple_project_name,
        p.customer_name AS vsimple_customer_name,
        p.warranty_term_raw,
        p.reported_contact_name,
        p.reported_contact_email,
        p.reported_contact_phone,
        p.vsimple_url,
        p.sharepoint_url,
        dc.contact_count,
        dc.contact_names AS related_contact_names,
        dc.contact_emails AS related_contact_emails,
        dc.contact_phones AS related_contact_phones,
        dc.contacts,
        gvc.contact_name AS general_vsimple_contact_name,
        gvc.contact_email AS general_vsimple_contact_email,
        gvc.contact_phone AS general_vsimple_contact_phone,
        gvc.contact_name_source AS general_vsimple_contact_name_source,
        gvc.contact_email_source AS general_vsimple_contact_email_source,
        gvc.contact_phone_source AS general_vsimple_contact_phone_source,
        jc.contact_name AS estimate_contact_name,
        jc.contact_email AS estimate_contact_email,
        jc.contact_phone AS estimate_contact_phone,
        jc.folder_url AS job_folder_url,
        jc.estimate_url,
        jc.estimate_file AS estimate_contact_file,
        COUNT(*) OVER (
            PARTITION BY r.identity_key
        ) AS merged_source_count,
        ROW_NUMBER() OVER (
            PARTITION BY r.identity_key
            ORDER BY
                CASE r.warranty_status WHEN 'issued' THEN 0 ELSE 1 END,
                (r.expiration_date IS NOT NULL) DESC,
                (r.start_date IS NOT NULL) DESC,
                (r.duration_years IS NOT NULL) DESC,
                CASE COALESCE(r.source_system, r.duration_source_kind)
                    WHEN 'sharepoint_warranty_folder' THEN 0
                    WHEN 'warranty_document' THEN 0
                    WHEN 'manufacturer_warranty_list' THEN 1
                    WHEN 'recent_completed_warranty_list' THEN 2
                    WHEN 'vsimple_project_warranty_export' THEN 3
                    WHEN 'legacy_vsimple_warranty_export' THEN 4
                    WHEN 'legacy_customer_list' THEN 5
                    ELSE 6
                END,
                r.refreshed_at DESC NULLS LAST,
                r.warranty_summary_id
        ) AS identity_rank
    FROM identity_resolved r
    LEFT JOIN vsimple_warranty_projects_clean p ON p.vsimple_id = r.resolved_vsimple_id
    LEFT JOIN direct_contacts dc ON dc.vsimple_id = r.resolved_vsimple_id
    LEFT JOIN general_vsimple_contacts gvc ON gvc.vsimple_id = r.resolved_vsimple_id
    LEFT JOIN job_contacts jc ON jc.job_id = r.job_id
),
contact_resolved AS (
    SELECT
        r.*,
        COALESCE(
            related_contact_names,
            NULLIF(BTRIM(reported_contact_name), ''),
            NULLIF(BTRIM(source_raw ->> 'Contact Name'), ''),
            NULLIF(BTRIM(source_raw ->> 'bill_to_contact'), ''),
            NULLIF(BTRIM(CONCAT_WS(
                ' ',
                NULLIF(BTRIM(source_raw ->> 'contact_first_name'), ''),
                NULLIF(BTRIM(source_raw ->> 'contact_last_name'), '')
            )), ''),
            general_vsimple_contact_name,
            estimate_contact_name
        ) AS resolved_contact_names,
        COALESCE(
            related_contact_emails,
            NULLIF(BTRIM(reported_contact_email), ''),
            NULLIF(BTRIM(source_raw ->> 'Contact Email'), ''),
            NULLIF(BTRIM(source_raw ->> 'bill_to_email_address'), ''),
            NULLIF(BTRIM(source_raw ->> 'contact_email'), ''),
            general_vsimple_contact_email,
            estimate_contact_email
        ) AS resolved_contact_emails,
        COALESCE(
            related_contact_phones,
            NULLIF(BTRIM(reported_contact_phone), ''),
            NULLIF(BTRIM(source_raw ->> 'Contact Phone'), ''),
            NULLIF(BTRIM(source_raw ->> 'bill_to_phone'), ''),
            NULLIF(BTRIM(source_raw ->> 'contact_phone'), ''),
            general_vsimple_contact_phone,
            estimate_contact_phone
        ) AS resolved_contact_phones,
        CASE
            WHEN related_contact_names IS NOT NULL THEN 'vsimple_customer_export'
            WHEN NULLIF(BTRIM(reported_contact_name), '') IS NOT NULL THEN 'vsimple_warranty_project_export'
            WHEN COALESCE(
                NULLIF(BTRIM(source_raw ->> 'Contact Name'), ''),
                NULLIF(BTRIM(source_raw ->> 'bill_to_contact'), ''),
                NULLIF(BTRIM(source_raw ->> 'contact_first_name'), ''),
                NULLIF(BTRIM(source_raw ->> 'contact_last_name'), '')
            ) IS NOT NULL THEN 'warranty_source_export'
            WHEN general_vsimple_contact_name IS NOT NULL THEN general_vsimple_contact_name_source
            WHEN estimate_contact_name IS NOT NULL THEN 'estimate_workbook'
        END AS contact_name_source,
        CASE
            WHEN related_contact_emails IS NOT NULL THEN 'vsimple_customer_export'
            WHEN NULLIF(BTRIM(reported_contact_email), '') IS NOT NULL THEN 'vsimple_warranty_project_export'
            WHEN COALESCE(
                NULLIF(BTRIM(source_raw ->> 'Contact Email'), ''),
                NULLIF(BTRIM(source_raw ->> 'bill_to_email_address'), ''),
                NULLIF(BTRIM(source_raw ->> 'contact_email'), '')
            ) IS NOT NULL THEN 'warranty_source_export'
            WHEN general_vsimple_contact_email IS NOT NULL THEN general_vsimple_contact_email_source
            WHEN estimate_contact_email IS NOT NULL THEN 'estimate_workbook'
        END AS contact_email_source,
        CASE
            WHEN related_contact_phones IS NOT NULL THEN 'vsimple_customer_export'
            WHEN NULLIF(BTRIM(reported_contact_phone), '') IS NOT NULL THEN 'vsimple_warranty_project_export'
            WHEN COALESCE(
                NULLIF(BTRIM(source_raw ->> 'Contact Phone'), ''),
                NULLIF(BTRIM(source_raw ->> 'bill_to_phone'), ''),
                NULLIF(BTRIM(source_raw ->> 'contact_phone'), '')
            ) IS NOT NULL THEN 'warranty_source_export'
            WHEN general_vsimple_contact_phone IS NOT NULL THEN general_vsimple_contact_phone_source
            WHEN estimate_contact_phone IS NOT NULL THEN 'estimate_workbook'
        END AS contact_phone_source
    FROM ranked r
)
SELECT
    identity_key AS warranty_master_id,
    warranty_status,
    (
        idoc.job_id IS NOT NULL
        OR COALESCE(source_system, duration_source_kind) = 'sharepoint_warranty_folder'
    ) AS has_issued_document_evidence,
    CASE
        WHEN idoc.job_id IS NOT NULL
          OR COALESCE(source_system, duration_source_kind) = 'sharepoint_warranty_folder'
        THEN 'issued_document'
        ELSE 'reported_source'
    END AS evidence_status,
    (
        idoc.job_id IS NOT NULL
        OR COALESCE(source_system, duration_source_kind) = 'sharepoint_warranty_folder'
        OR COALESCE(source_system, duration_source_kind) IN (
            'recent_completed_warranty_list',
            'vsimple_project_warranty_export',
            'legacy_vsimple_warranty_export',
            'legacy_customer_list',
            'manufacturer_warranty_list'
        )
    ) AS is_reliable_warranty,
    CASE
        WHEN idoc.job_id IS NOT NULL
          OR COALESCE(source_system, duration_source_kind) = 'sharepoint_warranty_folder'
        THEN 'issued_document'
        WHEN COALESCE(source_system, duration_source_kind) IN (
            'recent_completed_warranty_list',
            'vsimple_project_warranty_export',
            'legacy_vsimple_warranty_export',
            'legacy_customer_list',
            'manufacturer_warranty_list'
        ) THEN 'trusted_warranty_sheet'
        ELSE 'estimate_or_proposal_only'
    END AS reliability_basis,
    r.job_id,
    resolved_vsimple_id AS vsimple_id,
    COALESCE(NULLIF(BTRIM(vsimple_project_name), ''), NULLIF(BTRIM(job_name), ''), NULLIF(BTRIM(reported_contact_name), ''))
        AS project_name,
    COALESCE(NULLIF(BTRIM(customer), ''), NULLIF(BTRIM(vsimple_customer_name), '')) AS customer_name,
    division,
    source_year,
    warranty_category,
    warranty_type,
    COALESCE(NULLIF(BTRIM(warranty_term_raw), ''), NULLIF(BTRIM(warranty_type), '')) AS warranty_term,
    provider,
    duration_years,
    start_date,
    start_date_source,
    start_date_confidence,
    start_date_is_inferred,
    expiration_date AS end_date,
    expiration_date_source,
    resolved_contact_names AS contact_names,
    resolved_contact_emails AS contact_emails,
    resolved_contact_phones AS contact_phones,
    contact_name_source,
    contact_email_source,
    contact_phone_source,
    COALESCE(contact_email_source, contact_phone_source, contact_name_source) AS contact_source,
    CASE
        WHEN COALESCE(contact_email_source, contact_phone_source, contact_name_source) = 'estimate_workbook'
            THEN COALESCE(estimate_url, job_folder_url, estimate_contact_file)
        WHEN COALESCE(contact_email_source, contact_phone_source, contact_name_source) IN (
            'vsimple_customer_export', 'vsimple_warranty_project_export', 'vsimple_project_export'
        ) THEN vsimple_url
        ELSE source_url
    END AS contact_source_reference,
    COALESCE(
        contact_count,
        CASE
            WHEN COALESCE(resolved_contact_names, resolved_contact_emails, resolved_contact_phones) IS NOT NULL THEN 1
            ELSE 0
        END
    ) AS contact_count,
    COALESCE(contacts, '[]'::JSONB) AS contacts,
    (
        resolved_contact_emails IS NOT NULL
        OR resolved_contact_phones IS NOT NULL
    ) AS contact_follow_up_ready,
    (
        COALESCE(has_conflict, FALSE)
        OR COALESCE(match_review_required, FALSE)
        OR duration_years IS NULL
        OR start_date IS NULL
        OR expiration_date IS NULL
        OR (
            resolved_contact_emails IS NULL
            AND resolved_contact_phones IS NULL
        )
    ) AS needs_review,
    COALESCE(job_folder_url, NULLIF(BTRIM(sharepoint_url), '')) AS job_link,
    COALESCE(
        idoc.issued_document_url,
        CASE WHEN COALESCE(source_system, duration_source_kind) = 'sharepoint_warranty_folder' THEN source_url END
    ) AS issued_warranty_link,
    COALESCE(
        idoc.issued_document_file,
        CASE WHEN COALESCE(source_system, duration_source_kind) = 'sharepoint_warranty_folder' THEN source_file END
    ) AS issued_warranty_file,
    vsimple_url,
    source_file,
    source_url,
    COALESCE(source_system, duration_source_kind) AS source_kind,
    source_document_id,
    evidence_count,
    issued_evidence_count,
    reported_evidence_count,
    merged_source_count,
    has_conflict,
    match_review_required,
    refreshed_at
FROM contact_resolved r
LEFT JOIN issued_documents idoc ON idoc.job_id = r.job_id
WHERE identity_rank = 1;
