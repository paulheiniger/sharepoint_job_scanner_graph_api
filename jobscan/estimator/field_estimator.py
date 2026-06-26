from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

from .calibration import calibrate_from_history
from .data_loader import load_estimator_data
from .decision_tree import evaluate_decision_tree
from .field_notes import parse_field_notes, parsed_to_scope
from .line_items import summarize_similar_job_buckets
from .material_calibration import build_material_calibration
from .materials import coating_gallons, find_current_price, historical_unit_cost
from .rules import first_nonblank, to_float
from .schemas import EstimateRecommendation, EstimatorAssumptions, EstimatorData, FieldNotesInput
from .similarity import find_similar_jobs
from .travel import build_travel_plan


@dataclass(frozen=True)
class WorkPackageDecision:
    package_name: str
    applies: bool | str
    confidence: float
    reason: str
    basis: str
    quantity_scope: str
    review_required: bool


def is_finite_number(value: Any) -> bool:
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except Exception:
        pass
    number = to_float(value)
    if number is None:
        return False
    return math.isfinite(number)


def is_missing_number(value: Any) -> bool:
    return not is_finite_number(value)


def is_missing_or_bad_number(value: Any) -> bool:
    return not is_finite_number(value)


def safe_float(value: Any, default: float = 0.0) -> float:
    return to_float_or_default(value, default)


def safe_int(value: Any, default: int = 0) -> int:
    return to_int_or_default(value, default)


def sane_crew_size(value: Any, default: int = 4, *, max_size: int = 12) -> int:
    size = to_int_or_default(value, default)
    if size <= 0 or size > max_size:
        return default
    return size


def to_int_or_default(value: Any, default: int) -> int:
    if not is_finite_number(value):
        return default
    number = to_float(value)
    return int(number) if number is not None else default


def to_float_or_default(value: Any, default: float) -> float:
    if not is_finite_number(value):
        return default
    number = to_float(value)
    return float(number) if number is not None else default


def optional_positive_float(value: Any) -> float | None:
    if not is_finite_number(value):
        return None
    number = to_float(value)
    if number is None:
        return None
    return number if number > 0 else None


def optional_positive_int(value: Any) -> int | None:
    number = optional_positive_float(value)
    return int(number) if number is not None else None


def warranty_wet_mils(warranty_target: Any, coating_type: str) -> float:
    target = to_float(warranty_target)
    if target and target >= 20:
        return 30.0
    if target and target >= 15:
        return 25.0
    if target and target >= 10:
        return 20.0
    return 24.0 if "silicone" in coating_type.lower() else 30.0 if "acrylic" in coating_type.lower() else 24.0


def template_rows_with_job_sqft(data: EstimatorData) -> pd.DataFrame:
    if data.template_rows.empty:
        return pd.DataFrame()
    rows = data.template_rows.copy()
    sqft_by_job: dict[str, float] = {}
    for frame in (data.jobs, data.estimates):
        if frame.empty or "job_id" not in frame.columns:
            continue
        for _, row in frame.iterrows():
            sqft = to_float(row.get("estimated_sqft")) or to_float(row.get("surface_area_sqft"))
            if sqft:
                sqft_by_job[str(row.get("job_id"))] = sqft
    if "job_id" in rows.columns:
        rows["historical_sqft"] = rows["job_id"].astype(str).map(sqft_by_job)
    return rows


def historical_template_calibration(data: EstimatorData, similar_jobs: pd.DataFrame) -> dict[str, Any]:
    template_rows = template_rows_with_job_sqft(data)
    if template_rows.empty:
        return {
            "source": "estimate_line_item_classifications" if not data.classified_line_items.empty else "none",
            "template_row_count": 0,
            "labor_by_bucket": [],
            "material_by_bucket": [],
            "median_labor_cost_per_sqft": None,
            "median_material_cost_per_sqft": None,
            "worksheet_price_examples": [],
        }
    similar_ids = set(similar_jobs.get("job_id", pd.Series(dtype=str)).dropna().astype(str))
    if similar_ids and "job_id" in template_rows.columns:
        relevant = template_rows[template_rows["job_id"].astype(str).isin(similar_ids)].copy()
        if relevant.empty:
            relevant = template_rows.copy()
    else:
        relevant = template_rows.copy()
    for column in ("estimated_cost", "total_hours", "days", "crew_size", "historical_sqft"):
        if column in relevant.columns:
            relevant[column] = pd.to_numeric(relevant[column], errors="coerce")
    labor_rows = relevant[relevant.get("line_item_kind", pd.Series(dtype=str)).astype(str).eq("labor")].copy()
    material_rows = relevant[relevant.get("line_item_kind", pd.Series(dtype=str)).astype(str).isin(["material", "equipment", "travel"])].copy()
    totals = relevant[relevant.get("template_bucket", pd.Series(dtype=str)).astype(str).eq("worksheet_price")].copy()
    if not labor_rows.empty:
        labor_rows["cost_per_sqft"] = labor_rows["estimated_cost"] / labor_rows["historical_sqft"]
    if not material_rows.empty:
        material_rows["cost_per_sqft"] = material_rows["estimated_cost"] / material_rows["historical_sqft"]
    labor_summary = (
        labor_rows.groupby("template_bucket", dropna=False, as_index=False)
        .agg(
            evidence_count=("template_bucket", "size"),
            median_days=("days", "median"),
            median_crew_size=("crew_size", "median"),
            median_total_hours=("total_hours", "median"),
            median_estimated_cost=("estimated_cost", "median"),
        )
        .to_dict(orient="records")
        if not labor_rows.empty
        else []
    )
    material_summary = (
        material_rows.groupby(["template_bucket", "line_item_kind"], dropna=False, as_index=False)
        .agg(evidence_count=("template_bucket", "size"), median_estimated_cost=("estimated_cost", "median"))
        .to_dict(orient="records")
        if not material_rows.empty
        else []
    )
    return {
        "source": "estimate_template_rows",
        "template_row_count": int(len(relevant)),
        "labor_by_bucket": labor_summary,
        "material_by_bucket": material_summary,
        "median_labor_cost_per_sqft": _median_positive(labor_rows.get("cost_per_sqft", pd.Series(dtype=float))),
        "median_material_cost_per_sqft": _median_positive(material_rows.get("cost_per_sqft", pd.Series(dtype=float))),
        "worksheet_price_examples": totals[["document_id", "job_id", "source_file", "estimated_cost"]].dropna(how="all").head(8).to_dict(orient="records") if not totals.empty else [],
    }


