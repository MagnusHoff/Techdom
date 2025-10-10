from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Iterable, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from techdom.domain.salgsoppgave.models import SalgsoppgaveCache
from techdom.ingestion.fetch import (
    _infer_finnkode,
    _persist_to_s3_and_mirror,
    fetch_prospectus_from_finn,
)
from techdom.ingestion.fetch_helpers import sha256_bytes
from techdom.ingestion.pdf_validation import (
    PdfValidationResult,
    validate_salgsoppgave_pdf,
)
from techdom.infrastructure.db import session_scope


LOGGER = logging.getLogger(__name__)

SalgsoppgaveStatus = str


@dataclass
class SalgsoppgaveResult:
    status: SalgsoppgaveStatus
    original_pdf_url: Optional[str] = None
    stable_pdf_url: Optional[str] = None
    filesize_bytes: Optional[int] = None
    sha256: Optional[str] = None
    confidence: float = 0.0
    log: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        payload = asdict(self)
        if payload.get("log") is None:
            payload["log"] = []
        return payload


def _normalise_finn_url(raw: str) -> tuple[str, str]:
    value = (raw or "").strip()
    if not value:
        raise ValueError("finn_url_or_id må være satt")
    if value.startswith("http://") or value.startswith("https://"):
        url = value
    elif value.isdigit():
        url = f"https://www.finn.no/realestate/homes/ad.html?finnkode={value}"
    else:
        if value.startswith("finnkode="):
            code = value.split("=", 1)[1]
            url = f"https://www.finn.no/realestate/homes/ad.html?finnkode={code}"
        else:
            url = value if value.startswith("www.") else f"https://{value}"
    finnkode = _infer_finnkode(url) or value
    if not finnkode or not finnkode.isdigit():
        raise ValueError(f"Kunne ikke utlede FINN-kode fra {raw!r}")
    return finnkode, url


def _confidence_from_validation(validation: PdfValidationResult) -> float:
    if not validation.ok:
        return 0.0
    matched = max(1, len(validation.matched_keywords))
    score = 0.85 + min(0.05 * matched, 0.1)
    return round(min(1.0, score), 3)


def _compose_log_entry(prefix: str, info: Optional[str]) -> str:
    if not info:
        return prefix
    return f"{prefix}:{info}"


async def _fetch_pdf_bytes(finn_url: str) -> tuple[bytes | None, str | None, dict]:
    return await asyncio.to_thread(
        fetch_prospectus_from_finn,
        finn_url,
        persist=False,
    )


async def _upload_pdf(
    pdf_bytes: bytes, source_url: Optional[str], finnkode: str
) -> tuple[bytes, Optional[str], dict]:
    return await asyncio.to_thread(
        _persist_to_s3_and_mirror,
        pdf_bytes=pdf_bytes,
        pdf_url=source_url,
        finnkode=finnkode,
    )


def _virus_scan_ok(_: bytes) -> bool:
    """
    Placeholder for virus scanning. Returns True for now but provides
    a hook for future integration with an AV engine.
    """
    return True


