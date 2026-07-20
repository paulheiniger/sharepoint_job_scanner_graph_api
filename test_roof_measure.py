from __future__ import annotations

from io import BytesIO
import base64
import gzip
import json
import math
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image

from roof_measure.calibration import (
    clicked_known_length_calibration,
    detect_google_earth_scale_bar,
    detect_scale_bar_with_ai,
    feet_from_pixels,
    parse_scale_label_feet,
    sqft_from_pixels,
)
from roof_measure.ai_polygons import _focus_crop_box, polygon_suggestion_from_payload, suggest_refined_roof_polygons, suggest_roof_polygons
from roof_measure.ai_qa import RoofQaFinding, qa_corrections_to_prompts, qa_finding_from_payload
from roof_measure.ai_points import suggestion_from_payload
from roof_measure.ai_polygons import RoofPolygonSuggestion, _call_openai_roof_polygon_refiner, _call_openai_roof_polygon_suggester
from roof_measure.ai_raster_outline import _repair_short_boundary_gaps, _yellow_boundary_to_polygons
from roof_measure.ai_points import _call_openai_roof_point_suggester
from roof_measure.calibration import _call_openai_scale_reader
from roof_measure.confidence import measurement_warnings
from roof_measure.exports import measurement_to_geojson
from roof_measure.footprint_deformation import deform_footprints_to_roof_support
from roof_measure.geometry import polygon_area_pixels, repair_polygon, simplify_ring, straighten_architectural_ring
from roof_measure.image_io import image_hash, load_image_bytes
from roof_measure.lidar import LidarMaskAssessment, _height_grid_from_points, assess_mask_against_height_grid
from roof_measure.map_reference import (
    BuildingFootprint,
    _kyfromabove_lidar_coverage_from_payload,
    _microsoft_global_tile_features,
    _quadkey,
    footprint_rings_to_image_pixels,
    geojson_building_footprints,
)
from roof_measure.models import ImageMetadata
from roof_measure.ai_polygon_editor import PolygonEditSuggestion
from roof_measure.polygon_editor import apply_polygon_operations, sections_to_vertex_document
from roof_measure.polygonize import section_from_polygon, sections_from_mask
from roof_measure.segmentation import MockRoofSegmenter, Sam2RoofSegmenter, SegmentationPrompts
from roof_measure.service import _constrain_mask_to_footprints, _footprint_buffer_pixels, finalize_roof_sections, footprint_constraint_mask, measure_roof_from_outline_polygons, measure_roof_from_overhead_image, recalculate_report_from_corrected_sections, score_roof_result, sections_mask
from roof_measure.models import RoofMeasureRequest
from roof_measure.streamlit_page import (
    _canvas_background_image,
    _canvas_json_to_corner_edit_points,
    _canvas_json_to_points,
    _canvas_json_to_prompt_points,
    _canvas_json_to_sections,
    _canvas_json_to_corner_points,
    _format_points_text,
    _footprints_for_prompt_points,
    _footprint_visible_area_pixels,
    _footprint_rings_to_inference_pixels,
    _footprint_support_regression,
    _insert_new_corner_points,
    _lidar_core_cut,
    _lidar_ground_regression,
    _evaluate_ai_outline_candidate,
    _map_view_for_image_crop,
    _primary_prompt_cluster,
    _polygons_interior_prompt_points,
    _polygons_prompt_box,
    _parse_points_text,
    _prompt_points_to_canvas_initial_drawing,
    _points_to_canvas_initial_drawing,
    _replace_section_polygon,
    _raster_outline_is_prior_only,
    _qa_requires_manual_review,
    _qa_prompt_satisfaction,
    _run_ai_polygon_editor,
    _section_to_corner_canvas_initial_drawing,
    _sections_from_ai_polygons,
    _sections_to_canvas_initial_drawing,
    _targeted_qa_retry_is_accepted,
)
from roof_measure.visualization import prompt_points_overlay
from jobscan.env import load_project_env


def _image_bytes(size: tuple[int, int] = (100, 80), *, fmt: str = "PNG") -> bytes:
    image = Image.new("RGB", size, "white")
    buffer = BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


def _ring_has_no_crossing_edges(points: list[tuple[float, float]]) -> bool:
    vertices = points[:-1] if points and points[0] == points[-1] else points
    count = len(vertices)
    for index, start in enumerate(vertices):
        end = vertices[(index + 1) % count]
        for other_index in range(index + 1, count):
            if other_index in {index, (index + 1) % count} or index == (other_index + 1) % count:
                continue
            other_start = vertices[other_index]
            other_end = vertices[(other_index + 1) % count]
            if _segments_cross(start, end, other_start, other_end):
                return False
    return True


def _segments_cross(a, b, c, d) -> bool:  # noqa: ANN001
    def orientation(first, second, third):  # noqa: ANN001
        return (second[0] - first[0]) * (third[1] - first[1]) - (second[1] - first[1]) * (third[0] - first[0])

    return orientation(a, b, c) * orientation(a, b, d) < 0 and orientation(c, d, a) * orientation(c, d, b) < 0


def _google_earth_scale_image_bytes(size: tuple[int, int] = (600, 400), *, bar_pixels: int = 200) -> bytes:
    image = Image.new("RGB", size, "white")
    pixels = image.load()
    y = size[1] - 42
    x0 = 40
    x1 = x0 + bar_pixels
    for x in range(x0, x1):
        for dy in range(0, 4):
            pixels[x, y + dy] = (0, 0, 0)
    for x in (x0, x1 - 1):
        for yy in range(y - 8, y + 12):
            pixels[x, yy] = (0, 0, 0)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _mask_png_base64(mask: np.ndarray) -> str:
    image = Image.fromarray((mask.astype(np.uint8) * 255), mode="L")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def test_clicked_known_length_calibration_and_unit_conversion() -> None:
    calibration = clicked_known_length_calibration(
        point_a=(0, 0),
        point_b=(100, 0),
        length_feet=50,
    )

    assert calibration.pixels_per_foot == 2
    assert sqft_from_pixels(400, calibration.pixels_per_foot) == 100
    assert feet_from_pixels(40, calibration.pixels_per_foot) == 20


def test_parse_scale_label_feet_supports_common_google_earth_units() -> None:
    assert parse_scale_label_feet("100 ft") == 100
    assert parse_scale_label_feet("0.5 mi") == 2640
    assert round(parse_scale_label_feet("10 m") or 0, 3) == 32.808
    assert round(parse_scale_label_feet("1 km") or 0, 3) == 3280.84


def test_google_earth_scale_bar_detection_uses_label_hint(tmp_path) -> None:
    data = _google_earth_scale_image_bytes(bar_pixels=200)
    image = load_image_bytes(data, file_name="earth.png", storage_root=tmp_path).inference_image

    calibration = detect_google_earth_scale_bar(image, label_hint="100 ft")

    assert calibration.calibration_type == "scale_bar"
    assert calibration.length_feet == 100
    assert calibration.pixel_distance == 200
    assert calibration.pixels_per_foot == 2


def test_ai_scale_reader_provider_calibrates_from_crop_coordinates() -> None:
    image = Image.new("RGB", (600, 400), "white")

    calibration = detect_scale_bar_with_ai(
        image,
        provider=lambda crop: {
            "scale_label": "100 ft",
            "length_feet": 100,
            "bar_start": {"x": 40, "y": 98},
            "bar_end": {"x": 240, "y": 98},
            "confidence": "high",
        },
    )

    assert calibration.calibration_type == "scale_bar"
    assert calibration.length_feet == 100
    assert calibration.point_a == (40, 358)
    assert calibration.point_b == (240, 358)
    assert calibration.pixels_per_foot == 2


