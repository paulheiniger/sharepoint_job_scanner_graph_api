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

CREATE TABLE IF NOT EXISTS estimate_line_item_classifications (
    line_item_id TEXT PRIMARY KEY,
    job_id TEXT,
    estimate_id TEXT,
    source_file TEXT,
    sheet_name TEXT,
    row_number INTEGER,
    raw_item_name TEXT,
    raw_description TEXT,
    normalized_item_name TEXT,
    template_bucket TEXT,
    template_section TEXT,
    template_row_hint TEXT,
    line_item_kind TEXT,
    quantity NUMERIC,
    unit TEXT,
    unit_price NUMERIC,
    line_total NUMERIC,
    classification_confidence NUMERIC,
    classification_reason TEXT,
    needs_review BOOLEAN DEFAULT FALSE,
    classifier_version TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
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
    closed_did_not_get BOOLEAN DEFAULT FALSE,
    review_mark_contracted BOOLEAN DEFAULT FALSE,
    review_mark_completed BOOLEAN DEFAULT FALSE,
    internal_notes TEXT,
    updated_by TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sharepoint_delta_state (
    site_id TEXT,
    drive_id TEXT PRIMARY KEY,
    library_name TEXT,
    delta_link TEXT,
    sync_status TEXT,
    sync_started_at TIMESTAMPTZ,
    sync_completed_at TIMESTAMPTZ,
    last_successful_sync_at TIMESTAMPTZ,
    items_seen BIGINT DEFAULT 0,
    changes_applied BIGINT DEFAULT 0,
    error_message TEXT,
    checkpoint_next_link TEXT,
    checkpoint_page INTEGER,
    checkpoint_items_seen BIGINT,
    checkpoint_updated_at TIMESTAMPTZ,
    last_error_page INTEGER,
    last_error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sharepoint_drive_items (
    drive_id TEXT NOT NULL,
    drive_item_id TEXT NOT NULL,
    parent_item_id TEXT,
    name TEXT,
    web_url TEXT,
    parent_path TEXT,
    relative_path TEXT,
    is_folder BOOLEAN,
    is_file BOOLEAN,
    mime_type TEXT,
    size_bytes BIGINT,
    etag TEXT,
    ctag TEXT,
    last_modified_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    first_seen_at TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ DEFAULT NOW(),
    metadata_json JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (drive_id, drive_item_id)
);

CREATE INDEX IF NOT EXISTS idx_sharepoint_drive_items_relative_path
    ON sharepoint_drive_items(relative_path);

CREATE INDEX IF NOT EXISTS idx_sharepoint_drive_items_web_url
    ON sharepoint_drive_items(web_url)
    WHERE web_url IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_sharepoint_drive_items_deleted_at
    ON sharepoint_drive_items(deleted_at);

CREATE TABLE IF NOT EXISTS sharepoint_incremental_runs (
    run_id TEXT PRIMARY KEY,
    delta_run_id TEXT,
    drive_id TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    status TEXT,
    affected_jobs INTEGER DEFAULT 0,
    affected_estimates INTEGER DEFAULT 0,
    affected_tracking_files INTEGER DEFAULT 0,
    affected_timesheet_files INTEGER DEFAULT 0,
    affected_documents INTEGER DEFAULT 0,
    jobs_processed INTEGER DEFAULT 0,
    files_processed INTEGER DEFAULT 0,
    failures INTEGER DEFAULT 0,
    output_manifest_path TEXT,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sharepoint_incremental_run_items (
    run_id TEXT,
    drive_id TEXT,
    drive_item_id TEXT,
    change_type TEXT,
    source_path TEXT,
    destination_path TEXT,
    mapped_job_id TEXT,
    processor TEXT,
    processing_status TEXT,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (run_id, drive_id, drive_item_id, processor)
);

CREATE INDEX IF NOT EXISTS idx_sharepoint_incremental_run_items_run_id
    ON sharepoint_incremental_run_items(run_id);

CREATE INDEX IF NOT EXISTS idx_sharepoint_incremental_run_items_status
    ON sharepoint_incremental_run_items(processing_status);

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
    deleted_at TIMESTAMPTZ,
    drive_metadata_match_strategy TEXT,
    drive_metadata_matched_at TIMESTAMPTZ,
    drive_metadata_match_confidence TEXT,
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

CREATE TABLE IF NOT EXISTS estimate_template_rows (
    template_row_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    job_id TEXT,
    source_file TEXT,
    template_type TEXT,
    sheet_name TEXT,
    row_number INTEGER,
    cell_range TEXT,
    template_bucket TEXT,
    template_section TEXT,
    line_item_kind TEXT,
    row_label TEXT,
    raw_text TEXT,
    cell_values JSONB,
    formula_cells JSONB,
    selected_item_name TEXT,
    quantity NUMERIC,
    unit TEXT,
    unit_price NUMERIC,
    estimated_units NUMERIC,
    estimated_cost NUMERIC,
    selector_code NUMERIC,
    resolved_item_name TEXT,
    area_sqft NUMERIC,
    thickness_inches NUMERIC,
    yield_or_coverage NUMERIC,
    yield_factor NUMERIC,
    estimated_sets NUMERIC,
    foam_brand TEXT,
    foam_density_lb NUMERIC,
    units_per_sqft_per_inch NUMERIC,
    sets_per_sqft_per_inch NUMERIC,
    cost_per_sqft_per_inch NUMERIC,
    gal_per_100_sqft NUMERIC,
    gal_per_sqft NUMERIC,
    estimated_gallons NUMERIC,
    linear_ft NUMERIC,
    ft_per_unit NUMERIC,
    margin_pct NUMERIC,
    waste_margin_cell TEXT,
    quantity_cell_role TEXT,
    formula_model TEXT,
    days NUMERIC,
    crew_size NUMERIC,
    total_hours NUMERIC,
    daily_rate NUMERIC,
    crew_selector_code NUMERIC,
    hourly_rate NUMERIC,
    calculated_cost NUMERIC,
    formula_mode TEXT,
    trips NUMERIC,
    round_trip_miles NUMERIC,
    cost_per_mile NUMERIC,
    warranty_years NUMERIC,
    overhead_pct NUMERIC,
    profit_pct NUMERIC,
    parsed_confidence NUMERIC,
    needs_review BOOLEAN DEFAULT FALSE,
    parser_version TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_document_content_document_id ON document_content(document_id);
CREATE INDEX IF NOT EXISTS idx_document_content_job_id ON document_content(job_id);
CREATE INDEX IF NOT EXISTS idx_document_content_page_number ON document_content(page_number);
CREATE INDEX IF NOT EXISTS idx_document_content_sheet_name ON document_content(sheet_name);
CREATE INDEX IF NOT EXISTS idx_document_content_content_type ON document_content(content_type);
CREATE INDEX IF NOT EXISTS idx_job_document_signals_refreshed_at ON job_document_signals(refreshed_at);
CREATE INDEX IF NOT EXISTS idx_documents_extraction_status ON documents(extraction_status);
CREATE INDEX IF NOT EXISTS idx_estimate_template_rows_document_id ON estimate_template_rows(document_id);
CREATE INDEX IF NOT EXISTS idx_estimate_template_rows_job_id ON estimate_template_rows(job_id);
CREATE INDEX IF NOT EXISTS idx_estimate_template_rows_sheet_row ON estimate_template_rows(sheet_name, row_number);
CREATE INDEX IF NOT EXISTS idx_estimate_template_rows_template_bucket ON estimate_template_rows(template_bucket);
CREATE INDEX IF NOT EXISTS idx_estimate_template_rows_template_type ON estimate_template_rows(template_type);
CREATE INDEX IF NOT EXISTS idx_estimate_template_rows_line_item_kind ON estimate_template_rows(line_item_kind);
CREATE INDEX IF NOT EXISTS idx_estimate_template_rows_needs_review ON estimate_template_rows(needs_review);

CREATE TABLE IF NOT EXISTS pricing_catalog (
    pricing_item_id TEXT PRIMARY KEY,
    vendor TEXT,
    category TEXT,
    product_name TEXT NOT NULL,
    product_name_normalized TEXT,
    description TEXT,
    unit_price NUMERIC,
    unit_of_measure TEXT,
    package_size TEXT,
    price_basis TEXT,
    price_per_gallon NUMERIC,
    price_per_sqft NUMERIC,
    price_per_unit NUMERIC,
    vendor_item_no TEXT,
    source_file TEXT,
    source_type TEXT,
    source_sheet TEXT,
    source_page INTEGER,
    effective_date DATE,
    expiration_date DATE,
    is_current BOOLEAN DEFAULT TRUE,
    status TEXT DEFAULT 'active',
    needs_review BOOLEAN DEFAULT FALSE,
    review_notes TEXT,
    notes TEXT,
    raw_row_json JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pricing_catalog_product_name_normalized
    ON pricing_catalog(product_name_normalized);
CREATE INDEX IF NOT EXISTS idx_pricing_catalog_vendor
    ON pricing_catalog(vendor);
CREATE INDEX IF NOT EXISTS idx_pricing_catalog_category
    ON pricing_catalog(category);
CREATE INDEX IF NOT EXISTS idx_pricing_catalog_status
    ON pricing_catalog(status);
CREATE INDEX IF NOT EXISTS idx_pricing_catalog_is_current
    ON pricing_catalog(is_current);
CREATE INDEX IF NOT EXISTS idx_pricing_catalog_effective_date
    ON pricing_catalog(effective_date);
CREATE INDEX IF NOT EXISTS idx_pricing_catalog_needs_review
    ON pricing_catalog(needs_review);

CREATE TABLE IF NOT EXISTS pricing_source_files (
    source_file_id TEXT PRIMARY KEY,
    file_name TEXT,
    source_type TEXT,
    vendor TEXT,
    effective_date DATE,
    loaded_at TIMESTAMPTZ,
    row_count INTEGER,
    notes TEXT,
    metadata_json JSONB
);

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
    actual_foam_strokes NUMERIC,
    actual_foam_thickness_inches NUMERIC,
    actual_foam_sqft NUMERIC,
    actual_foam_yield NUMERIC,
    actual_base_coat_1 NUMERIC,
    actual_base_coat_2 NUMERIC,
    actual_granules NUMERIC,
    actual_af_buttergrade NUMERIC,
    actual_caulk NUMERIC,
    actual_primer NUMERIC,
    actual_sf NUMERIC,
    estimated_labor_hours NUMERIC,
    estimated_travel_hours NUMERIC,
    estimated_load_hours NUMERIC,
    estimated_mileage NUMERIC,
    estimated_os_mileage NUMERIC,
    estimated_foam_strokes NUMERIC,
    estimated_foam_thickness_inches NUMERIC,
    estimated_foam_sqft NUMERIC,
    estimated_foam_yield NUMERIC,
    estimated_base_coat_1 NUMERIC,
    estimated_base_coat_2 NUMERIC,
    estimated_granules NUMERIC,
    estimated_af_buttergrade NUMERIC,
    estimated_caulk NUMERIC,
    estimated_primer NUMERIC,
    estimated_sf NUMERIC,
    labor_hours_variance NUMERIC,
    foam_strokes_variance NUMERIC,
    foam_sqft_variance NUMERIC,
    granules_variance NUMERIC,
    primer_variance NUMERIC,
    sf_variance NUMERIC,
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
    foam_strokes NUMERIC,
    foam_thickness_inches NUMERIC,
    foam_sqft NUMERIC,
    foam_yield NUMERIC,
    a_side_lot TEXT,
    b_side_lot TEXT,
    base_coat_1 NUMERIC,
    base_sqft NUMERIC,
    base_gal_per_sq NUMERIC,
    base_coat_2 NUMERIC,
    top_sqft NUMERIC,
    top_gal_per_sq NUMERIC,
    granules NUMERIC,
    af_buttergrade NUMERIC,
    caulk NUMERIC,
    primer NUMERIC,
    sf NUMERIC,
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
    source_drive_id TEXT,
    source_drive_item_id TEXT,
    source_file_path TEXT,
    source_modified_at TIMESTAMPTZ,
    source_content_hash TEXT,
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
CREATE INDEX IF NOT EXISTS idx_estimate_line_item_classifications_job_id ON estimate_line_item_classifications(job_id);
CREATE INDEX IF NOT EXISTS idx_estimate_line_item_classifications_estimate_id ON estimate_line_item_classifications(estimate_id);
CREATE INDEX IF NOT EXISTS idx_estimate_line_item_classifications_template_bucket ON estimate_line_item_classifications(template_bucket);
CREATE INDEX IF NOT EXISTS idx_estimate_line_item_classifications_line_item_kind ON estimate_line_item_classifications(line_item_kind);
CREATE INDEX IF NOT EXISTS idx_estimate_line_item_classifications_needs_review ON estimate_line_item_classifications(needs_review);
CREATE INDEX IF NOT EXISTS idx_crew_schedule_job_id ON crew_schedule(job_id);
CREATE INDEX IF NOT EXISTS idx_daily_dispatch_date ON daily_dispatch(dispatch_date);
CREATE INDEX IF NOT EXISTS idx_daily_dispatch_job_id ON daily_dispatch(job_id);
CREATE INDEX IF NOT EXISTS idx_job_workflow_status ON job_workflow_overrides(workflow_status);
CREATE INDEX IF NOT EXISTS idx_job_workflow_priority ON job_workflow_overrides(priority);
CREATE INDEX IF NOT EXISTS idx_tracking_summary_job_id ON job_tracking_summary(job_id);
CREATE INDEX IF NOT EXISTS idx_timesheet_project_name ON office_timesheet_entries(project_name);
CREATE INDEX IF NOT EXISTS idx_office_timesheet_source_drive_item ON office_timesheet_entries(source_drive_id, source_drive_item_id);
