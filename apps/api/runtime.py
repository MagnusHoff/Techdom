from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path
from types import ModuleType


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def ensure_bootstrap() -> ModuleType:
    try:
        return import_module("bootstrap")
    except ModuleNotFoundError:
        root = _project_root()
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        return import_module("bootstrap")


def load_environment() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:  # pragma: no cover
        return
    load_dotenv(_project_root() / ".env")


__all__ = ["ensure_bootstrap", "load_environment"]
