# core/drivers/garanti.py
from __future__ import annotations

import io
import re
from typing import Tuple, Dict, Any, Optional

import requests
from PyPDF2 import PdfReader

from .base import Driver
from techdom.ingestion.http_headers import BROWSER_HEADERS
from techdom.infrastructure.config import SETTINGS

# Hvis du har en Playwright-basert helper: fetch_pdf_with_browser(url) -> (bytes|None, final_url|None, debug_dict)
from techdom.ingestion.browser_fetch import fetch_pdf_with_browser  # type: ignore

PDF_MAGIC = b"%PDF-"
MIN_REAL_BYTES = 2_000_000  # ~2 MB – reelle salgsoppgaver er normalt > 2–3 MB
MIN_REAL_PAGES = 8

_G_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

# Ting vi ikke vil forveksle med salgsoppgaven (i URL/filnavn)
BLOCK_URL_HINTS = (
    "tilstandsrapport",
    "boligsalgsrapport",
    "ns3600",
    "ns_3600",
    "ns-3600",
    "energiattest",
    "nabolag",
    "nabolagsprofil",
    "contentassets/nabolaget",
    "egenerkl",
    "anticimex",
    "bud",
    "budskjema",
    "prisliste",
    "vilkår",
    "terms",
    "cookies",
)

# Vi aksepterer kun disse "kildene" som salgsoppgave
ALLOW_URL_HINTS = (
    "digitalsalgsoppgave.garanti.no",
    "meglervisning.no/salgsoppgave/hent",
)

# Raskt negativt for objekt-side-URL-er (ikke avgjørende, men sparer runder)
NEG_PATTERNS = (
    "GARANTI_10EnkleTips.pdf",
    "/files/doc/",
    "boligkjøperforsikring",
    "anticimex",
    "nabolagsprofil",
    "prisliste",
)


def _looks_like_pdf(b: bytes | None) -> bool:
    return isinstance(b, (bytes, bytearray)) and b.startswith(PDF_MAGIC)


def _pdf_ok(b: bytes | None) -> bool:
    """Kvalitetsfilter: PDF-header + størrelse + minimum side-antal."""
    if not b or len(b) < MIN_REAL_BYTES or not _looks_like_pdf(b):
        return False
    try:
        return len(PdfReader(io.BytesIO(b)).pages) >= MIN_REAL_PAGES
    except Exception:
        return False


def _url_is_allowed(u: str | None) -> bool:
    lo = (u or "").lower()
    if not lo:
        return False
    if any(b in lo for b in BLOCK_URL_HINTS):
        return False
    return any(a in lo for a in ALLOW_URL_HINTS)


def _extract_first_url_from_pdf(b: bytes) -> Optional[str]:
    """Hent første http(s)-URL fra tekstinnholdet (nyttig når mini-PDF peker videre)."""
    try:
        rdr = PdfReader(io.BytesIO(b))
        fragments: list[str] = []
        for p in rdr.pages[:3]:
            try:
                t = p.extract_text() or ""
            except Exception:
                t = ""
            if t:
                fragments.append(t)
        m = re.search(r"https?://[^\s)>\]]+", "\n".join(fragments))
        return m.group(0) if m else None
    except Exception:
        return None


def _find_estateid_in_text(txt: str) -> Optional[str]:
    """Plukk ut estateId fra diverse formater i HTML/JS."""
    m = re.search(r"[?&]Estateid=(" + _G_UUID + ")", txt, re.I)
    if m:
        return m.group(1)
    m = re.search(r"digitalsalgsoppgave\.garanti\.no/(" + _G_UUID + r")/\d+", txt, re.I)
    if m:
        return m.group(1)
    m = re.search(r'"estateId"\s*:\s*"(' + _G_UUID + ')"', txt, re.I)
    if m:
        return m.group(1)
    return None


