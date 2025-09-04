# app2.py

import os, math, json, re, time, requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import streamlit as st
from openai import OpenAI

load_dotenv()

# ---------------- Page setup ----------------
st.set_page_config(
    page_title="Techdom.AI – Eiendomsanalyse",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="collapsed",
)

DARK_BLUE       = "#0b1f55"
DARK_BLUE_HOVER = "#0d2668"

# ---------------- CSS ----------------
st.markdown(f"""
<style>
/* Skjul standardmeny + sidebar */
#MainMenu, footer, header, [data-testid="stSidebar"], [data-testid="collapsedControl"] {{
  display:none !important;
}}
/* Gi plass under sticky header */
.block-container {{ padding-top: 92px !important; }}

/* Sticky header (svart bakgrunn), med mørkeblå stripe helt øverst */
.app-sticky-wrap {{
  position:fixed; top:0; left:0; right:0; z-index:9999;
}}
.app-blue-strip {{
  height:6px; width:100%; background:{DARK_BLUE};
}}
.app-header {{
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  background: rgba(2, 6, 23, 0.85); /* nesten sort */
  padding:10px 0 8px 0;
  border-bottom:1px solid rgba(148,163,184,0.28);
}}
.app-header-inner {{
  max-width:1200px; margin:0 auto; padding:0 12px;
  display:flex; align-items:center; justify-content:space-between; gap:12px;
}}
.app-title {{
  background:transparent; border:none; color:#fff; font-size:18px; font-weight:800;
  letter-spacing:.2px; cursor:pointer; padding:6px 8px;
  transition: transform .12s ease, opacity .12s ease;
}}
.app-title:hover {{ transform:translateY(-1px) scale(1.02); opacity:1; }}

.app-header-right {{ display:flex; align-items:center; gap:10px; }}
.header-btn {{
  background:{DARK_BLUE}; color:#fff; border:1px solid {DARK_BLUE};
  border-radius:10px; padding:8px 12px; font-weight:700; cursor:pointer;
  transition: transform .12s ease, background .12s ease, box-shadow .12s ease;
}}
.header-btn:hover {{ background:{DARK_BLUE_HOVER}; transform:translateY(-1px) scale(1.02);
  box-shadow:0 6px 18px rgba(13,38,104,.25);
}}
.header-badge {{
  border:1px solid {DARK_BLUE}; color:#fff; border-radius:999px;
  padding:6px 10px; font-weight:700; font-size:13px; opacity:.95;
}}

/* Primærknapp-stil (Kjør analyse / Oppdater) */
.stButton > button, .stFormSubmitButton > button {{
  background:{DARK_BLUE} !important; color:#fff !important; border:1px solid {DARK_BLUE} !important;
  border-radius:10px !important; padding:10px 16px !important; font-weight:700 !important;
  transition: transform .12s ease, background .12s ease, box-shadow .12s ease;
}}
.stButton > button:hover, .stFormSubmitButton > button:hover {{
  background:{DARK_BLUE_HOVER} !important; transform:translateY(-1px) scale(1.02);
  box-shadow:0 6px 18px rgba(13,38,104,.25);
}}

/* Tekstinput – mørkeblå outline (landing) */
.stTextInput > div > div > input {{
  border-radius:10px !important;
  border:1px solid {DARK_BLUE} !important;
  outline:1px solid {DARK_BLUE} !important;
}}
.stTextInput > div > div > input:focus {{
  border:2px solid {DARK_BLUE} !important;
  outline:2px solid {DARK_BLUE} !important;
}}

/* Blå outline rundt AI-kortet */
.card-blue {{
  border:1px solid {DARK_BLUE}; border-radius:12px; padding:16px;
}}

/* Spinner-container (brukes lite nå) */
[data-testid="stSpinner"] > div {{
  border:1px solid {DARK_BLUE}; border-radius:10px; padding:8px 10px;
}}

/* Seksjonstittel for Parametre */
.section-title {{
  font-weight:800; font-size:16px; margin:0 0 8px 0;
}}
</style>
""", unsafe_allow_html=True)

