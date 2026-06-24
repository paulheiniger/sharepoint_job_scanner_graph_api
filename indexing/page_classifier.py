from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ingest.pdf_ingest import PageRecord
from indexing.trade_profiles import load_trade_profile


DEFAULT_CONFIG = Path("configs/foam_keywords.yaml")


def load_keyword_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    if not path.exists():
        return {"foam_keywords": {}, "role_keywords": {}}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def keyword_hits(text: str, keywords: list[str]) -> list[str]:
    lowered = (text or "").lower()
    return [keyword for keyword in keywords if keyword.lower() in lowered]


def score_page(page: PageRecord, config: dict[str, Any] | None = None, trade_profile: dict[str, Any] | None = None) -> PageRecord:
    config = config or load_keyword_config()
    trade_profile = trade_profile or load_trade_profile()
    text = f"{page.sheet_title}\n{page.text}"
    high_keywords = trade_profile.get("high_confidence_seed_keywords") or []
    generic_keywords = trade_profile.get("generic_keywords") or []
    high_hits = keyword_hits(text, high_keywords)
    generic_hits = [hit for hit in keyword_hits(text, generic_keywords) if hit not in high_hits]
    score = len(high_hits) * 8 + min(len(generic_hits), 4)
    if page.sheet_title and keyword_hits(page.sheet_title, high_keywords):
        score += 4
    page.trade_type = str(trade_profile.get("trade_type") or "foam_insulation")
    page.trade_name = str(trade_profile.get("trade_name") or "Foam Insulation")
    page.relevance_score = float(score)
    page.seed_evidence_score = float(score)
    if high_hits:
        page.relevance_level = "high"
        page.foam_seed_level = "high"
    elif generic_hits:
        page.relevance_level = "low"
        page.foam_seed_level = "generic_only"
    else:
        page.relevance_level = "low"
        page.foam_seed_level = "none"
    page.foam_specific_evidence = list(dict.fromkeys(high_hits[:8]))
    page.generic_evidence = list(dict.fromkeys(generic_hits[:8]))
    page.evidence = page.foam_specific_evidence + [f"generic: {hit}" for hit in page.generic_evidence[:4]]
    page.page_type = classify_role(page, config, trade_profile)
    page.role = page.page_type
    page.measurement_likelihood_score = 0.0
    page.final_selection_score = page.seed_evidence_score
    return page


def classify_role(page: PageRecord, config: dict[str, Any] | None = None, trade_profile: dict[str, Any] | None = None) -> str:
    config = config or load_keyword_config()
    trade_profile = trade_profile or load_trade_profile()
    title = (page.sheet_title or "").lower()
    text = f"{page.sheet_title}\n{page.text}".lower()
    sheet_id = (page.canonical_sheet_id or page.sheet_id or page.sheet_number or "").upper()
    if sheet_id.startswith(("AD-", "AD")):
        return "addendum_or_override"
    if sheet_id.startswith("A2-"):
        return "attic_plan" if "attic" in text else "floor_plan"
    if sheet_id.startswith("A4-"):
        return "elevation"
    if sheet_id.startswith("A5-"):
        return "section_sheet" if "section" in text else "elevation"
    if "attic" in text and page.original_page_number is not None:
        return "attic_plan"
    if sheet_id.startswith("A6-"):
        if "detail" in title or "detail" in text:
            return "detail_reference" if page.foam_seed_level == "high" else "detail_sheet"
        return "assembly_definition" if page.foam_seed_level == "high" else "section_sheet"
    if sheet_id.startswith("A9-"):
        return "detail_reference" if page.foam_seed_level == "high" else "detail_sheet"
    if sheet_id.startswith(("C", "E", "M", "P", "FP", "L", "T", "EL", "EP")):
        if page.foam_seed_level == "high" and any(term in text for term in ("foam", "spray foam", "thermal insulation", "r-value", "air barrier", "vapor barrier")):
            return "detail_reference" if "detail" in text else "candidate_only"
        return "unknown"
    if sheet_id.startswith("A0-"):
        if any(term in text for term in ("specification", "project manual", "section 07", "07 21 00", "07 54 00", "07 56 00")) and page.foam_seed_level == "high":
            return "spec_definition"
        if any(term in title for term in ("wall type", "partition type", "assembly", "schedule")) or any(
            term in text for term in ("wall type schedule", "partition schedule", "wall schedule")
        ):
            return "wall_type_schedule"
        return "general_notes"
    if any(term in text for term in ("addendum", "asi ", "architect supplemental instruction", "bulletin", "revision")) and not sheet_id.startswith("A"):
        return "addendum_or_override"
    if any(term in text for term in ("specification", "project manual", "section 07", "07 21 00", "07 54 00", "07 56 00")) and page.foam_seed_level == "high":
        return "spec_definition"
    if page.foam_seed_level == "high" and ("detail" in title or "detail" in text):
        return "detail_reference"
    if page.foam_seed_level == "high" and (sheet_id.startswith("A6-") or "wall section" in text or "building section" in text):
        return "assembly_definition"
    if any(term in title for term in ("wall type", "partition type", "assembly", "schedule")) or any(
        term in text for term in ("wall type schedule", "partition schedule", "wall schedule")
    ) or sheet_id.startswith(("A0-", "A7-", "A8-", "A9-")):
        return "wall_type_schedule"
    if "attic" in text:
        return "attic_plan"
    if "ceiling plan" in text or "reflected ceiling plan" in text:
        return "ceiling_plan"
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


def classify_pages(
    pages: list[PageRecord],
    config_path: Path = DEFAULT_CONFIG,
    *,
    trade_type: str = "foam_insulation",
) -> list[PageRecord]:
    config = load_keyword_config(config_path)
    trade_profile = load_trade_profile(trade_type)
    for page in pages:
        score_page(page, config, trade_profile)
    return pages
