# core/drivers/heimdal.py
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Tuple, Dict, Any, Optional

import requests
from PyPDF2 import PdfReader

from core.http_headers import BROWSER_HEADERS
from ..config import SETTINGS

PDF_MAGIC = b"%PDF-"
MIN_BYTES = 500_000
MIN_PAGES = 4
_G_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

DEBUG_DIR = Path("data/debug")
DEBUG_DIR.mkdir(parents=True, exist_ok=True)
DEBUG_HTML = DEBUG_DIR / "heimdal_last.html"


def _looks_like_pdf(b: bytes | None) -> bool:
    if not b or not isinstance(b, (bytes, bytearray)):
        return False
    if not b.startswith(PDF_MAGIC):
        return False
    try:
        n_pages = len(PdfReader(io.BytesIO(b)).pages)
    except Exception:
        return False
    return (len(b) >= MIN_BYTES) and (n_pages >= MIN_PAGES)


def _trim_headers(h: Dict[str, str]) -> Dict[str, str]:
    """Ta kun nyttige biter for debug."""
    keep = [
        "content-type",
        "content-length",
        "server",
        "cache-control",
        "cf-ray",
        "cf-cache-status",
    ]
    out = {}
    for k, v in (h or {}).items():
        lk = k.lower()
        if lk in keep:
            out[lk] = v
    return out


class HeimdalDriver:
    name = "heimdal"

    def matches(self, url: str) -> bool:
        u = (url or "").lower()
        return "hem.no/" in u

    def _try_get(
        self, sess: requests.Session, url: str, headers: Dict[str, str]
    ) -> tuple[Optional[requests.Response], Dict[str, Any]]:
        meta: Dict[str, Any] = {
            "req_headers": {
                k: headers[k]
                for k in headers
                if k.lower() in ("referer", "origin", "user-agent", "accept-language")
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
            # dump HTML uansett, det hjelper oss ved 403/404
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

        # 1) Hent megler-siden – prøv flere varianter med ulike Referer/Origin
        attempts = [
            # Mest “nettleseraktig”
            {
                "Referer": "https://www.finn.no/",
                "Origin": "https://hem.no",
                "Accept-Language": "nb-NO,nb;q=0.9,en;q=0.8",
            },
            # Samme side som referer
            {
                "Referer": page_url,
                "Origin": "https://hem.no",
                "Accept-Language": "nb-NO,nb;q=0.9,en;q=0.8",
            },
            # Uten Origin (noen WAF-er liker det bedre)
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
            if r is not None:
                html = r.text or ""
                # selv ved 403/404 prøver vi å parse – ofte ligger URLen i DOM likevel
                if html:
                    break

        dbg["meta"]["page_fetch_tries"] = page_meta_all
        if not html:
            dbg["step"] = "page_fetch_error"
            dbg["error"] = "Ingen HTML hentet (se meta.page_fetch_tries)"
            return None, None, dbg

        # 2) Finn meglervisning-lenke direkte i HTML
        m_mv = re.search(
            r'https?://meglervisning\.no/salgsoppgave/hent\?[^"\']+',
            html,
            re.I,
        )
        mv_url: Optional[str] = m_mv.group(0) if m_mv else None
        dbg["meta"]["mv_from_html"] = mv_url

        # 3) Hvis ikke funnet: hent estateId (UUID) og bygg lenken
        if not mv_url:
            m_uuid = re.search(_G_UUID, html, re.I)
            estate_id = m_uuid.group(0) if m_uuid else None
            dbg["meta"]["estate_id_from_html"] = estate_id
            if estate_id:
                mv_url = f"https://meglervisning.no/salgsoppgave/hent?instid=MSHMDL&estateid={estate_id}"

        if not mv_url:
            dbg["step"] = "no_mv_url_in_html"
            return None, None, dbg

        # 4) Last ned PDF fra meglervisning.no (også her: flere varianter)
        pdf_attempts = [
            {
                "Referer": page_url,
                "Origin": "https://hem.no",
                "Accept": "application/pdf,application/octet-stream,*/*",
                "Accept-Language": "nb-NO,nb;q=0.9,en;q=0.8",
            },
            {
                "Referer": "https://www.finn.no/",
                "Origin": "https://hem.no",
                "Accept": "application/pdf,application/octet-stream,*/*",
                "Accept-Language": "nb-NO,nb;q=0.9,en;q=0.8",
            },
            {
                "Referer": page_url,
                "Accept": "application/pdf,application/octet-stream,*/*",
                "Accept-Language": "nb-NO,nb;q=0.9,en;q=0.8",
            },
        ]

        pdf_meta_all: list[Dict[str, Any]] = []
        for i, extra in enumerate(pdf_attempts, 1):
            headers_pdf = {**BROWSER_HEADERS, **extra}
            r = None
            meta_pdf: Dict[str, Any] = {
                "attempt": i,
                "req_headers": {
                    k: headers_pdf[k]
                    for k in headers_pdf
                    if k.lower() in ("referer", "origin", "accept", "accept-language")
                },
            }
            try:
                rp = sess.get(
                    mv_url,
                    headers=headers_pdf,
                    timeout=SETTINGS.REQ_TIMEOUT,
                    allow_redirects=True,
                )
                meta_pdf["status"] = rp.status_code
                meta_pdf["final_url"] = str(rp.url)
                meta_pdf["resp_headers"] = _trim_headers(dict(rp.headers))
                try:
                    meta_pdf["cookies"] = rp.cookies.get_dict()
                except Exception:
                    pass

                ct = (rp.headers.get("Content-Type") or "").lower()
                meta_pdf["content_type"] = ct
                meta_pdf["len"] = len(rp.content or b"")
                pdf_meta_all.append(meta_pdf)

                if rp.ok and (("pdf" in ct) or (rp.content[:4] == PDF_MAGIC)):
                    if _looks_like_pdf(rp.content):
                        dbg["meta"]["pdf_fetch_tries"] = pdf_meta_all
                        dbg["step"] = "ok_from_meglervisning"
                        return rp.content, str(rp.url), dbg
                    else:
                        # Fortsett – kanskje neste forsøk gir «full» PDF
                        continue
            except Exception as e:
                meta_pdf["exception"] = f"{type(e).__name__}: {e}"
                pdf_meta_all.append(meta_pdf)
                continue

        dbg["meta"]["pdf_fetch_tries"] = pdf_meta_all
        dbg["step"] = "pdf_fetch_failed_or_small"
        return None, None, dbg
