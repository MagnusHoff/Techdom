from __future__ import annotations
import re
import io
from typing import Optional, Tuple, Dict, Any, List
import requests
from bs4 import BeautifulSoup, Tag
from PyPDF2 import PdfReader

from ..http_headers import BROWSER_HEADERS
from ..sessions import new_session
from ..config import SETTINGS
from ..browser_fetch import BROWSER_UA, _response_looks_like_pdf  # reuse helpers
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

PDF_MAGIC = b"%PDF-"
PDF_RX = re.compile(r"\.pdf(?:[\?#][^\s\"']*)?$", re.I)
PDF_URL_HINTS = re.compile(
    r"(wngetfile\.ashx|/getdocument|/getfile|/download|/proxy/webmegler/.+/wngetfile\.ashx)",
    re.I,
)

# --- STRAMME REGLER ---
BLOCKLIST = re.compile(
    r"(nabolag|nabolagsprofil|contentassets/nabolaget|energiattest|egenerkl|salgsoppgave)",
    re.I,
)

WHITELIST_HINT = re.compile(
    r"(tilstandsrapport|boligsalgsrapport|fidens|estates|nordvik-vitec-documents)",
    re.I,
)

CLICK_TEXTS = [
    "tilstandsrapport",
    "se tilstandsrapport",
    "boligsalgsrapport",
    "takst",
    "fidens",
    "tilstandsrapport for",  # noen bygger hele filnavnet inn
]


def _looks_like_pdf(b: bytes) -> bool:
    return isinstance(b, (bytes, bytearray)) and b[:4] == PDF_MAGIC


def _min_pages(b: bytes, nmin: int = 2) -> bool:
    try:
        return len(PdfReader(io.BytesIO(b)).pages) >= nmin
    except Exception:
        return False


def _url_allowed(u: str) -> bool:
    lo = (u or "").lower()
    if not u:
        return False
    if BLOCKLIST.search(lo):
        return False
    # whitelist-hint er sterkt ønsket – men hvis mangler: innholdssjekk senere
    return True


def _tr_urlish(u: str) -> bool:
    return bool(WHITELIST_HINT.search((u or "")))


def _first_text_pages_have_tr(b: bytes, max_pages: int = 3) -> bool:
    try:
        r = PdfReader(io.BytesIO(b))
        pages = []
        for i, p in enumerate(r.pages[:max_pages]):
            try:
                t = p.extract_text() or ""
            except Exception:
                t = ""
            if t:
                pages.append(t.lower())
        txt = "\n".join(pages)
        return ("tilstandsrapport" in txt) or ("boligsalgsrapport" in txt)
    except Exception:
        return False


