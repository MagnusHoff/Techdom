# core/geo.py
from __future__ import annotations
import json
import os
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

# NB: GeoJSON bruker [lon, lat] i koordinatene.
Point = Tuple[float, float]  # (lat, lon)


# -------------------------------
# Lasting m/cache
# -------------------------------


@lru_cache(maxsize=16)
def load_geojson(path: str) -> Dict[str, Any]:
    """
    Leser en GeoJSON-fil fra disk og cacher resultatet i minnet.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"GeoJSON ikke funnet: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# -------------------------------
# Geometri – point in polygon
# -------------------------------


def _point_in_ring(lat: float, lon: float, ring: List[List[float]]) -> bool:
    """
    Ray-casting test mot en ring (ytre eller indre). Ring er en liste av [lon, lat].
    Returnerer True hvis punktet er inne i ringen.
    """
    x = lon
    y = lat
    inside = False
    n = len(ring)
    if n < 3:
        return False

    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        # Sjekk om horisontal ray krysser segmentet
        if (y1 > y) != (y2 > y):
            # unngå deling på 0
            denom = (y2 - y1) if (y2 - y1) != 0 else 1e-12
            x_intersect = (x2 - x1) * (y - y1) / denom + x1
            if x < x_intersect:
                inside = not inside
    return inside


def _point_in_polygon(lat: float, lon: float, coords: List) -> bool:
    """
    Støtter både Polygon og MultiPolygon.
    Tar hensyn til hull (innerringer) for Polygon.
    """
    # MultiPolygon: liste av polygoner, hver polygon: [outer, hole1, hole2, ...]
    if coords and isinstance(coords[0][0][0], list):
        for poly in coords:
            outer = poly[0]
            if not _point_in_ring(lat, lon, outer):
                continue
            # inne i outer – sjekk hull
            holes = poly[1:] if len(poly) > 1 else []
            for hole in holes:
                if _point_in_ring(lat, lon, hole):
                    # Treffer hull – regnes som utenfor
                    break
            else:
                return True
        return False

    # Polygon: [outer, hole1, hole2, ...]
    outer = coords[0]
    if not _point_in_ring(lat, lon, outer):
        return False
    holes = coords[1:] if len(coords) > 1 else []
    for hole in holes:
        if _point_in_ring(lat, lon, hole):
            return False
    return True


# -------------------------------
# API
# -------------------------------


def find_bucket_from_point(
    lat: float,
    lon: float,
    geojson_path: str,
    name_key: str = "name",
) -> Optional[str]:
    """
    Returnerer navnet/bucket'en (fra properties[name_key]) til første feature
    i geojson som inneholder punktet (lat, lon). Returnerer None hvis ingen treffer.
    """
    gj = load_geojson(geojson_path)
    feats = gj.get("features") or []
    for feat in feats:
        props = feat.get("properties") or {}
        geom = feat.get("geometry") or {}
        if not geom:
            continue
        gtype = (geom.get("type") or "").lower()
        if gtype not in ("polygon", "multipolygon"):
            continue
        coords = geom.get("coordinates") or []
        try:
            if _point_in_polygon(lat, lon, coords):
                name = props.get(name_key)
                return str(name).strip() if name else None
        except Exception:
            # Tåler små feil i enkeltfeatures
            continue
    return None
