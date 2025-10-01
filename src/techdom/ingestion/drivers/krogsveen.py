# core/drivers/krogsveen.py
from __future__ import annotations

import io
import re
import time
from typing import Tuple, Dict, Any, Optional, List, Mapping
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag
from PyPDF2 import PdfReader

from .base import Driver
from techdom.ingestion.http_headers import BROWSER_HEADERS
from techdom.infrastructure.config import SETTINGS

PDF_MAGIC = b"%PDF-"

# --- bare salgsoppgave/prospekt ---
ALLOW_RX = re.compile(r"(salgsoppgav|prospekt|utskriftsvennlig|komplett)", re.I)
BLOCK_RX = re.compile(
    r"(tilstandsrapport|boligsalgsrapport|ns[\s_-]*3600|energiattest|egenerkl|"
    r"nabolag|takst|boligselgerforsikring|bud|budskjema|vedtekter|"
    r"arsberetning|årsberetning|regnskap|sameie|kontrakt|kjopetilbud)",
    re.I,
)

MIN_BYTES = 300_000
MIN_PAGES = 4


def _looks_like_pdf(b: bytes | None) -> bool:
    return isinstance(b, (bytes, bytearray)) and b.startswith(PDF_MAGIC)


def _pdf_pages(b: bytes | None) -> int:
    if not b:
        return 0
    try:
        return len(PdfReader(io.BytesIO(b)).pages)
    except Exception:
        return 0


def _pdf_quality_ok(b: bytes | None) -> bool:
    if not b or not _looks_like_pdf(b) or len(b) < MIN_BYTES:
        return False
    return _pdf_pages(b) >= MIN_PAGES


def _content_filename(headers: Mapping[str, str] | None) -> str:
    if not headers:
        return ""
    cd = headers.get("Content-Disposition") or headers.get("content-disposition") or ""
    m = re.search(r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';]+)', cd)
    return (m.group(1) if m else "").strip()


def _is_salgsoppgave(
    url: str, headers: Mapping[str, str] | None, label: str = ""
) -> bool:
    lo = (url or "").lower()
    fn = (_content_filename(headers) or "").lower()
    hay = " ".join([lo, fn, (label or "").lower()])
    if BLOCK_RX.search(hay):
        return False
    return bool(ALLOW_RX.search(hay))


def _abs(base: str, href: str | None) -> Optional[str]:
    return urljoin(base, href) if href else None


def _head(
    sess: requests.Session, url: str, referer: str, timeout: int
) -> requests.Response:
    headers = dict(BROWSER_HEADERS)
    # forsøk å sette Origin (kan hjelpe mot WAF)
    try:
        pr = urlparse(referer)
        origin = f"{pr.scheme}://{pr.netloc}"
    except Exception:
        origin = None
    headers.update(
        {
            "Accept": "application/pdf,application/octet-stream,*/*",
            "Referer": referer,
        }
    )
    if origin:
        headers["Origin"] = origin
    return sess.head(url, headers=headers, timeout=timeout, allow_redirects=True)


def _get(
    sess: requests.Session, url: str, referer: str, timeout: int
) -> requests.Response:
    headers = dict(BROWSER_HEADERS)
    try:
        pr = urlparse(referer)
        origin = f"{pr.scheme}://{pr.netloc}"
    except Exception:
        origin = None
    headers.update(
        {
            "Accept": "application/pdf,application/octet-stream,*/*",
            "Referer": referer,
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
        }
    )
    if origin:
        headers["Origin"] = origin
    return sess.get(url, headers=headers, timeout=timeout, allow_redirects=True)


def _find_candidates(html: str, base_url: str) -> List[tuple[str, str]]:
    """
    Finn lenker som sannsynligvis peker til salgsoppgave/prospekt (ikke TR).
    Tar med Sanity-CDN dersom label/URL matcher ALLOW_RX.
    Returnerer liste av (url, label).
    """
    out: List[tuple[str, str]] = []
    soup = BeautifulSoup(html or "", "html.parser")

    # 1) Tekstnære lenker/knapper (salgsoppgave/prospekt osv.)
    for el in soup.find_all(["a", "button"]):
        if not isinstance(el, Tag):
            continue
        label = (el.get_text(" ", strip=True) or "").lower()
        for attr in ("href", "data-href", "data-url", "data-download"):
            href = el.get(attr)
            if not href:
                continue
            u = _abs(base_url, str(href))
            if not u:
                continue
            # filtrer KUN salgsoppgave-kandidater
            if _is_salgsoppgave(u, None, label):
                out.append((u, label))

    # 2) Direkte sanity-URLer hvor som helst i HTML (men filtrer)
    for m in re.finditer(
        r"https?://cdn\.sanity\.io/files/[^\s\"']+\.pdf", html or "", re.I
    ):
        u = m.group(0)
        if _is_salgsoppgave(u, None, ""):
            out.append((u, ""))

    # uniq, behold rekkefølge
    seen: set[str] = set()
    uniq: List[tuple[str, str]] = []
    for u, lbl in out:
        if u not in seen:
            uniq.append((u, lbl))
            seen.add(u)
    return uniq


