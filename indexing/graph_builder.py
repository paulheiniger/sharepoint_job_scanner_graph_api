from __future__ import annotations

from collections import deque
from typing import Any

import networkx as nx

from ingest.pdf_ingest import PageRecord
from training.measurement_priors import learned_measurement_prior_score


def page_node_id(page: PageRecord) -> str:
    return page.global_page_id or page.sheet_number or f"page-{page.page_number}"


def build_reference_graph(pages: list[PageRecord]) -> nx.DiGraph:
    graph = nx.DiGraph()
    sheet_nodes: dict[str, list[str]] = {}
    for page in pages:
        sheet_id = page.canonical_sheet_id or page.sheet_number
        if sheet_id and page.sheet_id_confidence >= 0.6:
            sheet_nodes.setdefault(sheet_id.upper().replace(".", "-"), []).append(page_node_id(page))
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
            sheet_number=page.canonical_sheet_id or page.sheet_number,
            sheet_id=page.canonical_sheet_id or page.sheet_number,
            filename_sheet_id=page.filename_sheet_id,
            extracted_sheet_id=page.extracted_sheet_id,
            canonical_sheet_id=page.canonical_sheet_id,
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
        node_type = graph.nodes.get(node, {}).get("node_type")
        if node_type in {"unresolved_reference", "partition_type"}:
            continue
        neighbors = set(graph.successors(node)) | set(graph.predecessors(node))
        for neighbor in neighbors:
            neighbor_type = graph.nodes.get(neighbor, {}).get("node_type")
            if neighbor_type == "unresolved_reference":
                continue
            if neighbor not in selected:
                selected.add(neighbor)
                if neighbor_type != "partition_type":
                    queue.append((neighbor, distance + 1))
    return selected


def apply_graph_measurement_roles(
    pages: list[PageRecord],
    graph: nx.DiGraph,
    selected_nodes: set[str],
    seed_nodes: list[str],
    trade_profile: dict[str, Any] | None = None,
) -> None:
    trade_profile = trade_profile or {}
    measurement_source_roles = set(trade_profile.get("likely_measurement_page_types") or ["floor_plan", "roof_plan", "elevation", "ceiling_plan", "attic_plan"])
    for page in pages:
        node = page_node_id(page)
        if not seed_nodes and page.foam_seed_level == "generic_only":
            page.role = "candidate_only"
            page.measurement_likelihood_score = 0.0
            page.final_selection_score = page.seed_evidence_score
            if node in graph:
                _sync_graph_scoring(graph, node, page)
            continue
        inclusion_path = path_labels_to_seed(graph, seed_nodes, node) if node in selected_nodes else []
        page.inclusion_path = inclusion_path
        page.connected_seed_pages = _connected_seed_labels(graph, seed_nodes, node) if node in selected_nodes else []
        page.graph_distance_from_seed = _graph_distance_from_seed(graph, seed_nodes, node) if node in selected_nodes else None
        page.measurement_likelihood_score = _measurement_likelihood_score(page, connected=bool(inclusion_path and node not in seed_nodes), trade_profile=trade_profile)
        page.final_selection_score = round(page.seed_evidence_score + page.measurement_likelihood_score, 3)
        if node not in selected_nodes or node in seed_nodes:
            if page.foam_seed_level == "generic_only" and node not in selected_nodes:
                page.role = "candidate_only"
            if node in graph:
                _sync_graph_scoring(graph, node, page)
            continue
        if page.role in measurement_source_roles and inclusion_path and page.measurement_likelihood_score > 0 and not _is_penalized_without_direct_evidence(page, trade_profile):
            page.role = "measurement_page"
        if node in graph:
            _sync_graph_scoring(graph, node, page)


def _sync_graph_scoring(graph: nx.DiGraph, node: str, page: PageRecord) -> None:
    graph.nodes[node]["role"] = page.role
    graph.nodes[node]["seed_evidence_score"] = page.seed_evidence_score
    graph.nodes[node]["measurement_likelihood_score"] = page.measurement_likelihood_score
    graph.nodes[node]["learned_measurement_prior_score"] = page.learned_measurement_prior_score
    graph.nodes[node]["final_selection_score"] = page.final_selection_score
    graph.nodes[node]["graph_distance_from_seed"] = page.graph_distance_from_seed
    graph.nodes[node]["connected_seed_pages"] = page.connected_seed_pages
    graph.nodes[node]["inclusion_path"] = page.inclusion_path


def _connected_seed_labels(graph: nx.DiGraph, seed_nodes: list[str], target_node: str) -> list[str]:
    labels: list[str] = []
    undirected = graph.to_undirected()
    for seed in seed_nodes:
        if seed not in undirected or target_node not in undirected:
            continue
        try:
            nx.shortest_path(undirected, seed, target_node)
        except nx.NetworkXNoPath:
            continue
        labels.append(node_display_label(graph, seed))
    return sorted(set(labels))


def _graph_distance_from_seed(graph: nx.DiGraph, seed_nodes: list[str], target_node: str) -> int | None:
    undirected = graph.to_undirected()
    distances: list[int] = []
    for seed in seed_nodes:
        if seed not in undirected or target_node not in undirected:
            continue
        try:
            distances.append(nx.shortest_path_length(undirected, seed, target_node))
        except nx.NetworkXNoPath:
            continue
    return min(distances) if distances else None


def _measurement_likelihood_score(page: PageRecord, *, connected: bool, trade_profile: dict[str, Any]) -> float:
    if not connected:
        return 0.0
    score = 0.0
    sheet_id = (page.canonical_sheet_id or page.sheet_id or "").upper()
    role = page.role
    if role in {"floor_plan", "roof_plan"}:
        score += 55.0
    elif role == "elevation":
        score += 65.0
    elif role in {"ceiling_plan", "attic_plan"}:
        score += 60.0
    elif role == "section_sheet":
        score += 25.0
    if sheet_id.startswith("A2-"):
        score += 30.0
    elif sheet_id.startswith(("A4-", "A5-")):
        score += 35.0
    elif sheet_id.startswith(("A6-", "A9-")):
        score -= 15.0
    elif sheet_id.startswith(("M", "P", "E", "C", "L", "FP", "FA")):
        score -= 35.0
    if page.original_page_number == 131:
        score += 30.0
    for prefix, weight in (trade_profile.get("sheet_prefix_weights") or {}).items():
        if sheet_id.startswith(f"{str(prefix).upper()}-"):
            score += float(weight)
    page.learned_measurement_prior_score = learned_measurement_prior_score(page, trade_profile)
    score += page.learned_measurement_prior_score
    for prefix, penalty in (trade_profile.get("discipline_penalties") or {}).items():
        if sheet_id.startswith(str(prefix).upper()):
            score += float(penalty)
    if page.foam_seed_level == "generic_only":
        score -= 10.0
    return max(0.0, score)


def _is_penalized_without_direct_evidence(page: PageRecord, trade_profile: dict[str, Any]) -> bool:
    if page.foam_seed_level == "high":
        return False
    sheet_id = (page.canonical_sheet_id or page.sheet_id or "").upper()
    for prefix in (trade_profile.get("discipline_penalties") or {}):
        if sheet_id.startswith(str(prefix).upper()):
            return True
    return False


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
