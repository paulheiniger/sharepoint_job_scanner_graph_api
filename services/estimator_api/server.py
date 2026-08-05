from __future__ import annotations

import base64
import hashlib
import hmac
from io import BytesIO
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from PIL import Image

from jobscan.business.chart_history_service import HISTORY_DATASETS, get_chart_history
from jobscan.business.chart_service import build_chart_dataset, chart_dataset_csv

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
from jobscan.business.warranty_service import get_warranty_summary
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
    resolve_estimate_artifact,
)
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
    EstimateContextRequest,
    EstimateContextResponse,
    EstimateWorkbookRequest,
    EstimateWorkbookResponse,
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
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_project_env(PROJECT_ROOT / ".env")
app = FastAPI(
    title="Spray-Tec Business Intelligence API",
    description=(
        "Estimator evidence, controlled workbook generation, and read-only "
        "operational intelligence for conversational agents."
    ),
    version="0.20.0",
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
  <p class="updated"><strong>Effective and last updated:</strong> August 4, 2026</p>
  <p>This policy describes how Spray-Tec, Inc. handles information sent to the Spray-Tec Business Assistant API through a custom GPT or another authorized client.</p>

  <h2>Information processed</h2>
  <p>The service may process job notes, customer or project names, site addresses, measurements, scope details, estimating decisions, and operational questions supplied by a user. When a user provides an image or document to ChatGPT, the service generally receives the structured facts or notes that the GPT sends to the API, rather than the original file. At a user's request, the service may also retrieve metadata and readable content from SharePoint job documents already indexed by Spray-Tec's SharePoint Job Scanner.</p>

  <h2>How information is used</h2>
  <p>Information is used only to retrieve business evidence and source documents, prepare summaries and charts, support estimate decisions, validate estimate inputs, and generate draft estimate workbooks requested by an authorized user. SharePoint access is read-only. Drafts require human review and are not automatically uploaded to SharePoint.</p>

  <h2>Storage and retention</h2>
  <p>Context, reporting, and SharePoint document requests are processed to produce the requested response and are not intentionally added to a separate marketing or advertising database. A SharePoint source file downloaded for on-demand text extraction is held only in temporary service storage for that request. Generated workbooks and roof-measure context images are stored temporarily by the API so the requesting user can retrieve them; signed links normally expire after 15 minutes. Limited technical logs and temporary service files may be retained as needed for security, troubleshooting, reliability, and service operation.</p>

  <h2>Sharing and service providers</h2>
  <p>Spray-Tec does not sell information submitted to this service. Information may be processed by service providers used to operate it, including OpenAI for the ChatGPT experience, Microsoft Azure for API hosting, Microsoft Graph and SharePoint for authorized source-document retrieval, Mapbox for address geocoding and satellite imagery, and public mapping or LiDAR services used to retrieve building evidence. Those providers handle information under their own terms and privacy commitments. Information may also be disclosed when required by law or to protect the security and integrity of the service.</p>

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
        "openapi": "/openapi.json",
    }


@app.api_route(
    "/privacy",
    methods=["GET", "HEAD"],
    response_class=HTMLResponse,
    include_in_schema=False,
)
def privacy_policy() -> HTMLResponse:
    """Return the public privacy policy required by shared GPT Actions."""
    return HTMLResponse(PRIVACY_POLICY_HTML)


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
    summary="Create reviewable SAM2 roof-mask candidates",
    description=(
        "Uses explicitly reviewed footprint IDs from a prior roof context to "
        "prompt the configured private SAM2 service. When available, Kentucky "
        "LiDAR height-above-ground blocks score the inside/outside boundary band "
        "and can produce a guarded connected high-band alternative. Returns up "
        "to three ranked candidate "
        "overlays. No OpenAI API or fallback rectangle is used, and a candidate "
        "must be confirmed before calculation."
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
                    "name": "roof_sam2_candidates.jpg",
                    "mime_type": "image/jpeg",
                    "content": preview_base64,
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
        "Deterministically calculates plan-view area and perimeter from one or "
        "more footprint candidates or custom pixel polygons in a prior roof "
        "context. An optional pitch produces a slope-adjusted surface area. "
        "The result always requires estimator verification and no AI service is called."
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
                    "content": selected_preview_base64,
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
