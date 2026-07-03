from __future__ import annotations

import math
import re
import hashlib
import time
from dataclasses import asdict, dataclass
from typing import Any
from uuid import uuid4

import pandas as pd

from . import ai_scope_interpreter
from .calibration import calibrate_from_history
from .data_loader import load_estimator_data
from .decision_tree import evaluate_decision_tree
from .field_notes import parse_field_notes, parsed_to_scope
from .line_items import summarize_similar_job_buckets
from .material_calibration import build_bucket_calibration, normalize_unit, sane_quantity_ratio
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


def notes_hash(notes: str | None) -> str:
    return hashlib.sha256((notes or "").encode("utf-8")).hexdigest()


def new_estimator_run_id(notes: str | None) -> str:
    return f"field-{notes_hash(notes)[:12]}-{uuid4().hex[:8]}"


READY_TO_ESTIMATE = "READY_TO_ESTIMATE"
NEED_MORE_INFORMATION = "NEED_MORE_INFORMATION"
RECOMMEND_SITE_VISIT = "RECOMMEND_SITE_VISIT"
RECOMMEND_RESTORATION_OPTION = "RECOMMEND_RESTORATION_OPTION"


ROOF_RESTORATION_SCOPE_KEYWORDS = (
    "coating",
    "coat ",
    "coated",
    "silicone",
    "acrylic",
    "urethane",
    "restoration",
    "restore",
    "full roof",
    "warranty",
    "recover",
    "maintenance coating",
)


def _notes_contain_any(notes: str, keywords: tuple[str, ...]) -> bool:
    normalized = f" {normalized_source_text(notes)} "
    return any(keyword in normalized for keyword in keywords)


def _is_roof_restoration_or_coating_scope(scope: dict[str, Any], notes: str) -> bool:
    project_type = normalized_source_text(scope.get("project_type"))
    division = normalized_source_text(scope.get("division"))
    coating_type = normalized_source_text(scope.get("coating_type"))
    if "roof" in project_type and any(token in project_type for token in ("coating", "restoration", "restore")):
        return True
    if division == "roofing" and (coating_type or scope.get("warranty_target_years")):
        return True
    if coating_type or scope.get("warranty_target_years"):
        return True
    return _notes_contain_any(notes, ROOF_RESTORATION_SCOPE_KEYWORDS)


def evaluate_estimate_readiness(scope: dict[str, Any], notes: str) -> dict[str, Any]:
    """Return a deterministic estimate-readiness gate before calibration/pricing."""
    resolved_sqft = optional_positive_float(scope.get("estimated_sqft")) or optional_positive_float(scope.get("surface_area_sqft"))
    if scope_template_type(scope) == "insulation" and resolved_sqft is None:
        return {
            "estimate_status": NEED_MORE_INFORMATION,
            "estimate_reason": "Insulation area is unknown. An insulation estimate cannot be generated without building dimensions or square footage.",
            "missing_fields": ["estimated_sqft"],
            "required_questions": [
                "Building length and width?",
                "Wall height?",
                "Which surfaces should be insulated?",
                "Foam type and desired thickness or R-value?",
            ],
            "recommended_next_actions": ["Request insulation dimensions", "Confirm wall/ceiling scope", "Confirm opening deductions"],
            "confidence": "high",
        }
    is_roof_restoration = _is_roof_restoration_or_coating_scope(scope, notes)
    if is_roof_restoration and resolved_sqft is None:
        comparison_requested = _notes_contain_any(notes, ("repair", "repairs", "restoration", "restore", "make more sense"))
        actions = ["Request roof measurements", "Schedule inspection"]
        if comparison_requested:
            actions.append("Offer both repair and restoration options after measurements are available")
        return {
            "estimate_status": NEED_MORE_INFORMATION,
            "estimate_reason": "Roof area is unknown. A coating estimate cannot be generated without roof size.",
            "missing_fields": ["estimated_sqft"],
            "required_questions": [
                "Approximate roof square footage?",
                "Roof dimensions?",
                "Is this repair only or full restoration?",
                "Address for travel?",
            ],
            "recommended_next_actions": actions,
            "confidence": "high",
        }
    return {
        "estimate_status": READY_TO_ESTIMATE,
        "estimate_reason": "Required estimate inputs are present.",
        "missing_fields": [],
        "required_questions": [],
        "recommended_next_actions": [],
        "confidence": "medium",
    }


