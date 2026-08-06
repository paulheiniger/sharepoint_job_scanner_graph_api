from __future__ import annotations

from io import BytesIO
import json
import time

from PIL import Image, ImageDraw
import numpy as np
import pytest

from roof_measure.api_context import (
    RoofMeasureContextExpiredError,
    RoofMeasureInputError,
    calculate_roof_measurement,
    create_roof_measure_context,
    focus_roof_measure_context,
    load_roof_measure_context,
    resolve_roof_measure_asset,
)
from roof_measure.api_segmentation import (
    _architectural_simplification_tolerance_pixels,
    segment_roof_measure_context,
)
from roof_measure.lidar import LidarHeightGrid
from roof_measure.map_reference import (
    BuildingFootprint,
    BuildingFootprintLookup,
    LidarCoverage,
    MapboxStaticImage,
)
from roof_measure.segmentation import MaskCandidate, SegmentationResult


def _png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (100, 100), "white").save(buffer, format="PNG")
    return buffer.getvalue()


class _FakeMapboxProvider:
    def __init__(self, _token: str):
        pass

    def static_satellite_image(self, *_args, **_kwargs) -> MapboxStaticImage:
        return MapboxStaticImage(
            ok=True,
            image_bytes=_png_bytes(),
            latitude=38.0,
            longitude=-84.0,
            zoom=17.5,
            pixels_per_foot=2.0,
        )

    def building_footprints(self, **_kwargs) -> BuildingFootprintLookup:
        return BuildingFootprintLookup(
            ok=True,
            footprints=[
                BuildingFootprint(
                    footprint_id="source-1",
                    label="Main building",
                    provider="mapbox",
                    rings=[[(-84.0, 38.0), (-83.9, 38.0), (-83.9, 38.1)]],
                )
            ],
        )


@pytest.fixture
def roof_context(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "roof_measure.api_context.MapboxReferenceProvider",
        _FakeMapboxProvider,
    )
    monkeypatch.setattr(
        "roof_measure.api_context.postgres_building_footprints",
        lambda **_kwargs: BuildingFootprintLookup(
            ok=False,
            footprints=[],
            provider="postgres",
            warning="No local footprint.",
        ),
    )
    monkeypatch.setattr(
        "roof_measure.api_context.footprint_rings_to_image_pixels",
        lambda *_args, **_kwargs: [[(40, 40), (60, 40), (60, 60), (40, 60)]],
    )
    monkeypatch.setattr(
        "roof_measure.api_context.kyfromabove_lidar_coverage",
        lambda **_kwargs: LidarCoverage(
            ok=True,
            collection="laz-phase3",
            captured_at="2025-01-01T00:00:00Z",
            point_count=1234,
        ),
    )
    monkeypatch.setattr(
        "roof_measure.api_context.microsoft_global_building_footprints",
        lambda **_kwargs: BuildingFootprintLookup(
            ok=False,
            footprints=[],
            provider="microsoft_global_ml",
            warning="No Microsoft footprint.",
        ),
    )
    context = create_roof_measure_context(
        address="830 South 1st Street, Louisville, KY 40203",
        job_id="JOB-1",
        view="whole_site",
        include_lidar_coverage=True,
        mapbox_token="test-token",
        database_url="",
        artifact_dir=tmp_path,
        expires_at=int(time.time()) + 900,
    )
    return tmp_path, context


def test_context_creates_bounded_signed_asset_inputs_without_ai(roof_context) -> None:
    artifact_dir, context = roof_context

    assert context["footprint_candidates"][0]["footprint_id"] == "fp-01"
    assert context["footprint_candidates"][0]["plan_area_sqft"] == 100.0
    assert context["footprint_candidates"][0]["perimeter_ft"] == 40.0
    assert context["lidar_coverage"]["available"] is True
    assert context["lidar_coverage"]["point_count"] == 1234
    assert resolve_roof_measure_asset(
        context_id=context["context_id"],
        asset_name="satellite.png",
        artifact_dir=artifact_dir,
    ).is_file()
    assert resolve_roof_measure_asset(
        context_id=context["context_id"],
        asset_name="footprint-overlay.png",
        artifact_dir=artifact_dir,
    ).is_file()


