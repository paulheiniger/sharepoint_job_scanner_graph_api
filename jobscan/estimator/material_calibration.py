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
        "required_product_keywords": ("primer", "prime"),
        "excluded_product_keywords": ("granule", "granules"),
        "preferred_price_columns": ("unit_price", "price_per_unit", "price_per_gallon", "price_per_sqft"),
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
INSULATION_SOURCE_SIGNALS = (
    "insulation",
    "spray foam",
    "open-cell",
    "open cell",
    "closed-cell",
    "closed cell",
    "dc315",
    "thermal barrier",
    "wall",
    "crawlspace",
    "crawl space",
    "attic",
)
ROOFING_SOURCE_SIGNALS = (
    "roof",
    "roofing",
    "coating",
    "silicone",
    "acrylic",
    "metal roof",
    "tpo",
    "epdm",
    "modified bitumen",
)
PHYSICAL_UNITS_BY_BUCKET = {
    "primer": {"gal", "gallon", "gallons", "pail", "pails", "container", "containers", "drum", "drums"},
    # Seam treatment ratios must be linear footage. Roll/tube counts are useful
    # pricing clues, but they are not interchangeable with LF production ratios.
    "seam_treatment": {"lf", "linear foot", "linear feet", "ft", "feet"},
    "fastener_treatment": {"ea", "each", "count", "counts", "unit", "units", "piece", "pieces", "pc", "pcs"},
    "caulk_detail": {"case", "cases", "tube", "tubes", "ea", "each", "unit", "units", "lf", "linear foot", "linear feet"},
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

DEFAULT_UNIT_BY_BUCKET = {
    "primer": "pail",
    "seam_treatment": "lf",
    "fastener_treatment": "ea",
    "caulk_detail": "unit",
    "coating": "gal",
}

MAX_ESTIMATED_UNITS_RATIO_BY_BUCKET = {
    "primer": 0.004,
    "seam_treatment": 0.5,
    "fastener_treatment": 0.2,
    "caulk_detail": 0.1,
    "coating": 0.05,
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


def normalize_context(value: Any) -> str:
    return first_nonblank(value).strip().lower().replace("_", " ").replace("-", " ")


def scope_template_type(scope: dict[str, Any]) -> str:
    text = " ".join(str(value or "") for value in scope.values()).lower()
    if any(term in text for term in ("insulation", "spray foam", "closed cell", "open cell", "closed-cell", "open-cell", "dc315", "thermal barrier")):
        return "insulation"
    if any(term in text for term in ROOFING_SOURCE_SIGNALS):
        return "roofing"
    return normalize_context(scope.get("template_type"))


def evidence_template_type(row: dict[str, Any] | pd.Series) -> str:
    value = first_nonblank(row.get("template_type"), row.get("job_template_type"), row.get("template_name"))
    normalized = normalize_context(value)
    if normalized in {"roof", "roofing", "roof coating"}:
        return "roofing"
    if normalized in {"insulation", "foam", "spray foam"}:
        return "insulation"
    if normalized in {"unknown", "none", "null"}:
        return ""
    return normalized


def evidence_source_text(row: dict[str, Any] | pd.Series) -> str:
    return " ".join(
        str(row.get(column) or "").lower()
        for column in (
            "source_file",
            "folder_path",
            "relative_path",
            "job_name",
            "customer",
            "estimate_file",
            "document_name",
            "division",
            "job_division",
            "job_project_type",
            "project_type",
        )
    )


def evidence_has_insulation_source_signal(row: dict[str, Any] | pd.Series) -> bool:
    text = evidence_source_text(row)
    return any(term in text for term in INSULATION_SOURCE_SIGNALS)


def evidence_has_strong_roofing_signal(row: dict[str, Any] | pd.Series) -> bool:
    text = f"{evidence_source_text(row)} {row_text(row)}"
    return any(term in text for term in ROOFING_SOURCE_SIGNALS)


def evidence_allowed_for_scope(
    row: dict[str, Any] | pd.Series,
    scope: dict[str, Any],
    *,
    allow_unknown_with_roofing_signal: bool,
) -> tuple[bool, str]:
    scope_type = scope_template_type(scope)
    evidence_type = evidence_template_type(row)
    if scope_type == "roofing":
        if evidence_type == "insulation":
            return False, "Template type mismatch: roofing scope cannot use insulation evidence."
        if evidence_has_insulation_source_signal(row):
            return False, "Source path/name mismatch: roofing scope cannot use insulation source evidence."
        if evidence_type and evidence_type != "roofing":
            return False, f"Template type mismatch: roofing scope cannot use {evidence_type} evidence."
        if not evidence_type and not (allow_unknown_with_roofing_signal and evidence_has_strong_roofing_signal(row)):
            return False, "Unknown template type without strong roofing signal."
    if scope_type == "insulation" and evidence_type == "roofing":
        return False, "Template type mismatch: insulation scope cannot use roofing evidence."
    return True, ""


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


def current_price_unit(price: dict[str, Any] | None, bucket: str) -> str:
    if not price:
        return DEFAULT_UNIT_BY_BUCKET.get(bucket, "unit")
    explicit = normalize_unit(first_nonblank(price.get("unit_of_measure"), price.get("unit"), price.get("price_basis")))
    if explicit in {"unit cost", "extracted line price"}:
        explicit = ""
    if explicit:
        return explicit
    column = first_nonblank(price.get("matched_price_column")).lower()
    if column == "price_per_gallon":
        return "gal"
    if column == "price_per_lf":
        return "lf"
    if column == "price_per_sqft":
        return "sqft"
    if column == "price_per_unit":
        return "ea"
    text = pricing_text(price)
    if any(term in text for term in ("pail", "5 gal", "2 gal", "bucket")):
        return "pail"
    if any(term in text for term in ("drum", "54 gal", "55 gal")):
        return "drum"
    if any(term in text for term in ("gallon", " gal")):
        return "gal"
    if "tube" in text:
        return "tube"
    if "case" in text:
        return "case"
    if any(term in text for term in ("linear", " lf")):
        return "lf"
    return DEFAULT_UNIT_BY_BUCKET.get(bucket, "unit")


def row_kind_is_material(row: dict[str, Any] | pd.Series) -> bool:
    return str(row.get("line_item_kind") or "").strip().lower() in {"", "material", "materials"}


def quantity_looks_like_scope_area(quantity: float | None, sqft: float | None, unit: str) -> bool:
    if quantity is None or quantity <= 0:
        return False
    if normalize_unit(unit) in {"sqft", "sf", "square feet", "square foot"}:
        return True
    if sqft and sqft > 0 and 0.8 <= quantity / sqft <= 1.25:
        return True
    return quantity >= 500


def choose_material_quantity(
    row: dict[str, Any] | pd.Series,
    *,
    bucket: str,
    sqft: float | None,
    current_price: dict[str, Any] | None,
    scope_type: str,
) -> tuple[float | None, float | None, str, str, dict[str, Any]]:
    raw_quantity = finite_float(row.get("quantity"))
    estimated_units = finite_float(row.get("estimated_units"))
    raw_unit = normalize_unit(row.get("unit"))
    area_unit = raw_unit in {"sqft", "sf", "square feet", "square foot"}
    inferred_unit = raw_unit or current_price_unit(current_price, bucket)
    area_used = sqft
    chosen_field = ""
    quantity = None
    if (
        scope_type == "roofing"
        and row_kind_is_material(row)
        and estimated_units
        and estimated_units > 0
    ):
        if area_used is None and raw_quantity and quantity_looks_like_scope_area(raw_quantity, None, raw_unit):
            area_used = raw_quantity
        if area_used and area_used > 0:
            ratio = estimated_units / area_used
            max_ratio = MAX_ESTIMATED_UNITS_RATIO_BY_BUCKET.get(bucket)
            if max_ratio is None or ratio <= max_ratio:
                chosen_field = "estimated_units"
                quantity = estimated_units
                if area_unit:
                    inferred_unit = current_price_unit(current_price, bucket)
            else:
                chosen_field = "estimated_units_rejected"
        if not chosen_field and raw_quantity and raw_quantity > 0 and not quantity_looks_like_scope_area(raw_quantity, area_used, raw_unit):
            chosen_field = "quantity"
            quantity = raw_quantity
    elif raw_quantity and raw_quantity > 0:
        chosen_field = "quantity"
        quantity = raw_quantity
    elif estimated_units and estimated_units > 0:
        chosen_field = "estimated_units"
        quantity = estimated_units
    diagnostics = {
        "raw_quantity": raw_quantity,
        "estimated_units": estimated_units,
        "area_sqft_used": area_used,
        "chosen_material_quantity_field": chosen_field or "none",
        "chosen_unit": inferred_unit,
        "implied_quantity_per_sqft": quantity / area_used if quantity and area_used else None,
        "rejection_reason": "",
    }
    return quantity, area_used, inferred_unit, chosen_field, diagnostics


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


def row_has_valid_physical_quantity(row: dict[str, Any] | pd.Series, bucket: str, quantity: float, sqft: float, unit_override: str | None = None) -> tuple[bool, str]:
    unit = normalize_unit(unit_override) or normalize_unit(row.get("unit"))
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


def context_by_job(data: EstimatorData) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for frame in (data.jobs, data.estimates):
        if frame.empty or "job_id" not in frame.columns:
            continue
        for _, row in frame.iterrows():
            job_id = row.get("job_id")
            if job_id is None:
                continue
            target = out.setdefault(str(job_id), {})
            for column in (
                "template_type",
                "project_type",
                "job_type",
                "division",
                "job_name",
                "customer",
                "estimate_file",
                "substrate",
                "coating_type",
            ):
                if column in row and first_nonblank(row.get(column)) and column not in target:
                    target[column] = row.get(column)
    return out


def enriched_row(row: dict[str, Any] | pd.Series, job_context: dict[str, dict[str, Any]]) -> dict[str, Any]:
    row_dict = row.to_dict() if isinstance(row, pd.Series) else dict(row)
    job_id = row_dict.get("job_id")
    if job_id is not None:
        for key, value in job_context.get(str(job_id), {}).items():
            row_dict.setdefault(f"job_{key}", value)
            row_dict.setdefault(key, value)
    return row_dict


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


def pricing_product_text(row: dict[str, Any] | pd.Series) -> str:
    columns = ("product_name", "description")
    return " ".join(str(row.get(column) or "") for column in columns).lower()


def select_current_price(pricing: pd.DataFrame, bucket: str) -> dict[str, Any] | None:
    config = BUCKETS[bucket]
    rows = current_pricing_rows(pricing)
    if rows.empty:
        return None
    candidates: list[tuple[int, dict[str, Any]]] = []
    for _, row in rows.iterrows():
        text = pricing_text(row)
        product_text = pricing_product_text(row)
        matched_keywords = [keyword for keyword in config["price_keywords"] if keyword in text]
        if not matched_keywords:
            continue
        required_product_keywords = config.get("required_product_keywords", ())
        if required_product_keywords and not any(keyword in product_text for keyword in required_product_keywords):
            continue
        excluded_product_keywords = config.get("excluded_product_keywords", ())
        if excluded_product_keywords and any(keyword in product_text for keyword in excluded_product_keywords):
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
    job_context = context_by_job(data)
    scope_type = scope_template_type(scope)
    current_price = select_current_price(data.pricing_catalog if not data.pricing_catalog.empty else data.pricing, bucket)
    has_explicit_roofing_rows = False
    if scope_type == "roofing":
        for _, row in rows.iterrows():
            row_dict = enriched_row(row, job_context)
            if evidence_template_type(row_dict) == "roofing":
                has_explicit_roofing_rows = True
                break
    quantity_ratios: list[float] = []
    cost_ratios: list[float] = []
    unit_prices: list[float] = []
    units: list[str] = []
    usable_evidence = 0
    physical_candidate_rows = 0
    cost_fallback_rows = 0
    rejected_quantity_rows = 0
    rejected_template_rows = 0
    rejection_reasons: list[str] = []
    template_rejection_reasons: list[str] = []
    quantity_diagnostics: list[dict[str, Any]] = []
    rejected_by_reason: dict[str, int] = {}
    chosen_quantity_fields: dict[str, int] = {}
    for _, row in rows.iterrows():
        row_dict = enriched_row(row, job_context)
        allowed, template_reason = evidence_allowed_for_scope(
            row_dict,
            scope,
            allow_unknown_with_roofing_signal=not has_explicit_roofing_rows,
        )
        if not allowed:
            rejected_template_rows += 1
            if len(template_rejection_reasons) < 5:
                template_rejection_reasons.append(template_reason)
            continue
        sqft = row_sqft(row_dict, sqft_map)
        quantity, sqft, quantity_unit, chosen_field, quantity_diag = choose_material_quantity(
            row_dict,
            bucket=bucket,
            sqft=sqft,
            current_price=current_price,
            scope_type=scope_type,
        )
        cost = finite_float(row_dict.get("estimated_cost"))
        quantity_used = False
        if quantity and sqft and sqft > 0:
            physical_candidate_rows += 1
            is_valid, reason = row_has_valid_physical_quantity(row_dict, bucket, quantity, sqft, quantity_unit)
            quantity_diag["rejection_reason"] = reason
            if is_valid:
                quantity_ratios.append(quantity / sqft)
                usable_evidence += 1
                quantity_used = True
                chosen_quantity_fields[chosen_field or "unknown"] = chosen_quantity_fields.get(chosen_field or "unknown", 0) + 1
                unit = first_nonblank(quantity_unit, row_dict.get("unit"))
                if unit:
                    units.append(unit)
            else:
                rejected_quantity_rows += 1
                rejected_by_reason[reason] = rejected_by_reason.get(reason, 0) + 1
                if len(rejection_reasons) < 5:
                    rejection_reasons.append(reason)
        elif chosen_field.endswith("_rejected"):
            reason = (
                f"estimated_units ratio {quantity_diag.get('implied_quantity_per_sqft')} "
                f"exceeds sane bound for {bucket}"
            )
            quantity_diag["rejection_reason"] = reason
            rejected_quantity_rows += 1
            rejected_by_reason[reason] = rejected_by_reason.get(reason, 0) + 1
            if len(rejection_reasons) < 5:
                rejection_reasons.append(reason)
        if cost and sqft and sqft > 0:
            cost_ratios.append(cost / sqft)
            if not quantity_used:
                usable_evidence += 1
                cost_fallback_rows += 1
        unit_price = finite_float(row_dict.get("unit_price"))
        if unit_price:
            unit_prices.append(unit_price)
        if len(quantity_diagnostics) < 25:
            quantity_diag["source_file"] = first_nonblank(row_dict.get("source_file"), row_dict.get("estimate_file"))
            quantity_diag["job_id"] = row_dict.get("job_id")
            quantity_diag["template_bucket"] = row_dict.get("template_bucket")
            quantity_diag["row_label"] = first_nonblank(row_dict.get("row_label"), row_dict.get("selected_item_name"))
            quantity_diagnostics.append(quantity_diag)
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
        "rejected_template_type_rows_count": rejected_template_rows,
        "rejected_quantity_ratio_count": rejected_quantity_rows,
        "quantity_ratio_rejection_reasons": sorted(set(rejection_reasons)),
        "quantity_ratio_rejections_by_reason": rejected_by_reason,
        "quantity_evidence_diagnostics": quantity_diagnostics,
        "chosen_material_quantity_fields": chosen_quantity_fields,
        "selected_material_calibration_field": max(chosen_quantity_fields, key=chosen_quantity_fields.get) if chosen_quantity_fields else "cost_ratio_fallback" if cost_fallback_rows else "none",
        "template_type_rejection_reasons": sorted(set(template_rejection_reasons)),
        "scope_template_type": scope_type,
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
