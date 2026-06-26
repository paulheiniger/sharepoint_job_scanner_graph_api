# SharePoint Job Scanner — Project Instructions

## Project objective

This repository supports a production-oriented SharePoint job indexing,
document extraction, operational reporting, estimating, and bid-assistance
pipeline.

Optimize for:

- low Microsoft Graph usage
- incremental processing
- resumability
- auditability
- safe reruns
- reliable database-backed state
- actionable operational reporting

## Architecture principles

Treat Microsoft Graph as the source system, not as the operational state store.

Persist resolved identifiers, scan state, extraction state, timestamps, errors,
and delta tokens in PostgreSQL. Do not repeatedly query Graph for information
that has already been resolved and stored.

Prefer extending the existing architecture over creating parallel scanners,
databases, caches, or command paths.

Before changing behavior, inspect:

- existing CLI commands and flags
- database models and migrations
- Graph client and throttling behavior
- scan-state and identifier-resolution logic
- document extraction workflow
- tests and smoke-test commands
- configuration and environment-variable handling

## Microsoft Graph requirements

For ongoing scans:

- Prefer Microsoft Graph delta queries wherever supported.
- Store delta tokens by site, drive, and applicable scan scope.
- Process only new, changed, moved, or deleted items.
- Reuse stored site IDs, drive IDs, item IDs, list item IDs, and parent IDs.
- Do not perform name-based identifier resolution during every normal scan.
- Do not issue one Graph request per item when batching, caching, expansion,
  delta discovery, or database joins can avoid it.
- Apply bounded concurrency, retries, exponential backoff, jitter, and timeout
  handling.
- Respect Retry-After responses.
- Track Graph request counts, retries, throttles, cache hits, unchanged items,
  resolved identifiers, and unresolved identifiers.

Treat these as distinct identifiers:

- tenant ID
- site ID
- drive ID
- drive item ID
- SharePoint list ID
- list item ID
- folder path
- web URL

Do not assume that every document currently has every identifier.

If a delta token expires or becomes invalid, use a controlled reconciliation
path. Do not silently trigger an unrestricted full scan.

## Workflow separation

Keep these workflows logically and operationally separate:

1. initial full discovery
2. incremental delta scan
3. metadata extraction
4. document-content extraction
5. identifier repair
6. explicit reconciliation
7. historical backfill

Do not make an ordinary scan automatically perform an unrestricted identifier
repair, document backfill, or full Graph traversal unless explicitly requested.

Identifier repair should be bounded, resumable, rate-limited, and capable of
processing only unresolved records.

## Database behavior

Database operations and backfills must be idempotent and safely rerunnable.

Use:

- stable source identifiers
- unique constraints where duplication is possible
- upserts where appropriate
- explicit transactions for related writes
- checkpoints for long-running work
- processing-status fields
- timestamps for discovery, source modification, retrieval, processing, and
  successful synchronization

Do not use names or folder paths as permanent identities when stable Graph IDs
are available.

Do not overwrite authoritative source values with lower-confidence inferred
values.

Schema migrations should be backward-compatible when practical.

## Extraction behavior

Prefer deterministic extraction from structured files and known templates
before using probabilistic or LLM-based extraction.

Preserve:

- original source content or source reference
- extracted value
- extraction method
- confidence
- parser or model version
- validation result
- source document and page or worksheet when available

Financial values, invoice data, contract values, job IDs, and authoritative
identifiers must not be silently changed based on LLM output.

Conflicts and low-confidence results should be flagged for review.

## CLI and operational requirements

Expensive commands should support appropriate combinations of:

- --dry-run
- --limit
- --resume
- --status
- --division
- --pipeline-status
- --folder
- --since
- --only-unresolved
- --only-changed
- --max-concurrency

Do not add flags merely for appearance; add them where they provide genuine
control or recovery value.

Commands should provide concise summaries including:

- records considered
- records processed
- records skipped
- records unchanged
- successes
- transient failures
- permanent failures
- unresolved records
- Graph calls
- retries
- throttling events
- elapsed time

Failures must include enough identifying context to diagnose and rerun the
affected records without flooding logs.

## Testing and completion

After changing code:

1. Run the most relevant unit tests, integration tests, linting, type checks,
   migration checks, and smoke tests available.
2. Report the actual commands and results.
3. Do not claim success merely because the code imports or compiles.
4. Verify the intended behavior where practical.
5. State unresolved risks and assumptions.
6. Provide the next commands as one copy-pasteable multiline shell block when
   they belong together.

Prefer commands that can be run from the repository root and return enough
output to diagnose the result in a single response.

## Security

