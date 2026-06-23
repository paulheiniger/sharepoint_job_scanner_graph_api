from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from ingest.pdf_ingest import PageRecord


DEFAULT_CONFIG = Path("configs/sheet_patterns.yaml")


def load_sheet_patterns(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    if not path.exists():
        return {"sheet_number_patterns": [], "sheet_title_hints": [], "title_stopwords": []}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def detect_sheet_number(text: str, config: dict[str, Any] | None = None) -> str:
    config = config or load_sheet_patterns()
    search_text = "\n".join((text or "").splitlines()[:30])
    matches: list[str] = []
    for pattern in config.get("sheet_number_patterns") or []:
        for match in re.finditer(pattern, search_text, flags=re.I):
            value = match.group(1).upper().replace(".", "-")
            if any(char.isdigit() for char in value):
                matches.append(value)
    if not matches:
        return ""
    return sorted(set(matches), key=lambda value: (len(value), value))[0]


def detect_sheet_title(text: str, sheet_number: str = "", config: dict[str, Any] | None = None) -> str:
    config = config or load_sheet_patterns()
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    stopwords = set(config.get("title_stopwords") or [])
    hints = tuple(str(hint).lower() for hint in config.get("sheet_title_hints") or [])
    candidates: list[str] = []
    for line in lines[:50]:
        lowered = line.lower()
        if sheet_number and sheet_number.lower() in lowered:
            remainder = re.sub(re.escape(sheet_number), "", line, flags=re.I).strip(" -:\t")
            if remainder:
                candidates.append(remainder)
        if any(hint in lowered for hint in hints):
            candidates.append(line)
    for candidate in candidates:
        words = [word for word in candidate.split() if word.lower().strip(":") not in stopwords]
        cleaned = " ".join(words).strip(" -:")
        if 3 <= len(cleaned) <= 80:
            return cleaned
    return lines[0][:80] if lines else ""


def index_sheets(pages: list[PageRecord], config_path: Path = DEFAULT_CONFIG) -> list[PageRecord]:
    config = load_sheet_patterns(config_path)
    for page in pages:
        page.sheet_number = detect_sheet_number(page.text, config)
        page.sheet_title = detect_sheet_title(page.text, page.sheet_number, config)
    return pages
