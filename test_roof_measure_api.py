from __future__ import annotations

from io import BytesIO
import time

from PIL import Image
import pytest

from roof_measure.api_context import (
    RoofMeasureContextExpiredError,
    calculate_roof_measurement,
    create_roof_measure_context,
    load_roof_measure_context,
    resolve_roof_measure_asset,
)
from roof_measure.map_reference import (
    BuildingFootprint,
    BuildingFootprintLookup,
    LidarCoverage,
    MapboxStaticImage,
)


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
