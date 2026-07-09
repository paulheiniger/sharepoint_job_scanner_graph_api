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
    line_item_classifications: pd.DataFrame = field(default_factory=pd.DataFrame)
    template_rows: pd.DataFrame = field(default_factory=pd.DataFrame)
    tracking_summary: pd.DataFrame = field(default_factory=pd.DataFrame)
    tracking_daily: pd.DataFrame = field(default_factory=pd.DataFrame)
    relationship_material_qty_ratios: pd.DataFrame = field(default_factory=pd.DataFrame)
    relationship_labor_rates: pd.DataFrame = field(default_factory=pd.DataFrame)
    relationship_package_cooccurrence: pd.DataFrame = field(default_factory=pd.DataFrame)
    job_package_summary: pd.DataFrame = field(default_factory=pd.DataFrame)
    pricing: pd.DataFrame = field(default_factory=pd.DataFrame)
    pricing_catalog: pd.DataFrame = field(default_factory=pd.DataFrame)
    product_catalog: pd.DataFrame = field(default_factory=pd.DataFrame)
    product_aliases: pd.DataFrame = field(default_factory=pd.DataFrame)
    product_documents: pd.DataFrame = field(default_factory=pd.DataFrame)
    product_properties: pd.DataFrame = field(default_factory=pd.DataFrame)
    product_rules: pd.DataFrame = field(default_factory=pd.DataFrame)
    product_decision_links: pd.DataFrame = field(default_factory=pd.DataFrame)
    template_product_option_links: pd.DataFrame = field(default_factory=pd.DataFrame)
    template_pricing_option_links: pd.DataFrame = field(default_factory=pd.DataFrame)
    template_selector_maps: pd.DataFrame = field(default_factory=pd.DataFrame)
    template_lookup_tables: pd.DataFrame = field(default_factory=pd.DataFrame)
    template_row_catalog: pd.DataFrame = field(default_factory=pd.DataFrame)
    template_formula_models: pd.DataFrame = field(default_factory=pd.DataFrame)
    template_product_options: pd.DataFrame = field(default_factory=pd.DataFrame)
    template_labor_options: pd.DataFrame = field(default_factory=pd.DataFrame)
    historical_scope_texts: pd.DataFrame = field(default_factory=pd.DataFrame)
    decision_history_tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    estimator_decision_recommendations: pd.DataFrame = field(default_factory=pd.DataFrame)
    estimator_memory: pd.DataFrame = field(default_factory=pd.DataFrame)
    warnings: list[str] = field(default_factory=list)
    source_files_used: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.pricing.empty and not self.pricing_catalog.empty:
            self.pricing = self.pricing_catalog
        if self.pricing_catalog.empty and not self.pricing.empty:
            self.pricing_catalog = self.pricing
        if self.classified_line_items.empty and not self.line_item_classifications.empty:
            self.classified_line_items = self.line_item_classifications
        if self.line_item_classifications.empty and not self.classified_line_items.empty:
            self.line_item_classifications = self.classified_line_items


@dataclass
class FieldNotesInput:
    raw_notes: str
    job_name: str | None = None
    site_address: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    estimated_sqft: float | None = None
    substrate: str | None = None
    roof_condition: str | None = None
    coating_type: str | None = None
    warranty_target_years: int | None = None
    access_complexity: str | None = None
    penetrations_complexity: str | None = None
    insulation_present: bool | None = None
    condensation_risk: bool | None = None
    travel_origin: str = "1132 Equity Street, Shelbyville, KY"


@dataclass
class ParsedFieldNotes:
    project_type: str = ""
    division: str = ""
    building_type: str = ""
    substrate: str = ""
    estimated_sqft: float | None = None
    coating_type: str = ""
    foam_type: str = ""
    foam_thickness_inches: float | None = None
    warranty_target_years: int | None = None
    roof_condition: str = ""
    access_complexity: str = ""
    penetrations_complexity: str = ""
    penetration_count: int | None = None
    condition_detail_flags: list[str] = field(default_factory=list)
    insulation_present: bool | None = None
    condensation_risk: bool = False
    city: str = ""
    state: str = ""
    missing_info: list[str] = field(default_factory=list)
    review_flags: list[str] = field(default_factory=list)
    dimension_summary: dict = field(default_factory=dict)
    confidence: float = 0.0


@dataclass
class EstimateRecommendation:
    parsed_fields: dict
    recommended_scope: list[str]
    material_plan: list[dict]
    labor_plan: list[dict]
    travel_plan: dict
    historical_calibration: dict
    similar_examples: list[dict]
    estimate_low: float | None
    estimate_target: float | None
    estimate_high: float | None
    review_flags: list[str]
    human_review_required: bool
    draft_workbook_inputs: dict
    estimate_status: str = "READY_TO_ESTIMATE"
    estimate_reason: str = ""
    required_questions: list[str] = field(default_factory=list)
    recommended_next_actions: list[str] = field(default_factory=list)
    confidence: str = "medium"
    debug: dict = field(default_factory=dict)


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
