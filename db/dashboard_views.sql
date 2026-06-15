-- Dashboard Views for Spray-Tec Ops Database
-- Conservative version based on current schema.

DROP VIEW IF EXISTS dashboard_line_item_rollup CASCADE;
DROP VIEW IF EXISTS dashboard_stamp_tracking CASCADE;
DROP VIEW IF EXISTS dashboard_job_warnings CASCADE;
DROP VIEW IF EXISTS dashboard_pipeline_rollup CASCADE;
DROP VIEW IF EXISTS dashboard_estimate_line_items CASCADE;
DROP VIEW IF EXISTS dashboard_estimates CASCADE;
DROP VIEW IF EXISTS dashboard_jobs CASCADE;


-- ============================================================
-- Jobs
-- ============================================================

CREATE OR REPLACE VIEW dashboard_jobs AS
SELECT
    j.job_id,
    j.division,
    j.pipeline_status,
    j.status,
    j.customer,
    j.job_name,
    j.job_type,
    j.site_address,
    j.city,
    j.state,
    j.zip_code,
    j.estimated_sqft,

    j.final_price,
    j.total_job_cost,

    COALESCE(j.final_price, j.total_job_cost) AS estimated_value,

    CASE
        WHEN j.final_price IS NOT NULL THEN 'final_price'
        WHEN j.total_job_cost IS NOT NULL THEN 'total_job_cost'
        ELSE NULL
    END AS estimated_value_source,

    j.price_per_sqft,
    j.material_subtotal,
    j.labor_subtotal,

    j.has_signed_contract,
    j.has_invoice,
    j.has_warranty,
    j.has_proposal,
    j.has_job_spec,
    j.has_aerial,
    j.has_notes,

    j.photo_count,
    j.folder_name,
    j.folder_path,
    j.folder_url,
    j.estimate_file,

    j.warnings,
    CASE
        WHEN COALESCE(TRIM(j.warnings), '') <> '' THEN TRUE
        ELSE FALSE
    END AS has_warnings,

    CASE
        WHEN j.pipeline_status = 'Completed' AND COALESCE(j.has_invoice, FALSE) = FALSE THEN TRUE
        ELSE FALSE
    END AS completed_missing_invoice,

    CASE
        WHEN j.pipeline_status = 'Completed' AND j.final_price IS NULL THEN TRUE
        ELSE FALSE
    END AS completed_missing_final_price,

    CASE
        WHEN COALESCE(j.has_signed_contract, FALSE) = FALSE THEN TRUE
        ELSE FALSE
    END AS missing_signed_contract,

    CASE
        WHEN COALESCE(j.has_job_spec, FALSE) = FALSE THEN TRUE
        ELSE FALSE
    END AS missing_job_spec,

    j.last_scanned_at,
    j.scan_root,
    j.source_year,
    j.updated_at
FROM jobs j;


-- ============================================================
-- Estimates
-- ============================================================

CREATE OR REPLACE VIEW dashboard_estimates AS
SELECT
    e.estimate_id,
    e.job_id,
    e.estimate_file,
    e.estimate_role,
    e.estimate_scope_type,

    e.division,
    e.pipeline_status,
    e.customer,
    e.job_name,
    e.job_type,

    e.estimated_sqft,

    e.final_price,
    e.worksheet_price,
    e.total_job_cost,

    COALESCE(e.final_price, e.worksheet_price, e.total_job_cost) AS estimated_value,

    CASE
        WHEN e.final_price IS NOT NULL THEN 'final_price'
        WHEN e.worksheet_price IS NOT NULL THEN 'worksheet_price'
        WHEN e.total_job_cost IS NOT NULL THEN 'total_job_cost'
        ELSE NULL
    END AS estimated_value_source,

    e.price_per_sqft,

    e.material_subtotal,
    e.labor_subtotal,
    e.equipment_subtotal,
    e.subcontractor_subtotal,
    e.travel_lodging,

    e.estimated_duration_days,
    e.estimated_labor_hours,
    e.estimated_crew_size,
    e.estimated_hours_per_day,

    e.adders_subtotal,
    e.warranty_amount,
    e.insurance_amount,
    e.equipment_rental_amount,
    e.subcontractor_amount,
    e.misc_materials_amount,

    e.source_path,
    e.extraction_warnings,

    CASE
        WHEN COALESCE(TRIM(e.extraction_warnings), '') <> '' THEN TRUE
        ELSE FALSE
    END AS has_extraction_warnings,

    e.updated_at
FROM estimates e;


-- ============================================================
-- Estimate line items
-- ============================================================

CREATE OR REPLACE VIEW dashboard_estimate_line_items AS
SELECT
    li.line_item_id,
    li.estimate_id,
    li.job_id,

    li.division,
    li.pipeline_status,
    li.customer,
    li.job_name,
    li.estimate_file,

    li.section,
    li.line_item_category,
    li.line_item_name,
    li.description,

    li.quantity,
    li.unit,
    li.unit_cost,
    li.unit_price,
    li.extended_cost,
    li.markup_pct,

    li.labor_days,
    li.crew_size,
    li.labor_hours,

    li.vendor,
    li.notes,
    li.source_sheet,
    li.source_row,

    li.updated_at
FROM estimate_line_items li;


-- ============================================================
-- Pipeline rollup
-- ============================================================

