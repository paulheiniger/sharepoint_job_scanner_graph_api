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

ALTER TABLE estimate_line_item_classifications
    ADD COLUMN IF NOT EXISTS job_id TEXT,
    ADD COLUMN IF NOT EXISTS estimate_id TEXT,
    ADD COLUMN IF NOT EXISTS source_file TEXT,
    ADD COLUMN IF NOT EXISTS sheet_name TEXT,
    ADD COLUMN IF NOT EXISTS row_number INTEGER,
    ADD COLUMN IF NOT EXISTS raw_item_name TEXT,
    ADD COLUMN IF NOT EXISTS raw_description TEXT,
    ADD COLUMN IF NOT EXISTS normalized_item_name TEXT,
    ADD COLUMN IF NOT EXISTS template_bucket TEXT,
    ADD COLUMN IF NOT EXISTS template_section TEXT,
    ADD COLUMN IF NOT EXISTS template_row_hint TEXT,
    ADD COLUMN IF NOT EXISTS line_item_kind TEXT,
    ADD COLUMN IF NOT EXISTS quantity NUMERIC,
    ADD COLUMN IF NOT EXISTS unit TEXT,
    ADD COLUMN IF NOT EXISTS unit_price NUMERIC,
    ADD COLUMN IF NOT EXISTS line_total NUMERIC,
    ADD COLUMN IF NOT EXISTS classification_confidence NUMERIC,
    ADD COLUMN IF NOT EXISTS classification_reason TEXT,
    ADD COLUMN IF NOT EXISTS needs_review BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS classifier_version TEXT,
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now(),
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();

CREATE INDEX IF NOT EXISTS idx_estimate_line_item_classifications_job_id
    ON estimate_line_item_classifications(job_id);
CREATE INDEX IF NOT EXISTS idx_estimate_line_item_classifications_estimate_id
    ON estimate_line_item_classifications(estimate_id);
CREATE INDEX IF NOT EXISTS idx_estimate_line_item_classifications_template_bucket
    ON estimate_line_item_classifications(template_bucket);
CREATE INDEX IF NOT EXISTS idx_estimate_line_item_classifications_line_item_kind
    ON estimate_line_item_classifications(line_item_kind);
CREATE INDEX IF NOT EXISTS idx_estimate_line_item_classifications_needs_review
    ON estimate_line_item_classifications(needs_review);
