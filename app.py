"""Compatibility wrapper for running the Streamlit app via `streamlit run app.py`."""
from __future__ import annotations

import bootstrap  # noqa: F401
from apps.streamlit.main import main

__all__ = ["main"]

if __name__ == "__main__":
    main()
