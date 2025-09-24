# core/drivers/exbo.py
from __future__ import annotations

import re
from typing import Dict, Any, Tuple, List, Optional
from urllib.parse import urlparse, urljoin, parse_qs

import requests
from bs4 import BeautifulSoup, Tag

from core.http_headers import BROWSER_HEADERS
from ..config import SETTINGS

PDF_MAGIC = b"%PDF"
_MIN_GOOD_BYTES = 150_000  # Exbo-pdf'ene kan være små – ikke sett dette for høyt


def _origin(u: str) -> str:
    try:
        p = urlparse(u)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""


def _head(
    sess: requests.Session, url: str, referer: str, timeout: int
) -> requests.Response:
    headers = dict(BROWSER_HEADERS)
    headers.update(
        {
            "Accept": "application/pdf,application/octet-stream,*/*;q=0.8",
            "Referer": referer,
            "Origin": _origin(referer) or _origin(url),
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
            "Origin": _origin(referer) or _origin(url),
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
        }
    )
    return sess.get(url, headers=headers, timeout=timeout, allow_redirects=True)


def _looks_like_pdf_bytes(b: bytes | None) -> bool:
    if not b or len(b) < _MIN_GOOD_BYTES:
        return False
    return b.startswith(PDF_MAGIC)


def _abs(base_url: str, href: str | None) -> str | None:
    if not href:
        return None
    return urljoin(base_url, href)


def _find_meglervisning_href(html: str, base_url: str) -> Optional[str]:
    """
    Finn direkte-lenker til meglervisning.no/salgsoppgave/hent... i HTML.
    """
    m = re.search(
        r'https?://meglervisning\.no/salgsoppgave/hent\?[^"\']+',
        html,
        flags=re.I,
    )
    if m:
        return m.group(0)
    # fallback: relativ URL i attributter (sjelden)
    m2 = re.search(
        r'["\'](/salgsoppgave/hent\?[^"\']+)["\']',
        html,
        flags=re.I,
    )
    if m2:
        return urljoin(base_url, m2.group(1))
    return None


def _gather_candidates(soup: BeautifulSoup, base_url: str) -> List[str]:
    urls: List[str] = []

    # a[href]
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
        if (
            "salgsoppgav" in lo
            or "prospekt" in lo
            or "komplett" in lo
            or absu.lower().endswith(".pdf")
            or "meglervisning.no/salgsoppgave/hent" in absu.lower()
        ):
            urls.append(absu)

    # andre elementer med data-* lenker
    for el in soup.find_all(["button", "div", "span"]):
        if not isinstance(el, Tag):
            continue
        txt = (el.get_text(" ", strip=True) or "").lower()
        for attr in ("data-href", "data-url", "data-file", "data-download"):
            raw = (el.get(attr) or "").strip()
            if not raw:
                continue
            absu = _abs(base_url, raw)
            if not absu:
                continue
            lo = txt + " " + absu.lower()
            if (
                "salgsoppgav" in lo
                or "prospekt" in lo
                or "komplett" in lo
                or absu.lower().endswith(".pdf")
                or "meglervisning.no/salgsoppgave/hent" in absu.lower()
            ):
                urls.append(absu)

    # uniq
    seen: set[str] = set()
    out: List[str] = []
    for u in urls:
        if u not in seen:
            out.append(u)
            seen.add(u)
    return out


class ExboDriver:
    name = "exbo"

    def matches(self, url: str) -> bool:
        u = (url or "").lower()
        if "exbo.no" in u:
            return True
        if "meglervisning.no/salgsoppgave/hent" in u:
            # helst med instid=MSEXBO, men tillat generelt
            q = parse_qs(urlparse(u).query)
            inst = (q.get("instid") or [""])[0].upper()
            return inst in ("MSEXBO", "")  # ofte MSEXBO
        return False

    def try_fetch(
        self, sess: requests.Session, page_url: str
    ) -> Tuple[bytes | None, str | None, dict]:
        dbg: Dict[str, Any] = {"driver": self.name, "step": "start", "meta": {}}
        timeout = SETTINGS.REQ_TIMEOUT

        def _return_pdf(u: str, referer: str) -> Tuple[bytes | None, str | None]:
            # Prøv HEAD → GET
            try:
                h = _head(sess, u, referer, timeout)
                ct = (h.headers.get("Content-Type") or "").lower()
                final = str(h.url)
                if h.ok and ("application/pdf" in ct or final.lower().endswith(".pdf")):
                    r = _get(sess, final, referer, timeout)
                    dbg["meta"][f"get_{final}"] = {
                        "status": r.status_code,
                        "ct": r.headers.get("Content-Type"),
                        "len": r.headers.get("Content-Length"),
                        "final_url": str(r.url),
                        "bytes": len(r.content or b""),
                    }
                    if _looks_like_pdf_bytes(r.content):
                        return r.content, final
            except Exception:
                pass
            # GET direkte (no-HEAD)
            try:
                r = _get(sess, u, referer, timeout)
                dbg["meta"][f"get_{u}"] = {
                    "status": r.status_code,
                    "ct": r.headers.get("Content-Type"),
                    "len": r.headers.get("Content-Length"),
                    "final_url": str(r.url),
                    "bytes": len(r.content or b""),
                }
                if _looks_like_pdf_bytes(r.content):
                    return r.content, str(r.url)
            except Exception:
                pass
            return None, None

        # 1) Direkte meglervisning-lenke → hent
        if "meglervisning.no/salgsoppgave/hent" in page_url.lower():
            b, u = _return_pdf(page_url, page_url)
            if b:
                dbg["step"] = "ok_direct_mvl"
                return b, u or page_url, dbg
            # hvis første forsøk feiler, prøv med “renskede” query (uten sporing)
            try:
                p = urlparse(page_url)
                q = parse_qs(p.query)
                base = f"{p.scheme}://{p.netloc}{p.path}"
                keys = ["instid", "estateid"]
                clean = (
                    base
                    + "?"
                    + "&".join(f"{k}={q[k][0]}" for k in keys if k in q and q[k])
                )
                b2, u2 = _return_pdf(clean, page_url)
                if b2:
                    dbg["step"] = "ok_direct_mvl_clean"
                    return b2, u2 or clean, dbg
            except Exception:
                pass
            dbg["step"] = "direct_mvl_failed"

        # 2) Exbo-side → finn meglervisning-lenke eller direkte pdf
        try:
            r0 = sess.get(
                page_url, headers=BROWSER_HEADERS, timeout=timeout, allow_redirects=True
            )
            r0.raise_for_status()
            html = r0.text or ""
            soup = BeautifulSoup(html, "html.parser")
        except Exception as e:
            dbg["step"] = f"page_fetch_error:{type(e).__name__}"
            dbg["error"] = str(e)
            return None, None, dbg

        # 2a) Prøv å finne meglervisning-lenke i rå HTML
        mv = _find_meglervisning_href(html, page_url)
        if mv:
            b, u = _return_pdf(mv, page_url)
            if b:
                dbg["step"] = "ok_from_meglervisning"
                dbg["meta"]["mv_href"] = mv
                return b, u or mv, dbg

        # 2b) Ellers vanlige kandidater på siden
        for cand in _gather_candidates(soup, page_url):
            lo = cand.lower()
            if ("meglervisning.no/salgsoppgave/hent" in lo) or lo.endswith(".pdf"):
                b, u = _return_pdf(cand, page_url)
                if b:
                    dbg["step"] = "ok_direct"
                    dbg["meta"]["picked"] = cand
                    return b, u or cand, dbg

        dbg["step"] = "no_pdf_confirmed"
        return None, None, dbg
