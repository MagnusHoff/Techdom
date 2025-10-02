# ui/result.py
from __future__ import annotations

import io
import html
import re
from urllib.parse import urlparse, parse_qs, urlunparse
from typing import Any, Dict, Optional, List, Iterable, Tuple, cast

import streamlit as st
# PDF-leser: prøv pypdf først, fallback til PyPDF2
try:
    from pypdf import PdfReader  # type: ignore
except Exception:
    from PyPDF2 import PdfReader  # type: ignore

from techdom.processing.ai import analyze_prospectus
from techdom.processing.rates import get_interest_estimate
from techdom.processing.rent import get_rent_by_csv
from techdom.domain.history import add_analysis
from techdom.ingestion.scrape import scrape_finn
from techdom.ingestion.fetch import get_prospect_or_scrape  # ⬅️ kun prospekt
from techdom.domain.analysis_service import (
    AnalysisDecisionContext,
    as_float as _as_float,
    as_int as _as_int,
    as_opt_float as _as_opt_float,
    as_str as _as_str,
    compute_analysis,
    default_equity as _default_equity,
)

# --------------------------- PDF tekstuttrekk ---------------------------

# Valgfri "rask" PDF-tekstuttrekk fra core (hvis du har en der)
try:
    from techdom.ingestion.scrape import extract_pdf_text_from_bytes as _EXTRACT_PDF_TEXT_FROM_BYTES  # type: ignore
except Exception:
    _EXTRACT_PDF_TEXT_FROM_BYTES = None  # type: ignore


def extract_pdf_text_from_bytes(data: bytes, max_pages: int = 40) -> str:
    """
    Hent tekst fra PDF-bytes.
    Bruker techdom.ingestion.scrape sin funksjon hvis den finnes, ellers fallback via (py)PdfReader.
    """
    if _EXTRACT_PDF_TEXT_FROM_BYTES:
        try:
            return _EXTRACT_PDF_TEXT_FROM_BYTES(data)  # type: ignore[misc]
        except Exception:
            pass  # fall tilbake til lokal metode

    try:
        reader = PdfReader(io.BytesIO(data))
        chunks: List[str] = []
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


def _interest_only_float() -> float:
    """get_interest_estimate() kan returnere float | (float, meta). Normaliser til float."""
    try:
        r = get_interest_estimate()
        if isinstance(r, tuple):
            return float(r[0])
        return float(r)
    except Exception:
        return 0.0


def _init_params_for_new_url(info: Dict[str, Any]) -> Dict[str, Any]:
    """Default-verdier ved ny FINN-URL."""
    price = _as_int(info.get("total_price"), 0)
    return {
        "price": price,
        "equity": _default_equity(price),
        "interest": 0.0,
        "term_years": 30,
        "rent": 0,
        "hoa": 0,
        "maint_pct": 0.0,
        "vacancy_pct": 0.0,
        "other_costs": 0,
    }


def _clean_url(u: str) -> str:
    """Dropp tracking/fragment for visning."""
    try:
        p = urlparse(u)
        return urlunparse((p.scheme, p.netloc, p.path, p.params, "", ""))
    except Exception:
        return u


def _color_class(color: Any) -> str:
    if isinstance(color, str):
        key = color.strip().lower()
    else:
        key = ""
    return {
        "red": "red",
        "orange": "orange",
        "green": "green",
        "neutral": "neutral",
    }.get(key, "neutral")


def _tg_lists_from_state() -> Tuple[List[str], List[str], bool]:
    res = cast(Dict[str, Any], st.session_state.get("prospectus_ai") or {})
    has_tg_keys = "tg2" in res or "tg3" in res
    tg2_items = list(cast(Iterable[str], res.get("tg2") or [])) if has_tg_keys else []
    tg3_items = list(cast(Iterable[str], res.get("tg3") or [])) if has_tg_keys else []
    has_tg_data = has_tg_keys
    return tg2_items, tg3_items, has_tg_data


def _run_manual_prospectus_analysis() -> None:
    if not st.session_state.get("prospectus_manual_running"):
        return
    if not st.session_state.get("prospectus_manual_execute"):
        return

    st.session_state["prospectus_manual_execute"] = False

    pdf_bytes_state = cast(Optional[bytes], st.session_state.get("prospectus_pdf_bytes"))
    if not pdf_bytes_state:
        st.session_state["prospectus_manual_pending"] = False
        st.session_state["prospectus_manual_running"] = False
        st.toast("Fant ikke PDF-data i minnet.", icon="⚠️")
        st.rerun()

    text = extract_pdf_text_from_bytes(pdf_bytes_state)
    if not text:
        st.session_state["prospectus_ai"] = {}
        st.session_state["prospectus_manual_prompt"] = True
        st.session_state["prospectus_manual_pending"] = True
        st.session_state["prospectus_manual_running"] = False
        st.toast("Fant ingen tekst i PDF-en.", icon="⚠️")
        st.rerun()

    try:
        analysis = analyze_prospectus(text) or {}
    except Exception:
        st.session_state["prospectus_ai"] = {}
        st.session_state["prospectus_manual_prompt"] = True
        st.session_state["prospectus_manual_pending"] = True
        st.session_state["prospectus_manual_running"] = False
        st.toast("Klarte ikke å analysere salgsoppgaven manuelt.", icon="⚠️")
        st.rerun()

    st.session_state["prospectus_ai"] = analysis
    st.session_state["prospectus_manual_prompt"] = False
    st.session_state["prospectus_manual_pending"] = False
    st.toast("Salgsoppgave analysert.", icon="✅")
    st.session_state["_queued_params"] = dict(st.session_state.get("params", {}))
    st.session_state["_updating"] = True
    st.rerun()


