ALTER TABLE IF EXISTS daily_dispatch
    ADD COLUMN IF NOT EXISTS end_time TEXT;

CREATE TABLE IF NOT EXISTS daily_dispatch_roster (
    roster_person_id TEXT PRIMARY KEY,
    person_name TEXT NOT NULL,
    person_role TEXT,
    hourly_rate NUMERIC,
    burden_rate NUMERIC,
    source TEXT,
    active BOOLEAN DEFAULT TRUE,
    raw JSONB,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (person_name)
);

CREATE TABLE IF NOT EXISTS daily_dispatch_crew_assignments (
    assignment_id TEXT PRIMARY KEY,
    dispatch_date DATE NOT NULL,
    job_id TEXT NOT NULL,
    roster_person_id TEXT,
    person_name TEXT NOT NULL,
    person_role TEXT,
    assignment_source TEXT,
    start_time TEXT,
    end_time TEXT,
    sequence INTEGER,
    notes TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (dispatch_date, job_id, person_name)
);

CREATE INDEX IF NOT EXISTS idx_daily_dispatch_crew_assignments_date
    ON daily_dispatch_crew_assignments(dispatch_date);

CREATE INDEX IF NOT EXISTS idx_daily_dispatch_crew_assignments_job
    ON daily_dispatch_crew_assignments(job_id);
