from __future__ import annotations

import argparse
import json
import math
import re
from itertools import combinations
from pathlib import Path
from typing import Any

import pandas as pd


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
    parser.add_argument("--jobs", type=Path, default=Path("output/job_index.csv"), help="Job-level CSV. Defaults to output/job_index.csv.")
    parser.add_argument("--estimate-summary", type=Path, default=Path("output/estimate_summary.csv"), help="Estimate summary CSV. Defaults to output/estimate_summary.csv.")
    parser.add_argument("--line-items", type=Path, default=Path("output/estimate_line_items.csv"), help="Material/labor line item CSV. Defaults to output/estimate_line_items.csv.")
    parser.add_argument("--out-dir", type=Path, default=Path("output/relationships"), help="Directory for relationship profile outputs.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
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
