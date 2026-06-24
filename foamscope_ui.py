from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from dotenv import load_dotenv

load_dotenv()

from jobscan.env import graph_env_debug_info, graph_env_status, load_project_env

load_project_env()

from indexing.graph_builder import apply_graph_measurement_roles, build_reference_graph, expand_neighbors, foam_seed_nodes, graph_edges_table
from indexing.page_classifier import classify_pages
from indexing.progressive_pipeline import ProgressiveBudgets, candidate_priority, run_progressive_package_analysis
from indexing.reference_extractor import attach_references
from indexing.sheet_indexer import index_sheets
from indexing.trade_profiles import available_trade_types, load_trade_profile
from ingest.package_ingest import (
    MB,
    PackageInspectionResult,
    PdfCandidate,
    PdfDocumentInput,
    SMALL_UPLOAD_WARNING_BYTES,
    expand_sharepoint_zip_candidates,
    inspect_path_package,
    inspect_uploaded_package,
    normalize_pdf_document,
    triage_pdf_candidate,
)
from ingest.pdf_ingest import PageRecord, ingest_pdf
from ingest.sharepoint_package_ingest import SHAREPOINT_NOT_CONFIGURED_MESSAGE, inspect_sharepoint_url_package
from intake.source_detector import detect_source_type
from takeoff.insulation_scope_tree import build_measurement_tree, relevant_pages_table
from training.bidscope_review_export import build_bidscope_review_export_zip
from training.foamscope_evaluator import compare_foamscope_output_to_takeoff_export


def dataframe_from_records(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def render_graph_config_debug_panel() -> None:
    with st.expander("Graph config debug", expanded=False):
        st.caption("Environment variable values are hidden; only FOUND/MISSING status is shown.")
        debug_info = graph_env_debug_info()
        st.write("Current working directory:", debug_info["current_working_directory"])
        st.write(".env exists in current working directory:", "FOUND" if debug_info["cwd_dotenv_exists"] else "MISSING")
        st.write(".env exists in repository root:", "FOUND" if debug_info["repo_dotenv_exists"] else "MISSING")
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
    original_document_name: str,
    original_page_number: int | None,
) -> list[dict[str, Any]]:
    pages = ingest_pdf(
        file_path,
        ocr_sparse_pages=use_ocr,
        document_id=document_id,
        document_name=document_name,
        document_type=document_type,
        source_path=source_path,
        original_document_name=original_document_name,
        original_page_number=original_page_number,
    )
    return [page.to_dict() for page in pages]


def analyze_pdf(pdf_bytes: bytes, *, depth: int, use_ocr: bool, trade_type: str = "foam_insulation") -> dict[str, Any]:
    document = normalize_pdf_document("uploaded.pdf", pdf_bytes, index=0)
    return analyze_documents([document], depth=depth, use_ocr=use_ocr, package_warnings=[], trade_type=trade_type)


