"""Roof measurement support for Spray-Tec.

The API-facing deterministic modules must remain importable without loading the
optional segmentation and AI stack. The historical package-level measurement
function is retained through a lazy import for existing callers.
"""

from .models import (
    CalibrationResult,
    ImageMetadata,
    MeasurementReport,
    MeasurementWarning,
    RoofMeasurement,
    RoofMeasureRequest,
    RoofSection,
)
__all__ = [
    "CalibrationResult",
    "ImageMetadata",
    "MeasurementReport",
    "MeasurementWarning",
    "RoofMeasurement",
    "RoofMeasureRequest",
    "RoofSection",
    "measure_roof_from_overhead_image",
]


def __getattr__(name: str):
    if name == "measure_roof_from_overhead_image":
        from .service import measure_roof_from_overhead_image

        return measure_roof_from_overhead_image
    raise AttributeError(name)
