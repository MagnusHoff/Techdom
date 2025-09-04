# ui_header.py
import streamlit as st

def render_header():
    left, right = st.columns([6, 2])
    with left:
        if st.button("Techdom.AI – eiendomsanalyse", use_container_width=False):
            st.session_state.update({
                "page": "landing",
                "listing_url": "",
                "params": {},
                "computed": None,
                "ai_text": ""
            })
            st.rerun()
    with right:
        if st.button("Ny analyse", use_container_width=True):
            st.session_state.update({
                "page": "landing",
                "listing_url": "",
                "params": {},
                "computed": None,
                "ai_text": ""
            })
            st.rerun()
    st.markdown("<hr>", unsafe_allow_html=True)