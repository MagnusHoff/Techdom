# drivers/privatmegleren.py
from __future__ import annotations

import os
import time
import re
import json
import requests
from typing import Optional, Dict, Any, Tuple, List
from bs4 import BeautifulSoup, Tag
from urllib.parse import urlparse, urljoin

from core.http_headers import BROWSER_HEADERS
from ..config import SETTINGS

# Valgfritt: hvis du har lagt inn denne helperen i core.scrape,
# bruker vi den til å klippe ut kun "salgsoppgaven" fra vedleggs-PDF.
try:
    from core.scrape import refine_salgsoppgave_from_bundle  # type: ignore
except Exception:
    refine_salgsoppgave_from_bundle = None  # type: ignore[assignment]

PDF_MAGIC = b"%PDF-"

# Kjente "dårlige" PDF-er som ikke er salgsoppgave
PM_BAD_PDFS = {
    "https://privatmegleren.no/docs/klikk.pdf",
    "http://privatmegleren.no/docs/klikk.pdf",
}


def _is_blacklisted_pdf(url: str) -> bool:
    try:
        u = (url or "").split("#")[0]
        return u in PM_BAD_PDFS or u.lower().endswith("/docs/klikk.pdf")
    except Exception:
        return False


def _looks_like_pdf(b: bytes) -> bool:
    return isinstance(b, (bytes, bytearray)) and b.startswith(PDF_MAGIC)


def _abs(base_url: str, href: str | None) -> str | None:
    if not href:
        return None
    return urljoin(base_url, href)


def _origin_of(u: str) -> str:
    try:
        p = urlparse(u)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""


