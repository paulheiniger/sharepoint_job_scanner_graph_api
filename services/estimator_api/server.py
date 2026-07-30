from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from jobscan.estimator.context_service import build_copilot_estimator_context
from .schemas import EstimateContextRequest, EstimateContextResponse


PROJECT_ROOT = Path(__file__).resolve().parents[2]
app = FastAPI(
    title="Spray-Tec Estimator API",
    description=(
        "Read-only estimator evidence and deterministic services for "
        "Microsoft 365 Copilot agents."
    ),
    version="0.1.0",
)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "spraytec-estimator-api",
        "version": app.version,
        "authentication_required": _authentication_required(),
    }


@app.post(
    "/v1/estimating/context",
    response_model=EstimateContextResponse,
    response_model_exclude_none=True,
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
    _require_authenticated_principal(
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
            reference_job_ids=payload.reference_job_ids,
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


def _database_url() -> str | None:
    return os.getenv("NEON_DATABASE_URL") or os.getenv("DATABASE_URL")


def _authentication_required() -> bool:
    return str(os.getenv("ESTIMATOR_API_REQUIRE_AUTH") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _require_authenticated_principal(
    *,
    principal: str | None,
    principal_id: str | None,
) -> None:
    """Require an identity header injected by a trusted Azure Easy Auth proxy."""

    if _authentication_required() and not any(
        str(value or "").strip() for value in (principal, principal_id)
    ):
        raise HTTPException(
            status_code=401,
            detail="Authenticated Microsoft Entra principal required.",
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "services.estimator_api.server:app",
        host=os.getenv("ESTIMATOR_API_HOST") or "127.0.0.1",
        port=int(os.getenv("ESTIMATOR_API_PORT") or "8770"),
        reload=False,
    )
