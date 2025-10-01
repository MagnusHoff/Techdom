# core/scrape.py
from __future__ import annotations

import io
import json
import re
from typing import Dict, Optional, List, Tuple, Any, cast
from urllib.parse import urljoin, urlparse, parse_qs, urlunparse

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag

from PyPDF2 import PdfReader, PdfWriter  # fallback + trimming

from techdom.ingestion.http_headers import BROWSER_HEADERS
from techdom.ingestion.sessions import new_session  # <-- felles session-oppsett


# ──────────────────────────────────────────────────────────────────────────────
#  Requests / HTML (delt session)
# ──────────────────────────────────────────────────────────────────────────────
def fetch_html(
    url: str, *, sess: requests.Session | None = None, timeout: int = 15
) -> str:
    """
    Hent HTML med felles new_session()-oppsett for stabil UA/proxy/cookies.
    """
    s = sess or new_session()
    r = s.get(url, headers=BROWSER_HEADERS, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    return r.text


# ──────────────────────────────────────────────────────────────────────────────
#  Små helpers
# ──────────────────────────────────────────────────────────────────────────────
def _attr_to_str(val: Any) -> str | None:
    """BeautifulSoup-attributter kan være lister. Normaliser til str|None."""
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


def _to_float(x: str | float | int | None) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(str(x).strip().replace(" ", "").replace(",", "."))
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

    writer = PdfWriter()
    out_pages = 0  # robust teller (noen PyPDF2-versjoner har ikke writer.pages)
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
#  Scrape hovedinfo fra FINN (bilde, adresse, pris, areal, rom, geo)
# ──────────────────────────────────────────────────────────────────────────────
def _address_from_jsonld(item: Any) -> Optional[str]:
    if not isinstance(item, dict):
        return None
    addr: Any = item.get("address") or {}
    if isinstance(addr, list) and addr:
        addr = addr[0]
    if not isinstance(addr, dict):
        return None
    street = (addr.get("streetAddress") or "").strip()
    locality = (addr.get("addressLocality") or "").strip()
    postal = (addr.get("postalCode") or "").strip()
    if street and postal and locality:
        return f"{street}, {postal} {locality}"
    if street or locality:
        return (street or locality) or None
    return None


def _clean_address(s: str) -> str:
    s = re.sub(r"^\s*Kart\s+", "", s).strip()
    s = re.sub(r"\s+(Prisantydning|Totalpris)\s*$", "", s, flags=re.I).strip()
    return s


def _num(s: Any) -> Optional[int]:
    if s is None:
        return None
    t = re.sub(r"[^0-9,\.]", "", str(s)).replace(".", "").replace(",", ".")
    try:
        return int(round(float(t)))
    except Exception:
        return None


def scrape_finn(url: str) -> Dict[str, object]:
    """
    Skraper nøkkelinformasjon fra FINN-objektside: bilde, adresse, totalpris,
    evt. felleskost, areal (BRA/P-rom/boligareal), antall rom og lat/lon.
    """
    out: Dict[str, object] = {"source_url": url}
    try:
        sess = new_session()
        html_text = fetch_html(url, sess=sess)
        soup = BeautifulSoup(html_text, "html.parser")
        text = soup.get_text(" ", strip=True)

        # bilde
        try:
            img: Optional[str] = None
            og = soup.find("meta", property="og:image")
            if isinstance(og, Tag):
                cand = og.get("content")
                if isinstance(cand, str) and cand:
                    img = cand
            if not img:
                tw = soup.find("meta", attrs={"name": "twitter:image"})
                if isinstance(tw, Tag):
                    cand = tw.get("content")
                    if isinstance(cand, str) and cand:
                        img = cand
            if not img:
                for tag in soup.find_all("script", type="application/ld+json"):
                    if not isinstance(tag, Tag):
                        continue
                    try:
                        blob = json.loads(tag.string or "{}")
                    except Exception:
                        continue
                    items = blob if isinstance(blob, list) else [blob]
                    for item in items:
                        if isinstance(item, dict):
                            if isinstance(item.get("image"), str) and not img:
                                img = cast(str, item["image"])
                            elif isinstance(item.get("image"), list) and item["image"]:
                                if isinstance(item["image"][0], str):
                                    img = item["image"][0]
                        if img:
                            break
                    if img:
                        break
            if not img and hasattr(soup, "select_one"):
                gimg = soup.select_one(
                    "img[data-testid='gallery-image'], img[src*='images']"
                )
                if isinstance(gimg, Tag):
                    src = gimg.get("src")
                    if isinstance(src, str) and src:
                        img = src
            if img:
                out["image"] = img
        except Exception:
            pass

        # adresse & pris (+ geo)
        found_addr: Optional[str] = None
        found_price: Optional[int] = None

        try:
            addr_tag = soup.select_one('[data-testid="object-address"]')
            if isinstance(addr_tag, Tag):
                cand = _clean_address(addr_tag.get_text(strip=True))
                if any(ch.isdigit() for ch in cand) and len(cand) <= 80:
                    found_addr = cand
        except Exception:
            pass

        try:
            lat_lon_set = False
            for tag in soup.find_all("script", type="application/ld+json"):
                if not isinstance(tag, Tag):
                    continue
                try:
                    blob = json.loads(tag.string or "{}")
                except Exception:
                    continue
                items: List[Any] = blob if isinstance(blob, list) else [blob]
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    if not found_addr:
                        a = _address_from_jsonld(item)
                        if a:
                            cand = _clean_address(a)
                            if any(ch.isdigit() for ch in cand) and len(cand) <= 80:
                                found_addr = cand

                    if not found_price:
                        offers: Any = item.get("offers") or {}
                        if isinstance(offers, list) and offers:
                            offers = offers[0]
                        if isinstance(offers, dict):
                            price = offers.get("price") or (
                                (offers.get("priceSpecification") or {})
                                if isinstance(offers.get("priceSpecification"), dict)
                                else {}
                            )
                            if isinstance(price, dict):
                                price = price.get("price")
                            if price is not None:
                                n = _num(price)
                                if n:
                                    found_price = n

                    if not lat_lon_set:
                        geo: Any = item.get("geo") or {}
                        if isinstance(geo, dict):
                            lat = geo.get("latitude")
                            lon = geo.get("longitude")
                            if lat is not None and lon is not None:
                                try:
                                    out["lat"] = float(str(lat).replace(",", "."))
                                    out["lon"] = float(str(lon).replace(",", "."))
                                    lat_lon_set = True
                                except Exception:
                                    pass
        except Exception:
            pass

        if found_addr:
            out["address"] = found_addr
        if found_price:
            out["total_price"] = found_price

        if "total_price" not in out:
            try:
                m = re.search(
                    r"(Totalpris)\s*[:\s]\s*([0-9\s\.\u00A0]+)kr?", text, flags=re.I
                )
                if m:
                    out["total_price"] = _num(m.group(2))
            except Exception:
                pass
        if "total_price" not in out:
            try:
                m = re.search(
                    r"(Prisantydning)\s*[:\s]\s*([0-9\s\.\u00A0]+)kr?", text, flags=re.I
                )
                if m:
                    out["total_price"] = _num(m.group(2))
            except Exception:
                pass

        if "hoa_month" not in out:
            try:
                m = re.search(
                    r"(Felleskostnader|Felleskost/mnd\.?|Fellesutgifter)\s*[:\s]\s*([0-9\s\.\u00A0]+)kr?",
                    text,
                    flags=re.I,
                )
                if m:
                    out["hoa_month"] = _num(m.group(2))
            except Exception:
                pass

        try:
            attrs = _collect_attrs(soup)
        except Exception:
            attrs = {}
        try:
            a = choose_area_m2(attrs, text)
            if a is not None:
                out["area_m2"] = float(a)
        except Exception:
            pass
        try:
            r = choose_rooms(attrs, text)
            if r is not None:
                out["rooms"] = int(r)
        except Exception:
            pass

    except Exception:
        pass

    return out
