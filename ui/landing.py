# landing.py
import time

import streamlit as st
from core.history import get_total_count


def render_landing():
    # FINN-lenke i midtkolonne
    left, mid, right = st.columns([1, 2, 1])
    with mid:
        st.subheader("Lim inn FINN-lenke")
        with st.form("landing_form", clear_on_submit=False):
            url = st.text_input(
                "", placeholder="www.finn.no", label_visibility="collapsed"
            )
            st.markdown('<div class="center-btn">', unsafe_allow_html=True)
            run = st.form_submit_button("Kjør analyse")
            st.markdown("</div>", unsafe_allow_html=True)

    # ⬇️ Full bredde under: divider + techdom teller
    st.divider()

    total_analyses = get_total_count()
    should_animate = not run

    st.markdown(
        """
        <style>
          .td-analytics-wrapper {
            display: flex;
            justify-content: center;
            padding: 1.5rem 0 3rem;
          }

          .td-analytics-box {
            position: relative;
            display: flex;
            flex-direction: column;
            align-items: center;
            width: min(100%, 420px);
            padding: 1.8rem 2.4rem;
            border-radius: 18px;
            background: rgba(8, 10, 18, 0.34);
            border: 1px solid rgba(102, 112, 140, 0.12);
            text-align: center;
            color: #f1f4ff;
            overflow: visible;
          }

          .td-analytics-label {
            font-size: 0.88rem;
            letter-spacing: 0.18em;
            text-transform: uppercase;
            opacity: 0.58;
          }

          .td-analytics-value {
            position: relative;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            font-size: clamp(3rem, 6.5vw, 4.6rem);
            font-weight: 600;
            letter-spacing: 0.035em;
            margin-top: 0.75rem;
            padding: 0.1rem 0.35rem;
            color: #f9fbff;
            line-height: 1;
            z-index: 0;
          }

          .td-analytics-value::before {
            content: "";
            position: absolute;
            inset: -40% -60%;
            background: radial-gradient(circle,
                         rgba(16, 44, 120, 0.36) 0%,
                         rgba(18, 38, 96, 0.30) 25%,
                         rgba(16, 28, 64, 0.20) 48%,
                         rgba(14, 22, 44, 0.12) 70%,
                         rgba(10, 16, 32, 0.04) 85%,
                         rgba(10, 14, 26, 0) 100%);
            filter: blur(26px);
            opacity: 0.5;
            border-radius: 50%;
            z-index: -1;
            transition: opacity 0.35s ease, transform 0.35s ease;
          }

          @media (max-width: 768px) {
            .td-analytics-box {
              border-radius: 14px;
              padding: 1.4rem;
            }
          }
        </style>
        """,
        unsafe_allow_html=True,
    )

    analytics_html = """
        <div class=\"td-analytics-wrapper\">
          <div class=\"td-analytics-box\">
            <div class=\"td-analytics-label\">Eiendommer analysert</div>
            <div class=\"td-analytics-value\">{value}</div>
          </div>
        </div>
        """

    counter_placeholder = st.empty()

    def _render_counter(count: int) -> None:
        pretty = f"{count:,}".replace(",", " ")
        counter_placeholder.markdown(
            analytics_html.format(value=pretty),
            unsafe_allow_html=True,
        )

    if should_animate and total_analyses > 0:
        steps = min(60, total_analyses)
        duration = 1.0
        for step in range(steps + 1):
            progress = step / steps
            value = int(round(progress * total_analyses))
            _render_counter(value)
            # short sleep to give the illusion av klient-side counter
            time.sleep(duration / steps)
    else:
        _render_counter(total_analyses)

    if run:
        url = (url or "").strip()
        if not url:
            st.warning("Lim inn en FINN-lenke først.")
            return
        st.session_state.update(
            {
                "listing_url": url,
                "params": {},
                "computed": None,
                "ai_text": "",
                "_first_compute_done": False,
                "_updating": False,
                "_queued_params": None,
                "_scraped_url": None,
                "_scraped_info": {},
                "page": "result",
            }
        )
        st.rerun()
