"""Shared helpers for driver modules.

The goal is to centralise the small utilities that every driver ended up
re-implementing (string coercion, absolute URLs, PDF heuristics, etc.) so we
avoid copy/paste bugs and can tweak request headers in one place.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

import requests

from techdom.ingestion.fetch_helpers import (
    PDF_MAGIC,
    absolute_url,
    attr_to_str,
    looks_like_pdf,
    origin_from_url,
    pdf_get,
    pdf_head,
)


def as_str(value: Any) -> str:
    """Convert BeautifulSoup attribute values to a safe string."""

    text = attr_to_str(value)
    if text is None:
        return ""
    return text


def abs_url(base_url: str, href: Any) -> str | None:
    """Return an absolute URL or ``None`` when ``href`` is not usable."""

    return absolute_url(base_url, href)


def origin(url: str | None) -> str:
    """Return ``scheme://host`` for ``url`` or an empty string on failure."""

    return origin_from_url(url)


def looks_like_pdf_bytes(blob: bytes | bytearray | None) -> bool:
    """Cheap PDF check used by drivers when validating downloads."""

    return looks_like_pdf(blob)


def request_pdf(
    sess: requests.Session,
    url: str,
    referer: str | None,
    timeout: int,
    *,
    method: str = "get",
    extra_headers: Optional[Mapping[str, str]] = None,
    allow_redirects: bool = True,
) -> requests.Response:
    """Perform a GET/HEAD request with consistent PDF-friendly headers."""

    method_lower = method.lower()
    ref = referer or url

    if method_lower == "head":
        return pdf_head(
            sess,
            url,
            ref,
            timeout,
            extra_headers=extra_headers,
            allow_redirects=allow_redirects,
        )

    if method_lower == "get":
        return pdf_get(
            sess,
            url,
            ref,
            timeout,
            extra_headers=extra_headers,
            allow_redirects=allow_redirects,
        )

    raise ValueError(f"Unsupported method for request_pdf: {method!r}")


__all__ = [
    "PDF_MAGIC",
    "abs_url",
    "as_str",
    "looks_like_pdf_bytes",
    "origin",
    "request_pdf",
]
