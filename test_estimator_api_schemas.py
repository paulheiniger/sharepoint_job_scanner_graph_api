from __future__ import annotations

import pytest
from pydantic import ValidationError

from services.estimator_api.schemas import (
    EstimateContextRequest,
    EstimateContextResponse,
)


def test_context_request_defaults() -> None:
    request = EstimateContextRequest(raw_notes="30x40 metal building")
    assert request.scope == {}
    assert request.reference_job_ids == []
    assert request.include_source_metadata is False


def test_context_request_caps_reference_jobs() -> None:
    with pytest.raises(ValidationError):
        EstimateContextRequest(
            raw_notes="Job",
            reference_job_ids=[f"JOB-{index}" for index in range(11)],
        )


def test_context_request_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        EstimateContextRequest(
            raw_notes="Job",
            unexpected_action="write-workbook",
        )


def test_context_response_accepts_bounded_contract() -> None:
    response = EstimateContextResponse.model_validate(
        {
            "schema_version": "spraytec.copilot_estimator_context.v1",
            "scope": {"template_type": "roofing"},
            "context": {"historical_evidence_packet": {}},
            "warnings": [],
            "retrieval_summary": {"matched_comparable_count": 0},
        }
    )
    assert response.scope["template_type"] == "roofing"
    assert response.source_metadata is None