# ---------------- Sticky header (med state) ----------------
if "page" not in st.session_state:
    st.session_state["page"] = "landing"
if "listing_url" not in st.session_state:
    st.session_state["listing_url"] = ""
if "busy" not in st.session_state:
    st.session_state["busy"] = False
if "computed" not in st.session_state:
    st.session_state["computed"] = None
if "ai_text" not in st.session_state:
    st.session_state["ai_text"] = ""

# Render header
st.markdown('<div class="app-sticky-wrap">', unsafe_allow_html=True)
st.markdown('<div class="app-blue-strip"></div>', unsafe_allow_html=True)
st.markdown('<div class="app-header"><div class="app-header-inner">', unsafe_allow_html=True)

c1, c2, c3 = st.columns([3, 3, 2])
with c1:
    if st.button("Techdom.AI – eiendomsanalyse", key="home_btn__top", help="Til start", use_container_width=False):
        st.session_state.update({"page":"landing", "listing_url":"", "computed":None, "ai_text":""})
        st.rerun()
with c2:
    # midt – loader badge
    if st.session_state["busy"]:
        st.markdown('<div class="header-badge" style="text-align:center;">Kjører AI-analyse …</div>', unsafe_allow_html=True)
with c3:
    if st.button("Ny analyse", key="new_btn__top", use_container_width=True):
        st.session_state.update({"page":"landing", "listing_url":"", "computed":None, "ai_text":""})
        st.rerun()

st.markdown('</div></div></div>', unsafe_allow_html=True)  # close header

# ---------------- Beregninger + AI ----------------
def format_number(n, decimals=0):
    if n is None: return "—"
    try: x = float(n)
    except: return str(n)
    s = f"{x:,.{decimals}f}".replace(",", " ")
    return s.split(".")[0] if decimals == 0 else s

def monthly_payment(principal, annual_rate_pct, n_years):
    if principal <= 0: return 0.0
    r = (annual_rate_pct/100.0)/12.0
    n = int(n_years*12)
    return principal/n if r == 0 else principal*r*(1+r)**n/((1+r)**n-1)

def compute_metrics(price, equity, interest, term_years, rent, hoa, maint_pct, vacancy_pct, other_costs):
    loan = max(price - equity, 0)
    m_payment = monthly_payment(loan, interest, term_years)
    maint = rent * (maint_pct/100.0)
    vacancy = rent * (vacancy_pct/100.0)
    total_monthly_costs = m_payment + hoa + maint + vacancy + other_costs
    cashflow = rent - total_monthly_costs
    noi_month = rent - (hoa + maint + vacancy + other_costs)
    noi_year = noi_month * 12
    invested_equity = equity if equity > 0 else 1
    annual_rate = interest/100.0
    approx_interest_year = loan * annual_rate
    principal_reduction_year = max(m_payment*12 - approx_interest_year, 0)
    total_equity_return_pct = ((cashflow*12) + principal_reduction_year)/invested_equity*100.0
    factor = 1.0 - (maint_pct/100.0) - (vacancy_pct/100.0)
    break_even = (m_payment + hoa + other_costs)/factor if factor > 0 else float("inf")
    return {
        "loan": loan, "m_payment": m_payment, "maint": maint, "vacancy": vacancy,
        "total_costs": total_monthly_costs, "cashflow": cashflow, "noi_year": noi_year,
        "break_even": break_even, "principal_reduction_year": principal_reduction_year,
        "total_equity_return_pct": total_equity_return_pct,
        "legacy_net_yield_pct": (noi_year/invested_equity)*100.0
    }

