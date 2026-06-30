# Power BI Analytics Marts

Spray-Tec Power BI should connect to the `analytics` schema in Neon. The mart
layer is designed for reporting and operational support, not source-system
editing.

## Refresh

Apply or refresh the mart layer:

```bash
psql "$NEON_PSQL_URL" -f db/powerbi_marts.sql
```

or from Python:

```bash
python -m jobscan.analytics.powerbi_marts --db-url "$NEON_DATABASE_URL" --refresh
```

Show row counts and reader-role status:

```bash
python -m jobscan.analytics.powerbi_marts --db-url "$NEON_DATABASE_URL" --summary
```

The script does not print database secrets.

## Security

The SQL creates a read-only role named `powerbi_reader` when the executing
database user has permission. It grants:

- `USAGE` on schema `analytics`
- `SELECT` on analytics marts and notes

It does not grant access to raw operational tables.

If Neon permissions prevent role creation or grants, the script emits a notice
and still creates the analytics views. An administrator can create/grant the role
separately.

## Marts

- `analytics.mart_jobs`: job/job-folder operational dimension.
- `analytics.mart_documents`: SharePoint document inventory and extraction health.
- `analytics.mart_estimate_template_rows`: parsed/mapped estimate workbook rows.
- `analytics.mart_unknown_template_rows`: grouped unknown template rows for parser review.
- `analytics.mart_material_history`: normalized material package history.
- `analytics.mart_labor_history`: normalized labor package history.
- `analytics.mart_material_defaults`: relationship-mined material defaults.
- `analytics.mart_labor_defaults`: relationship-mined labor defaults.
- `analytics.mart_pricing_catalog`: pricing catalog rows used by estimating.
- `analytics.mart_repairs`: VSimple repair job, scope, and outcome facts.
- `analytics.mart_repair_materials`: repair material usage.
- `analytics.mart_repair_labor`: repair labor usage.
- `analytics.mart_repair_defaults`: repair default ranges.
- `analytics.mart_quality_warnings`: scan/job warning facts.
- `analytics.mart_timesheets`: office timesheet entries.
- `analytics.mart_estimator_feedback`: estimator edit history when database-backed.
- `analytics.mart_rule_candidates`: candidate estimator rules.

The table `analytics.semantic_model_notes` documents each mart for Power BI
modeling.

## Source Expectations

Most marts are views over existing operational tables. Missing optional tables
produce empty views with the expected columns so Power BI models can still load
while a pipeline is being rolled out.

Relationship marts depend on the relationship profiler:

```bash
python relationship_profiler.py --db-url "$NEON_DATABASE_URL" --output-dir output/relationships
```

Repair marts depend on the VSimple repair ingestion pipeline. Estimator feedback
currently appears only when edit history has been persisted to a database table.

## Power BI Notes

Recommended relationships:

- `mart_jobs[job_id]` to job-bearing marts such as documents, template rows,
  material history, and labor history.
- `mart_repairs[repair_id]` to repair material/labor marts.
- Pricing and default marts are usually lookup/reference tables filtered by
  package, division, project type, substrate, warranty, and year.

Use Import mode first. If the marts become large, add materialized views or
incremental refresh policies after the reporting model stabilizes.
