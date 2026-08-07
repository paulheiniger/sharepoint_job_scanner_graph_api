from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class DashboardSourceReference:
    source: str
    source_type: str
    used_for: str
    lineage_or_rule: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _ref(source: str, source_type: str, used_for: str, lineage_or_rule: str) -> DashboardSourceReference:
    return DashboardSourceReference(source, source_type, used_for, lineage_or_rule)


DASHBOARD_PAGES = [
    "Job Board",
    "Sales Dashboard",
    "Operations Dashboard",
    "Office Timesheet",
    "Timesheet Job Touches",
    "Job Tracking",
    "Warranty Registry",
    "Schedule Calendar",
    "Daily Crew Dispatch",
    "Daily Production",
    "AI Roof Measure",
    "Pricing Catalog",
    "Admin / Health",
]


PAGE_SOURCE_REFERENCES: dict[str, tuple[DashboardSourceReference, ...]] = {
    "Sales Dashboard": (
        _ref(
            "load_job_board_df()",
            "Python aggregation",
            "Job population, pipeline value, sales stage, estimator, lead source",
            "Uses job_board_static_snapshot when populated; otherwise dashboard_jobs with VSimple, estimate, and workflow enrichments.",
        ),
        _ref(
            "dashboard_jobs; dashboard_estimates; estimate_template_rows",
            "PostgreSQL views/tables",
            "Job identity, estimate value, square footage, labor, and status evidence",
            "dashboard_jobs is defined in db/dashboard_views.sql; estimate values can be supplemented when the primary job value is blank or zero.",
        ),
        _ref(
            "vsimple_projects; vsimple_sharepoint_job_matches_accepted",
            "PostgreSQL tables",
            "CRM project type, owner/estimator, lead source, bid, billing, cost, and area enrichment",
            "Only accepted VSimple-to-SharePoint matches are used.",
        ),
    ),
    "Operations Dashboard": (
        _ref(
            "operations_dashboard_all_jobs_snapshot; operations_dashboard_ops_snapshot",
            "PostgreSQL snapshot tables",
            "Primary prepared operations dataset",
            "Used when both snapshots are populated; otherwise the page rebuilds the dataset live.",
        ),
        _ref(
            "dashboard_contracted_backlog; crew_schedule",
            "PostgreSQL view/table",
            "Backlog population, schedule dates, crew, readiness, and blocking issues",
            "Readiness and schedule health are inferred in dashboard/app.py from folder, status, schedule, warning, and job-spec evidence.",
        ),
        _ref(
            "job_tracking_summary; job_tracking_daily_entries; estimate_template_rows",
            "PostgreSQL views/tables",
            "Actual hours/materials versus estimated production",
            "Percent complete and risk are calculated from available tracking/material ratios; missing actuals remain a coverage gap.",
        ),
    ),
    "Job Board": (
        _ref(
            "job_board_static_snapshot or dashboard_jobs",
            "PostgreSQL snapshot/view",
            "Base job list, pipeline/status, value, documents, and folder links",
            "The snapshot is preferred when available; live mode uses dashboard_jobs.",
        ),
        _ref(
            "documents; sharepoint_drive_items; document_content",
            "PostgreSQL tables",
            "Proposal/estimate dates, document signals, and SharePoint file provenance",
            "Folder-name matching is a fallback when a stable job_id link is unavailable.",
        ),
        _ref(
            "job_workflow_overrides; job_workflow_events; crew_schedule; dashboard_job_warnings_actionable",
            "PostgreSQL tables/view",
            "User workflow edits, auditable Kanban stage moves, scheduling, priorities, and action warnings",
            "Board status is normalized in Python from workflow, pipeline, folder, and document evidence.",
        ),
    ),
    "Warranty Registry": (
        _ref(
            "warranty_master_clean; job_warranty_evidence; warranty_source_records",
            "PostgreSQL cleaned view and evidence tables",
            "Issued/reported warranty terms, dates, contacts, review flags, and source links",
            "The cleaned master deduplicates by VSimple/project or job identity. Issued documents remain distinct from historical reported warranties.",
        ),
        _ref(
            "vsimple_warranty_projects_clean; vsimple_project_contacts_clean; vsimple_customers_clean",
            "PostgreSQL cleaned tables",
            "Project identity and customer follow-up contacts",
            "Exact VSimple project-contact IDs are used; missing contacts remain flagged for review rather than inferred.",
        ),
    ),
    "Office Timesheet": (
        _ref(
            "office_timesheet_entries",
            "PostgreSQL table",
            "Entered/imported office time, milestones, next actions, and notes",
            "Imported rows retain source file, sheet, row, Graph drive/item IDs, and file path. Persisted Graph IDs resolve to clickable SharePoint file links.",
        ),
        _ref(
            "dashboard_jobs",
            "PostgreSQL view",
            "Job choices and job metadata",
            "Job matching should use job_id; project-name matching is less authoritative.",
        ),
    ),
    "Timesheet Job Touches": (
        _ref(
            "office_timesheet_entries",
            "PostgreSQL table",
            "Office activity by employee, date, job, milestone, and next action",
            "Durations are summed from stored duration_hours; rows without a job_id may be matched by normalized project text.",
        ),
        _ref(
            "dashboard_jobs",
            "PostgreSQL view",
            "Customer/job context for timesheet activity",
            "Name-based matches are heuristic and should be reviewed where job_id is missing.",
        ),
    ),
    "Job Tracking": (
        _ref(
            "job_tracking_summary; job_tracking_daily_entries",
            "PostgreSQL view/table",
            "Job-level and daily labor, travel, production, material, and weather entries",
            "Dashboard totals aggregate daily entries by job and work date. Source filenames are matched to persisted document/drive metadata for clickable file links when an exact job-and-filename match exists.",
        ),
        _ref(
            "job_tracking_estimated_material_snapshot; job_tracking_estimate_budget_snapshot",
            "PostgreSQL snapshot tables",
            "Estimated material and labor budget comparison",
            "Snapshots are preferred; estimate_template_rows is the fallback source.",
        ),
        _ref(
            "estimate_template_rows",
            "PostgreSQL table",
            "Fallback estimated quantities, costs, days, crew, and hours",
            "The latest/strongest workbook rows are selected and normalized before comparison.",
        ),
    ),
    "Schedule Calendar": (
        _ref(
            "crew_schedule",
            "PostgreSQL table",
            "Scheduled start/end dates, duration, crew, priority, status, and blockers",
            "Calendar edits write back to this table.",
        ),
        _ref(
            "dashboard_estimates; dashboard_jobs",
            "PostgreSQL views",
            "Job, proposal, value, square footage, labor, and document context",
            "Estimate data is joined by job_id; missing schedule estimates may be supplemented from the latest estimate.",
        ),
        _ref(
            "job_tracking_daily_entries",
            "PostgreSQL table",
            "Optional actual-work events displayed on the calendar",
            "Actual tracking events are separate from planned crew_schedule events.",
        ),
    ),
    "Daily Crew Dispatch": (
        _ref(
            "daily_dispatch; daily_dispatch_roster; daily_dispatch_crew_assignments",
            "PostgreSQL tables",
            "Dispatch date, roster, crew assignments, vehicles, and daily instructions",
            "Prior-day assignments may be copied only through the explicit dispatch workflow.",
        ),
        _ref(
            "crew_schedule; dashboard_jobs",
            "PostgreSQL table/view",
            "Jobs available for dispatch and their schedule/job context",
            "Dispatch is operational state and does not alter the source SharePoint job record.",
        ),
    ),
    "Daily Production": (
        _ref(
            "daily_production_entries; daily_production_material_usage",
            "PostgreSQL tables",
            "Submitted production, conditions, safety, equipment, and material usage",
            "Form submissions upsert by production entry identity; material rows are stored separately.",
        ),
        _ref(
            "crew_schedule; dashboard_jobs",
            "PostgreSQL table/view",
            "Scheduled job and crew context",
            "Production entries are actual field reports; estimated values come from job/estimate sources.",
        ),
    ),
    "Estimating Assistant": (
        _ref(
            "Uploaded notes, images, photos, and reference workbooks",
            "User-provided evidence",
            "Current-job scope interpretation",
            "Original evidence and extracted notes are retained in estimator session/export artifacts.",
        ),
        _ref(
            "estimate_template_rows; template_lookup_tables; pricing_catalog",
            "PostgreSQL tables",
            "Workbook decision menu, formulas/selectors, and pricing candidates",
            "Excel templates remain the calculation authority; AI proposes decisions rather than totals.",
        ),
        _ref(
            "Historical answer keys; estimator memory; product catalog/properties/rules",
            "PostgreSQL knowledge layers",
            "Comparable evidence, learned corrections, and product guidance",
            "Historical rows are evidence only and must not be copied into current-job changes without current-scope support.",
        ),
    ),
    "AI Roof Measure": (
        _ref(
            "Mapbox satellite imagery and geocoding",
            "External imagery service",
            "Nadir image, address location, scale, and image bounds",
            "Pixel measurements depend on image calibration, zoom, and coordinate transforms.",
        ),
        _ref(
            "Building footprint datasets; local footprint PostgreSQL slice",
            "External/open geospatial data",
            "Initial building topology and spatial constraint",
            "Footprint completeness and image alignment vary by provider and capture date.",
        ),
        _ref(
            "SAM2; OpenAI vision; LiDAR point data",
            "Model and geospatial evidence",
            "Segmentation, correction prompts/QA, and optional elevation support",
            "The final editable polygon and exported trace record the accepted candidate; model output is not an authoritative survey.",
        ),
    ),
    "Pricing Catalog": (
        _ref(
            "pricing_catalog",
            "PostgreSQL table",
            "Products, vendors, prices, effective dates, review status, and source files",
            "Current rows are selected by is_current/status; source_file and source_type identify the imported evidence.",
        ),
        _ref(
            "product_catalog; product_properties; product_rules",
            "PostgreSQL knowledge tables",
            "Product identity and technical guidance",
            "Product guidance does not override a reviewed pricing record.",
        ),
    ),
    "Ask Spray-Tec": (
        _ref(
            "dashboard_jobs; dashboard_estimates; estimate_template_rows; pricing_catalog",
            "PostgreSQL views/tables",
            "Structured operational and estimating answers",
            "The assistant selects relevant datasets based on the question and should return record-level citations.",
        ),
        _ref(
            "documents; document_content",
            "PostgreSQL tables",
            "Source-document search and extracted text evidence",
            "Extracted text retains document/file references; OCR/extraction quality affects answer coverage.",
        ),
        _ref(
            "crew_schedule; job_tracking_summary; office_timesheet_entries",
            "PostgreSQL tables/views",
            "Scheduling, field production, and office activity questions",
            "These are operational state stores, not direct live Microsoft Graph reads.",
        ),
    ),
    "BidScope AI": (
        _ref(
            "Uploaded bid documents and drawings",
            "User-provided evidence",
            "Bid scope, requirements, quantities, and review package",
            "Results depend on document/OCR readability and should cite source pages.",
        ),
        _ref(
            "BidScope extraction and review artifacts",
            "Local generated artifacts",
            "AI extraction, human review, and export",
            "Generated scope is not written back to an authoritative source until explicitly approved.",
        ),
    ),
    "Admin / Health": (
        _ref(
            "PostgreSQL information_schema and registered dashboard relations",
            "Database metadata",
            "Table/view availability, row counts, and latest timestamps",
            "Health queries inspect the configured operational database without exposing credentials.",
        ),
        _ref(
            "documents; estimate_template_rows; pricing_catalog",
            "PostgreSQL tables",
            "Extraction status, template coverage, and pricing freshness",
            "Use this page to confirm whether stale or missing upstream data explains dashboard gaps.",
        ),
    ),
    "Owner Overview": (
        _ref(
            "dashboard_jobs; dashboard_top_open_jobs",
            "PostgreSQL views",
            "Pipeline totals, job counts, status values, and top open jobs",
            "Headline values are sums over the filtered dashboard_jobs rows.",
        ),
        _ref(
            "dashboard_jobs_needing_action_clean; dashboard_division_summary",
            "PostgreSQL views",
            "Action counts and division rollups",
            "Definitions are maintained in db/dashboard_views.sql.",
        ),
    ),
    "Pipeline / Money": (
        _ref(
            "dashboard_jobs; dashboard_top_open_jobs",
            "PostgreSQL views",
            "Total/proposed/contracted value and top open jobs",
            "Status metrics group normalized pipeline_status values after active sidebar filters.",
        ),
        _ref(
            "dashboard_job_value_bands",
            "PostgreSQL view",
            "Job counts and value by configured value band",
            "Band boundaries are defined in db/dashboard_views.sql.",
        ),
    ),
    "Sales Follow-Up": (
        _ref(
            "dashboard_sales_followup",
            "PostgreSQL view",
            "Proposed jobs, missing information, and follow-up status",
            "Follow-up status is derived from pipeline state and missing estimate/value fields.",
        ),
        _ref(
            "dashboard_job_value_bands",
            "PostgreSQL view",
            "Proposed value-band chart",
            "The page filters this view to Proposed pipeline rows.",
        ),
    ),
    "Contracted Backlog / Scheduling": (
        _ref(
            "dashboard_contracted_backlog; dashboard_contracted_backlog_summary",
            "PostgreSQL views",
            "Contracted jobs, backlog value, divisions, and scheduling readiness",
            "Contracted status definitions are maintained in db/dashboard_views.sql.",
        ),
        _ref(
            "crew_schedule",
            "PostgreSQL table",
            "Planned dates, duration, crew, and blockers",
            "Unscheduled backlog rows have no qualifying crew_schedule start date.",
        ),
    ),
    "Project Scheduling": (
        _ref(
            "crew_schedule",
            "PostgreSQL table",
            "Editable project schedule, crew assignments, priorities, and blockers",
            "Edits on this page write to operational schedule state.",
        ),
        _ref(
            "dashboard_contracted_backlog; dashboard_jobs",
            "PostgreSQL views",
            "Candidate contracted jobs and job/document context",
            "Jobs are matched by job_id; unscheduled candidates are filtered against existing schedule rows.",
        ),
    ),
    "Jobs Needing Action": (
        _ref(
            "dashboard_jobs_needing_action_clean",
            "PostgreSQL view",
            "Missing invoices, final prices, contracts, and other action rules",
            "Each view row represents an action reason, so one job can contribute multiple action rows.",
        ),
    ),
    "Closeout / Billing Risk": (
        _ref(
            "dashboard_closeout_billing_risk; dashboard_closeout_billing_risk_rollup",
            "PostgreSQL views",
            "Completed-job documentation/billing issues and value at risk",
            "Issue rows are rule-derived; one job may have more than one closeout issue.",
        ),
    ),
    "Documentation Risk": (
        _ref(
            "dashboard_documentation_risk; dashboard_documentation_summary",
            "PostgreSQL views",
            "Missing aerials, photos, specs, contracts, invoices, and warranties",
            "Document flags originate from indexed SharePoint documents and extracted metadata.",
        ),
        _ref(
            "documents; sharepoint_drive_items",
            "PostgreSQL tables",
            "Underlying document inventory and Graph identifiers",
            "The dashboard reads persisted scan state rather than querying Microsoft Graph live.",
        ),
    ),
    "Job Warnings": (
        _ref(
            "dashboard_job_warnings_actionable",
            "PostgreSQL view",
            "Actionable job-level warning flags and warning text",
            "Warning definitions are maintained in db/dashboard_views.sql.",
        ),
    ),
    "Estimate Analytics": (
        _ref(
            "dashboard_estimates",
            "PostgreSQL view",
            "Estimate count, value, labor hours, duration, scope, and price/sq ft",
            "Metrics sum or average extracted estimate records after current filters.",
        ),
        _ref(
            "estimates; documents",
            "PostgreSQL tables",
            "Underlying extracted estimate and source-file identity",
            "Multiple estimate files can exist for one job; this page counts estimate records, not unique jobs.",
        ),
    ),
    "Estimate Quality Issues": (
        _ref(
            "dashboard_estimate_quality_issues",
            "PostgreSQL view",
            "Missing/zero estimate fields and calculation-quality rules",
            "One estimate/job may generate multiple issue rows.",
        ),
    ),
    "Line Item Analysis": (
        _ref(
            "dashboard_estimate_line_items_clean; dashboard_line_item_rollup_clean",
            "PostgreSQL views",
            "Line quantities, extended cost, labor hours/days, and category rollups",
            "Rollups sum cleaned extracted line items; source estimate and row identity remain in the detailed view.",
        ),
        _ref(
            "estimate_line_items; estimate_template_rows",
            "PostgreSQL tables",
            "Underlying extracted workbook rows",
            "Workbook extraction quality and duplicate-version handling affect totals.",
        ),
    ),
    "Estimate Adders": (
        _ref(
            "dashboard_estimate_adders_enhanced; dashboard_adder_business_category_rollup",
            "PostgreSQL views",
            "Adder lines, business categories, extended costs, and labor hours",
            "Adder classification is rule-based; source_sheet and source_row support workbook-level audit.",
        ),
    ),
    "STAMP Tracking": (
        _ref(
            "dashboard_stamp_tracking",
            "PostgreSQL view",
            "STAMP estimate count, value, duration, and labor",
            "STAMP identification is derived from estimate/source metadata in db/dashboard_views.sql.",
        ),
    ),
    "Raw Tables": (
        _ref(
            "Selected dashboard PostgreSQL view",
            "Direct database read",
            "Unformatted rows for audit and troubleshooting",
            "This page runs SELECT * against the selected allow-listed view; no cross-page aggregation is applied.",
        ),
    ),
}


