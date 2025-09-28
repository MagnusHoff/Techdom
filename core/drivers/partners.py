# core/drivers/partners.py
from __future__ import annotations

import re
import io
from typing import Tuple, Dict, Any, Optional, List

from PyPDF2 import PdfReader
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

from .base import Driver
from core.config import SETTINGS
from core.browser_fetch import BROWSER_UA, _response_looks_like_pdf

PDF_MAGIC = b"%PDF-"
PDF_RX = re.compile(r"\.pdf(?:[\?#][^\s\"']*)?$", re.I)
# Webmegler / Reeltime gateway (PDF uten .pdf i URL)
PDF_URL_HINTS = re.compile(
    r"(wngetfile\.ashx|/getdocument|/getfile|/download|/proxy/webmegler/)", re.I
)

# --- KUN PROSPEKT / SALGSOPPGAVE ---
POSITIVE_HINTS = re.compile(
    r"(salgsoppgav|prospekt|utskriftsvennlig|komplett|digital[_\-]?salgsoppgave|se\s+pdf|last\s+ned\s+pdf)",
    re.I,
)

# Blokker uønskede dokumenter (TR/energiattest/nabolag/egenerkl./budskjema/etc.)
NEGATIVE_HINTS = re.compile(
    r"(tilstandsrapport|boligsalgsrapport|takst|fidens|estates|ns[\s\-_]*3600|energiattest|"
    r"nabolag|nabolagsprofil|contentassets/nabolaget|egenerkl|budskjema|kjøpekontrakt|vilkår|terms|cookies)",
    re.I,
)

# Tekster vi klikker på i UI (kun prospekt)
CLICK_TEXTS = [
    "salgsoppgave",
    "prospekt",
    "komplett salgsoppgave",
    "utskriftsvennlig",
    "se pdf",
    "last ned pdf",
]

MIN_PAGES = 6
MIN_BYTES = 200_000  # moderat terskel; partners-prospekter kan variere


def _looks_like_pdf(b: bytes | None) -> bool:
    return isinstance(b, (bytes, bytearray)) and b.startswith(PDF_MAGIC)


def _min_pages(b: bytes, n: int = MIN_PAGES) -> bool:
    try:
        r = PdfReader(io.BytesIO(b))
        return len(r.pages) >= n
    except Exception:
        return False


def _first_pages_text(b: bytes, first: int = 3) -> str:
    try:
        r = PdfReader(io.BytesIO(b))
        out: List[str] = []
        for p in r.pages[:first]:
            try:
                t = (p.extract_text() or "").lower()
            except Exception:
                t = ""
            if t:
                out.append(t)
        return "\n".join(out)
    except Exception:
        return ""


def _url_is_candidate(u: str, ctype: str = "") -> bool:
    if not u:
        return False
    lo = u.lower()
    if NEGATIVE_HINTS.search(lo):
        return False
    return (
        "application/pdf" in (ctype or "").lower()
        or PDF_RX.search(lo) is not None
        or PDF_URL_HINTS.search(lo) is not None
        or POSITIVE_HINTS.search(lo) is not None
    )


def _is_prospect_pdf(b: bytes | None, url: Optional[str]) -> bool:
    if not _looks_like_pdf(b):
        return False
    if not b or len(b) < MIN_BYTES or not _min_pages(b, MIN_PAGES):
        return False
    lo = (url or "").lower()
    if NEGATIVE_HINTS.search(lo):
        return False
    # Sjekk at første sider ikke inneholder TR-signaler
    first_txt = _first_pages_text(b, 3)
    if first_txt and NEGATIVE_HINTS.search(first_txt):
        return False
    return True