CREATE OR REPLACE VIEW dashboard_pipeline_rollup AS
SELECT
    division,
    pipeline_status,
    COUNT(*) AS job_count,
    SUM(estimated_value) AS total_estimated_value,
    AVG(estimated_value) AS avg_estimated_value,
    SUM(CASE WHEN has_warnings THEN 1 ELSE 0 END) AS jobs_with_warnings,
    SUM(CASE WHEN completed_missing_invoice THEN 1 ELSE 0 END) AS completed_missing_invoice_count,
    SUM(CASE WHEN completed_missing_final_price THEN 1 ELSE 0 END) AS completed_missing_final_price_count,
    SUM(CASE WHEN has_aerial THEN 1 ELSE 0 END) AS jobs_with_aerial,
    SUM(COALESCE(photo_count, 0)) AS total_photos
FROM dashboard_jobs
GROUP BY division, pipeline_status;


-- ============================================================
-- Job warnings
-- ============================================================

CREATE OR REPLACE VIEW dashboard_job_warnings AS
SELECT
    job_id,
    division,
    pipeline_status,
    status,
    customer,
    job_name,
    folder_url,
    warnings,

    completed_missing_invoice,
    completed_missing_final_price,
    missing_signed_contract,
    missing_job_spec,

    has_invoice,
    has_signed_contract,
    has_job_spec,
    has_aerial,
    photo_count,
    estimated_value,
    updated_at
FROM dashboard_jobs
WHERE
    has_warnings = TRUE
    OR completed_missing_invoice = TRUE
    OR completed_missing_final_price = TRUE
    OR missing_signed_contract = TRUE
    OR missing_job_spec = TRUE;


-- ============================================================
-- STAMP tracking
-- ============================================================

CREATE OR REPLACE VIEW dashboard_stamp_tracking AS
SELECT
    e.estimate_id,
    e.job_id,
    e.division,
    e.pipeline_status,
    e.customer,
    e.job_name,
    e.estimate_file,
    e.estimate_role,
    e.estimate_scope_type,
    e.estimated_value,
    e.final_price,
    e.worksheet_price,
    e.total_job_cost,
    e.estimated_duration_days,
    e.estimated_labor_hours,
    e.estimated_crew_size,
    e.source_path,
    e.extraction_warnings,
    e.updated_at
FROM dashboard_estimates e
WHERE
    LOWER(COALESCE(e.estimate_file, '')) LIKE '%stamp%'
    OR LOWER(COALESCE(e.estimate_scope_type, '')) LIKE '%stamp%'
    OR LOWER(COALESCE(e.job_type, '')) LIKE '%stamp%';


-- ============================================================
-- Line item rollup
-- ============================================================

CREATE OR REPLACE VIEW dashboard_line_item_rollup AS
SELECT
    division,
    pipeline_status,
    section,
    line_item_category,
    COUNT(*) AS line_item_count,
    SUM(extended_cost) AS total_extended_cost,
    AVG(extended_cost) AS avg_extended_cost,
    SUM(labor_hours) AS total_labor_hours,
    SUM(labor_days) AS total_labor_days
FROM dashboard_estimate_line_items
GROUP BY
    division,
    pipeline_status,
    section,
    line_item_category;

CREATE OR REPLACE VIEW dashboard_pipeline_rollup_enriched AS
SELECT
    division,
    pipeline_status,
    COUNT(*) AS job_count,
    COUNT(estimated_value) AS jobs_with_estimated_value,
    COUNT(*) - COUNT(estimated_value) AS jobs_missing_estimated_value,
    SUM(estimated_value) AS total_estimated_value,
    AVG(estimated_value) AS avg_estimated_value,
    MIN(estimated_value) AS min_estimated_value,
    MAX(estimated_value) AS max_estimated_value,
    SUM(CASE WHEN has_warnings THEN 1 ELSE 0 END) AS jobs_with_warnings,
    SUM(CASE WHEN completed_missing_invoice THEN 1 ELSE 0 END) AS completed_missing_invoice_count,
    SUM(CASE WHEN completed_missing_final_price THEN 1 ELSE 0 END) AS completed_missing_final_price_count,
    SUM(CASE WHEN missing_signed_contract THEN 1 ELSE 0 END) AS missing_signed_contract_count,
    SUM(CASE WHEN missing_job_spec THEN 1 ELSE 0 END) AS missing_job_spec_count,
    SUM(CASE WHEN has_aerial THEN 1 ELSE 0 END) AS jobs_with_aerial,
    SUM(COALESCE(photo_count, 0)) AS total_photos
FROM dashboard_jobs
GROUP BY division, pipeline_status;


-- ============================================================
-- Additional Owner / Operations Dashboard Views
-- ============================================================


-- ============================================================
-- Owner overview
-- One-row executive summary.
-- ============================================================

