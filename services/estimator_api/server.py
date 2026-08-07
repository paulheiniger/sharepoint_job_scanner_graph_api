from __future__ import annotations

import base64
import hashlib
import hmac
import html
from io import BytesIO
import os
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from PIL import Image
import requests

from jobscan.business.chart_history_service import HISTORY_DATASETS, get_chart_history
from jobscan.business.chart_service import build_chart_dataset, chart_dataset_csv
from jobscan.business.bidscope_service import (
    BidScopeContextExpiredError,
    BidScopeInputError,
    BidScopeUnavailableError,
    build_bidscope_review_packet,
    create_bidscope_measurement_context,
    prepare_bidscope_measurement_context,
    prepare_bidscope_measurement_context_from_inspection,
)
from ingest.package_ingest import inspect_path_package
from jobscan.business.bidscope_trace_service import trace_bidscope_regions

from jobscan.business.job_service import (
    JobIntelligenceUnavailableError,
    JobNotFoundError,
    get_job_context,
    search_jobs,
)
from jobscan.business.operations_service import (
    get_operations_backlog,
    get_operations_schedule,
)
from jobscan.business.office_service import get_office_activity
from jobscan.business.office_progress_service import get_office_job_progress
from jobscan.business.production_budget_service import get_production_budget_health
from jobscan.business.sales_service import (
    get_sales_followups,
    get_sales_pipeline,
)
from jobscan.business.sharepoint_document_service import (
    SharePointDocumentNotFoundError,
    SharePointDocumentUnavailableError,
    fetch_sharepoint_document,
    search_sharepoint_documents,
)
from jobscan.business.warranty_service import get_warranty_list, get_warranty_summary
from jobscan.db_connections import create_resilient_engine
from jobscan.quickbooks.oauth import (
    QuickBooksCompanyMismatchError,
    complete_admin_authorization,
    create_admin_authorization,
)
from jobscan.quickbooks.security import QuickBooksConfigurationError, QuickBooksStateError
from jobscan.quickbooks.service import (
    get_accounting_exceptions,
    get_accounting_summary,
    get_customer_context,
)
from jobscan.quickbooks.sync import sync_quickbooks
from jobscan.estimator.context_service import build_copilot_estimator_context
from jobscan.estimator.planning_snapshot import (
    PlanningSnapshotError,
    create_planning_snapshot,
    verify_planning_snapshot,
)
from jobscan.estimator.workbook_recommendations import (
    apply_api_planning_guidance,
    normalize_template_material_pricing,
)
from jobscan.estimator.workbook_service import (
    EstimateWorkbookInputError,
    EstimateWorkbookOutputError,
    EstimateWorkbookUnavailableError,
    create_estimate_workbook,
    create_estimate_workbook_options,
    discover_estimate_workbook_profile,
    estimate_template_path,
    estimate_template_version,
    resolve_estimate_artifact,
    validate_ai_edited_workbook,
)
from jobscan.estimator.sharepoint_staging import (
    EstimateSharePointStagingUnavailable,
    stage_estimate_workbook,
)
from jobscan.estimator.workbook_writer import safe_filename
from jobscan.env import load_project_env
from roof_measure.api_context import (
    RoofMeasureContextError,
    RoofMeasureContextExpiredError,
    RoofMeasureInputError,
    calculate_roof_measurement,
    create_roof_measure_context,
    resolve_roof_measure_asset,
)
from roof_measure.api_segmentation import segment_roof_measure_context
from .schemas import (
    ChartDatasetRequest,
    ChartDatasetResponse,
    BidScopeMeasurementContextRequest,
    BidScopeMeasurementContextResponse,
    BidScopePageSelectionRequest,
    BidScopePageSelectionResponse,
    BidScopePrepareAttachmentContextRequest,
    BidScopePrepareMeasurementContextRequest,
    BidScopeRegionTraceRequest,
    BidScopeRegionTraceResponse,
    EstimateContextRequest,
    EstimateContextResponse,
    EstimateWorkbookRequest,
    EstimateWorkbookResponse,
    EstimateWorkbookTemplateRequest,
    EstimateWorkbookTemplateResponse,
    EstimateWorkbookValidationRequest,
    EstimateWorkbookValidationResponse,
    EstimateWorkbookOptionArtifact,
    EstimateWorkbookOptionsRequest,
    EstimateWorkbookOptionsResponse,
    JobContextResponse,
    JobSearchRequest,
    JobSearchResponse,
    OperationsBacklogRequest,
    OperationsIntelligenceResponse,
    OperationsScheduleRequest,
    OfficeActivityRequest,
    OfficeActivityResponse,
    OfficeJobProgressRequest,
    OfficeJobProgressResponse,
    ProductionBudgetHealthRequest,
    ProductionBudgetHealthResponse,
    QuickBooksAccountingResponse,
    QuickBooksAccountingSummaryRequest,
    QuickBooksCustomerContextRequest,
    RoofMeasureCalculationRequest,
    RoofMeasureCalculationResponse,
    RoofMeasureContextRequest,
    RoofMeasureContextResponse,
    RoofMeasureSegmentationRequest,
    RoofMeasureSegmentationResponse,
    SalesFollowupRequest,
    SalesIntelligenceResponse,
    SalesPipelineRequest,
    SharePointDocumentFetchRequest,
    SharePointDocumentFetchResponse,
    SharePointDocumentSearchRequest,
    SharePointDocumentSearchResponse,
    WarrantySummaryRequest,
    WarrantySummaryResponse,
    WarrantyListRequest,
    WarrantyListResponse,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_ACTION_WORKBOOK_BYTES = 10 * 1024 * 1024
MAX_ACTION_BIDSCOPE_FILE_BYTES = 1024 * 1024 * 1024
MAX_ACTION_BIDSCOPE_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
load_project_env(PROJECT_ROOT / ".env")
PUBLIC_API_ORIGIN = os.getenv(
    "ESTIMATOR_API_PUBLIC_URL",
    "https://spraytec-business-api.icysand-5925ab36.eastus2.azurecontainerapps.io",
).rstrip("/")
app = FastAPI(
    title="Spray-Tec Business Intelligence API",
    description=(
        "Estimator evidence, controlled workbook generation, and read-only "
        "operational intelligence for conversational agents."
    ),
    version="0.27.1",
    servers=[{"url": PUBLIC_API_ORIGIN}],
)


PRIVACY_POLICY_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Spray-Tec Business Assistant Privacy Policy</title>
  <style>
    body { font-family: system-ui, sans-serif; line-height: 1.55; margin: 0; color: #17202a; background: #f6f8fa; }
    main { max-width: 760px; margin: 3rem auto; padding: 2rem; background: white; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,.08); }
    h1, h2 { line-height: 1.2; }
    h2 { margin-top: 1.8rem; }
    a { color: #0957a5; }
    .updated { color: #5f6b76; }
  </style>
</head>
<body>
<main>
  <h1>Spray-Tec Business Assistant Privacy Policy</h1>
  <p class="updated"><strong>Effective and last updated:</strong> August 6, 2026</p>
  <p>This policy describes how Spray-Tec, Inc. handles information sent to the Spray-Tec Business Assistant API through a custom GPT or another authorized client.</p>

  <h2>Information processed</h2>
  <p>The service may process job notes, customer or project names, site addresses, measurements, scope details, estimating decisions, operational questions, and synchronized QuickBooks customer and sales-transaction records supplied by authorized business systems. The initial QuickBooks integration excludes payroll, bank-account, and payment-card data. When a user provides an image or document to ChatGPT, the service generally receives the structured facts or notes that the GPT sends to the API, rather than the original file. At a user's request, the service may also retrieve metadata and readable content from SharePoint job documents already indexed by Spray-Tec's SharePoint Job Scanner.</p>

  <h2>How information is used</h2>
  <p>Information is used only to retrieve business evidence and source documents, prepare summaries and charts, support estimate decisions, validate estimate inputs, report synchronized accounting context, and generate draft estimate workbooks requested by an authorized user. QuickBooks operations exposed to the Assistant are read-only. Most SharePoint operations are read-only. When requested, a validated draft estimate may be uploaded only to Spray-Tec's configured SharePoint estimate-staging folder. Drafts require human review and are not automatically filed in permanent job folders.</p>

  <h2>Storage and retention</h2>
  <p>Context, reporting, and SharePoint document requests are processed to produce the requested response and are not intentionally added to a separate marketing or advertising database. A SharePoint source file downloaded for on-demand text extraction is held only in temporary service storage for that request. Generated workbooks and roof-measure context images are stored temporarily by the API so the requesting user can retrieve them; signed links normally expire after 15 minutes. Validated workbooks uploaded to the configured SharePoint staging folder remain there until Spray-Tec reviews, moves, or deletes them. Limited technical logs and temporary service files may be retained as needed for security, troubleshooting, reliability, and service operation.</p>

  <h2>Sharing and service providers</h2>
  <p>Spray-Tec does not sell information submitted to this service. Information may be processed by service providers used to operate it, including OpenAI for the ChatGPT experience, Microsoft Azure for API hosting, Microsoft Graph and SharePoint for authorized source-document retrieval, Intuit QuickBooks for authorized accounting synchronization, Mapbox for address geocoding and satellite imagery, and public mapping or LiDAR services used to retrieve building evidence. Those providers handle information under their own terms and privacy commitments. Information may also be disclosed when required by law or to protect the security and integrity of the service.</p>

  <h2>Security and appropriate use</h2>
  <p>The API uses access controls and encrypted network connections. Users should submit only information needed for Spray-Tec business purposes and should not submit payment-card details, Social Security numbers, health information, passwords, or other unnecessary sensitive personal information.</p>

  <h2>Your choices</h2>
  <p>Access to a shared GPT can be stopped by discontinuing its use. Questions about information handled by Spray-Tec, or requests to review or delete information where applicable, may be directed to Spray-Tec using the contact information below. ChatGPT conversation controls and deletion requests are managed separately through the user's OpenAI account.</p>

  <h2>Contact</h2>
  <p>Spray-Tec, Inc.<br>
     1132 Equity Street, Shelbyville, KY 40065<br>
     <a href="mailto:info@spray-tec.com">info@spray-tec.com</a><br>
     <a href="tel:+15026335499">502-633-5499</a></p>
</main>
</body>
</html>
"""


TERMS_OF_USE_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Spray-Tec Business Assistant Terms of Use</title>
  <style>
    body { font-family: system-ui, sans-serif; line-height: 1.55; margin: 0; color: #17202a; background: #f6f8fa; }
    main { max-width: 760px; margin: 3rem auto; padding: 2rem; background: white; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,.08); }
    h1, h2 { line-height: 1.2; }
    h2 { margin-top: 1.8rem; }
    a { color: #0957a5; }
    .updated { color: #5f6b76; }
  </style>
</head>
<body>
<main>
  <h1>Spray-Tec Business Assistant Terms of Use</h1>
  <p class="updated"><strong>Effective and last updated:</strong> August 7, 2026</p>
  <p>These terms govern use of the Spray-Tec Business Assistant and its supporting API (the “Service”), operated by Spray-Tec, Inc. The Service is a private business tool for authorized Spray-Tec personnel and approved representatives.</p>

  <h2>Authorized use</h2>
  <p>Users may access the Service only for legitimate Spray-Tec business purposes and only through accounts, workspaces, and systems they are authorized to use. Users must not attempt to bypass access controls, access another organization’s data, disrupt the Service, or use it for unlawful or deceptive activity. Access may be limited or revoked when needed to protect Spray-Tec, its customers, or connected systems.</p>

  <h2>Human review required</h2>
  <p>The Service assists with document retrieval, operational reporting, estimating, measurements, draft workbooks, and accounting context. Its responses and generated files may be incomplete or incorrect and are not final bids, contracts, invoices, accounting records, engineering conclusions, or professional advice. An authorized Spray-Tec employee must verify source documents, quantities, pricing, scope, customer information, and other material outputs before relying on them or sharing them externally.</p>

  <h2>QuickBooks connection</h2>
  <p>The initial QuickBooks integration is read-only. It synchronizes authorized company, customer, estimate, invoice, payment, and credit-memo information for internal reporting and customer context. It does not process charges, access payroll, or automatically create, edit, or delete QuickBooks accounting records. A QuickBooks company administrator controls authorization and may disconnect or reconnect the Service.</p>

  <h2>Data and connected services</h2>
  <p>The Service relies on third-party systems such as OpenAI, Microsoft Azure, Microsoft Graph and SharePoint, Intuit QuickBooks, and mapping or imagery providers. Availability may be affected by those services, network conditions, permissions, subscriptions, or source-data quality. Information handling is described in the <a href="/privacy">Spray-Tec Business Assistant Privacy Policy</a>.</p>

  <h2>Security and sensitive information</h2>
  <p>Users must protect their credentials and promptly report suspected unauthorized access. Payment-card details, Social Security numbers, passwords, health information, and other unnecessary sensitive personal information must not be submitted to the Service.</p>

  <h2>Availability and responsibility</h2>
  <p>The Service is provided for internal operational assistance and may be changed, suspended, or discontinued. To the extent permitted by law, Spray-Tec does not guarantee that the Service will be uninterrupted or error-free. Users remain responsible for reviewing outputs and following Spray-Tec’s established approval, estimating, accounting, safety, and records-management procedures.</p>

  <h2>Contact</h2>
  <p>Questions about these terms may be directed to:<br>
     Spray-Tec, Inc.<br>
     1132 Equity Street, Shelbyville, KY 40065<br>
     <a href="mailto:info@spray-tec.com">info@spray-tec.com</a><br>
     <a href="tel:+15026335499">502-633-5499</a></p>
</main>
</body>
</html>
"""


def _prepare_workbook_payload(payload: Any) -> tuple[dict[str, Any], list[str]]:
    prepared, material_warnings = normalize_template_material_pricing(
        payload.model_dump()
    )
    if prepared.get("labor_plan_mode") == "estimator_override":
        return prepared, [
            *material_warnings,
            "A reviewed estimator labor override was used: "
            + str(prepared.get("labor_override_reason") or "").strip()
        ]
    if prepared.get("template_type") != "roofing" or not prepared.get("structured_scope"):
        return prepared, material_warnings
    header = prepared.get("header") or {}
    site_address = ", ".join(
        value
        for value in (
            str(header.get("site_address") or "").strip(),
            str(header.get("city_state_zip") or "").strip(),
        )
        if value
    )
    snapshot_token = str(prepared.get("planning_snapshot_token") or "").strip()
    if snapshot_token:
        try:
            context = verify_planning_snapshot(
                snapshot_token,
                scope=prepared["structured_scope"],
                site_address=site_address,
                signing_key=_artifact_signing_key(),
            )
            prepared, planning_warnings = apply_api_planning_guidance(
                prepared,
                context,
            )
            return prepared, [
                *material_warnings,
                "Reused the signed planning snapshot; labor and logistics "
                "retrieval was not repeated.",
                *planning_warnings,
            ]
        except PlanningSnapshotError:
            material_warnings.append(
                "The planning snapshot was unavailable, expired, altered, or "
                "did not match the final geometry; labor guidance was refreshed."
            )
    try:
        context = build_copilot_estimator_context(
            scope=prepared["structured_scope"],
            template_type_hint="roofing",
            site_address=site_address,
            database_url=_database_url(),
            base_dir=PROJECT_ROOT,
            focus="labor",
        )
    except Exception as exc:
        return prepared, [
            *material_warnings,
            "API labor recommendations could not be refreshed before workbook "
            f"generation ({type(exc).__name__}); submitted labor was retained."
        ]
    prepared, planning_warnings = apply_api_planning_guidance(prepared, context)
    return prepared, [*material_warnings, *planning_warnings]


def _public_download_origin(request: Request, artifact_id: str) -> str:
    """Build an artifact URL that remains HTTPS behind Azure's proxy."""
    configured = str(os.getenv("ESTIMATOR_API_PUBLIC_BASE_URL") or "").strip()
    if not configured:
        return str(
            request.url_for(
                "download_estimate_workbook",
                artifact_id=artifact_id,
            )
        )
    parsed = urlsplit(configured)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise HTTPException(
            status_code=503,
            detail=(
                "ESTIMATOR_API_PUBLIC_BASE_URL must be an HTTPS origin without "
                "a path, query, or fragment."
            ),
        )
    return (
        f"{configured.rstrip('/')}"
        f"/v1/estimating/workbooks/{artifact_id}"
    )


@app.get("/", include_in_schema=False)
def service_root() -> dict[str, Any]:
    """Return public service metadata for deployment connectivity checks."""
    return {
        "ok": True,
        "service": "spraytec-estimator-api",
        "version": app.version,
        "health": "/health",
        "privacy": "/privacy",
        "terms": "/terms",
        "openapi": "/openapi.json",
        "action_openapi": "/openapi-actions.json",
    }


@app.get(
    "/openapi-actions.json",
    include_in_schema=False,
    response_class=FileResponse,
)
def action_openapi() -> FileResponse:
    """Return the GPT-safe OpenAPI contract generated for external actions."""
    return FileResponse(
        PROJECT_ROOT / "services" / "estimator_api" / "openapi.json",
        media_type="application/json",
    )


@app.api_route(
    "/privacy",
    methods=["GET", "HEAD"],
    response_class=HTMLResponse,
    include_in_schema=False,
)
def privacy_policy() -> HTMLResponse:
    """Return the public privacy policy required by shared GPT Actions."""
    return HTMLResponse(PRIVACY_POLICY_HTML)


@app.api_route(
    "/terms",
    methods=["GET", "HEAD"],
    response_class=HTMLResponse,
    include_in_schema=False,
)
def terms_of_use() -> HTMLResponse:
    """Return the public terms required for the private accounting integration."""
    return HTMLResponse(TERMS_OF_USE_HTML)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "spraytec-estimator-api",
        "version": app.version,
        "authentication_required": _authentication_required(),
        "api_key_required": bool(_configured_api_key()),
        "entra_principal_required": _entra_authentication_required(),
    }


@app.post(
    "/v1/estimating/context",
    response_model=EstimateContextResponse,
    response_model_exclude_none=True,
    response_model_exclude_defaults=True,
    operation_id="getEstimatorContext",
    summary="Retrieve estimator evidence and deterministic context",
    description=(
        "Returns a read-only evidence package for Copilot reasoning. "
        "This operation does not call an LLM or create an estimate."
    ),
)
def estimate_context(
    request: Request,
    payload: EstimateContextRequest,
) -> EstimateContextResponse:
    _require_authenticated_request(
        authorization=request.headers.get("Authorization"),
        api_key_header=request.headers.get("X-API-Key"),
        principal=request.headers.get("X-MS-CLIENT-PRINCIPAL"),
        principal_id=request.headers.get("X-MS-CLIENT-PRINCIPAL-ID"),
    )
    if not payload.raw_notes.strip() and not payload.scope:
        raise HTTPException(
            status_code=422,
            detail="Provide raw_notes, structured scope, or both.",
        )
    try:
        context_payload = build_copilot_estimator_context(
            scope=payload.scope,
            raw_notes=payload.raw_notes,
            template_type_hint=payload.template_type,
            site_address=payload.site_address,
            reference_job_ids=payload.reference_job_ids,
            exclude_job_ids=payload.exclude_job_ids,
            exclude_source_files=payload.exclude_source_files,
            database_url=_database_url(),
            base_dir=PROJECT_ROOT,
            include_source_metadata=payload.include_source_metadata,
            focus=payload.focus,
        )
        signing_key = _artifact_signing_key()
        if signing_key and context_payload.get("template_type") == "roofing":
            context_scope = context_payload.get("scope") or payload.scope
            if context_scope.get("area_scopes"):
                context_payload["planning_snapshot_token"] = create_planning_snapshot(
                    scope=context_scope,
                    site_address=payload.site_address,
                    labor_plan_guidance=context_payload.get("labor_plan_guidance") or [],
                    logistics_guidance=context_payload.get("logistics_guidance") or [],
                    signing_key=signing_key,
                    ttl_seconds=_planning_snapshot_ttl_seconds(),
                )
        return EstimateContextResponse.model_validate(context_payload)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Estimator context is unavailable: {type(exc).__name__}.",
        ) from exc


@app.post(
    "/v1/roof-measure/context",
    response_model=RoofMeasureContextResponse,
    response_model_exclude_none=True,
    operation_id="getRoofMeasureContext",
    summary="Retrieve calibrated roof imagery and footprint evidence",
    description=(
        "Returns short-lived signed satellite and footprint-overlay images, "
        "bounded building-footprint candidates, calibration, and optional "
        "public LiDAR coverage metadata. The operation does not call an LLM, "
        "OpenAI, or SAM2 and does not create a final roof measurement."
    ),
)
def roof_measure_context(
    request: Request,
    payload: RoofMeasureContextRequest,
) -> RoofMeasureContextResponse:
    _require_api_request(request)
    signing_key = _artifact_signing_key()
    if not signing_key:
        raise HTTPException(
            status_code=503,
            detail="Roof imagery signing is not configured.",
        )
    expires = int(time.time()) + _artifact_ttl_seconds()
    try:
        context = create_roof_measure_context(
            address=payload.address,
            job_id=payload.job_id,
            site_name=payload.site_name,
            site_type=payload.site_type,
            view=payload.view,
            include_lidar_coverage=payload.include_lidar_coverage,
            mapbox_token=(
                os.getenv("MAPBOX_TOKEN")
                or os.getenv("MAPBOX_ACCESS_TOKEN")
                or ""
            ),
            database_url=_database_url() or "",
            artifact_dir=_roof_measure_artifact_dir(),
            expires_at=expires,
        )
    except RoofMeasureInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RoofMeasureContextError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    context_id = str(context["context_id"])
    public_context = {
        key: value for key, value in context.items() if key != "created_at"
    }
    public_context["lidar_coverage"] = {
        key: value
        for key, value in dict(context.get("lidar_coverage") or {}).items()
        if key != "asset_url"
    }
    response_payload = {
        **public_context,
        "satellite_image_url": _signed_roof_asset_url(
            request=request,
            context_id=context_id,
            asset_name="satellite.png",
            expires=expires,
            signing_key=signing_key,
        ),
        "footprint_overlay_url": _signed_roof_asset_url(
            request=request,
            context_id=context_id,
            asset_name="footprint-overlay.png",
            expires=expires,
            signing_key=signing_key,
        ),
        "footprint_overlay_preview_media_type": "image/jpeg",
        "footprint_overlay_preview_base64": _roof_overlay_preview_base64(
            resolve_roof_measure_asset(
                context_id=context_id,
                asset_name="footprint-overlay.png",
                artifact_dir=_roof_measure_artifact_dir(),
            )
        ),
    }
    return RoofMeasureContextResponse.model_validate(response_payload)


@app.post(
    "/v1/roof-measure/segment",
    response_model=RoofMeasureSegmentationResponse,
    response_model_exclude_none=True,
    operation_id="segmentRoofMeasureContext",
    summary="Select the best SAM2 roof mask and return a full-size review overlay",
    description=(
        "Uses reviewed footprint IDs to fetch a tightly fitted source image and "
        "prompt private SAM2. LiDAR adds guarded high-band candidates when "
        "available. Automatically selects the top-ranked candidate and "
        "returns one zoomed review overlay; alternates remain internal diagnostics."
    ),
)
def roof_measure_segment(
    request: Request,
    payload: RoofMeasureSegmentationRequest,
) -> RoofMeasureSegmentationResponse:
    _require_api_request(request)
    signing_key = _artifact_signing_key()
    if not signing_key:
        raise HTTPException(
            status_code=503,
            detail="Roof imagery signing is not configured.",
        )
    try:
        result = segment_roof_measure_context(
            context_id=payload.context_id,
            selected_footprint_ids=payload.selected_footprint_ids,
            sam2_url=os.getenv("SAM2_SEGMENTATION_URL") or "",
            sam2_api_key=os.getenv("SAM2_API_KEY") or "",
            mapbox_token=(
                os.getenv("MAPBOX_TOKEN")
                or os.getenv("MAPBOX_ACCESS_TOKEN")
                or ""
            ),
            artifact_dir=_roof_measure_artifact_dir(),
            timeout_seconds=float(
                os.getenv("ROOF_MEASURE_SEGMENTATION_TIMEOUT_SECONDS") or "90"
            ),
        )
        asset_name = str(result.pop("candidate_overlay_asset_name"))
        expires = int(time.time()) + _artifact_ttl_seconds()
        asset_path = resolve_roof_measure_asset(
            context_id=payload.context_id,
            asset_name=asset_name,
            artifact_dir=_roof_measure_artifact_dir(),
        )
        preview_base64 = _roof_overlay_preview_base64(asset_path)
        full_size_base64 = _roof_overlay_file_base64(asset_path)
        response_payload = {
            **result,
            "candidate_overlay_url": _signed_roof_asset_url(
                request=request,
                context_id=payload.context_id,
                asset_name=asset_name,
                expires=expires,
                signing_key=signing_key,
            ),
            "candidate_overlay_preview_media_type": "image/jpeg",
            "candidate_overlay_preview_base64": preview_base64,
            "openaiFileResponse": [
                {
                    "name": "roof_sam2_best_candidate.jpg",
                    "mime_type": "image/jpeg",
                    "content": full_size_base64,
                }
            ],
        }
        return RoofMeasureSegmentationResponse.model_validate(response_payload)
    except RoofMeasureInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RoofMeasureContextExpiredError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except RoofMeasureContextError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"SAM2 roof refinement is unavailable: {type(exc).__name__}.",
        ) from exc


