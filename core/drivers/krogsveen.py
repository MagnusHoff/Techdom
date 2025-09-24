# core/drivers/krogsveen.py
from __future__ import annotations

import re
import io
from typing import Tuple, Dict, Any, Optional
import requests
from bs4 import BeautifulSoup
from bs4.element import Tag
from PyPDF2 import PdfReader

from core.http_headers import BROWSER_HEADERS
from ..config import SETTINGS


PDF_MAGIC = b"%PDF-"
MIN_BYTES = 200_000  # Tilstandsrapporter ligger typisk > 200 KB


def _looks_like_pdf(b: bytes | None) -> bool:
    if not b:
        return False
    if len(b) < MIN_BYTES:
        # Some viewer pages return tiny HTML or thumbnails — reject
        if not b.startswith(PDF_MAGIC):
            return False
    # Quick structural check
    return b.startswith(PDF_MAGIC)


def _pdf_pages(b: bytes | None) -> int:
    if not b:
        return 0
    try:
        return len(PdfReader(io.BytesIO(b)).pages)
    except Exception:
        return 0


class KrogsveenDriver:
    name = "krogsveen"

    def matches(self, url: str) -> bool:
        u = (url or "").lower()
        # Matches listing details page
        return "krogsveen.no" in u and ("/kjope/" in u or "/boliger-til-salgs" in u)

    def _abs(self, base: str, href: str | None) -> str | None:
        if not href:
            return None
        try:
            from urllib.parse import urljoin

            return urljoin(base, href)
        except Exception:
            return None

    def _find_tilstands_links(self, html: str, base_url: str) -> list[str]:
        out: list[str] = []
        soup = BeautifulSoup(html or "", "html.parser")

        # 1) Anchor/button with text like "Tilstandsrapport"
        for el in soup.find_all(["a", "button"]):
            if not isinstance(el, Tag):
                continue
            text = (el.get_text(" ", strip=True) or "").lower()
            if "tilstandsrapport" in text:
                for attr in ("href", "data-href", "data-url"):
                    href = el.get(attr)
                    if href:
                        u = self._abs(base_url, str(href))
                        if u:
                            out.append(u)

        # 2) Sanity CDN direct links found anywhere in HTML
        for m in re.finditer(
            r"https?://cdn\.sanity\.io/files/[^\s\"']+\.pdf", html or "", flags=re.I
        ):
            out.append(m.group(0))

        # Keep order, de-dup
        seen: set[str] = set()
        uniq: list[str] = []
        for u in out:
            if u not in seen:
                uniq.append(u)
                seen.add(u)
        return uniq

    def _download_pdf(
        self, sess: requests.Session, url: str, referer: str
    ) -> tuple[bytes | None, dict]:
        headers = {
            **BROWSER_HEADERS,
            "Accept": "application/pdf,application/octet-stream,*/*",
            "Referer": referer,
        }
        # Try to set Origin sensibly
        try:
            from urllib.parse import urlparse

            pr = urlparse(referer)
            headers["Origin"] = f"{pr.scheme}://{pr.netloc}"
        except Exception:
            pass

        dbg_dl: Dict[str, Any] = {"url": url}
        r = sess.get(
            url, headers=headers, timeout=SETTINGS.REQ_TIMEOUT, allow_redirects=True
        )
        dbg_dl["status"] = r.status_code
        dbg_dl["final_url"] = str(r.url)
        dbg_dl["ct"] = r.headers.get("Content-Type")
        dbg_dl["len"] = len(r.content or b"")
        if r.ok and (
            ("pdf" in (r.headers.get("Content-Type", "").lower()))
            or r.content.startswith(PDF_MAGIC)
        ):
            return r.content, dbg_dl
        return None, dbg_dl

    def try_fetch(
        self, sess: requests.Session, page_url: str
    ) -> Tuple[bytes | None, str | None, dict]:
        dbg: Dict[str, Any] = {"driver": self.name, "step": "start", "meta": {}}

        # 1) Load listing page (Krogsveen blocks some bots; send decent headers)
        try:
            r = sess.get(
                page_url,
                headers={**BROWSER_HEADERS, "Referer": "https://www.finn.no/"},
                timeout=SETTINGS.REQ_TIMEOUT,
                allow_redirects=True,
            )
            r.raise_for_status()
            html = r.text or ""
            dbg["meta"]["page_status"] = r.status_code
            dbg["meta"]["page_len"] = len(html)
        except Exception as e:
            dbg["step"] = f"page_fetch_error:{type(e).__name__}"
            dbg["error"] = repr(e)
            return None, None, dbg

        # 2) Find candidate links to Tilstandsrapport or direct Sanity PDF
        cands = self._find_tilstands_links(html, str(r.url))
        dbg["meta"]["candidates"] = cands[:5]  # short preview

        # Some sites link to a viewer page first; visit non-PDF links once and
        # scrape again for a Sanity PDF inside.
        def _expand_once(u: str) -> list[str]:
            if u.lower().endswith(".pdf"):
                return [u]
            try:
                rr = sess.get(
                    u,
                    headers={**BROWSER_HEADERS, "Referer": page_url},
                    timeout=SETTINGS.REQ_TIMEOUT,
                    allow_redirects=True,
                )
                if rr.ok:
                    inner = self._find_tilstands_links(rr.text or "", str(rr.url))
                    return inner or [u]
            except Exception:
                pass
            return [u]

        expanded: list[str] = []
        for u in cands:
            expanded.extend(_expand_once(u))
        # De-dup again, keep order
        seen: set[str] = set()
        cand_pdf: list[str] = []
        for u in expanded:
            if u not in seen:
                seen.add(u)
                cand_pdf.append(u)
        dbg["meta"]["expanded"] = cand_pdf[:5]

        # 3) Try to download a real PDF
        for u in cand_pdf:
            b, d_dl = self._download_pdf(sess, u, referer=page_url)
            dbg.setdefault("downloads", []).append(d_dl)
            if b and _looks_like_pdf(b) and _pdf_pages(b) >= 2:
                dbg["step"] = "ok_tilstandsrapport"
                dbg.setdefault("meta", {})["is_tilstandsrapport"] = True
                return b, u, dbg

        dbg["step"] = "no_pdf_confirmed"
        return None, None, dbg
