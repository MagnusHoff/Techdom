from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    USER_AGENT: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/127.0.0.0 Safari/537.36"
    )
    REQ_TIMEOUT: int = 25
    PLAYWRIGHT_TIMEOUT: int = 25000


SETTINGS = Settings()
