"""Historical-data-backed estimator prototype helpers."""

from .data_loader import load_estimator_data
from .estimate import build_estimate
from .rules import extract_scope

try:
    from .field_estimator import estimate_from_field_notes
except Exception as err:  # pragma: no cover - exercised only when optional import breaks
    _FIELD_ESTIMATOR_IMPORT_ERROR = err

    def estimate_from_field_notes(*args, **kwargs):
        raise ImportError(
            "estimate_from_field_notes is unavailable because "
            "jobscan.estimator.field_estimator could not be imported"
        ) from _FIELD_ESTIMATOR_IMPORT_ERROR


__all__ = ["build_estimate", "estimate_from_field_notes", "extract_scope", "load_estimator_data"]
