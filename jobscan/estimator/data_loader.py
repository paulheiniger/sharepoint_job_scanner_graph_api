from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text

from jobscan.db_connections import create_resilient_engine
from .decision_history import DECISION_NUMERIC_FIELDS, DECISION_TABLES
from .estimator_memory import estimator_memory_from_rows
from .schemas import DEFAULT_STAGE_FILES, PRICING_CANDIDATES, EstimatorData


TEMPLATE_ROW_COLUMNS = [
    "template_row_id",
    "document_id",
    "job_id",
    "source_file",
    "template_type",
    "sheet_name",
    "row_number",
    "template_bucket",
    "template_section",
    "line_item_kind",
    "row_label",
    "selected_item_name",
    "quantity",
    "unit",
    "unit_price",
    "estimated_units",
    "estimated_cost",
    "selector_code",
    "resolved_item_name",
    "area_sqft",
    "thickness_inches",
    "yield_or_coverage",
    "yield_factor",
    "estimated_sets",
    "foam_brand",
    "foam_density_lb",
    "units_per_sqft_per_inch",
    "sets_per_sqft_per_inch",
    "cost_per_sqft_per_inch",
    "gal_per_100_sqft",
    "gal_per_sqft",
    "estimated_gallons",
    "linear_ft",
    "ft_per_unit",
    "margin_pct",
    "waste_margin_cell",
    "quantity_cell_role",
    "formula_model",
    "days",
    "crew_size",
    "total_hours",
    "daily_rate",
    "crew_selector_code",
    "hourly_rate",
    "calculated_cost",
    "formula_mode",
    "trips",
    "round_trip_miles",
    "cost_per_mile",
    "warranty_years",
    "overhead_pct",
    "profit_pct",
    "needs_review",
]

ESTIMATOR_LOAD_PROFILE_FULL = "full"
ESTIMATOR_LOAD_PROFILE_INTERACTIVE = "interactive"
ESTIMATOR_LOAD_PROFILES = {ESTIMATOR_LOAD_PROFILE_FULL, ESTIMATOR_LOAD_PROFILE_INTERACTIVE}

INTERACTIVE_TEMPLATE_ROW_WHERE = """
WHERE (
  lower(coalesce(line_item_kind,'')) IN ('material','labor','equipment','travel','adder','pricing')
  OR lower(coalesce(template_bucket,'')) IN ('overhead','profit')
  OR row_number IN (19,20,21,26,27,28,30,41,95,97,99,100,165,167)
)
AND (
  nullif(selected_item_name,'') IS NOT NULL
  OR nullif(row_label,'') IS NOT NULL
  OR coalesce(quantity,0) <> 0
  OR coalesce(unit_price,0) <> 0
  OR coalesce(estimated_units,0) <> 0
  OR coalesce(estimated_cost,0) <> 0
  OR coalesce(total_hours,0) <> 0
  OR coalesce(days,0) <> 0
  OR coalesce(overhead_pct,0) <> 0
  OR coalesce(profit_pct,0) <> 0
)
"""

INTERACTIVE_RELATIONSHIP_COOCCURRENCE_WHERE = """
WHERE co_occurrence_rate >= 0.5
  AND job_count >= 3
  AND (
    project_type ILIKE '%roof%'
    OR project_type ILIKE '%insulation%'
    OR project_type ILIKE '%floor%'
    OR project_type IS NULL
    OR project_type = ''
  )
"""

INTERACTIVE_RELATIONSHIP_COOCCURRENCE_ORDER_LIMIT = "ORDER BY job_count DESC, co_occurrence_rate DESC LIMIT 5000"

ESTIMATOR_NUMERIC_COLUMNS = [
    "estimated_cost",
    "cost_low",
    "cost_high",
    "unit_price",
    "unit_cost",
    "price_per_gallon",
    "price_per_sqft",
    "price_per_unit",
    "quantity",
    "median_cost",
    "median_days",
    "median_total_hours",
    "median_crew_size",
    "evidence_count",
    "job_count",
    "area_sqft",
    "hours_per_sqft",
    "hours_per_1000_sqft",
    "cost_per_sqft",
    "median_hours_per_1000_sqft",
    "p25_hours_per_1000_sqft",
    "p75_hours_per_1000_sqft",
    "median_qty_per_sqft",
    "p25_qty_per_sqft",
    "p75_qty_per_sqft",
    "median_cost_per_sqft",
    "days",
    "crew_size",
    "total_hours",
    "total_quantity",
    "qty_per_sqft",
    "daily_rate",
    "crew_selector_code",
    "hourly_rate",
    "calculated_cost",
    "surface_area_sqft",
    "estimated_sqft",
    "gross_area_sqft",
    "deduction_area_sqft",
    "net_area_sqft",
    "selector_code",
    "thickness_inches",
    "yield_or_coverage",
    "yield_factor",
    "estimated_sets",
    "foam_density_lb",
    "units_per_sqft_per_inch",
    "sets_per_sqft_per_inch",
    "cost_per_sqft_per_inch",
    "gal_per_100_sqft",
    "gal_per_sqft",
    "estimated_gallons",
    "linear_ft",
    "ft_per_unit",
    "margin_pct",
]


