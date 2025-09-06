# core/rent_csv.py
from __future__ import annotations
import csv
from functools import lru_cache
from typing import Optional, Dict, Tuple

from core.geo import find_bucket_from_point

CSV_PATH = "data/rent_m2.csv"


@lru_cache(maxsize=1)
def _load_table(path: str = CSV_PATH) -> list[dict]:
    out: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            city = (row.get("city") or "").strip()
            bucket = (row.get("bucket") or "").strip()
            krm2 = row.get("kr_per_m2")
            updated = (row.get("updated") or "").strip()
            try:
                val = float(str(krm2).replace(",", "."))
            except Exception:
                continue
            out.append(
                {"city": city, "bucket": bucket, "kr_per_m2": val, "updated": updated}
            )
    return out


def _city_rows(city: str) -> list[dict]:
    t = _load_table()
    return [r for r in t if r["city"].lower() == (city or "").strip().lower()]


def _avg_krm2(rows: list[dict]) -> Optional[float]:
    if not rows:
        return None
    return sum(r["kr_per_m2"] for r in rows) / len(rows)


def estimate_rent_from_csv(
    city: Optional[str],
    area_m2: Optional[float],
    rooms: Optional[int],
    lat: Optional[float],
    lng: Optional[float],
) -> dict:
    """
    Slår opp kr/m² for (city, bucket) – bucket via GeoJSON (lat/lng) hvis mulig.
    Fallback: by-snitt. Returnerer et debug-vennlig dict.
    """
    city = (city or "").strip().title()
    rows = _city_rows(city)
    debug = {
        "source": "csv",
        "city": city or None,
        "bucket": None,
        "kr_per_m2": None,
        "updated": None,
        "confidence": 0.0,
        "note": "",
        "area_m2": area_m2,
        "rooms": rooms,
    }

    if not rows:
        debug["note"] = "Fant ingen by i rent_m2.csv."
        return {**debug, "rent": None}

    # 1) forsøk bydel via GeoJSON
    bucket, bydel = find_bucket_from_point(city, lat, lng)
    if bucket:
        match = next((r for r in rows if r["bucket"].lower() == bucket.lower()), None)
        if match:
            krm2 = match["kr_per_m2"]
            debug.update(
                {
                    "bucket": bucket,
                    "kr_per_m2": krm2,
                    "updated": match["updated"],
                    "confidence": 0.9,
                    "note": f"GeoJSON-treff i bydel: {bydel or bucket}.",
                }
            )
        else:
            krm2 = _avg_krm2(rows)
            debug.update(
                {
                    "bucket": bucket,
                    "kr_per_m2": krm2,
                    "updated": None,
                    "confidence": 0.6,
                    "note": f"Bydel '{bydel or bucket}' ikke i CSV – brukte bysnitt.",
                }
            )
    else:
        # 2) fallback: by-snitt
        krm2 = _avg_krm2(rows)
        debug.update(
            {
                "bucket": None,
                "kr_per_m2": krm2,
                "updated": None,
                "confidence": 0.5,
                "note": "Ingen lat/lng eller polygon-treff – brukte bysnitt.",
            }
        )

    if not krm2 or not area_m2:
        return {**debug, "rent": None}

    est = round(krm2 * float(area_m2) / 100.0) * 100  # rund til nærmeste 100
    return {**debug, "rent": int(est)}
