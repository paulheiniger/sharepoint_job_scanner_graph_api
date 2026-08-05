# Spray-Tec Business Intelligence API

This service exposes Spray-Tec's historical estimating evidence and reusable
estimating knowledge, plus bounded operational job evidence, to conversational
agents. The agent supplies the reasoning. The service does not call OpenAI.

Estimator and operational context operations remain read-only. The current
roofing and insulation milestone also provides a confirmation-gated draft
workbook action.
Semantic validation and SharePoint delivery remain deferred.

## Roof measurement operations

### `POST /v1/roof-measure/context`

Accepts a site address and returns a short-lived, model-neutral evidence
package containing:

- a signed north-up satellite image;
- a signed overlay labeling bounded building-footprint candidates;
- pixel-coordinate footprint components, calibrated plan area, and perimeter;
- image dimensions, map scale, coordinates, and source attribution; and
- optional Kentucky From Above LiDAR coverage metadata.

Use `view: "whole_site"` first. `building_detail` is a closer secondary view
and can crop a large roof or campus. The API uses Mapbox and existing footprint
providers, but it does not call OpenAI, SAM2, or another model. Raw LiDAR point
clouds are not returned to the agent. Signed images and their context expire
after 15 minutes by default.

### `POST /v1/roof-measure/calculate`

Accepts the unexpired `context_id` and either reviewed footprint candidate IDs
or custom polygons expressed in the context image's pixel coordinates. The
operation deterministically calculates horizontal plan-view area and perimeter.
When `pitch_rise_per_12` is supplied, it also applies the corresponding uniform
slope factor and returns surface area. Omitting pitch never silently treats plan
area as roof-surface area.

Every result is marked `requires_estimator_verification`. Footprints are
building evidence, not a survey: the estimator must verify the target building,
roof edges, overhangs, canopies, penetrations, excluded areas, and pitch before
using the measurement in an estimate.

## Estimating operation

### `POST /v1/estimating/context`

The request contains current-job facts extracted by Copilot:

```json
{
  "raw_notes": "30x40 metal building with walls and roof deck included.",
  "template_type": "insulation",
  "site_address": "314 E Aberdeen Drive, Trenton, OH",
  "scope": {
    "building_type": "metal building",
    "building_footprint_length_ft": 40,
    "building_footprint_width_ft": 30,
    "wall_height_ft": 9,
    "outside_walls_included": true,
    "ceiling_included": true,
    "estimated_sqft": 2226
  },
  "reference_job_ids": [],
  "exclude_job_ids": [],
  "exclude_source_files": []
}
```

The action-friendly response includes:

- comparable historical estimates and decision evidence;
- typed historical material usage, including quantities, basis measurements,
  application parameters, cost, support, and source references;
- typed historical labor performance, including crew, hours, days, cost, and
  observed productivity drivers;
- historical assemblies that describe which semantic categories occurred
  together in a comparable job context;
- approved estimator memories;
- pricing candidates and product guidance;
- historical foam-yield evidence;
- reviewable purchasing guidance that separates measured scope from package or
  production rounding;
- reviewable labor-plan guidance scaled from scope-compatible historical work
  while preferring current People-tab crew rates;
- semantic decision concepts and the inputs needed for their calculations;
- mileage context; and
- links and paths for the matched source estimates.

The request can hard-exclude historical jobs and completed estimate filenames.
Use these exclusions for evaluation and re-estimation so the target workbook
cannot be returned as its own comparable. The response echoes the applied
`retrieval_exclusions`.

For structured roofing area scopes, the response also includes deterministic
`scope_integrity`. Exclusive sections are reconciled against the declared roof
area, nested repair areas are excluded from the roof-area sum, and assembly
bases for foam, coating, board stock, and deck replacement are reported before
the agent drafts quantities.

For a reconciled roofing takeoff, `purchasing_guidance` provides explicit
candidate adjustments such as full-board-sheet rounding, coating/foam
production increments, and stock-length edge-metal rounding. Supported
historical ratios raise confidence but are not compounded with deterministic
package rounding. Every candidate remains review-required and supplies the
`quantity_adjustment_reason` expected by workbook generation.