def analyze_documents(
    documents: list[PdfDocumentInput],
    *,
    depth: int,
    use_ocr: bool,
    package_warnings: list[str] | None = None,
    trade_type: str = "foam_insulation",
) -> dict[str, Any]:
    trade_profile = load_trade_profile(trade_type)
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
                    document.original_document_name,
                    document.original_page_number,
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
                        original_document_name=document.original_document_name,
                        original_page_number=document.original_page_number,
                    )
                )
        except Exception as exc:
            warnings.append(f"Could not analyze {document.document_name}: {type(exc).__name__}: {exc}")
    pages = index_sheets(pages)
    pages = attach_references(pages)
    pages = classify_pages(pages, trade_type=trade_type)
    graph = build_reference_graph(pages)
    warnings.extend(graph.graph.get("warnings", []))
    seeds = foam_seed_nodes(pages)
    selected_nodes = expand_neighbors(graph, seeds, depth=depth) if seeds else set()
    if not selected_nodes:
        selected_nodes = {page.global_page_id for page in pages if page.global_page_id and page.foam_seed_level == "generic_only"}
    apply_graph_measurement_roles(pages, graph, selected_nodes, seeds, trade_profile)
    tree = build_measurement_tree(pages, graph, selected_nodes, seeds, trade_profile=trade_profile)
    return {
        "trade_type": trade_profile.get("trade_type", trade_type),
        "trade_name": trade_profile.get("trade_name", trade_type.replace("_", " ").title()),
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


def build_export_payload(result: dict[str, Any], pages: list[PageRecord], *, analysis_mode: str = "Standard") -> dict[str, Any]:
    return {
        "tool_name": "BidScope AI",
        "trade_type": result.get("trade_type") or (result.get("scan_completeness") or {}).get("trade_type"),
        "trade_name": result.get("trade_name") or (result.get("scan_completeness") or {}).get("trade_name"),
        "documents": result["documents"],
        "manifest": result["manifest"],
        "progress": result["progress"],
        "scan_completeness": result.get("scan_completeness", {}),
        "partial": result["partial"],
        "analysis_mode": analysis_mode,
        "selected_node_count_internal": result.get("selected_node_count_internal"),
        "exported_node_count": result.get("exported_node_count"),
        "selected_nodes_exported": result.get("selected_nodes_exported", []),
        "pages": [page.to_dict() for page in pages],
        "relevant_pages": result["relevant_rows"],
        "reference_graph": {
            "nodes": [{"node_id": node, **data} for node, data in result["graph"].nodes(data=True)],
            "edges": result["edge_rows"],
            "warnings": result["graph"].graph.get("warnings", []),
        },
        "measurement_tree": result["tree"],
        "warnings": result["warnings"],
    }


def render_foamscope_page() -> None:
    st.title("BidScope AI")
    st.caption(
        "Upload construction plan/spec PDFs or ZIP bid packages to identify trade scope evidence, "
        "referenced sheets, and likely measurement pages. Prototype only: estimator review required."
    )

    with st.sidebar:
        st.header("BidScope Analysis")
        trade_options = available_trade_types()
        trade_labels = list(trade_options.values())
        selected_trade_label = st.selectbox("Trade", trade_labels, index=0)
        trade_type = {label: key for key, label in trade_options.items()}[selected_trade_label]
        trade_profile = load_trade_profile(trade_type)
        analysis_mode = st.selectbox(
            "Analysis mode",
            ["Quick Scan", "Standard", "Full Package Analysis"],
            index=1,
            help=(
                "Quick Scan returns early for preview. Standard uses moderate budgets. "
                "Full Package Analysis lightweight-indexes all selected PDFs/pages and builds the full reference graph."
            ),
        )
        depth = st.slider("Reference expansion depth", min_value=0, max_value=8, value=5)
        use_ocr = st.checkbox(
            "Enable OCR fallback",
            value=False,
            help="Advanced. Uses pytesseract when embedded PDF text is sparse; keep off for large packages unless needed.",
        )
        render_graph_images = st.checkbox(
            "Render page images for graph-included pages only",
            value=False,
            help="Reserved for visual review. BidScope will not render/OCR every page in Full Package Analysis.",
        )
        st.markdown("**No paid API key required.**")
        st.caption("TODO: optional LLM summaries could later explain ambiguous scope evidence.")
        review_selection = st.checkbox(
            "Review document selection before deep analysis",
            value=False,
            help="Advanced: lets you override BidScope's automatic triage selection.",
        )
        analyze_all = st.checkbox(
            "Analyze all documents anyway",
            value=False,
            help="Overrides triage and may be slow for large bid packages.",
        )
        stop_after_initial_tree = st.checkbox(
            "Stop after initial tree",
            value=False,
            help="Build the manifest, sheet map, trade seed pages, and reference-expanded tree without marking pages for deep analysis.",
        )
        budget_multiplier = st.session_state.get("foamscope_budget_multiplier", 1)
        if analysis_mode == "Quick Scan":
            default_initial_pages = 60 * budget_multiplier
            default_light_pages = 120 * budget_multiplier
            default_deep_pages = 40 * budget_multiplier
            default_runtime_seconds = 12 * budget_multiplier
        elif analysis_mode == "Full Package Analysis":
            default_initial_pages = None
            default_light_pages = None
            default_deep_pages = 5000
            default_runtime_seconds = None
        else:
            default_initial_pages = 200 * budget_multiplier
            default_light_pages = 500 * budget_multiplier
            default_deep_pages = 150 * budget_multiplier
            default_runtime_seconds = 25 * budget_multiplier
        with st.expander("Processing budgets", expanded=False):
            if analysis_mode == "Full Package Analysis":
                st.caption(
                    "Full Package Analysis disables light-index page and runtime stop budgets. "
                    "It still keeps OCR off unless you enable OCR fallback."
                )
                max_initial_sample_pages = None
                max_light_index_pages = None
                max_runtime_seconds = None
            else:
                max_initial_sample_pages = st.number_input(
                    "Max initial sample pages",
                    min_value=10,
                    max_value=5000,
                    value=int(default_initial_pages or 200),
                )
                max_light_index_pages = st.number_input(
                    "Max light index pages",
                    min_value=10,
                    max_value=10000,
                    value=int(default_light_pages or 500),
                )
                max_runtime_seconds = st.number_input(
                    "Max runtime seconds",
                    min_value=5,
                    max_value=300,
                    value=int(default_runtime_seconds or 25),
                )
            max_deep_analysis_pages = st.number_input(
                "Max deep analysis pages",
                min_value=1,
                max_value=10000,
                value=int(default_deep_pages),
            )

    st.subheader("Package Intake")
    project_name = st.text_input("Project name for exports", value="", placeholder="Optional project or bid package name")
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
        if any(candidate.source_kind == "sharepoint_zip" for candidate in inspection.candidates):
            st.info("ZIP detected. Reading files inside ZIP. No manual extraction required.")
            with st.spinner("Downloading SharePoint ZIP and reading its manifest..."):
                inspection = expand_sharepoint_zip_candidates(inspection)
        package_source = "SharePoint folder URL"

    if inspection is None:
        return
    with st.spinner("Running lightweight document triage..."):
        inspection = triage_inspection_cached(inspection)
    if inspection.warnings:
        with st.expander("Package warnings", expanded=True):
            for warning in inspection.warnings:
                st.warning(warning)
    if not inspection.candidates:
        st.warning("No PDF documents were found in the uploaded files.")
        return

    st.subheader("Package Manifest")
    st.caption(
        "BidScope inspects package files first. ZIP central directories are read without extracting every PDF. "
        "Low-priority documents remain deferred, not discarded."
    )
    candidate_df = candidates_table(inspection.candidates)
    zip_member_df = dataframe_from_records(inspection.zip_members or [])
    if not zip_member_df.empty:
        with st.expander("ZIP contents", expanded=True):
            st.caption("ZIP detected. Reading files inside ZIP. No manual extraction required.")
            display_cols = [
                "source_zip_name",
                "internal_path",
                "filename",
                "extension",
                "compressed_size",
                "uncompressed_size",
                "inferred_type",
                "source_sharepoint_url",
            ]
            st.dataframe(zip_member_df[[col for col in display_cols if col in zip_member_df.columns]], use_container_width=True, hide_index=True)
    if inspection.zip_members and not inspection.candidates and not (inspection.takeoff_csvs or []):
        st.warning("No supported PDF or STACK takeoff CSV files were found inside the ZIP.")
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
        max_initial_sample_pages=None if max_initial_sample_pages is None else int(max_initial_sample_pages),
        max_light_index_pages=None if max_light_index_pages is None else int(max_light_index_pages),
        max_deep_analysis_pages=0 if stop_after_initial_tree else int(max_deep_analysis_pages),
        max_ocr_pages=10000 if use_ocr else 0,
        max_runtime_seconds=None if max_runtime_seconds is None else int(max_runtime_seconds),
        include_low_priority_documents=analysis_mode == "Full Package Analysis",
        full_lightweight_index=analysis_mode == "Full Package Analysis",
    )
    if render_graph_images:
        st.info("Page image rendering is limited to graph-included pages and is reserved for a later visual review step.")
    if analysis_mode == "Full Package Analysis":
        st.caption("Full Package Analysis saves per-document progress to disk cache and resumes after reruns when possible.")
    with st.spinner("Building progressive package manifest, sheet map, trade seed pages, and reference-expanded tree..."):
        try:
            result = run_progressive_package_analysis(
                selected_inspection,
                depth=depth,
                budgets=budgets,
                use_disk_cache=analysis_mode == "Full Package Analysis",
                analysis_mode=analysis_mode,
                trade_type=trade_type,
            )
        except Exception as exc:
            st.error(f"Could not analyze PDF package: {type(exc).__name__}: {exc}")
            return

    pages = result["pages"]
    tree = result["tree"]
    relevant_df = dataframe_from_records(result["relevant_rows"])
    edge_df = dataframe_from_records(result["edge_rows"])
    progress = result["progress"]
    scan_completeness = result.get("scan_completeness", {})
    if result.get("partial"):
        st.warning("Processing budget was hit. Results are partial, and deferred pages were not discarded.")
        if analysis_mode != "Full Package Analysis" and st.button("Continue expanding analysis", type="primary"):
            st.session_state["foamscope_budget_multiplier"] = st.session_state.get("foamscope_budget_multiplier", 1) + 1
            st.rerun()
    if analysis_mode == "Full Package Analysis" and st.button("Continue/resume full analysis", type="primary"):
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
    c4.metric("Seed pages found", f"{progress['foam_seed_pages']:,}")
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
        f"{trade_profile.get('trade_name', selected_trade_label)} seed pages found: {seed_count:,}. "
        f"Reference-expanded pages included even without direct trade keywords: {low_connected_count:,}."
    )
    with st.expander("Scan completeness", expanded=True):
        st.json(scan_completeness)
    if result.get("cache_hit"):
        st.caption("Loaded progressive analysis from cache.")
    st.caption(f"Analysis mode: {analysis_mode}. Full lightweight index: {progress.get('full_lightweight_index', False)}.")

    st.warning("BidScope AI produces an estimator-reviewed measurement map. It does not calculate a final bid.")
    if result["warnings"]:
        with st.expander("Analysis warnings", expanded=False):
            for warning in result["warnings"]:
                st.warning(warning)

    tree_nodes_df = dataframe_from_records((tree or {}).get("nodes", []))
    st.subheader("Scope Evidence and Measurement Pages")
    if tree_nodes_df.empty:
        st.info("No reference-expanded pages are available yet.")
    else:
        seed_roles = {"spec_definition", "scope_definition", "assembly_definition", "detail_reference", "section_sheet", "wall_type_schedule"}
        seed_df = tree_nodes_df[
            (tree_nodes_df.get("role", pd.Series(dtype=str)).isin(seed_roles))
            | (
                (tree_nodes_df.get("foam_seed_level", pd.Series(dtype=str)) == "high")
                & (tree_nodes_df.get("role", pd.Series(dtype=str)) != "measurement_page")
            )
        ]
        measurement_df = tree_nodes_df[tree_nodes_df.get("role", pd.Series(dtype=str)) == "measurement_page"]
        seed_cols = [
            "document_name",
            "canonical_sheet_id",
            "sheet_id",
            "sheet_title",
            "role",
            "seed_evidence_score",
            "foam_specific_evidence",
            "inclusion_path",
        ]
        measurement_cols = [
            "document_name",
            "canonical_sheet_id",
            "sheet_id",
            "sheet_title",
            "role",
            "measurement_likelihood_score",
            "final_selection_score",
            "graph_distance_from_seed",
            "connected_seed_pages",
            "inclusion_path",
            "measurement_guidance",
        ]
        left, right = st.columns(2)
        with left:
            st.markdown("**Seed / scope evidence pages**")
            st.dataframe(seed_df[[col for col in seed_cols if col in seed_df.columns]], use_container_width=True, hide_index=True)
        with right:
            st.markdown("**Predicted measurement pages**")
            st.dataframe(
                measurement_df[[col for col in measurement_cols if col in measurement_df.columns]],
                use_container_width=True,
                hide_index=True,
            )

    with st.expander("BidScope debug", expanded=False):
        st.caption(f"Graph expansion depth: {depth}")
        seed_debug = dataframe_from_records(
            [
                {
                    "document_name": page.document_name,
                    "page_num": page.page_num,
                    "sheet_id": page.sheet_id,
                    "filename_sheet_id": page.filename_sheet_id,
                    "extracted_sheet_id": page.extracted_sheet_id,
                    "canonical_sheet_id": page.canonical_sheet_id,
                    "sheet_title": page.sheet_title,
                    "role": page.role,
                    "foam_seed_level": page.foam_seed_level,
                    "foam_specific_evidence": ", ".join(page.foam_specific_evidence),
                    "generic_evidence": ", ".join(page.generic_evidence),
                    "relevance_score": page.relevance_score,
                }
                for page in sorted(pages, key=lambda item: item.relevance_score, reverse=True)[:25]
            ]
        )
        st.markdown("**Top seed candidates**")
        st.dataframe(seed_debug, use_container_width=True, hide_index=True)
        generic_only = dataframe_from_records(
            [
                {
                    "document_name": page.document_name,
                    "page_num": page.page_num,
                    "sheet_id": page.sheet_id,
                    "filename_sheet_id": page.filename_sheet_id,
                    "extracted_sheet_id": page.extracted_sheet_id,
                    "canonical_sheet_id": page.canonical_sheet_id,
                    "sheet_title": page.sheet_title,
                    "role": page.role,
                    "generic_evidence": ", ".join(page.generic_evidence),
                }
                for page in pages
                if page.foam_seed_level == "generic_only"
            ]
        )
        st.markdown("**Pages rejected as generic-only**")
        st.dataframe(generic_only, use_container_width=True, hide_index=True)
        sheet_confidence = dataframe_from_records(
            [
                {
                    "document_name": page.document_name,
                    "page_num": page.page_num,
                    "sheet_id": page.sheet_id,
                    "sheet_title": page.sheet_title,
                    "sheet_id_confidence": page.sheet_id_confidence,
                    "sheet_id_source": page.sheet_id_source,
                    "warnings": "; ".join(page.warnings),
                }
                for page in pages
            ]
        )
        st.markdown("**Sheet ID confidence**")
        st.dataframe(sheet_confidence, use_container_width=True, hide_index=True)
        unresolved_refs = edge_df[edge_df["type"].astype(str).str.contains("unresolved", na=False)] if not edge_df.empty and "type" in edge_df.columns else pd.DataFrame()
        st.markdown("**Unresolved references**")
        st.dataframe(unresolved_refs, use_container_width=True, hide_index=True)

    st.subheader("Relevant Sheets")
    if relevant_df.empty:
        st.info("No relevant trade scope sheets were identified. Review the selected trade profile or try OCR.")
    else:
        display_cols = [
            "document_name",
            "sheet_id",
            "filename_sheet_id",
            "extracted_sheet_id",
            "canonical_sheet_id",
            "sheet_title",
            "sheet_id_confidence",
            "page_num",
            "page_type",
            "foam_relevance",
            "foam_seed_level",
            "role",
            "relevance_score",
            "seed_evidence_score",
            "measurement_likelihood_score",
            "final_selection_score",
            "graph_distance_from_seed",
            "connected_seed_pages",
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
                    "filename_sheet_id": page.filename_sheet_id,
                    "extracted_sheet_id": page.extracted_sheet_id,
                    "canonical_sheet_id": page.canonical_sheet_id,
                    "sheet_title": page.sheet_title,
                    "sheet_id_confidence": page.sheet_id_confidence,
                    "sheet_id_source": page.sheet_id_source,
                    "role": page.role,
                    "foam_relevance": page.foam_relevance,
                    "foam_seed_level": page.foam_seed_level,
                    "relevance_score": page.relevance_score,
                    "seed_evidence_score": page.seed_evidence_score,
                    "measurement_likelihood_score": page.measurement_likelihood_score,
                    "final_selection_score": page.final_selection_score,
                    "graph_distance_from_seed": page.graph_distance_from_seed,
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
    with st.expander("Indexed pages not in expanded BidScope tree", expanded=False):
        st.caption(
            "These pages remained in the lightweight global index and reference graph, "
            "but were not connected to the current trade seed subgraph within the selected expansion depth."
        )
        st.dataframe(dataframe_from_records(indexed_not_included), use_container_width=True, hide_index=True)

    export_payload = build_export_payload(result, pages, analysis_mode=analysis_mode)
    takeoff_evaluation_for_export: dict[str, Any] | None = None
    st.subheader("Evaluate against completed takeoff export")
    st.caption(
        "Upload a completed STACK-style takeoff CSV to compare BidScope-predicted measurement pages "
        "against known takeoff pages. This is evaluation/training data only."
    )
    embedded_takeoff_csvs = inspection.takeoff_csvs or []
    if embedded_takeoff_csvs:
        st.markdown("**STACK takeoff CSVs found inside package**")
        st.dataframe(dataframe_from_records(embedded_takeoff_csvs), use_container_width=True, hide_index=True)
        for index, takeoff_csv in enumerate(embedded_takeoff_csvs, start=1):
            try:
                csv_payload = Path(str(takeoff_csv["file_path"])).read_bytes()
                evaluation = compare_foamscope_output_to_takeoff_export(
                    export_payload,
                    csv_payload,
                    trade_type=trade_type,
                )
            except Exception as exc:
                st.warning(f"Could not evaluate {takeoff_csv.get('filename')}: {type(exc).__name__}: {exc}")
                continue
            takeoff_evaluation_for_export = evaluation
            counts = evaluation["counts"]
            with st.expander(f"Embedded takeoff evaluation {index}: {takeoff_csv.get('filename')}", expanded=index == 1):
                e1, e2, e3, e4, e5 = st.columns(5)
                e1.metric("Expected pages", f"{counts['expected']:,}")
                e2.metric("Predicted pages", f"{counts['predicted']:,}")
                e3.metric("Matched pages", f"{counts['matched']:,}")
                e4.metric("Recall", f"{evaluation['recall']:.0%}")
                e5.metric("Precision", f"{evaluation['precision']:.0%}")
                st.markdown("**Top 25 predicted measurement pages**")
                st.dataframe(
                    dataframe_from_records(evaluation.get("top_predicted_measurement_pages", [])[:25]),
                    use_container_width=True,
                    hide_index=True,
                )
                st.markdown("**Missed pages**")
                st.dataframe(dataframe_from_records(evaluation["missed_pages"]), use_container_width=True, hide_index=True)
    takeoff_upload = st.file_uploader(
        "Completed takeoff CSV",
        type=["csv"],
        key="foamscope_takeoff_evaluation_csv",
    )
    if takeoff_upload is not None:
        try:
            evaluation = compare_foamscope_output_to_takeoff_export(
                export_payload,
                takeoff_upload.getvalue(),
                trade_type=trade_type,
            )
        except Exception as exc:
            st.error(f"Could not evaluate takeoff CSV: {type(exc).__name__}: {exc}")
        else:
            takeoff_evaluation_for_export = evaluation
            counts = evaluation["counts"]
            e1, e2, e3, e4, e5 = st.columns(5)
            e1.metric("Expected pages", f"{counts['expected']:,}")
            e2.metric("Selected pages", f"{counts['selected']:,}")
            e3.metric("Matched pages", f"{counts['matched']:,}")
            e4.metric("Recall", f"{evaluation['recall']:.0%}")
            e5.metric("Precision", f"{evaluation['precision']:.0%}")
            with st.expander("Takeoff evaluation detail", expanded=True):
                st.markdown("**Top 25 predicted measurement pages**")
                st.dataframe(
                    dataframe_from_records(evaluation.get("top_predicted_measurement_pages", [])[:25]),
                    use_container_width=True,
                    hide_index=True,
                )
                st.markdown("**Matched pages**")
                st.dataframe(dataframe_from_records(evaluation["matched_pages"]), use_container_width=True, hide_index=True)
                st.markdown("**Missed pages**")
                st.dataframe(dataframe_from_records(evaluation["missed_pages"]), use_container_width=True, hide_index=True)
                st.markdown("**Extra selected pages**")
                st.dataframe(dataframe_from_records(evaluation.get("extra_pages", evaluation["extra_selected_pages"])), use_container_width=True, hide_index=True)

    st.subheader("Exports")
    review_zip = build_bidscope_review_export_zip(
        export_payload,
        trade_profile=trade_profile,
        project_name=project_name,
        source_type=intake_mode,
        package_name=package_source,
        takeoff_evaluation=takeoff_evaluation_for_export,
    )
    st.download_button(
        "Export analysis summary for review",
        data=review_zip,
        file_name="bidscope_analysis_review.zip",
        mime="application/zip",
        help="Exports CSV/JSON/text review files only. Original PDFs, page images, and large binary files are not included.",
    )
    e1, e2 = st.columns(2)
    with e1:
        st.download_button(
            "Download JSON",
            data=json.dumps(export_payload, indent=2, default=str).encode("utf-8"),
            file_name="bidscope_ai_measurement_tree.json",
            mime="application/json",
        )
    with e2:
        csv_bytes = relevant_df.to_csv(index=False).encode("utf-8") if not relevant_df.empty else b""
        st.download_button(
            "Download Relevant Sheets CSV",
            data=csv_bytes,
            file_name="bidscope_ai_relevant_sheets.csv",
            mime="text/csv",
            disabled=relevant_df.empty,
        )