@app.post(
    "/v1/roof-measure/calculate",
    response_model=RoofMeasureCalculationResponse,
    response_model_exclude_none=True,
    operation_id="calculateRoofMeasurement",
    summary="Calculate roof area and perimeter from reviewed polygons",
    description=(
        "Calculates plan area and perimeter from reviewed footprint candidates "
        "or custom polygons. Optional pitch adds surface area. Custom polygons "
        "receive a tightly centered satellite overlay. No AI service is called, "
        "and estimator verification is always required."
    ),
)
def roof_measure_calculate(
    request: Request,
    payload: RoofMeasureCalculationRequest,
) -> RoofMeasureCalculationResponse:
    _require_api_request(request)
    try:
        result = calculate_roof_measurement(
            context_id=payload.context_id,
            selected_footprint_ids=payload.selected_footprint_ids,
            sections=[section.model_dump() for section in payload.sections],
            pitch_rise_per_12=payload.pitch_rise_per_12,
            artifact_dir=_roof_measure_artifact_dir(),
            sam2_candidate_id=payload.sam2_candidate_id,
            mapbox_token=(
                os.getenv("MAPBOX_TOKEN")
                or os.getenv("MAPBOX_ACCESS_TOKEN")
                or ""
            ),
        )
        selected_asset_name = str(result.pop("selected_overlay_asset_name"))
        expires = int(time.time()) + _artifact_ttl_seconds()
        signing_key = _artifact_signing_key()
        if not signing_key:
            raise RoofMeasureContextError("Roof imagery signing is not configured.")
        selected_asset_path = resolve_roof_measure_asset(
            context_id=payload.context_id,
            asset_name=selected_asset_name,
            artifact_dir=_roof_measure_artifact_dir(),
        )
        selected_preview_base64 = _roof_overlay_preview_base64(selected_asset_path)
        selected_full_size_base64 = _roof_overlay_file_base64(selected_asset_path)
        response_payload = {
            **result,
            "selected_footprint_overlay_url": _signed_roof_asset_url(
                request=request,
                context_id=payload.context_id,
                asset_name=selected_asset_name,
                expires=expires,
                signing_key=signing_key,
            ),
            "selected_footprint_overlay_preview_media_type": "image/jpeg",
            "selected_footprint_overlay_preview_base64": selected_preview_base64,
            "openaiFileResponse": [
                {
                    "name": "roof_measure_overlay.jpg",
                    "mime_type": "image/jpeg",
                    "content": selected_full_size_base64,
                }
            ],
        }
        return RoofMeasureCalculationResponse.model_validate(response_payload)
    except RoofMeasureInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RoofMeasureContextExpiredError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except RoofMeasureContextError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get(
    "/v1/roof-measure/contexts/{context_id}/assets/{asset_name}",
    include_in_schema=False,
    name="download_roof_measure_asset",
)
def download_roof_measure_asset(
    context_id: str,
    asset_name: str,
    expires: int,
    signature: str,
) -> FileResponse:
    signing_key = _artifact_signing_key()
    if not signing_key:
        raise HTTPException(status_code=503, detail="Asset signing is not configured.")
    if expires < int(time.time()):
        raise HTTPException(status_code=410, detail="Roof image link has expired.")
    expected = _sign_roof_asset(context_id, asset_name, expires, signing_key)
    if not hmac.compare_digest(str(signature or ""), expected):
        raise HTTPException(status_code=403, detail="Invalid roof image signature.")
    try:
        path = resolve_roof_measure_asset(
            context_id=context_id,
            asset_name=asset_name,
            artifact_dir=_roof_measure_artifact_dir(),
        )
    except RoofMeasureInputError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RoofMeasureContextExpiredError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except RoofMeasureContextError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        path,
        media_type="image/png",
        headers={"Cache-Control": "private, no-store"},
    )


