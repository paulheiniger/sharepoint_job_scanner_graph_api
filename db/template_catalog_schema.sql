-- Template intelligence catalog tables for extracted estimator workbook logic.
-- These tables preserve workbook formulas, selectors, lookup tables, and row maps
-- before historical estimate rows are normalized into estimator defaults.

CREATE TABLE IF NOT EXISTS template_selector_maps (
    selector_map_id TEXT PRIMARY KEY,
    template_type TEXT NOT NULL,
    template_name TEXT NOT NULL,
    sheet_name TEXT NOT NULL,
    row_number INTEGER,
    formula_cell TEXT,
    selector_cell TEXT,
    template_bucket TEXT,
    selector_code TEXT,
    resolved_item_name TEXT,
    formula TEXT,
    extracted_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS template_lookup_tables (
    lookup_table_id TEXT PRIMARY KEY,
    template_type TEXT NOT NULL,
    template_name TEXT NOT NULL,
    sheet_name TEXT NOT NULL,
    table_name TEXT NOT NULL,
    row_number INTEGER,
    lookup_key TEXT,
    headers_json JSONB,
    values_json JSONB,
    extracted_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS template_row_catalog (
    template_row_catalog_id TEXT PRIMARY KEY,
    template_type TEXT NOT NULL,
    template_name TEXT NOT NULL,
    sheet_name TEXT NOT NULL,
    row_number INTEGER NOT NULL,
    section TEXT,
    template_bucket TEXT,
    line_item_kind TEXT,
    formula_model TEXT,
    cell_roles_json JSONB,
    extracted_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS template_formula_models (
    template_formula_model_id TEXT PRIMARY KEY,
    template_type TEXT NOT NULL,
    template_name TEXT NOT NULL,
    sheet_name TEXT NOT NULL,
    cell_address TEXT NOT NULL,
    row_number INTEGER,
    template_bucket TEXT,
    formula_kind TEXT,
    formula_model TEXT,
    formula TEXT,
    dependencies_json JSONB,
    selector_map_json JSONB,
    extracted_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS template_product_options (
    template_product_option_id TEXT PRIMARY KEY,
    template_type TEXT NOT NULL,
    template_name TEXT NOT NULL,
    source_type TEXT,
    source_table TEXT,
    template_bucket TEXT,
    row_number INTEGER,
    selector_code TEXT,
    product_name TEXT,
    source_values_json JSONB,
    extracted_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS template_labor_options (
    template_labor_option_id TEXT PRIMARY KEY,
    template_type TEXT NOT NULL,
    template_name TEXT NOT NULL,
    source_type TEXT,
    source_table TEXT,
    row_number INTEGER,
    labor_package TEXT,
    lookup_key TEXT,
    source_values_json JSONB,
    extracted_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS insulation_foam_decision_history (
    decision_history_id TEXT PRIMARY KEY,
    template_name TEXT,
    source_file TEXT,
    row_number INTEGER,
    selector_code TEXT,
    resolved_item_name TEXT,
    foam_brand TEXT,
    foam_density_lb NUMERIC,
    area_sqft NUMERIC,
    thickness_inches NUMERIC,
    yield_factor NUMERIC,
    unit_price NUMERIC,
    estimated_units NUMERIC,
    estimated_sets NUMERIC,
    estimated_cost NUMERIC,
    units_per_sqft_per_inch NUMERIC,
    sets_per_sqft_per_inch NUMERIC,
    cost_per_sqft_per_inch NUMERIC,
    extracted_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS insulation_coating_decision_history (
    decision_history_id TEXT PRIMARY KEY,
    template_name TEXT,
    source_file TEXT,
    row_number INTEGER,
    selector_code TEXT,
    resolved_item_name TEXT,
    area_sqft NUMERIC,
    gal_per_100_sqft NUMERIC,
    gal_per_sqft NUMERIC,
    waste_margin_pct NUMERIC,
    unit_price NUMERIC,
    estimated_gallons NUMERIC,
    estimated_cost NUMERIC,
    extracted_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS insulation_labor_decision_history (
    decision_history_id TEXT PRIMARY KEY,
    template_name TEXT,
    source_file TEXT,
    row_number INTEGER,
    template_bucket TEXT,
    labor_task TEXT,
    days NUMERIC,
    crew_size NUMERIC,
    total_hours NUMERIC,
    rate NUMERIC,
    estimated_cost NUMERIC,
    extracted_at TIMESTAMPTZ DEFAULT now()
);
