# core/rent_csv.py
from __future__ import annotations
import csv
from pathlib import Path
from typing import Dict, Tuple, Optional

CSV_PATH = Path("data/rent_m2.csv")


def load_bucket_ratios() -> Dict[Tuple[str, str, str], float]:
    """
    Leser data/rent_m2.csv (city,bucket,segment,kr_per_m2,updated) og
    bygger ratio(bucket,segment) = bucket_m2 / city_snitt_m2.

    Returnerer dict[(city_lower, bucket, segment)] = ratio (float).
    """
    if not CSV_PATH.exists():
        return {}

    rows = []
    with CSV_PATH.open("r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                city = (row.get("city") or "").strip()
                bucket = (row.get("bucket") or "").strip()
                seg = (row.get("segment") or "standard").strip().lower()
                kr = float(str(row.get("kr_per_m2", "")).replace(",", "."))
                rows.append((city, bucket, seg, kr))
            except Exception:
                pass

    per_city_seg: Dict[str, Dict[str, Dict[str, float]]] = {}
    for city, bucket, seg, kr in rows:
        per_city_seg.setdefault(city, {}).setdefault(seg, {})
        per_city_seg[city][seg][bucket] = kr

    ratios: Dict[Tuple[str, str, str], float] = {}
    for city, seg_map in per_city_seg.items():
        for seg, buckets in seg_map.items():
            city_snitt_key = f"{city} snitt"
            base = buckets.get(city_snitt_key)
            if not base:
                continue
            for bucket, kr in buckets.items():
                if bucket == city_snitt_key:
                    continue
                try:
                    r = float(kr) / float(base)
                    ratios[(city.lower(), bucket, seg)] = r
                except Exception:
                    pass
    return ratios
