# core/geo_registry.py
from __future__ import annotations
from typing import Optional, Dict
import os

REGISTRY: Dict[str, dict] = {
    "bergen": {
        "path": os.path.join("data", "geo", "bergen_bydeler.geojson"),
        "name_key": "name",
    },
    "oslo": {
        "path": os.path.join("data", "geo", "oslo_bydeler.geojson"),
        "name_key": "name",
    },
    "trondheim": {
        "path": os.path.join("data", "geo", "trondheim_bydeler.geojson"),
        "name_key": "name",
    },
    "stavanger": {
        "path": os.path.join("data", "geo", "stavanger_bydeler.geojson"),
        "name_key": "name",
    },
    # flere byer etter hvert...
}


def get_geojson_info(city: str) -> Optional[dict]:
    c = (city or "").strip().lower()
    info = REGISTRY.get(c)
    if not info:
        return None
    if not os.path.exists(info["path"]):
        return None
    return info
