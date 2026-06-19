from __future__ import annotations

from typing import Any

from .labor import estimate_travel_impact
from .schemas import EstimatorAssumptions


def build_travel_plan(
    scope: dict[str, Any],
    *,
    recommended_crew_size: int,
    estimated_work_days: int,
    assumptions: EstimatorAssumptions | None = None,
) -> dict[str, Any]:
    return estimate_travel_impact(
        scope,
        recommended_crew_size=recommended_crew_size,
        estimated_work_days=estimated_work_days,
        assumptions=assumptions,
    )
