import os
import math
import streamlit as st
from openai import OpenAI


# Hide the Streamlit footer, menu, and "Made with Streamlit" branding
hide_streamlit_style = """
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    .stAppDeployButton {display: none;}
    .stAppBottomRight {display: none;}
    </style>
"""
st.markdown(hide_streamlit_style, unsafe_allow_html=True)

# ---------- Skjul Streamlit-standard ----------
HIDE_STYLE = """
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}
</style>
"""
st.markdown(HIDE_STYLE, unsafe_allow_html=True)

# ---------- Hjelpere ----------
def format_number(n, decimals=0):
    """Sikker formattering med mellomrom som tusenskilletegn."""
    if n is None:
        return "—"
    try:
        x = float(n)
    except Exception:
        return str(n)
    if math.isnan(x):
        return "—"
    if math.isinf(x):
        return "∞"
    s = f"{x:,.{decimals}f}".replace(",", " ")
    if decimals == 0:
        s = s.split(".")[0]
    return s

def monthly_payment(principal, annual_rate_pct, n_years):
    """Annuitetsbetaling pr. måned."""
    if principal <= 0:
        return 0.0
    r = (annual_rate_pct / 100.0) / 12.0
    n = int(n_years * 12)
    if r == 0:
        return principal / n
    return principal * r * (1 + r)**n / ((1 + r)**n - 1)

def compute_metrics(price, equity, interest, term_years, rent, hoa, maint_pct, vacancy_pct, other_costs):
    loan = max(price - equity, 0)
    m_payment = monthly_payment(loan, interest, term_years)

    # Månedsvise kostnader av leie
    maint = rent * (maint_pct / 100.0)
    vacancy = rent * (vacancy_pct / 100.0)

    total_monthly_costs = m_payment + hoa + maint + vacancy + other_costs
    cashflow = rent - total_monthly_costs

    # NOI ekskl. lån (bransjestandard)
    noi_month = rent - (hoa + maint + vacancy + other_costs)
    noi_year = noi_month * 12

    invested_equity = equity if equity > 0 else 1  # unngå /0

    # Årlig avdrag (grovt anslag første år)
    annual_rate = interest / 100.0
    paid_year = m_payment * 12
    approx_interest_year = loan * annual_rate
    principal_reduction_year = max(paid_year - approx_interest_year, 0)

    # Total EK-avkastning etter renter/kostnader
    total_equity_return_pct = ((cashflow * 12) + principal_reduction_year) / invested_equity * 100.0

    # Break-even leie
    factor = 1.0 - (maint_pct/100.0) - (vacancy_pct/100.0)
    break_even = (m_payment + hoa + other_costs) / factor if factor > 0 else float("inf")

    return {
        "loan": loan,
        "m_payment": m_payment,
        "maint": maint,
        "vacancy": vacancy,
        "total_costs": total_monthly_costs,
        "cashflow": cashflow,
        "noi_year": noi_year,
        "break_even": break_even,
        "principal_reduction_year": principal_reduction_year,
        "total_equity_return_pct": total_equity_return_pct,
        "legacy_net_yield_pct": (noi_year / invested_equity) * 100.0
    }

# ---------- Lokal fallback (ingen API nødvendig) ----------
def local_explain(inputs, m):
    vurdering = "ok"
    if m['total_equity_return_pct'] >= 7: vurdering = "god"
    if m['total_equity_return_pct'] < 3: vurdering = "svak"

    risiko = []
    if inputs['interest'] > 6: risiko.append("høy rente")
    if inputs['vacancy_pct'] >= 6: risiko.append("høy tomgang")
    if m['cashflow'] < 0: risiko.append("negativ månedlig cashflow")

    tiltak = []
    if m['cashflow'] < 0: tiltak.append("øk leie eller reduser kostnader")
    if inputs['interest'] > 5: tiltak.append("forhandle rente/fast rente")
    if inputs['equity']/max(inputs['price'],1) < 0.20: tiltak.append("øke egenkapitalen")

    txt = []
    txt.append(
        f"**Vurdering:** {vurdering}. Total EK-avkastning anslås til **{m['total_equity_return_pct']:.1f}%** "
        f"med månedlig cashflow **{format_number(m['cashflow'])} kr** og break-even leie "
        f"**{format_number(m['break_even'])} kr/mnd**."
    )
    if risiko:
        txt.append("**Risikofaktorer:** " + ", ".join(risiko) + ".")
    if tiltak:
        txt.append("**Tiltak:** " + ", ".join(tiltak) + ".")
    return "\n\n".join(txt)

