"""Utility helpers used by ingestion.fetch and driver modules."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional
from urllib.parse import urljoin, urlparse, parse_qs, urlunparse

import requests

from techdom.ingestion.http_headers import BROWSER_HEADERS

PDF_MAGIC = b"%PDF-"


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
        u = u.replace("\\/", "/")
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


def looks_like_pdf(data: bytes | bytearray | None) -> bool:
    return isinstance(data, (bytes, bytearray)) and data.startswith(PDF_MAGIC)


def origin_from_url(url: str | None) -> str:
    if not url:
        return ""
    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}" if p.scheme and p.netloc else ""
    except Exception:
        return ""


def _pdf_headers(
    referer: str | None,
    url: str | None,
    extra: Optional[Mapping[str, str]] = None,
) -> dict[str, str]:
    headers = dict(BROWSER_HEADERS)
    headers["Accept"] = "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8"
    if referer:
        headers["Referer"] = referer
        origin = origin_from_url(referer) or origin_from_url(url)
        if origin:
            headers["Origin"] = origin
    if extra:
        headers.update(extra)
    return headers


def pdf_get(
    sess: requests.Session,
    url: str,
    referer: str,
    timeout: int,
    *,
    extra_headers: Optional[Mapping[str, str]] = None,
    allow_redirects: bool = True,
) -> requests.Response:
    headers = _pdf_headers(referer, url, extra_headers)
    return sess.get(url, headers=headers, timeout=timeout, allow_redirects=allow_redirects)


def pdf_head(
    sess: requests.Session,
    url: str,
    referer: str,
    timeout: int,
    *,
    extra_headers: Optional[Mapping[str, str]] = None,
    allow_redirects: bool = True,
) -> requests.Response:
    headers = _pdf_headers(referer, url, extra_headers)
    return sess.head(url, headers=headers, timeout=timeout, allow_redirects=allow_redirects)


__all__ = [
    "PDF_MAGIC",
    "attr_to_str",
    "absolute_url",
    "clean_url",
    "normalize",
    "sha256_bytes",
    "sha256_file",
    "looks_like_pdf",
    "origin_from_url",
    "pdf_get",
    "pdf_head",
]
