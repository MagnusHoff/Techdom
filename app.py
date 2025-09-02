import math
import numpy as np
import streamlit as st
import locale

import math

def format_number(n, decimals=0):
    """
    Sikker formattering:
    - Håndterer None, NaN, ±inf
    - Bruker mellomrom som tusenskilletegn
    - Desimals=0 fjerner desimaler
    """
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
        # fjern .00 hvis formatter har lagt igjen punktum
        s = s.split(".")[0]
    return s



st.set_page_config(page_title="Eiendom-AI – MVP kalkulator", page_icon="🏠", layout="centered")

st.title("🏠 Eiendom-AI (MVP) – 60-sek kalkulator")
st.caption("Regn ut månedlig cashflow, nettoutbytte (yield) og break-even på en utleiebolig.")

with st.form("inputs"):
    col1, col2 = st.columns(2)
    with col1:
        price = st.number_input("Total kjøpesum (kr)", min_value=0, step=50000, value=3_500_000)
        equity = st.number_input("Egenkapital (kr)", min_value=0, step=10000, value=700_000)
        interest = st.number_input("Nominell rente (% per år)", min_value=0.0, step=0.1, value=5.5)
        term_years = st.number_input("Lånetid (år)", min_value=1, max_value=40, step=1, value=25)
    with col2:
        rent = st.number_input("Brutto leie pr mnd (kr)", min_value=0, step=500, value=16_000)
        hoa = st.number_input("Felleskostnader pr mnd (kr)", min_value=0, step=100, value=3_000)
        maint_pct = st.number_input("Vedlikehold (% av leie)", min_value=0.0, step=0.5, value=5.0)
        vacancy_pct = st.number_input("Tomgang (% av leie)", min_value=0.0, step=0.5, value=5.0)
        other_costs = st.number_input("Andre kostn. pr mnd (forsikring, kommunale) (kr)", min_value=0, step=100, value=500)
    submitted = st.form_submit_button("Kjør analyse")

def monthly_payment(principal, annual_rate_pct, n_years):
    if principal <= 0:
        return 0.0
    r = (annual_rate_pct / 100.0) / 12.0  # månedsrente
    n = int(n_years * 12)
    if r == 0:
        return principal / n
    return principal * r * (1 + r)**n / ((1 + r)**n - 1)

def compute_metrics(price, equity, interest, term_years, rent, hoa, maint_pct, vacancy_pct, other_costs):
    loan = max(price - equity, 0)
    m_payment = monthly_payment(loan, interest, term_years)

    # Kostnader som andeler av leie
    maint = rent * (maint_pct / 100.0)
    vacancy = rent * (vacancy_pct / 100.0)

    total_monthly_costs = m_payment + hoa + maint + vacancy + other_costs
    cashflow = rent - total_monthly_costs

    # Enkel netto-yield: netto driftsresultat / total investert kapital (her: egenkapital)
    # NOI (måned) = leie - (hoa + maint + vacancy + other_costs)  (ekskl. låneservicing)
    noi_month = rent - (hoa + maint + vacancy + other_costs)
    noi_year = noi_month * 12
    invested_equity = equity if equity > 0 else 1  # unngå deling på 0
    net_yield_pct = (noi_year / invested_equity) * 100.0

    # Break-even leie: når cashflow = 0  => leie = m_payment + hoa + maint%*leie + vacancy%*leie + other
    # leie * (1 - maint% - vacancy%) = m_payment + hoa + other
    factor = 1.0 - (maint_pct/100.0) - (vacancy_pct/100.0)
    break_even = (m_payment + hoa + other_costs) / factor if factor > 0 else float("inf")

    # En enkel årlig nedbetaling (amortisering): første års avdrag ~ total betalt − renter (tilnærmet)
    # Approksimer første års rente som loan * annual_rate_pct
    annual_rate = interest / 100.0
    paid_year = m_payment * 12
    approx_interest_year = loan * annual_rate
    principal_reduction_year = max(paid_year - approx_interest_year, 0)

    return {
        "loan": loan,
        "m_payment": m_payment,
        "maint": maint,
        "vacancy": vacancy,
        "total_costs": total_monthly_costs,
        "cashflow": cashflow,
        "noi_year": noi_year,
        "net_yield_pct": net_yield_pct,
        "break_even": break_even,
        "principal_reduction_year": principal_reduction_year
    }

if submitted:
    m = compute_metrics(price, equity, interest, term_years, rent, hoa, maint_pct, vacancy_pct, other_costs)

    st.subheader("📊 Resultater")
    colA, colB = st.columns(2)
    with colA:
        st.metric("Månedlig cashflow", format_number(m['cashflow']) + " kr")
        st.metric("Månedlig lånebetaling", format_number(m['m_payment']) + " kr")
        st.metric("Break-even leie", format_number(m['break_even']) + " kr/mnd")
    with colB:
        st.metric("Netto-yield (på EK)", f"{m['net_yield_pct']:.1f} %")
        st.metric("Årlig NOI (ekskl. lån)", format_number(m['noi_year']) + " kr/år")
        st.metric("Årlig avdrag (ca.)", format_number(m['principal_reduction_year']) + " kr/år")

    st.divider()
    st.caption("Merk: NOI ekskluderer låneservicing. Cashflow inkluderer lån, felleskostn., vedlikehold, tomgang og andre kostnader.")

    with st.expander("Detaljerte beregninger"):
        st.write({
            "Lån (kr)": m['loan'],
            "Felleskostn. (mnd)": hoa,
            "Vedlikehold (mnd)": m['maint'],
            "Tomgang (mnd)": m['vacancy'],
            "Andre kostn. (mnd)": other_costs,
            "Totale kostn. (mnd)": m['total_costs'],
        })

st.info("Tips: Start enkelt. Når tallene gir mening, legger vi til AI-forklaring og PDF-rapport i neste steg.")

