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
SPECIFIC_LABOR_BUCKETS = {
    "labor_foam",
    "labor_clean_up",
    "labor_set_up",
    "labor_traveling",
    "labor_loading",
    "labor_mask",
    "labor_prime",
    "labor_dc_315",
    "labor_misc",
    "labor_membrane",
    "labor_prep",
    "labor_seam_sealer",
    "labor_base",
    "labor_top_coat",
    "labor_caulk",
    "labor_details",
    "labor_floor_grind_patch",
    "labor_floor_pop_off",
    "labor_floor_prep_base",
    "labor_floor_patch_grind",
    "labor_floor_primer",
    "labor_floor_base_coat",
    "labor_floor_details",
    "labor_floor_topcoat",
    "labor_floor_misc",
}
SPECIFIC_MATERIAL_BUCKETS = {
    "coating",
    "primer",
    "seam_treatment",
    "fastener_treatment",
    "caulk_detail",
    "foam",
    "membrane",
    "thermal_barrier_coating",
    "floor_base_coat",
    "floor_topcoat",
    "floor_coating",
    "floor_primer",
    "floor_flake",
    "lift",
    "generator",
    "space_heater",
    "delivery_fee",
    "freight",
}

ESTIMATED_UNITS_PHYSICAL_UNIT_BY_PACKAGE = {
    "coating": "gal",
    "primer": "gal",
    "thinner": "gal",
    "caulk_sealant": "unit",
    "caulk_detail": "unit",
    "seam_treatment": "unit",
    "fasteners": "ea",
    "fastener_treatment": "ea",
    "plates": "ea",
    "fabric": "roll",
    "foam": "unit",
    "membrane": "unit",
    "thermal_barrier_coating": "gal",
    "floor_base_coat": "gal",
    "floor_topcoat": "gal",
    "floor_coating": "gal",
    "floor_primer": "gal",
    "floor_flake": "unit",
}

