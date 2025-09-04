# ui/result.py
import streamlit as st
from core.scrape import scrape_finn
from core.compute import compute_metrics
from core.ai import ai_explain

def render_result():
    url = st.session_state.get("listing_url") or ""
    if not url:
        st.session_state["page"] = "landing"
        st.rerun()
        return

    # init interne flagg
    st.session_state.setdefault("_first_compute_done", False)
    st.session_state.setdefault("_updating", False)

    # Scrape kun når URL endres
    if st.session_state.get("_scraped_url") != url:
        info = scrape_finn(url)
        st.session_state["_scraped_url"] = url
        st.session_state["_scraped_info"] = info
        st.session_state["computed"] = None
        st.session_state["params"] = {}
        st.session_state["_first_compute_done"] = False
    else:
        info = st.session_state.get("_scraped_info", {})

    address = (info.get("address") or "").strip()
    if address:
        st.subheader(address)

    left, right = st.columns([6, 6], gap="large")

    # ---------- VENSTRE ----------
    with left:
        if info.get("image"):
            st.image(info["image"], use_container_width=True)

    # ---------- HØYRE ----------
    defaults = {
        "price": info.get("total_price", 3_500_000),
        "equity": 0,
        "interest": 5.5,
        "term_years": 25,
        "rent": 0,
        "hoa": info.get("hoa_month", 0),
        "maint_pct": 0.0,
        "other_costs": 0,
        "vacancy_pct": 0.0,
    }
    params_view = {**defaults, **st.session_state.get("params", {})}

    with right:
        st.markdown("**Parametre**")
        c1, c2 = st.columns(2)

        with c1:
            params_view["price"]      = st.number_input("Total kjøpesum (kr)", min_value=0, step=50_000, value=int(params_view["price"]))
            params_view["equity"]     = st.number_input("Egenkapital (kr)",   min_value=0, step=10_000, value=int(params_view["equity"]))
            params_view["interest"]   = st.number_input("Rente (% p.a.)",     min_value=0.0, step=0.1, value=float(params_view["interest"]))
            params_view["term_years"] = st.number_input("Lånetid (år)",       min_value=1, max_value=40, step=1, value=int(params_view["term_years"]))
        with c2:
            params_view["rent"]        = st.number_input("Brutto leie (kr/mnd)",   min_value=0, step=500,  value=int(params_view["rent"]))
            params_view["hoa"]         = st.number_input("Felleskost. (kr/mnd)",   min_value=0, step=100,  value=int(params_view["hoa"]))
            params_view["maint_pct"]   = st.number_input("Vedlikehold (% av leie)", min_value=0.0, step=0.5, value=float(params_view["maint_pct"]))
            params_view["other_costs"] = st.number_input("Andre kostn. (kr/mnd)", min_value=0, step=100,  value=int(params_view["other_costs"]))

        # Knapp: "Kjør analyse" / "🔄 Kjører analyse …"
        if st.session_state.get("_updating", False):
            st.markdown('<div class="analyze-btn">', unsafe_allow_html=True)
            st.button("Kjører analyse …", disabled=True, use_container_width=True, key="upd_disabled")
            st.markdown('</div>', unsafe_allow_html=True)
        else:
            if st.button("Kjør analyse", use_container_width=True, key="upd"):
                st.session_state["_updating"] = True
                st.session_state["_queued_params"] = params_view
                st.rerun()


    # Utfør oppdatering når _updating = True (uten global spinner)
    if st.session_state["_updating"]:
        p = st.session_state.get("_queued_params", params_view).copy()
        m = compute_metrics(
            p["price"], p["equity"], p["interest"], p["term_years"],
            p["rent"], p["hoa"], p["maint_pct"], p["vacancy_pct"], p["other_costs"]
        )
        st.session_state["params"] = p
        st.session_state["computed"] = m
        st.session_state["ai_text"] = ai_explain(p, m)
        st.session_state["_updating"] = False
        st.rerun()

    # Vis beregninger
    m = st.session_state.get("computed")
    if not m:
        return

    a, b, c = st.columns(3)
    with a:
        st.metric("Cashflow (mnd)", f"{m['cashflow']:.0f} kr")
        st.metric("Lånebetaling (mnd)", f"{m['m_payment']:.0f} kr")
    with b:
        st.metric("Break-even", f"{m['break_even']:.0f} kr/mnd")
        st.metric("NOI (år)", f"{m['noi_year']:.0f} kr")
    with c:
        st.metric("Avdrag (år)", f"{m['principal_reduction_year']:.0f} kr")
        st.metric("ROE", f"{m['total_equity_return_pct']:.1f} %")

    st.markdown("---")
    st.subheader("🧠 AI-analyse")
    st.write(st.session_state.get("ai_text") or "")