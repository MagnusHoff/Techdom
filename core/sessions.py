from __future__ import annotations
import requests
from .config import SETTINGS

BASE_HEADERS = {
    "User-Agent": SETTINGS.USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "nb-NO,nb;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


def new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(BASE_HEADERS)
    s.max_redirects = 10
    return s
