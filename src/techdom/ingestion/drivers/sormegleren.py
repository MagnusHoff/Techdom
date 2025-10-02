# core/drivers/sormegleren.py
from __future__ import annotations
import re, io
from typing import Tuple, Dict, Any, Optional, List

from PyPDF2 import PdfReader
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

from .base import Driver
from techdom.infrastructure.config import SETTINGS
from ..browser_fetch import BROWSER_UA
from .common import looks_like_pdf_bytes


# --- kun prospekt/samle-PDF ---

# Emvest: /{estateId}/2 er “Komplett/Utskriftsvennlig salgsoppgave”
ALLOW_URL_RX = re.compile(
    r"(digitalsalgsoppgave\.emvest\.no/[0-9a-f\-]{36}/2|/Vedlegg/Document|/Vedlegg/Dokument)",
    re.I,
)

# Ikke tillatt (TR, energi, nabolag, egenerkl., osv.)
NEGATIVE_RX = re.compile(
    r"(tilstandsrapport|boligsalgsrapport|ns[\s_\-]?3600|bygningssakkyndig|tilstandsgrader|"
    r"energimerke|energiattest|nabolag|nabolagsprofil|egenerkl|egenerklæring|budskjema|vilkår|terms|cookies)",
    re.I,
)

# Tekster vi klikker på for å få PROSPEKT (ikke TR)
COMBINED_LABELS = [
    "komplett salgsoppgave",
    "salgsoppgave",
    "utskriftsvennlig",
    "se pdf",
    "last ned pdf",
    "for utskrift",
]

# Minstekrav for samle-PDF
MIN_PAGES = 6
MIN_BYTES = 200_000


def _looks_like_pdf(b: Optional[bytes]) -> bool:
    return looks_like_pdf_bytes(b)


def _pdf_pages(b: bytes) -> int:
    try:
        return len(PdfReader(io.BytesIO(b)).pages)
    except Exception:
        return 0


def _first_pages_text(b: bytes, first: int = 3) -> str:
    try:
        r = PdfReader(io.BytesIO(b))
        out: List[str] = []
        for p in r.pages[: min(first, len(r.pages))]:
            try:
                t = p.extract_text() or ""
            except Exception:
                t = ""
            if t:
                out.append(t.lower())
        return "\n".join(out)
    except Exception:
        return ""


def _is_prospect_pdf(b: bytes, url: Optional[str] = None) -> bool:
    if not looks_like_pdf_bytes(b):
        return False
    if len(b) < MIN_BYTES:
        return False
    if _pdf_pages(b) < MIN_PAGES:
        return False
    if url and NEGATIVE_RX.search(url):
        return False
    # Innholdet skal IKKE se ut som TR
    txt = _first_pages_text(b, 3)
    if NEGATIVE_RX.search(txt):
        return False
    return True


def _allowed_url(u: str, label: str = "") -> bool:
    if not isinstance(u, str) or not u:
        return False
    s = f"{label} {u}".lower()
    if NEGATIVE_RX.search(s):
        return False
    return ALLOW_URL_RX.search(u) is not None


