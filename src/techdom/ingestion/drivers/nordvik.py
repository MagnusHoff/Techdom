# core/drivers/nordvik.py
from __future__ import annotations

import re
import time
from typing import Dict, Any, Tuple, List, Optional, Mapping
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup, Tag

from .base import Driver
from techdom.infrastructure.config import SETTINGS
from .common import abs_url, as_str, looks_like_pdf_bytes, request_pdf

# --- kun salgsoppgave/prospekt ---
ALLOW_RX = re.compile(r"(salgsoppgav|prospekt|utskriftsvennlig|komplett)", re.I)
BLOCK_RX = re.compile(
    r"(tilstandsrapport|boligsalgsrapport|ns[\s_-]*3600|energiattest|egenerkl|"
    r"nabolag|nabolagsprofil|contentassets/nabolaget|takst|fidens|bud|budskjema|"
    r"vedtekter|arsberetning|årsberetning|regnskap|sameie|kontrakt|kjopetilbud)",
    re.I,
)

MIN_BYTES = 300_000
MIN_PAGES = 4
API_ROOT = "https://www.nordvikbolig.no"
API_DOCUMENTS = f"{API_ROOT}/api/documents"
API_DOWNLOAD = f"{API_ROOT}/api/documents/download"


def _pdf_pages(b: bytes | None) -> int:
    """Liten, robust sidetelling (ikke kritisk ved feil)."""
    if not b:
        return 0
    try:
        import io
        from PyPDF2 import PdfReader  # type: ignore

        return len(PdfReader(io.BytesIO(b)).pages)
    except Exception:
        return 0


def _pdf_quality_ok(b: bytes | None) -> bool:
    if not b or not looks_like_pdf_bytes(b) or len(b) < MIN_BYTES:
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
    """Strengt filter: kun salgsoppgave/prospekt; blokker TR/annet."""
    lo = (url or "").lower()
    fn = (_content_filename(headers) or "").lower()
    hay = " ".join([lo, fn, (label or "").lower()])
    if BLOCK_RX.search(hay):
        return False
    return bool(ALLOW_RX.search(hay))


def _head(
    sess: requests.Session, url: str, referer: str, timeout: int
) -> requests.Response:
    return request_pdf(
        sess,
        url,
        referer,
        timeout,
        method="head",
        allow_redirects=True,
    )


def _get(
    sess: requests.Session, url: str, referer: str, timeout: int
) -> requests.Response:
    extra = {
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-site",
    }
    return request_pdf(
        sess,
        url,
        referer,
        timeout,
        extra_headers=extra,
        allow_redirects=True,
    )


def _estate_id_from_url(url: str) -> str | None:
    """Return Nordvik estate UUID from /boliger/{estateId} path."""
    try:
        path = urlparse(url or "").path or ""
    except Exception:
        path = ""
    if not path:
        return None
    parts = [p for p in path.split("/") if p]
    for idx, part in enumerate(parts):
        if part.lower() == "boliger" and idx + 1 < len(parts):
            candidate = parts[idx + 1]
            if re.fullmatch(r"[0-9a-fA-F-]{10,}", candidate):
                return candidate.upper()
            return candidate or None
    if parts and re.fullmatch(r"[0-9a-fA-F-]{10,}", parts[-1]):
        return parts[-1].upper()
    return None


def _api_headers(referer: str | None, *, accept: str = "application/json") -> Dict[str, str]:
    headers: Dict[str, str] = {"User-Agent": SETTINGS.USER_AGENT}
    if accept:
        headers["Accept"] = accept
    if referer:
        headers["Referer"] = referer
        try:
            pr = urlparse(referer)
            if pr.scheme and pr.netloc:
                headers["Origin"] = f"{pr.scheme}://{pr.netloc}"
        except Exception:
            headers.setdefault("Origin", API_ROOT)
    return headers


