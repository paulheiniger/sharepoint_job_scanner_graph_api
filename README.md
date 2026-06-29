# SharePoint Job Folder Scanner

A Python starter project for scanning roofing job folders directly from SharePoint using Microsoft Graph, extracting estimate/invoice/status metadata, and producing a dashboard-ready job index.

The local scanner still works, but the primary path is now:

```text
Microsoft Graph → SharePoint job folder cache → extractor → job index → SharePoint List / dashboard
```

## What it extracts

For each job folder, the scanner detects:

- estimate workbook
- invoice PDF
- signed contract
- warranty file
- proposal / job spec
- aerial / notes documents
- job-site photos and duplicate photo count
- crew schedule signals such as crew leader, estimated start, duration, end date, readiness, blocking issue, and source file

It extracts structured estimate fields including customer/job metadata, labor/material totals, final price, invoice amount, and inferred job status.

## Install

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Configure Microsoft Graph

Copy the example env file:

```bash
cp .env.example .env
```

Fill these values from an Azure app registration:

```bash
MS_TENANT_ID=...
MS_CLIENT_ID=...
MS_CLIENT_SECRET=...
ZAPIER_WEBHOOK_URL=...
```

See `docs/AZURE_GRAPH_SETUP.md` for the Azure setup steps.

For direct SharePoint List synchronization, the Azure app also needs write access to the target site/list, typically one of:

- `Sites.ReadWrite.All` application permission, admin consented
- or a narrower Microsoft Graph Sites.Selected setup with write permission granted to the target site

The sync command never logs the client secret, access token, or authorization headers.

## Run direct SharePoint sync + scan

```bash
python -m jobscan.sharepoint_sync \
  --sharepoint-url "https://yourtenant.sharepoint.com/sites/Operations" \
  --library "Documents" \
  --folder "Estimates" \
  --out output/job_index.csv \
  --json output/job_index.json \
  --xlsx output/job_index.xlsx
```

The sync step downloads only useful files into `.cache/sharepoint/`:

- Excel estimate files
- PDFs / Word docs

For speed, image downloads are skipped by default. The sync still writes `.image_manifest.json` files in cached folders, so `photo_count` can include SharePoint photos without downloading hundreds of JPG/PNG/HEIC files. Skipped images are normal scan metadata, not warnings: records use `image_files_cached`, `skipped_image_count`, and a blank/null `duplicate_photo_count` when duplicate detection was not run.

Use `--include-images` only when you need duplicate image detection or image-byte analysis:

```bash
python -m jobscan.sharepoint_sync \
  --sharepoint-url "https://yourtenant.sharepoint.com/sites/Operations" \
  --library "Documents" \
  --folder "Estimates" \
  --include-images
```

It uses a manifest with eTags so unchanged downloaded files are skipped on later runs.

## Run batch SharePoint sync + scan

Use batch scanning when jobs are spread across multiple division/status folders. Configure roots in `config/sharepoint_scan_roots.yaml`:

```yaml
sharepoint:
  site_url: "https://yourtenant.sharepoint.com/sites/Operations"
  library: "Documents"

scan_roots:
  - folder: "2026 Roofing/COMPLETED"
    division: "Roofing"
    pipeline_status: "Completed"
```

Then run:

```bash
python -m jobscan.batch_sharepoint_sync \
  --config config/sharepoint_scan_roots.yaml \
  --out output/job_index.csv \
  --json output/job_index.json \
  --xlsx output/job_index.xlsx \
  --crew-schedule-out output/crew_schedule_candidates.csv \
  --crew-schedule-json output/crew_schedule_candidates.json \
  --estimate-summary-out output/estimate_summary.csv \
  --estimate-summary-json output/estimate_summary.json \
  --estimate-line-items-out output/estimate_line_items.csv \
  --estimate-line-items-json output/estimate_line_items.json \
  --job-tracking-summary-out output/job_tracking_summary.csv \
  --job-tracking-summary-json output/job_tracking_summary.json \
  --job-tracking-daily-out output/job_tracking_daily_entries.csv \
  --job-tracking-daily-json output/job_tracking_daily_entries.json \
  --force
```

The batch scanner syncs each root, scans its local cache, combines all records into one index, and annotates each job with `division`, `pipeline_status`, `scan_root`, and `source_year`. If one root fails, the remaining roots still run; failures are written to `output/batch_scan_summary.json` under `scan_errors`. Missing signed contracts remain visible through `has_signed_contract`; for Contracted roots, the summary also includes `contracted_without_signed_contract_count` as a non-warning metric.

The batch scanner also writes crew schedule candidate files:

- `output/crew_schedule_candidates.csv`
- `output/crew_schedule_candidates.json`

These files are dashboard-friendly assignment candidates for production scheduling. The scanner uses estimate labor data to populate `estimated_duration_days`, `estimated_labor_hours`, and `estimated_crew_size`, then flags jobs as `Needs Assignment`, `Needs Start Date`, `Scheduled`, `Complete`, or `Not Ready`. `crew_leader`, `assigned_crew_leader`, and `estimated_start_date` stay blank unless they are manually provided later. `suggested_crew_type` is blank by default and `suggested_crew_reason` is `manual_needed` until a real recommendation rule is added.

For estimating analytics and future AI estimate generation, the batch scanner also writes detailed estimate datasets:

- `output/estimate_summary.csv`
- `output/estimate_summary.json`
- `output/estimate_line_items.csv`
- `output/estimate_line_items.json`

The summary dataset is one row per estimate workbook. The line-item dataset preserves section, item, quantity/cost fields where available, labor task days/crew/hours, and `source_sheet` / `source_row` references for parser improvements.

If a job folder contains multiple estimate workbooks, the Job Index remains one row per job and selects one `primary_estimate_file` for high-level fields. Other workbooks are retained in `supporting_estimate_files` and emitted as supporting rows in `estimate_summary`.

When a job folder contains a Job Tracking Form workbook or a job tracking worksheet inside another workbook, the batch scanner writes:

- `output/job_tracking_summary.csv`
- `output/job_tracking_summary.json`
- `output/job_tracking_daily_entries.csv`
- `output/job_tracking_daily_entries.json`

These outputs capture actual daily production entries, actual-versus-estimated summary totals, and source row references. The Job Index also includes high-level tracking fields such as `has_job_tracking_form`, `actual_labor_hours`, dates worked, and `labor_hours_variance`.

