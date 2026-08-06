from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from services.estimator_api.server import app
from services.estimator_api.generate_openapi import build_action_openapi
from services.estimator_api.schemas import EstimateWorkbookRequest
from jobscan.estimator.planning_snapshot import create_planning_snapshot
from jobscan.estimator.workbook_service import (
    EstimateWorkbookArtifact,
    EstimateWorkbookInputError,
    EstimateWorkbookValidation,
    estimate_template_path,
    estimate_template_version,
)


client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_estimator_api_auth(monkeypatch):
    monkeypatch.delenv("ESTIMATOR_API_KEY", raising=False)
    monkeypatch.delenv("ESTIMATOR_API_REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("ESTIMATOR_API_PUBLIC_BASE_URL", raising=False)


def context_payload() -> dict:
    return {
        "schema_version": "spraytec.copilot_estimator_context.v1",
        "scope": {"template_type": "insulation"},
        "template_type": "insulation",
        "route_mileage": {},
        "matched_comparables": [],
        "decision_evidence": [],
        "historical_material_usage": [],
        "historical_labor_performance": [],
        "historical_assemblies": [],
        "matched_scope_pattern": {},
        "validated_relationships": [],
        "approved_memories": [],
        "pricing_candidates": [],
        "product_guidance": [],
        "foam_yield_history": [],
        "decision_concepts": [],
        "calculation_requirements": [],
        "source_links": [],
        "warnings": [],
        "retrieval_summary": {"matched_comparable_count": 0},
    }


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "spraytec-estimator-api"


def test_service_root_supports_deployment_connectivity_checks() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "service": "spraytec-estimator-api",
        "version": app.version,
        "health": "/health",
        "privacy": "/privacy",
        "openapi": "/openapi.json",
        "action_openapi": "/openapi-actions.json",
    }


def test_action_openapi_serves_checked_in_gpt_contract() -> None:
    response = client.get("/openapi-actions.json")
    checked_in = json.loads(
        Path("services/estimator_api/openapi.json").read_text(encoding="utf-8")
    )

    assert response.status_code == 200
    assert response.json() == checked_in
    assert "/health" not in response.json()["paths"]
    assert "/openapi-actions.json" not in client.get("/openapi.json").json()["paths"]


def test_privacy_policy_is_public_html_and_excluded_from_openapi(monkeypatch) -> None:
    monkeypatch.setenv("ESTIMATOR_API_KEY", "test-secret")

    response = client.get("/privacy")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Spray-Tec Business Assistant Privacy Policy" in response.text
    assert "info@spray-tec.com" in response.text
    assert client.head("/privacy").status_code == 200
    assert "/privacy" not in client.get("/openapi.json").json()["paths"]


def test_context_requires_input() -> None:
    response = client.post("/v1/estimating/context", json={})
    assert response.status_code == 422


def test_context_returns_bounded_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        "services.estimator_api.server.build_copilot_estimator_context",
        lambda **_kwargs: context_payload(),
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
    assert "context" not in response.json()


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
        lambda **_kwargs: context_payload(),
    )
    response = client.post(
        "/v1/estimating/context",
        json={"raw_notes": "30x40 metal building"},
        headers={"X-MS-CLIENT-PRINCIPAL": "trusted-easy-auth-envelope"},
    )
    assert response.status_code == 200


def test_context_rejects_missing_or_invalid_api_key(monkeypatch) -> None:
    monkeypatch.setenv("ESTIMATOR_API_KEY", "test-secret")

    missing = client.post(
        "/v1/estimating/context",
        json={"raw_notes": "30x40 metal building"},
    )
    invalid = client.post(
        "/v1/estimating/context",
        json={"raw_notes": "30x40 metal building"},
        headers={"Authorization": "Bearer wrong-secret"},
    )

    assert missing.status_code == 401
    assert invalid.status_code == 401


def test_context_accepts_bearer_api_key(monkeypatch) -> None:
    monkeypatch.setenv("ESTIMATOR_API_KEY", "test-secret")
    monkeypatch.setattr(
        "services.estimator_api.server.build_copilot_estimator_context",
        lambda **_kwargs: context_payload(),
    )

    response = client.post(
        "/v1/estimating/context",
        json={"raw_notes": "30x40 metal building"},
        headers={"Authorization": "Bearer test-secret"},
    )

    assert response.status_code == 200


def test_context_accepts_custom_api_key_header(monkeypatch) -> None:
    monkeypatch.setenv("ESTIMATOR_API_KEY", "test-secret")
    monkeypatch.setattr(
        "services.estimator_api.server.build_copilot_estimator_context",
        lambda **_kwargs: context_payload(),
    )

    response = client.post(
        "/v1/estimating/context",
        json={"raw_notes": "30x40 metal building"},
        headers={"X-API-Key": "test-secret"},
    )

    assert response.status_code == 200


