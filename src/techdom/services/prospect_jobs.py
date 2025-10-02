from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

try:  # pragma: no cover - optional dependency for test environments
    import redis  # type: ignore
except Exception:  # pragma: no cover - redis not installed during some tests
    redis = None  # type: ignore


LOGGER = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_dict(data: Any) -> Dict[str, Any]:
    if isinstance(data, dict):
        return dict(data)
    return {}


@dataclass
class ProspectJob:
    id: str
    finnkode: str
    status: str = "queued"
    progress: int = 0
    message: Optional[str] = None
    pdf_path: Optional[str] = None
    pdf_url: Optional[str] = None
    debug: Dict[str, Any] = field(default_factory=dict)
    payload: Dict[str, Any] = field(default_factory=dict)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    result: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "finnkode": self.finnkode,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "pdf_path": self.pdf_path,
            "pdf_url": self.pdf_url,
            "debug": self.debug or None,
            "payload": self.payload or None,
            "artifacts": self.artifacts or None,
            "result": self.result or None,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ProspectJob:
        return cls(
            id=str(data.get("id")),
            finnkode=str(data.get("finnkode") or data.get("listing_id") or ""),
            status=str(data.get("status") or data.get("state") or "queued"),
            progress=int(data.get("progress") or 0),
            message=data.get("message"),
            pdf_path=data.get("pdf_path"),
            pdf_url=data.get("pdf_url"),
            debug=_coerce_dict(data.get("debug")),
            payload=_coerce_dict(data.get("payload")),
            artifacts=_coerce_dict(data.get("artifacts")),
            result=_coerce_dict(data.get("result")),
            error=data.get("error"),
            created_at=str(data.get("created_at") or _utc_now()),
            updated_at=str(data.get("updated_at") or _utc_now()),
        )

    @classmethod
    def from_json(cls, payload: str) -> ProspectJob:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            LOGGER.error("Failed to decode job payload: %s", payload)
            data = {}
        return cls.from_dict(data)


class _BackendProtocol:
    def save(self, job: ProspectJob) -> None: ...

    def load(self, job_id: str) -> Optional[ProspectJob]: ...

    def enqueue(self, job_id: str) -> None: ...

    def pop(self, timeout: int) -> Optional[str]: ...

    def delete(self, job_id: str) -> None: ...


