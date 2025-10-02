from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ProspectJob:
    id: str
    finnkode: str
    state: str = "queued"
    progress: int = 0
    message: Optional[str] = None
    pdf_path: Optional[str] = None
    pdf_url: Optional[str] = None
    debug: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "finnkode": self.finnkode,
            "state": self.state,
            "progress": self.progress,
            "message": self.message,
            "pdf_path": self.pdf_path,
            "pdf_url": self.pdf_url,
            "debug": self.debug if self.debug else None,
        }


class ProspectJobService:
    """Simple in-memory job store with thread-safe updates."""

    def __init__(self) -> None:
        self._jobs: Dict[str, ProspectJob] = {}
        self._lock = threading.Lock()

    def create(self, finnkode: str) -> ProspectJob:
        job = ProspectJob(id=str(uuid.uuid4()), finnkode=finnkode)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def mark_running(self, job_id: str, *, progress: int, message: Optional[str] = None) -> None:
        job = self._get(job_id)
        job.state = "running"
        job.progress = progress
        if message is not None:
            job.message = message

    def attach_debug(self, job_id: str, debug: Dict[str, Any]) -> None:
        job = self._get(job_id)
        job.debug = debug

    def mark_done(
        self,
        job_id: str,
        *,
        pdf_path: Optional[str],
        pdf_url: Optional[str],
        progress: int = 100,
        message: Optional[str] = None,
    ) -> None:
        job = self._get(job_id)
        job.state = "done"
        job.progress = progress
        job.pdf_path = pdf_path
        job.pdf_url = pdf_url
        if message is not None:
            job.message = message

    def mark_failed(self, job_id: str, message: str) -> None:
        job = self._get(job_id)
        job.state = "failed"
        job.message = message

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        job = self._jobs.get(job_id)
        return job.to_dict() if job else None

    def _get(self, job_id: str) -> ProspectJob:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            return job


__all__ = ["ProspectJobService", "ProspectJob"]