def test_context_returns_signed_planning_snapshot_for_structured_roofing(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ESTIMATOR_API_KEY", "test-secret")
    payload = context_payload()
    payload.update(
        {
            "scope": {
                "template_type": "roofing",
                "declared_total_area_sqft": 5000,
                "area_scopes": [
                    {
                        "scope_id": "recover",
                        "scope_role": "exclusive_area",
                        "area_sqft": 5000,
                    }
                ],
            },
            "template_type": "roofing",
            "labor_plan_guidance": [
                {
                    "category": "labor_prep",
                    "recommended_days": 1,
                    "recommended_crew_size": 5,
                }
            ],
            "logistics_guidance": [],
        }
    )
    monkeypatch.setattr(
        "services.estimator_api.server.build_copilot_estimator_context",
        lambda **_kwargs: payload,
    )

    response = client.post(
        "/v1/estimating/context",
        json={
            "raw_notes": "Prepare and recoat 5,000 square feet",
            "template_type": "roofing",
            "site_address": "830 South 1st Street, Louisville, KY 40203",
        },
        headers={"Authorization": "Bearer test-secret"},
    )

    assert response.status_code == 200
    assert response.json()["planning_snapshot_token"]


def test_context_openapi_has_stable_operation_id() -> None:
    response = client.get("/openapi.json")
    assert response.json()["servers"] == [
        {
            "url": (
                "https://spraytec-business-api.icysand-5925ab36."
                "eastus2.azurecontainerapps.io"
            )
        }
    ]
    operation = response.json()["paths"]["/v1/estimating/context"]["post"]
    assert operation["operationId"] == "getEstimatorContext"
    assert "parameters" not in operation


def test_roof_measure_context_is_authenticated_and_returns_signed_images(
    monkeypatch,
    tmp_path: Path,
) -> None:
    context_id = "f" * 32
    satellite = tmp_path / "satellite.png"
    satellite.write_bytes(b"png bytes")
    monkeypatch.setenv("ESTIMATOR_API_KEY", "test-secret")
    monkeypatch.setattr(
        "services.estimator_api.server.create_roof_measure_context",
        lambda **_kwargs: {
            "schema_version": "spraytec.roof_measure_context.v1",
            "context_id": context_id,
            "created_at": 1,
            "expires_at": 9_999_999_999,
            "address": "830 South 1st Street, Louisville, KY 40203",
            "job_id": "JOB-1",
            "site_name": "Example School",
            "site_type": "school",
            "latitude": 38.0,
            "longitude": -84.0,
            "zoom": 17.5,
            "image_width": 1280,
            "image_height": 1280,
            "pixels_per_foot": 1.5,
            "footprint_candidates": [],
            "candidate_groups": [],
            "site_resolution_status": "review_required",
            "recommended_candidate_group_id": "",
            "site_resolution_reason": "No complete group was found.",
            "requires_site_confirmation": True,
            "lidar_coverage": {
                "available": False,
                "asset_url": "https://lidar.test/private-source.copc.laz",
            },
            "attributions": ["Imagery from Mapbox."],
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        "services.estimator_api.server.resolve_roof_measure_asset",
        lambda **_kwargs: satellite,
    )
    monkeypatch.setattr(
        "services.estimator_api.server._roof_overlay_preview_base64",
        lambda _path: "encoded-preview",
    )

    unauthorized = client.post(
        "/v1/roof-measure/context",
        json={"address": "830 South 1st Street, Louisville, KY 40203"},
    )
    response = client.post(
        "/v1/roof-measure/context",
        json={"address": "830 South 1st Street, Louisville, KY 40203"},
        headers={"Authorization": "Bearer test-secret"},
    )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    body = response.json()
    assert body["context_id"] == context_id
    assert body["satellite_image_url"].startswith(
        f"http://testserver/v1/roof-measure/contexts/{context_id}/assets/satellite.png?"
    )
    assert body["footprint_overlay_preview_base64"] == "encoded-preview"
    assert body["site_name"] == "Example School"
    assert "asset_url" not in body["lidar_coverage"]
    asset = client.get(body["satellite_image_url"])
    assert asset.status_code == 200
    assert asset.content == b"png bytes"
    tampered = client.get(
        body["footprint_overlay_url"].replace("signature=", "signature=bad")
    )
    assert tampered.status_code == 403


def test_roof_measure_calculation_returns_review_required_contract(monkeypatch) -> None:
    selected_overlay = Path("/tmp/selected-footprints-test.png")
    monkeypatch.setenv("ESTIMATOR_API_KEY", "test-secret")
    monkeypatch.setattr(
        "services.estimator_api.server.resolve_roof_measure_asset",
        lambda **_kwargs: selected_overlay,
    )
    monkeypatch.setattr(
        "services.estimator_api.server._roof_overlay_preview_base64",
        lambda _path: "selected-preview",
    )
    monkeypatch.setattr(
        "services.estimator_api.server._roof_overlay_file_base64",
        lambda _path: "selected-full-size",
    )
    monkeypatch.setattr(
        "services.estimator_api.server.calculate_roof_measurement",
        lambda **_kwargs: {
            "schema_version": "spraytec.roof_measure_calculation.v1",
            "context_id": "a" * 32,
            "measurement_basis": "address_calibrated_satellite_plan_view",
            "total_plan_area_sqft": 5000,
            "total_perimeter_ft": 300,
            "sections": [
                {
                    "section_id": "fp-01",
                    "source": "footprint",
                    "plan_area_sqft": 5000,
                    "perimeter_ft": 300,
                }
            ],
            "selected_overlay_asset_name": "selected-footprints-0123456789abcdef.png",
            "review_status": "requires_estimator_verification",
            "assumptions": [],
            "warnings": ["Verify the roof boundary."],
        },
    )

    response = client.post(
        "/v1/roof-measure/calculate",
        json={
            "context_id": "a" * 32,
            "selected_footprint_ids": ["fp-01"],
        },
        headers={"Authorization": "Bearer test-secret"},
    )

    assert response.status_code == 200
    assert response.json()["total_plan_area_sqft"] == 5000
    assert response.json()["review_status"] == "requires_estimator_verification"
    assert response.json()["selected_footprint_overlay_preview_base64"] == (
        "selected-preview"
    )
    assert response.json()["openaiFileResponse"] == [
        {
            "name": "roof_measure_overlay.jpg",
            "mime_type": "image/jpeg",
            "content": "selected-full-size",
        }
    ]


def test_roof_measure_segmentation_returns_reviewable_candidates(monkeypatch) -> None:
    candidate_overlay = Path("/tmp/sam2-candidates-test.png")
    monkeypatch.setenv("ESTIMATOR_API_KEY", "test-secret")
    monkeypatch.setenv("SAM2_SEGMENTATION_URL", "http://sam2.test/segment")
    monkeypatch.setattr(
        "services.estimator_api.server.segment_roof_measure_context",
        lambda **_kwargs: {
            "schema_version": "spraytec.roof_measure_sam2_candidates.v2",
            "context_id": "a" * 32,
            "selected_footprint_ids": ["fp-01"],
            "recommended_candidate_id": "sam2-0123456789abcdef",
            "requires_candidate_confirmation": False,
            "evaluated_candidate_count": 3,
            "candidates": [
                {
                    "candidate_id": "sam2-0123456789abcdef",
                    "rank": 1,
                    "provider_rank": 2,
                    "model_name": "sam2_remote",
                    "model_version": "test",
                    "model_score": 0.88,
                    "selection_score": 0.91,
                    "footprint_overlap": 0.95,
                    "footprint_coverage": 0.97,
                    "area_ratio_to_footprint": 1.02,
                    "plan_area_sqft": 5100,
                    "perimeter_ft": 310,
                    "section_count": 1,
                }
            ],
            "candidate_overlay_asset_name": "sam2-candidates-0123456789abcdef.png",
            "model_name": "sam2_remote",
            "model_version": "test",
            "source_view": "footprint_fitted",
            "source_zoom": 20.25,
            "source_pixels_per_foot": 7.5,
            "warnings": ["Confirm a candidate before calculation."],
        },
    )
    monkeypatch.setattr(
        "services.estimator_api.server.resolve_roof_measure_asset",
        lambda **_kwargs: candidate_overlay,
    )
    monkeypatch.setattr(
        "services.estimator_api.server._roof_overlay_preview_base64",
        lambda _path: "candidate-preview",
    )
    monkeypatch.setattr(
        "services.estimator_api.server._roof_overlay_file_base64",
        lambda _path: "full-size-candidate",
    )

    unauthorized = client.post(
        "/v1/roof-measure/segment",
        json={"context_id": "a" * 32, "selected_footprint_ids": ["fp-01"]},
    )
    response = client.post(
        "/v1/roof-measure/segment",
        json={"context_id": "a" * 32, "selected_footprint_ids": ["fp-01"]},
        headers={"Authorization": "Bearer test-secret"},
    )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert response.json()["requires_candidate_confirmation"] is False
    assert response.json()["evaluated_candidate_count"] == 3
    assert response.json()["candidates"][0]["provider_rank"] == 2
    assert response.json()["openaiFileResponse"][0]["content"] == (
        "full-size-candidate"
    )
    assert response.json()["openaiFileResponse"][0]["name"] == (
        "roof_sam2_best_candidate.jpg"
    )


def workbook_request_payload(*, confirmed: bool = True) -> dict:
    return {
        "confirmed": confirmed,
        "header": {
            "job_name": "Action Roof",
            "job_type": "Roof restoration",
            "estimated_sqft": 5000,
        },
        "materials": [
            {
                "category": "coating",
                "item": "Gaco Silicone",
                "selector_code": 11,
                "area_sqft": 5000,
                "gal_per_100_sqft": 1.5,
                "unit_price": 36,
            }
        ],
    }


def workbook_options_request_payload(*, confirmed: bool = True) -> dict:
    base = workbook_request_payload()
    base.pop("confirmed")
    return {
        "confirmed": confirmed,
        "options": [
            {**base, "option_label": "10-year warranty"},
            {**base, "option_label": "15-year warranty"},
        ],
    }


def test_workbook_request_applies_standard_commercial_percentages() -> None:
    roofing = EstimateWorkbookRequest.model_validate(workbook_request_payload())
    assert roofing.pricing.overhead_pct == 35
    assert roofing.pricing.profit_pct == 15

    insulation_payload = workbook_request_payload()
    insulation_payload["template_type"] = "insulation"
    insulation = EstimateWorkbookRequest.model_validate(insulation_payload)
    assert insulation.pricing.overhead_pct == 30
    assert insulation.pricing.profit_pct == 10


@pytest.mark.parametrize("template_type", ["roofing", "insulation", "flooring"])
def test_workbook_template_route_returns_native_default_file(
    monkeypatch,
    template_type: str,
) -> None:
    monkeypatch.setenv("ESTIMATOR_API_KEY", "test-secret")
    response = client.post(
        "/v1/estimating/workbook-template",
        json={"template_type": template_type},
        headers={"Authorization": "Bearer test-secret"},
    )

    assert response.status_code == 200
    body = response.json()
    template_path = estimate_template_path(template_type, base_dir=Path.cwd())
    assert body["template_version"] == estimate_template_version(template_path)
    assert body["openaiFileResponse"][0]["content"] == __import__("base64").b64encode(
        template_path.read_bytes()
    ).decode("ascii")


def test_workbook_validation_returns_recalculated_file_and_sharepoint_link(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ESTIMATOR_API_KEY", "test-secret")
    template_path = estimate_template_path("roofing", base_dir=Path.cwd())
    monkeypatch.setattr(
        "services.estimator_api.server._download_openai_action_workbook",
        lambda _reference: ("Estimate - Test.xlsx", template_path.read_bytes()),
    )
    monkeypatch.setattr(
        "services.estimator_api.server.validate_ai_edited_workbook",
        lambda *_args, **_kwargs: EstimateWorkbookValidation(
            valid=True,
            calculated_outputs={"worksheet_price": 12345.0},
            issues=[],
            warnings=[],
            template_profile={"profile_version": "test"},
        ),
    )
    monkeypatch.setattr(
        "services.estimator_api.server.stage_estimate_workbook",
        lambda _path: "https://spraytec.sharepoint.com/staged-estimate",
    )
    response = client.post(
        "/v1/estimating/workbook-validation",
        json={
            "template_type": "roofing",
            "template_version": estimate_template_version(template_path),
            "expected_estimated_sqft": 5000,
            "expected_items": ["roofing foam"],
            "save_to_sharepoint": True,
            "openaiFileIdRefs": [
                {
                    "name": "Estimate - Test.xlsx",
                    "mime_type": (
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    ),
                    "download_link": "https://files.oaiusercontent.com/test.xlsx",
                }
            ],
        },
        headers={"Authorization": "Bearer test-secret"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["sharepoint_url"].endswith("/staged-estimate")
    assert body["calculated_outputs"]["worksheet_price"] == 12345
    assert len(body["openaiFileResponse"]) == 1


def test_workbook_route_requires_confirmation(monkeypatch) -> None:
    monkeypatch.setenv("ESTIMATOR_API_KEY", "test-secret")

    response = client.post(
        "/v1/estimating/workbook",
        json=workbook_request_payload(confirmed=False),
        headers={"Authorization": "Bearer test-secret"},
    )

    assert response.status_code == 409
    assert "confirmation" in response.json()["detail"].lower()


def test_workbook_route_creates_signed_download(monkeypatch, tmp_path: Path) -> None:
    artifact_id = "a" * 32
    stored_path = tmp_path / f"{artifact_id}__Estimate - Action_Roof.xlsx"
    stored_path.write_bytes(b"test workbook bytes")
    monkeypatch.setenv("ESTIMATOR_API_KEY", "test-secret")
    monkeypatch.setenv("ESTIMATOR_API_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setattr(
        "services.estimator_api.server.create_estimate_workbook",
        lambda *_args, **_kwargs: EstimateWorkbookArtifact(
            artifact_id=artifact_id,
            file_name="Estimate - Action_Roof.xlsx",
            path=stored_path,
            calculated_outputs={"worksheet_price": 12500.0},
            template_profile={"profile_version": "test-profile"},
        ),
    )

    response = client.post(
        "/v1/estimating/workbook",
        json=workbook_request_payload(),
        headers={"Authorization": "Bearer test-secret"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["artifact_id"] == artifact_id
    assert body["file_name"] == "Estimate - Action_Roof.xlsx"
    assert body["calculated_outputs"]["worksheet_price"] == 12500
    assert body["template_profile"]["profile_version"] == "test-profile"
    download = client.get(body["download_url"])
    assert download.status_code == 200
    assert download.content == b"test workbook bytes"

    tampered = client.get(body["download_url"].replace("signature=", "signature=bad"))
    assert tampered.status_code == 403


def test_workbook_route_reapplies_api_labor_plan_before_generation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    artifact_id = "c" * 32
    stored_path = tmp_path / f"{artifact_id}__Estimate.xlsx"
    stored_path.write_bytes(b"xlsx")
    captured: dict = {}
    monkeypatch.setenv("ESTIMATOR_API_KEY", "test-secret")
    monkeypatch.setenv("ESTIMATOR_API_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setattr(
        "services.estimator_api.server.build_copilot_estimator_context",
        lambda **_kwargs: {
            "labor_plan_guidance": [
                {
                    "category": "labor_prep",
                    "activity": "Roof preparation",
                    "recommended_days": 0.52,
                    "recommended_crew_size": 5,
                    "recommended_total_hours": 26.2,
                    "calibration_status": "calibrated_candidate",
                }
            ],
            "logistics_guidance": [
                {
                    "category": "truck_expense",
                    "include": True,
                    "recommended_trip_count": 6,
                    "round_trip_miles": 62,
                }
            ],
        },
    )

    def fake_create(payload, **_kwargs):
        captured.update(payload)
        return EstimateWorkbookArtifact(
            artifact_id=artifact_id,
            file_name="Estimate.xlsx",
            path=stored_path,
            calculated_outputs={"worksheet_price": 1.0},
            template_profile={},
        )

    monkeypatch.setattr(
        "services.estimator_api.server.create_estimate_workbook",
        fake_create,
    )
    payload = workbook_request_payload()
    payload.update(
        {
            "structured_scope": {
                "declared_total_area_sqft": 5000,
                "area_scopes": [
                    {
                        "scope_id": "recover",
                        "scope_role": "exclusive_area",
                        "area_sqft": 5000,
                        "action": "Prepare and recoat existing roof",
                    }
                ],
            },
            "labor": [
                {"task": "labor_full_repair", "days": 2, "crew_size": 5},
                {"task": "labor_prep", "days": 3, "crew_size": 5},
            ],
            "logistics": [
                {
                    "category": "truck_expense",
                    "item": "Production truck mileage",
                    "trip_count": 9,
                    "round_trip_miles": 62,
                }
            ],
        }
    )
    payload["materials"].append(
        {
            "category": "roofing_foam",
            "item": "Gaco Roof 2.7",
            "area_sqft": 5000,
            "thickness_inches": 1.5,
            "yield_factor": 2700,
            "unit_price": 2100,
        }
    )

    response = client.post(
        "/v1/estimating/workbook",
        json=payload,
        headers={"Authorization": "Bearer test-secret"},
    )

    assert response.status_code == 201
    labor = {row["task"]: row for row in captured["labor"]}
    assert "labor_full_repair" not in labor
    assert labor["labor_prep"]["days"] == 0.52
    logistics = {row["category"]: row for row in captured["logistics"]}
    assert logistics["truck_expense"]["trip_count"] == 6
    foam = next(
        row for row in captured["materials"] if row["category"] == "roofing_foam"
    )
    assert foam["unit_price"] == 2.1
    assert foam["price_per_set"] == 2100
    assert any(
        "API labor recommendations were applied" in warning
        for warning in response.json()["warnings"]
    )


def test_workbook_route_reuses_matching_planning_snapshot(
    monkeypatch,
    tmp_path: Path,
) -> None:
    artifact_id = "d" * 32
    stored_path = tmp_path / f"{artifact_id}__Estimate.xlsx"
    stored_path.write_bytes(b"xlsx")
    captured: dict = {}
    monkeypatch.setenv("ESTIMATOR_API_KEY", "test-secret")
    monkeypatch.setenv("ESTIMATOR_API_ARTIFACT_DIR", str(tmp_path))
    structured_scope = {
        "template_type": "roofing",
        "declared_total_area_sqft": 5000,
        "area_scopes": [
            {
                "scope_id": "recover",
                "scope_role": "exclusive_area",
                "area_sqft": 5000,
            }
        ],
    }
    token = create_planning_snapshot(
        scope=structured_scope,
        site_address="830 South 1st Street, Louisville, KY 40203",
        labor_plan_guidance=[
            {
                "category": "labor_prep",
                "recommended_days": 0.52,
                "recommended_crew_size": 5,
                "recommended_total_hours": 26.2,
            }
        ],
        logistics_guidance=[
            {
                "category": "truck_expense",
                "include": True,
                "recommended_trip_count": 6,
                "round_trip_miles": 62,
            }
        ],
        signing_key="test-secret",
    )

    def retrieval_must_not_run(**_kwargs):
        raise AssertionError("labor retrieval should be skipped for a valid snapshot")

    def fake_create(payload, **_kwargs):
        captured.update(payload)
        return EstimateWorkbookArtifact(
            artifact_id=artifact_id,
            file_name="Estimate.xlsx",
            path=stored_path,
            calculated_outputs={"worksheet_price": 1.0},
            template_profile={},
        )

    monkeypatch.setattr(
        "services.estimator_api.server.build_copilot_estimator_context",
        retrieval_must_not_run,
    )
    monkeypatch.setattr(
        "services.estimator_api.server.create_estimate_workbook",
        fake_create,
    )
    payload = workbook_request_payload()
    payload.update(
        {
            "structured_scope": structured_scope,
            "planning_snapshot_token": token,
            "header": {
                **payload["header"],
                "site_address": "830 South 1st Street",
                "city_state_zip": "Louisville, KY 40203",
            },
            "labor": [{"task": "labor_prep", "days": 3, "crew_size": 5}],
            "logistics": [
                {
                    "category": "truck_expense",
                    "item": "Production truck mileage",
                    "trip_count": 9,
                    "round_trip_miles": 62,
                }
            ],
        }
    )

    response = client.post(
        "/v1/estimating/workbook",
        json=payload,
        headers={"Authorization": "Bearer test-secret"},
    )

    assert response.status_code == 201
    assert captured["labor"][0]["days"] == 0.52
    assert captured["logistics"][0]["trip_count"] == 6
    assert any(
        "Reused the signed planning snapshot" in warning
        for warning in response.json()["warnings"]
    )


def test_workbook_route_falls_back_when_planning_snapshot_is_invalid(
    monkeypatch,
    tmp_path: Path,
) -> None:
    artifact_id = "e" * 32
    stored_path = tmp_path / f"{artifact_id}__Estimate.xlsx"
    stored_path.write_bytes(b"xlsx")
    calls = {"context": 0}
    monkeypatch.setenv("ESTIMATOR_API_KEY", "test-secret")
    monkeypatch.setenv("ESTIMATOR_API_ARTIFACT_DIR", str(tmp_path))

    def fake_context(**_kwargs):
        calls["context"] += 1
        return {"labor_plan_guidance": [], "logistics_guidance": []}

    monkeypatch.setattr(
        "services.estimator_api.server.build_copilot_estimator_context",
        fake_context,
    )
    monkeypatch.setattr(
        "services.estimator_api.server.create_estimate_workbook",
        lambda *_args, **_kwargs: EstimateWorkbookArtifact(
            artifact_id=artifact_id,
            file_name="Estimate.xlsx",
            path=stored_path,
            calculated_outputs={},
            template_profile={},
        ),
    )
    payload = workbook_request_payload()
    payload.update(
        {
            "structured_scope": {
                "declared_total_area_sqft": 5000,
                "area_scopes": [
                    {
                        "scope_id": "recover",
                        "scope_role": "exclusive_area",
                        "area_sqft": 5000,
                    }
                ],
            },
            "planning_snapshot_token": "invalid-token",
        }
    )

    response = client.post(
        "/v1/estimating/workbook",
        json=payload,
        headers={"Authorization": "Bearer test-secret"},
    )

    assert response.status_code == 201
    assert calls["context"] == 1
    assert any(
        "labor guidance was refreshed" in warning
        for warning in response.json()["warnings"]
    )


def test_workbook_route_uses_configured_https_public_origin(
    monkeypatch,
    tmp_path: Path,
) -> None:
    artifact_id = "b" * 32
    stored_path = tmp_path / f"{artifact_id}__Estimate.xlsx"
    stored_path.write_bytes(b"xlsx")
    monkeypatch.setenv("ESTIMATOR_API_KEY", "test-secret")
    monkeypatch.setenv("ESTIMATOR_API_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setenv(
        "ESTIMATOR_API_PUBLIC_BASE_URL",
        "https://spraytec-api.example",
    )
    monkeypatch.setattr(
        "services.estimator_api.server.create_estimate_workbook",
        lambda *_args, **_kwargs: EstimateWorkbookArtifact(
            artifact_id=artifact_id,
            file_name="Estimate.xlsx",
            path=stored_path,
            calculated_outputs={"worksheet_price": 1.0},
            template_profile={},
        ),
    )

    response = client.post(
        "/v1/estimating/workbook",
        json=workbook_request_payload(),
        headers={"Authorization": "Bearer test-secret"},
    )

    assert response.status_code == 201
    assert response.json()["download_url"].startswith(
        f"https://spraytec-api.example/v1/estimating/workbooks/{artifact_id}?"
    )


def test_workbook_route_returns_actionable_input_issues(monkeypatch) -> None:
    monkeypatch.setenv("ESTIMATOR_API_KEY", "test-secret")
    monkeypatch.setattr(
        "services.estimator_api.server.create_estimate_workbook",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            EstimateWorkbookInputError(
                ["Sales/inspection travel must be explicitly included or excluded."]
            )
        ),
    )

    response = client.post(
        "/v1/estimating/workbook",
        json=workbook_request_payload(),
        headers={"Authorization": "Bearer test-secret"},
    )

    assert response.status_code == 422
    assert "Sales/inspection" in response.json()["detail"]["issues"][0]


def test_workbook_options_route_requires_confirmation(monkeypatch) -> None:
    monkeypatch.setenv("ESTIMATOR_API_KEY", "test-secret")

    response = client.post(
        "/v1/estimating/workbook-options",
        json=workbook_options_request_payload(confirmed=False),
        headers={"Authorization": "Bearer test-secret"},
    )

    assert response.status_code == 409
    assert "every option" in response.json()["detail"].lower()


def test_workbook_options_route_returns_signed_downloads(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ESTIMATOR_API_KEY", "test-secret")
    monkeypatch.setenv("ESTIMATOR_API_ARTIFACT_DIR", str(tmp_path))
    artifacts = []
    for index, label in enumerate(("10-year warranty", "15-year warranty")):
        artifact_id = str(index + 1) * 32
        file_name = f"Estimate - Action_Roof - {label.replace(' ', '_')}.xlsx"
        path = tmp_path / f"{artifact_id}__{file_name}"
        path.write_bytes(label.encode("utf-8"))
        artifacts.append(
            (
                label,
                EstimateWorkbookArtifact(
                    artifact_id=artifact_id,
                    file_name=file_name,
                    path=path,
                    calculated_outputs={"worksheet_price": 10000 + index * 1000},
                    template_profile={"profile_version": "test-profile"},
                ),
            )
        )
    monkeypatch.setattr(
        "services.estimator_api.server.create_estimate_workbook_options",
        lambda *_args, **_kwargs: artifacts,
    )

    response = client.post(
        "/v1/estimating/workbook-options",
        json=workbook_options_request_payload(),
        headers={"Authorization": "Bearer test-secret"},
    )

    assert response.status_code == 201
    body = response.json()
    assert [item["option_label"] for item in body["artifacts"]] == [
        "10-year warranty",
        "15-year warranty",
    ]
    assert body["artifacts"][1]["calculated_outputs"]["worksheet_price"] == 11000
    for item in body["artifacts"]:
        download = client.get(item["download_url"])
        assert download.status_code == 200


def test_openapi_exposes_expected_operations() -> None:
    spec = client.get("/openapi.json").json()
    action_operations = {
        operation["operationId"]
        for path in spec["paths"].values()
        for operation in path.values()
    }
    assert action_operations == {
        "getJobContext",
        "getEstimatorContext",
        "getRoofMeasureContext",
        "segmentRoofMeasureContext",
        "calculateRoofMeasurement",
        "getEstimateWorkbookTemplate",
        "validateEstimateWorkbook",
        "generateEstimateWorkbook",
        "generateEstimateWorkbookOptions",
        "downloadEstimateWorkbook",
        "downloadChartDatasetCsv",
        "getChartDataset",
        "getOfficeActivity",
        "getOfficeJobProgress",
        "getOperationsBacklog",
        "getOperationsSchedule",
        "getProductionBudgetHealth",
            "getSalesFollowUps",
            "getSalesPipeline",
        "getWarrantySummary",
        "getWarrantyList",
        "searchSharePointDocuments",
            "fetchSharePointDocument",
            "selectBidScopePages",
            "createBidScopeMeasurementContext",
            "prepareBidScopeMeasurementContext",
            "traceBidScopeRegions",
                "health_health_get",
        "searchJobs",
    }


def test_job_search_route_is_authenticated_and_returns_contract(monkeypatch) -> None:
    monkeypatch.setenv("ESTIMATOR_API_KEY", "test-secret")
    monkeypatch.setattr(
        "services.estimator_api.server.search_jobs",
        lambda **_kwargs: {
            "schema_version": "spraytec.job_search.v1",
            "as_of": "2026-07-30T12:00:00Z",
            "filters_applied": {"query": "Acme", "limit": 10},
            "headline_metrics": {"returned_records": 1},
            "records": [{"job_id": "JOB-1"}],
            "attention_items": [],
            "source_links": [],
            "source_tables": ["job_board_static_snapshot"],
            "data_freshness": {},
            "coverage": {},
            "warnings": [],
            "response_budget": {"max_records": 25},
        },
    )

    unauthorized = client.post("/v1/jobs/search", json={"query": "Acme"})
    authorized = client.post(
        "/v1/jobs/search",
        json={"query": "Acme"},
        headers={"Authorization": "Bearer test-secret"},
    )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert authorized.json()["records"][0]["job_id"] == "JOB-1"


def test_sharepoint_document_routes_use_shared_auth_and_contract(monkeypatch) -> None:
    monkeypatch.setenv("ESTIMATOR_API_KEY", "test-secret")
    monkeypatch.setattr(
        "services.estimator_api.server.search_sharepoint_documents",
        lambda **_kwargs: {
            "schema_version": "spraytec.sharepoint_document_search.v1",
            "query": "warranty",
            "filters_applied": {"limit": 10},
            "records": [
                {
                    "document_id": "DOC-1",
                    "job_id": "JOB-1",
                    "file_name": "Warranty.pdf",
                }
            ],
            "coverage": {"records_returned": 1},
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        "services.estimator_api.server.fetch_sharepoint_document",
        lambda **_kwargs: {
            "schema_version": "spraytec.sharepoint_document_fetch.v1",
            "document_id": "DOC-1",
            "job_id": "JOB-1",
            "file_name": "Warranty.pdf",
            "content": "Issued warranty details.",
            "content_source": "persisted_extracted_content",
            "content_available": True,
            "included_sections": 1,
            "total_sections": 1,
            "truncated": False,
            "warnings": [],
        },
    )

    unauthorized = client.post(
        "/v1/sharepoint/documents/search",
        json={"query": "warranty"},
    )
    searched = client.post(
        "/v1/sharepoint/documents/search",
        json={"query": "warranty"},
        headers={"Authorization": "Bearer test-secret"},
    )
    fetched = client.post(
        "/v1/sharepoint/documents/fetch",
        json={"document_id": "DOC-1"},
        headers={"Authorization": "Bearer test-secret"},
    )

    assert unauthorized.status_code == 401
    assert searched.status_code == 200
    assert searched.json()["records"][0]["document_id"] == "DOC-1"
    assert fetched.status_code == 200
    assert fetched.json()["content"] == "Issued warranty details."


def test_job_context_route_returns_404_for_unknown_job(monkeypatch) -> None:
    from jobscan.business.job_service import JobNotFoundError

    monkeypatch.setattr(
        "services.estimator_api.server.get_job_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(JobNotFoundError()),
    )

    response = client.get("/v1/jobs/UNKNOWN/context")

    assert response.status_code == 404


def test_action_openapi_marks_read_only_posts_nonconsequential() -> None:
    spec = build_action_openapi()

    assert spec["paths"]["/v1/estimating/context"]["post"][
        "x-openai-isConsequential"
    ] is False
    assert spec["paths"]["/v1/roof-measure/context"]["post"][
        "x-openai-isConsequential"
    ] is False
    assert spec["paths"]["/v1/roof-measure/calculate"]["post"][
        "x-openai-isConsequential"
    ] is False
    assert spec["paths"]["/v1/roof-measure/segment"]["post"][
        "x-openai-isConsequential"
    ] is False
    assert spec["paths"]["/v1/jobs/search"]["post"][
        "x-openai-isConsequential"
    ] is False
    assert spec["paths"]["/v1/sharepoint/documents/search"]["post"][
        "x-openai-isConsequential"
    ] is False
    assert spec["paths"]["/v1/sharepoint/documents/fetch"]["post"][
        "x-openai-isConsequential"
    ] is False
    assert spec["paths"]["/v1/sales/pipeline"]["post"][
        "x-openai-isConsequential"
    ] is False
    assert spec["paths"]["/v1/sales/follow-ups"]["post"][
        "x-openai-isConsequential"
    ] is False
    assert spec["paths"]["/v1/operations/backlog"]["post"][
        "x-openai-isConsequential"
    ] is False
    assert spec["paths"]["/v1/operations/schedule"]["post"][
        "x-openai-isConsequential"
    ] is False
    assert spec["paths"]["/v1/office/activity"]["post"][
        "x-openai-isConsequential"
    ] is False
    assert spec["paths"]["/v1/office/job-progress"]["post"][
        "x-openai-isConsequential"
    ] is False
    assert spec["paths"]["/v1/operations/production-budget-health"]["post"][
        "x-openai-isConsequential"
    ] is False
    assert spec["paths"]["/v1/estimating/workbook"]["post"][
        "x-openai-isConsequential"
    ] is True
    assert spec["paths"]["/v1/estimating/workbook-options"]["post"][
        "x-openai-isConsequential"
    ] is True
    assert spec["paths"]["/v1/estimating/workbook-template"]["post"][
        "x-openai-isConsequential"
    ] is False
    assert spec["paths"]["/v1/estimating/workbook-validation"]["post"][
        "x-openai-isConsequential"
    ] is True
    validation_schema = spec["components"]["schemas"][
        "EstimateWorkbookValidationRequest"
    ]
    assert validation_schema["properties"]["openaiFileIdRefs"]["items"] == {
        "type": "string"
    }
    assert "/v1/estimating/workbooks/{artifact_id}" not in spec["paths"]


def test_sales_pipeline_route_uses_shared_auth_and_contract(monkeypatch) -> None:
    monkeypatch.setenv("ESTIMATOR_API_KEY", "test-secret")
    monkeypatch.setattr(
        "services.estimator_api.server.get_sales_pipeline",
        lambda **_kwargs: {
            "schema_version": "spraytec.sales_pipeline.v1",
            "as_of": "2026-07-30T12:00:00Z",
            "filters_applied": {"limit": 10},
            "headline_metrics": {"job_count": 1},
            "stage_rollup": [],
            "owner_rollup": [],
            "records": [{"job_id": "JOB-1"}],
            "attention_items": [],
            "source_links": [],
            "source_tables": ["job_board_static_snapshot"],
            "data_freshness": {},
            "coverage": {},
            "warnings": [],
            "response_budget": {"max_records": 25},
        },
    )

    unauthorized = client.post("/v1/sales/pipeline", json={})
    authorized = client.post(
        "/v1/sales/pipeline",
        json={},
        headers={"Authorization": "Bearer test-secret"},
    )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert authorized.json()["records"][0]["job_id"] == "JOB-1"


def test_operations_routes_use_shared_auth_and_contract(monkeypatch) -> None:
    monkeypatch.setenv("ESTIMATOR_API_KEY", "test-secret")
    response_payload = {
        "schema_version": "spraytec.operations_backlog.v1",
        "as_of": "2026-07-30T12:00:00Z",
        "filters_applied": {"limit": 10},
        "headline_metrics": {"backlog_jobs": 1},
        "readiness_rollup": [],
        "division_rollup": [],
        "records": [{"job_id": "JOB-1"}],
        "attention_items": [],
        "source_links": [],
        "source_tables": ["operations_dashboard_ops_snapshot"],
        "data_freshness": {},
        "coverage": {},
        "warnings": [],
        "response_budget": {"max_records": 25},
    }
    monkeypatch.setattr(
        "services.estimator_api.server.get_operations_backlog",
        lambda **_kwargs: response_payload,
    )
    monkeypatch.setattr(
        "services.estimator_api.server.get_operations_schedule",
        lambda **_kwargs: {
            **response_payload,
            "schema_version": "spraytec.operations_schedule.v1",
            "schedule_health_rollup": [],
            "project_health_rollup": [],
            "crew_rollup": [],
        },
    )

    unauthorized = client.post("/v1/operations/backlog", json={})
    backlog = client.post(
        "/v1/operations/backlog",
        json={"unscheduled_only": True},
        headers={"Authorization": "Bearer test-secret"},
    )
    schedule = client.post(
        "/v1/operations/schedule",
        json={"risk_only": True},
        headers={"Authorization": "Bearer test-secret"},
    )

    assert unauthorized.status_code == 401
    assert backlog.status_code == 200
    assert backlog.json()["records"][0]["job_id"] == "JOB-1"
    assert schedule.status_code == 200
    assert schedule.json()["schema_version"] == "spraytec.operations_schedule.v1"


def test_office_activity_route_uses_shared_auth_and_contract(monkeypatch) -> None:
    monkeypatch.setenv("ESTIMATOR_API_KEY", "test-secret")
    monkeypatch.setattr(
        "services.estimator_api.server.get_office_activity",
        lambda **_kwargs: {
            "schema_version": "spraytec.office_activity.v1",
            "as_of": "2026-07-30T12:00:00Z",
            "filters_applied": {
                "start_date": "2026-07-24",
                "end_date": "2026-07-30",
                "timed_only": False,
                "limit": 10,
            },
            "headline_metrics": {"activity_entries": 1, "total_hours": 1.5},
            "employee_rollup": [],
            "code_rollup": [],
            "project_rollup": [],
            "daily_rollup": [],
            "records": [{"entry_id": "ENTRY-1"}],
            "attention_items": [],
            "source_links": [],
            "source_tables": ["office_timesheet_entries"],
            "data_freshness": {},
            "coverage": {},
            "warnings": [],
            "response_budget": {"max_records": 25},
        },
    )

    unauthorized = client.post("/v1/office/activity", json={})
    authorized = client.post(
        "/v1/office/activity",
        json={"employee": "Anthony P", "timed_only": True},
        headers={"Authorization": "Bearer test-secret"},
    )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert authorized.json()["records"][0]["entry_id"] == "ENTRY-1"


def test_production_budget_route_uses_shared_auth_and_proxy_contract(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ESTIMATOR_API_KEY", "test-secret")
    monkeypatch.setattr(
        "services.estimator_api.server.get_production_budget_health",
        lambda **_kwargs: {
            "schema_version": "spraytec.production_budget_health.v1",
            "as_of": "2026-07-30T12:00:00Z",
            "truth_class": "proxy",
            "methodology": {"budget_used_pct": "Not percent complete."},
            "filters_applied": {"limit": 10},
            "headline_metrics": {"jobs_with_budget_signal": 1},
            "bucket_rollup": [],
            "records": [{"job_id": "JOB-1", "truth_class": "proxy"}],
            "bucket_details": [],
            "attention_items": [],
            "source_links": [],
            "source_tables": [
                "job_tracking_summary",
                "job_tracking_estimate_budget_snapshot",
            ],
            "data_freshness": {},
            "coverage": {},
            "warnings": ["Not accounting actual cost."],
            "response_budget": {"max_records": 25},
        },
    )

    unauthorized = client.post(
        "/v1/operations/production-budget-health",
        json={},
    )
    authorized = client.post(
        "/v1/operations/production-budget-health",
        json={"over_plan_only": True},
        headers={"Authorization": "Bearer test-secret"},
    )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert authorized.json()["truth_class"] == "proxy"


def test_office_job_progress_route_uses_shared_auth_and_mixed_contract(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ESTIMATOR_API_KEY", "test-secret")
    monkeypatch.setattr(
        "services.estimator_api.server.get_office_job_progress",
        lambda **_kwargs: {
            "schema_version": "spraytec.office_job_progress.v1",
            "as_of": "2026-07-30T12:00:00Z",
            "truth_class": "mixed",
            "methodology": {"progress_definition": "Not percent complete."},
            "filters_applied": {"limit": 10},
            "headline_metrics": {"project_labels": 1},
            "link_status_rollup": [],
            "records": [
                {
                    "project_label": "Acme",
                    "job_id": "JOB-1",
                    "link_truth_class": "inferred",
                }
            ],
            "attention_items": [],
            "source_links": [],
            "source_tables": ["office_timesheet_entries", "dashboard_jobs"],
            "data_freshness": {},
            "coverage": {},
            "warnings": ["Text job links are inferred."],
            "response_budget": {"max_records": 25},
        },
    )

    unauthorized = client.post("/v1/office/job-progress", json={})
    authorized = client.post(
        "/v1/office/job-progress",
        json={"stalled_only": True},
        headers={"Authorization": "Bearer test-secret"},
    )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert authorized.json()["truth_class"] == "mixed"
    assert authorized.json()["records"][0]["link_truth_class"] == "inferred"


def test_chart_dataset_routes_reuse_business_rollups_and_shared_auth(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ESTIMATOR_API_KEY", "test-secret")
    monkeypatch.setattr(
        "services.estimator_api.server.get_sales_pipeline",
        lambda **_kwargs: {
            "schema_version": "spraytec.sales_pipeline.v1",
            "as_of": "2026-07-31T12:00:00Z",
            "filters_applied": {"division": "Roofing"},
            "stage_rollup": [
                {"pipeline_status": "Proposed", "job_count": 2, "estimated_value": 120000}
            ],
            "source_tables": ["dashboard_jobs"],
            "data_freshness": {},
            "coverage": {},
            "warnings": [],
        },
    )

    unauthorized = client.post(
        "/v1/reporting/chart-data",
        json={"dataset": "sales_pipeline_by_stage"},
    )
    authorized = client.post(
        "/v1/reporting/chart-data",
        json={"dataset": "sales_pipeline_by_stage", "division": "Roofing"},
        headers={"Authorization": "Bearer test-secret"},
    )
    csv_response = client.post(
        "/v1/reporting/chart-data.csv",
        json={"dataset": "sales_pipeline_by_stage", "division": "Roofing"},
        headers={"Authorization": "Bearer test-secret"},
    )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert authorized.json()["rows"][0]["estimated_value"] == 120000
    assert csv_response.status_code == 200
    assert csv_response.headers["content-type"].startswith("text/csv")
    assert "sales_pipeline_by_stage" in csv_response.text
    assert "120000" in csv_response.text


def test_schedule_gantt_route_uses_larger_bounded_record_budget(monkeypatch) -> None:
    monkeypatch.setenv("ESTIMATOR_API_KEY", "test-secret")
    captured: dict[str, object] = {}

    def fake_schedule(**kwargs):
        captured.update(kwargs)
        return {
            "as_of": "2026-08-01T12:00:00Z",
            "filters_applied": {
                "start_date": "2026-08-01",
                "end_date": "2026-10-24",
            },
            "records": [
                {
                    "job_id": "JOB-1",
                    "job_name": "Acme Roof",
                    "assigned_crew_leader": "Carlos",
                    "estimated_start_date": "2026-08-04",
                    "estimated_duration_days": 4,
                }
            ],
            "coverage": {"results_truncated": False},
        }

    monkeypatch.setattr(
        "services.estimator_api.server.get_operations_schedule",
        fake_schedule,
    )

    response = client.post(
        "/v1/reporting/chart-data",
        json={
            "dataset": "operations_schedule_gantt",
            "start_date": "2026-08-01",
            "end_date": "2026-10-24",
            "gantt_limit": 60,
        },
        headers={"Authorization": "Bearer test-secret"},
    )

    assert response.status_code == 200
    assert captured["limit"] == 60
    assert captured["max_records"] == 125
    body = response.json()
    assert body["recommended_chart_type"] == "gantt"
    assert body["group_field"] == "crew_leader"
    assert body["rows"][0]["display_end_date"] == "2026-08-07"


def test_historical_chart_route_uses_staged_daily_observations(monkeypatch) -> None:
    monkeypatch.setenv("ESTIMATOR_API_KEY", "test-secret")
    captured: dict[str, object] = {}

    def fake_history(dataset, **kwargs):
        captured["dataset"] = dataset
        captured.update(kwargs)
        return {
            "as_of": "2026-08-04T12:00:00Z",
            "truth_class": "authoritative",
            "filters_applied": {
                "start_date": "2026-08-01",
                "end_date": "2026-08-04",
            },
            "records": [
                {
                    "snapshot_date": "2026-08-03",
                    "pipeline_value": 100000,
                    "job_count": 2,
                },
                {
                    "snapshot_date": "2026-08-04",
                    "pipeline_value": 125000,
                    "job_count": 3,
                },
            ],
            "source_tables": ["reporting_chart_daily_snapshots"],
            "data_freshness": {},
            "staging": {
                "aggregation_mode": "staged_daily_snapshot",
                "source_storage": "append_only_history",
                "snapshot_tables": ["reporting_chart_daily_snapshots"],
                "freshness": {},
                "historical_series_available": True,
                "historical_limitation": "History begins at deployment.",
            },
            "coverage": {"available_snapshot_days": 2},
            "warnings": [],
        }

    monkeypatch.setattr("services.estimator_api.server.get_chart_history", fake_history)

    response = client.post(
        "/v1/reporting/chart-data",
        json={
            "dataset": "sales_pipeline_history",
            "start_date": "2026-08-01",
            "end_date": "2026-08-04",
        },
        headers={"Authorization": "Bearer test-secret"},
    )

    assert response.status_code == 200
    assert captured["dataset"] == "sales_pipeline_history"
    assert str(captured["start_date"]) == "2026-08-01"
    assert response.json()["staging"]["historical_series_available"] is True
    assert response.json()["rows"][-1]["pipeline_value"] == 125000


def test_checked_in_action_openapi_is_current() -> None:
    path = Path("services/estimator_api/openapi.json")
    checked_in = json.loads(path.read_text(encoding="utf-8"))
    deployed_server_url = checked_in["servers"][0]["url"]
    assert checked_in == build_action_openapi(deployed_server_url)


def test_action_openapi_operation_descriptions_fit_gpt_limit() -> None:
    specification = build_action_openapi(
        "https://spraytec-business-api.icysand-5925ab36.eastus2.azurecontainerapps.io"
    )
    over_limit = [
        (operation.get("operationId"), len(operation.get("description") or ""))
        for path_item in specification["paths"].values()
        for method, operation in path_item.items()
        if method.lower() in {"get", "post"}
        and len(operation.get("description") or "") > 300
    ]

    assert over_limit == []


def test_bidscope_page_selection_route_returns_native_review_packet(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_bidscope(**kwargs):
        captured.update(kwargs)
        return {
            "schema_version": "spraytec.bidscope_page_selection.v1",
            "context_id": "a" * 32,
            "expires_at": 4102444800,
            "source_sharepoint_url": kwargs["sharepoint_url"],
            "trade_type": kwargs["trade_type"],
            "trade_name": "Foam Insulation",
            "selection_method": "deterministic test selection",
            "assistant_review_instruction": "Review the attached pages.",
            "packet_page_count": 1,
            "seed_pages": [],
            "measurement_candidates": [],
            "supporting_reference_pages": [],
            "source_links": [],
            "coverage": {"selection_is_partial": False},
            "measurement_readiness": {
                "status": "requires_confirmed_pages_and_scale",
                "scale_required": True,
            },
            "warnings": [],
            "openaiFileResponse": [
                {
                    "name": "bidscope_page_review.pdf",
                    "mime_type": "application/pdf",
                    "content": "JVBERi0xLjQ=",
                }
            ],
        }

    monkeypatch.setattr(
        "services.estimator_api.server.build_bidscope_review_packet",
        fake_bidscope,
    )
    response = client.post(
        "/v1/bidscope/page-selection",
        json={
            "sharepoint_url": "https://spraytec.sharepoint.com/sites/Data/Shared%20Documents/Bid",
            "trade_type": "foam_insulation",
            "reference_depth": 4,
            "max_scan_pages": 250,
            "max_packet_pages": 10,
        },
    )

    assert response.status_code == 200
    assert response.json()["openaiFileResponse"][0]["name"] == "bidscope_page_review.pdf"
    assert captured["reference_depth"] == 4
    assert captured["max_scan_pages"] == 250
    assert captured["max_packet_pages"] == 10
    assert captured["artifact_dir"].name == "bidscope"


def test_bidscope_page_selection_action_is_read_only_in_openapi() -> None:
    specification = build_action_openapi(
        "https://spraytec-business-api.icysand-5925ab36.eastus2.azurecontainerapps.io"
    )
    operation = specification["paths"]["/v1/bidscope/page-selection"]["post"]

    assert operation["operationId"] == "selectBidScopePages"
    assert operation["x-openai-isConsequential"] is False


def test_bidscope_measurement_context_route_prepares_confirmed_pages(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_measurement_context(**kwargs):
        captured.update(kwargs)
        return {
            "schema_version": "spraytec.bidscope_measurement_context.v1",
            "source_context_id": kwargs["context_id"],
            "measurement_context_id": "b" * 32,
            "expires_at": 4102444800,
            "trade_type": "foam_insulation",
            "confirmed_page_count": 1,
            "pages": [{"page_id": "plan::page_1"}],
            "measurement_readiness": {
                "status": "ready_for_tracing",
                "segmentation_status": "not_run",
            },
            "warnings": [],
            "openaiFileResponse": [
                {
                    "name": "bidscope_confirmed_measurement_pages.pdf",
                    "mime_type": "application/pdf",
                    "content": "JVBERi0xLjQ=",
                }
            ],
        }

    monkeypatch.setattr(
        "services.estimator_api.server.create_bidscope_measurement_context",
        fake_measurement_context,
    )
    response = client.post(
        "/v1/bidscope/measurement-context",
        json={
            "context_id": "a" * 32,
            "confirmed_pages": [
                {
                    "page_id": "plan::page_1",
                    "confirmed_scale_text": '1/8" = 1\'-0"',
                }
            ],
            "render_dpi": 144,
        },
    )

    assert response.status_code == 200
    assert response.json()["measurement_context_id"] == "b" * 32
    assert captured["confirmed_pages"][0]["page_id"] == "plan::page_1"
    assert captured["render_dpi"] == 144


def test_bidscope_measurement_context_action_is_read_only_in_openapi() -> None:
    specification = build_action_openapi(
        "https://spraytec-business-api.icysand-5925ab36.eastus2.azurecontainerapps.io"
    )
    operation = specification["paths"]["/v1/bidscope/measurement-context"]["post"]

    assert operation["operationId"] == "createBidScopeMeasurementContext"
    assert operation["x-openai-isConsequential"] is False


def test_bidscope_prepare_measurement_context_route_resolves_known_sheets(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_prepare(**kwargs):
        captured.update(kwargs)
        return {
            "schema_version": "spraytec.bidscope_measurement_context.v1",
            "source_context_id": "a" * 32,
            "measurement_context_id": "b" * 32,
            "expires_at": 4102444800,
            "trade_type": "foam_insulation",
            "confirmed_page_count": 1,
            "pages": [{"page_id": "plans::page_4", "sheet_id": "A-201"}],
            "measurement_readiness": {"status": "ready_for_tracing"},
            "warnings": [],
            "openaiFileResponse": [
                {
                    "name": "bidscope_confirmed_measurement_pages.pdf",
                    "mime_type": "application/pdf",
                    "content": "JVBERi0xLjQ=",
                }
            ],
        }

    monkeypatch.setattr(
        "services.estimator_api.server.prepare_bidscope_measurement_context",
        fake_prepare,
    )
    response = client.post(
        "/v1/bidscope/prepare-measurement-context",
        json={
            "sharepoint_url": "https://spraytec.sharepoint.com/sites/Data/Plans.pdf",
            "confirmed_pages": [
                {
                    "sheet_id": "A-201",
                    "confirmed_scale_text": '1/8" = 1\'-0"',
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["measurement_context_id"] == "b" * 32
    assert captured["confirmed_pages"][0]["sheet_id"] == "A-201"


def test_bidscope_prepare_measurement_context_action_is_read_only_in_openapi() -> None:
    specification = build_action_openapi(
        "https://spraytec-business-api.icysand-5925ab36.eastus2.azurecontainerapps.io"
    )
    operation = specification["paths"]["/v1/bidscope/prepare-measurement-context"]["post"]

    assert operation["operationId"] == "prepareBidScopeMeasurementContext"
    assert operation["x-openai-isConsequential"] is False


def test_bidscope_region_trace_route_returns_review_overlay(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_trace(**kwargs):
        captured.update(kwargs)
        return {
            "schema_version": "spraytec.bidscope_region_trace.v1",
            "measurement_context_id": kwargs["measurement_context_id"],
            "trace_count": 1,
            "traces": [
                {
                    "region_id": "level-one",
                    "measurement_type": "boundary_length",
                    "measurements": {"quantity": 240.0, "unit": "linear_ft"},
                }
            ],
            "review_status": "requires_estimator_verification",
            "segmentation_status": "completed",
            "warnings": [],
            "openaiFileResponse": [
                {
                    "name": "bidscope_traced_regions.jpg",
                    "mime_type": "image/jpeg",
                    "content": "/9j/",
                }
            ],
        }

    monkeypatch.setattr(
        "services.estimator_api.server.trace_bidscope_regions",
        fake_trace,
    )
    response = client.post(
        "/v1/bidscope/trace-regions",
        json={
            "measurement_context_id": "a" * 32,
            "regions": [
                {
                    "region_id": "level-one",
                    "page_id": "plans::page_1",
                    "measurement_type": "boundary_length",
                    "positive_points": [{"x": 0.5, "y": 0.5}],
                    "negative_points": [{"x": 0.05, "y": 0.05}],
                    "box": {
                        "x_min": 0.1,
                        "y_min": 0.1,
                        "x_max": 0.9,
                        "y_max": 0.9,
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["openaiFileResponse"][0]["name"] == "bidscope_traced_regions.jpg"
    assert captured["regions"][0]["measurement_type"] == "boundary_length"
    assert captured["inference_max_side"] == 1600


def test_bidscope_region_trace_action_is_read_only_in_openapi() -> None:
    specification = build_action_openapi(
        "https://spraytec-business-api.icysand-5925ab36.eastus2.azurecontainerapps.io"
    )
    operation = specification["paths"]["/v1/bidscope/trace-regions"]["post"]

    assert operation["operationId"] == "traceBidScopeRegions"
    assert operation["x-openai-isConsequential"] is False


def test_warranty_summary_route_uses_auth_and_passes_filters(monkeypatch) -> None:
    monkeypatch.setenv("ESTIMATOR_API_KEY", "test-secret")
    captured: dict[str, object] = {}

    def fake_warranty_summary(**kwargs):
        captured.update(kwargs)
        return {
            "schema_version": "spraytec.warranty_summary.v2",
            "as_of": "2026-08-04T12:00:00Z",
            "filters_applied": {"job_year": 2026, "warranty_status": "issued"},
            "headline_metrics": {"warranty_records": 1},
            "status_rollup": [{"warranty_status": "issued", "warranty_count": 1}],
            "category_rollup": [],
            "records": [{"job_id": "JOB-1", "warranty_status": "issued"}],
            "attention_items": [],
            "review_queue_summary": {},
            "data_quality_tasks": [],
            "source_links": [],
            "source_tables": ["job_warranty_summary"],
            "data_freshness": {},
            "coverage": {},
            "warnings": [],
            "response_budget": {"max_records": 25},
        }

    monkeypatch.setattr(
        "services.estimator_api.server.get_warranty_summary",
        fake_warranty_summary,
    )

    unauthorized = client.post("/v1/jobs/warranties", json={})
    authorized = client.post(
        "/v1/jobs/warranties",
        json={"job_year": 2026, "warranty_status": "issued", "needs_review": False},
        headers={"Authorization": "Bearer test-secret"},
    )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert captured["job_year"] == 2026
    assert captured["warranty_status"] == "issued"
    assert captured["needs_review"] is False


def test_warranty_list_route_uses_auth_and_passes_review_filters(monkeypatch) -> None:
    monkeypatch.setenv("ESTIMATOR_API_KEY", "test-secret")
    captured: dict[str, object] = {}

    def fake_warranty_list(**kwargs):
        captured.update(kwargs)
        return {
            "schema_version": "spraytec.warranty_list.v1",
            "as_of": "2026-08-05T12:00:00Z",
            "filters_applied": {"query": "Acme", "has_contact": True},
            "headline_metrics": {"warranty_records": 1},
            "evidence_status_rollup": [],
            "category_rollup": [],
            "records": [{"project_name": "Acme Roof", "contact_emails": "alex@example.com"}],
            "source_links": [],
            "source_tables": ["warranty_master_clean"],
            "data_freshness": {},
            "coverage": {},
            "warnings": [],
            "response_budget": {"max_records": 50, "returned_records": 1},
        }

    monkeypatch.setattr(
        "services.estimator_api.server.get_warranty_list",
        fake_warranty_list,
    )

    unauthorized = client.post("/v1/warranties/list", json={})
    authorized = client.post(
        "/v1/warranties/list",
        json={
            "query": "Acme",
            "evidence_status": "issued_document",
            "needs_review": False,
            "has_contact": True,
            "reliable_only": True,
        },
        headers={"Authorization": "Bearer test-secret"},
    )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert captured["query"] == "Acme"
    assert captured["evidence_status"] == "issued_document"
    assert captured["needs_review"] is False
    assert captured["has_contact"] is True
    assert captured["reliable_only"] is True


def test_action_openapi_excludes_internal_source_metadata() -> None:
    spec = build_action_openapi()
    schemas = spec["components"]["schemas"]

    assert (
        "include_source_metadata"
        not in schemas["EstimateContextRequest"]["properties"]
    )
    assert "source_metadata" not in schemas["EstimateContextResponse"]["properties"]
