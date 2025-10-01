# core/drivers/base.py
from __future__ import annotations

from typing import Tuple, Dict, Any
import requests
from abc import ABC, abstractmethod


class Driver(ABC):
    """
    Abstrakt baseklasse for alle meglere.
    Alle driver-klasser må implementere matches() og try_fetch().
    """

    # Kjent navn (brukes i logging/debug)
    name: str = "base"

    @abstractmethod
    def matches(self, url: str) -> bool:
        """
        Returner True hvis driveren støtter gitt URL.
        Eksempel: "aktiv.no" i url.
        """
        raise NotImplementedError

    @abstractmethod
    def try_fetch(
        self, sess: requests.Session, page_url: str
    ) -> Tuple[bytes | None, str | None, Dict[str, Any]]:
        """
        Forsøk å hente en prospekt-PDF fra megler-siden.

        Args:
            sess: requests.Session med riktige headers/proxy/timeouts
            page_url: URL til meglers annonse-side

        Returns:
            (pdf_bytes, final_url, debug_dict)
            pdf_bytes: innholdet i PDF, eller None ved feil
            final_url: endelig URL som ble brukt, eller None
            debug_dict: metadata for debugging (hvilke steg, statuskoder, etc.)
        """
        raise NotImplementedError
