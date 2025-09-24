# drivers/generic_local.py
from __future__ import annotations

import re
import time
from typing import Dict, Tuple, List
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

from core.http_headers import BROWSER_HEADERS
from ..config import SETTINGS

PDF_MAGIC = b"%PDF-"


def _looks_like_pdf(b: bytes) -> bool:
    return isinstance(b, (bytes, bytearray)) and b.startswith(PDF_MAGIC)


def _origin_of(u: str) -> str:
    try:
        p = urlparse(u)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""


def _abs(base_url: str, href: str | None) -> str | None:
    if not href:
        return None
    return urljoin(base_url, href)


def _get(
    sess: requests.Session, url: str, referer: str, timeout: int
) -> requests.Response:
    headers = dict(BROWSER_HEADERS)
    headers.update(
        {
            "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
            "Referer": referer,
            "Origin": _origin_of(referer) or _origin_of(url),
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "cross-site",
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
            "Origin": _origin_of(referer) or _origin_of(url),
        }
    )
    return sess.head(url, headers=headers, timeout=timeout, allow_redirects=True)


def _gather_pdf_candidates(soup: BeautifulSoup, base_url: str) -> List[str]:
    urls: List[str] = []

    # 1) A-tagger
    for a in soup.find_all("a"):
        if not isinstance(a, Tag):
            continue
        txt = (a.get_text(" ", strip=True) or "").lower()
        href = (a.get("href") or a.get("data-href") or a.get("download") or "").strip()
        if not href:
            continue
        absu = _abs(base_url, href)
        if not absu:
            continue
        lo = txt + " " + absu.lower()
        if absu.lower().endswith(".pdf") or any(
            k in lo for k in ("salgsoppgav", "prospekt", "vedlegg", "pdf")
        ):
            urls.append(absu)

    # 2) andre elementer med data-* lenker
    for el in soup.find_all(["button", "div", "span"]):
        if not isinstance(el, Tag):
            continue
        txt = (el.get_text(" ", strip=True) or "").lower()
        for attr in ("data-href", "data-file", "data-url", "data-download"):
            href = (el.get(attr) or "").strip()
            if not href:
                continue
            absu = _abs(base_url, href)
            if not absu:
                continue
            lo = txt + " " + absu.lower()
            if absu.lower().endswith(".pdf") or any(
                k in lo for k in ("salgsoppgav", "prospekt", "vedlegg", "pdf")
            ):
                urls.append(absu)

    # 3) Regex i rå HTML (fanger inline JSON)
    try:
        html = soup.decode()
    except Exception:
        html = ""
    for m in re.finditer(r'https?://[^\s"\'<>]+\.pdf(?:\?[^\s<>\'"]*)?', html, re.I):
        urls.append(m.group(0))

    # uniq
    seen: set[str] = set()
    out: List[str] = []
    for u in urls:
        if u not in seen:
            out.append(u)
            seen.add(u)
    return out


def _score_candidate(url: str) -> int:
    s = url.lower()
    sc = 0
    if s.endswith(".pdf"):
        sc += 30
    if "salgsoppgav" in s or "prospekt" in s:
        sc += 25
    if "vedlegg" in s or "dokument" in s:
        sc += 5
    # straff åpenbart “feil” ting
    base = s.rsplit("/", 1)[-1]
    if base in ("klikk.pdf",):
        sc -= 200
    return sc


class GenericLocalDriver:
    """
    Fallback-Driver: Prøver å finne PDF-er på hvilken som helst megler-side.
    matches() returnerer alltid True – derfor MÅ denne stå SIST i DRIVERS-listen.
    """

    name = "generic_local"

    def matches(self, url: str) -> bool:
        return True  # alltid true (fallback)

    def try_fetch(
        self, sess: requests.Session, page_url: str
    ) -> Tuple[bytes | None, str | None, dict]:
        dbg: Dict[str, object] = {
            "driver": self.name,
            "step": "start",
            "driver_meta": {},
        }

        try:
            r0 = _get(sess, page_url, page_url, SETTINGS.REQ_TIMEOUT)
            r0.raise_for_status()
            soup = BeautifulSoup(r0.text, "html.parser")
        except Exception as e:
            dbg["step"] = f"page_fetch_error:{type(e).__name__}"
            return None, None, dbg

        candidates = _gather_pdf_candidates(soup, page_url)
        if not candidates:
            dbg["step"] = "no_candidates"
            return None, None, dbg

        # Prioriter “salgsoppgave/prospekt” først, deretter andre .pdf
        candidates.sort(key=_score_candidate, reverse=True)

        backoff = 0.6
        max_tries = 2

        for url in candidates:
            # HEAD-verifisering (hurtig)
            try:
                h = _head(sess, url, page_url, SETTINGS.REQ_TIMEOUT)
                ct = (h.headers.get("Content-Type") or "").lower()
                final = str(h.url)
                if h.ok and (
                    ct.startswith("application/pdf") or final.lower().endswith(".pdf")
                ):
                    # GET for å hente bytes
                    for attempt in range(1, max_tries + 1):
                        t0 = time.monotonic()
                        rr = _get(sess, final, page_url, SETTINGS.REQ_TIMEOUT)
                        ct2 = (rr.headers.get("Content-Type") or "").lower()
                        ok_pdf = rr.ok and (
                            ("application/pdf" in ct2) or _looks_like_pdf(rr.content)
                        )
                        dbg["driver_meta"][f"get_{attempt}_{final}"] = {
                            "status": rr.status_code,
                            "content_type": rr.headers.get("Content-Type"),
                            "content_length": rr.headers.get("Content-Length"),
                            "elapsed_ms": int((time.monotonic() - t0) * 1000),
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

            # Fallback: direkte GET uten HEAD
            for attempt in range(1, max_tries + 1):
                try:
                    t0 = time.monotonic()
                    rr = _get(sess, url, page_url, SETTINGS.REQ_TIMEOUT)
                    ct2 = (rr.headers.get("Content-Type") or "").lower()
                    ok_pdf = rr.ok and (
                        ("application/pdf" in ct2) or _looks_like_pdf(rr.content)
                    )
                    dbg["driver_meta"][f"get_{attempt}_{url}"] = {
                        "status": rr.status_code,
                        "content_type": rr.headers.get("Content-Type"),
                        "content_length": rr.headers.get("Content-Length"),
                        "elapsed_ms": int((time.monotonic() - t0) * 1000),
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
