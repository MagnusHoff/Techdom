# core/drivers/generic_local.py
from __future__ import annotations

import re
import time
from typing import Dict, Tuple, List, Optional, Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

from techdom.ingestion.http_headers import BROWSER_HEADERS
from techdom.infrastructure.config import SETTINGS
from .base import Driver  # viktig: arve fra base

PDF_MAGIC = b"%PDF-"

# --- Policy: KUN salgsoppgave/prospekt ---
ALLOW_SIGNS = (
    "salgsoppgav",  # salgsoppgave / salgsoppgaven
    "prospekt",  # noen kaller salgsoppgaven prospekt
    "digital_salgsoppgave",
    "komplett",  # ofte "Komplett salgsoppgave"
    "utskriftsvennlig",  # "Utskriftsvennlig salgsoppgave"
)

BLOCK_SIGNS = (
    "tilstandsrapport",
    "boligsalgsrapport",
    "takst",
    "energiattest",
    "egenerkl",  # egenerkl/egen-erkl
    "egen-erkl",
    "nabolag",
    "nabolagsprofil",
    "bud",  # bud/budskjema
    "prisliste",
    "vilkår",
    "terms",
    "personvern",
    "privacy",
    "vedtekter",
    "avhendingslova",
    "anticimex",
    "boligkjøperforsikring",
)

# Åpenbare “klikk”-filer som ofte er dummy
BAD_FILENAMES = {"klikk.pdf"}


def _as_str(v: object) -> str:
    """Normaliser BeautifulSoup AttributeValue til str for trygg .strip()."""
    if isinstance(v, str):
        return v
    if isinstance(v, (list, tuple)) and v and isinstance(v[0], str):
        return v[0]
    return ""


def _looks_like_pdf(b: bytes | None) -> bool:
    return isinstance(b, (bytes, bytearray)) and b.startswith(PDF_MAGIC)


def _origin_of(u: str) -> str:
    try:
        p = urlparse(u)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""


def _abs(base_url: str, href: str | None) -> Optional[str]:
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


def _is_salgsoppgave(url: str, label: str) -> bool:
    """Returner True kun hvis dette sannsynligvis er salgsoppgave/prospekt."""
    s = (url or "").lower()
    lbl = (label or "").lower()

    # Må være en .pdf (unngå diffuse vedlegg-endepunkter uten filtype)
    if not s.endswith(".pdf"):
        return False

    # Filnavn som er kjent dårlig
    base = s.rsplit("/", 1)[-1]
    if base in BAD_FILENAMES:
        return False

    # Blokker typiske ikke-salgsoppgave-dokumenter
    hay = f"{s} {lbl}"
    if any(b in hay for b in BLOCK_SIGNS):
        return False

    # Krev salgsoppgave/prospekt-signal et sted
    return any(k in hay for k in ALLOW_SIGNS)


def _gather_pdf_candidates(soup: BeautifulSoup, base_url: str) -> List[str]:
    urls: List[str] = []

    def consider(href_val: object, label: str):
        href = _as_str(href_val).strip()
        if not href:
            return
        u = _abs(base_url, href)
        if not u:
            return
        if _is_salgsoppgave(u, label):
            urls.append(u)

    # 1) A-tagger
    for a in soup.find_all("a"):
        if not isinstance(a, Tag):
            continue
        txt = a.get_text(" ", strip=True) or ""
        consider(a.get("href") or a.get("data-href") or a.get("download"), txt)

    # 2) Elementer med data-* lenker
    for el in soup.find_all(["button", "div", "span"]):
        if not isinstance(el, Tag):
            continue
        txt = el.get_text(" ", strip=True) or ""
        for attr in ("data-href", "data-file", "data-url", "data-download"):
            consider(el.get(attr), txt)

    # 3) Regex i rå HTML – hent kun .pdf og filtrer strengt
    try:
        html = soup.decode()
    except Exception:
        html = ""
    for m in re.finditer(r'https?://[^\s"\'<>]+\.pdf(?:\?[^\s<>\'"]*)?', html, re.I):
        u = m.group(0)
        if _is_salgsoppgave(u, ""):
            urls.append(u)

    # uniq
    seen: set[str] = set()
    return [u for u in urls if not (u in seen or seen.add(u))]


def _score_candidate(url: str) -> int:
    s = url.lower()
    sc = 0
    if s.endswith(".pdf"):
        sc += 10
    if "digital_salgsoppgave" in s:
        sc += 40
    if "salgsoppgav" in s:
        sc += 40
    if "prospekt" in s:
        sc += 25
    return sc


class GenericLocalDriver(Driver):
    """
    Fallback-driver som prøver å finne PDF-er på hvilken som helst megler-side.
    Skal alltid stå sist i DRIVERS-listen i __init__.py.
    KUN salgsoppgave/prospekt returneres.
    """

    name = "generic_local"

    def matches(self, url: str) -> bool:
        return True  # alltid fallback

    def try_fetch(
        self, sess: requests.Session, page_url: str
    ) -> Tuple[bytes | None, str | None, dict]:
        driver_meta: Dict[str, Any] = {"page_url": page_url}
        dbg: Dict[str, Any] = {
            "driver": self.name,
            "step": "start",
            "driver_meta": driver_meta,
        }

        try:
            r0 = _get(sess, page_url, page_url, SETTINGS.REQ_TIMEOUT)
            r0.raise_for_status()
            soup = BeautifulSoup(r0.text, "html.parser")
        except Exception as e:
            dbg["step"] = f"page_fetch_error:{type(e).__name__}"
            driver_meta["error"] = str(e)
            return None, None, dbg

        candidates = _gather_pdf_candidates(soup, page_url)
        if not candidates:
            dbg["step"] = "no_candidates"
            return None, None, dbg

        candidates.sort(key=_score_candidate, reverse=True)

        backoff = 0.6
        max_tries = 2

        for url in candidates:
            try:
                h = _head(sess, url, page_url, SETTINGS.REQ_TIMEOUT)
                final = str(h.url)
                ct = (h.headers.get("Content-Type") or "").lower()
                pdfish = ct.startswith("application/pdf") or final.lower().endswith(
                    ".pdf"
                )

                driver_meta[f"head_{url}"] = {
                    "status": h.status_code,
                    "ct": h.headers.get("Content-Type"),
                    "final_url": final,
                }

                if pdfish:
                    for attempt in range(1, max_tries + 1):
                        t0 = time.monotonic()
                        rr = _get(sess, final, page_url, SETTINGS.REQ_TIMEOUT)
                        ct2 = (rr.headers.get("Content-Type") or "").lower()
                        ok_pdf = rr.ok and (
                            ("application/pdf" in ct2) or _looks_like_pdf(rr.content)
                        )
                        driver_meta[f"get_{attempt}_{final}"] = {
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
            except Exception as e:
                driver_meta[f"head_err_{url}"] = str(e)

            # fallback: GET direkte
            for attempt in range(1, max_tries + 1):
                try:
                    t0 = time.monotonic()
                    rr = _get(sess, url, page_url, SETTINGS.REQ_TIMEOUT)
                    ct2 = (rr.headers.get("Content-Type") or "").lower()
                    ok_pdf = rr.ok and (
                        ("application/pdf" in ct2) or _looks_like_pdf(rr.content)
                    )
                    driver_meta[f"get_{attempt}_{url}"] = {
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
                except requests.RequestException as e:
                    driver_meta[f"get_err_{attempt}_{url}"] = str(e)
                    if attempt < max_tries:
                        time.sleep(backoff * attempt)
                        continue
                    break

        dbg["step"] = "no_pdf_confirmed"
        return None, None, dbg
