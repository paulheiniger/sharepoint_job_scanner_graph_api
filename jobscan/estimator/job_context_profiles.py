from __future__ import annotations

import argparse
import json
import math
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text

from jobscan.db_connections import create_resilient_engine


PROFILE_COLUMNS = [
    "job_id",
    "customer",
    "job_name",
    "template_type",
    "project_class",
    "market_segment",
    "building_type",
    "substrate",
    "material_system",
    "material_packages",
    "material_packages_json",
    "warranty_years",
    "area_sqft",
    "area_bucket",
    "scope_summary",
    "scope_evidence_excerpt",
    "source_documents_json",
    "confidence",
]

PACKAGE_BUCKETS = {
    "foam",
    "coating",
    "primer",
    "caulk_detail",
    "caulk_sealant",
    "fabric",
    "seams_misc",
    "penetrations",
    "board_stock",
    "fasteners",
    "plates",
    "granules",
    "thermal_barrier",
    "generator",
    "truck_expense",
    "sales_inspection_trips",
    "labor_foam",
    "labor_coating",
    "flooring_coating",
    "flooring_flake",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: Any) -> str:
    return " ".join(_text(value).lower().replace("_", " ").replace("-", " ").split())


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _mode_text(values: list[Any]) -> str:
    cleaned = [_text(value) for value in values if _text(value)]
    if not cleaned:
        return ""
    return Counter(cleaned).most_common(1)[0][0]


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


def _area_bucket(area: float) -> str:
    if area <= 0:
        return "unknown"
    if area < 5_000:
        return "under_5k"
    if area < 15_000:
        return "5k_15k"
    if area < 50_000:
        return "15k_50k"
    return "50k_plus"


def _classify_market_segment(text: str) -> str:
    normalized = _norm(text)
    if any(term in normalized for term in ("pole barn", "barn", "farm", "agricultural")):
        return "agricultural"
    if any(term in normalized for term in ("school", "elementary", "library", "university", "college")):
        return "institutional"
    if any(term in normalized for term in ("plant", "factory", "warehouse", "industrial", "manufacturing", "conveyor")):
        return "industrial"
    if any(term in normalized for term in ("residence", "residential", "home", "house")):
        return "residential"
    if any(term in normalized for term in ("church", "retail", "shop", "office", "commercial")):
        return "commercial"
    return "unknown"


def _classify_building_type(text: str) -> str:
    normalized = _norm(text)
    if "pole barn" in normalized:
        return "pole_barn"
    if "metal building" in normalized or "metal barn" in normalized:
        return "metal_building"
    if "warehouse" in normalized or "whse" in normalized:
        return "warehouse"
    if "library" in normalized:
        return "library"
    if "school" in normalized or "elementary" in normalized:
        return "school"
    if "conveyor" in normalized or "belt ramp" in normalized:
        return "industrial_enclosure"
    if "residence" in normalized or "house" in normalized or "home" in normalized:
        return "residential"
    return "unknown"


def _classify_substrate(text: str) -> str:
    normalized = _norm(text)
    if "standing seam" in normalized:
        return "standing_seam_metal"
    if "metal" in normalized:
        return "metal"
    if "tpo" in normalized:
        return "tpo"
    if "epdm" in normalized:
        return "epdm"
    if "mod bit" in normalized or "modified bitumen" in normalized:
        return "modified_bitumen"
    if "concrete" in normalized:
        return "concrete"
    return "unknown"


def _classify_project(template_type: str, packages: set[str], text: str) -> str:
    normalized = _norm(text)
    if template_type == "insulation":
        if "pole barn" in normalized:
            return "insulation_pole_barn"
        if "metal building" in normalized or "metal barn" in normalized:
            return "insulation_metal_building"
        return "insulation_spray_foam" if "foam" in packages or "spray foam" in normalized else "insulation_general"
    if template_type == "flooring":
        if "flake" in normalized:
            return "flooring_flake"
        if "epoxy" in normalized or "polyaspartic" in normalized:
            return "flooring_resinous"
        return "flooring_general"
    if "roofing_foam" in packages or ("foam" in packages and "roof" in normalized):
        return "roof_spf"
    if "coating" in packages or "restoration" in normalized or "coating" in normalized:
        return "roof_restoration"
    if "repair" in normalized or "leak" in normalized:
        return "roof_repair"
    return "roofing_general"


