# Template Parsing Eval Placeholder

This folder is reserved for estimate template parsing sanity evals.

Planned checks:

- Roofing and insulation templates are detected separately.
- Known row maps produce expected `template_bucket` values.
- Manual adder rows are parsed without creating noisy review rows.
- Header and square-footage rows remain traceable to source cells.

Do not read raw Excel files for relationship mining evals. Relationship mining
should use `estimate_template_rows`, `estimate_line_item_classifications`,
`job_package_summary`, and `relationship_*` tables as the source of truth.

