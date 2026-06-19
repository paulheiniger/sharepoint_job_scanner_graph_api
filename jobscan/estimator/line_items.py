from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from .rules import to_float

TEMPLATE_BUCKETS = [
    "foam",
    "coating",
    "thinner",
    "granules",
    "primer",
    "caulk_sealant",
    "seams_misc",
    "penetrations",
    "hvac_units",
    "drains",
    "board_stock",
    "fasteners",
    "plates",
    "dumpsters",
    "lift",
    "delivery_fee",
    "fabric",
    "edge_metal",
    "gutter",
    "downspouts",
    "roof_hatch",
    "scuppers",
    "curbs",
    "ladders",
    "pitch_pockets",
    "generator",
    "freight",
    "sales_inspection_trips",
    "truck_expense",
    "labor_prep",
    "labor_prime",
    "labor_seam_sealer",
    "labor_base",
    "labor_top_coat",
    "labor_details",
    "labor_cleanup",
    "labor_loading",
    "labor_traveling",
    "meals_lodging",
    "overhead_profit",
    "other",
    "unknown",
]

BUCKET_SECTIONS = {
    "foam": "materials",
    "coating": "materials",
    "thinner": "materials",
    "granules": "materials",
    "primer": "materials",
    "caulk_sealant": "materials",
    "seams_misc": "materials",
    "penetrations": "materials",
    "hvac_units": "materials",
    "drains": "materials",
    "board_stock": "materials",
    "fasteners": "materials",
    "plates": "materials",
    "dumpsters": "equipment",
    "lift": "equipment",
    "delivery_fee": "equipment",
    "fabric": "materials",
    "edge_metal": "materials",
    "gutter": "materials",
    "downspouts": "materials",
    "roof_hatch": "materials",
    "scuppers": "materials",
    "curbs": "materials",
    "ladders": "materials",
    "pitch_pockets": "materials",
    "generator": "equipment",
    "freight": "travel",
    "sales_inspection_trips": "travel",
    "truck_expense": "travel",
    "labor_prep": "labor",
    "labor_prime": "labor",
    "labor_seam_sealer": "labor",
    "labor_base": "labor",
    "labor_top_coat": "labor",
    "labor_details": "labor",
    "labor_cleanup": "labor",
    "labor_loading": "labor",
    "labor_traveling": "labor",
    "meals_lodging": "travel",
    "overhead_profit": "overhead_profit",
    "other": "other",
    "unknown": "unknown",
}

BUCKET_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("meals_lodging", ("lodging", "hotel", "meal", "per diem")),
    ("overhead_profit", ("overhead", "profit", "markup")),
    ("labor_loading", ("loading", "load out", "load-in", "load in")),
    ("labor_traveling", ("traveling", "travel labor")),
    ("labor_prep", ("prep", "pressure wash", "pwash", "grind", "patch")),
    ("labor_prime", ("prime", "primer labor")),
    ("labor_seam_sealer", ("seam sealer", "seam labor")),
    ("labor_base", ("base coat", "base ")),
    ("labor_top_coat", ("top coat", "finish coat")),
    ("labor_cleanup", ("cleanup", "clean up", "touch")),
    ("labor_details", ("detail", "caulk/sf")),
    ("sales_inspection_trips", ("sales trip", "inspection", "inspect")),
    ("truck_expense", ("truck", "mileage", "miles")),
    ("freight", ("freight",)),
    ("delivery_fee", ("delivery",)),
    ("dumpsters", ("dumpster",)),
    ("lift", ("lift", "boom", "scissor", "forklift", "articulating")),
    ("generator", ("generator",)),
    ("roof_hatch", ("roof hatch",)),
    ("pitch_pockets", ("pitch pocket", "pitch pan", "pipe boot")),
    ("penetrations", ("penetration", "pipe boot", "pipe flashing")),
    ("hvac_units", ("hvac", "unit curb", "unit")),
    ("curbs", ("curb",)),
    ("drains", ("drain",)),
    ("scuppers", ("scupper",)),
    ("ladders", ("ladder",)),
    ("downspouts", ("downspout",)),
    ("gutter", ("gutter",)),
    ("edge_metal", ("edge metal", "coping", "termination bar")),
    ("fasteners", ("fastener", "screw")),
    ("plates", ("plate",)),
    ("board_stock", ("board", "iso", "cover board", "dens deck", "hd board", "gypsum")),
    ("fabric", ("fabric", "stitchbond", "roll")),
    ("caulk_sealant", ("caulk", "sealant", "flashing grade", "buttergrade", "sausage")),
    ("seams_misc", ("seam", "seams", "tape")),
    ("primer", ("primer", "bleed block", "red zinc", "e-5320")),
    ("granules", ("granule", "granules")),
    ("thinner", ("thinner", "solvent", "xylene", "naphtha", "mineral spirits")),
    ("foam", ("spray foam", "polyurethane foam", "spf", " foam", "gaco roof 2.7", "basf roof")),
    ("coating", ("silicone", "acrylic", "urethane", "coating", "topcoat", "top coat")),
]

