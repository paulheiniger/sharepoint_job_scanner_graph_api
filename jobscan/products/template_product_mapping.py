from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pandas as pd

from jobscan.estimator.schemas import EstimatorData
from jobscan.products.product_catalog import normalize_product_name, slugify
from jobscan.products.product_matching import match_product
from jobscan.products.product_rules import DECISION_LINKS_BY_CATEGORY, detect_category


BUCKET_CATEGORY_HINTS = {
    "coating": "roof_coating",
    "roofing_coating_system": "roof_coating",
    "primer": "primer",
    "thermal_barrier_coating": "thermal_barrier",
    "foam": "spray_foam",
    "caulk_detail": "sealant",
    "caulk_sealant": "sealant",
    "seam_treatment": "sealant",
    "fabric": "fabric",
    "fasteners": "fastener",
    "fastener_treatment": "fastener",
    "granules": "granules",
    "thinner": "thinner",
}

BUCKET_DECISION_HINTS = {
    "coating": "roofing_coating_system",
    "primer": "roofing_primer",
    "thermal_barrier_coating": "insulation_thermal_barrier",
    "foam": "insulation_foam_system",
    "caulk_detail": "roofing_caulk_detail",
    "caulk_sealant": "insulation_caulk_sealant",
    "seam_treatment": "roofing_seam_treatment",
    "fabric": "roofing_fabric",
    "fasteners": "roofing_fastener_treatment",
    "fastener_treatment": "roofing_fastener_treatment",
    "granules": "roofing_granules",
    "thinner": "insulation_thinner",
}

ALIAS_COLUMNS = [
    "alias_id",
    "product_id",
    "alias",
    "alias_type",
    "confidence",
    "review_status",
    "reason",
    "frequency",
]

TEMPLATE_LINK_COLUMNS = [
    "link_id",
    "template_product_option_id",
    "product_id",
    "template_type",
    "template_bucket",
    "row_number",
    "selector_code",
    "product_name",
    "confidence",
    "review_status",
    "reason",
]

GENERIC_ALIAS_NAMES = {
    "acrylic",
    "base",
    "coating",
    "foam",
    "material",
    "mastic",
    "primer",
    "sealant",
    "silicone",
    "thinner",
}


