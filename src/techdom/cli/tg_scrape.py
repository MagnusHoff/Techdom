from __future__ import annotations

import argparse
import json
from typing import Any

from techdom.processing.tg_extract import ExtractionError, extract_tg


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Trekk ut TG2/TG3-funn fra salgsoppgave og eventuell FINN-side."
    )
    parser.add_argument(
        "--source",
        required=True,
        help="URL eller filsti til salgsoppgaven (PDF eller HTML).",
    )
    parser.add_argument(
        "--finn",
        default=None,
        help="Valgfri FINN-URL som også skannes for TG-funn.",
    )
    parser.add_argument(
        "--json",
        dest="json_only",
        action="store_true",
        help="Skriv bare JSON-resultatet (hopper over Markdown).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = extract_tg(args.source, source_finn=args.finn)
    except ExtractionError as exc:
        print(f"Feil: {exc}")
        return 1

    if not args.json_only:
        print(result["markdown"])
        print()

    print(json.dumps(result["json"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
