# core/drivers/ask.py
from __future__ import annotations

import re
import time
from typing import Dict, Any, Tuple, List, Optional
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup, Tag

from .base import Driver
from techdom.ingestion.http_headers import BROWSER_HEADERS
from techdom.infrastructure.config import SETTINGS

PDF_MAGIC = b"%PDF-"
MAX_PDF_BYTES = 120_000_000  # ~114 MB – unngå å laste ned gigastore filer

POSITIVE_SIGNS = (
    "salgsoppgav",
    "prospekt",
    "digital",
    "utskrifts",
    "komplett",
    "download",
    "last ned",
)

NEGATIVE_SIGNS = (
    "tilstands",
    "boligsalgs",
    "energiattest",
    "nabolag",
    "nabolagsprofil",
    "egenerkl",
    "budskjema",
    "meglerpakke",
    "forsikring",
)


def _as_str(v: object) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, (list, tuple)) and v and isinstance(v[0], str):
        return v[0]
    return ""


def _looks_like_pdf(b: bytes | None) -> bool:
    return isinstance(b, (bytes, bytearray)) and b.startswith(PDF_MAGIC)


def _origin(u: str) -> str:
    try:
        p = urlparse(u)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""


def _abs(base_url: str, href: str | None) -> Optional[str]:
    if not href:
        return None
    try:
        return urljoin(base_url, href)
    except Exception:
        return None


def _head(sess: requests.Session, url: str, referer: str, timeout: int) -> requests.Response:
    headers = dict(BROWSER_HEADERS)
    headers.update(
        {
            "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
            "Referer": referer,
            "Origin": _origin(referer) or _origin(url),
        }
    )
    return sess.head(url, headers=headers, timeout=timeout, allow_redirects=True)


def _get(sess: requests.Session, url: str, referer: str, timeout: int) -> requests.Response:
    headers = dict(BROWSER_HEADERS)
    headers.update(
        {
            "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
            "Referer": referer,
            "Origin": _origin(referer) or _origin(url),
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "cross-site",
        }
    )
    return sess.get(url, headers=headers, timeout=timeout, allow_redirects=True)


def _is_candidate(label: str, url: str) -> bool:
    hay = f"{label or ''} {url or ''}".lower()
    if not url.lower().endswith(".pdf"):
        return False
    if any(bad in hay for bad in NEGATIVE_SIGNS):
        return False
    return any(pos in hay for pos in POSITIVE_SIGNS) or urlparse(url).netloc.endswith(
        "outgoing.webtopsolutions.com"
    )


def _gather_candidates(html: str, soup: BeautifulSoup, base_url: str) -> List[str]:
    urls: List[str] = []

    for a in soup.find_all("a"):
        if not isinstance(a, Tag):
            continue
        txt = a.get_text(" ", strip=True) or ""
        href = _abs(base_url, _as_str(a.get("href")))
        if href and _is_candidate(txt, href):
            urls.append(href)

    for el in soup.find_all(["button", "div", "span"]):
        if not isinstance(el, Tag):
            continue
        txt = el.get_text(" ", strip=True) or ""
        for attr in ("data-href", "data-url", "data-file", "data-download"):
            href = _abs(base_url, _as_str(el.get(attr)))
            if href and _is_candidate(txt, href):
                urls.append(href)

    for match in re.finditer(r"https?://[^\s\'\"<>]+\.pdf(?:\?[^\s\'\"<>]*)?", html, re.I):
        url = match.group(0).replace("\\/", "/")
        if _is_candidate("", url):
            urls.append(url)

    seen: set[str] = set()
    unique: List[str] = []
    for url in urls:
        if url not in seen:
            unique.append(url)
            seen.add(url)
    return unique


def _score(url: str) -> int:
    s = url.lower()
    score = 0
    if "outgoing.webtopsolutions.com" in s:
        score += 40
    if s.endswith(".pdf"):
        score += 20
    if "hires" in s:
        score -= 5
    if "salgsopp" in s:
        score += 30
    if "prospekt" in s:
        score += 25
    return score