def _fetch_via_api(
    sess: requests.Session, referer: str, estate_id: str
) -> Tuple[bytes | None, str | None, Dict[str, Any], Dict[str, Any]]:
    """
    Fetch prospectus using Nordvik's JSON API.
    Returns (pdf_bytes, final_url, meta, driver_meta).
    """
    meta: Dict[str, Any] = {
        "api_estate_id": estate_id,
    }
    driver_meta: Dict[str, Any] = {}

    docs_url = f"{API_DOCUMENTS}/{estate_id}"
    headers = _api_headers(referer)
    try:
        resp = sess.get(docs_url, headers=headers, timeout=SETTINGS.REQ_TIMEOUT)
        meta["api_documents_status"] = resp.status_code
        resp.raise_for_status()
    except Exception as exc:
        meta["api_documents_error"] = f"{type(exc).__name__}:{exc}"
        return None, None, meta, driver_meta

    try:
        payload = resp.json()
    except Exception as exc:
        meta["api_documents_json_error"] = f"{type(exc).__name__}:{exc}"
        return None, None, meta, driver_meta

    estate_doc = payload.get("estateDocument") if isinstance(payload, dict) else None
    meta["api_has_estate_document"] = bool(estate_doc)
    if not estate_doc:
        return None, None, meta, driver_meta

    doc_id = estate_doc.get("documentId")
    doc_type = estate_doc.get("docType")
    last_changed = estate_doc.get("lastChanged")
    meta.update(
        {
            "api_doc_id": doc_id,
            "api_doc_type": doc_type,
            "api_doc_last_changed": last_changed,
        }
    )

    if not doc_id:
        meta["api_doc_missing_id"] = True
        return None, None, meta, driver_meta

    download_payload = {
        "estateId": estate_id,
        "docId": doc_id,
        "docType": doc_type,
        "lastChanged": last_changed,
    }
    post_headers = _api_headers(referer)
    post_headers["Content-Type"] = "application/json"

    try:
        resp_dl = sess.post(
            API_DOWNLOAD,
            json=download_payload,
            headers=post_headers,
            timeout=SETTINGS.REQ_TIMEOUT,
        )
        meta["api_download_status"] = resp_dl.status_code
        resp_dl.raise_for_status()
    except Exception as exc:
        meta["api_download_error"] = f"{type(exc).__name__}:{exc}"
        return None, None, meta, driver_meta

    try:
        dl_json = resp_dl.json()
    except Exception as exc:
        meta["api_download_json_error"] = f"{type(exc).__name__}:{exc}"
        return None, None, meta, driver_meta

    download_url = (dl_json or {}).get("url")
    meta["api_download_success"] = bool((dl_json or {}).get("success"))
    if not download_url:
        meta["api_download_error_msg"] = (dl_json or {}).get("error")
        return None, None, meta, driver_meta

    meta["api_download_url"] = download_url

    try:
        rr = _get(sess, download_url, referer, SETTINGS.REQ_TIMEOUT)
    except requests.RequestException as exc:
        meta["api_pdf_error"] = f"{type(exc).__name__}:{exc}"
        return None, None, meta, driver_meta

    driver_meta["api_pdf_fetch"] = {
        "status": rr.status_code,
        "content_type": rr.headers.get("Content-Type"),
        "content_length": rr.headers.get("Content-Length"),
        "bytes": len(rr.content or b""),
        "final_url": str(rr.url),
    }

    meta["api_pdf_status"] = rr.status_code
    meta["api_pdf_content_type"] = rr.headers.get("Content-Type")
    meta["api_pdf_bytes"] = len(rr.content or b"")

    if not rr.ok:
        meta["api_pdf_not_ok"] = True
        return None, None, meta, driver_meta

    if not _is_salgsoppgave(str(rr.url), rr.headers, "api"):
        meta["api_pdf_filtered"] = True
        return None, None, meta, driver_meta

    if not _pdf_quality_ok(rr.content):
        meta["api_pdf_quality"] = "insufficient"
        return None, None, meta, driver_meta

    meta["api_pdf_quality"] = "ok"
    return rr.content, str(rr.url), meta, driver_meta


