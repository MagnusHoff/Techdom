from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Dict, Optional, Tuple

from techdom.domain.geo_registry import get_geojson_info
from techdom.integrations.ssb import get_city_m2_month

RentTable = Dict[str, Dict[str, Dict[str, Tuple[float, str]]]]

CSV_PATH = Path("data/processed/rent_m2.csv")

_table: RentTable = {}
_table_mtime: Optional[float] = None
_oslo_postnr_exact_cache: Optional[Dict[str, Tuple[Optional[str], Optional[str]]]] = None
_oslo_prefix2_cache: Optional[Dict[str, str]] = None


def load_bucket_table(force: bool = False) -> RentTable:
    """Load rent_m2.csv into a nested mapping."""
    global _table, _table_mtime
    if not CSV_PATH.exists():
        _table, _table_mtime = {}, None
        return {}

    mtime = CSV_PATH.stat().st_mtime
    if not force and _table and _table_mtime == mtime:
        return _table

    table: RentTable = {}
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


def get_geojson_metadata(city: str) -> Optional[dict]:
    info = get_geojson_info(city)
    if info and os.path.exists(info.get("path", "")):
        return info
    return None


def _load_oslo_postnr_exact(
    path: str = "data/static/lookup/postnr/oslo_postnr.csv",
) -> Dict[str, Tuple[Optional[str], Optional[str]]]:
    mapping: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
    if not os.path.exists(path):
        return mapping
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            postnr = (row.get("postnr") or row.get("postcode") or "").strip()
            bydel = (row.get("bydel") or row.get("bydelnavn") or "").strip() or None
            bucket = (row.get("bucket") or "").strip() or None
            if len(postnr) == 4 and postnr.isdigit():
                mapping[postnr] = (bydel, bucket)
    return mapping


def _load_oslo_prefix2(path: str = "data/static/lookup/postnr/oslo_prefix.csv") -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    if not os.path.exists(path):
        return mapping
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            prefix = (row.get("prefix2") or "").strip()
            bucket = (row.get("bucket") or "").strip()
            if len(prefix) == 2 and prefix.isdigit() and bucket:
                mapping[prefix] = bucket
    return mapping


def get_oslo_postnr_exact() -> Dict[str, Tuple[Optional[str], Optional[str]]]:
    global _oslo_postnr_exact_cache
    if _oslo_postnr_exact_cache is None:
        _oslo_postnr_exact_cache = _load_oslo_postnr_exact()
    return _oslo_postnr_exact_cache


def get_oslo_prefix2() -> Dict[str, str]:
    global _oslo_prefix2_cache
    if _oslo_prefix2_cache is None:
        _oslo_prefix2_cache = _load_oslo_prefix2()
    return _oslo_prefix2_cache


def fetch_ssb_city_value(city_name: str, segment: str) -> Optional[float]:
    try:
        value = get_city_m2_month(city_name=city_name, segment=segment, year=None)
    except Exception:
        value = None
    return float(value) if value is not None else None


def load_bucket_ratios() -> Dict[Tuple[str, str, str], float]:
    table = load_bucket_table()
    ratios: Dict[Tuple[str, str, str], float] = {}
    for city, buckets in table.items():
        city_lower = city.lower()
        for bucket, segments in buckets.items():
            for segment, (kr_per_m2, _updated) in segments.items():
                city_snitt_key = f"{city} snitt"
                if bucket == city_snitt_key:
                    continue
                base = table.get(city, {}).get(city_snitt_key, {}).get(segment)
                if not base:
                    continue
                try:
                    base_value = float(base[0])
                    ratios[(city_lower, bucket, segment)] = float(kr_per_m2) / base_value
                except Exception:
                    continue
    return ratios
