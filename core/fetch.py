# core/fetch.py
from __future__ import annotations

from typing import Any, Dict, Tuple, Optional, List, cast
from pathlib import Path
from urllib.parse import urlparse, urljoin, parse_qs, urlunparse
import datetime as dt
import io
import json
import os
import re
import requests
import traceback
from bs4 import BeautifulSoup
from bs4.element import Tag
from PyPDF2 import PdfReader

from .sessions import new_session
from .finn_discovery import discover_megler_url
from .browser_fetch import (
    fetch_pdf_with_browser,
    fetch_pdf_with_browser_filtered,  # <- viktig for Nordvik
)
from .http_headers import BROWSER_HEADERS
from .drivers import DRIVERS

# ──────────────────────────────────────────────────────────────────────────────
#  Stier
# ──────────────────────────────────────────────────────────────────────────────
DATA_DIR = Path("data")
PROSPEKT_DIR = DATA_DIR / "prospekt"
TR_DIR = DATA_DIR / "tilstandsrapport"
FAIL_DIR = DATA_DIR / "failcases"

PROSPEKT_DIR.mkdir(parents=True, exist_ok=True)
TR_DIR.mkdir(parents=True, exist_ok=True)
FAIL_DIR.mkdir(parents=True, exist_ok=True)

# ──────────────────────────────────────────────────────────────────────────────
#  Soft import av TR-klipper (synlig feilmelding i debug hvis import feiler)
# ──────────────────────────────────────────────────────────────────────────────
TR_IMPORT_ERROR: str | None = None
try:
    from core.pdf_utils import extract_tilstandsrapport, detect_tilstandsrapport_span  # type: ignore
except Exception as _e:
    TR_IMPORT_ERROR = repr(_e)
    extract_tilstandsrapport = None  # type: ignore[assignment]
    detect_tilstandsrapport_span = None  # type: ignore[assignment]


# ──────────────────────────────────────────────────────────────────────────────
#  Små helpers
# ──────────────────────────────────────────────────────────────────────────────
def _attr_to_str(val: Any) -> str | None:
    if val is None:
        return None
    try:
        if isinstance(val, (list, tuple)):
            if not val:
                return None
            val = val[0]
        s = str(val).strip()
        return s or None
    except Exception:
        return None


def _abs(base_url: str, href: Any) -> str | None:
    if not href:
        return None
    try:
        return urljoin(base_url, str(href))
    except Exception:
        return None


def _clean_url(u: str) -> str:
    """Dropp tracking/fragment og unescape JSON-escaped slashes."""
    try:
        u = u.replace("\\/", "/")
        p = urlparse(u)
        q = parse_qs(p.query)
        drop = {k for k in q if k.startswith("utm_") or k in {"gclid", "fbclid"}}
        kept = [(k, v) for k, v in q.items() if k not in drop]
        query = "&".join(f"{k}={v[0]}" for k, v in kept if v)
        return urlunparse((p.scheme, p.netloc, p.path, p.params, query, ""))
    except Exception:
        return u


def _norm(s: str | None) -> str:
    return (s or "").lower().strip()


def _new_bs_soup(
    sess: requests.Session, url: str, referer: str | None = None
) -> tuple[BeautifulSoup, str]:
    headers: Dict[str, str] = {**BROWSER_HEADERS}
    if referer:
        headers["Referer"] = referer
        try:
            pr = urlparse(referer)
            headers["Origin"] = f"{pr.scheme}://{pr.netloc}"
        except Exception:
            pass
    r = sess.get(url, headers=headers, timeout=25, allow_redirects=True)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser"), r.text


# ---- Garanti helpers (mini-PDF -> finn DS-lenke) ----
_G_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def _garanti_find_estateid_in_text(txt: str) -> str | None:
    m = re.search(r"[?&]Estateid=(" + _G_UUID + ")", txt, flags=re.I)
    if m:
        return m.group(1)
    m = re.search(r"digitalsalgsoppgave\.garanti\.no/(" + _G_UUID + r")/\d+", txt, re.I)
    if m:
        return m.group(1)
    m = re.search(r'"estateId"\s*:\s*"(' + _G_UUID + ')"', txt, re.I)
    if m:
        return m.group(1)
    return None


# --- TR cache helper ----------------------------------------------------------
def get_tr_or_scrape(
    finn_url: str,
    *,
    prefer_cache: bool = True,
    delete_combined_on_success: bool = False,
) -> tuple[bytes | None, str | None, dict]:
    """
    1) Slår opp TR i cache: data/tilstandsrapport/<finnkode>.pdf
    2) Hvis ikke funnet: kjører fetch_prospectus_from_finn() som allerede
       forsøker å finne/klippe TR og lagrer til TR_DIR ved suksess.
    Returnerer (pdf_bytes, source_url, debug).
    """
    dbg: Dict[str, Any] = {
        "step": "start_tr_helper",
        "finn_url": finn_url,
        "used_return": None,
        "cache_checked": prefer_cache,
    }

    finnkode = _infer_finnkode(finn_url) or "prospekt"
    tr_path = TR_DIR / f"{finnkode}.pdf"

    # 1) Cache-hit?
    if prefer_cache and tr_path.exists():
        try:
            b = tr_path.read_bytes()
            if b and len(b) > 1024:
                dbg.update(
                    {
                        "step": "cache_hit_tr",
                        "tilstandsrapport_path": str(tr_path),
                        "tilstands_size": tr_path.stat().st_size,
                        "used_return": "tilstandsrapport",
                    }
                )
                return b, None, dbg
        except Exception as e:
            dbg["cache_read_error"] = f"{type(e).__name__}: {e}"

    # 2) Ingen cache → kjør full flyt (den lagrer selv ved suksess)
    b, u, inner_dbg = fetch_prospectus_from_finn(
        finn_url,
        return_tilstandsrapport_if_found=True,
        delete_combined_on_success=delete_combined_on_success,
    )
    dbg.update(inner_dbg or {})
    return b, u, dbg