def local_explain(inputs, m):
    vurdering = "ok"
    if m['total_equity_return_pct'] >= 7: vurdering = "god"
    if m['total_equity_return_pct'] < 3: vurdering = "svak"
    risiko, tiltak = [], []
    if inputs['interest'] > 6: risiko.append("høy rente")
    if m['cashflow'] < 0: risiko.append("negativ månedlig cashflow")
    if m['cashflow'] < 0: tiltak.append("øk leie eller reduser kostnader")
    if inputs['interest'] > 5: tiltak.append("forhandle rente/fast rente")
    if inputs['equity']/max(inputs['price'],1) < 0.20: tiltak.append("øke egenkapitalen")
    out = [
        f"**Vurdering:** {vurdering}. ROE **{m['total_equity_return_pct']:.1f}%**, "
        f"cashflow **{format_number(m['cashflow'])} kr/mnd**, break-even **{format_number(m['break_even'])} kr/mnd**."
    ]
    if risiko: out.append("**Risikofaktorer:** " + ", ".join(risiko) + ".")
    if tiltak: out.append("**Tiltak:** " + ", ".join(tiltak) + ".")
    return "\n\n".join(out)

def get_openai_key() -> str:
    k = os.getenv("OPENAI_API_KEY")
    if k: return k.strip()
    try: return st.secrets["OPENAI_API_KEY"]
    except Exception: return ""

# ---------------- Scraper ----------------
def _num(s):
    if s is None: return None
    t = re.sub(r"[^0-9,\.]", "", str(s)).replace(".", "").replace(",", ".")
    try: return int(round(float(t)))
    except:
        t2 = re.sub(r"\D", "", str(s))
        return int(t2) if t2.isdigit() else None

def fetch_listing_meta(url: str) -> dict:
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Techdom.AI fetch)"}
        html = requests.get(url, headers=headers, timeout=12).text
        soup = BeautifulSoup(html, "html.parser")
        og_title = soup.find("meta", property="og:title")
        og_image = soup.find("meta", property="og:image")
        return {"title": og_title.get("content") if og_title else None,
                "image": og_image.get("content") if og_image else None,
                "html": html}
    except Exception:
        return {"title": None, "image": None, "html": ""}

def _parse_ld_json(soup: BeautifulSoup) -> dict:
    data = {}
    for tag in soup.find_all("script", type="application/ld+json"):
        try: blob = json.loads(tag.string or "{}")
        except Exception: continue
        items = blob if isinstance(blob, list) else [blob]
        for item in items:
            offers = item.get("offers") or {}
            if isinstance(offers, list) and offers: offers = offers[0]
            price = offers.get("price") or offers.get("priceSpecification", {}).get("price")
            if price: data["total_price"] = _num(price)
            addr = item.get("address") or {}
            if isinstance(addr, list) and addr: addr = addr[0]
            street  = (addr.get("streetAddress") or "").strip() if addr else ""
            locality= (addr.get("addressLocality") or "").strip() if addr else ""
            postal  = (addr.get("postalCode") or "").strip() if addr else ""
            if street and postal and locality:
                data["address"] = f"{street}, {postal} {locality}"
            elif street or locality:
                data["address"] = street or locality
    return data

def _regex_pick(soup: BeautifulSoup, labels: list[str]) -> int | None:
    text = soup.get_text(" ", strip=True)
    for lab in labels:
        m = re.search(rf"{lab}\s*[:\s]\s*([0-9\.\s]+)", text, flags=re.IGNORECASE)
        if m: return _num(m.group(1))
    return None

def _find_address_fallback(soup: BeautifulSoup) -> str | None:
    # Prøv lenker som inneholder postnummer
    for a in soup.find_all("a"):
        txt = (a.get_text(" ", strip=True) or "").strip()
        if re.search(r"\b\d{4}\b", txt) and "," in txt:
            return txt
    # Regex i fritekst – "Gate 12, 0123 Sted"
    text = soup.get_text(" ", strip=True)
    m = re.search(r"([A-Za-zÆØÅæøå0-9\.\-/' ]+),\s*(\d{4})\s+([A-Za-zÆØÅæøå\-\.\' ]+)", text)
    if m:
        return f"{m.group(1).strip()}, {m.group(2).strip()} {m.group(3).strip()}"
    return None

