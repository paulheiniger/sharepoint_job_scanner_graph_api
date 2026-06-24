from __future__ import annotations

from typing import Any

import networkx as nx

from ingest.pdf_ingest import PageRecord
from indexing.graph_builder import page_node_id, path_labels_to_seed


MEASUREMENT_ROLES = {
    "measurement_page",
    "assembly_definition",
    "wall_type_schedule",
    "section_reference",
    "section_sheet",
    "height_or_opening_confirmation",
    "detail_reference",
    "detail_sheet",
    "elevation",
}


def relevant_pages_table(
    pages: list[PageRecord],
    selected_nodes: set[str] | None = None,
    graph: nx.DiGraph | None = None,
    seed_nodes: list[str] | None = None,
) -> list[dict[str, Any]]:
    if selected_nodes is None:
        selected_nodes = {page_node_id(page) for page in pages if page.relevance_level in {"high", "medium"}}
    rows: list[dict[str, Any]] = []
    for page in pages:
        node = page_node_id(page)
        if node not in selected_nodes and page.relevance_level == "low":
            continue
        inclusion_path = path_labels_to_seed(graph, seed_nodes or [], node) if graph is not None else []
        rows.append(
            {
                "document_name": page.document_name,
                "global_page_id": page.global_page_id,
                "document_type": page.document_type,
                "original_document_name": page.original_document_name,
                "original_page_number": page.original_page_number,
                "page_type": page.page_type,
                "page_num": page.page_num,
                "page_number": page.page_number,
                "sheet_id": page.sheet_id,
                "sheet_number": page.sheet_number,
                "filename_sheet_id": page.filename_sheet_id,
                "extracted_sheet_id": page.extracted_sheet_id,
                "canonical_sheet_id": page.canonical_sheet_id,
                "sheet_title": page.sheet_title,
                "sheet_id_confidence": page.sheet_id_confidence,
                "sheet_id_source": page.sheet_id_source,
                "role": page.role,
                "foam_seed_level": page.foam_seed_level,
                "foam_relevance": page.foam_relevance,
                "relevance_level": page.relevance_level,
                "relevance_score": page.relevance_score,
                "foam_specific_evidence": ", ".join(page.foam_specific_evidence),
                "generic_evidence": ", ".join(page.generic_evidence),
                "evidence": ", ".join(page.evidence),
                "inclusion_path": " -> ".join(inclusion_path),
                "seed_evidence_score": page.seed_evidence_score,
                "measurement_likelihood_score": page.measurement_likelihood_score,
                "final_selection_score": page.final_selection_score,
                "graph_distance_from_seed": page.graph_distance_from_seed,
                "connected_seed_pages": ", ".join(page.connected_seed_pages),
                "references": ", ".join(ref.get("label", "") for ref in page.references[:12]),
                "needs_measurement": page.role in MEASUREMENT_ROLES or page.relevance_level == "high",
                "used_ocr": page.used_ocr,
                "warnings": "; ".join(page.warnings),
            }
        )
    return rows


