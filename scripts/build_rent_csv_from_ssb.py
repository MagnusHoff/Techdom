# scripts/build_rent_csv_from_ssb.py
from __future__ import annotations
import argparse
import csv
from pathlib import Path
from typing import Dict, Tuple

from core.ssb import list_soner2, get_segment_m2_by_soner2

# Map SSB Soner2 labels -> (city, bucket) used by your app's CSV
S2_TO_CITY_BUCKET = {
    "Hele landet": ("Norge", "Norge snitt"),
    "Oslo og Bærum kommune": ("Oslo", "Oslo snitt"),
    "Akershus utenom Bærum kommune": ("Akershus", "Akershus snitt"),
    "Bergen kommune": ("Bergen", "Bergen snitt"),
    "Trondheim kommune": ("Trondheim", "Trondheim snitt"),
    "Stavanger kommune": ("Stavanger", "Stavanger snitt"),
}

# How we map your app's segments to a single SSB pull (we’re using 1-rom skattning
# as the “hybel” proxy; you can extend later by querying rom_code="2","3", etc.)
APP_SEGMENTS = ["hybel", "liten", "standard", "stor"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2024, help="SSB År (e.g. 2024)")
    ap.add_argument("--out", type=Path, default=Path("data/rent_m2.csv"))
    ap.add_argument(
        "--rom-code", default="1", help="Rom-kode eller label (default '1')"
    )
    args = ap.parse_args()

    # 1) Pull m² per month for all Soner2 (using chosen room category)
    print("Henter Soner2 fra SSB …")
    m2_by_soner: Dict[str, Tuple[str, float]] = get_segment_m2_by_soner2(
        year=args.year, rom_code=args.rom_code
    )
    # m2_by_soner: { "00": ("Hele landet", 330.1), "04": ("Bergen kommune", 320.0), ... }

    # 2) Write your app’s CSV
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["city", "bucket", "segment", "kr_per_m2", "updated"])

        for _code, (label, kr_m2_month) in m2_by_soner.items():
            if label not in S2_TO_CITY_BUCKET:
                # skip zones you don’t map yet
                continue
            city, bucket = S2_TO_CITY_BUCKET[label]
            # For now, use same kr/m2 for all segments (we can refine later)
            for seg in APP_SEGMENTS:
                w.writerow([city, bucket, seg, f"{kr_m2_month:.2f}", f"Q? {args.year}"])

    print(f"Skrev {args.out} ✔")


if __name__ == "__main__":
    main()
