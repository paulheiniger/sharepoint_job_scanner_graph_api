from __future__ import annotations

import json
import re
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
    """Evaluation mode: compare live BidScope predictions to completed takeoff labels.

    The completed takeoff is never used to boost, add, or reorder current-job
    predictions in this function.
    """
    foamscope = _load_foamscope_json(foamscope_json)
    expected_labels = parse_stack_takeoff_csv(takeoff_csv, project_id=project_id, trade_type=trade_type)
    expected_by_key = {label.match_key: label for label in expected_labels}
    expected_keys = set(expected_by_key)
    predicted_pages = _predicted_measurement_pages(foamscope, trade_type=trade_type)
    final_predictions = _final_predictions(predicted_pages, trade_type)

    matches: dict[str, dict[str, Any]] = {}
    matched_prediction_ids: set[int] = set()
    for key, expected in expected_by_key.items():
        prediction = _find_matching_prediction(expected, final_predictions)
        if prediction:
            matches[key] = prediction
            matched_prediction_ids.add(id(prediction))

    missed_keys = expected_keys - set(matches)
    extra_predictions = [prediction for prediction in final_predictions if id(prediction) not in matched_prediction_ids]
    return {
        "mode": "evaluation",
        "expected_measurement_pages": [expected_by_key[key].to_dict() for key in sorted(expected_keys)],
        "predicted_measurement_pages": final_predictions,
        "selected_measurement_pages": final_predictions,
        "top_predicted_measurement_pages": predicted_pages,
        "matched_pages": [_merge_match(expected_by_key[key], matches[key]) for key in sorted(matches)],
        "missed_pages": [_missed(expected_by_key[key]) for key in sorted(missed_keys)],
        "extra_pages": extra_predictions,
        "extra_selected_pages": extra_predictions,
        "recall": _ratio(len(matches), len(expected_keys)),
        "precision": _ratio(len(matches), len(final_predictions)),
        "precision_at_10": _precision_at_k(predicted_pages, expected_labels, 10),
        "precision_at_25": _precision_at_k(predicted_pages, expected_labels, 25),
        "precision_at_50": _precision_at_k(predicted_pages, expected_labels, 50),
        "counts": {
            "expected": len(expected_keys),
            "predicted": len(final_predictions),
            "selected": len(final_predictions),
            "matched": len(matches),
            "missed": len(missed_keys),
            "extra": len(extra_predictions),
        },
    }


def _predicted_measurement_pages(foamscope: dict[str, Any], *, trade_type: str = "foam_insulation") -> list[dict[str, Any]]:
    nodes = list((foamscope.get("measurement_tree") or {}).get("nodes") or [])
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
                "match_keys": _node_match_keys(node),
                "canonical_sheet_id": canonical_sheet_id,
                "original_page_number": original_page_number,
                "document_name": node.get("document_name"),
                "original_document_name": node.get("original_document_name"),
                "page_num": node.get("page_num"),
                "sheet_title": node.get("sheet_title"),
                "page_type": node.get("page_type"),
                "role": node.get("role"),
                "seed_evidence_score": node.get("seed_evidence_score", 0),
                "measurement_likelihood_score": node.get("measurement_likelihood_score", 0),
                "learned_measurement_prior_score": node.get("learned_measurement_prior_score", 0),
                "final_selection_score": score,
                "graph_distance_from_seed": node.get("graph_distance_from_seed"),
                "connected_seed_pages": node.get("connected_seed_pages") or [],
                "inclusion_path": node.get("inclusion_path") or [],
                "measurement_guidance": node.get("measurement_guidance"),
                "predicted_measurement_type": _prediction_type_from_node(node),
                "why_selected": _why_selected(node),
            }
        )
    return sorted(predictions, key=lambda row: (-(row.get("final_selection_score") or 0), row.get("match_key") or ""))


def _final_predictions(predictions: list[dict[str, Any]], trade_type: str) -> list[dict[str, Any]]:
    max_pages = 25 if trade_type == "foam_insulation" else 50
    return predictions[:max_pages]


def _find_matching_prediction(expected: TakeoffMeasurementLabel, predictions: list[dict[str, Any]]) -> dict[str, Any] | None:
    expected_keys = _label_match_keys(expected)
    for prediction in predictions:
        if set(prediction.get("match_keys") or []) & expected_keys:
            return prediction
    return None


def _label_match_keys(label: TakeoffMeasurementLabel) -> set[str]:
    keys = {label.match_key}
    if label.canonical_sheet_id:
        keys.add(f"sheet:{label.canonical_sheet_id}")
    if label.original_page_number is not None:
        keys.add(f"page:{label.original_page_number}")
    fuzzy = _plan_fuzzy_key(label.plan_name)
    if fuzzy:
        keys.add(f"plan:{fuzzy}")
    return keys


