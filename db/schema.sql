-- Put all CREATE TABLE statements here

CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    division TEXT,
    pipeline_status TEXT,
    status TEXT,
    customer TEXT,
    job_name TEXT,
    job_type TEXT,
    site_address TEXT,
    city TEXT,
    state TEXT,
    zip_code TEXT,
    estimated_sqft NUMERIC,
    material_subtotal NUMERIC,
    labor_subtotal NUMERIC,
    total_job_cost NUMERIC,
    final_price NUMERIC,
    price_per_sqft NUMERIC,
    has_signed_contract BOOLEAN,
    has_invoice BOOLEAN,
    has_warranty BOOLEAN,
    has_proposal BOOLEAN,
    has_job_spec BOOLEAN,
    has_aerial BOOLEAN,
    has_notes BOOLEAN,
    photo_count INTEGER,
    folder_name TEXT,
    folder_path TEXT,
    folder_url TEXT,
    primary_doc_link TEXT,
    primary_doc_type TEXT,
    primary_doc_name TEXT,
    proposal_url TEXT,
    estimate_url TEXT,
    contract_url TEXT,
    invoice_url TEXT,
    job_tracking_url TEXT,
    warranty_url TEXT,
    aerial_url TEXT,
    important_doc_links_json TEXT,
    document_link_count INTEGER,
    estimate_file TEXT,
    primary_estimate_file TEXT,
    estimate_file_count INTEGER,
    multiple_estimates_found BOOLEAN,
    warnings TEXT,
    last_scanned_at TIMESTAMPTZ,
    scan_root TEXT,
    source_year TEXT,
    raw JSONB,
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS estimates (
    estimate_id TEXT PRIMARY KEY,
    job_id TEXT REFERENCES jobs(job_id) ON DELETE CASCADE,
    estimate_file TEXT,
    estimate_role TEXT,
    estimate_scope_type TEXT,
    division TEXT,
    pipeline_status TEXT,
    customer TEXT,
    job_name TEXT,
    job_type TEXT,
    estimated_sqft NUMERIC,
    material_subtotal NUMERIC,
    labor_subtotal NUMERIC,
    equipment_subtotal NUMERIC,
    subcontractor_subtotal NUMERIC,
    travel_lodging NUMERIC,
    total_job_cost NUMERIC,
    overhead_pct NUMERIC,
    overhead_amount NUMERIC,
    profit_pct NUMERIC,
    profit_amount NUMERIC,
    worksheet_price NUMERIC,
    final_price NUMERIC,
    price_per_sqft NUMERIC,
    estimated_duration_days NUMERIC,
    estimated_labor_hours NUMERIC,
    estimated_crew_size NUMERIC,
    estimated_hours_per_day NUMERIC,
    adders_subtotal NUMERIC,
    warranty_amount NUMERIC,
    insurance_amount NUMERIC,
    equipment_rental_amount NUMERIC,
    subcontractor_amount NUMERIC,
    misc_materials_amount NUMERIC,
    labor_duration_source TEXT,
    source_path TEXT,
    extraction_warnings TEXT,
    raw JSONB,
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS estimate_line_items (
    line_item_id TEXT PRIMARY KEY,
    estimate_id TEXT REFERENCES estimates(estimate_id) ON DELETE CASCADE,
    job_id TEXT REFERENCES jobs(job_id) ON DELETE CASCADE,
    estimate_file TEXT,
    division TEXT,
    pipeline_status TEXT,
    customer TEXT,
    job_name TEXT,
    section TEXT,
    line_item_category TEXT,
    line_item_name TEXT,
    description TEXT,
    quantity NUMERIC,
    unit TEXT,
    unit_cost NUMERIC,
    unit_price NUMERIC,
    extended_cost NUMERIC,
    markup_pct NUMERIC,
    labor_days NUMERIC,
    crew_size NUMERIC,
    labor_hours NUMERIC,
    vendor TEXT,
    notes TEXT,
    source_sheet TEXT,
    source_row INTEGER,
    raw JSONB,
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS crew_schedule (
    schedule_id TEXT PRIMARY KEY,
    job_id TEXT REFERENCES jobs(job_id) ON DELETE CASCADE,
    assigned_crew_leader TEXT,
    suggested_crew_type TEXT,
    suggested_crew_reason TEXT,
    scheduled_sequence INTEGER,
    estimated_start_date DATE,
    estimated_duration_days NUMERIC,
    estimated_end_date DATE,
    schedule_status TEXT,
    ready_to_schedule BOOLEAN,
    blocking_issue TEXT,
    priority TEXT,
    schedule_notes TEXT,
    updated_by TEXT,
    raw JSONB,
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS daily_dispatch (
    dispatch_id TEXT PRIMARY KEY,
    dispatch_date DATE NOT NULL,
    job_id TEXT,
    customer TEXT,
    job_name TEXT,
    site_address TEXT,
    start_time TEXT,
    crew_leader TEXT,
    crew_members TEXT,
    work_scope TEXT,
    equipment_notes TEXT,
    material_notes TEXT,
    safety_notes TEXT,
    weather_notes TEXT,
    special_instructions TEXT,
    message_text TEXT,
    send_method TEXT,
    sent_status TEXT,
    sent_at TIMESTAMPTZ,
    raw JSONB,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS job_workflow_overrides (
    job_id TEXT PRIMARY KEY,
    workflow_status TEXT,
    deal_owner TEXT,
    assigned_user TEXT,
    follow_up_date DATE,
    priority TEXT,
    internal_notes TEXT,
    updated_by TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    document_type TEXT,
    classification_reason TEXT,
    file_name TEXT NOT NULL,
    sharepoint_url TEXT,
    folder_path TEXT,
    relative_path TEXT,
    mime_type TEXT,
    file_extension TEXT,
    size_bytes BIGINT,
    modified_at TIMESTAMPTZ,
    source_year INTEGER,
    source_division TEXT,
    drive_id TEXT,
    drive_item_id TEXT,
    content_hash TEXT,
    extraction_status TEXT,
    extraction_method TEXT,
    extraction_error TEXT,
    extracted_at TIMESTAMPTZ,
    cached_file_path TEXT,
    requires_ocr BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS document_content (
    content_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    job_id TEXT,
    content_type TEXT,
    source_locator TEXT,
    page_number INTEGER,
    sheet_name TEXT,
    cell_range TEXT,
    row_number INTEGER,
    section_name TEXT,
    text_content TEXT NOT NULL,
    normalized_text TEXT,
    extraction_method TEXT,
    content_hash TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_document_content_document_id ON document_content(document_id);
CREATE INDEX IF NOT EXISTS idx_document_content_job_id ON document_content(job_id);
CREATE INDEX IF NOT EXISTS idx_document_content_page_number ON document_content(page_number);
CREATE INDEX IF NOT EXISTS idx_document_content_sheet_name ON document_content(sheet_name);
CREATE INDEX IF NOT EXISTS idx_document_content_content_type ON document_content(content_type);
CREATE INDEX IF NOT EXISTS idx_documents_extraction_status ON documents(extraction_status);

CREATE TABLE IF NOT EXISTS job_tracking_summary (
    tracking_id TEXT PRIMARY KEY,
    job_id TEXT REFERENCES jobs(job_id) ON DELETE CASCADE,
    tracking_file TEXT,
    actual_first_work_date DATE,
    actual_last_work_date DATE,
    actual_work_day_count NUMERIC,
    actual_labor_hours NUMERIC,
    actual_travel_hours NUMERIC,
    actual_load_hours NUMERIC,
    actual_os_hours NUMERIC,
    actual_mileage NUMERIC,
    actual_os_mileage NUMERIC,
    actual_base_coat_1 NUMERIC,
    actual_base_coat_2 NUMERIC,
    actual_af_buttergrade NUMERIC,
    actual_caulk NUMERIC,
    estimated_labor_hours NUMERIC,
    estimated_travel_hours NUMERIC,
    estimated_load_hours NUMERIC,
    estimated_mileage NUMERIC,
    estimated_os_mileage NUMERIC,
    estimated_base_coat_1 NUMERIC,
    estimated_base_coat_2 NUMERIC,
    estimated_af_buttergrade NUMERIC,
    estimated_caulk NUMERIC,
    labor_hours_variance NUMERIC,
    tracking_notes TEXT,
    tracking_warnings TEXT,
    source_file TEXT,
    source_path TEXT,
    raw JSONB,
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS job_tracking_daily_entries (
    tracking_entry_id TEXT PRIMARY KEY,
    tracking_id TEXT REFERENCES job_tracking_summary(tracking_id) ON DELETE CASCADE,
    job_id TEXT REFERENCES jobs(job_id) ON DELETE CASCADE,
    tracking_file TEXT,
    work_date DATE,
    labor_hours NUMERIC,
    travel_hours NUMERIC,
    load_hours NUMERIC,
    os_hours NUMERIC,
    mileage NUMERIC,
    os_mileage NUMERIC,
    base_coat_1 NUMERIC,
    base_sqft NUMERIC,
    base_gal_per_sq NUMERIC,
    base_coat_2 NUMERIC,
    top_sqft NUMERIC,
    top_gal_per_sq NUMERIC,
    af_buttergrade NUMERIC,
    caulk NUMERIC,
    crew TEXT,
    notes TEXT,
    source_sheet TEXT,
    source_row INTEGER,
    raw JSONB,
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS office_timesheet_entries (
    entry_id TEXT PRIMARY KEY,
    employee TEXT,
    work_date DATE,
    project_name TEXT,
    code TEXT,
    duration_hours NUMERIC,
    row_type TEXT,
    notes TEXT,
    source_file TEXT,
    source_sheet TEXT,
    source_row INTEGER,
    warnings TEXT,
    raw JSONB,
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS scan_runs (
    scan_run_id TEXT PRIMARY KEY,
    scan_started_at TIMESTAMPTZ,
    scan_finished_at TIMESTAMPTZ,
    scan_type TEXT,
    source TEXT,
    rows_processed INTEGER,
    warnings_count INTEGER,
    raw JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS scan_warnings (
    warning_id TEXT PRIMARY KEY,
    job_id TEXT,
    scan_run_id TEXT,
    warning_type TEXT,
    warning_message TEXT,
    source_file TEXT,
    severity TEXT,
    raw JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_jobs_division_status ON jobs(division, pipeline_status);
CREATE INDEX IF NOT EXISTS idx_jobs_customer ON jobs(customer);
CREATE INDEX IF NOT EXISTS idx_estimates_job_id ON estimates(job_id);
CREATE INDEX IF NOT EXISTS idx_line_items_job_id ON estimate_line_items(job_id);
CREATE INDEX IF NOT EXISTS idx_line_items_estimate_id ON estimate_line_items(estimate_id);
CREATE INDEX IF NOT EXISTS idx_crew_schedule_job_id ON crew_schedule(job_id);
CREATE INDEX IF NOT EXISTS idx_daily_dispatch_date ON daily_dispatch(dispatch_date);
CREATE INDEX IF NOT EXISTS idx_daily_dispatch_job_id ON daily_dispatch(job_id);
CREATE INDEX IF NOT EXISTS idx_job_workflow_status ON job_workflow_overrides(workflow_status);
CREATE INDEX IF NOT EXISTS idx_job_workflow_priority ON job_workflow_overrides(priority);
CREATE INDEX IF NOT EXISTS idx_tracking_summary_job_id ON job_tracking_summary(job_id);
CREATE INDEX IF NOT EXISTS idx_timesheet_project_name ON office_timesheet_entries(project_name);
