# core/drivers/obosmegleren.py
from __future__ import annotations

import io
import re
from typing import Tuple, Dict, Any, List, Optional

from PyPDF2 import PdfReader
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

from .base import Driver
from core.config import SETTINGS
from core.browser_fetch import BROWSER_UA, _response_looks_like_pdf

PDF_MAGIC = b"%PDF-"
PDF_RX = re.compile(r"\.pdf(?:[\?#][^\s\"']*)?$", re.I)
# Webmegler-ender (attachment uten .pdf i URL)
PDF_URL_HINTS = re.compile(r"(wngetfile\.ashx|/getdocument|/getfile|/download)", re.I)

# --- Kun prospekt/salgsoppgave ---
POSITIVE_HINTS = re.compile(
    r"(salgsoppgav|prospekt|utskriftsvennlig|komplett|digital[_\-]?salgsoppgave|se\s+pdf|last\s+ned\s+pdf)",
    re.I,
)

# Alt dette skal ekskluderes
NEGATIVE_HINTS = re.compile(
    r"(tilstandsrapport|boligsalgsrapport|takst|fidens|estates|energiattest|nabolag|"
    r"nabolagsprofil|contentassets/nabolaget|egenerkl|budskjema|kjøpekontrakt|vilkår|terms|cookies)",
    re.I,
)

# Tekster vi aktivt klikker på i UI
CLICK_TEXTS = [
    "salgsoppgave",
    "prospekt",
    "komplett salgsoppgave",
    "utskriftsvennlig",
    "se pdf",
    "last ned pdf",
]

MIN_PAGES = 6
MIN_BYTES = 200_000  # konservativ grense, OBOS-prospekter kan variere


def _looks_like_pdf(b: bytes | None) -> bool:
    return isinstance(b, (bytes, bytearray)) and b.startswith(PDF_MAGIC)


def _min_pages(b: bytes, min_pages: int = MIN_PAGES) -> bool:
    try:
        r = PdfReader(io.BytesIO(b))
        return len(r.pages) >= min_pages
    except Exception:
        return False


def _first_pages_text(b: bytes, n: int = 3) -> str:
    try:
        r = PdfReader(io.BytesIO(b))
        parts: List[str] = []
        for p in r.pages[:n]:
            try:
                t = (p.extract_text() or "").lower()
            except Exception:
                t = ""
            if t:
                parts.append(t)
        return "\n".join(parts)
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
    first_txt = _first_pages_text(b, 3)
    if first_txt and NEGATIVE_HINTS.search(first_txt):
        return False
    return True


class ObosMeglerenDriver(Driver):
    name = "obosmegleren"

    def matches(self, url: str) -> bool:
        return "obos.no/brukt-bolig/" in (url or "").lower()

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

                # --- Sniff responses (wngetfile.ashx, direkte .pdf osv.) ---
                def handle_response(resp):
                    nonlocal pdf_bytes, pdf_url
                    if pdf_bytes is not None:
                        return
                    try:
                        url = resp.url or ""
                        ctype = (resp.headers or {}).get("content-type", "").lower()
                    except Exception:
                        url, ctype = "", ""

                    if not _url_is_candidate(url, ctype):
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

                # --- Naviger til siden ---
                try:
                    page.goto(
                        page_url,
                        wait_until="domcontentloaded",
                        timeout=SETTINGS.REQ_TIMEOUT * 1000,
                    )
                except PWTimeoutError:
                    page.goto(page_url, timeout=SETTINGS.REQ_TIMEOUT * 1000)

                # Godta cookies (beste-effort)
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

                # Åpne "Last ned dokumenter" / "Dokumenter"
                try:
                    nodes = page.locator("button, [role='button'], summary, a")
                    for i in range(min(nodes.count(), 200)):
                        el = nodes.nth(i)
                        try:
                            raw = el.inner_text(timeout=250) or ""
                        except Exception:
                            raw = ""
                        txt = raw.strip().lower()
                        if ("last ned dokument" in txt) or ("dokumenter" in txt):
                            try:
                                el.click(timeout=1200)
                                dbg["opened_documents"] = True
                                break
                            except Exception:
                                continue
                except Exception:
                    pass

                # Klikk KUN på salgsoppgave/prospekt-lenker
                attempts: List[Dict[str, Any]] = []
                try:
                    cands = page.locator("a[href], button, [role='button']")
                    for i in range(min(cands.count(), 300)):
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

                        # 1) Direkte via href
                        href = ""
                        try:
                            href = el.get_attribute("href") or ""
                        except Exception:
                            href = ""
                        if href and _url_is_candidate(href):
                            try:
                                rr = context.request.get(
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

                        # 2) Klikk for å trigge XHR/download
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

                # Vent litt for sen XHR
                try:
                    page.wait_for_load_state("networkidle", timeout=3000)
                except Exception:
                    page.wait_for_timeout(800)

                # Fallback: harvest URL’er (DOM / __NEXT_DATA__ / scripts)
                if not pdf_bytes:
                    harvested: List[str] = []

                    # lenker i DOM
                    try:
                        urls = page.evaluate(
                            "Array.from(document.querySelectorAll('a[href]')).map(a=>a.href)"
                        )
                        if isinstance(urls, list):
                            harvested.extend([u for u in urls if isinstance(u, str)])
                    except Exception:
                        pass

                    # __NEXT_DATA__ JSON
                    try:
                        txt = page.evaluate(
                            "(() => (document.getElementById('__NEXT_DATA__')||{}).textContent)()"
                        )
                    except Exception:
                        txt = None
                    if isinstance(txt, str) and txt:
                        harvested += re.findall(
                            r'https?://[^"\'\s]+?\.pdf(?:\?[^"\'\s]*)?', txt, re.I
                        )
                        harvested += re.findall(
                            r'https?://[^"\'\s]+?(wngetfile\.ashx|/getdocument|/getfile|/download)[^"\'\s]*',
                            txt,
                            re.I,
                        )

                    # scripts
                    try:
                        scripts = page.locator("script")
                        for i in range(min(scripts.count(), 60)):
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
                                r'https?://[^"\'\s]+?(wngetfile\.ashx|/getdocument|/getfile|/download)[^"\'\s]*',
                                content,
                                re.I,
                            )
                    except Exception:
                        pass

                    # uniq + filtrer + ranger (prospekt først)
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

                    def _score(u: str) -> tuple:
                        lo = u.lower()
                        return (
                            1 if POSITIVE_HINTS.search(lo) else 0,
                            0 if NEGATIVE_HINTS.search(lo) else 1,
                            lo.endswith(".pdf"),
                            -len(lo),
                        )

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

                # Nedlastings-event (siste mulighet)
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
