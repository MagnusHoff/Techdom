# core/drivers/notar.py
from __future__ import annotations

import io
import re
from typing import Tuple, Dict, Any, List, Optional

from PyPDF2 import PdfReader
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

from .base import Driver
from techdom.infrastructure.config import SETTINGS
from techdom.ingestion.browser_fetch import BROWSER_UA, _response_looks_like_pdf
from .common import looks_like_pdf_bytes

# Godartede (prospekt) signaler vi ser etter i URL/label
POSITIVE_HINTS = re.compile(
    r"(salgsoppgav|prospekt|utskriftsvennlig|komplett|digital[_\-]?salgsoppgave|se\s+pdf|last\s+ned\s+pdf)",
    re.I,
)

# Alt dette skal vi IKKE plukke opp
NEGATIVE_HINTS = re.compile(
    r"(tilstandsrapport|boligsalgsrapport|takst|fidens|estates|energiattest|nabolag|"
    r"nabolagsprofil|contentassets/nabolaget|egenerkl|budskjema|kjøpekontrakt|vilkår|terms|cookies)",
    re.I,
)

# Nettverks-URL-mønstre Notar ofte bruker for dokumenter (inkl. webmegler-proxy),
# men vi filtrerer i tillegg på POSITIVE/NEGATIVE over.
PDF_URL_HINTS = re.compile(
    r"(wngetfile\.ashx|/getdocument|/getfile|/download|/proxy/webmegler/.+/wngetfile\.ashx|\.pdf(?:[\?#][^\s\"']*)?$)",
    re.I,
)

# Tekster vi klikker på i UI
CLICK_TEXTS = [
    "salgsoppgave",
    "prospekt",
    "utskriftsvennlig",
    "komplett salgsoppgave",
    "se pdf",
    "last ned pdf",
]

MIN_PAGES = 6  # rene prospekter er normalt > ~6 sider
MIN_BYTES = 250_000  # vær konservativ men unngå bittesmå kvitteringer


def _pdf_pages(b: bytes | None) -> int:
    if not b:
        return 0
    try:
        return len(PdfReader(io.BytesIO(b)).pages)
    except Exception:
        return 0


def _pdf_text_first_pages(b: bytes, first: int = 3) -> str:
    try:
        rdr = PdfReader(io.BytesIO(b))
        txt: List[str] = []
        for p in rdr.pages[: min(first, len(rdr.pages))]:
            try:
                t = p.extract_text() or ""
            except Exception:
                t = ""
            if t:
                txt.append(t.lower())
        return "\n".join(txt)
    except Exception:
        return ""


def _is_prospect_pdf(b: bytes | None, url: Optional[str]) -> bool:
    """
    Kun salgsoppgave/prospekt: må se 'pdf'-magick, nok sider/størrelse,
    og verken URL eller innhold skal inneholde TR/negative hint.
    """
    if not looks_like_pdf_bytes(b):
        return False
    if not b or len(b) < MIN_BYTES or _pdf_pages(b) < MIN_PAGES:
        return False

    lo_url = (url or "").lower()
    if NEGATIVE_HINTS.search(lo_url):
        return False

    first_txt = _pdf_text_first_pages(b, first=3)
    if first_txt and NEGATIVE_HINTS.search(first_txt):
        return False

    # positivt signal hjelper men er ikke krav (noen prospekter har ikke ordene i URL)
    return True


def _url_is_candidate(u: str) -> bool:
    if not u:
        return False
    lo = u.lower()
    if NEGATIVE_HINTS.search(lo):
        return False
    return bool(PDF_URL_HINTS.search(lo) or POSITIVE_HINTS.search(lo))


