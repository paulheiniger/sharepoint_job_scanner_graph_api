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
    outbound_departure_time TEXT,
    jobsite_arrival_time TEXT,
    lunch_start_time TEXT,
    lunch_end_time TEXT,
    return_departure_time TEXT,
    return_arrival_time TEXT,
    labor_hours NUMERIC,
    travel_hours NUMERIC,
    load_hours NUMERIC,
    os_hours NUMERIC,
    mileage NUMERIC,
    os_mileage NUMERIC,
    truck_number TEXT,
    trailer_number TEXT,
    driver_name TEXT,
    odometer_out NUMERIC,
    odometer_in NUMERIC,
    equipment_notes TEXT,
    rain_observed BOOLEAN,
    weather_condition TEXT,
    temperature_f NUMERIC,
    wind_mph NUMERIC,
    humidity_pct NUMERIC,
    weather_source TEXT,
    interior_temperature_f NUMERIC,
    substrate_temperature_f NUMERIC,
    substrate_moisture NUMERIC,
    proportioner_a_side_temp_f NUMERIC,
    proportioner_b_side_temp_f NUMERIC,
    proportioner_drum_temp_f NUMERIC,
    proportioner_hose_temp_f NUMERIC,
    hydraulic_pressure_psi NUMERIC,
    safety_issue_options TEXT,
    safety_issues TEXT,
    hazard_mitigation_options TEXT,
    hazard_mitigation_plan TEXT,
    trailer_closeout_options TEXT,
    work_notes TEXT,
    submitted_by TEXT,
    submitted_at TIMESTAMPTZ DEFAULT NOW(),
    raw JSONB,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (job_id, work_date, crew_leader)
);

ALTER TABLE daily_production_entries ADD COLUMN IF NOT EXISTS outbound_departure_time TEXT;
ALTER TABLE daily_production_entries ADD COLUMN IF NOT EXISTS jobsite_arrival_time TEXT;
ALTER TABLE daily_production_entries ADD COLUMN IF NOT EXISTS lunch_start_time TEXT;
ALTER TABLE daily_production_entries ADD COLUMN IF NOT EXISTS lunch_end_time TEXT;
ALTER TABLE daily_production_entries ADD COLUMN IF NOT EXISTS return_departure_time TEXT;
ALTER TABLE daily_production_entries ADD COLUMN IF NOT EXISTS return_arrival_time TEXT;
ALTER TABLE daily_production_entries ADD COLUMN IF NOT EXISTS truck_number TEXT;
ALTER TABLE daily_production_entries ADD COLUMN IF NOT EXISTS trailer_number TEXT;
ALTER TABLE daily_production_entries ADD COLUMN IF NOT EXISTS driver_name TEXT;
ALTER TABLE daily_production_entries ADD COLUMN IF NOT EXISTS odometer_out NUMERIC;
ALTER TABLE daily_production_entries ADD COLUMN IF NOT EXISTS odometer_in NUMERIC;
ALTER TABLE daily_production_entries ADD COLUMN IF NOT EXISTS equipment_notes TEXT;
ALTER TABLE daily_production_entries ADD COLUMN IF NOT EXISTS rain_observed BOOLEAN;
ALTER TABLE daily_production_entries ADD COLUMN IF NOT EXISTS weather_condition TEXT;
ALTER TABLE daily_production_entries ADD COLUMN IF NOT EXISTS interior_temperature_f NUMERIC;
ALTER TABLE daily_production_entries ADD COLUMN IF NOT EXISTS substrate_temperature_f NUMERIC;
ALTER TABLE daily_production_entries ADD COLUMN IF NOT EXISTS substrate_moisture NUMERIC;
ALTER TABLE daily_production_entries ADD COLUMN IF NOT EXISTS proportioner_a_side_temp_f NUMERIC;
ALTER TABLE daily_production_entries ADD COLUMN IF NOT EXISTS proportioner_b_side_temp_f NUMERIC;
ALTER TABLE daily_production_entries ADD COLUMN IF NOT EXISTS proportioner_drum_temp_f NUMERIC;
ALTER TABLE daily_production_entries ADD COLUMN IF NOT EXISTS proportioner_hose_temp_f NUMERIC;
ALTER TABLE daily_production_entries ADD COLUMN IF NOT EXISTS hydraulic_pressure_psi NUMERIC;
ALTER TABLE daily_production_entries ADD COLUMN IF NOT EXISTS safety_issue_options TEXT;
ALTER TABLE daily_production_entries ADD COLUMN IF NOT EXISTS hazard_mitigation_options TEXT;
ALTER TABLE daily_production_entries ADD COLUMN IF NOT EXISTS trailer_closeout_options TEXT;

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
