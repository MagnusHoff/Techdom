# core/drivers/exbo.py
from __future__ import annotations

import re
import time
from typing import Dict, Any, Tuple, List, Optional
from urllib.parse import urlparse, urljoin, parse_qs

import requests
from bs4 import BeautifulSoup, Tag

from .base import Driver
from techdom.ingestion.http_headers import BROWSER_HEADERS
from techdom.infrastructure.config import SETTINGS

REQ_TIMEOUT: int = int(getattr(SETTINGS, "REQ_TIMEOUT", 25))

PDF_MAGIC = b"%PDF"
_MIN_GOOD_BYTES = 150_000  # Exbo-PDF-er kan være små

# --- Salgsoppgave-only heuristics ---
ALLOW_CUES = (
    "salgsoppgav",  # salgsoppgave/salgsoppgaven
    "prospekt",  # benevnes noen ganger slik
    "komplett",  # komplett salgsoppgave
    "utskriftsvennlig",  # utskriftsvennlig salgsoppgave
    "digital",  # digital salgsoppgave
)
BLOCK_CUES = (
    "tilstandsrapport",
    "boligsalgsrapport",
    "ns3600",
    "ns_3600",
    "ns-3600",
    "energiattest",
    "nabolag",
    "nabolagsprofil",
    "contentassets/nabolaget",
    "egenerkl",
    "takst",
    "anticimex",
    "bud",
    "budskjema",
    "prisliste",
    "vilkår",
    "terms",
    "cookies",
)


def _as_str(v: object) -> str:
    """Normaliser BeautifulSoup AttributeValue til str."""
    if isinstance(v, str):
        return v
    if isinstance(v, (list, tuple)) and v and isinstance(v[0], str):
        return v[0]
    return ""


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


def _abs(base_url: str, href: str | None) -> Optional[str]:
    return urljoin(base_url, href) if href else None


def _is_meglervisning_salgsoppgave(u: str) -> bool:
    return "meglervisning.no/salgsoppgave/hent" in (u or "").lower()


def _is_salgsoppgave(label: str, url: str) -> bool:
    lo = (f"{label} {url}").lower()
    if any(b in lo for b in BLOCK_CUES):
        return False
    if _is_meglervisning_salgsoppgave(url):
        # dette endepunktet er selve salgsoppgaven hos Exbo
        return True
    # må ha minst ett positivt salgsoppgave-signal
    return any(a in lo for a in ALLOW_CUES)


def _find_meglervisning_href(html: str, base_url: str) -> Optional[str]:
    m = re.search(r'https?://meglervisning\.no/salgsoppgave/hent\?[^"\']+', html, re.I)
    if m:
        return m.group(0)
    m2 = re.search(r'["\'](/salgsoppgave/hent\?[^"\']+)["\']', html, re.I)
    if m2:
        return urljoin(base_url, m2.group(1))
    return None


