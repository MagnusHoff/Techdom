# core/drivers/proaktiv.py
from __future__ import annotations

import time
import re
import io
import requests
from typing import Dict, Any, Tuple, List, Optional
from bs4 import BeautifulSoup, Tag
from urllib.parse import urlparse, urljoin

from .base import Driver
from techdom.ingestion.http_headers import BROWSER_HEADERS
from techdom.infrastructure.config import SETTINGS

PDF_MAGIC = b"%PDF-"

# Kilder hvor prospekt/vedlegg ofte ligger
ALLOW_PDF_HOST_HINTS = (
    "cdn.webmegler.no",
    "webmegler.no",
    "azureedge.net",
    "cloudfront.net",
    "blob.core.windows.net",
    "proaktiv.no/media/",
)

# Kun salgsoppgave/prospekt
POSITIVE_WORDS = (
    "prospekt",
    "salgsoppgav",  # salgsoppgave / salgsoppgaven
    "digital_salgsoppgave",
    "digital-salgsoppgave",
    "utskriftsvennlig",
    "komplett",
)

# Alt dette skal bort
NEGATIVE_WORDS = (
    "tilstandsrapport",
    "tilstandsgrader",
    "bygningssakkyndig",
    "nøkkeltakst",
    "boligsalgsrapport",
    "energiattest",
    "energimerke",
    "nabolag",
    "nabolagsprofil",
    "egenerkl",
    "budskjema",
    "kjøpekontrakt",
    "vilkår",
    "terms",
    "cookies",
)

# Innholdsjekk for TR
TR_CUES = (
    "tilstandsrapport",
    "tilstandsgrader",
    "bygningssakkyndig",
    "nøkkeltakst",
    "boligsalgsrapport",
    "ns 3600",
    "ns-3600",
    "ns_3600",
)

MIN_PAGES = 6
MIN_BYTES = 200_000  # moderat terskel


def _as_str(v: object) -> str:
    """Trygg konvertering av BS4-attributtverdi til str."""
    if isinstance(v, str):
        return v
    if isinstance(v, (list, tuple)) and v and isinstance(v[0], str):
        return v[0]
    return ""


def _looks_like_pdf(b: Optional[bytes]) -> bool:
    return isinstance(b, (bytes, bytearray)) and b.startswith(PDF_MAGIC)


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
    if any(w in lo for w in POSITIVE_WORDS):
        sc += 40
    return sc


def _allowed(label: str, url: str) -> bool:
    lo = f"{label} {url}".lower()
    if any(w in lo for w in NEGATIVE_WORDS):
        return False
    # krever tydelig salgsoppgave-signal i label/URL
    return any(w in lo for w in POSITIVE_WORDS)


def _gather_pdf_candidates(soup: BeautifulSoup, base_url: str) -> List[str]:
    urls: List[str] = []

    if hasattr(soup, "find_all"):
        # <a>
        for a in soup.find_all("a"):
            if not isinstance(a, Tag):
                continue
            txt = a.get_text(" ", strip=True) or ""
            raw = a.get("href") or a.get("data-href") or a.get("download") or ""
            href = _as_str(raw).strip()
            if not href:
                continue
            absu = _abs(base_url, href)
            if not absu:
                continue
            if _allowed(txt, absu):
                urls.append(absu)

        # buttons/divs/spans
        for el in soup.find_all(["button", "div", "span"]):
            if not isinstance(el, Tag):
                continue
            txt = el.get_text(" ", strip=True) or ""
            for attr in ("data-href", "data-file", "data-url", "data-download"):
                raw = el.get(attr) or ""
                href = _as_str(raw).strip()
                if not href:
                    continue
                absu = _abs(base_url, href)
                if absu and _allowed(txt, absu):
                    urls.append(absu)

    # Regex i rå HTML – kun hvis positive hint, og ingen negative
    try:
        html = soup.decode()
    except Exception:
        html = ""

    for m in re.finditer(r'https?://[^\s"\'<>]+\.pdf(?:\?[^\s<>\'"]*)?', html, re.I):
        u = m.group(0)
        if u and _allowed("", u):
            urls.append(u)

    # webmegler-ashx (uten .pdf) – kun dersom positive hint finnes rundt
    for m in re.finditer(
        r'https?://[^\s"\'<>]*webmegler\.no/[^\s"\'<>]*wngetfile\.ashx\?[^\s<>\'"]+',
        html,
        re.I,
    ):
        u = m.group(0)
        if u and _allowed("", u):
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


