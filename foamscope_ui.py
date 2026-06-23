from __future__ import annotations

import json
from typing import Any

import pandas as pd
import streamlit as st

from indexing.graph_builder import build_reference_graph, expand_neighbors, graph_edges_table, high_confidence_nodes
from indexing.page_classifier import classify_pages
from indexing.reference_extractor import attach_references
from indexing.sheet_indexer import index_sheets
from ingest.pdf_ingest import ingest_pdf
from takeoff.insulation_scope_tree import build_measurement_tree, relevant_pages_table


def dataframe_from_records(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def analyze_pdf(pdf_bytes: bytes, *, depth: int, use_ocr: bool) -> dict[str, Any]:
    pages = ingest_pdf(pdf_bytes, ocr_sparse_pages=use_ocr)
    pages = index_sheets(pages)
    pages = attach_references(pages)
    pages = classify_pages(pages)
    graph = build_reference_graph(pages)
    seeds = high_confidence_nodes(pages)
    selected_nodes = expand_neighbors(graph, seeds, depth=depth) if seeds else set()
    if not selected_nodes:
        selected_nodes = {node for node in (page.sheet_number or f"page-{page.page_number}" for page in pages) if node}
    tree = build_measurement_tree(pages, graph, selected_nodes)
    return {
        "pages": pages,
        "graph": graph,
        "selected_nodes": selected_nodes,
        "tree": tree,
        "relevant_rows": relevant_pages_table(pages, selected_nodes),
        "edge_rows": graph_edges_table(graph),
    }


def render_foamscope_page() -> None:
    st.title("FoamScope AI")
    st.caption(
        "Upload a construction plan/spec PDF to identify spray-foam insulation scope sheets, "
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

    uploaded = st.file_uploader("Upload construction PDF", type=["pdf"])
    if uploaded is None:
        st.info("Upload a plan/spec PDF to begin.")
        return

    with st.spinner("Reading PDF pages, scoring insulation relevance, and building reference graph..."):
        try:
            result = analyze_pdf(uploaded.getvalue(), depth=depth, use_ocr=use_ocr)
        except Exception as exc:
            st.error(f"Could not analyze PDF: {type(exc).__name__}: {exc}")
            return

    pages = result["pages"]
    tree = result["tree"]
    relevant_df = dataframe_from_records(result["relevant_rows"])
    edge_df = dataframe_from_records(result["edge_rows"])

    high_count = sum(1 for page in pages if page.relevance_level == "high")
    medium_count = sum(1 for page in pages if page.relevance_level == "medium")
    selected_count = len(result["selected_nodes"])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Pages", f"{len(pages):,}")
    c2.metric("High Confidence", f"{high_count:,}")
    c3.metric("Medium Confidence", f"{medium_count:,}")
    c4.metric("Selected for Review", f"{selected_count:,}")

    st.warning("FoamScope AI produces an estimator-reviewed measurement map. It does not calculate a final bid.")

    st.subheader("Relevant Sheets")
    if relevant_df.empty:
        st.info("No relevant foam insulation sheets were identified. Review keyword configs or try OCR.")
    else:
        display_cols = [
            "page_number",
            "sheet_number",
            "sheet_title",
            "role",
            "relevance_level",
            "relevance_score",
            "needs_measurement",
            "evidence",
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
                    "page_number": page.page_number,
                    "sheet_number": page.sheet_number,
                    "sheet_title": page.sheet_title,
                    "role": page.role,
                    "relevance_level": page.relevance_level,
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
        "measurement_tree": tree,
        "relevant_sheets": result["relevant_rows"],
        "graph_edges": result["edge_rows"],
        "page_count": len(pages),
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
