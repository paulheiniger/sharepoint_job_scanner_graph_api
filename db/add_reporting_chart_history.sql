CREATE TABLE IF NOT EXISTS reporting_chart_daily_snapshots (
    snapshot_date DATE NOT NULL,
    source_dataset TEXT NOT NULL,
    observation_json TEXT NOT NULL,
    source_as_of TEXT,
    truth_class TEXT NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (snapshot_date, source_dataset)
);

CREATE INDEX IF NOT EXISTS idx_reporting_chart_daily_snapshots_dataset_date
    ON reporting_chart_daily_snapshots(source_dataset, snapshot_date);