SOURCE_ROW_BUCKETS = {
    116: "labor_prep",
    118: "labor_prime",
    120: "labor_seam_sealer",
    122: "labor_base",
    124: "labor_top_coat",
    126: "labor_details",
    128: "labor_details",
    130: "labor_top_coat",
    132: "labor_cleanup",
    134: "other",
    137: "labor_loading",
    139: "labor_traveling",
    145: "meals_lodging",
}

IMPORTANT_MATERIAL_BUCKETS = {
    "foam",
    "coating",
    "thinner",
    "granules",
    "primer",
    "caulk_sealant",
    "fabric",
    "board_stock",
    "fasteners",
    "plates",
}


@dataclass(frozen=True)
class ClassificationResult:
    template_bucket: str
    template_section: str
    classification_confidence: float
    classification_reason: str
    needs_review: bool


def _row_text(row: dict[str, Any] | pd.Series) -> str:
    return " ".join(
        str(row.get(column) or "")
        for column in (
            "section",
            "line_item_category",
            "line_item_name",
            "item_name",
            "description",
            "unit",
            "notes",
            "source_sheet",
        )
    ).lower()


def _line_total(row: dict[str, Any] | pd.Series) -> float | None:
    for column in ("line_total", "extended_cost", "total", "amount"):
        value = to_float(row.get(column))
        if value is not None:
            return value
    return None


def _has_missing_quantity_for_material(row: dict[str, Any] | pd.Series, bucket: str) -> bool:
    if bucket not in IMPORTANT_MATERIAL_BUCKETS:
        return False
    return to_float(row.get("quantity")) is None and to_float(row.get("labor_hours")) is None


def classify_template_line_item(row: dict[str, Any] | pd.Series) -> ClassificationResult:
    text = _row_text(row)
    matches: list[tuple[str, str]] = []
    source_row = int(to_float(row.get("source_row")) or -1)
    labor_context = "labor" in str(row.get("section") or "").lower() or "subcontractor" in str(row.get("section") or "").lower() or "labor" in text
    if source_row in SOURCE_ROW_BUCKETS and "labor" in text:
        bucket = SOURCE_ROW_BUCKETS[source_row]
        return ClassificationResult(bucket, BUCKET_SECTIONS[bucket], 0.9, f"matched template labor source row {source_row}", False)

    for bucket, keywords in BUCKET_KEYWORDS:
        if bucket.startswith("labor_") and not labor_context:
            continue
        matched = [keyword for keyword in keywords if keyword in text]
        if matched:
            matches.append((bucket, matched[0]))

    if "labor" in text and not any(bucket.startswith("labor_") for bucket, _keyword in matches):
        matches.append(("labor_details", "labor"))

    if not matches:
        total = _line_total(row)
        needs_review = total is not None and total != 0
        return ClassificationResult("unknown", "unknown", 0.0, "no keyword or template-row match", needs_review)

    primary_bucket, primary_keyword = matches[0]
    strong_buckets = {bucket for bucket, _keyword in matches[:3]}
    needs_review = len(strong_buckets) > 1 and primary_bucket not in {"fabric", "seams_misc", "caulk_sealant"}
    if _has_missing_quantity_for_material(row, primary_bucket):
        needs_review = True
    confidence = 0.9 if len(strong_buckets) == 1 else 0.65
    if needs_review:
        confidence = min(confidence, 0.7)
    reason = f"matched keyword '{primary_keyword}'"
    if len(strong_buckets) > 1:
        reason += f"; competing buckets: {', '.join(sorted(strong_buckets))}"
    if _has_missing_quantity_for_material(row, primary_bucket):
        reason += "; missing quantity/unit for material bucket"
    return ClassificationResult(primary_bucket, BUCKET_SECTIONS[primary_bucket], confidence, reason, needs_review)