class KrogsveenDriver(Driver):
    name = "krogsveen"

    def matches(self, url: str) -> bool:
        u = (url or "").lower()
        # Krogsveen-objektsider
        return "krogsveen.no" in u and ("/kjope/" in u or "/boliger-til-salgs" in u)

    def try_fetch(
        self, sess: requests.Session, page_url: str
    ) -> Tuple[bytes | None, str | None, dict]:
        dbg: Dict[str, Any] = {"driver": self.name, "step": "start", "meta": {}}
        timeout = SETTINGS.REQ_TIMEOUT

        # 1) Last objektsiden (med FINN som referer hjelper ofte)
        try:
            r = sess.get(
                page_url,
                headers={**BROWSER_HEADERS, "Referer": "https://www.finn.no/"},
                timeout=timeout,
                allow_redirects=True,
            )
            r.raise_for_status()
            html = r.text or ""
            base = str(r.url)
            dbg["meta"]["page_status"] = r.status_code
            dbg["meta"]["page_len"] = len(html)
        except Exception as e:
            dbg["step"] = f"page_fetch_error:{type(e).__name__}"
            dbg["error"] = repr(e)
            return None, None, dbg

        # 2) Kandidater (kun salgsoppgave/prospekt)
        raw_cands = _find_candidates(html, base)
        dbg["meta"]["candidates_preview"] = [u for u, _ in raw_cands[:5]]

        # 2b) Utvid non-PDF kandidat én gang (noen peker til visningsside → sanity-pdf)
        def _expand_once(u: str, label: str) -> List[tuple[str, str]]:
            if u.lower().endswith(".pdf"):
                return [(u, label)]
            try:
                rr = sess.get(
                    u,
                    headers={**BROWSER_HEADERS, "Referer": page_url},
                    timeout=timeout,
                    allow_redirects=True,
                )
                if rr.ok:
                    inner = _find_candidates(rr.text or "", str(rr.url))
                    # filtrert allerede, men behold label dersom inner er tomt
                    return inner or [(u, label)]
            except Exception:
                pass
            return [(u, label)]

        expanded: List[tuple[str, str]] = []
        for u, lbl in raw_cands:
            expanded.extend(_expand_once(u, lbl))

        # uniq igjen, legg .pdf først
        seen: set[str] = set()
        ordered: List[tuple[str, str]] = []
        for u, lbl in expanded:
            if u not in seen and u.lower().endswith(".pdf"):
                seen.add(u)
                ordered.append((u, lbl))
        for u, lbl in expanded:
            if u not in seen:
                seen.add(u)
                ordered.append((u, lbl))

        dbg["meta"]["expanded_preview"] = [u for u, _ in ordered[:5]]

        # 3) HEAD→GET, fallback GET; filtrer på salgsoppgave + kvalitet
        backoff, max_tries = 0.5, 2
        for url, label in ordered:
            # HEAD
            try:
                h = _head(sess, url, referer=page_url, timeout=timeout)
                ct = (h.headers.get("Content-Type") or "").lower()
                final = str(h.url)
                cd_name = _content_filename(h.headers)
                dbg.setdefault("downloads", []).append(
                    {
                        "kind": "HEAD",
                        "url": url,
                        "status": h.status_code,
                        "ct": h.headers.get("Content-Type"),
                        "final": final,
                        "cd_filename": cd_name,
                        "label": label,
                    }
                )
                # strengt filter: kun salgsoppgave/prospekt
                if not _is_salgsoppgave(final, h.headers, label):
                    continue
                pdfish = ("application/pdf" in ct) or final.lower().endswith(".pdf")
            except Exception:
                pdfish, final = False, url

            # GET-forsøk
            target = final if pdfish else url
            for attempt in range(1, max_tries + 1):
                try:
                    rr = _get(sess, target, referer=page_url, timeout=timeout)
                    rec: Dict[str, Any] = {
                        "kind": "GET",
                        "attempt": attempt,
                        "url": target,
                        "status": rr.status_code,
                        "ct": rr.headers.get("Content-Type"),
                        "len": len(rr.content or b""),
                        "final": str(rr.url),
                        "cd_filename": _content_filename(rr.headers),
                        "label": label,
                    }
                    dbg.setdefault("downloads", []).append(rec)

                    # dobbeltsjekk: fortsatt salgsoppgave?
                    if not _is_salgsoppgave(str(rr.url), rr.headers, label):
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

                    if rr.ok and _pdf_quality_ok(rr.content):
                        dbg["step"] = "ok_pdf"
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