## Run local/exported folder scan

Useful for testing parser changes without hitting Graph:

```bash
python -m jobscan.cli examples/sample_export \
  --out output/job_index.csv \
  --json output/job_index.json \
  --xlsx output/job_index.xlsx
```

## Scan office/admin/sales timesheets

The office timesheet scanner reads exported SharePoint folders or ZIP files containing daily Excel timesheets for office, admin, estimating, and sales activity. It summarizes employee time, codes, project/customer touches, HubSpot notes, and warnings.

This is not the field crew labor scanner and should not be used as full job labor costing or field labor profitability reporting. Field worker/job-site labor timesheets are separate and can be handled later.

Run against a local export:

```bash
python -m jobscan.office_timesheet_sync \
  --root "Timesheets" \
  --out output/office_timesheet_entries.csv \
  --json output/office_timesheet_entries.json
```

By default this writes:

- `output/office_timesheet_entries.csv`
- `output/office_timesheet_employee_daily_summary.csv`
- `output/office_timesheet_code_summary.csv`
- `output/office_timesheet_project_touch_summary.csv`
- `output/office_timesheet_warnings.csv`

Filter by date, employee, code, or project:

```bash
python -m jobscan.office_timesheet_sync \
  --root "Timesheets" \
  --start-date 2026-06-01 \
  --end-date 2026-06-30 \
  --employee Aaron \
  --code EST \
  --project "Smith"
```

Preview counts without writing files:

```bash
python -m jobscan.office_timesheet_sync --root "Timesheets" --dry-run
```

The same scanner can download `.xlsx` timesheets through Microsoft Graph before parsing:

```bash
python -m jobscan.office_timesheet_sync \
  --sharepoint-url "https://yourtenant.sharepoint.com/sites/Operations" \
  --library "Documents" \
  --folder "Timesheets" \
  --out output/office_timesheet_entries.csv \
  --json output/office_timesheet_entries.json
```

## Load outputs into Postgres

The scanner still writes CSV/JSON/XLSX files as the source of truth for exports. To load the JSON outputs into the local Postgres database, set `DATABASE_URL` in `.env`:

```bash
DATABASE_URL=postgresql+psycopg2://spraytec:spraytec_dev_password@localhost:5432/spraytec_ops
```

Load one dataset:

```bash
python -m jobscan.db_loader --jobs output/job_index.json
python -m jobscan.db_loader --estimates output/estimate_summary.json
python -m jobscan.db_loader --line-items output/estimate_line_items.json
python -m jobscan.db_loader --crew-schedule output/crew_schedule_candidates.json
python -m jobscan.db_loader --documents output/document_index.json
```

Load all available default JSON outputs, skipping missing files:

```bash
python -m jobscan.db_loader --all
```

The loader upserts into existing tables, stores each source record in the table's `raw` JSONB column, and only writes columns that exist in the current SQL schema.

For deployed Streamlit, prefer Neon’s pooled connection string for `DATABASE_URL` so the web app can recover cleanly from idle or stale database connections. CLI migrations, schema changes, and bulk/admin loads can continue to use the direct Neon connection string when appropriate.

## Microsoft Graph delta synchronization

The SharePoint sync CLI supports Microsoft Graph delta synchronization for metadata-first file discovery and change detection. The first run performs a full drive delta enumeration and stores the final Graph delta state in Postgres. Later runs use the saved delta state and process only additions, updates, moves, renames, and deletions returned by Graph.

Apply the idempotent migration:

```bash
psql "$DATABASE_URL" -f db/add_sharepoint_delta_tables.sql
```

Check saved delta state:

```bash
python -m jobscan.sharepoint_sync \
  --delta-status \
  --database-url "$NEON_DATABASE_URL"
```

Initial sync, and also the normal incremental command after a delta state exists:

```bash
python -m jobscan.sharepoint_sync \
  --delta \
  --site-url "https://aro365531128.sharepoint.com/sites/Data" \
  --library "Documents" \
  --config config/sharepoint_scan_roots.yaml \
  --database-url "$NEON_DATABASE_URL"
```

Force a fresh full delta enumeration without deleting existing inventory first:

```bash
python -m jobscan.sharepoint_sync \
  --delta \
  --full-refresh \
  --site-url "https://aro365531128.sharepoint.com/sites/Data" \
  --library "Documents" \
  --config config/sharepoint_scan_roots.yaml \
  --database-url "$NEON_DATABASE_URL"
```

Delta sync stores:

- `sharepoint_delta_state`: one row per drive, including the saved Graph delta state and sync status.
- `sharepoint_drive_items`: the persistent current inventory of DriveItems keyed by `(drive_id, drive_item_id)`.

Operational behavior:

- The previous valid delta state is preserved until a complete run succeeds.
- If Graph returns `410 Gone`, the tool records the event and performs a fresh full delta enumeration without truncating the existing inventory first.
- Deleted files are soft-deleted in `sharepoint_drive_items` and matching document rows are marked `extraction_status = 'deleted'`.
- New or changed files reconcile `documents.drive_id` and `documents.drive_item_id`, clear extraction errors, and mark matching documents pending for extraction.
- The sync does not download document contents. Extraction still happens through `jobscan.document_extraction`.
- Configured scan roots are used as local relevance filters after drive inventory updates; delta is not run separately for every job folder.
- Output never prints the saved delta state value.

## Delta-driven incremental processing

The normal scheduled path is `jobscan.incremental_scan`. It runs Graph delta, routes changed DriveItems to the smallest affected processing unit, writes changed-only output files, and preserves unchanged records in the persistent outputs and database.

Normal daily command:

```bash
python -m jobscan.incremental_scan \
  --delta \
  --site-url "https://aro365531128.sharepoint.com/sites/Data" \
  --library "Documents" \
  --config config/sharepoint_scan_roots.yaml \
  --database-url "$NEON_DATABASE_URL"
```

Metadata-only run, useful for validating delta routing without reparsing cached files:

```bash
python -m jobscan.incremental_scan \
  --delta \
  --metadata-only \
  --site-url "https://aro365531128.sharepoint.com/sites/Data" \
  --library "Documents" \
  --config config/sharepoint_scan_roots.yaml \
  --database-url "$NEON_DATABASE_URL"
```

Resume a failed processing run after correcting the underlying parser/cache issue:

```bash
python -m jobscan.incremental_scan \
  --resume \
  --run-id "<RUN_ID>" \
  --database-url "$NEON_DATABASE_URL"
```

Changed-only Postgres loads:

```bash
python -m jobscan.db_loader --jobs-changed output/changed_jobs.json
python -m jobscan.db_loader --estimates-changed output/changed_estimates.json
python -m jobscan.db_loader --line-items-changed output/changed_estimate_line_items.json
python -m jobscan.db_loader --job-tracking-summary-changed output/changed_tracking_summary.json
python -m jobscan.db_loader --job-tracking-daily-changed output/changed_tracking_daily_entries.json
python -m jobscan.db_loader --timesheets-changed output/changed_timesheets.json
python -m jobscan.db_loader --documents-changed output/changed_documents.json
```

Changed-only SharePoint Job Index List sync:

```bash
python -m jobscan.sharepoint_list_sync \
  --input output/changed_jobs.json \
  --continue-on-error \
  --url-fields-as-text
```

Recovery and validation commands remain explicit administrative operations:

```bash
python -m jobscan.incremental_scan --full-refresh-metadata --delta --site-url "https://aro365531128.sharepoint.com/sites/Data" --library "Documents" --config config/sharepoint_scan_roots.yaml --database-url "$NEON_DATABASE_URL"
python -m jobscan.incremental_scan --rebuild-all-jobs
python -m jobscan.incremental_scan --rebuild-all-estimates
python -m jobscan.incremental_scan --rebuild-all-tracking
python -m jobscan.incremental_scan --rebuild-all-timesheets
python -m jobscan.incremental_scan --rebuild-all-documents
```

Incremental processing rules:

- Delta changes are used only to identify affected jobs, workbooks, and documents.
- Parsers must be rerun against the complete current state of the affected job folder or source workbook.
- Unchanged jobs, estimates, tracking files, timesheets, and documents are skipped.
- New or changed documents are queued by setting `extraction_status = pending`; document content extraction is run separately.
- Deleted documents are marked inactive/deleted while historical extracted content is retained.
- If an affected folder or workbook is not available in the local cache, the run records a retryable failure instead of falling back to a broad SharePoint traversal.
- Office-timesheet SharePoint routing activates when those workbook roots are included in scan-root configuration; otherwise local-only full rebuild remains an explicit operation.
- Scan-root, classification-rule, or parser-version changes can make previously unchanged files newly relevant. In that case run a bounded validation or explicit rebuild command rather than relying on delta changes alone.

## Build and load the document index

The normalized `documents` table stores one row per discovered SharePoint file and links it to `jobs.job_id`. It is populated from existing `.cache/sharepoint/**/.jobscan_manifest.json` files plus `output/job_index.json`; it does not re-scan SharePoint or download document contents.

Apply the idempotent migration:

```bash
psql "$DATABASE_URL" -f db/add_documents_table.sql
```

Build the reusable manifest:

```bash
python -m jobscan.document_index \
  --build \
  --job-index output/job_index.json \
  --cache-root .cache/sharepoint \
  --out output/document_index.json
```

Load it into Postgres/Neon:

```bash
python -m jobscan.db_loader --documents output/document_index.json
```

Verify rows by type:

```sql
SELECT document_type, COUNT(*)
FROM documents
GROUP BY document_type
ORDER BY COUNT(*) DESC;
```

CLI examples:

```bash
python -m jobscan.document_index --job-id "<JOB_ID>" --database-url "$DATABASE_URL" --debug
python -m jobscan.job_search --query "what files do we have for Canadian Solar" --database-url "$DATABASE_URL"
```

## Extract indexed document content

The document content extractor adds source-aware text chunks for supported cached files:

- PDF text pages via `pypdf`
- DOCX headings, paragraphs, and table rows
- XLSX/XLSM visible worksheet rows with sheet, row, and cell-range references
- TXT/CSV whole-file text

Apply the idempotent content migration:

```bash
psql "$DATABASE_URL" -f db/add_document_content_tables.sql
```

Extract one document:

```bash
python -m jobscan.document_extraction \
  --document-id "<DOCUMENT_ID>" \
  --database-url "$DATABASE_URL"
```

Extract a bounded batch of pending documents:

```bash
python -m jobscan.document_extraction \
  --pending \
  --limit 10 \
  --database-url "$DATABASE_URL"
```

Extract one job's indexed documents:

```bash
python -m jobscan.document_extraction \
  --job-id "<JOB_ID>" \
  --database-url "$DATABASE_URL"
```

Check extraction status:

```bash
python -m jobscan.document_extraction --status --database-url "$DATABASE_URL"
```

Check whether indexed documents have the Graph identifiers needed for downloads:

```bash
python -m jobscan.document_extraction --identifier-status --database-url "$DATABASE_URL"
```

Backfill download identifiers from existing SharePoint scanner manifests without rescanning or downloading files:

```bash
python -m jobscan.document_extraction \
  --backfill-metadata \
  --cache-root .cache/sharepoint \
  --limit 1000 \
  --database-url "$DATABASE_URL"
```

If cached manifests do not have enough metadata, resolve missing identifiers from Graph by site/library/path without downloading content:

```bash
python -m jobscan.document_extraction \
  --resolve-metadata \
  --site-url "https://YOURTENANT.sharepoint.com/sites/YOURSITE" \
  --library "Documents" \
  --root-folder "OPTIONAL/LIBRARY/ROOT" \
  --limit 25 \
  --database-url "$DATABASE_URL"
```

Rebuild and load document metadata after scanner manifests include `drive_id` and `drive_item_id`:

```bash
python -m jobscan.document_index \
  --build \
  --job-index output/job_index.json \
  --cache-root .cache/sharepoint \
  --out output/document_index.json

python -m jobscan.db_loader --documents output/document_index.json
```

Retry one PDF/XLSX document or a limited pending batch after identifiers are present:

```bash
python -m jobscan.document_extraction \
  --document-id "<DOCUMENT_ID>" \
  --force \
  --database-url "$DATABASE_URL"

python -m jobscan.document_extraction \
  --document-id "<XLSX_ESTIMATE_DOCUMENT_ID>" \
  --force \
  --database-url "$DATABASE_URL"

python -m jobscan.document_extraction \
  --pending \
  --document-type estimate \
  --limit 10 \
  --database-url "$DATABASE_URL"
```

