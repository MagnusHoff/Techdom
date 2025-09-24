# core/drivers/eie.py
from __future__ import annotations

import re, time
from typing import Any, Dict, List, Tuple, Optional
import requests
from bs4 import BeautifulSoup, Tag
from urllib.parse import urljoin, urlparse
from core.http_headers import BROWSER_HEADERS
from ..config import SETTINGS

PDF_MAGIC = b"%PDF-"


def _looks_like_pdf(b: bytes) -> bool:
    return isinstance(b, (bytes, bytearray)) and b.startswith(PDF_MAGIC)


ALLOW_HOSTS = ("cdn.eiendomssentralen.no",)  # primær kilde
MUST_HAVE_NAME = ("salgsoppgave", "digital_salgsoppgave")  # label/url må treffe én
BLOCK_NAME = (
    "nabolagsprofil",
    "naboprofil",
    "energiattest",
    "anticimex",
    "boligselgerforsikring",
    "bud",
    "budskjema",
    "prisliste",
    "vilkår",
    "terms",
    "cookies",
)
BLOCK_PATH_HINTS = (
    "/files/",
    "/media/other/",
)  # typisk interne PDF-er som ikke er salgsoppgave
BLOCK_URL_PARTS = ("registrer", "logg-inn", "/digital/")  # gated flows


def _origin(u: str) -> str:
    try:
        p = urlparse(u)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""


def _abs(base: str, href: str | None) -> str | None:
    return urljoin(base, href) if href else None


def _get(
    sess: requests.Session, url: str, referer: str, timeout: int
) -> requests.Response:
    h = dict(BROWSER_HEADERS)
    h.update(
        {
            "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
            "Referer": referer,
            "Origin": _origin(referer) or _origin(url),
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-site",
        }
    )
    return sess.get(url, headers=h, timeout=timeout, allow_redirects=True)


def _head(
    sess: requests.Session, url: str, referer: str, timeout: int
) -> requests.Response:
    h = dict(BROWSER_HEADERS)
    h.update(
        {
            "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
            "Referer": referer,
            "Origin": _origin(referer) or _origin(url),
        }
    )
    return sess.head(url, headers=h, timeout=timeout, allow_redirects=True)


# -------- kandidatinnsamling (kun Salgsoppgave) --------------------------------
def _is_allowed(url: str, label: str) -> bool:
    s = url.lower()
    lbl = (label or "").lower()
    # blokkér tydelig feil
    if any(b in s for b in BLOCK_URL_PARTS):
        return False
    if any(b in s for b in BLOCK_PATH_HINTS):
        return False
    if any(b in lbl or b in s for b in BLOCK_NAME):
        return False
    # må ha salgsoppgave-signal
    has_name = any(k in lbl for k in MUST_HAVE_NAME) or any(
        k in s for k in MUST_HAVE_NAME
    )
    if not has_name:
        return False
    # domenekrav: helst CDN, ellers krever digital_salgsoppgave i URL
    host = urlparse(s).netloc
    if host in ALLOW_HOSTS:
        return True
    return "digital_salgsoppgave" in s


def _gather_salgsoppgave_candidates(
    soup: BeautifulSoup, base_url: str
) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []

    def add(href: str | None, label: str):
        if not href:
            return
        u = _abs(base_url, href)
        if not u:
            return
        # ta bare .pdf-lenker
        if not u.lower().endswith(".pdf"):
            return
        if _is_allowed(u, label):
            out.append((u, label.strip().lower()))

    # <a>
    for a in soup.find_all("a"):
        if not isinstance(a, Tag):
            continue
        label = a.get_text(" ", strip=True) or ""
        href = a.get("href") or a.get("data-href") or a.get("download") or ""
        add(href, label)

    # knapper/div/span
    for el in soup.find_all(["button", "div", "span"]):
        if not isinstance(el, Tag):
            continue
        label = el.get_text(" ", strip=True) or ""
        for attr in ("data-href", "data-url", "data-file", "data-download"):
            href = el.get(attr) or ""
            if href:
                add(href, label)

    # rå HTML (fanger script/JSON)
    try:
        html = soup.decode()
    except Exception:
        html = ""
    for m in re.finditer(r'https?://[^\s"\'<>]+\.pdf(?:\?[^\s<>\'"]*)?', html, re.I):
        u = m.group(0)
        if _is_allowed(u, ""):
            out.append((u, ""))

    # uniq
    seen: set[str] = set()
    uniq: List[Tuple[str, str]] = []
    for u, lbl in out:
        if u not in seen:
            uniq.append((u, lbl))
            seen.add(u)
    return uniq


