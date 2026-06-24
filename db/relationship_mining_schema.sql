CREATE TABLE IF NOT EXISTS source_documents (
    source_document_id TEXT PRIMARY KEY,
    source_file TEXT,
    source_path TEXT,
    source_sheet TEXT,
    parser_version TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS estimate_line_items_raw (
    line_item_id TEXT PRIMARY KEY,
    source_document_id TEXT,
    parser_version TEXT,
    raw_json TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS estimate_line_items_normalized (
    normalized_line_item_id TEXT PRIMARY KEY,
    raw_line_item_id TEXT,
    source_document_id TEXT,
    job_id TEXT,
    estimate_id TEXT,
    estimate_file TEXT,
    source_sheet TEXT,
    source_row INTEGER,
    line_type TEXT,
    package TEXT,
    normalized_item_name TEXT,
    item_name TEXT,
    category TEXT,
    section TEXT,
    description TEXT,
    quantity NUMERIC,
    unit TEXT,
    unit_cost NUMERIC,
    total_cost NUMERIC,
    labor_days NUMERIC,
    labor_hours NUMERIC,
    crew_size NUMERIC,
    source_type TEXT,
    physical_quantity_valid BOOLEAN,
    review_required BOOLEAN,
    normalization_confidence NUMERIC,
    normalization_reason TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS estimate_jobs (
    job_id TEXT PRIMARY KEY,
    source_year TEXT,
    division TEXT,
    pipeline_status TEXT,
    status TEXT,
    customer TEXT,
    job_name TEXT,
    project_type TEXT,
    substrate TEXT,
    area_sqft NUMERIC,
    area_bucket TEXT,
    warranty_years NUMERIC,
    wet_mils NUMERIC,
    coating_type TEXT,
    roof_condition TEXT,
    access_complexity TEXT,
    final_price NUMERIC,
    invoice_amount NUMERIC,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS job_package_summary (
    job_id TEXT,
    package TEXT,
    included BOOLEAN,
    total_quantity NUMERIC,
    unit TEXT,
    total_cost NUMERIC,
    total_hours NUMERIC,
    qty_per_sqft NUMERIC,
    cost_per_sqft NUMERIC,
    has_physical_quantity BOOLEAN,
    has_allowance BOOLEAN,
    review_required BOOLEAN,
    evidence_line_item_ids TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (job_id, package)
);

CREATE INDEX IF NOT EXISTS idx_estimate_line_items_normalized_job_id ON estimate_line_items_normalized(job_id);
CREATE INDEX IF NOT EXISTS idx_estimate_line_items_normalized_package ON estimate_line_items_normalized(package);
CREATE INDEX IF NOT EXISTS idx_estimate_line_items_normalized_source_type ON estimate_line_items_normalized(source_type);
CREATE INDEX IF NOT EXISTS idx_estimate_jobs_filters ON estimate_jobs(source_year, division, pipeline_status, status);
CREATE INDEX IF NOT EXISTS idx_job_package_summary_package ON job_package_summary(package);