class _InMemoryBackend(_BackendProtocol):
    def __init__(self) -> None:
        self._jobs: Dict[str, ProspectJob] = {}
        self._lock = threading.Lock()
        self._queue: "queue.Queue[str]" = queue.Queue()

    def save(self, job: ProspectJob) -> None:
        with self._lock:
            self._jobs[job.id] = job

    def load(self, job_id: str) -> Optional[ProspectJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def enqueue(self, job_id: str) -> None:
        self._queue.put(job_id)

    def pop(self, timeout: int) -> Optional[str]:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def delete(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)


class _RedisBackend(_BackendProtocol):
    def __init__(self, client: "redis.Redis", queue_name: str, key_prefix: str) -> None:
        self._redis = client
        self._queue_name = queue_name
        self._key_prefix = key_prefix

    def _job_key(self, job_id: str) -> str:
        return f"{self._key_prefix}:{job_id}"

    def save(self, job: ProspectJob) -> None:
        self._redis.set(self._job_key(job.id), job.to_json())

    def load(self, job_id: str) -> Optional[ProspectJob]:
        payload = self._redis.get(self._job_key(job_id))
        if not payload:
            return None
        return ProspectJob.from_json(payload)

    def enqueue(self, job_id: str) -> None:
        self._redis.lpush(self._queue_name, job_id)

    def pop(self, timeout: int) -> Optional[str]:
        result = self._redis.brpop(self._queue_name, timeout=timeout)
        if not result:
            return None
        _, job_id = result
        return job_id

    def delete(self, job_id: str) -> None:
        self._redis.delete(self._job_key(job_id))


class ProspectJobService:
    """Redis-backed job queue with in-memory fallback for tests/local dev."""

    DEFAULT_QUEUE = "queue:prospects"
    DEFAULT_NAMESPACE = "prospect-job"

    def __init__(
        self,
        *,
        redis_url: Optional[str] = None,
        queue_name: Optional[str] = None,
        namespace: Optional[str] = None,
    ) -> None:
        self._lock = threading.Lock()
        self._backend = self._init_backend(redis_url, queue_name, namespace)

    def _init_backend(
        self,
        redis_url: Optional[str],
        queue_name: Optional[str],
        namespace: Optional[str],
    ) -> _BackendProtocol:
        url = (
            redis_url
            or os.getenv("PROSPECT_REDIS_URL")
            or os.getenv("REDIS_URL")
        )
        qname = queue_name or os.getenv("PROSPECT_QUEUE_NAME") or self.DEFAULT_QUEUE
        ns = namespace or os.getenv("PROSPECT_NAMESPACE") or self.DEFAULT_NAMESPACE

        if url and redis is not None:
            try:
                client = redis.Redis.from_url(url, decode_responses=True)
                client.ping()
                LOGGER.info("Using Redis backend for prospect jobs (%s)", url)
                return _RedisBackend(client, qname, ns)
            except Exception:  # pragma: no cover - redis failures hard to simulate
                LOGGER.exception("Failed to initialise Redis backend – falling back to memory")

        LOGGER.info("Using in-memory backend for prospect jobs")
        return _InMemoryBackend()

    def create(
        self,
        finnkode: str,
        *,
        payload: Optional[Dict[str, Any]] = None,
        enqueue: bool = True,
    ) -> ProspectJob:
        job = ProspectJob(id=str(uuid.uuid4()), finnkode=finnkode)
        if payload:
            job.payload.update(payload)
        job.payload.setdefault("finnkode", finnkode)
        self._backend.save(job)
        if enqueue:
            self._backend.enqueue(job.id)
        return job

    def enqueue(self, job_id: str) -> None:
        self._backend.enqueue(job_id)

    def reserve_next(self, timeout: int = 5) -> Optional[ProspectJob]:
        job_id = self._backend.pop(timeout)
        if not job_id:
            return None
        job = self._backend.load(job_id)
        if not job:
            LOGGER.warning("Queue returned job %s but payload missing", job_id)
            return None
        return job

    def mark_running(
        self,
        job_id: str,
        *,
        progress: int,
        message: Optional[str] = None,
    ) -> None:
        def _mutate(job: ProspectJob) -> None:
            job.status = "running"
            job.progress = progress
            if message is not None:
                job.message = message

        self._mutate(job_id, _mutate)

    def store_artifact(self, job_id: str, name: str, value: Any) -> None:
        def _mutate(job: ProspectJob) -> None:
            job.artifacts[name] = value

        self._mutate(job_id, _mutate)

    def update_payload(self, job_id: str, data: Dict[str, Any]) -> None:
        def _mutate(job: ProspectJob) -> None:
            job.payload.update(data)

        self._mutate(job_id, _mutate)

    def attach_debug(self, job_id: str, debug: Dict[str, Any]) -> None:
        def _mutate(job: ProspectJob) -> None:
            job.debug.update(debug)

        self._mutate(job_id, _mutate)

    def mark_done(
        self,
        job_id: str,
        *,
        pdf_path: Optional[str],
        pdf_url: Optional[str],
        result: Optional[Dict[str, Any]] = None,
        progress: int = 100,
        message: Optional[str] = None,
    ) -> None:
        def _mutate(job: ProspectJob) -> None:
            job.status = "done"
            job.progress = progress
            job.pdf_path = pdf_path
            job.pdf_url = pdf_url
            if result is not None:
                job.result = result
            if message is not None:
                job.message = message
            job.error = None

        self._mutate(job_id, _mutate)

    def mark_failed(self, job_id: str, message: str, *, error: Optional[str] = None) -> None:
        def _mutate(job: ProspectJob) -> None:
            job.status = "failed"
            job.message = message
            job.error = error or message

        self._mutate(job_id, _mutate)

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        job = self._backend.load(job_id)
        return job.to_dict() if job else None

    def delete(self, job_id: str) -> None:
        self._backend.delete(job_id)

    def _mutate(self, job_id: str, fn: Callable[[ProspectJob], None]) -> None:
        with self._lock:
            job = self._backend.load(job_id)
            if job is None:
                raise KeyError(job_id)
            fn(job)
            job.updated_at = _utc_now()
            self._backend.save(job)


__all__ = ["ProspectJobService", "ProspectJob"]
