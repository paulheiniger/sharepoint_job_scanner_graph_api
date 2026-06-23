from __future__ import annotations

import time
from dataclasses import asdict, dataclass
import hashlib
import pickle
from pathlib import Path
from typing import Any

from indexing.graph_builder import build_reference_graph, expand_neighbors, foam_seed_nodes, graph_edges_table, page_node_id
from indexing.page_classifier import classify_pages
from indexing.reference_extractor import attach_references
from indexing.sheet_indexer import index_sheets
from ingest.package_ingest import PackageInspectionResult, PdfCandidate, materialize_selected_documents
from ingest.pdf_ingest import PageRecord
from takeoff.insulation_scope_tree import build_measurement_tree, relevant_pages_table


@dataclass(frozen=True)
class ProgressiveBudgets:
    max_initial_sample_pages: int | None = 200
    max_light_index_pages: int | None = 500
    max_deep_analysis_pages: int = 150
    max_ocr_pages: int = 0
    max_runtime_seconds: int | None = 25
    include_low_priority_documents: bool = False
    full_lightweight_index: bool = False


_PROGRESSIVE_CACHE: dict[str, dict[str, Any]] = {}
CACHE_VERSION = "foamscope-progressive-v2"


def current_memory_rss_mb() -> float | None:
    try:
        import resource
    except Exception:
        return None
    try:
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except Exception:
        return None
    # macOS reports bytes, Linux reports KiB.
    return round(usage / (1024 * 1024), 2) if usage > 10_000_000 else round(usage / 1024, 2)


def candidate_priority(candidate: PdfCandidate) -> str:
    text = f"{candidate.document_name} {candidate.source_path}".lower().replace("\\", "/")
    high_terms = (
        "architectural",
        "/arch",
        " arch",
        "plans",
        "drawings",
        "spec",
        "specifications",
        "project manual",
        "addendum",
        "addenda",
        "bulletin",
        "asi",
        "envelope",
        "insulation",
    )
    medium_terms = ("structural", "roof")
    low_terms = ("electrical", "plumbing", "mechanical", "civil", "landscape", "fire alarm", "low voltage")
    high_specific = ("architectural", "/arch", "spec", "project manual", "addendum", "addenda", "bulletin", "asi", "envelope", "insulation")
    if any(term in text for term in low_terms) and not any(term in text for term in high_specific):
        return "low"
    if any(term in text for term in high_terms):
        return "high"
    if any(term in text for term in medium_terms):
        return "medium"
    return "low"


def package_cache_key(inspection: PackageInspectionResult, budgets: ProgressiveBudgets, depth: int) -> str:
    parts = [
        f"{candidate.candidate_id}:{candidate.file_hash}:{candidate.compressed_size}:{candidate.uncompressed_size}"
        for candidate in inspection.candidates
    ]
    return "|".join(parts) + f"|depth={depth}|budgets={asdict(budgets)}"


def _cache_digest(key: str) -> str:
    return hashlib.sha1(f"{CACHE_VERSION}|{key}".encode("utf-8")).hexdigest()


def _cache_dir() -> Path:
    path = Path(".cache") / "foamscope_progressive"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _candidate_page_cache_path(run_digest: str, candidate_id: str) -> Path:
    candidate_digest = hashlib.sha1(candidate_id.encode("utf-8")).hexdigest()
    return _cache_dir() / f"{run_digest}_{candidate_digest}_pages.pkl"


def _result_cache_path(run_digest: str) -> Path:
    return _cache_dir() / f"{run_digest}_result.pkl"


def _load_pickle(path: Path) -> Any | None:
    try:
        if path.exists():
            with path.open("rb") as handle:
                return pickle.load(handle)
    except Exception:
        return None
    return None


def _save_pickle(path: Path, payload: Any) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp_path.open("wb") as handle:
            pickle.dump(payload, handle)
        tmp_path.replace(path)
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def _budget_hit(value: int, budget: int | None) -> bool:
    return budget is not None and value >= budget


def _remaining_budget(value: int, budget: int | None) -> int | None:
    if budget is None:
        return None
    return max(0, budget - value)


