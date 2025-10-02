from __future__ import annotations

import os
import sys
from importlib import import_module
from pathlib import Path
from types import ModuleType


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def ensure_bootstrap() -> ModuleType:
    """Import `bootstrap`, inserting the project root on sys.path if needed."""
    try:
        return import_module("bootstrap")
    except ModuleNotFoundError:
        root = _project_root()
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        return import_module("bootstrap")


def load_environment() -> None:
    """Load environment variables from .env when python-dotenv is available."""
    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:  # pragma: no cover - optional dependency
        return
    load_dotenv()


def prepare_workdir(root: Path) -> None:
    """Ensure the process runs from the project root for relative assets."""
    os.chdir(root)


__all__ = ["ensure_bootstrap", "load_environment", "prepare_workdir"]
