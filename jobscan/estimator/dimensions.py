from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .rules import to_float


DEDUCT_WORDS = ("deduct", "less", "minus", "remove", "exclude", "not included", "take out")
INCLUDE_WORDS = (
    "area",
    "section",
    "roof",
    "wall",
    "main",
    "plus",
    "addition",
    "north",
    "south",
    "east",
    "west",
)

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

DIMENSION_RE = re.compile(
    r"(?P<length>\d[\d,]*(?:\.\d+)?)\s*(?:ft|feet|foot|[']|[’])?\s*"
    r"(?:x|X|by|BY|×)\s*"
    r"(?P<width>\d[\d,]*(?:\.\d+)?)\s*(?:ft|feet|foot|[']|[’])?",
)
AREA_RANGE_RE = re.compile(
    r"(?P<low>\d[\d,]*(?:\.\d+)?)\s*-\s*(?P<high>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<k>k)?\s*(?:sq\.?\s*ft|sqft|sf|square\s*feet)\b",
    re.I,
)
AREA_RE = re.compile(
    r"(?:(?:about|around|roughly|approx(?:imately)?|totaling)\s+)?"
    r"(?P<area>\d[\d,]*(?:\.\d+)?)\s*(?P<k>k)?\s*"
    r"(?:sq\.?\s*ft|sqft|sf|square\s*feet)\b",
    re.I,
)
QUANTITY_RE = re.compile(
    r"\b(?P<qty>one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d{1,3})\s+"
    r"(?P<object>(?:[a-z]+[\s-]+){0,4}?"
    r"(?:skylights?|doors?|windows?|rtus?|units?|sections?|areas?|overhangs?|penthouses?))\b"
    r"[^.;:\n]{0,35}$",
    re.I,
)


@dataclass
class DimensionArea:
    label: str
    length: float | None
    width: float | None
    quantity: int
    area_each: float | None
    total_area: float
    operation: str
    confidence: float
    source_text: str


@dataclass
class DimensionSummary:
    gross_area_sqft: float | None = None
    deduction_area_sqft: float = 0.0
    net_area_sqft: float | None = None
    included_areas: list[DimensionArea] = field(default_factory=list)
    deducted_areas: list[DimensionArea] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    confidence: float = 0.0
    stated_sqft: float | None = None
    stated_sqft_low: float | None = None
    stated_sqft_high: float | None = None
    no_deductions: bool = False

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["included_areas"] = [asdict(area) for area in self.included_areas]
        out["deducted_areas"] = [asdict(area) for area in self.deducted_areas]
        return out


def _number(value: Any) -> float | None:
    return to_float(str(value).replace(",", ""))


def _clean_area(value: float | None) -> float | None:
    if value is None:
        return None
    rounded = round(float(value), 2)
    return int(rounded) if rounded.is_integer() else rounded


def _quantity_value(value: str) -> int | None:
    text = value.strip().lower()
    if text in NUMBER_WORDS:
        return NUMBER_WORDS[text]
    number = to_float(text)
    if number is None:
        return None
    return int(number)


def _sentence_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    left_candidates = [text.rfind(marker, 0, start) for marker in (".", ";", "\n")]
    left = max(left_candidates)
    right_candidates = [idx for idx in (text.find(marker, end) for marker in (".", ";", "\n")) if idx != -1]
    right = min(right_candidates) if right_candidates else len(text)
    return left + 1, right


def _operation_for_context(context: str) -> str:
    lowered = context.lower()
    if any(word in lowered for word in DEDUCT_WORDS):
        return "deduct"
    return "include"


def _label_for_dimension(prefix: str, operation: str) -> str:
    text = " ".join(prefix.strip(" ,:-").split())
    if not text:
        return "Deduction" if operation == "deduct" else "Area"
    words = text.split()
    return " ".join(words[-8:])


def _quantity_for_dimension(prefix: str, sibling_count: int) -> int:
    match = QUANTITY_RE.search(prefix.lower())
    if not match:
        return 1
    quantity = _quantity_value(match.group("qty"))
    if not quantity:
        return 1
    if sibling_count > 1 and quantity == sibling_count:
        return 1
    return quantity


def _area_operation_near(text: str, start: int) -> str:
    left = max(0, start - 80)
    return _operation_for_context(text[left:start])