def _records(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, pd.DataFrame):
        return value.to_dict(orient="records")
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    return []


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(_clean(part).lower() for part in parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    label = slugify(parts[-1] if parts else prefix, prefix)[:48]
    return f"{prefix}_{label}_{digest}"


def _template_type_for_row(row: dict[str, Any]) -> str:
    return _clean(row.get("template_type") or row.get("division")).lower()


def _bucket_for_row(row: dict[str, Any]) -> str:
    return _clean(row.get("template_bucket") or row.get("package") or row.get("package_key")).lower()


def _category_for_name_bucket(product_name: str, bucket: str) -> str:
    bucket_category = BUCKET_CATEGORY_HINTS.get(bucket)
    detected_category, _subcategory = detect_category("", product_name)
    if bucket_category:
        return bucket_category
    return detected_category if detected_category != "unknown" else ""


def _decision_id_for(template_type: str, bucket: str, category: str) -> str:
    if bucket in BUCKET_DECISION_HINTS:
        decision = BUCKET_DECISION_HINTS[bucket]
        if bucket == "foam" and template_type == "roofing":
            return "roofing_foam"
        if bucket == "primer" and template_type == "insulation":
            return "insulation_primer"
        return decision
    decisions = DECISION_LINKS_BY_CATEGORY.get(category) or []
    if decisions:
        if template_type:
            scoped = [decision for decision in decisions if decision.startswith(template_type)]
            if scoped:
                return scoped[0]
        return decisions[0]
    return ""


def _product_name_from_row(row: dict[str, Any], fields: tuple[str, ...]) -> str:
    for field in fields:
        value = _clean(row.get(field))
        if value:
            return value
    return ""


def _template_product_option_rows(data: EstimatorData) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in _records(data.template_product_options):
        product_name = _product_name_from_row(row, ("product_name", "item_name", "selected_item_name", "resolved_item_name"))
        if not product_name:
            continue
        rows.append(
            {
                "source_type": "template_product_options",
                "source_id": row.get("template_product_option_id"),
                "template_product_option_id": row.get("template_product_option_id"),
                "template_type": _template_type_for_row(row),
                "template_bucket": _bucket_for_row(row),
                "row_number": row.get("row_number"),
                "selector_code": row.get("selector_code"),
                "product_name": product_name,
                "frequency": 1,
            }
        )
    return rows


def _template_selector_rows(data: EstimatorData) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in _records(data.template_selector_maps):
        product_name = _product_name_from_row(row, ("resolved_item_name", "resolved_template_option", "product_name", "selected_item_name"))
        if not product_name:
            continue
        rows.append(
            {
                "source_type": "template_selector_maps",
                "source_id": row.get("template_selector_map_id"),
                "template_type": _template_type_for_row(row),
                "template_bucket": _bucket_for_row(row),
                "row_number": row.get("row_number"),
                "selector_code": row.get("selector_code") or row.get("lookup_key"),
                "product_name": product_name,
                "frequency": 1,
            }
        )
    return rows


def _historical_template_rows(data: EstimatorData) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for row in _records(data.template_rows):
        if _clean(row.get("line_item_kind")).lower() not in {"material", "equipment", "accessory", ""}:
            continue
        product_name = _product_name_from_row(row, ("selected_item_name", "resolved_item_name", "row_label"))
        if not product_name:
            continue
        template_type = _template_type_for_row(row)
        bucket = _bucket_for_row(row)
        key = (template_type, bucket, str(row.get("row_number") or ""), "", normalize_product_name(product_name))
        entry = grouped.setdefault(
            key,
            {
                "source_type": "estimate_template_rows",
                "source_id": "",
                "template_type": template_type,
                "template_bucket": bucket,
                "row_number": row.get("row_number"),
                "selector_code": "",
                "product_name": product_name,
                "frequency": 0,
            },
        )
        entry["frequency"] += 1
    return list(grouped.values())


def _pricing_rows(data: EstimatorData) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in _records(data.pricing_catalog if not data.pricing_catalog.empty else data.pricing):
        product_name = _product_name_from_row(row, ("product_name", "item_name", "description"))
        if not product_name:
            continue
        rows.append(
            {
                "source_type": "pricing_catalog",
                "source_id": row.get("pricing_item_id"),
                "template_type": "",
                "template_bucket": _clean(row.get("category")).lower(),
                "row_number": "",
                "selector_code": "",
                "product_name": product_name,
                "frequency": 1,
            }
        )
    return rows


def collect_product_mapping_audit(data: EstimatorData, *, min_score: float = 0.55) -> pd.DataFrame:
    source_rows = [
        *_template_product_option_rows(data),
        *_template_selector_rows(data),
        *_historical_template_rows(data),
        *_pricing_rows(data),
    ]
    audit_rows: list[dict[str, Any]] = []
    for row in source_rows:
        product_name = row["product_name"]
        bucket = row.get("template_bucket") or ""
        template_type = row.get("template_type") or ""
        category = _category_for_name_bucket(product_name, bucket)
        decision_id = _decision_id_for(template_type, bucket, category)
        matched = match_product(
            product_name,
            data.product_catalog,
            category=category or None,
            decision_id=decision_id or None,
            product_decision_links=data.product_decision_links,
            product_aliases=data.product_aliases,
            template_product_links=data.template_product_option_links,
            template_product_option_id=row.get("template_product_option_id"),
            min_score=min_score,
        )
        audit_rows.append(
            {
                **row,
                "normalized_product_name": normalize_product_name(product_name),
                "category_hint": category,
                "decision_id_hint": decision_id,
                "matched_product_id": matched.get("product_id", ""),
                "matched_product_name": matched.get("product_name", ""),
                "matched_manufacturer": matched.get("manufacturer", ""),
                "match_score": matched.get("match_score", 0.0),
                "match_strategy": matched.get("match_strategy", ""),
                "matched_name": matched.get("matched_name", ""),
                "mapping_status": "matched" if matched else "unmapped",
            }
        )
    frame = pd.DataFrame(audit_rows)
    if frame.empty:
        return frame
    frame["frequency"] = pd.to_numeric(frame["frequency"], errors="coerce").fillna(1).astype(int)
    frame.sort_values(
        by=["mapping_status", "frequency", "source_type", "product_name"],
        ascending=[True, False, True, True],
        inplace=True,
    )
    return frame


def _existing_alias_keys(data: EstimatorData) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for row in _records(data.product_aliases):
        product_id = _clean(row.get("product_id"))
        alias = normalize_product_name(row.get("alias"))
        if product_id and alias:
            keys.add((product_id, alias))
    for row in _records(data.product_catalog):
        product_id = _clean(row.get("product_id"))
        for value in (row.get("product_name"), row.get("sku"), row.get("product_family")):
            alias = normalize_product_name(value)
            if product_id and alias:
                keys.add((product_id, alias))
    return keys


def proposed_product_aliases(data: EstimatorData, audit: pd.DataFrame) -> pd.DataFrame:
    if audit.empty:
        return pd.DataFrame(columns=ALIAS_COLUMNS)
    existing = _existing_alias_keys(data)
    rows: list[dict[str, Any]] = []
    matched = audit[audit["mapping_status"].eq("matched")].copy()
    matched.sort_values(by=["frequency", "product_name"], ascending=[False, True], inplace=True)
    for row in matched.to_dict(orient="records"):
        product_id = _clean(row.get("matched_product_id"))
        alias = _clean(row.get("product_name"))
        normalized_alias = normalize_product_name(alias)
        alias_tokens = normalized_alias.split()
        if not product_id or not normalized_alias or (product_id, normalized_alias) in existing:
            continue
        if normalized_alias in GENERIC_ALIAS_NAMES or normalized_alias.replace(" ", "").isdigit() or len(alias_tokens) < 2:
            continue
        existing.add((product_id, normalized_alias))
        score = float(row.get("match_score") or 0)
        strategy = str(row.get("match_strategy") or "")
        rows.append(
            {
                "alias_id": _stable_id("alias", product_id, alias),
                "product_id": product_id,
                "alias": alias,
                "alias_type": row.get("source_type"),
                "confidence": round(min(max(score, 0.0), 1.0), 4),
                "review_status": "approved_candidate" if score >= 0.85 and strategy in {"exact_product_or_alias", "template_product_option_link"} else "needs_review",
                "reason": f"{row.get('source_type')} matched {row.get('matched_product_name')} via {strategy}",
                "frequency": row.get("frequency"),
            }
        )
    return pd.DataFrame(rows, columns=ALIAS_COLUMNS)


def proposed_template_product_links(audit: pd.DataFrame) -> pd.DataFrame:
    if audit.empty:
        return pd.DataFrame(columns=TEMPLATE_LINK_COLUMNS)
    if "template_product_option_id" not in audit.columns:
        return pd.DataFrame(columns=TEMPLATE_LINK_COLUMNS)
    rows: list[dict[str, Any]] = []
    source = audit[
        audit["mapping_status"].eq("matched")
        & audit["source_type"].eq("template_product_options")
        & audit["template_product_option_id"].fillna("").astype(str).ne("")
    ].copy()
    source.sort_values(by=["match_score", "product_name"], ascending=[False, True], inplace=True)
    seen: set[tuple[str, str]] = set()
    for row in source.to_dict(orient="records"):
        product_id = _clean(row.get("matched_product_id"))
        option_id = _clean(row.get("template_product_option_id"))
        if not product_id or not option_id or (option_id, product_id) in seen:
            continue
        seen.add((option_id, product_id))
        score = float(row.get("match_score") or 0)
        rows.append(
            {
                "link_id": _stable_id("tplink", option_id, product_id),
                "template_product_option_id": option_id,
                "product_id": product_id,
                "template_type": row.get("template_type"),
                "template_bucket": row.get("template_bucket"),
                "row_number": row.get("row_number"),
                "selector_code": row.get("selector_code"),
                "product_name": row.get("product_name"),
                "confidence": round(min(max(score, 0.0), 1.0), 4),
                "review_status": "approved_candidate" if score >= 0.85 else "needs_review",
                "reason": f"Template option matched product knowledge via {row.get('match_strategy')}",
            }
        )
    return pd.DataFrame(rows, columns=TEMPLATE_LINK_COLUMNS)


def write_product_mapping_audit(data: EstimatorData, out_dir: str | Path, *, min_score: float = 0.55) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    audit = collect_product_mapping_audit(data, min_score=min_score)
    aliases = proposed_product_aliases(data, audit)
    links = proposed_template_product_links(audit)
    paths = {
        "audit": out / "product_mapping_audit.csv",
        "aliases": out / "product_alias_candidates.csv",
        "template_links": out / "template_product_link_candidates.csv",
    }
    audit.to_csv(paths["audit"], index=False)
    aliases.to_csv(paths["aliases"], index=False)
    links.to_csv(paths["template_links"], index=False)
    return paths