def _render_manual_prospectus_upload() -> None:
    manual_container = st.container()

    with manual_container:
        manual_container.markdown(
            """
            <div class="aiR-grid aiR-manual-grid">
              <div class="aiR-cell aiR-span2">
                <div class="aiR-card aiR-manual-card">
                  <div class="aiR-title">📄 Legg til salgsoppgave manuelt</div>
                  <div class="aiR-manual-text">
                    Fant ikke salgsoppgaven automatisk.<br>Gjerne last opp PDF-en fra megler så analyserer vi den for deg.
                  </div>
            """,
            unsafe_allow_html=True,
        )
        uploaded = st.file_uploader(
            "Last opp salgsoppgave (PDF)",
            type=["pdf"],
            key="prospectus_manual_upload",
            label_visibility="collapsed",
        )
        if uploaded is None and st.session_state.get("prospectus_manual_token"):
            st.session_state["prospectus_manual_token"] = ""
            st.session_state["prospectus_manual_pending"] = False
            st.session_state["prospectus_manual_running"] = False
            st.session_state["prospectus_manual_execute"] = False
            st.session_state.pop("prospectus_pdf_bytes", None)
        manual_container.markdown(
            """
                </div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    if uploaded:
        file_id = f"{uploaded.name}:{uploaded.size}"
        if st.session_state.get("prospectus_manual_token") != file_id:
            pdf_bytes = uploaded.read()
            st.session_state["prospectus_manual_token"] = file_id

            if not pdf_bytes:
                st.session_state["prospectus_manual_prompt"] = True
                st.session_state["prospectus_manual_pending"] = False
                st.session_state["prospectus_manual_execute"] = False
                st.toast("Kunne ikke lese salgsoppgaven – filen var tom.", icon="⚠️")
                st.rerun()
                return

            st.session_state["prospectus_pdf_bytes"] = pdf_bytes
            st.session_state.pop("prospectus_pdf_url", None)
            st.session_state["prospectus_ai"] = {}
            st.session_state["prospectus_manual_pending"] = True
            st.session_state["prospectus_manual_prompt"] = True
            st.session_state["prospectus_manual_running"] = False
            st.session_state["prospectus_manual_execute"] = False
            st.toast("PDF klar – trykk Re-analyser for å lese den.", icon="ℹ️")
            st.rerun()
            return

    has_pdf_bytes = bool(st.session_state.get("prospectus_pdf_bytes"))
    manual_pending = bool(st.session_state.get("prospectus_manual_pending"))

    if not has_pdf_bytes or not manual_pending:
        return

    with st.form("prospectus_manual_reanalyze"):
        col_btn, col_spin = st.columns([0.2, 0.05])
        with col_btn:
            reanalyze_clicked = st.form_submit_button(
                "Re-analyser",
                use_container_width=False,
                disabled=bool(st.session_state.get("prospectus_manual_running")),
            )
        with col_spin:
            if st.session_state.get("prospectus_manual_running"):
                st.markdown('<div class="aiR-manual-spinner"></div>', unsafe_allow_html=True)

    if reanalyze_clicked:
        st.session_state["prospectus_manual_running"] = True
        st.session_state["prospectus_manual_execute"] = True
        st.rerun()

    if st.session_state.get("prospectus_manual_running"):
        _run_manual_prospectus_analysis()


# --------------------------- main view ---------------------------


def render_result() -> None:
    # --- URL-guard ---
    url = _as_str(st.session_state.get("listing_url"))
    if not url:
        st.session_state["page"] = "landing"
        st.rerun()
        return

    # --- Init flags/state ---
    st.session_state.setdefault("_first_compute_done", False)
    st.session_state.setdefault("_updating", False)  # for "Kjør analyse"
    st.session_state.setdefault("_fetching", False)  # for "Hent data"
    st.session_state.setdefault("_history_logged", False)
    st.session_state.setdefault("prospectus_ai", {})
    st.session_state.setdefault("prospectus_manual_prompt", False)
    st.session_state.setdefault("prospectus_manual_token", "")
    st.session_state.setdefault("prospectus_manual_pending", False)
    st.session_state.setdefault("prospectus_manual_running", False)
    st.session_state.setdefault("prospectus_manual_execute", False)
    st.session_state.setdefault("show_details_modal", False)
    st.session_state.setdefault("decision_result", None)
    st.session_state.setdefault("decision_ui", {})

    # En felles "busy"-flag for å låse inputs/knapper
    busy = bool(st.session_state.get("_updating") or st.session_state.get("_fetching"))

    # --- Scrape ved ny URL + init params ---
    if st.session_state.get("_scraped_url") != url:
        info: Dict[str, Any] = scrape_finn(url) or {}
        st.session_state["_scraped_url"] = url
        st.session_state["_scraped_info"] = info
        st.session_state["computed"] = None
        st.session_state["decision_result"] = None
        st.session_state["decision_ui"] = {}
        st.session_state["params"] = _init_params_for_new_url(info)
        st.session_state["_first_compute_done"] = False
        st.session_state["_history_logged"] = False
        # Nullstill tidligere prospekt-ting
        for key in (
            "prospectus_pdf_bytes",
            "prospectus_pdf_url",
            "prospectus_ai",
            "prospectus_debug",
        ):
            st.session_state.pop(key, None)
        st.session_state["prospectus_source_url"] = url
        st.session_state["prospectus_manual_prompt"] = False
        st.session_state["prospectus_manual_token"] = ""
        st.session_state["prospectus_manual_pending"] = False
        st.session_state["prospectus_manual_running"] = False
        st.session_state["prospectus_manual_execute"] = False
    else:
        info = cast(Dict[str, Any], st.session_state.get("_scraped_info", {}) or {})

    params = cast(Dict[str, Any], st.session_state["params"])

    # --- Tittel + tre små lenkeknapper på samme linje ---
    address = _as_str(info.get("address")).strip()

    # Felles stil for chip-lenker (alle tre helt like)
    st.markdown(
        """
        <style>
          #hdr_chips { display:flex; justify-content:flex-end; }
          #hdr_chips_row { display:flex; gap:5px; align-items:center; }
          a.chip, span.chip {
            display:inline-flex; align-items:center; justify-content:center;
            padding:7px 16px;
            font-size:14px; font-weight:600;
            line-height:1;
            color:#E7ECFF !important;
            text-decoration:none !important;
            white-space:nowrap;
            background:transparent;
            border:1px solid rgba(255,255,255,.35);
            border-radius:8px;
          }
          a.chip:hover { background:rgba(255,255,255,.06); }
          .chip.disabled { opacity:.55; pointer-events:none; cursor:default; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    hdr_left, hdr_right = st.columns([0.68, 0.32], gap="small")
    with hdr_left:
        if address:
            st.subheader(address)

    with hdr_right:
        pdf_url = _as_str(st.session_state.get("prospectus_pdf_url")) or None
        listing_url = url.strip() or None

        chips = []
        if pdf_url:
            safe_pdf_url = html.escape(pdf_url, quote=True)
            chips.append(
                f'<a class="chip" href="{safe_pdf_url}" target="_blank" rel="noopener">Salgsoppgave</a>'
            )
        else:
            chips.append('<span class="chip disabled">Salgsoppgave</span>')

        if listing_url:
            safe_listing_url = html.escape(listing_url, quote=True)
            chips.append(
                f'<a class="chip" href="{safe_listing_url}" target="_blank" rel="noopener">Annonse</a>'
            )
        else:
            chips.append('<span class="chip disabled">Annonse</span>')

        # Alltid grå ut "Alle detaljer" (placeholder)
        chips.append('<span class="chip disabled">Alle detaljer</span>')

        st.markdown(
            f'<div id="hdr_chips"><div id="hdr_chips_row">{"".join(chips)}</div></div>',
            unsafe_allow_html=True,
        )

    # --- Layout ---
    left, right = st.columns([6, 6], gap="large")

    # ---------- VENSTRE ----------
    with left:
        img_url = _as_str(info.get("image"))
        if img_url:
            st.image(img_url, use_container_width=True)

    # ---------- HØYRE ----------
    with right:
        st.markdown(
            """
            <style>
              [role="tooltip"], [data-testid="stTooltip"] {
                background:#000 !important;
                color:#fff !important;
                border:1px solid rgba(255,255,255,.18) !important;
                box-shadow:0 6px 16px rgba(0,0,0,.35) !important;
              }
              [data-testid="stTooltipContent"] {
                color:#fff !important;
                white-space:normal !important;
                font-size:13px;
                line-height:1.4;
              }
              [data-testid="stTooltipContent"] * {
                color:inherit !important;
              }
            </style>
            """,
            unsafe_allow_html=True,
        )
        # --- Parametere (inputfeltene) ---
        st.markdown("**Parametre**")
        c1, c2 = st.columns(2)

        with c1:
            params["price"] = st.number_input(
                "Total kjøpesum (kr)",
                min_value=0,
                step=50_000,
                value=_as_int(params.get("price"), 0),
                help="Sum kjøpesum inkl. omkostninger fra annonsen.",
                disabled=busy,
            )
            params["equity"] = st.number_input(
                "Egenkapital (kr)",
                min_value=0,
                step=10_000,
                value=_as_int(params.get("equity"), 0),
                help="Kontanter/egenkapital du legger inn i kjøpet.",
                disabled=busy,
            )
            params["interest"] = st.number_input(
                "Rente (% p.a.)",
                min_value=0.0,
                step=0.1,
                value=_as_float(params.get("interest"), 0.0),
                help="Nominell årlig rente brukt i låneberegningen.",
                disabled=busy,
            )
            params["term_years"] = st.number_input(
                "Lånetid (år)",
                min_value=1,
                max_value=40,
                step=1,
                value=_as_int(params.get("term_years"), 30),
                help="Nedbetalingstid for annuitetslånet (år).",
                disabled=busy,
            )

        with c2:
            params["rent"] = st.number_input(
                "Brutto leie (kr/mnd)",
                min_value=0,
                step=500,
                value=_as_int(params.get("rent"), 0),
                help="Estimert månedlig husleie før kostnader (foreløpig Oslo/Bergen).",
                disabled=busy,
            )
            params["hoa"] = st.number_input(
                "Felleskost. (kr/mnd)",
                min_value=0,
                step=100,
                value=_as_int(params.get("hoa"), 0),
                help="Månedlige felleskostnader (TV/internett inkludert hvis oppgitt).",
                disabled=busy,
            )
            params["maint_pct"] = st.number_input(
                "Vedlikehold (% av leie)",
                min_value=0.0,
                step=0.5,
                value=_as_float(params.get("maint_pct"), 0.0),
                help="Avsatt vedlikehold i prosent av brutto leie.",
                disabled=busy,
            )
            params["other_costs"] = st.number_input(
                "Andre kostn. (kr/mnd)",
                min_value=0,
                step=100,
                value=_as_int(params.get("other_costs"), 0),
                help="Andre månedlige driftskostnader (strøm, forsikring, mv.).",
                disabled=busy,
            )

        # --- Felles CSS for liten spinner (ved siden av "Hent data") ---
        st.markdown(
            """
            <style>
              .btn-spin {
                width: 16px; height: 16px; margin-left: 10px;
                border: 2px solid rgba(255,255,255,.35);
                border-top-color: #fff; border-radius: 50%;
                animation: tdspn .8s linear infinite;
                display: inline-block;
                vertical-align: middle;
              }
              @keyframes tdspn { to { transform: rotate(360deg); } }
            </style>
            """,
            unsafe_allow_html=True,
        )

        busy = bool(st.session_state.get("_fetching")) or bool(
            st.session_state.get("_updating")
        )

        # --- Bunn-knapper: analyse / hent data / infoboble ---
        k1, k2, k3 = st.columns([6, 3, 1], gap="small")

        # ---------------- K1: KJØR ANALYSE ----------------
        with k1:
            updating = bool(st.session_state.get("_updating"))
            label = "Kjører analyse …" if updating else "Kjør analyse"
            if st.button(label, use_container_width=True, disabled=busy):
                st.session_state["_updating"] = True
                st.session_state["_queued_params"] = dict(params)
                st.rerun()

        # ---------------- K2: HENT DATA ----------------
        with k2:
            # Nullstill lagret PDF/AI når FINN-url endres
            current_url = url.strip()
            prev_url = _as_str(st.session_state.get("prospectus_source_url"))
            if current_url and current_url != prev_url:
                for key in (
                    "prospectus_pdf_bytes",
                    "prospectus_pdf_url",
                    "prospectus_ai",
                    "prospectus_debug",
                ):
                    st.session_state.pop(key, None)
                st.session_state["prospectus_source_url"] = current_url

            col_btn, col_spin = st.columns([1, 0.1])
            with col_btn:
                fetching = bool(st.session_state.get("_fetching"))
                fetch_label = "Henter …" if fetching else "Hent data"
                if st.button(
                    fetch_label,
                    use_container_width=True,
                    key="fetch_btn",
                    disabled=busy,
                ):
                    st.session_state["_fetching"] = True
                    st.rerun()

            with col_spin:
                if busy:
                    st.markdown('<div class="btn-spin"></div>', unsafe_allow_html=True)

            # Gjør jobben når _fetching = True
            if st.session_state.get("_fetching"):
                try:
                    # 1) Tall fra FINN
                    price_from_finn = _as_int(info.get("total_price"), 0)
                    hoa_from_finn = _as_int(info.get("hoa_month"), 0)

                    # 2) Egenkapital 15 %
                    equity_from_price = _default_equity(price_from_finn)

                    # 3) CSV/Geo-estimat (leie)
                    target_area = _as_opt_float(info.get("area_m2"))
                    target_rooms = _as_int(info.get("rooms"), 0) or None
                    est = get_rent_by_csv(info, area_m2=target_area, rooms=target_rooms)

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
                            f"Leieforslag: {rent_suggestion:,} kr  •  {est.bucket}  •  {est.updated}".replace(
                                ",", " "
                            ),
                            icon="✅",
                        )
                    else:
                        rent_suggestion = (
                            _as_int(
                                st.session_state.get("params", {}).get("rent"), 15000
                            )
                            or 15000
                        )
                        st.session_state["rent_debug"] = {
                            "source": "csv",
                            "error": "Fant ikke m²-tabell – brukte foreløpig verdi.",
                            "area_m2": target_area,
                            "rooms": target_rooms,
                        }
                        st.toast(
                            "Fant ikke m²-tabell – brukte foreløpig verdi.", icon="⚠️"
                        )

                    # 4) Rente-estimat
                    params["interest"] = _interest_only_float()

                    # 5) Hent PROSPEKT: bruk bytes + presigned URL direkte fra get_prospect_or_scrape()
                    pdf_bytes, presigned_url, pdf_dbg = get_prospect_or_scrape(
                        current_url
                    )
                    st.session_state["prospectus_debug"] = pdf_dbg

                    if pdf_bytes:
                        if presigned_url:
                            st.session_state["prospectus_pdf_url"] = presigned_url
                        else:
                            st.session_state.pop("prospectus_pdf_url", None)

                        st.session_state["prospectus_pdf_bytes"] = pdf_bytes
                        st.session_state["prospectus_source_url"] = current_url

                        text = extract_pdf_text_from_bytes(pdf_bytes)
                        if text:
                            st.session_state["prospectus_ai"] = analyze_prospectus(text)
                            st.session_state["prospectus_manual_prompt"] = False
                            st.session_state["prospectus_manual_token"] = ""
                            st.session_state["prospectus_manual_pending"] = False
                            st.session_state["prospectus_manual_running"] = False
                            st.session_state["prospectus_manual_execute"] = False
                            st.toast("Salgsoppgave hentet og analysert.", icon="✅")
                        else:
                            st.session_state.pop("prospectus_ai", None)
                            st.session_state["prospectus_manual_prompt"] = True
                            st.session_state["prospectus_manual_token"] = ""
                            st.session_state["prospectus_manual_pending"] = False
                            st.session_state["prospectus_manual_running"] = False
                            st.session_state["prospectus_manual_execute"] = False
                            st.caption(
                                "Klarte ikke å hente tekst fra PDF-en (kan være skannet). "
                                "Last opp en tekst-PDF manuelt."
                            )
                    else:
                        for key in (
                            "prospectus_pdf_bytes",
                            "prospectus_pdf_url",
                            "prospectus_ai",
                        ):
                            st.session_state.pop(key, None)
                        st.session_state["prospectus_manual_prompt"] = True
                        st.session_state["prospectus_manual_token"] = ""
                        st.session_state["prospectus_manual_pending"] = False
                        st.session_state["prospectus_manual_running"] = False
                        st.session_state["prospectus_manual_execute"] = False
                        st.caption(
                            "Fant ikke salgsoppgave automatisk – du kan laste opp PDF manuelt under."
                        )

                    # 6) Oppdater felter
                    params["price"] = price_from_finn
                    params["equity"] = equity_from_price
                    params["rent"] = rent_suggestion
                    params["hoa"] = hoa_from_finn

                finally:
                    st.session_state["_fetching"] = False
                    st.rerun()

        # ---------------- K3: Info-ikon ----------------
        with k3:
            st.markdown(
                """
                <style>
                  .td-info { position: relative; display:flex; justify-content:flex-end; height:28px; margin-top:6px; }
                  .td-info .ic { width:18px; height:18px; opacity:.85; cursor:help; }
                  .td-info .tip {
                    position:absolute; bottom:32px; right:0; background:#000; color:#fff;
                    padding:10px 12px; border-radius:6px; font-size:13px; line-height:1.45;
                    white-space:normal; box-shadow:0 6px 16px rgba(0,0,0,.33); z-index:9999;
                    width:clamp(200px, 52vw, 360px); max-width:calc(100vw - 32px);
                    border:1px solid rgba(255,255,255,.18); opacity:0; visibility:hidden;
                    transition:opacity .12s ease;
                  }
                  .td-info:hover .tip { opacity:1; visibility:visible; }
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

    # --- Kjør beregning når _updating = True ---
    if st.session_state["_updating"]:
        p = cast(Dict[str, Any], st.session_state.get("_queued_params") or dict(params))
        tg2_items, tg3_items, has_tg_data = _tg_lists_from_state()
        analysis = compute_analysis(
            p,
            AnalysisDecisionContext(
                tg2_items=tg2_items,
                tg3_items=tg3_items,
                tg_data_available=has_tg_data,
            ),
        )
        st.session_state["params"] = p
        st.session_state["computed"] = analysis.metrics
        st.session_state["decision_result"] = analysis.decision_result
        st.session_state["decision_ui"] = analysis.decision_ui
        st.session_state["ai_text"] = analysis.ai_text
        st.session_state["prospectus_manual_running"] = False
        st.session_state["_updating"] = False
        st.session_state["_first_compute_done"] = True
        st.rerun()

    # --- Vis beregninger ---
    m = cast(Dict[str, Any], st.session_state.get("computed") or {})
    if not m:
        return

    # Logg kun én gang når første gyldige resultat foreligger
    if not st.session_state.get("_history_logged"):
        info_now = cast(Dict[str, Any], st.session_state.get("_scraped_info", {}) or {})
        title = _as_str(info_now.get("address")).strip() or "Uten tittel"
        price_for_log = _as_int(info_now.get("total_price"), 0) or _as_int(
            st.session_state["params"].get("price"), 0
        )
        summary = _as_str(st.session_state.get("ai_text")).strip()[:200]
        image_url = _as_str(info_now.get("image"))
        add_analysis(
            finn_url=_as_str(st.session_state.get("listing_url")),
            title=title,
            price=price_for_log if price_for_log > 0 else None,
            summary=summary,
            image=image_url or None,
            result_args={"id": ""},
        )
        st.session_state["_history_logged"] = True

    # --- AI: tall vs. salgsoppgave (PDF) ---
    st.markdown("---")

    # Global CSS for AI-seksjonene (separate hooks + egne klasser)
    st.markdown(
        """
        <style>
          #ai-metrics     { margin-top: 0px; }
          #ai-prospectus  { margin-top: 0px; }

          .aiL-scorecard{
            background:linear-gradient(180deg,rgba(255,255,255,.03),rgba(255,255,255,.02));
            border:1px solid rgba(255,255,255,.12);
            border-radius:16px;
            padding:18px 20px;
            display:flex;
            flex-direction:column;
            gap:14px;
          }
          .aiL-scoreheader{display:flex; align-items:center; justify-content:space-between; gap:12px;}
          .aiL-scorevalue{font-size:46px; font-weight:800; line-height:1;}
          .aiL-scorelabel{font-size:12px; opacity:.7; letter-spacing:.06em; text-transform:uppercase;}
          .aiL-chip{
            display:inline-flex; align-items:center; gap:6px;
            font-size:13px; font-weight:600;
            padding:4px 12px; border-radius:999px;
            border:1px solid rgba(255,255,255,.16);
            background:rgba(255,255,255,.08);
            text-transform:uppercase; letter-spacing:.02em;
          }
          .aiL-chip.red{ color:#ff6b6b; border-color:rgba(239,68,68,.55); background:rgba(239,68,68,.16); }
          .aiL-chip.orange{ color:#ffa940; border-color:rgba(245,158,11,.55); background:rgba(245,158,11,.16); }
          .aiL-chip.green{ color:#3dd27c; border-color:rgba(74,222,128,.45); background:rgba(74,222,128,.16); }
          .aiL-chip.neutral{ color:rgba(255,255,255,.85); }
          .aiL-scorebar{ width:100%; height:10px; background:rgba(255,255,255,.08); border-radius:999px; overflow:hidden; }
          .aiL-scorefill{ height:100%; border-radius:inherit; transition:width .2s ease; }
          .aiL-scorefill.red{ background:linear-gradient(90deg,#ef4444,#f87171); }
          .aiL-scorefill.orange{ background:linear-gradient(90deg,#f59e0b,#fbbf24); }
          .aiL-scorefill.green{ background:linear-gradient(90deg,#10b981,#34d399); }
          .aiL-scorefill.neutral{ background:linear-gradient(90deg,#64748b,#94a3b8); }
          .aiL-scoretext{ font-size:14px; line-height:1.6; opacity:.9; }
          .aiL-scorewarn{ font-size:13px; line-height:1.6; opacity:.85; color:#fbbf24; margin-top:4px; }
          .aiL-scorenote{ font-size:13px; line-height:1.6; opacity:.7; font-style:italic; }
          .aiL-scorecard-ghost{ opacity:0; pointer-events:none; }

          .aiL-keygrid{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:14px; margin:20px 0 4px 0; }
          .aiL-keycard{
            background:linear-gradient(180deg,rgba(255,255,255,.02),rgba(255,255,255,.01));
            border:1px solid rgba(255,255,255,.10);
            border-radius:14px; padding:14px 16px;
            display:flex; flex-direction:column; gap:6px;
          }
          .aiL-keyname{ font-size:13px; opacity:.78; text-transform:uppercase; letter-spacing:.04em; }
          .aiL-keyvalue{ font-size:24px; font-weight:700; color:inherit; }
          .aiL-keyvalue.red{ color:#ff4d4f; }
          .aiL-keyvalue.orange{ color:#ffa940; }
          .aiL-keyvalue.green{ color:#3dd27c; }
          .aiL-keyvalue.neutral{ color:rgba(255,255,255,.92); }

          .aiL-grid{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:16px; margin-top:20px; align-items:stretch; }
          .aiL-card{
            background:linear-gradient(180deg,rgba(255,255,255,.03),rgba(255,255,255,.02));
            border:1px solid rgba(255,255,255,.12);
            border-radius:14px; padding:16px 18px; width:100%;
            display:flex; flex-direction:column; gap:10px;
          }
          .aiL-title{ font-weight:700; font-size:16px; margin:0; display:flex; align-items:center; gap:8px; }
          .aiL-card ul{ margin:0; padding-left:1.1rem; }
          .aiL-card li{ margin:.2rem 0; line-height:1.45; }
          .aiL-subtle{ opacity:.85; font-size:13px; }

          .aiR-offset{ height:52px; }

          .aiR-grid{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; margin-top:16px; align-items:stretch; }
          @media (max-width:1000px){ .aiR-grid{ grid-template-columns:1fr } }
          .aiR-cell{ min-height:100%; display:flex }
          .aiR-card{
            background:linear-gradient(180deg,rgba(255,255,255,.03),rgba(255,255,255,.02));
            border:1px solid rgba(255,255,255,.12);
            border-radius:14px; padding:16px 18px; width:100%;
            display:flex; flex-direction:column; gap:8px;
          }
          .aiR-manual-card{
            display:flex;
            flex-direction:column;
            gap:16px;
            padding:18px 20px;
          }
          .aiR-title{ display:flex; align-items:center; gap:10px; margin:0 0 4px 0; font-weight:700; font-size:16px }
          .aiR-badge{ display:inline-flex; align-items:center; gap:6px; font-size:12px; font-weight:600; padding:4px 8px; border-radius:999px; background:rgba(59,130,246,.15); border:1px solid rgba(59,130,246,.35) }
          .aiR-badge.warn{ background:rgba(245,158,11,.12); border-color:rgba(245,158,11,.35) }
          .aiR-badge.danger{ background:rgba(239,68,68,.12); border-color:rgba(239,68,68,.35) }
          .aiR-list{ margin:0; padding-left:1.15rem }
          .aiR-list li{ margin:.18rem 0 }
          .aiR-subtle{ opacity:.85; font-size:13px }
          .aiR-span2{ grid-column:1 / -1 }
          .aiR-manual-text{ font-size:14px; line-height:1.55; opacity:.88; }
          .aiR-manual-card form[data-testid="stForm"]{
            margin:0;
            display:inline-flex;
          }
          .aiR-manual-card form[data-testid="stForm"] button{
            background:transparent;
            border:1px solid rgba(255,255,255,.25);
            color:rgba(231,236,255,.92);
            border-radius:999px;
            padding:6px 18px;
            font-weight:600;
            font-size:13px;
            letter-spacing:.01em;
          }
          .aiR-manual-card form[data-testid="stForm"] button:hover{
            background:rgba(255,255,255,.08);
            border-color:rgba(255,255,255,.4);
          }
          .aiR-manual-card form[data-testid="stForm"] button:focus{
            outline:none;
            box-shadow:0 0 0 2px rgba(255,255,255,.25);
          }
          .aiR-manual-spinner{
            width:16px;
            height:16px;
            margin-top:6px;
            margin-left:6px;
            border:2px solid rgba(255,255,255,.25);
            border-top-color:#fff;
            border-radius:50%;
            animation: tdspn .8s linear infinite;
            display:inline-block;
          }
          .aiR-manual-card [data-testid="stFileUploader"]{
            background:rgba(15,23,42,.55);
            border:1px dashed rgba(255,255,255,.22);
            border-radius:12px;
            padding:12px 14px;
            margin-top:0;
          }
          .aiR-manual-card [data-testid="stFileUploader"] section{
            gap:8px;
            padding:0;
          }
          .aiR-manual-card [data-testid="stFileUploader"] label{ display:none; }
          .aiR-manual-card [data-testid="stFileUploader"] button{ border-radius:8px; font-weight:600; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    m_now = cast(Dict[str, Any], st.session_state.get("computed") or {})
    p_now = cast(Dict[str, Any], st.session_state.get("params") or {})
    decision_ui = cast(Dict[str, Any], st.session_state.get("decision_ui") or {})

    if not decision_ui and m_now:
        tg2_items, tg3_items, has_tg_data = _tg_lists_from_state()
        analysis = compute_analysis(
            p_now,
            AnalysisDecisionContext(
                tg2_items=tg2_items,
                tg3_items=tg3_items,
                tg_data_available=has_tg_data,
            ),
        )
        decision_ui = analysis.decision_ui
        st.session_state["decision_result"] = analysis.decision_result
        st.session_state["decision_ui"] = decision_ui

    score_html = ""
    if decision_ui:
        status = cast(Dict[str, Any], decision_ui.get("status", {}))
        scorelinjal = cast(Dict[str, Any], decision_ui.get("scorelinjal", {}))
        score_value = int(status.get("score") or scorelinjal.get("value") or 0)
        score_percent = max(0.0, min(100.0, float(scorelinjal.get("value", score_value))))
        score_color = _color_class(scorelinjal.get("farge"))
        dom_text = html.escape(_as_str(status.get("dom")))
        status_sentence = html.escape(_as_str(status.get("setning")))
        prospectus_missing = not bool(st.session_state.get("prospectus_ai"))
        manual_pending = bool(st.session_state.get("prospectus_manual_pending"))
        manual_running = bool(st.session_state.get("prospectus_manual_running"))
        manual_pending_or_running = manual_pending or manual_running
        score_warning_html = (
            "<div class=\"aiL-scorewarn\">⚠️ Scoren kan være lavere dersom salgsoppgaven mangler.</div>"
            if prospectus_missing and not manual_pending_or_running
            else ""
        )

        dom_note = html.escape(_as_str(decision_ui.get("dom_notat")))
        note_html = (
            f"<div class=\"aiL-scorenote\">{dom_note}</div>" if dom_note else ""
        )

        score_html = f"""
        <div class=\"aiL-scorecard\">
          <div class=\"aiL-scoreheader\">
            <div>
              <div class=\"aiL-scorevalue\">{score_value}</div>
              <div class=\"aiL-scorelabel\">Total score</div>
            </div>
            <div class=\"aiL-chip {score_color}\">{dom_text}</div>
          </div>
          <div class=\"aiL-scorebar\">
            <div class=\"aiL-scorefill {score_color}\" style=\"width:{score_percent:.0f}%;\"></div>
          </div>
          <div class=\"aiL-scoretext\">{status_sentence}</div>
          {score_warning_html}
          {note_html}
        </div>
        """

    left_ai, right_ai = st.columns([6, 6], gap="large")

    # ------------------- VENSTRE: Økonomi -------------------
    with left_ai:
        st.markdown('<div id="ai-metrics">', unsafe_allow_html=True)

        st.subheader("Resultat - forsterket av OpenAI")

        if not decision_ui:
            st.caption("Kjør analyse for å se vurderingen.")
        else:
            if score_html:
                st.markdown(score_html, unsafe_allow_html=True)

            key_cards: List[str] = []
            for fig in cast(List[Dict[str, Any]], decision_ui.get("nokkel_tall", [])):
                name = html.escape(_as_str(fig.get("navn")))
                value = html.escape(_as_str(fig.get("verdi")))
                color_cls = _color_class(fig.get("farge"))
                key_cards.append(
                    f"<div class=\"aiL-keycard\"><div class=\"aiL-keyname\">{name}</div><div class=\"aiL-keyvalue {color_cls}\">{value}</div></div>"
                )
            if key_cards:
                st.markdown(
                    f"<div class=\"aiL-keygrid\">{''.join(key_cards)}</div>",
                    unsafe_allow_html=True,
                )

            def _render_list_card(title: str, items: List[str], empty_msg: str) -> str:
                esc_title = html.escape(title)
                if items:
                    lis = "".join(
                        f"<li>{html.escape(_as_str(it))}</li>" for it in items[:4]
                    )
                    body = f"<ul>{lis}</ul>"
                else:
                    body = f"<div class=\"aiL-subtle\">{html.escape(empty_msg)}</div>"
                return f"<div class=\"aiL-card\"><div class=\"aiL-title\">{esc_title}</div>{body}</div>"

            tiltak_card = _render_list_card(
                "🔧 Tiltak",
                cast(List[str], decision_ui.get("tiltak", [])),
                "Ingen tiltak anbefalt nå.",
            )
            positivt_card = _render_list_card(
                "✅ Det som er bra",
                cast(List[str], decision_ui.get("positivt", [])),
                "Ingen positive funn registrert ennå.",
            )
            risiko_card = _render_list_card(
                "⚠️ Risiko",
                cast(List[str], decision_ui.get("risiko", [])),
                "Ingen risikopunkter identifisert ennå.",
            )

            st.markdown(
                f"<div class=\"aiL-grid\">{tiltak_card}{positivt_card}{risiko_card}</div>",
                unsafe_allow_html=True,
            )

        st.markdown("</div>", unsafe_allow_html=True)

    # ------------------- HØYRE: Salgsoppgave -------------------
    with right_ai:
        st.markdown('<div id="ai-prospectus">', unsafe_allow_html=True)

        res = cast(Dict[str, Any], st.session_state.get("prospectus_ai") or {})
        manual_prompt = bool(st.session_state.get("prospectus_manual_prompt"))
        if not res:
            if manual_prompt:
                if score_html:
                    ghost_score_html = score_html.replace(
                        "aiL-scorecard",
                        "aiL-scorecard aiL-scorecard-ghost",
                        1,
                    )
                    st.markdown(ghost_score_html, unsafe_allow_html=True)
                    st.markdown('<div class="aiR-offset"></div>', unsafe_allow_html=True)
                _render_manual_prospectus_upload()
            else:
                st.caption("Ingen salgsoppgave funnet eller analysert.")
            return

        if score_html:
            ghost_score_html = score_html.replace(
                "aiL-scorecard",
                "aiL-scorecard aiL-scorecard-ghost",
                1,
            )
            st.markdown(ghost_score_html, unsafe_allow_html=True)

        st.markdown('<div class="aiR-offset"></div>', unsafe_allow_html=True)

        # Lokalt helper for kort
        def _card(title_html: str, items: List[str]) -> str:
            if items:
                lis = "".join(f"<li>{html.escape(str(it))}</li>" for it in items)
                body = f'<ul class="aiR-list">{lis}</ul>'
            else:
                body = '<div class="aiR-subtle">Ingen punkter.</div>'
            return f'<div class="aiR-card"><div class="aiR-title">{title_html}</div>{body}</div>'

        tg3_html = _card(
            '🛑 TG3 (alvorlig) <span class="aiR-badge danger">Høy risiko</span>',
            res.get("tg3") or [],
        )
        tiltak_html = _card("🛠️ Tiltak / bør pusses opp", res.get("upgrades") or [])
        tg2_html = _card(
            '⚠️ TG2 <span class="aiR-badge warn">Middels risiko</span>',
            res.get("tg2") or [],
        )
        watch_html = _card("👀 Vær oppmerksom på", res.get("watchouts") or [])
        qs_list = cast(List[str], res.get("questions") or [])
        if qs_list:
            qs_html = _card("❓ Spørsmål til megler", qs_list[:6])
        else:
            qs_html = '<div class="aiR-card"><div class="aiR-title">❓ Spørsmål til megler</div><div class="aiR-subtle">Ingen spørsmål generert.</div></div>'

        grid_html = f"""
        <div class="aiR-grid">
          <div class="aiR-cell">{tg3_html}</div>
          <div class="aiR-cell">{tiltak_html}</div>
          <div class="aiR-cell">{tg2_html}</div>
          <div class="aiR-cell">{watch_html}</div>
          <div class="aiR-cell aiR-span2">{qs_html}</div>
        </div>
        """
        st.markdown(grid_html, unsafe_allow_html=True)

        st.markdown("</div>", unsafe_allow_html=True)
