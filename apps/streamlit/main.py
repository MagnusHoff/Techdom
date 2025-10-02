"""Streamlit application entrypoint for Techdom."""
from __future__ import annotations

import streamlit as st

from apps.streamlit import runtime
from apps.streamlit.services.ui import (
    ensure_session_defaults,
    get_secret,
    inject_css,
    resolve_page_icon,
)
from apps.streamlit.views.footer import render_footer
from apps.streamlit.views.header import render_header
from apps.streamlit.views.landing import render_landing
from apps.streamlit.views.result import render_result

_bootstrap = runtime.ensure_bootstrap()
runtime.load_environment()
ROOT = _bootstrap.ROOT


def main() -> None:
    runtime.prepare_workdir(ROOT)

    page_icon = resolve_page_icon()
    st.set_page_config(page_title="Techdom.AI", page_icon=page_icon, layout="wide")
    st.markdown(
        """
<style>
  #MainMenu{visibility:hidden;}
  footer{visibility:hidden;}
  header{visibility:hidden;}
</style>
""",
        unsafe_allow_html=True,
    )
    inject_css()
    ensure_session_defaults()

    render_header()

    page = st.session_state.get("page", "landing")
    if page == "landing":
        render_landing()
    elif page == "result":
        render_result()
    else:
        st.error(f"Ukjent side: {page}")

    render_footer()


__all__ = ["main", "get_secret"]

if __name__ == "__main__":
    main()
