# app.py
import streamlit as st
from ui.header import render_header
from ui.landing import render_landing
from ui.result import render_result

st.set_page_config(page_title="Techdom.AI", page_icon="🏠", layout="wide")

# app.py
import streamlit as st

# Last inn CSS
try:
    with open("styles.css") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
except FileNotFoundError:
    pass

# init shared state
st.session_state.setdefault("page", "landing")
st.session_state.setdefault("listing_url", "")
st.session_state.setdefault("params", {})
st.session_state.setdefault("computed", None)
st.session_state.setdefault("ai_text", "")

# always show the header
render_header()

# route between pages
if st.session_state["page"] == "landing":
    render_landing()
elif st.session_state["page"] == "result":
    render_result()