def classify_line_items(line_items: pd.DataFrame) -> pd.DataFrame:
    if line_items.empty:
        return pd.DataFrame()
    rows = []
    for _, row in line_items.iterrows():
        result = classify_template_line_item(row)
        out = row.to_dict()
        out.update(
            {
                "template_bucket": result.template_bucket,
                "template_section": result.template_section,
                "classification_confidence": result.classification_confidence,
                "classification_reason": result.classification_reason,
                "needs_review": result.needs_review,
                "line_total": _line_total(row),
            }
        )
        rows.append(out)
    return pd.DataFrame(rows)


def summarize_classified_by_job(classified: pd.DataFrame, job_sqft: dict[str, float] | None = None) -> dict[str, Any]:
    job_sqft = job_sqft or {}
    if classified.empty:
        return {"job_bucket_summary": pd.DataFrame(), "total_by_section": {}}
    df = classified.copy()
    df["line_total"] = pd.to_numeric(df.get("line_total"), errors="coerce").fillna(0)
    summary = (
        df.groupby(["job_id", "template_section", "template_bucket"], dropna=False, as_index=False)
        .agg(
            total_cost=("line_total", "sum"),
            line_count=("template_bucket", "size"),
            review_count=("needs_review", "sum"),
        )
        .sort_values(["job_id", "template_section", "template_bucket"])
    )
    if job_sqft:
        summary["estimated_sqft"] = summary["job_id"].astype(str).map(job_sqft)
        summary["cost_per_sqft"] = summary.apply(
            lambda row: row["total_cost"] / row["estimated_sqft"] if row.get("estimated_sqft") else None,
            axis=1,
        )
    total_by_section = df.groupby("template_section")["line_total"].sum().to_dict()
    return {"job_bucket_summary": summary, "total_by_section": total_by_section}


def summarize_similar_job_buckets(
    line_items: pd.DataFrame,
    similar_jobs: pd.DataFrame,
    *,
    job_sqft: dict[str, float] | None = None,
) -> dict[str, Any]:
    if line_items.empty or similar_jobs.empty or "job_id" not in line_items.columns or "job_id" not in similar_jobs.columns:
        return {
            "classified_rows": pd.DataFrame(),
            "bucket_summary": pd.DataFrame(),
            "common_items": pd.DataFrame(),
        }
    similar_ids = [str(job_id) for job_id in similar_jobs["job_id"].dropna().astype(str)]
    filtered = line_items[line_items["job_id"].astype(str).isin(similar_ids)].copy()
    if filtered.empty:
        return {
            "classified_rows": pd.DataFrame(),
            "bucket_summary": pd.DataFrame(),
            "common_items": pd.DataFrame(),
        }
    if job_sqft is None:
        job_sqft = {
            str(row.get("job_id")): float(row.get("estimated_sqft"))
            for _, row in similar_jobs.iterrows()
            if to_float(row.get("estimated_sqft"))
        }
    classified = classify_line_items(filtered)
    classified["line_total"] = pd.to_numeric(classified["line_total"], errors="coerce").fillna(0)
    classified["estimated_sqft"] = classified["job_id"].astype(str).map(job_sqft)
    classified["cost_per_sqft"] = classified.apply(
        lambda row: row["line_total"] / row["estimated_sqft"] if row.get("estimated_sqft") else None,
        axis=1,
    )
    by_job_bucket = (
        classified.groupby(["job_id", "template_bucket"], dropna=False, as_index=False)
        .agg(
            bucket_cost=("line_total", "sum"),
            cost_per_sqft=("cost_per_sqft", "sum"),
            needs_review=("needs_review", "max"),
        )
    )
    bucket_summary = (
        by_job_bucket.groupby("template_bucket", dropna=False, as_index=False)
        .agg(
            frequency=("job_id", "nunique"),
            median_cost_per_sqft=("cost_per_sqft", "median"),
            low_cost_per_sqft=("cost_per_sqft", lambda values: values.quantile(0.25)),
            high_cost_per_sqft=("cost_per_sqft", lambda values: values.quantile(0.75)),
            median_total_cost=("bucket_cost", "median"),
            needs_review_count=("needs_review", "sum"),
        )
        .sort_values(["frequency", "median_total_cost"], ascending=[False, False])
    )
    name_col = "line_item_name" if "line_item_name" in classified.columns else "item_name"
    common_items = (
        classified.groupby(["template_bucket", name_col], dropna=False, as_index=False)
        .agg(
            count=(name_col, "size"),
            median_line_total=("line_total", "median"),
            needs_review=("needs_review", "max"),
        )
        .rename(columns={name_col: "raw_item_name"})
        .sort_values(["count", "median_line_total"], ascending=[False, False])
        .head(50)
    )
    return {
        "classified_rows": classified,
        "bucket_summary": bucket_summary,
        "common_items": common_items,
    }
