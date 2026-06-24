from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from itertools import combinations
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine


PHYSICAL_UNITS = {
    "gal",
    "gallon",
    "gallons",
    "pail",
    "pails",
    "drum",
    "drums",
    "lf",
    "ln ft",
    "linear ft",
    "linear feet",
    "sqft",
    "sq ft",
    "sf",
    "ea",
    "each",
    "unit",
    "roll",
    "rolls",
    "case",
    "cases",
    "bag",
    "bags",
    "board",
    "boards",
}

ALLOWANCE_UNITS = {"allowance", "ls", "lump sum", "lot", "sum"}
PROFILER_PARSER_VERSION = "relationship-profiler-v1"


def read_csv_if_exists(path: Path | None) -> pd.DataFrame:
    if not path or not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def first_existing(df: pd.DataFrame, names: list[str]) -> str | None:
    lower_map = {column.lower(): column for column in df.columns}
    for name in names:
        if name in df.columns:
            return name
        if name.lower() in lower_map:
            return lower_map[name.lower()]
    return None


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def to_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    text = str(value).strip().replace(",", "").replace("$", "").replace("%", "")
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def confidence(job_count: int) -> str:
    if job_count >= 10:
        return "high"
    if job_count >= 4:
        return "medium"
    return "low"


def canonical_unit(value: Any) -> str:
    text = clean_text(value).lower()
    replacements = {
        "gals": "gal",
        "gallon": "gal",
        "gallons": "gal",
        "lineal feet": "lf",
        "linear feet": "lf",
        "linear ft": "lf",
        "ln ft": "lf",
        "sq ft": "sqft",
        "sf": "sqft",
        "square feet": "sqft",
        "each": "ea",
    }
    return replacements.get(text, text)


def normalize_jobs(job_csv: Path | None, estimate_csv: Path | None) -> pd.DataFrame:
    estimates = read_csv_if_exists(estimate_csv)
    jobs = read_csv_if_exists(job_csv)
    source = estimates if not estimates.empty else jobs
    if source.empty:
        return pd.DataFrame()
    df = source.copy()
    rename = {
        first_existing(df, ["estimated_sqft", "surface_area_sqft", "area_sqft"]): "area_sqft",
        first_existing(df, ["warranty_years", "warranty_target", "warranty_target_years"]): "warranty_years",
        first_existing(df, ["final_price", "worksheet_price", "estimated_value"]): "final_price",
    }
    rename = {key: value for key, value in rename.items() if key and key != value}
    df = df.rename(columns=rename)
    for column in ["area_sqft", "warranty_years", "final_price", "price_per_sqft"]:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    for column in ["job_id", "project_type", "job_type", "substrate", "coating_type", "roof_condition", "access_complexity"]:
        if column not in df.columns:
            df[column] = ""
    if "project_type" not in df.columns or df["project_type"].fillna("").astype(str).str.strip().eq("").all():
        df["project_type"] = df.get("job_type", "")
    df["project_type"] = df["project_type"].fillna(df.get("job_type", "")).astype(str).replace("", "unknown")
    df["substrate"] = df["substrate"].fillna("").astype(str).replace("", "unknown")
    df["coating_type"] = df["coating_type"].fillna("").astype(str).replace("", "unknown")
    df["area_bucket"] = df["area_sqft"].apply(area_bucket) if "area_sqft" in df.columns else "unknown"
    return df


def area_bucket(area: Any) -> str:
    value = to_number(area)
    if value is None or value <= 0:
        return "unknown"
    if value < 5000:
        return "small"
    if value < 20000:
        return "medium"
    if value < 60000:
        return "large"
    return "very_large"


def row_text(row: pd.Series) -> str:
    parts = []
    for column in ("item_name", "line_item_name", "description", "category", "line_item_category", "section", "notes", "labor_package"):
        if column in row.index:
            parts.append(clean_text(row.get(column)))
    return " ".join(parts).lower()


def classify_package(row: pd.Series) -> str:
    text = row_text(row)
    category = clean_text(row.get("category") or row.get("line_item_category")).lower()
    section = clean_text(row.get("section")).lower()
    if "labor" in category or "labor" in section:
        return clean_text(row.get("labor_package")) or "labor"
    if any(term in text for term in ("primer", "prime coat", "epoxy prime")):
        return "primer"
    if any(term in text for term in ("seam", "butter grade", "fabric", "detail tape")):
        return "seam_treatment"
    if any(term in text for term in ("fastener", "screw", "washer")):
        return "fastener_treatment"
    if any(term in text for term in ("caulk", "sealant", "penetration", "curb", "drain", "detail")):
        return "caulk_detail"
    if any(term in text for term in ("silicone", "acrylic", "urethane", "coating", "top coat", "base coat")):
        return "coating"
    if any(term in text for term in ("foam", "spf", "polyurethane")):
        return "foam"
    if any(term in text for term in ("lift", "rental", "equipment")):
        return "equipment"
    if any(term in text for term in ("travel", "lodging", "mileage", "freight", "delivery")):
        return "travel"
    if any(term in text for term in ("warranty", "bond", "insurance")):
        return "warranty_insurance"
    if category:
        return category.replace(" ", "_")
    return "other"


def normalize_line_items(path: Path | None) -> pd.DataFrame:
    df = read_csv_if_exists(path)
    if df.empty:
        return df
    df = df.copy()
    rename = {
        first_existing(df, ["line_item_name", "item_name", "selected_item_name"]): "item_name",
        first_existing(df, ["line_item_category", "category"]): "category",
        first_existing(df, ["unit_cost", "unit_price"]): "unit_cost",
        first_existing(df, ["extended_cost", "total_cost", "estimated_cost"]): "total_cost",
        first_existing(df, ["labor_hours", "total_hours"]): "labor_hours",
        first_existing(df, ["labor_days", "days"]): "labor_days",
    }
    rename = {key: value for key, value in rename.items() if key and key != value}
    df = df.rename(columns=rename)
    for column in ["quantity", "unit_cost", "total_cost", "labor_hours", "labor_days", "crew_size"]:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    if "unit" in df.columns:
        df["unit"] = df["unit"].apply(canonical_unit)
    else:
        df["unit"] = ""
    if "job_id" not in df.columns:
        df["job_id"] = ""
    df["package"] = df.apply(classify_package, axis=1)
    df["is_labor"] = df.apply(is_labor_row, axis=1)
    df["is_material"] = ~df["is_labor"]
    return df


