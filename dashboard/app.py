from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

import hashlib
import inspect
import json
import logging
import math
import os
import copy
import re
import sys
import time
from io import BytesIO
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError

from jobscan.env import load_project_env

load_project_env()

from foamscope_ui import render_foamscope_page
from jobscan.db_connections import (
    ReadQueryResult,
    create_resilient_engine,
    database_target,
    execute_read_with_retry,
)
from jobscan.document_extraction import search_extracted_text
from jobscan.document_index import search_documents
from jobscan.job_search import (
    get_preferred_job_documents,
    interpret_search_request,
    requested_document_label,
    search_jobs,
    tokenize_search_text,
)
try:
    from jobscan.estimator import estimate_from_field_notes, load_estimator_data
except ImportError:
    from jobscan.estimator import load_estimator_data

    estimate_from_field_notes = None
from jobscan.estimator.schemas import EstimatorData
from jobscan.estimator.evidence_export import write_estimator_evidence_export
from jobscan.estimator import session_capture as estimator_sessions
from jobscan.estimator.estimator_memory import delete_estimator_memory, estimator_memory_frame, update_estimator_memory_status
from jobscan.estimator.workbench import (
    append_edit_history,
    apply_historical_filter_update,
    build_edit_history_rows,
    build_estimating_workbench,
    historical_filter_hash,
    historical_filters_from_scope,
    recalculate_workbench_tables,
    summarize_workbench_totals,
    workbench_to_draft_workbook_inputs,
)
from jobscan.estimator.workbench_export import DEFAULT_WORKBENCH_EXPORT_DIR, export_workbench_review_package
from jobscan.estimator.workbook_writer import DEFAULT_ESTIMATE_OUTPUT_DIR, generate_estimate_workbook, resolve_default_template_path
from jobscan.estimator.photo_evidence import (
    PHOTO_CATEGORY_OPTIONS,
    PHOTO_SIGNAL_OPTIONS,
    analyze_selected_photos_with_ai,
    apply_photo_record_edits,
    build_photo_scope_context,
    merge_photo_ai_analysis,
    stage_uploaded_images,
)
from jobscan.estimator.chat_assistant import estimator_context_cache_stats, run_estimator_chat_turn
from jobscan.estimator.note_images import extract_notes_from_images_with_ai, stage_note_images
from jobscan.estimator.reference_answer_key import (
    answer_key_to_workbook_decision_preferences,
    build_reference_estimate_answer_key,
)
from jobscan.estimator.template_examples import build_template_examples

try:
    from streamlit_calendar import calendar
except ImportError:
    calendar = None


logger = logging.getLogger(__name__)

DEFAULT_DATABASE_URL = "postgresql+psycopg2://spraytec:spraytec_dev_password@127.0.0.1:5433/spraytec_ops"
ESTIMATOR_CHAT_SESSION_DIR = Path("output/estimator_chat_sessions")
ESTIMATOR_CHAT_SESSION_SCHEMA_VERSION = 2
DECISION_EVIDENCE_DISPLAY_COLUMNS = [
    "decision_evidence_summary",
    "decision_evidence_types",
    "why_included",
    "historical_evidence_summary",
    "pricing_evidence_summary",
    "product_evidence_summary",
    "formula_evidence_summary",
    "proposal_source",
    "proposal_confidence",
    "proposal_review_required",
    "proposal_review_reasons",
]
CHOICE_SUMMARY_COLUMN = "why_this_choice"
COMPACT_DIAGNOSTIC_COLUMNS = set(DECISION_EVIDENCE_DISPLAY_COLUMNS) | {
    "decision_evidence_count",
    "decision_confidence",
    "evidence_count",
    "confidence",
    "historical_selector_recommendation",
    "historical_selector_evidence_count",
    "historical_selector_confidence",
    "historical_recommendation",
    "historical_markup_pct",
    "historical_markup_p25",
    "historical_markup_p75",
    "row_traceability",
    "product_match_score",
    "product_name",
    "compatibility_warnings",
    "product_guidance",
    "product_warnings",
    "product_warning_summary",
    "notes",
}

MATERIAL_WORKBENCH_COMPACT_COLUMNS = [
    "include",
    "workbook_row",
    "package",
    "estimator_decision",
    "historical_recommendation",
    "editable_value",
    "calculated_output_summary",
    "item_name",
    "suggested_by_notes_rules",
    "editable_basis_sqft",
    "editable_qty_per_sqft",
    "calculated_quantity",
    "unit",
    "current_unit_price",
    "estimated_cost",
    CHOICE_SUMMARY_COLUMN,
    "product_guidance",
    "product_warning_summary",
]

AREA_TRACE_COMPACT_COLUMNS = [
    "step",
    "formula",
    "inputs",
    "ai_value",
    "deterministic_value",
    "selected_value",
    "selected_source",
    "confidence",
    "conflict",
    "notes",
]

SURFACE_AREA_REVIEW_COLUMNS = [
    "component",
    "quantity",
    "length_ft",
    "width_ft",
    "height_ft",
    "gross_area_sqft",
    "deduction_area_sqft",
    "net_area_sqft",
    "target_r_value",
    "foam_type",
    "edited_thickness_inches",
    "area_formula",
    "notes",
]

SURFACE_AREA_DETAIL_COLUMNS = [
    *SURFACE_AREA_REVIEW_COLUMNS,
    "source_text",
    "confidence",
    "selected_source",
    "ai_value",
    "deterministic_value",
]

INSULATION_PERFORMANCE_COMPACT_COLUMNS = [
    "surface",
    "application_context",
    "net_area_sqft",
    "target_r_value",
    "foam_type",
    "historical_product_decision",
    "selected_current_product",
    "product_knowledge_match",
    "alignment_status",
    "product_fit_status",
    "product_r_value_per_inch",
    "required_thickness_inches",
    "edited_thickness_inches",
    "estimated_sets",
    "estimated_cost",
    "product_guidance",
    "product_warnings",
    "notes",
]

INSULATION_FOAM_TEMPLATE_COMPACT_COLUMNS = [
    "include",
    "workbook_row",
    "editable_selector_code",
    "resolved_template_option",
    CHOICE_SUMMARY_COLUMN,
    "basis_sqft",
    "thickness_inches",
    "yield_or_coverage",
    "unit_price",
    "estimated_units",
    "estimated_sets",
    "estimated_cost",
    "selected_pricing_candidate",
    "compatibility_status",
    "product_guidance_status",
    "product_guidance",
    "notes",
]

INSULATION_DECISION_TEMPLATE_COMPACT_COLUMNS = [
    "include",
    "workbook_row",
    "template_line",
    "labor_task",
    "editable_selector_code",
    "resolved_template_option",
    CHOICE_SUMMARY_COLUMN,
    "basis_sqft",
    "linear_ft",
    "quantity",
    "days",
    "period",
    "trip_count",
    "round_trip_miles",
    "gal_per_100_sqft",
    "waste_factor_pct",
    "feet_per_unit",
    "unit_price",
    "margin_pct",
    "estimated_units",
    "estimated_gallons",
    "estimated_drums",
    "total_hours",
    "crew_size",
    "daily_rate",
    "hourly_rate",
    "formula_mode",
    "estimated_cost",
    "selected_pricing_candidate",
    "compatibility_status",
    "product_guidance_status",
    "product_guidance",
    "notes",
]

INSULATION_DECISION_SECTION_COLUMNS = {
    "insulation_detail_material_template_decisions": [
        "include",
        "workbook_row",
        "template_line",
        "editable_selector_code",
        "resolved_template_option",
        CHOICE_SUMMARY_COLUMN,
        "basis_sqft",
        "linear_ft",
        "quantity",
        "feet_per_unit",
        "unit_price",
        "estimated_units",
        "estimated_cost",
        "selected_pricing_candidate",
        "compatibility_status",
        "product_guidance_status",
        "product_guidance",
        "notes",
    ],
    "insulation_thermal_barrier_template_decisions": [
        "include",
        "workbook_row",
        "template_line",
        "editable_selector_code",
        "resolved_template_option",
        CHOICE_SUMMARY_COLUMN,
        "basis_sqft",
        "gal_per_100_sqft",
        "waste_factor_pct",
        "unit_price",
        "estimated_gallons",
        "estimated_cost",
        "selected_pricing_candidate",
        "compatibility_status",
        "product_guidance_status",
        "product_guidance",
        "notes",
    ],
    "insulation_support_material_template_decisions": [
        "include",
        "workbook_row",
        "template_line",
        "editable_selector_code",
        "resolved_template_option",
        CHOICE_SUMMARY_COLUMN,
        "quantity",
        "estimated_drums",
        "unit_price",
        "estimated_units",
        "estimated_cost",
        "selected_pricing_candidate",
        "compatibility_status",
        "product_guidance_status",
        "product_guidance",
        "notes",
    ],
    "insulation_equipment_logistics_template_decisions": [
        "include",
        "workbook_row",
        "template_line",
        "editable_selector_code",
        "resolved_template_option",
        CHOICE_SUMMARY_COLUMN,
        "days",
        "period",
        "trip_count",
        "round_trip_miles",
        "unit_price",
        "margin_pct",
        "estimated_units",
        "estimated_cost",
        "selected_pricing_candidate",
        "compatibility_status",
        "notes",
    ],
    "insulation_logistics_expense_template_decisions": [
        "include",
        "workbook_row",
        "template_line",
        CHOICE_SUMMARY_COLUMN,
        "hours_per_day",
        "days",
        "people_count",
        "trip_count",
        "unit_price",
        "estimated_units",
        "estimated_cost",
        "formula_model",
        "compatibility_status",
        "compatibility_warnings",
        "notes",
    ],
    "roofing_logistics_expense_template_decisions": [
        "include",
        "workbook_row",
        "template_line",
        CHOICE_SUMMARY_COLUMN,
        "hours_per_day",
        "days",
        "people_count",
        "trip_count",
        "unit_price",
        "estimated_units",
        "estimated_cost",
        "formula_model",
        "compatibility_status",
        "compatibility_warnings",
        "notes",
    ],
    "insulation_compliance_template_decisions": [
        "include",
        "workbook_row",
        "template_line",
        "resolved_template_option",
        CHOICE_SUMMARY_COLUMN,
        "quantity",
        "unit_price",
        "estimated_units",
        "estimated_cost",
        "compatibility_status",
        "notes",
    ],
    "insulation_labor_template_decisions": [
        "include",
        "workbook_row",
        "labor_task",
        CHOICE_SUMMARY_COLUMN,
        "days",
        "crew_size",
        "daily_rate",
        "hourly_rate",
        "total_hours",
        "labor_driver_summary",
        "formula_mode",
        "estimated_cost",
        "compatibility_status",
        "notes",
    ],
    "insulation_pricing_template_decisions": [
        "include",
        "workbook_row",
        "template_line",
        "resolved_template_option",
        CHOICE_SUMMARY_COLUMN,
        "quantity",
        "unit_price",
        "margin_pct",
        "estimated_cost",
        "compatibility_status",
        "notes",
    ],
}

INSULATION_DECISION_SECTIONS = [
    ("insulation_detail_material_template_decisions", "Insulation Detail Materials"),
    ("insulation_thermal_barrier_template_decisions", "Insulation Thermal Barrier / Coating"),
    ("insulation_support_material_template_decisions", "Insulation Support Materials"),
    ("insulation_equipment_logistics_template_decisions", "Insulation Equipment / Logistics"),
    ("insulation_logistics_expense_template_decisions", "Insulation Loading / Travel / Lodging"),
    ("insulation_compliance_template_decisions", "Insulation Compliance"),
    ("insulation_labor_template_decisions", "Insulation Labor Planning"),
    ("insulation_pricing_template_decisions", "Insulation Pricing"),
]

ROOFING_FOAM_TEMPLATE_COMPACT_COLUMNS = [
    "include",
    "workbook_row",
    "editable_selector_code",
    "resolved_template_option",
    CHOICE_SUMMARY_COLUMN,
    "basis_sqft",
    "thickness_inches",
    "yield_or_coverage",
    "unit_price",
    "estimated_units",
    "estimated_sets",
    "estimated_cost",
    "selected_pricing_candidate",
    "compatibility_status",
    "product_guidance",
    "notes",
]

ROOFING_COATING_TEMPLATE_COMPACT_COLUMNS = [
    "include",
    "workbook_row",
    "editable_selector_code",
    "resolved_template_option",
    CHOICE_SUMMARY_COLUMN,
    "basis_sqft",
    "gal_per_100_sqft",
    "waste_factor_pct",
    "unit_price",
    "estimated_gallons",
    "estimated_cost",
    "selected_pricing_candidate",
    "compatibility_status",
    "product_guidance",
    "notes",
]

ROOFING_PRIMER_TEMPLATE_COMPACT_COLUMNS = [
    "include",
    "workbook_row",
    "editable_selector_code",
    "resolved_template_option",
    CHOICE_SUMMARY_COLUMN,
    "basis_sqft",
    "coverage_sqft_per_unit",
    "unit_price",
    "estimated_units",
    "estimated_cost",
    "selected_pricing_candidate",
    "compatibility_status",
    "product_guidance",
    "notes",
]

ROOFING_DETAIL_TEMPLATE_COMPACT_COLUMNS = [
    "include",
    "workbook_row",
    "editable_selector_code",
    "resolved_template_option",
    CHOICE_SUMMARY_COLUMN,
    "linear_ft",
    "estimated_units",
    "unit_price",
    "estimated_cost",
    "selected_pricing_candidate",
    "compatibility_status",
    "product_guidance",
    "notes",
]

ROOFING_DETAIL_QUANTITY_TEMPLATE_COMPACT_COLUMNS = [
    "include",
    "workbook_row",
    "resolved_template_option",
    CHOICE_SUMMARY_COLUMN,
    "linear_ft",
    "units",
    "estimated_units",
    "amount",
    "estimated_cost",
    "compatibility_status",
    "notes",
]

ROOFING_BOARD_FASTENER_TEMPLATE_COMPACT_COLUMNS = [
    "include",
    "workbook_row",
    "editable_selector_code",
    "resolved_template_option",
    CHOICE_SUMMARY_COLUMN,
    "basis_sqft",
    "board_area_sqft",
    "thickness_inches",
    "price_per_square",
    "unit_price_per_thousand",
    "estimated_squares",
    "estimated_units",
    "estimated_cost",
    "selected_pricing_candidate",
    "compatibility_status",
    "product_guidance",
    "notes",
]

ROOFING_BOARD_STOCK_TEMPLATE_COMPACT_COLUMNS = [
    "include",
    "workbook_row",
    "editable_selector_code",
    "resolved_template_option",
    CHOICE_SUMMARY_COLUMN,
    "basis_sqft",
    "thickness_inches",
    "price_per_square",
    "estimated_squares",
    "estimated_cost",
    "selected_pricing_candidate",
    "compatibility_status",
    "product_guidance",
    "notes",
]

ROOFING_FASTENER_PLATE_TEMPLATE_COMPACT_COLUMNS = [
    "include",
    "workbook_row",
    "editable_selector_code",
    "resolved_template_option",
    CHOICE_SUMMARY_COLUMN,
    "board_area_sqft",
    "unit_price_per_thousand",
    "estimated_units",
    "estimated_cost",
    "selected_pricing_candidate",
    "compatibility_status",
    "product_guidance",
    "notes",
]

ROOFING_GRANULES_TEMPLATE_COMPACT_COLUMNS = [
    "include",
    "workbook_row",
    "editable_selector_code",
    "resolved_template_option",
    CHOICE_SUMMARY_COLUMN,
    "basis_sqft",
    "coverage_lbs_per_100_sqft",
    "bag_weight_lbs",
    "unit_price",
    "estimated_units",
    "estimated_cost",
    "selected_pricing_candidate",
    "compatibility_status",
    "product_guidance",
    "notes",
]

ROOFING_EQUIPMENT_TEMPLATE_COMPACT_COLUMNS = [
    "include",
    "workbook_row",
    "editable_selector_code",
    "resolved_template_option",
    CHOICE_SUMMARY_COLUMN,
    "basis_sqft",
    "debris_thickness_inches",
    "debris_thickness_source",
    "size",
    "period",
    "days",
    "unit_price",
    "margin_pct",
    "estimated_units",
    "estimated_cost",
    "compatibility_status",
    "notes",
]

ROOFING_TRAVEL_FREIGHT_TEMPLATE_COMPACT_COLUMNS = [
    "include",
    "workbook_row",
    "resolved_template_option",
    "estimated_units",
    "amount",
    "trip_count",
    "round_trip_miles",
    "unit_price",
    "estimated_cost",
    "compatibility_status",
    CHOICE_SUMMARY_COLUMN,
    "notes",
]

ROOFING_ACCESSORY_TEMPLATE_COMPACT_COLUMNS = [
    "include",
    "workbook_row",
    "editable_selector_code",
    "resolved_template_option",
    CHOICE_SUMMARY_COLUMN,
    "total_coating_gallons",
    "linear_ft",
    "estimated_units",
    "amount",
    "unit_price",
    "estimated_cost",
    "compatibility_status",
    "notes",
]

ROOFING_LOGISTICS_EXPENSE_TEMPLATE_COMPACT_COLUMNS = [
    "include",
    "workbook_row",
    "template_line",
    CHOICE_SUMMARY_COLUMN,
    "hours_per_day",
    "days",
    "people_count",
    "trip_count",
    "unit_price",
    "estimated_units",
    "estimated_cost",
    "formula_model",
    "compatibility_status",
    "compatibility_warnings",
    "notes",
]

ROOFING_FREE_ADDER_TEMPLATE_COMPACT_COLUMNS = [
    "include",
    "workbook_row",
    "template_line",
    "amount",
    "estimated_cost",
    "markup_treatment",
    CHOICE_SUMMARY_COLUMN,
    "compatibility_status",
    "compatibility_warnings",
    "notes",
]

ROOFING_LABOR_TEMPLATE_COMPACT_COLUMNS = [
    "include",
    "workbook_row",
    "labor_task",
    "days",
    "crew_people_selection",
    "crew_selection",
    "selected_daily_rate_cell",
    "daily_rate",
    "hourly_rate",
    "editable_hours_per_1000_sqft",
    "total_hours",
    "formula_mode",
    "estimated_cost",
    CHOICE_SUMMARY_COLUMN,
    "compatibility_status",
]

PRICING_MARKUP_COMPACT_COLUMNS = [
    "include",
    "workbook_row",
    "template_line",
    "markup_pct",
    "historical_markup_pct",
    "historical_markup_p25",
    "historical_markup_p75",
    "base_total",
    "estimated_cost",
    CHOICE_SUMMARY_COLUMN,
    "compatibility_status",
]

LABOR_WORKBENCH_COMPACT_COLUMNS = [
    "include",
    "workbook_row",
    "labor_package",
    "estimator_decision",
    "historical_recommendation",
    "editable_value",
    "calculated_output_summary",
    "suggested_by_notes_rules",
    "days",
    "crew_people_selection",
    "daily_rate",
    "formula_mode",
    "editable_hours_per_1000_sqft",
    "calculated_hours",
    "crew_size",
    "labor_rate",
    "estimated_cost",
    CHOICE_SUMMARY_COLUMN,
]

ADDER_WORKBENCH_COMPACT_COLUMNS = [
    "include",
    "workbook_row",
    "adder",
    "editable_value",
    "evidence_count",
    "confidence",
    CHOICE_SUMMARY_COLUMN,
    "notes",
]

PRICING_EXPORT_COLUMNS = [
    "pricing_item_id",
    "vendor",
    "category",
    "product_name",
    "description",
    "unit_price",
    "unit_of_measure",
    "package_size",
    "price_basis",
    "price_per_gallon",
    "price_per_sqft",
    "price_per_unit",
    "effective_date",
    "status",
    "is_current",
    "needs_review",
    "source_file",
    "source_type",
    "notes",
]

ESTIMATOR_SAMPLE_NOTES = {
    "Metal roof silicone coating": "Metal roof, about 12,000 sqft, rusted fasteners, restaurant in Louisville, silicone coating, medium access.",
    "Coated polyurethane foam roof": "Existing foam roof, about 18,000 sqft, 1.5 inch foam repairs, silicone top coat, commercial building in Lexington.",
    "Spray foam insulation": "Spray foam insulation for warehouse walls, wall area 8,500 sqft, 2 inch foam, easy access in Shelbyville.",
    "Floor coating": "Flooring job, 2,400 sq ft concrete slab, grind and patch prep, epoxy base coat, polyaspartic top coat, flake broadcast, generator needed.",
    "Roof repair": "Roof repair, about 3,000 sqft, leaks around penetrations, rusted metal panels, Louisville KY, difficult access.",
    "Wall insulation": "Wall insulation, wall area 10,000 sqft, metal building, 2 inch spray foam, Cincinnati OH.",
}

ESTIMATE_TYPE_AUTO = "Auto-detect"
ESTIMATE_TYPE_RESTORATION = "Roof Restoration / Coating"
ESTIMATE_TYPE_REPAIR = "Roof Repair"
ESTIMATE_TYPE_INSULATION = "Insulation"
ESTIMATE_TYPE_FLOORING = "Flooring"
ESTIMATE_TYPE_OPTIONS = [
    ESTIMATE_TYPE_AUTO,
    ESTIMATE_TYPE_RESTORATION,
    ESTIMATE_TYPE_REPAIR,
    ESTIMATE_TYPE_INSULATION,
    ESTIMATE_TYPE_FLOORING,
]

REPAIR_MODE_KEYWORDS = [
    "leak",
    "patch",
    "pipe boot",
    "seam repair",
    "fastener repair",
    "service call",
    "emergency",
    "small repair",
    "curb leak",
    "flashing",
    "punch list",
    "skylight curb",
    "drain leak",
]
RESTORATION_MODE_KEYWORDS = [
    "full roof",
    "coating system",
    "warranty",
    "silicone restoration",
    "roof measures",
    "square footage",
    "sqft",
    "sq ft",
    "silicone coating",
    "acrylic coating",
    "roof coating",
]
INSULATION_MODE_KEYWORDS = [
    "foam",
    "spray foam",
    "r-value",
    "thermal barrier",
    "dc315",
    "walls",
    "attic",
    "crawlspace",
    "closed-cell",
    "open-cell",
    "insulation",
]
FLOORING_MODE_KEYWORDS = [
    "flooring",
    "floor coating",
    "floor system",
    "concrete floor",
    "concrete slab",
    "polyaspartic",
    "epoxy floor",
    "epoxy base",
    "flake broadcast",
    "grind and patch",
    "shotblast",
    "shot blast",
]


def get_database_url() -> str:
    try:
        secret_url = st.secrets.get("DATABASE_URL")
    except Exception:
        secret_url = None
    return secret_url or os.getenv("DATABASE_URL") or DEFAULT_DATABASE_URL


DATABASE_URL = get_database_url()

VIEWS = [
    "dashboard_jobs",
    "dashboard_pipeline_rollup",
    "dashboard_job_warnings",
    "dashboard_job_warnings_actionable",
    "dashboard_estimates",
    "dashboard_estimate_line_items",
    "dashboard_estimate_line_items_clean",
    "dashboard_stamp_tracking",
    "dashboard_line_item_rollup",
    "dashboard_line_item_rollup_clean",
    "dashboard_owner_overview",
    "dashboard_top_open_jobs",
    "dashboard_jobs_needing_action",
    "dashboard_jobs_needing_action_clean",
    "dashboard_contracted_backlog",
    "dashboard_estimate_quality_issues",
    "dashboard_division_summary",
    "dashboard_documentation_summary",
    "dashboard_high_value_missing_docs",
    "dashboard_estimate_economics_by_job_type",
    "dashboard_estimate_adders",
    "dashboard_estimate_adders_clean",
    "dashboard_adder_rollup",
    "dashboard_adder_rollup_clean",
    "dashboard_job_value_bands",
    "dashboard_closeout_billing_risk",
    "dashboard_closeout_billing_risk_rollup",
    "dashboard_contracted_backlog_summary",
    "dashboard_estimate_adders_enhanced",
    "dashboard_adder_business_category_rollup",
    "dashboard_sales_followup",
    "dashboard_documentation_risk",
    "pricing_catalog",
    "vsimple_projects",
    "vsimple_sharepoint_job_matches_accepted",
    "vsimple_sharepoint_job_matches",
]

HEALTH_TABLES = [
    ("jobs", "Scanner jobs"),
    ("documents", "Document manifest"),
    ("document_content", "Extracted document content"),
    ("estimate_template_rows", "Estimate template parser"),
    ("pricing_catalog", "Pricing catalog"),
    ("estimate_line_item_classifications", "Legacy line-item classifications"),
    ("job_package_summary", "Relationship package summary"),
    ("relationship_material_qty_ratios", "Material relationship ratios"),
    ("relationship_labor_rates", "Labor relationship rates"),
]

selected_divisions: list[str] = []
selected_pipeline_statuses: list[str] = []
selected_statuses: list[str] = []
customer_search = ""
DASHBOARD_PERF_TIMING_LIMIT = 80

st.set_page_config(page_title="Spray-Tec Ops Dashboard", layout="wide")


@st.cache_resource
def get_engine():
    return create_resilient_engine(DATABASE_URL)


def read_dataframe(connection: Any, statement: Any, params: dict[str, Any] | None = None) -> pd.DataFrame:
    return pd.read_sql_query(statement, connection, params=params)


def load_df_uncached(query: str, params: dict[str, Any] | None = None) -> ReadQueryResult:
    return execute_read_with_retry(get_engine(), text(query), params=params, retries=1, read_fn=read_dataframe)


@st.cache_data(ttl=300, show_spinner=False)
def load_df(query: str) -> pd.DataFrame:
    result = load_df_uncached(query)
    if result.ok:
        return result.value
    raise result.error or RuntimeError("Database read failed.")


def record_dashboard_perf_event(
    name: str,
    *,
    seconds: float,
    detail: str = "",
    row_count: int | None = None,
) -> None:
    if not bool(st.session_state.get("show_dashboard_perf_timings")):
        return
    try:
        event = {
            "name": name,
            "seconds": round(float(seconds), 4),
            "detail": detail,
            "row_count": row_count,
        }
        timings = st.session_state.setdefault("dashboard_perf_timings", [])
        timings.append(event)
        st.session_state["dashboard_perf_timings"] = timings[-DASHBOARD_PERF_TIMING_LIMIT:]
    except Exception:
        logger.debug("dashboard performance event capture failed", exc_info=True)


@contextmanager
def dashboard_perf_step(name: str, *, detail: str = "", row_count: int | None = None):
    start = time.perf_counter()
    try:
        yield
    finally:
        record_dashboard_perf_event(
            name,
            seconds=time.perf_counter() - start,
            detail=detail,
            row_count=row_count,
        )


def reset_dashboard_perf_timings() -> None:
    st.session_state["dashboard_perf_timings"] = []


def render_dashboard_perf_timings() -> None:
    timings = st.session_state.get("dashboard_perf_timings") or []
    if not timings:
        st.caption("No timings recorded yet.")
        return
    timing_df = pd.DataFrame(timings)
    total_seconds = float(timing_df.get("seconds", pd.Series(dtype=float)).sum())
    st.caption(f"Recorded time: {total_seconds:.2f}s across {len(timing_df):,} step(s).")
    st.dataframe(timing_df, width="stretch", hide_index=True, height=220)


def reset_database_connection() -> None:
    try:
        get_engine().dispose()
    except Exception:
        logger.exception("database pool dispose failed")
    st.cache_data.clear()
    try:
        get_engine.clear()
    except Exception:
        logger.exception("database engine cache clear failed")


@st.cache_data(ttl=600, show_spinner=False)
def read_binary_file_cached(path: str, mtime_ns: int, size: int) -> bytes:
    return Path(path).read_bytes()


def cached_download_bytes(path: Path) -> bytes:
    stat = path.stat()
    return read_binary_file_cached(str(path), int(stat.st_mtime_ns), int(stat.st_size))


def database_target_debug_payload() -> dict[str, Any]:
    target = database_target(DATABASE_URL)
    return {
        "host": target.host,
        "database": target.database,
        "appears_neon": target.is_neon,
        "uses_pooler": target.uses_pooler,
    }


def render_neon_pooler_warning() -> None:
    target = database_target(DATABASE_URL)
    if target.is_neon and not target.uses_pooler:
        st.warning(
            "This appears to be a direct Neon database host. "
            "For the Streamlit web app, Neon recommends using the pooled connection string. "
            "CLI migrations and bulk/admin loads may continue to use a direct connection."
        )


def render_database_target_debug() -> None:
    with st.expander("Developer database details"):
        st.write(database_target_debug_payload())
        render_neon_pooler_warning()


def safe_exception_text(exc: Exception) -> str:
    text_value = str(exc)
    if DATABASE_URL and DATABASE_URL in text_value:
        text_value = text_value.replace(DATABASE_URL, "[database URL redacted]")
    text_value = re.sub(
        r"postgresql(?:\+\w+)?://[^@\s]+@",
        "postgresql://[credentials-redacted]@",
        text_value,
        flags=re.IGNORECASE,
    )
    return text_value


def capture_estimator_session_event(action: Any, *args: Any, **kwargs: Any) -> Any:
    """Best-effort Estimating Assistant session capture.

    Estimating should never fail because training-trail persistence is down, but
    production support still gets a useful log trail when the DB write fails.
    """

    try:
        return action(get_engine(), *args, **kwargs)
    except Exception:
        logger.exception("Estimator session capture failed")
        return None


def current_estimator_session_id() -> str:
    return str(st.session_state.get("estimator_session_id") or "")


def capture_estimator_memory_candidates(session_id: str, edit_rows: list[dict[str, Any]], *, template_type: str = "") -> None:
    if not session_id or not edit_rows:
        return
    memory_ids = capture_estimator_session_event(
        estimator_sessions.save_memory_candidates_from_edits,
        session_id,
        edit_rows,
        template_type=template_type,
    )
    if memory_ids:
        st.session_state["estimator_memory_pending_count"] = int(st.session_state.get("estimator_memory_pending_count") or 0) + len(memory_ids)


def explicit_learning_memory_auto_approval_enabled() -> bool:
    value = str(os.getenv("ESTIMATOR_AUTO_APPROVE_EXPLICIT_LEARNING_MEMORY", "1") or "").strip().lower()
    return value not in {"0", "false", "no", "off"}


def reference_template_row_memory_enabled() -> bool:
    value = str(os.getenv("ESTIMATOR_SAVE_ROW_REFERENCE_MEMORIES", "0") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def estimator_chat_learning_mode(chat_result: dict[str, Any] | None) -> bool:
    if not isinstance(chat_result, dict):
        return False
    if bool(chat_result.get("learning_mode")):
        return True
    scope = chat_result.get("scope_overrides") if isinstance(chat_result.get("scope_overrides"), dict) else {}
    return bool(scope.get("explicit_learning_intent"))


def estimator_reference_memory_capture_enabled(chat_result: dict[str, Any] | None) -> bool:
    if estimator_chat_learning_mode(chat_result):
        return True
    if not isinstance(chat_result, dict):
        return False
    scope = chat_result.get("scope_overrides") if isinstance(chat_result.get("scope_overrides"), dict) else {}
    answer_key_mode = str(scope.get("reference_answer_key_mode") or "").strip().lower()
    return answer_key_mode in {"apply", "teach"}


def _normalize_reference_template_type(value: Any) -> str:
    normalized = " ".join(str(value or "").strip().lower().replace("_", " ").replace("-", " ").split())
    if normalized in {"insulation", "spray foam insulation"}:
        return "insulation"
    if normalized in {"roofing", "roof", "roof restoration", "roof coating"}:
        return "roofing"
    if normalized in {"repair", "repairs"}:
        return "repair"
    if normalized == "flooring":
        return "flooring"
    return ""


def _reference_template_type_from_context(
    reference_answer_key: dict[str, Any] | None,
    decision_preferences: list[dict[str, Any]] | None,
) -> str:
    counts: dict[str, int] = {}

    def add_candidate(value: Any, weight: int = 1) -> None:
        candidate = _normalize_reference_template_type(value)
        if candidate:
            counts[candidate] = counts.get(candidate, 0) + weight

    if isinstance(reference_answer_key, dict):
        add_candidate(reference_answer_key.get("template_type"), 5)
        for decision in reference_answer_key.get("decisions") or []:
            if isinstance(decision, dict):
                add_candidate(decision.get("template_type"), 1)
                section = str(decision.get("section") or "")
                if section.startswith("insulation_"):
                    add_candidate("insulation", 1)
                elif section.startswith("roofing_"):
                    add_candidate("roofing", 1)
    for preference in decision_preferences or []:
        if not isinstance(preference, dict):
            continue
        add_candidate(preference.get("template_type"), 2)
        section = str(preference.get("section") or "")
        if section.startswith("insulation_"):
            add_candidate("insulation", 1)
        elif section.startswith("roofing_"):
            add_candidate("roofing", 1)
    if not counts:
        return ""
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def scope_with_reference_template_type(
    scope: dict[str, Any],
    reference_answer_key: dict[str, Any] | None,
    decision_preferences: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    resolved_scope = dict(scope or {})
    template_type = _reference_template_type_from_context(reference_answer_key, decision_preferences)
    if not template_type:
        return resolved_scope
    resolved_scope["template_type"] = template_type
    resolved_scope["estimate_mode"] = template_type
    if template_type == "insulation":
        resolved_scope["division"] = "Insulation"
        project_type = str(resolved_scope.get("project_type") or "").lower()
        if not project_type or "roof" in project_type:
            resolved_scope["project_type"] = "spray foam insulation"
    elif template_type == "roofing":
        resolved_scope["division"] = "Roofing"
    elif template_type == "flooring":
        resolved_scope["division"] = "Flooring"
    elif template_type == "repair":
        resolved_scope["division"] = "Repair"
    return resolved_scope


def preserve_attached_reference_answer_key_context(
    result_payload: dict[str, Any],
    previous_result: dict[str, Any] | None,
    attached_reference_answer_key: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(result_payload, dict):
        return result_payload
    preserved_key = attached_reference_answer_key if isinstance(attached_reference_answer_key, dict) else {}
    if not preserved_key and isinstance(previous_result, dict):
        preserved_key = previous_result.get("reference_answer_key") if isinstance(previous_result.get("reference_answer_key"), dict) else {}
    if preserved_key and not isinstance(result_payload.get("reference_answer_key"), dict):
        result_payload["reference_answer_key"] = preserved_key
    if isinstance(previous_result, dict):
        for key in ("reference_answer_key_mode", "reference_answer_key_label", "reference_answer_key_source_file"):
            if previous_result.get(key) not in (None, "", [], {}) and result_payload.get(key) in (None, "", [], {}):
                result_payload[key] = previous_result.get(key)
    decision_preferences = result_payload.get("workbook_decision_preferences") if isinstance(result_payload.get("workbook_decision_preferences"), list) else []
    result_payload["scope_overrides"] = scope_with_reference_template_type(
        result_payload.get("scope_overrides") if isinstance(result_payload.get("scope_overrides"), dict) else {},
        result_payload.get("reference_answer_key") if isinstance(result_payload.get("reference_answer_key"), dict) else preserved_key,
        decision_preferences,
    )
    result_payload["scope_overrides"] = scope_with_decision_basis_area(
        result_payload.get("scope_overrides") if isinstance(result_payload.get("scope_overrides"), dict) else {},
        decision_preferences,
    )
    return result_payload


def decision_basis_area_from_preferences(decision_preferences: list[dict[str, Any]] | None) -> float:
    candidates: list[float] = []
    preferred_buckets = {"coating", "primer", "foam", "board_stock", "granules", "thermal_barrier_coating"}
    for preference in decision_preferences or []:
        if not isinstance(preference, dict):
            continue
        bucket = text_value(preference.get("template_bucket")).lower()
        if bucket and bucket not in preferred_buckets:
            continue
        values = preference.get("proposed_values") if isinstance(preference.get("proposed_values"), dict) else {}
        for key in ("basis_sqft", "area_sqft", "surface_area_sqft", "estimated_sqft", "net_sqft"):
            area = _surface_review_number(values.get(key))
            if area and area > 0:
                candidates.append(float(area))
                break
    return max(candidates) if candidates else 0.0


def scope_with_decision_basis_area(scope: dict[str, Any], decision_preferences: list[dict[str, Any]] | None) -> dict[str, Any]:
    resolved_scope = dict(scope or {})
    existing_area = (
        _surface_review_number(resolved_scope.get("estimated_sqft"))
        or _surface_review_number(resolved_scope.get("surface_area_sqft"))
        or _surface_review_number(resolved_scope.get("net_sqft"))
        or _surface_review_number(resolved_scope.get("basis_sqft"))
        or 0.0
    )
    if existing_area > 0:
        return resolved_scope
    decision_area = decision_basis_area_from_preferences(decision_preferences)
    if decision_area <= 0:
        return resolved_scope
    resolved_scope.setdefault("estimated_sqft", decision_area)
    resolved_scope.setdefault("surface_area_sqft", decision_area)
    resolved_scope.setdefault("net_sqft", decision_area)
    resolved_scope.setdefault("basis_sqft", decision_area)
    resolved_scope.setdefault("area_source", "workbook_decision_preferences")
    return resolved_scope


def capture_reference_template_memory_candidates(
    session_id: str,
    chat_result: dict[str, Any] | None,
    *,
    template_type: str = "",
) -> None:
    status_key = "estimator_memory_last_capture_status"
    if not session_id or not isinstance(chat_result, dict):
        return
    decision_rows = chat_result.get("workbook_decision_preferences")
    if not isinstance(decision_rows, list) or not decision_rows:
        st.session_state[status_key] = {
            "status": "skipped",
            "message": "No answer-key workbook decisions were available to save as memory.",
        }
        return
    save_cue_memory = getattr(estimator_sessions, "save_cue_memory_candidates_from_reference_template", None)
    save_row_memory = getattr(estimator_sessions, "save_memory_candidates_from_reference_template", None)
    if save_cue_memory is None and save_row_memory is None:
        logger.warning("Reference-template memory capture skipped; session_capture helpers are unavailable.")
        return
    memory_ids: list[str] = []
    scope_context = chat_result.get("scope_overrides") or {}
    if save_cue_memory is not None:
        cue_memory_ids = capture_estimator_session_event(
            save_cue_memory,
            session_id,
            decision_rows,
            template_type=template_type,
            scope_context=scope_context,
        )
        if cue_memory_ids:
            memory_ids.extend(cue_memory_ids)
        elif cue_memory_ids is None:
            st.session_state[status_key] = {
                "status": "failed",
                "message": "Answer-key memory capture failed while saving cue memories. Check application logs for the database error.",
            }
    if (reference_template_row_memory_enabled() or save_cue_memory is None) and save_row_memory is not None:
        row_memory_ids = capture_estimator_session_event(
            save_row_memory,
            session_id,
            decision_rows,
            template_type=template_type,
            scope_context=scope_context,
        )
        if row_memory_ids:
            memory_ids.extend(row_memory_ids)
        elif row_memory_ids is None:
            st.session_state[status_key] = {
                "status": "failed",
                "message": "Answer-key memory capture failed while saving row memories. Check application logs for the database error.",
            }
    if memory_ids:
        auto_approve = estimator_chat_learning_mode(chat_result) and explicit_learning_memory_auto_approval_enabled()
        if auto_approve:
            approved_count = capture_estimator_session_event(
                update_estimator_memory_status,
                memory_ids,
                status="approved",
                approved_by="explicit_learning_chat",
            )
            st.session_state["estimator_memory_auto_approved_count"] = (
                int(st.session_state.get("estimator_memory_auto_approved_count") or 0) + int(approved_count or 0)
            )
            if approved_count:
                st.session_state[status_key] = {
                    "status": "approved",
                    "message": f"Saved and approved {int(approved_count):,} estimator memory item(s) from the applied answer key.",
                    "count": int(approved_count),
                }
                st.success(f"Saved and approved {int(approved_count):,} estimator memory item(s) from this explicit learning message.")
        else:
            st.session_state["estimator_memory_pending_count"] = int(st.session_state.get("estimator_memory_pending_count") or 0) + len(memory_ids)
            st.session_state[status_key] = {
                "status": "pending",
                "message": f"Saved {len(memory_ids):,} estimator memory item(s) for review from the applied answer key.",
                "count": len(memory_ids),
            }
            st.caption(f"Saved {len(memory_ids):,} estimator memory item(s) for review.")
    elif st.session_state.get(status_key, {}).get("status") != "failed":
        st.session_state[status_key] = {
            "status": "empty",
            "message": "The applied answer key was detected, but no estimator memory candidates were generated.",
        }


DAILY_DISPATCH_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS daily_dispatch (
    dispatch_id TEXT PRIMARY KEY,
    dispatch_date DATE NOT NULL,
    job_id TEXT,
    customer TEXT,
    job_name TEXT,
    site_address TEXT,
    start_time TEXT,
    crew_leader TEXT,
    crew_members TEXT,
    work_scope TEXT,
    equipment_notes TEXT,
    material_notes TEXT,
    safety_notes TEXT,
    weather_notes TEXT,
    special_instructions TEXT,
    message_text TEXT,
    send_method TEXT,
    sent_status TEXT,
    sent_at TIMESTAMPTZ,
    raw JSONB,
    updated_at TIMESTAMPTZ DEFAULT NOW()
)
"""


JOB_WORKFLOW_OVERRIDES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS job_workflow_overrides (
    job_id TEXT PRIMARY KEY,
    workflow_status TEXT,
    deal_owner TEXT,
    assigned_user TEXT,
    follow_up_date DATE,
    priority TEXT,
    closed_did_not_get BOOLEAN DEFAULT FALSE,
    review_mark_contracted BOOLEAN DEFAULT FALSE,
    review_mark_completed BOOLEAN DEFAULT FALSE,
    internal_notes TEXT,
    updated_by TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
)
"""


def clean_db_value(value):
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.lower() in {"", "nan", "none", "null", "n/a"}:
            return None
        return stripped
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def text_value(value) -> str:
    cleaned = clean_db_value(value)
    return "" if cleaned is None else str(cleaned).strip()


def schedule_id_for_job(job_id: object) -> str:
    job_text = text_value(job_id)
    digest = hashlib.sha1(job_text.encode("utf-8")).hexdigest()[:20]
    return f"schedule-{digest}"


def dispatch_id_for(dispatch_date: date, job_id: object) -> str:
    key = f"{dispatch_date.isoformat()}||{text_value(job_id)}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
    return f"dispatch-{digest}"


def calculate_end_date(start_value: object, duration_value: object) -> str | None:
    start = pd.to_datetime(start_value, errors="coerce")
    duration = pd.to_numeric(pd.Series([duration_value]), errors="coerce").iloc[0]
    if pd.isna(start) or pd.isna(duration):
        return None
    duration_days = max(int(round(float(duration))), 1)
    return (start.date() + timedelta(days=duration_days - 1)).isoformat()


def ensure_daily_dispatch_table() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text(DAILY_DISPATCH_TABLE_SQL))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_daily_dispatch_date ON daily_dispatch(dispatch_date)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_daily_dispatch_job_id ON daily_dispatch(job_id)"))


def ensure_job_workflow_overrides_table() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text(JOB_WORKFLOW_OVERRIDES_TABLE_SQL))
        conn.execute(text("ALTER TABLE job_workflow_overrides ADD COLUMN IF NOT EXISTS closed_did_not_get BOOLEAN DEFAULT FALSE"))
        conn.execute(text("ALTER TABLE job_workflow_overrides ADD COLUMN IF NOT EXISTS review_mark_contracted BOOLEAN DEFAULT FALSE"))
        conn.execute(text("ALTER TABLE job_workflow_overrides ADD COLUMN IF NOT EXISTS review_mark_completed BOOLEAN DEFAULT FALSE"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_job_workflow_status ON job_workflow_overrides(workflow_status)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_job_workflow_priority ON job_workflow_overrides(priority)"))


def load_schedule_df() -> pd.DataFrame:
    return safe_load("SELECT * FROM crew_schedule")


def save_schedule_rows(df: pd.DataFrame) -> int:
    if df.empty:
        return 0

    schedule_columns = [
        "schedule_id",
        "job_id",
        "assigned_crew_leader",
        "estimated_start_date",
        "estimated_duration_days",
        "estimated_end_date",
        "schedule_status",
        "blocking_issue",
        "priority",
        "schedule_notes",
        "raw",
    ]
    upsert_sql = text(
        """
        INSERT INTO crew_schedule (
            schedule_id,
            job_id,
            assigned_crew_leader,
            estimated_start_date,
            estimated_duration_days,
            estimated_end_date,
            schedule_status,
            blocking_issue,
            priority,
            schedule_notes,
            raw,
            updated_at
        )
        VALUES (
            :schedule_id,
            :job_id,
            :assigned_crew_leader,
            :estimated_start_date,
            :estimated_duration_days,
            :estimated_end_date,
            :schedule_status,
            :blocking_issue,
            :priority,
            :schedule_notes,
            CAST(:raw AS JSONB),
            NOW()
        )
        ON CONFLICT (schedule_id) DO UPDATE SET
            job_id = EXCLUDED.job_id,
            assigned_crew_leader = EXCLUDED.assigned_crew_leader,
            estimated_start_date = EXCLUDED.estimated_start_date,
            estimated_duration_days = EXCLUDED.estimated_duration_days,
            estimated_end_date = EXCLUDED.estimated_end_date,
            schedule_status = EXCLUDED.schedule_status,
            blocking_issue = EXCLUDED.blocking_issue,
            priority = EXCLUDED.priority,
            schedule_notes = EXCLUDED.schedule_notes,
            raw = EXCLUDED.raw,
            updated_at = NOW()
        """
    )

    records = []
    for row in df.to_dict(orient="records"):
        if not text_value(row.get("job_id")):
            continue
        row["schedule_id"] = text_value(row.get("schedule_id")) or schedule_id_for_job(row.get("job_id"))
        row["estimated_end_date"] = calculate_end_date(
            row.get("estimated_start_date"),
            row.get("estimated_duration_days"),
        ) or clean_db_value(row.get("estimated_end_date"))
        record = {column: clean_db_value(row.get(column)) for column in schedule_columns}
        record["raw"] = json.dumps(row, default=str)
        records.append(record)

    if not records:
        return 0

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(upsert_sql, records)
    st.cache_data.clear()
    return len(records)


@st.cache_data(ttl=300, show_spinner=False)
def load_job_workflow_overrides() -> pd.DataFrame:
    try:
        ensure_job_workflow_overrides_table()
        return safe_load(
            """
            SELECT
                job_id,
                workflow_status,
                deal_owner,
                assigned_user,
                follow_up_date,
                priority,
                closed_did_not_get,
                review_mark_contracted,
                review_mark_completed,
                internal_notes,
                updated_by,
                updated_at
            FROM job_workflow_overrides
            """
        )
    except Exception:
        return pd.DataFrame()


def save_job_workflow_override(
    *,
    job_id: object,
    workflow_status: object,
    deal_owner: object,
    assigned_user: object,
    follow_up_date: object,
    priority: object,
    internal_notes: object,
    closed_did_not_get: object = False,
    review_mark_contracted: object = False,
    review_mark_completed: object = False,
    updated_by: object | None = None,
) -> None:
    ensure_job_workflow_overrides_table()
    job_id_text = text_value(job_id)
    if not job_id_text:
        raise ValueError("job_id is required to save workflow overrides.")
    record = {
        "job_id": job_id_text,
        "workflow_status": clean_db_value(workflow_status),
        "deal_owner": clean_db_value(deal_owner),
        "assigned_user": clean_db_value(assigned_user),
        "follow_up_date": clean_db_value(follow_up_date),
        "priority": clean_db_value(priority),
        "closed_did_not_get": truthy_bool(closed_did_not_get),
        "review_mark_contracted": truthy_bool(review_mark_contracted),
        "review_mark_completed": truthy_bool(review_mark_completed),
        "internal_notes": clean_db_value(internal_notes),
        "updated_by": clean_db_value(updated_by),
    }
    upsert_sql = text(
        """
        INSERT INTO job_workflow_overrides (
            job_id,
            workflow_status,
            deal_owner,
            assigned_user,
            follow_up_date,
            priority,
            closed_did_not_get,
            review_mark_contracted,
            review_mark_completed,
            internal_notes,
            updated_by,
            updated_at
        )
        VALUES (
            :job_id,
            :workflow_status,
            :deal_owner,
            :assigned_user,
            :follow_up_date,
            :priority,
            :closed_did_not_get,
            :review_mark_contracted,
            :review_mark_completed,
            :internal_notes,
            :updated_by,
            NOW()
        )
        ON CONFLICT (job_id) DO UPDATE SET
            workflow_status = EXCLUDED.workflow_status,
            deal_owner = EXCLUDED.deal_owner,
            assigned_user = EXCLUDED.assigned_user,
            follow_up_date = EXCLUDED.follow_up_date,
            priority = EXCLUDED.priority,
            closed_did_not_get = EXCLUDED.closed_did_not_get,
            review_mark_contracted = EXCLUDED.review_mark_contracted,
            review_mark_completed = EXCLUDED.review_mark_completed,
            internal_notes = EXCLUDED.internal_notes,
            updated_by = EXCLUDED.updated_by,
            updated_at = NOW()
        """
    )
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(upsert_sql, record)
    st.cache_data.clear()


CREW_COLOR_PALETTE = [
    "#1f77b4",
    "#ff7f0e",
    "#2ca02c",
    "#d62728",
    "#9467bd",
    "#8c564b",
    "#e377c2",
    "#7f7f7f",
    "#bcbd22",
    "#17becf",
]


def get_crew_color(crew_leader: object) -> str:
    leader = text_value(crew_leader) or "Unassigned"
    digest = hashlib.sha1(leader.lower().encode("utf-8")).hexdigest()
    return CREW_COLOR_PALETTE[int(digest[:8], 16) % len(CREW_COLOR_PALETTE)]


@st.cache_data(ttl=600, show_spinner=False)
def relation_columns(relation_name: str) -> set[str]:
    try:
        engine = get_engine()
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = :relation_name
                    """
                ),
                {"relation_name": relation_name},
            ).fetchall()
        return {row[0] for row in rows}
    except Exception:
        return set()


def sql_column(alias: str, columns: set[str], column: str, default: str = "NULL") -> str:
    return f"{alias}.{column}" if column in columns else default


def sql_nonblank_column(alias: str, columns: set[str], column: str, default: str = "NULL") -> str:
    return f"NULLIF({alias}.{column}, '')" if column in columns else default


def sql_coalesce(expressions: list[str], default: str = "NULL") -> str:
    available = [expression for expression in expressions if expression != "NULL"]
    if not available:
        return default
    if len(available) == 1:
        return available[0]
    return f"COALESCE({', '.join(available)})"


def load_schedule_calendar_df() -> pd.DataFrame:
    jobs_cols = relation_columns("dashboard_jobs")
    backlog_cols = relation_columns("dashboard_contracted_backlog")
    estimate_cols = relation_columns("dashboard_estimates")
    folder_expr = sql_coalesce(
        [
            sql_nonblank_column("j", jobs_cols, "folder_url"),
            sql_nonblank_column("j", jobs_cols, "folder_path"),
            sql_nonblank_column("b", backlog_cols, "folder_link_or_path"),
            sql_nonblank_column("b", backlog_cols, "folder_url"),
            sql_nonblank_column("b", backlog_cols, "folder_path"),
        ]
    )
    estimate_expr = sql_coalesce(
        [
            sql_column("j", jobs_cols, "estimate_file"),
            sql_column("b", backlog_cols, "estimate_file"),
        ]
    )
    proposal_expr = sql_coalesce(
        [
            sql_column("j", jobs_cols, "proposal_file"),
            sql_column("b", backlog_cols, "proposal_file"),
            estimate_expr,
        ]
    )
    estimate_select_columns = [
        "job_id",
        "estimated_sqft",
        "price_per_sqft",
        "estimated_labor_hours",
        "estimated_crew_size",
        "job_type",
        "coating_type",
        "foam_type",
        "warranty_amount",
        "equipment_rental_amount",
        "subcontractor_amount",
        "material_subtotal",
        "labor_subtotal",
        "extraction_warnings",
    ]
    estimate_subquery_select = ",\n                ".join(
        f"{column}" if column in estimate_cols else f"NULL AS {column}"
        for column in estimate_select_columns
    )
    query = f"""
        SELECT
            cs.schedule_id,
            cs.job_id,
            COALESCE(j.customer, b.customer) AS customer,
            COALESCE(j.job_name, b.job_name) AS job_name,
            COALESCE(j.division, b.division) AS division,
            COALESCE(j.pipeline_status, b.pipeline_status) AS pipeline_status,
            COALESCE(j.status, b.status) AS status,
            COALESCE(j.estimated_value, b.estimated_value) AS estimated_value,
            COALESCE(b.estimated_duration_days, cs.estimated_duration_days) AS estimated_duration_days,
            {sql_coalesce([sql_column("b", backlog_cols, "estimated_labor_hours"), "e.estimated_labor_hours"])} AS estimated_labor_hours,
            {sql_coalesce([sql_column("b", backlog_cols, "estimated_crew_size"), "e.estimated_crew_size"])} AS estimated_crew_size,
            {sql_coalesce([sql_column("j", jobs_cols, "estimated_sqft"), sql_column("b", backlog_cols, "estimated_sqft"), "e.estimated_sqft"])} AS estimated_sqft,
            {sql_coalesce([sql_column("j", jobs_cols, "price_per_sqft"), sql_column("b", backlog_cols, "price_per_sqft"), "e.price_per_sqft"])} AS price_per_sqft,
            {sql_coalesce([sql_column("j", jobs_cols, "job_type"), sql_column("b", backlog_cols, "job_type"), "e.job_type"])} AS job_type,
            {sql_coalesce([sql_column("j", jobs_cols, "coating_type"), sql_column("b", backlog_cols, "coating_type"), "e.coating_type"])} AS coating_type,
            {sql_coalesce([sql_column("j", jobs_cols, "foam_type"), sql_column("b", backlog_cols, "foam_type"), "e.foam_type"])} AS foam_type,
            {sql_coalesce([sql_column("j", jobs_cols, "warranty_amount"), sql_column("b", backlog_cols, "warranty_amount"), "e.warranty_amount"])} AS warranty_amount,
            {sql_coalesce([sql_column("j", jobs_cols, "equipment_rental_amount"), sql_column("b", backlog_cols, "equipment_rental_amount"), "e.equipment_rental_amount"])} AS equipment_rental_amount,
            {sql_coalesce([sql_column("j", jobs_cols, "subcontractor_amount"), sql_column("b", backlog_cols, "subcontractor_amount"), "e.subcontractor_amount"])} AS subcontractor_amount,
            {sql_coalesce([sql_column("j", jobs_cols, "material_subtotal"), sql_column("b", backlog_cols, "material_subtotal"), "e.material_subtotal"])} AS material_subtotal,
            {sql_coalesce([sql_column("j", jobs_cols, "labor_subtotal"), sql_column("b", backlog_cols, "labor_subtotal"), "e.labor_subtotal"])} AS labor_subtotal,
            cs.assigned_crew_leader,
            cs.estimated_start_date,
            cs.estimated_end_date,
            cs.schedule_status,
            cs.priority,
            cs.blocking_issue,
            cs.schedule_notes,
            {sql_column("j", jobs_cols, "folder_url")} AS folder_url,
            {sql_column("j", jobs_cols, "folder_path")} AS folder_path,
            {folder_expr} AS folder_link_or_path,
            {estimate_expr} AS estimate_file,
            {proposal_expr} AS proposal_file,
            {sql_coalesce([sql_column("j", jobs_cols, "contract_file"), sql_column("b", backlog_cols, "contract_file")])} AS contract_file,
            {sql_coalesce([sql_column("j", jobs_cols, "job_tracking_file"), sql_column("b", backlog_cols, "job_tracking_file")])} AS job_tracking_file,
            {sql_coalesce([sql_column("j", jobs_cols, "has_proposal"), sql_column("b", backlog_cols, "has_proposal")])} AS has_proposal,
            {sql_coalesce([sql_column("j", jobs_cols, "has_signed_contract"), sql_column("b", backlog_cols, "has_signed_contract")])} AS has_signed_contract,
            {sql_coalesce([sql_column("j", jobs_cols, "has_job_tracking_form"), sql_column("b", backlog_cols, "has_job_tracking_form")])} AS has_job_tracking_form,
            {sql_coalesce([sql_column("j", jobs_cols, "has_aerial"), sql_column("b", backlog_cols, "has_aerial")])} AS has_aerial,
            {sql_coalesce([sql_column("j", jobs_cols, "photo_count"), sql_column("b", backlog_cols, "photo_count")])} AS photo_count,
            {sql_coalesce([sql_column("j", jobs_cols, "warnings"), sql_column("b", backlog_cols, "warnings"), "e.extraction_warnings"])} AS warnings
        FROM crew_schedule cs
        LEFT JOIN dashboard_jobs j ON cs.job_id = j.job_id
        LEFT JOIN dashboard_contracted_backlog b ON cs.job_id = b.job_id
        LEFT JOIN (
            SELECT DISTINCT ON (job_id)
                {estimate_subquery_select}
            FROM dashboard_estimates
            WHERE job_id IS NOT NULL
            ORDER BY job_id, estimated_value DESC NULLS LAST
        ) e ON cs.job_id = e.job_id
        WHERE cs.estimated_start_date IS NOT NULL
    """
    return safe_load(query)


def load_unscheduled_backlog_df(scheduled_job_ids: set[str]) -> pd.DataFrame:
    backlog = query_view("dashboard_contracted_backlog")
    if backlog.empty or "job_id" not in backlog.columns:
        return backlog
    out = backlog[~backlog["job_id"].fillna("").astype(str).isin(scheduled_job_ids)].copy()
    out = with_folder_link(out)
    if "proposal_file" not in out.columns:
        out["proposal_file"] = out["estimate_file"] if "estimate_file" in out.columns else None
    return out


def is_url(value: object) -> bool:
    return text_value(value).lower().startswith(("http://", "https://"))


def render_document_access(label: str, value: object, unavailable_message: str | None = None) -> None:
    value_text = text_value(value)
    if is_url(value_text):
        st.link_button(label, value_text)
    elif value_text:
        st.write(f"**{label}:**")
        st.caption(value_text)
    elif unavailable_message:
        st.caption(unavailable_message)


def format_summary_value(value: object, *, kind: str = "text") -> str:
    if kind in {"money", "number"}:
        number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
        if pd.isna(number):
            return "-"
        if kind == "money":
            return fmt_dollar(number)
        return fmt_count(number)
    return text_value(value) or "-"


def calendar_events_from_schedule(df: pd.DataFrame) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    if df.empty:
        return events
    for row in df.to_dict(orient="records"):
        start = pd.to_datetime(row.get("estimated_start_date"), errors="coerce")
        if pd.isna(start):
            continue
        end = pd.to_datetime(row.get("estimated_end_date"), errors="coerce")
        if pd.isna(end):
            end = start
        event_id = text_value(row.get("schedule_id")) or text_value(row.get("job_id"))
        crew_leader = text_value(row.get("assigned_crew_leader")) or "Unassigned"
        customer = text_value(row.get("customer")) or "Unknown customer"
        job_name = text_value(row.get("job_name")) or text_value(row.get("job_id"))
        color = get_crew_color(crew_leader)
        props = {key: clean_db_value(value) for key, value in row.items()}
        events.append(
            {
                "id": event_id,
                "title": f"{crew_leader} | {customer} - {job_name}",
                "start": start.date().isoformat(),
                "end": (end.date() + timedelta(days=1)).isoformat(),
                "backgroundColor": color,
                "borderColor": color,
                "extendedProps": props,
            }
        )
    return events


def find_calendar_event(calendar_result: object, events: list[dict[str, object]]) -> dict[str, object] | None:
    if not isinstance(calendar_result, dict):
        return None
    event_payload = calendar_result.get("eventClick") or calendar_result.get("event")
    if isinstance(event_payload, dict) and isinstance(event_payload.get("event"), dict):
        event_payload = event_payload["event"]
    event_id = calendar_event_id(event_payload)
    if not event_id:
        return None
    return next((event for event in events if text_value(event.get("id")) == event_id), None)


def calendar_event_id(event_payload: object) -> str:
    if not isinstance(event_payload, dict):
        return ""
    extended_props = event_payload.get("extendedProps") if isinstance(event_payload.get("extendedProps"), dict) else {}
    event_def = event_payload.get("_def") if isinstance(event_payload.get("_def"), dict) else {}
    for value in (
        event_payload.get("id"),
        event_payload.get("publicId"),
        event_def.get("publicId"),
        extended_props.get("schedule_id"),
        extended_props.get("job_id"),
    ):
        event_id = text_value(value)
        if event_id:
            return event_id
    return ""


def calendar_event_date(event_payload: dict[str, object], *keys: str) -> object | None:
    for key in keys:
        value = event_payload.get(key)
        if value:
            return value
    instance = event_payload.get("_instance") if isinstance(event_payload.get("_instance"), dict) else {}
    date_range = instance.get("range") if isinstance(instance.get("range"), dict) else {}
    for key in keys:
        if key.startswith("start"):
            value = date_range.get("start")
        elif key.startswith("end"):
            value = date_range.get("end")
        else:
            value = None
        if value:
            return value
    return None


def parse_calendar_change(calendar_result: object) -> dict[str, object] | None:
    if not isinstance(calendar_result, dict):
        return None
    payload = calendar_result.get("eventDrop") or calendar_result.get("eventChange") or calendar_result.get("eventResize")
    if not isinstance(payload, dict):
        return None
    event_payload = payload.get("event") if isinstance(payload.get("event"), dict) else payload
    event_id = calendar_event_id(event_payload)
    start = calendar_event_date(event_payload, "startStr", "start")
    end = calendar_event_date(event_payload, "endStr", "end")
    if not event_id or not start:
        return None
    return {"event_id": event_id, "start": start, "end": end}


def update_calendar_schedule_dates(event_id: object, start_value: object, exclusive_end_value: object | None) -> None:
    start = pd.to_datetime(start_value, errors="coerce")
    end = pd.to_datetime(exclusive_end_value, errors="coerce") if exclusive_end_value else pd.NaT
    if pd.isna(start):
        return
    if pd.isna(end):
        inclusive_end = start.date()
    else:
        inclusive_end = end.date() - timedelta(days=1)
    duration_days = max((inclusive_end - start.date()).days + 1, 1)

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE crew_schedule
                SET estimated_start_date = :estimated_start_date,
                    estimated_end_date = :estimated_end_date,
                    estimated_duration_days = :estimated_duration_days,
                    updated_at = NOW()
                WHERE schedule_id = :event_id OR job_id = :event_id
                """
            ),
            {
                "event_id": text_value(event_id),
                "estimated_start_date": start.date().isoformat(),
                "estimated_end_date": inclusive_end.isoformat(),
                "estimated_duration_days": duration_days,
            },
        )
    st.cache_data.clear()


def load_dispatch_jobs(dispatch_date: date) -> pd.DataFrame:
    query = text(
        """
        SELECT
            cs.schedule_id,
            cs.job_id,
            j.customer,
            j.job_name,
            j.site_address,
            cs.assigned_crew_leader AS crew_leader,
            cs.estimated_start_date,
            cs.estimated_end_date,
            cs.estimated_duration_days,
            cs.schedule_status,
            cs.priority,
            cs.schedule_notes AS work_scope,
            NULL::TEXT AS start_time,
            NULL::TEXT AS crew_members,
            NULL::TEXT AS equipment_notes,
            NULL::TEXT AS material_notes,
            NULL::TEXT AS work_notes,
            NULL::TEXT AS safety_notes,
            NULL::TEXT AS weather_notes,
            NULL::TEXT AS special_instructions
        FROM crew_schedule cs
        LEFT JOIN dashboard_jobs j ON j.job_id = cs.job_id
        WHERE cs.estimated_start_date IS NOT NULL
          AND cs.estimated_end_date IS NOT NULL
          AND cs.estimated_start_date <= :dispatch_date
          AND cs.estimated_end_date >= :dispatch_date
        ORDER BY cs.assigned_crew_leader NULLS LAST, cs.priority NULLS LAST, j.customer, j.job_name
        """
    )
    result = load_df_uncached(query, params={"dispatch_date": dispatch_date})
    if result.ok:
        return result.value
    show_database_error(result.error or RuntimeError("Database read failed."))
    st.stop()


def generate_dispatch_message(df: pd.DataFrame, dispatch_date: date) -> str:
    if df.empty:
        return f"Daily Crew Dispatch - {dispatch_date:%A, %B %-d, %Y}\n\nNo scheduled jobs."

    lines = [f"Daily Crew Dispatch - {dispatch_date:%A, %B %-d, %Y}"]
    leader_series = df["crew_leader"] if "crew_leader" in df.columns else pd.Series("", index=df.index)
    working_df = df.assign(_crew_leader=leader_series.fillna("").astype(str).str.strip().replace("", "Unassigned"))
    for crew_leader, group in working_df.groupby("_crew_leader", dropna=False):
        lines.append("")
        lines.append(str(crew_leader))
        for _, row in group.iterrows():
            start_time = text_value(row.get("start_time")) or "TBD"
            customer = text_value(row.get("customer")) or "Unknown customer"
            job_name = text_value(row.get("job_name")) or text_value(row.get("job_id"))
            address = text_value(row.get("site_address"))
            scope = text_value(row.get("work_notes")) or text_value(row.get("work_scope"))
            lines.append(f"- {start_time} | {customer} - {job_name}")
            if address:
                lines.append(f"  Site: {address}")
            if scope:
                lines.append(f"  Work: {scope}")
            for label, column in (
                ("Crew", "crew_members"),
                ("Equipment", "equipment_notes"),
                ("Materials", "material_notes"),
                ("Safety", "safety_notes"),
                ("Weather", "weather_notes"),
                ("Special", "special_instructions"),
            ):
                value = text_value(row.get(column))
                if value:
                    lines.append(f"  {label}: {value}")
    return "\n".join(lines)


def save_dispatch_draft(df: pd.DataFrame, message_text: str, dispatch_date: date) -> int:
    ensure_daily_dispatch_table()
    if df.empty:
        return 0

    upsert_sql = text(
        """
        INSERT INTO daily_dispatch (
            dispatch_id,
            dispatch_date,
            job_id,
            customer,
            job_name,
            site_address,
            start_time,
            crew_leader,
            crew_members,
            work_scope,
            equipment_notes,
            material_notes,
            safety_notes,
            weather_notes,
            special_instructions,
            message_text,
            send_method,
            sent_status,
            raw,
            updated_at
        )
        VALUES (
            :dispatch_id,
            :dispatch_date,
            :job_id,
            :customer,
            :job_name,
            :site_address,
            :start_time,
            :crew_leader,
            :crew_members,
            :work_scope,
            :equipment_notes,
            :material_notes,
            :safety_notes,
            :weather_notes,
            :special_instructions,
            :message_text,
            :send_method,
            :sent_status,
            CAST(:raw AS JSONB),
            NOW()
        )
        ON CONFLICT (dispatch_id) DO UPDATE SET
            customer = EXCLUDED.customer,
            job_name = EXCLUDED.job_name,
            site_address = EXCLUDED.site_address,
            start_time = EXCLUDED.start_time,
            crew_leader = EXCLUDED.crew_leader,
            crew_members = EXCLUDED.crew_members,
            work_scope = EXCLUDED.work_scope,
            equipment_notes = EXCLUDED.equipment_notes,
            material_notes = EXCLUDED.material_notes,
            safety_notes = EXCLUDED.safety_notes,
            weather_notes = EXCLUDED.weather_notes,
            special_instructions = EXCLUDED.special_instructions,
            message_text = EXCLUDED.message_text,
            send_method = EXCLUDED.send_method,
            sent_status = EXCLUDED.sent_status,
            raw = EXCLUDED.raw,
            updated_at = NOW()
        """
    )

    records = []
    for row in df.to_dict(orient="records"):
        if not text_value(row.get("job_id")):
            continue
        row["dispatch_date"] = dispatch_date.isoformat()
        record = {
            "dispatch_id": dispatch_id_for(dispatch_date, row.get("job_id")),
            "dispatch_date": dispatch_date.isoformat(),
            "job_id": clean_db_value(row.get("job_id")),
            "customer": clean_db_value(row.get("customer")),
            "job_name": clean_db_value(row.get("job_name")),
            "site_address": clean_db_value(row.get("site_address")),
            "start_time": clean_db_value(row.get("start_time")),
            "crew_leader": clean_db_value(row.get("crew_leader")),
            "crew_members": clean_db_value(row.get("crew_members")),
            "work_scope": clean_db_value(row.get("work_notes")) or clean_db_value(row.get("work_scope")),
            "equipment_notes": clean_db_value(row.get("equipment_notes")),
            "material_notes": clean_db_value(row.get("material_notes")),
            "safety_notes": clean_db_value(row.get("safety_notes")),
            "weather_notes": clean_db_value(row.get("weather_notes")),
            "special_instructions": clean_db_value(row.get("special_instructions")),
            "message_text": message_text,
            "send_method": "draft",
            "sent_status": "draft",
            "raw": json.dumps(row, default=str),
        }
        records.append(record)

    if not records:
        return 0

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(upsert_sql, records)
    st.cache_data.clear()
    return len(records)


def show_database_error(exc: Exception) -> None:
    st.error(
        "Spray-Tec data is temporarily unavailable. "
        "The app attempted to reconnect but could not reach the database. Please retry in a moment."
    )
    if st.button("Retry database connection", key="retry_database_connection"):
        reset_database_connection()
        st.rerun()
    with st.expander("Developer database diagnostics"):
        st.write(database_target_debug_payload())
        render_neon_pooler_warning()
        st.caption(safe_exception_text(exc))


def safe_load(query: str) -> pd.DataFrame:
    result = load_df_uncached(query)
    if result.ok:
        return result.value
    show_database_error(result.error or RuntimeError("Database read failed."))
    st.stop()


def query_view(view_name: str) -> pd.DataFrame:
    if view_name not in VIEWS:
        raise ValueError(f"Unsupported dashboard view: {view_name}")
    return safe_load(f"SELECT * FROM {view_name}")


def sql_identifier(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(f"Unsafe SQL identifier: {name}")
    return name


def health_query(query: str, params: dict[str, Any] | None = None) -> tuple[pd.DataFrame, str | None]:
    result = load_df_uncached(query, params=params)
    if result.ok:
        return result.value, None
    return pd.DataFrame(), safe_exception_text(result.error or RuntimeError("Health query failed."))


@st.cache_data(ttl=120, show_spinner=False)
def health_table_exists(table_name: str) -> bool:
    df, _ = health_query("SELECT to_regclass(:table_name) IS NOT NULL AS table_exists", {"table_name": table_name})
    if df.empty or "table_exists" not in df.columns:
        return False
    return bool(df.iloc[0]["table_exists"])


@st.cache_data(ttl=120, show_spinner=False)
def health_table_columns(table_name: str) -> list[str]:
    df, _ = health_query(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = :table_name
        ORDER BY ordinal_position
        """,
        {"table_name": table_name},
    )
    return df["column_name"].astype(str).tolist() if not df.empty and "column_name" in df.columns else []


@st.cache_data(ttl=120, show_spinner=False)
def load_admin_health_snapshot() -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "connection_ok": False,
        "connection_error": None,
        "row_counts": pd.DataFrame(),
        "extraction_status_counts": pd.DataFrame(),
        "template_type_counts": pd.DataFrame(),
        "recent_problem_documents": pd.DataFrame(),
        "timestamp_rows": pd.DataFrame(),
        "warnings": [],
        "query_errors": [],
    }

    ping_df, ping_error = health_query("SELECT 1 AS ok")
    snapshot["connection_ok"] = bool(not ping_df.empty and int(ping_df.iloc[0]["ok"]) == 1)
    snapshot["connection_error"] = ping_error
    if ping_error:
        snapshot["query_errors"].append({"area": "database_connection", "error": ping_error})
        return snapshot

    row_count_rows: list[dict[str, Any]] = []
    timestamp_rows: list[dict[str, Any]] = []
    for table_name, label in HEALTH_TABLES:
        exists = health_table_exists(table_name)
        columns = health_table_columns(table_name) if exists else []
        row: dict[str, Any] = {
            "table": table_name,
            "label": label,
            "exists": exists,
            "row_count": None,
        }
        if exists:
            identifier = sql_identifier(table_name)
            count_df, count_error = health_query(f"SELECT COUNT(*)::BIGINT AS row_count FROM {identifier}")
            if count_error:
                snapshot["query_errors"].append({"area": f"{table_name}.row_count", "error": count_error})
            elif not count_df.empty:
                row["row_count"] = int(count_df.iloc[0]["row_count"] or 0)

            if table_name == "pricing_catalog" and "is_current" in columns:
                where = "WHERE COALESCE(is_current, false)"
                if "status" in columns:
                    where += " AND COALESCE(status, '') = 'active'"
                current_df, current_error = health_query(f"SELECT COUNT(*)::BIGINT AS current_rows FROM {identifier} {where}")
                if current_error:
                    snapshot["query_errors"].append({"area": "pricing_catalog.current_rows", "error": current_error})
                elif not current_df.empty:
                    row["current_rows"] = int(current_df.iloc[0]["current_rows"] or 0)

            for timestamp_column in [
                "last_scanned_at",
                "last_parsed_at",
                "parsed_at",
                "extracted_at",
                "checkpoint_updated_at",
                "updated_at",
                "created_at",
                "modified_at",
            ]:
                if timestamp_column not in columns:
                    continue
                ts_df, ts_error = health_query(f"SELECT MAX({sql_identifier(timestamp_column)}) AS latest_value FROM {identifier}")
                if ts_error:
                    snapshot["query_errors"].append({"area": f"{table_name}.{timestamp_column}", "error": ts_error})
                    continue
                if not ts_df.empty:
                    timestamp_rows.append(
                        {
                            "table": table_name,
                            "timestamp_column": timestamp_column,
                            "latest_value": ts_df.iloc[0]["latest_value"],
                        }
                    )
        row_count_rows.append(row)

    row_counts = pd.DataFrame(row_count_rows)
    snapshot["row_counts"] = row_counts
    snapshot["timestamp_rows"] = pd.DataFrame(timestamp_rows)

    if health_table_exists("documents") and "extraction_status" in health_table_columns("documents"):
        extraction_df, extraction_error = health_query(
            """
            SELECT COALESCE(NULLIF(extraction_status, ''), 'unknown') AS extraction_status,
                   COUNT(*)::BIGINT AS row_count
            FROM documents
            GROUP BY COALESCE(NULLIF(extraction_status, ''), 'unknown')
            ORDER BY row_count DESC, extraction_status
            """
        )
        if extraction_error:
            snapshot["query_errors"].append({"area": "documents.extraction_status_counts", "error": extraction_error})
        else:
            snapshot["extraction_status_counts"] = extraction_df

        doc_columns = health_table_columns("documents")
        display_columns = [
            column
            for column in [
                "document_id",
                "job_id",
                "file_name",
                "document_type",
                "file_extension",
                "extraction_status",
                "extraction_error",
                "extracted_at",
                "updated_at",
            ]
            if column in doc_columns
        ]
        if display_columns:
            order_column = "updated_at" if "updated_at" in doc_columns else "extracted_at" if "extracted_at" in doc_columns else "document_id"
            recent_df, recent_error = health_query(
                f"""
                SELECT {", ".join(sql_identifier(column) for column in display_columns)}
                FROM documents
                WHERE extraction_status IS NULL
                   OR LOWER(COALESCE(extraction_status, '')) IN ('', 'failed', 'error', 'pending', 'not_started', 'not started', 'queued')
                ORDER BY {sql_identifier(order_column)} DESC NULLS LAST
                LIMIT 25
                """
            )
            if recent_error:
                snapshot["query_errors"].append({"area": "documents.recent_problem_documents", "error": recent_error})
            else:
                snapshot["recent_problem_documents"] = recent_df

    if health_table_exists("estimate_template_rows") and "template_type" in health_table_columns("estimate_template_rows"):
        template_df, template_error = health_query(
            """
            SELECT COALESCE(NULLIF(template_type, ''), 'null') AS template_type,
                   COUNT(*)::BIGINT AS row_count
            FROM estimate_template_rows
            GROUP BY COALESCE(NULLIF(template_type, ''), 'null')
            ORDER BY row_count DESC, template_type
            """
        )
        if template_error:
            snapshot["query_errors"].append({"area": "estimate_template_rows.template_type_counts", "error": template_error})
        else:
            snapshot["template_type_counts"] = template_df

    row_counts_by_table = {str(row.get("table")): row for row in row_count_rows}
    pricing = row_counts_by_table.get("pricing_catalog", {})
    if pricing.get("exists") and int(pricing.get("current_rows") or 0) == 0:
        snapshot["warnings"].append("pricing_catalog current active rows = 0")
    templates = row_counts_by_table.get("estimate_template_rows", {})
    if templates.get("exists") and int(templates.get("row_count") or 0) == 0:
        snapshot["warnings"].append("estimate_template_rows has 0 rows")
    labor_rates = row_counts_by_table.get("relationship_labor_rates", {})
    if labor_rates.get("exists") and int(labor_rates.get("row_count") or 0) == 0:
        snapshot["warnings"].append("relationship_labor_rates has 0 rows")

    extraction_counts = snapshot["extraction_status_counts"]
    if isinstance(extraction_counts, pd.DataFrame) and not extraction_counts.empty:
        pending_mask = extraction_counts["extraction_status"].astype(str).str.lower().isin({"", "unknown", "pending", "not_started", "not started", "queued"})
        pending_count = int(pd.to_numeric(extraction_counts.loc[pending_mask, "row_count"], errors="coerce").fillna(0).sum())
        total_docs = int(pd.to_numeric(extraction_counts["row_count"], errors="coerce").fillna(0).sum())
        if pending_count >= 100 or (total_docs and pending_count / total_docs >= 0.25):
            snapshot["warnings"].append(f"documents pending extraction is high: {pending_count:,} of {total_docs:,}")

    template_counts = snapshot["template_type_counts"]
    if isinstance(template_counts, pd.DataFrame) and not template_counts.empty:
        total_templates = int(pd.to_numeric(template_counts["row_count"], errors="coerce").fillna(0).sum())
        null_templates = int(pd.to_numeric(template_counts.loc[template_counts["template_type"].astype(str).str.lower().eq("null"), "row_count"], errors="coerce").fillna(0).sum())
        if total_templates and null_templates / total_templates >= 0.5:
            snapshot["warnings"].append(f"template_type is mostly null: {null_templates:,} of {total_templates:,}")

    return snapshot


@st.cache_data(ttl=300, show_spinner=False)
def load_pricing_health() -> pd.DataFrame:
    result = load_df_uncached(
        """
        SELECT
            COUNT(*) AS total_rows,
            COUNT(*) FILTER (WHERE COALESCE(is_current, false) AND COALESCE(status, '') = 'active') AS current_active_rows,
            COUNT(*) FILTER (WHERE COALESCE(needs_review, false)) AS rows_needing_review,
            COUNT(DISTINCT NULLIF(vendor, '')) AS distinct_vendors,
            MAX(effective_date) AS latest_effective_date,
            COUNT(*) FILTER (WHERE unit_price IS NULL) AS missing_price_count
        FROM pricing_catalog
        """
    )
    if result.ok:
        return result.value
    raise result.error or RuntimeError("Pricing health query failed.")


@st.cache_data(ttl=300, show_spinner=False)
def load_pricing_catalog_filtered(
    search: str,
    vendors: tuple[str, ...],
    categories: tuple[str, ...],
    statuses: tuple[str, ...],
    source_files: tuple[str, ...],
    source_types: tuple[str, ...],
    is_current_filter: str,
    needs_review_filter: str,
    effective_start: str | None,
    effective_end: str | None,
    limit: int,
) -> pd.DataFrame:
    clauses = ["1 = 1"]
    params: dict[str, Any] = {"limit": int(limit)}
    if search:
        clauses.append(
            """
            (
                product_name ILIKE :search
                OR description ILIKE :search
                OR vendor ILIKE :search
                OR category ILIKE :search
                OR vendor_item_no ILIKE :search
                OR notes ILIKE :search
            )
            """
        )
        params["search"] = f"%{search}%"
    if vendors:
        clauses.append("vendor = ANY(:vendors)")
        params["vendors"] = list(vendors)
    if categories:
        clauses.append("category = ANY(:categories)")
        params["categories"] = list(categories)
    if statuses:
        clauses.append("status = ANY(:statuses)")
        params["statuses"] = list(statuses)
    if source_files:
        clauses.append("source_file = ANY(:source_files)")
        params["source_files"] = list(source_files)
    if source_types:
        clauses.append("source_type = ANY(:source_types)")
        params["source_types"] = list(source_types)
    if is_current_filter == "Current only":
        clauses.append("COALESCE(is_current, false) IS TRUE")
    elif is_current_filter == "Not current":
        clauses.append("COALESCE(is_current, false) IS FALSE")
    if needs_review_filter == "Needs review":
        clauses.append("COALESCE(needs_review, false) IS TRUE")
    elif needs_review_filter == "Reviewed / OK":
        clauses.append("COALESCE(needs_review, false) IS FALSE")
    if effective_start:
        clauses.append("effective_date >= :effective_start")
        params["effective_start"] = effective_start
    if effective_end:
        clauses.append("effective_date <= :effective_end")
        params["effective_end"] = effective_end

    query = f"""
        SELECT
            pricing_item_id,
            vendor,
            category,
            product_name,
            description,
            unit_price,
            unit_of_measure,
            package_size,
            price_basis,
            price_per_gallon,
            price_per_sqft,
            price_per_unit,
            effective_date,
            status,
            is_current,
            needs_review,
            source_file,
            source_type,
            notes,
            vendor_item_no
        FROM pricing_catalog
        WHERE {" AND ".join(clauses)}
        ORDER BY product_name
        LIMIT :limit
    """
    result = load_df_uncached(query, params=params)
    if result.ok:
        return result.value
    raise result.error or RuntimeError("Pricing catalog query failed.")


@st.cache_data(ttl=300, show_spinner=False)
def load_current_pricing_catalog_export() -> pd.DataFrame:
    result = load_df_uncached(
        """
        SELECT
            pricing_item_id,
            vendor,
            category,
            product_name,
            description,
            unit_price,
            unit_of_measure,
            package_size,
            price_basis,
            price_per_gallon,
            price_per_sqft,
            price_per_unit,
            effective_date,
            status,
            is_current,
            needs_review,
            source_file,
            source_type,
            notes
        FROM pricing_catalog
        WHERE COALESCE(is_current, false) IS TRUE
        ORDER BY vendor NULLS LAST, category NULLS LAST, product_name
        """
    )
    if result.ok:
        return result.value
    raise result.error or RuntimeError("Current pricing catalog export query failed.")


@st.cache_data(ttl=300, show_spinner=False)
def load_pricing_filter_options() -> dict[str, list[str]]:
    result = load_df_uncached(
        """
        SELECT 'vendor' AS field, vendor AS value FROM pricing_catalog WHERE NULLIF(vendor, '') IS NOT NULL
        UNION
        SELECT 'category' AS field, category AS value FROM pricing_catalog WHERE NULLIF(category, '') IS NOT NULL
        UNION
        SELECT 'status' AS field, status AS value FROM pricing_catalog WHERE NULLIF(status, '') IS NOT NULL
        UNION
        SELECT 'source_file' AS field, source_file AS value FROM pricing_catalog WHERE NULLIF(source_file, '') IS NOT NULL
        UNION
        SELECT 'source_type' AS field, source_type AS value FROM pricing_catalog WHERE NULLIF(source_type, '') IS NOT NULL
        """
    )
    if not result.ok:
        raise result.error or RuntimeError("Pricing filter option query failed.")
    df = result.value
    if df.empty:
        return {"vendor": [], "category": [], "status": []}
    options: dict[str, list[str]] = {}
    for field, group in df.groupby("field"):
        options[str(field)] = sorted(group["value"].dropna().astype(str).str.strip().unique().tolist())
    return {
        "vendor": options.get("vendor", []),
        "category": options.get("category", []),
        "status": options.get("status", []),
        "source_file": options.get("source_file", []),
        "source_type": options.get("source_type", []),
    }


def pricing_export_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    return df[[column for column in PRICING_EXPORT_COLUMNS if column in df.columns]].copy()


PRICING_EDITABLE_COLUMNS = [
    "vendor",
    "category",
    "product_name",
    "description",
    "unit_price",
    "unit_of_measure",
    "package_size",
    "price_basis",
    "price_per_gallon",
    "price_per_sqft",
    "price_per_unit",
    "effective_date",
    "status",
    "is_current",
    "needs_review",
    "vendor_item_no",
    "notes",
]


def pricing_product_name_normalized(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())
    return " ".join(text.split())


def _db_blank_to_none(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


def _db_float_or_none(value: Any) -> float | None:
    value = _db_blank_to_none(value)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _db_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "checked"}


def _db_date_or_none(value: Any) -> str | None:
    value = _db_blank_to_none(value)
    if value is None:
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date().isoformat()


def save_pricing_catalog_edits(original: pd.DataFrame, edited: pd.DataFrame) -> int:
    if original.empty or edited.empty or "pricing_item_id" not in edited.columns:
        return 0
    original_by_id = original.set_index("pricing_item_id", drop=False)
    updates: list[dict[str, Any]] = []
    for _, row in edited.iterrows():
        pricing_item_id = text_value(row.get("pricing_item_id"))
        if not pricing_item_id or pricing_item_id not in original_by_id.index:
            continue
        original_row = original_by_id.loc[pricing_item_id]
        changed = False
        for column in PRICING_EDITABLE_COLUMNS:
            if column not in edited.columns:
                continue
            old = _db_blank_to_none(original_row.get(column))
            new = _db_blank_to_none(row.get(column))
            if column in {"unit_price", "price_per_gallon", "price_per_sqft", "price_per_unit"}:
                old = _db_float_or_none(old)
                new = _db_float_or_none(new)
            elif column == "effective_date":
                old = _db_date_or_none(old)
                new = _db_date_or_none(new)
            elif column in {"is_current", "needs_review"}:
                old = _db_bool(old)
                new = _db_bool(new)
            if old != new:
                changed = True
                break
        if not changed:
            continue
        updates.append(
            {
                "pricing_item_id": pricing_item_id,
                "vendor": _db_blank_to_none(row.get("vendor")),
                "category": _db_blank_to_none(row.get("category")),
                "product_name": _db_blank_to_none(row.get("product_name")) or pricing_item_id,
                "product_name_normalized": pricing_product_name_normalized(row.get("product_name")),
                "description": _db_blank_to_none(row.get("description")),
                "unit_price": _db_float_or_none(row.get("unit_price")),
                "unit_of_measure": _db_blank_to_none(row.get("unit_of_measure")),
                "package_size": _db_blank_to_none(row.get("package_size")),
                "price_basis": _db_blank_to_none(row.get("price_basis")),
                "price_per_gallon": _db_float_or_none(row.get("price_per_gallon")),
                "price_per_sqft": _db_float_or_none(row.get("price_per_sqft")),
                "price_per_unit": _db_float_or_none(row.get("price_per_unit")),
                "effective_date": _db_date_or_none(row.get("effective_date")),
                "status": _db_blank_to_none(row.get("status")) or "active",
                "is_current": _db_bool(row.get("is_current")),
                "needs_review": _db_bool(row.get("needs_review")),
                "vendor_item_no": _db_blank_to_none(row.get("vendor_item_no")),
                "notes": _db_blank_to_none(row.get("notes")),
            }
        )
    if not updates:
        return 0
    statement = text(
        """
        UPDATE pricing_catalog
        SET
            vendor = :vendor,
            category = :category,
            product_name = :product_name,
            product_name_normalized = :product_name_normalized,
            description = :description,
            unit_price = :unit_price,
            unit_of_measure = :unit_of_measure,
            package_size = :package_size,
            price_basis = :price_basis,
            price_per_gallon = :price_per_gallon,
            price_per_sqft = :price_per_sqft,
            price_per_unit = :price_per_unit,
            effective_date = :effective_date,
            status = :status,
            is_current = :is_current,
            needs_review = :needs_review,
            vendor_item_no = :vendor_item_no,
            notes = :notes,
            updated_at = now()
        WHERE pricing_item_id = :pricing_item_id
        """
    )
    with get_engine().begin() as connection:
        connection.execute(statement, updates)
    load_pricing_health.clear()
    load_pricing_catalog_filtered.clear()
    load_current_pricing_catalog_export.clear()
    load_pricing_filter_options.clear()
    clear_estimator_data_caches()
    return len(updates)


def create_pricing_catalog_row(row: dict[str, Any]) -> str:
    from jobscan.pricing_loader import stable_pricing_item_id

    product_name = _db_blank_to_none(row.get("product_name"))
    if not product_name:
        raise ValueError("Product name is required.")
    prepared = {
        "vendor": _db_blank_to_none(row.get("vendor")),
        "category": _db_blank_to_none(row.get("category")),
        "product_name": product_name,
        "product_name_normalized": pricing_product_name_normalized(product_name),
        "description": _db_blank_to_none(row.get("description")),
        "unit_price": _db_float_or_none(row.get("unit_price")),
        "unit_of_measure": _db_blank_to_none(row.get("unit_of_measure")),
        "package_size": _db_blank_to_none(row.get("package_size")),
        "price_basis": _db_blank_to_none(row.get("price_basis")),
        "price_per_gallon": _db_float_or_none(row.get("price_per_gallon")),
        "price_per_sqft": _db_float_or_none(row.get("price_per_sqft")),
        "price_per_unit": _db_float_or_none(row.get("price_per_unit") or row.get("unit_price")),
        "vendor_item_no": _db_blank_to_none(row.get("vendor_item_no")),
        "source_file": _db_blank_to_none(row.get("source_file")) or "dashboard_manual_entry",
        "source_type": "manual",
        "source_sheet": None,
        "source_page": None,
        "effective_date": _db_date_or_none(row.get("effective_date")) or date.today().isoformat(),
        "expiration_date": None,
        "is_current": _db_bool(row.get("is_current", True)),
        "status": _db_blank_to_none(row.get("status")) or "active",
        "needs_review": _db_bool(row.get("needs_review", False)),
        "review_notes": _db_blank_to_none(row.get("review_notes")),
        "notes": _db_blank_to_none(row.get("notes")),
    }
    if prepared["unit_price"] is None:
        prepared["needs_review"] = True
        if not prepared["status"] or prepared["status"] == "active":
            prepared["status"] = "review"
    prepared["pricing_item_id"] = stable_pricing_item_id(prepared)
    prepared["raw_row_json"] = json.dumps(
        {
            "source": "dashboard_manual_entry",
            "created_from": {key: value for key, value in prepared.items() if key != "raw_row_json"},
        },
        default=str,
        sort_keys=True,
    )
    statement = text(
        """
        INSERT INTO pricing_catalog (
            pricing_item_id, vendor, category, product_name, product_name_normalized, description,
            unit_price, unit_of_measure, package_size, price_basis, price_per_gallon, price_per_sqft,
            price_per_unit, vendor_item_no, source_file, source_type, source_sheet, source_page,
            effective_date, expiration_date, is_current, status, needs_review, review_notes, notes,
            raw_row_json, created_at, updated_at
        )
        VALUES (
            :pricing_item_id, :vendor, :category, :product_name, :product_name_normalized, :description,
            :unit_price, :unit_of_measure, :package_size, :price_basis, :price_per_gallon, :price_per_sqft,
            :price_per_unit, :vendor_item_no, :source_file, :source_type, :source_sheet, :source_page,
            :effective_date, :expiration_date, :is_current, :status, :needs_review, :review_notes, :notes,
            CAST(:raw_row_json AS JSONB), now(), now()
        )
        ON CONFLICT (pricing_item_id) DO UPDATE SET
            vendor = EXCLUDED.vendor,
            category = EXCLUDED.category,
            product_name = EXCLUDED.product_name,
            product_name_normalized = EXCLUDED.product_name_normalized,
            description = EXCLUDED.description,
            unit_price = EXCLUDED.unit_price,
            unit_of_measure = EXCLUDED.unit_of_measure,
            package_size = EXCLUDED.package_size,
            price_basis = EXCLUDED.price_basis,
            price_per_gallon = EXCLUDED.price_per_gallon,
            price_per_sqft = EXCLUDED.price_per_sqft,
            price_per_unit = EXCLUDED.price_per_unit,
            vendor_item_no = EXCLUDED.vendor_item_no,
            source_file = EXCLUDED.source_file,
            source_type = EXCLUDED.source_type,
            source_sheet = EXCLUDED.source_sheet,
            source_page = EXCLUDED.source_page,
            effective_date = EXCLUDED.effective_date,
            expiration_date = EXCLUDED.expiration_date,
            is_current = EXCLUDED.is_current,
            status = EXCLUDED.status,
            needs_review = EXCLUDED.needs_review,
            review_notes = EXCLUDED.review_notes,
            notes = EXCLUDED.notes,
            raw_row_json = EXCLUDED.raw_row_json,
            updated_at = now()
        """
    )
    with get_engine().begin() as connection:
        connection.execute(statement, prepared)
    load_pricing_health.clear()
    load_pricing_catalog_filtered.clear()
    load_current_pricing_catalog_export.clear()
    load_pricing_filter_options.clear()
    clear_estimator_data_caches()
    return prepared["pricing_item_id"]


@st.cache_data(ttl=300, show_spinner=False)
def load_product_catalog_options() -> pd.DataFrame:
    result = load_df_uncached(
        """
        SELECT product_id, manufacturer, product_name, product_family, category, active
        FROM product_catalog
        ORDER BY COALESCE(manufacturer, ''), product_name
        LIMIT 5000
        """
    )
    if result.ok:
        return result.value
    return pd.DataFrame()


def product_catalog_option_label(row: dict[str, Any]) -> str:
    manufacturer = text_value(row.get("manufacturer"))
    name = text_value(row.get("product_name")) or text_value(row.get("product_id"))
    category = text_value(row.get("category"))
    prefix = f"{manufacturer} - " if manufacturer else ""
    suffix = f" ({category})" if category else ""
    return f"{prefix}{name}{suffix}"


def stage_product_document_upload(uploaded_file: Any) -> Path:
    raw = bytes(uploaded_file.getbuffer())
    digest = hashlib.sha256(raw).hexdigest()[:16]
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", str(uploaded_file.name or "product_document"))
    out_dir = Path("output/product_uploads")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{digest}_{safe_name}"
    path.write_bytes(raw)
    return path


def retarget_product_knowledge_to_catalog_product(knowledge: Any, product_row: dict[str, Any]) -> Any:
    product_id = text_value(product_row.get("product_id"))
    if not product_id:
        return knowledge
    parsed_products = list(getattr(knowledge, "product_catalog", []) or [])
    parsed_aliases = []
    for product in parsed_products:
        if text_value(product.get("product_name")):
            parsed_aliases.append(text_value(product.get("product_name")))
        parsed_aliases.extend(str(alias) for alias in (product.get("aliases") or []) if str(alias).strip())
    catalog_product = {
        "product_id": product_id,
        "manufacturer": _db_blank_to_none(product_row.get("manufacturer")),
        "product_family": _db_blank_to_none(product_row.get("product_family")),
        "product_name": _db_blank_to_none(product_row.get("product_name")) or product_id,
        "sku": "",
        "category": _db_blank_to_none(product_row.get("category")),
        "subcategory": "",
        "unit": "",
        "aliases": sorted(set(alias for alias in parsed_aliases if alias)),
        "active": _db_bool(product_row.get("active")) if "active" in product_row else True,
        "extraction_method": "dashboard_upload_linked",
        "extraction_warnings": ["Uploaded document was linked to an existing product catalog item by the user."],
    }
    knowledge.product_catalog = [catalog_product]
    for collection_name in ("product_documents", "product_properties", "product_rules", "product_decision_links"):
        for row in getattr(knowledge, collection_name, []) or []:
            row["product_id"] = product_id
    for alias in getattr(knowledge, "product_aliases", []) or []:
        alias["product_id"] = product_id
        alias_text = text_value(alias.get("alias"))
        alias["alias_id"] = re.sub(r"[^a-z0-9]+", "_", f"{product_id}_{alias_text}".lower()).strip("_")
    return knowledge


def ingest_uploaded_product_document(
    uploaded_file: Any,
    *,
    selected_product: dict[str, Any] | None = None,
    use_ai: bool = False,
    manufacturer_hint: str = "",
) -> dict[str, Any]:
    from jobscan.products.catalog_db import apply_product_knowledge_schema, upsert_product_knowledge
    from jobscan.products.product_catalog import write_product_catalog_json
    from jobscan.products.product_ingest import ingest_product_document

    staged_path = stage_product_document_upload(uploaded_file)
    knowledge = ingest_product_document(
        staged_path,
        use_ai=use_ai,
        manufacturer_hint=manufacturer_hint or None,
    )
    if selected_product:
        knowledge = retarget_product_knowledge_to_catalog_product(knowledge, selected_product)
    json_path = staged_path.with_suffix(staged_path.suffix + ".product_knowledge.json")
    write_product_catalog_json(knowledge, json_path)
    apply_product_knowledge_schema(DATABASE_URL)
    counts = upsert_product_knowledge(DATABASE_URL, knowledge, catalog_path=json_path, update_queue=False)
    clear_estimator_data_caches()
    load_product_catalog_options.clear()
    return {"file_path": str(staged_path), "json_path": str(json_path), "counts": counts, "knowledge": knowledge.to_dict()}


def numeric_series(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(dtype="float64")
    return pd.to_numeric(df[column], errors="coerce")


def bool_series(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(False, index=df.index)
    return df[column].fillna(False).astype(bool)


def fmt_count(value: int | float | None) -> str:
    return f"{0 if value is None or pd.isna(value) else value:,.0f}"


def fmt_dollar(value: int | float | None) -> str:
    return f"${0 if value is None or pd.isna(value) else value:,.0f}"


def money_metric(value: int | float | None) -> str:
    return fmt_dollar(value)


def number_metric(value: int | float | None) -> str:
    return fmt_count(value)


def optional_positive_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def safe_sum(df: pd.DataFrame, column: str) -> float:
    return float(numeric_series(df, column).sum()) if column in df.columns else 0.0


def safe_count_true(df: pd.DataFrame, column: str) -> int:
    return int(bool_series(df, column).sum()) if column in df.columns else 0


def metric_row(metrics: list[tuple[str, str]]) -> None:
    columns = st.columns(len(metrics))
    for column, (label, value) in zip(columns, metrics):
        column.metric(label, value)


def options_from(df: pd.DataFrame, column: str) -> list[str]:
    if column not in df.columns or df.empty:
        return []
    values = df[column].dropna().astype(str).str.strip()
    return sorted(value for value in values.unique() if value)


@st.cache_data(ttl=300, show_spinner=False)
def load_sidebar_filter_jobs() -> pd.DataFrame:
    cols = relation_columns("dashboard_jobs")
    if not cols:
        return pd.DataFrame(columns=["division", "pipeline_status", "status", "customer"])
    fields = {
        "division": "division",
        "pipeline_status": "pipeline_status",
        "status": "status",
        "customer": "customer",
    }
    select_parts = [f"{sql_column('j', cols, source)} AS {alias}" for source, alias in fields.items()]
    return load_df(f"SELECT DISTINCT {', '.join(select_parts)} FROM dashboard_jobs j")


def sidebar_filters(jobs: pd.DataFrame) -> dict[str, object]:
    global selected_divisions, selected_pipeline_statuses, selected_statuses, customer_search

    st.sidebar.title("Spray-Tec Ops")
    st.sidebar.caption("Filters")
    selected_divisions = st.sidebar.multiselect("Division", options_from(jobs, "division"))
    selected_pipeline_statuses = st.sidebar.multiselect("Pipeline Status", options_from(jobs, "pipeline_status"))
    selected_statuses = st.sidebar.multiselect("Status", options_from(jobs, "status"))
    customer_search = st.sidebar.text_input("Customer Search", value="").strip()
    return {
        "division": selected_divisions,
        "pipeline_status": selected_pipeline_statuses,
        "status": selected_statuses,
        "customer": customer_search,
    }


def apply_filters(
    df: pd.DataFrame,
    filters: dict[str, object],
    *,
    include_status: bool = True,
    include_customer: bool = True,
) -> pd.DataFrame:
    filtered = df.copy()
    for column in ("division", "pipeline_status"):
        selected = filters.get(column) or []
        if selected and column in filtered.columns:
            filtered = filtered[filtered[column].astype(str).isin(selected)]

    selected_status = filters.get("status") or []
    if include_status and selected_status and "status" in filtered.columns:
        filtered = filtered[filtered["status"].astype(str).isin(selected_status)]

    customer_search = str(filters.get("customer") or "")
    if include_customer and customer_search and "customer" in filtered.columns:
        filtered = filtered[
            filtered["customer"].fillna("").astype(str).str.contains(customer_search, case=False, na=False)
        ]
    return filtered


def apply_basic_filters(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame):
        return pd.DataFrame()
    filtered = df.copy()
    if selected_divisions and "division" in filtered.columns:
        filtered = filtered[filtered["division"].astype(str).isin(selected_divisions)]
    if selected_pipeline_statuses and "pipeline_status" in filtered.columns:
        filtered = filtered[filtered["pipeline_status"].astype(str).isin(selected_pipeline_statuses)]
    if selected_statuses and "status" in filtered.columns:
        filtered = filtered[filtered["status"].astype(str).isin(selected_statuses)]
    if customer_search and "customer" in filtered.columns:
        filtered = filtered[
            filtered["customer"].fillna("").astype(str).str.contains(customer_search, case=False, na=False)
        ]
    return filtered


def with_folder_link(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "folder_link_or_path" not in out.columns:
        if "folder_url" in out.columns:
            out["folder_link_or_path"] = out["folder_url"].where(
                out["folder_url"].fillna("").astype(str).str.strip() != "",
                out["folder_path"] if "folder_path" in out.columns else "",
            )
        elif "folder_path" in out.columns:
            out["folder_link_or_path"] = out["folder_path"]
        else:
            out["folder_link_or_path"] = ""
    return out


def show_empty(message: str = "No rows match the current filters.") -> None:
    st.info(message)


def bar_chart(
    df: pd.DataFrame,
    x: str,
    y: str | None,
    title: str,
    *,
    color: str | None = None,
    labels: dict[str, str] | None = None,
    top_n: int | None = None,
) -> None:
    if df.empty or x not in df.columns or (y is not None and y not in df.columns):
        show_empty(f"No data available for {title}.")
        return
    if y is None:
        group_cols = [x] + ([color] if color and color in df.columns else [])
        chart_df = df.groupby(group_cols, dropna=False).size().reset_index(name="count")
        y = "count"
    else:
        group_cols = [x] + ([color] if color and color in df.columns else [])
        chart_df = df.groupby(group_cols, dropna=False, as_index=False)[y].sum()
    chart_df[x] = chart_df[x].fillna("Unknown").astype(str)
    if color and color in chart_df.columns:
        chart_df[color] = chart_df[color].fillna("Unknown").astype(str)
    chart_df = chart_df.sort_values(y, ascending=False)
    if top_n is not None and top_n > 0 and not color:
        chart_df = chart_df.head(top_n)
    fig = px.bar(chart_df, x=x, y=y, color=color if color in chart_df.columns else None, title=title, labels=labels)
    st.plotly_chart(fig, width="stretch")


def show_table(
    df: pd.DataFrame,
    columns: Iterable[str] | None = None,
    height: int = 450,
    *,
    sort_by: str | None = None,
    n: int | None = None,
    row_style_column: str | None = None,
    row_style_colors: dict[str, str] | None = None,
    column_labels: dict[str, str] | None = None,
    default_visible_columns: Iterable[str] | None = None,
) -> None:
    table_df = with_folder_link(df)
    requested_columns = unique_columns(columns if columns is not None else table_df.columns)
    available = [column for column in requested_columns if column in table_df.columns]
    if not available:
        show_empty("No requested columns are available.")
        return
    if sort_by and sort_by in table_df.columns:
        table_df = table_df.sort_values(sort_by, ascending=False, na_position="last")
    if n is not None:
        table_df = table_df.head(n)
    if table_df.empty:
        show_empty()
        return
    display_df = table_df[available].copy()
    column_order = None
    if default_visible_columns is not None:
        visible_requested = unique_columns(default_visible_columns)
        column_order = [column for column in visible_requested if column in display_df.columns]
    column_config = None
    if column_labels:
        column_config = {
            column: st.column_config.Column(label)
            for column, label in column_labels.items()
            if column in display_df.columns
        }
    if row_style_column and row_style_column in table_df.columns and row_style_colors:
        style_values = table_df.loc[display_df.index, row_style_column].fillna("").astype(str)

        def row_style(row: pd.Series) -> list[str]:
            color = row_style_colors.get(style_values.get(row.name, ""), "")
            return [f"background-color: {color}; color: #111827" if color else "" for _ in row]

        st.dataframe(
            display_df.style.apply(row_style, axis=1),
            width="stretch",
            hide_index=True,
            height=height,
            column_order=column_order,
            column_config=column_config,
        )
        return
    st.dataframe(
        display_df,
        width="stretch",
        hide_index=True,
        height=height,
        column_order=column_order,
        column_config=column_config,
    )


def status_value(df: pd.DataFrame, status_text: str) -> float:
    if "pipeline_status" not in df.columns:
        return 0.0
    mask = df["pipeline_status"].fillna("").astype(str).str.contains(status_text, case=False, na=False)
    return safe_sum(df[mask], "estimated_value")


def markdown_link(label: str, url: str) -> str:
    safe_label = str(label).replace("[", "\\[").replace("]", "\\]")
    safe_url = str(url).replace(")", "%29")
    return f"[{safe_label}]({safe_url})"


def job_result_markdown(job: dict[str, Any], interpreted: dict[str, Any], *, include_documents: bool = True, connection: Any = None) -> str:
    title = text_value(job.get("job_name")) or text_value(job.get("customer")) or text_value(job.get("job_id")) or "Untitled job"
    location = ", ".join(part for part in [text_value(job.get("city")), text_value(job.get("state"))] if part)
    meta = " · ".join(
        part
        for part in [
            text_value(job.get("division")),
            text_value(job.get("pipeline_status")) or text_value(job.get("status")),
            location,
        ]
        if part
    )
    lines = [f"**{title}**"]
    if text_value(job.get("customer")) and text_value(job.get("customer")) != title:
        lines.append(text_value(job.get("customer")))
    if meta:
        lines.append(meta)
    lines.append(f"Match: {text_value(job.get('match_reason')) or 'Matched job data'}")
    if include_documents:
        docs = job.get("_documents") if isinstance(job.get("_documents"), list) else None
        if docs is None:
            docs = get_preferred_job_documents(connection, job, interpreted.get("document_type")) if connection is not None else []
        requested_type = interpreted.get("document_type")
        if requested_type not in (None, "all") and not any(doc.get("type") == requested_type for doc in docs):
            lines.append(f"{requested_document_label(requested_type)}: not indexed")
        if docs:
            lines.append("Documents:")
            for doc in docs:
                file_name = text_value(doc.get("file_name"))
                label = file_name or f"Open {doc['label'].lower()}"
                lines.append(f"- {doc['label']}: {markdown_link(label, doc['url'])}")
        elif requested_type:
            lines.append(f"No stored {str(interpreted['document_type']).replace('_', ' ')} link found for this job.")
    return "\n".join(lines)


def is_document_lookup_request(interpreted: dict[str, Any]) -> bool:
    return interpreted.get("document_type") not in (None, "")


def is_data_answer_request(prompt: str) -> bool:
    normalized = " " + " ".join(str(prompt or "").lower().split()) + " "
    question_markers = (
        " what ",
        " when ",
        " where ",
        " which ",
        " who ",
        " how ",
        " why ",
        " tell me ",
        " summarize ",
        " summary ",
        " do we ",
        " did we ",
        " does ",
        " is there ",
        " are there ",
    )
    business_markers = (
        " cost",
        " price",
        " final ",
        " warranty",
        " substrate",
        " material",
        " system",
        " status",
        " estimate",
        " labor",
        " crew",
        " profit",
        " overhead",
        " invoice",
        " contract",
        " proposal",
        " completed",
        " schedule",
        " square",
        " sqft",
        " sq ft",
    )
    return any(marker in normalized for marker in question_markers) or any(marker in normalized for marker in business_markers)


ASK_SPRAYTEC_STRUCTURED_TARGETS = {
    "jobs",
    "estimates",
    "estimate_line_items",
    "estimate_template_rows",
    "pricing_catalog",
    "product_catalog",
    "crew_schedule",
}


ASK_JOB_ATTRIBUTE_CONCEPTS = {
    "coating": (
        "coating",
        "silicone",
        "acrylic",
        "urethane",
        "elastomeric",
        "gaco s20",
        "gacoflex s20",
        "gaco s42",
        "gacoflex s42",
        "top coat",
        "base coat",
    ),
    "foam": (
        "foam",
        "spf",
        "spray foam",
        "roof foam",
        "polyurethane",
        "gaco roof",
        "gacoroof",
        "f2733",
        "2.7 lb",
        "2.8 lb",
    ),
    "primer": ("primer", "e5320", "e-5320", "epoxy primer", "rust primer"),
    "fabric": ("fabric", "reinforcement", "reinforcing", "mesh"),
    "fasteners": ("fastener", "fasteners", "screw", "screws", "plate", "plates"),
    "board": ("iso", "polyiso", "cover board", "board stock", "gypsum board"),
    "tearoff": ("tear off", "tear-off", "tearout", "tear out", "remove existing", "removal"),
    "sealant": ("sealant", "caulk", "mastic", "flashing", "sausage", "buttergrade"),
}


ASK_JOB_ATTRIBUTE_ACTION_MARKERS = (
    " find ",
    " show ",
    " list ",
    " which ",
    " what ",
    " jobs ",
    " job ",
    " with ",
    " had ",
    " have ",
    " used ",
    " required ",
    " needed ",
    " included ",
    " involving ",
)


ASK_JOB_SUBSTRATE_ALIASES = {
    "metal": ("metal", "metal roof", "metal roofs", "metal panel", "standing seam", "r panel", "r-panel"),
    "tpo": ("tpo",),
    "epdm": ("epdm", "rubber roof"),
    "concrete": ("concrete",),
    "spf": ("spf", "spray foam roof", "foam roof"),
    "mod bit": ("mod bit", "modified bitumen", "mod-bit"),
    "bur": ("bur", "built up roof", "built-up roof"),
}


ASK_JOB_SYSTEM_ALIASES = {
    "silicone": ("silicone", "gaco s20", "gacoflex s20", "gaco s42", "gacoflex s42"),
    "acrylic": ("acrylic",),
    "urethane": ("urethane", "polyurethane", "u91", "u92"),
    "gaco": ("gaco", "gacoflex", "gacoroof"),
}


def _prompt_has_any(normalized: str, markers: Iterable[str]) -> bool:
    return any(marker in normalized for marker in markers)


def _ask_attribute_normalized(value: object) -> str:
    return " " + " ".join(re.sub(r"[^a-z0-9.#]+", " ", str(value or "").lower()).split()) + " "


def _normalized_contains_phrase(normalized: str, phrase: str) -> bool:
    phrase_norm = " ".join(re.sub(r"[^a-z0-9.#]+", " ", phrase.lower()).split())
    if not phrase_norm:
        return False
    return f" {phrase_norm} " in normalized


def _parse_attribute_number(raw: str) -> float | None:
    cleaned = raw.replace(",", "").strip().lower()
    multiplier = 1000 if cleaned.endswith("k") else 1
    if cleaned.endswith("k"):
        cleaned = cleaned[:-1]
    try:
        return float(cleaned) * multiplier
    except ValueError:
        return None


def _infer_attribute_sqft_filter(normalized: str) -> dict[str, Any] | None:
    sqft_words = r"(?:sq\s*ft|sqft|square\s*feet|sf)"
    number = r"(\d[\d,]*(?:\.\d+)?k?)"
    between = re.search(rf"\bbetween\s+{number}\s+(?:and|-)\s+{number}\s*{sqft_words}\b", normalized)
    if between:
        lower = _parse_attribute_number(between.group(1))
        upper = _parse_attribute_number(between.group(2))
        if lower is not None and upper is not None:
            return {"operator": "between", "min": min(lower, upper), "max": max(lower, upper)}
    comparisons = [
        (rf"\b(?:over|above|more than|greater than|at least|>=)\s+{number}\s*{sqft_words}\b", ">="),
        (rf"\b(?:under|below|less than|no more than|<=)\s+{number}\s*{sqft_words}\b", "<="),
        (rf"\b{number}\s*\+\s*{sqft_words}\b", ">="),
    ]
    for pattern, operator in comparisons:
        match = re.search(pattern, normalized)
        if match:
            value = _parse_attribute_number(match.group(1))
            if value is not None:
                return {"operator": operator, "value": value}
    return None


def _infer_attribute_terms(normalized: str, alias_map: dict[str, tuple[str, ...]]) -> list[str]:
    return [
        key
        for key, aliases in alias_map.items()
        if any(_normalized_contains_phrase(normalized, alias) for alias in aliases)
    ]


def infer_ask_job_attribute_query(prompt: str, interpreted: dict[str, Any]) -> dict[str, Any]:
    normalized = _ask_attribute_normalized(prompt)
    concepts = [
        concept
        for concept, aliases in ASK_JOB_ATTRIBUTE_CONCEPTS.items()
        if any(_normalized_contains_phrase(normalized, alias) for alias in aliases)
    ]
    has_action = _prompt_has_any(normalized, ASK_JOB_ATTRIBUTE_ACTION_MARKERS)
    enabled = bool(concepts and has_action and (" job " in normalized or " jobs " in normalized or len(concepts) >= 2))
    year_match = re.search(r"\b(20\d{2})\b", normalized)
    warranty_match = re.search(r"\b(\d{1,2})\s*(?:-| )?(?:year|yr)\b", normalized)
    return {
        "enabled": enabled,
        "concepts": concepts,
        "division": interpreted.get("division"),
        "status": interpreted.get("status"),
        "year": int(year_match.group(1)) if year_match else None,
        "warranty_years": int(warranty_match.group(1)) if warranty_match else None,
        "substrates": _infer_attribute_terms(normalized, ASK_JOB_SUBSTRATE_ALIASES),
        "systems": _infer_attribute_terms(normalized, ASK_JOB_SYSTEM_ALIASES),
        "sqft_filter": _infer_attribute_sqft_filter(normalized),
    }


def plan_ask_spraytec_query(prompt: str, interpreted: dict[str, Any]) -> dict[str, Any]:
    normalized = " " + " ".join(str(prompt or "").lower().split()) + " "
    document_type = interpreted.get("document_type")
    search_text = text_value(interpreted.get("search_text"))
    targets: set[str] = {"jobs"}
    reasons: list[str] = []
    attribute_query = infer_ask_job_attribute_query(prompt, interpreted)

    if is_generated_field_notes_request(prompt):
        return {
            "mode": "generated_field_notes",
            "targets": ["historical_scope_texts", "template_examples", "estimate_template_rows"],
            "requires_job_context": True,
            "needs_clarification": not generated_field_notes_query(prompt),
            "clarification": "Which job, customer, or project should I use to generate field notes?",
            "use_llm_answer": False,
            "reason": "generate estimator field notes from historical proposal scope",
            "attribute_query": attribute_query,
        }

    document_markers = (
        " document ",
        " documents ",
        " file ",
        " files ",
        " folder ",
        " folders ",
        " estimate ",
        " proposal ",
        " contract ",
        " invoice ",
        " warranty ",
        " aerial ",
        " drawing ",
        " drawings ",
        " photo ",
        " photos ",
        " notes ",
        " spec ",
        " specs ",
        " submittal ",
    )
    estimate_markers = (
        " estimate ",
        " estimated ",
        " final price ",
        " price per ",
        " job cost ",
        " material subtotal ",
        " labor subtotal ",
        " overhead ",
        " profit ",
        " sqft ",
        " sq ft ",
        " square feet ",
        " warranty ",
        " substrate ",
        " material system ",
        " scope ",
    )
    pricing_markers = (
        " price ",
        " pricing ",
        " unit price ",
        " unit cost ",
        " cost per ",
        " catalog ",
        " rate ",
        " material cost ",
    )
    product_markers = (
        " product ",
        " pds ",
        " sds ",
        " technical data ",
        " application guide ",
        " coverage ",
        " yield ",
        " r-value ",
        " r value ",
        " thickness ",
        " foam ",
        " silicone ",
        " coating ",
        " primer ",
        " sealant ",
        " fabric ",
        " gaco ",
        " enverge ",
        " dc315 ",
        " noburn ",
        " accufoam ",
    )
    schedule_markers = (
        " schedule ",
        " scheduled ",
        " start date ",
        " starts ",
        " crew ",
        " dispatch ",
        " duration ",
        " backlog ",
        " ready ",
        " waiting ",
        " hold ",
        " permit ",
        " weather ",
        " equipment allocation ",
    )

    if document_type not in (None, "") or _prompt_has_any(normalized, document_markers):
        targets.update({"documents", "document_content"})
        reasons.append("document terms")
    if _prompt_has_any(normalized, estimate_markers):
        targets.update({"estimates", "estimate_line_items", "estimate_template_rows"})
        reasons.append("estimate/job financial terms")
    if _prompt_has_any(normalized, pricing_markers):
        targets.add("pricing_catalog")
        reasons.append("pricing terms")
    if _prompt_has_any(normalized, product_markers):
        targets.add("product_catalog")
        reasons.append("product/system terms")
    if _prompt_has_any(normalized, schedule_markers):
        targets.add("crew_schedule")
        reasons.append("schedule terms")

    if document_type not in (None, "", "all"):
        targets.update({"documents", "document_content"})
        reasons.append(f"requested {requested_document_label(document_type).lower()}")

    if attribute_query.get("enabled"):
        targets.difference_update({"pricing_catalog", "product_catalog", "documents"})
        reasons = [
            reason
            for reason in reasons
            if reason not in {"pricing terms", "product/system terms", "document terms"} and not str(reason).startswith("requested ")
        ]
        targets.update({"jobs", "estimates", "estimate_line_items", "estimate_template_rows", "document_content"})
        reasons.append("estimate attribute search")

    if targets == {"jobs"} and is_data_answer_request(prompt):
        targets.update({"estimates", "estimate_template_rows"})
        reasons.append("general data question")
    if targets <= {"jobs", "pricing_catalog", "product_catalog"} and targets & {"pricing_catalog", "product_catalog"}:
        targets.discard("jobs")

    needs_clarification = bool({"documents", "document_content"} & targets) and not search_text and not interpreted.get("is_follow_up")
    mode = "job_lookup"
    if attribute_query.get("enabled"):
        mode = "attribute_job_search"
    elif {"documents", "document_content"} & targets and targets - {"jobs", "documents", "document_content"}:
        mode = "mixed_answer"
    elif {"documents", "document_content"} & targets:
        mode = "document_lookup"
    elif targets & {"estimates", "estimate_line_items", "estimate_template_rows", "pricing_catalog", "product_catalog", "crew_schedule"}:
        mode = "structured_answer"

    return {
        "mode": mode,
        "targets": sorted(targets),
        "requires_job_context": bool(targets & {"jobs", "documents", "document_content", "estimates", "estimate_line_items", "estimate_template_rows", "crew_schedule"}),
        "needs_clarification": needs_clarification,
        "clarification": "Which job, customer, project, product, or file should I search for?" if needs_clarification else "",
        "use_llm_answer": mode in {"mixed_answer", "structured_answer"} or is_data_answer_request(prompt),
        "reason": "; ".join(dict.fromkeys(reasons)) or "job lookup",
        "attribute_query": attribute_query,
    }


def is_generated_field_notes_request(prompt: str) -> bool:
    normalized = " " + " ".join(str(prompt or "").lower().split()) + " "
    has_action = any(marker in normalized for marker in (" generate ", " create ", " draft ", " make "))
    has_field_notes = " field notes " in normalized or " field note " in normalized or " notes " in normalized
    has_scope_source = any(marker in normalized for marker in (" proposal scope ", " proposal ", " poposal ", " scope "))
    return bool(has_action and has_field_notes and has_scope_source)


def generated_field_notes_query(prompt: str) -> str:
    text = str(prompt or "").strip()
    match = re.search(r"\bfor\s+(.+)$", text, re.I)
    query = match.group(1).strip() if match else text
    query = re.sub(
        r"\b(?:generate|create|draft|make|some|field|notes?|from|proposal|poposal|scope|historical|the|a|an)\b",
        " ",
        query,
        flags=re.I,
    )
    return " ".join(query.split()).strip()


def generated_field_notes_template_types(prompt: str) -> list[str]:
    normalized = " " + " ".join(str(prompt or "").lower().split()) + " "
    if " floor " in normalized or " flooring " in normalized:
        return ["flooring"]
    if " insulation " in normalized or " insulate " in normalized or " spray foam " in normalized and " roof " not in normalized:
        return ["insulation"]
    if " roof " in normalized or " roofing " in normalized or " coating " in normalized:
        return ["roofing"]
    return ["roofing", "insulation", "flooring"]


def _json_dict_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not text_value(value):
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _template_examples_for_generated_notes(data: EstimatorData) -> pd.DataFrame:
    examples = getattr(data, "template_examples", pd.DataFrame())
    if isinstance(examples, pd.DataFrame) and not examples.empty:
        return examples
    try:
        built = build_template_examples(data)
    except Exception:
        logger.debug("could not build template examples for generated notes", exc_info=True)
        built = pd.DataFrame()
    if isinstance(built, pd.DataFrame) and not built.empty:
        return built
    fallback_path = Path("output/estimator_template_examples/estimator_template_examples.csv")
    if fallback_path.exists():
        try:
            return pd.read_csv(fallback_path)
        except Exception:
            logger.debug("could not read estimator template examples fallback csv", exc_info=True)
    return pd.DataFrame()


def _estimator_type_for_template(template_type: str) -> str:
    normalized = str(template_type or "").strip().lower()
    if normalized == "insulation":
        return ESTIMATE_TYPE_INSULATION
    if normalized == "flooring":
        return ESTIMATE_TYPE_FLOORING
    return ESTIMATE_TYPE_RESTORATION


def _division_for_template(template_type: str) -> str:
    normalized = str(template_type or "").strip().lower()
    if normalized == "insulation":
        return "Insulation"
    if normalized == "flooring":
        return "Flooring"
    if normalized == "roofing":
        return "Roofing"
    return normalized.title()


def _field_note_match_score(
    *,
    query: str,
    template_type: str,
    example: dict[str, Any],
    scope_row: dict[str, Any],
    answer_key: dict[str, Any],
) -> float:
    query_tokens = tokenize_search_text(query)
    candidate_text = " ".join(
        text_value(value)
        for value in (
            example.get("job_id"),
            example.get("customer"),
            example.get("job_name"),
            example.get("source_file"),
            scope_row.get("file_name"),
            scope_row.get("scope_text"),
        )
        if text_value(value)
    ).lower()
    if not query_tokens:
        return 0.0
    matched_tokens = sum(1 for token in query_tokens if token in candidate_text)
    if matched_tokens == 0:
        return 0.0
    score = matched_tokens * 25.0
    normalized_query = " ".join(query_tokens)
    if normalized_query and normalized_query in candidate_text:
        score += 80.0
    if len(query_tokens) > 1 and matched_tokens == len(query_tokens):
        score += 50.0
    for phrase in re.findall(r"\broof\s+[a-z0-9#-]+\b", query.lower()):
        if phrase in candidate_text:
            score += 35.0
    if template_type and template_type in candidate_text:
        score += 10.0
    if str(scope_row.get("document_type") or "").lower() == "proposal":
        score += 12.0
    if "proposal" in str(scope_row.get("file_name") or "").lower():
        score += 8.0
    summary = answer_key.get("summary") if isinstance(answer_key.get("summary"), dict) else {}
    score += min(float(summary.get("decision_count") or 0), 60.0) * 0.75
    if text_value(scope_row.get("scope_text")):
        score += min(len(text_value(scope_row.get("scope_text"))) / 250.0, 12.0)
    return score


def _generated_field_notes_resolution_bonus(query: str, candidate: dict[str, Any]) -> float:
    query_tokens = tokenize_search_text(query)
    if not query_tokens:
        return 0.0
    source_file = " ".join(tokenize_search_text(candidate.get("source_file")))
    proposal_file = " ".join(tokenize_search_text(candidate.get("proposal_file_name")))
    job_text = " ".join(
        tokenize_search_text(
            " ".join(
                text_value(candidate.get(field))
                for field in ("customer", "job_name", "job_id")
            )
        )
    )
    bonus = 0.0
    for token in query_tokens:
        if token in source_file:
            bonus += 8.0
        if token in proposal_file:
            bonus += 5.0
        if token in job_text:
            bonus += 2.0
    normalized_query = " ".join(query_tokens)
    for match in re.findall(r"\broof\s+([a-z0-9#-]+)\b", normalized_query):
        roof_variants = (f"roof {match}", f"{match} roof")
        if any(variant in source_file for variant in roof_variants):
            bonus += 45.0
        if any(variant in proposal_file for variant in roof_variants):
            bonus += 20.0
    for token in query_tokens:
        if re.fullmatch(r"20\d{2}", token) and token in source_file:
            bonus += 30.0
        if token in {"stamp", "final", "master", "white", "recoat"} and token in source_file:
            bonus += 35.0
        if token in {"10", "15", "20"} and token in source_file:
            bonus += 10.0
    if "signed" in proposal_file:
        bonus += 3.0
    if "final" in source_file:
        bonus += 8.0
    summary = candidate.get("answer_key_summary") if isinstance(candidate.get("answer_key_summary"), dict) else {}
    bonus += min(float(summary.get("decision_count") or 0), 80.0) * 0.1
    bonus += min(float(summary.get("source_row_count") or 0), 160.0) * 0.02
    return bonus


def _generated_field_notes_can_auto_select(candidates: list[dict[str, Any]]) -> bool:
    if len(candidates) <= 1:
        return True
    top_score = float(candidates[0].get("score") or 0.0)
    close = [candidate for candidate in candidates if top_score - float(candidate.get("score") or 0.0) < 20.0]
    if len(close) <= 1:
        return True
    close_job_ids = {text_value(candidate.get("job_id")) for candidate in close if text_value(candidate.get("job_id"))}
    close_estimates = {text_value(candidate.get("source_file")) for candidate in close if text_value(candidate.get("source_file"))}
    close_proposals = {text_value(candidate.get("proposal_file_name")) for candidate in close if text_value(candidate.get("proposal_file_name"))}
    if len(close_job_ids) == 1 and len(close_estimates) == 1:
        return True
    if len(close_estimates) == 1 and len(close_proposals) == 1:
        candidates[0]["selection_warning"] = (
            "The same historical estimate/proposal pair matched multiple job IDs; I selected the best-ranked job link. "
            "Confirm the source folder before relying on the generated workbook."
        )
        return True
    # In historical folders with several proposal/estimate variants for the same job, selecting
    # the best-ranked answer key is more useful than blocking the generator entirely.
    if len(close_job_ids) == 1:
        candidates[0]["selection_warning"] = (
            "Multiple historical estimates matched this job; I selected the best-ranked answer key. "
            "Confirm the estimate option before relying on the generated workbook."
        )
        return True
    return False


def _generated_scope_rows_from_examples(examples: pd.DataFrame) -> list[dict[str, Any]]:
    if examples.empty or "job_id" not in examples.columns:
        return []
    rows: list[dict[str, Any]] = []
    for example in examples.fillna("").to_dict(orient="records"):
        job_id = text_value(example.get("job_id"))
        scope_text = text_value(example.get("scope_summary"))
        if not scope_text:
            scope_text = text_value(example.get("decision_summary"))
        if not job_id or not scope_text:
            continue
        rows.append(
            {
                "job_id": job_id,
                "document_id": text_value(example.get("document_id")),
                "document_type": "template_example_scope_summary",
                "file_name": text_value(example.get("source_file")) or "Historical estimate scope summary",
                "scope_text": scope_text,
                "sharepoint_url": "",
                "scope_source": "template_example_scope_summary",
            }
        )
    return rows


def _generated_notes_answer_key_context(answer_key: dict[str, Any], *, max_decisions: int = 18) -> list[dict[str, Any]]:
    decisions = answer_key.get("decisions") if isinstance(answer_key, dict) else []
    if not isinstance(decisions, list):
        return []
    compact: list[dict[str, Any]] = []
    for decision in decisions:
        if not isinstance(decision, dict) or decision.get("include") is False:
            continue
        line_item = text_value(decision.get("line_item") or decision.get("template_line"))
        bucket = text_value(decision.get("template_bucket"))
        inputs = decision.get("inputs") if isinstance(decision.get("inputs"), dict) else {}
        input_keys = [
            key
            for key, value in inputs.items()
            if text_value(value) and key not in {"unit_price", "hourly_rate", "daily_rate", "estimated_cost", "total_cost"}
        ][:5]
        compact.append(
            {
                "bucket": bucket,
                "line_item": line_item,
                "non_price_inputs": input_keys,
            }
        )
        if len(compact) >= max_decisions:
            break
    return compact


def _fallback_scope_to_estimator_notes(
    *,
    customer: str,
    job_name: str,
    address: str,
    template_type: str,
    scope_text: str,
) -> str:
    cleaned_lines: list[str] = []
    skip_patterns = re.compile(
        r"\b("
        r"subtotal|total|proposal amount|contract amount|unit price|estimated cost|profit|overhead|sales tax|"
        r"payment|terms|signature|accepted|warranty fee|line item|source row|workbook row"
        r")\b",
        re.I,
    )
    for raw_line in re.split(r"[\r\n]+", scope_text):
        line = " ".join(str(raw_line or "").strip().split())
        if not line or skip_patterns.search(line):
            continue
        if len(line) > 320:
            line = line[:320].rstrip(" ,;:") + "."
        cleaned_lines.append(line)
        if len(cleaned_lines) >= 8:
            break
    prefix_parts = [part for part in (customer, job_name) if part]
    lines: list[str] = []
    if prefix_parts:
        lines.append(" / ".join(prefix_parts))
    if address:
        lines.append(f"Site address: {address}")
    if template_type == "insulation":
        lines.append("Estimator field notes from prior proposal scope: spray foam insulation review.")
    elif template_type == "flooring":
        lines.append("Estimator field notes from prior proposal scope: flooring/repair review.")
    else:
        lines.append("Estimator field notes from prior proposal scope: roof condition and restoration/repair review.")
    lines.extend(cleaned_lines)
    lines.append("Verify measurements, substrate condition, access, product/system selection, labor plan, logistics, and any warranty requirements before quoting.")
    return "\n".join(line for line in lines if line).strip()


def _call_openai_generated_field_notes_rewrite(payload: dict[str, Any]) -> dict[str, Any]:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not configured")
    try:
        from openai import OpenAI  # type: ignore
    except ImportError as exc:
        raise RuntimeError("openai package is not installed") from exc
    system = (
        "You rewrite historical Spray-Tec proposal scope text into realistic estimator field notes. "
        "The notes should sound like what an estimator might enter after reading a scope or walking a site, "
        "not like a formal proposal or an answer key."
    )
    instructions = [
        "Return strict JSON with keys generated_notes, note_style, preserved_cues, omitted_details, warnings.",
        "Keep customer/job/address, dimensions, areas, substrate, condition, access, constraints, customer intent, and uncertainty cues when present.",
        "Use short natural field-note prose or bullets.",
        "Do not copy proposal boilerplate, legal terms, pricing, totals, taxes, profit, overhead, payment terms, signatures, or acceptance language.",
        "Do not mention workbook rows, source rows, selector codes, or that an answer key exists.",
        "Do not list every product or line item from the historical estimate. Include product names only when the proposal text itself clearly makes them part of the field-facing scope.",
        "Convert final-scope certainty into review language when appropriate, such as verify, review, confirm, possible, likely, or if qualifies.",
        "Keep enough estimating cues that an AI estimator could infer template decisions, but leave product/package choices for the estimator system to infer from context and history.",
    ]
    user_payload = {**payload, "instructions": instructions}
    client = OpenAI(timeout=float(os.getenv("OPENAI_GENERATED_FIELD_NOTES_TIMEOUT_SECONDS", "25")))
    response = client.chat.completions.create(
        model=os.getenv("OPENAI_GENERATED_FIELD_NOTES_MODEL")
        or os.getenv("OPENAI_ASK_SPRAYTEC_MODEL")
        or os.getenv("OPENAI_MODEL")
        or "gpt-4o-mini",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user_payload, indent=2, default=str)},
        ],
        temperature=0.25,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content if response.choices else ""
    parsed = json.loads(text_value(content) or "{}")
    return parsed if isinstance(parsed, dict) else {}


def _rewrite_generated_field_notes_from_scope(
    *,
    example: dict[str, Any],
    scope_row: dict[str, Any],
    answer_key: dict[str, Any],
) -> dict[str, Any]:
    context = answer_key.get("job_context") if isinstance(answer_key.get("job_context"), dict) else {}
    customer = text_value(example.get("customer") or context.get("customer"))
    job_name = text_value(example.get("job_name") or context.get("job_name"))
    address = text_value(context.get("site_address") or context.get("address") or example.get("site_address"))
    template_type = text_value(example.get("template_type")).lower()
    scope_text = text_value(scope_row.get("scope_text"))
    if len(scope_text) > 5200:
        scope_text = scope_text[:5200].rstrip() + " ..."
    fallback_notes = _fallback_scope_to_estimator_notes(
        customer=customer,
        job_name=job_name,
        address=address,
        template_type=template_type,
        scope_text=scope_text,
    )
    payload = {
        "source_metadata": {
            "customer": customer,
            "job_name": job_name,
            "site_address": address,
            "template_type": template_type,
            "proposal_file": text_value(scope_row.get("file_name")),
            "estimate_file": text_value(example.get("source_file")),
        },
        "proposal_scope_text": scope_text,
        "historical_answer_key_context_for_cues_only": _generated_notes_answer_key_context(answer_key),
    }
    try:
        parsed = _call_openai_generated_field_notes_rewrite(payload)
        notes = text_value(parsed.get("generated_notes"))
        if notes:
            return {
                "generated_notes": notes,
                "note_style": text_value(parsed.get("note_style")) or "llm_field_note_rewrite",
                "generation_method": "openai_field_note_rewrite",
                "preserved_cues": parsed.get("preserved_cues") if isinstance(parsed.get("preserved_cues"), list) else [],
                "omitted_details": parsed.get("omitted_details") if isinstance(parsed.get("omitted_details"), list) else [],
                "warnings": parsed.get("warnings") if isinstance(parsed.get("warnings"), list) else [],
            }
    except Exception as exc:
        logger.info("Generated field-note LLM rewrite unavailable; using local fallback: %s", safe_exception_text(exc))
    return {
        "generated_notes": fallback_notes,
        "note_style": "local_scope_note_rewrite",
        "generation_method": "local_scope_note_rewrite",
        "preserved_cues": [],
        "omitted_details": ["proposal boilerplate", "pricing/totals/markup language"],
        "warnings": ["AI field-note rewrite was unavailable; used local proposal-scope cleanup."],
    }


def _generated_field_notes_from_scope(
    *,
    example: dict[str, Any],
    scope_row: dict[str, Any],
    answer_key: dict[str, Any],
) -> str:
    rewritten = _rewrite_generated_field_notes_from_scope(
        example=example,
        scope_row=scope_row,
        answer_key=answer_key,
    )
    return text_value(rewritten.get("generated_notes"))


def build_generated_field_notes_case_from_history(
    data: EstimatorData,
    prompt: str,
    *,
    limit: int = 4,
) -> dict[str, Any]:
    query = generated_field_notes_query(prompt)
    template_types = set(generated_field_notes_template_types(prompt))
    examples = _template_examples_for_generated_notes(data)
    if examples.empty:
        return {
            "status": "missing_source",
            "query": query,
            "message": "No estimator template examples with answer keys are loaded yet.",
            "candidates": [],
        }
    scope_rows = getattr(data, "historical_scope_texts", pd.DataFrame())
    fallback_scope_rows = False
    if not isinstance(scope_rows, pd.DataFrame) or scope_rows.empty:
        generated_rows = _generated_scope_rows_from_examples(examples)
        scope_rows = pd.DataFrame(generated_rows)
        fallback_scope_rows = True
    if scope_rows.empty:
        return {
            "status": "missing_source",
            "query": query,
            "message": (
                "No historical proposal scope text or template scope summaries are loaded, "
                "so I cannot generate field notes yet."
            ),
            "candidates": [],
        }
    if "job_id" not in examples.columns or "job_id" not in scope_rows.columns:
        return {
            "status": "missing_source",
            "query": query,
            "message": "Historical examples or proposal scope rows are missing job_id, so they cannot be paired reliably.",
            "candidates": [],
        }
    scope_by_job: dict[str, list[dict[str, Any]]] = {}
    for row in scope_rows.fillna("").to_dict(orient="records"):
        job_id = text_value(row.get("job_id"))
        if not job_id or not text_value(row.get("scope_text")):
            continue
        scope_by_job.setdefault(job_id, []).append(row)
    scored: list[tuple[float, dict[str, Any]]] = []
    for example in examples.fillna("").to_dict(orient="records"):
        template_type = text_value(example.get("template_type")).lower()
        if template_types and template_type not in template_types:
            continue
        job_id = text_value(example.get("job_id"))
        if not job_id or job_id not in scope_by_job:
            continue
        answer_key = _json_dict_value(example.get("answer_key_json"))
        if not answer_key:
            try:
                answer_key = build_reference_estimate_answer_key(
                    data,
                    document_id=text_value(example.get("document_id")) or None,
                    source_file=text_value(example.get("source_file")) or None,
                )
            except Exception:
                logger.debug("could not build reference answer key for generated notes", exc_info=True)
                answer_key = {}
        if not answer_key:
            continue
        for scope_row in scope_by_job.get(job_id, []):
            if (
                text_value(scope_row.get("scope_source")) == "template_example_scope_summary"
                and text_value(scope_row.get("file_name"))
                and text_value(example.get("source_file"))
                and text_value(scope_row.get("file_name")) != text_value(example.get("source_file"))
            ):
                continue
            score = _field_note_match_score(
                query=query,
                template_type=template_type,
                example=example,
                scope_row=scope_row,
                answer_key=answer_key,
            )
            if score <= 0:
                continue
            summary = answer_key.get("summary") if isinstance(answer_key.get("summary"), dict) else {}
            preferences = answer_key_to_workbook_decision_preferences(answer_key)
            candidate = {
                "status": "selected",
                "query": query,
                "score": round(score, 3),
                "template_type": template_type,
                "estimate_type": _estimator_type_for_template(template_type),
                "job_id": job_id,
                "customer": text_value(example.get("customer")),
                "job_name": text_value(example.get("job_name")),
                "source_file": text_value(example.get("source_file")),
                "proposal_file_name": text_value(scope_row.get("file_name")),
                "proposal_url": text_value(scope_row.get("sharepoint_url")),
                "scope_source": text_value(scope_row.get("scope_source")) or text_value(scope_row.get("document_type")),
                "used_scope_summary_fallback": fallback_scope_rows
                or text_value(scope_row.get("scope_source")) == "template_example_scope_summary",
                "generated_notes": "",
                "generated_notes_method": "",
                "generated_notes_style": "",
                "generated_notes_warnings": [],
                "answer_key": answer_key,
                "workbook_decision_preferences": preferences,
                "answer_key_summary": {
                    "decision_count": int(summary.get("decision_count") or len(answer_key.get("decisions") or [])),
                    "unmapped_count": int(summary.get("unmapped_count") or 0),
                    "source_row_count": int(summary.get("source_row_count") or 0),
                    "preference_count": len(preferences),
                },
                "_rewrite_example": example,
                "_rewrite_scope_row": scope_row,
            }
            candidate_score = score + _generated_field_notes_resolution_bonus(query, candidate)
            candidate["score"] = round(candidate_score, 3)
            scored.append((candidate_score, candidate))
    scored.sort(key=lambda item: item[0], reverse=True)
    candidates = [item for _, item in scored[: max(1, int(limit or 4))]]
    if not candidates:
        return {
            "status": "not_found",
            "query": query,
            "message": f"I could not find a proposal scope plus answer-key match for {query or 'that request'}.",
            "candidates": [],
        }
    if len(candidates) > 1 and candidates[0]["score"] < candidates[1]["score"] + 20 and not _generated_field_notes_can_auto_select(candidates):
        return {
            "status": "ambiguous",
            "query": query,
            "message": "I found multiple plausible proposal/estimate pairs. Ask with the proposal or estimate file name, or use one of these candidates.",
            "candidates": candidates,
        }
    return finalize_generated_field_notes_candidate(candidates[0], candidates=candidates)


def generated_field_notes_response(case: dict[str, Any]) -> str:
    status = text_value(case.get("status"))
    if status in {"missing_source", "not_found"}:
        return text_value(case.get("message")) or "I could not generate field notes from historical proposal scope."
    if status == "ambiguous":
        lines = [text_value(case.get("message")) or "I found multiple candidates.", ""]
        for index, candidate in enumerate(case.get("candidates") or [], start=1):
            summary = candidate.get("answer_key_summary") if isinstance(candidate.get("answer_key_summary"), dict) else {}
            lines.append(
                f"{index}. {candidate.get('customer') or ''} / {candidate.get('job_name') or ''} "
                f"- proposal: {candidate.get('proposal_file_name') or 'unknown'} "
                f"- estimate: {candidate.get('source_file') or 'unknown'} "
                f"- decisions: {summary.get('decision_count', 0)} "
                f"- score: {candidate.get('score')}"
            )
        return "\n".join(lines).strip()
    summary = case.get("answer_key_summary") if isinstance(case.get("answer_key_summary"), dict) else {}
    lines = [
        "Generated field notes from historical proposal scope:",
        "",
        "```text",
        text_value(case.get("generated_notes")),
        "```",
        "",
        "Attached to Estimating Assistant context:",
        f"- Proposal scope: {case.get('proposal_file_name') or 'unknown'}",
        f"- Estimate answer key: {case.get('source_file') or 'unknown'}",
        f"- Template type: {case.get('template_type') or 'unknown'}",
        f"- Notes rewrite: {case.get('generated_notes_method') or 'unknown'}",
        f"- Answer-key decisions: {summary.get('decision_count', 0)} mapped, {summary.get('unmapped_count', 0)} unmapped",
        "- Mode: generated notes only. The matched answer key is retained for evaluation/reference, not automatically applied.",
        "",
        "Open Estimating Assistant and build/rebuild the workbook from the generated notes. Use an explicit learn/apply command only when you want the answer key to drive decisions.",
    ]
    if case.get("used_scope_summary_fallback"):
        lines.insert(
            -2,
            "- Scope source note: proposal scope text was not loaded for this match, so I used the historical template scope summary.",
        )
    if text_value(case.get("selection_warning")):
        lines.insert(-2, f"- Selection note: {case.get('selection_warning')}")
    for warning in case.get("generated_notes_warnings") or []:
        if text_value(warning):
            lines.insert(-2, f"- Notes rewrite warning: {warning}")
    if text_value(case.get("proposal_url")):
        lines.insert(-2, f"- Proposal link: {markdown_link(case.get('proposal_file_name') or 'proposal', case.get('proposal_url'))}")
    return "\n".join(lines).strip()


ASK_SPRAYTEC_PENDING_GENERATED_FIELD_NOTES_KEY = "ask_spraytec_pending_generated_field_notes"


def ask_spraytec_option_selection_index(prompt: str) -> int | None:
    normalized = text_value(prompt).lower().strip()
    if not normalized:
        return None
    match = re.search(r"^(?:use|select|choose|pick|go\s+with)?\s*(?:option|#|number|candidate)?\s*(\d+)\b", normalized)
    if not match:
        return None
    return max(int(match.group(1)) - 1, 0)


def finalize_generated_field_notes_candidate(candidate: dict[str, Any], *, candidates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    selected = dict(candidate or {})
    selected["status"] = "selected"
    generated_note_result = _rewrite_generated_field_notes_from_scope(
        example=selected.get("_rewrite_example") if isinstance(selected.get("_rewrite_example"), dict) else {},
        scope_row=selected.get("_rewrite_scope_row") if isinstance(selected.get("_rewrite_scope_row"), dict) else {},
        answer_key=selected.get("answer_key") if isinstance(selected.get("answer_key"), dict) else {},
    )
    selected["generated_notes"] = text_value(generated_note_result.get("generated_notes"))
    selected["generated_notes_method"] = text_value(generated_note_result.get("generation_method"))
    selected["generated_notes_style"] = text_value(generated_note_result.get("note_style"))
    selected["generated_notes_warnings"] = (
        generated_note_result.get("warnings") if isinstance(generated_note_result.get("warnings"), list) else []
    )
    cleaned_candidates = []
    for candidate_row in candidates or []:
        cleaned_candidates.append({key: value for key, value in candidate_row.items() if not str(key).startswith("_rewrite_")})
    selected = {key: value for key, value in selected.items() if not str(key).startswith("_rewrite_")}
    if cleaned_candidates:
        selected["candidates"] = cleaned_candidates
    return selected


def attach_generated_field_notes_case_to_estimator_context(case: dict[str, Any]) -> str:
    if text_value(case.get("status")) != "selected":
        return ""
    notes = text_value(case.get("generated_notes"))
    if not notes:
        return ""
    thread_id = reset_current_estimator_chat_thread()
    template_type = text_value(case.get("template_type"))
    result_payload = {
        "source": "ask_spraytec_generated_proposal_scope",
        "confidence": min(float(case.get("score") or 0.0) / 200.0, 0.95),
        "estimator_notes": notes,
        "assistant_message": "Generated field notes from historical proposal scope. The matched answer key is retained for evaluation/reference and was not applied to workbook decisions.",
        "missing_questions": [],
        "warnings": [],
        "scope_overrides": {
            "template_type": template_type,
            "division": _division_for_template(template_type),
            "raw_input_notes": notes,
            "reference_job_id": case.get("job_id"),
            "reference_source_file": case.get("source_file"),
            "reference_proposal_file": case.get("proposal_file_name"),
        },
        "workbook_decision_preferences": [],
        "reference_answer_key_mode": "evaluate",
        "reference_answer_key": case.get("answer_key") or {},
    }
    history = [
        {
            "role": "assistant",
            "content": generated_field_notes_response(case),
        }
    ]
    st.session_state["estimator_notes"] = notes
    st.session_state["estimator_estimate_type"] = _estimator_type_for_template(template_type)
    st.session_state["estimator_chat_result_active"] = result_payload
    st.session_state["estimator_chat_history_active"] = history
    st.session_state[f"estimator_chat_result_{thread_id}"] = result_payload
    st.session_state[f"estimator_chat_history_{thread_id}"] = history
    save_estimator_chat_session(
        thread_id,
        history=history,
        result=result_payload,
        estimator_notes=notes,
        estimate_type=_estimator_type_for_template(template_type),
    )
    return thread_id


def indexed_document_markdown(doc: dict[str, Any]) -> str:
    file_name = text_value(doc.get("file_name")) or text_value(doc.get("document_id")) or "Document"
    label = str(doc.get("document_type") or "document").replace("_", " ").title()
    url = text_value(doc.get("sharepoint_url"))
    title = markdown_link(file_name, url) if url else file_name
    context_parts = [
        text_value(doc.get("job_id")),
        text_value(doc.get("folder_path")) or text_value(doc.get("relative_path")),
        text_value(doc.get("classification_reason")),
    ]
    context = " · ".join(part for part in context_parts if part)
    return f"- **{label}:** {title}" + (f"\n  {context}" if context else "")


def indexed_documents_response(
    docs: list[dict[str, Any]],
    *,
    interpreted: dict[str, Any],
    query: str,
    limit: int = 20,
) -> str:
    requested = requested_document_label(interpreted.get("document_type"))
    search_text = text_value(interpreted.get("search_text")) or query
    if not docs:
        return ""
    shown = docs[:limit]
    lines = [
        f"I found {len(docs):,} indexed {requested.lower()} match{'es' if len(docs) != 1 else ''} for **{search_text}**.",
        "",
    ]
    lines.extend(indexed_document_markdown(doc) for doc in shown)
    if len(docs) > len(shown):
        lines.append(f"\nShowing the first {len(shown):,}. Add a document type, year, location, or file name to narrow this down.")
    return "\n".join(lines)


ASK_DOCUMENT_CHUNK_LIMIT = 24
ASK_DOCUMENT_FETCH_LIMIT = 250
ASK_DOCUMENT_CHUNK_CHAR_LIMIT = 1400
ASK_DOCUMENT_TOTAL_CHAR_LIMIT = 18000


def source_label_for_chunk(chunk: dict[str, Any], index: int) -> str:
    file_name = text_value(chunk.get("file_name")) or text_value(chunk.get("document_id")) or "Document"
    parts = [file_name]
    if text_value(chunk.get("page_number")):
        parts.append(f"page {text_value(chunk.get('page_number'))}")
    if text_value(chunk.get("sheet_name")):
        parts.append(f"sheet {text_value(chunk.get('sheet_name'))}")
    if text_value(chunk.get("row_number")):
        parts.append(f"row {text_value(chunk.get('row_number'))}")
    if text_value(chunk.get("source_locator")) and len(parts) == 1:
        parts.append(text_value(chunk.get("source_locator")))
    return f"S{index}: " + ", ".join(parts)


def score_document_chunk(chunk: dict[str, Any], tokens: list[str]) -> float:
    text_blob = " ".join(
        text_value(chunk.get(field))
        for field in ("text_content", "file_name", "document_type", "sheet_name", "section_name", "source_locator")
    ).lower()
    if not tokens:
        return 1.0
    matched = sum(1 for token in tokens if token.lower() in text_blob)
    phrase_bonus = 2.0 if " ".join(tokens).lower() in text_blob else 0.0
    location_bonus = 0.5 if any(text_value(chunk.get(field)) for field in ("page_number", "sheet_name", "row_number")) else 0.0
    return matched * 2.0 + phrase_bonus + location_bonus


def rank_document_content_chunks(rows: list[dict[str, Any]], query: str, *, limit: int = ASK_DOCUMENT_CHUNK_LIMIT) -> list[dict[str, Any]]:
    tokens = tokenize_search_text(query)
    ranked = sorted(
        rows,
        key=lambda row: (
            score_document_chunk(row, tokens),
            text_value(row.get("file_name")),
            -int(float(row.get("page_number") or row.get("row_number") or 0)),
        ),
        reverse=True,
    )
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    total_chars = 0
    for row in ranked:
        text_content = " ".join(text_value(row.get("text_content")).split())
        if not text_content:
            continue
        key = (
            text_value(row.get("document_id")),
            text_value(row.get("source_locator")),
            text_content[:120],
        )
        if key in seen:
            continue
        seen.add(key)
        clipped = text_content[:ASK_DOCUMENT_CHUNK_CHAR_LIMIT]
        total_chars += len(clipped)
        chunk = dict(row)
        chunk["text_content"] = clipped
        out.append(chunk)
        if len(out) >= limit or total_chars >= ASK_DOCUMENT_TOTAL_CHAR_LIMIT:
            break
    return out


def fetch_document_content_chunks(
    connection: Any,
    *,
    query: str,
    document_ids: list[str] | None = None,
    job_id: str | None = None,
    document_type: str | None = None,
    limit: int = ASK_DOCUMENT_CHUNK_LIMIT,
) -> list[dict[str, Any]]:
    clauses = []
    params: dict[str, Any] = {"fetch_limit": ASK_DOCUMENT_FETCH_LIMIT}
    statement = """
        SELECT
            c.content_id,
            c.document_id,
            c.job_id,
            d.file_name,
            d.document_type,
            d.sharepoint_url,
            d.folder_path,
            d.relative_path,
            c.content_type,
            c.source_locator,
            c.page_number,
            c.sheet_name,
            c.cell_range,
            c.row_number,
            c.section_name,
            c.text_content
        FROM document_content c
        LEFT JOIN documents d ON d.document_id = c.document_id
    """
    bindparams = []
    clean_document_ids = [doc_id for doc_id in (document_ids or []) if text_value(doc_id)]
    if clean_document_ids:
        clauses.append("c.document_id IN :document_ids")
        params["document_ids"] = clean_document_ids[:50]
        bindparams.append(bindparam("document_ids", expanding=True))
    if job_id:
        clauses.append("c.job_id = :job_id")
        params["job_id"] = str(job_id)
    if document_type and document_type != "all":
        clauses.append("d.document_type = :document_type")
        params["document_type"] = document_type
    if clauses:
        statement += " WHERE " + " AND ".join(clauses)
    statement += """
        ORDER BY d.file_name, c.page_number NULLS LAST, c.sheet_name NULLS LAST, c.row_number NULLS LAST
        LIMIT :fetch_limit
    """
    sql = text(statement)
    if bindparams:
        sql = sql.bindparams(*bindparams)
    rows = [dict(row) for row in connection.execute(sql, params).mappings().all()]
    return rank_document_content_chunks(rows, query, limit=limit)


def source_lines_for_document_chunks(chunks: list[dict[str, Any]]) -> list[str]:
    lines = []
    for index, chunk in enumerate(chunks, start=1):
        label = source_label_for_chunk(chunk, index)
        url = text_value(chunk.get("sharepoint_url"))
        source = markdown_link(label, url) if url else label
        excerpt = text_value(chunk.get("text_content"))
        lines.append(f"- [{label.split(':', 1)[0]}] {source}: {excerpt[:320]}")
    return lines


def _connection_table_columns(connection: Any, table_name: str) -> set[str]:
    try:
        rows = connection.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = :table_name
                """
            ),
            {"table_name": table_name},
        ).fetchall()
    except Exception:
        return set()
    return {str(row[0]) for row in rows}


def _select_columns(table_columns: set[str], requested: list[str]) -> list[str]:
    return [column for column in requested if column in table_columns]


def _json_ready_record(row: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for key, value in row.items():
        if value is None:
            continue
        if isinstance(value, float) and pd.isna(value):
            continue
        out[key] = value
    return out


def _query_rows(connection: Any, sql: Any, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [_json_ready_record(dict(row)) for row in connection.execute(sql, params or {}).mappings().all()]


def _attribute_text_expr(alias: str, columns: set[str], fields: list[str]) -> str:
    present = [field for field in fields if field in columns]
    if not present:
        return "LOWER('')"
    joined = ", ".join(f"COALESCE({alias}.{field}::text, '')" for field in present)
    return f"LOWER(CONCAT_WS(' ', {joined}))"


def _non_informational_attribute_filter(text_expr: str) -> str:
    blocked = [
        "total job cost",
        "worksheet price",
        "work sheet price",
        "estimated o/h",
        "overhead",
        "profit",
        "subtotal",
        "sub total",
    ]
    return " AND ".join(f"{text_expr} NOT LIKE '%{term}%'" for term in blocked)


def _query_attribute_evidence_table(
    connection: Any,
    *,
    table_name: str,
    concept: str,
    aliases: Iterable[str],
    limit: int,
) -> list[dict[str, Any]]:
    columns = _connection_table_columns(connection, table_name)
    if "job_id" not in columns:
        return []
    if table_name == "estimate_template_rows":
        searchable_fields = [
            "template_type",
            "template_bucket",
            "template_section",
            "line_item_kind",
            "row_label",
            "raw_text",
            "selected_item_name",
            "resolved_item_name",
            "source_file",
            "sheet_name",
        ]
        requested_fields = [
            "job_id",
            "source_file",
            "template_type",
            "template_bucket",
            "template_section",
            "line_item_kind",
            "row_number",
            "row_label",
            "selected_item_name",
            "resolved_item_name",
            "quantity",
            "unit",
            "unit_price",
            "estimated_units",
            "estimated_cost",
            "area_sqft",
            "thickness_inches",
            "yield_or_coverage",
            "estimated_sets",
            "estimated_gallons",
            "warranty_years",
            "needs_review",
        ]
    else:
        searchable_fields = [
            "division",
            "section",
            "line_item_category",
            "line_item_name",
            "description",
            "vendor",
            "notes",
            "estimate_file",
            "source_sheet",
        ]
        requested_fields = [
            "job_id",
            "estimate_file",
            "division",
            "pipeline_status",
            "customer",
            "job_name",
            "section",
            "line_item_category",
            "line_item_name",
            "description",
            "quantity",
            "unit",
            "unit_cost",
            "unit_price",
            "extended_cost",
            "labor_days",
            "crew_size",
            "labor_hours",
            "vendor",
            "notes",
            "source_sheet",
            "source_row",
        ]
    selected = _select_columns(columns, requested_fields)
    if not selected:
        return []
    text_expr = _attribute_text_expr("r", columns, searchable_fields)
    where = ["r.job_id IS NOT NULL", _non_informational_attribute_filter(text_expr)]
    params: dict[str, Any] = {"limit": limit}
    alias_clauses = []
    for index, alias in enumerate(aliases):
        key = f"alias_{index}"
        alias_clauses.append(f"{text_expr} LIKE :{key}")
        params[key] = f"%{alias.lower()}%"
    if not alias_clauses:
        return []
    where.append("(" + " OR ".join(alias_clauses) + ")")
    select_sql = ", ".join(f"r.{column}" for column in selected)
    statement = text(
        f"""
        SELECT
            '{table_name}' AS source_table,
            :matched_concept AS matched_concept,
            {select_sql}
        FROM {table_name} r
        WHERE {' AND '.join(where)}
        ORDER BY r.job_id
        LIMIT :limit
        """
    )
    rows = _query_rows(connection, statement, {**params, "matched_concept": concept})
    for row in rows:
        row["matched_concept"] = concept
        row["source_table"] = table_name
    return rows


def _fetch_ask_job_rows(connection: Any, job_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not job_ids:
        return {}
    columns = _connection_table_columns(connection, "jobs")
    if "job_id" not in columns:
        return {}
    selected = _select_columns(
        columns,
        [
            "job_id",
            "customer",
            "job_name",
            "division",
            "pipeline_status",
            "status",
            "job_type",
            "site_address",
            "city",
            "state",
            "estimated_sqft",
            "total_job_cost",
            "final_price",
            "price_per_sqft",
            "folder_url",
            "estimate_file",
            "source_year",
            "updated_at",
        ],
    )
    if not selected:
        return {}
    statement = text(
        f"SELECT {', '.join(selected)} FROM jobs WHERE job_id IN :job_ids"
    ).bindparams(bindparam("job_ids", expanding=True))
    rows = _query_rows(connection, statement, {"job_ids": job_ids})
    return {text_value(row.get("job_id")): row for row in rows}


def _fetch_ask_estimate_rows(connection: Any, job_ids: list[str], limit: int) -> dict[str, dict[str, Any]]:
    if not job_ids:
        return {}
    columns = _connection_table_columns(connection, "estimates")
    if "job_id" not in columns:
        return {}
    selected = _select_columns(
        columns,
        [
            "job_id",
            "estimate_file",
            "estimate_scope_type",
            "division",
            "pipeline_status",
            "customer",
            "job_name",
            "job_type",
            "estimated_sqft",
            "material_subtotal",
            "labor_subtotal",
            "equipment_subtotal",
            "total_job_cost",
            "worksheet_price",
            "final_price",
            "price_per_sqft",
            "estimated_duration_days",
            "estimated_labor_hours",
            "estimated_crew_size",
            "warranty_amount",
            "updated_at",
        ],
    )
    if not selected:
        return {}
    order_sql = "updated_at DESC NULLS LAST" if "updated_at" in columns else "job_id"
    statement = text(
        f"""
        SELECT {', '.join(selected)}
        FROM estimates
        WHERE job_id IN :job_ids
        ORDER BY {order_sql}
        LIMIT :limit
        """
    ).bindparams(bindparam("job_ids", expanding=True))
    rows = _query_rows(connection, statement, {"job_ids": job_ids, "limit": max(limit, len(job_ids))})
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        job_id = text_value(row.get("job_id"))
        if job_id and job_id not in out:
            out[job_id] = row
    return out


def _fetch_ask_document_signal_rows(connection: Any, job_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not job_ids:
        return {}
    columns = _connection_table_columns(connection, "document_content")
    if not {"job_id", "text_content"}.issubset(columns):
        return {}
    text_expr = "LOWER(COALESCE(d.text_content, ''))"
    if "normalized_text" in columns:
        text_expr = "LOWER(COALESCE(d.normalized_text, d.text_content, ''))"
    statement = text(
        f"""
        WITH signals AS (
            SELECT
                d.job_id,
                CASE
                    WHEN {text_expr} LIKE '%metal roof%' OR {text_expr} LIKE '%metal panel%' OR {text_expr} LIKE '%standing seam%' THEN 'metal'
                    WHEN {text_expr} LIKE '%tpo%' THEN 'tpo'
                    WHEN {text_expr} LIKE '%epdm%' THEN 'epdm'
                    WHEN {text_expr} LIKE '%concrete%' THEN 'concrete'
                    WHEN {text_expr} LIKE '%spray foam%' OR {text_expr} LIKE '%spf%' THEN 'spf'
                    WHEN {text_expr} LIKE '%modified bitumen%' OR {text_expr} LIKE '%mod bit%' THEN 'mod bit'
                    WHEN {text_expr} LIKE '%built up roof%' OR {text_expr} LIKE '%built-up roof%' THEN 'bur'
                    ELSE NULL
                END AS substrate_signal,
                CASE
                    WHEN {text_expr} LIKE '%silicone%' THEN 'silicone'
                    WHEN {text_expr} LIKE '%acrylic%' THEN 'acrylic'
                    WHEN {text_expr} LIKE '%urethane%' THEN 'urethane'
                    WHEN {text_expr} LIKE '%gaco%' THEN 'gaco'
                    ELSE NULL
                END AS material_signal,
                NULLIF(SUBSTRING({text_expr} FROM '([0-9]{{1,2}})[ -]?year'), '')::NUMERIC AS warranty_year_signal
            FROM document_content d
            WHERE d.job_id IN :job_ids
              AND (
                {text_expr} LIKE '%metal%'
                OR {text_expr} LIKE '%tpo%'
                OR {text_expr} LIKE '%epdm%'
                OR {text_expr} LIKE '%concrete%'
                OR {text_expr} LIKE '%spf%'
                OR {text_expr} LIKE '%spray foam%'
                OR {text_expr} LIKE '%silicone%'
                OR {text_expr} LIKE '%acrylic%'
                OR {text_expr} LIKE '%urethane%'
                OR {text_expr} LIKE '%gaco%'
                OR {text_expr} LIKE '%warranty%'
              )
        )
        SELECT
            job_id,
            STRING_AGG(DISTINCT substrate_signal, ', ') FILTER (WHERE substrate_signal IS NOT NULL) AS document_substrate,
            STRING_AGG(DISTINCT material_signal, ', ') FILTER (WHERE material_signal IS NOT NULL) AS document_material_system,
            MAX(warranty_year_signal) AS document_warranty_years
        FROM signals
        GROUP BY job_id
        """
    ).bindparams(bindparam("job_ids", expanding=True))
    rows = _query_rows(connection, statement, {"job_ids": job_ids})
    return {text_value(row.get("job_id")): row for row in rows}


def _attribute_numeric_value(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(number):
        return None
    return number


def _attribute_max_numeric(rows: Iterable[dict[str, Any]], fields: Iterable[str]) -> float | None:
    values: list[float] = []
    for row in rows:
        for field in fields:
            number = _attribute_numeric_value(row.get(field))
            if number is not None:
                values.append(number)
    return max(values) if values else None


def _attribute_best_sqft(result: dict[str, Any]) -> float | None:
    direct = _attribute_numeric_value(result.get("estimated_sqft"))
    if direct is not None and direct > 0:
        return direct
    evidence = result.get("match_evidence") if isinstance(result.get("match_evidence"), dict) else {}
    rows = [row for concept_rows in evidence.values() for row in concept_rows]
    return _attribute_max_numeric(rows, ("area_sqft", "quantity"))


def _attribute_best_warranty_years(result: dict[str, Any]) -> float | None:
    for field in ("warranty_years", "template_warranty_years", "document_warranty_years"):
        direct = _attribute_numeric_value(result.get(field))
        if direct is not None and direct > 0:
            return direct
    evidence = result.get("match_evidence") if isinstance(result.get("match_evidence"), dict) else {}
    rows = [row for concept_rows in evidence.values() for row in concept_rows]
    return _attribute_max_numeric(rows, ("warranty_years",))


def _sqft_filter_matches(value: float | None, sqft_filter: dict[str, Any] | None) -> bool:
    if not sqft_filter:
        return True
    if value is None:
        return False
    operator = sqft_filter.get("operator")
    if operator == "between":
        return float(sqft_filter.get("min") or 0) <= value <= float(sqft_filter.get("max") or 0)
    threshold = float(sqft_filter.get("value") or 0)
    if operator == ">=":
        return value >= threshold
    if operator == "<=":
        return value <= threshold
    return True


def _attribute_result_matches_filter(result: dict[str, Any], interpreted: dict[str, Any], attribute_query: dict[str, Any]) -> bool:
    division = text_value(attribute_query.get("division") or interpreted.get("division")).lower()
    status = text_value(attribute_query.get("status") or interpreted.get("status")).lower()
    year = attribute_query.get("year")
    warranty_years = attribute_query.get("warranty_years")
    substrates = [text_value(term).lower() for term in attribute_query.get("substrates") or [] if text_value(term)]
    systems = [text_value(term).lower() for term in attribute_query.get("systems") or [] if text_value(term)]
    sqft_filter = attribute_query.get("sqft_filter") if isinstance(attribute_query.get("sqft_filter"), dict) else None
    haystack = _ask_attribute_normalized(
        " ".join(
            text_value(value)
            for value in [
                result.get("division"),
                result.get("job_type"),
                result.get("estimate_scope_type"),
                result.get("status"),
                result.get("pipeline_status"),
                result.get("source_year"),
                result.get("estimate_file"),
                result.get("source_file"),
                result.get("document_substrate"),
                result.get("document_material_system"),
                result.get("match_evidence_text"),
            ]
        )
    )
    if division and division not in haystack:
        return False
    if status and status not in haystack:
        return False
    if year and str(year) not in haystack:
        return False
    if warranty_years:
        found_warranty = _attribute_best_warranty_years(result)
        if found_warranty is None or int(found_warranty) != int(warranty_years):
            return False
    if substrates and not any(term in haystack for term in substrates):
        return False
    if systems and not any(term in haystack for term in systems):
        return False
    if not _sqft_filter_matches(_attribute_best_sqft(result), sqft_filter):
        return False
    return True


def assemble_attribute_job_matches(
    evidence_rows: list[dict[str, Any]],
    *,
    required_concepts: list[str],
    job_rows: dict[str, dict[str, Any]] | None = None,
    estimate_rows: dict[str, dict[str, Any]] | None = None,
    document_signal_rows: dict[str, dict[str, Any]] | None = None,
    interpreted: dict[str, Any] | None = None,
    attribute_query: dict[str, Any] | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    job_rows = job_rows or {}
    estimate_rows = estimate_rows or {}
    document_signal_rows = document_signal_rows or {}
    interpreted = interpreted or {}
    attribute_query = attribute_query or {}
    by_job: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for row in evidence_rows:
        job_id = text_value(row.get("job_id"))
        concept = text_value(row.get("matched_concept"))
        if not job_id or concept not in required_concepts:
            continue
        by_job.setdefault(job_id, {}).setdefault(concept, []).append(row)

    results: list[dict[str, Any]] = []
    for job_id, evidence_by_concept in by_job.items():
        if not all(evidence_by_concept.get(concept) for concept in required_concepts):
            continue
        job = dict(job_rows.get(job_id) or {})
        estimate = dict(estimate_rows.get(job_id) or {})
        document_signals = dict(document_signal_rows.get(job_id) or {})
        merged = {**estimate, **document_signals, **job}
        merged["job_id"] = job_id
        merged["matched_concepts"] = required_concepts
        merged["match_evidence"] = {
            concept: rows[:4]
            for concept, rows in evidence_by_concept.items()
        }
        template_warranty = _attribute_max_numeric(
            [row for rows in evidence_by_concept.values() for row in rows],
            ("warranty_years",),
        )
        if template_warranty is not None:
            merged["template_warranty_years"] = template_warranty
        merged["match_evidence_count"] = sum(len(rows) for rows in evidence_by_concept.values())
        merged["match_score"] = 90 + len(required_concepts) * 5 + min(merged["match_evidence_count"], 10)
        merged["match_reason"] = "Historical estimate rows matched all requested attributes: " + ", ".join(required_concepts)
        merged["match_evidence_text"] = " ".join(
            " ".join(text_value(row.get(field)) for field in row)
            for rows in evidence_by_concept.values()
            for row in rows[:3]
        )
        if _attribute_result_matches_filter(merged, interpreted, attribute_query):
            results.append(merged)
    return sorted(
        results,
        key=lambda row: (
            float(row.get("match_score") or 0),
            float(row.get("final_price") or row.get("worksheet_price") or row.get("total_job_cost") or 0),
            text_value(row.get("customer")),
        ),
        reverse=True,
    )[:limit]


def search_jobs_by_estimate_attributes(
    connection: Any,
    *,
    concepts: list[str],
    interpreted: dict[str, Any],
    attribute_query: dict[str, Any],
    limit: int = 20,
) -> list[dict[str, Any]]:
    required_concepts = [concept for concept in concepts if concept in ASK_JOB_ATTRIBUTE_CONCEPTS]
    if not required_concepts:
        return []
    evidence_rows: list[dict[str, Any]] = []
    per_concept_limit = max(limit * 80, 500)
    for concept in required_concepts:
        aliases = ASK_JOB_ATTRIBUTE_CONCEPTS[concept]
        evidence_rows.extend(
            _query_attribute_evidence_table(
                connection,
                table_name="estimate_template_rows",
                concept=concept,
                aliases=aliases,
                limit=per_concept_limit,
            )
        )
        evidence_rows.extend(
            _query_attribute_evidence_table(
                connection,
                table_name="estimate_line_items",
                concept=concept,
                aliases=aliases,
                limit=per_concept_limit,
            )
        )
    candidate_job_ids = list(dict.fromkeys(text_value(row.get("job_id")) for row in evidence_rows if text_value(row.get("job_id"))))
    job_rows = _fetch_ask_job_rows(connection, candidate_job_ids)
    estimate_rows = _fetch_ask_estimate_rows(connection, candidate_job_ids, limit=max(len(candidate_job_ids), limit * 3))
    document_signal_rows = _fetch_ask_document_signal_rows(connection, candidate_job_ids)
    return assemble_attribute_job_matches(
        evidence_rows,
        required_concepts=required_concepts,
        job_rows=job_rows,
        estimate_rows=estimate_rows,
        document_signal_rows=document_signal_rows,
        interpreted=interpreted,
        attribute_query=attribute_query,
        limit=limit,
    )


def _format_attribute_money(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if pd.isna(number):
        return ""
    return f"${number:,.0f}"


def _attribute_evidence_label(row: dict[str, Any]) -> str:
    label = text_value(
        row.get("selected_item_name")
        or row.get("resolved_item_name")
        or row.get("line_item_name")
        or row.get("row_label")
        or row.get("description")
        or row.get("template_bucket")
    )
    source = text_value(row.get("source_file") or row.get("estimate_file") or row.get("source_sheet") or row.get("source_table"))
    quantity_parts = []
    for field, label_name in [
        ("quantity", "qty"),
        ("area_sqft", "sqft"),
        ("estimated_gallons", "gal"),
        ("estimated_sets", "sets"),
        ("estimated_cost", "cost"),
        ("extended_cost", "cost"),
    ]:
        value = row.get(field)
        if value not in (None, ""):
            quantity_parts.append(f"{label_name}={value}")
    row_ref = text_value(row.get("row_number") or row.get("source_row"))
    prefix = f"{row.get('source_table')}"
    if row_ref:
        prefix += f" row {row_ref}"
    details = "; ".join(part for part in [label, ", ".join(quantity_parts), source] if part)
    return f"{prefix}: {details}" if details else prefix


def attribute_job_search_response(results: list[dict[str, Any]], attribute_query: dict[str, Any], *, limit: int = 10) -> str:
    concepts = [text_value(concept) for concept in attribute_query.get("concepts", []) if text_value(concept)]
    if not results:
        filters = []
        if attribute_query.get("division"):
            filters.append(f"division={attribute_query['division']}")
        if attribute_query.get("year"):
            filters.append(f"year={attribute_query['year']}")
        if attribute_query.get("warranty_years"):
            filters.append(f"warranty={attribute_query['warranty_years']}-year")
        if attribute_query.get("substrates"):
            filters.append("substrate=" + ", ".join(attribute_query["substrates"]))
        if attribute_query.get("systems"):
            filters.append("system=" + ", ".join(attribute_query["systems"]))
        if attribute_query.get("sqft_filter"):
            filters.append("sqft=" + json.dumps(attribute_query["sqft_filter"], default=str))
        filter_text = f" with {'; '.join(filters)}" if filters else ""
        return (
            "I could not find historical estimate rows that match all requested attributes"
            f"{filter_text}: {', '.join(concepts) or 'none detected'}."
        )
    shown = results[:limit]
    filter_parts = []
    if attribute_query.get("division"):
        filter_parts.append(f"division={attribute_query['division']}")
    if attribute_query.get("warranty_years"):
        filter_parts.append(f"{attribute_query['warranty_years']}-year warranty")
    if attribute_query.get("substrates"):
        filter_parts.append("substrate " + ", ".join(attribute_query["substrates"]))
    if attribute_query.get("systems"):
        filter_parts.append("system " + ", ".join(attribute_query["systems"]))
    if attribute_query.get("sqft_filter"):
        filter_parts.append("sqft " + json.dumps(attribute_query["sqft_filter"], default=str))
    lines = [
        f"Found {len(results):,} job{'s' if len(results) != 1 else ''} with historical estimate evidence for **{', '.join(concepts)}**.",
        "",
    ]
    if filter_parts:
        lines.insert(1, "Filters applied: " + "; ".join(filter_parts))
        lines.insert(2, "")
    for index, job in enumerate(shown, start=1):
        title = text_value(job.get("job_name")) or text_value(job.get("customer")) or text_value(job.get("job_id"))
        if text_value(job.get("folder_url")):
            title = markdown_link(title, text_value(job.get("folder_url")))
        meta = " · ".join(
            part
            for part in [
                text_value(job.get("customer")) if text_value(job.get("customer")) != title else "",
                text_value(job.get("division")),
                text_value(job.get("pipeline_status") or job.get("status")),
                _format_attribute_money(job.get("final_price") or job.get("worksheet_price") or job.get("total_job_cost")),
            ]
            if part
        )
        lines.append(f"{index}. **{title}**")
        if meta:
            lines.append(f"   {meta}")
        lines.append(f"   Match: {text_value(job.get('match_reason'))}")
        context = []
        sqft = _attribute_best_sqft(job)
        warranty_years = _attribute_best_warranty_years(job)
        if sqft is not None:
            context.append(f"sqft={sqft:,.0f}")
        if warranty_years is not None:
            context.append(f"warranty={warranty_years:g}-year")
        if text_value(job.get("document_substrate")):
            context.append(f"substrate={text_value(job.get('document_substrate'))}")
        if text_value(job.get("document_material_system")):
            context.append(f"system={text_value(job.get('document_material_system'))}")
        if context:
            lines.append("   Context: " + "; ".join(context))
        evidence_by_concept = job.get("match_evidence") if isinstance(job.get("match_evidence"), dict) else {}
        for concept in concepts:
            evidence_rows = evidence_by_concept.get(concept) or []
            if not evidence_rows:
                continue
            lines.append(f"   - {concept}: {_attribute_evidence_label(evidence_rows[0])}")
            if len(evidence_rows) > 1:
                lines.append(f"     plus {len(evidence_rows) - 1} more {concept} row{'s' if len(evidence_rows) != 2 else ''}")
        lines.append("")
    if len(results) > len(shown):
        lines.append(f"Showing {len(shown)}. Add a year, customer, warranty, substrate, or size filter to narrow the list.")
    return "\n".join(lines).strip()


def build_structured_evidence_pack(
    connection: Any,
    *,
    query: str,
    interpreted: dict[str, Any],
    job_ids: list[str] | None = None,
    max_rows: int = 12,
    targets: Iterable[str] | None = None,
) -> dict[str, Any]:
    tokens = tokenize_search_text(text_value(interpreted.get("search_text")) or query)
    clean_job_ids = list(dict.fromkeys(job_id for job_id in (job_ids or []) if text_value(job_id)))[:20]
    target_set = set(targets or ASK_SPRAYTEC_STRUCTURED_TARGETS)
    evidence: dict[str, Any] = {
        "query": query,
        "search_text": text_value(interpreted.get("search_text")),
        "tokens": tokens,
        "job_ids": clean_job_ids,
        "targets": sorted(target_set),
        "facts": {},
        "skipped_sources": [],
    }

    def wants(source_name: str) -> bool:
        return source_name in target_set

    def add_job_filter(sql_where: list[str], params: dict[str, Any]) -> Any:
        if clean_job_ids:
            sql_where.append("job_id IN :job_ids")
            params["job_ids"] = clean_job_ids
            return bindparam("job_ids", expanding=True)
        return None

    job_columns = _connection_table_columns(connection, "jobs")
    if wants("jobs") and job_columns:
        selected = _select_columns(
            job_columns,
            [
                "job_id",
                "division",
                "pipeline_status",
                "status",
                "customer",
                "job_name",
                "job_type",
                "site_address",
                "city",
                "state",
                "estimated_sqft",
                "material_subtotal",
                "labor_subtotal",
                "total_job_cost",
                "final_price",
                "price_per_sqft",
                "has_signed_contract",
                "has_invoice",
                "has_warranty",
                "has_proposal",
                "has_job_spec",
                "folder_url",
                "estimate_file",
                "warnings",
                "source_year",
            ],
        )
        where: list[str] = []
        params: dict[str, Any] = {"limit": max_rows}
        bind = add_job_filter(where, params)
        if not clean_job_ids and tokens:
            token_clauses = []
            for index, token_value in enumerate(tokens[:4]):
                key = f"token_{index}"
                params[key] = f"%{token_value}%"
                token_clauses.append(
                    f"(LOWER(COALESCE(customer, '')) LIKE :{key} OR LOWER(COALESCE(job_name, '')) LIKE :{key} OR LOWER(COALESCE(folder_name, '')) LIKE :{key})"
                )
            where.extend(token_clauses)
        if selected:
            statement = text(
                f"SELECT {', '.join(selected)} FROM jobs"
                + (" WHERE " + " AND ".join(where) if where else "")
                + " ORDER BY updated_at DESC NULLS LAST LIMIT :limit"
            )
            if bind is not None:
                statement = statement.bindparams(bind)
            evidence["facts"]["jobs"] = _query_rows(connection, statement, params)
    elif wants("jobs"):
        evidence["skipped_sources"].append("jobs table unavailable")

    estimates_columns = _connection_table_columns(connection, "estimates")
    if wants("estimates") and estimates_columns:
        selected = _select_columns(
            estimates_columns,
            [
                "estimate_id",
                "job_id",
                "estimate_file",
                "estimate_role",
                "estimate_scope_type",
                "division",
                "pipeline_status",
                "customer",
                "job_name",
                "job_type",
                "estimated_sqft",
                "material_subtotal",
                "labor_subtotal",
                "equipment_subtotal",
                "travel_lodging",
                "total_job_cost",
                "overhead_pct",
                "profit_pct",
                "worksheet_price",
                "final_price",
                "price_per_sqft",
                "estimated_duration_days",
                "estimated_labor_hours",
                "estimated_crew_size",
                "adders_subtotal",
                "warranty_amount",
                "source_path",
                "extraction_warnings",
            ],
        )
        if selected:
            where = []
            params = {"limit": max_rows}
            bind = add_job_filter(where, params)
            statement = text(
                f"SELECT {', '.join(selected)} FROM estimates"
                + (" WHERE " + " AND ".join(where) if where else "")
                + " ORDER BY updated_at DESC NULLS LAST LIMIT :limit"
            )
            if bind is not None:
                statement = statement.bindparams(bind)
            evidence["facts"]["estimates"] = _query_rows(connection, statement, params)
    elif wants("estimates"):
        evidence["skipped_sources"].append("estimates table unavailable")

    line_item_columns = _connection_table_columns(connection, "estimate_line_items")
    if wants("estimate_line_items") and line_item_columns and clean_job_ids:
        selected = _select_columns(
            line_item_columns,
            [
                "job_id",
                "estimate_file",
                "section",
                "line_item_category",
                "line_item_name",
                "description",
                "quantity",
                "unit",
                "unit_cost",
                "unit_price",
                "extended_cost",
                "labor_days",
                "crew_size",
                "labor_hours",
                "vendor",
                "notes",
                "source_sheet",
                "source_row",
            ],
        )
        if selected:
            statement = text(
                f"""
                SELECT {', '.join(selected)}
                FROM estimate_line_items
                WHERE job_id IN :job_ids
                ORDER BY ABS(COALESCE(extended_cost, 0)) DESC NULLS LAST
                LIMIT :limit
                """
            ).bindparams(bindparam("job_ids", expanding=True))
            evidence["facts"]["estimate_line_items"] = _query_rows(
                connection,
                statement,
                {"job_ids": clean_job_ids, "limit": max_rows},
            )

    template_columns = _connection_table_columns(connection, "estimate_template_rows")
    if wants("estimate_template_rows") and template_columns and clean_job_ids:
        selected = _select_columns(
            template_columns,
            [
                "job_id",
                "source_file",
                "template_type",
                "template_bucket",
                "template_section",
                "line_item_kind",
                "row_number",
                "row_label",
                "selected_item_name",
                "quantity",
                "unit_price",
                "estimated_units",
                "estimated_cost",
                "area_sqft",
                "thickness_inches",
                "yield_or_coverage",
                "estimated_sets",
                "gal_per_100_sqft",
                "linear_ft",
                "days",
                "crew_size",
                "total_hours",
                "daily_rate",
                "hourly_rate",
                "formula_model",
                "warranty_years",
                "overhead_pct",
                "profit_pct",
                "needs_review",
            ],
        )
        if selected:
            template_cost_expr = "COALESCE(estimated_cost, calculated_cost, 0)" if "calculated_cost" in template_columns else "COALESCE(estimated_cost, 0)"
            statement = text(
                f"""
                SELECT {', '.join(selected)}
                FROM estimate_template_rows
                WHERE job_id IN :job_ids
                  AND COALESCE(template_section, '') NOT IN ('job_header', 'totals')
                ORDER BY ABS({template_cost_expr}) DESC NULLS LAST, row_number
                LIMIT :limit
                """
            ).bindparams(bindparam("job_ids", expanding=True))
            evidence["facts"]["estimate_template_rows"] = _query_rows(
                connection,
                statement,
                {"job_ids": clean_job_ids, "limit": max_rows * 2},
            )

    pricing_columns = _connection_table_columns(connection, "pricing_catalog")
    if wants("pricing_catalog") and pricing_columns and tokens:
        selected = _select_columns(
            pricing_columns,
            [
                "pricing_item_id",
                "vendor",
                "category",
                "product_name",
                "description",
                "unit_price",
                "unit_of_measure",
                "package_size",
                "price_basis",
                "price_per_gallon",
                "price_per_sqft",
                "price_per_unit",
                "effective_date",
                "status",
                "is_current",
                "needs_review",
                "notes",
            ],
        )
        if selected:
            where = ["COALESCE(is_current, false) IS TRUE"]
            params = {"limit": 8}
            for index, token_value in enumerate(tokens[:4]):
                key = f"pricing_token_{index}"
                params[key] = f"%{token_value}%"
                where.append(
                    f"(LOWER(COALESCE(product_name, '')) LIKE :{key} OR LOWER(COALESCE(description, '')) LIKE :{key} OR LOWER(COALESCE(vendor, '')) LIKE :{key} OR LOWER(COALESCE(category, '')) LIKE :{key})"
                )
            statement = text(
                f"SELECT {', '.join(selected)} FROM pricing_catalog WHERE {' AND '.join(where)} ORDER BY product_name LIMIT :limit"
            )
            evidence["facts"]["pricing_catalog"] = _query_rows(connection, statement, params)

    product_columns = _connection_table_columns(connection, "product_catalog")
    if wants("product_catalog") and product_columns and tokens:
        selected = _select_columns(
            product_columns,
            ["product_id", "manufacturer", "product_family", "product_name", "sku", "category", "subcategory", "unit", "active"],
        )
        if selected:
            where = ["COALESCE(active, true) IS TRUE"] if "active" in product_columns else ["1 = 1"]
            params = {"limit": 8}
            for index, token_value in enumerate(tokens[:4]):
                key = f"product_token_{index}"
                params[key] = f"%{token_value}%"
                where.append(
                    f"(LOWER(COALESCE(product_name, '')) LIKE :{key} OR LOWER(COALESCE(product_family, '')) LIKE :{key} OR LOWER(COALESCE(manufacturer, '')) LIKE :{key} OR LOWER(COALESCE(category, '')) LIKE :{key})"
                )
            products = _query_rows(
                connection,
                text(f"SELECT {', '.join(selected)} FROM product_catalog WHERE {' AND '.join(where)} ORDER BY product_name LIMIT :limit"),
                params,
            )
            evidence["facts"]["product_catalog"] = products
            product_ids = [text_value(row.get("product_id")) for row in products if text_value(row.get("product_id"))]
            if product_ids and _connection_table_columns(connection, "product_properties"):
                evidence["facts"]["product_properties"] = _query_rows(
                    connection,
                    text(
                        """
                        SELECT product_id, property_name, property_value, numeric_value, numeric_min, numeric_max, unit, confidence
                        FROM product_properties
                        WHERE product_id IN :product_ids
                        ORDER BY product_id, property_name
                        LIMIT :limit
                        """
                    ).bindparams(bindparam("product_ids", expanding=True)),
                    {"product_ids": product_ids, "limit": 20},
                )
            if product_ids and _connection_table_columns(connection, "product_rules"):
                evidence["facts"]["product_rules"] = _query_rows(
                    connection,
                    text(
                        """
                        SELECT product_id, rule_type, rule_value, severity, confidence
                        FROM product_rules
                        WHERE product_id IN :product_ids
                        ORDER BY product_id, rule_type
                        LIMIT :limit
                        """
                    ).bindparams(bindparam("product_ids", expanding=True)),
                    {"product_ids": product_ids, "limit": 20},
                )

    schedule_columns = _connection_table_columns(connection, "crew_schedule")
    if wants("crew_schedule") and schedule_columns:
        selected = _select_columns(
            schedule_columns,
            [
                "schedule_id",
                "job_id",
                "assigned_crew_leader",
                "suggested_crew_type",
                "suggested_crew_reason",
                "scheduled_sequence",
                "estimated_start_date",
                "estimated_duration_days",
                "estimated_end_date",
                "schedule_status",
                "ready_to_schedule",
                "blocking_issue",
                "priority",
                "schedule_notes",
                "updated_by",
                "updated_at",
            ],
        )
        if selected:
            where = []
            params = {"limit": max_rows}
            bind = add_job_filter(where, params)
            statement = text(
                f"SELECT {', '.join(selected)} FROM crew_schedule"
                + (" WHERE " + " AND ".join(where) if where else "")
                + " ORDER BY estimated_start_date NULLS LAST, scheduled_sequence NULLS LAST, updated_at DESC NULLS LAST LIMIT :limit"
            )
            if bind is not None:
                statement = statement.bindparams(bind)
            evidence["facts"]["crew_schedule"] = _query_rows(connection, statement, params)
    elif wants("crew_schedule"):
        evidence["skipped_sources"].append("crew_schedule table unavailable")

    evidence["facts"] = {key: value for key, value in evidence["facts"].items() if value}
    return evidence


def structured_evidence_lines(evidence: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    facts = evidence.get("facts") if isinstance(evidence.get("facts"), dict) else {}
    for source_name, rows in facts.items():
        if not rows:
            continue
        lines.append(f"**{source_name}**")
        for row in rows[:5]:
            compact = {key: value for key, value in row.items() if value not in (None, "", [], {})}
            lines.append(f"- {json.dumps(compact, default=str)[:500]}")
    return lines


def fallback_document_answer(prompt: str, chunks: list[dict[str, Any]], structured_evidence: dict[str, Any] | None = None) -> str:
    evidence_lines = structured_evidence_lines(structured_evidence or {})
    if not chunks and not evidence_lines:
        return "I found indexed records, but no extracted text chunks or structured evidence are available to summarize yet."
    lines = [
        "AI summarization is not available in this runtime. Here is the retrieved evidence:",
        "",
    ]
    if chunks:
        lines.append("Document excerpts:")
        lines.extend(source_lines_for_document_chunks(chunks[:8]))
    if evidence_lines:
        lines.append("")
        lines.append("Structured evidence:")
        lines.extend(evidence_lines[:25])
    return "\n".join(lines)


def llm_grounded_document_answer(prompt: str, chunks: list[dict[str, Any]], structured_evidence: dict[str, Any] | None = None) -> str:
    structured_evidence = structured_evidence or {}
    if not chunks and not structured_evidence.get("facts"):
        return ""
    if not os.getenv("OPENAI_API_KEY"):
        return fallback_document_answer(prompt, chunks, structured_evidence)
    try:
        from openai import OpenAI  # type: ignore
    except ImportError:
        return fallback_document_answer(prompt, chunks, structured_evidence)
    sources = []
    for index, chunk in enumerate(chunks, start=1):
        sources.append(
            {
                "source_id": f"S{index}",
                "label": source_label_for_chunk(chunk, index),
                "job_id": text_value(chunk.get("job_id")),
                "document_type": text_value(chunk.get("document_type")),
                "file_name": text_value(chunk.get("file_name")),
                "url": text_value(chunk.get("sharepoint_url")),
                "text": text_value(chunk.get("text_content")),
            }
        )
    system = (
        "You are Ask Spray-Tec, a grounded assistant for Spray-Tec operational data. "
        "Answer only from the provided extracted document sources and structured database evidence. Cite document claims with source ids like [S1]. "
        "For structured database facts, name the source table such as jobs, estimates, estimate_template_rows, pricing_catalog, or product_catalog. "
        "If the sources do not answer the question, say what is missing. Do not invent facts, totals, dates, warranty terms, or scope."
    )
    user_payload = {
        "question": prompt,
        "sources": sources,
        "structured_evidence": structured_evidence,
        "instructions": [
            "Start with a concise answer.",
            "Use bullets when useful.",
            "Include a Sources section listing the source ids used.",
            "Include a Data checked line naming structured tables used when relevant.",
            "Mention uncertainty or missing documents when the evidence is weak.",
        ],
    }
    try:
        client = OpenAI(timeout=float(os.getenv("OPENAI_ASK_SPRAYTEC_TIMEOUT_SECONDS", "30")))
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_ASK_SPRAYTEC_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-4o-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user_payload, indent=2, default=str)},
            ],
            temperature=0.1,
        )
        content = response.choices[0].message.content if response.choices else ""
        return text_value(content) or fallback_document_answer(prompt, chunks, structured_evidence)
    except Exception as exc:
        logger.exception("Ask Spray-Tec document answer failed")
        return fallback_document_answer(prompt, chunks, structured_evidence) + f"\n\nAI summary failed: {safe_exception_text(exc)}"


def concise_job_candidates_response(results: list[dict[str, Any]], interpreted: dict[str, Any]) -> str:
    if not results:
        return "I could not find a confident job or document match. Try a customer name, job name, city, year, or document type."
    strong = [result for result in results if float(result.get("match_score") or 0) >= 45]
    display = (strong or results)[:3]
    intro = (
        f"I found {len(strong):,} possible job match{'es' if len(strong) != 1 else ''}:"
        if strong
        else "I did not find a confident match. The closest job candidates are:"
    )
    chunks = [intro, ""]
    for index, job in enumerate(display, start=1):
        chunks.append(f"{index}. " + job_result_markdown(job, interpreted, include_documents=True, connection=None))
        chunks.append("")
    if not strong:
        chunks.append("I’m not showing broader weak matches by default. Add more detail or ask to broaden the search.")
    return "\n".join(chunks).strip()


def ask_spraytec_page() -> None:
    st.title("Ask Spray-Tec")
    st.caption("Conversational job and document finder. This searches structured job data, stored document links, and any extracted document text.")

    if "ask_spraytec_messages" not in st.session_state:
        st.session_state["ask_spraytec_messages"] = [
            {
                "role": "assistant",
                "content": "Ask me to find a job or document, like “Find the Mudd furniture job” or “Show me the Canadian Solar estimate.”",
            }
        ]
    selected_job = st.session_state.get("ask_spraytec_selected_job")
    selected_job_id = st.session_state.get("ask_spraytec_selected_job_id")
    if selected_job_id:
        st.caption(f"Selected job_id: {selected_job_id}")
        if st.button("Clear selected job", key="ask_spraytec_clear_selected"):
            st.session_state.pop("ask_spraytec_selected_job", None)
            st.session_state.pop("ask_spraytec_selected_job_id", None)
            st.rerun()
        with st.expander("Indexed document content"):
            try:
                with get_engine().connect() as conn:
                    preview_rows = search_extracted_text(conn, "", job_id=str(selected_job_id), limit=25)
            except Exception as exc:
                st.info(f"Document content preview is not available yet: {exc}")
                preview_rows = []
            if preview_rows:
                st.dataframe(
                    pd.DataFrame(preview_rows)[
                        [
                            column
                            for column in [
                                "file_name",
                                "document_type",
                                "source_locator",
                                "page_number",
                                "sheet_name",
                                "row_number",
                                "excerpt",
                                "sharepoint_url",
                            ]
                            if column in preview_rows[0]
                        ]
                    ],
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.caption("No extracted document content is indexed for this job yet.")

    for message in st.session_state["ask_spraytec_messages"]:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    prompt = st.chat_input("Find a job or document")
    if not prompt:
        return

    st.session_state["ask_spraytec_messages"].append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    pending_generated_notes = st.session_state.get(ASK_SPRAYTEC_PENDING_GENERATED_FIELD_NOTES_KEY)
    option_index = ask_spraytec_option_selection_index(prompt)
    if isinstance(pending_generated_notes, dict) and option_index is not None:
        pending_candidates = pending_generated_notes.get("candidates") if isinstance(pending_generated_notes.get("candidates"), list) else []
        debug_payload: dict[str, Any] = {
            "interpreted": {"option_selection": option_index + 1},
            "query_plan": {"mode": "generated_field_notes_option_selection"},
            "ranked_matches": [],
        }
        if 0 <= option_index < len(pending_candidates):
            with st.spinner("Generating field notes from selected historical proposal scope..."):
                generated_case = finalize_generated_field_notes_candidate(
                    pending_candidates[option_index],
                    candidates=pending_candidates,
                )
            thread_id = attach_generated_field_notes_case_to_estimator_context(generated_case)
            generated_case["estimator_chat_thread_id"] = thread_id
            st.session_state.pop(ASK_SPRAYTEC_PENDING_GENERATED_FIELD_NOTES_KEY, None)
            response = generated_field_notes_response(generated_case)
            debug_payload["generated_field_notes"] = {
                "status": generated_case.get("status"),
                "selected_option": option_index + 1,
                "query": generated_case.get("query") or pending_generated_notes.get("query"),
                "selected_job_id": generated_case.get("job_id"),
                "source_file": generated_case.get("source_file"),
                "proposal_file_name": generated_case.get("proposal_file_name"),
                "answer_key_summary": generated_case.get("answer_key_summary"),
                "candidate_count": len(pending_candidates),
            }
        else:
            response = f"I only have {len(pending_candidates)} generated-notes option(s) from the last search. Choose one of those options or ask a new field-notes question."
            debug_payload["generated_field_notes"] = {
                "status": "invalid_option",
                "selected_option": option_index + 1,
                "candidate_count": len(pending_candidates),
            }
        st.session_state["ask_spraytec_messages"].append({"role": "assistant", "content": response})
        with st.chat_message("assistant"):
            st.markdown(response)
            with st.expander("Search details"):
                st.write("query plan", debug_payload.get("query_plan"))
                st.write("generated field notes", debug_payload.get("generated_field_notes"))
        return

    interpreted = interpret_search_request(prompt)
    query_plan = plan_ask_spraytec_query(prompt, interpreted)
    plan_targets = set(query_plan.get("targets") or [])
    debug_payload: dict[str, Any] = {"interpreted": interpreted, "query_plan": query_plan, "ranked_matches": []}
    response = ""

    if query_plan.get("mode") == "generated_field_notes":
        if query_plan.get("needs_clarification"):
            response = text_value(query_plan.get("clarification")) or "Which job should I use?"
        else:
            with st.spinner("Generating field notes from historical proposal scope..."):
                estimator_data = load_estimator_data_for_ui("full")
                generated_case = build_generated_field_notes_case_from_history(estimator_data, prompt)
            if generated_case.get("status") == "selected":
                thread_id = attach_generated_field_notes_case_to_estimator_context(generated_case)
                generated_case["estimator_chat_thread_id"] = thread_id
                st.session_state.pop(ASK_SPRAYTEC_PENDING_GENERATED_FIELD_NOTES_KEY, None)
            elif generated_case.get("status") == "ambiguous" and generated_case.get("candidates"):
                st.session_state[ASK_SPRAYTEC_PENDING_GENERATED_FIELD_NOTES_KEY] = generated_case
            response = generated_field_notes_response(generated_case)
            debug_payload["generated_field_notes"] = {
                "status": generated_case.get("status"),
                "query": generated_case.get("query"),
                "selected_job_id": generated_case.get("job_id"),
                "source_file": generated_case.get("source_file"),
                "proposal_file_name": generated_case.get("proposal_file_name"),
                "answer_key_summary": generated_case.get("answer_key_summary"),
                "candidate_count": len(generated_case.get("candidates") or []),
            }
        st.session_state["ask_spraytec_messages"].append({"role": "assistant", "content": response})
        with st.chat_message("assistant"):
            st.markdown(response)
            with st.expander("Search details"):
                st.write("query plan", debug_payload.get("query_plan"))
                st.write("generated field notes", debug_payload.get("generated_field_notes"))
        return

    if query_plan.get("needs_clarification") and not (selected_job or selected_job_id):
        response = text_value(query_plan.get("clarification")) or "What should I search for?"
        st.session_state["ask_spraytec_messages"].append({"role": "assistant", "content": response})
        with st.chat_message("assistant"):
            st.markdown(response)
            with st.expander("Search details"):
                st.write("query plan", query_plan)
                st.write("interpreted search text", interpreted.get("search_text"))
                st.write("detected document type", interpreted.get("document_type"))
        return

    if query_plan.get("mode") == "attribute_job_search":
        attribute_query = query_plan.get("attribute_query") if isinstance(query_plan.get("attribute_query"), dict) else {}
        try:
            with get_engine().connect() as conn:
                attribute_results = search_jobs_by_estimate_attributes(
                    conn,
                    concepts=list(attribute_query.get("concepts") or []),
                    interpreted=interpreted,
                    attribute_query=attribute_query,
                    limit=20,
                )
                for result in attribute_results[:10]:
                    result["_documents"] = get_preferred_job_documents(conn, result, interpreted.get("document_type"))
        except Exception as exc:
            show_database_error(exc)
            return
        response = attribute_job_search_response(attribute_results, attribute_query)
        debug_payload["attribute_results"] = [
            {
                "job_id": result.get("job_id"),
                "customer": result.get("customer"),
                "job_name": result.get("job_name"),
                "matched_concepts": result.get("matched_concepts"),
                "evidence_count": result.get("match_evidence_count"),
                "score": result.get("match_score"),
            }
            for result in attribute_results[:25]
        ]
        if attribute_results:
            first_result = attribute_results[0]
            st.session_state["ask_spraytec_selected_job"] = first_result
            st.session_state["ask_spraytec_selected_job_id"] = str(first_result.get("job_id") or "")
    elif interpreted.get("is_follow_up") and (selected_job or selected_job_id):
        requested_type = interpreted.get("document_type")
        active_job = selected_job if isinstance(selected_job, dict) else {"job_id": selected_job_id}
        try:
            with get_engine().connect() as conn:
                docs = get_preferred_job_documents(conn, active_job, requested_type) if "documents" in plan_targets else []
                document_chunks = []
                if "document_content" in plan_targets:
                    document_chunks = fetch_document_content_chunks(
                        conn,
                        query=prompt,
                        job_id=str(selected_job_id or active_job.get("job_id") or ""),
                        document_type=requested_type,
                        limit=ASK_DOCUMENT_CHUNK_LIMIT,
                    )
                structured_evidence = build_structured_evidence_pack(
                    conn,
                    query=prompt,
                    interpreted=interpreted,
                    job_ids=[str(selected_job_id or active_job.get("job_id") or "")],
                    targets=plan_targets & ASK_SPRAYTEC_STRUCTURED_TARGETS,
                )
        except Exception as exc:
            show_database_error(exc)
            return
        active_job_label = text_value(active_job.get("job_name")) or text_value(active_job.get("customer")) or text_value(active_job.get("job_id"))
        response = f"Using selected job: **{active_job_label}**\n\n"
        if document_chunks or structured_evidence.get("facts"):
            response += llm_grounded_document_answer(prompt, document_chunks, structured_evidence) + "\n\n"
        if "documents" in plan_targets and requested_type not in (None, "all") and not any(doc.get("type") == requested_type for doc in docs):
            response += f"{requested_document_label(requested_type)}: not indexed\n\n"
        if docs:
            response += "Indexed document links:\n"
            response += "\n".join(
                f"- {doc['label']}: {markdown_link(text_value(doc.get('file_name')) or 'Open ' + doc['label'].lower(), doc['url'])}"
                for doc in docs
            )
        elif "documents" in plan_targets:
            response += "I do not see any indexed document links for the selected job."
        debug_payload["ranked_matches"] = [{"job_id": active_job.get("job_id"), "score": active_job.get("match_score"), "reason": active_job.get("match_reason")}]
        debug_payload["document_chunks"] = [
            {
                "source": source_label_for_chunk(chunk, index),
                "document_id": chunk.get("document_id"),
                "job_id": chunk.get("job_id"),
                "file_name": chunk.get("file_name"),
            }
            for index, chunk in enumerate(document_chunks[:10], start=1)
        ]
        debug_payload["structured_evidence"] = {
            key: len(value)
            for key, value in (structured_evidence.get("facts") or {}).items()
        }
    else:
        try:
            with get_engine().connect() as conn:
                document_matches: list[dict[str, Any]] = []
                document_chunks: list[dict[str, Any]] = []
                structured_evidence: dict[str, Any] = {}
                if "documents" in plan_targets and interpreted.get("search_text"):
                    document_matches = search_documents(
                        conn,
                        str(interpreted.get("search_text") or ""),
                        document_type=interpreted.get("document_type"),
                        limit=50,
                    )
                    if document_matches and "document_content" in plan_targets:
                        document_chunks = fetch_document_content_chunks(
                            conn,
                            query=prompt,
                            document_ids=[text_value(doc.get("document_id")) for doc in document_matches],
                            document_type=interpreted.get("document_type"),
                            limit=ASK_DOCUMENT_CHUNK_LIMIT,
                        )
                matched_job_ids = [text_value(doc.get("job_id")) for doc in document_matches if text_value(doc.get("job_id"))]
                results = [] if document_matches or "jobs" not in plan_targets else search_jobs(conn, prompt, limit=10)
                for result in results:
                    result["_documents"] = get_preferred_job_documents(conn, result, interpreted.get("document_type"))
                if not matched_job_ids:
                    matched_job_ids = [text_value(result.get("job_id")) for result in results[:3] if text_value(result.get("job_id"))]
                structured_evidence = build_structured_evidence_pack(
                    conn,
                    query=prompt,
                    interpreted=interpreted,
                    job_ids=matched_job_ids,
                    targets=plan_targets & ASK_SPRAYTEC_STRUCTURED_TARGETS,
                )
        except Exception as exc:
            show_database_error(exc)
            return
        if "document_matches" in locals() and document_matches:
            if document_chunks:
                response = llm_grounded_document_answer(prompt, document_chunks, structured_evidence)
                response += "\n\nIndexed document links:\n"
                response += "\n".join(indexed_document_markdown(doc) for doc in document_matches[:12])
            else:
                summary = llm_grounded_document_answer(prompt, [], structured_evidence)
                response = summary + "\n\n" if summary else ""
                response += indexed_documents_response(document_matches, interpreted=interpreted, query=prompt)
                response += "\n\nI found matching document metadata, but no extracted text chunks were available to summarize."
            debug_payload["document_matches"] = [
                {
                    "document_id": doc.get("document_id"),
                    "job_id": doc.get("job_id"),
                    "document_type": doc.get("document_type"),
                    "file_name": doc.get("file_name"),
                    "folder_path": doc.get("folder_path"),
                }
                for doc in document_matches[:25]
            ]
            debug_payload["document_chunks"] = [
                {
                    "source": source_label_for_chunk(chunk, index),
                    "document_id": chunk.get("document_id"),
                    "job_id": chunk.get("job_id"),
                    "file_name": chunk.get("file_name"),
                }
                for index, chunk in enumerate(document_chunks[:10], start=1)
            ]
            debug_payload["structured_evidence"] = {
                key: len(value)
                for key, value in (structured_evidence.get("facts") or {}).items()
            }
            job_ids = [text_value(doc.get("job_id")) for doc in document_matches if text_value(doc.get("job_id"))]
            if job_ids:
                st.session_state["ask_spraytec_selected_job_id"] = job_ids[0]
                st.session_state.pop("ask_spraytec_selected_job", None)
        else:
            if is_document_lookup_request(interpreted) and not interpreted.get("search_text"):
                response = "Which job, customer, or project should I search documents for?"
            else:
                debug_payload["ranked_matches"] = [
                    {
                        "job_id": result.get("job_id"),
                        "customer": result.get("customer"),
                        "job_name": result.get("job_name"),
                        "score": result.get("match_score"),
                        "reason": result.get("match_reason"),
                    }
                    for result in results
                ]
                debug_payload["structured_evidence"] = {
                    key: len(value)
                    for key, value in (structured_evidence.get("facts") or {}).items()
                }
                strong_results = [result for result in results if float(result.get("match_score") or 0) >= 45]
                if query_plan.get("use_llm_answer") and structured_evidence.get("facts"):
                    response = llm_grounded_document_answer(prompt, [], structured_evidence)
                    related = strong_results or results[:3]
                    if related:
                        response += "\n\nRelated job matches:\n"
                        response += "\n\n".join(
                            f"{index}. " + job_result_markdown(job, interpreted, include_documents=True, connection=None)
                            for index, job in enumerate(related[:3], start=1)
                        )
                elif "jobs" not in plan_targets and query_plan.get("mode") == "structured_answer":
                    requested_sources = ", ".join(
                        target for target in query_plan.get("targets", []) if target in ASK_SPRAYTEC_STRUCTURED_TARGETS
                    )
                    response = (
                        "I did not find matching structured records for that question"
                        + (f" in {requested_sources}." if requested_sources else ".")
                        + " Try a more specific product, system, customer, job, or document name."
                    )
                elif len(strong_results) == 1 and float(strong_results[0].get("match_score") or 0) >= 75:
                    job = strong_results[0]
                    st.session_state["ask_spraytec_selected_job"] = job
                    st.session_state["ask_spraytec_selected_job_id"] = str(job.get("job_id") or "")
                    response = "I found a strong match.\n\n" + job_result_markdown(job, interpreted, connection=None)
                    alternatives = results[1:4]
                    if alternatives:
                        response += "\n\nLower-ranked alternatives are available in Search details."
                else:
                    response = concise_job_candidates_response(results, interpreted)
                display_results = strong_results or results[:3]
                if display_results:
                    job = display_results[0]
                    st.session_state["ask_spraytec_selected_job"] = job
                    st.session_state["ask_spraytec_selected_job_id"] = str(job.get("job_id") or "")
                if structured_evidence.get("facts") and not strong_results:
                    response += "\n\nI also checked structured data tables; details are in Search details."

            if is_document_lookup_request(interpreted) and interpreted.get("search_text") and not response.startswith("Which"):
                response = (
                    f"I did not find indexed {requested_document_label(interpreted.get('document_type')).lower()} "
                    f"documents matching **{text_value(interpreted.get('search_text'))}**.\n\n"
                    + response
                )

    st.session_state["ask_spraytec_messages"].append({"role": "assistant", "content": response})
    with st.chat_message("assistant"):
        st.markdown(response)
        with st.expander("Search details"):
            st.write("query plan", debug_payload.get("query_plan"))
            st.write("interpreted search text", interpreted.get("search_text"))
            st.write("detected document type", interpreted.get("document_type"))
            st.write(
                "detected filters",
                {
                    "division": interpreted.get("division"),
                    "status": interpreted.get("status"),
                    "city": interpreted.get("city"),
                    "state": interpreted.get("state"),
                },
            )
            if debug_payload.get("document_matches"):
                st.write("document matches", debug_payload["document_matches"])
            if debug_payload.get("document_chunks"):
                st.write("document chunks sent to answer model", debug_payload["document_chunks"])
            if debug_payload.get("structured_evidence"):
                st.write("structured evidence row counts", debug_payload["structured_evidence"])
            if debug_payload.get("attribute_results"):
                st.write("attribute job matches", debug_payload["attribute_results"])
            if debug_payload.get("ranked_matches"):
                st.write("ranked job matches", debug_payload["ranked_matches"])


JOB_BOARD_STATUS_ORDER = [
    "Lead Created",
    "Contacted",
    "Estimate In Progress",
    "Proposed",
    "Proposal Submitted",
    "Contracted",
    "Contracted Repairs",
    "Scheduled",
    "In Progress",
    "Completed",
    "Invoiced",
    "Folder Created",
    "Other",
]


JOB_WORKFLOW_PRIORITY_OPTIONS = ["Low", "Normal", "High", "Urgent"]


POSSIBLE_WORKFLOW_STATUS_COLS = [
    "workflow_status",
    "workflow_status_override",
]


POSSIBLE_PIPELINE_STATUS_COLS = [
    "pipeline_status",
    "pipeline_status_x",
    "pipeline_status_y",
]


POSSIBLE_STATUS_COLS = [
    "status",
    "status_x",
    "status_y",
]


JOB_BOARD_FIELDS = [
    "job_id",
    "customer",
    "job_name",
    "division",
    "pipeline_status",
    "status",
    "job_type",
    "project_type",
    "scope_type",
    "substrate",
    "roof_type",
    "existing_roof_type",
    "material_system",
    "product_system",
    "material_type",
    "site_address",
    "city",
    "state",
    "zip_code",
    "estimated_value",
    "total_job_cost",
    "final_price",
    "invoice_amount",
    "estimated_sqft",
    "price_per_sqft",
    "estimate_date",
    "completion_date",
    "date_of_completion",
    "completed_date",
    "estimator",
    "salesperson",
    "sales_person",
    "lead_source",
    "source",
    "referral_source",
    "marketing_source",
    "warranty_years",
    "warranty_type",
    "warranty_scope",
    "warranty_amount",
    "coating_type",
    "foam_type",
    "folder_url",
    "folder_path",
    "folder_link_or_path",
    "estimate_file",
    "proposal_file",
    "contract_file",
    "job_tracking_file",
    "has_proposal",
    "has_signed_contract",
    "has_invoice",
    "has_warranty",
    "has_job_spec",
    "has_aerial",
    "photo_count",
    "warnings",
    "last_scanned_at",
]


@st.cache_data(ttl=300, show_spinner=False)
def load_job_board_jobs() -> pd.DataFrame:
    cols = relation_columns("dashboard_jobs")
    if not cols:
        return query_view("dashboard_jobs")

    select_parts: list[str] = []
    for field in JOB_BOARD_FIELDS:
        if field == "folder_link_or_path":
            select_parts.append(
                f"{sql_coalesce([sql_nonblank_column('j', cols, 'folder_url'), sql_nonblank_column('j', cols, 'folder_path')])} AS folder_link_or_path"
            )
        elif field == "proposal_file":
            select_parts.append(
                f"{sql_coalesce([sql_column('j', cols, 'proposal_file'), sql_column('j', cols, 'estimate_file')])} AS proposal_file"
            )
        else:
            select_parts.append(f"{sql_column('j', cols, field)} AS {field}")

    return safe_load(f"SELECT {', '.join(select_parts)} FROM dashboard_jobs j")


@st.cache_data(ttl=300, show_spinner=False)
def load_job_board_document_dates() -> pd.DataFrame:
    document_cols = relation_columns("documents")
    if not {"job_id", "file_name"}.issubset(document_cols):
        return pd.DataFrame()
    drive_cols = relation_columns("sharepoint_drive_items")
    document_type_expr = sql_column("d", document_cols, "document_type", "''")
    file_name_expr = sql_column("d", document_cols, "file_name", "''")
    relative_path_expr = sql_column("d", document_cols, "relative_path", "NULL")
    modified_expr = sql_column("d", document_cols, "modified_at", "NULL")
    document_created_expr = sql_column("d", document_cols, "created_at", "NULL")

    join_sql = ""
    drive_created_expr = "NULL"
    drive_modified_expr = "NULL"
    modified_by_expr = "NULL"
    if {"drive_id", "drive_item_id"}.issubset(document_cols) and {"drive_id", "drive_item_id"}.issubset(drive_cols):
        join_sql = """
        LEFT JOIN sharepoint_drive_items s
          ON s.drive_id = d.drive_id
         AND s.drive_item_id = d.drive_item_id
        """
        if "metadata_json" in drive_cols:
            drive_created_expr = "NULLIF(s.metadata_json ->> 'createdDateTime', '')::timestamptz"
            modified_by_expr = """
            COALESCE(
                NULLIF(s.metadata_json #>> '{lastModifiedBy,user,displayName}', ''),
                NULLIF(s.metadata_json #>> '{lastModifiedBy,user,email}', ''),
                NULLIF(s.metadata_json #>> '{lastModifiedBy,application,displayName}', '')
            )
            """
        drive_modified_expr = sql_column("s", drive_cols, "last_modified_at", "NULL")

    created_expr = drive_created_expr
    updated_expr = sql_coalesce([drive_modified_expr, modified_expr])
    sql = f"""
        WITH typed_documents AS (
            SELECT
                d.job_id,
                CASE
                    WHEN LOWER(COALESCE({document_type_expr}, '')) LIKE '%proposal%'
                      OR LOWER(COALESCE({file_name_expr}, '')) LIKE '%proposal%'
                        THEN 'proposal'
                    WHEN LOWER(COALESCE({document_type_expr}, '')) LIKE '%estimate%'
                      OR LOWER(COALESCE({file_name_expr}, '')) LIKE '%estimate%'
                        THEN 'estimate'
                    ELSE NULL
                END AS document_kind,
                {file_name_expr} AS file_name,
                {relative_path_expr} AS relative_path,
                {created_expr} AS file_created_at,
                {updated_expr} AS file_modified_at,
                {modified_by_expr} AS file_modified_by
            FROM documents d
            {join_sql}
            WHERE d.job_id IS NOT NULL
              AND (
                LOWER(COALESCE({document_type_expr}, '')) LIKE '%proposal%'
                OR LOWER(COALESCE({file_name_expr}, '')) LIKE '%proposal%'
                OR LOWER(COALESCE({document_type_expr}, '')) LIKE '%estimate%'
                OR LOWER(COALESCE({file_name_expr}, '')) LIKE '%estimate%'
              )
        ),
        ranked_documents AS (
            SELECT
                *,
                ROW_NUMBER() OVER (
                    PARTITION BY job_id, document_kind
                    ORDER BY file_created_at DESC NULLS LAST, file_modified_at DESC NULLS LAST, file_name
                ) AS rn,
                COUNT(*) OVER (PARTITION BY job_id, document_kind) AS document_count
            FROM typed_documents
            WHERE document_kind IS NOT NULL
        )
        SELECT
            job_id,
            document_kind,
            file_name,
            relative_path,
            file_created_at,
            file_modified_at,
            file_modified_by,
            document_count
        FROM ranked_documents
        WHERE rn = 1
    """
    try:
        dates = safe_load(sql)
    except Exception:
        return pd.DataFrame()
    if dates.empty:
        return pd.DataFrame()
    pivot = dates.pivot(
        index="job_id",
        columns="document_kind",
        values=["file_created_at", "file_modified_at", "file_modified_by", "file_name", "relative_path", "document_count"],
    )
    pivot.columns = [f"{kind}_{field}" for field, kind in pivot.columns]
    return pivot.reset_index()


def add_job_board_proposal_stale_columns(jobs: pd.DataFrame) -> pd.DataFrame:
    out = jobs.copy()
    for column in ["proposal_file_created_at", "proposal_file_modified_at", "estimate_file_created_at", "estimate_file_modified_at"]:
        if column not in out.columns:
            out[column] = pd.NaT
    out["proposal_created_at"] = date_column_series(out, ["proposal_file_created_at"])
    out["estimate_created_at"] = date_column_series(out, ["estimate_file_created_at", "estimate_date"])
    out["proposal_modified_at"] = date_column_series(out, ["proposal_file_modified_at"])
    out["estimate_modified_at"] = date_column_series(out, ["estimate_file_modified_at"])
    out["proposal_modified_by"] = out.get("proposal_file_modified_by", pd.Series("", index=out.index)).fillna("").astype(str)
    out["estimate_modified_by"] = out.get("estimate_file_modified_by", pd.Series("", index=out.index)).fillna("").astype(str)
    out["proposal_date_for_stale"] = out["proposal_modified_at"].combine_first(out["proposal_created_at"])
    today = pd.Timestamp(date.today())
    out["proposal_age_days"] = (today.normalize() - out["proposal_date_for_stale"].dt.normalize()).dt.days
    out.loc[out["proposal_age_days"].isna() | (out["proposal_age_days"] < 0), "proposal_age_days"] = pd.NA
    out["proposal_stale"] = out["proposal_age_days"].fillna(0).astype(float) > 90
    out["proposal_status_flag"] = out["proposal_stale"].map({True: "Stale", False: "Current"})
    out.loc[out["proposal_date_for_stale"].isna(), "proposal_status_flag"] = "No proposal date"
    return out


@st.cache_data(ttl=300, show_spinner=False)
def load_job_board_vsimple_enrichment() -> pd.DataFrame:
    match_cols = relation_columns("vsimple_sharepoint_job_matches_accepted")
    project_cols = relation_columns("vsimple_projects")
    if not {"job_id", "vsimple_id"}.issubset(match_cols) or "vsimple_id" not in project_cols:
        return pd.DataFrame()
    select_parts = [
        "m.job_id",
        f"{sql_column('p', project_cols, 'project_type')} AS vsimple_project_type",
        f"{sql_column('p', project_cols, 'deal_type')} AS vsimple_deal_type",
        f"{sql_column('p', project_cols, 'lead_source')} AS vsimple_lead_source",
        f"{sql_column('p', project_cols, 'referral_source')} AS vsimple_referral_source",
        f"{sql_column('p', project_cols, 'deal_owner')} AS vsimple_deal_owner",
        f"{sql_column('p', project_cols, 'estimator_salesperson')} AS vsimple_estimator",
        f"{sql_column('p', project_cols, 'bid_amount')} AS vsimple_bid_amount",
        f"{sql_column('p', project_cols, 'billing_amount')} AS vsimple_billing_amount",
        f"{sql_column('p', project_cols, 'gross_profit')} AS vsimple_gross_profit",
        f"{sql_column('p', project_cols, 'all_costs')} AS vsimple_all_costs",
        f"{sql_column('p', project_cols, 'estimated_sqft')} AS vsimple_estimated_sqft",
        f"{sql_column('p', project_cols, 'roof_deck_sqft')} AS vsimple_roof_deck_sqft",
        f"{sql_column('p', project_cols, 'completion_date')} AS vsimple_completion_date",
        f"{sql_column('p', project_cols, 'closed_date')} AS vsimple_closed_date",
        f"{sql_column('p', project_cols, 'spray_tec_system')} AS vsimple_spray_tec_system",
        f"{sql_column('p', project_cols, 'roof_type')} AS vsimple_roof_type",
        f"{sql_column('p', project_cols, 'construction_type')} AS vsimple_construction_type",
        f"{sql_column('p', project_cols, 'building_use')} AS vsimple_building_use",
        f"{sql_column('p', project_cols, 'scope_summary')} AS vsimple_scope_summary",
    ]
    try:
        return safe_load(
            f"""
            SELECT {', '.join(select_parts)}
            FROM vsimple_sharepoint_job_matches_accepted m
            LEFT JOIN vsimple_projects p ON p.vsimple_id = m.vsimple_id
            WHERE m.job_id IS NOT NULL
            """
        ).drop_duplicates("job_id", keep="first")
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def load_job_board_template_enrichment() -> pd.DataFrame:
    cols = relation_columns("estimate_template_rows")
    if "job_id" not in cols:
        return pd.DataFrame()
    selected_item = sql_nonblank_column("t", cols, "selected_item_name", "NULL::TEXT")
    bucket = sql_column("t", cols, "template_bucket", "''")
    line_kind = sql_column("t", cols, "line_item_kind", "''")
    warranty_years = sql_column("t", cols, "warranty_years", "NULL::NUMERIC")
    try:
        return safe_load(
            f"""
            SELECT
                t.job_id,
                MAX({warranty_years}) AS template_warranty_years,
                STRING_AGG(DISTINCT {selected_item}, ', ' ORDER BY {selected_item})
                    FILTER (
                        WHERE {selected_item} IS NOT NULL
                          AND (
                            LOWER(COALESCE({bucket}, '')) IN (
                                'coating', 'foam', 'roofing_foam', 'thermal_barrier_coating',
                                'primer', 'fabric', 'caulk_sealant', 'membrane'
                            )
                            OR LOWER(COALESCE({line_kind}, '')) = 'material'
                          )
                    ) AS template_material_system
            FROM estimate_template_rows t
            WHERE t.job_id IS NOT NULL
            GROUP BY t.job_id
            """
        )
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def load_job_board_document_signal_enrichment() -> pd.DataFrame:
    cols = relation_columns("document_content")
    if not {"job_id", "text_content"}.issubset(cols):
        return pd.DataFrame()
    text_expr = f"LOWER(COALESCE({sql_column('d', cols, 'normalized_text')}, d.text_content, ''))"
    try:
        return safe_load(
            f"""
            WITH signals AS (
                SELECT
                    d.job_id,
                    CASE
                        WHEN {text_expr} LIKE '%metal roof%' OR {text_expr} LIKE '%metal panel%' OR {text_expr} LIKE '%standing seam%' THEN 'Metal'
                        WHEN {text_expr} LIKE '%epdm%' THEN 'EPDM'
                        WHEN {text_expr} LIKE '%tpo%' THEN 'TPO'
                        WHEN {text_expr} LIKE '%concrete%' THEN 'Concrete'
                        WHEN {text_expr} LIKE '%spray foam%' OR {text_expr} LIKE '%spf%' THEN 'SPF'
                        ELSE NULL
                    END AS substrate_signal,
                    CASE
                        WHEN {text_expr} LIKE '%silicone%' THEN 'Silicone'
                        WHEN {text_expr} LIKE '%acrylic%' THEN 'Acrylic'
                        WHEN {text_expr} LIKE '%open cell%' OR {text_expr} LIKE '%open-cell%' THEN 'Open-cell spray foam'
                        WHEN {text_expr} LIKE '%closed cell%' OR {text_expr} LIKE '%closed-cell%' THEN 'Closed-cell spray foam'
                        WHEN {text_expr} LIKE '%spray foam%' OR {text_expr} LIKE '%spf%' THEN 'Spray foam'
                        ELSE NULL
                    END AS material_signal,
                    CASE
                        WHEN {text_expr} LIKE '%gaco%warranty%' THEN 'Gaco'
                        WHEN {text_expr} LIKE '%spray-tec%warranty%' OR {text_expr} LIKE '%spray tec%warranty%' THEN 'Spray-Tec'
                        ELSE NULL
                    END AS warranty_type_signal,
                    NULLIF(SUBSTRING({text_expr} FROM '([0-9]{{1,2}})[ -]?year'), '')::NUMERIC AS warranty_year_signal
                FROM document_content d
                WHERE d.job_id IS NOT NULL
                  AND (
                    {text_expr} LIKE '%metal%'
                    OR {text_expr} LIKE '%tpo%'
                    OR {text_expr} LIKE '%epdm%'
                    OR {text_expr} LIKE '%concrete%'
                    OR {text_expr} LIKE '%silicone%'
                    OR {text_expr} LIKE '%acrylic%'
                    OR {text_expr} LIKE '%spray foam%'
                    OR {text_expr} LIKE '%closed cell%'
                    OR {text_expr} LIKE '%open cell%'
                    OR {text_expr} LIKE '%warranty%'
                  )
            )
            SELECT
                job_id,
                STRING_AGG(DISTINCT substrate_signal, ', ') FILTER (WHERE substrate_signal IS NOT NULL) AS document_substrate,
                STRING_AGG(DISTINCT material_signal, ', ') FILTER (WHERE material_signal IS NOT NULL) AS document_material_system,
                STRING_AGG(DISTINCT warranty_type_signal, ', ') FILTER (WHERE warranty_type_signal IS NOT NULL) AS document_warranty_type,
                MAX(warranty_year_signal) AS document_warranty_years
            FROM signals
            GROUP BY job_id
            """
        )
    except Exception:
        return pd.DataFrame()


def merge_job_board_enrichments(jobs: pd.DataFrame, *enrichments: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(jobs, pd.DataFrame) or jobs.empty or "job_id" not in jobs.columns:
        return jobs
    out = jobs.copy()
    for enrichment in enrichments:
        if not isinstance(enrichment, pd.DataFrame) or enrichment.empty or "job_id" not in enrichment.columns:
            continue
        out = out.merge(enrichment.drop_duplicates("job_id", keep="first"), on="job_id", how="left")

    text_targets = {
        "project_type": ["vsimple_project_type", "vsimple_deal_type"],
        "job_type": ["vsimple_deal_type", "vsimple_project_type"],
        "lead_source": ["vsimple_lead_source"],
        "referral_source": ["vsimple_referral_source"],
        "estimator": ["vsimple_estimator", "vsimple_deal_owner"],
        "salesperson": ["vsimple_deal_owner", "vsimple_estimator"],
        "substrate": ["vsimple_roof_type", "vsimple_construction_type", "document_substrate"],
        "roof_type": ["vsimple_roof_type", "document_substrate"],
        "building_type": ["vsimple_construction_type", "vsimple_building_use"],
        "material_system": ["vsimple_spray_tec_system", "template_material_system", "document_material_system"],
        "product_system": ["vsimple_spray_tec_system", "template_material_system", "document_material_system"],
        "warranty_type": ["document_warranty_type"],
        "completion_date": ["vsimple_completion_date", "vsimple_closed_date"],
    }
    numeric_targets = {
        "estimated_value": ["vsimple_bid_amount", "vsimple_billing_amount"],
        "final_price": ["vsimple_billing_amount"],
        "total_job_cost": ["vsimple_all_costs"],
        "estimated_sqft": ["vsimple_estimated_sqft", "vsimple_roof_deck_sqft"],
        "warranty_years": ["template_warranty_years", "document_warranty_years"],
        "estimated_duration_days": ["estimate_estimated_duration_days"],
        "estimated_labor_hours": ["estimate_estimated_labor_hours"],
        "estimated_crew_size": ["estimate_estimated_crew_size"],
        "labor_subtotal": ["estimate_labor_subtotal"],
    }
    for target, sources in text_targets.items():
        if target not in out.columns:
            out[target] = ""
        for source in sources:
            if source not in out.columns:
                continue
            target_text = out[target].fillna("").astype(str).str.strip()
            source_text = out[source].fillna("").astype(str).str.strip()
            mask = target_text.isin(["", "nan", "None", "null", "-"]) & ~source_text.isin(["", "nan", "None", "null", "-"])
            out.loc[mask, target] = out.loc[mask, source]
    for target, sources in numeric_targets.items():
        if target not in out.columns:
            out[target] = pd.NA
        target_num = pd.to_numeric(out[target], errors="coerce")
        for source in sources:
            if source not in out.columns:
                continue
            source_num = pd.to_numeric(out[source], errors="coerce")
            mask = (target_num.isna() | target_num.eq(0)) & source_num.notna() & source_num.ne(0)
            out.loc[mask, target] = source_num[mask]
            target_num = pd.to_numeric(out[target], errors="coerce")
    return out


@st.cache_data(ttl=300, show_spinner=False)
def load_job_board_estimate_labor_enrichment() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    cols = relation_columns("dashboard_estimates")
    fields = {
        "estimated_duration_days": "estimate_estimated_duration_days",
        "estimated_labor_hours": "estimate_estimated_labor_hours",
        "estimated_crew_size": "estimate_estimated_crew_size",
        "labor_subtotal": "estimate_labor_subtotal",
    }
    if "job_id" in cols:
        select_parts = ["e.job_id"]
        for source, alias in fields.items():
            select_parts.append(f"MAX({sql_column('e', cols, source, 'NULL')}) AS {alias}")
        try:
            frames.append(
                safe_load(
                    f"""
                    SELECT {', '.join(select_parts)}
                    FROM dashboard_estimates e
                    WHERE e.job_id IS NOT NULL
                    GROUP BY e.job_id
                    """
                )
            )
        except Exception:
            pass

    template_cols = relation_columns("estimate_template_rows")
    if {"job_id", "document_id"}.issubset(template_cols):
        document_join = ""
        modified_order = "TIMESTAMPTZ 'epoch'"
        if {"document_id", "modified_at"}.issubset(relation_columns("documents")):
            document_join = "LEFT JOIN documents d ON d.document_id = r.document_id"
            modified_order = "COALESCE(MAX(d.modified_at), TIMESTAMPTZ 'epoch')"
        labor_filter_parts = []
        if "template_section" in template_cols:
            labor_filter_parts.append("LOWER(COALESCE(r.template_section, '')) LIKE '%labor%'")
        if "template_bucket" in template_cols:
            labor_filter_parts.append("LOWER(COALESCE(r.template_bucket, '')) LIKE 'labor_%'")
        if "line_item_kind" in template_cols:
            labor_filter_parts.append("LOWER(COALESCE(r.line_item_kind, '')) = 'labor'")
        if labor_filter_parts:
            try:
                frames.append(
                    safe_load(
                        f"""
                        WITH labor_by_workbook AS (
                            SELECT
                                r.job_id,
                                r.document_id,
                                COALESCE(r.source_file, '') AS source_file,
                                SUM(GREATEST(COALESCE(r.days, 0), 0)) AS estimate_estimated_duration_days,
                                SUM(GREATEST(COALESCE(r.total_hours, 0), 0)) AS estimate_estimated_labor_hours,
                                MAX(NULLIF(r.crew_size, 0)) AS estimate_estimated_crew_size,
                                SUM(COALESCE(NULLIF(r.estimated_cost, 0), NULLIF(r.calculated_cost, 0), 0)) AS estimate_labor_subtotal,
                                COUNT(*) AS labor_row_count,
                                {modified_order} AS source_modified_at
                            FROM estimate_template_rows r
                            {document_join}
                            WHERE r.job_id IS NOT NULL
                              AND ({' OR '.join(labor_filter_parts)})
                              AND LOWER(BTRIM(COALESCE(r.row_label, ''))) NOT IN ('types', 'types:', 'units')
                              AND COALESCE(r.days, 0) <= 30
                              AND COALESCE(r.total_hours, 0) <= 1000
                            GROUP BY r.job_id, r.document_id, COALESCE(r.source_file, '')
                        ),
                        ranked AS (
                            SELECT
                                *,
                                ROW_NUMBER() OVER (
                                    PARTITION BY job_id
                                    ORDER BY
                                        CASE WHEN COALESCE(estimate_labor_subtotal, 0) > 0 THEN 1 ELSE 0 END DESC,
                                        source_modified_at DESC,
                                        COALESCE(estimate_estimated_labor_hours, 0) DESC,
                                        COALESCE(estimate_estimated_duration_days, 0) DESC,
                                        labor_row_count DESC
                                ) AS rank
                            FROM labor_by_workbook
                            WHERE COALESCE(estimate_estimated_labor_hours, 0) > 0
                               OR COALESCE(estimate_estimated_duration_days, 0) > 0
                               OR COALESCE(estimate_labor_subtotal, 0) > 0
                        )
                        SELECT
                            job_id,
                            estimate_estimated_duration_days,
                            estimate_estimated_labor_hours,
                            estimate_estimated_crew_size,
                            estimate_labor_subtotal
                        FROM ranked
                        WHERE rank = 1
                        """
                    )
                )
            except Exception:
                pass

    if not frames:
        return pd.DataFrame()
    out = frames[0].copy()
    for frame in frames[1:]:
        if not isinstance(frame, pd.DataFrame) or frame.empty or "job_id" not in frame.columns:
            continue
        out = out.merge(frame.drop_duplicates("job_id", keep="first"), on="job_id", how="outer", suffixes=("", "_detail"))
        for alias in fields.values():
            detail_column = f"{alias}_detail"
            if detail_column not in out.columns:
                continue
            if alias not in out.columns:
                out[alias] = out[detail_column]
            else:
                current = pd.to_numeric(out[alias], errors="coerce")
                detail = pd.to_numeric(out[detail_column], errors="coerce")
                mask = (current.isna() | current.eq(0)) & detail.notna() & detail.ne(0)
                out.loc[mask, alias] = out.loc[mask, detail_column]
            out = out.drop(columns=[detail_column])
    return out


@st.cache_data(ttl=300, show_spinner=False)
def load_job_board_schedule() -> pd.DataFrame:
    cols = relation_columns("crew_schedule")
    if not cols or "job_id" not in cols:
        return pd.DataFrame()
    fields = {
        "job_id": "job_id",
        "assigned_crew_leader": "assigned_crew_leader",
        "estimated_start_date": "estimated_start_date",
        "estimated_end_date": "estimated_end_date",
        "estimated_duration_days": "estimated_duration_days",
        "estimated_labor_hours": "estimated_labor_hours",
        "estimated_crew_size": "estimated_crew_size",
        "schedule_status": "schedule_status",
        "priority": "schedule_priority",
        "blocking_issue": "blocking_issue",
        "schedule_notes": "schedule_notes",
    }
    select_parts = [f"{sql_column('cs', cols, source)} AS {alias}" for source, alias in fields.items()]
    try:
        schedule = safe_load(f"SELECT {', '.join(select_parts)} FROM crew_schedule cs")
    except Exception:
        return pd.DataFrame()
    if schedule.empty:
        return schedule
    return schedule.sort_values("estimated_start_date", na_position="last").drop_duplicates("job_id", keep="first")


@st.cache_data(ttl=300, show_spinner=False)
def load_job_board_warnings() -> pd.DataFrame:
    if "job_id" not in relation_columns("dashboard_job_warnings_actionable"):
        return pd.DataFrame()
    cols = relation_columns("dashboard_job_warnings_actionable")
    warning_expr = sql_column("w", cols, "warnings", "NULL")
    try:
        warnings = safe_load(
            f"""
            SELECT
                w.job_id,
                COUNT(*) AS warning_count,
                STRING_AGG(DISTINCT COALESCE({warning_expr}, ''), '; ') AS warning_summary
            FROM dashboard_job_warnings_actionable w
            WHERE w.job_id IS NOT NULL
            GROUP BY w.job_id
            """
        )
    except Exception:
        return pd.DataFrame()
    return warnings


@st.cache_data(ttl=300, show_spinner=False)
def load_job_board_df() -> pd.DataFrame:
    jobs = load_job_board_jobs()
    if not isinstance(jobs, pd.DataFrame):
        return pd.DataFrame()
    if jobs.empty or "job_id" not in jobs.columns:
        return jobs
    jobs = with_folder_link(jobs)
    jobs = merge_job_board_enrichments(
        jobs,
        load_job_board_vsimple_enrichment(),
        load_job_board_template_enrichment(),
        load_job_board_document_signal_enrichment(),
        load_job_board_estimate_labor_enrichment(),
    )
    document_dates = load_job_board_document_dates()
    if not document_dates.empty and "job_id" in document_dates.columns:
        jobs = jobs.merge(document_dates, on="job_id", how="left")
    jobs = add_job_board_proposal_stale_columns(jobs)
    overrides = load_job_workflow_overrides()
    if "job_id" in overrides.columns:
        jobs = jobs.merge(overrides, on="job_id", how="left")
    schedule = load_job_board_schedule()
    if not schedule.empty and "job_id" in schedule.columns:
        jobs = jobs.merge(schedule, on="job_id", how="left", suffixes=("", "_schedule"))
        for column in [
            "assigned_crew_leader",
            "estimated_start_date",
            "estimated_end_date",
            "estimated_duration_days",
            "estimated_labor_hours",
            "estimated_crew_size",
            "schedule_status",
            "schedule_priority",
            "blocking_issue",
            "schedule_notes",
        ]:
            schedule_column = f"{column}_schedule"
            if schedule_column not in jobs.columns:
                continue
            if column in jobs.columns:
                jobs[column] = jobs[column].combine_first(jobs[schedule_column])
            else:
                jobs[column] = jobs[schedule_column]
            jobs = jobs.drop(columns=[schedule_column])
    warnings = load_job_board_warnings()
    if not warnings.empty and "job_id" in warnings.columns:
        jobs = jobs.merge(warnings, on="job_id", how="left")
    if "warning_count" not in jobs.columns:
        jobs["warning_count"] = jobs["warnings"].fillna("").astype(str).str.strip().ne("").astype(int) if "warnings" in jobs.columns else 0
    if "warning_summary" not in jobs.columns:
        jobs["warning_summary"] = jobs["warnings"] if "warnings" in jobs.columns else ""
    return jobs


@st.cache_data(ttl=300, show_spinner=False)
def load_office_timesheet_entries() -> pd.DataFrame:
    cols = relation_columns("office_timesheet_entries")
    if not cols:
        return pd.DataFrame()
    fields = {
        "entry_id": "entry_id",
        "employee": "employee",
        "work_date": "work_date",
        "project_name": "project_name",
        "code": "code",
        "duration_hours": "duration_hours",
        "row_type": "row_type",
        "notes": "notes",
        "source_file": "source_file",
        "source_sheet": "source_sheet",
        "warnings": "warnings",
    }
    select_parts = [f"{sql_column('t', cols, source)} AS {alias}" for source, alias in fields.items()]
    try:
        return safe_load(f"SELECT {', '.join(select_parts)} FROM office_timesheet_entries t")
    except Exception:
        return pd.DataFrame()


def timesheet_match_text(value: object) -> str:
    text_clean = text_value(value).lower()
    text_clean = re.sub(r"&", " and ", text_clean)
    text_clean = re.sub(r"[^a-z0-9]+", " ", text_clean)
    stop_words = {
        "the",
        "and",
        "inc",
        "llc",
        "co",
        "company",
        "corp",
        "corporation",
        "roof",
        "roofing",
        "project",
        "job",
        "estimate",
        "proposal",
        "section",
        "sections",
        "building",
        "bldg",
    }
    tokens = [token for token in text_clean.split() if len(token) >= 2 and token not in stop_words]
    return " ".join(tokens)


def timesheet_match_tokens(value: object) -> set[str]:
    return set(timesheet_match_text(value).split())


TIMESHEET_JOB_CONTEXT_FIELDS = [
    "job_id",
    "customer",
    "job_name",
    "division",
    "job_type",
    "project_type",
    "pipeline_status",
    "status",
    "estimated_value",
    "final_price",
    "estimated_sqft",
    "folder_link_or_path",
]


def job_timesheet_match_candidates(jobs: pd.DataFrame) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    if not isinstance(jobs, pd.DataFrame) or jobs.empty:
        return candidates
    for _, row in jobs.iterrows():
        text_parts = [
            row.get("customer"),
            row.get("job_name"),
            row.get("job_id"),
            row.get("site_address"),
            row.get("city"),
            row.get("folder_path"),
            row.get("folder_link_or_path"),
            row.get("estimate_file"),
            row.get("proposal_file"),
            row.get("contract_file"),
        ]
        raw_label = first_nonblank(row.get("customer"), row.get("job_name"), row.get("job_id"))
        normalized = timesheet_match_text(" ".join(text_value(part) for part in text_parts))
        tokens = set(normalized.split())
        if not tokens:
            continue
        candidates.append(
            {
                "job_id": row.get("job_id"),
                "customer": row.get("customer"),
                "job_name": row.get("job_name"),
                "division": row.get("division"),
                "job_type": row.get("job_type"),
                "project_type": row.get("project_type"),
                "pipeline_status": row.get("pipeline_status"),
                "status": row.get("status"),
                "estimated_value": row.get("estimated_value"),
                "final_price": row.get("final_price"),
                "estimated_sqft": row.get("estimated_sqft"),
                "folder_link_or_path": row.get("folder_link_or_path"),
                "match_label": raw_label,
                "match_text": normalized,
                "match_tokens": tokens,
            }
        )
    return candidates


def score_timesheet_project_to_job(project: str, candidate: dict[str, Any]) -> tuple[float, str]:
    project_text = timesheet_match_text(project)
    if not project_text:
        return 0.0, "blank project"
    project_tokens = set(project_text.split())
    job_text = str(candidate.get("match_text") or "")
    job_tokens = set(candidate.get("match_tokens") or set())
    if not job_tokens:
        return 0.0, "job has no match tokens"
    overlap = project_tokens & job_tokens
    overlap_ratio = len(overlap) / max(len(project_tokens), 1)
    reverse_ratio = len(overlap) / max(len(job_tokens), 1)
    sequence_bonus = 0.0
    reason_parts: list[str] = []
    if project_text and project_text in job_text:
        sequence_bonus = 35.0
        reason_parts.append("project phrase appears in job text")
    elif job_text and job_text in project_text:
        sequence_bonus = 25.0
        reason_parts.append("job phrase appears in project text")
    if overlap:
        reason_parts.append(f"{len(overlap)} token overlap: {', '.join(sorted(overlap)[:6])}")
    score = min(100.0, sequence_bonus + overlap_ratio * 50.0 + reverse_ratio * 20.0)
    if len(project_tokens) == 1 and len(overlap) == 1:
        score = min(score, 72.0)
        reason_parts.append("single-token project match")
    if len(project_tokens) <= 2 and score > 88:
        score = 88.0
        reason_parts.append("short project label")
    return round(score, 3), "; ".join(reason_parts) or "no strong overlap"


def timesheet_candidate_token_index(candidates: list[dict[str, Any]]) -> dict[str, list[int]]:
    index: dict[str, list[int]] = {}
    for candidate_index, candidate in enumerate(candidates):
        for token in candidate.get("match_tokens") or set():
            index.setdefault(str(token), []).append(candidate_index)
    return index


def timesheet_candidate_indices_for_project(
    project: object,
    token_index: dict[str, list[int]],
) -> list[int]:
    project_tokens = timesheet_match_tokens(project)
    if not project_tokens:
        return []
    indices: set[int] = set()
    for token in project_tokens:
        indices.update(token_index.get(token, []))
    return sorted(indices)


def match_status_from_score(score: float) -> str:
    if score >= 90:
        return "Exact/Strong"
    if score >= 75:
        return "Strong"
    if score >= 58:
        return "Review"
    if score >= 42:
        return "Weak"
    return "Unmatched"


def office_timesheet_project_summary(timesheets: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(timesheets, pd.DataFrame) or timesheets.empty:
        return pd.DataFrame()
    df = timesheets.copy()
    if "project_name" not in df.columns:
        df["project_name"] = ""
    df["project_name"] = df["project_name"].fillna("").astype(str).str.strip()
    if "duration_hours" not in df.columns:
        df["duration_hours"] = 0.0
    df["duration_hours"] = pd.to_numeric(df["duration_hours"], errors="coerce").fillna(0.0)
    if "work_date" not in df.columns:
        df["work_date"] = pd.NaT
    df["work_date_parsed"] = pd.to_datetime(df["work_date"], errors="coerce")
    for column in ("employee", "code", "row_type", "notes"):
        if column not in df.columns:
            df[column] = ""
        df[column] = df[column].fillna("").astype(str)
    grouped = (
        df.groupby("project_name", dropna=False)
        .agg(
            total_hours=("duration_hours", "sum"),
            touch_count=("project_name", "size"),
            timed_entry_count=("row_type", lambda values: int((values == "timed_entry").sum())),
            activity_only_count=("row_type", lambda values: int((values == "activity_only").sum())),
            employee_count=("employee", lambda values: int(pd.Series(values).replace("", pd.NA).dropna().nunique())),
            first_touch=("work_date_parsed", "min"),
            last_touch=("work_date_parsed", "max"),
            codes=("code", lambda values: ", ".join(list(dict.fromkeys(v for v in values if text_value(v)))[:8])),
            employees=("employee", lambda values: ", ".join(list(dict.fromkeys(v for v in values if text_value(v)))[:8])),
            latest_notes=("notes", lambda values: next((text_value(v) for v in reversed(list(values)) if text_value(v)), "")),
        )
        .reset_index()
    )
    grouped["project_name"] = grouped["project_name"].replace("", "(blank)")
    grouped["first_touch"] = grouped["first_touch"].dt.date.astype("string")
    grouped["last_touch"] = grouped["last_touch"].dt.date.astype("string")
    return grouped.sort_values(["last_touch", "touch_count"], ascending=[False, False], na_position="last")


def match_timesheet_projects_to_jobs(project_summary: pd.DataFrame, jobs: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(project_summary, pd.DataFrame) or project_summary.empty:
        return pd.DataFrame()
    candidates = job_timesheet_match_candidates(jobs)
    token_index = timesheet_candidate_token_index(candidates)
    rows: list[dict[str, Any]] = []
    for _, project_row in project_summary.iterrows():
        project = text_value(project_row.get("project_name"))
        best: dict[str, Any] | None = None
        best_score = 0.0
        best_reason = ""
        candidate_indices = timesheet_candidate_indices_for_project(project, token_index)
        for candidate in (candidates[index] for index in candidate_indices):
            score, reason = score_timesheet_project_to_job(project, candidate)
            if score > best_score:
                best = candidate
                best_score = score
                best_reason = reason
        row = project_row.to_dict()
        row["match_score"] = round(best_score, 1)
        row["match_status"] = match_status_from_score(best_score)
        row["match_reason"] = best_reason
        if best and best_score >= 42:
            for key in TIMESHEET_JOB_CONTEXT_FIELDS:
                row[key] = best.get(key)
        else:
            for key in TIMESHEET_JOB_CONTEXT_FIELDS:
                row[key] = ""
        rows.append(row)
    return pd.DataFrame(rows)


def compact_unique_text(values: Iterable[object], limit: int = 8) -> str:
    seen: list[str] = []
    for value in values:
        text = text_value(value)
        if not text or text in seen:
            continue
        seen.append(text)
        if len(seen) >= limit:
            break
    return ", ".join(seen)


def timesheet_job_value(row: pd.Series | dict[str, Any]) -> float:
    final_price = optional_positive_number(row.get("final_price"))
    estimated_value = optional_positive_number(row.get("estimated_value"))
    return float(final_price or estimated_value or 0.0)


def timesheet_value_band(value: object) -> str:
    number = optional_positive_number(value) or 0.0
    if number >= 500000:
        return "$500k+"
    if number >= 250000:
        return "$250k-$500k"
    if number >= 100000:
        return "$100k-$250k"
    if number >= 50000:
        return "$50k-$100k"
    if number > 0:
        return "Under $50k"
    return "Unknown"


def prepare_timesheet_activity_rows(timesheets: pd.DataFrame, jobs: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(timesheets, pd.DataFrame) or timesheets.empty:
        return pd.DataFrame()
    df = timesheets.copy()
    for column in ("employee", "project_name", "code", "row_type", "notes", "source_file", "source_sheet", "warnings"):
        if column not in df.columns:
            df[column] = ""
        df[column] = df[column].fillna("").astype(str)
    if "duration_hours" not in df.columns:
        df["duration_hours"] = 0.0
    df["duration_hours"] = pd.to_numeric(df["duration_hours"], errors="coerce").fillna(0.0)
    if "work_date" not in df.columns:
        df["work_date"] = pd.NaT
    df["work_date_parsed"] = pd.to_datetime(df["work_date"], errors="coerce")
    df["activity_date"] = df["work_date_parsed"].dt.date.astype("string")
    df["employee"] = df["employee"].replace("", "Unknown")
    df["project_name"] = df["project_name"].replace("", "(blank)")
    df["touch_count"] = 1

    project_summary = office_timesheet_project_summary(df)
    matched = match_timesheet_projects_to_jobs(project_summary, jobs)
    match_columns = ["project_name", "match_score", "match_status", "match_reason", *TIMESHEET_JOB_CONTEXT_FIELDS]
    if not matched.empty:
        df = df.merge(matched[[column for column in match_columns if column in matched.columns]], on="project_name", how="left")
    else:
        for column in match_columns:
            if column != "project_name":
                df[column] = ""
    for column in ("match_score", "estimated_value", "final_price", "estimated_sqft"):
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0.0)
    df["job_value"] = df.apply(timesheet_job_value, axis=1)
    df["value_band"] = df["job_value"].apply(timesheet_value_band)
    df["matched_job"] = df["job_id"].fillna("").astype(str).str.strip().ne("")
    return df


@st.cache_data(ttl=300, show_spinner=False)
def load_timesheet_dashboard_activity() -> dict[str, Any]:
    timings: list[dict[str, Any]] = []
    start = time.perf_counter()
    timesheets = load_office_timesheet_entries()
    timings.append({"name": "office timesheet entry load", "seconds": round(time.perf_counter() - start, 4), "row_count": len(timesheets)})
    start = time.perf_counter()
    jobs = load_job_board_df()
    timings.append({"name": "job board load for timesheets", "seconds": round(time.perf_counter() - start, 4), "row_count": len(jobs)})
    start = time.perf_counter()
    activity = prepare_timesheet_activity_rows(timesheets, jobs)
    timings.append({"name": "timesheet job matching prep", "seconds": round(time.perf_counter() - start, 4), "row_count": len(activity)})
    return {
        "timesheet_rows": len(timesheets) if isinstance(timesheets, pd.DataFrame) else 0,
        "job_rows": len(jobs) if isinstance(jobs, pd.DataFrame) else 0,
        "activity": activity,
        "build_timings": timings,
    }


def summarize_timesheet_by_employee(activity: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(activity, pd.DataFrame) or activity.empty:
        return pd.DataFrame()
    df = activity.copy()
    grouped = (
        df.groupby("employee", dropna=False)
        .agg(
            total_hours=("duration_hours", "sum"),
            touch_count=("touch_count", "sum"),
            timed_entry_count=("row_type", lambda values: int((values == "timed_entry").sum())),
            activity_only_count=("row_type", lambda values: int((values == "activity_only").sum())),
            job_count=("job_id", lambda values: int(pd.Series(values).replace("", pd.NA).dropna().nunique())),
            project_string_count=("project_name", "nunique"),
            first_touch=("work_date_parsed", "min"),
            last_touch=("work_date_parsed", "max"),
            codes=("code", compact_unique_text),
            recent_projects=("project_name", compact_unique_text),
        )
        .reset_index()
    )
    grouped["first_touch"] = pd.to_datetime(grouped["first_touch"], errors="coerce").dt.date.astype("string")
    grouped["last_touch"] = pd.to_datetime(grouped["last_touch"], errors="coerce").dt.date.astype("string")
    return grouped.sort_values(["touch_count", "job_count", "last_touch"], ascending=False, na_position="last")


def summarize_timesheet_by_code(activity: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(activity, pd.DataFrame) or activity.empty:
        return pd.DataFrame()
    df = activity.copy()
    if "code" not in df.columns:
        df["code"] = ""
    df["code"] = df["code"].fillna("").astype(str).replace("", "Unknown")
    grouped = (
        df.groupby("code", dropna=False)
        .agg(
            touch_count=("touch_count", "sum"),
            total_hours=("duration_hours", "sum"),
            job_count=("job_id", lambda values: int(pd.Series(values).replace("", pd.NA).dropna().nunique())),
            project_string_count=("project_name", "nunique"),
            employee_count=("employee", lambda values: int(pd.Series(values).replace("", pd.NA).dropna().nunique())),
            employees=("employee", compact_unique_text),
            recent_projects=("project_name", compact_unique_text),
            last_touch=("work_date_parsed", "max"),
        )
        .reset_index()
    )
    grouped["last_touch"] = pd.to_datetime(grouped["last_touch"], errors="coerce").dt.date.astype("string")
    return grouped.sort_values(["touch_count", "job_count", "last_touch"], ascending=False, na_position="last")


def summarize_timesheet_daily_touches(activity: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(activity, pd.DataFrame) or activity.empty:
        return pd.DataFrame()
    df = activity.copy()
    if "work_date_parsed" not in df.columns:
        return pd.DataFrame()
    df = df[df["work_date_parsed"].notna()].copy()
    if df.empty:
        return pd.DataFrame()
    df["activity_date"] = df["work_date_parsed"].dt.date.astype("string")
    grouped = (
        df.groupby("activity_date", dropna=False)
        .agg(
            touch_count=("touch_count", "sum"),
            total_hours=("duration_hours", "sum"),
            job_count=("job_id", lambda values: int(pd.Series(values).replace("", pd.NA).dropna().nunique())),
            project_string_count=("project_name", "nunique"),
            employee_count=("employee", lambda values: int(pd.Series(values).replace("", pd.NA).dropna().nunique())),
        )
        .reset_index()
        .sort_values("activity_date")
    )
    return grouped


def timesheet_touch_value_weight(value: object, *, scale: str = "sqrt", baseline: float = 100000.0, cap: float = 5.0) -> float:
    number = optional_positive_number(value) or 0.0
    if number <= 0:
        return 0.25
    baseline = max(float(baseline or 100000.0), 1.0)
    normalized = number / baseline
    scale_key = str(scale or "sqrt").lower()
    if scale_key == "linear":
        weight = normalized
    elif scale_key == "log":
        weight = math.log10(number + 1.0) / math.log10(baseline + 1.0)
    else:
        weight = math.sqrt(normalized)
    return float(min(max(weight, 0.25), cap))


def summarize_timesheet_employee_weighted_touches(
    activity: pd.DataFrame,
    *,
    value_scale: str = "sqrt",
    top_employee_count: int = 8,
) -> pd.DataFrame:
    if not isinstance(activity, pd.DataFrame) or activity.empty or "work_date_parsed" not in activity.columns:
        return pd.DataFrame()
    df = activity.copy()
    df = df[df["work_date_parsed"].notna()].copy()
    if df.empty:
        return pd.DataFrame()
    for column in ("employee", "project_name", "job_id", "customer", "job_name", "code"):
        if column not in df.columns:
            df[column] = ""
        df[column] = df[column].fillna("").astype(str)
    if "job_value" not in df.columns:
        df["job_value"] = 0.0
    df["job_value"] = pd.to_numeric(df["job_value"], errors="coerce").fillna(0.0)
    df["activity_date"] = df["work_date_parsed"].dt.date.astype("string")
    df["touch_project_key"] = df["job_id"].where(df["job_id"].str.strip().ne(""), df["project_name"])
    df["touch_project_key"] = df["touch_project_key"].fillna("").astype(str).replace("", "(blank)")

    touch_rows = (
        df.groupby(["activity_date", "employee", "touch_project_key"], dropna=False)
        .agg(
            job_value=("job_value", "max"),
            customer=("customer", "first"),
            job_name=("job_name", "first"),
            project_name=("project_name", "first"),
            codes=("code", compact_unique_text),
            source_line_count=("touch_count", "sum"),
        )
        .reset_index()
    )
    touch_rows["project_touch_count"] = 1
    touch_rows["value_weight"] = touch_rows["job_value"].apply(lambda value: timesheet_touch_value_weight(value, scale=value_scale))
    touch_rows["weighted_touch_score"] = touch_rows["project_touch_count"] * touch_rows["value_weight"]

    employee_totals = (
        touch_rows.groupby("employee", dropna=False)["weighted_touch_score"]
        .sum()
        .sort_values(ascending=False)
        .head(max(int(top_employee_count or 8), 1))
    )
    top_employees = set(employee_totals.index.astype(str))
    chart_rows = touch_rows[touch_rows["employee"].astype(str).isin(top_employees)].copy()
    if chart_rows.empty:
        return pd.DataFrame()
    grouped = (
        chart_rows.groupby(["activity_date", "employee"], dropna=False)
        .agg(
            weighted_touch_score=("weighted_touch_score", "sum"),
            project_touch_count=("project_touch_count", "sum"),
            job_value_touched=("job_value", "sum"),
            source_line_count=("source_line_count", "sum"),
            projects=("project_name", compact_unique_text),
            customers=("customer", compact_unique_text),
            codes=("codes", compact_unique_text),
        )
        .reset_index()
        .sort_values(["activity_date", "employee"])
    )
    return grouped


def summarize_timesheet_job_type_touches(job_rollup: pd.DataFrame, activity: pd.DataFrame) -> pd.DataFrame:
    source = job_rollup if isinstance(job_rollup, pd.DataFrame) and not job_rollup.empty else activity
    if not isinstance(source, pd.DataFrame) or source.empty:
        return pd.DataFrame()
    df = source.copy()
    if "job_type" not in df.columns:
        df["job_type"] = ""
    df["job_type"] = df["job_type"].fillna("").astype(str).replace("", "Unknown")
    if "job_id" not in df.columns:
        df["job_id"] = ""
    if "job_value" not in df.columns:
        df["job_value"] = 0.0
    grouped = (
        df.groupby("job_type", dropna=False)
        .agg(
            touch_count=("touch_count", "sum"),
            total_hours=("total_hours" if "total_hours" in df.columns else "duration_hours", "sum"),
            job_count=("job_id", lambda values: int(pd.Series(values).replace("", pd.NA).dropna().nunique())),
            employee_count=("employee_count" if "employee_count" in df.columns else "employee", "sum" if "employee_count" in df.columns else lambda values: int(pd.Series(values).replace("", pd.NA).dropna().nunique())),
            job_value=("job_value", "sum"),
        )
        .reset_index()
    )
    return grouped.sort_values(["touch_count", "job_count"], ascending=False, na_position="last")


def summarize_timesheet_by_job(activity: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(activity, pd.DataFrame) or activity.empty or "job_id" not in activity.columns:
        return pd.DataFrame()
    matched = activity[activity["job_id"].fillna("").astype(str).str.strip().ne("")].copy()
    if matched.empty:
        return pd.DataFrame()
    grouped = (
        matched.groupby("job_id", dropna=False)
        .agg(
            customer=("customer", "first"),
            job_name=("job_name", "first"),
            division=("division", "first"),
            job_type=("job_type", "first"),
            project_type=("project_type", "first"),
            pipeline_status=("pipeline_status", "first"),
            status=("status", "first"),
            job_value=("job_value", "max"),
            estimated_sqft=("estimated_sqft", "max"),
            total_hours=("duration_hours", "sum"),
            touch_count=("touch_count", "sum"),
            employee_count=("employee", lambda values: int(pd.Series(values).replace("", pd.NA).dropna().nunique())),
            employees=("employee", compact_unique_text),
            codes=("code", compact_unique_text),
            project_strings=("project_name", compact_unique_text),
            first_touch=("work_date_parsed", "min"),
            last_touch=("work_date_parsed", "max"),
            best_match_score=("match_score", "max"),
            folder_link_or_path=("folder_link_or_path", "first"),
        )
        .reset_index()
    )
    grouped["value_band"] = grouped["job_value"].apply(timesheet_value_band)
    grouped["first_touch"] = pd.to_datetime(grouped["first_touch"], errors="coerce").dt.date.astype("string")
    grouped["last_touch"] = pd.to_datetime(grouped["last_touch"], errors="coerce").dt.date.astype("string")
    return grouped.sort_values(["last_touch", "total_hours", "touch_count"], ascending=False, na_position="last")


def first_nonblank(*values: object) -> str:
    for value in values:
        if value is None:
            continue
        try:
            if pd.isna(value):
                continue
        except (TypeError, ValueError):
            pass
        text = str(value).strip()
        if text and text.lower() not in {"nan", "none", "null", "-", "—"}:
            return text
    return ""


def truthy_bool(value: object) -> bool:
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "checked", "closed", "lost"}
    return bool(value)


def folder_pipeline_bucket_for_row(row: pd.Series | dict[str, Any]) -> str:
    folder_text = " ".join(
        text_value(row.get(column))
        for column in ["folder_path", "folder_url", "folder_link_or_path", "folder"]
        if hasattr(row, "get")
    ).lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", folder_text)
    tokens = set(normalized.split())
    if (
        {"did", "not", "get"}.issubset(tokens)
        or "dng" in tokens
        or "cancelled" in tokens
        or "canceled" in tokens
        or "cancel" in tokens
    ):
        return "Closed Lost Folder"
    if "completed" in tokens or "complete" in tokens:
        return "Completed Folder"
    if "contracted" in tokens or "contract" in tokens:
        return "Contracted Folder"
    return "Proposal Pipeline"


def is_proposal_pipeline_review_row(row: pd.Series | dict[str, Any]) -> bool:
    if folder_pipeline_bucket_for_row(row) != "Proposal Pipeline":
        return False
    if truthy_bool(row.get("closed_did_not_get")):
        return False
    freshness = text_value(row.get("opportunity_freshness")) or job_board_freshness_for_row(row)
    if freshness in {"Contracted / Active", "Completed", "Closed / Did Not Get", "Not Proposal Pipeline"}:
        return False
    if freshness in {"Fresh / Active", "Aging", "Stale", "Estimate, No Proposal"}:
        return True
    proposal_signal = first_nonblank(
        row.get("proposal_status_flag"),
        row.get("proposal_file"),
        row.get("proposal_file_name"),
        row.get("proposal_modified_at"),
        row.get("proposal_created_at"),
    )
    stage = normalize_board_status(first_nonblank(row.get("workflow_status"), row.get("pipeline_status"), row.get("status"), row.get("sales_stage")))
    if proposal_signal and text_value(proposal_signal) != "No proposal date":
        return True
    return stage in {"Proposed", "Proposal Submitted", "Contract Pending"}


def job_board_contract_completion_bucket(row: pd.Series | dict[str, Any]) -> str:
    folder_bucket = folder_pipeline_bucket_for_row(row)
    status_text = " ".join(
        text_value(row.get(column))
        for column in [
            "workflow_status",
            "pipeline_status",
            "status",
            "sales_stage",
            "schedule_status",
            "win_loss_status",
        ]
        if hasattr(row, "get")
    ).lower()
    status_normalized = re.sub(r"[^a-z0-9]+", " ", status_text)
    closed_lost_signal = (
        folder_bucket == "Closed Lost Folder"
        or "closed lost" in status_normalized
        or "did not get" in status_normalized
        or any(token in status_normalized.split() for token in ["lost", "dead", "declined", "cancelled", "canceled"])
    )
    if closed_lost_signal:
        return "Closed / Did Not Get"
    completed_signal = (
        folder_bucket == "Completed Folder"
        or any(token in status_normalized.split() for token in ["completed", "complete", "invoiced", "invoice"])
        or bool(first_nonblank(row.get("completion_date"), row.get("date_of_completion"), row.get("completed_date")))
    )
    if completed_signal:
        return "Completed"
    contracted_signal = (
        folder_bucket == "Contracted Folder"
        or truthy_bool(row.get("has_signed_contract"))
        or "closed won" in status_normalized
        or any(token in status_normalized.split() for token in ["contracted", "awarded", "signed"])
    )
    if contracted_signal:
        return "Contracted / Active"
    return ""


JOB_BOARD_FRESHNESS_COLORS = {
    "Fresh / Active": "#e6f4ea",
    "Aging": "#fff7d6",
    "Stale": "#fde8e8",
    "Contracted / Active": "#e8f0fe",
    "Estimate, No Proposal": "#e8f0fe",
    "Closed / Did Not Get": "#f3f4f6",
    "Completed": "#f3f4f6",
    "No Proposal Date": "#f3f4f6",
    "Not Proposal Pipeline": "#f3f4f6",
}


def job_board_freshness_for_row(row: pd.Series | dict[str, Any]) -> str:
    contract_bucket = job_board_contract_completion_bucket(row)
    if contract_bucket:
        return contract_bucket
    if folder_pipeline_bucket_for_row(row) != "Proposal Pipeline":
        return "Not Proposal Pipeline"
    has_estimate = bool(
        first_nonblank(
            row.get("estimate_created_at"),
            row.get("estimate_modified_at"),
            row.get("estimate_file"),
            row.get("estimate_file_name"),
        )
    )
    has_proposal = bool(
        first_nonblank(
            row.get("proposal_date_for_stale"),
            row.get("proposal_created_at"),
            row.get("proposal_modified_at"),
            row.get("proposal_file"),
            row.get("proposal_file_name"),
        )
    )
    if has_estimate and not has_proposal:
        return "Estimate, No Proposal"
    age = pd.to_numeric(pd.Series([row.get("proposal_age_days")]), errors="coerce").iloc[0]
    if pd.isna(age):
        return "No Proposal Date"
    if float(age) <= 30:
        return "Fresh / Active"
    if float(age) <= 90:
        return "Aging"
    return "Stale"


SALES_PIPELINE_STAGES = [
    "Lead Received",
    "Site Visit Scheduled",
    "Estimate In Progress",
    "Proposal Submitted",
    "Follow-Up / Negotiation",
    "Contract Pending",
    "Closed Won",
    "Closed Lost",
]

READINESS_STATUSES = [
    "Ready To Schedule",
    "Customer Hold",
    "Material Hold",
    "Permit Hold",
    "Weather Window",
    "Scheduled",
    "Missing Job Spec",
    "Not Contracted Folder",
]


def row_first_nonblank(row: pd.Series, columns: Iterable[str]) -> str:
    return first_nonblank(*(row.get(column) for column in columns if column in row.index))


def row_first_positive_number(row: pd.Series, columns: Iterable[str]) -> float:
    for column in columns:
        if column not in row.index:
            continue
        value = pd.to_numeric(pd.Series([row.get(column)]), errors="coerce").iloc[0]
        if not pd.isna(value) and float(value) > 0:
            return float(value)
    return 0.0


def normalized_sales_stage(row: pd.Series) -> str:
    if truthy_bool(row.get("closed_did_not_get")) or folder_pipeline_bucket_for_row(row) == "Closed Lost Folder":
        return "Closed Lost"
    source_text = " ".join(
        row_first_nonblank(row, [column])
        for column in [
            "workflow_status",
            "pipeline_status",
            "status",
            "proposal_status",
            "contract_status",
            "followup_status",
            "stage",
        ]
    ).lower()
    if any(token in source_text for token in ["closed lost", "lost", "dead", "no bid", "declined", "cancelled"]):
        return "Closed Lost"
    if any(token in source_text for token in ["closed won", "won", "completed", "complete", "invoiced"]):
        return "Closed Won"
    if any(token in source_text for token in ["contract pending", "pending contract", "signed", "contracted", "awarded"]):
        return "Contract Pending"
    if any(token in source_text for token in ["follow", "negotiat", "revision", "revised"]):
        return "Follow-Up / Negotiation"
    if any(token in source_text for token in ["proposal", "proposed", "submitted", "sent"]):
        return "Proposal Submitted"
    if any(token in source_text for token in ["estimate", "pricing", "takeoff", "progress"]):
        return "Estimate In Progress"
    if any(token in source_text for token in ["site visit", "visit scheduled", "walkthrough", "walk through", "inspection"]):
        return "Site Visit Scheduled"
    if source_text.strip():
        return "Lead Received"
    return "Lead Received"


def normalized_project_category(row: pd.Series) -> str:
    source_text = " ".join(
        row_first_nonblank(row, [column])
        for column in ["division", "job_type", "project_type", "scope_type", "coating_type", "foam_type", "job_name"]
    ).lower()
    if "repair" in source_text:
        return "Repairs"
    if any(token in source_text for token in ["metal", "standing seam", "r-panel", "r panel"]):
        return "Metal Restoration"
    if any(token in source_text for token in ["insulation", "spray foam", "foam", "open cell", "closed cell"]):
        return "Spray Foam Insulation"
    if any(token in source_text for token in ["roof", "coating", "restoration", "silicone", "acrylic"]):
        return "Roofing Restoration"
    return "Unclassified"


def normalized_project_size(value: float) -> str:
    if value >= 250000:
        return "$250k+"
    if value >= 100000:
        return "$100k-$250k"
    if value >= 25000:
        return "$25k-$100k"
    if value > 0:
        return "Under $25k"
    return "Unknown"


def normalize_sales_jobs(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame):
        return pd.DataFrame()
    out = with_folder_link(df).copy()
    if out.empty:
        return out
    out["sales_stage"] = out.apply(normalized_sales_stage, axis=1)
    out["sales_value"] = out.apply(
        lambda row: row_first_positive_number(
            row,
            ["estimated_value", "final_price", "contract_amount", "contract_value", "proposal_amount", "invoice_amount"],
        ),
        axis=1,
    )
    out["estimator_display"] = out.apply(
        lambda row: row_first_nonblank(
            row,
            ["estimator", "salesperson", "sales_person", "deal_owner", "assigned_user", "owner", "project_manager"],
        )
        or "Not Captured",
        axis=1,
    )
    out["lead_source_display"] = out.apply(
        lambda row: row_first_nonblank(
            row,
            ["lead_source", "source", "referral_source", "marketing_source", "business_development_source"],
        )
        or "Not Captured",
        axis=1,
    )
    out["project_category"] = out.apply(normalized_project_category, axis=1)
    out["project_size"] = out["sales_value"].apply(normalized_project_size)
    out["win_loss_status"] = out["sales_stage"].map(
        {
            "Closed Won": "Won",
            "Closed Lost": "Lost",
        }
    ).fillna("Open")
    return out


def warranty_display_for_row(row: pd.Series) -> str:
    parts = [
        row_first_nonblank(row, ["warranty_years", "warranty_duration", "warranty_term"]),
        row_first_nonblank(row, ["warranty_type", "warranty_provider"]),
        row_first_nonblank(row, ["warranty_scope", "warranty_area", "warranty_amount"]),
    ]
    text = " ".join(part for part in parts if part).strip()
    if text:
        return text
    has_warranty = row.get("has_warranty")
    has_warranty_text = text_value(has_warranty).lower()
    if has_warranty_text in {"true", "yes", "y", "1", "available"}:
        return "Warranty indicated"
    return "Not Captured"


def prepare_job_board_dashboard_rows(jobs: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(jobs, pd.DataFrame) or jobs.empty:
        return pd.DataFrame()
    out = normalize_operations_jobs(normalize_sales_jobs(jobs))
    if out.empty:
        return out
    out["project"] = out.apply(lambda row: row_first_nonblank(row, ["job_name", "customer", "estimate_file"]) or "Untitled job", axis=1)
    out["customer_display"] = out.apply(lambda row: row_first_nonblank(row, ["customer", "bill_to"]) or "Not Captured", axis=1)
    out["substrate_display"] = out.apply(
        lambda row: row_first_nonblank(
            row,
            ["substrate", "roof_type", "existing_roof_type", "deck_type", "surface_type", "building_type"],
        )
        or "Not Captured",
        axis=1,
    )
    out["material_system_display"] = out.apply(
        lambda row: row_first_nonblank(
            row,
            ["material_system", "product_system", "coating_type", "foam_type", "material_type", "roof_system"],
        )
        or "Not Captured",
        axis=1,
    )
    out["warranty_display"] = out.apply(warranty_display_for_row, axis=1)
    out["completion_date_display"] = date_column_series(
        out,
        ["completion_date", "date_of_completion", "completed_date", "invoice_date"],
    )
    out["labor_plan"] = out.apply(
        lambda row: " / ".join(
            part
            for part in [
                f"{format_summary_value(row.get('estimated_duration_days'), kind='number')} days"
                if row_first_positive_number(row, ["estimated_duration_days"]) > 0
                else "",
                f"{format_summary_value(row.get('estimated_crew_size'), kind='number')} crew"
                if row_first_positive_number(row, ["estimated_crew_size"]) > 0
                else "",
                f"{format_summary_value(row.get('estimated_labor_hours'), kind='number')} hrs"
                if row_first_positive_number(row, ["estimated_labor_hours"]) > 0
                else "",
                f"{fmt_dollar(row_first_positive_number(row, ['labor_subtotal']))} labor"
                if row_first_positive_number(row, ["labor_subtotal"]) > 0
                else "",
            ]
            if part
        )
        or "Not Captured",
        axis=1,
    )
    out["folder"] = out.get("folder_link_or_path", "")
    return out


def sales_pipeline_rollup(jobs: pd.DataFrame) -> pd.DataFrame:
    if jobs.empty:
        return pd.DataFrame(columns=["stage", "job_count", "value"])
    rollup = (
        jobs.groupby("sales_stage", dropna=False)
        .agg(job_count=("sales_stage", "size"), value=("sales_value", "sum"))
        .reindex(SALES_PIPELINE_STAGES, fill_value=0)
        .reset_index()
        .rename(columns={"sales_stage": "stage"})
    )
    return rollup


def sales_performance_rollup(jobs: pd.DataFrame, group_column: str) -> pd.DataFrame:
    columns = [
        "category",
        "proposal_count",
        "won_count",
        "lost_count",
        "open_count",
        "proposal_value",
        "won_value",
        "win_rate",
    ]
    if jobs.empty or group_column not in jobs.columns:
        return pd.DataFrame(columns=columns)
    proposed_stages = {"Proposal Submitted", "Follow-Up / Negotiation", "Contract Pending", "Closed Won", "Closed Lost"}
    rows: list[dict[str, Any]] = []
    for category, group in jobs.groupby(group_column, dropna=False):
        stage = group["sales_stage"].fillna("").astype(str)
        proposed = group[stage.isin(proposed_stages)]
        won = group[stage == "Closed Won"]
        lost = group[stage == "Closed Lost"]
        decided_count = len(won) + len(lost)
        rows.append(
            {
                "category": text_value(category) or "Not Captured",
                "proposal_count": len(proposed),
                "won_count": len(won),
                "lost_count": len(lost),
                "open_count": len(group[~stage.isin(["Closed Won", "Closed Lost"])]),
                "proposal_value": float(proposed["sales_value"].sum()),
                "won_value": float(won["sales_value"].sum()),
                "win_rate": (len(won) / decided_count) if decided_count else None,
            }
        )
    return pd.DataFrame(rows).sort_values(["won_value", "proposal_value"], ascending=False, na_position="last")


def estimator_kpi_rollup(jobs: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "estimator",
        "site_visits",
        "site_visit_goal",
        "proposals_sent",
        "proposal_goal",
        "proposal_value",
        "proposal_value_goal",
        "followups_completed",
        "followup_goal",
        "contracts_won",
        "contracts_won_goal",
    ]
    if jobs.empty:
        return pd.DataFrame(columns=columns)
    stage_rank = {stage: index for index, stage in enumerate(SALES_PIPELINE_STAGES)}
    rows: list[dict[str, Any]] = []
    for estimator, group in jobs.groupby("estimator_display", dropna=False):
        ranks = group["sales_stage"].map(stage_rank).fillna(0)
        proposals = group[group["sales_stage"].isin(["Proposal Submitted", "Follow-Up / Negotiation", "Contract Pending", "Closed Won", "Closed Lost"])]
        rows.append(
            {
                "estimator": text_value(estimator) or "Not Captured",
                "site_visits": int((ranks >= stage_rank["Site Visit Scheduled"]).sum()),
                "site_visit_goal": 10,
                "proposals_sent": int(len(proposals)),
                "proposal_goal": 5,
                "proposal_value": float(proposals["sales_value"].sum()),
                "proposal_value_goal": "$150k+",
                "followups_completed": int((group["sales_stage"] == "Follow-Up / Negotiation").sum()),
                "followup_goal": 20,
                "contracts_won": int((group["sales_stage"] == "Closed Won").sum()),
                "contracts_won_goal": "1-2",
            }
        )
    return pd.DataFrame(rows).sort_values("proposal_value", ascending=False)


def lead_source_rollup(jobs: pd.DataFrame) -> pd.DataFrame:
    columns = ["source", "job_count", "open_value", "revenue_won"]
    if jobs.empty:
        return pd.DataFrame(columns=columns)
    open_mask = ~jobs["sales_stage"].isin(["Closed Won", "Closed Lost"])
    won_mask = jobs["sales_stage"] == "Closed Won"
    rollup = (
        jobs.assign(open_value=jobs["sales_value"].where(open_mask, 0.0), revenue_won=jobs["sales_value"].where(won_mask, 0.0))
        .groupby("lead_source_display", dropna=False)
        .agg(job_count=("lead_source_display", "size"), open_value=("open_value", "sum"), revenue_won=("revenue_won", "sum"))
        .reset_index()
        .rename(columns={"lead_source_display": "source"})
        .sort_values(["revenue_won", "open_value"], ascending=False)
    )
    return rollup


def date_column_series(df: pd.DataFrame, columns: Iterable[str]) -> pd.Series:
    values = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
    for column in columns:
        if column in df.columns:
            parsed = pd.to_datetime(df[column], errors="coerce", utc=True).dt.tz_convert(None)
            values = values.combine_first(parsed)
    return values


def normalize_operations_jobs(jobs: pd.DataFrame, schedule: pd.DataFrame | None = None) -> pd.DataFrame:
    if not isinstance(jobs, pd.DataFrame):
        return pd.DataFrame()
    out = with_folder_link(jobs).copy()
    if schedule is not None and not schedule.empty and "job_id" in out.columns and "job_id" in schedule.columns:
        schedule_cols = [
            column
            for column in [
                "job_id",
                "assigned_crew_leader",
                "estimated_start_date",
                "estimated_end_date",
                "estimated_duration_days",
                "estimated_labor_hours",
                "estimated_crew_size",
                "schedule_status",
                "blocking_issue",
                "priority",
                "schedule_notes",
            ]
            if column in schedule.columns
        ]
        out = out.merge(schedule[schedule_cols].drop_duplicates("job_id"), on="job_id", how="left", suffixes=("", "_schedule"))
        for column in [name for name in schedule_cols if name != "job_id"]:
            schedule_column = f"{column}_schedule"
            if schedule_column in out.columns:
                if column in out.columns:
                    out[column] = out[column].combine_first(out[schedule_column])
                else:
                    out[column] = out[schedule_column]
                out = out.drop(columns=[schedule_column])
    if out.empty:
        return out
    out["operations_value"] = out.apply(
        lambda row: row_first_positive_number(
            row,
            ["estimated_value", "final_price", "contract_amount", "contract_value", "proposal_amount", "invoice_amount"],
        ),
        axis=1,
    )
    out["project_category"] = out.apply(normalized_project_category, axis=1)
    out["ready_date"] = date_column_series(
        out,
        [
            "ready_date",
            "contract_date",
            "signed_contract_date",
            "estimate_date",
            "created_at",
            "last_scanned_at",
        ],
    )
    out["completion_date"] = date_column_series(
        out,
        ["completion_date", "date_of_completion", "completed_date", "invoice_date", "updated_at", "last_scanned_at"],
    )
    out["estimated_start_date_parsed"] = date_column_series(out, ["estimated_start_date"]) if "estimated_start_date" in out.columns else pd.NaT
    out["estimated_end_date_parsed"] = date_column_series(out, ["estimated_end_date"]) if "estimated_end_date" in out.columns else pd.NaT
    today = pd.Timestamp(date.today())
    out["days_waiting"] = (today.normalize() - out["ready_date"].dt.normalize()).dt.days
    out.loc[out["days_waiting"].isna() | (out["days_waiting"] < 0), "days_waiting"] = 0
    out["readiness_status"] = out.apply(normalized_readiness_status, axis=1)
    out["schedule_health"] = out.apply(normalized_schedule_health, axis=1)
    out["expected_pct_complete"] = out.apply(expected_percent_complete, axis=1)
    out["actual_pct_complete"] = out.apply(actual_percent_complete, axis=1)
    out["production_risk_summary"] = out.apply(production_risk_summary, axis=1)
    out["material_readiness"] = out.apply(lambda row: readiness_flag(row, ["material", "submittal"], "Material review"), axis=1)
    out["equipment_readiness"] = out.apply(lambda row: readiness_flag(row, ["equipment", "rig", "lift"], "Equipment review"), axis=1)
    out["customer_communication"] = out.apply(lambda row: readiness_flag(row, ["customer", "communicat", "expectation", "promise"], "Customer review"), axis=1)
    return out


def normalized_readiness_status(row: pd.Series) -> str:
    source_text = " ".join(
        row_first_nonblank(row, [column])
        for column in ["workflow_status", "schedule_status", "blocking_issue", "schedule_notes", "pipeline_status", "status", "warnings"]
    ).lower()
    has_start = not pd.isna(row.get("estimated_start_date_parsed"))
    in_contracted_folder = folder_pipeline_bucket_for_row(row) == "Contracted Folder"
    has_job_spec = truthy_bool(row.get("has_job_spec"))
    if any(token in source_text for token in ["customer hold", "waiting on customer", "customer delay", "owner hold"]):
        return "Customer Hold"
    if any(token in source_text for token in ["material", "submittal", "lead time"]):
        return "Material Hold"
    if "permit" in source_text:
        return "Permit Hold"
    if any(token in source_text for token in ["weather", "temperature", "seasonal", "window"]):
        return "Weather Window"
    if has_start or any(token in source_text for token in ["scheduled", "mobilized", "in progress"]):
        return "Scheduled"
    if not in_contracted_folder:
        return "Not Contracted Folder"
    if not has_job_spec:
        return "Missing Job Spec"
    return "Ready To Schedule"


def normalized_schedule_health(row: pd.Series) -> str:
    source_text = " ".join(
        row_first_nonblank(row, [column])
        for column in ["schedule_status", "status", "pipeline_status", "blocking_issue", "schedule_notes"]
    ).lower()
    today = pd.Timestamp(date.today()).normalize()
    start = row.get("estimated_start_date_parsed")
    end = row.get("estimated_end_date_parsed")
    if any(token in source_text for token in ["complete", "closed won", "invoiced"]):
        return "Completed"
    if any(token in source_text for token in ["behind", "delayed", "blocked", "hold"]):
        return "Behind / Blocked"
    if not pd.isna(end) and today > pd.Timestamp(end).normalize():
        return "Behind / Blocked"
    if not pd.isna(start) and pd.Timestamp(start).normalize() > today:
        return "Starting Soon"
    if not pd.isna(start):
        return "On Track"
    return "Awaiting Schedule"


def expected_percent_complete(row: pd.Series) -> float | None:
    start = row.get("estimated_start_date_parsed")
    end = row.get("estimated_end_date_parsed")
    if pd.isna(start) or pd.isna(end):
        return None
    start_ts = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize()
    today = pd.Timestamp(date.today()).normalize()
    total_days = max((end_ts - start_ts).days + 1, 1)
    elapsed_days = min(max((today - start_ts).days + 1, 0), total_days)
    return round(elapsed_days / total_days, 2)


def actual_percent_complete(row: pd.Series) -> float | None:
    for column in ["percent_complete", "pct_complete", "progress_pct", "actual_pct_complete"]:
        if column not in row.index:
            continue
        value = pd.to_numeric(pd.Series([row.get(column)]), errors="coerce").iloc[0]
        if not pd.isna(value):
            numeric = float(value)
            return round(numeric / 100, 2) if numeric > 1 else round(numeric, 2)
    return None


def readiness_flag(row: pd.Series, keywords: list[str], default: str) -> str:
    source_text = " ".join(
        row_first_nonblank(row, [column])
        for column in ["schedule_status", "blocking_issue", "schedule_notes", "warnings", "internal_notes"]
    ).lower()
    if any(f"{keyword} ready" in source_text or f"{keyword}s ready" in source_text for keyword in keywords):
        return "Ready"
    if any(keyword in source_text for keyword in keywords):
        return default
    return "Not Captured"


def production_risk_summary(row: pd.Series) -> str:
    notes = " ".join(
        row_first_nonblank(row, [column])
        for column in ["blocking_issue", "schedule_notes", "warnings", "warning_summary", "internal_notes"]
    )
    if notes.strip():
        return notes[:240]
    missing: list[str] = []
    if pd.isna(row.get("estimated_start_date_parsed")):
        missing.append("schedule")
    if not row_first_nonblank(row, ["assigned_crew_leader"]):
        missing.append("crew")
    if row_first_positive_number(row, ["estimated_labor_hours"]) <= 0:
        missing.append("labor estimate")
    if row_first_positive_number(row, ["estimated_sqft"]) <= 0:
        missing.append("square footage")
    return f"Missing {', '.join(missing)}" if missing else "No risk note captured"


def readiness_summary(ops: pd.DataFrame) -> pd.DataFrame:
    columns = ["status", "jobs", "revenue", "avg_days_waiting"]
    if ops.empty:
        return pd.DataFrame(columns=columns)
    unscheduled = ops[ops["readiness_status"].isin(READINESS_STATUSES)]
    return (
        unscheduled.groupby("readiness_status", dropna=False)
        .agg(jobs=("readiness_status", "size"), revenue=("operations_value", "sum"), avg_days_waiting=("days_waiting", "mean"))
        .reindex(READINESS_STATUSES, fill_value=0)
        .reset_index()
        .rename(columns={"readiness_status": "status"})
    )


def first_existing_value(row: pd.Series, columns: list[str]) -> object:
    for column in columns:
        if column in row.index:
            value = row.get(column)
            if first_nonblank(value):
                return value
    return None


def normalize_board_status(value: object) -> str:
    raw = str(value or "").strip()
    key = raw.lower().replace("_", " ").replace("-", " ")
    key = " ".join(key.split())

    mapping = {
        "lead created": "Lead Created",
        "lead": "Lead Created",
        "new lead": "Lead Created",
        "contacted": "Contacted",
        "estimate in progress": "Estimate In Progress",
        "estimating": "Estimate In Progress",
        "estimate": "Estimate In Progress",
        "estimated": "Estimate In Progress",
        "proposed": "Proposed",
        "proposal": "Proposal Submitted",
        "proposal submitted": "Proposal Submitted",
        "submitted": "Proposal Submitted",
        "contracted": "Contracted",
        "contract": "Contracted",
        "contracted repairs": "Contracted Repairs",
        "contracted repair": "Contracted Repairs",
        "scheduled": "Scheduled",
        "in progress": "In Progress",
        "active": "In Progress",
        "completed": "Completed",
        "complete": "Completed",
        "invoiced": "Invoiced",
        "invoice": "Invoiced",
        "closed lost": "Closed Lost",
        "lost": "Closed Lost",
        "did not get": "Closed Lost",
        "closed won": "Closed Won",
        "won": "Closed Won",
        "folder created": "Folder Created",
    }
    return mapping.get(key, raw if raw else "Other")


def board_status_for_row(row: pd.Series) -> str:
    if truthy_bool(row.get("closed_did_not_get")) or folder_pipeline_bucket_for_row(row) == "Closed Lost Folder":
        return "Closed Lost"
    workflow_value = first_existing_value(row, POSSIBLE_WORKFLOW_STATUS_COLS)
    pipeline_value = first_existing_value(row, POSSIBLE_PIPELINE_STATUS_COLS)
    status_value = first_existing_value(row, POSSIBLE_STATUS_COLS)
    raw_status = first_nonblank(workflow_value, pipeline_value, status_value)
    return normalize_board_status(raw_status)


def bool_label(value: object) -> str:
    return "Yes" if truthy_bool(value) else "No"


def job_board_summary(row: pd.Series) -> str:
    parts = [
        f"Job: {text_value(row.get('job_name')) or text_value(row.get('customer'))}",
        f"Customer: {text_value(row.get('customer')) or '-'}",
        f"Status: {text_value(row.get('workflow_status')) or text_value(row.get('pipeline_status')) or text_value(row.get('status')) or '-'}",
        f"Value: {format_summary_value(row.get('estimated_value'), kind='money')}",
        f"Owner: {text_value(row.get('deal_owner')) or '-'}",
        f"Assigned User: {text_value(row.get('assigned_user')) or '-'}",
        f"Priority: {text_value(row.get('priority')) or '-'}",
        f"Follow Up: {text_value(row.get('follow_up_date')) or '-'}",
        f"Crew: {text_value(row.get('assigned_crew_leader')) or '-'}",
        f"Schedule: {text_value(row.get('estimated_start_date')) or '-'} to {text_value(row.get('estimated_end_date')) or '-'}",
        f"Warnings: {text_value(row.get('warning_summary')) or text_value(row.get('warnings')) or '-'}",
    ]
    return "\n".join(parts)


def render_job_board_documents(row: pd.Series) -> None:
    st.subheader("Job Documents")
    render_document_access("Open Job Folder", row.get("folder_url") or row.get("folder_link_or_path") or row.get("folder_path"), "Job folder link not available.")
    render_document_access("Open Proposal / Estimate", row.get("proposal_file") or row.get("estimate_file"), "Proposal / estimate link not available.")
    render_document_access("Open Contract", row.get("contract_file"), "Contract link not available.")
    render_document_access("Open Job Tracking Form", row.get("job_tracking_file"), "Job tracking form link not available.")


def dataframe_to_excel_bytes(df: pd.DataFrame, sheet_name: str = "Jobs") -> bytes:
    output = BytesIO()
    safe_sheet_name = re.sub(r"[\[\]\:*?/\\]", "_", sheet_name or "Jobs")[:31] or "Jobs"
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=safe_sheet_name)
    output.seek(0)
    return output.read()


def job_board_export_rows(rows: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(rows, pd.DataFrame) or rows.empty:
        return pd.DataFrame()
    export = rows.copy()
    for column in ["proposal_created_at", "estimate_created_at", "proposal_modified_at", "estimate_modified_at"]:
        if column in export.columns:
            export[column] = pd.to_datetime(export[column], errors="coerce").dt.date.astype("string")
    columns = [
        "closed_did_not_get",
        "review_mark_contracted",
        "review_mark_completed",
        "folder_pipeline_bucket",
        "opportunity_freshness",
        "proposal_status_flag",
        "proposal_age_days",
        "proposal_created_at",
        "proposal_modified_at",
        "proposal_modified_by",
        "estimate_created_at",
        "estimate_modified_at",
        "estimate_modified_by",
        "customer_display",
        "project",
        "division",
        "sales_stage",
        "sales_value",
        "project_category",
        "estimator_display",
        "lead_source_display",
        "workflow_status",
        "pipeline_status",
        "status",
        "follow_up_date",
        "priority",
        "folder",
        "job_id",
    ]
    return export[[column for column in columns if column in export.columns]]


JOB_BOARD_REVIEW_CHECKBOX_FIELDS = ["closed_did_not_get", "review_mark_contracted", "review_mark_completed"]


def workflow_status_from_review_marks(original_row: dict[str, Any], marks: dict[str, bool]) -> object:
    if marks.get("review_mark_completed"):
        return "Completed"
    if marks.get("review_mark_contracted"):
        return "Contracted"
    if marks.get("closed_did_not_get"):
        return "Closed Lost"
    current = original_row.get("workflow_status")
    if normalize_board_status(current) in {"Closed Lost", "Contracted", "Completed"}:
        return first_nonblank(original_row.get("pipeline_status"), original_row.get("status"), "Lead Received")
    return current


def save_job_board_review_edits(original_rows: pd.DataFrame, edited_rows: pd.DataFrame) -> int:
    if not isinstance(original_rows, pd.DataFrame) or not isinstance(edited_rows, pd.DataFrame):
        return 0
    if original_rows.empty or edited_rows.empty or "job_id" not in original_rows.columns or "job_id" not in edited_rows.columns:
        return 0
    original_lookup = {
        text_value(row.get("job_id")): row
        for row in original_rows.to_dict(orient="records")
        if text_value(row.get("job_id"))
    }
    saved = 0
    for row in edited_rows.to_dict(orient="records"):
        job_id = text_value(row.get("job_id"))
        if not job_id:
            continue
        original_row = original_lookup.get(job_id, {})
        marks = {
            field: truthy_bool(row.get(field, original_row.get(field)))
            for field in JOB_BOARD_REVIEW_CHECKBOX_FIELDS
        }
        changed = any(truthy_bool(original_row.get(field)) != marks[field] for field in JOB_BOARD_REVIEW_CHECKBOX_FIELDS)
        if not changed:
            continue
        save_job_workflow_override(
            job_id=job_id,
            workflow_status=workflow_status_from_review_marks(original_row, marks),
            deal_owner=original_row.get("deal_owner"),
            assigned_user=original_row.get("assigned_user"),
            follow_up_date=original_row.get("follow_up_date"),
            priority=original_row.get("priority"),
            internal_notes=original_row.get("internal_notes"),
            closed_did_not_get=marks["closed_did_not_get"],
            review_mark_contracted=marks["review_mark_contracted"],
            review_mark_completed=marks["review_mark_completed"],
            updated_by=os.getenv("USER"),
        )
        saved += 1
    return saved


def parsed_date_or_today(value: object) -> date:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return date.today()
    return parsed.date()


def job_board_page() -> None:
    st.title("Job Board")
    st.caption("VSimple-style pipeline view built from Spray-Tec job data.")
    # TODO: add activity stream/comments.
    # TODO: add true drag/drop kanban if moving away from Streamlit.
    # TODO: connect VSimple export/API if available.

    jobs = load_job_board_df()
    if jobs.empty:
        show_empty("No jobs are available for the board.")
        return

    for column in JOB_BOARD_FIELDS:
        if column not in jobs.columns:
            jobs[column] = None
    for column in [
        "workflow_status",
        "deal_owner",
        "assigned_user",
        "follow_up_date",
        "priority",
        "closed_did_not_get",
        "review_mark_contracted",
        "review_mark_completed",
        "internal_notes",
        "updated_by",
        "updated_at",
        "assigned_crew_leader",
        "estimated_start_date",
        "estimated_end_date",
        "estimated_duration_days",
        "estimated_labor_hours",
        "estimated_crew_size",
        "schedule_status",
        "schedule_priority",
        "blocking_issue",
        "schedule_notes",
        "warning_count",
        "warning_summary",
        "proposal_created_at",
        "estimate_created_at",
        "proposal_modified_at",
        "estimate_modified_at",
        "proposal_modified_by",
        "estimate_modified_by",
        "proposal_date_for_stale",
        "proposal_age_days",
        "proposal_stale",
        "proposal_status_flag",
        "folder_pipeline_bucket",
        "opportunity_freshness",
    ]:
        if column not in jobs.columns:
            jobs[column] = None

    jobs["job_id"] = jobs["job_id"].fillna("").astype(str)
    for checkbox_column in JOB_BOARD_REVIEW_CHECKBOX_FIELDS:
        jobs[checkbox_column] = jobs[checkbox_column].apply(truthy_bool)
    jobs["folder_pipeline_bucket"] = jobs.apply(folder_pipeline_bucket_for_row, axis=1)
    jobs["opportunity_freshness"] = jobs.apply(job_board_freshness_for_row, axis=1)
    terminal_folder_buckets = ["Closed Lost Folder", "Contracted Folder", "Completed Folder"]
    overall_folder_excluded_count = int(jobs["folder_pipeline_bucket"].isin(terminal_folder_buckets).sum())
    jobs["board_status"] = jobs.apply(board_status_for_row, axis=1)
    selected_job_id = str(st.session_state.get("selected_job_board_job_id", "") or "")
    if selected_job_id:
        st.caption(f"Selected job_id: {selected_job_id}")

    if st.checkbox("Show Job Board diagnostics", value=False, key="job_board_show_diagnostics"):
        with st.expander("Job Board status debug", expanded=True):
            cols = [column for column in ["workflow_status", "pipeline_status", "status", "board_status"] if column in jobs.columns]
            st.write(jobs[cols].head(50))
            st.write(jobs["board_status"].value_counts(dropna=False))

    st.subheader("Filters")
    f1, f2, f3, f4 = st.columns(4)
    with f1:
        search = st.text_input("Search jobs / customers / addresses", key="job_board_search").strip()
    with f2:
        division_filter = st.multiselect("Division", options_from(jobs, "division"), key="job_board_division")
    with f3:
        pipeline_filter = st.multiselect("Pipeline Status", options_from(jobs, "pipeline_status"), key="job_board_pipeline")
    with f4:
        status_filter = st.multiselect("Status", options_from(jobs, "status"), key="job_board_status")

    f5, f6, f7, f8 = st.columns(4)
    with f5:
        crew_filter = st.multiselect("Crew Leader", options_from(jobs, "assigned_crew_leader"), key="job_board_crew")
    with f6:
        workflow_filter = st.multiselect("Workflow Status", options_from(jobs, "workflow_status"), key="job_board_workflow_status")
    with f7:
        priority_filter = st.multiselect("Priority", options_from(jobs, "priority"), key="job_board_priority")
    with f8:
        hide_completed = st.checkbox("Hide completed/invoiced jobs", value=True, key="job_board_hide_completed")
    show_action_only = st.checkbox("Show only jobs needing action", key="job_board_action_only")

    filtered = jobs.copy()
    if search:
        search_cols = [column for column in ["job_name", "customer", "site_address", "city", "state", "zip_code"] if column in filtered.columns]
        mask = pd.Series(False, index=filtered.index)
        for column in search_cols:
            mask = mask | filtered[column].fillna("").astype(str).str.contains(search, case=False, na=False)
        filtered = filtered[mask]
    for selected, column in (
        (division_filter, "division"),
        (pipeline_filter, "pipeline_status"),
        (status_filter, "status"),
        (crew_filter, "assigned_crew_leader"),
        (workflow_filter, "workflow_status"),
        (priority_filter, "priority"),
    ):
        if selected and column in filtered.columns:
            filtered = filtered[filtered[column].astype(str).isin(selected)]
    if show_action_only:
        warning_mask = numeric_series(filtered, "warning_count").fillna(0) > 0
        warning_text_mask = filtered["warnings"].fillna("").astype(str).str.strip().ne("") if "warnings" in filtered.columns else False
        blocking_mask = filtered["blocking_issue"].fillna("").astype(str).str.strip().ne("") if "blocking_issue" in filtered.columns else False
        filtered = filtered[warning_mask | warning_text_mask | blocking_mask]
    if hide_completed:
        status_text = (
            filtered.get("workflow_status", pd.Series("", index=filtered.index)).fillna("").astype(str)
            + " "
            + filtered.get("pipeline_status", pd.Series("", index=filtered.index)).fillna("").astype(str)
            + " "
            + filtered.get("status", pd.Series("", index=filtered.index)).fillna("").astype(str)
        )
        filtered = filtered[~status_text.str.contains("completed|invoiced", case=False, na=False)]

    if st.session_state.get("job_board_show_diagnostics"):
        with st.expander("Job Board selection debug"):
            st.write("selected_job_board_job_id", st.session_state.get("selected_job_board_job_id"))
            st.write("Job IDs sample", jobs["job_id"].head(20).tolist())
            st.write("Filtered rows", len(filtered))

    metric_row(
        [
            ("Total Jobs Shown", fmt_count(len(filtered))),
            ("Total Estimated Value", fmt_dollar(safe_sum(filtered, "estimated_value"))),
            ("Proposed Value", fmt_dollar(status_value(filtered, "proposed"))),
            ("Contracted / Backlog Value", fmt_dollar(status_value(filtered, "contracted"))),
            ("Stale Proposals", fmt_count(filtered.get("proposal_stale", pd.Series(False, index=filtered.index)).fillna(False).astype(bool).sum())),
            ("Terminal Folders", fmt_count(filtered.get("folder_pipeline_bucket", pd.Series("", index=filtered.index)).isin(terminal_folder_buckets).sum())),
            ("Warnings / Action Items", fmt_count((numeric_series(filtered, "warning_count").fillna(0) > 0).sum())),
        ]
    )

    dashboard_rows = prepare_job_board_dashboard_rows(filtered)
    for checkbox_column in JOB_BOARD_REVIEW_CHECKBOX_FIELDS:
        if checkbox_column in dashboard_rows.columns:
            dashboard_rows[checkbox_column] = dashboard_rows[checkbox_column].apply(truthy_bool)
    if "folder_pipeline_bucket" not in dashboard_rows.columns:
        dashboard_rows["folder_pipeline_bucket"] = dashboard_rows.apply(folder_pipeline_bucket_for_row, axis=1)
    if "opportunity_freshness" not in dashboard_rows.columns:
        dashboard_rows["opportunity_freshness"] = dashboard_rows.apply(job_board_freshness_for_row, axis=1)
    dashboard_rows["material_system_warranty"] = dashboard_rows.apply(
        lambda row: " / ".join(
            part
            for part in [
                text_value(row.get("material_system_display")),
                text_value(row.get("warranty_display")),
            ]
            if part and part != "Not Captured"
        )
        or "Not Captured",
        axis=1,
    )

    job_board_default_columns = [
        "project",
        "project_category",
        "sales_stage",
        "sales_value",
        "opportunity_freshness",
        "proposal_modified_at",
        "proposal_modified_by",
        "substrate_display",
        "material_system_display",
        "warranty_display",
        "labor_plan",
        "win_loss_status",
        "completion_date_display",
        "lead_source_display",
        "readiness_status",
        "schedule_health",
        "production_risk_summary",
        "folder",
        "customer_display",
        "estimate_modified_at",
        "estimate_modified_by",
    ]
    job_board_table_columns = unique_columns(
        [
            *job_board_default_columns,
            "material_system_warranty",
            "estimator_display",
            "estimated_start_date",
            "proposal_status_flag",
            "folder_pipeline_bucket",
            "proposal_created_at",
            "estimate_created_at",
            "closed_did_not_get",
            "review_mark_contracted",
            "review_mark_completed",
            "warranty_type",
            "warranty_years",
            "material_system",
            "product_system",
            "substrate",
            "roof_type",
            "building_type",
        ]
    )
    job_board_default_hidden_columns = set(job_board_table_columns) - set(job_board_default_columns)
    job_board_default_hidden_columns.update(
        {
            "proposal_status_flag",
            "folder_pipeline_bucket",
            "proposal_created_at",
            "estimate_created_at",
            "closed_did_not_get",
            "review_mark_contracted",
            "review_mark_completed",
        }
    )
    job_board_column_labels = {
        "project": "Project",
        "project_category": "Project Category",
        "sales_stage": "Sales Stage",
        "sales_value": "Value",
        "opportunity_freshness": "Freshness",
        "proposal_modified_at": "Proposal Modified",
        "proposal_modified_by": "Proposal Modified By",
        "substrate_display": "Substrate",
        "material_system_warranty": "Material / Warranty",
        "win_loss_status": "Win / Loss",
        "completion_date_display": "Completion Date",
        "estimator_display": "Estimator",
        "lead_source_display": "Lead Source",
        "readiness_status": "Readiness",
        "schedule_health": "Schedule Health",
        "estimated_start_date": "Start",
        "labor_plan": "Labor Plan",
        "production_risk_summary": "Production Risk",
        "folder": "Folder",
        "customer_display": "Customer",
        "division": "Division",
        "proposal_status_flag": "Proposal Status",
        "proposal_created_at": "Proposal Created",
        "estimate_created_at": "Estimate Created",
        "estimate_modified_at": "Estimate Modified",
        "estimate_modified_by": "Estimate Modified By",
        "closed_did_not_get": "Closed / Did Not Get",
        "review_mark_contracted": "Contracted Review",
        "review_mark_completed": "Completed Review",
        "folder_pipeline_bucket": "Folder Rule",
        "material_system_display": "Material System",
        "warranty_display": "Warranty",
    }
    available_job_board_columns = [column for column in job_board_table_columns if column in dashboard_rows.columns]
    default_job_board_columns = [column for column in available_job_board_columns if column not in job_board_default_hidden_columns]

    st.subheader("Job Board Table")
    show_table(
        dashboard_rows,
        available_job_board_columns,
        height=520,
        sort_by="sales_value",
        row_style_column="opportunity_freshness",
        row_style_colors=JOB_BOARD_FRESHNESS_COLORS,
        column_labels=job_board_column_labels,
        default_visible_columns=default_job_board_columns,
    )

    review_dashboard_rows = dashboard_rows[dashboard_rows.apply(is_proposal_pipeline_review_row, axis=1)].copy()
    review_rows = job_board_export_rows(review_dashboard_rows)
    if not review_rows.empty:
        st.subheader("Proposal / Estimate Follow-Up Review")
        excluded_folder_count = int(
            dashboard_rows.get("folder_pipeline_bucket", pd.Series("", index=dashboard_rows.index))
            .isin(terminal_folder_buckets)
            .sum()
        )
        st.caption(
            "Did Not Get, Cancelled, Contracted, and Completed folders are excluded from this proposal-pipeline review list. "
            f"{excluded_folder_count:,} job{'s' if excluded_folder_count != 1 else ''} were excluded in the current filtered view; "
            f"{overall_folder_excluded_count:,} job{'s' if overall_folder_excluded_count != 1 else ''} are excluded across the loaded job board. "
            "Use the checkboxes for remaining jobs that need to be moved or marked after estimator review. "
            "Rows marked Estimate, No Proposal are active follow-up items."
        )
        edited_review_rows = st.data_editor(
            review_rows,
            column_order=[
                column
                for column in [
                    "closed_did_not_get",
                    "review_mark_contracted",
                    "review_mark_completed",
                    "folder_pipeline_bucket",
                    "opportunity_freshness",
                    "proposal_status_flag",
                    "proposal_age_days",
                    "proposal_created_at",
                    "proposal_modified_at",
                    "proposal_modified_by",
                    "estimate_created_at",
                    "estimate_modified_at",
                    "estimate_modified_by",
                    "customer_display",
                    "project",
                    "sales_stage",
                    "sales_value",
                    "estimator_display",
                    "follow_up_date",
                    "priority",
                    "folder",
                    "job_id",
                ]
                if column in review_rows.columns
            ],
            hide_index=True,
            width="stretch",
            height=360,
            disabled=[column for column in review_rows.columns if column not in JOB_BOARD_REVIEW_CHECKBOX_FIELDS],
            column_config={
                "closed_did_not_get": st.column_config.CheckboxColumn("Closed / Did Not Get"),
                "review_mark_contracted": st.column_config.CheckboxColumn("Contracted"),
                "review_mark_completed": st.column_config.CheckboxColumn("Completed"),
                "folder_pipeline_bucket": st.column_config.TextColumn("Folder Rule"),
                "opportunity_freshness": st.column_config.TextColumn("Freshness"),
                "proposal_status_flag": st.column_config.TextColumn("Proposal Status"),
                "proposal_age_days": st.column_config.NumberColumn("Proposal Age Days", format="%d"),
                "proposal_created_at": st.column_config.TextColumn("Proposal Created"),
                "proposal_modified_at": st.column_config.TextColumn("Proposal Modified"),
                "proposal_modified_by": st.column_config.TextColumn("Proposal Modified By"),
                "estimate_created_at": st.column_config.TextColumn("Estimate Created"),
                "estimate_modified_at": st.column_config.TextColumn("Estimate Modified"),
                "estimate_modified_by": st.column_config.TextColumn("Estimate Modified By"),
                "customer_display": st.column_config.TextColumn("Customer"),
                "project": st.column_config.TextColumn("Project", width="large"),
                "sales_stage": st.column_config.TextColumn("Sales Stage"),
                "sales_value": st.column_config.NumberColumn("Value", format="$%.0f"),
                "estimator_display": st.column_config.TextColumn("Estimator"),
                "folder": st.column_config.LinkColumn("Folder"),
            },
            key="job_board_closeout_review_editor",
        )
        export_cols = st.columns([1, 1, 2])
        with export_cols[0]:
            if st.button("Save Review Checks", type="primary", key="save_job_board_closeout_checks"):
                try:
                    saved = save_job_board_review_edits(dashboard_rows, edited_review_rows)
                    if saved:
                        st.success(f"Saved {saved:,} review update{'s' if saved != 1 else ''}.")
                        st.rerun()
                    else:
                        st.info("No review changes detected.")
                except Exception as exc:
                    show_database_error(exc)
        with export_cols[1]:
            export_df = job_board_export_rows(review_dashboard_rows)
            st.download_button(
                "Export Excel",
                data=dataframe_to_excel_bytes(export_df, "Job Board"),
                file_name="job_board_proposal_closeout_review.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="download_job_board_closeout_excel",
            )
        with export_cols[2]:
            uploaded_closeout_excel = st.file_uploader(
                "Import marked Excel",
                type=["xlsx"],
                key="upload_job_board_closeout_excel",
                help="Requires job_id and closed_did_not_get columns from the exported workbook.",
            )
            if uploaded_closeout_excel is not None:
                try:
                    imported_closeout = pd.read_excel(uploaded_closeout_excel)
                    if "job_id" not in imported_closeout.columns or not any(column in imported_closeout.columns for column in JOB_BOARD_REVIEW_CHECKBOX_FIELDS):
                        st.error("Imported workbook must include job_id and at least one review checkbox column.")
                    elif st.button("Apply Imported Closeout Checks", key="apply_imported_job_board_closeout"):
                        saved = save_job_board_review_edits(dashboard_rows, imported_closeout)
                        if saved:
                            st.success(f"Imported {saved:,} review update{'s' if saved != 1 else ''}.")
                            st.rerun()
                        else:
                            st.info("No imported review changes detected.")
                except Exception as exc:
                    show_database_error(exc)

    if not dashboard_rows.empty and "job_id" in dashboard_rows.columns:
        selectable_rows = dashboard_rows[dashboard_rows["job_id"].fillna("").astype(str).str.strip().ne("")]
        if not selectable_rows.empty:
            row_lookup = {
                text_value(row.get("job_id")): row
                for row in selectable_rows.to_dict(orient="records")
                if text_value(row.get("job_id"))
            }

            def job_board_select_label(job_id: str) -> str:
                row = row_lookup.get(job_id, {})
                value = format_summary_value(row.get("sales_value"), kind="money")
                return f"{text_value(row.get('customer_display'))} - {text_value(row.get('project'))} ({value})"

            selected_option = st.selectbox(
                "Open Job Detail",
                [""] + list(row_lookup.keys()),
                format_func=lambda value: "Select a job" if not value else job_board_select_label(value),
                key="job_board_table_selected_job",
            )
            if selected_option and selected_option != selected_job_id:
                st.session_state["selected_job_board_job_id"] = selected_option
                st.rerun()

    if st.checkbox("Show pipeline board cards", value=False, key="job_board_show_cards"):
        st.subheader("Pipeline Board")
        available_statuses = list(jobs["board_status"].dropna().unique())
        ordered_statuses = [status for status in JOB_BOARD_STATUS_ORDER if status in available_statuses]
        ordered_statuses.extend(sorted(status for status in available_statuses if status not in JOB_BOARD_STATUS_ORDER))
        for board_status in ordered_statuses:
            column_df = filtered[filtered["board_status"] == board_status].sort_values("estimated_value", ascending=False, na_position="last")
            if column_df.empty:
                continue
            with st.expander(f"{board_status}: {len(column_df):,} jobs | {fmt_dollar(safe_sum(column_df, 'estimated_value'))}"):
                compact_cards = prepare_job_board_dashboard_rows(column_df)
                show_table(
                    compact_cards,
                    [
                        "customer_display",
                        "project",
                        "sales_value",
                        "project_category",
                        "readiness_status",
                        "schedule_health",
                        "estimator_display",
                        "folder",
                    ],
                    height=260,
                    sort_by="sales_value",
                )

    if selected_job_id:
        selected_rows = jobs[jobs["job_id"].astype(str) == selected_job_id]
        if selected_rows.empty:
            st.warning("Selected job was not found in the current job data. It may be hidden by filters.")
            if st.button("Clear selected job", key="clear_selected_job_board_job"):
                del st.session_state["selected_job_board_job_id"]
                st.rerun()
            return
        row = selected_rows.iloc[0]
        prepared_detail = prepare_job_board_dashboard_rows(pd.DataFrame([row.to_dict()]))
        display_row = prepared_detail.iloc[0] if not prepared_detail.empty else row
        st.divider()
        st.header("Job Detail")
        detail_cols = st.columns(3)
        detail_items = [
            ("Project", display_row.get("project"), "text"),
            ("Customer", display_row.get("customer_display"), "text"),
            ("Division", row.get("division"), "text"),
            ("Sales Stage", display_row.get("sales_stage"), "text"),
            ("Win / Loss", display_row.get("win_loss_status"), "text"),
            ("Closed / Did Not Get", bool_label(row.get("closed_did_not_get")), "text"),
            ("Review Mark Contracted", bool_label(row.get("review_mark_contracted")), "text"),
            ("Review Mark Completed", bool_label(row.get("review_mark_completed")), "text"),
            ("Folder Rule", row.get("folder_pipeline_bucket"), "text"),
            ("Proposal Created", row.get("proposal_created_at"), "text"),
            ("Proposal Modified", row.get("proposal_modified_at"), "text"),
            ("Proposal Modified By", row.get("proposal_modified_by"), "text"),
            ("Estimate Created", row.get("estimate_created_at"), "text"),
            ("Estimate Modified", row.get("estimate_modified_at"), "text"),
            ("Estimate Modified By", row.get("estimate_modified_by"), "text"),
            ("Proposal Status", row.get("proposal_status_flag"), "text"),
            ("Priority", row.get("priority"), "text"),
            ("Follow Up Date", row.get("follow_up_date"), "text"),
            ("Estimator / Owner", display_row.get("estimator_display"), "text"),
            ("Lead Source", display_row.get("lead_source_display"), "text"),
            ("Assigned User", row.get("assigned_user"), "text"),
            ("Pipeline Status", row.get("pipeline_status"), "text"),
            ("Status", row.get("status"), "text"),
            ("Project Category", display_row.get("project_category"), "text"),
            ("Substrate", display_row.get("substrate_display"), "text"),
            ("Material / System", display_row.get("material_system_display"), "text"),
            ("Warranty", display_row.get("warranty_display"), "text"),
            ("Address", " ".join(part for part in [text_value(row.get("site_address")), text_value(row.get("city")), text_value(row.get("state")), text_value(row.get("zip_code"))] if part), "text"),
            ("Estimated / Proposal Value", display_row.get("sales_value"), "money"),
            ("Estimated Sq Ft", row.get("estimated_sqft"), "number"),
            ("Price / Sq Ft", row.get("price_per_sqft"), "money"),
            ("Final Price", row.get("final_price"), "money"),
            ("Invoice Amount", row.get("invoice_amount"), "money"),
            ("Completion Date", display_row.get("completion_date_display"), "text"),
            ("Assigned Crew Leader", row.get("assigned_crew_leader"), "text"),
            ("Scheduled Start", row.get("estimated_start_date"), "text"),
            ("Scheduled End", row.get("estimated_end_date"), "text"),
            ("Labor Plan", display_row.get("labor_plan"), "text"),
            ("Warnings", row.get("warning_summary") or row.get("warnings"), "text"),
            ("Last Scanned At", row.get("last_scanned_at"), "text"),
        ]
        for index, (label, value, kind) in enumerate(detail_items):
            with detail_cols[index % 3]:
                st.write(f"**{label}:** {format_summary_value(value, kind=kind)}")

        render_job_board_documents(row)

        st.subheader("Edit Workflow")
        st.caption("These workflow edits are stored in the app and do not overwrite SharePoint scan data.")
        job_key = hashlib.sha1(str(selected_job_id).encode("utf-8")).hexdigest()[:12]
        workflow_value = normalize_board_status(row.get("workflow_status") or row.get("pipeline_status") or row.get("status"))
        workflow_options = JOB_BOARD_STATUS_ORDER
        workflow_index = workflow_options.index(workflow_value) if workflow_value in workflow_options else workflow_options.index("Other")
        priority_value = text_value(row.get("priority")) or "Normal"
        priority_index = JOB_WORKFLOW_PRIORITY_OPTIONS.index(priority_value) if priority_value in JOB_WORKFLOW_PRIORITY_OPTIONS else JOB_WORKFLOW_PRIORITY_OPTIONS.index("Normal")
        with st.form(f"job_workflow_form_{job_key}"):
            edit_cols = st.columns(3)
            with edit_cols[0]:
                workflow_status = st.selectbox(
                    "Workflow Status",
                    workflow_options,
                    index=workflow_index,
                    key=f"job_workflow_status_{job_key}",
                )
                deal_owner = st.text_input("Deal Owner", value=text_value(row.get("deal_owner")), key=f"job_deal_owner_{job_key}")
            with edit_cols[1]:
                assigned_user = st.text_input("Assigned User", value=text_value(row.get("assigned_user")), key=f"job_assigned_user_{job_key}")
                follow_up_date = st.date_input(
                    "Follow Up Date",
                    value=parsed_date_or_today(row.get("follow_up_date")),
                    key=f"job_follow_up_date_{job_key}",
                )
            with edit_cols[2]:
                priority = st.selectbox(
                    "Priority",
                    JOB_WORKFLOW_PRIORITY_OPTIONS,
                    index=priority_index,
                    key=f"job_workflow_priority_{job_key}",
                )
                closed_did_not_get = st.checkbox(
                    "Closed / Did Not Get",
                    value=truthy_bool(row.get("closed_did_not_get")),
                    key=f"job_closed_did_not_get_{job_key}",
                )
                review_mark_contracted = st.checkbox(
                    "Contracted",
                    value=truthy_bool(row.get("review_mark_contracted")),
                    key=f"job_review_mark_contracted_{job_key}",
                )
                review_mark_completed = st.checkbox(
                    "Completed",
                    value=truthy_bool(row.get("review_mark_completed")),
                    key=f"job_review_mark_completed_{job_key}",
                )
            internal_notes = st.text_area(
                "Internal Notes",
                value=text_value(row.get("internal_notes")),
                height=120,
                key=f"job_internal_notes_{job_key}",
            )
            if st.form_submit_button("Save Workflow Changes"):
                try:
                    save_job_workflow_override(
                        job_id=selected_job_id,
                        workflow_status=workflow_status,
                        deal_owner=deal_owner,
                        assigned_user=assigned_user,
                        follow_up_date=follow_up_date,
                        priority=priority,
                        internal_notes=internal_notes,
                        closed_did_not_get=closed_did_not_get,
                        review_mark_contracted=review_mark_contracted,
                        review_mark_completed=review_mark_completed,
                        updated_by=os.getenv("USER"),
                    )
                    st.success("Workflow updated")
                    st.rerun()
                except Exception as exc:
                    show_database_error(exc)

        st.subheader("Operational Context")
        operational_items = [
            ("Signed Contract", bool_label(row.get("has_signed_contract"))),
            ("Invoice", bool_label(row.get("has_invoice"))),
            ("Warranty", bool_label(row.get("has_warranty"))),
            ("Job Spec", bool_label(row.get("has_job_spec"))),
            ("Aerial", bool_label(row.get("has_aerial"))),
            ("Photo Count", format_summary_value(row.get("photo_count"), kind="number")),
            ("Warnings", text_value(row.get("warning_summary")) or text_value(row.get("warnings")) or "-"),
        ]
        for label, value in operational_items:
            st.write(f"**{label}:** {value}")

        st.subheader("Scheduling Context")
        schedule_items = [
            ("Crew Leader", row.get("assigned_crew_leader")),
            ("Start Date", row.get("estimated_start_date")),
            ("End Date", row.get("estimated_end_date")),
            ("Duration Days", row.get("estimated_duration_days")),
            ("Labor Hours", row.get("estimated_labor_hours")),
            ("Crew Size", row.get("estimated_crew_size")),
            ("Schedule Status", row.get("schedule_status")),
            ("Schedule Priority", row.get("schedule_priority")),
            ("Blocking Issue", row.get("blocking_issue")),
            ("Schedule Notes", row.get("schedule_notes")),
        ]
        for label, value in schedule_items:
            st.write(f"**{label}:** {text_value(value) or '-'}")
        st.caption("Use Schedule Calendar to move this job.")

        st.subheader("Copy Job Summary")
        st.text_area("Copy into Teams/email", value=job_board_summary(row), height=180)


def timesheet_job_touches_page() -> None:
    st.title("Office Timesheet Job Touches")
    st.caption(
        "Office/admin/sales work by employee and job, matched back to the job board where timesheet project text is strong enough."
    )

    show_perf = bool(st.session_state.get("show_dashboard_perf_timings"))
    if show_perf:
        reset_dashboard_perf_timings()

    with dashboard_perf_step("timesheet cached activity load"):
        dashboard_data = load_timesheet_dashboard_activity()
    if show_perf and isinstance(dashboard_data, dict):
        for timing in dashboard_data.get("build_timings") or []:
            if isinstance(timing, dict):
                record_dashboard_perf_event(
                    f"cached build: {timing.get('name')}",
                    seconds=float(timing.get("seconds") or 0.0),
                    row_count=int(timing.get("row_count") or 0),
                )
    activity_all = dashboard_data.get("activity") if isinstance(dashboard_data, dict) else pd.DataFrame()
    timesheet_rows = int(dashboard_data.get("timesheet_rows") or 0) if isinstance(dashboard_data, dict) else 0
    job_rows = int(dashboard_data.get("job_rows") or 0) if isinstance(dashboard_data, dict) else 0
    if timesheet_rows <= 0:
        show_empty("No office timesheet entries are loaded.")
        return
    if job_rows <= 0:
        show_empty("No job board rows are loaded, so timesheets cannot be matched to jobs yet.")
        return

    if not isinstance(activity_all, pd.DataFrame) or activity_all.empty:
        show_empty("No timesheet activity rows are available.")
        return

    date_col1, date_col2, date_col3, date_col4 = st.columns(4)
    min_date = activity_all["work_date_parsed"].dropna().min()
    max_date = activity_all["work_date_parsed"].dropna().max()
    default_end = max_date.date() if not pd.isna(max_date) else date.today()
    default_start = max(min_date.date(), default_end - timedelta(days=29)) if not pd.isna(min_date) else default_end - timedelta(days=29)
    with date_col1:
        start_date = st.date_input(
            "From",
            value=default_start,
            key="timesheet_touches_start_date",
        )
    with date_col2:
        end_date = st.date_input(
            "To",
            value=default_end,
            key="timesheet_touches_end_date",
        )
    with date_col3:
        employee_filter = st.multiselect("Employee", options_from(activity_all, "employee"), key="timesheet_touches_employee")
    with date_col4:
        code_filter = st.multiselect("Code", options_from(activity_all, "code"), key="timesheet_touches_code")

    filter_col1, filter_col2, filter_col3, filter_col4 = st.columns([1.2, 1.2, 1.2, 2.2])
    with filter_col1:
        division_filter = st.multiselect("Division", options_from(activity_all, "division"), key="timesheet_touches_division")
    with filter_col2:
        job_type_filter = st.multiselect("Job Type", options_from(activity_all, "job_type"), key="timesheet_touches_job_type")
    with filter_col3:
        status_filter = st.multiselect(
            "Match Status",
            ["Exact/Strong", "Strong", "Review", "Weak", "Unmatched"],
            default=["Exact/Strong", "Strong", "Review"],
            key="timesheet_touches_match_status",
        )
    with filter_col4:
        search = st.text_input("Search project/job/customer", key="timesheet_touches_search").strip()

    weight_col1, weight_col2 = st.columns([1.2, 1.2])
    with weight_col1:
        weighted_touch_scale = st.selectbox(
            "Value weighting",
            ["sqrt", "log", "linear"],
            index=0,
            key="timesheet_touches_value_weighting",
            help="Weights each employee/project/day touch by matched job value. Sqrt is the default because raw linear dollars can make large jobs dominate.",
        )
    with weight_col2:
        weighted_touch_top_n = st.slider(
            "Employees in weighted trend",
            min_value=3,
            max_value=15,
            value=8,
            key="timesheet_touches_weighted_employee_count",
        )

    with dashboard_perf_step("timesheet filter application", row_count=len(activity_all)):
        activity = activity_all.copy()
        if start_date:
            activity = activity[activity["work_date_parsed"].isna() | (activity["work_date_parsed"].dt.date >= start_date)]
        if end_date:
            activity = activity[activity["work_date_parsed"].isna() | (activity["work_date_parsed"].dt.date <= end_date)]
        if employee_filter:
            activity = activity[activity["employee"].astype(str).isin(employee_filter)]
        if code_filter:
            activity = activity[activity["code"].astype(str).isin(code_filter)]
        if division_filter and "division" in activity.columns:
            activity = activity[activity["division"].astype(str).isin(division_filter)]
        if job_type_filter and "job_type" in activity.columns:
            activity = activity[activity["job_type"].astype(str).isin(job_type_filter)]
        if status_filter:
            activity = activity[activity["match_status"].isin(status_filter)]
        if search:
            search_columns = [column for column in ["project_name", "customer", "job_name", "job_id", "notes", "code"] if column in activity.columns]
            mask = pd.Series(False, index=activity.index)
            for column in search_columns:
                mask = mask | activity[column].fillna("").astype(str).str.contains(search, case=False, na=False)
            activity = activity[mask]

    if activity.empty:
        show_empty("No timesheet rows match the current filters.")
        return

    with dashboard_perf_step("timesheet summary rollups", row_count=len(activity)):
        employee_summary = summarize_timesheet_by_employee(activity)
        job_rollup = summarize_timesheet_by_job(activity)
        code_summary = summarize_timesheet_by_code(activity)
        daily_summary = summarize_timesheet_daily_touches(activity)
        job_type_summary = summarize_timesheet_job_type_touches(job_rollup, activity)
        weighted_employee_touches = summarize_timesheet_employee_weighted_touches(
            activity,
            value_scale=weighted_touch_scale,
            top_employee_count=weighted_touch_top_n,
        )
    matched_rows = activity[activity["matched_job"]]
    unmatched_rows = activity[~activity["matched_job"]]
    metric_row(
        [
            ("Job Touches", fmt_count(len(activity))),
            ("Jobs Touched", fmt_count(job_rollup["job_id"].nunique() if not job_rollup.empty else 0)),
            ("Project Strings", fmt_count(activity["project_name"].nunique())),
            ("Employees", fmt_count(activity["employee"].nunique())),
            ("Timed Hours", f"{safe_sum(activity, 'duration_hours'):,.1f}"),
            ("Unmatched", fmt_count(len(unmatched_rows))),
        ]
    )

    chart_col1, chart_col2 = st.columns(2)
    with chart_col1:
        bar_chart(activity, "employee", "touch_count", "Touches by Employee", top_n=12)
    with chart_col2:
        bar_chart(code_summary, "code", "touch_count", "Touches by Work Code", top_n=12)
    chart_col3, chart_col4 = st.columns(2)
    with chart_col3:
        if job_type_summary.empty:
            show_empty("No matched jobs available for job type chart.")
        else:
            bar_chart(job_type_summary, "job_type", "touch_count", "Touches by Job Type", top_n=12)
    with chart_col4:
        if job_rollup.empty:
            show_empty("No matched jobs available for pipeline chart.")
        else:
            bar_chart(job_rollup, "pipeline_status", "touch_count", "Touches by Pipeline Status", top_n=10)
    if not daily_summary.empty:
        fig = px.line(
            daily_summary,
            x="activity_date",
            y="touch_count",
            markers=True,
            title="Daily Job Touches",
            labels={"activity_date": "date", "touch_count": "touches"},
        )
        st.plotly_chart(fig, width="stretch")
    if weighted_employee_touches.empty:
        show_empty("No dated employee project touches are available for the weighted touch trend.")
    else:
        fig = px.line(
            weighted_employee_touches,
            x="activity_date",
            y="weighted_touch_score",
            color="employee",
            markers=True,
            title="Value-Weighted Project Touches Over Time by Employee",
            labels={
                "activity_date": "date",
                "weighted_touch_score": "weighted touch score",
                "employee": "employee",
                "project_touch_count": "project touches",
                "job_value_touched": "job value touched",
                "projects": "projects",
                "customers": "customers",
                "codes": "codes",
            },
            hover_data={
                "project_touch_count": True,
                "job_value_touched": ":$,.0f",
                "source_line_count": True,
                "projects": True,
                "customers": True,
                "codes": True,
            },
        )
        st.plotly_chart(fig, width="stretch")
        st.caption(
            "Weighted score counts one touch per employee/project/day and weights it by matched job value. "
            f"Current scale: {weighted_touch_scale}; unknown-value projects receive a small weight."
        )

    tab_jobs, tab_employee, tab_codes, tab_activity, tab_review = st.tabs(
        ["Projects Moving", "By Employee", "By Code", "Recent Activity", "Match Review"]
    )
    with tab_jobs:
        st.subheader("Where Are We With This Project")
        if job_rollup.empty:
            st.caption("No matched jobs under the current filters.")
        else:
            show_table(
                job_rollup,
                [
                    "customer",
                    "job_name",
                    "division",
                    "job_type",
                    "project_type",
                    "pipeline_status",
                    "status",
                    "job_value",
                    "value_band",
                    "touch_count",
                    "total_hours",
                    "employee_count",
                    "employees",
                    "codes",
                    "last_touch",
                    "best_match_score",
                    "folder_link_or_path",
                ],
                height=520,
                sort_by="last_touch",
            )

    with tab_employee:
        st.subheader("Who Touched What")
        show_table(
            employee_summary,
            [
                "employee",
                "touch_count",
                "job_count",
                "project_string_count",
                "total_hours",
                "timed_entry_count",
                "activity_only_count",
                "last_touch",
                "codes",
                "recent_projects",
            ],
            height=420,
            sort_by="touch_count",
        )

    with tab_codes:
        st.subheader("What Kind of Work Is Happening")
        show_table(
            code_summary,
            [
                "code",
                "touch_count",
                "job_count",
                "project_string_count",
                "employee_count",
                "total_hours",
                "last_touch",
                "employees",
                "recent_projects",
            ],
            height=420,
            sort_by="touch_count",
        )

    with tab_activity:
        st.subheader("Recent Timesheet Activity")
        recent_activity = activity.sort_values("work_date_parsed", ascending=False, na_position="last")
        show_table(
            recent_activity,
            [
                "activity_date",
                "employee",
                "code",
                "project_name",
                "customer",
                "job_name",
                "division",
                "job_type",
                "pipeline_status",
                "duration_hours",
                "notes",
                "match_status",
                "match_score",
                "source_file",
            ],
            height=560,
        )

    with tab_review:
        st.subheader("Match Review")
        project_summary = office_timesheet_project_summary(activity)
        jobs = load_job_board_df()
        matched_projects = match_timesheet_projects_to_jobs(project_summary, jobs)
        show_table(
            matched_projects,
            [
                "match_status",
                "match_score",
                "project_name",
                "customer",
                "job_name",
                "division",
                "job_type",
                "pipeline_status",
                "touch_count",
                "total_hours",
                "employee_count",
                "last_touch",
                "codes",
                "employees",
                "latest_notes",
                "match_reason",
            ],
            height=420,
            sort_by="last_touch",
        )
        if not unmatched_rows.empty:
            st.subheader("Unmatched Touches")
            show_table(
                unmatched_rows,
                [
                    "activity_date",
                    "employee",
                    "project_name",
                    "code",
                    "duration_hours",
                    "notes",
                    "match_status",
                    "match_reason",
                ],
                height=320,
            )

    with st.expander("Matching Method and Limits"):
        st.markdown(
            """
- Timesheet rows do not carry job IDs, so this page matches free-form project names to job/customer/folder/address text.
- Job type, pipeline status, value, and folder links come from the matched job-board row.
- Activity-only timesheet rows count as touches but carry zero parsed hours.
- `Review`, `Weak`, and `Unmatched` matches should be treated as fuzzy until reviewed.
- The next improvement should persist reviewed match overrides for common shorthand.
            """.strip()
        )
    if show_perf:
        with st.expander("Performance timings", expanded=True):
            render_dashboard_perf_timings()


def owner_overview_page() -> None:
    st.title("Owner Overview")
    jobs = apply_basic_filters(query_view("dashboard_jobs"))
    top_open = apply_basic_filters(query_view("dashboard_top_open_jobs"))
    needing_action = apply_basic_filters(query_view("dashboard_jobs_needing_action_clean"))
    division_summary = apply_basic_filters(query_view("dashboard_division_summary"))

    if jobs.empty:
        show_empty()
        return

    metric_row(
        [
            ("Total Pipeline Value", money_metric(safe_sum(jobs, "estimated_value"))),
            ("Total Jobs", number_metric(len(jobs))),
            ("Jobs Needing Action", number_metric(len(needing_action))),
            ("Jobs With Warnings", number_metric(safe_count_true(jobs, "has_warnings"))),
            ("Proposed Value", money_metric(status_value(jobs, "proposed"))),
            ("Contracted Value", money_metric(status_value(jobs, "contracted"))),
            ("Completed Value", money_metric(status_value(jobs, "completed"))),
            ("Total Photos", number_metric(safe_sum(jobs, "photo_count"))),
        ]
    )

    c1, c2 = st.columns(2)
    with c1:
        chart_df = division_summary if not division_summary.empty and "total_estimated_value" in division_summary.columns else jobs
        bar_chart(
            chart_df,
            "division",
            "total_estimated_value" if "total_estimated_value" in chart_df.columns else "estimated_value",
            "Pipeline Value by Division",
        )
    with c2:
        bar_chart(jobs, "pipeline_status", "estimated_value", "Pipeline Value by Status")

    st.subheader("Top Open Jobs")
    show_table(
        top_open,
        [
            "customer",
            "job_name",
            "division",
            "pipeline_status",
            "status",
            "estimated_value",
            "price_per_sqft",
            "has_warnings",
            "folder_link_or_path",
        ],
        sort_by="estimated_value",
    )
    st.subheader("Jobs Needing Action")
    show_table(
        needing_action,
        [
            "action_needed",
            "customer",
            "job_name",
            "division",
            "pipeline_status",
            "status",
            "estimated_value",
            "warnings",
            "folder_link_or_path",
        ],
    )


def sales_dashboard_page() -> None:
    st.title("Sales Dashboard")
    st.caption("Sales rollups are inferred from current job, estimate, workflow, and pipeline fields. Missing estimator, lead-source, or outcome data is shown as Not Captured.")

    base_jobs = load_job_board_df()
    if not isinstance(base_jobs, pd.DataFrame):
        base_jobs = pd.DataFrame()
    jobs = normalize_sales_jobs(apply_basic_filters(base_jobs))
    if jobs.empty:
        show_empty("No sales jobs match the current filters.")
        return

    open_jobs = jobs[~jobs["sales_stage"].isin(["Closed Won", "Closed Lost"])]
    won_jobs = jobs[jobs["sales_stage"] == "Closed Won"]
    lost_jobs = jobs[jobs["sales_stage"] == "Closed Lost"]
    decided_count = len(won_jobs) + len(lost_jobs)
    win_rate = (len(won_jobs) / decided_count) if decided_count else None
    metric_row(
        [
            ("Open Pipeline", money_metric(open_jobs["sales_value"].sum())),
            ("Open Jobs", number_metric(len(open_jobs))),
            ("Closed Won Value", money_metric(won_jobs["sales_value"].sum())),
            ("Closed Lost Value", money_metric(lost_jobs["sales_value"].sum())),
            ("Win Rate", f"{win_rate:.0%}" if win_rate is not None else "-"),
            ("Missing Lead Source", number_metric((jobs["lead_source_display"] == "Not Captured").sum())),
        ]
    )

    st.subheader("Current Sales Pipeline")
    pipeline = sales_pipeline_rollup(jobs)
    show_table(pipeline, ["stage", "job_count", "value"], height=315)

    c1, c2 = st.columns(2)
    with c1:
        bar_chart(pipeline, "stage", "value", "Pipeline Value by Stage")
    with c2:
        bar_chart(jobs, "project_category", "sales_value", "Pipeline Value by Category", color="sales_stage")

    st.subheader("Sales Performance")
    performance_tabs = st.tabs(["By Estimator", "By Division", "By Category", "By Project Size"])
    performance_sources = [
        ("estimator_display", performance_tabs[0]),
        ("division", performance_tabs[1]),
        ("project_category", performance_tabs[2]),
        ("project_size", performance_tabs[3]),
    ]
    for group_column, tab in performance_sources:
        with tab:
            perf = sales_performance_rollup(jobs, group_column)
            show_table(
                perf,
                [
                    "category",
                    "win_rate",
                    "proposal_count",
                    "won_count",
                    "lost_count",
                    "open_count",
                    "proposal_value",
                    "won_value",
                ],
                height=320,
                sort_by="proposal_value",
            )

    st.subheader("Estimator Weekly KPI Proxy")
    st.caption("Until activity tracking is wired in, these counts use pipeline-stage evidence as a proxy for visits, proposals, follow-ups, and wins.")
    show_table(
        estimator_kpi_rollup(jobs),
        [
            "estimator",
            "site_visits",
            "site_visit_goal",
            "proposals_sent",
            "proposal_goal",
            "proposal_value",
            "proposal_value_goal",
            "followups_completed",
            "followup_goal",
            "contracts_won",
            "contracts_won_goal",
        ],
        height=360,
        sort_by="proposal_value",
    )

    st.subheader("Business Development / Lead Sources")
    show_table(
        lead_source_rollup(jobs),
        ["source", "job_count", "open_value", "revenue_won"],
        height=300,
        sort_by="revenue_won",
    )

    st.subheader("Sales Data Gaps")
    gap_mask = (
        (jobs["estimator_display"] == "Not Captured")
        | (jobs["lead_source_display"] == "Not Captured")
        | (jobs["sales_value"] <= 0)
        | (jobs["sales_stage"].fillna("").astype(str) == "Lead Received")
    )
    gap_df = jobs[gap_mask].copy()
    gap_df["gap_summary"] = gap_df.apply(
        lambda row: ", ".join(
            label
            for label, missing in [
                ("estimator", row.get("estimator_display") == "Not Captured"),
                ("lead source", row.get("lead_source_display") == "Not Captured"),
                ("value", float(row.get("sales_value") or 0) <= 0),
                ("pipeline stage", row.get("sales_stage") == "Lead Received"),
            ]
            if missing
        ),
        axis=1,
    )
    show_table(
        gap_df,
        [
            "gap_summary",
            "customer",
            "job_name",
            "division",
            "sales_stage",
            "sales_value",
            "estimator_display",
            "lead_source_display",
            "folder_link_or_path",
        ],
        height=340,
        sort_by="sales_value",
    )


def pipeline_money_page() -> None:
    st.title("Pipeline / Money")
    jobs = apply_basic_filters(query_view("dashboard_jobs"))
    value_bands = apply_basic_filters(query_view("dashboard_job_value_bands"))
    top_open = apply_basic_filters(query_view("dashboard_top_open_jobs"))

    if jobs.empty:
        show_empty()
        return

    metric_row(
        [
            ("Total Value", money_metric(safe_sum(jobs, "estimated_value"))),
            ("Proposed Value", money_metric(status_value(jobs, "proposed"))),
            ("Contracted Value", money_metric(status_value(jobs, "contracted"))),
            ("Average Job Value", money_metric(numeric_series(jobs, "estimated_value").mean())),
        ]
    )

    c1, c2 = st.columns(2)
    with c1:
        bar_chart(jobs, "division", "estimated_value", "Value by Division and Pipeline Status", color="pipeline_status")
    with c2:
        band_col = "value_band" if "value_band" in value_bands.columns else "job_value_band"
        count_col = "job_count" if "job_count" in value_bands.columns else None
        bar_chart(value_bands, band_col, count_col, "Job Count by Value Band")

    st.subheader("Top Open Jobs by Value")
    show_table(
        top_open,
        [
            "customer",
            "job_name",
            "division",
            "pipeline_status",
            "status",
            "estimated_value",
            "estimated_sqft",
            "price_per_sqft",
            "folder_link_or_path",
        ],
        sort_by="estimated_value",
    )


def sales_followup_page() -> None:
    st.title("Sales Follow-Up")
    followup = apply_basic_filters(load_df("SELECT * FROM dashboard_sales_followup"))
    value_bands = apply_basic_filters(query_view("dashboard_job_value_bands"))
    if "pipeline_status" in value_bands.columns:
        value_bands = value_bands[value_bands["pipeline_status"].fillna("").astype(str) == "Proposed"]

    if followup.empty:
        show_empty()
        return

    status_text = followup["followup_status"].fillna("").astype(str) if "followup_status" in followup.columns else pd.Series("", index=followup.index)
    metric_row(
        [
            ("Proposed Jobs", number_metric(len(followup))),
            ("Proposed Value", money_metric(safe_sum(followup, "estimated_value"))),
            ("Ready for Follow-Up", number_metric(status_text.str.contains("ready", case=False, na=False).sum())),
            ("Missing Estimate Value", number_metric(status_text.str.contains("estimated value", case=False, na=False).sum())),
            ("Missing Sq Ft", number_metric(status_text.str.contains("square footage", case=False, na=False).sum())),
            ("Missing Price/Sq Ft", number_metric(status_text.str.contains("price per sqft", case=False, na=False).sum())),
        ]
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        bar_chart(followup, "division", "estimated_value", "Proposed Value by Division")
    with c2:
        bar_chart(followup, "followup_status", None, "Proposed Jobs by Follow-Up Status")
    with c3:
        band_col = "value_band" if "value_band" in value_bands.columns else "job_value_band"
        value_col = "total_estimated_value" if "total_estimated_value" in value_bands.columns else "estimated_value"
        bar_chart(value_bands, band_col, value_col, "Proposed Value by Value Band")

    show_table(
        followup,
        [
            "followup_status",
            "customer",
            "job_name",
            "division",
            "estimated_value",
            "estimated_sqft",
            "price_per_sqft",
            "warnings",
            "folder_link_or_path",
        ],
        sort_by="estimated_value",
    )


def jobs_needing_action_page() -> None:
    st.title("Jobs Needing Action")
    df = apply_basic_filters(query_view("dashboard_jobs_needing_action_clean"))

    if df.empty:
        show_empty()
        return

    action_text = df["action_needed"].fillna("").astype(str) if "action_needed" in df.columns else pd.Series("", index=df.index)
    metric_row(
        [
            ("Action Items", number_metric(len(df))),
            ("Missing Invoice", number_metric(action_text.str.contains("invoice", case=False, na=False).sum())),
            ("Missing Final Price", number_metric(action_text.str.contains("final price", case=False, na=False).sum())),
            ("Missing Contract", number_metric(action_text.str.contains("contract", case=False, na=False).sum())),
        ]
    )
    bar_chart(df, "action_needed", None, "Action Items by Type")
    show_table(
        df,
        [
            "action_needed",
            "customer",
            "job_name",
            "division",
            "pipeline_status",
            "status",
            "estimated_value",
            "warnings",
            "folder_link_or_path",
        ],
        sort_by="estimated_value",
    )


def contracted_backlog_scheduling_page() -> None:
    st.title("Contracted Backlog / Scheduling")
    backlog = apply_basic_filters(query_view("dashboard_contracted_backlog"))
    summary = apply_basic_filters(load_df("SELECT * FROM dashboard_contracted_backlog_summary"))

    if backlog.empty:
        show_empty()
        return

    summary_source = summary if not summary.empty else backlog
    metric_row(
        [
            ("Contracted Jobs", number_metric(len(backlog))),
            ("Backlog Value", money_metric(safe_sum(backlog, "estimated_value"))),
            ("Estimated Labor Hours", number_metric(safe_sum(backlog, "estimated_labor_hours"))),
            ("Estimated Duration Days", number_metric(safe_sum(backlog, "estimated_duration_days"))),
            ("Jobs Missing Duration", number_metric(safe_sum(summary_source, "jobs_missing_duration"))),
            ("Jobs Missing Labor Hours", number_metric(safe_sum(summary_source, "jobs_missing_labor_hours"))),
        ]
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        chart_df = summary if not summary.empty else backlog
        bar_chart(chart_df, "division", "contracted_backlog_value" if "contracted_backlog_value" in chart_df.columns else "estimated_value", "Backlog Value by Division")
    with c2:
        chart_df = summary if not summary.empty else backlog
        bar_chart(chart_df, "division", "estimated_labor_hours", "Estimated Labor Hours by Division")
    with c3:
        if not summary.empty and {"division", "jobs_missing_duration", "jobs_missing_labor_hours", "jobs_missing_crew_size"}.issubset(summary.columns):
            missing_df = summary.melt(
                id_vars=["division"],
                value_vars=["jobs_missing_duration", "jobs_missing_labor_hours", "jobs_missing_crew_size"],
                var_name="missing_type",
                value_name="job_count",
            )
            fig = px.bar(missing_df, x="division", y="job_count", color="missing_type", title="Missing Duration / Labor / Crew Size by Division")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No data available for Missing Duration / Labor / Crew Size by Division.")

    show_table(
        backlog,
        [
            "customer",
            "job_name",
            "division",
            "pipeline_status",
            "status",
            "estimated_value",
            "estimated_duration_days",
            "estimated_labor_hours",
            "estimated_crew_size",
            "has_warnings",
            "warnings",
            "folder_link_or_path",
        ],
        sort_by="estimated_value",
    )


def operations_dashboard_page() -> None:
    st.title("Operations Dashboard")
    st.caption(
        "Production rollups use contracted backlog, schedule records, extracted estimate fields, and warning notes. "
        "QuickBooks-only metrics and true field progress are flagged where source data is not captured yet."
    )

    base_jobs = load_job_board_df()
    if not isinstance(base_jobs, pd.DataFrame):
        base_jobs = pd.DataFrame()
    all_jobs = normalize_sales_jobs(apply_basic_filters(base_jobs))
    schedule = load_schedule_df() if relation_columns("crew_schedule") else pd.DataFrame()
    backlog = apply_basic_filters(query_view("dashboard_contracted_backlog"))
    backlog_source = backlog if not backlog.empty else all_jobs
    ops = normalize_operations_jobs(backlog_source, schedule=schedule)

    if all_jobs.empty and ops.empty:
        show_empty("No operational jobs match the current filters.")
        return

    today = pd.Timestamp(date.today()).normalize()
    month_start = pd.Timestamp(date.today().replace(day=1))
    won_jobs = all_jobs[all_jobs["sales_stage"] == "Closed Won"] if not all_jobs.empty else pd.DataFrame()
    open_proposals = (
        all_jobs[all_jobs["sales_stage"].isin(["Proposal Submitted", "Follow-Up / Negotiation", "Contract Pending"])]
        if not all_jobs.empty
        else pd.DataFrame()
    )
    completed_recent = ops[
        (ops["schedule_health"] == "Completed")
        | (ops["completion_date"].notna() & (ops["completion_date"] >= (today - pd.Timedelta(days=30))))
    ] if not ops.empty and "completion_date" in ops.columns else pd.DataFrame()
    completed_mtd = completed_recent[
        completed_recent["completion_date"].notna() & (completed_recent["completion_date"] >= month_start)
    ] if not completed_recent.empty and "completion_date" in completed_recent.columns else pd.DataFrame()

    metric_row(
        [
            ("Contracted Backlog", money_metric(ops["operations_value"].sum()) if not ops.empty else "$0"),
            ("Sales Closed MTD", money_metric(won_jobs["sales_value"].sum()) if not won_jobs.empty else "$0"),
            ("Open Proposal Value", money_metric(open_proposals["sales_value"].sum()) if not open_proposals.empty else "$0"),
            ("Sq Ft Completed MTD", number_metric(safe_sum(completed_mtd, "estimated_sqft"))),
            ("Recently Completed", number_metric(len(completed_recent))),
            ("AR Over 60", "Needs QB"),
        ]
    )

    st.subheader("Operational KPI Coverage")
    coverage_rows = pd.DataFrame(
        [
            {"metric": "Revenue MTD vs Goal", "current_source": "QuickBooks goal not connected", "status": "Needs QB / goal source"},
            {"metric": "Gross Profit % MTD", "current_source": "Estimate costs available on some jobs", "status": "Needs QB actuals"},
            {"metric": "Labor Efficiency %", "current_source": "Estimated labor captured; actual hours not connected", "status": "Needs time tracking"},
            {"metric": "Material Usage vs Estimate", "current_source": "Estimated materials captured; actual usage not connected", "status": "Needs field/job-cost data"},
            {"metric": "AR Over 60 Days", "current_source": "Not in current dashboard marts", "status": "Needs QuickBooks"},
        ]
    )
    show_table(coverage_rows, ["metric", "current_source", "status"], height=230)

    st.subheader("Projects Waiting To Be Scheduled")
    if ops.empty:
        show_empty("No contracted backlog rows are available.")
    else:
        waiting = ops[ops["readiness_status"].isin(["Ready To Schedule", "Customer Hold", "Material Hold", "Permit Hold", "Weather Window"])].copy()
        waiting = waiting[waiting["estimated_start_date_parsed"].isna()]
        metric_row(
            [
                ("Ready Jobs", number_metric((waiting["readiness_status"] == "Ready To Schedule").sum())),
                ("Ready Value", money_metric(waiting.loc[waiting["readiness_status"] == "Ready To Schedule", "operations_value"].sum())),
                ("Hold Jobs", number_metric((waiting["readiness_status"] != "Ready To Schedule").sum())),
                ("Hold Value", money_metric(waiting.loc[waiting["readiness_status"] != "Ready To Schedule", "operations_value"].sum())),
            ]
        )
        show_table(readiness_summary(waiting), ["status", "jobs", "revenue", "avg_days_waiting"], height=250)
        show_table(
            waiting,
            [
                "customer",
                "job_name",
                "division",
                "project_category",
                "operations_value",
                "ready_date",
                "days_waiting",
                "readiness_status",
                "production_risk_summary",
                "estimated_duration_days",
                "estimated_labor_hours",
                "estimated_crew_size",
                "folder_link_or_path",
            ],
            height=400,
            sort_by="operations_value",
        )

    st.subheader("Production Status / Risk")
    active = ops[ops["readiness_status"].eq("Scheduled") | ops["estimated_start_date_parsed"].notna()].copy() if not ops.empty else pd.DataFrame()
    if not active.empty:
        c1, c2 = st.columns(2)
        with c1:
            bar_chart(active, "schedule_health", "operations_value", "Scheduled Value by Health")
        with c2:
            bar_chart(active, "assigned_crew_leader", "operations_value", "Scheduled Value by Crew")
    show_table(
        active,
        [
            "schedule_health",
            "customer",
            "job_name",
            "division",
            "operations_value",
            "assigned_crew_leader",
            "estimated_start_date",
            "estimated_end_date",
            "expected_pct_complete",
            "actual_pct_complete",
            "estimated_sqft",
            "estimated_labor_hours",
            "estimated_crew_size",
            "material_readiness",
            "equipment_readiness",
            "customer_communication",
            "production_risk_summary",
            "folder_link_or_path",
        ],
        height=430,
    )

    st.subheader("Jobs Starting Soon")
    if not ops.empty:
        starting_soon = ops[
            ops["estimated_start_date_parsed"].notna()
            & (ops["estimated_start_date_parsed"] >= today)
            & (ops["estimated_start_date_parsed"] <= today + pd.Timedelta(days=14))
        ].copy()
    else:
        starting_soon = pd.DataFrame()
    show_table(
        starting_soon,
        [
            "customer",
            "job_name",
            "division",
            "operations_value",
            "estimated_start_date",
            "assigned_crew_leader",
            "estimated_duration_days",
            "estimated_labor_hours",
            "material_readiness",
            "equipment_readiness",
            "customer_communication",
            "production_risk_summary",
            "folder_link_or_path",
        ],
        height=330,
    )

    st.subheader("Recently Completed / Warranty")
    completed = completed_recent.copy()
    if not completed.empty and "has_warranty" not in completed.columns:
        completed["has_warranty"] = "Not Captured"
    show_table(
        completed,
        [
            "customer",
            "job_name",
            "division",
            "operations_value",
            "completion_date",
            "has_warranty",
            "warranty_amount",
            "estimated_sqft",
            "project_category",
            "folder_link_or_path",
        ],
        height=330,
    )


def operations_scheduling_page() -> None:
    contracted_backlog_scheduling_page()


def schedule_calendar_page() -> None:
    st.title("Schedule Calendar")
    if calendar is None:
        st.error("streamlit-calendar is not installed. Run `pip install streamlit-calendar` and restart Streamlit.")
        return

    schedule_df = load_schedule_calendar_df()
    if schedule_df.empty:
        show_empty("No scheduled jobs have an estimated start date yet.")

    sidebar = st.sidebar
    sidebar.caption("Schedule Calendar Filters")
    crew_filter = sidebar.multiselect("Calendar Crew Leader", options_from(schedule_df, "assigned_crew_leader"))
    division_filter = sidebar.multiselect("Calendar Division", options_from(schedule_df, "division"))
    status_filter = sidebar.multiselect("Calendar Schedule Status", options_from(schedule_df, "schedule_status"))
    show_unscheduled = sidebar.checkbox("Show unscheduled contracted jobs", value=True)

    filtered = schedule_df.copy()
    if crew_filter and "assigned_crew_leader" in filtered.columns:
        filtered = filtered[filtered["assigned_crew_leader"].astype(str).isin(crew_filter)]
    if division_filter and "division" in filtered.columns:
        filtered = filtered[filtered["division"].astype(str).isin(division_filter)]
    if status_filter and "schedule_status" in filtered.columns:
        filtered = filtered[filtered["schedule_status"].astype(str).isin(status_filter)]

    events = calendar_events_from_schedule(filtered)
    calendar_options = {
        "initialView": "dayGridMonth",
        "editable": True,
        "eventStartEditable": True,
        "eventDurationEditable": True,
        "selectable": True,
        "height": 800,
        "headerToolbar": {
            "left": "prev,next today",
            "center": "title",
            "right": "dayGridMonth,timeGridWeek,timeGridDay,listWeek",
        },
        "eventDisplay": "block",
    }

    # TODO: add Teams send button from selected calendar day.
    # TODO: add weather delay/push schedule feature.
    # TODO: add true crew availability table.
    calendar_col, detail_col = st.columns([2, 1])
    with calendar_col:
        calendar_result = calendar(events=events, options=calendar_options, key="schedule_calendar")
        with st.expander("Calendar event debug"):
            st.write(calendar_result)

    change = parse_calendar_change(calendar_result)
    if change:
        update_calendar_schedule_dates(change["event_id"], change["start"], change.get("end"))
        st.success("Schedule updated")
        st.rerun()

    selected_event = find_calendar_event(calendar_result, events)
    with detail_col:
        st.subheader("Selected Job")
        if not selected_event:
            st.info("Click a scheduled job block to view and edit details.")
        else:
            props = selected_event.get("extendedProps", {})
            if not isinstance(props, dict):
                props = {}
            details = [
                ("Customer", props.get("customer")),
                ("Job Name", props.get("job_name")),
                ("Division", props.get("division")),
                ("Pipeline Status", props.get("pipeline_status")),
                ("Status", props.get("status")),
                ("Estimated Value", fmt_dollar(pd.to_numeric(pd.Series([props.get("estimated_value")]), errors="coerce").iloc[0])),
                ("Estimated Duration Days", props.get("estimated_duration_days")),
                ("Estimated Labor Hours", props.get("estimated_labor_hours")),
                ("Estimated Crew Size", props.get("estimated_crew_size")),
                ("Crew Leader", props.get("assigned_crew_leader")),
                ("Start Date", props.get("estimated_start_date")),
                ("End Date", props.get("estimated_end_date")),
                ("Schedule Status", props.get("schedule_status")),
                ("Priority", props.get("priority")),
                ("Blocking Issue", props.get("blocking_issue")),
                ("Schedule Notes", props.get("schedule_notes")),
            ]
            for label, value in details:
                st.write(f"**{label}:** {text_value(value) or '-'}")
            folder_link = text_value(props.get("folder_link_or_path"))
            if folder_link:
                render_document_access("Open Job Folder", folder_link)

            st.subheader("Proposal Summary")
            summary_fields = [
                ("Estimated Value", props.get("estimated_value"), "money"),
                ("Estimated Sq Ft", props.get("estimated_sqft"), "number"),
                ("Price / Sq Ft", props.get("price_per_sqft"), "money"),
                ("Estimated Duration Days", props.get("estimated_duration_days"), "number"),
                ("Estimated Labor Hours", props.get("estimated_labor_hours"), "number"),
                ("Estimated Crew Size", props.get("estimated_crew_size"), "number"),
                ("Job Type", props.get("job_type"), "text"),
                ("Coating Type", props.get("coating_type"), "text"),
                ("Foam Type", props.get("foam_type"), "text"),
                ("Warranty Amount", props.get("warranty_amount"), "money"),
                ("Equipment Rental Amount", props.get("equipment_rental_amount"), "money"),
                ("Subcontractor Amount", props.get("subcontractor_amount"), "money"),
                ("Material Subtotal", props.get("material_subtotal"), "money"),
                ("Labor Subtotal", props.get("labor_subtotal"), "money"),
                ("Schedule Notes", props.get("schedule_notes"), "text"),
                ("Warnings", props.get("warnings"), "text"),
            ]
            for label, value, kind in summary_fields:
                st.write(f"**{label}:** {format_summary_value(value, kind=kind)}")

            st.subheader("Job Documents")
            # TODO: generate true SharePoint web links for estimate/proposal files.
            # TODO: extract proposal scope summary for display in calendar.
            # TODO: add proposal preview panel or PDF link.
            folder_value = props.get("folder_url") or props.get("folder_link_or_path") or props.get("folder_path")
            proposal_value = props.get("proposal_file") or props.get("estimate_file")
            render_document_access("Open Job Folder", folder_value)
            render_document_access(
                "Open Proposal / Estimate",
                proposal_value,
                "Proposal link not available yet — use job folder.",
            )
            render_document_access("Open Contract", props.get("contract_file"))
            render_document_access("Open Job Tracking Form", props.get("job_tracking_file"))
            aerial_status = "Available" if bool(props.get("has_aerial")) else "Not found"
            st.write(f"**Aerial/Drone status:** {aerial_status}")
            st.write(f"**Photo count:** {text_value(props.get('photo_count')) or '0'}")

            start_default = pd.to_datetime(props.get("estimated_start_date"), errors="coerce")
            end_default = pd.to_datetime(props.get("estimated_end_date"), errors="coerce")
            duration_default = pd.to_numeric(pd.Series([props.get("estimated_duration_days")]), errors="coerce").iloc[0]
            with st.form("calendar_selected_job_form"):
                assigned_crew_leader = st.text_input(
                    "Crew Leader",
                    value=text_value(props.get("assigned_crew_leader")),
                )
                estimated_start_date = st.date_input(
                    "Estimated Start Date",
                    value=start_default.date() if not pd.isna(start_default) else date.today(),
                )
                estimated_duration_days = st.number_input(
                    "Estimated Duration Days",
                    min_value=1.0,
                    value=float(duration_default) if not pd.isna(duration_default) and float(duration_default) > 0 else 1.0,
                    step=0.5,
                )
                calculated_end = calculate_end_date(estimated_start_date, estimated_duration_days)
                estimated_end_date = st.date_input(
                    "Estimated End Date",
                    value=pd.to_datetime(calculated_end or end_default, errors="coerce").date()
                    if not pd.isna(pd.to_datetime(calculated_end or end_default, errors="coerce"))
                    else estimated_start_date,
                )
                schedule_status = st.text_input("Schedule Status", value=text_value(props.get("schedule_status")))
                priority = st.text_input("Priority", value=text_value(props.get("priority")))
                blocking_issue = st.text_area("Blocking Issue", value=text_value(props.get("blocking_issue")), height=80)
                schedule_notes = st.text_area("Schedule Notes", value=text_value(props.get("schedule_notes")), height=120)
                submitted = st.form_submit_button("Save Calendar Changes", type="primary")

            if submitted:
                row = pd.DataFrame(
                    [
                        {
                            "schedule_id": props.get("schedule_id") or selected_event.get("id"),
                            "job_id": props.get("job_id"),
                            "assigned_crew_leader": assigned_crew_leader,
                            "estimated_start_date": estimated_start_date,
                            "estimated_duration_days": estimated_duration_days,
                            "estimated_end_date": estimated_end_date,
                            "schedule_status": schedule_status,
                            "priority": priority,
                            "blocking_issue": blocking_issue,
                            "schedule_notes": schedule_notes,
                        }
                    ]
                )
                save_schedule_rows(row)
                st.success("Calendar changes saved.")
                st.rerun()

    if show_unscheduled:
        scheduled_job_ids = set(schedule_df["job_id"].dropna().astype(str)) if "job_id" in schedule_df.columns else set()
        unscheduled = load_unscheduled_backlog_df(scheduled_job_ids)
        st.subheader("Unscheduled Contracted Jobs")
        show_table(
            unscheduled,
            [
                "customer",
                "job_name",
                "division",
                "pipeline_status",
                "estimated_value",
                "estimated_duration_days",
                "estimated_labor_hours",
                "estimated_crew_size",
                "estimate_file",
                "proposal_file",
                "folder_link_or_path",
            ],
            height=300,
            sort_by="estimated_value",
        )

        if not unscheduled.empty and "job_id" in unscheduled.columns:
            unscheduled_by_id = {
                text_value(row.get("job_id")): row
                for row in unscheduled.to_dict(orient="records")
                if text_value(row.get("job_id"))
            }
            job_ids = list(unscheduled_by_id.keys())
            if not job_ids:
                st.info("No unscheduled jobs with job_id are available to schedule.")
                return

            def job_label(job_id: str) -> str:
                row = unscheduled_by_id.get(job_id, {})
                return f"{text_value(row.get('customer'))} - {text_value(row.get('job_name'))} ({job_id})"

            selected_job_id = st.selectbox("Job", job_ids, format_func=job_label, key="schedule_job_to_add")
            selected_row = unscheduled_by_id.get(selected_job_id, {})
            if st.session_state.get("schedule_selected_job_id") != selected_job_id:
                st.session_state["schedule_selected_job_id"] = selected_job_id
                selected_duration = pd.to_numeric(
                    pd.Series([selected_row.get("estimated_duration_days")]),
                    errors="coerce",
                ).iloc[0]
                selected_labor_hours = pd.to_numeric(
                    pd.Series([selected_row.get("estimated_labor_hours")]),
                    errors="coerce",
                ).iloc[0]
                selected_crew_size = pd.to_numeric(
                    pd.Series([selected_row.get("estimated_crew_size")]),
                    errors="coerce",
                ).iloc[0]
                st.session_state["schedule_new_duration_days"] = (
                    int(selected_duration) if not pd.isna(selected_duration) and selected_duration > 0 else 1
                )
                st.session_state["schedule_new_labor_hours"] = (
                    float(selected_labor_hours) if not pd.isna(selected_labor_hours) else 0.0
                )
                st.session_state["schedule_new_crew_size"] = (
                    int(selected_crew_size) if not pd.isna(selected_crew_size) else 0
                )

            with st.form("add_unscheduled_job_form"):
                assigned_crew_leader = st.text_input("Crew Leader", key="unscheduled_crew_leader")
                estimated_start_date = st.date_input("Estimated Start Date", value=date.today(), key="unscheduled_start")
                estimated_duration_days = st.number_input(
                    "Estimated Duration Days",
                    min_value=1.0,
                    step=0.5,
                    key="schedule_new_duration_days",
                )
                estimated_labor_hours = st.number_input(
                    "Estimated Labor Hours",
                    min_value=0.0,
                    step=1.0,
                    key="schedule_new_labor_hours",
                )
                estimated_crew_size = st.number_input(
                    "Estimated Crew Size",
                    min_value=0,
                    step=1,
                    key="schedule_new_crew_size",
                )
                estimated_end_date = calculate_end_date(estimated_start_date, estimated_duration_days)
                st.write(f"**Estimated End Date:** {estimated_end_date or '-'}")
                st.write("**Selected Job Metadata**")
                metadata = [
                    ("Customer", selected_row.get("customer")),
                    ("Job Name", selected_row.get("job_name")),
                    ("Division", selected_row.get("division")),
                    ("Estimated Value", fmt_dollar(pd.to_numeric(pd.Series([selected_row.get("estimated_value")]), errors="coerce").iloc[0])),
                    ("Estimated Duration Days", selected_row.get("estimated_duration_days")),
                    ("Estimated Labor Hours", selected_row.get("estimated_labor_hours")),
                    ("Estimated Crew Size", selected_row.get("estimated_crew_size")),
                    ("Folder Link / Path", selected_row.get("folder_link_or_path")),
                ]
                for label, value in metadata:
                    st.write(f"**{label}:** {text_value(value) or '-'}")
                schedule_status = st.text_input("Schedule Status", value="Scheduled", key="unscheduled_status")
                priority = st.text_input("Priority", key="unscheduled_priority")
                schedule_notes = st.text_area("Schedule Notes", key="unscheduled_notes")
                submitted = st.form_submit_button("Add Job to Schedule", type="primary")

            if submitted:
                row = pd.DataFrame(
                    [
                        {
                            "schedule_id": schedule_id_for_job(selected_row.get("job_id")),
                            "job_id": selected_row.get("job_id"),
                            "assigned_crew_leader": assigned_crew_leader,
                            "estimated_start_date": estimated_start_date,
                            "estimated_duration_days": estimated_duration_days,
                            "estimated_labor_hours": estimated_labor_hours,
                            "estimated_crew_size": estimated_crew_size,
                            "estimated_end_date": estimated_end_date,
                            "schedule_status": schedule_status,
                            "priority": priority,
                            "schedule_notes": schedule_notes,
                        }
                    ]
                )
                save_schedule_rows(row)
                st.success("Job added to schedule.")
                st.session_state.pop("schedule_selected_job_id", None)
                st.rerun()


def project_scheduling_page() -> None:
    st.title("Project Scheduling")
    backlog = apply_basic_filters(query_view("dashboard_contracted_backlog"))
    schedule = load_schedule_df()

    if backlog.empty and schedule.empty:
        show_empty("No contracted backlog or schedule rows are available.")
        return

    if "job_id" not in backlog.columns:
        show_empty("dashboard_contracted_backlog does not include job_id.")
        return

    merged = backlog.merge(schedule, on="job_id", how="left", suffixes=("", "_schedule"))
    for column in (
        "schedule_id",
        "assigned_crew_leader",
        "estimated_start_date",
        "estimated_end_date",
        "schedule_status",
        "priority",
        "blocking_issue",
        "schedule_notes",
    ):
        if column not in merged.columns:
            merged[column] = None

    if "estimated_duration_days_schedule" in merged.columns:
        merged["estimated_duration_days"] = merged["estimated_duration_days_schedule"].combine_first(
            merged.get("estimated_duration_days")
        )

    merged["schedule_id"] = merged.apply(
        lambda row: text_value(row.get("schedule_id")) or schedule_id_for_job(row.get("job_id")),
        axis=1,
    )

    filter_cols = st.columns(3)
    with filter_cols[0]:
        division_filter = st.multiselect("Division", options_from(merged, "division"), key="project_schedule_division")
    with filter_cols[1]:
        leader_filter = st.multiselect(
            "Crew Leader",
            options_from(merged, "assigned_crew_leader"),
            key="project_schedule_leader",
        )
    with filter_cols[2]:
        schedule_status_filter = st.multiselect(
            "Schedule Status",
            options_from(merged, "schedule_status"),
            key="project_schedule_status",
        )

    filtered = merged.copy()
    if division_filter:
        filtered = filtered[filtered["division"].astype(str).isin(division_filter)]
    if leader_filter:
        filtered = filtered[filtered["assigned_crew_leader"].astype(str).isin(leader_filter)]
    if schedule_status_filter:
        filtered = filtered[filtered["schedule_status"].astype(str).isin(schedule_status_filter)]

    display_columns = [
        "job_id",
        "customer",
        "job_name",
        "division",
        "pipeline_status",
        "estimated_value",
        "estimated_duration_days",
        "estimated_labor_hours",
        "estimated_crew_size",
        "assigned_crew_leader",
        "estimated_start_date",
        "estimated_end_date",
        "schedule_status",
        "priority",
        "blocking_issue",
        "schedule_notes",
    ]
    for column in display_columns:
        if column not in filtered.columns:
            filtered[column] = None

    st.caption("Edit schedule fields, then save. End date is recalculated from start date and duration when possible.")
    edited = st.data_editor(
        filtered[display_columns],
        use_container_width=True,
        hide_index=True,
        height=560,
        disabled=[
            "job_id",
            "customer",
            "job_name",
            "division",
            "pipeline_status",
            "estimated_value",
            "estimated_labor_hours",
            "estimated_crew_size",
        ],
        column_config={
            "estimated_start_date": st.column_config.DateColumn("Estimated Start Date"),
            "estimated_end_date": st.column_config.DateColumn("Estimated End Date"),
            "estimated_value": st.column_config.NumberColumn("Estimated Value", format="$%.0f"),
        },
    )

    if st.button("Save Schedule", type="primary"):
        try:
            saved_count = save_schedule_rows(edited)
            st.success(f"Saved {saved_count:,} schedule rows.")
        except Exception as exc:
            show_database_error(exc)


def daily_crew_dispatch_page() -> None:
    st.title("Daily Crew Dispatch")
    dispatch_date = st.date_input("Dispatch Date", value=date.today())
    jobs = load_dispatch_jobs(dispatch_date)

    if jobs.empty:
        show_empty("No scheduled jobs overlap the selected dispatch date.")
        return

    editable_columns = [
        "job_id",
        "customer",
        "job_name",
        "site_address",
        "start_time",
        "crew_leader",
        "crew_members",
        "equipment_notes",
        "material_notes",
        "work_notes",
        "special_instructions",
    ]
    for column in editable_columns:
        if column not in jobs.columns:
            jobs[column] = None

    edited = st.data_editor(
        jobs[editable_columns],
        use_container_width=True,
        hide_index=True,
        height=420,
        disabled=["job_id", "customer", "job_name", "site_address"],
    )

    message_text = generate_dispatch_message(edited, dispatch_date)
    st.subheader("Dispatch Message")
    st.text_area("Copy-friendly dispatch output", value=message_text, height=360)

    # TODO: Add Teams/Zapier/Twilio send integration after draft review and approval.
    if st.button("Save Dispatch Draft", type="primary"):
        try:
            saved_count = save_dispatch_draft(edited, message_text, dispatch_date)
            st.success(f"Saved {saved_count:,} dispatch draft rows.")
        except Exception as exc:
            show_database_error(exc)


def closeout_billing_risk_page() -> None:
    st.title("Closeout / Billing Risk")
    risk = apply_basic_filters(load_df("SELECT * FROM dashboard_closeout_billing_risk"))
    rollup = apply_basic_filters(load_df("SELECT * FROM dashboard_closeout_billing_risk_rollup"))

    if risk.empty:
        show_empty()
        return

    issue_text = risk["closeout_issue"].fillna("").astype(str) if "closeout_issue" in risk.columns else pd.Series("", index=risk.index)
    metric_row(
        [
            ("Closeout Risk Jobs", number_metric(len(risk))),
            ("Value at Risk", money_metric(safe_sum(risk, "estimated_value"))),
            ("Completed Missing Invoice", number_metric(issue_text.str.contains("missing invoice", case=False, na=False).sum())),
            ("Completed Missing Final Price", number_metric(issue_text.str.contains("missing final price", case=False, na=False).sum())),
            ("Invoice Mismatch Review", number_metric(issue_text.str.contains("differs", case=False, na=False).sum())),
            ("Completed Missing Warranty", number_metric(issue_text.str.contains("missing warranty", case=False, na=False).sum())),
        ]
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        bar_chart(risk, "closeout_issue", None, "Closeout Issues by Type")
    with c2:
        chart_df = rollup if not rollup.empty else risk
        bar_chart(chart_df, "division", "total_estimated_value" if "total_estimated_value" in chart_df.columns else "estimated_value", "Value at Risk by Division")
    with c3:
        bar_chart(risk, "division", None, "Closeout Issues by Division", color="closeout_issue")

    show_table(
        risk,
        [
            "closeout_issue",
            "customer",
            "job_name",
            "division",
            "status",
            "estimated_value",
            "final_price",
            "has_invoice",
            "has_warranty",
            "warnings",
            "folder_link_or_path",
        ],
        sort_by="estimated_value",
    )


def job_warnings_page() -> None:
    st.title("Job Warnings")
    warnings = apply_basic_filters(query_view("dashboard_job_warnings_actionable"))
    if "warnings" in warnings.columns:
        warnings = warnings[warnings["warnings"].fillna("").astype(str).str.strip() != ""]

    if warnings.empty:
        show_empty()
        return

    metric_row(
        [
            ("Warning Jobs", fmt_count(len(warnings))),
            ("Missing Invoice", fmt_count(bool_series(warnings, "completed_missing_invoice").sum())),
            ("Missing Final Price", fmt_count(bool_series(warnings, "completed_missing_final_price").sum())),
            ("Missing Signed Contract", fmt_count(bool_series(warnings, "missing_signed_contract").sum())),
            ("Missing Job Spec", fmt_count(bool_series(warnings, "missing_job_spec").sum())),
        ]
    )
    show_table(
        warnings,
        ["customer", "job_name", "division", "pipeline_status", "status", "warnings", "estimated_value", "folder_link_or_path"],
        sort_by="estimated_value",
    )


def estimate_analytics_page() -> None:
    st.title("Estimate Analytics")
    estimates = apply_basic_filters(query_view("dashboard_estimates"))

    if estimates.empty:
        show_empty()
        return

    metric_row(
        [
            ("Estimate Files", fmt_count(len(estimates))),
            ("Total Estimated Value", fmt_dollar(numeric_series(estimates, "estimated_value").sum())),
            ("Estimated Labor Hours", fmt_count(numeric_series(estimates, "estimated_labor_hours").sum())),
            ("Estimated Duration Days", fmt_count(numeric_series(estimates, "estimated_duration_days").sum())),
            ("Average Price/Sq Ft", fmt_dollar(numeric_series(estimates, "price_per_sqft").mean())),
        ]
    )

    c1, c2 = st.columns(2)
    with c1:
        bar_chart(estimates, "estimate_scope_type", "estimated_value", "Estimated Value by Estimate Scope Type")
        bar_chart(estimates, "estimate_role", None, "Estimate Count by Role")
    with c2:
        bar_chart(estimates, "division", "estimated_labor_hours", "Estimated Labor Hours by Division")
        bar_chart(estimates, "division", "estimated_duration_days", "Estimated Duration Days by Division")

    show_table(
        estimates,
        [
            "estimate_file",
            "customer",
            "job_name",
            "estimate_role",
            "estimate_scope_type",
            "estimated_value",
            "estimated_duration_days",
            "estimated_labor_hours",
            "source_path",
        ],
        sort_by="estimated_value",
    )


def estimate_quality_issues_page() -> None:
    st.title("Estimate Quality Issues")
    issues = apply_basic_filters(query_view("dashboard_estimate_quality_issues"))

    if issues.empty:
        show_empty()
        return

    issue_text = issues["estimate_issue"].fillna("").astype(str) if "estimate_issue" in issues.columns else pd.Series("", index=issues.index)
    metric_row(
        [
            ("Estimate Issues", number_metric(len(issues))),
            ("Missing Value", number_metric(issue_text.str.contains("missing value|value", case=False, regex=True, na=False).sum())),
            ("Missing Sq Ft", number_metric(issue_text.str.contains("sq ft|sqft", case=False, regex=True, na=False).sum())),
            ("Zero Roof Labor", number_metric(issue_text.str.contains("zero roof labor|zero labor", case=False, regex=True, na=False).sum())),
        ]
    )
    bar_chart(issues, "estimate_issue", None, "Estimate Issues by Type")
    show_table(
        issues,
        [
            "estimate_issue",
            "customer",
            "job_name",
            "division",
            "pipeline_status",
            "job_type",
            "estimated_value",
            "estimated_sqft",
            "price_per_sqft",
            "material_subtotal",
            "labor_subtotal",
            "folder_link_or_path",
        ],
        sort_by="estimated_value",
    )


def line_item_analysis_page() -> None:
    st.title("Line Item Analysis")
    line_items = apply_basic_filters(query_view("dashboard_estimate_line_items_clean"))
    rollup = apply_basic_filters(query_view("dashboard_line_item_rollup_clean"))

    if line_items.empty:
        show_empty()
        return

    metric_row(
        [
            ("Line Items", fmt_count(len(line_items))),
            ("Total Extended Cost", fmt_dollar(numeric_series(line_items, "extended_cost").sum())),
            ("Total Labor Hours", fmt_count(numeric_series(line_items, "labor_hours").sum())),
            ("Total Labor Days", fmt_count(numeric_series(line_items, "labor_days").sum())),
        ]
    )

    c1, c2, c3 = st.columns(3)
    chart_df = rollup if not rollup.empty else line_items
    with c1:
        bar_chart(chart_df, "section", "total_extended_cost" if "total_extended_cost" in chart_df.columns else "extended_cost", "Extended Cost by Section")
    with c2:
        bar_chart(chart_df, "line_item_category", "total_extended_cost" if "total_extended_cost" in chart_df.columns else "extended_cost", "Extended Cost by Line Item Category")
    with c3:
        bar_chart(chart_df, "section", "total_labor_hours" if "total_labor_hours" in chart_df.columns else "labor_hours", "Labor Hours by Section")

    show_table(
        line_items,
        [
            "job_name",
            "estimate_file",
            "section",
            "line_item_category",
            "line_item_name",
            "quantity",
            "unit",
            "extended_cost",
            "labor_hours",
        ],
        sort_by="extended_cost",
    )


def estimate_adders_page() -> None:
    st.title("Estimate Adders")
    adders = apply_basic_filters(load_df("SELECT * FROM dashboard_estimate_adders_enhanced"))
    rollup = apply_basic_filters(load_df("SELECT * FROM dashboard_adder_business_category_rollup"))

    with st.expander("Debug: Estimate Adders data"):
        st.write("Rows after filters:", len(adders))
        st.write("Columns:", list(adders.columns))
        debug_columns = ["division", "pipeline_status", "adder_business_category", "extended_cost"]
        if not adders.empty and set(debug_columns).issubset(adders.columns):
            st.write(adders[debug_columns].head(20))

    if "extended_cost" in adders.columns:
        adders["extended_cost"] = pd.to_numeric(adders["extended_cost"], errors="coerce")
    if "labor_hours" in adders.columns:
        adders["labor_hours"] = pd.to_numeric(adders["labor_hours"], errors="coerce")

    if adders.empty:
        show_empty()
        return

    metric_row(
        [
            ("Adder Lines", number_metric(len(adders))),
            ("Total Adder Cost", money_metric(safe_sum(adders, "extended_cost"))),
            ("Adder Labor Hours", number_metric(safe_sum(adders, "labor_hours"))),
            ("Business Categories", number_metric(adders["adder_business_category"].nunique() if "adder_business_category" in adders.columns else 0)),
        ]
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        if not adders.empty and {"adder_business_category", "extended_cost"}.issubset(adders.columns):
            by_cat = (
                adders.groupby("adder_business_category", dropna=False, as_index=False)
                .agg(extended_cost=("extended_cost", "sum"))
                .sort_values("extended_cost", ascending=False)
            )
            by_cat = by_cat[by_cat["extended_cost"].fillna(0) != 0]
            if by_cat.empty:
                st.info("No non-zero adder cost available for Adder Cost by Business Category.")
            else:
                fig = px.bar(by_cat, x="adder_business_category", y="extended_cost", title="Adder Cost by Business Category")
                st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No data available for Adder Cost by Business Category.")
    with c2:
        if not adders.empty and {"pipeline_status", "adder_business_category", "extended_cost"}.issubset(adders.columns):
            by_pipeline = (
                adders.groupby(["pipeline_status", "adder_business_category"], dropna=False, as_index=False)
                .agg(total_adder_cost=("extended_cost", "sum"))
                .sort_values("total_adder_cost", ascending=False)
            )
            by_pipeline = by_pipeline[by_pipeline["total_adder_cost"].fillna(0) != 0]
            if by_pipeline.empty:
                st.info("No non-zero adder cost available for Adder Cost by Pipeline Status.")
            else:
                fig = px.bar(
                    by_pipeline,
                    x="pipeline_status",
                    y="total_adder_cost",
                    color="adder_business_category",
                    title="Adder Cost by Pipeline Status",
                )
                st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No data available for Adder Cost by Pipeline Status.")
    with c3:
        chart_df = rollup if not rollup.empty else adders
        value_col = "total_adder_cost" if "total_adder_cost" in chart_df.columns else "extended_cost"
        bar_chart(chart_df, "division", value_col, "Adder Cost by Division")

    show_table(
        adders,
        [
            "customer",
            "job_name",
            "estimate_file",
            "division",
            "pipeline_status",
            "adder_business_category",
            "section",
            "line_item_category",
            "line_item_name",
            "description",
            "extended_cost",
            "labor_hours",
            "source_sheet",
            "source_row",
        ],
        sort_by="extended_cost",
    )


def stamp_tracking_page() -> None:
    st.title("STAMP Tracking")
    stamp = apply_basic_filters(query_view("dashboard_stamp_tracking"))

    if stamp.empty:
        show_empty()
        return

    metric_row(
        [
            ("STAMP Estimate Count", fmt_count(len(stamp))),
            ("STAMP Estimated Value", fmt_dollar(numeric_series(stamp, "estimated_value").sum())),
            ("STAMP Labor Hours", fmt_count(numeric_series(stamp, "estimated_labor_hours").sum())),
            ("STAMP Duration Days", fmt_count(numeric_series(stamp, "estimated_duration_days").sum())),
        ]
    )
    show_table(
        stamp,
        [
            "customer",
            "job_name",
            "estimate_file",
            "estimate_role",
            "estimate_scope_type",
            "estimated_value",
            "estimated_duration_days",
            "estimated_labor_hours",
            "source_path",
        ],
        sort_by="estimated_value",
    )


def documentation_risk_page() -> None:
    st.title("Documentation Risk")
    risk = apply_basic_filters(load_df("SELECT * FROM dashboard_documentation_risk"))
    docs = apply_basic_filters(query_view("dashboard_documentation_summary"))

    if risk.empty:
        show_empty()
        return

    risk_text = risk["documentation_risk"].fillna("").astype(str) if "documentation_risk" in risk.columns else pd.Series("", index=risk.index)
    metric_row(
        [
            ("Documentation Risk Jobs", number_metric(len(risk))),
            ("High-Value Missing Aerial", number_metric(risk_text.str.contains("aerial|drone", case=False, regex=True, na=False).sum())),
            ("Missing Photos", number_metric(risk_text.str.contains("photos", case=False, na=False).sum())),
            ("Missing Job Spec", number_metric(risk_text.str.contains("job spec", case=False, na=False).sum())),
            ("Missing Signed Contract", number_metric(risk_text.str.contains("signed contract", case=False, na=False).sum())),
            ("Completed Missing Warranty", number_metric(risk_text.str.contains("warranty", case=False, na=False).sum())),
        ]
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        bar_chart(risk, "documentation_risk", None, "Documentation Risk by Type")
    with c2:
        bar_chart(risk, "division", None, "Documentation Risk by Division", color="documentation_risk")
    with c3:
        photo_y = "total_photos" if "total_photos" in docs.columns else "photo_count"
        bar_chart(docs, "division", photo_y, "Photos by Division / Pipeline", color="pipeline_status")

    show_table(
        risk,
        [
            "documentation_risk",
            "customer",
            "job_name",
            "division",
            "pipeline_status",
            "status",
            "estimated_value",
            "photo_count",
            "has_aerial",
            "has_job_spec",
            "has_signed_contract",
            "has_invoice",
            "has_warranty",
            "folder_link_or_path",
        ],
        sort_by="estimated_value",
    )


def documentation_page() -> None:
    documentation_risk_page()


def pricing_catalog_page() -> None:
    st.title("Pricing Catalog")
    try:
        health = load_pricing_health()
        filter_options = load_pricing_filter_options()
    except Exception as exc:
        show_database_error(exc)
        st.stop()

    if not health.empty:
        row = health.iloc[0]
        metric_row(
            [
                ("Total Pricing Rows", fmt_count(row.get("total_rows"))),
                ("Current Active Rows", fmt_count(row.get("current_active_rows"))),
                ("Needs Review", fmt_count(row.get("rows_needing_review"))),
                ("Distinct Vendors", fmt_count(row.get("distinct_vendors"))),
                ("Latest Effective Date", text_value(row.get("latest_effective_date")) or "-"),
                ("Missing Price", fmt_count(row.get("missing_price_count"))),
            ]
        )

    with st.expander("Pricing filters", expanded=True):
        search = st.text_input("Search products, descriptions, vendors, categories, item numbers, and notes", key="pricing_search").strip()
        c1, c2, c3 = st.columns(3)
        with c1:
            vendors = st.multiselect("Vendor", filter_options.get("vendor", []), key="pricing_vendor_filter")
            is_current_filter = st.selectbox("Current", ["All", "Current only", "Not current"], index=1, key="pricing_current_filter")
        with c2:
            categories = st.multiselect("Category", filter_options.get("category", []), key="pricing_category_filter")
            source_types = st.multiselect("Source type", filter_options.get("source_type", []), key="pricing_source_type_filter")
        with c3:
            default_statuses = ["active"] if "active" in filter_options.get("status", []) else []
            statuses = st.multiselect("Status", filter_options.get("status", []), default=default_statuses, key="pricing_status_filter")
            limit = st.number_input("Row limit", min_value=100, max_value=10000, value=2000, step=100, key="pricing_limit")

        source_files = st.multiselect("Source file", filter_options.get("source_file", []), key="pricing_source_file_filter")
        show_review_rows = st.checkbox("Show review/ambiguous rows", value=False, key="pricing_show_review_rows")
        needs_review_filter = "All" if show_review_rows else "Reviewed / OK"

        d1, d2 = st.columns(2)
        with d1:
            effective_start = st.date_input("Effective date from", value=None, key="pricing_effective_start")
        with d2:
            effective_end = st.date_input("Effective date to", value=None, key="pricing_effective_end")

    try:
        pricing = load_pricing_catalog_filtered(
            search,
            tuple(vendors),
            tuple(categories),
            tuple(statuses),
            tuple(source_files),
            tuple(source_types),
            is_current_filter,
            needs_review_filter,
            effective_start.isoformat() if effective_start else None,
            effective_end.isoformat() if effective_end else None,
            int(limit),
        )
    except Exception as exc:
        show_database_error(exc)
        st.stop()

    try:
        current_pricing_export = load_current_pricing_catalog_export()
    except Exception as exc:
        show_database_error(exc)
        st.stop()

    catalog_tab, product_docs_tab = st.tabs(["Pricing Catalog", "Product Data Sheets"])

    with catalog_tab:
        with st.expander("Add pricing row", expanded=False):
            with st.form("add_pricing_catalog_row_form", clear_on_submit=True):
                a1, a2, a3 = st.columns(3)
                with a1:
                    new_product_name = st.text_input("Product name")
                    new_vendor = st.text_input("Vendor")
                    new_category = st.text_input("Category")
                    new_vendor_item_no = st.text_input("Vendor item no.")
                with a2:
                    new_unit_price = st.number_input("Unit price", min_value=0.0, value=0.0, step=1.0, format="%.4f")
                    new_unit = st.text_input("Unit of measure", value="unit")
                    new_package_size = st.text_input("Package size")
                    new_price_basis = st.text_input("Price basis")
                with a3:
                    new_price_per_gallon = st.number_input("Price per gallon", min_value=0.0, value=0.0, step=1.0, format="%.4f")
                    new_price_per_sqft = st.number_input("Price per sq ft", min_value=0.0, value=0.0, step=0.01, format="%.4f")
                    new_price_per_unit = st.number_input("Price per unit", min_value=0.0, value=0.0, step=1.0, format="%.4f")
                    new_effective_date = st.date_input("Effective date", value=date.today())
                new_description = st.text_area("Description")
                new_notes = st.text_area("Notes")
                s1, s2, s3 = st.columns(3)
                with s1:
                    new_status = st.selectbox("Status", ["active", "review", "inactive"], index=0)
                with s2:
                    new_is_current = st.checkbox("Current", value=True)
                with s3:
                    new_needs_review = st.checkbox("Needs review", value=False)
                submitted_new_pricing = st.form_submit_button("Create Pricing Row", type="primary")
                if submitted_new_pricing:
                    try:
                        pricing_item_id = create_pricing_catalog_row(
                            {
                                "product_name": new_product_name,
                                "vendor": new_vendor,
                                "category": new_category,
                                "vendor_item_no": new_vendor_item_no,
                                "unit_price": new_unit_price if new_unit_price > 0 else None,
                                "unit_of_measure": new_unit,
                                "package_size": new_package_size,
                                "price_basis": new_price_basis,
                                "price_per_gallon": new_price_per_gallon if new_price_per_gallon > 0 else None,
                                "price_per_sqft": new_price_per_sqft if new_price_per_sqft > 0 else None,
                                "price_per_unit": new_price_per_unit if new_price_per_unit > 0 else None,
                                "effective_date": new_effective_date,
                                "description": new_description,
                                "notes": new_notes,
                                "status": new_status,
                                "is_current": new_is_current,
                                "needs_review": new_needs_review,
                            }
                        )
                        st.success(f"Created pricing row {pricing_item_id}.")
                        st.rerun()
                    except Exception as exc:
                        show_database_error(exc)

        st.caption(f"Showing {fmt_count(len(pricing))} pricing rows")
        if pricing.empty:
            show_empty("No pricing rows match the current filters.")
        else:
            display_columns = [
                "pricing_item_id",
                "product_name",
                "vendor",
                "category",
                "unit_price",
                "unit_of_measure",
                "package_size",
                "price_basis",
                "price_per_gallon",
                "price_per_sqft",
                "price_per_unit",
                "effective_date",
                "status",
                "is_current",
                "needs_review",
                "vendor_item_no",
                "source_file",
                "source_type",
                "notes",
            ]
            for column in display_columns:
                if column not in pricing.columns:
                    pricing[column] = None
            edited_pricing = st.data_editor(
                pricing[display_columns],
                use_container_width=True,
                hide_index=True,
                height=560,
                key="pricing_catalog_editor",
                disabled=["pricing_item_id", "source_file", "source_type"],
                column_config={
                    "unit_price": st.column_config.NumberColumn("Unit Price", format="$%.4f"),
                    "price_per_gallon": st.column_config.NumberColumn("Price / Gal", format="$%.4f"),
                    "price_per_sqft": st.column_config.NumberColumn("Price / Sq Ft", format="$%.4f"),
                    "price_per_unit": st.column_config.NumberColumn("Price / Unit", format="$%.4f"),
                    "effective_date": st.column_config.DateColumn("Effective Date"),
                    "is_current": st.column_config.CheckboxColumn("Current"),
                    "needs_review": st.column_config.CheckboxColumn("Needs Review"),
                    "notes": st.column_config.TextColumn("Notes", width="large"),
                },
            )
            save_col, clear_col = st.columns([1, 3])
            with save_col:
                if st.button("Save Pricing Edits", type="primary", disabled=pricing.empty):
                    try:
                        saved_count = save_pricing_catalog_edits(pricing, edited_pricing)
                        if saved_count:
                            st.success(f"Saved {saved_count:,} pricing row edits.")
                            st.rerun()
                        else:
                            st.info("No pricing changes detected.")
                    except Exception as exc:
                        show_database_error(exc)
            with clear_col:
                st.caption("Existing catalog rows can be corrected here. New product creation still flows through pricing import or product document upload.")

        filtered_export = pricing_export_dataframe(pricing)
        full_current_export = pricing_export_dataframe(current_pricing_export)
        c1, c2 = st.columns(2)
        with c1:
            st.download_button(
                "Download filtered pricing CSV",
                data=filtered_export.to_csv(index=False).encode("utf-8"),
                file_name="pricing_catalog_filtered.csv",
                mime="text/csv",
            )
        with c2:
            st.download_button(
                "Download full current pricing catalog CSV",
                data=full_current_export.to_csv(index=False).encode("utf-8"),
                file_name="pricing_catalog_current.csv",
                mime="text/csv",
            )

    with product_docs_tab:
        st.subheader("Upload Product Data Sheets")
        st.caption("Upload a PDS, SDS, application guide, installation guide, or technical bulletin. Link it to an existing product when possible.")
        product_options = load_product_catalog_options()
        selected_product_row: dict[str, Any] | None = None
        if product_options.empty:
            st.info("No product catalog rows are available yet. Uploads will create product knowledge rows from the document.")
        else:
            option_rows = product_options.to_dict(orient="records")
            selected_idx = st.selectbox(
                "Link uploaded sheet to product",
                options=list(range(len(option_rows) + 1)),
                index=0,
                format_func=lambda idx: "Create or infer product from document" if idx == 0 else product_catalog_option_label(option_rows[idx - 1]),
                key="product_sheet_catalog_link",
            )
            if selected_idx:
                selected_product_row = option_rows[selected_idx - 1]
        manufacturer_hint = st.text_input("Manufacturer hint", key="product_sheet_manufacturer_hint").strip()
        use_ai = st.checkbox(
            "Use AI parser for richer extraction",
            value=False,
            key="product_sheet_use_ai",
            help="When unchecked, the deterministic/regex parser is used and no OpenAI API call is made.",
        )
        uploaded_product_files = st.file_uploader(
            "Product document files",
            type=["pdf", "txt", "md", "text"],
            accept_multiple_files=True,
            key="product_sheet_uploads",
        )
        if uploaded_product_files and st.button("Process Product Sheets", type="primary"):
            processed_rows = []
            for uploaded_file in uploaded_product_files:
                try:
                    result = ingest_uploaded_product_document(
                        uploaded_file,
                        selected_product=selected_product_row,
                        use_ai=use_ai,
                        manufacturer_hint=manufacturer_hint,
                    )
                    counts = result["counts"]
                    processed_rows.append(
                        {
                            "file": uploaded_file.name,
                            "products": counts.get("product_catalog", 0),
                            "documents": counts.get("product_documents", 0),
                            "properties": counts.get("product_properties", 0),
                            "rules": counts.get("product_rules", 0),
                            "linked_product": text_value((selected_product_row or {}).get("product_name")),
                        }
                    )
                except Exception as exc:
                    processed_rows.append({"file": uploaded_file.name, "error": safe_exception_text(exc)})
            if processed_rows:
                st.success(f"Processed {len(processed_rows):,} uploaded product document(s).")
                st.dataframe(pd.DataFrame(processed_rows), use_container_width=True, hide_index=True)


@st.cache_data(ttl=300, show_spinner=False)
def load_estimator_data_cached(load_profile: str = "interactive"):
    return load_estimator_data(Path.cwd(), database_url=DATABASE_URL, prefer_database=True, load_profile=load_profile)


ESTIMATOR_DATA_SESSION_CACHE_TTL_SECONDS = 300


def clear_estimator_data_caches() -> None:
    load_estimator_data_cached.clear()
    try:
        for key in list(st.session_state.keys()):
            if str(key).startswith("estimator_data_session_cache_"):
                st.session_state.pop(key, None)
    except Exception:
        logger.debug("could not clear estimator session data cache", exc_info=True)


def load_estimator_data_for_ui(load_profile: str = "interactive") -> EstimatorData:
    cache_key = f"estimator_data_session_cache_{load_profile}"
    now = time.time()
    cached = st.session_state.get(cache_key)
    if isinstance(cached, dict) and isinstance(cached.get("data"), EstimatorData):
        age_seconds = now - float(cached.get("loaded_at") or 0)
        if age_seconds <= ESTIMATOR_DATA_SESSION_CACHE_TTL_SECONDS:
            data = cached["data"]
            record_estimator_perf_event(
                f"{load_profile} estimator data load",
                cache_status="hit",
                detail=f"session cache age={age_seconds:.1f}s; {estimator_data_signature(data)}",
            )
            return data
    with estimator_perf_step(f"{load_profile} estimator data load", cache_status="miss"):
        data = load_estimator_data_cached(load_profile)
    st.session_state[cache_key] = {"loaded_at": now, "data": data}
    return data


def optional_field_notes_estimator():
    if estimate_from_field_notes is None:
        return None, "Field notes estimator is not available in this deployment yet."
    return estimate_from_field_notes, None


def keyword_score(text_value: str, keywords: list[str]) -> int:
    return sum(1 for keyword in keywords if keyword in text_value)


def classify_estimate_type_from_notes(notes: str | None) -> str:
    text_value = " ".join(str(notes or "").lower().split())
    if not text_value:
        return ESTIMATE_TYPE_RESTORATION
    repair_score = keyword_score(text_value, REPAIR_MODE_KEYWORDS)
    restoration_score = keyword_score(text_value, RESTORATION_MODE_KEYWORDS)
    insulation_score = keyword_score(text_value, INSULATION_MODE_KEYWORDS)
    flooring_score = keyword_score(text_value, FLOORING_MODE_KEYWORDS)
    if re.search(r"\b\d+(?:,\d{3})?\s*(?:sqft|sq ft|sf|square feet)\b", text_value):
        restoration_score += 2
    if any(term in text_value for term in ("10-year", "10 year", "15-year", "15 year", "20-year", "20 year")):
        restoration_score += 2
    if any(term in text_value for term in ("pipe boot", "curb leak", "service call", "emergency", "small repair", "patch")):
        repair_score += 3
    if any(term in text_value for term in ("walls", "attic", "crawlspace", "r-value", "dc315", "thermal barrier")):
        insulation_score += 3
    if any(term in text_value for term in ("polyaspartic", "epoxy floor", "floor system", "concrete floor", "flake broadcast")):
        flooring_score += 3
    if flooring_score >= max(repair_score, restoration_score, insulation_score) and flooring_score > 0:
        return ESTIMATE_TYPE_FLOORING
    if insulation_score >= max(repair_score, restoration_score, flooring_score) and insulation_score > 0:
        return ESTIMATE_TYPE_INSULATION
    if repair_score > restoration_score and repair_score > 0:
        return ESTIMATE_TYPE_REPAIR
    return ESTIMATE_TYPE_RESTORATION


def resolve_estimate_type(selection: str, notes: str | None) -> str:
    if selection == ESTIMATE_TYPE_AUTO:
        return classify_estimate_type_from_notes(notes)
    return selection if selection in ESTIMATE_TYPE_OPTIONS else ESTIMATE_TYPE_RESTORATION


def route_estimator_request(
    notes: str,
    estimate_type_selection: str,
    *,
    overrides: dict[str, Any] | None = None,
    repair_data: Any = None,
    field_estimator_fn: Any = None,
    field_notes_data: Any = None,
) -> tuple[str, Any]:
    resolved_type = resolve_estimate_type(estimate_type_selection, notes)
    if resolved_type == ESTIMATE_TYPE_REPAIR:
        from jobscan.repair_estimator.estimator import estimate_repair_from_notes

        if repair_data is None:
            repair_data = load_repair_history_cached()
        return resolved_type, estimate_repair_from_notes(notes, repair_data, overrides=overrides)
    if resolved_type == ESTIMATE_TYPE_FLOORING:
        from jobscan.flooring_estimator.estimator import estimate_flooring_from_notes

        return resolved_type, estimate_flooring_from_notes(notes, overrides=overrides, data=field_notes_data)
    if field_estimator_fn is None:
        field_estimator_fn, _ = optional_field_notes_estimator()
    if field_estimator_fn is None:
        raise RuntimeError("Field notes estimator is not available in this deployment yet.")
    return resolved_type, field_estimator_fn(notes, overrides or {}, data=field_notes_data)


def clear_conflicting_readiness_after_chat_override(recommendation: Any, final_template_type: str) -> Any:
    template_type = text_value(final_template_type).lower()
    if template_type == "insulation" or not template_type:
        return recommendation
    parsed_fields = recommendation.parsed_fields if isinstance(getattr(recommendation, "parsed_fields", None), dict) else {}
    reason = text_value(getattr(recommendation, "estimate_reason", "") or parsed_fields.get("estimate_reason"))
    if "Insulation area is unknown" not in reason:
        return recommendation
    recommendation.estimate_status = "READY_TO_ESTIMATE"
    recommendation.estimate_reason = ""
    recommendation.required_questions = []
    recommendation.recommended_next_actions = []
    for key in ("estimate_status", "estimate_reason", "required_questions", "recommended_next_actions"):
        parsed_fields.pop(key, None)
    parsed_fields["estimate_status"] = "READY_TO_ESTIMATE"
    missing = parsed_fields.get("missing_info") if isinstance(parsed_fields.get("missing_info"), list) else []
    parsed_fields["missing_info"] = [item for item in missing if text_value(item) != "estimated_sqft"]
    recommendation.review_flags = [
        flag
        for flag in (getattr(recommendation, "review_flags", None) or [])
        if "Insulation area is unknown" not in text_value(flag) and text_value(flag) != "Missing: estimated_sqft"
    ]
    return recommendation


@st.cache_data(ttl=300, show_spinner=False)
def load_repair_history_cached():
    from jobscan.repair_estimator.estimator import load_repair_history_from_database

    return load_repair_history_from_database(get_engine())


def dataframe_from_records(records: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(records) if records else pd.DataFrame()


def _surface_review_number(value: Any, digits: int = 2) -> float | None:
    if value is None or value == "":
        return None
    try:
        if pd.isna(value):
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, digits)


def _surface_trace_for(surface_type: str, trace_rows: list[dict[str, Any]]) -> dict[str, Any]:
    step_by_surface = {
        "walls": "wall_area",
        "wall": "wall_area",
        "ceiling": "ceiling_or_roof_area",
        "roof_underside": "roof_underside_area",
        "roof underside": "roof_underside_area",
        "gable": "gable_area",
        "gables": "gable_area",
    }
    target_step = step_by_surface.get(str(surface_type or "").lower())
    if not target_step:
        return {}
    for row in trace_rows or []:
        if row.get("step") == target_step:
            return row
    return {}


def build_surface_area_review_rows(parsed_fields: dict[str, Any], workbench: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Build the single estimator-facing surface/dimension review table."""

    workbench = workbench or {}
    parsed_fields = parsed_fields or {}
    rows: list[dict[str, Any]] = []
    trace_rows = [row for row in workbench.get("area_calculation_trace") or [] if isinstance(row, dict)]
    performance_by_surface = {
        row.get("surface_type"): row
        for row in workbench.get("insulation_performance_specs") or []
        if isinstance(row, dict)
    }

    for surface in workbench.get("insulation_surfaces") or []:
        if not isinstance(surface, dict):
            continue
        surface_type = str(surface.get("surface_type") or "").lower()
        trace = _surface_trace_for(surface_type, trace_rows)
        inputs = trace.get("inputs") if isinstance(trace.get("inputs"), dict) else {}
        performance = performance_by_surface.get(surface.get("surface_type")) or {}
        rows.append(
            {
                "component": surface.get("surface") or surface_type.replace("_", " ").title() or "Surface",
                "component_type": "surface",
                "surface_type": surface.get("surface_type"),
                "quantity": _surface_review_number(inputs.get("quantity"), 0),
                "length_ft": _surface_review_number(inputs.get("length_ft")),
                "width_ft": _surface_review_number(inputs.get("width_ft")),
                "height_ft": _surface_review_number(inputs.get("wall_height_ft") or inputs.get("height_ft") or inputs.get("roof_rise_ft")),
                "gross_area_sqft": _surface_review_number(surface.get("gross_area_sqft")),
                "deduction_area_sqft": _surface_review_number(surface.get("deduction_area_sqft")),
                "net_area_sqft": _surface_review_number(surface.get("net_area_sqft")),
                "target_r_value": surface.get("target_r_value") if surface.get("target_r_value") not in (None, "") else performance.get("target_r_value"),
                "foam_type": surface.get("foam_type") or performance.get("foam_type"),
                "edited_thickness_inches": surface.get("edited_thickness_inches")
                if surface.get("edited_thickness_inches") not in (None, "")
                else performance.get("edited_thickness_inches"),
                "area_formula": surface.get("area_formula") or trace.get("formula"),
                "source_text": surface.get("source_text") or trace.get("source_text"),
                "confidence": surface.get("confidence") or trace.get("confidence"),
                "selected_source": trace.get("selected_source"),
                "ai_value": trace.get("ai_value"),
                "deterministic_value": trace.get("deterministic_value"),
                "notes": surface.get("notes") or performance.get("notes") or trace.get("notes"),
            }
        )

    dimension_summary = parsed_fields.get("dimension_summary") or {}
    if not isinstance(dimension_summary, dict):
        dimension_summary = {}
    if dimension_summary:
        if not rows:
            for item in dimension_summary.get("included_areas") or []:
                if not isinstance(item, dict):
                    continue
                total_area = item.get("total_area") or item.get("area_sqft")
                rows.append(
                    {
                        "component": item.get("label") or item.get("component") or "Included area",
                        "component_type": "surface",
                        "quantity": item.get("quantity"),
                        "length_ft": item.get("length") or item.get("length_ft"),
                        "width_ft": item.get("width") or item.get("width_ft"),
                        "height_ft": item.get("height") or item.get("height_ft"),
                        "gross_area_sqft": _surface_review_number(total_area),
                        "deduction_area_sqft": 0,
                        "net_area_sqft": _surface_review_number(total_area),
                        "area_formula": item.get("formula"),
                        "source_text": item.get("source_text"),
                        "notes": item.get("notes"),
                    }
                )
        for item in dimension_summary.get("deducted_areas") or []:
            if not isinstance(item, dict):
                continue
            total_area = item.get("total_area") or item.get("area_sqft")
            rows.append(
                {
                    "component": "Deduction - " + str(item.get("label") or item.get("component") or "opening"),
                    "component_type": "deduction",
                    "quantity": item.get("quantity"),
                    "length_ft": item.get("length") or item.get("length_ft"),
                    "width_ft": item.get("width") or item.get("width_ft"),
                    "height_ft": item.get("height") or item.get("height_ft"),
                    "gross_area_sqft": None,
                    "deduction_area_sqft": _surface_review_number(total_area),
                    "net_area_sqft": None,
                    "area_formula": item.get("formula"),
                    "source_text": item.get("source_text"),
                    "notes": item.get("notes"),
                }
            )

    if rows:
        surface_rows = [row for row in rows if row.get("component_type") == "surface"]
        total_gross = sum(float(row.get("gross_area_sqft") or 0) for row in surface_rows)
        total_deductions = sum(float(row.get("deduction_area_sqft") or 0) for row in surface_rows)
        total_net = sum(float(row.get("net_area_sqft") or 0) for row in surface_rows)
        if not total_gross:
            total_gross = _surface_review_number(parsed_fields.get("gross_insulation_area_sqft") or parsed_fields.get("gross_area_sqft") or parsed_fields.get("gross_sqft")) or 0
        if not total_deductions:
            total_deductions = _surface_review_number(parsed_fields.get("opening_area_known_sqft") or parsed_fields.get("deduction_area_sqft") or parsed_fields.get("deduction_sqft")) or 0
        if not total_net:
            total_net = _surface_review_number(parsed_fields.get("net_insulation_area_sqft") or parsed_fields.get("net_area_sqft") or parsed_fields.get("net_sqft")) or 0
        rows.append(
            {
                "component": "Total",
                "component_type": "total",
                "gross_area_sqft": _surface_review_number(total_gross),
                "deduction_area_sqft": _surface_review_number(total_deductions),
                "net_area_sqft": _surface_review_number(total_net),
                "area_formula": "surface totals",
                "notes": workbench.get("area_calculation_explanation") or "; ".join(str(item) for item in dimension_summary.get("warnings") or []),
            }
        )
    elif any(parsed_fields.get(key) for key in ("gross_insulation_area_sqft", "gross_area_sqft", "gross_sqft", "net_insulation_area_sqft", "net_area_sqft", "net_sqft")):
        rows.append(
            {
                "component": "Total",
                "component_type": "total",
                "gross_area_sqft": _surface_review_number(parsed_fields.get("gross_insulation_area_sqft") or parsed_fields.get("gross_area_sqft") or parsed_fields.get("gross_sqft")),
                "deduction_area_sqft": _surface_review_number(parsed_fields.get("opening_area_known_sqft") or parsed_fields.get("deduction_area_sqft") or parsed_fields.get("deduction_sqft")),
                "net_area_sqft": _surface_review_number(parsed_fields.get("net_insulation_area_sqft") or parsed_fields.get("net_area_sqft") or parsed_fields.get("net_sqft")),
                "area_formula": "parsed total",
                "notes": "; ".join(str(item) for item in dimension_summary.get("warnings") or []),
            }
        )
    return rows


def display_safe_cell_value(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(value, sort_keys=True, default=str)
    return value


def _choice_number(value: Any) -> float:
    if isinstance(value, (dict, list, tuple, set)):
        return 0.0
    try:
        number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if pd.isna(number) else float(number)


def _choice_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = text_value(value).lower()
    if text in {"true", "1", "yes", "y", "checked"}:
        return True
    if text in {"false", "0", "no", "n", "unchecked", ""}:
        return False
    return bool(value)


def _choice_quantity(value: Any, *, suffix: str = "") -> str:
    number = _choice_number(value)
    if number <= 0:
        return ""
    rendered = f"{number:,.2f}".rstrip("0").rstrip(".")
    return f"{rendered}{suffix}"


def _choice_text_items(value: Any, *, limit: int = 4) -> list[str]:
    if value is None:
        return []
    parsed = value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped.lower() in {"nan", "none", "null", "[]", "{}"}:
            return []
        if stripped[:1] in {"[", "{"}:
            try:
                parsed = json.loads(stripped)
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed = stripped
    if isinstance(parsed, dict):
        items = [f"{key}: {item}" for key, item in parsed.items() if text_value(item)]
    elif isinstance(parsed, (list, tuple, set)):
        items = [text_value(item) for item in parsed if text_value(item)]
    else:
        items = [text_value(parsed)]
    return list(dict.fromkeys(item for item in items if item))[:limit]


def _choice_add_unique(parts: list[str], text: Any) -> None:
    cleaned = text_value(text)
    if not cleaned:
        return
    if cleaned not in parts:
        parts.append(cleaned)


def _choice_label(row: dict[str, Any]) -> str:
    return text_value(
        row.get("resolved_template_option")
        or row.get("template_line")
        or row.get("labor_task")
        or row.get("package")
        or row.get("decision_id")
    )


def _choice_include_summary(row: dict[str, Any]) -> str:
    label = _choice_label(row) or "this row"
    if not _choice_bool(row.get("include")):
        return f"Not included: {label} is available for review but is not selected."
    if text_value(row.get("manual_override")) in {"True", "true", "1", "yes"} or text_value(row.get("proposal_source")) == "estimator_edit":
        return f"Included because the estimator selected or edited {label}."
    why = text_value(row.get("why_included"))
    if why and not why.lower().startswith("included from historical default/workbench rule"):
        return why
    source = text_value(row.get("proposal_source") or row.get("include_source")).lower()
    if source in {"chat_estimator", "ai_chat"}:
        return f"Included because the estimator chat identified {label} for this scope."
    if source in {"reference_template_summary", "reference_estimate_answer_key", "reference_project"}:
        return f"Included because the matched reference estimate used {label}."
    if source in {"historical_companion"}:
        return f"Included because it is commonly paired with the selected material package."
    if text_value(row.get("historical_evidence_summary")):
        return f"Included based on historical estimating patterns for {label}."
    return f"Included for the current scope: {label}."


def _choice_calculation_summary(row: dict[str, Any]) -> str:
    pieces: list[str] = []
    basis = _choice_quantity(row.get("basis_sqft") or row.get("editable_basis_sqft"), suffix=" sq ft")
    if basis:
        pieces.append(f"basis {basis}")
    thickness = _choice_quantity(row.get("thickness_inches") or row.get("foam_thickness_inches"), suffix='"')
    if thickness:
        pieces.append(f"thickness {thickness}")
    for field, label in (
        ("estimated_units", "units"),
        ("estimated_sets", "sets"),
        ("quantity", "quantity"),
        ("linear_ft", "linear ft"),
        ("days", "days"),
        ("total_hours", "hours"),
        ("display_total_hours", "display hours"),
        ("crew_size", "people"),
        ("trip_count", "trips"),
        ("round_trip_miles", "round trip miles"),
    ):
        value = _choice_quantity(row.get(field))
        if value:
            pieces.append(f"{label} {value}")
    unit_price = _choice_quantity(row.get("unit_price") or row.get("current_unit_price") or row.get("current_price"))
    if unit_price:
        pieces.append(f"unit price {unit_price}")
    cost = _choice_quantity(row.get("estimated_cost"), suffix="")
    if cost:
        pieces.append(f"cost ${cost}")
    if not pieces:
        return ""
    return "Calculation: " + "; ".join(pieces) + "."


def _choice_labor_dependency_summary(row: dict[str, Any]) -> str:
    driver_summary = text_value(row.get("labor_driver_summary"))
    if driver_summary:
        return f"Labor sizing: {driver_summary}"
    driver_qty = _choice_quantity(row.get("labor_driver_quantity"))
    driver_unit = text_value(row.get("labor_driver_unit"))
    driver_rate = _choice_quantity(row.get("historical_driver_rate"))
    driver_rate_unit = text_value(row.get("labor_driver_rate_unit"))
    if driver_qty and driver_rate:
        unit = f" {driver_unit}" if driver_unit else ""
        rate_unit = f" {driver_rate_unit}" if driver_rate_unit else ""
        return f"Labor sizing: {driver_qty}{unit} x {driver_rate}{rate_unit}."
    return ""


def _choice_product_summary(row: dict[str, Any]) -> str:
    guidance = text_value(row.get("product_guidance"))
    warning = text_value(row.get("product_warning_summary") or row.get("product_warnings"))
    product = text_value(row.get("product_name") or row.get("selected_pricing_candidate") or row.get("item_name"))
    status = text_value(row.get("product_guidance_status"))
    if guidance:
        prefix = f"Product guidance ({product or status}): " if product or status else "Product guidance: "
        return prefix + guidance
    if warning:
        return f"Product warning: {warning}"
    return ""


def _choice_full_explanation(row: dict[str, Any]) -> str:
    parts: list[str] = []
    _choice_add_unique(parts, _choice_include_summary(row))
    _choice_add_unique(parts, row.get("reference_project_evidence_summary"))
    _choice_add_unique(parts, row.get("chat_estimator_evidence_summary"))
    _choice_add_unique(parts, row.get("historical_evidence_summary"))
    _choice_add_unique(parts, row.get("pricing_evidence_summary"))
    _choice_add_unique(parts, _choice_product_summary(row))
    _choice_add_unique(parts, _choice_labor_dependency_summary(row))
    _choice_add_unique(parts, _choice_calculation_summary(row))
    for warning in _choice_text_items(row.get("proposal_review_reasons")) + _choice_text_items(row.get("compatibility_warnings")):
        _choice_add_unique(parts, f"Review: {warning}")
    if not parts:
        _choice_add_unique(parts, row.get("notes"))
    return "\n\n".join(parts)


def choice_summary_for_row(row: dict[str, Any]) -> str:
    return _choice_full_explanation(row)


def display_safe_records(records: list[dict[str, Any]], *, editable_fields: set[str] | None = None) -> list[dict[str, Any]]:
    editable_fields = editable_fields or set()
    rows: list[dict[str, Any]] = []
    for row in records or []:
        safe_row: dict[str, Any] = {}
        enriched_row = dict(row)
        enriched_row.setdefault(CHOICE_SUMMARY_COLUMN, choice_summary_for_row(enriched_row))
        for key, value in enriched_row.items():
            safe_row[key] = value if key in editable_fields else display_safe_cell_value(value)
        rows.append(safe_row)
    return rows


def _compact_cell_has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, float) and pd.isna(value):
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) != 0.0
    text = str(value).strip()
    return bool(text and text.lower() not in {"0", "0.0", "nan", "none", "null", "[]", "{}"})


def projected_display_records(
    records: list[dict[str, Any]],
    columns: Iterable[str],
    *,
    editable_fields: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Build a compact display payload without serializing hidden row metadata."""

    editable_fields = editable_fields or set()
    requested = unique_columns(columns)
    if CHOICE_SUMMARY_COLUMN not in requested:
        insert_after = next(
            (
                requested.index(column) + 1
                for column in (
                    "resolved_template_option",
                    "labor_task",
                    "template_line",
                    "package",
                    "adder",
                    "workbook_row",
                )
                if column in requested
            ),
            len(requested),
        )
        requested.insert(insert_after, CHOICE_SUMMARY_COLUMN)
    available = [
        column
        for column in requested
        if column == CHOICE_SUMMARY_COLUMN or any(isinstance(row, dict) and column in row for row in records or [])
    ]
    compact_columns = [
        column
        for column in available
        if column not in COMPACT_DIAGNOSTIC_COLUMNS
        and (
            column in COMPACT_ALWAYS_SHOW_COLUMNS
            or column in editable_fields
            or column == CHOICE_SUMMARY_COLUMN
            or any(_compact_cell_has_value((row or {}).get(column)) for row in records or [])
        )
    ]
    selected_columns = compact_columns or available
    rows: list[dict[str, Any]] = []
    for row in records or []:
        enriched_row = dict(row)
        enriched_row.setdefault(CHOICE_SUMMARY_COLUMN, choice_summary_for_row(enriched_row))
        safe_row: dict[str, Any] = {}
        for key in selected_columns:
            value = enriched_row.get(key)
            safe_row[key] = value if key in editable_fields else display_safe_cell_value(value)
        rows.append(safe_row)
    return rows


def workbench_display_frame_from_records(
    records: list[dict[str, Any]],
    compact_columns: Iterable[str],
    *,
    editable_fields: set[str] | None = None,
    show_row_details: bool = False,
) -> tuple[pd.DataFrame, list[str]]:
    if show_row_details:
        df = pd.DataFrame(display_safe_records(records, editable_fields=editable_fields))
        return df, list(df.columns)
    projected = projected_display_records(records, compact_columns, editable_fields=editable_fields)
    df = pd.DataFrame(projected)
    return df, list(df.columns)


def display_safe_dataframe(records: list[dict[str, Any]]) -> pd.DataFrame:
    """Build a read-only Streamlit dataframe without mixed Arrow object columns."""

    df = pd.DataFrame(display_safe_records(records))
    for column in df.columns:
        if df[column].dtype != "object":
            continue
        df[column] = df[column].map(lambda value: "" if value is None else str(value))
    return df


def estimator_chat_assistant_history_content(result: Any) -> str:
    payload = result.to_dict() if hasattr(result, "to_dict") else result
    if not isinstance(payload, dict):
        return ""
    lines = [str(payload.get("assistant_message") or "I drafted a first pass from the project information.")]
    change_rows = estimator_chat_decision_change_rows(payload.get("workbook_decision_preferences") or [])
    if change_rows:
        lines.append("")
        lines.append("Workbook changes proposed:")
        for row in change_rows[:6]:
            target = row.get("target") or row.get("decision_id") or "decision"
            action = row.get("action") or "update"
            fields = row.get("field_changes") or ""
            suffix = f": {fields}" if fields else ""
            lines.append(f"- {action} {target}{suffix}")
    questions = payload.get("missing_questions") or []
    if questions:
        lines.append("")
        lines.append("Questions to confirm:")
        lines.extend(f"- {question}" for question in questions)
    assumptions = payload.get("assumptions") or []
    if assumptions:
        lines.append("")
        lines.append("Assumptions:")
        lines.extend(f"- {assumption}" for assumption in assumptions)
    warnings = payload.get("warnings") or []
    if warnings:
        lines.append("")
        lines.append("Review flags:")
        lines.extend(f"- {warning}" for warning in warnings)
    return "\n".join(lines)


def estimator_chat_decision_change_rows(preferences: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in preferences if isinstance(preferences, list) else []:
        if not isinstance(item, dict):
            continue
        values = item.get("proposed_values") if isinstance(item.get("proposed_values"), dict) else {}
        template_bucket = str(item.get("template_bucket") or item.get("package") or "").strip().lower().replace(" ", "_")
        alias_text = " ".join(
            str(item.get(key) or "")
            for key in ("decision_id", "template_bucket", "package", "label", "target", "line_item", "section", "description")
        ).lower()
        alias_token = re.sub(r"[^a-z0-9]+", "_", alias_text).strip("_")
        if not template_bucket:
            if re.search(r"\b(?:labor\s+)?loading\b", alias_text) or "labor_loading" in alias_token:
                template_bucket = "labor_loading"
            elif re.search(r"\b(?:labor\s+)?travel(?:ing)?\b", alias_text) or "labor_traveling" in alias_token:
                template_bucket = "labor_traveling"
        workbook_row = str(item.get("workbook_row") or item.get("row_number") or "").strip()
        logistics_expense_row = template_bucket in {
            "labor_loading",
            "labor_traveling",
            "infrared_scan",
            "labor_infrared_scan",
            "meals_lodging",
            "labor_meals_lodging",
        } or workbook_row in {"95", "97", "99", "100", "136", "138", "141", "144"}
        direct_field_names = {
            "basis_sqft",
            "thickness_inches",
            "debris_thickness_inches",
            "tearout_thickness_inches",
            "removed_assembly_thickness_inches",
            "foam_thickness_inches",
            "gal_per_100_sqft",
            "unit_price",
            "estimated_units",
            "linear_ft",
            "days",
            "hours_per_day",
            "people_count",
            "trip_count",
            "round_trip_miles",
            "crew_size",
            "daily_rate",
            "hourly_rate",
            "total_hours",
            "editable_total_hours",
            "formula_mode",
        }
        if logistics_expense_row:
            if template_bucket in {"labor_loading", "labor_traveling"} or workbook_row in {"95", "97", "136", "138"}:
                direct_field_names = {"hours_per_day", "people_count", "trip_count", "unit_price", "round_trip_miles"}
            elif template_bucket in {"infrared_scan", "labor_infrared_scan"} or workbook_row in {"99", "141"}:
                direct_field_names = {"hours_per_day", "unit_price"}
            elif template_bucket in {"meals_lodging", "labor_meals_lodging"} or workbook_row in {"100", "144"}:
                direct_field_names = {"days", "people_count", "unit_price"}
        direct_values = {
            key: value
            for key, value in item.items()
            if key in direct_field_names
            and value not in (None, "")
        }
        merged_values = {key: value for key, value in {**direct_values, **values}.items() if key in direct_field_names}
        if template_bucket in {"labor_loading", "labor_traveling"} or workbook_row in {"95", "97", "136", "138"}:
            is_loading = template_bucket == "labor_loading" or workbook_row in {"95", "136"}
            default_hours = 0.5 if is_loading else 2.5
            default_rate = 25.5 if is_loading else 13.0
            max_hours = 2.0 if is_loading else 6.0
            try:
                hours_value = float(str(merged_values.get("hours_per_day") or "").replace(",", ""))
            except ValueError:
                hours_value = 0.0
            try:
                rate_value = float(str(merged_values.get("unit_price") or "").replace(",", ""))
            except ValueError:
                rate_value = 0.0
            if hours_value <= 0 or hours_value > max_hours:
                merged_values["hours_per_day"] = default_hours
            if rate_value <= 0 or rate_value > default_rate * 1.5:
                merged_values["unit_price"] = default_rate
        target_parts = [
            str(item.get("template_bucket") or item.get("package") or template_bucket or item.get("section") or "").replace("_", " ").strip(),
            f"row {item.get('workbook_row') or item.get('row_number')}" if item.get("workbook_row") or item.get("row_number") else "",
        ]
        include_value = item.get("include")
        action = "update"
        if include_value is True:
            action = "include"
        elif include_value is False:
            action = "remove"
        rows.append(
            {
                "action": action,
                "target": " ".join(part for part in target_parts if part).strip() or item.get("decision_id") or "workbook decision",
                "decision_id": item.get("decision_id") or "",
                "template_bucket": item.get("template_bucket") or item.get("package") or "",
                "workbook_row": item.get("workbook_row") or item.get("row_number") or "",
                "field_changes": ", ".join(f"{key}={value}" for key, value in merged_values.items())[:500],
                "confidence": item.get("confidence") or "",
                "review_required": item.get("review_required") if item.get("review_required") is not None else "",
                "why": text_value(item.get("reason") or item.get("evidence") or item.get("review_reasons") or item.get("review_flags"))[:500],
            }
        )
    return rows


def safe_estimator_chat_thread_id(value: Any) -> str:
    thread_id = re.sub(r"[^a-zA-Z0-9_-]+", "", str(value or "").strip())
    if len(thread_id) < 8:
        return ""
    return thread_id[:64]


def new_estimator_chat_thread_id() -> str:
    return hashlib.sha1(os.urandom(16)).hexdigest()[:16]


def estimator_chat_session_path(thread_id: str) -> Path:
    safe_thread_id = safe_estimator_chat_thread_id(thread_id)
    if not safe_thread_id:
        safe_thread_id = new_estimator_chat_thread_id()
    return ESTIMATOR_CHAT_SESSION_DIR / f"{safe_thread_id}.json"


def load_estimator_chat_session(thread_id: str) -> dict[str, Any]:
    safe_thread_id = safe_estimator_chat_thread_id(thread_id)
    if not safe_thread_id:
        return {}
    path = estimator_chat_session_path(safe_thread_id)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        logger.exception("failed to load estimator chat session snapshot")
        return {}
    if not isinstance(payload, dict):
        return {}
    if payload.get("history") is not None and not isinstance(payload.get("history"), list):
        payload["history"] = []
    if payload.get("result") is not None and not isinstance(payload.get("result"), dict):
        payload["result"] = {}
    if int(payload.get("schema_version") or 0) != ESTIMATOR_CHAT_SESSION_SCHEMA_VERSION:
        user_history = [
            dict(message)
            for message in payload.get("history") or []
            if isinstance(message, dict) and str(message.get("role") or "") == "user"
        ]
        return {
            "thread_id": safe_thread_id,
            "schema_version": ESTIMATOR_CHAT_SESSION_SCHEMA_VERSION,
            "history": user_history,
            "result": {},
            "estimator_notes": "\n\n".join(str(message.get("content") or "") for message in user_history).strip(),
            "stale_snapshot_discarded": True,
        }
    payload["thread_id"] = safe_thread_id
    return payload


def save_estimator_chat_session(
    thread_id: str,
    *,
    history: list[dict[str, Any]] | None = None,
    result: dict[str, Any] | None = None,
    estimator_notes: str = "",
    estimate_type: str = "",
) -> None:
    safe_thread_id = safe_estimator_chat_thread_id(thread_id)
    if not safe_thread_id:
        return
    payload = {
        "thread_id": safe_thread_id,
        "schema_version": ESTIMATOR_CHAT_SESSION_SCHEMA_VERSION,
        "updated_at": pd.Timestamp.utcnow().isoformat(),
        "estimate_type": str(estimate_type or ""),
        "estimator_notes": str(estimator_notes or ""),
        "history": history or [],
        "result": result or {},
    }
    try:
        ESTIMATOR_CHAT_SESSION_DIR.mkdir(parents=True, exist_ok=True)
        path = estimator_chat_session_path(safe_thread_id)
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        temp_path.replace(path)
    except OSError:
        logger.exception("failed to save estimator chat session snapshot")


def current_estimator_chat_thread_id() -> str:
    thread_id = safe_estimator_chat_thread_id(st.session_state.get("estimator_chat_thread_id"))
    if not thread_id:
        try:
            query_value = st.query_params.get("estimator_chat_thread")
        except Exception:
            query_value = ""
        if isinstance(query_value, list):
            query_value = query_value[0] if query_value else ""
        thread_id = safe_estimator_chat_thread_id(query_value)
    if not thread_id:
        thread_id = new_estimator_chat_thread_id()
    st.session_state["estimator_chat_thread_id"] = thread_id
    try:
        if st.query_params.get("estimator_chat_thread") != thread_id:
            st.query_params["estimator_chat_thread"] = thread_id
    except Exception:
        logger.debug("could not sync estimator chat thread query parameter", exc_info=True)
    return thread_id


def reset_current_estimator_chat_thread() -> str:
    thread_id = new_estimator_chat_thread_id()
    st.session_state["estimator_chat_thread_id"] = thread_id
    try:
        st.query_params["estimator_chat_thread"] = thread_id
    except Exception:
        logger.debug("could not reset estimator chat thread query parameter", exc_info=True)
    return thread_id


def unique_columns(columns: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for column in columns:
        if column in seen:
            continue
        seen.add(column)
        out.append(column)
    return out


COMPACT_ALWAYS_SHOW_COLUMNS = {
    "include",
    "workbook_row",
    "template_line",
    "package",
    "template_bucket",
    "labor_package",
    "labor_task",
    "estimator_decision",
    "editable_selector_code",
    "resolved_template_option",
    "selected_pricing_candidate",
    "unit_price",
    "estimated_cost",
    "compatibility_status",
    "product_guidance_status",
    CHOICE_SUMMARY_COLUMN,
}


def compact_column_has_value(series: pd.Series) -> bool:
    if series.empty:
        return False
    for value in series:
        if value is None:
            continue
        if isinstance(value, float) and pd.isna(value):
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if float(value) != 0.0:
                return True
            continue
        text = str(value).strip()
        if not text or text.lower() in {"0", "0.0", "nan", "none", "null", "[]", "{}"}:
            continue
        return True
    return False


def project_display_frame(frame: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    available = [column for column in unique_columns(columns) if column in frame.columns]
    if CHOICE_SUMMARY_COLUMN in frame.columns and CHOICE_SUMMARY_COLUMN not in available:
        insert_after = next(
            (
                available.index(column) + 1
                for column in (
                    "resolved_template_option",
                    "labor_task",
                    "template_line",
                    "package",
                    "adder",
                    "workbook_row",
                )
                if column in available
            ),
            len(available),
        )
        available.insert(insert_after, CHOICE_SUMMARY_COLUMN)
    if not available:
        return frame.copy()
    compact_columns = [
        column
        for column in available
        if column not in COMPACT_DIAGNOSTIC_COLUMNS
        and (column in COMPACT_ALWAYS_SHOW_COLUMNS or compact_column_has_value(frame[column]))
    ]
    return frame[compact_columns or available].copy()


ESTIMATOR_PERF_TIMING_LIMIT = 160
ESTIMATOR_WORKBENCH_BUILD_CACHE_LIMIT = 10


def record_estimator_perf_event(
    name: str,
    *,
    seconds: float = 0.0,
    cache_status: str = "",
    detail: str = "",
    row_count: int | None = None,
) -> None:
    timings = st.session_state.setdefault("estimator_perf_timings", [])
    event: dict[str, Any] = {"step": name, "seconds": round(float(seconds or 0), 4)}
    if cache_status:
        event["cache_status"] = cache_status
    if detail:
        event["detail"] = str(detail)[:240]
    if row_count is not None:
        event["row_count"] = int(row_count)
    timings.append(event)
    st.session_state["estimator_perf_timings"] = timings[-ESTIMATOR_PERF_TIMING_LIMIT:]


@contextmanager
def estimator_perf_step(name: str, *, cache_status: str = "", detail: str = "", row_count: int | None = None):
    start = time.perf_counter()
    try:
        yield
    finally:
        record_estimator_perf_event(
            name,
            seconds=time.perf_counter() - start,
            cache_status=cache_status,
            detail=detail,
            row_count=row_count,
        )


def reset_estimator_perf_timings() -> None:
    st.session_state["estimator_perf_timings"] = []


def render_estimator_perf_timings() -> None:
    timings = st.session_state.get("estimator_perf_timings") or []
    if not timings:
        return
    with st.expander("Performance timings", expanded=False):
        timing_df = pd.DataFrame(timings)
        total_seconds = float(timing_df.get("seconds", pd.Series(dtype=float)).sum())
        cache_summary = ""
        if "cache_status" in timing_df.columns:
            cache_counts = timing_df["cache_status"].replace("", pd.NA).dropna().value_counts()
            if not cache_counts.empty:
                cache_summary = " | " + ", ".join(f"{key}: {int(value)}" for key, value in cache_counts.items())
        st.caption(f"Observed dashboard work: {total_seconds:.2f}s{cache_summary}")
        st.dataframe(timing_df, use_container_width=True, hide_index=True, height=220)


def stable_payload_hash(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def data_editor_state_key(base_key: str, display_df: pd.DataFrame | None) -> str:
    if not isinstance(display_df, pd.DataFrame) or display_df.empty:
        return f"{base_key}_empty"
    payload = display_df.to_dict(orient="records")
    return f"{base_key}_{stable_payload_hash(payload)[:10]}"


def cached_export_path_for_ui(state_key: str, cache_key: str, timing_name: str) -> Path | None:
    cached = st.session_state.get(state_key)
    if not isinstance(cached, dict) or cached.get("key") != cache_key:
        return None
    cached_path_value = cached.get("path")
    if not cached_path_value:
        return None
    cached_path = Path(str(cached_path_value))
    if not cached_path.exists():
        return None
    record_estimator_perf_event(timing_name, cache_status="hit", detail="unchanged export inputs")
    return cached_path


def store_export_path_for_ui(state_key: str, cache_key: str, path: Path) -> None:
    st.session_state[state_key] = {
        "key": cache_key,
        "path": str(path),
        "created_at": pd.Timestamp.utcnow().isoformat(),
    }


def recalculate_workbench_tables_with_optional_data(workbench: dict[str, Any], data: EstimatorData | None = None) -> dict[str, Any]:
    signature = inspect.signature(recalculate_workbench_tables)
    if "data" in signature.parameters:
        return recalculate_workbench_tables(workbench, data=data)
    return recalculate_workbench_tables(workbench)


def recalculate_workbench_tables_for_ui(workbench: dict[str, Any], data: EstimatorData | None = None) -> dict[str, Any]:
    cache_key = stable_payload_hash({"workbench": workbench, "data": estimator_data_signature(data) if data is not None else {}})
    state_key = "estimator_recalculated_workbench_cache"
    cached = st.session_state.get(state_key)
    if isinstance(cached, dict) and cached.get("key") == cache_key and isinstance(cached.get("workbench"), dict):
        record_estimator_perf_event("workbench recalculation", cache_status="hit", detail="unchanged workbench inputs")
        return copy.deepcopy(cached["workbench"])
    with estimator_perf_step("workbench recalculation", cache_status="miss"):
        recalculated = recalculate_workbench_tables_with_optional_data(workbench, data=data)
    st.session_state[state_key] = {"key": cache_key, "workbench": copy.deepcopy(recalculated)}
    return recalculated


def draft_workbook_inputs_for_ui(workbench: dict[str, Any]) -> dict[str, Any]:
    cache_key = stable_payload_hash(workbench)
    state_key = "estimator_draft_workbook_inputs_cache"
    cached = st.session_state.get(state_key)
    if isinstance(cached, dict) and cached.get("key") == cache_key and isinstance(cached.get("draft"), dict):
        record_estimator_perf_event("draft workbook input build", cache_status="hit", detail="unchanged workbench inputs")
        return copy.deepcopy(cached["draft"])
    with estimator_perf_step("draft workbook input build", cache_status="miss"):
        draft = workbench_to_draft_workbook_inputs(workbench, recalculate=False)
    st.session_state[state_key] = {"key": cache_key, "draft": copy.deepcopy(draft)}
    return draft


def estimator_data_signature(data: EstimatorData) -> dict[str, Any]:
    return {
        "source_files_used": list(data.source_files_used or []),
        "template_rows": len(data.template_rows),
        "pricing": len(data.pricing),
        "product_catalog": len(data.product_catalog),
        "product_properties": len(data.product_properties),
        "template_product_options": len(data.template_product_options),
        "job_context_profiles": len(getattr(data, "job_context_profiles", pd.DataFrame())),
        "template_examples": len(getattr(data, "template_examples", pd.DataFrame())),
        "foam_yield_history": len(getattr(data, "foam_yield_history", pd.DataFrame())),
        "estimator_decision_recommendations": len(data.estimator_decision_recommendations),
    }


def estimator_data_table_count(data: Any, attr: str) -> int:
    value = getattr(data, attr, None)
    if value is None:
        return 0
    try:
        return len(value)
    except TypeError:
        return 0


def recommendation_cache_payload(recommendation: Any) -> dict[str, Any]:
    return {
        "parsed_fields": getattr(recommendation, "parsed_fields", {}) or {},
        "review_flags": getattr(recommendation, "review_flags", []) or [],
        "estimate_status": getattr(recommendation, "estimate_status", ""),
        "estimate_reason": getattr(recommendation, "estimate_reason", ""),
        "required_questions": getattr(recommendation, "required_questions", []) or [],
    }


def build_estimating_workbench_for_ui(
    recommendation: Any,
    data: EstimatorData,
    *,
    scope_override: dict[str, Any] | None = None,
    historical_filters: dict[str, Any] | None = None,
    timing_label: str = "workbench build",
) -> dict[str, Any]:
    cache_payload = {
        "recommendation": recommendation_cache_payload(recommendation),
        "data": estimator_data_signature(data),
        "scope_override": scope_override or {},
        "historical_filters": historical_filters or {},
    }
    cache_key = stable_payload_hash(cache_payload)
    state_key = "estimator_build_workbench_cache"
    cache = st.session_state.setdefault(state_key, {})
    if isinstance(cache, dict) and cache_key in cache and isinstance(cache[cache_key], dict):
        record_estimator_perf_event(timing_label, cache_status="hit", detail="unchanged recommendation, data, scope, and filters")
        return copy.deepcopy(cache[cache_key])
    with estimator_perf_step(timing_label, cache_status="miss"):
        workbench = build_estimating_workbench(
            recommendation,
            data,
            scope_override=scope_override,
            historical_filters=historical_filters,
        )
    if not isinstance(cache, dict):
        cache = {}
        st.session_state[state_key] = cache
    cache[cache_key] = copy.deepcopy(workbench)
    while len(cache) > ESTIMATOR_WORKBENCH_BUILD_CACHE_LIMIT:
        cache.pop(next(iter(cache)))
    return workbench


def estimator_reference_job_options(data: EstimatorData, *, template_type: str = "") -> tuple[list[str], dict[str, str]]:
    jobs = getattr(data, "jobs", pd.DataFrame())
    template_rows = getattr(data, "template_rows", pd.DataFrame())
    rows: dict[str, dict[str, Any]] = {}

    def add_row(row: dict[str, Any]) -> None:
        job_id = text_value(row.get("job_id"))
        if not job_id:
            return
        existing = rows.setdefault(job_id, {"job_id": job_id})
        for key in ("customer", "job_name", "division", "template_type", "project_type", "source_file", "estimated_sqft"):
            if not text_value(existing.get(key)) and text_value(row.get(key)):
                existing[key] = row.get(key)

    if isinstance(jobs, pd.DataFrame) and not jobs.empty:
        for row in jobs.to_dict(orient="records"):
            add_row(row)
    if isinstance(template_rows, pd.DataFrame) and not template_rows.empty:
        frame = template_rows
        requested_template = text_value(template_type).lower()
        if requested_template and "template_type" in frame.columns:
            scoped = frame[frame["template_type"].fillna("").astype(str).str.lower().eq(requested_template)]
            if not scoped.empty:
                frame = scoped
        for row in frame.to_dict(orient="records"):
            add_row(row)

    def label(row: dict[str, Any]) -> str:
        title = text_value(row.get("job_name")) or text_value(row.get("source_file")) or text_value(row.get("job_id"))
        customer = text_value(row.get("customer"))
        project_type = text_value(row.get("project_type") or row.get("template_type"))
        sqft = text_value(row.get("estimated_sqft"))
        parts = [part for part in (customer, title) if part]
        label_text = " - ".join(parts) if parts else text_value(row.get("job_id"))
        suffix = " | ".join(part for part in (project_type, f"{sqft} sqft" if sqft else "") if part)
        return f"{label_text} ({row['job_id']})" + (f" - {suffix}" if suffix else "")

    sorted_rows = sorted(rows.values(), key=lambda row: label(row).lower())
    options = [str(row["job_id"]) for row in sorted_rows]
    labels = {str(row["job_id"]): label(row) for row in sorted_rows}
    return options, labels


def render_workbench_selected_row_details(
    workbench: dict[str, Any],
    *,
    workbench_key: str,
    scope_key: str,
    historical_filters_key: str,
) -> None:
    sections: list[tuple[str, str, list[dict[str, Any]]]] = []
    for section_key, section_label in [
        ("insulation_foam_template_decisions", "Insulation Foam"),
        *INSULATION_DECISION_SECTIONS,
        ("roofing_foam_template_decisions", "Roofing Foam"),
        ("roofing_coating_template_decisions", "Roof Coating"),
        ("roofing_primer_template_decisions", "Roof Primer"),
        ("roofing_detail_template_decisions", "Roof Detail Materials"),
        ("roofing_detail_quantity_template_decisions", "Roof Detail Quantities"),
        ("roofing_board_fastener_template_decisions", "Roof Board / Fasteners"),
        ("roofing_granules_template_decisions", "Roof Granules"),
        ("roofing_equipment_template_decisions", "Roof Equipment"),
        ("roofing_travel_freight_template_decisions", "Roof Travel / Freight"),
        ("roofing_accessory_template_decisions", "Roof Accessories"),
        ("roofing_logistics_expense_template_decisions", "Roof Loading / Travel / Lodging"),
        ("roofing_free_adder_template_decisions", "Roof Free Adders"),
        ("roofing_labor_template_decisions", "Roof Labor"),
        ("pricing_markup_decisions", "Pricing Markup"),
    ]:
        rows = [row for row in workbench.get(section_key) or [] if isinstance(row, dict)]
        if rows:
            sections.append((section_key, section_label, rows))
    if not sections:
        return
    with st.expander("Selected row details", expanded=False):
        section_labels = [label for _, label, _ in sections]
        selected_label = st.selectbox(
            "Section",
            section_labels,
            key=f"wb_row_detail_section_{workbench_key}_{scope_key}_{historical_filters_key}",
        )
        section_key, _, rows = next(item for item in sections if item[1] == selected_label)

        def row_label(row: dict[str, Any]) -> str:
            name = text_value(
                row.get("resolved_template_option")
                or row.get("template_line")
                or row.get("labor_task")
                or row.get("package")
                or row.get("decision_id")
            )
            row_number = text_value(row.get("workbook_row"))
            return f"Row {row_number} - {name}" if row_number else name or "Decision row"

        row_options = list(range(len(rows)))
        selected_idx = st.selectbox(
            "Row",
            row_options,
            format_func=lambda idx: row_label(rows[int(idx)]),
            key=f"wb_row_detail_row_{section_key}_{workbench_key}_{scope_key}_{historical_filters_key}",
        )
        selected_row = rows[int(selected_idx)]
        summary_columns = [
            "include",
            "workbook_row",
            "resolved_template_option",
            "template_line",
            "labor_task",
            "basis_sqft",
            "quantity",
            "unit_price",
            "estimated_units",
            "estimated_cost",
            "compatibility_status",
            CHOICE_SUMMARY_COLUMN,
        ]
        summary = projected_display_records([selected_row], summary_columns)
        if summary:
            st.dataframe(pd.DataFrame(summary), use_container_width=True, hide_index=True)
        full_explanation = choice_summary_for_row(selected_row)
        if full_explanation:
            st.text_area(
                "Why this choice",
                value=full_explanation,
                height=220,
                disabled=True,
                key=f"wb_row_detail_why_{section_key}_{selected_idx}_{workbench_key}_{scope_key}_{historical_filters_key}",
            )
        detail_keys = [
            key
            for key in (
                "decision_evidence_summary",
                "why_included",
                "historical_evidence_summary",
                "pricing_evidence_summary",
                "product_evidence_summary",
                "formula_evidence_summary",
                "proposal_source",
                "proposal_confidence",
                "proposal_review_required",
                "proposal_review_reasons",
                "compatibility_warnings",
                "product_guidance",
                "product_guidance_status",
                "selected_pricing_candidate",
                "pricing_candidates",
                "selector_options",
                "workbook_cell_write_preview",
                "notes",
            )
            if key in selected_row and text_value(display_safe_cell_value(selected_row.get(key)))
        ]
        if detail_keys:
            detail_rows = [{"field": key, "value": display_safe_cell_value(selected_row.get(key))} for key in detail_keys]
            st.dataframe(pd.DataFrame(detail_rows), use_container_width=True, hide_index=True, height=260)


def parse_reference_job_ids(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple, set)):
        return [text_value(item) for item in value if text_value(item)]
    text = str(value)
    for token in ("\n", ";", "|"):
        text = text.replace(token, ",")
    return [part.strip() for part in text.split(",") if part.strip()]


def render_estimator_photo_upload_panel(*, notes: str, estimate_type: str) -> dict[str, Any] | None:
    st.subheader("Photo Evidence")
    st.caption(
        "Upload photos freely. The app stores them, generates thumbnails, classifies obvious signals locally, "
        "and selects a small representative set. No vision API call runs unless you click Analyze Selected Photos with AI."
    )
    upload_key_source = f"{estimate_type}|{notes}|{current_estimator_session_id() or 'draft'}"
    upload_key = hashlib.sha1(upload_key_source.encode("utf-8")).hexdigest()[:16]
    uploaded_files = st.file_uploader(
        "Upload job photos",
        type=["jpg", "jpeg", "png", "webp", "bmp", "tif", "tiff"],
        accept_multiple_files=True,
        key=f"estimator_photo_uploads_{upload_key}",
        help="Photos are processed locally first. Use selected photos as decision evidence only when you choose to include them.",
    )
    if not uploaded_files:
        st.session_state.pop("estimator_photo_context", None)
        st.session_state.pop("estimator_photo_records", None)
        return None

    photo_records = stage_uploaded_images(uploaded_files, upload_key=upload_key)
    if not photo_records:
        st.info("No readable image files were uploaded.")
        return None

    st.session_state["estimator_photo_records"] = photo_records
    selected_default = {record.get("content_hash") for record in photo_records if record.get("selected")}
    photo_rows = [
        {
            "use": record.get("content_hash") in selected_default,
            "file_name": record.get("file_name"),
            "category": record.get("category"),
            "signals": ", ".join(record.get("signals") or []),
            "quality_flags": ", ".join(record.get("quality_flags") or []),
            "image_id": record.get("image_id"),
            "content_hash": record.get("content_hash"),
        }
        for record in photo_records
    ]
    photo_df = pd.DataFrame(photo_rows)
    signal_help = "Comma-separated decision signals. Accepted: " + ", ".join(PHOTO_SIGNAL_OPTIONS)
    edited_photo_df = st.data_editor(
        photo_df[["use", "file_name", "category", "signals", "quality_flags", "image_id"]],
        hide_index=True,
        use_container_width=True,
        num_rows="fixed",
        key=f"estimator_photo_selection_{upload_key}",
        column_config={
            "use": "Use",
            "file_name": "Photo",
            "category": st.column_config.SelectboxColumn(
                "Local Category",
                options=PHOTO_CATEGORY_OPTIONS,
                help="Cheap local classification. Update this when filenames are generic.",
            ),
            "signals": st.column_config.TextColumn(
                "Decision Signals",
                help=signal_help,
            ),
            "quality_flags": "Quality Flags",
            "image_id": "Image ID",
        },
        disabled=["file_name", "quality_flags", "image_id"],
    )
    edited_photo_records = apply_photo_record_edits(photo_records, edited_photo_df.to_dict(orient="records"))
    st.session_state["estimator_photo_records"] = edited_photo_records
    selected_image_ids = {str(row.get("image_id")) for row in edited_photo_df.to_dict(orient="records") if row.get("use") and row.get("image_id")}
    selected_hashes = [str(record.get("content_hash")) for record in edited_photo_records if str(record.get("image_id")) in selected_image_ids]
    selected_records = [record for record in edited_photo_records if record.get("content_hash") in set(selected_hashes)]
    if selected_records:
        preview_cols = st.columns(min(len(selected_records), 4))
        for idx, record in enumerate(selected_records[:4]):
            thumb = record.get("thumbnail_path")
            if thumb and Path(str(thumb)).exists():
                preview_cols[idx % len(preview_cols)].image(str(thumb), caption=str(record.get("file_name") or ""), use_container_width=True)

    estimate_type_text = str(estimate_type or "").lower()
    template_hint = "insulation" if "insulation" in estimate_type_text else "roofing" if "roof" in estimate_type_text else ""
    photo_context = build_photo_scope_context(edited_photo_records, selected_hashes=selected_hashes, template_type=template_hint)
    try:
        max_ai_images = max(1, int(os.getenv("OPENAI_ESTIMATOR_PHOTO_MAX_IMAGES", "8")))
    except (TypeError, ValueError):
        max_ai_images = 8
    ai_analysis_key = f"estimator_photo_ai_analysis_{upload_key}"
    selected_hash_set = {str(value) for value in selected_hashes if str(value)}
    stored_ai_analysis = st.session_state.get(ai_analysis_key)
    if stored_ai_analysis and set(str(value) for value in (stored_ai_analysis.get("selected_hashes") or [])) == selected_hash_set:
        photo_context = merge_photo_ai_analysis(photo_context, stored_ai_analysis, records=edited_photo_records)
    elif stored_ai_analysis:
        st.caption("Stored AI photo analysis is hidden because the selected photo set changed.")
    st.caption(
        f"Optional AI photo analysis sends at most {max_ai_images} selected image(s), uses cached results when available, "
        "and returns structured estimator notes."
    )
    analyze_disabled = not selected_hashes
    if st.button(
        "Analyze Selected Photos with AI",
        key=f"estimator_analyze_photos_ai_{upload_key}",
        disabled=analyze_disabled,
        help="Runs only when clicked. The selected photos are sent to the configured OpenAI model with low-detail image inputs.",
    ):
        if not os.getenv("OPENAI_API_KEY"):
            st.warning("OPENAI_API_KEY is not configured for this runtime, so AI photo analysis cannot run.")
        else:
            try:
                with st.spinner("Analyzing selected photos..."):
                    stored_ai_analysis = analyze_selected_photos_with_ai(
                        edited_photo_records,
                        selected_hashes=selected_hashes,
                        template_type=template_hint,
                        notes=notes,
                        max_images=max_ai_images,
                    )
                st.session_state[ai_analysis_key] = stored_ai_analysis
                photo_context = merge_photo_ai_analysis(photo_context, stored_ai_analysis, records=edited_photo_records)
                if stored_ai_analysis.get("cache_hit"):
                    st.success("Loaded cached AI photo analysis for this selected photo set.")
                else:
                    st.success("AI photo analysis added to the selected photo evidence.")
            except Exception as exc:
                st.warning(f"AI photo analysis failed: {type(exc).__name__}: {exc}")
    st.session_state["estimator_photo_context"] = photo_context
    use_photo_evidence = st.checkbox(
        "Use selected photo evidence to fill review-marked decisions",
        value=True,
        key=f"estimator_use_photo_evidence_{upload_key}",
        help="Photo evidence becomes review-required decision evidence. It does not call the vision API and does not override estimator edits.",
    )
    st.session_state["estimator_use_photo_evidence"] = use_photo_evidence

    metric_row(
        [
            ("Uploaded", str(photo_context.get("image_count") or 0)),
            ("Selected", str(photo_context.get("selected_image_count") or 0)),
            ("Local Signals", str(len(photo_context.get("signals") or []))),
            ("Confidence", f"{float(photo_context.get('confidence') or 0):.2f}"),
        ]
    )
    if photo_context.get("note_text"):
        st.info(photo_context["note_text"])
    if photo_context.get("ai_photo_analysis_used"):
        ai_analysis = photo_context.get("ai_photo_analysis") or {}
        if ai_analysis.get("cache_hit"):
            st.caption("AI photo analysis source: cached")
        else:
            st.caption("AI photo analysis source: current run")
    if photo_context.get("missing_photos"):
        st.warning("Missing useful photos: " + "; ".join(str(item) for item in photo_context.get("missing_photos") or []))
    return photo_context if use_photo_evidence else None


NOTE_IMAGE_NAME_HINTS = (
    "note",
    "notes",
    "field",
    "sketch",
    "drawing",
    "measure",
    "measurement",
    "scope",
    "handwritten",
    "whiteboard",
)


def estimator_upload_default_rows(photo_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in photo_records or []:
        category = str(record.get("category") or "unknown")
        signals = [str(signal) for signal in (record.get("signals") or []) if str(signal)]
        file_name = str(record.get("file_name") or "")
        file_text = file_name.lower()
        has_photo_signal = category != "unknown" or bool(signals)
        note_hint = any(token in file_text for token in NOTE_IMAGE_NAME_HINTS)
        read_as_notes = bool(note_hint or not has_photo_signal)
        use_as_site_photo = bool(record.get("selected") and has_photo_signal and not note_hint)
        rows.append(
            {
                "read_as_notes": read_as_notes,
                "use_as_site_photo": use_as_site_photo,
                "file_name": file_name,
                "category": category,
                "signals": ", ".join(signals),
                "quality_flags": ", ".join(str(flag) for flag in (record.get("quality_flags") or []) if str(flag)),
                "image_id": record.get("image_id"),
                "content_hash": record.get("content_hash"),
            }
        )
    return rows


def render_estimator_note_image_upload(*, chat_key: str, estimate_type: str) -> dict[str, Any] | None:
    try:
        max_note_images = max(1, int(os.getenv("OPENAI_ESTIMATOR_NOTE_IMAGE_MAX_IMAGES", "3")))
    except (TypeError, ValueError):
        max_note_images = 3
    uploaded_files = st.file_uploader(
        "Upload notes or site photos",
        type=["jpg", "jpeg", "png", "webp", "bmp", "tif", "tiff", "heic", "heif"],
        accept_multiple_files=True,
        key=f"estimator_note_image_uploads_{chat_key}",
        help=(
            "Mark handwritten/printed pages as notes and job-condition photos as site photos. "
            "Only note-marked images are parsed automatically; site photos are classified locally unless you run photo analysis separately."
        ),
    )
    if not uploaded_files:
        return None
    upload_key = hashlib.sha1(
        (
            chat_key
            + "|"
            + estimate_type
            + "|"
            + "|".join(str(getattr(file, "name", "") or "") for file in uploaded_files)
        ).encode("utf-8")
    ).hexdigest()[:16]
    photo_records = stage_uploaded_images(uploaded_files, upload_key=f"notes-and-photos-{upload_key}")
    if not photo_records:
        st.warning("No readable image files were uploaded.")
        return None
    upload_rows = estimator_upload_default_rows(photo_records)
    upload_df = pd.DataFrame(upload_rows)
    edited_upload_df = st.data_editor(
        upload_df[["read_as_notes", "use_as_site_photo", "file_name", "category", "signals", "quality_flags", "image_id"]],
        hide_index=True,
        use_container_width=True,
        num_rows="fixed",
        key=f"estimator_note_site_image_review_{chat_key}_{upload_key}",
        column_config={
            "read_as_notes": st.column_config.CheckboxColumn(
                "Read as notes",
                help="These images are sent to the note-reading model, capped by the note image limit.",
            ),
            "use_as_site_photo": st.column_config.CheckboxColumn(
                "Use as site photo",
                help="These images add photo-derived scope evidence and review flags.",
            ),
            "file_name": "Image",
            "category": st.column_config.SelectboxColumn("Photo Category", options=PHOTO_CATEGORY_OPTIONS),
            "signals": st.column_config.TextColumn(
                "Photo Signals",
                help="Comma-separated decision signals. Accepted: " + ", ".join(PHOTO_SIGNAL_OPTIONS),
            ),
            "quality_flags": "Quality Flags",
            "image_id": "Image ID",
        },
        disabled=["file_name", "quality_flags", "image_id"],
    )
    edited_rows = edited_upload_df.to_dict(orient="records")
    edited_photo_records = apply_photo_record_edits(photo_records, edited_rows)
    site_image_ids = {str(row.get("image_id")) for row in edited_rows if row.get("use_as_site_photo") and row.get("image_id")}
    selected_hashes = [
        str(record.get("content_hash"))
        for record in edited_photo_records
        if str(record.get("image_id")) in site_image_ids and record.get("content_hash")
    ]
    template_hint = "insulation" if "insulation" in str(estimate_type or "").lower() else "roofing" if "roof" in str(estimate_type or "").lower() else ""
    photo_context = build_photo_scope_context(
        edited_photo_records,
        selected_hashes=selected_hashes,
        template_type=template_hint,
    ) if selected_hashes else None
    if photo_context:
        st.session_state["estimator_photo_context"] = photo_context
        st.caption(
            f"Site photo evidence selected from {photo_context.get('selected_image_count') or 0} image(s); "
            f"local signals: {len(photo_context.get('signals') or [])}."
        )
        if photo_context.get("note_text"):
            with st.expander("Review site photo scope interpretation", expanded=False):
                st.write(str(photo_context.get("note_text") or ""))
                if photo_context.get("missing_photos"):
                    st.write("Missing useful photos:", "; ".join(str(item) for item in photo_context.get("missing_photos") or []))
    else:
        st.session_state.pop("estimator_photo_context", None)

    edited_note_image_ids = {str(row.get("image_id")) for row in edited_rows if row.get("read_as_notes") and row.get("image_id")}
    note_hashes = {
        str(record.get("content_hash"))
        for record in edited_photo_records
        if str(record.get("image_id")) in edited_note_image_ids and record.get("content_hash")
    }
    note_uploaded_files = []
    if note_hashes:
        for uploaded in uploaded_files:
            try:
                data = bytes(uploaded.getvalue()) if hasattr(uploaded, "getvalue") else bytes(uploaded.read())
            except Exception:
                data = b""
            if data and hashlib.sha256(data).hexdigest() in note_hashes:
                note_uploaded_files.append(uploaded)
    records = [
        record
        for record in stage_note_images(note_uploaded_files, upload_key=upload_key)
        if str(record.get("content_hash") or "") in note_hashes
    ]
    if not records:
        return {
            "records": [],
            "normalized_estimator_notes": "",
            "warnings": [],
            "confidence": 0.0,
            "image_hash_key": "",
            "photo_context": photo_context,
        }
    image_hash_key = hashlib.sha1(
        "|".join(str(record.get("content_hash") or "") for record in records).encode("utf-8")
    ).hexdigest()[:16]
    result_key = f"estimator_note_image_extraction_{chat_key}_{image_hash_key}"
    result = st.session_state.get(result_key)
    if result is None:
        if not os.getenv("OPENAI_API_KEY"):
            st.warning("OPENAI_API_KEY is not configured, so uploaded note images cannot be parsed automatically.")
            return {
                "records": records,
                "normalized_estimator_notes": "",
                "warnings": ["OPENAI_API_KEY is not configured."],
                "confidence": 0.0,
                "image_hash_key": image_hash_key,
            }
        try:
            with st.spinner("Reading uploaded notes..."):
                result = extract_notes_from_images_with_ai(records, max_images=max_note_images)
            st.session_state[result_key] = result
        except Exception as exc:
            result = {
                "records": records,
                "normalized_estimator_notes": "",
                "warnings": [f"Note image parsing failed: {type(exc).__name__}: {exc}"],
                "confidence": 0.0,
                "image_hash_key": image_hash_key,
            }
            st.session_state[result_key] = result
    result = dict(result)
    result["records"] = records
    result["image_hash_key"] = image_hash_key
    result["photo_context"] = photo_context
    notes_text = str(result.get("normalized_estimator_notes") or "").strip()
    confidence = float(result.get("confidence") or 0.0)
    if notes_text:
        st.caption(
            f"Uploaded notes parsed from {min(len(records), max_note_images)} image(s)"
            + (" using cached extraction." if result.get("cache_hit") else ".")
            + f" Confidence: {confidence:.2f}"
        )
        with st.expander("Review extracted note text", expanded=False):
            reviewed_notes = st.text_area(
                "Extracted notes",
                value=notes_text,
                height=180,
                key=f"estimator_note_image_text_{chat_key}_{image_hash_key}",
            )
            result["normalized_estimator_notes"] = str(reviewed_notes or "").strip()
            questions = result.get("questions") or []
            unreadable = result.get("unreadable_regions") or []
            if questions:
                st.write("Questions:", "; ".join(str(item) for item in questions))
            if unreadable:
                st.write("Unreadable:", "; ".join(str(item) for item in unreadable))
    for warning in result.get("warnings") or []:
        st.warning(str(warning))
    return result


def render_estimator_chat_draft_panel(
    *,
    notes: str,
    estimate_type: str,
    data: EstimatorData,
) -> dict[str, Any] | None:
    thread_id = current_estimator_chat_thread_id()
    chat_key = str(thread_id)
    history_key = f"estimator_chat_history_{chat_key}"
    result_key = f"estimator_chat_result_{chat_key}"
    active_history_key = "estimator_chat_history_active"
    active_result_key = "estimator_chat_result_active"
    if st.button("Start a new estimate chat", key=f"estimator_chat_reset_{chat_key}"):
        st.session_state.pop(history_key, None)
        st.session_state.pop(result_key, None)
        st.session_state.pop(active_history_key, None)
        st.session_state.pop(active_result_key, None)
        st.session_state.pop("estimator_notes", None)
        reset_current_estimator_chat_thread()
        st.rerun()

    chat_history = [dict(message) for message in (st.session_state.get(history_key) or [])]
    if not chat_history and isinstance(st.session_state.get(active_history_key), list):
        chat_history = [dict(message) for message in (st.session_state.get(active_history_key) or [])]
        st.session_state[history_key] = chat_history
    if not chat_history and not st.session_state.get(result_key):
        saved_chat = load_estimator_chat_session(chat_key)
        saved_history = saved_chat.get("history") if isinstance(saved_chat.get("history"), list) else []
        saved_result = saved_chat.get("result") if isinstance(saved_chat.get("result"), dict) else {}
        if saved_history:
            chat_history = [dict(message) for message in saved_history if isinstance(message, dict)]
            st.session_state[history_key] = chat_history
            st.session_state[active_history_key] = chat_history
        if saved_result:
            st.session_state[result_key] = saved_result
            st.session_state[active_result_key] = saved_result
        saved_notes = str(saved_chat.get("estimator_notes") or "").strip()
        if saved_notes and not st.session_state.get("estimator_notes"):
            st.session_state["estimator_notes"] = saved_notes
    note_image_result = render_estimator_note_image_upload(chat_key=chat_key, estimate_type=estimate_type)
    extracted_note_text = str((note_image_result or {}).get("normalized_estimator_notes") or "").strip()
    extracted_note_key = str((note_image_result or {}).get("image_hash_key") or "")
    uploaded_photo_context = (
        note_image_result.get("photo_context")
        if isinstance(note_image_result, dict) and isinstance(note_image_result.get("photo_context"), dict)
        else None
    )
    prompt_placeholder = (
        "Paste field notes, measurements, photos summary, or answer the questions above. Example: "
        "30x40 metal building, 9 ft walls, outside walls and ceiling, two 9x9 doors, "
        "two walk doors, five windows, wants open-cell foam this fall."
    )
    prompt = st.chat_input(
        prompt_placeholder,
        key=f"estimator_chat_input_{chat_key}",
    )
    pending_message = str(st.session_state.pop("estimator_chat_pending_message", "") or "").strip()
    typed_message = pending_message or str(prompt or "").strip()
    image_message_applied_key = f"estimator_note_image_chat_applied_{chat_key}_{extracted_note_key}"
    image_message = ""
    if extracted_note_text and not st.session_state.get(image_message_applied_key):
        image_message = "Notes extracted from uploaded note image(s):\n" + extracted_note_text
    photo_message = ""
    photo_context_key = ""
    if uploaded_photo_context:
        photo_context_key = hashlib.sha1(
            json.dumps(
                {
                    "selected_hashes": uploaded_photo_context.get("selected_hashes") or [],
                    "signals": uploaded_photo_context.get("signals") or [],
                    "note_text": uploaded_photo_context.get("note_text") or "",
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:16]
    photo_message_applied_key = f"estimator_site_photo_chat_applied_{chat_key}_{photo_context_key}"
    if uploaded_photo_context and uploaded_photo_context.get("note_text") and not st.session_state.get(photo_message_applied_key):
        photo_message = "Site photo evidence summary:\n" + str(uploaded_photo_context.get("note_text") or "")
    user_message = "\n\n".join(part for part in [typed_message, image_message, photo_message] if part)
    use_chat_draft = st.checkbox(
        "Use this draft when building the workbook",
        value=True,
        key=f"estimator_chat_use_{chat_key}",
    )
    if user_message:
        messages = [dict(message) for message in chat_history]
        messages.append({"role": "user", "content": user_message})
        previous_result = st.session_state.get(result_key)
        existing_scope = (
            previous_result.get("scope_overrides")
            if isinstance(previous_result, dict) and isinstance(previous_result.get("scope_overrides"), dict)
            else {}
        )
        attached_reference_answer_key = (
            previous_result.get("reference_answer_key")
            if isinstance(previous_result, dict) and isinstance(previous_result.get("reference_answer_key"), dict)
            else {}
        )
        with st.spinner("Drafting estimate intake..."):
            context_cache_before = estimator_context_cache_stats()
            with estimator_perf_step("chat context and response"):
                result = run_estimator_chat_turn(
                    messages,
                    data=data,
                    template_type_hint=estimate_type,
                    existing_scope=existing_scope,
                    attached_reference_answer_key=attached_reference_answer_key,
                )
            context_cache_after = estimator_context_cache_stats()
            context_cache_hits = int(context_cache_after.get("hit", 0)) - int(context_cache_before.get("hit", 0))
            context_cache_misses = int(context_cache_after.get("miss", 0)) - int(context_cache_before.get("miss", 0))
            if context_cache_hits or context_cache_misses:
                record_estimator_perf_event(
                    "chat context digest",
                    cache_status="hit" if context_cache_hits and not context_cache_misses else "miss" if context_cache_misses else "hit",
                    detail=f"context cache hits={context_cache_hits}, misses={context_cache_misses}",
                )
        messages.append({"role": "assistant", "content": estimator_chat_assistant_history_content(result)})
        st.session_state[history_key] = messages
        st.session_state[active_history_key] = messages
        result_payload = result.to_dict()
        result_payload = preserve_attached_reference_answer_key_context(
            result_payload,
            previous_result if isinstance(previous_result, dict) else {},
            attached_reference_answer_key,
        )
        if uploaded_photo_context:
            result_payload["photo_context"] = uploaded_photo_context
        if estimator_chat_learning_mode(result_payload):
            learning_intent = result_payload.get("learning_intent") if isinstance(result_payload.get("learning_intent"), dict) else {}
            if learning_intent.get("auto_build_workbook", True):
                st.session_state["estimator_auto_build_requested"] = True
        st.session_state[result_key] = result_payload
        st.session_state[active_result_key] = result_payload
        st.session_state["estimator_notes"] = result.estimator_notes or user_message
        save_estimator_chat_session(
            chat_key,
            history=messages,
            result=result_payload,
            estimator_notes=str(st.session_state.get("estimator_notes") or ""),
            estimate_type=estimate_type,
        )
        if image_message:
            st.session_state[image_message_applied_key] = True
        if photo_message:
            st.session_state[photo_message_applied_key] = True
        chat_history = messages

    result_payload = st.session_state.get(result_key) or st.session_state.get(active_result_key)
    if not result_payload:
        if not chat_history:
            st.caption("Paste field notes or answer follow-up questions in the message box.")
        return None
    result = result_payload if isinstance(result_payload, dict) else {}
    if result and not st.session_state.get("estimator_notes"):
        fallback_notes = str(result.get("estimator_notes") or "").strip()
        if not fallback_notes:
            fallback_notes = "\n\n".join(
                str(message.get("content") or "")
                for message in chat_history
                if str(message.get("role") or "") == "user"
            ).strip()
        if fallback_notes:
            st.session_state["estimator_notes"] = fallback_notes
    if uploaded_photo_context:
        result["photo_context"] = uploaded_photo_context
    if not chat_history and result:
        chat_history = [{"role": "assistant", "content": estimator_chat_assistant_history_content(result)}]
        st.session_state[history_key] = chat_history
        st.session_state[active_history_key] = chat_history
        save_estimator_chat_session(
            chat_key,
            history=chat_history,
            result=result,
            estimator_notes=str(st.session_state.get("estimator_notes") or ""),
            estimate_type=estimate_type,
        )
    for message in chat_history[-10:]:
        role = str(message.get("role") or "assistant")
        with st.chat_message("user" if role == "user" else "assistant"):
            st.write(str(message.get("content") or ""))
    raw_response = result.get("raw_response") if isinstance(result.get("raw_response"), dict) else {}
    historical_answer_key_matches = raw_response.get("historical_answer_key_matches") if isinstance(raw_response, dict) else []
    if historical_answer_key_matches:
        with st.expander("Historical answer keys used", expanded=False):
            st.dataframe(
                pd.DataFrame(historical_answer_key_matches),
                use_container_width=True,
                hide_index=True,
            )
    return result if use_chat_draft else None


def json_list_value(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if not text_value(value):
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def decision_row_selector_options(row: dict[str, Any]) -> list[dict[str, Any]]:
    options = json_list_value(row.get("selector_options")) or json_list_value(row.get("selector_options_json"))
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for option in options:
        if not isinstance(option, dict):
            continue
        selector_code = text_value(option.get("selector_code") or option.get("code") or option.get("value"))
        label = text_value(
            option.get("resolved_template_option")
            or option.get("template_option")
            or option.get("label")
            or option.get("description")
        )
        if not selector_code and not label:
            continue
        key = (selector_code, label)
        if key in seen:
            continue
        seen.add(key)
        normalized.append({**option, "selector_code": selector_code, "resolved_template_option": label})
    return normalized


def decision_row_pricing_options(row: dict[str, Any]) -> list[dict[str, Any]]:
    options = (
        json_list_value(row.get("item_options_json"))
        or json_list_value(row.get("pricing_options"))
        or json_list_value(row.get("pricing_options_json"))
    )
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for option in options:
        if not isinstance(option, dict):
            continue
        item_name = text_value(
            option.get("item_name")
            or option.get("selected_pricing_candidate")
            or option.get("resolved_template_option")
            or option.get("label")
        )
        if not item_name or item_name in seen:
            continue
        seen.add(item_name)
        normalized.append({**option, "item_name": item_name})
    return normalized


def decision_row_crew_options(row: dict[str, Any]) -> list[dict[str, Any]]:
    options = json_list_value(row.get("crew_selector_options")) or json_list_value(row.get("crew_selector_options_json"))
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for option in options:
        if not isinstance(option, dict):
            continue
        selector_code = text_value(option.get("selector_code") or option.get("code") or option.get("value"))
        label = text_value(option.get("resolved_template_option") or option.get("label") or option.get("description"))
        key = selector_code or label
        if not key or key in seen:
            continue
        seen.add(key)
        normalized.append({**option, "selector_code": selector_code, "resolved_template_option": label})
    return normalized


def decision_row_label(row: dict[str, Any], idx: int) -> str:
    row_id = text_value(row.get("workbook_row") or row.get("row_number") or idx + 1)
    label = text_value(
        row.get("resolved_template_option")
        or row.get("template_line")
        or row.get("labor_task")
        or row.get("template_bucket")
        or row.get("decision_id")
    )
    return f"Row {row_id}" + (f" - {label}" if label else "")


def decision_row_has_option_editor(row: dict[str, Any], editable_fields: set[str]) -> bool:
    return (
        ("editable_selector_code" in editable_fields and bool(decision_row_selector_options(row)))
        or ("selected_pricing_candidate" in editable_fields and bool(decision_row_pricing_options(row)))
        or (
            bool({"crew_people_selection", "crew_selection", "crew_size", "daily_rate"} & editable_fields)
            and bool(decision_row_crew_options(row))
        )
    )


def _matching_option_index(options: list[dict[str, Any]], current_values: Iterable[Any], fields: Iterable[str]) -> int:
    normalized_values = {text_value(value).lower() for value in current_values if text_value(value)}
    if not normalized_values:
        return 0
    for idx, option in enumerate(options):
        option_values = {text_value(option.get(field)).lower() for field in fields if text_value(option.get(field))}
        if normalized_values & option_values:
            return idx
    return 0


def pricing_option_label(option: dict[str, Any]) -> str:
    label = text_value(option.get("item_name") or option.get("resolved_template_option") or option.get("label"))
    unit_price = text_value(option.get("unit_price"))
    if not unit_price:
        return label
    try:
        return f"{label} - ${float(unit_price):,.2f}"
    except ValueError:
        return f"{label} - {unit_price}"


def render_decision_row_option_editor(
    *,
    section_key: str,
    section_label: str,
    rows: list[dict[str, Any]],
    editable_fields: set[str],
    workbench_key: str,
    scope_key: str,
    historical_filters_key: str,
) -> list[dict[str, Any]]:
    editable_indexes = [
        idx
        for idx, row in enumerate(rows or [])
        if isinstance(row, dict) and decision_row_has_option_editor(row, editable_fields)
    ]
    if not editable_indexes:
        return rows

    selected_idx = st.selectbox(
        f"{section_label} row options",
        options=editable_indexes,
        format_func=lambda idx: decision_row_label(rows[idx], idx),
        key=f"wb_row_option_editor_{section_key}_{workbench_key}_{scope_key}_{historical_filters_key}",
        help="Select one row to edit with row-specific template and pricing options.",
    )
    edited_rows = [dict(row) for row in rows]
    row = dict(edited_rows[selected_idx])
    original_row = dict(row)

    c1, c2 = st.columns(2)
    selector_options = decision_row_selector_options(row)
    if "editable_selector_code" in editable_fields and selector_options:
        selector_index = _matching_option_index(
            selector_options,
            [
                row.get("editable_selector_code"),
                row.get("selector_code"),
                row.get("resolved_template_option"),
            ],
            ["selector_code", "resolved_template_option"],
        )
        with c1:
            selected_selector = st.selectbox(
                "Template Option",
                options=list(range(len(selector_options))),
                index=selector_index,
                format_func=lambda idx: (
                    f"{selector_options[idx].get('selector_code')} - {selector_options[idx].get('resolved_template_option')}"
                    if selector_options[idx].get("selector_code")
                    else str(selector_options[idx].get("resolved_template_option") or "")
                ),
                key=f"wb_row_selector_{section_key}_{selected_idx}_{workbench_key}_{scope_key}_{historical_filters_key}",
            )
        selector_option = selector_options[selected_selector]
        row["editable_selector_code"] = selector_option.get("selector_code") or row.get("editable_selector_code")
        row["selector_code"] = selector_option.get("selector_code") or row.get("selector_code")
        if selector_option.get("resolved_template_option"):
            row["resolved_template_option"] = selector_option.get("resolved_template_option")

    pricing_options = decision_row_pricing_options(row)
    if "selected_pricing_candidate" in editable_fields and pricing_options:
        pricing_index = _matching_option_index(
            pricing_options,
            [row.get("selected_pricing_candidate"), row.get("item_name")],
            ["item_name", "selected_pricing_candidate", "resolved_template_option"],
        )
        with c2:
            selected_pricing = st.selectbox(
                "Pricing Candidate",
                options=list(range(len(pricing_options))),
                index=pricing_index,
                format_func=lambda idx: pricing_option_label(pricing_options[idx]),
                key=f"wb_row_pricing_{section_key}_{selected_idx}_{workbench_key}_{scope_key}_{historical_filters_key}",
            )
        pricing_option = pricing_options[selected_pricing]
        row["selected_pricing_candidate"] = pricing_option.get("item_name") or row.get("selected_pricing_candidate")
        if text_value(pricing_option.get("unit_price")):
            row["unit_price"] = pricing_option.get("unit_price")

    crew_options = decision_row_crew_options(row)
    if {"crew_people_selection", "crew_selection", "crew_size", "daily_rate"} & editable_fields and crew_options:
        crew_index = _matching_option_index(
            crew_options,
            [row.get("crew_people_selection"), row.get("crew_selection"), row.get("selected_daily_rate_cell")],
            ["selector_code", "resolved_template_option", "daily_rate_cell"],
        )
        with c1:
            selected_crew = st.selectbox(
                "Crew / People Rate",
                options=list(range(len(crew_options))),
                index=crew_index,
                format_func=lambda idx: (
                    f"{crew_options[idx].get('selector_code')} - {crew_options[idx].get('resolved_template_option')}"
                    if crew_options[idx].get("selector_code")
                    else str(crew_options[idx].get("resolved_template_option") or "")
                ),
                key=f"wb_row_crew_{section_key}_{selected_idx}_{workbench_key}_{scope_key}_{historical_filters_key}",
            )
        crew_option = crew_options[selected_crew]
        if "crew_people_selection" in editable_fields:
            row["crew_people_selection"] = crew_option.get("selector_code") or row.get("crew_people_selection")
        if "crew_selection" in editable_fields:
            row["crew_selection"] = crew_option.get("selector_code") or row.get("crew_selection")
        if "crew_size" in editable_fields and text_value(crew_option.get("crew_size")):
            row["crew_size"] = crew_option.get("crew_size")
        if text_value(crew_option.get("daily_rate")):
            row["daily_rate"] = crew_option.get("daily_rate")

    override_fields = set(editable_fields) | {
        "selector_code",
        "resolved_template_option",
        "selected_pricing_candidate",
        "crew_selection",
        "crew_people_selection",
        "daily_rate",
        "unit_price",
    }
    if any(_editable_values_differ(original_row.get(field), row.get(field)) for field in override_fields):
        row["manual_override"] = True
        row["proposal_source"] = "estimator_edit"
    edited_rows[selected_idx] = row

    with st.expander("Selected row evidence and guidance", expanded=False):
        evidence = row.get("decision_evidence_summary") or row.get("proposal_evidence_summary") or row.get("notes")
        if evidence:
            st.caption(str(evidence))
        warnings = row.get("compatibility_warnings") or row.get("proposal_review_reasons")
        if warnings:
            st.warning(str(warnings))
        guidance = row.get("product_guidance")
        if guidance:
            st.info(str(guidance))
    return edited_rows


def _editable_values_differ(original_value: Any, edited_value: Any) -> bool:
    def _normalize(value: Any) -> Any:
        if value is None:
            return ""
        try:
            if pd.isna(value):
                return ""
        except (TypeError, ValueError):
            pass
        return value

    original_normalized = _normalize(original_value)
    edited_normalized = _normalize(edited_value)
    try:
        original_number = float(original_normalized)
        edited_number = float(edited_normalized)
        return abs(original_number - edited_number) > 1e-9
    except (TypeError, ValueError):
        return str(original_normalized) != str(edited_normalized)


def merge_editable_rows(
    original_rows: list[dict[str, Any]],
    edited_rows: list[dict[str, Any]],
    editable_fields: set[str],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for idx, original in enumerate(original_rows or []):
        row = dict(original)
        edited = edited_rows[idx] if idx < len(edited_rows) else {}
        for field in editable_fields:
            if field in edited:
                if field == "include" and _editable_values_differ(original.get(field), edited[field]):
                    row["manual_override"] = True
                    row["include_source"] = "estimator_edit"
                if field in {"total_hours", "editable_total_hours"}:
                    if _editable_values_differ(original.get(field), edited[field]):
                        row["manual_labor_hours_override"] = True
                        row["manual_override"] = True
                        row["total_hours_source"] = "estimator_override"
                        row["labor_driver_applied"] = False
                row[field] = edited[field]
        merged.append(row)
    return merged


def merge_dynamic_free_adder_rows(
    original_rows: list[dict[str, Any]],
    edited_rows: list[dict[str, Any]],
    editable_fields: set[str],
) -> list[dict[str, Any]]:
    merged = merge_editable_rows(original_rows, edited_rows, editable_fields)
    for idx, edited in enumerate(edited_rows[len(original_rows or []):], start=len(original_rows or [])):
        if not isinstance(edited, dict):
            continue
        has_label = str(edited.get("template_line") or "").strip()
        has_amount = optional_positive_number(edited.get("amount")) is not None or optional_positive_number(
            edited.get("estimated_cost")
        ) is not None
        if not (has_label or has_amount or bool(edited.get("include"))):
            continue
        workbook_row = str(edited.get("workbook_row") or f"manual-{idx + 1}")
        template_line = str(edited.get("template_line") or "Manual adder").strip()
        row = {
            "include": bool(edited.get("include", True)),
            "section": "roofing_free_adder_template_decisions",
            "decision_id": str(edited.get("decision_id") or f"roofing_free_adder_manual_{idx + 1}"),
            "template_bucket": str(edited.get("template_bucket") or "free_adder"),
            "workbook_row": workbook_row,
            "template_line": template_line,
            "resolved_template_option": template_line,
            "amount": edited.get("amount"),
            "estimated_cost": edited.get("estimated_cost") or edited.get("amount"),
            "markup_treatment": edited.get("markup_treatment") or "post_markup",
            "compatibility_status": "review",
            "compatibility_warnings": ["Manual free adder; verify amount and markup treatment."],
            "notes": edited.get("notes") or "Manual free adder from estimator UI.",
            "manual_override": True,
            "include_source": "estimator_edit",
        }
        for field in editable_fields:
            if field in edited:
                row[field] = edited[field]
        merged.append(row)
    return merged


def render_repair_estimate_result(
    result_payload: dict[str, Any],
    *,
    notes: str,
    customer_job_name: str = "",
    site_address: str = "",
    contact_name: str = "",
    contact_phone: str = "",
    contact_email: str = "",
    estimator: str = "",
) -> None:
    metric_row(
        [
            ("Labor Target", f"{result_payload.get('estimated_labor_hours_target') or 0:,.1f} hrs"),
            ("Material Target", fmt_dollar(result_payload.get("estimated_material_cost_target"))),
            ("Invoice Target", fmt_dollar(result_payload.get("estimated_invoice_target"))),
            ("Confidence", str(result_payload.get("confidence") or "-").title()),
        ]
    )
    st.markdown("**Parsed Repair Scope**")
    repair_scope = result_payload.get("parsed_scope") or {}
    repair_fields = [
        "repair_type",
        "roof_type",
        "issue_type",
        "leak_present",
        "emergency_or_standard",
        "affected_area",
        "affected_area_sqft",
        "affected_linear_feet",
        "penetration_count",
        "access_complexity",
        "materials_mentioned",
        "actions_requested",
        "missing_info",
    ]
    st.dataframe(pd.DataFrame([{field: repair_scope.get(field) for field in repair_fields}]), use_container_width=True, hide_index=True)
    if result_payload.get("review_flags"):
        st.warning("\n".join(result_payload.get("review_flags") or []))

    st.markdown("**Estimate Range**")
    range_df = pd.DataFrame(
        [
            {
                "bucket": "Labor hours",
                "low": result_payload.get("estimated_labor_hours_low"),
                "target": result_payload.get("estimated_labor_hours_target"),
                "high": result_payload.get("estimated_labor_hours_high"),
            },
            {
                "bucket": "Material cost",
                "low": result_payload.get("estimated_material_cost_low"),
                "target": result_payload.get("estimated_material_cost_target"),
                "high": result_payload.get("estimated_material_cost_high"),
            },
            {
                "bucket": "Invoice / price",
                "low": result_payload.get("estimated_invoice_low"),
                "target": result_payload.get("estimated_invoice_target"),
                "high": result_payload.get("estimated_invoice_high"),
            },
        ]
    )
    st.dataframe(range_df, use_container_width=True, hide_index=True)

    st.markdown("**Materials / Repair Packages**")
    show_table(
        dataframe_from_records(result_payload.get("selected_repair_packages") or []),
        ["material_package", "selection_reason", "evidence_count", "median_total_cost", "common_material_names"],
        height=240,
    )

    st.markdown("**Labor / Historical Calibration**")
    st.dataframe(pd.DataFrame([result_payload.get("evidence_summary") or {}]), use_container_width=True, hide_index=True)
    show_table(
        dataframe_from_records(result_payload.get("matched_repair_profiles") or []),
        ["type_of_repair", "roof_type", "evidence_count", "median_labor_hours", "median_invoice_amount"],
        height=220,
    )

    st.markdown("**Similar Historical Repairs**")
    show_table(
        dataframe_from_records(result_payload.get("similar_repairs") or []),
        [
            "repair_id",
            "job_name",
            "customer",
            "status",
            "type_of_repair",
            "roof_type",
            "historical_labor_hours",
            "invoice_amount",
            "gross_profit",
            "url",
            "similarity_score",
            "reason_matched",
        ],
        height=360,
    )

    st.markdown("**Repair Audit Export**")
    if st.button("Export Repair Audit Package", key="export_integrated_repair_estimator_audit"):
        try:
            from jobscan.repair_estimator.estimator import RepairEstimateResult, write_repair_audit_package

            audit_result = RepairEstimateResult(**result_payload)
            stem = re.sub(
                r"[^a-zA-Z0-9]+",
                "_",
                (customer_job_name or repair_scope.get("issue_type") or "repair_estimate"),
            ).strip("_").lower()
            paths = write_repair_audit_package(audit_result, Path("output/repair_estimator/audit"), stem=stem or "repair_estimate")
            st.session_state["integrated_repair_estimate_audit_paths"] = {name: str(path) for name, path in paths.items()}
            st.success("Repair audit package exported.")
        except Exception as exc:
            logger.exception("Repair audit export failed")
            st.error(f"Could not export repair audit package: {safe_exception_text(exc)}")
    audit_paths = st.session_state.get("integrated_repair_estimate_audit_paths") or {}
    audit_json = Path(audit_paths.get("json", "")) if audit_paths.get("json") else None
    audit_xlsx = Path(audit_paths.get("xlsx", "")) if audit_paths.get("xlsx") else None
    if audit_json and audit_json.exists():
        st.download_button("Download Repair Audit JSON", audit_json.read_bytes(), audit_json.name, "application/json", key="download_integrated_repair_audit_json")
    if audit_xlsx and audit_xlsx.exists():
        st.download_button(
            "Download Repair Audit Workbook",
            audit_xlsx.read_bytes(),
            audit_xlsx.name,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="download_integrated_repair_audit_xlsx",
        )

    st.markdown("**Filled Repair Template**")
    if st.button("Export Filled Repair Template", key="export_integrated_repair_template"):
        try:
            from jobscan.repair_estimator.workbook_writer import generate_repair_estimate_workbook

            stem = re.sub(
                r"[^a-zA-Z0-9]+",
                "_",
                (customer_job_name or repair_scope.get("issue_type") or "repair_estimate"),
            ).strip("_").lower()
            output_path = generate_repair_estimate_workbook(
                result_payload,
                job_name=customer_job_name,
                site_address=site_address,
                contact_name=contact_name,
                contact_phone=contact_phone,
                contact_email=contact_email,
                estimator=estimator,
                output_filename=f"{stem or 'repair_estimate'}_filled.xlsx",
            )
            st.session_state["integrated_repair_filled_template_path"] = str(output_path)
            st.success("Filled repair template exported.")
        except Exception as exc:
            logger.exception("Filled repair template export failed")
            st.error(f"Could not export filled repair template: {safe_exception_text(exc)}")
    filled_template_text = st.session_state.get("integrated_repair_filled_template_path")
    filled_template = Path(filled_template_text) if filled_template_text else None
    if filled_template and filled_template.exists():
        st.download_button(
            "Download Filled Repair Template",
            filled_template.read_bytes(),
            filled_template.name,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="download_integrated_repair_template",
        )


def render_flooring_estimate_result(
    result_payload: dict[str, Any],
    *,
    notes: str,
    customer_job_name: str = "",
    site_address: str = "",
    city_state_zip: str = "",
    contact_name: str = "",
    contact_phone: str = "",
    contact_email: str = "",
    estimator: str = "",
) -> None:
    flooring_scope = result_payload.get("parsed_scope") or {}
    decisions = result_payload.get("workbook_decisions") or []
    metric_row(
        [
            ("Area", f"{flooring_scope.get('area_sqft') or 0:,.0f} sq ft"),
            ("System", str(flooring_scope.get("system") or "-").replace("_", " ").title()),
            ("Decisions", f"{len(decisions):,}"),
            ("Confidence", str(result_payload.get("confidence") or "-").title()),
        ]
    )
    st.markdown("**Parsed Flooring Scope**")
    scope_fields = [
        "job_type",
        "area_sqft",
        "system",
        "substrate",
        "prep_required",
        "flake_broadcast",
        "primer_required",
        "generator_required",
        "access_complexity",
    ]
    st.dataframe(
        pd.DataFrame([{field: flooring_scope.get(field) for field in scope_fields}]),
        use_container_width=True,
        hide_index=True,
    )
    if result_payload.get("review_flags"):
        st.warning("\n".join(result_payload.get("review_flags") or []))

    st.markdown("**Workbook Decisions**")
    show_table(
        dataframe_from_records(decisions),
        [
            "decision_id",
            "row_type",
            "template_bucket",
            "workbook_row",
            "item",
            "area_sqft",
            "gal_per_100_sqft",
            "unit_price",
            "days",
            "crew_size",
            "estimated_cost",
            "include_source",
            "historical_evidence_summary",
        ],
        height=300,
    )

    st.markdown("**Filled Flooring Template**")
    if st.button("Export Filled Flooring Template", key="export_integrated_flooring_template"):
        try:
            from jobscan.flooring_estimator.workbook_writer import generate_flooring_estimate_workbook

            stem = re.sub(
                r"[^a-zA-Z0-9]+",
                "_",
                (customer_job_name or flooring_scope.get("system") or "flooring_estimate"),
            ).strip("_").lower()
            output_path = generate_flooring_estimate_workbook(
                result_payload,
                job_name=customer_job_name,
                site_address=site_address,
                city_state_zip=city_state_zip,
                contact_name=contact_name,
                contact_phone=contact_phone,
                contact_email=contact_email,
                estimator=estimator,
                output_filename=f"{stem or 'flooring_estimate'}_filled.xlsx",
            )
            st.session_state["integrated_flooring_filled_template_path"] = str(output_path)
            st.success("Filled flooring template exported.")
        except Exception as exc:
            logger.exception("Filled flooring template export failed")
            st.error(f"Could not export filled flooring template: {safe_exception_text(exc)}")
    filled_template_text = st.session_state.get("integrated_flooring_filled_template_path")
    filled_template = Path(filled_template_text) if filled_template_text else None
    if filled_template and filled_template.exists():
        st.download_button(
            "Download Filled Flooring Template",
            filled_template.read_bytes(),
            filled_template.name,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="download_integrated_flooring_template",
        )


def estimator_prototype_page() -> None:
    reset_estimator_perf_timings()
    st.title("Estimating Assistant")
    st.caption("Describe the job, review what was parsed, then build the workbook draft. Estimator review is required before quoting.")

    data = load_estimator_data_for_ui("interactive")
    with st.expander("Examples, routing, and data status", expanded=False):
        estimate_type_selection = st.selectbox(
            "Estimate Type",
            ESTIMATE_TYPE_OPTIONS,
            index=0,
            help="Auto-detect uses deterministic keywords to route notes to repair, restoration/coating, insulation, or flooring estimating.",
            key="estimator_estimate_type",
        )
        sample_cols = st.columns(len(ESTIMATOR_SAMPLE_NOTES))
        for column, (label, sample) in zip(sample_cols, ESTIMATOR_SAMPLE_NOTES.items()):
            if column.button(label, key=f"estimator_sample_{label}"):
                st.session_state["estimator_notes"] = sample
                st.session_state["estimator_chat_pending_message"] = sample
        st.write("Files used:", getattr(data, "source_files_used", []) or [])
        data_warnings = getattr(data, "warnings", []) or []
        if data_warnings:
            st.warning("\n".join(str(warning) for warning in data_warnings))
        st.write(
            {
                "load_profile": "interactive",
                "jobs": estimator_data_table_count(data, "jobs"),
                "estimates": estimator_data_table_count(data, "estimates"),
                "line_items": estimator_data_table_count(data, "line_items"),
                "template_rows": estimator_data_table_count(data, "template_rows"),
                "template_row_catalog": estimator_data_table_count(data, "template_row_catalog"),
                "template_formula_models": estimator_data_table_count(data, "template_formula_models"),
                "classified_line_items": estimator_data_table_count(data, "classified_line_items"),
                "tracking_summary": estimator_data_table_count(data, "tracking_summary"),
                "tracking_daily": estimator_data_table_count(data, "tracking_daily"),
                "pricing": estimator_data_table_count(data, "pricing"),
                "pricing_catalog": estimator_data_table_count(data, "pricing_catalog"),
                "relationship_labor_rates": estimator_data_table_count(data, "relationship_labor_rates"),
                "relationship_material_qty_ratios": estimator_data_table_count(data, "relationship_material_qty_ratios"),
                "relationship_package_cooccurrence": estimator_data_table_count(data, "relationship_package_cooccurrence"),
                "job_context_profiles": estimator_data_table_count(data, "job_context_profiles"),
                "template_examples": estimator_data_table_count(data, "template_examples"),
                "foam_yield_history": estimator_data_table_count(data, "foam_yield_history"),
                "estimator_decision_recommendations": estimator_data_table_count(data, "estimator_decision_recommendations"),
                "estimator_memory": estimator_data_table_count(data, "estimator_memory"),
            }
        )
    with st.expander("Estimator Memory Review", expanded=False):
        st.caption(
            "Approve answer-key or edit-derived memory here. Approved rows are loaded back into the chat context; "
            "pending rows are not used for future recommendations."
        )
        render_estimator_memory_admin()

    notes = str(st.session_state.get("estimator_notes") or "")
    resolved_estimate_type = resolve_estimate_type(estimate_type_selection, notes)
    if estimate_type_selection == ESTIMATE_TYPE_AUTO:
        st.caption(f"Auto-detected estimate type: {resolved_estimate_type}")
    else:
        st.caption(f"Selected estimate type: {resolved_estimate_type}")
    active_chat_context = render_estimator_chat_draft_panel(
        notes=notes,
        estimate_type=resolved_estimate_type,
        data=data,
    )
    if active_chat_context and isinstance(active_chat_context.get("scope_overrides"), dict):
        active_chat_context["scope_overrides"] = scope_with_reference_template_type(
            active_chat_context.get("scope_overrides") or {},
            active_chat_context.get("reference_answer_key") if isinstance(active_chat_context.get("reference_answer_key"), dict) else None,
            active_chat_context.get("workbook_decision_preferences") if isinstance(active_chat_context.get("workbook_decision_preferences"), list) else [],
        )
    chat_template_type = ""
    if active_chat_context and isinstance(active_chat_context.get("scope_overrides"), dict):
        chat_template_type = text_value(active_chat_context.get("scope_overrides", {}).get("template_type")).lower()
    if chat_template_type in {"roofing", "insulation", "repair", "flooring"}:
        chat_resolved_estimate_type = _estimator_type_for_template(chat_template_type)
        if chat_resolved_estimate_type != resolved_estimate_type:
            st.caption(f"Using chat-selected estimate type: {chat_resolved_estimate_type}")
            resolved_estimate_type = chat_resolved_estimate_type
    latest_notes = str(st.session_state.get("estimator_notes") or notes)
    chat_augmented_notes = (
        str(active_chat_context.get("estimator_notes") or latest_notes)
        if active_chat_context
        else latest_notes
    )
    estimator_chat_scope_overrides = (
        active_chat_context.get("scope_overrides")
        if active_chat_context and isinstance(active_chat_context.get("scope_overrides"), dict)
        else {}
    )
    estimator_chat_scope_overrides = scope_with_decision_basis_area(
        estimator_chat_scope_overrides,
        active_chat_context.get("workbook_decision_preferences") if active_chat_context else [],
    )
    estimator_chat_scope_overrides = scope_with_reference_template_type(
        estimator_chat_scope_overrides,
        active_chat_context.get("reference_answer_key") if active_chat_context and isinstance(active_chat_context.get("reference_answer_key"), dict) else None,
        active_chat_context.get("workbook_decision_preferences") if active_chat_context else [],
    )
    active_photo_context = (
        active_chat_context.get("photo_context")
        if active_chat_context and isinstance(active_chat_context.get("photo_context"), dict)
        else st.session_state.get("estimator_photo_context")
        if isinstance(st.session_state.get("estimator_photo_context"), dict)
        else None
    )

    project_type = ""
    division = ""
    substrate = ""
    coating_type = ""
    roof_condition = ""
    access_complexity = ""
    location = ""
    repair_roof_type_override = ""
    repair_urgency_override = ""
    overrides: dict[str, Any] = {}

    field_estimator_fn, field_estimator_import_warning = optional_field_notes_estimator()
    if field_estimator_import_warning and resolved_estimate_type not in {ESTIMATE_TYPE_REPAIR, ESTIMATE_TYPE_FLOORING}:
        st.warning(field_estimator_import_warning)
    if resolved_estimate_type in {ESTIMATE_TYPE_REPAIR, ESTIMATE_TYPE_FLOORING}:
        use_historical_calibration = False
    else:
        use_historical_calibration = False
    field_notes_data = data
    with st.expander("Job header and advanced options", expanded=False):
        f1, f2 = st.columns(2)
        with f1:
            field_job_name = st.text_input("Job name", key="field_estimator_job_name")
            field_site_address = st.text_input("Address", key="field_estimator_site_address")
        with f2:
            field_city = st.text_input("City", value="", key="field_estimator_city")
            field_state = st.text_input("State", value="", key="field_estimator_state")
        if resolved_estimate_type in {ESTIMATE_TYPE_REPAIR, ESTIMATE_TYPE_FLOORING}:
            if resolved_estimate_type == ESTIMATE_TYPE_REPAIR:
                st.caption("Repair mode uses VSimple repair history tables and does not run the sqft-based roof coating estimator.")
            else:
                st.caption("Flooring mode fills the flooring estimate template and does not run the sqft-based roof coating estimator.")
        else:
            use_historical_calibration = st.checkbox(
                "Debug: run full historical calibration inside parser",
                value=False,
                help=(
                    "Default workbench mode uses the compact estimator evidence already loaded for chat and workbook defaults. "
                    "Enable this only when debugging the older automatic calibration path against the full estimate history."
                ),
                key="use_historical_calibration",
            )
            if use_historical_calibration:
                field_notes_data = load_estimator_data_for_ui("full")
            else:
                field_notes_data = data
    estimator_input_notes = chat_augmented_notes
    build_requested = st.button("Build / Rebuild Filled Estimate Template", key="generate_field_estimate_recommendation")
    auto_build_requested = bool(st.session_state.pop("estimator_auto_build_requested", False))
    if auto_build_requested:
        st.info("Learning mode requested a workbook rebuild automatically.")
    if build_requested or auto_build_requested:
        try:
            photo_file_ids = (active_photo_context or {}).get("selected_image_ids") or []
            session_id = capture_estimator_session_event(
                estimator_sessions.create_estimator_session,
                raw_input_notes=estimator_input_notes,
                division=(
                    "Repair"
                    if resolved_estimate_type == ESTIMATE_TYPE_REPAIR
                    else "Flooring"
                    if resolved_estimate_type == ESTIMATE_TYPE_FLOORING
                    else ""
                ),
                template_type=(
                    "repair"
                    if resolved_estimate_type == ESTIMATE_TYPE_REPAIR
                    else "flooring"
                    if resolved_estimate_type == ESTIMATE_TYPE_FLOORING
                    else ""
                ),
                job_name=field_job_name,
                site_address=field_site_address,
                input_source_type="manual",
                photos_present=bool(active_photo_context),
                source_file_ids=[*(data.source_files_used or []), *photo_file_ids],
                estimate_status="PARSING",
            )
            if session_id:
                st.session_state["estimator_session_id"] = session_id
                reference_memory_saved_key = f"estimator_reference_memory_saved_{session_id}"
                if (
                    active_chat_context
                    and estimator_reference_memory_capture_enabled(active_chat_context)
                    and not st.session_state.get(reference_memory_saved_key)
                ):
                    capture_reference_template_memory_candidates(
                        session_id,
                        active_chat_context,
                        template_type=str(
                            estimator_chat_scope_overrides.get("template_type")
                            or resolved_estimate_type
                            or ""
                        ),
                    )
                    st.session_state[reference_memory_saved_key] = True
            else:
                st.session_state.pop("estimator_session_id", None)
            if resolved_estimate_type == ESTIMATE_TYPE_REPAIR:
                route, repair_result = route_estimator_request(
                    estimator_input_notes,
                    resolved_estimate_type,
                    overrides={
                        "roof_type": repair_roof_type_override,
                        "urgency": repair_urgency_override,
                        "customer_job_name": field_job_name,
                        "photos_link": "",
                    },
                )
                st.session_state["field_estimate_route"] = route
                st.session_state["integrated_repair_estimate_result"] = repair_result.to_dict()
                st.session_state["field_estimate_recommendation"] = None
                st.session_state["field_estimate_recommendation_notes"] = estimator_input_notes
                st.session_state.pop("integrated_repair_estimate_audit_paths", None)
                st.session_state.pop("integrated_repair_filled_template_path", None)
                st.session_state.pop("integrated_flooring_estimate_result", None)
                st.session_state.pop("integrated_flooring_filled_template_path", None)
                if session_id:
                    repair_payload = repair_result.to_dict()
                    capture_estimator_session_event(
                        estimator_sessions.update_estimator_session,
                        session_id,
                        division="Repair",
                        template_type="repair",
                        estimate_status="READY_TO_ESTIMATE",
                    )
                    capture_estimator_session_event(
                        estimator_sessions.save_scope_interpretation,
                        session_id,
                        parsed_scope=repair_payload.get("parsed_scope") or repair_payload.get("scope") or repair_payload,
                        deterministic_scope=repair_payload.get("parsed_scope") or {},
                        assumptions=repair_payload.get("assumptions") or {},
                        missing_questions=repair_payload.get("missing_info") or repair_payload.get("missing_questions") or [],
                        confidence_by_field=repair_payload.get("confidence_by_field") or {},
                        review_flags=repair_payload.get("review_flags") or [],
                    )
            elif resolved_estimate_type == ESTIMATE_TYPE_FLOORING:
                city_state_zip = ", ".join(part for part in [field_city, field_state] if part)
                route, flooring_result = route_estimator_request(
                    estimator_input_notes,
                    resolved_estimate_type,
                    overrides={
                        "customer_job_name": field_job_name,
                        "site_address": field_site_address,
                        "city_state_zip": city_state_zip,
                    },
                    field_notes_data=data,
                )
                st.session_state["field_estimate_route"] = route
                st.session_state["integrated_flooring_estimate_result"] = flooring_result.to_dict()
                st.session_state["field_estimate_recommendation"] = None
                st.session_state["field_estimate_recommendation_notes"] = estimator_input_notes
                st.session_state.pop("integrated_flooring_filled_template_path", None)
                st.session_state.pop("integrated_repair_estimate_result", None)
                st.session_state.pop("integrated_repair_estimate_audit_paths", None)
                st.session_state.pop("integrated_repair_filled_template_path", None)
                if session_id:
                    flooring_payload = flooring_result.to_dict()
                    capture_estimator_session_event(
                        estimator_sessions.update_estimator_session,
                        session_id,
                        division="Flooring",
                        template_type="flooring",
                        job_name=field_job_name,
                        site_address=field_site_address,
                        estimate_status="READY_TO_ESTIMATE",
                    )
                    capture_estimator_session_event(
                        estimator_sessions.save_scope_interpretation,
                        session_id,
                        parsed_scope=flooring_payload.get("parsed_scope") or flooring_payload,
                        deterministic_scope=flooring_payload.get("parsed_scope") or {},
                        assumptions={},
                        missing_questions=flooring_payload.get("missing_info") or [],
                        confidence_by_field={},
                        review_flags=flooring_payload.get("review_flags") or [],
                    )
            elif field_estimator_fn is None:
                st.warning("Field notes estimator is not available in this deployment yet.")
            else:
                with estimator_perf_step("field notes parser"):
                    recommendation = field_estimator_fn(
                        estimator_input_notes,
                        {
                            "job_name": field_job_name,
                            "site_address": field_site_address,
                            "city": field_city,
                            "state": field_state,
                            "estimated_sqft": estimator_chat_scope_overrides.get("estimated_sqft"),
                            "surface_area_sqft": estimator_chat_scope_overrides.get("surface_area_sqft"),
                            "disable_ai_scope_interpreter": True,
                        },
                        data=field_notes_data,
                    )
                if estimator_chat_scope_overrides:
                    recommendation.parsed_fields = {
                        **(recommendation.parsed_fields or {}),
                        **estimator_chat_scope_overrides,
                        "estimator_chat": {
                            "source": active_chat_context.get("source") if active_chat_context else "",
                            "confidence": active_chat_context.get("confidence") if active_chat_context else None,
                            "assistant_message": active_chat_context.get("assistant_message") if active_chat_context else "",
                            "missing_questions": active_chat_context.get("missing_questions") if active_chat_context else [],
                            "workbook_decision_preferences": active_chat_context.get("workbook_decision_preferences") if active_chat_context else [],
                        },
                    }
                    recommendation.review_flags = list(
                        dict.fromkeys(
                            [
                                *(getattr(recommendation, "review_flags", None) or []),
                                "Estimator chat draft supplied scope overrides; estimator must verify before quoting.",
                                *(
                                    [
                                        f"Estimator chat warning: {warning}"
                                        for warning in (active_chat_context.get("warnings") or [])
                                    ]
                                    if active_chat_context
                                    else []
                                ),
                            ]
                        )
                    )
                    recommendation = clear_conflicting_readiness_after_chat_override(
                        recommendation,
                        str(recommendation.parsed_fields.get("template_type") or ""),
                    )
                st.session_state["field_estimate_recommendation"] = recommendation
                st.session_state["field_estimate_route"] = resolved_estimate_type
                st.session_state.pop("integrated_repair_estimate_result", None)
                st.session_state.pop("integrated_flooring_estimate_result", None)
                st.session_state["field_estimate_recommendation_notes"] = estimator_input_notes
                st.session_state.pop("field_estimator_evidence_export_paths", None)
                if session_id:
                    parsed_scope = recommendation.parsed_fields or {}
                    ai_debug = (getattr(recommendation, "debug", {}) or {}).get("ai_scope_interpreter") or {}
                    ai_model = (
                        parsed_scope.get("ai_model")
                        or ai_debug.get("ai_model")
                        or os.getenv("OPENAI_MODEL")
                        or ""
                    )
                    capture_estimator_session_event(
                        estimator_sessions.update_estimator_session,
                        session_id,
                        division=parsed_scope.get("division") or ("Insulation" if parsed_scope.get("template_type") == "insulation" else "Roofing"),
                        template_type=parsed_scope.get("template_type") or ("insulation" if parsed_scope.get("division") == "Insulation" else "roofing"),
                        customer=parsed_scope.get("customer_name") or parsed_scope.get("customer"),
                        job_name=field_job_name or parsed_scope.get("job_name"),
                        site_address=field_site_address or parsed_scope.get("site_address") or parsed_scope.get("address"),
                        ai_model=ai_model,
                        estimate_status=getattr(recommendation, "estimate_status", None) or parsed_scope.get("estimate_status"),
                    )
                    capture_estimator_session_event(
                        estimator_sessions.save_scope_interpretation,
                        session_id,
                        parsed_scope=parsed_scope,
                        deterministic_scope=ai_debug.get("deterministic_scope")
                        or ai_debug.get("deterministic_parsed_scope")
                        or parsed_scope,
                        assumptions=parsed_scope.get("assumptions") or ai_debug.get("assumptions") or {},
                        missing_questions=(
                            getattr(recommendation, "required_questions", None)
                            or parsed_scope.get("missing_questions")
                            or parsed_scope.get("required_questions")
                            or []
                        ),
                        confidence_by_field=parsed_scope.get("confidence_by_field")
                        or (ai_debug.get("ai_parsed_scope") or {}).get("confidence_by_field")
                        or {},
                        review_flags=getattr(recommendation, "review_flags", None) or parsed_scope.get("review_flags") or [],
                    )
        except Exception as err:
            logger.exception("Estimator mode failed")
            st.error("Estimator failed for this input.")
            st.warning(f"{type(err).__name__}: {safe_exception_text(err)}")
            st.session_state["field_estimate_recommendation"] = None
            st.session_state.pop("integrated_repair_estimate_result", None)
            st.session_state.pop("integrated_flooring_estimate_result", None)
    if st.session_state.get("field_estimate_route") == ESTIMATE_TYPE_REPAIR:
        repair_payload = st.session_state.get("integrated_repair_estimate_result")
        recommendation_notes = st.session_state.get("field_estimate_recommendation_notes") or estimator_input_notes
        if repair_payload:
            if recommendation_notes != estimator_input_notes:
                st.warning(
                    "The displayed repair estimate was generated from earlier notes. "
                    "Click Build Filled Estimate Template again to refresh it for the current text."
                )
            render_repair_estimate_result(
                repair_payload,
                notes=recommendation_notes,
                customer_job_name=field_job_name,
                site_address=field_site_address,
            )
            return
    if st.session_state.get("field_estimate_route") == ESTIMATE_TYPE_FLOORING:
        flooring_payload = st.session_state.get("integrated_flooring_estimate_result")
        recommendation_notes = st.session_state.get("field_estimate_recommendation_notes") or estimator_input_notes
        if flooring_payload:
            if recommendation_notes != estimator_input_notes:
                st.warning(
                    "The displayed flooring estimate was generated from earlier notes. "
                    "Click Build Filled Estimate Template again to refresh it for the current text."
                )
            city_state_zip = ", ".join(part for part in [field_city, field_state] if part)
            render_flooring_estimate_result(
                flooring_payload,
                notes=recommendation_notes,
                customer_job_name=field_job_name,
                site_address=field_site_address,
                city_state_zip=city_state_zip,
            )
            return
    field_recommendation = st.session_state.get("field_estimate_recommendation")
    if field_recommendation:
        recommendation_notes = st.session_state.get("field_estimate_recommendation_notes") or estimator_input_notes
        if recommendation_notes != estimator_input_notes:
            st.warning(
                "The displayed estimate was generated from earlier notes. "
                "Click Build Filled Estimate Template again to refresh it for the current text."
            )
        estimate_status = getattr(field_recommendation, "estimate_status", None) or field_recommendation.parsed_fields.get("estimate_status") or "READY_TO_ESTIMATE"
        parsed_fields = field_recommendation.parsed_fields
        if estimate_status != "READY_TO_ESTIMATE":
            st.warning(getattr(field_recommendation, "estimate_reason", "") or field_recommendation.parsed_fields.get("estimate_reason") or "More information is required before estimating.")
            questions = getattr(field_recommendation, "required_questions", None) or field_recommendation.parsed_fields.get("required_questions") or []
            actions = getattr(field_recommendation, "recommended_next_actions", None) or field_recommendation.parsed_fields.get("recommended_next_actions") or []
            q_col, a_col = st.columns(2)
            with q_col:
                st.markdown("**Required Questions**")
                show_table(dataframe_from_records([{"question": item} for item in questions]), ["question"], height=180)
            with a_col:
                st.markdown("**Recommended Next Actions**")
                show_table(dataframe_from_records([{"action": item} for item in actions]), ["action"], height=180)
        with st.expander("Parser diagnostics", expanded=False):
            metric_row(
                [
                    ("Readiness", str(estimate_status).replace("_", " ").title()),
                    ("Review Required", "Yes" if field_recommendation.human_review_required else "No"),
                ]
            )
            summary_cols = [
                "project_type",
                "estimate_mode",
                "substrate",
                "coating_type",
                "warranty_target_years",
                "estimated_sqft",
                "roof_condition",
                "access_complexity",
                "penetrations_complexity",
            ]
            summary_row = {column: parsed_fields.get(column) for column in summary_cols if column in parsed_fields}
            if summary_row:
                st.caption("Parsed scope")
                show_table(dataframe_from_records([summary_row]), list(summary_row.keys()), height=90)
            if field_recommendation.review_flags:
                st.caption("Review flags")
                show_table(
                    dataframe_from_records([{"flag": flag} for flag in field_recommendation.review_flags]),
                    ["flag"],
                    height=180,
                )
            ai_debug = (getattr(field_recommendation, "debug", {}) or {}).get("ai_scope_interpreter") or {}
            evidence = parsed_fields.get("evidence_by_field") or (ai_debug.get("ai_parsed_scope") or {}).get("evidence_by_field") or {}
            confidence = parsed_fields.get("confidence_by_field") or (ai_debug.get("ai_parsed_scope") or {}).get("confidence_by_field") or {}
            contradictions = parsed_fields.get("contradictions") or (ai_debug.get("ai_parsed_scope") or {}).get("contradictions") or []
            missing_questions = parsed_fields.get("missing_questions") or parsed_fields.get("required_questions") or []
            c1, c2 = st.columns(2)
            with c1:
                st.caption("Field Evidence")
                evidence_rows = [
                    {"field": field, "evidence": "; ".join(str(item) for item in (items if isinstance(items, list) else [items]))}
                    for field, items in (evidence or {}).items()
                ]
                show_table(dataframe_from_records(evidence_rows), ["field", "evidence"], height=220)
            with c2:
                st.caption("Confidence / Uncertainty")
                confidence_rows = [{"field": field, "confidence": value} for field, value in (confidence or {}).items()]
                show_table(dataframe_from_records(confidence_rows), ["field", "confidence"], height=160)
                if contradictions:
                    st.warning("\n".join(str(item) for item in contradictions))
                if missing_questions:
                    st.info("Missing questions: " + "; ".join(str(item) for item in missing_questions))
            with st.expander("Raw parser details", expanded=False):
                st.dataframe(pd.DataFrame([parsed_fields]), use_container_width=True, hide_index=True)
        if estimate_status != "READY_TO_ESTIMATE":
            surface_review_rows = build_surface_area_review_rows(parsed_fields)
            if surface_review_rows:
                st.markdown("**Surface Areas / Dimensions**")
                show_table(
                    dataframe_from_records(surface_review_rows),
                    SURFACE_AREA_REVIEW_COLUMNS,
                    height=220,
                )
            st.info("Estimate generation stopped before material selection, labor calibration, similar jobs, pricing, workbook export, and evidence export.")
            return
        parsed_workbench = build_estimating_workbench_for_ui(
            field_recommendation,
            data,
            timing_label="initial workbench build",
        )
        workbench_key = str(parsed_workbench.get("estimate_id") or "current")
        debug_mode = st.checkbox(
            "Debug Mode",
            value=False,
            help="Shows legacy calibration, similar-job evidence, and evidence export tools. Normal workbench mode stays focused on editable defaults.",
            key=f"estimator_debug_mode_{workbench_key}",
        )

        st.markdown("### Project Inputs")
        st.caption("Review the job facts that drive historical defaults and workbook rows.")
        base_scope = parsed_workbench.get("scope") or {}
        s1, s2, s3 = st.columns(3)
        with s1:
            edited_project_type = st.text_input("Project Type", value=str(base_scope.get("project_type") or ""), key=f"wb_project_type_{workbench_key}")
            edited_substrate = st.text_input("Roof Type / Substrate", value=str(base_scope.get("roof_type_substrate") or ""), key=f"wb_substrate_{workbench_key}")
            edited_gross = st.number_input("Gross Sq Ft", min_value=0.0, value=float(base_scope.get("gross_sqft") or 0), step=100.0, key=f"wb_gross_{workbench_key}")
        with s2:
            edited_deduction = st.number_input("Deduction Sq Ft", min_value=0.0, value=float(base_scope.get("deduction_sqft") or 0), step=25.0, key=f"wb_deduction_{workbench_key}")
            default_net = float(base_scope.get("net_sqft") or max(edited_gross - edited_deduction, 0))
            edited_net = st.number_input("Net Sq Ft", min_value=0.0, value=default_net, step=100.0, key=f"wb_net_{workbench_key}")
            edited_warranty = st.number_input("Warranty", min_value=0.0, value=float(base_scope.get("warranty_years") or 0), step=5.0, key=f"wb_warranty_{workbench_key}")
        with s3:
            edited_coating = st.text_input("Coating Type", value=str(base_scope.get("coating_type") or ""), key=f"wb_coating_{workbench_key}")
            edited_condition = st.text_input("Roof Condition", value=str(base_scope.get("roof_condition") or ""), key=f"wb_condition_{workbench_key}")
            edited_access = st.text_input("Access", value=str(base_scope.get("access_complexity") or ""), key=f"wb_access_{workbench_key}")
            edited_penetrations = st.text_input("Penetrations", value=str(base_scope.get("penetrations_complexity") or ""), key=f"wb_penetrations_{workbench_key}")
        reference_defaults = parse_reference_job_ids(base_scope.get("reference_job_ids") or base_scope.get("reference_project_ids") or "")
        reference_options, reference_labels = estimator_reference_job_options(
            data,
            template_type=str(base_scope.get("template_type") or historical_filters_from_scope(base_scope).get("template_type") or ""),
        )
        selected_reference_defaults = [job_id for job_id in reference_defaults if job_id in reference_options]
        manual_reference_defaults = [job_id for job_id in reference_defaults if job_id not in reference_options]
        selected_reference_job_ids = st.multiselect(
            "Reference Jobs",
            options=reference_options,
            default=selected_reference_defaults,
            format_func=lambda job_id: reference_labels.get(str(job_id), str(job_id)),
            key=f"wb_reference_jobs_select_{workbench_key}",
            help="Historical jobs selected here act as comparison anchors for estimator decisions.",
        )
        manual_reference_job_ids = st.text_input(
            "Other Reference Job IDs",
            value=", ".join(manual_reference_defaults),
            key=f"wb_reference_jobs_manual_{workbench_key}",
            help="Optional comma-separated job IDs not shown in the list.",
        )
        edited_reference_job_ids = [*selected_reference_job_ids, *parse_reference_job_ids(manual_reference_job_ids)]

        edited_scope = {
            **base_scope,
            "project_type": edited_project_type,
            "roof_type_substrate": edited_substrate,
            "gross_sqft": edited_gross,
            "deduction_sqft": edited_deduction,
            "net_sqft": edited_net,
            "warranty_years": edited_warranty,
            "coating_type": edited_coating,
            "roof_condition": edited_condition,
            "access_complexity": edited_access,
            "penetrations_complexity": edited_penetrations,
            "reference_job_ids": edited_reference_job_ids,
        }
        scope_key = hashlib.sha1(json.dumps(edited_scope, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:8]

        default_filters = historical_filters_from_scope(edited_scope)
        st.markdown("### Stage 2 - Historical Defaults")
        st.caption("Spray-Tec history fills in the template defaults. The parser chooses an initial comparison pool; the estimator can tighten or broaden it.")
        with st.expander("Recommended historical filters / comparison pool", expanded=False):
            st.caption("These filters only recalculate historical defaults. They do not change the parsed scope or hide estimator-editable rows.")
            f1, f2, f3 = st.columns(3)
            with f1:
                filter_division = st.text_input("Division", value=str(default_filters.get("division") or "Roofing"), key=f"wb_filter_division_{workbench_key}")
                filter_template_type = st.text_input("Template Type", value=str(default_filters.get("template_type") or "roofing"), key=f"wb_filter_template_{workbench_key}")
                filter_project_type = st.text_input("Project Type", value=str(default_filters.get("project_type") or ""), key=f"wb_filter_project_{workbench_key}")
                filter_substrate = st.text_input("Substrate", value=str(default_filters.get("substrate") or ""), key=f"wb_filter_substrate_{workbench_key}")
            with f2:
                filter_coating_type = st.text_input("Coating Type", value=str(default_filters.get("coating_type") or ""), key=f"wb_filter_coating_{workbench_key}")
                filter_warranty = st.text_input(
                    "Warranty Years",
                    value=str(int(default_filters["warranty_years"])) if default_filters.get("warranty_years") else "",
                    key=f"wb_filter_warranty_{workbench_key}",
                )
                filter_condition = st.text_input("Roof Condition", value=str(default_filters.get("roof_condition") or ""), key=f"wb_filter_condition_{workbench_key}")
                filter_access = st.text_input("Access Complexity", value=str(default_filters.get("access_complexity") or ""), key=f"wb_filter_access_{workbench_key}")
            with f3:
                filter_penetrations = st.text_input("Penetration Complexity", value=str(default_filters.get("penetrations_complexity") or ""), key=f"wb_filter_penetrations_{workbench_key}")
                filter_area_bucket = st.selectbox(
                    "Area Bucket / Size Range",
                    ["", "under_5k", "5k_15k", "15k_50k", "50k_plus"],
                    index=["", "under_5k", "5k_15k", "15k_50k", "50k_plus"].index(str(default_filters.get("area_bucket") or "")),
                    key=f"wb_filter_area_bucket_{workbench_key}",
                )
                filter_source_year = st.text_input("Source Year", value=str(default_filters.get("source_year") or ""), key=f"wb_filter_source_year_{workbench_key}")
                filter_pipeline_status = st.text_input("Pipeline Status", value="", key=f"wb_filter_pipeline_status_{workbench_key}")
            f4, f5, f6 = st.columns(3)
            with f4:
                filter_completed_only = st.checkbox("Completed Only", value=False, key=f"wb_filter_completed_only_{workbench_key}")
            with f5:
                filter_include_repairs = st.checkbox("Include Repairs", value=True, key=f"wb_filter_include_repairs_{workbench_key}")
            with f6:
                filter_min_evidence = st.number_input("Minimum Evidence Count", min_value=0, value=3, step=1, key=f"wb_filter_min_evidence_{workbench_key}")

        historical_filters = {
            "division": filter_division,
            "template_type": filter_template_type,
            "project_type": filter_project_type,
            "substrate": filter_substrate,
            "coating_type": filter_coating_type,
            "warranty_years": optional_positive_number(filter_warranty),
            "roof_condition": filter_condition,
            "access_complexity": filter_access,
            "penetrations_complexity": filter_penetrations,
            "area_bucket": filter_area_bucket,
            "source_year": optional_positive_number(filter_source_year),
            "pipeline_status": filter_pipeline_status,
            "completed_only": filter_completed_only,
            "include_repairs": filter_include_repairs,
            "min_evidence_count": filter_min_evidence,
        }
        historical_filters_key = historical_filter_hash(historical_filters)
        reset_filtered_defaults = st.button(
            "Reset all unedited rows to filtered historical defaults",
            key=f"wb_reset_filtered_defaults_{workbench_key}_{historical_filters_key}",
        )
        filtered_default_workbench = build_estimating_workbench_for_ui(
            field_recommendation,
            data,
            scope_override=edited_scope,
            historical_filters=historical_filters,
            timing_label="filtered workbench build",
        )
        previous_workbench_key = f"wb_last_edited_{workbench_key}"
        previous_workbench = None if reset_filtered_defaults else st.session_state.get(previous_workbench_key)
        original_workbench = apply_historical_filter_update(previous_workbench, filtered_default_workbench)
        edited_workbench = dict(original_workbench)
        edited_workbench["scope"] = edited_scope
        feedback_baseline = dict(filtered_default_workbench)
        feedback_baseline["scope"] = base_scope

        session_id = current_estimator_session_id()
        if session_id:
            proposal_saved_key = f"estimator_session_proposal_saved_{session_id}_{historical_filters_key}"
            if not st.session_state.get(proposal_saved_key):
                proposal_id = capture_estimator_session_event(
                    estimator_sessions.save_decision_proposal,
                    session_id,
                    proposed_decisions=estimator_sessions.proposed_decisions_from_workbench(filtered_default_workbench),
                    template_type=str(filtered_default_workbench.get("scope", {}).get("template_type") or historical_filters.get("template_type") or ""),
                    proposal_source="historical_defaults",
                    evidence_summary={
                        "historical_filters": historical_filters,
                        "totals": summarize_workbench_totals(filtered_default_workbench),
                    },
                )
                if proposal_id:
                    st.session_state[proposal_saved_key] = True

        st.markdown("### Stage 3 - Estimator Workbench")
        st.caption("Review and edit template decisions. Workbook formulas remain the calculation engine; these rows control the inputs.")
        show_row_details = st.checkbox(
            "Show detailed row diagnostics",
            value=debug_mode,
            key=f"wb_show_row_details_{workbench_key}_{historical_filters_key}",
            help="Shows accepted/rejected evidence, percentile ranges, relaxed filters, and source diagnostics.",
        )
        show_row_option_editor = st.checkbox(
            "Show selected-row option editor",
            value=False,
            key=f"wb_show_row_option_editor_{workbench_key}_{historical_filters_key}",
            help="Shows one focused editor per section with row-specific template, pricing, and crew dropdowns.",
        )
        surface_review_rows = build_surface_area_review_rows(parsed_fields, original_workbench)
        if surface_review_rows:
            st.markdown("#### Surface Areas / Dimensions")
            st.caption("Review the parsed components once here. Target R and edited thickness feed the insulation foam decision; detailed formula trace stays in diagnostics.")
            surface_area_editable_fields = {"target_r_value", "edited_thickness_inches"}
            surface_area_column_order = (
                SURFACE_AREA_DETAIL_COLUMNS
                if show_row_details
                else SURFACE_AREA_REVIEW_COLUMNS
            )
            with estimator_perf_step("surface table prep"):
                surface_area_display_df, surface_area_column_order = workbench_display_frame_from_records(
                    surface_review_rows,
                    surface_area_column_order,
                    editable_fields=surface_area_editable_fields,
                    show_row_details=show_row_details,
                )
            edited_surface_area_df = st.data_editor(
                surface_area_display_df,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                key=f"wb_surface_area_review_{workbench_key}_{scope_key}_{historical_filters_key}",
                column_order=surface_area_column_order,
                column_config={
                    "component": "Component",
                    "quantity": "Qty",
                    "length_ft": "Length",
                    "width_ft": "Width",
                    "height_ft": "Height",
                    "gross_area_sqft": "Gross Sq Ft",
                    "deduction_area_sqft": "Deductions",
                    "net_area_sqft": "Net Sq Ft",
                    "target_r_value": "Target R",
                    "foam_type": "Foam Type",
                    "edited_thickness_inches": "Edited Thickness",
                    "area_formula": "Formula",
                    "source_text": "Source Text",
                    "confidence": "Confidence",
                    "selected_source": "Selected Source",
                    "ai_value": "AI Value",
                    "deterministic_value": "Deterministic Value",
                    "notes": "Notes",
                },
                disabled=[column for column in surface_area_column_order if column not in surface_area_editable_fields],
            )
            edited_surface_rows = edited_surface_area_df.to_dict(orient="records")
            surfaces_by_type = {row.get("surface_type"): row for row in edited_workbench.get("insulation_surfaces") or []}
            surfaces_by_name = {str(row.get("surface") or "").lower(): row for row in edited_workbench.get("insulation_surfaces") or []}
            for row in edited_surface_rows:
                if row.get("component_type") != "surface":
                    continue
                surface = surfaces_by_type.get(row.get("surface_type")) or surfaces_by_name.get(str(row.get("component") or "").lower())
                if not surface:
                    continue
                for field in ("target_r_value", "edited_thickness_inches"):
                    if row.get(field) not in (None, ""):
                        surface[field] = row.get(field)
            edited_workbench["insulation_surfaces"] = list(surfaces_by_type.values()) or list(surfaces_by_name.values())
            if original_workbench.get("area_calculation_trace") and show_row_details:
                with st.expander("Show formula trace", expanded=False):
                    area_trace_rows = display_safe_records(original_workbench.get("area_calculation_trace") or [])
                    area_trace_df = pd.DataFrame(area_trace_rows)
                    st.dataframe(
                        area_trace_df[[column for column in AREA_TRACE_COMPACT_COLUMNS if column in area_trace_df.columns]],
                        use_container_width=True,
                        hide_index=True,
                    )

        if original_workbench.get("insulation_foam_template_decisions"):
            st.markdown("#### Insulation Foam Template")
            foam_template_editable_fields = {
                "include",
                "editable_selector_code",
                "basis_sqft",
                "thickness_inches",
                "yield_or_coverage",
                "unit_price",
                "selected_pricing_candidate",
            }
            foam_template_rows = original_workbench.get("insulation_foam_template_decisions") or []
            foam_template_column_order = (
                []
                if show_row_details
                else INSULATION_FOAM_TEMPLATE_COMPACT_COLUMNS
            )
            with estimator_perf_step("insulation foam table prep"):
                foam_template_display_df, foam_template_column_order = workbench_display_frame_from_records(
                    foam_template_rows,
                    foam_template_column_order,
                    editable_fields=foam_template_editable_fields,
                    show_row_details=show_row_details,
                )
            edited_foam_template_df = st.data_editor(
                foam_template_display_df,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                key=f"wb_insulation_foam_template_{workbench_key}_{scope_key}_{historical_filters_key}",
                column_order=foam_template_column_order,
                column_config={
                    "include": "Include",
                    "workbook_row": "Rows",
                    "editable_selector_code": "Selector",
                    "resolved_template_option": "Template Option",
                    "historical_selector_recommendation": "Historical Default",
                    "historical_selector_evidence_count": "Evidence",
                    "basis_sqft": "Basis Sq Ft",
                    "thickness_inches": "Thickness",
                    "yield_or_coverage": "Yield",
                    "unit_price": "Unit Price",
                    "estimated_units": "Units",
                    "estimated_sets": "Sets",
                    "estimated_cost": "Cost",
                    "selected_pricing_candidate": "Pricing Candidate",
                    "compatibility_status": "Compatibility",
                    "compatibility_warnings": "Warnings",
                    "product_guidance": "Product Guidance",
                    "notes": "Notes",
                },
                disabled=[column for column in foam_template_column_order if column not in foam_template_editable_fields],
            )
            merged_foam_template_rows = merge_editable_rows(
                foam_template_rows,
                edited_foam_template_df.to_dict(orient="records"),
                foam_template_editable_fields,
            )
            if show_row_option_editor:
                merged_foam_template_rows = render_decision_row_option_editor(
                    section_key="insulation_foam_template_decisions",
                    section_label="Insulation Foam Template",
                    rows=merged_foam_template_rows,
                    editable_fields=foam_template_editable_fields,
                    workbench_key=workbench_key,
                    scope_key=scope_key,
                    historical_filters_key=historical_filters_key,
                )
            edited_workbench["insulation_foam_template_decisions"] = merged_foam_template_rows

        insulation_template_editable_fields = {
            "include",
            "editable_selector_code",
            "basis_sqft",
            "linear_ft",
            "quantity",
            "days",
            "hours_per_day",
            "people_count",
            "period",
            "trip_count",
            "round_trip_miles",
            "gal_per_100_sqft",
            "waste_factor_pct",
            "feet_per_unit",
            "unit_price",
            "margin_pct",
            "selected_pricing_candidate",
            "crew_size",
            "daily_rate",
            "hourly_rate",
            "total_hours",
            "formula_mode",
        }
        for section_key, section_label in INSULATION_DECISION_SECTIONS:
            if not original_workbench.get(section_key):
                continue
            st.markdown(f"#### {section_label}")
            section_rows = original_workbench.get(section_key) or []
            section_compact_columns = INSULATION_DECISION_SECTION_COLUMNS.get(
                section_key,
                INSULATION_DECISION_TEMPLATE_COMPACT_COLUMNS,
            )
            section_column_order = (
                []
                if show_row_details
                else section_compact_columns
            )
            with estimator_perf_step(f"{section_label} table prep"):
                section_display_df, section_column_order = workbench_display_frame_from_records(
                    section_rows,
                    section_column_order,
                    editable_fields=insulation_template_editable_fields,
                    show_row_details=show_row_details,
                )
            edited_section_df = st.data_editor(
                section_display_df,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                key=f"wb_{section_key}_{workbench_key}_{scope_key}_{historical_filters_key}",
                column_order=section_column_order,
                column_config={
                    "include": "Include",
                    "workbook_row": "Row",
                    "template_line": "Template Line",
                    "labor_task": "Labor Task",
                    "editable_selector_code": "Selector",
                    "resolved_template_option": "Template Option",
                    "basis_sqft": "Basis Sq Ft",
                    "linear_ft": "Linear Ft",
                    "quantity": "Quantity",
                    "days": "Days",
                    "hours_per_day": "Hours / Day",
                    "people_count": "People",
                    "period": "Period",
                    "trip_count": "Trips",
                    "round_trip_miles": "Round Trip Miles",
                    "gal_per_100_sqft": "Gal / 100 Sq Ft",
                    "waste_factor_pct": "Waste %",
                    "feet_per_unit": "Ft / Unit",
                    "unit_price": "Unit Price",
                    "margin_pct": "Margin %",
                    "estimated_units": "Units",
                    "estimated_gallons": "Gallons",
                    "estimated_drums": "Drums",
                    "total_hours": "Hours",
                    "crew_size": "Crew",
                    "daily_rate": "Daily Rate",
                    "hourly_rate": "Hourly Rate",
                    "formula_mode": "Formula Mode",
                    "estimated_cost": "Cost",
                    "selected_pricing_candidate": "Pricing Candidate",
                    "compatibility_status": "Status",
                    "compatibility_warnings": "Warnings",
                    "product_guidance": "Product Guidance",
                    "notes": "Notes",
                },
                disabled=[column for column in section_column_order if column not in insulation_template_editable_fields],
            )
            merged_section_rows = merge_editable_rows(
                section_rows,
                edited_section_df.to_dict(orient="records"),
                insulation_template_editable_fields,
            )
            if show_row_option_editor:
                merged_section_rows = render_decision_row_option_editor(
                    section_key=section_key,
                    section_label=section_label,
                    rows=merged_section_rows,
                    editable_fields=insulation_template_editable_fields,
                    workbench_key=workbench_key,
                    scope_key=scope_key,
                    historical_filters_key=historical_filters_key,
                )
            edited_workbench[section_key] = merged_section_rows

        if original_workbench.get("roofing_foam_template_decisions"):
            st.markdown("#### Roofing SPF Foam")
            roofing_foam_template_editable_fields = {
                "include",
                "editable_selector_code",
                "basis_sqft",
                "thickness_inches",
                "yield_or_coverage",
                "unit_price",
                "selected_pricing_candidate",
            }
            roofing_foam_template_rows = original_workbench.get("roofing_foam_template_decisions") or []
            roofing_foam_template_column_order = (
                []
                if show_row_details
                else ROOFING_FOAM_TEMPLATE_COMPACT_COLUMNS
            )
            with estimator_perf_step("roofing foam table prep"):
                roofing_foam_template_display_df, roofing_foam_template_column_order = workbench_display_frame_from_records(
                    roofing_foam_template_rows,
                    roofing_foam_template_column_order,
                    editable_fields=roofing_foam_template_editable_fields,
                    show_row_details=show_row_details,
                )
            edited_roofing_foam_template_df = st.data_editor(
                roofing_foam_template_display_df,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                key=f"wb_roofing_foam_template_{workbench_key}_{scope_key}_{historical_filters_key}",
                column_order=roofing_foam_template_column_order,
                column_config={
                    "include": "Include",
                    "workbook_row": "Row",
                    "editable_selector_code": "Selector",
                    "resolved_template_option": "Template Option",
                    "historical_selector_recommendation": "Historical Default",
                    "historical_selector_evidence_count": "Evidence",
                    "basis_sqft": "Basis Sq Ft",
                    "thickness_inches": "Thickness",
                    "yield_or_coverage": "Yield",
                    "unit_price": "Unit Price",
                    "estimated_units": "Units",
                    "estimated_sets": "Sets",
                    "estimated_cost": "Cost",
                    "selected_pricing_candidate": "Pricing Candidate",
                    "compatibility_status": "Compatibility",
                    "compatibility_warnings": "Warnings",
                    "product_guidance": "Product Guidance",
                    "notes": "Notes",
                },
                disabled=[
                    column for column in roofing_foam_template_column_order if column not in roofing_foam_template_editable_fields
                ],
            )
            merged_roofing_foam_template_rows = merge_editable_rows(
                roofing_foam_template_rows,
                edited_roofing_foam_template_df.to_dict(orient="records"),
                roofing_foam_template_editable_fields,
            )
            if show_row_option_editor:
                merged_roofing_foam_template_rows = render_decision_row_option_editor(
                    section_key="roofing_foam_template_decisions",
                    section_label="Roofing SPF Foam",
                    rows=merged_roofing_foam_template_rows,
                    editable_fields=roofing_foam_template_editable_fields,
                    workbench_key=workbench_key,
                    scope_key=scope_key,
                    historical_filters_key=historical_filters_key,
                )
            edited_workbench["roofing_foam_template_decisions"] = merged_roofing_foam_template_rows

        if original_workbench.get("roofing_coating_template_decisions"):
            st.markdown("#### Roof Coating System")
            coating_template_editable_fields = {
                "include",
                "editable_selector_code",
                "basis_sqft",
                "gal_per_100_sqft",
                "waste_factor_pct",
                "unit_price",
                "selected_pricing_candidate",
            }
            coating_template_rows = original_workbench.get("roofing_coating_template_decisions") or []
            coating_template_column_order = (
                []
                if show_row_details
                else ROOFING_COATING_TEMPLATE_COMPACT_COLUMNS
            )
            with estimator_perf_step("roof coating table prep"):
                coating_template_display_df, coating_template_column_order = workbench_display_frame_from_records(
                    coating_template_rows,
                    coating_template_column_order,
                    editable_fields=coating_template_editable_fields,
                    show_row_details=show_row_details,
                )
            edited_coating_template_df = st.data_editor(
                coating_template_display_df,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                key=f"wb_roofing_coating_template_{workbench_key}_{scope_key}_{historical_filters_key}",
                column_order=coating_template_column_order,
                column_config={
                    "include": "Include",
                    "workbook_row": "Row",
                    "editable_selector_code": "Selector",
                    "resolved_template_option": "Template Option",
                    "historical_selector_recommendation": "Historical Default",
                    "historical_selector_evidence_count": "Evidence",
                    "basis_sqft": "Basis Sq Ft",
                    "gal_per_100_sqft": "Gal / 100 Sq Ft",
                    "waste_factor_pct": "Waste %",
                    "unit_price": "Unit Price",
                    "estimated_gallons": "Gallons",
                    "estimated_cost": "Cost",
                    "selected_pricing_candidate": "Pricing Candidate",
                    "compatibility_status": "Compatibility",
                    "compatibility_warnings": "Warnings",
                    "product_guidance": "Product Guidance",
                    "notes": "Notes",
                },
                disabled=[column for column in coating_template_column_order if column not in coating_template_editable_fields],
            )
            merged_coating_template_rows = merge_editable_rows(
                coating_template_rows,
                edited_coating_template_df.to_dict(orient="records"),
                coating_template_editable_fields,
            )
            if show_row_option_editor:
                merged_coating_template_rows = render_decision_row_option_editor(
                    section_key="roofing_coating_template_decisions",
                    section_label="Roof Coating System",
                    rows=merged_coating_template_rows,
                    editable_fields=coating_template_editable_fields,
                    workbench_key=workbench_key,
                    scope_key=scope_key,
                    historical_filters_key=historical_filters_key,
                )
            edited_workbench["roofing_coating_template_decisions"] = merged_coating_template_rows

        if original_workbench.get("roofing_primer_template_decisions"):
            st.markdown("#### Roofing Primer System")
            primer_template_editable_fields = {
                "include",
                "editable_selector_code",
                "basis_sqft",
                "coverage_sqft_per_unit",
                "unit_price",
                "selected_pricing_candidate",
            }
            primer_template_rows = original_workbench.get("roofing_primer_template_decisions") or []
            primer_template_column_order = (
                []
                if show_row_details
                else ROOFING_PRIMER_TEMPLATE_COMPACT_COLUMNS
            )
            with estimator_perf_step("roof primer table prep"):
                primer_template_display_df, primer_template_column_order = workbench_display_frame_from_records(
                    primer_template_rows,
                    primer_template_column_order,
                    editable_fields=primer_template_editable_fields,
                    show_row_details=show_row_details,
                )
            edited_primer_template_df = st.data_editor(
                primer_template_display_df,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                key=f"wb_roofing_primer_template_{workbench_key}_{scope_key}_{historical_filters_key}",
                column_order=primer_template_column_order,
                column_config={
                    "include": "Include",
                    "workbook_row": "Row",
                    "editable_selector_code": "Selector",
                    "resolved_template_option": "Template Option",
                    "historical_selector_recommendation": "Historical Default",
                    "historical_selector_evidence_count": "Evidence",
                    "basis_sqft": "Basis Sq Ft",
                    "coverage_sqft_per_unit": "Sq Ft / Unit",
                    "unit_price": "Unit Price",
                    "estimated_units": "Units",
                    "estimated_cost": "Cost",
                    "selected_pricing_candidate": "Pricing Candidate",
                    "compatibility_status": "Compatibility",
                    "compatibility_warnings": "Warnings",
                    "product_guidance": "Product Guidance",
                    "notes": "Notes",
                },
                disabled=[column for column in primer_template_column_order if column not in primer_template_editable_fields],
            )
            merged_primer_template_rows = merge_editable_rows(
                primer_template_rows,
                edited_primer_template_df.to_dict(orient="records"),
                primer_template_editable_fields,
            )
            if show_row_option_editor:
                merged_primer_template_rows = render_decision_row_option_editor(
                    section_key="roofing_primer_template_decisions",
                    section_label="Roofing Primer System",
                    rows=merged_primer_template_rows,
                    editable_fields=primer_template_editable_fields,
                    workbench_key=workbench_key,
                    scope_key=scope_key,
                    historical_filters_key=historical_filters_key,
                )
            edited_workbench["roofing_primer_template_decisions"] = merged_primer_template_rows

        if original_workbench.get("roofing_detail_template_decisions"):
            st.markdown("#### Roofing Fabric / Sealant System")
            detail_template_editable_fields = {
                "include",
                "editable_selector_code",
                "units",
                "estimated_units",
                "linear_ft",
                "unit_price",
                "selected_pricing_candidate",
            }
            detail_template_rows = original_workbench.get("roofing_detail_template_decisions") or []
            detail_template_column_order = (
                []
                if show_row_details
                else ROOFING_DETAIL_TEMPLATE_COMPACT_COLUMNS
            )
            with estimator_perf_step("roof detail material table prep"):
                detail_template_display_df, detail_template_column_order = workbench_display_frame_from_records(
                    detail_template_rows,
                    detail_template_column_order,
                    editable_fields=detail_template_editable_fields,
                    show_row_details=show_row_details,
                )
            edited_detail_template_df = st.data_editor(
                detail_template_display_df,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                key=f"wb_roofing_detail_template_{workbench_key}_{scope_key}_{historical_filters_key}",
                column_order=detail_template_column_order,
                column_config={
                    "include": "Include",
                    "workbook_row": "Row",
                    "editable_selector_code": "Selector",
                    "resolved_template_option": "Template Option",
                    "historical_selector_recommendation": "Historical Default",
                    "historical_selector_evidence_count": "Evidence",
                    "units": "Units",
                    "linear_ft": "Linear Ft",
                    "unit_price": "Unit Price",
                    "estimated_units": "Expected Units",
                    "estimated_cost": "Cost",
                    "selected_pricing_candidate": "Pricing Candidate",
                    "compatibility_status": "Compatibility",
                    "compatibility_warnings": "Warnings",
                    "product_guidance": "Product Guidance",
                    "notes": "Notes",
                },
                disabled=[column for column in detail_template_column_order if column not in detail_template_editable_fields],
            )
            merged_detail_template_rows = merge_editable_rows(
                detail_template_rows,
                edited_detail_template_df.to_dict(orient="records"),
                detail_template_editable_fields,
            )
            if show_row_option_editor:
                merged_detail_template_rows = render_decision_row_option_editor(
                    section_key="roofing_detail_template_decisions",
                    section_label="Roofing Fabric / Sealant System",
                    rows=merged_detail_template_rows,
                    editable_fields=detail_template_editable_fields,
                    workbench_key=workbench_key,
                    scope_key=scope_key,
                    historical_filters_key=historical_filters_key,
                )
            edited_workbench["roofing_detail_template_decisions"] = merged_detail_template_rows

        if original_workbench.get("roofing_detail_quantity_template_decisions"):
            st.markdown("#### Roofing Detail Quantity")
            detail_quantity_template_editable_fields = {
                "include",
                "linear_ft",
                "units",
                "estimated_units",
                "amount",
            }
            detail_quantity_template_rows = original_workbench.get("roofing_detail_quantity_template_decisions") or []
            detail_quantity_template_column_order = (
                []
                if show_row_details
                else ROOFING_DETAIL_QUANTITY_TEMPLATE_COMPACT_COLUMNS
            )
            with estimator_perf_step("roof detail quantity table prep"):
                detail_quantity_template_display_df, detail_quantity_template_column_order = workbench_display_frame_from_records(
                    detail_quantity_template_rows,
                    detail_quantity_template_column_order,
                    editable_fields=detail_quantity_template_editable_fields,
                    show_row_details=show_row_details,
                )
            edited_detail_quantity_template_df = st.data_editor(
                detail_quantity_template_display_df,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                key=f"wb_roofing_detail_quantity_template_{workbench_key}_{scope_key}_{historical_filters_key}",
                column_order=detail_quantity_template_column_order,
                column_config={
                    "include": "Include",
                    "workbook_row": "Row",
                    "resolved_template_option": "Template Line",
                    "linear_ft": "Linear Ft",
                    "units": "Units",
                    "estimated_units": "Estimated Units",
                    "amount": "Amount",
                    "estimated_cost": "Cost",
                    "compatibility_status": "Status",
                    "compatibility_warnings": "Warnings",
                    "notes": "Notes",
                },
                disabled=[
                    column
                    for column in detail_quantity_template_column_order
                    if column not in detail_quantity_template_editable_fields
                ],
            )
            merged_detail_quantity_template_rows = merge_editable_rows(
                detail_quantity_template_rows,
                edited_detail_quantity_template_df.to_dict(orient="records"),
                detail_quantity_template_editable_fields,
            )
            if show_row_option_editor:
                merged_detail_quantity_template_rows = render_decision_row_option_editor(
                    section_key="roofing_detail_quantity_template_decisions",
                    section_label="Roofing Detail Quantity",
                    rows=merged_detail_quantity_template_rows,
                    editable_fields=detail_quantity_template_editable_fields,
                    workbench_key=workbench_key,
                    scope_key=scope_key,
                    historical_filters_key=historical_filters_key,
                )
            edited_workbench["roofing_detail_quantity_template_decisions"] = merged_detail_quantity_template_rows

        if original_workbench.get("roofing_board_fastener_template_decisions"):
            st.markdown("#### Roofing Board Stock")
            board_template_editable_fields = {
                "include",
                "editable_selector_code",
                "basis_sqft",
                "board_area_sqft",
                "thickness_inches",
                "price_per_square",
                "unit_price",
                "unit_price_per_thousand",
                "selected_pricing_candidate",
            }
            board_template_rows = original_workbench.get("roofing_board_fastener_template_decisions") or []
            board_stock_rows = [row for row in board_template_rows if str((row or {}).get("template_bucket") or "") == "board_stock"]
            fastener_plate_rows = [row for row in board_template_rows if str((row or {}).get("template_bucket") or "") != "board_stock"]
            board_template_column_order = (
                []
                if show_row_details
                else ROOFING_BOARD_STOCK_TEMPLATE_COMPACT_COLUMNS
            )
            with estimator_perf_step("roof board stock table prep"):
                board_template_display_df, board_template_column_order = workbench_display_frame_from_records(
                    board_stock_rows,
                    board_template_column_order,
                    editable_fields=board_template_editable_fields,
                    show_row_details=show_row_details,
                )
            edited_board_template_df = st.data_editor(
                board_template_display_df,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                key=f"wb_roofing_board_fastener_template_{workbench_key}_{scope_key}_{historical_filters_key}",
                column_order=board_template_column_order,
                column_config={
                    "include": "Include",
                    "workbook_row": "Row",
                    "editable_selector_code": "Selector",
                    "resolved_template_option": "Template Option",
                    "historical_selector_recommendation": "Historical Default",
                    "historical_selector_evidence_count": "Evidence",
                    "basis_sqft": "Board Area",
                    "board_area_sqft": "Fastener Area",
                    "thickness_inches": "Thickness",
                    "price_per_square": "Price / Sq",
                    "unit_price": "Unit Price",
                    "unit_price_per_thousand": "Price / 1,000",
                    "estimated_squares": "Squares",
                    "estimated_units": "Calculated Units",
                    "estimated_cost": "Cost",
                    "selected_pricing_candidate": "Pricing Candidate",
                    "compatibility_status": "Compatibility",
                    "compatibility_warnings": "Warnings",
                    "product_guidance": "Product Guidance",
                    "notes": "Notes",
                },
                disabled=[column for column in board_template_column_order if column not in board_template_editable_fields],
            )
            merged_board_stock_rows = merge_editable_rows(
                board_stock_rows,
                edited_board_template_df.to_dict(orient="records"),
                board_template_editable_fields,
            )
            if show_row_option_editor:
                merged_board_stock_rows = render_decision_row_option_editor(
                    section_key="roofing_board_fastener_template_decisions",
                    section_label="Roofing Board Stock",
                    rows=merged_board_stock_rows,
                    editable_fields=board_template_editable_fields,
                    workbench_key=workbench_key,
                    scope_key=scope_key,
                    historical_filters_key=historical_filters_key,
                )
            merged_fastener_plate_rows = fastener_plate_rows
            if fastener_plate_rows:
                st.markdown("#### Roofing Fasteners / Plates")
                fastener_plate_column_order = (
                    []
                    if show_row_details
                    else ROOFING_FASTENER_PLATE_TEMPLATE_COMPACT_COLUMNS
                )
                with estimator_perf_step("roof fastener plate table prep"):
                    fastener_plate_display_df, fastener_plate_column_order = workbench_display_frame_from_records(
                        fastener_plate_rows,
                        fastener_plate_column_order,
                        editable_fields=board_template_editable_fields,
                        show_row_details=show_row_details,
                    )
                edited_fastener_plate_df = st.data_editor(
                    fastener_plate_display_df,
                    use_container_width=True,
                    hide_index=True,
                    num_rows="fixed",
                    key=f"wb_roofing_fastener_plate_template_{workbench_key}_{scope_key}_{historical_filters_key}",
                    column_order=fastener_plate_column_order,
                    column_config={
                        "include": "Include",
                        "workbook_row": "Row",
                        "editable_selector_code": "Selector",
                        "resolved_template_option": "Template Option",
                        "historical_selector_recommendation": "Historical Default",
                        "historical_selector_evidence_count": "Evidence",
                        "board_area_sqft": "Fastener Area",
                        "unit_price_per_thousand": "Price / 1,000",
                        "estimated_units": "Calculated Units",
                        "estimated_cost": "Cost",
                        "selected_pricing_candidate": "Pricing Candidate",
                        "compatibility_status": "Compatibility",
                        "compatibility_warnings": "Warnings",
                        "product_guidance": "Product Guidance",
                        "notes": "Notes",
                    },
                    disabled=[column for column in fastener_plate_column_order if column not in board_template_editable_fields],
                )
                merged_fastener_plate_rows = merge_editable_rows(
                    fastener_plate_rows,
                    edited_fastener_plate_df.to_dict(orient="records"),
                    board_template_editable_fields,
                )
                if show_row_option_editor:
                    merged_fastener_plate_rows = render_decision_row_option_editor(
                        section_key="roofing_board_fastener_template_decisions",
                        section_label="Roofing Fasteners / Plates",
                        rows=merged_fastener_plate_rows,
                        editable_fields=board_template_editable_fields,
                        workbench_key=workbench_key,
                        scope_key=scope_key,
                        historical_filters_key=historical_filters_key,
                    )
            merged_board_by_id = {
                str(first_nonblank(row.get("decision_id"), row.get("workbook_row"), row.get("template_bucket"))): row
                for row in [*merged_board_stock_rows, *merged_fastener_plate_rows]
                if isinstance(row, dict)
            }
            merged_board_template_rows = [
                merged_board_by_id.get(str(first_nonblank(row.get("decision_id"), row.get("workbook_row"), row.get("template_bucket"))), row)
                for row in board_template_rows
            ]
            edited_workbench["roofing_board_fastener_template_decisions"] = merged_board_template_rows

        if original_workbench.get("roofing_granules_template_decisions"):
            st.markdown("#### Roofing Granules System")
            granules_template_editable_fields = {
                "include",
                "editable_selector_code",
                "basis_sqft",
                "coverage_lbs_per_100_sqft",
                "bag_weight_lbs",
                "unit_price",
                "selected_pricing_candidate",
            }
            granules_template_rows = original_workbench.get("roofing_granules_template_decisions") or []
            granules_template_column_order = (
                []
                if show_row_details
                else ROOFING_GRANULES_TEMPLATE_COMPACT_COLUMNS
            )
            with estimator_perf_step("roof granules table prep"):
                granules_template_display_df, granules_template_column_order = workbench_display_frame_from_records(
                    granules_template_rows,
                    granules_template_column_order,
                    editable_fields=granules_template_editable_fields,
                    show_row_details=show_row_details,
                )
            edited_granules_template_df = st.data_editor(
                granules_template_display_df,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                key=f"wb_roofing_granules_template_{workbench_key}_{scope_key}_{historical_filters_key}",
                column_order=granules_template_column_order,
                column_config={
                    "include": "Include",
                    "workbook_row": "Row",
                    "editable_selector_code": "Selector",
                    "resolved_template_option": "Template Option",
                    "historical_selector_recommendation": "Historical Default",
                    "historical_selector_evidence_count": "Evidence",
                    "basis_sqft": "Basis Sq Ft",
                    "coverage_lbs_per_100_sqft": "Lb / 100 Sq Ft",
                    "bag_weight_lbs": "Bag Lb",
                    "unit_price": "Unit Price",
                    "estimated_units": "Bags",
                    "estimated_cost": "Cost",
                    "selected_pricing_candidate": "Pricing Candidate",
                    "compatibility_status": "Compatibility",
                    "compatibility_warnings": "Warnings",
                    "product_guidance": "Product Guidance",
                    "notes": "Notes",
                },
                disabled=[column for column in granules_template_column_order if column not in granules_template_editable_fields],
            )
            merged_granules_template_rows = merge_editable_rows(
                granules_template_rows,
                edited_granules_template_df.to_dict(orient="records"),
                granules_template_editable_fields,
            )
            if show_row_option_editor:
                merged_granules_template_rows = render_decision_row_option_editor(
                    section_key="roofing_granules_template_decisions",
                    section_label="Roofing Granules System",
                    rows=merged_granules_template_rows,
                    editable_fields=granules_template_editable_fields,
                    workbench_key=workbench_key,
                    scope_key=scope_key,
                    historical_filters_key=historical_filters_key,
                )
            edited_workbench["roofing_granules_template_decisions"] = merged_granules_template_rows

        if original_workbench.get("roofing_equipment_template_decisions"):
            st.markdown("#### Roofing Equipment / Dumpster")
            equipment_template_editable_fields = {
                "include",
                "editable_selector_code",
                "basis_sqft",
                "debris_thickness_inches",
                "thickness_inches",
                "size",
                "period",
                "days",
                "unit_price",
                "margin_pct",
            }
            equipment_template_rows = original_workbench.get("roofing_equipment_template_decisions") or []
            equipment_template_column_order = (
                []
                if show_row_details
                else ROOFING_EQUIPMENT_TEMPLATE_COMPACT_COLUMNS
            )
            with estimator_perf_step("roof equipment table prep"):
                equipment_template_display_df, equipment_template_column_order = workbench_display_frame_from_records(
                    equipment_template_rows,
                    equipment_template_column_order,
                    editable_fields=equipment_template_editable_fields,
                    show_row_details=show_row_details,
                )
            edited_equipment_template_df = st.data_editor(
                equipment_template_display_df,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                key=f"wb_roofing_equipment_template_{workbench_key}_{scope_key}_{historical_filters_key}",
                column_order=equipment_template_column_order,
                column_config={
                    "include": "Include",
                    "workbook_row": "Row",
                    "editable_selector_code": "Selector",
                    "resolved_template_option": "Template Option",
                    "historical_selector_recommendation": "Historical Default",
                    "historical_selector_evidence_count": "Evidence",
                    "basis_sqft": "Basis Sq Ft",
                    "debris_thickness_inches": "Debris Thickness",
                    "debris_thickness_source": "Thickness Source",
                    "thickness_inches": "Workbook Thickness",
                    "size": "Size",
                    "period": "Period",
                    "days": "Days",
                    "unit_price": "Unit Price",
                    "margin_pct": "Margin %",
                    "estimated_units": "Units",
                    "estimated_cost": "Cost",
                    "compatibility_status": "Compatibility",
                    "compatibility_warnings": "Warnings",
                    "notes": "Notes",
                },
                disabled=[column for column in equipment_template_column_order if column not in equipment_template_editable_fields],
            )
            merged_equipment_template_rows = merge_editable_rows(
                equipment_template_rows,
                edited_equipment_template_df.to_dict(orient="records"),
                equipment_template_editable_fields,
            )
            if show_row_option_editor:
                merged_equipment_template_rows = render_decision_row_option_editor(
                    section_key="roofing_equipment_template_decisions",
                    section_label="Roofing Equipment / Dumpster",
                    rows=merged_equipment_template_rows,
                    editable_fields=equipment_template_editable_fields,
                    workbench_key=workbench_key,
                    scope_key=scope_key,
                    historical_filters_key=historical_filters_key,
                )
            edited_workbench["roofing_equipment_template_decisions"] = merged_equipment_template_rows

        if original_workbench.get("roofing_travel_freight_template_decisions"):
            st.markdown("#### Roofing Travel / Freight")
            travel_freight_template_editable_fields = {
                "include",
                "estimated_units",
                "units",
                "amount",
                "trip_count",
                "round_trip_miles",
                "unit_price",
            }
            travel_freight_template_rows = original_workbench.get("roofing_travel_freight_template_decisions") or []
            travel_freight_template_column_order = (
                []
                if show_row_details
                else ROOFING_TRAVEL_FREIGHT_TEMPLATE_COMPACT_COLUMNS
            )
            with estimator_perf_step("roof travel freight table prep"):
                travel_freight_template_display_df, travel_freight_template_column_order = workbench_display_frame_from_records(
                    travel_freight_template_rows,
                    travel_freight_template_column_order,
                    editable_fields=travel_freight_template_editable_fields,
                    show_row_details=show_row_details,
                )
            edited_travel_freight_template_df = st.data_editor(
                travel_freight_template_display_df,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                key=data_editor_state_key(
                    f"wb_roofing_travel_freight_template_{workbench_key}_{scope_key}_{historical_filters_key}",
                    travel_freight_template_display_df,
                ),
                column_order=travel_freight_template_column_order,
                column_config={
                    "include": "Include",
                    "workbook_row": "Row",
                    "resolved_template_option": "Template Option",
                    "estimated_units": "Units",
                    "units": "Units",
                    "amount": "Amount",
                    "trip_count": "Trips",
                    "round_trip_miles": "Round Trip Miles",
                    "unit_price": "Rate / Unit Price",
                    "estimated_cost": "Cost",
                    "compatibility_status": "Status",
                    "compatibility_warnings": "Warnings",
                    "notes": "Notes",
                },
                disabled=[
                    column
                    for column in travel_freight_template_column_order
                    if column not in travel_freight_template_editable_fields
                ],
            )
            merged_travel_freight_template_rows = merge_editable_rows(
                travel_freight_template_rows,
                edited_travel_freight_template_df.to_dict(orient="records"),
                travel_freight_template_editable_fields,
            )
            if show_row_option_editor:
                merged_travel_freight_template_rows = render_decision_row_option_editor(
                    section_key="roofing_travel_freight_template_decisions",
                    section_label="Roofing Travel / Freight",
                    rows=merged_travel_freight_template_rows,
                    editable_fields=travel_freight_template_editable_fields,
                    workbench_key=workbench_key,
                    scope_key=scope_key,
                    historical_filters_key=historical_filters_key,
                )
            edited_workbench["roofing_travel_freight_template_decisions"] = merged_travel_freight_template_rows

        if original_workbench.get("roofing_accessory_template_decisions"):
            st.markdown("#### Roofing Accessories / Support")
            accessory_template_editable_fields = {
                "include",
                "editable_selector_code",
                "total_coating_gallons",
                "linear_ft",
                "units",
                "estimated_units",
                "amount",
                "unit_price",
            }
            accessory_template_rows = original_workbench.get("roofing_accessory_template_decisions") or []
            accessory_template_column_order = (
                []
                if show_row_details
                else ROOFING_ACCESSORY_TEMPLATE_COMPACT_COLUMNS
            )
            with estimator_perf_step("roof accessory table prep"):
                accessory_template_display_df, accessory_template_column_order = workbench_display_frame_from_records(
                    accessory_template_rows,
                    accessory_template_column_order,
                    editable_fields=accessory_template_editable_fields,
                    show_row_details=show_row_details,
                )
            edited_accessory_template_df = st.data_editor(
                accessory_template_display_df,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                key=f"wb_roofing_accessory_template_{workbench_key}_{scope_key}_{historical_filters_key}",
                column_order=accessory_template_column_order,
                column_config={
                    "include": "Include",
                    "workbook_row": "Row",
                    "editable_selector_code": "Selector",
                    "resolved_template_option": "Template Option",
                    "total_coating_gallons": "Coating Gallons",
                    "linear_ft": "Linear Ft",
                    "units": "Units",
                    "estimated_units": "Units",
                    "amount": "Amount",
                    "unit_price": "Unit Price",
                    "estimated_cost": "Cost",
                    "compatibility_status": "Status",
                    "compatibility_warnings": "Warnings",
                    "notes": "Notes",
                },
                disabled=[column for column in accessory_template_column_order if column not in accessory_template_editable_fields],
            )
            merged_accessory_template_rows = merge_editable_rows(
                accessory_template_rows,
                edited_accessory_template_df.to_dict(orient="records"),
                accessory_template_editable_fields,
            )
            if show_row_option_editor:
                merged_accessory_template_rows = render_decision_row_option_editor(
                    section_key="roofing_accessory_template_decisions",
                    section_label="Roofing Accessories / Support",
                    rows=merged_accessory_template_rows,
                    editable_fields=accessory_template_editable_fields,
                    workbench_key=workbench_key,
                    scope_key=scope_key,
                    historical_filters_key=historical_filters_key,
                )
            edited_workbench["roofing_accessory_template_decisions"] = merged_accessory_template_rows

        if original_workbench.get("roofing_logistics_expense_template_decisions"):
            st.markdown("#### Roofing Loading / Travel / Lodging")
            roofing_logistics_expense_editable_fields = {
                "include",
                "hours_per_day",
                "days",
                "people_count",
                "trip_count",
                "unit_price",
            }
            roofing_logistics_expense_rows = original_workbench.get("roofing_logistics_expense_template_decisions") or []
            roofing_logistics_expense_column_order = (
                []
                if show_row_details
                else ROOFING_LOGISTICS_EXPENSE_TEMPLATE_COMPACT_COLUMNS
            )
            with estimator_perf_step("roofing logistics expense table prep"):
                roofing_logistics_expense_display_df, roofing_logistics_expense_column_order = workbench_display_frame_from_records(
                    roofing_logistics_expense_rows,
                    roofing_logistics_expense_column_order,
                    editable_fields=roofing_logistics_expense_editable_fields,
                    show_row_details=show_row_details,
                )
            edited_roofing_logistics_expense_df = st.data_editor(
                roofing_logistics_expense_display_df,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                key=data_editor_state_key(
                    f"wb_roofing_logistics_expense_{workbench_key}_{scope_key}_{historical_filters_key}",
                    roofing_logistics_expense_display_df,
                ),
                column_order=roofing_logistics_expense_column_order,
                column_config={
                    "include": "Include",
                    "workbook_row": "Row",
                    "template_line": "Expense",
                    "hours_per_day": "Hours",
                    "days": "Days",
                    "people_count": "People",
                    "trip_count": "Trips",
                    "unit_price": "Rate",
                    "estimated_units": "Units",
                    "estimated_cost": "Cost",
                    "formula_model": "Formula",
                    "compatibility_status": "Status",
                    "compatibility_warnings": "Warnings",
                    "notes": "Notes",
                },
                disabled=[
                    column
                    for column in roofing_logistics_expense_column_order
                    if column not in roofing_logistics_expense_editable_fields
                ],
            )
            edited_workbench["roofing_logistics_expense_template_decisions"] = merge_editable_rows(
                roofing_logistics_expense_rows,
                edited_roofing_logistics_expense_df.to_dict(orient="records"),
                roofing_logistics_expense_editable_fields,
            )

        if str(edited_scope.get("template_type") or "").lower() != "insulation":
            st.markdown("#### Roofing Free Adders")
            roofing_free_adder_editable_fields = {
                "include",
                "workbook_row",
                "template_line",
                "amount",
                "estimated_cost",
                "markup_treatment",
                "notes",
            }
            roofing_free_adder_rows = original_workbench.get("roofing_free_adder_template_decisions") or []
            roofing_free_adder_display_rows = roofing_free_adder_rows or [
                {
                    "include": False,
                    "workbook_row": "",
                    "template_line": "",
                    "amount": 0.0,
                    "estimated_cost": 0.0,
                    "markup_treatment": "post_markup",
                    "compatibility_status": "review",
                    "compatibility_warnings": "",
                    "notes": "",
                }
            ]
            roofing_free_adder_column_order = (
                []
                if show_row_details
                else ROOFING_FREE_ADDER_TEMPLATE_COMPACT_COLUMNS
            )
            with estimator_perf_step("roofing free adder table prep"):
                roofing_free_adder_display_df, roofing_free_adder_column_order = workbench_display_frame_from_records(
                    roofing_free_adder_display_rows,
                    roofing_free_adder_column_order,
                    editable_fields=roofing_free_adder_editable_fields,
                    show_row_details=show_row_details,
                )
            edited_roofing_free_adder_df = st.data_editor(
                roofing_free_adder_display_df,
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                key=f"wb_roofing_free_adder_{workbench_key}_{scope_key}_{historical_filters_key}",
                column_order=roofing_free_adder_column_order,
                column_config={
                    "include": "Include",
                    "workbook_row": "Source Row",
                    "template_line": "Adder",
                    "amount": "Amount",
                    "estimated_cost": "Cost",
                    "markup_treatment": "Markup Treatment",
                    "compatibility_status": "Status",
                    "compatibility_warnings": "Warnings",
                    "notes": "Notes",
                },
                disabled=[
                    column
                    for column in roofing_free_adder_column_order
                    if column not in roofing_free_adder_editable_fields
                ],
            )
            edited_workbench["roofing_free_adder_template_decisions"] = merge_dynamic_free_adder_rows(
                roofing_free_adder_rows,
                edited_roofing_free_adder_df.to_dict(orient="records"),
                roofing_free_adder_editable_fields,
            )

        if original_workbench.get("roofing_labor_template_decisions"):
            st.markdown("#### Roofing Labor Planning")
            labor_template_editable_fields = {
                "include",
                "days",
                "crew_size",
                "crew_people_selection",
                "daily_rate",
                "hourly_rate",
                "labor_rate",
                "editable_hours_per_1000_sqft",
                "total_hours",
                "editable_total_hours",
                "formula_mode",
            }
            labor_template_rows = original_workbench.get("roofing_labor_template_decisions") or []
            labor_template_column_order = (
                []
                if show_row_details
                else ROOFING_LABOR_TEMPLATE_COMPACT_COLUMNS
            )
            with estimator_perf_step("roof labor table prep"):
                labor_template_display_df, labor_template_column_order = workbench_display_frame_from_records(
                    labor_template_rows,
                    labor_template_column_order,
                    editable_fields=labor_template_editable_fields,
                    show_row_details=show_row_details,
                )
            edited_labor_template_df = st.data_editor(
                labor_template_display_df,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                key=f"wb_roofing_labor_template_{workbench_key}_{scope_key}_{historical_filters_key}",
                column_order=labor_template_column_order,
                column_config={
                    "include": "Include",
                    "workbook_row": "Row",
                    "labor_task": "Labor Task",
                    "days": "Days",
                    "crew_size": "Crew",
                    "crew_people_selection": "Crew / People",
                    "crew_selection": "People Rate",
                    "selected_daily_rate_cell": "Daily Rate Cell",
                    "daily_rate": "Daily Rate",
                    "hourly_rate": "Hourly Rate",
                    "labor_rate": "Hourly Rate",
                    "editable_hours_per_1000_sqft": "Hrs / 1000 Sq Ft",
                    "total_hours": "Total Hours",
                    "editable_total_hours": "Total Hours",
                    "formula_mode": "Formula Mode",
                    "estimated_cost": "Cost",
                    "historical_selector_evidence_count": "Evidence",
                    "decision_confidence": "Confidence",
                    "compatibility_status": "Status",
                    "compatibility_warnings": "Warnings",
                    "notes": "Notes",
                },
                disabled=[column for column in labor_template_column_order if column not in labor_template_editable_fields],
            )
            merged_labor_template_rows = merge_editable_rows(
                labor_template_rows,
                edited_labor_template_df.to_dict(orient="records"),
                labor_template_editable_fields,
            )
            if show_row_option_editor:
                merged_labor_template_rows = render_decision_row_option_editor(
                    section_key="roofing_labor_template_decisions",
                    section_label="Roofing Labor Planning",
                    rows=merged_labor_template_rows,
                    editable_fields=labor_template_editable_fields,
                    workbench_key=workbench_key,
                    scope_key=scope_key,
                    historical_filters_key=historical_filters_key,
                )
            edited_workbench["roofing_labor_template_decisions"] = merged_labor_template_rows

        if original_workbench.get("pricing_markup_decisions"):
            st.markdown("#### Pricing Markup")
            pricing_markup_editable_fields = {
                "include",
                "markup_pct",
            }
            pricing_markup_rows = original_workbench.get("pricing_markup_decisions") or []
            pricing_markup_column_order = (
                []
                if show_row_details
                else PRICING_MARKUP_COMPACT_COLUMNS
            )
            with estimator_perf_step("pricing markup table prep"):
                pricing_markup_display_df, pricing_markup_column_order = workbench_display_frame_from_records(
                    pricing_markup_rows,
                    pricing_markup_column_order,
                    editable_fields=pricing_markup_editable_fields,
                    show_row_details=show_row_details,
                )
            edited_pricing_markup_df = st.data_editor(
                pricing_markup_display_df,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                key=f"wb_pricing_markup_{workbench_key}_{scope_key}_{historical_filters_key}",
                column_order=pricing_markup_column_order,
                column_config={
                    "include": "Include",
                    "workbook_row": "Row",
                    "template_line": "Markup",
                    "markup_pct": "Markup %",
                    "historical_markup_pct": "Historical %",
                    "historical_markup_p25": "P25 %",
                    "historical_markup_p75": "P75 %",
                    "base_total": "Base Total",
                    "estimated_cost": "Amount",
                    "historical_selector_evidence_count": "Evidence",
                    "decision_confidence": "Confidence",
                    "compatibility_status": "Status",
                    "compatibility_warnings": "Warnings",
                    "notes": "Notes",
                },
                disabled=[column for column in pricing_markup_column_order if column not in pricing_markup_editable_fields],
            )
            edited_workbench["pricing_markup_decisions"] = merge_editable_rows(
                pricing_markup_rows,
                edited_pricing_markup_df.to_dict(orient="records"),
                pricing_markup_editable_fields,
            )

        edited_workbench = recalculate_workbench_tables_for_ui(edited_workbench, data=data)
        st.session_state[previous_workbench_key] = edited_workbench
        totals = summarize_workbench_totals(edited_workbench)
        labor_metric_rows = (
            edited_workbench.get("insulation_labor_template_decisions")
            or edited_workbench.get("roofing_labor_template_decisions")
            or []
        )
        selected_labor_hours = sum(
            float(row.get("calculated_hours") or row.get("total_hours") or 0)
            for row in labor_metric_rows
            if row.get("include")
        )
        selected_area = float(edited_scope.get("net_sqft") or 0)
        labor_hours_per_1000 = selected_labor_hours / selected_area * 1000 if selected_area else 0.0
        metric_row(
            [
                ("Materials", fmt_dollar(totals.get("material_total"))),
                ("Labor", fmt_dollar(totals.get("labor_total"))),
                ("Adders", fmt_dollar(totals.get("adder_total"))),
                ("Overhead", fmt_dollar(totals.get("overhead_amount"))),
                ("Profit", fmt_dollar(totals.get("profit_amount"))),
                ("Draft Total", fmt_dollar(totals.get("draft_total"))),
                ("Labor Hrs / 1k Sq Ft", f"{labor_hours_per_1000:,.1f}"),
            ]
        )

        with st.sidebar.expander("Sanity Check Examples", expanded=False):
            similar_rows = original_workbench.get("similar_jobs") or []
            if similar_rows:
                show_table(
                    dataframe_from_records(similar_rows),
                    ["job_id", "customer", "job_name", "estimated_sqft", "estimated_value", "price_per_sqft", "similarity_score", "reason_matched"],
                    height=360,
                )
            else:
                st.caption("No similar jobs available for this draft.")

        edit_history_preview = build_edit_history_rows(feedback_baseline, edited_workbench)
        reason_required_rows = [row for row in edit_history_preview if row.get("reason_required")]
        reason_map: dict[str, str] = {}
        if reason_required_rows:
            with st.expander("Large Edits - Optional Reasons", expanded=False):
                st.caption("Reasons are optional. They are requested when material quantities change more than 50% or labor hours change more than 30%.")
                for row in reason_required_rows[:20]:
                    reason_key = f"{row.get('section')}.{row.get('field')}"
                    reason_map[reason_key] = st.text_input(
                        f"{row.get('section')} {row.get('field')} changed from {row.get('historical_default')} to {row.get('final_value')}",
                        key=f"wb_reason_{workbench_key}_{reason_key}",
                    )

        with st.expander("Suggested Rules (placeholder)", expanded=False):
            st.caption("Suggested rules are collected for future approval dashboards. They are not applied automatically.")
            show_table(dataframe_from_records(original_workbench.get("suggested_rules") or []), ["rule", "status", "applied_automatically"], height=160)

        if not show_row_details:
            render_workbench_selected_row_details(
                edited_workbench,
                workbench_key=workbench_key,
                scope_key=scope_key,
                historical_filters_key=historical_filters_key,
            )

        with st.expander("Draft workbook input preview", expanded=False):
            st.json(draft_workbook_inputs_for_ui(edited_workbench))

        st.markdown("**Excel Estimate Draft**")
        workbook_path_key = f"field_notes_excel_workbook_path_{workbench_key}"
        workbook_error_key = f"field_notes_excel_workbook_error_{workbench_key}"
        if st.button("Generate Excel Estimate Draft", key=f"generate_field_notes_excel_workbook_{workbench_key}"):
            template_path = resolve_default_template_path()
            if not template_path.exists():
                message = "Estimate template workbook not found. Add it to templates/Estimate - Full Turnkey.xlsx."
                st.session_state.pop(workbook_path_key, None)
                st.session_state[workbook_error_key] = message
                st.warning(message)
            else:
                try:
                    edited_workbook_inputs = draft_workbook_inputs_for_ui(edited_workbench)
                    output_path = generate_estimate_workbook(
                        edited_workbook_inputs,
                        template_path,
                        DEFAULT_ESTIMATE_OUTPUT_DIR,
                    )
                    edit_rows = build_edit_history_rows(feedback_baseline, edited_workbench, reason_map=reason_map)
                    feedback_path = append_edit_history(edit_rows)
                    session_id = current_estimator_session_id()
                    if session_id:
                        capture_estimator_session_event(
                            estimator_sessions.save_decision_edits,
                            session_id,
                            edit_rows,
                            edited_by="estimator",
                        )
                        capture_estimator_memory_candidates(
                            session_id,
                            edit_rows,
                            template_type=str(edited_scope.get("template_type") or ""),
                        )
                        workbook_cell_writes = estimator_sessions.workbook_cell_writes_from_inputs(edited_workbook_inputs)
                        final_id = capture_estimator_session_event(
                            estimator_sessions.save_final_decisions,
                            session_id,
                            final_decisions=estimator_sessions.final_decisions_from_workbench(edited_workbench),
                            calculated_outputs={
                                "totals": totals,
                                "draft_workbook_inputs": edited_workbook_inputs,
                            },
                            workbook_cell_writes=workbook_cell_writes,
                            workbook_export_path=str(output_path),
                        )
                        if final_id:
                            capture_estimator_session_event(
                                estimator_sessions.save_session_artifact,
                                session_id,
                                artifact_type="workbook",
                                artifact_path=str(output_path),
                                artifact_json={"final_decision_id": final_id},
                            )
                    st.session_state[workbook_path_key] = str(output_path)
                    st.session_state.pop(workbook_error_key, None)
                    st.success(f"Excel estimate draft created: {output_path}")
                    st.caption(f"Estimator edit history captured: {feedback_path}")
                    st.download_button(
                        "Download Excel Estimate Draft",
                        data=output_path.read_bytes(),
                        file_name=output_path.name,
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="download_field_notes_excel_workbook",
                    )
                except Exception as exc:
                    logger.exception("Field notes Excel draft generation failed")
                    message = f"Could not generate Excel estimate draft: {safe_exception_text(exc)}"
                    st.session_state.pop(workbook_path_key, None)
                    st.session_state[workbook_error_key] = message
                    st.error(message)

        st.markdown("**Review Package**")
        if st.button("Export Review Package", key=f"export_workbench_review_package_{workbench_key}_{scope_key}_{historical_filters_key}"):
            try:
                workbook_path_for_package = st.session_state.get(workbook_path_key)
                if workbook_path_for_package and not Path(str(workbook_path_for_package)).exists():
                    workbook_path_for_package = None
                workbook_error_for_package = None if workbook_path_for_package else (
                    st.session_state.get(workbook_error_key)
                    or "Workbook was not included. Use Generate Excel Estimate Draft first if the package needs the workbook."
                )
                draft_inputs_for_package = draft_workbook_inputs_for_ui(edited_workbench)
                review_export_cache_key = stable_payload_hash(
                    {
                        "workbench": edited_workbench,
                        "draft_workbook_inputs": draft_inputs_for_package,
                        "input_notes": recommendation_notes,
                        "workbook_path": workbook_path_for_package,
                        "workbook_error": workbook_error_for_package,
                        "run_id": str(edited_workbench.get("estimate_id") or workbench_key),
                    }
                )
                review_export_cache_state_key = f"workbench_review_package_export_cache_{workbench_key}"
                package_path = cached_export_path_for_ui(
                    review_export_cache_state_key,
                    review_export_cache_key,
                    "review package export",
                )
                package_cache_hit = package_path is not None
                if package_path is None:
                    with estimator_perf_step("review package export", cache_status="miss"):
                        package_path = export_workbench_review_package(
                            workbench=edited_workbench,
                            input_notes=recommendation_notes,
                            output_dir=DEFAULT_WORKBENCH_EXPORT_DIR,
                            workbook_path=workbook_path_for_package,
                            workbook_export_error=workbook_error_for_package,
                            runtime=getattr(field_recommendation, "runtime_seconds_by_stage", None)
                            or field_recommendation.parsed_fields.get("runtime_seconds_by_stage")
                            or {},
                            run_id=str(edited_workbench.get("estimate_id") or workbench_key),
                            include_debug=False,
                            workbench_is_recalculated=True,
                            draft_workbook_inputs=draft_inputs_for_package,
                        )
                    store_export_path_for_ui(review_export_cache_state_key, review_export_cache_key, package_path)
                session_id = current_estimator_session_id()
                if session_id and not package_cache_hit:
                    edit_rows = build_edit_history_rows(feedback_baseline, edited_workbench, reason_map=reason_map)
                    capture_estimator_session_event(
                        estimator_sessions.save_decision_edits,
                        session_id,
                        edit_rows,
                        edited_by="estimator",
                    )
                    capture_estimator_memory_candidates(
                        session_id,
                        edit_rows,
                        template_type=str(edited_scope.get("template_type") or ""),
                    )
                    edited_workbook_inputs_for_capture = draft_inputs_for_package
                    final_id = capture_estimator_session_event(
                        estimator_sessions.save_final_decisions,
                        session_id,
                        final_decisions=estimator_sessions.final_decisions_from_workbench(edited_workbench),
                        calculated_outputs={
                            "totals": totals,
                            "draft_workbook_inputs": edited_workbook_inputs_for_capture,
                            "review_package_path": str(package_path),
                        },
                        workbook_cell_writes=estimator_sessions.workbook_cell_writes_from_inputs(edited_workbook_inputs_for_capture),
                        workbook_export_path=workbook_path_for_package,
                    )
                    capture_estimator_session_event(
                        estimator_sessions.save_session_artifact,
                        session_id,
                        artifact_type="review_package",
                        artifact_path=str(package_path),
                        artifact_json={"final_decision_id": final_id, "workbook_export_error": workbook_error_for_package},
                    )
                st.session_state[f"workbench_review_package_path_{workbench_key}"] = str(package_path)
                st.session_state[f"workbench_review_package_bytes_{workbench_key}"] = cached_download_bytes(package_path)
                st.success(f"Estimator review package created: {package_path}")
                if workbook_path_for_package:
                    st.caption(f"Included generated workbook: {workbook_path_for_package}")
                elif workbook_error_for_package:
                    st.warning(f"Review package includes workbook_export_error.txt: {workbook_error_for_package}")
            except Exception as exc:
                logger.exception("Estimator workbench review package export failed")
                st.error(f"Could not export review package: {safe_exception_text(exc)}")
        package_path_value = st.session_state.get(f"workbench_review_package_path_{workbench_key}")
        if package_path_value:
            package_path = Path(package_path_value)
            if package_path.exists():
                st.caption(f"Local review package path: {package_path}")
                st.download_button(
                    "Download Review Package",
                    data=st.session_state.get(f"workbench_review_package_bytes_{workbench_key}") or cached_download_bytes(package_path),
                    file_name=package_path.name,
                    mime="application/zip",
                    key=f"download_workbench_review_package_{workbench_key}",
                )
        st.markdown("**Session Review Package**")
        session_id = current_estimator_session_id()
        if session_id:
            st.caption(f"Session ID: {session_id}")
            if st.button("Export Session Review Package", key=f"export_estimator_session_review_package_{session_id}"):
                try:
                    edit_rows = build_edit_history_rows(feedback_baseline, edited_workbench, reason_map=reason_map)
                    edited_workbook_inputs_for_session = draft_workbook_inputs_for_ui(edited_workbench)
                    workbook_path_for_session = st.session_state.get(workbook_path_key)
                    session_export_cache_key = stable_payload_hash(
                        {
                            "session_id": session_id,
                            "edit_rows": edit_rows,
                            "draft_workbook_inputs": edited_workbook_inputs_for_session,
                            "totals": totals,
                            "workbook_path": workbook_path_for_session,
                        }
                    )
                    session_export_cache_state_key = f"estimator_session_review_package_export_cache_{session_id}"
                    session_package_path = cached_export_path_for_ui(
                        session_export_cache_state_key,
                        session_export_cache_key,
                        "session package export",
                    )
                    if session_package_path is None:
                        capture_estimator_session_event(
                            estimator_sessions.save_decision_edits,
                            session_id,
                            edit_rows,
                            edited_by="estimator",
                        )
                        capture_estimator_memory_candidates(
                            session_id,
                            edit_rows,
                            template_type=str(edited_scope.get("template_type") or ""),
                        )
                        capture_estimator_session_event(
                            estimator_sessions.save_final_decisions,
                            session_id,
                            final_decisions=estimator_sessions.final_decisions_from_workbench(edited_workbench),
                            calculated_outputs={
                                "totals": totals,
                                "draft_workbook_inputs": edited_workbook_inputs_for_session,
                            },
                            workbook_cell_writes=estimator_sessions.workbook_cell_writes_from_inputs(edited_workbook_inputs_for_session),
                            workbook_export_path=workbook_path_for_session,
                        )
                        with estimator_perf_step("session package export", cache_status="miss"):
                            session_package_path = estimator_sessions.export_estimator_session_package(
                                get_engine(),
                                session_id,
                                DEFAULT_WORKBENCH_EXPORT_DIR / f"estimator_session_{session_id}.zip",
                                include_full_payload=False,
                            )
                        store_export_path_for_ui(
                            session_export_cache_state_key,
                            session_export_cache_key,
                            session_package_path,
                        )
                    st.session_state[f"estimator_session_review_package_path_{session_id}"] = str(session_package_path)
                    st.session_state[f"estimator_session_review_package_bytes_{session_id}"] = cached_download_bytes(session_package_path)
                    st.success(f"Estimator session review package created: {session_package_path}")
                except Exception as exc:
                    logger.exception("Estimator session review package export failed")
                    st.error(f"Could not export session review package: {safe_exception_text(exc)}")
            session_package_value = st.session_state.get(f"estimator_session_review_package_path_{session_id}")
            if session_package_value:
                session_package_path = Path(session_package_value)
                if session_package_path.exists():
                    st.caption(f"Local session package path: {session_package_path}")
                    st.download_button(
                        "Download Session Review Package",
                        data=st.session_state.get(f"estimator_session_review_package_bytes_{session_id}")
                        or cached_download_bytes(session_package_path),
                        file_name=session_package_path.name,
                        mime="application/zip",
                        key=f"download_estimator_session_review_package_{session_id}",
                    )
        else:
            st.caption("Build a filled estimate template to start a persisted estimating session.")
        render_estimator_perf_timings()
        if debug_mode:
            with st.expander("Debug Evidence and Legacy Calibration", expanded=False):
                st.dataframe(pd.DataFrame([field_recommendation.historical_calibration]), use_container_width=True, hide_index=True)
                show_table(dataframe_from_records(field_recommendation.similar_examples), ["job_id", "customer", "job_name", "estimated_sqft", "estimated_value", "price_per_sqft", "estimate_file", "similarity_score", "reason_matched"], height=260)
                st.markdown("**Estimator Evidence Export**")
                if st.button("Export Estimator Evidence Package", key="export_field_notes_estimator_evidence"):
                    try:
                        export_paths = write_estimator_evidence_export(
                            field_recommendation,
                            data=data,
                            notes=recommendation_notes,
                            output_dir=Path("output/estimator_evidence"),
                            fast=True,
                            debug_evidence=True,
                        )
                        st.session_state["field_estimator_evidence_export_paths"] = {
                            key: str(path) for key, path in export_paths.items()
                        }
                        st.success("Estimator evidence package exported.")
                    except Exception as exc:
                        logger.exception("Field notes estimator evidence export failed")
                        st.error(f"Could not export estimator evidence package: {safe_exception_text(exc)}")
                evidence_paths = st.session_state.get("field_estimator_evidence_export_paths") or {}
                evidence_xlsx = Path(evidence_paths.get("xlsx", "")) if evidence_paths.get("xlsx") else None
                evidence_json = Path(evidence_paths.get("json", "")) if evidence_paths.get("json") else None
                if evidence_xlsx and evidence_xlsx.exists():
                    st.download_button(
                        "Download Estimator Evidence Workbook",
                        data=evidence_xlsx.read_bytes(),
                        file_name=evidence_xlsx.name,
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="download_field_notes_estimator_evidence_xlsx",
                    )
                if evidence_json and evidence_json.exists():
                    st.download_button(
                        "Download Estimator Evidence JSON",
                        data=evidence_json.read_bytes(),
                        file_name=evidence_json.name,
                        mime="application/json",
                        key="download_field_notes_estimator_evidence_json",
                    )

def repair_estimator_page() -> None:
    st.title("Repair Estimator")
    st.caption(
        "MVP for small repair calls. Uses VSimple repair history from repair_* tables, "
        "separate from the full roof coating/restoration estimator. Estimator review is required."
    )
    try:
        from jobscan.repair_estimator.estimator import estimate_repair_from_notes, write_repair_audit_package
    except Exception as exc:
        st.error("Repair estimator module is unavailable.")
        st.warning(safe_exception_text(exc))
        return

    try:
        repair_data = load_repair_history_cached()
    except Exception as exc:
        logger.exception("Repair estimator data load failed")
        st.error("Could not load repair estimator history from the database.")
        st.warning(safe_exception_text(exc))
        return

    with st.expander("Repair history data loaded", expanded=False):
        st.write(
            {
                "repair_jobs": len(repair_data.repair_jobs),
                "repair_material_usage": len(repair_data.repair_material_usage),
                "repair_labor_usage": len(repair_data.repair_labor_usage),
                "repair_scope_text": len(repair_data.repair_scope_text),
                "repair_outcomes": len(repair_data.repair_outcomes),
            }
        )

    sample_notes = {
        "Pipe boot leak": "Small active leak around one pipe boot on TPO roof. Easy access from roof hatch. Seal and reinforce with fabric if needed.",
        "Open seam": "Open seam on metal roof, about 12 linear feet, water entering after rain. Need clean, fabric, and sealant repair.",
        "Fasteners": "Seal exposed fasteners on standing seam metal roof. About 30 screws at edge condition. Standard access.",
        "Skylight curb": "Leak at skylight curb on coated roof. Inspect curb flashing, seal, and reinforce the corner.",
        "Emergency leak": "Emergency active roof leak over office area. Unknown roof type. Need same-day leak investigation and temporary patch.",
    }
    sample_cols = st.columns(len(sample_notes))
    for column, (label, sample) in zip(sample_cols, sample_notes.items()):
        if column.button(label, key=f"repair_sample_{label}"):
            st.session_state["repair_estimator_notes"] = sample

    notes = st.text_area(
        "Repair field notes",
        key="repair_estimator_notes",
        height=140,
        placeholder="Leak around pipe boot on TPO roof, one penetration, easy access, seal and reinforce with fabric.",
    )
    st.caption("Use this for small repair calls. For coating/restoration scopes, use the Estimating Assistant instead.")
    with st.expander("Optional context", expanded=True):
        r1, r2, r3 = st.columns(3)
        with r1:
            customer_job_name = st.text_input("Customer/job name", key="repair_customer_job_name")
            known_roof_type = st.selectbox(
                "Roof type override",
                ["", "metal", "tpo", "epdm", "modified_bitumen", "built_up", "shingle", "foam", "coated_roof"],
                key="repair_roof_type_override",
            )
        with r2:
            urgency = st.selectbox("Urgency", ["", "standard", "emergency"], key="repair_urgency_override")
            photos_link = st.text_input("Photos/link", key="repair_photos_link")
        with r3:
            known_status = st.text_input("Known status/notes", key="repair_status_notes")

    if st.button("Generate Repair Estimate", key="generate_repair_estimate"):
        if not notes.strip():
            st.warning("Enter repair notes first.")
        else:
            try:
                result = estimate_repair_from_notes(
                    notes,
                    repair_data,
                    overrides={
                        "roof_type": known_roof_type,
                        "urgency": urgency,
                        "customer_job_name": customer_job_name,
                        "photos_link": photos_link,
                        "status_notes": known_status,
                    },
                )
                st.session_state["repair_estimate_result"] = result.to_dict()
                st.session_state["repair_estimate_notes"] = notes
                st.session_state.pop("repair_estimate_audit_paths", None)
            except Exception as exc:
                logger.exception("Repair estimator failed")
                st.error("Repair estimator failed for this input.")
                st.warning(safe_exception_text(exc))

    result_payload = st.session_state.get("repair_estimate_result")
    if not result_payload:
        return
    if st.session_state.get("repair_estimate_notes") != notes:
        st.warning("The displayed repair estimate was generated from earlier notes. Click Generate Repair Estimate to refresh it.")

    metric_row(
        [
            ("Labor Target", f"{result_payload.get('estimated_labor_hours_target') or 0:,.1f} hrs"),
            ("Material Target", fmt_dollar(result_payload.get("estimated_material_cost_target"))),
            ("Invoice Target", fmt_dollar(result_payload.get("estimated_invoice_target"))),
            ("Confidence", str(result_payload.get("confidence") or "-").title()),
        ]
    )
    st.subheader("Parsed Repair Scope")
    st.dataframe(pd.DataFrame([result_payload.get("parsed_scope") or {}]), use_container_width=True, hide_index=True)
    if result_payload.get("review_flags"):
        st.warning("\n".join(result_payload.get("review_flags") or []))

    st.subheader("Estimate Range")
    range_df = pd.DataFrame(
        [
            {
                "bucket": "Labor hours",
                "low": result_payload.get("estimated_labor_hours_low"),
                "target": result_payload.get("estimated_labor_hours_target"),
                "high": result_payload.get("estimated_labor_hours_high"),
            },
            {
                "bucket": "Material cost",
                "low": result_payload.get("estimated_material_cost_low"),
                "target": result_payload.get("estimated_material_cost_target"),
                "high": result_payload.get("estimated_material_cost_high"),
            },
            {
                "bucket": "Invoice / price",
                "low": result_payload.get("estimated_invoice_low"),
                "target": result_payload.get("estimated_invoice_target"),
                "high": result_payload.get("estimated_invoice_high"),
            },
        ]
    )
    st.dataframe(range_df, use_container_width=True, hide_index=True)

    st.subheader("Likely Repair Packages")
    show_table(
        dataframe_from_records(result_payload.get("selected_repair_packages") or []),
        ["material_package", "selection_reason", "evidence_count", "median_total_cost", "common_material_names"],
        height=240,
    )

    st.subheader("Similar Historical Repairs")
    show_table(
        dataframe_from_records(result_payload.get("similar_repairs") or []),
        [
            "repair_id",
            "job_name",
            "customer",
            "status",
            "type_of_repair",
            "roof_type",
            "historical_labor_hours",
            "invoice_amount",
            "gross_profit",
            "url",
            "similarity_score",
            "reason_matched",
        ],
        height=360,
    )

    with st.expander("Evidence summary and matched profiles", expanded=False):
        st.dataframe(pd.DataFrame([result_payload.get("evidence_summary") or {}]), use_container_width=True, hide_index=True)
        show_table(
            dataframe_from_records(result_payload.get("matched_repair_profiles") or []),
            ["type_of_repair", "roof_type", "evidence_count", "median_labor_hours", "median_invoice_amount"],
            height=260,
        )

    st.subheader("Repair Estimate Audit Export")
    if st.button("Export Repair Audit Package", key="export_repair_estimator_audit"):
        try:
            from jobscan.repair_estimator.estimator import RepairEstimateResult

            audit_result = RepairEstimateResult(**result_payload)
            stem = re.sub(r"[^a-zA-Z0-9]+", "_", (customer_job_name or result_payload.get("parsed_scope", {}).get("issue_type") or "repair_estimate")).strip("_").lower()
            paths = write_repair_audit_package(audit_result, Path("output/repair_estimator/audit"), stem=stem or "repair_estimate")
            st.session_state["repair_estimate_audit_paths"] = {name: str(path) for name, path in paths.items()}
            st.success("Repair audit package exported.")
        except Exception as exc:
            logger.exception("Repair audit export failed")
            st.error(f"Could not export repair audit package: {safe_exception_text(exc)}")
    audit_paths = st.session_state.get("repair_estimate_audit_paths") or {}
    audit_json = Path(audit_paths.get("json", "")) if audit_paths.get("json") else None
    audit_xlsx = Path(audit_paths.get("xlsx", "")) if audit_paths.get("xlsx") else None
    if audit_json and audit_json.exists():
        st.download_button("Download Repair Audit JSON", audit_json.read_bytes(), audit_json.name, "application/json")
    if audit_xlsx and audit_xlsx.exists():
        st.download_button(
            "Download Repair Audit Workbook",
            audit_xlsx.read_bytes(),
            audit_xlsx.name,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


def raw_tables_page() -> None:
    st.title("Raw Tables")
    available_views = [view for view in VIEWS if relation_columns(view)]
    missing_views = [view for view in VIEWS if view not in available_views]
    if not available_views:
        show_empty("No dashboard views or raw tables are available.")
        return
    view_name = st.selectbox("View", available_views)
    if missing_views:
        st.caption(f"Hidden missing relations: {', '.join(missing_views[:6])}" + ("..." if len(missing_views) > 6 else ""))
    df = query_view(view_name)
    st.metric("Rows", fmt_count(len(df)))
    if df.empty:
        show_empty(f"{view_name} is empty.")
        return
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.download_button(
        "Download CSV",
        data=df.to_csv(index=False).encode("utf-8"),
        file_name=f"{view_name}.csv",
        mime="text/csv",
    )


def render_estimator_memory_admin() -> None:
    st.subheader("Estimator Memory")
    st.caption("Approved memory is exposed back to the Estimating Assistant chat. Answer-key learning is grouped into cue memories by default.")
    try:
        engine = get_engine()
        pending = estimator_memory_frame(engine, status="pending", limit=500)
        approved = estimator_memory_frame(engine, status="approved", limit=200)
    except Exception as exc:
        logger.exception("Estimator memory review load failed")
        st.warning(f"Estimator memory table is unavailable: {safe_exception_text(exc)}")
        return

    last_capture_status = st.session_state.get("estimator_memory_last_capture_status")
    if isinstance(last_capture_status, dict) and last_capture_status.get("message"):
        status_value = str(last_capture_status.get("status") or "")
        message = str(last_capture_status.get("message") or "")
        if status_value == "failed":
            st.warning(message)
        elif status_value in {"empty", "skipped"}:
            st.info(message)
        else:
            st.success(message)

    def _memory_ids(frame: pd.DataFrame) -> list[str]:
        if frame.empty or "memory_id" not in frame.columns:
            return []
        return [str(value) for value in frame["memory_id"].dropna().astype(str).tolist() if str(value).strip()]

    def _apply_memory_bulk_action(memory_ids: list[str], *, action: str, label: str) -> None:
        if not memory_ids:
            st.warning(f"No visible memory rows to {label.lower()}.")
            return
        try:
            if action == "delete":
                changed_count = delete_estimator_memory(get_engine(), memory_ids)
            else:
                changed_count = update_estimator_memory_status(
                    get_engine(),
                    memory_ids,
                    status=action,
                    approved_by="streamlit_admin",
                )
            clear_estimator_data_caches()
            st.success(f"{label} {changed_count:,} estimator memory item(s).")
            st.rerun()
        except Exception as exc:
            logger.exception("Estimator memory bulk action failed")
            st.error(f"Could not update estimator memory: {safe_exception_text(exc)}")

    st.metric("Pending Memory Candidates", fmt_count(len(pending)))
    if pending.empty:
        st.caption("No pending estimator memory candidates.")
    else:
        pending_ids = _memory_ids(pending)
        bulk_cols = st.columns(3)
        if bulk_cols[0].button("Approve All Visible Pending", key="approve_all_visible_pending_memory"):
            _apply_memory_bulk_action(pending_ids, action="approved", label="Approved")
        if bulk_cols[1].button("Disable All Visible Pending", key="disable_all_visible_pending_memory"):
            _apply_memory_bulk_action(pending_ids, action="disabled", label="Disabled")
        if bulk_cols[2].button("Delete All Visible Pending", key="delete_all_visible_pending_memory"):
            _apply_memory_bulk_action(pending_ids, action="delete", label="Deleted")
        review_rows = pending.copy()
        review_rows.insert(0, "approve", False)
        review_rows.insert(1, "disable", False)
        review_rows.insert(2, "delete", False)
        visible_columns = [
            "approve",
            "disable",
            "delete",
            "priority",
            "template_type",
            "template_bucket",
            "decision_id",
            "guidance",
            "rationale",
            "source_type",
            "memory_id",
        ]
        visible_columns = [column for column in visible_columns if column in review_rows.columns]
        edited = st.data_editor(
            review_rows[visible_columns],
            width="stretch",
            hide_index=True,
            num_rows="fixed",
            key="estimator_memory_pending_review",
            column_config={
                "approve": "Approve",
                "disable": "Disable",
                "delete": "Delete",
                "template_type": "Template",
                "template_bucket": "Bucket",
                "decision_id": "Decision",
                "guidance": st.column_config.TextColumn("Guidance", width="large"),
                "memory_id": st.column_config.TextColumn("Memory ID", width="small"),
            },
            disabled=[column for column in visible_columns if column not in {"approve", "disable", "delete"}],
        )
        if st.button("Apply Estimator Memory Review", key="apply_estimator_memory_review"):
            edited_rows = edited.to_dict(orient="records")
            approve_ids = [str(row.get("memory_id")) for row in edited_rows if row.get("approve")]
            disable_ids = [str(row.get("memory_id")) for row in edited_rows if row.get("disable") and not row.get("approve") and not row.get("delete")]
            delete_ids = [str(row.get("memory_id")) for row in edited_rows if row.get("delete") and not row.get("approve")]
            try:
                approved_count = update_estimator_memory_status(
                    get_engine(),
                    approve_ids,
                    status="approved",
                    approved_by="streamlit_admin",
                )
                disabled_count = update_estimator_memory_status(
                    get_engine(),
                    disable_ids,
                    status="disabled",
                    approved_by="streamlit_admin",
                )
                deleted_count = delete_estimator_memory(get_engine(), delete_ids)
                clear_estimator_data_caches()
                st.success(
                    f"Approved {approved_count:,}, disabled {disabled_count:,}, and deleted {deleted_count:,} memory candidate(s)."
                )
                st.rerun()
            except Exception as exc:
                logger.exception("Estimator memory review update failed")
                st.error(f"Could not update estimator memory: {safe_exception_text(exc)}")

    with st.expander("Approved estimator memory", expanded=False):
        if approved.empty:
            st.caption("No approved estimator memory yet.")
        else:
            approved_ids = _memory_ids(approved)
            approved_cols = st.columns(2)
            if approved_cols[0].button("Disable All Visible Approved", key="disable_all_visible_approved_memory"):
                _apply_memory_bulk_action(approved_ids, action="disabled", label="Disabled")
            if approved_cols[1].button("Delete All Visible Approved", key="delete_all_visible_approved_memory"):
                _apply_memory_bulk_action(approved_ids, action="delete", label="Deleted")
            approved_review_rows = approved.copy()
            approved_review_rows.insert(0, "disable", False)
            approved_review_rows.insert(1, "delete", False)
            approved_columns = [
                column
                for column in [
                    "disable",
                    "delete",
                    "priority",
                    "template_type",
                    "template_bucket",
                    "decision_id",
                    "guidance",
                    "source_type",
                    "updated_at",
                    "memory_id",
                ]
                if column in approved_review_rows.columns
            ]
            edited_approved = st.data_editor(
                approved_review_rows[approved_columns],
                width="stretch",
                hide_index=True,
                num_rows="fixed",
                key="estimator_memory_approved_review",
                column_config={
                    "disable": "Disable",
                    "delete": "Delete",
                    "template_type": "Template",
                    "template_bucket": "Bucket",
                    "decision_id": "Decision",
                    "guidance": st.column_config.TextColumn("Guidance", width="large"),
                    "memory_id": st.column_config.TextColumn("Memory ID", width="small"),
                },
                disabled=[column for column in approved_columns if column not in {"disable", "delete"}],
            )
            if st.button("Apply Approved Memory Changes", key="apply_approved_estimator_memory_review"):
                edited_rows = edited_approved.to_dict(orient="records")
                disable_ids = [str(row.get("memory_id")) for row in edited_rows if row.get("disable") and not row.get("delete")]
                delete_ids = [str(row.get("memory_id")) for row in edited_rows if row.get("delete")]
                try:
                    disabled_count = update_estimator_memory_status(
                        get_engine(),
                        disable_ids,
                        status="disabled",
                        approved_by="streamlit_admin",
                    )
                    deleted_count = delete_estimator_memory(get_engine(), delete_ids)
                    clear_estimator_data_caches()
                    st.success(f"Disabled {disabled_count:,} and deleted {deleted_count:,} approved memory item(s).")
                    st.rerun()
                except Exception as exc:
                    logger.exception("Approved estimator memory update failed")
                    st.error(f"Could not update approved estimator memory: {safe_exception_text(exc)}")


def admin_health_page() -> None:
    st.title("Admin / Health")
    st.caption("Read-only operational checks for scanner, extraction, parser, estimator data, and relationship profiler outputs.")

    if st.button("Refresh health checks"):
        load_admin_health_snapshot.clear()
        health_table_exists.clear()
        health_table_columns.clear()
        st.rerun()

    snapshot = load_admin_health_snapshot()

    connection_status = "OK" if snapshot.get("connection_ok") else "Unavailable"
    connection_help = None if snapshot.get("connection_ok") else snapshot.get("connection_error")
    st.metric("Database Connection", connection_status, help=connection_help)
    render_database_target_debug()

    if not snapshot.get("connection_ok"):
        st.warning("Database connection is unavailable. Other health checks could not run.")
        return

    warnings = snapshot.get("warnings") or []
    if warnings:
        st.subheader("Warnings")
        for warning in warnings:
            st.warning(warning)
    else:
        st.success("No health warnings triggered.")

    row_counts = snapshot.get("row_counts")
    if isinstance(row_counts, pd.DataFrame) and not row_counts.empty:
        st.subheader("Core Table Row Counts")
        metric_columns = st.columns(3)
        for index, row in row_counts.iterrows():
            exists = bool(row.get("exists"))
            count_value = row.get("row_count")
            label = str(row.get("table"))
            help_text = str(row.get("label") or "")
            value = fmt_count(int(count_value)) if exists and pd.notna(count_value) else "Missing"
            metric_columns[index % 3].metric(label, value, help=help_text)

        display_columns = [column for column in ["table", "label", "exists", "row_count", "current_rows"] if column in row_counts.columns]
        st.dataframe(row_counts[display_columns], use_container_width=True, hide_index=True)

    extraction_counts = snapshot.get("extraction_status_counts")
    if isinstance(extraction_counts, pd.DataFrame) and not extraction_counts.empty:
        st.subheader("Document Extraction Status")
        st.dataframe(extraction_counts, use_container_width=True, hide_index=True)
    elif health_table_exists("documents"):
        st.info("documents table exists, but extraction_status is unavailable or has no values.")

    template_counts = snapshot.get("template_type_counts")
    if isinstance(template_counts, pd.DataFrame) and not template_counts.empty:
        st.subheader("Estimate Template Types")
        st.dataframe(template_counts, use_container_width=True, hide_index=True)
    elif health_table_exists("estimate_template_rows"):
        st.info("estimate_template_rows exists, but template_type is unavailable or has no values.")

    recent_docs = snapshot.get("recent_problem_documents")
    if isinstance(recent_docs, pd.DataFrame) and not recent_docs.empty:
        st.subheader("Recent Failed / Pending Documents")
        st.dataframe(recent_docs, use_container_width=True, hide_index=True)
    else:
        st.subheader("Recent Failed / Pending Documents")
        st.caption("No failed or pending documents found, or extraction_status is unavailable.")

    timestamp_rows = snapshot.get("timestamp_rows")
    if isinstance(timestamp_rows, pd.DataFrame) and not timestamp_rows.empty:
        st.subheader("Latest Activity Timestamps")
        st.dataframe(timestamp_rows, use_container_width=True, hide_index=True)

    query_errors = snapshot.get("query_errors") or []
    if query_errors:
        with st.expander("Health query diagnostics"):
            st.dataframe(pd.DataFrame(query_errors), use_container_width=True, hide_index=True)

    render_estimator_memory_admin()


def main() -> None:
    database_startup_error: Exception | None = None
    try:
        jobs_for_filters = load_sidebar_filter_jobs()
    except Exception as exc:
        jobs_for_filters = pd.DataFrame()
        database_startup_error = exc

    with st.sidebar:
        render_database_target_debug()
        filters = sidebar_filters(jobs_for_filters)
        core_pages = [
            "Owner Overview",
            "Sales Dashboard",
            "Operations Dashboard",
            "Job Board",
            "Timesheet Job Touches",
            "Schedule Calendar",
            "Estimating Assistant",
            "Pricing Catalog",
            "Ask Spray-Tec",
            "BidScope AI",
            "Admin / Health",
        ]
        legacy_pages = [
            "Pipeline / Money",
            "Sales Follow-Up",
            "Contracted Backlog / Scheduling",
            "Project Scheduling",
            "Daily Crew Dispatch",
            "Jobs Needing Action",
            "Closeout / Billing Risk",
            "Documentation Risk",
            "Job Warnings",
            "Estimate Analytics",
            "Estimate Quality Issues",
            "Line Item Analysis",
            "Estimate Adders",
            "STAMP Tracking",
            "Raw Tables",
        ]
        show_legacy_pages = st.checkbox("Show legacy/raw dashboard pages", value=False)
        show_perf_timings = st.checkbox("Show performance timings", value=False, key="show_dashboard_perf_timings")
        page_options = core_pages + (legacy_pages if show_legacy_pages else [])
        page = st.radio(
            "Page",
            page_options,
        )

    if database_startup_error and page not in {"Estimating Assistant", "Admin / Health"}:
        show_database_error(database_startup_error)
        st.stop()

    if page == "Owner Overview":
        owner_overview_page()
    elif page == "Ask Spray-Tec":
        ask_spraytec_page()
    elif page == "Job Board":
        job_board_page()
    elif page == "Timesheet Job Touches":
        timesheet_job_touches_page()
    elif page == "Sales Dashboard":
        sales_dashboard_page()
    elif page == "Operations Dashboard":
        operations_dashboard_page()
    elif page == "Schedule Calendar":
        schedule_calendar_page()
    elif page == "Estimating Assistant":
        estimator_prototype_page()
    elif page == "BidScope AI":
        render_foamscope_page()
    elif page == "Admin / Health":
        admin_health_page()
    elif page == "Pipeline / Money":
        pipeline_money_page()
    elif page == "Sales Follow-Up":
        sales_followup_page()
    elif page == "Contracted Backlog / Scheduling":
        contracted_backlog_scheduling_page()
    elif page == "Project Scheduling":
        project_scheduling_page()
    elif page == "Daily Crew Dispatch":
        daily_crew_dispatch_page()
    elif page == "Jobs Needing Action":
        jobs_needing_action_page()
    elif page == "Closeout / Billing Risk":
        closeout_billing_risk_page()
    elif page == "Documentation Risk":
        documentation_risk_page()
    elif page == "Job Warnings":
        job_warnings_page()
    elif page == "Estimate Analytics":
        estimate_analytics_page()
    elif page == "Estimate Quality Issues":
        estimate_quality_issues_page()
    elif page == "Line Item Analysis":
        line_item_analysis_page()
    elif page == "Estimate Adders":
        estimate_adders_page()
    elif page == "STAMP Tracking":
        stamp_tracking_page()
    elif page == "Pricing Catalog":
        pricing_catalog_page()
    else:
        raw_tables_page()


if __name__ == "__main__":
    main()