def _parse_direct_areas(text: str, summary: DimensionSummary) -> list[DimensionArea]:
    direct_deductions: list[DimensionArea] = []
    range_spans: list[tuple[int, int]] = []
    for match in AREA_RANGE_RE.finditer(text):
        low = _number(match.group("low"))
        high = _number(match.group("high"))
        if low is None or high is None:
            continue
        if match.group("k") or max(low, high) < 100:
            low *= 1000
            high *= 1000
        summary.stated_sqft_low = _clean_area(low)
        summary.stated_sqft_high = _clean_area(high)
        summary.stated_sqft = _clean_area((low + high) / 2)
        summary.warnings.append("Area was stated as a range; midpoint was used.")
        range_spans.append(match.span())

    for match in AREA_RE.finditer(text):
        if any(start <= match.start() < end for start, end in range_spans):
            continue
        area = _number(match.group("area"))
        if area is None:
            continue
        if match.group("k") or ("k" in match.group(0).lower() and area < 1000):
            area *= 1000
        area = float(area)
        operation = _area_operation_near(text, match.start())
        if operation == "deduct":
            left, right = _sentence_bounds(text, match.start(), match.end())
            source = text[left:right].strip()
            direct_deductions.append(
                DimensionArea(
                    label="Direct area deduction",
                    length=None,
                    width=None,
                    quantity=1,
                    area_each=_clean_area(area),
                    total_area=_clean_area(area) or 0.0,
                    operation="deduct",
                    confidence=0.8,
                    source_text=source,
                )
            )
        elif summary.stated_sqft is None:
            summary.stated_sqft = _clean_area(area)
    return direct_deductions


def parse_dimensions(raw_notes: str) -> DimensionSummary:
    text = raw_notes or ""
    summary = DimensionSummary()
    correction_markers = [
        match.end()
        for match in re.finditer(r"\b(?:scratch\s+that|correction|corrected|actually)\b", text, re.I)
        if DIMENSION_RE.search(text[match.end() : match.end() + 160])
    ]
    if correction_markers:
        text = text[max(correction_markers) :]
    if re.search(r"\bno\s+(?:deductions?|deducts?|openings?|areas?\s+to\s+deduct)\b", text, re.I):
        summary.no_deductions = True
    direct_deductions = _parse_direct_areas(text, summary)
    matches = list(DIMENSION_RE.finditer(text))
    spans_by_sentence: dict[tuple[int, int], int] = {}
    for match in matches:
        bounds = _sentence_bounds(text, match.start(), match.end())
        spans_by_sentence[bounds] = spans_by_sentence.get(bounds, 0) + 1

    for match in matches:
        length = _number(match.group("length"))
        width = _number(match.group("width"))
        if length is None or width is None:
            continue
        left, right = _sentence_bounds(text, match.start(), match.end())
        sentence = text[left:right].strip()
        prefix = text[left : match.start()]
        if re.search(r"\b(?:not|instead\s+of|rather\s+than)\s*$", prefix, re.I):
            continue
        context = text[left:right]
        operation = _operation_for_context(context)
        sibling_count = spans_by_sentence.get((left, right), 1)
        quantity = _quantity_for_dimension(prefix, sibling_count)
        area_each = length * width
        total_area = area_each * quantity
        unit_confidence = 0.9 if re.search(r"(?:ft|feet|foot|[']|[’])", match.group(0), re.I) else 0.75
        dimension = DimensionArea(
            label=_label_for_dimension(prefix, operation),
            length=_clean_area(length),
            width=_clean_area(width),
            quantity=quantity,
            area_each=_clean_area(area_each),
            total_area=_clean_area(total_area) or 0.0,
            operation=operation,
            confidence=unit_confidence,
            source_text=sentence,
        )
        if operation == "deduct":
            summary.deducted_areas.append(dimension)
        else:
            summary.included_areas.append(dimension)

    summary.deducted_areas.extend(direct_deductions)
    gross = sum(area.total_area for area in summary.included_areas)
    deductions = sum(area.total_area for area in summary.deducted_areas)
    if gross:
        summary.gross_area_sqft = _clean_area(gross)
        summary.deduction_area_sqft = _clean_area(deductions) or 0.0
        summary.net_area_sqft = _clean_area(max(gross - deductions, 0))
    elif summary.stated_sqft is not None:
        summary.gross_area_sqft = summary.stated_sqft
        summary.deduction_area_sqft = _clean_area(deductions) or 0.0
        summary.net_area_sqft = _clean_area(max(float(summary.stated_sqft) - deductions, 0))
    elif deductions:
        summary.deduction_area_sqft = _clean_area(deductions) or 0.0
        summary.warnings.append("Deductions were found but no gross area was found.")

    if summary.stated_sqft and gross and summary.net_area_sqft:
        difference = abs(float(summary.stated_sqft) - float(summary.net_area_sqft))
        if difference / max(float(summary.stated_sqft), 1) > 0.10:
            summary.warnings.append("Dimension math differs from stated sqft.")

    if summary.included_areas or summary.deducted_areas:
        summary.confidence = min([area.confidence for area in summary.included_areas + summary.deducted_areas] or [0.8])
    elif summary.stated_sqft is not None:
        summary.confidence = 0.75 if summary.stated_sqft_low and summary.stated_sqft_high else 0.85
    else:
        summary.confidence = 0.0
    return summary