def is_labor_row(row: pd.Series) -> bool:
    category = clean_text(row.get("category")).lower()
    section = clean_text(row.get("section")).lower()
    return (
        "labor" in category
        or "labor" in section
        or to_number(row.get("labor_hours")) is not None
        or to_number(row.get("labor_days")) is not None
    )


def merge_job_context(rows: pd.DataFrame, jobs: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return rows
    if jobs.empty or "job_id" not in jobs.columns:
        rows = rows.copy()
        for column in ["project_type", "substrate", "coating_type", "warranty_years", "area_sqft", "area_bucket", "roof_condition", "access_complexity"]:
            rows[column] = rows.get(column, "unknown")
        return rows
    keep = [
        column
        for column in [
            "job_id",
            "project_type",
            "substrate",
            "coating_type",
            "warranty_years",
            "area_sqft",
            "area_bucket",
            "roof_condition",
            "access_complexity",
            "final_price",
        ]
        if column in jobs.columns
    ]
    merged = rows.merge(jobs[keep].drop_duplicates("job_id"), on="job_id", how="left", suffixes=("", "_job"))
    for column in ["project_type", "substrate", "coating_type", "area_bucket"]:
        if column in merged.columns:
            merged[column] = merged[column].fillna("unknown").astype(str).replace("", "unknown")
    if "warranty_years" in merged.columns:
        merged["warranty_years"] = pd.to_numeric(merged["warranty_years"], errors="coerce")
    return merged


def infer_wet_mils(warranty_years: Any, coating_type: Any) -> float | None:
    warranty = to_number(warranty_years)
    coating = clean_text(coating_type).lower()
    if warranty is None:
        return None
    if warranty >= 20:
        return 30.0 if "silicone" in coating else 36.0
    if warranty >= 15:
        return 26.0 if "silicone" in coating else 32.0
    if warranty >= 10:
        return 24.0 if "silicone" in coating else 30.0
    return 22.0


def percentile(values: pd.Series, q: float) -> float | None:
    values = pd.to_numeric(values, errors="coerce").dropna()
    values = values[values > 0]
    if values.empty:
        return None
    return round(float(values.quantile(q)), 6)


def build_warranty_coating(materials: pd.DataFrame) -> pd.DataFrame:
    if materials.empty or not {"package", "quantity", "unit", "area_sqft"}.issubset(materials.columns):
        return pd.DataFrame(columns=["coating_type", "warranty_years", "wet_mils", "waste_factor", "avg_gal_per_sqft", "median_gal_per_sqft", "job_count", "confidence"])
    coatings = materials[(materials.get("package") == "coating") & (materials.get("quantity").notna())].copy()
    coatings = coatings[coatings["unit"].isin({"gal"})]
    coatings = coatings[pd.to_numeric(coatings.get("area_sqft"), errors="coerce") > 0]
    if coatings.empty:
        return pd.DataFrame(columns=["coating_type", "warranty_years", "wet_mils", "waste_factor", "avg_gal_per_sqft", "median_gal_per_sqft", "job_count", "confidence"])
    coatings["gal_per_sqft"] = coatings["quantity"] / coatings["area_sqft"]
    coatings["wet_mils"] = coatings.apply(lambda row: infer_wet_mils(row.get("warranty_years"), row.get("coating_type")), axis=1)
    grouped = []
    for keys, group in coatings.groupby(["coating_type", "warranty_years", "wet_mils"], dropna=False):
        job_ids = sorted(set(group["job_id"].dropna().astype(str)))
        grouped.append(
            {
                "coating_type": keys[0],
                "warranty_years": keys[1],
                "wet_mils": keys[2],
                "waste_factor": 0.12,
                "avg_gal_per_sqft": round(float(group["gal_per_sqft"].mean()), 6),
                "median_gal_per_sqft": round(float(group["gal_per_sqft"].median()), 6),
                "job_count": len(job_ids),
                "confidence": confidence(len(job_ids)),
                "supporting_job_ids": json.dumps(job_ids),
            }
        )
    return pd.DataFrame(grouped).sort_values(["confidence", "job_count"], ascending=[True, False])


def build_work_package_cooccurrence(materials: pd.DataFrame) -> pd.DataFrame:
    if materials.empty:
        return pd.DataFrame(columns=["project_type", "substrate", "package_a", "package_b", "co_occurrence_rate", "job_count", "confidence"])
    job_packages = (
        materials[materials["package"].notna()]
        .groupby(["project_type", "substrate", "job_id"], dropna=False)["package"]
        .apply(lambda values: sorted(set(clean_text(value) for value in values if clean_text(value))))
        .reset_index()
    )
    rows = []
    for (project_type, substrate), group in job_packages.groupby(["project_type", "substrate"], dropna=False):
        total_jobs = len(group)
        pair_counts: dict[tuple[str, str], list[str]] = {}
        for _, row in group.iterrows():
            packages = [package for package in row["package"] if package]
            for package_a, package_b in combinations(packages, 2):
                pair_counts.setdefault((package_a, package_b), []).append(str(row["job_id"]))
        for (package_a, package_b), job_ids in pair_counts.items():
            count = len(set(job_ids))
            rows.append(
                {
                    "project_type": project_type,
                    "substrate": substrate,
                    "package_a": package_a,
                    "package_b": package_b,
                    "co_occurrence_rate": round(count / total_jobs, 4) if total_jobs else 0,
                    "job_count": count,
                    "confidence": confidence(count),
                    "supporting_job_ids": json.dumps(sorted(set(job_ids))),
                }
            )
    return pd.DataFrame(rows)


def build_material_qty_ratios(materials: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "project_type",
        "substrate",
        "coating_type",
        "warranty_years",
        "package",
        "unit",
        "median_qty_per_sqft",
        "p25_qty_per_sqft",
        "p75_qty_per_sqft",
        "median_cost_per_sqft",
        "job_count",
        "confidence",
    ]
    if materials.empty or "is_material" not in materials.columns:
        return pd.DataFrame(columns=columns)
    rows = materials[materials["is_material"]].copy()
    rows = rows[pd.to_numeric(rows.get("area_sqft"), errors="coerce") > 0]
    rows["physical_quantity_valid"] = rows.apply(
        lambda row: to_number(row.get("quantity")) is not None and canonical_unit(row.get("unit")) in PHYSICAL_UNITS and canonical_unit(row.get("unit")) not in ALLOWANCE_UNITS,
        axis=1,
    )
    rows["qty_per_sqft"] = rows.apply(lambda row: row["quantity"] / row["area_sqft"] if row.get("physical_quantity_valid") else math.nan, axis=1)
    rows["cost_per_sqft"] = rows["total_cost"] / rows["area_sqft"]
    out = []
    group_cols = ["project_type", "substrate", "coating_type", "warranty_years", "package", "unit"]
    for keys, group in rows.groupby(group_cols, dropna=False):
        valid_qty = group[group["physical_quantity_valid"]]
        job_ids = sorted(set(group["job_id"].dropna().astype(str)))
        out.append(
            {
                "project_type": keys[0],
                "substrate": keys[1],
                "coating_type": keys[2],
                "warranty_years": keys[3],
                "package": keys[4],
                "unit": keys[5],
                "median_qty_per_sqft": percentile(valid_qty.get("qty_per_sqft", pd.Series(dtype=float)), 0.5),
                "p25_qty_per_sqft": percentile(valid_qty.get("qty_per_sqft", pd.Series(dtype=float)), 0.25),
                "p75_qty_per_sqft": percentile(valid_qty.get("qty_per_sqft", pd.Series(dtype=float)), 0.75),
                "median_cost_per_sqft": percentile(group.get("cost_per_sqft", pd.Series(dtype=float)), 0.5),
                "job_count": len(job_ids),
                "confidence": confidence(len(job_ids)),
                "supporting_job_ids": json.dumps(job_ids),
            }
        )
    return pd.DataFrame(out)


def build_labor_rates(labor: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "project_type",
        "substrate",
        "coating_type",
        "warranty_years",
        "labor_package",
        "median_hours_per_1000_sqft",
        "p25_hours_per_1000_sqft",
        "p75_hours_per_1000_sqft",
        "median_cost_per_sqft",
        "job_count",
        "confidence",
    ]
    if labor.empty or "is_labor" not in labor.columns:
        return pd.DataFrame(columns=columns)
    rows = labor[labor["is_labor"]].copy()
    rows = rows[pd.to_numeric(rows.get("area_sqft"), errors="coerce") > 0]
    if rows.empty:
        return pd.DataFrame(columns=columns)
    rows["labor_package"] = rows["package"].where(rows["package"].ne("labor"), rows.get("item_name", "labor").astype(str).str.lower().str.replace(r"\s+", "_", regex=True))
    rows["hours"] = rows["labor_hours"]
    rows.loc[rows["hours"].isna() & rows["labor_days"].notna() & rows.get("crew_size").notna(), "hours"] = rows["labor_days"] * rows["crew_size"] * 8
    rows["hours_per_1000_sqft"] = rows["hours"] / rows["area_sqft"] * 1000
    rows["cost_per_sqft"] = rows["total_cost"] / rows["area_sqft"]
    out = []
    group_cols = ["project_type", "substrate", "coating_type", "warranty_years", "labor_package"]
    for keys, group in rows.groupby(group_cols, dropna=False):
        job_ids = sorted(set(group["job_id"].dropna().astype(str)))
        out.append(
            {
                "project_type": keys[0],
                "substrate": keys[1],
                "coating_type": keys[2],
                "warranty_years": keys[3],
                "labor_package": keys[4],
                "median_hours_per_1000_sqft": percentile(group.get("hours_per_1000_sqft", pd.Series(dtype=float)), 0.5),
                "p25_hours_per_1000_sqft": percentile(group.get("hours_per_1000_sqft", pd.Series(dtype=float)), 0.25),
                "p75_hours_per_1000_sqft": percentile(group.get("hours_per_1000_sqft", pd.Series(dtype=float)), 0.75),
                "median_cost_per_sqft": percentile(group.get("cost_per_sqft", pd.Series(dtype=float)), 0.5),
                "job_count": len(job_ids),
                "confidence": confidence(len(job_ids)),
                "supporting_job_ids": json.dumps(job_ids),
            }
        )
    return pd.DataFrame(out)


def build_anomalies(materials: pd.DataFrame, labor: pd.DataFrame) -> pd.DataFrame:
    anomalies: list[dict[str, Any]] = []
    for _, row in materials.iterrows():
        job_id = clean_text(row.get("job_id"))
        package = clean_text(row.get("package"))
        area = to_number(row.get("area_sqft"))
        quantity = to_number(row.get("quantity"))
        unit = canonical_unit(row.get("unit"))
        unit_cost = to_number(row.get("unit_cost"))
        total_cost = to_number(row.get("total_cost"))
        if package == "primer" and area and quantity and unit in {"pail", "drum", "ea"}:
            sqft_per_pail = area / quantity
            if sqft_per_pail < 500:
                anomalies.append(anomaly(job_id, "primer_pails_implausible", f"Primer quantity implies {sqft_per_pail:.0f} sqft per {unit}.", row))
        if package == "coating" and area and quantity and unit == "gal":
            sqft_per_gal = area / quantity
            if sqft_per_gal < 25 or sqft_per_gal > 120:
                anomalies.append(anomaly(job_id, "coating_gallons_inconsistent", f"Coating coverage {sqft_per_gal:.0f} sqft/gal is outside expected review range.", row))
        if unit in ALLOWANCE_UNITS and quantity and total_cost:
            anomalies.append(anomaly(job_id, "allowance_as_quantity", "Allowance/lump-sum row has a physical quantity; do not use as quantity ratio.", row))
        if unit_cost is not None and ((unit in {"pail", "drum"} and unit_cost < 20) or unit_cost > 100000):
            anomalies.append(anomaly(job_id, "unit_cost_suspicious", f"Unit cost {unit_cost:g} for {unit or 'unknown unit'} looks suspicious.", row))
        if package == "fastener_treatment" and "metal" not in clean_text(row.get("substrate")).lower() and "metal" not in row_text(row):
            anomalies.append(anomaly(job_id, "fastener_on_non_metal", "Fastener treatment found on non-metal roof without clear manual justification.", row))

    material_packages_by_job = materials.groupby("job_id")["package"].apply(lambda values: set(values.dropna().astype(str))).to_dict() if not materials.empty else {}
    for _, row in labor.iterrows():
        job_id = clean_text(row.get("job_id"))
        package = clean_text(row.get("package"))
        materials_for_job = material_packages_by_job.get(job_id, set())
        if package == "labor_prime" or "prime" in row_text(row):
            if "primer" not in materials_for_job:
                anomalies.append(anomaly(job_id, "primer_labor_without_primer_material", "Primer labor present but primer material package is absent.", row))
        if package and package not in {"labor", "other"} and package not in materials_for_job and package.startswith("labor_"):
            anomalies.append(anomaly(job_id, "labor_without_material_package", f"Labor package {package} has no corresponding material package.", row))
    return pd.DataFrame(anomalies)


def anomaly(job_id: str, anomaly_type: str, message: str, row: pd.Series) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "anomaly_type": anomaly_type,
        "message": message,
        "package": row.get("package"),
        "item_name": row.get("item_name"),
        "quantity": row.get("quantity"),
        "unit": row.get("unit"),
        "unit_cost": row.get("unit_cost"),
        "total_cost": row.get("total_cost"),
        "source_reference": row.get("estimate_file") or row.get("source_file") or row.get("source_path"),
    }


