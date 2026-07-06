from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pandas as pd


PRODUCT_BUCKETS = {
    "board_stock",
    "caulk_sealant",
    "coating",
    "curbs",
    "drum_disposal",
    "edge_metal",
    "fabric",
    "fasteners",
    "foam",
    "granules",
    "liquid_flashing",
    "membrane",
    "misc_materials",
    "pitch_pockets",
    "plates",
    "primer",
    "scuppers",
    "seams_misc",
    "thermal_barrier_coating",
    "thinner",
}

NON_PRODUCT_BUCKETS = {
    "delivery_fee",
    "downspouts",
    "dumpsters",
    "freight",
    "generator",
    "hvac_units",
    "ladders",
    "lift",
    "misc",
    "misc_equipment",
    "roof_hatch",
    "sales_inspection_trips",
    "space_heater",
    "truck_expense",
}

BUCKET_CATEGORY_HINTS = {
    "board_stock": "roof_board",
    "caulk_sealant": "sealant",
    "coating": "roof_coating",
    "curbs": "flashing_detail",
    "drum_disposal": "disposal",
    "edge_metal": "metal_accessory",
    "fabric": "reinforcement",
    "fasteners": "fasteners",
    "foam": "spray_foam",
    "granules": "granules",
    "liquid_flashing": "sealant",
    "membrane": "membrane",
    "misc_materials": "misc_material",
    "pitch_pockets": "flashing_detail",
    "plates": "fasteners",
    "primer": "primer",
    "scuppers": "drainage",
    "seams_misc": "seam_treatment",
    "thermal_barrier_coating": "thermal_barrier",
    "thinner": "solvent",
}

TOKEN_STOPWORDS = {
    "and",
    "case",
    "color",
    "colors",
    "custom",
    "for",
    "gal",
    "gallon",
    "gallons",
    "gray",
    "grey",
    "high",
    "lb",
    "light",
    "low",
    "medium",
    "of",
    "pail",
    "roof",
    "roofing",
    "series",
    "solids",
    "standard",
    "the",
    "white",
}

GENERIC_OR_NON_PRODUCT_NAMES = {
    "cost",
    "costs",
    "extra",
    "included",
    "material",
    "materials",
    "misc",
    "misc material",
    "misc materials",
    "misc.",
    "misc. materials",
    "n/a",
    "na",
    "none",
    "price",
    "prices",
    "product",
    "products",
    "type",
    "types",
    "types:",
}

