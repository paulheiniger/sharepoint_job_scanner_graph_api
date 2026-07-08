from __future__ import annotations

import argparse
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlparse

import pandas as pd
from sqlalchemy.engine import Engine
from sqlalchemy import inspect
from sqlalchemy import text

from jobscan.db_connections import create_resilient_engine
from jobscan.env import load_project_env


PARSER_VERSION = "vsimple-project-export-v1"
ACCEPTED_MATCH_STATUSES = ("matched", "review")

TEXT_NA_VALUES = {"", "nan", "none", "null", "n/a", "na", "-", "[]"}
MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


CONDENSED_COLUMNS = [
    "vsimple_id",
    "vsimple_record_id",
    "associated_contact_id",
    "record_type",
    "status_name",
    "sales_stage",
    "pipeline_status",
    "division",
    "project_category",
    "project_type",
    "deal_type",
    "name",
    "job_name",
    "customer",
    "site_address",
    "city_state_zip",
    "state",
    "contact_name",
    "contact_email",
    "contact_phone",
    "deal_owner",
    "estimator_salesperson",
    "lead_source",
    "referral_source",
    "bid_amount",
    "billing_amount",
    "gross_profit",
    "all_costs",
    "subtotal_materials",
    "subtotal_labor",
    "overhead_pct",
    "profit_pct",
    "estimated_sqft",
    "roof_deck_sqft",
    "square_footage",
    "approximate_roof_dimensions",
    "approximate_bldg_dimensions",
    "crew_leader",
    "crew_size",
    "estimated_days",
    "labor_hours",
    "created_date",
    "last_modified_date",
    "estimate_scheduled_date",
    "appointment_date",
    "follow_up_date",
    "est_close_date",
    "closed_date",
    "requested_job_start_date",
    "job_start_date",
    "install_date",
    "completion_date",
    "lead_notes",
    "scope_summary",
    "production_notes",
    "safety_concerns",
    "quality_notes",
    "spray_tec_system",
    "roof_type",
    "construction_type",
    "building_use",
    "sharepoint_url",
    "production_file_link",
    "job_costing_link",
    "job_tracking_sheet",
    "quickbooks_invoice",
    "raw_source_name",
    "parser_version",
]


@dataclass(frozen=True)
class MatchWeights:
    name: float = 40.0
    customer: float = 16.0
    address: float = 12.0
    division: float = 10.0
    year: float = 6.0
    value: float = 8.0
    sqft: float = 5.0
    url: float = 8.0


def load_vsimple_projects(path: Path | str, *, sheet_name: str = "Export") -> pd.DataFrame:
    source_path = Path(path)
    if not source_path.exists():
        raise FileNotFoundError(f"VSimple export not found: {source_path}")
    raw = pd.read_excel(source_path, sheet_name=sheet_name, dtype=object)
    raw.columns = [str(column).strip() for column in raw.columns]
    condensed = pd.DataFrame([condense_vsimple_row(row) for row in raw.to_dict(orient="records")])
    for column in CONDENSED_COLUMNS:
        if column not in condensed.columns:
            condensed[column] = None
    return condensed[CONDENSED_COLUMNS].copy()


