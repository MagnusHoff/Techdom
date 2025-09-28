# core/sessions.py
from __future__ import annotations

import os
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import SETTINGS

# Prøv å importere boto3; hvis ikke tilgjengelig, faller vi tilbake til lokal fil
try:
    import boto3  # type: ignore

    try:
        from botocore.exceptions import BotoCoreError as _BotoCoreError, ClientError as _ClientError  # type: ignore
    except Exception:  # pragma: no cover

        class _BotoCoreError(Exception):  # type: ignore
            pass

        class _ClientError(Exception):  # type: ignore
            pass

except Exception:  # pragma: no cover
    boto3 = None  # type: ignore

    class _BotoCoreError(Exception):  # type: ignore
        pass

    class _ClientError(Exception):  # type: ignore
        pass


# ──────────────────────────────────────────────────────────────────────────────
#  Standard headers
# ──────────────────────────────────────────────────────────────────────────────
BASE_HEADERS: Dict[str, str] = {
    "User-Agent": SETTINGS.USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "nb-NO,nb;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# Lokalt speil av godkjente proxier (fallback)
PROXY_FILE = (
    Path(__file__).resolve().parent.parent / "data" / "proxy" / "good_proxies.txt"
)

# S3 (PROXY) – bruk de dedikerte env-ene
S3_PROXY_BUCKET = os.getenv("S3_PROXY_BUCKET", "").strip()
S3_PROXY_PREFIX = os.getenv("S3_PROXY_PREFIX", "proxy").strip().strip("/")
AWS_PROXY_REGION = os.getenv("AWS_PROXY_REGION", "eu-north-1").strip()
AWS_PROXY_ACCESS_KEY_ID = os.getenv("AWS_PROXY_ACCESS_KEY_ID", "").strip()
AWS_PROXY_SECRET_ACCESS_KEY = os.getenv("AWS_PROXY_SECRET_ACCESS_KEY", "").strip()

# In-memory cache for å skåne S3
_CACHE: Dict[str, object] = {"ts": 0.0, "lines": []}
CACHE_TTL = 6 * 3600  # 6 timer


# ──────────────────────────────────────────────────────────────────────────────
#  Timeout-wrapper
# ──────────────────────────────────────────────────────────────────────────────
class SessionWithTimeout(requests.Session):
    """Session som alltid bruker default timeout fra SETTINGS hvis ikke spesifisert."""

    def request(  # type: ignore[override]
        self,
        method: str,
        url: str,
        params: Any = None,
        data: Any = None,
        headers: Any = None,
        cookies: Any = None,
        files: Any = None,
        auth: Any = None,
        timeout: Any = None,
        allow_redirects: bool = True,
        proxies: Any = None,
        hooks: Any = None,
        stream: Any = None,
        verify: Any = None,
        cert: Any = None,
        json: Any = None,
    ) -> requests.Response:
        if timeout is None:
            timeout = SETTINGS.REQ_TIMEOUT
        return super().request(
            method,
            url,
            params=params,
            data=data,
            headers=headers,
            cookies=cookies,
            files=files,
            auth=auth,
            timeout=timeout,
            allow_redirects=allow_redirects,
            proxies=proxies,
            hooks=hooks,
            stream=stream,
            verify=verify,
            cert=cert,
            json=json,
        )


# ──────────────────────────────────────────────────────────────────────────────
#  Proxy-hjelpere
# ──────────────────────────────────────────────────────────────────────────────
def _read_local() -> List[str]:
    """Les proxyliste fra lokal fil (fallback/cache)."""
    try:
        if PROXY_FILE.exists():
            content = PROXY_FILE.read_text(encoding="utf-8", errors="replace")
            return [ln.strip() for ln in content.splitlines() if ln.strip()]
    except Exception as e:  # pragma: no cover
        print(f"⚠️ Kunne ikke lese {PROXY_FILE}: {e}")
    return []


def _read_s3() -> List[str]:
    """
    Les proxyliste fra S3 (proxy-bucket). Returnerer [] ved feil eller manglende oppsett.
    Forventer at listen ligger på: s3://<S3_PROXY_BUCKET>/<S3_PROXY_PREFIX>/good_proxies.txt
    """
    if not (
        boto3
        and S3_PROXY_BUCKET
        and AWS_PROXY_ACCESS_KEY_ID
        and AWS_PROXY_SECRET_ACCESS_KEY
    ):
        return []

    key = f"{S3_PROXY_PREFIX}/good_proxies.txt"

    try:
        s3 = boto3.client(
            "s3",
            region_name=AWS_PROXY_REGION,
            aws_access_key_id=AWS_PROXY_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_PROXY_SECRET_ACCESS_KEY,
        )
        obj = s3.get_object(Bucket=S3_PROXY_BUCKET, Key=key)
        content = obj["Body"].read().decode("utf-8", errors="replace")
        lines = [ln.strip() for ln in content.splitlines() if ln.strip()]

        # Speil til lokal fil for senere fallback
        try:
            PROXY_FILE.parent.mkdir(parents=True, exist_ok=True)
            PROXY_FILE.write_text(
                "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
            )
        except Exception:
            pass

        return lines

    except (_BotoCoreError, _ClientError, Exception) as e:  # pragma: no cover
        # Stillegående fallback – vi logger enkelt og går videre
        print(f"⚠️ Klarte ikke hente proxyliste fra S3 ({S3_PROXY_BUCKET}/{key}): {e}")
        return []


def _load_proxies() -> List[str]:
    """Hent proxier: S3 → lokal fallback. Cache i minne i 6t."""
    now = time.time()
    cached = _CACHE.get("lines")
    ts = _CACHE.get("ts", 0.0)

    if (
        isinstance(cached, list)
        and isinstance(ts, (int, float))
        and (now - float(ts) < CACHE_TTL)
    ):
        return list(cached)

    lines = _read_s3()
    if not lines:
        lines = _read_local()

    _CACHE["lines"] = lines
    _CACHE["ts"] = now
    return lines


def _choose_proxy() -> Optional[Dict[str, str]]:
    """Velg en tilfeldig proxy fra listen og returner i requests-format."""
    proxies = _load_proxies()
    if not proxies:
        return None
    proxy = random.choice(proxies)
    return {"http": proxy, "https": proxy}


# ──────────────────────────────────────────────────────────────────────────────
#  Session factory
# ──────────────────────────────────────────────────────────────────────────────
def new_session(
    *, with_retries: bool = True, total_retries: int = 3
) -> requests.Session:
    """
    Lag en requests.Session med standard headers, retry-policy, default timeout og random proxy.
    """
    s = SessionWithTimeout()
    s.headers.update(BASE_HEADERS)
    s.max_redirects = 10

    if with_retries:
        retry_strategy = Retry(
            total=total_retries,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=frozenset({"HEAD", "GET", "OPTIONS"}),
            raise_on_status=False,
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        s.mount("http://", adapter)
        s.mount("https://", adapter)

    # Proxy: bruk enten SETTINGS.HTTP_PROXY eller random fra good_proxies.txt/S3
    http_proxy_value = getattr(SETTINGS, "HTTP_PROXY", None)
    if http_proxy_value:
        proxy_str = str(http_proxy_value)
        s.proxies.update({"http": proxy_str, "https": proxy_str})
    else:
        proxy = _choose_proxy()
        if proxy:
            s.proxies.update(proxy)

    return s
