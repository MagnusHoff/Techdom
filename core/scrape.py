# core/scrape.py
from __future__ import annotations

import re
import json
from typing import Dict, Optional

import requests
from bs4 import BeautifulSoup

# ---------- Area/rooms parsing helpers ----------

_M2_RX = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:m²|m2|m\^2|kvm|kvadrat)", re.IGNORECASE)


def _to_float(x: str) -> Optional[float]:
    if not x:
        return None
    try:
        return float(str(x).strip().replace(" ", "").replace(",", "."))
    except Exception:
        return None


def _parse_m2_from_text(txt: str) -> Optional[float]:
    """Finn første m²-tall i en tekstbit ('48 m²', '48m2', '48 kvm')."""
    if not txt:
        return None
    m = _M2_RX.search(txt)
    return _to_float(m.group(1)) if m else None


def _norm(s: str) -> str:
    return (s or "").lower().strip()


def _get_first(attrs: Dict[str, str], keys: list[str]) -> Optional[float]:
    """Se etter første match i attrs (nøkkel~feltet etter informasjonstabellen)."""
    for want in keys:
        for k, v in attrs.items():
            if _norm(want) in _norm(k):
                val = _parse_m2_from_text(v)
                if val:
                    return val
    return None


def choose_area_m2(attrs: Dict[str, str], page_text: str) -> Optional[float]:
    """
    Velg beste areal fra attrs + fallback i hele sideteksten.
    Prioritet: BRA > P-rom > Boligareal/Areal.
    """
    # 1) tabell-feltene (mest presist)
    bra_keys = ["bruksareal", "bra"]
    prom_keys = ["primærrom", "p-rom", "prom", "p rom"]
    area_keys = ["boligareal", "areal"]

    # a) BRA
    v = _get_first(attrs, bra_keys)
    if v:
        return v
    # b) P-rom
    v = _get_first(attrs, prom_keys)
    if v:
        return v
    # c) Boligareal/Areal
    v = _get_first(attrs, area_keys)
    if v:
        return v

    # 2) Fallback: skann hele siden rundt nøkkelord → m²
    text = page_text or ""
    for kw in bra_keys + prom_keys + area_keys:
        rx = re.compile(
            rf"{kw}[^0-9]{{0,40}}(\d+(?:[.,]\d+)?)\s*(?:m²|m2|m\^2|kvm)",
            re.IGNORECASE,
        )
        m = rx.search(text)
        if m:
            return _to_float(m.group(1))

    # 3) Siste utvei: hvilket som helst m²-tall i siden (kan være upresist)
    any_m2 = _parse_m2_from_text(text)
    return any_m2


