from __future__ import annotations

import csv
import io
import json
import zipfile
from typing import Any


EXPECTED_EXPORT_FILES = [
    "run_summary.json",
    "input_manifest.csv",
    "trade_profile_used.json",
    "seed_pages.csv",
    "measurement_candidates.csv",
    "selected_pages.csv",
    "rejected_pages_sample.csv",
    "reference_paths.csv",
    "unresolved_references.csv",
    "warnings.json",
    "chatgpt_review_prompt.txt",
]


def build_bidscope_review_export_zip(
    export_payload: dict[str, Any],
    *,
    trade_profile: dict[str, Any],
    project_name: str = "",
    source_type: str = "",
    package_name: str = "",
    takeoff_evaluation: dict[str, Any] | None = None,
) -> bytes:
    """Build a small review bundle with only JSON/CSV/text artifacts."""
    run_summary = build_run_summary(
        export_payload,
        project_name=project_name,
        source_type=source_type,
        package_name=package_name,
        takeoff_evaluation=takeoff_evaluation,
    )
    files: dict[str, bytes] = {
        "run_summary.json": _json_bytes(run_summary),
        "input_manifest.csv": _csv_bytes(_input_manifest_rows(export_payload), _input_manifest_columns()),
        "trade_profile_used.json": _json_bytes(trade_profile),
        "seed_pages.csv": _csv_bytes(_seed_page_rows(export_payload), _seed_page_columns()),
        "measurement_candidates.csv": _csv_bytes(_measurement_candidate_rows(export_payload), _measurement_candidate_columns()),
        "selected_pages.csv": _csv_bytes(_selected_page_rows(export_payload, trade_profile=trade_profile), _selected_page_columns()),
        "rejected_pages_sample.csv": _csv_bytes(_rejected_page_rows(export_payload), _rejected_page_columns()),
        "reference_paths.csv": _csv_bytes(_reference_path_rows(export_payload), _reference_path_columns()),
        "unresolved_references.csv": _csv_bytes(_unresolved_reference_rows(export_payload), _unresolved_reference_columns()),
        "warnings.json": _json_bytes({"warnings": export_payload.get("warnings") or [], "reference_graph_warnings": _reference_graph(export_payload).get("warnings") or []}),
        "chatgpt_review_prompt.txt": _review_prompt(run_summary).encode("utf-8"),
    }
    if takeoff_evaluation:
        files["takeoff_eval.csv"] = _csv_bytes(_takeoff_eval_rows(takeoff_evaluation), _takeoff_eval_columns())

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def build_run_summary(
    export_payload: dict[str, Any],
    *,
    project_name: str = "",
    source_type: str = "",
    package_name: str = "",
    takeoff_evaluation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scan = export_payload.get("scan_completeness") or {}
    progress = export_payload.get("progress") or {}
    tree = export_payload.get("measurement_tree") or {}
    nodes = tree.get("nodes") or []
    measurement_nodes = [node for node in nodes if _selection_tier(node) == "likely_measurement_pages"]
    return {
        "project_name": project_name,
        "trade_type": export_payload.get("trade_type") or scan.get("trade_type"),
        "analysis_mode": export_payload.get("analysis_mode") or scan.get("analysis_mode"),
        "source_type": source_type,
        "package_name": package_name,
        "total_documents": scan.get("total_documents_discovered", progress.get("pdf_count", len(export_payload.get("documents") or []))),
        "total_pages_discovered": scan.get("total_pages_discovered", progress.get("estimated_total_pages")),
        "pages_sampled": scan.get("total_pages_sampled", progress.get("fast_scanned_pages")),
        "pages_lightly_indexed": scan.get("total_pages_lightly_indexed"),
        "pages_deep_analyzed": scan.get("total_pages_deep_analyzed", progress.get("deep_analyzed_pages")),
        "processing_budget_hit": scan.get("processing_budget_hit", False),
        "budget_hit_reason": scan.get("budget_hit_reason", ""),
        "high_confidence_seed_count": scan.get("high_confidence_seed_count", len(tree.get("high_confidence_scope_nodes") or [])),
        "generic_candidate_count": scan.get("generic_candidate_count"),
        "selected_node_count": tree.get("selected_node_count"),
        "selected_node_count_internal": export_payload.get("selected_node_count_internal", tree.get("selected_node_count_internal")),
        "exported_node_count": export_payload.get("exported_node_count", tree.get("exported_node_count")),
        "selected_measurement_page_count": len(measurement_nodes),
        "resolved_reference_count": scan.get("resolved_reference_count"),
        "unresolved_reference_count": scan.get("unresolved_reference_count"),
        "measurement_pages_with_resolved_paths": scan.get("measurement_pages_with_resolved_paths", sum(1 for node in measurement_nodes if node.get("inclusion_path"))),
        "measurement_pages_without_resolved_paths": scan.get(
            "measurement_pages_without_resolved_paths",
            sum(1 for node in measurement_nodes if not node.get("inclusion_path")),
        ),
        "takeoff_eval_recall": (takeoff_evaluation or {}).get("recall"),
        "takeoff_eval_precision": (takeoff_evaluation or {}).get("precision"),
        "takeoff_eval_precision_at_10": (takeoff_evaluation or {}).get("precision_at_10"),
        "takeoff_eval_precision_at_25": (takeoff_evaluation or {}).get("precision_at_25"),
        "takeoff_eval_precision_at_50": (takeoff_evaluation or {}).get("precision_at_50"),
        "warnings_count": len(export_payload.get("warnings") or []) + len(_reference_graph(export_payload).get("warnings") or []),
    }


def _input_manifest_rows(export_payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in export_payload.get("manifest") or []]


def _seed_page_rows(export_payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for node in _nodes(export_payload):
        if str(node.get("foam_seed_level") or "") != "high" and not _number(node.get("seed_evidence_score")):
            continue
        evidence = _join(node.get("foam_specific_evidence") or node.get("evidence") or [])
        rows.append(
            {
                "page_id": node.get("global_page_id") or node.get("node_id"),
                "document_name": node.get("document_name"),
                "original_document_name": node.get("original_document_name"),
                "original_page_number": node.get("original_page_number"),
                "canonical_sheet_id": node.get("canonical_sheet_id"),
                "page_type": node.get("page_type"),
                "seed_evidence_score": node.get("seed_evidence_score"),
                "foam_seed_level": node.get("foam_seed_level"),
                "foam_specific_evidence": _join(node.get("foam_specific_evidence") or []),
                "generic_evidence": _join(node.get("generic_evidence") or []),
                "why_selected": f"seed evidence: {evidence}" if evidence else f"seed score {node.get('seed_evidence_score')}",
            }
        )
    return rows


def _measurement_candidate_rows(export_payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = [
        node
        for node in _nodes(export_payload)
        if _number(node.get("measurement_likelihood_score")) or node.get("role") == "measurement_page"
    ]
    candidates = sorted(candidates, key=lambda node: -_selection_score(node))
    rows = []
    for index, node in enumerate(candidates, start=1):
        path = node.get("inclusion_path") or []
        rows.append(
            {
                "rank": index,
                "page_id": node.get("global_page_id") or node.get("node_id"),
                "document_name": node.get("document_name"),
                "canonical_sheet_id": node.get("canonical_sheet_id"),
                "original_page_number": node.get("original_page_number"),
                "page_type": node.get("page_type"),
                "measurement_likelihood_score": node.get("measurement_likelihood_score"),
                "seed_evidence_score": node.get("seed_evidence_score"),
                "learned_measurement_prior_score": node.get("learned_measurement_prior_score", 0),
                "final_selection_score": _selection_score(node),
                "graph_distance_from_seed": node.get("graph_distance_from_seed"),
                "connected_seed_pages": _join(node.get("connected_seed_pages") or []),
                "best_reference_path": _join(path, separator=" -> "),
                "predicted_measurement_type": _predicted_measurement_type(node),
                "why_candidate": _why_candidate(node),
            }
        )
    return rows


def _selected_page_rows(
    export_payload: dict[str, Any],
    *,
    trade_profile: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = []
    nodes = sorted(_nodes(export_payload), key=lambda node: -_selection_score(node))
    max_measurements = int(trade_profile.get("max_final_measurement_pages") or 50)
    measurement_count = 0
    for node in nodes:
        tier = _selection_tier(node)
        if tier == "debug_only_connected_pages":
            continue
        if tier == "likely_measurement_pages":
            if measurement_count >= max_measurements:
                continue
            measurement_count += 1
        rows.append(
            {
                "page_id": node.get("global_page_id") or node.get("node_id"),
                "canonical_sheet_id": node.get("canonical_sheet_id"),
                "document_name": node.get("document_name"),
                "original_page_number": node.get("original_page_number"),
                "page_type": node.get("page_type"),
                "role": node.get("role"),
                "final_selection_score": _selection_score(node),
                "measurement_likelihood_score": node.get("measurement_likelihood_score"),
                "seed_evidence_score": node.get("seed_evidence_score"),
                "learned_measurement_prior_score": node.get("learned_measurement_prior_score", 0),
                "reference_path": _join(node.get("inclusion_path") or [], separator=" -> "),
                "measurement_guidance": node.get("measurement_guidance"),
                "selection_tier": tier,
            }
        )
    return rows


def _rejected_page_rows(export_payload: dict[str, Any], *, limit: int = 200) -> list[dict[str, Any]]:
    selected = {str(node.get("global_page_id") or node.get("node_id")) for node in _nodes(export_payload) if _selection_tier(node) != "debug_only_connected_pages"}
    rows = []
    for node in _nodes(export_payload):
        if _selection_tier(node) != "debug_only_connected_pages":
            continue
        rows.append(
            {
                "page_id": node.get("global_page_id") or node.get("node_id"),
                "document_name": node.get("document_name"),
                "canonical_sheet_id": node.get("canonical_sheet_id"),
                "page_type": node.get("page_type"),
                "role": node.get("role"),
                "seed_evidence_score": node.get("seed_evidence_score"),
                "measurement_likelihood_score": node.get("measurement_likelihood_score"),
                "final_selection_score": node.get("final_selection_score"),
                "reason_rejected": "connected for debugging/reference only; low measurement likelihood or penalized discipline",
            }
        )
        if len(rows) >= limit:
            return rows
    for page in export_payload.get("pages") or []:
        page_id = str(page.get("global_page_id") or "")
        if page_id in selected:
            continue
        rows.append(
            {
                "page_id": page_id,
                "document_name": page.get("document_name"),
                "canonical_sheet_id": page.get("canonical_sheet_id"),
                "page_type": page.get("page_type") or page.get("role"),
                "role": page.get("role"),
                "seed_evidence_score": page.get("seed_evidence_score"),
                "measurement_likelihood_score": page.get("measurement_likelihood_score"),
                "final_selection_score": page.get("final_selection_score"),
                "reason_rejected": "not connected to selected seed/reference subgraph",
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _reference_path_rows(export_payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for index, node in enumerate(_nodes(export_payload), start=1):
        if node.get("role") != "measurement_page":
            continue
        path = list(node.get("inclusion_path") or [])
        if not path:
            continue
        seed = path[0] if path else ""
        hops = path[1:-1]
        rows.append(
            {
                "path_id": f"path-{index}",
                "seed_sheet": seed,
                "seed_page_type": _seed_page_type(export_payload, seed),
                "seed_evidence": _seed_evidence(export_payload, seed),
                "hop_1": hops[0] if len(hops) > 0 else "",
                "hop_2": hops[1] if len(hops) > 1 else "",
                "hop_3": hops[2] if len(hops) > 2 else "",
                "measurement_sheet": node.get("canonical_sheet_id") or node.get("sheet_id"),
                "measurement_page_type": node.get("page_type"),
                "path_confidence": _path_confidence(node),
            }
        )
    return rows


def _unresolved_reference_rows(export_payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for edge in _reference_graph(export_payload).get("edges") or []:
        if "unresolved" not in str(edge.get("type") or ""):
            continue
        rows.append(
            {
                "source_sheet": edge.get("from_sheet"),
                "source_page": edge.get("from_document"),
                "raw_reference": edge.get("reference"),
                "reference_type": edge.get("type"),
                "reason_unresolved": "no matching trusted sheet id in sheet map",
                "was_used_for_expansion": False,
            }
        )
    return rows


def _takeoff_eval_rows(takeoff_evaluation: dict[str, Any]) -> list[dict[str, Any]]:
    predictions = {}
    ranked_keys = []
    rank_by_key = {}
    for index, row in enumerate(takeoff_evaluation.get("top_predicted_measurement_pages") or [], start=1):
        keys = list(row.get("match_keys") or [])
        if row.get("match_key"):
            keys.append(row["match_key"])
            ranked_keys.append(row["match_key"])
        for key in keys:
            predictions.setdefault(key, row)
            rank_by_key.setdefault(key, index)
    matched_by_key = {row.get("match_key"): row for row in takeoff_evaluation.get("matched_pages") or []}
    rows = []
    for actual in takeoff_evaluation.get("expected_measurement_pages") or []:
        key = actual.get("match_key")
        predicted = predictions.get(key) or {}
        matched = matched_by_key.get(key) or {}
        rows.append(
            {
                "actual_plan_name": actual.get("plan_name"),
                "actual_sheet_id": actual.get("canonical_sheet_id"),
                "takeoff_name": actual.get("takeoff_name"),
                "quantity": actual.get("quantity"),
                "unit": actual.get("unit"),
                "predicted_rank": rank_by_key.get(key, ""),
                "was_selected": bool(predicted),
                "match_type": "matched" if matched else "missed",
                "match_by_sheet_id": matched.get("match_by_sheet_id", False),
                "match_by_original_page_number": matched.get("match_by_original_page_number", False),
                "match_by_plan_name_fuzzy": matched.get("match_by_plan_name_fuzzy", False),
                "actual_page_number_match": matched.get("actual_page_number_match", False),
                "predicted_page_type": predicted.get("page_type"),
                "predicted_measurement_type": predicted.get("predicted_measurement_type"),
                "reason_missed": "" if matched else actual.get("reason_missed", "No predicted page matched."),
            }
        )
    for extra in takeoff_evaluation.get("extra_pages") or takeoff_evaluation.get("extra_selected_pages") or []:
        rows.append(
            {
                "actual_plan_name": "",
                "actual_sheet_id": extra.get("canonical_sheet_id"),
                "takeoff_name": "",
                "quantity": "",
                "unit": "",
                "predicted_rank": ranked_keys.index(extra.get("match_key")) + 1 if extra.get("match_key") in ranked_keys else "",
                "was_selected": True,
                "match_type": "extra_selected",
                "match_by_sheet_id": False,
                "match_by_original_page_number": False,
                "match_by_plan_name_fuzzy": False,
                "actual_page_number_match": extra.get("actual_page_number_match", False),
                "predicted_page_type": extra.get("page_type"),
                "predicted_measurement_type": extra.get("predicted_measurement_type"),
                "reason_missed": "",
            }
        )
    return rows


def _review_prompt(summary: dict[str, Any]) -> str:
    return (
        "You are reviewing a BidScope AI measurement-map run.\n\n"
        "Use the attached CSV/JSON files to identify why relevant measurement pages were selected or missed. "
        "Do not assume the bid is complete. Recommend tuning changes to trade keywords, sheet classification, "
        "reference expansion, measurement likelihood scoring, and STACK takeoff evaluation.\n\n"
        f"Project: {summary.get('project_name') or 'unknown'}\n"
        f"Trade: {summary.get('trade_type')}\n"
        f"Analysis mode: {summary.get('analysis_mode')}\n"
        f"Documents: {summary.get('total_documents')}\n"
        f"Pages discovered/sample/deep analyzed: {summary.get('total_pages_discovered')} / "
        f"{summary.get('pages_sampled')} / {summary.get('pages_deep_analyzed')}\n"
        f"High-confidence seeds: {summary.get('high_confidence_seed_count')}\n"
        f"Selected measurement pages: {summary.get('selected_measurement_page_count')}\n"
        f"Takeoff recall/precision: {summary.get('takeoff_eval_recall')} / {summary.get('takeoff_eval_precision')}\n\n"
        "Please return: top false positives, likely false negatives, unresolved reference issues, and concrete rule/profile changes."
    )


def _nodes(export_payload: dict[str, Any]) -> list[dict[str, Any]]:
    return list((export_payload.get("measurement_tree") or {}).get("nodes") or [])


def _reference_graph(export_payload: dict[str, Any]) -> dict[str, Any]:
    return export_payload.get("reference_graph") or {}


def _csv_bytes(rows: list[dict[str, Any]], columns: list[str]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({column: _cell(row.get(column)) for column in columns})
    return buffer.getvalue().encode("utf-8")


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, indent=2, sort_keys=True, default=str).encode("utf-8")


def _cell(value: Any) -> Any:
    if isinstance(value, (list, tuple, set)):
        return _join(list(value))
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, default=str)
    return "" if value is None else value


def _join(values: Any, *, separator: str = "; ") -> str:
    if isinstance(values, str):
        return values
    if not values:
        return ""
    return separator.join(str(value) for value in values)


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _predicted_measurement_type(node: dict[str, Any]) -> str:
    page_type = str(node.get("page_type") or node.get("role") or "").lower()
    sheet_id = str(node.get("canonical_sheet_id") or node.get("sheet_id") or "").upper()
    title = f"{node.get('sheet_title') or ''} {node.get('measurement_guidance') or ''} {_join(node.get('inclusion_path') or [])}".lower()
    if "attic" in page_type or "attic" in title:
        return "attic_area"
    if "perimeter" in title or "ln ft" in title or "edge" in title:
        return "perimeter"
    if sheet_id.startswith(("A4-", "A5-")) or "elevation" in page_type or "elevation" in title:
        return "elevation_area"
    if sheet_id.startswith("A2-") or "floor_plan" in page_type:
        return "area"
    if "elevation" in page_type or "elevation" in title:
        return "elevation_area"
    if "roof" in page_type or "roof" in title:
        return "area"
    if "attic" in page_type or "attic" in title:
        return "attic_area"
    if "plan" in page_type:
        return "area"
    return "unknown"


def _selection_score(node: dict[str, Any]) -> float:
    return _number(node.get("final_selection_score")) or 0.0


def _selection_tier(
    node: dict[str, Any],
) -> str:
    role = str(node.get("role") or "")
    sheet_id = str(node.get("canonical_sheet_id") or node.get("sheet_id") or "").upper()
    seed_score = _number(node.get("seed_evidence_score")) or 0.0
    measurement_score = _number(node.get("measurement_likelihood_score")) or 0.0
    if role == "measurement_page" and measurement_score >= 50 and not _penalized_sheet(sheet_id, seed_score):
        return "likely_measurement_pages"
    if str(node.get("foam_seed_level") or "") == "high" or role in {"spec_definition"}:
        return "seed_pages"
    if role in {"assembly_definition", "detail_reference", "section_sheet", "wall_type_schedule"} and (seed_score > 0 or measurement_score >= 20):
        return "supporting_reference_pages"
    return "debug_only_connected_pages"


def _penalized_sheet(sheet_id: str, seed_score: float) -> bool:
    if seed_score > 0:
        return False
    return sheet_id.startswith(("C", "E", "M", "P", "FP", "L", "T", "EL", "EP"))


def _why_candidate(node: dict[str, Any]) -> str:
    pieces = [
        f"measurement likelihood {node.get('measurement_likelihood_score')}",
        f"seed evidence {node.get('seed_evidence_score')}",
    ]
    path = _join(node.get("inclusion_path") or [], separator=" -> ")
    if path:
        pieces.append(f"path {path}")
    return "; ".join(pieces)


def _path_confidence(node: dict[str, Any]) -> str:
    if not (node.get("canonical_sheet_id") or node.get("sheet_id")):
        return "low"
    if node.get("role") != "measurement_page":
        return "low"
    if node.get("graph_distance_from_seed") in (None, ""):
        return "low"
    distance = int(node.get("graph_distance_from_seed") or 0)
    if distance <= 2:
        return "high"
    if distance <= 4:
        return "medium"
    return "low"


def _seed_page_type(export_payload: dict[str, Any], seed_label: str) -> str:
    seed = _find_node_by_label(export_payload, seed_label)
    return str((seed or {}).get("page_type") or (seed or {}).get("role") or "")


def _seed_evidence(export_payload: dict[str, Any], seed_label: str) -> str:
    seed = _find_node_by_label(export_payload, seed_label)
    return _join((seed or {}).get("foam_specific_evidence") or (seed or {}).get("evidence") or [])


def _find_node_by_label(export_payload: dict[str, Any], label: str) -> dict[str, Any] | None:
    for node in _nodes(export_payload):
        labels = {
            str(node.get("canonical_sheet_id") or ""),
            str(node.get("sheet_id") or ""),
            str(node.get("sheet_title") or ""),
            str(node.get("document_name") or ""),
        }
        if label in labels:
            return node
    return None


def _input_manifest_columns() -> list[str]:
    return ["candidate_id", "document_name", "source_path", "priority", "compressed_size", "uncompressed_size", "status"]


def _seed_page_columns() -> list[str]:
    return [
        "page_id",
        "document_name",
        "original_document_name",
        "original_page_number",
        "canonical_sheet_id",
        "page_type",
        "seed_evidence_score",
        "foam_seed_level",
        "foam_specific_evidence",
        "generic_evidence",
        "why_selected",
    ]


def _measurement_candidate_columns() -> list[str]:
    return [
        "rank",
        "page_id",
        "document_name",
        "canonical_sheet_id",
        "original_page_number",
        "page_type",
        "measurement_likelihood_score",
        "seed_evidence_score",
        "learned_measurement_prior_score",
        "final_selection_score",
        "graph_distance_from_seed",
        "connected_seed_pages",
        "best_reference_path",
        "predicted_measurement_type",
        "why_candidate",
    ]


def _selected_page_columns() -> list[str]:
    return [
        "page_id",
        "canonical_sheet_id",
        "document_name",
        "original_page_number",
        "page_type",
        "role",
        "final_selection_score",
        "measurement_likelihood_score",
        "seed_evidence_score",
        "learned_measurement_prior_score",
        "reference_path",
        "measurement_guidance",
        "selection_tier",
    ]


def _rejected_page_columns() -> list[str]:
    return [
        "page_id",
        "document_name",
        "canonical_sheet_id",
        "page_type",
        "role",
        "seed_evidence_score",
        "measurement_likelihood_score",
        "final_selection_score",
        "reason_rejected",
    ]


def _reference_path_columns() -> list[str]:
    return [
        "path_id",
        "seed_sheet",
        "seed_page_type",
        "seed_evidence",
        "hop_1",
        "hop_2",
        "hop_3",
        "measurement_sheet",
        "measurement_page_type",
        "path_confidence",
    ]


def _unresolved_reference_columns() -> list[str]:
    return ["source_sheet", "source_page", "raw_reference", "reference_type", "reason_unresolved", "was_used_for_expansion"]


def _takeoff_eval_columns() -> list[str]:
    return [
        "actual_plan_name",
        "actual_sheet_id",
        "takeoff_name",
        "quantity",
        "unit",
        "predicted_rank",
        "was_selected",
        "match_type",
        "match_by_sheet_id",
        "match_by_original_page_number",
        "match_by_plan_name_fuzzy",
        "actual_page_number_match",
        "predicted_page_type",
        "predicted_measurement_type",
        "reason_missed",
    ]
