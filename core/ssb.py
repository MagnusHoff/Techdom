# core/ssb.py
from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

RENT_CSV = Path("data/rent_m2.csv")

_SEG_CANON = {
    "hybel": "hybel",
    "liten": "liten",
    "standard": "standard",
    "stor": "stor",
    "small": "liten",
    "medium": "standard",
    "large": "stor",
}


def _norm(s: Optional[str]) -> str:
    return (s or "").strip()


def _canon_seg(seg: str) -> str:
    key = _norm(seg).lower()
    return _SEG_CANON.get(key, "standard")


def _canon_city(s: str) -> str:
    t = _norm(s)
    # Enkle normaliseringer; behold original casing for visning
    low = t.lower()
    mapping = {
        "oslo": "Oslo",
        "bergen": "Bergen",
        "trondheim": "Trondheim",
        "stavanger": "Stavanger",
        "tromsø": "Tromsø",
        "tromso": "Tromsø",
        "kristiansand": "Kristiansand",
        "drammen": "Drammen",
        "fredrikstad": "Fredrikstad",
        "sarpsborg": "Sarpsborg",
        "skien": "Skien",
        "porsgrunn": "Porsgrunn",
        "sandnes": "Sandnes",
        "ålesund": "Ålesund",
        "alesund": "Ålesund",
        "haugesund": "Haugesund",
        "hele landet": "Hele landet",
        "store tettsteder": "Store tettsteder",
        "mellomstore tettsteder": "Mellomstore tettsteder",
        "små tettsteder/spredt": "Små tettsteder/spredt",
    }
    return mapping.get(low, t)


def _read_csv() -> List[Dict[str, str]]:
    if not RENT_CSV.exists():
        return []
    rows: List[Dict[str, str]] = []
    with RENT_CSV.open("r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            # sikre at kolonner finnes
            city = _norm(row.get("city"))
            bucket = _norm(row.get("bucket"))
            seg = _canon_seg(_norm(row.get("segment")))
            kr = _norm(row.get("kr_per_m2"))
            upd = _norm(row.get("updated") or row.get("update") or "")
            if not city or not bucket or not seg or not kr:
                continue
            # parse kr_per_m2
            try:
                krf = float(kr.replace(",", "."))
            except Exception:
                continue
            rows.append(
                {
                    "city": city,
                    "bucket": bucket,
                    "segment": seg,
                    "kr_per_m2": f"{krf:.6f}",
                    "updated": upd,
                }
            )
    return rows


def _city_snitt_from_rows(
    rows: List[Dict[str, str]], city: str, segment: str
) -> Optional[float]:
    """
    Finn bysnitt for segment.
    1) Hvis en rad finnes med bucket == "<City> snitt" -> bruk den.
    2) Ellers snitt over alle buckets for byen for gitt segment.
    """
    city_rows = [
        r for r in rows if _canon_city(r["city"]) == city and r["segment"] == segment
    ]
    if not city_rows:
        return None

    # 1) direkte snitt-rad
    want_bucket = f"{city} snitt"
    for r in city_rows:
        if r["bucket"].lower() == want_bucket.lower():
            return float(r["kr_per_m2"])

    # 2) gjennomsnitt over buckets
    vals = [float(r["kr_per_m2"]) for r in city_rows]
    if vals:
        return float(sum(vals) / len(vals))
    return None


# Baseline fallback (brukes kun hvis CSV mangler eller by ikke finnes)
# Tallene her er placebo/plausible – systemet fungerer, men du bør overstyre via CSV.
_BASELINE_STANDARD = {
    "Hele landet": 260.0,
    "Oslo": 400.0,
    "Bergen": 250.0,
    "Trondheim": 280.0,
    "Stavanger": 270.0,
    "Tromsø": 290.0,
    "Kristiansand": 260.0,
    "Drammen": 270.0,
    "Fredrikstad": 250.0,
    "Sarpsborg": 230.0,
    "Skien": 240.0,
    "Porsgrunn": 240.0,
    "Sandnes": 260.0,
    "Ålesund": 250.0,
    "Haugesund": 240.0,
}

# Segmentfaktorer ift "standard" (enkelt, juster ved behov)
_SEG_FACTOR = {
    "hybel": 1.10,
    "liten": 1.05,
    "standard": 1.00,
    "stor": 0.92,
}


def get_city_m2_month(
    city_name: str,
    segment: str,
    year: int | None = None,
    quarter: int | None = None,
) -> float:
    """
    Returnerer kr/m² per måned for (by, segment).
    Bruker data/rent_m2.csv hvis mulig, ellers baseline.
    year/quarter ignoreres her (dummy), men beholdes i signatur.
    """
    city = _canon_city(city_name)
    seg = _canon_seg(segment)

    rows = _read_csv()
    if rows:
        val = _city_snitt_from_rows(rows, city, seg)
        if val is not None:
            return float(val)

    # fallback
    base_std = _BASELINE_STANDARD.get(city)
    if base_std is None:
        # hvis ukjent by → fall tilbake til Hele landet
        base_std = _BASELINE_STANDARD["Hele landet"]
    factor = _SEG_FACTOR.get(seg, 1.0)
    return float(base_std * factor)


# --- For skriptet ditt (dummy-implementasjoner) ---


def list_soner2() -> Dict[str, str]:
    """
    Minimal Soner2-liste (kodene som matcher eksempelet ditt).
    """
    return {
        "00": "Hele landet",
        "01": "Oslo og Bærum kommune",
        "02": "Akershus utenom Bærum kommune",
        "03": "Trondheim kommune",
        "04": "Bergen kommune",
        "05": "Stavanger kommune",
        "20": "Store tettsteder",
        "21": "Mellomstore tettsteder",
        "22": "Små tettsteder/spredt",
    }


def _label_to_city(label: str) -> Optional[str]:
    # map Soner2-label → vår by for CSV/baseline
    m = {
        "Hele landet": "Hele landet",
        "Oslo og Bærum kommune": "Oslo",
        "Akershus utenom Bærum kommune": "Akershus",  # ikke i baseline/CSV typisk
        "Bergen kommune": "Bergen",
        "Trondheim kommune": "Trondheim",
        "Stavanger kommune": "Stavanger",
        "Store tettsteder": "Store tettsteder",
        "Mellomstore tettsteder": "Mellomstore tettsteder",
        "Små tettsteder/spredt": "Små tettsteder/spredt",
    }
    return m.get(label)


def get_segment_m2_by_soner2(
    year: int | None = None,
    rom_code: str | None = "1",  # vi bruker dette til å velge segment ~ "hybel"
) -> Dict[str, Tuple[str, float]]:
    """
    Returnerer { code: (label, kr_per_m2_mnd) } for valgt romkategori.
    Implementasjonen her er *dummy*: vi kalkulerer fra CSV/ baseline via get_city_m2_month.
    """
    # enkel rom→segment mapping
    seg = "hybel" if (rom_code or "1").strip() == "1" else "standard"

    res: Dict[str, Tuple[str, float]] = {}
    for code, label in list_soner2().items():
        city = _label_to_city(label)
        if city is None:
            continue
        try:
            val = get_city_m2_month(city, seg, year=year)
        except Exception:
            val = math.nan
        res[code] = (label, float(val))
    return res
