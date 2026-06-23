from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from ingest.pdf_ingest import PageRecord


DEFAULT_CONFIG = Path("configs/sheet_patterns.yaml")
ALLOWED_SHEET_PREFIXES = ("A", "S", "M", "P", "E", "FP", "FA", "C", "L", "G")


def load_sheet_patterns(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    if not path.exists():
        return {"sheet_number_patterns": [], "sheet_title_hints": [], "title_stopwords": []}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _normalize_sheet_id(value: str) -> str:
    cleaned = value.upper().replace(".", "-").strip()
    compact = re.match(r"^([A-Z]{1,3})(\d{3,4}(?:-\d+)?)$", cleaned)
    if compact and "-" not in cleaned:
        return f"{compact.group(1)}-{compact.group(2)}"
    return cleaned


def _known_sheet_prefix(value: str) -> bool:
    prefix = re.match(r"^([A-Z]+)", value.upper())
    return bool(prefix and prefix.group(1) in ALLOWED_SHEET_PREFIXES)


def _looks_like_real_sheet_id(value: str) -> bool:
    normalized = _normalize_sheet_id(value)
    return _known_sheet_prefix(normalized) and bool(
        re.match(r"^(?:FP|FA|[ASMEPCLG]\d?|[ASMEPCLG])-\d{2,4}(?:-\d+)?$", normalized)
        or re.match(r"^(?:FP|FA|[ASMEPCLG])\d{3,4}$", normalized.replace("-", ""))
    )


def detect_filename_sheet_id(document_name: str, source_path: str = "") -> str:
    candidates = [Path(document_name or "").name]
    if source_path:
        candidates.append(Path(str(source_path).split(":", 1)[-1]).name)
    for candidate in candidates:
        stem = Path(candidate).stem.strip()
        split_page_match = re.match(r"(?P<original>.+?\.pdf)\s+Page\s+\d+$", stem, flags=re.I)
        if split_page_match:
            stem = Path(split_page_match.group("original")).stem
        match = re.match(r"^(?P<prefix>FP|FA|[ASMEPCLG]\d?)[._ -](?P<number>\d{2,4})(?:\D.*)?$", stem, flags=re.I)
        if match:
            return f"{match.group('prefix').upper()}-{match.group('number')}"
        match = re.match(r"^(?P<prefix>FP|FA|[ASMEPCLG])(?P<number>\d{3,4})(?:\D.*)?$", stem, flags=re.I)
        if match:
            number = match.group("number")
            return f"{match.group('prefix').upper()}-{number}"
    return ""


def _sheet_id_confidence(value: str, line: str, line_number: int) -> tuple[float, str]:
    normalized = _normalize_sheet_id(value)
    if re.match(r"^[A-Z]{1,3}[A-Z0-9]?-\d{2,4}(?:-\d+)?$", normalized):
        if not _known_sheet_prefix(normalized):
            return 0.25, "unknown_prefix"
        return 0.95 if line_number <= 8 else 0.8, "title_block_or_header"
    if re.match(r"^[A-Z]{1,3}\d{3,4}$", value.upper()):
        if not _known_sheet_prefix(normalized):
            return 0.25, "unknown_prefix"
        return 0.85 if line_number <= 12 else 0.65, "compact_sheet_id"
    if re.match(r"^[A-Z]\d$", value.upper()):
        if re.search(r"\b(?:sheet|drawing|page)\b", line, flags=re.I) and line_number <= 8:
            return 0.65, "short_sheet_id_with_label"
        return 0.2, "short_ambiguous"
    return 0.45, "pattern_match"


def detect_sheet_number_with_metadata(text: str, config: dict[str, Any] | None = None) -> tuple[str, float, str, list[str]]:
    config = config or load_sheet_patterns()
    lines = (text or "").splitlines()[:40]
    matches: list[tuple[str, float, str]] = []
    uncertain: list[str] = []
    for pattern in config.get("sheet_number_patterns") or []:
        for line_number, line in enumerate(lines, start=1):
            for match in re.finditer(pattern, line, flags=re.I):
                raw_value = match.group(1)
                value = _normalize_sheet_id(raw_value)
                confidence, source = _sheet_id_confidence(raw_value, line, line_number)
                if confidence < 0.6:
                    uncertain.append(value)
                    continue
                if re.match(r"^(?:W|WT)-?\d", value):
                    continue
                if not _looks_like_real_sheet_id(value):
                    continue
                matches.append((value, confidence, source))
    if not matches:
        return "", 0.0, "", uncertain
    unique: dict[str, tuple[float, str]] = {}
    for value, confidence, source in matches:
        if value not in unique or confidence > unique[value][0]:
            unique[value] = (confidence, source)
    value, (confidence, source) = max(unique.items(), key=lambda item: (item[1][0], -len(item[0])))
    return value, confidence, source, uncertain


def detect_sheet_number(text: str, config: dict[str, Any] | None = None) -> str:
    sheet_number, confidence, _, _ = detect_sheet_number_with_metadata(text, config)
    return sheet_number if confidence >= 0.6 else ""


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
        filename_sheet_id = detect_filename_sheet_id(page.document_name, page.source_path)
        extracted_sheet_id, confidence, source, uncertain = detect_sheet_number_with_metadata(page.text, config)
        page.filename_sheet_id = filename_sheet_id
        page.extracted_sheet_id = extracted_sheet_id if confidence >= 0.6 else ""
        if filename_sheet_id and (confidence < 0.98 or extracted_sheet_id != filename_sheet_id):
            page.canonical_sheet_id = filename_sheet_id
            page.sheet_number = filename_sheet_id
            page.sheet_id_confidence = 0.97
            page.sheet_id_source = "filename"
        elif extracted_sheet_id and confidence >= 0.6:
            page.canonical_sheet_id = extracted_sheet_id
            page.sheet_number = extracted_sheet_id
            page.sheet_id_confidence = confidence
            page.sheet_id_source = source
        else:
            page.canonical_sheet_id = ""
            page.sheet_number = ""
            page.sheet_id_confidence = confidence
            page.sheet_id_source = source
        if uncertain:
            page.warnings.append(f"Untrusted sheet id candidates ignored: {', '.join(sorted(set(uncertain))[:6])}")
        page.sheet_title = detect_sheet_title(page.text, page.sheet_number, config)
    return pages