CREATE OR REPLACE VIEW dashboard_owner_overview AS
SELECT
    COUNT(*) AS total_jobs,

    SUM(estimated_value) AS total_pipeline_value,

    SUM(CASE WHEN pipeline_status = 'Proposed'
        THEN estimated_value ELSE 0 END) AS proposed_value,

    SUM(CASE WHEN pipeline_status IN ('Contracted', 'Contracted Repairs')
        THEN estimated_value ELSE 0 END) AS contracted_value,

    SUM(CASE WHEN pipeline_status = 'Completed'
        THEN estimated_value ELSE 0 END) AS completed_value,

    SUM(CASE WHEN pipeline_status = 'Proposed'
        THEN 1 ELSE 0 END) AS proposed_jobs,

    SUM(CASE WHEN pipeline_status IN ('Contracted', 'Contracted Repairs')
        THEN 1 ELSE 0 END) AS contracted_jobs,

    SUM(CASE WHEN pipeline_status = 'Completed'
        THEN 1 ELSE 0 END) AS completed_jobs,

    SUM(CASE WHEN has_warnings
        THEN 1 ELSE 0 END) AS jobs_with_warnings,

    SUM(CASE WHEN completed_missing_invoice
        THEN 1 ELSE 0 END) AS completed_missing_invoice_count,

    SUM(CASE WHEN completed_missing_final_price
        THEN 1 ELSE 0 END) AS completed_missing_final_price_count,

    SUM(CASE WHEN missing_signed_contract
        THEN 1 ELSE 0 END) AS missing_signed_contract_count,

    SUM(CASE WHEN missing_job_spec
        THEN 1 ELSE 0 END) AS missing_job_spec_count,

    SUM(CASE WHEN has_aerial
        THEN 1 ELSE 0 END) AS jobs_with_aerial,

    SUM(COALESCE(photo_count, 0)) AS total_photos
FROM dashboard_jobs;


-- ============================================================
-- Top open jobs
-- Proposed / contracted jobs ranked by value.
-- ============================================================

CREATE OR REPLACE VIEW dashboard_top_open_jobs AS
SELECT
    job_id,
    division,
    pipeline_status,
    status,
    customer,
    job_name,
    job_type,
    estimated_value,
    estimated_sqft,
    price_per_sqft,
    has_warnings,
    warnings,
    COALESCE(folder_url, folder_path) AS folder_link_or_path,
    folder_url,
    folder_path,
    updated_at
FROM dashboard_jobs
WHERE pipeline_status IN ('Proposed', 'Contracted', 'Contracted Repairs')
ORDER BY estimated_value DESC NULLS LAST;


-- ============================================================
-- Jobs needing action
-- Owner-friendly punch list.
-- ============================================================

CREATE OR REPLACE VIEW dashboard_jobs_needing_action AS
SELECT
    job_id,
    division,
    pipeline_status,
    status,
    customer,
    job_name,
    job_type,
    estimated_value,
    estimated_sqft,
    price_per_sqft,
    COALESCE(folder_url, folder_path) AS folder_link_or_path,
    folder_url,
    folder_path,
    warnings,

    CASE
        WHEN completed_missing_invoice THEN 'Completed missing invoice'
        WHEN completed_missing_final_price THEN 'Completed missing final price'
        WHEN missing_signed_contract THEN 'Missing signed contract'
        WHEN missing_job_spec THEN 'Missing job spec'
        WHEN has_warnings THEN 'Review warning'
        ELSE 'Review'
    END AS action_needed,

    updated_at
FROM dashboard_jobs
WHERE
    completed_missing_invoice = TRUE
    OR completed_missing_final_price = TRUE
    OR missing_signed_contract = TRUE
    OR missing_job_spec = TRUE
    OR has_warnings = TRUE;


-- ============================================================
-- Contracted backlog
-- Work that has been won but still needs scheduling/completion.
-- ============================================================

CREATE OR REPLACE VIEW dashboard_contracted_backlog AS
SELECT
    j.job_id,
    j.division,
    j.pipeline_status,
    j.status,
    j.customer,
    j.job_name,
    j.job_type,
    j.estimated_value,
    j.estimated_sqft,
    j.price_per_sqft,

    e.estimated_duration_days,
    e.estimated_labor_hours,
    e.estimated_crew_size,
    e.estimated_hours_per_day,

    j.has_warnings,
    j.warnings,
    COALESCE(j.folder_url, j.folder_path) AS folder_link_or_path,
    j.folder_url,
    j.folder_path,
    j.updated_at
FROM dashboard_jobs j
LEFT JOIN dashboard_estimates e
    ON j.job_id = e.job_id
   AND COALESCE(e.estimate_role, 'primary') = 'primary'
WHERE j.pipeline_status IN ('Contracted', 'Contracted Repairs');


-- ============================================================
-- Estimate quality issues
-- Jobs where estimate data looks incomplete or suspicious.
-- ============================================================

CREATE OR REPLACE VIEW dashboard_estimate_quality_issues AS
SELECT
    j.job_id,
    j.division,
    j.pipeline_status,
    j.status,
    j.customer,
    j.job_name,
    j.job_type,
    j.estimated_value,
    j.estimated_sqft,
    j.price_per_sqft,
    j.material_subtotal,
    j.labor_subtotal,
    j.estimate_file,
    COALESCE(j.folder_url, j.folder_path) AS folder_link_or_path,
    j.folder_url,
    j.folder_path,

    CASE
        WHEN j.estimate_file IS NULL THEN 'No estimate workbook found'
        WHEN j.estimated_value IS NULL THEN 'Missing estimated value'
        WHEN j.estimated_sqft IS NULL THEN 'Missing square footage'
        WHEN LOWER(COALESCE(j.job_type, '')) LIKE '%roof%'
             AND COALESCE(j.labor_subtotal, 0) = 0
        THEN 'Roof job with zero labor subtotal'
        WHEN j.price_per_sqft IS NULL THEN 'Missing price per sqft'
        ELSE NULL
    END AS estimate_issue,

    j.updated_at