def test_context_view_zoom_supports_conversational_close_detail() -> None:
    from roof_measure.api_context import VIEW_ZOOM

    assert VIEW_ZOOM["whole_site"] == 16.5
    assert VIEW_ZOOM["building_detail"] == 19.0
    assert VIEW_ZOOM["close_detail"] == 20.0


def test_segmentation_view_fits_and_recenters_selected_footprint(
    roof_context,
    monkeypatch,
) -> None:
    from roof_measure.api_segmentation import (
        _fit_segmentation_view_to_footprints,
        _fitted_components_to_original,
    )

    _artifact_dir, context = roof_context
    selected_components = context["footprint_candidates"][0]["components"]
    requested: dict[str, float] = {}

    class FakeDetailProvider:
        def __init__(self, token: str):
            assert token == "mapbox-test-token"

        def static_satellite_image_at(self, **kwargs) -> MapboxStaticImage:
            requested.update(kwargs)
            return MapboxStaticImage(
                ok=True,
                image_bytes=_png_bytes(),
                latitude=float(kwargs["latitude"]),
                longitude=float(kwargs["longitude"]),
                zoom=float(kwargs["zoom"]),
                pixels_per_foot=6.8,
            )

    monkeypatch.setattr(
        "roof_measure.api_segmentation.MapboxReferenceProvider",
        FakeDetailProvider,
    )
    fitted, warning = _fit_segmentation_view_to_footprints(
        context=context,
        selected_components=selected_components,
        mapbox_token="mapbox-test-token",
    )

    assert fitted is not None
    assert 19.2 < requested["zoom"] < 19.4
    fitted_polygon = fitted.components[0]["polygon"]
    assert min(point["x"] for point in fitted_polygon) == pytest.approx(16, abs=1)
    assert max(point["x"] for point in fitted_polygon) == pytest.approx(84, abs=1)
    restored = _fitted_components_to_original(
        fitted.components,
        fitted_view=fitted,
        original_image_size=(100, 100),
    )
    for restored_point, original_point in zip(
        restored[0]["polygon"],
        selected_components[0]["polygon"],
        strict=True,
    ):
        assert restored_point["x"] == pytest.approx(original_point["x"])
        assert restored_point["y"] == pytest.approx(original_point["y"])
    assert "footprint-fitted" in warning


def test_focus_context_uses_footprint_only_for_camera_bounds(
    roof_context,
    monkeypatch,
) -> None:
    artifact_dir, context = roof_context

    class FakeDetailProvider:
        def __init__(self, _token: str):
            pass

        def static_satellite_image_at(self, **kwargs) -> MapboxStaticImage:
            return MapboxStaticImage(
                ok=True,
                image_bytes=_png_bytes(),
                latitude=float(kwargs["latitude"]),
                longitude=float(kwargs["longitude"]),
                zoom=float(kwargs["zoom"]),
                pixels_per_foot=6.8,
            )

    monkeypatch.setattr(
        "roof_measure.api_segmentation.MapboxReferenceProvider",
        FakeDetailProvider,
    )
    focused = focus_roof_measure_context(
        context_id=context["context_id"],
        selected_footprint_ids=["fp-01"],
        artifact_dir=artifact_dir,
        mapbox_token="mapbox-test-token",
    )

    assert focused["context_id"] != context["context_id"]
    assert focused["focus_source_context_id"] == context["context_id"]
    assert focused["focus_footprint_ids"] == ["fp-01"]
    assert focused["zoom"] > context["zoom"]
    assert focused["pixels_per_foot"] == 6.8
    assert focused["footprint_candidates"] == []
    assert focused["candidate_groups"] == []
    assert (artifact_dir / focused["context_id"] / "satellite.png").is_file()