class NotarDriver(Driver):
    name = "notar"

    def matches(self, url: str) -> bool:
        return "notar.no/bolig-til-salgs/" in (url or "").lower()

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

                pdf_bytes: bytes | None = None
                pdf_url: str | None = None

                # --- Sniff nettverksresponser for mulige prospekter ---
                def handle_response(resp):
                    nonlocal pdf_bytes, pdf_url
                    if pdf_bytes is not None:
                        return
                    try:
                        url = resp.url or ""
                        ct = (resp.headers or {}).get("content-type", "").lower()
                    except Exception:
                        url, ct = "", ""

                    if not _url_is_candidate(url):
                        return

                    looks_pdfish = (
                        "application/pdf" in ct
                        or _response_looks_like_pdf(resp)
                        or PDF_URL_HINTS.search(url)
                    )
                    if not looks_pdfish:
                        return

                    try:
                        body = resp.body()
                    except Exception:
                        body = None

                    if body and _is_prospect_pdf(body, url):
                        pdf_bytes, pdf_url = body, url
                        dbg["response_hit"] = url

                page.on("response", handle_response)

                # --- Last objektsiden ---
                try:
                    page.goto(
                        page_url,
                        wait_until="domcontentloaded",
                        timeout=SETTINGS.REQ_TIMEOUT * 1000,
                    )
                except PWTimeoutError:
                    page.goto(page_url, timeout=SETTINGS.REQ_TIMEOUT * 1000)

                # Cookie-accept (best effort)
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
                        pass

                # Litt scroll for lazy sections
                try:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight/2)")
                    page.wait_for_timeout(350)
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(350)
                except Exception:
                    pass

                # Åpne ev. «Dokumenter»-seksjon
                try:
                    btns = page.locator("button, [role='button'], a")
                    for i in range(min(btns.count(), 200)):
                        b = btns.nth(i)
                        try:
                            t = (b.inner_text(timeout=250) or "").strip().lower()
                        except Exception:
                            t = ""
                        if not t:
                            continue
                        if "dokument" in t and any(
                            x in t for x in ("se", "vis", "åpne")
                        ):
                            try:
                                b.click(timeout=1500)
                                dbg["opened_documents"] = True
                                break
                            except Exception:
                                continue
                except Exception:
                    pass

                # --- Klikk bare på ting som ser ut som salgsoppgave/prospekt ---
                attempts: List[Dict[str, Any]] = []
                try:
                    candidates = page.locator("a, button, [role='button']")
                    for i in range(min(candidates.count(), 250)):
                        el = candidates.nth(i)
                        try:
                            raw = el.inner_text(timeout=250) or ""
                        except Exception:
                            raw = ""
                        txt = raw.strip().lower()
                        matched = any(
                            h in txt for h in CLICK_TEXTS
                        ) and not NEGATIVE_HINTS.search(txt)
                        if len(attempts) < 120:
                            attempts.append(
                                {
                                    "index": i,
                                    "text_preview": raw[:90]
                                    + ("…" if len(raw) > 90 else ""),
                                    "match": matched,
                                }
                            )
                        if not matched:
                            continue

                        # Direkte href?
                        href = ""
                        try:
                            href = el.get_attribute("href") or ""
                        except Exception:
                            pass

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

                        # Klikk for å trigge evt. viewer/download
                        try:
                            el.scroll_into_view_if_needed(timeout=600)
                        except Exception:
                            pass
                        try:
                            el.click(timeout=1800)
                            dbg["click_hit"] = {"index": i, "text": raw[:200]}
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

                # --- Vent litt for sene XHR ---
                try:
                    page.wait_for_load_state("networkidle", timeout=3000)
                except Exception:
                    page.wait_for_timeout(800)

                # --- Fallback: harvest fra DOM / __NEXT_DATA__ / scripts ---
                if not pdf_bytes:
                    harvested: List[str] = []

                    # a[href]
                    try:
                        urls = page.evaluate(
                            "Array.from(document.querySelectorAll('a[href]')).map(a=>a.href)"
                        )
                        if isinstance(urls, list):
                            harvested.extend([u for u in urls if isinstance(u, str)])
                    except Exception:
                        pass

                    # __NEXT_DATA__
                    try:
                        txt = page.evaluate(
                            "(() => (document.getElementById('__NEXT_DATA__')||{}).textContent)()"
                        )
                    except Exception:
                        txt = None
                    if isinstance(txt, str) and txt:
                        harvested += re.findall(
                            r'https?://[^"\'\s]+?\.pdf(?:\?[^"\'\s]*)?', txt, flags=re.I
                        )
                        harvested += re.findall(
                            r'https?://[^"\'\s]+?(wngetfile\.ashx|/getdocument|/getfile|/download)[^"\'\s]*',
                            txt,
                            flags=re.I,
                        )

                    # <script>
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
                                flags=re.I,
                            )
                            harvested += re.findall(
                                r'https?://[^"\'\s]+?(wngetfile\.ashx|/getdocument|/getfile|/download)[^"\'\s]*',
                                content,
                                flags=re.I,
                            )
                    except Exception:
                        pass

                    # uniq + filtrer + ranger (prospekt-vennlige først)
                    seen = set()
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

                    for u in uniq[:20]:
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

                # Nedlasting-event (siste sjanse)
                if not pdf_bytes:
                    try:
                        dl = page.wait_for_event("download", timeout=2000)
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
