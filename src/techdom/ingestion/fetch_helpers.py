"""Utility helpers used by ingestion.fetch."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse, parse_qs, urlunparse


def attr_to_str(val: Any) -> str | None:
    if val is None:
        return None
    try:
        if isinstance(val, (list, tuple)) and val:
            val = val[0]
        s = str(val).strip()
        return s or None
    except Exception:
        return None


def absolute_url(base_url: str, href: Any) -> str | None:
    if not href:
        return None
    try:
        return urljoin(base_url, str(href))
    except Exception:
        return None


def clean_url(u: str) -> str:
    try:
        u = u.replace("\/", "/")
        p = urlparse(u)
        q = parse_qs(p.query)
        drop = {k for k in q if k.startswith("utm_") or k in {"gclid", "fbclid"}}
        kept = [(k, v) for k, v in q.items() if k not in drop]
        query = "&".join(f"{k}={v[0]}" for k, v in kept if v)
        return urlunparse((p.scheme, p.netloc, p.path, p.params, query, ""))
    except Exception:
        return u


def normalize(s: str | None) -> str:
    return (s or "").lower().strip()


def sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def sha256_file(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


__all__ = [
    "attr_to_str",
    "absolute_url",
    "clean_url",
    "normalize",
    "sha256_bytes",
    "sha256_file",
]
