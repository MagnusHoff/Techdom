from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from techdom.domain.geo import find_bucket_from_point

from .data_access import (
    RentTable,
    fetch_ssb_city_value,
    get_geojson_metadata,
    get_oslo_postnr_exact,
    get_oslo_prefix2,
    load_bucket_table,
)

ROUND_TO = 100
ANNUAL_TO_MONTHLY_THRESHOLD = 1500.0

CONF_GEOJSON = 0.90
CONF_TEXT_MATCH = 0.70
CONF_CITY_AVG = 0.50


@dataclass
class RentEstimate:
    gross_rent: int
    kr_per_m2: float
    bucket: str
    city: str
    confidence: float
    note: str
    updated: str


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def _to_float_or_none(x: Any) -> Optional[float]:
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


def _select_segment(area_m2: Optional[float], rooms: Optional[int]) -> str:
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
    if addr is None:
        return None
    s = str(addr)
    match = re.search(r"\b(\d{4})\b", s)
    return int(match.group(1)) if match else None


def _guess_city(info: Dict[str, object]) -> str:
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

    match = re.search(r"\b(\d{4})\b", text)
    if match:
        p = int(match.group(1))
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
        for lower, upper, name in ranges:
            if lower <= p <= upper:
                return name
    return ""


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
    if any(k in t for k in ["åsane", "eidsvåg", "toppe", "tellevik", "salhus", "myrdal"]):
        return "Bergen nord"
    if any(k in t for k in ["arna", "indre arna", "ytre arna", "espeland", "haukeland"]):
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


def _oslo_bucket_from_postnr(postnr: str) -> Optional[str]:
    exact = get_oslo_postnr_exact()
    if exact and postnr in exact:
        bydel, bucket = exact[postnr]
        if bucket:
            return bucket
        if bydel:
            return _oslo_bucket_from_bydel(bydel)

    prefixes = get_oslo_prefix2()
    if len(postnr) == 4 and postnr.isdigit() and prefixes:
        pref = postnr[:2]
        if pref in prefixes:
            return prefixes[pref]
    return None


def _load_city_buckets(table: RentTable, city: str) -> Dict[str, Dict[str, Tuple[float, str]]]:
    return table.get(city, {}) if city else {}


