# core/ssb.py
from __future__ import annotations
import csv, os
from functools import lru_cache
from typing import Optional

# --- Lokalt snapshot (anbefalt primærkilde for stabilitet) ---
_SNAPSHOT = "data/rent_m2.csv"  # columns: city,bucket,segment,kr_per_m2,updated

# Normalisering av bynavn -> snapshot-navn
_CITY_ALIASES = {
    "oslo": "Oslo",
    "bergen": "Bergen",
    "trondheim": "Trondheim",
    "stavanger": "Stavanger",
    "tromso": "Tromsø",
    "tromsø": "Tromsø",
    "kristiansand": "Kristiansand",
    "drammen": "Drammen",
    "fredrikstad": "Fredrikstad",
    "porsgrunn": "Porsgrunn",
    "skien": "Skien",
    "sarpsborg": "Sarpsborg",
    "sandnes": "Sandnes",
    "alesund": "Ålesund",
    "ålesund": "Ålesund",
    "haugesund": "Haugesund",
    # Aggregerte nivåer
    "hele landet": "Hele landet",
    "store tettsteder": "Store tettsteder",
    "mellomstore tettsteder": "Mellomstore tettsteder",
    "små tettsteder/spredt": "Små tettsteder/spredt",
}

_SEG_ALIASES = {
    "hybel": "hybel",
    "liten": "liten",
    "standard": "standard",
    "stor": "stor",
}


@lru_cache(maxsize=1)
def _load_snapshot():
    data = {}
    if not os.path.exists(_SNAPSHOT):
        return data
    with open(_SNAPSHOT, "r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            city = (row.get("city") or "").strip()
            bucket = (row.get("bucket") or "").strip()
            seg = (row.get("segment") or "").strip().lower()
            kr = row.get("kr_per_m2")
            if not city or not seg or not kr:
                continue

            # Bruk kun by-snitt (bucket == "<City> snitt") eller reine by-rader
            take = False
            if bucket and bucket.lower() == f"{city.lower()} snitt":
                take = True
            if not bucket:  # tillat rader uten bucket for by-snitt
                take = True

            if take:
                try:
                    v = float(str(kr).replace(",", "."))
                    data.setdefault(city, {})[seg] = v
                except Exception:
                    pass
    return data


def _alias_city(name: str) -> str:
    return _CITY_ALIASES.get((name or "").strip().lower(), (name or "").strip())


def _alias_seg(seg: str) -> str:
    return _SEG_ALIASES.get((seg or "").strip().lower(), (seg or "").strip().lower())


def get_city_m2_month(
    city_name: str, segment: str, year: Optional[int] = None
) -> Optional[float]:
    """
    Returnerer kr/m² per MND for (city_name, segment).
    1) Prøv lokalt snapshot (stabilt/ras kjapt).
    2) Hvis ikke funn, prøv SSB live (best effort) med Soner2-koder.
    """
    if not city_name or not segment:
        return None

    city_key = _alias_city(city_name)
    seg_key = _alias_seg(segment)

    snap = _load_snapshot()
    if city_key in snap and seg_key in snap[city_key]:
        return float(snap[city_key][seg_key])

    # --- Best effort live-kall (valgfritt) ---
    try:
        import requests  # lokal import så modul ikke feiler uten requests

        TABLE = "09895"
        DATA_URL = f"https://data.ssb.no/api/v0/no/table/{TABLE}"

        # Soner2-koder (stabile koder er tryggere enn fritekst)
        SONER2 = {
            "Hele landet": "00",
            "Oslo": "01",
            "Bergen": "03",
            "Trondheim": "04",
            "Stavanger": "05",
            "Store tettsteder": "20",
            "Mellomstore tettsteder": "21",
            "Små tettsteder/spredt": "22",
        }

        # AntRom-koder
        ANTROM = {"hybel": "1", "liten": "2", "standard": "3", "stor": "4"}

        soner2_code = SONER2.get(city_key)
        if not soner2_code:
            # mangler spesifikk by -> prøv nivåer i gradert rekkefølge
            for fallback in [
                "Hele landet",
                "Store tettsteder",
                "Mellomstore tettsteder",
                "Små tettsteder/spredt",
            ]:
                if fallback in SONER2:
                    soner2_code = SONER2[fallback]
                    city_key = fallback
                    break

        antrom_code = ANTROM.get(seg_key, "3")
        y = str(year or 2024)

        payload = {
            "query": [
                {
                    "code": "Soner2",
                    "selection": {"filter": "item", "values": [soner2_code]},
                },
                {
                    "code": "AntRom",
                    "selection": {"filter": "item", "values": [antrom_code]},
                },
                # Innholds-koden i 09895 er årlig kr/m². Navnet kan variere; 'Husleiear' / lignende.
                # Mange PxWeb-tabeller har standardinnhold som eneste gyldige verdi, så vi spør på Tid og henter 'value'[0].
                {"code": "Tid", "selection": {"filter": "item", "values": [y]}},
            ],
            "response": {"format": "json-stat2"},
        }

        r = requests.post(DATA_URL, json=payload, timeout=20)
        r.raise_for_status()
        js = r.json()
        vals = js.get("value")
        if not vals:
            return None
        annual = float(vals[0])  # kr/m² per ÅR
        return annual / 12.0
    except Exception:
        return None
