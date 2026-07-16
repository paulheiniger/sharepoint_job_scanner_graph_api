CREATE TABLE IF NOT EXISTS daily_production_entries (
    production_entry_id TEXT PRIMARY KEY,
    job_id TEXT REFERENCES jobs(job_id) ON DELETE SET NULL,
    dispatch_date DATE NOT NULL,
    work_date DATE NOT NULL,
    customer TEXT,
    job_name TEXT,
    site_address TEXT,
    crew_leader TEXT,
    crew_members TEXT,
    start_time TEXT,
    end_time TEXT,
    labor_hours NUMERIC,
    travel_hours NUMERIC,
    load_hours NUMERIC,
    os_hours NUMERIC,
    mileage NUMERIC,
    os_mileage NUMERIC,
    temperature_f NUMERIC,
    wind_mph NUMERIC,
    humidity_pct NUMERIC,
    weather_source TEXT,
    safety_issues TEXT,
    hazard_mitigation_plan TEXT,
    work_notes TEXT,
    submitted_by TEXT,
    submitted_at TIMESTAMPTZ DEFAULT NOW(),
    raw JSONB,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (job_id, work_date, crew_leader)
);

CREATE TABLE IF NOT EXISTS daily_production_material_usage (
    material_usage_id TEXT PRIMARY KEY,
    production_entry_id TEXT REFERENCES daily_production_entries(production_entry_id) ON DELETE CASCADE,
    job_id TEXT REFERENCES jobs(job_id) ON DELETE SET NULL,
    work_date DATE NOT NULL,
    material_type TEXT NOT NULL,
    quantity NUMERIC,
    unit TEXT,
    notes TEXT,
    raw JSONB,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (production_entry_id, material_type)
);

CREATE INDEX IF NOT EXISTS idx_daily_production_entries_job_date
    ON daily_production_entries(job_id, work_date);

CREATE INDEX IF NOT EXISTS idx_daily_production_material_usage_entry
    ON daily_production_material_usage(production_entry_id);
