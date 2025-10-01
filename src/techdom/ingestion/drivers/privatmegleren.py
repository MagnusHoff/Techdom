# core/drivers/privatmegleren.py
from __future__ import annotations

import os
import time
import re
import json
import uuid
import requests
from typing import Optional, Dict, Any, Tuple, List
from bs4 import BeautifulSoup, Tag
from urllib.parse import urlparse, urljoin

from .base import Driver
from techdom.ingestion.http_headers import BROWSER_HEADERS
from techdom.infrastructure.config import SETTINGS

PDF_MAGIC = b"%PDF-"

# Kjente “dårlige” PDF-er som ikke er salgsoppgave
PM_BAD_PDFS = {
    "https://privatmegleren.no/docs/klikk.pdf",
    "http://privatmegleren.no/docs/klikk.pdf",
}

# --- Kun salgsoppgave/prospekt ---
POSITIVE_HINTS_RX = re.compile(
    r"(salgsoppgav|prospekt|digital[\-_]?salgsoppgave|utskriftsvennlig|komplett|se\s+pdf|last\s+ned\s+pdf)",
    re.I,
)

# Ekskluder andre dokumenttyper
NEGATIVE_HINTS_RX = re.compile(
    r"(tilstandsrapport|boligsalgsrapport|takst|fidens|estates|ns[\s\-_]*3600|"
    r"energiattest|nabolag|nabolagsprofil|contentassets/nabolaget|egenerkl|"
    r"budskjema|kjøpekontrakt|vilkår|terms|cookies)",
    re.I,
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


def _is_blacklisted_pdf(url: str) -> bool:
    try:
        u = (url or "").split("#")[0]
        return u in PM_BAD_PDFS or u.lower().endswith("/docs/klikk.pdf")
    except Exception:
        return False


def _looks_like_pdf(b: bytes | None) -> bool:
    return isinstance(b, (bytes, bytearray)) and b.startswith(PDF_MAGIC)


# --- NEXT.js helpers (uendret der det gir mening) ---
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


# --- HTTP helpers ---
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


# --- Kandidatinnsamling: KUN prospekt/salgsoppgave ---
def _allowed_candidate(label: str, url: str) -> bool:
    lo = f"{label} {url}".lower()
    if _is_blacklisted_pdf(url):
        return False
    if NEGATIVE_HINTS_RX.search(lo):
        return False
    # Må ha positive prospekt-signaler i label/URL eller avslutte med .pdf
    return POSITIVE_HINTS_RX.search(lo) is not None or url.lower().endswith(".pdf")


def _gather_pdf_candidates(soup: BeautifulSoup, base_url: str) -> List[str]:
    urls: List[str] = []

    # 1) <a> – kun med positive hint
    if hasattr(soup, "find_all"):
        for a in soup.find_all("a"):
            if not isinstance(a, Tag):
                continue
            txt = a.get_text(" ", strip=True) or ""
            raw = a.get("href") or a.get("data-href") or a.get("download") or ""
            href = _as_str(raw).strip()
            if not href:
                continue
            u = _abs(base_url, href)
            if not u:
                continue
            if _allowed_candidate(txt, u):
                urls.append(u)

    # 2) knapper/div/span med data-attrs – samme filter
    if hasattr(soup, "find_all"):
        for el in soup.find_all(["button", "div", "span"]):
            if not isinstance(el, Tag):
                continue
            txt = el.get_text(" ", strip=True) or ""
            for attr in ("data-href", "data-file", "data-url", "data-download"):
                raw = el.get(attr) or ""
                href = _as_str(raw).strip()
                if not href:
                    continue
                u = _abs(base_url, href)
                if u and _allowed_candidate(txt, u):
                    urls.append(u)

    # 3) Regex i rå HTML – ta kun .pdf-lenker som ikke trigges av negative hint
    try:
        html = soup.decode()
    except Exception:
        html = ""
    for m in re.finditer(r'https?://[^\s"\'<>]+\.pdf(?:\?[^\s<>\'"]*)?', html, re.I):
        u = m.group(0).replace("\\/", "/")
        if _allowed_candidate("", u):
            urls.append(u)

    # uniq
    seen: set[str] = set()
    out: List[str] = []
    for u in urls:
        if u not in seen:
            out.append(u)
            seen.add(u)
    return out


# Bonus: løft riktige kandidater (objekt-ID og prospekt-ord), straff "klikk.pdf"
OBJ_ID_RX = re.compile(r"/(\d{6,})\b")


def _score_candidate(url: str, page_url: str, label: str | None = None) -> int:
    s = (url or "").lower()
    lbl = (label or "").lower()
    sc = 0
    if s.endswith(".pdf"):
        sc += 25
    if POSITIVE_HINTS_RX.search(s) or POSITIVE_HINTS_RX.search(lbl):
        sc += 40
    if "prospekt" in s or "prospekt" in lbl:
        sc += 30
    if "salgsoppgav" in s or "salgsoppgav" in lbl:
        sc += 30
    # bonus hvis URL inneholder samme objekt-ID som siden
    m = OBJ_ID_RX.search(page_url)
    if m and m.group(1) in s:
        sc += 40
    # straff for kjente dårlige
    base = os.path.basename(s)
    if base == "klikk.pdf" or "/docs/klikk.pdf" in s:
        sc -= 500
    return sc


# --- Innholdsvalidering: PDF må ligne prospekt, og ikke inneholde TR-ord først ---
def _first_pages_text(b: bytes, max_pages: int = 3) -> str:
    try:
        from PyPDF2 import PdfReader
        import io

        r = PdfReader(io.BytesIO(b))  # type: ignore[name-defined]
        out: List[str] = []
        for p in r.pages[:max_pages]:
            try:
                t = (p.extract_text() or "").lower()
            except Exception:
                t = ""
            if t:
                out.append(t)
        return "\n".join(out)
    except Exception:
        return ""


def _is_prospect_pdf(
    b: bytes | None, url: Optional[str], allow_tr_terms: bool = False
) -> bool:
    if not _looks_like_pdf(b):
        return False
    if not b or len(b) < MIN_BYTES:
        return False
    # minimumssider – prospekt er vanligvis >5–6 sider
    try:
        from PyPDF2 import PdfReader
        import io

        n_pages = len(PdfReader(io.BytesIO(b)).pages)  # type: ignore[name-defined]
    except Exception:
        n_pages = 0
    if n_pages < MIN_PAGES:
        return False
    lo = (url or "").lower()
    if NEGATIVE_HINTS_RX.search(lo):
        return False
    first_txt = _first_pages_text(b, 3)
    if first_txt and not allow_tr_terms and NEGATIVE_HINTS_RX.search(first_txt):
        return False
    return True


def _extract_estate_id(url: str) -> Optional[str]:
    try:
        parts = [segment for segment in urlparse(url or "").path.split("/") if segment]
    except Exception:
        return None
    for segment in reversed(parts):
        if segment.isdigit():
            return segment
    return None


def _graphql_attachments(
    sess: requests.Session,
    page_url: str,
    referer: str,
    timeout: int,
    dbg: Dict[str, Any],
) -> List[Dict[str, Any]]:
    estate_id = _extract_estate_id(page_url)
    if not estate_id:
        return []

    payload = {
        "query": (
            "query EstateDocuments($estateId: String!, $brandId: String!) "
            "{ estate(input:{estateId:$estateId, brandId:$brandId, preview:false, refresh:false}) "
            "{ documents { attachments { description category extension url documentId } } } }"
        ),
        "variables": {
            "estateId": estate_id,
            "brandId": "privatmegleren",
        },
    }

    headers = dict(BROWSER_HEADERS)
    headers.update(
        {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://privatmegleren.no",
            "Referer": referer,
            "traceId": str(uuid.uuid4()),
        }
    )

    try:
        resp = sess.post(
            "https://ds.privatmegleren.no/graphql",
            headers=headers,
            json=payload,
            timeout=timeout,
        )
    except Exception as exc:
        dbg.setdefault("driver_meta", {})["graphql_error"] = type(exc).__name__
        return []

    dbg.setdefault("driver_meta", {}).setdefault(
        "graphql_documents",
        {"status": getattr(resp, "status_code", None), "ok": getattr(resp, "ok", None)},
    )

    if not getattr(resp, "ok", False):
        return []

    try:
        data = resp.json()
    except Exception as exc:
        dbg["driver_meta"]["graphql_error"] = type(exc).__name__
        return []

    try:
        attachments = (
            data.get("data", {})
            .get("estate", {})
            .get("documents", {})
            .get("attachments")
        )
    except Exception:
        attachments = None

    out: List[Dict[str, Any]] = []
    for att in attachments or []:
        url = att.get("url")
        if not isinstance(url, str) or not url:
            continue
        description = att.get("description") or ""
        category = att.get("category") or ""
        label = " ".join(part for part in (description.strip(), category.strip()) if part)
        out.append(
            {
                "url": url,
                "label": label,
                "extension": (att.get("extension") or "").lower(),
                "document_id": att.get("documentId"),
                "category": att.get("category") or "",
            }
        )

    if out:
        dbg["driver_meta"]["graphql_documents"].update(
            {"attachments": len(out), "estate_id": estate_id}
        )

    return out


class PrivatMeglerenDriver(Driver):
    name = "privatmegleren"

    def matches(self, url: str) -> bool:
        return "privatmegleren.no" in (url or "").lower()

    def try_fetch(
        self, sess: requests.Session, page_url: str
    ) -> Tuple[bytes | None, str | None, dict]:
        dbg: Dict[str, Any] = {"driver": self.name, "step": "start", "driver_meta": {}}

        # Prøv vanlige undersider hvor dokumentseksjon ligger
        base = page_url.rstrip("/")
        variants = [
            base,
            base + "/salgsoppgave",
            base + "/dokumenter",
            base + "#salgsoppgave",
        ]

        backoff = 0.6
        max_tries = 2

        graphql_candidates: List[Dict[str, Any]] = []
        graphql_attempted = False

        for view_url in variants:
            # 0) last side
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

            if not graphql_attempted:
                graphql_attempted = True
                canonical_url = str(r0.url)
                graphql_candidates = _graphql_attachments(
                    sess,
                    canonical_url,
                    referer=canonical_url,
                    timeout=SETTINGS.REQ_TIMEOUT,
                    dbg=dbg,
                )

            # 1) NEXT-data: direkte PDF-lenker hvis mulig (+/_next/data/)
            try:
                blob = _read_next_data(soup)
                pdfs = _pdfs_from_next(blob) if isinstance(blob, dict) else []
                if not pdfs:
                    pdfs = _try_buildid_fetch(sess, view_url, soup, referer=view_url)
            except Exception:
                pdfs = []

            # 2) Vanlige kandidater fra DOM/script (KUN prospekt)
            dom_pdfs = _gather_pdf_candidates(soup, view_url)

            # 3) Samle og filtrer KUN prospekt-lenker
            candidate_entries: List[Tuple[str, Optional[str], Optional[Dict[str, Any]]]] = []
            for u in pdfs:
                candidate_entries.append((u, None, None))
            for u in dom_pdfs:
                candidate_entries.append((u, None, None))
            for att in graphql_candidates:
                candidate_entries.append(
                    (att.get("url"), att.get("label"), att)
                )

            candidates: List[Tuple[str, Optional[str], Optional[Dict[str, Any]]]] = []
            seen: set[str] = set()
            for url, label, meta in candidate_entries:
                if not url or url in seen or _is_blacklisted_pdf(url):
                    continue
                combined = " ".join(
                    part for part in ((label or ""), url) if part
                ).lower()
                if NEGATIVE_HINTS_RX.search(combined):
                    continue
                extension = (meta or {}).get("extension", "") if meta else ""
                pdfish = url.lower().endswith(".pdf") or extension == "pdf"
                if not (POSITIVE_HINTS_RX.search(combined) or pdfish):
                    continue
                seen.add(url)
                candidates.append((url, label, meta))

            if not candidates:
                continue

            candidates.sort(
                key=lambda item: _score_candidate(item[0], view_url, item[1]),
                reverse=True,
            )

            # 4) HEAD/GET med validering av prospekt-innhold
            for url, label, meta in candidates:
                # HEAD
                try:
                    h = _head(sess, url, view_url, SETTINGS.REQ_TIMEOUT)
                    ct = (h.headers.get("Content-Type") or "").lower()
                    final = str(h.url)
                    if _is_blacklisted_pdf(final) or NEGATIVE_HINTS_RX.search(
                        final.lower()
                    ):
                        continue
                    is_pdfish = h.ok and (
                        ct.startswith("application/pdf")
                        or final.lower().endswith(".pdf")
                    )
                except Exception:
                    final, is_pdfish = url, False

                target = final if is_pdfish else url

                # GET bekreft (med små retries)
                for attempt in range(1, max_tries + 1):
                    try:
                        t0 = time.monotonic()
                        rr = _get(sess, target, view_url, SETTINGS.REQ_TIMEOUT)
                        elapsed_ms = int((time.monotonic() - t0) * 1000)
                        dbg["driver_meta"][f"get_{attempt}_{target}"] = {
                            "status": rr.status_code,
                            "content_type": rr.headers.get("Content-Type"),
                            "content_length": rr.headers.get("Content-Length"),
                            "elapsed_ms": elapsed_ms,
                            "final_url": str(rr.url),
                            "bytes": len(rr.content) if rr.content else 0,
                        }
                        allow_tr_terms = False
                        if meta and (meta.get("category") or "").lower() == "mergedattachment":
                            allow_tr_terms = True
                        if rr.ok and _is_prospect_pdf(
                            rr.content, str(rr.url), allow_tr_terms
                        ):
                            dbg["step"] = "ok_prospect"
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

        dbg["step"] = "no_pdf_confirmed"
        return None, None, dbg