def build_rule_suggestions(
    warranty: pd.DataFrame,
    cooccurrence: pd.DataFrame,
    material_ratios: pd.DataFrame,
    labor_rates: pd.DataFrame,
    anomalies: pd.DataFrame,
) -> dict[str, Any]:
    rules: dict[str, Any] = {
        "warranty_years_to_wet_mils": [],
        "project_substrate_likely_work_packages": [],
        "material_package_to_labor_package": [],
        "primer_inclusion_triggers": [],
        "fastener_treatment_triggers": [],
        "default_production_rates_by_labor_package": [],
        "anomaly_summary": {},
    }
    for _, row in warranty.sort_values("job_count", ascending=False).head(50).iterrows():
        rules["warranty_years_to_wet_mils"].append(
            {
                "coating_type": row.get("coating_type"),
                "warranty_years": row.get("warranty_years"),
                "suggested_wet_mils": row.get("wet_mils"),
                "median_gal_per_sqft": row.get("median_gal_per_sqft"),
                "supporting_job_count": int(row.get("job_count") or 0),
                "confidence": row.get("confidence"),
            }
        )
    likely = cooccurrence[cooccurrence.get("co_occurrence_rate", 0) >= 0.5] if not cooccurrence.empty else pd.DataFrame()
    for _, row in likely.sort_values(["job_count", "co_occurrence_rate"], ascending=False).head(100).iterrows():
        rules["project_substrate_likely_work_packages"].append(row.dropna().to_dict())
    for _, row in labor_rates.sort_values("job_count", ascending=False).head(100).iterrows():
        rate = to_number(row.get("median_hours_per_1000_sqft"))
        if rate:
            rules["default_production_rates_by_labor_package"].append(
                {
                    "project_type": row.get("project_type"),
                    "substrate": row.get("substrate"),
                    "coating_type": row.get("coating_type"),
                    "warranty_years": row.get("warranty_years"),
                    "labor_package": row.get("labor_package"),
                    "median_hours_per_1000_sqft": rate,
                    "supporting_job_count": int(row.get("job_count") or 0),
                    "confidence": row.get("confidence"),
                }
            )
    primer_rows = material_ratios[material_ratios["package"].eq("primer")] if not material_ratios.empty and "package" in material_ratios.columns else pd.DataFrame()
    for _, row in primer_rows.sort_values("job_count", ascending=False).head(50).iterrows():
        rules["primer_inclusion_triggers"].append(row.dropna().to_dict())
    fastener_rows = material_ratios[material_ratios["package"].eq("fastener_treatment")] if not material_ratios.empty and "package" in material_ratios.columns else pd.DataFrame()
    for _, row in fastener_rows.sort_values("job_count", ascending=False).head(50).iterrows():
        rules["fastener_treatment_triggers"].append(row.dropna().to_dict())
    if not anomalies.empty:
        rules["anomaly_summary"] = anomalies["anomaly_type"].value_counts().to_dict()
    return rules