def run_progressive_package_analysis(
    inspection: PackageInspectionResult,
    *,
    depth: int = 5,
    budgets: ProgressiveBudgets | None = None,
    use_cache: bool = True,
    use_disk_cache: bool = False,
    analysis_mode: str = "Standard",
) -> dict[str, Any]:
    budgets = budgets or ProgressiveBudgets()
    key = package_cache_key(inspection, budgets, depth)
    if use_cache and key in _PROGRESSIVE_CACHE:
        cached = _PROGRESSIVE_CACHE[key].copy()
        cached["cache_hit"] = True
        cached.setdefault("scan_completeness", {})["cache_resume_used"] = True
        return cached
    run_digest = _cache_digest(key)
    if use_cache and use_disk_cache:
        cached = _load_pickle(_result_cache_path(run_digest))
        if isinstance(cached, dict):
            cached = cached.copy()
            cached["cache_hit"] = True
            cached.setdefault("scan_completeness", {})["cache_resume_used"] = True
            return cached

    started = time.monotonic()
    warnings = list(inspection.warnings)
    partial = False
    budget_hit_reason = ""
    candidates = list(inspection.candidates)
    manifest_rows = []
    total_estimated_pages = len(candidates)
    for candidate in candidates:
        priority = candidate_priority(candidate)
        manifest_rows.append(
            {
                "candidate_id": candidate.candidate_id,
                "document_name": candidate.document_name,
                "source_path": candidate.source_path,
                "priority": priority,
                "compressed_size": candidate.compressed_size,
                "uncompressed_size": candidate.uncompressed_size,
                "status": "manifested" if budgets.include_low_priority_documents or priority != "low" else "deferred",
            }
        )

    indexed_pages: list[PageRecord] = []
    fast_scanned_docs = 0
    sampled_page_count = 0
    light_index_count = 0
    materialized_by_id = {}

    for candidate in sorted(candidates, key=lambda item: {"high": 0, "medium": 1, "low": 2}[candidate_priority(item)]):
        priority = candidate_priority(candidate)
        if priority == "low" and not budgets.include_low_priority_documents:
            continue
        if budgets.max_runtime_seconds is not None and time.monotonic() - started > budgets.max_runtime_seconds:
            partial = True
            budget_hit_reason = "runtime"
            warnings.append("Runtime budget hit during document fast scan; results are partial.")
            break
        if _budget_hit(sampled_page_count, budgets.max_initial_sample_pages) or _budget_hit(light_index_count, budgets.max_light_index_pages):
            partial = True
            budget_hit_reason = "page_index"
            warnings.append("Page indexing budget hit during document fast scan; results are partial.")
            break

        package = materialize_selected_documents(inspection, {candidate.candidate_id})
        warnings.extend(package.warnings)
        if not package.documents:
            continue
        document = package.documents[0]
        materialized_by_id[candidate.candidate_id] = document
        page_cache_path = _candidate_page_cache_path(run_digest, candidate.candidate_id)
        cached_sampled = _load_pickle(page_cache_path) if use_disk_cache else None
        if isinstance(cached_sampled, dict):
            sampled = {
                "page_count": cached_sampled.get("page_count", 0),
                "pages": [PageRecord(**page) for page in cached_sampled.get("pages", [])],
            }
        else:
            sampled = sample_document_pages(document, budgets, sampled_page_count)
            if use_disk_cache:
                _save_pickle(
                    page_cache_path,
                    {
                        "page_count": sampled.get("page_count", 0),
                        "pages": [page.to_dict() for page in sampled.get("pages", [])],
                    },
                )
        if sampled["page_count"]:
            total_estimated_pages += max(0, int(sampled["page_count"]) - 1)
        pages = sampled["pages"]
        if not pages:
            continue
        remaining_sample_budget = _remaining_budget(sampled_page_count, budgets.max_initial_sample_pages)
        remaining_light_budget = _remaining_budget(light_index_count, budgets.max_light_index_pages)
        remaining_budgets = [value for value in (remaining_sample_budget, remaining_light_budget) if value is not None]
        if remaining_budgets:
            pages = pages[: max(0, min(remaining_budgets))]
        sampled_page_count += len(pages)
        light_index_count += len(pages)
        fast_scanned_docs += 1
        indexed_pages.extend(pages)

    indexed_pages = index_sheets(indexed_pages)
    indexed_pages = attach_references(indexed_pages)
    indexed_pages = classify_pages(indexed_pages)
    for page in indexed_pages:
        page.processing_status = "lightly_indexed"

    graph = build_reference_graph(indexed_pages)
    warnings.extend(graph.graph.get("warnings", []))
    seeds = foam_seed_nodes(indexed_pages)
    selected_nodes = expand_neighbors(graph, seeds, depth=depth) if seeds else set()
    selected_page_nodes = {node for node in selected_nodes if node in {page_node_id(page) for page in indexed_pages}}

    deep_analyzed_count = 0
    for page in indexed_pages:
        if page_node_id(page) in selected_page_nodes:
            if deep_analyzed_count < budgets.max_deep_analysis_pages:
                page.processing_status = "deep_analyzed"
                deep_analyzed_count += 1
            else:
                page.processing_status = "graph_included"
                if budgets.max_deep_analysis_pages > 0:
                    partial = True
        elif page.processing_status != "deep_analyzed":
            page.processing_status = "sampled"

    sheet_map = build_sheet_map(indexed_pages)
    deferred_pages = max(0, total_estimated_pages - len(indexed_pages))
    relevant_rows = relevant_pages_table(indexed_pages, selected_nodes, graph, seeds)
    tree = build_measurement_tree(indexed_pages, graph, selected_nodes, seeds)
    exported_selected_nodes = [node["node_id"] for node in tree.get("nodes", [])]
    scan_completeness = {
        "total_documents_discovered": len(candidates),
        "total_pages_discovered": total_estimated_pages,
        "total_pages_sampled": sampled_page_count,
        "total_pages_lightly_indexed": light_index_count,
        "total_pages_deep_analyzed": deep_analyzed_count,
        "total_pages_deferred": deferred_pages,
        "processing_budget_hit": partial and bool(budget_hit_reason),
        "budget_hit_reason": budget_hit_reason,
        "analysis_mode": analysis_mode,
        "cache_resume_used": False,
    }
    result = {
        "documents": [document.to_dict() for document in materialized_by_id.values()],
        "manifest": manifest_rows,
        "pages": indexed_pages,
        "graph": graph,
        "selected_nodes": selected_nodes,
        "selected_nodes_exported": exported_selected_nodes,
        "selected_node_count_internal": len(selected_nodes),
        "exported_node_count": len(exported_selected_nodes),
        "seed_nodes": seeds,
        "tree": tree,
        "relevant_rows": relevant_rows,
        "edge_rows": graph_edges_table(graph),
        "sheet_map": sheet_map,
        "warnings": sorted(set(warnings)),
        "partial": partial,
        "cache_hit": False,
        "budgets": asdict(budgets),
        "progress": {
            "pdf_count": len(candidates),
            "estimated_total_pages": total_estimated_pages,
            "fast_scanned_documents": fast_scanned_docs,
            "fast_scanned_pages": sampled_page_count,
            "sheet_count": sum(len(nodes) for nodes in sheet_map.values()),
            "foam_seed_pages": len(seeds),
            "reference_expanded_pages": len(selected_page_nodes),
            "deep_analyzed_pages": deep_analyzed_count,
            "deferred_pages": deferred_pages,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "memory_rss_mb": current_memory_rss_mb(),
            "stage": "partial" if partial else "initial_tree_complete",
            "full_lightweight_index": budgets.full_lightweight_index,
        },
        "scan_completeness": scan_completeness,
    }
    if use_cache:
        _PROGRESSIVE_CACHE[key] = result.copy()
        if use_disk_cache:
            _save_pickle(_result_cache_path(run_digest), result)
    return result


