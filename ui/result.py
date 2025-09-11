# ui/result.py
import re
import streamlit as st

from core.scrape import scrape_finn
from core.compute import compute_metrics
from core.ai import ai_explain
from core.rates import get_interest_estimate
from core.rent import get_rent_by_csv
from core.history import add_analysis  # ⬅️ NYTT


def _strip_house_number(addr: str) -> str:
    """Fjerner tydelig husnummer-suffiks fra adresse."""
    return re.sub(r"\s+\d+[A-Za-z]?$", "", addr or "").strip()


def _init_params_for_new_url(info: dict) -> dict:
    """Startverdier når ny FINN-URL limes inn: alt 0, unntak lånetid=30."""
    return {
        "price": 0,
        "equity": 0,
        "interest": 0.0,
        "term_years": 30,
        "rent": 0,
        "hoa": 0,
        "maint_pct": 0.0,
        "vacancy_pct": 0.0,
        "other_costs": 0,
    }


def render_result() -> None:
    # --- URL-guard ---
    url = st.session_state.get("listing_url") or ""
    if not url:
        st.session_state["page"] = "landing"
        st.rerun()
        return

    # --- Init flagg ---
    st.session_state.setdefault("_first_compute_done", False)
    st.session_state.setdefault("_updating", False)
    st.session_state.setdefault("_history_logged", False)  # ⬅️ NYTT

    # --- Scrape ved ny URL + init params ---
    if st.session_state.get("_scraped_url") != url:
        info = scrape_finn(url)
        st.session_state["_scraped_url"] = url
        st.session_state["_scraped_info"] = info
        st.session_state["computed"] = None
        st.session_state["params"] = _init_params_for_new_url(info)
        st.session_state["_first_compute_done"] = False
        st.session_state["_history_logged"] = False
    else:
        info = st.session_state.get("_scraped_info", {}) or {}

    params = st.session_state["params"]

    # --- Tittel ---
    address = (info.get("address") or "").strip()
    if address:
        st.subheader(address)

    # --- Layout ---
    left, right = st.columns([6, 6], gap="large")

    # ---------- VENSTRE ----------
    with left:
        if info.get("image"):
            st.image(info["image"], use_container_width=True)

    # ---------- HØYRE ----------
    with right:
        st.markdown("**Parametre**")
        c1, c2 = st.columns(2)

        with c1:
            params["price"] = st.number_input(
                "Total kjøpesum (kr)",
                min_value=0,
                step=50_000,
                value=int(params["price"]),
                help="Sum kjøpesum inkl. omkostninger fra annonsen.",
            )
            params["equity"] = st.number_input(
                "Egenkapital (kr)",
                min_value=0,
                step=10_000,
                value=int(params["equity"]),
                help="Kontanter/egenkapital du legger inn i kjøpet.",
            )
            params["interest"] = st.number_input(
                "Rente (% p.a.)",
                min_value=0.0,
                step=0.1,
                value=float(params["interest"]),
                help="Nominell årlig rente brukt i låneberegningen.",
            )
            params["term_years"] = st.number_input(
                "Lånetid (år)",
                min_value=1,
                max_value=40,
                step=1,
                value=int(params["term_years"]),
                help="Nedbetalingstid for annuitetslånet (år).",
            )

        with c2:
            params["rent"] = st.number_input(
                "Brutto leie (kr/mnd)",
                min_value=0,
                step=500,
                value=int(params["rent"]),
                help="Estimert månedlig husleie før kostnader. Foreløpig kun støttet for Bergen og Oslo",
            )
            params["hoa"] = st.number_input(
                "Felleskost. (kr/mnd)",
                min_value=0,
                step=100,
                value=int(params["hoa"]),
                help="Månedlige felleskostnader (TV/internett inkludert hvis oppgitt).",
            )
            params["maint_pct"] = st.number_input(
                "Vedlikehold (% av leie)",
                min_value=0.0,
                step=0.5,
                value=float(params["maint_pct"]),
                help="Avsatt vedlikehold i prosent av brutto leie.",
            )
            params["other_costs"] = st.number_input(
                "Andre kostn. (kr/mnd)",
                min_value=0,
                step=100,
                value=int(params["other_costs"]),
                help="Andre månedlige driftskostnader (strøm, forsikring, mv.).",
            )

        # --- Bunn-knapper: analyse / hent data / infoboble
        k1, k2, k3 = st.columns([6, 3, 1], gap="small")

        with k1:
            if st.session_state.get("_updating", False):
                st.button(
                    "Kjører analyse …",
                    disabled=True,
                    use_container_width=True,
                    key="upd_disabled_main",
                )
            else:
                if st.button("Kjør analyse", use_container_width=True, key="upd_main"):
                    st.session_state["_updating"] = True
                    st.session_state["_queued_params"] = params.copy()
                    st.rerun()

        with k2:
            if st.button("Hent data", use_container_width=True, key="rent_csv_btn"):
                # 1) Tall fra FINN
                price_from_finn = int(info.get("total_price") or 0)
                hoa_from_finn = int(info.get("hoa_month") or 0)

                # 2) Egenkapital 15%
                equity_from_price = (
                    int(round(price_from_finn * 0.15)) if price_from_finn else 0
                )

                # 3) CSV/Geo-estimat
                target_area = (
                    float(info.get("area_m2")) if info.get("area_m2") else None
                )
                target_rooms = int(info.get("rooms")) if info.get("rooms") else None

                est = get_rent_by_csv(info, area_m2=target_area, rooms=target_rooms)

                # 4) Debug + toast
                if est:
                    rent_suggestion = int(est.gross_rent)
                    st.session_state["rent_debug"] = {
                        "source": "csv",
                        "city": est.city,
                        "bucket": est.bucket,
                        "kr_per_m2": est.kr_per_m2,
                        "updated": est.updated,
                        "confidence": est.confidence,
                        "note": est.note,
                        "area_m2": target_area,
                        "rooms": target_rooms,
                    }
                    st.toast(
                        f"Leieforslag: {rent_suggestion:,} kr  •  {est.bucket}  •  {est.updated}",
                        icon="✅",
                    )
                else:
                    rent_suggestion = params.get("rent") or 15000
                    st.session_state["rent_debug"] = {
                        "source": "csv",
                        "error": "Fant ikke m²-tabell for byen – brukte foreløpig verdi.",
                        "area_m2": target_area,
                        "rooms": target_rooms,
                    }
                    st.toast(
                        "Fant ikke m²-tabell for byen – brukte foreløpig verdi.",
                        icon="⚠️",
                    )

                # 5) Rente-estimat
                params["interest"] = float(get_interest_estimate())

                # 6) Oppdater felter og rerun
                params["price"] = price_from_finn
                params["equity"] = equity_from_price
                params["rent"] = rent_suggestion
                params["hoa"] = hoa_from_finn
                st.rerun()

        # Debug-panel (CSV)
        with st.expander("Debug: leie (CSV) – sist hentet"):
            st.json(st.session_state.get("rent_debug") or {})

        with k3:
            # Lite info-ikon
            st.markdown(
                """
                <style>
                  .td-info { position: relative; display:flex; justify-content:flex-end; height:28px; margin-top:6px; }
                  .td-info .ic { width:18px; height:18px; opacity:.85; cursor:help; }
                  .td-info .tip {
                    display:none; position:absolute; bottom:28px; right:0; background:#111; color:#fff;
                    padding:10px 12px; border-radius:6px; font-size:13px; line-height:1.45;
                    white-space:normal; box-shadow:0 6px 16px rgba(0,0,0,.3); z-index:9999; width:420px; text-align:left;
                  }
                  .td-info:hover .tip { display:block; }
                </style>
                <div class="td-info">
                  <svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                       stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
                       aria-hidden="true" focusable="false">
                    <circle cx="12" cy="12" r="10"></circle>
                    <line x1="12" y1="16" x2="12" y2="12"></line>
                    <line x1="12" y1="8"  x2="12.01" y2="8"></line>
                  </svg>
                  <div class="tip">
                    Henter fra FINN-annonsen:<br>
                    • Kjøpesum og felleskostnader<br>
                    • Egenkapital = 15 % av kjøpesum<br>
                    • Brutto leie fra CSV/Geo (kr/m² × BRA)<br>
                    • Rente (DNB hvis mulig, ellers styringsrente + margin)
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    # --- Utfør beregning når _updating = True ---
    if st.session_state["_updating"]:
        p = st.session_state.get("_queued_params") or params.copy()
        m = compute_metrics(
            p["price"],
            p["equity"],
            p["interest"],
            p["term_years"],
            p["rent"],
            p["hoa"],
            p["maint_pct"],
            p["vacancy_pct"],
            p["other_costs"],
        )
        st.session_state["params"] = p
        st.session_state["computed"] = m
        st.session_state["ai_text"] = ai_explain(p, m)
        st.session_state["_updating"] = False
        st.session_state["_first_compute_done"] = (
            True  # ⬅️ NYTT: markér at første compute er gjort
        )
        st.rerun()

    # --- Vis beregninger ---
    m = st.session_state.get("computed")
    if not m:
        return

    # --- NYTT: logg én gang når vi har første gyldige resultat ---
    if not st.session_state.get("_history_logged"):
        info_now = st.session_state.get("_scraped_info", {}) or {}
        # Hent felter med fornuftige fallbacks
        title = info_now.get("title") or (info_now.get("address") or "Uten tittel")
        address_for_log = info_now.get("address") or ""
        price_for_log = int(info_now.get("total_price") or 0) or int(
            st.session_state["params"].get("price") or 0
        )
        summary = (st.session_state.get("ai_text") or "")[:200]

    # --- NYTT: logg én gang når vi har første gyldige resultat ---
    if not st.session_state.get("_history_logged"):
        info_now = st.session_state.get("_scraped_info", {}) or {}

        title = (
            info_now.get("address") or ""
        ).strip() or "Uten tittel"  # kun tittel = adresse
        price_for_log = int(info_now.get("total_price") or 0) or int(
            st.session_state["params"].get("price") or 0
        )
        summary = (st.session_state.get("ai_text") or "")[:200]
        image_url = info_now.get("image")  # <- bilde fra annonsen

        add_analysis(
            finn_url=url,
            title=title,
            price=price_for_log if price_for_log > 0 else None,
            summary=summary,
            image=image_url,
            result_args={"id": ""},  # ev. intern id dersom du har
        )
        st.session_state["_history_logged"] = True

    a, b, c = st.columns(3)
    with a:
        st.metric(
            "Cashflow (mnd)",
            f"{m['cashflow']:.0f} kr",
            help="Netto kontantstrøm per måned.",
        )
        st.metric(
            "Lånebetaling (mnd)",
            f"{m['m_payment']:.0f} kr",
            help="Terminbeløp (renter + avdrag).",
        )
    with b:
        st.metric(
            "Break-even",
            f"{m['break_even']:.0f} kr/mnd",
            help="Leie for 0 kr i månedlig cashflow.",
        )
        st.metric(
            "NOI (år)",
            f"{m['noi_year']:.0f} kr",
            help="Netto driftsresultat (årlig) før finans.",
        )
    with c:
        st.metric(
            "Avdrag (år)",
            f"{m['principal_reduction_year']:.0f} kr",
            help="Sum årlige avdrag.",
        )
        st.metric(
            "ROE",
            f"{m['total_equity_return_pct']:.1f} %",
            help="Avkastning på egenkapital.",
        )
    st.markdown("---")
    st.subheader("🧠 AI-analyse")
    st.write(st.session_state.get("ai_text") or "")