def _download_openai_action_workbook(file_reference: Any) -> tuple[str, bytes]:
    reference = (
        file_reference.model_dump()
        if hasattr(file_reference, "model_dump")
        else file_reference
    )
    if not isinstance(reference, dict):
        raise HTTPException(
            status_code=422,
            detail=(
                "Attach the edited XLSX workbook to validateEstimateWorkbook; "
                "the action did not receive a downloadable file reference."
            ),
        )
    file_name = safe_filename(str(reference.get("name") or "edited-estimate.xlsx"))
    download_link = str(reference.get("download_link") or "").strip()
    parsed = urlsplit(download_link)
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not hostname
        or not (
            hostname == "files.oaiusercontent.com"
            or hostname.endswith(".files.oaiusercontent.com")
        )
    ):
        raise HTTPException(
            status_code=422,
            detail="The attached workbook did not provide a trusted HTTPS download link.",
        )
    try:
        response = requests.get(
            download_link,
            timeout=(5, 30),
            stream=True,
            allow_redirects=False,
        )
        response.raise_for_status()
        content_length = int(response.headers.get("Content-Length") or 0)
        if content_length > MAX_ACTION_WORKBOOK_BYTES:
            raise HTTPException(status_code=413, detail="The workbook exceeds 10 MB.")
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_ACTION_WORKBOOK_BYTES:
                raise HTTPException(status_code=413, detail="The workbook exceeds 10 MB.")
            chunks.append(chunk)
    except HTTPException:
        raise
    except (requests.RequestException, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail=f"The attached workbook could not be downloaded: {type(exc).__name__}.",
        ) from exc
    if not file_name.lower().endswith(".xlsx"):
        raise HTTPException(status_code=422, detail="Attach an XLSX estimate workbook.")
    return file_name, b"".join(chunks)


