from __future__ import annotations

import argparse
import json
import math
import os
import re
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import bindparam, text

from jobscan.db_connections import create_resilient_engine

from .job_context_profiles import build_job_context_digest, build_job_context_profiles
from .reference_answer_key import build_reference_estimate_answer_key


TEMPLATE_EXAMPLE_COLUMNS = [
    "example_id",
    "job_id",
    "document_id",
    "source_file",
    "customer",
    "job_name",
    "template_type",
    "project_class",
    "market_segment",
    "building_type",
    "substrate",
    "material_system",
    "material_packages_json",
    "warranty_years",
    "area_sqft",
    "area_bucket",
    "scope_summary",
    "decision_summary",
    "decisions_json",
    "answer_key_json",
    "source",
    "confidence",
]

DECISION_FIELD_CANDIDATES = (
    "row_number",
    "workbook_row",
    "template_section",
    "section",
    "template_bucket",
    "line_item_kind",
    "row_label",
    "selected_item_name",
    "resolved_item_name",
    "resolved_template_option",
    "selector_code",
    "basis_sqft",
    "area_sqft",
    "estimated_sqft",
    "square_feet",
    "thickness_inches",
    "foam_thickness_inches",
    "yield_or_coverage",
    "estimated_yield",
    "estimated_units",
    "estimated_sets",
    "estimated_gallons",
    "unit_price",
    "price_per_square",
    "unit_price_per_thousand",
    "estimated_cost",
    "line_total",
    "days",
    "hours_per_day",
    "people_count",
    "trip_count",
    "round_trip_miles",
    "crew_size",
    "daily_rate",
    "hourly_rate",
    "total_hours",
    "markup_pct",
    "warranty_years",
)

NON_DECISION_KINDS = {"header", "total", "subtotal", "metadata", "other"}

ANSWER_KEY_TEXT_STOPWORDS = {
    "above",
    "address",
    "all",
    "and",
    "applicable",
    "are",
    "bid",
    "business",
    "city",
    "com",
    "contact",
    "contract",
    "contractor",
    "cost",
    "customer",
    "date",
    "description",
    "document",
    "documents",
    "drive",
    "email",
    "estimate",
    "file",
    "from",
    "http",
    "https",
    "inc",
    "job",
    "kentucky",
    "llc",
    "louisville",
    "name",
    "page",
    "phone",
    "portal",
    "price",
    "project",
    "proposal",
    "provide",
    "quote",
    "respectfully",
    "road",
    "roof",
    "shall",
    "service",
    "services",
    "scope",
    "source",
    "state",
    "street",
    "submitted",
    "the",
    "this",
    "time",
    "total",
    "web",
    "will",
    "with",
    "work",
    "www",
}

