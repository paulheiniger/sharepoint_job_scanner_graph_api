CREATE TABLE IF NOT EXISTS building_footprints (
    source TEXT NOT NULL,
    source_feature_id TEXT NOT NULL,
    state_code CHAR(2) NOT NULL,
    geometry_geojson JSONB NOT NULL,
    geometry_type TEXT NOT NULL,
    min_longitude DOUBLE PRECISION NOT NULL,
    min_latitude DOUBLE PRECISION NOT NULL,
    max_longitude DOUBLE PRECISION NOT NULL,
    max_latitude DOUBLE PRECISION NOT NULL,
    source_properties JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_file TEXT NOT NULL,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (source, source_feature_id),
    CHECK (min_longitude <= max_longitude),
    CHECK (min_latitude <= max_latitude)
);

CREATE INDEX IF NOT EXISTS idx_building_footprints_state_bbox
    ON building_footprints (state_code, min_longitude, max_longitude, min_latitude, max_latitude);

CREATE TABLE IF NOT EXISTS building_footprint_import_state (
    source TEXT NOT NULL,
    source_file TEXT NOT NULL,
    last_line_number BIGINT NOT NULL DEFAULT 0,
    imported_records BIGINT NOT NULL DEFAULT 0,
    skipped_records BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (source, source_file)
);