`labor_plan_guidance` projects the existing closest-comparable decision and
labor calibration into semantic activities. It returns the current-job basis,
recommended hours, crew, days, hours per 1,000 square feet, comparable source,
confidence, and whether the daily rate came from the current People tab or only
from historical evidence. These are planning candidates; the workbook People
rates and labor formulas remain authoritative. Assembly-required activities
remain in the response even when no usable productivity evidence exists; those
rows are marked `missing_calibration` and `blocking_input_required` so the agent
cannot silently present an incomplete labor plan.

Decision concepts use semantic identifiers such as `insulation.foam`. Workbook
rows and cells are not decision identifiers. Template coordinates may be kept
as provenance in underlying evidence, but the Copilot-facing contract must
remain useful when a workbook is reordered or replaced.

The Phase A semantic collections are additive:

```text
historical_material_usage
historical_labor_performance
historical_assemblies
```

They are translated from the existing historical packet after the current
Estimating Assistant completes retrieval. The Assistant's row-oriented packet
and workbook behavior are not changed.

The longer-term architecture and migration plan are documented in
`docs/copilot_estimator_semantic_plan.md`.

### `POST /v1/estimating/workbook`

Creates a draft `.xlsx` only after the estimator explicitly confirms the
semantic draft and the request sends `confirmed: true`. The request contains
job header facts, semantic materials, labor, logistics, adders, scope text, and
specification notes. It does not expose template row numbers.

The service copies the selected clean roofing, insulation, or flooring template, resolves
supported labor and material sections by visible template labels, writes only template input
cells, preserves formulas, recalculates the saved workbook with a configured
spreadsheet engine, validates required cached cost outputs, and returns a
signed download link that expires in 15 minutes by default. It does not call an
LLM or upload the result to SharePoint. The existing Estimating Assistant
continues to use the same workbook writer and remains backward compatible with
its row-oriented inputs.

Before writing, the service profiles the supplied template by visible labels
and formula dependencies. It verifies sales/inspection mileage, truck mileage,
People-tab crew-day rates, supported labor tasks, material and labor subtotals,
total job cost, and final worksheet price. A moved but semantically equivalent
section can therefore be rediscovered; a missing dependency fails generation
instead of silently producing a partial estimate.

The semantic request may include the same roofing `structured_scope` used for
context retrieval. When supplied, workbook generation independently
reconciles exclusive and nested areas, checks `header.estimated_sqft`, requires
materials implied by the assembly, and rejects mismatched foam, coating, or
board measured areas. Full-removal scope also requires tear-off labor.

Material `area_sqft` is the measured scope. `basis_sqft` is the reviewed
formula or purchasing basis. When those values differ, the request must include
`quantity_adjustment_reason`; the generated workbook uses `basis_sqft` for its
formula input and records both values and the reason in the material-cell
comment.

The semantic request must explicitly include or exclude sales/inspection
travel, truck travel, loading labor, and traveling labor. Included travel needs
trip count and round-trip miles. Included loading/traveling labor needs hours
per trip and crew size. Included production labor needs days and a supported
People-tab crew size.

Roofing-derived templates can also receive a semantic `warranty` object with
manufacturer, years, type, optional area, and unit cost. The template formula
calculates warranty cost. If a selected template has no warranty calculation
section, an included warranty fails validation instead of being silently
omitted. The current insulation default does not expose a warranty row.

For insulation, `header.sqft_calculation_rows` can carry signed semantic area
components into the template's Sq Ft Calculation sheet. Gross areas are
positive and opening deductions are negative; their sum must reconcile to
`header.estimated_sqft`. This preserves the field-note geometry without making
specific workbook rows part of the agent contract.

Template selection can be configured independently:

The repository defaults are `templates/Estimate + Spec - Roofing.xlsx`,
`templates/Estimate + Spec - Insulation.xlsx`, and
`templates/Estimate + Spec - Flooring.xlsx`. The flooring template is a
sanitized roofing-derived workbook with NPI 707/polyaspartic coating formulas
and floor-repair labor activities discovered by visible labels. Deployment
configuration may override any file without changing the agent contract:

```text
ESTIMATOR_ROOFING_TEMPLATE_PATH=/path/to/roofing-template.xlsx
ESTIMATOR_INSULATION_TEMPLATE_PATH=/path/to/insulation-template.xlsx
ESTIMATOR_FLOORING_TEMPLATE_PATH=/path/to/flooring-template.xlsx
```

The host must provide LibreOffice-compatible `soffice`/`libreoffice`, or set:

```text
ESTIMATOR_WORKBOOK_RECALCULATOR=/path/to/soffice
```

Generation fails closed when recalculation is unavailable or required outputs
are blank, zero, erroneous, or no longer formula-driven.

### `POST /v1/estimating/workbook-options`

Creates one separate draft workbook for each of two to six explicitly approved
options. Each option has a unique user-facing label and repeats the complete
semantic estimate payload; options are not expressed as ambiguous partial
overrides. This supports alternatives such as different warranties, included
areas, material systems, thicknesses, or base and alternate scope packages.

The outer `confirmed: true` applies to every displayed option. Every workbook
is independently copied, populated, recalculated, and validated. Generation is
atomic: if any option fails, earlier artifacts from that request are removed
and no partial option set is returned. Successful responses provide one
short-lived signed download link and calculated-output summary per option.

## Job intelligence operations

### `POST /v1/jobs/search`

Searches the prepared job-board snapshot using bounded filters for customer,
job name, address, job ID, division, pipeline status, workflow status, owner,
and attention state. The response returns at most 25 jobs with headline
metrics, warnings, schedule signals, source links, freshness, and coverage.

### `GET /v1/jobs/{job_id}/context`

Returns the complete bounded evidence package for one authoritative `job_id`:

- job-board and workflow state;
- schedule and blocking issues;
- job-tracking summary and the 10 most recent daily entries;
- up to 20 related document records and SharePoint links;
- up to 10 recent office-timesheet entries; and
- attention items, source freshness, and coverage.

Timesheet entries joined by `job_id` are authoritative. Because the current
timesheet corpus has limited `job_id` coverage, the service may fall back to an
exact project-name match only when that job name is unique. The response marks
that evidence `exact_project_name` and warns that it is heuristic.

These operations do not call an LLM. They return evidence for ChatGPT or
Copilot to summarize.

## SharePoint document operations

### `POST /v1/sharepoint/documents/search`

Searches file names, persisted folder paths, and extracted text for SharePoint
documents already discovered by the SharePoint Job Scanner. The request is
bounded to 20 documents and can be narrowed by authoritative `job_id` and
document type. Results include the stable `document_id`, source URL, matched
content excerpts, extraction status, and whether stored Graph identifiers are
available.

The endpoint searches PostgreSQL rather than traversing SharePoint on every
request. This preserves the scanner's stored identifiers and scan state,
avoids tenant-wide Graph discovery, and makes result coverage explicit.

### `POST /v1/sharepoint/documents/fetch`

Returns bounded readable content and source locators for one stable
`document_id`. Stored extracted content is preferred. If text is missing and
the file has stored drive/item identifiers, the service may perform one
read-only Graph download into temporary storage and run the existing
deterministic document extractor. On-demand downloads are limited to supported
document types and 20 MB; files requiring OCR return metadata, their
SharePoint link, and a warning rather than invented content.

Both operations are read-only. They do not upload, modify, move, share, or
delete SharePoint files. Their coverage is limited to documents known to the
scanner and is not a tenant-wide SharePoint search.

## Sales intelligence operations

### `POST /v1/sales/pipeline`

Returns current pipeline totals, raw pipeline-status rollups, owner rollups,
top opportunities, warnings, and source folders. By default it includes
Proposed, Contracted, and Contracted Repairs jobs. Completed and other statuses
are available only when requested.

Owner evidence uses current workflow assignments first. When those are absent,
the most recent person to modify the proposal or estimate in SharePoint is used
as an explicitly inferred owner; the historical vSimple export is the final
fallback. Each record returns `owner_source`, and owner rollups include
`inferred_job_count`, so the agent does not present editor-based ownership as an
authoritative assignment.