ANSWER_KEY_TECHNICAL_TOKEN_WEIGHTS = {
    "acrylic": 1.5,
    "board": 1.4,
    "caulk": 1.4,
    "ceiling": 1.4,
    "cell": 1.4,
    "closed": 1.6,
    "cmu": 1.5,
    "coating": 1.8,
    "concrete": 1.5,
    "curb": 1.3,
    "deck": 1.3,
    "drain": 1.3,
    "epdm": 1.8,
    "fabric": 1.4,
    "fastener": 1.5,
    "foam": 1.8,
    "gaco": 1.3,
    "granules": 1.5,
    "industrial": 1.3,
    "insulation": 1.6,
    "metal": 1.7,
    "open": 1.5,
    "penetration": 1.4,
    "polyurethane": 1.5,
    "primer": 1.5,
    "restoration": 1.4,
    "seam": 1.4,
    "silicone": 1.8,
    "spray": 1.4,
    "substrate": 1.3,
    "tank": 1.6,
    "tear": 1.5,
    "tpo": 1.8,
    "urethane": 1.5,
    "wall": 1.4,
    "warranty": 1.4,
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", _text(value).lower().replace("-", "_").replace(" ", "_")).strip("_")


def _canonical_template_type(value: Any) -> str:
    normalized = _norm(value)
    if not normalized:
        return ""
    if "insulation" in normalized:
        return "insulation"
    if "roof" in normalized:
        return "roofing"
    if "floor" in normalized:
        return "flooring"
    return normalized


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _frame(data: Any, attr: str) -> pd.DataFrame:
    value = getattr(data, attr, pd.DataFrame()) if data is not None else pd.DataFrame()
    return value.copy() if isinstance(value, pd.DataFrame) else pd.DataFrame(value)


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if not _text(value):
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not _text(value):
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _profile_lookup(profiles: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if profiles.empty or "job_id" not in profiles.columns:
        return {}
    return {
        _text(row.get("job_id")): row
        for row in profiles.fillna("").to_dict(orient="records")
        if _text(row.get("job_id"))
    }


def _selected_name(row: dict[str, Any]) -> str:
    for column in ("resolved_item_name", "selected_item_name", "resolved_template_option", "row_label", "description"):
        value = _text(row.get(column))
        if value:
            return value
    return ""


def _row_number(row: dict[str, Any]) -> str:
    value = row.get("row_number", row.get("workbook_row", ""))
    if value in (None, ""):
        return ""
    number = _number(value, default=float("nan"))
    if number == number:
        return str(int(number)) if number.is_integer() else str(number)
    return _text(value)


def _example_identity(row: dict[str, Any]) -> str:
    for column in ("document_id", "source_file", "job_id"):
        value = _text(row.get(column))
        if value:
            return value
    return ""


def _example_id(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", _text(value)).strip("-").lower()
    return f"historical-template-example-{cleaned or 'unknown'}"


def _decision_row(row: dict[str, Any]) -> dict[str, Any]:
    decision: dict[str, Any] = {}
    for column in DECISION_FIELD_CANDIDATES:
        value = row.get(column)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, float) and not math.isfinite(value):
            continue
        if column in {"row_number", "workbook_row"}:
            decision["workbook_row"] = _row_number(row)
        elif column in {"resolved_item_name", "selected_item_name", "resolved_template_option", "row_label"}:
            decision.setdefault("item", _text(value))
        else:
            decision[column] = value
    if not decision.get("item"):
        decision["item"] = _selected_name(row)
    return {key: value for key, value in decision.items() if value not in (None, "", [], {})}


def _important_decision_rows(group: pd.DataFrame, *, limit: int = 28) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if group.empty:
        return rows
    sortable = group.fillna("").copy()
    if "line_item_kind" not in sortable.columns:
        sortable["line_item_kind"] = ""
    if "template_bucket" not in sortable.columns:
        sortable["template_bucket"] = ""
    if "row_number" not in sortable.columns:
        sortable["row_number"] = ""
    sortable["_kind_rank"] = sortable["line_item_kind"].astype(str).str.lower().map(
        {"material": 0, "equipment": 1, "labor": 2, "pricing": 3, "adder": 4}
    ).fillna(9)
    sortable["_row_number_sort"] = pd.to_numeric(sortable["row_number"], errors="coerce").fillna(9999)
    sortable = sortable.sort_values(["_kind_rank", "_row_number_sort"])
    seen: set[tuple[str, str, str]] = set()
    for row in sortable.to_dict(orient="records"):
        kind = _norm(row.get("line_item_kind"))
        bucket = _norm(row.get("template_bucket"))
        name = _selected_name(row)
        row_number = _row_number(row)
        if kind in NON_DECISION_KINDS:
            continue
        if not bucket and not name:
            continue
        key = (row_number, bucket, name.lower())
        if key in seen:
            continue
        seen.add(key)
        decision = _decision_row(row)
        if decision:
            rows.append(decision)
        if len(rows) >= limit:
            break
    return rows


def _decision_summary(decisions: list[dict[str, Any]], *, limit: int = 10) -> str:
    labels: list[str] = []
    for decision in decisions:
        bucket = _text(decision.get("template_bucket")).replace("_", " ")
        item = _text(decision.get("item"))
        row = _text(decision.get("workbook_row"))
        label = " ".join(part for part in (bucket, item) if part).strip()
        if row:
            label = f"row {row} {label}".strip()
        if label and label not in labels:
            labels.append(label)
        if len(labels) >= limit:
            break
    return "; ".join(labels)


def build_template_examples(data: Any) -> pd.DataFrame:
    rows = _frame(data, "template_rows")
    if rows.empty:
        return pd.DataFrame(columns=TEMPLATE_EXAMPLE_COLUMNS)
    profiles = _frame(data, "job_context_profiles")
    if profiles.empty:
        profiles = build_job_context_profiles(data)
    profiles_by_job = _profile_lookup(profiles)
    if "job_id" not in rows.columns:
        rows["job_id"] = ""
    if "document_id" not in rows.columns:
        rows["document_id"] = ""
    if "source_file" not in rows.columns:
        rows["source_file"] = ""
    rows = rows.copy()
    rows["_example_key"] = rows.apply(lambda row: _example_identity(row.to_dict()), axis=1)
    records: list[dict[str, Any]] = []
    for example_key, group in rows.groupby("_example_key", dropna=False):
        if not _text(example_key):
            continue
        first = group.fillna("").iloc[0].to_dict()
        job_id = _text(first.get("job_id")) or _text(example_key)
        document_id = _text(first.get("document_id"))
        source_file = _text(first.get("source_file"))
        profile = profiles_by_job.get(job_id, {})
        if not profile and profiles_by_job:
            continue
        decisions = _important_decision_rows(group)
        if not decisions:
            continue
        template_type = _text(profile.get("template_type")) or _text(group.get("template_type", pd.Series(dtype=str)).mode().iloc[0] if "template_type" in group.columns and not group["template_type"].mode().empty else "")
        packages = _json_list(profile.get("material_packages_json")) or profile.get("material_packages") or []
        answer_key = build_reference_estimate_answer_key(
            group,
            job_context={
                "job_id": job_id,
                "customer": _text(profile.get("customer")),
                "job_name": _text(profile.get("job_name")),
                "template_type": template_type,
                "project_type": _text(profile.get("project_class")),
                "market_segment": _text(profile.get("market_segment")),
                "building_type": _text(profile.get("building_type")),
                "substrate": _text(profile.get("substrate")),
                "material_system": _text(profile.get("material_system")),
                "scope_summary": _text(profile.get("scope_summary")),
                "area_sqft": _number(profile.get("area_sqft")),
                "warranty_years": _number(profile.get("warranty_years")),
            },
        )
        records.append(
            {
                "example_id": _example_id(_text(example_key)),
                "job_id": job_id,
                "document_id": document_id,
                "source_file": source_file,
                "customer": _text(profile.get("customer")),
                "job_name": _text(profile.get("job_name")),
                "template_type": template_type,
                "project_class": _text(profile.get("project_class")),
                "market_segment": _text(profile.get("market_segment")),
                "building_type": _text(profile.get("building_type")),
                "substrate": _text(profile.get("substrate")),
                "material_system": _text(profile.get("material_system")),
                "material_packages_json": json.dumps(packages, sort_keys=True, default=str),
                "warranty_years": _number(profile.get("warranty_years")),
                "area_sqft": _number(profile.get("area_sqft")),
                "area_bucket": _text(profile.get("area_bucket")),
                "scope_summary": _text(profile.get("scope_summary")),
                "decision_summary": _decision_summary(decisions),
                "decisions_json": json.dumps(decisions, sort_keys=True, default=str),
                "answer_key_json": json.dumps(answer_key, sort_keys=True, default=str),
                "source": "historical_estimate_template_rows",
                "confidence": _number(profile.get("confidence"), 0.5),
            }
        )
    return pd.DataFrame(records, columns=TEMPLATE_EXAMPLE_COLUMNS)


def _answer_key_for_example(data: Any, row: dict[str, Any]) -> dict[str, Any]:
    answer_key = _json_dict(row.get("answer_key_json"))
    if answer_key:
        return answer_key
    job_id = _text(row.get("job_id"))
    source_file = _text(row.get("source_file") or row.get("file_name"))
    try:
        if job_id:
            return build_reference_estimate_answer_key(data, job_id=job_id)
        if source_file:
            return build_reference_estimate_answer_key(data, source_file=source_file)
    except Exception:
        return {}
    return {}


def _example_lookup_value(row: dict[str, Any], field: str) -> str:
    return _text(row.get(field))


def _fetch_answer_keys_for_examples(data: Any, rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    database_url = _text(getattr(data, "database_url", ""))
    if not database_url or not rows:
        return {}
    example_ids = sorted(
        {
            _example_lookup_value(row, "example_id")
            for row in rows
            if _example_lookup_value(row, "example_id")
        }
    )
    document_ids = sorted(
        {
            _example_lookup_value(row, "document_id")
            for row in rows
            if _example_lookup_value(row, "document_id")
        }
    )
    source_files = sorted(
        {
            _example_lookup_value(row, "source_file")
            for row in rows
            if _example_lookup_value(row, "source_file")
        }
    )
    if not example_ids and not document_ids and not source_files:
        return {}
    filters: list[str] = []
    params: dict[str, Any] = {}
    if example_ids:
        filters.append("example_id IN :example_ids")
        params["example_ids"] = example_ids
    if document_ids:
        filters.append("document_id IN :document_ids")
        params["document_ids"] = document_ids
    if source_files:
        filters.append("source_file IN :source_files")
        params["source_files"] = source_files
    statement = text(
        f"""
        SELECT example_id, document_id, source_file, answer_key_json
        FROM analytics.estimator_template_examples
        WHERE answer_key_json IS NOT NULL
          AND ({' OR '.join(filters)})
        """
    )
    if example_ids:
        statement = statement.bindparams(bindparam("example_ids", expanding=True))
    if document_ids:
        statement = statement.bindparams(bindparam("document_ids", expanding=True))
    if source_files:
        statement = statement.bindparams(bindparam("source_files", expanding=True))
    hydrated: dict[str, dict[str, Any]] = {}
    try:
        engine = create_resilient_engine(database_url)
        with engine.connect() as connection:
            records = connection.execute(statement, params).mappings().all()
    except Exception:
        return {}
    for record in records:
        answer_key = _json_dict(record.get("answer_key_json"))
        if not answer_key:
            continue
        for field in ("example_id", "document_id", "source_file"):
            value = _text(record.get(field))
            if value:
                hydrated.setdefault(f"{field}:{value}", answer_key)
    return hydrated


def _hydrated_answer_key_for_row(
    data: Any,
    row: dict[str, Any],
    hydrated_answer_keys: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    answer_key = _json_dict(row.get("answer_key_json"))
    if answer_key:
        return answer_key
    for field in ("example_id", "document_id", "source_file"):
        value = _text(row.get(field))
        if not value:
            continue
        answer_key = hydrated_answer_keys.get(f"{field}:{value}")
        if answer_key:
            return answer_key
    return _answer_key_for_example(data, row)


def _scope_answer_key_text(scope: dict[str, Any]) -> str:
    fields = (
        "template_type",
        "division",
        "project_type",
        "project_class",
        "market_segment",
        "building_type",
        "substrate",
        "roof_type_substrate",
        "material_system",
        "coating_type",
        "foam_type",
        "roof_condition",
        "access_complexity",
        "raw_input_notes",
        "notes",
        "estimator_notes",
    )
    return " ".join(_text(scope.get(field)) for field in fields if _text(scope.get(field))).lower()


def _scope_answer_key_packages(scope: dict[str, Any]) -> set[str]:
    text = _scope_answer_key_text(scope)
    packages: set[str] = set()
    package_terms = {
        "foam": ("foam", "spf", "spray foam"),
        "roofing_foam": ("roof foam", "roof spf", "spf roof"),
        "coating": ("coating", "silicone", "acrylic", "urethane", "restoration", "top coat"),
        "primer": ("primer", "prime"),
        "caulk_detail": ("caulk", "sealant", "sausage", "flashing", "detail"),
        "fabric": ("fabric", "reinforcement"),
        "seams_misc": ("seam", "seams"),
        "fasteners": ("fastener", "fasteners", "screw"),
        "plates": ("plate", "plates"),
        "board_stock": ("board", "iso", "cover board"),
        "granules": ("granule", "granules"),
        "thermal_barrier": ("thermal barrier", "dc315", "noburn", "ignition barrier"),
        "generator": ("generator",),
        "truck_expense": ("truck", "miles", "mileage"),
        "sales_inspection_trips": ("sales", "inspection", "site visit"),
    }
    for package, terms in package_terms.items():
        if any(term in text for term in terms):
            packages.add(package)
    scope_packages = scope.get("material_packages") or scope.get("scope_triggers") or []
    if isinstance(scope_packages, str):
        scope_packages = _json_list(scope_packages)
    if isinstance(scope_packages, (list, tuple, set)):
        packages.update(_norm(value) for value in scope_packages if _text(value))
    if _text(scope.get("foam_type")):
        packages.add("foam")
    if _text(scope.get("coating_type")):
        packages.add("coating")
    return {package for package in packages if package}


def _answer_key_decision_priority(
    decision: dict[str, Any],
    *,
    preferred_decision_ids: set[str],
    preferred_buckets: set[str],
) -> tuple[int, int, int]:
    decision_id = _text(decision.get("decision_id"))
    bucket = _norm(decision.get("template_bucket"))
    section = _norm(decision.get("section"))
    outputs = decision.get("calculated_outputs") if isinstance(decision.get("calculated_outputs"), dict) else {}
    inputs = decision.get("inputs") if isinstance(decision.get("inputs"), dict) else {}
    score = 0
    if decision_id and decision_id in preferred_decision_ids:
        score += 80
    if bucket and bucket in preferred_buckets:
        score += 50
    if _number(outputs.get("estimated_cost") or outputs.get("calculated_cost"), 0.0) > 0:
        score += 18
    if inputs:
        score += 10
    if "labor" in section or bucket.startswith("labor_") or bucket in {"meals_lodging", "infrared_scan"}:
        score += 18
    if section.endswith("material_template_decisions") or "material" in section:
        score += 8
    if "pricing_markup" in section:
        score += 3
    row_number = _number(decision.get("workbook_row") or decision.get("source_row"), 9999)
    return (score, -int(row_number or 9999), -len(str(decision)))


def _compact_answer_key(
    answer_key: dict[str, Any],
    *,
    limit: int = 12,
    preferred_decision_ids: set[str] | None = None,
    preferred_buckets: set[str] | None = None,
) -> dict[str, Any]:
    if not answer_key:
        return {}
    preferred_decision_ids = preferred_decision_ids or set()
    preferred_buckets = preferred_buckets or set()
    raw_decisions = [decision for decision in answer_key.get("decisions") or [] if isinstance(decision, dict)]
    if preferred_decision_ids or preferred_buckets:
        raw_decisions = sorted(
            raw_decisions,
            key=lambda decision: _answer_key_decision_priority(
                decision,
                preferred_decision_ids=preferred_decision_ids,
                preferred_buckets=preferred_buckets,
            ),
            reverse=True,
        )
    decisions: list[dict[str, Any]] = []
    for decision in raw_decisions:
        decisions.append(
            {
                "decision_id": decision.get("decision_id"),
                "section": decision.get("section"),
                "template_bucket": decision.get("template_bucket"),
                "workbook_row": decision.get("workbook_row"),
                "source_row": decision.get("source_row"),
                "line_item": decision.get("line_item"),
                "template_option": decision.get("template_option"),
                "inputs": decision.get("inputs") or {},
                "calculated_outputs": decision.get("calculated_outputs") or {},
                "evidence": decision.get("evidence") or {},
                "confidence": decision.get("confidence"),
                "needs_review": decision.get("needs_review"),
            }
        )
        if len(decisions) >= limit:
            break
    return {
        "schema_version": answer_key.get("schema_version"),
        "source_workbook": answer_key.get("source_workbook") or {},
        "job_context": answer_key.get("job_context") or {},
        "decisions": decisions,
        "summary": {
            "decision_count": (answer_key.get("summary") or {}).get("decision_count", len(answer_key.get("decisions") or [])),
            "unmapped_count": (answer_key.get("summary") or {}).get("unmapped_count", 0),
        },
    }


def _area_match_score(example_area: float, scope_area: float) -> tuple[float, str]:
    if example_area <= 0 or scope_area <= 0:
        return (0.0, "")
    ratio = max(example_area, scope_area) / max(min(example_area, scope_area), 1.0)
    if ratio <= 1.25:
        return (35.0, "similar area")
    if ratio <= 2.0:
        return (20.0, "same area order")
    if ratio <= 4.0:
        return (8.0, "loose area match")
    return (0.0, "")


def _answer_key_similarity_tokens(value: str) -> set[str]:
    tokens: set[str] = set()
    for token in re.findall(r"[a-z0-9]{3,}", str(value or "").lower()):
        if token.isdigit():
            continue
        if token in ANSWER_KEY_TEXT_STOPWORDS:
            continue
        if token.startswith("01x"):
            continue
        if re.fullmatch(r"\d+[a-z]{0,2}", token):
            continue
        tokens.add(token)
    return tokens


def _token_overlap_score(left: str, right: str) -> tuple[float, str]:
    left_tokens = _answer_key_similarity_tokens(left)
    right_tokens = _answer_key_similarity_tokens(right)
    if not left_tokens or not right_tokens:
        return (0.0, "")
    overlap = left_tokens & right_tokens
    if not overlap:
        return (0.0, "")
    weighted = sorted(
        ((ANSWER_KEY_TECHNICAL_TOKEN_WEIGHTS.get(token, 1.0), token) for token in overlap),
        reverse=True,
    )
    score = min(sum(weight for weight, _token in weighted) * 5.0, 38.0)
    return (score, f"text overlap: {', '.join(token for _weight, token in weighted[:5])}")


def _name_overlap_score(scope_text: str, row: dict[str, Any]) -> tuple[float, str]:
    stopwords = {
        "hist",
        "proposal",
        "estimate",
        "driveitem",
        "source",
        "roofing",
        "insulation",
        "flooring",
        "roof",
        "job",
        "the",
        "and",
        "with",
    }
    name_text = " ".join(
        _text(row.get(field))
        for field in ("customer", "job_name", "source_file")
        if _text(row.get(field))
    )
    name_tokens = {
        token
        for token in re.findall(r"[a-z0-9]{4,}", name_text.lower())
        if token not in stopwords and not token.startswith("01x") and not token.isdigit()
    }
    if not name_tokens or not scope_text:
        return (0.0, "")
    scope_tokens = {
        token
        for token in re.findall(r"[a-z0-9]{4,}", scope_text.lower())
        if token not in stopwords and not token.isdigit()
    }
    overlap = name_tokens & scope_tokens
    if not overlap:
        return (0.0, "")
    score = min(len(overlap) * 18.0, 90.0)
    return (score, f"name overlap: {', '.join(sorted(overlap)[:5])}")


def _identity_match_enabled(scope: dict[str, Any]) -> bool:
    if bool(scope.get("allow_identity_retrieval") or scope.get("target_job_id") or scope.get("reference_job_ids")):
        return True
    text = _scope_answer_key_text(scope)
    return bool(
        re.search(
            r"\b(?:like|similar\s+to|same\s+as|based\s+on|use\s+the|reference\s+job|answer\s+key\s+for|find\s+(?:me\s+)?(?:the\s+)?job)\b",
            text,
        )
    )


def _answer_key_example_score(
    row: dict[str, Any],
    answer_key: dict[str, Any],
    scope: dict[str, Any],
    *,
    profile_score: float = 0.0,
) -> tuple[float, list[str]]:
    reasons: list[str] = []
    score = 0.0
    scope_text = _scope_answer_key_text(scope)
    example_text = " ".join(
        _text(row.get(field))
        for field in (
            "customer",
            "job_name",
            "source_file",
            "template_type",
            "project_class",
            "market_segment",
            "building_type",
            "substrate",
            "material_system",
            "scope_summary",
            "decision_summary",
        )
        if _text(row.get(field))
    ).lower()
    scope_template = _norm(scope.get("template_type") or scope.get("division"))
    example_template = _norm(row.get("template_type"))
    if scope_template and example_template and (scope_template == example_template or scope_template in example_template or example_template in scope_template):
        score += 90
        reasons.append(f"template={example_template}")
    if profile_score > 0:
        score += min(profile_score * 0.4, 60.0)
        reasons.append("matched job profile")
    for field, weight in (("project_class", 35), ("market_segment", 18), ("building_type", 25), ("substrate", 30), ("material_system", 20)):
        value = _text(row.get(field)).lower().replace("_", " ")
        if value and value != "unknown" and value in scope_text:
            score += weight
            reasons.append(f"{field}={value}")
    scope_packages = _scope_answer_key_packages(scope)
    example_packages = {_norm(value) for value in _json_list(row.get("material_packages_json")) if _text(value)}
    answer_key_buckets = {
        _norm(decision.get("template_bucket"))
        for decision in answer_key.get("decisions") or []
        if isinstance(decision, dict) and _text(decision.get("template_bucket"))
    }
    example_packages.update(answer_key_buckets)
    package_overlap = scope_packages & example_packages
    if package_overlap:
        score += min(len(package_overlap) * 22.0, 70.0)
        reasons.append("packages: " + ", ".join(sorted(package_overlap)[:6]))
    scope_area = _number(scope.get("estimated_sqft") or scope.get("net_sqft") or scope.get("area_sqft"), 0.0)
    area_score, area_reason = _area_match_score(_number(row.get("area_sqft"), 0.0), scope_area)
    if area_score:
        score += area_score
        reasons.append(area_reason)
    scope_warranty = _number(scope.get("warranty_target_years") or scope.get("warranty_years"), 0.0)
    example_warranty = _number(row.get("warranty_years"), 0.0)
    if scope_warranty and example_warranty:
        if abs(scope_warranty - example_warranty) <= 1:
            score += 20
            reasons.append("similar warranty")
        elif abs(scope_warranty - example_warranty) <= 5:
            score += 8
            reasons.append("near warranty")
    if _identity_match_enabled(scope):
        name_score, name_reason = _name_overlap_score(scope_text, row)
        if name_score:
            score += name_score
            reasons.append(name_reason)
    text_score, text_reason = _token_overlap_score(scope_text, example_text)
    if text_score:
        score += text_score
        reasons.append(text_reason)
    decision_count = _number((answer_key.get("summary") or {}).get("decision_count"), len(answer_key.get("decisions") or []))
    if decision_count > 0:
        score += min(decision_count * 0.5, 18.0)
        reasons.append(f"{int(decision_count)} answer-key decisions")
    return score, reasons[:8]


def build_similar_answer_key_digest(
    data: Any,
    *,
    scope: dict[str, Any] | None = None,
    limit: int = 5,
    decisions_per_example: int = 20,
    decision_menu: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    scope = scope or {}
    examples = _frame(data, "template_examples")
    if examples.empty:
        examples = build_template_examples(data)
    if examples.empty:
        return {"matched_answer_keys": []}
    profile_digest = build_job_context_digest(data, scope=scope, limit=max(limit * 4, 10))
    profile_scores = {
        _text(profile.get("job_id")): _number(profile.get("similarity_score"))
        for profile in profile_digest.get("matched_profiles") or []
        if _text(profile.get("job_id"))
    }
    preferred_decision_ids = {
        _text(row.get("decision_id"))
        for row in decision_menu or []
        if isinstance(row, dict) and _text(row.get("decision_id"))
    }
    preferred_buckets = {
        _norm(row.get("template_bucket"))
        for row in decision_menu or []
        if isinstance(row, dict) and _text(row.get("template_bucket"))
    }
    preferred_buckets.update(_scope_answer_key_packages(scope))
    scored: list[tuple[float, dict[str, Any], dict[str, Any], list[str]]] = []
    scope_template_type = _canonical_template_type(scope.get("template_type") or scope.get("division") or scope.get("project_type"))
    for row in examples.fillna("").to_dict(orient="records"):
        example_template_type = _canonical_template_type(row.get("template_type"))
        if scope_template_type and example_template_type and scope_template_type != example_template_type:
            continue
        answer_key = _json_dict(row.get("answer_key_json"))
        score, reasons = _answer_key_example_score(
            row,
            answer_key,
            scope,
            profile_score=profile_scores.get(_text(row.get("job_id")), 0.0),
        )
        if score <= 0:
            continue
        scored.append((score, row, answer_key, reasons))
    scored.sort(key=lambda item: item[0], reverse=True)
    matched: list[dict[str, Any]] = []
    candidate_window = scored[: max(max(0, int(limit or 5)) * 4, max(0, int(limit or 5)))]
    hydrated_answer_keys = _fetch_answer_keys_for_examples(data, [row for _, row, _, _ in candidate_window])
    for score, row, answer_key, reasons in candidate_window:
        if not answer_key or not answer_key.get("decisions"):
            answer_key = _hydrated_answer_key_for_row(data, row, hydrated_answer_keys)
        if not answer_key or not answer_key.get("decisions"):
            continue
        compact = _compact_answer_key(
            answer_key,
            limit=decisions_per_example,
            preferred_decision_ids=preferred_decision_ids,
            preferred_buckets=preferred_buckets,
        )
        matched.append(
            {
                "example_id": row.get("example_id"),
                "job_id": row.get("job_id"),
                "customer": row.get("customer"),
                "job_name": row.get("job_name"),
                "source_file": row.get("source_file"),
                "similarity_score": round(score, 3),
                "match_reasons": reasons,
                "template_type": row.get("template_type"),
                "project_class": row.get("project_class"),
                "market_segment": row.get("market_segment"),
                "building_type": row.get("building_type"),
                "substrate": row.get("substrate"),
                "material_system": row.get("material_system"),
                "warranty_years": row.get("warranty_years"),
                "area_sqft": row.get("area_sqft"),
                "scope_summary": row.get("scope_summary"),
                "reference_answer_key": compact,
            }
        )
        if len(matched) >= max(0, int(limit or 5)):
            break
    return {
        "matched_answer_keys": matched,
        "retrieval": {
            "candidate_count": len(scored),
            "limit": int(limit or 5),
            "decisions_per_example": int(decisions_per_example or 20),
            "preferred_buckets": sorted(preferred_buckets)[:30],
        },
    }


def build_template_example_digest(data: Any, *, scope: dict[str, Any] | None = None, limit: int = 3) -> dict[str, Any]:
    scope = scope or {}
    examples = _frame(data, "template_examples")
    if examples.empty:
        examples = build_template_examples(data)
    if examples.empty:
        return {"matched_examples": []}
    profile_digest = build_job_context_digest(data, scope=scope, limit=max(limit * 3, 6))
    profile_scores = {
        _text(profile.get("job_id")): _number(profile.get("similarity_score"))
        for profile in profile_digest.get("matched_profiles") or []
        if _text(profile.get("job_id"))
    }
    records = examples.fillna("").to_dict(orient="records")
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in records:
        job_id = _text(row.get("job_id"))
        score = profile_scores.get(job_id, 0.0)
        if score <= 0:
            score = _fallback_example_score(row, scope)
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    matched: list[dict[str, Any]] = []
    for score, row in scored[: max(0, int(limit or 3))]:
        decisions = _json_list(row.get("decisions_json"))
        reference_answer_key = _compact_answer_key(_answer_key_for_example(data, row), limit=12)
        matched.append(
            {
                "example_id": row.get("example_id"),
                "job_id": row.get("job_id"),
                "customer": row.get("customer"),
                "job_name": row.get("job_name"),
                "similarity_score": round(score, 3),
                "template_type": row.get("template_type"),
                "project_class": row.get("project_class"),
                "market_segment": row.get("market_segment"),
                "building_type": row.get("building_type"),
                "substrate": row.get("substrate"),
                "material_system": row.get("material_system"),
                "warranty_years": row.get("warranty_years"),
                "area_sqft": row.get("area_sqft"),
                "scope_summary": row.get("scope_summary"),
                "decision_summary": row.get("decision_summary"),
                "decisions": decisions[:18],
                "reference_answer_key": reference_answer_key,
            }
        )
    return {"matched_examples": matched}


def _fallback_example_score(example: dict[str, Any], scope: dict[str, Any]) -> float:
    scope_text = " ".join(str(scope.get(key) or "") for key in ("template_type", "division", "project_type", "substrate", "roof_type_substrate", "coating_type", "foam_type", "raw_input_notes", "notes")).lower()
    if not scope_text:
        return 0.0
    score = 0.0
    if _text(example.get("template_type")).lower() and _text(example.get("template_type")).lower() in scope_text:
        score += 60
    for field, weight in (("project_class", 25), ("building_type", 15), ("substrate", 20), ("material_system", 15)):
        value = _text(example.get(field)).lower().replace("_", " ")
        if value and value != "unknown" and value in scope_text:
            score += weight
    packages = _json_list(example.get("material_packages_json"))
    if "coating" in scope_text and "coating" in packages:
        score += 15
    if "foam" in scope_text and ("foam" in packages or "roofing_foam" in packages):
        score += 15
    return score


def write_template_examples_table(engine: Any, examples: pd.DataFrame, *, schema: str = "analytics") -> int:
    if examples.empty:
        return 0
    with engine.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
    examples.to_sql("estimator_template_examples", engine, schema=schema, if_exists="replace", index=False, chunksize=1000)
    return int(len(examples))


def main(argv: list[str] | None = None) -> int:
    from .data_loader import load_estimator_data

    parser = argparse.ArgumentParser(description="Build whole historical estimator template examples from mined template rows.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL"), help="Database URL. Defaults to DATABASE_URL/NEON_DATABASE_URL.")
    parser.add_argument("--output-dir", default="output/estimator_template_examples", help="Directory for CSV output.")
    parser.add_argument("--write-db", action="store_true", help="Write analytics.estimator_template_examples.")
    parser.add_argument("--limit-print", type=int, default=10, help="Number of sample rows to print.")
    args = parser.parse_args(argv)

    data = load_estimator_data(Path.cwd(), database_url=args.database_url, prefer_database=bool(args.database_url), load_profile="full")
    examples = build_template_examples(data)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "estimator_template_examples.csv"
    examples.to_csv(csv_path, index=False)
    print(f"Template example rows: {len(examples):,}")
    print(f"CSV: {csv_path}")
    if not examples.empty:
        print(examples.head(max(args.limit_print, 0)).to_string(index=False))
    if args.write_db:
        if not args.database_url:
            raise RuntimeError("--write-db requires --database-url or DATABASE_URL/NEON_DATABASE_URL")
        count = write_template_examples_table(create_resilient_engine(args.database_url), examples)
        print(f"Database rows written: {count:,} to analytics.estimator_template_examples")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