Never place secrets, access tokens, passwords, database credentials, connection
strings, webhook URLs, or client secrets in:

- source code
- committed configuration
- test fixtures
- logs
- screenshots
- documentation examples
- Codex responses

Use environment variables and sanitized placeholders.

The `.env` file must remain excluded from Git. Do not print its values.

---

## Estimator, relationship mining, and bid takeoff objectives

This repository now also supports Spray-Tec estimating workflows, including:

1. field-notes-to-estimate assistance
2. historical estimate line item extraction
3. relationship mining from parsed estimate data
4. pricing catalog integration
5. Excel estimate workbook generation
6. AI-assisted bid takeoff evaluation

The estimating system should be built as a deterministic, auditable rules engine with AI-assisted interpretation layered around it.

Do not ask an LLM to directly invent estimate quantities, prices, labor, markup, or final totals.

Preferred estimator flow:

field notes / bid files / historical estimates
→ structured scope and quantities
→ deterministic pricing and calculation
→ historical relationship evidence
→ estimator review flags
→ workbook/dashboard output
→ estimator feedback
→ improved rules and tests

Use AI for:

- interpreting messy field notes
- classifying ambiguous scope
- summarizing supporting evidence
- explaining estimate assumptions
- identifying missing information
- comparing estimator corrections to system assumptions

Do not use AI as the source of truth for:

- current material pricing
- final estimate totals
- invoice values
- labor math
- warranty/wet-mil calculations
- database identifiers
- authoritative extracted values

## Estimate template handling

Estimate templates vary by division and must not share row maps unless explicitly compatible.

Known template types:

- roofing
- insulation
- unknown

Preserve `template_type` through:

- document extraction
- estimate_template_rows
- line item classification
- relationship mining
- estimator data loading
- workbook generation
- dashboard displays

Do not apply roofing row mappings to insulation templates.

Do not mix roofing labor buckets and insulation labor buckets in relationship mining unless the relationship explicitly normalizes across template types.

When adding a new estimate template:

1. add template detection
2. add a separate row map
3. preserve formulas where possible
4. add parser tests using a fixture
5. add workbook writer tests if generation is supported
6. include template_type in downstream outputs

## Estimator calculation principles

The estimator should be explainable and reproducible.

For every priced estimate row, preserve or generate:

- item/package
- quantity
- unit
- unit price
- estimated cost
- pricing source
- quantity source
- historical evidence count when applicable
- review flag
- notes explaining the assumption

Current pricing catalog is the source of truth for unit pricing.

Historical extracted estimate rows are the source of truth for quantity ratios, labor rates, and package relationships.

Use deterministic formulas for:

- square footage
- deductions
- coating gallons
- wet mil assumptions
- waste factors
- travel calculations
- labor-hour math
- cost rollups

Important pricing rule:

`needs_review = true` does not mean exclude from price.
Only rows with `estimated_cost IS NULL` should be excluded from numeric totals.

Review flags should identify uncertainty without silently dropping cost-bearing assumptions.

## Material calibration

Secondary materials such as primer, seam treatment, fastener treatment, caulk/detail materials, foam, membrane, thermal barrier, and accessories should not remain blank allowances when enough information exists to estimate them.

Preferred material estimate priority:

1. exact user-provided quantity
2. historical quantity ratio from similar jobs
3. historical cost-per-sqft ratio from similar jobs
4. deterministic fallback rule
5. unpriced manual review allowance

Use current pricing catalog for unit cost whenever possible.

Use relationship tables and job_package_summary for historical quantity/cost behavior.

Do not treat cost allowances as physical quantities.

Material calibration should distinguish:

- physical_quantity
- cost_allowance
- labor_budget
- derived_ratio
- unknown

## Labor calibration

Labor should remain package-specific wherever possible.

Do not collapse specific labor tasks into generic `labor` if a more specific package is available.

Examples of roofing labor packages:

- labor_prep
- labor_prime
- labor_seam_sealer
- labor_base
- labor_top_coat
- labor_caulk
- labor_details
- labor_cleanup
- labor_loading
- labor_traveling
- infrared_scan
- labor_top_coat_granules

Examples of insulation labor packages:

- labor_set_up
- labor_mask
- labor_prime
- labor_membrane
- labor_foam
- labor_dc_315
- labor_misc
- labor_clean_up
- labor_loading
- labor_traveling
- meals_lodging

Labor relationship outputs should preserve:

- total_hours
- total_days
- crew_size
- hours_per_sqft
- cost_per_sqft
- evidence_count
- supporting job IDs

Missing or malformed historical labor rows should create review flags or diagnostics, not crash the estimator.