def stable_id(prefix: str, *parts: Any) -> str:
    payload = "||".join(clean_text(part) for part in parts)
    return f"{prefix}-{hashlib.sha1(payload.encode('utf-8')).hexdigest()[:24]}"


def table_exists(engine: Engine, table_name: str) -> bool:
    return inspect(engine).has_table(table_name)


def read_table(engine: Engine, table_name: str) -> pd.DataFrame:
    if not table_exists(engine, table_name):
        return pd.DataFrame()
    with engine.connect() as conn:
        return pd.read_sql_query(text(f"SELECT * FROM {table_name}"), conn)


def sanitize_frame_for_sql(frame: pd.DataFrame | None, table_name: str | None = None) -> pd.DataFrame:
    if frame is None:
        return pd.DataFrame()
    cleaned = frame.copy()

    # raw_json already preserves the full raw payload; do not also write raw dicts.
    if table_name == "estimate_line_items_raw" and "raw" in cleaned.columns and "raw_json" in cleaned.columns:
        cleaned = cleaned.drop(columns=["raw"])

    def clean_value(value: Any) -> Any:
        if value is None:
            return None
        try:
            if pd.isna(value):
                return None
        except Exception:
            pass
        if isinstance(value, (dict, list, tuple, set)):
            return json.dumps(value, default=str, sort_keys=True)
        return value

    for column in cleaned.columns:
        if cleaned[column].dtype == "object":
            cleaned[column] = cleaned[column].map(clean_value)

    return cleaned


