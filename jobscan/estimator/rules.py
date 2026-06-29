from __future__ import annotations

import re
from typing import Any


BLANKS = {"", "nan", "none", "null", "n/a", "-"}


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in BLANKS else text


def first_nonblank(*values: Any) -> str:
    for value in values:
        text = clean_text(value)
        if text:
            return text
    return ""


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").strip()
    if not text or text.lower() in BLANKS:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_sqft(text: str, *, wall: bool = False) -> float | None:
    label = r"(?:wall|walls|vertical)\s+" if wall else r""
    pattern = re.compile(rf"{label}(\d[\d,]*(?:\.\d+)?)\s*(?:sq\.?\s*ft|sqft|sf|square\s*feet)", re.I)
    match = pattern.search(text)
    if match:
        return to_float(match.group(1))
    if not wall:
        about_match = re.search(r"(?:about|around|approx(?:imately)?|roughly)?\s*(\d[\d,]*(?:\.\d+)?)\s*(?:k)?\s*(?:square|sq|sf)", text, re.I)
        if about_match:
            value = to_float(about_match.group(1))
            if value and "k" in about_match.group(0).lower() and value < 1000:
                value *= 1000
            return value
    return None


def parse_foam_thickness(text: str) -> float | None:
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:in|inch|inches|[\"”])\s*"
        r"(?:thick|thickness|foam|spf|spray foam|closed[- ]cell|open[- ]cell)?",
        text,
        re.I,
    )
    if match:
        return to_float(match.group(1))
    return None


def parse_foam_type(text: str) -> str:
    lowered = text.lower()
    if re.search(r"\bclosed[- ]cell\b", lowered):
        return "closed_cell"
    if re.search(r"\bopen[- ]cell\b", lowered):
        return "open_cell"
    if "spray foam" in lowered or "spf" in lowered or "polyurethane foam" in lowered:
        return "spray_foam"
    return ""


def parse_warranty_target(text: str) -> int | None:
    match = re.search(r"(\d{1,2})\s*(?:year|yr)\s*(?:warranty|system|coating)?", text, re.I)
    if match:
        value = int(match.group(1))
        if 5 <= value <= 30:
            return value
    return None


def detect_location(notes: str) -> str:
    city_state = re.search(r"\b(?:in|near|at)\s+([A-Z][A-Za-z .'-]+,\s*[A-Z]{2})\b", notes)
    if city_state:
        return city_state.group(1).strip()
    city = re.search(r"\b(?:in|near|at)\s+(Louisville|Lexington|Shelbyville|Cincinnati|Indianapolis|Nashville|Columbus)\b", notes, re.I)
    return city.group(1).title() if city else ""


def default_crew_size(project_type: str) -> int:
    key = project_type.lower()
    if "foam" in key and "roof" not in key:
        return 3
    if "repair" in key:
        return 2
    return 4


