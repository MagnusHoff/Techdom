# core/http_headers.py
"""
Standard HTTP-headere for scraping.
Bruker alltid USER_AGENT fra config, så alt styres via .env / config.py.
"""

from techdom.infrastructure.config import SETTINGS

DEFAULT_HEADERS = {
    "User-Agent": SETTINGS.USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "nb-NO,nb;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# Praktisk alias så resten av koden kan bruke samme navn
BROWSER_HEADERS = DEFAULT_HEADERS