def _download_openai_action_bid_package(
    file_references: list[Any],
    destination: Path,
) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    downloaded: list[Path] = []
    total_bytes = 0
    for index, file_reference in enumerate(file_references, start=1):
        reference = (
            file_reference.model_dump()
            if hasattr(file_reference, "model_dump")
            else file_reference
        )
        if not isinstance(reference, dict):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Attach the reviewed bid PDF or ZIP files to "
                    "prepareBidScopeAttachmentContext; a downloadable file reference was missing."
                ),
            )
        file_name = safe_filename(
            str(reference.get("name") or f"bid-package-part-{index}.pdf")
        )
        if Path(file_name).suffix.lower() not in {".pdf", ".zip"}:
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported BidScope attachment {file_name}; attach only PDF or ZIP files.",
            )
        download_link = str(reference.get("download_link") or "").strip()
        parsed = urlsplit(download_link)
        hostname = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "https"
            or not hostname
            or not (
                hostname == "files.oaiusercontent.com"
                or hostname.endswith(".files.oaiusercontent.com")
            )
        ):
            raise HTTPException(
                status_code=422,
                detail=f"The attached BidScope file {file_name} did not provide a trusted HTTPS download link.",
            )
        part_directory = destination / f"part-{index:02d}"
        part_directory.mkdir(parents=True, exist_ok=False)
        target = part_directory / file_name
        try:
            response = requests.get(
                download_link,
                timeout=(5, 180),
                stream=True,
                allow_redirects=False,
            )
            response.raise_for_status()
            content_length = int(response.headers.get("Content-Length") or 0)
            if content_length > MAX_ACTION_BIDSCOPE_FILE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"The BidScope attachment {file_name} exceeds 1 GB.",
                )
            if total_bytes + content_length > MAX_ACTION_BIDSCOPE_TOTAL_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail="The combined BidScope attachments exceed 2 GB.",
                )
            with target.open("wb") as output:
                file_bytes = 0
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    file_bytes += len(chunk)
                    total_bytes += len(chunk)
                    if file_bytes > MAX_ACTION_BIDSCOPE_FILE_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail=f"The BidScope attachment {file_name} exceeds 1 GB.",
                        )
                    if total_bytes > MAX_ACTION_BIDSCOPE_TOTAL_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail="The combined BidScope attachments exceed 2 GB.",
                        )
                    output.write(chunk)
        except HTTPException:
            raise
        except (OSError, requests.RequestException, ValueError) as exc:
            raise HTTPException(
                status_code=503,
                detail=f"The attached BidScope file {file_name} could not be downloaded: {type(exc).__name__}.",
            ) from exc
        downloaded.append(target)
    return downloaded


@app.post(
    "/v1/estimating/workbook-template",
    response_model=EstimateWorkbookTemplateResponse,
    response_model_by_alias=True,
    operation_id="getEstimateWorkbookTemplate",
    summary="Get the default estimate workbook for assistant editing",
    description=(
        "Returns the authoritative roofing, insulation, or flooring XLSX template "
        "as a native action file, plus the template version required for validation."
    ),
)
def estimate_workbook_template(
    request: Request,
    payload: EstimateWorkbookTemplateRequest,
) -> EstimateWorkbookTemplateResponse:
    _require_api_request(request)
    try:
        template_path = estimate_template_path(payload.template_type, base_dir=PROJECT_ROOT)
        profile = discover_estimate_workbook_profile(payload.template_type, template_path)
        content = template_path.read_bytes()
    except EstimateWorkbookUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return EstimateWorkbookTemplateResponse.model_validate(
        {
            "schema_version": "spraytec.estimate_workbook_template.v1",
            "template_type": payload.template_type,
            "template_version": estimate_template_version(template_path),
            "file_name": template_path.name,
            "template_profile": profile.action_summary(),
            "assistant_edit_instruction": (
                "Edit input cells in the attached workbook directly. Preserve all sheets, "
                "formulas, merged cells, formatting, selectors, and lookup tables. Then attach "
                "the edited XLSX to validateEstimateWorkbook."
            ),
            "openaiFileResponse": [
                {
                    "name": template_path.name,
                    "mime_type": XLSX_MEDIA_TYPE,
                    "content": base64.b64encode(content).decode("ascii"),
                }
            ],
            "warnings": [
                "This is an unpriced working copy until it is edited, recalculated, and validated."
            ],
        }
    )


@app.post(
    "/v1/estimating/workbook-validation",
    response_model=EstimateWorkbookValidationResponse,
    response_model_by_alias=True,
    operation_id="validateEstimateWorkbook",
    summary="Validate an assistant-edited estimate workbook",
    description=(
        "Downloads one attached XLSX, verifies the authoritative template formulas and "
        "sheets, recalculates it, checks required totals, and optionally stages a valid "
        "draft in the configured SharePoint folder."
    ),
)
def estimate_workbook_validation(
    request: Request,
    payload: EstimateWorkbookValidationRequest,
) -> EstimateWorkbookValidationResponse:
    _require_api_request(request)
    template_path = estimate_template_path(payload.template_type, base_dir=PROJECT_ROOT)
    current_version = estimate_template_version(template_path)
    if payload.template_version != current_version:
        raise HTTPException(
            status_code=409,
            detail=(
                "The default estimate template changed after this workbook was retrieved. "
                "Fetch a fresh template and reapply the reviewed inputs."
            ),
        )
    original_name, content = _download_openai_action_workbook(
        payload.openai_file_id_refs[0]
    )
    with tempfile.TemporaryDirectory(prefix="estimate-validation-") as temporary:
        timestamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        output_name = f"Assistant Draft - {timestamp} - {safe_filename(original_name)}"
        path = Path(temporary) / output_name
        path.write_bytes(content)
        try:
            validation = validate_ai_edited_workbook(
                path,
                template_type=payload.template_type,
                base_dir=PROJECT_ROOT,
                expected_estimated_sqft=payload.expected_estimated_sqft,
                expected_items=payload.expected_items,
            )
        except EstimateWorkbookInputError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "The submitted workbook could not be validated.",
                    "issues": exc.issues,
                },
            ) from exc
        except EstimateWorkbookUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        sharepoint_url = ""
        warnings = list(validation.warnings)
        file_response: list[dict[str, str]] = []
        if validation.valid:
            recalculated_content = path.read_bytes()
            file_response = [
                {
                    "name": output_name,
                    "mime_type": XLSX_MEDIA_TYPE,
                    "content": base64.b64encode(recalculated_content).decode("ascii"),
                }
            ]
            if payload.save_to_sharepoint:
                try:
                    sharepoint_url = stage_estimate_workbook(path)
                    warnings.append(
                        "The workbook is in the temporary SharePoint estimate staging folder; "
                        "it was not filed in a permanent job folder."
                    )
                except EstimateSharePointStagingUnavailable as exc:
                    warnings.append(str(exc))
        return EstimateWorkbookValidationResponse.model_validate(
            {
                "schema_version": "spraytec.estimate_workbook_validation.v1",
                "template_type": payload.template_type,
                "template_version": current_version,
                "valid": validation.valid,
                "calculated_outputs": validation.calculated_outputs,
                "template_profile": validation.template_profile,
                "issues": validation.issues,
                "warnings": warnings,
                "sharepoint_url": sharepoint_url,
                "openaiFileResponse": file_response,
            }
        )


@app.post(
    "/v1/estimating/workbook",
    response_model=EstimateWorkbookResponse,
    response_model_exclude_none=True,
    status_code=201,
    operation_id="generateEstimateWorkbook",
    summary="Create an estimate workbook after estimator confirmation",
    description=(
        "Profiles the selected roofing, insulation, or flooring template, creates and "
        "recalculates a draft, "
        "validates required cost outputs, and returns a short-lived download "
        "link. This consequential operation requires confirmed=true and does "
        "not call an LLM or upload to SharePoint."
    ),
)
def estimate_workbook(
    request: Request,
    payload: EstimateWorkbookRequest,
) -> EstimateWorkbookResponse:
    _require_api_request(request)
    if payload.confirmed is not True:
        raise HTTPException(
            status_code=409,
            detail=(
                "Explicit estimator confirmation is required before workbook "
                "creation. Set confirmed=true only after approval."
            ),
        )
    if (
        not payload.materials
        and not payload.labor
        and not payload.logistics
        and not payload.adders
    ):
        raise HTTPException(
            status_code=422,
            detail="Provide at least one material, labor item, or adder.",
        )
    signing_key = _artifact_signing_key()
    if not signing_key:
        raise HTTPException(
            status_code=503,
            detail="Estimate artifact signing is not configured.",
        )
    try:
        prepared_payload, planning_warnings = _prepare_workbook_payload(payload)
        artifact = create_estimate_workbook(
            prepared_payload,
            base_dir=PROJECT_ROOT,
        )
    except EstimateWorkbookInputError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": (
                    "Estimate inputs are incomplete for the selected template."
                ),
                "issues": exc.issues,
            },
        ) from exc
    except EstimateWorkbookOutputError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Generated workbook failed calculated-output validation.",
                "issues": exc.issues,
            },
        ) from exc
    except EstimateWorkbookUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    expires = int(time.time()) + _artifact_ttl_seconds()
    signature = _sign_artifact(artifact.artifact_id, expires, signing_key)
    download_origin = _public_download_origin(request, artifact.artifact_id)
    download_url = f"{download_origin}?{urlencode({'expires': expires, 'signature': signature})}"
    return EstimateWorkbookResponse(
        schema_version="spraytec.estimate_workbook.v2",
        artifact_id=artifact.artifact_id,
        file_name=artifact.file_name,
        template_type=payload.template_type,
        download_url=download_url,
        expires_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expires)),
        calculated_outputs=artifact.calculated_outputs,
        template_profile=artifact.template_profile,
        warnings=[
            *planning_warnings,
            "Draft only: estimator review is required before use.",
            "The saved workbook was recalculated and required cost outputs were validated.",
            "The file has not been uploaded to SharePoint.",
        ],
    )


