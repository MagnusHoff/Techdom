"""Utils for lagring av failcases med opsjonell S3-upload."""
from __future__ import annotations

import datetime as dt
import json
import os
import socket
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from .prospect_paths import FAIL_DIR
from .prospect_store import (
    FAILCASE_BUCKET,
    FAILCASE_PREFIX,
    failcase_key,
    failcase_s3_enabled,
    _client as _s3_client,
)


def dump_failcase(
    finnkode: str,
    label: str,
    dbg: Dict[str, Any],
    pdf_bytes: Optional[bytes] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Lagre debuginfo lokalt, og eventuelt laste opp til S3."""

    try:
        ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        stem = f"{ts}_{finnkode}_{label}"
        base = FAIL_DIR / stem

        payload = dict(dbg or {})
        if extra:
            payload["extra"] = {**payload.get("extra", {}), **extra}

        json_path = base.with_suffix(".json")
        base.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        pdf_path: Optional[Path] = None
        if pdf_bytes:
            pdf_path = base.with_suffix(".pdf")
            pdf_path.write_bytes(pdf_bytes)

        if failcase_s3_enabled():
            try:
                client = _s3_client()
                key_json = failcase_key(stem, ".json")
                client.upload_file(
                    str(json_path),
                    FAILCASE_BUCKET,
                    key_json,
                    ExtraArgs={"ContentType": "application/json; charset=utf-8"},
                )
                if pdf_path and pdf_path.exists():
                    key_pdf = failcase_key(stem, ".pdf")
                    client.upload_file(
                        str(pdf_path),
                        FAILCASE_BUCKET,
                        key_pdf,
                        ExtraArgs={"ContentType": "application/pdf"},
                    )
            except Exception:
                pass
    except Exception:
        pass


def net_diag_for_exception(
    url: str | None, sess: requests.Session | None = None
) -> dict[str, Any]:
    import platform
    import sys

    info: dict[str, Any] = {
        "timestamp_utc": dt.datetime.utcnow().isoformat() + "Z",
        "cwd": os.getcwd(),
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "python": sys.version,
        "requests_version": getattr(requests, "__version__", None),
        "env_http_proxy": os.environ.get("http_proxy") or os.environ.get("HTTP_PROXY"),
        "env_https_proxy": os.environ.get("https_proxy")
        or os.environ.get("HTTPS_PROXY"),
    }

    if isinstance(sess, requests.Session):
        info["session_proxies"] = getattr(sess, "proxies", None)

    host = None
    try:
        if url:
            from urllib.parse import urlparse

            host = urlparse(url).hostname
    except Exception:
        pass

    if host:
        try:
            addrs = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            info["dns_getaddrinfo"] = list(
                {f"{a[4][0]}:{a[4][1]}" for a in addrs if a and a[4]}
            )
        except Exception as e:
            info["dns_getaddrinfo_error"] = f"{type(e).__name__}: {e}"

    try:
        r0 = requests.get("https://example.com", timeout=5)
        info["probe_example_com"] = {"ok": r0.ok, "status": r0.status_code}
    except Exception as e:
        info["probe_example_com_error"] = f"{type(e).__name__}: {e}"

    if host:
        try:
            test_url = f"https://{host}/"
            r1 = requests.get(test_url, timeout=5)
            info["probe_domain_root"] = {"ok": r1.ok, "status": r1.status_code}
        except Exception as e:
            info["probe_domain_root_error"] = f"{type(e).__name__}: {e}"

    return info


__all__ = ["dump_failcase", "net_diag_for_exception"]
