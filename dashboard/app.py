from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError


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
            out["folder_link_or_path"] = out["folder_url"]
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
            "Pipeline / Money",
            "Sales Follow-Up",
            "Contracted Backlog / Scheduling",
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
    elif page == "Pipeline / Money":
        pipeline_money_page()
    elif page == "Sales Follow-Up":
        sales_followup_page()
    elif page == "Contracted Backlog / Scheduling":
        contracted_backlog_scheduling_page()
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