@app.post(
    "/v1/estimating/workbook-options",
    response_model=EstimateWorkbookOptionsResponse,
    response_model_exclude_none=True,
    status_code=201,
    operation_id="generateEstimateWorkbookOptions",
    summary="Create estimate workbooks for multiple approved options",
    description=(
        "Creates one independently recalculated and validated workbook for each "
        "complete option. Use for alternate warranties, areas, systems, or scope "
        "packages. The operation is atomic, requires confirmed=true, does not "
        "call an LLM, and does not upload files to SharePoint."
    ),
)
def estimate_workbook_options(
    request: Request,
    payload: EstimateWorkbookOptionsRequest,
) -> EstimateWorkbookOptionsResponse:
    _require_api_request(request)
    if payload.confirmed is not True:
        raise HTTPException(
            status_code=409,
            detail=(
                "Explicit estimator confirmation is required before option "
                "workbook creation. Set confirmed=true only after every option "
                "has been reviewed."
            ),
        )
    for option in payload.options:
        if (
            not option.materials
            and not option.labor
            and not option.logistics
            and not option.adders
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Option {option.option_label!r} must provide at least one "
                    "material, labor item, logistics item, or adder."
                ),
            )
    signing_key = _artifact_signing_key()
    if not signing_key:
        raise HTTPException(
            status_code=503,
            detail="Estimate artifact signing is not configured.",
        )
    try:
        prepared_options: list[tuple[str, dict[str, Any]]] = []
        planning_warnings: list[str] = []
        for option in payload.options:
            prepared, option_warnings = _prepare_workbook_payload(option)
            prepared.pop("option_label", None)
            prepared_options.append((option.option_label, prepared))
            planning_warnings.extend(
                f"{option.option_label}: {warning}" for warning in option_warnings
            )
        generated = create_estimate_workbook_options(
            prepared_options,
            base_dir=PROJECT_ROOT,
        )
    except EstimateWorkbookInputError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "One or more estimate options are incomplete.",
                "issues": exc.issues,
            },
        ) from exc
    except EstimateWorkbookOutputError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "One or more option workbooks failed output validation.",
                "issues": exc.issues,
            },
        ) from exc
    except EstimateWorkbookUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    expires = int(time.time()) + _artifact_ttl_seconds()
    expires_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expires))
    artifacts: list[EstimateWorkbookOptionArtifact] = []
    for option, (option_label, artifact) in zip(payload.options, generated):
        signature = _sign_artifact(artifact.artifact_id, expires, signing_key)
        download_origin = _public_download_origin(request, artifact.artifact_id)
        download_url = (
            f"{download_origin}?"
            f"{urlencode({'expires': expires, 'signature': signature})}"
        )
        artifacts.append(
            EstimateWorkbookOptionArtifact(
                option_label=option_label,
                artifact_id=artifact.artifact_id,
                file_name=artifact.file_name,
                template_type=option.template_type,
                download_url=download_url,
                expires_at=expires_at,
                calculated_outputs=artifact.calculated_outputs,
                template_profile=artifact.template_profile,
            )
        )
    return EstimateWorkbookOptionsResponse(
        schema_version="spraytec.estimate_workbook_options.v1",
        artifacts=artifacts,
        warnings=[
            *planning_warnings,
            "Draft options only: estimator review is required before use.",
            "Every workbook was recalculated and required cost outputs were validated.",
            "No option workbook has been uploaded to SharePoint.",
        ],
    )
@app.get(
    "/v1/estimating/workbooks/{artifact_id}",
    response_class=FileResponse,
    operation_id="downloadEstimateWorkbook",
    summary="Download a generated estimate using its short-lived signed link",
    include_in_schema=True,
)
def download_estimate_workbook(
    artifact_id: str,
    expires: int,
    signature: str,
) -> FileResponse:
    signing_key = _artifact_signing_key()
    now = int(time.time())
    if (
        not signing_key
        or expires < now
        or expires > now + 3_600
        or not hmac.compare_digest(
            signature,
            _sign_artifact(artifact_id, expires, signing_key),
        )
    ):
        raise HTTPException(status_code=403, detail="Download link is invalid or expired.")
    try:
        path, file_name = resolve_estimate_artifact(
            artifact_id,
            base_dir=PROJECT_ROOT,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Estimate artifact was not found.") from exc
    return FileResponse(
        path,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        filename=file_name,
    )


@app.post(
    "/v1/jobs/search",
    response_model=JobSearchResponse,
    response_model_exclude_none=True,
    operation_id="searchJobs",
    summary="Search the Spray-Tec job board",
    description=(
        "Returns a bounded list of jobs and operational attention signals. "
        "Use the returned stable job_id with getJobContext for detail."
    ),
)
def job_search(
    request: Request,
    payload: JobSearchRequest,
) -> JobSearchResponse:
    _require_api_request(request)
    try:
        result = search_jobs(
            database_url=_database_url(),
            query=payload.query,
            job_ids=payload.job_ids,
            division=payload.division,
            pipeline_status=payload.pipeline_status,
            workflow_status=payload.workflow_status,
            owner=payload.owner,
            job_year=payload.job_year,
            needs_attention=payload.needs_attention,
            limit=payload.limit,
        )
        return JobSearchResponse.model_validate(result)
    except JobIntelligenceUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Job intelligence is unavailable: {type(exc).__name__}.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Job context is unavailable: {type(exc).__name__}.",
        ) from exc


@app.post(
    "/v1/sharepoint/documents/search",
    response_model=SharePointDocumentSearchResponse,
    response_model_exclude_none=True,
    operation_id="searchSharePointDocuments",
    summary="Search indexed SharePoint job documents",
    description=(
        "Searches persisted SharePoint file metadata and extracted document text. "
        "The operation is read-only and bounded to files already discovered by "
        "the SharePoint Job Scanner."
    ),
)
def sharepoint_document_search(
    request: Request,
    payload: SharePointDocumentSearchRequest,
) -> SharePointDocumentSearchResponse:
    _require_api_request(request)
    try:
        result = search_sharepoint_documents(
            database_url=_database_url(),
            query=payload.query,
            job_id=payload.job_id,
            document_type=payload.document_type,
            limit=payload.limit,
        )
        return SharePointDocumentSearchResponse.model_validate(result)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SharePointDocumentUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"SharePoint document search is unavailable: {type(exc).__name__}.",
        ) from exc


@app.post(
    "/v1/sharepoint/documents/fetch",
    response_model=SharePointDocumentFetchResponse,
    response_model_exclude_none=True,
    operation_id="fetchSharePointDocument",
    summary="Fetch readable content for one indexed SharePoint document",
    description=(
        "Returns persisted extracted content for one stable document_id. When "
        "content is missing and allowed, it reuses stored drive/item identifiers "
        "for one bounded read-only Graph download and temporary extraction."
    ),
)
def sharepoint_document_fetch(
    request: Request,
    payload: SharePointDocumentFetchRequest,
) -> SharePointDocumentFetchResponse:
    _require_api_request(request)
    try:
        result = fetch_sharepoint_document(
            database_url=_database_url(),
            document_id=payload.document_id,
            max_chars=payload.max_chars,
            allow_graph_download=payload.allow_graph_download,
        )
        return SharePointDocumentFetchResponse.model_validate(result)
    except SharePointDocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="SharePoint document was not found.") from exc
    except SharePointDocumentUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"SharePoint document fetch is unavailable: {type(exc).__name__}.",
        ) from exc


@app.post(
    "/v1/bidscope/page-selection",
    response_model=BidScopePageSelectionResponse,
    response_model_exclude_none=True,
    operation_id="selectBidScopePages",
    summary="Select bid-package pages for Assistant visual review",
    description=(
        "Accepts a SharePoint PDF, ZIP, or folder link and uses bounded, read-only "
        "Microsoft Graph retrieval. Deterministic keyword seeds and drawing-reference "
        "expansion select a compact source-page PDF for the Assistant to inspect. "
        "The operation does not call an LLM and does not calculate quantities."
    ),
)
def bidscope_page_selection(
    request: Request,
    payload: BidScopePageSelectionRequest,
) -> BidScopePageSelectionResponse:
    _require_api_request(request)
    try:
        result = build_bidscope_review_packet(
            sharepoint_url=payload.sharepoint_url,
            trade_type=payload.trade_type,
            reference_depth=payload.reference_depth,
            max_scan_pages=payload.max_scan_pages,
            max_packet_pages=payload.max_packet_pages,
            artifact_dir=_bidscope_artifact_dir(),
            ttl_seconds=_bidscope_context_ttl_seconds(),
        )
        return BidScopePageSelectionResponse.model_validate(result)
    except BidScopeInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except BidScopeUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"BidScope page selection is unavailable: {type(exc).__name__}.",
        ) from exc


@app.post(
    "/v1/bidscope/measurement-context",
    response_model=BidScopeMeasurementContextResponse,
    response_model_exclude_none=True,
    operation_id="createBidScopeMeasurementContext",
    summary="Prepare confirmed bid pages for tracing",
    description=(
        "Accepts page IDs confirmed from a prior BidScope selection. It preserves "
        "each original vector PDF page, renders a high-resolution tracing image, "
        "and resolves estimator-confirmed or detected drawing scales. It does not "
        "segment regions or calculate quantities."
    ),
)
def bidscope_measurement_context(
    request: Request,
    payload: BidScopeMeasurementContextRequest,
) -> BidScopeMeasurementContextResponse:
    _require_api_request(request)
    try:
        result = create_bidscope_measurement_context(
            context_id=payload.context_id,
            confirmed_pages=[page.model_dump() for page in payload.confirmed_pages],
            render_dpi=payload.render_dpi,
            artifact_dir=_bidscope_artifact_dir(),
            ttl_seconds=_bidscope_context_ttl_seconds(),
        )
        return BidScopeMeasurementContextResponse.model_validate(result)
    except BidScopeContextExpiredError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except BidScopeInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except BidScopeUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"BidScope measurement context is unavailable: {type(exc).__name__}.",
        ) from exc


@app.post(
    "/v1/bidscope/prepare-measurement-context",
    response_model=BidScopeMeasurementContextResponse,
    response_model_exclude_none=True,
    operation_id="prepareBidScopeMeasurementContext",
    summary="Prepare known SharePoint bid sheets for tracing",
    description=(
        "Resolves estimator-confirmed printed sheet IDs from an already-reviewed "
        "SharePoint PDF, ZIP, or folder and creates a tracing context. Use after "
        "native package analysis; it does not repeat scope reasoning or calculate quantities."
    ),
)
def bidscope_prepare_measurement_context(
    request: Request,
    payload: BidScopePrepareMeasurementContextRequest,
) -> BidScopeMeasurementContextResponse:
    _require_api_request(request)
    try:
        result = prepare_bidscope_measurement_context(
            sharepoint_url=payload.sharepoint_url,
            confirmed_pages=[page.model_dump() for page in payload.confirmed_pages],
            trade_type=payload.trade_type,
            max_scan_pages=payload.max_scan_pages,
            render_dpi=payload.render_dpi,
            artifact_dir=_bidscope_artifact_dir(),
            ttl_seconds=_bidscope_context_ttl_seconds(),
        )
        return BidScopeMeasurementContextResponse.model_validate(result)
    except BidScopeInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except BidScopeUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"BidScope sheet preparation is unavailable: {type(exc).__name__}.",
        ) from exc