def choose_rooms(attrs: Dict[str, str], page_text: str) -> Optional[int]:
    """
    Finn 'rom' (best-effort). På FINN er 'Soverom' ofte oppgitt – det kan være
    nyttigere enn 'Rom'. Vi forsøker begge.
    """
    # fra tabellen
    for want in ["soverom", "antall soverom", "rom", "antall rom"]:
        for k, v in attrs.items():
            if _norm(want) in _norm(k):
                m = re.search(r"(\d+)", str(v))
                if m:
                    return int(m.group(1))

    # fallback: hele sideteksten
    m = re.search(r"(?:soverom|rom)\D{0,10}(\d+)", page_text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


# ---------- Hjelpere ----------


def _num(s):
    """Rydd tall: '3 500 000 kr' -> 3500000"""
    if s is None:
        return None
    t = re.sub(r"[^0-9,\.]", "", str(s)).replace(".", "").replace(",", ".")
    try:
        return int(round(float(t)))
    except Exception:
        return None


def fetch_html(url: str) -> str:
    """Hent HTML med realistiske headers (mindre sjanse for blokkering)."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "nb-NO,nb;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    r = requests.get(url, headers=headers, timeout=15)
    r.raise_for_status()
    return r.text


def _address_from_jsonld(item) -> Optional[str]:
    """
    Plukk adressefelt fra JSON-LD hvis det finnes.
    Returnerer 'Gate 1, 5000 Bergen' eller None.
    """
    addr = item.get("address") or {}
    if isinstance(addr, list) and addr:
        addr = addr[0]
    street = (addr.get("streetAddress") or "").strip()
    locality = (addr.get("addressLocality") or "").strip()
    postal = (addr.get("postalCode") or "").strip()
    if street and postal and locality:
        return f"{street}, {postal} {locality}"
    if street or locality:
        return (street or locality) or None
    return None


def _clean_address(s: str) -> str:
    """Fjern 'Kart ' foran + trailing etiketter (Totalpris/Prisantydning)."""
    s = re.sub(r"^\s*Kart\s+", "", s).strip()
    s = re.sub(r"\s+(Prisantydning|Totalpris)\s*$", "", s, flags=re.I).strip()
    return s


def _kv(txt: str) -> Optional[tuple[str, str]]:
    """Gjetter på key/value i en tekst: 'Bruksareal (BRA) 48 m²' -> ('Bruksareal (BRA)', '48 m²')"""
    if not txt:
        return None
    # del på to+ mellomrom el.l.
    m = re.match(r"\s*([A-Za-zÆØÅæøå0-9()\-\/\. ]{3,}?)\s{2,}(.+)\s*$", txt)
    if m:
        return (m.group(1).strip(), m.group(2).strip())
    # kolon-separert
    m = re.match(r"\s*([^:]{3,}):\s*(.+)\s*$", txt)
    if m:
        return (m.group(1).strip(), m.group(2).strip())
    return None


def _collect_attrs(soup: BeautifulSoup) -> Dict[str, str]:
    """
    Prøv flere FINN-varianter for fakta-/nøkkelinfo-tabell og bygg et dict {label: verdi}.
    Vi leter i dl/dt/dd, i tabeller og i 'chips'-lister.
    """
    attrs: Dict[str, str] = {}

    # 1) Klassisk <dl><dt>Label</dt><dd>Verdi</dd>
    for dl in soup.find_all("dl"):
        dts = dl.find_all("dt")
        dds = dl.find_all("dd")
        if dts and dds and len(dts) == len(dds):
            for dt, dd in zip(dts, dds):
                k = (dt.get_text(" ", strip=True) or "").strip()
                v = (dd.get_text(" ", strip=True) or "").strip()
                if k and v and k not in attrs:
                    attrs[k] = v

    # 2) Tabell <table><tr><th/td>...
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            tds = tr.find_all(["th", "td"])
            if len(tds) >= 2:
                k = (tds[0].get_text(" ", strip=True) or "").strip()
                v = (tds[1].get_text(" ", strip=True) or "").strip()
                if k and v and k not in attrs:
                    attrs[k] = v

    # 3) Chips-lister / key-value i div/span – prøv å gjette
    for container in soup.select(
        "[data-testid*='object-facts'], [data-testid*='facts'], [class*='fact'], [class*='key'], [class*='info']"
    ):
        for el in container.find_all(["li", "div", "span"]):
            txt = el.get_text(" ", strip=True)
            kv = _kv(txt)
            if kv:
                k, v = kv
                if k and v and k not in attrs:
                    attrs[k] = v

    return attrs


# ---------- Hovedfunksjon ----------


def scrape_finn(url: str) -> Dict[str, object]:
    """
    Skraper en FINN-boligannonse og returnerer et dict med bl.a.:
      - image, address, total_price, hoa_month
      - lat, lon (for GeoJSON-bucket)
      - area_m2, rooms (best-effort)
    """
    out: Dict[str, object] = {"source_url": url}

    try:
        html = fetch_html(url)
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True)

        # -----------------------------
        # BILDE: og:image -> twitter:image -> JSON-LD -> galleri <img>
        # -----------------------------
        try:
            img = None
            og = soup.find("meta", property="og:image")
            if og and og.get("content"):
                img = og["content"]

            if not img:
                tw = soup.find("meta", attrs={"name": "twitter:image"})
                if tw and tw.get("content"):
                    img = tw["content"]

            if not img:
                for tag in soup.find_all("script", type="application/ld+json"):
                    try:
                        blob = json.loads(tag.string or "{}")
                    except Exception:
                        continue
                    items = blob if isinstance(blob, list) else [blob]
                    for item in items:
                        if isinstance(item.get("image"), str) and not img:
                            img = item["image"]
                        elif (
                            isinstance(item.get("image"), list)
                            and item["image"]
                            and not img
                        ):
                            img = item["image"][0]
                        if img:
                            break
                    if img:
                        break

            if not img:
                gimg = soup.select_one(
                    "img[data-testid='gallery-image'], img[src*='images']"
                )
                if gimg and gimg.get("src"):
                    img = gimg["src"]

            if img:
                out["image"] = img
        except Exception:
            pass

        # -----------------------------
        # ADRESSE + PRIS: DOM -> JSON-LD -> regex
        # -----------------------------
        found_addr = None
        found_price = None

        # 1) DOM-adresse (blå lenke)
        try:
            addr_tag = soup.select_one('[data-testid="object-address"]')
            if addr_tag:
                cand = _clean_address(addr_tag.get_text(strip=True))
                if any(ch.isdigit() for ch in cand) and len(cand) <= 80:
                    found_addr = cand
        except Exception:
            pass

        # 2) JSON-LD: adresse, pris, geo.lat/lon
        try:
            lat_lon_set = False
            for tag in soup.find_all("script", type="application/ld+json"):
                try:
                    blob = json.loads(tag.string or "{}")
                except Exception:
                    continue
                items = blob if isinstance(blob, list) else [blob]
                for item in items:
                    # adresse
                    if not found_addr:
                        a = _address_from_jsonld(item)
                        if a:
                            cand = _clean_address(a)
                            if any(ch.isdigit() for ch in cand) and len(cand) <= 80:
                                found_addr = cand

                    # pris
                    if not found_price:
                        offers = item.get("offers") or {}
                        if isinstance(offers, list) and offers:
                            offers = offers[0]
                        price = offers.get("price") or (
                            offers.get("priceSpecification") or {}
                        ).get("price")
                        if price:
                            n = _num(price)
                            if n:
                                found_price = n

                    # geo (lat/lon)
                    if not lat_lon_set:
                        geo = item.get("geo") or {}
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

        # 3) Regex fallback for adresse (husnr før komma)
        if "address" not in out:
            try:
                m = re.search(
                    r"(?:Kart\s+)?((?=[^,]*\d)[A-Za-zÆØÅæøå0-9\.\- ]+),\s*(\d{4})\s+([A-Za-zÆØÅæøå\-\s]+)",
                    text,
                )
                if m:
                    cand = _clean_address(
                        f"{m.group(1).strip()}, {m.group(2).strip()} {m.group(3).strip()}"
                    )
                    if any(ch.isdigit() for ch in cand) and len(cand) <= 80:
                        out["address"] = cand
            except Exception:
                pass

        # -----------------------------
        # LAT/LON fallback via meta
        # -----------------------------
        if "lat" not in out or "lon" not in out:
            try:
                meta_lat = soup.find(
                    "meta", attrs={"property": "place:location:latitude"}
                )
                meta_lon = soup.find(
                    "meta", attrs={"property": "place:location:longitude"}
                )
                if (
                    meta_lat
                    and meta_lon
                    and meta_lat.get("content")
                    and meta_lon.get("content")
                ):
                    out["lat"] = float(str(meta_lat["content"]).replace(",", "."))
                    out["lon"] = float(str(meta_lon["content"]).replace(",", "."))
            except Exception:
                pass

        # -----------------------------
        # TOTALPRIS / PRISANTYDNING (regex fallback)
        # -----------------------------
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

        # -----------------------------
        # FELLESKOST/MND
        # -----------------------------
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

        # -----------------------------
        # AREA/ROOMS (best-effort via attrs + tekst)
        # -----------------------------
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
        # Ikke krasj – returner det vi evt. har klart å hente
        pass

    return out
