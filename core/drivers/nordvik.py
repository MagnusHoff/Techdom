# core/drivers/nordvik.py
from __future__ import annotations

import re
import time
from typing import Dict, Any, Tuple, List, Optional, Mapping
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup, Tag

from .base import Driver
from core.http_headers import BROWSER_HEADERS
from ..config import SETTINGS

PDF_MAGIC = b"%PDF-"

# --- kun salgsoppgave/prospekt ---
ALLOW_RX = re.compile(r"(salgsoppgav|prospekt|utskriftsvennlig|komplett)", re.I)
BLOCK_RX = re.compile(
    r"(tilstandsrapport|boligsalgsrapport|ns[\s_-]*3600|energiattest|egenerkl|"
    r"nabolag|nabolagsprofil|contentassets/nabolaget|takst|fidens|bud|budskjema|"
    r"vedtekter|arsberetning|årsberetning|regnskap|sameie|kontrakt|kjopetilbud)",
    re.I,
)

MIN_BYTES = 300_000
MIN_PAGES = 4


def _looks_like_pdf(b: bytes | None) -> bool:
    return isinstance(b, (bytes, bytearray)) and b.startswith(PDF_MAGIC)


def _pdf_pages(b: bytes | None) -> int:
    """Liten, robust sidetelling (ikke kritisk ved feil)."""
    if not b:
        return 0
    try:
        import io
        from PyPDF2 import PdfReader  # type: ignore

        return len(PdfReader(io.BytesIO(b)).pages)
    except Exception:
        return 0


def _pdf_quality_ok(b: bytes | None) -> bool:
    if not b or not _looks_like_pdf(b) or len(b) < MIN_BYTES:
        return False
    return _pdf_pages(b) >= MIN_PAGES


def _origin(u: str) -> str:
    try:
        p = urlparse(u)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""


def _abs(base_url: str, href: str | None) -> Optional[str]:
    if not href:
        return None
    return urljoin(base_url, href)


def _content_filename(headers: Mapping[str, str] | None) -> str:
    if not headers:
        return ""
    cd = headers.get("Content-Disposition") or headers.get("content-disposition") or ""
    m = re.search(r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';]+)', cd)
    return (m.group(1) if m else "").strip()


def _is_salgsoppgave(
    url: str, headers: Mapping[str, str] | None, label: str = ""
) -> bool:
    """Strengt filter: kun salgsoppgave/prospekt; blokker TR/annet."""
    lo = (url or "").lower()
    fn = (_content_filename(headers) or "").lower()
    hay = " ".join([lo, fn, (label or "").lower()])
    if BLOCK_RX.search(hay):
        return False
    return bool(ALLOW_RX.search(hay))


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


# ---- trygge extractor-hjelpere for BeautifulSoup ----
def _as_str(v: object) -> str:
    """Gjør BS4-attributtverdi om til str på en trygg måte."""
    if isinstance(v, str):
        return v
    # BeautifulSoup kan returnere liste av verdier for noen attributter
    if isinstance(v, (list, tuple)) and v and isinstance(v[0], str):
        return v[0]
    return ""


def _gather_salgsoppgave_candidates(
    soup: BeautifulSoup, base_url: str
) -> List[tuple[str, str]]:
    """
    Returner [(url, label)] som tydelig matcher salgsoppgave/prospekt.
    Ikke ta med generelle /dokument/ uten navn – disse kan være TR.
    """
    out: List[tuple[str, str]] = []

    # 1) DOM-elementer (a/button/div/span) med relevant label/URL
    for el in soup.find_all(["a", "button", "div", "span"]):
        if not isinstance(el, Tag):
            continue
        label = (el.get_text(" ", strip=True) or "").strip()
        href_raw = (
            el.get("href")
            or el.get("data-href")
            or el.get("data-url")
            or el.get("data-file")
            or ""
        )
        href = _as_str(href_raw).strip()
        if not href:
            continue
        u = _abs(base_url, href)
        if not u:
            continue
        # Strengt: KUN hvis label/URL peker mot salgsoppgave/prospekt – og ikke har blokkord
        if _is_salgsoppgave(u, None, label):
            out.append((u, label))

    # 2) Direkte .pdf-URL-er i rå HTML – men kun dersom ALLOW_RX treffer og ikke BLOCK_RX
    try:
        html = soup.decode()
    except Exception:
        html = ""
    for m in re.finditer(
        r'https?://[^\s"\'<>]+\.pdf(?:\?[^\s<>\'"]*)?', html or "", re.I
    ):
        u = m.group(0)
        if _is_salgsoppgave(u, None, ""):
            out.append((u, ""))

    # uniq, behold rekkefølge
    seen: set[str] = set()
    uniq: List[tuple[str, str]] = []
    for u, t in out:
        if u not in seen:
            uniq.append((u, t))
            seen.add(u)
    return uniq


