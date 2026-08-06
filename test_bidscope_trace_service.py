from __future__ import annotations

import base64
import json
import time

import numpy as np
import pytest
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


def test_sam2_mask_is_hard_clipped_to_requested_box(tmp_path) -> None:
    context_id, page_id = _measurement_context(tmp_path)
    mask = np.ones((100, 100), dtype=bool)

    result = trace_bidscope_regions(
        measurement_context_id=context_id,
        regions=[
            {
                "region_id": "bounded-facade",
                "page_id": page_id,
                "measurement_type": "area",
                "positive_points": [{"x": 0.5, "y": 0.5}],
                "box": {
                    "x_min": 0.2,
                    "y_min": 0.2,
                    "x_max": 0.8,
                    "y_max": 0.8,
                },
            }
        ],
        sam2_url="",
        sam2_api_key="",
        artifact_dir=tmp_path,
        segmenter=MockRoofSegmenter([mask]),
    )

    trace = result["traces"][0]
    assert trace["hard_clip_applied"] is True
    assert trace["trace_method"] == "sam2_prompted_region_hard_clipped"
    assert trace["measurements"]["quantity"] < 40
    polygon = trace["sections"][0]["polygon"]
    assert min(point["x"] for point in polygon) >= 0.19
    assert max(point["x"] for point in polygon) <= 0.81
    assert min(point["y"] for point in polygon) >= 0.19
    assert max(point["y"] for point in polygon) <= 0.81


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


def test_opening_region_is_subtracted_from_gross_surface_on_same_image(tmp_path) -> None:
    context_id, page_id = _measurement_context(tmp_path)

    result = trace_bidscope_regions(
        measurement_context_id=context_id,
        regions=[
            {
                "region_id": "south-gross",
                "page_id": page_id,
                "label": "South elevation gross surface",
                "measurement_type": "area",
                "polygon": [
                    {"x": 0.0, "y": 0.0},
                    {"x": 1.0, "y": 0.0},
                    {"x": 1.0, "y": 1.0},
                    {"x": 0.0, "y": 1.0},
                ],
            },
            {
                "region_id": "south-openings",
                "page_id": page_id,
                "label": "South elevation windows and doors",
                "measurement_type": "area",
                "quantity_role": "deduction",
                "deduct_from_region_id": "south-gross",
                "polygon": [
                    {"x": 0.1, "y": 0.1},
                    {"x": 0.2, "y": 0.1},
                    {"x": 0.2, "y": 0.2},
                    {"x": 0.1, "y": 0.2},
                ],
            },
        ],
        sam2_url="",
        sam2_api_key="",
        artifact_dir=tmp_path,
    )

    summary = result["quantity_summary"][0]
    assert summary["region_id"] == "south-gross"
    assert summary["gross_quantity_sqft"] == 98.0
    assert summary["traced_opening_deduction_sqft"] == 1.0
    assert summary["net_quantity_sqft"] == 97.0
    assert summary["deduction_region_ids"] == ["south-openings"]
    assert result["traces"][1]["quantity_role"] == "deduction"


def test_disconnected_openings_are_independent_components_without_connectors(tmp_path) -> None:
    context_id, page_id = _measurement_context(tmp_path)

    result = trace_bidscope_regions(
        measurement_context_id=context_id,
        regions=[
            {
                "region_id": "north-gross",
                "page_id": page_id,
                "measurement_type": "area",
                "polygon": [
                    {"x": 0.0, "y": 0.0},
                    {"x": 1.0, "y": 0.0},
                    {"x": 1.0, "y": 1.0},
                    {"x": 0.0, "y": 1.0},
                ],
            },
            {
                "region_id": "north-openings",
                "page_id": page_id,
                "measurement_type": "area",
                "quantity_role": "deduction",
                "deduct_from_region_id": "north-gross",
                "polygons": [
                    [
                        {"x": 0.1, "y": 0.1},
                        {"x": 0.2, "y": 0.1},
                        {"x": 0.2, "y": 0.2},
                        {"x": 0.1, "y": 0.2},
                    ],
                    [
                        {"x": 0.4, "y": 0.1},
                        {"x": 0.5, "y": 0.1},
                        {"x": 0.5, "y": 0.2},
                        {"x": 0.4, "y": 0.2},
                    ],
                ],
            },
        ],
        sam2_url="",
        sam2_api_key="",
        artifact_dir=tmp_path,
    )

    deduction = result["traces"][1]
    assert deduction["trace_method"] == "assistant_supplied_multipolygon"
    assert deduction["component_count"] == 2
    assert len(deduction["sections"]) == 2
    assert deduction["measurements"]["quantity"] == 2.0
    assert result["quantity_summary"][0]["net_quantity_sqft"] == 96.0


