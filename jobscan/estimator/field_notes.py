from __future__ import annotations

import re
import math
from dataclasses import asdict
from typing import Any

from .dimensions import parse_dimensions
from .rules import clean_text, extract_scope, first_nonblank, to_float
from .schemas import FieldNotesInput, ParsedFieldNotes

STATE_BY_CITY = {
    "shelbyville": "KY",
    "louisville": "KY",
    "lexington": "KY",
    "frankfort": "KY",
    "cincinnati": "OH",
    "indianapolis": "IN",
    "nashville": "TN",
    "columbus": "OH",
}

NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}


def optional_positive_float(value: Any) -> float | None:
    number = to_float(value)
    if number is None:
        return None
    if not math.isfinite(number):
        return None
    return number if number > 0 else None


def parse_field_sqft(text: str) -> float | None:
    normalized = text.replace(",", "")
    patterns = [
        r"(\d+(?:\.\d+)?)\s*k\s*(?:sq\.?\s*ft|sqft|sf|square\s*feet)?",
        r"(\d+(?:\.\d+)?)\s*(?:sq\.?\s*ft|sqft|sf|square\s*feet)",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized, re.I)
        if match:
            value = to_float(match.group(1))
            if value is None:
                continue
            if "k" in match.group(0).lower() and value < 1000:
                value *= 1000
            return value
    return None


def parse_city_state(text: str) -> tuple[str, str]:
    explicit = re.search(r"\b([A-Z][A-Za-z .'-]+),\s*([A-Z]{2})\b", text)
    if explicit:
        return explicit.group(1).strip(), explicit.group(2).strip().upper()
    for city, state in STATE_BY_CITY.items():
        if re.search(rf"\b{re.escape(city)}\b", text, re.I):
            return city.title(), state
    return "", ""


def parse_field_warranty_target(text: str) -> int | None:
    match = re.search(r"(\d{1,2})\s*[- ]?\s*(?:year|yr)", text, re.I)
    if not match:
        return None
    value = int(match.group(1))
    return value if 5 <= value <= 30 else None


def parse_count_word(value: str) -> int | None:
    text = value.strip().lower()
    if text in NUMBER_WORDS:
        return NUMBER_WORDS[text]
    try:
        return int(text)
    except ValueError:
        return None


def parse_penetration_count(text: str) -> int | None:
    total = 0
    matches = re.finditer(
        r"\b(?P<count>one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d{1,3})\s+"
        r"(?P<object>(?:plumbing\s+)?vents?|hvac\s+curbs?|curbs?|rtus?|rooftop\s+units?|drains?|skylights?|penetrations?)\b",
        text,
        re.I,
    )
    for match in matches:
        count = parse_count_word(match.group("count"))
        if count is not None:
            total += count
    return total or None


def parse_condition_detail_flags(text: str) -> list[str]:
    lowered = text.lower()
    no_rust = bool(re.search(r"\b(?:no|without)\s+(?:visible\s+)?rust\b|\bno\s+rusted\s+fasteners?\b", lowered))
    flags: list[str] = []
    if not no_rust:
        if "rusted fastener" in lowered or "rusted fasteners" in lowered:
            flags.append("rusted_fasteners")
        elif "rust" in lowered or "rusted" in lowered:
            flags.append("rust")
    if "open seam" in lowered or "open seams" in lowered or "seams opening" in lowered:
        flags.append("open_seams")
    if "ponding" in lowered:
        flags.append("ponding")
    return flags