def test_google_earth_scale_bar_uses_ai_provider_when_ocr_fails() -> None:
    image = Image.new("RGB", (600, 400), "white")

    calibration = detect_google_earth_scale_bar(
        image,
        use_ai_fallback=True,
        ai_provider=lambda crop: {
            "scale_label": "50 ft",
            "bar_start": [25, 90],
            "bar_end": [125, 90],
            "confidence": "medium",
        },
    )

    assert calibration.calibration_type == "scale_bar"
    assert calibration.length_feet == 50
    assert calibration.pixels_per_foot == 2


def test_polygon_area_supports_holes() -> None:
    outer = [(0, 0), (100, 0), (100, 100), (0, 100)]
    hole = [(40, 40), (60, 40), (60, 60), (40, 60)]

    assert polygon_area_pixels(outer, [hole]) == 9600


def test_repair_and_simplify_ring_close_polygon() -> None:
    noisy = [(0, 0), (20, 0), (40, 0), (40, 40), (0, 40)]

    repaired = repair_polygon(noisy)
    simplified = simplify_ring(noisy, tolerance=2)

    assert repaired[0] == repaired[-1]
    assert simplified[0] == simplified[-1]
    assert len(simplified) < len(repaired)


def test_simplify_ring_removes_stair_step_vertices_from_a_closed_boundary() -> None:
    stair_step = [(0, 0), (20, 1), (40, 0), (41, 20), (40, 40), (20, 39), (0, 40), (-1, 20)]

    simplified = simplify_ring(stair_step, tolerance=3)

    assert len(simplified) == 5
    assert polygon_area_pixels(simplified) == 1600


def test_straighten_architectural_ring_fits_rotated_orthogonal_edges_and_preserves_area() -> None:
    jagged = [
        (10, 11),
        (50, 20),
        (89, 29),
        (84, 49),
        (79, 69),
        (40, 60),
        (1, 51),
        (5, 31),
    ]

    straightened = straighten_architectural_ring(jagged)

    original_area = polygon_area_pixels(jagged)
    straightened_area = polygon_area_pixels(straightened)
    assert straightened[0] == straightened[-1]
    assert abs(straightened_area - original_area) / original_area <= 0.03
    edge_angles = [
        math.atan2(
            straightened[index + 1][1] - straightened[index][1],
            straightened[index + 1][0] - straightened[index][0],
        )
        for index in range(len(straightened) - 1)
    ]
    assert all(
        min(abs((angle - edge_angles[0] + math.pi / 2) % math.pi - math.pi / 2),
            abs((angle - edge_angles[0]) % (math.pi / 2))) < math.radians(2)
        for angle in edge_angles
    )


def test_sections_from_mask_detects_multiple_sections() -> None:
    mask = np.zeros((80, 100), dtype=bool)
    mask[10:30, 20:50] = True
    mask[45:65, 60:90] = True

    sections = sections_from_mask(mask, minimum_section_area_pixels=100)

    assert len(sections) == 2
    assert [section.area_pixels for section in sections] == [600, 600]


def test_sections_from_mask_traces_component_boundary_not_bounding_box() -> None:
    mask = np.zeros((80, 100), dtype=bool)
    mask[10:50, 20:40] = True
    mask[30:50, 40:70] = True

    sections = sections_from_mask(mask, minimum_section_area_pixels=100, simplification_tolerance=0)

    assert len(sections) == 1
    section = sections[0]
    assert section.area_pixels == 1400
    assert polygon_area_pixels(section.polygon, section.holes) == 1400
    assert polygon_area_pixels([(20, 10), (70, 10), (70, 50), (20, 50)]) == 2000
    assert len(section.polygon) > 5


def test_sections_from_mask_simplifies_noisy_rectangular_roof_boundary() -> None:
    mask = np.zeros((100, 120), dtype=bool)
    mask[20:80, 30:90] = True
    mask[18:20, 48:52] = True
    mask[80:82, 68:72] = True
    mask[44:48, 28:30] = True
    mask[54:58, 90:92] = True

    sections = sections_from_mask(mask, minimum_section_area_pixels=100, simplification_tolerance=8)

    assert len(sections) == 1
    assert len(sections[0].polygon) == 5
    assert abs(polygon_area_pixels(sections[0].polygon) - float(mask.sum())) / float(mask.sum()) < 0.02


def test_sections_from_mask_orders_complex_boundary_without_crossing_edges() -> None:
    mask = np.zeros((100, 100), dtype=bool)
    mask[15:70, 20:45] = True
    mask[45:70, 45:80] = True
    mask[25:40, 45:65] = True

    sections = sections_from_mask(mask, minimum_section_area_pixels=100, simplification_tolerance=1)

    assert len(sections) == 1
    polygon = sections[0].polygon
    assert polygon_area_pixels(polygon) == float(mask.sum())
    assert _ring_has_no_crossing_edges(polygon)


def test_mapbox_footprint_projection_centers_coordinates_on_static_image() -> None:
    projected = footprint_rings_to_image_pixels(
        [[(-84.0, 38.0), (-83.9999, 38.0), (-83.9999, 38.0001)]],
        center_latitude=38.0,
        center_longitude=-84.0,
        zoom=18,
        width=1200,
        height=1200,
    )

    assert projected[0][0] == (600.0, 600.0)
    assert projected[0][1][0] > 600
    assert projected[0][2][1] < 600


def test_uploaded_geojson_building_footprint_supports_polygon_and_multipolygon() -> None:
    lookup = geojson_building_footprints(
        '{"type":"FeatureCollection","features":[{"type":"Feature","properties":{"name":"Main"},"geometry":{"type":"Polygon","coordinates":[[[-84,38],[-83.9,38],[-83.9,38.1],[-84,38]]]}},{"type":"Feature","geometry":{"type":"MultiPolygon","coordinates":[[[[-84.2,38],[-84.1,38],[-84.1,38.1],[-84.2,38]]]]}}]}'
    )

    assert lookup.ok
    assert len(lookup.footprints) == 2
    assert lookup.footprints[0].label == "Main"
    assert len(lookup.footprints[1].rings) == 1


def test_microsoft_global_tile_parser_filters_nearby_footprints() -> None:
    payload = "\n".join(
        [
            '{"type":"Feature","properties":{"confidence":0.9},"geometry":{"type":"Polygon","coordinates":[[[-84.001,38],[-83.999,38],[-83.999,38.001],[-84.001,38]]]}}',
            '{"type":"Feature","properties":{},"geometry":{"type":"Polygon","coordinates":[[[-85,39],[-84.9,39],[-84.9,39.1],[-85,39]]]}}',
        ]
    ).encode("utf-8")

    footprints = _microsoft_global_tile_features(
        gzip.compress(payload),
        latitude=38.0,
        longitude=-84.0,
        radius_meters=500,
        limit=10,
    )

    assert _quadkey(37.97867, -84.192173, zoom=9) == "032001202"
    assert len(footprints) == 1
    assert footprints[0].provider == "microsoft_global_ml"


def test_kyfromabove_lidar_coverage_prefers_newest_phase_with_pointcloud_asset() -> None:
    coverage = _kyfromabove_lidar_coverage_from_payload(
        {
            "features": [
                {
                    "collection": "laz-phase2",
                    "properties": {"datetime": "2024-01-22T00:00:00Z", "pc:count": 12_345},
                    "assets": {"pointcloud": {"href": "https://example.test/phase2.copc.laz"}},
                },
                {
                    "collection": "laz-phase1",
                    "properties": {"datetime": "2020-01-01T00:00:00Z", "pc:count": 1},
                    "assets": {"pointcloud": {"href": "https://example.test/phase1.laz"}},
                },
            ]
        }
    )

    assert coverage.ok
    assert coverage.collection == "laz-phase2"
    assert coverage.point_count == 12_345