NON_PRODUCT_NAME_PATTERNS = (
    r"^\$?\d+(?:\.\d+)?\s*(?:bags?|rolls?|sheets?|cases?|pails?|drums?)\b",
    r"\b\d+\s*(?:bags?|rolls?|sheets?|cases?|pails?|drums?)\s*@\b",
    r"\b@\s*\$?\d+",
    r"\bper\s+(?:bag|roll|sheet|case|pail|drum)\b",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export LLM-friendly template/pricing/product mapping input lists.")
    parser.add_argument("--out-dir", default="output/llm_product_mapping_inputs")
    parser.add_argument("--template-options", default="output/template_catalog_backfill/template_product_options.csv")
    parser.add_argument("--historical-candidates", default="output/template_catalog_qa/historical_product_candidates.csv")
    parser.add_argument("--pricing-current", default="output/pricing/pricing_catalog_current_cleaned.csv")
    parser.add_argument("--pricing-source", default="output/pricing/pricing_source_items.csv")
    parser.add_argument("--product-catalog", default="output/product_catalog.json")
    parser.add_argument("--family-lookup", default="output/product_family_lookup_normalized.csv")
    parser.add_argument("--max-pricing-candidates", type=int, default=12)
    parser.add_argument("--max-product-candidates", type=int, default=12)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    template_rows = build_template_options(Path(args.template_options), Path(args.historical_candidates))
    pricing_rows = build_pricing_candidates(Path(args.pricing_current), Path(args.pricing_source))
    knowledge_rows = build_product_knowledge(Path(args.product_catalog), Path(args.family_lookup))
    tasks = build_mapping_tasks(
        template_rows,
        pricing_rows,
        knowledge_rows,
        max_pricing=args.max_pricing_candidates,
        max_products=args.max_product_candidates,
    )

    write_csv(out_dir / "template_options_for_llm.csv", template_rows)
    write_jsonl(out_dir / "template_options_for_llm.jsonl", template_rows)
    write_csv(out_dir / "template_options_product_mappable_for_llm.csv", [row for row in template_rows if row["mappable_to_product"]])
    write_csv(out_dir / "pricing_candidates_for_llm.csv", pricing_rows)
    write_jsonl(out_dir / "pricing_candidates_for_llm.jsonl", pricing_rows)
    write_csv(out_dir / "product_knowledge_for_llm.csv", knowledge_rows)
    write_jsonl(out_dir / "product_knowledge_for_llm.jsonl", knowledge_rows)
    write_jsonl(out_dir / "mapping_tasks_for_llm.jsonl", tasks)
    write_prompt(out_dir / "mapping_prompt.md")
    summary = {
        "template_options": len(template_rows),
        "template_options_product_mappable": sum(1 for row in template_rows if row["mappable_to_product"]),
        "pricing_candidates": len(pricing_rows),
        "product_knowledge_records": len(knowledge_rows),
        "mapping_tasks": len(tasks),
        "outputs": sorted(path.name for path in out_dir.iterdir() if path.is_file()),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))


def build_template_options(template_path: Path, historical_path: Path) -> list[dict[str, Any]]:
    templates = pd.read_csv(template_path).fillna("")
    historical = pd.read_csv(historical_path).fillna("")
    hist_index: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for _, row in historical.iterrows():
        key = (
            clean(row.get("template_type")),
            clean(row.get("template_bucket")),
            clean(row.get("row_number")),
            normalize_name(row.get("selected_item_name")),
        )
        hist_index[key] = row.to_dict()

    grouped: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    source_ids: dict[tuple[str, str, str, str, str], list[str]] = defaultdict(list)
    template_names: dict[tuple[str, str, str, str, str], Counter[str]] = defaultdict(Counter)
    source_types: dict[tuple[str, str, str, str, str], Counter[str]] = defaultdict(Counter)

    for _, row in templates.iterrows():
        product_name = clean(row.get("product_name"))
        template_type = clean(row.get("template_type"))
        bucket = clean(row.get("template_bucket"))
        row_number = clean(row.get("row_number"))
        selector_code = clean(row.get("selector_code"))
        normalized = normalize_name(product_name)
        if not product_name:
            continue
        key = (template_type, bucket, row_number, selector_code, normalized)
        hist = hist_index.get((template_type, bucket, row_number, normalized), {})
        source_values = parse_json(row.get("source_values_json"))
        source_type = clean(row.get("source_type"))
        option = grouped.setdefault(
            key,
            {
                "template_option_key": stable_key("tpl", template_type, bucket, row_number, selector_code, normalized),
                "template_type": template_type,
                "template_bucket": bucket,
                "row_number": row_number,
                "selector_code": selector_code,
                "raw_template_option": product_name,
                "normalized_template_option": normalized,
                "probable_vendor": probable_vendor(product_name),
                "category_hint": BUCKET_CATEGORY_HINTS.get(bucket, bucket),
                "mappable_to_product": is_mappable_template_option(bucket, product_name),
                "non_product_reason": non_product_reason(bucket, product_name),
                "name_quality_flags": ";".join(name_quality_flags(product_name)),
                "source_type_summary": "",
                "template_name_examples": "",
                "source_option_ids": "",
                "historical_job_count": int_number(hist.get("job_count")),
                "historical_row_count": int_number(hist.get("row_count")),
                "historical_median_unit_price": number_or_blank(hist.get("median_unit_price")),
                "historical_unit": clean(hist.get("unit")),
                "line_item_kind": clean(hist.get("line_item_kind") or source_values.get("line_item_kind")),
                "formula_context": clean(source_values.get("formula"))[:500],
            },
        )
        option["historical_job_count"] = max(int(option["historical_job_count"] or 0), int_number(hist.get("job_count")) or 0)
        option["historical_row_count"] = max(int(option["historical_row_count"] or 0), int_number(hist.get("row_count")) or 0)
        if option["historical_median_unit_price"] == "" and number_or_blank(hist.get("median_unit_price")) != "":
            option["historical_median_unit_price"] = number_or_blank(hist.get("median_unit_price"))
        source_ids[key].append(clean(row.get("template_product_option_id")))
        template_names[key][clean(row.get("template_name"))] += 1
        source_types[key][source_type] += 1

    rows = []
    for key, row in grouped.items():
        row = dict(row)
        row["source_option_ids"] = ";".join(sorted(value for value in set(source_ids[key]) if value))
        row["template_name_examples"] = ";".join(name for name, _ in template_names[key].most_common(4) if name)
        row["source_type_summary"] = ";".join(f"{name}:{count}" for name, count in source_types[key].most_common())
        rows.append(row)
    return sorted(rows, key=lambda r: (not r["mappable_to_product"], r["template_type"], r["template_bucket"], str(r["raw_template_option"])))


