from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd


@dataclass
class EstimatorData:
    jobs: pd.DataFrame = field(default_factory=pd.DataFrame)
    estimates: pd.DataFrame = field(default_factory=pd.DataFrame)
    line_items: pd.DataFrame = field(default_factory=pd.DataFrame)
    classified_line_items: pd.DataFrame = field(default_factory=pd.DataFrame)
    tracking_summary: pd.DataFrame = field(default_factory=pd.DataFrame)
    tracking_daily: pd.DataFrame = field(default_factory=pd.DataFrame)
    pricing: pd.DataFrame = field(default_factory=pd.DataFrame)
    warnings: list[str] = field(default_factory=list)
    source_files_used: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EstimatorAssumptions:
    blended_hourly_rate: float = 72.0
    crew_productivity_sqft_per_day_low: float = 1800.0
    crew_productivity_sqft_per_day_high: float = 3200.0
    cost_per_mile: float = 0.75
    local_radius_miles: float = 25.0
    lodging_review_one_way_miles: float = 90.0
    lodging_review_one_way_minutes: float = 90.0
    average_speed_mph_for_fallback: float = 50.0
    origin_address: str = "1132 Equity Street, Shelbyville, KY"
    coating_waste_factor: float = 0.12


DEFAULT_STAGE_FILES = {
    "jobs": Path("output/job_index.json"),
    "estimates": Path("output/estimate_summary.json"),
    "line_items": Path("output/estimate_line_items.json"),
    "tracking_summary": Path("output/job_tracking_summary.json"),
    "tracking_daily": Path("output/job_tracking_daily_entries.json"),
}

PRICING_CANDIDATES = (
    Path("output/pricing/pricing_catalog_current_cleaned.csv"),
    Path("output/pricing/pricing_catalog_current.csv"),
)
