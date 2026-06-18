-- Normalized SharePoint document index.
-- One row per discovered SharePoint file. Safe to run repeatedly.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

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
    drive_item_id TEXT,
    content_hash TEXT,
    extraction_status TEXT,
    extraction_error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_documents_job_id
    ON documents(job_id);

CREATE INDEX IF NOT EXISTS idx_documents_document_type
    ON documents(document_type);

CREATE INDEX IF NOT EXISTS idx_documents_file_name_trgm
    ON documents USING gin (LOWER(COALESCE(file_name, '')) gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_documents_folder_path_trgm
    ON documents USING gin (LOWER(COALESCE(folder_path, '')) gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_documents_relative_path_trgm
    ON documents USING gin (LOWER(COALESCE(relative_path, '')) gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_documents_modified_at
    ON documents(modified_at);

CREATE INDEX IF NOT EXISTS idx_documents_drive_item_id
    ON documents(drive_item_id)
    WHERE drive_item_id IS NOT NULL;