By default, extraction reuses existing files under `.cache/sharepoint` and records failures on the document row instead of stopping the whole batch. Successful content is replaced transactionally per document; prior extracted content is left in place if a later extraction fails. Re-run a previously completed or failed unchanged file with `--force`.

Limitations: image-only PDFs are marked `requires_ocr`; this patch does not perform OCR, embeddings, vector search, or LLM document answers. If the local SharePoint cache does not contain a file and the document row lacks download identifiers, extraction records a failure for that document.

For faster conversational job lookup, apply the optional search indexes after the base schema:

```bash
psql "$DATABASE_URL" -f db/job_search.sql
```

Smoke-test the job/document finder from the command line:

```bash
python -m jobscan.job_search \
  --query "show me the Canadian Solar estimate" \
  --database-url "$DATABASE_URL"
```

## Generate Zapier handoff payloads

```bash
python -m jobscan.zapier_payloads output/job_index.json \
  --digest output/teams_digest.md \
  --payload output/zapier_payload.json
```

Use `teams_digest.md` for a Teams post. Use `zapier_payload.json` for webhook or MCP-driven QuickBooks / CompanyCam / Teams actions.

## Send records to Zapier

Create a Zapier Catch Hook trigger, then set its URL in `.env`:

```bash
ZAPIER_WEBHOOK_URL=https://hooks.zapier.com/hooks/catch/...
```

Send all job records from the default index:

```bash
python -m jobscan.zapier_sender
```

Send only records with warnings:

```bash
python -m jobscan.zapier_sender output/job_index.json --only-warnings
```

`--only-warnings` is intended for actionable business/data issues, such as failed estimate parsing, missing estimate workbooks, invoice mismatches, completed jobs missing invoices or final prices, or roof jobs with zero labor. Normal skipped-image metadata and missing signed contracts in Contracted folders do not trigger warning-only sends.

Preview payloads without sending:

```bash
python -m jobscan.zapier_sender output/job_index.json --status Completed --limit 5 --dry-run
```

Each webhook payload includes the job record plus `source=sharepoint_job_scanner` and a UTC `sent_at` timestamp. Zapier can route those records to Teams, QuickBooks, CompanyCam, Outlook, or other follow-up actions.

## Send Job Index To SharePoint List Directly With Graph

The preferred Job Index list path is now direct Microsoft Graph sync. It discovers the SharePoint site, list, and actual internal column names automatically, then upserts one item per unique `job_id`.

Default target:

```bash
SHAREPOINT_JOB_INDEX_SITE_URL=https://aro365531128.sharepoint.com/sites/Data
SHAREPOINT_JOB_INDEX_LIST_NAME="Job Index"
```

Optional ID overrides skip discovery:

```bash
SHAREPOINT_JOB_INDEX_SITE_ID=...
SHAREPOINT_JOB_INDEX_LIST_ID=...
```

Inspect SharePoint columns without syncing rows:

```bash
python -m jobscan.sharepoint_list_sync \
  --site-url "https://aro365531128.sharepoint.com/sites/Data" \
  --list-name "Job Index" \
  --print-columns \
  --columns-only
```

Dry-run a full sync plan without writing:

```bash
python -m jobscan.sharepoint_list_sync \
  --input output/job_index.json \
  --site-url "https://aro365531128.sharepoint.com/sites/Data" \
  --list-name "Job Index" \
  --dry-run \
  --print-columns
```

Run a limited write test:

```bash
python -m jobscan.sharepoint_list_sync \
  --input output/job_index.json \
  --site-url "https://aro365531128.sharepoint.com/sites/Data" \
  --list-name "Job Index" \
  --limit 5
```

Run the full sync:

```bash
python -m jobscan.sharepoint_list_sync \
  --input output/job_index.json \
  --site-url "https://aro365531128.sharepoint.com/sites/Data" \
  --list-name "Job Index"
```

The command writes:

- `output/job_index_sharepoint_columns.json`
- `output/sharepoint_job_index_sync_report.json`

Recommended manual SharePoint column types:

- URL/link fields such as `folder_url`, `primary_doc_link`, `proposal_url`, `estimate_url`, `contract_url`, `invoice_url`, `job_tracking_url`, `warranty_url`, and `aerial_url`: **Single line of text**. This is the easiest and most reliable Graph sync target.
- `important_doc_links_json`: **Multiple lines of text**.
- `document_link_count`: **Number**.
- Money fields such as `final_price`, `invoice_amount`, `material_subtotal`, `labor_subtotal`, and `total_job_cost`: **Currency**.
- Yes/no fields such as `has_invoice`, `has_signed_contract`, and `has_aerial`: **Yes/No**.

The scanner now preserves Graph `webUrl` values for job folders and important documents when available. The Job Index includes direct link fields such as `folder_url`, `proposal_url`, `estimate_url`, `invoice_url`, and `primary_doc_link`. Verify document links by opening a few SharePoint List rows and confirming `primary_doc_link` opens the best proposal/estimate, falling back to contract, tracking form, or job folder.

`important_doc_links_json` is not written by default because historical JSON values can exceed SharePoint's single-line text limit. Add `--include-important-doc-links-json` only after that column has been changed to Multiple lines of text. If it is still single-line text and a value exceeds 255 characters, the sync omits that field for the row and continues.

The batch scanner can run the direct list sync after protected outputs are successfully written:

```bash
python -m jobscan.batch_sharepoint_sync \
  --config config/sharepoint_scan_roots.yaml \
  --sync-job-index-list
```

If shrink protection or partial-root protection blocks replacing `output/job_index.json`, the direct SharePoint List sync is skipped.

## Send Job Index To SharePoint List Through Zapier

The Zapier sender is still available as a fallback/legacy path. Direct Graph sync replaces the Zapier Job Index upsert for normal use.

Create a Zapier Catch Hook that creates or updates rows in the SharePoint List named `Job Index`, then set its URL in `.env`:

```bash
ZAPIER_JOB_INDEX_WEBHOOK_URL=https://hooks.zapier.com/hooks/catch/...
```

Preview normalized SharePoint List upsert payloads:

```bash
python -m jobscan.zapier_job_index_sender output/job_index.json --dry-run
```

Send all job index records:

```bash
python -m jobscan.zapier_job_index_sender output/job_index.json
```

