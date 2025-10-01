"""Streamlit application entrypoint for Techdom."""
from __future__ import annotations

import os
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import bootstrap  # noqa: F401

try:
    from dotenv import load_dotenv  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    def load_dotenv(*_args, **_kwargs):  # type: ignore
        return False

load_dotenv()

import streamlit as st
from PIL import Image

from bootstrap import ROOT
from apps.streamlit.views.header import render_header
from apps.streamlit.views.landing import render_landing
from apps.streamlit.views.result import render_result
from apps.streamlit.views.footer import render_footer

ASSETS_DIR = ROOT / "Assets"
os.chdir(ROOT)


def _resolve_page_icon() -> Image.Image | str:
    icon_path = ASSETS_DIR / "logo_64.png"
    if icon_path.exists():
        try:
            return Image.open(icon_path)
        except Exception:
            pass
    return "💼"


def _inject_css() -> None:
    for css_candidate in ("styles.css", "Assets/styles.css"):
        css_path = ROOT / css_candidate
        if css_path.exists():
            try:
                st.markdown(
                    f"<style>{css_path.read_text(encoding='utf-8')}</style>",
                    unsafe_allow_html=True,
                )
                break
            except Exception:
                continue


def get_secret(name: str, default: str = "") -> str:
    try:
        val = st.secrets.get(name)  # type: ignore[attr-defined]
        if val:
            return str(val)
    except Exception:
        pass
    return os.getenv(name, default) or default


def _ensure_session_defaults() -> None:
    st.session_state.setdefault("page", "landing")
    st.session_state.setdefault("listing_url", "")
    st.session_state.setdefault("params", {})
    st.session_state.setdefault("computed", {})
    st.session_state.setdefault("ai_text", "")
    st.session_state.setdefault("rent", None)
    st.session_state.setdefault("brutto_leie", 0)


def main() -> None:
    page_icon = _resolve_page_icon()
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
    _inject_css()
    _ensure_session_defaults()

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
