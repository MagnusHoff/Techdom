from __future__ import annotations
import re, io
from typing import Tuple, Dict, Any, Optional, List
import requests
from PyPDF2 import PdfReader
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

from ..config import SETTINGS
from ..browser_fetch import BROWSER_UA, _response_looks_like_pdf

PDF_MAGIC = b"%PDF-"
PDF_RX = re.compile(r"\.pdf(?:[\?#][^\s\"']*)?$", re.I)

# Sem & Johnsen bruker Sanity CDN til «Utskriftsvennlig salgsoppgave»
SANITY_PDF_RX = re.compile(
    r"https?://cdn\.sanity\.io/files/[^\s\"']+?\.pdf(?:\?[^\"']*)?", re.I
)

# Tekster vi klikker på
CLICK_TEXTS = [
    "utskriftsvennlig salgsoppgave",
    "komplett salgsoppgave",
    "salgsoppgave",
    "se pdf",
    "last ned pdf",
    "for utskrift",
]


def _looks_like_pdf(b: bytes) -> bool:
    return isinstance(b, (bytes, bytearray)) and b.startswith(PDF_MAGIC)


def _min_pages(b: bytes, n: int = 4) -> bool:
    try:
        r = PdfReader(io.BytesIO(b))
        return len(r.pages) >= n
    except Exception:
        return False


class SemJohnsenDriver:
    name = "semjohnsen"

    def matches(self, url: str) -> bool:
        return "sem-johnsen.no/boliger/" in (url or "").lower()

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

                # Sniff PDF-responser (særlig Sanity-URL)
                def handle_response(resp):
                    nonlocal pdf_bytes, pdf_url
                    if pdf_bytes is not None:
                        return
                    try:
                        url = resp.url or ""
                        ctype = (resp.headers or {}).get("content-type", "").lower()
                    except Exception:
                        url, ctype = "", ""
                    if not url:
                        return
                    if (
                        "application/pdf" in ctype
                        or PDF_RX.search(url)
                        or SANITY_PDF_RX.search(url)
                        or _response_looks_like_pdf(resp)
                    ):
                        try:
                            body = resp.body()
                        except Exception:
                            body = None
                        if body and _looks_like_pdf(body):
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

                # Klikk på «Utskriftsvennlig/Komplett salgsoppgave»
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

                        # Direkte via href (Sanity-lenke)
                        href = ""
                        try:
                            href = el.get_attribute("href") or ""
                        except Exception:
                            href = ""
                        if href:
                            # forsøk direkte binærhent
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

                # Harvest som ekstra sikkerhet (DOM + scripts + __NEXT_DATA__)
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
                                harvested.append(m.group(0))
                    except Exception:
                        pass

                    # uniq
                    seen = set()
                    uniq = []
                    for u in harvested:
                        if isinstance(u, str) and u not in seen:
                            seen.add(u)
                            uniq.append(u)

                    # Prioriter Sanity-URLer
                    def _score(u: str) -> int:
                        return (200 if "cdn.sanity.io/files/" in (u.lower()) else 0) + (
                            20 if u.lower().endswith(".pdf") else 0
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
                            if rr.ok and _looks_like_pdf(rr.body()):
                                pdf_bytes, pdf_url = rr.body(), u
                                dbg["harvest_hit"] = u
                                break
                        except Exception:
                            continue

                context.close()
                browser.close()

                if not pdf_bytes or not pdf_url:
                    dbg["step"] = "no_pdf_found"
                    return None, None, dbg

                # Samle-PDF sanity check: minst 4 sider
                if not _min_pages(pdf_bytes, 4):
                    dbg["step"] = "pdf_rejected_min_pages"
                    return None, None, dbg

                # Viktig: IKKE sett meta.is_tilstandsrapport her (dette er samle-PDF).
                # Hint til postprosess: si fra at dette er "prospekt/combined"
                dbg.setdefault("meta", {})
                dbg["meta"]["combined_prospectus"] = True
                dbg["meta"]["source"] = "sanity_cdn"

                dbg["step"] = "ok"
                return pdf_bytes, pdf_url, dbg

        except Exception as e:
            dbg["step"] = "exception"
            dbg["error"] = f"{type(e).__name__}: {e}"
            return None, None, dbg
