# ui/result.py
import streamlit as st
from core.scrape import scrape_finn
from core.compute import compute_metrics
from core.ai import ai_explain
from core.rates import get_interest_estimate
from core.rent import get_rent_suggestion

import re


def _strip_house_number(addr: str) -> str:
    """Fjerner tydelig husnummer-suffiks fra adresse."""
    # Eksempler: 'Alléen 6C' -> 'Alléen', 'Nygårdsgaten 5' -> 'Nygårdsgaten'
    return re.sub(r"\s+\d+[A-Za-z]?$", "", addr or "").strip()


def build_comp_queries(info: dict) -> list[str]:
    """
    Bygger en robust liste med søkestrenger for leie-comps fra FINN.
    Henter by/bydel fra addressestrengen hvis dedikerte felt mangler.
    """
    qs: list[str] = []
    addr = (
        info.get("address") or ""
    ).strip()  # f.eks 'Søndre Skogveien 8, 5055 Bergen'
    city = (info.get("city") or "").strip()
    muni = (info.get("municipality") or "").strip()
    district = (
        info.get("district") or info.get("subarea") or info.get("area") or ""
    ).strip()

    # Pull ut postnr og by fra adresse dersom felt mangler
    zipcode = ""
    m = re.search(r"\b(\d{4})\b", addr)
    if m:
        zipcode = m.group(1)
    if not city:
        # prøv å hente ordet etter postnr som bynavn
        m2 = re.search(r"\b\d{4}\s+([A-Za-zÆØÅæøå\-\s]+)$", addr)
        if m2:
            city = m2.group(1).strip()

    # Rens gate (uten husnr og uten del etter komma)
    street = addr.split(",")[0].strip()
    street_wo_no = _strip_house_number(street)

    # Bygg kandidater fra mest presis til bredest
    candidates = [
        " ".join(x for x in [street_wo_no, district, city] if x),
        " ".join(x for x in [district, city] if x),
        " ".join(x for x in [zipcode, city] if x),
        " ".join(x for x in [street_wo_no, city] if x),
        city or muni,
        muni,
    ]
    # Fjern tomme/duplikater
    seen = set()
    for q in candidates:
        q = (q or "").strip()
        if q and q not in seen:
            qs.append(q)
            seen.add(q)
    return qs


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

    # --- Init interne flagg ---
    st.session_state.setdefault("_first_compute_done", False)
    st.session_state.setdefault("_updating", False)

    # --- Scrape ved ny URL + init params til startverdier ---
    if st.session_state.get("_scraped_url") != url:
        info = scrape_finn(url)
        st.session_state["_scraped_url"] = url
        st.session_state["_scraped_info"] = info
        st.session_state["computed"] = None
        st.session_state["params"] = _init_params_for_new_url(info)
        st.session_state["_first_compute_done"] = False
    else:
        info = st.session_state.get("_scraped_info", {}) or {}

    # Peker til “levende” parametere – alt lagres her fortløpende
    params = st.session_state["params"]

    # --- Header / tittel ---
    address = (info.get("address") or "").strip()
    if address:
        st.subheader(address)

    # --- Layout: venstre (bilde), høyre (parametre) ---
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
                help="Estimert månedlig husleie før kostnader.",
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

        # ---------- BUNNRAD: Kjør analyse (venstre) + Hent data (midt) + info (høyre) ----------
        # Juster breddene her: [analyse, hent data, ikon]
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
            if st.button("Hent data", use_container_width=True, key="rent_comps_btn"):
                # --- 1) Hent kjøpesum/felleskost fra FINN (fra scrape) ---
                price_from_finn = int(info.get("total_price") or 0)
                hoa_from_finn = int(info.get("hoa_month") or 0)

                # --- 2) Egenkapital 15 % ---
                equity_from_price = (
                    int(round(price_from_finn * 0.15)) if price_from_finn else 0
                )

                # --- 3) Finn comps: enten limt FINN-URL eller fritekstspørringer ---
                target_area = (
                    float(info.get("area_m2")) if info.get("area_m2") else None
                )
                target_rooms = int(info.get("rooms")) if info.get("rooms") else None

                custom_url = (st.session_state.get("custom_finn_url") or "").strip()
                use_custom = (
                    custom_url.startswith("http")
                    and "/realestate/lettings/search.html" in custom_url
                )

                tried = []
                rent_suggestion = None
                best_note = ""
                best_n_used = 0
                best_n_raw = 0

                if use_custom:
                    # ✅ Bruker limt FINN-URL (kart/område)
                    tried.append(f"[URL] {custom_url}")
                    from core.rent import (
                        fetch_finn_comps_from_url,
                        suggest_rent_from_comps,
                    )

                    all_comps = fetch_finn_comps_from_url(
                        custom_url
                    )  # ikke send max_pages
                    s = suggest_rent_from_comps(all_comps, target_area, target_rooms)
                    if s:
                        rent_suggestion = int(s.suggested_rent)
                        best_note = s.note
                        best_n_used, best_n_raw = s.n_used, s.n_raw
                else:
                    # 🔎 Fritekst-trapp basert på annonsen
                    from core.rent import get_rent_suggestion

                    queries = build_comp_queries(info)
                    MIN_POINTS = 6
                    for q in queries:
                        tried.append(q)
                        try:
                            s = get_rent_suggestion(
                                address=q,
                                areal_m2=target_area,
                                rom=target_rooms,
                                type=info.get("type") or "leilighet",
                            )
                        except Exception:
                            s = None
                        if s and s.suggested_rent > 0:
                            if s.n_used >= MIN_POINTS:
                                rent_suggestion = int(s.suggested_rent)
                                best_note = s.note
                                best_n_used, best_n_raw = s.n_used, s.n_raw
                                break
                            if s.n_used > best_n_used:
                                rent_suggestion = int(s.suggested_rent)
                                best_note = s.note + " (under terskel)"
                                best_n_used, best_n_raw = s.n_used, s.n_raw

                # --- 4) Lagre debug-snapshot så du kan se det i UI ---
                st.session_state["rent_debug"] = {
                    "tried_queries": tried,
                    "chosen_rent": rent_suggestion,
                    "n_used": best_n_used,
                    "n_raw": best_n_raw,
                    "note": best_note,
                    "area_m2": target_area,
                    "rooms": target_rooms,
                    "used_custom_url": use_custom,
                }

                # --- 5) Tilbakemelding + fallback ---
                if rent_suggestion:
                    st.toast(
                        f"Leieforslag: {rent_suggestion:,} kr  •  {best_n_used}/{best_n_raw} comps",
                        icon="✅",
                    )
                else:
                    rent_suggestion = params.get("rent") or 15000
                    st.toast(
                        "Fant ingen ferske leie-comps – brukte foreløpig verdi.",
                        icon="⚠️",
                    )

                # --- 6) Rente-estimat (hybrid) ---
                from core.rates import get_interest_estimate

                params["interest"] = float(get_interest_estimate())

                # --- 7) Oppdater felt og rerun ---
                params["price"] = price_from_finn
                params["equity"] = equity_from_price
                params["rent"] = rent_suggestion
                params["hoa"] = hoa_from_finn

                st.rerun()

        # 👇 Debug-panel (plasseres på samme nivå som k1/k2/k3)
        with st.expander("Debug: leie-comps (sist hentet)"):
            st.json(st.session_state.get("rent_debug") or {})

        with k3:
            # Lite URL-felt ved siden av "Hent data"
            url_val = st.text_input(
                "Lim inn FINN-søk (valgfritt)",
                key="custom_finn_url",
                placeholder="https://www.finn.no/realestate/lettings/search.html?...",
                label_visibility="collapsed",
                help="Valgfritt: Lim inn en FINN-søk-URL for mer presist område.",
            )

            # Liten info-ikon-boble (samme stil som før)
            st.markdown(
                """
                <style>
                  .td-info {
                    position: relative; 
                    display:flex; 
                    align-items:center; 
                    justify-content:flex-end; 
                    height:28px; 
                    margin-top:6px;
                  }
                  .td-info .ic {
                    width:18px; height:18px; opacity:.85; cursor:help;
                  }
                  .td-info .tip {
                    display:none; position:absolute; bottom:28px; right:0;
                    background:#111; color:#fff; padding:10px 12px; border-radius:6px;
                    font-size:13px; line-height:1.45; white-space:normal; word-wrap:break-word;
                    box-shadow:0 6px 16px rgba(0,0,0,.3); z-index:9999; width:420px; text-align:left;
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
                    Henter fra Finn-annonsen:<br>
                    • Kjøpesum<br>
                    • Egenkapital = 15 % av kjøpesum<br>
                    • Brutto leie (live comps når tilgjengelig)<br>
                    • Felleskostnader<br>
                    • Rente (DNB hvis mulig, ellers styringsrente + margin)
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        # --- Valgfri debug-panel for comps (vises bare når slått på) ---
        st.session_state.setdefault("show_rent_debug", False)
        with st.expander(
            "Debug: leie-comps (sist hentet)",
            expanded=st.session_state["show_rent_debug"],
        ):
            dbg = st.session_state.get("rent_debug")
            if dbg:
                st.json(dbg)
            else:
                st.caption("Ingen debug-data ennå – trykk **Hent data**.")
        st.session_state["show_rent_debug"] = st.checkbox(
            "Vis debug-panelet for leie-comps",
            value=st.session_state["show_rent_debug"],
            help="Praktisk under testing. Lagres i minnet for denne økten.",
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
        st.rerun()

    # --- Vis beregninger ---
    m = st.session_state.get("computed")
    if not m:
        return

    a, b, c = st.columns(3)
    with a:
        st.metric(
            "Cashflow (mnd)",
            f"{m['cashflow']:.0f} kr",
            help="Netto kontantstrøm per måned etter alle kostnader og finans.",
        )
        st.metric(
            "Lånebetaling (mnd)",
            f"{m['m_payment']:.0f} kr",
            help="Månedlig terminbeløp (renter + avdrag).",
        )
    with b:
        st.metric(
            "Break-even",
            f"{m['break_even']:.0f} kr/mnd",
            help="Nødvendig brutto leie for at månedlig cashflow blir 0 kr.",
        )
        st.metric(
            "NOI (år)",
            f"{m['noi_year']:.0f} kr",
            help="Netto driftsresultat (årlig) før finansiering.",
        )
    with c:
        st.metric(
            "Avdrag (år)",
            f"{m['principal_reduction_year']:.0f} kr",
            help="Sum årlige avdrag som reduserer lånesaldoen.",
        )
        st.metric(
            "ROE",
            f"{m['total_equity_return_pct']:.1f} %",
            help="Avkastning på egenkapital (cashflow + avdrag) relativt til egenkapital.",
        )

    st.markdown("---")
    st.subheader("🧠 AI-analyse")
    st.write(st.session_state.get("ai_text") or "")
