from __future__ import annotations

import streamlit as st

from foamscope_ui import render_foamscope_page


st.set_page_config(page_title="FoamScope AI", layout="wide")


def main() -> None:
    render_foamscope_page()


if __name__ == "__main__":
    main()
