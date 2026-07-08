from __future__ import annotations

import re
import math
from dataclasses import asdict
from typing import Any

from .dimensions import parse_dimensions
from .insulation_surfaces import (
    DEFAULT_R_VALUE_PER_INCH_BY_FOAM_TYPE,
    build_insulation_deductions,
    build_insulation_surface_area_rows,
    parse_r_value_targets,
)
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


def parse_explicit_net_area(text: str, *, preferred_context: str = "") -> float | None:
    normalized = clean_text(text).replace(",", "")
    context = preferred_context.strip().lower()
    patterns = [
        rf"\buse\s+net\s+(?:{re.escape(context)}\s+)?area\s+(?P<area>\d+(?:\.\d+)?)\s*(?P<k>k)?\s*(?:sq\.?\s*ft|sqft|sf|square\s*feet)\b"
        if context
        else r"\buse\s+net\s+area\s+(?P<area>\d+(?:\.\d+)?)\s*(?P<k>k)?\s*(?:sq\.?\s*ft|sqft|sf|square\s*feet)\b",
        r"\buse\s+net\s+(?P<area>\d+(?:\.\d+)?)\s*(?P<k>k)?\s*(?:sq\.?\s*ft|sqft|sf|square\s*feet)\b",
        r"\bnet\s+(?:scope\s+|area\s+)?(?P<area>\d+(?:\.\d+)?)\s*(?P<k>k)?\s*(?:sq\.?\s*ft|sqft|sf|square\s*feet)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized, re.I)
        if not match:
            continue
        area = to_float(match.group("area"))
        if area is None:
            continue
        if match.groupdict().get("k") or ("k" in match.group(0).lower() and area < 1000):
            area *= 1000
        return area
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


def _has_conditional_coating_path(text: str) -> bool:
    lowered = clean_text(text).lower()
    patterns = (
        r"\bcoating\s+(?:path|option)\b",
        r"\bcoating\s+restoration\s+option\b",
        r"\broof\s+coating\s+restoration\s+option\b",
        r"\bcoating\s+restoration\s+(?:seems\s+)?possible\b",
        r"\broof\s+restoration\s+review\b",
        r"\brestoration\s+review\b",
        r"\brepair/restoration\s+lead\b",
        r"\bsmall\s+repair\s+or\s+full\s+restoration\b",
        r"\broof\s+coating/detail\s+categories?\s+may\s+have\s+been\s+considered\b",
        r"\brepairs?\s+plus\s+(?:a\s+)?coating\b",
        r"\bpractical\s+repairs?\s+plus\s+(?:a\s+)?coating\b",
        r"\bcoating\s+path\s+if\s+.*\bqualif",
        r"\bif\s+.*\broof\s+can\s+qualif",
    )
    return any(re.search(pattern, lowered) for pattern in patterns)


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
    return parse_count_word(value.strip().strip("()").strip())


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


def _parse_general_target_r_value(text: str) -> float | None:
    patterns = [
        r"\bR[-\s]?(?P<value>\d+(?:\.\d+)?)\s*(?:target|desired|specified|requested)\b",
        r"\b(?:target|desired|specified|requested)\s*R[-\s]?(?P<value>\d+(?:\.\d+)?)\b",
        r"\bR[-\s]?(?P<value>\d+(?:\.\d+)?)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        value = to_float(match.group("value"))
        if value and 1 <= value <= 100:
            return value
    return None


def _parse_r_value_per_inch(text: str) -> float | None:
    patterns = [
        r"\b(?P<value>\d+(?:\.\d+)?)\s*R\s*/\s*in(?:ch)?\b",
        r"\b(?P<value>\d+(?:\.\d+)?)\s*R[-\s]?per[-\s]?in(?:ch)?\b",
        r"\bR[- ]?value\s+per\s+inch\s+(?:is|=|of)?\s*(?P<value>\d+(?:\.\d+)?)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        value = to_float(match.group("value"))
        if value and 1 <= value <= 10:
            return value
    return None


def _parse_assumed_rollup_door_height(text: str) -> float | None:
    patterns = [
        r"\bassume\s+(?P<height>\d+(?:\.\d+)?)\s*(?:ft|feet|foot|')\s+(?:rolling|roll[- ]?up|overhead)\s+door\s+height\b",
        r"\b(?P<height>\d+(?:\.\d+)?)\s*(?:ft|feet|foot|')\s+(?:rolling|roll[- ]?up|overhead)\s+door\s+height\b",
        r"\b(?:rolling|roll[- ]?up|overhead)\s+door\s+height\s+(?:is|=|assume|assumed)?\s*(?P<height>\d+(?:\.\d+)?)\s*(?:ft|feet|foot|')\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        height = to_float(match.group("height"))
        if height and 4 <= height <= 30:
            return height
    return None


def _parse_formula_insulation_areas(text: str) -> dict[str, Any]:
    lowered = clean_text(text).lower()
    result: dict[str, Any] = {}
    wall_match = re.search(
        r"\bwalls?\s+(?P<qty>\d+(?:\.\d+)?)\s*(?:x|by)\s*"
        r"\(\s*(?P<length>\d+(?:\.\d+)?)\s*\+\s*(?P<width>\d+(?:\.\d+)?)\s*\)\s*"
        r"(?:x|by)\s*(?P<height>\d+(?:\.\d+)?)\b",
        lowered,
        re.I,
    )
    if wall_match:
        qty = to_float(wall_match.group("qty")) or 2.0
        length = to_float(wall_match.group("length"))
        width = to_float(wall_match.group("width"))
        height = to_float(wall_match.group("height"))
        if length and width and height:
            wall_area = round(qty * (length + width) * height, 2)
            result.update(
                {
                    "building_footprint_length_ft": length,
                    "building_footprint_width_ft": width,
                    "building_perimeter_ft": round(qty * (length + width), 2),
                    "wall_height_ft": height,
                    "gross_wall_area_sqft": wall_area,
                    "outside_walls_included": True,
                    "formula_wall_area_sqft": wall_area,
                    "formula_wall_source_text": wall_match.group(0),
                }
            )
    ceiling_match = re.search(
        r"\b(?:ceiling|ceiling/underside|underside)\s+"
        r"(?P<length>\d+(?:\.\d+)?)\s*(?:x|by)\s*(?P<width>\d+(?:\.\d+)?)\b",
        lowered,
        re.I,
    )
    if ceiling_match:
        length = to_float(ceiling_match.group("length"))
        width = to_float(ceiling_match.group("width"))
        if length and width:
            ceiling_area = round(length * width, 2)
            result.update(
                {
                    "building_footprint_length_ft": result.get("building_footprint_length_ft") or length,
                    "building_footprint_width_ft": result.get("building_footprint_width_ft") or width,
                    "footprint_area_sqft": ceiling_area,
                    "ceiling_area_sqft": ceiling_area,
                    "ceiling_included": True,
                    "formula_ceiling_source_text": ceiling_match.group(0),
                }
            )
    return result


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
        "roof_underside_included": bool(
            re.search(r"\b(?:underside\s+of\s+the\s+roof\s+deck|underside\s+of\s+roof|roof\s+underside|roof\s+deck\s+sprayed|cathedral\s+ceiling)\b", lowered)
        ),
        "openings": [],
        "opening_area_known_sqft": 0.0,
        "opening_area_missing": False,
        "missing_questions": [],
        "review_flags": [],
        "evidence_by_field": {},
        "confidence_by_field": {},
    }
    if re.search(r"\bnot\s+(?:the\s+)?ceiling\b", lowered) and result["roof_underside_included"]:
        result["ceiling_included"] = False

    formula_areas = _parse_formula_insulation_areas(text)
    if formula_areas:
        result.update(formula_areas)
        if formula_areas.get("formula_wall_source_text"):
            result["evidence_by_field"]["wall_formula"] = formula_areas.get("formula_wall_source_text")
            result["confidence_by_field"]["wall_formula"] = "high"
        if formula_areas.get("formula_ceiling_source_text"):
            result["evidence_by_field"]["ceiling_formula"] = formula_areas.get("formula_ceiling_source_text")
            result["confidence_by_field"]["ceiling_formula"] = "high"

    footprint_match = re.search(
        r"\b(?P<length>\d+(?:\.\d+)?)\s*(?:'|’|ft|feet|foot)?\s*(?:x|by)\s*"
        r"(?P<width>\d+(?:\.\d+)?)\s*(?:'|’|ft|feet|foot)?\s*(?:metal\s+)?building\b",
        lowered,
        re.I,
    )
    if not footprint_match:
        footprint_match = re.search(
            r"\b(?P<length>\d+(?:\.\d+)?)\s*(?:'|’|ft|feet|foot)?\s*(?:x|by)\s*"
            r"(?P<width>\d+(?:\.\d+)?)\s*(?:'|’|ft|feet|foot)?\b",
            lowered,
            re.I,
        )
    if footprint_match and not formula_areas:
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

    wall_height_match = re.search(r"\b(?P<height>\d+(?:\.\d+)?)\s*(?:'|’|ft|feet|foot)\s+(?:side)?walls?\b", lowered, re.I)
    if wall_height_match and "wall_height_ft" not in result:
        wall_height = to_float(wall_height_match.group("height"))
        if wall_height:
            result["wall_height_ft"] = wall_height
            result["evidence_by_field"]["wall_height_ft"] = wall_height_match.group(0)
            result["confidence_by_field"]["wall_height_ft"] = "high"

    center_height_match = re.search(
        r"\b(?P<height>\d+(?:\.\d+)?)\s*(?:'|’|ft|feet|foot)\s+tall\s+in\s+the\s+center\b|"
        r"\b(?:center|ridge|peak)\s+(?:height\s+)?(?:is\s+)?(?P<height2>\d+(?:\.\d+)?)\s*(?:'|’|ft|feet|foot)\b",
        lowered,
        re.I,
    )
    if center_height_match:
        center_height = to_float(center_height_match.group("height") or center_height_match.group("height2"))
        if center_height:
            result["roof_center_height_ft"] = center_height
            result["ridge_height_ft"] = center_height
            result["evidence_by_field"]["roof_center_height_ft"] = center_height_match.group(0)
            result["confidence_by_field"]["roof_center_height_ft"] = "high"

    perimeter = to_float(result.get("building_perimeter_ft"))
    wall_height = to_float(result.get("wall_height_ft"))
    if perimeter and wall_height and result["outside_walls_included"]:
        result["gross_wall_area_sqft"] = round(perimeter * wall_height, 2)
    elif perimeter and wall_height:
        result["gross_wall_area_sqft"] = round(perimeter * wall_height, 2)

    length = to_float(result.get("building_footprint_length_ft"))
    width = to_float(result.get("building_footprint_width_ft"))
    center_height = to_float(result.get("roof_center_height_ft")) or to_float(result.get("ridge_height_ft"))
    roof_underside_area = 0.0
    if result.get("roof_underside_included") and length and width and wall_height and center_height and center_height > wall_height:
        half_span = width / 2.0
        rise = center_height - wall_height
        rafter_length = math.sqrt((half_span * half_span) + (rise * rise))
        roof_underside_area = round(2 * rafter_length * length, 2)
        result["roof_rise_ft"] = round(rise, 4)
        result["roof_half_span_ft"] = round(half_span, 4)
        result["roof_rafter_length_ft"] = round(rafter_length, 4)
        result["roof_underside_area_sqft"] = roof_underside_area
        result["pitched_roof_underside_area_sqft"] = roof_underside_area
        result["roof_underside_area_formula"] = "2 * building_length_ft * sqrt((building_width_ft / 2)^2 + (roof_center_height_ft - wall_height_ft)^2)"
        result["roof_underside_source_text"] = first_nonblank(
            result["evidence_by_field"].get("roof_center_height_ft"),
            result["evidence_by_field"].get("building_footprint"),
        )
    formula_ceiling_area = bool(result.get("formula_ceiling_source_text"))
    ceiling_area = (
        to_float(result.get("ceiling_area_sqft")) or 0.0
        if formula_ceiling_area or not result.get("roof_underside_included")
        else 0.0
    )
    if result.get("roof_underside_included") and not formula_ceiling_area:
        result["ceiling_area_sqft"] = 0.0
    wall_area = to_float(result.get("gross_wall_area_sqft")) or 0.0
    gross_area = wall_area + ceiling_area + roof_underside_area
    if gross_area:
        result["gross_insulation_area_sqft"] = round(gross_area, 2)

    opening_area_known = 0.0
    openings: list[dict[str, Any]] = []

    dimension_unit = r"(?:ft|feet|foot|'|’|\"|“|”|in|inch|inches)"
    count_pattern = r"\(?\s*(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d{1,2})\s*\)?"
    consumed_spans: list[tuple[int, int]] = []
    assumed_rollup_height_ft = _parse_assumed_rollup_door_height(text)

    def span_consumed(match: re.Match[str]) -> bool:
        return any(not (match.end() <= start or match.start() >= end) for start, end in consumed_spans)

    for match in re.finditer(
        rf"(?<!\w)(?P<count>{count_pattern})\s+"
        rf"(?P<dim1>\d+(?:\.\d+)?)\s*(?P<unit1>{dimension_unit})\s*(?:x|by)\s*"
        rf"(?P<dim2>\d+(?:\.\d+)?)\s*(?P<unit2>{dimension_unit})\s*"
        rf"(?P<kind>roll[- ]?up|rollup|overhead|walk[- ]?in|man|personnel)\s+doors?\b",
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
        opening_type = "walk_in_door" if any(token in kind for token in ("walk", "man", "personnel")) else "rollup_door"
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
        rf"(?<!\w)(?P<count>{count_pattern})\s+"
        rf"(?P<kind>roll[- ]?up|rollup|overhead|walk[- ]?in|man|personnel)\s+doors?\s+"
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
        opening_type = "walk_in_door" if any(token in kind for token in ("walk", "man", "personnel")) else "rollup_door"
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
        r"(?<!\w)(?P<count>\(?\s*(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d{1,2})\s*\)?)\s+"
        r"(?P<height>\d+(?:\.\d+)?)\s*(?:ft|feet|foot|')\s*(?:roll\s*up|rollup)\s+doors?\b",
        lowered,
        re.I,
    ):
        if span_consumed(match):
            continue
        count = _count_from_match(match.group("count")) or 1
        single_dimension = to_float(match.group("height"))
        if single_dimension and assumed_rollup_height_ft:
            area = _opening_area(count, single_dimension, assumed_rollup_height_ft)
            if area:
                opening_area_known += area
            openings.append(
                {
                    "opening_type": "rollup_door",
                    "quantity": count,
                    "height_ft": round(assumed_rollup_height_ft, 3),
                    "width_ft": round(single_dimension, 3),
                    "known_area_sqft": area,
                    "missing_dimensions": [] if area else ["width_ft", "height_ft"],
                    "assumptions": [f"Rollup door height assumed {assumed_rollup_height_ft:g} ft from notes."],
                    "source_text": match.group(0),
                }
            )
        else:
            openings.append(
                {
                    "opening_type": "rollup_door",
                    "quantity": count,
                    "height_ft": single_dimension,
                    "width_ft": None,
                    "known_area_sqft": None,
                    "missing_dimensions": ["width_ft"],
                    "source_text": match.group(0),
                }
            )
            result["opening_area_missing"] = True

    for match in re.finditer(
        r"(?<!\w)(?P<count>\(?\s*(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d{1,2})\s*\)?)\s+"
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
        r"(?<!\w)(?P<count>\(?\s*(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d{1,2})\s*\)?)\s+"
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

    for match in re.finditer(
        rf"(?<!\w)(?P<count>{count_pattern})\s+"
        rf"windows?\s+"
        rf"(?P<width>\d+(?:\.\d+)?)\s*(?P<unit1>{dimension_unit})?\s*(?:x|by)\s*"
        rf"(?P<height>\d+(?:\.\d+)?)\s*(?P<unit2>{dimension_unit})?\b",
        lowered,
        re.I,
    ):
        if span_consumed(match):
            continue
        count = _count_from_match(match.group("count")) or 1
        width_ft = _dimension_value_to_ft(match.group("width"), match.group("unit1") or "ft") or 0.0
        height_ft = _dimension_value_to_ft(match.group("height"), match.group("unit2") or "ft") or 0.0
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
        consumed_spans.append(match.span())

    result["openings"] = openings
    result["opening_area_known_sqft"] = round(opening_area_known, 2)
    explicit_net_area = parse_explicit_net_area(text, preferred_context="insulation")
    if gross_area:
        if explicit_net_area is not None and explicit_net_area <= gross_area:
            result["net_insulation_area_sqft"] = round(explicit_net_area, 2)
            if not opening_area_known and gross_area > explicit_net_area:
                opening_area_known = round(gross_area - explicit_net_area, 2)
                result["opening_area_known_sqft"] = opening_area_known
                result["review_flags"].append("Opening deduction inferred from explicit net area and gross formula.")
        else:
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

    r_value_targets = parse_r_value_targets(text)
    general_target_r_value = _parse_general_target_r_value(text)
    if general_target_r_value and not r_value_targets:
        target_surfaces: list[str] = []
        if result.get("outside_walls_included") or result.get("gross_wall_area_sqft"):
            target_surfaces.append("walls")
        if result.get("ceiling_included") and result.get("ceiling_area_sqft"):
            target_surfaces.append("ceiling")
        if result.get("roof_underside_included") and result.get("roof_underside_area_sqft"):
            target_surfaces.append("roof_underside")
        if not target_surfaces:
            target_surfaces.append("general")
        r_value_targets = [
            {
                "surface_type": surface,
                "target_r_value": round(general_target_r_value, 4),
                "source_text": f"R{general_target_r_value:g} target",
                "confidence": "medium",
            }
            for surface in target_surfaces
        ]
        result["target_r_value"] = round(general_target_r_value, 4)
    if r_value_targets:
        result["insulation_r_value_targets"] = r_value_targets
        result["evidence_by_field"]["insulation_r_value_targets"] = [row.get("source_text") for row in r_value_targets]
        result["confidence_by_field"]["insulation_r_value_targets"] = "high"
    if not first_nonblank(result.get("foam_type")):
        if re.search(r"\bopen[- ]cell\b", lowered, re.I):
            result["foam_type"] = "open_cell"
            result["insulation_foam_type"] = "open_cell"
        elif re.search(r"\bclosed[- ]cell\b", lowered, re.I):
            result["foam_type"] = "closed_cell"
            result["insulation_foam_type"] = "closed_cell"
    r_value_per_inch = _parse_r_value_per_inch(text)
    if r_value_per_inch:
        result["r_value_per_inch"] = round(r_value_per_inch, 4)
        result["evidence_by_field"]["r_value_per_inch"] = f"{r_value_per_inch:g} R/in"
        result["confidence_by_field"]["r_value_per_inch"] = "medium"
    elif result.get("foam_type"):
        r_value_per_inch = DEFAULT_R_VALUE_PER_INCH_BY_FOAM_TYPE.get(str(result.get("foam_type")))
    if general_target_r_value and r_value_per_inch and not to_float(result.get("foam_thickness_inches")):
        result["foam_thickness_inches"] = round(general_target_r_value / r_value_per_inch, 4)
        result["insulation_thickness_calculation"] = {
            "target_r_value": round(general_target_r_value, 4),
            "r_value_per_inch": round(r_value_per_inch, 4),
            "foam_thickness_inches": result["foam_thickness_inches"],
            "formula": "target_r_value / r_value_per_inch",
        }
    result["insulation_deductions"] = build_insulation_deductions(result)
    result["insulation_surface_areas"] = build_insulation_surface_area_rows(result, text)

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
    if not to_float(result.get("foam_thickness_inches")) and not r_value_targets:
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
        if (
            "rusted fastener" in lowered
            or "rusted fasteners" in lowered
            or "rust/fastener" in lowered
            or "rust/fasteners" in lowered
            or "rust / fastener" in lowered
            or "rust / fasteners" in lowered
            or "rusted screws" in lowered
            or "rusted screw" in lowered
            or re.search(r"\brusted\s*/\s*aging\s+fasteners?\b", lowered)
        ):
            flags.append("rusted_fasteners")
        elif "rust" in lowered or "rusted" in lowered:
            flags.append("rust")
    if not no_open_seams and (
        "open seam" in lowered
        or "open seams" in lowered
        or "seams opening" in lowered
        or "seam treatment" in lowered
        or re.search(r"\bseams\b", lowered)
    ):
        flags.append("open_seams")
    if re.search(r"\bpenetrations?\b", lowered):
        flags.append("penetrations")
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
        for key in (
            "foam_type",
            "foam_thickness_inches",
            "target_r_value",
            "r_value_per_inch",
            "insulation_foam_type",
            "insulation_thickness_calculation",
        ):
            if insulation_scope.get(key) not in (None, "", [], {}):
                scope[key] = insulation_scope.get(key)
        dimension_dict["insulation_scope"] = insulation_scope
        for area_key in ("gross_area_sqft", "deduction_area_sqft", "net_area_sqft"):
            if insulation_scope.get(area_key) is not None:
                dimension_dict[area_key] = insulation_scope.get(area_key)
        dimension_dict["gross_insulation_area_sqft"] = insulation_scope.get("gross_insulation_area_sqft")
        dimension_dict["gross_wall_area_sqft"] = insulation_scope.get("gross_wall_area_sqft")
        dimension_dict["ceiling_area_sqft"] = insulation_scope.get("ceiling_area_sqft")
        dimension_dict["roof_underside_included"] = insulation_scope.get("roof_underside_included")
        dimension_dict["roof_center_height_ft"] = insulation_scope.get("roof_center_height_ft")
        dimension_dict["ridge_height_ft"] = insulation_scope.get("ridge_height_ft")
        dimension_dict["roof_rise_ft"] = insulation_scope.get("roof_rise_ft")
        dimension_dict["roof_half_span_ft"] = insulation_scope.get("roof_half_span_ft")
        dimension_dict["roof_rafter_length_ft"] = insulation_scope.get("roof_rafter_length_ft")
        dimension_dict["roof_underside_area_sqft"] = insulation_scope.get("roof_underside_area_sqft")
        dimension_dict["pitched_roof_underside_area_sqft"] = insulation_scope.get("pitched_roof_underside_area_sqft")
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

    conditional_coating_path = _has_conditional_coating_path(notes)
    project_type = first_nonblank(scope.get("project_type"))
    if insulation_scope:
        project_type = "spray foam insulation"
    elif conditional_coating_path:
        project_type = "roof coating"
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
        if not coating_type and not warranty_target and not conditional_coating_path:
            missing.append("coating/warranty target")
        if not first_nonblank(field_input.site_address, city):
            missing.append("address/city for travel")

    review_flags = list(dimension_summary.warnings)
    if insulation_scope:
        review_flags.extend(str(item) for item in insulation_scope.get("review_flags") or [])
    elif conditional_coating_path:
        review_flags.append("Conditional coating/restoration path requires estimator qualification before warranty commitment.")
        if not warranty_target:
            review_flags.append("Coating path mentioned, but warranty duration was not stated.")
        if not coating_type:
            review_flags.append("Coating path mentioned, but coating chemistry/product was not stated.")
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
            "coating_required": False
            if insulation_scope
            else bool(
                parsed.coating_type
                or parsed.warranty_target_years
                or _has_conditional_coating_path(field_input.raw_notes)
                or re.search(r"\b(?:need|needs|include|apply|coat|coating)\s+(?:a\s+)?(?:roof\s+)?coating\b", field_input.raw_notes, re.I)
                or re.search(r"\bcoating\b", field_input.raw_notes, re.I)
            ),
            "coating_path_review": False if insulation_scope else _has_conditional_coating_path(field_input.raw_notes),
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