### `POST /v1/sales/follow-ups`

Returns the proposed-job follow-up queue with:

- explicit follow-up date state when a date has been captured;
- proposal freshness derived from persisted proposal timestamps;
- ownership and ownership provenance;
- missing estimate/value information from the prepared sales-follow-up view;
- bounded attention items and source links.

Proposal freshness does not create a follow-up date. The API separately labels
missing dates, overdue explicit dates, aging proposals, and stale proposals.
Use `GET /v1/jobs/{job_id}/context` for supporting documents or operational
detail on one opportunity.

## Operations intelligence operations

### `POST /v1/operations/backlog`

Returns the active contracted backlog from the prepared operations snapshot,
excluding rows already classified as completed unless requested. The response
includes readiness and division rollups, unscheduled value, missing job
specifications, folder/status mismatches, schedule blockers, crew-assignment
gaps, and bounded job-folder links.

Use the rollups for totals rather than totaling the bounded record list.
`readiness_status` is persisted operations evidence and remains distinct from
pipeline status.

### `POST /v1/operations/schedule`

Returns a bounded schedule window with crew, schedule-health, project-health,
progress, tracking, and production-risk evidence. With no date range, the
normal schedule view covers today through the next 14 days. With
`risk_only: true` and no dates, it searches the full active operations
snapshot for persisted blockers and production-risk states.

The operation does not infer percent complete from conversation text. It
returns the snapshot's `actual_pct_source`, tracking status, and risk summary
so the agent can explain the evidence and its limitations. Use
`GET /v1/jobs/{job_id}/context` for detailed tracking entries and supporting
documents for one job.

For an empty schedule window, freshness and source-level schedule coverage are
still returned. `zero_result_reason`, `source_total_jobs`,
`scheduled_outside_window`, and `past_start_date_jobs` distinguish “no matching
starts” from an empty or unavailable operations source.

### `POST /v1/operations/production-budget-health`

Compares job-tracking quantities and hours with estimate-derived production
budget buckets. It returns job and bucket status, estimate-rate cost-used
proxies, estimated over-plan exposure, source freshness, coverage, and bounded
job-folder links.

This operation is deliberately not an accounting profitability endpoint:

- `estimated_cost_used_proxy` values tracked usage with estimate-derived rates;
- `budget_used_pct` is not percent complete;
- cost position does not establish realized profit, final margin, or a
  forecast at completion;
- ambiguous tracking IDs that aggregate more than ten source files are
  excluded and counted in coverage; and
- the mixed-unit Primer / Sealants bucket is excluded from dollar usage
  calculations rather than treating unlike quantities as interchangeable.

Use `over_plan_only: true` for an exception queue or pass authoritative
`job_ids` for selected-job detail. Every response has `truth_class: proxy` and
includes the methodology and limitations the agent must retain.

## Chart dataset operations

### `POST /v1/reporting/chart-data`

Returns bounded chart rows from the existing sales, operations, office, or
production-budget service. The endpoint owns chart semantics as well as the
aggregation: deterministic row order, category order and colors, orientation,
number formats, multi-scale handling, data-label guidance, and reference lines
are returned in the versioned `display` contract. This keeps rendering in the
client while preventing different assistants from silently changing the
meaning or ordering of the chart.

The `staging` object identifies whether the source is an operational query, a
current persisted snapshot, or a hybrid. Current snapshots improve reliability
and request cost, but they are overwritten during refresh and are not a
historical time series. `historical_series_available` therefore remains false;
the client may draw a trend only when rows contain an explicit period field,
such as `activity_date`.

Mixed-unit charts return one of three strategies: `shared_axis`, `dual_axis`,
or `small_multiples`. Production budget-by-job data also returns a 100% plan
reference line on `budget_used_pct`. The endpoint continues to return the
original truth class, freshness, coverage, warnings, and source tables.

### `POST /v1/reporting/chart-data.csv`

Downloads the same deterministically ordered rows for Data Analysis or an
explicit user-requested file. Formula-like text values are escaped for safe
spreadsheet opening. Display metadata remains available from the JSON endpoint.

## Office activity intelligence operation

