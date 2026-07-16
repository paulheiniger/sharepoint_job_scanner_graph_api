from __future__ import annotations

from io import BytesIO

import numpy as np
from PIL import Image

from roof_measure.calibration import (
    clicked_known_length_calibration,
    detect_google_earth_scale_bar,
    feet_from_pixels,
    parse_scale_label_feet,
    sqft_from_pixels,
)
from roof_measure.confidence import measurement_warnings
from roof_measure.exports import measurement_to_geojson
from roof_measure.geometry import polygon_area_pixels, repair_polygon, simplify_ring
from roof_measure.image_io import image_hash, load_image_bytes
from roof_measure.models import ImageMetadata
from roof_measure.polygonize import section_from_polygon, sections_from_mask
from roof_measure.segmentation import MockRoofSegmenter
from roof_measure.service import measure_roof_from_overhead_image, recalculate_report_from_corrected_sections
from roof_measure.models import RoofMeasureRequest
from roof_measure.streamlit_page import _canvas_json_to_sections, _sections_to_canvas_initial_drawing


def _image_bytes(size: tuple[int, int] = (100, 80), *, fmt: str = "PNG") -> bytes:
    image = Image.new("RGB", size, "white")
    buffer = BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


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


def test_sections_from_mask_detects_multiple_sections() -> None:
    mask = np.zeros((80, 100), dtype=bool)
    mask[10:30, 20:50] = True
    mask[45:65, 60:90] = True

    sections = sections_from_mask(mask, minimum_section_area_pixels=100)

    assert len(sections) == 2
    assert [section.area_pixels for section in sections] == [600, 600]


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