class NotarDriver:
    name = "notar"

    def matches(self, url: str) -> bool:
        u = (url or "").lower()
        return "notar.no/bolig-til-salgs/" in u

    def try_fetch(
        self, sess: requests.Session, page_url: str
    ) -> Tuple[bytes | None, str | None, Dict[str, Any]]:
        dbg: Dict[str, Any] = {
            "driver": self.name,
            "step": "start",
            "page_url": page_url,
        }

        # Primært Playwright-flow fordi Notar ofte laster dokumentlenker via JS
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    accept_downloads=True, user_agent=BROWSER_UA
                )
                page = context.new_page()

                # ---- response-sniff ----
                pdf_bytes: bytes | None = None
                pdf_url: str | None = None

                def handle_response(resp):
                    nonlocal pdf_bytes, pdf_url
                    if pdf_bytes is not None:
                        return
                    try:
                        url = resp.url or ""
                        ct = (resp.headers or {}).get("content-type", "").lower()
                    except Exception:
                        url, ct = "", ""

                    if not url or not _url_allowed(url):
                        return

                    looks_pdf = (
                        ("application/pdf" in ct)
                        or PDF_RX.search(url)
                        or PDF_URL_HINTS.search(url)
                        or _response_looks_like_pdf(resp)
                    )
                    if not looks_pdf:
                        return

                    try:
                        body = resp.body()
                    except Exception:
                        body = None

                    if body and _looks_like_pdf(body):
                        # whitelist-hint preferert; hvis ikke → innholdssjekk senere
                        pdf_bytes, pdf_url = body, url
                        dbg["response_hit"] = url

                page.on("response", handle_response)

                # ---- goto ----
                try:
                    page.goto(
                        page_url,
                        wait_until="domcontentloaded",
                        timeout=SETTINGS.REQ_TIMEOUT * 1000,
                    )
                except PWTimeoutError:
                    page.goto(page_url, timeout=SETTINGS.REQ_TIMEOUT * 1000)

                # cookie-accept (best effort)
                try:
                    # enkle tekstmatcher
                    for sel in [
                        "#onetrust-accept-btn-handler",
                        "button:has-text('Godta')",
                        "button:has-text('Aksepter')",
                        "button:has-text('Tillat alle')",
                    ]:
                        try:
                            el = page.locator(sel)
                            if el.count() > 0:
                                el.first.click(timeout=900)
                                break
                        except Exception:
                            continue
                except Exception:
                    pass

                # Scroll for lazy content
                try:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight/2)")
                    page.wait_for_timeout(400)
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(400)
                except Exception:
                    pass

                # Åpne "Dokumenter" / accordion dersom finnes
                try:
                    # Notar bruker ofte "Dokumenter" som knapp/accordion
                    btns = page.locator("button, [role='button'], a")
                    n = btns.count()
                    for i in range(min(n, 200)):
                        b = btns.nth(i)
                        try:
                            t = (b.inner_text(timeout=250) or "").strip().lower()
                        except Exception:
                            t = ""
                        if not t:
                            continue
                        if "dokument" in t and (
                            "se" in t or "vis" in t or "åpne" in t or "dokumenter" in t
                        ):
                            try:
                                b.click(timeout=1500)
                                dbg["opened_documents"] = True
                                break
                            except Exception:
                                continue
                except Exception:
                    pass

                # Klikk på TR-relaterte knapper/lenker
                attempts: List[Dict[str, Any]] = []
                try:
                    candidates = page.locator("a, button, [role='button']")
                    n = candidates.count()
                    for i in range(min(n, 250)):
                        el = candidates.nth(i)
                        try:
                            raw = el.inner_text(timeout=250) or ""
                        except Exception:
                            raw = ""
                        txt = raw.strip().lower()
                        matched = any(h in txt for h in CLICK_TEXTS)
                        if len(attempts) < 120:
                            attempts.append(
                                {
                                    "index": i,
                                    "text_preview": (
                                        raw[:90] + ("…" if len(raw) > 90 else "")
                                    ),
                                    "match": matched,
                                }
                            )
                        if not matched:
                            continue

                        # Prøv direkte href først
                        href = ""
                        try:
                            href = el.get_attribute("href") or ""
                        except Exception:
                            href = ""

                        if href and _url_allowed(href):
                            try:
                                rr = page.context.request.get(
                                    href,
                                    headers={
                                        "Accept": "application/pdf,application/octet-stream,*/*"
                                    },
                                    timeout=SETTINGS.REQ_TIMEOUT * 1000,
                                )
                                if rr.ok:
                                    body = rr.body()
                                    if _looks_like_pdf(body):
                                        pdf_bytes, pdf_url = body, href
                                        dbg["click_direct_href"] = href
                                        break
                            except Exception:
                                pass

                        # Ellers klikk for å trigge XHR/download
                        try:
                            el.scroll_into_view_if_needed(timeout=600)
                        except Exception:
                            pass
                        try:
                            el.click(timeout=1800)
                            dbg["click_hit"] = {"index": i, "text": raw[:200]}
                            # liten pause for XHR
                            page.wait_for_timeout(1200)
                            if pdf_bytes:
                                break
                        except Exception:
                            try:
                                el.click(timeout=1800, force=True)
                                dbg["click_hit_force"] = {"index": i, "text": raw[:200]}
                                page.wait_for_timeout(1200)
                                if pdf_bytes:
                                    break
                            except Exception:
                                continue
                except Exception:
                    pass

                dbg["click_attempts"] = attempts

                # Vent på network idle kort
                try:
                    page.wait_for_load_state("networkidle", timeout=3000)
                except Exception:
                    page.wait_for_timeout(800)

                # Fallback: harvest URL’er fra DOM/__NEXT_DATA__/script
                if not pdf_bytes:
                    harvested: List[str] = []

                    # a[href]
                    try:
                        urls = page.evaluate(
                            "(() => Array.from(document.querySelectorAll('a[href]')).map(a=>a.href))()"
                        )
                        if isinstance(urls, list):
                            harvested.extend([u for u in urls if isinstance(u, str)])
                    except Exception:
                        pass

                    # __NEXT_DATA__
                    try:
                        txt = page.evaluate(
                            "(() => { const el=document.getElementById('__NEXT_DATA__'); return el?el.textContent:null; })()"
                        )
                    except Exception:
                        txt = None
                    if isinstance(txt, str) and txt:
                        for m in re.finditer(
                            r'https?://[^"\'\s]+?\.pdf(?:\?[^"\'\s]*)?', txt, re.I
                        ):
                            harvested.append(m.group(0))
                        for m in re.finditer(
                            r'https?://[^"\'\s]+?(wngetfile\.ashx|/getdocument|/getfile|/download)[^"\'\s]*',
                            txt,
                            re.I,
                        ):
                            harvested.append(m.group(0))

                    # <script> bodies
                    try:
                        scripts = page.locator("script")
                        n = scripts.count()
                        for i in range(min(n, 60)):
                            try:
                                content = scripts.nth(i).inner_text(timeout=200) or ""
                            except Exception:
                                continue
                            for m in re.finditer(
                                r'https?://[^"\'\s]+?\.pdf(?:\?[^"\'\s]*)?',
                                content,
                                re.I,
                            ):
                                harvested.append(m.group(0))
                            for m in re.finditer(
                                r'https?://[^"\'\s]+?(wngetfile\.ashx|/getdocument|/getfile|/download)[^"\'\s]*',
                                content,
                                re.I,
                            ):
                                harvested.append(m.group(0))
                    except Exception:
                        pass

                    # uniq + filtrering
                    seen = set()
                    uniq: List[str] = []
                    for u in harvested:
                        if isinstance(u, str) and u not in seen and _url_allowed(u):
                            seen.add(u)
                            uniq.append(u)

                    # Prøv de mest lovende først (TR-hint)
                    uniq.sort(
                        key=lambda u: (1 if _tr_urlish(u) else 0, len(u)), reverse=True
                    )

                    for u in uniq[:20]:
                        try:
                            rr = context.request.get(
                                u,
                                headers={
                                    "Accept": "application/pdf,application/octet-stream,*/*"
                                },
                                timeout=SETTINGS.REQ_TIMEOUT * 1000,
                            )
                            if rr.ok and _looks_like_pdf(rr.body()):
                                pdf_bytes, pdf_url = rr.body(), u
                                dbg["harvest_hit"] = u
                                break
                        except Exception:
                            continue

                # download-event fallback
                if not pdf_bytes:
                    try:
                        dl = page.wait_for_event("download", timeout=2000)
                        if dl:
                            u = dl.url or ""
                            if _url_allowed(u):
                                rr = context.request.get(
                                    u,
                                    headers={
                                        "Accept": "application/pdf,application/octet-stream,*/*"
                                    },
                                    timeout=SETTINGS.REQ_TIMEOUT * 1000,
                                )
                                if rr.ok and _looks_like_pdf(rr.body()):
                                    pdf_bytes, pdf_url = rr.body(), u
                                    dbg["download_hit"] = u
                    except Exception:
                        pass

                context.close()
                browser.close()

                if not pdf_bytes or not pdf_url:
                    dbg["step"] = "no_pdf_found"
                    return None, None, dbg

                # Valider sider + (helst) TR-tekst
                if not _min_pages(pdf_bytes, 2):
                    dbg["step"] = "pdf_rejected_min_pages"
                    return None, None, dbg

                # Hvis URL mangler klare TR-ord → innholdssjekk
                tr_ok = True
                if not _tr_urlish(pdf_url):
                    tr_ok = _first_text_pages_have_tr(pdf_bytes)
                dbg["tr_text_found"] = tr_ok
                if not tr_ok:
                    dbg["step"] = "pdf_rejected_not_tr"
                    return None, None, dbg

                dbg["step"] = "ok"
                return pdf_bytes, pdf_url, dbg

        except Exception as e:
            dbg["step"] = "exception"
            dbg["error"] = f"{type(e).__name__}: {e}"
            return None, None, dbg
