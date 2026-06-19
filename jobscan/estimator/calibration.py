from __future__ import annotations

from typing import Any

import pandas as pd

from .rules import first_nonblank, to_float


def _median_positive(df: pd.DataFrame, column: str) -> float | None:
    if column not in df.columns or df.empty:
        return None
    values = pd.to_numeric(df[column], errors="coerce").dropna()
    values = values[values > 0]
    return float(values.median()) if not values.empty else None


def _range_positive(df: pd.DataFrame, column: str) -> dict[str, float | None]:
    if column not in df.columns or df.empty:
        return {"low": None, "high": None}
    values = pd.to_numeric(df[column], errors="coerce").dropna()
    values = values[values > 0]
    if values.empty:
        return {"low": None, "high": None}
    return {"low": float(values.quantile(0.25)), "high": float(values.quantile(0.75))}


def _cost_per_sqft(line_items: pd.DataFrame, similar_job_ids: list[str], category_keywords: tuple[str, ...], job_sqft: dict[str, float]) -> float | None:
    if line_items.empty or "job_id" not in line_items.columns or not similar_job_ids:
        return None
    rows = line_items[line_items["job_id"].astype(str).isin(similar_job_ids)].copy()
    if rows.empty or "extended_cost" not in rows.columns:
        return None
    haystack = rows.apply(
        lambda row: " ".join(str(row.get(column) or "") for column in ("section", "line_item_category", "line_item_name", "description")).lower(),
        axis=1,
    )
    mask = pd.Series(False, index=rows.index)
    for keyword in category_keywords:
        mask |= haystack.str.contains(keyword, regex=False, na=False)
    rows = rows[mask]
    rows["extended_cost"] = pd.to_numeric(rows["extended_cost"], errors="coerce")
    values = []
    for _, row in rows.iterrows():
        sqft = job_sqft.get(str(row.get("job_id")))
        cost = to_float(row.get("extended_cost"))
        if sqft and cost and sqft > 0:
            values.append(cost / sqft)
    if not values:
        return None
    return float(pd.Series(values).median())


def calibrate_from_history(similar_jobs: pd.DataFrame, line_items: pd.DataFrame, scope: dict[str, Any]) -> dict[str, Any]:
    if similar_jobs.empty:
        return {
            "median_price_per_sqft": None,
            "median_labor_cost_per_sqft": None,
            "median_material_cost_per_sqft": None,
            "observed_price_per_sqft_range": {"low": None, "high": None},
            "evidence_job_count": 0,
            "calibration_notes": ["No similar jobs available for calibration."],
        }

    jobs = similar_jobs.copy()
    if "price_per_sqft" not in jobs.columns:
        value_col = "estimated_value" if "estimated_value" in jobs.columns else "final_price" if "final_price" in jobs.columns else ""
        if value_col and "estimated_sqft" in jobs.columns:
            jobs["price_per_sqft"] = pd.to_numeric(jobs[value_col], errors="coerce") / pd.to_numeric(jobs["estimated_sqft"], errors="coerce")
    job_sqft = {
        str(row.get("job_id")): float(row.get("estimated_sqft"))
        for _, row in jobs.iterrows()
        if to_float(row.get("estimated_sqft"))
    }
    similar_ids = [str(value) for value in jobs.get("job_id", pd.Series(dtype=str)).dropna().tolist()]
    material_psf = _cost_per_sqft(line_items, similar_ids, ("material", "coating", "silicone", "acrylic", "foam", "primer"), job_sqft)
    labor_psf = _cost_per_sqft(line_items, similar_ids, ("labor", "crew", "hours", "days"), job_sqft)
    notes = []
    project_type = first_nonblank(scope.get("project_type"))
    if project_type:
        notes.append(f"Calibration filtered through top similar jobs for {project_type}.")
    return {
        "median_price_per_sqft": _median_positive(jobs, "price_per_sqft"),
        "median_labor_cost_per_sqft": labor_psf,
        "median_material_cost_per_sqft": material_psf,
        "observed_price_per_sqft_range": _range_positive(jobs, "price_per_sqft"),
        "evidence_job_count": int(len(jobs)),
        "calibration_notes": notes,
    }
