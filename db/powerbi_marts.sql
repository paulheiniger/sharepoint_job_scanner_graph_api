-- Power BI analytics mart layer for Spray-Tec.
--
-- This script is intentionally idempotent and safe to rerun.  It creates a
-- read-only analytics schema made of curated views. Optional upstream tables
-- are handled defensively: if a source table is missing, the mart is created as
-- an empty view with the expected columns.

CREATE SCHEMA IF NOT EXISTS analytics;

CREATE OR REPLACE FUNCTION pg_temp.relation_exists(p_relation text)
RETURNS boolean
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN to_regclass(p_relation) IS NOT NULL;
END;
$$;

CREATE OR REPLACE FUNCTION pg_temp.has_col(p_relation text, p_column text)
RETURNS boolean
LANGUAGE plpgsql
AS $$
DECLARE
    v_schema text;
    v_table text;
BEGIN
    SELECT n.nspname, c.relname
      INTO v_schema, v_table
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE c.oid = to_regclass(p_relation)
     LIMIT 1;

    IF v_schema IS NULL THEN
        RETURN false;
    END IF;

    RETURN EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema = v_schema
           AND table_name = v_table
           AND column_name = p_column
    );
END;
$$;

CREATE OR REPLACE FUNCTION pg_temp.col_expr(
    p_relation text,
    p_alias text,
    p_source_column text,
    p_output_column text,
    p_type text,
    p_fallback text DEFAULT 'NULL'
)
RETURNS text
LANGUAGE plpgsql
AS $$
BEGIN
    IF p_relation IS NOT NULL AND pg_temp.has_col(p_relation, p_source_column) THEN
        IF lower(p_type) = 'date' THEN
            RETURN format(
                'CASE
                    WHEN NULLIF(TRIM(%1$I.%2$I::text), '''') IS NULL THEN NULL
                    WHEN TRIM(%1$I.%2$I::text) ~ ''^[0-9]{4}-[0-9]{2}-[0-9]{2}'' THEN TRIM(%1$I.%2$I::text)::date
                    WHEN TRIM(%1$I.%2$I::text) ~ ''^[0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4}'' THEN TRIM(%1$I.%2$I::text)::date
                    ELSE NULL
                 END::date AS %3$I',
                p_alias,
                p_source_column,
                p_output_column
            );
        END IF;

        IF lower(p_type) IN ('timestamp', 'timestamptz', 'timestamp with time zone', 'timestamp without time zone') THEN
            RETURN format(
                'CASE
                    WHEN NULLIF(TRIM(%1$I.%2$I::text), '''') IS NULL THEN NULL
                    WHEN TRIM(%1$I.%2$I::text) ~ ''^[0-9]{4}-[0-9]{2}-[0-9]{2}'' THEN TRIM(%1$I.%2$I::text)::%4$s
                    WHEN TRIM(%1$I.%2$I::text) ~ ''^[0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4}'' THEN TRIM(%1$I.%2$I::text)::%4$s
                    ELSE NULL
                 END::%4$s AS %3$I',
                p_alias,
                p_source_column,
                p_output_column,
                p_type
            );
        END IF;

        RETURN format('%I.%I::%s AS %I', p_alias, p_source_column, p_type, p_output_column);
    END IF;
    RETURN format('%s::%s AS %I', COALESCE(p_fallback, 'NULL'), p_type, p_output_column);
END;
$$;

CREATE OR REPLACE FUNCTION pg_temp.empty_exprs(p_columns jsonb)
RETURNS text
LANGUAGE plpgsql
AS $$
DECLARE
    v_item jsonb;
    v_exprs text[] := ARRAY[]::text[];
BEGIN
    FOR v_item IN SELECT * FROM jsonb_array_elements(p_columns)
    LOOP
        v_exprs := v_exprs || format(
            'NULL::%s AS %I',
            v_item->>'type',
            v_item->>'out'
        );
    END LOOP;
    RETURN array_to_string(v_exprs, ', ');
END;
$$;

CREATE OR REPLACE FUNCTION pg_temp.create_empty_view(p_view_name text, p_columns jsonb)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    EXECUTE format(
        'CREATE OR REPLACE VIEW analytics.%I AS SELECT %s WHERE false',
        p_view_name,
        pg_temp.empty_exprs(p_columns)
    );
END;
$$;

CREATE OR REPLACE FUNCTION pg_temp.create_simple_mart(
    p_view_name text,
    p_source_relation text,
    p_alias text,
    p_columns jsonb,
    p_where_clause text DEFAULT NULL
)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    v_item jsonb;
    v_exprs text[] := ARRAY[]::text[];
    v_sql text;
BEGIN
    IF p_source_relation IS NULL OR NOT pg_temp.relation_exists(p_source_relation) THEN
        PERFORM pg_temp.create_empty_view(p_view_name, p_columns);
        RETURN;
    END IF;

    FOR v_item IN SELECT * FROM jsonb_array_elements(p_columns)
    LOOP
        v_exprs := v_exprs || pg_temp.col_expr(
            p_source_relation,
            p_alias,
            v_item->>'src',
            v_item->>'out',
            v_item->>'type',
            COALESCE(v_item->>'fallback', 'NULL')
        );
    END LOOP;

    v_sql := format(
        'CREATE OR REPLACE VIEW analytics.%I AS SELECT %s FROM %s %I',
        p_view_name,
        array_to_string(v_exprs, ', '),
        p_source_relation,
        p_alias
    );

    IF p_where_clause IS NOT NULL AND length(trim(p_where_clause)) > 0 THEN
        v_sql := v_sql || ' WHERE ' || p_where_clause;
    END IF;

    EXECUTE v_sql;
END;
$$;

CREATE OR REPLACE FUNCTION pg_temp.first_existing_relation(p_relations text[])
RETURNS text
LANGUAGE plpgsql
AS $$
DECLARE
    v_relation text;
BEGIN
    FOREACH v_relation IN ARRAY p_relations
    LOOP
        IF pg_temp.relation_exists(v_relation) THEN
            RETURN v_relation;
        END IF;
    END LOOP;
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION pg_temp.text_col_or_null(p_relation text, p_alias text, p_column text, p_out text)
RETURNS text LANGUAGE sql AS $$
    SELECT pg_temp.col_expr(p_relation, p_alias, p_column, p_out, 'text', 'NULL')
$$;

CREATE OR REPLACE FUNCTION pg_temp.numeric_col_or_null(p_relation text, p_alias text, p_column text, p_out text)
RETURNS text LANGUAGE sql AS $$
    SELECT pg_temp.col_expr(p_relation, p_alias, p_column, p_out, 'numeric', 'NULL')
$$;

CREATE OR REPLACE FUNCTION pg_temp.boolean_col_or_null(p_relation text, p_alias text, p_column text, p_out text)
RETURNS text LANGUAGE sql AS $$
    SELECT pg_temp.col_expr(p_relation, p_alias, p_column, p_out, 'boolean', 'NULL')
$$;

CREATE OR REPLACE FUNCTION pg_temp.timestamptz_col_or_null(p_relation text, p_alias text, p_column text, p_out text)
RETURNS text LANGUAGE sql AS $$
    SELECT pg_temp.col_expr(p_relation, p_alias, p_column, p_out, 'timestamptz', 'NULL')
$$;

CREATE TABLE IF NOT EXISTS analytics.semantic_model_notes (
    mart_name text NOT NULL,
    column_name text NOT NULL DEFAULT '',
    description text NOT NULL,
    source_hint text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (mart_name, column_name)
);

TRUNCATE analytics.semantic_model_notes;

INSERT INTO analytics.semantic_model_notes (mart_name, column_name, description, source_hint) VALUES
('mart_jobs', '', 'Curated job/job-folder dimension for operational reporting.', 'dashboard_jobs when available, otherwise jobs'),
('mart_documents', '', 'Document inventory and extraction health for scanned SharePoint files.', 'documents'),
('mart_estimate_template_rows', '', 'Mapped estimate workbook rows for estimator analytics and calibration.', 'estimate_template_rows'),
('mart_unknown_template_rows', '', 'Grouped unknown template rows for parser mapping review.', 'estimate_template_rows'),
('mart_material_history', '', 'Job/package material history normalized for estimator defaults.', 'job_package_summary + estimate_jobs'),
('mart_labor_history', '', 'Job/package labor history normalized for estimator defaults.', 'job_package_summary + estimate_jobs'),
('mart_material_defaults', '', 'Precomputed material quantity/cost defaults from relationship mining.', 'relationship_material_qty_ratios'),
('mart_labor_defaults', '', 'Precomputed labor productivity defaults from relationship mining.', 'relationship_labor_rates'),
('mart_pricing_catalog', '', 'Current and historical pricing catalog rows used by estimator workbench.', 'pricing_catalog'),
('mart_repairs', '', 'VSimple repair jobs, scope text, and outcomes.', 'repair_jobs + repair_scope_text + repair_outcomes'),
('mart_repair_materials', '', 'Repair material usage history.', 'repair_material_usage'),
('mart_repair_labor', '', 'Repair labor usage history.', 'repair_labor_usage'),
('mart_repair_defaults', '', 'Repair default ranges by repair type / roof type from repair profiling.', 'repair_profile_summary'),
('mart_quality_warnings', '', 'Operational warnings surfaced for QA and support.', 'scan_warnings and job warnings'),
('mart_timesheets', '', 'Office/admin/sales timesheet entries for labor and project touch reporting.', 'office_timesheet_entries'),
('mart_estimator_feedback', '', 'Estimator edit-feedback history when persisted to the database.', 'estimator_edit_history or estimator_feedback'),
('mart_rule_candidates', '', 'Candidate estimating rules generated by relationship mining.', 'estimator_rule_suggestions or repair rule suggestions');

-- 1. Jobs
SELECT pg_temp.create_simple_mart(
    'mart_jobs',
    pg_temp.first_existing_relation(ARRAY['dashboard_jobs', 'jobs']),
    'j',
    $json$[
      {"out":"job_id","src":"job_id","type":"text"},
      {"out":"division","src":"division","type":"text"},
      {"out":"pipeline_status","src":"pipeline_status","type":"text"},
      {"out":"status","src":"status","type":"text"},
      {"out":"customer","src":"customer","type":"text"},
      {"out":"job_name","src":"job_name","type":"text"},
      {"out":"job_type","src":"job_type","type":"text"},
      {"out":"site_address","src":"site_address","type":"text"},
      {"out":"city","src":"city","type":"text"},
      {"out":"state","src":"state","type":"text"},
      {"out":"zip_code","src":"zip_code","type":"text"},
      {"out":"estimated_sqft","src":"estimated_sqft","type":"numeric"},
      {"out":"material_subtotal","src":"material_subtotal","type":"numeric"},
      {"out":"labor_subtotal","src":"labor_subtotal","type":"numeric"},
      {"out":"total_job_cost","src":"total_job_cost","type":"numeric"},
      {"out":"final_price","src":"final_price","type":"numeric"},
      {"out":"price_per_sqft","src":"price_per_sqft","type":"numeric"},
      {"out":"has_signed_contract","src":"has_signed_contract","type":"boolean"},
      {"out":"has_invoice","src":"has_invoice","type":"boolean"},
      {"out":"has_warranty","src":"has_warranty","type":"boolean"},
      {"out":"has_proposal","src":"has_proposal","type":"boolean"},
      {"out":"has_job_spec","src":"has_job_spec","type":"boolean"},
      {"out":"has_aerial","src":"has_aerial","type":"boolean"},
      {"out":"photo_count","src":"photo_count","type":"numeric"},
      {"out":"folder_path","src":"folder_path","type":"text"},
      {"out":"folder_url","src":"folder_url","type":"text"},
      {"out":"primary_doc_link","src":"primary_doc_link","type":"text"},
      {"out":"proposal_url","src":"proposal_url","type":"text"},
      {"out":"estimate_url","src":"estimate_url","type":"text"},
      {"out":"contract_url","src":"contract_url","type":"text"},
      {"out":"invoice_url","src":"invoice_url","type":"text"},
      {"out":"job_tracking_url","src":"job_tracking_url","type":"text"},
      {"out":"warnings","src":"warnings","type":"text"},
      {"out":"source_year","src":"source_year","type":"text"},
      {"out":"last_scanned_at","src":"last_scanned_at","type":"timestamptz"},
      {"out":"updated_at","src":"updated_at","type":"timestamptz"}
    ]$json$::jsonb
);

-- 2. Documents
SELECT pg_temp.create_simple_mart(
    'mart_documents',
    'documents',
    'd',
    $json$[
      {"out":"document_id","src":"document_id","type":"text"},
      {"out":"job_id","src":"job_id","type":"text"},
      {"out":"document_type","src":"document_type","type":"text"},
      {"out":"classification_reason","src":"classification_reason","type":"text"},
      {"out":"file_name","src":"file_name","type":"text"},
      {"out":"file_extension","src":"file_extension","type":"text"},
      {"out":"mime_type","src":"mime_type","type":"text"},
      {"out":"size_bytes","src":"size_bytes","type":"numeric"},
      {"out":"sharepoint_url","src":"sharepoint_url","type":"text"},
      {"out":"folder_path","src":"folder_path","type":"text"},
      {"out":"relative_path","src":"relative_path","type":"text"},
      {"out":"source_year","src":"source_year","type":"numeric"},
      {"out":"source_division","src":"source_division","type":"text"},
      {"out":"extraction_status","src":"extraction_status","type":"text"},
      {"out":"extraction_method","src":"extraction_method","type":"text"},
      {"out":"extraction_error","src":"extraction_error","type":"text"},
      {"out":"requires_ocr","src":"requires_ocr","type":"boolean"},
      {"out":"modified_at","src":"modified_at","type":"timestamptz"},
      {"out":"extracted_at","src":"extracted_at","type":"timestamptz"},
      {"out":"created_at","src":"created_at","type":"timestamptz"},
      {"out":"updated_at","src":"updated_at","type":"timestamptz"}
    ]$json$::jsonb
);

-- 3. Estimate template rows
SELECT pg_temp.create_simple_mart(
    'mart_estimate_template_rows',
    'estimate_template_rows',
    'r',
    $json$[
      {"out":"template_row_id","src":"template_row_id","type":"text"},
      {"out":"document_id","src":"document_id","type":"text"},
      {"out":"job_id","src":"job_id","type":"text"},
      {"out":"source_file","src":"source_file","type":"text"},
      {"out":"template_type","src":"template_type","type":"text"},
      {"out":"sheet_name","src":"sheet_name","type":"text"},
      {"out":"row_number","src":"row_number","type":"numeric"},
      {"out":"template_bucket","src":"template_bucket","type":"text"},
      {"out":"template_section","src":"template_section","type":"text"},
      {"out":"line_item_kind","src":"line_item_kind","type":"text"},
      {"out":"row_label","src":"row_label","type":"text"},
      {"out":"selected_item_name","src":"selected_item_name","type":"text"},
      {"out":"quantity","src":"quantity","type":"numeric"},
      {"out":"unit","src":"unit","type":"text"},
      {"out":"unit_price","src":"unit_price","type":"numeric"},
      {"out":"estimated_units","src":"estimated_units","type":"numeric"},
      {"out":"estimated_cost","src":"estimated_cost","type":"numeric"},
      {"out":"days","src":"days","type":"numeric"},
      {"out":"crew_size","src":"crew_size","type":"numeric"},
      {"out":"total_hours","src":"total_hours","type":"numeric"},
      {"out":"daily_rate","src":"daily_rate","type":"numeric"},
      {"out":"warranty_years","src":"warranty_years","type":"numeric"},
      {"out":"overhead_pct","src":"overhead_pct","type":"numeric"},
      {"out":"profit_pct","src":"profit_pct","type":"numeric"},
      {"out":"needs_review","src":"needs_review","type":"boolean"},
      {"out":"parsed_confidence","src":"parsed_confidence","type":"numeric"},
      {"out":"parser_version","src":"parser_version","type":"text"},
      {"out":"created_at","src":"created_at","type":"timestamptz"},
      {"out":"updated_at","src":"updated_at","type":"timestamptz"}
    ]$json$::jsonb,
    'COALESCE(NULLIF(r.template_bucket, ''''), ''unknown'') <> ''unknown'''
);

-- 4. Unknown template row clusters
DO $$
DECLARE
    v_columns jsonb := $json$[
      {"out":"cluster_key","type":"text"},
      {"out":"row_count","type":"numeric"},
      {"out":"distinct_file_count","type":"numeric"},
      {"out":"template_type","type":"text"},
      {"out":"sheet_name","type":"text"},
      {"out":"row_number","type":"numeric"},
      {"out":"row_label","type":"text"},
      {"out":"selected_item_name","type":"text"},
      {"out":"line_item_kind","type":"text"},
      {"out":"sample_source_files","type":"text"},
      {"out":"sample_job_ids","type":"text"},
      {"out":"suggested_bucket","type":"text"},
      {"out":"suggested_line_item_kind","type":"text"},
      {"out":"confidence","type":"text"},
      {"out":"review_status","type":"text"}
    ]$json$::jsonb;
BEGIN
    IF NOT pg_temp.relation_exists('estimate_template_rows') THEN
        PERFORM pg_temp.create_empty_view('mart_unknown_template_rows', v_columns);
        RETURN;
    END IF;

    EXECUTE $sql$
        CREATE OR REPLACE VIEW analytics.mart_unknown_template_rows AS
        SELECT
            md5(concat_ws('|', COALESCE(template_type, ''), COALESCE(sheet_name, ''), row_number::text, COALESCE(row_label, ''), COALESCE(selected_item_name, ''), COALESCE(line_item_kind, ''))) AS cluster_key,
            count(*)::numeric AS row_count,
            count(DISTINCT source_file)::numeric AS distinct_file_count,
            template_type,
            sheet_name,
            row_number::numeric AS row_number,
            row_label,
            selected_item_name,
            line_item_kind,
            left(string_agg(DISTINCT source_file, ' | ' ORDER BY source_file), 500) AS sample_source_files,
            left(string_agg(DISTINCT job_id, ' | ' ORDER BY job_id), 500) AS sample_job_ids,
            CASE
                WHEN lower(concat_ws(' ', row_label, selected_item_name)) ~ 'labor|crew|travel|loading|cleanup|clean up|setup|set up' THEN 'labor_review'
                WHEN lower(concat_ws(' ', row_label, selected_item_name)) ~ 'total|subtotal|overhead|profit|price' THEN 'total_or_formula'
                WHEN lower(concat_ws(' ', row_label, selected_item_name)) ~ 'lift|generator|dumpster|freight|delivery|truck|hotel|lodging' THEN 'adder_or_equipment'
                WHEN lower(concat_ws(' ', row_label, selected_item_name)) ~ 'primer|coating|foam|caulk|sealant|fastener|seam|fabric|board' THEN 'material_review'
                ELSE NULL
            END AS suggested_bucket,
            CASE
                WHEN lower(concat_ws(' ', row_label, selected_item_name)) ~ 'labor|crew|travel|loading|cleanup|clean up|setup|set up' THEN 'labor'
                WHEN lower(concat_ws(' ', row_label, selected_item_name)) ~ 'lift|generator|dumpster|freight|delivery|truck|hotel|lodging' THEN 'equipment'
                WHEN lower(concat_ws(' ', row_label, selected_item_name)) ~ 'primer|coating|foam|caulk|sealant|fastener|seam|fabric|board' THEN 'material'
                WHEN lower(concat_ws(' ', row_label, selected_item_name)) ~ 'total|subtotal|overhead|profit|price' THEN 'total'
                ELSE NULL
            END AS suggested_line_item_kind,
            CASE WHEN count(*) >= 100 THEN 'high' WHEN count(*) >= 20 THEN 'medium' ELSE 'low' END AS confidence,
            'needs_review'::text AS review_status
        FROM estimate_template_rows
        WHERE COALESCE(NULLIF(template_bucket, ''), 'unknown') = 'unknown'
        GROUP BY template_type, sheet_name, row_number, row_label, selected_item_name, line_item_kind
    $sql$;
END $$;

-- 5/6. Material and labor history from job_package_summary + estimate_jobs.
DO $$
DECLARE
    v_history_columns jsonb := $json$[
      {"out":"job_id","type":"text"},
      {"out":"source_year","type":"text"},
      {"out":"division","type":"text"},
      {"out":"pipeline_status","type":"text"},
      {"out":"status","type":"text"},
      {"out":"customer","type":"text"},
      {"out":"job_name","type":"text"},
      {"out":"template_type","type":"text"},
      {"out":"project_type","type":"text"},
      {"out":"substrate","type":"text"},
      {"out":"area_sqft","type":"numeric"},
      {"out":"area_bucket","type":"text"},
      {"out":"warranty_years","type":"numeric"},
      {"out":"wet_mils","type":"numeric"},
      {"out":"coating_type","type":"text"},
      {"out":"roof_condition","type":"text"},
      {"out":"access_complexity","type":"text"},
      {"out":"package","type":"text"},
      {"out":"included","type":"boolean"},
      {"out":"total_quantity","type":"numeric"},
      {"out":"unit","type":"text"},
      {"out":"total_cost","type":"numeric"},
      {"out":"total_hours","type":"numeric"},
      {"out":"qty_per_sqft","type":"numeric"},
      {"out":"cost_per_sqft","type":"numeric"},
      {"out":"hours_per_1000_sqft","type":"numeric"},
      {"out":"has_physical_quantity","type":"boolean"},
      {"out":"has_allowance","type":"boolean"},
      {"out":"review_required","type":"boolean"},
      {"out":"evidence_line_item_ids","type":"text"}
    ]$json$::jsonb;
    v_join text := '';
    v_sql text;
    v_select text;
BEGIN
    IF NOT pg_temp.relation_exists('job_package_summary') THEN
        PERFORM pg_temp.create_empty_view('mart_material_history', v_history_columns);
        PERFORM pg_temp.create_empty_view('mart_labor_history', v_history_columns);
        RETURN;
    END IF;

    IF pg_temp.relation_exists('estimate_jobs') THEN
        v_join := ' LEFT JOIN estimate_jobs ej ON ej.job_id = jps.job_id';
    END IF;

    v_select := array_to_string(ARRAY[
        pg_temp.text_col_or_null('job_package_summary', 'jps', 'job_id', 'job_id'),
        pg_temp.text_col_or_null('estimate_jobs', 'ej', 'source_year', 'source_year'),
        pg_temp.text_col_or_null('estimate_jobs', 'ej', 'division', 'division'),
        pg_temp.text_col_or_null('estimate_jobs', 'ej', 'pipeline_status', 'pipeline_status'),
        pg_temp.text_col_or_null('estimate_jobs', 'ej', 'status', 'status'),
        pg_temp.text_col_or_null('estimate_jobs', 'ej', 'customer', 'customer'),
        pg_temp.text_col_or_null('estimate_jobs', 'ej', 'job_name', 'job_name'),
        pg_temp.text_col_or_null('estimate_jobs', 'ej', 'template_type', 'template_type'),
        pg_temp.text_col_or_null('estimate_jobs', 'ej', 'project_type', 'project_type'),
        pg_temp.text_col_or_null('estimate_jobs', 'ej', 'substrate', 'substrate'),
        pg_temp.numeric_col_or_null('estimate_jobs', 'ej', 'area_sqft', 'area_sqft'),
        pg_temp.text_col_or_null('estimate_jobs', 'ej', 'area_bucket', 'area_bucket'),
        pg_temp.numeric_col_or_null('estimate_jobs', 'ej', 'warranty_years', 'warranty_years'),
        pg_temp.numeric_col_or_null('estimate_jobs', 'ej', 'wet_mils', 'wet_mils'),
        pg_temp.text_col_or_null('estimate_jobs', 'ej', 'coating_type', 'coating_type'),
        pg_temp.text_col_or_null('estimate_jobs', 'ej', 'roof_condition', 'roof_condition'),
        pg_temp.text_col_or_null('estimate_jobs', 'ej', 'access_complexity', 'access_complexity'),
        pg_temp.text_col_or_null('job_package_summary', 'jps', 'package', 'package'),
        pg_temp.boolean_col_or_null('job_package_summary', 'jps', 'included', 'included'),
        pg_temp.numeric_col_or_null('job_package_summary', 'jps', 'total_quantity', 'total_quantity'),
        pg_temp.text_col_or_null('job_package_summary', 'jps', 'unit', 'unit'),
        pg_temp.numeric_col_or_null('job_package_summary', 'jps', 'total_cost', 'total_cost'),
        pg_temp.numeric_col_or_null('job_package_summary', 'jps', 'total_hours', 'total_hours'),
        pg_temp.numeric_col_or_null('job_package_summary', 'jps', 'qty_per_sqft', 'qty_per_sqft'),
        pg_temp.numeric_col_or_null('job_package_summary', 'jps', 'cost_per_sqft', 'cost_per_sqft'),
        CASE
            WHEN pg_temp.relation_exists('estimate_jobs')
             AND pg_temp.has_col('estimate_jobs', 'area_sqft')
             AND pg_temp.has_col('job_package_summary', 'total_hours')
                THEN 'CASE WHEN COALESCE(ej.area_sqft, 0) > 0 THEN jps.total_hours / ej.area_sqft * 1000 ELSE NULL END::numeric AS hours_per_1000_sqft'
            ELSE 'NULL::numeric AS hours_per_1000_sqft'
        END,
        pg_temp.boolean_col_or_null('job_package_summary', 'jps', 'has_physical_quantity', 'has_physical_quantity'),
        pg_temp.boolean_col_or_null('job_package_summary', 'jps', 'has_allowance', 'has_allowance'),
        pg_temp.boolean_col_or_null('job_package_summary', 'jps', 'review_required', 'review_required'),
        pg_temp.text_col_or_null('job_package_summary', 'jps', 'evidence_line_item_ids', 'evidence_line_item_ids')
    ], ', ');

    v_sql := format(
        'CREATE OR REPLACE VIEW analytics.mart_material_history AS SELECT %s FROM job_package_summary jps%s WHERE COALESCE(jps.package, '''') !~* ''^labor_|labor|crew|traveling$''',
        v_select,
        v_join
    );
    EXECUTE v_sql;

    v_sql := format(
        'CREATE OR REPLACE VIEW analytics.mart_labor_history AS SELECT %s FROM job_package_summary jps%s WHERE COALESCE(jps.package, '''') ~* ''^labor_|labor|crew|traveling$'' OR COALESCE(jps.total_hours, 0) > 0',
        v_select,
        v_join
    );
    EXECUTE v_sql;
END $$;

-- 7. Material defaults
SELECT pg_temp.create_simple_mart(
    'mart_material_defaults',
    'relationship_material_qty_ratios',
    'm',
    $json$[
      {"out":"source_year","src":"source_year","type":"text"},
      {"out":"division","src":"division","type":"text"},
      {"out":"template_type","src":"template_type","type":"text"},
      {"out":"project_type","src":"project_type","type":"text"},
      {"out":"substrate","src":"substrate","type":"text"},
      {"out":"coating_type","src":"coating_type","type":"text"},
      {"out":"warranty_years","src":"warranty_years","type":"numeric"},
      {"out":"wet_mils","src":"wet_mils","type":"numeric"},
      {"out":"package","src":"package","type":"text"},
      {"out":"unit","src":"unit","type":"text"},
      {"out":"median_qty_per_sqft","src":"median_qty_per_sqft","type":"numeric"},
      {"out":"p25_qty_per_sqft","src":"p25_qty_per_sqft","type":"numeric"},
      {"out":"p75_qty_per_sqft","src":"p75_qty_per_sqft","type":"numeric"},
      {"out":"median_cost_per_sqft","src":"median_cost_per_sqft","type":"numeric"},
      {"out":"job_count","src":"job_count","type":"numeric"},
      {"out":"confidence","src":"confidence","type":"text"}
    ]$json$::jsonb
);

-- 8. Labor defaults
SELECT pg_temp.create_simple_mart(
    'mart_labor_defaults',
    'relationship_labor_rates',
    'l',
    $json$[
      {"out":"source_year","src":"source_year","type":"text"},
      {"out":"division","src":"division","type":"text"},
      {"out":"template_type","src":"template_type","type":"text"},
      {"out":"project_type","src":"project_type","type":"text"},
      {"out":"substrate","src":"substrate","type":"text"},
      {"out":"coating_type","src":"coating_type","type":"text"},
      {"out":"warranty_years","src":"warranty_years","type":"numeric"},
      {"out":"labor_package","src":"labor_package","type":"text"},
      {"out":"package","src":"package","type":"text"},
      {"out":"median_hours_per_1000_sqft","src":"median_hours_per_1000_sqft","type":"numeric"},
      {"out":"p25_hours_per_1000_sqft","src":"p25_hours_per_1000_sqft","type":"numeric"},
      {"out":"p75_hours_per_1000_sqft","src":"p75_hours_per_1000_sqft","type":"numeric"},
      {"out":"median_cost_per_sqft","src":"median_cost_per_sqft","type":"numeric"},
      {"out":"job_count","src":"job_count","type":"numeric"},
      {"out":"confidence","src":"confidence","type":"text"}
    ]$json$::jsonb
);

-- 9. Pricing catalog
SELECT pg_temp.create_simple_mart(
    'mart_pricing_catalog',
    'pricing_catalog',
    'p',
    $json$[
      {"out":"pricing_item_id","src":"pricing_item_id","type":"text"},
      {"out":"vendor","src":"vendor","type":"text"},
      {"out":"category","src":"category","type":"text"},
      {"out":"product_name","src":"product_name","type":"text"},
      {"out":"product_name_normalized","src":"product_name_normalized","type":"text"},
      {"out":"description","src":"description","type":"text"},
      {"out":"unit_price","src":"unit_price","type":"numeric"},
      {"out":"unit_of_measure","src":"unit_of_measure","type":"text"},
      {"out":"package_size","src":"package_size","type":"text"},
      {"out":"price_basis","src":"price_basis","type":"text"},
      {"out":"price_per_gallon","src":"price_per_gallon","type":"numeric"},
      {"out":"price_per_sqft","src":"price_per_sqft","type":"numeric"},
      {"out":"price_per_unit","src":"price_per_unit","type":"numeric"},
      {"out":"vendor_item_no","src":"vendor_item_no","type":"text"},
      {"out":"source_file","src":"source_file","type":"text"},
      {"out":"source_type","src":"source_type","type":"text"},
      {"out":"effective_date","src":"effective_date","type":"date"},
      {"out":"expiration_date","src":"expiration_date","type":"date"},
      {"out":"is_current","src":"is_current","type":"boolean"},
      {"out":"status","src":"status","type":"text"},
      {"out":"needs_review","src":"needs_review","type":"boolean"},
      {"out":"review_notes","src":"review_notes","type":"text"},
      {"out":"notes","src":"notes","type":"text"},
      {"out":"updated_at","src":"updated_at","type":"timestamptz"}
    ]$json$::jsonb
);

-- 10. Repairs with scope and outcome.
DO $$
DECLARE
    v_columns jsonb := $json$[
      {"out":"repair_id","type":"text"},
      {"out":"customer","type":"text"},
      {"out":"job_name","type":"text"},
      {"out":"status","type":"text"},
      {"out":"type_of_repair","type":"text"},
      {"out":"roof_type","type":"text"},
      {"out":"repair_address","type":"text"},
      {"out":"city","type":"text"},
      {"out":"state","type":"text"},
      {"out":"zip","type":"text"},
      {"out":"url","type":"text"},
      {"out":"scope_of_work","type":"text"},
      {"out":"work_performed_long_text","type":"text"},
      {"out":"special_notes","type":"text"},
      {"out":"materials_used","type":"text"},
      {"out":"combined_scope_text","type":"text"},
      {"out":"total_bill_amount","type":"numeric"},
      {"out":"invoice_amount","type":"numeric"},
      {"out":"gross_profit","type":"numeric"},
      {"out":"gross_profit_percentage","type":"numeric"},
      {"out":"created_date","type":"date"},
      {"out":"completion_date","type":"date"},
      {"out":"source_file","type":"text"},
      {"out":"updated_at","type":"timestamptz"}
    ]$json$::jsonb;
    v_join_scope text := '';
    v_join_outcome text := '';
    v_select text;
BEGIN
    IF NOT pg_temp.relation_exists('repair_jobs') THEN
        PERFORM pg_temp.create_empty_view('mart_repairs', v_columns);
        RETURN;
    END IF;

    IF pg_temp.relation_exists('repair_scope_text') THEN
        v_join_scope := ' LEFT JOIN repair_scope_text st ON st.repair_id = r.repair_id';
    END IF;
    IF pg_temp.relation_exists('repair_outcomes') THEN
        v_join_outcome := ' LEFT JOIN repair_outcomes ro ON ro.repair_id = r.repair_id';
    END IF;

    v_select := array_to_string(ARRAY[
        pg_temp.text_col_or_null('repair_jobs', 'r', 'repair_id', 'repair_id'),
        pg_temp.text_col_or_null('repair_jobs', 'r', 'customer', 'customer'),
        pg_temp.text_col_or_null('repair_jobs', 'r', 'job_name', 'job_name'),
        pg_temp.text_col_or_null('repair_jobs', 'r', 'status', 'status'),
        pg_temp.text_col_or_null('repair_jobs', 'r', 'type_of_repair', 'type_of_repair'),
        pg_temp.text_col_or_null('repair_jobs', 'r', 'roof_type', 'roof_type'),
        pg_temp.text_col_or_null('repair_jobs', 'r', 'repair_address', 'repair_address'),
        pg_temp.text_col_or_null('repair_jobs', 'r', 'city', 'city'),
        pg_temp.text_col_or_null('repair_jobs', 'r', 'state', 'state'),
        pg_temp.text_col_or_null('repair_jobs', 'r', 'zip', 'zip'),
        pg_temp.text_col_or_null('repair_jobs', 'r', 'url', 'url'),
        pg_temp.text_col_or_null('repair_scope_text', 'st', 'scope_of_work', 'scope_of_work'),
        pg_temp.text_col_or_null('repair_scope_text', 'st', 'work_performed_long_text', 'work_performed_long_text'),
        pg_temp.text_col_or_null('repair_scope_text', 'st', 'special_notes', 'special_notes'),
        pg_temp.text_col_or_null('repair_scope_text', 'st', 'materials_used', 'materials_used'),
        pg_temp.text_col_or_null('repair_scope_text', 'st', 'combined_scope_text', 'combined_scope_text'),
        pg_temp.numeric_col_or_null('repair_outcomes', 'ro', 'total_bill_amount', 'total_bill_amount'),
        pg_temp.numeric_col_or_null('repair_outcomes', 'ro', 'invoice_amount', 'invoice_amount'),
        pg_temp.numeric_col_or_null('repair_outcomes', 'ro', 'gross_profit', 'gross_profit'),
        pg_temp.numeric_col_or_null('repair_outcomes', 'ro', 'gross_profit_percentage', 'gross_profit_percentage'),
        pg_temp.col_expr('repair_jobs', 'r', 'created_date', 'created_date', 'date', 'NULL'),
        pg_temp.col_expr('repair_jobs', 'r', 'completion_date', 'completion_date', 'date', 'NULL'),
        pg_temp.text_col_or_null('repair_jobs', 'r', 'source_file', 'source_file'),
        pg_temp.timestamptz_col_or_null('repair_jobs', 'r', 'updated_at', 'updated_at')
    ], ', ');

    EXECUTE format(
        'CREATE OR REPLACE VIEW analytics.mart_repairs AS SELECT %s FROM repair_jobs r%s%s',
        v_select,
        v_join_scope,
        v_join_outcome
    );
END $$;

-- 11. Repair materials
SELECT pg_temp.create_simple_mart(
    'mart_repair_materials',
    'repair_material_usage',
    'm',
    $json$[
      {"out":"repair_material_usage_id","src":"repair_material_usage_id","type":"text"},
      {"out":"repair_id","src":"repair_id","type":"text"},
      {"out":"material_package","src":"material_package","type":"text"},
      {"out":"material_name","src":"material_name","type":"text"},
      {"out":"quantity","src":"quantity","type":"numeric"},
      {"out":"unit","src":"unit","type":"text"},
      {"out":"unit_cost","src":"unit_cost","type":"numeric"},
      {"out":"total_cost","src":"total_cost","type":"numeric"},
      {"out":"source_column","src":"source_column","type":"text"},
      {"out":"raw_materials_used","src":"raw_materials_used","type":"text"},
      {"out":"created_at","src":"created_at","type":"timestamptz"},
      {"out":"updated_at","src":"updated_at","type":"timestamptz"}
    ]$json$::jsonb
);

-- 12. Repair labor
SELECT pg_temp.create_simple_mart(
    'mart_repair_labor',
    'repair_labor_usage',
    'l',
    $json$[
      {"out":"repair_labor_usage_id","src":"repair_labor_usage_id","type":"text"},
      {"out":"repair_id","src":"repair_id","type":"text"},
      {"out":"labor_role","src":"labor_role","type":"text"},
      {"out":"technician_name","src":"technician_name","type":"text"},
      {"out":"labor_hours","src":"labor_hours","type":"numeric"},
      {"out":"labor_cost","src":"labor_cost","type":"numeric"},
      {"out":"total_labor_hours","src":"total_labor_hours","type":"numeric"},
      {"out":"source_column","src":"source_column","type":"text"},
      {"out":"created_at","src":"created_at","type":"timestamptz"},
      {"out":"updated_at","src":"updated_at","type":"timestamptz"}
    ]$json$::jsonb
);

-- 13. Repair defaults
SELECT pg_temp.create_simple_mart(
    'mart_repair_defaults',
    'repair_profile_summary',
    'p',
    $json$[
      {"out":"repair_type","src":"type_of_repair","type":"text"},
      {"out":"roof_type","src":"roof_type","type":"text"},
      {"out":"evidence_count","src":"evidence_count","type":"numeric"},
      {"out":"median_labor_hours","src":"median_labor_hours","type":"numeric"},
      {"out":"p25_labor_hours","src":"p25_labor_hours","type":"numeric"},
      {"out":"p75_labor_hours","src":"p75_labor_hours","type":"numeric"},
      {"out":"median_invoice_amount","src":"median_invoice_amount","type":"numeric"},
      {"out":"p25_invoice_amount","src":"p25_invoice_amount","type":"numeric"},
      {"out":"p75_invoice_amount","src":"p75_invoice_amount","type":"numeric"},
      {"out":"common_material_packages","src":"common_material_packages","type":"text"},
      {"out":"confidence","src":"confidence","type":"text"},
      {"out":"created_at","src":"created_at","type":"timestamptz"}
    ]$json$::jsonb
);

-- 14. Quality warnings
DO $$
DECLARE
    v_columns jsonb := $json$[
      {"out":"warning_id","type":"text"},
      {"out":"job_id","type":"text"},
      {"out":"source","type":"text"},
      {"out":"warning_type","type":"text"},
      {"out":"severity","type":"text"},
      {"out":"warning_message","type":"text"},
      {"out":"source_file","type":"text"},
      {"out":"created_at","type":"timestamptz"}
    ]$json$::jsonb;
BEGIN
    IF pg_temp.relation_exists('scan_warnings') THEN
        EXECUTE $sql$
            CREATE OR REPLACE VIEW analytics.mart_quality_warnings AS
            SELECT
                warning_id::text,
                job_id::text,
                'scan_warnings'::text AS source,
                warning_type::text,
                severity::text,
                warning_message::text,
                source_file::text,
                created_at::timestamptz
            FROM scan_warnings
            UNION ALL
            SELECT
                md5(COALESCE(job_id, '') || COALESCE(warnings, ''))::text AS warning_id,
                job_id::text,
                'jobs.warnings'::text AS source,
                'job_warning'::text AS warning_type,
                'warning'::text AS severity,
                warnings::text AS warning_message,
                NULL::text AS source_file,
                updated_at::timestamptz AS created_at
            FROM jobs
            WHERE COALESCE(warnings, '') <> ''
        $sql$;
    ELSIF pg_temp.relation_exists('jobs') THEN
        EXECUTE $sql$
            CREATE OR REPLACE VIEW analytics.mart_quality_warnings AS
            SELECT
                md5(COALESCE(job_id, '') || COALESCE(warnings, ''))::text AS warning_id,
                job_id::text,
                'jobs.warnings'::text AS source,
                'job_warning'::text AS warning_type,
                'warning'::text AS severity,
                warnings::text AS warning_message,
                NULL::text AS source_file,
                updated_at::timestamptz AS created_at
            FROM jobs
            WHERE COALESCE(warnings, '') <> ''
        $sql$;
    ELSE
        PERFORM pg_temp.create_empty_view('mart_quality_warnings', v_columns);
    END IF;
END $$;

-- 15. Timesheets
SELECT pg_temp.create_simple_mart(
    'mart_timesheets',
    'office_timesheet_entries',
    't',
    $json$[
      {"out":"entry_id","src":"entry_id","type":"text"},
      {"out":"employee","src":"employee","type":"text"},
      {"out":"work_date","src":"work_date","type":"date"},
      {"out":"project_name","src":"project_name","type":"text"},
      {"out":"code","src":"code","type":"text"},
      {"out":"duration_hours","src":"duration_hours","type":"numeric"},
      {"out":"row_type","src":"row_type","type":"text"},
      {"out":"notes","src":"notes","type":"text"},
      {"out":"source_file","src":"source_file","type":"text"},
      {"out":"source_sheet","src":"source_sheet","type":"text"},
      {"out":"warnings","src":"warnings","type":"text"},
      {"out":"updated_at","src":"updated_at","type":"timestamptz"}
    ]$json$::jsonb
);

-- 16. Estimator feedback
SELECT pg_temp.create_simple_mart(
    'mart_estimator_feedback',
    pg_temp.first_existing_relation(ARRAY['estimator_edit_history', 'estimator_feedback']),
    'f',
    $json$[
      {"out":"estimate_id","src":"estimate_id","type":"text"},
      {"out":"run_id","src":"run_id","type":"text"},
      {"out":"estimator","src":"estimator","type":"text"},
      {"out":"field_name","src":"field_name","type":"text"},
      {"out":"package","src":"package","type":"text"},
      {"out":"suggested_value","src":"suggested_value","type":"text"},
      {"out":"final_value","src":"final_value","type":"text"},
      {"out":"difference_pct","src":"difference_pct","type":"numeric"},
      {"out":"reason","src":"reason","type":"text"},
      {"out":"created_at","src":"created_at","type":"timestamptz"}
    ]$json$::jsonb
);

-- 17. Rule candidates
SELECT pg_temp.create_simple_mart(
    'mart_rule_candidates',
    pg_temp.first_existing_relation(ARRAY['estimator_rule_suggestions', 'repair_estimator_rule_suggestions']),
    'r',
    $json$[
      {"out":"rule_id","src":"rule_id","type":"text"},
      {"out":"rule_type","src":"rule_type","type":"text"},
      {"out":"trade_type","src":"trade_type","type":"text"},
      {"out":"condition","src":"condition","type":"text"},
      {"out":"recommendation","src":"recommendation","type":"text"},
      {"out":"supporting_job_count","src":"supporting_job_count","type":"numeric"},
      {"out":"confidence","src":"confidence","type":"text"},
      {"out":"status","src":"status","type":"text"},
      {"out":"created_at","src":"created_at","type":"timestamptz"}
    ]$json$::jsonb
);

-- Compatibility aliases for repair mart names used by some Power BI/query checks.
CREATE OR REPLACE VIEW analytics.mart_repair_jobs AS
SELECT * FROM analytics.mart_repairs;

CREATE OR REPLACE VIEW analytics.mart_repair_material_usage AS
SELECT * FROM analytics.mart_repair_materials;

CREATE OR REPLACE VIEW analytics.mart_repair_labor_usage AS
SELECT * FROM analytics.mart_repair_labor;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'powerbi_reader') THEN
        BEGIN
            CREATE ROLE powerbi_reader;
        EXCEPTION
            WHEN insufficient_privilege THEN
                RAISE NOTICE 'Skipping powerbi_reader role creation: insufficient privilege.';
            WHEN duplicate_object THEN
                NULL;
        END;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'powerbi_reader') THEN
        GRANT USAGE ON SCHEMA analytics TO powerbi_reader;
        GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO powerbi_reader;
        ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT SELECT ON TABLES TO powerbi_reader;
    END IF;
EXCEPTION
    WHEN insufficient_privilege THEN
        RAISE NOTICE 'Skipping analytics grants for powerbi_reader: insufficient privilege.';
END $$;
