# core/rent.py
from __future__ import annotations

import os
import re
import time
import math
import json
import hashlib
from dataclasses import dataclass
from typing import List, Optional, Tuple
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

# ======================================================================
# Modeller
# ======================================================================


@dataclass
class RentComp:
    source: str
    url: str
    address: Optional[str]
    price_month: int  # kr/mnd
    area_m2: Optional[float]
    rooms: Optional[int]


@dataclass
class RentSuggestion:
    suggested_rent: int  # kr/mnd (avrundet til nærmeste 100)
    low_ci: int
    high_ci: int
    n_used: int  # antall comps brukt etter filtrering
    n_raw: int  # antall comps hentet før filtrering
    note: str  # kort forklaring


# ======================================================================
# Cache (enkel filcache)
# ======================================================================

CACHE_DIR = ".cache"
os.makedirs(CACHE_DIR, exist_ok=True)


def _cache_path(key: str) -> str:
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return os.path.join(CACHE_DIR, f"{h}.json")


def cache_get(key: str, ttl_sec: int) -> Optional[dict]:
    p = _cache_path(key)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            obj = json.load(f)
        if time.time() - obj["ts"] > ttl_sec:
            return None
        return obj["data"]
    except Exception:
        return None


def cache_set(key: str, data: dict) -> None:
    p = _cache_path(key)
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "data": data}, f)
    except Exception:
        pass


# ======================================================================
# Utils
# ======================================================================

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124 Safari/537.36"
)

PRICE_RX = re.compile(r"(\d[\d\s\.]*)\s*kr", re.I)
AREA_RX = re.compile(r"(\d+(?:[\,\.]\d+)?)\s*m", re.I)
ROOM_RX = re.compile(r"(\d+)\s*rom", re.I)


def _to_int(s: str) -> Optional[int]:
    s = s.strip().replace(" ", "").replace(".", "")
    return int(s) if s.isdigit() else None


def _clean_int(txt: str) -> Optional[int]:
    m = PRICE_RX.search(txt or "")
    return _to_int(m.group(1)) if m else None


def _clean_area(txt: str) -> Optional[float]:
    m = AREA_RX.search(txt or "")
    if not m:
        return None
    return float(m.group(1).replace(",", "."))


def _clean_rooms(txt: str) -> Optional[int]:
    m = ROOM_RX.search(txt or "")
    return int(m.group(1)) if m else None


def _round100(x: float) -> int:
    return int(round(x / 100.0)) * 100


# ======================================================================
# FINN scraping
# ======================================================================


def _fetch_finn_html(
    q: str | None, page: int = 1, *, url_override: str | None = None
) -> str:
    """
    Hent rå HTML for et FINN-søk.
    - q: fritekst (brukes når url_override ikke er satt)
    - url_override: full FINN-URL (f.eks. polylocation-link)
    """
    if url_override:
        url = url_override
        if "page=" not in url:
            join = "&" if "?" in url else "?"
            url = f"{url}{join}page={page}"
    else:
        base = "https://www.finn.no/realestate/lettings/search.html"
        join = "&" if "?" in base else "?"
        qp = f"q={quote_plus(q or '')}&sort=RELEVANCE&page={page}"
        url = f"{base}{join}{qp}"

    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "nb-NO,nb;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": "https://www.finn.no/",
    }
    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    return r.text


