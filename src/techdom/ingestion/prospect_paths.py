"""Path helpers for cached prospekt og failcase-data."""
from __future__ import annotations

import os
from pathlib import Path

CACHE_DIR = Path("data/cache")
PROSPEKT_DIR = CACHE_DIR / "prospekt"
FAIL_DIR = Path("data/debug/failcases")

for directory in (PROSPEKT_DIR, FAIL_DIR):
    directory.mkdir(parents=True, exist_ok=True)

LOCAL_MIRROR = os.getenv("TD_LOCAL_MIRROR", "1") not in {"0", "false", "False"}

__all__ = ["CACHE_DIR", "PROSPEKT_DIR", "FAIL_DIR", "LOCAL_MIRROR"]
