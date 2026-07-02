from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .product_catalog import clean_text, slugify


MANUFACTURERS = [
    "GAF",
    "Gaco",
    "Sherwin-Williams",
    "GE",
    "BASF",
    "NCFI",
    "Demilec",
    "PSI",
    "International Fireproof Technology",
    "No-Burn",
]

CATEGORY_KEYWORDS = [
    ("thermal_barrier", ["dc315", "dc 315", "thermal barrier", "ignition barrier", "intumescent"]),
    ("primer", ["primer", "prime", "rust inhibitive", "epoxy primer", "acrylic primer"]),
    ("roof_coating", ["roof coating", "silicone coating", "acrylic coating", "high solids", "top coat", "base coat"]),
    ("spray_foam", ["spray foam", "spf", "closed cell", "open cell", "polyurethane foam", "2.0 lb", "0.5 lb"]),
    ("sealant", ["sealant", "caulk", "flashing grade", "sausage", "tube", "cartridge"]),
    ("fabric", ["fabric", "reinforcement", "seam fabric"]),
    ("granules", ["granules", "ceramic granules", "broadcast"]),
    ("thinner", ["xylene", "mineral spirits", "naphtha", "solvent", "thinner"]),
]

DECISION_LINKS_BY_CATEGORY = {
    "primer": ["roofing_primer"],
    "roof_coating": ["roofing_coating_system"],
    "spray_foam": ["insulation_foam_system", "roofing_spf"],
    "thermal_barrier": ["insulation_thermal_barrier", "thermal_barrier"],
    "sealant": ["caulk_detail", "seam_treatment"],
    "fabric": ["fabric", "seam_treatment"],
    "granules": ["granules"],
    "thinner": ["insulation_thinner"],
}

SUBSTRATES = [
    "metal",
    "concrete",
    "modified bitumen",
    "asphalt",
    "epdm",
    "tpo",
    "pvc",
    "spray polyurethane foam",
    "wood",
    "masonry",
    "existing silicone",
]


def detect_manufacturer(text: str, path: Path | None = None) -> str:
    haystack = f"{path.name if path else ''}\n{text[:4000]}".lower()
    for manufacturer in MANUFACTURERS:
        if manufacturer.lower() in haystack:
            return manufacturer
    if "dc315" in haystack or "dc 315" in haystack:
        return "International Fireproof Technology"
    return ""


def detect_category(text: str, product_name: str = "") -> tuple[str, str]:
    name_text = str(product_name or "").lower()
    for category, keywords in CATEGORY_KEYWORDS:
        if any(keyword in name_text for keyword in keywords):
            subcategory = next((keyword for keyword in keywords if keyword in name_text), "")
            return category, subcategory
    haystack = f"{product_name}\n{text[:6000]}".lower()
    for category, keywords in CATEGORY_KEYWORDS:
        if any(keyword in haystack for keyword in keywords):
            subcategory = next((keyword for keyword in keywords if keyword in haystack), "")
            return category, subcategory
    return "unknown", ""


def infer_product_name(text: str, path: Path) -> str:
    filename = path.stem.replace("_", " ").replace("-", " ")
    lines = [clean_text(line) for line in text.splitlines() if clean_text(line)]
    for line in lines[:20]:
        lower = line.lower()
        if any(skip in lower for skip in ("product data sheet", "safety data sheet", "technical bulletin", "application guide")):
            continue
        if 3 <= len(line) <= 90 and not re.search(r"^(table|page|\d+|section)\b", lower):
            if any(marker in lower for marker in ("gaco", "gaf", "primer", "silicone", "foam", "dc", "sealant", "coating")):
                return line
    return clean_text(filename)


def parse_revision_date(text: str) -> str:
    patterns = [
        r"(?:revision|revised|rev\.?|date)\s*(?:date)?\s*[:\-]?\s*([A-Za-z]+\s+\d{1,2},?\s+\d{4})",
        r"(?:revision|revised|rev\.?|date)\s*(?:date)?\s*[:\-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        r"\b(\d{4}-\d{2}-\d{2})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1).replace(",", "")
    return ""


def _source_page_for(text: str, page_texts: list[tuple[int, str]], snippet: str) -> int | None:
    needle = snippet[:60].lower()
    for page_num, page_text in page_texts:
        if needle and needle in page_text.lower():
            return page_num
    return None


def _add_property(
    rows: list[dict[str, Any]],
    *,
    product_id: str,
    document_id: str,
    name: str,
    value: str,
    numeric_value: float | None = None,
    numeric_min: float | None = None,
    numeric_max: float | None = None,
    unit: str = "",
    source_page: int | None = None,
    source_text: str = "",
    confidence: float = 0.75,
) -> None:
    rows.append(
        {
            "property_id": slugify(f"{product_id}_{document_id}_{name}_{len(rows)}"),
            "product_id": product_id,
            "document_id": document_id,
            "property_name": name,
            "property_value": value,
            "numeric_value": numeric_value,
            "numeric_min": numeric_min if numeric_min is not None else numeric_value,
            "numeric_max": numeric_max if numeric_max is not None else numeric_value,
            "unit": unit,
            "source_page": source_page,
            "source_text": source_text,
            "confidence": confidence,
        }
    )


def _add_rule(
    rows: list[dict[str, Any]],
    *,
    product_id: str,
    document_id: str,
    rule_type: str,
    rule_value: str,
    source_page: int | None = None,
    source_text: str = "",
    confidence: float = 0.7,
    severity: str = "info",
) -> None:
    rows.append(
        {
            "rule_id": slugify(f"{product_id}_{document_id}_{rule_type}_{len(rows)}"),
            "product_id": product_id,
            "document_id": document_id,
            "rule_type": rule_type,
            "rule_value": rule_value,
            "source_page": source_page,
            "source_text": source_text,
            "confidence": confidence,
            "severity": severity,
        }
    )