def condense_vsimple_row(row: dict[str, Any]) -> dict[str, Any]:
    contact_name = first_nonblank(
        full_name(row.get("contact_first_name"), row.get("contact_last_name")),
        row.get("bill_to_contact"),
        row.get("name_of_building_or_customer"),
    )
    lead_notes = join_notes(
        row.get("initial_intake_information"),
        row.get("notes"),
        row.get("project_description"),
        row.get("project_description_1"),
    )
    scope_summary = join_notes(
        row.get("scope_of_work"),
        row.get("materials_required"),
        row.get("building_description"),
        row.get("obstacles"),
    )
    production_notes = join_notes(row.get("job_setup_notes"), row.get("items_needed"), row.get("directions"))
    project_type = clean_text(row.get("project_type"))
    deal_type = clean_text(row.get("deal_type"))
    record_type = clean_text(row.get("Record Type"))
    status_name = clean_text(row.get("Status Name"))
    return {
        "vsimple_id": clean_text(row.get("id")),
        "vsimple_record_id": clean_text(row.get("record_id")),
        "associated_contact_id": clean_text(row.get("associated_contact_id")),
        "record_type": record_type,
        "status_name": status_name,
        "sales_stage": sales_stage_from_status(status_name),
        "pipeline_status": pipeline_status_from_status(status_name, record_type),
        "division": infer_division(project_type=project_type, deal_type=deal_type, record_type=record_type),
        "project_category": infer_project_category(project_type=project_type, deal_type=deal_type, record_type=record_type),
        "project_type": project_type,
        "deal_type": deal_type,
        "name": clean_text(row.get("Name")),
        "job_name": clean_text(row.get("job_name")),
        "customer": first_nonblank(row.get("name_of_building_or_customer"), row.get("bill_to_contact"), contact_name, row.get("Name")),
        "site_address": first_nonblank(row.get("street_address"), row.get("bill_to_address"), row.get("street_address_warranty")),
        "city_state_zip": first_nonblank(row.get("city_state_zip"), row.get("bill_to_city_state_zip"), row.get("city_warranty")),
        "state": first_nonblank(row.get("state"), row.get("state_warranty")),
        "contact_name": contact_name,
        "contact_email": first_nonblank(row.get("contact_email"), row.get("bill_to_email_address")),
        "contact_phone": first_nonblank(row.get("contact_phone"), row.get("bill_to_phone")),
        "deal_owner": first_nonblank(row.get("deal_owner"), row.get("deal_owner_2")),
        "estimator_salesperson": first_nonblank(row.get("st_estimatorsales_person_most_associated"), row.get("inspector")),
        "lead_source": first_nonblank(row.get("lead_source"), row.get("how_did_you_hear_about_us")),
        "referral_source": first_nonblank(row.get("referral_source"), row.get("please_list_the_name_of_your_referralwed_love_to_thank_them")),
        "bid_amount": number(row.get("bid_amount")),
        "billing_amount": number(row.get("billing_amount")),
        "gross_profit": number(row.get("gross_profit")),
        "all_costs": number(row.get("all_costs")),
        "subtotal_materials": number(row.get("subtotal_materials")),
        "subtotal_labor": number(row.get("subtotal_subcontractor_labor_cost")),
        "overhead_pct": number(row.get("oh_percentage")),
        "profit_pct": number(row.get("profit_percentage")),
        "estimated_sqft": first_number(row.get("est_square_feet"), row.get("square_footage"), row.get("roof_deck_sq_ft")),
        "roof_deck_sqft": number(row.get("roof_deck_sq_ft")),
        "square_footage": number(row.get("square_footage")),
        "approximate_roof_dimensions": clean_text(row.get("approximate_roof_dimensions")),
        "approximate_bldg_dimensions": clean_text(row.get("approximate_bldg_dimensions")),
        "crew_leader": first_nonblank(row.get("crew_leader"), row.get("crew_leader_1")),
        "crew_size": first_number(row.get("crew_size"), row.get("estimated_of_crew_members")),
        "estimated_days": number(row.get("estimated_of_days")),
        "labor_hours": first_number(row.get("labor_hours"), row.get("labor_hours_total")),
        "created_date": split_date(row, "Created Date"),
        "last_modified_date": split_date(row, "Last Modified Date"),
        "estimate_scheduled_date": split_date(row, "estimate_scheduled"),
        "appointment_date": split_date(row, "appointment_date_and_time"),
        "follow_up_date": split_date(row, "follow_up_date"),
        "est_close_date": split_date(row, "est_close_date"),
        "closed_date": split_date(row, "closed_datetime"),
        "requested_job_start_date": split_date(row, "requested_job_start_date"),
        "job_start_date": split_date(row, "job_start_date"),
        "install_date": split_date(row, "install_date"),
        "completion_date": split_date(row, "completion_date"),
        "lead_notes": lead_notes,
        "scope_summary": scope_summary,
        "production_notes": production_notes,
        "safety_concerns": clean_text(row.get("safety_concerns")),
        "quality_notes": first_nonblank(row.get("quality_inspection_notes"), row.get("special_quality_concerns")),
        "spray_tec_system": clean_text(row.get("spray_tec_system")),
        "roof_type": first_nonblank(row.get("roof_type"), row.get("roof_type_1")),
        "construction_type": clean_text(row.get("construction_type")),
        "building_use": first_nonblank(row.get("building_use"), row.get("building_use_1")),
        "sharepoint_url": clean_text(row.get("sharepoint_url")),
        "production_file_link": clean_text(row.get("production_file_link")),
        "job_costing_link": clean_text(row.get("job_costing_link")),
        "job_tracking_sheet": clean_text(row.get("job_tracking_sheet")),
        "quickbooks_invoice": clean_text(row.get("quickbooks_invoice")),
        "raw_source_name": clean_text(row.get("Name")),
        "parser_version": PARSER_VERSION,
    }


