from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Dict, Tuple

from techdom.integrations.ssb import get_segment_m2_by_soner2

S2_TO_CITY_BUCKET = {
    "Hele landet": ("Norge", "Norge snitt"),
    "Oslo og Bærum kommune": ("Oslo", "Oslo snitt"),
    "Akershus utenom Bærum kommune": ("Akershus", "Akershus snitt"),
    "Bergen kommune": ("Bergen", "Bergen snitt"),
    "Trondheim kommune": ("Trondheim", "Trondheim snitt"),
    "Stavanger kommune": ("Stavanger", "Stavanger snitt"),
}

APP_SEGMENTS = ["hybel", "liten", "standard", "stor"]


def build_rent_csv(
    *,
    year: int,
    out_path: Path,
    rom_code: str,
    updated_label: str,
) -> Path:
    mapping: Dict[str, Tuple[str, float]] = get_segment_m2_by_soner2(
        year=year, rom_code=rom_code
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["city", "bucket", "segment", "kr_per_m2", "updated"])

        for _code, (label, kr_m2_month) in mapping.items():
            if label not in S2_TO_CITY_BUCKET:
                continue
            city, bucket = S2_TO_CITY_BUCKET[label]
            for segment in APP_SEGMENTS:
                writer.writerow([city, bucket, segment, f"{kr_m2_month:.2f}", updated_label])

    return out_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=2024, help="SSB-år (eks. 2024)")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/processed/rent_m2.csv"),
        help="Hvor rent CSV skal skrives",
    )
    parser.add_argument("--rom-code", default="1", help="Rom-kode (default '1' ~ hybel)")
    parser.add_argument("--updated", default="Q? 2024", help="Tekst for 'updated'-kolonnen")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> Path:
    args = parse_args(argv)
    out_path = build_rent_csv(
        year=args.year,
        out_path=args.out,
        rom_code=args.rom_code,
        updated_label=args.updated,
    )
    print(f"Skrev {out_path} ✔")
    return out_path


if __name__ == "__main__":
    main()
