"""Legacy compatibility layer for pre-refactor imports."""
from __future__ import annotations

import importlib
import sys
from types import ModuleType
from typing import Dict

_MODULE_ALIASES: Dict[str, str] = {
    "analysis_contracts": "techdom.domain.analysis_contracts",
    "browser_fetch": "techdom.ingestion.browser_fetch",
    "compute": "techdom.processing.compute",
    "config": "techdom.infrastructure.config",
    "configs": "techdom.infrastructure.configs",
    "counters": "techdom.infrastructure.counters",
    "downloader": "techdom.ingestion.downloader",
    "fetch": "techdom.ingestion.fetch",
    "finn_discovery": "techdom.ingestion.finn_discovery",
    "geo": "techdom.domain.geo",
    "geo_registry": "techdom.domain.geo_registry",
    "history": "techdom.domain.history",
    "http_headers": "techdom.ingestion.http_headers",
    "rent": "techdom.processing.rent",
    "rent_csv": "techdom.processing.rent_csv",
    "rates": "techdom.processing.rates",
    "scrape": "techdom.ingestion.scrape",
    "sessions": "techdom.ingestion.sessions",
    "ai": "techdom.processing.ai",
    "pdf_utils": "techdom.processing.pdf_utils",
    "ssb": "techdom.integrations.ssb",
    "s3_upload": "techdom.integrations.s3_upload",
    "s3_prospekt_store": "techdom.integrations.s3_prospekt_store",
    "drivers": "techdom.ingestion.drivers",
}


class _LegacyModule(ModuleType):
    """Thin proxy that lazily loads the new module on first use."""

    def __init__(self, name: str, target_name: str) -> None:
        super().__init__(name)
        self.__dict__["_target_name"] = target_name
        self.__dict__["_loaded"] = False
        self.__dict__["_target_module"] = None

    def _load(self) -> ModuleType:
        if not self.__dict__["_loaded"]:
            target_module = importlib.import_module(self.__dict__["_target_name"])
            self.__dict__["_target_module"] = target_module
            self.__dict__.update(target_module.__dict__)
            self.__dict__["__package__"] = self.__name__
            if hasattr(target_module, "__path__"):
                self.__dict__["__path__"] = target_module.__path__  # type: ignore[attr-defined]
            self.__dict__["_loaded"] = True
        return self.__dict__["_target_module"]  # type: ignore[return-value]

    def __getattr__(self, item: str):
        module = self._load()
        return getattr(module, item)

    def __dir__(self):
        module = self._load()
        return sorted(set(dir(module)))


for _legacy_name, _target in _MODULE_ALIASES.items():
    full_name = f"core.{_legacy_name}"
    if full_name not in sys.modules:
        sys.modules[full_name] = _LegacyModule(full_name, _target)

__all__ = sorted(_MODULE_ALIASES.keys())


def __getattr__(name: str):
    if name in _MODULE_ALIASES:
        return sys.modules[f"core.{name}"]
    raise AttributeError(name)
