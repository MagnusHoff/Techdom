# core/scrape.py
import re
import json
import requests
from bs4 import BeautifulSoup


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
    """
    Henter HTML med realistiske headers (mindre sjanse for robot-blokkering).
    """
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


def _address_from_jsonld(item) -> str | None:
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
    """
    Fjern 'Kart ' foran + trailing etiketter som kan ha blitt limt på (Totalpris/Prisantydning).
    """
    s = re.sub(r"^\s*Kart\s+", "", s).strip()
    s = re.sub(r"\s+(Prisantydning|Totalpris)\s*$", "", s, flags=re.I).strip()
    return s


# ---------- Hovedfunksjon ----------


def scrape_finn(url: str) -> dict:
    """
    Returnerer et dict med felter:
      {
        "source_url": url,
        "image": str | None,
        "address": str | None,    # 'Gate 1, 5000 Bergen'
        "total_price": int | None,
        "hoa_month": int | None,
      }
    Enkel, robust scraping uten JS-avhengigheter.
    """
    out = {"source_url": url}

    try:
        html = fetch_html(url)
        soup = BeautifulSoup(html, "html.parser")

        # Tekstversjon (til regex-fallbacks)
        text = soup.get_text(" ", strip=True)

        # -----------------------------
        # BILDE: og:image -> twitter:image -> JSON-LD -> galleri <img>
        # -----------------------------
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

        # -----------------------------
        # ADRESSE: DOM -> JSON-LD -> regex
        # -----------------------------
        addr = None

        # 1) Blå adresselenke
        try:
            addr_tag = soup.select_one('[data-testid="object-address"]')
            if addr_tag:
                cand = _clean_address(addr_tag.get_text(strip=True))
                # enkel sanity: må ha siffer (husnr) og ikke være for lang
                if any(ch.isdigit() for ch in cand) and len(cand) <= 80:
                    addr = cand
        except Exception:
            pass

        # 2) JSON-LD
        found_addr = None
        found_price = None
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                blob = json.loads(tag.string or "{}")
            except Exception:
                continue
            items = blob if isinstance(blob, list) else [blob]
            for item in items:
                if not found_addr:
                    a = _address_from_jsonld(item)
                    if a:
                        cand = _clean_address(a)
                        if any(ch.isdigit() for ch in cand) and len(cand) <= 80:
                            found_addr = cand

                # pris (kan ligge i offers/price eller offers/priceSpecification/price)
                offers = item.get("offers") or {}
                if isinstance(offers, list) and offers:
                    offers = offers[0]
                price = offers.get("price") or (
                    offers.get("priceSpecification") or {}
                ).get("price")
                if price and not found_price:
                    n = _num(price)
                    if n:
                        found_price = n

        if not addr and found_addr:
            addr = found_addr

        if addr:
            out["address"] = addr
        if found_price:
            out["total_price"] = found_price

        # 3) Regex-fallback for adresse (krever husnr før komma)
        if "address" not in out:
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

        # -----------------------------
        # TOTALPRIS / PRISANTYDNING (regex fallback)
        # -----------------------------
        if "total_price" not in out:
            m = re.search(
                r"(Totalpris)\s*[:\s]\s*([0-9\s\.\u00A0]+)kr?", text, flags=re.I
            )
            if m:
                out["total_price"] = _num(m.group(2))
        if "total_price" not in out:
            m = re.search(
                r"(Prisantydning)\s*[:\s]\s*([0-9\s\.\u00A0]+)kr?", text, flags=re.I
            )
            if m:
                out["total_price"] = _num(m.group(2))

        # -----------------------------
        # FELLESKOST/MND (flere varianter)
        # -----------------------------
        if "hoa_month" not in out:
            m = re.search(
                r"(Felleskostnader|Felleskost/mnd\.?|Fellesutgifter)\s*[:\s]\s*([0-9\s\.\u00A0]+)kr?",
                text,
                flags=re.I,
            )
            if m:
                out["hoa_month"] = _num(m.group(2))

    except Exception:
        # Ikke krasj – returner det vi evt. klarte å hente
        pass

    return out