def sample_document_pages(document: Any, budgets: ProgressiveBudgets, already_sampled: int) -> dict[str, Any]:
    try:
        import fitz
    except ImportError:
        return {"pages": [], "page_count": 0}

    if not document.file_path:
        return {"pages": [], "page_count": 0}
    path = Path(document.file_path)
    if not path.exists():
        return {"pages": [], "page_count": 0}

    pdf = fitz.open(str(path))
    pages: list[PageRecord] = []
    try:
        page_count = pdf.page_count
        if budgets.full_lightweight_index:
            sample_indexes = set(range(page_count))
        else:
            sample_indexes = set(range(min(2, page_count)))
            for index in range(max(0, page_count - 2), page_count):
                sample_indexes.add(index)
            for index in range(min(page_count, 12)):
                text = pdf[index].get_text("text") or ""
                lowered = text.lower()
                if "table of contents" in lowered or "drawing index" in lowered or "sheet index" in lowered or lowered.strip().startswith("index"):
                    sample_indexes.add(index)
        remaining = _remaining_budget(already_sampled, budgets.max_initial_sample_pages)
        indexes = sorted(sample_indexes) if remaining is None else sorted(sample_indexes)[:remaining]
        for index in indexes:
            page = pdf[index]
            text = page.get_text("text") or ""
            rect = page.rect
            pages.append(
                PageRecord(
                    document_id=document.document_id,
                    document_name=document.document_name,
                    document_type=document.document_type,
                    source_path=document.source_path,
                    global_page_id=f"{document.document_id}::page_{index + 1}",
                    page_index=index,
                    page_num=index + 1,
                    page_number=index + 1,
                    text=text,
                    word_count=len(text.split()),
                    width=float(rect.width),
                    height=float(rect.height),
                    processing_status="sampled",
                    original_document_name=getattr(document, "original_document_name", "") or document.document_name,
                    original_page_number=getattr(document, "original_page_number", None),
                )
            )
    finally:
        pdf.close()
    return {"pages": pages, "page_count": page_count}


def build_sheet_map(pages: list[PageRecord]) -> dict[str, list[dict[str, Any]]]:
    sheet_map: dict[str, list[dict[str, Any]]] = {}
    for page in pages:
        if not page.sheet_number:
            continue
        sheet_map.setdefault(page.sheet_number, []).append(
            {
                "global_page_id": page.global_page_id,
                "document_name": page.document_name,
                "page_num": page.page_num,
                "sheet_title": page.sheet_title,
            }
        )
    return sheet_map
