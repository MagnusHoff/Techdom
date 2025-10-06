from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


logger = logging.getLogger(__name__)


def _should_echo_sql() -> bool:
    value = os.getenv("SQLALCHEMY_ECHO", "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _resolve_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if url:
        return url

    sqlite_path = Path(os.getenv("LOCAL_SQLITE_PATH", "data/local.db")).resolve()
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    logger.warning(
        "DATABASE_URL is not set. Falling back to local SQLite database at %s", sqlite_path
    )
    return f"sqlite+aiosqlite:///{sqlite_path}"


DATABASE_URL = _resolve_database_url()


class Base(DeclarativeBase):
    """Base class for SQLAlchemy models."""


engine: AsyncEngine = create_async_engine(DATABASE_URL, echo=_should_echo_sql())
SessionMaker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionMaker() as session:
        yield session


async def init_models() -> None:
    """Create database tables if they do not already exist."""
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional scope around a series of operations."""
    async with SessionMaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
