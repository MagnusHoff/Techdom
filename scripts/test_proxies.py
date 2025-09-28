# scripts/test_proxies.py
from __future__ import annotations

import csv
import time
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import requests

from core.s3_upload import upload_good_proxies

# ---- konfig ----
ROOT = Path(__file__).resolve().parent.parent

ALL_PROXIES_FILE = ROOT / "data" / "proxy" / "proxies.txt"
GOOD_PROXIES_FILE = ROOT / "data" / "proxy" / "good_proxies.txt"
RESULTS_CSV = ROOT / "data" / "proxy" / "proxy_results.csv"

TEST_URL = "https://httpbin.org/ip"  # evt: "https://api.ipify.org?format=json"
TIMEOUT = 8
WORKERS = 50
# -----------------


def _to_url(line: str) -> str | None:
    """
    Tar en linje fra lista og returnerer 'http://user:pass@ip:port'.
    Støtter både ferdig URL og 'ip:port:user:pass'.
    """
    line = line.strip()
    if not line:
        return None
    if line.startswith("http://") or line.startswith("https://"):
        return line
    parts = line.split(":")
    if len(parts) == 4:
        ip, port, user, pwd = parts
        return f"http://{user}:{pwd}@{ip}:{port}"
    return None


def _check_proxy(url: str) -> tuple[str, bool, float | None, str | None]:
    """
    Returnerer (proxy_url, ok, latency_s, error_msg)
    """
    proxies = {"http": url, "https": url}
    t0 = time.perf_counter()
    try:
        # korte timeouts + ingen DNS hang
        socket.setdefaulttimeout(TIMEOUT)
        r = requests.get(TEST_URL, proxies=proxies, timeout=TIMEOUT)
        ok = r.ok
        dt = time.perf_counter() - t0
        return url, ok, dt if ok else None, None if ok else f"HTTP {r.status_code}"
    except Exception as e:
        return url, False, None, str(e)


def main():
    # sørg for at mappa finnes
    ALL_PROXIES_FILE.parent.mkdir(parents=True, exist_ok=True)

    if not ALL_PROXIES_FILE.exists():
        raise FileNotFoundError(
            f"Fant ikke {ALL_PROXIES_FILE}. Legg inn proxy-lista di der."
        )

    raw = [
        ln.strip()
        for ln in ALL_PROXIES_FILE.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    urls = [_to_url(ln) for ln in raw]
    urls = [u for u in urls if u]

    print(
        f"Tester {len(urls)} proxier → {TEST_URL}  (timeout={TIMEOUT}s, workers={WORKERS})"
    )

    results: list[tuple[str, bool, float | None, str | None]] = []

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(_check_proxy, u) for u in urls]
        for fut in as_completed(futs):
            results.append(fut.result())

    # skriv CSV
    RESULTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["proxy_url", "ok", "latency_s", "error"])
        for row in results:
            w.writerow(row)

    # skriv good_proxies.txt (samme format som sessions forventer: http://user:pass@ip:port)
    good = [url for (url, ok, _, _) in results if ok]
    GOOD_PROXIES_FILE.write_text(
        "\n".join(good) + ("\n" if good else ""), encoding="utf-8"
    )

    print(f"✅ {len(good)} OK av {len(results)} — lagret til {GOOD_PROXIES_FILE}")
    print(f"🧾 CSV: {RESULTS_CSV}")

    # last opp til S3
    upload_good_proxies(GOOD_PROXIES_FILE)


if __name__ == "__main__":
    main()
