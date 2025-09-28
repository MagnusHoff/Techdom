# core/drivers/boa.py
from __future__ import annotations

import re
import time
from typing import Dict, Any, Tuple, List, Optional, Mapping
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup, Tag

from core.http_headers import BROWSER_HEADERS
from ..config import SETTINGS
from .base import Driver  # arver fra base

PDF_MAGIC = b"%PDF-"


def _as_str(v: Any) -> str:
    """Trygt konverter BeautifulSoup-attributt (kan være liste) til str."""
    if isinstance(v, str):
        return v
    if isinstance(v, (list, tuple)) and v and isinstance(v[0], str):
        return v[0]
    return ""


# --- policy: KUN salgsoppgave/prospekt ---
POS_WORDS = (
    "salgsoppgav",
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


def _looks_like_pdf(b: bytes | None) -> bool:
    return isinstance(b, (bytes, bytearray)) and b.startswith(PDF_MAGIC)


def _origin(u: str) -> str:
    try:
        p = urlparse(u)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""


def _abs(base: str, href: str | None) -> str | None:
    if not href:
        return None
    return urljoin(base, href)


def _content_disposition_filename(headers: Mapping[str, str] | None) -> str:
    if not headers:
        return ""
    cd = headers.get("Content-Disposition") or headers.get("content-disposition") or ""
    m = re.search(r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';]+)', cd)
    return (m.group(1) if m else "").strip()


def _is_salgsoppgave_only(
    url: str, label: str = "", headers: Mapping[str, str] | None = None
) -> bool:
    """
    Siste portvakt: URL + ev. label + Content-Disposition-filenavn må
    ikke inneholde negative hint, og bør ha positive hint dersom mulig.
    """
    fn = _content_disposition_filename(headers).lower()
    blob = f"{url.lower()} {label.lower()} {fn}"
    if any(w in blob for w in NEG_WORDS):
        return False
    # godta .pdf selv uten positive ord, men foretrekk positive hvis tilstede
    if any(w in blob for w in POS_WORDS):
        return True
    return url.lower().endswith(".pdf")


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


def _gather_pdf_candidates(soup: BeautifulSoup, base_url: str) -> List[tuple[str, str]]:
    """
    Returner [(url, label)] for lenker som sannsynligvis er salgsoppgave/prospekt.
    Filtrer ut TR/energiattest/nabolag/egenerkl/etc.
    """
    out: List[tuple[str, str]] = []

    # 1) <a> m/ href/data-href/download
    for a in soup.find_all("a"):
        if not isinstance(a, Tag):
            continue
        label = (a.get_text(" ", strip=True) or "").strip()
        href = _as_str(a.get("href") or a.get("data-href") or a.get("download")).strip()
        if not href:
            continue
        u = _abs(base_url, href)
        if not u:
            continue
        blob = f"{label.lower()} {u.lower()}"
        if any(w in blob for w in NEG_WORDS):
            continue
        if u.lower().endswith(".pdf") or any(w in blob for w in POS_WORDS):
            out.append((u, label))

    # 2) knapper/div/span med data-*
    for el in soup.find_all(["button", "div", "span"]):
        if not isinstance(el, Tag):
            continue
        label = (el.get_text(" ", strip=True) or "").strip()
        for attr in ("data-href", "data-url", "data-file", "data-download"):
            raw = _as_str(el.get(attr)).strip()
            if not raw:
                continue
            u = _abs(base_url, raw)
            if not u:
                continue
            blob = f"{label.lower()} {u.lower()}"
            if any(w in blob for w in NEG_WORDS):
                continue
            if u.lower().endswith(".pdf") or any(w in blob for w in POS_WORDS):
                out.append((u, label))

    # 3) Regex i rå HTML – kun slipp til hvis positive hint og ikke negative
    try:
        html = soup.decode()
    except Exception:
        html = ""
    for m in re.finditer(r'https?://[^\s"\'<>]+\.pdf(?:\?[^\s<>\'"]*)?', html, re.I):
        u = m.group(0)
        lo = u.lower()
        if any(w in lo for w in NEG_WORDS):
            continue
        if lo.endswith(".pdf") or any(w in lo for w in POS_WORDS):
            out.append((u, ""))

    # uniq
    seen: set[str] = set()
    uniq: List[tuple[str, str]] = []
    for u, lbl in out:
        if u not in seen:
            uniq.append((u, lbl))
            seen.add(u)
    return uniq


def _score(u: str, label: str) -> int:
    s = (u or "").lower()
    lbl = (label or "").lower()
    sc = 0
    if s.endswith(".pdf"):
        sc += 30
    if any(w in s or w in lbl for w in POS_WORDS):
        sc += 50
    if any(w in s or w in lbl for w in ("dokument", "vedlegg")):
        sc += 8
    # hard straff for negative ord (sikkerhet)
    if any(w in s or w in lbl for w in NEG_WORDS):
        sc -= 200
    return sc


class BoaDriver(Driver):
    name = "boa"

    def matches(self, url: str) -> bool:
        u = (url or "").lower()
        # boaeiendom.no (inkl evt subdomener)
        return "boaeiendom.no" in u

    def try_fetch(
        self, sess: requests.Session, page_url: str
    ) -> Tuple[bytes | None, str | None, dict]:
        dbg: Dict[str, Any] = {
            "driver": self.name,
            "step": "start",
            "driver_meta": {"page_url": page_url},
        }

        # 1) Hent megler-side
        try:
            r = _get(sess, page_url, page_url, SETTINGS.REQ_TIMEOUT)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            dbg["step"] = f"page_fetch_error:{type(e).__name__}"
            dbg["driver_meta"]["error"] = str(e)
            return None, None, dbg

        # 2) Finn kandidater (kun salgsoppgave/prospekt)
        cands = _gather_pdf_candidates(soup, page_url)
        if not cands:
            dbg["step"] = "no_candidates"
            return None, None, dbg

        cands.sort(key=lambda x: _score(x[0], x[1]), reverse=True)
        dbg["driver_meta"]["candidates"] = [u for u, _ in cands[:8]]

        # 3) HEAD→GET med liten backoff
        backoff, max_tries = 0.5, 2

        for url, label in cands:
            # HEAD
            try:
                h = _head(sess, url, page_url, SETTINGS.REQ_TIMEOUT)
                final = str(h.url)
                ct = (h.headers.get("Content-Type") or "").lower()

                dbg["driver_meta"][f"head_{url}"] = {
                    "status": h.status_code,
                    "ct": h.headers.get("Content-Type"),
                    "final_url": final,
                }

                pdfish = ct.startswith("application/pdf") or final.lower().endswith(
                    ".pdf"
                )
                if (
                    not pdfish
                    and not h.ok
                    and h.status_code not in (301, 302, 303, 307, 308)
                ):
                    # hvis HEAD blokkeres/ikke nyttig, prøv GET på original-URL
                    final = url

                # GET (med 1–2 forsøk)
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

                        if ok_pdf and _is_salgsoppgave_only(
                            str(rr.url), label, rr.headers
                        ):
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
                        dbg["driver_meta"][f"get_err_{attempt}_{final}"] = str(e)
                        if attempt < max_tries:
                            time.sleep(backoff * attempt)
                            continue
                        break

            except Exception as e:
                dbg["driver_meta"][f"head_err_{url}"] = str(e)
                # Fallback: prøv direkte GET under uansett

            # 4) Fallback: direkte GET uten ny HEAD
            for attempt in range(1, max_tries + 1):
                try:
                    t0 = time.monotonic()
                    rr = _get(sess, url, page_url, SETTINGS.REQ_TIMEOUT)
                    elapsed_ms = int((time.monotonic() - t0) * 1000)
                    ct2 = (rr.headers.get("Content-Type") or "").lower()
                    ok_pdf = rr.ok and (
                        ("application/pdf" in ct2) or _looks_like_pdf(rr.content)
                    )
                    dbg["driver_meta"][f"fallback_get_{attempt}_{url}"] = {
                        "status": rr.status_code,
                        "content_type": rr.headers.get("Content-Type"),
                        "content_length": rr.headers.get("Content-Length"),
                        "elapsed_ms": elapsed_ms,
                        "final_url": str(rr.url),
                        "bytes": len(rr.content) if rr.content else 0,
                    }
                    if ok_pdf and _is_salgsoppgave_only(str(rr.url), label, rr.headers):
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
                    dbg["driver_meta"][f"fallback_get_err_{attempt}_{url}"] = str(e)
                    if attempt < max_tries:
                        time.sleep(backoff * attempt)
                        continue
                    break

        dbg["step"] = "no_pdf_confirmed"
        return None, None, dbg