def build_pricing_candidates(current_path: Path, source_path: Path) -> list[dict[str, Any]]:
    rows_by_key: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for source_label, path in (("current_cleaned", current_path), ("source_item", source_path)):
        if not path.exists():
            continue
        df = pd.read_csv(path).fillna("")
        for _, item in df.iterrows():
            name = clean(item.get("product_name"))
            if not name:
                continue
            vendor = clean(item.get("vendor"))
            category = clean(item.get("category"))
            unit = clean(item.get("unit_of_measure"))
            key = (normalize_name(vendor), normalize_name(category), normalize_name(name), normalize_name(unit))
            existing = rows_by_key.get(key, {})
            rows_by_key[key] = {
                "pricing_candidate_key": existing.get("pricing_candidate_key") or stable_key("price", vendor, category, name, unit),
                "vendor": vendor,
                "category": category,
                "raw_pricing_name": name,
                "normalized_pricing_name": normalize_name(name),
                "probable_vendor": probable_vendor(" ".join([vendor, category, name])),
                "unit_price": first_nonblank(existing.get("unit_price"), number_or_blank(item.get("unit_price"))),
                "unit_of_measure": unit,
                "package_size": first_nonblank(existing.get("package_size"), clean(item.get("package_size"))),
                "price_basis": first_nonblank(existing.get("price_basis"), clean(item.get("price_basis"))),
                "price_per_gallon": first_nonblank(existing.get("price_per_gallon"), number_or_blank(item.get("price_per_gallon"))),
                "price_per_sqft": first_nonblank(existing.get("price_per_sqft"), number_or_blank(item.get("price_per_sqft"))),
                "price_per_unit": first_nonblank(existing.get("price_per_unit"), number_or_blank(item.get("price_per_unit"))),
                "effective_date": first_nonblank(existing.get("effective_date"), clean(item.get("effective_date"))),
                "source_file": first_nonblank(existing.get("source_file"), clean(item.get("source_file"))),
                "source_records": ";".join(sorted(set(filter(None, [existing.get("source_records"), source_label])))),
                "needs_review": bool(item.get("needs_review")) if clean(item.get("needs_review")) else existing.get("needs_review", False),
            }
    return sorted(rows_by_key.values(), key=lambda r: (r["vendor"], r["category"], r["raw_pricing_name"]))


