from __future__ import annotations

import time
import re
import json
from typing import Optional, Dict, Any, Tuple

import requests
from bs4 import BeautifulSoup, Tag
from urllib.parse import urlparse

from ..base import Driver
from ..http_headers import BROWSER_HEADERS

# SETTINGS kan mangle i dev – fall tilbake til safe defaults
try:
    from ..config import SETTINGS  # type: ignore

    REQ_TIMEOUT: int = int(getattr(SETTINGS, "REQ_TIMEOUT", 25))
except Exception:
    REQ_TIMEOUT = 25

PDF_MAGIC = b"%PDF-"


def _looks_like_pdf(b: bytes) -> bool:
    return isinstance(b, (bytes, bytearray)) and b.startswith(PDF_MAGIC)


UUID_RX = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
)


def _json_from_next_data(soup: BeautifulSoup) -> dict | None:
    tag = soup.find("script", id="__NEXT_DATA__")
    if not isinstance(tag, Tag):
        return None
    try:
        blob = json.loads(tag.string or "{}")
        return blob if isinstance(blob, dict) else None
    except Exception:
        return None


def _find_uuid_in(obj: Any) -> Optional[str]:
    def walk(o: Any):
        if isinstance(o, dict):
            for v in o.values():
                yield from walk(v)
        elif isinstance(o, list):
            for v in o:
                yield from walk(v)
        elif isinstance(o, str):
            s = o.strip()
            if UUID_RX.fullmatch(s):
                yield s

    try:
        for s in walk(obj):
            return s
    except Exception:
        pass
    return None


def _content_filename(headers: Dict[str, str] | None) -> Optional[str]:
    if not headers:
        return None
    cd = headers.get("Content-Disposition") or headers.get("content-disposition") or ""
    m = re.search(r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';]+)', cd)
    if m:
        return m.group(1)
    return None


def _guess_is_tilstandsrapport(url: str | None, headers: Dict[str, str] | None) -> bool:
    """
    Svært enkel heuristikk for å markere om PDF trolig er en (ren) tilstandsrapport.
    Dette gjør at fetch.py kan hoppe over ny klipp.
    """
    lo = (url or "").lower()
    filename = (_content_filename(headers) or "").lower()
    hay = " ".join([lo, filename])

    # typiske indikatorer
    if any(
        k in hay
        for k in (
            "tilstandsrapport",
            "boligsalgsrapport",
            "ns3600",
            "ns_3600",
            "ns-3600",
        )
    ):
        return True
    return False