def test_sam2_uses_fitted_source_then_preserves_context_coordinates(
    roof_context,
    monkeypatch,
) -> None:
    artifact_dir, context = roof_context

    class FakeDetailProvider:
        def __init__(self, _token: str):
            pass

        def static_satellite_image_at(self, **kwargs) -> MapboxStaticImage:
            return MapboxStaticImage(
                ok=True,
                image_bytes=_png_bytes(),
                latitude=float(kwargs["latitude"]),
                longitude=float(kwargs["longitude"]),
                zoom=float(kwargs["zoom"]),
                pixels_per_foot=6.8,
            )

    fitted_mask = np.zeros((100, 100), dtype=bool)
    fitted_mask[16:85, 16:85] = True

    class FakeSegmenter:
        def __init__(self, **_kwargs):
            pass

        def segment(self, image, prompts):
            assert image.shape[:2] == (100, 100)
            assert prompts.mask_input[16:85, 16:85].any()
            return SegmentationResult(
                candidates=[MaskCandidate(fitted_mask, 0.9, "fitted")],
                model_name="sam2_remote",
                model_version="test",
            )

    monkeypatch.setattr(
        "roof_measure.api_segmentation.MapboxReferenceProvider",
        FakeDetailProvider,
    )
    monkeypatch.setattr(
        "roof_measure.api_segmentation.Sam2RoofSegmenter",
        FakeSegmenter,
    )
    segmented = segment_roof_measure_context(
        context_id=context["context_id"],
        selected_footprint_ids=["fp-01"],
        sam2_url="http://sam2.test/segment",
        sam2_api_key="test-key",
        mapbox_token="mapbox-test-token",
        artifact_dir=artifact_dir,
    )

    assert segmented["source_view"] == "footprint_fitted"
    assert 19.2 < segmented["source_zoom"] < 19.4
    assert segmented["source_pixels_per_foot"] == 6.8
    stored = load_roof_measure_context(
        context_id=context["context_id"],
        artifact_dir=artifact_dir,
    )
    candidate = stored["sam2_candidates"][0]
    xs = [point["x"] for point in candidate["components"][0]["polygon"]]
    assert min(xs) == pytest.approx(40, abs=1)
    assert max(xs) == pytest.approx(60, abs=1)
    assert "display_components" not in candidate


def test_calculate_uses_reviewed_footprint_and_optional_pitch(roof_context) -> None:
    artifact_dir, context = roof_context

    result = calculate_roof_measurement(
        context_id=context["context_id"],
        selected_footprint_ids=["fp-01"],
        sections=[],
        pitch_rise_per_12=6,
        artifact_dir=artifact_dir,
    )

    assert result["total_plan_area_sqft"] == 100.0
    assert result["total_perimeter_ft"] == 40.0
    assert result["total_surface_area_sqft"] == 111.8
    assert result["review_status"] == "requires_estimator_verification"
    selected_overlay = resolve_roof_measure_asset(
        context_id=context["context_id"],
        asset_name=result["selected_overlay_asset_name"],
        artifact_dir=artifact_dir,
    )
    assert selected_overlay.is_file()


