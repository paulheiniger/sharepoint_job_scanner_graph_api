from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

SUPPORTING_ESTIMATE_TERMS = ("ir scan", "stamp", "inspection", "aerial", "test", "addendum")


def slugify_estimate_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").upper()
    return re.sub(r"-+", "-", cleaned)[:100] or "ESTIMATE"


def estimate_id(job_id: str, estimate_file: str | Path) -> str:
    name = Path(str(estimate_file)).name
    digest = hashlib.sha1(f"{job_id}|{name}".encode("utf-8")).hexdigest()[:8]
    return f"{job_id}-{slugify_estimate_name(Path(name).stem)}-{digest}"


def infer_estimate_scope_type(path: str | Path, text: str = "") -> str:
    source = f"{Path(str(path)).stem} {text}".lower()
    if "ir scan" in source or re.search(r"\bir\b", source):
        return "IR Scan"
    if "stamp" in source:
        return "STAMP"
    if "coated polyurethane foam roof" in source:
        return "Coated Polyurethane Foam Roof"
    if "repair" in source:
        return "Repair"
    if "roof" in source:
        return "Roofing"
    return "Unknown"


def is_likely_supporting_estimate(path: str | Path) -> bool:
    name = Path(str(path)).name.lower()
    return any(term in name for term in SUPPORTING_ESTIMATE_TERMS)


def select_primary_estimate(estimates: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    if not estimates:
        return None, "No estimate workbooks found"
    if len(estimates) == 1:
        return estimates[0], "Only estimate workbook found"

    candidates = [estimate for estimate in estimates if not is_likely_supporting_estimate(estimate.get("path") or estimate.get("estimate_file") or "")]
    if candidates:
        pool = candidates
        reason_prefix = "Excluded likely supporting estimates; "
    else:
        pool = estimates
        reason_prefix = "All estimates looked supporting or no primary candidate was obvious; "

    for key, reason in [
        ("final_price", "selected largest final_price"),
        ("estimated_duration_days", "selected largest estimated_duration_days"),
        ("estimated_labor_hours", "selected largest estimated_labor_hours"),
    ]:
        with_values = [estimate for estimate in pool if _numeric(estimate.get(key)) is not None]
        if with_values:
            selected = sorted(with_values, key=lambda item: (_numeric(item.get(key)) or 0, _name(item)), reverse=True)[0]
            return selected, reason_prefix + reason

    return sorted(pool, key=_name)[0], reason_prefix + "selected first alphabetically"


def _numeric(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except ValueError:
        return None


def _name(estimate: dict[str, Any]) -> str:
    return Path(str(estimate.get("path") or estimate.get("estimate_file") or "")).name.lower()
