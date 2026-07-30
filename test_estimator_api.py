from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from services.estimator_api.server import app


client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "spraytec-estimator-api"


def test_context_requires_input() -> None:
    response = client.post("/v1/estimating/context", json={})
    assert response.status_code == 422


def test_context_returns_bounded_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        "services.estimator_api.server.build_copilot_estimator_context",
        lambda **_kwargs: {
            "schema_version": "spraytec.copilot_estimator_context.v1",
            "scope": {"template_type": "insulation"},
            "context": {"historical_evidence_packet": {}},
            "warnings": [],
            "retrieval_summary": {"matched_comparable_count": 0},
        },
    )
    response = client.post(
        "/v1/estimating/context",
        json={
            "raw_notes": "30x40 metal building",
            "template_type": "insulation",
        },
    )
    assert response.status_code == 200
    assert response.json()["scope"]["template_type"] == "insulation"


def test_context_can_require_entra_principal(monkeypatch) -> None:
    monkeypatch.setenv("ESTIMATOR_API_REQUIRE_AUTH", "true")
    response = client.post(
        "/v1/estimating/context",
        json={"raw_notes": "30x40 metal building"},
    )
    assert response.status_code == 401


def test_context_accepts_easy_auth_principal(monkeypatch) -> None:
    monkeypatch.setenv("ESTIMATOR_API_REQUIRE_AUTH", "true")
    monkeypatch.setattr(
        "services.estimator_api.server.build_copilot_estimator_context",
        lambda **_kwargs: {
            "schema_version": "spraytec.copilot_estimator_context.v1",
            "scope": {"template_type": "insulation"},
            "context": {},
            "warnings": [],
            "retrieval_summary": {},
        },
    )
    response = client.post(
        "/v1/estimating/context",
        json={"raw_notes": "30x40 metal building"},
        headers={"X-MS-CLIENT-PRINCIPAL": "trusted-easy-auth-envelope"},
    )
    assert response.status_code == 200


def test_context_openapi_has_stable_operation_id() -> None:
    response = client.get("/openapi.json")
    operation = response.json()["paths"]["/v1/estimating/context"]["post"]
    assert operation["operationId"] == "getEstimatorContext"
    assert "parameters" not in operation
