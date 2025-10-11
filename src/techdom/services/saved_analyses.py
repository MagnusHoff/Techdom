from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Sequence

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from techdom.domain.saved_analyses.models import SavedAnalysis
from techdom.domain.saved_analyses.schemas import SavedAnalysisCreate


def _timestamp() -> datetime:
    return datetime.now(timezone.utc)


async def list_saved_analyses(
    session: AsyncSession,
    *,
    user_id: int,
    analysis_key: str | None = None,
) -> Sequence[SavedAnalysis]:
    stmt: Select[tuple[SavedAnalysis]] = select(SavedAnalysis).where(SavedAnalysis.user_id == user_id)
    if analysis_key:
        stmt = stmt.where(SavedAnalysis.analysis_key == analysis_key)
    stmt = stmt.order_by(SavedAnalysis.saved_at.desc())
    result = await session.execute(stmt)
    return result.scalars().all()


async def upsert_saved_analysis(
    session: AsyncSession,
    *,
    user_id: int,
    payload: SavedAnalysisCreate,
) -> SavedAnalysis:
    stmt: Select[tuple[SavedAnalysis]] = select(SavedAnalysis).where(
        SavedAnalysis.user_id == user_id,
        SavedAnalysis.analysis_key == payload.analysis_key,
    )
    result = await session.execute(stmt)
    instance = result.scalar_one_or_none()

    now = _timestamp()
    data = payload.model_dump()

    if instance is None:
        instance = SavedAnalysis(user_id=user_id, **data)
        instance.saved_at = now
        session.add(instance)
    else:
        for field, value in data.items():
            setattr(instance, field, value)
        instance.saved_at = now

    await session.flush()
    await session.refresh(instance)
    await session.commit()
    return instance


async def get_saved_analysis(
    session: AsyncSession,
    *,
    user_id: int,
    analysis_id: str,
) -> SavedAnalysis | None:
    stmt: Select[tuple[SavedAnalysis]] = select(SavedAnalysis).where(
        SavedAnalysis.user_id == user_id,
        SavedAnalysis.id == analysis_id.strip(),
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def delete_saved_analysis(
    session: AsyncSession,
    *,
    user_id: int,
    analysis_id: str,
) -> bool:
    stmt: Select[tuple[SavedAnalysis]] = select(SavedAnalysis).where(
        SavedAnalysis.user_id == user_id,
        SavedAnalysis.id == analysis_id,
    )
    result = await session.execute(stmt)
    instance = result.scalar_one_or_none()
    if instance is None:
        return False
    await session.delete(instance)
    await session.flush()
    await session.commit()
    return True
