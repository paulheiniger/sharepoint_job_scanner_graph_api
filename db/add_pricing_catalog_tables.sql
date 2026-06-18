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
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
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
