from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from .ai_document_parser import (
    evidence_for,
    is_bad_source_excerpt,
    is_suspicious_product_name,
    normalize_ai_payload,
    normalize_extracted_measure,
    parse_product_document_with_ai,
)
from ..env import load_project_env
from .product_catalog import ProductKnowledge, product_id_for, slugify, write_product_catalog_json
from .product_documents import extract_local_document_text, iter_local_product_documents
from .product_rules import DECISION_LINKS_BY_CATEGORY, detect_category, detect_manufacturer, extract_properties, extract_rules, infer_product_name, parse_revision_date


def _aliases_for(product_name: str, manufacturer: str, sku: str = "") -> list[str]:
    aliases = {product_name}
    if manufacturer:
        aliases.add(f"{manufacturer} {product_name}")
    if sku:
        aliases.add(sku)
        aliases.add(f"{manufacturer} {sku}".strip())
    return sorted(alias for alias in aliases if alias)


def _unit_for_category(category: str) -> str:
    return {
        "roof_coating": "gal",
        "primer": "gal",
        "spray_foam": "set",
        "thermal_barrier": "gal",
        "sealant": "tube",
        "fabric": "roll",
        "granules": "bag",
        "thinner": "gal",
    }.get(category, "")


def _document_id(path: Path, product_id: str) -> str:
    return slugify(f"{product_id}_{path.stem}")


def _link_rows(product_id: str, category: str, source_reason: str) -> list[dict[str, Any]]:
    rows = []
    for decision_id in DECISION_LINKS_BY_CATEGORY.get(category, []):
        rows.append(
            {
                "link_id": slugify(f"{product_id}_{decision_id}"),
                "product_id": product_id,
                "decision_id": decision_id,
                "influence_type": "candidate_product",
                "confidence": 0.75,
                "reason": source_reason,
            }
        )
    return rows


def _fact_text(value: Any) -> str:
    if isinstance(value, dict):
        direct = value.get("value") or value.get("text") or value.get("description") or value.get("substrate") or value.get("condition")
        if direct:
            return str(direct)
        low = value.get("min") or value.get("minimum") or value.get("low")
        high = value.get("max") or value.get("maximum") or value.get("high")
        units = value.get("unit") or value.get("units") or ""
        if low not in (None, "") and high not in (None, ""):
            return f"{low} - {high} {units}".strip()
        if low not in (None, ""):
            return f"{low} {units}".strip()
        if high not in (None, ""):
            return f"{high} {units}".strip()
        return str(value)
    return str(value or "")


def _source_page(payload: dict[str, Any], field: str, value: Any) -> Any:
    if isinstance(value, dict) and value.get("source_page"):
        return value.get("source_page")
    evidence = evidence_for(payload, field, _fact_text(value))
    return evidence.get("source_page")


def _source_text(payload: dict[str, Any], field: str, value: Any) -> str:
    if isinstance(value, dict) and value.get("source_text_excerpt"):
        excerpt = str(value.get("source_text_excerpt") or "")
        if not is_bad_source_excerpt(excerpt):
            return excerpt
    evidence = evidence_for(payload, field, _fact_text(value))
    excerpt = str(evidence.get("source_text_excerpt") or "")
    if excerpt and not is_bad_source_excerpt(excerpt):
        return excerpt
    return _fact_text(value)


def _confidence(payload: dict[str, Any], field: str, default: float = 0.75) -> float:
    confidence = payload.get("confidence_by_field") if isinstance(payload.get("confidence_by_field"), dict) else {}
    value = confidence.get(field)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").lower()
    if text == "high":
        return 0.9
    if text == "medium":
        return 0.7
    if text == "low":
        return 0.4
    return default


