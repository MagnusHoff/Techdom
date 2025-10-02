from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import streamlit as st
from PIL import Image


def _assets_dir() -> Path:
    from bootstrap import ROOT  # Lazy import; runtime ensures availability.

    return ROOT / "Assets"


def resolve_page_icon() -> Image.Image | str:
    icon_path = _assets_dir() / "logo_64.png"
    if icon_path.exists():
        try:
            return Image.open(icon_path)
        except Exception:
            pass
    return "💼"


def inject_css() -> None:
    from bootstrap import ROOT

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


def ensure_session_defaults() -> None:
    st.session_state.setdefault("page", "landing")
    st.session_state.setdefault("listing_url", "")
    st.session_state.setdefault("params", {})
    st.session_state.setdefault("computed", {})
    st.session_state.setdefault("ai_text", "")
    st.session_state.setdefault("rent", None)
    st.session_state.setdefault("brutto_leie", 0)


__all__ = [
    "ensure_session_defaults",
    "get_secret",
    "inject_css",
    "resolve_page_icon",
]
