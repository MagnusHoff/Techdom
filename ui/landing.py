import streamlit as st
from ui.recent import render_recent_analyses


def render_landing():
    # Midtstill alt med kolonner
    left, mid, right = st.columns([1, 2, 1])

    with mid:
        st.subheader("Lim inn FINN-lenke")

        with st.form("landing_form", clear_on_submit=False):
            url = st.text_input(
                "",
                placeholder="www.finn.no",
                label_visibility="collapsed",
            )
            st.markdown('<div class="center-btn">', unsafe_allow_html=True)
            run = st.form_submit_button("Kjør analyse")
            st.markdown("</div>", unsafe_allow_html=True)

        # ⬇️ Vis de 6 siste analysene + DEV-knapp
        st.divider()
        render_recent_analyses(6)

        if run:
            url = (url or "").strip()
            if not url:
                st.warning("Lim inn en FINN-lenke først.")
                return

            # klargjør state for resultatsiden
            st.session_state.update(
                {
                    "listing_url": url,
                    "params": {},
                    "computed": None,
                    "ai_text": "",
                    "_first_compute_done": False,  # ikke auto-kjør
                    "_updating": False,
                    "_queued_params": None,
                    "_scraped_url": None,
                    "_scraped_info": {},
                    "page": "result",
                }
            )
            st.rerun()
