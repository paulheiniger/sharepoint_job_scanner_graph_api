from __future__ import annotations

import re
from typing import Any

from ingest.pdf_ingest import PageRecord


REFERENCE_PATTERNS = [
    ("detail_sheet", re.compile(r"\b(?:detail|section)?\s*(\d{1,2})\s*/\s*([A-Z]{1,3}[A-Z0-9]?[-.]?\d{1,4}(?:\.\d+)?)\b", re.I)),
    ("spec_section", re.compile(r"\b(?:division\s*)?(0?7(?:\s+\d{2}\s+\d{2}|[\s.-]*21[\s.-]*00))\b", re.I)),
    ("sheet", re.compile(r"\b([A-Z]{1,3}[A-Z0-9]?[-.]?\d{1,4}(?:\.\d+)?)\b", re.I)),
    ("wall_type", re.compile(r"\b(?:wall|partition)\s+type\s+([A-Z]?-?\d+[A-Z]?)\b", re.I)),
    ("partition_type", re.compile(r"\bP(?:artition)?[- ]?(\d+[A-Z]?)\b", re.I)),
]


def normalize_sheet(value: str) -> str:
    return value.upper().replace(".", "-").strip()


def extract_references(text: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for ref_type, pattern in REFERENCE_PATTERNS:
        for match in pattern.finditer(text or ""):
            if ref_type == "detail_sheet":
                target = normalize_sheet(match.group(2))
                label = f"{match.group(1)}/{target}"
            elif ref_type == "sheet":
                target = normalize_sheet(match.group(1))
                if re.match(r"^(?:W|WT)-?\d", target):
                    continue
                label = target
            elif ref_type == "spec_section":
                target = " ".join(match.group(1).replace(".", " ").replace("-", " ").split()).upper()
                label = target
            else:
                target = match.group(1).upper()
                label = match.group(0)
            key = (ref_type, label)
            if key in seen:
                continue
            seen.add(key)
            refs.append({"type": ref_type, "label": label, "target": target, "context": _context(text, match.start(), match.end())})
    return refs


def _context(text: str, start: int, end: int, width: int = 80) -> str:
    return " ".join((text or "")[max(0, start - width) : min(len(text or ""), end + width)].split())


def attach_references(pages: list[PageRecord]) -> list[PageRecord]:
    for page in pages:
        page.references = extract_references(page.text)
    return pages
