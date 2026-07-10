from __future__ import annotations

import argparse
import json
import math
import os
import re
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text

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


def _text(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", _text(value).lower().replace("-", "_").replace(" ", "_")).strip("_")


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


def _compact_answer_key(answer_key: dict[str, Any], *, limit: int = 12) -> dict[str, Any]:
    if not answer_key:
        return {}
    decisions: list[dict[str, Any]] = []
    for decision in answer_key.get("decisions") or []:
        if not isinstance(decision, dict):
            continue
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
