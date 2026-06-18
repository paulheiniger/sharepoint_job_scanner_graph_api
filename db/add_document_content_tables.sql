ALTER TABLE documents ADD COLUMN IF NOT EXISTS extraction_method TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS extracted_at TIMESTAMPTZ;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS cached_file_path TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS requires_ocr BOOLEAN DEFAULT FALSE;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS drive_id TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS drive_item_id TEXT;

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
CREATE INDEX IF NOT EXISTS idx_documents_drive_id ON documents(drive_id) WHERE drive_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_documents_drive_item_id ON documents(drive_item_id) WHERE drive_item_id IS NOT NULL;
