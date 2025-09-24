# core/drivers/garanti.py
from __future__ import annotations

import io, re, json
from typing import Tuple, Dict, Any, Optional
import requests
from PyPDF2 import PdfReader

from core.http_headers import BROWSER_HEADERS
from core.browser_fetch import fetch_pdf_with_browser
from core.finn_discovery import discover_megler_url
from core.sessions import new_session
from ..config import SETTINGS

PDF_MAGIC = b"%PDF-"
MIN_REAL_BYTES = 2_000_000
MIN_REAL_PAGES = 8

_G_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

NEG_PATTERNS = (
    "GARANTI_10EnkleTips.pdf",
    "/files/doc/",
    "boligkjøperforsikring",
    "anticimex",
    "nabolagsprofil",
    "prisliste",
)


def _looks_like_pdf(b: bytes | None) -> bool:
    return isinstance(b, (bytes, bytearray)) and b[:4] == PDF_MAGIC


def _pdf_ok(b: bytes | None) -> bool:
    if not b or len(b) < MIN_REAL_BYTES:
        return False
    try:
        return len(PdfReader(io.BytesIO(b)).pages) >= MIN_REAL_PAGES
    except Exception:
        return False


def _extract_first_url_from_pdf(b: bytes) -> Optional[str]:
    try:
        rdr = PdfReader(io.BytesIO(b))
        txt = []
        for p in rdr.pages[:3]:
            try:
                t = p.extract_text() or ""
            except Exception:
                t = ""
            if t:
                txt.append(t)
        m = re.search(r"https?://[^\s)>\]]+", "\n".join(txt))
        return m.group(0) if m else None
    except Exception:
        return None


def _find_estateid_in_text(txt: str) -> Optional[str]:
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


class GarantiDriver:
    name = "garanti"

    def matches(self, url: str) -> bool:
        return "garanti.no/eiendom/" in (url or "").lower()

    def try_fetch(
        self, sess: requests.Session, page_url: str
    ) -> Tuple[bytes | None, str | None, dict]:
        dbg: Dict[str, Any] = {"driver": self.name, "step": "start", "meta": {}}

        # 1) Hent megler-HTML
        try:
            r = sess.get(
                page_url,
                headers=BROWSER_HEADERS,
                timeout=SETTINGS.REQ_TIMEOUT,
                allow_redirects=True,
            )
            r.raise_for_status()
            html = r.text or ""
            dbg["meta"]["page_status"] = r.status_code
            dbg["meta"]["page_len"] = len(html)
        except Exception as e:
            dbg["step"] = f"page_fetch_error:{type(e).__name__}"
            dbg["error"] = repr(e)
            return None, None, dbg

        # 2) Se om vi allerede finner DS-link i HTML
        m_ds = re.search(
            r'https?://digitalsalgsoppgave\.garanti\.no/[^\s"\']+', html, re.I
        )
        if m_ds:
            ds_url = m_ds.group(0)
            dbg["meta"]["ds_from_html"] = ds_url
            try:
                b, u, bdbg = fetch_pdf_with_browser(ds_url)
                dbg["browser"] = bdbg
                if b and _pdf_ok(b):
                    dbg["step"] = "browser_ok_ds_from_html"
                    return b, u or ds_url, dbg
            except Exception as e:
                dbg["meta"]["ds_browser_error"] = repr(e)

        # 3) Grep estateId. Hvis mangler i megler-HTML, prøv FINN-siden
        estate_id = _find_estateid_in_text(html) or None
        dbg["meta"]["estate_id_from_megler"] = estate_id

        if not estate_id:
            # Finn FINN-url (dersom vi kom hit via fetch_prospectus_from_megler_url)
            try:
                # Prøv å gjette FINN via canonical eller budlenker i html:
                # (fallback – ikke kritisk, det funker også uten)
                pass
            except Exception:
                pass

        # 4) Hvis vi har estateId → hent mini-PDF fra meglervisning.no
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
                    timeout=SETTINGS.REQ_TIMEOUT,
                    allow_redirects=True,
                )
                ct = (r_mv.headers.get("Content-Type") or "").lower()
                if r_mv.ok and (
                    ("pdf" in ct) or (r_mv.content and r_mv.content[:4] == b"%PDF")
                ):
                    if _pdf_ok(r_mv.content):
                        dbg["step"] = "ok_from_meglervisning_full"
                        return r_mv.content, str(r_mv.url), dbg
                    # mini-PDF: trekk ut DS-url fra tekst
                    link = _extract_first_url_from_pdf(r_mv.content)
                    dbg["meta"]["ds_from_mini_pdf"] = link
                    if link and "digitalsalgsoppgave.garanti.no" in link:
                        try:
                            b2, u2, bdbg2 = fetch_pdf_with_browser(link)
                            dbg["browser_from_mini"] = bdbg2
                            if b2 and _pdf_ok(b2):
                                dbg["step"] = "browser_ok_from_mini"
                                return b2, u2 or link, dbg
                        except Exception as e:
                            dbg["meta"]["browser_from_mini_error"] = repr(e)
            except Exception as e:
                dbg["meta"]["mv_error"] = repr(e)

        # 5) Siste: la browser forsøke direkte megler-side (kan navigere selv).
        try:
            b3, u3, bdbg3 = fetch_pdf_with_browser(page_url)
            dbg["browser_fallback"] = bdbg3
            if b3 and _pdf_ok(b3):
                dbg["step"] = "browser_ok_megler"
                return b3, u3 or page_url, dbg
        except Exception as e:
            dbg["meta"]["browser_fallback_error"] = repr(e)

        dbg["step"] = "no_pdf_confirmed"
        return None, None, dbg
