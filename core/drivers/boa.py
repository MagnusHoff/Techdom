# core/drivers/boa.py
from __future__ import annotations

import re
import time
from typing import Dict, Any, Tuple, List
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup, Tag

from core.http_headers import BROWSER_HEADERS
from ..config import SETTINGS

PDF_MAGIC = b"%PDF-"


def _looks_like_pdf(b: bytes) -> bool:
    return isinstance(b, (bytes, bytearray)) and b.startswith(PDF_MAGIC)


def _origin(u: str) -> str:
    try:
        p = urlparse(u)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""


def _abs(base: str, href: str | None) -> str | None:
    if not href:
        return None
    return urljoin(base, href)


def _get(
    sess: requests.Session, url: str, referer: str, timeout: int
) -> requests.Response:
    headers = dict(BROWSER_HEADERS)
    headers.update(
        {
            "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
            "Referer": referer,
            "Origin": _origin(referer) or _origin(url),
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-site",
        }
    )
    return sess.get(url, headers=headers, timeout=timeout, allow_redirects=True)


def _head(
    sess: requests.Session, url: str, referer: str, timeout: int
) -> requests.Response:
    headers = dict(BROWSER_HEADERS)
    headers.update(
        {
            "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
            "Referer": referer,
            "Origin": _origin(referer) or _origin(url),
        }
    )
    return sess.head(url, headers=headers, timeout=timeout, allow_redirects=True)


def _gather_pdf_candidates(soup: BeautifulSoup, base_url: str) -> List[str]:
    urls: List[str] = []

    # <a> med tekst/href
    for a in soup.find_all("a"):
        if not isinstance(a, Tag):
            continue
        txt = (a.get_text(" ", strip=True) or "").lower()
        href = (a.get("href") or a.get("data-href") or a.get("download") or "").strip()
        if not href:
            continue
        u = _abs(base_url, href)
        if not u:
            continue
        lo = f"{txt} {u.lower()}"
        if u.lower().endswith(".pdf") or any(
            k in lo
            for k in (
                "tilstandsrapport",
                "salgsoppgav",
                "prospekt",
                "dokument",
                "vedlegg",
            )
        ):
            urls.append(u)

    # Regex i rå HTML (fanger også script/JSON)
    try:
        html = soup.decode()
    except Exception:
        html = ""
    for m in re.finditer(r'https?://[^\s"\']+\.pdf(?:\?[^\s<>\'"]*)?', html, re.I):
        urls.append(m.group(0))

    # uniq
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            out.append(u)
            seen.add(u)
    return out


def _score(u: str) -> int:
    s = (u or "").lower()
    sc = 0
    if s.endswith(".pdf"):
        sc += 30
    if "tilstandsrapport" in s:
        sc += 40
    if "salgsoppgav" in s or "prospekt" in s:
        sc += 25
    if "dokument" in s or "vedlegg" in s:
        sc += 10
    return sc


class BoaDriver:
    name = "boa"

    def matches(self, url: str) -> bool:
        u = (url or "").lower()
        # boaeiendom.no og ev. andre subdomener
        return "boaeiendom.no" in u

    def try_fetch(
        self, sess: requests.Session, page_url: str
    ) -> Tuple[bytes | None, str | None, dict]:
        dbg: Dict[str, Any] = {"driver": self.name, "step": "start", "driver_meta": {}}

        # Hent megler-siden
        try:
            r = _get(sess, page_url, page_url, SETTINGS.REQ_TIMEOUT)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            dbg["step"] = f"page_fetch_error:{type(e).__name__}"
            return None, None, dbg

        # Finn PDF-kandidater (Dokumenter-seksjonen inneholder direkte lenker)
        cands = _gather_pdf_candidates(soup, page_url)
        if not cands:
            dbg["step"] = "no_candidates"
            return None, None, dbg

        cands.sort(key=_score, reverse=True)

        # HEAD→GET med små retries
        backoff, max_tries = 0.5, 2
        for url in cands:
            try:
                h = _head(sess, url, page_url, SETTINGS.REQ_TIMEOUT)
                final = str(h.url)
                ct = (h.headers.get("Content-Type") or "").lower()
                if h.ok and (
                    ct.startswith("application/pdf") or final.lower().endswith(".pdf")
                ):
                    for attempt in range(1, max_tries + 1):
                        t0 = time.monotonic()
                        rr = _get(sess, final, page_url, SETTINGS.REQ_TIMEOUT)
                        elapsed_ms = int((time.monotonic() - t0) * 1000)
                        ct2 = (rr.headers.get("Content-Type") or "").lower()
                        ok_pdf = rr.ok and (
                            ("application/pdf" in ct2) or _looks_like_pdf(rr.content)
                        )
                        dbg["driver_meta"][f"get_{attempt}_{final}"] = {
                            "status": rr.status_code,
                            "content_type": rr.headers.get("Content-Type"),
                            "content_length": rr.headers.get("Content-Length"),
                            "elapsed_ms": elapsed_ms,
                            "final_url": str(rr.url),
                            "bytes": len(rr.content) if rr.content else 0,
                        }
                        if ok_pdf:
                            dbg["step"] = "ok_direct"
                            return rr.content, final, dbg
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

            # fallback rett GET
            for attempt in range(1, max_tries + 1):
                try:
                    t0 = time.monotonic()
                    rr = _get(sess, url, page_url, SETTINGS.REQ_TIMEOUT)
                    elapsed_ms = int((time.monotonic() - t0) * 1000)
                    ct2 = (rr.headers.get("Content-Type") or "").lower()
                    ok_pdf = rr.ok and (
                        ("application/pdf" in ct2) or _looks_like_pdf(rr.content)
                    )
                    dbg["driver_meta"][f"get_{attempt}_{url}"] = {
                        "status": rr.status_code,
                        "content_type": rr.headers.get("Content-Type"),
                        "content_length": rr.headers.get("Content-Length"),
                        "elapsed_ms": elapsed_ms,
                        "final_url": str(rr.url),
                        "bytes": len(rr.content) if rr.content else 0,
                    }
                    if ok_pdf:
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
                except requests.RequestException:
                    if attempt < max_tries:
                        time.sleep(backoff * attempt)
                        continue
                    break

        dbg["step"] = "no_pdf_confirmed"
        return None, None, dbg