FROM dashboard_jobs j
WHERE
    j.estimate_file IS NULL
    OR j.estimated_value IS NULL
    OR j.estimated_sqft IS NULL
    OR j.price_per_sqft IS NULL
    OR (
        LOWER(COALESCE(j.job_type, '')) LIKE '%roof%'
        AND COALESCE(j.labor_subtotal, 0) = 0
    );


-- ============================================================
-- Division summary
-- Top-level performance by division.
-- ============================================================

CREATE OR REPLACE VIEW dashboard_division_summary AS
SELECT
    division,
    COUNT(*) AS job_count,
    COUNT(estimated_value) AS jobs_with_estimated_value,
    COUNT(*) - COUNT(estimated_value) AS jobs_missing_estimated_value,
    SUM(estimated_value) AS total_estimated_value,
    AVG(estimated_value) AS avg_estimated_value,
    AVG(price_per_sqft) AS avg_price_per_sqft,
    SUM(COALESCE(photo_count, 0)) AS total_photos,
    SUM(CASE WHEN has_warnings THEN 1 ELSE 0 END) AS jobs_with_warnings,
    SUM(CASE WHEN has_invoice THEN 1 ELSE 0 END) AS jobs_with_invoice,
    SUM(CASE WHEN has_signed_contract THEN 1 ELSE 0 END) AS jobs_with_signed_contract,
    SUM(CASE WHEN has_aerial THEN 1 ELSE 0 END) AS jobs_with_aerial
FROM dashboard_jobs
GROUP BY division;


-- ============================================================
-- Documentation summary
-- Photos, aerials, and missing documentation.
-- ============================================================

CREATE OR REPLACE VIEW dashboard_documentation_summary AS
SELECT
    division,
    pipeline_status,
    COUNT(*) AS job_count,
    SUM(COALESCE(photo_count, 0)) AS total_photos,
    SUM(CASE WHEN COALESCE(photo_count, 0) > 0 THEN 1 ELSE 0 END) AS jobs_with_photos,
    SUM(CASE WHEN COALESCE(photo_count, 0) = 0 THEN 1 ELSE 0 END) AS jobs_without_photos,
    SUM(CASE WHEN has_aerial THEN 1 ELSE 0 END) AS jobs_with_aerial,
    SUM(CASE WHEN NOT has_aerial THEN 1 ELSE 0 END) AS jobs_without_aerial,
    SUM(CASE WHEN has_job_spec THEN 1 ELSE 0 END) AS jobs_with_job_spec,
    SUM(CASE WHEN NOT has_job_spec THEN 1 ELSE 0 END) AS jobs_without_job_spec,
    SUM(CASE WHEN has_signed_contract THEN 1 ELSE 0 END) AS jobs_with_signed_contract,
    SUM(CASE WHEN NOT has_signed_contract THEN 1 ELSE 0 END) AS jobs_without_signed_contract
FROM dashboard_jobs
GROUP BY division, pipeline_status;


-- ============================================================
-- High-value jobs missing documentation
-- Useful for owner review.
-- ============================================================

CREATE OR REPLACE VIEW dashboard_high_value_missing_docs AS
SELECT
    job_id,
    division,
    pipeline_status,
    status,
    customer,
    job_name,
    job_type,
    estimated_value,
    photo_count,
    has_aerial,
    has_job_spec,
    has_signed_contract,
    has_invoice,
    COALESCE(folder_url, folder_path) AS folder_link_or_path,
    folder_url,
    folder_path,

    CASE
        WHEN COALESCE(photo_count, 0) = 0 THEN 'Missing photos'
        WHEN NOT has_aerial THEN 'Missing aerial/drone'
        WHEN NOT has_job_spec THEN 'Missing job spec'
        WHEN NOT has_signed_contract THEN 'Missing signed contract'
        WHEN pipeline_status = 'Completed' AND NOT has_invoice THEN 'Completed missing invoice'
        ELSE 'Review documentation'
    END AS documentation_issue,

    updated_at
FROM dashboard_jobs
WHERE
    estimated_value >= 25000
    AND (
        COALESCE(photo_count, 0) = 0
        OR NOT has_aerial
        OR NOT has_job_spec
        OR NOT has_signed_contract
        OR (pipeline_status = 'Completed' AND NOT has_invoice)
    )
ORDER BY estimated_value DESC NULLS LAST;


-- ============================================================
-- Estimate economics by job type
-- Useful for pricing/estimating analysis.
-- ============================================================

CREATE OR REPLACE VIEW dashboard_estimate_economics_by_job_type AS
SELECT
    division,
    job_type,
    COUNT(*) AS estimate_count,
    COUNT(estimated_value) AS estimates_with_value,
    SUM(estimated_value) AS total_estimated_value,
    AVG(estimated_value) AS avg_estimated_value,
    AVG(price_per_sqft) AS avg_price_per_sqft,
    SUM(estimated_labor_hours) AS total_estimated_labor_hours,
    AVG(estimated_labor_hours) AS avg_estimated_labor_hours,
    SUM(estimated_duration_days) AS total_estimated_duration_days,
    AVG(estimated_duration_days) AS avg_estimated_duration_days,
    AVG(estimated_crew_size) AS avg_estimated_crew_size
FROM dashboard_estimates
GROUP BY division, job_type;


-- ============================================================
-- Estimate adders
-- Warranty, insurance, lift rental, subcontractors, misc, etc.
-- ============================================================

