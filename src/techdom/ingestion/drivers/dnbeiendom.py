# core/drivers/dnbeiendom.py
from __future__ import annotations

import time
import re
import json
from typing import Optional, Dict, Any, Tuple, Mapping

import requests
from bs4 import BeautifulSoup, Tag
from urllib.parse import urlparse

from .base import Driver
from techdom.ingestion.http_headers import BROWSER_HEADERS
from techdom.infrastructure.config import SETTINGS

REQ_TIMEOUT: int = int(getattr(SETTINGS, "REQ_TIMEOUT", 25))

PDF_MAGIC = b"%PDF-"
UUID_RX = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
)

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


def _content_filename(headers: Mapping[str, str] | None) -> str:
    if not headers:
        return ""
    cd = headers.get("Content-Disposition") or headers.get("content-disposition") or ""
    m = re.search(r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';]+)', cd)
    return (m.group(1) if m else "").strip().lower()


def _is_salgsoppgave_only(url: str | None, headers: Mapping[str, str] | None) -> bool:
    """Returner True hvis URL/filnavn ser ut som salgsoppgave/prospekt, og IKKE matcher negative hint."""
    lo = (url or "").lower()
    fn = _content_filename(headers)
    hay = f"{lo} {fn}"

    if any(w in hay for w in NEG_WORDS):
        return False
    # godta .pdf selv uten positive ord, men foretrekk positive når de finnes
    if any(w in hay for w in POS_WORDS):
        return True
    return lo.endswith(".pdf")