def test_visible_footprint_area_prefers_large_candidate_inside_map() -> None:
    small = BuildingFootprint(
        footprint_id="small",
        label="small",
        rings=[[(-84.0000, 38.0000), (-83.9999, 38.0000), (-83.9999, 38.0001), (-84.0000, 38.0001)]],
    )
    large = BuildingFootprint(
        footprint_id="large",
        label="large",
        rings=[[(-84.0005, 37.9995), (-83.9995, 37.9995), (-83.9995, 38.0005), (-84.0005, 38.0005)]],
    )
    kwargs = {
        "center_latitude": 38.0,
        "center_longitude": -84.0,
        "zoom": 19.0,
        "width": 1280,
        "height": 1280,
    }
    assert _footprint_visible_area_pixels(large, **kwargs) > _footprint_visible_area_pixels(small, **kwargs)


def test_footprint_projection_applies_native_mapbox_resize_scale() -> None:
    rings = [[(-84.001, 38.0), (-83.999, 38.0), (-83.999, 38.001)]]
    native = footprint_rings_to_image_pixels(
        rings,
        center_latitude=38.0,
        center_longitude=-84.0,
        zoom=19.0,
        width=1280,
        height=1280,
    )
    inferred = _footprint_rings_to_inference_pixels(
        rings,
        center_latitude=38.0,
        center_longitude=-84.0,
        zoom=19.0,
        source_width=1280,
        source_height=1280,
        scale_x=0.9375,
        scale_y=0.9375,
    )

    assert inferred[0][0] == (native[0][0][0] * 0.9375, native[0][0][1] * 0.9375)


def test_footprints_for_prompt_points_ignores_larger_unrelated_building() -> None:
    school = [(100, 100), (300, 100), (300, 300), (100, 300)]
    warehouse = [(600, 100), (1100, 100), (1100, 600), (600, 600)]

    selected = _footprints_for_prompt_points([school, warehouse], [(180, 180), (240, 240)])

    assert selected == [school]


def test_selected_footprint_constrains_segmentation_mask() -> None:
    mask = np.ones((30, 30), dtype=bool)

    constrained = _constrain_mask_to_footprints(
        mask,
        [[(8, 8), (20, 8), (20, 20), (8, 20)]],
        buffer_pixels=0,
    )

    assert constrained.sum() == 169
    assert constrained[14, 14]
    assert not constrained[4, 4]


def test_footprint_deformation_preserves_gap_between_supported_buildings() -> None:
    image = np.full((120, 160, 3), 120, dtype=np.uint8)
    mask = np.zeros((120, 160), dtype=bool)
    first = [(20.0, 20.0), (65.0, 20.0), (65.0, 90.0), (20.0, 90.0)]
    second = [(90.0, 20.0), (140.0, 20.0), (140.0, 90.0), (90.0, 90.0)]
    mask[20:91, 20:66] = True
    mask[20:91, 90:141] = True

    candidate = deform_footprints_to_roof_support([first, second], image=image, sam_mask=mask)

    assert candidate.accepted
    assert len(candidate.polygons) == 2
    assert candidate.sam_support > 0.9
    assert len(candidate.edge_diagnostics) == 2
    assert candidate.edge_diagnostics[0][0]["components"]["roof_support_inside"] >= 0
    assert max(x for x, _ in candidate.polygons[0]) < min(x for x, _ in candidate.polygons[1])


def test_footprint_deformation_records_local_lidar_edge_evidence() -> None:
    image = np.full((120, 160, 3), 120, dtype=np.uint8)
    mask = np.zeros((120, 160), dtype=bool)
    footprint = [(20.0, 20.0), (65.0, 20.0), (65.0, 90.0), (20.0, 90.0)]
    mask[20:91, 20:66] = True
    height_grid = np.zeros((15, 20), dtype=float)
    height_grid[3:11, 3:8] = 12.0

    candidate = deform_footprints_to_roof_support(
        [footprint],
        image=image,
        sam_mask=mask,
        lidar_height_grid=height_grid,
        lidar_cell_pixels=8,
    )

    components = candidate.edge_diagnostics[0][0]["components"]
    assert components["lidar_available"] == 1.0
    assert components["lidar_roof_inside"] > 0.0
    assert components["lidar_ground_outside"] > 0.0


def test_atomic_polygon_editor_applies_valid_move_and_rejects_overlap() -> None:
    first = section_from_polygon("first", [(10, 10), (40, 10), (40, 40), (10, 40)])
    second = section_from_polygon("second", [(60, 10), (90, 10), (90, 40), (60, 40)])
    moved = apply_polygon_operations(
        [first, second],
        [{"op": "move_vertex", "polygon_id": "first", "vertex_index": 0, "x": 12, "y": 12}],
        image_size=(100, 100),
    )
    assert len(moved.applied_operations) == 1
    assert moved.sections[0].polygon[0] == (12.0, 12.0)

    overlapping = apply_polygon_operations(
        [first, second],
        [{"op": "move_vertex", "polygon_id": "first", "vertex_index": 1, "x": 80, "y": 10}],
        image_size=(100, 100),
    )
    assert not overlapping.applied_operations
    assert overlapping.rejected_operations


def test_polygon_editor_document_preserves_hole_identity() -> None:
    section = section_from_polygon(
        "main",
        [(10, 10), (90, 10), (90, 90), (10, 90)],
        holes=[[(40, 40), (60, 40), (60, 60), (40, 60)]],
    )
    document = sections_to_vertex_document([section])

    assert document[0]["polygon_id"] == "main"
    assert document[0]["holes"][0]["hole_id"] == "main:hole:0"
    changed = apply_polygon_operations(
        [section],
        [{"op": "modify_hole_vertex", "polygon_id": "main", "hole_id": "main:hole:0", "vertex_index": 0, "x": 42, "y": 42}],
        image_size=(100, 100),
    )
    assert len(changed.applied_operations) == 1
    assert changed.sections[0].holes[0][0] == (42.0, 42.0)


def test_ai_polygon_editor_scores_accepted_atomic_edit_without_missing_score_arguments() -> None:
    image = Image.new("RGB", (100, 100), (128, 128, 128))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    result = measure_roof_from_outline_polygons(
        image_bytes=buffer.getvalue(),
        request=RoofMeasureRequest(overhead_image_name="editor.png", metadata_pixels_per_foot=1.0),
        polygons=[[(10, 10), (90, 10), (90, 90), (10, 90)]],
    )
    result.selected_mask = np.ones((100, 100), dtype=bool)
    suggestion = PolygonEditSuggestion(
        operations=[{"op": "move_vertex", "polygon_id": "section-1", "vertex_index": 0, "x": 12, "y": 12}],
        confidence=0.9,
    )
    with patch("roof_measure.streamlit_page.suggest_polygon_operations", return_value=suggestion):
        edited, notes = _run_ai_polygon_editor(result, image=image, lidar_asset_url="", run_semantic_analysis=False)

    assert any("applied 3 validated vertex edits" in note for note in notes)
    assert edited.deterministic_score > 0
    assert any(item.get("stage") == "ai_polygon_editor" for item in edited.report.processing_iterations)


def test_ai_outline_prior_constrains_segmented_mask(tmp_path) -> None:
    mask = np.ones((100, 100), dtype=bool)
    request = RoofMeasureRequest(
        overhead_image_name="roof.png",
        metadata_pixels_per_foot=1.0,
        minimum_section_area_pixels=1,
        outline_prior_polygons=[[(20, 20), (80, 20), (80, 80), (20, 80)]],
        outline_prior_buffer_pixels=0,
    )

    result = measure_roof_from_overhead_image(
        image_bytes=_image_bytes((100, 100)),
        request=request,
        segmenter=MockRoofSegmenter([mask]),
        storage_root=str(tmp_path),
    )

    assert result.selected_mask is not None
    assert result.selected_mask.sum() == 3721
    assert result.applied_outline_prior_polygons == request.outline_prior_polygons
    assert result.outline_prior_buffer_pixels == 0


