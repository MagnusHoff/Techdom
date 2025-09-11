# ui/recent.py
import streamlit as st
from core.history import get_recent


def _open_analysis(finn_url: str):
    # legg URL i state og hopp til resultatsiden
    st.session_state["listing_url"] = finn_url
    # nullstill noe state så resultatsiden scraper på nytt
    st.session_state["_scraped_url"] = None
    st.session_state["_first_compute_done"] = False
    st.session_state["_history_logged"] = False
    st.switch_page("ui/result.py")


def render_recent_analyses(n: int = 6) -> None:
    recent = get_recent(n)
    if not recent:
        st.caption("Ingen analyser enda.")
        return

    st.markdown("### Siste analyser")
    cols = st.columns(3, gap="large")

    for i, rec in enumerate(recent):
        with cols[i % 3]:
            with st.container(border=True):
                # Bilde på toppen (om tilgjengelig)
                if rec.get("image"):
                    st.image(rec["image"], use_container_width=True)

                title = rec.get("title") or "Uten tittel"
                ts = (rec.get("ts") or "")[:16].replace("T", " ")
                price = rec.get("price")
                finn_url = rec.get("finn_url")
                aid = rec.get("id")

                st.caption(ts)
                st.markdown(f"**{title}**")  # Kun tittel (adressen)
                if isinstance(price, (int, float)) and price > 0:
                    st.write(f"Pris: {price:,.0f} kr".replace(",", " "))

                c1, c2 = st.columns([1, 1])
                with c1:
                    if st.button("Åpne analyse", key=f"open_{aid}"):
                        _open_analysis(finn_url)
                with c2:
                    if finn_url:
                        st.link_button("FINN-annonse", finn_url, type="secondary")
