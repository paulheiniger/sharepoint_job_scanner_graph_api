from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .product_catalog import clean_text

DEFAULT_PRODUCT_AI_MODEL = "gpt-4.1-mini"

PRODUCT_DOCUMENT_SCHEMA_FIELDS = {
    "manufacturer": "",
    "product_name": "",
    "product_family": "",
    "sku_or_model": "",
    "category": "",
    "subcategory": "",
    "document_type": "PDS",
    "revision_date": "",
    "recommended_uses": [],
    "approved_substrates": [],
    "prohibited_substrates": [],
    "limitations": [],
    "application_conditions": [],
    "coverage_rates": [],
    "wet_mils": [],
    "dry_times": [],
    "topcoat_windows": [],
    "density": [],
    "r_values": [],
    "pass_thickness": [],
    "service_temperature_range": [],
    "fire_ratings": [],
    "approvals": [],
    "storage_shelf_life": [],
    "cleanup_products": [],
    "safety_flags": [],
    "source_evidence": [],
    "confidence_by_field": {},
}

SECTION_HEADING_RE = re.compile(r"^[A-Z][A-Z\s/&-]{2,}:?$")
PHONE_OR_URL_RE = re.compile(r"(?:www\.|https?://|\.com\b|\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b)", re.I)

CATEGORY_NORMALIZATION = {
    "primer": "primer",
    "roof primer": "primer",
    "low voc primer": "primer",
    "roof_coating": "roof_coating",
    "roof coating": "roof_coating",
    "silicone coating": "roof_coating",
    "spray foam": "spray_foam",
    "roofing foam": "spray_foam",
    "spray foam / roofing foam": "spray_foam",
    "spf": "spray_foam",
    "polyurethane foam": "spray_foam",
    "thermal barrier": "thermal_barrier",
    "ignition barrier": "thermal_barrier",
    "sealant": "sealant",
    "fabric": "fabric",
    "granules": "granules",
    "thinner": "thinner",
}


def empty_product_document_payload() -> dict[str, Any]:
    return {key: (value.copy() if isinstance(value, (dict, list)) else value) for key, value in PRODUCT_DOCUMENT_SCHEMA_FIELDS.items()}


def is_suspicious_product_name(value: Any) -> bool:
    text = clean_text(value)
    if not text:
        return True
    lower = text.lower()
    if PHONE_OR_URL_RE.search(text):
        return True
    if lower in {"gaco.com", "gaf.com", "product data sheet", "safety data sheet", "technical data sheet"}:
        return True
    if len(text.split()) <= 2 and any(token in lower for token in ("phone", "fax", "www", "page")):
        return True
    if re.fullmatch(r"[\d\s().|+-]+", text):
        return True
    return False


def is_bad_source_excerpt(value: Any) -> bool:
    text = clean_text(value)
    if not text:
        return True
    return bool(SECTION_HEADING_RE.fullmatch(text.rstrip(":"))) or text.rstrip(":").lower() in {
        "limitations",
        "application",
        "coverage",
        "technical data",
        "properties",
    }


def normalize_category(value: Any) -> str:
    text = clean_text(value).lower().replace("-", " ")
    return CATEGORY_NORMALIZATION.get(text, text.replace(" ", "_") if text else "")


def normalize_unit(value: Any) -> str:
    text = clean_text(value).lower()
    text = text.replace("²", "2").replace("°", "")
    text = text.replace("square feet", "sqft").replace("sq. ft.", "sqft").replace("sq ft", "sqft")
    text = text.replace("ft2", "sqft")
    text = text.replace("per gallon", "/gal").replace("per gal", "/gal")
    text = text.replace("gallon", "gal")
    text = text.replace("inches", "in").replace("inch", "in")
    text = re.sub(r"\bmil(?:s)?\b", "mils", text)
    text = text.replace("pounds per cubic foot", "pcf")
    text = text.replace("lb/ft3", "pcf").replace("lbs/ft3", "pcf")
    text = text.replace("degrees f", "f").replace("deg f", "f")
    text = re.sub(r"\s+", "", text)
    aliases = {
        "sqft/gal": "sqft/gal",
        "sf/gal": "sqft/gal",
        "ft2/gal": "sqft/gal",
        "mils": "mils",
        "in": "inches",
        "pcf": "pcf",
        "r/in": "R/in",
        "rperin": "R/in",
        "f": "F",
    }
    return aliases.get(text, text)


def _first_number(value: Any) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", clean_text(value))
    return float(match.group(0)) if match else None


