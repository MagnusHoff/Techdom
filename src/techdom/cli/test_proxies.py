from __future__ import annotations

import csv
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import requests

from techdom.integrations.s3_upload import upload_good_proxies

TEST_URL = "https://httpbin.org/ip"
TIMEOUT = 8
WORKERS = 50


def _to_url(line: str) -> Optional[str]:
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


def _check_proxy(url: str) -> Tuple[str, bool, Optional[float], Optional[str]]:
    proxies = {"http": url, "https": url}
    start = time.perf_counter()
    try:
        socket.setdefaulttimeout(TIMEOUT)
        response = requests.get(TEST_URL, proxies=proxies, timeout=TIMEOUT)
        latency = time.perf_counter() - start
        if response.ok:
            return url, True, latency, None
        return url, False, None, f"HTTP {response.status_code}"
    except Exception as exc:
        return url, False, None, str(exc)


def load_proxy_urls(path: Path) -> List[str]:
    raw = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    urls = [_to_url(line) for line in raw]
    return [u for u in urls if u]


def write_results_csv(path: Path, rows: Iterable[Tuple[str, bool, Optional[float], Optional[str]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["proxy_url", "ok", "latency_s", "error"])
        for row in rows:
            writer.writerow(row)


def write_good_proxies(path: Path, urls: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(urls)
    if text:
        text += "\n"
    path.write_text(text, encoding="utf-8")


def main(
    *,
    all_proxies_file: Path,
    good_proxies_file: Path,
    results_csv: Path,
) -> Tuple[int, int]:
    if not all_proxies_file.exists():
        raise FileNotFoundError(
            f"Fant ikke {all_proxies_file}. Legg inn proxy-lista di der."
        )

    urls = load_proxy_urls(all_proxies_file)
    print(
        f"Tester {len(urls)} proxier → {TEST_URL}  (timeout={TIMEOUT}s, workers={WORKERS})"
    )

    results: List[Tuple[str, bool, Optional[float], Optional[str]]] = []

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = [executor.submit(_check_proxy, url) for url in urls]
        for future in as_completed(futures):
            results.append(future.result())

    write_results_csv(results_csv, results)

    good_urls = [url for (url, ok, _latency, _err) in results if ok]
    write_good_proxies(good_proxies_file, good_urls)

    print(f"✅ {len(good_urls)} OK av {len(results)} — lagret til {good_proxies_file}")
    print(f"🧾 CSV: {results_csv}")

    upload_good_proxies(good_proxies_file)
    return len(good_urls), len(results)


if __name__ == "__main__":
    project_root = Path(__file__).resolve().parents[3]
    main(
        all_proxies_file=project_root / "data/raw/proxy/proxies.txt",
        good_proxies_file=project_root / "data/raw/proxy/good_proxies.txt",
        results_csv=project_root / "data/raw/proxy/proxy_results.csv",
    )
