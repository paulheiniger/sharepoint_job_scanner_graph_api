from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any

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
    sqft = to_float(scope.get("surface_area_sqft")) or parse_field_sqft(notes)
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
    penetrations_complexity = first_nonblank(scope.get("penetrations_complexity"))
    if not penetrations_complexity and any(token in text for token in ("rtu", "rtus", "rooftop unit", "hvac", "drain", "skylight")):
        penetrations_complexity = "high"

    missing = []
    if not sqft:
        missing.append("estimated_sqft")
    if not substrate:
        missing.append("substrate")
    if not first_nonblank(scope.get("roof_condition")):
        missing.append("roof_condition")
    if not coating_type and not warranty_target:
        missing.append("coating/warranty target")
    if not first_nonblank(field_input.site_address, city):
        missing.append("address/city for travel")

    confidence = max(0.25, min(0.9, 0.9 - len(missing) * 0.08))
    return ParsedFieldNotes(
        project_type=project_type,
        division=first_nonblank(scope.get("division")),
        building_type=first_nonblank(scope.get("building_type")),
        substrate=substrate,
        estimated_sqft=sqft,
        coating_type=coating_type,
        warranty_target_years=int(warranty_target or 0) or None,
        roof_condition=first_nonblank(scope.get("roof_condition")),
        access_complexity=first_nonblank(scope.get("access_complexity")),
        penetrations_complexity=penetrations_complexity,
        insulation_present=scope.get("insulation_present"),
        condensation_risk=bool(scope.get("condensation_risk")),
        city=city,
        state=state,
        missing_info=missing,
        confidence=round(confidence, 2),
    )


def parsed_to_scope(parsed: ParsedFieldNotes, field_input: FieldNotesInput) -> dict[str, Any]:
    scope = asdict(parsed)
    scope.update(
        {
            "notes": field_input.raw_notes,
            "surface_area_sqft": parsed.estimated_sqft,
            "warranty_target": parsed.warranty_target_years,
            "coating_required": bool(parsed.coating_type or parsed.warranty_target_years),
            "location": ", ".join(part for part in (parsed.city, parsed.state) if part),
            "site_address": field_input.site_address,
            "destination_address": first_nonblank(field_input.site_address, ", ".join(part for part in (parsed.city, parsed.state) if part)),
            "human_review_required": bool(parsed.missing_info),
        }
    )
    return scope