# ──────────────────────────────────────────────────────────────────────────────
#  HTML → PDF-kandidater
# ──────────────────────────────────────────────────────────────────────────────
def _gather_candidate_links(soup: Any, base_url: str) -> list[tuple[int, str, str]]:
    POS_STRONG = [
        "salgsoppgave",
        "komplett salgsoppgave",
        "prospekt",
        "for utskrift",
        "utskrift",
        "digitalformat",
        "vedlegg",
        "last ned pdf",
        "se pdf",
    ]
    POS_WEAK = ["pdf"]
    NEG = [
        "egenerkl",
        "budskjema",
        "nabolagsprofil",
        "nabolag",
        "nordvikunders",
        "energiattest",
        "takst",
        "seksjon",
        "planinfo",
        "faktura",
        "skatt",
        "basiskart",
        "tegning",
        "kommunal",
        "avgift",
        "gebyr",
        "kart",
        "situasjonsplan",
        "anticimex",
        "boligkjøperforsikring",
        "prisliste",
        "garanti_10enkletips",
        "/files/doc/",
        "garanti.no/files/doc",
        "contentassets/nabolaget",
    ]

    def score_of(href: str, text: str) -> int:
        lo = (href + " " + text).lower()
        sc = 0
        if ".pdf" in lo:
            sc += 8
        for w in POS_STRONG:
            if w in lo:
                sc += 10
        for w in POS_WEAK:
            if w in lo:
                sc += 3
        for w in NEG:
            if w in lo:
                sc -= 12
        return sc

    out: list[tuple[int, str, str]] = []

    if hasattr(soup, "find_all"):
        # <a ...>
        for a in soup.find_all("a"):
            if not isinstance(a, Tag):
                continue
            text = a.get_text(" ", strip=True) or ""
            for attr in ("href", "data-href", "data-file", "download"):
                href_val = _attr_to_str(a.get(attr))
                if not href_val:
                    continue
                absu = _abs(base_url, href_val)
                if not absu:
                    continue
                sc = score_of(absu, text)
                if sc > 0:
                    out.append((sc, absu, text))

        # knapper/div/span med data-URL
        for el in soup.find_all(["button", "div", "span"]):
            if not isinstance(el, Tag):
                continue
            text = el.get_text(" ", strip=True) or ""
            for attr in ("data-href", "data-file", "data-url", "data-download"):
                href_val = _attr_to_str(el.get(attr))
                if not href_val:
                    continue
                absu = _abs(base_url, href_val)
                if not absu:
                    continue
                sc = score_of(absu, text)
                if sc > 0:
                    out.append((sc, absu, text))

    return out


def _extract_pdf_urls_from_html(html_text: str, base_url: str) -> list[tuple[int, str]]:
    if not html_text:
        return []
    raw_hits: set[str] = set()
    for m in re.finditer(r"""https?:\/\/[^\s"'<>]+\.pdf\b""", html_text, flags=re.I):
        raw_hits.add(m.group(0))
    for m in re.finditer(r"""(?<!:)\/\/[^\s"'<>]+\.pdf\b""", html_text, flags=re.I):
        raw_hits.add(m.group(0))
    for m in re.finditer(
        r"""(?<![a-zA-Z0-9])\/[^\s"'<>]+\.pdf\b""", html_text, flags=re.I
    ):
        raw_hits.add(m.group(0))

    def _score(url: str) -> int:
        lo = url.lower()
        score = 0
        if "salgsoppgav" in lo or "prospekt" in lo:
            score += 50
        if ".pdf" in lo:
            score += 10
        if (
            "nabolagsprofil" in lo
            or "anticimex" in lo
            or "nabolag" in lo
            or "nordvikunders" in lo
            or "boligkjøperforsikring" in lo
            or "prisliste" in lo
            or "garanti_10enkletips" in lo
            or "/files/doc/" in lo
            or "garanti.no/files/doc" in lo
            or "contentassets/nabolaget" in lo
        ):
            score -= 100
        return score

    out: list[tuple[int, str]] = []
    for hit in raw_hits:
        absu = _abs(base_url, hit)
        if absu:
            out.append((_score(absu), absu))
    return out


def _td_extract_pdf_urls_from_scripts(
    html_txt: str, base_url: Optional[str] = None
) -> list[str]:
    hits: list[str] = []
    try:
        for m in re.finditer(
            r'["\'](?P<u>[^"\']+?\.pdf(?:\?[^"\']*)?)["\']', html_txt, re.I
        ):
            raw = m.group("u")
            if not raw:
                continue
            u = raw.replace("\\/", "/")
            if base_url and not u.lower().startswith(("http://", "https://")):
                u = urljoin(base_url, u)
            lo = u.lower()
            if any(
                x in lo
                for x in (
                    "nabolagsprofil",
                    "contentassets/nabolaget",
                    "anticimex",
                    "nabolag",
                    "nordvikunders",
                    "boligkjøperforsikring",
                    "prisliste",
                    "garanti_10enkletips",
                    "/files/doc/",
                )
            ):
                continue
            hits.append(u)
    except Exception:
        pass
    # uniq/keep order
    seen: set[str] = set()
    out: list[str] = []
    for u in hits:
        if u not in seen:
            out.append(u)
            seen.add(u)
    return out


