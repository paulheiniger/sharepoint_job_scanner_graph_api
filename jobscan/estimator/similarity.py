from __future__ import annotations

from typing import Any

import pandas as pd

from .rules import first_nonblank, to_float
from .schemas import EstimatorData


def _contains(value: Any, needle: str) -> bool:
    return bool(needle) and needle.lower() in str(value or "").lower()


def _pct_similarity(target: float | None, candidate: float | None) -> float:
    if not target or not candidate or target <= 0 or candidate <= 0:
        return 0.0
    ratio = min(target, candidate) / max(target, candidate)
    return max(0.0, min(1.0, ratio))


def historical_jobs(data: EstimatorData) -> pd.DataFrame:
    jobs = data.jobs.copy()
    estimates = data.estimates.copy()
    if jobs.empty and estimates.empty:
        return pd.DataFrame()
    if jobs.empty:
        return estimates
    if estimates.empty or "job_id" not in jobs.columns or "job_id" not in estimates.columns:
        return jobs

    estimate_cols = [
        column
        for column in [
            "job_id",
            "estimate_id",
            "estimate_file",
            "estimate_scope_type",
            "coating_type",
            "coating_required",
            "estimated_sqft",
            "wall_area_sqft",
            "total_job_cost",
            "final_price",
            "price_per_sqft",
            "estimated_labor_hours",
            "estimated_duration_days",
            "estimated_crew_size",
            "folder_url",
        ]
        if column in estimates.columns
    ]
    estimates_one = estimates.sort_values("estimate_id" if "estimate_id" in estimates.columns else "job_id").drop_duplicates("job_id")
    merged = jobs.merge(estimates_one[estimate_cols], on="job_id", how="left", suffixes=("", "_estimate"))
    for column in ("estimated_sqft", "total_job_cost", "final_price", "price_per_sqft", "estimated_labor_hours", "estimated_duration_days", "estimated_crew_size", "folder_url"):
        estimate_column = f"{column}_estimate"
        if estimate_column in merged.columns:
            merged[column] = merged[column].where(merged[column].notna(), merged[estimate_column]) if column in merged.columns else merged[estimate_column]
    return merged


def score_job(row: pd.Series, scope: dict[str, Any]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    haystack = " ".join(
        str(row.get(column) or "")
        for column in ["job_name", "customer", "job_type", "estimate_scope_type", "city", "state", "site_address"]
    ).lower()

    division = first_nonblank(scope.get("division")).lower()
    row_division = first_nonblank(row.get("division")).lower()
    if division and row_division and division == row_division:
        score += 18
        reasons.append("same division")

    for field, label, weight in [
        ("project_type", "project type", 18),
        ("substrate", "substrate", 14),
        ("coating_type", "coating type", 16),
        ("roof_condition", "condition", 8),
        ("access_complexity", "access", 6),
    ]:
        value = first_nonblank(scope.get(field)).lower()
        if value and (_contains(row.get(field), value) or value in haystack):
            score += weight
            reasons.append(f"matched {label}")

    if scope.get("coating_required") and bool(row.get("coating_required")):
        score += 6
        reasons.append("coating job")

    target_sqft = to_float(scope.get("surface_area_sqft")) or to_float(scope.get("wall_area_sqft"))
    candidate_sqft = to_float(row.get("estimated_sqft")) or to_float(row.get("wall_area_sqft"))
    sqft_score = _pct_similarity(target_sqft, candidate_sqft)
    if sqft_score:
        score += 22 * sqft_score
        reasons.append("similar size")

    location = first_nonblank(scope.get("location")).lower()
    if location and any(part.strip() and part.strip() in haystack for part in location.replace(",", " ").split()):
        score += 6
        reasons.append("location keyword")

    return round(score, 2), reasons


def _roofing_scope(scope: dict[str, Any]) -> bool:
    text = " ".join(str(value or "") for value in scope.values()).lower()
    return "roof" in text and ("coat" in text or "coating" in text or "silicone" in text or "acrylic" in text)


def _similarity_evidence_flags(row: pd.Series, scope: dict[str, Any], reasons: list[str]) -> dict[str, Any]:
    reason_text = ", ".join(reasons).lower()
    haystack = " ".join(
        str(row.get(column) or "")
        for column in [
            "template_type",
            "job_template_type",
            "job_name",
            "customer",
            "job_type",
            "estimate_scope_type",
            "estimate_file",
            "division",
        ]
    ).lower()
    outlier_reasons: list[str] = []
    price_per_sqft = to_float(row.get("price_per_sqft"))
    if price_per_sqft is not None and (price_per_sqft < 1 or price_per_sqft > 75):
        outlier_reasons.append("price_per_sqft_outlier")
    if any(term in haystack for term in ("all trades", "facade", "façade", "skylight", "repair only", "nte", "budgetary", "insulation", "spray foam")):
        outlier_reasons.append("scope_outlier_keyword")

    strong_reason_count = sum(
        1
        for term in (
            "project type",
            "substrate",
            "coating type",
            "coating job",
            "warranty",
            "roof coating",
        )
        if term in reason_text or term in haystack
    )
    weak_reason_count = sum(1 for term in ("same division", "similar size", "location keyword") if term in reason_text)
    has_roofing_signal = any(term in haystack for term in ("roof", "roofing", "coating", "silicone", "acrylic", "metal roof"))
    included = True
    exclusion_reason = ""
    match_strength = "strong" if strong_reason_count >= 2 and (has_roofing_signal or not _roofing_scope(scope)) else "weak"
    if _roofing_scope(scope) and match_strength != "strong":
        included = False
        exclusion_reason = "Weak-only similar job match; not used as estimator evidence."
    if outlier_reasons:
        included = False
        exclusion_reason = "; ".join(outlier_reasons)
    return {
        "match_strength": match_strength,
        "strong_reason_count": strong_reason_count,
        "weak_reason_count": weak_reason_count,
        "included_as_evidence": included,
        "exclusion_reason": exclusion_reason,
    }


def find_similar_jobs(data: EstimatorData, scope: dict[str, Any], limit: int = 8) -> pd.DataFrame:
    history = historical_jobs(data)
    if history.empty:
        return pd.DataFrame()

    rows = []
    for _, row in history.iterrows():
        score, reasons = score_job(row, scope)
        if score <= 0:
            continue
        out = row.to_dict()
        out["similarity_score"] = score
        out["reason_matched"] = ", ".join(reasons[:5])
        out["estimated_value"] = row.get("estimated_value") or row.get("final_price") or row.get("total_job_cost")
        out.update(_similarity_evidence_flags(row, scope, reasons))
        rows.append(out)
    if not rows:
        return pd.DataFrame()
    similar = pd.DataFrame(rows).sort_values("similarity_score", ascending=False)
    keep = [
        "job_id",
        "customer",
        "job_name",
        "division",
        "pipeline_status",
        "status",
        "job_type",
        "estimated_sqft",
        "estimated_value",
        "total_job_cost",
        "final_price",
        "price_per_sqft",
        "estimate_file",
        "folder_url",
        "similarity_score",
        "reason_matched",
        "match_strength",
        "strong_reason_count",
        "weak_reason_count",
        "included_as_evidence",
        "exclusion_reason",
    ]
    return similar[[column for column in keep if column in similar.columns]].head(limit)