Never convert possibly missing numeric values directly with `int(...)` or `float(...)`.
Use safe numeric helpers that treat None, NaN, empty strings, and infinity as missing.

## Relationship mining architecture

Relationship mining should read from database-backed extracted tables, not raw source files.

Preferred inputs:

- estimate_template_rows
- estimate_line_item_classifications
- pricing_catalog
- jobs / estimate_jobs
- documents / source_documents where traceability is needed

Preferred intermediate/output tables:

- source_documents
- estimate_line_items_raw
- estimate_line_items_normalized
- estimate_jobs
- job_package_summary
- relationship_material_qty_ratios
- relationship_labor_rates
- relationship_package_cooccurrence
- relationship_warranty_coating
- relationship_anomalies
- estimator_rule_suggestions

job_package_summary should be the main input for relationship profiling.

It should preserve:

- job_id
- source_year
- division
- pipeline_status
- status
- template_type
- project_type
- substrate
- area_sqft
- warranty_years
- wet_mils
- coating_type
- roof_condition
- access_complexity
- package
- total_quantity
- unit
- total_cost
- total_hours
- qty_per_sqft
- cost_per_sqft
- hours_per_sqft
- has_physical_quantity
- has_allowance
- review_required
- evidence_line_item_ids

Relationship mining must preserve traceability back to source documents and raw line items.

Missing optional context columns such as warranty_years or wet_mils should not crash profiling.
Instead, generate diagnostics that explain what is missing.

Required diagnostics where practical:

- relationship_input_diagnostics.csv
- package_normalization_diagnostics.csv
- missing_job_context.csv
- labor_rate_diagnostics.csv

## Bid takeoff principles

AI-assisted bid takeoff should be evaluated against completed takeoff examples.

Do not optimize only for visually plausible output.

For bid takeoff, preserve:

- source file
- sheet/page
- detected scope region
- candidate quantity
- confidence
- reason
- link to completed takeoff prior when available
- human review status

Preferred bid takeoff flow:

bid files / plan set
→ document indexing
→ sheet detection
→ scope and quantity candidates
→ estimator review
→ comparison to completed takeoff
→ evaluation metrics
→ improved selector/extractor rules

Codex should not make broad bid takeoff changes without an evaluation fixture or expected output.

## Evaluation-driven development

When improving estimator, relationship mining, dashboarding, or bid takeoff behavior, prefer adding evals before broad refactors.

Recommended eval structure:

- evals/estimator/field_notes_cases.json
- evals/estimator/run_estimator_eval.py
- evals/relationship_mining/run_relationship_eval.py
- evals/template_parsing/run_template_eval.py
- evals/bid_takeoff/run_takeoff_eval.py

Each eval should:

1. use representative fixtures or database-backed test data
2. assert expected outputs
3. print a concise pass/fail report
4. exit nonzero on failed required checks
5. avoid secrets in fixtures or logs

For field notes estimator evals, check:

- estimated sqft
- gross/deduction/net area
- project type
- substrate
- coating or foam system
- warranty years
- required material packages
- excluded material/labor packages
- travel reasonableness
- review flags

For relationship mining evals, check:

- job_package_summary has specific packages, not only generic labor/materials
- area_sqft and hours_per_sqft are populated when possible
- material ratios exist for known packages when evidence exists
- labor rates exist when labor hours and area exist
- diagnostics exist and are useful

For bid takeoff evals, check:

- expected sheets found
- expected scope categories found
- quantities compared to completed takeoff within tolerance
- uncertain cases flagged instead of guessed

## Dashboard and operations

The dashboard should support both business use and operational support.

Add or preserve an Admin / Health view where practical, showing:

- database connection status
- jobs count
- documents count
- extraction status counts
- document_content count
- estimate_template_rows count
- template_type counts
- pricing_catalog current rows
- line item classification counts
- job_package_summary count
- relationship table counts
- recent failed documents or parser errors
- last scan/extraction/parser/profiler timestamps

Dashboard pages must fail gracefully when optional tables are missing.

Do not expose secrets or raw connection strings in the dashboard.

## Support and production handoff

Assume an outside IT provider may host infrastructure but application-level support remains with this project.

Build features that help diagnose issues quickly:

- clear error messages
- run summaries
- row counts
- diagnostics files
- rerunnable commands
- health checks
- failed-record IDs
- parser version fields
- timestamps

Application-level issues include:

- scanner failures
- extraction failures
- parser bugs
- estimator logic issues
- dashboard errors
- relationship mining problems
- workbook generation problems
- integration changes

Infrastructure-level issues include:

- server availability
- network access
- Microsoft tenant/app permissions
- backups
- environment variables
- SSL/domains

Keep this distinction clear in documentation and runbooks.