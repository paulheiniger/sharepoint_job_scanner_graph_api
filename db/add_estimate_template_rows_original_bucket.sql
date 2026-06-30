ALTER TABLE estimate_template_rows
    ADD COLUMN IF NOT EXISTS original_template_bucket TEXT;

CREATE INDEX IF NOT EXISTS idx_estimate_template_rows_original_template_bucket
    ON estimate_template_rows(original_template_bucket);
