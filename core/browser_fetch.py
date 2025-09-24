# core/browser_fetch.py
from __future__ import annotations
import re
import unicodedata
from typing import Optional, Tuple, Dict, Any, List
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError
from typing import Iterable

# -- Browser fetch heuristics / blacklist --
PM_BAD_PDFS = {
    "https://privatmegleren.no/docs/klikk.pdf",
    "http://privatmegleren.no/docs/klikk.pdf",
}


def _is_blacklisted_pdf(url: str) -> bool:
    try:
        u = (url or "").split("#")[0]
        return u in PM_BAD_PDFS or u.lower().endswith("/docs/klikk.pdf")
    except Exception:
        return False


# Konsistent UA
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/127.0.0.0 Safari/537.36"
)


# === TEKSTNORMALISERING ===
def _norm_text(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = " ".join(s.split())
    return s.strip().lower()


# === PRIORITERINGSORDLISTER ===

# Ekstra – tilstandsrapport-labels (for andre meglere med separate PDF-er)
TILSTAND_LABELS = [
    "tilstandsrapport",
    "boligsalgsrapport",
    "tilstandsrapport bolig",
    "tilstandsrapport (ns 3600)",
    "boligsalgsrapport (ns 3600)",
    "takstrapport",
]

# Høy prioritet for «Vedlegg til salgsoppgave» (PrivatMegleren)
EXACT_LABELS = [
    "vedlegg til salgsoppgave",
    "vedlegg",
    "salgsoppgave vedlegg",
    "se vedlegg",
] + TILSTAND_LABELS  # prøv også eksakt treff på tilstandsrapport

# Dette er *klikk*-keywords (fall-back). Første element viktigst.
KEYWORDS = [
    # løft tilstandsrapport først (for meglere som har egne PDFer)
    "tilstandsrapport",
    "boligsalgsrapport",
    # deretter vedleggs-/prospekt-ord
    "vedlegg til salgsoppgave",
    "vedlegg",
    "salgsoppgave",
    "prospekt",
    "digitalformat",
    "se pdf",
    "last ned pdf",
    "komplett",
    "utskrift",
    "dokument",
    "dokumenter",
]

# Dette er *lenke/URL* scoring (for kandidater)
POSITIVE_WORDS = [
    "tilstandsrapport",
    "boligsalgsrapport",
    "vedlegg til salgsoppgave",
    "vedlegg",
    "salgsoppgave",
    "komplett salgsoppgave",
    "prospekt",
    "digitalformat",
    "for utskrift",
    "utskrift",
    "pdf",
]

# Skal IKKE trigge (verken klikk eller høy score)
NEGATIVE_WORDS = [
    "all informasjon om eiendommen",
    "boliginformasjon",
    "vil du vite mer",
    "kontakt megler",
    "meld interesse",
    "meld på visning",
    "bilder",
    "video",
    "360",
    "kart",
    "budskjema",
    "egenerkl",
    "egenerklaering",
    "egenerklæring",
    "energiattest",
    "energimerke",
    "nabolagsprofil",
    "meglerpakke",
    "megleropplysninger",
    "finans",
    "seksjon",
    "planinfo",
    "faktura",
    "skatt",
    "basiskart",
    "tegning",
    "situasjonsplan",
    "kommunal",
    "avgift",
    "gebyr",
]

# === PDF-gjenkjenning ===
PDF_RX = re.compile(r"\.pdf(?:[\?#][^\s\"']*)?$", re.I)
PDF_MIME_HINT = "application/pdf"

# URL-hints som ofte leverer PDF som attachment uten .pdf i URL
PDF_URL_HINTS = re.compile(
    r"(wngetfile\.ashx|/getdocument|/getfile|/download|/proxy/webmegler/.+/wngetfile\.ashx)",
    re.I,
)


def _response_looks_like_pdf(resp) -> bool:
    """Sjekk response-headere for PDF."""
    try:
        hdrs = resp.headers or {}
    except Exception:
        hdrs = {}
    ctype = (hdrs.get("content-type") or "").lower()
    dispo = (hdrs.get("content-disposition") or "").lower()
    if "application/pdf" in ctype:
        return True
    if "attachment" in dispo and ".pdf" in dispo:
        return True
    return False


def _score_pdf_candidate(href: str, text: str) -> int:
    lo = _norm_text((href or "") + " " + (text or ""))
    sc = 0
    # tungt pluss for reelle pdf/hint-url
    if href and href.lower().endswith(".pdf"):
        sc += 80
    if PDF_URL_HINTS.search(href or ""):
        sc += 120

    # EKSTRA: svært høy score for tilstandsrapport/boligsalgsrapport
    if any(w in lo for w in ("tilstandsrapport", "boligsalgsrapport")):
        sc += 1000

    # fortsatt høyt for «vedlegg til salgsoppgave» (samle-PDF)
    if "vedlegg til salgsoppgave" in lo:
        sc += 300

    for w in POSITIVE_WORDS:
        if w in lo:
            sc += 20
    for w in NEGATIVE_WORDS:
        if w in lo:
            sc -= 500
    return sc


def _pick_best_pdf_href(anchors: List[dict]) -> Optional[str]:
    best, best_sc = None, -(10**9)
    for a in anchors:
        href = (a.get("href") or "").strip()
        text = (a.get("text") or "").strip()
        if not href:
            continue
        sc = _score_pdf_candidate(href, text)
        if sc > best_sc:
            best_sc, best = sc, href
    return best


# URL-varianter vi prøver på megler-sider
DOC_ANCHOR_VARIANTS = ["#salgsoppgave", "#dokumenter"]
PATH_VARIANTS = ["salgsoppgave", "dokumenter"]  # legger til på slutten av path


def _text_like(el_text: str) -> bool:
    t = _norm_text(el_text or "")
    if not t:
        return False
    if any(n in t for n in NEGATIVE_WORDS):
        return False
    if any(t == lab or lab in t for lab in EXACT_LABELS):
        return True
    return any(k in t for k in KEYWORDS)


def _maybe_accept_cookies(page) -> None:
    # 1) kjente selectorer
    selectors = [
        "#onetrust-accept-btn-handler",
        "button#cookie-accept",
        "[data-testid='cookie-accept']",
        "button[aria-label*='godta' i]",
        "button[aria-label*='aksepter' i]",
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel)
            if btn.count() > 0:
                btn.first.click(timeout=1000)
                return
        except Exception:
            pass

    # 2) fallback: match på knappetekst
    text_variants = [
        "tillat alle",
        "godta alle",
        "aksepter alle",
        "godta",
        "aksepter",
        "accept all",
        "allow all",
    ]
    try:
        buttons = page.locator("button")
        n = buttons.count()
        for i in range(min(n, 80)):
            b = buttons.nth(i)
            try:
                t = _norm_text(b.inner_text(timeout=300) or "")
            except Exception:
                continue
            if any(tv in t for tv in text_variants):
                try:
                    b.click(timeout=1500)
                    return
                except Exception:
                    continue
    except Exception:
        pass


# -------- Anker-høsting (href + synlig tekst) --------
def _harvest_anchors_with_text(page) -> List[Dict[str, str]]:
    try:
        return page.evaluate(
            """
            () => Array.from(document.querySelectorAll('a[href]')).map(a => ({
                href: a.href || '',
                text: (a.innerText || a.textContent || '').trim()
            }))
        """
        )
    except Exception:
        return []


def _harvest_anchors_with_text_from_frames(page) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    try:
        for fr in page.frames:
            try:
                if not fr.url or fr.url.startswith("about:"):
                    continue
            except Exception:
                pass
            try:
                anchors = fr.evaluate(
                    """
                    () => Array.from(document.querySelectorAll('a[href]')).map(a => ({
                        href: a.href || '',
                        text: (a.innerText || a.textContent || '').trim()
                    }))
                """
                )
                if isinstance(anchors, list):
                    out.extend(anchors)
            except Exception:
                continue
    except Exception:
        pass
    return out


# -------- Eksakt klikk (main + iframes) --------
def _click_exact_label_in(
    page_or_frame, labels: List[str], dbg: Dict[str, Any], where: str
):
    """
    Klikker på en link/knapp med eksakt tekst (labels).
    Returnerer:
       - dict {"bytes": ..., "url": ...} hvis direkte PDF via href ble hentet
       - True hvis klikk ble gjort (for videre sniff/harvest)
       - False hvis ingenting ble klikket
    """
    # 1) Søk i <a> først – ofte med direkte href
    try:
        links = page_or_frame.locator("a")
        n = links.count()
        for i in range(min(n, 200)):
            el = links.nth(i)
            try:
                raw = el.inner_text(timeout=300) or ""
            except Exception:
                raw = ""
            txt = _norm_text(raw)
            if not txt:
                continue
            if any(lbl in txt or txt == lbl for lbl in labels):
                href = ""
                try:
                    href = el.get_attribute("href") or ""
                except Exception:
                    href = ""
                if href and not _is_blacklisted_pdf(href):
                    try:
                        rr = el.page.context.request.get(
                            href,
                            headers={
                                "Accept": "application/pdf,application/octet-stream,*/*"
                            },
                            timeout=8000,
                        )
                        if rr.ok:
                            body = rr.body()
                            if (
                                body and body[:4] == b"%PDF"
                            ) or _response_looks_like_pdf(rr):
                                dbg[f"click_exact_{where}"] = {
                                    "selector": "a",
                                    "index": i,
                                    "text": raw[:200],
                                    "direct_href": href,
                                }
                                return {
                                    "bytes": (
                                        body if body and body[:4] == b"%PDF" else body
                                    ),
                                    "url": href,
                                }
                    except Exception:
                        pass
                # ellers klikk
                try:
                    el.scroll_into_view_if_needed(timeout=800)
                except Exception:
                    pass
                try:
                    el.click(timeout=2000)
                except Exception:
                    try:
                        el.click(timeout=2000, force=True)
                    except Exception:
                        continue
                dbg[f"click_exact_{where}"] = {
                    "selector": "a",
                    "index": i,
                    "text": raw[:200],
                }
                return True
    except Exception:
        pass

    # 2) Søk i knapper
    try:
        buttons = page_or_frame.locator(
            "button, [role='button'], div[role='button'], span[role='button']"
        )
        n = buttons.count()
        for i in range(min(n, 200)):
            el = buttons.nth(i)
            try:
                raw = el.inner_text(timeout=300) or ""
            except Exception:
                raw = ""
            txt = _norm_text(raw)
            if not txt:
                continue
            if any(lbl in txt or txt == lbl for lbl in labels):
                try:
                    el.scroll_into_view_if_needed(timeout=800)
                except Exception:
                    pass
                try:
                    el.click(timeout=2000)
                except Exception:
                    try:
                        el.click(timeout=2000, force=True)
                    except Exception:
                        continue
                dbg[f"click_exact_{where}"] = {
                    "selector": "button",
                    "index": i,
                    "text": raw[:200],
                }
                return True
    except Exception:
        pass

    return False


def _click_exact_label(page, dbg: Dict[str, Any]):
    """Klikk en av våre høyest prioriterte labels (main + iframes)."""
    res = _click_exact_label_in(page, EXACT_LABELS, dbg, where="main")
    if res:
        return res
    try:
        for fr in page.frames:
            try:
                if not fr.url or fr.url.startswith("about:"):
                    continue
            except Exception:
                pass
            res = _click_exact_label_in(
                fr, EXACT_LABELS, dbg, where=getattr(fr, "url", "frame")
            )
            if res:
                return res
    except Exception:
        pass
    return False


# -------- Generisk klikk (fallback) --------
def _click_candidates(page, dbg: Dict[str, Any]) -> None:
    locs = [
        "button",
        "[role='button']",
        "a",
        "div[role='button']",
        "span[role='button']",
    ]
    attempts_log: List[Dict[str, Any]] = []
    max_logged = 100

    for sel in locs:
        try:
            els = page.locator(sel)
            n = els.count()
            if not n:
                continue
            upto = min(n, 120)
            for i in range(upto):
                h = els.nth(i)
                try:
                    txt = (h.inner_text(timeout=300) or "").strip()
                except Exception:
                    txt = ""
                looks_ok = _text_like(txt)
                if len(attempts_log) < max_logged:
                    attempts_log.append(
                        {
                            "selector": sel,
                            "index": i,
                            "text_preview": (txt[:80] + ("…" if len(txt) > 80 else "")),
                            "match": looks_ok,
                        }
                    )
                if not looks_ok:
                    continue
                try:
                    h.click(timeout=1500)
                    dbg["click_hit"] = {"selector": sel, "index": i, "text": txt[:200]}
                    dbg["click_attempts"] = attempts_log
                    return
                except Exception as e:
                    if len(attempts_log) < max_logged:
                        attempts_log.append(
                            {
                                "selector": sel,
                                "index": i,
                                "text_preview": (
                                    txt[:80] + ("…" if len(txt) > 80 else "")
                                ),
                                "match": True,
                                "click_error": type(e).__name__,
                            }
                        )
                    continue
        except Exception as e:
            if len(attempts_log) < max_logged:
                attempts_log.append(
                    {"selector": sel, "locator_error": type(e).__name__}
                )
            continue
    dbg["click_attempts"] = attempts_log  # ingen treff


# -------- HØSTING AV URLER (uten klikk) --------
def _harvest_pdf_urls_from_dom(page) -> List[str]:
    """Finn både .pdf og hint-URLer (ashx/getdocument/download) i DOM."""
    try:
        urls = page.evaluate(
            """
            () => Array.from(document.querySelectorAll('a[href]'))
                       .map(a => a.href)
                       .filter(u => typeof u === 'string')
            """
        )
        out: List[str] = []
        for u in urls:
            lu = (u or "").lower()
            if ".pdf" in lu or PDF_URL_HINTS.search(lu):
                if not _is_blacklisted_pdf(u):
                    out.append(u)
        # uniq
        seen, uniq = set(), []
        for u in out:
            if u not in seen:
                seen.add(u)
                uniq.append(u)
        return uniq
    except Exception:
        return []


def _harvest_pdf_urls_from_next(page) -> List[str]:
    out: List[str] = []
    try:
        txt = page.evaluate(
            """() => {
                const el = document.getElementById('__NEXT_DATA__');
                return el ? el.textContent : null;
            }"""
        )
        if isinstance(txt, str) and txt:
            # .pdf
            for m in re.finditer(
                r'https?://[^"\'\\s]+?\\.pdf(?:\\?[^"\'\\s]*)?', txt, re.I
            ):
                u = m.group(0).replace("\\/", "/")
                if not _is_blacklisted_pdf(u):
                    out.append(u)
            # hint-URLer
            for m in re.finditer(
                r'https?://[^"\'\\s]+?(wngetfile\\.ashx|/getdocument|/getfile|/download)[^"\'\\s]*',
                txt,
                re.I,
            ):
                u = m.group(0).replace("\\/", "/")
                if not _is_blacklisted_pdf(u):
                    out.append(u)
    except Exception:
        pass
    # uniq
    seen = set()
    uniq: List[str] = []
    for u in out:
        if u not in seen:
            uniq.append(u)
            seen.add(u)
    return uniq


def _harvest_pdf_urls_from_scripts(page) -> List[str]:
    out: List[str] = []
    try:
        scripts = page.locator("script")
        n = scripts.count()
        for i in range(min(n, 60)):
            try:
                content = scripts.nth(i).inner_text(timeout=200) or ""
            except Exception:
                continue
            if not content:
                continue
            # .pdf
            for m in re.finditer(
                r'https?://[^"\'\\s]+?\\.pdf(?:\\?[^"\'\\s]*)?', content, re.I
            ):
                u = m.group(0)
                if not _is_blacklisted_pdf(u):
                    out.append(u)
            # hint-URLer
            for m in re.finditer(
                r'https?://[^"\'\\s]+?(wngetfile\\.ashx|/getdocument|/getfile|/download)[^"\'\\s]*',
                content,
                re.I,
            ):
                u = m.group(0)
                if not _is_blacklisted_pdf(u):
                    out.append(u)
    except Exception:
        pass
    # uniq
    seen = set()
    uniq: List[str] = []
    for u in out:
        if u not in seen:
            uniq.append(u)
            seen.add(u)
    return uniq


# -------- IFRAME-STØTTE (klikk + harvesting) --------
def _harvest_pdf_urls_from_frame_dom(frame) -> List[str]:
    try:
        urls = frame.evaluate(
            """
            () => Array.from(document.querySelectorAll('a[href]'))
                       .map(a => a.href)
                       .filter(u => typeof u === 'string')
            """
        )
        out: List[str] = []
        for u in urls:
            lu = (u or "").lower()
            if ".pdf" in lu or PDF_URL_HINTS.search(lu):
                if not _is_blacklisted_pdf(u):
                    out.append(u)
        # uniq
        seen, uniq = set(), []
        for u in out:
            if u not in seen:
                seen.add(u)
                uniq.append(u)
        return uniq
    except Exception:
        return []


def _harvest_pdf_urls_from_frame_scripts(frame) -> List[str]:
    out: List[str] = []
    try:
        scripts = frame.locator("script")
        n = scripts.count()
        for i in range(min(n, 80)):
            try:
                content = scripts.nth(i).inner_text(timeout=250) or ""
            except Exception:
                continue
            if not content:
                continue
            for m in re.finditer(
                r'https?://[^"\'\\s]+?\\.pdf(?:\\?[^"\'\\s]*)?', content, re.I
            ):
                u = m.group(0)
                if not _is_blacklisted_pdf(u):
                    out.append(u)
            for m in re.finditer(
                r'https?://[^"\'\\s]+?(wngetfile\\.ashx|/getdocument|/getfile|/download)[^"\'\\s]*',
                content,
                re.I,
            ):
                u = m.group(0)
                if not _is_blacklisted_pdf(u):
                    out.append(u)
    except Exception:
        pass
    # uniq
    seen = set()
    uniq: List[str] = []
    for u in out:
        if u not in seen:
            uniq.append(u)
            seen.add(u)
    return uniq


def _harvest_pdf_urls_from_frames(page) -> List[str]:
    """Kjør harvesting i alle iframes/frames (DOM + scripts)."""
    collected: List[str] = []
    try:
        for fr in page.frames:
            try:
                if not fr.url or fr.url.startswith("about:"):
                    continue
            except Exception:
                pass
            try:
                dom_urls = _harvest_pdf_urls_from_frame_dom(fr)
                scr_urls = _harvest_pdf_urls_from_frame_scripts(fr)
                for u in dom_urls + scr_urls:
                    if (
                        isinstance(u, str)
                        and u not in collected
                        and not _is_blacklisted_pdf(u)
                    ):
                        collected.append(u)
            except Exception:
                continue
    except Exception:
        pass
    return collected


def _click_candidates_in_frames(page, dbg: Dict[str, Any]) -> None:
    """Forsøk samme klikk-heuristikk i alle iframes."""
    attempts_log = dbg.setdefault("frame_click_attempts", [])
    max_logged = 100

    def _scan_frame(fr):
        nonlocal attempts_log
        locs = [
            "button",
            "[role='button']",
            "a",
            "div[role='button']",
            "span[role='button']",
        ]
        for sel in locs:
            try:
                els = fr.locator(sel)
                n = els.count()
                if not n:
                    continue
                upto = min(n, 100)
                for i in range(upto):
                    el = els.nth(i)
                    try:
                        txt = (el.inner_text(timeout=300) or "").strip()
                    except Exception:
                        txt = ""
                    looks = _text_like(txt)
                    if len(attempts_log) < max_logged:
                        attempts_log.append(
                            {
                                "frame_url": getattr(fr, "url", ""),
                                "selector": sel,
                                "index": i,
                                "text_preview": (
                                    txt[:80] + ("…" if len(txt) > 80 else "")
                                ),
                                "match": looks,
                            }
                        )
                    if not looks:
                        continue
                    try:
                        el.click(timeout=1500)
                        dbg["frame_click_hit"] = {
                            "frame_url": getattr(fr, "url", ""),
                            "selector": sel,
                            "index": i,
                            "text": txt[:200],
                        }
                        return True
                    except Exception:
                        continue
            except Exception:
                continue
        return False

    try:
        for fr in page.frames:
            try:
                if not fr.url or fr.url.startswith("about:"):
                    continue
            except Exception:
                pass
            if _scan_frame(fr):
                return
    except Exception:
        pass


def _fetch_pdf_bytes_via_context_request(context, url: str) -> bytes | None:
    if _is_blacklisted_pdf(url):
        return None
    try:
        r = context.request.get(
            url, headers={"Accept": "application/pdf,application/octet-stream,*/*"}
        )
        if r.ok:
            body = r.body()
            if (body and body[:4] == b"%PDF") or _response_looks_like_pdf(r):
                return body if body else None
    except Exception:
        pass
    return None


# -------- HOVEDFUNKSJON --------
def fetch_pdf_with_browser(
    start_url: str, *, timeout: int = 25000
) -> Tuple[bytes | None, str | None, Dict[str, Any]]:
    """
    Forsterket Playwright-fallback:
      - prøver url-varianter (/salgsoppgave, /dokumenter, #salgsoppgave)
      - aksepterer cookies
      - klikker relevante knapper/lenker (også i iframes)
      - sniffer nettverk for PDF-responser + JSON/XHR som inneholder PDF-URLer (og ashx/download-hints)
      - høster URLer fra DOM/__NEXT_DATA__/scripts (og i iframes)
      - binærhenter via context.request.get()
    Returnerer (pdf_bytes, pdf_url, debug)
    """
    dbg: Dict[str, Any] = {"url": start_url, "step": "start", "notes": []}
    pdf_bytes: Optional[bytes] = None
    pdf_url: Optional[str] = None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(accept_downloads=True, user_agent=BROWSER_UA)
            page = context.new_page()

            # ---- Sniff network for PDFs (inkl. JSON/XHR som peker til PDF) ----
            def handle_response(resp):
                nonlocal pdf_bytes, pdf_url
                try:
                    ctype = (resp.headers or {}).get("content-type", "").lower()
                except Exception:
                    ctype = ""
                url = resp.url

                # 1) Direkte PDF-respons eller hintede endepunkter
                try:
                    if (
                        (PDF_MIME_HINT in ctype)
                        or PDF_RX.search(url)
                        or PDF_URL_HINTS.search(url)
                        or _response_looks_like_pdf(resp)
                    ):
                        if _is_blacklisted_pdf(url):
                            dbg["notes"].append(f"skip:blacklisted:{url}")
                            return
                        if pdf_bytes is None:
                            body = resp.body()
                            if body and (
                                body[:4] == b"%PDF" or _response_looks_like_pdf(resp)
                            ):
                                pdf_bytes = body
                                pdf_url = url
                                dbg["notes"].append(f"net:pdf_response:{url}")
                                return
                except Exception:
                    pass

                # 2) JSON/XHR som kan inneholde lenker
                try:
                    if (
                        "application/json" in ctype
                        or "text/plain" in ctype
                        or "application/javascript" in ctype
                        or "text/javascript" in ctype
                    ):
                        txt = None
                        try:
                            txt = resp.text()
                        except Exception:
                            try:
                                b = resp.body()
                                txt = b.decode("utf-8", errors="ignore") if b else ""
                            except Exception:
                                txt = ""
                        if not txt or pdf_bytes is not None:
                            return
                        # .pdf-lenker
                        for m in re.finditer(
                            r'https?://[^\s"\'<>]+\.pdf(?:\?[^\s<>\'"]*)?', txt, re.I
                        ):
                            u = m.group(0)
                            if not u or pdf_bytes is not None:
                                continue
                            if _is_blacklisted_pdf(u):
                                dbg["notes"].append(f"skip:blacklisted:{u}")
                                continue
                            try:
                                rr = page.context.request.get(u, timeout=timeout)
                                if rr.ok:
                                    b = rr.body()
                                    if b and b[:4] == b"%PDF":
                                        pdf_bytes = b
                                        pdf_url = u
                                        dbg["notes"].append(f"net:json_link_pdf:{u}")
                                        return
                            except Exception:
                                continue
                        # hint-URLer (ashx/download)
                        for m in re.finditer(
                            r'https?://[^\s"\'<>]+?(wngetfile\.ashx|/getdocument|/getfile|/download)[^\s<>\'"]*',
                            txt,
                            re.I,
                        ):
                            u = m.group(0)
                            if pdf_bytes is not None:
                                break
                            if _is_blacklisted_pdf(u):
                                dbg["notes"].append(f"skip:blacklisted:{u}")
                                continue
                            try:
                                rr = page.context.request.get(u, timeout=timeout)
                                if rr.ok:
                                    b = rr.body()
                                    if b and (
                                        b[:4] == b"%PDF" or _response_looks_like_pdf(rr)
                                    ):
                                        pdf_bytes = b
                                        pdf_url = u
                                        dbg["notes"].append(f"net:json_link_hint:{u}")
                                        return
                            except Exception:
                                continue
                except Exception:
                    pass

            page.on("response", handle_response)

            # ---- Prøv å gå til flere varianter av URL ----
            base = start_url.split("#")[0].rstrip("/")
            variants: List[str] = [start_url]
            for seg in PATH_VARIANTS:
                variants.append(base + "/" + seg)
            for anc in DOC_ANCHOR_VARIANTS:
                variants.append(base + anc)

            seen = set()
            unique_variants = []
            for u in variants:
                if u not in seen:
                    unique_variants.append(u)
                    seen.add(u)

            for u in unique_variants:
                try:
                    page.goto(u, wait_until="domcontentloaded", timeout=timeout)
                except PWTimeoutError:
                    try:
                        page.goto(u, timeout=timeout)
                    except Exception:
                        continue

                _maybe_accept_cookies(page)

                # Scroll litt for å trigge lazy innhold
                try:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight/3)")
                    page.wait_for_timeout(400)
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight*0.9)")
                    page.wait_for_timeout(400)
                except Exception:
                    pass

                # Klikk eksakt label først (main + iframes)
                _clicked_specific = _click_exact_label(page, dbg)
                if isinstance(_clicked_specific, dict) and "bytes" in _clicked_specific:
                    dbg["step"] = "browser_ok_sniff"
                    context.close()
                    browser.close()
                    return _clicked_specific["bytes"], _clicked_specific.get("url"), dbg

                if not _clicked_specific:
                    _click_candidates(page, dbg)
                    _click_candidates_in_frames(page, dbg)

                # Vent på nettverksidle eller kort pause for XHR
                try:
                    page.wait_for_load_state("networkidle", timeout=4000)
                except Exception:
                    page.wait_for_timeout(1200)

                # Hvis sniff allerede fant PDF
                if pdf_bytes and pdf_url:
                    dbg["step"] = "browser_ok_sniff"
                    break

                # --- NYTT: prøv ankre (href + tekst) og hent beste kandidater først ---
                anchors_all = _harvest_anchors_with_text(
                    page
                ) + _harvest_anchors_with_text_from_frames(page)
                if anchors_all:
                    scored = [
                        (_score_pdf_candidate(a.get("href", ""), a.get("text", "")), a)
                        for a in anchors_all
                        if a.get("href")
                    ]
                    scored.sort(key=lambda x: x[0], reverse=True)
                    for _sc, a in scored[:10]:
                        href = a.get("href", "")
                        if not href or _is_blacklisted_pdf(href):
                            continue
                        b = _fetch_pdf_bytes_via_context_request(context, href)
                        if b:
                            dbg["notes"].append(f"anchor_hit:{href}")
                            dbg["step"] = "browser_ok_sniff"
                            context.close()
                            browser.close()
                            return b, href, dbg

                # --- HØST URLER UTEN KLIKK (main + iframes) ---
                urls_dom = _harvest_pdf_urls_from_dom(page)
                urls_next = _harvest_pdf_urls_from_next(page)
                urls_js = _harvest_pdf_urls_from_scripts(page)
                urls_frames = _harvest_pdf_urls_from_frames(page)

                harvested: List[str] = []
                for uu in urls_dom + urls_next + urls_js + urls_frames:
                    if (
                        isinstance(uu, str)
                        and uu not in harvested
                        and not _is_blacklisted_pdf(uu)
                    ):
                        harvested.append(uu)

                if harvested:
                    dbg["notes"].append(f"harvested:{len(harvested)}")
                    # sorter beste først
                    scored = [(_score_pdf_candidate(pu, pu), pu) for pu in harvested]
                    harvested = [
                        pu for _, pu in sorted(scored, key=lambda x: x[0], reverse=True)
                    ]

                    # Prøv å hente første som funker
                    for pu in harvested:
                        b = _fetch_pdf_bytes_via_context_request(context, pu)
                        if b:
                            dbg["notes"].append(f"harvest_hit:{pu}")
                            dbg["step"] = "browser_ok_sniff"
                            context.close()
                            browser.close()
                            return b, pu, dbg

                # Som ekstra forsøk: vent på download-event (noen sider åpner tom side + attachment)
                try:
                    dl = page.wait_for_event("download", timeout=2000)
                    try:
                        src = dl.url
                        if src and (PDF_RX.search(src) or PDF_URL_HINTS.search(src)):
                            if not _is_blacklisted_pdf(src):
                                b = _fetch_pdf_bytes_via_context_request(context, src)
                                if b:
                                    dbg["notes"].append(f"download_event_hit:{src}")
                                    dbg["step"] = "browser_ok_sniff"
                                    context.close()
                                    browser.close()
                                    return b, src, dbg
                                else:
                                    dbg["notes"].append(f"download_event:{src}")
                    except Exception:
                        pass
                except Exception:
                    pass

                if pdf_bytes and pdf_url:
                    dbg["step"] = "browser_ok_sniff"
                    break

            context.close()
            browser.close()

    except Exception as e:
        dbg["step"] = "browser_exception"
        dbg["error"] = repr(e)
        return None, None, dbg

    if pdf_bytes and pdf_url:
        return pdf_bytes, pdf_url, dbg
    else:
        dbg["step"] = "browser_failed"
        return None, None, dbg