def _get(
    sess: requests.Session, url: str, referer: str, timeout: int
) -> requests.Response:
    headers = dict(BROWSER_HEADERS)
    origin = _origin_of(referer) or _origin_of(url)
    headers.update(
        {
            "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
            "Referer": referer,
            "Origin": origin,
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
    origin = _origin_of(referer) or _origin_of(url)
    headers.update(
        {
            "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
            "Referer": referer,
            "Origin": origin,
        }
    )
    return sess.head(url, headers=headers, timeout=timeout, allow_redirects=True)


# --- NEXT.js helpers ---
def _read_next_data(soup: BeautifulSoup) -> dict | None:
    tag = soup.find("script", id="__NEXT_DATA__")
    if not isinstance(tag, Tag):
        return None
    try:
        return json.loads(tag.string or "{}") or None
    except Exception:
        return None


def _walk(o: Any):
    if isinstance(o, dict):
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)
    elif isinstance(o, str):
        yield o


def _pdfs_from_next(blob: dict) -> List[str]:
    urls: List[str] = []
    for s in _walk(blob):
        if (
            isinstance(s, str)
            and s.lower().startswith(("http://", "https://"))
            and ".pdf" in s.lower()
        ):
            urls.append(s.replace("\\/", "/"))
    # uniq
    seen: set[str] = set()
    out: List[str] = []
    for u in urls:
        if u not in seen:
            out.append(u)
            seen.add(u)
    return out


def _try_buildid_fetch(
    sess: requests.Session, page_url: str, soup: BeautifulSoup, referer: str
) -> List[str]:
    """Prøv å hente /_next/data/{buildId}/{path}.json og skrape PDF-lenker."""
    pdfs: List[str] = []
    blob = _read_next_data(soup)
    build_id: Optional[str] = None
    if isinstance(blob, dict):
        bid = blob.get("buildId")
        if isinstance(bid, str):
            build_id = bid
    if not build_id:
        # fallback: sniffe fra html
        try:
            html = soup.decode()
        except Exception:
            html = ""
        m = re.search(r"/_next/static/([^/]+)/", html)
        if m:
            build_id = m.group(1)
    if not build_id:
        return pdfs

    try:
        path = urlparse(page_url).path.strip("/")
        data_url = f"https://www.privatmegleren.no/_next/data/{build_id}/{path}.json"
        r = _get(sess, data_url, referer, SETTINGS.REQ_TIMEOUT)
        if r.ok and "application/json" in (r.headers.get("Content-Type", "").lower()):
            blob2 = r.json()
            pdfs.extend(_pdfs_from_next(blob2))
    except Exception:
        pass
    return pdfs


def _gather_pdf_candidates(soup: BeautifulSoup, base_url: str) -> List[str]:
    urls: List[str] = []

    # 1) <a>, tekst + href
    if hasattr(soup, "find_all"):
        for a in soup.find_all("a"):
            if not isinstance(a, Tag):
                continue
            txt = (a.get_text(" ", strip=True) or "").lower()
            href = (
                a.get("href") or a.get("data-href") or a.get("download") or ""
            ).strip()
            if not href:
                continue
            absu = _abs(base_url, href)
            if not absu or _is_blacklisted_pdf(absu):
                continue
            lo = txt + " " + absu.lower()
            if any(
                k in lo
                for k in (
                    "salgsoppgav",
                    "prospekt",
                    "vedlegg",
                    "digitalformat",
                    "pdf",
                    "dokument",
                    "dokumenter",
                    "all informasjon",
                )
            ) or absu.lower().endswith(".pdf"):
                urls.append(absu)

    # 2) knapper/div/span med data-attributt
    if hasattr(soup, "find_all"):
        for el in soup.find_all(["button", "div", "span"]):
            if not isinstance(el, Tag):
                continue
            txt = (el.get_text(" ", strip=True) or "").lower()
            for attr in ("data-href", "data-file", "data-url", "data-download"):
                href = (el.get(attr) or "").strip()
                if not href:
                    continue
                absu = _abs(base_url, href)
                if not absu or _is_blacklisted_pdf(absu):
                    continue
                lo = txt + " " + absu.lower()
                if any(
                    k in lo
                    for k in (
                        "salgsoppgav",
                        "prospekt",
                        "vedlegg",
                        "digitalformat",
                        "pdf",
                        "dokument",
                        "dokumenter",
                        "all informasjon",
                    )
                ) or absu.lower().endswith(".pdf"):
                    urls.append(absu)

    # 3) Regex i rå HTML (fanger JSON i scripts også)
    try:
        html = soup.decode()
    except Exception:
        html = ""
    for m in re.finditer(r'https?://[^\s"\'<>]+\.pdf(?:\?[^\s<>\'"]*)?', html, re.I):
        u = m.group(0)
        if u:
            u = u.replace("\\/", "/")
            if not _is_blacklisted_pdf(u):
                urls.append(u)

    # uniq
    seen: set[str] = set()
    out: List[str] = []
    for u in urls:
        if u not in seen:
            out.append(u)
            seen.add(u)
    return out


def _is_gated(html_text: str) -> bool:
    """Heuristikk: skjema for å få salgsoppgaven (navn/telefon)."""
    lo = (html_text or "").lower()
    # typiske tekster og felt
    hints = [
        "få salgsoppgaven",
        "send salgsoppaven",
        "send salgsoppgaven",
        "motta salgsoppgaven",
        "salgsoppgaven på e-post",
        "navn",
        "telefon",
        "mobil",
        "postadresse",
        "samtykke",
    ]
    score = sum(1 for h in hints if h in lo)
    return score >= 3  # justér ved behov


# Bonus: løft riktige kandidater (objekt-ID og navn i URL), straff "klikk.pdf"
OBJ_ID_RX = re.compile(r"/(\d{6,})\b")


def _score_candidate(url: str, page_url: str) -> int:
    s = (url or "").lower()
    sc = 0
    if s.endswith(".pdf"):
        sc += 30
    if "salgsoppgav" in s or "prospekt" in s:
        sc += 30
    if "vedlegg" in s:
        sc += 15
    if "dokument" in s:
        sc += 10

    # bonus hvis URL inneholder samme objekt-ID som siden
    m = OBJ_ID_RX.search(page_url)
    if m and m.group(1) in s:
        sc += 40

    # straff for kjente dårlige
    base = os.path.basename(s)
    if base == "klikk.pdf" or "/docs/klikk.pdf" in s:
        sc -= 500

    return sc


class PrivatMeglerenDriver:
    name = "privatmegleren"

    def matches(self, url: str) -> bool:
        return "privatmegleren.no" in url.lower()

    def try_fetch(
        self, sess: requests.Session, page_url: str
    ) -> Tuple[bytes | None, str | None, dict]:
        dbg: Dict[str, Any] = {"driver": self.name, "step": "start", "driver_meta": {}}

        # Bygg varianter av URL som ofte inneholder dokumentseksjonen
        base = page_url.rstrip("/")
        variants = [
            base,
            base + "/salgsoppgave",
            base + "/dokumenter",
            base + "#salgsoppgave",
        ]

        backoff = 0.6
        max_tries = 2

        for view_url in variants:
            # 0) last side (cookies + markup)
            try:
                r0 = _get(sess, view_url, view_url, SETTINGS.REQ_TIMEOUT)
                r0.raise_for_status()
                html_text = r0.text
                soup = BeautifulSoup(html_text, "html.parser")
            except Exception as e:
                dbg.setdefault("driver_meta", {})[
                    f"fetch_err_{view_url}"
                ] = f"{type(e).__name__}"
                continue

            gated = _is_gated(html_text)
            dbg["driver_meta"][f"gated_{view_url}"] = gated

            # 1) NEXT-data: direkte PDF-lenker hvis mulig
            try:
                blob = _read_next_data(soup)
                if isinstance(blob, dict):
                    pdfs = _pdfs_from_next(blob)
                else:
                    pdfs = []
                if not pdfs:
                    # Prøv /_next/data/{buildId}/{path}.json
                    pdfs = _try_buildid_fetch(sess, view_url, soup, referer=view_url)
            except Exception:
                pdfs = []

            # 2) Vanlige kandidater fra DOM/script
            dom_pdfs = _gather_pdf_candidates(soup, view_url)

            # 3) Samle og prioriter (med scoring og blacklist-filter)
            candidates: List[str] = []
            for u in pdfs + dom_pdfs:
                if not u or _is_blacklisted_pdf(u):
                    continue
                if u not in candidates:
                    candidates.append(u)

            if not candidates:
                # ingen kandidater på denne varianten, gå videre
                continue

            candidates.sort(key=lambda u: _score_candidate(u, view_url), reverse=True)

            # 4) HEAD/GET m/ retry + timing
            for url in candidates:
                # Prøv HEAD
                try:
                    h = _head(sess, url, view_url, SETTINGS.REQ_TIMEOUT)
                    ct = (h.headers.get("Content-Type") or "").lower()
                    final = str(h.url)
                    if _is_blacklisted_pdf(final):
                        continue
                    if h.ok and (
                        ct.startswith("application/pdf")
                        or final.lower().endswith(".pdf")
                    ):
                        # Bekreft med GET (med små retries)
                        for attempt in range(1, max_tries + 1):
                            t0 = time.monotonic()
                            rr = _get(sess, final, view_url, SETTINGS.REQ_TIMEOUT)
                            elapsed_ms = int((time.monotonic() - t0) * 1000)
                            ct2 = (rr.headers.get("Content-Type") or "").lower()
                            ok_pdf = rr.ok and (
                                ("application/pdf" in ct2)
                                or _looks_like_pdf(rr.content)
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
                                # Hvis gated og dette ser ut som VEDLEGG, forsøk å rense
                                if (
                                    gated
                                    and "vedlegg" in final.lower()
                                    and refine_salgsoppgave_from_bundle
                                ):
                                    try:
                                        clean_bytes, meta = refine_salgsoppgave_from_bundle(rr.content)  # type: ignore[misc]
                                        if clean_bytes:
                                            dbg["driver_meta"]["refine"] = meta
                                            dbg["step"] = "ok_vedlegg_refined"
                                            return clean_bytes, final, dbg
                                    except Exception:
                                        pass
                                dbg["step"] = "ok_direct"
                                return rr.content, final, dbg
                            if attempt < max_tries and rr.status_code in (
                                429,
                                500,
                                502,
                                503,
                                504,
                            ):
                                time.sleep(0.5 * attempt)
                                continue
                            break
                except Exception:
                    pass

                # Fallback: direkte GET med retries
                for attempt in range(1, max_tries + 1):
                    try:
                        t0 = time.monotonic()
                        rr = _get(sess, url, view_url, SETTINGS.REQ_TIMEOUT)
                        elapsed_ms = int((time.monotonic() - t0) * 1000)
                        ct2 = (rr.headers.get("Content-Type") or "").lower()
                        ok_pdf = rr.ok and (
                            ("application/pdf" in ct2) or _looks_like_pdf(rr.content)
                        )
                        dbg["driver_meta"][f"get_{attempt}_{url}"] = {
                            "status": rr.status_code,
                            "content_type": rr.headers.get("Content-Type"),
                            "content_length": rr.headers.get("Content-Length"),
                            "elapsed_ms": elapsed_ms,
                            "final_url": str(rr.url),
                            "bytes": len(rr.content) if rr.content else 0,
                        }
                        if ok_pdf:
                            # Hvis gated og dette er VEDLEGG, prøv rensing
                            if (
                                gated
                                and "vedlegg" in url.lower()
                                and refine_salgsoppgave_from_bundle
                            ):
                                try:
                                    clean_bytes, meta = refine_salgsoppgave_from_bundle(rr.content)  # type: ignore[misc]
                                    if clean_bytes:
                                        dbg["driver_meta"]["refine"] = meta
                                        dbg["step"] = "ok_vedlegg_refined"
                                        return clean_bytes, str(rr.url), dbg
                                except Exception:
                                    pass
                            dbg["step"] = "ok_direct"
                            return rr.content, str(rr.url), dbg
                        if attempt < max_tries and rr.status_code in (
                            429,
                            500,
                            502,
                            503,
                            504,
                        ):
                            time.sleep(0.5 * attempt)
                            continue
                        break
                    except requests.RequestException:
                        if attempt < max_tries:
                            time.sleep(0.5 * attempt)
                            continue
                        break

        # Ingen PDF ble bekreftet på noen variant
        dbg["step"] = "no_pdf_confirmed"
        return None, None, dbg
