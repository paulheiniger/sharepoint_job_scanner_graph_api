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

ALTER TABLE sharepoint_delta_state ADD COLUMN IF NOT EXISTS checkpoint_next_link TEXT;
ALTER TABLE sharepoint_delta_state ADD COLUMN IF NOT EXISTS checkpoint_page INTEGER;
ALTER TABLE sharepoint_delta_state ADD COLUMN IF NOT EXISTS checkpoint_items_seen BIGINT;
ALTER TABLE sharepoint_delta_state ADD COLUMN IF NOT EXISTS checkpoint_updated_at TIMESTAMPTZ;
ALTER TABLE sharepoint_delta_state ADD COLUMN IF NOT EXISTS last_error_page INTEGER;
ALTER TABLE sharepoint_delta_state ADD COLUMN IF NOT EXISTS last_error_message TEXT;

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

ALTER TABLE documents ADD COLUMN IF NOT EXISTS drive_id TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS drive_item_id TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS drive_metadata_match_strategy TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS drive_metadata_matched_at TIMESTAMPTZ;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS drive_metadata_match_confidence TEXT;
CREATE INDEX IF NOT EXISTS idx_documents_drive_id ON documents(drive_id) WHERE drive_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_documents_drive_item_id ON documents(drive_item_id) WHERE drive_item_id IS NOT NULL;