def test_sam2_refinement_ranks_all_candidates_and_calculates_confirmed_one(
    roof_context,
    monkeypatch,
) -> None:
    artifact_dir, context = roof_context
    exact = np.zeros((100, 100), dtype=bool)
    exact[40:61, 40:61] = True
    undersized = np.zeros((100, 100), dtype=bool)
    undersized[45:56, 45:56] = True
    oversized = np.zeros((100, 100), dtype=bool)
    oversized[30:71, 30:71] = True

    class FakeSegmenter:
        def __init__(self, **kwargs):
            assert kwargs["url"] == "http://sam2.test/segment"

        def segment(self, _image, prompts):
            assert prompts.box is not None
            assert prompts.positive_points
            assert prompts.mask_input is not None
            return SegmentationResult(
                candidates=[
                    MaskCandidate(undersized, 0.95, "undersized"),
                    MaskCandidate(exact, 0.82, "exact"),
                    MaskCandidate(oversized, 0.88, "oversized"),
                ],
                model_name="sam2_remote",
                model_version="test",
            )

    monkeypatch.setattr(
        "roof_measure.api_segmentation.Sam2RoofSegmenter",
        FakeSegmenter,
    )

    segmented = segment_roof_measure_context(
        context_id=context["context_id"],
        selected_footprint_ids=["fp-01"],
        sam2_url="http://sam2.test/segment",
        sam2_api_key="test-key",
        artifact_dir=artifact_dir,
    )

    assert len(segmented["candidates"]) == 1
    assert segmented["evaluated_candidate_count"] == 3
    assert segmented["requires_candidate_confirmation"] is False
    assert segmented["recommended_candidate_id"] == segmented["candidates"][0][
        "candidate_id"
    ]
    assert segmented["candidates"][0]["provider_rank"] == 2
    candidate_asset = resolve_roof_measure_asset(
        context_id=context["context_id"],
        asset_name=segmented["candidate_overlay_asset_name"],
        artifact_dir=artifact_dir,
    )
    assert candidate_asset.is_file()
    with Image.open(candidate_asset) as candidate_image:
        assert candidate_image.size == (1280, 1280)
    stored_context = load_roof_measure_context(
        context_id=context["context_id"],
        artifact_dir=artifact_dir,
    )
    assert len(stored_context["sam2_candidates"]) == 3

    result = calculate_roof_measurement(
        context_id=context["context_id"],
        selected_footprint_ids=[],
        sections=[],
        sam2_candidate_id=segmented["recommended_candidate_id"],
        pitch_rise_per_12=None,
        artifact_dir=artifact_dir,
    )

    assert result["measurement_basis"].startswith("sam2_refined")
    assert result["sections"][0]["source"] == "sam2_candidate"
    assert "no OpenAI API" in result["assumptions"][1]
    assert result["selected_overlay_asset_name"] == segmented[
        "candidate_overlay_asset_name"
    ]


def test_candidate_review_crop_focuses_small_roof_and_clamps_to_image() -> None:
    from roof_measure.api_segmentation import _candidate_review_crop_box

    centered = _candidate_review_crop_box(
        (1280, 1280),
        [
            {
                "components": [
                    {
                        "polygon": [
                            {"x": 600, "y": 600},
                            {"x": 700, "y": 600},
                            {"x": 700, "y": 700},
                            {"x": 600, "y": 700},
                        ]
                    }
                ]
            }
        ],
    )
    assert centered == (490, 490, 810, 810)

    near_edge = _candidate_review_crop_box(
        (1280, 1280),
        [
            {
                "components": [
                    {
                        "polygon": [
                            {"x": 10, "y": 20},
                            {"x": 80, "y": 20},
                            {"x": 80, "y": 90},
                            {"x": 10, "y": 90},
                        ]
                    }
                ]
            }
        ],
    )
    assert near_edge == (0, 0, 320, 320)


def test_sam2_refinement_fails_closed_when_service_fails(
    roof_context,
    monkeypatch,
) -> None:
    artifact_dir, context = roof_context

    class FailingSegmenter:
        def __init__(self, **_kwargs):
            pass

        def segment(self, _image, _prompts):
            raise RuntimeError("service unavailable")

    monkeypatch.setattr(
        "roof_measure.api_segmentation.Sam2RoofSegmenter",
        FailingSegmenter,
    )

    with pytest.raises(RuntimeError, match="service unavailable"):
        segment_roof_measure_context(
            context_id=context["context_id"],
            selected_footprint_ids=["fp-01"],
            sam2_url="http://sam2.test/segment",
            sam2_api_key="",
            artifact_dir=artifact_dir,
        )


