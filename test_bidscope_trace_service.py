from __future__ import annotations

import base64
import json
import time

import numpy as np
from PIL import Image

from jobscan.business.bidscope_service import BidScopeInputError
from jobscan.business.bidscope_trace_service import trace_bidscope_regions
from roof_measure.segmentation import MockRoofSegmenter


def _measurement_context(tmp_path, *, scale_status: str = "confirmed") -> tuple[str, str]:
    context_id = "a" * 32
    page_id = "plans::page_1"
    context_path = tmp_path / context_id
    context_path.mkdir()
    Image.new("RGB", (100, 100), "white").save(context_path / "measurement_page_01_144dpi.png")
    payload = {
        "schema_version": "spraytec.bidscope_measurement_context.v1",
        "context_type": "measurement_context",
        "context_id": context_id,
        "expires_at": int(time.time()) + 3600,
        "trade_type": "foam_insulation",
        "pages": [
            {
                "page_id": page_id,
                "sheet_id": "A2.00",
                "rendered_image_asset_name": "measurement_page_01_144dpi.png",
                "scale_calibration": {
                    "status": scale_status,
                    "scale_inches_per_foot": 0.06944444,
                    "pdf_points_per_foot": 5,
                    "rendered_pixels_per_foot": 10,
                    "source": "estimator_confirmed_numeric",
                },
            }
        ],
    }
    (context_path / "context.json").write_text(json.dumps(payload), encoding="utf-8")
    return context_id, page_id


def test_sam2_prompted_region_returns_scaled_measurement_and_overlay(tmp_path) -> None:
    context_id, page_id = _measurement_context(tmp_path)
    mask = np.zeros((100, 100), dtype=bool)
    mask[20:80, 20:80] = True

    result = trace_bidscope_regions(
        measurement_context_id=context_id,
        regions=[
            {
                "region_id": "floorplate",
                "page_id": page_id,
                "label": "Exterior floorplate",
                "measurement_type": "area",
                "positive_points": [{"x": 0.5, "y": 0.5}],
                "negative_points": [{"x": 0.05, "y": 0.05}],
                "architectural_cleanup": True,
            }
        ],
        sam2_url="",
        sam2_api_key="",
        artifact_dir=tmp_path,
        inference_max_side=1600,
        segmenter=MockRoofSegmenter([mask]),
    )

    assert result["schema_version"] == "spraytec.bidscope_region_trace.v1"
    assert result["segmentation_status"] == "completed"
    trace = result["traces"][0]
    assert trace["trace_method"] == "sam2_prompted_region"
    assert trace["measurements"]["quantity"] == 36.0
    assert trace["measurements"]["unit"] == "sqft"
    assert trace["measurements"]["closed_boundary_length_ft"] == 24.0
    assert trace["review_status"] == "requires_estimator_verification"
    assert trace["sections"][0]["polygon"]
    attachment = result["openaiFileResponse"][0]
    assert attachment["name"] == "bidscope_traced_regions.jpg"
    assert base64.b64decode(attachment["content"]).startswith(b"\xff\xd8")


def test_explicit_polygon_can_calculate_net_wall_area(tmp_path) -> None:
    context_id, page_id = _measurement_context(tmp_path)

    result = trace_bidscope_regions(
        measurement_context_id=context_id,
        regions=[
            {
                "region_id": "level-one-exterior",
                "page_id": page_id,
                "measurement_type": "wall_area",
                "polygon": [
                    {"x": 0.1, "y": 0.1},
                    {"x": 0.6, "y": 0.1},
                    {"x": 0.6, "y": 0.6},
                    {"x": 0.1, "y": 0.6},
                ],
                "height_ft": 10,
                "opening_deduction_sqft": 20,
            }
        ],
        sam2_url="",
        sam2_api_key="",
        artifact_dir=tmp_path,
    )

    trace = result["traces"][0]
    assert trace["trace_method"] == "assistant_supplied_polygon"
    assert trace["measurements"]["closed_boundary_length_ft"] == 19.8
    assert trace["measurements"]["gross_wall_area_sqft"] == 198.0
    assert trace["measurements"]["quantity"] == 178.0
    assert result["segmentation_status"] == "not_requested"


def test_trace_rejects_page_without_confirmed_scale(tmp_path) -> None:
    context_id, page_id = _measurement_context(
        tmp_path,
        scale_status="detected_requires_confirmation",
    )

    try:
        trace_bidscope_regions(
            measurement_context_id=context_id,
            regions=[
                {
                    "region_id": "unscaled",
                    "page_id": page_id,
                    "measurement_type": "area",
                    "polygon": [
                        {"x": 0.1, "y": 0.1},
                        {"x": 0.5, "y": 0.1},
                        {"x": 0.5, "y": 0.5},
                    ],
                }
            ],
            sam2_url="",
            sam2_api_key="",
            artifact_dir=tmp_path,
        )
    except BidScopeInputError as exc:
        assert "confirmed scale" in str(exc)
    else:
        raise AssertionError("Expected tracing to reject an unconfirmed scale")