def _parse_finn(html: str) -> List[RentComp]:
    """
    Prøv flere selektorer, velg den som gir flest treff. Logg antall per selektor.
    Dump HTML til .cache/finn_debug_*.html hvis 0 treff (for manuell inspeksjon).
    """
    soup = BeautifulSoup(html, "html.parser")
    items: List[RentComp] = []

    # Kandidat-selektorer (hold disse oppdatert når FINN endrer markup)
    selector_candidates = {
        "a[data-testid='ad-title']": soup.select("a[data-testid='ad-title']"),
        "a[data-testid='result-title']": soup.select("a[data-testid='result-title']"),
        "a[href*='/realestate/lettings/ad.html']": soup.select(
            "a[href*='/realestate/lettings/ad.html']"
        ),
        "article a[href*='/realestate/lettings/']": soup.select(
            "article a[href*='/realestate/lettings/']"
        ),
        "a[href*='/realestate/lettings/']": soup.select(
            "a[href*='/realestate/lettings/']"
        ),
    }

    # Velg beste
    best_name, best_nodes = max(selector_candidates.items(), key=lambda kv: len(kv[1]))
    try:
        print(
            "[rent] selektor-treff:",
            {k: len(v) for k, v in selector_candidates.items()},
            "best:",
            best_name,
        )
    except Exception:
        pass

    anchors = list(best_nodes)

    # Ingen treff? Dump HTML til fil for inspeksjon
    if not anchors:
        try:
            dbg = _cache_path(
                "finn_debug_" + hashlib.sha256(html.encode("utf-8")).hexdigest()[:10]
            ).replace(".json", ".html")
            with open(dbg, "w", encoding="utf-8") as f:
                f.write(html)
            print(
                f"[rent] 0 treff – dumpet søke-HTML til {dbg}. Åpne i nettleser og finn stabil selektor."
            )
        except Exception:
            pass
        return items

    seen = set()
    for a in anchors:
        href = a.get("href")
        if not href or href in seen:
            continue
        if "/realestate/lettings/" not in href:
            continue
        seen.add(href)
        url = href if href.startswith("http") else ("https://www.finn.no" + href)

        # Finn "kortet" rundt lenken
        card = a
        for _ in range(5):
            if card and card.parent and card.parent.name not in ("html", "body"):
                card = card.parent
            else:
                break

        # Prisnoder + fallback på hele kortet
        cand_nodes = []
        cand_nodes += card.select('[data-testid="price"]') or []
        cand_nodes += card.select(".ads__unit__price, .u-t3, .u-t2, .u-t4") or []

        card_txt = (
            card.get_text(" ", strip=True) if card else a.get_text(" ", strip=True)
        ) or ""

        kr_vals = []
        for n in cand_nodes:
            m = PRICE_RX.search(n.get_text(" ", strip=True))
            if m:
                v = _to_int(m.group(1))
                if v:
                    kr_vals.append((v, n.get_text(" ", strip=True)))

        for m in PRICE_RX.finditer(card_txt):
            v = _to_int(m.group(1))
            if v:
                kr_vals.append((v, card_txt))

        price_val = None
        # helst en pristekst som inneholder mnd/måned
        for v, ctx in kr_vals:
            if re.search(r"\b(mnd|måned)\b", ctx, re.I):
                price_val = v
                break
        # ellers maks fornuftig tall
        if price_val is None:
            big = [v for v, _ in kr_vals if v >= 3000]
            if big:
                price_val = max(big)

        if not price_val:
            continue

        area = _clean_area(card_txt)
        rooms = _clean_rooms(card_txt)

        items.append(
            RentComp(
                source="FINN",
                url=url,
                address=None,
                price_month=int(price_val),
                area_m2=area,
                rooms=rooms,
            )
        )

    return items


def fetch_finn_comps(query: str, max_pages: int = 3) -> List[RentComp]:
    """Hent comps fra FINN med fritekst-søk."""
    cache_key = f"finn:q:{query}:{max_pages}"
    c = cache_get(cache_key, ttl_sec=6 * 3600)
    if c is not None:
        return [RentComp(**d) for d in c]

    all_items: List[RentComp] = []
    print(f"[rent] Starter leie-comps… (q='{query}')")
    for p in range(1, max_pages + 1):
        try:
            html = _fetch_finn_html(query, page=p)
            items = _parse_finn(html)
            print(f"[rent] side {p}: {len(items)} treff")
            if not items:
                # stopp tidlig hvis neste sider sannsynlig er tomme
                if p == 1:
                    # men dumpet HTML allerede i parser ved 0
                    pass
                break
            all_items.extend(items)
        except Exception as e:
            print("[rent] fetch/parsing-feil:", e)
            continue

    # dedup på URL
    seen = set()
    dedup: List[RentComp] = []
    for it in all_items:
        if it.url in seen:
            continue
        seen.add(it.url)
        dedup.append(it)

    cache_set(cache_key, [it.__dict__ for it in dedup])
    return dedup


