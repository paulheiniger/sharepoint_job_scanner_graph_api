from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from training.completed_takeoff_parser import TakeoffMeasurementLabel, parse_stack_takeoff_csv


def compare_foamscope_output_to_takeoff_export(
    foamscope_json: dict[str, Any] | str | bytes | Path,
    takeoff_csv: str | bytes | Path,
    *,
    project_id: str = "",
    trade_type: str = "foam_insulation",
) -> dict[str, Any]:
    foamscope = _load_foamscope_json(foamscope_json)
    expected_labels = parse_stack_takeoff_csv(takeoff_csv, project_id=project_id, trade_type=trade_type)
    expected_by_key = {label.match_key: label for label in expected_labels}
    predicted_pages = _predicted_measurement_pages(foamscope)
    predicted_by_key = {page["match_key"]: page for page in predicted_pages if page.get("match_key")}

    expected_keys = set(expected_by_key)
    predicted_keys = set(predicted_by_key)
    matched_keys = expected_keys & predicted_keys
    missed_keys = expected_keys - predicted_keys
    extra_keys = predicted_keys - expected_keys
    return {
        "expected_measurement_pages": [expected_by_key[key].to_dict() for key in sorted(expected_keys)],
        "predicted_measurement_pages": [predicted_by_key[key] for key in sorted(predicted_keys)],
        "selected_measurement_pages": [predicted_by_key[key] for key in sorted(predicted_keys)],
        "top_predicted_measurement_pages": predicted_pages,
        "matched_pages": [_merge_match(expected_by_key[key], predicted_by_key[key]) for key in sorted(matched_keys)],
        "missed_pages": [expected_by_key[key].to_dict() for key in sorted(missed_keys)],
        "extra_pages": [predicted_by_key[key] for key in sorted(extra_keys)],
        "extra_selected_pages": [predicted_by_key[key] for key in sorted(extra_keys)],
        "recall": _ratio(len(matched_keys), len(expected_keys)),
        "precision": _ratio(len(matched_keys), len(predicted_keys)),
        "precision_at_10": _precision_at_k(predicted_pages, expected_keys, 10),
        "precision_at_25": _precision_at_k(predicted_pages, expected_keys, 25),
        "precision_at_50": _precision_at_k(predicted_pages, expected_keys, 50),
        "counts": {
            "expected": len(expected_keys),
            "predicted": len(predicted_keys),
            "selected": len(predicted_keys),
            "matched": len(matched_keys),
            "missed": len(missed_keys),
            "extra": len(extra_keys),
        },
    }


def _predicted_measurement_pages(foamscope: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = (foamscope.get("measurement_tree") or {}).get("nodes") or []
    predictions: list[dict[str, Any]] = []
    for node in nodes:
        score = _number(node.get("final_selection_score")) or 0.0
        likelihood = _number(node.get("measurement_likelihood_score")) or 0.0
        if node.get("role") != "measurement_page" and likelihood <= 0:
            continue
        canonical_sheet_id = str(node.get("canonical_sheet_id") or node.get("sheet_id") or "").strip()
        original_page_number = _int_or_none(node.get("original_page_number"))
        match_key = f"sheet:{canonical_sheet_id}" if canonical_sheet_id else f"page:{original_page_number}" if original_page_number is not None else ""
        predictions.append(
            {
                "match_key": match_key,
                "canonical_sheet_id": canonical_sheet_id,
                "original_page_number": original_page_number,
                "document_name": node.get("document_name"),
                "original_document_name": node.get("original_document_name"),
                "page_num": node.get("page_num"),
                "sheet_title": node.get("sheet_title"),
                "role": node.get("role"),
                "seed_evidence_score": node.get("seed_evidence_score", 0),
                "measurement_likelihood_score": node.get("measurement_likelihood_score", 0),
                "final_selection_score": score,
                "graph_distance_from_seed": node.get("graph_distance_from_seed"),
                "connected_seed_pages": node.get("connected_seed_pages") or [],
                "inclusion_path": node.get("inclusion_path") or [],
                "measurement_guidance": node.get("measurement_guidance"),
                "why_selected": _why_selected(node),
            }
        )
    return sorted(predictions, key=lambda row: (-(row.get("final_selection_score") or 0), row.get("match_key") or ""))


def _why_selected(node: dict[str, Any]) -> str:
    pieces = [
        f"seed score {node.get('seed_evidence_score', 0)}",
        f"measurement prior {node.get('measurement_likelihood_score', 0)}",
    ]
    path = node.get("inclusion_path") or []
    if path:
        pieces.append("path " + " -> ".join(str(part) for part in path))
    seeds = node.get("connected_seed_pages") or []
    if seeds:
        pieces.append("connected seed " + ", ".join(str(seed) for seed in seeds))
    return "; ".join(pieces)


def _merge_match(expected: TakeoffMeasurementLabel, predicted: dict[str, Any]) -> dict[str, Any]:
    out = expected.to_dict()
    out.update(
        {
            "predicted_document_name": predicted.get("document_name"),
            "predicted_page_num": predicted.get("page_num"),
            "predicted_sheet_title": predicted.get("sheet_title"),
            "predicted_final_selection_score": predicted.get("final_selection_score"),
            "predicted_inclusion_path": predicted.get("inclusion_path"),
            "why_selected": predicted.get("why_selected"),
        }
    )
    return out


def _precision_at_k(predictions: list[dict[str, Any]], expected_keys: set[str], k: int) -> float:
    top = [row for row in predictions[:k] if row.get("match_key")]
    if not top:
        return 0.0
    matched = sum(1 for row in top if row["match_key"] in expected_keys)
    return matched / len(top)


def _load_foamscope_json(payload: dict[str, Any] | str | bytes | Path) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, bytes):
        return json.loads(payload.decode("utf-8"))
    if isinstance(payload, Path):
        return json.loads(payload.read_text(encoding="utf-8"))
    text = str(payload)
    if text.lstrip().startswith(("{", "[")):
        return json.loads(text)
    path = Path(text)
    return json.loads(path.read_text(encoding="utf-8"))


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def _int_or_none(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0