PAGE_AUDIT_NOTES: dict[str, tuple[str, ...]] = {
    "Sales Dashboard": (
        "Sales stage and KPI counts are inferred from current pipeline/folder evidence; the weekly KPI section is explicitly a proxy until activity tracking is complete.",
        "sales_value can come from the job estimate, VSimple bid amount, or VSimple billing amount when earlier values are missing.",
        "Job-level exception tables link to the SharePoint job folder. Aggregate pipeline, estimator, category, and lead-source totals are grouped from those filtered job rows and therefore do not have one source file.",
    ),
    "Operations Dashboard": (
        "Check whether snapshot tables or live assembly were used before reconciling a number; both paths are documented above.",
        "AR Over 60 and accounting actuals are not connected and must not be treated as complete financial metrics.",
        "Operational rollups combine job folders, crew_schedule, extracted estimate budgets, and job-tracking actuals. Job-level tables link to the folder; aggregate status and risk metrics reconcile to the filtered job rows beneath them.",
    ),
    "Job Board": (
        "The page coalesces fields from several sources. Blank/zero primary values may be enriched from accepted VSimple matches or extracted estimates.",
        "Folder-name and document-text matches are lower-confidence fallbacks than stable job_id/Graph identifiers.",
        "The Folder column opens the persisted SharePoint folder URL when available. Proposal, estimate, contract, and tracking filenames are document signals; a filename without a persisted file URL is not presented as a clickable file.",
    ),
    "Office Timesheet": (
        "App-entered rows originate in office_timesheet_entries and link to the selected job folder. Imported rows retain source workbook path and Graph IDs; Graph-matched sources open the exact SharePoint workbook.",
    ),
    "Timesheet Job Touches": (
        "Recent Activity preserves the imported source workbook, sheet, row, and exact file link where Graph identifiers are available. Projects Moving links to the matched job folder.",
        "Employee, code, daily-touch, and weighted-touch charts aggregate office_timesheet_entries after date filters and job matching; no single file or folder represents those totals.",
    ),
    "Job Tracking": (
        "Actual production comes from job_tracking_daily_entries and job_tracking_summary. Exact job-and-filename matches to documents/sharepoint_drive_items provide clickable tracking-workbook links; the full stored path remains visible when no URL resolves.",
        "Estimate-vs-actual and material variance views combine tracking actuals with job_tracking estimate snapshots or estimate_template_rows fallback values. Those variance numbers therefore do not originate from one file.",
    ),
    "Warranty Registry": (
        "Issued-document evidence and historical reported warranties remain distinct on every row.",
        "Needs Review includes match conflicts or missing duration, dates, or actionable contact information.",
        "Job, issued-warranty, and VSimple links use persisted source URLs and are not reconstructed from names.",
    ),
    "Schedule Calendar": (
        "Planned events come from crew_schedule; optional actual-work events come from job_tracking_daily_entries. Selected job panels link to the SharePoint folder and available source documents.",
    ),
    "Daily Crew Dispatch": (
        "Dispatch rows are operational entries stored in daily_dispatch tables. Job identity and folder links come from dashboard_jobs; roster and assignment totals are aggregates of the selected dispatch date.",
    ),
    "Daily Production": (
        "Production and material totals aggregate submitted daily_production_entries and daily_production_material_usage rows. Job folder links come from dashboard_jobs; form-entered records do not have a separate SharePoint source file.",
    ),
    "Owner Overview": (
        "Totals are job-row totals, while action/warning views may contain multiple rows per job. Do not add issue-row counts to job counts.",
    ),
    "Jobs Needing Action": (
        "Count distinct job_id when reconciling affected jobs; the displayed action-item total counts reasons.",
    ),
    "Closeout / Billing Risk": (
        "Count distinct job_id when reconciling affected jobs; value-at-risk can be repeated if grouped from issue-level rows without deduplication.",
    ),
    "Estimate Analytics": (
        "Estimate Files counts extracted estimate records, not unique jobs. Filter or group by job_id for a job-level reconciliation.",
    ),
    "Estimate Quality Issues": (
        "Issue totals count rules triggered, not necessarily unique estimates or jobs.",
    ),
    "AI Roof Measure": (
        "Roof area is a calibrated imagery estimate, not a legal survey. Review the exported polygon, scale, imagery metadata, and pipeline trace.",
    ),
    "Estimating Assistant": (
        "Historical examples and product documents are evidence. Current-job workbook changes should be traceable to current scope, explicit defaults, or reviewed estimator decisions.",
    ),
    "Pricing Catalog": (
        "Catalog rows retain source_file, source_type, source_sheet, and source_page when imported. A source filename is not made clickable unless a persisted URL is available; vendor/category rollups aggregate multiple pricing records.",
    ),
    "Ask Spray-Tec": (
        "Answers can combine jobs, documents, schedules, tracking, timesheets, estimates, and pricing. Record-level citations should be used for specific facts; summaries may draw from multiple persisted datasets.",
    ),
    "Admin / Health": (
        "Health counts and timestamps are direct database metadata checks. They describe ingestion coverage and freshness rather than a SharePoint file or job folder.",
    ),
}


def references_for_page(page: str) -> list[dict[str, str]]:
    return [reference.to_dict() for reference in PAGE_SOURCE_REFERENCES.get(page, ())]


def audit_notes_for_page(page: str) -> tuple[str, ...]:
    return PAGE_AUDIT_NOTES.get(page, ())


def all_dashboard_pages() -> tuple[str, ...]:
    return tuple(DASHBOARD_PAGES)
