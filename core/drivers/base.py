from __future__ import annotations
from typing import Protocol, Optional
import requests


class Driver(Protocol):
    name: str

    def matches(self, url: str) -> bool: ...
    def try_fetch(
        self, sess: requests.Session, page_url: str
    ) -> tuple[bytes | None, str | None, dict]: ...
