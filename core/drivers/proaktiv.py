# core/drivers/proaktiv.py
from __future__ import annotations

import time
import re
import io
import requests
from typing import Dict, Any, Tuple, List
from bs4 import BeautifulSoup, Tag
from urllib.parse import urlparse, urljoin
from core.http_headers import BROWSER_HEADERS
from ..config import SETTINGS

PDF_MAGIC = b"%PDF-"

ALLOW_PDF_HOST_HINTS = (
    "cdn.webmegler.no",
    "webmegler.no",
    "azureedge.net",
    "cloudfront.net",
    "blob.core.windows.net",
    "proaktiv.no/media/",
)

PROSPEKT_WORDS = ("prospekt", "salgsoppgav")
TR_CUES = (
    "tilstandsrapport",
    "tilstandsgrader",
    "bygningssakkyndig",
    "nøkkeltakst",
    "boligsalgsrapport",  # eldre begrep noen ganger i bruk
)


def _looks_like_pdf(b: bytes) -> bool:
    return b.startswith(PDF_MAGIC)


def _abs(base_url: str, href: str | None) -> str | None:
    if not href:
        return None
    try:
        return urljoin(base_url, href)
    except Exception:
        return None


def _mk_headers(referer: str) -> Dict[str, str]:
    pr = urlparse(referer)
    origin = f"{pr.scheme}://{pr.netloc}" if pr.scheme and pr.netloc else ""
    h = dict(BROWSER_HEADERS)
    h.update(
        {
            "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
            "Referer": referer,
        }
    )
    if origin:
        h["Origin"] = origin
    return h


def _get(
    sess: requests.Session, url: str, referer: str, timeout: int
) -> requests.Response:
    return sess.get(
        url, headers=_mk_headers(referer), timeout=timeout, allow_redirects=True
    )


def _head(
    sess: requests.Session, url: str, referer: str, timeout: int
) -> requests.Response:
    return sess.head(
        url, headers=_mk_headers(referer), timeout=timeout, allow_redirects=True
    )


def _domain_score(u: str) -> int:
    lo = u.lower()
    for hint in ALLOW_PDF_HOST_HINTS:
        if hint in lo:
            return 20
    return 0


def _anchor_score(text: str) -> int:
    lo = (text or "").lower()
    sc = 0
    if any(w in lo for w in PROSPEKT_WORDS):
        sc += 25
    if "dokument" in lo or "vedlegg" in lo:
        sc += 8
    return sc


def _gather_pdf_candidates(soup: BeautifulSoup, base_url: str) -> List[str]:
    urls: List[str] = []

    if hasattr(soup, "find_all"):
        # <a>
        for a in soup.find_all("a"):
            if not isinstance(a, Tag):
                continue
            txt = a.get_text(" ", strip=True) or ""
            href = (
                a.get("href") or a.get("data-href") or a.get("download") or ""
            ).strip()
            if not href:
                continue
            absu = _abs(base_url, href)
            if not absu:
                continue
            lo = absu.lower()
            if lo.endswith(".pdf") or _domain_score(lo) > 0 or _anchor_score(txt) > 0:
                urls.append(absu)

        # buttons/divs/spans
        for el in soup.find_all(["button", "div", "span"]):
            if not isinstance(el, Tag):
                continue
            txt = el.get_text(" ", strip=True) or ""
            for attr in ("data-href", "data-file", "data-url", "data-download"):
                href = (el.get(attr) or "").strip()
                if not href:
                    continue
                absu = _abs(base_url, href)
                if not absu:
                    continue
                lo = absu.lower()
                if (
                    lo.endswith(".pdf")
                    or _domain_score(lo) > 0
                    or _anchor_score(txt) > 0
                ):
                    urls.append(absu)

    # Regex i rå HTML
    try:
        html = soup.decode()
    except Exception:
        html = ""

    # .pdf
    for m in re.finditer(r'https?://[^\s"\'<>]+\.pdf(?:\?[^\s<>\'"]*)?', html, re.I):
        u = m.group(0)
        if u:
            urls.append(u)

    # webmegler-ashx uten .pdf
    for m in re.finditer(
        r'https?://[^\s"\'<>]*webmegler\.no/[^\s"\'<>]*wngetfile\.ashx\?[^\s<>\'"]+',
        html,
        re.I,
    ):
        u = m.group(0)
        if u:
            urls.append(u)

    # uniq
    seen: set[str] = set()
    uniq: List[str] = []
    for u in urls:
        if u not in seen:
            uniq.append(u)
            seen.add(u)
    return uniq