@app.post(
    "/v1/bidscope/prepare-attachment-context",
    response_model=BidScopeMeasurementContextResponse,
    response_model_exclude_none=True,
    operation_id="prepareBidScopeAttachmentContext",
    summary="Prepare attached bid sheets for tracing",
    description=(
        "Uses the PDF or ZIP package parts already attached in the conversation to "
        "resolve confirmed sheets and create a temporary tracing context. It does not "
        "repeat scope analysis or require SharePoint."
    ),
)
def bidscope_prepare_attachment_context(
    request: Request,
    payload: BidScopePrepareAttachmentContextRequest,
) -> BidScopeMeasurementContextResponse:
    _require_api_request(request)
    try:
        with tempfile.TemporaryDirectory(prefix="bidscope-attachments-") as temporary:
            package_root = Path(temporary)
            _download_openai_action_bid_package(
                payload.openai_file_id_refs,
                package_root,
            )
            inspection = inspect_path_package(package_root)
            if not inspection.candidates:
                detail = "; ".join(inspection.warnings[:3]) or (
                    "No supported PDF or ZIP package parts were attached."
                )
                raise BidScopeUnavailableError(detail)
            result = prepare_bidscope_measurement_context_from_inspection(
                inspection,
                sharepoint_url="",
                confirmed_pages=[page.model_dump() for page in payload.confirmed_pages],
                trade_type=payload.trade_type,
                max_scan_pages=payload.max_scan_pages,
                render_dpi=payload.render_dpi,
                artifact_dir=_bidscope_artifact_dir(),
                ttl_seconds=_bidscope_context_ttl_seconds(),
            )
        return BidScopeMeasurementContextResponse.model_validate(result)
    except HTTPException:
        raise
    except BidScopeInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except BidScopeUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"BidScope attachment preparation is unavailable: {type(exc).__name__}.",
        ) from exc


@app.post(
    "/v1/bidscope/trace-regions",
    response_model=BidScopeRegionTraceResponse,
    response_model_exclude_none=True,
    operation_id="traceBidScopeRegions",
    summary="Trace gross regions and opening deductions",
    description=(
        "Traces confirmed scope and linked window or door deductions with private "
        "SAM2 or corrected polygons. Returns review overlays and gross, deduction, "
        "and net quantities using confirmed drawing scale."
    ),
)
def bidscope_region_trace(
    request: Request,
    payload: BidScopeRegionTraceRequest,
) -> BidScopeRegionTraceResponse:
    _require_api_request(request)
    try:
        result = trace_bidscope_regions(
            measurement_context_id=payload.measurement_context_id,
            regions=[region.model_dump() for region in payload.regions],
            sam2_url=os.getenv("SAM2_SEGMENTATION_URL") or "",
            sam2_api_key=os.getenv("SAM2_API_KEY") or "",
            artifact_dir=_bidscope_artifact_dir(),
            timeout_seconds=float(
                os.getenv("ROOF_MEASURE_SEGMENTATION_TIMEOUT_SECONDS") or "90"
            ),
            inference_max_side=payload.inference_max_side,
        )
        return BidScopeRegionTraceResponse.model_validate(result)
    except BidScopeContextExpiredError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except BidScopeInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except BidScopeUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"BidScope region tracing is unavailable: {type(exc).__name__}.",
        ) from exc


@app.get(
    "/v1/jobs/{job_id}/context",
    response_model=JobContextResponse,
    response_model_exclude_none=True,
    operation_id="getJobContext",
    summary="Retrieve complete operational context for one job",
    description=(
        "Returns read-only job-board, workflow, schedule, tracking, document, "
        "and bounded office-activity evidence for an authoritative job_id."
    ),
)
def job_context(
    request: Request,
    job_id: str,
) -> JobContextResponse:
    _require_api_request(request)
    try:
        result = get_job_context(
            job_id,
            database_url=_database_url(),
        )
        return JobContextResponse.model_validate(result)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Job was not found.") from exc
    except JobIntelligenceUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Job intelligence is unavailable: {type(exc).__name__}.",
        ) from exc


@app.post(
    "/v1/warranties/list",
    response_model=WarrantyListResponse,
    response_model_exclude_none=True,
    operation_id="getWarrantyList",
    summary="Search the cleaned master list of issued and reported warranties",
    description=(
        "Returns the deduplicated warranty master with terms, duration, start and end dates, "
        "customer follow-up contacts, project identity, SharePoint job links, issued-document links, "
        "historical-list provenance, reliability classification, and explicit review flags. This operation is read-only."
    ),
)
def warranty_list(
    request: Request,
    payload: WarrantyListRequest,
) -> WarrantyListResponse:
    _require_api_request(request)
    try:
        result = get_warranty_list(
            database_url=_database_url(),
            query=payload.query,
            division=payload.division,
            evidence_status=payload.evidence_status,
            expiring_after=payload.expiring_after,
            expiring_before=payload.expiring_before,
            needs_review=payload.needs_review,
            has_contact=payload.has_contact,
            reliable_only=payload.reliable_only,
            limit=payload.limit,
        )
        return WarrantyListResponse.model_validate(result)
    except JobIntelligenceUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Warranty list is unavailable: {type(exc).__name__}.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Warranty list is unavailable: {type(exc).__name__}.",
        ) from exc


@app.post(
    "/v1/jobs/warranties",
    response_model=WarrantySummaryResponse,
    response_model_exclude_none=True,
    operation_id="getWarrantySummary",
    summary="Retrieve issued, reported, and proposed warranty intelligence",
    description=(
        "Returns source-backed warranty type, provider, coverage, duration, "
        "start-date provenance, expiration, conflicts, candidate job matches, cleanup tasks, and document links. "
        "Legacy reported records and proposed terms remain distinct from issued warranties. This operation is read-only."
    ),
)
def warranty_summary(
    request: Request,
    payload: WarrantySummaryRequest,
) -> WarrantySummaryResponse:
    _require_api_request(request)
    try:
        result = get_warranty_summary(
            database_url=_database_url(),
            job_ids=payload.job_ids,
            job_year=payload.job_year,
            division=payload.division,
            warranty_status=payload.warranty_status,
            expiring_after=payload.expiring_after,
            expiring_before=payload.expiring_before,
            needs_review=payload.needs_review,
            limit=payload.limit,
        )
        return WarrantySummaryResponse.model_validate(result)
    except JobIntelligenceUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Warranty intelligence is unavailable: {type(exc).__name__}.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Warranty summary is unavailable: {type(exc).__name__}.",
        ) from exc


@app.post(
    "/v1/sales/pipeline",
    response_model=SalesIntelligenceResponse,
    response_model_exclude_none=True,
    operation_id="getSalesPipeline",
    summary="Summarize the current sales pipeline",
    description=(
        "Returns bounded pipeline totals, stage and owner rollups, top "
        "opportunities, attention items, and source links. Current workflow "
        "assignments are authoritative; SharePoint proposal/estimate editors are "
        "explicitly labeled inferred fallbacks. This operation is read-only."
    ),
)
def sales_pipeline(
    request: Request,
    payload: SalesPipelineRequest,
) -> SalesIntelligenceResponse:
    _require_api_request(request)
    try:
        result = get_sales_pipeline(
            database_url=_database_url(),
            division=payload.division,
            owner=payload.owner,
            job_year=payload.job_year,
            pipeline_statuses=payload.pipeline_statuses,
            include_completed=payload.include_completed,
            limit=payload.limit,
        )
        return SalesIntelligenceResponse.model_validate(result)
    except JobIntelligenceUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Sales intelligence is unavailable: {type(exc).__name__}.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Sales pipeline is unavailable: {type(exc).__name__}.",
        ) from exc


@app.post(
    "/v1/sales/follow-ups",
    response_model=SalesIntelligenceResponse,
    response_model_exclude_none=True,
    operation_id="getSalesFollowUps",
    summary="Retrieve the proposed-job sales follow-up queue",
    description=(
        "Returns a bounded, prioritized follow-up queue with ownership, due-state, "
        "data-quality issues, opportunity value, and source links. This operation is read-only."
    ),
)
def sales_followups(
    request: Request,
    payload: SalesFollowupRequest,
) -> SalesIntelligenceResponse:
    _require_api_request(request)
    try:
        result = get_sales_followups(
            database_url=_database_url(),
            division=payload.division,
            owner=payload.owner,
            job_year=payload.job_year,
            followup_status=payload.followup_status,
            overdue_only=payload.overdue_only,
            unassigned_only=payload.unassigned_only,
            limit=payload.limit,
        )
        return SalesIntelligenceResponse.model_validate(result)
    except JobIntelligenceUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Sales intelligence is unavailable: {type(exc).__name__}.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Sales follow-ups are unavailable: {type(exc).__name__}.",
        ) from exc


@app.post(
    "/v1/accounting/summary",
    response_model=QuickBooksAccountingResponse,
    response_model_exclude_none=True,
    operation_id="getQuickBooksAccountingSummary",
    summary="Summarize synchronized QuickBooks accounting activity",
    description=(
        "Returns read-only open receivables, overdue invoices, recent payments, "
        "and synchronization freshness from Spray-Tec's operational accounting read model."
    ),
)
def quickbooks_accounting_summary(
    request: Request,
    payload: QuickBooksAccountingSummaryRequest,
) -> QuickBooksAccountingResponse:
    _require_api_request(request)
    try:
        return QuickBooksAccountingResponse.model_validate(get_accounting_summary(
            database_url=_database_url(),
            customer_query=payload.customer_query,
            limit=payload.limit,
        ))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Accounting summary is unavailable: {type(exc).__name__}.") from exc


