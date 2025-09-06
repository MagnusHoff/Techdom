# app.py — clean baseline (no sidebar comps button)

import streamlit as st

st.set_page_config(page_title="Techdom", layout="wide")

# Hide Streamlit's default menu & footer
HIDE = """
    <style>
      #MainMenu {visibility: hidden;}
      footer {visibility: hidden;}
      header {visibility: hidden;}
    </style>
"""
st.markdown(HIDE, unsafe_allow_html=True)

st.set_page_config(
    page_title="Techdom.AI",
    page_icon="logo.png",  # ligger i rot, så ingen assets/-mappe trengs
    layout="wide",
)

from dotenv import load_dotenv

load_dotenv()

from ui.header import render_header
from ui.landing import render_landing
from ui.result import render_result


# CSS
try:
    with open("styles.css") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
except FileNotFoundError:
    pass

# Shared state
st.session_state.setdefault("page", "landing")
st.session_state.setdefault("listing_url", "")
st.session_state.setdefault("params", {})
st.session_state.setdefault("computed", {})
st.session_state.setdefault("ai_text", "")
st.session_state.setdefault("rent", None)  # used by inline rent input
st.session_state.setdefault("brutto_leie", 0)  # the actual field value

render_header()

# Route
if st.session_state["page"] == "landing":
    render_landing()
elif st.session_state["page"] == "result":
    render_result()
else:
    st.error(f"Ukjent side: {st.session_state['page']}")