class DnbEiendomDriver(Driver):
    name = "dnbeiendom"

    def matches(self, url: str) -> bool:
        return "dnbeiendom.no" in (url or "").lower()

    def try_fetch(
        self, sess: requests.Session, page_url: str
    ) -> Tuple[bytes | None, str | None, Dict[str, Any]]:
        dbg: Dict[str, Any] = {"driver": self.name, "step": "start", "meta": {}}

        # Sørg for /salgsoppgave som referer
        referer = (
            page_url
            if "/salgsoppgave" in page_url
            else page_url.rstrip("/") + "/salgsoppgave"
        )

        # Last HTML for å finne UUID via __NEXT_DATA__ eller _next-data route
        r = sess.get(
            referer,
            headers={"Referer": page_url, **BROWSER_HEADERS},
            timeout=REQ_TIMEOUT,
            allow_redirects=True,
        )
        r.raise_for_status()
        html = r.text
        soup = BeautifulSoup(html, "html.parser")

        data = _json_from_next_data(soup)
        uuid = _find_uuid_in(data) if data else None

        # fallback: hent buildId og slå opp JSON-data
        if not uuid:
            build_id: Optional[str] = None
            if isinstance(data, dict) and isinstance(data.get("buildId"), str):
                build_id = data["buildId"]
            if not build_id:
                m = re.search(r"/_next/static/([^/]+)/", html)
                if m:
                    build_id = m.group(1)
            if build_id:
                path = urlparse(referer).path.strip("/")
                if path.endswith("salgsoppgave"):
                    path = path[: -len("salgsoppgave")].rstrip("/")
                data_url = f"https://dnbeiendom.no/_next/data/{build_id}/{path}.json"
                jd = sess.get(
                    data_url,
                    headers={"Referer": referer, **BROWSER_HEADERS},
                    timeout=REQ_TIMEOUT,
                )
                if jd.ok:
                    try:
                        cand = _find_uuid_in(jd.json())
                    except Exception:
                        cand = None
                    if cand:
                        uuid = cand

        if not uuid:
            m = UUID_RX.search(html)
            if m:
                uuid = m.group(0)

        if not uuid:
            dbg["step"] = "uuid_not_found"
            return None, None, dbg

        # ---- Hent PDF (requests, ikke Playwright) ----
        req_headers = dict(BROWSER_HEADERS)
        req_headers.update(
            {
                "Accept": "application/pdf,application/octet-stream,*/*",
                "Referer": referer,
                "Origin": "https://dnbeiendom.no",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-site",
            }
        )

        # 1) Direkte dokument-URL (vanlig hos DNB)
        direct_url = (
            f"https://dnbeiendom.no/api/v1/properties/{uuid}/documents/{uuid}.pdf"
        )
        backoff = 0.6
        max_tries = 2

        for attempt in range(1, max_tries + 1):
            try:
                t0 = time.monotonic()
                rr = sess.get(
                    direct_url,
                    headers=req_headers,
                    timeout=REQ_TIMEOUT,
                    allow_redirects=True,
                )
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                ct = (rr.headers.get("Content-Type") or "").lower()
                ok_pdf = rr.ok and (
                    ("application/pdf" in ct) or _looks_like_pdf(rr.content)
                )

                dbg.setdefault("driver_meta", {})
                dbg["driver_meta"][f"direct_try_{attempt}"] = {
                    "status": rr.status_code,
                    "content_type": rr.headers.get("Content-Type"),
                    "content_length": rr.headers.get("Content-Length"),
                    "elapsed_ms": elapsed_ms,
                    "final_url": str(rr.url),
                    "bytes": len(rr.content) if rr.content else 0,
                }

                if ok_pdf:
                    dbg["step"] = "ok_direct"
                    # Dokumentet her er normalt hele salgsoppgaven (ikke ren TR)
                    dbg["meta"]["is_tilstandsrapport"] = _guess_is_tilstandsrapport(
                        str(rr.url), rr.headers
                    )
                    return rr.content, str(rr.url), dbg

                # Retry kun på transiente statuser
                if attempt < max_tries and rr.status_code in (429, 500, 502, 503, 504):
                    time.sleep(backoff * attempt)
                    continue
                break

            except requests.RequestException:
                if attempt < max_tries:
                    time.sleep(backoff * attempt)
                    continue
                break

        # 2) Fallback: POST til pdfdownload (kan gi redirect, PDF eller JSON med URL)
        api = f"https://dnbeiendom.no/api/v1/properties/{uuid}/pdfdownload"
        try:
            resp = sess.post(
                api,
                headers=req_headers,
                json={},
                timeout=REQ_TIMEOUT,
                allow_redirects=False,
            )

            # Redirect (signert URL)
            if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("Location")
                if loc:
                    t0 = time.monotonic()
                    rr = sess.get(
                        loc,
                        headers=req_headers,
                        timeout=REQ_TIMEOUT,
                        allow_redirects=True,
                    )
                    elapsed_ms = int((time.monotonic() - t0) * 1000)
                    ct = (rr.headers.get("Content-Type") or "").lower()
                    if rr.ok and (
                        ("application/pdf" in ct) or _looks_like_pdf(rr.content)
                    ):
                        dbg["step"] = "ok_redirect"
                        dbg.setdefault("driver_meta", {})
                        dbg["driver_meta"]["redirect"] = {
                            "status": rr.status_code,
                            "content_type": rr.headers.get("Content-Type"),
                            "content_length": rr.headers.get("Content-Length"),
                            "elapsed_ms": elapsed_ms,
                            "final_url": str(rr.url),
                            "bytes": len(rr.content) if rr.content else 0,
                            "location": loc,
                        }
                        dbg["meta"]["is_tilstandsrapport"] = _guess_is_tilstandsrapport(
                            str(rr.url), rr.headers
                        )
                        return rr.content, str(rr.url), dbg

            # 200 → direkte PDF eller JSON med lenke
            ct = (resp.headers.get("Content-Type") or "").lower()
            if resp.status_code == 200:
                # Direkte PDF/binær?
                if resp.content and (
                    ("application/pdf" in ct)
                    or ("octet-stream" in ct)
                    or _looks_like_pdf(resp.content)
                ):
                    dbg["step"] = "ok_post_pdf"
                    dbg["meta"]["is_tilstandsrapport"] = _guess_is_tilstandsrapport(
                        api, resp.headers
                    )
                    return resp.content, api, dbg

                # JSON → lenke
                if "application/json" in ct:
                    try:
                        blob = resp.json()
                    except Exception:
                        blob = None
                    if isinstance(blob, dict):
                        for k in ("url", "href", "file", "downloadUrl"):
                            u = blob.get(k)
                            if isinstance(u, str) and u:
                                t0 = time.monotonic()
                                rr = sess.get(
                                    u,
                                    headers=req_headers,
                                    timeout=REQ_TIMEOUT,
                                    allow_redirects=True,
                                )
                                elapsed_ms = int((time.monotonic() - t0) * 1000)
                                ct2 = (rr.headers.get("Content-Type") or "").lower()
                                if rr.ok and (
                                    ("application/pdf" in ct2)
                                    or _looks_like_pdf(rr.content)
                                ):
                                    dbg["step"] = "ok_post_json_url"
                                    dbg.setdefault("driver_meta", {})
                                    dbg["driver_meta"]["json_url"] = {
                                        "status": rr.status_code,
                                        "content_type": rr.headers.get("Content-Type"),
                                        "content_length": rr.headers.get(
                                            "Content-Length"
                                        ),
                                        "elapsed_ms": elapsed_ms,
                                        "final_url": str(rr.url),
                                        "bytes": len(rr.content) if rr.content else 0,
                                        "url": u,
                                    }
                                    dbg["meta"]["is_tilstandsrapport"] = (
                                        _guess_is_tilstandsrapport(
                                            str(rr.url), rr.headers
                                        )
                                    )
                                    return rr.content, str(rr.url), dbg

            # Nødforsøk: PDF-lenke i body
            try:
                m = re.search(r'https?://[^"\']+\.pdf', resp.text or "", re.I)
            except Exception:
                m = None
            if m:
                u = m.group(0)
                t0 = time.monotonic()
                rr = sess.get(
                    u,
                    headers=req_headers,
                    timeout=REQ_TIMEOUT,
                    allow_redirects=True,
                )
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                ct2 = (rr.headers.get("Content-Type") or "").lower()
                if rr.ok and (
                    ("application/pdf" in ct2) or _looks_like_pdf(rr.content)
                ):
                    dbg["step"] = "ok_post_body_url"
                    dbg.setdefault("driver_meta", {})
                    dbg["driver_meta"]["body_url"] = {
                        "status": rr.status_code,
                        "content_type": rr.headers.get("Content-Type"),
                        "content_length": rr.headers.get("Content-Length"),
                        "elapsed_ms": elapsed_ms,
                        "final_url": str(rr.url),
                        "bytes": len(rr.content) if rr.content else 0,
                        "url": u,
                    }
                    dbg["meta"]["is_tilstandsrapport"] = _guess_is_tilstandsrapport(
                        str(rr.url), rr.headers
                    )
                    return rr.content, str(rr.url), dbg

            dbg["step"] = f"bad_response:{resp.status_code}"
            return None, None, dbg

        except Exception:
            dbg["step"] = "exception_post"
            return None, None, dbg