def _resolve_first_pdf(
    sess: requests.Session,
    candidates: list[tuple[int, str]],
    referer: str | None = None,
) -> str | None:
    headers: Dict[str, str] = {}
    if referer:
        headers["Referer"] = referer

    for _, url in sorted(candidates, key=lambda x: x[0], reverse=True):
        if url.lower().endswith(".pdf"):
            return url
        try:
            h = sess.head(url, allow_redirects=True, headers=headers, timeout=20)
            ct = (h.headers.get("Content-Type") or "").lower()
            final = str(h.url)
            if ct.startswith("application/pdf") or final.lower().endswith(".pdf"):
                return final
        except Exception:
            pass
        try:
            g = sess.get(url, allow_redirects=True, headers=headers, timeout=25)
            ct = (g.headers.get("Content-Type") or "").lower()
            final = str(g.url)
            if ct.startswith("application/pdf") or final.lower().endswith(".pdf"):
                return final
        except Exception:
            pass
    return None


def _td_resolve_first_pdf_from_strs(
    sess: requests.Session, urls: list[str], *, referer: Optional[str]
) -> Optional[str]:
    try:
        tuples = [(0, u) for u in urls]
        return _resolve_first_pdf(sess, tuples, referer=referer)
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────────────
#  Driver matching (rekkefølgen i DRIVERS = prioritet)
# ──────────────────────────────────────────────────────────────────────────────
def _match_driver(url: str):
    u = (url or "").lower()
    for d in DRIVERS:
        try:
            if d.matches(u):
                return d
        except Exception:
            continue
    return None


