# ui/result.py
from __future__ import annotations

import re
import streamlit as st

# (IKKE importer extract_pdf_text_from_bytes fra core.scrape her)
from core.compute import compute_metrics
from core.ai import ai_explain, analyze_prospectus
from core.rates import get_interest_estimate
from core.rent import get_rent_by_csv
from core.history import add_analysis
from core.scrape import scrape_finn

# --- Robust PDF-tekstuttrekk: prøv å importere fra core.scrape, ellers fallback her ---
try:
    from core.scrape import extract_pdf_text_from_bytes as _extract_pdf_text_from_bytes  # type: ignore
except Exception:
    _extract_pdf_text_from_bytes = None  # type: ignore


def extract_pdf_text_from_bytes(data: bytes, max_pages: int = 40) -> str:
    """
    Hent tekst fra PDF-bytes. Bruker core.scrape sin funksjon hvis tilgjengelig,
    ellers en lokal fallback via PyPDF2.
    """
    if _extract_pdf_text_from_bytes is not None:
        try:
            return _extract_pdf_text_from_bytes(data)  # bruker core.scrape sin
        except Exception:
            pass

    # Fallback: lokal implementasjon
    import io
    from PyPDF2 import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        chunks = []
        for page in reader.pages[:max_pages]:
            try:
                t = page.extract_text() or ""
            except Exception:
                t = ""
            if t.strip():
                chunks.append(t)
        return "\n".join(chunks).strip()
    except Exception:
        return ""


# --------------------------- helpers ---------------------------


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


def _pdf_bytes_to_text(pdf_bytes: bytes, max_pages: int = 40) -> str:
    """Best-effort tekstuttrekk fra opplastet PDF (bytes)."""
    try:
        bio = io.BytesIO(pdf_bytes)
        reader = PdfReader(bio)
        chunks = []
        for page in reader.pages[:max_pages]:
            try:
                t = page.extract_text() or ""
            except Exception:
                t = ""
            if t.strip():
                chunks.append(t)
        return "\n".join(chunks).strip()
    except Exception:
        return ""


# --------------------------- main view ---------------------------