JOB_CONTEXT_COLUMNS = [
    "source_year",
    "division",
    "pipeline_status",
    "status",
    "customer",
    "job_name",
    "template_type",
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
MATERIAL_RATIO_GROUP_COLS = ["source_year", "division", "template_type", "project_type", "substrate", "coating_type", "warranty_years", "wet_mils", "package", "unit"]
LABOR_RATE_GROUP_COLS = ["source_year", "division", "template_type", "project_type", "substrate", "package", "unit"]


def read_csv_if_exists(path: Path | None) -> pd.DataFrame:
    if not path or not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def ensure_columns(frame: pd.DataFrame, columns: list[str], default: Any = None) -> pd.DataFrame:
    out = frame.copy()
    for column in columns:
        if column not in out.columns:
            out[column] = default
    return out


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
    for column in ("item_name", "line_item_name", "selected_item_name", "row_label", "description", "category", "line_item_category", "section", "notes", "labor_package", "template_bucket"):
        if column in row.index:
            parts.append(clean_text(row.get(column)))
    return " ".join(parts).lower()


def normalized_template_bucket(value: Any) -> str:
    bucket = clean_text(value).lower().replace(" ", "_").replace("-", "_")
    bucket = re.sub(r"_+", "_", bucket).strip("_")
    if bucket in {"", "unknown", "none", "nan"}:
        return ""
    return bucket


def classify_package(row: pd.Series) -> str:
    bucket = normalized_template_bucket(row.get("template_bucket"))
    if bucket in SPECIFIC_LABOR_BUCKETS or bucket in SPECIFIC_MATERIAL_BUCKETS:
        return bucket
    text = row_text(row)
    category = clean_text(row.get("category") or row.get("line_item_category")).lower()
    section = clean_text(row.get("section")).lower()
    line_kind = clean_text(row.get("line_item_kind")).lower()
    if bucket.startswith("labor_"):
        return bucket
    if any(term in text for term in ("grind/patch", "grind patch", "patch/grind", "floor grind")):
        return "labor_floor_grind_patch"
    if any(term in text for term in ("prep & base", "prep/base", "floor prep")):
        return "labor_floor_prep_base"
    if any(term in text for term in ("trip #3 top coat", "floor top coat", "floor topcoat")):
        return "labor_floor_topcoat"
    if "labor" in category or "labor" in section or line_kind == "labor":
        labor_package = normalized_template_bucket(row.get("labor_package"))
        return labor_package or bucket or "labor"
    if bucket and bucket not in {"materials", "material", "misc", "other"}:
        return bucket
    if any(term in text for term in ("polyaspartic", "polyspartic")):
        return "floor_topcoat"
    if any(term in text for term in ("707", "base coat", "epoxy base", "npi epoxy")) and any(term in text for term in ("floor", "epoxy", "707", "base")):
        return "floor_base_coat"
    if "flake" in text:
        return "floor_flake"
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
    if any(term in text for term in ("membrane",)):
        return "membrane"
    if any(term in text for term in ("thermal barrier", "dc 315", "dc-315", "ignition barrier")):
        return "thermal_barrier_coating"
    if any(term in text for term in ("lift", "rental", "equipment")):
        return "lift"
    if any(term in text for term in ("generator",)):
        return "generator"
    if any(term in text for term in ("space heater", "heater")):
        return "space_heater"
    if any(term in text for term in ("freight",)):
        return "freight"
    if any(term in text for term in ("delivery",)):
        return "delivery_fee"
    if any(term in text for term in ("travel", "lodging", "mileage")):
        return "travel"
    if any(term in text for term in ("warranty", "bond", "insurance")):
        return "warranty_insurance"
    if category:
        normalized_category = normalized_template_bucket(category)
        if normalized_category in {"materials", "material"}:
            return "materials"
        return normalized_category
    return bucket or "other"


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
    bucket = normalized_template_bucket(row.get("template_bucket"))
    line_kind = clean_text(row.get("line_item_kind")).lower()
    return (
        bucket.startswith("labor_")
        or line_kind == "labor"
        or "labor" in category
        or "labor" in section
        or to_number(row.get("labor_hours")) is not None
        or to_number(row.get("labor_days")) is not None
    )


def merge_job_context(rows: pd.DataFrame, jobs: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return ensure_columns(rows, JOB_CONTEXT_COLUMNS)
    if jobs.empty or "job_id" not in jobs.columns:
        return ensure_columns(rows, JOB_CONTEXT_COLUMNS)
    jobs = ensure_columns(jobs, ["job_id", *JOB_CONTEXT_COLUMNS])
    keep = [
        column
        for column in [
            "job_id",
            *JOB_CONTEXT_COLUMNS,
        ]
        if column in jobs.columns
    ]
    merged = rows.merge(jobs[keep].drop_duplicates("job_id"), on="job_id", how="left", suffixes=("", "_job"))
    for column in JOB_CONTEXT_COLUMNS:
        job_column = f"{column}_job"
        if job_column in merged.columns:
            if column in merged.columns:
                merged[column] = merged[column].where(merged[column].notna(), merged[job_column])
                empty_mask = merged[column].astype(str).str.strip().isin({"", "nan", "None"})
                if empty_mask.any():
                    merged.loc[empty_mask, column] = merged.loc[empty_mask, job_column]
            else:
                merged[column] = merged[job_column]
            merged = merged.drop(columns=[job_column])
    merged = ensure_columns(merged, JOB_CONTEXT_COLUMNS)
    for column in ["project_type", "substrate", "coating_type", "area_bucket"]:
        if column in merged.columns:
            merged[column] = merged[column].fillna("unknown").astype(str).replace("", "unknown")
    if "warranty_years" in merged.columns:
        merged["warranty_years"] = pd.to_numeric(merged["warranty_years"], errors="coerce")
    for column in ["source_year", "area_sqft", "wet_mils", "final_price", "invoice_amount"]:
        if column in merged.columns:
            merged[column] = pd.to_numeric(merged[column], errors="coerce")
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
        "source_year",
        "division",
        "template_type",
        "project_type",
        "substrate",
        "coating_type",
        "warranty_years",
        "wet_mils",
        "package",
        "unit",
        "median_qty_per_sqft",
        "median_cost_per_sqft",
        "evidence_count",
        "supporting_job_ids",
        "p25_qty_per_sqft",
        "p75_qty_per_sqft",
        "job_count",
        "confidence",
    ]
    if materials.empty or "is_material" not in materials.columns:
        return pd.DataFrame(columns=columns)
    rows = materials[materials["is_material"]].copy()
    rows = ensure_columns(rows, ["job_id", "quantity", "total_cost", "area_sqft", "physical_quantity_valid", *MATERIAL_RATIO_GROUP_COLS])
    rows = rows[pd.to_numeric(rows.get("area_sqft"), errors="coerce") > 0]
    if rows.empty:
        return pd.DataFrame(columns=columns)
    rows["quantity"] = pd.to_numeric(rows["quantity"], errors="coerce")
    rows["total_cost"] = pd.to_numeric(rows["total_cost"], errors="coerce")
    rows["physical_quantity_valid"] = rows.apply(
        lambda row: to_number(row.get("quantity")) is not None and canonical_unit(row.get("unit")) in PHYSICAL_UNITS and canonical_unit(row.get("unit")) not in ALLOWANCE_UNITS,
        axis=1,
    )
    rows["qty_per_sqft"] = rows.apply(lambda row: row["quantity"] / row["area_sqft"] if row.get("physical_quantity_valid") else math.nan, axis=1)
    rows["cost_per_sqft"] = rows["total_cost"] / rows["area_sqft"]
    out = []
    group_cols = MATERIAL_RATIO_GROUP_COLS
    rows = ensure_columns(rows, group_cols)
    for keys, group in rows.groupby(group_cols, dropna=False):
        key_values = dict(zip(group_cols, keys, strict=True))
        valid_qty = group[group["physical_quantity_valid"]]
        job_ids = sorted(set(group["job_id"].dropna().astype(str)))
        out.append(
            {
                **key_values,
                "median_qty_per_sqft": percentile(valid_qty.get("qty_per_sqft", pd.Series(dtype=float)), 0.5),
                "median_cost_per_sqft": percentile(group.get("cost_per_sqft", pd.Series(dtype=float)), 0.5),
                "evidence_count": len(job_ids),
                "supporting_job_ids": json.dumps(job_ids),
                "p25_qty_per_sqft": percentile(valid_qty.get("qty_per_sqft", pd.Series(dtype=float)), 0.25),
                "p75_qty_per_sqft": percentile(valid_qty.get("qty_per_sqft", pd.Series(dtype=float)), 0.75),
                "job_count": len(job_ids),
                "confidence": confidence(len(job_ids)),
            }
        )
    return pd.DataFrame(out, columns=columns)


def build_labor_rates(labor: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "source_year",
        "division",
        "template_type",
        "project_type",
        "substrate",
        "coating_type",
        "warranty_years",
        "package",
        "unit",
        "median_hours_per_sqft",
        "median_cost_per_sqft",
        "median_total_hours",
        "median_total_cost",
        "median_crew_size",
        "median_days",
        "evidence_count",
        "supporting_job_ids",
        "labor_package",
        "median_hours_per_1000_sqft",
        "p25_hours_per_1000_sqft",
        "p75_hours_per_1000_sqft",
        "job_count",
        "confidence",
    ]
    if labor.empty:
        return pd.DataFrame(columns=columns)
    rows = labor.copy()
    rows = ensure_columns(rows, ["job_id", "package", "item_name", "labor_hours", "total_hours", "labor_days", "total_days", "days", "crew_size", "total_cost", "area_sqft", "is_labor", *LABOR_RATE_GROUP_COLS])
    is_labor_flag = rows["is_labor"].map(lambda value: False if value is None or pd.isna(value) else bool(value))
    rows["is_labor"] = is_labor_flag | rows["package"].astype(str).str.startswith("labor_") | rows["package"].astype(str).eq("labor")
    rows = rows[rows["is_labor"]].copy()
    if rows.empty:
        return pd.DataFrame(columns=columns)
    rows = rows[pd.to_numeric(rows.get("area_sqft"), errors="coerce") > 0]
    if rows.empty:
        return pd.DataFrame(columns=columns)
    rows["labor_package"] = rows["package"].where(rows["package"].ne("labor"), rows.get("item_name", "labor").astype(str).str.lower().str.replace(r"\s+", "_", regex=True))
    rows["package"] = rows["labor_package"]
    rows["hours"] = pd.to_numeric(rows["total_hours"], errors="coerce")
    rows.loc[rows["hours"].isna(), "hours"] = pd.to_numeric(rows.loc[rows["hours"].isna(), "labor_hours"], errors="coerce")
    labor_days = pd.to_numeric(rows["labor_days"], errors="coerce")
    missing_days = labor_days.isna()
    labor_days.loc[missing_days] = pd.to_numeric(rows.loc[missing_days, "total_days"], errors="coerce")
    missing_days = labor_days.isna()
    labor_days.loc[missing_days] = pd.to_numeric(rows.loc[missing_days, "days"], errors="coerce")
    rows["labor_days"] = labor_days
    crew_size = pd.to_numeric(rows["crew_size"], errors="coerce")
    fallback_hours = labor_days * crew_size * 8
    missing_hours = rows["hours"].isna() & fallback_hours.notna()
    rows.loc[missing_hours, "hours"] = fallback_hours.loc[missing_hours]
    rows["hours_per_1000_sqft"] = rows["hours"] / rows["area_sqft"] * 1000
    rows["hours_per_sqft"] = rows["hours"] / rows["area_sqft"]
    rows["cost_per_sqft"] = rows["total_cost"] / rows["area_sqft"]
    out = []
    group_cols = LABOR_RATE_GROUP_COLS
    rows = ensure_columns(rows, group_cols)
    for keys, group in rows.groupby(group_cols, dropna=False):
        key_values = dict(zip(group_cols, keys, strict=True))
        job_ids = sorted(set(group["job_id"].dropna().astype(str)))
        out.append(
            {
                **key_values,
                "coating_type": group.get("coating_type", pd.Series(dtype=object)).dropna().iloc[0] if "coating_type" in group.columns and group["coating_type"].notna().any() else None,
                "warranty_years": group.get("warranty_years", pd.Series(dtype=object)).dropna().iloc[0] if "warranty_years" in group.columns and group["warranty_years"].notna().any() else None,
                "median_hours_per_sqft": percentile(group.get("hours_per_sqft", pd.Series(dtype=float)), 0.5),
                "median_cost_per_sqft": percentile(group.get("cost_per_sqft", pd.Series(dtype=float)), 0.5),
                "median_total_hours": percentile(group.get("hours", pd.Series(dtype=float)), 0.5),
                "median_total_cost": percentile(group.get("total_cost", pd.Series(dtype=float)), 0.5),
                "median_crew_size": percentile(group.get("crew_size", pd.Series(dtype=float)), 0.5),
                "median_days": percentile(group.get("labor_days", pd.Series(dtype=float)), 0.5),
                "evidence_count": len(job_ids),
                "supporting_job_ids": json.dumps(job_ids),
                "labor_package": key_values.get("package"),
                "median_hours_per_1000_sqft": percentile(group.get("hours_per_1000_sqft", pd.Series(dtype=float)), 0.5),
                "p25_hours_per_1000_sqft": percentile(group.get("hours_per_1000_sqft", pd.Series(dtype=float)), 0.25),
                "p75_hours_per_1000_sqft": percentile(group.get("hours_per_1000_sqft", pd.Series(dtype=float)), 0.75),
                "job_count": len(job_ids),
                "confidence": confidence(len(job_ids)),
            }
        )
    return pd.DataFrame(out, columns=columns)


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


def normalize_suggestion_frame(frame: pd.DataFrame | None, required_columns: list[str]) -> pd.DataFrame:
    if frame is None:
        frame = pd.DataFrame()
    frame = frame.copy()
    for column in required_columns:
        if column not in frame.columns:
            frame[column] = None
    return frame


def ensure_job_count(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    has_usable_job_count = "job_count" in frame.columns and pd.to_numeric(frame["job_count"], errors="coerce").notna().any()
    if not has_usable_job_count:
        for candidate in ["evidence_count", "supporting_job_count", "n_jobs", "count"]:
            if candidate in frame.columns:
                frame["job_count"] = frame[candidate]
                break
        else:
            frame["job_count"] = 0
    frame["job_count"] = pd.to_numeric(frame["job_count"], errors="coerce").fillna(0)
    return frame


def ensure_cooccurrence_rate(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    has_usable_rate = "co_occurrence_rate" in frame.columns and pd.to_numeric(frame["co_occurrence_rate"], errors="coerce").notna().any()
    if not has_usable_rate:
        for candidate in ["support", "confidence", "rate"]:
            if candidate in frame.columns:
                frame["co_occurrence_rate"] = frame[candidate]
                break
        else:
            frame["co_occurrence_rate"] = 0
    frame["co_occurrence_rate"] = pd.to_numeric(frame["co_occurrence_rate"], errors="coerce").fillna(0)
    return frame


def suggestion_diagnostic(rule_type: str, message: str, frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "rule_type": "diagnostic",
        "category": rule_type,
        "message": message,
        "available_columns": list(frame.columns) if frame is not None else [],
    }


def print_suggestion_frame_debug(name: str, frame: pd.DataFrame | None) -> None:
    columns = list(frame.columns) if isinstance(frame, pd.DataFrame) else []
    rows = len(frame) if isinstance(frame, pd.DataFrame) else 0
    print(f"Rule suggestion input: {name} rows={rows} columns={columns}", flush=True)


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
        "diagnostics": [],
    }
    print_suggestion_frame_debug("warranty", warranty)
    print_suggestion_frame_debug("cooccurrence", cooccurrence)
    print_suggestion_frame_debug("material_ratios", material_ratios)
    print_suggestion_frame_debug("labor_rates", labor_rates)
    print_suggestion_frame_debug("anomalies", anomalies)

    warranty = ensure_job_count(normalize_suggestion_frame(warranty, ["coating_type", "warranty_years", "wet_mils", "median_gal_per_sqft", "job_count", "confidence"]))
    cooccurrence = ensure_cooccurrence_rate(ensure_job_count(normalize_suggestion_frame(cooccurrence, ["co_occurrence_rate", "job_count"])))
    material_ratios = ensure_job_count(normalize_suggestion_frame(material_ratios, ["package", "job_count", "confidence"]))
    labor_rates = ensure_job_count(normalize_suggestion_frame(labor_rates, ["project_type", "substrate", "coating_type", "warranty_years", "labor_package", "package", "median_hours_per_1000_sqft", "job_count", "confidence"]))
    anomalies = normalize_suggestion_frame(anomalies, ["anomaly_type"])

    if warranty.empty:
        rules["diagnostics"].append(suggestion_diagnostic("warranty_years_to_wet_mils", "Skipped warranty/coating suggestions because output was empty", warranty))
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

    if cooccurrence.empty:
        rules["diagnostics"].append(suggestion_diagnostic("project_substrate_likely_work_packages", "Skipped package co-occurrence suggestions because output was empty", cooccurrence))
    likely = cooccurrence[cooccurrence["co_occurrence_rate"] >= 0.5] if not cooccurrence.empty else cooccurrence.iloc[0:0].copy()
    for _, row in likely.sort_values(["job_count", "co_occurrence_rate"], ascending=False).head(100).iterrows():
        rules["project_substrate_likely_work_packages"].append(row.dropna().to_dict())

    if labor_rates.empty:
        rules["diagnostics"].append(suggestion_diagnostic("default_production_rates_by_labor_package", "Skipped labor rate suggestions because output was empty", labor_rates))
    for _, row in labor_rates.sort_values("job_count", ascending=False).head(100).iterrows():
        rate = to_number(row.get("median_hours_per_1000_sqft"))
        if rate:
            rules["default_production_rates_by_labor_package"].append(
                {
                    "project_type": row.get("project_type"),
                    "substrate": row.get("substrate"),
                    "coating_type": row.get("coating_type"),
                    "warranty_years": row.get("warranty_years"),
                    "labor_package": row.get("labor_package") or row.get("package"),
                    "median_hours_per_1000_sqft": rate,
                    "supporting_job_count": int(row.get("job_count") or 0),
                    "confidence": row.get("confidence"),
                }
            )

    if material_ratios.empty:
        rules["diagnostics"].append(suggestion_diagnostic("material_package_relationships", "Skipped material trigger suggestions because output was empty", material_ratios))
    primer_rows = material_ratios[material_ratios["package"].eq("primer")] if not material_ratios.empty and "package" in material_ratios.columns else material_ratios.iloc[0:0].copy()
    for _, row in primer_rows.sort_values("job_count", ascending=False).head(50).iterrows():
        rules["primer_inclusion_triggers"].append(row.dropna().to_dict())
    fastener_rows = material_ratios[material_ratios["package"].eq("fastener_treatment")] if not material_ratios.empty and "package" in material_ratios.columns else material_ratios.iloc[0:0].copy()
    for _, row in fastener_rows.sort_values("job_count", ascending=False).head(50).iterrows():
        rules["fastener_treatment_triggers"].append(row.dropna().to_dict())
    if anomalies.empty:
        rules["diagnostics"].append(suggestion_diagnostic("anomaly_summary", "Skipped anomaly summary because output was empty", anomalies))
    elif "anomaly_type" in anomalies.columns:
        rules["anomaly_summary"] = anomalies["anomaly_type"].value_counts().to_dict()
    return rules


def stable_id(prefix: str, *parts: Any) -> str:
    payload = "||".join(clean_text(part) for part in parts)
    return f"{prefix}-{hashlib.sha1(payload.encode('utf-8')).hexdigest()[:24]}"


def table_exists(engine: Engine, table_name: str) -> bool:
    return inspect(engine).has_table(table_name)


def quote_identifier(engine: Engine, identifier: str) -> str:
    return engine.dialect.identifier_preparer.quote(identifier)


def existing_table_columns(engine: Engine, table_name: str) -> set[str]:
    if not table_exists(engine, table_name):
        return set()
    return {column["name"] for column in inspect(engine).get_columns(table_name)}


def sql_type_for_series(series: pd.Series, dialect_name: str) -> str:
    if pd.api.types.is_bool_dtype(series):
        return "BOOLEAN"
    if pd.api.types.is_integer_dtype(series):
        return "BIGINT" if dialect_name == "postgresql" else "INTEGER"
    if pd.api.types.is_float_dtype(series):
        return "DOUBLE PRECISION" if dialect_name == "postgresql" else "REAL"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "TIMESTAMP"
    return "TEXT"


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
    if not table_exists(engine, table_name):
        frame.to_sql(table_name, engine, if_exists="replace", index=False, chunksize=1000)
        return

    existing_columns = existing_table_columns(engine, table_name)
    table_identifier = quote_identifier(engine, table_name)
    with engine.begin() as connection:
        for column in frame.columns:
            if column in existing_columns:
                continue
            column_identifier = quote_identifier(engine, column)
            column_type = sql_type_for_series(frame[column], engine.dialect.name)
            connection.execute(text(f"ALTER TABLE {table_identifier} ADD COLUMN {column_identifier} {column_type}"))
            existing_columns.add(column)
        connection.execute(text(f"DELETE FROM {table_identifier}"))
        if not frame.empty:
            frame.to_sql(table_name, connection, if_exists="append", index=False, chunksize=1000)


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


def line_items_from_template_rows(template_rows: pd.DataFrame) -> pd.DataFrame:
    if template_rows.empty:
        return pd.DataFrame()
    rows = template_rows.copy()
    rows = rows.rename(
        columns={
            "template_row_id": "line_item_id",
            "source_file": "estimate_file",
            "sheet_name": "source_sheet",
            "row_number": "source_row",
            "selected_item_name": "line_item_name",
            "template_section": "section",
            "unit_price": "unit_cost",
            "estimated_cost": "extended_cost",
            "days": "labor_days",
            "total_hours": "labor_hours",
        }
    )
    if "category" not in rows.columns:
        rows["category"] = rows.get("line_item_kind")
    if "line_item_name" not in rows.columns:
        rows["line_item_name"] = None
    rows["line_item_name"] = rows["line_item_name"].where(rows["line_item_name"].notna(), rows.get("row_label"))
    rows["line_item_name"] = rows["line_item_name"].where(rows["line_item_name"].notna(), rows.get("template_bucket"))
    rows["source_type_table"] = "estimate_template_rows"
    return rows


def line_items_from_classifications(classifications: pd.DataFrame) -> pd.DataFrame:
    if classifications.empty:
        return pd.DataFrame()
    rows = classifications.copy()
    rows = rows.rename(
        columns={
            "source_file": "estimate_file",
            "sheet_name": "source_sheet",
            "row_number": "source_row",
            "raw_item_name": "line_item_name",
            "raw_description": "description",
            "template_section": "section",
            "line_total": "extended_cost",
        }
    )
    if "category" not in rows.columns:
        rows["category"] = rows.get("line_item_kind")
    if "line_item_name" not in rows.columns:
        rows["line_item_name"] = rows.get("normalized_item_name")
    rows["source_type_table"] = "estimate_line_item_classifications"
    return rows


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
        *JOB_CONTEXT_COLUMNS,
    ]
    source = ensure_columns(source, keep)
    return source[keep].drop_duplicates("job_id")


def first_nonmissing(values: pd.Series) -> Any:
    for value in values:
        if clean_text(value):
            return value
    return None


def value_from_template_row(row: pd.Series) -> float | None:
    for column in ("quantity", "estimated_units", "estimated_cost", "unit_price", "total_hours", "warranty_years"):
        number = to_number(row.get(column))
        if number and number > 0:
            return number
    text = " ".join(clean_text(row.get(column)) for column in ("selected_item_name", "row_label", "raw_text", "cell_values") if column in row.index)
    matches = re.findall(r"\b\d[\d,]*(?:\.\d+)?\b", text)
    for match in matches:
        number = to_number(match)
        if number and number > 0:
            return number
    return None


def job_context_from_template_rows(template_rows: pd.DataFrame) -> pd.DataFrame:
    if template_rows.empty or "job_id" not in template_rows.columns:
        return pd.DataFrame()
    rows = ensure_columns(template_rows, ["job_id", "template_type", "template_bucket", "selected_item_name", "row_label", "quantity", "estimated_units", "estimated_cost", "unit_price", "warranty_years"])
    out = []
    for job_id, group in rows.groupby("job_id", dropna=False):
        if not clean_text(job_id):
            continue
        context: dict[str, Any] = {"job_id": job_id}
        if group["template_type"].notna().any():
            context["template_type"] = first_nonmissing(group["template_type"])
        for bucket, field in {
            "job_name": "job_name",
            "job_type": "project_type",
            "estimated_square_feet": "area_sqft",
            "warranty": "warranty_years",
        }.items():
            bucket_rows = group[group["template_bucket"].astype(str).eq(bucket)]
            if bucket_rows.empty:
                continue
            if field == "area_sqft":
                for _, row in bucket_rows.iterrows():
                    value = value_from_template_row(row)
                    if value:
                        context[field] = value
                        break
            elif field == "warranty_years":
                value = first_nonmissing(bucket_rows["warranty_years"])
                if value is None:
                    for _, row in bucket_rows.iterrows():
                        value = value_from_template_row(row)
                        if value:
                            break
                context[field] = value
            else:
                context[field] = first_nonmissing(bucket_rows.get("selected_item_name", pd.Series(dtype=object))) or first_nonmissing(bucket_rows.get("row_label", pd.Series(dtype=object)))
        out.append(context)
    if not out:
        return pd.DataFrame()
    context = pd.DataFrame(out)
    context = ensure_columns(context, ["job_id", *JOB_CONTEXT_COLUMNS])
    for column in ["area_sqft", "warranty_years"]:
        context[column] = pd.to_numeric(context[column], errors="coerce")
    context["area_bucket"] = context["area_sqft"].apply(area_bucket)
    return context


def enrich_jobs_with_template_context(estimate_jobs: pd.DataFrame, template_rows: pd.DataFrame) -> pd.DataFrame:
    template_context = job_context_from_template_rows(template_rows)
    if template_context.empty:
        return ensure_columns(estimate_jobs, ["job_id", *JOB_CONTEXT_COLUMNS]) if not estimate_jobs.empty else estimate_jobs
    if estimate_jobs.empty:
        return template_context[["job_id", *JOB_CONTEXT_COLUMNS]].drop_duplicates("job_id")
    merged = merge_job_context(estimate_jobs, template_context)
    return ensure_columns(merged, ["job_id", *JOB_CONTEXT_COLUMNS])[["job_id", *JOB_CONTEXT_COLUMNS]].drop_duplicates("job_id")


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
    for column in ["quantity", "estimated_units", "unit_cost", "total_cost", "labor_hours", "labor_days", "crew_size"]:
        if column in normalized.columns:
            normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
        else:
            normalized[column] = math.nan
    if "unit" not in normalized.columns:
        normalized["unit"] = ""
    normalized["unit"] = normalized["unit"].apply(canonical_unit)
    if "job_id" not in normalized.columns:
        normalized["job_id"] = ""
    if "template_bucket" not in normalized.columns:
        normalized["template_bucket"] = ""
    normalized["template_bucket"] = normalized["template_bucket"].apply(normalized_template_bucket)
    normalized["package"] = normalized.apply(classify_package, axis=1)
    normalized["line_type"] = normalized.apply(lambda row: "labor" if is_labor_row(row) else "material", axis=1)
    template_material_units = (
        normalized.get("source_type_table", pd.Series("", index=normalized.index)).astype(str).eq("estimate_template_rows")
        & normalized["line_type"].eq("material")
        & pd.to_numeric(normalized["estimated_units"], errors="coerce").gt(0)
        & normalized["package"].isin(ESTIMATED_UNITS_PHYSICAL_UNIT_BY_PACKAGE)
    )
    normalized["scope_quantity"] = normalized["quantity"]
    normalized.loc[template_material_units, "quantity"] = normalized.loc[template_material_units, "estimated_units"]
    missing_unit = normalized["unit"].astype(str).str.strip().eq("") | normalized["unit"].isna()
    for package, inferred_unit in ESTIMATED_UNITS_PHYSICAL_UNIT_BY_PACKAGE.items():
        mask = template_material_units & normalized["package"].eq(package) & missing_unit
        normalized.loc[mask, "unit"] = inferred_unit
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
        "template_bucket",
        "template_type",
        "line_item_kind",
        "normalized_item_name",
        "item_name",
        "category",
        "section",
        "description",
        "quantity",
        "estimated_units",
        "scope_quantity",
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
    rows = ensure_columns(rows, ["normalized_line_item_id", "package", "source_type", "physical_quantity_valid", "review_required", "labor_hours", "labor_days", "crew_size", "quantity", "total_cost", *JOB_CONTEXT_COLUMNS])
    rows["total_cost"] = pd.to_numeric(rows.get("total_cost"), errors="coerce")
    rows["quantity"] = pd.to_numeric(rows.get("quantity"), errors="coerce")
    rows["labor_hours"] = pd.to_numeric(rows.get("labor_hours"), errors="coerce")
    rows["labor_days"] = pd.to_numeric(rows.get("labor_days"), errors="coerce")
    rows["crew_size"] = pd.to_numeric(rows.get("crew_size"), errors="coerce")
    out = []
    for (job_id, package), group in rows.groupby(["job_id", "package"], dropna=False):
        valid_quantity = group[group["physical_quantity_valid"].astype(bool)]
        units = sorted(set(valid_quantity["unit"].dropna().astype(str)))
        unit = units[0] if len(units) == 1 else "mixed" if len(units) > 1 else ""
        total_quantity = valid_quantity["quantity"].sum() if not valid_quantity.empty and unit != "mixed" else math.nan
        total_cost = group["total_cost"].sum(min_count=1)
        area = to_number(group["area_sqft"].dropna().iloc[0]) if "area_sqft" in group.columns and group["area_sqft"].notna().any() else None
        total_hours = group["labor_hours"].sum(min_count=1) if "labor_hours" in group.columns else math.nan
        total_days = group["labor_days"].sum(min_count=1) if "labor_days" in group.columns else math.nan
        median_crew_size = group["crew_size"].median() if "crew_size" in group.columns else math.nan
        line_ids = sorted(set(group["normalized_line_item_id"].dropna().astype(str)))
        context = {column: (group[column].dropna().iloc[0] if column in group.columns and group[column].notna().any() else None) for column in JOB_CONTEXT_COLUMNS}
        out.append(
            {
                "job_id": job_id,
                **context,
                "package": package,
                "included": True,
                "total_quantity": total_quantity,
                "unit": unit,
                "total_cost": total_cost,
                "total_hours": total_hours,
                "total_days": total_days,
                "crew_size": median_crew_size,
                "qty_per_sqft": total_quantity / area if area and total_quantity == total_quantity else math.nan,
                "cost_per_sqft": total_cost / area if area and total_cost == total_cost else math.nan,
                "hours_per_sqft": total_hours / area if area and total_hours == total_hours else math.nan,
                "has_physical_quantity": bool(group["physical_quantity_valid"].astype(bool).any()),
                "has_allowance": bool(group["source_type"].eq("cost_allowance").any()),
                "review_required": bool(group["review_required"].astype(bool).any()),
                "evidence_line_item_ids": json.dumps(line_ids),
            }
        )
    return ensure_columns(pd.DataFrame(out), ["job_id", *JOB_CONTEXT_COLUMNS, "package", "included", "total_quantity", "unit", "total_cost", "total_hours", "total_days", "crew_size", "qty_per_sqft", "cost_per_sqft", "hours_per_sqft", "has_physical_quantity", "has_allowance", "review_required", "evidence_line_item_ids"])


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
    rows = ensure_columns(rows, ["package", "total_quantity", "total_cost", *JOB_CONTEXT_COLUMNS])
    rows["is_material"] = ~rows["package"].astype(str).str.startswith("labor")
    rows["quantity"] = rows.get("total_quantity")
    rows["total_cost"] = rows.get("total_cost")
    return build_material_qty_ratios(rows)


def print_relationship_generation_summary(summary_with_jobs: pd.DataFrame) -> None:
    expected = ["source_year", "division", "template_type", *MATERIAL_RATIO_GROUP_COLS, "area_sqft", "roof_condition"]
    missing = [column for column in expected if column not in summary_with_jobs.columns]
    print(
        "Relationship generation input:",
        f"rows={len(summary_with_jobs)}",
        f"columns_present={sorted(summary_with_jobs.columns.tolist())}",
        f"missing_expected_context_columns={missing}",
        flush=True,
    )


def build_relationship_input_diagnostics(package_summary: pd.DataFrame, normalized: pd.DataFrame, estimate_jobs: pd.DataFrame) -> pd.DataFrame:
    rows = [
        {"metric": "package_summary_rows", "value": len(package_summary)},
        {"metric": "normalized_line_item_rows", "value": len(normalized)},
        {"metric": "estimate_jobs_rows", "value": len(estimate_jobs)},
    ]
    if not package_summary.empty:
        job_summary = package_summary.drop_duplicates("job_id") if "job_id" in package_summary.columns else package_summary
        rows.extend(
            [
                {"metric": "jobs_with_area_sqft", "value": int((pd.to_numeric(job_summary.get("area_sqft"), errors="coerce") > 0).sum()) if "area_sqft" in job_summary.columns else 0},
                {"metric": "jobs_missing_area_sqft", "value": int((pd.to_numeric(job_summary.get("area_sqft"), errors="coerce").fillna(0) <= 0).sum()) if "area_sqft" in job_summary.columns else len(job_summary)},
                {"metric": "jobs_with_warranty_years", "value": int(pd.to_numeric(job_summary.get("warranty_years"), errors="coerce").notna().sum()) if "warranty_years" in job_summary.columns else 0},
                {"metric": "jobs_with_wet_mils", "value": int(pd.to_numeric(job_summary.get("wet_mils"), errors="coerce").notna().sum()) if "wet_mils" in job_summary.columns else 0},
            ]
        )
    if not normalized.empty and "template_bucket" in normalized.columns:
        for bucket, count in normalized["template_bucket"].fillna("unknown").replace("", "unknown").value_counts().items():
            rows.append({"metric": f"template_bucket:{bucket}", "value": int(count)})
    return pd.DataFrame(rows)


def build_package_normalization_diagnostics(normalized: pd.DataFrame) -> pd.DataFrame:
    columns = ["template_bucket", "package", "line_type", "row_count", "rows_with_hours", "rows_with_cost"]
    if normalized.empty:
        return pd.DataFrame(columns=columns)
    rows = ensure_columns(normalized, ["template_bucket", "package", "line_type", "labor_hours", "total_cost"])
    out = (
        rows.groupby(["template_bucket", "package", "line_type"], dropna=False, as_index=False)
        .agg(
            row_count=("package", "size"),
            rows_with_hours=("labor_hours", lambda values: int(pd.to_numeric(values, errors="coerce").notna().sum())),
            rows_with_cost=("total_cost", lambda values: int(pd.to_numeric(values, errors="coerce").notna().sum())),
        )
        .sort_values(["row_count", "package"], ascending=[False, True])
    )
    return out[columns]


def build_missing_job_context(package_summary: pd.DataFrame) -> pd.DataFrame:
    columns = ["job_id", "missing_context_fields"]
    if package_summary.empty or "job_id" not in package_summary.columns:
        return pd.DataFrame(columns=columns)
    job_rows = ensure_columns(package_summary.drop_duplicates("job_id"), ["job_id", *JOB_CONTEXT_COLUMNS])
    rows = []
    for _, row in job_rows.iterrows():
        missing = []
        for column in ["area_sqft", "source_year", "division", "pipeline_status", "status", "template_type", "project_type", "substrate", "warranty_years", "wet_mils", "coating_type", "roof_condition", "access_complexity"]:
            value = row.get(column)
            if column in {"area_sqft", "warranty_years", "wet_mils"}:
                if to_number(value) is None:
                    missing.append(column)
            elif not clean_text(value):
                missing.append(column)
        if missing:
            rows.append({"job_id": row.get("job_id"), "missing_context_fields": ",".join(missing)})
    return pd.DataFrame(rows, columns=columns)


def build_labor_rate_diagnostics(package_summary: pd.DataFrame) -> pd.DataFrame:
    columns = ["package", "row_count", "rows_with_total_hours", "rows_with_total_cost", "rows_with_area_sqft", "excluded_missing_area", "excluded_missing_hours_and_cost"]
    if package_summary.empty or "package" not in package_summary.columns:
        return pd.DataFrame(columns=columns)
    rows = package_summary[package_summary["package"].astype(str).str.startswith("labor")].copy()
    if rows.empty:
        return pd.DataFrame(columns=columns)
    rows = ensure_columns(rows, ["package", "total_hours", "total_cost", "area_sqft"])
    rows["_has_hours"] = pd.to_numeric(rows["total_hours"], errors="coerce").notna()
    rows["_has_cost"] = pd.to_numeric(rows["total_cost"], errors="coerce").notna()
    rows["_has_area"] = pd.to_numeric(rows["area_sqft"], errors="coerce") > 0
    out = (
        rows.groupby("package", dropna=False, as_index=False)
        .agg(
            row_count=("package", "size"),
            rows_with_total_hours=("_has_hours", "sum"),
            rows_with_total_cost=("_has_cost", "sum"),
            rows_with_area_sqft=("_has_area", "sum"),
            excluded_missing_area=("_has_area", lambda values: int((~values).sum())),
            excluded_missing_hours_and_cost=("_has_hours", lambda values: 0),
        )
    )
    missing_hours_cost = rows.assign(_missing_hours_cost=~(rows["_has_hours"] | rows["_has_cost"])).groupby("package")["_missing_hours_cost"].sum()
    out["excluded_missing_hours_and_cost"] = out["package"].map(missing_hours_cost).fillna(0).astype(int)
    return out[columns]


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
    existing_template_rows = read_table(engine, "estimate_template_rows")
    existing_classifications = read_table(engine, "estimate_line_item_classifications")
    existing_jobs = read_table(engine, "jobs")
    existing_estimates = read_table(engine, "estimates")
    if not existing_template_rows.empty:
        source_line_items = line_items_from_template_rows(existing_template_rows)
        source_name = "estimate_template_rows"
    elif not existing_classifications.empty:
        source_line_items = line_items_from_classifications(existing_classifications)
        source_name = "estimate_line_item_classifications"
    else:
        source_line_items = existing_line_items
        source_name = "estimate_line_items"
    if source_line_items.empty:
        raise RuntimeError("No extracted estimate line item tables have data; load estimate_template_rows, estimate_line_item_classifications, or estimate_line_items before profiling.")
    print(f"Relationship profiler source table: {source_name} ({len(source_line_items)} rows)", flush=True)

    source_documents = source_documents_from_line_items(source_line_items)
    raw = raw_line_items_from_existing(source_line_items, source_documents)
    normalized = normalize_raw_line_items(raw)
    estimate_jobs = enrich_jobs_with_template_context(normalize_jobs_from_database(existing_jobs, existing_estimates), existing_template_rows)
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
    print_relationship_generation_summary(summary_with_jobs)
    package_rows = material_rows_from_package_summary(filtered_summary, filtered_jobs)
    labor_rows = package_rows[package_rows["package"].astype(str).str.startswith("labor")].copy() if not package_rows.empty and "package" in package_rows.columns else pd.DataFrame()
    labor_rows_for_anomalies = labor_rows_from_normalized(filtered_normalized, filtered_jobs)
    normalized_material_rows = filtered_normalized[filtered_normalized["line_type"].ne("labor")].copy()
    normalized_material_rows["is_material"] = True
    normalized_material_rows["is_labor"] = False
    normalized_material_rows = merge_job_context(normalized_material_rows, filtered_jobs)

    outputs = {
        "relationship_warranty_coating.csv": build_warranty_coating(package_rows),
        "relationship_package_cooccurrence.csv": build_work_package_cooccurrence(package_rows),
        "relationship_work_package_cooccurrence.csv": build_work_package_cooccurrence(package_rows),
        "relationship_material_qty_ratios.csv": material_qty_ratios_from_summary(summary_with_jobs),
        "relationship_labor_rates.csv": build_labor_rates(labor_rows),
        "relationship_anomalies.csv": build_anomalies(normalized_material_rows, labor_rows_for_anomalies),
    }
    diagnostics = {
        "relationship_input_diagnostics.csv": build_relationship_input_diagnostics(package_summary, normalized, estimate_jobs),
        "package_normalization_diagnostics.csv": build_package_normalization_diagnostics(normalized),
        "missing_job_context.csv": build_missing_job_context(package_summary),
        "labor_rate_diagnostics.csv": build_labor_rate_diagnostics(package_summary),
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
    for filename, frame in diagnostics.items():
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
            diagnostics["labor_rate_diagnostics.csv"].head(5000).to_excel(writer, sheet_name="labor_diagnostics", index=False)
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