def _scope_summary(text: str, *, limit: int = 360) -> str:
    cleaned = " ".join(_text(text).split())
    if not cleaned:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    summary = " ".join(sentences[:2]).strip() or cleaned
    return summary[:limit]


def _frame(data: Any, attr: str) -> pd.DataFrame:
    value = getattr(data, attr, pd.DataFrame()) if data is not None else pd.DataFrame()
    return value.copy() if isinstance(value, pd.DataFrame) else pd.DataFrame(value)


def _job_lookup(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if frame.empty or "job_id" not in frame.columns:
        return {}
    return {
        _text(row.get("job_id")): row
        for row in frame.fillna("").to_dict(orient="records")
        if _text(row.get("job_id"))
    }


def _scope_text_by_job(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if frame.empty or "job_id" not in frame.columns:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for job_id, group in frame.fillna("").groupby("job_id", dropna=False):
        key = _text(job_id)
        if not key:
            continue
        texts = [_text(value) for value in group.get("scope_text", pd.Series(dtype=str)).tolist() if _text(value)]
        documents = []
        for row in group.to_dict(orient="records"):
            label = _text(row.get("file_name") or row.get("document_type") or row.get("document_id"))
            if label:
                documents.append(label)
        out[key] = {"scope_text": "\n".join(texts), "source_documents": list(dict.fromkeys(documents))[:5]}
    return out


def build_job_context_profiles(data: Any) -> pd.DataFrame:
    rows = _frame(data, "template_rows")
    if rows.empty:
        return pd.DataFrame(columns=PROFILE_COLUMNS)
    jobs = _job_lookup(_frame(data, "jobs"))
    scope_texts = _scope_text_by_job(_frame(data, "historical_scope_texts"))
    if "job_id" not in rows.columns:
        rows["job_id"] = ""
    if "source_file" not in rows.columns:
        rows["source_file"] = ""
    keys = rows["job_id"].fillna("").astype(str).str.strip()
    rows = rows.copy()
    rows["_profile_key"] = keys.where(keys.ne(""), rows["source_file"].fillna("").astype(str))
    records: list[dict[str, Any]] = []
    for profile_key, group in rows.fillna("").groupby("_profile_key", dropna=False):
        job_id = _text(profile_key)
        if not job_id:
            continue
        group_records = group.to_dict(orient="records")
        job = jobs.get(job_id, {})
        scope_info = scope_texts.get(job_id, {})
        text_parts = [
            _text(job.get("customer")),
            _text(job.get("job_name")),
            _text(job.get("project_type")),
            _text(job.get("division")),
            _text(scope_info.get("scope_text")),
            " ".join(_text(row.get(field)) for row in group_records for field in ("project_type", "row_label", "selected_item_name", "resolved_item_name", "substrate", "coating_type")),
        ]
        combined_text = " ".join(part for part in text_parts if part)
        template_type = _norm(_mode_text([row.get("template_type") for row in group_records]))
        if "insulation" in template_type:
            template_type = "insulation"
        elif "floor" in template_type:
            template_type = "flooring"
        elif "roof" in template_type:
            template_type = "roofing"
        else:
            template_type = _norm(_text(job.get("division"))) or "unknown"
        packages = {
            _norm(row.get("template_bucket")).replace(" ", "_")
            for row in group_records
            if _norm(row.get("template_bucket")).replace(" ", "_") in PACKAGE_BUCKETS
        }
        if template_type == "roofing" and "foam" in packages:
            packages.add("roofing_foam")
        area = max(
            [_number(row.get(field), 0.0) for row in group_records for field in ("area_sqft", "estimated_sqft", "net_sqft", "quantity")]
            or [0.0]
        )
        warranty = max([_number(row.get("warranty_years"), 0.0) for row in group_records] or [0.0])
        material_names = [
            _text(row.get(field))
            for row in group_records
            for field in ("resolved_item_name", "selected_item_name", "row_label")
            if _text(row.get(field)) and _norm(row.get("template_bucket")).replace(" ", "_") in packages
        ]
        material_system = ", ".join(list(dict.fromkeys(material_names))[:5])
        substrate = _classify_substrate(" ".join([combined_text, _text(job.get("substrate"))]))
        building_type = _classify_building_type(combined_text)
        market_segment = _classify_market_segment(combined_text)
        project_class = _classify_project(template_type, packages, combined_text)
        confidence = 0.35
        confidence += 0.2 if packages else 0.0
        confidence += 0.15 if substrate != "unknown" else 0.0
        confidence += 0.15 if scope_info.get("scope_text") else 0.0
        confidence += 0.1 if area > 0 else 0.0
        records.append(
            {
                "job_id": job_id,
                "customer": _text(job.get("customer")),
                "job_name": _text(job.get("job_name")),
                "template_type": template_type,
                "project_class": project_class,
                "market_segment": market_segment,
                "building_type": building_type,
                "substrate": substrate,
                "material_system": material_system,
                "material_packages": sorted(packages),
                "material_packages_json": json.dumps(sorted(packages)),
                "warranty_years": warranty if warranty > 0 else 0.0,
                "area_sqft": area,
                "area_bucket": _area_bucket(area),
                "scope_summary": _scope_summary(_text(scope_info.get("scope_text")) or combined_text),
                "scope_evidence_excerpt": _scope_summary(_text(scope_info.get("scope_text")), limit=500),
                "source_documents_json": json.dumps(scope_info.get("source_documents") or []),
                "confidence": round(min(confidence, 0.95), 3),
            }
        )
    return pd.DataFrame(records, columns=PROFILE_COLUMNS)


def _scope_tokens(scope: dict[str, Any]) -> set[str]:
    values = [
        scope.get("template_type"),
        scope.get("division"),
        scope.get("project_type"),
        scope.get("building_type"),
        scope.get("substrate"),
        scope.get("roof_type_substrate"),
        scope.get("coating_type"),
        scope.get("raw_input_notes"),
        scope.get("notes"),
    ]
    return {token for value in values for token in _norm(value).split() if token}


def _profile_score(profile: dict[str, Any], scope: dict[str, Any]) -> float:
    score = 0.0
    template = _norm(scope.get("template_type") or scope.get("division"))
    if template and _norm(profile.get("template_type")) == ("roofing" if "roof" in template else "insulation" if "insulation" in template else "flooring" if "floor" in template else template):
        score += 80
    scope_text = " ".join(str(scope.get(key) or "") for key in ("project_type", "building_type", "substrate", "roof_type_substrate", "coating_type", "raw_input_notes", "notes"))
    scope_norm = _norm(scope_text)
    for field, weight in (("project_class", 35), ("building_type", 25), ("substrate", 25), ("market_segment", 15)):
        value = _norm(profile.get(field))
        if value and value != "unknown" and value.replace("_", " ") in scope_norm:
            score += weight
    packages = set(_json_list(profile.get("material_packages_json")) or profile.get("material_packages") or [])
    if "coating" in scope_norm and "coating" in packages:
        score += 20
    if "foam" in scope_norm and ("foam" in packages or "roofing_foam" in packages):
        score += 20
    warranty = _number(scope.get("warranty_years") or scope.get("warranty_target_years"), 0.0)
    if warranty > 0 and int(warranty) == int(_number(profile.get("warranty_years"), 0.0)):
        score += 20
    area = _number(scope.get("estimated_sqft") or scope.get("net_sqft") or scope.get("net_insulation_area_sqft"), 0.0)
    if area > 0 and _area_bucket(area) == _text(profile.get("area_bucket")):
        score += 10
    token_overlap = len(_scope_tokens(scope) & set(_norm(" ".join(str(profile.get(key) or "") for key in ("job_name", "scope_summary", "material_system"))).split()))
    return score + min(token_overlap, 20) + _number(profile.get("confidence"), 0.0)


def build_job_context_digest(data: Any, *, scope: dict[str, Any] | None = None, limit: int = 5) -> dict[str, Any]:
    scope = scope or {}
    profiles = _frame(data, "job_context_profiles")
    if profiles.empty:
        profiles = build_job_context_profiles(data)
    if profiles.empty:
        return {"matched_profiles": [], "aggregate_priors": []}
    records = profiles.fillna("").to_dict(orient="records")
    scored = []
    for row in records:
        score = _profile_score(row, scope)
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    matched = []
    for score, row in scored[:limit]:
        packages = _json_list(row.get("material_packages_json")) or row.get("material_packages") or []
        matched.append(
            {
                "job_id": row.get("job_id"),
                "customer": row.get("customer"),
                "job_name": row.get("job_name"),
                "similarity_score": round(score, 3),
                "project_class": row.get("project_class"),
                "market_segment": row.get("market_segment"),
                "building_type": row.get("building_type"),
                "substrate": row.get("substrate"),
                "material_system": row.get("material_system"),
                "material_packages": packages,
                "warranty_years": row.get("warranty_years"),
                "area_sqft": row.get("area_sqft"),
                "scope_summary": row.get("scope_summary"),
            }
        )
    prior_groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for _, row in scored[:50]:
        key = (_text(row.get("template_type")), _text(row.get("project_class")), _text(row.get("substrate")))
        prior_groups.setdefault(key, []).append(row)
    priors = []
    for (template_type, project_class, substrate), rows in prior_groups.items():
        package_counter: Counter[str] = Counter()
        for row in rows:
            package_counter.update(_json_list(row.get("material_packages_json")) or row.get("material_packages") or [])
        priors.append(
            {
                "condition": " + ".join(part for part in (template_type, project_class, substrate) if part and part != "unknown"),
                "normally_included": [name for name, _ in package_counter.most_common(10)],
                "evidence_count": len(rows),
            }
        )
    priors.sort(key=lambda row: row["evidence_count"], reverse=True)
    return {"matched_profiles": matched, "aggregate_priors": priors[:5]}


def write_job_context_profiles_table(engine: Any, profiles: pd.DataFrame, *, schema: str = "analytics") -> int:
    if profiles.empty:
        return 0
    with engine.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
    profiles.to_sql("estimator_job_context_profiles", engine, schema=schema, if_exists="replace", index=False, chunksize=1000)
    return int(len(profiles))


def main(argv: list[str] | None = None) -> int:
    from .data_loader import load_estimator_data

    parser = argparse.ArgumentParser(description="Mine historical job context profiles from estimate template rows and scope text.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL"), help="Database URL. Defaults to DATABASE_URL/NEON_DATABASE_URL.")
    parser.add_argument("--output-dir", default="output/estimator_job_context_profiles", help="Directory for CSV output.")
    parser.add_argument("--write-db", action="store_true", help="Write analytics.estimator_job_context_profiles.")
    parser.add_argument("--limit-print", type=int, default=10, help="Number of sample rows to print.")
    args = parser.parse_args(argv)

    data = load_estimator_data(Path.cwd(), database_url=args.database_url, prefer_database=bool(args.database_url), load_profile="full")
    profiles = build_job_context_profiles(data)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "estimator_job_context_profiles.csv"
    profiles.to_csv(csv_path, index=False)
    print(f"Job context profile rows: {len(profiles):,}")
    print(f"CSV: {csv_path}")
    if not profiles.empty:
        print(profiles.head(max(args.limit_print, 0)).to_string(index=False))
    if args.write_db:
        if not args.database_url:
            raise RuntimeError("--write-db requires --database-url or DATABASE_URL/NEON_DATABASE_URL")
        count = write_job_context_profiles_table(create_resilient_engine(args.database_url), profiles)
        print(f"Database rows written: {count:,} to analytics.estimator_job_context_profiles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
