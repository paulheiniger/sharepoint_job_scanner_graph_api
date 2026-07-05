from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

import hashlib
import json
import logging
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import text
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
from jobscan.job_search import (
    get_preferred_job_documents,
    interpret_search_request,
    requested_document_label,
    search_jobs,
)
try:
    from jobscan.estimator import estimate_from_field_notes, load_estimator_data
except ImportError:
    from jobscan.estimator import load_estimator_data

    estimate_from_field_notes = None
from jobscan.estimator.schemas import EstimatorData
from jobscan.estimator.evidence_export import write_estimator_evidence_export
from jobscan.estimator import session_capture as estimator_sessions
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

try:
    from streamlit_calendar import calendar
except ImportError:
    calendar = None


logger = logging.getLogger(__name__)

DEFAULT_DATABASE_URL = "postgresql+psycopg2://spraytec:spraytec_dev_password@127.0.0.1:5433/spraytec_ops"
DECISION_EVIDENCE_DISPLAY_COLUMNS = [
    "decision_evidence_summary",
    "proposal_source",
    "proposal_confidence",
    "proposal_review_required",
    "proposal_review_reasons",
]

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
    "decision_evidence_count",
    "decision_confidence",
    *DECISION_EVIDENCE_DISPLAY_COLUMNS,
    "product_guidance",
    "product_warning_summary",
    "row_traceability",
    "notes",
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
    "historical_selector_recommendation",
    "historical_selector_evidence_count",
    *DECISION_EVIDENCE_DISPLAY_COLUMNS,
    "basis_sqft",
    "thickness_inches",
    "yield_or_coverage",
    "unit_price",
    "estimated_units",
    "estimated_sets",
    "estimated_cost",
    "selected_pricing_candidate",
    "compatibility_status",
    "compatibility_warnings",
    *DECISION_EVIDENCE_DISPLAY_COLUMNS,
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
    "compatibility_warnings",
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
        "basis_sqft",
        "linear_ft",
        "quantity",
        "feet_per_unit",
        "unit_price",
        "estimated_units",
        "estimated_cost",
        "selected_pricing_candidate",
        "compatibility_status",
        "compatibility_warnings",
        "product_guidance",
        "notes",
    ],
    "insulation_thermal_barrier_template_decisions": [
        "include",
        "workbook_row",
        "template_line",
        "editable_selector_code",
        "resolved_template_option",
        "basis_sqft",
        "gal_per_100_sqft",
        "waste_factor_pct",
        "unit_price",
        "estimated_gallons",
        "estimated_cost",
        "selected_pricing_candidate",
        "compatibility_status",
        "compatibility_warnings",
        "product_guidance",
        "notes",
    ],
    "insulation_support_material_template_decisions": [
        "include",
        "workbook_row",
        "template_line",
        "editable_selector_code",
        "resolved_template_option",
        "quantity",
        "estimated_drums",
        "unit_price",
        "estimated_units",
        "estimated_cost",
        "selected_pricing_candidate",
        "compatibility_status",
        "compatibility_warnings",
        "product_guidance",
        "notes",
    ],
    "insulation_equipment_logistics_template_decisions": [
        "include",
        "workbook_row",
        "template_line",
        "editable_selector_code",
        "resolved_template_option",
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
        "compatibility_warnings",
        "notes",
    ],
    "insulation_compliance_template_decisions": [
        "include",
        "workbook_row",
        "template_line",
        "resolved_template_option",
        "quantity",
        "unit_price",
        "estimated_units",
        "estimated_cost",
        "compatibility_status",
        "compatibility_warnings",
        "notes",
    ],
    "insulation_labor_template_decisions": [
        "include",
        "workbook_row",
        "labor_task",
        "days",
        "crew_size",
        "daily_rate",
        "hourly_rate",
        "total_hours",
        "formula_mode",
        "estimated_cost",
        "compatibility_status",
        "compatibility_warnings",
        "notes",
    ],
    "insulation_pricing_template_decisions": [
        "include",
        "workbook_row",
        "template_line",
        "resolved_template_option",
        "quantity",
        "unit_price",
        "margin_pct",
        "estimated_cost",
        "compatibility_status",
        "compatibility_warnings",
        "notes",
    ],
}

INSULATION_DECISION_SECTIONS = [
    ("insulation_detail_material_template_decisions", "Insulation Detail Materials"),
    ("insulation_thermal_barrier_template_decisions", "Insulation Thermal Barrier / Coating"),
    ("insulation_support_material_template_decisions", "Insulation Support Materials"),
    ("insulation_equipment_logistics_template_decisions", "Insulation Equipment / Logistics"),
    ("insulation_compliance_template_decisions", "Insulation Compliance"),
    ("insulation_labor_template_decisions", "Insulation Labor Planning"),
    ("insulation_pricing_template_decisions", "Insulation Pricing"),
]

ROOFING_FOAM_TEMPLATE_COMPACT_COLUMNS = [
    "include",
    "workbook_row",
    "editable_selector_code",
    "resolved_template_option",
    "historical_selector_recommendation",
    "historical_selector_evidence_count",
    *DECISION_EVIDENCE_DISPLAY_COLUMNS,
    "basis_sqft",
    "thickness_inches",
    "yield_or_coverage",
    "unit_price",
    "estimated_units",
    "estimated_sets",
    "estimated_cost",
    "selected_pricing_candidate",
    "compatibility_status",
    "compatibility_warnings",
    "product_guidance",
    "notes",
]

ROOFING_COATING_TEMPLATE_COMPACT_COLUMNS = [
    "include",
    "workbook_row",
    "editable_selector_code",
    "resolved_template_option",
    "historical_selector_recommendation",
    "historical_selector_evidence_count",
    *DECISION_EVIDENCE_DISPLAY_COLUMNS,
    "basis_sqft",
    "gal_per_100_sqft",
    "waste_factor_pct",
    "wet_mils_estimate",
    "unit_price",
    "estimated_gallons",
    "estimated_cost",
    "selected_pricing_candidate",
    "compatibility_status",
    "compatibility_warnings",
    "product_guidance",
    "notes",
]