Send a filtered subset:

```bash
python -m jobscan.zapier_job_index_sender output/job_index.json --division Roofing --status Invoiced --limit 25
```

To avoid resending unchanged rows after a successful run, add `--only-changed`. The sender stores local hashes in `.cache/zapier_job_index_sender_state.json`.

## Send Daily Summary To Zapier

Create a separate Zapier Catch Hook for the daily scan summary, then set its URL in `.env`:

```bash
ZAPIER_DAILY_SUMMARY_WEBHOOK_URL=https://hooks.zapier.com/hooks/catch/...
```

Preview the one-message summary payload:

```bash
python -m jobscan.zapier_summary_sender output/job_index.json --dry-run
```

Send the live summary:

```bash
python -m jobscan.zapier_summary_sender output/job_index.json
```

This module sends one aggregate payload only. It includes total jobs, quoted estimated value totals, division and pipeline value breakdowns, warning counts, completed-folder issue counts, aerial/photo totals, top warning jobs, and top highest-value jobs. Estimated value uses `final_price`, then `worksheet_price`, then `total_job_cost` as a fallback. It also includes Teams-ready newline-separated text fields: `division_summary_text`, `pipeline_summary_text`, `pipeline_value_summary_text`, `warning_jobs_text`, and `top_value_jobs_text`.

For Microsoft Teams Zapier actions, set Message Text Format / Format to HTML and use `{{teams_message_html}}` as the message body. The payload also includes section-level HTML fields: `division_summary_html`, `pipeline_summary_html`, `pipeline_value_summary_html`, `warning_jobs_html`, and `top_value_jobs_html`.

## Pricing intake and master reconciliation

The pricing workflow normalizes vendor price files from a local folder into a review CSV before any master pricing update is made. It supports CSV/XLSX sheets and PDFs when a text extraction backend such as `pypdf` or `pdfplumber` is installed.

Source pricing files are immutable inputs. Do not edit or overwrite the original master CSV or vendor files in `data/pricing/`; generated files should be written under `output/pricing/`.

Extract source pricing rows:

```bash
python -m jobscan.pricing.extract_pricing \
  --input-dir data/pricing \
  --out output/pricing/pricing_source_items.csv
```

Compare extracted rows to the current master pricing sheet:

```bash
python -m jobscan.pricing.reconcile_pricing \
  --master "data/pricing/Pricing Sheet (MASTER 2026)(Sheet1).csv" \
  --source output/pricing/pricing_source_items.csv \
  --out output/pricing/pricing_master_update_review.csv
```

The reconcile command also writes `output/pricing/pricing_master_updated_draft.csv`. The original master file is never overwritten. Review `pricing_master_update_review.csv` first; rows are flagged as `new_item`, `price_changed`, `possible_duplicate`, `missing_from_new_source`, and/or `needs_review`. Low-confidence fuzzy matches are reported for human review and are not automatically merged into the draft.

### Load approved pricing into Postgres

Apply the pricing catalog migration:

```bash
psql "$DATABASE_URL" -f db/add_pricing_catalog_tables.sql
```

Load the current master pricing CSV:

```bash
python -m jobscan.pricing_loader \
  --input "data/pricing/Pricing Sheet (MASTER 2026)(Sheet1).csv" \
  --database-url "$DATABASE_URL" \
  --mark-current
```

You can also load a local folder of CSV/XLSX/PDF pricing files:

```bash
python -m jobscan.pricing_loader \
  --input-dir data/pricing \
  --database-url "$DATABASE_URL"
```

The loader upserts rows into `pricing_catalog`, preserves the source row in `raw_row_json`, and never deletes old pricing rows automatically. Machine-readable PDFs are parsed with text extraction only; no OCR is attempted. PDF-derived rows preserve `source_file` and `source_page`, and ambiguous rows or rows with unclear prices are flagged with `needs_review`. Reloading a PDF source automatically marks prior rows from that same PDF `inactive` before inserting the newly parsed rows, so stale parser output does not remain active. Use `--replace-source` only when you explicitly want the same source-scoped retirement behavior for non-PDF inputs.

PDF extraction is intentionally conservative: obvious page headers, table headers, footers, and general notes are skipped instead of being loaded as active catalog products. Section headers such as `Silicone Roofing Products`, `Primers`, and `Granules` are carried forward as categories for following product-price rows. Packaged liquid rows such as `5 Gal`, `54G`, `55 Gal`, and `250G` are treated as container/drum prices with calculated `price_per_gallon` when the package basis is clear. Product family is tracked in PDF row metadata, and package-size variants such as `2 Gal` versus `5 Gal` or pail versus drum are kept as separate catalog rows.

Clean up previously loaded noisy PDF rows without touching CSV-derived master rows:

```bash
python -m jobscan.pricing_loader \
  --cleanup-pdf-pricing \
  --database-url "$DATABASE_URL"
```

The cleanup marks obvious stale PDF header/note/package rows as `status='inactive'`, `is_current=false`, and `needs_review=true`; it does not delete pricing rows or modify source files.

Export the combined current pricing catalog from Postgres:

```bash
python -m jobscan.pricing_loader \
  --export-current \
  --out output/pricing/pricing_catalog_current.csv \
  --database-url "$DATABASE_URL"
```

The original master CSV is an input only, not the system of record. The normalized working catalog is `pricing_catalog` in Postgres, and combined master exports are generated from that table.

The Streamlit dashboard includes a **Pricing Catalog** page with search, vendor/category/status/current/review/date filters, health metrics, CSV download for filtered rows, and CSV download for the full current catalog. Both downloads are generated from Postgres query results, not from the source master CSV.

Current pricing rule for future estimator work: new estimates should use current approved rows from `pricing_catalog`. Historical estimate unit prices should be used for analysis and fallback only, and any fallback should be flagged for review.

## Estimator prototype

The Streamlit dashboard includes an **Estimating Assistant** page. This is an early planning aid, not a production estimating system. It uses deterministic rules plus database history when available, with local staging files created by the scanner as a development fallback:

- `output/job_index.json`
- `output/estimate_summary.json`
- `output/estimate_line_items.json`
- `output/job_tracking_summary.json`
- `output/job_tracking_daily_entries.json`
- `output/pricing/pricing_catalog_current_cleaned.csv`
- `output/pricing/pricing_catalog_current.csv`

