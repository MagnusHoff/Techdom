# core/rent.py
from __future__ import annotations

import os
import re
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple, List, Optional, Any

from techdom.domain.geo_registry import get_geojson_info
from techdom.domain.geo import find_bucket_from_point
from techdom.integrations.ssb import get_city_m2_month  # kr/m² per MND for (by, segment)

# -------------------------------
# Konfig
# -------------------------------
CSV_PATH = Path("data/processed/rent_m2.csv")  # kolonner: city,bucket,segment,kr_per_m2,updated
ROUND_TO = 100
ANNUAL_TO_MONTHLY_THRESHOLD = 1500.0  # over dette tolker vi tall som årlig beløp

CONF_GEOJSON = 0.90
CONF_TEXT_MATCH = 0.70
CONF_CITY_AVG = 0.50


# -------------------------------
# Datamodell
# -------------------------------
@dataclass
class RentEstimate:
    gross_rent: int  # foreslått brutto leie (kr/mnd)
    kr_per_m2: float  # brukt m²-pris (kr/mnd per m²)
    bucket: str  # bydel/bucket som ble brukt
    city: str  # by (for visning)
    confidence: float  # 0.0–1.0
    note: str  # forklaring/fallback
    updated: str  # f.eks. "SSB" / "SSB × CSV-ratio" / CSV-kilde


# -------------------------------
# CSV-cache: table[city][bucket][segment] = (kr_m2, updated)
# -------------------------------
_table: Dict[str, Dict[str, Dict[str, Tuple[float, str]]]] = {}
_table_mtime: Optional[float] = None


# -------------------------------
# Hjelpere
# -------------------------------
def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def _to_float_or_none(x: Any) -> Optional[float]:
    """Trygg konvertering fra ukjent type til float."""
    if x is None:
        return None
    try:
        if isinstance(x, (int, float)):
            return float(x)
        if isinstance(x, str):
            t = x.strip().replace(",", ".")
            if not t:
                return None
            return float(t)
    except Exception:
        return None
    return None


def _canon_city_for_csv(s: str) -> str:
    """Normaliser bynavn så de matcher rent_m2.csv og SSB."""
    t = _norm(s)
    if t.endswith(" kommune"):
        t = t[:-8].strip()
    mapping = {
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
    }
    return mapping.get(t, s.strip() or "")


def _load_table(
    force: bool = False,
) -> Dict[str, Dict[str, Dict[str, Tuple[float, str]]]]:
    """Laster data/processed/rent_m2.csv inn i et nestet dict."""
    global _table, _table_mtime
    if not CSV_PATH.exists():
        _table, _table_mtime = {}, None
        return {}

    mtime = CSV_PATH.stat().st_mtime
    if not force and _table and _table_mtime == mtime:
        return _table

    table: Dict[str, Dict[str, Dict[str, Tuple[float, str]]]] = {}
    with CSV_PATH.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            city = (row.get("city") or "").strip()
            bucket = (row.get("bucket") or "").strip()
            segment = (row.get("segment") or "standard").strip().lower()
            try:
                kr_per_m2 = float(str(row.get("kr_per_m2", "")).replace(",", "."))
            except ValueError:
                continue
            updated = (row.get("updated") or "—").strip()
            table.setdefault(city, {}).setdefault(bucket, {})[segment] = (
                kr_per_m2,
                updated,
            )

    _table = table
    _table_mtime = mtime
    return table


def _select_segment(area_m2: Optional[float], rooms: Optional[int]) -> str:
    """hybel (<30m²/≤1rom), liten (30–50/2), standard (50–90/3), stor (>90/≥4)."""
    a = float(area_m2 or 0.0)
    r = rooms if rooms is not None else None
    if (a > 0 and a < 30) or (r is not None and r <= 1):
        return "hybel"
    if (30 <= a <= 50) or (r == 2):
        return "liten"
    if (50 < a <= 90) or (r == 3):
        return "standard"
    if (a > 90) or (r is not None and r >= 4):
        return "stor"
    return "standard"