def _add_property_from_measure(
    rows: list[dict[str, Any]],
    *,
    product_id: str,
    document_id: str,
    payload: dict[str, Any],
    property_name: str,
    field_name: str,
    value: Any,
    default_unit: str = "",
) -> None:
    measure = normalize_extracted_measure(value, default_unit)
    property_text = str(measure.get("value") or "").lower()
    if property_name == "coverage_sqft_per_gallon":
        measure["unit"] = "sqft/gal"
    elif property_name == "wet_mils":
        measure["unit"] = "mils"
    elif property_name in {"dry_time", "topcoat_window"}:
        measure["unit"] = "hours"
    elif property_name == "R_value":
        measure["unit"] = "R/in"
    elif property_name == "pass_thickness":
        measure["unit"] = "inches"
    elif property_name == "density" and "specific gravity" in property_text:
        measure["unit"] = "specific_gravity"
    rows.append(
        {
            "property_id": slugify(f"{product_id}_{document_id}_{property_name}_{len(rows)}"),
            "product_id": product_id,
            "document_id": document_id,
            "property_name": property_name,
            "property_value": measure["value"],
            "numeric_value": measure.get("numeric_value"),
            "numeric_min": measure.get("numeric_min"),
            "numeric_max": measure.get("numeric_max"),
            "unit": measure.get("unit") or default_unit,
            "source_page": _source_page(payload, field_name, value),
            "source_text": _source_text(payload, field_name, value),
            "confidence": _confidence(payload, field_name),
        }
    )


def _add_rule_from_fact(
    rows: list[dict[str, Any]],
    *,
    product_id: str,
    document_id: str,
    payload: dict[str, Any],
    rule_type: str,
    field_name: str,
    value: Any,
    severity: str = "info",
) -> None:
    text = _fact_text(value).strip()
    if not text:
        return
    rows.append(
        {
            "rule_id": slugify(f"{product_id}_{document_id}_{rule_type}_{len(rows)}"),
            "product_id": product_id,
            "document_id": document_id,
            "rule_type": rule_type,
            "rule_value": text,
            "source_page": _source_page(payload, field_name, value),
            "source_text": _source_text(payload, field_name, value),
            "confidence": _confidence(payload, field_name),
            "severity": severity,
        }
    )


def _knowledge_from_structured_payload(
    *,
    payload: dict[str, Any],
    document: Any,
    fallback_text: str,
    manufacturer_hint: str | None = None,
) -> ProductKnowledge:
    normalized, validation_warnings = normalize_ai_payload(payload)
    text = fallback_text
    manufacturer = normalized.get("manufacturer") or manufacturer_hint or detect_manufacturer(text, document.path)
    product_name = normalized.get("product_name")
    if not product_name:
        inferred = infer_product_name(text, document.path)
        product_name = "" if is_suspicious_product_name(inferred) else inferred
        if product_name:
            validation_warnings.append(f"Product name came from deterministic fallback: {product_name}")
    sku = str(normalized.get("sku_or_model") or "")
    category = str(normalized.get("category") or "")
    subcategory = str(normalized.get("subcategory") or "")
    if not category:
        category, subcategory = detect_category(text, product_name)
    product_id = product_id_for(manufacturer, product_name or document.path.stem, sku)
    document_id = _document_id(document.path, product_id)
    aliases = _aliases_for(product_name or document.path.stem, manufacturer, sku)

    knowledge = ProductKnowledge()
    knowledge.product_catalog.append(
        {
            "product_id": product_id,
            "manufacturer": manufacturer,
            "product_family": normalized.get("product_family") or "",
            "product_name": product_name,
            "sku": sku,
            "category": category,
            "subcategory": subcategory,
            "unit": _unit_for_category(category),
            "aliases": aliases,
            "active": True,
            "extraction_method": normalized.get("extraction_method") or "ai_structured",
            "extraction_warnings": validation_warnings + list(normalized.get("extraction_warnings") or []),
        }
    )
    for alias in aliases:
        knowledge.product_aliases.append(
            {
                "alias_id": slugify(f"{product_id}_{alias}"),
                "product_id": product_id,
                "alias": alias,
                "alias_type": "parsed",
                "confidence": 0.8 if normalized.get("extraction_method") == "ai_structured" else 0.6,
            }
        )
    knowledge.product_documents.append(
        {
            "document_id": document_id,
            "product_id": product_id,
            "document_type": normalized.get("document_type") or document.document_type,
            "source_type": document.source_type,
            "source_path": str(document.path),
            "revision_date": normalized.get("revision_date") or parse_revision_date(text) or None,
            "raw_text_hash": document.text_hash,
            "extraction_method": normalized.get("extraction_method") or "ai_structured",
            "extraction_warnings": validation_warnings + list(normalized.get("extraction_warnings") or []),
        }
    )
    for field, property_name, unit in (
        ("coverage_rates", "coverage_sqft_per_gallon", "sqft/gal"),
        ("wet_mils", "wet_mils", "mils"),
        ("dry_times", "dry_time", "hours"),
        ("topcoat_windows", "topcoat_window", "hours"),
        ("density", "density", "pcf"),
        ("r_values", "R_value", "R/in"),
        ("pass_thickness", "pass_thickness", "inches"),
        ("service_temperature_range", "service_temperature", "F"),
        ("fire_ratings", "fire_rating", ""),
        ("approvals", "approval_standard", ""),
        ("storage_shelf_life", "storage_shelf_life", ""),
        ("cleanup_products", "cleanup_product", ""),
        ("safety_flags", "safety_flag", ""),
    ):
        for value in normalized.get(field) or []:
            _add_property_from_measure(
                knowledge.product_properties,
                product_id=product_id,
                document_id=document_id,
                payload=normalized,
                property_name=property_name,
                field_name=field,
                value=value,
                default_unit=unit,
            )
    for field, rule_type, severity in (
        ("recommended_uses", "recommended_use", "info"),
        ("approved_substrates", "approved_substrate", "info"),
        ("prohibited_substrates", "prohibited_substrate", "warning"),
        ("limitations", "limitation", "warning"),
        ("application_conditions", "application_condition", "info"),
    ):
        for value in normalized.get(field) or []:
            _add_rule_from_fact(
                knowledge.product_rules,
                product_id=product_id,
                document_id=document_id,
                payload=normalized,
                rule_type=rule_type,
                field_name=field,
                value=value,
                severity=severity,
            )
    knowledge.product_decision_links.extend(_link_rows(product_id, category, f"Linked from parsed category {category}."))
    return knowledge