### `POST /v1/office/activity`

Returns complete rollups for a bounded date window by:

- employee source name;
- office work code;
- project label;
- activity day; and
- captured hours versus activity-only touches.

The default window is the most recent seven calendar days and the maximum
window is 92 days. The action supports exact employee and work-code filters,
project-label text search, and a timed-only mode. Recent source records and
available SharePoint timesheet links are capped at 25, while the headline and
rollup queries cover the full requested date window.

Current historical office timesheets have little or no authoritative `job_id`
coverage. Project names therefore remain source labels rather than confirmed
job matches. The agent must not join or attribute them to a job unless a stable
`job_id` is returned or `GET /v1/jobs/{job_id}/context` supplies separately
qualified evidence.

### `POST /v1/office/job-progress`

Rolls office timesheet activity up by project label and returns captured hours,
touches, milestones, next actions, stale-activity signals, and job-link
coverage. It reuses the dashboard's token and phrase matching approach with a
stricter action boundary:

- a stable `job_id` stored on a timesheet is authoritative;
- a score of 75 or greater is returned as an inferred job link;
- scores from 58 through 74.9 are review candidates and are not attributed to
  a job; and
- weaker labels remain unmatched.

`stalled_after_days` means no captured office activity after the configured
threshold. It does not prove that no work occurred, that a deliverable is late,
or that the inferred job association is correct. Use `stalled_only: true` for
an exception queue and retain each record's `link_truth_class`,
`link_status`, match explanation, freshness, and coverage. Completed, invoiced,
cancelled, and closed linked jobs are excluded unless `include_closed` is true.

The endpoint is read-only. It does not persist inferred links or change source
timesheets; the new office timesheet tool can create authoritative links by
storing a selected job ID.

## Local run

Install dependencies:

```bash
python -m pip install -r services/estimator_api/requirements.txt
```

Set `NEON_DATABASE_URL` or `DATABASE_URL`. Roof context also requires
`MAPBOX_TOKEN` (or `MAPBOX_ACCESS_TOKEN`) and an artifact signing key through
`ESTIMATOR_ARTIFACT_SIGNING_KEY` or `ESTIMATOR_API_KEY`.

Read-only on-demand SharePoint document retrieval uses the scanner's existing
Microsoft Graph application credentials:

```text
MS_TENANT_ID
MS_CLIENT_ID
MS_CLIENT_SECRET
```

Do not place their values in source code or the generated OpenAPI contract.
Search continues to use the persisted document index when Graph is temporarily
unavailable; only a fetch without stored extracted text requires Graph.

Then run:

```bash
python -m services.estimator_api.server
```

Check the service:

```bash
curl http://127.0.0.1:8770/health
```

FastAPI publishes its runtime contract at:

```text
http://127.0.0.1:8770/openapi.json
```

The checked-in action contract contains the current read-only actions:

```text
services/estimator_api/openapi.json
```

Regenerate it after changing an API schema:

```bash
python -m services.estimator_api.generate_openapi
```

Replace the example server URL in the generated contract with the deployed
hostname before importing it into Copilot.

## Authentication

Local development does not require authentication unless an API key is
configured. A private ChatGPT action can use:

```text
ESTIMATOR_API_KEY=<random secret>
```

Send it as a bearer token or `X-API-Key`. Optional Azure Easy Auth enforcement
also remains available:

```text
ESTIMATOR_API_REQUIRE_AUTH=true
```

When enabled, the service requires a principal header injected by Azure. This
check is defense in depth, not standalone authentication. Clients must not be
able to bypass Easy Auth and reach the application directly.

Entra scopes, roles, group restrictions, and Copilot authentication remain
deferred until the tenant and administrative access are available. A temporary
public tunnel must require the API key and should be stopped after testing.

## Deferred operations

- semantic estimate validation;
- generalized compatibility mapping for additional or materially redesigned
  templates;
- SharePoint workbook delivery;
- estimator feedback as pending memory candidates;
- restricted memory approval;
- persistent session resume and audit; and
- model-neutral LiDAR height overlays and document-analysis actions;
- job, schedule, timesheet, and pricing mutation actions.
