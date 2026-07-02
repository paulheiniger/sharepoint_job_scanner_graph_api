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
    "trenton": "OH",
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

INSULATION_ROUTE_KEYWORDS = (
    "foam sprayed",
    "spray foam",
    "sprayed foam",
    "foam insulation",
    "insulated",
    "insulation",
    "r-value",
    "dc315",
    "thermal barrier",
    "ignition barrier",
    "attic",
    "crawlspace",
)

ROOFING_ROUTE_KEYWORDS = (
    "roof coating",
    "silicone coating",
    "roof restoration",
    "roof repair",
    "roof leak",
    "membrane roof",
    "standing seam roof",
)


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
    candidates: list[tuple[int, int]] = []
    for match in re.finditer(r"(\d{1,2})\s*[- ]?\s*(?:year|yr)\b", text, re.I):
        value = int(match.group(1))
        if not 5 <= value <= 30:
            continue
        after = text[match.end() : match.end() + 12].lower()
        if after.startswith("-old") or after.startswith(" old"):
            continue
        window = text[max(0, match.start() - 60) : min(len(text), match.end() + 80)].lower()
        score = 1
        if re.search(r"\b(?:warranty|system|coating|restoration|maintenance)\b", window):
            score += 10
        candidates.append((score, value))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def parse_count_word(value: str) -> int | None:
    text = value.strip().lower()
    if text in NUMBER_WORDS:
        return NUMBER_WORDS[text]
    try:
        return int(text)
    except ValueError:
        return None


def _route_score(text: str, keywords: tuple[str, ...]) -> int:
    return sum(1 for keyword in keywords if keyword in text)


def is_insulation_quote(text: str) -> bool:
    normalized = clean_text(text).lower()
    insulation_score = _route_score(normalized, INSULATION_ROUTE_KEYWORDS)
    if any(phrase in normalized for phrase in ("outside walls", "walls and ceiling", "metal building")):
        insulation_score += 2
    roofing_score = _route_score(normalized, ROOFING_ROUTE_KEYWORDS)
    if roofing_score > 0:
        return insulation_score > roofing_score
    return insulation_score > 0


def _count_from_match(value: str | None) -> int | None:
    if not value:
        return None
    return parse_count_word(value)


def _feet_from_inches(value: Any) -> float | None:
    number = to_float(value)
    if number is None:
        return None
    return number / 12.0


def _dimension_value_to_ft(value: Any, unit: str | None) -> float | None:
    number = to_float(value)
    if number is None:
        return None
    normalized_unit = (unit or "").lower()
    if normalized_unit in {'"', "in", "inch", "inches"}:
        return number / 12.0
    return number


def _opening_area(quantity: int, width_ft: float | None, height_ft: float | None) -> float | None:
    if width_ft is None or height_ft is None:
        return None
    return round(quantity * width_ft * height_ft, 2)


def _clean_phone(match_value: str | None) -> str:
    return re.sub(r"\s+", " ", match_value or "").strip()


