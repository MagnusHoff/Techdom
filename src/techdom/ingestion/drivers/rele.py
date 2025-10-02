# core/drivers/rele.py
from __future__ import annotations
import re, io
from typing import Tuple, Dict, Any, Optional, List

from PyPDF2 import PdfReader
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

from .base import Driver
from techdom.infrastructure.config import SETTINGS
from ..browser_fetch import BROWSER_UA, _response_looks_like_pdf
from .common import looks_like_pdf_bytes

PDF_RX = re.compile(r"\.pdf(?:[\?#][^\s\"']*)?$", re.I)

# Rele/Vitec leverer ofte via proxy-endepunkter (ofte uten .pdf i URL)
PDF_URL_HINTS = re.compile(
    r"(/proxy/vitec/|/document/|/download|wngetfile\.ashx)", re.I
)

# Vi vil KUN ha prospekt/salgsoppgave
POSITIVE_RX = re.compile(
    r"(prospekt|salgsoppgav|digital[_\- ]salgsoppgave|utskriftsvennlig|komplett)",
    re.I,
)

# Alt dette skal IKKE hentes
NEGATIVE_RX = re.compile(
    r"(tilstandsrapport|boligsalgsrapport|ns[\s_\-]?3600|bygningssakkyndig|tilstandsgrader|"
    r"energiattest|energimerke|nabolag|nabolagsprofil|egenerkl|budskjema|vilkår|terms|cookies)",
    re.I,
)

# Innholdscues som avslører TR (brukes for å avvise feil PDF)
TR_CONTENT_RX = re.compile(
    r"(tilstandsrapport|boligsalgsrapport|ns[\s_\-]?3600|bygningssakkyndig|tilstandsgrader|nøkkeltakst)",
    re.I,
)

# Tekster vi klikker på i UI for å få prospekt
CLICK_TEXTS = [
    "prospekt",
    "salgsoppgave",
    "se salgsoppgave",
    "last ned prospekt",
    "last ned salgsoppgave",
    "digital salgsoppgave",
    "utskriftsvennlig",
    "komplett",
]

MIN_PAGES = 6
MIN_BYTES = 200_000  # moderat terskel for ekte prospekt


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


def _is_prospect_pdf(b: bytes, url: str | None = None) -> bool:
    if not looks_like_pdf_bytes(b):
        return False
    if len(b) < MIN_BYTES:
        return False
    if _pdf_pages(b) < MIN_PAGES:
        return False
    # URL må ikke ha tydelige negative signaler
    if url and NEGATIVE_RX.search(url):
        return False
    # Innholdet skal IKKE se ut som TR
    txt = _first_pages_text(b, 3)
    if TR_CONTENT_RX.search(txt):
        return False
    return True


def _allowed_url(u: str, label: str = "") -> bool:
    s = f"{label} {u}".lower()
    if NEGATIVE_RX.search(s):
        return False
    # Krev positive signaler i label/URL for å begrense oss til prospekt
    return POSITIVE_RX.search(s) is not None


def _looks_like_pdf_url(u: str, ctype: str = "") -> bool:
    lo = (u or "").lower()
    return (
        "application/pdf" in (ctype or "").lower()
        or PDF_RX.search(lo) is not None
        or PDF_URL_HINTS.search(lo) is not None
    )