@app.post(
    "/v1/accounting/customer-context",
    response_model=QuickBooksAccountingResponse,
    response_model_exclude_none=True,
    operation_id="getQuickBooksCustomerContext",
    summary="Retrieve synchronized QuickBooks customer context",
    description=(
        "Returns read-only customer, estimate, invoice, credit, and payment evidence "
        "for a customer name or reviewed Spray-Tec job link."
    ),
)
def quickbooks_customer_context(
    request: Request,
    payload: QuickBooksCustomerContextRequest,
) -> QuickBooksAccountingResponse:
    _require_api_request(request)
    try:
        return QuickBooksAccountingResponse.model_validate(get_customer_context(
            database_url=_database_url(),
            customer_query=payload.customer_query,
            job_id=payload.job_id,
            limit=payload.limit,
        ))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Customer accounting context is unavailable: {type(exc).__name__}.") from exc


@app.post(
    "/v1/accounting/exceptions",
    response_model=QuickBooksAccountingResponse,
    response_model_exclude_none=True,
    operation_id="getQuickBooksAccountingExceptions",
    summary="Retrieve synchronized QuickBooks accounting exceptions",
    description=(
        "Returns a bounded read-only list of overdue receivables and accounting-data "
        "freshness warnings for operational follow-up."
    ),
)
def quickbooks_accounting_exceptions(
    request: Request,
    payload: QuickBooksAccountingSummaryRequest,
) -> QuickBooksAccountingResponse:
    _require_api_request(request)
    try:
        return QuickBooksAccountingResponse.model_validate(get_accounting_exceptions(
            database_url=_database_url(),
            limit=payload.limit,
        ))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Accounting exceptions are unavailable: {type(exc).__name__}.") from exc


@app.post("/v1/integrations/quickbooks/authorize", include_in_schema=False)
def quickbooks_authorize(request: Request) -> dict[str, str]:
    _require_api_request(request)
    database_url = _database_url()
    if not database_url:
        raise HTTPException(status_code=503, detail="Database configuration is unavailable.")
    engine = create_resilient_engine(database_url)
    try:
        return {
            "company": str(os.getenv("QUICKBOOKS_EXPECTED_COMPANY_NAME") or "Spray-Tec Inc."),
            "status": "awaiting_administrator_authorization",
            "authorization_url": create_admin_authorization(engine),
        }
    except QuickBooksConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        engine.dispose()


@app.get("/v1/integrations/quickbooks/callback", include_in_schema=False, response_class=HTMLResponse)
def quickbooks_callback(
    background_tasks: BackgroundTasks,
    state: str = "",
    code: str = "",
    realmId: str = "",
    error: str = "",
) -> HTMLResponse:
    if error:
        return HTMLResponse("<h1>QuickBooks authorization was not completed.</h1><p>You may close this window.</p>", status_code=400)
    database_url = _database_url()
    if not database_url:
        raise HTTPException(status_code=503, detail="Database configuration is unavailable.")
    engine = create_resilient_engine(database_url)
    try:
        result = complete_admin_authorization(engine, state=state, code=code, realm_id=realmId)
        company = html.escape(result["company_name"])
        background_tasks.add_task(_run_quickbooks_initial_sync, database_url, realmId)
        return HTMLResponse(
            f"<h1>{company} is connected.</h1><p>Authorization succeeded and the initial read-only synchronization has started. You may close this window.</p>"
        )
    except (QuickBooksStateError, QuickBooksCompanyMismatchError, QuickBooksConfigurationError) as exc:
        return HTMLResponse(f"<h1>QuickBooks authorization failed.</h1><p>{str(exc)}</p>", status_code=400)
    finally:
        engine.dispose()


@app.post("/v1/integrations/quickbooks/sync", include_in_schema=False)
def quickbooks_sync(request: Request, full: bool = False) -> dict[str, Any]:
    _require_api_request(request)
    database_url = _database_url()
    if not database_url:
        raise HTTPException(status_code=503, detail="Database configuration is unavailable.")
    engine = create_resilient_engine(database_url)
    try:
        return sync_quickbooks(engine, full=full)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"QuickBooks synchronization failed: {type(exc).__name__}.") from exc
    finally:
        engine.dispose()


def _run_quickbooks_initial_sync(database_url: str, realm_id: str) -> None:
    engine = create_resilient_engine(database_url)
    try:
        sync_quickbooks(engine, realm_id=realm_id, full=True)
    finally:
        engine.dispose()


@app.post(
    "/v1/operations/backlog",
    response_model=OperationsIntelligenceResponse,
    response_model_exclude_none=True,
    operation_id="getOperationsBacklog",
    summary="Summarize contracted backlog readiness",
    description=(
        "Returns current contracted backlog totals, readiness and division "
        "rollups, prioritized blockers, and source folders. This operation is read-only."
    ),
)
def operations_backlog(
    request: Request,
    payload: OperationsBacklogRequest,
) -> OperationsIntelligenceResponse:
    _require_api_request(request)
    try:
        result = get_operations_backlog(
            database_url=_database_url(),
            division=payload.division,
            job_year=payload.job_year,
            readiness_statuses=payload.readiness_statuses,
            unscheduled_only=payload.unscheduled_only,
            needs_attention=payload.needs_attention,
            include_completed=payload.include_completed,
            limit=payload.limit,
        )
        return OperationsIntelligenceResponse.model_validate(result)
    except JobIntelligenceUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Operations intelligence is unavailable: {type(exc).__name__}.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Operations backlog is unavailable: {type(exc).__name__}.",
        ) from exc


@app.post(
    "/v1/operations/schedule",
    response_model=OperationsIntelligenceResponse,
    response_model_exclude_none=True,
    operation_id="getOperationsSchedule",
    summary="Retrieve schedule workload and production risks",
    description=(
        "Returns a bounded schedule window or the active production-risk queue "
        "with crew, schedule health, project health, and tracking signals. "
        "This operation is read-only."
    ),
)
def operations_schedule(
    request: Request,
    payload: OperationsScheduleRequest,
) -> OperationsIntelligenceResponse:
    _require_api_request(request)
    try:
        result = get_operations_schedule(
            database_url=_database_url(),
            division=payload.division,
            crew_leader=payload.crew_leader,
            job_year=payload.job_year,
            start_date=payload.start_date,
            end_date=payload.end_date,
            risk_only=payload.risk_only,
            include_unscheduled=payload.include_unscheduled,
            include_completed=payload.include_completed,
            limit=payload.limit,
        )
        return OperationsIntelligenceResponse.model_validate(result)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except JobIntelligenceUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Operations intelligence is unavailable: {type(exc).__name__}.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Operations schedule is unavailable: {type(exc).__name__}.",
        ) from exc


@app.post(
    "/v1/office/activity",
    response_model=OfficeActivityResponse,
    response_model_exclude_none=True,
    operation_id="getOfficeActivity",
    summary="Summarize office timesheet activity",
    description=(
        "Returns complete bounded-date rollups by employee, work code, project "
        "label, and day, plus recent source entries and timesheet links. "
        "Activity-only touches remain distinct from captured hours. This operation is read-only."
    ),
)
def office_activity(
    request: Request,
    payload: OfficeActivityRequest,
) -> OfficeActivityResponse:
    _require_api_request(request)
    try:
        result = get_office_activity(
            database_url=_database_url(),
            employee=payload.employee,
            code=payload.code,
            project_query=payload.project_query,
            start_date=payload.start_date,
            end_date=payload.end_date,
            timed_only=payload.timed_only,
            limit=payload.limit,
        )
        return OfficeActivityResponse.model_validate(result)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except JobIntelligenceUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Office activity is unavailable: {type(exc).__name__}.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Office timesheet intelligence is unavailable: {type(exc).__name__}.",
        ) from exc


@app.post(
    "/v1/office/job-progress",
    response_model=OfficeJobProgressResponse,
    response_model_exclude_none=True,
    operation_id="getOfficeJobProgress",
    summary="Summarize office work progress by project and qualified job link",
    description=(
        "Returns read-only office activity rollups by project label, including "
        "captured hours, milestones, next actions, stalled-work signals, and "
        "explicitly qualified authoritative or inferred job links. Progress "
        "means activity evidence, not percent complete."
    ),
)
def office_job_progress(
    request: Request,
    payload: OfficeJobProgressRequest,
) -> OfficeJobProgressResponse:
    _require_api_request(request)
    try:
        result = get_office_job_progress(
            database_url=_database_url(),
            division=payload.division,
            employee=payload.employee,
            project_query=payload.project_query,
            lookback_days=payload.lookback_days,
            stalled_after_days=payload.stalled_after_days,
            stalled_only=payload.stalled_only,
            include_unmatched=payload.include_unmatched,
            include_closed=payload.include_closed,
            limit=payload.limit,
        )
        return OfficeJobProgressResponse.model_validate(result)
    except JobIntelligenceUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Office job progress is unavailable: {type(exc).__name__}.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Office job progress is unavailable: {type(exc).__name__}.",
        ) from exc


@app.post(
    "/v1/operations/production-budget-health",
    response_model=ProductionBudgetHealthResponse,
    response_model_exclude_none=True,
    operation_id="getProductionBudgetHealth",
    summary="Compare tracked production usage with estimate-derived budgets",
    description=(
        "Returns read-only production-plan cost proxies by job and budget bucket. "
        "Tracked quantities and hours are valued with estimate-derived rates. "
        "The results are not accounting actual costs, percent complete, or "
        "realized profitability."
    ),
)
def production_budget_health(
    request: Request,
    payload: ProductionBudgetHealthRequest,
) -> ProductionBudgetHealthResponse:
    _require_api_request(request)
    try:
        result = get_production_budget_health(
            database_url=_database_url(),
            job_ids=payload.job_ids,
            division=payload.division,
            job_year=payload.job_year,
            over_plan_only=payload.over_plan_only,
            include_no_actuals=payload.include_no_actuals,
            include_completed=payload.include_completed,
            limit=payload.limit,
        )
        return ProductionBudgetHealthResponse.model_validate(result)
    except JobIntelligenceUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Production budget intelligence is unavailable: {type(exc).__name__}.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Production budget health is unavailable: {type(exc).__name__}.",
        ) from exc


