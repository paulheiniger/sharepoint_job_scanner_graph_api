from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


Point = tuple[float, float]
Ring = list[Point]


class ImageMetadata(BaseModel):
    image_id: str
    file_name: str
    stored_path: str = ""
    width: int
    height: int
    inference_width: int
    inference_height: int
    scale_x: float = 1.0
    scale_y: float = 1.0
    content_hash: str
    duplicate: bool = False
    exif_orientation_applied: bool = False
    quality_flags: list[str] = Field(default_factory=list)


class CalibrationResult(BaseModel):
    calibration_type: Literal["clicked_known_length", "scale_bar", "metadata", "estimated", "none"] = "none"
    length_feet: float | None = None
    point_a: Point | None = None
    point_b: Point | None = None
    pixel_distance: float | None = None
    pixels_per_foot: float | None = None
    confidence: Literal["high", "medium", "low", "none"] = "none"
    warning: str | None = None


class MeasurementWarning(BaseModel):
    code: str
    message: str
    severity: Literal["info", "warning", "error"] = "warning"


class RoofSection(BaseModel):
    section_id: str
    polygon: Ring
    holes: list[Ring] = Field(default_factory=list)
    area_pixels: float
    perimeter_pixels: float
    area_sqft: float | None = None
    perimeter_ft: float | None = None
    confidence: float = 0.0


class RoofMeasurement(BaseModel):
    total_area_sqft: float | None = None
    total_perimeter_ft: float | None = None
    low_area_sqft: float | None = None
    high_area_sqft: float | None = None
    sections: list[RoofSection] = Field(default_factory=list)
    calibration: CalibrationResult
    confidence: dict[str, float] = Field(default_factory=dict)
    warnings: list[MeasurementWarning] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class RoofMeasureRequest(BaseModel):
    address: str = ""
    job_id: str | None = None
    overhead_image_name: str
    positive_points: list[Point] = Field(default_factory=list)
    negative_points: list[Point] = Field(default_factory=list)
    segmentation_box: tuple[float, float, float, float] | None = None
    outline_prior_polygons: list[Ring] = Field(default_factory=list)
    outline_prior_buffer_pixels: int = 16
    outline_prior_as_mask_prompt: bool = False
    calibration_length_feet: float | None = None
    calibration_point_a: Point | None = None
    calibration_point_b: Point | None = None
    metadata_pixels_per_foot: float | None = None
    scale_bar_label_hint: str | None = None
    use_ai_scale_reader: bool = True
    simplification_tolerance: float = 6.0
    minimum_section_area_pixels: float = 400.0
    edge_snap_strength: float = 0.0
    segmenter_name: str = "manual_fallback"
    footprint_polygons: list[Ring] = Field(default_factory=list)
    footprint_buffer_feet: float = 10.0
    footprint_source_records: list[dict[str, Any]] = Field(default_factory=list)
    map_view: dict[str, float] = Field(default_factory=dict)


class MeasurementReport(BaseModel):
    id: str
    address: str = ""
    job_id: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source_images: list[ImageMetadata] = Field(default_factory=list)
    calibration_method: str
    pixels_per_foot: float | None = None
    measurement: RoofMeasurement
    user_corrections: list[dict[str, Any]] = Field(default_factory=list)
    processing_iterations: list[dict[str, Any]] = Field(default_factory=list)
    model_name: str = "manual_fallback_segmenter"
    model_version: str = "roof-measure-mvp1"
