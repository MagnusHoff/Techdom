# core/fetch.py
from __future__ import annotations

from typing import Any, Dict, Tuple, Optional, List, cast
from pathlib import Path
from urllib.parse import urlparse, urljoin, parse_qs, urlunparse
import datetime as dt
import hashlib
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
    fetch_pdf_with_browser_filtered,  # brukt for målrettet "Salgsoppgave"-klikk
)
from .http_headers import BROWSER_HEADERS
from .drivers import DRIVERS  # ⬅️ bruk spesifikke megler-drivere

# ✅ S3-prospekt-lagring
from .s3_prospekt_store import upload_prospekt

# Prøv å importere boto3 for S3-lesing/HEAD
try:
    import boto3  # type: ignore
    from botocore.exceptions import BotoCoreError as _BotoCoreError, ClientError as _ClientError  # type: ignore
except Exception:  # pragma: no cover
    boto3 = None  # type: ignore

    class _BotoCoreError(Exception):  # type: ignore
        pass

    class _ClientError(Exception):  # type: ignore
        pass


# ──────────────────────────────────────────────────────────────────────────────
#  Stier (lokal speiling kun for debugging/utvikling)
# ──────────────────────────────────────────────────────────────────────────────
DATA_DIR = Path("data")
PROSPEKT_DIR = DATA_DIR / "prospekt"
FAIL_DIR = DATA_DIR / "failcases"

PROSPEKT_DIR.mkdir(parents=True, exist_ok=True)
FAIL_DIR.mkdir(parents=True, exist_ok=True)

# Lokal speiling av S3 (skriv kopi lokalt etter vellykket opplasting)
LOCAL_MIRROR = os.getenv("TD_LOCAL_MIRROR", "1") not in {"0", "false", "False"}

# S3-prospekt-innstillinger
PROSPEKT_BUCKET = os.getenv("PROSPEKT_BUCKET", "").strip()
PROSPEKT_PREFIX = (
    (os.getenv("PROSPEKT_PREFIX", "prospekt") or "prospekt").strip().strip("/")
)
AWS_PROSPEKT_REGION = os.getenv("AWS_PROSPEKT_REGION", "eu-north-1").strip()
AWS_PROSPEKT_ACCESS_KEY_ID = os.getenv("AWS_PROSPEKT_ACCESS_KEY_ID", "").strip()
AWS_PROSPEKT_SECRET_ACCESS_KEY = os.getenv("AWS_PROSPEKT_SECRET_ACCESS_KEY", "").strip()


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


def _sha256_bytes(b: bytes) -> str:
    h = hashlib.sha256()
    h.update(b)
    return h.hexdigest()


