from __future__ import annotations

import math
from statistics import median
from typing import Any

import pandas as pd

from .rules import first_nonblank, to_float
from .schemas import EstimatorData


BUCKETS = {
    "primer": {
        "keywords": ("primer", "prime", "epoxy primer", "rust primer"),
        "price_keywords": ("primer",),
        "preferred_price_columns": ("price_per_gallon", "unit_price", "price_per_sqft", "price_per_unit"),
    },
    "seam_treatment": {
        "keywords": ("seam", "seam sealer", "seam tape", "butter grade", "fabric", "detail tape"),
        "price_keywords": ("seam", "seam sealer", "tape", "fabric", "butter grade", "sealant"),
        "preferred_price_columns": ("price_per_lf", "price_per_unit", "unit_price"),
    },
    "fastener_treatment": {
        "keywords": ("fastener", "screw", "screws", "washer", "rusted fasteners"),
        "price_keywords": ("fastener", "screw", "washer", "dab", "rusted fastener"),
        "preferred_price_columns": ("price_per_unit", "unit_price"),
    },
    "caulk_detail": {
        "keywords": ("caulk", "sealant", "detail", "penetration", "curb"),
        "price_keywords": ("caulk", "sealant", "detail"),
        "preferred_price_columns": ("price_per_unit", "unit_price", "price_per_lf"),
    },
    "coating": {
        "keywords": ("coating", "silicone", "acrylic", "urethane", "roof coating"),
        "price_keywords": ("coating",),
        "preferred_price_columns": ("price_per_gallon", "unit_price"),
    },
}

TEXT_COLUMNS = ("selected_item_name", "item_name", "line_item_name", "row_label", "template_bucket", "category", "notes", "description")


def finite_float(value: Any) -> float | None:
    number = to_float(value)
    if number is None or not math.isfinite(number):
        return None
    return number


def median_positive(values: list[float]) -> float | None:
    positives = [value for value in values if value is not None and value > 0 and math.isfinite(value)]
    return float(median(positives)) if positives else None


def row_text(row: dict[str, Any] | pd.Series) -> str:
    return " ".join(str(row.get(column) or "") for column in TEXT_COLUMNS).lower()


def is_labor_row(row: dict[str, Any] | pd.Series) -> bool:
    text = row_text(row)
    kind = str(row.get("line_item_kind") or "").strip().lower()
    bucket = str(row.get("template_bucket") or "").strip().lower()
    return kind == "labor" or bucket.startswith("labor_") or " labor" in text or text.startswith("labor_")


def estimate_sqft_by_job(data: EstimatorData) -> dict[str, float]:
    out: dict[str, float] = {}
    for frame in (data.jobs, data.estimates):
        if frame.empty or "job_id" not in frame.columns:
            continue
        for _, row in frame.iterrows():
            sqft = (
                finite_float(row.get("estimated_sqft"))
                or finite_float(row.get("surface_area_sqft"))
                or finite_float(row.get("net_area_sqft"))
                or finite_float(row.get("gross_area_sqft"))
            )
            if sqft and sqft > 0:
                out[str(row.get("job_id"))] = sqft
    return out


def row_sqft(row: dict[str, Any] | pd.Series, sqft_map: dict[str, float]) -> float | None:
    direct = finite_float(row.get("historical_sqft")) or finite_float(row.get("estimated_sqft")) or finite_float(row.get("surface_area_sqft"))
    if direct and direct > 0:
        return direct
    job_id = row.get("job_id")
    if job_id is not None:
        return sqft_map.get(str(job_id))
    return None


def current_pricing_rows(pricing: pd.DataFrame) -> pd.DataFrame:
    if pricing.empty:
        return pricing
    rows = pricing.copy()
    if "is_current" in rows.columns:
        rows = rows[rows["is_current"].astype(str).str.lower().isin({"true", "1", "yes", "y"}) | (rows["is_current"] == True)]  # noqa: E712
    if "status" in rows.columns:
        rows = rows[rows["status"].fillna("").astype(str).str.lower().isin({"", "active"})]
    if "needs_review" in rows.columns:
        rows = rows[~rows["needs_review"].astype(str).str.lower().isin({"true", "1", "yes", "y"})]
    return rows


