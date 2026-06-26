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
FALSE_VALUES = {"false", "0", "no", "n", "invalid"}
NON_PHYSICAL_SOURCE_TYPES = {"cost_allowance", "labor_budget", "derived_ratio", "unknown"}
PHYSICAL_SOURCE_TYPES = {"physical_quantity", "physical", "material_quantity"}
PHYSICAL_UNITS_BY_BUCKET = {
    "primer": {"gal", "gallon", "gallons", "pail", "pails", "container", "containers", "drum", "drums"},
    "seam_treatment": {"lf", "linear foot", "linear feet", "ft", "feet", "roll", "rolls", "tube", "tubes"},
    "fastener_treatment": {"ea", "each", "count", "counts", "unit", "units", "piece", "pieces", "pc", "pcs"},
    "caulk_detail": {"case", "cases", "tube", "tubes", "ea", "each", "lf", "linear foot", "linear feet"},
    "coating": {"gal", "gallon", "gallons", "pail", "pails", "drum", "drums"},
}
MAX_QUANTITY_RATIO_BY_BUCKET_UNIT = {
    ("primer", "gal"): 0.02,
    ("primer", "gallon"): 0.02,
    ("primer", "gallons"): 0.02,
    ("primer", "pail"): 0.004,
    ("primer", "pails"): 0.004,
    ("primer", "container"): 0.004,
    ("primer", "containers"): 0.004,
    ("primer", "drum"): 0.001,
    ("primer", "drums"): 0.001,
    ("caulk_detail", "case"): 0.01,
    ("caulk_detail", "cases"): 0.01,
}


def finite_float(value: Any) -> float | None:
    number = to_float(value)
    if number is None or not math.isfinite(number):
        return None
    return number


def median_positive(values: list[float]) -> float | None:
    positives = [value for value in values if value is not None and value > 0 and math.isfinite(value)]
    return float(median(positives)) if positives else None


