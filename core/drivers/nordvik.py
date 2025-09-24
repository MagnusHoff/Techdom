# core/drivers/nordvik.py
from __future__ import annotations

import re
import time
from typing import Dict, Any, Tuple, List, Optional
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup, Tag

from core.http_headers import BROWSER_HEADERS
from ..config import SETTINGS

PDF_MAGIC = b"%PDF-"

NEG_HINTS = (
    "nabolag",
    "nabolagsprofil",
    "contentassets/nabolaget",
    "energiattest",
    "egenerkl",
)

TR_TEXT_HINTS = (
    "tilstandsrapport",
    "boligsalgsrapport",
    "takst",
    "fidens",  # dukker ofte opp i S3-url
)

S3_HOST_HINT = "nordvik-vitec-documents"


def _looks_like_pdf(b: bytes | None) -> bool:
    return isinstance(b, (bytes, bytearray)) and b.startswith(PDF_MAGIC)


def _bad_hint(s: str | None) -> bool:
    lo = (s or "").lower()
    return any(h in lo for h in NEG_HINTS)


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


def _gather_pdf_candidates(soup: BeautifulSoup, base_url: str) -> List[tuple[str, str]]:
    """
    Returnerer [(url, label)] for alt som kan være TR/salgsoppgave hos Nordvik.
    Vi scorer senere; her samler vi både <a> og rå-HTML.
    """
    out: List[tuple[str, str]] = []

    # 1) DOM-elementer
    if hasattr(soup, "find_all"):
        for el in soup.find_all(["a", "button", "div", "span"]):
            if not isinstance(el, Tag):
                continue
            txt = (el.get_text(" ", strip=True) or "").strip()
            href = (
                el.get("href")
                or el.get("data-href")
                or el.get("data-url")
                or el.get("data-file")
                or ""
            ).strip()
            if not href:
                continue
            u = _abs(base_url, href)
            if not u:
                continue
            lo = (txt + " " + u.lower()).lower()
            if _bad_hint(lo):
                continue
            # Nordvik-dokumenter mangler ofte .pdf, men har /dokument/
            if (
                "/dokument/" in u
                or u.lower().endswith(".pdf")
                or any(k in lo for k in ("tilstandsrapport", "salgsoppgav", "prospekt"))
            ):
                out.append((u, txt))

    # 2) Regex i rå HTML (fanger /dokument/... og .pdf)
    try:
        html = soup.decode()
    except Exception:
        html = ""
    for m in re.finditer(r'https?://[^\s"\'<>]+', html or "", re.I):
        u = m.group(0)
        lo = u.lower()
        if _bad_hint(lo):
            continue
        if ("/dokument/" in lo) or lo.endswith(".pdf") or S3_HOST_HINT in lo:
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
    lo = (u + " " + (label or "")).lower()
    sc = 0
    if "tilstandsrapport" in lo:
        sc += 80
    if "/dokument/" in lo:
        sc += 60
    if lo.endswith(".pdf"):
        sc += 40
    if "salgsoppgav" in lo or "prospekt" in lo:
        sc += 15
    if S3_HOST_HINT in lo or "fidens" in lo:
        sc += 10
    return sc


class NordvikDriver:
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

        # 2) Finn kandidater
        cands = _gather_pdf_candidates(soup, referer)
        if not cands:
            dbg["step"] = "no_pdf_confirmed"
            dbg["meta"]["candidates"] = []
            return None, None, dbg

        # 3) Prioriter
        cands.sort(key=lambda x: _score_candidate(x[0], x[1]), reverse=True)
        dbg["meta"]["candidates"] = [u for (u, _t) in cands[:8]]

        # 4) HEAD/GET med korte retries
        backoff = 0.6
        max_tries = 2

        for url, label in cands:
            # HEAD
            try:
                h = _head(sess, url, referer, SETTINGS.REQ_TIMEOUT)
                final = str(h.url)
                ct = (h.headers.get("Content-Type") or "").lower()
                pdfish = ct.startswith("application/pdf") or final.lower().endswith(
                    ".pdf"
                )
            except Exception:
                final = url
                pdfish = False

            # GET
            for attempt in range(1, max_tries + 1):
                try:
                    t0 = time.monotonic()
                    rr = _get(sess, final, referer, SETTINGS.REQ_TIMEOUT)
                    elapsed_ms = int((time.monotonic() - t0) * 1000)
                    dbg.setdefault("driver_meta", {})[f"get_{attempt}_{final}"] = {
                        "status": rr.status_code,
                        "content_type": rr.headers.get("Content-Type"),
                        "content_length": rr.headers.get("Content-Length"),
                        "elapsed_ms": elapsed_ms,
                        "final_url": str(rr.url),
                        "bytes": len(rr.content or b""),
                    }
                    if rr.ok and (
                        (
                            "application/pdf"
                            in (rr.headers.get("Content-Type") or "").lower()
                        )
                        or _looks_like_pdf(rr.content)
                    ):
                        dbg["step"] = "ok_direct"
                        # Er dette sannsynligvis en ren TR?
                        lo_all = (final + " " + url + " " + label).lower()
                        is_tr = ("tilstandsrapport" in lo_all) or (
                            "/dokument/" in final
                            and "salgsoppgav" not in lo_all
                            and "prospekt" not in lo_all
                        )
                        dbg["meta"]["is_tilstandsrapport"] = bool(is_tr)
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
