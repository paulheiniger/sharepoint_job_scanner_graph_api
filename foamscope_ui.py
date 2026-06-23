from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from jobscan.env import graph_env_status, load_project_env

load_project_env()

from indexing.graph_builder import build_reference_graph, expand_neighbors, foam_seed_nodes, graph_edges_table
from indexing.page_classifier import classify_pages
from indexing.progressive_pipeline import ProgressiveBudgets, candidate_priority, run_progressive_package_analysis
from indexing.reference_extractor import attach_references
from indexing.sheet_indexer import index_sheets
from ingest.package_ingest import (
    MB,
    PackageInspectionResult,
    PdfCandidate,
    PdfDocumentInput,
    SMALL_UPLOAD_WARNING_BYTES,
    inspect_path_package,
    inspect_uploaded_package,
    normalize_pdf_document,
    triage_pdf_candidate,
)
from ingest.pdf_ingest import PageRecord, ingest_pdf
from ingest.sharepoint_package_ingest import SHAREPOINT_NOT_CONFIGURED_MESSAGE, inspect_sharepoint_url_package
from intake.source_detector import detect_source_type
from takeoff.insulation_scope_tree import build_measurement_tree, relevant_pages_table


def dataframe_from_records(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def render_graph_config_debug_panel() -> None:
    with st.expander("Graph config debug", expanded=False):
        st.caption("Environment variable values are hidden; only FOUND/MISSING status is shown.")
        status_rows = [{"setting": key, "status": value} for key, value in graph_env_status().items()]
        st.dataframe(pd.DataFrame(status_rows), use_container_width=True, hide_index=True)


def documents_table(documents: list[PdfDocumentInput]) -> pd.DataFrame:
    return dataframe_from_records([document.to_dict() for document in documents])


def candidates_table(candidates: list[Any]) -> pd.DataFrame:
    rows = []
    for candidate in candidates:
        rows.append(
            {
                "selected": candidate.default_selected,
                "candidate_id": candidate.candidate_id,
                "filename": candidate.document_name,
                "source": candidate.source_path,
                "source_kind": candidate.source_kind,
                "priority": candidate_priority(candidate),
                "guessed_document_type": candidate.document_type,
                "triage_classification": candidate.triage_classification,
                "triage_score": candidate.triage_score,
                "triage_evidence": "; ".join(candidate.triage_evidence or []),
                "sample_pages": ", ".join(str(page) for page in (candidate.triage_sample_pages or [])),
                "compressed_size_mb": round(candidate.compressed_size / MB, 2),
                "uncompressed_size_mb": round(candidate.uncompressed_size / MB, 2),
            }
        )
    return dataframe_from_records(rows)


@st.cache_data(show_spinner=False)
def triage_candidate_cached(candidate_data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    candidate = PdfCandidate(**candidate_data)
    triaged, warnings = triage_pdf_candidate(candidate)
    return triaged.to_dict(), warnings


def triage_inspection_cached(inspection: PackageInspectionResult) -> PackageInspectionResult:
    candidates: list[PdfCandidate] = []
    warnings = list(inspection.warnings)
    for candidate in inspection.candidates:
        triaged_data, triage_warnings = triage_candidate_cached(candidate.to_dict())
        candidates.append(PdfCandidate(**triaged_data))
        warnings.extend(f"{candidate.document_name}: {warning}" for warning in triage_warnings)
    return replace(inspection, candidates=candidates, warnings=warnings)


@st.cache_data(show_spinner=False)
def ingest_pdf_cached(
    file_path: str,
    file_hash: str,
    use_ocr: bool,
    document_id: str,
    document_name: str,
    document_type: str,
    source_path: str,
) -> list[dict[str, Any]]:
    pages = ingest_pdf(
        file_path,
        ocr_sparse_pages=use_ocr,
        document_id=document_id,
        document_name=document_name,
        document_type=document_type,
        source_path=source_path,
    )
    return [page.to_dict() for page in pages]


def analyze_pdf(pdf_bytes: bytes, *, depth: int, use_ocr: bool) -> dict[str, Any]:
    document = normalize_pdf_document("uploaded.pdf", pdf_bytes, index=0)
    return analyze_documents([document], depth=depth, use_ocr=use_ocr, package_warnings=[])


def analyze_documents(
    documents: list[PdfDocumentInput],
    *,
    depth: int,
    use_ocr: bool,
    package_warnings: list[str] | None = None,
) -> dict[str, Any]:
    pages = []
    warnings = list(package_warnings or [])
    for document in documents:
        try:
            if document.file_path:
                page_dicts = ingest_pdf_cached(
                    document.file_path,
                    document.file_hash,
                    use_ocr,
                    document.document_id,
                    document.document_name,
                    document.document_type,
                    document.source_path,
                )
                pages.extend(PageRecord(**page_dict) for page_dict in page_dicts)
            else:
                pages.extend(
                    ingest_pdf(
                        document.content or b"",
                        ocr_sparse_pages=use_ocr,
                        document_id=document.document_id,
                        document_name=document.document_name,
                        document_type=document.document_type,
                        source_path=document.source_path,
                    )
                )
        except Exception as exc:
            warnings.append(f"Could not analyze {document.document_name}: {type(exc).__name__}: {exc}")
    pages = index_sheets(pages)
    pages = attach_references(pages)
    pages = classify_pages(pages)
    graph = build_reference_graph(pages)
    warnings.extend(graph.graph.get("warnings", []))
    seeds = foam_seed_nodes(pages)
    selected_nodes = expand_neighbors(graph, seeds, depth=depth) if seeds else set()
    if not selected_nodes:
        selected_nodes = {page.global_page_id for page in pages if page.global_page_id}
    tree = build_measurement_tree(pages, graph, selected_nodes, seeds)
    return {
        "documents": documents,
        "pages": pages,
        "graph": graph,
        "selected_nodes": selected_nodes,
        "tree": tree,
        "seed_nodes": seeds,
        "relevant_rows": relevant_pages_table(pages, selected_nodes, graph, seeds),
        "edge_rows": graph_edges_table(graph),
        "warnings": sorted(set(warnings)),
    }


def render_foamscope_page() -> None:
    st.title("FoamScope AI")
    st.caption(
        "Upload construction plan/spec PDFs or ZIP bid packages to identify spray-foam insulation scope sheets, "
        "referenced sheets, and likely measurement pages. Prototype only: estimator review required."
    )

    with st.sidebar:
        st.header("FoamScope Analysis")
        depth = st.slider("Reference expansion depth", min_value=0, max_value=8, value=5)
        use_ocr = st.checkbox(
            "Use OCR fallback for sparse pages",
            value=False,
            help="Advanced. Uses pytesseract when embedded PDF text is sparse; keep off for large packages unless needed.",
        )
        st.markdown("**No paid API key required.**")
        st.caption("TODO: optional LLM summaries could later explain ambiguous scope evidence.")
        review_selection = st.checkbox(
            "Review document selection before deep analysis",
            value=False,
            help="Advanced: lets you override FoamScope's automatic triage selection.",
        )
        analyze_all = st.checkbox(
            "Analyze all documents anyway",
            value=False,
            help="Overrides triage and may be slow for large bid packages.",
        )
        stop_after_initial_tree = st.checkbox(
            "Stop after initial tree",
            value=False,
            help="Build the manifest, sheet map, foam seeds, and reference-expanded tree without marking pages for deep analysis.",
        )
        budget_multiplier = st.session_state.get("foamscope_budget_multiplier", 1)
        with st.expander("Processing budgets", expanded=False):
            max_initial_sample_pages = st.number_input("Max initial sample pages", min_value=10, max_value=5000, value=200 * budget_multiplier)
            max_light_index_pages = st.number_input("Max light index pages", min_value=10, max_value=10000, value=500 * budget_multiplier)
            max_deep_analysis_pages = st.number_input("Max deep analysis pages", min_value=1, max_value=2000, value=150 * budget_multiplier)
            max_runtime_seconds = st.number_input("Max runtime seconds", min_value=5, max_value=300, value=25 * budget_multiplier)

    st.subheader("Package Intake")
    intake_mode = st.radio(
        "Intake mode",
        ["SharePoint folder URL", "Local/server path", "Upload small package"],
        horizontal=True,
        help="Use SharePoint URL mode for web links, local/server path for files visible to the Streamlit server, or upload for small packages.",
    )

    inspection: PackageInspectionResult | None = None
    package_source = ""
    if intake_mode == "Upload small package":
        st.warning(
            f"Small Upload Mode is intended for packages under {SMALL_UPLOAD_WARNING_BYTES / MB:,.0f} MB. "
            "For larger ZIPs, use local/server path mode to avoid browser memory pressure."
        )
        uploaded_files = st.file_uploader(
            "Upload construction PDFs or ZIP bid packages",
            type=["pdf", "zip"],
            accept_multiple_files=True,
        )
        if not uploaded_files:
            st.info("Upload one or more plan/spec PDFs or ZIP files containing PDFs to begin.")
            return
        with st.spinner("Writing uploads to a temporary project directory and creating package manifest..."):
            inspection = inspect_uploaded_package(uploaded_files)
        package_source = "browser upload"
    elif intake_mode == "Local/server path":
        st.caption("Local/server path must be a path visible to the machine running this app. Use this only for paths on the machine running Streamlit.")
        path_value = st.text_input(
            "Local/server ZIP or folder path",
            value="",
            placeholder="/path/to/bid-package.zip or /path/to/bid-folder",
        )
        if not path_value.strip():
            st.info("Enter a local/server path to a ZIP, PDF, or folder containing PDFs/ZIPs.")
            return
        source_type = detect_source_type(path_value)
        if source_type == "sharepoint_url":
            st.error("This is a SharePoint URL, not a local path. Choose SharePoint folder URL mode.")
            return
        path_obj = Path(path_value).expanduser()
        if not path_obj.exists():
            st.error(f"Path does not exist: {path_obj}")
            return
        with st.spinner("Inspecting local/server path and creating package manifest..."):
            inspection = inspect_path_package(path_obj)
        package_source = str(path_obj)
    else:
        st.caption("SharePoint links require SharePoint/Graph intake.")
        render_graph_config_debug_panel()
        url_value = st.text_input(
            "SharePoint folder URL",
            value="",
            placeholder="https://contoso.sharepoint.com/:f:/s/...",
        )
        if not url_value.strip():
            st.info("Paste a SharePoint or OneDrive folder link, or use a synced local OneDrive folder in Local/server path mode.")
            return
        source_type = detect_source_type(url_value)
        if source_type != "sharepoint_url":
            st.error("Enter a SharePoint or OneDrive folder URL, or choose Local/server path mode for filesystem paths.")
            return
        with st.spinner("Inspecting SharePoint folder through Microsoft Graph..."):
            inspection = inspect_sharepoint_url_package(url_value.strip())
        if inspection.warnings and not inspection.candidates:
            st.error(SHAREPOINT_NOT_CONFIGURED_MESSAGE)
        package_source = "SharePoint folder URL"

    if inspection is None:
        return
    if inspection.warnings:
        with st.expander("Package warnings", expanded=True):
            for warning in inspection.warnings:
                st.warning(warning)
    if not inspection.candidates:
        st.warning("No PDF documents were found in the uploaded files.")
        return

    st.subheader("Package Manifest")
    st.caption(
        "FoamScope inspects package files first. ZIP central directories are read without extracting every PDF. "
        "Low-priority documents remain deferred, not discarded."
    )
    candidate_df = candidates_table(inspection.candidates)
    s1, s2, s3 = st.columns(3)
    s1.metric("Package source", package_source if len(package_source) < 28 else "path/upload")
    s2.metric("Package size", f"{inspection.total_upload_size / MB:,.1f} MB")
    s3.metric("PDFs discovered", f"{len(inspection.candidates):,}")
    if analyze_all:
        st.warning("Analyze all documents is enabled. This may be slow for large bid packages.")
        candidate_df["selected"] = True
        selected_ids = set(candidate_df["candidate_id"].astype(str).tolist())
        st.dataframe(
            candidate_df.drop(columns=["candidate_id"]),
            use_container_width=True,
            hide_index=True,
        )
    elif review_selection:
        edited_candidates = st.data_editor(
            candidate_df,
            use_container_width=True,
            hide_index=True,
            disabled=[
                "candidate_id",
                "filename",
                "source",
                "source_kind",
                "priority",
                "guessed_document_type",
                "triage_classification",
                "triage_score",
                "triage_evidence",
                "sample_pages",
                "compressed_size_mb",
                "uncompressed_size_mb",
            ],
            column_config={
                "selected": st.column_config.CheckboxColumn("Analyze", help="Only selected PDFs are extracted and processed."),
                "candidate_id": None,
            },
            key="foamscope_candidate_selector",
        )
        selected_ids = set(edited_candidates.loc[edited_candidates["selected"] == True, "candidate_id"].astype(str).tolist())
    else:
        candidate_df["selected"] = True
        selected_ids = set(candidate_df["candidate_id"].astype(str).tolist())
        st.dataframe(
            candidate_df.drop(columns=["candidate_id"]),
            use_container_width=True,
            hide_index=True,
        )

    selected_uncompressed = sum(candidate.uncompressed_size for candidate in inspection.candidates if candidate.candidate_id in selected_ids)
    st.caption(
        f"{len(selected_ids):,} of {len(inspection.candidates):,} PDFs selected. "
        f"Selected uncompressed size: {selected_uncompressed / MB:,.1f} MB."
    )
    if not selected_ids:
        st.info("Select at least one PDF for the global index.")
        return
    if review_selection and not analyze_all and not st.button("Analyze selected documents", type="primary"):
        return

    selected_inspection = replace(
        inspection,
        candidates=[candidate for candidate in inspection.candidates if candidate.candidate_id in selected_ids],
    )

    budgets = ProgressiveBudgets(
        max_initial_sample_pages=int(max_initial_sample_pages),
        max_light_index_pages=int(max_light_index_pages),
        max_deep_analysis_pages=0 if stop_after_initial_tree else int(max_deep_analysis_pages),
        max_ocr_pages=0,
        max_runtime_seconds=int(max_runtime_seconds),
    )
    with st.spinner("Building progressive package manifest, sheet map, foam seeds, and reference-expanded tree..."):
        try:
            result = run_progressive_package_analysis(selected_inspection, depth=depth, budgets=budgets)
        except Exception as exc:
            st.error(f"Could not analyze PDF package: {type(exc).__name__}: {exc}")
            return

    pages = result["pages"]
    tree = result["tree"]
    relevant_df = dataframe_from_records(result["relevant_rows"])
    edge_df = dataframe_from_records(result["edge_rows"])
    progress = result["progress"]
    if result.get("partial"):
        st.warning("Processing budget was hit. Results are partial, and deferred pages were not discarded.")
        if st.button("Continue expanding analysis", type="primary"):
            st.session_state["foamscope_budget_multiplier"] = st.session_state.get("foamscope_budget_multiplier", 1) + 1
            st.rerun()

    high_count = sum(1 for page in pages if page.relevance_level == "high")
    medium_count = sum(1 for page in pages if page.relevance_level == "medium")
    selected_count = len(result["selected_nodes"])
    seed_count = len(result["seed_nodes"])
    selected_page_ids = {node for node in result["selected_nodes"] if node in {page.global_page_id for page in pages}}
    low_connected_count = sum(1 for page in pages if page.global_page_id in selected_page_ids and page.relevance_level == "low")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Package manifest", f"{progress['pdf_count']:,} PDFs")
    c2.metric("Fast scanned", f"{progress['fast_scanned_documents']:,} docs / {progress['fast_scanned_pages']:,} pages")
    c3.metric("Sheet map found", f"{progress['sheet_count']:,} sheets")
    c4.metric("Foam seeds found", f"{progress['foam_seed_pages']:,}")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Reference-expanded pages", f"{progress['reference_expanded_pages']:,}")
    m2.metric("Deep analyzed pages", f"{progress['deep_analyzed_pages']:,}")
    m3.metric("Deferred pages", f"{progress['deferred_pages']:,}")
    m4.metric("Warnings", f"{len(result['warnings']):,}")
    p1, p2, p3 = st.columns(3)
    p1.metric("Current stage", str(progress.get("stage") or "unknown"))
    p2.metric("Runtime", f"{progress.get('elapsed_seconds', 0):,.1f}s")
    memory_value = progress.get("memory_rss_mb")
    p3.metric("Process memory", f"{memory_value:,.1f} MB" if memory_value is not None else "n/a")
    st.caption(
        f"Package indexed: {progress['pdf_count']:,} documents, {progress['estimated_total_pages']:,} estimated pages. "
        f"Foam seed pages found: {seed_count:,}. "
        f"Reference-expanded pages included even without foam keywords: {low_connected_count:,}."
    )
    if result.get("cache_hit"):
        st.caption("Loaded progressive analysis from cache.")

    st.warning("FoamScope AI produces an estimator-reviewed measurement map. It does not calculate a final bid.")
    if result["warnings"]:
        with st.expander("Analysis warnings", expanded=False):
            for warning in result["warnings"]:
                st.warning(warning)

    st.subheader("Relevant Sheets")
    if relevant_df.empty:
        st.info("No relevant foam insulation sheets were identified. Review keyword configs or try OCR.")
    else:
        display_cols = [
            "document_name",
            "sheet_id",
            "sheet_title",
            "page_num",
            "page_type",
            "foam_relevance",
            "role",
            "relevance_score",
            "evidence",
            "inclusion_path",
            "needs_measurement",
            "references",
            "used_ocr",
            "warnings",
        ]
        st.dataframe(relevant_df[[col for col in display_cols if col in relevant_df.columns]], use_container_width=True, hide_index=True)

    st.subheader("Reference Tree")
    left, right = st.columns([1, 1])
    with left:
        st.markdown("**Measurement Tree JSON**")
        st.json(tree)
    with right:
        st.markdown("**Graph Edges**")
        if edge_df.empty:
            st.caption("No sheet references were connected to known sheets.")
        else:
            st.dataframe(edge_df, use_container_width=True, hide_index=True)

    with st.expander("All page scores", expanded=False):
        all_pages = dataframe_from_records(
            [
                {
                    "document_name": page.document_name,
                    "global_page_id": page.global_page_id,
                    "page_num": page.page_num,
                    "page_type": page.page_type,
                    "sheet_id": page.sheet_id,
                    "sheet_title": page.sheet_title,
                    "role": page.role,
                    "foam_relevance": page.foam_relevance,
                    "relevance_score": page.relevance_score,
                    "evidence": ", ".join(page.evidence),
                    "word_count": page.word_count,
                    "used_ocr": page.used_ocr,
                    "processing_status": page.processing_status,
                }
                for page in pages
            ]
        )
        st.dataframe(all_pages, use_container_width=True, hide_index=True)

    indexed_not_included = [
        {
            "document_name": page.document_name,
            "page_num": page.page_num,
            "sheet_id": page.sheet_id,
            "sheet_title": page.sheet_title,
            "role": page.role,
            "foam_relevance": page.foam_relevance,
        }
        for page in pages
        if page.global_page_id not in selected_page_ids
    ]
    with st.expander("Indexed pages not in expanded FoamScope tree", expanded=False):
        st.caption(
            "These pages remained in the lightweight global index and reference graph, "
            "but were not connected to the current foam seed subgraph within the selected expansion depth."
        )
        st.dataframe(dataframe_from_records(indexed_not_included), use_container_width=True, hide_index=True)

    export_payload = {
        "documents": result["documents"],
        "manifest": result["manifest"],
        "progress": result["progress"],
        "partial": result["partial"],
        "pages": [page.to_dict() for page in pages],
        "relevant_pages": result["relevant_rows"],
        "reference_graph": {
            "nodes": [{"node_id": node, **data} for node, data in result["graph"].nodes(data=True)],
            "edges": result["edge_rows"],
            "warnings": result["graph"].graph.get("warnings", []),
        },
        "measurement_tree": tree,
        "warnings": result["warnings"],
    }
    st.subheader("Exports")
    e1, e2 = st.columns(2)
    with e1:
        st.download_button(
            "Download JSON",
            data=json.dumps(export_payload, indent=2, default=str).encode("utf-8"),
            file_name="foamscope_ai_measurement_tree.json",
            mime="application/json",
        )
    with e2:
        csv_bytes = relevant_df.to_csv(index=False).encode("utf-8") if not relevant_df.empty else b""
        st.download_button(
            "Download Relevant Sheets CSV",
            data=csv_bytes,
            file_name="foamscope_ai_relevant_sheets.csv",
            mime="text/csv",
            disabled=relevant_df.empty,
        )