def build_product_knowledge(catalog_path: Path, family_lookup_path: Path) -> list[dict[str, Any]]:
    catalog = json.loads(catalog_path.read_text(encoding="utf-8")) if catalog_path.exists() else {}
    products = {clean(row.get("product_id")): row for row in catalog.get("product_catalog") or []}
    aliases_by_product: dict[str, list[str]] = defaultdict(list)
    docs_by_product: dict[str, list[dict[str, Any]]] = defaultdict(list)
    props_by_product: dict[str, list[str]] = defaultdict(list)
    rules_by_product: dict[str, list[str]] = defaultdict(list)
    decision_links_by_product: dict[str, list[str]] = defaultdict(list)

    for row in catalog.get("product_aliases") or []:
        aliases_by_product[clean(row.get("product_id"))].append(clean(row.get("alias")))
    for row in catalog.get("product_documents") or []:
        docs_by_product[clean(row.get("product_id"))].append(row)
    for row in catalog.get("product_properties") or []:
        props_by_product[clean(row.get("product_id"))].append(f"{clean(row.get('property_name'))}: {clean(row.get('source_text') or row.get('property_value'))}")
    for row in catalog.get("product_rules") or []:
        rules_by_product[clean(row.get("product_id"))].append(f"{clean(row.get('rule_type'))}: {clean(row.get('source_text') or row.get('rule_value'))}")
    for row in catalog.get("product_decision_links") or []:
        decision_links_by_product[clean(row.get("product_id"))].append(clean(row.get("decision_id")))

    rows: list[dict[str, Any]] = []
    for product_id, product in products.items():
        aliases = sorted(set(value for value in aliases_by_product.get(product_id, []) if value))
        docs = docs_by_product.get(product_id, [])
        rows.append(
            {
                "knowledge_key": product_id,
                "record_type": "product_catalog",
                "manufacturer": clean(product.get("manufacturer")),
                "raw_product_name": clean(product.get("product_name")),
                "normalized_product_name": normalize_name(product.get("product_name")),
                "product_family": clean(product.get("product_family")),
                "category": clean(product.get("category")),
                "subcategory": clean(product.get("subcategory")),
                "unit": clean(product.get("unit")),
                "aliases": ";".join(aliases[:12]),
                "decision_links": ";".join(sorted(set(decision_links_by_product.get(product_id, [])))),
                "document_types": ";".join(sorted(set(clean(doc.get("document_type")) for doc in docs if clean(doc.get("document_type"))))),
                "source_documents": ";".join(clean(doc.get("source_path")) for doc in docs[:5] if clean(doc.get("source_path"))),
                "extraction_methods": ";".join(sorted(set(clean(doc.get("extraction_method")) for doc in docs if clean(doc.get("extraction_method"))))),
                "properties_summary": " | ".join(props_by_product.get(product_id, [])[:8]),
                "rules_summary": " | ".join(rules_by_product.get(product_id, [])[:8]),
            }
        )

    if family_lookup_path.exists():
        lookup = pd.read_csv(family_lookup_path).fillna("")
        for _, row in lookup.iterrows():
            if clean(row.get("active")).lower() in {"false", "0", "no"}:
                continue
            rows.append(
                {
                    "knowledge_key": clean(row.get("lookup_id")) or stable_key("family", row.get("vendor"), row.get("canonical_product_family")),
                    "record_type": "product_family_lookup",
                    "manufacturer": clean(row.get("vendor")),
                    "raw_product_name": clean(row.get("canonical_product_family")),
                    "normalized_product_name": normalize_name(row.get("canonical_product_family")),
                    "product_family": clean(row.get("canonical_product_family")),
                    "category": clean(row.get("product_type")),
                    "subcategory": clean(row.get("application_hint")),
                    "unit": "",
                    "aliases": ";".join(value for value in [clean(row.get("template_option")), clean(row.get("lookup_terms"))] if value),
                    "decision_links": clean(row.get("decision_nodes")),
                    "document_types": clean(row.get("preferred_documents")),
                    "source_documents": first_nonblank(clean(row.get("vendor_product_url")), clean(row.get("official_vendor_url"))),
                    "extraction_methods": "manual_seed",
                    "properties_summary": "; ".join(value for value in [clean(row.get("cell_type")), clean(row.get("density_class"))] if value),
                    "rules_summary": clean(row.get("notes")),
                }
            )
    return sorted(rows, key=lambda r: (r["manufacturer"], r["raw_product_name"], r["record_type"]))