def test_sam2_returns_guarded_orthogonal_candidate_with_original(
    roof_context,
    monkeypatch,
) -> None:
    artifact_dir, context = roof_context
    mask_image = Image.new("L", (100, 100), 0)
    ImageDraw.Draw(mask_image).polygon(
        [
            (26, 27),
            (50, 32),
            (73, 37),
            (70, 49),
            (67, 61),
            (44, 56),
            (21, 51),
            (23, 39),
        ],
        fill=255,
    )
    rotated_roof = np.asarray(mask_image, dtype=bool)

    class FakeSegmenter:
        def __init__(self, **_kwargs):
            pass

        def segment(self, _image, _prompts):
            return SegmentationResult(
                candidates=[MaskCandidate(rotated_roof, 0.9, "rotated")],
                model_name="sam2_remote",
                model_version="test",
            )

    monkeypatch.setattr(
        "roof_measure.api_segmentation.Sam2RoofSegmenter",
        FakeSegmenter,
    )

    segmented = segment_roof_measure_context(
        context_id=context["context_id"],
        selected_footprint_ids=["fp-01"],
        sam2_url="http://sam2.test/segment",
        sam2_api_key="test-key",
        artifact_dir=artifact_dir,
    )

    assert len(segmented["candidates"]) == 1
    assert segmented["evaluated_candidate_count"] == 2
    cleaned = segmented["candidates"][0]
    stored_context = load_roof_measure_context(
        context_id=context["context_id"],
        artifact_dir=artifact_dir,
    )
    original = stored_context["sam2_candidates"][1]
    assert cleaned["geometry_refinement"] == "dominant_orthogonal"
    assert cleaned["geometry_simplification_tolerance_pixels"] == 8.0
    assert original["geometry_refinement"] == "mask_polygon"
    assert cleaned["boundary_refinement"] == original["boundary_refinement"]
    assert cleaned["candidate_id"] != original["candidate_id"]
    assert cleaned["geometry_area_drift_fraction"] <= 0.015
    assert segmented["recommended_candidate_id"] == cleaned["candidate_id"]


def test_architectural_tolerance_uses_image_scale_and_lidar_cell_size() -> None:
    assert _architectural_simplification_tolerance_pixels(
        pixels_per_foot=0.5,
    ) == 4.0
    assert _architectural_simplification_tolerance_pixels(
        pixels_per_foot=2.0,
    ) == 8.0
    assert _architectural_simplification_tolerance_pixels(
        pixels_per_foot=2.0,
        lidar_cell_pixels=15,
    ) == 16.0
    assert _architectural_simplification_tolerance_pixels(
        pixels_per_foot=8.0,
        lidar_cell_pixels=40,
    ) == 20.0


def test_sam2_lidar_guidance_scores_elevated_edge_beyond_footprint(
    roof_context,
    monkeypatch,
) -> None:
    artifact_dir, context = roof_context
    context["lidar_coverage"]["asset_url"] = "https://lidar.test/roof.copc.laz"
    context_path = artifact_dir / context["context_id"] / "context.json"
    context_path.write_text(json.dumps(context), encoding="utf-8")

    height_grid = np.zeros((10, 10), dtype=float)
    height_grid[3:7, 4:7] = 12.0
    monkeypatch.setattr(
        "roof_measure.api_segmentation.kyfromabove_height_grid_for_image",
        lambda **_kwargs: LidarHeightGrid(
            height_grid=height_grid,
            cell_pixels=10,
            lidar_points=2000,
            image_points=800,
        ),
    )

    extended_roof = np.zeros((100, 100), dtype=bool)
    extended_roof[30:70, 40:70] = True
    # A non-elevated coarse transition cell extends from the connected roof
    # band. The guarded high-band alternative excludes it.
    extended_roof[40:50, 30:40] = True

    class FakeSegmenter:
        def __init__(self, **_kwargs):
            pass

        def segment(self, _image, prompts):
            assert prompts.mask_input is not None
            assert prompts.mask_input[40:61, 40:61].any()
            assert not prompts.mask_input[30:40, 30:80].any()
            assert prompts.positive_points
            return SegmentationResult(
                candidates=[MaskCandidate(extended_roof, 0.9, "extended")],
                model_name="sam2_remote",
                model_version="test",
            )

    monkeypatch.setattr(
        "roof_measure.api_segmentation.Sam2RoofSegmenter",
        FakeSegmenter,
    )

    segmented = segment_roof_measure_context(
        context_id=context["context_id"],
        selected_footprint_ids=["fp-01"],
        sam2_url="http://sam2.test/segment",
        sam2_api_key="test-key",
        artifact_dir=artifact_dir,
    )

    assert segmented["lidar_guidance_used"] is True
    assert segmented["lidar_points"] == 2000
    assert segmented["lidar_image_points"] == 800
    assert segmented["lidar_cell_pixels"] == 10
    stored_context = load_roof_measure_context(
        context_id=context["context_id"],
        artifact_dir=artifact_dir,
    )
    refinements = {
        item["boundary_refinement"]: item
        for item in stored_context["sam2_candidates"]
    }
    assert set(refinements) == {"sam2", "sam2_lidar_high_band"}
    assert (
        refinements["sam2_lidar_high_band"]["plan_area_sqft"]
        < refinements["sam2"]["plan_area_sqft"]
    )
    candidate = refinements["sam2_lidar_high_band"]
    assert candidate["area_ratio_to_footprint"] > 1.0
    assert candidate["lidar_roof_support_fraction"] == 1.0
    assert candidate["lidar_sampled_fraction"] == 1.0
    assert candidate["lidar_ground_fraction"] == 0.0
    assert candidate["lidar_elevated_coverage"] == 1.0
    assert candidate["lidar_boundary_score"] > 0.5
    assert candidate["lidar_roof_leakage_outside"] == 0.0


