"""Utility funksjoner for å finne relevante prospekt-PDF-lenker."""
from __future__ import annotations

import re
from typing import List, Tuple

from bs4 import BeautifulSoup
from bs4.element import Tag

from .fetch_helpers import attr_to_str, absolute_url

POS_STRONG = [
    "salgsoppgave",
    "komplett salgsoppgave",
    "prospekt",
    "salgsprospekt",
    "salgspresentasjon",
    "for utskrift",
    "utskrift",
    "digitalformat",
    "last ned pdf",
    "se pdf",
]
POS_WEAK = ["pdf"]

NEG_ALWAYS = [
    "tilstandsrapport",
    "boligsalgsrapport",
    "byggteknisk",
    "fidens",
    "egenerkl",
    "egenerklæring",
    "energiattest",
    "epc",
    "nabolag",
    "nabolagsprofil",
    "nordvikunders",
    "anticimex",
    "boligkjøperforsikring",
    "prisliste",
    "/files/doc/",
    "garanti.no/files/doc",
    "contentassets/nabolaget",
    "budskjema",
    "samtykke",
    "planinfo",
    "tegning",
    "seksjon",
    "kart",
    "situasjonsplan",
    "kommunal",
    "gebyr",
    "avgift",
    "skatt",
]


def score_pdf_link_for_prospect(href: str, text: str) -> int:
    lo = (href + " " + text).lower()
    sc = 0
    if ".pdf" in lo:
        sc += 8
    for w in POS_STRONG:
        if w in lo:
            sc += 10
    for w in POS_WEAK:
        if w in lo:
            sc += 2
    for w in NEG_ALWAYS:
        if w in lo:
            sc -= 30
    return sc


def gather_candidate_links(soup: BeautifulSoup, base_url: str) -> List[tuple[int, str, str]]:
    out: List[tuple[int, str, str]] = []

    if hasattr(soup, "find_all"):
        for a in soup.find_all("a"):
            if not isinstance(a, Tag):
                continue
            text = a.get_text(" ", strip=True) or ""
            for attr in ("href", "data-href", "data-file", "download"):
                href_val = attr_to_str(a.get(attr))
                if not href_val:
                    continue
                absu = absolute_url(base_url, href_val)
                if not absu:
                    continue
                sc = score_pdf_link_for_prospect(absu, text)
                if sc > 0:
                    out.append((sc, absu, text))

        for el in soup.find_all(["button", "div", "span"]):
            if not isinstance(el, Tag):
                continue
            text = el.get_text(" ", strip=True) or ""
            for attr in ("data-href", "data-file", "data-url", "data-download"):
                href_val = attr_to_str(el.get(attr))
                if not href_val:
                    continue
                absu = absolute_url(base_url, href_val)
                if not absu:
                    continue
                sc = score_pdf_link_for_prospect(absu, text)
                if sc > 0:
                    out.append((sc, absu, text))

    return out


def extract_pdf_urls_from_html(html_text: str, base_url: str) -> List[tuple[int, str]]:
    if not html_text:
        return []
    raw_hits: set[str] = set()
    for m in re.finditer(r"https?:\/\/[^\s\"'<>]+\.pdf\b", html_text, flags=re.I):
        raw_hits.add(m.group(0))
    for m in re.finditer(r"(?<!:)\/\/[^\s\"'<>]+\.pdf\b", html_text, flags=re.I):
        raw_hits.add(m.group(0))
    for m in re.finditer(r"(?<![a-zA-Z0-9])\/[^\s\"'<>]+\.pdf\b", html_text, flags=re.I):
        raw_hits.add(m.group(0))

    def _score(url: str) -> int:
        lo = url.lower()
        score = 0
        if "salgsoppgav" in lo or "prospekt" in lo or "salgsprospekt" in lo:
            score += 50
        if ".pdf" in lo:
            score += 10
        if any(x in lo for x in NEG_ALWAYS):
            score -= 100
        return score

    out: List[tuple[int, str]] = []
    for hit in raw_hits:
        absu = absolute_url(base_url, hit)
        if not absu:
            continue
        sc = _score(absu)
        if sc > 0:
            out.append((sc, absu))
    return out


__all__ = [
    "POS_STRONG",
    "POS_WEAK",
    "NEG_ALWAYS",
    "score_pdf_link_for_prospect",
    "gather_candidate_links",
    "extract_pdf_urls_from_html",
]
