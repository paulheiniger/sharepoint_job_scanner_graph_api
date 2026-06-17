-- Add SharePoint document-link fields to existing jobs rows.
-- Safe to run repeatedly on local Postgres or Neon.

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS primary_doc_link TEXT,
    ADD COLUMN IF NOT EXISTS primary_doc_type TEXT,
    ADD COLUMN IF NOT EXISTS primary_doc_name TEXT,
    ADD COLUMN IF NOT EXISTS proposal_url TEXT,
    ADD COLUMN IF NOT EXISTS estimate_url TEXT,
    ADD COLUMN IF NOT EXISTS contract_url TEXT,
    ADD COLUMN IF NOT EXISTS invoice_url TEXT,
    ADD COLUMN IF NOT EXISTS job_tracking_url TEXT,
    ADD COLUMN IF NOT EXISTS warranty_url TEXT,
    ADD COLUMN IF NOT EXISTS aerial_url TEXT,
    ADD COLUMN IF NOT EXISTS document_link_count INTEGER;