CREATE OR REPLACE VIEW dashboard_estimate_adders AS
SELECT
    li.line_item_id,
    li.estimate_id,
    li.job_id,
    li.division,
    li.pipeline_status,
    li.customer,
    li.job_name,
    li.estimate_file,
    li.section,
    li.line_item_category,
    li.line_item_name,
    li.description,
    li.extended_cost,
    li.labor_hours,
    li.source_sheet,
    li.source_row,
    li.updated_at
FROM dashboard_estimate_line_items li
WHERE
    LOWER(COALESCE(li.section, '')) LIKE '%adder%'
    OR LOWER(COALESCE(li.section, '')) LIKE '%misc%'
    OR LOWER(COALESCE(li.line_item_category, '')) IN (
        'warranty',
        'insurance',
        'equipment rental',
        'rental / site services',
        'subcontractor',
        'materials',
        'labor',
        'misc'
    )
    OR LOWER(COALESCE(li.line_item_name, '')) LIKE '%warranty%'
    OR LOWER(COALESCE(li.line_item_name, '')) LIKE '%insurance%'
    OR LOWER(COALESCE(li.line_item_name, '')) LIKE '%porta%'
    OR LOWER(COALESCE(li.line_item_name, '')) LIKE '%lift%'
    OR LOWER(COALESCE(li.line_item_name, '')) LIKE '%subcontractor%'
    OR LOWER(COALESCE(li.line_item_name, '')) LIKE '%rustnox%'
    OR LOWER(COALESCE(li.line_item_name, '')) LIKE '%caulk%';


-- ============================================================
-- Adder rollup
-- ============================================================

CREATE OR REPLACE VIEW dashboard_adder_rollup AS
SELECT
    division,
    pipeline_status,
    line_item_category,
    COUNT(*) AS adder_line_count,
    SUM(extended_cost) AS total_adder_cost,
    AVG(extended_cost) AS avg_adder_cost,
    SUM(labor_hours) AS total_adder_labor_hours
FROM dashboard_estimate_adders
GROUP BY
    division,
    pipeline_status,
    line_item_category;


-- ============================================================
-- Value bands
-- Helps owner see job mix by size.
-- ============================================================

CREATE OR REPLACE VIEW dashboard_job_value_bands AS
SELECT
    division,
    pipeline_status,
    CASE
        WHEN estimated_value IS NULL THEN 'Missing value'
        WHEN estimated_value < 10000 THEN '< $10k'
        WHEN estimated_value < 25000 THEN '$10k - $25k'
        WHEN estimated_value < 50000 THEN '$25k - $50k'
        WHEN estimated_value < 100000 THEN '$50k - $100k'
        WHEN estimated_value < 250000 THEN '$100k - $250k'
        ELSE '$250k+'
    END AS value_band,
    COUNT(*) AS job_count,
    SUM(estimated_value) AS total_estimated_value
FROM dashboard_jobs
GROUP BY
    division,
    pipeline_status,
    CASE
        WHEN estimated_value IS NULL THEN 'Missing value'
        WHEN estimated_value < 10000 THEN '< $10k'
        WHEN estimated_value < 25000 THEN '$10k - $25k'
        WHEN estimated_value < 50000 THEN '$25k - $50k'
        WHEN estimated_value < 100000 THEN '$50k - $100k'
        WHEN estimated_value < 250000 THEN '$100k - $250k'
        ELSE '$250k+'
    END;



-- ============================================================
-- Clean / Actionable Dashboard Views
-- ============================================================

-- Warnings with actual warning text only.
CREATE OR REPLACE VIEW dashboard_job_warnings_actionable AS
SELECT
    job_id,
    division,
    pipeline_status,
    status,
    customer,
    job_name,
    folder_url,
    folder_path,
    COALESCE(folder_url, folder_path) AS folder_link_or_path,
    warnings,
    estimated_value,
    updated_at
FROM dashboard_jobs
WHERE COALESCE(TRIM(warnings), '') <> '';


-- Jobs needing action includes derived flags, even if warnings text is blank.
-- This is separate from true warning text.
CREATE OR REPLACE VIEW dashboard_jobs_needing_action_clean AS
SELECT
    job_id,
    division,
    pipeline_status,
    status,
    customer,
    job_name,
    job_type,
    estimated_value,
    estimated_sqft,
    price_per_sqft,
    COALESCE(folder_url, folder_path) AS folder_link_or_path,
    folder_url,
    folder_path,
    warnings,

    CASE
        WHEN completed_missing_invoice THEN 'Completed missing invoice'
        WHEN completed_missing_final_price THEN 'Completed missing final price'
        WHEN missing_signed_contract THEN 'Missing signed contract'
        WHEN missing_job_spec THEN 'Missing job spec'
        WHEN COALESCE(TRIM(warnings), '') <> '' THEN 'Review warning'
        ELSE 'Review'
    END AS action_needed,

    updated_at
FROM dashboard_jobs
WHERE
    completed_missing_invoice = TRUE
    OR completed_missing_final_price = TRUE
    OR missing_signed_contract = TRUE
    OR missing_job_spec = TRUE
    OR COALESCE(TRIM(warnings), '') <> '';


-- Clean estimate line items:
-- removes duplicate extracted rows and workbook total/summary rows.
CREATE OR REPLACE VIEW dashboard_estimate_line_items_clean AS
SELECT DISTINCT ON (
    estimate_id,
    estimate_file,
    source_sheet,
    source_row,
    section,
    line_item_category,
    line_item_name,
    description,
    quantity,
    unit,
    extended_cost,
    labor_hours
)
    line_item_id,
    estimate_id,
    job_id,
    division,
    pipeline_status,
    customer,
    job_name,
    estimate_file,
    section,
    line_item_category,
    line_item_name,
    description,
    quantity,
    unit,
    unit_cost,
    unit_price,
    extended_cost,
    markup_pct,
    labor_days,
    crew_size,
    labor_hours,
    vendor,
    notes,
    source_sheet,
    source_row,
    updated_at