def load_job_index(path: Path | str) -> pd.DataFrame:
    source_path = Path(path)
    if not source_path.exists():
        raise FileNotFoundError(f"Job index not found: {source_path}")
    if source_path.suffix.lower() in {".xlsx", ".xls"}:
        return pd.read_excel(source_path, dtype=object)
    return pd.read_csv(source_path, dtype=object)


def load_job_indexes(paths: Iterable[Path | str]) -> pd.DataFrame:
    frames = [load_job_index(path) for path in paths]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True, sort=False)


def load_dashboard_jobs_from_database(database_url: str | None = None) -> pd.DataFrame:
    load_project_env()
    resolved_url = database_url or os.getenv("DATABASE_URL")
    if not resolved_url:
        raise RuntimeError("DATABASE_URL is not configured.")
    engine = create_resilient_engine(resolved_url)
    return pd.read_sql_query(text("SELECT * FROM dashboard_jobs"), engine)


def align_vsimple_to_jobs(vsimple: pd.DataFrame, jobs: pd.DataFrame, *, limit: int = 1) -> pd.DataFrame:
    if vsimple.empty or jobs.empty:
        return pd.DataFrame(columns=match_columns())
    prepared_jobs = prepare_match_frame(jobs, prefix="job")
    job_records = prepared_jobs.to_dict(orient="records")
    rows: list[dict[str, Any]] = []
    for v_row in prepare_match_frame(vsimple, prefix="vsimple").to_dict(orient="records"):
        candidates = score_candidates(v_row, candidate_records_for(v_row, job_records))
        for candidate in candidates[: max(1, limit)]:
            rows.append(candidate)
    return pd.DataFrame(rows, columns=match_columns())


