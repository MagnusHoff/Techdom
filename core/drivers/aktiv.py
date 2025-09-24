# drivers/aktiv.py
from __future__ import annotations

import time
import re
from typing import Dict, Any, Tuple, List
import requests
from bs4 import BeautifulSoup, Tag
from urllib.parse import urlparse, urljoin

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
    """
    Finn PDF-kandidater i DOM og rå HTML.
    – Aktiv bruker ofte direkte <a href="https://file-proxy.rfcdn.io/.../Digital~salgsoppgave.PDF?...">
    """
    urls: List[str] = []

    # 1) <a> med href/data-href/download
    if hasattr(soup, "find_all"):
        for a in soup.find_all("a"):
            if not isinstance(a, Tag):
                continue
            txt = (a.get_text(" ", strip=True) or "").lower()
            href = (
                a.get("href") or a.get("data-href") or a.get("download") or ""
            ).strip()
            if not href:
                continue
            absu = _abs(base_url, href)
            if not absu:
                continue
            lo = f"{txt} {absu.lower()}"
            if "salgsoppgav" in lo or "prospekt" in lo or absu.lower().endswith(".pdf"):
                urls.append(absu)

    # 2) andre elementer med data-url/data-file/data-download
    if hasattr(soup, "find_all"):
        for el in soup.find_all(["button", "div", "span"]):
            if not isinstance(el, Tag):
                continue
            txt = (el.get_text(" ", strip=True) or "").lower()
            for attr in ("data-href", "data-url", "data-file", "data-download"):
                href = (el.get(attr) or "").strip()
                if not href:
                    continue
                absu = _abs(base_url, href)
                if not absu:
                    continue
                lo = f"{txt} {absu.lower()}"
                if (
                    "salgsoppgav" in lo
                    or "prospekt" in lo
                    or absu.lower().endswith(".pdf")
                ):
                    urls.append(absu)

    # 3) Regex i rå HTML (fanger file-proxy.rfcdn.io / *.pdf i scripts)
    try:
        html = soup.decode()
    except Exception:
        html = ""
    for m in re.finditer(r'https?://[^\s"\'<>]+\.pdf(?:\?[^\s<>\'"]*)?', html, re.I):
        u = m.group(0)
        if u:
            urls.append(u)

    # uniq + gi “salgsoppgave”/“.pdf” et fortrinn senere
    seen: set[str] = set()
    out: List[str] = []
    for u in urls:
        if u not in seen:
            out.append(u)
            seen.add(u)
    return out


def _score_candidate(url: str) -> int:
    s = (url or "").lower()
    sc = 0
    if s.endswith(".pdf"):
        sc += 30
    if "salgsoppgav" in s:
        sc += 40
    if "prospekt" in s:
        sc += 20
    # File-proxy fra Aktiv (rfcdn) er ofte riktig dokument-kilde
    if "file-proxy.rfcdn.io" in s or "/aktiv/" in s:
        sc += 15
    return sc


class AktivDriver:
    name = "aktiv"

    def matches(self, url: str) -> bool:
        return "aktiv.no" in (url or "").lower()

    def try_fetch(
        self, sess: requests.Session, page_url: str
    ) -> Tuple[bytes | None, str | None, dict]:
        dbg: Dict[str, Any] = {"driver": self.name, "step": "start", "driver_meta": {}}

        # Bruk siden som referer (Aktiv har typisk dokumentseksjon på hovedsiden)
        referer = page_url.rstrip("/")

        # 1) Hent HTML
        try:
            r0 = _get(sess, referer, referer, SETTINGS.REQ_TIMEOUT)
            r0.raise_for_status()
            soup = BeautifulSoup(r0.text, "html.parser")
        except Exception as e:
            dbg["step"] = f"page_fetch_error:{type(e).__name__}"
            return None, None, dbg

        # 2) Finn PDF-kandidater
        candidates = _gather_pdf_candidates(soup, referer)
        if not candidates:
            dbg["step"] = "no_candidates"
            return None, None, dbg

        # 3) Prioriter
        candidates.sort(key=_score_candidate, reverse=True)

        # 4) HEAD + GET (små retries)
        backoff = 0.6
        max_tries = 2

        for url in candidates:
            # HEAD for å sjekke content-type og evt. endelig URL
            try:
                h = _head(sess, url, referer, SETTINGS.REQ_TIMEOUT)
                final = str(h.url)
                ct = (h.headers.get("Content-Type") or "").lower()
                pdfish = ct.startswith("application/pdf") or final.lower().endswith(
                    ".pdf"
                )

                dbg["driver_meta"][f"head_{url}"] = {
                    "status": h.status_code,
                    "ct": h.headers.get("Content-Type"),
                    "final_url": final,
                }

                if not h.ok and h.status_code not in (301, 302, 303, 307, 308):
                    # Prøv GET direkte om HEAD blokkeres
                    final = url

                # GET for å hente bytes
                for attempt in range(1, max_tries + 1):
                    try:
                        t0 = time.monotonic()
                        rr = _get(sess, final, referer, SETTINGS.REQ_TIMEOUT)
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

            except Exception:
                # Fortsett til neste kandidat
                continue

        dbg["step"] = "no_pdf_confirmed"
        return None, None, dbg