def _extract_postal(addr: Any) -> Optional[int]:
    """Trekk ut firesifret postnummer fra vilkårlig verdi (str, tall, None)."""
    if addr is None:
        return None
    s = str(addr)
    m = re.search(r"\b(\d{4})\b", s)
    return int(m.group(1)) if m else None


def _guess_city(info: Dict[str, object]) -> str:
    """Prøv å finne by fra tekst eller postnummer. Returnerer '' hvis ukjent."""
    text = " ".join(
        str(x or "")
        for x in [
            info.get("city"),
            info.get("municipality"),
            info.get("district"),
            info.get("area"),
            info.get("address"),
        ]
    ).lower()

    for needle, cityname in [
        ("oslo", "Oslo"),
        ("bergen", "Bergen"),
        ("trondheim", "Trondheim"),
        ("stavanger", "Stavanger"),
        ("tromsø", "Tromsø"),
        ("tromso", "Tromsø"),
        ("kristiansand", "Kristiansand"),
        ("drammen", "Drammen"),
        ("fredrikstad", "Fredrikstad"),
        ("sarpsborg", "Sarpsborg"),
        ("skien", "Skien"),
        ("porsgrunn", "Porsgrunn"),
        ("sandnes", "Sandnes"),
        ("ålesund", "Ålesund"),
        ("alesund", "Ålesund"),
        ("haugesund", "Haugesund"),
    ]:
        if needle in text:
            return cityname

    m = re.search(r"\b(\d{4})\b", text)
    if m:
        p = int(m.group(1))
        ranges = [
            (1, 1299, "Oslo"),
            (4000, 4099, "Stavanger"),
            (4300, 4399, "Sandnes"),
            (5003, 5299, "Bergen"),
            (5500, 5599, "Haugesund"),
            (6000, 6099, "Ålesund"),
            (7000, 7099, "Trondheim"),
            (9000, 9099, "Tromsø"),
            (1600, 1699, "Fredrikstad"),
            (1700, 1799, "Sarpsborg"),
            (3000, 3099, "Drammen"),
            (3700, 3799, "Skien"),
            (3900, 3999, "Porsgrunn"),
            (4600, 4699, "Kristiansand"),
        ]
        for a, b, nm in ranges:
            if a <= p <= b:
                return nm
    return ""


# -------------------------------
# Bergen heuristikk (tekst + postnr)
# -------------------------------
def _bergen_bucket_from_text(txt: str) -> Optional[str]:
    t = _norm(txt)
    if any(
        k in t
        for k in [
            "sentrum",
            "bergenhus",
            "nøstet",
            "møhlenpris",
            "marken",
            "torgallmenningen",
            "verftet",
            "nordnes",
            "engens",
            "nygårdshøyden",
            "sydnes",
            "strandkaien",
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
            "kråkenes",
            "lyngbø",
            "gravdal",
            "vadmyra",
            "olsvik",
        ]
    ):
        return "Bergen vest"
    if any(
        k in t
        for k in [
            "årstad",
            "minde",
            "landås",
            "kronstad",
            "solheim",
            "danmarksplass",
            "wergeland",
            "fana",
            "ytrebygda",
            "nesttun",
            "paradis",
            "sandsli",
            "kokstad",
            "flesland",
            "nattland",
            "smørås",
        ]
    ):
        return "Bergen sør"
    if any(
        k in t for k in ["åsane", "eidsvåg", "toppe", "tellevik", "salhus", "myrdal"]
    ):
        return "Bergen nord"
    if any(
        k in t for k in ["arna", "indre arna", "ytre arna", "espeland", "haukeland"]
    ):
        return "Bergen øst"
    return None


def _bergen_bucket_from_postal(postal: int) -> Optional[str]:
    if 5003 <= postal <= 5050:
        return "Bergen sentrum"
    if 5051 <= postal <= 5159:
        return "Bergen sør"
    if 5100 <= postal <= 5139:
        return "Bergen nord"
    if 5160 <= postal <= 5179:
        return "Bergen vest"
    if 5260 <= postal <= 5269:
        return "Bergen øst"
    if 5200 <= postal <= 5299:
        return "Bergen sør"
    return None


