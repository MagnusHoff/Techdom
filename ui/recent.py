import streamlit as st
from core.history import get_recent


def _open_analysis(finn_url: str):
    st.session_state["listing_url"] = finn_url
    st.session_state["_scraped_url"] = None
    st.session_state["_first_compute_done"] = False
    st.session_state["_history_logged"] = False
    st.session_state["page"] = "result"
    st.rerun()


def render_recent_analyses(n: int = 8, columns: int = 4) -> None:
    items = get_recent(n)
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

    for i, rec in enumerate(items[: columns * 2]):  # 2 rows max
        with cols[i % columns]:
            with st.container(border=True):
                # Build the whole card in ONE markdown call (prevents ghost boxes)
                img = rec.get("image") or ""
                ts = (rec.get("ts") or "")[:16].replace("T", " ")
                title = rec.get("title") or "Uten tittel"
                price = rec.get("price")
                price_html = (
                    f"<div class='price'>Pris: {price:,.0f} kr</div>".replace(",", " ")
                    if isinstance(price, (int, float)) and price > 0
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

                # Actions (buttons) stay at the bottom
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("Åpne analyse", key=f"open_{rec.get('id')}"):
                        _open_analysis(rec.get("finn_url"))
                with c2:
                    st.link_button(
                        "FINN-annonse", rec.get("finn_url"), type="secondary"
                    )
