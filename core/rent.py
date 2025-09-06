# core/rent.py
from __future__ import annotations
import os
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.geo import find_bucket_from_point

# -------------------------------
# Konstanter / konfig
# -------------------------------

CSV_PATH = Path("data/rent_m2.csv")
DEFAULT_CITY = "Bergen"
ROUND_TO = 100  # rund leie til nærmeste hundrelapp

# Rom-justering (svak). Sett til 0.0 hvis du ikke vil bruke den.
ROOM_ADJ_PER_ROOM = 0.00
MAX_ROOM_ADJ = 0.10

# -------------------------------
# Datamodell
# -------------------------------


@dataclass
class RentEstimate:
    gross_rent: int
    kr_per_m2: float
    bucket: str
    city: str
    confidence: float
    note: str
    updated: str


# -------------------------------
# Internt cache for CSV
# -------------------------------

# city -> {bucket -> (kr_per_m2, updated)}
_table_cache: Dict[str, Dict[str, Tuple[float, str]]] = {}
_table_mtime: Optional[float] = None


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def _load_table(force: bool = False) -> Dict[str, Dict[str, Tuple[float, str]]]:
    """
    Laster (og cacher) rent_m2.csv til struktur:
      {city: {bucket: (kr_per_m2, updated)}}
    """
    global _table_cache, _table_mtime

    if not CSV_PATH.exists():
        _table_cache = {}
        _table_mtime = None
        return {}

    mtime = CSV_PATH.stat().st_mtime
    if not force and _table_mtime == mtime and _table_cache:
        return _table_cache

    table: Dict[str, Dict[str, Tuple[float, str]]] = {}
    with CSV_PATH.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            city = (row.get("city") or "").strip()
            bucket = (row.get("bucket") or "").strip()
            if not city or not bucket:
                continue
            try:
                kr_per_m2 = float(str(row.get("kr_per_m2", "")).replace(",", "."))
            except Exception:
                continue
            updated = (row.get("updated") or "").strip() or "—"
            table.setdefault(city, {})[bucket] = (kr_per_m2, updated)

    _table_cache = table
    _table_mtime = mtime
    return table


# -------------------------------
# Tekstlig bucket-heuristikk (Bergen)
# -------------------------------


def _bergen_bucket_from_text(txt: str) -> Optional[str]:
    """
    Veldig enkel heuristikk for Bergen – kan utvides.
    Matcher mot kjente bydeler/stedsnavn.
    """
    t = _norm(txt)

    if any(
        k in t
        for k in [
            "sentrum",
            "bergenhus",
            "nøstet",
            "møhlenpris",
            "marken",
            "torgalmenningen",
        ]
    ):
        return "Bergen sentrum"

    if any(
        k in t
        for k in [
            "laksevåg",
            "loddefjord",
            "godvik",
            "melkeplassen",
            "damsgård",
            "fyllingsdalen",
        ]
    ):
        return "Bergen vest"

    if any(
        k in t
        for k in ["paradis", "hop", "nesttun", "sandsli", "kokstad", "fana", "lagunen"]
    ):
        return "Bergen sør"

    if any(k in t for k in ["åsane", "eidsvåg", "toppe", "tellevik", "salhus"]):
        return "Bergen nord"

    if any(k in t for k in ["arna", "indre arna", "ytre arna"]):
        return "Bergen øst"

    return None


def _pick_bucket(
    city: str,
    info: Dict[str, object],
    city_buckets: Dict[str, Tuple[float, str]],
) -> Tuple[str, float, str, float, str]:
    """
    Velger bucket basert på tekstlige hint (adresse/district/subarea/area).
    Fallback: bysnitt / nærmeste bucket til snittet.
    Returnerer (bucket, kr_m2, updated, confidence, note).
    """
    candidates: List[str] = []
    for k in ("district", "subarea", "area", "address"):
        v = info.get(k)
        if v:
            candidates.append(str(v))

    if _norm(city) == "bergen":
        for c in candidates:
            b = _bergen_bucket_from_text(c)
            if b and b in city_buckets:
                kr, upd = city_buckets[b]
                return b, kr, upd, 0.9, f"Traff bydel '{b}' fra tekst."

    if len(city_buckets) == 1:
        b, (kr, upd) = next(iter(city_buckets.items()))
        return b, kr, upd, 0.7, "Kun én bucket for byen i tabellen."

    vals = [v[0] for v in city_buckets.values()]
    avg = sum(vals) / len(vals)
    b, (kr, upd) = min(city_buckets.items(), key=lambda kv: abs(kv[1][0] - avg))
    return b, kr, upd, 0.5, "Ingen klar bydel. Bruker bysnitt/nærmeste bucket."