ROOF_COATING_LABOR_BUCKETS = {
    "labor_prep",
    "labor_prime",
    "labor_seam_sealer",
    "labor_base",
    "labor_top_coat",
    "labor_caulk",
    "labor_details",
    "labor_cleanup",
    "labor_loading",
}

OPTIONAL_LABOR_BUCKET_TRIGGERS = {
    "infrared_scan": ("ir scan", "infrared", "moisture scan", "thermal scan"),
    "labor_top_coat_granules": ("granules", "granule", "broadcast"),
    "labor_misc": ("misc", "miscellaneous"),
}

REPAIR_LABOR_BUCKETS = {"tear_off", "replacement", "substrate_repair", "roof_repair"}
REPAIR_TRIGGERS = (
    "tear off",
    "tear-off",
    "tearoff",
    "replacement",
    "replace roof",
    "wet insulation",
    "failed substrate",
    "saturated",
    "rotten",
    "major repair",
)


def _text_has_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in text for phrase in phrases)


def selected_labor_buckets(scope: dict[str, Any], decision: dict[str, Any]) -> set[str] | None:
    """Return calibrated labor buckets that belong to the parsed project scope.

    None means the scope is not specific enough to filter historical rows safely.
    """
    notes = first_nonblank(scope.get("notes")).lower()
    project_type = first_nonblank(scope.get("project_type")).lower()
    substrate = first_nonblank(scope.get("substrate")).lower()
    coating_type = first_nonblank(scope.get("coating_type")).lower()
    coating_required = bool(scope.get("coating_required") or coating_type)
    foam_required = bool(scope.get("foam_required") or scope.get("foam_thickness_inches"))
    work_packages = ensure_work_package_decisions(scope, decision)

    is_roof_coating = coating_required and ("roof" in project_type or "roof" in notes or substrate in {"metal", "tpo", "epdm"})
    if is_roof_coating:
        buckets = set(ROOF_COATING_LABOR_BUCKETS)
        primer_decision = work_packages.get("primer")
        if not _decision_applies(primer_decision, include_review=True):
            buckets.discard("labor_prime")
        if not _decision_applies(work_packages.get("prep_powerwash"), include_review=True):
            buckets.discard("labor_prep")
        if not _decision_applies(work_packages.get("seam_treatment"), include_review=True):
            buckets.discard("labor_seam_sealer")
        if not _decision_applies(work_packages.get("caulk_detail"), include_review=True):
            buckets.discard("labor_caulk")
            buckets.discard("labor_details")
        for bucket, triggers in OPTIONAL_LABOR_BUCKET_TRIGGERS.items():
            if _text_has_any(notes, triggers):
                buckets.add(bucket)
        if _text_has_any(notes, REPAIR_TRIGGERS):
            buckets.update(REPAIR_LABOR_BUCKETS)
        return buckets

    if foam_required:
        buckets = {"labor_prep", "spray_foam", "insulation", "labor_details", "labor_cleanup", "labor_loading"}
        if "wall" in project_type or "wall" in notes:
            buckets.add("wall_insulation")
        return buckets

    if "repair" in project_type:
        buckets = {"labor_prep", "labor_details", "labor_cleanup", "labor_loading", "roof_repair"}
        if _text_has_any(notes, REPAIR_TRIGGERS):
            buckets.update(REPAIR_LABOR_BUCKETS)
        if _text_has_any(notes, OPTIONAL_LABOR_BUCKET_TRIGGERS["labor_misc"]):
            buckets.add("labor_misc")
        return buckets

    return None


def filter_labor_calibration_rows(
    rows: list[Any],
    scope: dict[str, Any],
    decision: dict[str, Any],
) -> tuple[list[Any], list[str]]:
    allowed = selected_labor_buckets(scope, decision)
    if not allowed:
        return rows, []
    filtered: list[Any] = []
    excluded: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            filtered.append(row)
            continue
        bucket = first_nonblank(row.get("template_bucket"), row.get("task")).strip()
        if not bucket or bucket in allowed:
            filtered.append(row)
        else:
            excluded.append(bucket)
    return filtered, sorted(set(excluded))


def _median_positive(values: pd.Series) -> float | None:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    numeric = numeric[numeric > 0]
    return float(numeric.median()) if not numeric.empty else None


def _round_to_nearest(value: float, increment: int) -> int:
    if increment <= 0:
        return int(round(value))
    return int(round(value / increment) * increment)


def _scope_text(scope: dict[str, Any]) -> str:
    return " ".join(str(value or "") for value in (scope.get("notes"), scope.get("roof_condition"), scope.get("substrate"), scope.get("coating_type"))).lower()


def _decision_applies(decision: dict[str, Any] | None, *, include_review: bool = True) -> bool:
    if not decision:
        return False
    applies = decision.get("applies")
    return applies is True or (include_review and applies == "review")


def _work_package_dict(decision: WorkPackageDecision) -> dict[str, Any]:
    return asdict(decision)


