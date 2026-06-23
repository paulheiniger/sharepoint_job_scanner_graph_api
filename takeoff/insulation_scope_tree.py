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
    "height_or_opening_confirmation",
    "detail_reference",
}


def relevant_pages_table(
    pages: list[PageRecord],
    selected_nodes: set[str] | None = None,
    graph: nx.DiGraph | None = None,
    seed_nodes: list[str] | None = None,
) -> list[dict[str, Any]]:
    selected_nodes = selected_nodes or {page_node_id(page) for page in pages if page.relevance_level in {"high", "medium"}}
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
                "page_type": page.page_type,
                "page_num": page.page_num,
                "page_number": page.page_number,
                "sheet_id": page.sheet_id,
                "sheet_number": page.sheet_number,
                "sheet_title": page.sheet_title,
                "role": page.role,
                "foam_relevance": page.foam_relevance,
                "relevance_level": page.relevance_level,
                "relevance_score": page.relevance_score,
                "evidence": ", ".join(page.evidence),
                "inclusion_path": " -> ".join(inclusion_path),
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
) -> dict[str, Any]:
    seed_nodes = seed_nodes or []
    page_by_node = {page_node_id(page): page for page in pages}
    high_confidence = [node for node in selected_nodes if node in page_by_node and page_by_node[node].relevance_level == "high"]
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
        tree_nodes.append(
            {
                "node_id": node,
                "global_page_id": page.global_page_id,
                "document_name": page.document_name,
                "document_type": page.document_type,
                "page_type": page.page_type,
                "page_num": page.page_num,
                "page_number": page.page_number,
                "sheet_id": page.sheet_id,
                "sheet_number": page.sheet_number,
                "sheet_title": page.sheet_title,
                "role": page.role,
                "relevance_score": page.relevance_score,
                "evidence": page.evidence,
                "inclusion_path": path_labels_to_seed(graph, seed_nodes, node),
                "outgoing_references": outgoing,
                "incoming_references": incoming,
                "measurement_guidance": measurement_guidance(page),
            }
        )
    return {
        "prototype": "FoamScope AI",
        "disclaimer": "Estimator-reviewed measurement map only. This does not calculate a final bid.",
        "high_confidence_scope_nodes": high_confidence,
        "selected_node_count": len(selected_nodes),
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


def measurement_guidance(page: PageRecord) -> str:
    role = page.role
    if role == "spec_definition":
        return "Review foam type, R-value, air/vapor barrier, and product requirements."
    if role == "assembly_definition":
        return "Use this sheet to determine which assemblies receive spray foam insulation."
    if role == "measurement_page":
        return "Measure affected wall/roof/ceiling surface areas from this sheet."
    if role == "height_or_opening_confirmation":
        return "Use to confirm wall heights, openings, parapets, and deductions."
    if role == "detail_reference":
        return "Review detail for transitions, edges, penetrations, and unusual geometry."
    return "No measurement action suggested."