ROOFING_PRIMER_TEMPLATE_COMPACT_COLUMNS = [
    "include",
    "workbook_row",
    "editable_selector_code",
    "resolved_template_option",
    "historical_selector_recommendation",
    "historical_selector_evidence_count",
    *DECISION_EVIDENCE_DISPLAY_COLUMNS,
    "basis_sqft",
    "coverage_sqft_per_unit",
    "unit_price",
    "estimated_units",
    "estimated_cost",
    "selected_pricing_candidate",
    "compatibility_status",
    "compatibility_warnings",
    "product_guidance",
    "notes",
]

ROOFING_DETAIL_TEMPLATE_COMPACT_COLUMNS = [
    "include",
    "workbook_row",
    "editable_selector_code",
    "resolved_template_option",
    "historical_selector_recommendation",
    "historical_selector_evidence_count",
    *DECISION_EVIDENCE_DISPLAY_COLUMNS,
    "units",
    "linear_ft",
    "unit_price",
    "estimated_units",
    "estimated_cost",
    "selected_pricing_candidate",
    "compatibility_status",
    "compatibility_warnings",
    "product_guidance",
    "notes",
]

ROOFING_DETAIL_QUANTITY_TEMPLATE_COMPACT_COLUMNS = [
    "include",
    "workbook_row",
    "resolved_template_option",
    "linear_ft",
    "units",
    "estimated_units",
    "amount",
    "estimated_cost",
    "compatibility_status",
    "compatibility_warnings",
    *DECISION_EVIDENCE_DISPLAY_COLUMNS,
    "notes",
]

ROOFING_BOARD_FASTENER_TEMPLATE_COMPACT_COLUMNS = [
    "include",
    "workbook_row",
    "editable_selector_code",
    "resolved_template_option",
    "historical_selector_recommendation",
    "historical_selector_evidence_count",
    *DECISION_EVIDENCE_DISPLAY_COLUMNS,
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
    "compatibility_warnings",
    "product_guidance",
    "notes",
]

ROOFING_GRANULES_TEMPLATE_COMPACT_COLUMNS = [
    "include",
    "workbook_row",
    "editable_selector_code",
    "resolved_template_option",
    "historical_selector_recommendation",
    "historical_selector_evidence_count",
    *DECISION_EVIDENCE_DISPLAY_COLUMNS,
    "basis_sqft",
    "coverage_lbs_per_100_sqft",
    "bag_weight_lbs",
    "unit_price",
    "estimated_units",
    "estimated_cost",
    "selected_pricing_candidate",
    "compatibility_status",
    "compatibility_warnings",
    "product_guidance",
    "notes",
]

ROOFING_EQUIPMENT_TEMPLATE_COMPACT_COLUMNS = [
    "include",
    "workbook_row",
    "editable_selector_code",
    "resolved_template_option",
    "historical_selector_recommendation",
    "historical_selector_evidence_count",
    *DECISION_EVIDENCE_DISPLAY_COLUMNS,
    "basis_sqft",
    "thickness_inches",
    "size",
    "period",
    "days",
    "unit_price",
    "margin_pct",
    "estimated_units",
    "estimated_cost",
    "compatibility_status",
    "compatibility_warnings",
    *DECISION_EVIDENCE_DISPLAY_COLUMNS,
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
    "compatibility_warnings",
    *DECISION_EVIDENCE_DISPLAY_COLUMNS,
    "notes",
]

ROOFING_ACCESSORY_TEMPLATE_COMPACT_COLUMNS = [
    "include",
    "workbook_row",
    "editable_selector_code",
    "resolved_template_option",
    "total_coating_gallons",
    "linear_ft",
    "estimated_units",
    "amount",
    "unit_price",
    "estimated_cost",
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
    "historical_selector_evidence_count",
    "decision_confidence",
    *DECISION_EVIDENCE_DISPLAY_COLUMNS,
    "compatibility_status",
    "compatibility_warnings",
    "notes",
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
    "decision_evidence_count",
    "decision_confidence",
    *DECISION_EVIDENCE_DISPLAY_COLUMNS,
    "row_traceability",
    "notes",
]

ADDER_WORKBENCH_COMPACT_COLUMNS = [
    "include",
    "workbook_row",
    "adder",
    "editable_value",
    "evidence_count",
    "confidence",
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
    "Roof repair": "Roof repair, about 3,000 sqft, leaks around penetrations, rusted metal panels, Louisville KY, difficult access.",
    "Wall insulation": "Wall insulation, wall area 10,000 sqft, metal building, 2 inch spray foam, Cincinnati OH.",
}

ESTIMATE_TYPE_AUTO = "Auto-detect"
ESTIMATE_TYPE_RESTORATION = "Roof Restoration / Coating"
ESTIMATE_TYPE_REPAIR = "Roof Repair"
ESTIMATE_TYPE_INSULATION = "Insulation"
ESTIMATE_TYPE_OPTIONS = [
    ESTIMATE_TYPE_AUTO,
    ESTIMATE_TYPE_RESTORATION,
    ESTIMATE_TYPE_REPAIR,
    ESTIMATE_TYPE_INSULATION,
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
    }


def pricing_export_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    return df[[column for column in PRICING_EXPORT_COLUMNS if column in df.columns]].copy()


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
    fig = px.bar(chart_df, x=x, y=y, color=color if color in chart_df.columns else None, title=title, labels=labels)
    st.plotly_chart(fig, use_container_width=True)


