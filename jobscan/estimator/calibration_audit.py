from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from . import estimate_from_field_notes, load_estimator_data
from .evidence_export import sanitize_for_export
from .schemas import EstimatorData


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES_PATH = REPO_ROOT / "evals" / "estimator" / "field_notes_cases.json"
DEFAULT_OUTPUT_DIR = Path("output/estimator_audit")

AUDIT_SHEETS = [
    "summary",
    "parsed_scope",
    "ai_scope_interpreter",
    "material_plan",
    "material_audit",
    "material_evidence",
    "labor_plan",
    "labor_audit",
    "labor_evidence",
    "similar_jobs_audit",
    "rejected_evidence",
    "relationship_rows_sample",
]

EXPECTED_ROOF_COATING_LABOR_TASKS = [
    "labor_prep",
    "labor_seam_sealer",
    "labor_base",
    "labor_top_coat",
    "labor_details",
    "labor_cleanup",
    "labor_loading",
]

PACKAGE_TERMS: dict[str, tuple[str, ...]] = {
    "coating": ("coating", "silicone", "acrylic", "urethane", "top coat", "base coat"),
    "primer": ("primer", "prime", "epoxy", "bleed block", "bleed-block"),
    "seam_treatment": ("seam", "sealer", "seam sealer", "lap", "fabric", "tape"),
    "fastener_treatment": ("fastener", "screw", "screws", "washer"),
    "caulk_detail": ("caulk", "sealant", "detail", "penetration", "curb", "drain", "rtu", "flashing"),
    "foam": ("foam", "closed cell", "open cell", "spray foam", "spf"),
}

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

LABOR_TASK_TERMS: dict[str, tuple[str, ...]] = {
    "labor_prep": ("labor_prep", "prep", "pressure wash", "power wash", "clean"),
    "labor_prime": ("labor_prime", "prime", "primer"),
    "labor_seam_sealer": ("labor_seam_sealer", "seam", "sealer", "seam sealer"),
    "labor_base": ("labor_base", "base coat", "base"),
    "labor_top_coat": ("labor_top_coat", "top coat", "finish coat"),
    "labor_details": ("labor_details", "details", "detail", "penetration", "curb"),
    "labor_caulk": ("labor_caulk", "caulk", "sealant"),
    "labor_cleanup": ("labor_cleanup", "cleanup", "clean up", "touch/cleanup"),
    "labor_loading": ("labor_loading", "loading", "load"),
}


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def lower_text(value: Any) -> str:
    return clean_text(value).lower()


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def object_to_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if is_dataclass(value):
        return sanitize_for_export(asdict(value), excel=False)
    if isinstance(value, dict):
        return sanitize_for_export(value, excel=False)
    if hasattr(value, "__dict__"):
        return sanitize_for_export(vars(value), excel=False)
    return {"value": sanitize_for_export(value, excel=False)}


def records_from(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, pd.DataFrame):
        return sanitize_for_export(value.to_dict(orient="records"), excel=False)
    if isinstance(value, pd.Series):
        return [sanitize_for_export(value.to_dict(), excel=False)]
    if isinstance(value, list):
        rows: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, dict):
                rows.append(sanitize_for_export(item, excel=False))
            elif is_dataclass(item):
                rows.append(sanitize_for_export(asdict(item), excel=False))
            else:
                rows.append({"value": sanitize_for_export(item, excel=False)})
        return rows
    if isinstance(value, dict):
        return [sanitize_for_export(value, excel=False)]
    return [{"value": sanitize_for_export(value, excel=False)}]


def frame_records(data: EstimatorData | None, attr: str, *, source: str | None = None, limit: int = 5000) -> list[dict[str, Any]]:
    frame = getattr(data, attr, pd.DataFrame()) if data is not None else pd.DataFrame()
    if frame is None or frame.empty:
        return []
    rows = frame.head(limit).copy()
    rows["evidence_source_table"] = source or attr
    return records_from(rows)


def row_text(row: dict[str, Any]) -> str:
    cached = row.get("_audit_row_text")
    if isinstance(cached, str):
        return cached
    fields = (
        "template_bucket",
        "package",
        "category",
        "item",
        "item_name",
        "product_name",
        "selected_item_name",
        "row_label",
        "line_item_name",
        "normalized_item_name",
        "description",
        "notes",
        "line_item_kind",
        "labor_package",
        "task",
        "source_type",
    )
    return " ".join(lower_text(row.get(field)) for field in fields)


def normalize_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", lower_text(value)).strip("_")


def package_for_material_row(row: dict[str, Any]) -> str:
    direct = first_nonblank(row.get("package"), row.get("category"))
    key = normalize_key(direct)
    if key in PACKAGE_TERMS:
        return key
    text = row_text(row)
    for package, terms in PACKAGE_TERMS.items():
        if any(term in text for term in terms):
            return package
    return key or "unknown"


def labor_task_for_row(row: dict[str, Any]) -> str:
    direct = first_nonblank(row.get("task"), row.get("labor_package"), row.get("template_bucket"), row.get("package"))
    key = normalize_key(direct)
    if key in LABOR_TASK_TERMS:
        return key
    text = row_text(row)
    for task, terms in LABOR_TASK_TERMS.items():
        if any(term in text for term in terms):
            return task
    return key or "unknown"