def normalize_numeric_columns(df: pd.DataFrame | None, columns: list[str]) -> pd.DataFrame:
    if df is None:
        return pd.DataFrame()
    df = df.copy()
    for column in columns:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def normalize_estimator_dataframe(df: pd.DataFrame | None) -> pd.DataFrame:
    return normalize_numeric_columns(df, ESTIMATOR_NUMERIC_COLUMNS)


def normalize_estimator_data(data: EstimatorData) -> EstimatorData:
    data.pricing_catalog = normalize_estimator_dataframe(data.pricing_catalog)
    data.pricing = normalize_estimator_dataframe(data.pricing)
    data.template_rows = normalize_estimator_dataframe(data.template_rows)
    data.line_items = normalize_estimator_dataframe(data.line_items)
    data.classified_line_items = normalize_estimator_dataframe(data.classified_line_items)
    data.line_item_classifications = normalize_estimator_dataframe(data.line_item_classifications)
    data.jobs = normalize_estimator_dataframe(data.jobs)
    data.estimates = normalize_estimator_dataframe(data.estimates)
    data.tracking_summary = normalize_estimator_dataframe(data.tracking_summary)
    data.tracking_daily = normalize_estimator_dataframe(data.tracking_daily)
    data.relationship_material_qty_ratios = normalize_estimator_dataframe(data.relationship_material_qty_ratios)
    data.relationship_labor_rates = normalize_estimator_dataframe(data.relationship_labor_rates)
    data.relationship_package_cooccurrence = normalize_estimator_dataframe(data.relationship_package_cooccurrence)
    data.job_package_summary = normalize_estimator_dataframe(data.job_package_summary)
    data.product_catalog = normalize_estimator_dataframe(data.product_catalog)
    data.product_aliases = normalize_estimator_dataframe(data.product_aliases)
    data.product_documents = normalize_estimator_dataframe(data.product_documents)
    data.product_properties = normalize_estimator_dataframe(data.product_properties)
    data.product_rules = normalize_estimator_dataframe(data.product_rules)
    data.product_decision_links = normalize_estimator_dataframe(data.product_decision_links)
    data.template_product_option_links = normalize_estimator_dataframe(data.template_product_option_links)
    data.template_pricing_option_links = normalize_estimator_dataframe(data.template_pricing_option_links)
    data.template_selector_maps = normalize_estimator_dataframe(data.template_selector_maps)
    data.template_lookup_tables = normalize_estimator_dataframe(data.template_lookup_tables)
    data.template_row_catalog = normalize_estimator_dataframe(data.template_row_catalog)
    data.template_formula_models = normalize_estimator_dataframe(data.template_formula_models)
    data.template_product_options = normalize_estimator_dataframe(data.template_product_options)
    data.template_labor_options = normalize_estimator_dataframe(data.template_labor_options)
    data.estimator_memory = estimator_memory_from_rows(data.estimator_memory)
    data.estimator_decision_recommendations = normalize_numeric_columns(
        data.estimator_decision_recommendations,
        [
            "recommended_value",
            "evidence_count",
            "source_jobs_count",
            "p25",
            "median",
            "p75",
        ],
    )
    normalized_history: dict[str, pd.DataFrame] = {}
    for table_name, frame in (data.decision_history_tables or {}).items():
        normalized_history[table_name] = normalize_numeric_columns(frame, DECISION_NUMERIC_FIELDS)
    data.decision_history_tables = normalized_history
    if data.pricing.empty and not data.pricing_catalog.empty:
        data.pricing = data.pricing_catalog
    if data.pricing_catalog.empty and not data.pricing.empty:
        data.pricing_catalog = data.pricing
    if data.classified_line_items.empty and not data.line_item_classifications.empty:
        data.classified_line_items = data.line_item_classifications
    if data.line_item_classifications.empty and not data.classified_line_items.empty:
        data.line_item_classifications = data.classified_line_items
    return data


