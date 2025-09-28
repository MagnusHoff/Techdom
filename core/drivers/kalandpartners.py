# core/drivers/kalandpartners.py
from __future__ import annotations

import io
import re
import time
from typing import Tuple, Dict, Any, Optional, List, Mapping
from urllib.parse import urljoin, urlparse

import requests
from PyPDF2 import PdfReader

from .base import Driver
from core.http_headers import BROWSER_HEADERS
from ..config import SETTINGS

PDF_MAGIC = b"%PDF-"

# --- kun salgsoppgave/prospekt ---
ALLOW_RX = re.compile(r"(salgsoppgav|prospekt|komplett|utskriftsvennlig)", re.I)
BLOCK_RX = re.compile(
    r"(tilstandsrapport|boligsalgsrapport|ns[\s_-]*3600|energiattest|egenerkl|"
    r"nabolag|takst|boligselgerforsikring|bud|budskjema|vedtekter|"
    r"arsberetning|årsberetning|regnskap|sameie|kjopetilbud|kontrakt)",
    re.I,
)

MIN_BYTES = 150_000
MIN_PAGES = 4


def _looks_like_pdf(b: bytes | None) -> bool:
    return isinstance(b, (bytes, bytearray)) and b.startswith(PDF_MAGIC)


def _pdf_quality_ok(b: bytes | None) -> bool:
    if not b or not _looks_like_pdf(b) or len(b) < MIN_BYTES:
        return False
    try:
        return len(PdfReader(io.BytesIO(b)).pages) >= MIN_PAGES
    except Exception:
        # hvis vi ikke får lest sider, stol i det minste på header+størrelse
        return len(b) >= max(MIN_BYTES * 2, 300_000)


def _content_filename(headers: Mapping[str, str] | None) -> str:
    if not headers:
        return ""
    cd = headers.get("Content-Disposition") or headers.get("content-disposition") or ""
    m = re.search(r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';]+)', cd)
    return (m.group(1) if m else "").strip()


def _is_salgsoppgave(url: str, headers: Mapping[str, str] | None) -> bool:
    lo = (url or "").lower()
    fn = (_content_filename(headers) or "").lower()
    hay = f"{lo} {fn}"

    if BLOCK_RX.search(hay):
        return False
    return bool(ALLOW_RX.search(hay))


def _head(
    sess: requests.Session, url: str, referer: str, timeout: int
) -> requests.Response:
    headers = dict(BROWSER_HEADERS)
    headers.update(
        {
            "Accept": "application/pdf,application/octet-stream,*/*;q=0.8",
            "Referer": referer,
            "Origin": "https://partners.no",
        }
    )
    return sess.head(url, headers=headers, timeout=timeout, allow_redirects=True)


def _get(
    sess: requests.Session, url: str, referer: str, timeout: int
) -> requests.Response:
    headers = dict(BROWSER_HEADERS)
    headers.update(
        {
            "Accept": "application/pdf,application/octet-stream,*/*;q=0.8",
            "Referer": referer,
            "Origin": "https://partners.no",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
        }
    )
    return sess.get(url, headers=headers, timeout=timeout, allow_redirects=True)


def _gather_candidates(html: str, base_url: str) -> List[str]:
    """
    Finn nedlastings-URLer hos Partners (wngetfile.ashx).
    """
    cands: List[str] = []

    for m in re.finditer(r'https?://[^"\']+?wngetfile\.ashx[^"\']*', html, re.I):
        cands.append(m.group(0))

    for m in re.finditer(r'["\'](/[^"\']*?wngetfile\.ashx[^"\']*)["\']', html, re.I):
        cands.append(urljoin(base_url, m.group(1)))

    # uniq
    seen: set[str] = set()
    out: List[str] = []
    for u in cands:
        if u not in seen:
            out.append(u)
            seen.add(u)
    return out