# -------------------------------
# Oslo: bydel → bucket + postnr-fallback
# -------------------------------
def _oslo_bucket_from_bydel(bydel_name: str) -> str:
    n = _norm(bydel_name)
    sentrum = {
        "gamle oslo",
        "grünerløkka",
        "grunerloekka",
        "sagene",
        "st. hanshaugen",
        "st hanshaugen",
        "sentrum",
    }
    vest = {"frogner", "ullern", "vestre aker", "nordre aker"}
    if n in sentrum:
        return "Oslo sentrum"
    if n in vest:
        return "Oslo vest"
    return "Oslo øst"


_oslo_postnr_exact_cache: Optional[Dict[str, Tuple[Optional[str], Optional[str]]]] = (
    None
)
_oslo_prefix2_cache: Optional[Dict[str, str]] = None


def _load_oslo_postnr_exact(
    path: str = "data/static/lookup/postnr/oslo_postnr.csv",
) -> Dict[str, Tuple[Optional[str], Optional[str]]]:
    m: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
    if not os.path.exists(path):
        return m
    with open(path, "r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            p = (row.get("postnr") or row.get("postcode") or "").strip()
            bydel = (row.get("bydel") or row.get("bydelnavn") or "").strip() or None
            bucket = (row.get("bucket") or "").strip() or None
            if len(p) == 4 and p.isdigit():
                m[p] = (bydel, bucket)
    return m


def _load_oslo_prefix2(path: str = "data/static/lookup/postnr/oslo_prefix.csv") -> Dict[str, str]:
    m: Dict[str, str] = {}
    if not os.path.exists(path):
        return m
    with open(path, "r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            pref = (row.get("prefix2") or "").strip()
            bucket = (row.get("bucket") or "").strip()
            if len(pref) == 2 and pref.isdigit() and bucket:
                m[pref] = bucket
    return m


def _oslo_bucket_from_postnr(postnr: str) -> Optional[str]:
    global _oslo_postnr_exact_cache, _oslo_prefix2_cache
    if _oslo_postnr_exact_cache is None:
        _oslo_postnr_exact_cache = _load_oslo_postnr_exact()
    if _oslo_prefix2_cache is None:
        _oslo_prefix2_cache = _load_oslo_prefix2()

    if _oslo_postnr_exact_cache and postnr in _oslo_postnr_exact_cache:
        bydel, bucket = _oslo_postnr_exact_cache[postnr]
        if bucket:
            return bucket
        if bydel:
            return _oslo_bucket_from_bydel(bydel)

    if len(postnr) == 4 and postnr.isdigit() and _oslo_prefix2_cache:
        pref = postnr[:2]
        if pref in _oslo_prefix2_cache:
            return _oslo_prefix2_cache[pref]
    return None


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
    Hybrid SSB + CSV:
      1) Finn by og (ev.) bucket (GeoJSON → postnr → tekst).
      2) Finn segment.
      3) SSB bysnitt (måned) skaleres med CSV-bucket-ratio når mulig.
      4) CSV fallback hvis SSB ikke ga verdi.
    """
    table = _load_table() or {}  # selv om CSV mangler, fortsett (SSB kan ta over)

    # --- Finn by trygt (unngå uinitialiserte variabler)
    scraped_city = (str(info.get("city") or info.get("municipality") or "")).strip()
    raw_city = (city_hint or scraped_city or _guess_city(info) or "").strip()
    city_csv = _canon_city_for_csv(raw_city) if raw_city else ""
    city_display = city_csv or "Hele landet"
    city_buckets = table.get(city_csv, {}) if city_csv else {}

    # --- Kun Bergen/Oslo har bucket-støtte nå
    SUPPORTED_FOR_BUCKETS = {"Bergen", "Oslo"}
    note_parts: List[str] = []
    if city_csv and city_csv not in SUPPORTED_FOR_BUCKETS:
        # slå av all bucket/GeoJSON-bruk og gå rett på SSB
        city_buckets = {}
        note_parts.append(
            "Bydelsinndelt leie er foreløpig kun støttet for Bergen og Oslo. "
            "Bruker SSB bysnitt for denne byen."
        )

    # --- Finn bucket (GeoJSON → postnr → tekst)
    bucket: Optional[str] = None
    confidence = CONF_CITY_AVG
    if city_csv:
        note_parts.append(f"By brukt: {city_csv}")

    # GeoJSON (dersom registrert i geo_registry)
    lat = info.get("lat")
    lon = info.get("lon")
    lat_f = _to_float_or_none(lat)
    lon_f = _to_float_or_none(lon)

    if lat_f is not None and lon_f is not None and city_csv and city_buckets:
        gj = get_geojson_info(city_csv)
        if gj and os.path.exists(gj["path"]):
            try:
                bydel_name = find_bucket_from_point(
                    lat_f, lon_f, gj["path"], name_key=gj["name_key"]
                )
            except Exception:
                bydel_name = None
            if bydel_name:
                if _norm(city_csv) == "oslo":
                    mapped = _oslo_bucket_from_bydel(bydel_name)
                    if mapped in city_buckets:
                        bucket = mapped
                        confidence = CONF_GEOJSON
                        note_parts.append(
                            f"Bucket fra GeoJSON (Oslo): {bydel_name} → {mapped}"
                        )
                else:
                    if bydel_name in city_buckets:
                        bucket = bydel_name
                        confidence = CONF_GEOJSON
                        note_parts.append(f"Bucket fra GeoJSON: {bucket}")

    # Postnr → bucket (Bergen)
    if bucket is None and _norm(city_csv) == "bergen" and city_buckets:
        p = _extract_postal(info.get("address"))
        if p is not None:
            b_post = _bergen_bucket_from_postal(p)
            if b_post and b_post in city_buckets:
                bucket = b_post
                confidence = max(confidence, CONF_TEXT_MATCH)
                note_parts.append(f"Bucket fra postnr {p}: {bucket}")

    # Postnr → bucket (Oslo)
    if bucket is None and _norm(city_csv) == "oslo" and city_buckets:
        m = re.search(r"\b(\d{4})\b", str(info.get("address") or ""))
        if m:
            b_post = _oslo_bucket_from_postnr(m.group(1))
            if b_post and b_post in city_buckets:
                bucket = b_post
                confidence = max(confidence, CONF_TEXT_MATCH)
                note_parts.append(f"Bucket fra postnr {m.group(1)}: {bucket}")

    # Tekst → bucket (Bergen)
    if bucket is None and city_buckets and _norm(city_csv) == "bergen":
        for key in [
            info.get("district"),
            info.get("subarea"),
            info.get("area"),
            info.get("address"),
        ]:
            if not key:
                continue
            b_txt = _bergen_bucket_from_text(str(key))
            if b_txt and b_txt in city_buckets:
                bucket = b_txt
                confidence = max(confidence, CONF_TEXT_MATCH)
                note_parts.append(f"Traff bydel fra tekst: {bucket}")
                break

    # --- Segment
    seg = _select_segment(area_m2, rooms)
    note_parts.append(f"Segment: {seg}")

    # --- CSV-ratio (bucket vs "<city> snitt")
    def _get(b: str, s: str) -> Optional[Tuple[float, str]]:
        return city_buckets.get(b, {}).get(s)

    def _std(b: str) -> Optional[float]:
        rec = _get(b, "standard")
        return float(rec[0]) if rec else None

    ratio: Optional[float] = None
    if (
        city_buckets
        and bucket
        and (f"{city_csv} snitt" in city_buckets)
        and (bucket in city_buckets)
    ):
        csv_city_std = _std(f"{city_csv} snitt")
        csv_bucket_std = _std(bucket)
        if csv_city_std and csv_bucket_std:
            try:
                ratio = float(csv_bucket_std) / float(csv_city_std)
            except Exception:
                ratio = None

    # --- SSB (kr/m² MND) – prøv i prioritert rekkefølge
    ssb_candidates: List[str] = []
    if city_csv:
        ssb_candidates.append(city_csv)
    ssb_candidates += [
        "Hele landet",
        "Store tettsteder",
        "Mellomstore tettsteder",
        "Små tettsteder/spredt",
    ]

    ssb_value: Optional[float] = None
    ssb_used_label: Optional[str] = None
    for cand in ssb_candidates:
        try:
            v = get_city_m2_month(city_name=cand, segment=seg, year=None)
        except Exception:
            v = None
        if v is not None:
            ssb_value = float(v)
            ssb_used_label = cand
            break

    kr_per_m2: Optional[float] = None
    used_bucket: Optional[str] = None
    used_seg: Optional[str] = None
    updated = "—"

    # --- SSB + evt. CSV-ratio ---
    if ssb_value is not None:
        if ratio is not None:
            kr_per_m2 = ssb_value * float(ratio)
            used_bucket = bucket or (
                f"{city_csv} snitt" if city_csv else (ssb_used_label or "Hele landet")
            )
            used_seg = seg
            updated = "SSB × CSV-ratio"
            confidence = max(confidence, 0.85)
            note_parts.append(f"Kilde: SSB ({ssb_used_label}) × CSV-bucket-ratio")
        else:
            kr_per_m2 = ssb_value
            used_bucket = (
                f"{city_csv} snitt" if city_csv else (ssb_used_label or "Hele landet")
            )
            used_seg = seg
            updated = "SSB"
            confidence = max(confidence, 0.80)
            note_parts.append(f"Kilde: SSB ({ssb_used_label})")

    # --- CSV fallback hvis SSB ikke ga verdi
    if kr_per_m2 is None and city_buckets:
        # a) bucket/segment
        if bucket:
            got = _get(bucket, seg)
            if got:
                kr_per_m2, updated = float(got[0]), got[1]
                used_bucket, used_seg = bucket, seg

        # b) bucket/standard
        if kr_per_m2 is None and bucket:
            got = _get(bucket, "standard")
            if got:
                kr_per_m2, updated = float(got[0]), got[1]
                used_bucket, used_seg = bucket, "standard"

        # c) bysnitt/segment
        if kr_per_m2 is None and city_csv:
            got = _get(f"{city_csv} snitt", seg)
            if got:
                kr_per_m2, updated = float(got[0]), got[1]
                used_bucket, used_seg = f"{city_csv} snitt", seg
                confidence = min(confidence, CONF_CITY_AVG)
                note_parts.append("Fallback: bysnitt (segment)")

        # d) bysnitt/standard
        if kr_per_m2 is None and city_csv:
            got = _get(f"{city_csv} snitt", "standard")
            if got:
                kr_per_m2, updated = float(got[0]), got[1]
                used_bucket, used_seg = f"{city_csv} snitt", "standard"
                confidence = min(confidence, CONF_CITY_AVG)
                note_parts.append("Fallback: bysnitt (standard)")

    # Hvis vi fortsatt ikke har noe: gi opp
    if kr_per_m2 is None:
        return None

    # Sørg for fornuftige felter
    if not used_bucket:
        used_bucket = bucket or (
            f"{city_csv} snitt" if city_csv else (ssb_used_label or "Hele landet")
        )
    if not used_seg:
        used_seg = seg

    # Noen SSB-kilder leverer kr/m² per år – gjenkjenn via størrelse og del på 12
    monthly_adjusted = False
    if kr_per_m2 is not None and kr_per_m2 > ANNUAL_TO_MONTHLY_THRESHOLD:
        kr_per_m2 = float(kr_per_m2) / 12.0
        monthly_adjusted = True

    # --- Beregn brutto leie (kr/mnd)
    a = float(area_m2 or 0.0)
    gross = max(0.0, a) * float(kr_per_m2)
    rounded = int(round(gross / ROUND_TO)) * ROUND_TO

    note_parts.append(f"Oppslag: {used_bucket} / {used_seg}")
    if monthly_adjusted:
        note_parts.append("Konverterte kvadratmeterpris fra år til måned (÷12)")

    return RentEstimate(
        gross_rent=rounded,
        kr_per_m2=float(kr_per_m2),
        bucket=used_bucket,
        city=city_display,
        confidence=float(confidence),
        note="; ".join(note_parts),
        updated=updated,
    )
