CREATE TABLE IF NOT EXISTS warranty_source_records (
    source_record_id TEXT PRIMARY KEY,
    source_system TEXT NOT NULL,
    source_file TEXT NOT NULL,
    source_sheet TEXT,
    source_row INTEGER,
    source_locator TEXT,
    source_url TEXT,
    snapshot_date DATE,
    vsimple_id TEXT,
    reported_name TEXT,
    reported_customer TEXT,
    reported_address TEXT,
    reported_city TEXT,
    reported_state TEXT,
    division TEXT,
    source_year INTEGER,
    reported_status TEXT,
    warranty_category TEXT,
    warranty_type TEXT,
    provider TEXT,
    duration_years NUMERIC,
    start_date DATE,
    expiration_date DATE,
    expiration_date_source TEXT,
    has_date_conflict BOOLEAN NOT NULL DEFAULT FALSE,
    coverage_summary TEXT,
    coverage_excerpt TEXT,
    source_modified_at TIMESTAMPTZ,
    matched_vsimple_id TEXT,
    matched_job_id TEXT,
    match_method TEXT,
    match_confidence TEXT,
    match_score NUMERIC,
    match_candidates JSONB NOT NULL DEFAULT '[]'::JSONB,
    match_review_required BOOLEAN NOT NULL DEFAULT TRUE,
    extraction_method TEXT NOT NULL,
    extraction_confidence TEXT NOT NULL,
    raw JSONB NOT NULL DEFAULT '{}'::JSONB,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE warranty_source_records
    ADD COLUMN IF NOT EXISTS expiration_date_source TEXT;
ALTER TABLE warranty_source_records
    ADD COLUMN IF NOT EXISTS has_date_conflict BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE warranty_source_records
    ADD COLUMN IF NOT EXISTS match_candidates JSONB NOT NULL DEFAULT '[]'::JSONB;

CREATE INDEX IF NOT EXISTS idx_warranty_source_records_system
    ON warranty_source_records(source_system);
CREATE INDEX IF NOT EXISTS idx_warranty_source_records_job
    ON warranty_source_records(matched_job_id);
CREATE INDEX IF NOT EXISTS idx_warranty_source_records_vsimple
    ON warranty_source_records(matched_vsimple_id);
CREATE INDEX IF NOT EXISTS idx_warranty_source_records_year
    ON warranty_source_records(source_year);
CREATE INDEX IF NOT EXISTS idx_warranty_source_records_review
    ON warranty_source_records(match_review_required);