def candidate_records_for(v_row: dict[str, Any], job_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    year = clean_text(v_row.get("_match_year"))
    division = clean_text(v_row.get("_match_division")).lower()
    candidates = job_records
    if year:
        year_matches = [row for row in candidates if clean_text(row.get("_match_year")) == year]
        if year_matches:
            candidates = year_matches
    if division:
        division_matches = [row for row in candidates if clean_text(row.get("_match_division")).lower() == division]
        if division_matches:
            candidates = division_matches
    return candidates


def prepare_match_frame(df: pd.DataFrame, *, prefix: str) -> pd.DataFrame:
    out = df.copy()
    for column in ("name", "job_name", "customer", "site_address", "city_state_zip", "city", "state", "folder_name", "folder_path"):
        if column not in out.columns:
            out[column] = ""
    value_column = first_existing_column(out, ["bid_amount", "billing_amount", "estimated_value", "final_price", "invoice_amount", "total_job_cost"])
    sqft_column = first_existing_column(out, ["estimated_sqft", "est_square_feet", "square_footage", "roof_deck_sqft"])
    out["_match_title"] = out.apply(lambda row: first_nonblank(row.get("job_name"), row.get("name"), row.get("folder_name"), row.get("Name")), axis=1)
    out["_match_customer"] = out.apply(lambda row: first_nonblank(row.get("customer"), row.get("name_of_building_or_customer"), row.get("contact_name")), axis=1)
    out["_match_address"] = out.apply(lambda row: " ".join(clean_text(row.get(c)) for c in ["site_address", "street_address", "city_state_zip", "city", "state"] if c in row and clean_text(row.get(c))), axis=1)
    out["_match_division"] = out.apply(lambda row: infer_division(project_type=clean_text(row.get("project_type")), deal_type=clean_text(row.get("deal_type") or row.get("job_type")), record_type=clean_text(row.get("record_type") or row.get("division"))), axis=1)
    if "division" in out.columns:
        out["_match_division"] = out["division"].map(clean_text).where(out["division"].map(clean_text).ne(""), out["_match_division"])
    out["_match_year"] = out.apply(row_year, axis=1)
    out["_match_value"] = out[value_column].map(number) if value_column else None
    out["_match_sqft"] = out[sqft_column].map(number) if sqft_column else None
    out["_match_url_text"] = out.apply(lambda row: url_match_text(row), axis=1)
    out["_match_name_key"] = out.apply(lambda row: normalize_match_text(" ".join([clean_text(row.get("_match_title")), clean_text(row.get("_match_customer"))])), axis=1)
    out["_match_address_key"] = out["_match_address"].map(normalize_match_text)
    return out


def score_candidates(v_row: dict[str, Any], job_records: list[dict[str, Any]], *, weights: MatchWeights = MatchWeights()) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    v_year = clean_text(v_row.get("_match_year"))
    v_division = clean_text(v_row.get("_match_division")).lower()
    for j_row in job_records:
        reasons: list[str] = []
        name_ratio = text_ratio(v_row.get("_match_name_key"), j_row.get("_match_name_key"))
        customer_ratio = text_ratio(v_row.get("_match_customer"), j_row.get("_match_customer"))
        address_ratio = text_ratio(v_row.get("_match_address_key"), j_row.get("_match_address_key"))
        score = (name_ratio * weights.name) + (customer_ratio * weights.customer) + (address_ratio * weights.address)
        if name_ratio >= 0.82:
            reasons.append(f"name {name_ratio:.2f}")
        if customer_ratio >= 0.80:
            reasons.append(f"customer {customer_ratio:.2f}")
        if address_ratio >= 0.80:
            reasons.append(f"address {address_ratio:.2f}")
        j_division = clean_text(j_row.get("_match_division")).lower()
        if v_division and j_division and v_division == j_division:
            score += weights.division
            reasons.append("division")
        j_year = clean_text(j_row.get("_match_year"))
        if v_year and j_year and v_year == j_year:
            score += weights.year
            reasons.append("year")
        value_score = numeric_similarity(v_row.get("_match_value"), j_row.get("_match_value"))
        if value_score:
            score += value_score * weights.value
            if value_score >= 0.8:
                reasons.append("value")
        sqft_score = numeric_similarity(v_row.get("_match_sqft"), j_row.get("_match_sqft"))
        if sqft_score:
            score += sqft_score * weights.sqft
            if sqft_score >= 0.8:
                reasons.append("sqft")
        url_score = token_overlap(v_row.get("_match_url_text"), j_row.get("_match_url_text"))
        if url_score:
            score += url_score * weights.url
            if url_score >= 0.25:
                reasons.append("url")
        if score < 25:
            continue
        scored.append(match_row(v_row, j_row, score=score, reasons=reasons))
    return sorted(scored, key=lambda row: row["match_score"], reverse=True)


def match_row(v_row: dict[str, Any], j_row: dict[str, Any], *, score: float, reasons: list[str]) -> dict[str, Any]:
    status = "matched" if score >= 78 else "review" if score >= 55 else "weak"
    return {
        "match_status": status,
        "match_score": round(score, 2),
        "match_reasons": "; ".join(reasons),
        "vsimple_id": clean_text(v_row.get("vsimple_id") or v_row.get("id")),
        "vsimple_record_id": clean_text(v_row.get("vsimple_record_id") or v_row.get("record_id")),
        "vsimple_name": clean_text(v_row.get("name") or v_row.get("Name")),
        "vsimple_job_name": clean_text(v_row.get("job_name")),
        "vsimple_customer": clean_text(v_row.get("customer")),
        "vsimple_status": clean_text(v_row.get("status_name") or v_row.get("Status Name")),
        "vsimple_pipeline_status": clean_text(v_row.get("pipeline_status")),
        "vsimple_division": clean_text(v_row.get("division")),
        "vsimple_bid_amount": number(v_row.get("bid_amount")),
        "vsimple_sharepoint_url": clean_text(v_row.get("sharepoint_url")),
        "job_id": clean_text(j_row.get("job_id")),
        "job_customer": clean_text(j_row.get("customer")),
        "job_name": clean_text(j_row.get("job_name")),
        "job_division": clean_text(j_row.get("division")),
        "job_pipeline_status": clean_text(j_row.get("pipeline_status")),
        "job_estimated_value": number(j_row.get("estimated_value")),
        "job_folder_url": clean_text(j_row.get("folder_url")),
        "job_folder_path": clean_text(j_row.get("folder_path")),
    }


def match_columns() -> list[str]:
    return [
        "match_status",
        "match_score",
        "match_reasons",
        "vsimple_id",
        "vsimple_record_id",
        "vsimple_name",
        "vsimple_job_name",
        "vsimple_customer",
        "vsimple_status",
        "vsimple_pipeline_status",
        "vsimple_division",
        "vsimple_bid_amount",
        "vsimple_sharepoint_url",
        "job_id",
        "job_customer",
        "job_name",
        "job_division",
        "job_pipeline_status",
        "job_estimated_value",
        "job_folder_url",
        "job_folder_path",
    ]


def write_outputs(condensed: pd.DataFrame, matches: pd.DataFrame | None, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "condensed": output_dir / "vsimple_projects_condensed.csv",
    }
    condensed.to_csv(paths["condensed"], index=False)
    if matches is not None:
        paths["matches"] = output_dir / "vsimple_sharepoint_job_matches.csv"
        matches.to_csv(paths["matches"], index=False)
        accepted = accepted_matches(matches)
        paths["review"] = output_dir / "vsimple_sharepoint_job_matches_review.csv"
        accepted.to_csv(paths["review"], index=False)
        paths["accepted"] = output_dir / "vsimple_sharepoint_job_matches_accepted.csv"
        accepted.to_csv(paths["accepted"], index=False)
    summary = {
        "parser_version": PARSER_VERSION,
        "condensed_rows": int(len(condensed)),
        "match_rows": int(len(matches)) if matches is not None else 0,
        "match_status_counts": matches["match_status"].value_counts(dropna=False).to_dict() if matches is not None and not matches.empty else {},
        "accepted_match_statuses": list(ACCEPTED_MATCH_STATUSES),
        "accepted_match_rows": int(len(accepted_matches(matches))) if matches is not None else 0,
    }
    paths["summary"] = output_dir / "vsimple_projects_summary.json"
    paths["summary"].write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return paths


def accepted_matches(matches: pd.DataFrame) -> pd.DataFrame:
    if matches.empty or "match_status" not in matches.columns:
        return pd.DataFrame(columns=match_columns())
    return matches[matches["match_status"].isin(ACCEPTED_MATCH_STATUSES)].copy()


def write_outputs_to_database(
    condensed: pd.DataFrame,
    matches: pd.DataFrame | None,
    engine: Engine,
    *,
    if_exists: str = "replace",
) -> dict[str, int]:
    if if_exists not in {"replace", "append", "fail"}:
        raise ValueError("if_exists must be replace, append, or fail")
    table_frames = {
        "vsimple_projects": condensed,
    }
    if matches is not None:
        table_frames["vsimple_sharepoint_job_matches"] = matches
        table_frames["vsimple_sharepoint_job_matches_accepted"] = accepted_matches(matches)
    inspector = inspect(engine)
    for table_name, frame in table_frames.items():
        sql_mode = if_exists
        if if_exists == "replace" and inspector.has_table(table_name):
            with engine.begin() as connection:
                connection.execute(text(f"DELETE FROM {table_name}"))
            sql_mode = "append"
        clean_frame_for_sql(frame).to_sql(table_name, engine, if_exists=sql_mode, index=False, chunksize=1000)
    return {table_name: int(len(frame)) for table_name, frame in table_frames.items()}


def clean_frame_for_sql(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    return out.where(pd.notna(out), None)


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text_value = str(value).strip()
    return "" if text_value.lower() in TEXT_NA_VALUES else text_value


def first_nonblank(*values: Any) -> str:
    for value in values:
        text_value = clean_text(value)
        if text_value:
            return text_value
    return ""


def number(value: Any) -> float | None:
    text_value = clean_text(value)
    if not text_value:
        return None
    cleaned = re.sub(r"[^0-9.\\-]+", "", text_value)
    if cleaned in {"", "-", ".", "-."}:
        return None
    try:
        numeric = float(cleaned)
    except ValueError:
        return None
    if math.isnan(numeric):
        return None
    return numeric


def first_number(*values: Any) -> float | None:
    for value in values:
        numeric = number(value)
        if numeric is not None:
            return numeric
    return None


def full_name(first: Any, last: Any) -> str:
    return " ".join(part for part in [clean_text(first), clean_text(last)] if part)


def join_notes(*values: Any, max_chars: int = 4000) -> str:
    parts = [clean_text(value) for value in values if clean_text(value)]
    return "\n\n".join(parts)[:max_chars]


def split_date(row: dict[str, Any], prefix: str) -> str:
    year = number(row.get(f"{prefix} - Year"))
    month_raw = clean_text(row.get(f"{prefix} - Month")).lower()
    day = number(row.get(f"{prefix} - Day"))
    if year is None or day is None or not month_raw:
        return ""
    month = MONTHS.get(month_raw)
    if month is None:
        month = int(number(month_raw) or 0)
    try:
        return date(int(year), int(month), int(day)).isoformat()
    except Exception:
        return ""


def sales_stage_from_status(status: str) -> str:
    text_value = status.lower()
    if "closed lost" in text_value:
        return "Closed Lost"
    if "closed won" in text_value or "job closeout" in text_value or "invoicing" in text_value or "completed" in text_value:
        return "Closed Won"
    if "production" in text_value or "job in progress" in text_value or "warranty" in text_value:
        return "Closed Won"
    if "proposal submitted" in text_value:
        return "Proposal Submitted"
    if "estimate" in text_value:
        return "Estimate In Progress"
    if "contacted" in text_value:
        return "Site Visit Scheduled"
    if "lead" in text_value:
        return "Lead Received"
    if "limbo" in text_value:
        return "Follow-Up / Negotiation"
    return status or "Unknown"


def pipeline_status_from_status(status: str, record_type: str = "") -> str:
    text_value = f"{status} {record_type}".lower()
    if "closed lost" in text_value:
        return "Closed Lost"
    if any(token in text_value for token in ["job closeout", "invoicing", "completed", "warranty", "job roofing", "job insulation", "job flooring", "job repair"]):
        return "Completed"
    if any(token in text_value for token in ["production", "job in progress", "pre-mobilization", "on hold"]):
        return "Contracted"
    if any(token in text_value for token in ["proposal", "estimate", "contacted", "lead", "limbo", "bid projects"]):
        return "Proposed"
    return status or "Unknown"


def infer_division(*, project_type: str = "", deal_type: str = "", record_type: str = "") -> str:
    text_value = f"{project_type} {deal_type} {record_type}".lower()
    if "floor" in text_value:
        return "Flooring"
    if "repair" in text_value:
        return "Roofing"
    if any(token in text_value for token in ["insulation", "foam", "ductwork", "tank"]):
        return "Insulation"
    if any(token in text_value for token in ["roof", "coating", "recoat", "membrane", "shingle", "scan"]):
        return "Roofing"
    return clean_text(project_type) or clean_text(record_type) or "Unknown"


def infer_project_category(*, project_type: str = "", deal_type: str = "", record_type: str = "") -> str:
    text_value = f"{project_type} {deal_type} {record_type}".lower()
    if "repair" in text_value:
        return "Repairs"
    if "insulation" in text_value or "spray foam" in text_value or "tank" in text_value or "ductwork" in text_value:
        return "Spray Foam Insulation"
    if "floor" in text_value:
        return "Flooring"
    if "metal" in text_value:
        return "Metal Restoration"
    if "roof" in text_value or "coating" in text_value or "recoat" in text_value:
        return "Roofing Restoration"
    return clean_text(deal_type) or clean_text(project_type) or "Unclassified"


def first_existing_column(df: pd.DataFrame, candidates: Iterable[str]) -> str:
    for column in candidates:
        if column in df.columns:
            return column
    return ""


def row_year(row: pd.Series | dict[str, Any]) -> str:
    for column in ["source_year", "created_date", "completion_date", "estimate_date", "invoice_date", "Created Date - Year"]:
        text_value = clean_text(row.get(column) if hasattr(row, "get") else "")
        if not text_value:
            continue
        match = re.search(r"(20\\d{2})", text_value)
        if match:
            return match.group(1)
    return ""


def normalize_match_text(value: Any) -> str:
    text_value = unquote(clean_text(value)).lower()
    text_value = re.sub(r"https?://\\S+", " ", text_value)
    text_value = re.sub(r"[^a-z0-9]+", " ", text_value)
    stopwords = {
        "the",
        "and",
        "of",
        "for",
        "estimate",
        "roofing",
        "insulation",
        "job",
        "project",
        "completed",
        "contracted",
        "proposed",
        "residence",
        "customer",
    }
    tokens = [
        token
        for token in text_value.split()
        if token not in stopwords and len(token) > 1 and not re.fullmatch(r"20\\d{2}", token)
    ]
    return " ".join(tokens)


def text_ratio(left: Any, right: Any) -> float:
    left_text = normalize_match_text(left)
    right_text = normalize_match_text(right)
    if not left_text or not right_text:
        return 0.0
    sequence_ratio = SequenceMatcher(None, left_text, right_text).ratio()
    left_tokens = set(left_text.split())
    right_tokens = set(right_text.split())
    containment = len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens)) if left_tokens and right_tokens else 0.0
    return max(sequence_ratio, containment * 0.96)


