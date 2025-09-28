# core/drivers/aktiv.py
from __future__ import annotations

import time
import re
from typing import Dict, Any, Tuple, List, Optional, Mapping
import requests
from bs4 import BeautifulSoup, Tag
from urllib.parse import urlparse, urljoin

from core.http_headers import BROWSER_HEADERS
from ..config import SETTINGS
from .base import Driver  # viktig: arve riktig base


def _as_str(v: Any) -> str:
    """Trygt konverter BeautifulSoup-attribute (kan være liste) til str."""
    if isinstance(v, str):
        return v
    if isinstance(v, (list, tuple)) and v and isinstance(v[0], str):
        return v[0]
    return ""


PDF_MAGIC = b"%PDF-"

# ---- policy: KUN salgsoppgave/prospekt ----
POS_WORDS = (
    "salgsoppgav",  # salgsoppgave / salgsoppgaven
    "prospekt",
    "utskriftsvennlig",
    "komplett",
    "digital_salgsoppgave",
)

NEG_WORDS = (
    "tilstandsrapport",
    "boligsalgsrapport",
    "ns3600",
    "ns_3600",
    "ns-3600",
    "energiattest",
    "egenerkl",
    "nabolag",
    "nabolagsprofil",
    "anticimex",
    "takst",
    "bud",
    "prisliste",
    "vilkår",
    "terms",
    "cookies",
)

# Typiske Aktiv-kilder/filnavn for salgsoppgave
POS_HOST_HINTS = (
    "file-proxy.rfcdn.io",  # Aktiv bruker ofte dette
    "/aktiv/",  # egne CDN-stier
)
POS_FILENAME_HINTS = (
    "digital~salgsoppgave",  # ofte brukt filnavn hos rfcdn/proxy
    "salgsoppgave",
    "prospekt",
)


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


def _content_filename(headers: Mapping[str, str] | None) -> Optional[str]:
    """Plukk evt. filnavn fra Content-Disposition."""
    if not headers:
        return None
    cd = headers.get("Content-Disposition") or headers.get("content-disposition") or ""
    m = re.search(r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';]+)', cd)
    if m:
        return m.group(1)
    return None


def _is_positive(url: str, label: str) -> bool:
    lo = f"{(label or '').lower()} {url.lower()}"
    if any(w in lo for w in NEG_WORDS):
        return False
    if any(w in lo for w in POS_WORDS):
        return True
    if any(h in lo for h in POS_HOST_HINTS):
        return True
    # Tillat .pdf hvis filnavn tyder på salgsoppgave/prospekt
    try:
        base = urlparse(url).path.lower().rsplit("/", 1)[-1]
    except Exception:
        base = url.lower()
    return base.endswith(".pdf") and any(h in base for h in POS_FILENAME_HINTS)


def _gather_pdf_candidates(soup: BeautifulSoup, base_url: str) -> List[Tuple[str, str]]:
    """
    Samler KUN kandidater som med høy sannsynlighet er salgsoppgave/prospekt.
    """
    out: List[Tuple[str, str]] = []

    # 1) <a> med href/data-href/download
    if hasattr(soup, "find_all"):
        for a in soup.find_all("a"):
            if not isinstance(a, Tag):
                continue
            txt = (a.get_text(" ", strip=True) or "").strip()
            href_raw = a.get("href") or a.get("data-href") or a.get("download")
            href = _as_str(href_raw).strip()
            if not href:
                continue
            absu = _abs(base_url, href)
            if not absu:
                continue
            if _is_positive(absu, txt):
                out.append((absu, txt))

    # 2) Elementer med data-url/data-file/data-download
    if hasattr(soup, "find_all"):
        for el in soup.find_all(["button", "div", "span"]):
            if not isinstance(el, Tag):
                continue
            txt = (el.get_text(" ", strip=True) or "").strip()
            for attr in ("data-href", "data-url", "data-file", "data-download"):
                href = _as_str(el.get(attr)).strip()
                if not href:
                    continue
                absu = _abs(base_url, href)
                if not absu:
                    continue
                if _is_positive(absu, txt):
                    out.append((absu, txt))

    # 3) Regex i rå HTML (fanger *.pdf i script/data) – filtrert med _is_positive
    try:
        html = soup.decode()
    except Exception:
        html = ""
    for m in re.finditer(r'https?://[^\s"\'<>]+\.pdf(?:\?[^\s<>\'"]*)?', html, re.I):
        u = m.group(0)
        if u and _is_positive(u, ""):
            out.append((u, ""))

    # uniq
    seen: set[str] = set()
    uniq: List[Tuple[str, str]] = []
    for u, lbl in out:
        if u not in seen:
            uniq.append((u, lbl))
            seen.add(u)
    return uniq