The page accepts rough project notes and optional structured overrides, then opens an AI-assisted estimating workbench. The workbench is intended to create a high-quality first draft that an estimator can adjust quickly; it is not intended to produce a finished quote automatically.

The main workbench sections are:

- Parsed Scope: editable project type, substrate, gross/deduction/net square footage, warranty, coating type, roof condition, access, and penetrations.
- Materials: common roof coating packages are always visible with include checkboxes, historical quantity-per-square-foot defaults, editable quantity ratios, current pricing, evidence counts, confidence, and source.
- Labor: historical labor packages are always visible with include checkboxes, historical hours-per-1,000-square-foot defaults, editable hours, crew size, estimated cost, evidence counts, confidence, and source.
- Adders / Miscellaneous: travel, lift, generator, dumpster, hotel, inspection, infrared, mobilization, and miscellaneous adders are editable review items.

Historical defaults come from precomputed relationship tables such as `relationship_material_qty_ratios` and `relationship_labor_rates` when available. If evidence is insufficient, defaults stay at zero instead of fabricating values. Similar jobs are shown as sanity checks, not as the primary estimating engine. Workbook export uses the estimator-edited workbench values and writes edit history to `output/estimator_feedback/estimator_edit_history.csv`.

The decision-tree layer turns project conditions into scope and assumption changes before similar jobs are used for calibration. For example, rusted metal roofs can add fastener/seam treatment and primer review, warranty targets adjust wet-mil assumptions, poor condition raises prep review, and high access or many penetrations increase labor modifiers. Similar historical jobs remain supporting evidence, not the sole estimating logic.

Historical estimate line items are also classified into template-aware buckets that match the real workbook structure: `foam`, `coating`, `thinner`, `granules`, `primer`, `caulk_sealant`, `seams_misc`, `penetrations`, `hvac_units`, `drains`, `board_stock`, `fasteners`, `plates`, `dumpsters`, `lift`, `delivery_fee`, `fabric`, `edge_metal`, `gutter`, `downspouts`, `roof_hatch`, `scuppers`, `curbs`, `ladders`, `pitch_pockets`, `generator`, `freight`, `sales_inspection_trips`, `truck_expense`, labor task buckets, `meals_lodging`, `overhead_profit`, `other`, and `unknown`. Classification is transparent and rule-based using item names, descriptions, units, sections, and template source rows. Unknown or ambiguous rows, priced rows with unclear descriptions, and important material rows missing quantity/unit are flagged for review.

For deployed Streamlit, store these classifications in Postgres:

```bash
psql "$NEON_DATABASE_URL" -f db/add_estimate_line_item_classifications.sql

python -m jobscan.estimator.line_items \
  --classify-existing \
  --database-url "$NEON_DATABASE_URL"
```

For local development without a database, write a reviewable CSV from the staging file:

```bash
python -m jobscan.estimator.line_items \
  --classify-file output/estimate_line_items.json \
  --out output/estimate_line_item_classifications.csv
```

There are now two historical estimate row structures:

- `estimate_template_rows` is the newer `document_content`-based parser for standard Spray-Tec XLSX estimate workbook templates. It reads extracted rows such as `A116: Pwash/Prep | B116: 4 | C116: 5...`, maps known workbook row numbers to template buckets, preserves formulas separately, and extracts structured material, labor, travel, warranty, overhead, profit, and total fields.
- `estimate_line_item_classifications` is the older classifier built from the extracted `estimate_line_items` staging/table data. Keep it as a fallback for non-standard templates or older extraction outputs.

Rows `173`-`180` on the standard `Estimate` sheet are parsed as flexible manual estimate adders. Estimators may type custom descriptions into column `A` such as `Misc. Materials`, `Misc. Insurance`, `Lift Rental`, or `Misc. Equipment`; amounts are usually in column `F`. These rows are parsed separately from standard material/labor rows, formulas in column `F` are preserved, and empty placeholder rows such as `Additional Amount w/o Markup` are skipped to avoid review noise.

Apply and run the document-content template parser:

```bash
psql "$NEON_DATABASE_URL" -f db/add_estimate_template_rows.sql

python -m jobscan.estimator.template_rows \
  --parse-existing \
  --database-url "$NEON_DATABASE_URL"
```

Parse a single extracted estimate workbook document:

```bash
python -m jobscan.estimator.template_rows \
  --document-id "<DOCUMENT_ID>" \
  --database-url "$NEON_DATABASE_URL"
```

Verify parsed template rows:

```sql
SELECT COUNT(*) FROM estimate_template_rows;

SELECT template_bucket, line_item_kind, COUNT(*) AS rows
FROM estimate_template_rows
GROUP BY template_bucket, line_item_kind
ORDER BY rows DESC;

SELECT row_number, template_bucket, row_label, selected_item_name,
       days, crew_size, total_hours, unit_price, estimated_units,
       estimated_cost, needs_review
FROM estimate_template_rows
WHERE document_id = '<DOCUMENT_ID>'
ORDER BY row_number;
```

The prototype can also generate a filled draft copy of the Spray-Tec estimate workbook template at `data/estimate_samples/Estimate - Full Turnkey.xlsx`. The original template is never overwritten. The fill layer writes mapped input cells on the `Estimate` sheet, preserves formula cells, leaves `People` and `Materials` lookup tabs intact, and saves generated drafts under `output/estimates/` for estimator review.

Current approved pricing is preferred from the exported pricing catalog. Historical estimate line-item pricing is used only for calibration or fallback; any fallback is marked for pricing review. Missing square footage, missing location, ambiguous coating/foam assumptions, distant travel, and unavailable current pricing trigger human review warnings.

### Estimator relationship profiler

Use `relationship_profiler.py` to analyze extracted database line items and discover repeatable estimating relationships before turning them into estimator rules. The extracted database tables are the source of truth; CSV files are only output/review artifacts.

Optional schema bootstrap:

```bash
psql "$NEON_DATABASE_URL" -f db/relationship_mining_schema.sql
```

```bash
python relationship_profiler.py \
  --db-url "$NEON_DATABASE_URL" \
  --source-year 2026 \
  --division Roofing \
  --status Completed \
  --output-dir output/relationships \
  --min-job-count 3 \
  --write-review-sheet
```

The profiler reads `jobs`, `estimates`, and `estimate_line_items`, then materializes a traceable relationship-mining pipeline:

- `source_documents`: source file/sheet metadata and profiler parser version.
- `estimate_line_items_raw`: raw extracted line items with source document IDs and raw row JSON.
- `estimate_line_items_normalized`: cleaned rows with `line_type`, `package`, normalized item name, numeric quantity/unit/cost fields, `source_type`, `physical_quantity_valid`, `review_required`, confidence, and normalization reason.
- `estimate_jobs`: job-level extracted facts used for segmentation and filtering.
- `job_package_summary`: one row per job/package with quantity/cost per sqft, allowance/physical-quantity flags, review flags, and supporting normalized line item IDs.

The profiler writes reviewable training artifacts:

- `relationship_warranty_coating.csv` groups coating type and warranty by inferred wet mils, gallons per square foot, job count, and confidence.
- `relationship_package_cooccurrence.csv` shows which material work packages tend to appear together for each project type and substrate. `relationship_work_package_cooccurrence.csv` is also written as a compatibility alias.
- `relationship_material_qty_ratios.csv` reports median and percentile quantity/cost ratios by project type, substrate, coating, warranty, package, and unit. Cost allowance rows are not treated as physical quantity ratios unless they have a valid quantity and physical unit.
- `relationship_labor_rates.csv` reports package-level labor hours per 1,000 sqft and cost per sqft, using medians and percentiles.
- `relationship_anomalies.csv` flags suspicious relationships such as implausible primer pails per sqft, coating gallons outside wet-mil review ranges, allowance dollars used as quantities, primer labor without primer material, and fastener treatment on non-metal roofs.
- `estimator_rule_suggestions.json` summarizes candidate rules such as warranty-to-wet-mil assumptions, likely work packages by scope, primer/fastener triggers, and default labor production rates.
- `relationship_review_sheet.xlsx` is optional when `--write-review-sheet` is supplied.

Confidence is based on supporting job counts: `high` for 10 or more jobs, `medium` for 4-9 jobs, and `low` below 4 jobs. The output includes supporting job IDs for debug/review; treat these files as training evidence, not automatic production rules.

### Repair estimator data pipeline

VSimple repair exports are handled separately from full roof coating/restoration estimates. The sample workbook lives at `data/data.xlsx` and is treated as an immutable source input.

Normalize the VSimple export into repair-estimator tables:

```bash
python -m jobscan.repair_estimator.vsimple_loader \
  --input data/data.xlsx \
  --output-dir output/repair_estimator
```

Optional database bootstrap and load:

```bash
psql "$NEON_DATABASE_URL" -f db/repair_estimator_schema.sql

python -m jobscan.repair_estimator.vsimple_loader \
  --input data/data.xlsx \
  --output-dir output/repair_estimator \
  --db-url "$NEON_DATABASE_URL"
```

The loader writes:

- `repair_jobs.csv`: repair ID, customer/job name, status, repair type, roof type, address, URL, and source row metadata.
- `repair_material_usage.csv`: material package/name, quantity, unit, unit cost, total cost, source column, and raw `materials_used` text.
- `repair_labor_usage.csv`: aggregate labor plus technician-level hours/costs when present.
- `repair_scope_text.csv`: scope/work/special-notes text plus extracted work phrase patterns.
- `repair_outcomes.csv`: invoice/bill/gross-profit and cost outcome fields.

Profile normalized repair history:

```bash
python repair_profiler.py \
  --input-dir output/repair_estimator \
  --output-dir output/repair_estimator/profile \
  --min-job-count 3
```

Or parse and profile the workbook in one step:

```bash
python repair_profiler.py \
  --input data/data.xlsx \
  --output-dir output/repair_estimator/profile \
  --db-url "$NEON_DATABASE_URL" \
  --min-job-count 3
```

The repair profiler outputs:

- `repair_profile_summary.csv`: repair type/roof type groups with median labor hours, invoice amount, gross profit, common phrase patterns, and confidence.
- `repair_material_package_profile.csv`: common material packages by repair type and roof type.
- `repair_work_phrase_profile.csv`: phrase patterns such as leak, drain, seam, fabric reinforcement, fastener, flashing, and coating tied to labor and invoice medians.
- `repair_estimator_rule_suggestions.json`: candidate defaults for small repair field-notes estimating.

These repair artifacts are intended to calibrate small repair estimates from field notes. They should not be mixed directly into the full roof coating/restoration estimator without explicit repair-scope routing.

### Repair Estimator MVP

The Streamlit dashboard includes repair estimating inside the **Estimating Assistant** page. Choose **Estimate Type → Roof Repair**, or leave **Auto-detect** on and enter repair notes. Repair mode reads the normalized VSimple repair tables from Postgres:

- `repair_jobs`
- `repair_material_usage`
- `repair_labor_usage`
- `repair_scope_text`
- `repair_outcomes`

The repair estimator engine is deliberately separate from the full roof coating/restoration estimator. It parses repair notes, retrieves similar historical repairs using messy text evidence, estimates labor/material/invoice ranges from repair history, and exports a reviewable JSON/XLSX audit package. Roof Restoration / Coating and Insulation modes continue to use the existing field-notes estimator flow.

CLI usage:

```bash
python -m jobscan.repair_estimator.estimate \
  --notes "Small active leak around one pipe boot on TPO roof. Easy access. Seal and reinforce with fabric if needed." \
  --db-url "$NEON_DATABASE_URL" \
  --out-dir output/repair_estimator/audit
```

Evaluation harness:

```bash
python evals/repair_estimator/run_repair_eval.py \
  --db-url "$NEON_DATABASE_URL" \
  --write-audit \
  --audit-output-dir output/repair_estimator/eval_audit
```

Guardrails:

- Fewer than five similar historical repairs marks the result low confidence.
- Vague repair notes produce missing-info review flags.
- Material quantities are not invented; the MVP uses historical material cost/package evidence and flags estimator review.
- Emergency leak calls get an urgency review flag and a range adjustment.

### Field-notes estimator

The first field-notes-to-estimate engine is available inside the Streamlit **Estimating Assistant** page. It accepts rough notes such as:

```text
Metal roof, about 12,000 sqft, rusted fasteners, wants 15-year warranty, lots of rooftop units, medium access, Louisville KY.
```