def test_footprint_buffer_uses_metadata_calibration() -> None:
    request = RoofMeasureRequest(
        overhead_image_name="roof.png",
        metadata_pixels_per_foot=1.5,
        footprint_buffer_feet=10,
    )

    assert _footprint_buffer_pixels(request) == 15


def test_measurement_result_retains_applied_footprint_provenance(tmp_path) -> None:
    polygon = [(20, 20), (80, 20), (80, 60), (20, 60)]
    request = RoofMeasureRequest(
        overhead_image_name="roof.png",
        metadata_pixels_per_foot=1.0,
        footprint_buffer_feet=5,
        footprint_polygons=[polygon],
        footprint_source_records=[
            {
                "footprint_id": "microsoft-123",
                "label": "Clark County Schools",
                "provider": "microsoft_global_buildings",
                "attribution": "Microsoft",
                "geographic_rings": [[(-84.1, 38.1), (-84.0, 38.1), (-84.0, 38.0)]],
                "image_polygons": [polygon],
            }
        ],
    )
    mask = np.ones((80, 100), dtype=bool)

    result = measure_roof_from_overhead_image(
        image_bytes=_image_bytes(),
        request=request,
        segmenter=MockRoofSegmenter([mask]),
        storage_root=str(tmp_path),
    )

    assert result.footprint_buffer_pixels == 5
    assert result.footprint_audit[0]["footprint_id"] == "microsoft-123"
    assert result.footprint_audit[0]["provider"] == "microsoft_global_buildings"
    assert result.footprint_audit[0]["buffer_feet"] == 5
    assert footprint_constraint_mask(mask.shape, [polygon], buffer_pixels=5).sum() > 0


def test_semantic_qa_defects_become_targeted_sam_prompts() -> None:
    finding = qa_finding_from_payload(
        {
            "missing_regions": [{"x": 20, "y": 30}],
            "extra_regions": [{"x": 60, "y": 30}],
            "courtyard_errors": [{"x": 40, "y": 40}],
            "ground_gaps": [{"x": 45, "y": 55}],
            "boundary_errors": [{"x": 10, "y": 10}],
            "confidence": 0.91,
        },
        width=100,
        height=80,
    )

    positive, negative = qa_corrections_to_prompts(finding)

    assert positive == [(20.0, 30.0)]
    assert negative == [(60.0, 30.0), (40.0, 40.0), (45.0, 55.0)]
    assert finding.ground_gaps == [(45.0, 55.0)]
    assert finding.boundary_errors == [(10.0, 10.0)]
    assert finding.confidence == 0.91


def test_semantic_qa_requires_manual_review_only_for_high_confidence_major_conflict() -> None:
    severe = RoofQaFinding(
        missing_regions=[(10, 10), (20, 20)],
        extra_regions=[(70, 10), (80, 20)],
        confidence=0.9,
    )
    ambiguous = RoofQaFinding(confidence=0.9, warnings=["Interior transitions are ambiguous."])

    assert _qa_requires_manual_review(severe)
    assert not _qa_requires_manual_review(ambiguous)


def test_targeted_qa_retry_can_improve_semantic_corrections_without_matching_initial_mask() -> None:
    initial = np.zeros((80, 80), dtype=bool)
    initial[20:60, 20:60] = True
    retry = initial.copy()
    retry[25:35, 25:35] = True
    retry[45:55, 45:55] = False
    positive = [(30.0, 30.0)]
    negative = [(50.0, 50.0)]

    initial_score = _qa_prompt_satisfaction(initial, positive, negative, radius_pixels=3)
    retry_score = _qa_prompt_satisfaction(retry, positive, negative, radius_pixels=3)

    assert initial_score == 0.5
    assert retry_score == 1.0
    assert _targeted_qa_retry_is_accepted(
        has_sections=True,
        deterministic_score_delta=-0.02,
        qa_score_delta=retry_score - initial_score,
        qa_confidence=0.76,
        lidar_core_cut=False,
        lidar_ground_regression=False,
    )


def test_targeted_qa_retry_rejects_ground_regression() -> None:
    initial = LidarMaskAssessment(ok=True, ground_fraction=0.08)
    retry = LidarMaskAssessment(ok=True, ground_fraction=0.16)

    assert _lidar_ground_regression(initial, retry)
    assert not _targeted_qa_retry_is_accepted(
        has_sections=True,
        deterministic_score_delta=0.02,
        qa_score_delta=0.5,
        qa_confidence=0.9,
        lidar_core_cut=False,
        lidar_ground_regression=True,
    )


def test_targeted_qa_retry_requires_actual_prompt_correction() -> None:
    assert not _targeted_qa_retry_is_accepted(
        has_sections=True,
        deterministic_score_delta=0.06,
        qa_score_delta=0.0,
        qa_confidence=0.9,
        lidar_core_cut=False,
        lidar_ground_regression=False,
    )


def test_qa_retry_rejects_collapse_of_accepted_footprint_support() -> None:
    initial = {"accepted": True, "sam_support": 0.96}
    retry = {"accepted": False, "sam_support": 0.40}

    assert _footprint_support_regression(initial, retry)
    assert not _targeted_qa_retry_is_accepted(
        has_sections=True,
        deterministic_score_delta=0.02,
        qa_score_delta=0.25,
        qa_confidence=0.8,
        lidar_core_cut=False,
        lidar_ground_regression=False,
        footprint_support_regression=True,
    )


def test_deterministic_score_penalizes_fragmented_sections() -> None:
    mask = np.ones((20, 20), dtype=bool)
    whole = [section_from_polygon("main", [(1, 1), (18, 1), (18, 18), (1, 18)])]
    fragmented = [
        section_from_polygon("one", [(1, 1), (7, 1), (7, 7), (1, 7)]),
        section_from_polygon("two", [(11, 11), (18, 11), (18, 18), (11, 18)]),
    ]

    assert score_roof_result(mask, whole, []) > score_roof_result(mask, fragmented, [])


def test_deterministic_score_heavily_penalizes_polygon_cutting_roof_core() -> None:
    mask = np.zeros((80, 80), dtype=bool)
    mask[10:70, 10:70] = True
    whole = [section_from_polygon("whole", [(10, 10), (70, 10), (70, 70), (10, 70)])]
    cut_through_middle = [section_from_polygon("left", [(10, 10), (37, 10), (37, 70), (10, 70)])]

    assert score_roof_result(mask, whole, []) > score_roof_result(mask, cut_through_middle, []) + 0.1


def test_scored_finalizer_preserves_constrained_sam_mask_core() -> None:
    mask = np.zeros((100, 100), dtype=bool)
    mask[15:85, 15:85] = True
    raw_sections = [section_from_polygon("main", [(15, 15), (85, 15), (85, 85), (15, 85)])]
    outline = [[(12, 12), (88, 12), (88, 88), (12, 88)]]

    final_sections, record = finalize_roof_sections(mask, raw_sections, outline_prior_polygons=outline)

    final_mask = sections_mask(mask.shape, final_sections)
    assert record["candidate"] in {"raw_mask", "topology_clean", "architectural_fit"}
    assert float((final_mask & mask).sum()) / float(mask.sum()) >= 0.96


