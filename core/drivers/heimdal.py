# core/drivers/heimdal.py
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Tuple, Dict, Any, Optional

import requests
from PyPDF2 import PdfReader

from .base import Driver
from core.http_headers import BROWSER_HEADERS
from ..config import SETTINGS

PDF_MAGIC = b"%PDF-"
MIN_BYTES = 500_000  # Heimdal-prospekter kan være moderat store
MIN_PAGES = 4
_G_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

# Kun dette mønsteret er lov (salgsoppgave/prospekt):
MV_ALLOWED_RX = re.compile(
    r"^https?://meglervisning\.no/salgsoppgave/hent\?[^\"']*\b(instid=MSHMDL|estateid=)[^\"']+",
    re.I,
)

# En enkel debug-dump, nyttig ved feilsøking
DEBUG_DIR = Path("data/debug")
DEBUG_DIR.mkdir(parents=True, exist_ok=True)
DEBUG_HTML = DEBUG_DIR / "heimdal_last.html"


def _looks_like_pdf(b: bytes | None) -> bool:
    if not b or not isinstance(b, (bytes, bytearray)) or not b.startswith(PDF_MAGIC):
        return False
    try:
        n_pages = len(PdfReader(io.BytesIO(b)).pages)
    except Exception:
        return False
    return (len(b) >= MIN_BYTES) and (n_pages >= MIN_PAGES)


def _trim_headers(h: Dict[str, str] | None) -> Dict[str, str]:
    if not h:
        return {}
    keep = {
        "content-type",
        "content-length",
        "server",
        "cache-control",
        "cf-ray",
        "cf-cache-status",
    }
    out: Dict[str, str] = {}
    for k, v in h.items():
        lk = k.lower()
        if lk in keep:
            out[lk] = v
    return out


class HeimdalDriver(Driver):
    name = "heimdal"

    def matches(self, url: str) -> bool:
        return "hem.no/" in (url or "").lower()

    def _try_get(
        self, sess: requests.Session, url: str, headers: Dict[str, str]
    ) -> tuple[Optional[requests.Response], Dict[str, Any]]:
        meta: Dict[str, Any] = {
            "req_headers": {
                k: headers[k]
                for k in headers
                if k.lower()
                in ("referer", "origin", "user-agent", "accept", "accept-language")
            }
        }
        try:
            r = sess.get(
                url, headers=headers, timeout=SETTINGS.REQ_TIMEOUT, allow_redirects=True
            )
            meta["status"] = r.status_code
            meta["final_url"] = str(r.url)
            meta["resp_headers"] = _trim_headers(dict(r.headers))
            try:
                meta["cookies"] = r.cookies.get_dict()
            except Exception:
                pass
            try:
                DEBUG_HTML.write_text(r.text or "", encoding="utf-8", errors="ignore")
                meta["html_dump"] = str(DEBUG_HTML)
                meta["html_len"] = len(r.text or "")
            except Exception:
                pass
            return r, meta
        except Exception as e:
            meta["exception"] = f"{type(e).__name__}: {e}"
            return None, meta

    def try_fetch(
        self, sess: requests.Session, page_url: str
    ) -> Tuple[bytes | None, str | None, dict]:
        dbg: Dict[str, Any] = {"driver": self.name, "step": "start", "meta": {}}

        # 1) Hent megler-siden (kun for å finne salgsoppgave-URL)
        attempts = [
            {
                "Referer": "https://www.finn.no/",
                "Origin": "https://hem.no",
                "Accept-Language": "nb-NO,nb;q=0.9,en;q=0.8",
            },
            {
                "Referer": page_url,
                "Origin": "https://hem.no",
                "Accept-Language": "nb-NO,nb;q=0.9,en;q=0.8",
            },
            {
                "Referer": page_url,
                "Accept-Language": "nb-NO,nb;q=0.9,en;q=0.8",
            },
        ]

        html = ""
        page_meta_all: list[Dict[str, Any]] = []
        for i, extra in enumerate(attempts, 1):
            headers = {**BROWSER_HEADERS, **extra}
            r, meta = self._try_get(sess, page_url, headers)
            meta["attempt"] = i
            page_meta_all.append(meta)
            if r is not None and (r.text or ""):
                html = r.text or ""
                break

        dbg["meta"]["page_fetch_tries"] = page_meta_all
        if not html:
            dbg["step"] = "page_fetch_error"
            dbg["error"] = "Ingen HTML hentet (se meta.page_fetch_tries)"
            return None, None, dbg

        # 2) Finn eksplisitt meglervisning-salgsoppgave i HTML
        mv_url: Optional[str] = None
        m_mv = re.search(
            r'https?://meglervisning\.no/salgsoppgave/hent\?[^"\']+', html, re.I
        )
        if m_mv and MV_ALLOWED_RX.search(m_mv.group(0)):
            mv_url = m_mv.group(0)

        # 3) Evt. bygg URL fra estateId (kun dette endepunktet er lov)
        if not mv_url:
            m_uuid = re.search(_G_UUID, html, re.I)
            estate_id = m_uuid.group(0) if m_uuid else None
            dbg["meta"]["estate_id_from_html"] = estate_id
            if estate_id:
                mv_url = (
                    f"https://meglervisning.no/salgsoppgave/hent"
                    f"?instid=MSHMDL&estateid={estate_id}"
                )

        if not (mv_url and MV_ALLOWED_RX.search(mv_url)):
            dbg["step"] = "no_mv_url_in_html"
            return None, None, dbg

        dbg["meta"]["mv_url"] = mv_url

        # 4) Last ned KUN salgsoppgaven fra Meglervisning
        pdf_headers = {
            **BROWSER_HEADERS,
            "Referer": page_url,
            "Origin": "https://hem.no",
            "Accept": "application/pdf,application/octet-stream,*/*",
            "Accept-Language": "nb-NO,nb;q=0.9,en;q=0.8",
        }

        try:
            rp = sess.get(
                mv_url,
                headers=pdf_headers,
                timeout=SETTINGS.REQ_TIMEOUT,
                allow_redirects=True,
            )
            dbg["meta"]["pdf_status"] = rp.status_code
            dbg["meta"]["pdf_final_url"] = str(rp.url)
            dbg["meta"]["pdf_headers"] = _trim_headers(dict(rp.headers))
            if rp.ok and _looks_like_pdf(rp.content):
                dbg["step"] = "ok_from_meglervisning"
                return rp.content, str(rp.url), dbg
        except Exception as e:
            dbg["meta"]["pdf_exception"] = f"{type(e).__name__}: {e}"

        dbg["step"] = "pdf_fetch_failed_or_small"
        return None, None, dbg
