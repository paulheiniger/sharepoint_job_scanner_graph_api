from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, text

from .decision_graph import build_decision_graph
from .schemas import EstimatorData


DECISION_TABLES = [
    "insulation_foam_decision_history",
    "insulation_thermal_barrier_decision_history",
    "insulation_labor_decision_history",
    "roofing_coating_decision_history",
    "roofing_scope_decision_history",
    "roofing_labor_decision_history",
    "equipment_decision_history",
]

DECISION_NUMERIC_FIELDS = [
    "selector_code",
    "area_basis_sqft",
    "thickness_inches",
    "foam_density_lb",
    "yield_or_coverage",
    "estimated_units",
    "estimated_sets",
    "gal_per_100_sqft",
    "gal_per_sqft",
    "wet_mils_estimate",
    "waste_factor_pct",
    "warranty_years",
    "crew_size",
    "crew_selector_code",
    "days",
    "total_hours",
    "daily_rate",
    "hourly_rate",
    "calculated_output",
    "estimated_cost",
    "unit_price",
]


def _normalized(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("_", " ").replace("-", " ").split())


def _safe_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else [], default=str, sort_keys=True)


def _mode(values: pd.Series | list[Any]) -> Any:
    series = pd.Series(values).dropna().astype(str).map(str.strip)
    series = series[series.ne("")]
    if series.empty:
        return None
    return series.value_counts().sort_values(ascending=False).index[0]


def _confidence(evidence_count: int) -> str:
    if evidence_count >= 25:
        return "high"
    if evidence_count >= 8:
        return "medium"
    if evidence_count > 0:
        return "low"
    return "none"


def _size_bucket(area: Any) -> str:
    value = _safe_number(area)
    if value is None or value <= 0:
        return ""
    if value < 5000:
        return "under_5k"
    if value < 15000:
        return "5k_15k"
    if value < 50000:
        return "15k_50k"
    return "50k_plus"


def _frame(data: EstimatorData | Any, attr: str) -> pd.DataFrame:
    value = getattr(data, attr, pd.DataFrame()) if data is not None else pd.DataFrame()
    return value.copy() if isinstance(value, pd.DataFrame) else pd.DataFrame(value)


