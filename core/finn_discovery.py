# core/finn_discovery.py
from __future__ import annotations

from typing import Optional, List, Tuple, Any
from urllib.parse import urljoin, urlparse, urlunparse, parse_qs
import re
import json
import requests
from bs4 import BeautifulSoup, Tag

from .sessions import new_session
from .config import SETTINGS
from .http_headers import BROWSER_HEADERS  # ✅ bruk felles nettleser-headere


# ──────────────────────────────────────────────────────────────────────────────
#  Små helpers
# ──────────────────────────────────────────────────────────────────────────────
_NEEDLES = [
    "se komplett salgsoppgave",
    "komplett salgsoppgave",
    "salgsoppgave",
    "se salgsoppgave",
    "prospekt",
    "se prospekt",
    "last ned prospekt",
    "last ned salgsoppgave",
    "komplett",
]

_PDF_RX = re.compile(r"\.pdf(?:$|\?)", re.IGNORECASE)


def _as_str(v: Any) -> str:
    """Trygg konvertering av BS4-attributtverdi til str."""
    if isinstance(v, str):
        return v
    if isinstance(v, (list, tuple)) and v and isinstance(v[0], str):
        return v[0]
    return ""


def _clean_url(u: str) -> str:
    """Fjern trackingparametre og fragment for stabil lagring."""
    try:
        p = urlparse(u.replace("\\/", "/"))
        q = parse_qs(p.query)
        drop = {k for k in q if k.startswith("utm_") or k in {"gclid", "fbclid"}}
        kept = [(k, v) for k, v in q.items() if k not in drop]
        query = "&".join(f"{k}={v[0]}" for k, v in kept if v)
        return urlunparse((p.scheme, p.netloc, p.path, p.params, query, ""))
    except Exception:
        return u


def _abs(base: str, href: str | None) -> Optional[str]:
    if not href:
        return None
    try:
        return _clean_url(urljoin(base, href))
    except Exception:
        return None


def _best_candidate(cands: list[str]) -> Optional[str]:
    """Prioriter PDF-lenker først, ellers første gyldige."""
    if not cands:
        return None
    pdfs = [c for c in cands if _PDF_RX.search(c)]
    return (pdfs[0] if pdfs else cands[0]) if cands else None


# ──────────────────────────────────────────────────────────────────────────────
#  Henting
# ──────────────────────────────────────────────────────────────────────────────
def get_soup(
    sess: requests.Session, url: str, referer: str | None = None
) -> tuple[BeautifulSoup, str]:
    headers = dict(BROWSER_HEADERS)
    if referer:
        headers["Referer"] = referer
    r = sess.get(
        url, headers=headers, timeout=SETTINGS.REQ_TIMEOUT, allow_redirects=True
    )
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser"), r.text


# ──────────────────────────────────────────────────────────────────────────────
#  Heuristikker for å finne prospekt/salgsoppgave
# ──────────────────────────────────────────────────────────────────────────────
def _collect_link_candidates(base_url: str, soup: BeautifulSoup) -> list[str]:
    cands: list[str] = []

    # 1) Direkte <a>-elementer
    for a in soup.find_all("a"):
        if not isinstance(a, Tag):
            continue
        txt = (a.get_text(" ", strip=True) or "").lower()
        title = _as_str(a.get("title")).lower()
        aria = _as_str(a.get("aria-label")).lower()
        href = _abs(base_url, _as_str(a.get("href")))

        if href and any(n in txt or n in title or n in aria for n in _NEEDLES):
            cands.append(href)

        # PDF + hint i tekst
        if href and _PDF_RX.search(href) and (txt or title or aria):
            if any(n in (txt + " " + title + " " + aria) for n in _NEEDLES):
                cands.append(href)

    # 2) Buttons/divs som fungerer som lenker
    for btn in soup.find_all(["button", "div", "span"]):
        if not isinstance(btn, Tag):
            continue
        txt = (btn.get_text(" ", strip=True) or "").lower()
        aria = _as_str(btn.get("aria-label")).lower()
        dtid = _as_str(btn.get("data-testid")).lower()
        if not any(n in txt or n in aria or n in dtid for n in _NEEDLES):
            continue
        a = btn.find("a")
        if isinstance(a, Tag):
            href = _abs(base_url, _as_str(a.get("href")))
            if href:
                cands.append(href)

    # 3) Meta/ld+json
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            raw = tag.get_text()  # sikrere enn tag.string
        except Exception:
            raw = ""
        try:
            data = json.loads(raw or "{}")
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            for key in ("url", "mainEntityOfPage"):
                val = item.get(key)
                if isinstance(val, str) and _PDF_RX.search(val):
                    href = _abs(base_url, val)
                    if href:
                        cands.append(href)

    # 4) Hard fallback: alle <a> som peker til .pdf
    for a in soup.find_all("a"):
        if not isinstance(a, Tag):
            continue
        href = _abs(base_url, _as_str(a.get("href")))
        if href and _PDF_RX.search(href):
            cands.append(href)

    # Dedup – bevar rekkefølge
    seen: set[str] = set()
    uniq: list[str] = []
    for h in cands:
        if h and h not in seen:
            seen.add(h)
            uniq.append(h)
    return uniq


# ──────────────────────────────────────────────────────────────────────────────
#  Offentlig API
# ──────────────────────────────────────────────────────────────────────────────
def discover_megler_url(finn_url: str) -> tuple[str, str | None]:
    """
    Oppdag lenken til prospekt/salgsoppgave på FINN-siden.
    Returnerer (megler_url_eller_finn_url, html_text).
    Hvis ingen kandidat finnes, returneres (finn_url, html_text).
    """
    sess = new_session()
    try:
        soup, html = get_soup(sess, finn_url)
    except Exception:
        return finn_url, None

    candidates = _collect_link_candidates(finn_url, soup)
    choice = _best_candidate(candidates)

    if not choice:
        wants = [w.lower() for w in _NEEDLES]
        for a in soup.find_all("a"):
            if not isinstance(a, Tag):
                continue
            txt = (a.get_text(" ", strip=True) or "").lower()
            href = _abs(finn_url, _as_str(a.get("href")))
            if href and any(w in txt for w in wants):
                choice = href
                break

    return (choice or finn_url), html