def show_table(
    df: pd.DataFrame,
    columns: Iterable[str] | None = None,
    height: int = 450,
    *,
    sort_by: str | None = None,
    n: int | None = None,
) -> None:
    table_df = with_folder_link(df)
    requested_columns = list(columns) if columns is not None else list(table_df.columns)
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
    st.dataframe(table_df[available], use_container_width=True, hide_index=True, height=height)


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
    interpreted = interpret_search_request(prompt)
    debug_payload: dict[str, Any] = {"interpreted": interpreted, "ranked_matches": []}
    response = ""

    if interpreted.get("is_follow_up") and selected_job:
        requested_type = interpreted.get("document_type")
        try:
            with get_engine().connect() as conn:
                docs = get_preferred_job_documents(conn, selected_job, requested_type)
        except Exception as exc:
            show_database_error(exc)
            return
        response = f"Using selected job: **{text_value(selected_job.get('job_name')) or text_value(selected_job.get('customer'))}**\n\n"
        if requested_type not in (None, "all") and not any(doc.get("type") == requested_type for doc in docs):
            response += f"{requested_document_label(requested_type)}: not indexed\n\n"
        if docs:
            response += "\n".join(
                f"- {doc['label']}: {markdown_link(text_value(doc.get('file_name')) or 'Open ' + doc['label'].lower(), doc['url'])}"
                for doc in docs
            )
        else:
            response += "I do not see any indexed document links for the selected job."
        debug_payload["ranked_matches"] = [{"job_id": selected_job.get("job_id"), "score": selected_job.get("match_score"), "reason": selected_job.get("match_reason")}]
    else:
        try:
            with get_engine().connect() as conn:
                results = search_jobs(conn, prompt, limit=10)
                for result in results:
                    result["_documents"] = get_preferred_job_documents(conn, result, interpreted.get("document_type"))
        except Exception as exc:
            show_database_error(exc)
            return
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
        strong_results = [result for result in results if float(result.get("match_score") or 0) >= 45]
        display_results = strong_results or results[:5]
        if not display_results:
            response = "No confident match was found. Try adding a customer name, location, division, or approximate year."
        elif len(strong_results) == 1 and float(strong_results[0].get("match_score") or 0) >= 75:
            job = strong_results[0]
            st.session_state["ask_spraytec_selected_job"] = job
            st.session_state["ask_spraytec_selected_job_id"] = str(job.get("job_id") or "")
            response = "I found a strong match.\n\n" + job_result_markdown(job, interpreted, connection=None)
            alternatives = results[1:4]
            if alternatives:
                response += "\n\nLower-ranked alternatives are available in Search details."
        else:
            if strong_results:
                response = f"I found {len(strong_results)} possible matches. The strongest results are:\n\n"
            else:
                response = "No confident match was found, but these weaker suggestions may help:\n\n"
            chunks = []
            for index, job in enumerate(display_results[:5], start=1):
                chunks.append(f"{index}. " + job_result_markdown(job, interpreted, connection=None))
            response += "\n\n".join(chunks)
            if display_results:
                job = display_results[0]
                st.session_state["ask_spraytec_selected_job"] = job
                st.session_state["ask_spraytec_selected_job_id"] = str(job.get("job_id") or "")
            if not strong_results:
                response += "\n\nTry adding customer, location, division, or approximate year to narrow this down."

    st.session_state["ask_spraytec_messages"].append({"role": "assistant", "content": response})
    with st.chat_message("assistant"):
        st.markdown(response)
        with st.expander("Search details"):
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
            st.write(debug_payload["ranked_matches"])


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


def load_job_board_df() -> pd.DataFrame:
    jobs = load_job_board_jobs()
    if jobs.empty or "job_id" not in jobs.columns:
        return jobs
    jobs = with_folder_link(jobs)
    overrides = load_job_workflow_overrides()
    if "job_id" in overrides.columns:
        jobs = jobs.merge(overrides, on="job_id", how="left")
    schedule = load_job_board_schedule()
    if not schedule.empty and "job_id" in schedule.columns:
        jobs = jobs.merge(schedule, on="job_id", how="left")
    warnings = load_job_board_warnings()
    if not warnings.empty and "job_id" in warnings.columns:
        jobs = jobs.merge(warnings, on="job_id", how="left")
    if "warning_count" not in jobs.columns:
        jobs["warning_count"] = jobs["warnings"].fillna("").astype(str).str.strip().ne("").astype(int) if "warnings" in jobs.columns else 0
    if "warning_summary" not in jobs.columns:
        jobs["warning_summary"] = jobs["warnings"] if "warnings" in jobs.columns else ""
    return jobs


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
        "folder created": "Folder Created",
    }
    return mapping.get(key, raw if raw else "Other")


def board_status_for_row(row: pd.Series) -> str:
    workflow_value = first_existing_value(row, POSSIBLE_WORKFLOW_STATUS_COLS)
    pipeline_value = first_existing_value(row, POSSIBLE_PIPELINE_STATUS_COLS)
    status_value = first_existing_value(row, POSSIBLE_STATUS_COLS)
    raw_status = first_nonblank(workflow_value, pipeline_value, status_value)
    return normalize_board_status(raw_status)


