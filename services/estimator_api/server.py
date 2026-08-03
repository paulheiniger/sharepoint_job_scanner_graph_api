from __future__ import annotations

import hashlib
import hmac
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response

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
from jobscan.estimator.context_service import build_copilot_estimator_context
from jobscan.estimator.workbook_service import (
    EstimateWorkbookInputError,
    EstimateWorkbookOutputError,
    EstimateWorkbookUnavailableError,
    create_estimate_workbook,
    create_estimate_workbook_options,
    resolve_estimate_artifact,
)
from jobscan.env import load_project_env
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
    SalesFollowupRequest,
    SalesIntelligenceResponse,
    SalesPipelineRequest,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_project_env(PROJECT_ROOT / ".env")
app = FastAPI(
    title="Spray-Tec Business Intelligence API",
    description=(
        "Estimator evidence, controlled workbook generation, and read-only "
        "operational intelligence for conversational agents."
    ),
    version="0.13.8",
)


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
        "openapi": "/openapi.json",
    }


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
        artifact = create_estimate_workbook(
            payload.model_dump(),
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
        generated = create_estimate_workbook_options(
            [
                (
                    option.option_label,
                    option.model_dump(exclude={"option_label"}),
                )
                for option in payload.options
            ],
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
    "/v1/sales/pipeline",
    response_model=SalesIntelligenceResponse,
    response_model_exclude_none=True,
    operation_id="getSalesPipeline",
    summary="Summarize the current sales pipeline",
    description=(
        "Returns bounded pipeline totals, stage and owner rollups, top "
        "opportunities, attention items, and source links. This operation is read-only."
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
        "service and returns a normalized chart specification with bounded rows. "
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
    if dataset.startswith("sales_pipeline_"):
        return get_sales_pipeline(
            database_url=_database_url(),
            division=payload.division,
            owner=payload.owner,
            pipeline_statuses=payload.pipeline_statuses,
            include_completed=payload.include_completed,
            limit=payload.limit,
        )
    if dataset.startswith("operations_backlog_"):
        return get_operations_backlog(
            database_url=_database_url(),
            division=payload.division,
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


def _sign_artifact(artifact_id: str, expires: int, signing_key: str) -> str:
    message = f"{artifact_id}:{expires}".encode("utf-8")
    return hmac.new(
        signing_key.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()


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