class GarantiDriver(Driver):
    name = "garanti"

    def matches(self, url: str) -> bool:
        u = (url or "").lower()
        # Garanti-objekter ligger typisk under /eiendom/
        return "garanti.no/eiendom/" in u

    def try_fetch(
        self, sess: requests.Session, page_url: str
    ) -> Tuple[bytes | None, str | None, dict]:
        dbg: Dict[str, Any] = {"driver": self.name, "step": "start", "meta": {}}
        timeout = SETTINGS.REQ_TIMEOUT

        # 1) Hent megler-HTML
        try:
            r = sess.get(
                page_url,
                headers=BROWSER_HEADERS,
                timeout=timeout,
                allow_redirects=True,
            )
            r.raise_for_status()
            html = r.text or ""
            dbg["meta"]["page_status"] = r.status_code
            dbg["meta"]["page_len"] = len(html)
        except Exception as e:
            dbg["step"] = f"page_fetch_error:{type(e).__name__}"
            dbg["meta"]["error"] = str(e)
            return None, None, dbg

        # Hurtig-negativ: åpenbart ikke-salgsoppgave?
        lo_url = page_url.lower()
        if any(p in lo_url for p in NEG_PATTERNS):
            dbg["step"] = "negative_pattern_in_url"
            return None, None, dbg

        # 2) Forsøk direkte DigitalSalgsoppgave-link i HTML
        m_ds = re.search(
            r'https?://digitalsalgsoppgave\.garanti\.no/[^\s"\']+', html, re.I
        )
        if m_ds:
            ds_url = m_ds.group(0)
            dbg["meta"]["ds_from_html"] = ds_url
            try:
                b, u, bdbg = fetch_pdf_with_browser(ds_url)
                dbg["browser"] = bdbg
                if b and _pdf_ok(b) and _url_is_allowed(u or ds_url):
                    dbg["step"] = "browser_ok_ds_from_html"
                    return b, u or ds_url, dbg
            except Exception as e:
                dbg["meta"]["ds_browser_error"] = str(e)

        # 3) Finn estateId i megler-HTML (flest treff)
        estate_id = _find_estateid_in_text(html)
        dbg["meta"]["estate_id_from_megler"] = estate_id

        # 4) Hvis estateId: hent “mini/full” PDF fra Meglervisning (instid=MSGAR)
        if estate_id:
            mv_url = f"https://meglervisning.no/salgsoppgave/hent?instid=MSGAR&estateid={estate_id}"
            dbg["meta"]["mv_url"] = mv_url
            try:
                r_mv = sess.get(
                    mv_url,
                    headers={
                        **BROWSER_HEADERS,
                        "Accept": "application/pdf,application/octet-stream,*/*",
                        "Referer": page_url,
                        "Origin": "https://www.garanti.no",
                    },
                    timeout=timeout,
                    allow_redirects=True,
                )
                ct = (r_mv.headers.get("Content-Type") or "").lower()
                if r_mv.ok and (("pdf" in ct) or _looks_like_pdf(r_mv.content)):
                    # Hvis dette allerede er en “full” salgsoppgave, great:
                    if _pdf_ok(r_mv.content) and _url_is_allowed(str(r_mv.url)):
                        dbg["step"] = "ok_from_meglervisning_full"
                        return r_mv.content, str(r_mv.url), dbg

                    # Mini-PDF – prøv å finne DS-url i innholdet og last den via browser
                    link = _extract_first_url_from_pdf(r_mv.content or b"")
                    dbg["meta"]["ds_from_mini_pdf"] = link
                    if link and "digitalsalgsoppgave.garanti.no" in link:
                        try:
                            b2, u2, bdbg2 = fetch_pdf_with_browser(link)
                            dbg["browser_from_mini"] = bdbg2
                            if b2 and _pdf_ok(b2) and _url_is_allowed(u2 or link):
                                dbg["step"] = "browser_ok_from_mini"
                                return b2, u2 or link, dbg
                        except Exception as e:
                            dbg["meta"]["browser_from_mini_error"] = str(e)
            except Exception as e:
                dbg["meta"]["mv_error"] = str(e)

        # 5) Siste utvei: la headless-browser navigere fra megler-siden selv,
        # men aksepter KUN PDF-er som matcher allow-listen og ikke treffer blokkerte hint.
        try:
            b3, u3, bdbg3 = fetch_pdf_with_browser(page_url)
            dbg["browser_fallback"] = bdbg3
            if b3 and _pdf_ok(b3) and _url_is_allowed(u3 or ""):
                dbg["step"] = "browser_ok_megler"
                return b3, u3 or page_url, dbg
        except Exception as e:
            dbg["meta"]["browser_fallback_error"] = str(e)

        dbg["step"] = "no_pdf_confirmed"
        return None, None, dbg
