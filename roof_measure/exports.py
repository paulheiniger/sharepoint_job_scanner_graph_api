from __future__ import annotations

import json
from typing import Any

from .geometry import feature_collection, polygon_to_geojson_feature
from .models import MeasurementReport, RoofMeasurement


def measurement_to_geojson(measurement: RoofMeasurement) -> dict[str, Any]:
    features = [
        polygon_to_geojson_feature(
            section.polygon,
            section.holes,
            {
                "section_id": section.section_id,
                "area_sqft": section.area_sqft,
                "perimeter_ft": section.perimeter_ft,
                "area_pixels": section.area_pixels,
            },
        )
        for section in measurement.sections
    ]
    return feature_collection(features)


def report_to_json(report: MeasurementReport, *, indent: int = 2) -> str:
    return report.model_dump_json(indent=indent)


def geojson_to_string(geojson: dict[str, Any], *, indent: int = 2) -> str:
    return json.dumps(geojson, indent=indent, default=str)