def _regex_fallback_payload(path: str | Path, *, extraction_warnings: list[str] | None = None) -> ProductKnowledge:
    document = extract_local_document_text(path)
    text = document.text
    manufacturer = detect_manufacturer(text, document.path)
    product_name = infer_product_name(text, document.path)
    if is_suspicious_product_name(product_name):
        product_name = document.path.stem
    category, subcategory = detect_category(text, product_name)
    sku = ""
    product_id = product_id_for(manufacturer, product_name, sku)
    document_id = _document_id(document.path, product_id)
    aliases = _aliases_for(product_name, manufacturer, sku)

    knowledge = ProductKnowledge()
    knowledge.product_catalog.append(
        {
            "product_id": product_id,
            "manufacturer": manufacturer,
            "product_family": "",
            "product_name": product_name,
            "sku": sku,
            "category": category,
            "subcategory": subcategory,
            "unit": _unit_for_category(category),
            "aliases": aliases,
            "active": True,
            "extraction_method": "regex_fallback",
            "extraction_warnings": extraction_warnings or [],
        }
    )
    for alias in aliases:
        knowledge.product_aliases.append(
            {
                "alias_id": slugify(f"{product_id}_{alias}"),
                "product_id": product_id,
                "alias": alias,
                "alias_type": "parsed",
                "confidence": 0.75,
            }
        )
    knowledge.product_documents.append(
        {
            "document_id": document_id,
            "product_id": product_id,
            "document_type": document.document_type,
            "source_type": document.source_type,
            "source_path": str(document.path),
            "revision_date": parse_revision_date(text) or None,
            "raw_text_hash": document.text_hash,
            "extraction_method": "regex_fallback",
            "extraction_warnings": extraction_warnings or [],
        }
    )
    knowledge.product_properties.extend(extract_properties(product_id, document_id, text, document.page_texts))
    knowledge.product_rules.extend(extract_rules(product_id, document_id, text, document.page_texts))
    knowledge.product_decision_links.extend(_link_rows(product_id, category, f"Linked from parsed category {category}."))
    return knowledge


