# core/drivers/eie.py
from __future__ import annotations

import re, time
from typing import Any, Dict, List, Tuple, Optional
import requests
from bs4 import BeautifulSoup, Tag
from urllib.parse import urljoin, urlparse

from .base import Driver
from core.http_headers import BROWSER_HEADERS
from ..config import SETTINGS

REQ_TIMEOUT: int = int(getattr(SETTINGS, "REQ_TIMEOUT", 25))

PDF_MAGIC = b"%PDF-"


def _looks_like_pdf(b: bytes | None) -> bool:
    return isinstance(b, (bytes, bytearray)) and b.startswith(PDF_MAGIC)


# Eie pleier å servere salgsoppgave via Eiendomssentralen-CDN
ALLOW_HOSTS = ("cdn.eiendomssentralen.no",)
# Aksepterte navn
MUST_HAVE_NAME = ("salgsoppgave", "digital_salgsoppgave", "prospekt")
# Ekskluder åpenbart feil dokumenter/flows
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
BLOCK_PATH_HINTS = ("/files/", "/media/other/")
BLOCK_URL_PARTS = (
    "registrer",
    "logg-inn",
)  # gated flows etc. (beholdt, men ikke /digital/)


def _origin(u: str) -> str:
    try:
        p = urlparse(u)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""


def _abs(base: str, href: str | None) -> Optional[str]:
    return urljoin(base, href) if href else None


def _as_str(v: Any) -> str:
    """Normalize BeautifulSoup _AttributeValue (str | list[str] | None) to str."""
    if isinstance(v, str):
        return v
    if isinstance(v, (list, tuple)) and v and isinstance(v[0], str):
        return v[0]
    return ""


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


# -------- kandidatinnsamling (kun Salgsoppgave/Prospekt) ------------------------
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

    # må ha salgsoppgave-/prospekt-signal
    has_name = any(k in lbl for k in MUST_HAVE_NAME) or any(
        k in s for k in MUST_HAVE_NAME
    )
    if not has_name:
        return False

    # domenekrav: tillat Eiendomssentralen umiddelbart,
    # men godta også andre hoster når vi har tydelig navn
    host = urlparse(s).netloc
    if host in ALLOW_HOSTS:
        return True
    return True  # har_name allerede verifisert; ikke begrens på host


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
            out.append((u, (label or "").strip().lower()))

    # <a>
    for a in soup.find_all("a"):
        if not isinstance(a, Tag):
            continue
        label = _as_str(a.get_text(" ", strip=True) or "").strip()
        href = _as_str(a.get("href") or a.get("data-href") or a.get("download")).strip()
        add(href, label)

    # knapper/div/span
    for el in soup.find_all(["button", "div", "span"]):
        if not isinstance(el, Tag):
            continue
        label = _as_str(el.get_text(" ", strip=True) or "").strip()
        for attr in ("data-href", "data-url", "data-file", "data-download"):
            raw = _as_str(el.get(attr)).strip()
            if raw:
                add(raw, label)

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
    if ("salgsoppgave" in lbl) or ("prospekt" in lbl) or ("prospekt" in s):
        sc += 40
    if urlparse(s).netloc in ALLOW_HOSTS:
        sc += 30
    if "vedlegg" in lbl:
        sc += 5
    return sc


# -------- driver ----------------------------------------------------------------
class EieDriver(Driver):
    name = "eie"

    def matches(self, url: str) -> bool:
        return "eie.no" in (url or "").lower()

    def try_fetch(
        self, sess: requests.Session, page_url: str
    ) -> Tuple[bytes | None, str | None, dict]:
        dbg: Dict[str, Any] = {"driver": self.name, "step": "start", "driver_meta": {}}

        # Hent objektsiden
        try:
            r0 = _get(sess, page_url, page_url, REQ_TIMEOUT)
            r0.raise_for_status()
            soup = BeautifulSoup(r0.text, "html.parser")
        except Exception as e:
            dbg["step"] = f"page_fetch_error:{type(e).__name__}"
            dbg["driver_meta"]["error"] = str(e)
            return None, None, dbg

        # Finn salgsoppgave-kandidater
        cands = _gather_salgsoppgave_candidates(soup, page_url)
        if not cands:
            dbg["step"] = "no_candidates"
            return None, None, dbg

        # Prioriter “digital_salgsoppgave” og CDN-host
        cands.sort(key=lambda p: _score(p[0], p[1]), reverse=True)

        # HEAD→GET med liten backoff
        backoff, max_tries = 0.6, 2
        for url, label in cands:
            # 1) HEAD
            try:
                h = _head(sess, url, page_url, REQ_TIMEOUT)
                ct = (h.headers.get("Content-Type") or "").lower()
                final = str(h.url)
                if h.ok and (
                    ct.startswith("application/pdf") or final.lower().endswith(".pdf")
                ):
                    # 2) GET
                    for attempt in range(1, max_tries + 1):
                        try:
                            t0 = time.monotonic()
                            rr = _get(sess, final, page_url, REQ_TIMEOUT)
                            elapsed = int((time.monotonic() - t0) * 1000)
                            ct2 = (rr.headers.get("Content-Type") or "").lower()
                            ok = rr.ok and (
                                ("application/pdf" in ct2)
                                or _looks_like_pdf(rr.content)
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
                        except requests.RequestException as e:
                            dbg["driver_meta"][f"get_err_{attempt}_{final}"] = str(e)
                            if attempt < max_tries:
                                time.sleep(backoff * attempt)
                                continue
                            break
            except Exception as e:
                dbg["driver_meta"][f"head_err_{url}"] = str(e)

            # 3) Fallback: direkte GET uten HEAD
            for attempt in range(1, max_tries + 1):
                try:
                    t0 = time.monotonic()
                    rr = _get(sess, url, page_url, REQ_TIMEOUT)
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
                except requests.RequestException as e:
                    dbg["driver_meta"][f"get_err_{attempt}_{url}"] = str(e)
                    if attempt < max_tries:
                        time.sleep(backoff * attempt)
                        continue
                    break

        dbg["step"] = "no_pdf_confirmed"
        return None, None, dbg