It returns parsed fields, missing information, recommended scope, material assumptions, labor assumptions, travel assumptions, historical calibration evidence, similar historical estimate examples, review flags, a low/target/high estimate range, and a draft workbook input plan. The result is an internal recommendation for estimator review, not a final customer-facing quote.

Current data source hierarchy:

1. `pricing_catalog` for current approved material pricing.
2. `estimate_template_rows` for historical workbook-template calibration from extracted XLSX `document_content`.
3. `estimate_line_item_classifications` as fallback evidence for older/non-standard line-item extraction.
4. Local staging files only as a development fallback.

Run the parser after document extraction, then open Streamlit:

```bash
python -m jobscan.estimator.template_rows \
  --parse-existing \
  --database-url "$NEON_DATABASE_URL"
```

Limitations:

- No external routing, geocoding, OCR, or LLM APIs are used.
- Travel uses city/state distance buckets from `1132 Equity Street, Shelbyville, KY`.
- Material pricing uses `pricing_catalog` first; historical fallback is flagged for review.
- Labor calibration is strongest when `estimate_template_rows` has standard Spray-Tec workbook rows with labor days/hours/costs.
- Workbook generation remains a draft/internal review step.

Estimator limitations:

- No external maps, OCR, routing, or LLM APIs are used.
- Travel is estimated from simple city/state buckets or staged coordinates when available.
- Material formulas are coarse; roof coating uses `gallons = sqft * wet_mils / 1604` plus waste.
- Labor is inferred from similar estimate/tracking history or configurable productivity assumptions.
- Every result requires estimator review before quoting.

## Streamlit app

Run the Streamlit app:

```bash
streamlit run app.py
```

The root page is the local SharePoint scanner app. BidScope AI is available from the Streamlit sidebar pages menu. If the deployed app uses the richer `dashboard/app.py` entrypoint, BidScope AI is also listed explicitly in that dashboard sidebar navigation.

## BidScope AI prototype

BidScope AI is a Streamlit page for construction plan/spec PDFs and ZIP bid packages. It identifies trade scope evidence, extracts sheet references, builds a directed reference graph, and produces an estimator-reviewed measurement tree. The current trade profiles are `Foam Insulation` and `Roofing`.

What it does:

- Upload a single PDF, multiple PDFs, or one or more ZIP files containing PDFs. Streamlit is configured for uploads up to 2 GB.
- Scan ZIP files for PDFs only; folders, macOS metadata, and non-PDF files are skipped with warnings.
- BidScope performs a lightweight triage scan across package PDFs for transparency and priority. It samples filenames, paths, PDF metadata/text from first/last/index pages, and sheet ID signals without OCRing or rendering every page.
- Triage classifies each PDF as `likely_relevant`, `possibly_relevant`, or `likely_irrelevant`, but the default BidScope architecture keeps every PDF in the lightweight global index before building the reference tree.
- BidScope builds a package-wide page/reference graph first, then finds high-confidence trade seed pages and expands through sheet references, wall types, partition types, spec sections, and detail callouts.
- Connected measurement pages are included even if they do not contain direct trade keywords. For example, a floor plan can be included through a path such as `A-601 Wall Types -> W3 -> A-301 Section -> A-101 Floor Plan`.
- Large bid packages use progressive indexing. BidScope manifests every PDF first, fast-scans high/medium priority PDFs, builds a sheet map and reference graph from sampled/index pages, and defers low-priority pages instead of discarding them.
- Page processing statuses include `manifested`, `sampled`, `lightly_indexed`, `graph_included`, `deep_analyzed`, and `deferred`.
- Processing budgets limit initial sampling, light indexing, deep analysis, OCR, and runtime. If a budget is hit, BidScope returns partial results and offers a continuation control rather than failing.
- The triage decision table is shown for transparency. Advanced controls allow manual selection review or "Analyze all documents anyway" for unusual packages.
- Large uploads warn above 500 MB, and selected/indexed documents warn when they exceed 1 GB or approach the temp-disk safety threshold.
- Split the PDF into page records using PyMuPDF.
- Extract embedded text and use optional pytesseract OCR only when text is sparse.
- Detect sheet numbers and sheet titles.
- Score pages with deterministic trade-profile keywords. Foam insulation uses SPF, closed-cell/open-cell, R-value, air/vapor barrier, and envelope terms. Roofing uses roof replacement/coating, TPO, EPDM, modified bitumen, metal roof, cover board, parapet, coping, flashing, drains, gutters, and downspouts.
- Extract references such as `1/A-301`, `Detail 5/A-502`, `Wall Type W3`, and `Partition Type P-2`.
- Build a NetworkX directed graph of sheet references.
- Resolve references across all uploaded documents in the package and warn when duplicate sheet IDs make a reference ambiguous.
- Expand high-confidence trade scope pages to referenced neighbors up to the selected depth.
- Classify selected sheets into roles such as `spec_definition`, `assembly_definition`, `measurement_page`, `height_or_opening_confirmation`, and `detail_reference`.
- Optionally upload completed STACK Takeoff Quantity CSV exports for evaluation/training feedback. BidScope reports expected pages, predicted pages, matches, misses, extras, recall, precision, and precision@10/25/50.
- Roofing guidance focuses on roof areas from roof plans, edge/perimeter/coping/flashing from plans/details/elevations, and counts for drains, curbs, and penetrations.
- Export the measurement tree as JSON and relevant sheets as CSV.

BidScope AI does not calculate a final bid. It produces a measurement map for estimator review. No paid API key is required. TODO markers in the code show where an LLM could later summarize evidence or resolve ambiguous sheet relationships.

## Recommended rollout

1. Run on 10-20 real job folders.
2. Review extracted fields and warning rates.
3. Add alternate extractor profiles if older estimate templates differ.
4. Write the job index to SharePoint List or SQL.
5. Let Zapier handle Teams notifications first.
6. Add QuickBooks writes only after human-reviewed accuracy is solid.

## Current limitations

- Excel extraction assumes the estimate workbook contains an `Estimate` sheet with labels like `Job Name:`, `Job Type:`, `Total Job Cost`, and `Work Sheet Price`.
- Invoice number and amount are parsed from invoice filenames first. PDF OCR/text extraction can be added later.
- The Graph sync currently mirrors files into a local cache, then runs the same scanner. This is intentional: it keeps the extractor testable and avoids brittle direct workbook reads through API calls.
- SharePoint permissions must be approved by a Microsoft 365 admin.
