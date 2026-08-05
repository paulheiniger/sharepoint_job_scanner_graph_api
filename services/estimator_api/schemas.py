from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


RoofingMaterialCategory = Literal[
    "roofing_foam",
    "coating",
    "seams_misc",
    "penetrations",
    "hvac_units",
    "drains",
    "primer",
    "caulk_detail",
    "caulk_sealant",
    "fabric",
    "board_stock",
    "fasteners",
    "plates",
    "granules",
    "edge_metal",
    "gutter",
    "downspouts",
    "roof_hatch",
    "scuppers",
    "curbs",
    "ladders",
    "pitch_pockets",
    "misc",
    "thinner",
]
RoofingLogisticsCategory = Literal[
    "sales_inspection_trips",
    "truck_expense",
    "dumpster",
    "lift",
    "generator",
    "delivery_fee",
    "freight",
]
RoofingLaborTask = Literal[
    "labor_setup_safety",
    "labor_full_repair",
    "labor_prep",
    "labor_tearoff",
    "labor_board",
    "labor_base",
    "labor_caulk",
    "labor_details",
    "labor_top_coat",
    "labor_cleanup",
    "labor_loading",
    "labor_traveling",
]
InsulationMaterialCategory = Literal[
    "foam",
    "membrane",
    "primer",
    "coating",
    "thermal_barrier_coating",
    "thinner",
    "caulk_sealant",
    "misc",
]
InsulationLogisticsCategory = Literal[
    "sales_inspection_trips",
    "truck_expense",
    "lift",
    "delivery_fee",
    "generator",
    "space_heater",
    "freight",
    "drum_disposal",
    "abaa_audits",
    "abaa_fee",
]
InsulationLaborTask = Literal[
    "labor_set_up",
    "labor_mask",
    "labor_prime",
    "labor_membrane",
    "labor_foam",
    "labor_dc_315",
    "labor_misc",
    "labor_cleanup",
    "labor_loading",
    "labor_traveling",
]
FlooringLaborTask = Literal[
    "labor_floor_grind_patch",
    "labor_floor_corner_repair",
    "labor_floor_prep_base_flake",
    "labor_floor_patch_grind",
    "labor_floor_primer",
    "labor_floor_base_707",
    "labor_floor_details",
    "labor_floor_top_coat",
    "labor_floor_cleanup",
    "labor_loading",
    "labor_traveling",
]
EstimateMaterialCategory = RoofingMaterialCategory | InsulationMaterialCategory
EstimateLogisticsCategory = RoofingLogisticsCategory | InsulationLogisticsCategory
EstimateLaborTask = RoofingLaborTask | InsulationLaborTask | FlooringLaborTask


class EstimateContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_notes: str = Field(
        default="",
        max_length=50_000,
        description="Original estimator notes or relevant conversation text.",
    )
    template_type: str = Field(
        default="",
        max_length=40,
        description="Estimator template family, normally insulation or roofing.",
    )
    site_address: str = Field(
        default="",
        max_length=500,
        description="Current job site address used for route mileage.",
    )
    scope: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured current-job facts extracted by Copilot.",
    )
    reference_job_ids: list[
        Annotated[str, Field(min_length=1, max_length=200)]
    ] = Field(default_factory=list, max_length=10)
    exclude_job_ids: list[
        Annotated[str, Field(min_length=1, max_length=200)]
    ] = Field(
        default_factory=list,
        max_length=20,
        description="Historical job IDs that must not be used as comparables.",
    )
    exclude_source_files: list[
        Annotated[str, Field(min_length=1, max_length=300)]
    ] = Field(
        default_factory=list,
        max_length=20,
        description=(
            "Completed estimate filenames that must not be used as evidence, "
            "including the target estimate in evaluation runs."
        ),
    )
    include_source_metadata: bool = Field(
        default=False,
        description=(
            "Internal diagnostics only. Keep false for agent actions; "
            "source_links are returned separately."
        ),
    )
    focus: Literal[
        "full",
        "labor",
        "pricing",
        "commercial",
        "materials",
        "evidence",
    ] = Field(
        default="full",
        description=(
            "Optional second-pass detail view. Use labor, pricing, commercial, "
            "materials, or evidence when the full response was compacted or a "
            "specific estimate section needs more detail."
        ),
    )


class HistoricalMeasurement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    value: float
    unit: str


class HistoricalEvidenceSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str = ""
    example_id: str = ""
    document_id: str = ""
    label: str = ""
    file_name: str = ""
    file_web_url: str = ""
    folder_path: str = ""
    relative_path: str = ""
    similarity_score: float | None = None
    match_reasons: list[str] = Field(default_factory=list)
    reference_area_sqft: float | None = None


class HistoricalMaterialUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str
    concept_id: str
    category: str
    material_name: str = ""
    quantity_measurements: list[HistoricalMeasurement] = Field(default_factory=list)
    basis_measurements: list[HistoricalMeasurement] = Field(default_factory=list)
    application_parameters: list[HistoricalMeasurement] = Field(default_factory=list)
    unit_price: float | None = None
    estimated_cost: float | None = None
    support_count: int = 0
    confidence: float | None = None
    formula_ready: bool | None = None
    missing_inputs: list[str] = Field(default_factory=list)
    sources: list[HistoricalEvidenceSource] = Field(default_factory=list)


class HistoricalLaborProductivity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    driver_type: str = ""
    driver_quantity: float | None = None
    driver_unit: str = ""
    rate: float | None = None
    rate_unit: str = ""
    evidence_count: int = 0


class HistoricalLaborPerformance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str
    concept_id: str
    category: str
    activity: str = ""
    total_hours: float | None = None
    crew_size: float | None = None
    days: float | None = None
    hourly_rate: float | None = None
    daily_rate: float | None = None
    estimated_cost: float | None = None
    productivity: HistoricalLaborProductivity
    support_count: int = 0
    confidence: float | None = None
    formula_ready: bool | None = None
    missing_inputs: list[str] = Field(default_factory=list)
    sources: list[HistoricalEvidenceSource] = Field(default_factory=list)


