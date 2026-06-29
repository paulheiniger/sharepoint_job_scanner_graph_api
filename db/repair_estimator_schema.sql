CREATE TABLE IF NOT EXISTS repair_jobs (
    repair_id TEXT PRIMARY KEY,
    customer TEXT,
    job_name TEXT,
    status TEXT,
    type_of_repair TEXT,
    roof_type TEXT,
    repair_address TEXT,
    city TEXT,
    state TEXT,
    zip TEXT,
    url TEXT,
    sharepoint_url TEXT,
    created_date TEXT,
    completion_date TEXT,
    source_file TEXT,
    source_sheet TEXT,
    source_row_number INTEGER,
    parser_version TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS repair_material_usage (
    repair_material_usage_id TEXT PRIMARY KEY,
    repair_id TEXT,
    material_package TEXT,
    material_name TEXT,
    quantity NUMERIC,
    unit TEXT,
    unit_cost NUMERIC,
    total_cost NUMERIC,
    source_column TEXT,
    raw_materials_used TEXT,
    source_row_number INTEGER,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS repair_labor_usage (
    repair_labor_usage_id TEXT PRIMARY KEY,
    repair_id TEXT,
    labor_role TEXT,
    technician_name TEXT,
    labor_hours NUMERIC,
    labor_cost NUMERIC,
    total_labor_hours NUMERIC,
    source_column TEXT,
    source_row_number INTEGER,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS repair_scope_text (
    repair_id TEXT PRIMARY KEY,
    scope_of_work TEXT,
    work_performed_long_text TEXT,
    special_notes TEXT,
    materials_used TEXT,
    combined_scope_text TEXT,
    work_phrase_patterns TEXT,
    source_row_number INTEGER,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS repair_outcomes (
    repair_id TEXT PRIMARY KEY,
    status TEXT,
    total_bill_amount NUMERIC,
    invoice_amount NUMERIC,
    gross_profit NUMERIC,
    gross_profit_percentage NUMERIC,
    final_cost NUMERIC,
    gross_cost NUMERIC,
    total_st_cost NUMERIC,
    estimate_total_material_cost NUMERIC,
    estimate_total_labor_cost NUMERIC,
    completion_date TEXT,
    source_row_number INTEGER,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_repair_jobs_status ON repair_jobs(status);
CREATE INDEX IF NOT EXISTS idx_repair_jobs_type_roof ON repair_jobs(type_of_repair, roof_type);
CREATE INDEX IF NOT EXISTS idx_repair_material_usage_repair_id ON repair_material_usage(repair_id);
CREATE INDEX IF NOT EXISTS idx_repair_material_usage_package ON repair_material_usage(material_package);
CREATE INDEX IF NOT EXISTS idx_repair_labor_usage_repair_id ON repair_labor_usage(repair_id);
CREATE INDEX IF NOT EXISTS idx_repair_outcomes_status ON repair_outcomes(status);

CREATE TABLE IF NOT EXISTS repair_profile_summary (
    type_of_repair TEXT,
    roof_type TEXT,
    repair_count INTEGER,
    median_labor_hours NUMERIC,
    p75_labor_hours NUMERIC,
    median_invoice_amount NUMERIC,
    p75_invoice_amount NUMERIC,
    median_gross_profit NUMERIC,
    common_work_phrase_patterns TEXT,
    confidence TEXT
);

CREATE TABLE IF NOT EXISTS repair_material_package_profile (
    type_of_repair TEXT,
    roof_type TEXT,
    material_package TEXT,
    repair_count INTEGER,
    usage_count INTEGER,
    median_quantity NUMERIC,
    median_total_cost NUMERIC,
    p75_total_cost NUMERIC,
    common_material_names TEXT,
    confidence TEXT
);

CREATE TABLE IF NOT EXISTS repair_work_phrase_profile (
    work_phrase_pattern TEXT,
    type_of_repair TEXT,
    roof_type TEXT,
    repair_count INTEGER,
    median_labor_hours NUMERIC,
    median_invoice_amount NUMERIC,
    confidence TEXT
);