def scrape_finn(url: str) -> dict:
    out = {"source_url": url}
    meta = fetch_listing_meta(url)
    out["title"]  = meta.get("title")
    out["image"]  = meta.get("image")
    soup = BeautifulSoup(meta.get("html") or "", "html.parser")

    out.update({k: v for k, v in _parse_ld_json(soup).items() if v})
    if not out.get("address"):
        fb = _find_address_fallback(soup)
        if fb: out["address"] = fb

    hoa = _regex_pick(soup, ["Felleskostnader", r"Felleskost/mnd\.?", "Fellesutgifter"])
    if hoa: out["hoa_month"] = hoa

    if "total_price" not in out:
        tp = _regex_pick(soup, ["Totalpris", "Prisantydning"])
        if tp: out["total_price"] = tp

    time.sleep(0.2)
    return out

# ---------------- Analysis runner ----------------
def run_full_analysis(url: str, params: dict):
    """
    Kjører scraping -> beregning -> AI og lagrer i session_state.
    Viser 'Kjører AI-analyse …' i sticky header mens det jobbes.
    """
    st.session_state["busy"] = True
    st.rerun()

def _actually_run():
    """Kalles på 'result'-siden når busy=True for å kjøre jobben og lagre resultatet."""
    url = st.session_state.get("listing_url") or ""
    info = scrape_finn(url)

    # Prefill
    price = info.get("total_price", 3_500_000)
    hoa   = info.get("hoa_month", 0)
    params = st.session_state.get("params") or {}
    price = params.get("price", price)
    equity= params.get("equity", 0)
    interest = params.get("interest", 5.5)
    term_years = params.get("term_years", 25)
    rent = params.get("rent", 0)
    hoa  = params.get("hoa", hoa)
    maint_pct = params.get("maint_pct", 0.0)
    other_costs = params.get("other_costs", 0)
    vacancy_pct = 0.0

    computed = compute_metrics(price, equity, interest, term_years, rent, hoa, maint_pct, vacancy_pct, other_costs)

    key = get_openai_key()
    inputs_dict = {
        "price": price, "equity": equity, "interest": interest, "term_years": term_years,
        "rent": rent, "hoa": hoa, "maint_pct": maint_pct, "vacancy_pct": vacancy_pct, "other_costs": other_costs
    }
    if not key:
        ai_text = local_explain(inputs_dict, computed)
    else:
        try:
            client = OpenAI(api_key=key)
            prompt = (
                f"Kort norsk vurdering (2–3 avsnitt) av investeringscaset.\n"
                f"Kjøpesum {price:,} kr, egenkapital {equity:,} kr, rente {interest} %, {term_years} år.\n"
                f"Leie {rent:,}/mnd, felleskost {hoa:,}/mnd, vedlikehold {maint_pct}% av leie, andre {other_costs:,}/mnd.\n"
                f"Cashflow {computed['cashflow']:.0f}/mnd, break-even {computed['break_even']:.0f}/mnd, "
                f"NOI {computed['noi_year']:.0f}/år, avdrag {computed['principal_reduction_year']:.0f}/år, "
                f"ROE {computed['total_equity_return_pct']:.1f}%."
            )
            res = client.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0.2,
                messages=[{"role":"user","content":prompt}],
                max_tokens=450,
            )
            ai_text = res.choices[0].message.content.strip()
        except Exception:
            ai_text = local_explain(inputs_dict, computed)

    st.session_state["scraped_info"] = info
    st.session_state["computed"] = computed
    st.session_state["ai_text"] = ai_text
    st.session_state["busy"] = False
    st.rerun()

# ---------------- Pages ----------------
def render_landing():
    st.markdown("### Lim inn FINN-lenke")
    with st.form("landing_form", clear_on_submit=False):
        url = st.text_input("",
                            placeholder="https://www.finn.no/realestate/…",
                            label_visibility="collapsed")
        run = st.form_submit_button("Kjør analyse", use_container_width=True)
    if run and url:
        st.session_state.update({
            "page": "result",
            "listing_url": url.strip(),
            "params": {},  # reset
            "computed": None,
            "ai_text": ""
        })
        run_full_analysis(url.strip(), {})
        # rerun trigges i run_full_analysis