def _build_work_package_decisions(scope: dict[str, Any], decision: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    text = _scope_text(scope)
    notes = first_nonblank(scope.get("notes")).lower()
    substrate = first_nonblank(scope.get("substrate")).lower()
    project_type = first_nonblank(scope.get("project_type")).lower()
    coating_type = first_nonblank(scope.get("coating_type")).lower()
    coating_required = bool(scope.get("coating_required") or coating_type or "coating" in text)
    metal_context = substrate == "metal" or "metal roof" in text or "standing seam" in text or "r panel" in text
    flat_membrane_context = any(term in text for term in ("flat roof", "membrane", "tpo", "epdm", "modified bitumen", "mod bit"))
    foam_context = bool(scope.get("foam_required") or scope.get("foam_thickness_inches")) or any(term in text for term in ("foam", "spf", "polyurethane foam"))

    packages: dict[str, WorkPackageDecision] = {}
    packages["coating"] = WorkPackageDecision(
        "coating",
        coating_required,
        0.9 if coating_required else 0.4,
        "Coating scope detected from notes or structured fields." if coating_required else "No coating scope detected.",
        "sqft",
        "full_area" if coating_required else "none",
        not coating_required,
    )

    primer_terms = (
        "primer",
        "prime",
        "adhesion concern",
        "adhesion test",
        "compatibility",
        "manufacturer requirement",
        "manufacturer required",
        "warranty requirement",
        "asphalt bleed",
        "bleed-through",
        "bleed through",
        "concrete",
        "modified bitumen",
        "mod bit",
        "severe weathering",
        "chalking",
    )
    rusted_metal = metal_context and any(term in text for term in ("rust", "rusted", "oxidized"))
    explicit_primer = any(term in text for term in primer_terms)
    if coating_required and (explicit_primer or rusted_metal or foam_context):
        primer_applies: bool | str = True if ("primer" in text or "prime" in text or rusted_metal or "asphalt bleed" in text or "bleed" in text) else "review"
        primer_reason = "Primer trigger found from substrate/condition/manufacturer language."
        primer_confidence = 0.78 if primer_applies is True else 0.58
    else:
        primer_applies = False
        primer_reason = "No primer trigger found; verify adhesion/manufacturer requirement."
        primer_confidence = 0.72 if coating_required else 0.45
    packages["primer"] = WorkPackageDecision(
        "primer",
        primer_applies,
        primer_confidence,
        primer_reason,
        "sqft",
        "full_area" if primer_applies is True else "unknown" if primer_applies == "review" else "none",
        primer_applies != True,
    )

    seam_terms = ("seam", "seams", "lap", "laps", "opening up", "open seams", "seam treatment")
    seam_applies = coating_required and (any(term in text for term in seam_terms) or metal_context)
    packages["seam_treatment"] = WorkPackageDecision(
        "seam_treatment",
        seam_applies,
        0.82 if seam_applies else 0.5,
        "Seam treatment indicated by roof coating seam language or metal roof scope." if seam_applies else "No seam treatment trigger found.",
        "detail_density",
        "spot_area" if seam_applies else "none",
        not seam_applies,
    )

    explicit_fastener = any(term in text for term in ("exposed fastener", "rusted fastener", "fastener leak", "fastener leaks", "screw", "screws", "fasteners"))
    fastener_applies = coating_required and explicit_fastener and (metal_context or any(term in text for term in ("exposed fastener", "rusted fastener", "screw", "screws", "fastener leak")))
    fastener_reason = (
        "Fastener treatment indicated by metal/exposed fastener language."
        if fastener_applies
        else "No metal/exposed fastener trigger found; do not include fastener treatment by default."
    )
    if explicit_fastener and flat_membrane_context and not metal_context:
        fastener_reason = "Fastener/detail language appears on a flat membrane scope; verify before adding fastener treatment."
    packages["fastener_treatment"] = WorkPackageDecision(
        "fastener_treatment",
        fastener_applies,
        0.82 if fastener_applies else 0.62,
        fastener_reason,
        "detail_density",
        "spot_area" if fastener_applies else "none",
        bool(explicit_fastener and not fastener_applies),
    )

    caulk_applies: bool | str = "review" if _caulk_detail_needed(scope) else False
    packages["caulk_detail"] = WorkPackageDecision(
        "caulk_detail",
        caulk_applies,
        0.62 if caulk_applies else 0.5,
        "Details/penetrations/drains indicate a caulk/detail allowance should be reviewed." if caulk_applies else "No detail allowance trigger found.",
        "detail_density",
        "spot_area" if caulk_applies else "none",
        bool(caulk_applies),
    )

    prep_applies = coating_required or any(term in text for term in ("power wash", "powerwash", "wash", "prep"))
    packages["prep_powerwash"] = WorkPackageDecision(
        "prep_powerwash",
        prep_applies,
        0.82 if prep_applies else 0.45,
        "Prep/power wash belongs to coating surface preparation." if prep_applies else "No prep/power wash trigger found.",
        "sqft",
        "full_area" if prep_applies else "none",
        False,
    )

    packages["foam"] = WorkPackageDecision(
        "foam",
        foam_context,
        0.82 if foam_context else 0.4,
        "Foam scope detected." if foam_context else "No foam scope detected.",
        "sqft",
        "full_area" if foam_context else "none",
        False,
    )

    repair_terms = ("tear off", "tear-off", "tearoff", "wet insulation", "saturated", "rotten", "replace roof", "major repair")
    repair_review_terms = ("ponding", "seams opening", "opening up", "few ponding", "repair")
    repair_applies: bool | str = True if any(term in text for term in repair_terms) else "review" if any(term in text for term in repair_review_terms) else False
    packages["tearoff_or_repair"] = WorkPackageDecision(
        "tearoff_or_repair",
        repair_applies,
        0.78 if repair_applies is True else 0.55 if repair_applies == "review" else 0.5,
        "Repair/tear-off trigger found." if repair_applies is True else "Localized repair review indicated." if repair_applies == "review" else "No tear-off/repair trigger found.",
        "manual",
        "spot_area" if repair_applies else "none",
        repair_applies != False,
    )

    return {name: _work_package_dict(package) for name, package in packages.items()}


def ensure_work_package_decisions(scope: dict[str, Any], decision: dict[str, Any]) -> dict[str, dict[str, Any]]:
    packages = decision.get("work_package_decisions")
    if not isinstance(packages, dict):
        packages = _build_work_package_decisions(scope, decision)
        decision["work_package_decisions"] = packages
    return packages


def _primer_needed(scope: dict[str, Any], material_assumptions: dict[str, Any]) -> bool:
    return _decision_applies(_build_work_package_decisions(scope).get("primer"), include_review=False)


def _fastener_treatment_needed(scope: dict[str, Any], material_assumptions: dict[str, Any]) -> bool:
    return _decision_applies(_build_work_package_decisions(scope).get("fastener_treatment"), include_review=False)


def _caulk_detail_needed(scope: dict[str, Any]) -> bool:
    text = _scope_text(scope)
    penetrations = first_nonblank(scope.get("penetrations_complexity")).lower()
    return penetrations in {"medium", "high"} or any(phrase in text for phrase in ("penetration", "curb", "detail", "caulk", "sealant", "skylight", "drain", "hvac", "rtu"))


def _matching_current_price(pricing: pd.DataFrame, keywords: list[str], preferred_columns: list[str]) -> dict[str, Any] | None:
    for column in preferred_columns:
        price = find_current_price(pricing, keywords, column)
        if price and price.get("matched_price_column") == column:
            return price
    return None


def _priced_allowance_row(
    *,
    item: str,
    category: str,
    quantity: float | int | None,
    unit: str,
    unit_price: float | None,
    selected_price_source: str,
    notes: str,
    estimated_cost: float | None = None,
    low_multiplier: float = 0.8,
    high_multiplier: float = 1.25,
) -> dict[str, Any]:
    if estimated_cost is None and quantity is not None and unit_price is not None:
        estimated_cost = float(quantity) * unit_price
    return {
        "item": item,
        "category": category,
        "quantity": quantity,
        "unit": unit,
        "selected_price_source": selected_price_source,
        "price_source_type": selected_price_source,
        "unit_price": unit_price,
        "estimated_cost": round(estimated_cost, 2) if estimated_cost is not None else None,
        "cost_low": round(estimated_cost * low_multiplier, 2) if estimated_cost is not None else None,
        "cost_high": round(estimated_cost * high_multiplier, 2) if estimated_cost is not None else None,
        "needs_review": True,
        "notes": notes,
    }


def _add_allowance_cost_to_totals(row: dict[str, Any], totals: tuple[float, float]) -> tuple[float, float]:
    low_total, high_total = totals
    estimated_cost = optional_positive_float(row.get("estimated_cost"))
    if estimated_cost is None:
        return low_total, high_total
    low = to_float_or_default(row.get("cost_low"), estimated_cost)
    high = to_float_or_default(row.get("cost_high"), estimated_cost)
    return low_total + low, high_total + high


def _row_with_package_context(
    row: dict[str, Any],
    package_decision: dict[str, Any] | None,
    *,
    source_type: str | None = None,
    matched_comparable_job_count: int | None = None,
) -> dict[str, Any]:
    package_decision = package_decision or {}
    row["applies_reason"] = package_decision.get("reason") or row.get("applies_reason") or ""
    row["review_required"] = bool(package_decision.get("review_required") or row.get("needs_review"))
    if matched_comparable_job_count is not None:
        row["matched_comparable_job_count"] = matched_comparable_job_count
    else:
        row.setdefault("matched_comparable_job_count", safe_int(row.get("evidence_count"), 0))
    if source_type:
        row["source_type"] = source_type
    else:
        row.setdefault("source_type", row.get("price_source_type") or row.get("selected_price_source") or "manual_review")
    row.setdefault("sanity_check_status", "ok")
    return row


def _sanity_check_material_row(row: dict[str, Any], area: float, package_name: str) -> dict[str, Any]:
    status = "ok"
    quantity = optional_positive_float(row.get("quantity"))
    unit = first_nonblank(row.get("unit")).lower()
    unit_price = optional_positive_float(row.get("unit_price"))
    notes: list[str] = []

    if package_name == "primer" and area > 0 and quantity is not None and unit in {"pail", "pails", "container", "containers", "drum", "drums"}:
        sqft_per_unit = area / quantity if quantity else None
        if sqft_per_unit is not None and sqft_per_unit < 500:
            status = "blocked: implausible primer quantity"
            notes.append(f"Implied {sqft_per_unit:.0f} sqft per {unit}; removed from base estimate pending review.")
    if unit in {"pail", "pails", "drum", "drums", "item", "each", "ea"} and unit_price is not None and unit_price < 20:
        notes.append(f"Unit price ${unit_price:g} for {unit} looks low; verify pricing.")
        if status == "ok":
            status = "warning: suspicious unit price"
    if package_name == "coating" and area > 0 and quantity is not None and unit in {"gal", "gallon", "gallons"}:
        sqft_per_gallon = area / quantity if quantity else None
        if sqft_per_gallon is not None and (sqft_per_gallon < 25 or sqft_per_gallon > 120):
            notes.append(f"Coating coverage {sqft_per_gallon:.0f} sqft/gal is outside a typical review range.")
            if status == "ok":
                status = "warning: coating coverage review"

    if status.startswith("blocked"):
        row["estimated_cost"] = None
        row["cost_low"] = None
        row["cost_high"] = None
        row["needs_review"] = True
        row["review_required"] = True
    if notes:
        row["notes"] = f"{row.get('notes') or ''} {' '.join(notes)}".strip()
    row["sanity_check_status"] = status
    return row


LABOR_BUCKET_TO_PACKAGE = {
    "labor_prep": "prep_powerwash",
    "labor_prime": "primer",
    "labor_seam_sealer": "seam_treatment",
    "labor_base": "coating",
    "labor_top_coat": "coating",
    "labor_caulk": "caulk_detail",
    "labor_details": "caulk_detail",
    "labor_cleanup": "coating",
    "labor_loading": "coating",
    "labor_traveling": "coating",
    "traveling": "coating",
    "roof_repair": "tearoff_or_repair",
    "tear_off": "tearoff_or_repair",
    "replacement": "tearoff_or_repair",
    "substrate_repair": "tearoff_or_repair",
}


def _labor_package_for_bucket(bucket: Any) -> str:
    key = first_nonblank(bucket).strip().lower()
    return LABOR_BUCKET_TO_PACKAGE.get(key, key or "labor_allowance")


def _labor_row_with_package_context(
    row: dict[str, Any],
    package_decision: dict[str, Any] | None,
    *,
    production_rate: float,
    evidence_count: int,
    source_type: str,
) -> dict[str, Any]:
    package_decision = package_decision or {}
    adjusted_days = safe_float(row.get("adjusted_days"), safe_float(row.get("crew_days"), 0.0))
    total_hours = safe_float(row.get("total_hours"), safe_float(row.get("labor_hours"), 0.0))
    crew_size = safe_int(row.get("crew_size"), 4)
    row["labor_package"] = package_decision.get("package_name") or _labor_package_for_bucket(row.get("task"))
    row["applies"] = package_decision.get("applies", True)
    row["basis"] = package_decision.get("basis") or "historical_calibration"
    row["production_rate"] = production_rate
    row["labor_hours"] = round(total_hours, 1)
    row["crew_days"] = round(adjusted_days, 2)
    row["reason"] = package_decision.get("reason") or "Labor calibrated from matching historical estimate rows."
    row["confidence"] = package_decision.get("confidence", 0.55)
    row["applies_reason"] = row["reason"]
    row["review_required"] = bool(package_decision.get("review_required") or row.get("needs_review"))
    row["matched_comparable_job_count"] = evidence_count
    row["source_type"] = source_type
    row["sanity_check_status"] = "ok" if total_hours >= 0 and crew_size > 0 else "warning: labor assumptions require review"
    return row


def _allowance_from_calibration(
    *,
    bucket: str,
    item: str,
    category: str,
    area: float,
    material_calibration: dict[str, Any],
    fallback_quantity: float | int | None,
    fallback_unit: str,
    fallback_unit_price: float | None,
    fallback_notes: str,
    review_flags: list[str],
    package_decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    calibration = material_calibration.get(bucket) or {}
    evidence_count = safe_int(calibration.get("evidence_count"), 0)
    quantity_ratio = optional_positive_float(calibration.get("median_quantity_per_sqft"))
    cost_ratio = optional_positive_float(calibration.get("median_cost_per_sqft"))
    current_price = optional_positive_float(calibration.get("selected_current_unit_price"))
    current_item = calibration.get("selected_current_price_item") or {}
    current_item_name = first_nonblank(current_item.get("product_name") if isinstance(current_item, dict) else "", item)
    unit = first_nonblank(calibration.get("unit"), fallback_unit)

    if evidence_count >= 3 and quantity_ratio is not None and current_price is not None:
        quantity = quantity_ratio * area
        review_flags.append(f"{item} quantity estimated from historical ratio; verify requirement.")
        return _priced_allowance_row(
            item=f"{current_item_name} - historically calibrated",
            category=category,
            quantity=round(quantity, 2),
            unit=unit,
            unit_price=current_price,
            selected_price_source="current_pricing + historical_quantity_ratio",
            notes=f"Estimated from historical {item.lower()} quantity per sqft and current pricing; estimator should verify requirement.",
        ) | {
            "evidence_count": evidence_count,
            "calibration_method": "historical_quantity_ratio",
            "source_type": "physical_quantity_ratio",
        }

    if evidence_count >= 3 and cost_ratio is not None:
        estimated_cost = cost_ratio * area
        review_flags.append(f"{item} estimated from historical cost ratio; verify scope.")
        return _priced_allowance_row(
            item=f"{item} - historically calibrated",
            category=category,
            quantity=None,
            unit="sqft",
            unit_price=None,
            estimated_cost=estimated_cost,
            selected_price_source="historical_cost_ratio",
            notes=f"Estimated from historical {item.lower()} cost per sqft; estimator should verify quantity and price.",
        ) | {
            "evidence_count": evidence_count,
            "calibration_method": "historical_cost_ratio",
            "source_type": "cost_allowance_ratio",
        }

    if fallback_quantity is not None and (fallback_unit_price is not None or current_price is not None):
        if evidence_count < 3:
            review_flags.append(f"Low historical evidence for {item.lower()}; fallback allowance used.")
        unit_price = current_price if current_price is not None else fallback_unit_price
        price_source = "current_pricing + deterministic_quantity" if current_price is not None else "rule_based_allowance"
        return _priced_allowance_row(
            item=current_item_name if current_price is not None else item,
            category=category,
            quantity=fallback_quantity,
            unit=fallback_unit,
            unit_price=unit_price,
            selected_price_source=price_source,
            notes=fallback_notes,
        ) | {
            "evidence_count": evidence_count,
            "calibration_method": "deterministic_fallback",
            "source_type": "current_pricing" if current_price is not None else "manual_review",
        }

    return _priced_allowance_row(
        item=item,
        category=category,
        quantity=None,
        unit=fallback_unit,
        unit_price=None,
        selected_price_source="review_allowance",
        notes=f"{item} could not be priced; estimator should verify quantity and pricing.",
    ) | {"evidence_count": evidence_count, "calibration_method": "unpriced_review", "source_type": "manual_review"}


def build_material_plan(
    scope: dict[str, Any],
    data: EstimatorData,
    calibration: dict[str, Any],
    decision: dict[str, Any],
    assumptions: EstimatorAssumptions,
) -> tuple[list[dict[str, Any]], float, float, list[str]]:
    area = to_float(scope.get("surface_area_sqft")) or 0.0
    coating_type = first_nonblank(scope.get("coating_type"))
    plan: list[dict[str, Any]] = []
    review_flags: list[str] = []
    low_total = 0.0
    high_total = 0.0
    work_packages = ensure_work_package_decisions(scope, decision)
    if scope.get("coating_required") and area:
        coating_decision = work_packages.get("coating")
        wet_mils = warranty_wet_mils(scope.get("warranty_target"), coating_type)
        gallons = coating_gallons(area, wet_mils, assumptions.coating_waste_factor)
        price = find_current_price(data.pricing, [coating_type] if coating_type else ["coating"], "price_per_gallon")
        price_source = "current_pricing"
        needs_review = False
        unit_price = to_float(price.get("matched_price")) if price else None
        item_name = first_nonblank(price.get("product_name") if price else "", coating_type, "Roof coating")
        if unit_price is None:
            fallback_psf = to_float(calibration.get("median_material_cost_per_sqft"))
            historical_unit = historical_unit_cost(data.line_items if not data.line_items.empty else data.classified_line_items, [coating_type or "coating"], area)
            price_source = "historical_fallback"
            needs_review = True
            review_flags.append("Historical fallback pricing used for coating.")
            if historical_unit:
                unit_price = historical_unit
                cost_target = gallons * unit_price
            elif fallback_psf:
                cost_target = fallback_psf * area
            else:
                cost_target = 0.0
                review_flags.append("No coating price available.")
        else:
            cost_target = gallons * unit_price
        low = cost_target * 0.9
        high = cost_target * 1.15
        low_total += low
        high_total += high
        coating_row = _row_with_package_context(
            {
                "item": item_name,
                "category": "coating",
                "quantity": round(gallons, 1),
                "unit": "gal",
                "selected_price_source": price_source,
                "price_source_type": price_source,
                "unit_price": unit_price,
                "estimated_cost": round(cost_target, 2),
                "cost_low": round(low, 2),
                "cost_high": round(high, 2),
                "needs_review": needs_review,
                "notes": f"{wet_mils:g} wet mils with {assumptions.coating_waste_factor:.0%} waste factor.",
            },
            coating_decision,
            source_type=price_source,
        )
        coating_row = _sanity_check_material_row(coating_row, area, "coating")
        plan.append(coating_row)
    material_assumptions = decision.get("material_assumptions", {})
    is_metal_roof_coating = bool(scope.get("coating_required")) and first_nonblank(scope.get("substrate")).lower() == "metal"
    material_calibration = calibration.get("material_calibration") or build_material_calibration(data, scope)
    calibration["material_calibration"] = material_calibration
    primer_decision = work_packages.get("primer")
    if _decision_applies(primer_decision, include_review=False):
        if area <= 0:
            row = _priced_allowance_row(
                item="Primer allowance",
                category="allowance",
                quantity=None,
                unit="sqft",
                unit_price=None,
                selected_price_source="review_allowance",
                notes="Primer allowance could not be priced because estimated square footage is missing.",
            )
            review_flags.append("Primer allowance could not be priced because estimated_sqft is missing.")
        else:
            text = _scope_text(scope)
            fallback_unit_price = 0.4 if any(token in text for token in ("poor", "heavy rust", "severe rust", "oxidized")) else 0.25
            row = _allowance_from_calibration(
                bucket="primer",
                item="Primer allowance",
                category="primer",
                area=area,
                material_calibration=material_calibration,
                fallback_quantity=round(area, 1),
                fallback_unit="sqft",
                fallback_unit_price=fallback_unit_price,
                fallback_notes="Rule-based primer allowance due to rust/condition; estimator should verify primer requirement.",
                review_flags=review_flags,
                package_decision=primer_decision,
            )
        row = _row_with_package_context(row, primer_decision)
        row = _sanity_check_material_row(row, area, "primer")
        plan.append(row)
        low_total, high_total = _add_allowance_cost_to_totals(row, (low_total, high_total))
    elif primer_decision and primer_decision.get("review_required"):
        review_flags.append(primer_decision.get("reason") or "Primer requirement should be verified.")

    seam_decision = work_packages.get("seam_treatment")
    if _decision_applies(seam_decision, include_review=False):
        if area <= 0:
            row = _priced_allowance_row(
                item="Seam treatment allowance",
                category="allowance",
                quantity=None,
                unit="lf",
                unit_price=None,
                selected_price_source="review_allowance",
                notes="Seam treatment allowance could not be priced because estimated square footage is missing.",
            )
            review_flags.append("Seam treatment allowance could not be priced because estimated_sqft is missing.")
        else:
            seam_lf = _round_to_nearest(math.sqrt(area) * 8, 10)
            row = _allowance_from_calibration(
                bucket="seam_treatment",
                item="Seam treatment allowance",
                category="seam_treatment",
                area=area,
                material_calibration=material_calibration,
                fallback_quantity=seam_lf,
                fallback_unit="lf",
                fallback_unit_price=3.0,
                fallback_notes="Rule-based seam/detail LF allowance for metal roof coating; estimator should verify seam layout and detail requirements.",
                review_flags=review_flags,
                package_decision=seam_decision,
            )
        row = _row_with_package_context(row, seam_decision)
        row = _sanity_check_material_row(row, area, "seam_treatment")
        plan.append(row)
        low_total, high_total = _add_allowance_cost_to_totals(row, (low_total, high_total))

    fastener_decision = work_packages.get("fastener_treatment")
    if _decision_applies(fastener_decision, include_review=False):
        if area <= 0:
            row = _priced_allowance_row(
                item="Fastener treatment allowance",
                category="allowance",
                quantity=None,
                unit="ea",
                unit_price=None,
                selected_price_source="review_allowance",
                notes="Fastener treatment allowance could not be priced because estimated square footage is missing.",
            )
            review_flags.append("Fastener treatment allowance could not be priced because estimated_sqft is missing.")
        else:
            fasteners = _round_to_nearest(area / 20, 25)
            row = _allowance_from_calibration(
                bucket="fastener_treatment",
                item="Fastener treatment allowance",
                category="fastener_treatment",
                area=area,
                material_calibration=material_calibration,
                fallback_quantity=fasteners,
                fallback_unit="ea",
                fallback_unit_price=1.5,
                fallback_notes="Rule-based fastener treatment allowance; estimator should verify count and detail requirements.",
                review_flags=review_flags,
                package_decision=fastener_decision,
            )
        row = _row_with_package_context(row, fastener_decision)
        row = _sanity_check_material_row(row, area, "fastener_treatment")
        plan.append(row)
        low_total, high_total = _add_allowance_cost_to_totals(row, (low_total, high_total))
    elif fastener_decision and fastener_decision.get("review_required"):
        review_flags.append(fastener_decision.get("reason") or "Fastener treatment should be verified.")

    caulk_decision = work_packages.get("caulk_detail")
    if _decision_applies(caulk_decision, include_review=True):
        if area <= 0:
            row = _priced_allowance_row(
                item="Caulk/detail allowance",
                category="caulk_detail",
                quantity=None,
                unit="allowance",
                unit_price=None,
                selected_price_source="review_allowance",
                notes="Caulk/detail allowance could not be priced because estimated square footage is missing.",
            )
            review_flags.append("Caulk/detail allowance could not be priced because estimated_sqft is missing.")
        else:
            detail_units = _round_to_nearest(area / 1000, 1)
            row = _allowance_from_calibration(
                bucket="caulk_detail",
                item="Caulk/detail allowance",
                category="caulk_detail",
                area=area,
                material_calibration=material_calibration,
                fallback_quantity=max(detail_units, 1),
                fallback_unit="allowance",
                fallback_unit_price=150.0,
                fallback_notes="Rule-based caulk/detail allowance for penetrations and roof details; estimator should verify count.",
                review_flags=review_flags,
                package_decision=caulk_decision,
            )
        row = _row_with_package_context(row, caulk_decision)
        row = _sanity_check_material_row(row, area, "caulk_detail")
        plan.append(row)
        low_total, high_total = _add_allowance_cost_to_totals(row, (low_total, high_total))
    return plan, round(low_total, 2), round(high_total, 2), review_flags


def build_labor_plan(
    scope: dict[str, Any],
    calibration: dict[str, Any],
    decision: dict[str, Any],
    assumptions: EstimatorAssumptions,
) -> tuple[list[dict[str, Any]], float, float, int, int, int]:
    area = safe_float(scope.get("surface_area_sqft"), 0.0)
    multiplier = to_float_or_default(decision.get("labor_modifiers", {}).get("combined_labor_multiplier"), 1.0)
    production_rate = to_float_or_default(decision.get("labor_modifiers", {}).get("adjusted_productivity_sqft_per_day"), 0.0)
    crew_size = sane_crew_size(decision.get("crew_assumptions", {}).get("recommended_crew_size"), 4)
    if crew_size <= 0:
        crew_size = 4
    work_packages = ensure_work_package_decisions(scope, decision)
    raw_rows = calibration.get("labor_by_bucket") or []
    rows, excluded_buckets = filter_labor_calibration_rows(raw_rows, scope, decision)
    if excluded_buckets:
        calibration["excluded_labor_buckets"] = excluded_buckets
    plan: list[dict[str, Any]] = []
    incomplete_calibration = False
    skipped_rows: list[str] = []
    total_hours = 0.0
    total_cost = 0.0
    filtered_crew_sizes: list[int] = []
    if rows:
        for row in rows:
            try:
                hours_missing = is_missing_or_bad_number(row.get("median_total_hours"))
                days_missing = is_missing_or_bad_number(row.get("median_days"))
                crew_missing = is_missing_or_bad_number(row.get("median_crew_size"))
                cost_missing = is_missing_or_bad_number(row.get("median_estimated_cost"))
                evidence_missing = is_missing_or_bad_number(row.get("evidence_count"))
                row_incomplete = any([hours_missing, days_missing, crew_missing, cost_missing, evidence_missing])
                incomplete_calibration = incomplete_calibration or row_incomplete
                days = 1.0 if days_missing else max(safe_float(row.get("median_days"), 1.0), 0.0)
                raw_crew_size = safe_int(row.get("median_crew_size"), 0)
                valid_historical_crew = bool(raw_crew_size and 0 < raw_crew_size <= 12)
                row_crew_size = raw_crew_size if valid_historical_crew else crew_size
                if row_crew_size <= 0:
                    row_crew_size = 4
                elif valid_historical_crew:
                    filtered_crew_sizes.append(row_crew_size)
                hours = None if hours_missing else max(safe_float(row.get("median_total_hours"), 0.0), 0.0)
                cost_value = row.get("median_estimated_cost")
                if is_missing_or_bad_number(cost_value):
                    cost_value = row.get("median_cost")
                if is_missing_or_bad_number(cost_value):
                    cost_missing = True
                    row_incomplete = True
                    incomplete_calibration = True
                    cost = None
                else:
                    cost = max(safe_float(cost_value, 0.0), 0.0)
                adjusted_days = safe_float(days * multiplier, 1.0)
                adjusted_hours = safe_float(hours * multiplier, 0.0) if hours is not None else adjusted_days * row_crew_size * 10
                estimated_cost = safe_float(cost * multiplier, 0.0) if cost is not None else 0.0
                total_hours += adjusted_hours
                total_cost += estimated_cost
                task = row.get("template_bucket") or "labor_calibration"
                labor_package = _labor_package_for_bucket(task)
                evidence_count = safe_int(row.get("evidence_count"), 0)
                plan.append(
                    _labor_row_with_package_context(
                        {
                            "task": task,
                            "base_days": round(days, 2),
                            "adjusted_days": round(adjusted_days, 2),
                            "crew_size": row_crew_size,
                            "total_hours": round(adjusted_hours, 1),
                            "estimated_cost": round(estimated_cost, 2),
                            "evidence_count": evidence_count,
                            "needs_review": bool(row_incomplete),
                            "notes": (
                                "Historical labor calibration was incomplete for one or more tasks; defaults were used."
                                if row_incomplete
                                else "Calibrated from estimate_template_rows."
                            ),
                        },
                        work_packages.get(labor_package),
                        production_rate=production_rate,
                        evidence_count=evidence_count,
                        source_type="historical_calibration",
                    )
                )
            except Exception as err:
                incomplete_calibration = True
                skipped_rows.append(f"Skipped malformed labor calibration row: {type(err).__name__}")
                continue
    if not plan:
        days = 1.0
        total_hours = 40.0
        total_cost = 0.0
        plan.append(
            _labor_row_with_package_context(
                {
                "task": "labor_allowance",
                "base_days": 1.0,
                "adjusted_days": 1.0,
                "crew_size": 4,
                "total_hours": 40,
                "estimated_cost": 0.0,
                "evidence_count": 0,
                "needs_review": True,
                "notes": "Historical labor calibration unavailable; estimator must price labor manually.",
                },
                None,
                production_rate=production_rate,
                evidence_count=0,
                source_type="manual_review",
            )
        )
        crew_size = 4
    elif filtered_crew_sizes:
        filtered_crew_sizes = sorted(filtered_crew_sizes)
        crew_size = filtered_crew_sizes[len(filtered_crew_sizes) // 2]
    low = total_cost * 0.85
    high = total_cost * 1.2
    if skipped_rows:
        plan[0]["notes"] = f"{plan[0].get('notes', '')} {'; '.join(skipped_rows[:3])}".strip()
    duration_total = sum(safe_float(row.get("adjusted_days"), 0.0) for row in plan)
    duration_days = max(1, safe_int(round(duration_total), 1))
    return plan, round(low, 2), round(high, 2), crew_size, duration_days, safe_int(round(total_hours), 0)


def similar_examples(similar: pd.DataFrame) -> list[dict[str, Any]]:
    if similar.empty:
        return []
    keep = [
        "job_id",
        "customer",
        "job_name",
        "estimated_sqft",
        "estimated_value",
        "price_per_sqft",
        "estimate_file",
        "folder_url",
        "similarity_score",
        "reason_matched",
    ]
    return similar[[column for column in keep if column in similar.columns]].head(8).to_dict(orient="records")


def _dimension_summary_value(summary: Any, key: str) -> Any:
    if isinstance(summary, dict):
        return summary.get(key)
    return getattr(summary, key, None)


def resolve_estimated_sqft(parsed: Any, scope: dict[str, Any], overrides: dict[str, Any]) -> float | None:
    dimension_summary = getattr(parsed, "dimension_summary", {}) or scope.get("dimension_summary") or {}
    candidates = [
        overrides.get("estimated_sqft"),
        overrides.get("surface_area_sqft"),
        overrides.get("sqft_override"),
        _dimension_summary_value(dimension_summary, "net_area_sqft"),
        getattr(parsed, "estimated_sqft", None),
        scope.get("estimated_sqft"),
        scope.get("surface_area_sqft"),
    ]
    for candidate in candidates:
        number = optional_positive_float(candidate)
        if number is not None:
            return number
    return None


def draft_workbook_inputs(field_input: FieldNotesInput, scope: dict[str, Any], material_plan: list[dict[str, Any]], labor_plan: list[dict[str, Any]], travel_plan: dict[str, Any], review_flags: list[str]) -> dict[str, Any]:
    city_state_zip = " ".join(
        part
        for part in [
            ", ".join(part for part in (scope.get("city"), scope.get("state")) if part),
            field_input.zip_code or "",
        ]
        if part
    )
    dimension_summary = scope.get("dimension_summary") or {}
    resolved_sqft = optional_positive_float(scope.get("estimated_sqft")) or optional_positive_float(scope.get("surface_area_sqft"))
    return {
        "header": {
            "C2_job_name": first_nonblank(field_input.job_name, scope.get("project_type"), "Field Notes Estimate Draft"),
            "C3_job_type": scope.get("project_type"),
            "C4_site_address": field_input.site_address,
            "C5_city_state_zip": city_state_zip,
            "C12_estimated_sqft": resolved_sqft,
            "gross_area_sqft": scope.get("gross_area_sqft") or _dimension_summary_value(dimension_summary, "gross_area_sqft"),
            "deduction_area_sqft": scope.get("deduction_area_sqft") or _dimension_summary_value(dimension_summary, "deduction_area_sqft"),
            "net_area_sqft": scope.get("net_area_sqft") or _dimension_summary_value(dimension_summary, "net_area_sqft"),
            "dimension_notes": scope.get("dimension_warnings") or _dimension_summary_value(dimension_summary, "warnings") or [],
        },
        "material_rows": material_plan,
        "labor_rows": labor_plan,
        "travel_rows": [travel_plan],
        "adders_review_rows": [{"flag": flag} for flag in review_flags],
    }


def estimate_from_field_notes(
    raw_notes: str,
    optional_overrides: dict[str, Any] | None = None,
    database_url: str | None = None,
    *,
    data: EstimatorData | None = None,
    assumptions: EstimatorAssumptions | None = None,
) -> EstimateRecommendation:
    assumptions = assumptions or EstimatorAssumptions()
    optional_overrides = optional_overrides or {}
    field_input = FieldNotesInput(
        raw_notes=raw_notes,
        job_name=optional_overrides.get("job_name"),
        site_address=optional_overrides.get("site_address"),
        city=optional_overrides.get("city"),
        state=optional_overrides.get("state"),
        zip_code=optional_overrides.get("zip_code"),
        estimated_sqft=optional_positive_float(optional_overrides.get("estimated_sqft")),
        substrate=optional_overrides.get("substrate"),
        roof_condition=optional_overrides.get("roof_condition"),
        coating_type=optional_overrides.get("coating_type"),
        warranty_target_years=optional_positive_int(optional_overrides.get("warranty_target_years")),
        access_complexity=optional_overrides.get("access_complexity"),
        penetrations_complexity=optional_overrides.get("penetrations_complexity"),
        insulation_present=optional_overrides.get("insulation_present"),
        condensation_risk=optional_overrides.get("condensation_risk"),
    )
    if data is None:
        data = load_estimator_data(database_url=database_url, prefer_database=bool(database_url))
    parsed = parse_field_notes(field_input)
    scope = parsed_to_scope(parsed, field_input)
    resolved_sqft = resolve_estimated_sqft(parsed, scope, optional_overrides)
    if resolved_sqft is not None:
        parsed.estimated_sqft = resolved_sqft
        parsed.missing_info = [item for item in parsed.missing_info if item != "estimated_sqft"]
        scope["estimated_sqft"] = resolved_sqft
        scope["surface_area_sqft"] = resolved_sqft
    dimension_summary = parsed.dimension_summary or {}
    scope["dimension_summary"] = dimension_summary
    scope["gross_area_sqft"] = scope.get("gross_area_sqft") or _dimension_summary_value(dimension_summary, "gross_area_sqft")
    scope["deduction_area_sqft"] = scope.get("deduction_area_sqft") or _dimension_summary_value(dimension_summary, "deduction_area_sqft")
    scope["net_area_sqft"] = scope.get("net_area_sqft") or _dimension_summary_value(dimension_summary, "net_area_sqft")
    similar = find_similar_jobs(data, scope, limit=8)
    legacy_calibration = calibrate_from_history(similar, data.line_items, scope)
    template_calibration = historical_template_calibration(data, similar)
    calibration = {**legacy_calibration, **template_calibration}
    decision = evaluate_decision_tree(scope, calibration)
    calibration["work_package_decisions"] = ensure_work_package_decisions(scope, decision)
    material_plan, material_low, material_high, material_review_flags = build_material_plan(scope, data, calibration, decision, assumptions)
    labor_review_flags: list[str] = []
    try:
        labor_plan, labor_low, labor_high, crew_size, duration_days, _labor_hours = build_labor_plan(scope, calibration, decision, assumptions)
    except Exception as err:
        labor_plan = [
            {
                "task": "labor_allowance",
                "base_days": 1.0,
                "adjusted_days": 1.0,
                "crew_size": 4,
                "total_hours": 40,
                "estimated_cost": 0.0,
                "needs_review": True,
                "notes": f"Labor calibration failed; manual labor pricing required. Error: {type(err).__name__}",
            }
        ]
        labor_low = 0.0
        labor_high = 0.0
        crew_size = 4
        duration_days = 1
        labor_review_flags = ["Historical labor calibration failed; manual labor pricing required."]
    travel_plan = build_travel_plan(scope, recommended_crew_size=crew_size, estimated_work_days=duration_days, assumptions=assumptions)
    equipment_low = sum(to_float(row.get("estimated_cost")) or 0 for row in material_plan if row.get("category") == "equipment") * 0.85
    equipment_high = equipment_low * 1.25
    travel_low = to_float(travel_plan.get("travel_vehicle_cost")) or 0
    travel_high = travel_low * 1.15
    subtotal_low = material_low + labor_low + equipment_low + travel_low
    subtotal_high = material_high + labor_high + equipment_high + travel_high
    estimate_low = subtotal_low * 1.18
    estimate_high = subtotal_high * 1.28
    estimate_target = (estimate_low + estimate_high) / 2
    review_flags = []
    review_flags.extend(f"Missing: {item}" for item in parsed.missing_info)
    review_flags.extend(parsed.review_flags)
    review_flags.extend(decision.get("human_review_flags") or [])
    review_flags.extend(material_review_flags)
    review_flags.extend(labor_review_flags)
    if any("Historical labor calibration was incomplete" in str(row.get("notes") or "") for row in labor_plan):
        review_flags.append("Historical labor calibration was incomplete for one or more tasks.")
    if any("Historical labor calibration unavailable" in str(row.get("notes") or "") for row in labor_plan):
        review_flags.append("Historical labor calibration unavailable or incomplete.")
    if travel_plan.get("needs_travel_review"):
        review_flags.append("Travel assumptions require review.")
    if data.template_rows.empty:
        review_flags.append("estimate_template_rows unavailable or empty; template calibration is limited.")
    if data.pricing.empty:
        review_flags.append("pricing_catalog unavailable or empty; current material pricing is limited.")
    if data.template_rows.empty and not data.classified_line_items.empty:
        review_flags.append("Using estimate_line_item_classifications fallback evidence.")
    parsed_fields = asdict(parsed)
    if resolved_sqft is not None:
        parsed_fields["estimated_sqft"] = resolved_sqft
        parsed_fields["surface_area_sqft"] = resolved_sqft
    return EstimateRecommendation(
        parsed_fields=parsed_fields,
        recommended_scope=decision.get("recommended_scope") or [],
        material_plan=material_plan,
        labor_plan=labor_plan,
        travel_plan=travel_plan,
        historical_calibration=calibration,
        similar_examples=similar_examples(similar),
        estimate_low=round(estimate_low, 2),
        estimate_target=round(estimate_target, 2),
        estimate_high=round(estimate_high, 2),
        review_flags=review_flags,
        human_review_required=bool(review_flags),
        draft_workbook_inputs=draft_workbook_inputs(field_input, scope, material_plan, labor_plan, travel_plan, review_flags),
    )