# ---------- OpenAI forklaring med trygg fallback ----------
def ai_explain(inputs, metrics):
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return local_explain(inputs, metrics)

    try:
        client = OpenAI(api_key=api_key)
        prompt = f"""
Du er en norsk rådgiver for eiendomsinvestering. Gi en kort, presis analyse (2–3 avsnitt).

Input:
- Total kjøpesum: {inputs['price']:,} kr
- Egenkapital: {inputs['equity']:,} kr
- Rente: {inputs['interest']} %
- Lånetid: {inputs['term_years']} år
- Leie: {inputs['rent']:,} kr/mnd
- Felleskostnader: {inputs['hoa']:,} kr/mnd
- Vedlikehold: {inputs['maint_pct']} % av leie
- Tomgang: {inputs['vacancy_pct']} % av leie
- Andre kostnader: {inputs['other_costs']:,} kr/mnd

Resultater:
- Månedlig cashflow: {metrics['cashflow']:.0f} kr
- Total EK-avkastning: {metrics['total_equity_return_pct']:.1f} %
- Break-even leie: {metrics['break_even']:.0f} kr/mnd
- Årlig NOI (ekskl. lån): {metrics['noi_year']:.0f} kr/år
- Årlig avdrag (ca.): {metrics['principal_reduction_year']:.0f} kr/år

Skriv:
1) Klar vurdering (god/ok/dårlig) + hvorfor (henvis til tall).
2) 2–3 risikofaktorer (konkret).
3) 2–3 forbedringstiltak (rente, leie, kostnader, EK, tomgang).
"""
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        return r.choices[0].message.content.strip()

    except Exception as e:
        st.warning(f"AI utilgjengelig: {type(e).__name__}: {str(e)[:140]} … Viser lokal analyse i stedet.")
        return local_explain(inputs, metrics)

# ---------- UI ----------
st.set_page_config(page_title="Eiendom-AI – MVP kalkulator", page_icon="🏠", layout="centered")
st.title("🏠 Eiendom-AI (MVP) – 60-sek kalkulator")
st.caption("Regn ut månedlig cashflow, break-even og **total avkastning på egenkapitalen** (etter renter og kostnader).")

with st.form("inputs"):
    col1, col2 = st.columns(2)
    with col1:
        price = st.number_input("Total kjøpesum (kr)", min_value=0, step=50_000, value=3_500_000)
        equity = st.number_input("Egenkapital (kr)", min_value=0, step=10_000, value=700_000)
        interest = st.number_input("Nominell rente (% per år)", min_value=0.0, step=0.1, value=5.5)
        term_years = st.number_input("Lånetid (år)", min_value=1, max_value=40, step=1, value=25)
    with col2:
        rent = st.number_input("Brutto leie pr mnd (kr)", min_value=0, step=500, value=16_000)
        hoa = st.number_input("Felleskostnader pr mnd (kr)", min_value=0, step=100, value=3_000)
        maint_pct = st.number_input("Vedlikehold (% av leie)", min_value=0.0, step=0.5, value=5.0)
        vacancy_pct = st.number_input("Tomgang (% av leie)", min_value=0.0, step=0.5, value=5.0)
        other_costs = st.number_input("Andre kostn. pr mnd (forsikring, kommunale) (kr)", min_value=0, step=100, value=500)
    submitted = st.form_submit_button("Kjør analyse")

st.caption("ℹ️ **Tomgang** = forventet leietap (måneder/dager uten leie). 5 % ≈ ~18 dager per år.")

if submitted:
    m = compute_metrics(price, equity, interest, term_years, rent, hoa, maint_pct, vacancy_pct, other_costs)

    st.subheader("📊 Resultater")
    colA, colB, colC = st.columns(3)
    with colA:
        st.metric("Månedlig cashflow", f"{format_number(m['cashflow'])} kr")
        st.metric("Månedlig lånebetaling", f"{format_number(m['m_payment'])} kr")
    with colB:
        st.metric("Break-even leie", f"{format_number(m['break_even'])} kr/mnd")
        st.metric("Årlig NOI (ekskl. lån)", f"{format_number(m['noi_year'])} kr/år")
    with colC:
        st.metric("Årlig avdrag (ca.)", f"{format_number(m['principal_reduction_year'])} kr/år")
        st.metric("Total EK-avkastning", f"{m['total_equity_return_pct']:.1f} %")

    st.divider()
    st.caption("NOI ekskluderer låneservicing. Cashflow inkluderer lån, felleskostn., vedlikehold, tomgang og andre kostnader.")

    with st.expander("Detaljerte beregninger"):
        st.write({
            "Lån (kr)": format_number(m['loan']),
            "Felleskostn. (mnd)": format_number(hoa),
            "Vedlikehold (mnd)": format_number(m['maint']),
            "Tomgang (mnd)": format_number(m['vacancy']),
            "Andre kostn. (mnd)": format_number(other_costs),
            "Totale kostn. (mnd)": format_number(m['total_costs']),
            "Legacy netto-yield (NOI/EK)": f"{m['legacy_net_yield_pct']:.1f} %",
        })

    # ---- AI / lokal analyse (må være inni if submitted) ----
    st.subheader("🧠 AI-analyse")
    use_ai = st.toggle("Aktiver AI-analyse", value=True, help="Krever OPENAI_API_KEY og gyldig kvote")
    inputs_dict = {
        "price": price, "equity": equity, "interest": interest, "term_years": term_years,
        "rent": rent, "hoa": hoa, "maint_pct": maint_pct, "vacancy_pct": vacancy_pct, "other_costs": other_costs
    }
    if use_ai:
        st.write(ai_explain(inputs_dict, m))
    else:
        st.write(local_explain(inputs_dict, m))


