from __future__ import annotations

from collections import deque
from typing import Any

import networkx as nx

from ingest.pdf_ingest import PageRecord


def page_node_id(page: PageRecord) -> str:
    return page.sheet_number or f"page-{page.page_number}"


def build_reference_graph(pages: list[PageRecord]) -> nx.DiGraph:
    graph = nx.DiGraph()
    known_sheets = {page.sheet_number: page for page in pages if page.sheet_number}
    for page in pages:
        node = page_node_id(page)
        graph.add_node(
            node,
            page_number=page.page_number,
            sheet_number=page.sheet_number,
            sheet_title=page.sheet_title,
            role=page.role,
            relevance_score=page.relevance_score,
            relevance_level=page.relevance_level,
        )
        for ref in page.references:
            target = ref.get("target") or ref.get("label")
            if not target:
                continue
            target = str(target).upper().replace(".", "-")
            if ref.get("type") in {"sheet", "detail_sheet"} and target not in known_sheets:
                continue
            graph.add_edge(node, target, label=ref.get("label"), ref_type=ref.get("type"), context=ref.get("context"))
    return graph


def high_confidence_nodes(pages: list[PageRecord]) -> list[str]:
    return [page_node_id(page) for page in pages if page.relevance_level == "high"]


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
    return [
        {
            "from": source,
            "to": target,
            "reference": data.get("label"),
            "type": data.get("ref_type"),
            "context": data.get("context"),
        }
        for source, target, data in graph.edges(data=True)
    ]