class AskDriver(Driver):
    name = "ask"

    def matches(self, url: str) -> bool:
        host = (url or "").lower()
        return any(
            marker in host
            for marker in (
                "askeiendomsmegling.no",
                "ask.webtopsolutions.com",
                "outgoing.webtopsolutions.com",
            )
        )

    def try_fetch(
        self, sess: requests.Session, page_url: str
    ) -> Tuple[bytes | None, str | None, Dict[str, Any]]:
        dbg: Dict[str, Any] = {"driver": self.name, "step": "start", "driver_meta": {}}

        lo = (page_url or "").lower()
        if lo.endswith(".pdf"):
            try:
                head = _head(sess, page_url, page_url, SETTINGS.REQ_TIMEOUT)
                meta = {
                    "status": head.status_code,
                    "content_type": head.headers.get("Content-Type"),
                    "content_length": head.headers.get("Content-Length"),
                    "final_url": str(head.url),
                }
                dbg["driver_meta"]["head_initial"] = meta
                size_hint = head.headers.get("Content-Length")
                if size_hint and size_hint.isdigit():
                    estimate = int(size_hint)
                    if estimate > MAX_PDF_BYTES:
                        dbg["step"] = "pdf_too_large"
                        dbg["driver_meta"]["size_hint"] = estimate
                        return None, None, dbg
            except Exception as exc:
                dbg["driver_meta"]["head_error"] = type(exc).__name__
            try:
                rr = _get(sess, page_url, page_url, SETTINGS.REQ_TIMEOUT)
                dbg["driver_meta"]["get_initial"] = {
                    "status": rr.status_code,
                    "content_type": rr.headers.get("Content-Type"),
                    "content_length": rr.headers.get("Content-Length"),
                    "final_url": str(rr.url),
                    "bytes": len(rr.content or b""),
                }
                if rr.ok and _looks_like_pdf(rr.content):
                    dbg["step"] = "ok_direct"
                    return rr.content, str(rr.url), dbg
            except Exception as exc:
                dbg["driver_meta"]["get_error"] = type(exc).__name__
            dbg["step"] = "pdf_fetch_failed"
            return None, None, dbg

        try:
            r0 = sess.get(
                page_url,
                headers=BROWSER_HEADERS,
                timeout=SETTINGS.REQ_TIMEOUT,
                allow_redirects=True,
            )
            r0.raise_for_status()
            html = r0.text
            soup = BeautifulSoup(html, "html.parser")
        except Exception as exc:
            dbg["step"] = f"page_fetch_error:{type(exc).__name__}"
            dbg["driver_meta"]["error"] = str(exc)
            return None, None, dbg

        candidates = _gather_candidates(html, soup, r0.url)
        if not candidates:
            dbg["step"] = "no_candidates"
            return None, None, dbg

        candidates.sort(key=_score, reverse=True)

        backoff = 0.5
        max_tries = 2

        for cand in candidates:
            try:
                h = _head(sess, cand, r0.url, SETTINGS.REQ_TIMEOUT)
                final = str(h.url)
                ct = (h.headers.get("Content-Type") or "").lower()
                pdfish = ct.startswith("application/pdf") or final.lower().endswith(
                    ".pdf"
                )
                dbg["driver_meta"][f"head_{cand}"] = {
                    "status": h.status_code,
                    "content_type": h.headers.get("Content-Type"),
                    "content_length": h.headers.get("Content-Length"),
                    "final_url": final,
                }
                size_hint = h.headers.get("Content-Length")
                if size_hint and size_hint.isdigit():
                    estimate = int(size_hint)
                    if estimate > MAX_PDF_BYTES:
                        dbg["driver_meta"][f"skip_{cand}"] = {
                            "reason": "too_large",
                            "content_length": estimate,
                        }
                        continue
            except Exception as exc:
                dbg["driver_meta"][f"head_err_{cand}"] = type(exc).__name__
                final = cand
                pdfish = final.lower().endswith(".pdf")

            target = final if pdfish else cand

            for attempt in range(1, max_tries + 1):
                try:
                    t0 = time.monotonic()
                    rr = _get(sess, target, r0.url, SETTINGS.REQ_TIMEOUT)
                    elapsed_ms = int((time.monotonic() - t0) * 1000)
                    dbg["driver_meta"][f"get_{attempt}_{target}"] = {
                        "status": rr.status_code,
                        "content_type": rr.headers.get("Content-Type"),
                        "content_length": rr.headers.get("Content-Length"),
                        "elapsed_ms": elapsed_ms,
                        "final_url": str(rr.url),
                        "bytes": len(rr.content or b""),
                    }
                    if rr.ok and _looks_like_pdf(rr.content):
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
                except requests.RequestException as exc:
                    dbg["driver_meta"][f"get_err_{attempt}_{target}"] = type(exc).__name__
                    if attempt < max_tries:
                        time.sleep(backoff * attempt)
                        continue
                    break

        dbg["step"] = "no_pdf_confirmed"
        return None, None, dbg
