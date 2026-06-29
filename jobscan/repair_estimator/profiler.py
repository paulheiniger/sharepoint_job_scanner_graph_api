from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from .vsimple_loader import RepairTables, load_vsimple_repair_export, write_repair_tables_to_database


def to_number_series(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def confidence(count: int) -> str:
    if count >= 20:
        return "high"
    if count >= 8:
        return "medium"
    return "low"


def safe_median(series: pd.Series) -> float | None:
    values = to_number_series(series).dropna()
    if values.empty:
        return None
    return float(values.median())


def safe_quantile(series: pd.Series, quantile: float) -> float | None:
    values = to_number_series(series).dropna()
    if values.empty:
        return None
    return float(values.quantile(quantile))


def read_table_csv(input_dir: Path, table_name: str) -> pd.DataFrame:
    path = input_dir / f"{table_name}.csv"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def load_tables_from_csv_dir(input_dir: Path | str) -> RepairTables:
    root = Path(input_dir)
    return RepairTables(
        repair_jobs=read_table_csv(root, "repair_jobs"),
        repair_material_usage=read_table_csv(root, "repair_material_usage"),
        repair_labor_usage=read_table_csv(root, "repair_labor_usage"),
        repair_scope_text=read_table_csv(root, "repair_scope_text"),
        repair_outcomes=read_table_csv(root, "repair_outcomes"),
    )


def load_tables_from_database(engine: Engine) -> RepairTables:
    return RepairTables(
        repair_jobs=pd.read_sql_table("repair_jobs", engine),
        repair_material_usage=pd.read_sql_table("repair_material_usage", engine),
        repair_labor_usage=pd.read_sql_table("repair_labor_usage", engine),
        repair_scope_text=pd.read_sql_table("repair_scope_text", engine),
        repair_outcomes=pd.read_sql_table("repair_outcomes", engine),
    )


def base_repair_frame(tables: RepairTables) -> pd.DataFrame:
    jobs = tables.repair_jobs.copy()
    outcomes = tables.repair_outcomes.copy()
    labor = tables.repair_labor_usage.copy()
    scope = tables.repair_scope_text.copy()
    if jobs.empty:
        return pd.DataFrame()
    for frame in [outcomes, labor, scope]:
        if "repair_id" not in frame.columns:
            frame["repair_id"] = None
    labor_agg = pd.DataFrame(columns=["repair_id", "total_labor_hours", "labor_cost"])
    if not labor.empty:
        aggregate = labor[labor.get("labor_role", "") == "aggregate"].copy() if "labor_role" in labor.columns else labor.copy()
        if not aggregate.empty:
            aggregate["total_labor_hours"] = to_number_series(aggregate.get("total_labor_hours", aggregate.get("labor_hours")))
            aggregate["labor_cost"] = to_number_series(aggregate.get("labor_cost"))
            labor_agg = aggregate.groupby("repair_id", dropna=False).agg(
                total_labor_hours=("total_labor_hours", "max"),
                labor_cost=("labor_cost", "sum"),
            ).reset_index()
    merged = jobs.merge(outcomes, on="repair_id", how="left", suffixes=("", "_outcome"))
    merged = merged.merge(labor_agg, on="repair_id", how="left", suffixes=("", "_labor"))
    scope_cols = [column for column in ["repair_id", "work_phrase_patterns", "combined_scope_text"] if column in scope.columns]
    if scope_cols:
        merged = merged.merge(scope[scope_cols], on="repair_id", how="left")
    for column in ["invoice_amount", "total_bill_amount", "gross_profit", "total_labor_hours", "labor_cost"]:
        if column in merged.columns:
            merged[column] = to_number_series(merged[column])
    return merged


def build_repair_type_profile(base: pd.DataFrame, min_job_count: int = 1) -> pd.DataFrame:
    columns = [
        "type_of_repair",
        "roof_type",
        "repair_count",
        "median_labor_hours",
        "p75_labor_hours",
        "median_invoice_amount",
        "p75_invoice_amount",
        "median_gross_profit",
        "common_work_phrase_patterns",
        "confidence",
    ]
    if base.empty:
        return pd.DataFrame(columns=columns)
    frame = base.copy()
    frame["type_of_repair"] = frame.get("type_of_repair", "").fillna("").replace("", "unknown")
    frame["roof_type"] = frame.get("roof_type", "").fillna("").replace("", "unknown")
    rows: list[dict[str, Any]] = []
    for (repair_type, roof_type), group in frame.groupby(["type_of_repair", "roof_type"], dropna=False):
        count = int(group["repair_id"].nunique())
        if count < min_job_count:
            continue
        phrases: dict[str, int] = {}
        for value in group.get("work_phrase_patterns", pd.Series(dtype=str)).fillna("[]"):
            try:
                parsed = json.loads(value) if isinstance(value, str) else []
            except json.JSONDecodeError:
                parsed = []
            for phrase in parsed:
                phrases[phrase] = phrases.get(phrase, 0) + 1
        common_phrases = sorted(phrases, key=lambda phrase: (-phrases[phrase], phrase))[:8]
        rows.append(
            {
                "type_of_repair": repair_type,
                "roof_type": roof_type,
                "repair_count": count,
                "median_labor_hours": safe_median(group["total_labor_hours"]),
                "p75_labor_hours": safe_quantile(group["total_labor_hours"], 0.75),
                "median_invoice_amount": safe_median(group["invoice_amount"]),
                "p75_invoice_amount": safe_quantile(group["invoice_amount"], 0.75),
                "median_gross_profit": safe_median(group["gross_profit"]),
                "common_work_phrase_patterns": json.dumps(common_phrases),
                "confidence": confidence(count),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def build_material_package_profile(tables: RepairTables, min_job_count: int = 1) -> pd.DataFrame:
    columns = [
        "type_of_repair",
        "roof_type",
        "material_package",
        "repair_count",
        "usage_count",
        "median_quantity",
        "median_total_cost",
        "p75_total_cost",
        "common_material_names",
        "confidence",
    ]
    materials = tables.repair_material_usage.copy()
    jobs = tables.repair_jobs.copy()
    if materials.empty or jobs.empty:
        return pd.DataFrame(columns=columns)
    frame = materials.merge(jobs[["repair_id", "type_of_repair", "roof_type"]], on="repair_id", how="left")
    frame["type_of_repair"] = frame.get("type_of_repair", "").fillna("").replace("", "unknown")
    frame["roof_type"] = frame.get("roof_type", "").fillna("").replace("", "unknown")
    frame["material_package"] = frame.get("material_package", "").fillna("").replace("", "misc_material")
    frame["quantity"] = to_number_series(frame.get("quantity"))
    frame["total_cost"] = to_number_series(frame.get("total_cost"))
    rows: list[dict[str, Any]] = []
    for keys, group in frame.groupby(["type_of_repair", "roof_type", "material_package"], dropna=False):
        repair_count = int(group["repair_id"].nunique())
        if repair_count < min_job_count:
            continue
        common_names = (
            group["material_name"]
            .fillna("")
            .astype(str)
            .str.strip()
            .replace("", pd.NA)
            .dropna()
            .value_counts()
            .head(8)
            .index.tolist()
        )
        rows.append(
            {
                "type_of_repair": keys[0],
                "roof_type": keys[1],
                "material_package": keys[2],
                "repair_count": repair_count,
                "usage_count": int(len(group)),
                "median_quantity": safe_median(group["quantity"]),
                "median_total_cost": safe_median(group["total_cost"]),
                "p75_total_cost": safe_quantile(group["total_cost"], 0.75),
                "common_material_names": json.dumps(common_names),
                "confidence": confidence(repair_count),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def build_work_phrase_profile(base: pd.DataFrame, min_job_count: int = 1) -> pd.DataFrame:
    columns = [
        "work_phrase_pattern",
        "type_of_repair",
        "roof_type",
        "repair_count",
        "median_labor_hours",
        "median_invoice_amount",
        "confidence",
    ]
    if base.empty or "work_phrase_patterns" not in base.columns:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, Any]] = []
    exploded_rows: list[dict[str, Any]] = []
    for _, row in base.iterrows():
        try:
            phrases = json.loads(row.get("work_phrase_patterns") or "[]")
        except json.JSONDecodeError:
            phrases = []
        for phrase in phrases:
            record = row.to_dict()
            record["work_phrase_pattern"] = phrase
            exploded_rows.append(record)
    if not exploded_rows:
        return pd.DataFrame(columns=columns)
    exploded = pd.DataFrame(exploded_rows)
    exploded["type_of_repair"] = exploded.get("type_of_repair", "").fillna("").replace("", "unknown")
    exploded["roof_type"] = exploded.get("roof_type", "").fillna("").replace("", "unknown")
    for keys, group in exploded.groupby(["work_phrase_pattern", "type_of_repair", "roof_type"], dropna=False):
        count = int(group["repair_id"].nunique())
        if count < min_job_count:
            continue
        rows.append(
            {
                "work_phrase_pattern": keys[0],
                "type_of_repair": keys[1],
                "roof_type": keys[2],
                "repair_count": count,
                "median_labor_hours": safe_median(group["total_labor_hours"]),
                "median_invoice_amount": safe_median(group["invoice_amount"]),
                "confidence": confidence(count),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def build_rule_suggestions(
    repair_type_profile: pd.DataFrame,
    material_profile: pd.DataFrame,
    phrase_profile: pd.DataFrame,
) -> dict[str, Any]:
    suggestions: dict[str, Any] = {
        "repair_type_defaults": [],
        "material_package_defaults": [],
        "work_phrase_patterns": [],
        "diagnostics": [],
    }
    if not repair_type_profile.empty:
        top = repair_type_profile.sort_values(["repair_count", "median_invoice_amount"], ascending=[False, False]).head(50)
        for _, row in top.iterrows():
            suggestions["repair_type_defaults"].append(
                {
                    "type_of_repair": row.get("type_of_repair"),
                    "roof_type": row.get("roof_type"),
                    "median_labor_hours": row.get("median_labor_hours"),
                    "median_invoice_amount": row.get("median_invoice_amount"),
                    "repair_count": int(row.get("repair_count") or 0),
                    "confidence": row.get("confidence"),
                }
            )
    else:
        suggestions["diagnostics"].append("No repair type profile rows were available.")
    if not material_profile.empty:
        top = material_profile.sort_values(["repair_count", "usage_count"], ascending=False).head(100)
        for _, row in top.iterrows():
            suggestions["material_package_defaults"].append(
                {
                    "type_of_repair": row.get("type_of_repair"),
                    "roof_type": row.get("roof_type"),
                    "material_package": row.get("material_package"),
                    "median_quantity": row.get("median_quantity"),
                    "median_total_cost": row.get("median_total_cost"),
                    "repair_count": int(row.get("repair_count") or 0),
                    "confidence": row.get("confidence"),
                }
            )
    else:
        suggestions["diagnostics"].append("No material package profile rows were available.")
    if not phrase_profile.empty:
        top = phrase_profile.sort_values(["repair_count", "median_invoice_amount"], ascending=[False, False]).head(100)
        for _, row in top.iterrows():
            suggestions["work_phrase_patterns"].append(
                {
                    "work_phrase_pattern": row.get("work_phrase_pattern"),
                    "type_of_repair": row.get("type_of_repair"),
                    "roof_type": row.get("roof_type"),
                    "median_labor_hours": row.get("median_labor_hours"),
                    "median_invoice_amount": row.get("median_invoice_amount"),
                    "repair_count": int(row.get("repair_count") or 0),
                    "confidence": row.get("confidence"),
                }
            )
    else:
        suggestions["diagnostics"].append("No work phrase profile rows were available.")
    return suggestions


def profile_repairs(
    tables: RepairTables,
    output_dir: Path | str,
    *,
    min_job_count: int = 1,
) -> dict[str, Path]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = base_repair_frame(tables)
    repair_type_profile = build_repair_type_profile(base, min_job_count=min_job_count)
    material_profile = build_material_package_profile(tables, min_job_count=min_job_count)
    phrase_profile = build_work_phrase_profile(base, min_job_count=min_job_count)
    suggestions = build_rule_suggestions(repair_type_profile, material_profile, phrase_profile)

    outputs = {
        "repair_profile_summary.csv": repair_type_profile,
        "repair_material_package_profile.csv": material_profile,
        "repair_work_phrase_profile.csv": phrase_profile,
    }
    paths: dict[str, Path] = {}
    for filename, frame in outputs.items():
        path = out_dir / filename
        frame.to_csv(path, index=False)
        paths[filename] = path
    suggestions_path = out_dir / "repair_estimator_rule_suggestions.json"
    suggestions_path.write_text(json.dumps(suggestions, indent=2, default=str), encoding="utf-8")
    paths["repair_estimator_rule_suggestions.json"] = suggestions_path
    return paths


def write_profiles_to_database(engine: Engine, profile_paths: dict[str, Path], *, if_exists: str = "replace") -> None:
    table_map = {
        "repair_profile_summary.csv": "repair_profile_summary",
        "repair_material_package_profile.csv": "repair_material_package_profile",
        "repair_work_phrase_profile.csv": "repair_work_phrase_profile",
    }
    inspector = inspect(engine)
    for filename, table_name in table_map.items():
        path = profile_paths.get(filename)
        if path and path.exists():
            sql_mode = if_exists
            if if_exists == "replace" and inspector.has_table(table_name):
                with engine.begin() as connection:
                    connection.execute(text(f"DELETE FROM {table_name}"))
                sql_mode = "append"
            pd.read_csv(path).to_sql(table_name, engine, if_exists=sql_mode, index=False, chunksize=1000)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Profile normalized VSimple repair history for repair-estimator calibration.")
    parser.add_argument("--input", type=Path, default=None, help="Optional VSimple XLSX export to parse before profiling.")
    parser.add_argument("--input-dir", type=Path, default=Path("output/repair_estimator"), help="Directory containing normalized repair CSV tables.")
    parser.add_argument("--output-dir", "--out-dir", dest="output_dir", type=Path, default=Path("output/repair_estimator/profile"), help="Directory for profiler outputs.")
    parser.add_argument("--db-url", default=None, help="Optional database URL for reading/writing repair tables.")
    parser.add_argument("--min-job-count", type=int, default=1, help="Minimum repairs required for profile rows.")
    parser.add_argument("--if-exists", choices=["replace", "append", "fail"], default="replace", help="Database to_sql if_exists behavior.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    args = parse_args(argv)
    engine = create_engine(args.db_url) if args.db_url else None
    if args.input:
        tables = load_vsimple_repair_export(args.input)
        if engine is not None:
            write_repair_tables_to_database(tables, engine, if_exists=args.if_exists)
    elif engine is not None:
        tables = load_tables_from_database(engine)
    else:
        tables = load_tables_from_csv_dir(args.input_dir)
    paths = profile_repairs(tables, args.output_dir, min_job_count=max(args.min_job_count, 1))
    if engine is not None:
        write_profiles_to_database(engine, paths, if_exists=args.if_exists)
    print(f"Wrote repair profiler outputs to {args.output_dir}")
    for name, path in paths.items():
        print(f"- {name}: {path}")


if __name__ == "__main__":
    main()
