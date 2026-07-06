from __future__ import annotations

import json
from difflib import SequenceMatcher
from typing import Any

import pandas as pd

from .product_catalog import normalize_product_name

WORKBENCH_CATEGORY_ALIASES = {
    "coating": {"roof_coating"},
    "roofing_coating_system": {"roof_coating"},
    "primer": {"primer"},
    "foam": {"spray_foam"},
    "insulation_foam_system": {"spray_foam"},
    "thermal_barrier_coating": {"thermal_barrier"},
    "insulation_thermal_barrier": {"thermal_barrier"},
    "caulk_detail": {"sealant"},
    "caulk_sealant": {"sealant"},
    "seam_treatment": {"sealant", "fabric"},
    "fabric": {"fabric"},
    "granules": {"granules"},
    "fastener_treatment": {"fastener", "sealant"},
    "thinner": {"thinner"},
}


def _records(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, pd.DataFrame):
        return value.to_dict(orient="records")
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    return []


def _contains_score(query: str, candidate: str) -> float:
    if not query or not candidate:
        return 0.0
    if query == candidate:
        return 1.0
    if query in candidate or candidate in query:
        return 0.9
    query_parts = set(query.split())
    candidate_parts = set(candidate.split())
    if not query_parts or not candidate_parts:
        return 0.0
    overlap = len(query_parts & candidate_parts) / max(len(query_parts | candidate_parts), 1)
    return max(overlap, SequenceMatcher(None, query, candidate).ratio())


def _aliases(row: dict[str, Any]) -> list[str]:
    aliases = row.get("aliases") or []
    if isinstance(aliases, str):
        try:
            aliases = json.loads(aliases)
        except Exception:
            aliases = [aliases]
    return [str(item) for item in aliases if str(item or "").strip()]


def _alias_names_for_product(product_aliases: Any, product_id: str) -> list[str]:
    if not product_id:
        return []
    names: list[str] = []
    for row in _records(product_aliases):
        if str(row.get("product_id") or "") != product_id:
            continue
        alias = str(row.get("alias") or "").strip()
        if alias:
            names.append(alias)
    return names


def _template_link_product_ids(template_product_links: Any, template_product_option_id: str | None) -> set[str]:
    option_id = str(template_product_option_id or "").strip()
    if not option_id:
        return set()
    product_ids: set[str] = set()
    for row in _records(template_product_links):
        if str(row.get("template_product_option_id") or "") != option_id:
            continue
        if str(row.get("review_status") or "approved").lower() in {"rejected", "inactive"}:
            continue
        product_id = str(row.get("product_id") or "").strip()
        if product_id:
            product_ids.add(product_id)
    return product_ids


def _category_matches(product_category: Any, requested_category: str | None, decision_id: str | None = None) -> bool:
    product = str(product_category or "").lower().strip()
    requested = str(requested_category or "").lower().strip()
    decision = str(decision_id or "").lower().strip()
    if not product or not requested:
        return False
    if product == requested:
        return True
    accepted = set(WORKBENCH_CATEGORY_ALIASES.get(requested, set()))
    accepted.update(WORKBENCH_CATEGORY_ALIASES.get(decision, set()))
    return product in accepted


def _numeric_value(row: dict[str, Any]) -> float | None:
    for key in ("numeric_value", "value_numeric", "property_value"):
        value = row.get(key)
        if value is None:
            continue
        try:
            text = str(value).replace(",", "").strip()
            if not text:
                continue
            return float(text.split()[0])
        except Exception:
            continue
    return None


def _r_value_context(properties: list[dict[str, Any]]) -> dict[str, Any]:
    r_rows = [
        row
        for row in properties
        if str(row.get("property_name") or "").lower() in {"r_value", "r-value", "r value"}
        or "r/in" in str(row.get("unit") or "").lower()
    ]
    if not r_rows:
        return {}
    aged: dict[str, Any] | None = None
    initial: dict[str, Any] | None = None
    fallback: dict[str, Any] | None = None
    for row in r_rows:
        value = _numeric_value(row)
        if value is None or value <= 0:
            continue
        text = " ".join(str(row.get(key) or "") for key in ("property_value", "source_text", "notes")).lower()
        normalized = {**row, "numeric_value": value}
        if "aged" in text:
            aged = normalized
        elif "initial" in text:
            initial = normalized
        elif fallback is None:
            fallback = normalized
    chosen = aged or fallback or initial
    if not chosen:
        return {}
    out: dict[str, Any] = {
        "r_value_per_inch": chosen["numeric_value"],
        "r_value_per_inch_source": chosen.get("source_text") or chosen.get("property_value") or "product property",
    }
    if aged:
        out["aged_r_value_per_inch"] = aged["numeric_value"]
        out["aged_r_value_per_inch_source"] = aged.get("source_text") or aged.get("property_value") or "aged product property"
    if initial:
        out["initial_r_value_per_inch"] = initial["numeric_value"]
        out["initial_r_value_per_inch_source"] = initial.get("source_text") or initial.get("property_value") or "initial product property"
    return out


