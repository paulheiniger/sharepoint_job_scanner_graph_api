ALTER TABLE office_timesheet_entries ADD COLUMN IF NOT EXISTS job_id TEXT;
ALTER TABLE office_timesheet_entries ADD COLUMN IF NOT EXISTS start_time TEXT;
ALTER TABLE office_timesheet_entries ADD COLUMN IF NOT EXISTS end_time TEXT;
ALTER TABLE office_timesheet_entries ADD COLUMN IF NOT EXISTS milestone TEXT;
ALTER TABLE office_timesheet_entries ADD COLUMN IF NOT EXISTS next_action TEXT;
ALTER TABLE office_timesheet_entries ADD COLUMN IF NOT EXISTS next_action_owner TEXT;
ALTER TABLE office_timesheet_entries ADD COLUMN IF NOT EXISTS next_action_due DATE;
ALTER TABLE office_timesheet_entries ADD COLUMN IF NOT EXISTS source_app TEXT;
ALTER TABLE office_timesheet_entries ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_office_timesheet_job_id
    ON office_timesheet_entries(job_id);

CREATE INDEX IF NOT EXISTS idx_office_timesheet_milestone
    ON office_timesheet_entries(milestone);

CREATE INDEX IF NOT EXISTS idx_office_timesheet_next_action_due
    ON office_timesheet_entries(next_action_due);
