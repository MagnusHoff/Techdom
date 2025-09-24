# core/drivers/em1.py
from __future__ import annotations

import io
import re
import time
from typing import Dict, Any, Tuple, List, Optional
from urllib.parse import urlparse, urljoin

import requests
from PyPDF2 import PdfReader
from bs4 import BeautifulSoup, Tag

from core.http_headers import BROWSER_HEADERS
from ..config import SETTINGS

PDF_MAGIC = b"%PDF-"
_WTS_HOST = "epaper.webtopsolutions.com"
_MIN_GOOD_BYTES = 2_000_000  # 2 MB – ekte salgsoppgaver er normalt > 2–3 MB
_MIN_GOOD_PAGES = 8  # krever minst 8 sider


def _page_count(b: bytes) -> int:
    try:
        return len(PdfReader(io.BytesIO(b)).pages)
    except Exception:
        return 0


def _looks_like_pdf(b: bytes | None) -> bool:
    return isinstance(b, (bytes, bytearray)) and b.startswith(PDF_MAGIC)


def _origin(u: str) -> str:
    try:
        p = urlparse(u)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""


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


def _abs(base_url: str, href: str | None) -> str | None:
    if not href:
        return None
    return urljoin(base_url, href)


def _gather_pdf_candidates(soup: BeautifulSoup, base_url: str) -> List[str]:
    urls: List[str] = []

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
        if any(
            k in lo for k in ("salgsoppgav", "prospekt", "last ned", "pdf")
        ) or absu.lower().endswith(".pdf"):
            urls.append(absu)

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
            lo = txt + " " + absu.lower()
            if any(
                k in lo for k in ("salgsoppgav", "prospekt", "last ned", "pdf")
            ) or absu.lower().endswith(".pdf"):
                urls.append(absu)

    try:
        html = soup.decode()
    except Exception:
        html = ""
    for m in re.finditer(r'https?://[^\s"\'<>]+\.pdf(?:\?[^\s<>\'"]*)?', html, re.I):
        urls.append(m.group(0))

    seen: set[str] = set()
    out: List[str] = []
    for u in urls:
        if u not in seen:
            out.append(u)
            seen.add(u)
    return out


def _maybe_build_wts_pdf_urls(epaper_url: str) -> List[str]:
    out: List[str] = []
    try:
        p = urlparse(epaper_url)
        base = f"{p.scheme}://{p.netloc}{p.path}".rstrip("/")
        # mest vanlige
        out += [
            base + "/complete.pdf",
            base + "/publication.pdf",
            base + "/salgsoppgave.pdf",
            base + "/Digital~salgsoppgave.pdf",
            base + "/download.pdf",
            base + "/download?format=pdf",
            base + "?format=pdf",
            base + ".pdf",
        ]
        # noen installasjoner legger ‘publication/complete.pdf’
        out += [base + "/publication/complete.pdf", base + "/publication/download.pdf"]
    except Exception:
        pass
    # uniq
    seen: set[str] = set()
    uniq: List[str] = []
    for u in out:
        if u not in seen:
            uniq.append(u)
            seen.add(u)
    return uniq


def _extract_wts_pdf_from_html(html: str, base_url: str) -> Optional[str]:
    m = re.search(r'https?://[^\s"\']+\.pdf(?:\?[^\s"\']*)?', html, re.I)
    if m:
        return m.group(0)

    for m in re.finditer(
        r'"(?:url|pdf|downloadUrl)"\s*:\s*"([^"]+\.pdf[^"]*)"', html, re.I
    ):
        u = m.group(1).replace("\\/", "/")
        if u.lower().startswith(("http://", "https://")):
            return u
        return urljoin(base_url, u)

    if "?format=pdf" in html:
        return base_url.rstrip("/") + "?format=pdf"

    return None


def _bytes_ok(resp: requests.Response) -> bool:
    if not resp.ok:
        return False
    ct = (resp.headers.get("Content-Type") or "").lower()
    b = resp.content or b""
    size = int(resp.headers.get("Content-Length") or 0) or len(b)

    # Avvis eksplisitt WebtopSolutions /file.pdf
    try:
        pu = urlparse(str(resp.url))
        if pu.netloc.endswith(_WTS_HOST) and pu.path.endswith("/file.pdf"):
            return False
    except Exception:
        pass

    # Må være PDF + stor nok + mange nok sider
    if (_looks_like_pdf(b) or "application/pdf" in ct) and size >= _MIN_GOOD_BYTES:
        return _page_count(b) >= _MIN_GOOD_PAGES
    return False