def render_result():
    # Hvis vi nettopp har trykket "Kjør analyse" eller "Oppdater"
    if st.session_state["busy"]:
        _actually_run()
        return

    url = st.session_state.get("listing_url") or ""
    info = st.session_state.get("scraped_info") or {}
    if not url or not info:
        st.session_state["page"] = "landing"
        st.rerun()

    # Adresse som heading (ikke beskrivelse)
    heading = info.get("address") or "Adresse ikke funnet"
    st.markdown(f"### {heading}")

    # Bilde + parametre på samme linje
    left, right = st.columns([6, 6], gap="large")
    with left:
        if info.get("image"):
            st.image(info["image"], use_container_width=True)

    # Prefill defaults fra scrape
    default_price = info.get("total_price", 3_500_000)
    default_hoa   = info.get("hoa_month", 0)

    params = st.session_state.get("params") or {}
    with right:
        st.markdown('<div class="section-title">Parametre</div>', unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        with c1:
            price = st.number_input("Total kjøpesum (kr)", min_value=0, step=50_000,
                                    value=params.get("price", default_price), key="p_price")
            equity = st.number_input("Egenkapital (kr)", min_value=0, step=10_000,
                                     value=params.get("equity", 0), key="p_equity")
            interest = st.number_input("Nominell rente (% per år)", min_value=0.0, step=0.1,
                                       value=params.get("interest", 5.5), key="p_interest")
            term_years = st.number_input("Lånetid (år)", min_value=1, max_value=40, step=1,
                                         value=params.get("term_years", 25), key="p_term")
        with c2:
            rent = st.number_input("Brutto leie pr mnd (kr)", min_value=0, step=500,
                                   value=params.get("rent", 0), key="p_rent")
            hoa = st.number_input("Felleskostnader pr mnd (kr)", min_value=0, step=100,
                                  value=params.get("hoa", default_hoa), key="p_hoa")
            maint_pct = st.number_input("Vedlikehold (% av leie)", min_value=0.0, step=0.5,
                                        value=params.get("maint_pct", 0.0), key="p_maint")
            other_costs = st.number_input("Andre kostn. pr mnd (kr)", min_value=0, step=100,
                                          value=params.get("other_costs", 0), key="p_other")

        # Oppdater-knapp – ingen autoanalyse ved endring
        if st.button("Oppdater", use_container_width=True):
            st.session_state["params"] = {
                "price": price, "equity": equity, "interest": interest, "term_years": term_years,
                "rent": rent, "hoa": hoa, "maint_pct": maint_pct, "other_costs": other_costs
            }
            run_full_analysis(url, st.session_state["params"])

    st.markdown("<hr>", unsafe_allow_html=True)

    # Resultater (bruk siste computed fra state)
    computed = st.session_state.get("computed")
    if computed:
        st.subheader("📊 Resultater")
        a, b, c = st.columns(3)
        with a:
            st.metric("Månedlig cashflow", f"{format_number(computed['cashflow'])} kr")
            st.metric("Månedlig lånebetaling", f"{format_number(computed['m_payment'])} kr")
        with b:
            st.metric("Break-even leie", f"{format_number(computed['break_even'])} kr/mnd")
            st.metric("Årlig NOI (ekskl. lån)", f"{format_number(computed['noi_year'])} kr/år")
        with c:
            st.metric("Årlig avdrag (ca.)", f"{format_number(computed['principal_reduction_year'])} kr/år")
            st.metric("Total EK-avkastning", f"{computed['total_equity_return_pct']:.1f} %")
    else:
        st.info("Ingen beregning enda – trykk Oppdater.")

    st.markdown("<hr>", unsafe_allow_html=True)

    # AI – venstre halvdel, med mørkeblå outline
    st.subheader("🧠 AI-analyse")
    ai_left, _ai_right = st.columns([6,6], gap="large")
    with ai_left:
        st.markdown('<div class="card-blue">', unsafe_allow_html=True)
        st.write(st.session_state.get("ai_text", ""))
        st.markdown('</div>', unsafe_allow_html=True)

# ---------------- Router ----------------
if st.session_state["page"] == "landing":
    render_landing()
else:
    render_result()