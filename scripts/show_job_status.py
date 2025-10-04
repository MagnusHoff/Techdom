#!/usr/bin/env python
"""Utility to inspect prospect jobs in Redis."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

try:
    from redis import Redis
except Exception as exc:  # pragma: no cover - redis missing
    raise SystemExit(f"redis-py not installed: {exc}")


@dataclass
class JobInfo:
    key: str
    status: str
    message: str
    updated_at: datetime

    @classmethod
    def from_payload(cls, key: str, payload: str) -> "JobInfo":
        data = json.loads(payload or "{}")
        status = str(data.get("status") or "?")
        message = str(data.get("message") or "")
        updated_raw = str(data.get("updated_at") or "")
        try:
            updated_at = datetime.fromisoformat(updated_raw.replace("Z", "+00:00"))
        except Exception:
            updated_at = datetime.min
        return cls(key=key, status=status, message=message, updated_at=updated_at)


COLORS = {
    "done": "\x1b[32m",
    "failed": "\x1b[31m",
    "running": "\x1b[33m",
    "queued": "\x1b[36m",
}
RESET = "\x1b[0m"


def format_job(job: JobInfo) -> str:
    color = COLORS.get(job.status.lower(), "")
    return (
        f"{color}{job.key}{RESET}\n"
        f"  status : {job.status}\n"
        f"  message: {job.message or '-'}\n"
        f"  updated: {job.updated_at.isoformat() if job.updated_at else '-'}\n"
    )


def main() -> None:
    client = Redis()
    keys: Iterable[bytes] = client.scan_iter("prospect-job:*")
    jobs: list[JobInfo] = []
    for raw_key in keys:
        key = raw_key.decode()
        payload = client.get(raw_key)
        if not payload:
            continue
        jobs.append(JobInfo.from_payload(key, payload.decode()))

    if not jobs:
        print("Ingen jobber funnet.")
        return

    jobs.sort(key=lambda j: j.updated_at, reverse=True)
    print(f"Fant {len(jobs)} jobber. Nyeste først:\n")
    for job in jobs:
        print(format_job(job))


if __name__ == "__main__":
    main()