def render_result() -> None:
    # --- URL-guard ---
    url = st.session_state.get("listing_url") or ""
    if not url:
        st.session_state["page"] = "landing"
        st.rerun()
        return

    # --- Init flags/state ---
    st.session_state.setdefault("_first_compute_done", False)
    st.session_state.setdefault("_updating", False)
    st.session_state.setdefault("_history_logged", False)
    st.session_state.setdefault("prospectus_ai", {})

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

                # 2) Egenkapital 15 %
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
        st.session_state["_first_compute_done"] = True
        st.rerun()

    # --- Vis beregninger ---
    m = st.session_state.get("computed")
    if not m:
        return

    # Logg kun én gang til historikken når vi har første gyldige resultat
    if not st.session_state.get("_history_logged"):
        info_now = st.session_state.get("_scraped_info", {}) or {}
        title = (info_now.get("address") or "").strip() or "Uten tittel"
        price_for_log = int(info_now.get("total_price") or 0) or int(
            st.session_state["params"].get("price") or 0
        )
        summary = (st.session_state.get("ai_text") or "")[:200]
        image_url = info_now.get("image")

        add_analysis(
            finn_url=url,
            title=title,
            price=price_for_log if price_for_log > 0 else None,
            summary=summary,
            image=image_url,
            result_args={"id": ""},
        )
        st.session_state["_history_logged"] = True

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

    # --- AI: tall vs. salgsoppgave (PDF) ---
    st.markdown("---")
    left_ai, right_ai = st.columns([6, 6], gap="large")

    with left_ai:
        st.subheader("🧠 AI-analyse (tall)")

        m = st.session_state.get("computed") or {}
        p = st.session_state.get("params") or {}

        def kr(x):
            try:
                return f"{float(x):,.0f} kr".replace(",", " ").replace(".0", "")
            except Exception:
                return "–"

        def pct(x):
            try:
                return f"{float(x):.1f} %"
            except Exception:
                return "–"

        # --- Kjøp & finansiering ---
        with st.container(border=True):
            st.markdown("**📑 Kjøp & finansiering**")
            st.markdown(
                f"""
                • **Kjøpesum:** {kr(p.get("price", 0))}  
                • **Egenkapital (EK):** {kr(p.get("equity", 0))}  
                • **Rente:** {pct(p.get("interest", 0))}  
                • **Lånetid:** {int(p.get("term_years", 0)) or 0} år
                """
            )

        # --- Leieinntekter & kostnader ---
        with st.container(border=True):
            st.markdown("**📊 Leieinntekter & kostnader**")
            st.markdown(
                f"""
                • **Leieinntekt (brutto):** {kr(p.get("rent", 0))} / mnd  
                • **Felleskostnader:** {kr(p.get("hoa", 0))} / mnd  
                • **Vedlikehold:** {pct(p.get("maint_pct", 0))} av leie  
                • **Andre kostnader:** {kr(p.get("other_costs", 0))} / mnd
                """
            )

        # --- Kontantstrøm & avkastning ---
        with st.container(border=True):
            st.markdown("**💰 Kontantstrøm & avkastning**")
            if m:
                st.markdown(
                    f"""
                    • **Cashflow (mnd):** {kr(m.get("cashflow", 0))}  
                    • **Break-even (mnd):** {kr(m.get("break_even", 0))}  
                    • **NOI (år):** {kr(m.get("noi_year", 0))}  
                    • **Lånebetaling (mnd):** {kr(m.get("m_payment", 0))}  
                    • **ROE:** {pct(m.get("total_equity_return_pct", 0))}
                    """
                )
            else:
                st.caption("Kjør analyse for å fylle inn tallene.")

        # --- Oppsummering (tall) fra AI (valgfritt) ---
        ai_md = (st.session_state.get("ai_text") or "").strip()
        if ai_md:
            with st.container(border=True):
                st.markdown("**🧾 Oppsummering (tall)**")
                st.markdown(ai_md)

    with right_ai:
        # ---------- CSS (layout, cards, compact uploader) ----------
        st.markdown(
            """
            <style>
              /* two-column grid that stretches cards to equal height per row */
              .td-grid{
                display:grid;
                grid-template-columns:repeat(2,minmax(0,1fr));
                gap:16px;
                margin-top:18px;
                align-items:stretch;
              }
              @media (max-width:1000px){
                .td-grid{grid-template-columns:1fr;}
              }
              .td-cell{min-height:100%;display:flex}
              .td-card{
                background:linear-gradient(180deg,rgba(255,255,255,.03),rgba(255,255,255,.02));
                border:1px solid rgba(255,255,255,.12);
                border-radius:14px;
                padding:16px 18px;
                width:100%;
                display:flex;
                flex-direction:column;
                gap:8px;
              }
              .td-title{
                display:flex;align-items:center;gap:10px;
                margin:0 0 4px 0;font-weight:700;font-size:16px;
              }
              .td-badge{
                display:inline-flex;align-items:center;gap:6px;
                font-size:12px;font-weight:600;padding:4px 8px;border-radius:999px;
                background:rgba(59,130,246,.15);border:1px solid rgba(59,130,246,.35);
              }
              .td-badge.warn{background:rgba(245,158,11,.12);border-color:rgba(245,158,11,.35)}
              .td-badge.danger{background:rgba(239,68,68,.12);border-color:rgba(239,68,68,.35)}
              .td-list{margin:0;padding-left:1.15rem}
              .td-list li{margin:.18rem 0}
              .td-subtle{opacity:.85;font-size:13px}
              .td-span2{grid-column:1 / -1}

              /* Compact header: filename chip left + small dropzone right */
              .td-head{display:grid;grid-template-columns:1fr 280px;gap:16px;margin-bottom:8px}
              @media (max-width:1000px){ .td-head{grid-template-columns:1fr} }
              .td-chip{
                display:flex;align-items:center;gap:10px;
                background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.18);
                border-radius:10px;padding:10px 12px;min-height:46px;
              }
              .td-chip .x{
                margin-left:auto;opacity:.9;cursor:pointer;font-weight:700;
              }
              /* Hide streamlit’s built-in uploaded-file preview (we show our own chip) */
              div[data-testid="stUploadedFile"]{display:none !important;}
              /* Make dropzone compact */
              div[data-testid="stFileUploaderDropzone"]{padding:10px 12px !important;border-radius:10px !important;}
            </style>
            """,
            unsafe_allow_html=True,
        )

        # ---------- Header ----------
        st.subheader("📄 AI-analyse (salgsoppgave)")

        # Compact header: filename chip (left) + uploader (right)
        head_l, head_r = st.columns([1, 0.58])
        with head_l:
            _u = st.session_state.get("prospectus_pdf")
            if _u is not None:
                try:
                    _name = getattr(_u, "name", "salgsoppgave.pdf")
                    _size_mb = (
                        f"{(len(_u.getvalue())/1_048_576):.1f}MB"
                        if hasattr(_u, "getvalue")
                        else ""
                    )
                except Exception:
                    _name, _size_mb = "salgsoppgave.pdf", ""
                st.markdown(
                    f'<div class="td-chip">📄 <b>{_name}</b> <span style="opacity:.7">{_size_mb}</span>'
                    f'<span class="x" onclick="window.dispatchEvent(new Event(\'td-clear-pdf\'))">✕</span></div>',
                    unsafe_allow_html=True,
                )
                # Small JS hook to request rerun + clear; Streamlit will ignore JS, so keep a real button too.
                if st.button("Fjern", key="td_clear_pdf_btn", help="Fjern valgt PDF"):
                    st.session_state["prospectus_pdf"] = None
                    st.session_state.pop("prospectus_ai", None)
                    st.rerun()
            else:
                st.markdown(
                    '<div class="td-chip">📄 <i>Ingen fil valgt</i></div>',
                    unsafe_allow_html=True,
                )

        with head_r:
            uploaded = st.file_uploader(
                " ",
                type=["pdf"],
                accept_multiple_files=False,
                key="prospectus_pdf",
                label_visibility="collapsed",
            )

        # ---------- Analyse-knapp ----------
        loading = st.session_state.get("prospectus_loading", False)
        if st.button(
            "🌀 Analyserer…" if loading else "Analyser PDF",
            disabled=(uploaded is None or loading),
        ):
            if uploaded is not None and not loading:
                st.session_state["prospectus_loading"] = True
                with st.spinner("Analyserer PDF …"):
                    data = uploaded.read()
                    text = extract_pdf_text_from_bytes(data)
                    if text:
                        st.session_state["prospectus_ai"] = analyze_prospectus(text)
                    else:
                        st.error(
                            "Klarte ikke å hente tekst fra PDF-en (kan være skannet/bilde-PDF)."
                        )
                st.session_state["prospectus_loading"] = False
                st.rerun()

        # ---------- Resultat ----------
        res = st.session_state.get("prospectus_ai") or {}
        if not res:
            st.caption("Last opp en PDF og trykk «Analyser PDF» for å få vurdering.")
            st.stop()

        # Optional top summary from model
        if res.get("summary_md"):
            st.markdown(res["summary_md"])

        # Helper to build one full card as a single HTML block (prevents broken boxes)
        def _card(title_html: str, items: list[str]) -> str:
            if items:
                lis = "".join(
                    f"<li>{st._escape_markdown(it, unsafe_allow_html=True) if hasattr(st,'_escape_markdown') else it}</li>"
                    for it in items
                )
                body = f'<ul class="td-list">{lis}</ul>'
            else:
                body = '<div class="td-subtle">Ingen punkter.</div>'
            return f'<div class="td-card"><div class="td-title">{title_html}</div>{body}</div>'

        # Prepare sections
        tg3_html = _card(
            '🛑 TG3 (alvorlig) <span class="td-badge danger">Høy risiko</span>',
            res.get("tg3") or [],
        )
        tiltak_html = _card("🛠️ Tiltak / bør pusses opp", res.get("upgrades") or [])
        tg2_html = _card(
            '⚠️ TG2 <span class="td-badge warn">Middels risiko</span>',
            res.get("tg2") or [],
        )
        watch_html = _card("👀 Vær oppmerksom på", res.get("watchouts") or [])
        qs_list = res.get("questions") or []
        if qs_list:
            qs_html = _card("❓ Spørsmål til megler", qs_list[:6])
        else:
            qs_html = '<div class="td-card"><div class="td-title">❓ Spørsmål til megler</div><div class="td-subtle">Ingen spørsmål generert.</div></div>'

        # Render grid: two rows of two, then a full-width questions card
        grid_html = f"""
        <div class="td-grid">
          <div class="td-cell">{tg3_html}</div>
          <div class="td-cell">{tiltak_html}</div>
          <div class="td-cell">{tg2_html}</div>
          <div class="td-cell">{watch_html}</div>
          <div class="td-cell td-span2">{qs_html}</div>
        </div>
        """
        st.markdown(grid_html, unsafe_allow_html=True)