def _score(url: str, label: str) -> int:
    s = url.lower()
    lbl = (label or "").lower()
    sc = 0
    if s.endswith(".pdf"):
        sc += 20
    if "digital_salgsoppgave" in s:
        sc += 60
    if "salgsoppgave" in lbl:
        sc += 40
    if urlparse(s).netloc in ALLOW_HOSTS:
        sc += 30
    # litt bonus hvis «vedlegg»-seksjon i label
    if "vedlegg" in lbl:
        sc += 5
    return sc


# -------- driver ----------------------------------------------------------------
class EieDriver:
    name = "eie"

    def matches(self, url: str) -> bool:
        return "eie.no" in url.lower()

    def try_fetch(
        self, sess: requests.Session, page_url: str
    ) -> Tuple[bytes | None, str | None, dict]:
        dbg: Dict[str, Any] = {"driver": self.name, "step": "start", "driver_meta": {}}

        try:
            r0 = _get(sess, page_url, page_url, SETTINGS.REQ_TIMEOUT)
            r0.raise_for_status()
            soup = BeautifulSoup(r0.text, "html.parser")
        except Exception as e:
            dbg["step"] = f"page_fetch_error:{type(e).__name__}"
            return None, None, dbg

        cands = _gather_salgsoppgave_candidates(soup, page_url)
        if not cands:
            dbg["step"] = "no_candidates"
            return None, None, dbg

        cands.sort(key=lambda p: _score(p[0], p[1]), reverse=True)

        backoff, max_tries = 0.6, 2
        for url, label in cands:
            # HEAD → bekreft PDF
            try:
                h = _head(sess, url, page_url, SETTINGS.REQ_TIMEOUT)
                ct = (h.headers.get("Content-Type") or "").lower()
                final = str(h.url)
                if h.ok and (
                    ct.startswith("application/pdf") or final.lower().endswith(".pdf")
                ):
                    for attempt in range(1, max_tries + 1):
                        t0 = time.monotonic()
                        rr = _get(sess, final, page_url, SETTINGS.REQ_TIMEOUT)
                        elapsed = int((time.monotonic() - t0) * 1000)
                        ct2 = (rr.headers.get("Content-Type") or "").lower()
                        ok = rr.ok and (
                            ("application/pdf" in ct2) or _looks_like_pdf(rr.content)
                        )
                        dbg["driver_meta"][f"get_{attempt}_{final}"] = {
                            "status": rr.status_code,
                            "content_type": rr.headers.get("Content-Type"),
                            "content_length": rr.headers.get("Content-Length"),
                            "elapsed_ms": elapsed,
                            "final_url": str(rr.url),
                            "bytes": len(rr.content) if rr.content else 0,
                            "label": label,
                        }
                        if ok:
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
            except Exception:
                pass

            # fallback GET (no HEAD)
            for attempt in range(1, max_tries + 1):
                try:
                    t0 = time.monotonic()
                    rr = _get(sess, url, page_url, SETTINGS.REQ_TIMEOUT)
                    elapsed = int((time.monotonic() - t0) * 1000)
                    ct2 = (rr.headers.get("Content-Type") or "").lower()
                    ok = rr.ok and (
                        ("application/pdf" in ct2) or _looks_like_pdf(rr.content)
                    )
                    dbg["driver_meta"][f"get_{attempt}_{url}"] = {
                        "status": rr.status_code,
                        "content_type": rr.headers.get("Content-Type"),
                        "content_length": rr.headers.get("Content-Length"),
                        "elapsed_ms": elapsed,
                        "final_url": str(rr.url),
                        "bytes": len(rr.content) if rr.content else 0,
                        "label": label,
                    }
                    if ok:
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
