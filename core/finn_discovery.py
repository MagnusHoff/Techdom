from __future__ import annotations
from typing import Optional, Tuple, List
from urllib.parse import urljoin
import re
import requests
from bs4 import BeautifulSoup, Tag
from .sessions import new_session
from .config import SETTINGS


def get_soup(
    sess: requests.Session, url: str, referer: str | None = None
) -> tuple[BeautifulSoup, str]:
    headers = {}
    if referer:
        headers["Referer"] = referer
    r = sess.get(url, headers=headers, timeout=SETTINGS.REQ_TIMEOUT)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser"), r.text


def first_link_by_text(soup: BeautifulSoup, needles: List[str]) -> Optional[str]:
    wants = [n.lower() for n in needles]
    for a in soup.find_all("a"):
        if not isinstance(a, Tag):
            continue
        txt = (a.get_text(" ", strip=True) or "").lower()
        if any(w in txt for w in wants):
            href = a.get("href")
            if href:
                return href
    return None


def discover_megler_url(finn_url: str) -> tuple[str, str | None]:
    """
    Returnerer (megler_url eller finn_url, html_text)
    """
    sess = new_session()
    soup, html = get_soup(sess, finn_url)
    jump = first_link_by_text(
        soup,
        [
            "se komplett salgsoppgave",
            "salgsoppgave",
            "prospekt",
            "se prospekt",
            "komplett",
        ],
    )
    if jump and not jump.lower().startswith(("http://", "https://")):
        jump = urljoin(finn_url, jump)
    return (jump or finn_url), html
