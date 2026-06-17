-- Fuzzy job search support for Ask Spray-Tec.
-- Run after db/schema.sql on Postgres/Neon.
--
-- Local schema inspection on 2026-06-17:
-- Ask Spray-Tec prefers dashboard_jobs when available. The local dashboard_jobs
-- view exposed job_id, customer, job_name, site_address, city, state, division,
-- pipeline_status, status, folder_name, folder_path, folder_url, and estimate_file.
-- invoice_file was not present, so every optional index below is guarded.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

DO $$
DECLARE
    source_table TEXT := 'jobs';
    indexed_columns TEXT[] := ARRAY[
        'job_id',
        'customer',
        'job_name',
        'site_address',
        'city',
        'state',
        'division',
        'pipeline_status',
        'status',
        'folder_name',
        'folder_path',
        'estimate_file',
        'invoice_file',
        'primary_doc_name',
        'primary_doc_link',
        'proposal_url',
        'estimate_url',
        'contract_url',
        'invoice_url',
        'job_tracking_url',
        'warranty_url',
        'aerial_url'
    ];
    candidate_column TEXT;
    index_name TEXT;
BEGIN
    FOREACH candidate_column IN ARRAY indexed_columns LOOP
        IF EXISTS (
            SELECT 1
            FROM information_schema.columns c
            WHERE c.table_schema = 'public'
              AND c.table_name = source_table
              AND c.column_name = candidate_column
        ) THEN
            index_name := 'idx_jobs_search_' || regexp_replace(candidate_column, '[^a-zA-Z0-9_]', '_', 'g') || '_trgm';
            EXECUTE format(
                'CREATE INDEX IF NOT EXISTS %I ON public.%I USING gin (LOWER(COALESCE(%I::text, '''')) gin_trgm_ops)',
                index_name,
                source_table,
                candidate_column
            );
        ELSE
            RAISE NOTICE 'Skipping search index for missing column %.%', source_table, candidate_column;
        END IF;
    END LOOP;
END $$;
