from __future__ import annotations
from typing import Optional, List, Tuple
from urllib.parse import urlparse, urlunparse, parse_qs
import requests
from .sessions import new_session
from techdom.infrastructure.config import SETTINGS


def _clean_url(u: str) -> str:
    pr = urlparse(u)
    q = parse_qs(pr.query)
    drop = {k for k in q if k.startswith("utm_") or k in {"gclid", "fbclid"}}
    if not drop:
        return u
    kept = [(k, v) for k, v in q.items() if k not in drop]
    query = "&".join(f"{k}={v[0]}" for k, v in kept)
    return urlunparse((pr.scheme, pr.netloc, pr.path, pr.params, query, ""))


def try_download_pdf(url: str, referers: list[str | None]) -> tuple[bytes | None, dict]:
    sess = new_session()
    info = {"attempts": []}
    for candidate in [url, _clean_url(url)]:
        for ref in referers:
            headers = {"Accept": "application/pdf,application/octet-stream,*/*"}
            if ref:
                headers["Referer"] = ref
                pr = urlparse(ref)
                headers["Origin"] = f"{pr.scheme}://{pr.netloc}"
            try:
                r = sess.get(
                    candidate,
                    headers=headers,
                    timeout=SETTINGS.REQ_TIMEOUT,
                    allow_redirects=True,
                )
                rec = {
                    "candidate": candidate,
                    "referer": ref,
                    "status": r.status_code,
                    "ct": r.headers.get("Content-Type"),
                    "len": r.headers.get("Content-Length"),
                }
                info["attempts"].append(rec)
                ct = (r.headers.get("Content-Type") or "").lower()
                if r.ok and (
                    ct.startswith("application/pdf")
                    or r.content[:4] == b"%PDF"
                    or len(r.content) > 50_000
                ):
                    return r.content, info
            except Exception as e:
                info["attempts"].append(
                    {"candidate": candidate, "referer": ref, "status": f"exc:{e}"}
                )
    return None, info