def _score_candidate(u: str, label: str) -> int:
    """Prioriter tydelige salgsoppgave-signaler."""
    lo = (u + " " + (label or "")).lower()
    sc = 0
    if lo.endswith(".pdf"):
        sc += 30
    if "salgsoppgav" in lo:
        sc += 40
    if "prospekt" in lo:
        sc += 20
    if "utskriftsvennlig" in lo or "komplett" in lo:
        sc += 10
    # liten bonus for kortere (ofte mer 'direkte') URL
    sc += max(0, 20 - len(u) // 100)
    return sc


class NordvikDriver(Driver):
    name = "nordvik"

    def matches(self, url: str) -> bool:
        return "nordvikbolig.no/boliger/" in (url or "").lower()

    def try_fetch(
        self, sess: requests.Session, page_url: str
    ) -> Tuple[bytes | None, str | None, dict]:
        dbg: Dict[str, Any] = {"driver": self.name, "step": "start", "meta": {}}

        referer = page_url.rstrip("/")

        # 1) Hent siden
        try:
            r0 = _get(sess, referer, referer, SETTINGS.REQ_TIMEOUT)
            r0.raise_for_status()
            soup = BeautifulSoup(r0.text, "html.parser")
            dbg["meta"]["page_status"] = r0.status_code
            dbg["meta"]["page_len"] = len(r0.text or "")
        except Exception as e:
            dbg["step"] = f"page_fetch_error:{type(e).__name__}"
            dbg["error"] = repr(e)
            return None, None, dbg

        # 2) Kandidater (kun salgsoppgave/prospekt)
        cands = _gather_salgsoppgave_candidates(soup, referer)
        if not cands:
            dbg["step"] = "no_candidates"
            dbg["meta"]["candidates"] = []
            return None, None, dbg

        cands.sort(key=lambda x: _score_candidate(x[0], x[1]), reverse=True)
        dbg["meta"]["candidates_preview"] = [u for (u, _t) in cands[:8]]

        # 3) HEAD/GET med korte retries + streng filtrering ved hver respons
        backoff = 0.6
        max_tries = 2
        transient = (429, 500, 502, 503, 504)

        for url, label in cands:
            # HEAD
            try:
                h = _head(sess, url, referer, SETTINGS.REQ_TIMEOUT)
                final = str(h.url)
                ct = (h.headers.get("Content-Type") or "").lower()
                if not _is_salgsoppgave(final, h.headers, label):
                    continue
                pdfish = ct.startswith("application/pdf") or final.lower().endswith(
                    ".pdf"
                )
            except Exception:
                final = url
                pdfish = False

            # GET
            target = final if pdfish else url
            for attempt in range(1, max_tries + 1):
                try:
                    t0 = time.monotonic()
                    rr = _get(sess, target, referer, SETTINGS.REQ_TIMEOUT)
                    elapsed_ms = int((time.monotonic() - t0) * 1000)
                    dbg.setdefault("driver_meta", {})[f"get_{attempt}_{target}"] = {
                        "status": rr.status_code,
                        "content_type": rr.headers.get("Content-Type"),
                        "content_length": rr.headers.get("Content-Length"),
                        "elapsed_ms": elapsed_ms,
                        "final_url": str(rr.url),
                        "bytes": len(rr.content or b""),
                        "cd_filename": _content_filename(rr.headers),
                        "label": label,
                    }

                    # filtrer fortsatt: kun salgsoppgave
                    if not _is_salgsoppgave(str(rr.url), rr.headers, label):
                        if attempt < max_tries and rr.status_code in transient:
                            time.sleep(backoff * attempt)
                            continue
                        break

                    if rr.ok and _pdf_quality_ok(rr.content):
                        dbg["step"] = "ok_direct"
                        return rr.content, str(rr.url), dbg

                    if attempt < max_tries and rr.status_code in transient:
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
