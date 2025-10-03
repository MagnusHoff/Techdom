"""Background worker process that consumes prospect jobs from Redis."""
from __future__ import annotations

import logging
import os
import signal
import time
from typing import Optional

from services import runtime

_bootstrap = runtime.ensure_bootstrap()
runtime.load_environment()

from techdom.services.prospect_jobs import ProspectJob, ProspectJobService
from techdom.services.prospect_pipeline import ProspectAnalysisPipeline

LOGGER = logging.getLogger(__name__)


class Worker:
    def __init__(
        self,
        job_service: ProspectJobService,
        pipeline: ProspectAnalysisPipeline,
        *,
        poll_timeout: int = 5,
        idle_sleep: float = 0.5,
    ) -> None:
        self.job_service = job_service
        self.pipeline = pipeline
        self.poll_timeout = poll_timeout
        self.idle_sleep = idle_sleep
        self._running = True

    def stop(self, *_signal: object, **_kw: object) -> None:
        LOGGER.info("Worker received shutdown signal")
        self._running = False

    def run(self) -> None:
        LOGGER.info("Worker started")
        while self._running:
            job = self._next_job()
            if job is None:
                time.sleep(self.idle_sleep)
                continue
            LOGGER.info("Processing job %s (finnkode=%s)", job.id, job.finnkode)
            try:
                self.pipeline.run(job)
            except Exception:  # pragma: no cover - safety net
                LOGGER.exception("Unhandled worker exception for job %s", job.id)
                try:
                    self.job_service.mark_failed(job.id, "Workerfeil", error="unhandled")
                except Exception:  # pragma: no cover
                    LOGGER.exception("Failed to mark job %s as failed", job.id)

        LOGGER.info("Worker stopped")

    def _next_job(self) -> Optional[ProspectJob]:
        return self.job_service.reserve_next(timeout=self.poll_timeout)


def _configure_logging() -> None:
    log_level = os.getenv("WORKER_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s [worker] %(levelname)s: %(message)s",
    )


def main() -> None:
    runtime.prepare_workdir(_bootstrap.ROOT)
    _configure_logging()
    job_service = ProspectJobService()
    pipeline = ProspectAnalysisPipeline(job_service)
    worker = Worker(job_service, pipeline)

    signal.signal(signal.SIGTERM, worker.stop)
    signal.signal(signal.SIGINT, worker.stop)

    worker.run()


if __name__ == "__main__":
    main()
