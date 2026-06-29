from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any


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

ROOF_TYPE_KEYWORDS = {
    "metal": ["metal", "standing seam", "r panel", "corrugated"],
    "tpo": ["tpo"],
    "epdm": ["epdm", "rubber roof"],
    "modified_bitumen": ["modified bitumen", "mod bit", "torch down"],
    "built_up": ["built-up", "bur", "tar and gravel"],
    "shingle": ["shingle", "asphalt shingle"],
    "foam": ["foam roof", "spray foam", "spf"],
    "coated_roof": ["silicone", "acrylic coating", "roof coating", "coated roof"],
}

ISSUE_KEYWORDS = {
    "pipe_boot_leak": ["pipe boot", "plumbing boot", "vent boot"],
    "open_seam": ["open seam", "split seam", "failed seam", "seam leak", "seam repair"],
    "exposed_fasteners": ["exposed fastener", "loose fastener", "rusted fastener", "screw", "fastener"],
    "skylight_curb_leak": ["skylight", "skylight curb"],
    "curb_leak": ["curb", "hvac curb", "unit curb"],
    "drain_leak": ["drain", "scupper"],
    "small_coating_touch_up": ["touch up", "touch-up", "top coat", "recoat", "coating repair", "small coating"],
    "puncture_or_patch": ["puncture", "hole", "tear", "patch"],
    "flashing_leak": ["flashing", "counterflashing", "edge metal"],
    "gutter_downspout": ["gutter", "downspout"],
    "unknown_leak": ["leak", "water intrusion", "active leak", "water entering", "water coming in"],
}

ACTION_KEYWORDS = {
    "inspect": ["inspect", "investigate", "find leak", "locate leak"],
    "clean": ["clean", "power wash", "wash"],
    "seal": ["seal", "caulk", "sealant"],
    "patch": ["patch", "repair membrane", "cover patch"],
    "reinforce_with_fabric": ["fabric", "reinforce", "fleece"],
    "replace_fasteners": ["replace fastener", "new screws", "replace screws"],
    "coat": ["coat", "top coat", "apply coating"],
}

MATERIAL_KEYWORDS = {
    "sealant": ["sealant", "caulk", "np1", "aldo", "dow"],
    "fabric": ["fabric", "fleece", "reinforcement"],
    "coating": ["silicone", "acrylic", "sf2000", "coating"],
    "fasteners": ["fastener", "screw", "anchor"],
    "membrane": ["membrane", "patch"],
    "flashing": ["flashing", "edge metal"],
    "primer": ["primer"],
}


@dataclass
class ParsedRepairScope:
    repair_type: str = "unknown"
    roof_type: str = "unknown"
    issue_type: str = "unknown"
    affected_area: str = ""
    affected_area_sqft: float | None = None
    affected_linear_feet: float | None = None
    penetration_count: int | None = None
    leak_present: bool = False
    emergency_or_standard: str = "standard"
    access_complexity: str = "unknown"
    materials_mentioned: list[str] = field(default_factory=list)
    actions_requested: list[str] = field(default_factory=list)
    missing_info: list[str] = field(default_factory=list)
    review_flags: list[str] = field(default_factory=list)
    evidence_terms: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_notes(notes: str | None) -> str:
    return re.sub(r"\s+", " ", (notes or "").strip().lower())


def _contains_any(text: str, terms: list[str]) -> list[str]:
    return [term for term in terms if term in text]


def _first_keyword_match(text: str, mapping: dict[str, list[str]]) -> tuple[str, list[str]]:
    for key, terms in mapping.items():
        matches = _contains_any(text, terms)
        if matches:
            return key, matches
    return "unknown", []


def _quantity_from_match(value: str) -> int | None:
    value = value.strip().lower()
    if value.isdigit():
        return int(value)
    return NUMBER_WORDS.get(value)


def parse_penetration_count(text: str) -> int | None:
    total = 0
    found = False
    pattern = re.compile(
        r"\b(?P<count>\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+"
        r"(?P<thing>pipe boots?|plumbing vents?|vents?|hvac curbs?|curbs?|skylights?|drains?|penetrations?)\b"
    )
    for match in pattern.finditer(text):
        count = _quantity_from_match(match.group("count"))
        if count:
            total += count
            found = True
    if found:
        return total
    if any(term in text for term in ["many penetrations", "lots of penetrations"]):
        return 12
    if any(term in text for term in ["few penetrations", "couple penetrations"]):
        return 3
    if "penetration" in text or any(term in text for term in ["pipe boot", "vent", "curb", "drain", "skylight"]):
        return 1
    return None


