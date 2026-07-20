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
    payload = report.model_dump(mode="json")
    # The report is an operator artifact, not a per-edge optimizer dump. Keep
    # stage decisions and scores while excluding large transient geometry logs.
    for iteration in payload.get("processing_iterations") or []:
        if not isinstance(iteration, dict):
            continue
        for key in ("edge_diagnostics", "polygons", "candidate_comparison", "coordinate_frame", "vertex_document"):
            iteration.pop(key, None)
        retry = iteration.get("footprint_deformation")
        if isinstance(retry, dict):
            for key in ("edge_diagnostics", "polygons", "coordinate_frame"):
                retry.pop(key, None)
        if iteration.get("stage") == "semantic_roof_analysis" and isinstance(iteration.get("analysis"), dict):
            analysis = iteration["analysis"]
            iteration["analysis"] = {
                "target_description": analysis.get("target_description"),
                "confidence": iteration.get("confidence"),
            }
    return json.dumps(payload, indent=indent, default=str)


def geojson_to_string(geojson: dict[str, Any], *, indent: int = 2) -> str:
    return json.dumps(geojson, indent=indent, default=str)
