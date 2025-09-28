# core/config.py
from __future__ import annotations
from dataclasses import dataclass
import os


def _env_bool(name: str, default: bool) -> bool:
    """Les boolsk miljøvariabel på en tolerant måte."""
    val = os.getenv(name)
    if val is None:
        return default
    return str(val).strip().lower() not in {"0", "false", "no", "off", ""}


@dataclass(frozen=True)
class Settings:
    # Bruk miljøvariabel hvis den finnes, ellers standard
    USER_AGENT: str = os.getenv(
        "USER_AGENT",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/127.0.0.0 Safari/537.36",
    )

    REQ_TIMEOUT: int = int(os.getenv("REQ_TIMEOUT", "25"))
    PLAYWRIGHT_TIMEOUT: int = int(os.getenv("PLAYWRIGHT_TIMEOUT", "25000"))

    # Proxy kan settes via miljøvariabel: HTTP_PROXY (overstyrer random fra S3-lista)
    HTTP_PROXY: str | None = os.getenv("HTTP_PROXY", None)

    # Lokal speiling av prospekt-PDF (for dev/feilsøking).
    # Brukes i fetch.py; default = på. Sett TD_LOCAL_MIRROR=0 for å slå av.
    LOCAL_MIRROR: bool = _env_bool("TD_LOCAL_MIRROR", True)


SETTINGS = Settings()