def _gather_salgsoppgave_candidates(
    soup: BeautifulSoup, base_url: str
) -> List[tuple[str, str]]:
    """
    Returner [(url, label)] som tydelig matcher salgsoppgave/prospekt.
    Ikke ta med generelle /dokument/ uten navn – disse kan være TR.
    """
    out: List[tuple[str, str]] = []

    # 1) DOM-elementer (a/button/div/span) med relevant label/URL
    for el in soup.find_all(["a", "button", "div", "span"]):
        if not isinstance(el, Tag):
            continue
        label = (el.get_text(" ", strip=True) or "").strip()
        href_raw = (
            el.get("href")
            or el.get("data-href")
            or el.get("data-url")
            or el.get("data-file")
            or ""
        )
        href = as_str(href_raw).strip()
        if not href:
            continue
        u = abs_url(base_url, href)
        if not u:
            continue
        # Strengt: KUN hvis label/URL peker mot salgsoppgave/prospekt – og ikke har blokkord
        if _is_salgsoppgave(u, None, label):
            out.append((u, label))

    # 2) Direkte .pdf-URL-er i rå HTML – men kun dersom ALLOW_RX treffer og ikke BLOCK_RX
    try:
        html = soup.decode()
    except Exception:
        html = ""
    for m in re.finditer(
        r'https?://[^\s"\'<>]+\.pdf(?:\?[^\s<>\'"]*)?', html or "", re.I
    ):
        u = m.group(0)
        if _is_salgsoppgave(u, None, ""):
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
    """Prioriter tydelige salgsoppgave-signaler."""
    lo = (u + " " + (label or "")).lower()
    sc = 0
    if lo.endswith(".pdf"):
        sc += 30
    if "salgsoppgav" in lo:
        sc += 40
    if "prospekt" in lo:
        sc += 20
    if "utskriftsvennlig" in lo or "komplett" in lo:
        sc += 10
    # liten bonus for kortere (ofte mer 'direkte') URL
    sc += max(0, 20 - len(u) // 100)
    return sc


class NordvikDriver(Driver):
    name = "nordvik"

    def matches(self, url: str) -> bool:
        return "nordvikbolig.no/boliger/" in (url or "").lower()

    def try_fetch(
        self, sess: requests.Session, page_url: str
    ) -> Tuple[bytes | None, str | None, dict]:
        dbg: Dict[str, Any] = {"driver": self.name, "step": "start", "meta": {}}

        referer = page_url.rstrip("/")

        estate_id = _estate_id_from_url(referer)
        if estate_id:
            dbg["meta"]["api_estate_id"] = estate_id
            pdf_api, url_api, api_meta, api_driver_meta = _fetch_via_api(
                sess, referer, estate_id
            )
            if api_meta:
                dbg["meta"].update(api_meta)
            if api_driver_meta:
                dbg.setdefault("driver_meta", {}).update(api_driver_meta)
            if pdf_api:
                dbg["step"] = "ok_api"
                return pdf_api, url_api, dbg

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

        # 2) Kandidater (kun salgsoppgave/prospekt)
        cands = _gather_salgsoppgave_candidates(soup, referer)
        if not cands:
            dbg["step"] = "no_candidates"
            dbg["meta"]["candidates"] = []
            return None, None, dbg

        cands.sort(key=lambda x: _score_candidate(x[0], x[1]), reverse=True)
        dbg["meta"]["candidates_preview"] = [u for (u, _t) in cands[:8]]

        # 3) HEAD/GET med korte retries + streng filtrering ved hver respons
        backoff = 0.6
        max_tries = 2
        transient = (429, 500, 502, 503, 504)

        for url, label in cands:
            # HEAD
            try:
                h = _head(sess, url, referer, SETTINGS.REQ_TIMEOUT)
                final = str(h.url)
                ct = (h.headers.get("Content-Type") or "").lower()
                if not _is_salgsoppgave(final, h.headers, label):
                    continue
                pdfish = ct.startswith("application/pdf") or final.lower().endswith(
                    ".pdf"
                )
            except Exception:
                final = url
                pdfish = False

            # GET
            target = final if pdfish else url
            for attempt in range(1, max_tries + 1):
                try:
                    t0 = time.monotonic()
                    rr = _get(sess, target, referer, SETTINGS.REQ_TIMEOUT)
                    elapsed_ms = int((time.monotonic() - t0) * 1000)
                    dbg.setdefault("driver_meta", {})[f"get_{attempt}_{target}"] = {
                        "status": rr.status_code,
                        "content_type": rr.headers.get("Content-Type"),
                        "content_length": rr.headers.get("Content-Length"),
                        "elapsed_ms": elapsed_ms,
                        "final_url": str(rr.url),
                        "bytes": len(rr.content or b""),
                        "cd_filename": _content_filename(rr.headers),
                        "label": label,
                    }

                    # filtrer fortsatt: kun salgsoppgave
                    if not _is_salgsoppgave(str(rr.url), rr.headers, label):
                        if attempt < max_tries and rr.status_code in transient:
                            time.sleep(backoff * attempt)
                            continue
                        break

                    if rr.ok and _pdf_quality_ok(rr.content):
                        dbg["step"] = "ok_direct"
                        return rr.content, str(rr.url), dbg

                    if attempt < max_tries and rr.status_code in transient:
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
