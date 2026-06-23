from __future__ import annotations

import json
from typing import Any

import pandas as pd
import streamlit as st

from indexing.graph_builder import build_reference_graph, expand_neighbors, graph_edges_table, high_confidence_nodes
from indexing.page_classifier import classify_pages
from indexing.reference_extractor import attach_references
from indexing.sheet_indexer import index_sheets
from ingest.package_ingest import (
    MB,
    PdfDocumentInput,
    inspect_uploaded_package,
    materialize_selected_documents,
    normalize_pdf_document,
)
from ingest.pdf_ingest import PageRecord, ingest_pdf
from takeoff.insulation_scope_tree import build_measurement_tree, relevant_pages_table


def dataframe_from_records(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows) if rows else pd.DataFrame()


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
                "guessed_document_type": candidate.document_type,
                "compressed_size_mb": round(candidate.compressed_size / MB, 2),
                "uncompressed_size_mb": round(candidate.uncompressed_size / MB, 2),
            }
        )
    return dataframe_from_records(rows)


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
    seeds = high_confidence_nodes(pages)
    selected_nodes = expand_neighbors(graph, seeds, depth=depth) if seeds else set()
    if not selected_nodes:
        selected_nodes = {page.global_page_id for page in pages if page.global_page_id}
    tree = build_measurement_tree(pages, graph, selected_nodes)
    return {
        "documents": documents,
        "pages": pages,
        "graph": graph,
        "selected_nodes": selected_nodes,
        "tree": tree,
        "relevant_rows": relevant_pages_table(pages, selected_nodes),
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
        depth = st.slider("Reference expansion depth", min_value=0, max_value=3, value=2)
        use_ocr = st.checkbox(
            "Use OCR fallback for sparse pages",
            value=True,
            help="Uses pytesseract only when embedded PDF text is sparse and OCR is installed locally.",
        )
        st.markdown("**No paid API key required.**")
        st.caption("TODO: optional LLM summaries could later explain ambiguous scope evidence.")

    uploaded_files = st.file_uploader(
        "Upload construction PDFs or ZIP bid packages",
        type=["pdf", "zip"],
        accept_multiple_files=True,
    )
    if not uploaded_files:
        st.info("Upload one or more plan/spec PDFs or ZIP files containing PDFs to begin.")
        return

    inspection = inspect_uploaded_package(uploaded_files)
    if inspection.warnings:
        with st.expander("Package warnings", expanded=True):
            for warning in inspection.warnings:
                st.warning(warning)
    if not inspection.candidates:
        st.warning("No PDF documents were found in the uploaded files.")
        return

    st.subheader("PDF Document Selection")
    st.caption("Review the package contents and choose the PDFs FoamScope should analyze. ZIP PDFs are extracted only after selection.")
    candidate_df = candidates_table(inspection.candidates)
    edited_candidates = st.data_editor(
        candidate_df,
        use_container_width=True,
        hide_index=True,
        disabled=[
            "candidate_id",
            "filename",
            "source",
            "source_kind",
            "guessed_document_type",
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
    selected_uncompressed = sum(candidate.uncompressed_size for candidate in inspection.candidates if candidate.candidate_id in selected_ids)
    st.caption(
        f"{len(selected_ids):,} of {len(inspection.candidates):,} PDFs selected. "
        f"Selected uncompressed size: {selected_uncompressed / MB:,.1f} MB."
    )
    if not selected_ids:
        st.info("Select at least one PDF to analyze.")
        return
    if not st.button("Analyze selected documents", type="primary"):
        return

    package = materialize_selected_documents(inspection, selected_ids)
    if package.warnings:
        with st.expander("Selection/extraction warnings", expanded=True):
            for warning in package.warnings:
                st.warning(warning)
    if not package.documents:
        st.warning("No selected PDF documents could be extracted for analysis.")
        return

    st.subheader("Selected Documents")
    st.dataframe(documents_table(package.documents), use_container_width=True, hide_index=True)

    with st.spinner("Reading PDF pages, scoring insulation relevance, and building package reference graph..."):
        try:
            result = analyze_documents(package.documents, depth=depth, use_ocr=use_ocr, package_warnings=package.warnings)
        except Exception as exc:
            st.error(f"Could not analyze PDF package: {type(exc).__name__}: {exc}")
            return

    pages = result["pages"]
    tree = result["tree"]
    relevant_df = dataframe_from_records(result["relevant_rows"])
    edge_df = dataframe_from_records(result["edge_rows"])

    high_count = sum(1 for page in pages if page.relevance_level == "high")
    medium_count = sum(1 for page in pages if page.relevance_level == "medium")
    selected_count = len(result["selected_nodes"])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Documents", f"{len(package.documents):,}")
    c2.metric("Pages", f"{len(pages):,}")
    c3.metric("High Confidence", f"{high_count:,}")
    c4.metric("Selected for Review", f"{selected_count:,}")
    m1, m2 = st.columns(2)
    m1.metric("Medium Confidence", f"{medium_count:,}")
    m2.metric("Warnings", f"{len(result['warnings']):,}")

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
                }
                for page in pages
            ]
        )
        st.dataframe(all_pages, use_container_width=True, hide_index=True)

    export_payload = {
        "documents": [document.to_dict() for document in result["documents"]],
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