class ReleDriver(Driver):
    name = "rele"

    def matches(self, url: str) -> bool:
        lo = (url or "").lower()
        return "ds.meglerhuset-rele.no/" in lo or "meglerhuset-rele" in lo

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

                # --- Sniff responses og fang potensielle prospekt-PDF-er ---
                def handle_response(resp):
                    nonlocal pdf_bytes, pdf_url
                    if pdf_bytes is not None:
                        return
                    try:
                        url = resp.url or ""
                        ctype = (resp.headers or {}).get("content-type", "").lower()
                    except Exception:
                        url, ctype = "", ""

                    # må se ut som PDF-respons, og ikke åpenbart "feil" dokumenttype
                    if not url or not _looks_like_pdf_url(url, ctype):
                        return
                    # tillat bare hvis URL/label har positive hint eller er nøytral – vi verifiserer innhold etterpå
                    if NEGATIVE_RX.search(url):
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

                # --- Last siden ---
                try:
                    page.goto(
                        page_url,
                        wait_until="domcontentloaded",
                        timeout=SETTINGS.REQ_TIMEOUT * 1000,
                    )
                except PWTimeoutError:
                    page.goto(page_url, timeout=SETTINGS.REQ_TIMEOUT * 1000)

                # Godta cookies (best-effort)
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

                # Litt scrolling for lazy content
                try:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight/3)")
                    page.wait_for_timeout(400)
                except Exception:
                    pass

                # Klikk på prospekt/salgsoppgave-lenker/knapper
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
                        hit = any(k in low for k in CLICK_TEXTS)
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

                        # Direkte via href
                        href = ""
                        try:
                            href = el.get_attribute("href") or ""
                        except Exception:
                            href = ""
                        if href and _allowed_url(href, raw):
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

                        # Klikk for å trigge proxy/vitec/wngetfile
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

                # Vent for sene XHR
                try:
                    page.wait_for_load_state("networkidle", timeout=3000)
                except Exception:
                    page.wait_for_timeout(800)

                # Fallback: harvest URL-er fra DOM/__NEXT_DATA__/scripts
                if not pdf_bytes:
                    harvested: List[str] = []

                    # DOM lenker
                    try:
                        urls = page.evaluate(
                            "Array.from(document.querySelectorAll('a[href]')).map(a=>({href:a.href,text:a.innerText||''}))"
                        )
                        if isinstance(urls, list):
                            for it in urls:
                                if not isinstance(it, dict):
                                    continue
                                href = it.get("href") or ""
                                txt = it.get("text") or ""
                                if href and _allowed_url(href, txt):
                                    harvested.append(href)
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
                        for m in re.finditer(
                            r'https?://[^"\'\s]+?(?:\.pdf(?:\?[^"\'\s]*)?|/proxy/vitec/|/document/|/download|wngetfile\.ashx)[^"\'\s]*',
                            txt,
                            re.I,
                        ):
                            u = m.group(0)
                            if _allowed_url(u):
                                harvested.append(u)

                    # <script> innhold
                    try:
                        scripts = page.locator("script")
                        n = scripts.count()
                        for i in range(min(n, 60)):
                            try:
                                content = scripts.nth(i).inner_text(timeout=200) or ""
                            except Exception:
                                continue
                            for m in re.finditer(
                                r'https?://[^"\'\s]+?(?:\.pdf(?:\?[^"\'\s]*)?|/proxy/vitec/|/document/|/download|wngetfile\.ashx)[^"\'\s]*',
                                content,
                                re.I,
                            ):
                                u = m.group(0)
                                if _allowed_url(u):
                                    harvested.append(u)
                    except Exception:
                        pass

                    # uniq + prioritér prospekt-signaler og vitec-proxy
                    seen = set()
                    uniq: List[str] = []
                    for u in harvested:
                        if isinstance(u, str) and u not in seen:
                            seen.add(u)
                            uniq.append(u)

                    def _score(u: str) -> int:
                        lo = u.lower()
                        sc = 0
                        if "/proxy/vitec/" in lo:
                            sc += 200
                        if "/document/" in lo or "wngetfile.ashx" in lo:
                            sc += 120
                        if POSITIVE_RX.search(lo):
                            sc += 80
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

                # Nedlastings-event som siste utvei
                if not pdf_bytes:
                    try:
                        dl = page.wait_for_event("download", timeout=2500)
                        if dl:
                            u = dl.url or ""
                            if _allowed_url(u):
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

                # Endelig validering (belt & braces)
                if not _is_prospect_pdf(pdf_bytes, pdf_url):
                    dbg["step"] = "pdf_rejected_not_prospect"
                    return None, None, dbg

                dbg["step"] = "ok_prospect"
                return pdf_bytes, pdf_url, dbg

        except Exception as e:
            dbg["step"] = "exception"
            dbg["error"] = f"{type(e).__name__}: {e}"
            return None, None, dbg