def write_table(engine: Engine, table_name: str, frame: pd.DataFrame) -> None:
    frame = sanitize_frame_for_sql(frame, table_name)
    frame.to_sql(table_name, engine, if_exists="replace", index=False, chunksize=1000)


def write_outputs_to_database(engine: Engine, outputs: dict[str, pd.DataFrame]) -> None:
    name_map = {
        "relationship_warranty_coating.csv": "relationship_warranty_coating",
        "relationship_package_cooccurrence.csv": "relationship_package_cooccurrence",
        "relationship_work_package_cooccurrence.csv": "relationship_package_cooccurrence",
        "relationship_material_qty_ratios.csv": "relationship_material_qty_ratios",
        "relationship_labor_rates.csv": "relationship_labor_rates",
        "relationship_anomalies.csv": "relationship_anomalies",
    }
    for filename, frame in outputs.items():
        table_name = name_map.get(filename)
        if table_name:
            write_table(engine, table_name, frame)


def source_documents_from_line_items(line_items: pd.DataFrame) -> pd.DataFrame:
    if line_items.empty:
        return pd.DataFrame(columns=["source_document_id", "source_file", "source_path", "source_sheet", "parser_version"])
    rows = []
    seen: set[str] = set()
    for _, row in line_items.iterrows():
        source_file = clean_text(row.get("estimate_file") or row.get("source_file"))
        source_path = clean_text(row.get("source_path") or row.get("estimate_file"))
        source_sheet = clean_text(row.get("source_sheet"))
        source_document_id = stable_id("sourcedoc", source_path, source_file, source_sheet)
        if source_document_id in seen:
            continue
        seen.add(source_document_id)
        rows.append(
            {
                "source_document_id": source_document_id,
                "source_file": source_file,
                "source_path": source_path,
                "source_sheet": source_sheet,
                "parser_version": PROFILER_PARSER_VERSION,
            }
        )
    return pd.DataFrame(rows)


def raw_line_items_from_existing(line_items: pd.DataFrame, source_documents: pd.DataFrame) -> pd.DataFrame:
    if line_items.empty:
        return pd.DataFrame()
    df = line_items.copy()
    if "line_item_id" not in df.columns:
        df["line_item_id"] = df.apply(
            lambda row: stable_id(
                "rawline",
                row.get("estimate_id"),
                row.get("job_id"),
                row.get("estimate_file"),
                row.get("source_row"),
                row.get("line_item_name"),
                row.get("extended_cost"),
            ),
            axis=1,
        )
    docs = source_documents.copy()
    if not docs.empty:
        docs["_join_key"] = docs["source_path"].fillna("").astype(str) + "||" + docs["source_file"].fillna("").astype(str) + "||" + docs["source_sheet"].fillna("").astype(str)
        source_path = df["source_path"] if "source_path" in df.columns else df["estimate_file"] if "estimate_file" in df.columns else pd.Series([""] * len(df), index=df.index)
        estimate_file = df["estimate_file"] if "estimate_file" in df.columns else pd.Series([""] * len(df), index=df.index)
        source_sheet = df["source_sheet"] if "source_sheet" in df.columns else pd.Series([""] * len(df), index=df.index)
        df["_join_key"] = source_path.fillna("").astype(str) + "||" + estimate_file.fillna("").astype(str) + "||" + source_sheet.fillna("").astype(str)
        df = df.merge(docs[["_join_key", "source_document_id"]], on="_join_key", how="left").drop(columns=["_join_key"])
    else:
        df["source_document_id"] = None
    df["raw_json"] = df.apply(lambda row: json.dumps(row.dropna().to_dict(), default=str, sort_keys=True), axis=1)
    df["parser_version"] = PROFILER_PARSER_VERSION
    return df


def normalize_jobs_from_database(jobs: pd.DataFrame, estimates: pd.DataFrame) -> pd.DataFrame:
    source = estimates.copy() if not estimates.empty else pd.DataFrame()
    if source.empty and not jobs.empty:
        source = jobs.copy()
    elif not source.empty and not jobs.empty and "job_id" in source.columns and "job_id" in jobs.columns:
        job_keep = [
            column
            for column in [
                "job_id",
                "source_year",
                "division",
                "pipeline_status",
                "status",
                "customer",
                "job_name",
                "job_type",
                "site_address",
                "city",
                "state",
                "estimated_value",
                "invoice_amount",
            ]
            if column in jobs.columns
        ]
        source = source.merge(jobs[job_keep].drop_duplicates("job_id"), on="job_id", how="outer", suffixes=("", "_job"))
    if source.empty:
        return pd.DataFrame()
    source = normalize_jobs(None, None) if False else source
    rename = {
        first_existing(source, ["estimated_sqft", "surface_area_sqft", "area_sqft"]): "area_sqft",
        first_existing(source, ["warranty_years", "warranty_target", "warranty_target_years"]): "warranty_years",
        first_existing(source, ["final_price", "worksheet_price", "estimated_value"]): "final_price",
    }
    rename = {key: value for key, value in rename.items() if key and key != value}
    source = source.rename(columns=rename)
    for column in ["area_sqft", "warranty_years", "wet_mils", "final_price", "invoice_amount"]:
        if column in source.columns:
            source[column] = pd.to_numeric(source[column], errors="coerce")
    if "wet_mils" not in source.columns:
        source["wet_mils"] = source.apply(lambda row: infer_wet_mils(row.get("warranty_years"), row.get("coating_type")), axis=1)
    if "project_type" not in source.columns:
        source["project_type"] = source.get("job_type", "")
    for column in [
        "job_id",
        "source_year",
        "division",
        "pipeline_status",
        "status",
        "customer",
        "job_name",
        "project_type",
        "substrate",
        "coating_type",
        "roof_condition",
        "access_complexity",
    ]:
        if column not in source.columns:
            source[column] = ""
    source["area_bucket"] = source["area_sqft"].apply(area_bucket) if "area_sqft" in source.columns else "unknown"
    keep = [
        "job_id",
        "source_year",
        "division",
        "pipeline_status",
        "status",
        "customer",
        "job_name",
        "project_type",
        "substrate",
        "area_sqft",
        "area_bucket",
        "warranty_years",
        "wet_mils",
        "coating_type",
        "roof_condition",
        "access_complexity",
        "final_price",
        "invoice_amount",
    ]
    return source[[column for column in keep if column in source.columns]].drop_duplicates("job_id")


