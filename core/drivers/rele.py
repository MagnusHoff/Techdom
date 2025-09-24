from __future__ import annotations
import re, io
from typing import Tuple, Dict, Any, Optional, List
import requests
from PyPDF2 import PdfReader
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

from ..sessions import new_session
from ..config import SETTINGS
from ..browser_fetch import BROWSER_UA, _response_looks_like_pdf

PDF_MAGIC = b"%PDF-"
PDF_RX = re.compile(r"\.pdf(?:[\?#][^\s\"']*)?$", re.I)

# Rele/Vitec leverer som regel via dette proxyet (uten .pdf i URL)
PDF_URL_HINTS = re.compile(
    r"(/proxy/vitec/|/document/|/download|wngetfile\.ashx)",
    re.I,
)

# Blokker uønskede dokumenter
BLOCKLIST_RX = re.compile(
    r"(nabolag|nabolagsprofil|contentassets/nabolaget|energiattest|egenerkl|salgsoppgave)",
    re.I,
)

# TR-hint i URL (kan ofte mangle – da sjekker vi innhold)
TR_URL_RX = re.compile(
    r"(tilstandsrapport|boligsalgsrapport|fidens|estates|ns\s*3600|vitec)", re.I
)

# Tekster vi klikker på i UI
CLICK_TEXTS = [
    "tilstandsrapport",
    "boligsalgsrapport",
    "takst",
    "se tilstandsrapport",
]


def _looks_like_pdf(b: bytes) -> bool:
    return isinstance(b, (bytes, bytearray)) and b.startswith(PDF_MAGIC)


def _min_pages(b: bytes, n: int = 2) -> bool:
    try:
        r = PdfReader(io.BytesIO(b))
        return len(r.pages) >= n
    except Exception:
        return False


def _first_pages_have_tr(b: bytes, first: int = 3) -> bool:
    try:
        r = PdfReader(io.BytesIO(b))
        txt = []
        for p in r.pages[:first]:
            try:
                t = (p.extract_text() or "").lower()
            except Exception:
                t = ""
            if t:
                txt.append(t)
        blob = "\n".join(txt)
        return ("tilstandsrapport" in blob) or ("boligsalgsrapport" in blob)
    except Exception:
        return False


def _url_allowed(u: str) -> bool:
    if not u:
        return False
    return not BLOCKLIST_RX.search(u.lower())


def _looks_like_pdf_url(u: str, ctype: str = "") -> bool:
    lo = (u or "").lower()
    return (
        "application/pdf" in (ctype or "").lower()
        or PDF_RX.search(lo) is not None
        or PDF_URL_HINTS.search(lo) is not None
    )


