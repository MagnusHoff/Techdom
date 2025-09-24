from __future__ import annotations
import re
from typing import Tuple, Dict, Any, Optional
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

from ..config import SETTINGS
from ..browser_fetch import BROWSER_UA

PDF_MAGIC = b"%PDF-"


def _looks_like_pdf(b: bytes) -> bool:
    return isinstance(b, (bytes, bytearray)) and b.startswith(PDF_MAGIC)


# Streng allowlist for samle-PDF
ALLOW_URL_RX = re.compile(
    r"(digitalsalgsoppgave\.emvest\.no/[0-9a-f\-]{36}/2|/Vedlegg/Document|/Vedlegg/Dokument)",
    re.I,
)

# Det vi klikker på
COMBINED_LABELS = [
    "komplett salgsoppgave",
    "salgsoppgave",
    "utskriftsvennlig",
    "se pdf",
    "last ned pdf",
]


class SorMeglerenDriver:
    name = "sormegleren"

    def matches(self, url: str) -> bool:
        lo = (url or "").lower()
        return (
            "bolig.eiendomsmeglervest.no/" in lo
            or "digitalsalgsoppgave.emvest.no/" in lo
            or "sormegleren.no/" in lo
        )

    def try_fetch(
        self, sess: requests.Session, page_url: str
    ) -> Tuple[bytes | None, str | None, Dict[str, Any]]:
        dbg: Dict[str, Any] = {"driver": self.name, "step": "start"}

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    accept_downloads=True, user_agent=BROWSER_UA
                )
                page = context.new_page()

                # --- goto ---
                try:
                    page.goto(
                        page_url,
                        wait_until="domcontentloaded",
                        timeout=SETTINGS.REQ_TIMEOUT * 1000,
                    )
                except PWTimeoutError:
                    page.goto(page_url, timeout=SETTINGS.REQ_TIMEOUT * 1000)

                # --- cookie best-effort ---
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

                # --- klikk "Komplett salgsoppgave" (eller nært) ---
                clicked = False
                try:
                    els = page.locator("a[href], button, [role='button']")
                    n = min(200, els.count())
                    for i in range(n):
                        el = els.nth(i)
                        try:
                            raw = (el.inner_text(timeout=200) or "").strip().lower()
                        except Exception:
                            raw = ""
                        if not raw:
                            continue
                        if any(lbl in raw for lbl in COMBINED_LABELS):
                            try:
                                el.scroll_into_view_if_needed(timeout=500)
                            except Exception:
                                pass
                            try:
                                el.click(timeout=1200)
                            except Exception:
                                try:
                                    el.click(timeout=1200, force=True)
                                except Exception:
                                    continue
                            clicked = True
                            break
                except Exception:
                    pass

                # --- gi viewer/nedlasting litt tid ---
                page.wait_for_timeout(1400)

                # --- høst kandidat-URLer (DOM + __NEXT_DATA__ + scripts) ---
                harvested = []
                try:
                    dom_urls = (
                        page.evaluate(
                            "(()=>Array.from(document.querySelectorAll('a[href]')).map(a=>a.href))()"
                        )
                        or []
                    )
                    if isinstance(dom_urls, list):
                        harvested.extend([u for u in dom_urls if isinstance(u, str)])
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
                        harvested.append(m.group(0).replace("\\/", "/"))

                try:
                    scripts = page.locator("script")
                    sN = min(60, scripts.count())
                    for i in range(sN):
                        try:
                            sc = scripts.nth(i).inner_text(timeout=200) or ""
                        except Exception:
                            continue
                        for m in re.finditer(r"https?://[^\s\"']+", sc):
                            harvested.append(m.group(0))
                except Exception:
                    pass

                # de-dupe + filtér med allowlist
                seen = set()
                cand = []
                for u in harvested:
                    if not isinstance(u, str) or u in seen:
                        continue
                    seen.add(u)
                    if ALLOW_URL_RX.search(u):
                        cand.append(u)

                # --- prøv kandidat-URLer direkte via context.request.get ---
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
                        if r.ok and _looks_like_pdf(r.body()):
                            pdf_bytes, pdf_url = r.body(), u
                            break
                    except Exception:
                        continue

                # --- nedlastings-event som siste sjanse ---
                if not pdf_bytes:
                    try:
                        dl = page.wait_for_event("download", timeout=3000)
                        if dl:
                            u = dl.url or ""
                            if ALLOW_URL_RX.search(u or ""):
                                r = context.request.get(
                                    u,
                                    headers={
                                        "Accept": "application/pdf,application/octet-stream,*/*"
                                    },
                                    timeout=SETTINGS.REQ_TIMEOUT * 1000,
                                )
                                if r.ok and _looks_like_pdf(r.body()):
                                    pdf_bytes, pdf_url = r.body(), u
                    except Exception:
                        pass

                context.close()
                browser.close()

                if not (pdf_bytes and pdf_url):
                    dbg["step"] = "no_pdf_found"
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
