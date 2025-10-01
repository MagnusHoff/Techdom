# core/drivers/semjohnsen.py
from __future__ import annotations
import re, io
from typing import Tuple, Dict, Any, Optional, List

from PyPDF2 import PdfReader
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

from .base import Driver
from techdom.infrastructure.config import SETTINGS
from ..browser_fetch import BROWSER_UA, _response_looks_like_pdf

PDF_MAGIC = b"%PDF-"
PDF_RX = re.compile(r"\.pdf(?:[\?#][^\s\"']*)?$", re.I)

# Sem & Johnsen bruker Sanity CDN for «Utskriftsvennlig/Komplett salgsoppgave»
SANITY_PDF_RX = re.compile(
    r"https?://cdn\.sanity\.io/files/[^\s\"']+?\.pdf(?:\?[^\"']*)?", re.I
)

# Vi vil KUN ha prospekt/salgsoppgave
POSITIVE_RX = re.compile(
    r"(salgsoppgav|prospekt|utskriftsvennlig|komplett|for\s*utskrift|se\s*pdf|last\s*ned\s*pdf)",
    re.I,
)

# Dette skal IKKE hentes
NEGATIVE_RX = re.compile(
    r"(tilstandsrapport|boligsalgsrapport|ns[\s_\-]?3600|bygningssakkyndig|tilstandsgrader|"
    r"energiattest|energimerke|nabolag|nabolagsprofil|egenerkl|budskjema|vilkår|terms|cookies)",
    re.I,
)

# Innholdscues for TR (for å avvise feil PDF selv om URL ser OK ut)
TR_CONTENT_RX = re.compile(
    r"(tilstandsrapport|boligsalgsrapport|ns[\s_\-]?3600|bygningssakkyndig|tilstandsgrader|nøkkeltakst)",
    re.I,
)

# Tekster vi klikker på for å få prospekt
CLICK_TEXTS = [
    "utskriftsvennlig salgsoppgave",
    "komplett salgsoppgave",
    "salgsoppgave",
    "se pdf",
    "last ned pdf",
    "for utskrift",
]

# Moderat, men realistisk for samle-PDF
MIN_PAGES = 6
MIN_BYTES = 200_000


def _looks_like_pdf(b: Optional[bytes]) -> bool:
    return isinstance(b, (bytes, bytearray)) and b.startswith(PDF_MAGIC)


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
    if not _looks_like_pdf(b):
        return False
    if len(b) < MIN_BYTES:
        return False
    if _pdf_pages(b) < MIN_PAGES:
        return False
    # Avvis åpenbare negative signaler i URL
    if url and NEGATIVE_RX.search(url):
        return False
    # Innholdet skal ikke se ut som TR
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
        or SANITY_PDF_RX.search(lo) is not None
    )


class SemJohnsenDriver(Driver):
    name = "semjohnsen"

    def matches(self, url: str) -> bool:
        return "sem-johnsen.no/boliger/" in (url or "").lower()

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

                # Sniff PDF-responser (Sanity og generelle PDF-ruter) – valider som prospekt
                def handle_response(resp):
                    nonlocal pdf_bytes, pdf_url
                    if pdf_bytes is not None:
                        return
                    try:
                        url = resp.url or ""
                        ctype = (resp.headers or {}).get("content-type", "").lower()
                    except Exception:
                        url, ctype = "", ""
                    if not url or not _looks_like_pdf_url(url, ctype):
                        return
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

                # Gå til objektet
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

                # Scroll litt for å laste inn seksjoner
                try:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight/2)")
                    page.wait_for_timeout(400)
                except Exception:
                    pass

                # Klikk på «Utskriftsvennlig/Komplett salgsoppgave» (kun positive)
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
                        label = raw.strip()
                        low = label.lower()
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

                        # Direkte via href (Sanity/annen PDF)
                        href = ""
                        try:
                            href = el.get_attribute("href") or ""
                        except Exception:
                            href = ""
                        if href and _allowed_url(href, label):
                            try:
                                rr = page.context.request.get(
                                    href,
                                    headers={
                                        "Accept": "application/pdf,application/octet-stream,*/*"
                                    },
                                    timeout=SETTINGS.REQ_TIMEOUT * 1000,
                                )
                                body = rr.body() if rr.ok else None
                                if body and _is_prospect_pdf(body, href):
                                    pdf_bytes, pdf_url = body, href
                                    dbg["click_direct_href"] = href
                                    break
                            except Exception:
                                pass

                        # Ellers klikk og la sniff fange Sanity-URLen
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

                # Kort vent for XHR
                try:
                    page.wait_for_load_state("networkidle", timeout=3000)
                except Exception:
                    page.wait_for_timeout(800)

                # Harvest som ekstra sikkerhet (DOM + scripts)
                if not pdf_bytes:
                    harvested: List[str] = []
                    try:
                        urls = page.evaluate(
                            "Array.from(document.querySelectorAll('a[href]')).map(a=>({href:a.href,text:a.innerText||''}))"
                        )
                        if isinstance(urls, list):
                            for it in urls:
                                if not isinstance(it, dict):
                                    continue
                                href = it.get("href") or ""
                                text = it.get("text") or ""
                                if href and _allowed_url(href, text):
                                    harvested.append(href)
                    except Exception:
                        pass
                    try:
                        scripts = page.locator("script")
                        n = scripts.count()
                        for i in range(min(n, 60)):
                            try:
                                content = scripts.nth(i).inner_text(timeout=200) or ""
                            except Exception:
                                continue
                            for m in SANITY_PDF_RX.finditer(content):
                                harvested.append(m.group(0))
                            for m in re.finditer(
                                r'https?://[^"\'\s]+?\.pdf(?:\?[^"\'\s]*)?',
                                content,
                                re.I,
                            ):
                                u = m.group(0)
                                if _allowed_url(u):
                                    harvested.append(u)
                    except Exception:
                        pass

                    # uniq
                    seen = set()
                    uniq = []
                    for u in harvested:
                        if isinstance(u, str) and u not in seen:
                            seen.add(u)
                            uniq.append(u)

                    # Prioriter Sanity-URLer + positive signaler
                    def _score(u: str) -> int:
                        lo = u.lower()
                        sc = 0
                        if "cdn.sanity.io/files/" in lo:
                            sc += 200
                        if POSITIVE_RX.search(lo):
                            sc += 60
                        if lo.endswith(".pdf"):
                            sc += 20
                        return sc

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
                            body = rr.body() if rr.ok else None
                            if body and _is_prospect_pdf(body, u):
                                pdf_bytes, pdf_url = body, u
                                dbg["harvest_hit"] = u
                                break
                        except Exception:
                            continue

                context.close()
                browser.close()

                if not pdf_bytes or not pdf_url:
                    dbg["step"] = "no_pdf_found"
                    return None, None, dbg

                # Endelig prospekt-validering
                if not _is_prospect_pdf(pdf_bytes, pdf_url):
                    dbg["step"] = "pdf_rejected_not_prospect"
                    return None, None, dbg

                # Hint til videre pipeline: dette er samle/prospekt fra Sanity
                dbg.setdefault("meta", {})
                dbg["meta"]["combined_prospectus"] = True
                dbg["meta"]["source"] = "sanity_cdn"

                dbg["step"] = "ok"
                return pdf_bytes, pdf_url, dbg

        except Exception as e:
            dbg["step"] = "exception"
            dbg["error"] = f"{type(e).__name__}: {e}"
            return None, None, dbg