def _gather_candidates(soup: BeautifulSoup, base_url: str) -> List[str]:
    urls: List[str] = []

    # a[href]
    for a in soup.find_all("a"):
        if not isinstance(a, Tag):
            continue
        txt = a.get_text(" ", strip=True) or ""
        href = _as_str(a.get("href") or a.get("data-href") or a.get("download")).strip()
        if not href:
            continue
        absu = _abs(base_url, href)
        if not absu:
            continue
        if _is_salgsoppgave(txt, absu) and (
            absu.lower().endswith(".pdf") or _is_meglervisning_salgsoppgave(absu)
        ):
            urls.append(absu)

    # data-* lenker
    for el in soup.find_all(["button", "div", "span"]):
        if not isinstance(el, Tag):
            continue
        txt = el.get_text(" ", strip=True) or ""
        for attr in ("data-href", "data-url", "data-file", "data-download"):
            raw = _as_str(el.get(attr)).strip()
            if not raw:
                continue
            absu = _abs(base_url, raw)
            if not absu:
                continue
            if _is_salgsoppgave(txt, absu) and (
                absu.lower().endswith(".pdf") or _is_meglervisning_salgsoppgave(absu)
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


class ExboDriver(Driver):
    name = "exbo"

    def matches(self, url: str) -> bool:
        u = (url or "").lower()
        if "exbo.no" in u:
            return True
        if _is_meglervisning_salgsoppgave(u):
            q = parse_qs(urlparse(u).query)
            inst = (q.get("instid") or [""])[0].upper()
            return inst in ("MSEXBO", "")  # tillat generelt, men MSEXBO er “riktig”
        return False

    def try_fetch(
        self, sess: requests.Session, page_url: str
    ) -> Tuple[bytes | None, str | None, dict]:
        dbg: Dict[str, Any] = {"driver": self.name, "step": "start", "driver_meta": {}}
        timeout = REQ_TIMEOUT
        backoff = 0.5
        transient = (429, 500, 502, 503, 504)

        def _return_pdf(u: str, referer: str) -> Tuple[bytes | None, str | None]:
            # HEAD → GET med små retries på transiente feil
            for attempt in range(1, 3):
                try:
                    h = _head(sess, u, referer, timeout)
                    ct = (h.headers.get("Content-Type") or "").lower()
                    final = str(h.url)
                    if h.ok and (
                        "application/pdf" in ct or final.lower().endswith(".pdf")
                    ):
                        r = _get(sess, final, referer, timeout)
                        dbg["driver_meta"][f"get_{attempt}_{final}"] = {
                            "status": r.status_code,
                            "ct": r.headers.get("Content-Type"),
                            "len": r.headers.get("Content-Length"),
                            "final_url": str(r.url),
                            "bytes": len(r.content or b""),
                        }
                        if _looks_like_pdf_bytes(r.content):
                            return r.content, final
                        if r.status_code in transient and attempt < 2:
                            time.sleep(backoff * attempt)
                            continue
                        return None, None
                except requests.RequestException:
                    if attempt < 2:
                        time.sleep(backoff * attempt)
                        continue
                break

            # Fallback: direkte GET uten HEAD
            for attempt in range(1, 3):
                try:
                    r = _get(sess, u, referer, timeout)
                    dbg["driver_meta"][f"get_{attempt}_{u}"] = {
                        "status": r.status_code,
                        "ct": r.headers.get("Content-Type"),
                        "len": r.headers.get("Content-Length"),
                        "final_url": str(r.url),
                        "bytes": len(r.content or b""),
                    }
                    if _looks_like_pdf_bytes(r.content):
                        return r.content, str(r.url)
                    if r.status_code in transient and attempt < 2:
                        time.sleep(backoff * attempt)
                        continue
                except requests.RequestException:
                    if attempt < 2:
                        time.sleep(backoff * attempt)
                        continue
                break
            return None, None

        # 1) Direkte meglervisning-lenke (alltid tillatt – dette er salgsoppgaven)
        if _is_meglervisning_salgsoppgave(page_url):
            b, u = _return_pdf(page_url, page_url)
            if b:
                dbg["step"] = "ok_direct_mvl"
                return b, u or page_url, dbg

            # Rensket query (uten sporing)
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

        # 2) Exbo-side → hent HTML
        try:
            r0 = sess.get(
                page_url, headers=BROWSER_HEADERS, timeout=timeout, allow_redirects=True
            )
            r0.raise_for_status()
            html = r0.text or ""
            soup = BeautifulSoup(html, "html.parser")
        except Exception as e:
            dbg["step"] = f"page_fetch_error:{type(e).__name__}"
            dbg["driver_meta"]["error"] = str(e)
            return None, None, dbg

        # 2a) Prøv å finne meglervisning-lenke i rå HTML
        mv = _find_meglervisning_href(html, page_url)
        if mv:
            b, u = _return_pdf(mv, page_url)
            if b:
                dbg["step"] = "ok_from_meglervisning"
                dbg["driver_meta"]["mv_href"] = mv
                return b, u or mv, dbg

        # 2b) Vanlige kandidater (kun salgsoppgave/prospekt)
        for cand in _gather_candidates(soup, page_url):
            lo = cand.lower()
            if not (_is_meglervisning_salgsoppgave(lo) or lo.endswith(".pdf")):
                continue
            b, u = _return_pdf(cand, page_url)
            if b:
                dbg["step"] = "ok_direct"
                dbg["driver_meta"]["picked"] = cand
                return b, u or cand, dbg

        dbg["step"] = "no_pdf_confirmed"
        return None, None, dbg