class HistoricalAssembly(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str
    template_type: str
    job_id: str = ""
    example_id: str = ""
    label: str = ""
    project_class: str = ""
    market_segment: str = ""
    building_type: str = ""
    substrate: str = ""
    material_system: str = ""
    warranty_years: float | None = None
    area_sqft: float | None = None
    scope_summary: str = ""
    decision_categories: list[str] = Field(default_factory=list)
    similarity_score: float | None = None
    match_reasons: list[str] = Field(default_factory=list)
    sources: list[HistoricalEvidenceSource] = Field(default_factory=list)


class DecisionConcept(BaseModel):
    model_config = ConfigDict(extra="forbid")

    concept_id: str
    category: str
    label: str
    decision_type: str
    editable_inputs: list[str] = Field(default_factory=list)
    required_calculation_inputs: list[str] = Field(default_factory=list)


class CalculationRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    concept_id: str
    category: str
    required_inputs: list[str] = Field(default_factory=list)


class EstimateSourceLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: str
    example_id: str = ""
    job_id: str = ""
    customer: str | None = None
    job_name: str | None = None
    document_id: str | None = None
    file_name: str = ""
    file_web_url: str = ""
    job_folder_web_url: str = ""
    folder_path: str | None = None
    relative_path: str | None = None


class EstimateContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    focus: str = "full"
    scope: dict[str, Any]
    template_type: str
    route_mileage: dict[str, Any] = Field(default_factory=dict)
    scope_integrity: dict[str, Any] = Field(default_factory=dict)
    retrieval_exclusions: dict[str, list[str]] = Field(default_factory=dict)
    matched_comparables: list[dict[str, Any]] = Field(default_factory=list)
    decision_evidence: list[dict[str, Any]] = Field(default_factory=list)
    historical_material_usage: list[HistoricalMaterialUsage] = Field(
        default_factory=list
    )
    historical_labor_performance: list[HistoricalLaborPerformance] = Field(
        default_factory=list
    )
    historical_assemblies: list[HistoricalAssembly] = Field(default_factory=list)
    matched_scope_pattern: dict[str, Any] = Field(default_factory=dict)
    validated_relationships: list[dict[str, Any]] = Field(default_factory=list)
    approved_memories: list[dict[str, Any]] = Field(default_factory=list)
    pricing_candidates: list[dict[str, Any]] = Field(default_factory=list)
    pricing_coverage: dict[str, Any] = Field(default_factory=dict)
    product_guidance: list[dict[str, Any]] = Field(default_factory=list)
    foam_yield_history: list[dict[str, Any]] = Field(default_factory=list)
    purchasing_guidance: list[dict[str, Any]] = Field(default_factory=list)
    labor_plan_guidance: list[dict[str, Any]] = Field(default_factory=list)
    labor_cost_summary: dict[str, Any] = Field(default_factory=dict)
    commercial_guidance: dict[str, Any] = Field(default_factory=dict)
    logistics_guidance: list[dict[str, Any]] = Field(default_factory=list)
    decision_concepts: list[DecisionConcept] = Field(default_factory=list)
    calculation_requirements: list[CalculationRequirement] = Field(
        default_factory=list
    )
    source_links: list[EstimateSourceLink] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    retrieval_summary: dict[str, Any]
    response_budget: dict[str, Any] = Field(default_factory=dict)
    source_metadata: dict[str, Any] | None = None
    planning_snapshot_token: str = Field(
        default="",
        max_length=30_000,
        description=(
            "Short-lived signed planning snapshot. Return this unchanged as "
            "planning_snapshot_token when generating the reviewed workbook."
        ),
    )


class RoofMeasureContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: str = Field(
        min_length=5,
        max_length=500,
        description="Street address for the roof or building site.",
    )
    job_id: str = Field(default="", max_length=200)
    site_name: str = Field(
        default="",
        max_length=300,
        description=(
            "Optional facility or place name visible to the user, such as a school "
            "or industrial campus. This is used for site interpretation, not for "
            "historical-area lookup."
        ),
    )
    site_type: str = Field(
        default="",
        max_length=100,
        description=(
            "Optional physical site classification such as school, campus, hospital, "
            "industrial complex, or single building."
        ),
    )
    view: Literal["whole_site", "building_detail"] = Field(
        default="whole_site",
        description=(
            "Use whole_site first so the complete roof remains visible. Use "
            "building_detail only when a closer image is needed."
        ),
    )
    include_lidar_coverage: bool = Field(
        default=True,
        description=(
            "Return Kentucky public LiDAR coverage metadata when available. "
            "Raw point-cloud data is never returned."
        ),
    )


class RoofMeasurePoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=-256, le=1536)
    y: float = Field(ge=-256, le=1536)


class RoofMeasurePolygonComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    polygon: list[RoofMeasurePoint] = Field(min_length=3, max_length=200)
    holes: list[list[RoofMeasurePoint]] = Field(default_factory=list, max_length=20)


class RoofMeasureFootprintCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    footprint_id: str
    label: str
    provider: str
    plan_area_sqft: float
    perimeter_ft: float
    components: list[RoofMeasurePolygonComponent]


class RoofMeasureCandidateGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: str
    label: str
    footprint_ids: list[str] = Field(default_factory=list)
    building_count: int = Field(ge=1)
    plan_area_sqft: float = Field(gt=0)
    perimeter_ft: float = Field(gt=0)
    distance_from_address_point_ft: float = Field(ge=0)
    contains_address_point: bool = False


class RoofMeasureLidarCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool = False
    collection: str = ""
    captured_at: str = ""
    point_count: int = 0
    provider: str = "kyfromabove"
    attribution: str = ""
    warning: str = ""


class RoofMeasureContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    context_id: str
    expires_at: int
    address: str
    job_id: str = ""
    site_name: str = ""
    site_type: str = ""
    latitude: float
    longitude: float
    zoom: float
    image_width: int
    image_height: int
    pixels_per_foot: float
    satellite_image_url: str
    footprint_overlay_url: str
    footprint_overlay_preview_media_type: Literal["image/jpeg"]
    footprint_overlay_preview_base64: str = Field(
        description=(
            "Base64-encoded JPEG preview containing the satellite pixels and all "
            "candidate footprint outlines. Decode and display this image before "
            "choosing footprint IDs; do not redraw the polygons on a blank canvas."
        )
    )
    footprint_candidates: list[RoofMeasureFootprintCandidate] = Field(
        default_factory=list
    )
    candidate_groups: list[RoofMeasureCandidateGroup] = Field(default_factory=list)
    site_resolution_status: Literal[
        "candidate_group_suggested", "review_required"
    ]
    recommended_candidate_group_id: str = ""
    site_resolution_reason: str = ""
    requires_site_confirmation: bool = True
    lidar_coverage: RoofMeasureLidarCoverage
    attributions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RoofMeasureSectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: str = Field(min_length=1, max_length=100)
    polygon: list[RoofMeasurePoint] = Field(min_length=3, max_length=200)
    holes: list[list[RoofMeasurePoint]] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_holes(self) -> "RoofMeasureSectionInput":
        if any(len(hole) < 3 or len(hole) > 200 for hole in self.holes):
            raise ValueError("Each polygon hole must contain 3 to 200 points.")
        return self