def _first_pages_text(b: bytes, n: int = 3) -> str:
    try:
        from PyPDF2 import PdfReader

        rdr = PdfReader(io.BytesIO(b))
        pages = rdr.pages[: min(n, len(rdr.pages))]
        return "\n".join([(p.extract_text() or "") for p in pages]).lower()
    except Exception:
        return ""


def _looks_like_tr_pdf(b: bytes) -> bool:
    if not _looks_like_pdf(b):
        return False
    txt = _first_pages_text(b, 3)
    return any(w in txt for w in TR_CUES)


def _looks_like_prospect_pdf(b: bytes, url: str | None) -> bool:
    if not _looks_like_pdf(b):
        return False
    if not b or len(b) < MIN_BYTES:
        return False
    # min. sider
    try:
        from PyPDF2 import PdfReader

        n_pages = len(PdfReader(io.BytesIO(b)).pages)
    except Exception:
        n_pages = 0
    if n_pages < MIN_PAGES:
        return False
    lo = (url or "").lower()
    if any(w in lo for w in NEGATIVE_WORDS):
        return False
    # innhold skal ikke ha TR-cues
    return not _looks_like_tr_pdf(b)


class ProaktivDriver(Driver):
    name = "proaktiv"

    def matches(self, url: str) -> bool:
        return "proaktiv.no" in (url or "").lower()

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

        # Prioriter: (1) positive ord i URL/label, (2) kjente CDN/domener, (3) .pdf
        def _prio(u: str) -> tuple:
            lo = u.lower()
            return (
                0 if any(w in lo for w in POSITIVE_WORDS) else 1,
                0 if _domain_score(lo) > 0 else 1,
                0 if lo.endswith(".pdf") else 1,
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
                # aldri forsøk hvis URL har negative hint
                if any(w in final.lower() for w in NEGATIVE_WORDS):
                    continue
            except Exception:
                final = url
                is_pdfish = final.lower().endswith(".pdf") or _domain_score(final) > 0

            target = final if is_pdfish else url

            # GET (m/ små retries)
            for attempt in range(1, max_tries + 1):
                try:
                    t0 = time.monotonic()
                    rr = _get(sess, target, page_url, SETTINGS.REQ_TIMEOUT)
                    elapsed_ms = int((time.monotonic() - t0) * 1000)
                    ct2 = (rr.headers.get("Content-Type") or "").lower()
                    maybe_pdf = rr.ok and (
                        ("application/pdf" in ct2) or _looks_like_pdf(rr.content)
                    )
                    dbg["driver_meta"][f"get_{attempt}_{target}"] = {
                        "status": rr.status_code,
                        "content_type": rr.headers.get("Content-Type"),
                        "content_length": rr.headers.get("Content-Length"),
                        "elapsed_ms": elapsed_ms,
                        "final_url": str(rr.url),
                        "bytes": len(rr.content) if rr.content else 0,
                    }
                    if maybe_pdf and _looks_like_prospect_pdf(rr.content, str(rr.url)):
                        dbg["step"] = "ok_prospect"
                        return rr.content, str(rr.url), dbg

                    # Hvis det er PDF men ser ut som TR → hopp videre (ikke returnér)
                    if maybe_pdf and _looks_like_tr_pdf(rr.content or b""):
                        dbg.setdefault("meta", {})["skipped_tr_pdf"] = str(rr.url)
                        break

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