class KalandPartnersDriver(Driver):
    name = "kalandpartners"

    def matches(self, url: str) -> bool:
        return "partners.no/eiendom/" in (url or "").lower()

    def try_fetch(
        self, sess: requests.Session, page_url: str
    ) -> Tuple[bytes | None, str | None, dict]:
        dbg: Dict[str, Any] = {"driver": self.name, "step": "start", "meta": {}}
        timeout = SETTINGS.REQ_TIMEOUT

        # 1) Hent objektside
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
            dbg["error"] = repr(e)
            return None, None, dbg

        # 2) wngetfile-kandidater
        cands = _gather_candidates(html, page_url)
        if not cands:
            dbg["step"] = "no_pdf_found"
            return None, None, dbg

        # 3) Prøv kandidatene, men KUN dersom headere/URL ser ut som salgsoppgave
        backoff, max_tries = 0.5, 2
        for url in cands:
            # HEAD
            try:
                h = _head(sess, url, page_url, timeout)
                ct = (h.headers.get("Content-Type") or "").lower()
                final = str(h.url)
                dbg.setdefault("meta", {})
                dbg["meta"][f"head_{url}"] = {
                    "status": h.status_code,
                    "content_type": h.headers.get("Content-Type"),
                    "final_url": final,
                    "cd_filename": _content_filename(h.headers),
                }

                # Filter: må se ut som salgsoppgave; blokker TR/annet
                if not _is_salgsoppgave(final, h.headers):
                    continue

                if h.ok and ("application/pdf" in ct or final.lower().endswith(".pdf")):
                    for attempt in range(1, max_tries + 1):
                        rr = _get(sess, final, page_url, timeout)
                        dbg["meta"][f"get_{attempt}_{final}"] = {
                            "status": rr.status_code,
                            "content_type": rr.headers.get("Content-Type"),
                            "content_length": rr.headers.get("Content-Length"),
                            "final_url": str(rr.url),
                            "bytes": len(rr.content or b""),
                            "cd_filename": _content_filename(rr.headers),
                        }
                        # Sikkerhet: sjekk igjen mot GET-responsen (Content-Disposition)
                        if not _is_salgsoppgave(str(rr.url), rr.headers):
                            # Ikke salgsoppgave – hopp videre
                            if attempt < max_tries and rr.status_code in (
                                429,
                                500,
                                502,
                                503,
                                504,
                            ):
                                time.sleep(backoff * attempt)
                                continue
                            break

                        if rr.ok and _pdf_quality_ok(rr.content):
                            dbg["step"] = "ok_direct"
                            return rr.content, str(rr.url), dbg

                        if attempt < max_tries and rr.status_code in (
                            429,
                            500,
                            502,
                            503,
                            504,
                        ):
                            time.sleep(backoff * attempt)
                            continue
                        break
            except Exception:
                pass

            # Direkte GET (noen ganger er HEAD blokkert), men fortsatt med filter
            for attempt in range(1, max_tries + 1):
                try:
                    rr = _get(sess, url, page_url, timeout)
                    if not _is_salgsoppgave(str(rr.url), rr.headers):
                        if attempt < max_tries and rr.status_code in (
                            429,
                            500,
                            502,
                            503,
                            504,
                        ):
                            time.sleep(backoff * attempt)
                            continue
                        break

                    dbg["meta"][f"get_{attempt}_{url}"] = {
                        "status": rr.status_code,
                        "content_type": rr.headers.get("Content-Type"),
                        "content_length": rr.headers.get("Content-Length"),
                        "final_url": str(rr.url),
                        "bytes": len(rr.content or b""),
                        "cd_filename": _content_filename(rr.headers),
                    }
                    if rr.ok and _pdf_quality_ok(rr.content):
                        dbg["step"] = "ok_direct_no_head"
                        return rr.content, str(rr.url), dbg

                    if attempt < max_tries and rr.status_code in (
                        429,
                        500,
                        502,
                        503,
                        504,
                    ):
                        time.sleep(backoff * attempt)
                        continue
                    break
                except Exception:
                    if attempt < max_tries:
                        time.sleep(backoff * attempt)
                        continue
                    break

        dbg["step"] = "no_pdf_confirmed"
        return None, None, dbg
