"""Compatibility wrapper for uvicorn import paths."""
from __future__ import annotations

import bootstrap  # noqa: F401

from apps.api.main import *  # noqa: F401,F403 - re-export FastAPI app and helpers