def build_mapping_tasks(
    template_rows: list[dict[str, Any]],
    pricing_rows: list[dict[str, Any]],
    knowledge_rows: list[dict[str, Any]],
    *,
    max_pricing: int,
    max_products: int,
) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for row in template_rows:
        if not row["mappable_to_product"]:
            continue
        pricing_candidates = rank_candidates(row, pricing_rows, "normalized_pricing_name", max_pricing)
        product_candidates = rank_candidates(row, knowledge_rows, "normalized_product_name", max_products)
        tasks.append(
            {
                "template_option": row,
                "pricing_candidates": pricing_candidates,
                "product_knowledge_candidates": product_candidates,
                "llm_task": (
                    "Choose the best pricing candidate and product knowledge record for the template option. "
                    "Use null when no real match exists. Return confidence, reason, and alias/canonical-name suggestions."
                ),
                "expected_output_schema": {
                    "template_option_key": row["template_option_key"],
                    "pricing_candidate_key": "string|null",
                    "knowledge_key": "string|null",
                    "canonical_template_option": "string",
                    "mapping_status": "approved|needs_review|no_match|not_a_product",
                    "confidence": "0.0-1.0",
                    "reason": "short explanation",
                    "suggested_aliases": ["list of names that should resolve to the same product"],
                },
            }
        )
    return tasks


def rank_candidates(template_row: dict[str, Any], candidates: list[dict[str, Any]], name_field: str, limit: int) -> list[dict[str, Any]]:
    template_tokens = meaningful_tokens(template_row["normalized_template_option"])
    vendor = normalize_name(template_row.get("probable_vendor"))
    bucket = normalize_name(template_row.get("category_hint"))
    scored: list[tuple[float, dict[str, Any]]] = []
    for candidate in candidates:
        candidate_text = " ".join(str(candidate.get(field) or "") for field in candidate.keys())
        candidate_tokens = meaningful_tokens(candidate.get(name_field))
        if not candidate_tokens:
            continue
        overlap = len(template_tokens & candidate_tokens)
        union = len(template_tokens | candidate_tokens) or 1
        score = overlap / union
        candidate_vendor = normalize_name(candidate.get("probable_vendor") or candidate.get("vendor") or candidate.get("manufacturer"))
        candidate_category = normalize_name(candidate.get("category") or candidate.get("subcategory"))
        if vendor and candidate_vendor and vendor == candidate_vendor:
            score += 0.35
        elif vendor and vendor in normalize_name(candidate_text):
            score += 0.2
        if bucket and any(token in candidate_category for token in bucket.split()):
            score += 0.15
        if any(token in normalize_name(candidate_text) for token in template_tokens):
            score += 0.05
        if score > 0:
            compact = dict(candidate)
            compact["candidate_rank_score"] = round(score, 4)
            scored.append((score, compact))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [item for _, item in scored[:limit]]


def is_mappable_template_option(bucket: str, name: str) -> bool:
    if bucket in NON_PRODUCT_BUCKETS:
        return False
    if name_quality_flags(name):
        return False
    if bucket in PRODUCT_BUCKETS:
        return True
    return bool(meaningful_tokens(name))


def non_product_reason(bucket: str, name: str) -> str:
    if bucket in NON_PRODUCT_BUCKETS:
        return f"Bucket {bucket} is equipment/logistics/travel/scaffolding, not product knowledge."
    flags = name_quality_flags(name)
    if flags:
        return "Name quality flags indicate this is not a usable product name: " + ";".join(flags)
    return ""