async def _retrieve_with_session(
    session: AsyncSession,
    *,
    finnkode: str,
    finn_url: str,
    extra_terms: Optional[Iterable[str]] = None,
) -> SalgsoppgaveResult:
    log: List[str] = [f"start:{finnkode}"]
    now = datetime.now(timezone.utc)

    cached: SalgsoppgaveCache | None = await session.get(SalgsoppgaveCache, finnkode)
    if cached and cached.status == "found" and cached.stable_pdf_url:
        cached.last_checked_at = now
        await session.flush()
        cache_log = cached.get_log()
        log.extend(["cache_hit", *cache_log])
        return SalgsoppgaveResult(
            status="found",
            original_pdf_url=cached.original_pdf_url,
            stable_pdf_url=cached.stable_pdf_url,
            filesize_bytes=cached.filesize_bytes,
            sha256=cached.sha256,
            confidence=cached.confidence or 0.0,
            log=log,
        )

    log.append("cache_miss")

    try:
        pdf_bytes, original_url, fetch_debug = await _fetch_pdf_bytes(finn_url)
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.exception("Henting av salgsoppgave fra FINN feilet for %s", finnkode)
        log.append(_compose_log_entry("fetch_exception", repr(exc)))
        result = SalgsoppgaveResult(status="not_found", log=log)
        await _upsert_cache(
            session,
            finnkode=finnkode,
            status=result.status,
            original_pdf_url=None,
            stable_pdf_url=None,
            sha256=None,
            filesize=None,
            confidence=0.0,
            log=log,
        )
        return result

    if fetch_debug:
        step = fetch_debug.get("step")
        if step:
            log.append(_compose_log_entry("fetch_step", str(step)))
        if fetch_debug.get("driver_used"):
            log.append(
                _compose_log_entry("driver", str(fetch_debug.get("driver_used")))
            )
        if fetch_debug.get("browser_debug", {}).get("notes"):
            notes = fetch_debug["browser_debug"]["notes"]
            if isinstance(notes, list):
                for note in notes[:5]:
                    log.append(_compose_log_entry("note", str(note)))

    if not pdf_bytes:
        log.append("no_pdf_bytes")
        result = SalgsoppgaveResult(
            status="not_found",
            original_pdf_url=original_url,
            stable_pdf_url=None,
            filesize_bytes=None,
            sha256=None,
            confidence=0.0,
            log=log,
        )
        await _upsert_cache(
            session,
            finnkode=finnkode,
            status=result.status,
            original_pdf_url=original_url,
            stable_pdf_url=None,
            sha256=None,
            filesize=None,
            confidence=0.0,
            log=log,
        )
        return result

    log.append("pdf_downloaded")

    validation = validate_salgsoppgave_pdf(
        pdf_bytes,
        extra_match_terms=extra_terms,
    )
    log.append(
        _compose_log_entry(
            "validation",
            "ok" if validation.ok else (validation.reason or "failed"),
        )
    )

    if not validation.ok:
        result = SalgsoppgaveResult(
            status="uncertain",
            original_pdf_url=original_url,
            stable_pdf_url=None,
            filesize_bytes=len(pdf_bytes),
            sha256=sha256_bytes(pdf_bytes),
            confidence=0.25,
            log=log,
        )
        await _upsert_cache(
            session,
            finnkode=finnkode,
            status=result.status,
            original_pdf_url=original_url,
            stable_pdf_url=None,
            sha256=result.sha256,
            filesize=len(pdf_bytes),
            confidence=result.confidence,
            log=log,
        )
        return result

    if not _virus_scan_ok(pdf_bytes):
        log.append("virus_scan_failed")
        result = SalgsoppgaveResult(
            status="uncertain",
            original_pdf_url=original_url,
            stable_pdf_url=None,
            filesize_bytes=len(pdf_bytes),
            sha256=sha256_bytes(pdf_bytes),
            confidence=0.1,
            log=log,
        )
        await _upsert_cache(
            session,
            finnkode=finnkode,
            status=result.status,
            original_pdf_url=original_url,
            stable_pdf_url=None,
            sha256=result.sha256,
            filesize=len(pdf_bytes),
            confidence=result.confidence,
            log=log,
        )
        return result

    log.append("virus_scan_ok")

    uploaded_bytes, stable_url, persist_dbg = await _upload_pdf(
        pdf_bytes, original_url, finnkode
    )
    if persist_dbg.get("stable_url"):
        log.append(_compose_log_entry("stable_url", persist_dbg["stable_url"]))
    if persist_dbg.get("s3_key"):
        log.append(_compose_log_entry("s3_key", persist_dbg["s3_key"]))

    sha_value = persist_dbg.get("pdf_hash") or sha256_bytes(uploaded_bytes)
    confidence = _confidence_from_validation(validation)

    result = SalgsoppgaveResult(
        status="found",
        original_pdf_url=original_url,
        stable_pdf_url=stable_url,
        filesize_bytes=len(uploaded_bytes),
        sha256=sha_value,
        confidence=confidence,
        log=log,
    )

    await _upsert_cache(
        session,
        finnkode=finnkode,
        status=result.status,
        original_pdf_url=original_url,
        stable_pdf_url=stable_url,
        sha256=sha_value,
        filesize=len(uploaded_bytes),
        confidence=confidence,
        log=log,
    )

    return result


async def _upsert_cache(
    session: AsyncSession,
    *,
    finnkode: str,
    status: SalgsoppgaveStatus,
    original_pdf_url: Optional[str],
    stable_pdf_url: Optional[str],
    sha256: Optional[str],
    filesize: Optional[int],
    confidence: float,
    log: Iterable[str],
) -> None:
    record: SalgsoppgaveCache | None = await session.get(SalgsoppgaveCache, finnkode)
    if record is None:
        record = SalgsoppgaveCache(finnkode=finnkode)
        session.add(record)

    record.status = status
    record.original_pdf_url = original_pdf_url
    record.stable_pdf_url = stable_pdf_url
    record.sha256 = sha256
    record.filesize_bytes = filesize
    record.confidence = confidence
    record.last_checked_at = datetime.now(timezone.utc)
    record.set_log(list(log))
    await session.flush()


async def retrieve_salgsoppgave(
    finnkode_or_url: str,
    *,
    session: AsyncSession | None = None,
    extra_terms: Optional[Iterable[str]] = None,
) -> SalgsoppgaveResult:
    finnkode, finn_url = _normalise_finn_url(finnkode_or_url)
    if session is not None:
        return await _retrieve_with_session(
            session,
            finnkode=finnkode,
            finn_url=finn_url,
            extra_terms=extra_terms,
        )

    async with session_scope() as scoped:
        return await _retrieve_with_session(
            scoped,
            finnkode=finnkode,
            finn_url=finn_url,
            extra_terms=extra_terms,
        )


__all__ = ["retrieve_salgsoppgave", "SalgsoppgaveResult", "SalgsoppgaveStatus"]