class Em1Driver:
    name = "eie1"

    def matches(self, url: str) -> bool:
        return "eiendomsmegler1.no" in (url or "").lower()

    def try_fetch(
        self, sess: requests.Session, page_url: str
    ) -> Tuple[bytes | None, str | None, dict]:
        dbg: Dict[str, Any] = {"driver": self.name, "step": "start", "driver_meta": {}}

        # 1) Hent megler-siden
        try:
            r0 = _get(sess, page_url, page_url, SETTINGS.REQ_TIMEOUT)
            r0.raise_for_status()
            html0 = r0.text
            soup = BeautifulSoup(html0, "html.parser")
        except Exception as e:
            dbg["step"] = f"page_fetch_error:{type(e).__name__}"
            return None, None, dbg

        # 2) Kandidater og evt. WebtopSolutions-url
        candidates = _gather_pdf_candidates(soup, page_url)

        wts_url: Optional[str] = None
        m = re.search(
            r'https?://epaper\.webtopsolutions\.com/[^\s"\'<>)]+', html0, re.I
        )
        if m:
            wts_url = m.group(0)

        # 3) WTS først – nedlast ekte PDF og filtrer små filer
        if wts_url:
            dbg["driver_meta"]["wts_url"] = wts_url
            pdf_from_html: Optional[str] = None
            try:
                w = _get(sess, wts_url, page_url, SETTINGS.REQ_TIMEOUT)
                if w.ok:
                    pdf_from_html = _extract_wts_pdf_from_html(w.text or "", wts_url)
            except Exception:
                pass

            trial_urls = []
            if pdf_from_html:
                trial_urls.append(pdf_from_html)
            trial_urls.extend(_maybe_build_wts_pdf_urls(wts_url))

            backoff = 0.5
            for u in trial_urls:
                # HEAD
                try:
                    h = _head(sess, u, wts_url, SETTINGS.REQ_TIMEOUT)
                    ct = (h.headers.get("Content-Type") or "").lower()
                    final = str(h.url)
                    if h.ok and (
                        "application/pdf" in ct or final.lower().endswith(".pdf")
                    ):
                        rr = _get(sess, final, wts_url, SETTINGS.REQ_TIMEOUT)
                        dbg["driver_meta"][f"wts_get_{final}"] = {
                            "status": rr.status_code,
                            "content_type": rr.headers.get("Content-Type"),
                            "content_length": rr.headers.get("Content-Length"),
                            "final_url": str(rr.url),
                            "bytes": len(rr.content or b""),
                        }
                        if _bytes_ok(rr):
                            dbg["step"] = "ok_from_wts"
                            return rr.content, final, dbg
                except Exception:
                    pass

                # GET fallback (uten HEAD)
                try:
                    rr = _get(sess, u, wts_url, SETTINGS.REQ_TIMEOUT)
                    dbg["driver_meta"][f"wts_get_{u}"] = {
                        "status": rr.status_code,
                        "content_type": rr.headers.get("Content-Type"),
                        "content_length": rr.headers.get("Content-Length"),
                        "final_url": str(rr.url),
                        "bytes": len(rr.content or b""),
                    }
                    if _bytes_ok(rr):
                        dbg["step"] = "ok_from_wts"
                        return rr.content, str(rr.url), dbg
                except Exception:
                    pass
                time.sleep(backoff)

        # 4) Vanlige PDF-kandidater på megler-siden (også med minstekrav)
        ordered = sorted(
            candidates, key=lambda u: (0 if u.lower().endswith(".pdf") else 1, -len(u))
        )
        for url in ordered:
            try:
                h = _head(sess, url, page_url, SETTINGS.REQ_TIMEOUT)
                final = str(h.url)
                ct = (h.headers.get("Content-Type") or "").lower()
                if h.ok and ("application/pdf" in ct or final.lower().endswith(".pdf")):
                    rr = _get(sess, final, page_url, SETTINGS.REQ_TIMEOUT)
                    dbg["driver_meta"][f"get_{final}"] = {
                        "status": rr.status_code,
                        "content_type": rr.headers.get("Content-Type"),
                        "content_length": rr.headers.get("Content-Length"),
                        "final_url": str(rr.url),
                        "bytes": len(rr.content or b""),
                    }
                    if _bytes_ok(rr):
                        dbg["step"] = "ok_direct"
                        return rr.content, final, dbg
            except Exception:
                pass

            try:
                rr = _get(sess, url, page_url, SETTINGS.REQ_TIMEOUT)
                dbg["driver_meta"][f"get_{url}"] = {
                    "status": rr.status_code,
                    "content_type": rr.headers.get("Content-Type"),
                    "content_length": rr.headers.get("Content-Length"),
                    "final_url": str(rr.url),
                    "bytes": len(rr.content or b""),
                }
                if _bytes_ok(rr):
                    dbg["step"] = "ok_direct"
                    return rr.content, str(rr.url), dbg
            except Exception:
                pass

        dbg["step"] = "no_pdf_confirmed"
        return None, None, dbg