def _ensure_columns(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    out = frame.copy()
    for column in columns:
        if column not in out.columns:
            out[column] = None
    return out


def _job_context(data: EstimatorData | Any) -> pd.DataFrame:
    jobs = _frame(data, "jobs")
    if jobs.empty:
        return pd.DataFrame(columns=["job_id"])
    keep = [
        "job_id",
        "source_year",
        "division",
        "project_type",
        "substrate",
        "building_type",
        "coating_type",
        "warranty_years",
        "roof_condition",
        "access_complexity",
        "penetrations_complexity",
        "pipeline_status",
        "status",
        "estimated_sqft",
        "area_sqft",
    ]
    return _ensure_columns(jobs, keep)[keep].drop_duplicates("job_id")


def _merge_job_context(rows: pd.DataFrame, data: EstimatorData | Any) -> pd.DataFrame:
    if rows.empty:
        return rows.copy()
    out = rows.copy()
    context = _job_context(data)
    if not context.empty and "job_id" in out.columns:
        out = out.merge(context, on="job_id", how="left", suffixes=("", "_job"))
        for column in [
            "source_year",
            "division",
            "project_type",
            "substrate",
            "building_type",
            "coating_type",
            "warranty_years",
            "roof_condition",
            "access_complexity",
            "penetrations_complexity",
            "pipeline_status",
            "status",
            "estimated_sqft",
            "area_sqft",
        ]:
            job_column = f"{column}_job"
            if job_column in out.columns:
                if column not in out.columns:
                    out[column] = out[job_column]
                else:
                    out[column] = out[column].where(out[column].notna() & out[column].astype(str).ne(""), out[job_column])
                out = out.drop(columns=[job_column])
    return out


def _source_year_from_file(value: Any) -> Any:
    text_value = str(value or "")
    for token in text_value.replace("\\", "/").split("/"):
        if token.isdigit() and len(token) == 4:
            return int(token)
    return None


def _base_decision_rows(data: EstimatorData | Any) -> pd.DataFrame:
    rows = _frame(data, "template_rows")
    if rows.empty:
        return pd.DataFrame()
    rows = _merge_job_context(rows, data)
    rows = _ensure_columns(
        rows,
        [
            "job_id",
            "source_file",
            "template_type",
            "division",
            "project_type",
            "substrate",
            "building_type",
            "coating_type",
            "warranty_years",
            "roof_condition",
            "access_complexity",
            "penetrations_complexity",
            "source_year",
            "template_row_id",
            "sheet_name",
            "row_number",
            "template_bucket",
            "line_item_kind",
            "selector_code",
            "resolved_item_name",
            "selected_item_name",
            "area_sqft",
            "quantity",
            "thickness_inches",
            "foam_density_lb",
            "yield_or_coverage",
            "yield_factor",
            "estimated_units",
            "estimated_sets",
            "gal_per_100_sqft",
            "gal_per_sqft",
            "estimated_gallons",
            "waste_factor_pct",
            "margin_pct",
            "unit_price",
            "estimated_cost",
            "days",
            "crew_size",
            "crew_selector_code",
            "total_hours",
            "daily_rate",
            "hourly_rate",
            "calculated_cost",
            "formula_model",
            "formula_mode",
        ],
    )
    if "source_year" in rows.columns:
        rows["source_year"] = rows["source_year"].where(rows["source_year"].notna(), rows["source_file"].map(_source_year_from_file))
    area = pd.to_numeric(rows.get("area_sqft"), errors="coerce")
    if "quantity" in rows.columns:
        quantity = pd.to_numeric(rows["quantity"], errors="coerce")
        area = area.where(area.notna() & (area > 0), quantity)
    rows["area_basis_sqft"] = area
    rows["size_bucket"] = rows["area_basis_sqft"].map(_size_bucket)
    rows["resolved_item_name"] = rows["resolved_item_name"].where(
        rows["resolved_item_name"].notna() & rows["resolved_item_name"].astype(str).ne(""),
        rows["selected_item_name"],
    )
    product_catalog = getattr(data, "product_catalog", pd.DataFrame())
    if product_catalog is not None and not product_catalog.empty:
        try:
            from jobscan.products.product_matching import match_product

            product_links = getattr(data, "product_decision_links", pd.DataFrame())
            product_ids = []
            product_match_scores = []
            for _, row in rows.iterrows():
                matched = match_product(
                    str(row.get("resolved_item_name") or ""),
                    product_catalog,
                    category=str(row.get("template_bucket") or ""),
                    product_decision_links=product_links,
                )
                product_ids.append(matched.get("product_id") if matched else "")
                product_match_scores.append(matched.get("match_score") if matched else None)
            rows["product_id"] = product_ids
            rows["product_match_score"] = product_match_scores
        except Exception:
            rows["product_id"] = ""
            rows["product_match_score"] = None
    else:
        rows["product_id"] = ""
        rows["product_match_score"] = None
    return rows


def _decision_base_output(rows: pd.DataFrame, decision_id: str, decision_node_title: str, decision_category: str) -> pd.DataFrame:
    out = rows.copy()
    out["decision_id"] = decision_id
    out["decision_node_title"] = decision_node_title
    out["decision_category"] = decision_category
    out["selected_option"] = out["resolved_item_name"]
    out["calculated_output"] = out["estimated_cost"]
    out["source_table"] = "estimate_template_rows"
    keep = [
        "decision_id",
        "decision_node_title",
        "decision_category",
        "job_id",
        "source_file",
        "source_year",
        "division",
        "template_type",
        "project_type",
        "substrate",
        "building_type",
        "coating_type",
        "warranty_years",
        "roof_condition",
        "access_complexity",
        "penetrations_complexity",
        "size_bucket",
        "template_row_id",
        "sheet_name",
        "row_number",
        "template_bucket",
        "line_item_kind",
        "selector_code",
        "product_id",
        "product_match_score",
        "selected_option",
        "resolved_item_name",
        "area_basis_sqft",
        "thickness_inches",
        "foam_density_lb",
        "yield_or_coverage",
        "yield_factor",
        "estimated_units",
        "estimated_sets",
        "gal_per_100_sqft",
        "gal_per_sqft",
        "wet_mils_estimate",
        "waste_factor_pct",
        "unit_price",
        "days",
        "crew_size",
        "crew_selector_code",
        "total_hours",
        "daily_rate",
        "hourly_rate",
        "formula_mode",
        "equipment_choice",
        "calculated_output",
        "estimated_cost",
        "source_table",
    ]
    out = _ensure_columns(out, keep)
    return out[keep].copy()


def build_historical_decision_tables(data: EstimatorData | Any) -> dict[str, pd.DataFrame]:
    existing_tables = getattr(data, "decision_history_tables", None)
    if isinstance(existing_tables, dict) and existing_tables:
        tables: dict[str, pd.DataFrame] = {}
        for table_name in DECISION_TABLES:
            frame = existing_tables.get(table_name, pd.DataFrame())
            tables[table_name] = frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame(frame)
        if any(not frame.empty for frame in tables.values()):
            return tables

    rows = _base_decision_rows(data)
    if rows.empty:
        return {name: pd.DataFrame() for name in DECISION_TABLES}
    rows["wet_mils_estimate"] = pd.to_numeric(rows["gal_per_100_sqft"], errors="coerce") * 16
    rows["equipment_choice"] = rows["resolved_item_name"]

    template_type = rows["template_type"].map(_normalized)
    bucket = rows["template_bucket"].map(_normalized)
    formula_model = rows["formula_model"].map(_normalized)
    kind = rows["line_item_kind"].map(_normalized)

    insulation_foam = rows[(template_type.eq("insulation")) & (bucket.eq("foam"))].copy()
    insulation_thermal = rows[(template_type.eq("insulation")) & (bucket.eq("thermal barrier coating"))].copy()
    if insulation_thermal.empty:
        insulation_thermal = rows[(template_type.eq("insulation")) & (formula_model.eq("coating gallons from area rate waste"))].copy()
    insulation_labor = rows[(template_type.eq("insulation")) & ((kind.eq("labor")) | bucket.str.startswith("labor"))].copy()
    roofing_coating = rows[(template_type.eq("roofing")) & (bucket.eq("coating"))].copy()
    roofing_scope = rows[(template_type.eq("roofing")) & (bucket.isin({"coating", "foam", "primer"}))].copy()
    roofing_labor = rows[(template_type.eq("roofing")) & ((kind.eq("labor")) | bucket.str.startswith("labor"))].copy()
    equipment = rows[
        kind.eq("equipment")
        | bucket.isin(
            {
                "lift",
                "generator",
                "space heater",
                "dumpsters",
                "dumpster",
                "drum disposal",
                "truck expense",
                "delivery fee",
                "freight",
            }
        )
    ].copy()

    return {
        "insulation_foam_decision_history": _decision_base_output(
            insulation_foam, "insulation_foam_system", "Insulation Foam System", "product_selection"
        ),
        "insulation_thermal_barrier_decision_history": _decision_base_output(
            insulation_thermal, "insulation_thermal_barrier", "Insulation Thermal Barrier / DC315", "product_selection"
        ),
        "insulation_labor_decision_history": _decision_base_output(
            insulation_labor, "insulation_labor", "Insulation Labor", "labor_planning"
        ),
        "roofing_coating_decision_history": _decision_base_output(
            roofing_coating, "roofing_coating_system", "Roofing Coating System", "product_selection"
        ),
        "roofing_scope_decision_history": _decision_base_output(
            roofing_scope, "roofing_scope", "Roofing Scope Decisions", "scope_decision"
        ),
        "roofing_labor_decision_history": _decision_base_output(
            roofing_labor, "roofing_labor", "Roofing Labor", "labor_planning"
        ),
        "equipment_decision_history": _decision_base_output(
            equipment, "equipment_selection", "Equipment / Adders", "equipment_selection"
        ),
    }


def _apply_filters(rows: pd.DataFrame, filters: dict[str, Any] | None) -> tuple[pd.DataFrame, list[str]]:
    filters = dict(filters or {})
    if "area_bucket" in filters and "size_bucket" not in filters:
        filters["size_bucket"] = filters.get("area_bucket")
    filtered = rows.copy()
    applied: list[str] = []
    for field in [
        "division",
        "template_type",
        "project_type",
        "substrate",
        "building_type",
        "coating_type",
        "warranty_years",
        "roof_condition",
        "access_complexity",
        "penetrations_complexity",
        "size_bucket",
        "source_year",
    ]:
        value = filters.get(field)
        if value in (None, "") or field not in filtered.columns or filtered.empty:
            continue
        if field in {"warranty_years", "source_year"}:
            expected = _safe_number(value)
            if expected is None:
                continue
            numeric = pd.to_numeric(filtered[field], errors="coerce")
            candidate = filtered[numeric.notna() & numeric.round(4).eq(float(expected))].copy()
        else:
            expected_text = _normalized(value)
            candidate = filtered[
                filtered[field].map(_normalized).eq(expected_text)
                | filtered[field].map(_normalized).str.contains(expected_text, na=False)
            ].copy()
        if not candidate.empty:
            filtered = candidate
            applied.append(field)
    return filtered, applied


def _relaxed_recommendation_rows(rows: pd.DataFrame, filters: dict[str, Any] | None, min_count: int = 3) -> tuple[pd.DataFrame, list[str], list[str]]:
    filtered, applied = _apply_filters(rows, filters)
    relaxed: list[str] = []
    if _source_jobs_count(filtered) >= min_count:
        return filtered, applied, relaxed
    filters = dict(filters or {})
    if "area_bucket" in filters and "size_bucket" not in filters:
        filters["size_bucket"] = filters.get("area_bucket")
    for field in [
        "penetrations_complexity",
        "access_complexity",
        "roof_condition",
        "source_year",
        "warranty_years",
        "coating_type",
        "size_bucket",
        "area_bucket",
        "substrate",
        "building_type",
        "project_type",
    ]:
        if field not in filters or filters.get(field) in (None, ""):
            continue
        filters.pop(field, None)
        relaxed.append(field)
        filtered, applied = _apply_filters(rows, filters)
        if _source_jobs_count(filtered) >= min_count:
            break
    return filtered, applied, relaxed


def _source_jobs_count(rows: pd.DataFrame) -> int:
    if rows.empty:
        return 0
    if "job_id" in rows.columns:
        return int(rows["job_id"].dropna().astype(str).nunique())
    return int(len(rows))


def _numeric_distribution(rows: pd.DataFrame, field: str) -> dict[str, Any]:
    if field not in rows.columns:
        return {"p25": None, "median": None, "p75": None, "evidence_count": 0}
    values = pd.to_numeric(rows[field], errors="coerce")
    values = values[values.notna()]
    if values.empty:
        return {"p25": None, "median": None, "p75": None, "evidence_count": 0}
    return {
        "p25": float(values.quantile(0.25)),
        "median": float(values.quantile(0.5)),
        "p75": float(values.quantile(0.75)),
        "evidence_count": int(values.count()),
    }


def _recommendation_for_field(
    rows: pd.DataFrame,
    *,
    decision_id: str,
    field: str,
    filters: dict[str, Any] | None = None,
    min_count: int = 3,
) -> dict[str, Any]:
    filtered, applied, relaxed = _relaxed_recommendation_rows(rows, filters, min_count)
    if filtered.empty:
        return {
            "decision_id": decision_id,
            "field_name": field,
            "recommended_value": None,
            "evidence_count": 0,
            "source_jobs_count": 0,
            "confidence": "none",
            "review_warning": "No historical decision evidence found.",
            "filters_applied": ",".join(applied),
            "filters_relaxed": ",".join(relaxed),
        }
    numeric = _numeric_distribution(filtered, field)
    if numeric["evidence_count"] > 0:
        recommended = numeric["median"]
        evidence_count = numeric["evidence_count"]
        mode_value = None
        p25 = numeric["p25"]
        median = numeric["median"]
        p75 = numeric["p75"]
    else:
        mode_value = _mode(filtered[field]) if field in filtered.columns else None
        recommended = mode_value
        evidence_count = int(filtered[field].dropna().astype(str).map(str.strip).ne("").sum()) if field in filtered.columns else 0
        p25 = median = p75 = None
    source_jobs = _source_jobs_count(filtered)
    confidence = _confidence(min(evidence_count, source_jobs if source_jobs else evidence_count))
    warning = ""
    if confidence in {"none", "low"}:
        warning = "Low historical decision evidence; estimator review required."
    elif relaxed:
        warning = f"Filters relaxed: {', '.join(relaxed)}."
    return {
        "decision_id": decision_id,
        "field_name": field,
        "recommended_value": recommended,
        "evidence_count": int(evidence_count),
        "p25": p25,
        "median": median,
        "p75": p75,
        "mode": mode_value,
        "confidence": confidence,
        "review_warning": warning,
        "source_jobs_count": source_jobs,
        "filters_applied": ",".join(applied),
        "filters_relaxed": ",".join(relaxed),
    }


def build_decision_recommendations(data: EstimatorData | Any, filters: dict[str, Any] | None = None, min_count: int = 3) -> pd.DataFrame:
    requested_min_count = _safe_number((filters or {}).get("min_evidence_count"))
    if requested_min_count is not None and requested_min_count > 0:
        min_count = int(requested_min_count)
    tables = build_historical_decision_tables(data)
    if not any(not frame.empty for frame in tables.values()):
        existing_recommendations = getattr(data, "estimator_decision_recommendations", pd.DataFrame())
        if isinstance(existing_recommendations, pd.DataFrame) and not existing_recommendations.empty:
            return existing_recommendations.copy()
    specs = [
        ("insulation_foam_decision_history", "insulation_foam_system", ["resolved_item_name", "thickness_inches", "yield_or_coverage", "foam_density_lb"]),
        ("insulation_thermal_barrier_decision_history", "insulation_thermal_barrier", ["resolved_item_name", "gal_per_100_sqft", "gal_per_sqft"]),
        ("roofing_coating_decision_history", "roofing_coating_system", ["resolved_item_name", "warranty_years", "wet_mils_estimate", "gal_per_100_sqft", "gal_per_sqft", "waste_factor_pct"]),
        ("roofing_scope_decision_history", "roofing_scope", ["warranty_years", "area_basis_sqft", "coating_type"]),
    ]
    rows: list[dict[str, Any]] = []
    for table_name, decision_id, fields in specs:
        frame = tables.get(table_name, pd.DataFrame())
        if frame.empty:
            continue
        for field in fields:
            if field not in frame.columns:
                continue
            rec = _recommendation_for_field(frame, decision_id=decision_id, field=field, filters=filters, min_count=min_count)
            rec["history_table"] = table_name
            rows.append(rec)
    for table_name, prefix in (
        ("insulation_labor_decision_history", "insulation"),
        ("roofing_labor_decision_history", "roofing"),
    ):
        frame = tables.get(table_name, pd.DataFrame())
        if frame.empty or "template_bucket" not in frame.columns:
            continue
        for package, package_rows in frame.groupby(frame["template_bucket"].fillna("").astype(str), dropna=False):
            if not package:
                continue
            decision_id = f"{prefix}_{package}"
            for field in ["days", "crew_size", "crew_selector_code", "daily_rate", "hourly_rate", "formula_mode"]:
                rec = _recommendation_for_field(package_rows, decision_id=decision_id, field=field, filters=filters, min_count=min_count)
                rec["history_table"] = table_name
                rec["template_bucket"] = package
                rows.append(rec)
    equipment = tables.get("equipment_decision_history", pd.DataFrame())
    if not equipment.empty and "template_bucket" in equipment.columns:
        for package, package_rows in equipment.groupby(equipment["template_bucket"].fillna("").astype(str), dropna=False):
            if not package:
                continue
            decision_id = f"equipment_{package}"
            for field in ["equipment_choice", "selector_code", "unit_price"]:
                rec = _recommendation_for_field(package_rows, decision_id=decision_id, field=field, filters=filters, min_count=min_count)
                rec["history_table"] = "equipment_decision_history"
                rec["template_bucket"] = package
                rows.append(rec)
    return pd.DataFrame(rows)


def recommendation_lookup(recommendations: pd.DataFrame) -> dict[tuple[str, str], dict[str, Any]]:
    if recommendations is None or recommendations.empty:
        return {}
    return {
        (str(row.get("decision_id") or ""), str(row.get("field_name") or "")): row.to_dict()
        for _, row in recommendations.iterrows()
    }


def write_decision_history_tables(engine: Any, tables: dict[str, pd.DataFrame], recommendations: pd.DataFrame | None = None) -> None:
    with engine.begin() as connection:
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS analytics"))
    for table_name, frame in tables.items():
        frame.to_sql(table_name, engine, schema="analytics", if_exists="replace", index=False, chunksize=1000)
    if recommendations is not None:
        recommendations.to_sql("estimator_decision_recommendations", engine, schema="analytics", if_exists="replace", index=False, chunksize=1000)


def write_decision_history_outputs(
    tables: dict[str, pd.DataFrame],
    recommendations: pd.DataFrame,
    out_dir: str | Path,
) -> Path:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    xlsx_path = out_path / "historical_decision_mining.xlsx"
    with pd.ExcelWriter(xlsx_path) as writer:
        recommendations.to_excel(writer, sheet_name="recommendations", index=False)
        for table_name, frame in tables.items():
            frame.to_excel(writer, sheet_name=table_name[:31], index=False)
    return xlsx_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mine historical estimator decisions from parsed template rows.")
    parser.add_argument("--db-url", default="", help="Optional database URL. When provided, reads estimate data from Neon/Postgres.")
    parser.add_argument("--out-dir", default="output", help="Output directory for review workbook.")
    parser.add_argument("--write-db", action="store_true", help="Write decision history tables to analytics schema.")
    parser.add_argument("--insulation-graph", default="output/template_decision_graph_insulation.json", help="Decision graph JSON path, loaded for validation/context.")
    parser.add_argument("--roofing-graph", default="output/template_decision_graph_roofing.json", help="Decision graph JSON path, loaded for validation/context.")
    parser.add_argument("--min-count", type=int, default=3, help="Minimum source jobs before filter relaxation.")
    args = parser.parse_args(argv)

    # Load graphs to fail early when the graph layer is missing/stale. The miner uses parsed row data as the source of truth.
    for path, template_type in ((args.insulation_graph, "insulation"), (args.roofing_graph, "roofing")):
        graph_path = Path(path)
        if graph_path.exists():
            graph_payload = json.loads(graph_path.read_text(encoding="utf-8"))
            if "decision_nodes" not in graph_payload and "template_type" in graph_payload:
                build_decision_graph(graph_payload)

    if args.db_url:
        from .data_loader import load_estimator_data

        data = load_estimator_data(database_url=args.db_url, prefer_database=True)
        engine = create_engine(args.db_url)
    else:
        data = EstimatorData()
        engine = None
    tables = build_historical_decision_tables(data)
    recommendations = build_decision_recommendations(data, min_count=args.min_count)
    xlsx_path = write_decision_history_outputs(tables, recommendations, args.out_dir)
    if args.write_db:
        if engine is None:
            raise SystemExit("--write-db requires --db-url")
        write_decision_history_tables(engine, tables, recommendations)
    print(f"Wrote historical decision mining workbook: {xlsx_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