def parse_affected_area(text: str) -> tuple[str, float | None, float | None]:
    sqft = None
    linear_feet = None
    area_match = re.search(r"\b(?:about|approx(?:imately)?|around)?\s*(?P<value>\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:sq\.?\s*ft|sqft|sf|square feet)\b", text)
    if area_match:
        sqft = float(area_match.group("value").replace(",", ""))
    lf_match = re.search(r"\b(?:about|approx(?:imately)?|around)?\s*(?P<value>\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:lf|ln ft|linear feet|linear ft|feet of)\b", text)
    if lf_match:
        linear_feet = float(lf_match.group("value").replace(",", ""))
    phrase = ""
    if sqft is not None:
        phrase = f"{sqft:g} sqft"
    elif linear_feet is not None:
        phrase = f"{linear_feet:g} lf"
    elif any(term in text for term in ["small", "minor", "spot repair"]):
        phrase = "small spot repair"
    elif any(term in text for term in ["large", "multiple areas", "several areas"]):
        phrase = "multiple/large areas"
    return phrase, sqft, linear_feet


def parse_access_complexity(text: str) -> str:
    if any(term in text for term in ["easy access", "parking lot", "ground access", "walk on"]):
        return "low"
    if any(term in text for term in ["difficult access", "hard access", "limited access", "steep", "lift required", "bucket truck"]):
        return "high"
    if any(term in text for term in ["ladder", "roof hatch", "moderate access"]):
        return "medium"
    return "unknown"


def parse_urgency(text: str) -> str:
    if any(term in text for term in ["emergency", "urgent", "same day", "asap", "water coming in"]):
        return "emergency"
    return "standard"


def parse_leak_present(text: str) -> bool:
    negated = [
        r"\bno\s+active\s+leaks?\b",
        r"\bno\s+leaks?\b",
        r"\bnot\s+leaking\b",
        r"\bwithout\s+leaks?\b",
    ]
    if any(re.search(pattern, text) for pattern in negated):
        return False
    return any(term in text for term in ["leak", "leaking", "water intrusion", "water entering", "water coming in"])


def parse_repair_notes(notes: str | None, overrides: dict[str, Any] | None = None) -> ParsedRepairScope:
    overrides = overrides or {}
    text = normalize_notes(notes)
    roof_type, roof_matches = _first_keyword_match(text, ROOF_TYPE_KEYWORDS)
    issue_type, issue_matches = _first_keyword_match(text, ISSUE_KEYWORDS)
    affected_area, sqft, linear_feet = parse_affected_area(text)
    actions = [key for key, terms in ACTION_KEYWORDS.items() if _contains_any(text, terms)]
    materials = [key for key, terms in MATERIAL_KEYWORDS.items() if _contains_any(text, terms)]
    leak_present = parse_leak_present(text)
    penetration_count = parse_penetration_count(text)
    access = parse_access_complexity(text)
    urgency = parse_urgency(text)

    if overrides.get("roof_type"):
        roof_type = str(overrides["roof_type"]).strip().lower().replace(" ", "_")
    if overrides.get("urgency"):
        urgency = str(overrides["urgency"]).strip().lower()

    repair_type = "emergency_leak_call" if urgency == "emergency" and leak_present else issue_type
    if repair_type == "unknown" and leak_present:
        repair_type = "unknown_leak"

    missing: list[str] = []
    if roof_type == "unknown":
        missing.append("roof_type")
    if issue_type == "unknown":
        missing.append("issue_type")
    if access == "unknown":
        missing.append("access_complexity")
    if not affected_area and penetration_count is None:
        missing.append("repair_size_or_count")

    review_flags: list[str] = []
    if len(text) < 25:
        review_flags.append("Repair notes are too vague for confident estimating.")
    if urgency == "emergency":
        review_flags.append("Emergency repair: confirm response time, access, and minimum trip charge.")
    if len(missing) >= 3:
        review_flags.append("Missing several key repair details; ask follow-up questions before quoting.")

    return ParsedRepairScope(
        repair_type=repair_type,
        roof_type=roof_type,
        issue_type=issue_type,
        affected_area=affected_area,
        affected_area_sqft=sqft,
        affected_linear_feet=linear_feet,
        penetration_count=penetration_count,
        leak_present=leak_present,
        emergency_or_standard=urgency,
        access_complexity=access,
        materials_mentioned=materials,
        actions_requested=actions,
        missing_info=missing,
        review_flags=review_flags,
        evidence_terms={
            "roof_type": roof_matches,
            "issue_type": issue_matches,
            "actions": actions,
            "materials": materials,
        },
    )


def finite_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