def bool_label(value: object) -> str:
    return "Yes" if bool(value) else "No"


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
    ]:
        if column not in jobs.columns:
            jobs[column] = None

    jobs["job_id"] = jobs["job_id"].fillna("").astype(str)
    jobs["board_status"] = jobs.apply(board_status_for_row, axis=1)
    selected_job_id = str(st.session_state.get("selected_job_board_job_id", "") or "")
    if selected_job_id:
        st.caption(f"Selected job_id: {selected_job_id}")

    with st.expander("Job Board status debug"):
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
            ("Warnings / Action Items", fmt_count((numeric_series(filtered, "warning_count").fillna(0) > 0).sum())),
        ]
    )

    available_statuses = list(jobs["board_status"].dropna().unique())
    ordered_statuses = [status for status in JOB_BOARD_STATUS_ORDER if status in available_statuses]
    ordered_statuses.extend(sorted(status for status in available_statuses if status not in JOB_BOARD_STATUS_ORDER))
    existing_columns = [status for status in ordered_statuses if status in set(filtered["board_status"])]
    if not existing_columns and not filtered.empty:
        existing_columns = ["Other"] if "Other" in ordered_statuses else ordered_statuses[:1]
    selected_board_columns = st.multiselect(
        "Board columns",
        ordered_statuses,
        default=existing_columns,
        key="job_board_columns",
    )

    if not selected_board_columns:
        st.info("Select at least one board column.")
        return

    st.subheader("Pipeline Board")
    board_columns = st.columns(len(selected_board_columns))
    for board_status, column in zip(selected_board_columns, board_columns):
        column_df = filtered[filtered["board_status"] == board_status].sort_values("estimated_value", ascending=False, na_position="last")
        with column:
            st.markdown(f"**{board_status}**")
            st.caption(f"{len(column_df):,} jobs | {fmt_dollar(safe_sum(column_df, 'estimated_value'))}")
            for row_index, row in column_df.iterrows():
                job_id = str(row.get("job_id") or "")
                if not job_id:
                    continue
                with st.container(border=True):
                    title = text_value(row.get("job_name")) or text_value(row.get("customer")) or "Untitled job"
                    customer = text_value(row.get("customer"))
                    st.markdown(f"**{title}**")
                    if customer and customer != title:
                        st.caption(customer)
                    badge_parts = [text_value(row.get("division")), text_value(row.get("job_type"))]
                    st.caption(" / ".join(part for part in badge_parts if part) or "No division / type")
                    st.write(format_summary_value(row.get("estimated_value"), kind="money"))
                    st.caption(f"Board: {text_value(row.get('board_status')) or 'Other'}")
                    pipeline_status = text_value(row.get("pipeline_status"))
                    row_status_value = text_value(row.get("status"))
                    if pipeline_status:
                        st.caption(f"Pipeline: {pipeline_status}")
                    if row_status_value and row_status_value != pipeline_status:
                        st.caption(f"Status: {row_status_value}")
                    workflow_priority = text_value(row.get("priority"))
                    if workflow_priority:
                        st.caption(f"Priority: {workflow_priority}")
                    crew = text_value(row.get("assigned_crew_leader"))
                    if crew:
                        st.caption(f"Crew: {crew}")
                    start = text_value(row.get("estimated_start_date"))
                    duration = text_value(row.get("estimated_duration_days"))
                    if start or duration:
                        st.caption(f"Schedule: {start or 'TBD'} | {duration or '-'} days")
                    indicators: list[str] = []
                    if pd.to_numeric(pd.Series([row.get("warning_count")]), errors="coerce").fillna(0).iloc[0] > 0 or text_value(row.get("warnings")):
                        indicators.append("Action")
                    if bool(row.get("has_aerial")):
                        indicators.append("Aerial")
                    photo_count = pd.to_numeric(pd.Series([row.get("photo_count")]), errors="coerce").fillna(0).iloc[0]
                    if photo_count:
                        indicators.append(f"{int(photo_count)} photos")
                    if indicators:
                        st.caption(" | ".join(indicators))
                    safe_job_id = hashlib.sha1(job_id.encode("utf-8")).hexdigest()[:12]
                    button_key = f"open_job_board_{safe_job_id}_{row_index}"
                    if st.button("Open", key=button_key):
                        st.session_state["selected_job_board_job_id"] = job_id
                        st.rerun()

    if selected_job_id:
        selected_rows = jobs[jobs["job_id"].astype(str) == selected_job_id]
        if selected_rows.empty:
            st.warning("Selected job was not found in the current job data. It may be hidden by filters.")
            if st.button("Clear selected job", key="clear_selected_job_board_job"):
                del st.session_state["selected_job_board_job_id"]
                st.rerun()
            return
        row = selected_rows.iloc[0]
        st.divider()
        st.header("Job Detail")
        detail_cols = st.columns(3)
        detail_items = [
            ("Job Name", row.get("job_name"), "text"),
            ("Customer", row.get("customer"), "text"),
            ("Division", row.get("division"), "text"),
            ("Workflow Status", row.get("workflow_status") or row.get("pipeline_status") or row.get("status"), "text"),
            ("Priority", row.get("priority"), "text"),
            ("Follow Up Date", row.get("follow_up_date"), "text"),
            ("Deal Owner", row.get("deal_owner"), "text"),
            ("Assigned User", row.get("assigned_user"), "text"),
            ("Pipeline Status", row.get("pipeline_status"), "text"),
            ("Status", row.get("status"), "text"),
            ("Job Type", row.get("job_type"), "text"),
            ("Address", " ".join(part for part in [text_value(row.get("site_address")), text_value(row.get("city")), text_value(row.get("state")), text_value(row.get("zip_code"))] if part), "text"),
            ("Estimated Value", row.get("estimated_value"), "money"),
            ("Estimated Sq Ft", row.get("estimated_sqft"), "number"),
            ("Price / Sq Ft", row.get("price_per_sqft"), "money"),
            ("Final Price", row.get("final_price"), "money"),
            ("Invoice Amount", row.get("invoice_amount"), "money"),
            ("Assigned Crew Leader", row.get("assigned_crew_leader"), "text"),
            ("Scheduled Start", row.get("estimated_start_date"), "text"),
            ("Scheduled End", row.get("estimated_end_date"), "text"),
            ("Duration Days", row.get("estimated_duration_days"), "number"),
            ("Labor Hours", row.get("estimated_labor_hours"), "number"),
            ("Crew Size", row.get("estimated_crew_size"), "number"),
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

    st.caption(f"Showing {fmt_count(len(pricing))} pricing rows")
    if pricing.empty:
        show_empty("No pricing rows match the current filters.")
    else:
        show_table(
            pricing,
            [
                "product_name",
                "vendor",
                "category",
                "unit_price",
                "unit_of_measure",
                "package_size",
                "price_basis",
                "price_per_gallon",
                "effective_date",
                "status",
                "needs_review",
                "source_file",
                "source_type",
                "notes",
            ],
            height=560,
        )
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


@st.cache_data(ttl=300, show_spinner=False)
def load_estimator_data_cached():
    return load_estimator_data(Path.cwd(), database_url=DATABASE_URL, prefer_database=True)


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
    if re.search(r"\b\d+(?:,\d{3})?\s*(?:sqft|sq ft|sf|square feet)\b", text_value):
        restoration_score += 2
    if any(term in text_value for term in ("10-year", "10 year", "15-year", "15 year", "20-year", "20 year")):
        restoration_score += 2
    if any(term in text_value for term in ("pipe boot", "curb leak", "service call", "emergency", "small repair", "patch")):
        repair_score += 3
    if any(term in text_value for term in ("walls", "attic", "crawlspace", "r-value", "dc315", "thermal barrier")):
        insulation_score += 3
    if insulation_score >= max(repair_score, restoration_score) and insulation_score > 0:
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
    if field_estimator_fn is None:
        field_estimator_fn, _ = optional_field_notes_estimator()
    if field_estimator_fn is None:
        raise RuntimeError("Field notes estimator is not available in this deployment yet.")
    return resolved_type, field_estimator_fn(notes, overrides or {}, data=field_notes_data)


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


def display_safe_records(records: list[dict[str, Any]], *, editable_fields: set[str] | None = None) -> list[dict[str, Any]]:
    editable_fields = editable_fields or set()
    rows: list[dict[str, Any]] = []
    for row in records or []:
        safe_row: dict[str, Any] = {}
        for key, value in row.items():
            safe_row[key] = value if key in editable_fields else display_safe_cell_value(value)
        rows.append(safe_row)
    return rows


def project_display_frame(frame: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    available = [column for column in columns if column in frame.columns]
    return frame[available].copy() if available else frame.copy()


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
                row[field] = edited[field]
        merged.append(row)
    return merged


def render_repair_estimate_result(result_payload: dict[str, Any], *, notes: str, customer_job_name: str = "") -> None:
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


def estimator_prototype_page() -> None:
    st.title("Estimating Assistant")
    st.caption("Estimator review is required before quoting. Incomplete notes return questions and next actions instead of fabricated estimate ranges.")

    estimate_type_selection = st.selectbox(
        "Estimate Type",
        ESTIMATE_TYPE_OPTIONS,
        index=0,
        help="Auto-detect uses deterministic keywords to route notes to repair, restoration/coating, or insulation estimating.",
        key="estimator_estimate_type",
    )

    data = load_estimator_data_cached()
    with st.expander("Source staging files", expanded=False):
        st.write("Files used:", data.source_files_used or [])
        if data.warnings:
            st.warning("\n".join(data.warnings))
        st.write(
            {
                "jobs": len(data.jobs),
                "estimates": len(data.estimates),
                "line_items": len(data.line_items),
                "template_rows": len(data.template_rows),
                "classified_line_items": len(data.classified_line_items),
                "tracking_summary": len(data.tracking_summary),
                "tracking_daily": len(data.tracking_daily),
                "pricing": len(data.pricing),
            }
        )

    st.subheader("Project Notes")
    sample_cols = st.columns(len(ESTIMATOR_SAMPLE_NOTES))
    for column, (label, sample) in zip(sample_cols, ESTIMATOR_SAMPLE_NOTES.items()):
        if column.button(label, key=f"estimator_sample_{label}"):
            st.session_state["estimator_notes"] = sample

    notes = st.text_area(
        "Rough project notes",
        key="estimator_notes",
        height=120,
        placeholder="Metal roof, about 12,000 sqft, rusted fasteners, restaurant in Louisville, silicone coating, medium access.",
    )
    st.caption("Paste notes here, then build a filled estimate template. Command+Enter only updates the text box; it does not build the draft.")
    resolved_estimate_type = resolve_estimate_type(estimate_type_selection, notes)
    if estimate_type_selection == ESTIMATE_TYPE_AUTO:
        st.caption(f"Auto-detected estimate type: {resolved_estimate_type}")
    else:
        st.caption(f"Selected estimate type: {resolved_estimate_type}")

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

    st.subheader("Scope Interpreter")
    field_estimator_fn, field_estimator_import_warning = optional_field_notes_estimator()
    if field_estimator_import_warning and resolved_estimate_type != ESTIMATE_TYPE_REPAIR:
        st.warning(field_estimator_import_warning)
    if resolved_estimate_type == ESTIMATE_TYPE_REPAIR:
        st.info("Repair mode uses VSimple repair history tables and does not run the sqft-based roof coating estimator.")
        use_historical_calibration = False
    else:
        use_historical_calibration = st.checkbox(
            "Debug: run full historical calibration inside parser",
            value=False,
            help="Default workbench mode uses precomputed relationship tables for editable defaults. Enable this only when debugging the older automatic calibration path.",
            key="use_historical_calibration",
        )
    field_notes_data = data if use_historical_calibration else EstimatorData()
    with st.expander("Optional job header", expanded=False):
        f1, f2 = st.columns(2)
        with f1:
            field_job_name = st.text_input("Job name", key="field_estimator_job_name")
            field_site_address = st.text_input("Address", key="field_estimator_site_address")
        with f2:
            field_city = st.text_input("City", value="", key="field_estimator_city")
            field_state = st.text_input("State", value="", key="field_estimator_state")
    if st.button("Build Filled Estimate Template", key="generate_field_estimate_recommendation"):
        try:
            session_id = capture_estimator_session_event(
                estimator_sessions.create_estimator_session,
                raw_input_notes=notes,
                division="Repair" if resolved_estimate_type == ESTIMATE_TYPE_REPAIR else "",
                template_type="repair" if resolved_estimate_type == ESTIMATE_TYPE_REPAIR else "",
                job_name=field_job_name,
                site_address=field_site_address,
                input_source_type="manual",
                photos_present=False,
                source_file_ids=data.source_files_used,
                estimate_status="PARSING",
            )
            if session_id:
                st.session_state["estimator_session_id"] = session_id
            else:
                st.session_state.pop("estimator_session_id", None)
            if resolved_estimate_type == ESTIMATE_TYPE_REPAIR:
                route, repair_result = route_estimator_request(
                    notes,
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
                st.session_state["field_estimate_recommendation_notes"] = notes
                st.session_state.pop("integrated_repair_estimate_audit_paths", None)
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
            elif field_estimator_fn is None:
                st.warning("Field notes estimator is not available in this deployment yet.")
            else:
                recommendation = field_estimator_fn(
                    notes,
                    {
                        "job_name": field_job_name,
                        "site_address": field_site_address,
                        "city": field_city,
                        "state": field_state,
                    },
                    data=field_notes_data,
                )
                st.session_state["field_estimate_recommendation"] = recommendation
                st.session_state["field_estimate_route"] = resolved_estimate_type
                st.session_state.pop("integrated_repair_estimate_result", None)
                st.session_state["field_estimate_recommendation_notes"] = notes
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
    if st.session_state.get("field_estimate_route") == ESTIMATE_TYPE_REPAIR:
        repair_payload = st.session_state.get("integrated_repair_estimate_result")
        recommendation_notes = st.session_state.get("field_estimate_recommendation_notes") or notes
        if repair_payload:
            if recommendation_notes != notes:
                st.warning(
                    "The displayed repair estimate was generated from earlier notes. "
                    "Click Build Filled Estimate Template again to refresh it for the current text."
                )
            render_repair_estimate_result(repair_payload, notes=recommendation_notes, customer_job_name=field_job_name)
            return
    field_recommendation = st.session_state.get("field_estimate_recommendation")
    if field_recommendation:
        recommendation_notes = st.session_state.get("field_estimate_recommendation_notes") or notes
        if recommendation_notes != notes:
            st.warning(
                "The displayed estimate was generated from earlier notes. "
                "Click Build Filled Estimate Template again to refresh it for the current text."
            )
        estimate_status = getattr(field_recommendation, "estimate_status", None) or field_recommendation.parsed_fields.get("estimate_status") or "READY_TO_ESTIMATE"
        metric_row(
            [
                ("Readiness", str(estimate_status).replace("_", " ").title()),
                ("Review Required", "Yes" if field_recommendation.human_review_required else "No"),
            ]
        )
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
        parsed_fields = field_recommendation.parsed_fields
        st.markdown("**Parsed Scope Summary**")
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
            show_table(dataframe_from_records([summary_row]), list(summary_row.keys()), height=90)
        with st.expander("Show AI evidence and uncertainty", expanded=False):
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
        if field_recommendation.review_flags:
            st.warning("\n".join(field_recommendation.review_flags))
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
        parsed_workbench = build_estimating_workbench(field_recommendation, data)
        workbench_key = str(parsed_workbench.get("estimate_id") or "current")
        debug_mode = st.checkbox(
            "Debug Mode",
            value=False,
            help="Shows legacy calibration, similar-job evidence, and evidence export tools. Normal workbench mode stays focused on editable defaults.",
            key=f"estimator_debug_mode_{workbench_key}",
        )

        st.markdown("### Scope Interpreter - Parsed Scope")
        st.caption("AI and deterministic parsing turn the notes into editable project facts. These fields drive the historical comparison pool and workbook draft.")
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
        filtered_default_workbench = build_estimating_workbench(
            field_recommendation,
            data,
            scope_override=edited_scope,
            historical_filters=historical_filters,
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
        surface_review_rows = build_surface_area_review_rows(parsed_fields, original_workbench)
        if surface_review_rows:
            st.markdown("#### Surface Areas / Dimensions")
            st.caption("Review the parsed components once here. Target R and edited thickness feed the insulation foam decision; detailed formula trace stays in diagnostics.")
            surface_area_editable_fields = {"target_r_value", "edited_thickness_inches"}
            surface_area_df = pd.DataFrame(display_safe_records(surface_review_rows, editable_fields=surface_area_editable_fields))
            surface_area_column_order = (
                [column for column in SURFACE_AREA_DETAIL_COLUMNS if column in surface_area_df.columns]
                if show_row_details
                else [column for column in SURFACE_AREA_REVIEW_COLUMNS if column in surface_area_df.columns]
            )
            surface_area_display_df = (
                surface_area_df if show_row_details else project_display_frame(surface_area_df, surface_area_column_order)
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
            st.markdown("#### Insulation Foam Template Decision")
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
            foam_template_df = pd.DataFrame(display_safe_records(foam_template_rows, editable_fields=foam_template_editable_fields))
            foam_template_column_order = (
                list(foam_template_df.columns)
                if show_row_details
                else [column for column in INSULATION_FOAM_TEMPLATE_COMPACT_COLUMNS if column in foam_template_df.columns]
            )
            foam_template_display_df = (
                foam_template_df if show_row_details else project_display_frame(foam_template_df, foam_template_column_order)
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
            edited_workbench["insulation_foam_template_decisions"] = merge_editable_rows(
                foam_template_rows,
                edited_foam_template_df.to_dict(orient="records"),
                foam_template_editable_fields,
            )

        insulation_template_editable_fields = {
            "include",
            "editable_selector_code",
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
            section_df = pd.DataFrame(display_safe_records(section_rows, editable_fields=insulation_template_editable_fields))
            section_compact_columns = INSULATION_DECISION_SECTION_COLUMNS.get(
                section_key,
                INSULATION_DECISION_TEMPLATE_COMPACT_COLUMNS,
            )
            section_column_order = (
                list(section_df.columns)
                if show_row_details
                else [column for column in section_compact_columns if column in section_df.columns]
            )
            section_display_df = section_df if show_row_details else project_display_frame(section_df, section_column_order)
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
            edited_workbench[section_key] = merge_editable_rows(
                section_rows,
                edited_section_df.to_dict(orient="records"),
                insulation_template_editable_fields,
            )

        if original_workbench.get("roofing_foam_template_decisions"):
            st.markdown("#### Roofing SPF Foam Decision")
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
            roofing_foam_template_df = pd.DataFrame(
                display_safe_records(roofing_foam_template_rows, editable_fields=roofing_foam_template_editable_fields)
            )
            roofing_foam_template_column_order = (
                list(roofing_foam_template_df.columns)
                if show_row_details
                else [column for column in ROOFING_FOAM_TEMPLATE_COMPACT_COLUMNS if column in roofing_foam_template_df.columns]
            )
            roofing_foam_template_display_df = (
                roofing_foam_template_df
                if show_row_details
                else project_display_frame(roofing_foam_template_df, roofing_foam_template_column_order)
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
            edited_workbench["roofing_foam_template_decisions"] = merge_editable_rows(
                roofing_foam_template_rows,
                edited_roofing_foam_template_df.to_dict(orient="records"),
                roofing_foam_template_editable_fields,
            )

        if original_workbench.get("roofing_coating_template_decisions"):
            st.markdown("#### Roof Coating System Decision")
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
            coating_template_df = pd.DataFrame(display_safe_records(coating_template_rows, editable_fields=coating_template_editable_fields))
            coating_template_column_order = (
                list(coating_template_df.columns)
                if show_row_details
                else [column for column in ROOFING_COATING_TEMPLATE_COMPACT_COLUMNS if column in coating_template_df.columns]
            )
            coating_template_display_df = (
                coating_template_df if show_row_details else project_display_frame(coating_template_df, coating_template_column_order)
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
                    "wet_mils_estimate": "Wet Mils",
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
            edited_workbench["roofing_coating_template_decisions"] = merge_editable_rows(
                coating_template_rows,
                edited_coating_template_df.to_dict(orient="records"),
                coating_template_editable_fields,
            )

        if original_workbench.get("roofing_primer_template_decisions"):
            st.markdown("#### Roofing Primer System Decision")
            primer_template_editable_fields = {
                "include",
                "editable_selector_code",
                "basis_sqft",
                "coverage_sqft_per_unit",
                "unit_price",
                "selected_pricing_candidate",
            }
            primer_template_rows = original_workbench.get("roofing_primer_template_decisions") or []
            primer_template_df = pd.DataFrame(display_safe_records(primer_template_rows, editable_fields=primer_template_editable_fields))
            primer_template_column_order = (
                list(primer_template_df.columns)
                if show_row_details
                else [column for column in ROOFING_PRIMER_TEMPLATE_COMPACT_COLUMNS if column in primer_template_df.columns]
            )
            primer_template_display_df = (
                primer_template_df if show_row_details else project_display_frame(primer_template_df, primer_template_column_order)
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
            edited_workbench["roofing_primer_template_decisions"] = merge_editable_rows(
                primer_template_rows,
                edited_primer_template_df.to_dict(orient="records"),
                primer_template_editable_fields,
            )

        if original_workbench.get("roofing_detail_template_decisions"):
            st.markdown("#### Roofing Fabric / Sealant System Decision")
            detail_template_editable_fields = {
                "include",
                "editable_selector_code",
                "units",
                "linear_ft",
                "unit_price",
                "selected_pricing_candidate",
            }
            detail_template_rows = original_workbench.get("roofing_detail_template_decisions") or []
            detail_template_df = pd.DataFrame(display_safe_records(detail_template_rows, editable_fields=detail_template_editable_fields))
            detail_template_column_order = (
                list(detail_template_df.columns)
                if show_row_details
                else [column for column in ROOFING_DETAIL_TEMPLATE_COMPACT_COLUMNS if column in detail_template_df.columns]
            )
            detail_template_display_df = (
                detail_template_df if show_row_details else project_display_frame(detail_template_df, detail_template_column_order)
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
                    "estimated_units": "Calculated Units",
                    "estimated_cost": "Cost",
                    "selected_pricing_candidate": "Pricing Candidate",
                    "compatibility_status": "Compatibility",
                    "compatibility_warnings": "Warnings",
                    "product_guidance": "Product Guidance",
                    "notes": "Notes",
                },
                disabled=[column for column in detail_template_column_order if column not in detail_template_editable_fields],
            )
            edited_workbench["roofing_detail_template_decisions"] = merge_editable_rows(
                detail_template_rows,
                edited_detail_template_df.to_dict(orient="records"),
                detail_template_editable_fields,
            )

        if original_workbench.get("roofing_detail_quantity_template_decisions"):
            st.markdown("#### Roofing Detail Quantity Decision")
            detail_quantity_template_editable_fields = {
                "include",
                "linear_ft",
                "units",
                "estimated_units",
                "amount",
            }
            detail_quantity_template_rows = original_workbench.get("roofing_detail_quantity_template_decisions") or []
            detail_quantity_template_df = pd.DataFrame(
                display_safe_records(detail_quantity_template_rows, editable_fields=detail_quantity_template_editable_fields)
            )
            detail_quantity_template_column_order = (
                list(detail_quantity_template_df.columns)
                if show_row_details
                else [column for column in ROOFING_DETAIL_QUANTITY_TEMPLATE_COMPACT_COLUMNS if column in detail_quantity_template_df.columns]
            )
            detail_quantity_template_display_df = (
                detail_quantity_template_df
                if show_row_details
                else project_display_frame(detail_quantity_template_df, detail_quantity_template_column_order)
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
            edited_workbench["roofing_detail_quantity_template_decisions"] = merge_editable_rows(
                detail_quantity_template_rows,
                edited_detail_quantity_template_df.to_dict(orient="records"),
                detail_quantity_template_editable_fields,
            )

        if original_workbench.get("roofing_board_fastener_template_decisions"):
            st.markdown("#### Roofing Board / Fastener System Decision")
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
            board_template_df = pd.DataFrame(display_safe_records(board_template_rows, editable_fields=board_template_editable_fields))
            board_template_column_order = (
                list(board_template_df.columns)
                if show_row_details
                else [column for column in ROOFING_BOARD_FASTENER_TEMPLATE_COMPACT_COLUMNS if column in board_template_df.columns]
            )
            board_template_display_df = (
                board_template_df if show_row_details else project_display_frame(board_template_df, board_template_column_order)
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
                    "estimated_units": "Units",
                    "estimated_cost": "Cost",
                    "selected_pricing_candidate": "Pricing Candidate",
                    "compatibility_status": "Compatibility",
                    "compatibility_warnings": "Warnings",
                    "product_guidance": "Product Guidance",
                    "notes": "Notes",
                },
                disabled=[column for column in board_template_column_order if column not in board_template_editable_fields],
            )
            edited_workbench["roofing_board_fastener_template_decisions"] = merge_editable_rows(
                board_template_rows,
                edited_board_template_df.to_dict(orient="records"),
                board_template_editable_fields,
            )

        if original_workbench.get("roofing_granules_template_decisions"):
            st.markdown("#### Roofing Granules System Decision")
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
            granules_template_df = pd.DataFrame(display_safe_records(granules_template_rows, editable_fields=granules_template_editable_fields))
            granules_template_column_order = (
                list(granules_template_df.columns)
                if show_row_details
                else [column for column in ROOFING_GRANULES_TEMPLATE_COMPACT_COLUMNS if column in granules_template_df.columns]
            )
            granules_template_display_df = (
                granules_template_df if show_row_details else project_display_frame(granules_template_df, granules_template_column_order)
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
            edited_workbench["roofing_granules_template_decisions"] = merge_editable_rows(
                granules_template_rows,
                edited_granules_template_df.to_dict(orient="records"),
                granules_template_editable_fields,
            )

        if original_workbench.get("roofing_equipment_template_decisions"):
            st.markdown("#### Roofing Equipment / Dumpster Decision")
            equipment_template_editable_fields = {
                "include",
                "editable_selector_code",
                "basis_sqft",
                "thickness_inches",
                "size",
                "period",
                "days",
                "unit_price",
                "margin_pct",
            }
            equipment_template_rows = original_workbench.get("roofing_equipment_template_decisions") or []
            equipment_template_df = pd.DataFrame(display_safe_records(equipment_template_rows, editable_fields=equipment_template_editable_fields))
            equipment_template_column_order = (
                list(equipment_template_df.columns)
                if show_row_details
                else [column for column in ROOFING_EQUIPMENT_TEMPLATE_COMPACT_COLUMNS if column in equipment_template_df.columns]
            )
            equipment_template_display_df = (
                equipment_template_df if show_row_details else project_display_frame(equipment_template_df, equipment_template_column_order)
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
                    "thickness_inches": "Thickness",
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
            edited_workbench["roofing_equipment_template_decisions"] = merge_editable_rows(
                equipment_template_rows,
                edited_equipment_template_df.to_dict(orient="records"),
                equipment_template_editable_fields,
            )

        if original_workbench.get("roofing_travel_freight_template_decisions"):
            st.markdown("#### Roofing Travel / Freight Decision")
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
            travel_freight_template_df = pd.DataFrame(
                display_safe_records(travel_freight_template_rows, editable_fields=travel_freight_template_editable_fields)
            )
            travel_freight_template_column_order = (
                list(travel_freight_template_df.columns)
                if show_row_details
                else [column for column in ROOFING_TRAVEL_FREIGHT_TEMPLATE_COMPACT_COLUMNS if column in travel_freight_template_df.columns]
            )
            travel_freight_template_display_df = (
                travel_freight_template_df
                if show_row_details
                else project_display_frame(travel_freight_template_df, travel_freight_template_column_order)
            )
            edited_travel_freight_template_df = st.data_editor(
                travel_freight_template_display_df,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                key=f"wb_roofing_travel_freight_template_{workbench_key}_{scope_key}_{historical_filters_key}",
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
            edited_workbench["roofing_travel_freight_template_decisions"] = merge_editable_rows(
                travel_freight_template_rows,
                edited_travel_freight_template_df.to_dict(orient="records"),
                travel_freight_template_editable_fields,
            )

        if original_workbench.get("roofing_accessory_template_decisions"):
            st.markdown("#### Roofing Accessories / Support Decision")
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
            accessory_template_df = pd.DataFrame(display_safe_records(accessory_template_rows, editable_fields=accessory_template_editable_fields))
            accessory_template_column_order = (
                list(accessory_template_df.columns)
                if show_row_details
                else [column for column in ROOFING_ACCESSORY_TEMPLATE_COMPACT_COLUMNS if column in accessory_template_df.columns]
            )
            accessory_template_display_df = (
                accessory_template_df if show_row_details else project_display_frame(accessory_template_df, accessory_template_column_order)
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
            edited_workbench["roofing_accessory_template_decisions"] = merge_editable_rows(
                accessory_template_rows,
                edited_accessory_template_df.to_dict(orient="records"),
                accessory_template_editable_fields,
            )

        if original_workbench.get("roofing_labor_template_decisions"):
            st.markdown("#### Roofing Labor Planning Decision")
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
            labor_template_df = pd.DataFrame(display_safe_records(labor_template_rows, editable_fields=labor_template_editable_fields))
            labor_template_column_order = (
                list(labor_template_df.columns)
                if show_row_details
                else [column for column in ROOFING_LABOR_TEMPLATE_COMPACT_COLUMNS if column in labor_template_df.columns]
            )
            labor_template_display_df = (
                labor_template_df if show_row_details else project_display_frame(labor_template_df, labor_template_column_order)
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
            edited_workbench["roofing_labor_template_decisions"] = merge_editable_rows(
                labor_template_rows,
                edited_labor_template_df.to_dict(orient="records"),
                labor_template_editable_fields,
            )

        edited_workbench = recalculate_workbench_tables(edited_workbench)
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

        with st.expander("Draft workbook input preview", expanded=False):
            st.json(workbench_to_draft_workbook_inputs(edited_workbench))

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
                    edited_workbook_inputs = workbench_to_draft_workbook_inputs(edited_workbench)
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
                workbook_path_for_package = None
                workbook_error_for_package = None
                template_path = resolve_default_template_path()
                if not template_path.exists():
                    workbook_error_for_package = "Estimate template workbook not found. Add it to templates/Estimate - Full Turnkey.xlsx."
                    st.session_state.pop(workbook_path_key, None)
                    st.session_state[workbook_error_key] = workbook_error_for_package
                else:
                    try:
                        edited_workbook_inputs = workbench_to_draft_workbook_inputs(edited_workbench)
                        generated_workbook_path = generate_estimate_workbook(
                            edited_workbook_inputs,
                            template_path,
                            DEFAULT_ESTIMATE_OUTPUT_DIR,
                        )
                        workbook_path_for_package = str(generated_workbook_path)
                        st.session_state[workbook_path_key] = workbook_path_for_package
                        st.session_state.pop(workbook_error_key, None)
                    except Exception as workbook_exc:
                        logger.exception("Field notes Excel draft generation failed during review package export")
                        workbook_error_for_package = f"Could not generate Excel estimate draft: {safe_exception_text(workbook_exc)}"
                        st.session_state.pop(workbook_path_key, None)
                        st.session_state[workbook_error_key] = workbook_error_for_package
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
                )
                session_id = current_estimator_session_id()
                if session_id:
                    edit_rows = build_edit_history_rows(feedback_baseline, edited_workbench, reason_map=reason_map)
                    capture_estimator_session_event(
                        estimator_sessions.save_decision_edits,
                        session_id,
                        edit_rows,
                        edited_by="estimator",
                    )
                    edited_workbook_inputs_for_capture = workbench_to_draft_workbook_inputs(edited_workbench)
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
                    data=package_path.read_bytes(),
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
                    capture_estimator_session_event(
                        estimator_sessions.save_decision_edits,
                        session_id,
                        edit_rows,
                        edited_by="estimator",
                    )
                    edited_workbook_inputs_for_session = workbench_to_draft_workbook_inputs(edited_workbench)
                    workbook_path_for_session = st.session_state.get(workbook_path_key)
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
                    session_package_path = estimator_sessions.export_estimator_session_package(
                        get_engine(),
                        session_id,
                        DEFAULT_WORKBENCH_EXPORT_DIR / f"estimator_session_{session_id}.zip",
                    )
                    st.session_state[f"estimator_session_review_package_path_{session_id}"] = str(session_package_path)
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
                        data=session_package_path.read_bytes(),
                        file_name=session_package_path.name,
                        mime="application/zip",
                        key=f"download_estimator_session_review_package_{session_id}",
                    )
        else:
            st.caption("Build a filled estimate template to start a persisted estimating session.")
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
    view_name = st.selectbox("View", VIEWS)
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


def main() -> None:
    database_startup_error: Exception | None = None
    try:
        jobs_for_filters = query_view("dashboard_jobs")
    except Exception as exc:
        jobs_for_filters = pd.DataFrame()
        database_startup_error = exc

    with st.sidebar:
        render_database_target_debug()
        filters = sidebar_filters(jobs_for_filters)
        page = st.radio(
            "Page",
            [
                "Owner Overview",
                "Ask Spray-Tec",
                "Job Board",
                "Schedule Calendar",
                "Estimating Assistant",
                "BidScope AI",
                "Admin / Health",
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
                "Pricing Catalog",
                "Raw Tables",
            ],
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
