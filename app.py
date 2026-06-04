from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from jobscan.scan import scan_root, records_as_dicts

st.set_page_config(page_title="SharePoint Job Folder Scanner + Graph", layout="wide")
st.title("SharePoint Job Folder Scanner + Graph")
st.caption("Local prototype: scan exported SharePoint job folders and build a job index.")

root = st.text_input("Folder to scan", value="examples/sample_export")

if st.button("Scan folders"):
    records = scan_root(Path(root))
    rows = records_as_dicts(records)
    df = pd.DataFrame(rows)
    st.success(f"Scanned {len(df)} job folder(s)")

    if not df.empty:
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Jobs", len(df))
        k2.metric("Total Estimate $", f"${df['final_price'].fillna(0).sum():,.0f}")
        k3.metric("Invoices $", f"${df['invoice_amount'].fillna(0).sum():,.0f}")
        k4.metric("Photos", int(df['photo_count'].fillna(0).sum()))

        st.dataframe(df, use_container_width=True)
        st.download_button("Download CSV", df.to_csv(index=False), "job_index.csv", "text/csv")
    else:
        st.warning("No job folders found.")
