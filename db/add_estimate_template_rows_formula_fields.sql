ALTER TABLE estimate_template_rows
    ADD COLUMN IF NOT EXISTS selector_code NUMERIC,
    ADD COLUMN IF NOT EXISTS resolved_item_name TEXT,
    ADD COLUMN IF NOT EXISTS area_sqft NUMERIC,
    ADD COLUMN IF NOT EXISTS thickness_inches NUMERIC,
    ADD COLUMN IF NOT EXISTS yield_or_coverage NUMERIC,
    ADD COLUMN IF NOT EXISTS yield_factor NUMERIC,
    ADD COLUMN IF NOT EXISTS estimated_sets NUMERIC,
    ADD COLUMN IF NOT EXISTS foam_brand TEXT,
    ADD COLUMN IF NOT EXISTS foam_density_lb NUMERIC,
    ADD COLUMN IF NOT EXISTS units_per_sqft_per_inch NUMERIC,
    ADD COLUMN IF NOT EXISTS sets_per_sqft_per_inch NUMERIC,
    ADD COLUMN IF NOT EXISTS cost_per_sqft_per_inch NUMERIC,
    ADD COLUMN IF NOT EXISTS gal_per_100_sqft NUMERIC,
    ADD COLUMN IF NOT EXISTS gal_per_sqft NUMERIC,
    ADD COLUMN IF NOT EXISTS estimated_gallons NUMERIC,
    ADD COLUMN IF NOT EXISTS linear_ft NUMERIC,
    ADD COLUMN IF NOT EXISTS ft_per_unit NUMERIC,
    ADD COLUMN IF NOT EXISTS margin_pct NUMERIC,
    ADD COLUMN IF NOT EXISTS waste_margin_cell TEXT,
    ADD COLUMN IF NOT EXISTS quantity_cell_role TEXT,
    ADD COLUMN IF NOT EXISTS formula_model TEXT,
    ADD COLUMN IF NOT EXISTS crew_selector_code NUMERIC,
    ADD COLUMN IF NOT EXISTS hourly_rate NUMERIC,
    ADD COLUMN IF NOT EXISTS calculated_cost NUMERIC,
    ADD COLUMN IF NOT EXISTS formula_mode TEXT;

ALTER TABLE estimate_template_rows
    ALTER COLUMN waste_margin_cell TYPE TEXT
    USING waste_margin_cell::TEXT;
