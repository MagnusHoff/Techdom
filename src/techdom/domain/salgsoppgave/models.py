from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from techdom.infrastructure.db import Base


class SalgsoppgaveCache(Base):
    __tablename__ = "salgsoppgave_cache"

    finnkode: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="not_found")
    original_pdf_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    stable_pdf_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    filesize_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    log_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def set_log(self, entries: Optional[list[str] | tuple[str, ...]]) -> None:
        if not entries:
            self.log_json = None
            return
        import json

        self.log_json = json.dumps(list(entries), ensure_ascii=False)

    def get_log(self) -> list[str]:
        if not self.log_json:
            return []
        import json

        try:
            data = json.loads(self.log_json)
        except Exception:
            return []
        return [str(item) for item in data if isinstance(item, str)]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "original_pdf_url": self.original_pdf_url,
            "stable_pdf_url": self.stable_pdf_url,
            "sha256": self.sha256,
            "filesize_bytes": self.filesize_bytes,
            "confidence": self.confidence,
            "log": self.get_log(),
        }


__all__ = ["SalgsoppgaveCache"]