def test_candidate_selection_keeps_nearest_and_largest_buildings() -> None:
    from roof_measure.api_context import _bounded_candidate_set

    candidates = [
        {
            "source_footprint_id": f"source-{index}",
            "center_distance_pixels": float(index),
            "provider": "mapbox",
            "plan_area_sqft": float(100 + index),
            "center_x": float(index * 50),
            "center_y": 0.0,
        }
        for index in range(16)
    ]
    candidates[-1]["plan_area_sqft"] = 100_000.0

    selected = _bounded_candidate_set(candidates)

    assert len(selected) == 12
    assert candidates[-1] in selected
    assert candidates[0] in selected


def test_school_site_recommends_multi_building_group_not_address_point() -> None:
    from roof_measure.api_context import (
        _build_candidate_groups,
        _site_resolution_guidance,
    )

    def candidate(footprint_id: str, x: float, area: float) -> dict:
        return {
            "footprint_id": footprint_id,
            "center_distance_pixels": abs(640.0 - x),
            "plan_area_sqft": area,
            "perimeter_ft": 400.0,
            "components": [
                {
                    "polygon": [
                        {"x": x, "y": 300.0},
                        {"x": x + 100.0, "y": 300.0},
                        {"x": x + 100.0, "y": 400.0},
                        {"x": x, "y": 400.0},
                    ],
                    "holes": [],
                }
            ],
        }

    candidates = [
        candidate("fp-01", 100.0, 80_000.0),
        candidate("fp-02", 220.0, 40_000.0),
        candidate("fp-03", 340.0, 20_000.0),
        candidate("fp-04", 590.0, 35_000.0),
    ]

    groups = _build_candidate_groups(
        candidates,
        pixels_per_foot=1.0,
        image_width=1280,
        image_height=1280,
    )
    guidance = _site_resolution_guidance(
        groups,
        site_name="Example School",
        site_type="",
    )

    recommended = next(
        group
        for group in groups
        if group["group_id"] == guidance["recommended_candidate_group_id"]
    )
    assert recommended["footprint_ids"] == ["fp-01", "fp-02", "fp-03"]
    assert recommended["plan_area_sqft"] == 140_000.0
    assert "fp-04" not in recommended["footprint_ids"]
    assert guidance["requires_site_confirmation"] is True


def test_calculate_accepts_custom_polygon_and_does_not_invent_surface_area(
    roof_context,
) -> None:
    artifact_dir, context = roof_context

    result = calculate_roof_measurement(
        context_id=context["context_id"],
        selected_footprint_ids=[],
        sections=[
            {
                "section_id": "reviewed-main-roof",
                "polygon": [
                    {"x": 10, "y": 10},
                    {"x": 30, "y": 10},
                    {"x": 30, "y": 30},
                    {"x": 10, "y": 30},
                ],
                "holes": [],
            }
        ],
        pitch_rise_per_12=None,
        artifact_dir=artifact_dir,
    )

    assert result["total_plan_area_sqft"] == 100.0
    assert result["total_surface_area_sqft"] is None
    assert "plan-view area" in result["warnings"][-1]


