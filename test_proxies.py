#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_TEST_URL = "https://httpbin.org/ip"  # returnerer {"origin": "..."}
DEFAULT_TIMEOUT = 8
DEFAULT_WORKERS = 50
UA_POOL = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]


def _new_session(timeout: int) -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": random.choice(UA_POOL),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "nb-NO,nb;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
    )
    retry = Retry(
        total=1,  # vi gjør én intern retry på 5xx (parallell test håndterer resten)
        backoff_factor=0.2,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS"],
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=100, pool_maxsize=100)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    # pakker inn default-timeout via request wrapper
    original_request = s.request

    def request_with_timeout(method, url, **kwargs):
        if "timeout" not in kwargs:
            kwargs["timeout"] = timeout
        return original_request(method, url, **kwargs)

    s.request = request_with_timeout  # type: ignore[assignment]
    return s


def test_one(
    proxy_url: str, timeout: int, url: str
) -> Tuple[str, bool, Optional[float], str, Optional[str]]:
    """
    Returnerer: (proxy, ok, latency_s, public_ip, error)
    """
    proxy_url = proxy_url.strip()
    if not proxy_url:
        return proxy_url, False, None, None, "empty_line"

    # requests-proxies format
    proxies = {"http": proxy_url, "https": proxy_url}
    s = _new_session(timeout)
    t0 = time.perf_counter()
    try:
        r = s.get(url, proxies=proxies, timeout=timeout)
        elapsed = time.perf_counter() - t0
        if r.status_code != 200:
            return proxy_url, False, elapsed, None, f"bad_status:{r.status_code}"
        data = {}
        try:
            data = r.json()
        except Exception:
            pass
        # httpbin.org/ip -> {"origin":"x.x.x.x"} eller "x.x.x.x, y.y.y.y"
        origin = data.get("origin") if isinstance(data, dict) else None
        if isinstance(origin, str) and origin.strip():
            public_ip = origin.split(",")[0].strip()
        else:
            public_ip = None
        return proxy_url, True, elapsed, public_ip, ""
    except requests.exceptions.ProxyError as e:
        return proxy_url, False, None, None, f"proxy_error:{e.__class__.__name__}"
    except requests.exceptions.ConnectTimeout:
        return proxy_url, False, None, None, "connect_timeout"
    except requests.exceptions.ReadTimeout:
        return proxy_url, False, None, None, "read_timeout"
    except requests.exceptions.SSLError as e:
        return proxy_url, False, None, None, f"ssl_error:{e.__class__.__name__}"
    except requests.exceptions.ConnectionError as e:
        return proxy_url, False, None, None, f"conn_error:{e.__class__.__name__}"
    except Exception as e:
        return proxy_url, False, None, None, f"error:{e.__class__.__name__}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Test en liste med HTTP(S) proxier.")
    ap.add_argument(
        "--in",
        dest="infile",
        default="proxies.txt",
        help="Input-fil (én proxy per linje: http://user:pass@ip:port)",
    )
    ap.add_argument(
        "--out",
        dest="outfile",
        default="good_proxies.txt",
        help="Output-fil med fungerende proxier",
    )
    ap.add_argument(
        "--csv",
        dest="csvfile",
        default="proxy_results.csv",
        help="CSV med resultater (alle)",
    )
    ap.add_argument(
        "--url",
        dest="url",
        default=DEFAULT_TEST_URL,
        help=f"Test-URL (default: {DEFAULT_TEST_URL})",
    )
    ap.add_argument(
        "--timeout",
        dest="timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"Timeout i sek (default: {DEFAULT_TIMEOUT})",
    )
    ap.add_argument(
        "--workers",
        dest="workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Antall parallelle tråder (default: {DEFAULT_WORKERS})",
    )
    ap.add_argument(
        "--limit",
        dest="limit",
        type=int,
        default=0,
        help="Test kun de N første prosylene (0=alle)",
    )
    args = ap.parse_args()

    infile = Path(args.infile)
    if not infile.exists():
        print(
            f"[!] Fant ikke {infile.resolve()}. Lag filen med én proxy per linje i format: http://user:pass@ip:port"
        )
        return

    proxies = [ln.strip() for ln in infile.read_text().splitlines() if ln.strip()]
    if args.limit and args.limit > 0:
        proxies = proxies[: args.limit]

    total = len(proxies)
    if total == 0:
        print("[!] Ingen proxier å teste.")
        return

    print(
        f"Tester {total} proxier → {args.url}  (timeout={args.timeout}s, workers={args.workers})"
    )

    good: list[tuple[str, float, Optional[str]]] = []
    rows: list[dict] = []

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(test_one, p, args.timeout, args.url): p for p in proxies}
        for i, fut in enumerate(as_completed(futs), 1):
            proxy = futs[fut]
            prx, ok, latency, public_ip, err = fut.result()
            if ok:
                good.append((prx, latency or 0.0, public_ip))
                print(
                    f"[{i:>4}/{total}] ✅ OK   {latency:5.2f}s  {prx}  ip={public_ip or '-'}"
                )
            else:
                print(
                    f"[{i:>4}/{total}] ❌ FAIL {('%.2f' % latency) if latency else '--'}s  {prx}  ({err})"
                )
            rows.append(
                {
                    "proxy": prx,
                    "ok": int(ok),
                    "latency_s": f"{latency:.3f}" if latency is not None else "",
                    "public_ip": public_ip or "",
                    "error": err or "",
                }
            )

    # skriv filer
    out = Path(args.outfile)
    out.write_text("\n".join(p for p, _, _ in good) + ("\n" if good else ""))
    csvp = Path(args.csvfile)
    with csvp.open("w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["proxy", "ok", "latency_s", "public_ip", "error"]
        )
        writer.writeheader()
        writer.writerows(rows)

    # oppsummering
    if good:
        fastest = sorted(good, key=lambda t: t[1])[:5]
    else:
        fastest = []
    print("\n— RESULTAT —")
    print(f"Totalt: {total}")
    print(f"OK:     {len(good)}")
    print(f"Feil:   {total - len(good)}")
    if fastest:
        print("Raskeste 5:")
        for prx, lat, ip in fastest:
            print(f"  {lat:5.2f}s  {prx}  ip={ip or '-'}")
    print(f"\nLagret fungerende proxier i: {out.resolve()}")
    print(f"Detaljert CSV i:            {csvp.resolve()}")


if __name__ == "__main__":
    main()
