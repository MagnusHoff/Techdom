# scripts/build_rent_csv_from_ssb.py
from __future__ import annotations

import bootstrap  # noqa: F401

import argparse
import csv
from pathlib import Path
from typing import Dict, Tuple

from techdom.integrations.ssb import list_soner2, get_segment_m2_by_soner2

# Map SSB Soner2 labels -> (city, bucket) i din CSV
S2_TO_CITY_BUCKET = {
    "Hele landet": ("Norge", "Norge snitt"),
    "Oslo og Bærum kommune": ("Oslo", "Oslo snitt"),
    "Akershus utenom Bærum kommune": ("Akershus", "Akershus snitt"),
    "Bergen kommune": ("Bergen", "Bergen snitt"),
    "Trondheim kommune": ("Trondheim", "Trondheim snitt"),
    "Stavanger kommune": ("Stavanger", "Stavanger snitt"),
}

APP_SEGMENTS = ["hybel", "liten", "standard", "stor"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2024, help="SSB År (e.g. 2024)")
    ap.add_argument("--out", type=Path, default=Path("data/processed/rent_m2.csv"))
    ap.add_argument("--rom-code", default="1", help="Rom-kode (default '1' ~ hybel)")
    ap.add_argument("--updated", default="Q? 2024", help="Tekst i 'updated'-kolonnen")
    args = ap.parse_args()

    print("Henter Soner2 (dummy fra techdom.integrations.ssb) …")
    m2_by_soner: Dict[str, Tuple[str, float]] = get_segment_m2_by_soner2(
        year=args.year, rom_code=args.rom_code
    )
    # m2_by_soner: { "00": ("Hele landet", 330.1), ... }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["city", "bucket", "segment", "kr_per_m2", "updated"])

        for _code, (label, kr_m2_month) in m2_by_soner.items():
            if label not in S2_TO_CITY_BUCKET:
                continue
            city, bucket = S2_TO_CITY_BUCKET[label]
            for seg in APP_SEGMENTS:
                # foreløpig: samme tall på alle segment (kan raffineres senere)
                w.writerow([city, bucket, seg, f"{kr_m2_month:.2f}", args.updated])

    print(f"Skrev {args.out} ✔")


if __name__ == "__main__":
    main()
