# ui/recent.py
from __future__ import annotations

import streamlit as st
from typing import Any, Dict, List, Optional, cast
from techdom.domain.history import get_recent


def _open_analysis(finn_url: str) -> None:
    st.session_state["listing_url"] = finn_url
    st.session_state["_scraped_url"] = None
    st.session_state["_first_compute_done"] = False
    st.session_state["_history_logged"] = False
    st.session_state["page"] = "result"
    st.rerun()


def _as_str(v: Any, default: str = "") -> str:
    """Trygt konverter til str, ellers default."""
    if isinstance(v, str):
        return v
    if v is None:
        return default
    try:
        return str(v)
    except Exception:
        return default


def _as_number(v: Any) -> Optional[float]:
    """Returner float hvis mulig, ellers None."""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        t = v.replace(" ", "").replace("\u00a0", "").replace(",", ".")
        try:
            return float(t)
        except Exception:
            return None
    return None


def render_recent_analyses(n: int = 8, columns: int = 4) -> None:
    raw_items = get_recent(n)
    # Anta liste av dict, men beskytt oss i tilfelle
    items: List[Dict[str, Any]] = []
    if isinstance(raw_items, list):
        for it in raw_items:
            if isinstance(it, dict):
                items.append(cast(Dict[str, Any], it))

    if not items:
        st.caption("Ingen analyser enda.")
        return

    # Layout + card CSS
    st.markdown(
        """
        <style>
          .block-container { max-width: 1400px; }

          /* Tighter gaps */
          [data-testid="column"] { padding-right: .5rem; padding-left: .5rem; }
          .stHorizontalBlock { margin-bottom: .5rem !important; }

          /* Kill the top padding from bordered st.container for our cards */
          [data-testid="stContainer"] .td-card { margin-top: -12px; }

          /* Card */
          .td-card {
            display: flex;
            flex-direction: column;
            height: 440px;                 /* uniform height */
            padding: 12px;
          }

          .td-thumb {
            width: 100%;
            height: 190px;                 /* uniform thumb height */
            border-radius: 8px;
            background-position: center;
            background-size: cover;
            background-repeat: no-repeat;
            margin: 0 0 .6rem 0;           /* no extra top margin */
          }

          .td-meta h4 { margin: 0 0 .25rem 0; }
          .td-meta .price { opacity:.9; margin:.15rem 0 .5rem 0; }

          .td-spacer { margin-top: auto; } /* push actions to bottom */
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("### Andre analyser")
    cols = st.columns(columns, gap="small")

    # Vis maks 2 rader
    max_cards = columns * 2
    for i, rec in enumerate(items[:max_cards]):
        with cols[i % columns]:
            with st.container(border=True):
                # Les felt trygt
                img = _as_str(rec.get("image"))
                ts = _as_str(rec.get("ts"))[:16].replace("T", " ")
                title = _as_str(rec.get("title"), "Uten tittel")
                price_num = _as_number(rec.get("price"))

                price_html = (
                    f"<div class='price'>Pris: {price_num:,.0f} kr</div>".replace(
                        ",", " "
                    )
                    if (price_num is not None and price_num > 0)
                    else ""
                )

                card_html = f"""
                <div class="td-card">
                  <div class="td-thumb" style="background-image:url('{img}');"></div>
                  <div class="td-meta">
                    <div style="opacity:.7;font-size:12px;margin-bottom:4px;">{ts}</div>
                    <h4>{title}</h4>
                    {price_html}
                  </div>
                  <div class="td-spacer"></div>
                </div>
                """
                st.markdown(card_html, unsafe_allow_html=True)

                # Actions nederst
                finn_url = _as_str(rec.get("finn_url"))
                c1, c2 = st.columns(2)
                with c1:
                    if finn_url:
                        if st.button(
                            "Åpne analyse", key=f"open_{_as_str(rec.get('id'))}"
                        ):
                            _open_analysis(finn_url)
                    else:
                        # Manglende URL: vis disabled knapp
                        st.button(
                            "Åpne analyse",
                            key=f"open_{_as_str(rec.get('id'))}",
                            disabled=True,
                        )
                with c2:
                    if finn_url:
                        st.link_button("FINN-annonse", finn_url, type="secondary")
                    else:
                        st.caption("Mangler FINN-URL")