def _sha256_file(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


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


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


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
#  S3-hjelpere (prospekter)
# ──────────────────────────────────────────────────────────────────────────────
def _prospekt_s3_enabled() -> bool:
    return bool(
        boto3
        and PROSPEKT_BUCKET
        and PROSPEKT_PREFIX
        and AWS_PROSPEKT_ACCESS_KEY_ID
        and AWS_PROSPEKT_SECRET_ACCESS_KEY
    )


def _prospekt_key(finnkode: str) -> str:
    return f"{PROSPEKT_PREFIX}/{finnkode}.pdf"


def _prospekt_client():
    assert boto3 is not None, "boto3 mangler"
    return boto3.client(
        "s3",
        region_name=AWS_PROSPEKT_REGION,
        aws_access_key_id=AWS_PROSPEKT_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_PROSPEKT_SECRET_ACCESS_KEY,
    )


def _s3_head(key: str) -> Optional[dict]:
    if not _prospekt_s3_enabled():
        return None
    try:
        c = _prospekt_client()
        return c.head_object(Bucket=PROSPEKT_BUCKET, Key=key)
    except Exception:
        return None


def _s3_get_bytes(key: str) -> Optional[bytes]:
    if not _prospekt_s3_enabled():
        return None
    try:
        c = _prospekt_client()
        obj = c.get_object(Bucket=PROSPEKT_BUCKET, Key=key)
        return obj["Body"].read()
    except Exception:
        return None


def _presigned_get(key: str, expire: int = 3600) -> Optional[str]:
    if not _prospekt_s3_enabled():
        return None
    try:
        c = _prospekt_client()
        return c.generate_presigned_url(
            "get_object",
            Params={"Bucket": PROSPEKT_BUCKET, "Key": key},
            ExpiresIn=expire,
        )
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────────────
#  PDF-tekst fra bytes (debug/verdi-ekstraksjon)
# ──────────────────────────────────────────────────────────────────────────────
def extract_pdf_text_from_bytes(pdf_bytes: bytes, max_pages: int = 40) -> str:
    """
    Prøver PyMuPDF (fitz) først for mer robust tekst, faller tilbake til PyPDF2.
    """
    # 1) PyMuPDF (fitz)
    try:
        import fitz  # type: ignore

        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            chunks: List[str] = []
            upto = min(doc.page_count, max_pages)
            for i in range(upto):
                try:
                    t = doc.load_page(i).get_text("text") or ""
                except Exception:
                    t = ""
                if t.strip():
                    chunks.append(t)
            if chunks:
                return "\n".join(chunks).strip()
    except Exception:
        pass

    # 2) PyPDF2 fallback
    try:
        bio = io.BytesIO(pdf_bytes)
        reader = PdfReader(bio)
        chunks = []
        upto = min(len(reader.pages), max_pages)
        for page in reader.pages[:upto]:
            try:
                t = page.extract_text() or ""
            except Exception:
                t = ""
            if t.strip():
                chunks.append(t)
        return "\n".join(chunks).strip()
    except Exception:
        return ""


# ──────────────────────────────────────────────────────────────────────────────
#  Refinement: trekk ut salgsoppgave-delen fra samle-PDF (valgfritt bruk)
# ──────────────────────────────────────────────────────────────────────────────
def refine_salgsoppgave_from_bundle(
    pdf_bytes: bytes,
) -> Tuple[bytes | None, Dict[str, Any]]:
    """
    Forsøker å trimme 'Vedlegg til salgsoppgave' slik at bare selve salgsoppgaven blir igjen.
    Return: (pdf_bytes_ren, meta)
    """
    meta: Dict[str, Any] = {}
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception as e:
        return None, {"error": f"read_fail:{e!r}"}

    n = len(reader.pages)
    meta["pages_total"] = n
    if n == 0:
        return None, {"error": "empty_pdf"}

    POS = [
        r"\bsalgsoppgav",
        r"\bprospekt",
        r"\bmegler",
        r"eiendom",
        r"adresser",
        r"fakta",
        r"innhold",
        r"om eiendommen",
        r"nabolag",
        r"beliggenhet",
        r"standard",
        r"bebyggelse",
        r"adkomst",
        r"kommunenr",
        r"gnr",
        r"bnr",
    ]
    NEG = [
        r"\btilstandsrapport",
        r"\begenerkl",
        r"\benergiattest",
        r"\bbudskjema",
        r"\bkommunale opplysninger",
        r"\bbygningstegninger",
        r"\bdokumentasjon",
        r"\bløsøre",
        r"\bvedlegg\b",
        r"\bmeglerpakke",
    ]
    POS_RX = [re.compile(rx, re.I) for rx in POS]
    NEG_RX = [re.compile(rx, re.I) for rx in NEG]

    scores: List[int] = []
    texts: List[str] = []
    for i in range(n):
        try:
            txt = reader.pages[i].extract_text() or ""
        except Exception:
            txt = ""
        texts.append(txt)
        lo = txt.lower()
        sc = sum(2 for rx in POS_RX if rx.search(lo)) - sum(
            4 for rx in NEG_RX if rx.search(lo)
        )
        sc += max(0, 5 - i)  # litt bias for tidlige sider
        scores.append(sc)

    cut_at: Optional[int] = None
    for i, txt in enumerate(texts):
        lo = txt.lower()
        if any(rx.search(lo) for rx in NEG_RX) and i >= 3:
            cut_at = i
            break

    end = min(cut_at if cut_at is not None else n, 80)

    # fallback: finn beste 20–40 siders vindu hvis starten ser rar ut
    if sum(1 for s in scores[: min(10, n)] if s > 0) <= 2:
        best_sum, best_range = -(10**9), (0, min(30, n))
        for w in (20, 30, 40):
            if w > n:
                continue
            for i in range(0, n - w + 1):
                ssum = sum(scores[i : i + w])
                if ssum > best_sum:
                    best_sum, best_range = ssum, (i, i + w)
        start, end = best_range
        if cut_at is not None and cut_at < start:
            end = cut_at

    from PyPDF2 import PdfWriter

    writer = PdfWriter()
    out_pages = 0
    for i in range(0, max(1, end)):
        try:
            writer.add_page(reader.pages[i])
            out_pages += 1
        except Exception:
            continue

    buf = io.BytesIO()
    try:
        writer.write(buf)
        out = buf.getvalue()
        meta.update(
            {
                "pages_out": out_pages,
                "cut_at": cut_at,
                "scores_head": scores[:10],
            }
        )
        return out, meta
    except Exception as e:
        return None, {"error": f"write_fail:{e!r}"}


# ──────────────────────────────────────────────────────────────────────────────
#  FINN-attributter (for areal/rom osv.)
# ──────────────────────────────────────────────────────────────────────────────
_M2_RX = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:m²|m2|m\^2|kvm|kvadrat)", re.IGNORECASE)