def get_rent_by_csv(
    info: Dict[str, object],
    area_m2: Optional[float],
    rooms: Optional[int] = None,
    city_hint: Optional[str] = None,
) -> Optional[RentEstimate]:
    table = load_bucket_table() or {}

    scraped_city = (str(info.get("city") or info.get("municipality") or "")).strip()
    raw_city = (city_hint or scraped_city or _guess_city(info) or "").strip()
    city_csv = _canon_city_for_csv(raw_city) if raw_city else ""
    city_display = city_csv or "Hele landet"
    city_buckets = _load_city_buckets(table, city_csv) if city_csv else {}

    supported_for_buckets = {"Bergen", "Oslo"}
    note_parts: List[str] = []
    if city_csv and city_csv not in supported_for_buckets:
        city_buckets = {}
        note_parts.append(
            "Bydelsinndelt leie er foreløpig kun støttet for Bergen og Oslo. "
            "Bruker SSB bysnitt for denne byen."
        )

    bucket: Optional[str] = None
    confidence = CONF_CITY_AVG
    if city_csv:
        note_parts.append(f"By brukt: {city_csv}")

    lat = info.get("lat")
    lon = info.get("lon")
    lat_f = _to_float_or_none(lat)
    lon_f = _to_float_or_none(lon)

    if lat_f is not None and lon_f is not None and city_csv and city_buckets:
        gj = get_geojson_metadata(city_csv)
        if gj:
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

    if bucket is None and _norm(city_csv) == "bergen" and city_buckets:
        postal = _extract_postal(info.get("address"))
        if postal is not None:
            bucket_from_postal = _bergen_bucket_from_postal(postal)
            if bucket_from_postal and bucket_from_postal in city_buckets:
                bucket = bucket_from_postal
                confidence = max(confidence, CONF_TEXT_MATCH)
                note_parts.append(f"Bucket fra postnr {postal}: {bucket}")

    if bucket is None and _norm(city_csv) == "oslo" and city_buckets:
        match = re.search(r"\b(\d{4})\b", str(info.get("address") or ""))
        if match:
            bucket_from_postal = _oslo_bucket_from_postnr(match.group(1))
            if bucket_from_postal and bucket_from_postal in city_buckets:
                bucket = bucket_from_postal
                confidence = max(confidence, CONF_TEXT_MATCH)
                note_parts.append(f"Bucket fra postnr {match.group(1)}: {bucket}")

    if bucket is None and city_buckets and _norm(city_csv) == "bergen":
        for key in [
            info.get("district"),
            info.get("subarea"),
            info.get("area"),
            info.get("address"),
        ]:
            if not key:
                continue
            bucket_from_text = _bergen_bucket_from_text(str(key))
            if bucket_from_text and bucket_from_text in city_buckets:
                bucket = bucket_from_text
                confidence = max(confidence, CONF_TEXT_MATCH)
                note_parts.append(f"Traff bydel fra tekst: {bucket}")
                break

    segment = _select_segment(area_m2, rooms)
    note_parts.append(f"Segment: {segment}")

    def _get(city_bucket: str, seg: str) -> Optional[Tuple[float, str]]:
        return city_buckets.get(city_bucket, {}).get(seg)

    def _std(city_bucket: str) -> Optional[float]:
        record = _get(city_bucket, "standard")
        return float(record[0]) if record else None

    ratio: Optional[float] = None
    if city_buckets and bucket and (f"{city_csv} snitt" in city_buckets) and (bucket in city_buckets):
        csv_city_std = _std(f"{city_csv} snitt")
        csv_bucket_std = _std(bucket)
        if csv_city_std and csv_bucket_std:
            try:
                ratio = float(csv_bucket_std) / float(csv_city_std)
            except Exception:
                ratio = None

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
    for candidate in ssb_candidates:
        value = fetch_ssb_city_value(candidate, segment)
        if value is not None:
            ssb_value = value
            ssb_used_label = candidate
            break

    kr_per_m2: Optional[float] = None
    used_bucket: Optional[str] = None
    used_segment: Optional[str] = None
    updated = "—"

    if ssb_value is not None:
        if ratio is not None:
            kr_per_m2 = ssb_value * float(ratio)
            used_bucket = bucket or (
                f"{city_csv} snitt" if city_csv else (ssb_used_label or "Hele landet")
            )
            used_segment = segment
            updated = "SSB × CSV-ratio"
            confidence = max(confidence, 0.85)
            note_parts.append(f"Kilde: SSB ({ssb_used_label}) × CSV-bucket-ratio")
        else:
            kr_per_m2 = ssb_value
            used_bucket = f"{city_csv} snitt" if city_csv else (ssb_used_label or "Hele landet")
            used_segment = segment
            updated = "SSB"
            confidence = max(confidence, 0.80)
            note_parts.append(f"Kilde: SSB ({ssb_used_label})")

    if kr_per_m2 is None and city_buckets:
        if bucket:
            got = _get(bucket, segment)
            if got:
                kr_per_m2, updated = float(got[0]), got[1]
                used_bucket, used_segment = bucket, segment

        if kr_per_m2 is None and bucket:
            got = _get(bucket, "standard")
            if got:
                kr_per_m2, updated = float(got[0]), got[1]
                used_bucket, used_segment = bucket, "standard"

        if kr_per_m2 is None and city_csv:
            got = _get(f"{city_csv} snitt", segment)
            if got:
                kr_per_m2, updated = float(got[0]), got[1]
                used_bucket, used_segment = f"{city_csv} snitt", segment
                confidence = min(confidence, CONF_CITY_AVG)
                note_parts.append("Fallback: bysnitt (segment)")

        if kr_per_m2 is None and city_csv:
            got = _get(f"{city_csv} snitt", "standard")
            if got:
                kr_per_m2, updated = float(got[0]), got[1]
                used_bucket, used_segment = f"{city_csv} snitt", "standard"
                confidence = min(confidence, CONF_CITY_AVG)
                note_parts.append("Fallback: bysnitt (standard)")

    if kr_per_m2 is None:
        return None

    if not used_bucket:
        used_bucket = bucket or (
            f"{city_csv} snitt" if city_csv else (ssb_used_label or "Hele landet")
        )
    if not used_segment:
        used_segment = segment

    monthly_adjusted = False
    if kr_per_m2 is not None and kr_per_m2 > ANNUAL_TO_MONTHLY_THRESHOLD:
        kr_per_m2 = float(kr_per_m2) / 12.0
        monthly_adjusted = True

    area = float(area_m2 or 0.0)
    gross = max(0.0, area) * float(kr_per_m2)
    rounded = int(round(gross / ROUND_TO)) * ROUND_TO

    note_parts.append(f"Oppslag: {used_bucket} / {used_segment}")
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