@app.post(
    "/v1/reporting/chart-data",
    response_model=ChartDatasetResponse,
    response_model_exclude_none=True,
    operation_id="getChartDataset",
    summary="Retrieve a chart-ready business dataset",
    description=(
        "Reuses the authoritative sales, operations, office, or production-budget "
        "service and returns a normalized chart specification with bounded, "
        "deterministically ordered rows plus a versioned display and staging "
        "contract. "
        "The operation is read-only and does not generate narrative conclusions."
    ),
)
def reporting_chart_data(
    request: Request,
    payload: ChartDatasetRequest,
) -> ChartDatasetResponse:
    _require_api_request(request)
    try:
        result = _chart_source_result(payload)
        return ChartDatasetResponse.model_validate(
            build_chart_dataset(payload.dataset, result)
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except JobIntelligenceUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Chart data is unavailable: {type(exc).__name__}.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Chart dataset generation failed: {type(exc).__name__}.",
        ) from exc


@app.post(
    "/v1/reporting/chart-data.csv",
    response_class=Response,
    operation_id="downloadChartDatasetCsv",
    summary="Download a chart-ready business dataset as CSV",
    description=(
        "Returns the same normalized chart dataset as a CSV file for ChatGPT "
        "Data Analysis. The operation is read-only."
    ),
    responses={
        200: {
            "description": "Chart-ready CSV file",
            "content": {"text/csv": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
def reporting_chart_data_csv(
    request: Request,
    payload: ChartDatasetRequest,
) -> Response:
    _require_api_request(request)
    try:
        dataset = build_chart_dataset(payload.dataset, _chart_source_result(payload))
        safe_name = payload.dataset.replace("_", "-") + ".csv"
        return Response(
            content=chart_dataset_csv(dataset),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except JobIntelligenceUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Chart data is unavailable: {type(exc).__name__}.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Chart CSV generation failed: {type(exc).__name__}.",
        ) from exc


def _chart_source_result(payload: ChartDatasetRequest) -> dict[str, Any]:
    dataset = payload.dataset
    if dataset in HISTORY_DATASETS:
        return get_chart_history(
            dataset,
            database_url=_database_url(),
            start_date=payload.start_date,
            end_date=payload.end_date,
        )
    if dataset.startswith("sales_pipeline_"):
        return get_sales_pipeline(
            database_url=_database_url(),
            division=payload.division,
            owner=payload.owner,
            job_year=payload.job_year,
            pipeline_statuses=payload.pipeline_statuses,
            include_completed=payload.include_completed,
            limit=payload.limit,
        )
    if dataset.startswith("operations_backlog_"):
        return get_operations_backlog(
            database_url=_database_url(),
            division=payload.division,
            job_year=payload.job_year,
            readiness_statuses=payload.readiness_statuses,
            unscheduled_only=payload.unscheduled_only,
            needs_attention=payload.needs_attention,
            include_completed=payload.include_completed,
            limit=payload.limit,
        )
    if dataset.startswith("operations_schedule_"):
        gantt = dataset == "operations_schedule_gantt"
        return get_operations_schedule(
            database_url=_database_url(),
            division=payload.division,
            crew_leader=payload.crew_leader,
            job_year=payload.job_year,
            start_date=payload.start_date,
            end_date=payload.end_date,
            risk_only=payload.risk_only,
            include_unscheduled=payload.include_unscheduled,
            include_completed=payload.include_completed,
            limit=payload.gantt_limit if gantt else payload.limit,
            max_records=125 if gantt else 25,
        )
    if dataset.startswith("office_activity_"):
        return get_office_activity(
            database_url=_database_url(),
            employee=payload.employee,
            code=payload.code,
            project_query=payload.project_query,
            start_date=payload.start_date,
            end_date=payload.end_date,
            timed_only=False,
            limit=payload.limit,
        )
    if dataset == "office_job_progress":
        return get_office_job_progress(
            database_url=_database_url(),
            division=payload.division,
            employee=payload.employee,
            project_query=payload.project_query,
            lookback_days=payload.lookback_days,
            stalled_after_days=payload.stalled_after_days,
            stalled_only=payload.stalled_only,
            include_unmatched=payload.include_unmatched,
            include_closed=payload.include_closed,
            limit=payload.limit,
        )
    return get_production_budget_health(
        database_url=_database_url(),
        job_ids=payload.job_ids,
        division=payload.division,
        job_year=payload.job_year,
        over_plan_only=payload.over_plan_only,
        include_no_actuals=payload.include_no_actuals,
        include_completed=payload.include_completed,
        limit=payload.limit,
    )


def _require_api_request(request: Request) -> None:
    _require_authenticated_request(
        authorization=request.headers.get("Authorization"),
        api_key_header=request.headers.get("X-API-Key"),
        principal=request.headers.get("X-MS-CLIENT-PRINCIPAL"),
        principal_id=request.headers.get("X-MS-CLIENT-PRINCIPAL-ID"),
    )


def _database_url() -> str | None:
    return os.getenv("NEON_DATABASE_URL") or os.getenv("DATABASE_URL")


def _authentication_required() -> bool:
    return bool(_configured_api_key()) or _entra_authentication_required()


def _entra_authentication_required() -> bool:
    return str(os.getenv("ESTIMATOR_API_REQUIRE_AUTH") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _configured_api_key() -> str:
    return str(os.getenv("ESTIMATOR_API_KEY") or "").strip()


def _artifact_signing_key() -> str:
    return str(
        os.getenv("ESTIMATOR_ARTIFACT_SIGNING_KEY")
        or os.getenv("ESTIMATOR_API_KEY")
        or ""
    ).strip()


def _artifact_ttl_seconds() -> int:
    try:
        configured = int(os.getenv("ESTIMATOR_ARTIFACT_TTL_SECONDS") or "900")
    except ValueError:
        configured = 900
    return min(max(configured, 60), 3_600)


def _roof_measure_artifact_dir() -> Path:
    configured = str(os.getenv("ROOF_MEASURE_CONTEXT_DIR") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    estimator_dir = Path(
        os.getenv("ESTIMATOR_API_ARTIFACT_DIR")
        or "/tmp/spraytec-estimator-artifacts"
    )
    return estimator_dir.expanduser().resolve() / "roof-measure"


def _bidscope_artifact_dir() -> Path:
    configured = str(os.getenv("BIDSCOPE_CONTEXT_DIR") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    estimator_dir = Path(
        os.getenv("ESTIMATOR_API_ARTIFACT_DIR")
        or "/tmp/spraytec-estimator-artifacts"
    )
    return estimator_dir.expanduser().resolve() / "bidscope"


def _bidscope_context_ttl_seconds() -> int:
    try:
        configured = int(os.getenv("BIDSCOPE_CONTEXT_TTL_SECONDS") or "3600")
    except ValueError:
        configured = 3600
    return min(max(configured, 300), 14_400)


def _roof_overlay_preview_base64(path: Path) -> str:
    """Return a bounded self-contained preview for action clients without URL access."""
    with Image.open(path) as source:
        image = source.convert("RGB")
        image.thumbnail((512, 512), Image.Resampling.LANCZOS)
        buffer = BytesIO()
        image.save(
            buffer,
            format="JPEG",
            quality=58,
            optimize=True,
            progressive=True,
        )
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _roof_overlay_file_base64(path: Path) -> str:
    """Return a clear, bounded full-size overlay for the GPT action attachment."""
    with Image.open(path) as source:
        image = source.convert("RGB")
        image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
        buffer = BytesIO()
        image.save(
            buffer,
            format="JPEG",
            quality=86,
            optimize=True,
            progressive=True,
        )
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _planning_snapshot_ttl_seconds() -> int:
    try:
        configured = int(os.getenv("ESTIMATOR_PLANNING_SNAPSHOT_TTL_SECONDS") or "900")
    except ValueError:
        configured = 900
    return min(max(configured, 60), 3_600)


def _sign_artifact(artifact_id: str, expires: int, signing_key: str) -> str:
    message = f"{artifact_id}:{expires}".encode("utf-8")
    return hmac.new(
        signing_key.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()


def _sign_roof_asset(
    context_id: str,
    asset_name: str,
    expires: int,
    signing_key: str,
) -> str:
    message = f"roof:{context_id}:{asset_name}:{expires}".encode("utf-8")
    return hmac.new(
        signing_key.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()


def _signed_roof_asset_url(
    *,
    request: Request,
    context_id: str,
    asset_name: str,
    expires: int,
    signing_key: str,
) -> str:
    configured = str(os.getenv("ESTIMATOR_API_PUBLIC_BASE_URL") or "").strip()
    if configured:
        parsed = urlsplit(configured)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise HTTPException(
                status_code=503,
                detail=(
                    "ESTIMATOR_API_PUBLIC_BASE_URL must be an HTTPS origin without "
                    "a path, query, or fragment."
                ),
            )
        origin = (
            f"{configured.rstrip('/')}"
            f"/v1/roof-measure/contexts/{context_id}/assets/{asset_name}"
        )
    else:
        origin = str(
            request.url_for(
                "download_roof_measure_asset",
                context_id=context_id,
                asset_name=asset_name,
            )
        )
    signature = _sign_roof_asset(context_id, asset_name, expires, signing_key)
    return f"{origin}?{urlencode({'expires': expires, 'signature': signature})}"


def _require_authenticated_request(
    *,
    authorization: str | None,
    api_key_header: str | None,
    principal: str | None,
    principal_id: str | None,
) -> None:
    """Apply the configured API-key and Azure Easy Auth gates.

    The API key supports private Custom GPT testing. The Microsoft principal
    headers remain an optional deployment gate for Azure Easy Auth.
    """

    configured_api_key = _configured_api_key()
    if configured_api_key:
        supplied_api_key = _supplied_api_key(
            authorization=authorization,
            api_key_header=api_key_header,
        )
        if not hmac.compare_digest(supplied_api_key, configured_api_key):
            raise HTTPException(
                status_code=401,
                detail="Valid estimator API key required.",
            )

    if _entra_authentication_required() and not any(
        str(value or "").strip() for value in (principal, principal_id)
    ):
        raise HTTPException(
            status_code=401,
            detail="Authenticated Microsoft Entra principal required.",
        )


def _supplied_api_key(
    *,
    authorization: str | None,
    api_key_header: str | None,
) -> str:
    authorization_text = str(authorization or "").strip()
    scheme, separator, credential = authorization_text.partition(" ")
    if separator and scheme.lower() == "bearer":
        return credential.strip()
    return str(api_key_header or "").strip()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "services.estimator_api.server:app",
        host=os.getenv("ESTIMATOR_API_HOST") or "127.0.0.1",
        port=int(os.getenv("ESTIMATOR_API_PORT") or "8770"),
        reload=False,
    )
