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
        COALESCE(
            'vsimple:' || NULLIF(BTRIM(r.resolved_vsimple_id), ''),
            'job:' || NULLIF(BTRIM(r.job_id), ''),
            'source:' || r.warranty_summary_id
        ) AS identity_key,
        COUNT(*) OVER (
            PARTITION BY COALESCE(
                'vsimple:' || NULLIF(BTRIM(r.resolved_vsimple_id), ''),
                'job:' || NULLIF(BTRIM(r.job_id), ''),
                'source:' || r.warranty_summary_id
            )
        ) AS merged_source_count,
        ROW_NUMBER() OVER (
            PARTITION BY COALESCE(
                'vsimple:' || NULLIF(BTRIM(r.resolved_vsimple_id), ''),
                'job:' || NULLIF(BTRIM(r.job_id), ''),
                'source:' || r.warranty_summary_id
            )
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
    FROM registry_enriched r
    LEFT JOIN vsimple_warranty_projects_clean p ON p.vsimple_id = r.resolved_vsimple_id
    LEFT JOIN direct_contacts dc ON dc.vsimple_id = r.resolved_vsimple_id
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
    COALESCE(
        related_contact_names,
        NULLIF(BTRIM(reported_contact_name), ''),
        NULLIF(BTRIM(source_raw ->> 'Contact Name'), ''),
        NULLIF(BTRIM(source_raw ->> 'bill_to_contact'), ''),
        NULLIF(BTRIM(source_raw ->> 'contact_first_name'), '')
    ) AS contact_names,
    COALESCE(
        related_contact_emails,
        NULLIF(BTRIM(reported_contact_email), ''),
        NULLIF(BTRIM(source_raw ->> 'Contact Email'), ''),
        NULLIF(BTRIM(source_raw ->> 'bill_to_email_address'), ''),
        NULLIF(BTRIM(source_raw ->> 'contact_email'), '')
    ) AS contact_emails,
    COALESCE(
        related_contact_phones,
        NULLIF(BTRIM(reported_contact_phone), ''),
        NULLIF(BTRIM(source_raw ->> 'Contact Phone'), ''),
        NULLIF(BTRIM(source_raw ->> 'bill_to_phone'), ''),
        NULLIF(BTRIM(source_raw ->> 'contact_phone'), '')
    ) AS contact_phones,
    COALESCE(contact_count, CASE WHEN reported_contact_name IS NOT NULL THEN 1 ELSE 0 END) AS contact_count,
    COALESCE(contacts, '[]'::JSONB) AS contacts,
    (
        COALESCE(
            related_contact_emails,
            NULLIF(BTRIM(reported_contact_email), ''),
            NULLIF(BTRIM(source_raw ->> 'Contact Email'), ''),
            NULLIF(BTRIM(source_raw ->> 'bill_to_email_address'), ''),
            NULLIF(BTRIM(source_raw ->> 'contact_email'), '')
        ) IS NOT NULL
        OR COALESCE(
            related_contact_phones,
            NULLIF(BTRIM(reported_contact_phone), ''),
            NULLIF(BTRIM(source_raw ->> 'Contact Phone'), ''),
            NULLIF(BTRIM(source_raw ->> 'bill_to_phone'), ''),
            NULLIF(BTRIM(source_raw ->> 'contact_phone'), '')
        ) IS NOT NULL
    ) AS contact_follow_up_ready,
    (
        COALESCE(has_conflict, FALSE)
        OR COALESCE(match_review_required, FALSE)
        OR duration_years IS NULL
        OR start_date IS NULL
        OR expiration_date IS NULL
        OR (
            COALESCE(
                related_contact_emails,
                NULLIF(BTRIM(reported_contact_email), ''),
                NULLIF(BTRIM(source_raw ->> 'Contact Email'), ''),
                NULLIF(BTRIM(source_raw ->> 'bill_to_email_address'), ''),
                NULLIF(BTRIM(source_raw ->> 'contact_email'), '')
            ) IS NULL
            AND COALESCE(
                related_contact_phones,
                NULLIF(BTRIM(reported_contact_phone), ''),
                NULLIF(BTRIM(source_raw ->> 'Contact Phone'), ''),
                NULLIF(BTRIM(source_raw ->> 'bill_to_phone'), ''),
                NULLIF(BTRIM(source_raw ->> 'contact_phone'), '')
            ) IS NULL
        )
    ) AS needs_review,
    COALESCE(NULLIF(BTRIM(j.folder_url), ''), NULLIF(BTRIM(sharepoint_url), '')) AS job_link,
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
FROM ranked r
LEFT JOIN issued_documents idoc ON idoc.job_id = r.job_id
LEFT JOIN (
    SELECT job_id AS linked_job_id, folder_url
    FROM jobs
) j ON j.linked_job_id = r.job_id
WHERE identity_rank = 1;