def ingest_product_document(
    path: str | Path,
    *,
    use_ai: bool = False,
    model: str = "gpt-4.1-mini",
    manufacturer_hint: str | None = None,
) -> ProductKnowledge:
    if not use_ai:
        return _regex_fallback_payload(path)
    document = extract_local_document_text(path)
    if not os.getenv("OPENAI_API_KEY"):
        return _regex_fallback_payload(path, extraction_warnings=["OPENAI_API_KEY not available; used regex_fallback."])
    try:
        payload = parse_product_document_with_ai(
            document.text,
            source_pdf=document.path,
            document_type=document.document_type,
            manufacturer_hint=manufacturer_hint,
            model=model,
        )
        payload["extraction_method"] = "ai_structured"
        return _knowledge_from_structured_payload(
            payload=payload,
            document=document,
            fallback_text=document.text,
            manufacturer_hint=manufacturer_hint,
        )
    except Exception as exc:
        return _regex_fallback_payload(path, extraction_warnings=[f"AI parser failed; used regex_fallback. {type(exc).__name__}: {exc}"])


def _legacy_ingest_product_document(path: str | Path) -> ProductKnowledge:
    document = extract_local_document_text(path)
    text = document.text
    manufacturer = detect_manufacturer(text, document.path)
    product_name = infer_product_name(text, document.path)
    category, subcategory = detect_category(text, product_name)
    sku = ""
    product_id = product_id_for(manufacturer, product_name, sku)
    document_id = _document_id(document.path, product_id)
    aliases = _aliases_for(product_name, manufacturer, sku)

    knowledge = ProductKnowledge()
    knowledge.product_catalog.append(
        {
            "product_id": product_id,
            "manufacturer": manufacturer,
            "product_family": "",
            "product_name": product_name,
            "sku": sku,
            "category": category,
            "subcategory": subcategory,
            "unit": _unit_for_category(category),
            "aliases": aliases,
            "active": True,
        }
    )
    for alias in aliases:
        knowledge.product_aliases.append(
            {
                "alias_id": slugify(f"{product_id}_{alias}"),
                "product_id": product_id,
                "alias": alias,
                "alias_type": "parsed",
                "confidence": 0.75,
            }
        )
    knowledge.product_documents.append(
        {
            "document_id": document_id,
            "product_id": product_id,
            "document_type": document.document_type,
            "source_type": document.source_type,
            "source_path": str(document.path),
            "revision_date": parse_revision_date(text) or None,
            "raw_text_hash": document.text_hash,
            "extraction_method": "regex_fallback",
            "extraction_warnings": [],
        }
    )
    knowledge.product_properties.extend(extract_properties(product_id, document_id, text, document.page_texts))
    knowledge.product_rules.extend(extract_rules(product_id, document_id, text, document.page_texts))
    knowledge.product_decision_links.extend(_link_rows(product_id, category, f"Linked from parsed category {category}."))
    return knowledge


def ingest_product_directory(
    pdf_dir: str | Path,
    *,
    use_ai: bool = False,
    model: str = "gpt-4.1-mini",
    manufacturer_hint: str | None = None,
) -> ProductKnowledge:
    from .product_catalog import merge_product_knowledge

    documents = list(iter_local_product_documents(pdf_dir))
    return merge_product_knowledge(
        [
            ingest_product_document(path, use_ai=use_ai, model=model, manufacturer_hint=manufacturer_hint)
            for path in documents
        ]
    )


def main(argv: list[str] | None = None) -> int:
    load_project_env()
    parser = argparse.ArgumentParser(description="Ingest local product PDFs/text into generic product knowledge JSON.")
    parser.add_argument("--pdf-dir", required=True, help="Directory containing local PDS/SDS/application PDFs.")
    parser.add_argument("--out", required=True, help="Output product catalog JSON path.")
    parser.add_argument("--use-ai", action="store_true", help="Use AI structured document understanding when OPENAI_API_KEY is available.")
    parser.add_argument("--model", default="gpt-4.1-mini", help="OpenAI model for --use-ai.")
    parser.add_argument("--manufacturer-hint", default="", help="Optional manufacturer hint from folder or filename context.")
    args = parser.parse_args(argv)
    knowledge = ingest_product_directory(
        args.pdf_dir,
        use_ai=args.use_ai,
        model=args.model,
        manufacturer_hint=args.manufacturer_hint or None,
    )
    out = write_product_catalog_json(knowledge, args.out)
    print(
        f"Wrote product catalog: {out} "
        f"({len(knowledge.product_catalog)} products, {len(knowledge.product_properties)} properties, {len(knowledge.product_rules)} rules)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