class OpenAIActionFile(BaseModel):
    """Native file attachment returned by a ChatGPT GPT Action."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    mime_type: str = Field(pattern=r"^[\w.+-]+/[\w.+-]+$")
    content: str = Field(description="Base64-encoded file content.")


class RoofMeasureSegmentationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    selected_footprint_ids: list[
        Annotated[str, Field(min_length=1, max_length=100)]
    ] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def validate_unique_footprints(self) -> "RoofMeasureSegmentationRequest":
        if len(set(self.selected_footprint_ids)) != len(
            self.selected_footprint_ids
        ):
            raise ValueError("selected_footprint_ids must be unique.")
        return self


class RoofMeasureSegmentationCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(pattern=r"^sam2-[a-f0-9]{16}$")
    rank: int = Field(ge=1, le=3)
    provider_rank: int = Field(ge=1, le=3)
    boundary_refinement: Literal["sam2", "sam2_lidar_high_band"] = "sam2"
    geometry_refinement: Literal["mask_polygon", "dominant_orthogonal"] = (
        "mask_polygon"
    )
    geometry_area_drift_fraction: float = Field(default=0, ge=0, le=0.015)
    model_name: str
    model_version: str
    model_score: float
    selection_score: float
    footprint_overlap: float
    footprint_coverage: float
    area_ratio_to_footprint: float
    lidar_roof_support_fraction: float | None = Field(
        default=None,
        ge=0,
        le=1,
    )
    lidar_sampled_fraction: float | None = Field(default=None, ge=0, le=1)
    lidar_ground_fraction: float | None = Field(default=None, ge=0, le=1)
    lidar_elevated_coverage: float | None = Field(default=None, ge=0, le=1)
    lidar_boundary_score: float | None = Field(default=None, ge=0, le=1)
    lidar_roof_leakage_outside: float | None = Field(
        default=None,
        ge=0,
        le=1,
    )
    plan_area_sqft: float = Field(gt=0)
    perimeter_ft: float = Field(gt=0)
    section_count: int = Field(ge=1)


class RoofMeasureSegmentationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["spraytec.roof_measure_sam2_candidates.v1"]
    context_id: str
    selected_footprint_ids: list[str]
    recommended_candidate_id: str
    requires_candidate_confirmation: Literal[True]
    candidates: list[RoofMeasureSegmentationCandidate] = Field(
        min_length=1,
        max_length=3,
    )
    candidate_overlay_url: str
    candidate_overlay_preview_media_type: Literal["image/jpeg"]
    candidate_overlay_preview_base64: str
    openai_file_response: list[OpenAIActionFile] = Field(
        serialization_alias="openaiFileResponse",
        validation_alias="openaiFileResponse",
        min_length=1,
        max_length=1,
    )
    model_name: str
    model_version: str
    lidar_guidance_used: bool = False
    lidar_points: int = Field(default=0, ge=0)
    lidar_image_points: int = Field(default=0, ge=0)
    lidar_cell_pixels: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)


class RoofMeasureCalculationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    selected_footprint_ids: list[
        Annotated[str, Field(min_length=1, max_length=100)]
    ] = Field(default_factory=list, max_length=12)
    sections: list[RoofMeasureSectionInput] = Field(default_factory=list, max_length=20)
    sam2_candidate_id: str = Field(
        default="",
        pattern=r"^(?:|sam2-[a-f0-9]{16})$",
        description=(
            "Estimator-confirmed candidate returned by segmentRoofMeasureContext."
        ),
    )
    pitch_rise_per_12: float | None = Field(
        default=None,
        ge=0,
        le=24,
        description=(
            "Optional roof rise in inches per 12 inches of run. When omitted, "
            "only plan-view area is returned. Use 0 explicitly for a flat roof."
        ),
    )

    @model_validator(mode="after")
    def validate_measurement_source(self) -> "RoofMeasureCalculationRequest":
        source_count = sum(
            (
                bool(self.selected_footprint_ids),
                bool(self.sections),
                bool(self.sam2_candidate_id),
            )
        )
        if source_count != 1:
            raise ValueError(
                "Provide exactly one of selected_footprint_ids, custom sections, "
                "or sam2_candidate_id."
            )
        if len(set(self.selected_footprint_ids)) != len(self.selected_footprint_ids):
            raise ValueError("selected_footprint_ids must be unique.")
        return self


class RoofMeasureSectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: str
    source: Literal["footprint", "custom_polygon", "sam2_candidate"]
    plan_area_sqft: float
    perimeter_ft: float
    surface_area_sqft: float | None = None


class RoofMeasureCalculationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    context_id: str
    measurement_basis: str
    total_plan_area_sqft: float
    total_perimeter_ft: float
    pitch_rise_per_12: float | None = None
    total_surface_area_sqft: float | None = None
    sections: list[RoofMeasureSectionResult]
    selected_footprint_overlay_url: str
    selected_footprint_overlay_preview_media_type: Literal["image/jpeg"]
    selected_footprint_overlay_preview_base64: str = Field(
        description=(
            "Base64-encoded JPEG with the selected roof sections highlighted on "
            "the satellite image. Decode and display this exact image with the "
            "measurement result; do not substitute a geometry-only drawing."
        )
    )
    openai_file_response: list[OpenAIActionFile] = Field(
        serialization_alias="openaiFileResponse",
        validation_alias="openaiFileResponse",
        description=(
            "Native ChatGPT Action attachment containing the exact selected-footprint "
            "overlay. ChatGPT should attach this file directly to the answer."
        ),
        min_length=1,
        max_length=1,
    )
    review_status: Literal["requires_estimator_verification"]
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class EstimateWorkbookDimensionRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=200)
    area_sqft: float = Field(ge=-10_000_000, le=10_000_000)


class EstimateWorkbookAreaScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_id: str = Field(min_length=1, max_length=100)
    parent_scope_id: str = Field(default="", max_length=100)
    scope_role: Literal["exclusive_area", "nested_sub_scope", "deduction"]
    label: str = Field(default="", max_length=200)
    area_sqft: float = Field(gt=0, le=10_000_000)
    action: str = Field(default="", max_length=500)
    existing_system: str = Field(default="", max_length=300)
    proposed_assembly: str = Field(default="", max_length=500)
    decking_replacement_sqft: float | None = Field(
        default=None,
        ge=0,
        le=10_000_000,
    )
    evidence_text: str = Field(default="", max_length=1_000)


class EstimateWorkbookStructuredScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_type: Literal["roofing"] = "roofing"
    declared_total_area_sqft: float = Field(gt=0, le=10_000_000)
    area_scopes: list[EstimateWorkbookAreaScope] = Field(min_length=1, max_length=20)
    area_reconciliation: dict[str, Any] = Field(default_factory=dict)


class EstimateWorkbookHeader(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_name: str = Field(min_length=1, max_length=200)
    job_type: str = Field(default="Roofing", max_length=120)
    site_address: str = Field(default="", max_length=300)
    city_state_zip: str = Field(default="", max_length=200)
    contact: str = Field(default="", max_length=200)
    title: str = Field(default="", max_length=120)
    email: str = Field(default="", max_length=254)
    phone: str = Field(default="", max_length=80)
    estimator: str = Field(default="", max_length=200)
    estimated_sqft: float = Field(gt=0, le=10_000_000)
    mobilizations: float | None = Field(default=None, ge=0, le=1_000)
    estimated_days: float | None = Field(default=None, ge=0, le=10_000)
    estimated_hours: float | None = Field(default=None, ge=0, le=10_000_000)
    estimated_crew_size: int | None = Field(default=None, ge=1, le=8)
    repair_area_description: str = Field(default="", max_length=200)
    warranty_description: str = Field(default="", max_length=200)
    sqft_calculation_rows: list[EstimateWorkbookDimensionRow] = Field(
        default_factory=list,
        max_length=12,
        description=(
            "Signed area components for the insulation Sq Ft Calculation sheet; "
            "opening deductions should be negative."
        ),
    )


class EstimateWorkbookMaterial(BaseModel):
    model_config = ConfigDict(extra="forbid")

    concept_id: str = Field(default="", max_length=160)
    category: EstimateMaterialCategory
    item: str = Field(default="", max_length=240)
    include: bool = True
    selector_code: str | float | int | None = None
    area_sqft: float | None = Field(
        default=None,
        ge=0,
        le=10_000_000,
        description="Measured scope area before purchasing or production allowance.",
    )
    basis_sqft: float | None = Field(
        default=None,
        ge=0,
        le=10_000_000,
        description="Formula or purchase basis after any explicit allowance.",
    )
    thickness_inches: float | None = Field(default=None, ge=0, le=100)
    debris_thickness_inches: float | None = Field(default=None, ge=0, le=100)
    size: str = Field(default="", max_length=80)
    unit_price: float | None = Field(
        default=None,
        ge=0,
        le=10_000_000,
        description=(
            "Price in the unit expected by the selected template row. For "
            "roofing_foam this is dollars per pound, not dollars per 1,000-pound "
            "set; use price_per_set when the source price is quoted per set."
        ),
    )
    price_per_set: float | None = Field(
        default=None,
        ge=0,
        le=10_000_000,
        description=(
            "Roofing foam price for one standard 1,000-pound set. The API "
            "converts this to the template's dollars-per-pound input."
        ),
    )
    price_per_square: float | None = Field(default=None, ge=0, le=10_000_000)
    amount: float | None = Field(default=None, ge=0, le=100_000_000)
    quantity: float | None = Field(default=None, ge=0, le=100_000_000)
    estimated_units: float | None = Field(default=None, ge=0, le=100_000_000)
    estimated_gallons: float | None = Field(default=None, ge=0, le=100_000_000)
    estimated_cost: float | None = Field(default=None, ge=0, le=100_000_000)
    gal_per_100_sqft: float | None = Field(default=None, ge=0, le=1_000)
    waste_factor_pct: float | None = Field(default=None, ge=0, le=500)
    yield_factor: float | None = Field(default=None, gt=0, le=10_000_000)
    linear_ft: float | None = Field(default=None, ge=0, le=100_000_000)
    feet_per_unit: float | None = Field(default=None, gt=0, le=100_000)
    trip_count: float | None = Field(
        default=None,
        ge=0,
        le=10_000,
        description=(
            "Number of round trips for a logistics line. Put this on the "
            "sales_inspection_trips or truck_expense item, not on labor."
        ),
    )
    round_trip_miles: float | None = Field(
        default=None,
        ge=0,
        le=100_000,
        description="Current origin-to-site-to-origin miles for each trip.",
    )
    period: float | None = Field(default=None, ge=0, le=10_000)
    days: float | None = Field(default=None, ge=0, le=10_000)
    margin_pct: float | None = Field(default=None, ge=0, le=500)
    quantity_adjustment_reason: str = Field(
        default="",
        max_length=500,
        description=(
            "Required when basis_sqft differs from measured area_sqft; explain "
            "sheet rounding, waste, production allowance, or another reviewed basis."
        ),
    )
    notes: str = Field(default="", max_length=2_000)


class EstimateWorkbookLabor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    concept_id: str = Field(default="", max_length=160)
    task: EstimateLaborTask
    label: str = Field(default="", max_length=240)
    include: bool = True
    days: float | None = Field(default=None, ge=0, le=10_000)
    crew_size: int | None = Field(default=None, ge=1, le=8)
    hours_per_trip: float | None = Field(
        default=None,
        gt=0,
        le=24,
        description=(
            "Loading or traveling hours per trip for labor_loading and "
            "labor_traveling."
        ),
    )
    total_hours: float | None = Field(default=None, ge=0, le=10_000_000)
    hourly_rate: float | None = Field(default=None, ge=0, le=100_000)
    daily_rate: float | None = Field(default=None, ge=0, le=10_000_000)
    estimated_cost: float | None = Field(default=None, ge=0, le=100_000_000)
    notes: str = Field(default="", max_length=2_000)


class EstimateWorkbookAdder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    concept_id: str = Field(default="", max_length=160)
    label: str = Field(min_length=1, max_length=240)
    amount: float | None = Field(default=None, ge=0, le=100_000_000)
    include: bool = True
    needs_review: bool = False
    notes: str = Field(default="", max_length=2_000)


class EstimateWorkbookLogistics(EstimateWorkbookMaterial):
    category: EstimateLogisticsCategory


class EstimateWorkbookPricing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overhead_pct: float | None = Field(
        default=None,
        ge=0,
        le=500,
        description="Defaults to the standard template-family percentage when omitted.",
    )
    profit_pct: float | None = Field(
        default=None,
        ge=0,
        le=500,
        description="Defaults to the standard template-family percentage when omitted.",
    )


class EstimateWorkbookWarranty(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include: bool = True
    manufacturer: str = Field(default="", max_length=160)
    years: int | None = Field(default=None, ge=1, le=100)
    warranty_type: str = Field(default="", max_length=120)
    area_sqft: float | None = Field(
        default=None,
        gt=0,
        le=10_000_000,
        description=(
            "Warranty area in square feet. Current templates calculate warranty "
            "cost over header.estimated_sqft, so this must match that value when "
            "provided. Use adders.amount for a localized flat allowance."
        ),
    )
    pricing_basis: Literal["per_sqft"] = Field(
        default="per_sqft",
        description=(
            "Warranty pricing is calculated per square foot by the template. "
            "Use an adders.amount entry for a flat warranty allowance."
        ),
    )
    unit_cost: float | None = Field(
        default=None,
        ge=0,
        le=25,
        description=(
            "Warranty cost in dollars per square foot, multiplied by area_sqft. "
            "This is not a flat amount; use adders.amount for a flat cost."
        ),
    )
    notes: str = Field(default="", max_length=2_000)


class EstimateWorkbookDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_type: Literal["roofing", "insulation", "flooring"] = "roofing"
    structured_scope: EstimateWorkbookStructuredScope | None = Field(
        default=None,
        description=(
            "Structured roofing takeoff used for deterministic area-integrity "
            "validation before workbook creation."
        ),
    )
    labor_plan_mode: Literal["api_recommendation", "estimator_override"] = Field(
        default="api_recommendation",
        description=(
            "For roofing estimates with structured scope, the API recommendation "
            "is reapplied immediately before workbook generation. Use "
            "estimator_override only for a deliberately reviewed exception."
        ),
    )
    labor_override_reason: str = Field(
        default="",
        max_length=1_000,
        description=(
            "Required when labor_plan_mode is estimator_override; explain why the "
            "reviewed labor plan differs from the API recommendation."
        ),
    )
    planning_snapshot_token: str = Field(
        default="",
        max_length=30_000,
        description=(
            "Unchanged token returned by the final estimator context call. A "
            "valid matching token avoids repeating labor and logistics retrieval."
        ),
    )
    header: EstimateWorkbookHeader
    pricing: EstimateWorkbookPricing = Field(default_factory=EstimateWorkbookPricing)
    warranty: EstimateWorkbookWarranty | None = None
    materials: list[EstimateWorkbookMaterial] = Field(default_factory=list, max_length=40)
    labor: list[EstimateWorkbookLabor] = Field(default_factory=list, max_length=25)
    logistics: list[EstimateWorkbookLogistics] = Field(default_factory=list, max_length=25)
    adders: list[EstimateWorkbookAdder] = Field(default_factory=list, max_length=20)
    scope_of_work: list[Annotated[str, Field(min_length=1, max_length=1_000)]] = Field(
        default_factory=list,
        max_length=30,
    )
    spec_notes: list[Annotated[str, Field(min_length=1, max_length=1_000)]] = Field(
        default_factory=list,
        max_length=20,
    )

    @model_validator(mode="after")
    def apply_standard_commercial_percentages(self) -> "EstimateWorkbookDraft":
        defaults = {
            "roofing": (35.0, 15.0),
            "flooring": (35.0, 15.0),
            "insulation": (30.0, 10.0),
        }
        overhead, profit = defaults[self.template_type]
        if self.pricing.overhead_pct is None:
            self.pricing.overhead_pct = overhead
        if self.pricing.profit_pct is None:
            self.pricing.profit_pct = profit
        if (
            self.labor_plan_mode == "estimator_override"
            and not self.labor_override_reason.strip()
        ):
            raise ValueError(
                "labor_override_reason is required when labor_plan_mode is "
                "estimator_override."
            )
        return self


class EstimateWorkbookRequest(EstimateWorkbookDraft):
    confirmed: bool = Field(
        default=False,
        description="Must be true after the estimator explicitly approves file creation.",
    )


class EstimateWorkbookOption(EstimateWorkbookDraft):
    option_label: str = Field(
        min_length=1,
        max_length=100,
        description=(
            "User-facing option name used in the output filename, such as "
            "10-year warranty or Base area plus alternate bay."
        ),
    )


class EstimateWorkbookOptionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: bool = Field(
        default=False,
        description=(
            "Must be true after the estimator explicitly approves creation of "
            "every listed option."
        ),
    )
    options: list[EstimateWorkbookOption] = Field(min_length=2, max_length=6)

    @model_validator(mode="after")
    def require_unique_option_labels(self) -> "EstimateWorkbookOptionsRequest":
        normalized = [option.option_label.strip().casefold() for option in self.options]
        if len(normalized) != len(set(normalized)):
            raise ValueError("Each estimate option must have a unique option_label.")
        return self


class EstimateWorkbookResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    artifact_id: str
    file_name: str
    template_type: str
    download_url: str
    expires_at: str
    calculated_outputs: dict[str, float] = Field(default_factory=dict)
    template_profile: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class EstimateWorkbookOptionArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    option_label: str
    artifact_id: str
    file_name: str
    template_type: str
    download_url: str
    expires_at: str
    calculated_outputs: dict[str, float] = Field(default_factory=dict)
    template_profile: dict[str, Any] = Field(default_factory=dict)


class EstimateWorkbookOptionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    artifacts: list[EstimateWorkbookOptionArtifact]
    warnings: list[str] = Field(default_factory=list)


class JobSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        default="",
        max_length=200,
        description="Customer, job name, address, city, folder name, or job ID text.",
    )
    job_ids: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list,
        max_length=25,
        description="Optional authoritative job IDs to retrieve.",
    )
    division: str = Field(default="", max_length=100)
    pipeline_status: str = Field(default="", max_length=100)
    workflow_status: str = Field(default="", max_length=100)
    owner: str = Field(
        default="",
        max_length=200,
        description="Exact deal owner or assigned user.",
    )
    job_year: int | None = Field(
        default=None,
        ge=2000,
        le=2100,
        description="Source job year, such as 2026. Omit to include all years.",
    )
    needs_attention: bool | None = Field(
        default=None,
        description="True returns jobs with operational warnings or missing artifacts.",
    )
    limit: int = Field(default=10, ge=1, le=25)


class JobSourceLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: str
    job_id: str
    label: str
    url: str
    document_id: str | None = None


class JobSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    as_of: str
    filters_applied: dict[str, Any]
    headline_metrics: dict[str, Any]
    records: list[dict[str, Any]] = Field(default_factory=list, max_length=25)
    attention_items: list[dict[str, Any]] = Field(default_factory=list, max_length=25)
    source_links: list[JobSourceLink] = Field(default_factory=list)
    source_tables: list[str] = Field(default_factory=list)
    data_freshness: dict[str, Any] = Field(default_factory=dict)
    coverage: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    response_budget: dict[str, Any] = Field(default_factory=dict)


class SharePointDocumentSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        min_length=1,
        max_length=500,
        description="File-name, folder-path, or extracted document-text query.",
    )
    job_id: str = Field(
        default="",
        max_length=200,
        description="Optional authoritative Spray-Tec job ID.",
    )
    document_type: str = Field(
        default="",
        max_length=100,
        description="Optional indexed type such as estimate, proposal, contract, or warranty.",
    )
    limit: int = Field(default=10, ge=1, le=20)


class SharePointDocumentSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    query: str
    filters_applied: dict[str, Any]
    records: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    coverage: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class SharePointDocumentFetchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(
        min_length=1,
        max_length=300,
        description="Stable document_id returned by searchSharePointDocuments.",
    )
    max_chars: int = Field(
        default=40_000,
        ge=1_000,
        le=80_000,
        description="Maximum readable source text returned to the Assistant.",
    )
    allow_graph_download: bool = Field(
        default=True,
        description=(
            "When stored extracted text is missing, use stored Graph identifiers "
            "for one bounded read-only download and temporary extraction."
        ),
    )


class SharePointDocumentFetchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    document_id: str
    job_id: str
    document_type: str | None = None
    file_name: str
    sharepoint_url: str | None = None
    folder_path: str | None = None
    relative_path: str | None = None
    mime_type: str | None = None
    file_extension: str | None = None
    size_bytes: int | None = None
    modified_at: str | None = None
    source_year: int | None = None
    source_division: str | None = None
    extraction_status: str | None = None
    extraction_method: str | None = None
    extracted_at: str | None = None
    requires_ocr: bool = False
    content: str = ""
    content_source: str
    content_available: bool
    included_sections: int = 0
    total_sections: int = 0
    truncated: bool = False
    warnings: list[str] = Field(default_factory=list)


class JobContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    as_of: str
    job_id: str
    job: dict[str, Any]
    workflow: dict[str, Any] = Field(default_factory=dict)
    schedule: dict[str, Any] = Field(default_factory=dict)
    tracking_summary: list[dict[str, Any]] = Field(default_factory=list, max_length=5)
    recent_daily_tracking: list[dict[str, Any]] = Field(
        default_factory=list,
        max_length=10,
    )
    recent_office_activity: list[dict[str, Any]] = Field(
        default_factory=list,
        max_length=10,
    )
    documents: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    attention_items: list[dict[str, Any]] = Field(default_factory=list, max_length=25)
    source_links: list[JobSourceLink] = Field(default_factory=list)
    source_tables: list[str] = Field(default_factory=list)
    data_freshness: dict[str, Any] = Field(default_factory=dict)
    coverage: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    response_budget: dict[str, Any] = Field(default_factory=dict)


class WarrantySummaryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_ids: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list,
        max_length=25,
        description="Optional authoritative job IDs.",
    )
    job_year: int | None = Field(default=None, ge=2000, le=2100)
    division: str = Field(default="", max_length=100)
    warranty_status: Literal["", "issued", "reported", "proposed"] = ""
    expiring_after: date | None = Field(
        default=None,
        description="Return warranties expiring on or after this date.",
    )
    expiring_before: date | None = Field(
        default=None,
        description="Return warranties expiring on or before this date.",
    )
    needs_review: bool | None = Field(
        default=None,
        description="Filter records with conflicts, missing duration, or uncertain start dates.",
    )
    limit: int = Field(default=10, ge=1, le=25)


class WarrantySummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    as_of: str
    filters_applied: dict[str, Any]
    headline_metrics: dict[str, Any]
    status_rollup: list[dict[str, Any]] = Field(default_factory=list)
    category_rollup: list[dict[str, Any]] = Field(default_factory=list)
    records: list[dict[str, Any]] = Field(default_factory=list, max_length=25)
    attention_items: list[dict[str, Any]] = Field(default_factory=list, max_length=25)
    review_queue_summary: dict[str, Any] = Field(default_factory=dict)
    data_quality_tasks: list[dict[str, Any]] = Field(default_factory=list, max_length=25)
    source_links: list[JobSourceLink] = Field(default_factory=list)
    source_tables: list[str] = Field(default_factory=list)
    data_freshness: dict[str, Any] = Field(default_factory=dict)
    coverage: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    response_budget: dict[str, Any] = Field(default_factory=dict)


class SalesPipelineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    division: str = Field(default="", max_length=100)
    owner: str = Field(default="", max_length=200)
    job_year: int | None = Field(
        default=None,
        ge=2000,
        le=2100,
        description="Source job year, such as 2026. Omit to include all years.",
    )
    pipeline_statuses: list[
        Annotated[str, Field(min_length=1, max_length=100)]
    ] = Field(default_factory=list, max_length=10)
    include_completed: bool = Field(
        default=False,
        description="Include completed and other non-open pipeline statuses.",
    )
    limit: int = Field(default=10, ge=1, le=25)


class SalesFollowupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    division: str = Field(default="", max_length=100)
    owner: str = Field(default="", max_length=200)
    job_year: int | None = Field(
        default=None,
        ge=2000,
        le=2100,
        description="Source job year, such as 2026. Omit to include all years.",
    )
    followup_status: str = Field(
        default="",
        max_length=100,
        description="Exact prepared follow-up status, such as Ready for follow-up.",
    )
    overdue_only: bool = False
    unassigned_only: bool = False
    limit: int = Field(default=10, ge=1, le=25)


class SalesIntelligenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    as_of: str
    filters_applied: dict[str, Any]
    headline_metrics: dict[str, Any]
    stage_rollup: list[dict[str, Any]] = Field(default_factory=list)
    owner_rollup: list[dict[str, Any]] = Field(default_factory=list)
    records: list[dict[str, Any]] = Field(default_factory=list, max_length=25)
    attention_items: list[dict[str, Any]] = Field(default_factory=list, max_length=25)
    source_links: list[JobSourceLink] = Field(default_factory=list)
    source_tables: list[str] = Field(default_factory=list)
    data_freshness: dict[str, Any] = Field(default_factory=dict)
    coverage: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    response_budget: dict[str, Any] = Field(default_factory=dict)


class OperationsBacklogRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    division: str = Field(default="", max_length=100)
    job_year: int | None = Field(
        default=None,
        ge=2000,
        le=2100,
        description="Source job year, such as 2026. Omit to include all years.",
    )
    readiness_statuses: list[
        Annotated[str, Field(min_length=1, max_length=100)]
    ] = Field(default_factory=list, max_length=10)
    unscheduled_only: bool = False
    needs_attention: bool | None = None
    include_completed: bool = Field(
        default=False,
        description="Include jobs the operations snapshot classifies as completed.",
    )
    limit: int = Field(default=10, ge=1, le=25)


class OperationsScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    division: str = Field(default="", max_length=100)
    crew_leader: str = Field(default="", max_length=200)
    job_year: int | None = Field(
        default=None,
        ge=2000,
        le=2100,
        description="Source job year, distinct from the schedule date window.",
    )
    start_date: date | None = Field(
        default=None,
        description=(
            "Inclusive schedule window start. When both dates are omitted, "
            "the normal schedule view defaults to today."
        ),
    )
    end_date: date | None = Field(
        default=None,
        description=(
            "Inclusive schedule window end. When both dates are omitted, "
            "the normal schedule view defaults to 14 days from today."
        ),
    )
    risk_only: bool = Field(
        default=False,
        description=(
            "Return jobs with a persisted schedule blocker or production-risk status. "
            "Without explicit dates, this searches all active operations rows."
        ),
    )
    include_unscheduled: bool = False
    include_completed: bool = False
    limit: int = Field(default=10, ge=1, le=25)


class OperationsIntelligenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    as_of: str
    filters_applied: dict[str, Any]
    headline_metrics: dict[str, Any]
    readiness_rollup: list[dict[str, Any]] = Field(default_factory=list)
    division_rollup: list[dict[str, Any]] = Field(default_factory=list)
    schedule_health_rollup: list[dict[str, Any]] = Field(default_factory=list)
    project_health_rollup: list[dict[str, Any]] = Field(default_factory=list)
    crew_rollup: list[dict[str, Any]] = Field(default_factory=list)
    records: list[dict[str, Any]] = Field(default_factory=list, max_length=25)
    attention_items: list[dict[str, Any]] = Field(default_factory=list, max_length=25)
    source_links: list[JobSourceLink] = Field(default_factory=list)
    source_tables: list[str] = Field(default_factory=list)
    data_freshness: dict[str, Any] = Field(default_factory=dict)
    coverage: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    response_budget: dict[str, Any] = Field(default_factory=dict)


class OfficeActivityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee: str = Field(
        default="",
        max_length=200,
        description="Exact employee name as captured in the source timesheet.",
    )
    code: str = Field(
        default="",
        max_length=100,
        description="Exact office work code, such as Estimating or Sales Call.",
    )
    project_query: str = Field(
        default="",
        max_length=200,
        description="Case-insensitive text contained in the source project label.",
    )
    start_date: date | None = Field(
        default=None,
        description="Inclusive activity start date. Defaults to six days before end_date.",
    )
    end_date: date | None = Field(
        default=None,
        description="Inclusive activity end date. Defaults to today.",
    )
    timed_only: bool = Field(
        default=False,
        description="Exclude activity-only touches without a positive captured duration.",
    )
    limit: int = Field(default=10, ge=1, le=25)


class OfficeActivityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    as_of: str
    filters_applied: dict[str, Any]
    headline_metrics: dict[str, Any]
    employee_rollup: list[dict[str, Any]] = Field(default_factory=list, max_length=25)
    code_rollup: list[dict[str, Any]] = Field(default_factory=list, max_length=25)
    project_rollup: list[dict[str, Any]] = Field(default_factory=list, max_length=25)
    daily_rollup: list[dict[str, Any]] = Field(default_factory=list, max_length=92)
    records: list[dict[str, Any]] = Field(default_factory=list, max_length=25)
    attention_items: list[dict[str, Any]] = Field(default_factory=list, max_length=25)
    source_links: list[JobSourceLink] = Field(default_factory=list)
    source_tables: list[str] = Field(default_factory=list)
    data_freshness: dict[str, Any] = Field(default_factory=dict)
    coverage: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    response_budget: dict[str, Any] = Field(default_factory=dict)


class OfficeJobProgressRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    division: str = Field(default="", max_length=100)
    employee: str = Field(
        default="",
        max_length=200,
        description="Exact employee name as captured in the source timesheet.",
    )
    project_query: str = Field(
        default="",
        max_length=200,
        description="Case-insensitive text contained in the source project label.",
    )
    lookback_days: int = Field(
        default=90,
        ge=7,
        le=365,
        description="Calendar-day activity lookback ending today.",
    )
    stalled_after_days: int = Field(
        default=7,
        ge=1,
        le=90,
        description="Mark projects stalled after this many days without activity.",
    )
    stalled_only: bool = False
    include_unmatched: bool = Field(
        default=True,
        description="Include project labels needing job-link review.",
    )
    include_closed: bool = Field(
        default=False,
        description="Include completed, invoiced, cancelled, or closed linked jobs.",
    )
    limit: int = Field(default=10, ge=1, le=25)


class OfficeJobProgressResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    as_of: str
    truth_class: str
    methodology: dict[str, Any]
    filters_applied: dict[str, Any]
    headline_metrics: dict[str, Any]
    link_status_rollup: list[dict[str, Any]] = Field(default_factory=list)
    records: list[dict[str, Any]] = Field(default_factory=list, max_length=25)
    owner_priorities: list[dict[str, Any]] = Field(default_factory=list, max_length=5)
    attention_items: list[dict[str, Any]] = Field(default_factory=list, max_length=25)
    source_links: list[JobSourceLink] = Field(default_factory=list)
    source_tables: list[str] = Field(default_factory=list)
    data_freshness: dict[str, Any] = Field(default_factory=dict)
    coverage: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    response_budget: dict[str, Any] = Field(default_factory=dict)


class ProductionBudgetHealthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_ids: list[
        Annotated[str, Field(min_length=1, max_length=200)]
    ] = Field(
        default_factory=list,
        max_length=25,
        description=(
            "Optional authoritative job IDs. Leave empty for the prioritized "
            "portfolio view."
        ),
    )
    division: str = Field(default="", max_length=100)
    job_year: int | None = Field(
        default=None,
        ge=2000,
        le=2100,
        description="Source job year, such as 2026. Omit to include all years.",
    )
    over_plan_only: bool = Field(
        default=False,
        description="Return only jobs whose comparable tracked usage exceeds plan.",
    )
    include_no_actuals: bool = Field(
        default=False,
        description=(
            "Include jobs with an estimate-derived budget but no comparable "
            "tracked quantities or hours."
        ),
    )
    include_completed: bool = Field(
        default=False,
        description="Include completed, invoiced, cancelled, or closed jobs.",
    )
    limit: int = Field(default=10, ge=1, le=25)


class ProductionBudgetHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    as_of: str
    truth_class: str
    methodology: dict[str, Any]
    filters_applied: dict[str, Any]
    headline_metrics: dict[str, Any]
    bucket_rollup: list[dict[str, Any]] = Field(default_factory=list)
    portfolio_rankings: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    records: list[dict[str, Any]] = Field(default_factory=list, max_length=25)
    bucket_details: list[dict[str, Any]] = Field(default_factory=list, max_length=125)
    attention_items: list[dict[str, Any]] = Field(default_factory=list, max_length=25)
    source_links: list[JobSourceLink] = Field(default_factory=list)
    source_tables: list[str] = Field(default_factory=list)
    data_freshness: dict[str, Any] = Field(default_factory=dict)
    coverage: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    response_budget: dict[str, Any] = Field(default_factory=dict)


ChartDatasetName = Literal[
    "sales_pipeline_by_stage",
    "sales_pipeline_by_owner",
    "operations_backlog_by_division",
    "operations_backlog_by_readiness",
    "operations_schedule_by_crew",
    "operations_schedule_by_health",
    "operations_schedule_gantt",
    "office_activity_by_day",
    "office_activity_by_employee",
    "office_activity_by_code",
    "office_job_progress",
    "production_budget_by_job",
    "production_budget_by_bucket",
    "sales_pipeline_history",
    "operations_backlog_history",
    "production_budget_history",
]


class ChartDatasetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset: ChartDatasetName
    division: str = Field(default="", max_length=100)
    owner: str = Field(default="", max_length=200)
    job_year: int | None = Field(
        default=None,
        ge=2000,
        le=2100,
        description="Source job year for sales and operations chart datasets.",
    )
    pipeline_statuses: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        default_factory=list,
        max_length=10,
    )
    readiness_statuses: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        default_factory=list,
        max_length=10,
    )
    crew_leader: str = Field(default="", max_length=200)
    employee: str = Field(default="", max_length=200)
    code: str = Field(default="", max_length=100)
    project_query: str = Field(default="", max_length=200)
    start_date: date | None = None
    end_date: date | None = None
    job_ids: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list,
        max_length=25,
    )
    include_completed: bool = False
    include_unscheduled: bool = False
    include_no_actuals: bool = False
    unscheduled_only: bool = False
    needs_attention: bool | None = None
    risk_only: bool = False
    over_plan_only: bool = False
    stalled_only: bool = False
    include_unmatched: bool = True
    include_closed: bool = False
    lookback_days: int = Field(default=90, ge=7, le=365)
    stalled_after_days: int = Field(default=7, ge=1, le=90)
    limit: int = Field(default=25, ge=1, le=25)
    gantt_limit: int = Field(
        default=60,
        ge=1,
        le=125,
        description=(
            "Maximum project bars for operations_schedule_gantt. Other chart "
            "datasets continue to use limit."
        ),
    )


class ChartSeries(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    label: str
    unit: Literal["currency", "count", "days", "hours", "ratio"]
    number_format: Literal[
        "currency_0", "integer", "decimal_1", "percent_1"
    ]
    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    axis: Literal["primary", "secondary"]
    panel: str


class ChartSort(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    direction: Literal["ascending", "descending"]
    then_by: list[str] = Field(default_factory=list)


class ChartReferenceLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    value: float
    label: str


class ChartDisplay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["spraytec.chart_display.v1"]
    orientation: Literal["vertical", "horizontal", "timeline"]
    sort: ChartSort
    category_order: list[str] = Field(default_factory=list)
    category_colors: dict[str, str] = Field(default_factory=dict)
    multi_scale_strategy: Literal["shared_axis", "dual_axis", "small_multiples"]
    show_legend: bool
    show_data_labels: bool
    zero_baseline: bool
    reference_lines: list[ChartReferenceLine] = Field(default_factory=list)


class ChartStaging(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aggregation_mode: Literal["endpoint_on_request", "staged_daily_snapshot"]
    source_storage: Literal[
        "operational_query",
        "current_snapshot",
        "hybrid_current_snapshot",
        "append_only_history",
    ]
    snapshot_tables: list[str] = Field(default_factory=list)
    freshness: dict[str, Any] = Field(default_factory=dict)
    historical_series_available: bool
    historical_limitation: str


class ChartDatasetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    dataset: ChartDatasetName
    title: str
    recommended_chart_type: Literal["bar", "line", "gantt"]
    category_field: str
    group_field: str | None = None
    start_field: str | None = None
    end_field: str | None = None
    series: list[ChartSeries]
    display: ChartDisplay
    as_of: str | None = None
    truth_class: str
    filters_applied: dict[str, Any] = Field(default_factory=dict)
    rows: list[dict[str, Any]] = Field(default_factory=list, max_length=365)
    source_tables: list[str] = Field(default_factory=list)
    data_freshness: dict[str, Any] = Field(default_factory=dict)
    staging: ChartStaging
    coverage: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