def extract_properties(product_id: str, document_id: str, text: str, page_texts: list[tuple[int, str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    patterns = [
        ("coverage_sqft_per_gallon", r"(\d+(?:\.\d+)?)\s*(?:sq\.?\s*ft|square feet|sf)\s*(?:/|per)\s*(?:gal|gallon)", "sqft/gal"),
        ("coverage_gal_per_100_sqft", r"(\d+(?:\.\d+)?)\s*(?:gal|gallons)\s*(?:/|per)\s*(?:100\s*)?(?:sq\.?\s*ft|square feet|sf|square)", "gal/100sqft"),
        ("wet_mils", r"(\d+(?:\.\d+)?)\s*(?:wet\s*)?mils?", "mils"),
        ("dry_time", r"dry(?:\s+time)?\s*[:\-]?\s*([\d.]+\s*(?:hours?|hrs?|minutes?|mins?))", ""),
        ("topcoat_window", r"(?:topcoat|recoat)\s*(?:window|within)?\s*[:\-]?\s*([\d.]+\s*(?:hours?|hrs?|days?))", ""),
        ("density", r"(\d+(?:\.\d+)?)\s*lb\.?\s*(?:density|foam)?", "lb"),
        ("R_value", r"R[-\s]?value\s*[:\-]?\s*([\d.]+)", ""),
        ("pass_thickness_min", r"minimum\s+(?:pass\s+)?thickness\s*[:\-]?\s*([\d.]+)\s*(?:in|inch|inches)", "in"),
        ("pass_thickness_max", r"maximum\s+(?:pass\s+)?thickness\s*[:\-]?\s*([\d.]+)\s*(?:in|inch|inches)", "in"),
        ("VOC", r"VOC\s*[:\-]?\s*([\d.]+)\s*(?:g/L|grams/liter)?", "g/L"),
        ("flash_point", r"flash\s+point\s*[:\-]?\s*([^\n.;]+)", ""),
        ("service_temperature", r"service\s+temperature\s*[:\-]?\s*([^\n.;]+)", ""),
    ]
    for name, pattern, unit in patterns:
        for match in re.finditer(pattern, text, re.I):
            source = clean_text(match.group(0))
            raw_value = clean_text(match.group(1))
            try:
                numeric = float(re.search(r"[\d.]+", raw_value).group(0))  # type: ignore[union-attr]
            except Exception:
                numeric = None
            _add_property(
                rows,
                product_id=product_id,
                document_id=document_id,
                name=name,
                value=raw_value,
                numeric_value=numeric,
                unit=unit,
                source_page=_source_page_for(text, page_texts, source),
                source_text=source,
            )
            break
    return rows


def extract_rules(product_id: str, document_id: str, text: str, page_texts: list[tuple[int, str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    lower = text.lower()
    for substrate in SUBSTRATES:
        if re.search(rf"(?:approved|suitable|recommended|use).*{re.escape(substrate)}", lower):
            source = _sentence_with(text, substrate)
            _add_rule(
                rows,
                product_id=product_id,
                document_id=document_id,
                rule_type="approved_substrate",
                rule_value=substrate,
                source_page=_source_page_for(text, page_texts, source),
                source_text=source,
                confidence=0.65,
            )
        if re.search(rf"(?:not|do not|not recommended|avoid|prohibited).*{re.escape(substrate)}", lower):
            source = _sentence_with(text, substrate)
            _add_rule(
                rows,
                product_id=product_id,
                document_id=document_id,
                rule_type="prohibited_substrate",
                rule_value=substrate,
                source_page=_source_page_for(text, page_texts, source),
                source_text=source,
                confidence=0.7,
                severity="warning",
            )
    rule_patterns = [
        ("requires_primer", r"(?:requires?|must\s+use|prime(?:r)?\s+required).*primer", "warning"),
        ("do_not_prime_existing_silicone", r"(?:do\s+not|not).*prime.*existing\s+silicone", "warning"),
        ("topcoat_within_24_hours", r"(?:topcoat|recoat).*within\s+24\s+hours?", "warning"),
        ("multiple_passes_required", r"(?:multiple|several)\s+passes?\s+(?:are\s+)?required", "info"),
        ("storage", r"store\s+(?:in|at|between)[^.\n;]+", "info"),
        ("cleanup", r"clean(?:up)?\s+(?:with|using)[^.\n;]+", "info"),
        ("limitation", r"(?:limitation|not recommended|do not use|avoid)[^.\n;]+", "warning"),
        ("warranty_guidance", r"warrant(?:y|ies)[^.\n;]+", "info"),
        ("recommended_use", r"(?:recommended use|intended use|used for)[^.\n;]+", "info"),
    ]
    for rule_type, pattern, severity in rule_patterns:
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        source = clean_text(match.group(0))
        _add_rule(
            rows,
            product_id=product_id,
            document_id=document_id,
            rule_type=rule_type,
            rule_value=source,
            source_page=_source_page_for(text, page_texts, source),
            source_text=source,
            confidence=0.75,
            severity=severity,
        )
    return rows


def _sentence_with(text: str, marker: str) -> str:
    pattern = rf"[^.\n;]*{re.escape(marker)}[^.\n;]*"
    match = re.search(pattern, text, re.I)
    return clean_text(match.group(0)) if match else marker