def test_report17_regression_fixture_records_missing_footprint_and_active_outline_prior() -> None:
    fixture_path = Path(__file__).parent / "tests" / "fixtures" / "roof_measure_report17.json"
    payload = json.loads(fixture_path.read_text())
    stages = {item["stage"]: item for item in payload["processing_iterations"]}

    assert stages["ai_outline_prior"]["accepted"]
    assert stages["initial_segmentation"]["footprint_count"] == 0
    assert stages["initial_segmentation"]["outline_prior_polygon_count"] == 6
    assert stages["lidar_height_prior"]["roof_support_fraction"] > 0.85


def test_lidar_height_prior_distinguishes_elevated_roof_from_ground() -> None:
    height_grid = np.array([[12.0, 1.0], [12.0, 1.0]])
    roof_mask = np.zeros((16, 16), dtype=bool)
    roof_mask[:, :8] = True
    ground_mask = np.zeros((16, 16), dtype=bool)
    ground_mask[:, 8:] = True

    roof = assess_mask_against_height_grid(roof_mask, height_grid, cell_pixels=8)
    ground = assess_mask_against_height_grid(ground_mask, height_grid, cell_pixels=8)

    assert roof.ok and roof.roof_support_fraction == 1.0 and roof.ground_fraction == 0.0
    assert ground.ok and ground.roof_support_fraction == 0.0 and ground.ground_fraction == 1.0


def test_lidar_elevated_core_retention_penalizes_polygon_cutting_roof() -> None:
    source_mask = np.ones((32, 32), dtype=bool)
    candidate_mask = np.zeros((32, 32), dtype=bool)
    candidate_mask[:, :16] = True
    height_grid = np.full((4, 4), 12.0)

    assessment = assess_mask_against_height_grid(
        source_mask,
        height_grid,
        cell_pixels=8,
        candidate_mask=candidate_mask,
    )

    assert assessment.ok
    assert assessment.elevated_core_retention is not None
    assert assessment.elevated_core_retention < 0.75


def test_lidar_core_cut_requires_material_loss_of_elevated_roof_interior() -> None:
    initial = LidarMaskAssessment(ok=True, elevated_core_retention=0.99)
    retry = LidarMaskAssessment(ok=True, elevated_core_retention=0.85)

    assert _lidar_core_cut(initial, retry)
    assert not _lidar_core_cut(initial, LidarMaskAssessment(ok=True, elevated_core_retention=0.96))


def test_lidar_height_grid_uses_classified_ground_as_local_elevation_baseline() -> None:
    grid = _height_grid_from_points(
        np.array([3.0, 3.0, 11.0]),
        np.array([3.0, 11.0, 11.0]),
        np.array([112.0, 100.0, 100.0]),
        np.array([1, 2, 2]),
        (16, 16),
        cell_pixels=8,
    )

    assert np.isfinite(grid[0, 0])
    assert grid[0, 0] == 12.0


def test_duplicate_image_detection_updates_seen_hashes(tmp_path) -> None:
    data = _image_bytes()
    seen: set[str] = set()

    first = load_image_bytes(data, file_name="roof.png", storage_root=tmp_path, seen_hashes=seen)
    second = load_image_bytes(data, file_name="roof.png", storage_root=tmp_path, seen_hashes=seen)

    assert image_hash(data) in seen
    assert first.metadata.duplicate is False
    assert second.metadata.duplicate is True


def test_geojson_export_includes_roof_sections() -> None:
    section = section_from_polygon("main", [(0, 0), (10, 0), (10, 10), (0, 10)])
    section.area_sqft = 25
    section.perimeter_ft = 20
    calibration = clicked_known_length_calibration(point_a=(0, 0), point_b=(10, 0), length_feet=5)
    warnings = measurement_warnings(
        calibration=calibration,
        sections=[section],
        image_metadata=ImageMetadata(
            image_id="img",
            file_name="roof.png",
            width=100,
            height=100,
            inference_width=100,
            inference_height=100,
            content_hash="hash",
        ),
    )
    from roof_measure.models import RoofMeasurement

    geojson = measurement_to_geojson(
        RoofMeasurement(
            total_area_sqft=25,
            total_perimeter_ft=20,
            sections=[section],
            calibration=calibration,
            warnings=warnings,
        )
    )

    assert geojson["type"] == "FeatureCollection"
    assert geojson["features"][0]["properties"]["section_id"] == "main"
    assert geojson["features"][0]["geometry"]["type"] == "Polygon"


def test_exif_orientation_is_applied(tmp_path) -> None:
    image = Image.new("RGB", (20, 40), "white")
    exif = image.getexif()
    exif[274] = 6
    buffer = BytesIO()
    image.save(buffer, format="JPEG", exif=exif)

    loaded = load_image_bytes(buffer.getvalue(), file_name="rotated.jpg", storage_root=tmp_path)

    assert loaded.metadata.width == 40
    assert loaded.metadata.height == 20
    assert loaded.metadata.exif_orientation_applied is True


def test_measurement_service_uses_mock_segmentation_and_calibration(tmp_path) -> None:
    mask = np.zeros((80, 100), dtype=bool)
    mask[10:30, 20:50] = True
    request = RoofMeasureRequest(
        overhead_image_name="roof.png",
        positive_points=[(35, 20)],
        calibration_length_feet=5,
        calibration_point_a=(0, 0),
        calibration_point_b=(10, 0),
        minimum_section_area_pixels=100,
    )

    result = measure_roof_from_overhead_image(
        image_bytes=_image_bytes(),
        request=request,
        segmenter=MockRoofSegmenter([mask]),
        storage_root=str(tmp_path),
    )

    measurement = result.report.measurement
    assert result.candidate_count == 1
    assert measurement.total_area_sqft == 150
    assert measurement.total_perimeter_ft == 50
    assert measurement.confidence["segmentation"] == 0.9


def test_remote_sam2_segmenter_posts_prompts_and_decodes_masks(monkeypatch) -> None:
    image = np.zeros((40, 50, 3), dtype=np.uint8)
    mask = np.zeros((40, 50), dtype=bool)
    mask[10:20, 15:30] = True

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "model_name": "sam2_remote",
                "model_version": "test",
                "warnings": ["test warning"],
                "candidates": [
                    {
                        "label": "roof",
                        "score": 0.88,
                        "mask_png_base64": _mask_png_base64(mask),
                        "reasons": ["prompt segmentation"],
                    }
                ],
            }

    def fake_post(url, json, timeout):  # noqa: ANN001
        assert url == "http://127.0.0.1:8765/segment"
        assert timeout == 90
        assert json["positive_points"] == [(20.0, 15.0)]
        assert json["negative_points"] == [(3.0, 4.0)]
        assert json["box"] == (10.0, 8.0, 35.0, 28.0)
        assert json["mask_input_png_base64"]
        assert json["max_candidates"] == 3
        assert json["image_png_base64"]
        return FakeResponse()

    monkeypatch.setenv("SAM2_SEGMENTATION_URL", "http://127.0.0.1:8765/segment")
    monkeypatch.setattr("roof_measure.segmentation.requests.post", fake_post)

    result = Sam2RoofSegmenter().segment(
        image,
        SegmentationPrompts(
            positive_points=[(20.0, 15.0)],
            negative_points=[(3.0, 4.0)],
            box=(10.0, 8.0, 35.0, 28.0),
            mask_input=mask,
        ),
    )

    assert result.model_name == "sam2_remote"
    assert result.model_version == "test"
    assert result.warnings == ["test warning"]
    assert len(result.candidates) == 1
    assert result.candidates[0].score == 0.88
    assert np.array_equal(result.candidates[0].mask, mask)