def first_nonblank(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text and text.lower() not in {"nan", "none", "null", "-", "—"}:
            return text
    return ""


def row_matches_terms(row: dict[str, Any], terms: tuple[str, ...]) -> bool:
    text = row_text(row)
    compact = text.replace(" ", "_")
    return any(term in text or term.replace(" ", "_") in compact for term in terms)


def row_matches_package(row: dict[str, Any], package: str) -> bool:
    package_key = normalize_key(package)
    terms = PACKAGE_TERMS.get(package_key, (package_key.replace("_", " "), package_key))
    return row_matches_terms(row, terms)


def row_matches_labor_task(row: dict[str, Any], task: str) -> bool:
    task_key = normalize_key(task)
    terms = LABOR_TASK_TERMS.get(task_key, (task_key.replace("_", " "), task_key))
    return row_matches_terms(row, terms)


def estimate_sqft_from_recommendation(recommendation: dict[str, Any]) -> float | None:
    parsed = recommendation.get("parsed_fields") or {}
    header = (recommendation.get("draft_workbook_inputs") or {}).get("header") or {}
    return (
        safe_float(parsed.get("surface_area_sqft"))
        or safe_float(parsed.get("estimated_sqft"))
        or safe_float(header.get("C12_estimated_sqft"))
    )


def recommendation_scope_template_type(recommendation: dict[str, Any]) -> str:
    parsed = recommendation.get("parsed_fields") or {}
    text = " ".join(
        lower_text(value)
        for value in (
            parsed.get("project_type"),
            parsed.get("division"),
            parsed.get("substrate"),
            parsed.get("coating_type"),
            " ".join(recommendation.get("recommended_scope") or []),
        )
    )
    if any(term in text for term in ("insulation", "spray foam", "closed cell", "open cell", "dc315", "thermal barrier")):
        return "insulation"
    if any(term in text for term in ROOFING_SOURCE_SIGNALS) and ("roof" in text or "roofing" in text):
        return "roofing"
    return ""


def audit_evidence_template_type(row: dict[str, Any]) -> str:
    text = normalize_key(first_nonblank(row.get("template_type"), row.get("job_template_type"), row.get("template_name"))).replace("_", " ")
    if text in {"roof", "roofing", "roof coating"}:
        return "roofing"
    if text in {"insulation", "foam", "spray foam"}:
        return "insulation"
    if text in {"unknown", "none", "null"}:
        return ""
    return text


def audit_evidence_source_text(row: dict[str, Any]) -> str:
    return " ".join(
        lower_text(row.get(column))
        for column in (
            "source_file",
            "folder_path",
            "relative_path",
            "job_name",
            "customer",
            "estimate_file",
            "document_name",
        )
    )


def audit_evidence_scope_match(row: dict[str, Any], scope_template_type: str) -> tuple[bool, str, str]:
    evidence_type = audit_evidence_template_type(row)
    source_text = audit_evidence_source_text(row)
    if scope_template_type == "roofing":
        if evidence_type == "insulation":
            return False, evidence_type, "Template type mismatch: roofing scope cannot use insulation evidence."
        if any(term in source_text for term in INSULATION_SOURCE_SIGNALS):
            return False, evidence_type, "Source path/name mismatch: roofing scope cannot use insulation source evidence."
        if evidence_type and evidence_type != "roofing":
            return False, evidence_type, f"Template type mismatch: roofing scope cannot use {evidence_type} evidence."
    if scope_template_type == "insulation" and evidence_type == "roofing":
        return False, evidence_type, "Template type mismatch: insulation scope cannot use roofing evidence."
    return True, evidence_type, ""


def job_area_map(data: EstimatorData | None) -> dict[str, float]:
    mapping: dict[str, float] = {}
    for attr in ("jobs", "estimates"):
        for row in frame_records(data, attr, limit=100000):
            job_id = first_nonblank(row.get("job_id"))
            if not job_id or job_id in mapping:
                continue
            area = first_positive_float(
                row.get("area_sqft"),
                row.get("estimated_sqft"),
                row.get("surface_area_sqft"),
                row.get("roof_area_sqft"),
                row.get("sqft"),
            )
            if area is not None:
                mapping[job_id] = area
    return mapping


def first_positive_float(*values: Any) -> float | None:
    for value in values:
        number = safe_float(value)
        if number is not None and number > 0:
            return number
    return None


def evidence_area(row: dict[str, Any], areas_by_job: dict[str, float]) -> float | None:
    area = first_positive_float(
        row.get("area_sqft"),
        row.get("estimated_sqft"),
        row.get("surface_area_sqft"),
        row.get("job_area_sqft"),
        row.get("roof_area_sqft"),
    )
    if area is not None:
        return area
    job_id = first_nonblank(row.get("job_id"))
    return areas_by_job.get(job_id)


def evidence_quantity(row: dict[str, Any]) -> float | None:
    return first_positive_float(
        row.get("quantity"),
        row.get("estimated_units"),
        row.get("total_quantity"),
        row.get("median_quantity"),
    )


def evidence_cost(row: dict[str, Any]) -> float | None:
    return first_positive_float(
        row.get("estimated_cost"),
        row.get("total_cost"),
        row.get("extended_cost"),
        row.get("line_total"),
        row.get("median_cost"),
        row.get("median_estimated_cost"),
    )


def unit_is_physical_quantity(unit: Any) -> bool:
    text = normalize_key(unit)
    if not text:
        return False
    rejected = {
        "allowance",
        "dollar",
        "dollars",
        "usd",
        "sf",
        "sqft",
        "square_feet",
        "square_foot",
        "ls",
        "lump_sum",
        "lot",
        "ea_allowance",
    }
    return text not in rejected


def physical_quantity_valid(row: dict[str, Any]) -> bool:
    explicit = row.get("physical_quantity_valid")
    if isinstance(explicit, bool):
        return explicit
    if lower_text(explicit) in {"true", "yes", "1"}:
        return True
    if lower_text(explicit) in {"false", "no", "0"}:
        return False
    source_type = normalize_key(row.get("source_type"))
    if source_type in {"cost_allowance", "cost_allowance_ratio", "labor_budget", "derived_ratio", "unknown"}:
        return False
    quantity = evidence_quantity(row)
    return bool(quantity and unit_is_physical_quantity(row.get("unit")))


def material_plan_rows(recommendation: dict[str, Any]) -> list[dict[str, Any]]:
    draft_rows = ((recommendation.get("draft_workbook_inputs") or {}).get("material_rows") or [])
    source_rows = draft_rows if draft_rows else recommendation.get("material_plan") or []
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(records_from(source_rows), start=1):
        package = package_for_material_row(row)
        source = first_nonblank(row.get("selected_price_source"), row.get("price_source_type"), row.get("source_type"), row.get("calibration_method"))
        estimated_cost = safe_float(row.get("estimated_cost"))
        sanity = first_nonblank(row.get("sanity_check_status"))
        included = (
            bool(row.get("included_in_total"))
            if "included_in_total" in row
            else estimated_cost is not None and source != "rejected_historical_quantity_ratio" and not sanity.lower().startswith("blocked")
        )
        rows.append(
            {
                "row_number": index,
                "package": package,
                "item": first_nonblank(row.get("item"), row.get("product_name"), row.get("selected_item_name"), row.get("row_label")),
                "category": row.get("category"),
                "quantity": row.get("quantity"),
                "unit": row.get("unit"),
                "unit_price": row.get("unit_price"),
                "estimated_cost": row.get("estimated_cost"),
                "cost_low": row.get("cost_low"),
                "cost_high": row.get("cost_high"),
                "selected_method": source,
                "source_type": first_nonblank(row.get("source_type"), row.get("price_source_type"), source),
                "quantity_source": row.get("quantity_source"),
                "unit_price_source": row.get("unit_price_source"),
                "selected_material_calibration_field": row.get("selected_material_calibration_field"),
                "chosen_material_quantity_fields": row.get("chosen_material_quantity_fields"),
                "rejected_material_evidence_counts_by_reason": row.get("rejected_material_evidence_counts_by_reason"),
                "valid_quantity_ratio_count": row.get("valid_quantity_ratio_count"),
                "rejected_quantity_ratio_count": row.get("rejected_quantity_ratio_count"),
                "review_required": bool(row.get("review_required") or row.get("needs_review")),
                "included_in_total": included,
                "applies_reason": row.get("applies_reason"),
                "matched_comparable_job_count": row.get("matched_comparable_job_count"),
                "evidence_count": row.get("evidence_count"),
                "sanity_check_status": row.get("sanity_check_status") or "not_checked",
                "notes": row.get("notes"),
                "raw_row": row,
            }
        )
    return rows


def labor_plan_rows(recommendation: dict[str, Any]) -> list[dict[str, Any]]:
    draft_rows = ((recommendation.get("draft_workbook_inputs") or {}).get("labor_rows") or [])
    source_rows = draft_rows if draft_rows else recommendation.get("labor_plan") or []
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(records_from(source_rows), start=1):
        task = labor_task_for_row(row)
        rows.append(
            {
                "row_number": index,
                "task": task,
                "labor_package": first_nonblank(row.get("labor_package"), row.get("task"), task),
                "base_days": row.get("base_days"),
                "adjusted_days": row.get("adjusted_days") or row.get("crew_days"),
                "crew_size": row.get("crew_size"),
                "total_hours": row.get("total_hours"),
                "estimated_cost": row.get("estimated_cost"),
                "evidence_count": row.get("evidence_count"),
                "calibration_method": first_nonblank(row.get("calibration_method"), row.get("source_type")),
                "selection_level": row.get("selection_level"),
                "labor_bucket_role": row.get("labor_bucket_role"),
                "labor_selection_status": row.get("labor_selection_status"),
                "labor_selection_reason": row.get("labor_selection_reason"),
                "median_hours_per_1000_sqft": row.get("median_hours_per_1000_sqft"),
                "capped_hours": row.get("capped_hours"),
                "review_required": bool(row.get("review_required") or row.get("needs_review")),
                "applies_reason": row.get("applies_reason"),
                "source_type": first_nonblank(row.get("source_type"), row.get("calibration_method")),
                "sanity_check_status": row.get("sanity_check_status") or "not_checked",
                "notes": row.get("notes"),
                "raw_row": row,
            }
        )
    return rows


def current_pricing_match_count(data: EstimatorData | None, package: str) -> int:
    count = 0
    seen: set[str] = set()
    for attr in ("pricing_catalog", "pricing"):
        for row in frame_records(data, attr, limit=100000):
            key = json.dumps(row, default=str, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            if lower_text(row.get("is_current")) in {"false", "0", "no"}:
                continue
            if lower_text(row.get("needs_review")) in {"true", "1", "yes"}:
                continue
            if row_matches_package(row, package):
                count += 1
    return count


def material_evidence_rows(data: EstimatorData | None, packages: set[str], *, scope_template_type: str = "") -> list[dict[str, Any]]:
    areas_by_job = job_area_map(data)
    rows: list[dict[str, Any]] = []
    sources = (
        "template_rows",
        "classified_line_items",
        "line_item_classifications",
        "job_package_summary",
        "pricing_catalog",
        "pricing",
    )
    for source in sources:
        for row in frame_records(data, source, limit=100000):
            row["_audit_row_text"] = row_text(row)
            matched_packages = [package for package in packages if package != "unknown" and row_matches_package(row, package)]
            if not matched_packages and not packages:
                matched_packages = [package_for_material_row(row)]
            for package in matched_packages:
                area = evidence_area(row, areas_by_job)
                quantity = evidence_quantity(row)
                cost = evidence_cost(row)
                qty_per_sqft = quantity / area if quantity and area else safe_float(row.get("qty_per_sqft"))
                cost_per_sqft = cost / area if cost and area else safe_float(row.get("cost_per_sqft"))
                physical_valid = physical_quantity_valid(row)
                source_type = first_nonblank(row.get("source_type"))
                template_match, evidence_type, template_rejected_reason = audit_evidence_scope_match(row, scope_template_type)
                rejected_reason = ""
                included_as_evidence = template_match
                if template_rejected_reason:
                    rejected_reason = template_rejected_reason
                if not physical_valid and quantity:
                    rejected_reason = rejected_reason or "Quantity is not a trusted physical quantity for estimator ratios."
                rows.append(
                    {
                        "package": package,
                        "scope_template_type": scope_template_type,
                        "evidence_template_type": evidence_type,
                        "template_type_match": template_match,
                        "evidence_source_table": row.get("evidence_source_table") or source,
                        "job_id": row.get("job_id"),
                        "source_file": row.get("source_file") or row.get("estimate_file"),
                        "template_type": row.get("template_type"),
                        "template_bucket": row.get("template_bucket") or row.get("package"),
                        "row_label": row.get("row_label") or row.get("line_item_name"),
                        "selected_item_name": first_nonblank(row.get("selected_item_name"), row.get("item_name"), row.get("product_name")),
                        "quantity": quantity,
                        "unit": row.get("unit"),
                        "unit_price": first_positive_float(row.get("unit_price"), row.get("unit_cost"), row.get("price_per_gallon"), row.get("price_per_unit"), row.get("matched_price")),
                        "estimated_cost": cost,
                        "area_sqft": area,
                        "qty_per_sqft": qty_per_sqft,
                        "cost_per_sqft": cost_per_sqft,
                        "source_type": source_type,
                        "physical_quantity_valid": physical_valid,
                        "included_as_evidence": included_as_evidence,
                        "rejected_reason": rejected_reason,
                        "match_reason": f"Matched {package} terms.",
                    }
                )
    if not rows:
        rows.append({"package": "", "evidence_source_table": "", "message": "No matching material evidence rows found."})
    return rows


def material_calibration_rows(recommendation: dict[str, Any], material_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calibration = recommendation.get("historical_calibration") or {}
    detail = calibration.get("material_calibration") if isinstance(calibration, dict) else None
    if not isinstance(detail, dict):
        return []
    rows: list[dict[str, Any]] = []
    selected_by_package = {row.get("package"): row for row in material_rows}
    for package, package_detail in detail.items():
        if not isinstance(package_detail, dict):
            continue
        selected = selected_by_package.get(package, {})
        rows.append(
            {
                "package": package,
                "evidence_source_table": "recommendation.historical_calibration.material_calibration",
                "candidate_physical_rows_count": package_detail.get("candidate_physical_rows_count"),
                "valid_quantity_ratio_count": package_detail.get("valid_quantity_ratio_count"),
                "historical_physical_quantity_rows_considered": package_detail.get("historical_physical_quantity_rows_considered"),
                "historical_cost_fallback_rows_considered": package_detail.get("historical_cost_fallback_rows_considered"),
                "historical_cost_ratio_was_used": "historical_cost_ratio" in lower_text(selected.get("selected_method")),
                "current_pricing_item_selected": (package_detail.get("selected_current_price_item") or {}).get("product_name")
                if isinstance(package_detail.get("selected_current_price_item"), dict)
                else "",
                "median_quantity_per_sqft": package_detail.get("median_quantity_per_sqft"),
                "median_cost_per_sqft": package_detail.get("median_cost_per_sqft"),
                "quantity_ratio_rejection_reasons": "; ".join(str(item) for item in package_detail.get("quantity_ratio_rejection_reasons") or []),
                "included_as_evidence": True,
                "match_reason": "Attached estimator material calibration detail.",
            }
        )
    return rows


def count_cost_ratio_evidence(rows: list[dict[str, Any]], package: str) -> int:
    count = 0
    for row in rows:
        if row.get("package") != package:
            continue
        if row.get("included_as_evidence") is False:
            continue
        source_type = normalize_key(row.get("source_type"))
        if source_type in {"cost_allowance", "cost_allowance_ratio", "derived_ratio"} or safe_float(row.get("cost_per_sqft")):
            count += 1
    return count


def count_physical_quantity_evidence(rows: list[dict[str, Any]], package: str) -> int:
    return sum(
        1
        for row in rows
        if row.get("package") == package
        and row.get("included_as_evidence") is not False
        and row.get("physical_quantity_valid")
        and safe_float(row.get("qty_per_sqft")) is not None
    )


def build_material_audit(recommendation: dict[str, Any], data: EstimatorData | None, material_rows: list[dict[str, Any]], evidence_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    audit_rows: list[dict[str, Any]] = []
    coating_costs = [
        safe_float(row.get("estimated_cost"))
        for row in material_rows
        if row.get("package") == "coating" and safe_float(row.get("estimated_cost")) is not None
    ]
    coating_cost = max(coating_costs) if coating_costs else None
    for row in material_rows:
        package = row.get("package") or "unknown"
        selected_method = lower_text(row.get("selected_method"))
        current_count = current_pricing_match_count(data, package)
        physical_count = count_physical_quantity_evidence(evidence_rows, package)
        cost_count = count_cost_ratio_evidence(evidence_rows, package)
        mismatched_used = [
            evidence
            for evidence in evidence_rows
            if evidence.get("package") == package
            and evidence.get("template_type_match") is False
            and evidence.get("included_as_evidence") is True
        ]
        status = "PASS"
        issue = ""
        recommendation_text = "Selection has no obvious calibration audit issue."
        expected_method = row.get("selected_method")

        if "historical_cost_ratio" in selected_method:
            if row.get("included_in_total") is True:
                status = "FAIL"
                issue = "historical_cost_ratio_fallback_included_in_total"
                expected_method = "review_only_historical_cost_ratio_fallback"
                recommendation_text = "Historical cost-per-sqft evidence must be review-only unless cost-ratio pricing is explicitly allowed."
            elif physical_count > 0 and current_count > 0:
                status = "FAIL"
                issue = "historical_cost_ratio_used_despite_physical_quantity_and_current_pricing"
                expected_method = "current_pricing * historical_physical_quantity_ratio"
                recommendation_text = "Use physical quantity ratios with current pricing; keep cost allowance ratios as review-only support."
            else:
                status = "PASS"
                issue = "historical_cost_ratio_fallback_used"
                recommendation_text = "Historical cost ratio was shown as review-only and excluded from the base total."
            if current_count > 0 and physical_count == 0 and row.get("included_in_total") is True:
                status = "FAIL"
                issue = "current_pricing_exists_but_cost_ratio_priced_without_quantity"
                recommendation_text = "Current pricing exists, but no valid physical quantity evidence was available; do not price a cost ratio into total."
        if mismatched_used:
            status = "FAIL"
            issue = "nonmatching_template_type_evidence_included"
            recommendation_text = "Evidence from a nonmatching template type must be rejected for this scope."
        if row.get("selected_method") == "rejected_historical_quantity_ratio" and safe_float(row.get("estimated_cost")) is not None:
            status = "FAIL"
            issue = "rejected_historical_quantity_ratio_still_priced"
            recommendation_text = "Rejected quantity evidence must not affect the estimate total."
        if lower_text(row.get("sanity_check_status")).startswith("blocked") and safe_float(row.get("estimated_cost")) is not None:
            status = "FAIL"
            issue = "blocked_sanity_check_row_still_priced"
            recommendation_text = "Blocked material rows should be excluded from the base estimate."
        if row.get("package") == "primer" and unit_is_physical_quantity(row.get("unit")) and normalize_key(row.get("unit")) in {"pail", "pails", "drum", "drums"}:
            quantity = safe_float(row.get("quantity"))
            area = estimate_sqft_from_recommendation(recommendation)
            if quantity and area:
                sqft_per_unit = area / quantity
                if sqft_per_unit < 100:
                    status = "FAIL"
                    issue = "implausible_primer_sqft_per_unit"
                    recommendation_text = "Primer physical quantity appears implausible; remove from base estimate and review source evidence."
        estimated_cost = safe_float(row.get("estimated_cost"))
        if coating_cost and estimated_cost and package != "coating" and estimated_cost > coating_cost * 2 and "manual_override" not in selected_method:
            status = "FAIL"
            issue = "material_cost_exceeds_2x_coating_without_manual_override"
            recommendation_text = "Secondary material cost is too large relative to coating; exclude or require manual override."

        audit_rows.append(
            {
                "package": package,
                "status": status,
                "issue": issue,
                "recommendation": recommendation_text,
                "evidence_count": row.get("evidence_count"),
                "current_pricing_match_count": current_count,
                "physical_quantity_evidence_count": physical_count,
                "cost_ratio_evidence_count": cost_count,
                "selected_method": row.get("selected_method"),
                "expected_method": expected_method,
                "included_in_total": row.get("included_in_total"),
                "row_reference": row.get("row_number"),
                "item": row.get("item"),
                "estimated_cost": row.get("estimated_cost"),
                "review_required": row.get("review_required"),
            }
        )
    if not audit_rows:
        audit_rows.append(
            {
                "package": "",
                "status": "WARN",
                "issue": "no_material_plan",
                "recommendation": "No material plan rows were available to audit.",
            }
        )
    return audit_rows


def row_labor_hours(row: dict[str, Any]) -> float | None:
    hours = first_positive_float(row.get("total_hours"), row.get("median_total_hours"), row.get("labor_hours"))
    if hours is not None:
        return hours
    days = first_positive_float(row.get("days"), row.get("median_days"), row.get("adjusted_days"), row.get("base_days"))
    crew = first_positive_float(row.get("crew_size"), row.get("median_crew_size"))
    if days and crew and crew <= 8:
        return days * crew * 8
    return None


def labor_numeric_valid(row: dict[str, Any], areas_by_job: dict[str, float]) -> tuple[bool, str]:
    hours = row_labor_hours(row)
    crew = first_positive_float(row.get("crew_size"), row.get("median_crew_size"))
    area = evidence_area(row, areas_by_job)
    if crew and crew > 8:
        return False, "crew_size_gt_8"
    if hours is None or hours <= 0:
        return False, "missing_or_nonpositive_hours"
    if area and hours / area > 0.5:
        return False, "hours_per_sqft_implausibly_high"
    return True, ""


def labor_evidence_rows(data: EstimatorData | None, tasks: set[str], *, scope_template_type: str = "") -> list[dict[str, Any]]:
    areas_by_job = job_area_map(data)
    rows: list[dict[str, Any]] = []
    for source in ("relationship_labor_rates", "job_package_summary", "template_rows"):
        for row in frame_records(data, source, limit=100000):
            row["_audit_row_text"] = row_text(row)
            matched_tasks = [task for task in tasks if row_matches_labor_task(row, task)]
            if not matched_tasks:
                continue
            for task in matched_tasks:
                area = evidence_area(row, areas_by_job)
                hours = row_labor_hours(row)
                cost = evidence_cost(row)
                valid, reason = labor_numeric_valid(row, areas_by_job)
                template_match, evidence_type, template_rejected_reason = audit_evidence_scope_match(row, scope_template_type)
                included_as_evidence = bool(valid and template_match)
                rejected_reason = template_rejected_reason or reason
                rows.append(
                    {
                        "task": task,
                        "scope_template_type": scope_template_type,
                        "evidence_template_type": evidence_type,
                        "template_type_match": template_match,
                        "evidence_source_table": row.get("evidence_source_table") or source,
                        "job_id": row.get("job_id"),
                        "source_file": row.get("source_file") or row.get("estimate_file"),
                        "template_type": row.get("template_type"),
                        "template_bucket": row.get("template_bucket") or row.get("package") or row.get("labor_package"),
                        "row_label": row.get("row_label") or row.get("line_item_name"),
                        "area_sqft": area,
                        "days": first_positive_float(row.get("days"), row.get("median_days")),
                        "crew_size": first_positive_float(row.get("crew_size"), row.get("median_crew_size")),
                        "total_hours": hours,
                        "estimated_cost": cost,
                        "hours_per_sqft": hours / area if hours and area else safe_float(row.get("hours_per_sqft")),
                        "cost_per_sqft": cost / area if cost and area else safe_float(row.get("cost_per_sqft")),
                        "source_type": row.get("source_type"),
                        "included_as_evidence": included_as_evidence,
                        "rejected_reason": rejected_reason,
                        "match_reason": f"Matched {task} terms.",
                    }
                )
    if not rows:
        rows.append({"task": "", "evidence_source_table": "", "message": "No matching labor evidence rows found."})
    return rows


def labor_tasks_to_audit(recommendation: dict[str, Any], labor_rows: list[dict[str, Any]]) -> list[str]:
    parsed = recommendation.get("parsed_fields") or {}
    scope_text = " ".join(
        lower_text(value)
        for value in (
            parsed.get("project_type"),
            parsed.get("division"),
            parsed.get("substrate"),
            parsed.get("coating_type"),
            " ".join(recommendation.get("recommended_scope") or []),
        )
    )
    if "roof" in scope_text and "coating" in scope_text:
        tasks = list(EXPECTED_ROOF_COATING_LABOR_TASKS)
        if any(row.get("package") == "primer" for row in material_plan_rows(recommendation)):
            tasks.insert(1, "labor_prime")
        return tasks
    return sorted({row.get("task") for row in labor_rows if row.get("task")})


def build_labor_audit(recommendation: dict[str, Any], labor_rows: list[dict[str, Any]], evidence_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows_by_task = {row.get("task"): row for row in labor_rows}
    debug = recommendation.get("debug") or {}
    labor_debug = debug.get("labor_calibration") if isinstance(debug, dict) else {}
    selection_rows = records_from(labor_debug.get("selection_rows") if isinstance(labor_debug, dict) else [])
    selection_by_task = {row.get("task"): row for row in selection_rows if row.get("task")}
    audit_rows: list[dict[str, Any]] = []
    for task in labor_tasks_to_audit(recommendation, labor_rows):
        selected = rows_by_task.get(task)
        selection = selection_by_task.get(task) or {}
        matching_evidence = [row for row in evidence_rows if row.get("task") == task]
        valid_count = sum(1 for row in matching_evidence if row.get("included_as_evidence"))
        rejected_count = sum(1 for row in matching_evidence if row.get("rejected_reason"))
        mismatched_included = [row for row in matching_evidence if row.get("template_type_match") is False and row.get("included_as_evidence") is True]
        selected_method = lower_text(selected.get("calibration_method") if selected else "")
        status = "PASS"
        issue = ""
        recommendation_text = "Labor task calibration has no obvious audit issue."
        if not selected:
            status = "WARN"
            issue = "expected_labor_task_missing"
            recommendation_text = "Expected roof coating labor task is missing from the plan."
        elif selected_method == "rule_based_fallback" and valid_count > 0:
            status = "FAIL"
            issue = "fallback_used_despite_valid_historical_evidence"
            recommendation_text = "Use historical labor evidence before rule-based fallback."
        elif selected_method == "rule_based_fallback":
            status = "WARN"
            issue = "rule_based_fallback_used"
            recommendation_text = "No valid historical evidence was selected; verify evidence availability."
        elif mismatched_included:
            status = "FAIL"
            issue = "nonmatching_template_type_evidence_included"
            recommendation_text = "Labor task used evidence from a nonmatching template type."
        elif valid_count == 0:
            status = "WARN"
            issue = "selected_without_matching_audit_evidence"
            recommendation_text = "Selected task did not have matching evidence rows in the audit sample."

        audit_rows.append(
            {
                "task": task,
                "status": status,
                "issue": issue,
                "recommendation": recommendation_text,
                "selected_method": selected.get("calibration_method") if selected else "",
                "selection_level": selected.get("selection_level") if selected else "",
                "labor_selection_status": selected.get("labor_selection_status") if selected else selection.get("selection_status"),
                "labor_bucket_role": selected.get("labor_bucket_role") if selected else selection.get("labor_bucket_role"),
                "labor_selection_reason": selected.get("labor_selection_reason") if selected else selection.get("reason"),
                "evidence_count": selected.get("evidence_count") if selected else 0,
                "valid_historical_evidence_count": valid_count,
                "rejected_evidence_count": rejected_count,
                "median_hours_per_1000_sqft": selected.get("median_hours_per_1000_sqft") if selected else selection.get("median_hours_per_1000_sqft"),
                "capped_hours": selected.get("capped_hours") if selected else selection.get("capped_hours"),
                "estimated_hours": selected.get("total_hours") if selected else None,
                "estimated_cost": selected.get("estimated_cost") if selected else None,
                "total_labor_hours": None,
                "labor_hours_per_1000_sqft": None,
                "row_reference": selected.get("row_number") if selected else "",
            }
        )
    audited_tasks = {row.get("task") for row in audit_rows}
    for selection in selection_rows:
        task = selection.get("task")
        if not task or task in audited_tasks or selection.get("selected"):
            continue
        audit_rows.append(
            {
                "task": task,
                "status": "INFO",
                "issue": "labor_bucket_rejected_by_selection_layer",
                "recommendation": selection.get("reason") or "Bucket was rejected by roof coating labor selection.",
                "selected_method": selection.get("calibration_method"),
                "selection_level": selection.get("selection_level"),
                "labor_selection_status": "rejected",
                "labor_bucket_role": selection.get("labor_bucket_role"),
                "labor_selection_reason": selection.get("reason"),
                "evidence_count": selection.get("evidence_count"),
                "valid_historical_evidence_count": "",
                "rejected_evidence_count": "",
                "median_hours_per_1000_sqft": selection.get("median_hours_per_1000_sqft"),
                "capped_hours": selection.get("capped_hours"),
                "estimated_hours": selection.get("median_total_hours"),
                "estimated_cost": None,
                "total_labor_hours": None,
                "labor_hours_per_1000_sqft": None,
                "row_reference": "",
            }
        )
        audited_tasks.add(task)
    area = estimate_sqft_from_recommendation(recommendation)
    total_hours = sum(safe_float(row.get("total_hours")) or 0 for row in labor_rows)
    if area and total_hours:
        hours_per_1000 = total_hours / area * 1000
        parsed_text = " ".join(lower_text(value) for value in (recommendation.get("parsed_fields") or {}).values())
        review_text = " ".join(lower_text(flag) for flag in recommendation.get("review_flags") or [])
        complex_scope = any(term in f"{parsed_text} {review_text}" for term in ("poor", "tear-off", "tear off", "foam", "granules", "major repair"))
        if not complex_scope and hours_per_1000 > 80:
            audit_rows.append(
                {
                    "task": "TOTAL_LABOR",
                    "status": "FAIL",
                    "issue": "labor_hours_per_1000_sqft_exceeds_simple_roof_threshold",
                    "recommendation": "Reject overbroad labor calibration for simple roof coating scopes.",
                    "selected_method": "aggregate",
                    "selection_level": "",
                    "evidence_count": "",
                    "valid_historical_evidence_count": "",
                    "rejected_evidence_count": "",
                    "estimated_hours": round(total_hours, 2),
                    "estimated_cost": sum(safe_float(row.get("estimated_cost")) or 0 for row in labor_rows),
                    "total_labor_hours": round(total_hours, 2),
                    "labor_hours_per_1000_sqft": round(hours_per_1000, 2),
                    "row_reference": "",
                }
            )
        elif not complex_scope and hours_per_1000 > 50:
            audit_rows.append(
                {
                    "task": "TOTAL_LABOR",
                    "status": "WARN",
                    "issue": "labor_hours_per_1000_sqft_above_simple_roof_warning_threshold",
                    "recommendation": "Review labor calibration for possible overbroad evidence.",
                    "selected_method": "aggregate",
                    "selection_level": "",
                    "evidence_count": "",
                    "valid_historical_evidence_count": "",
                    "rejected_evidence_count": "",
                    "estimated_hours": round(total_hours, 2),
                    "estimated_cost": sum(safe_float(row.get("estimated_cost")) or 0 for row in labor_rows),
                    "total_labor_hours": round(total_hours, 2),
                    "labor_hours_per_1000_sqft": round(hours_per_1000, 2),
                    "row_reference": "",
                }
            )
    if not audit_rows:
        audit_rows.append(
            {
                "task": "",
                "status": "WARN",
                "issue": "no_labor_plan",
                "recommendation": "No labor plan rows were available to audit.",
            }
        )
    return audit_rows


def similar_jobs_audit_rows(recommendation: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(records_from(recommendation.get("similar_examples")), start=1):
        reason = lower_text(row.get("reason_matched") or row.get("reason"))
        text = " ".join(lower_text(row.get(key)) for key in ("customer", "job_name", "job_type", "division", "scope", "reason_matched"))
        strong_reasons = [term for term in ("substrate", "coating", "warranty", "package", "roof coating", "project type") if term in reason or term in text]
        weak_reasons = [term for term in ("division", "location", "city", "size") if term in reason]
        price_per_sqft = safe_float(row.get("price_per_sqft"))
        outlier_reasons: list[str] = []
        if price_per_sqft is not None and (price_per_sqft < 1 or price_per_sqft > 75):
            outlier_reasons.append("price_per_sqft_outside_expected_range")
        if any(term in text for term in ("all trades", "facade", "façade", "tenant improvement", "interior only")):
            outlier_reasons.append("scope_mismatch_keyword")
        match_strength = "strong" if strong_reasons else "weak" if weak_reasons else "unknown"
        included_as_evidence = bool(row.get("included_as_evidence"))
        exclusion_reason = first_nonblank(row.get("exclusion_reason"))
        if match_strength != "strong" and not exclusion_reason:
            exclusion_reason = "Weak-only similar job match; not used as estimator evidence."
        if outlier_reasons:
            included_as_evidence = False
            exclusion_reason = "; ".join(outlier_reasons)
        rows.append(
            {
                "rank": index,
                "job_id": row.get("job_id"),
                "customer": row.get("customer"),
                "job_name": row.get("job_name"),
                "division": row.get("division"),
                "job_type": row.get("job_type"),
                "estimated_sqft": row.get("estimated_sqft"),
                "price_per_sqft": row.get("price_per_sqft"),
                "similarity_score": row.get("similarity_score"),
                "reason_matched": row.get("reason_matched") or row.get("reason"),
                "match_strength": match_strength,
                "strong_reason_count": len(strong_reasons),
                "weak_reason_count": len(weak_reasons),
                "included_as_evidence": included_as_evidence,
                "exclusion_reason": exclusion_reason,
                "outlier_flag": bool(outlier_reasons),
                "outlier_reason": "; ".join(outlier_reasons),
                "used_for_material_calibration": "",
                "used_for_labor_calibration": "",
            }
        )
    if not rows:
        rows.append({"message": "No similar jobs were attached to the recommendation."})
    return rows


def relationship_rows_sample(data: EstimatorData | None, limit_per_table: int = 100) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for table_name in (
        "relationship_material_qty_ratios",
        "relationship_labor_rates",
        "job_package_summary",
        "relationship_package_cooccurrence",
        "relationship_warranty_coating",
        "template_rows",
    ):
        table_rows = frame_records(data, table_name, source=table_name, limit=limit_per_table)
        if table_rows:
            rows.extend(table_rows)
        else:
            rows.append({"evidence_source_table": table_name, "message": "No rows loaded for this source."})
    return rows


def rejected_evidence_rows(recommendation: dict[str, Any], material_audit: list[dict[str, Any]], labor_audit: list[dict[str, Any]], material_evidence: list[dict[str, Any]], labor_evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for audit_type, audit_rows in (("material", material_audit), ("labor", labor_audit)):
        for row in audit_rows:
            if row.get("status") in {"FAIL", "WARN"}:
                rows.append(
                    {
                        "evidence_type": audit_type,
                        "package_or_task": row.get("package") or row.get("task"),
                        "severity": row.get("status"),
                        "reason": row.get("issue"),
                        "recommendation": row.get("recommendation"),
                    }
                )
    for row in material_evidence + labor_evidence:
        if row.get("rejected_reason"):
            rows.append(
                {
                    "evidence_type": "source_row",
                    "package_or_task": row.get("package") or row.get("task"),
                    "severity": "info",
                    "reason": row.get("rejected_reason"),
                    "source": row.get("evidence_source_table"),
                    "job_id": row.get("job_id"),
                }
            )
    for flag in recommendation.get("review_flags") or []:
        text = lower_text(flag)
        if any(term in text for term in ("reject", "blocked", "invalid", "implausible", "missing", "fallback", "review")):
            rows.append({"evidence_type": "review_flag", "severity": "info", "reason": flag})
    if not rows:
        rows.append({"evidence_type": "", "severity": "", "reason": "No rejected or warning evidence was identified."})
    return rows


def build_summary_rows(
    *,
    case_id: str,
    notes: str,
    recommendation: dict[str, Any],
    data: EstimatorData | None,
    material_audit: list[dict[str, Any]],
    labor_audit: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    failures = [row for row in material_audit + labor_audit if row.get("status") == "FAIL"]
    warnings = [row for row in material_audit + labor_audit if row.get("status") == "WARN"]
    first_failure = failures[0] if failures else {}
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    parsed = recommendation.get("parsed_fields") or {}
    header = (recommendation.get("draft_workbook_inputs") or {}).get("header") or {}
    return [
        {
            "generated_at": generated_at,
            "case_id": case_id,
            "notes": notes,
            "estimated_sqft": parsed.get("estimated_sqft") or parsed.get("surface_area_sqft") or header.get("C12_estimated_sqft"),
            "estimate_low": recommendation.get("estimate_low"),
            "estimate_target": recommendation.get("estimate_target"),
            "estimate_high": recommendation.get("estimate_high"),
            "material_rows": len(recommendation.get("material_plan") or []),
            "labor_rows": len(recommendation.get("labor_plan") or []),
            "total_labor_hours": sum(safe_float(row.get("total_hours")) or 0 for row in recommendation.get("labor_plan") or [] if isinstance(row, dict)),
            "labor_hours_per_1000_sqft": (
                sum(safe_float(row.get("total_hours")) or 0 for row in recommendation.get("labor_plan") or [] if isinstance(row, dict))
                / (safe_float(parsed.get("estimated_sqft")) or safe_float(parsed.get("surface_area_sqft")) or safe_float(header.get("C12_estimated_sqft")) or 1)
                * 1000
            ),
            "material_cost_ratio_fallback_total": sum(
                safe_float(row.get("review_estimated_cost")) or safe_float(row.get("estimated_cost")) or 0
                for row in recommendation.get("material_plan") or []
                if isinstance(row, dict) and "historical_cost_ratio" in lower_text(row.get("selected_price_source") or row.get("calibration_method"))
            ),
            "cost_ratio_fallback_included_total": sum(
                safe_float(row.get("estimated_cost")) or 0
                for row in recommendation.get("material_plan") or []
                if isinstance(row, dict)
                and "historical_cost_ratio" in lower_text(row.get("selected_price_source") or row.get("calibration_method"))
                and row.get("included_in_total") is not False
            ),
            "material_audit_failures": sum(1 for row in material_audit if row.get("status") == "FAIL"),
            "labor_audit_failures": sum(1 for row in labor_audit if row.get("status") == "FAIL"),
            "audit_warnings": len(warnings),
            "top_issue": first_failure.get("issue") or (warnings[0].get("issue") if warnings else ""),
            "recommended_next_fix": first_failure.get("recommendation") or (warnings[0].get("recommendation") if warnings else "No high-priority calibration issue found."),
            "data_sources": "; ".join(str(item) for item in getattr(data, "source_files_used", []) or []),
            "data_warnings": "; ".join(str(item) for item in getattr(data, "warnings", []) or []),
        }
    ]


def parsed_scope_rows(recommendation: dict[str, Any]) -> list[dict[str, Any]]:
    parsed = recommendation.get("parsed_fields") or {}
    header = (recommendation.get("draft_workbook_inputs") or {}).get("header") or {}
    row = dict(parsed)
    for key, value in header.items():
        row[f"header_{key}"] = value
    row["review_flags"] = "; ".join(str(flag) for flag in recommendation.get("review_flags") or [])
    return [row]


def ai_scope_rows(recommendation: dict[str, Any]) -> list[dict[str, Any]]:
    debug = recommendation.get("debug") or {}
    ai_debug = debug.get("ai_scope_interpreter") if isinstance(debug, dict) else {}
    if not isinstance(ai_debug, dict):
        return [{"section": "ai_scope_interpreter", "message": "No AI scope interpreter debug data."}]
    rows: list[dict[str, Any]] = [{"section": "enabled", "field": "enabled", "value": ai_debug.get("enabled")}]
    for section in (
        "deterministic_parsed_scope",
        "deterministic_scope",
        "ai_parsed_scope",
        "final_merged_scope",
        "ai_confidence_by_field",
    ):
        value = ai_debug.get(section)
        if isinstance(value, dict):
            for field, field_value in value.items():
                rows.append({"section": section, "field": field, "value": field_value})
        else:
            rows.append({"section": section, "field": "", "value": value})
    for index, decision in enumerate(records_from(ai_debug.get("merge_decisions")), start=1):
        rows.append(
            {
                "section": "merge_decisions",
                "row_number": index,
                "field": decision.get("field"),
                "from": decision.get("from"),
                "to": decision.get("to"),
                "decision": decision.get("decision"),
                "reason": decision.get("reason"),
            }
        )
    for index, flag in enumerate(ai_debug.get("ai_review_flags") or [], start=1):
        rows.append({"section": "ai_review_flags", "row_number": index, "field": "review_flag", "value": flag})
    return rows


def build_calibration_audit(
    recommendation: Any,
    data: EstimatorData | None = None,
    *,
    notes: str = "",
    case_id: str = "estimator_audit",
    evidence_limit: int = 5000,
    fast: bool = False,
    debug_evidence: bool = False,
) -> dict[str, Any]:
    recommendation_dict = object_to_dict(recommendation)
    scope_type = recommendation_scope_template_type(recommendation_dict)
    materials = material_plan_rows(recommendation_dict)
    material_packages = {row.get("package") for row in materials if row.get("package")}
    material_evidence = material_evidence_rows(data, material_packages, scope_template_type=scope_type)
    material_evidence.extend(material_calibration_rows(recommendation_dict, materials))
    material_audit = build_material_audit(recommendation_dict, data, materials, material_evidence)

    labor = labor_plan_rows(recommendation_dict)
    task_set = set(labor_tasks_to_audit(recommendation_dict, labor)) | {row.get("task") for row in labor if row.get("task")}
    labor_evidence = labor_evidence_rows(data, task_set, scope_template_type=scope_type)
    labor_audit = build_labor_audit(recommendation_dict, labor, labor_evidence)
    similar_audit = similar_jobs_audit_rows(recommendation_dict)
    rejected = rejected_evidence_rows(recommendation_dict, material_audit, labor_audit, material_evidence, labor_evidence)

    sheets = {
        "summary": build_summary_rows(
            case_id=case_id,
            notes=notes,
            recommendation=recommendation_dict,
            data=data,
            material_audit=material_audit,
            labor_audit=labor_audit,
        ),
        "parsed_scope": parsed_scope_rows(recommendation_dict),
        "ai_scope_interpreter": ai_scope_rows(recommendation_dict),
        "material_plan": materials,
        "material_audit": material_audit,
        "material_evidence": material_evidence,
        "labor_plan": labor,
        "labor_audit": labor_audit,
        "labor_evidence": labor_evidence,
        "similar_jobs_audit": similar_audit,
        "rejected_evidence": rejected,
        "relationship_rows_sample": relationship_rows_sample(data),
    }
    if not debug_evidence and evidence_limit > 0:
        for sheet_name in ("material_evidence", "labor_evidence", "relationship_rows_sample"):
            sheets[sheet_name] = sheets.get(sheet_name, [])[:evidence_limit]
    if fast and not debug_evidence:
        sheets["relationship_rows_sample"] = [{"message": "Relationship row sample skipped in fast audit mode."}]
        sheets["rejected_evidence"] = [{"message": "Rejected evidence detail skipped in fast audit mode."}]
    return sanitize_for_export(
        {
            "case_id": case_id,
            "notes": notes,
            "summary": sheets["summary"][0] if sheets["summary"] else {},
            "sheets": sheets,
        },
        excel=False,
    )


def safe_filename(value: str | None, default: str = "estimator_audit") -> str:
    text = lower_text(value or default)
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text[:90] or default


def sheet_dataframe(rows: list[dict[str, Any]]) -> pd.DataFrame:
    excel_rows = [sanitize_for_export(row, excel=True) for row in rows]
    frame = pd.DataFrame(excel_rows)
    if frame.empty:
        return pd.DataFrame([{"message": "No rows"}])
    for column in frame.columns:
        if frame[column].dtype == "object":
            frame[column] = frame[column].map(
                lambda value: json.dumps(value, default=str, sort_keys=True) if isinstance(value, (dict, list, tuple, set)) else value
            )
    return frame


def write_calibration_audit(
    audit: dict[str, Any],
    output_dir: Path | str = DEFAULT_OUTPUT_DIR,
    *,
    case_id: str | None = None,
) -> dict[str, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    base = safe_filename(case_id or audit.get("case_id") or "estimator_audit")
    json_path = output_path / f"{base}_audit.json"
    xlsx_path = output_path / f"{base}_audit.xlsx"
    json_export = sanitize_for_export(audit, excel=False)
    json_path.write_text(json.dumps(json_export, indent=2, default=str), encoding="utf-8")
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        for sheet_name in AUDIT_SHEETS:
            rows = sanitize_for_export(audit.get("sheets", {}).get(sheet_name, []), excel=True)
            frame = sheet_dataframe(rows)
            frame.to_excel(writer, sheet_name=sheet_name[:31], index=False)
            worksheet = writer.sheets[sheet_name[:31]]
            worksheet.freeze_panes = "A2"
            for column_cells in worksheet.columns:
                header = str(column_cells[0].value or "")
                width = min(max(len(header) + 2, 12), 56)
                worksheet.column_dimensions[column_cells[0].column_letter].width = width
    return {"json": json_path, "xlsx": xlsx_path}


def load_cases(path: Path = DEFAULT_CASES_PATH) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_case(case_id: str, path: Path = DEFAULT_CASES_PATH) -> dict[str, Any]:
    for case in load_cases(path):
        if case.get("case_id") == case_id:
            return case
    raise SystemExit(f"No estimator eval case found for --case-id {case_id!r}")


def run_audit_for_case(
    *,
    case_id: str,
    database_url: str | None = None,
    out_dir: Path | str = DEFAULT_OUTPUT_DIR,
    cases_path: Path = DEFAULT_CASES_PATH,
    data: EstimatorData | None = None,
    evidence_limit: int = 5000,
    fast: bool = False,
    debug_evidence: bool = False,
) -> dict[str, Path]:
    case = find_case(case_id, cases_path)
    if data is None:
        data = load_estimator_data(REPO_ROOT, database_url=database_url, prefer_database=bool(database_url))
    recommendation = estimate_from_field_notes(case["notes"], {}, data=data)
    audit = build_calibration_audit(
        recommendation,
        data,
        notes=case["notes"],
        case_id=case_id,
        evidence_limit=evidence_limit,
        fast=fast,
        debug_evidence=debug_evidence,
    )
    return write_calibration_audit(audit, out_dir, case_id=case_id)


def run_audit_for_notes(
    *,
    notes: str,
    case_id: str = "ad_hoc",
    database_url: str | None = None,
    out_dir: Path | str = DEFAULT_OUTPUT_DIR,
    evidence_limit: int = 5000,
    fast: bool = False,
    debug_evidence: bool = False,
) -> dict[str, Path]:
    data = load_estimator_data(REPO_ROOT, database_url=database_url, prefer_database=bool(database_url))
    recommendation = estimate_from_field_notes(notes, {}, data=data)
    audit = build_calibration_audit(
        recommendation,
        data,
        notes=notes,
        case_id=case_id,
        evidence_limit=evidence_limit,
        fast=fast,
        debug_evidence=debug_evidence,
    )
    return write_calibration_audit(audit, out_dir, case_id=case_id)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build estimator calibration audit evidence for a field-notes case.")
    parser.add_argument("--case-id")
    parser.add_argument("--notes")
    parser.add_argument("--database-url", default=os.getenv("NEON_DATABASE_URL") or os.getenv("DATABASE_URL"))
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--fast", action="store_true", help="Skip verbose audit diagnostics and large evidence samples.")
    parser.add_argument("--evidence-limit", type=int, default=50, help="Maximum evidence rows to keep per detailed audit sheet.")
    parser.add_argument("--debug-evidence", action="store_true", help="Include full diagnostic evidence even when --fast is set.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.case_id and not args.notes:
        raise SystemExit("Provide --case-id or --notes.")
    if args.case_id:
        paths = run_audit_for_case(
            case_id=args.case_id,
            database_url=args.database_url,
            out_dir=args.out_dir,
            cases_path=args.cases,
            evidence_limit=args.evidence_limit,
            fast=args.fast,
            debug_evidence=args.debug_evidence,
        )
    else:
        paths = run_audit_for_notes(
            notes=args.notes,
            case_id="ad_hoc",
            database_url=args.database_url,
            out_dir=args.out_dir,
            evidence_limit=args.evidence_limit,
            fast=args.fast,
            debug_evidence=args.debug_evidence,
        )
    print(f"Estimator audit JSON: {paths['json']}")
    print(f"Estimator audit XLSX: {paths['xlsx']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