def extract_scope(notes: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    overrides = overrides or {}
    notes_text = clean_text(notes)
    text = notes_text.lower()

    substrate = ""
    if "metal" in text:
        substrate = "metal"
    elif "concrete" in text:
        substrate = "concrete"
    elif "epdm" in text:
        substrate = "epdm"
    elif "tpo" in text:
        substrate = "tpo"
    elif "wood" in text:
        substrate = "wood"

    coating_type = ""
    if "silicone" in text:
        coating_type = "silicone"
    elif "acrylic" in text:
        coating_type = "acrylic"
    elif "urethane" in text or "polyurethane" in text:
        coating_type = "urethane"

    project_type = ""
    if "repair" in text:
        project_type = "roof repair"
    elif "spray foam" in text or "spf" in text or "foam insulation" in text:
        project_type = "spray foam insulation" if "roof" not in text else "coated foam roof"
    elif "roof" in text:
        project_type = "roof coating" if coating_type else "roofing"
    elif "wall" in text:
        project_type = "wall insulation"

    condition = ""
    no_visible_rust = bool(re.search(r"\b(?:no|without)\s+(?:visible\s+)?rust\b|\bno\s+rusted\s+fasteners?\b", text))
    if any(word in text for word in ("rust", "rusted", "corroded", "poor", "leak", "leaking")) and not no_visible_rust:
        condition = "poor/rusted"
    elif any(word in text for word in ("fair", "aged", "weathered")):
        condition = "fair"
    elif "good" in text:
        condition = "good"

    access = ""
    if "difficult access" in text or "tight access" in text or "high access" in text:
        access = "high"
    elif "medium access" in text or "moderate access" in text:
        access = "medium"
    elif "easy access" in text or "low access" in text:
        access = "low"

    penetrations = "high" if any(phrase in text for phrase in ("many penetration", "many penetrations", "lots of penetration", "lots of penetrations")) else ""
    prep = "high" if condition.startswith("poor") or "pressure wash" in text or "fastener" in text else ""
    surface_area = parse_sqft(notes_text)
    wall_area = parse_sqft(notes_text, wall=True)
    foam_thickness = parse_foam_thickness(notes_text)
    foam_type = parse_foam_type(notes_text)
    warranty_target = parse_warranty_target(notes_text)
    location = detect_location(notes_text)
    insulation_missing = any(phrase in text for phrase in ("no insulation", "missing insulation", "uninsulated", "needs insulation"))
    insulation_present = any(phrase in text for phrase in ("existing insulation", "insulated", "insulation present")) and not insulation_missing
    condensation_risk = any(word in text for word in ("condensation", "sweating", "sweat", "moisture drive"))
    rust_level = "" if no_visible_rust else "high" if any(phrase in text for phrase in ("heavy rust", "severe rust", "corroded")) else "medium" if "rust" in text or "rusted" in text else ""

    scope = {
        "notes": notes_text,
        "project_type": project_type,
        "division": "ROOFING" if "roof" in text else "WALLS" if "wall" in text else "",
        "building_type": "restaurant" if "restaurant" in text else "commercial" if "commercial" in text else "",
        "substrate": substrate,
        "surface_area_sqft": surface_area,
        "wall_area_sqft": wall_area,
        "coating_required": bool(coating_type or "coating" in text or "coat" in text),
        "coating_type": coating_type,
        "foam_required": bool(foam_thickness or "foam" in text or "spf" in text),
        "foam_type": foam_type,
        "foam_thickness_inches": foam_thickness,
        "insulation_present": insulation_present,
        "insulation_missing": insulation_missing,
        "condensation_risk": condensation_risk,
        "warranty_target": warranty_target,
        "roof_condition": condition,
        "access_complexity": access,
        "penetrations_complexity": penetrations,
        "prep_complexity": prep,
        "rust_level": rust_level,
        "tearoff_likely": "tear off" in text or "tearoff" in text,
        "location": location,
        "confidence": 0.65,
        "human_review_required": False,
    }
    for key, value in overrides.items():
        if clean_text(value) or isinstance(value, (int, float, bool)):
            scope[key] = value

    missing = []
    if not to_float(scope.get("surface_area_sqft")) and not to_float(scope.get("wall_area_sqft")):
        missing.append("surface_area_sqft")
    if not first_nonblank(scope.get("project_type"), scope.get("division")):
        missing.append("project_type")
    if scope.get("coating_required") and not first_nonblank(scope.get("coating_type")):
        missing.append("coating_type")
    if scope.get("foam_required") and not to_float(scope.get("foam_thickness_inches")):
        missing.append("foam_thickness_inches")
    if not first_nonblank(scope.get("location")):
        missing.append("location")

    confidence = 0.85 - 0.08 * len(missing)
    scope["missing_info"] = missing
    scope["confidence"] = max(0.25, min(0.9, confidence))
    scope["human_review_required"] = bool(missing)
    return scope
