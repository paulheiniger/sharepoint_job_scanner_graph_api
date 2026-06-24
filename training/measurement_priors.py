from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from training.completed_takeoff_parser import TakeoffMeasurementLabel, parse_stack_takeoff_csv


MODEL_VERSION = 1


def build_learned_measurement_priors(
    takeoff_csvs: Iterable[str | bytes | Path],
    *,
    trade_type: str = "foam_insulation",
) -> dict[str, Any]:
    """Aggregate completed takeoffs into reusable live-bid measurement priors."""
    labels: list[TakeoffMeasurementLabel] = []
    for index, takeoff_csv in enumerate(takeoff_csvs, start=1):
        labels.extend(parse_stack_takeoff_csv(takeoff_csv, project_id=f"training-{index}", trade_type=trade_type))

    sheet_prefix_counts: dict[str, int] = {}
    page_type_counts: dict[str, int] = {}
    measurement_type_counts: dict[str, int] = {}
    relationship_counts: dict[str, int] = {}
    for label in labels:
        prefix = sheet_prefix_from_label(label)
        page_type = page_type_from_label(label)
        measurement_type = label.measurement_type or "unknown"
        relationship = f"{page_type}:{measurement_type}"
        _increment(sheet_prefix_counts, prefix)
        _increment(page_type_counts, page_type)
        _increment(measurement_type_counts, measurement_type)
        _increment(relationship_counts, relationship)

    return {
        "model_version": MODEL_VERSION,
        "mode": "training",
        "trade_type": trade_type,
        "total_measurements": len(labels),
        "sheet_prefix_counts": dict(sorted(sheet_prefix_counts.items())),
        "page_type_counts": dict(sorted(page_type_counts.items())),
        "measurement_type_counts": dict(sorted(measurement_type_counts.items())),
        "graph_relationship_counts": dict(sorted(relationship_counts.items())),
        "sheet_prefix_prior_scores": _score_counts(sheet_prefix_counts),
        "page_type_prior_scores": _score_counts(page_type_counts),
        "measurement_type_prior_scores": _score_counts(measurement_type_counts),
    }


def load_learned_measurement_priors(path: Path | str) -> dict[str, Any]:
    model_path = Path(path)
    if not model_path.exists():
        return {}
    return json.loads(model_path.read_text(encoding="utf-8"))


def save_learned_measurement_priors(priors: dict[str, Any], path: Path | str) -> Path:
    model_path = Path(path)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_text(json.dumps(priors, indent=2, sort_keys=True), encoding="utf-8")
    return model_path


def learned_measurement_prior_score(page: Any, trade_profile: dict[str, Any]) -> float:
    priors = trade_profile.get("learned_measurement_priors") or {}
    if not priors:
        return 0.0
    sheet_id = str(getattr(page, "canonical_sheet_id", "") or getattr(page, "sheet_id", "") or "").upper()
    page_type = str(getattr(page, "role", "") or getattr(page, "page_type", "") or "").lower()
    prefix = sheet_prefix_from_sheet_id(sheet_id)
    score = 0.0
    score += _number((priors.get("sheet_prefix_prior_scores") or {}).get(prefix)) or 0.0
    score += _number((priors.get("page_type_prior_scores") or {}).get(page_type)) or 0.0
    return min(score, 60.0)


def sheet_prefix_from_label(label: TakeoffMeasurementLabel) -> str:
    return sheet_prefix_from_sheet_id(label.canonical_sheet_id or "")


def sheet_prefix_from_sheet_id(sheet_id: str) -> str:
    text = str(sheet_id or "").upper().replace(".", "-")
    if not text:
        return "original_page"
    return text.split("-", 1)[0]


def page_type_from_label(label: TakeoffMeasurementLabel) -> str:
    sheet = str(label.canonical_sheet_id or "").upper()
    text = f"{label.plan_name} {label.takeoff_name} {label.takeoff_description}".lower()
    if label.original_page_number is not None and not sheet:
        return "original_page"
    if "attic" in text:
        return "attic_plan"
    if sheet.startswith("A2-"):
        return "floor_plan"
    if sheet.startswith("A4-"):
        return "elevation"
    if sheet.startswith("A5-"):
        return "section_sheet"
    if sheet.startswith(("A6-", "A9-")):
        return "detail_reference"
    if sheet.startswith("A3-"):
        return "roof_plan"
    return "unknown"


def _score_counts(counts: dict[str, int]) -> dict[str, float]:
    if not counts:
        return {}
    max_count = max(counts.values())
    return {
        key: round(10.0 + 30.0 * (count / max_count), 3)
        for key, count in sorted(counts.items())
    }


def _increment(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