class DnbEiendomDriver(Driver):
    name = "dnbeiendom"

    def matches(self, url: str) -> bool:
        return "dnbeiendom.no" in (url or "").lower()

    def try_fetch(
        self, sess: requests.Session, page_url: str
    ) -> Tuple[bytes | None, str | None, Dict[str, Any]]:
        dbg: Dict[str, Any] = {"driver": self.name, "step": "start", "driver_meta": {}}

        # Bruk /salgsoppgave som referer (DNB forventer ofte den)
        referer = (
            page_url
            if "/salgsoppgave" in page_url
            else page_url.rstrip("/") + "/salgsoppgave"
        )

        # 1) Hent HTML og forsøk å finne UUID
        try:
            r = sess.get(
                referer,
                headers={"Referer": page_url, **BROWSER_HEADERS},
                timeout=REQ_TIMEOUT,
                allow_redirects=True,
            )
            r.raise_for_status()
        except Exception as e:
            dbg["step"] = f"page_fetch_error:{type(e).__name__}"
            dbg["driver_meta"]["error"] = str(e)
            return None, None, dbg

        html = r.text
        soup = BeautifulSoup(html, "html.parser")

        data = _json_from_next_data(soup)
        uuid: Optional[str] = _find_uuid_in(data) if data else None

        # Fallback: finn buildId og hent _next-data JSON
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

        # Nød-fallback: regex i HTML
        if not uuid:
            m = UUID_RX.search(html)
            if m:
                uuid = m.group(0)

        if not uuid:
            dbg["step"] = "uuid_not_found"
            return None, None, dbg

        # 2) Forsøk direkte dokument-URL (vanlig hos DNB)
        req_headers: Dict[str, str] = {
            **BROWSER_HEADERS,
            "Accept": "application/pdf,application/octet-stream,*/*",
            "Referer": referer,
            "Origin": "https://dnbeiendom.no",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-site",
        }

        direct_url = (
            f"https://dnbeiendom.no/api/v1/properties/{uuid}/documents/{uuid}.pdf"
        )
        backoff, max_tries = 0.6, 2

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

                dbg["driver_meta"][f"direct_try_{attempt}"] = {
                    "status": rr.status_code,
                    "content_type": rr.headers.get("Content-Type"),
                    "content_length": rr.headers.get("Content-Length"),
                    "elapsed_ms": elapsed_ms,
                    "final_url": str(rr.url),
                    "bytes": len(rr.content) if rr.content else 0,
                }

                if ok_pdf and _is_salgsoppgave_only(str(rr.url), rr.headers):
                    dbg["step"] = "ok_direct"
                    return rr.content, str(rr.url), dbg

                if attempt < max_tries and rr.status_code in (429, 500, 502, 503, 504):
                    time.sleep(backoff * attempt)
                    continue
                break
            except requests.RequestException as e:
                dbg["driver_meta"][f"direct_err_{attempt}"] = str(e)
                if attempt < max_tries:
                    time.sleep(backoff * attempt)
                    continue
                break

        # 3) Fallback: POST til pdfdownload → redirect/JSON/direkte PDF
        api = f"https://dnbeiendom.no/api/v1/properties/{uuid}/pdfdownload"
        try:
            resp = sess.post(
                api,
                headers=req_headers,
                json={},
                timeout=REQ_TIMEOUT,
                allow_redirects=False,
            )

            # 3a) Redirect til signert URL
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
                        if _is_salgsoppgave_only(str(rr.url), rr.headers):
                            dbg["step"] = "ok_redirect"
                            dbg["driver_meta"]["redirect"] = {
                                "status": rr.status_code,
                                "content_type": rr.headers.get("Content-Type"),
                                "content_length": rr.headers.get("Content-Length"),
                                "elapsed_ms": elapsed_ms,
                                "final_url": str(rr.url),
                                "bytes": len(rr.content) if rr.content else 0,
                                "location": loc,
                            }
                            return rr.content, str(rr.url), dbg

            # 3b) 200: direkte PDF eller JSON som peker på fil
            ct = (resp.headers.get("Content-Type") or "").lower()
            if resp.status_code == 200:
                # Direkte binær?
                if resp.content and (
                    ("application/pdf" in ct)
                    or ("octet-stream" in ct)
                    or _looks_like_pdf(resp.content)
                ):
                    if _is_salgsoppgave_only(api, resp.headers):
                        dbg["step"] = "ok_post_pdf"
                        return resp.content, api, dbg

                # JSON med lenke
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
                                    if _is_salgsoppgave_only(str(rr.url), rr.headers):
                                        dbg["step"] = "ok_post_json_url"
                                        dbg["driver_meta"]["json_url"] = {
                                            "status": rr.status_code,
                                            "content_type": rr.headers.get(
                                                "Content-Type"
                                            ),
                                            "content_length": rr.headers.get(
                                                "Content-Length"
                                            ),
                                            "elapsed_ms": elapsed_ms,
                                            "final_url": str(rr.url),
                                            "bytes": (
                                                len(rr.content) if rr.content else 0
                                            ),
                                            "url": u,
                                        }
                                        return rr.content, str(rr.url), dbg

            # 3c) Nød-forsøk: plukk .pdf-lenke fra body
            try:
                m = re.search(r'https?://[^"\']+\.pdf', resp.text or "", re.I)
            except Exception:
                m = None
            if m:
                u = m.group(0)
                t0 = time.monotonic()
                rr = sess.get(
                    u, headers=req_headers, timeout=REQ_TIMEOUT, allow_redirects=True
                )
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                ct2 = (rr.headers.get("Content-Type") or "").lower()
                if rr.ok and (
                    ("application/pdf" in ct2) or _looks_like_pdf(rr.content)
                ):
                    if _is_salgsoppgave_only(str(rr.url), rr.headers):
                        dbg["step"] = "ok_post_body_url"
                        dbg["driver_meta"]["body_url"] = {
                            "status": rr.status_code,
                            "content_type": rr.headers.get("Content-Type"),
                            "content_length": rr.headers.get("Content-Length"),
                            "elapsed_ms": elapsed_ms,
                            "final_url": str(rr.url),
                            "bytes": len(rr.content) if rr.content else 0,
                            "url": u,
                        }
                        return rr.content, str(rr.url), dbg

            dbg["step"] = f"bad_response:{resp.status_code}"
            return None, None, dbg

        except Exception as e:
            dbg["step"] = "exception_post"
            dbg["driver_meta"]["post_error"] = str(e)
            return None, None, dbg