def match_product(
    product_name: str,
    product_catalog: Any,
    *,
    category: str | None = None,
    decision_id: str | None = None,
    product_decision_links: Any = None,
    product_aliases: Any = None,
    template_product_links: Any = None,
    template_product_option_id: str | None = None,
    min_score: float = 0.55,
) -> dict[str, Any]:
    products = _records(product_catalog)
    if not products or not product_name:
        return {}
    link_product_ids: set[str] = set()
    if decision_id and product_decision_links is not None:
        for link in _records(product_decision_links):
            if str(link.get("decision_id") or "") == decision_id:
                link_product_ids.add(str(link.get("product_id") or ""))
    template_link_product_ids = _template_link_product_ids(template_product_links, template_product_option_id)
    query = normalize_product_name(product_name)
    best: dict[str, Any] = {}
    best_score = 0.0
    best_strategy = ""
    best_matched_name = ""
    for row in products:
        if row.get("active") is False:
            continue
        product_id = str(row.get("product_id") or "")
        candidate_names = [
            row.get("product_name"),
            row.get("sku"),
            row.get("product_family"),
            *_aliases(row),
            *_alias_names_for_product(product_aliases, product_id),
        ]
        scored_names = [
            (_contains_score(query, normalize_product_name(name)), str(name or ""))
            for name in candidate_names
            if str(name or "").strip()
        ]
        score, matched_name = max(scored_names, default=(0.0, ""))
        strategy = "fuzzy_product_name"
        if score >= 1.0:
            strategy = "exact_product_or_alias"
        category_match = _category_matches(row.get("category"), category, decision_id) if category else False
        if category and row.get("category") and not category_match and strategy != "exact_product_or_alias":
            if not (template_link_product_ids and product_id in template_link_product_ids):
                continue
        if category and category_match:
            score += 0.06
        if link_product_ids and str(row.get("product_id") or "") in link_product_ids:
            score += 0.1
        if template_link_product_ids and product_id in template_link_product_ids:
            score = max(score, 0.98)
            strategy = "template_product_option_link"
        if score > best_score:
            best = row
            best_score = score
            best_strategy = strategy
            best_matched_name = matched_name
    if best_score < min_score:
        return {}
    return {
        **best,
        "match_score": round(min(best_score, 1.0), 4),
        "match_strategy": best_strategy,
        "matched_name": best_matched_name,
    }


def product_context_for_decision(
    *,
    product_name: str,
    decision_id: str,
    product_catalog: Any,
    product_properties: Any = None,
    product_rules: Any = None,
    product_documents: Any = None,
    product_decision_links: Any = None,
    product_aliases: Any = None,
    template_product_links: Any = None,
    template_product_option_id: str | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    product = match_product(
        product_name,
        product_catalog,
        category=category,
        decision_id=decision_id,
        product_decision_links=product_decision_links,
        product_aliases=product_aliases,
        template_product_links=template_product_links,
        template_product_option_id=template_product_option_id,
    )
    if not product:
        return {}
    product_id = str(product.get("product_id") or "")
    properties = [row for row in _records(product_properties) if str(row.get("product_id") or "") == product_id]
    rules = [row for row in _records(product_rules) if str(row.get("product_id") or "") == product_id]
    documents = [row for row in _records(product_documents) if str(row.get("product_id") or "") == product_id]
    linked_decisions = [
        row for row in _records(product_decision_links) if str(row.get("product_id") or "") == product_id
    ]
    recommended = [row for row in rules if str(row.get("rule_type") or "") in {"recommended_use", "approved_substrate"}]
    limitations = [
        row
        for row in rules
        if str(row.get("severity") or "") == "warning"
        or str(row.get("rule_type") or "").startswith("prohibited")
        or "limitation" in str(row.get("rule_type") or "")
    ]
    coverage = [
        row
        for row in properties
        if str(row.get("property_name") or "") in {"coverage_sqft_per_gallon", "coverage_gal_per_100_sqft", "wet_mils"}
    ]
    r_value_context = _r_value_context(properties)
    source_evidence = []
    for row in [*rules[:8], *properties[:8]]:
        text = row.get("source_text")
        if not text:
            continue
        source_evidence.append(
            {
                "field": row.get("rule_type") or row.get("property_name"),
                "value": row.get("rule_value") or row.get("property_value"),
                "source_page": row.get("source_page"),
                "source_text": text,
            }
        )
    return {
        "product_id": product_id,
        "manufacturer": product.get("manufacturer") or "",
        "product_family": product.get("product_family") or "",
        "product_name": product.get("product_name") or "",
        "category": product.get("category") or "",
        "match_score": product.get("match_score"),
        "match_strategy": product.get("match_strategy"),
        "matched_name": product.get("matched_name"),
        "recommended_use": "; ".join(str(row.get("rule_value") or "") for row in recommended[:3]),
        "manufacturer_guidance": "; ".join(str(row.get("rule_value") or "") for row in rules[:5]),
        "coverage": "; ".join(
            f"{row.get('property_name')}: {row.get('property_value')} {row.get('unit') or ''}".strip()
            for row in coverage[:5]
        ),
        **r_value_context,
        "important_limitations": "; ".join(str(row.get("rule_value") or "") for row in limitations[:5]),
        "warnings": [row.get("rule_value") for row in limitations[:5] if row.get("rule_value")],
        "source_documents": [row.get("source_path") for row in documents[:5] if row.get("source_path")],
        "source_evidence": source_evidence[:10],
        "linked_decision_nodes": [row.get("decision_id") for row in linked_decisions if row.get("decision_id")],
        "confidence": "high" if float(product.get("match_score") or 0) >= 0.85 else "medium",
    }