def fetch_finn_comps_from_url(url: str) -> List[RentComp]:
    """Hent comps fra en ferdig FINN-URL (f.eks. polylocation-søk)."""
    cache_key = f"finn:url:{hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]}"
    c = cache_get(cache_key, ttl_sec=6 * 3600)
    if c is not None:
        return [RentComp(**d) for d in c]

    all_items: List[RentComp] = []
    print("[rent] Bruker full FINN-URL:", url)
    # vi henter 1 side – som regel holder det ved stramme polygoner
    try:
        html = _fetch_finn_html(None, page=1, url_override=url)
        items = _parse_finn(html)
        print(f"[rent] URL ⇒ {len(items)} treff")
        all_items.extend(items)
    except Exception as e:
        print("[rent] fetch/parsing-feil (url):", e)

    # dedup
    seen = set()
    dedup: List[RentComp] = []
    for it in all_items:
        if it.url in seen:
            continue
        seen.add(it.url)
        dedup.append(it)

    cache_set(cache_key, [it.__dict__ for it in dedup])
    return dedup


# ======================================================================
# Estimator
# ======================================================================


def _iqr_bounds(vals: List[float]) -> Tuple[float, float]:
    if not vals:
        return (float("-inf"), float("inf"))
    qs = sorted(vals)
    n = len(qs)
    q1 = qs[int(0.25 * (n - 1))]
    q3 = qs[int(0.75 * (n - 1))]
    iqr = q3 - q1
    return (q1 - 1.5 * iqr, q3 + 1.5 * iqr)


def _median(xs: List[float]) -> float:
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else 0.5 * (s[mid - 1] + s[mid])


def _mad(xs: List[float], m: Optional[float] = None) -> float:
    if not xs:
        return 0.0
    if m is None:
        m = _median(xs)
    dev = [abs(x - m) for x in xs]
    return _median(dev)


def suggest_rent_from_comps(
    comps: List[RentComp],
    target_area_m2: Optional[float],
    target_rooms: Optional[int],
) -> Optional[RentSuggestion]:
    """Filtrer comps og foreslå brutto leie (kr/mnd) + CI."""
    if not comps:
        return None

    n_raw = len(comps)

    comps = [c for c in comps if c.price_month]
    if target_area_m2:
        comps = [
            c
            for c in comps
            if c.area_m2 and 0.8 * target_area_m2 <= c.area_m2 <= 1.2 * target_area_m2
        ]
    if target_rooms:
        comps = [c for c in comps if c.rooms and abs(c.rooms - target_rooms) <= 1]

    if not comps:
        return None

    per_m2: List[float] = []
    total: List[float] = []
    for c in comps:
        total.append(float(c.price_month))
        if c.area_m2 and c.area_m2 > 5:
            per_m2.append(c.price_month / c.area_m2)

    use_per_m2 = len(per_m2) >= max(5, math.ceil(0.3 * len(comps)))
    series = per_m2 if use_per_m2 else total

    lo, hi = _iqr_bounds(series)
    series_cut = [x for x in series if lo <= x <= hi]
    if len(series_cut) >= 5:
        series = series_cut

    med = _median(series)
    mad = _mad(series, med)
    n = max(1, len(series))
    se = 1.253 * (mad if mad else 1.0) / math.sqrt(n)
    low = med - 1.96 * se
    high = med + 1.96 * se

    if use_per_m2 and target_area_m2:
        med *= target_area_m2
        low *= target_area_m2
        high *= target_area_m2

    suggested = _round100(med)
    low_ci = max(0, _round100(low))
    high_ci = _round100(high)

    note = "Median-basert estimat fra FINN-comps (IQR-outlierkutt"
    note += ", per m²)" if use_per_m2 else ", totalpris)"
    return RentSuggestion(suggested, low_ci, high_ci, n_used=n, n_raw=n_raw, note=note)


# ======================================================================
# Public API
# ======================================================================


def get_rent_suggestion(
    address: Optional[str],
    areal_m2: Optional[float],
    rom: Optional[int],
    type: Optional[
        str
    ] = None,  # ikke brukt i denne versjonen, men beholdt for signaturen
    query_override: Optional[str] = None,
    url_override: Optional[str] = None,  # NY: hvis du vil tvinge en ferdig FINN-URL
) -> Optional[RentSuggestion]:
    """
    Hent comp-søk + beregn forslag.
    - address: fritekst (gate/by/bydel). Brukes hvis url_override ikke er satt.
    - areal_m2 / rom: brukes til filtrering og ved per-m²-estimat.
    - query_override: tving fritekst
    - url_override: tving ferdig FINN-søke-URL (polylocation m.m.)
    """
    if url_override:
        comps = fetch_finn_comps_from_url(url_override)
    else:
        q = (query_override or address or "").strip()
        if not q:
            return None
        comps = fetch_finn_comps(q, max_pages=3)

    return suggest_rent_from_comps(comps, areal_m2, rom)
