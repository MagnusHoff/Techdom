from __future__ import annotations

import os
from typing import List, Type

from .base import Driver  # gir typesikkerhet på matches/try_fetch

DRIVERS: List[Driver] = []

# Sett miljøvariabel CORE_DRIVERS_DEBUG=1 for å få synlige importfeil i konsollen.
DEBUG_IMPORT = os.getenv("CORE_DRIVERS_DEBUG", "0") not in (
    "",
    "0",
    "false",
    "False",
    "FALSE",
)


def _safe_add(module_name: str, class_name: str) -> None:
    """Importerer og registrerer en driver hvis den finnes."""
    try:
        try:
            mod = __import__(
                f"techdom.ingestion.drivers.{module_name}", fromlist=[class_name]
            )
        except ModuleNotFoundError as err:
            if err.name == f"techdom.ingestion.drivers.{module_name}":
                if DEBUG_IMPORT:
                    print(f"[drivers] {module_name}: module not found")
                return
            raise

        cls: Type[Driver] | None = getattr(mod, class_name, None)  # type: ignore[assignment]
        if cls is None:
            if DEBUG_IMPORT:
                print(f"[drivers] {module_name}.{class_name}: not found")
            return
        DRIVERS.append(cls())  # type: ignore[misc]
    except Exception as e:
        if DEBUG_IMPORT:
            print(f"[drivers] skip {module_name}.{class_name}: {type(e).__name__}: {e}")
        # Stilletiende i prod
        return



# ---- Spesifikke drivere (rekkefølgen = prioritet) ----
_safe_add("dnbeiendom", "DnbEiendomDriver")
_safe_add("proaktiv", "ProaktivDriver")
_safe_add("privatmegleren", "PrivatMeglerenDriver")
_safe_add("aktiv", "AktivDriver")
_safe_add("eie", "EieDriver")
_safe_add("em1", "Em1Driver")
_safe_add("ask", "AskDriver")
_safe_add("krogsveen", "KrogsveenDriver")
_safe_add("notar", "NotarDriver")
_safe_add("obosmegleren", "ObosMeglerenDriver")
_safe_add("partners", "PartnersDriver")
_safe_add("nordvik", "NordvikDriver")
_safe_add("heimdal", "HeimdalDriver")
_safe_add("garanti", "GarantiDriver")
_safe_add("kalandpartners", "KalandPartnersDriver")
_safe_add("rele", "ReleDriver")
_safe_add("semjohnsen", "SemJohnsenDriver")
_safe_add("sormegleren", "SorMeglerenDriver")
_safe_add("boa", "BoaDriver")
_safe_add("exbo", "ExboDriver")

# ---- Fallback (alltid sist) ----
_safe_add("generic_local", "GenericLocalDriver")