def test_ai_outline_prior_creates_padded_box_and_interior_prompt_points() -> None:
    polygons = [
        [(20.0, 20.0), (60.0, 20.0), (60.0, 45.0), (20.0, 45.0)],
        [(70.0, 50.0), (95.0, 50.0), (95.0, 85.0), (70.0, 85.0)],
    ]

    assert _polygons_prompt_box(polygons, (100, 100), padding_pixels=5) == (15.0, 15.0, 99.0, 90.0)
    assert _polygons_interior_prompt_points(polygons, (100, 100)) == [(40.0, 32.0), (82.0, 67.0)]


def test_measurement_service_uses_scale_bar_when_manual_calibration_missing(tmp_path) -> None:
    mask = np.zeros((400, 600), dtype=bool)
    mask[100:300, 100:300] = True
    request = RoofMeasureRequest(
        overhead_image_name="earth.png",
        positive_points=[(200, 200)],
        scale_bar_label_hint="100 ft",
        minimum_section_area_pixels=100,
    )

    result = measure_roof_from_overhead_image(
        image_bytes=_google_earth_scale_image_bytes(bar_pixels=200),
        request=request,
        segmenter=MockRoofSegmenter([mask]),
        storage_root=str(tmp_path),
    )

    measurement = result.report.measurement
    assert measurement.calibration.calibration_type == "scale_bar"
    assert measurement.calibration.pixels_per_foot == 2
    assert measurement.total_area_sqft == 10000


def test_missing_calibration_warns_and_omits_area(tmp_path) -> None:
    request = RoofMeasureRequest(
        overhead_image_name="roof.png",
        positive_points=[(50, 40)],
        minimum_section_area_pixels=100,
    )

    result = measure_roof_from_overhead_image(
        image_bytes=_image_bytes(),
        request=request,
        segmenter=MockRoofSegmenter(),
        storage_root=str(tmp_path),
    )

    measurement = result.report.measurement
    assert measurement.total_area_sqft is None
    assert any(warning.code == "missing_calibration" for warning in measurement.warnings)


def test_corrected_polygon_recalculates_measurement_from_vertices(tmp_path) -> None:
    mask = np.zeros((80, 100), dtype=bool)
    mask[10:30, 20:50] = True
    request = RoofMeasureRequest(
        overhead_image_name="roof.png",
        positive_points=[(35, 20)],
        calibration_length_feet=5,
        calibration_point_a=(0, 0),
        calibration_point_b=(10, 0),
        minimum_section_area_pixels=100,
    )
    result = measure_roof_from_overhead_image(
        image_bytes=_image_bytes(),
        request=request,
        segmenter=MockRoofSegmenter([mask]),
        storage_root=str(tmp_path),
    )
    corrected_section = result.report.measurement.sections[0].model_copy(
        deep=True,
        update={
            "polygon": [(20, 10), (60, 10), (60, 30), (20, 30)],
        },
    )

    corrected_report = recalculate_report_from_corrected_sections(result.report, [corrected_section])
    measurement = corrected_report.measurement

    assert measurement.sections[0].area_pixels == 800
    assert measurement.total_area_sqft == 200
    assert measurement.total_perimeter_ft == 60
    assert any(correction["type"] == "polygon_vertices" for correction in corrected_report.user_corrections)
    assert any(warning.code == "manual_polygon_correction" for warning in measurement.warnings)


def test_canvas_rect_json_maps_back_to_image_section_coordinates() -> None:
    section = section_from_polygon("main", [(0, 0), (10, 0), (10, 10), (0, 10)])
    canvas_json = {
        "objects": [
            {
                "type": "rect",
                "left": 20,
                "top": 10,
                "width": 40,
                "height": 20,
                "scaleX": 1,
                "scaleY": 1,
            }
        ]
    }

    corrected = _canvas_json_to_sections(
        canvas_json,
        original_sections=[section],
        scale_x=0.5,
        scale_y=0.5,
    )

    assert corrected[0].section_id == "main"
    assert corrected[0].polygon == [(40.0, 20.0), (120.0, 20.0), (120.0, 60.0), (40.0, 60.0)]


def test_canvas_initial_drawing_path_round_trips_section_points() -> None:
    section = section_from_polygon("main", [(10, 10), (50, 10), (50, 30), (10, 30)])
    initial = _sections_to_canvas_initial_drawing([section], scale_x=0.5, scale_y=0.5)

    corrected = _canvas_json_to_sections(
        initial,
        original_sections=[section],
        scale_x=0.5,
        scale_y=0.5,
    )

    assert corrected[0].polygon == [(10.0, 10.0), (50.0, 10.0), (50.0, 30.0), (10.0, 30.0)]


def test_canvas_path_json_applies_transformed_offset() -> None:
    section = section_from_polygon("main", [(0, 0), (10, 0), (10, 10), (0, 10)])
    canvas_json = {
        "objects": [
            {
                "type": "path",
                "left": 5,
                "top": 10,
                "scaleX": 1,
                "scaleY": 1,
                "path": [["M", 0, 0], ["L", 20, 0], ["L", 20, 20], ["L", 0, 20], ["Z"]],
            }
        ]
    }

    corrected = _canvas_json_to_sections(
        canvas_json,
        original_sections=[section],
        scale_x=0.5,
        scale_y=0.5,
    )

    assert corrected[0].polygon == [(10.0, 20.0), (50.0, 20.0), (50.0, 60.0), (10.0, 60.0)]


def test_corner_canvas_round_trips_draggable_vertex_handles() -> None:
    section = section_from_polygon("main", [(10, 10), (50, 10), (50, 30), (10, 30)])
    initial = _section_to_corner_canvas_initial_drawing(section, scale_x=0.5, scale_y=0.5)

    points = _canvas_json_to_corner_points(initial, scale_x=0.5, scale_y=0.5)

    assert points == [(10.0, 10.0), (50.0, 10.0), (50.0, 30.0), (10.0, 30.0)]


def test_corner_canvas_contains_only_draggable_vertex_handles() -> None:
    section = section_from_polygon("main", [(10, 10), (50, 10), (50, 30), (10, 30)])
    initial = _section_to_corner_canvas_initial_drawing(section, scale_x=0.5, scale_y=0.5)

    assert len(initial["objects"]) == 4
    assert {obj["type"] for obj in initial["objects"]} == {"circle"}


def test_corner_canvas_splits_existing_handles_from_new_clicked_points() -> None:
    section = section_from_polygon("main", [(10, 10), (50, 10), (50, 30), (10, 30)])
    canvas_json = _section_to_corner_canvas_initial_drawing(section, scale_x=0.5, scale_y=0.5)
    canvas_json["objects"].append(
        {
            "type": "circle",
            "originX": "center",
            "originY": "center",
            "left": 15,
            "top": 5,
            "width": 18,
            "height": 18,
            "radius": 9,
            "scaleX": 1,
            "scaleY": 1,
        }
    )

    existing, new_points = _canvas_json_to_corner_edit_points(canvas_json, scale_x=0.5, scale_y=0.5)

    assert existing == [(10.0, 10.0), (50.0, 10.0), (50.0, 30.0), (10.0, 30.0)]
    assert new_points == [(30.0, 10.0)]


def test_corner_canvas_points_include_unapplied_added_corners() -> None:
    section = section_from_polygon("main", [(10, 10), (50, 10), (50, 30), (10, 30)])
    canvas_json = _section_to_corner_canvas_initial_drawing(section, scale_x=0.5, scale_y=0.5)
    canvas_json["objects"].append(
        {
            "type": "circle",
            "originX": "center",
            "originY": "center",
            "left": 15,
            "top": 5,
            "width": 18,
            "height": 18,
            "radius": 9,
            "scaleX": 1,
            "scaleY": 1,
        }
    )

    points = _canvas_json_to_corner_points(canvas_json, scale_x=0.5, scale_y=0.5)

    assert points == [(10.0, 10.0), (30.0, 10.0), (50.0, 10.0), (50.0, 30.0), (10.0, 30.0)]


