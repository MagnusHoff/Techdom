# app.py
from __future__ import annotations

# --- Robust oppstart: riktig arbeidskatalog + PYTHONPATH ---
from pathlib import Path
import sys, os

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # sikre at relative stier (data/*, Assets/*, styles.css) funker

# --- Valgfri .env-støtte (lokalt) ---
try:
    from dotenv import load_dotenv  # type: ignore
except Exception:

    def load_dotenv(*args, **kwargs):
        return False


load_dotenv()  # gjør ingenting hvis pakken ikke finnes

# --- Streamlit oppsett ---
import streamlit as st

# Last ikon trygt (fallback til emoji hvis savnes)
from PIL import Image

icon_path = ROOT / "Assets" / "logo_64.png"  # merk stor A
page_icon = "💼"
try:
    if icon_path.exists():
        page_icon = Image.open(icon_path)
except Exception:
    pass

st.set_page_config(page_title="Techdom.AI", page_icon=page_icon, layout="wide")

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


# --- Hjelper for secrets/nøkler (funksjonerer i Cloud/Render/locally) ---
def get_secret(name: str, default: str = "") -> str:
    # Streamlit Cloud: st.secrets, ellers miljøvariabel
    try:
        val = st.secrets.get(name)  # type: ignore[attr-defined]
        if val:
            return str(val)
    except Exception:
        pass
    return os.getenv(name, default) or default


# Eksempel bruk:
# OPENAI_API_KEY = get_secret("OPENAI_API_KEY", "")

# --- UI imports (etter at PATH/arbeidskatalog er riktig) ---
from ui.header import render_header
from ui.landing import render_landing
from ui.result import render_result
from ui.footer import render_footer

# --- CSS (frivillig) ---
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
            pass

# --- Shared state defaults ---
st.session_state.setdefault("page", "landing")
st.session_state.setdefault("listing_url", "")
st.session_state.setdefault("params", {})
st.session_state.setdefault("computed", {})
st.session_state.setdefault("ai_text", "")
st.session_state.setdefault("rent", None)  # brukt av inline rent input
st.session_state.setdefault("brutto_leie", 0)  # faktisk feltverdi

# --- Header ---
render_header()

# --- Routing ---
page = st.session_state.get("page", "landing")
if page == "landing":
    render_landing()
elif page == "result":
    render_result()
else:
    st.error(f"Ukjent side: {page}")

render_footer()
