from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from .profiler import load_tables_from_database
from .scope_parser import ParsedRepairScope, parse_repair_notes
from .vsimple_loader import RepairTables


LOW_EVIDENCE_THRESHOLD = 5


@dataclass
class RepairEstimateResult:
    notes: str
    parsed_scope: dict[str, Any]
    matched_repair_profiles: list[dict[str, Any]]
    selected_repair_packages: list[dict[str, Any]]
    estimated_labor_hours_low: float | None
    estimated_labor_hours_target: float | None
    estimated_labor_hours_high: float | None
    estimated_material_cost_low: float | None
    estimated_material_cost_target: float | None
    estimated_material_cost_high: float | None
    estimated_invoice_low: float | None
    estimated_invoice_target: float | None
    estimated_invoice_high: float | None
    confidence: str
    review_flags: list[str] = field(default_factory=list)
    evidence_summary: dict[str, Any] = field(default_factory=dict)
    similar_repairs: list[dict[str, Any]] = field(default_factory=list)
    audit_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    try:
        number = float(str(value).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def safe_quantile(values: pd.Series, q: float) -> float | None:
    numbers = pd.to_numeric(values, errors="coerce").dropna()
    if numbers.empty:
        return None
    return float(numbers.quantile(q))


def safe_median(values: pd.Series) -> float | None:
    return safe_quantile(values, 0.5)


def token_set(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 2}


def row_text(row: pd.Series) -> str:
    parts = [
        row.get("job_name"),
        row.get("type_of_repair"),
        row.get("roof_type"),
        row.get("scope_of_work"),
        row.get("work_performed_long_text"),
        row.get("special_notes"),
        row.get("materials_used"),
        row.get("combined_scope_text"),
    ]
    return " ".join(clean_text(part) for part in parts if clean_text(part))


def load_repair_history_from_database(engine: Engine) -> RepairTables:
    inspector = inspect(engine)
    required = [
        "repair_jobs",
        "repair_material_usage",
        "repair_labor_usage",
        "repair_scope_text",
        "repair_outcomes",
    ]
    missing = [table for table in required if not inspector.has_table(table)]
    if missing:
        raise RuntimeError(f"Missing repair estimator tables: {', '.join(missing)}")
    return load_tables_from_database(engine)


def build_repair_history_frame(tables: RepairTables) -> pd.DataFrame:
    jobs = tables.repair_jobs.copy()
    scope = tables.repair_scope_text.copy()
    outcomes = tables.repair_outcomes.copy()
    labor = tables.repair_labor_usage.copy()
    if jobs.empty:
        return pd.DataFrame()

    for frame in [scope, outcomes, labor]:
        if "repair_id" not in frame.columns:
            frame["repair_id"] = None

    frame = jobs.merge(scope, on="repair_id", how="left", suffixes=("", "_scope"))
    frame = frame.merge(outcomes, on="repair_id", how="left", suffixes=("", "_outcome"))
    if not labor.empty:
        aggregate = labor[labor.get("labor_role", "") == "aggregate"].copy() if "labor_role" in labor.columns else labor.copy()
        aggregate["total_labor_hours"] = pd.to_numeric(aggregate.get("total_labor_hours", aggregate.get("labor_hours")), errors="coerce")
        aggregate["labor_cost"] = pd.to_numeric(aggregate.get("labor_cost"), errors="coerce")
        labor_summary = aggregate.groupby("repair_id", dropna=False).agg(
            historical_labor_hours=("total_labor_hours", "max"),
            historical_labor_cost=("labor_cost", "sum"),
        ).reset_index()
        frame = frame.merge(labor_summary, on="repair_id", how="left")
    else:
        frame["historical_labor_hours"] = None
        frame["historical_labor_cost"] = None

    for column in ["invoice_amount", "total_bill_amount", "gross_profit", "historical_labor_hours", "historical_labor_cost"]:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["search_text"] = frame.apply(row_text, axis=1)
    return frame


def score_repair_row(row: pd.Series, parsed: ParsedRepairScope, note_tokens: set[str]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    roof_type = clean_text(row.get("roof_type")).lower().replace(" ", "_")
    type_of_repair = clean_text(row.get("type_of_repair")).lower().replace(" ", "_")
    search = clean_text(row.get("search_text")).lower()
    if parsed.roof_type != "unknown" and parsed.roof_type in roof_type:
        score += 18
        reasons.append("roof type")
    if parsed.repair_type != "unknown" and parsed.repair_type.replace("_", " ") in search:
        score += 16
        reasons.append("repair type")
    if parsed.issue_type != "unknown" and any(term in search for term in parsed.issue_type.split("_")):
        score += 14
        reasons.append("issue keywords")
    if parsed.leak_present and "leak" in search:
        score += 10
        reasons.append("leak evidence")
    for action in parsed.actions_requested:
        if action.replace("_", " ") in search or action in search:
            score += 6
            reasons.append(f"action:{action}")
    for material in parsed.materials_mentioned:
        if material.replace("_", " ") in search or material in search:
            score += 6
            reasons.append(f"material:{material}")
    row_tokens = token_set(search)
    if note_tokens and row_tokens:
        overlap = len(note_tokens & row_tokens)
        union = len(note_tokens | row_tokens)
        jaccard = overlap / union if union else 0
        score += min(30, jaccard * 100)
        if overlap:
            reasons.append(f"text overlap:{overlap}")
    status = clean_text(row.get("status")).lower()
    if "invoice" in status or "complete" in status:
        score += 4
        reasons.append("completed/invoiced")
    if parsed.emergency_or_standard == "emergency" and "emergency" in search:
        score += 8
        reasons.append("emergency")
    return score, reasons


def find_similar_repairs(
    tables: RepairTables,
    parsed: ParsedRepairScope,
    notes: str,
    *,
    limit: int = 12,
) -> pd.DataFrame:
    history = build_repair_history_frame(tables)
    if history.empty:
        return pd.DataFrame()
    note_tokens = token_set(notes)
    scored_rows: list[dict[str, Any]] = []
    for _, row in history.iterrows():
        score, reasons = score_repair_row(row, parsed, note_tokens)
        if score <= 0:
            continue
        record = row.to_dict()
        record["similarity_score"] = round(score, 2)
        record["reason_matched"] = ", ".join(dict.fromkeys(reasons))
        scored_rows.append(record)
    if not scored_rows:
        return pd.DataFrame()
    similar = pd.DataFrame(scored_rows).sort_values("similarity_score", ascending=False)
    return similar.head(limit).reset_index(drop=True)


def material_packages_for_scope(parsed: ParsedRepairScope, material_evidence: pd.DataFrame) -> list[dict[str, Any]]:
    packages: dict[str, dict[str, Any]] = {}
    issue_package_map = {
        "pipe_boot_leak": ["caulk_sealant", "fabric_reinforcement"],
        "open_seam": ["caulk_sealant", "fabric_reinforcement"],
        "exposed_fasteners": ["fasteners", "caulk_sealant"],
        "skylight_curb_leak": ["caulk_sealant", "fabric_reinforcement", "flashing_edge_metal"],
        "curb_leak": ["caulk_sealant", "fabric_reinforcement"],
        "drain_leak": ["caulk_sealant", "fabric_reinforcement", "membrane_patch"],
        "puncture_or_patch": ["membrane_patch", "fabric_reinforcement"],
        "flashing_leak": ["flashing_edge_metal", "caulk_sealant"],
        "small_coating_touch_up": ["coating", "caulk_sealant"],
        "unknown_leak": ["caulk_sealant"],
        "emergency_leak_call": ["caulk_sealant", "fabric_reinforcement"],
    }
    for package in issue_package_map.get(parsed.issue_type, []):
        packages[package] = {
            "material_package": package,
            "selection_reason": f"Selected from parsed issue_type={parsed.issue_type}",
            "evidence_count": 0,
            "median_total_cost": None,
        }
    for material in parsed.materials_mentioned:
        package = {
            "sealant": "caulk_sealant",
            "fabric": "fabric_reinforcement",
            "coating": "coating",
            "fasteners": "fasteners",
            "membrane": "membrane_patch",
            "flashing": "flashing_edge_metal",
            "primer": "primer",
        }.get(material, material)
        packages.setdefault(
            package,
            {
                "material_package": package,
                "selection_reason": f"Material mentioned in notes: {material}",
                "evidence_count": 0,
                "median_total_cost": None,
            },
        )
    if not material_evidence.empty and "material_package" in material_evidence.columns:
        for package, group in material_evidence.groupby("material_package", dropna=False):
            if not clean_text(package):
                continue
            if package in packages or len(group) >= 2:
                item = packages.setdefault(
                    str(package),
                    {
                        "material_package": str(package),
                        "selection_reason": "Common in similar historical repairs",
                        "evidence_count": 0,
                        "median_total_cost": None,
                    },
                )
                item["evidence_count"] = int(group["repair_id"].nunique()) if "repair_id" in group.columns else int(len(group))
                item["median_total_cost"] = safe_median(group.get("total_cost", pd.Series(dtype=float)))
                names = group.get("material_name", pd.Series(dtype=str)).dropna().astype(str).value_counts().head(5).index.tolist()
                item["common_material_names"] = names
    return sorted(packages.values(), key=lambda row: (-int(row.get("evidence_count") or 0), row.get("material_package") or ""))


def material_evidence_for_similar(tables: RepairTables, similar: pd.DataFrame) -> pd.DataFrame:
    materials = tables.repair_material_usage.copy()
    if materials.empty or similar.empty or "repair_id" not in materials.columns:
        return pd.DataFrame()
    ids = set(similar["repair_id"].astype(str))
    frame = materials[materials["repair_id"].astype(str).isin(ids)].copy()
    for column in ["quantity", "unit_cost", "total_cost"]:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def estimate_from_evidence(
    parsed: ParsedRepairScope,
    similar: pd.DataFrame,
    material_evidence: pd.DataFrame,
) -> dict[str, Any]:
    evidence_count = int(len(similar))
    labor_values = similar.get("historical_labor_hours", pd.Series(dtype=float)) if not similar.empty else pd.Series(dtype=float)
    invoice_values = similar.get("invoice_amount", pd.Series(dtype=float)) if not similar.empty else pd.Series(dtype=float)
    if invoice_values.dropna().empty and not similar.empty:
        invoice_values = similar.get("total_bill_amount", pd.Series(dtype=float))
    material_total_by_repair = pd.Series(dtype=float)
    if not material_evidence.empty and "repair_id" in material_evidence.columns:
        material_total_by_repair = (
            material_evidence.groupby("repair_id")["total_cost"].sum(min_count=1)
            if "total_cost" in material_evidence.columns
            else pd.Series(dtype=float)
        )

    labor_low = safe_quantile(labor_values, 0.25)
    labor_target = safe_median(labor_values)
    labor_high = safe_quantile(labor_values, 0.75)
    invoice_low = safe_quantile(invoice_values, 0.25)
    invoice_target = safe_median(invoice_values)
    invoice_high = safe_quantile(invoice_values, 0.75)
    material_low = safe_quantile(material_total_by_repair, 0.25)
    material_target = safe_median(material_total_by_repair)
    material_high = safe_quantile(material_total_by_repair, 0.75)

    if labor_target is None:
        labor_low, labor_target, labor_high = (2.0, 4.0, 8.0) if parsed.emergency_or_standard != "emergency" else (4.0, 8.0, 12.0)
    if material_target is None:
        material_low, material_target, material_high = 50.0, 150.0, 350.0
    if invoice_target is None:
        labor_component = (labor_target or 4.0) * 95
        material_component = material_target or 150
        subtotal = labor_component + material_component
        invoice_low, invoice_target, invoice_high = subtotal * 1.1, subtotal * 1.35, subtotal * 1.75

    if parsed.emergency_or_standard == "emergency":
        invoice_low = (invoice_low or 0) * 1.15
        invoice_target = (invoice_target or 0) * 1.2
        invoice_high = (invoice_high or 0) * 1.35

    return {
        "evidence_count": evidence_count,
        "labor_low": round(labor_low, 2) if labor_low is not None else None,
        "labor_target": round(labor_target, 2) if labor_target is not None else None,
        "labor_high": round(labor_high, 2) if labor_high is not None else None,
        "material_low": round(material_low, 2) if material_low is not None else None,
        "material_target": round(material_target, 2) if material_target is not None else None,
        "material_high": round(material_high, 2) if material_high is not None else None,
        "invoice_low": round(invoice_low, 2) if invoice_low is not None else None,
        "invoice_target": round(invoice_target, 2) if invoice_target is not None else None,
        "invoice_high": round(invoice_high, 2) if invoice_high is not None else None,
    }


def confidence_for_result(evidence_count: int, parsed: ParsedRepairScope) -> str:
    if evidence_count >= 20 and len(parsed.missing_info) <= 1:
        return "high"
    if evidence_count >= LOW_EVIDENCE_THRESHOLD and len(parsed.missing_info) <= 2:
        return "medium"
    return "low"


def estimate_repair_from_notes(
    notes: str,
    tables: RepairTables,
    overrides: dict[str, Any] | None = None,
    *,
    similar_limit: int = 12,
) -> RepairEstimateResult:
    parsed = parse_repair_notes(notes, overrides)
    similar = find_similar_repairs(tables, parsed, notes, limit=similar_limit)
    material_evidence = material_evidence_for_similar(tables, similar)
    package_rows = material_packages_for_scope(parsed, material_evidence)
    estimates = estimate_from_evidence(parsed, similar, material_evidence)
    evidence_count = estimates["evidence_count"]
    confidence = confidence_for_result(evidence_count, parsed)

    review_flags = list(parsed.review_flags)
    if evidence_count < LOW_EVIDENCE_THRESHOLD:
        review_flags.append(f"Low historical evidence count ({evidence_count}); estimator review required.")
    if parsed.missing_info:
        review_flags.append("Missing repair info: " + ", ".join(parsed.missing_info))
    if parsed.issue_type == "unknown":
        review_flags.append("Could not identify a specific repair issue from notes.")

    matched_profiles = []
    if not similar.empty:
        profile_group = similar.copy()
        profile_group["roof_type"] = profile_group.get("roof_type", "").fillna("").replace("", "unknown")
        profile_group["type_of_repair"] = profile_group.get("type_of_repair", "").fillna("").replace("", "unknown")
        for (repair_type, roof_type), group in profile_group.groupby(["type_of_repair", "roof_type"], dropna=False):
            matched_profiles.append(
                {
                    "type_of_repair": repair_type,
                    "roof_type": roof_type,
                    "evidence_count": int(group["repair_id"].nunique()),
                    "median_labor_hours": safe_median(group.get("historical_labor_hours", pd.Series(dtype=float))),
                    "median_invoice_amount": safe_median(group.get("invoice_amount", pd.Series(dtype=float))),
                }
            )
    similar_records = []
    if not similar.empty:
        columns = [
            "repair_id",
            "job_name",
            "customer",
            "status",
            "type_of_repair",
            "roof_type",
            "historical_labor_hours",
            "invoice_amount",
            "gross_profit",
            "url",
            "similarity_score",
            "reason_matched",
        ]
        for column in columns:
            if column not in similar.columns:
                similar[column] = None
        similar_records = similar[columns].to_dict(orient="records")

    return RepairEstimateResult(
        notes=notes,
        parsed_scope=parsed.to_dict(),
        matched_repair_profiles=matched_profiles,
        selected_repair_packages=package_rows,
        estimated_labor_hours_low=estimates["labor_low"],
        estimated_labor_hours_target=estimates["labor_target"],
        estimated_labor_hours_high=estimates["labor_high"],
        estimated_material_cost_low=estimates["material_low"],
        estimated_material_cost_target=estimates["material_target"],
        estimated_material_cost_high=estimates["material_high"],
        estimated_invoice_low=estimates["invoice_low"],
        estimated_invoice_target=estimates["invoice_target"],
        estimated_invoice_high=estimates["invoice_high"],
        confidence=confidence,
        review_flags=review_flags,
        evidence_summary={
            "similar_repair_count": evidence_count,
            "material_evidence_rows": int(len(material_evidence)),
            "low_evidence_threshold": LOW_EVIDENCE_THRESHOLD,
            "calibration_source": "repair_jobs + repair_scope_text + repair_labor_usage + repair_material_usage + repair_outcomes",
        },
        similar_repairs=similar_records,
        audit_metadata={
            "generated_at": datetime.now(UTC).isoformat(),
            "estimator_version": "repair-estimator-mvp-v1",
        },
    )


def sanitize_for_json(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, dict):
        return {str(key): sanitize_for_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_for_json(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_for_json(item) for item in value]
    return value


def write_repair_audit_package(result: RepairEstimateResult, output_dir: Path | str, *, stem: str = "repair_estimate") -> dict[str, Path]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = sanitize_for_json(result.to_dict())
    json_path = out_dir / f"{stem}.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    xlsx_path = out_dir / f"{stem}.xlsx"
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        pd.DataFrame([payload.get("parsed_scope", {})]).to_excel(writer, sheet_name="parsed_scope", index=False)
        pd.DataFrame(
            [
                {
                    "labor_low": payload.get("estimated_labor_hours_low"),
                    "labor_target": payload.get("estimated_labor_hours_target"),
                    "labor_high": payload.get("estimated_labor_hours_high"),
                    "material_low": payload.get("estimated_material_cost_low"),
                    "material_target": payload.get("estimated_material_cost_target"),
                    "material_high": payload.get("estimated_material_cost_high"),
                    "invoice_low": payload.get("estimated_invoice_low"),
                    "invoice_target": payload.get("estimated_invoice_target"),
                    "invoice_high": payload.get("estimated_invoice_high"),
                    "confidence": payload.get("confidence"),
                }
            ]
        ).to_excel(writer, sheet_name="estimate_range", index=False)
        pd.DataFrame(payload.get("selected_repair_packages") or []).to_excel(writer, sheet_name="repair_packages", index=False)
        pd.DataFrame(payload.get("similar_repairs") or []).to_excel(writer, sheet_name="similar_repairs", index=False)
        pd.DataFrame(payload.get("matched_repair_profiles") or []).to_excel(writer, sheet_name="matched_profiles", index=False)
        pd.DataFrame([{"review_flag": flag} for flag in payload.get("review_flags") or []]).to_excel(writer, sheet_name="review_flags", index=False)
        pd.DataFrame([payload.get("evidence_summary", {})]).to_excel(writer, sheet_name="evidence_summary", index=False)
    return {"json": json_path, "xlsx": xlsx_path}
