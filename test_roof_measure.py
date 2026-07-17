from __future__ import annotations

from io import BytesIO
import base64
import math
import sys
from types import SimpleNamespace

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
from roof_measure.ai_polygons import polygon_suggestion_from_payload, suggest_refined_roof_polygons
from roof_measure.ai_points import suggestion_from_payload
from roof_measure.ai_polygons import _call_openai_roof_polygon_refiner, _call_openai_roof_polygon_suggester
from roof_measure.ai_points import _call_openai_roof_point_suggester
from roof_measure.calibration import _call_openai_scale_reader
from roof_measure.confidence import measurement_warnings
from roof_measure.exports import measurement_to_geojson
from roof_measure.geometry import polygon_area_pixels, repair_polygon, simplify_ring, straighten_architectural_ring
from roof_measure.image_io import image_hash, load_image_bytes
from roof_measure.models import ImageMetadata
from roof_measure.polygonize import section_from_polygon, sections_from_mask
from roof_measure.segmentation import MockRoofSegmenter, Sam2RoofSegmenter, SegmentationPrompts
from roof_measure.service import measure_roof_from_outline_polygons, measure_roof_from_overhead_image, recalculate_report_from_corrected_sections
from roof_measure.models import RoofMeasureRequest
from roof_measure.streamlit_page import (
    _canvas_background_image,
    _canvas_json_to_corner_edit_points,
    _canvas_json_to_points,
    _canvas_json_to_prompt_points,
    _canvas_json_to_sections,
    _canvas_json_to_corner_points,
    _format_points_text,
    _insert_new_corner_points,
    _parse_points_text,
    _prompt_points_to_canvas_initial_drawing,
    _points_to_canvas_initial_drawing,
    _replace_section_polygon,
    _section_to_corner_canvas_initial_drawing,
    _sections_from_ai_polygons,
    _sections_to_canvas_initial_drawing,
)
from roof_measure.visualization import prompt_points_overlay


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
        assert json["max_candidates"] == 3
        assert json["image_png_base64"]
        return FakeResponse()

    monkeypatch.setenv("SAM2_SEGMENTATION_URL", "http://127.0.0.1:8765/segment")
    monkeypatch.setattr("roof_measure.segmentation.requests.post", fake_post)

    result = Sam2RoofSegmenter().segment(
        image,
        SegmentationPrompts(positive_points=[(20.0, 15.0)], negative_points=[(3.0, 4.0)]),
    )

    assert result.model_name == "sam2_remote"
    assert result.model_version == "test"
    assert result.warnings == ["test warning"]
    assert len(result.candidates) == 1
    assert result.candidates[0].score == 0.88
    assert np.array_equal(result.candidates[0].mask, mask)


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


def test_corner_canvas_lines_use_relative_endpoints() -> None:
    section = section_from_polygon("main", [(10, 10), (50, 10), (50, 30), (10, 30)])
    initial = _section_to_corner_canvas_initial_drawing(section, scale_x=0.5, scale_y=0.5)
    first_line = initial["objects"][0]

    assert first_line["left"] == 5
    assert first_line["top"] == 5
    assert first_line["x1"] == 0
    assert first_line["y1"] == 0
    assert first_line["x2"] == 20
    assert first_line["y2"] == 0


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
