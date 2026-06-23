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


def score_page(page: PageRecord, config: dict[str, Any] | None = None) -> PageRecord:
    config = config or load_keyword_config()
    foam = config.get("foam_keywords") or {}
    high_hits = keyword_hits(page.text, foam.get("high") or [])
    medium_hits = keyword_hits(page.text, foam.get("medium") or [])
    context_hits = keyword_hits(page.text, foam.get("context") or [])
    score = len(high_hits) * 5 + len(medium_hits) * 3 + len(context_hits)
    if page.sheet_title and keyword_hits(page.sheet_title, foam.get("high") or []):
        score += 4
    page.relevance_score = float(score)
    page.relevance_level = "high" if score >= 9 else "medium" if score >= 4 else "low"
    page.evidence = high_hits[:6] + medium_hits[:6] + context_hits[:6]
    page.role = classify_role(page, config)
    return page


def classify_role(page: PageRecord, config: dict[str, Any] | None = None) -> str:
    config = config or load_keyword_config()
    text = f"{page.sheet_title}\n{page.text}".lower()
    if page.relevance_score <= 0:
        return "irrelevant"
    role_scores: dict[str, int] = {}
    for role, keywords in (config.get("role_keywords") or {}).items():
        role_scores[role] = len(keyword_hits(text, keywords or []))
    if role_scores:
        role, score = max(role_scores.items(), key=lambda item: (item[1], item[0]))
        if score > 0:
            return role
    if "plan" in text:
        return "measurement_page"
    if "section" in text or "detail" in text:
        return "detail_reference"
    return "assembly_definition" if page.relevance_score >= 4 else "irrelevant"


def classify_pages(pages: list[PageRecord], config_path: Path = DEFAULT_CONFIG) -> list[PageRecord]:
    config = load_keyword_config(config_path)
    for page in pages:
        score_page(page, config)
    return pages