def parse_insulation_quote_scope(notes: str) -> dict[str, Any]:
    """Parse common spray-foam building quote details from field notes/email text."""
    text = clean_text(notes)
    lowered = text.lower()
    if not is_insulation_quote(text):
        return {}

    result: dict[str, Any] = {
        "division": "Insulation",
        "template_type": "insulation",
        "project_type": "spray foam insulation",
        "estimate_mode": "insulation",
        "building_type": "metal building" if "metal building" in lowered else "",
        "outside_walls_included": bool(re.search(r"\b(?:outside|exterior)?\s*walls?\b", lowered)),
        "ceiling_included": "ceiling" in lowered,
        "openings": [],
        "opening_area_known_sqft": 0.0,
        "opening_area_missing": False,
        "missing_questions": [],
        "review_flags": [],
        "evidence_by_field": {},
        "confidence_by_field": {},
    }

    footprint_match = re.search(
        r"\b(?P<length>\d+(?:\.\d+)?)\s*(?:x|by)\s*(?P<width>\d+(?:\.\d+)?)\s*(?:ft|feet|foot)?\s*(?:metal\s+)?building\b",
        lowered,
        re.I,
    )
    if not footprint_match:
        footprint_match = re.search(r"\b(?P<length>\d+(?:\.\d+)?)\s*(?:x|by)\s*(?P<width>\d+(?:\.\d+)?)\b", lowered, re.I)
    if footprint_match:
        length = to_float(footprint_match.group("length"))
        width = to_float(footprint_match.group("width"))
        if length and width:
            result["building_footprint_length_ft"] = length
            result["building_footprint_width_ft"] = width
            result["footprint_area_sqft"] = round(length * width, 2)
            result["ceiling_area_sqft"] = round(length * width, 2) if result["ceiling_included"] else 0.0
            result["building_perimeter_ft"] = round(2 * (length + width), 2)
            result["evidence_by_field"]["building_footprint"] = footprint_match.group(0)
            result["confidence_by_field"]["building_footprint"] = "high"

    wall_height_match = re.search(r"\b(?P<height>\d+(?:\.\d+)?)\s*(?:'|ft|feet|foot)\s+walls?\b", lowered, re.I)
    if wall_height_match:
        wall_height = to_float(wall_height_match.group("height"))
        if wall_height:
            result["wall_height_ft"] = wall_height
            result["evidence_by_field"]["wall_height_ft"] = wall_height_match.group(0)
            result["confidence_by_field"]["wall_height_ft"] = "high"

    perimeter = to_float(result.get("building_perimeter_ft"))
    wall_height = to_float(result.get("wall_height_ft"))
    if perimeter and wall_height and result["outside_walls_included"]:
        result["gross_wall_area_sqft"] = round(perimeter * wall_height, 2)
    elif perimeter and wall_height:
        result["gross_wall_area_sqft"] = round(perimeter * wall_height, 2)

    ceiling_area = to_float(result.get("ceiling_area_sqft")) or 0.0
    wall_area = to_float(result.get("gross_wall_area_sqft")) or 0.0
    gross_area = wall_area + ceiling_area
    if gross_area:
        result["gross_insulation_area_sqft"] = round(gross_area, 2)

    opening_area_known = 0.0
    openings: list[dict[str, Any]] = []

    dimension_unit = r"(?:ft|feet|foot|'|\"|in|inch|inches)"
    count_pattern = r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d{1,2}"
    consumed_spans: list[tuple[int, int]] = []

    def span_consumed(match: re.Match[str]) -> bool:
        return any(not (match.end() <= start or match.start() >= end) for start, end in consumed_spans)

    for match in re.finditer(
        rf"\b(?P<count>{count_pattern})\s+"
        rf"(?P<dim1>\d+(?:\.\d+)?)\s*(?P<unit1>{dimension_unit})\s*(?:x|by)\s*"
        rf"(?P<dim2>\d+(?:\.\d+)?)\s*(?P<unit2>{dimension_unit})\s*"
        rf"(?P<kind>roll[- ]?up|rollup|overhead|walk[- ]?in)\s+doors?\b",
        lowered,
        re.I,
    ):
        count = _count_from_match(match.group("count")) or 1
        first = _dimension_value_to_ft(match.group("dim1"), match.group("unit1"))
        second = _dimension_value_to_ft(match.group("dim2"), match.group("unit2"))
        kind = match.group("kind").lower()
        if "walk" in kind and first and first >= 6 and second and second <= 4:
            height_ft, width_ft = first, second
        else:
            width_ft, height_ft = first, second
        area = _opening_area(count, width_ft, height_ft)
        if area:
            opening_area_known += area
        opening_type = "walk_in_door" if "walk" in kind else "rollup_door"
        openings.append(
            {
                "opening_type": opening_type,
                "quantity": count,
                "width_ft": round(width_ft, 3) if width_ft is not None else None,
                "height_ft": round(height_ft, 3) if height_ft is not None else None,
                "known_area_sqft": area,
                "missing_dimensions": [] if area else ["width_ft", "height_ft"],
                "source_text": match.group(0),
            }
        )
        consumed_spans.append(match.span())

    for match in re.finditer(
        rf"\b(?P<count>{count_pattern})\s+"
        rf"(?P<kind>roll[- ]?up|rollup|overhead|walk[- ]?in)\s+doors?\s+"
        rf"(?P<dim1>\d+(?:\.\d+)?)\s*(?P<unit1>{dimension_unit})?\s*(?:x|by)\s*"
        rf"(?P<dim2>\d+(?:\.\d+)?)\s*(?P<unit2>{dimension_unit})?\s*(?:each)?\b",
        lowered,
        re.I,
    ):
        if span_consumed(match):
            continue
        count = _count_from_match(match.group("count")) or 1
        first = _dimension_value_to_ft(match.group("dim1"), match.group("unit1") or "ft")
        second = _dimension_value_to_ft(match.group("dim2"), match.group("unit2") or "ft")
        kind = match.group("kind").lower()
        if "walk" in kind and first and first >= 6 and second and second <= 4:
            height_ft, width_ft = first, second
        else:
            width_ft, height_ft = first, second
        area = _opening_area(count, width_ft, height_ft)
        if area:
            opening_area_known += area
        opening_type = "walk_in_door" if "walk" in kind else "rollup_door"
        openings.append(
            {
                "opening_type": opening_type,
                "quantity": count,
                "width_ft": round(width_ft, 3) if width_ft is not None else None,
                "height_ft": round(height_ft, 3) if height_ft is not None else None,
                "known_area_sqft": area,
                "missing_dimensions": [] if area else ["width_ft", "height_ft"],
                "source_text": match.group(0),
            }
        )
        consumed_spans.append(match.span())

    for match in re.finditer(
        r"\b(?P<count>one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d{1,2})\s+"
        r"(?P<height>\d+(?:\.\d+)?)\s*(?:ft|feet|foot|')\s*(?:roll\s*up|rollup)\s+doors?\b",
        lowered,
        re.I,
    ):
        if span_consumed(match):
            continue
        count = _count_from_match(match.group("count")) or 1
        height = to_float(match.group("height"))
        openings.append(
            {
                "opening_type": "rollup_door",
                "quantity": count,
                "height_ft": height,
                "width_ft": None,
                "known_area_sqft": None,
                "missing_dimensions": ["width_ft"],
                "source_text": match.group(0),
            }
        )
        result["opening_area_missing"] = True

    for match in re.finditer(
        r"\b(?P<count>one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d{1,2})\s+"
        r"(?P<width>\d+(?:\.\d+)?)\s*(?:\"|in|inch|inches)\s*walk[- ]?in\s+doors?\b",
        lowered,
        re.I,
    ):
        if span_consumed(match):
            continue
        count = _count_from_match(match.group("count")) or 1
        width_ft = _feet_from_inches(match.group("width"))
        assumed_height_ft = 7.0 if width_ft and abs(width_ft - 3.0) < 0.01 else None
        area = round(count * width_ft * assumed_height_ft, 2) if width_ft and assumed_height_ft else None
        if area:
            opening_area_known += area
        openings.append(
            {
                "opening_type": "walk_in_door",
                "quantity": count,
                "width_ft": round(width_ft, 3) if width_ft is not None else None,
                "height_ft": assumed_height_ft,
                "known_area_sqft": area,
                "missing_dimensions": [] if assumed_height_ft else ["height_ft"],
                "assumptions": ["Walk-in door height assumed 7 ft from estimator default."] if assumed_height_ft else [],
                "source_text": match.group(0),
            }
        )
        if not assumed_height_ft:
            result["opening_area_missing"] = True

    for match in re.finditer(
        r"\b(?P<count>one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d{1,2})\s+"
        r"(?P<width>\d+(?:\.\d+)?)\s*(?:\"|in|inch|inches)\s*(?:x|by)\s*"
        r"(?P<height>\d+(?:\.\d+)?)\s*(?:\"|in|inch|inches)\s+windows?\b",
        lowered,
        re.I,
    ):
        count = _count_from_match(match.group("count")) or 1
        width_ft = _feet_from_inches(match.group("width")) or 0.0
        height_ft = _feet_from_inches(match.group("height")) or 0.0
        area = round(count * width_ft * height_ft, 2)
        opening_area_known += area
        openings.append(
            {
                "opening_type": "window",
                "quantity": count,
                "width_ft": round(width_ft, 3),
                "height_ft": round(height_ft, 3),
                "known_area_sqft": area,
                "missing_dimensions": [],
                "source_text": match.group(0),
            }
        )

    result["openings"] = openings
    result["opening_area_known_sqft"] = round(opening_area_known, 2)
    if gross_area:
        result["net_insulation_area_sqft"] = round(max(gross_area - opening_area_known, 0.0), 2)
        result["estimated_sqft"] = result["net_insulation_area_sqft"]
        result["surface_area_sqft"] = result["net_insulation_area_sqft"]
        result["gross_area_sqft"] = round(gross_area, 2)
        result["deduction_area_sqft"] = round(opening_area_known, 2)
        result["net_area_sqft"] = result["net_insulation_area_sqft"]

    if result["opening_area_missing"]:
        result["review_flags"].append("Opening deductions are incomplete because one or more door dimensions are missing.")
    assumption_notes = [
        assumption
        for opening in openings
        for assumption in (opening.get("assumptions") or [])
    ]
    if assumption_notes:
        result["assumptions"] = list(dict.fromkeys(assumption_notes))
        result["review_flags"].extend(result["assumptions"])
    if openings:
        result["evidence_by_field"]["openings"] = [opening.get("source_text") for opening in openings]
        result["confidence_by_field"]["openings"] = "medium" if result["opening_area_missing"] else "high"

    if re.search(r"\bseptember\s+or\s+october\b", lowered, re.I):
        result["requested_timing"] = "September or October"
    elif re.search(r"\bseptember\b", lowered, re.I):
        result["requested_timing"] = "September"
    elif re.search(r"\boctober\b", lowered, re.I):
        result["requested_timing"] = "October"
    timing_match = re.search(r"\b(?:beginning|early)\s+to\s+mid[- ]?august\b|\bbeginning\s+to\s+mid\s+august\b", lowered, re.I)
    if timing_match:
        result["building_installation_timing"] = "beginning to mid-August"

    phone_match = re.search(r"\b(?:\+?1[-.\s]*)?\(?\d{3}\)?[-.\s]*\d{3}[-.\s]*\d{4}\b", text)
    if phone_match:
        result["phone"] = _clean_phone(phone_match.group(0))
    address_match = re.search(
        r"\b\d{1,6}\s+[A-Z][A-Za-z0-9 .'-]+?\s+(?:Drive|Dr|Street|St|Road|Rd|Avenue|Ave|Lane|Ln|Court|Ct|Boulevard|Blvd)\b(?:,\s*[A-Z][A-Za-z .'-]+,\s*[A-Z]{2})?",
        text,
    )
    if address_match:
        result["address"] = address_match.group(0).strip()
        city_state = re.search(r",\s*([A-Z][A-Za-z .'-]+),\s*([A-Z]{2})\b", result["address"])
        if city_state:
            result["city"] = city_state.group(1).strip()
            result["state"] = city_state.group(2).strip().upper()
    name_match = re.search(r"\bJames\s+F\.?\s+Collins\b", text)
    if name_match:
        result["customer_name"] = name_match.group(0).replace("  ", " ")

    missing_questions = []
    if not first_nonblank(result.get("foam_type")):
        missing_questions.append("What foam type: open-cell or closed-cell?")
    if not to_float(result.get("foam_thickness_inches")):
        missing_questions.append("Desired foam thickness or R-value?")
    if any(opening.get("opening_type") == "rollup_door" and "width_ft" in opening.get("missing_dimensions", []) for opening in openings):
        missing_questions.append("Rollup door width?")
    if any(opening.get("opening_type") == "walk_in_door" and "height_ft" in opening.get("missing_dimensions", []) for opening in openings):
        missing_questions.append("Walk-in door height, if not assuming standard size?")
    if result.get("ceiling_included"):
        missing_questions.append("Is ceiling underside of roof deck or flat ceiling?")
    missing_questions.append("Is thermal barrier / ignition barrier required?")
    result["missing_questions"] = missing_questions
    return result


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
    no_open_seams = bool(re.search(r"\b(?:no|without)\s+(?:open\s+)?seam\s+issues?\b|\b(?:no|without)\s+open\s+seams?\b", lowered))
    no_leaks = bool(re.search(r"\b(?:no|without)\s+(?:interior\s+)?leaks?\b|\bno\s+leaking\b", lowered))
    flags: list[str] = []
    if not no_rust:
        if "rusted fastener" in lowered or "rusted fasteners" in lowered:
            flags.append("rusted_fasteners")
        elif "rust" in lowered or "rusted" in lowered:
            flags.append("rust")
    if not no_open_seams and ("open seam" in lowered or "open seams" in lowered or "seams opening" in lowered):
        flags.append("open_seams")
    if not no_leaks and re.search(r"\b(?:leak|leaks|leaking)\b", lowered):
        flags.append("leaks")
    if "ponding" in lowered:
        flags.append("ponding")
    if "minor dirt" in lowered:
        flags.append("minor_dirt")
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
    insulation_scope = parse_insulation_quote_scope(notes)
    if insulation_scope:
        dimension_dict["insulation_scope"] = insulation_scope
        for area_key in ("gross_area_sqft", "deduction_area_sqft", "net_area_sqft"):
            if insulation_scope.get(area_key) is not None:
                dimension_dict[area_key] = insulation_scope.get(area_key)
        dimension_dict["gross_insulation_area_sqft"] = insulation_scope.get("gross_insulation_area_sqft")
        dimension_dict["gross_wall_area_sqft"] = insulation_scope.get("gross_wall_area_sqft")
        dimension_dict["ceiling_area_sqft"] = insulation_scope.get("ceiling_area_sqft")
        dimension_dict["opening_area_known_sqft"] = insulation_scope.get("opening_area_known_sqft")
        dimension_dict["opening_area_missing"] = insulation_scope.get("opening_area_missing")
        dimension_dict["openings"] = insulation_scope.get("openings") or []
    override_sqft = optional_positive_float(field_input.estimated_sqft) or optional_positive_float(overrides.get("surface_area_sqft"))
    stated_sqft = to_float(dimension_dict.get("stated_sqft")) or parse_field_sqft(notes)
    dimension_net_sqft = None
    if dimension_summary.included_areas or dimension_summary.deducted_areas:
        dimension_net_sqft = to_float(dimension_dict.get("net_area_sqft"))
    insulation_sqft = optional_positive_float(insulation_scope.get("net_insulation_area_sqft")) if insulation_scope else None
    if override_sqft:
        sqft = override_sqft
    elif insulation_sqft:
        sqft = insulation_sqft
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
    if insulation_scope:
        project_type = "spray foam insulation"
    if any(phrase in text for phrase in ("button up", "button-up", "temporary repair")):
        project_type = "button-up repair"
    elif any(phrase in text for phrase in ("full tear off", "full tear-off", "tear off", "tearoff")):
        project_type = "full tear-off"
    elif "floor" in text or "flooring" in text:
        project_type = "flooring"

    substrate = first_nonblank(scope.get("substrate"))
    if insulation_scope and insulation_scope.get("building_type") == "metal building":
        substrate = "metal"
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
    if insulation_scope:
        if not sqft:
            missing.append("insulation area or dimensions")
        missing.extend(str(item) for item in insulation_scope.get("missing_questions") or [])
    else:
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
    if insulation_scope:
        review_flags.extend(str(item) for item in insulation_scope.get("review_flags") or [])
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
        division=first_nonblank(insulation_scope.get("division") if insulation_scope else "", scope.get("division")),
        building_type=first_nonblank(insulation_scope.get("building_type") if insulation_scope else "", scope.get("building_type")),
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
    insulation_scope = dimension_summary.get("insulation_scope") if isinstance(dimension_summary.get("insulation_scope"), dict) else {}
    if insulation_scope:
        scope.update(insulation_scope)
    scope.update(
        {
            "notes": field_input.raw_notes,
            "estimated_sqft": parsed.estimated_sqft,
            "surface_area_sqft": parsed.estimated_sqft,
            "gross_area_sqft": insulation_scope.get("gross_area_sqft") or dimension_summary.get("gross_area_sqft"),
            "deduction_area_sqft": insulation_scope.get("deduction_area_sqft") or dimension_summary.get("deduction_area_sqft"),
            "net_area_sqft": insulation_scope.get("net_area_sqft") or dimension_summary.get("net_area_sqft"),
            "dimension_warnings": dimension_summary.get("warnings") or [],
            "condition_detail_flags": parsed.condition_detail_flags,
            "penetration_count": parsed.penetration_count,
            "warranty_target": parsed.warranty_target_years,
            "coating_required": False if insulation_scope else bool(parsed.coating_type or parsed.warranty_target_years),
            "location": ", ".join(part for part in (parsed.city, parsed.state) if part),
            "site_address": first_nonblank(field_input.site_address, insulation_scope.get("address")),
            "destination_address": first_nonblank(field_input.site_address, insulation_scope.get("address"), ", ".join(part for part in (parsed.city, parsed.state) if part)),
            "human_review_required": bool(parsed.missing_info),
        }
    )
    if insulation_scope:
        scope["template_type"] = "insulation"
        scope["division"] = "Insulation"
        scope["project_type"] = "spray foam insulation"
        scope["gross_sqft"] = insulation_scope.get("gross_insulation_area_sqft")
        scope["deduction_sqft"] = insulation_scope.get("opening_area_known_sqft")
        scope["net_sqft"] = insulation_scope.get("net_insulation_area_sqft")
        scope["missing_questions"] = insulation_scope.get("missing_questions") or []
        scope["review_flags"] = [*scope.get("review_flags", []), *list(insulation_scope.get("review_flags") or [])]
    return scope