FROM dashboard_estimate_line_items
WHERE
    NOT (
        LOWER(COALESCE(line_item_name, '')) LIKE '%work sheet price%'
        OR LOWER(COALESCE(description, '')) LIKE '%work sheet price%'
        OR LOWER(COALESCE(line_item_name, '')) LIKE '%rough est%'
        OR LOWER(COALESCE(description, '')) LIKE '%rough est%'
        OR LOWER(COALESCE(line_item_name, '')) LIKE '%total job cost%'
        OR LOWER(COALESCE(description, '')) LIKE '%total job cost%'
        OR LOWER(COALESCE(line_item_name, '')) LIKE '%estimated o/h%'
        OR LOWER(COALESCE(description, '')) LIKE '%estimated o/h%'
        OR LOWER(COALESCE(line_item_name, '')) = 'profit'
        OR LOWER(COALESCE(description, '')) = 'profit'
        OR LOWER(COALESCE(line_item_name, '')) LIKE 'subtotal%'
        OR LOWER(COALESCE(description, '')) LIKE 'subtotal%'
    )
ORDER BY
    estimate_id,
    estimate_file,
    source_sheet,
    source_row,
    section,
    line_item_category,
    line_item_name,
    description,
    quantity,
    unit,
    extended_cost,
    labor_hours,
    line_item_id;


-- Clean estimate adders:
-- starts from clean line items and keeps only true adder-like rows.
CREATE OR REPLACE VIEW dashboard_estimate_adders_clean AS
SELECT
    line_item_id,
    estimate_id,
    job_id,
    division,
    pipeline_status,
    customer,
    job_name,
    estimate_file,
    section,
    line_item_category,
    line_item_name,
    description,
    extended_cost,
    labor_hours,
    source_sheet,
    source_row,
    updated_at
FROM dashboard_estimate_line_items_clean
WHERE
    LOWER(COALESCE(section, '')) LIKE '%adder%'
    OR LOWER(COALESCE(section, '')) LIKE '%misc%'
    OR LOWER(COALESCE(line_item_category, '')) IN (
        'warranty',
        'insurance',
        'equipment rental',
        'rental / site services',
        'subcontractor',
        'materials',
        'labor',
        'misc'
    )
    OR LOWER(COALESCE(line_item_name, '')) LIKE '%warranty%'
    OR LOWER(COALESCE(line_item_name, '')) LIKE '%insurance%'
    OR LOWER(COALESCE(line_item_name, '')) LIKE '%porta%'
    OR LOWER(COALESCE(line_item_name, '')) LIKE '%lift%'
    OR LOWER(COALESCE(line_item_name, '')) LIKE '%subcontractor%'
    OR LOWER(COALESCE(line_item_name, '')) LIKE '%rustnox%'
    OR LOWER(COALESCE(line_item_name, '')) LIKE '%caulk%';


CREATE OR REPLACE VIEW dashboard_adder_rollup_clean AS
SELECT
    division,
    pipeline_status,
    line_item_category,
    COUNT(*) AS adder_line_count,
    SUM(extended_cost) AS total_adder_cost,
    AVG(extended_cost) AS avg_adder_cost,
    SUM(labor_hours) AS total_adder_labor_hours
FROM dashboard_estimate_adders_clean
GROUP BY
    division,
    pipeline_status,
    line_item_category;


CREATE OR REPLACE VIEW dashboard_line_item_rollup_clean AS
SELECT
    division,
    pipeline_status,
    section,
    line_item_category,
    COUNT(*) AS line_item_count,
    SUM(extended_cost) AS total_extended_cost,
    AVG(extended_cost) AS avg_extended_cost,
    SUM(labor_hours) AS total_labor_hours,
    SUM(labor_days) AS total_labor_days
FROM dashboard_estimate_line_items_clean
GROUP BY
    division,
    pipeline_status,
    section,
    line_item_category;


-- ============================================================
-- Owner Decision Views
-- Overrides and additions for cleaner owner-level dashboard pages.
-- ============================================================

CREATE OR REPLACE VIEW dashboard_jobs_needing_action_clean AS
SELECT
    job_id,
    division,
    pipeline_status,
    status,
    customer,
    job_name,
    job_type,
    estimated_value,
    estimated_sqft,
    price_per_sqft,
    COALESCE(folder_url, folder_path) AS folder_link_or_path,
    folder_url,
    folder_path,
    warnings,
    CASE
        WHEN pipeline_status = 'Completed' AND COALESCE(has_invoice, FALSE) = FALSE
            THEN 'Completed missing invoice'
        WHEN pipeline_status = 'Completed' AND final_price IS NULL
            THEN 'Completed missing final price'
        WHEN (
                pipeline_status IN ('Contracted', 'Contracted Repairs', 'Completed')
                OR status IN ('Contracted', 'Invoiced', 'Completed')
             )
             AND COALESCE(has_signed_contract, FALSE) = FALSE
            THEN 'Missing signed contract'
        WHEN (
                pipeline_status IN ('Contracted', 'Contracted Repairs', 'Completed')
                OR COALESCE(estimated_value, 0) >= 25000
             )
             AND COALESCE(has_job_spec, FALSE) = FALSE
            THEN 'Missing job spec'
        WHEN COALESCE(TRIM(warnings), '') <> ''
            THEN 'Review warning'
        ELSE 'Review'
    END AS action_needed,
    updated_at