def _parse_m2_from_text(txt: str | None) -> Optional[float]:
    if not txt:
        return None
    m = _M2_RX.search(txt)
    return _to_float(m.group(1)) if m else None


def _kv(txt: str | None) -> Optional[Tuple[str, str]]:
    if not txt:
        return None
    m = re.match(r"\s*([^:]{3,}):\s*(.+)\s*$", txt)
    if m:
        return (m.group(1).strip(), m.group(2).strip())
    m = re.match(r"\s*([A-Za-zÆØÅæøå0-9()\-\/\. ]{3,}?)\s{2,}(.+)\s*$", txt)
    if m:
        return (m.group(1).strip(), m.group(2).strip())
    return None


def _collect_attrs(soup: Any) -> Dict[str, str]:
    attrs: Dict[str, str] = {}

    # <dl><dt>/<dd>
    if hasattr(soup, "find_all"):
        for dl in soup.find_all("dl"):
            if not isinstance(dl, Tag):
                continue
            dts = [t for t in dl.find_all("dt") if isinstance(t, Tag)]
            dds = [t for t in dl.find_all("dd") if isinstance(t, Tag)]
            if dts and dds and len(dts) == len(dds):
                for dt, dd in zip(dts, dds):
                    k = (dt.get_text(" ", strip=True) or "").strip()
                    v = (dd.get_text(" ", strip=True) or "").strip()
                    if k and v and k not in attrs:
                        attrs[k] = v

    # tabeller
    if hasattr(soup, "find_all"):
        for table in soup.find_all("table"):
            if not isinstance(table, Tag):
                continue
            for tr in table.find_all("tr"):
                if not isinstance(tr, Tag):
                    continue
                tds = [t for t in tr.find_all(["th", "td"]) if isinstance(t, Tag)]
                if len(tds) >= 2:
                    k = (tds[0].get_text(" ", strip=True) or "").strip()
                    v = (tds[1].get_text(" ", strip=True) or "").strip()
                    if k and v and k not in attrs:
                        attrs[k] = v

    # diverse lister
    if hasattr(soup, "select"):
        for container in soup.select(
            "[data-testid*='object-facts'], [data-testid*='facts'], [class*='fact'], [class*='key'], [class*='info']"
        ):
            if not isinstance(container, Tag):
                continue
            for el in container.find_all(["li", "div", "span"]):
                if not isinstance(el, Tag):
                    continue
                txt = el.get_text(" ", strip=True)
                kv = _kv(txt)
                if kv:
                    k, v = kv
                    if k and v and k not in attrs:
                        attrs[k] = v
    return attrs