class PartnersDriver(Driver):
    name = "partners"

    def matches(self, url: str) -> bool:
        lo = (url or "").lower()
        return "partners.no/eiendom/" in lo or "tenant=" in lo

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

                pdf_bytes: Optional[bytes] = None
                pdf_url: Optional[str] = None

                # --- Sniff alle responses (fanger reeltime/proxy + wngetfile) ---
                def handle_response(resp):
                    nonlocal pdf_bytes, pdf_url
                    if pdf_bytes is not None:
                        return
                    try:
                        url = resp.url or ""
                        ctype = (resp.headers or {}).get("content-type", "").lower()
                    except Exception:
                        url, ctype = "", ""
                    if not url or not _url_is_candidate(url, ctype):
                        return
                    if _response_looks_like_pdf(resp):
                        try:
                            body = resp.body()
                        except Exception:
                            body = None
                        if body and _is_prospect_pdf(body, url):
                            pdf_bytes, pdf_url = body, url
                            dbg["response_hit"] = url

                page.on("response", handle_response)

                # --- Gå til siden ---
                try:
                    page.goto(
                        page_url,
                        wait_until="domcontentloaded",
                        timeout=SETTINGS.REQ_TIMEOUT * 1000,
                    )
                except PWTimeoutError:
                    page.goto(page_url, timeout=SETTINGS.REQ_TIMEOUT * 1000)

                # Godta cookies (best effort)
                try:
                    for sel in [
                        "#onetrust-accept-btn-handler",
                        "button:has-text('Godta')",
                        "button:has-text('Aksepter')",
                        "button:has-text('Tillat alle')",
                    ]:
                        el = page.locator(sel)
                        if el.count() > 0:
                            el.first.click(timeout=900)
                            break
                except Exception:
                    pass

                # Åpne "Vedlegg"/"Dokumenter" accordion hvis finnes
                try:
                    nodes = page.locator("button, [role='button'], summary, a")
                    n = nodes.count()
                    for i in range(min(n, 220)):
                        el = nodes.nth(i)
                        try:
                            raw = el.inner_text(timeout=250) or ""
                        except Exception:
                            raw = ""
                        t = raw.strip().lower()
                        if any(
                            k in t
                            for k in ("vedlegg", "dokument", "dokumenter", "last ned")
                        ):
                            try:
                                el.click(timeout=1200)
                                dbg["opened_documents"] = True
                                break
                            except Exception:
                                continue
                except Exception:
                    pass

                # Klikk KUN på prospekt/salgsoppgave
                attempts: List[Dict[str, Any]] = []
                try:
                    cands = page.locator("a[href], button, [role='button']")
                    n = cands.count()
                    for i in range(min(n, 300)):
                        el = cands.nth(i)
                        try:
                            raw = el.inner_text(timeout=250) or ""
                        except Exception:
                            raw = ""
                        low = raw.strip().lower()
                        hit = any(
                            k in low for k in CLICK_TEXTS
                        ) and not NEGATIVE_HINTS.search(low)
                        if len(attempts) < 120:
                            attempts.append(
                                {
                                    "index": i,
                                    "text_preview": raw[:90]
                                    + ("…" if len(raw) > 90 else ""),
                                    "match": hit,
                                }
                            )
                        if not hit:
                            continue

                        # Prøv direkte href
                        href = ""
                        try:
                            href = el.get_attribute("href") or ""
                        except Exception:
                            href = ""
                        if href and _url_is_candidate(href):
                            try:
                                rr = page.context.request.get(
                                    href,
                                    headers={
                                        "Accept": "application/pdf,application/octet-stream,*/*"
                                    },
                                    timeout=SETTINGS.REQ_TIMEOUT * 1000,
                                )
                                if rr.ok and _is_prospect_pdf(rr.body(), href):
                                    pdf_bytes, pdf_url = rr.body(), href
                                    dbg["click_direct_href"] = href
                                    break
                            except Exception:
                                pass

                        # Ellers: klikk for å trigge proxy/wngetfile
                        try:
                            el.scroll_into_view_if_needed(timeout=600)
                        except Exception:
                            pass
                        try:
                            el.click(timeout=1600)
                            dbg["click_hit"] = {"index": i, "text": raw[:200]}
                            page.wait_for_timeout(1200)
                            if pdf_bytes:
                                break
                        except Exception:
                            try:
                                el.click(timeout=1600, force=True)
                                dbg["click_hit_force"] = {"index": i, "text": raw[:200]}
                                page.wait_for_timeout(1200)
                                if pdf_bytes:
                                    break
                            except Exception:
                                continue
                except Exception:
                    pass
                dbg["click_attempts"] = attempts

                # Vent litt for XHR
                try:
                    page.wait_for_load_state("networkidle", timeout=3000)
                except Exception:
                    page.wait_for_timeout(800)

                # Harvest-lenker fra DOM/__NEXT_DATA__/scripts (inkl. /proxy/webmegler/)
                if not pdf_bytes:
                    harvested: List[str] = []
                    try:
                        urls = page.evaluate(
                            "(() => Array.from(document.querySelectorAll('a[href]')).map(a=>a.href))()"
                        )
                        if isinstance(urls, list):
                            harvested.extend([u for u in urls if isinstance(u, str)])
                    except Exception:
                        pass
                    try:
                        txt = page.evaluate(
                            "(() => { const el=document.getElementById('__NEXT_DATA__'); return el?el.textContent:null; })()"
                        )
                    except Exception:
                        txt = None
                    if isinstance(txt, str) and txt:
                        harvested += re.findall(
                            r'https?://[^"\'\s]+?\.pdf(?:\?[^"\'\s]*)?', txt, re.I
                        )
                        harvested += re.findall(
                            r'https?://[^"\'\s]+?(wngetfile\.ashx|/getdocument|/getfile|/download|/proxy/webmegler/)[^"\'\s]*',
                            txt,
                            re.I,
                        )
                    try:
                        scripts = page.locator("script")
                        n = scripts.count()
                        for i in range(min(n, 60)):
                            try:
                                content = scripts.nth(i).inner_text(timeout=200) or ""
                            except Exception:
                                continue
                            harvested += re.findall(
                                r'https?://[^"\'\s]+?\.pdf(?:\?[^"\'\s]*)?',
                                content,
                                re.I,
                            )
                            harvested += re.findall(
                                r'https?://[^"\'\s]+?(wngetfile\.ashx|/getdocument|/getfile|/download|/proxy/webmegler/)[^"\'\s]*',
                                content,
                                re.I,
                            )
                    except Exception:
                        pass

                    seen: set[str] = set()
                    uniq: List[str] = []
                    for u in harvested:
                        if (
                            isinstance(u, str)
                            and u not in seen
                            and _url_is_candidate(u)
                        ):
                            seen.add(u)
                            uniq.append(u)

                    # Score: prioriter Reeltime-proxy + wngetfile + positive prospekt-ord
                    def _score(u: str) -> int:
                        lo = u.lower()
                        sc = 0
                        if "/proxy/webmegler/" in lo:
                            sc += 200
                        if "wngetfile.ashx" in lo:
                            sc += 150
                        if POSITIVE_HINTS.search(lo):
                            sc += 60
                        if lo.endswith(".pdf"):
                            sc += 20
                        return sc

                    uniq.sort(key=_score, reverse=True)

                    for u in uniq[:25]:
                        try:
                            rr = context.request.get(
                                u,
                                headers={
                                    "Accept": "application/pdf,application/octet-stream,*/*"
                                },
                                timeout=SETTINGS.REQ_TIMEOUT * 1000,
                            )
                            if rr.ok and _is_prospect_pdf(rr.body(), u):
                                pdf_bytes, pdf_url = rr.body(), u
                                dbg["harvest_hit"] = u
                                break
                        except Exception:
                            continue

                # Nedlastings-event (for “tom side + tillat nedlasting”)
                if not pdf_bytes:
                    try:
                        dl = page.wait_for_event("download", timeout=2500)
                        if dl:
                            u = dl.url or ""
                            if _url_is_candidate(u):
                                rr = context.request.get(
                                    u,
                                    headers={
                                        "Accept": "application/pdf,application/octet-stream,*/*"
                                    },
                                    timeout=SETTINGS.REQ_TIMEOUT * 1000,
                                )
                                if rr.ok and _is_prospect_pdf(rr.body(), u):
                                    pdf_bytes, pdf_url = rr.body(), u
                                    dbg["download_hit"] = u
                    except Exception:
                        pass

                context.close()
                browser.close()

                if not pdf_bytes or not pdf_url:
                    dbg["step"] = "no_pdf_found"
                    return None, None, dbg

                dbg["step"] = "ok_prospect"
                return pdf_bytes, pdf_url, dbg

        except Exception as e:
            dbg["step"] = "exception"
            dbg["error"] = f"{type(e).__name__}: {e}"
            return None, None, dbg