FROM dashboard_jobs
WHERE
    (pipeline_status = 'Completed' AND COALESCE(has_invoice, FALSE) = FALSE)
    OR (pipeline_status = 'Completed' AND final_price IS NULL)
    OR (
        (
            pipeline_status IN ('Contracted', 'Contracted Repairs', 'Completed')
            OR status IN ('Contracted', 'Invoiced', 'Completed')
        )
        AND COALESCE(has_signed_contract, FALSE) = FALSE
    )
    OR (
        (
            pipeline_status IN ('Contracted', 'Contracted Repairs', 'Completed')
            OR COALESCE(estimated_value, 0) >= 25000
        )
        AND COALESCE(has_job_spec, FALSE) = FALSE
    )
    OR COALESCE(TRIM(warnings), '') <> '';


CREATE OR REPLACE VIEW dashboard_closeout_billing_risk AS
SELECT
    job_id,
    division,
    pipeline_status,
    status,
    customer,
    job_name,
    job_type,
    estimated_value,
    final_price,
    total_job_cost,
    has_invoice,
    has_signed_contract,
    has_warranty,
    has_job_spec,
    photo_count,
    has_aerial,
    warnings,
    COALESCE(folder_url, folder_path) AS folder_link_or_path,
    folder_url,
    folder_path,
    CASE
        WHEN pipeline_status = 'Completed' AND COALESCE(has_invoice, FALSE) = FALSE
            THEN 'Completed missing invoice'
        WHEN pipeline_status = 'Completed' AND final_price IS NULL
            THEN 'Completed missing final price'
        WHEN COALESCE(TRIM(warnings), '') ILIKE '%does not match invoice amount%'
            THEN 'Invoice amount differs from final price'
        WHEN pipeline_status = 'Completed' AND COALESCE(has_signed_contract, FALSE) = FALSE
            THEN 'Completed missing signed contract'
        WHEN pipeline_status = 'Completed' AND COALESCE(has_warranty, FALSE) = FALSE
            THEN 'Completed missing warranty'
        WHEN pipeline_status = 'Completed' AND COALESCE(photo_count, 0) = 0
            THEN 'Completed missing photos'
        ELSE 'Review closeout'
    END AS closeout_issue,
    updated_at
FROM dashboard_jobs
WHERE
    pipeline_status = 'Completed'
    AND (
        COALESCE(has_invoice, FALSE) = FALSE
        OR final_price IS NULL
        OR COALESCE(TRIM(warnings), '') ILIKE '%does not match invoice amount%'
        OR COALESCE(has_signed_contract, FALSE) = FALSE
        OR COALESCE(has_warranty, FALSE) = FALSE
        OR COALESCE(photo_count, 0) = 0
    );


CREATE OR REPLACE VIEW dashboard_closeout_billing_risk_rollup AS
SELECT
    division,
    closeout_issue,
    COUNT(*) AS job_count,
    SUM(estimated_value) AS total_estimated_value
FROM dashboard_closeout_billing_risk
GROUP BY division, closeout_issue;


CREATE OR REPLACE VIEW dashboard_contracted_backlog_summary AS
SELECT
    division,
    COUNT(*) AS contracted_job_count,
    SUM(estimated_value) AS contracted_backlog_value,
    SUM(estimated_labor_hours) AS estimated_labor_hours,
    SUM(estimated_duration_days) AS estimated_duration_days,
    SUM(CASE WHEN estimated_duration_days IS NULL THEN 1 ELSE 0 END) AS jobs_missing_duration,
    SUM(CASE WHEN estimated_labor_hours IS NULL THEN 1 ELSE 0 END) AS jobs_missing_labor_hours,
    SUM(CASE WHEN estimated_crew_size IS NULL THEN 1 ELSE 0 END) AS jobs_missing_crew_size,
    SUM(CASE WHEN has_warnings THEN 1 ELSE 0 END) AS jobs_with_warnings
FROM dashboard_contracted_backlog
GROUP BY division;