class SorMeglerenDriver(Driver):
    name = "sormegleren"

    def matches(self, url: str) -> bool:
        lo = (url or "").lower()
        return (
            "bolig.eiendomsmeglervest.no/" in lo
            or "digitalsalgsoppgave.emvest.no/" in lo
            or "sormegleren.no/" in lo
        )

    def try_fetch(
        self, sess, page_url: str
    ) -> Tuple[bytes | None, str | None, Dict[str, Any]]:
        dbg: Dict[str, Any] = {
            "driver": self.name,
            "step": "start",
            "page_url": page_url,
        }

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    accept_downloads=True, user_agent=BROWSER_UA
                )
                page = context.new_page()

                # --- Gå til siden ---
                try:
                    page.goto(
                        page_url,
                        wait_until="domcontentloaded",
                        timeout=SETTINGS.REQ_TIMEOUT * 1000,
                    )
                except PWTimeoutError:
                    page.goto(page_url, timeout=SETTINGS.REQ_TIMEOUT * 1000)

                # --- Godta cookies (best effort) ---
                try:
                    for sel in [
                        "#onetrust-accept-btn-handler",
                        "button:has-text('Godta')",
                        "button:has-text('Aksepter')",
                        "button:has-text('Tillat alle')",
                    ]:
                        el = page.locator(sel)
                        if el.count() > 0:
                            el.first.click(timeout=800)
                            break
                except Exception:
                    pass

                # --- Klikk bare tydelige prospekt-knapper/lenker ---
                clicked = False
                try:
                    els = page.locator("a[href], button, [role='button']")
                    n = min(250, els.count())
                    for i in range(n):
                        el = els.nth(i)
                        try:
                            raw = (el.inner_text(timeout=250) or "").strip()
                            low = raw.lower()
                        except Exception:
                            raw, low = "", ""
                        if not low or any(
                            bad in low
                            for bad in ["tilstandsrapport", "boligsalgsrapport"]
                        ):
                            continue
                        if any(lbl in low for lbl in COMBINED_LABELS):
                            # Hvis elementet allerede har href – sjekk at det er lov
                            href = ""
                            try:
                                href = el.get_attribute("href") or ""
                            except Exception:
                                pass
                            if href and not _allowed_url(href, raw):
                                continue
                            try:
                                el.scroll_into_view_if_needed(timeout=600)
                            except Exception:
                                pass
                            try:
                                el.click(timeout=1600)
                            except Exception:
                                try:
                                    el.click(timeout=1600, force=True)
                                except Exception:
                                    continue
                            clicked = True
                            break
                except Exception:
                    pass

                # --- Gi viewer/nedlasting litt tid ---
                page.wait_for_timeout(1400)

                # --- Høst kandidat-URLer (DOM + __NEXT_DATA__ + scripts) ---
                harvested: List[str] = []
                try:
                    dom_urls = page.evaluate(
                        "(()=>Array.from(document.querySelectorAll('a[href]')).map(a=>({href:a.href,text:a.innerText||''})))()"
                    )
                    if isinstance(dom_urls, list):
                        for it in dom_urls:
                            if not isinstance(it, dict):
                                continue
                            href = it.get("href") or ""
                            text = it.get("text") or ""
                            if _allowed_url(href, text):
                                harvested.append(href)
                except Exception:
                    pass

                try:
                    next_txt = page.evaluate(
                        "(()=>{const el=document.getElementById('__NEXT_DATA__');return el?el.textContent:null})()"
                    )
                except Exception:
                    next_txt = None
                if isinstance(next_txt, str) and next_txt:
                    for m in re.finditer(r"https?://[^\s\"']+", next_txt):
                        u = m.group(0).replace("\\/", "/")
                        if _allowed_url(u):
                            harvested.append(u)

                try:
                    scripts = page.locator("script")
                    sN = min(60, scripts.count())
                    for i in range(sN):
                        try:
                            sc = scripts.nth(i).inner_text(timeout=200) or ""
                        except Exception:
                            continue
                        for m in re.finditer(r"https?://[^\s\"']+", sc):
                            u = m.group(0)
                            if _allowed_url(u):
                                harvested.append(u)
                except Exception:
                    pass

                # de-dupe
                seen = set()
                cand: List[str] = []
                for u in harvested:
                    if isinstance(u, str) and u not in seen:
                        seen.add(u)
                        cand.append(u)

                # --- Prøv kandidat-URLer (kun prospekt) ---
                pdf_bytes: Optional[bytes] = None
                pdf_url: Optional[str] = None
                for u in cand:
                    try:
                        r = context.request.get(
                            u,
                            headers={
                                "Accept": "application/pdf,application/octet-stream,*/*"
                            },
                            timeout=SETTINGS.REQ_TIMEOUT * 1000,
                        )
                        body = r.body() if r.ok else None
                        if body and _is_prospect_pdf(body, u):
                            pdf_bytes, pdf_url = body, u
                            break
                    except Exception:
                        continue

                # --- Nedlastings-event (siste sjanse) ---
                if not pdf_bytes:
                    try:
                        dl = page.wait_for_event("download", timeout=3000)
                        if dl:
                            u = dl.url or ""
                            if _allowed_url(u or ""):
                                r = context.request.get(
                                    u,
                                    headers={
                                        "Accept": "application/pdf,application/octet-stream,*/*"
                                    },
                                    timeout=SETTINGS.REQ_TIMEOUT * 1000,
                                )
                                body = r.body() if r.ok else None
                                if body and _is_prospect_pdf(body, u):
                                    pdf_bytes, pdf_url = body, u
                    except Exception:
                        pass

                context.close()
                browser.close()

                if not (pdf_bytes and pdf_url):
                    dbg["step"] = "no_pdf_found"
                    dbg["clicked"] = clicked
                    return None, None, dbg

                # Endelig prospekt-validering (belt & suspenders)
                if not _is_prospect_pdf(pdf_bytes, pdf_url):
                    dbg["step"] = "pdf_rejected_not_prospect"
                    dbg["clicked"] = clicked
                    return None, None, dbg

                dbg["meta"] = {"combined_prospectus": True, "source": "emvest"}
                dbg["clicked"] = clicked
                dbg["step"] = "ok_combined"
                return pdf_bytes, pdf_url, dbg

        except Exception as e:
            dbg["step"] = "exception"
            dbg["error"] = f"{type(e).__name__}: {e}"
            return None, None, dbg
