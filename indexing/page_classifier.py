from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ingest.pdf_ingest import PageRecord


DEFAULT_CONFIG = Path("configs/foam_keywords.yaml")


def load_keyword_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    if not path.exists():
        return {"foam_keywords": {}, "role_keywords": {}}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def keyword_hits(text: str, keywords: list[str]) -> list[str]:
    lowered = (text or "").lower()
    return [keyword for keyword in keywords if keyword.lower() in lowered]


FOAM_SPECIFIC_TERMS = [
    "spray foam",
    "sprayed polyurethane foam",
    "polyurethane foam",
    "closed-cell",
    "closed cell",
    "open-cell",
    "open cell",
    "spf",
    "07 21 00",
    "thermal insulation",
    "r-value",
    "air barrier",
    "vapor barrier",
]

GENERIC_CONTEXT_TERMS = [
    "insulation",
    "partition type",
    "exterior wall",
    "assembly",
    "wall type",
    "wall section",
    "building section",
]


def score_page(page: PageRecord, config: dict[str, Any] | None = None) -> PageRecord:
    config = config or load_keyword_config()
    foam = config.get("foam_keywords") or {}
    text = f"{page.sheet_title}\n{page.text}"
    specific_hits = keyword_hits(text, FOAM_SPECIFIC_TERMS)
    high_hits = keyword_hits(text, foam.get("high") or [])
    medium_hits = [hit for hit in keyword_hits(text, foam.get("medium") or []) if hit != "insulation"]
    context_hits = keyword_hits(text, foam.get("context") or [])
    generic_hits = [hit for hit in keyword_hits(text, GENERIC_CONTEXT_TERMS) if hit not in specific_hits]
    score = len(high_hits) * 6 + len(specific_hits) * 4 + len(medium_hits) * 2 + min(len(context_hits), 3)
    if page.sheet_title and keyword_hits(page.sheet_title, foam.get("high") or []):
        score += 4
    page.relevance_score = float(score)
    if high_hits or score >= 10:
        page.relevance_level = "high"
        page.foam_seed_level = "high"
    elif specific_hits:
        page.relevance_level = "medium"
        page.foam_seed_level = "high"
    elif generic_hits:
        page.relevance_level = "low"
        page.foam_seed_level = "generic_only"
    else:
        page.relevance_level = "low"
        page.foam_seed_level = "none"
    page.foam_specific_evidence = list(dict.fromkeys((high_hits + specific_hits + medium_hits)[:8]))
    page.generic_evidence = list(dict.fromkeys(generic_hits[:8]))
    page.evidence = page.foam_specific_evidence + [f"generic: {hit}" for hit in page.generic_evidence[:4]]
    page.role = classify_role(page, config)
    return page


def classify_role(page: PageRecord, config: dict[str, Any] | None = None) -> str:
    config = config or load_keyword_config()
    title = (page.sheet_title or "").lower()
    text = f"{page.sheet_title}\n{page.text}".lower()
    sheet_id = (page.canonical_sheet_id or page.sheet_id or page.sheet_number or "").upper()
    if any(term in text for term in ("addendum", "asi ", "architect supplemental instruction", "bulletin", "revision")):
        return "addendum_or_override"
    if any(term in text for term in ("07 21 00", "section 07", "specification", "project manual")) and page.foam_seed_level in {
        "high",
        "candidate",
    }:
        return "spec_definition"
    if any(term in title for term in ("wall type", "partition type", "assembly", "schedule")) or any(
        term in text for term in ("wall type schedule", "partition schedule", "wall schedule")
    ) or sheet_id.startswith(("A0-", "A7-", "A8-", "A9-")):
        return "wall_type_schedule"
    if "floor plan" in text or "overall plan" in text or "enlarged plan" in text or sheet_id.startswith("A2-"):
        return "floor_plan"
    if "roof plan" in text or (sheet_id.startswith("A3-") and "section" not in text):
        return "roof_plan"
    if "elevation" in title or "exterior elevation" in text or sheet_id.startswith(("A4-", "A5-")):
        return "elevation"
    if "window schedule" in text or "door schedule" in text or "opening" in text:
        return "height_or_opening_confirmation"
    if "section" in title or ("building section" in text or "wall section" in text) or sheet_id.startswith("A6-"):
        return "section_sheet"
    if "detail" in title or "detail" in text:
        return "detail_sheet"
    role_scores: dict[str, int] = {}
    for role, keywords in (config.get("role_keywords") or {}).items():
        role_scores[role] = len(keyword_hits(text, keywords or []))
    if role_scores:
        role, score = max(role_scores.items(), key=lambda item: (item[1], item[0]))
        if score > 0:
            return role
    if page.foam_seed_level == "generic_only":
        return "candidate_only"
    if page.relevance_score <= 0:
        return "unknown"
    return "assembly_definition" if page.relevance_score >= 4 else "candidate_only"


def classify_pages(pages: list[PageRecord], config_path: Path = DEFAULT_CONFIG) -> list[PageRecord]:
    config = load_keyword_config(config_path)
    for page in pages:
        score_page(page, config)
    return pages