def percentile_positive(values: list[float], percentile: float) -> float | None:
    positives = sorted(value for value in values if value is not None and value > 0 and math.isfinite(value))
    if not positives:
        return None
    if len(positives) == 1:
        return float(positives[0])
    position = (len(positives) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(positives[int(position)])
    return float(positives[lower] + (positives[upper] - positives[lower]) * (position - lower))


def row_text(row: dict[str, Any] | pd.Series) -> str:
    return " ".join(str(row.get(column) or "") for column in TEXT_COLUMNS).lower()


def normalize_unit(value: Any) -> str:
    unit = first_nonblank(value).strip().lower()
    aliases = {
        "linear feet": "lf",
        "linear foot": "lf",
        "feet": "ft",
        "foot": "ft",
        "each": "ea",
        "count": "ea",
        "counts": "ea",
        "piece": "ea",
        "pieces": "ea",
        "pcs": "ea",
        "gals": "gal",
        "gallon": "gal",
        "gallons": "gal",
    }
    return aliases.get(unit, unit)


def physical_quantity_valid_flag(row: dict[str, Any] | pd.Series) -> bool:
    if "physical_quantity_valid" not in row:
        return True
    value = row.get("physical_quantity_valid")
    if value is None or str(value).strip() == "":
        return True
    return str(value).strip().lower() not in FALSE_VALUES


def source_type_allows_physical_quantity(row: dict[str, Any] | pd.Series) -> bool:
    source_type = first_nonblank(row.get("source_type")).strip().lower()
    if not source_type:
        return True
    if source_type in NON_PHYSICAL_SOURCE_TYPES:
        return False
    return source_type in PHYSICAL_SOURCE_TYPES


def sane_quantity_ratio(bucket: str, unit: str, ratio: float) -> bool:
    if ratio <= 0 or not math.isfinite(ratio):
        return False
    normalized = normalize_unit(unit)
    if normalized == "sqft":
        return False
    max_ratio = MAX_QUANTITY_RATIO_BY_BUCKET_UNIT.get((bucket, normalized))
    if max_ratio is not None:
        return ratio <= max_ratio
    if bucket == "seam_treatment" and normalized in {"lf", "ft"}:
        return ratio <= 0.5
    if bucket == "fastener_treatment" and normalized == "ea":
        return ratio <= 0.2
    if bucket == "caulk_detail" and normalized in {"tube", "tubes", "ea", "lf", "ft"}:
        return ratio <= 0.1
    if bucket == "coating" and normalized == "gal":
        return ratio <= 0.05
    return True


def row_has_valid_physical_quantity(row: dict[str, Any] | pd.Series, bucket: str, quantity: float, sqft: float) -> tuple[bool, str]:
    unit = normalize_unit(row.get("unit"))
    if not source_type_allows_physical_quantity(row):
        return False, "source_type is not physical_quantity"
    if not physical_quantity_valid_flag(row):
        return False, "physical_quantity_valid is false"
    if unit not in {normalize_unit(value) for value in PHYSICAL_UNITS_BY_BUCKET.get(bucket, set())}:
        return False, f"unit {unit or '<blank>'} is not a valid physical unit for {bucket}"
    ratio = quantity / sqft
    if not sane_quantity_ratio(bucket, unit, ratio):
        return False, f"quantity ratio {ratio:g} {unit}/sqft is unrealistic for {bucket}"
    if bucket == "primer" and unit in {"pail", "pails", "container", "containers"}:
        if quantity > sqft / 100:
            return False, "primer pail quantity exceeds sqft / 100"
        if sqft / quantity < 100:
            return False, "primer pail coverage is less than 100 sqft per pail"
    return True, ""


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
    physical_candidate_rows = 0
    cost_fallback_rows = 0
    rejected_quantity_rows = 0
    rejection_reasons: list[str] = []
    for _, row in rows.iterrows():
        sqft = row_sqft(row, sqft_map)
        quantity = finite_float(row.get("quantity")) or finite_float(row.get("estimated_units"))
        cost = finite_float(row.get("estimated_cost"))
        quantity_used = False
        if quantity and sqft and sqft > 0:
            physical_candidate_rows += 1
            is_valid, reason = row_has_valid_physical_quantity(row, bucket, quantity, sqft)
            if is_valid:
                quantity_ratios.append(quantity / sqft)
                usable_evidence += 1
                quantity_used = True
                unit = first_nonblank(row.get("unit"))
                if unit:
                    units.append(unit)
            else:
                rejected_quantity_rows += 1
                if len(rejection_reasons) < 5:
                    rejection_reasons.append(reason)
        if cost and sqft and sqft > 0:
            cost_ratios.append(cost / sqft)
            if not quantity_used:
                usable_evidence += 1
                cost_fallback_rows += 1
        unit_price = finite_float(row.get("unit_price"))
        if unit_price:
            unit_prices.append(unit_price)
    current_price = select_current_price(data.pricing_catalog if not data.pricing_catalog.empty else data.pricing, bucket)
    unit = units[0] if units else first_nonblank(current_price.get("unit_of_measure") if current_price else "")
    return {
        "bucket": bucket,
        "evidence_count": usable_evidence,
        "candidate_physical_rows_count": physical_candidate_rows,
        "historical_physical_quantity_rows_considered": physical_candidate_rows,
        "historical_cost_fallback_rows_considered": cost_fallback_rows,
        "valid_quantity_ratio_count": len(quantity_ratios),
        "rejected_physical_rows_count": rejected_quantity_rows,
        "median_quantity_per_sqft": median_positive(quantity_ratios),
        "p25_quantity_per_sqft": percentile_positive(quantity_ratios, 0.25),
        "p75_quantity_per_sqft": percentile_positive(quantity_ratios, 0.75),
        "median_cost_per_sqft": median_positive(cost_ratios),
        "p25_cost_per_sqft": percentile_positive(cost_ratios, 0.25),
        "p75_cost_per_sqft": percentile_positive(cost_ratios, 0.75),
        "median_unit_price": median_positive(unit_prices),
        "matching_historical_rows": int(len(rows)),
        "rejected_quantity_ratio_count": rejected_quantity_rows,
        "quantity_ratio_rejection_reasons": sorted(set(rejection_reasons)),
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
