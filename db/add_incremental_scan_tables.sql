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

ALTER TABLE office_timesheet_entries ADD COLUMN IF NOT EXISTS source_drive_id TEXT;
ALTER TABLE office_timesheet_entries ADD COLUMN IF NOT EXISTS source_drive_item_id TEXT;
ALTER TABLE office_timesheet_entries ADD COLUMN IF NOT EXISTS source_file_path TEXT;
ALTER TABLE office_timesheet_entries ADD COLUMN IF NOT EXISTS source_modified_at TIMESTAMPTZ;
ALTER TABLE office_timesheet_entries ADD COLUMN IF NOT EXISTS source_content_hash TEXT;

CREATE INDEX IF NOT EXISTS idx_office_timesheet_source_drive_item
    ON office_timesheet_entries(source_drive_id, source_drive_item_id);