CREATE OR REPLACE VIEW dashboard_estimate_adders_enhanced AS
SELECT
    *,
    CASE
        WHEN LOWER(COALESCE(line_item_name, '') || ' ' || COALESCE(description, '') || ' ' || COALESCE(line_item_category, '')) LIKE '%warranty%'
            THEN 'Warranty'
        WHEN LOWER(COALESCE(line_item_name, '') || ' ' || COALESCE(description, '') || ' ' || COALESCE(line_item_category, '')) LIKE '%insurance%'
            THEN 'Insurance'
        WHEN LOWER(COALESCE(line_item_name, '') || ' ' || COALESCE(description, '') || ' ' || COALESCE(line_item_category, '')) LIKE '%lift%'
          OR LOWER(COALESCE(line_item_name, '') || ' ' || COALESCE(description, '') || ' ' || COALESCE(line_item_category, '')) LIKE '%rental%'
          OR LOWER(COALESCE(line_item_name, '') || ' ' || COALESCE(description, '') || ' ' || COALESCE(line_item_category, '')) LIKE '%equipment%'
            THEN 'Equipment Rental'
        WHEN LOWER(COALESCE(line_item_name, '') || ' ' || COALESCE(description, '') || ' ' || COALESCE(line_item_category, '')) LIKE '%porta%'
          OR LOWER(COALESCE(line_item_name, '') || ' ' || COALESCE(description, '') || ' ' || COALESCE(line_item_category, '')) LIKE '%john%'
          OR LOWER(COALESCE(line_item_name, '') || ' ' || COALESCE(description, '') || ' ' || COALESCE(line_item_category, '')) LIKE '%toilet%'
            THEN 'Site Services'
        WHEN LOWER(COALESCE(line_item_name, '') || ' ' || COALESCE(description, '') || ' ' || COALESCE(line_item_category, '')) LIKE '%subcontractor%'
          OR LOWER(COALESCE(line_item_name, '') || ' ' || COALESCE(description, '') || ' ' || COALESCE(line_item_category, '')) LIKE '%sub%'
            THEN 'Subcontractor'
        WHEN LOWER(COALESCE(line_item_name, '') || ' ' || COALESCE(description, '') || ' ' || COALESCE(line_item_category, '')) LIKE '%allowance%'
          OR LOWER(COALESCE(line_item_name, '') || ' ' || COALESCE(description, '') || ' ' || COALESCE(line_item_category, '')) LIKE '%contingency%'
            THEN 'Allowance / Contingency'
        WHEN LOWER(COALESCE(line_item_category, '')) LIKE '%material%'
            THEN 'Extra Materials'
        WHEN LOWER(COALESCE(line_item_category, '')) LIKE '%labor%'
            THEN 'Extra Labor'
        WHEN LOWER(COALESCE(line_item_name, '') || ' ' || COALESCE(description, '')) LIKE '%caulk%'
          OR LOWER(COALESCE(line_item_name, '') || ' ' || COALESCE(description, '')) LIKE '%rustnox%'
            THEN 'Specialty Materials'
        ELSE COALESCE(NULLIF(line_item_category, ''), 'Other / Misc')
    END AS adder_business_category
FROM dashboard_estimate_adders_clean;


CREATE OR REPLACE VIEW dashboard_adder_business_category_rollup AS
SELECT
    division,
    pipeline_status,
    adder_business_category,
    COUNT(*) AS adder_line_count,
    SUM(extended_cost) AS total_adder_cost,
    AVG(extended_cost) AS avg_adder_cost,
    SUM(labor_hours) AS total_adder_labor_hours
FROM dashboard_estimate_adders_enhanced
GROUP BY
    division,
    pipeline_status,
    adder_business_category;


CREATE OR REPLACE VIEW dashboard_sales_followup AS
SELECT
    job_id,
    division,
    pipeline_status,
    status,
    customer,
    job_name,
    job_type,
    estimated_value,
    estimated_sqft,
    price_per_sqft,
    has_warnings,
    warnings,
    estimate_file,
    COALESCE(folder_url, folder_path) AS folder_link_or_path,
    folder_url,
    folder_path,
    CASE
        WHEN estimated_value IS NULL THEN 'Missing estimated value'
        WHEN estimated_sqft IS NULL THEN 'Missing square footage'
        WHEN price_per_sqft IS NULL THEN 'Missing price per sqft'
        WHEN COALESCE(TRIM(warnings), '') <> '' THEN 'Review warning'
        ELSE 'Ready for follow-up'
    END AS followup_status,
    updated_at
FROM dashboard_jobs
WHERE pipeline_status = 'Proposed';


CREATE OR REPLACE VIEW dashboard_documentation_risk AS
SELECT
    job_id,
    division,
    pipeline_status,
    status,
    customer,
    job_name,
    job_type,
    estimated_value,
    photo_count,
    has_aerial,
    has_job_spec,
    has_signed_contract,
    has_invoice,
    has_warranty,
    COALESCE(folder_url, folder_path) AS folder_link_or_path,
    folder_url,
    folder_path,
    CASE
        WHEN COALESCE(photo_count, 0) = 0 THEN 'Missing photos'
        WHEN COALESCE(has_aerial, FALSE) = FALSE AND COALESCE(estimated_value, 0) >= 25000 THEN 'High-value missing aerial/drone'
        WHEN COALESCE(has_job_spec, FALSE) = FALSE THEN 'Missing job spec'
        WHEN pipeline_status IN ('Contracted', 'Contracted Repairs', 'Completed') AND COALESCE(has_signed_contract, FALSE) = FALSE THEN 'Missing signed contract'
        WHEN pipeline_status = 'Completed' AND COALESCE(has_invoice, FALSE) = FALSE THEN 'Completed missing invoice'
        WHEN pipeline_status = 'Completed' AND COALESCE(has_warranty, FALSE) = FALSE THEN 'Completed missing warranty'
        ELSE 'Review documentation'
    END AS documentation_risk
FROM dashboard_jobs
WHERE
    COALESCE(photo_count, 0) = 0
    OR (COALESCE(has_aerial, FALSE) = FALSE AND COALESCE(estimated_value, 0) >= 25000)
    OR COALESCE(has_job_spec, FALSE) = FALSE
    OR (pipeline_status IN ('Contracted', 'Contracted Repairs', 'Completed') AND COALESCE(has_signed_contract, FALSE) = FALSE)
    OR (pipeline_status = 'Completed' AND COALESCE(has_invoice, FALSE) = FALSE)
    OR (pipeline_status = 'Completed' AND COALESCE(has_warranty, FALSE) = FALSE);