def name_quality_flags(name: Any) -> list[str]:
    text = normalize_name(name)
    raw = clean(name)
    flags: list[str] = []
    if not text:
        flags.append("blank_name")
    if text.isdigit():
        flags.append("numeric_selector_or_count")
    if text in GENERIC_OR_NON_PRODUCT_NAMES:
        flags.append("generic_placeholder")
    if len(meaningful_tokens(text)) == 0:
        flags.append("no_meaningful_tokens")
    if any(re.search(pattern, raw, flags=re.I) for pattern in NON_PRODUCT_NAME_PATTERNS):
        flags.append("quantity_or_price_note")
    return list(dict.fromkeys(flags))


def probable_vendor(value: Any) -> str:
    text = normalize_name(value)
    vendors = [
        ("Gaco", ("gaco", "gacoflex", "gacorooffoam", "gacoprime")),
        ("GAF", ("gaf", "hydrostop", "unisil")),
        ("AccuFoam", ("accufoam", "af1", "af2")),
        ("BASF", ("basf", "walltite", "enertite")),
        ("NCFI", ("ncfi", "insulbloc", "optimaxx")),
        ("Demilec", ("demilec", "heatlok")),
        ("3M", ("3m", "lr9300")),
        ("NoBurn", ("noburn", "no burn")),
        ("International Fireproof Technology", ("dc315", "dc 315", "international fireproof")),
        ("Aldo", ("aldo",)),
        ("Sherwin-Williams", ("sherwin", "sw ", "uniflex")),
        ("Tremco", ("tremco",)),
        ("Carlisle", ("carlisle",)),
        ("GenFlex", ("genflex",)),
    ]
    for vendor, terms in vendors:
        if any(term in text for term in terms):
            return vendor
    return ""


def meaningful_tokens(value: Any) -> set[str]:
    tokens = set(re.findall(r"[a-z0-9]+", normalize_name(value)))
    return {token for token in tokens if len(token) > 1 and token not in TOKEN_STOPWORDS}


def normalize_name(value: Any) -> str:
    text = str(value or "").lower()
    text = text.replace("™", "").replace("®", "")
    text = re.sub(r"[_/\\-]+", " ", text)
    text = re.sub(r"[^a-z0-9. ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return " ".join(str(value).strip().split())


def parse_json(value: Any) -> dict[str, Any]:
    text = clean(value)
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def stable_key(prefix: str, *parts: Any) -> str:
    import hashlib

    text = "|".join(clean(part) for part in parts)
    return f"{prefix}_{hashlib.sha1(text.encode('utf-8')).hexdigest()[:16]}"


def int_number(value: Any) -> int:
    try:
        if clean(value) == "":
            return 0
        return int(float(value))
    except Exception:
        return 0


def number_or_blank(value: Any) -> str:
    text = clean(value)
    if text == "":
        return ""
    try:
        return str(round(float(text), 6))
    except Exception:
        return text


def first_nonblank(*values: Any) -> str:
    for value in values:
        text = clean(value)
        if text:
            return text
    return ""


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def write_prompt(path: Path) -> None:
    path.write_text(
        """# LLM Product Mapping Prompt

You are mapping Spray-Tec estimating template options to current pricing records and product knowledge records.

Use the provided JSONL mapping tasks. For each task:
- Map only real materials/products to pricing and product knowledge.
- Use `null` when no current pricing candidate or product knowledge record is a defensible match.
- Do not force equipment, travel, dumpsters, truck expense, sales trips, numeric selector counts, or workbook scaffolding into product knowledge.
- Prefer exact manufacturer/product family matches over generic category matches.
- Treat legacy template labels as aliases when appropriate. Example: `Gaco 2.0 lb.` may map to a modern Gaco/Enverge closed-cell wall foam only if the product family evidence supports it.
- Return a JSONL row per task using the task's `expected_output_schema`.

Good reasons mention the template bucket, row number, raw option name, matched candidate name, and whether the match is exact, alias-based, family-level, or no-match.
""",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