# -------------------------------
# Hoved-API
# -------------------------------


def get_rent_by_csv(
    info: Dict[str, object],
    area_m2: Optional[float],
    rooms: Optional[int] = None,
    city_hint: Optional[str] = None,
) -> Optional[RentEstimate]:
    """
    CSV-basert leieestimat med GeoJSON-prioritet:
      1) Finn by (scrape -> hint -> DEFAULT_CITY)
      2) Hvis lat/lon + by støttes i geo, finn bucket fra GeoJSON
      3) Slå opp m²-pris i CSV (geo-bucket > bysnitt > tekstlig heuristikk)
      4) Regn ut brutto leie
    """
    table = _load_table()
    if not table:
        return None

    # 1) By
    scraped_city = (str(info.get("city") or info.get("municipality") or "")).strip()
    city = (city_hint or scraped_city or DEFAULT_CITY).strip()
    if city not in table:
        ci_map = {k.lower(): k for k in table.keys()}
        city = ci_map.get(city.lower())
        if not city:
            return None
    city_buckets = table[city]

    # 2) GeoJSON-bucket (om mulig)
    bucket_hint: Optional[str] = None
    lat = info.get("lat")
    lon = info.get("lon")
    try:
        lat_f = float(lat) if lat is not None else None
        lon_f = float(lon) if lon is not None else None
    except Exception:
        lat_f, lon_f = None, None

    geo_files = {
        "bergen": os.path.join("data", "geo", "bergen_bydeler.geojson"),
    }
    if lat_f is not None and lon_f is not None:
        gj_path = geo_files.get(city.lower())
        if gj_path and os.path.exists(gj_path):
            try:
                bucket_from_geo = find_bucket_from_point(
                    lat_f, lon_f, gj_path, name_key="name"
                )
            except Exception:
                bucket_from_geo = None
            if bucket_from_geo and bucket_from_geo in city_buckets:
                bucket_hint = bucket_from_geo  # f.eks. "Bergen vest"

    # 3) Slå opp kr/m²
    chosen_bucket: Optional[str] = None
    kr_m2: Optional[float] = None
    updated = ""
    confidence = 0.5
    note = ""

    if bucket_hint and bucket_hint in city_buckets:
        kr_m2, updated = city_buckets[bucket_hint]
        chosen_bucket = bucket_hint
        confidence = 0.9
        note = "Bucket fra GeoJSON."

    if kr_m2 is None:
        # Bysnitt?
        snitt_key = f"{city} snitt"
        if snitt_key in city_buckets:
            kr_m2, updated = city_buckets[snitt_key]
            chosen_bucket = snitt_key
            confidence = 0.5
            note = "Ingen klar bydel. Bruker bysnitt."
        # Tekstlig heuristikk
        if kr_m2 is None:
            b, kr, upd, conf, n = _pick_bucket(city, info, city_buckets)
            chosen_bucket, kr_m2, updated, confidence, note = b, kr, upd, conf, n

    if kr_m2 is None or chosen_bucket is None:
        return None

    # 4) Beregn leie
    a = float(area_m2 or 0.0)
    base = max(0.0, a) * float(kr_m2)

    adj = 0.0
    if rooms and ROOM_ADJ_PER_ROOM > 0.0:
        adj = min(MAX_ROOM_ADJ, (rooms - 1) * ROOM_ADJ_PER_ROOM)

    gross = base * (1.0 + adj)
    rounded = int(round(gross / ROUND_TO)) * ROUND_TO

    return RentEstimate(
        gross_rent=rounded,
        kr_per_m2=float(kr_m2),
        bucket=chosen_bucket,
        city=city,
        confidence=float(confidence),
        note=note,
        updated=updated,
    )