def parse_field_notes(field_input: FieldNotesInput | str, overrides: dict[str, Any] | None = None) -> ParsedFieldNotes:
    if isinstance(field_input, str):
        field_input = FieldNotesInput(raw_notes=field_input)
    overrides = overrides or {}
    input_overrides = {
        "surface_area_sqft": field_input.estimated_sqft,
        "substrate": field_input.substrate,
        "roof_condition": field_input.roof_condition,
        "coating_type": field_input.coating_type,
        "warranty_target": field_input.warranty_target_years,
        "access_complexity": field_input.access_complexity,
        "penetrations_complexity": field_input.penetrations_complexity,
        "insulation_present": field_input.insulation_present,
        "condensation_risk": field_input.condensation_risk,
        **overrides,
    }
    scope = extract_scope(field_input.raw_notes, input_overrides)
    notes = clean_text(field_input.raw_notes)
    text = notes.lower()
    dimension_summary = parse_dimensions(notes)
    dimension_dict = dimension_summary.to_dict()
    override_sqft = optional_positive_float(field_input.estimated_sqft) or optional_positive_float(overrides.get("surface_area_sqft"))
    stated_sqft = to_float(dimension_dict.get("stated_sqft")) or parse_field_sqft(notes)
    dimension_net_sqft = None
    if dimension_summary.included_areas or dimension_summary.deducted_areas:
        dimension_net_sqft = to_float(dimension_dict.get("net_area_sqft"))
    if override_sqft:
        sqft = override_sqft
    elif dimension_net_sqft:
        sqft = dimension_net_sqft
    elif stated_sqft:
        sqft = stated_sqft
    else:
        sqft = to_float(scope.get("surface_area_sqft"))
    city, state = parse_city_state(" ".join(first_nonblank(value) for value in (field_input.city, field_input.state, notes)))
    city = first_nonblank(field_input.city, city)
    state = first_nonblank(field_input.state, state)

    if "hydrostop" in text:
        coating_type = "hydrostop"
    elif "gaco" in text:
        coating_type = "gaco silicone"
    elif "polyurea" in text:
        coating_type = "polyurea"
    else:
        coating_type = first_nonblank(scope.get("coating_type"))

    project_type = first_nonblank(scope.get("project_type"))
    if any(phrase in text for phrase in ("button up", "button-up", "temporary repair")):
        project_type = "button-up repair"
    elif any(phrase in text for phrase in ("full tear off", "full tear-off", "tear off", "tearoff")):
        project_type = "full tear-off"
    elif "floor" in text or "flooring" in text:
        project_type = "flooring"

    substrate = first_nonblank(scope.get("substrate"))
    if "shingle" in text:
        substrate = "shingles"
    elif "cmu" in text:
        substrate = "cmu"
    elif "foam" in text and not substrate:
        substrate = "foam"
    warranty_target = to_float(scope.get("warranty_target")) or parse_field_warranty_target(notes)
    condition_detail_flags = parse_condition_detail_flags(notes)
    no_visible_rust = bool(re.search(r"\b(?:no|without)\s+(?:visible\s+)?rust\b|\bno\s+rusted\s+fasteners?\b", text))
    if "excellent condition" in text or "excellent" in text:
        roof_condition = "excellent"
    elif "good condition" in text or ("good" in text and "condition" in text):
        roof_condition = "good"
    elif ("fair overall" in text or "fair condition" in text or "fair" in text) and condition_detail_flags:
        roof_condition = "fair_with_rusted_fasteners" if any("rust" in flag for flag in condition_detail_flags) else "fair"
    elif no_visible_rust and any(term in text for term in ("minor dirt", "maintenance", "five-year-old", "5-year-old")):
        roof_condition = "good"
    else:
        roof_condition = first_nonblank(scope.get("roof_condition"))
    penetration_count = parse_penetration_count(notes)
    penetrations_complexity = first_nonblank(scope.get("penetrations_complexity"))
    if re.search(r"\bfew\s+penetrations?\b", text):
        penetrations_complexity = "low"
    elif penetration_count is not None:
        penetrations_complexity = "low" if penetration_count <= 2 else "medium" if penetration_count <= 8 else "high"
    elif not penetrations_complexity and any(token in text for token in ("rtu", "rtus", "rooftop unit", "hvac", "drain", "skylight")):
        penetrations_complexity = "medium"

    missing = []
    if not sqft:
        missing.append("estimated_sqft")
    if not substrate:
        missing.append("substrate")
    if not first_nonblank(roof_condition):
        missing.append("roof_condition")
    if not coating_type and not warranty_target:
        missing.append("coating/warranty target")
    if not first_nonblank(field_input.site_address, city):
        missing.append("address/city for travel")

    review_flags = list(dimension_summary.warnings)
    if override_sqft and dimension_net_sqft and abs(override_sqft - dimension_net_sqft) / max(override_sqft, 1) > 0.10:
        review_flags.append("Sqft override differs from dimension math; override was used.")
    if any(term in text for term in ("ir scan", "infrared", "moisture scan", "thermal scan")):
        review_flags.append("infrared_scan review: verify infrared/moisture scan requirement.")
    if any(term in text for term in ("granules", "granule", "broadcast")):
        review_flags.append("labor_top_coat_granules review: verify granules/top coat broadcast scope.")

    confidence = max(0.25, min(0.9, 0.9 - len(missing) * 0.08))
    if review_flags:
        confidence = min(confidence, 0.75)
    return ParsedFieldNotes(
        project_type=project_type,
        division=first_nonblank(scope.get("division")),
        building_type=first_nonblank(scope.get("building_type")),
        substrate=substrate,
        estimated_sqft=sqft,
        coating_type=coating_type,
        foam_type=first_nonblank(scope.get("foam_type")),
        foam_thickness_inches=to_float(scope.get("foam_thickness_inches")),
        warranty_target_years=int(warranty_target or 0) or None,
        roof_condition=roof_condition,
        access_complexity=first_nonblank(scope.get("access_complexity")),
        penetrations_complexity=penetrations_complexity,
        penetration_count=penetration_count,
        condition_detail_flags=condition_detail_flags,
        insulation_present=scope.get("insulation_present"),
        condensation_risk=bool(scope.get("condensation_risk")),
        city=city,
        state=state,
        missing_info=missing,
        review_flags=review_flags,
        dimension_summary=dimension_dict,
        confidence=round(confidence, 2),
    )


def parsed_to_scope(parsed: ParsedFieldNotes, field_input: FieldNotesInput) -> dict[str, Any]:
    scope = asdict(parsed)
    dimension_summary = parsed.dimension_summary or {}
    scope.update(
        {
            "notes": field_input.raw_notes,
            "estimated_sqft": parsed.estimated_sqft,
            "surface_area_sqft": parsed.estimated_sqft,
            "gross_area_sqft": dimension_summary.get("gross_area_sqft"),
            "deduction_area_sqft": dimension_summary.get("deduction_area_sqft"),
            "net_area_sqft": dimension_summary.get("net_area_sqft"),
            "dimension_warnings": dimension_summary.get("warnings") or [],
            "condition_detail_flags": parsed.condition_detail_flags,
            "penetration_count": parsed.penetration_count,
            "warranty_target": parsed.warranty_target_years,
            "coating_required": bool(parsed.coating_type or parsed.warranty_target_years),
            "location": ", ".join(part for part in (parsed.city, parsed.state) if part),
            "site_address": field_input.site_address,
            "destination_address": first_nonblank(field_input.site_address, ", ".join(part for part in (parsed.city, parsed.state) if part)),
            "human_review_required": bool(parsed.missing_info),
        }
    )
    return scope
