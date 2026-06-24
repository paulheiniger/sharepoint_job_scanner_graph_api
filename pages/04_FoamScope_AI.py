from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from jobscan.env import load_project_env

load_project_env()

import streamlit as st

from foamscope_ui import render_foamscope_page


st.set_page_config(page_title="BidScope AI", layout="wide")
render_foamscope_page()
