from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from techdom.infrastructure.db import Base

if TYPE_CHECKING:  # pragma: no cover - typing helpers
    from techdom.domain.auth.models import User


class SavedAnalysis(Base):
    __tablename__ = "saved_analyses"
    __table_args__ = (
        UniqueConstraint("user_id", "analysis_key", name="uq_saved_analyses_user_key"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: uuid4().hex)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    analysis_key: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    saved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    total_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(120), nullable=True)
    price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    finnkode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    analysis_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    prospectus_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="saved_analyses")

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return (
            f"SavedAnalysis(id={self.id!r}, user_id={self.user_id!r}, key={self.analysis_key!r})"
        )
