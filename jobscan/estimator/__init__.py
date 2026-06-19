"""Historical-data-backed estimator prototype helpers."""

__all__ = ["build_estimate", "extract_scope", "load_estimator_data"]


def __getattr__(name: str):
    if name == "build_estimate":
        from .estimate import build_estimate

        return build_estimate
    if name == "extract_scope":
        from .rules import extract_scope

        return extract_scope
    if name == "load_estimator_data":
        from .data_loader import load_estimator_data

        return load_estimator_data
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
