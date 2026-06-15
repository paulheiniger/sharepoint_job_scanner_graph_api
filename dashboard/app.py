from __future__ import annotations

import hashlib
import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

try:
    from streamlit_calendar import calendar
except ImportError:
    calendar = None


load_dotenv(dotenv_path=Path.cwd() / ".env")

DEFAULT_DATABASE_URL = "postgresql+psycopg2://spraytec:spraytec_dev_password@127.0.0.1:5433/spraytec_ops"


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
]

selected_divisions: list[str] = []
selected_pipeline_statuses: list[str] = []
selected_statuses: list[str] = []
customer_search = ""

st.set_page_config(page_title="Spray-Tec Ops Dashboard", layout="wide")


@st.cache_resource
def get_engine():
    return create_engine(DATABASE_URL, future=True)


@st.cache_data(ttl=300, show_spinner=False)
def load_df(query: str) -> pd.DataFrame:
    engine = get_engine()
    with engine.connect() as conn:
        return pd.read_sql_query(text(query), conn)


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
    try:
        engine = get_engine()
        with engine.connect() as conn:
            return pd.read_sql_query(query, conn, params={"dispatch_date": dispatch_date})
    except (SQLAlchemyError, OSError, ValueError) as exc:
        show_database_error(exc)
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
        "Could not connect to the Spray-Tec Postgres database. "
        "Check that Docker/Postgres is running and that DATABASE_URL in .env is correct."
    )
    st.caption(str(exc))


def safe_load(query: str) -> pd.DataFrame:
    try:
        return load_df(query)
    except (SQLAlchemyError, OSError, ValueError) as exc:
        show_database_error(exc)
        st.stop()


def query_view(view_name: str) -> pd.DataFrame:
    if view_name not in VIEWS:
        raise ValueError(f"Unsupported dashboard view: {view_name}")
    return safe_load(f"SELECT * FROM {view_name}")


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


def main() -> None:
    try:
        jobs_for_filters = query_view("dashboard_jobs")
    except Exception as exc:
        show_database_error(exc)
        st.stop()

    filters = sidebar_filters(jobs_for_filters)
    page = st.sidebar.radio(
        "Page",
        [
            "Owner Overview",
            "Job Board",
            "Schedule Calendar",
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
        ],
    )

    if page == "Owner Overview":
        owner_overview_page()
    elif page == "Job Board":
        job_board_page()
    elif page == "Schedule Calendar":
        schedule_calendar_page()
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
    else:
        raw_tables_page()


if __name__ == "__main__":
    main()