# ──────────────────────────────────────────────────────────────────────────────
#  Persist + sanity + failcase dump
# ──────────────────────────────────────────────────────────────────────────────
def _infer_finnkode(u: str) -> str | None:
    try:
        q = parse_qs(urlparse(u).query)
        if "finnkode" in q and q["finnkode"]:
            return str(q["finnkode"][0])
    except Exception:
        pass
    try:
        path = urlparse(u).path
        m = re.search(r"(\d{6,})", path)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def _likely_tr_url(u: Optional[str]) -> bool:
    """
    Stram heuristikk: sann kun når URL veldig tydelig peker på TR/boligsalgsrapport.
    """
    if not u:
        return False
    lo = u.lower()
    # Avvis kjente uønskede typer uansett
    if re.search(
        r"(nabolagsprofil|contentassets/nabolaget|nordvikunders|energiattest|egenerkl)",
        lo,
    ):
        return False
    # Godta kun eksplisitt TR i path/filnavn
    if ("tilstandsrapport" in lo) or ("boligsalgsrapport" in lo):
        if lo.endswith(".pdf"):
            return True
        if re.search(r"/dokument/[^/]*(tilstandsrapport|boligsalgsrapport)[^/]*", lo):
            return True
    return False


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _dump_failcase(
    finnkode: str,
    label: str,
    dbg: Dict[str, Any],
    pdf_bytes: Optional[bytes] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Lagre full debug + ev. PDF i data/failcases for rask feilsøking.
    Filnavn: YYYYMMDD-HHMMSS_<finnkode>_<label>.json/pdf
    """
    try:
        ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        stem = f"{ts}_{finnkode}_{label}"
        base = FAIL_DIR / stem

        payload = dict(dbg or {})
        if extra:
            payload["extra"] = {**payload.get("extra", {}), **extra}

        (base.with_suffix(".json")).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        if pdf_bytes:
            (base.with_suffix(".pdf")).write_bytes(pdf_bytes)
    except Exception:
        # Ikke la dump i seg selv velte kjøringen
        pass


# ---- TR sanity helpers -------------------------------------------------------
def _extract_first_pages_text(pdf_bytes: bytes, pages: int = 2) -> str:
    """Prøv å hente tekst fra de første 'pages' sidene (PyMuPDF -> PyPDF2 fallback)."""
    txt = ""
    try:
        import fitz  # PyMuPDF

        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            for i in range(min(pages, doc.page_count)):
                try:
                    t = doc.load_page(i).get_text("text") or ""
                    if t:
                        txt += "\n" + t
                except Exception:
                    pass
    except Exception:
        pass
    if txt.strip():
        return txt

    # Fallback: PyPDF2
    try:
        rdr = PdfReader(io.BytesIO(pdf_bytes))
        for i in range(min(pages, len(rdr.pages))):
            try:
                t = rdr.pages[i].extract_text() or ""
                if t:
                    txt += "\n" + t
            except Exception:
                pass
    except Exception:
        pass
    return txt


_TR_TITLE_RX = re.compile(r"^\s*(tilstandsrapport|boligsalgsrapport)\b", re.I | re.M)


def _has_strict_tr_title_first_pages(pdf_bytes: bytes) -> bool:
    """Krever at første/andre side har en klar TR-tittel-linje."""
    head = _extract_first_pages_text(pdf_bytes, pages=2)
    if not head:
        return False
    # Ikke godta hvis 'innhold'/'vedlegg' dominerer – da kreves eksplisitt TR-tittel
    if re.search(r"^\s*(innhold|vedlegg)\b", head, re.I | re.M):
        return bool(_TR_TITLE_RX.search(head))
    return bool(_TR_TITLE_RX.search(head))


def _sanity_accept_tr_bytes(pdf_bytes: bytes) -> bool:
    """
    Stram sanity: det klippede dokumentet må (a) starte ved TR-tittel
    og (b) være mer enn noen få sider. Vi forsøker også å finne et TR-span fra 0.
    """
    if not pdf_bytes or len(pdf_bytes) < 50_000:
        return False
    try:
        num_pages = len(PdfReader(io.BytesIO(pdf_bytes)).pages)
    except Exception:
        num_pages = 0
    if num_pages < 4:
        return False

    # Forsøk å la detektoren verifisere at indeksen starter ved 0/1 og er lang nok
    try:
        if callable(detect_tilstandsrapport_span):
            s2, e2, _ = detect_tilstandsrapport_span(pdf_bytes)  # type: ignore[misc]
            if s2 is None or e2 is None:
                return False
            if not (s2 in (0, 1) and (e2 - s2 + 1) >= 4):
                return False
    except Exception:
        # Detektorfail → fall gjennom til tittel-sjekk
        pass

    # Uansett: krev tydelig TR-tittel i starten
    if not _has_strict_tr_title_first_pages(pdf_bytes):
        return False
    return True


def _persist_both(
    *,
    pdf_bytes: bytes,
    pdf_url: Optional[str],
    finnkode: str,
    driver_debug: Dict[str, Any] | None,
    return_tilstandsrapport_if_found: bool,
    delete_combined_on_success: bool,
) -> tuple[bytes, Optional[str], Dict[str, Any]]:
    """
    - Hvis driver fant direkte TR → skriv kun TR til TR_DIR og returnér TR (med sanity-sjekk).
    - Hvis PDF er (trolig) prospekt → skriv prospekt til PROSPEKT_DIR,
      forsøk TR-utklipp og lagre klippet i TR_DIR; returnér TR om funnet ellers prospekt.
    """
    dbg: Dict[str, Any] = {
        "pdf_url": pdf_url,
        "pdf_path": None,
        "content_length": str(len(pdf_bytes)) if pdf_bytes else None,
        "tilstandsrapport_path": None,
        "tilstands_pages": None,
        "tilstands_size": None,
        "combined_deleted": False,
        "used_return": "combined",
        "tilstands_engine": None,
        "tilstands_method": None,
        "tilstands_impl": None,
    }

    # Sjekk om driver eksplisitt sier TR
    is_tr_direct = False
    if isinstance(driver_debug, dict):
        try:
            if driver_debug.get("is_tr_direct") is True:
                is_tr_direct = True
            meta = driver_debug.get("meta") or driver_debug.get("driver_meta") or {}
            if meta.get("is_tilstandsrapport") is True:
                is_tr_direct = True
        except Exception:
            pass

    # URL-heuristikk (sikkerhetsnett)
    if not is_tr_direct and _likely_tr_url(pdf_url):
        is_tr_direct = True

    combined_path = PROSPEKT_DIR / f"{finnkode}.pdf"
    tr_path = TR_DIR / f"{finnkode}.pdf"

    # Direkte TR → bare lagre til TR_DIR (med sanity/rollback)
    if is_tr_direct:
        try:
            _write_bytes(tr_path, pdf_bytes)

            # 1) Detektor må finne et span
            s = e = None
            if callable(detect_tilstandsrapport_span):
                try:
                    s, e, _meta = detect_tilstandsrapport_span(pdf_bytes)  # type: ignore[misc]
                except Exception:
                    s = e = None

            # 2) Ekstra streng sanity
            good = (s is not None and e is not None) and _sanity_accept_tr_bytes(
                pdf_bytes
            )
            if not good:
                # rollback – slett og fall til prospekt
                try:
                    tr_path.unlink(missing_ok=True)
                except Exception:
                    pass
                _dump_failcase(
                    finnkode,
                    "tr_direct_sanity_failed",
                    dbg,
                    pdf_bytes=pdf_bytes,
                    extra={
                        "reason": "no valid TR span / strict sanity reject",
                        "source_url": pdf_url,
                    },
                )
                is_tr_direct = False  # fallthrough til prospekt-lagring
            else:
                dbg["tilstandsrapport_path"] = str(tr_path)
                try:
                    dbg["tilstands_size"] = str(tr_path.stat().st_size)
                except Exception:
                    pass
                dbg["tilstands_pages"] = f"{(s or 0)+1}-{(e or 0)+1}"
                dbg["used_return"] = "tilstandsrapport"
                return pdf_bytes, pdf_url, dbg
        except Exception:
            # fall gjennom til «prospekt»-løype dersom noe uventet skjer
            pass

    # Hvis ikke direkte TR (eller rollback slo inn): behandle som prospekt
    _write_bytes(combined_path, pdf_bytes)
    dbg["pdf_path"] = str(combined_path)

    # Detekter TR-span for logging og evt fallback-slicing
    s = e = None
    if callable(detect_tilstandsrapport_span):
        try:
            s, e, meta = detect_tilstandsrapport_span(pdf_bytes)  # type: ignore[misc]
            if s is not None and e is not None:
                dbg["tilstands_pages"] = f"{s+1}-{e+1}"
            if isinstance(meta, dict):
                dbg["tilstands_engine"] = meta.get("engine_hint")
                dbg["tilstands_method"] = meta.get("method")
                dbg["tilstands_impl"] = meta.get("impl_version")
        except Exception:
            pass

    # Klipp TR (primær via extract_tilstandsrapport)
    clipped_ok = False
    attempted_clip = False
    if extract_tilstandsrapport:
        attempted_clip = True
        try:
            clipped_ok = bool(
                extract_tilstandsrapport(str(combined_path), str(tr_path))
            )  # type: ignore[misc]
            if clipped_ok and tr_path.exists() and tr_path.stat().st_size > 1024:
                # SANITY på klippet
                tr_bytes_tmp = tr_path.read_bytes()
                if not _sanity_accept_tr_bytes(tr_bytes_tmp):
                    # rollback – slett klippet og fortsett som prospekt
                    try:
                        tr_path.unlink()
                    except Exception:
                        pass
                    clipped_ok = False
                else:
                    dbg["tilstandsrapport_path"] = str(tr_path)
                    dbg["tilstands_size"] = tr_path.stat().st_size
        except Exception:
            clipped_ok = False

    # Fallback-slicing via PyPDF2 dersom vi har s/e
    if (not clipped_ok) and (s is not None) and (e is not None):
        attempted_clip = True
        try:
            from PyPDF2 import PdfReader as _PdfReader, PdfWriter

            rdr = _PdfReader(str(combined_path))
            w = PdfWriter()
            s2 = max(0, int(s))
            e2 = min(len(rdr.pages) - 1, int(e))
            for i in range(s2, e2 + 1):
                w.add_page(rdr.pages[i])
            with open(tr_path, "wb") as outf:
                w.write(outf)
            clipped_ok = tr_path.exists() and tr_path.stat().st_size > 1024
            if clipped_ok:
                # SANITY: dobbelt-sjekk at fallback-slicen faktisk er en TR
                try:
                    tr_bytes_tmp = tr_path.read_bytes()
                except Exception:
                    tr_bytes_tmp = b""
                if not _sanity_accept_tr_bytes(tr_bytes_tmp):
                    try:
                        tr_path.unlink()
                    except Exception:
                        pass
                    clipped_ok = False
                else:
                    dbg["tilstandsrapport_path"] = str(tr_path)
                    dbg["tilstands_size"] = tr_path.stat().st_size
                    dbg["tr_fallback"] = "pypdf2_slice"
        except Exception as _e:
            dbg["tr_fallback_error"] = f"{type(_e).__name__}: {_e}"

    # Hvis vi forsøkte å klippe men det ikke gikk → dump failcase
    if attempted_clip and not clipped_ok:
        _dump_failcase(
            finnkode,
            "tr_clip_failed",
            dbg,
            pdf_bytes=pdf_bytes,  # hele prospektet for reproduksjon
            extra={
                "span_detected": (s is not None and e is not None),
                "span": None if s is None or e is None else [int(s), int(e)],
                "source_url": pdf_url,
            },
        )

    # Hvis vi fikk til TR, returnér den (og evt slett prospektet om ønsket)
    if clipped_ok and tr_path.exists():
        try:
            tr_bytes = tr_path.read_bytes()
            if delete_combined_on_success and combined_path.exists():
                try:
                    combined_path.unlink()
                    dbg["combined_deleted"] = True
                except Exception:
                    pass
            if return_tilstandsrapport_if_found:
                dbg["used_return"] = "tilstandsrapport"
                return tr_bytes, pdf_url, dbg
        except Exception:
            pass

    # (Valgfritt) Dump “no_tr_detected_in_prospect” for statistikk/feilsøk
    _dump_failcase(
        finnkode,
        "no_tr_detected_in_prospect",
        dbg,
        pdf_bytes=None,  # bytt til pdf_bytes hvis du vil lagre hele prospektet her også
        extra={"source_url": pdf_url},
    )

    # Ellers returner prospektet
    return pdf_bytes, pdf_url, dbg


# --- Nettverksdiagnostikk for failcases --------------------------------------
def _net_diag_for_exception(
    url: str | None, sess: requests.Session | None = None
) -> dict:
    import sys, platform, socket

    info = {
        "timestamp_utc": dt.datetime.utcnow().isoformat() + "Z",
        "cwd": os.getcwd(),
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "python": sys.version,
        "requests_version": getattr(requests, "__version__", None),
        "env_http_proxy": os.environ.get("http_proxy") or os.environ.get("HTTP_PROXY"),
        "env_https_proxy": os.environ.get("https_proxy")
        or os.environ.get("HTTPS_PROXY"),
    }

    # Session-proxies
    if isinstance(sess, requests.Session):
        try:
            info["session_proxies"] = getattr(sess, "proxies", None)
        except Exception:
            pass

    # DNS test for host i URL
    host = None
    try:
        if url:
            host = urlparse(url).hostname
    except Exception:
        pass
    if host:
        try:
            addrs = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            info["dns_getaddrinfo"] = list(
                {f"{a[4][0]}:{a[4][1]}" for a in addrs if a and a[4]}
            )
        except Exception as e:
            info["dns_getaddrinfo_error"] = f"{type(e).__name__}: {e}"

    # Minimal reachability probe
    try:
        r0 = requests.get("https://example.com", timeout=5)
        info["probe_example_com"] = {"ok": r0.ok, "status": r0.status_code}
    except Exception as e:
        info["probe_example_com_error"] = f"{type(e).__name__}: {e}"

    # Domenespesifikk probe
    if host:
        try:
            test_url = f"https://{host}/"
            r1 = requests.get(test_url, timeout=5)
            info["probe_domain_root"] = {"ok": r1.ok, "status": r1.status_code}
        except Exception as e:
            info["probe_domain_root_error"] = f"{type(e).__name__}: {e}"

    return info


# ──────────────────────────────────────────────────────────────────────────────
#  Hovedløype fra FINN
# ──────────────────────────────────────────────────────────────────────────────
def fetch_prospectus_from_finn(
    finn_url: str,
    *,
    save_dir: str = "data/prospekt",  # beholdt for kompatibilitet (ikke brukt til TR)
    return_tilstandsrapport_if_found: bool = True,
    delete_combined_on_success: bool = False,
) -> Tuple[bytes | None, str | None, dict]:
    dbg: Dict[str, Any] = {
        "step": "start",
        "finn_url": finn_url,
        "megler_url": None,
        "driver_name": None,
        "driver_debug": None,
        "browser_debug": None,
        "pdf_url": None,
        "pdf_path": None,
        "content_type": None,
        "content_length": None,
        "tilstandsrapport_path": None,
        "tilstands_pages": None,
        "tilstands_size": None,
        "combined_deleted": False,
        "used_return": "combined",
        "tilstands_engine": None,
        "tilstands_method": None,
        "tilstands_impl": None,
    }
    if TR_IMPORT_ERROR:
        dbg["tr_import_error"] = TR_IMPORT_ERROR

    out_dir = Path(save_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    finnkode = _infer_finnkode(finn_url) or "prospekt"

    def _postprocess_and_return(
        pdf_bytes: bytes, source_url: Optional[str], driver_dbg: Dict[str, Any] | None
    ) -> tuple[bytes, Optional[str], dict]:
        used_bytes, url, meta = _persist_both(
            pdf_bytes=pdf_bytes,
            pdf_url=source_url,
            finnkode=finnkode,
            driver_debug=driver_dbg,
            return_tilstandsrapport_if_found=return_tilstandsrapport_if_found,
            delete_combined_on_success=delete_combined_on_success,
        )
        dbg.update(meta)
        return used_bytes, url, dbg

    try:
        sess: requests.Session = new_session()

        # 1) FINN → megler-URL
        megler_url, _html = discover_megler_url(finn_url)
        dbg["megler_url"] = megler_url

        # 2) Driver først
        drv = _match_driver(megler_url)
        if drv:
            dbg["driver_name"] = getattr(drv, "name", drv.__class__.__name__)
            try:
                b, u, ddbg = drv.try_fetch(sess, megler_url)  # type: ignore[attr-defined]
            except Exception as e:
                b, u, ddbg = None, None, {"error": f"{type(e).__name__}: {e}"}
            dbg["driver_debug"] = ddbg
            if isinstance(b, (bytes, bytearray)) and b:
                # Viktig: ikke avvis små PDF-er her (kan være direkte TR)
                dbg["step"] = "driver_ok"
                dbg["pdf_url"] = u or megler_url
                return _postprocess_and_return(cast(bytes, b), u or megler_url, ddbg)

        # 3) Uten driver: generisk scraping
        soup1, html1 = _new_bs_soup(sess, finn_url)
        pdf_url: Optional[str] = None

        hits1 = _td_extract_pdf_urls_from_scripts(html1 or "", base_url=finn_url)
        if hits1:
            pdf_url = _td_resolve_first_pdf_from_strs(sess, hits1, referer=finn_url)

        if not pdf_url:
            cand1 = _gather_candidate_links(soup1, finn_url)
            if cand1:
                pdf_url = _resolve_first_pdf(
                    sess, [(s, u) for (s, u, _t) in cand1], referer=finn_url
                )

        if not pdf_url:
            grep1 = _extract_pdf_urls_from_html(html1 or "", base_url=finn_url)
            if grep1:
                pdf_url = _td_resolve_first_pdf_from_strs(
                    sess, [u for _s, u in grep1], referer=finn_url
                )

        # Garanti-spesifikk fallback (estateId → mini-PDF → DS)
        if (
            (not pdf_url)
            and (megler_url or "")
            and ("garanti.no/eiendom/" in (megler_url or "").lower())
        ):
            estate_id = _garanti_find_estateid_in_text(html1 or "")
            if estate_id:
                mv_url = f"https://meglervisning.no/salgsoppgave/hent?instid=MSGAR&estateid={estate_id}"
                try:
                    r_mv = sess.get(
                        mv_url,
                        headers={
                            **BROWSER_HEADERS,
                            "Accept": "application/pdf,application/octet-stream,*/*",
                            "Referer": finn_url,
                            "Origin": "https://www.garanti.no",
                        },
                        timeout=30,
                        allow_redirects=True,
                    )
                    ct = (r_mv.headers.get("Content-Type") or "").lower()
                    if r_mv.ok and (
                        ("pdf" in ct) or (r_mv.content and r_mv.content[:4] == b"%PDF")
                    ):
                        dbg["step"] = "ok_garanti_mv"
                        dbg["pdf_url"] = mv_url
                        return _postprocess_and_return(r_mv.content, mv_url, None)
                except Exception:
                    pass

        # 3b) Hopp til megler-side og prøv tilsvarende
        if not pdf_url and megler_url:
            soup2, html2 = _new_bs_soup(sess, megler_url, referer=finn_url)

            # Garanti DS direkte i megler-HTML
            try:
                m_ds = re.search(
                    r'https?://digitalsalgsoppgave\.garanti\.no/[^\s"\']+',
                    html2 or "",
                    re.I,
                )
            except Exception:
                m_ds = None
            if m_ds:
                ds_url = m_ds.group(0)
                dbg["pdf_url"] = ds_url
                try:
                    b_ds, u_ds, bdbg_ds = fetch_pdf_with_browser(ds_url)
                    dbg["browser_debug"] = bdbg_ds
                    if b_ds:
                        dbg["step"] = "browser_ok_ds"
                        return _postprocess_and_return(b_ds, u_ds or ds_url, None)
                except Exception:
                    pass

            hits2 = _td_extract_pdf_urls_from_scripts(html2 or "", base_url=megler_url)
            if hits2:
                pdf_url = _td_resolve_first_pdf_from_strs(
                    sess, hits2, referer=megler_url
                )

            if not pdf_url:
                cand2 = _gather_candidate_links(soup2, megler_url)
                if cand2:
                    pdf_url = _resolve_first_pdf(
                        sess, [(s, u) for (s, u, _t) in cand2], referer=megler_url
                    )

            if not pdf_url:
                grep2 = _extract_pdf_urls_from_html(html2 or "", base_url=megler_url)
                if grep2:
                    pdf_url = _td_resolve_first_pdf_from_strs(
                        sess, [u for _s, u in grep2], referer=megler_url
                    )

        # 4) Hvis vi nå har URL → last ned med requests
        if pdf_url:
            # Avvis nabolagsprofil uansett
            if re.search(
                r"(nabolagsprofil|contentassets/nabolaget|nordvikunders)",
                pdf_url,
                re.I,
            ):
                dbg["skip_reason"] = "filtered_neighborhood_profile"
                pdf_url = None

        if pdf_url:
            dbg["pdf_url"] = pdf_url
            referers: list[Optional[str]] = [megler_url, finn_url, None]
            tried: set[str] = set()
            for candidate in [pdf_url, _clean_url(pdf_url)]:
                if candidate in tried:
                    continue
                tried.add(candidate)
                for ref in referers:
                    headers = {
                        **BROWSER_HEADERS,
                        "Accept": "application/pdf,application/octet-stream,*/*",
                    }
                    if ref:
                        headers["Referer"] = ref
                        try:
                            pr = urlparse(ref)
                            headers["Origin"] = f"{pr.scheme}://{pr.netloc}"
                        except Exception:
                            pass
                    try:
                        r = sess.get(
                            candidate, headers=headers, timeout=30, allow_redirects=True
                        )
                        ct = (r.headers.get("Content-Type") or "").lower()
                        if r.status_code < 400 and (
                            ("pdf" in ct) or (r.content and r.content[:4] == b"%PDF")
                        ):
                            dbg["step"] = "ok"
                            dbg["content_type"] = r.headers.get("Content-Type")
                            dbg["content_length"] = r.headers.get("Content-Length")
                            return _postprocess_and_return(r.content, candidate, None)
                    except Exception:
                        continue

        # 5) Fallback: Playwright
        dbg["step"] = "browser_try"
        start_url = megler_url or finn_url

        if megler_url and "nordvikbolig.no/boliger/" in megler_url.lower():
            b2, u2, bdbg = fetch_pdf_with_browser_filtered(
                start_url,
                click_text_contains=[
                    "tilstandsrapport",
                    "se tilstandsrapport",
                    "fidens",
                    "tilstandsrapport_for",
                ],
                allow_only_if_url_contains=[
                    "tilstandsrapport",
                    "estates",
                    "nordvik-vitec-documents",
                    "fidens",
                ],
                deny_if_url_contains=[
                    "nabolag",
                    "nabolagsprofil",
                    "contentassets/nabolaget",
                    "energiattest",
                    "egenerkl",
                    "salgsoppgave",
                ],
                timeout_ms=45000,
            )
        else:
            b2, u2, bdbg = fetch_pdf_with_browser(start_url)

        dbg["browser_debug"] = bdbg
        if b2:
            dbg["step"] = "browser_ok"
            dbg["pdf_url"] = u2 or start_url
            return _postprocess_and_return(b2, u2 or start_url, None)

        # Feil: ingen PDF
        dbg["step"] = "failed"
        dbg["error"] = "no_pdf_found"
        _dump_failcase(
            finnkode,
            "no_pdf_found",
            dbg,
            pdf_bytes=None,
            extra={"megler_url": dbg.get("megler_url")},
        )
        return None, None, dbg

    except Exception as e:
        dbg["step"] = "exception"
        dbg["error"] = f"{type(e).__name__}: {e}"
        try:
            dbg.setdefault("extra", {})
            dbg["extra"]["traceback"] = traceback.format_exc()
            dbg["extra"]["net_diag"] = _net_diag_for_exception(finn_url, sess=None)
        except Exception:
            pass
        _dump_failcase(_infer_finnkode(finn_url) or "prospekt", "exception", dbg)
        return None, None, dbg


# ──────────────────────────────────────────────────────────────────────────────
#  Hjelper: hvis du allerede har megler-URL (hopper over FINN)
# ──────────────────────────────────────────────────────────────────────────────
def fetch_prospectus_from_megler_url(
    megler_url: str,
    *,
    save_dir: str = "data/prospekt",
    return_tilstandsrapport_if_found: bool = True,
    delete_combined_on_success: bool = False,
) -> Tuple[bytes | None, str | None, dict]:
    dbg: Dict[str, Any] = {
        "step": "start",
        "megler_url": megler_url,
        "driver_name": None,
        "driver_debug": None,
        "browser_debug": None,
        "pdf_url": None,
        "pdf_path": None,
        "content_type": None,
        "content_length": None,
        "tilstandsrapport_path": None,
        "tilstands_pages": None,
        "tilstands_size": None,
        "combined_deleted": False,
        "used_return": "combined",
        "tilstands_engine": None,
        "tilstands_method": None,
        "tilstands_impl": None,
    }
    if TR_IMPORT_ERROR:
        dbg["tr_import_error"] = TR_IMPORT_ERROR

    m = re.search(r"(\d{6,})", megler_url)
    finnkode = m.group(1) if m else "prospekt"

    def _postprocess_and_return(
        pdf_bytes: bytes, source_url: Optional[str], driver_dbg: Dict[str, Any] | None
    ) -> tuple[bytes, Optional[str], dict]:
        used_bytes, url, meta = _persist_both(
            pdf_bytes=pdf_bytes,
            pdf_url=source_url,
            finnkode=finnkode,
            driver_debug=driver_dbg,
            return_tilstandsrapport_if_found=return_tilstandsrapport_if_found,
            delete_combined_on_success=delete_combined_on_success,
        )
        dbg.update(meta)
        return used_bytes, url, dbg

    try:
        sess: requests.Session = new_session()

        drv = _match_driver(megler_url)
        if drv:
            dbg["driver_name"] = getattr(drv, "name", drv.__class__.__name__)
            try:
                b, u, ddbg = drv.try_fetch(sess, megler_url)  # type: ignore[attr-defined]
            except Exception as e:
                b, u, ddbg = None, None, {"error": f"{type(e).__name__}: {e}"}
            dbg["driver_debug"] = ddbg
            if isinstance(b, (bytes, bytearray)) and b:
                dbg["step"] = "driver_ok"
                dbg["pdf_url"] = u or megler_url
                return _postprocess_and_return(cast(bytes, b), u or megler_url, ddbg)

        # Fallback: browser (Nordvik bruker filtrert)
        dbg["step"] = "browser_try"
        if "nordvikbolig.no/boliger/" in (megler_url or "").lower():
            b2, u2, bdbg = fetch_pdf_with_browser_filtered(
                megler_url,
                click_text_contains=[
                    "tilstandsrapport",
                    "se tilstandsrapport",
                    "fidens",
                    "tilstandsrapport_for",
                ],
                allow_only_if_url_contains=[
                    "tilstandsrapport",
                    "estates",
                    "nordvik-vitec-documents",
                    "fidens",
                ],
                deny_if_url_contains=[
                    "nabolag",
                    "nabolagsprofil",
                    "contentassets/nabolaget",
                    "energiattest",
                    "egenerkl",
                    "salgsoppgave",
                ],
                timeout_ms=45000,
            )
        else:
            b2, u2, bdbg = fetch_pdf_with_browser(megler_url)

        dbg["browser_debug"] = bdbg
        if b2:
            dbg["step"] = "browser_ok"
            dbg["pdf_url"] = u2 or megler_url
            return _postprocess_and_return(b2, u2 or megler_url, None)

        dbg["step"] = "failed"
        dbg["error"] = "no_pdf_found"
        _dump_failcase(
            finnkode,
            "no_pdf_found",
            dbg,
            pdf_bytes=None,
            extra={"megler_url": megler_url},
        )
        return None, None, dbg

    except Exception as e:
        dbg["step"] = "exception"
        dbg["error"] = f"{type(e).__name__}: {e}"
        try:
            dbg.setdefault("extra", {})
            dbg["extra"]["traceback"] = traceback.format_exc()
            dbg["extra"]["net_diag"] = _net_diag_for_exception(megler_url, sess=None)
        except Exception:
            pass
        _dump_failcase(_infer_finnkode(megler_url) or "prospekt", "exception", dbg)
        return None, None, dbg


# ──────────────────────────────────────────────────────────────────────────────
#  Lagring (beholdt for bakover-kompatibilitet)
# ──────────────────────────────────────────────────────────────────────────────
def save_pdf_locally(finnkode: str, pdf_bytes: bytes) -> str:
    os.makedirs(PROSPEKT_DIR, exist_ok=True)
    path = PROSPEKT_DIR / f"{finnkode}.pdf"
    path.write_bytes(pdf_bytes)
    return str(path)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Bruk: python -m core.fetch <FINN_URL>")
        sys.exit(1)
    url = sys.argv[1]
    b, u, dbg = fetch_prospectus_from_finn(url, delete_combined_on_success=False)
    print(json.dumps(dbg, indent=2, ensure_ascii=False))
    print("bytes:", 0 if b is None else len(b))
    print("pdf_url:", u)