def _records_from_json(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        for key in ("rows", "records", "data", "items"):
            rows = value.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    return []


def read_json_dataframe(path: Path) -> pd.DataFrame:
    value = json.loads(path.read_text(encoding="utf-8"))
    return pd.DataFrame(_records_from_json(value))


def read_csv_dataframe(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)


def _load_estimator_data_from_local_files(root: Path) -> EstimatorData:
    root = Path(root)
    data = EstimatorData()

    for attr, relative_path in DEFAULT_STAGE_FILES.items():
        path = root / relative_path
        if not path.exists():
            data.warnings.append(f"Missing staging file: {relative_path}")
            continue
        try:
            setattr(data, attr, read_json_dataframe(path))
            data.source_files_used.append(str(relative_path))
        except Exception as exc:
            data.warnings.append(f"Could not read {relative_path}: {exc}")

    for relative_path in PRICING_CANDIDATES:
        path = root / relative_path
        if not path.exists():
            continue
        try:
            data.pricing = read_csv_dataframe(path)
            data.source_files_used.append(str(relative_path))
            break
        except Exception as exc:
            data.warnings.append(f"Could not read {relative_path}: {exc}")

    if data.pricing.empty:
        data.warnings.append("No current pricing export found.")
    if not data.line_items.empty:
        try:
            from .line_items import classify_line_items

            data.classified_line_items = classify_line_items(data.line_items)
        except Exception as exc:
            data.warnings.append(f"Could not classify local estimate line items: {type(exc).__name__}")
    return normalize_estimator_data(data)


def _read_sql_dataframe(connection: Any, query: str) -> pd.DataFrame:
    return pd.read_sql_query(text(query), connection)


def relation_columns(connection: Any, relation_name: str) -> list[str]:
    try:
        result = connection.execute(text(f"SELECT * FROM {relation_name} LIMIT 0"))
        return list(result.keys())
    except Exception:
        return []


def relation_exists(connection: Any, relation_name: str) -> bool:
    return bool(relation_columns(connection, relation_name))


def read_relation_columns(
    connection: Any,
    relation_name: str,
    columns: list[str] | None = None,
    where: str = "",
    suffix: str = "",
) -> pd.DataFrame:
    available = relation_columns(connection, relation_name)
    if not available:
        return pd.DataFrame()
    selected = [column for column in (columns or available) if column in available]
    if not selected:
        return pd.DataFrame()
    sql = f"SELECT {', '.join(selected)} FROM {relation_name} {where} {suffix}".strip()
    return _read_sql_dataframe(connection, sql)


def load_current_pricing(connection: Any, data: EstimatorData) -> pd.DataFrame:
    columns = relation_columns(connection, "pricing_catalog")
    if not columns:
        data.warnings.append("pricing_catalog table not found; current material pricing is unavailable.")
        return pd.DataFrame()
    where = "WHERE is_current = true" if "is_current" in columns else ""
    try:
        pricing = _read_sql_dataframe(connection, f"SELECT * FROM pricing_catalog {where}".strip())
    except Exception as exc:
        data.warnings.append(f"Could not load pricing_catalog: {type(exc).__name__}")
        return pd.DataFrame()
    data.source_files_used.append("database: pricing_catalog")
    return pricing


def load_historical_scope_texts(connection: Any, *, limit: int | None = None) -> pd.DataFrame:
    if not relation_exists(connection, "documents") or not relation_exists(connection, "document_content"):
        return pd.DataFrame()
    row_limit = int(limit or os.getenv("ESTIMATOR_HISTORICAL_SCOPE_TEXT_LIMIT", "2000"))
    return _read_sql_dataframe(
        connection,
        f"""
        SELECT
            d.job_id,
            d.document_id,
            d.file_name,
            d.document_type,
            d.sharepoint_url,
            d.folder_path,
            d.relative_path,
            d.source_year,
            LEFT(
                STRING_AGG(
                    c.text_content,
                    E'\n'
                    ORDER BY c.page_number NULLS LAST,
                             c.sheet_name NULLS LAST,
                             c.row_number NULLS LAST,
                             c.source_locator NULLS LAST
                ),
                12000
            ) AS scope_text,
            COUNT(*) AS content_row_count
        FROM documents d
        JOIN document_content c ON c.document_id = d.document_id
        WHERE COALESCE(d.job_id, '') <> ''
          AND COALESCE(c.text_content, '') <> ''
          AND (
            LOWER(COALESCE(d.document_type, '')) = 'proposal'
            OR LOWER(COALESCE(d.file_name, '')) LIKE '%proposal%'
            OR LOWER(COALESCE(d.file_name, '')) LIKE '%quote%'
            OR LOWER(COALESCE(d.file_name, '')) LIKE '%bid%'
          )
        GROUP BY
            d.job_id,
            d.document_id,
            d.file_name,
            d.document_type,
            d.sharepoint_url,
            d.folder_path,
            d.relative_path,
            d.source_year
        HAVING LENGTH(STRING_AGG(c.text_content, E'\n')) >= 80
        ORDER BY d.source_year DESC NULLS LAST, d.file_name
        LIMIT {row_limit}
        """,
    )


def load_estimator_data_from_database(database_url: str, *, load_profile: str = ESTIMATOR_LOAD_PROFILE_FULL) -> EstimatorData:
    if load_profile not in ESTIMATOR_LOAD_PROFILES:
        raise ValueError(f"Unknown estimator load profile: {load_profile}")
    interactive = load_profile == ESTIMATOR_LOAD_PROFILE_INTERACTIVE
    engine = create_resilient_engine(database_url)
    data = EstimatorData()
    with engine.connect() as connection:
        if relation_exists(connection, "dashboard_jobs"):
            data.jobs = _read_sql_dataframe(connection, "SELECT * FROM dashboard_jobs")
            data.source_files_used.append("database: dashboard_jobs")
        elif relation_exists(connection, "jobs"):
            data.jobs = _read_sql_dataframe(connection, "SELECT * FROM jobs")
            data.source_files_used.append("database: jobs")
        else:
            data.warnings.append("dashboard_jobs/jobs table not found; similar-job matching is limited.")

        if relation_exists(connection, "estimates"):
            data.estimates = _read_sql_dataframe(connection, "SELECT * FROM estimates")
            data.source_files_used.append("database: estimates")
        else:
            data.warnings.append("estimates table not found; estimate summary history is unavailable.")

        if not interactive and relation_exists(connection, "estimate_line_items"):
            data.line_items = _read_sql_dataframe(connection, "SELECT * FROM estimate_line_items")
            data.source_files_used.append("database: estimate_line_items")
        elif not interactive:
            data.warnings.append("estimate_line_items table not found; using estimate_template_rows only.")

        if relation_exists(connection, "estimate_template_rows"):
            template_where = INTERACTIVE_TEMPLATE_ROW_WHERE if interactive else ""
            data.template_rows = read_relation_columns(connection, "estimate_template_rows", TEMPLATE_ROW_COLUMNS, where=template_where)
            data.source_files_used.append("database: estimate_template_rows")
        else:
            data.warnings.append(
                "estimate_template_rows table not found; run python -m jobscan.estimator.template_rows --parse-existing."
            )

        if not interactive and relation_exists(connection, "estimate_line_item_classifications"):
            data.classified_line_items = _read_sql_dataframe(connection, "SELECT * FROM estimate_line_item_classifications")
            data.line_item_classifications = data.classified_line_items
            data.source_files_used.append("database: estimate_line_item_classifications")
        elif not interactive:
            data.warnings.append("estimate_line_item_classifications table not found; using estimate_template_rows only")

        if not interactive and relation_exists(connection, "job_tracking_summary"):
            data.tracking_summary = _read_sql_dataframe(connection, "SELECT * FROM job_tracking_summary")
            data.source_files_used.append("database: job_tracking_summary")

        if not interactive and relation_exists(connection, "job_tracking_daily_entries"):
            data.tracking_daily = _read_sql_dataframe(connection, "SELECT * FROM job_tracking_daily_entries")
            data.source_files_used.append("database: job_tracking_daily_entries")

        if relation_exists(connection, "relationship_labor_rates"):
            data.relationship_labor_rates = _read_sql_dataframe(connection, "SELECT * FROM relationship_labor_rates")
            data.source_files_used.append("database: relationship_labor_rates")

        if relation_exists(connection, "relationship_material_qty_ratios"):
            data.relationship_material_qty_ratios = _read_sql_dataframe(connection, "SELECT * FROM relationship_material_qty_ratios")
            data.source_files_used.append("database: relationship_material_qty_ratios")

        if relation_exists(connection, "relationship_package_cooccurrence"):
            relationship_where = INTERACTIVE_RELATIONSHIP_COOCCURRENCE_WHERE if interactive else ""
            relationship_suffix = INTERACTIVE_RELATIONSHIP_COOCCURRENCE_ORDER_LIMIT if interactive else ""
            data.relationship_package_cooccurrence = read_relation_columns(
                connection,
                "relationship_package_cooccurrence",
                where=relationship_where,
                suffix=relationship_suffix,
            )
            data.source_files_used.append("database: relationship_package_cooccurrence")

        if relation_exists(connection, "job_package_summary"):
            data.job_package_summary = _read_sql_dataframe(connection, "SELECT * FROM job_package_summary")
            data.source_files_used.append("database: job_package_summary")

        for attr, relation_name in (
            ("product_catalog", "product_catalog"),
            ("product_aliases", "product_aliases"),
            ("product_documents", "product_documents"),
            ("product_properties", "product_properties"),
            ("product_rules", "product_rules"),
            ("product_decision_links", "product_decision_links"),
            ("template_product_option_links", "template_product_option_links"),
            ("template_pricing_option_links", "template_pricing_option_links"),
        ):
            if relation_exists(connection, relation_name):
                setattr(data, attr, _read_sql_dataframe(connection, f"SELECT * FROM {relation_name}"))
                data.source_files_used.append(f"database: {relation_name}")

        for attr, relation_name in (
            ("template_selector_maps", "template_selector_maps"),
            ("template_lookup_tables", "template_lookup_tables"),
            ("template_row_catalog", "template_row_catalog"),
            ("template_formula_models", "template_formula_models"),
            ("template_product_options", "template_product_options"),
            ("template_labor_options", "template_labor_options"),
        ):
            if relation_exists(connection, relation_name):
                setattr(data, attr, _read_sql_dataframe(connection, f"SELECT * FROM {relation_name}"))
                data.source_files_used.append(f"database: {relation_name}")

        if not interactive:
            decision_history_tables: dict[str, pd.DataFrame] = {}
            for table_name in DECISION_TABLES:
                relation_name = f"analytics.{table_name}"
                if relation_exists(connection, relation_name):
                    decision_history_tables[table_name] = _read_sql_dataframe(connection, f"SELECT * FROM {relation_name}")
                    data.source_files_used.append(f"database: {relation_name}")
            data.decision_history_tables = decision_history_tables

        recommendation_relation = "analytics.estimator_decision_recommendations"
        if relation_exists(connection, recommendation_relation):
            data.estimator_decision_recommendations = _read_sql_dataframe(connection, f"SELECT * FROM {recommendation_relation}")
            data.source_files_used.append(f"database: {recommendation_relation}")

        if relation_exists(connection, "estimator_memory"):
            data.estimator_memory = _read_sql_dataframe(
                connection,
                """
                SELECT *
                FROM estimator_memory
                WHERE status = 'approved'
                ORDER BY
                    CASE priority
                        WHEN 'high' THEN 0
                        WHEN 'medium' THEN 1
                        WHEN 'low' THEN 2
                        ELSE 3
                    END,
                    updated_at DESC
                LIMIT 250
                """,
            )
            data.source_files_used.append("database: estimator_memory")

        if not interactive:
            data.historical_scope_texts = load_historical_scope_texts(connection)
            if not data.historical_scope_texts.empty:
                data.source_files_used.append("database: historical proposal scope text")

        data.pricing_catalog = load_current_pricing(connection, data)
        data.pricing = data.pricing_catalog
    if not data.source_files_used:
        raise RuntimeError("No estimator database tables were available.")
    data.source_files_used.append("Postgres database")
    if data.template_rows.empty:
        data.warnings.append(
            "estimate_template_rows is empty; run python -m jobscan.estimator.template_rows --parse-existing."
        )
    if data.pricing_catalog.empty:
        data.warnings.append("pricing_catalog is empty; current material pricing is limited.")
    if interactive:
        data.source_files_used.append("estimator load profile: interactive")
    return normalize_estimator_data(data)


def load_estimator_data(
    base_dir: Path | str | None = None,
    database_url: str | None = None,
    *,
    prefer_database: bool = False,
    load_profile: str = ESTIMATOR_LOAD_PROFILE_FULL,
) -> EstimatorData:
    if load_profile not in ESTIMATOR_LOAD_PROFILES:
        raise ValueError(f"Unknown estimator load profile: {load_profile}")
    root = Path(base_dir or Path.cwd())
    resolved_database_url = database_url or (
        os.getenv("NEON_DATABASE_URL") if prefer_database else os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL")
    )
    if prefer_database and not resolved_database_url:
        raise RuntimeError("Database-backed estimator data was required, but no database URL was provided.")
    if resolved_database_url:
        try:
            return load_estimator_data_from_database(resolved_database_url, load_profile=load_profile)
        except Exception as exc:
            if prefer_database:
                raise RuntimeError(f"Database estimator load failed and local fallback is disabled. ({type(exc).__name__})") from exc
            data = _load_estimator_data_from_local_files(root)
            data.warnings.insert(0, f"Database estimator load failed; using local staging files. ({type(exc).__name__})")
            return data
    return _load_estimator_data_from_local_files(root)