def _to_float(x: str | float | int | None) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(str(x).strip().replace(" ", "").replace(",", "."))
    except Exception:
        return None


def choose_area_m2(attrs: Dict[str, str], page_text: str) -> Optional[float]:
    bra_keys = ["bruksareal", "bra"]
    prom_keys = ["primærrom", "p-rom", "prom", "p rom"]
    area_keys = ["boligareal", "areal"]

    def _get_first(keys: List[str]) -> Optional[float]:
        for want in keys:
            for k, v in attrs.items():
                if _norm(want) in _norm(k):
                    val = _parse_m2_from_text(v)
                    if val:
                        return val
        return None

    v = _get_first(bra_keys) or _get_first(prom_keys) or _get_first(area_keys)
    if v:
        return v

    text = page_text or ""
    for kw in bra_keys + prom_keys + area_keys:
        rx = re.compile(
            rf"{re.escape(kw)}[^0-9]{{0,40}}(\d+(?:[.,]\d+)?)\s*(?:m²|m2|m\^2|kvm)",
            re.IGNORECASE,
        )
        m = rx.search(text)
        if m:
            return _to_float(m.group(1))

    return _parse_m2_from_text(text)


def choose_rooms(attrs: Dict[str, str], page_text: str) -> Optional[int]:
    for want in ["soverom", "antall soverom", "rom", "antall rom"]:
        for k, v in attrs.items():
            if _norm(want) in _norm(k):
                m = re.search(r"(\d+)", str(v))
                if m:
                    return int(m.group(1))
    m = re.search(r"(?:soverom|rom)\D{0,10}(\d+)", page_text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


# ──────────────────────────────────────────────────────────────────────────────
#  HTML → PDF-kandidater (prospekt only)
# ──────────────────────────────────────────────────────────────────────────────
POS_STRONG = [
    "salgsoppgave",
    "komplett salgsoppgave",
    "prospekt",
    "salgsprospekt",
    "salgspresentasjon",
    "for utskrift",
    "utskrift",
    "digitalformat",
    "last ned pdf",
    "se pdf",
]
POS_WEAK = ["pdf"]

NEG_ALWAYS = [
    # dokumenter vi IKKE vil ha
    "tilstandsrapport",
    "boligsalgsrapport",
    "byggteknisk",
    "fidens",
    "egenerkl",
    "egenerklæring",
    "energiattest",
    "epc",
    "nabolag",
    "nabolagsprofil",
    "nordvikunders",
    "anticimex",
    "boligkjøperforsikring",
    "prisliste",
    "/files/doc/",
    "garanti.no/files/doc",
    "contentassets/nabolaget",
    "budskjema",
    "samtykke",
    "planinfo",
    "tegning",
    "seksjon",
    "kart",
    "situasjonsplan",
    "kommunal",
    "gebyr",
    "avgift",
    "skatt",
]


def _score_pdf_link_for_prospect(href: str, text: str) -> int:
    lo = (href + " " + text).lower()
    sc = 0
    if ".pdf" in lo:
        sc += 8
    for w in POS_STRONG:
        if w in lo:
            sc += 10
    for w in POS_WEAK:
        if w in lo:
            sc += 2
    for w in NEG_ALWAYS:
        if w in lo:
            sc -= 30
    return sc


def _gather_candidate_links(soup: Any, base_url: str) -> list[tuple[int, str, str]]:
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
                sc = _score_pdf_link_for_prospect(absu, text)
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
                sc = _score_pdf_link_for_prospect(absu, text)
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
        if "salgsoppgav" in lo or "prospekt" in lo or "salgsprospekt" in lo:
            score += 50
        if ".pdf" in lo:
            score += 10
        if any(x in lo for x in NEG_ALWAYS):
            score -= 100
        return score

    out: list[tuple[int, str]] = []
    for hit in raw_hits:
        absu = _abs(base_url, hit)
        if absu:
            out.append((_score(absu), absu))
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
#  Prospekt-cache & persist (S3 som sannhet, lokal speil valgfritt)
# ──────────────────────────────────────────────────────────────────────────────
def _meta_path_for(finnkode: str) -> Path:
    return PROSPEKT_DIR / f"{finnkode}.json"


def _load_meta(finnkode: str) -> dict:
    p = _meta_path_for(finnkode)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_meta(finnkode: str, meta: dict) -> None:
    p = _meta_path_for(finnkode)
    p.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


def _persist_to_s3_and_mirror(
    *, pdf_bytes: bytes, pdf_url: str | None, finnkode: str
) -> tuple[bytes, str | None, dict]:
    """
    Laster opp til S3 (primær), speiler lokalt (valgfritt), oppdaterer meta.
    Returnerer bytes + presigned URL (fra upload_prospekt).
    """
    out_dbg: Dict[str, Any] = {
        "used_return": "prospekt_s3",
        "pdf_url": pdf_url,
        "s3_key": None,
        "s3_uri": None,
        "presigned_url": None,
        "local_path": None,
        "pdf_hash": None,
        "pdf_uploaded": False,
        "cache_equal": False,
    }

    new_hash = _sha256_bytes(pdf_bytes)

    # S3-opplasting (primær)
    up = upload_prospekt(
        local_path=_mirror_tmp_write(finnkode, pdf_bytes),
        finnkode=finnkode,
        url_expire=3600,
    )
    # upload_prospekt returnerer dict med s3_uri, url (presigned), bucket, key
    out_dbg["s3_uri"] = up.get("s3_uri")
    out_dbg["s3_key"] = up.get("key")
    out_dbg["presigned_url"] = up.get("url")
    out_dbg["pdf_uploaded"] = True
    out_dbg["pdf_hash"] = new_hash

    # Lokal speiling (for dev/feilsøk)
    if LOCAL_MIRROR:
        local_path = PROSPEKT_DIR / f"{finnkode}.pdf"
        try:
            _write_bytes(local_path, pdf_bytes)
            out_dbg["local_path"] = str(local_path)
        except Exception:
            pass

    # Oppdater meta (lokal fil for debug)
    meta = _load_meta(finnkode)
    meta.update(
        {
            "finnkode": finnkode,
            "source_pdf_url": pdf_url,
            "saved_at": dt.datetime.utcnow().isoformat() + "Z",
            "sha256": new_hash,
            "size": len(pdf_bytes),
            "s3": {
                "bucket": up.get("bucket"),
                "key": up.get("key"),
                "s3_uri": up.get("s3_uri"),
            },
        }
    )
    _save_meta(finnkode, meta)

    return pdf_bytes, up.get("url"), out_dbg


def _mirror_tmp_write(finnkode: str, pdf_bytes: bytes) -> Path:
    """
    Skriv bytes til en tmp-fil for bruk i upload_prospekt (boto3.upload_file trenger path).
    """
    tmp_dir = PROSPEKT_DIR / "_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    p = tmp_dir / f"{finnkode}.pdf"
    p.write_bytes(pdf_bytes)
    return p


def get_prospect_or_scrape(
    finn_url: str,
    *,
    prefer_cache: bool = True,
    verify_remote: bool = False,
) -> tuple[bytes | None, str | None, dict]:
    """
    Hovedinngang for UI-knappen "Hent data":
      PRODUKSJON/LOKALT (nå S3 som sannhet):
        1) Sjekk S3 for prospekt: HEAD + GET hvis finnes.
        2) Hvis ikke finnes → scrape → last opp til S3 → returner bytes+presigned URL.
      Lokalt speil (kun debug): eventuell kopi i data/prospekt.
    """
    dbg: Dict[str, Any] = {
        "step": "start_prospect_helper",
        "finn_url": finn_url,
        "used_return": None,
        "prefer_cache": prefer_cache,
        "verify_remote": verify_remote,
    }

    finnkode = _infer_finnkode(finn_url) or "prospekt"
    s3_key = _prospekt_key(finnkode)

    # 1) Sjekk S3 først
    if _prospekt_s3_enabled() and prefer_cache:
        head = _s3_head(s3_key)
        if head:
            b = _s3_get_bytes(s3_key)
            if b:
                dbg.update(
                    {
                        "step": "s3_cache_hit",
                        "s3_key": s3_key,
                        "content_length": head.get("ContentLength"),
                        "etag": head.get("ETag"),
                        "used_return": "prospekt_s3_cache",
                    }
                )
                presigned = _presigned_get(s3_key, expire=3600)
                # Lokal speil oppdateres valgfritt
                if LOCAL_MIRROR:
                    try:
                        _write_bytes(PROSPEKT_DIR / f"{finnkode}.pdf", b)
                    except Exception:
                        pass
                return b, presigned, dbg

    # 2) Ingen gyldig S3-cache → hent (scrape)
    b, u, inner = fetch_prospectus_from_finn(finn_url)
    dbg.update(inner or {})
    return b, u, dbg


# ──────────────────────────────────────────────────────────────────────────────
#  Hovedløype fra FINN (prospekt only)
# ──────────────────────────────────────────────────────────────────────────────
def fetch_prospectus_from_finn(
    finn_url: str,
    *,
    save_dir: str = "data/prospekt",  # beholdt for kompatibilitet (speil)
) -> Tuple[bytes | None, str | None, dict]:
    dbg: Dict[str, Any] = {
        "step": "start",
        "finn_url": finn_url,
        "megler_url": None,
        "browser_debug": None,
        "pdf_url": None,
        "pdf_path": None,
        "content_type": None,
        "content_length": None,
        "used_return": "prospekt_s3",
    }

    out_dir = Path(save_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    finnkode = _infer_finnkode(finn_url) or "prospekt"

    def _postprocess_and_return(
        pdf_bytes: bytes, source_url: Optional[str]
    ) -> tuple[bytes, Optional[str], dict]:
        used_bytes, presigned_url, meta = _persist_to_s3_and_mirror(
            pdf_bytes=pdf_bytes, pdf_url=source_url, finnkode=finnkode
        )
        dbg.update(meta)
        return used_bytes, presigned_url, dbg

    try:
        sess: requests.Session = new_session()

        # 1) FINN → megler-URL
        megler_url, html1 = discover_megler_url(finn_url)
        dbg["megler_url"] = megler_url

        # 1b) DRIVER-FORSØK (hvis vi fant megler-URL)
        if megler_url:
            dbg.setdefault("driver_probe", [])
            for d in DRIVERS:
                try:
                    if not d.matches(megler_url):
                        continue
                except Exception:
                    continue
                dbg["driver_probe"].append(getattr(d, "name", d.__class__.__name__))
                try:
                    pdf, final_url, ddbg = d.try_fetch(sess, megler_url)
                except Exception as e:
                    dbg.setdefault("driver_errors", {})[getattr(d, "name", str(d))] = (
                        str(e)
                    )
                    continue
                if pdf:
                    dbg["step"] = "driver_ok"
                    dbg["driver_used"] = getattr(d, "name", d.__class__.__name__)
                    dbg["driver_debug"] = ddbg
                    return _postprocess_and_return(pdf, final_url or megler_url)

        # 2) Finn prospekt-PDF via FINN-siden
        soup1, html1_full = _new_bs_soup(sess, finn_url)
        pdf_url: Optional[str] = None

        cand1 = _gather_candidate_links(soup1, finn_url)
        if cand1:
            pdf_url = _resolve_first_pdf(
                sess, [(s, u) for (s, u, _t) in cand1], referer=finn_url
            )

        if not pdf_url:
            grep1 = _extract_pdf_urls_from_html(html1_full or "", base_url=finn_url)
            if grep1:
                pdf_url = _td_resolve_first_pdf_from_strs(
                    sess, [u for _s, u in grep1], referer=finn_url
                )

        # 3) Megler-side fallback
        if not pdf_url and megler_url:
            soup2, html2 = _new_bs_soup(sess, megler_url, referer=finn_url)

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

        # 4) Last ned hvis vi har URL (filtrer bort "NEG_ALWAYS")
        if pdf_url:
            if any(x in pdf_url.lower() for x in NEG_ALWAYS):
                dbg["skip_reason"] = "filtered_non_prospect_pdf"
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
                            return _postprocess_and_return(r.content, candidate)
                    except Exception:
                        continue

        # 5) Playwright fallback (klikk **salgsoppgave**, ikke TR)
        dbg["step"] = "browser_try"
        start_url = megler_url or finn_url

        # Nordvik: sørg for at vi IKKE klikker TR – vi søker salgsoppgave/prospekt
        if megler_url and "nordvikbolig.no/boliger/" in megler_url.lower():
            b2, u2, bdbg = fetch_pdf_with_browser_filtered(
                start_url,
                click_text_contains=[
                    "salgsoppgave",
                    "komplett salgsoppgave",
                    "prospekt",
                    "salgsprospekt",
                ],
                allow_only_if_url_contains=[
                    "prospekt",
                    "salgsoppgav",
                    "salgsprospekt",
                ],
                deny_if_url_contains=[
                    "tilstandsrapport",
                    "boligsalgsrapport",
                    "fidens",
                    "nabolag",
                    "nabolagsprofil",
                    "contentassets/nabolaget",
                    "energiattest",
                    "egenerkl",
                    "anticimex",
                ],
                timeout_ms=45000,
            )
        else:
            # generisk: favoriser "salgsoppgave/prospekt" tekster og deny TR/energiattest osv.
            b2, u2, bdbg = fetch_pdf_with_browser_filtered(
                start_url,
                click_text_contains=[
                    "salgsoppgave",
                    "prospekt",
                    "salgsprospekt",
                    "for utskrift",
                ],
                allow_only_if_url_contains=[
                    "prospekt",
                    "salgsoppgav",
                    "salgsprospekt",
                ],
                deny_if_url_contains=[
                    "tilstandsrapport",
                    "boligsalgsrapport",
                    "fidens",
                    "nabolag",
                    "nabolagsprofil",
                    "energiattest",
                    "egenerkl",
                    "anticimex",
                ],
                timeout_ms=45000,
            )

        dbg["browser_debug"] = bdbg
        if b2:
            dbg["step"] = "browser_ok"
            dbg["pdf_url"] = u2 or start_url
            return _postprocess_and_return(b2, u2 or start_url)

        # Feil: ingen PDF
        dbg["step"] = "failed_no_pdf"
        dbg["error"] = "no_prospect_pdf_found"
        _dump_failcase(
            finnkode,
            "no_prospect_pdf_found",
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
) -> Tuple[bytes | None, str | None, dict]:
    dbg: Dict[str, Any] = {
        "step": "start",
        "megler_url": megler_url,
        "browser_debug": None,
        "pdf_url": None,
        "pdf_path": None,
        "content_type": None,
        "content_length": None,
        "used_return": "prospekt_s3",
    }

    m = re.search(r"(\d{6,})", megler_url)
    finnkode = m.group(1) if m else "prospekt"

    def _postprocess_and_return(
        pdf_bytes: bytes, source_url: Optional[str]
    ) -> tuple[bytes, Optional[str], dict]:
        used_bytes, presigned_url, meta = _persist_to_s3_and_mirror(
            pdf_bytes=pdf_bytes, pdf_url=source_url, finnkode=finnkode
        )
        dbg.update(meta)
        return used_bytes, presigned_url, dbg

    try:
        sess: requests.Session = new_session()

        # 0) DRIVER-FORSØK først
        dbg.setdefault("driver_probe", [])
        for d in DRIVERS:
            try:
                if not d.matches(megler_url):
                    continue
            except Exception:
                continue
            dbg["driver_probe"].append(getattr(d, "name", d.__class__.__name__))
            try:
                pdf, final_url, ddbg = d.try_fetch(sess, megler_url)
            except Exception as e:
                dbg.setdefault("driver_errors", {})[getattr(d, "name", str(d))] = str(e)
                continue
            if pdf:
                dbg["step"] = "driver_ok"
                dbg["driver_used"] = getattr(d, "name", d.__class__.__name__)
                dbg["driver_debug"] = ddbg
                return _postprocess_and_return(pdf, final_url or megler_url)

        # 1) Megler-side: finn prospekt-lenker
        soup2, html2 = _new_bs_soup(sess, megler_url)

        pdf_url: Optional[str] = None
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

        if pdf_url:
            if any(x in pdf_url.lower() for x in NEG_ALWAYS):
                dbg["skip_reason"] = "filtered_non_prospect_pdf"
                pdf_url = None

        if pdf_url:
            dbg["pdf_url"] = pdf_url
            headers = {
                **BROWSER_HEADERS,
                "Accept": "application/pdf,application/octet-stream,*/*",
            }
            try:
                r = sess.get(pdf_url, headers=headers, timeout=30, allow_redirects=True)
                ct = (r.headers.get("Content-Type") or "").lower()
                if r.status_code < 400 and (
                    ("pdf" in ct) or (r.content and r.content[:4] == b"%PDF")
                ):
                    dbg["step"] = "ok"
                    dbg["content_type"] = r.headers.get("Content-Type")
                    dbg["content_length"] = r.headers.get("Content-Length")
                    return _postprocess_and_return(r.content, pdf_url)
            except Exception:
                pass

        # 2) Browser fallback (salgsoppgave, ikke TR)
        dbg["step"] = "browser_try"
        if "nordvikbolig.no/boliger/" in (megler_url or "").lower():
            b2, u2, bdbg = fetch_pdf_with_browser_filtered(
                megler_url,
                click_text_contains=["salgsoppgave", "prospekt", "salgsprospekt"],
                allow_only_if_url_contains=["prospekt", "salgsoppgav", "salgsprospekt"],
                deny_if_url_contains=[
                    "tilstandsrapport",
                    "boligsalgsrapport",
                    "fidens",
                    "nabolag",
                    "nabolagsprofil",
                    "energiattest",
                    "egenerkl",
                ],
                timeout_ms=45000,
            )
        else:
            b2, u2, bdbg = fetch_pdf_with_browser_filtered(
                megler_url,
                click_text_contains=["salgsoppgave", "prospekt", "salgsprospekt"],
                allow_only_if_url_contains=["prospekt", "salgsoppgav", "salgsprospekt"],
                deny_if_url_contains=[
                    "tilstandsrapport",
                    "boligsalgsrapport",
                    "fidens",
                    "nabolag",
                    "nabolagsprofil",
                    "energiattest",
                    "egenerkl",
                ],
                timeout_ms=45000,
            )

        dbg["browser_debug"] = bdbg
        if b2:
            dbg["step"] = "browser_ok"
            dbg["pdf_url"] = u2 or megler_url
            return _postprocess_and_return(b2, u2 or megler_url)

        dbg["step"] = "failed_no_pdf"
        dbg["error"] = "no_prospect_pdf_found"
        _dump_failcase(
            finnkode,
            "no_prospect_pdf_found",
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
#  Lagring (legacy helper – beholdt, men ikke brukt i prod)
# ──────────────────────────────────────────────────────────────────────────────
def save_pdf_locally(finnkode: str, pdf_bytes: bytes) -> str:
    os.makedirs(PROSPEKT_DIR, exist_ok=True)
    path = PROSPEKT_DIR / f"{finnkode}.pdf"
    path.write_bytes(pdf_bytes)
    return str(path)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Bruk: python -m core.fetch <FINN_URL> [--verify-remote]")
        sys.exit(1)
    url = sys.argv[1]
    verify = "--verify-remote" in sys.argv[2:]
    b, u, dbg = get_prospect_or_scrape(url, prefer_cache=True, verify_remote=verify)
    print(json.dumps(dbg, indent=2, ensure_ascii=False))
    print("bytes:", 0 if b is None else len(b))
    print("presigned_url:", u)
