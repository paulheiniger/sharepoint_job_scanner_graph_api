# SharePoint Job Folder Scanner

A Python starter project for scanning roofing job folders directly from SharePoint using Microsoft Graph, extracting estimate/invoice/status metadata, and producing a dashboard-ready job index.

The local scanner still works, but the primary path is now:

```text
Microsoft Graph → SharePoint job folder cache → extractor → job index → Zapier handoff
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
  --force
```

The batch scanner syncs each root, scans its local cache, combines all records into one index, and annotates each job with `division`, `pipeline_status`, `scan_root`, and `source_year`. If one root fails, the remaining roots still run; failures are written to `output/batch_scan_summary.json` under `scan_errors`. Missing signed contracts remain visible through `has_signed_contract`; for Contracted roots, the summary also includes `contracted_without_signed_contract_count` as a non-warning metric.

## Run local/exported folder scan

Useful for testing parser changes without hitting Graph:

```bash
python -m jobscan.cli examples/sample_export \
  --out output/job_index.csv \
  --json output/job_index.json \
  --xlsx output/job_index.xlsx
```

## Scan timesheets

The timesheet scanner reads exported SharePoint folders or ZIP files containing daily Excel timesheets, then writes detail rows plus daily/code and project/code summaries.

Run against a local export:

```bash
python -m jobscan.timesheet_sync \
  --root "Timesheets" \
  --out output/timesheet_entries.csv \
  --summary output/timesheet_summary.csv \
  --json output/timesheet_entries.json
```

This also writes `output/timesheet_project_summary.csv`.

Filter by date or employee:

```bash
python -m jobscan.timesheet_sync \
  --root "Timesheets" \
  --start-date 2026-06-01 \
  --end-date 2026-06-30 \
  --employee Aaron \
  --out output/timesheet_entries.csv \
  --summary output/timesheet_summary.csv
```

Preview counts without writing files:

```bash
python -m jobscan.timesheet_sync --root "Timesheets" --dry-run
```

The same scanner can download `.xlsx` timesheets through Microsoft Graph before parsing:

```bash
python -m jobscan.timesheet_sync \
  --sharepoint-url "https://yourtenant.sharepoint.com/sites/Operations" \
  --library "Documents" \
  --folder "Timesheets" \
  --out output/timesheet_entries.csv \
  --summary output/timesheet_summary.csv \
  --json output/timesheet_entries.json
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

## Send Job Index To SharePoint List

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

This module sends one aggregate payload only. It includes total jobs, final-price totals, division and status breakdowns, warning counts, completed-folder issue counts, aerial/photo totals, top warning jobs, and top highest-value jobs. It also includes Teams-ready newline-separated text fields: `division_summary_text`, `pipeline_summary_text`, `warning_jobs_text`, and `top_value_jobs_text`.

For Microsoft Teams Zapier actions, set Message Text Format / Format to HTML and use `{{teams_message_html}}` as the message body. The payload also includes section-level HTML fields: `division_summary_html`, `pipeline_summary_html`, `warning_jobs_html`, and `top_value_jobs_html`.

## Streamlit dashboard prototype

```bash
streamlit run app.py
```

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