class ReleDriver:
    name = "rele"

    def matches(self, url: str) -> bool:
        lo = (url or "").lower()
        return "ds.meglerhuset-rele.no/" in lo or "meglerhuset-rele" in lo

    def try_fetch(
        self, sess: requests.Session, page_url: str
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

                # --- Sniff alle responses (proxy/vitec/document) ---
                def handle_response(resp):
                    nonlocal pdf_bytes, pdf_url
                    if pdf_bytes is not None:
                        return
                    try:
                        url = resp.url or ""
                        ctype = (resp.headers or {}).get("content-type", "").lower()
                    except Exception:
                        url, ctype = "", ""

                    if not url or not _url_allowed(url):
                        return
                    if not _looks_like_pdf_url(url, ctype):
                        return

                    if _response_looks_like_pdf(resp):
                        try:
                            body = resp.body()
                        except Exception:
                            body = None
                        if body and _looks_like_pdf(body):
                            pdf_bytes, pdf_url = body, url
                            dbg["response_hit"] = url

                page.on("response", handle_response)

                # --- Gå til siden (approved/.../latest) ---
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

                # Scroll litt (lazy innhold)
                try:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight/3)")
                    page.wait_for_timeout(400)
                except Exception:
                    pass

                # Klikk på "Tilstandsrapport (PDF)"-knappen
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
                                    "text_preview": (
                                        raw[:90] + ("…" if len(raw) > 90 else "")
                                    ),
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
                        if href and _url_allowed(href):
                            try:
                                rr = page.context.request.get(
                                    href,
                                    headers={
                                        "Accept": "application/pdf,application/octet-stream,*/*"
                                    },
                                    timeout=SETTINGS.REQ_TIMEOUT * 1000,
                                )
                                if rr.ok and _looks_like_pdf(rr.body()):
                                    pdf_bytes, pdf_url = rr.body(), href
                                    dbg["click_direct_href"] = href
                                    break
                            except Exception:
                                pass

                        # 2) Klikk for å trigge proxy/vitec download
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

                # Vent på eventuelle XHR
                try:
                    page.wait_for_load_state("networkidle", timeout=3000)
                except Exception:
                    page.wait_for_timeout(800)

                # Fallback: høst URL’er fra DOM/__NEXT_DATA__/scripts
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
                        for m in re.finditer(
                            r'https?://[^"\'\s]+?\.pdf(?:\?[^"\'\s]*)?', txt, re.I
                        ):
                            harvested.append(m.group(0))
                        for m in re.finditer(
                            r'https?://[^"\'\s]+?(/proxy/vitec/|/document/|/download|wngetfile\.ashx)[^"\'\s]*',
                            txt,
                            re.I,
                        ):
                            harvested.append(m.group(0))

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
                                r'https?://[^"\'\s]+?(/proxy/vitec/|/document/|/download|wngetfile\.ashx)[^"\'\s]*',
                                content,
                                re.I,
                            ):
                                harvested.append(m.group(0))
                    except Exception:
                        pass

                    seen = set()
                    uniq = []
                    for u in harvested:
                        if isinstance(u, str) and u not in seen and _url_allowed(u):
                            seen.add(u)
                            uniq.append(u)

                    # Score: prioriter vitec-proxy + TR-hint
                    def _score(u: str) -> int:
                        lo = u.lower()
                        sc = 0
                        if "/proxy/vitec/" in lo:
                            sc += 220
                        if "/document/" in lo:
                            sc += 120
                        if TR_URL_RX.search(lo):
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
                            if rr.ok and _looks_like_pdf(rr.body()):
                                pdf_bytes, pdf_url = rr.body(), u
                                dbg["harvest_hit"] = u
                                break
                        except Exception:
                            continue

                # Nedlastings-event (tom side + tillat nedlasting)
                if not pdf_bytes:
                    try:
                        dl = page.wait_for_event("download", timeout=2500)
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

                # Valider minst 2 sider
                if not _min_pages(pdf_bytes, 2):
                    dbg["step"] = "pdf_rejected_min_pages"
                    return None, None, dbg

                # Hvis URL ikke har klare TR-ord → innholdssjekk
                tr_ok = True
                if not TR_URL_RX.search(pdf_url or ""):
                    tr_ok = _first_pages_have_tr(pdf_bytes)
                dbg["tr_text_found"] = tr_ok
                if not tr_ok:
                    dbg["step"] = "pdf_rejected_not_tr"
                    return None, None, dbg

                # Markér at dette faktisk ER tilstandsrapporten slik at fetch.py skipper klipping
                reason = None
                # a) vi klikket på en knapp/lenke som inneholdt "tilstandsrapport"
                if any(
                    "tilstandsrapport" in (a.get("text_preview", "").lower())
                    for a in (dbg.get("click_attempts") or [])
                ):
                    reason = "clicked_label"
                # b) URL har tydelige TR-hint
                elif TR_URL_RX.search(pdf_url or ""):
                    reason = "url_hint"
                # c) innholdssjekk bekreftet TR
                elif tr_ok:
                    reason = "content_match"

                if reason:
                    dbg.setdefault("meta", {})
                    dbg["meta"]["is_tilstandsrapport"] = True
                    dbg["meta"]["reason"] = reason

                dbg["step"] = "ok"
                return pdf_bytes, pdf_url, dbg

        except Exception as e:
            dbg["step"] = "exception"
            dbg["error"] = f"{type(e).__name__}: {e}"
            return None, None, dbg
