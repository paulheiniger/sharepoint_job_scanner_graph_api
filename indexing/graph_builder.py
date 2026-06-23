from __future__ import annotations

from collections import deque
from typing import Any

import networkx as nx

from ingest.pdf_ingest import PageRecord


def page_node_id(page: PageRecord) -> str:
    return page.global_page_id or page.sheet_number or f"page-{page.page_number}"


def build_reference_graph(pages: list[PageRecord]) -> nx.DiGraph:
    graph = nx.DiGraph()
    sheet_nodes: dict[str, list[str]] = {}
    for page in pages:
        if page.sheet_number and page.sheet_id_confidence >= 0.6:
            sheet_nodes.setdefault(page.sheet_number.upper().replace(".", "-"), []).append(page_node_id(page))
    warnings: list[str] = []
    for sheet_id, node_ids in sorted(sheet_nodes.items()):
        if len(node_ids) > 1:
            warnings.append(f"Duplicate sheet_id {sheet_id} found in {len(node_ids)} documents/pages; references are ambiguous.")

    for page in pages:
        node = page_node_id(page)
        graph.add_node(
            node,
            global_page_id=page.global_page_id,
            document_id=page.document_id,
            document_name=page.document_name,
            document_type=page.document_type,
            page_number=page.page_number,
            page_num=page.page_num,
            sheet_number=page.sheet_number,
            sheet_id=page.sheet_number,
            sheet_title=page.sheet_title,
            sheet_id_confidence=page.sheet_id_confidence,
            sheet_id_source=page.sheet_id_source,
            role=page.role,
            relevance_score=page.relevance_score,
            relevance_level=page.relevance_level,
            foam_seed_level=page.foam_seed_level,
            node_type="page",
        )
        for ref in page.references:
            target = ref.get("target") or ref.get("label")
            if not target:
                continue
            target = str(target).upper().replace(".", "-")
            if ref.get("type") in {"sheet", "detail_sheet"}:
                if ref.get("type") == "detail_sheet":
                    callout_node = f"detail::{ref.get('label') or target}"
                    graph.add_node(callout_node, node_type="detail_callout", label=ref.get("label"), sheet_number=target)
                    graph.add_edge(node, callout_node, label=ref.get("label"), ref_type=ref.get("type"), context=ref.get("context"))
                else:
                    callout_node = ""
                target_nodes = sheet_nodes.get(target, [])
                if not target_nodes:
                    graph.add_node(
                        f"unresolved_sheet::{target}",
                        sheet_number=target,
                        sheet_id=target,
                        node_type="unresolved_reference",
                        reference_only=True,
                        label=ref.get("label") or target,
                    )
                    graph.add_edge(
                        node,
                        f"unresolved_sheet::{target}",
                        label=ref.get("label") or target,
                        ref_type="unresolved_sheet",
                        context=ref.get("context"),
                    )
                    continue
                if len(target_nodes) > 1:
                    warnings.append(f"Reference {ref.get('label') or target} from {page.document_name} page {page.page_num} matches multiple sheets.")
                for target_node in target_nodes:
                    graph.add_edge(node, target_node, label=ref.get("label"), ref_type=ref.get("type"), context=ref.get("context"))
                    if callout_node:
                        graph.add_edge(callout_node, target_node, label=target, ref_type="detail_target", context=ref.get("context"))
            else:
                reference_node = f"{ref.get('type') or 'reference'}::{target}"
                graph.add_node(
                    reference_node,
                    sheet_number=target,
                    sheet_id=target,
                    node_type=ref.get("type") or "reference",
                    reference_only=True,
                    label=ref.get("label"),
                )
                graph.add_edge(node, reference_node, label=ref.get("label"), ref_type=ref.get("type"), context=ref.get("context"))
    graph.graph["warnings"] = sorted(set(warnings))
    return graph


def high_confidence_nodes(pages: list[PageRecord]) -> list[str]:
    return [page_node_id(page) for page in pages if page.relevance_level == "high"]


def foam_seed_nodes(pages: list[PageRecord]) -> list[str]:
    seeds: list[str] = []
    for page in pages:
        if page.foam_seed_level == "high" or page.role == "spec_definition":
            seeds.append(page_node_id(page))
    return seeds


def expand_neighbors(graph: nx.DiGraph, seed_nodes: list[str], *, depth: int = 2) -> set[str]:
    selected: set[str] = set(seed_nodes)
    queue = deque((node, 0) for node in seed_nodes if node in graph)
    while queue:
        node, distance = queue.popleft()
        if distance >= depth:
            continue
        neighbors = set(graph.successors(node)) | set(graph.predecessors(node))
        for neighbor in neighbors:
            if neighbor not in selected:
                selected.add(neighbor)
                queue.append((neighbor, distance + 1))
    return selected


def graph_edges_table(graph: nx.DiGraph) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source, target, data in graph.edges(data=True):
        source_data = graph.nodes.get(source, {})
        target_data = graph.nodes.get(target, {})
        rows.append(
            {
                "from": source,
                "from_document": source_data.get("document_name"),
                "from_sheet": source_data.get("sheet_number"),
                "to": target,
                "to_document": target_data.get("document_name"),
                "to_sheet": target_data.get("sheet_number"),
                "reference": data.get("label"),
                "type": data.get("ref_type"),
                "context": data.get("context"),
            }
        )
    return rows


def path_labels_to_seed(graph: nx.DiGraph, seed_nodes: list[str], target_node: str) -> list[str]:
    if target_node in seed_nodes:
        return [node_display_label(graph, target_node)]
    undirected = graph.to_undirected()
    best_path: list[str] | None = None
    for seed in seed_nodes:
        if seed not in undirected or target_node not in undirected:
            continue
        try:
            path = nx.shortest_path(undirected, seed, target_node)
        except nx.NetworkXNoPath:
            continue
        if best_path is None or len(path) < len(best_path):
            best_path = path
    return [node_display_label(graph, node) for node in best_path] if best_path else []


def node_display_label(graph: nx.DiGraph, node: str) -> str:
    data = graph.nodes.get(node, {})
    if data.get("node_type") == "page":
        return str(data.get("sheet_number") or data.get("sheet_title") or data.get("document_name") or node)
    return str(data.get("label") or data.get("sheet_number") or node.split("::", 1)[-1])