def test_calculate_accepts_assistant_normalized_polygons(roof_context) -> None:
    artifact_dir, context = roof_context

    result = calculate_roof_measurement(
        context_id=context["context_id"],
        selected_footprint_ids=[],
        sections=[],
        normalized_sections=[
            {
                "section_id": "assistant-main-roof",
                "polygon": [
                    {"x": 0.1, "y": 0.1},
                    {"x": 0.3, "y": 0.1},
                    {"x": 0.3, "y": 0.3},
                    {"x": 0.1, "y": 0.3},
                ],
                "holes": [],
            }
        ],
        pitch_rise_per_12=None,
        artifact_dir=artifact_dir,
    )

    assert result["total_plan_area_sqft"] == 100.0
    assert result["sections"][0]["source"] == "assistant_polygon"
    assert result["measurement_basis"].startswith("assistant_traced_")
    assert "Assistant visually traced" in result["warnings"][0]


def test_calculate_rejects_overlapping_assistant_sections(roof_context) -> None:
    artifact_dir, context = roof_context

    with pytest.raises(RoofMeasureInputError, match="overlap"):
        calculate_roof_measurement(
            context_id=context["context_id"],
            selected_footprint_ids=[],
            sections=[],
            normalized_sections=[
                {
                    "section_id": "first",
                    "polygon": [
                        {"x": 0.1, "y": 0.1},
                        {"x": 0.4, "y": 0.1},
                        {"x": 0.4, "y": 0.4},
                        {"x": 0.1, "y": 0.4},
                    ],
                    "holes": [],
                },
                {
                    "section_id": "second",
                    "polygon": [
                        {"x": 0.3, "y": 0.3},
                        {"x": 0.6, "y": 0.3},
                        {"x": 0.6, "y": 0.6},
                        {"x": 0.3, "y": 0.6},
                    ],
                    "holes": [],
                },
            ],
            pitch_rise_per_12=None,
            artifact_dir=artifact_dir,
        )


def test_calculate_refits_final_overlay_to_custom_boundary(
    roof_context,
    monkeypatch,
) -> None:
    artifact_dir, context = roof_context
    requested: dict[str, float] = {}

    class FakeDetailProvider:
        def __init__(self, token: str):
            assert token == "mapbox-test-token"

        def static_satellite_image_at(self, **kwargs) -> MapboxStaticImage:
            requested.update(kwargs)
            return MapboxStaticImage(
                ok=True,
                image_bytes=_png_bytes(),
                latitude=float(kwargs["latitude"]),
                longitude=float(kwargs["longitude"]),
                zoom=float(kwargs["zoom"]),
                pixels_per_foot=6.8,
            )

    monkeypatch.setattr(
        "roof_measure.api_segmentation.MapboxReferenceProvider",
        FakeDetailProvider,
    )
    result = calculate_roof_measurement(
        context_id=context["context_id"],
        selected_footprint_ids=[],
        sections=[
            {
                "section_id": "reviewed-main-roof",
                "polygon": [
                    {"x": 40, "y": 40},
                    {"x": 60, "y": 40},
                    {"x": 60, "y": 60},
                    {"x": 40, "y": 60},
                ],
                "holes": [],
            }
        ],
        pitch_rise_per_12=None,
        artifact_dir=artifact_dir,
        mapbox_token="mapbox-test-token",
    )

    assert result["total_plan_area_sqft"] == 100.0
    assert result["source_view"] == "custom_boundary_fitted"
    assert 19.2 < result["source_zoom"] < 19.4
    assert result["source_pixels_per_foot"] == 6.8
    assert requested["zoom"] == pytest.approx(result["source_zoom"], abs=0.01)
    assert "re-centered" in result["assumptions"][-1]
    assert resolve_roof_measure_asset(
        context_id=context["context_id"],
        asset_name=result["selected_overlay_asset_name"],
        artifact_dir=artifact_dir,
    ).is_file()


def test_expired_context_cannot_be_reused(tmp_path) -> None:
    context_id = "b" * 32
    context_dir = tmp_path / context_id
    context_dir.mkdir()
    (context_dir / "context.json").write_text(
        '{"expires_at":1}',
        encoding="utf-8",
    )

    with pytest.raises(RoofMeasureContextExpiredError):
        load_roof_measure_context(context_id=context_id, artifact_dir=tmp_path)