def _score_candidate(url: str, label: str) -> int:
    s = (url or "").lower()
    lbl = (label or "").lower()
    sc = 0
    if any(
        w in (s + " " + lbl)
        for w in ("salgsoppgav", "prospekt", "utskriftsvennlig", "komplett")
    ):
        sc += 60
    if s.endswith(".pdf"):
        sc += 25
    # Typiske Aktiv-kilder / filnavn
    if any(h in s for h in POS_HOST_HINTS):
        sc += 20
    if any(h in s for h in POS_FILENAME_HINTS):
        sc += 20
    return sc


class AktivDriver(Driver):
    name = "aktiv"

    def matches(self, url: str) -> bool:
        return "aktiv.no" in (url or "").lower()

    def try_fetch(
        self, sess: requests.Session, page_url: str
    ) -> Tuple[bytes | None, str | None, dict]:
        dbg: Dict[str, Any] = {"driver": self.name, "step": "start", "driver_meta": {}}

        referer = page_url.rstrip("/")

        # 1) Hent HTML for annonse-/objektsiden
        try:
            r0 = _get(sess, referer, referer, SETTINGS.REQ_TIMEOUT)
            r0.raise_for_status()
            soup = BeautifulSoup(r0.text, "html.parser")
        except Exception as e:
            dbg["step"] = f"page_fetch_error:{type(e).__name__}"
            dbg["driver_meta"]["error"] = str(e)
            return None, None, dbg

        # 2) Finn KUN salgsoppgave/prospekt-kandidater
        candidates = _gather_pdf_candidates(soup, referer)
        if not candidates:
            dbg["step"] = "no_candidates"
            return None, None, dbg

        # 3) Prioriter beste kandidater
        candidates.sort(key=lambda p: _score_candidate(p[0], p[1]), reverse=True)
        dbg["driver_meta"]["candidates_preview"] = [u for (u, _t) in candidates[:6]]

        # 4) HEAD→GET med liten backoff + siste portvakt (negativliste)
        backoff = 0.6
        max_tries = 2

        def _blocked_by_negatives(u: str, headers: Mapping[str, str] | None) -> bool:
            name = (_content_filename(headers) or "").lower()
            lo = f"{u.lower()} {name}"
            return any(w in lo for w in NEG_WORDS)

        for url, label in candidates:
            try:
                h = _head(sess, url, referer, SETTINGS.REQ_TIMEOUT)
                final = str(h.url)
                ct = (h.headers.get("Content-Type") or "").lower()

                dbg["driver_meta"][f"head_{url}"] = {
                    "status": h.status_code,
                    "ct": h.headers.get("Content-Type"),
                    "final_url": final,
                }

                # Hvis HEAD ikke funker → prøv GET på opprinnelig URL
                if not h.ok and h.status_code not in (301, 302, 303, 307, 308):
                    final = url

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
                            "label": label,
                        }

                        if ok_pdf:
                            # Siste portvakt: ikke returnér hvis neg. ord i final URL eller filnavn
                            if _blocked_by_negatives(str(rr.url), rr.headers):
                                dbg["driver_meta"][f"blocked_{final}"] = "negative_term"
                                break  # prøv neste kandidat
                            dbg["step"] = "ok_salgsoppgave"
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
                        dbg["driver_meta"][f"get_err_{attempt}_{final}"] = str(e)
                        if attempt < max_tries:
                            time.sleep(backoff * attempt)
                            continue
                        break

            except Exception as e:
                dbg["driver_meta"][f"head_err_{url}"] = str(e)
                continue

        dbg["step"] = "no_pdf_confirmed"
        return None, None, dbg