def parse_numeric_range(value: Any) -> dict[str, Any]:
    text = clean_text(value).replace("–", "-").replace("—", "-")
    numbers = [float(item) for item in re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", text)]
    if not numbers:
        return {"numeric_value": None, "numeric_min": None, "numeric_max": None}
    if len(numbers) >= 2 and re.search(r"\d+(?:\.\d+)?\s*(?:-|to)\s*\d+(?:\.\d+)?", text, re.I):
        low, high = numbers[0], numbers[1]
        return {"numeric_value": (low + high) / 2, "numeric_min": low, "numeric_max": high}
    return {"numeric_value": numbers[0], "numeric_min": numbers[0], "numeric_max": numbers[0]}


def normalize_extracted_measure(value: Any, default_unit: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        unit = normalize_unit(value.get("unit") or value.get("units") or default_unit)
        low = _first_number(value.get("min") or value.get("minimum") or value.get("low"))
        high = _first_number(value.get("max") or value.get("maximum") or value.get("high"))
        raw_value = clean_text(value.get("value") or value.get("rate") or value.get("range") or value.get("text"))
        if not raw_value and low is not None and high is not None:
            raw_value = f"{low:g}-{high:g} {unit or default_unit}".strip()
        elif not raw_value and low is not None:
            raw_value = f"{low:g} {unit or default_unit}".strip()
        elif not raw_value:
            raw_value = clean_text(value)
        if low is not None and high is not None:
            return {"value": raw_value, "unit": unit, "numeric_value": (low + high) / 2, "numeric_min": low, "numeric_max": high}
        if low is not None:
            return {"value": raw_value, "unit": unit, "numeric_value": low, "numeric_min": low, "numeric_max": low}
    else:
        raw_value = clean_text(value)
        unit_match = re.search(r"(ft²/gal|ft2/gal|sq\.?\s*ft\.?\s*/?\s*(?:per\s*)?gal(?:lon)?|mils?|in(?:ches)?|pcf|R\s*(?:/|per)\s*in(?:ch)?|°?F)", raw_value, re.I)
        unit = normalize_unit(unit_match.group(0) if unit_match else default_unit)
    numeric = parse_numeric_range(raw_value)
    return {"value": raw_value, "unit": unit, **numeric}


def normalize_ai_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    normalized = empty_product_document_payload()
    warnings: list[str] = []
    for key in normalized:
        if key in payload:
            normalized[key] = payload[key]
    normalized["manufacturer"] = clean_text(normalized.get("manufacturer"))
    normalized["product_name"] = clean_text(normalized.get("product_name"))
    normalized["product_family"] = clean_text(normalized.get("product_family"))
    normalized["sku_or_model"] = clean_text(normalized.get("sku_or_model"))
    normalized["category"] = normalize_category(normalized.get("category"))
    normalized["subcategory"] = clean_text(normalized.get("subcategory"))
    normalized["document_type"] = clean_text(normalized.get("document_type")) or "PDS"
    normalized["revision_date"] = clean_text(normalized.get("revision_date"))
    if is_suspicious_product_name(normalized["product_name"]):
        warnings.append(f"Rejected suspicious product name: {normalized['product_name'] or '[blank]'}")
        normalized["product_name"] = ""
    for key, default in PRODUCT_DOCUMENT_SCHEMA_FIELDS.items():
        if isinstance(default, list) and not isinstance(normalized.get(key), list):
            normalized[key] = [normalized[key]] if normalized.get(key) not in (None, "") else []
        if isinstance(default, dict) and not isinstance(normalized.get(key), dict):
            normalized[key] = {}
    cleaned_evidence = []
    for row in normalized.get("source_evidence") or []:
        if not isinstance(row, dict):
            continue
        excerpt = clean_text(row.get("source_text_excerpt"))
        if is_bad_source_excerpt(excerpt):
            warnings.append(f"Rejected weak source evidence for {row.get('field') or 'unknown field'}: {excerpt}")
            continue
        cleaned_evidence.append(
            {
                "field": clean_text(row.get("field")),
                "value": clean_text(row.get("value")),
                "source_page": row.get("source_page"),
                "source_text_excerpt": excerpt,
            }
        )
    normalized["source_evidence"] = cleaned_evidence
    normalized["extraction_warnings"] = warnings
    return normalized, warnings


def evidence_for(payload: dict[str, Any], field: str, value: Any = "") -> dict[str, Any]:
    value_text = clean_text(value).lower()
    field_text = clean_text(field).lower()
    for row in payload.get("source_evidence") or []:
        row_field = clean_text(row.get("field")).lower()
        row_value = clean_text(row.get("value")).lower()
        if row_field == field_text or (field_text and field_text in row_field):
            if not value_text or value_text in row_value or row_value in value_text:
                return row
    return {}


def build_product_document_prompt(text: str, source_pdf: str | Path, document_type: str, manufacturer_hint: str | None = None) -> str:
    return f"""
Extract structured product knowledge from this product document.

Return JSON only. Do not estimate costs or make estimating decisions.
Use facts present in the document only. If uncertain, leave a field blank and add source evidence only for facts you can cite.

Rules:
- Product name must come from the document title/product heading, not website headers, phone numbers, page numbers, or footers.
- Ignore repeated headers such as Gaco.com, phone numbers, page numbers, copyright lines, and generic footer text.
- Preserve source page and short source text excerpts for every extracted fact.
- Do not invent missing facts.
- Normalize categories to broad product categories such as primer, roof_coating, spray_foam, thermal_barrier, sealant, fabric, granules, thinner.

Source PDF: {source_pdf}
Document type: {document_type}
Manufacturer hint: {manufacturer_hint or ""}

Required JSON shape:
{json.dumps(PRODUCT_DOCUMENT_SCHEMA_FIELDS, indent=2)}

Document text:
{text[:60000]}
""".strip()


def parse_product_document_with_ai(
    text: str,
    *,
    source_pdf: str | Path,
    document_type: str,
    manufacturer_hint: str | None = None,
    model: str = DEFAULT_PRODUCT_AI_MODEL,
) -> dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not available.")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Install openai to use AI product document parsing.") from exc
    client = OpenAI(api_key=api_key)
    prompt = build_product_document_prompt(text, source_pdf, document_type, manufacturer_hint)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "You extract product document facts into strict JSON. You never make estimating decisions.",
            },
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    content = response.choices[0].message.content or "{}"
    payload = json.loads(content)
    normalized, warnings = normalize_ai_payload(payload)
    normalized["extraction_method"] = "ai_structured"
    normalized["ai_model"] = model
    normalized["extraction_warnings"] = warnings
    return normalized