def fetch_pdf_with_browser_filtered(
    start_url: str,
    *,
    click_text_contains: list[str],
    allow_only_if_url_contains: list[str] | None = None,
    deny_if_url_contains: list[str] | None = None,
    timeout_ms: int = 30000,
) -> tuple[bytes | None, str | None, dict]:
    """
    Naviger til start_url, klikk på en knapp/lenke som inneholder en av tekstene i
    `click_text_contains`, og sniff deretter network-responsene. Returner KUN PDF
    dersom URL matcher `allow_only_if_url_contains` og IKKE matcher `deny_if_url_contains`.
    """
    dbg: dict[str, any] = {
        "start_url": start_url,
        "step": "start",
        "click_hints": click_text_contains,
        "allow_only": allow_only_if_url_contains or [],
        "deny": deny_if_url_contains or [],
        "click_note": None,
        "response_hit": None,
        "download_hit": None,
    }

    def _norm(s: str) -> str:
        try:
            import unicodedata

            s = unicodedata.normalize("NFKD", s)
            s = "".join(ch for ch in s if not unicodedata.combining(ch))
        except Exception:
            pass
        return (s or "").strip().lower()

    allow = [a.lower() for a in (allow_only_if_url_contains or [])]
    deny = [d.lower() for d in (deny_if_url_contains or [])]
    hints = [_norm(t) for t in (click_text_contains or []) if t]

    def _url_allowed(u: str) -> bool:
        lo = (u or "").lower()
        if any(d in lo for d in deny):
            return False
        if allow:
            return any(a in lo for a in allow)
        return True

    pdf_bytes: bytes | None = None
    pdf_url: str | None = None

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(accept_downloads=True, user_agent=BROWSER_UA)
            page = context.new_page()

            # --- network sniff ---
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

                looks_like_pdf = (
                    (PDF_MIME_HINT in ctype)
                    or PDF_RX.search(url)
                    or PDF_URL_HINTS.search(url)
                    or _response_looks_like_pdf(resp)
                )
                if not looks_like_pdf:
                    return

                if _is_blacklisted_pdf(url):
                    return

                try:
                    body = resp.body()
                except Exception:
                    body = None

                if body and (body[:4] == b"%PDF" or "application/pdf" in ctype):
                    pdf_bytes, pdf_url = body, url
                    dbg["response_hit"] = url

            page.on("response", handle_response)

            # --- goto ---
            try:
                page.goto(start_url, wait_until="domcontentloaded", timeout=timeout_ms)
            except PWTimeoutError:
                page.goto(start_url, timeout=timeout_ms)

            # cookie-accept (beste-effort)
            try:
                _maybe_accept_cookies(page)
            except Exception:
                pass

            # --- Finn kandidat-element å klikke ---
            def _click_by_text() -> bool:
                # 1) <a>
                try:
                    links = page.locator("a[href]")
                    n = links.count()
                    for i in range(min(n, 250)):
                        el = links.nth(i)
                        try:
                            raw = el.inner_text(timeout=300) or ""
                        except Exception:
                            raw = ""
                        txt = _norm(raw)
                        if not txt or not any(h in txt for h in hints):
                            continue

                        # Hvis direkte href ser bra ut, prøv å hente binært direkte
                        try:
                            href = el.get_attribute("href") or ""
                        except Exception:
                            href = ""
                        if href:
                            absu = href
                            if _url_allowed(absu) and not _is_blacklisted_pdf(absu):
                                try:
                                    rr = page.context.request.get(
                                        absu,
                                        headers={
                                            "Accept": "application/pdf,application/octet-stream,*/*"
                                        },
                                        timeout=timeout_ms,
                                    )
                                    if rr.ok:
                                        b = rr.body()
                                        if b and b[:4] == b"%PDF":
                                            dbg["click_note"] = "direct_href_fetch"
                                            return_value = (b, absu)
                                            # sett resultater
                                            nonlocal pdf_bytes, pdf_url
                                            pdf_bytes, pdf_url = return_value
                                            return True
                                except Exception:
                                    pass

                        # ellers: klikk
                        try:
                            el.scroll_into_view_if_needed(timeout=600)
                        except Exception:
                            pass
                        try:
                            el.click(timeout=2000)
                            dbg["click_note"] = "clicked_link"
                            return True
                        except Exception:
                            try:
                                el.click(timeout=2000, force=True)
                                dbg["click_note"] = "clicked_link_force"
                                return True
                            except Exception:
                                continue
                except Exception:
                    pass

                # 2) buttons/role=button
                try:
                    buttons = page.locator(
                        "button, [role='button'], div[role='button'], span[role='button']"
                    )
                    n = buttons.count()
                    for i in range(min(n, 250)):
                        el = buttons.nth(i)
                        try:
                            raw = el.inner_text(timeout=300) or ""
                        except Exception:
                            raw = ""
                        txt = _norm(raw)
                        if not txt or not any(h in txt for h in hints):
                            continue
                        try:
                            el.scroll_into_view_if_needed(timeout=600)
                        except Exception:
                            pass
                        try:
                            el.click(timeout=2000)
                            dbg["click_note"] = "clicked_button"
                            return True
                        except Exception:
                            try:
                                el.click(timeout=2000, force=True)
                                dbg["click_note"] = "clicked_button_force"
                                return True
                            except Exception:
                                continue
                except Exception:
                    pass
                return False

            clicked = _click_by_text()
            if not clicked:
                dbg["click_note"] = "no element matched hints"

            # gi tid til XHR/nedlastning
            try:
                page.wait_for_load_state("networkidle", timeout=4000)
            except Exception:
                page.wait_for_timeout(1200)

            # hvis response-sniff traff
            if pdf_bytes and pdf_url:
                dbg["step"] = "ok"
                context.close()
                browser.close()
                return pdf_bytes, pdf_url, dbg

            # fallback: download event (attachment uten URL i DOM)
            try:
                dl = page.wait_for_event("download", timeout=2500)
                if dl:
                    u = dl.url or ""
                    if u and _url_allowed(u) and not _is_blacklisted_pdf(u):
                        try:
                            b = context.request.get(
                                u,
                                headers={
                                    "Accept": "application/pdf,application/octet-stream,*/*"
                                },
                                timeout=timeout_ms,
                            )
                            if b.ok:
                                body = b.body()
                                if body and body[:4] == b"%PDF":
                                    dbg["download_hit"] = u
                                    dbg["step"] = "ok"
                                    context.close()
                                    browser.close()
                                    return body, u, dbg
                        except Exception:
                            pass
            except Exception:
                pass

            # siste sjanse: __NEXT_DATA__ / scripts for skjulte PDF-URLer
            try:
                txt = page.evaluate(
                    """() => {
                    const el = document.getElementById('__NEXT_DATA__');
                    return el ? el.textContent : null;
                }"""
                )
            except Exception:
                txt = None
            harvested: list[str] = []
            if isinstance(txt, str) and txt:
                for m in re.finditer(
                    r'https?://[^"\'\\s]+?\\.pdf(?:\\?[^"\'\\s]*)?', txt, re.I
                ):
                    u = m.group(0).replace("\\/", "/")
                    if _url_allowed(u) and not _is_blacklisted_pdf(u):
                        harvested.append(u)

            try:
                scripts = page.locator("script")
                n = scripts.count()
                for i in range(min(n, 60)):
                    try:
                        content = scripts.nth(i).inner_text(timeout=250) or ""
                    except Exception:
                        continue
                    for m in re.finditer(
                        r'https?://[^"\'\\s]+?\\.pdf(?:\\?[^"\'\\s]*)?', content, re.I
                    ):
                        u = m.group(0)
                        if _url_allowed(u) and not _is_blacklisted_pdf(u):
                            harvested.append(u)
            except Exception:
                pass

            # uniq
            seen = set()
            uniq = []
            for u in harvested:
                if u not in seen:
                    seen.add(u)
                    uniq.append(u)

            # prøv å hente i prioritert rekkefølge (tillat først)
            for u in uniq:
                try:
                    r = context.request.get(
                        u,
                        headers={
                            "Accept": "application/pdf,application/octet-stream,*/*"
                        },
                        timeout=timeout_ms,
                    )
                    if r.ok:
                        b = r.body()
                        if b and b[:4] == b"%PDF":
                            dbg["response_hit"] = u
                            dbg["step"] = "ok"
                            context.close()
                            browser.close()
                            return b, u, dbg
                except Exception:
                    continue

            dbg["step"] = "failed"
            context.close()
            browser.close()
            return None, None, dbg

    except Exception as e:
        dbg["step"] = "exception"
        dbg["error"] = repr(e)
        return None, None, dbg