def normalized_source_text(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def collect_source_text_fields(value: Any, path: str = "") -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if str(key) == "source_text" and first_nonblank(item):
                rows.append({"field": child_path, "source_text": first_nonblank(item)})
            rows.extend(collect_source_text_fields(item, child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            rows.extend(collect_source_text_fields(item, f"{path}[{index}]"))
    return rows


def stale_source_text_fields(parsed_fields: dict[str, Any], notes: str) -> list[dict[str, str]]:
    normalized_notes = normalized_source_text(notes)
    stale: list[dict[str, str]] = []
    for row in collect_source_text_fields(parsed_fields):
        source = normalized_source_text(row.get("source_text"))
        if source and source not in normalized_notes:
            stale.append(row)
    return stale


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
    context_by_job: dict[str, dict[str, Any]] = {}
    for frame in (data.jobs, data.estimates):
        if frame.empty or "job_id" not in frame.columns:
            continue
        for _, row in frame.iterrows():
            job_id = row.get("job_id")
            if job_id is None:
                continue
            job_key = str(job_id)
            sqft = to_float(row.get("estimated_sqft")) or to_float(row.get("surface_area_sqft"))
            if sqft:
                sqft_by_job[job_key] = sqft
            context = context_by_job.setdefault(job_key, {})
            for source, target in (
                ("template_type", "job_template_type"),
                ("project_type", "job_project_type"),
                ("job_type", "job_project_type"),
                ("substrate", "job_substrate"),
                ("division", "job_division"),
                ("warranty_years", "job_warranty_years"),
                ("warranty_target_years", "job_warranty_years"),
                ("coating_type", "job_coating_type"),
                ("estimated_sqft", "job_area_sqft"),
                ("surface_area_sqft", "job_area_sqft"),
            ):
                value = row.get(source)
                if target not in context and first_nonblank(value):
                    context[target] = value
    if "job_id" in rows.columns:
        job_keys = rows["job_id"].astype(str)
        rows["historical_sqft"] = job_keys.map(sqft_by_job)
        for column in (
            "job_template_type",
            "job_project_type",
            "job_substrate",
            "job_division",
            "job_warranty_years",
            "job_coating_type",
            "job_area_sqft",
        ):
            rows[column] = job_keys.map(lambda job_id: context_by_job.get(job_id, {}).get(column))
    return rows


def historical_template_calibration(data: EstimatorData, similar_jobs: pd.DataFrame) -> dict[str, Any]:
    template_rows = template_rows_with_job_sqft(data)
    if template_rows.empty:
        return {
            "source": "estimate_line_item_classifications" if not data.classified_line_items.empty else "none",
            "template_row_count": 0,
            "labor_by_bucket": [],
            "all_labor_rows": [],
            "relationship_labor_rates": data.relationship_labor_rates.to_dict(orient="records") if not data.relationship_labor_rates.empty else [],
            "job_package_summary": data.job_package_summary.to_dict(orient="records") if not data.job_package_summary.empty else [],
            "material_by_bucket": [],
            "median_labor_cost_per_sqft": None,
            "median_material_cost_per_sqft": None,
            "worksheet_price_examples": [],
        }
    similar_for_evidence = similar_jobs
    if "included_as_evidence" in similar_for_evidence.columns:
        included_mask = similar_for_evidence["included_as_evidence"].astype(str).str.lower().isin({"true", "1", "yes"})
        similar_for_evidence = similar_for_evidence[included_mask].copy()
    similar_ids = set(similar_for_evidence.get("job_id", pd.Series(dtype=str)).dropna().astype(str))
    if similar_ids and "job_id" in template_rows.columns:
        relevant = template_rows[template_rows["job_id"].astype(str).isin(similar_ids)].copy()
        if relevant.empty:
            relevant = template_rows.copy()
    else:
        relevant = template_rows.copy()
    for column in ("estimated_cost", "total_hours", "days", "crew_size", "historical_sqft"):
        if column in relevant.columns:
            relevant[column] = pd.to_numeric(relevant[column], errors="coerce")
        if column in template_rows.columns:
            template_rows[column] = pd.to_numeric(template_rows[column], errors="coerce")
    labor_rows = relevant[relevant.get("line_item_kind", pd.Series(dtype=str)).astype(str).eq("labor")].copy()
    all_labor_rows = template_rows[template_rows.get("line_item_kind", pd.Series(dtype=str)).astype(str).eq("labor")].copy()
    for labor_frame in (labor_rows, all_labor_rows):
        if not labor_frame.empty:
            if "template_bucket" not in labor_frame.columns:
                labor_frame["template_bucket"] = ""
            labor_frame["template_bucket"] = labor_frame.apply(
                lambda row: _task_name_from_row(row.to_dict()) or first_nonblank(row.get("template_bucket")),
                axis=1,
            )
    material_rows = relevant[relevant.get("line_item_kind", pd.Series(dtype=str)).astype(str).isin(["material", "equipment", "travel"])].copy()
    template_bucket_series = (
        relevant["template_bucket"]
        if "template_bucket" in relevant.columns
        else pd.Series("", index=relevant.index, dtype=str)
    )
    totals = relevant[template_bucket_series.astype(str).eq("worksheet_price")].copy()
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
        "all_labor_rows": all_labor_rows.to_dict(orient="records") if not all_labor_rows.empty else [],
        "relationship_labor_rates": data.relationship_labor_rates.to_dict(orient="records") if not data.relationship_labor_rates.empty else [],
        "job_package_summary": data.job_package_summary.to_dict(orient="records") if not data.job_package_summary.empty else [],
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

ROOF_COATING_BASELINE_LABOR_TASKS = (
    "labor_prep",
    "labor_seam_sealer",
    "labor_base",
    "labor_top_coat",
    "labor_details",
    "labor_cleanup",
    "labor_loading",
)

ROOF_COATING_FALLBACK_HOURS_PER_1000 = {
    "labor_prep": 3.0,
    "labor_seam_sealer": 3.0,
    "labor_base": 3.0,
    "labor_top_coat": 3.0,
    "labor_details": 2.0,
    "labor_caulk": 2.0,
    "labor_cleanup": 1.0,
    "labor_loading": 0.35,
    "labor_prime": 2.5,
    "infrared_scan": 1.0,
    "labor_top_coat_granules": 2.0,
}

ROOF_COATING_LABOR_BUCKET_ROLES = {
    "labor_prep": "core_bundle",
    "labor_seam_sealer": "core_detail_bundle",
    "labor_base": "coating_application_bundle",
    "labor_top_coat": "coating_application_bundle",
    "labor_details": "core_detail_bundle",
    "labor_caulk": "core_detail_bundle",
    "labor_cleanup": "core_bundle",
    "labor_loading": "core_bundle",
    "labor_prime": "trigger_only",
    "infrared_scan": "trigger_only",
    "labor_top_coat_granules": "trigger_only",
}

ROOF_COATING_LABOR_BUCKET_HOURS_PER_1000_CAP = {
    "labor_prep": 12.0,
    "labor_seam_sealer": 14.0,
    "labor_base": 18.0,
    "labor_top_coat": 18.0,
    "labor_details": 10.0,
    "labor_caulk": 8.0,
    "labor_cleanup": 6.0,
    "labor_loading": 2.0,
    "labor_prime": 10.0,
    "infrared_scan": 2.0,
    "labor_top_coat_granules": 4.0,
}

ROOF_COATING_LABOR_GROUPS = {
    "coating_application_bundle": {
        "tasks": {"labor_base", "labor_top_coat"},
        "cap_hours_per_1000": 29.5,
        "reason": "Base/top-coat labor treated as a bounded coating application bundle.",
    },
    "core_detail_bundle": {
        "tasks": {"labor_seam_sealer", "labor_details", "labor_caulk"},
        "cap_hours_per_1000": 19.5,
        "reason": "Seam/detail/caulk labor treated as overlapping detail work and capped as a bundle.",
    },
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


def is_roof_coating_scope(scope: dict[str, Any]) -> bool:
    notes = first_nonblank(scope.get("notes")).lower()
    project_type = first_nonblank(scope.get("project_type")).lower()
    substrate = first_nonblank(scope.get("substrate")).lower()
    coating_type = first_nonblank(scope.get("coating_type")).lower()
    coating_required = bool(scope.get("coating_required") or coating_type or "coating" in notes or "coat" in notes)
    roof_context = "roof" in project_type or "roof" in notes or substrate in {"metal", "tpo", "epdm", "modified bitumen"}
    return coating_required and roof_context


INSULATION_SCOPE_SIGNALS = (
    "insulation",
    "spray foam",
    "open-cell",
    "open cell",
    "closed-cell",
    "closed cell",
    "dc315",
    "thermal barrier",
    "crawlspace",
    "crawl space",
    "attic",
)

INSULATION_SOURCE_SIGNALS = INSULATION_SCOPE_SIGNALS + ("wall",)

ROOFING_SCOPE_SIGNALS = (
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


def _normalized_text(value: Any) -> str:
    return first_nonblank(value).strip().lower().replace("_", " ").replace("-", " ")


def scope_template_type(scope: dict[str, Any]) -> str:
    text = _scope_text(scope)
    project_type = _normalized_text(scope.get("project_type"))
    explicit_template = _normalized_text(scope.get("template_type"))
    explicit_division = _normalized_text(scope.get("division"))
    if explicit_template == "insulation" or explicit_division == "insulation":
        return "insulation"
    strong_insulation_signal = any(
        term in text or term in project_type
        for term in (
            "spray foam",
            "foam sprayed",
            "sprayed foam",
            "wall insulation",
            "outside walls",
            "walls and ceiling",
            "dc315",
            "thermal barrier",
            "crawlspace",
            "crawl space",
            "attic insulation",
        )
    )
    if is_roof_coating_scope(scope) and not strong_insulation_signal and not bool(scope.get("foam_required") or scope.get("foam_thickness_inches")):
        return "roofing"
    if any(term in text or term in project_type for term in INSULATION_SCOPE_SIGNALS) or bool(scope.get("foam_required") or scope.get("foam_thickness_inches")):
        return "insulation"
    if is_roof_coating_scope(scope) or any(term in text or term in project_type for term in ROOFING_SCOPE_SIGNALS):
        return "roofing"
    return _normalized_text(scope.get("template_type"))


def evidence_template_type(row: dict[str, Any]) -> str:
    value = first_nonblank(row.get("template_type"), row.get("job_template_type"), row.get("template_name"))
    text = _normalized_text(value)
    if text in {"roof", "roofing", "roof coating"}:
        return "roofing"
    if text in {"insulation", "foam", "spray foam"}:
        return "insulation"
    if text in {"unknown", "none", "null"}:
        return ""
    return text


def evidence_source_text(row: dict[str, Any]) -> str:
    return " ".join(
        first_nonblank(row.get(column)).lower()
        for column in (
            "source_file",
            "folder_path",
            "relative_path",
            "job_name",
            "customer",
            "estimate_file",
            "document_name",
        )
        if first_nonblank(row.get(column))
    )


def evidence_has_insulation_source_signal(row: dict[str, Any]) -> bool:
    text = evidence_source_text(row)
    return any(term in text for term in INSULATION_SOURCE_SIGNALS)


def evidence_has_strong_roofing_signal(row: dict[str, Any]) -> bool:
    text = " ".join([evidence_source_text(row), row_text_for_scope(row)])
    return any(term in text for term in ROOFING_SCOPE_SIGNALS)


def row_text_for_scope(row: dict[str, Any]) -> str:
    return " ".join(
        first_nonblank(row.get(column)).lower()
        for column in (
            "template_bucket",
            "row_label",
            "selected_item_name",
            "item_name",
            "line_item_name",
            "description",
            "category",
            "notes",
            "job_project_type",
            "project_type",
            "job_division",
            "division",
        )
        if first_nonblank(row.get(column))
    )


def evidence_allowed_for_scope(row: dict[str, Any], scope: dict[str, Any], *, allow_unknown_with_roofing_signal: bool = True) -> tuple[bool, str]:
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


def required_roof_coating_labor_tasks(scope: dict[str, Any], decision: dict[str, Any]) -> list[str]:
    if not is_roof_coating_scope(scope):
        return []
    notes = first_nonblank(scope.get("notes")).lower()
    tasks = list(ROOF_COATING_BASELINE_LABOR_TASKS)
    work_packages = ensure_work_package_decisions(scope, decision)
    if _decision_applies(work_packages.get("primer"), include_review=False):
        tasks.append("labor_prime")
    if _decision_applies(work_packages.get("caulk_detail"), include_review=True) and _roof_coating_heavy_labor_trigger(scope):
        tasks.append("labor_caulk")
    if _text_has_any(notes, OPTIONAL_LABOR_BUCKET_TRIGGERS["infrared_scan"]):
        tasks.append("infrared_scan")
    if _text_has_any(notes, OPTIONAL_LABOR_BUCKET_TRIGGERS["labor_top_coat_granules"]):
        tasks.append("labor_top_coat_granules")
    return list(dict.fromkeys(tasks))


def _roof_labor_task_role(task: str) -> str:
    return ROOF_COATING_LABOR_BUCKET_ROLES.get(task, "excluded")


def _labor_row_hours(row: dict[str, Any]) -> float | None:
    return optional_positive_float(first_nonblank(row.get("median_total_hours"), row.get("total_hours"), row.get("labor_hours")))


def _labor_row_cost(row: dict[str, Any]) -> float | None:
    return optional_positive_float(first_nonblank(row.get("median_estimated_cost"), row.get("median_cost"), row.get("estimated_cost")))


def _labor_hours_per_1000(row: dict[str, Any], area: float) -> float | None:
    direct = optional_positive_float(first_nonblank(row.get("median_hours_per_1000_sqft"), row.get("hours_per_1000_sqft")))
    if direct is not None:
        return direct
    hours = _labor_row_hours(row)
    if hours is None or area <= 0:
        return None
    return hours / area * 1000


def _scale_labor_row_hours(row: dict[str, Any], new_hours: float, reason: str) -> dict[str, Any]:
    row = dict(row)
    old_hours = _labor_row_hours(row) or 0.0
    if old_hours <= 0 or new_hours >= old_hours:
        return row
    ratio = new_hours / old_hours
    old_cost = _labor_row_cost(row)
    crew_size = sane_crew_size(row.get("median_crew_size"), 4, max_size=8)
    row["original_median_total_hours"] = round(old_hours, 2)
    row["median_total_hours"] = round(new_hours, 2)
    row["capped_hours"] = round(new_hours, 2)
    row["labor_selection_capped"] = True
    row["labor_selection_cap_reason"] = reason
    row["median_days"] = round(new_hours / max(crew_size * 8, 1), 3)
    if old_cost is not None:
        row["original_median_estimated_cost"] = round(old_cost, 2)
        row["median_estimated_cost"] = round(old_cost * ratio, 2)
    notes = first_nonblank(row.get("notes"))
    cap_note = f"Labor evidence capped: {reason}"
    row["notes"] = f"{notes} {cap_note}".strip() if notes else cap_note
    return row


def _roof_labor_trigger_allowed(task: str, scope: dict[str, Any], decision: dict[str, Any]) -> tuple[bool, str]:
    notes = first_nonblank(scope.get("notes")).lower()
    work_packages = ensure_work_package_decisions(scope, decision)
    if task == "labor_prime" and not _decision_applies(work_packages.get("primer"), include_review=False):
        return False, "Primer labor excluded because primer package is not triggered."
    if task == "infrared_scan" and not _text_has_any(notes, OPTIONAL_LABOR_BUCKET_TRIGGERS["infrared_scan"]):
        return False, "Infrared scan excluded because notes did not request IR/thermal/moisture scan."
    if task == "labor_top_coat_granules" and not _text_has_any(notes, OPTIONAL_LABOR_BUCKET_TRIGGERS["labor_top_coat_granules"]):
        return False, "Granules labor excluded because notes did not request granules/broadcast."
    return True, ""


def _labor_selection_audit_row(
    row: dict[str, Any],
    *,
    task: str,
    selected: bool,
    reason: str,
    area: float,
    role: str | None = None,
) -> dict[str, Any]:
    hours = _labor_row_hours(row)
    hours_per_1000 = _labor_hours_per_1000(row, area)
    return {
        "task": task,
        "selected": selected,
        "labor_bucket_role": role or _roof_labor_task_role(task),
        "reason": reason,
        "evidence_count": safe_int(row.get("evidence_count"), 0),
        "median_hours_per_1000_sqft": round(hours_per_1000, 2) if hours_per_1000 is not None else None,
        "median_total_hours": round(hours, 2) if hours is not None else None,
        "capped_hours": row.get("capped_hours"),
        "calibration_method": row.get("calibration_method"),
        "selection_level": row.get("selection_level"),
    }


def select_roof_coating_labor_rows(
    rows: list[dict[str, Any]],
    scope: dict[str, Any],
    decision: dict[str, Any],
    *,
    area: float,
    multiplier: float,
    expected_tasks: list[str],
    all_candidate_rows: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Select and cap calibrated roof-coating labor buckets before cost rollup."""
    if not is_roof_coating_scope(scope):
        return rows, []
    expected = set(expected_tasks)
    effective_multiplier = max(multiplier, 0.1)
    selected_rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    selected_tasks: set[str] = set()
    considered_candidate_tasks: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        task = _task_name_from_row(row)
        considered_candidate_tasks.add(task)
        role = _roof_labor_task_role(task)
        if task not in expected:
            audit_rows.append(
                _labor_selection_audit_row(
                    row,
                    task=task,
                    selected=False,
                    reason="Bucket is not part of the selected roof coating labor scope.",
                    area=area,
                    role="excluded",
                )
            )
            continue
        trigger_allowed, trigger_reason = _roof_labor_trigger_allowed(task, scope, decision)
        if not trigger_allowed:
            audit_rows.append(_labor_selection_audit_row(row, task=task, selected=False, reason=trigger_reason, area=area, role=role))
            continue
        hours = _labor_row_hours(row)
        if hours is None:
            audit_rows.append(
                _labor_selection_audit_row(
                    row,
                    task=task,
                    selected=False,
                    reason="Bucket rejected because calibrated labor hours are missing.",
                    area=area,
                    role=role,
                )
            )
            continue
        capped_row = dict(row)
        cap_per_1000 = ROOF_COATING_LABOR_BUCKET_HOURS_PER_1000_CAP.get(task)
        if cap_per_1000 and area > 0:
            cap_hours = (cap_per_1000 * area / 1000) / effective_multiplier
            if hours > cap_hours:
                capped_row = _scale_labor_row_hours(
                    capped_row,
                    cap_hours,
                    f"{task} final adjusted hours capped at {cap_per_1000:g} hours per 1000 sqft.",
                )
        capped_row["labor_bucket_role"] = role
        capped_row["labor_selection_status"] = "selected"
        capped_row["labor_selection_reason"] = "Selected for roof coating labor bundle."
        selected_rows.append(capped_row)
        selected_tasks.add(task)

    for group_name, group in ROOF_COATING_LABOR_GROUPS.items():
        group_tasks = set(group["tasks"])
        group_rows = [(index, row) for index, row in enumerate(selected_rows) if _task_name_from_row(row) in group_tasks]
        if not group_rows or area <= 0:
            continue
        total_group_hours = sum(_labor_row_hours(row) or 0.0 for _, row in group_rows)
        cap_hours = (float(group["cap_hours_per_1000"]) * area / 1000) / effective_multiplier
        if total_group_hours <= cap_hours or cap_hours <= 0:
            continue
        scale = cap_hours / total_group_hours
        for index, row in group_rows:
            current_hours = _labor_row_hours(row) or 0.0
            scaled = _scale_labor_row_hours(row, current_hours * scale, str(group["reason"]))
            scaled["labor_bucket_role"] = group_name
            scaled["labor_selection_status"] = "selected_capped"
            scaled["labor_selection_reason"] = str(group["reason"])
            selected_rows[index] = scaled

    selected_by_task = {_task_name_from_row(row): row for row in selected_rows}
    for row in selected_rows:
        task = _task_name_from_row(row)
        status = "selected_capped" if row.get("labor_selection_capped") else "selected"
        reason = first_nonblank(row.get("labor_selection_reason"), "Selected for roof coating labor bundle.")
        audit = _labor_selection_audit_row(row, task=task, selected=True, reason=reason, area=area, role=row.get("labor_bucket_role"))
        audit["selection_status"] = status
        audit_rows.append(audit)

    for row in all_candidate_rows or []:
        if not isinstance(row, dict):
            continue
        task = _task_name_from_row(row)
        if task in selected_tasks or task in considered_candidate_tasks:
            continue
        role = _roof_labor_task_role(task)
        trigger_allowed, trigger_reason = _roof_labor_trigger_allowed(task, scope, decision)
        reason = trigger_reason
        if not reason:
            if task not in expected:
                reason = "Bucket was available historically but is outside the selected roof coating bundle."
            elif task not in selected_by_task:
                reason = "Bucket was expected but no selected valid historical row was available."
        audit_rows.append(_labor_selection_audit_row(row, task=task, selected=False, reason=reason, area=area, role=role))
    return selected_rows, audit_rows


def _roof_coating_heavy_labor_trigger(scope: dict[str, Any]) -> bool:
    text = _scope_text(scope)
    access = first_nonblank(scope.get("access_complexity")).lower()
    penetrations = first_nonblank(scope.get("penetrations_complexity")).lower()
    few_penetrations = "few penetration" in text or "few penetrations" in text
    penetration_heavy = penetrations in {"medium", "high"} and not few_penetrations
    return (
        access in {"hard", "difficult", "high"}
        or penetration_heavy
        or any(
            term in text
            for term in (
                "poor condition",
                "active leaks",
                "many penetrations",
                "lots of penetrations",
                "heavy rust",
                "severe rust",
                "tear off",
                "tear-off",
                "wet insulation",
                "granules",
                "broadcast",
                "infrared",
                "ir scan",
            )
        )
    )


def _clean_roof_coating_labor_scope(scope: dict[str, Any]) -> bool:
    text = _scope_text(scope)
    condition = first_nonblank(scope.get("roof_condition")).lower()
    access = first_nonblank(scope.get("access_complexity")).lower()
    penetrations = first_nonblank(scope.get("penetrations_complexity")).lower()
    flags = {str(flag).lower() for flag in scope.get("condition_detail_flags") or []}
    no_visible_rust = bool(re.search(r"\b(?:no|without)\s+(?:visible\s+)?rust\b|\bno\s+rusted\s+fasteners?\b", text))
    clean_condition = condition in {"excellent", "good"} or ("excellent condition" in text and no_visible_rust)
    low_access = access in {"", "low", "easy"}
    low_detail = penetrations in {"", "low"} or "few penetrations" in text
    no_rust_flags = not any("rust" in flag for flag in flags) and no_visible_rust
    maintenance = any(term in text for term in ("maintenance coating", "minor dirt", "extend the life", "five-year-old", "5-year-old"))
    return bool(is_roof_coating_scope(scope) and clean_condition and low_access and low_detail and no_rust_flags and maintenance)


def roof_coating_labor_bundle_cap_per_1000(scope: dict[str, Any]) -> float:
    if not is_roof_coating_scope(scope):
        return 0.0
    if _clean_roof_coating_labor_scope(scope):
        return 40.0
    return 80.0 if _roof_coating_heavy_labor_trigger(scope) else 60.0


def apply_roof_coating_labor_bundle_cap(
    plan: list[dict[str, Any]],
    *,
    total_hours: float,
    total_cost: float,
    area: float,
    scope: dict[str, Any],
    calibration: dict[str, Any],
) -> tuple[list[dict[str, Any]], float, float]:
    cap_per_1000 = roof_coating_labor_bundle_cap_per_1000(scope)
    if not cap_per_1000 or area <= 0 or total_hours <= 0:
        return plan, total_hours, total_cost
    cap_hours = cap_per_1000 * area / 1000
    diagnostics = calibration.get("labor_calibration_diagnostics")
    if isinstance(diagnostics, dict):
        diagnostics.setdefault("selection_summary", {})
        diagnostics["selection_summary"]["labor_bundle_before_cap_hours"] = round(total_hours, 2)
        diagnostics["selection_summary"]["labor_bundle_cap_hours"] = round(cap_hours, 2)
        diagnostics["selection_summary"]["labor_bundle_cap_hours_per_1000_sqft"] = cap_per_1000
        diagnostics["selection_summary"]["labor_bundle_summary"] = {
            "surface_prep_bundle": ["labor_prep"],
            "coating_application_bundle": ["labor_base", "labor_top_coat"],
            "detail_treatment_bundle": ["labor_seam_sealer", "labor_details", "labor_caulk"],
            "mobilization_cleanup_bundle": ["labor_cleanup", "labor_loading"],
            "optional_primer_bundle": ["labor_prime"],
        }
        diagnostics["selection_summary"]["labor_cap_applied"] = total_hours > cap_hours
        diagnostics["selection_summary"]["labor_overlap_adjustment"] = "detail and coating bundles are capped before total bundle cap"
        diagnostics["selection_summary"]["clean_maintenance_labor_scope"] = _clean_roof_coating_labor_scope(scope)
    if total_hours <= cap_hours:
        if isinstance(diagnostics, dict):
            diagnostics["selection_summary"]["labor_bundle_after_cap_hours"] = round(total_hours, 2)
            diagnostics["selection_summary"]["final_labor_hours_per_1000_sqft"] = round(total_hours / area * 1000, 2)
        return plan, total_hours, total_cost
    # Leave a little headroom so rounded per-row hours cannot sum back above the cap.
    target_cap_hours = max(0.0, cap_hours * 0.999)
    scale = target_cap_hours / total_hours
    capped_plan: list[dict[str, Any]] = []
    for row in plan:
        row = dict(row)
        old_hours = safe_float(row.get("total_hours"), 0.0)
        old_cost = safe_float(row.get("estimated_cost"), 0.0)
        new_hours = old_hours * scale
        new_cost = old_cost * scale
        row["original_total_hours_before_bundle_cap"] = round(old_hours, 2)
        row["original_estimated_cost_before_bundle_cap"] = round(old_cost, 2)
        row["total_hours"] = round(new_hours, 1)
        row["labor_hours"] = round(new_hours, 1)
        row["estimated_cost"] = round(new_cost, 2)
        crew_size = max(safe_int(row.get("crew_size"), 4), 1)
        days = new_hours / max(crew_size * 8, 1)
        row["adjusted_days"] = round(days, 2)
        row["crew_days"] = round(days, 2)
        row["labor_selection_status"] = "selected_bundle_capped"
        reason = f"Roof coating labor bundle capped at {cap_per_1000:g} hours per 1000 sqft for this scope."
        row["labor_selection_reason"] = f"{first_nonblank(row.get('labor_selection_reason'))} {reason}".strip()
        row["capped_hours"] = round(new_hours, 2)
        capped_plan.append(row)
    capped_total_hours = sum(safe_float(row.get("total_hours"), 0.0) for row in capped_plan)
    capped_total_cost = sum(safe_float(row.get("estimated_cost"), 0.0) for row in capped_plan)
    if isinstance(diagnostics, dict):
        diagnostics["selection_summary"]["labor_bundle_after_cap_hours"] = round(capped_total_hours, 2)
        diagnostics["selection_summary"]["labor_bundle_cap_scale"] = round(scale, 4)
        diagnostics["selection_summary"]["labor_bundle_target_cap_hours_after_rounding_headroom"] = round(target_cap_hours, 2)
        diagnostics["selection_summary"]["final_labor_hours_per_1000_sqft"] = round(capped_total_hours / area * 1000, 2)
        diagnostics.setdefault("selection_rows", []).append(
            {
                "task": "TOTAL_LABOR_BUNDLE",
                "selected": True,
                "labor_bucket_role": "roof_coating_bundle_cap",
                "reason": f"Reduced overlapping roof coating labor bundle to {cap_per_1000:g} hours per 1000 sqft.",
                "evidence_count": "",
                "median_hours_per_1000_sqft": round(total_hours / area * 1000, 2),
                "median_total_hours": round(total_hours, 2),
                "capped_hours": round(capped_total_hours, 2),
                "calibration_method": "bundle_cap",
                "selection_level": "roof_coating_bundle_cap",
                "selection_status": "selected_bundle_capped",
            }
        )
    return capped_plan, capped_total_hours, capped_total_cost


def labor_sanity_review_flags(
    scope: dict[str, Any],
    material_plan: list[dict[str, Any]],
    labor_plan: list[dict[str, Any]],
) -> list[str]:
    flags: list[str] = []
    area = optional_positive_float(scope.get("estimated_sqft")) or optional_positive_float(scope.get("surface_area_sqft"))
    net_area = _dimension_summary_value(scope.get("dimension_summary") or {}, "net_area_sqft")
    net_area_number = optional_positive_float(net_area)
    if area and net_area_number and abs(area - net_area_number) > max(1.0, net_area_number * 0.01):
        flags.append("Labor area does not equal parsed net area; verify stale scope or override.")
    total_hours = sum(safe_float(row.get("total_hours"), 0.0) for row in labor_plan)
    hours_per_1000 = total_hours / area * 1000 if area else None
    if _clean_roof_coating_labor_scope(scope) and hours_per_1000 and hours_per_1000 > 40:
        flags.append("Excellent/easy/low-penetration coating labor exceeds clean maintenance cap; verify labor calibration.")
    low_penetration = first_nonblank(scope.get("penetrations_complexity")).lower() in {"", "low"}
    detail_tasks = {"labor_seam_sealer", "labor_details", "labor_caulk"}
    coating_tasks = {"labor_base", "labor_top_coat"}
    detail_hours = sum(safe_float(row.get("total_hours"), 0.0) for row in labor_plan if first_nonblank(row.get("task")) in detail_tasks)
    coating_hours = sum(safe_float(row.get("total_hours"), 0.0) for row in labor_plan if first_nonblank(row.get("task")) in coating_tasks)
    if low_penetration and coating_hours and detail_hours > coating_hours:
        flags.append("Detail labor exceeds coating application labor on a low-penetration roof; verify detail bundle.")
    has_primer_labor = any(
        first_nonblank(row.get("task")) == "labor_prime" and row.get("included_in_total") is not False
        for row in labor_plan
    )
    primer_material_active = _primer_material_included_in_base_total(material_plan)
    if has_primer_labor and not primer_material_active:
        flags.append("Primer labor is included while primer material is excluded from the base estimate.")
    return flags


def _fallback_hours_for_task(task: str, area: float, scope: dict[str, Any]) -> float:
    area_factor = max(area, 0.0) / 1000 if area else 0.0
    notes = first_nonblank(scope.get("notes")).lower()
    rate = ROOF_COATING_FALLBACK_HOURS_PER_1000.get(task, 1.5)
    if task == "labor_seam_sealer" and any(term in notes for term in ("open seam", "open seams", "seams opening", "opening up")):
        rate = 5.0
    if task in {"labor_details", "labor_caulk"} and any(term in notes for term in ("many penetration", "lots of penetration", "curb", "drain", "hvac", "rtu")):
        rate = 3.0
    if task == "labor_loading":
        return max(2.0, min(6.0, area_factor * rate))
    if task == "infrared_scan":
        return max(4.0, area_factor * rate)
    return max(2.0, area_factor * rate)


def _fallback_labor_row(
    *,
    task: str,
    scope: dict[str, Any],
    decision: dict[str, Any],
    assumptions: EstimatorAssumptions,
    crew_size: int,
    multiplier: float,
    production_rate: float,
) -> dict[str, Any]:
    area = safe_float(scope.get("surface_area_sqft"), 0.0)
    hours = _fallback_hours_for_task(task, area, scope) * max(multiplier, 0.1)
    row_crew_size = sane_crew_size(crew_size, 4, max_size=8)
    crew_days = hours / max(row_crew_size * 8, 1)
    estimated_cost = hours * assumptions.blended_hourly_rate
    work_packages = ensure_work_package_decisions(scope, decision)
    labor_package = _labor_package_for_bucket(task)
    package_decision = work_packages.get(labor_package)
    return _labor_row_with_package_context(
        {
            "task": task,
            "base_days": round(crew_days, 2),
            "adjusted_days": round(crew_days, 2),
            "crew_size": row_crew_size,
            "total_hours": round(hours, 1),
            "estimated_cost": round(estimated_cost, 2),
            "evidence_count": 0,
            "needs_review": True,
            "calibration_method": "rule_based_fallback",
            "notes": "Fallback labor assumption; estimator should verify.",
        },
        package_decision,
        production_rate=production_rate,
        evidence_count=0,
        source_type="rule_based_fallback",
    )


LABOR_PACKAGE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "labor_prep": ("labor_prep", "prep", "preparation", "pressure wash", "power wash", "powerwash", "clean substrate", "substrate prep"),
    "labor_prime": ("labor_prime", "prime", "primer"),
    "labor_seam_sealer": ("labor_seam_sealer", "seam", "seams", "seam sealer", "seam treatment", "butter grade", "laps"),
    "labor_base": ("labor_base", "base coat", "base", "first coat", "coating base"),
    "labor_top_coat": ("labor_top_coat", "top coat", "topcoat", "finish coat", "second coat"),
    "labor_details": ("labor_details", "details", "detail work", "flashing", "penetrations", "curbs", "skylights", "rtu", "hvac"),
    "labor_caulk": ("labor_caulk", "caulk", "sealant", "aldo 399"),
    "labor_cleanup": ("labor_cleanup", "clean up", "cleanup", "job clean", "final clean", "touch/cleanup", "touch up"),
    "labor_loading": ("labor_loading", "loading", "load", "mobilization setup"),
    "labor_traveling": ("labor_traveling", "traveling", "travel"),
    "infrared_scan": ("infrared_scan", "infrared", "ir scan", "moisture scan", "thermal scan"),
    "labor_top_coat_granules": ("labor_top_coat_granules", "granules", "granule", "broadcast"),
}

LABOR_ROW_NUMBER_MAP = {
    116: "labor_prep",
    118: "labor_prime",
    120: "labor_seam_sealer",
    122: "labor_base",
    124: "labor_top_coat",
    126: "labor_caulk",
    128: "labor_details",
    132: "labor_cleanup",
    136: "labor_loading",
    138: "labor_traveling",
    141: "infrared_scan",
    130: "labor_top_coat_granules",
}


def _canonical_labor_task_name(value: Any) -> str:
    key = first_nonblank(value).strip().lower().replace("-", "_").replace(" ", "_")
    if key in LABOR_BUCKET_TO_PACKAGE or key in LABOR_PACKAGE_KEYWORDS:
        return key
    compact = key.replace("_", " ")
    for task, keywords in LABOR_PACKAGE_KEYWORDS.items():
        if any(keyword in compact or keyword.replace(" ", "_") in key for keyword in keywords):
            return task
    return key


def _task_name_from_row(row: dict[str, Any]) -> str:
    explicit = first_nonblank(row.get("template_bucket"), row.get("labor_package"), row.get("package"), row.get("task")).strip()
    canonical = _canonical_labor_task_name(explicit)
    if canonical in LABOR_PACKAGE_KEYWORDS or canonical in LABOR_BUCKET_TO_PACKAGE:
        return canonical
    row_number = optional_positive_int(row.get("row_number"))
    if row_number in LABOR_ROW_NUMBER_MAP:
        return LABOR_ROW_NUMBER_MAP[row_number]
    text = " ".join(
        str(row.get(column) or "")
        for column in (
            "row_label",
            "selected_item_name",
            "item_name",
            "line_item_name",
            "description",
            "category",
            "notes",
        )
    ).lower()
    for task, keywords in LABOR_PACKAGE_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return task
    return canonical


def _normal_context(value: Any) -> str:
    return first_nonblank(value).strip().lower().replace("_", " ").replace("-", " ")


def _scope_context(scope: dict[str, Any]) -> dict[str, str]:
    project_type = _normal_context(scope.get("project_type"))
    substrate = _normal_context(scope.get("substrate"))
    template_type = _normal_context(scope_template_type(scope))
    warranty = first_nonblank(scope.get("warranty_target"), scope.get("warranty_target_years"))
    return {
        "template_type": template_type,
        "project_type": project_type,
        "substrate": substrate,
        "warranty_years": str(int(float(warranty))) if is_finite_number(warranty) else "",
    }


def _row_context(row: dict[str, Any]) -> dict[str, str]:
    return {
        "template_type": _normal_context(first_nonblank(row.get("template_type"), row.get("job_template_type"), row.get("template_name"))),
        "project_type": _normal_context(first_nonblank(row.get("project_type"), row.get("job_project_type"), row.get("job_type"))),
        "substrate": _normal_context(first_nonblank(row.get("substrate"), row.get("job_substrate"))),
        "warranty_years": str(int(float(first_nonblank(row.get("warranty_years"), row.get("job_warranty_years")))))
        if is_finite_number(first_nonblank(row.get("warranty_years"), row.get("job_warranty_years")))
        else "",
        "division": _normal_context(first_nonblank(row.get("division"), row.get("job_division"))),
    }


def _context_matches(row: dict[str, Any], scope_context: dict[str, str], fields: tuple[str, ...]) -> bool:
    context = _row_context(row)
    for field in fields:
        expected = scope_context.get(field)
        if not expected:
            continue
        actual = context.get(field)
        if not actual:
            continue
        if expected not in actual and actual not in expected:
            return False
    return True


def _template_row_valid_for_labor(row: dict[str, Any]) -> tuple[bool, str]:
    hours = optional_positive_float(row.get("total_hours"))
    days = optional_positive_float(row.get("days"))
    crew = optional_positive_float(row.get("crew_size"))
    cost = optional_positive_float(row.get("estimated_cost"))
    sqft = optional_positive_float(first_nonblank(row.get("historical_sqft"), row.get("area_sqft"), row.get("job_area_sqft")))
    if hours is None:
        return False, "missing or non-positive total_hours"
    if crew is not None and crew > 8:
        return False, f"crew_size {crew:g} exceeds automatic range"
    if hours <= 0:
        return False, "total_hours <= 0"
    if sqft is not None and hours / sqft > 0.2:
        return False, "hours_per_sqft is outside sane range"
    if cost is not None and cost < 0:
        return False, "estimated_cost is negative"
    if days is not None and days < 0:
        return False, "days is negative"
    return True, ""


def _relationship_row_valid_for_labor(row: dict[str, Any]) -> tuple[bool, str]:
    hours_per_1000 = optional_positive_float(first_nonblank(row.get("median_hours_per_1000_sqft"), row.get("hours_per_1000_sqft")))
    hours_per_sqft = optional_positive_float(row.get("hours_per_sqft"))
    if hours_per_1000 is None and hours_per_sqft is None:
        return False, "missing hours_per_1000_sqft/hours_per_sqft"
    if hours_per_sqft is not None and hours_per_sqft > 0.2:
        return False, "hours_per_sqft is outside sane range"
    if hours_per_1000 is not None and hours_per_1000 > 200:
        return False, "hours_per_1000_sqft is outside sane range"
    return True, ""


def _candidate_rows_for_task(rows: list[Any], task: str) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if _task_name_from_row(row) == task:
            out.append(row)
    return out


def _select_rows_by_relaxation(candidates: list[dict[str, Any]], scope: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    if not candidates:
        return [], "no_candidates"
    scope_context = _scope_context(scope)
    levels: list[tuple[str, tuple[str, ...]]] = [
        ("exact_template_project_substrate_warranty", ("template_type", "project_type", "substrate", "warranty_years")),
        ("relaxed_warranty", ("template_type", "project_type", "substrate")),
        ("relaxed_project", ("template_type", "substrate")),
        ("all_roofing_template_bucket", ("template_type",)),
    ]
    for level, fields in levels:
        selected = [row for row in candidates if _context_matches(row, scope_context, fields)]
        if selected:
            return selected, level
    return candidates, "all_bucket_rows"


def _median(values: list[float]) -> float | None:
    values = sorted(value for value in values if value is not None and math.isfinite(value))
    if not values:
        return None
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2


def _labor_evidence_row_from_template(
    task: str,
    selected_rows: list[dict[str, Any]],
    *,
    area: float,
    multiplier: float,
    default_crew_size: int,
    selection_level: str,
) -> dict[str, Any]:
    hours_values: list[float] = []
    days_values: list[float] = []
    crew_values: list[float] = []
    for row in selected_rows:
        hours = optional_positive_float(row.get("total_hours"))
        sqft = optional_positive_float(first_nonblank(row.get("historical_sqft"), row.get("area_sqft"), row.get("job_area_sqft")))
        if hours is not None:
            if sqft and area:
                hours_values.append((hours / sqft) * area)
            else:
                hours_values.append(hours)
        days = optional_positive_float(row.get("days"))
        if days is not None:
            days_values.append(days)
        crew = optional_positive_float(row.get("crew_size"))
        if crew is not None and 0 < crew <= 8:
            crew_values.append(crew)
    hours = (_median(hours_values) or 0.0) * multiplier
    crew_size = int(round(_median(crew_values) or default_crew_size))
    crew_size = sane_crew_size(crew_size, default_crew_size, max_size=8)
    days = hours / max(crew_size * 8, 1) if hours else (_median(days_values) or 1.0)
    note = "Calibrated from estimate_template_rows."
    method = "historical_calibration"
    if selection_level not in {"exact_template_project_substrate_warranty", "relaxed_warranty"}:
        note = "Calibrated from relaxed historical roofing labor evidence."
        method = "relaxed_historical_calibration"
    return {
        "template_bucket": task,
        "median_days": days,
        "median_crew_size": crew_size,
        "median_total_hours": hours,
        "median_estimated_cost": hours * 72.0,
        "evidence_count": len(selected_rows),
        "calibration_method": method,
        "selection_level": selection_level,
        "notes": note,
    }


def _labor_evidence_row_from_relationship(
    task: str,
    selected_rows: list[dict[str, Any]],
    *,
    area: float,
    default_crew_size: int,
    selection_level: str,
) -> dict[str, Any]:
    hours_values: list[float] = []
    cost_values: list[float] = []
    crew_values: list[float] = []
    for row in selected_rows:
        hours_per_1000 = optional_positive_float(first_nonblank(row.get("median_hours_per_1000_sqft"), row.get("hours_per_1000_sqft")))
        hours_per_sqft = optional_positive_float(row.get("hours_per_sqft"))
        cost_per_sqft = optional_positive_float(first_nonblank(row.get("median_cost_per_sqft"), row.get("cost_per_sqft")))
        if hours_per_1000 is not None and area:
            hours_values.append(hours_per_1000 * area / 1000)
        elif hours_per_sqft is not None and area:
            hours_values.append(hours_per_sqft * area)
        if cost_per_sqft is not None and area:
            cost_values.append(cost_per_sqft * area)
        crew = optional_positive_float(first_nonblank(row.get("median_crew_size"), row.get("crew_size")))
        if crew is not None and 0 < crew <= 8:
            crew_values.append(crew)
    hours = _median(hours_values) or 0.0
    crew_size = sane_crew_size(round(_median(crew_values) or default_crew_size), default_crew_size, max_size=8)
    return {
        "template_bucket": task,
        "median_days": hours / max(crew_size * 8, 1) if hours else 1.0,
        "median_crew_size": crew_size,
        "median_total_hours": hours,
        "median_estimated_cost": _median(cost_values) or hours * 72.0,
        "evidence_count": int(sum(safe_int(row.get("job_count"), 0) or safe_int(row.get("evidence_count"), 0) or 1 for row in selected_rows)),
        "calibration_method": "relationship_labor_rates",
        "selection_level": selection_level,
        "notes": "Calibrated from relationship_labor_rates.",
    }


def build_labor_calibration_diagnostics(
    calibration: dict[str, Any],
    scope: dict[str, Any],
    expected_tasks: list[str],
) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {
        "source_priority": ["relationship_labor_rates", "job_package_summary", "estimate_template_rows", "rule_based_fallback"],
        "scope_template_type": scope_template_type(scope),
        "tasks": {},
    }
    template_rows = [row for row in calibration.get("all_labor_rows") or [] if isinstance(row, dict)]
    relationship_rows = [row for row in calibration.get("relationship_labor_rates") or [] if isinstance(row, dict)]
    package_rows = [row for row in calibration.get("job_package_summary") or [] if isinstance(row, dict)]
    for task in expected_tasks:
        task_diag = {
            "candidate_historical_rows": len(_candidate_rows_for_task(template_rows, task)),
            "candidate_relationship_rows": len(_candidate_rows_for_task(relationship_rows, task)),
            "candidate_package_rows": len(_candidate_rows_for_task(package_rows, task)),
            "after_project_template_filter": 0,
            "after_area_filter": 0,
            "after_numeric_validation": 0,
            "selected_source": None,
            "selected_calibration_rows": 0,
            "selection_level": None,
            "rejected_rows": [],
        }
        for row in _candidate_rows_for_task(template_rows, task):
            allowed, rejected_reason = evidence_allowed_for_scope(row, scope)
            if not allowed:
                if len(task_diag["rejected_rows"]) < 10:
                    task_diag["rejected_rows"].append(
                        {
                            "source": "estimate_template_rows",
                            "reason": rejected_reason,
                            "scope_template_type": scope_template_type(scope),
                            "evidence_template_type": evidence_template_type(row),
                        }
                    )
                continue
            valid, reason = _template_row_valid_for_labor(row)
            if valid:
                task_diag["after_numeric_validation"] += 1
            elif len(task_diag["rejected_rows"]) < 10:
                task_diag["rejected_rows"].append({"source": "estimate_template_rows", "reason": reason})
        diagnostics["tasks"][task] = task_diag
    return diagnostics


def select_historical_labor_evidence(
    calibration: dict[str, Any],
    scope: dict[str, Any],
    expected_tasks: list[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    diagnostics = build_labor_calibration_diagnostics(calibration, scope, expected_tasks)
    selected: dict[str, dict[str, Any]] = {}
    area = safe_float(scope.get("surface_area_sqft"), 0.0)
    default_crew = 4
    template_rows = [row for row in calibration.get("all_labor_rows") or [] if isinstance(row, dict)]
    package_rows = [row for row in calibration.get("job_package_summary") or [] if isinstance(row, dict)]
    relationship_rows = [row for row in calibration.get("relationship_labor_rates") or [] if isinstance(row, dict)]
    for task in expected_tasks:
        task_diag = diagnostics["tasks"][task]
        tmpl_candidates = _candidate_rows_for_task(template_rows, task)
        tmpl_valid = []
        for row in tmpl_candidates:
            allowed, rejected_reason = evidence_allowed_for_scope(row, scope)
            if not allowed:
                if len(task_diag["rejected_rows"]) < 10:
                    task_diag["rejected_rows"].append(
                        {
                            "source": "estimate_template_rows",
                            "reason": rejected_reason,
                            "scope_template_type": scope_template_type(scope),
                            "evidence_template_type": evidence_template_type(row),
                        }
                    )
                continue
            valid, reason = _template_row_valid_for_labor(row)
            if valid:
                tmpl_valid.append(row)
            elif len(task_diag["rejected_rows"]) < 10:
                task_diag["rejected_rows"].append({"source": "estimate_template_rows", "reason": reason})
        tmpl_selected, tmpl_level = _select_rows_by_relaxation(tmpl_valid, scope)
        task_diag["after_project_template_filter"] = len(tmpl_selected)
        task_diag["after_area_filter"] = len([row for row in tmpl_selected if optional_positive_float(first_nonblank(row.get("historical_sqft"), row.get("area_sqft"), row.get("job_area_sqft")))])
        if tmpl_selected:
            selected[task] = _labor_evidence_row_from_template(
                task,
                tmpl_selected,
                area=area,
                multiplier=1.0,
                default_crew_size=default_crew,
                selection_level=tmpl_level,
            )
            task_diag["selected_source"] = "estimate_template_rows"
            task_diag["selected_calibration_rows"] = len(tmpl_selected)
            task_diag["selection_level"] = tmpl_level
            continue

        package_candidates = _candidate_rows_for_task(package_rows, task)
        package_valid = []
        for row in package_candidates:
            allowed, rejected_reason = evidence_allowed_for_scope(row, scope)
            if not allowed:
                if len(task_diag["rejected_rows"]) < 10:
                    task_diag["rejected_rows"].append(
                        {
                            "source": "job_package_summary",
                            "reason": rejected_reason,
                            "scope_template_type": scope_template_type(scope),
                            "evidence_template_type": evidence_template_type(row),
                        }
                    )
                continue
            valid, reason = _relationship_row_valid_for_labor(row)
            if valid:
                package_valid.append(row)
            elif len(task_diag["rejected_rows"]) < 10:
                task_diag["rejected_rows"].append({"source": "job_package_summary", "reason": reason})
        package_selected, package_level = _select_rows_by_relaxation(package_valid, scope)
        if package_selected:
            selected[task] = _labor_evidence_row_from_relationship(task, package_selected, area=area, default_crew_size=default_crew, selection_level=package_level)
            selected[task]["calibration_method"] = "job_package_summary"
            selected[task]["notes"] = "Calibrated from job_package_summary."
            task_diag["selected_source"] = "job_package_summary"
            task_diag["selected_calibration_rows"] = len(package_selected)
            task_diag["selection_level"] = package_level
            task_diag["after_project_template_filter"] = len(package_selected)
            task_diag["after_area_filter"] = len(package_selected)
            continue

        rel_candidates = _candidate_rows_for_task(relationship_rows, task)
        rel_valid = []
        for row in rel_candidates:
            allowed, rejected_reason = evidence_allowed_for_scope(row, scope)
            if not allowed:
                if len(task_diag["rejected_rows"]) < 10:
                    task_diag["rejected_rows"].append(
                        {
                            "source": "relationship_labor_rates",
                            "reason": rejected_reason,
                            "scope_template_type": scope_template_type(scope),
                            "evidence_template_type": evidence_template_type(row),
                        }
                    )
                continue
            valid, reason = _relationship_row_valid_for_labor(row)
            if valid:
                rel_valid.append(row)
            elif len(task_diag["rejected_rows"]) < 10:
                task_diag["rejected_rows"].append({"source": "relationship_labor_rates", "reason": reason})
        rel_selected, rel_level = _select_rows_by_relaxation(rel_valid, scope)
        if rel_selected:
            selected[task] = _labor_evidence_row_from_relationship(task, rel_selected, area=area, default_crew_size=default_crew, selection_level=rel_level)
            task_diag["selected_source"] = "relationship_labor_rates"
            task_diag["selected_calibration_rows"] = len(rel_selected)
            task_diag["selection_level"] = rel_level
            task_diag["after_project_template_filter"] = len(rel_selected)
            task_diag["after_area_filter"] = len(rel_selected)
            continue
        task_diag["selected_source"] = "rule_based_fallback"
        task_diag["selection_level"] = "no_valid_historical_evidence"
    return selected, diagnostics


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


def _negated_phrase(text: str, pattern: str) -> bool:
    return bool(re.search(pattern, text, re.I))


def _positive_rust_evidence(text: str) -> bool:
    if _negated_phrase(text, r"\b(?:no|without)\s+(?:visible\s+)?rust\b|\bno\s+rusted\s+fasteners?\b"):
        return False
    return bool(re.search(r"\b(?:rust|rusted|oxidized|chalking)\b", text))


def _positive_seam_issue_evidence(text: str) -> bool:
    if _negated_phrase(text, r"\b(?:no|without)\s+(?:open\s+)?seam\s+issues?\b|\b(?:no|without)\s+open\s+seams?\b"):
        return False
    positive_patterns = (
        r"\bopen\s+seams?\b",
        r"\bseams?\s+opening\b",
        r"\bfailed\s+seams?\b",
        r"\bseam\s+repair\b",
        r"\bseam\s+treatment\b",
        r"\bseam\s+leaks?\b",
        r"\bleaking\s+seams?\b",
    )
    return any(re.search(pattern, text) for pattern in positive_patterns)


def _positive_leak_evidence(text: str) -> bool:
    if _negated_phrase(text, r"\b(?:no|without)\s+(?:interior\s+)?leaks?\b|\bno\s+leaking\b"):
        return False
    return bool(re.search(r"\b(?:leak|leaks|leaking)\b", text))


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
    no_visible_rust = bool(re.search(r"\b(?:no|without)\s+(?:visible\s+)?rust\b|\bno\s+rusted\s+fasteners?\b", text))
    rusted_metal = metal_context and _positive_rust_evidence(text)
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

    seam_issue = _positive_seam_issue_evidence(text) or _positive_leak_evidence(text)
    clean_maintenance = (
        first_nonblank(scope.get("roof_condition")).lower() in {"excellent", "good"}
        and not seam_issue
        and not _positive_rust_evidence(text)
        and not any(term in text for term in ("ponding", "repair", "failed fastener", "loose fastener"))
    )
    seam_applies: bool | str = bool(coating_required and (seam_issue or (metal_context and not clean_maintenance)))
    if coating_required and metal_context and clean_maintenance:
        seam_applies = "review"
        seam_reason = "Standing seam/metal roof should receive light seam/detail inspection; no base seam treatment priced without repair trigger."
    else:
        seam_reason = (
            "Seam treatment indicated by explicit seam repair/open seam/leak language."
            if seam_issue
            else "Seam treatment included for metal roof coating; estimator should verify seam condition."
            if seam_applies is True and metal_context
            else "No explicit seam repair trigger found."
        )
    packages["seam_treatment"] = WorkPackageDecision(
        "seam_treatment",
        seam_applies,
        0.82 if seam_applies is True else 0.58 if seam_applies == "review" else 0.5,
        seam_reason,
        "detail_density",
        "spot_area" if seam_applies is True else "unknown" if seam_applies == "review" else "none",
        seam_applies is not True,
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


def _price_item_unit(price_item: Any) -> str:
    if not isinstance(price_item, dict):
        return ""
    explicit = normalize_unit(first_nonblank(price_item.get("unit_of_measure"), price_item.get("unit"), price_item.get("price_basis")))
    if explicit in {"unit cost", "extracted line price"}:
        explicit = ""
    if explicit:
        return explicit
    text = " ".join(str(price_item.get(column) or "") for column in ("product_name", "description", "category")).lower()
    if any(term in text for term in ("5 gal", "2 gal", "pail", "bucket")):
        return "pail"
    if any(term in text for term in ("55 gal", "54 gal", "drum")):
        return "drum"
    if "gallon" in text or re.search(r"\bgal\b", text):
        return "gal"
    if "case" in text:
        return "case"
    if "tube" in text:
        return "tube"
    if any(term in text for term in ("each", " ea", "fastener", "screw")):
        return "ea"
    if "linear" in text or re.search(r"\blf\b", text):
        return "lf"
    if "sqft" in text or "square foot" in text:
        return "sqft"
    return ""


def _price_column_unit(price_column: Any) -> str:
    column = first_nonblank(price_column).lower()
    if column == "price_per_sqft":
        return "sqft"
    if column == "price_per_gallon":
        return "gal"
    if column == "price_per_lf":
        return "lf"
    if column == "price_per_unit":
        return "ea"
    return ""


def _units_compatible(quantity_unit: Any, current_item: Any, price_column: Any) -> bool:
    quantity_unit = normalize_unit(quantity_unit)
    column_unit = _price_column_unit(price_column)
    item_unit = _price_item_unit(current_item)
    if not quantity_unit:
        return False
    if column_unit:
        return quantity_unit == column_unit
    if item_unit:
        return quantity_unit == item_unit
    return quantity_unit != "sqft"


def _quantity_ratio_is_safe(
    *,
    bucket: str,
    item: str,
    area: float,
    quantity_ratio: float,
    unit: str,
    current_item: Any,
    price_column: Any,
    unit_price: float,
    max_estimated_cost: float | None,
) -> tuple[bool, str]:
    unit = normalize_unit(unit)
    if not _units_compatible(unit, current_item, price_column):
        return False, f"Rejected {item.lower()} historical quantity ratio because unit {unit or '<blank>'} is incompatible with selected pricing."
    if not sane_quantity_ratio(bucket, unit, quantity_ratio):
        return False, f"Rejected {item.lower()} historical quantity ratio because implied usage was unrealistic."
    quantity = quantity_ratio * area
    estimated_cost = quantity * unit_price
    if bucket == "primer" and unit in {"pail", "pails", "container", "containers"}:
        if quantity > area / 100 or (area / quantity if quantity else 0) < 100:
            return False, "Rejected primer historical quantity ratio because implied usage was unrealistic."
    if max_estimated_cost is not None and estimated_cost > max_estimated_cost:
        return False, f"Rejected {item.lower()} historical quantity ratio because estimated cost exceeded coating cost safety cap."
    return True, ""


def _cost_ratio_is_safe(bucket: str, item: str, area: float, cost_ratio: float, max_estimated_cost: float | None) -> tuple[bool, str]:
    if cost_ratio <= 0 or not math.isfinite(cost_ratio):
        return False, f"Rejected {item.lower()} historical cost ratio because it was not positive."
    estimated_cost = cost_ratio * area
    if max_estimated_cost is not None and estimated_cost > max_estimated_cost:
        return False, f"Rejected {item.lower()} historical cost ratio because estimated cost exceeded coating cost safety cap."
    max_ratio_by_bucket = {
        "primer": 1.0,
        "seam_treatment": 1.0,
        "fastener_treatment": 0.75,
        "caulk_detail": 0.75,
    }
    if max_estimated_cost is None and bucket in max_ratio_by_bucket and estimated_cost > area * max_ratio_by_bucket[bucket]:
        return False, f"Rejected {item.lower()} historical cost ratio because cost per sqft was unrealistic."
    return True, ""


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
    if row.get("included_in_total") is False:
        return low_total, high_total
    if first_nonblank(row.get("selected_price_source"), row.get("price_source_type")) == "historical_cost_ratio_fallback":
        return low_total, high_total
    estimated_cost = optional_positive_float(row.get("estimated_cost"))
    if estimated_cost is None:
        return low_total, high_total
    low = to_float_or_default(row.get("cost_low"), estimated_cost)
    high = to_float_or_default(row.get("cost_high"), estimated_cost)
    return low_total + low, high_total + high


def _material_row_included_in_base_total(row: dict[str, Any]) -> bool:
    if row.get("included_in_total") is False or row.get("excluded_from_base_total"):
        return False
    source = first_nonblank(row.get("selected_price_source"), row.get("price_source_type"))
    if source in {"historical_cost_ratio_fallback", "rejected_historical_quantity_ratio", "review_allowance"}:
        return False
    sanity = first_nonblank(row.get("sanity_check_status")).lower()
    if sanity.startswith("blocked"):
        return False
    return optional_positive_float(row.get("estimated_cost")) is not None


def _primer_material_included_in_base_total(material_plan: list[dict[str, Any]]) -> bool:
    return any(
        first_nonblank(row.get("category")) == "primer" and _material_row_included_in_base_total(row)
        for row in material_plan
        if isinstance(row, dict)
    )


def _exclude_primer_labor_if_material_excluded(
    material_plan: list[dict[str, Any]],
    labor_plan: list[dict[str, Any]],
    *,
    calibration: dict[str, Any],
) -> tuple[list[dict[str, Any]], float, float, int, list[str]]:
    """Remove primer labor from base totals when primer material is not base-priced."""

    has_primer_labor = any(first_nonblank(row.get("task")) == "labor_prime" for row in labor_plan if isinstance(row, dict))
    if not has_primer_labor or _primer_material_included_in_base_total(material_plan):
        total_cost = sum(safe_float(row.get("estimated_cost"), 0.0) for row in labor_plan if isinstance(row, dict) and row.get("included_in_total") is not False)
        total_hours = sum(safe_float(row.get("total_hours"), 0.0) for row in labor_plan if isinstance(row, dict) and row.get("included_in_total") is not False)
        return labor_plan, round(total_cost * 0.85, 2), round(total_cost * 1.2, 2), safe_int(round(total_hours), 0), []

    adjusted: list[dict[str, Any]] = []
    excluded_rows: list[dict[str, Any]] = []
    for row in labor_plan:
        if not isinstance(row, dict) or first_nonblank(row.get("task")) != "labor_prime":
            adjusted.append(row)
            continue
        row = dict(row)
        row["original_total_hours_before_primer_exclusion"] = row.get("total_hours")
        row["original_estimated_cost_before_primer_exclusion"] = row.get("estimated_cost")
        row["excluded_from_base_total"] = True
        row["included_in_total"] = False
        row["review_required"] = True
        row["needs_review"] = True
        row["total_hours"] = 0
        row["labor_hours"] = 0
        row["estimated_cost"] = 0
        row["adjusted_days"] = 0
        row["crew_days"] = 0
        row["labor_selection_status"] = "review_only_excluded"
        row["labor_selection_reason"] = "Primer labor excluded from base because primer material is excluded or unpriced."
        row["notes"] = (
            f"{first_nonblank(row.get('notes'))} "
            "Primer labor excluded from base estimate pending estimator review."
        ).strip()
        excluded_rows.append(row)
        adjusted.append(row)

    total_cost = sum(
        safe_float(row.get("estimated_cost"), 0.0)
        for row in adjusted
        if isinstance(row, dict) and row.get("included_in_total") is not False
    )
    total_hours = sum(
        safe_float(row.get("total_hours"), 0.0)
        for row in adjusted
        if isinstance(row, dict) and row.get("included_in_total") is not False
    )
    diagnostics = calibration.get("labor_calibration_diagnostics")
    if isinstance(diagnostics, dict):
        diagnostics.setdefault("selection_summary", {})
        diagnostics["selection_summary"]["primer_labor_excluded_from_base"] = True
        diagnostics["selection_summary"]["primer_labor_exclusion_reason"] = "Primer material was not included in the base material total."
        diagnostics.setdefault("selection_rows", []).append(
            {
                "task": "labor_prime",
                "selected": False,
                "labor_bucket_role": "optional_primer_bundle",
                "reason": "Primer labor excluded from base because primer material is excluded or unpriced.",
                "selection_status": "review_only_excluded",
            }
        )
    return (
        adjusted,
        round(total_cost * 0.85, 2),
        round(total_cost * 1.2, 2),
        safe_int(round(total_hours), 0),
        ["Primer may be required; material and labor excluded from base estimate pending estimator review."],
    )


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
        row["rejected_quantity"] = row.get("quantity")
        row["rejected_estimated_cost"] = row.get("estimated_cost")
        row["quantity"] = None
        row["estimated_cost"] = None
        row["cost_low"] = None
        row["cost_high"] = None
        row["selected_price_source"] = "rejected_historical_quantity_ratio"
        row["price_source_type"] = "rejected_historical_quantity_ratio"
        row["source_type"] = "manual_review"
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
    max_estimated_cost: float | None = None,
) -> dict[str, Any]:
    calibration = material_calibration.get(bucket) or {}
    evidence_count = safe_int(calibration.get("evidence_count"), 0)
    quantity_ratio = optional_positive_float(calibration.get("median_quantity_per_sqft"))
    valid_quantity_ratio_count = safe_int(
        calibration.get("valid_quantity_ratio_count"),
        evidence_count if quantity_ratio is not None else 0,
    )
    cost_ratio = optional_positive_float(calibration.get("median_cost_per_sqft"))
    current_price = optional_positive_float(calibration.get("selected_current_unit_price"))
    current_item = calibration.get("selected_current_price_item") or {}
    current_item_name = first_nonblank(current_item.get("product_name") if isinstance(current_item, dict) else "", item)
    unit = normalize_unit(first_nonblank(calibration.get("unit"), fallback_unit))
    message_item = bucket.replace("_", " ") if bucket else item.lower()
    if (
        bucket != "seam_treatment"
        and quantity_ratio is None
        and safe_int(calibration.get("rejected_quantity_ratio_count"), 0) > 0
    ):
        review_flags.append(f"Rejected {message_item} historical quantity ratio because implied usage was unrealistic.")

    calibration_audit_fields = {
        "selected_material_calibration_field": calibration.get("selected_material_calibration_field"),
        "chosen_material_quantity_fields": calibration.get("chosen_material_quantity_fields"),
        "rejected_material_evidence_counts_by_reason": calibration.get("quantity_ratio_rejections_by_reason"),
        "quantity_evidence_diagnostics": calibration.get("quantity_evidence_diagnostics"),
        "valid_quantity_ratio_count": valid_quantity_ratio_count,
        "rejected_quantity_ratio_count": calibration.get("rejected_quantity_ratio_count"),
    }

    if valid_quantity_ratio_count > 0 and quantity_ratio is not None and current_price is not None:
        safe, reason = _quantity_ratio_is_safe(
            bucket=bucket,
            item=item,
            area=area,
            quantity_ratio=quantity_ratio,
            unit=unit,
            current_item=current_item,
            price_column=calibration.get("selected_current_price_column"),
            unit_price=current_price,
            max_estimated_cost=max_estimated_cost,
        )
        if safe:
            quantity = quantity_ratio * area
            review_flags.append(f"{item} quantity estimated from historical ratio; verify requirement.")
            estimated_cost = quantity * current_price
            return _priced_allowance_row(
                item=f"{current_item_name} - historically calibrated",
                category=category,
                quantity=round(quantity, 2),
                unit=unit,
                unit_price=current_price,
                selected_price_source="current_pricing + historical_quantity_ratio",
                notes=f"Estimated from historical {item.lower()} physical quantity per sqft and current pricing; estimator should verify requirement.",
            ) | {
                "evidence_count": evidence_count,
                "calibration_method": "historical_quantity_ratio",
                "source_type": "physical_quantity_ratio",
                "quantity_source": "historical_physical_quantity_ratio",
                "unit_price_source": "current_pricing",
                "current_pricing_item": current_item_name,
                "current_unit_price": current_price,
                "current_price_unit": unit,
                "median_quantity_per_sqft": quantity_ratio,
                "p25_quantity_per_sqft": calibration.get("p25_quantity_per_sqft"),
                "p75_quantity_per_sqft": calibration.get("p75_quantity_per_sqft"),
                "estimated_quantity": round(quantity, 2),
                "estimated_cost_current_pricing": round(estimated_cost, 2),
                "historical_physical_quantity_rows_considered": calibration.get("historical_physical_quantity_rows_considered"),
                "historical_cost_fallback_rows_considered": calibration.get("historical_cost_fallback_rows_considered"),
            } | calibration_audit_fields
        review_flags.append(reason)

    current_price_compatible = current_price is not None and _units_compatible(fallback_unit, current_item, calibration.get("selected_current_price_column"))
    if current_price is not None and quantity_ratio is None:
        review_flags.append(f"Current pricing exists for {item.lower()}, but no valid physical quantity evidence was available.")

    if (
        bucket == "fastener_treatment"
        and valid_quantity_ratio_count >= 10
        and quantity_ratio is not None
        and fallback_quantity is not None
        and fallback_unit_price is not None
    ):
        quantity = _round_to_nearest(quantity_ratio * area, 25)
        estimated_cost = float(quantity) * fallback_unit_price
        if max_estimated_cost is not None and estimated_cost > max_estimated_cost:
            review_flags.append(f"Rejected {item.lower()} historical quantity ratio because estimated cost exceeded coating cost safety cap.")
        else:
            review_flags.append(f"{item} quantity estimated from historical fastener count ratio; estimator should verify count.")
            return _priced_allowance_row(
                item=item,
                category=category,
                quantity=quantity,
                unit=fallback_unit,
                unit_price=fallback_unit_price,
                selected_price_source="rule_based_unit_price + historical_quantity_ratio",
                notes="Estimated from historical fastener count per sqft and rule-based unit pricing; estimator should verify count and detail requirements.",
            ) | {
                "evidence_count": evidence_count,
                "calibration_method": "historical_quantity_ratio",
                "source_type": "physical_quantity_ratio",
                "quantity_source": "historical_physical_quantity_ratio",
                "unit_price_source": "rule_based_allowance",
                "median_quantity_per_sqft": quantity_ratio,
                "p25_quantity_per_sqft": calibration.get("p25_quantity_per_sqft"),
                "p75_quantity_per_sqft": calibration.get("p75_quantity_per_sqft"),
                "estimated_quantity": quantity,
                "estimated_cost_current_pricing": estimated_cost,
                "fallback_reason": "No compatible current fastener pricing was available; historical quantity ratio used with rule-based unit price.",
                "historical_physical_quantity_rows_considered": calibration.get("historical_physical_quantity_rows_considered"),
                "historical_cost_fallback_rows_considered": calibration.get("historical_cost_fallback_rows_considered"),
            } | calibration_audit_fields

    if fallback_quantity is not None and current_price_compatible:
        if evidence_count < 3:
            review_flags.append(f"Low historical evidence for {item.lower()}; fallback allowance used.")
        unit_price = current_price
        price_source = "current_pricing + deterministic_quantity"
        estimated_cost = float(fallback_quantity) * unit_price if fallback_quantity is not None and unit_price is not None else None
        if max_estimated_cost is not None and estimated_cost is not None and estimated_cost > max_estimated_cost:
            review_flags.append(f"Rejected {item.lower()} deterministic allowance because estimated cost exceeded coating cost safety cap.")
        else:
            return _priced_allowance_row(
                item=current_item_name if current_price_compatible else item,
                category=category,
                quantity=fallback_quantity,
                unit=fallback_unit,
                unit_price=unit_price,
                selected_price_source=price_source,
                notes=fallback_notes,
            ) | {
                "evidence_count": evidence_count,
                "calibration_method": "deterministic_fallback",
                "source_type": "current_pricing",
                "quantity_source": "deterministic_rule",
                "unit_price_source": "current_pricing",
                "current_pricing_item": current_item_name,
                "current_unit_price": current_price,
                "current_price_unit": fallback_unit,
                "estimated_quantity": fallback_quantity,
                "estimated_cost_current_pricing": estimated_cost,
                "fallback_reason": (
                    "No valid LF seam treatment quantity evidence was available; deterministic LF allowance used."
                    if bucket == "seam_treatment"
                    else "No valid compatible historical physical quantity ratio was available."
                ),
            } | calibration_audit_fields

    if fallback_quantity is not None and fallback_unit_price is not None:
        if evidence_count < 3:
            review_flags.append(f"Low historical evidence for {item.lower()}; fallback allowance used.")
        estimated_cost = float(fallback_quantity) * fallback_unit_price
        if max_estimated_cost is not None and estimated_cost > max_estimated_cost:
            review_flags.append(f"Rejected {item.lower()} rule-based allowance because estimated cost exceeded coating cost safety cap.")
        else:
            return _priced_allowance_row(
                item=item,
                category=category,
                quantity=fallback_quantity,
                unit=fallback_unit,
                unit_price=fallback_unit_price,
                selected_price_source="rule_based_allowance",
                notes=fallback_notes,
            ) | {
                "evidence_count": evidence_count,
                "calibration_method": "deterministic_fallback",
                "source_type": "manual_review",
                "quantity_source": "deterministic_rule",
                "unit_price_source": "rule_based_allowance",
                "estimated_quantity": fallback_quantity,
                "fallback_reason": (
                    "No valid LF seam treatment quantity evidence was available; deterministic LF allowance used."
                    if bucket == "seam_treatment"
                    else "No valid compatible current pricing or historical cost fallback was available."
                ),
            } | calibration_audit_fields

    if evidence_count >= 3 and cost_ratio is not None:
        safe, reason = _cost_ratio_is_safe(bucket, item, area, cost_ratio, max_estimated_cost)
        review_estimated_cost = cost_ratio * area if safe else None
        review_flags.append(reason if not safe else f"{item} historical cost ratio is review-only; verify scope, quantity, and current pricing.")
        return _priced_allowance_row(
            item=f"{item} - historical cost review allowance",
            category=category,
            quantity=None,
            unit="",
            unit_price=None,
            estimated_cost=None,
            selected_price_source="historical_cost_ratio_fallback",
            notes=f"Review-only historical {item.lower()} cost per sqft evidence; excluded from base estimate until quantity and current pricing are verified.",
        ) | {
            "evidence_count": evidence_count,
            "calibration_method": "historical_cost_ratio_fallback",
            "source_type": "cost_allowance_ratio",
            "quantity_source": "none",
            "unit_price_source": "none",
            "needs_review": True,
            "review_required": True,
            "included_in_total": False,
            "review_estimated_cost": round(review_estimated_cost, 2) if review_estimated_cost is not None else None,
            "fallback_reason": "Historical cost ratios are review-only by default.",
            "historical_cost_fallback_rows_considered": calibration.get("historical_cost_fallback_rows_considered"),
        } | calibration_audit_fields

    return _priced_allowance_row(
        item=item,
        category=category,
        quantity=None,
        unit=fallback_unit,
        unit_price=None,
        selected_price_source="review_allowance",
        notes=f"{item} could not be priced; estimator should verify quantity and pricing.",
    ) | {
        "evidence_count": evidence_count,
        "calibration_method": "unpriced_review",
        "source_type": "manual_review",
        "quantity_source": "none",
        "unit_price_source": "none",
        "fallback_reason": "No valid current pricing, deterministic quantity, or historical fallback was available.",
    } | calibration_audit_fields


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
    material_calibration = calibration.get("material_calibration")
    if not isinstance(material_calibration, dict):
        material_calibration = {}
        calibration["material_calibration"] = material_calibration

    def package_calibration(bucket: str) -> dict[str, Any]:
        if bucket not in material_calibration:
            material_calibration[bucket] = build_bucket_calibration(data, scope, bucket)
        return material_calibration

    if scope.get("coating_required") and area:
        coating_decision = work_packages.get("coating")
        wet_mils = warranty_wet_mils(scope.get("warranty_target"), coating_type)
        gallons = coating_gallons(area, wet_mils, assumptions.coating_waste_factor)
        pricing_source = data.pricing_catalog if not data.pricing_catalog.empty else data.pricing
        price = find_current_price(pricing_source, [coating_type] if coating_type else ["coating"], "price_per_gallon")
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
    coating_cost_cap = None
    for row in plan:
        if row.get("category") == "coating":
            coating_cost_cap = optional_positive_float(row.get("estimated_cost"))
            break
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
                material_calibration=package_calibration("primer"),
                fallback_quantity=round(area, 1),
                fallback_unit="sqft",
                fallback_unit_price=fallback_unit_price,
                fallback_notes="Rule-based primer allowance; estimator should verify primer requirement.",
                review_flags=review_flags,
                package_decision=primer_decision,
                max_estimated_cost=coating_cost_cap,
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
                material_calibration=package_calibration("seam_treatment"),
                fallback_quantity=seam_lf,
                fallback_unit="lf",
                fallback_unit_price=3.0,
                fallback_notes="Rule-based seam/detail LF allowance for metal roof coating; estimator should verify seam layout and detail requirements.",
                review_flags=review_flags,
                package_decision=seam_decision,
                max_estimated_cost=coating_cost_cap,
            )
        row = _row_with_package_context(row, seam_decision)
        row = _sanity_check_material_row(row, area, "seam_treatment")
        plan.append(row)
        low_total, high_total = _add_allowance_cost_to_totals(row, (low_total, high_total))
    elif seam_decision and seam_decision.get("review_required"):
        review_flags.append(seam_decision.get("reason") or "Seam treatment should be verified.")

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
                material_calibration=package_calibration("fastener_treatment"),
                fallback_quantity=fasteners,
                fallback_unit="ea",
                fallback_unit_price=1.5,
                fallback_notes="Rule-based fastener treatment allowance; estimator should verify count and detail requirements.",
                review_flags=review_flags,
                package_decision=fastener_decision,
                max_estimated_cost=coating_cost_cap,
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
                material_calibration=package_calibration("caulk_detail"),
                fallback_quantity=max(detail_units, 1),
                fallback_unit="allowance",
                fallback_unit_price=150.0,
                fallback_notes="Rule-based caulk/detail allowance for penetrations and roof details; estimator should verify count.",
                review_flags=review_flags,
                package_decision=caulk_decision,
                max_estimated_cost=coating_cost_cap,
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
    crew_size = sane_crew_size(decision.get("crew_assumptions", {}).get("recommended_crew_size"), 4, max_size=8)
    if crew_size <= 0:
        crew_size = 4
    work_packages = ensure_work_package_decisions(scope, decision)
    baseline_tasks = required_roof_coating_labor_tasks(scope, decision)
    historical_by_task: dict[str, dict[str, Any]] = {}
    if baseline_tasks:
        historical_by_task, diagnostics = select_historical_labor_evidence(calibration, scope, baseline_tasks)
        calibration["labor_calibration_diagnostics"] = diagnostics
    raw_rows = calibration.get("labor_by_bucket") or []
    all_candidate_labor_rows = [row for row in raw_rows if isinstance(row, dict)]
    raw_rows_by_task: dict[str, dict[str, Any]] = {}
    if baseline_tasks:
        raw_rows_by_task = {
            first_nonblank(row.get("template_bucket"), row.get("task")).strip(): row
            for row in raw_rows
            if isinstance(row, dict)
        }
        raw_rows = [historical_by_task.get(task) or raw_rows_by_task.get(task) for task in baseline_tasks]
        raw_rows = [row for row in raw_rows if isinstance(row, dict)]
    rows, excluded_buckets = filter_labor_calibration_rows(raw_rows, scope, decision)
    if excluded_buckets:
        calibration["excluded_labor_buckets"] = excluded_buckets
    if baseline_tasks and is_roof_coating_scope(scope):
        rows, labor_selection_rows = select_roof_coating_labor_rows(
            [row for row in rows if isinstance(row, dict)],
            scope,
            decision,
            area=area,
            multiplier=multiplier,
            expected_tasks=baseline_tasks,
            all_candidate_rows=all_candidate_labor_rows,
        )
        diagnostics = calibration.get("labor_calibration_diagnostics")
        if isinstance(diagnostics, dict):
            diagnostics["selection_rows"] = labor_selection_rows
            diagnostics["selection_summary"] = {
                "selected_count": sum(1 for row in labor_selection_rows if row.get("selected")),
                "rejected_count": sum(1 for row in labor_selection_rows if row.get("selected") is False),
                "hours_per_1000_cap": 80,
            }
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
                raw_crew_size = safe_int(row.get("median_crew_size"), 0)
                crew_invalid = bool(raw_crew_size and raw_crew_size > 8)
                cost_missing = is_missing_or_bad_number(row.get("median_estimated_cost"))
                evidence_missing = is_missing_or_bad_number(row.get("evidence_count"))
                row_incomplete = any([hours_missing, days_missing, crew_missing, crew_invalid, cost_missing, evidence_missing])
                incomplete_calibration = incomplete_calibration or row_incomplete
                days = 1.0 if days_missing else max(safe_float(row.get("median_days"), 1.0), 0.0)
                valid_historical_crew = bool(raw_crew_size and 0 < raw_crew_size <= 8)
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
                calibration_method = first_nonblank(row.get("calibration_method"), "historical_calibration")
                selection_level = first_nonblank(row.get("selection_level"))
                hours_per_1000 = _labor_hours_per_1000(row, area)
                notes = first_nonblank(row.get("notes"))
                if not notes:
                    notes = (
                        "Historical labor calibration was incomplete for one or more tasks; defaults were used."
                        if row_incomplete
                        else "Calibrated from estimate_template_rows."
                    )
                if row_incomplete and "incomplete" not in notes.lower():
                    notes = f"{notes} Historical labor calibration was incomplete for one or more tasks; defaults were used."
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
                            "calibration_method": calibration_method,
                            "selection_level": selection_level,
                            "labor_bucket_role": row.get("labor_bucket_role"),
                            "labor_selection_status": row.get("labor_selection_status") or "selected",
                            "labor_selection_reason": row.get("labor_selection_reason"),
                            "median_hours_per_1000_sqft": round(hours_per_1000, 2) if hours_per_1000 is not None else None,
                            "capped_hours": row.get("capped_hours"),
                            "notes": notes,
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
    if baseline_tasks:
        existing_tasks = {first_nonblank(row.get("task")).strip() for row in plan if isinstance(row, dict)}
        for task in baseline_tasks:
            if task in existing_tasks:
                continue
            if (
                is_roof_coating_scope(scope)
                and task == "labor_caulk"
                and ({"labor_seam_sealer", "labor_details"} & existing_tasks)
            ):
                diagnostics = calibration.get("labor_calibration_diagnostics")
                if isinstance(diagnostics, dict):
                    diagnostics.setdefault("selection_rows", []).append(
                        {
                            "task": task,
                            "selected": False,
                            "labor_bucket_role": "core_detail_bundle",
                            "reason": "Caulk/detail fallback skipped because seam/detail labor already covers overlapping detail work.",
                            "evidence_count": safe_int((raw_rows_by_task.get(task) or {}).get("evidence_count"), 0),
                            "median_hours_per_1000_sqft": None,
                            "median_total_hours": None,
                            "capped_hours": None,
                            "calibration_method": "rule_based_fallback",
                            "selection_level": "deduplicated_detail_bundle",
                        }
                    )
                continue
            fallback_row = _fallback_labor_row(
                task=task,
                scope=scope,
                decision=decision,
                assumptions=assumptions,
                crew_size=crew_size,
                multiplier=multiplier,
                production_rate=production_rate,
            )
            candidate_row = raw_rows_by_task.get(task) or {}
            candidate_evidence_count = safe_int(candidate_row.get("evidence_count"), 0)
            if candidate_evidence_count:
                fallback_row["evidence_count"] = candidate_evidence_count
                fallback_row["matched_comparable_job_count"] = candidate_evidence_count
                fallback_row["notes"] = (
                    f"{fallback_row.get('notes', '')} "
                    "Historical labor calibration was incomplete for this task; fallback used candidate evidence count."
                ).strip()
            plan.append(fallback_row)
            total_hours += safe_float(fallback_row.get("total_hours"), 0.0)
            total_cost += safe_float(fallback_row.get("estimated_cost"), 0.0)
            incomplete_calibration = True
            existing_tasks.add(task)
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
    plan, total_hours, total_cost = apply_roof_coating_labor_bundle_cap(
        plan,
        total_hours=total_hours,
        total_cost=total_cost,
        area=area,
        scope=scope,
        calibration=calibration,
    )
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
        "match_strength",
        "strong_reason_count",
        "weak_reason_count",
        "included_as_evidence",
        "exclusion_reason",
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


def apply_scope_to_parsed(parsed: Any, scope: dict[str, Any]) -> None:
    for attr, key in (
        ("project_type", "project_type"),
        ("division", "division"),
        ("building_type", "building_type"),
        ("substrate", "substrate"),
        ("coating_type", "coating_type"),
        ("roof_condition", "roof_condition"),
        ("access_complexity", "access_complexity"),
        ("penetrations_complexity", "penetrations_complexity"),
        ("city", "city"),
        ("state", "state"),
    ):
        value = first_nonblank(scope.get(key))
        if value:
            setattr(parsed, attr, value)
    sqft = optional_positive_float(scope.get("estimated_sqft")) or optional_positive_float(scope.get("surface_area_sqft"))
    if sqft is not None:
        parsed.estimated_sqft = sqft
    warranty = optional_positive_int(scope.get("warranty_target_years")) or optional_positive_int(scope.get("warranty_target"))
    if warranty is not None:
        parsed.warranty_target_years = warranty
    parsed.missing_info = [
        item
        for item in parsed.missing_info
        if not (
            (item == "estimated_sqft" and sqft is not None)
            or (item == "substrate" and first_nonblank(scope.get("substrate")))
            or (item == "roof_condition" and first_nonblank(scope.get("roof_condition")))
            or (item == "coating/warranty target" and (first_nonblank(scope.get("coating_type")) or warranty is not None))
        )
    ]


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


def parsed_fields_for_result(
    parsed: Any,
    scope: dict[str, Any],
    *,
    resolved_sqft: float | None,
    dimension_summary: dict[str, Any],
    run_id: str,
    input_notes_hash: str,
) -> dict[str, Any]:
    parsed_fields = asdict(parsed)
    if resolved_sqft is not None:
        parsed_fields["estimated_sqft"] = resolved_sqft
        parsed_fields["surface_area_sqft"] = resolved_sqft
    for area_field in ("gross_area_sqft", "deduction_area_sqft", "net_area_sqft"):
        value = scope.get(area_field) or _dimension_summary_value(dimension_summary, area_field)
        if value is not None:
            parsed_fields[area_field] = value
    for extra_field in (
        "notes",
        "division",
        "template_type",
        "estimate_mode",
        "building_type",
        "building_footprint_length_ft",
        "building_footprint_width_ft",
        "footprint_area_sqft",
        "building_perimeter_ft",
        "wall_height_ft",
        "ceiling_included",
        "roof_underside_included",
        "outside_walls_included",
        "ceiling_area_sqft",
        "roof_center_height_ft",
        "ridge_height_ft",
        "roof_rise_ft",
        "roof_half_span_ft",
        "roof_rafter_length_ft",
        "roof_underside_area_sqft",
        "pitched_roof_underside_area_sqft",
        "roof_underside_area_formula",
        "roof_underside_source_text",
        "gross_wall_area_sqft",
        "gross_insulation_area_sqft",
        "opening_area_known_sqft",
        "opening_area_missing",
        "net_insulation_area_sqft",
        "openings",
        "insulation_surface_areas",
        "insulation_deductions",
        "insulation_r_value_targets",
        "insulation_foam_type",
        "insulation_product_selection",
        "insulation_thickness_calculation",
        "assumptions",
        "requested_timing",
        "building_installation_timing",
        "customer_name",
        "phone",
        "address",
        "roof_type",
        "gross_sqft",
        "deduction_sqft",
        "net_sqft",
        "dimension_evidence",
        "condition",
        "condition_flags",
        "penetration_complexity",
        "defects",
        "scope_triggers",
        "partial_scope",
        "confidence_by_field",
        "evidence_by_field",
        "contradictions",
        "missing_questions",
        "condition_detail_flags",
        "penetration_count",
        "roof_condition_raw_phrase",
        "roof_condition_reason",
        "penetrations_complexity_reason",
        "access_complexity_reason",
        "ai_scope_packages",
    ):
        if extra_field in scope:
            parsed_fields[extra_field] = scope.get(extra_field)
    parsed_fields["run_id"] = run_id
    parsed_fields["input_notes_hash"] = input_notes_hash
    return parsed_fields


def run_integrity_for_result(parsed_fields: dict[str, Any], raw_notes: str, run_id: str, input_notes_hash: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    stale_fields = stale_source_text_fields(parsed_fields, raw_notes)
    return (
        {
            "run_id": run_id,
            "input_notes_hash": input_notes_hash,
            "parsed_scope_notes_hash": parsed_fields.get("input_notes_hash"),
            "stale_source_text_detected": bool(stale_fields),
            "stale_fields_detected": stale_fields,
            "prior_cache_used": False,
            "warnings": ["Possible stale parse/cache contamination."] if stale_fields else [],
        },
        stale_fields,
    )


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
    runtime_seconds_by_stage: dict[str, float] = {}
    run_id = new_estimator_run_id(raw_notes)
    input_notes_hash = notes_hash(raw_notes)
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
    stage_start = time.perf_counter()
    parsed = parse_field_notes(field_input)
    scope = parsed_to_scope(parsed, field_input)
    resolved_sqft = resolve_estimated_sqft(parsed, scope, optional_overrides)
    deterministic_resolved_sqft = resolved_sqft
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
    deterministic_parsed_scope = asdict(parsed)
    deterministic_scope = dict(scope)
    ai_enabled = ai_scope_interpreter.ai_scope_interpreter_enabled()
    ai_parsed_scope: dict[str, Any] = {}
    ai_merge_decisions: list[dict[str, Any]] = []
    ai_review_flags: list[str] = []
    if ai_enabled:
        ai_parsed_scope = ai_scope_interpreter.interpret_field_notes_with_ai(raw_notes, deterministic_scope=deterministic_scope)
        scope, ai_merge_decisions, ai_review_flags = ai_scope_interpreter.merge_ai_scope_with_deterministic(
            raw_notes,
            deterministic_scope,
            ai_parsed_scope,
        )
        if deterministic_resolved_sqft is not None:
            scope["estimated_sqft"] = deterministic_resolved_sqft
            scope["surface_area_sqft"] = deterministic_resolved_sqft
        else:
            ai_resolved_sqft = optional_positive_float(scope.get("estimated_sqft")) or optional_positive_float(scope.get("surface_area_sqft"))
            if ai_resolved_sqft is not None and ai_scope_interpreter._notes_have_area_or_dimensions(raw_notes):
                scope["estimated_sqft"] = ai_resolved_sqft
                scope["surface_area_sqft"] = ai_resolved_sqft
                resolved_sqft = ai_resolved_sqft
            else:
                scope.pop("estimated_sqft", None)
                scope.pop("surface_area_sqft", None)
        apply_scope_to_parsed(parsed, scope)
    runtime_seconds_by_stage["parse_scope"] = round(time.perf_counter() - stage_start, 4)

    resolved_sqft = deterministic_resolved_sqft or optional_positive_float(scope.get("estimated_sqft")) or optional_positive_float(scope.get("surface_area_sqft"))
    readiness = evaluate_estimate_readiness(scope, raw_notes)
    if readiness["estimate_status"] != READY_TO_ESTIMATE:
        for field_name in readiness.get("missing_fields") or []:
            if field_name not in parsed.missing_info:
                parsed.missing_info.append(field_name)
        parsed_fields = parsed_fields_for_result(
            parsed,
            scope,
            resolved_sqft=resolved_sqft,
            dimension_summary=dimension_summary,
            run_id=run_id,
            input_notes_hash=input_notes_hash,
        )
        parsed_fields.update(
            {
                "estimate_status": readiness["estimate_status"],
                "estimate_reason": readiness["estimate_reason"],
                "required_questions": readiness.get("required_questions") or [],
                "recommended_next_actions": readiness.get("recommended_next_actions") or [],
                "readiness_confidence": readiness.get("confidence"),
            }
        )
        review_flags = []
        review_flags.extend(f"Missing: {item}" for item in parsed.missing_info)
        review_flags.extend(parsed.review_flags)
        review_flags.extend(ai_review_flags)
        review_flags.append(readiness["estimate_reason"])
        stale_run_integrity, stale_fields = run_integrity_for_result(parsed_fields, raw_notes, run_id, input_notes_hash)
        if stale_fields:
            review_flags.append("Possible stale parse/cache contamination.")
        debug = {
            "labor_calibration": {},
            "ai_scope_interpreter": {
                "enabled": ai_enabled,
                "deterministic_parsed_scope": deterministic_parsed_scope,
                "deterministic_scope": deterministic_scope,
                "ai_parsed_scope": ai_parsed_scope,
                "final_merged_scope": scope,
                "merge_decisions": ai_merge_decisions,
                "ai_confidence_by_field": ai_parsed_scope.get("confidence_by_field") if isinstance(ai_parsed_scope, dict) else {},
                "ai_review_flags": ai_review_flags,
            },
            "estimate_readiness": readiness,
            "run_integrity": stale_run_integrity,
            "runtime_seconds_by_stage": runtime_seconds_by_stage,
        }
        return EstimateRecommendation(
            parsed_fields=parsed_fields,
            recommended_scope=[
                "Recommendation only: collect missing roof size before producing a coating estimate.",
                *list(readiness.get("recommended_next_actions") or []),
            ],
            material_plan=[],
            labor_plan=[],
            travel_plan={},
            historical_calibration={},
            similar_examples=[],
            estimate_low=None,
            estimate_target=None,
            estimate_high=None,
            review_flags=review_flags,
            human_review_required=True,
            draft_workbook_inputs=draft_workbook_inputs(field_input, scope, [], [], {}, review_flags),
            estimate_status=readiness["estimate_status"],
            estimate_reason=readiness["estimate_reason"],
            required_questions=list(readiness.get("required_questions") or []),
            recommended_next_actions=list(readiness.get("recommended_next_actions") or []),
            confidence=str(readiness.get("confidence") or "low"),
            debug=debug,
        )

    if scope_template_type(scope) == "insulation":
        parsed_fields = parsed_fields_for_result(
            parsed,
            scope,
            resolved_sqft=resolved_sqft,
            dimension_summary=dimension_summary,
            run_id=run_id,
            input_notes_hash=input_notes_hash,
        )
        parsed_fields.update(
            {
                "estimate_status": READY_TO_ESTIMATE,
                "estimate_reason": "Insulation scope parsed. Review missing foam/opening details before quoting.",
                "required_questions": scope.get("missing_questions") or parsed.missing_info or [],
                "recommended_next_actions": [
                    "Confirm foam type and thickness/R-value.",
                    "Confirm missing opening dimensions before final deduction.",
                    "Use the insulation workbench rows rather than roofing/coating rows.",
                ],
                "readiness_confidence": "medium",
            }
        )
        review_flags = []
        review_flags.extend(parsed.review_flags)
        review_flags.extend(scope.get("review_flags") or [])
        review_flags.extend(ai_review_flags)
        review_flags.append("Insulation workbench support is available as editable defaults; verify foam type, thickness, and thermal barrier requirements before quoting.")
        stale_run_integrity, stale_fields = run_integrity_for_result(parsed_fields, raw_notes, run_id, input_notes_hash)
        if stale_fields:
            review_flags.append("Possible stale parse/cache contamination.")
        debug = {
            "labor_calibration": {},
            "ai_scope_interpreter": {
                "enabled": ai_enabled,
                "deterministic_parsed_scope": deterministic_parsed_scope,
                "deterministic_scope": deterministic_scope,
                "ai_parsed_scope": ai_parsed_scope,
                "final_merged_scope": scope,
                "merge_decisions": ai_merge_decisions,
                "ai_confidence_by_field": ai_parsed_scope.get("confidence_by_field") if isinstance(ai_parsed_scope, dict) else {},
                "ai_review_flags": ai_review_flags,
            },
            "estimate_readiness": readiness,
            "run_integrity": stale_run_integrity,
            "runtime_seconds_by_stage": runtime_seconds_by_stage,
            "insulation_placeholder": {
                "reason": "Skipped legacy roofing material/labor calibration for insulation scope.",
                "missing_questions": parsed_fields.get("required_questions") or [],
            },
        }
        material_plan = [
            {"category": "foam", "package": "foam", "included_in_total": False, "needs_review": True, "notes": "Foam applies; confirm foam type and thickness/R-value."},
            {
                "category": "thermal_barrier",
                "package": "thermal_barrier",
                "included_in_total": False,
                "needs_review": True,
                "notes": "Verify thermal/ignition barrier requirement.",
            },
        ]
        labor_plan = [
            {"task": "labor_foam", "included_in_total": False, "needs_review": True, "notes": "Foam labor applies after foam scope is confirmed."},
            {"task": "labor_set_up", "included_in_total": False, "needs_review": True, "notes": "Setup labor available for estimator review."},
            {"task": "labor_clean_up", "included_in_total": False, "needs_review": True, "notes": "Cleanup labor available for estimator review."},
        ]
        return EstimateRecommendation(
            parsed_fields=parsed_fields,
            recommended_scope=[
                "Insulation scope: outside walls and ceiling.",
                "Confirm foam type, thickness/R-value, and missing opening dimensions.",
            ],
            material_plan=material_plan,
            labor_plan=labor_plan,
            travel_plan={},
            historical_calibration={},
            similar_examples=[],
            estimate_low=None,
            estimate_target=None,
            estimate_high=None,
            review_flags=list(dict.fromkeys(str(flag) for flag in review_flags if flag)),
            human_review_required=True,
            draft_workbook_inputs=draft_workbook_inputs(field_input, scope, material_plan, labor_plan, {}, review_flags),
            estimate_status=READY_TO_ESTIMATE,
            estimate_reason="Insulation scope parsed. Review missing foam/opening details before quoting.",
            required_questions=list(parsed_fields.get("required_questions") or []),
            recommended_next_actions=list(parsed_fields.get("recommended_next_actions") or []),
            confidence="medium",
            debug=debug,
        )

    if data is None:
        data = load_estimator_data(database_url=database_url, prefer_database=bool(database_url))
    stage_start = time.perf_counter()
    similar = find_similar_jobs(data, scope, limit=8)
    runtime_seconds_by_stage["similar_jobs"] = round(time.perf_counter() - stage_start, 4)
    stage_start = time.perf_counter()
    legacy_calibration = calibrate_from_history(similar, data.line_items, scope)
    template_calibration = historical_template_calibration(data, similar)
    calibration = {**legacy_calibration, **template_calibration}
    runtime_seconds_by_stage["historical_calibration"] = round(time.perf_counter() - stage_start, 4)
    decision = evaluate_decision_tree(scope, calibration)
    calibration["work_package_decisions"] = ensure_work_package_decisions(scope, decision)
    stage_start = time.perf_counter()
    material_plan, material_low, material_high, material_review_flags = build_material_plan(scope, data, calibration, decision, assumptions)
    runtime_seconds_by_stage["select_materials"] = round(time.perf_counter() - stage_start, 4)
    labor_review_flags: list[str] = []
    stage_start = time.perf_counter()
    try:
        labor_plan, labor_low, labor_high, crew_size, duration_days, _labor_hours = build_labor_plan(scope, calibration, decision, assumptions)
        labor_plan, labor_low, labor_high, _labor_hours, primer_labor_flags = _exclude_primer_labor_if_material_excluded(
            material_plan,
            labor_plan,
            calibration=calibration,
        )
        if primer_labor_flags:
            labor_review_flags.extend(primer_labor_flags)
        duration_total = sum(safe_float(row.get("adjusted_days"), 0.0) for row in labor_plan if isinstance(row, dict))
        duration_days = max(1, safe_int(round(duration_total), 1))
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
    labor_sanity_flags = labor_sanity_review_flags(scope, material_plan, labor_plan)
    runtime_seconds_by_stage["select_labor"] = round(time.perf_counter() - stage_start, 4)
    diagnostics = calibration.get("labor_calibration_diagnostics")
    if isinstance(diagnostics, dict):
        diagnostics.setdefault("selection_summary", {})
        diagnostics["selection_summary"]["labor_sanity_checks"] = labor_sanity_flags
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
    review_flags.extend(ai_review_flags)
    review_flags.extend(decision.get("human_review_flags") or [])
    review_flags.extend(material_review_flags)
    review_flags.extend(labor_review_flags)
    review_flags.extend(labor_sanity_flags)
    labor_evidence_available = not data.relationship_labor_rates.empty or not data.job_package_summary.empty
    if any("Historical labor calibration was incomplete" in str(row.get("notes") or "") for row in labor_plan) and not labor_evidence_available:
        review_flags.append("Historical labor calibration was incomplete for one or more tasks.")
    if any("Historical labor calibration unavailable" in str(row.get("notes") or "") for row in labor_plan) and not labor_evidence_available:
        review_flags.append("Historical labor calibration unavailable or incomplete.")
    if any(row.get("calibration_method") == "rule_based_fallback" for row in labor_plan) and not labor_evidence_available:
        review_flags.append("Historical labor calibration unavailable or incomplete; rule-based fallback labor rows were added.")
    if travel_plan.get("needs_travel_review"):
        review_flags.append("Travel assumptions require review.")
    if data.template_rows.empty:
        template_evidence_available = not getattr(data, "job_package_summary", pd.DataFrame()).empty or not getattr(data, "relationship_material_qty_ratios", pd.DataFrame()).empty
        if not template_evidence_available:
            review_flags.append("estimate_template_rows unavailable or empty; template calibration is limited.")
    if data.pricing.empty and data.pricing_catalog.empty:
        review_flags.append("pricing_catalog unavailable or empty; current material pricing is limited.")
    if data.template_rows.empty and not data.classified_line_items.empty:
        review_flags.append("Using estimate_line_item_classifications fallback evidence.")
    parsed_fields = parsed_fields_for_result(
        parsed,
        scope,
        resolved_sqft=resolved_sqft,
        dimension_summary=dimension_summary,
        run_id=run_id,
        input_notes_hash=input_notes_hash,
    )
    parsed_fields.update(
        {
            "estimate_status": READY_TO_ESTIMATE,
            "estimate_reason": "Required estimate inputs are present.",
            "required_questions": [],
            "recommended_next_actions": [],
            "readiness_confidence": "medium",
        }
    )
    run_integrity, stale_fields = run_integrity_for_result(parsed_fields, raw_notes, run_id, input_notes_hash)
    if stale_fields:
        review_flags.append("Possible stale parse/cache contamination.")
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
        estimate_status=READY_TO_ESTIMATE,
        estimate_reason="Required estimate inputs are present.",
        required_questions=[],
        recommended_next_actions=[],
        confidence="medium",
        debug={
            "labor_calibration": calibration.get("labor_calibration_diagnostics") or {},
            "ai_scope_interpreter": {
                "enabled": ai_enabled,
                "deterministic_parsed_scope": deterministic_parsed_scope,
                "deterministic_scope": deterministic_scope,
                "ai_parsed_scope": ai_parsed_scope,
                "final_merged_scope": scope,
                "merge_decisions": ai_merge_decisions,
                "ai_confidence_by_field": ai_parsed_scope.get("confidence_by_field") if isinstance(ai_parsed_scope, dict) else {},
                "ai_review_flags": ai_review_flags,
            },
            "run_integrity": run_integrity,
            "runtime_seconds_by_stage": runtime_seconds_by_stage,
        },
    )
