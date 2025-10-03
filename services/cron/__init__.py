"""FastAPI service exposing cron-triggered tasks for the SaaS pipeline."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from services import runtime

_bootstrap = runtime.ensure_bootstrap()
runtime.load_environment()

from techdom.processing.rates import get_interest_estimate
from techdom.processing.rent.data_access import load_bucket_table
from techdom.services.prospect_jobs import ProspectJobService

LOGGER = logging.getLogger(__name__)
CRON_TOKEN = os.getenv("TECHDOM_CRON_TOKEN")

app = FastAPI(title="Techdom Cron Service", version="0.1.0")
job_service = ProspectJobService()


class CronRequest(BaseModel):
    task: str = Field(..., description="Name of the cron task to execute")
    finnkode: Optional[str] = Field(None, description="FINN-kode for prospect tasks")
    params: Dict[str, Any] = Field(default_factory=dict)


def _extract_authorization(header: Optional[str]) -> Optional[str]:
    if not header:
        return None
    token = header.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token or None


def require_token(
    x_cron_token: Optional[str] = Header(default=None, convert_underscores=False),
    authorization: Optional[str] = Header(default=None),
) -> None:
    if not CRON_TOKEN:
        return
    candidates = {
        x_cron_token,
        _extract_authorization(authorization),
    }
    if CRON_TOKEN not in candidates:
        LOGGER.warning("Cron token rejected")
        raise HTTPException(status_code=403, detail="invalid cron token")


@app.get("/healthz")
def healthcheck() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/queue/depth")
def queue_depth(_: None = Depends(require_token)) -> Dict[str, int]:
    # For Redis-backed queues we cannot easily inspect size without direct client access.
    # Provide best-effort by querying provider state when available.
    try:
        backend = job_service._backend  # type: ignore[attr-defined]
        if hasattr(backend, "_redis") and hasattr(backend, "_queue_name"):
            size = backend._redis.llen(backend._queue_name)  # type: ignore[attr-defined]
            return {"depth": int(size)}
    except Exception:
        pass
    return {"depth": -1}


@app.post("/cron/run", dependencies=[Depends(require_token)])
def run_task(req: CronRequest) -> Dict[str, Any]:
    task = req.task.strip().lower()
    if task == "refresh-interest":
        result = get_interest_estimate(return_meta=True)
        if isinstance(result, tuple):
            rate, meta = result
            payload = {
                "rate": rate,
                "meta": meta.__dict__ if hasattr(meta, "__dict__") else meta,
            }
        else:
            payload = {"rate": result}
        LOGGER.info("Interest cache refreshed")
        return {"status": "ok", "task": task, "data": payload}

    if task == "warm-rent-cache":
        table = load_bucket_table(force=True)
        LOGGER.info("Rent cache warmed (%d cities)", len(table))
        return {"status": "ok", "task": task, "cities": len(table)}

    if task == "queue-prospect":
        finnkode = req.finnkode or req.params.get("finnkode")
        if not finnkode:
            raise HTTPException(status_code=400, detail="finnkode is required for queue-prospect")
        url = req.params.get("finn_url")
        payload = {"finnkode": finnkode}
        if isinstance(url, str) and url:
            payload["finn_url"] = url
        job = job_service.create(finnkode, payload=payload, enqueue=True)
        LOGGER.info("Queued prospect job %s (%s)", job.id, finnkode)
        return {"status": job.status, "job_id": job.id}

    raise HTTPException(status_code=400, detail=f"unknown task '{req.task}'")


def _configure_logging() -> None:
    log_level = os.getenv("CRON_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s [cron] %(levelname)s: %(message)s",
    )


def main() -> None:
    import uvicorn

    runtime.prepare_workdir(_bootstrap.ROOT)
    _configure_logging()
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level=os.getenv("UVICORN_LOG_LEVEL", "info"))


__all__ = ["app", "main"]