def test_insert_new_corner_points_uses_nearest_polygon_edge() -> None:
    updated = _insert_new_corner_points(
        [(10, 10), (50, 10), (50, 30), (10, 30)],
        [(30, 8)],
    )

    assert updated == [(10, 10), (30, 8), (50, 10), (50, 30), (10, 30)]


def test_replace_section_polygon_only_updates_selected_section() -> None:
    main = section_from_polygon("main", [(10, 10), (50, 10), (50, 30), (10, 30)])
    annex = section_from_polygon("annex", [(60, 60), (80, 60), (80, 80), (60, 80)])

    corrected = _replace_section_polygon(
        [main, annex],
        "annex",
        [(65, 65), (85, 65), (85, 90), (65, 90)],
    )

    assert corrected[0].polygon == main.polygon
    assert corrected[1].polygon == [(65, 65), (85, 65), (85, 90), (65, 90)]


def test_low_segmentation_confidence_warns() -> None:
    section = section_from_polygon("main", [(0, 0), (10, 0), (10, 10), (0, 10)])
    warnings = measurement_warnings(
        calibration=clicked_known_length_calibration(point_a=(0, 0), point_b=(10, 0), length_feet=5),
        sections=[section],
        image_metadata=ImageMetadata(
            image_id="img",
            file_name="roof.png",
            width=100,
            height=100,
            inference_width=100,
            inference_height=100,
            content_hash="hash",
        ),
        segmentation_score=0.04,
    )

    assert any(warning.code == "low_segmentation_confidence" and warning.severity == "error" for warning in warnings)


def test_parse_points_text_accepts_line_or_semicolon_separated_points() -> None:
    assert _parse_points_text("10,20\n30, 40;bad\n50,60") == [
        (10.0, 20.0),
        (30.0, 40.0),
        (50.0, 60.0),
    ]


def test_format_points_text_rounds_for_streamlit_fields() -> None:
    assert _format_points_text([(10.2, 20.6), (30.5, 40.4)]) == "10,21\n30,40"


def test_ai_point_suggestion_payload_filters_out_of_bounds_points() -> None:
    suggestion = suggestion_from_payload(
        {
            "positive_points": [
                {"x": 100, "y": 120, "reason": "main roof"},
                {"x": 9999, "y": 120, "reason": "bad"},
            ],
            "negative_points": [
                {"x": 20, "y": 30, "reason": "parking"},
                {"x": -1, "y": 10, "reason": "bad"},
            ],
            "confidence": 1.2,
            "notes": "School has multiple roof sections.",
        },
        width=500,
        height=400,
    )

    assert suggestion.positive_points == [(100.0, 120.0)]
    assert suggestion.negative_points == [(20.0, 30.0)]
    assert suggestion.confidence == 1.0
    assert "multiple roof sections" in suggestion.notes


def test_ai_polygon_suggestion_payload_filters_and_repairs_polygons() -> None:
    suggestion = polygon_suggestion_from_payload(
        {
            "roof_polygons": [
                {
                    "label": "main",
                    "points": [
                        {"x": 10, "y": 20},
                        {"x": 60, "y": 20},
                        {"x": 60, "y": 50},
                        {"x": 10, "y": 50},
                    ],
                },
                {
                    "label": "bad",
                    "points": [
                        {"x": 9999, "y": 20},
                        {"x": 60, "y": 20},
                    ],
                },
            ],
            "confidence": 0.8,
            "notes": "Simple roof outline.",
        },
        width=100,
        height=80,
    )

    assert len(suggestion.polygons) == 1
    assert suggestion.polygons[0] == [(10.0, 20.0), (60.0, 20.0), (60.0, 50.0), (10.0, 50.0), (10.0, 20.0)]
    assert suggestion.confidence == 0.8


def test_ai_polygon_suggestion_uses_prompt_focused_image_and_maps_back() -> None:
    image = Image.new("RGB", (1200, 900), "white")
    seen_sizes: list[tuple[int, int]] = []

    suggestion = suggest_roof_polygons(
        image,
        focus_points=[(260, 220), (640, 620)],
        provider=lambda focused, address, width, height: (
            seen_sizes.append((width, height))
            or {
                "roof_polygons": [
                    {"points": [{"x": 10, "y": 20}, {"x": 80, "y": 20}, {"x": 80, "y": 70}, {"x": 10, "y": 70}]}
                ]
            }
        ),
    )

    assert seen_sizes == [(640, 640)]
    assert suggestion.focus_crop == (130, 100, 770, 740)
    assert suggestion.polygons[0] == [(140.0, 120.0), (210.0, 120.0), (210.0, 170.0), (140.0, 170.0), (140.0, 120.0)]


def test_ai_polygon_focus_crop_uses_full_image_for_single_prompt() -> None:
    assert _focus_crop_box((1200, 900), [(600, 450)]) is None


def test_ai_point_payload_maps_qualitative_confidence_and_preserves_scene_analysis() -> None:
    suggestion = suggestion_from_payload(
        {
            "positive_points": [{"x": 20, "y": 30}],
            "confidence": "high",
            "scene_analysis": {"target_description": "One connected school roof."},
        },
        width=100,
        height=80,
    )

    assert suggestion.confidence == 0.82
    assert suggestion.scene_analysis["target_description"] == "One connected school roof."


def test_primary_prompt_cluster_excludes_detached_campus_prompts() -> None:
    points = [(463, 548), (366, 385), (526, 270), (628, 289), (294, 596), (828, 958), (870, 783)]

    assert _primary_prompt_cluster(points, (1280, 1280)) == points[:5]


def test_map_view_crop_reprojects_center_for_lidar_alignment() -> None:
    cropped = _map_view_for_image_crop(
        {"latitude": 37.97867, "longitude": -84.192173, "zoom": 19.0},
        (1280, 1280),
        (160, 120, 800, 760),
    )

    assert cropped["zoom"] == 19.0
    assert cropped["longitude"] < -84.192173
    assert cropped["latitude"] > 37.97867


def test_ai_outline_candidate_can_replace_weaker_sam_geometry() -> None:
    mask = np.zeros((100, 100), dtype=bool)
    mask[20:80, 20:80] = True
    sam = section_from_polygon("sam", [(20, 20), (80, 20), (80, 80), (20, 80)])
    outline = section_from_polygon("outline", [(22, 20), (80, 22), (78, 80), (20, 78)])

    candidate = _evaluate_ai_outline_candidate(
        mask,
        sam_sections=[sam],
        outline_sections=[outline],
        footprint_polygons=[],
        outline_prior_polygons=[outline.polygon],
        confidence=0.7,
    )

    assert candidate["accepted"] is True


def test_ai_outline_candidate_rejects_low_agreement_blob() -> None:
    mask = np.zeros((100, 100), dtype=bool)
    mask[20:80, 20:80] = True
    sam = section_from_polygon("sam", [(20, 20), (80, 20), (80, 80), (20, 80)])
    blob = section_from_polygon("outline", [(0, 0), (99, 0), (99, 99), (0, 99)])

    candidate = _evaluate_ai_outline_candidate(
        mask,
        sam_sections=[sam],
        outline_sections=[blob],
        footprint_polygons=[],
        outline_prior_polygons=[blob.polygon],
        confidence=0.9,
    )

    assert candidate["accepted"] is False


def test_raster_outline_cannot_replace_final_measurement_geometry() -> None:
    raster = RoofPolygonSuggestion(model_name="gpt_image_raster_outline")
    direct = RoofPolygonSuggestion(model_name="gpt-5.5")

    assert _raster_outline_is_prior_only(raster)
    assert not _raster_outline_is_prior_only(direct)