def _harvest_from_url(sess: requests.Session, url: str, dbg: dict) -> List[str]:
    try:
        r = _get(sess, url, url, SETTINGS.REQ_TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        dbg.setdefault("driver_meta", {})
        dbg["driver_meta"][f"fetch_err:{url}"] = f"{type(e).__name__}"
        return []
    cands = _gather_pdf_candidates(soup, url)
    if cands:
        dbg.setdefault("driver_meta", {})
        dbg["driver_meta"][f"cands:{url}"] = cands[:8]
    return cands


def _looks_like_tr_pdf(b: bytes) -> bool:
    # lett sniff: sjekk første ~3 sider for TR-cues
    if not b or not _looks_like_pdf(b):
        return False
    try:
        from PyPDF2 import PdfReader

        rdr = PdfReader(io.BytesIO(b))
        pages = rdr.pages[: min(3, len(rdr.pages))]
        txt = " ".join([(p.extract_text() or "") for p in pages]).lower()
        return any(w in txt for w in TR_CUES)
    except Exception:
        # hvis vi ikke får tekst, ikke konkluder – la fetch håndtere klipp senere
        return False


class ProaktivDriver:
    name = "proaktiv"

    def matches(self, url: str) -> bool:
        return "proaktiv.no" in url.lower()

    def try_fetch(
        self, sess: requests.Session, page_url: str
    ) -> Tuple[bytes | None, str | None, dict]:
        dbg: Dict[str, Any] = {"driver": self.name, "step": "start", "driver_meta": {}}

        urls_to_scan = [
            page_url,
            page_url.rstrip("/") + "#dokumenter",
            page_url.rstrip("/") + "/salgsoppgave",
        ]

        candidates: List[str] = []
        for u in urls_to_scan:
            candidates.extend(_harvest_from_url(sess, u, dbg))

        # uniq, bevar rekkefølge
        seen: set[str] = set()
        uniq: List[str] = []
        for u in candidates:
            if u not in seen:
                uniq.append(u)
                seen.add(u)

        if not uniq:
            dbg["step"] = "no_candidates"
            return None, None, dbg

        # Prioriter rekkefølge:
        # 1) webmegler/CDN (ofte enkeltvedlegg som TR)
        # 2) andre .pdf
        # 3) resten
        def _prio(u: str) -> tuple:
            lo = u.lower()
            return (
                (
                    0
                    if "webmegler.no" in lo or _domain_score(lo) > 0
                    else (1 if lo.endswith(".pdf") else 2)
                ),
                0 if any(w in lo for w in ("prospekt", "salgsoppgave")) else 1,
                -len(u),
            )

        ordered = sorted(uniq, key=_prio)

        backoff = 0.6
        max_tries = 2

        for url in ordered:
            # HEAD
            try:
                h = _head(sess, url, page_url, SETTINGS.REQ_TIMEOUT)
                ct = (h.headers.get("Content-Type") or "").lower()
                final = str(h.url)
                is_pdfish = (
                    ("application/pdf" in ct)
                    or final.lower().endswith(".pdf")
                    or _domain_score(final) > 0
                )
            except Exception:
                final = url
                is_pdfish = final.lower().endswith(".pdf") or _domain_score(final) > 0

            for attempt in range(1, max_tries + 1):
                try:
                    t0 = time.monotonic()
                    rr = _get(sess, final, page_url, SETTINGS.REQ_TIMEOUT)
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
                        # Hvis dette ser ut som en TR, merk det – fetch hopper da over klipp.
                        meta: Dict[str, Any] = {}
                        if "webmegler" in final.lower() or _domain_score(final) > 0:
                            if _looks_like_tr_pdf(rr.content):
                                meta["is_tilstandsrapport"] = True
                                dbg["meta"] = meta
                                dbg["step"] = "ok_tr_direct"
                                return rr.content, str(rr.url), dbg
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

        dbg["step"] = "no_pdf_confirmed"
        return None, None, dbg
