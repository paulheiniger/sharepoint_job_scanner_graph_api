"""Repair estimator ingestion and profiling helpers."""

__all__ = [
    "RepairTables",
    "load_vsimple_repair_export",
    "profile_repairs",
    "estimate_repair_from_notes",
    "parse_repair_notes",
    "write_repair_tables",
]


def __getattr__(name: str):
    if name in {"RepairTables", "load_vsimple_repair_export", "write_repair_tables"}:
        from . import vsimple_loader

        return getattr(vsimple_loader, name)
    if name == "profile_repairs":
        from . import profiler

        return getattr(profiler, name)
    if name == "estimate_repair_from_notes":
        from . import estimator

        return getattr(estimator, name)
    if name == "parse_repair_notes":
        from . import scope_parser

        return getattr(scope_parser, name)
    raise AttributeError(name)