def source_type_for_row(row: pd.Series) -> str:
    if is_labor_row(row):
        return "labor_budget"
    unit = canonical_unit(row.get("unit"))
    quantity = to_number(row.get("quantity"))
    total_cost = to_number(row.get("total_cost"))
    if quantity is not None and unit in PHYSICAL_UNITS and unit not in ALLOWANCE_UNITS:
        return "physical_quantity"
    if total_cost is not None and (unit in ALLOWANCE_UNITS or quantity is None):
        return "cost_allowance"
    return "unknown"


def normalize_raw_line_items(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    normalized = normalize_line_items(None) if False else raw.copy()
    rename = {
        first_existing(normalized, ["line_item_name", "item_name", "selected_item_name"]): "item_name",
        first_existing(normalized, ["line_item_category", "category"]): "category",
        first_existing(normalized, ["unit_cost", "unit_price"]): "unit_cost",
        first_existing(normalized, ["extended_cost", "total_cost", "estimated_cost"]): "total_cost",
        first_existing(normalized, ["labor_hours", "total_hours"]): "labor_hours",
        first_existing(normalized, ["labor_days", "days"]): "labor_days",
    }
    rename = {key: value for key, value in rename.items() if key and key != value}
    normalized = normalized.rename(columns=rename)
    for column in ["quantity", "unit_cost", "total_cost", "labor_hours", "labor_days", "crew_size"]:
        if column in normalized.columns:
            normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
        else:
            normalized[column] = math.nan
    if "unit" not in normalized.columns:
        normalized["unit"] = ""
    normalized["unit"] = normalized["unit"].apply(canonical_unit)
    if "job_id" not in normalized.columns:
        normalized["job_id"] = ""
    normalized["package"] = normalized.apply(classify_package, axis=1)
    normalized["line_type"] = normalized.apply(lambda row: "labor" if is_labor_row(row) else "material", axis=1)
    normalized["normalized_item_name"] = normalized.get("item_name", "").fillna("").astype(str).str.strip().str.lower().str.replace(r"\s+", " ", regex=True)
    normalized["source_type"] = normalized.apply(source_type_for_row, axis=1)
    normalized["physical_quantity_valid"] = normalized["source_type"].eq("physical_quantity")
    normalized["review_required"] = normalized["source_type"].isin({"cost_allowance", "unknown"}) | normalized["package"].eq("other")
    normalized["normalization_confidence"] = normalized["source_type"].map({"physical_quantity": 0.9, "labor_budget": 0.85, "cost_allowance": 0.65, "unknown": 0.35}).fillna(0.35)
    normalized["normalization_reason"] = normalized["source_type"].map(
        {
            "physical_quantity": "Valid physical quantity and unit.",
            "labor_budget": "Labor row identified from section/category/hours/days.",
            "cost_allowance": "Cost allowance or lump-sum row; do not use as physical quantity ratio.",
            "unknown": "Insufficient quantity/unit/cost evidence.",
        }
    )
    if "line_item_id" in normalized.columns:
        normalized["raw_line_item_id"] = normalized["line_item_id"]
    else:
        normalized["raw_line_item_id"] = normalized.apply(lambda row: stable_id("rawline", row.get("job_id"), row.get("item_name"), row.name), axis=1)
    normalized["normalized_line_item_id"] = normalized.apply(lambda row: stable_id("normline", row.get("raw_line_item_id"), row.get("package"), row.get("source_type")), axis=1)
    keep = [
        "normalized_line_item_id",
        "raw_line_item_id",
        "source_document_id",
        "job_id",
        "estimate_id",
        "estimate_file",
        "source_sheet",
        "source_row",
        "line_type",
        "package",
        "normalized_item_name",
        "item_name",
        "category",
        "section",
        "description",
        "quantity",
        "unit",
        "unit_cost",
        "total_cost",
        "labor_days",
        "labor_hours",
        "crew_size",
        "source_type",
        "physical_quantity_valid",
        "review_required",
        "normalization_confidence",
        "normalization_reason",
    ]
    for column in keep:
        if column not in normalized.columns:
            normalized[column] = None
    return normalized[keep]


def build_job_package_summary(normalized: pd.DataFrame, estimate_jobs: pd.DataFrame) -> pd.DataFrame:
    if normalized.empty:
        return pd.DataFrame()
    rows = merge_job_context(normalized.copy(), estimate_jobs)
    rows["total_cost"] = pd.to_numeric(rows.get("total_cost"), errors="coerce")
    rows["quantity"] = pd.to_numeric(rows.get("quantity"), errors="coerce")
    out = []
    for (job_id, package), group in rows.groupby(["job_id", "package"], dropna=False):
        valid_quantity = group[group["physical_quantity_valid"].astype(bool)]
        units = sorted(set(valid_quantity["unit"].dropna().astype(str)))
        unit = units[0] if len(units) == 1 else "mixed" if len(units) > 1 else ""
        total_quantity = valid_quantity["quantity"].sum() if not valid_quantity.empty and unit != "mixed" else math.nan
        total_cost = group["total_cost"].sum(min_count=1)
        area = to_number(group["area_sqft"].dropna().iloc[0]) if "area_sqft" in group.columns and group["area_sqft"].notna().any() else None
        line_ids = sorted(set(group["normalized_line_item_id"].dropna().astype(str)))
        out.append(
            {
                "job_id": job_id,
                "package": package,
                "included": True,
                "total_quantity": total_quantity,
                "unit": unit,
                "total_cost": total_cost,
                "total_hours": group["labor_hours"].sum(min_count=1) if "labor_hours" in group.columns else math.nan,
                "qty_per_sqft": total_quantity / area if area and total_quantity == total_quantity else math.nan,
                "cost_per_sqft": total_cost / area if area and total_cost == total_cost else math.nan,
                "has_physical_quantity": bool(group["physical_quantity_valid"].astype(bool).any()),
                "has_allowance": bool(group["source_type"].eq("cost_allowance").any()),
                "review_required": bool(group["review_required"].astype(bool).any()),
                "evidence_line_item_ids": json.dumps(line_ids),
            }
        )
    return pd.DataFrame(out)


def material_rows_from_package_summary(summary: pd.DataFrame, jobs: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        return pd.DataFrame()
    rows = summary.copy().rename(columns={"total_quantity": "quantity", "total_cost": "total_cost"})
    rows["is_material"] = ~rows["package"].astype(str).str.startswith("labor")
    rows["is_labor"] = rows["package"].astype(str).str.startswith("labor")
    rows["unit_cost"] = math.nan
    return merge_job_context(rows, jobs)


def labor_rows_from_normalized(normalized: pd.DataFrame, jobs: pd.DataFrame) -> pd.DataFrame:
    if normalized.empty:
        return pd.DataFrame()
    rows = normalized[normalized["line_type"].eq("labor")].copy()
    rows["is_labor"] = True
    rows["is_material"] = False
    return merge_job_context(rows, jobs)


def apply_job_filters(jobs: pd.DataFrame, source_year: str | None, division: str | None, status: str | None) -> pd.DataFrame:
    filtered = jobs.copy()
    if source_year and "source_year" in filtered.columns:
        filtered = filtered[filtered["source_year"].astype(str).eq(str(source_year))]
    if division and "division" in filtered.columns:
        filtered = filtered[filtered["division"].astype(str).str.lower().eq(division.lower())]
    if status:
        masks = []
        for column in ("status", "pipeline_status"):
            if column in filtered.columns:
                masks.append(filtered[column].astype(str).str.lower().eq(status.lower()))
        if masks:
            mask = masks[0]
            for extra in masks[1:]:
                mask = mask | extra
            filtered = filtered[mask]
    return filtered


def material_qty_ratios_from_summary(summary_with_jobs: pd.DataFrame) -> pd.DataFrame:
    rows = summary_with_jobs.copy()
    rows["is_material"] = ~rows["package"].astype(str).str.startswith("labor")
    rows["quantity"] = rows.get("total_quantity")
    rows["total_cost"] = rows.get("total_cost")
    return build_material_qty_ratios(rows)


def profile_relationships_from_database(
    *,
    engine: Engine,
    out_dir: Path,
    source_year: str | None = None,
    division: str | None = None,
    status: str | None = None,
    min_job_count: int = 1,
    write_review_sheet: bool = False,
) -> dict[str, Path]:
    existing_line_items = read_table(engine, "estimate_line_items")
    existing_jobs = read_table(engine, "jobs")
    existing_estimates = read_table(engine, "estimates")
    if existing_line_items.empty:
        raise RuntimeError("estimate_line_items table is empty or missing; load extracted line items before profiling.")

    source_documents = source_documents_from_line_items(existing_line_items)
    raw = raw_line_items_from_existing(existing_line_items, source_documents)
    normalized = normalize_raw_line_items(raw)
    estimate_jobs = normalize_jobs_from_database(existing_jobs, existing_estimates)
    package_summary = build_job_package_summary(normalized, estimate_jobs)

    write_table(engine, "source_documents", source_documents)
    write_table(engine, "estimate_line_items_raw", raw)
    write_table(engine, "estimate_line_items_normalized", normalized)
    write_table(engine, "estimate_jobs", estimate_jobs)
    write_table(engine, "job_package_summary", package_summary)

    filtered_jobs = apply_job_filters(estimate_jobs, source_year, division, status)
    job_ids = set(filtered_jobs["job_id"].dropna().astype(str)) if not filtered_jobs.empty and "job_id" in filtered_jobs.columns else set()
    filtered_summary = package_summary[package_summary["job_id"].astype(str).isin(job_ids)].copy() if job_ids else package_summary.iloc[0:0].copy()
    filtered_normalized = normalized[normalized["job_id"].astype(str).isin(job_ids)].copy() if job_ids else normalized.iloc[0:0].copy()
    summary_with_jobs = merge_job_context(filtered_summary, filtered_jobs)
    material_rows = material_rows_from_package_summary(filtered_summary, filtered_jobs)
    labor_rows = labor_rows_from_normalized(filtered_normalized, filtered_jobs)
    normalized_material_rows = filtered_normalized[filtered_normalized["line_type"].ne("labor")].copy()
    normalized_material_rows["is_material"] = True
    normalized_material_rows["is_labor"] = False
    normalized_material_rows = merge_job_context(normalized_material_rows, filtered_jobs)

    outputs = {
        "relationship_warranty_coating.csv": build_warranty_coating(material_rows),
        "relationship_package_cooccurrence.csv": build_work_package_cooccurrence(material_rows),
        "relationship_work_package_cooccurrence.csv": build_work_package_cooccurrence(material_rows),
        "relationship_material_qty_ratios.csv": material_qty_ratios_from_summary(summary_with_jobs),
        "relationship_labor_rates.csv": build_labor_rates(labor_rows),
        "relationship_anomalies.csv": build_anomalies(normalized_material_rows, labor_rows),
    }
    if min_job_count > 1:
        for key in [
            "relationship_warranty_coating.csv",
            "relationship_package_cooccurrence.csv",
            "relationship_work_package_cooccurrence.csv",
            "relationship_material_qty_ratios.csv",
            "relationship_labor_rates.csv",
        ]:
            frame = outputs[key]
            if not frame.empty and "job_count" in frame.columns:
                outputs[key] = frame[pd.to_numeric(frame["job_count"], errors="coerce").fillna(0) >= min_job_count]

    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for filename, frame in outputs.items():
        path = out_dir / filename
        frame.to_csv(path, index=False)
        paths[filename] = path
    suggestions = build_rule_suggestions(
        outputs["relationship_warranty_coating.csv"],
        outputs["relationship_package_cooccurrence.csv"],
        outputs["relationship_material_qty_ratios.csv"],
        outputs["relationship_labor_rates.csv"],
        outputs["relationship_anomalies.csv"],
    )
    suggestions_path = out_dir / "estimator_rule_suggestions.json"
    suggestions_path.write_text(json.dumps(suggestions, indent=2, default=str), encoding="utf-8")
    paths["estimator_rule_suggestions.json"] = suggestions_path
    write_outputs_to_database(engine, outputs)
    if write_review_sheet:
        review_path = out_dir / "relationship_review_sheet.xlsx"
        with pd.ExcelWriter(review_path) as writer:
            normalized.head(5000).to_excel(writer, sheet_name="normalized_rows", index=False)
            package_summary.head(5000).to_excel(writer, sheet_name="package_summary", index=False)
            outputs["relationship_anomalies.csv"].head(5000).to_excel(writer, sheet_name="anomalies", index=False)
        paths["relationship_review_sheet.xlsx"] = review_path
    return paths


def profile_relationships(
    *,
    jobs_csv: Path | None,
    estimate_summary_csv: Path | None,
    line_items_csv: Path | None,
    out_dir: Path,
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs = normalize_jobs(jobs_csv, estimate_summary_csv)
    line_items = normalize_line_items(line_items_csv)
    line_items = merge_job_context(line_items, jobs)
    materials = line_items[line_items["is_material"]].copy() if not line_items.empty else pd.DataFrame()
    labor = line_items[line_items["is_labor"]].copy() if not line_items.empty else pd.DataFrame()

    outputs = {
        "relationship_warranty_coating.csv": build_warranty_coating(materials),
        "relationship_work_package_cooccurrence.csv": build_work_package_cooccurrence(materials),
        "relationship_material_qty_ratios.csv": build_material_qty_ratios(materials),
        "relationship_labor_rates.csv": build_labor_rates(line_items),
        "relationship_anomalies.csv": build_anomalies(materials, labor),
    }
    paths: dict[str, Path] = {}
    for filename, frame in outputs.items():
        path = out_dir / filename
        frame.to_csv(path, index=False)
        paths[filename] = path
    suggestions = build_rule_suggestions(
        outputs["relationship_warranty_coating.csv"],
        outputs["relationship_work_package_cooccurrence.csv"],
        outputs["relationship_material_qty_ratios.csv"],
        outputs["relationship_labor_rates.csv"],
        outputs["relationship_anomalies.csv"],
    )
    suggestions_path = out_dir / "estimator_rule_suggestions.json"
    suggestions_path.write_text(json.dumps(suggestions, indent=2, default=str), encoding="utf-8")
    paths["estimator_rule_suggestions.json"] = suggestions_path
    return paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Profile estimator training exports for repeatable material/labor relationships.")
    parser.add_argument("--db-url", default=os.getenv("DATABASE_URL"), help="Database URL. When supplied, profiler reads existing DB tables and writes normalized relationship tables.")
    parser.add_argument("--source-year", help="Optional source_year filter for database profiling.")
    parser.add_argument("--division", help="Optional division filter for database profiling.")
    parser.add_argument("--status", help="Optional status or pipeline_status filter for database profiling.")
    parser.add_argument("--output-dir", "--out-dir", dest="out_dir", type=Path, default=Path("output/relationships"), help="Directory for relationship profile outputs.")
    parser.add_argument("--min-job-count", type=int, default=1, help="Minimum supporting job count for relationship outputs.")
    parser.add_argument("--write-review-sheet", action="store_true", help="Also write an XLSX review workbook with normalized rows, package summaries, and anomalies.")
    parser.add_argument("--jobs", type=Path, default=Path("output/job_index.csv"), help="Job-level CSV. Defaults to output/job_index.csv.")
    parser.add_argument("--estimate-summary", type=Path, default=Path("output/estimate_summary.csv"), help="Estimate summary CSV. Defaults to output/estimate_summary.csv.")
    parser.add_argument("--line-items", type=Path, default=Path("output/estimate_line_items.csv"), help="Material/labor line item CSV. Defaults to output/estimate_line_items.csv.")
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()
    if args.db_url:
        engine = create_engine(args.db_url)
        paths = profile_relationships_from_database(
            engine=engine,
            out_dir=args.out_dir,
            source_year=args.source_year,
            division=args.division,
            status=args.status,
            min_job_count=max(args.min_job_count, 1),
            write_review_sheet=args.write_review_sheet,
        )
    else:
        paths = profile_relationships(
            jobs_csv=args.jobs,
            estimate_summary_csv=args.estimate_summary,
            line_items_csv=args.line_items,
            out_dir=args.out_dir,
        )
    print(f"Wrote {len(paths)} relationship profiler outputs to {args.out_dir}")
    for name, path in paths.items():
        print(f"- {name}: {path}")


if __name__ == "__main__":
    main()