def numeric_similarity(left: Any, right: Any) -> float:
    left_num = number(left)
    right_num = number(right)
    if left_num is None or right_num is None or left_num <= 0 or right_num <= 0:
        return 0.0
    ratio = min(left_num, right_num) / max(left_num, right_num)
    return ratio if ratio >= 0.65 else 0.0


def token_overlap(left: Any, right: Any) -> float:
    left_tokens = set(normalize_match_text(left).split())
    right_tokens = set(normalize_match_text(right).split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def url_match_text(row: pd.Series | dict[str, Any]) -> str:
    values = []
    for column in [
        "sharepoint_url",
        "production_file_link",
        "job_costing_link",
        "job_tracking_sheet",
        "folder_url",
        "folder_path",
        "proposal_url",
        "estimate_url",
        "invoice_url",
        "warranty_url",
        "primary_doc_link",
    ]:
        value = clean_text(row.get(column) if hasattr(row, "get") else "")
        if value:
            parsed = urlparse(value)
            values.append(unquote(parsed.path or value))
    return " ".join(values)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Condense a full VSimple project export and optionally align it to SharePoint dashboard jobs.")
    parser.add_argument("--input", type=Path, required=True, help="VSimple XLSX export.")
    parser.add_argument("--sheet", default="Export", help="Worksheet name. Defaults to Export.")
    parser.add_argument("--job-index", type=Path, action="append", default=[], help="Optional SharePoint job index CSV/XLSX for matching. Repeat to combine years.")
    parser.add_argument("--database-url", default="", help="Optional database URL. If supplied without --job-index, reads dashboard_jobs.")
    parser.add_argument("--output-dir", type=Path, default=Path("output/vsimple_projects"), help="Output directory.")
    parser.add_argument("--match-limit", type=int, default=1, help="Number of match candidates per VSimple row.")
    parser.add_argument("--write-db", action="store_true", help="Write condensed VSimple projects and match tables to the configured database.")
    parser.add_argument("--db-if-exists", choices=["replace", "append", "fail"], default="replace", help="Database write mode for --write-db.")
    args = parser.parse_args(argv)

    condensed = load_vsimple_projects(args.input, sheet_name=args.sheet)
    jobs = pd.DataFrame()
    if args.job_index:
        jobs = load_job_indexes(args.job_index)
    elif args.database_url or os.getenv("DATABASE_URL"):
        jobs = load_dashboard_jobs_from_database(args.database_url or None)
    matches = align_vsimple_to_jobs(condensed, jobs, limit=args.match_limit) if not jobs.empty else None
    paths = write_outputs(condensed, matches, args.output_dir)
    result: dict[str, Any] = {name: str(path) for name, path in paths.items()}
    if args.write_db:
        load_project_env()
        resolved_url = args.database_url or os.getenv("DATABASE_URL")
        if not resolved_url:
            raise RuntimeError("DATABASE_URL is required when --write-db is used.")
        engine = create_resilient_engine(resolved_url)
        result["database_rows"] = write_outputs_to_database(condensed, matches, engine, if_exists=args.db_if_exists)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