def test_connected_opening_polygon_is_rejected(tmp_path) -> None:
    context_id, page_id = _measurement_context(tmp_path)

    with pytest.raises(BidScopeInputError, match="one independent ring per opening"):
        trace_bidscope_regions(
            measurement_context_id=context_id,
            regions=[
                {
                    "region_id": "joined-openings",
                    "page_id": page_id,
                    "measurement_type": "area",
                    "quantity_role": "deduction",
                    "deduct_from_region_id": "unused-gross",
                    "polygon": [
                        {"x": 0.1, "y": 0.1},
                        {"x": 0.2, "y": 0.1},
                        {"x": 0.2, "y": 0.2},
                        {"x": 0.1, "y": 0.2},
                        {"x": 0.1, "y": 0.1},
                        {"x": 0.4, "y": 0.1},
                        {"x": 0.5, "y": 0.1},
                        {"x": 0.5, "y": 0.2},
                        {"x": 0.4, "y": 0.2},
                        {"x": 0.4, "y": 0.1},
                    ],
                }
            ],
            sam2_url="",
            sam2_api_key="",
            artifact_dir=tmp_path,
        )


def test_opening_component_outside_gross_region_is_rejected(tmp_path) -> None:
    context_id, page_id = _measurement_context(tmp_path)

    with pytest.raises(BidScopeInputError, match="extends outside gross region"):
        trace_bidscope_regions(
            measurement_context_id=context_id,
            regions=[
                {
                    "region_id": "bounded-gross",
                    "page_id": page_id,
                    "measurement_type": "area",
                    "polygon": [
                        {"x": 0.1, "y": 0.1},
                        {"x": 0.9, "y": 0.1},
                        {"x": 0.9, "y": 0.9},
                        {"x": 0.1, "y": 0.9},
                    ],
                },
                {
                    "region_id": "outside-opening",
                    "page_id": page_id,
                    "measurement_type": "area",
                    "quantity_role": "deduction",
                    "deduct_from_region_id": "bounded-gross",
                    "polygons": [[
                        {"x": 0.05, "y": 0.2},
                        {"x": 0.2, "y": 0.2},
                        {"x": 0.2, "y": 0.3},
                        {"x": 0.05, "y": 0.3},
                    ]],
                },
            ],
            sam2_url="",
            sam2_api_key="",
            artifact_dir=tmp_path,
        )


def test_overlapping_opening_components_are_rejected(tmp_path) -> None:
    context_id, page_id = _measurement_context(tmp_path)

    with pytest.raises(BidScopeInputError, match="components 1 and 2 overlap"):
        trace_bidscope_regions(
            measurement_context_id=context_id,
            regions=[
                {
                    "region_id": "overlapping-openings",
                    "page_id": page_id,
                    "measurement_type": "area",
                    "quantity_role": "deduction",
                    "deduct_from_region_id": "unused-gross",
                    "polygons": [
                        [
                            {"x": 0.1, "y": 0.1},
                            {"x": 0.3, "y": 0.1},
                            {"x": 0.3, "y": 0.3},
                            {"x": 0.1, "y": 0.3},
                        ],
                        [
                            {"x": 0.2, "y": 0.2},
                            {"x": 0.4, "y": 0.2},
                            {"x": 0.4, "y": 0.4},
                            {"x": 0.2, "y": 0.4},
                        ],
                    ],
                }
            ],
            sam2_url="",
            sam2_api_key="",
            artifact_dir=tmp_path,
        )


def test_later_opening_pass_updates_persisted_gross_quantity(tmp_path) -> None:
    context_id, page_id = _measurement_context(tmp_path)
    common = {
        "measurement_context_id": context_id,
        "sam2_url": "",
        "sam2_api_key": "",
        "artifact_dir": tmp_path,
    }
    trace_bidscope_regions(
        **common,
        regions=[
            {
                "region_id": "west-gross",
                "page_id": page_id,
                "measurement_type": "area",
                "polygon": [
                    {"x": 0.0, "y": 0.0},
                    {"x": 1.0, "y": 0.0},
                    {"x": 1.0, "y": 1.0},
                    {"x": 0.0, "y": 1.0},
                ],
            }
        ],
    )

    deduction_pass = trace_bidscope_regions(
        **common,
        regions=[
            {
                "region_id": "west-openings",
                "page_id": page_id,
                "measurement_type": "area",
                "quantity_role": "deduction",
                "deduct_from_region_id": "west-gross",
                "polygon": [
                    {"x": 0.1, "y": 0.1},
                    {"x": 0.2, "y": 0.1},
                    {"x": 0.2, "y": 0.2},
                    {"x": 0.1, "y": 0.2},
                ],
            }
        ],
    )

    assert deduction_pass["quantity_summary"][0]["net_quantity_sqft"] == 97.0
    persisted = json.loads(
        (tmp_path / context_id / "context.json").read_text(encoding="utf-8")
    )
    gross = next(trace for trace in persisted["traces"] if trace["region_id"] == "west-gross")
    assert gross["measurements"]["traced_opening_deduction_sqft"] == 1.0


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