def pricing_text(row: dict[str, Any] | pd.Series) -> str:
    columns = ("product_name", "description", "category", "price_basis", "unit_of_measure")
    return " ".join(str(row.get(column) or "") for column in columns).lower()


def select_current_price(pricing: pd.DataFrame, bucket: str) -> dict[str, Any] | None:
    config = BUCKETS[bucket]
    rows = current_pricing_rows(pricing)
    if rows.empty:
        return None
    candidates: list[tuple[int, dict[str, Any]]] = []
    for _, row in rows.iterrows():
        text = pricing_text(row)
        matched_keywords = [keyword for keyword in config["price_keywords"] if keyword in text]
        if not matched_keywords:
            continue
        row_dict = row.to_dict()
        for index, column in enumerate(config["preferred_price_columns"]):
            price = finite_float(row_dict.get(column))
            if price and price > 0:
                score = 100 + len(matched_keywords) * 10 - index
                if any(keyword == text for keyword in config["price_keywords"]):
                    score += 50
                row_dict["matched_price"] = price
                row_dict["matched_price_column"] = column
                candidates.append((score, row_dict))
                break
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], str(item[1].get("product_name") or ""), float(item[1].get("matched_price") or 0)))
    return candidates[0][1]


def matching_rows(template_rows: pd.DataFrame, bucket: str) -> pd.DataFrame:
    if template_rows.empty:
        return pd.DataFrame()
    keywords = BUCKETS[bucket]["keywords"]
    mask = []
    for _, row in template_rows.iterrows():
        text = row_text(row)
        mask.append((not is_labor_row(row)) and any(keyword in text for keyword in keywords))
    return template_rows[pd.Series(mask, index=template_rows.index)].copy()


def build_bucket_calibration(data: EstimatorData, scope: dict[str, Any], bucket: str) -> dict[str, Any]:
    rows = matching_rows(data.template_rows, bucket)
    sqft_map = estimate_sqft_by_job(data)
    quantity_ratios: list[float] = []
    cost_ratios: list[float] = []
    unit_prices: list[float] = []
    units: list[str] = []
    usable_evidence = 0
    for _, row in rows.iterrows():
        sqft = row_sqft(row, sqft_map)
        quantity = finite_float(row.get("quantity")) or finite_float(row.get("estimated_units"))
        cost = finite_float(row.get("estimated_cost"))
        if quantity and sqft and sqft > 0:
            quantity_ratios.append(quantity / sqft)
            usable_evidence += 1
            unit = first_nonblank(row.get("unit"))
            if unit:
                units.append(unit)
        if cost and sqft and sqft > 0:
            cost_ratios.append(cost / sqft)
            if not quantity:
                usable_evidence += 1
        unit_price = finite_float(row.get("unit_price"))
        if unit_price:
            unit_prices.append(unit_price)
    current_price = select_current_price(data.pricing_catalog if not data.pricing_catalog.empty else data.pricing, bucket)
    unit = units[0] if units else first_nonblank(current_price.get("unit_of_measure") if current_price else "")
    return {
        "bucket": bucket,
        "evidence_count": usable_evidence,
        "median_quantity_per_sqft": median_positive(quantity_ratios),
        "median_cost_per_sqft": median_positive(cost_ratios),
        "median_unit_price": median_positive(unit_prices),
        "matching_historical_rows": int(len(rows)),
        "selected_current_price_item": current_price,
        "selected_current_unit_price": finite_float(current_price.get("matched_price")) if current_price else None,
        "selected_current_price_column": current_price.get("matched_price_column") if current_price else None,
        "unit": unit,
        "notes": (
            f"Matched {len(rows)} historical {bucket.replace('_', ' ')} rows; "
            f"{usable_evidence} had usable quantity or cost ratios."
        ),
    }


def build_material_calibration(data: EstimatorData, scope: dict[str, Any]) -> dict[str, Any]:
    return {bucket: build_bucket_calibration(data, scope, bucket) for bucket in BUCKETS}