def test_raster_yellow_outline_extracts_closed_polygon() -> None:
    from PIL import ImageDraw

    source = Image.new("RGB", (1024, 1024), (90, 90, 90))
    edited = source.copy()
    ImageDraw.Draw(edited).line([(160, 160), (860, 160), (860, 860), (160, 860), (160, 160)], fill="#FFD400", width=5)

    polygons, registration, warning = _yellow_boundary_to_polygons(source, edited)

    assert warning is None
    assert registration is not None and registration < 1
    assert len(polygons) == 1
    assert len(polygons[0]) >= 4


def test_raster_yellow_outline_rejects_open_line() -> None:
    from PIL import ImageDraw

    source = Image.new("RGB", (1024, 1024), (90, 90, 90))
    edited = source.copy()
    ImageDraw.Draw(edited).line([(160, 160), (860, 160), (860, 860)], fill="#FFD400", width=5)

    polygons, _, warning = _yellow_boundary_to_polygons(source, edited)

    assert not polygons
    assert warning and "not a closed perimeter" in warning


def test_raster_yellow_outline_repairs_a_short_directional_gap() -> None:
    from PIL import ImageDraw

    source = Image.new("RGB", (1024, 1024), (90, 90, 90))
    edited = source.copy()
    draw = ImageDraw.Draw(edited)
    draw.line([(160, 160), (500, 160)], fill="#FFD400", width=8)
    draw.line([(524, 160), (860, 160), (860, 860), (160, 860), (160, 160)], fill="#FFD400", width=8)

    polygons, _, warning = _yellow_boundary_to_polygons(source, edited)

    assert warning is None
    assert len(polygons) == 1


def test_raster_gap_repair_does_not_bridge_distant_fragments() -> None:
    boundary = np.zeros((256, 256), dtype=bool)
    boundary[50:151, 50] = True
    boundary[50:151, 205] = True

    repaired, repairs = _repair_short_boundary_gaps(boundary, footprint_mask=None)

    assert repairs == 0
    assert np.array_equal(repaired, boundary)


def test_refined_ai_polygon_suggestion_receives_current_sections() -> None:
    image = Image.new("RGB", (100, 80), "white")
    section = section_from_polygon("main", [(10, 20), (60, 20), (60, 50), (10, 50)])

    suggestion = suggest_refined_roof_polygons(
        image,
        [section],
        provider=lambda image, address, width, height, current: {
            "roof_polygons": [
                {
                    "label": current[0]["label"],
                    "points": current[0]["points"],
                }
            ],
            "confidence": 0.75,
            "notes": "Refined from SAM.",
        },
    )

    assert len(suggestion.polygons) == 1
    assert suggestion.polygons[0] == section.polygon
    assert suggestion.confidence == 0.75
    assert "Refined from SAM" in suggestion.notes


def test_measurement_service_uses_ai_outline_polygons_without_segmenter(tmp_path) -> None:
    request = RoofMeasureRequest(
        overhead_image_name="roof.png",
        calibration_length_feet=5,
        calibration_point_a=(0, 0),
        calibration_point_b=(10, 0),
    )

    result = measure_roof_from_outline_polygons(
        image_bytes=_image_bytes(),
        request=request,
        polygons=[[(20, 10), (60, 10), (60, 30), (20, 30)]],
        outline_confidence=0.82,
        outline_notes="AI outlined a simple rectangle.",
        storage_root=str(tmp_path),
    )

    measurement = result.report.measurement
    assert result.selected_mask is None
    assert result.candidate_count == 0
    assert measurement.sections[0].area_pixels == 800
    assert measurement.total_area_sqft == 200
    assert measurement.total_perimeter_ft == 60
    assert result.report.model_name == "openai_roof_outline"
    assert any(warning.code == "ai_outline_review" for warning in measurement.warnings)


def test_sections_from_ai_polygons_preserves_existing_section_order() -> None:
    main = section_from_polygon("main", [(10, 20), (60, 20), (60, 50), (10, 50)])
    cleaned = _sections_from_ai_polygons(
        [main],
        [[(12, 22), (58, 22), (58, 48), (12, 48)]],
    )

    assert cleaned[0].section_id == "main"
    assert cleaned[0].polygon == [(12, 22), (58, 22), (58, 48), (12, 48)]


def test_canvas_background_image_resizes_to_canvas_dimensions() -> None:
    image = Image.new("RGB", (200, 100), "white")

    background = _canvas_background_image(image, 100, 50)

    assert background.size == (100, 50)


def test_prompt_points_overlay_preserves_image_size() -> None:
    image = Image.new("RGB", (120, 80), "white")

    overlay = prompt_points_overlay(
        image,
        positive_points=[(30, 40)],
        negative_points=[(90, 40)],
    )

    assert overlay.size == image.size
    assert overlay.mode == "RGB"


def test_prompt_point_canvas_round_trips_points() -> None:
    points = [(100.0, 120.0), (250.0, 300.0)]

    canvas_json = _points_to_canvas_initial_drawing(points, scale_x=0.5, scale_y=0.5, color="#009760")
    parsed = _canvas_json_to_points(canvas_json, scale_x=0.5, scale_y=0.5)

    assert parsed == points


def test_prompt_point_canvas_round_trips_roof_and_exclude_points() -> None:
    canvas_json = _prompt_points_to_canvas_initial_drawing(
        positive_points=[(100.0, 120.0)],
        negative_points=[(250.0, 300.0)],
        scale_x=0.5,
        scale_y=0.5,
    )

    positive, negative = _canvas_json_to_prompt_points(canvas_json, scale_x=0.5, scale_y=0.5)

    assert positive == [(100.0, 120.0)]
    assert negative == [(250.0, 300.0)]


def test_prompt_point_canvas_reads_top_left_origin_circle() -> None:
    canvas_json = {
        "objects": [
            {
                "type": "circle",
                "left": 45,
                "top": 55,
                "width": 10,
                "height": 10,
                "scaleX": 1,
                "scaleY": 1,
            }
        ]
    }

    assert _canvas_json_to_points(canvas_json, scale_x=0.5, scale_y=0.5) == [(100.0, 120.0)]


def test_ai_point_payload_accepts_nested_and_normalized_coordinates() -> None:
    suggestion = suggestion_from_payload(
        {
            "positive_points": [
                {"point": {"x": 0.5, "y": 0.25}},
                {"coordinates": {"pixel_x": 80, "pixel_y": 70}},
            ],
            "negative_points": [
                {"location": [0.25, 0.5]},
            ],
        },
        width=101,
        height=81,
    )

    assert suggestion.positive_points == [(50.0, 20.0), (80.0, 70.0)]
    assert suggestion.negative_points == [(25.0, 40.0)]


def test_roof_measure_openai_calls_use_model_default_temperature(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))]
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    image = Image.new("RGB", (100, 80), "white")

    _call_openai_roof_point_suggester(image)
    _call_openai_roof_polygon_suggester(image)
    _call_openai_roof_polygon_refiner(image, current_payload=[])
    _call_openai_scale_reader(image)

    assert len(calls) == 4
    assert all("temperature" not in call for call in calls)
    polygon_prompt = calls[1]["messages"][1]["content"][0]["text"]
    assert "ONE continuous line around the outside perimeter" in polygon_prompt
    assert "do not cut across the middle of the building" in polygon_prompt


def test_roof_page_can_override_stale_inherited_project_env(tmp_path, monkeypatch) -> None:
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("OPENAI_API_KEY=project-key\n")
    monkeypatch.setenv("OPENAI_API_KEY", "stale-key")

    load_project_env(dotenv_path)
    assert os.environ["OPENAI_API_KEY"] == "stale-key"

    load_project_env(dotenv_path, override=True)
    assert os.environ["OPENAI_API_KEY"] == "project-key"