def _node_match_keys(node: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    canonical_sheet_id = str(node.get("canonical_sheet_id") or node.get("sheet_id") or "").strip()
    if canonical_sheet_id:
        keys.append(f"sheet:{canonical_sheet_id}")
    original_page_number = _int_or_none(node.get("original_page_number"))
    if original_page_number is not None:
        keys.append(f"page:{original_page_number}")
    for value in (node.get("document_name"), node.get("original_document_name")):
        fuzzy = _plan_fuzzy_key(str(value or ""))
        if fuzzy:
            keys.append(f"plan:{fuzzy}")
    return sorted(set(keys))


def _prediction_type_from_node(node: dict[str, Any]) -> str:
    page_type = str(node.get("page_type") or node.get("role") or "").lower()
    sheet_id = str(node.get("canonical_sheet_id") or node.get("sheet_id") or "").upper()
    title = f"{node.get('sheet_title') or ''} {node.get('measurement_guidance') or ''} {' '.join(node.get('inclusion_path') or [])}".lower()
    if "attic" in page_type or "attic" in title:
        return "attic_area"
    if "perimeter" in title or "ln ft" in title or "linear" in title:
        return "perimeter"
    if sheet_id.startswith(("A4-", "A5-")) or "elevation" in page_type or "elevation" in title:
        return "elevation_area"
    if sheet_id.startswith("A2-") or "floor_plan" in page_type:
        return "area"
    if "plan" in page_type:
        return "area"
    return "unknown"


def _why_selected(node: dict[str, Any]) -> str:
    pieces = [
        f"seed score {node.get('seed_evidence_score', 0)}",
        f"measurement likelihood {node.get('measurement_likelihood_score', 0)}",
    ]
    learned_prior = _number(node.get("learned_measurement_prior_score")) or 0.0
    if learned_prior:
        pieces.append(f"learned measurement prior {learned_prior:g}")
    path = node.get("inclusion_path") or []
    if path:
        pieces.append("path " + " -> ".join(str(part) for part in path))
    seeds = node.get("connected_seed_pages") or []
    if seeds:
        pieces.append("connected seed " + ", ".join(str(seed) for seed in seeds))
    return "; ".join(pieces)


def _merge_match(expected: TakeoffMeasurementLabel, predicted: dict[str, Any]) -> dict[str, Any]:
    out = expected.to_dict()
    match_keys = set(predicted.get("match_keys") or [])
    fuzzy_key = _plan_fuzzy_key(expected.plan_name)
    out.update(
        {
            "predicted_document_name": predicted.get("document_name"),
            "predicted_page_num": predicted.get("page_num"),
            "predicted_sheet_title": predicted.get("sheet_title"),
            "predicted_page_type": predicted.get("page_type"),
            "predicted_measurement_type": predicted.get("predicted_measurement_type"),
            "predicted_final_selection_score": predicted.get("final_selection_score"),
            "learned_measurement_prior_score": predicted.get("learned_measurement_prior_score"),
            "predicted_inclusion_path": predicted.get("inclusion_path"),
            "match_by_sheet_id": bool(expected.canonical_sheet_id and f"sheet:{expected.canonical_sheet_id}" in match_keys),
            "match_by_original_page_number": bool(expected.original_page_number is not None and f"page:{expected.original_page_number}" in match_keys),
            "match_by_plan_name_fuzzy": bool(fuzzy_key and f"plan:{fuzzy_key}" in match_keys),
            "actual_page_number_match": bool(
                expected.original_page_number is not None
                and predicted.get("original_page_number") == expected.original_page_number
            ),
            "why_selected": predicted.get("why_selected"),
        }
    )
    return out


def _missed(expected: TakeoffMeasurementLabel) -> dict[str, Any]:
    out = expected.to_dict()
    out["reason_missed"] = "No live predicted page matched sheet id, original page number, or normalized plan name."
    return out


def _precision_at_k(predictions: list[dict[str, Any]], expected_labels: list[TakeoffMeasurementLabel], k: int) -> float:
    top = predictions[:k]
    if not top:
        return 0.0
    matched = 0
    for row in top:
        prediction_keys = set(row.get("match_keys") or [])
        if any(prediction_keys & _label_match_keys(label) for label in expected_labels):
            matched += 1
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


def _plan_fuzzy_key(value: str) -> str:
    text = Path(str(value or "")).name.lower()
    text = re.sub(r"\.pdf$", "", text)
    text = re.sub(r"\s+page\s+\d+\s*$", "", text)
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


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