def build_measurement_tree(
    pages: list[PageRecord],
    graph: nx.DiGraph,
    selected_nodes: set[str],
    seed_nodes: list[str] | None = None,
    trade_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    seed_nodes = seed_nodes or []
    trade_profile = trade_profile or {}
    page_by_node = {page_node_id(page): page for page in pages}
    high_confidence = [
        node
        for node in selected_nodes
        if node in page_by_node and page_by_node[node].foam_seed_level == "high"
    ]
    generic_candidates = [
        page_node_id(page)
        for page in pages
        if page.foam_seed_level == "generic_only" and page_node_id(page) not in high_confidence
    ]
    tree_nodes: list[dict[str, Any]] = []
    for node in sorted(selected_nodes):
        page = page_by_node.get(node)
        if not page:
            continue
        outgoing = [
            {
                "target": target,
                "target_document": graph.nodes.get(target, {}).get("document_name"),
                "target_sheet": graph.nodes.get(target, {}).get("sheet_number"),
                "label": data.get("label"),
                "type": data.get("ref_type"),
            }
            for _, target, data in graph.out_edges(node, data=True)
            if target in selected_nodes
        ]
        incoming = [
            {
                "source": source,
                "source_document": graph.nodes.get(source, {}).get("document_name"),
                "source_sheet": graph.nodes.get(source, {}).get("sheet_number"),
                "label": data.get("label"),
                "type": data.get("ref_type"),
            }
            for source, _, data in graph.in_edges(node, data=True)
            if source in selected_nodes
        ]
        inclusion_path = path_labels_to_seed(graph, seed_nodes, node)
        tree_nodes.append(
            {
                "node_id": node,
                "global_page_id": page.global_page_id,
                "document_name": page.document_name,
                "document_type": page.document_type,
                "original_document_name": page.original_document_name,
                "original_page_number": page.original_page_number,
                "page_type": page.page_type,
                "page_num": page.page_num,
                "page_number": page.page_number,
                "sheet_id": page.sheet_id,
                "sheet_number": page.sheet_number,
                "filename_sheet_id": page.filename_sheet_id,
                "extracted_sheet_id": page.extracted_sheet_id,
                "canonical_sheet_id": page.canonical_sheet_id,
                "sheet_title": page.sheet_title,
                "sheet_id_confidence": page.sheet_id_confidence,
                "sheet_id_source": page.sheet_id_source,
                "role": page.role,
                "foam_seed_level": page.foam_seed_level,
                "relevance_score": page.relevance_score,
                "foam_specific_evidence": page.foam_specific_evidence,
                "generic_evidence": page.generic_evidence,
                "evidence": page.evidence,
                "inclusion_path": inclusion_path,
                "seed_evidence_score": page.seed_evidence_score,
                "measurement_likelihood_score": page.measurement_likelihood_score,
                "final_selection_score": page.final_selection_score,
                "graph_distance_from_seed": page.graph_distance_from_seed,
                "connected_seed_pages": page.connected_seed_pages,
                "outgoing_references": outgoing,
                "incoming_references": incoming,
                "measurement_guidance": measurement_guidance(page, inclusion_path, trade_profile),
            }
        )
    return {
        "prototype": "BidScope AI",
        "trade_type": trade_profile.get("trade_type", page_by_node[next(iter(page_by_node))].trade_type if page_by_node else "foam_insulation"),
        "trade_name": trade_profile.get("trade_name", page_by_node[next(iter(page_by_node))].trade_name if page_by_node else "Foam Insulation"),
        "disclaimer": "Estimator-reviewed measurement map only. This does not calculate a final bid.",
        "high_confidence_scope_nodes": high_confidence,
        "selected_node_count": len(tree_nodes),
        "selected_node_count_internal": len(selected_nodes),
        "exported_node_count": len(tree_nodes),
        "export_note": "Only selected page nodes are exported in measurement_tree.nodes; reference-only nodes remain in reference_graph.",
        "seed_guidance": (
            _missing_seed_guidance(trade_profile)
            if not high_confidence and generic_candidates
            else ""
        ),
        "generic_candidate_nodes": generic_candidates,
        "spec_definition_pages": [
            node for node in selected_nodes if node in page_by_node and page_by_node[node].role == "spec_definition"
        ],
        "drawing_measurement_pages": [
            node
            for node in selected_nodes
            if node in page_by_node and page_by_node[node].role in MEASUREMENT_ROLES and page_by_node[node].role != "spec_definition"
        ],
        "nodes": tree_nodes,
    }


def _missing_seed_guidance(trade_profile: dict[str, Any]) -> str:
    if str(trade_profile.get("trade_type") or "").lower() == "foam_insulation":
        return "No foam-specific scope seed found. Candidate insulation pages found only."
    return f"No high-confidence {trade_profile.get('trade_name', 'trade')} scope seed found. Candidate context pages found only."


def measurement_guidance(page: PageRecord, inclusion_path: list[str] | None = None, trade_profile: dict[str, Any] | None = None) -> str:
    trade_profile = trade_profile or {}
    role = page.role
    path_text = " -> ".join(inclusion_path or [])
    if role == "spec_definition":
        return f"Review {trade_profile.get('trade_name', 'trade')} specification requirements and scope exclusions."
    if role == "assembly_definition":
        return f"Use this sheet to determine which assemblies receive {trade_profile.get('trade_name', 'trade')} scope."
    if role == "measurement_page":
        sheet = page.sheet_id or page.sheet_title or page.document_name
        templates = trade_profile.get("output_guidance_templates") or {}
        if path_text:
            template = templates.get("measurement_page") or "Measure connected assembly/wall type on {sheet}, because path is {path}."
            return template.format(sheet=sheet, path=path_text)
        template = templates.get("unresolved_measurement") or "Candidate measurement page; assembly not resolved."
        return template.format(sheet=sheet, path=path_text)
    if role == "height_or_opening_confirmation":
        return "Use to confirm wall heights, openings, parapets, and deductions."
    if role in {"detail_reference", "detail_sheet"}:
        return "Review detail for transitions, edges, penetrations, and unusual geometry."
    if role == "elevation":
        return "Use to confirm exterior heights, openings, and wall deductions."
    if role == "candidate_only":
        return "Candidate context only; do not measure unless tied to a high-confidence scope path."
    return "No measurement action suggested."
