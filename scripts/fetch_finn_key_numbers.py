from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from techdom.ingestion.scrape import scrape_finn_key_numbers


def _build_finn_url(url: Optional[str], finnkode: Optional[str]) -> str:
    if url:
        return url
    assert finnkode is not None
    return f"https://www.finn.no/realestate/homes/ad.html?finnkode={finnkode}"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Hent nøkkeltall fra en FINN-boligannonse og skriv dem ut som JSON",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", help="Full FINN-URL til annonse")
    group.add_argument("--finnkode", help="FINN-kode for annonsen")
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON-indent (standard 2). Sett til 0 for kompakt output",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    url = _build_finn_url(args.url, args.finnkode)
    data = scrape_finn_key_numbers(url)
    indent = None if args.indent <= 0 else args.indent
    json_output = json.dumps(data, ensure_ascii=False, indent=indent)
    print(json_output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
