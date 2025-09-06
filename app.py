from pathlib import Path
from PIL import Image
import streamlit as st

icon_path = Path(__file__).parent / "Assets" / "logo_64.png"  # merk stor A
icon_img = Image.open(icon_path)

st.set_page_config(page_title="Techdom.AI", page_icon=icon_img, layout="wide")

# Skjul Streamlit-chrome
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
