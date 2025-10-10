from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping

from fastapi import HTTPException, status

from techdom.processing.tg_extract import (
    ExtractionError,
    build_v2_details,
    extract_tg,
    extract_tg_from_pdf_bytes,
)


_CACHE_DIR = Path("data/cache/tg_details")
_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _sanitize_id(value: str) -> str:
    """Keep filename safe while retaining enough entropy."""
    if not value:
        raise ValueError("analysis_id kan ikke være tom")
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", value.strip())
    return safe or "_"


def _cache_path(analysis_id: str) -> Path:
    return _CACHE_DIR / f"{_sanitize_id(analysis_id)}.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_cache(analysis_id: str) -> dict[str, Any] | None:
    path = _cache_path(analysis_id)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def save_cache(analysis_id: str, payload: Mapping[str, Any]) -> None:
    path = _cache_path(analysis_id)
    temp = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8")
    try:
        json.dump(dict(payload), temp, ensure_ascii=False, indent=2)
        temp.flush()
        Path(temp.name).replace(path)
    finally:
        try:
            temp.close()
        except Exception:
            pass


@dataclass(slots=True)
class ExtractionResult:
    analysis_id: str
    tg2_details: list[dict[str, Any]]
    tg_version: int = 2
    updated_at: str = ""
    pdf_url: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: MutableMapping[str, Any] = {
            "analysis_id": self.analysis_id,
            "tg_version": self.tg_version,
            "updated_at": self.updated_at or _utc_now(),
            "tg2_details": self.tg2_details,
        }
        if self.pdf_url:
            payload["pdf_url"] = self.pdf_url
        return dict(payload)


class PdfSourceMissing(HTTPException):
    def __init__(self, analysis_id: str) -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Fant ikke PDF for analyse {analysis_id}",
        )


class ExtractionFailed(HTTPException):
    def __init__(self, message: str) -> None:
        super().__init__(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=message,
        )


def extract_from_source(source: str) -> dict[str, Any]:
    try:
        return extract_tg(source)
    except ExtractionError as exc:
        raise ExtractionFailed(str(exc)) from exc


def extract_from_bytes(data: bytes) -> dict[str, Any]:
    try:
        return extract_tg_from_pdf_bytes(data)
    except ExtractionError as exc:
        raise ExtractionFailed(str(exc)) from exc


def _entries_from_payload(payload: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    json_payload = payload.get("json")
    if isinstance(json_payload, Mapping):
        entries = json_payload.get("TG2")
        if isinstance(entries, Iterable):
            return entries  # type: ignore[return-value]
    entries = payload.get("TG2")
    if isinstance(entries, Iterable):
        return entries  # type: ignore[return-value]
    return []


def extract_details(
    analysis_id: str,
    *,
    source: str | None = None,
    pdf_bytes: bytes | None = None,
    pdf_url: str | None = None,
) -> ExtractionResult:
    if pdf_bytes is not None:
        payload = extract_from_bytes(pdf_bytes)
    elif source:
        payload = extract_from_source(source)
    else:  # pragma: no cover - defensive branch, validated by caller
        raise ValueError("source eller pdf_bytes må settes")

    entries = _entries_from_payload(payload)
    tg2_details = build_v2_details(entries, level=2)
    result = ExtractionResult(
        analysis_id=analysis_id,
        tg2_details=tg2_details,
        updated_at=_utc_now(),
        pdf_url=pdf_url or source,
    )
    save_cache(analysis_id, result.as_dict())
    return result


def cache_is_v2(payload: Mapping[str, Any] | None) -> bool:
    if not payload:
        return False
    if payload.get("tg_version") != 2:
        return False
    details = payload.get("tg2_details")
    return isinstance(details, list)


def cache_to_result(analysis_id: str, payload: Mapping[str, Any]) -> ExtractionResult:
    tg2_details = payload.get("tg2_details")
    if not isinstance(tg2_details, list):
        tg2_details = []
    return ExtractionResult(
        analysis_id=analysis_id,
        tg2_details=[dict(item) for item in tg2_details if isinstance(item, Mapping)],
        tg_version=int(payload.get("tg_version", 2)),
        updated_at=str(payload.get("updated_at") or ""),
        pdf_url=str(payload.get("pdf_url") or "") or None,
    )
