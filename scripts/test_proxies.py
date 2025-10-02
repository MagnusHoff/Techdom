# scripts/test_proxies.py
from __future__ import annotations

import bootstrap  # noqa: F401

from pathlib import Path

from techdom.cli.test_proxies import main


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    main(
        all_proxies_file=root / "data/raw/proxy/proxies.txt",
        good_proxies_file=root / "data/raw/proxy/good_proxies.txt",
        results_csv=root / "data/raw/proxy/proxy_results.csv",
    )
