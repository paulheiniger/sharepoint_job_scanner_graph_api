from __future__ import annotations

import re
from typing import Iterable


INSULATION_ROUTE_KEYWORDS = (
    "foam sprayed",
    "spray foam",
    "sprayed foam",
    "foam insulation",
    "open cell",
    "closed cell",
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
    "acrylic coating",
    "roof restoration",
    "roof repair",
    "roof leak",
    "membrane roof",
    "standing seam roof",
)

INSULATION_EXCLUSION_PATTERNS = (
    r"\b(?:do(?:es)?|did)\s*n[\u2019']?t\s+(?:include|use|need|require|involve)\s+(?:any\s+)?(?:spray\s+)?foam(?:\s+insulation)?\b",
    r"\b(?:do(?:es)?|did)\s+not\s+(?:include|use|need|require|involve)\s+(?:any\s+)?(?:spray\s+)?foam(?:\s+insulation)?\b",
    r"\b(?:exclude[sd]?|excluding|without|no)\s+(?:any\s+)?(?:spray\s+)?foam(?:\s+insulation)?\b",
    r"\b(?:exclude[sd]?|excluding|without|no)\s+(?:any\s+)?insulation\b",
    r"\b(?:spray\s+foam|foam(?:\s+insulation)?|insulation)\s+(?:is|are|was|were)\s+not\s+"
    r"(?:included|required|needed|used|part\s+of\s+(?:the\s+)?scope)\b",
)


def _normalized_route_text(text: str) -> str:
    return " ".join(str(text or "").lower().replace("-", " ").split())


def _route_score(text: str, keywords: Iterable[str]) -> int:
    return sum(1 for keyword in keywords if keyword in text)


def strip_negated_insulation_scope(text: str) -> str:
    """Remove explicit insulation exclusions without hiding other scoped insulation work."""
    normalized = _normalized_route_text(text)
    for pattern in INSULATION_EXCLUSION_PATTERNS:
        normalized = re.sub(pattern, " ", normalized, flags=re.I)
    return " ".join(normalized.split())


def has_explicit_insulation_exclusion(text: str) -> bool:
    normalized = _normalized_route_text(text)
    return any(re.search(pattern, normalized, re.I) for pattern in INSULATION_EXCLUSION_PATTERNS)


def is_insulation_quote(text: str) -> bool:
    normalized = strip_negated_insulation_scope(text)
    insulation_score = _route_score(normalized, INSULATION_ROUTE_KEYWORDS)
    if any(phrase in normalized for phrase in ("outside walls", "walls and ceiling", "metal building")):
        insulation_score += 2
    roofing_score = _route_score(normalized, ROOFING_ROUTE_KEYWORDS)
    if roofing_score > 0:
        return insulation_score > roofing_score
    return insulation_score > 0
