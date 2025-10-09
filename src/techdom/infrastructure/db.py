from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Tuple

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.engine import make_url
from sqlalchemy.engine.url import URL


logger = logging.getLogger(__name__)


def _should_echo_sql() -> bool:
    value = os.getenv("SQLALCHEMY_ECHO", "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _normalise_database_url(raw: str) -> Tuple[str, Dict[str, Any]]:
    """Normalise database URL for async usage."""

    url: URL = make_url(raw)
    driver = url.drivername

    if driver in {"postgres", "postgresql", "postgresql+psycopg", "postgresql+psycopg2", "postgresql+asyncpg"}:
        url = url.set(drivername="postgresql+psycopg_async")

    return url.render_as_string(hide_password=False), {}


def _resolve_database_url() -> Tuple[str, Dict[str, Any]]:
    url = os.getenv("DATABASE_URL")
    if url:
        return _normalise_database_url(url)

    sqlite_path = Path(os.getenv("LOCAL_SQLITE_PATH", "data/local.db")).resolve()
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    logger.warning(
        "DATABASE_URL is not set. Falling back to local SQLite database at %s", sqlite_path
    )
    return f"sqlite+aiosqlite:///{sqlite_path}", {}


DATABASE_URL, CONNECT_ARGS = _resolve_database_url()


class Base(DeclarativeBase):
    """Base class for SQLAlchemy models."""


_DB_IMPORT_ERROR: Exception | None = None

try:
    engine: AsyncEngine = create_async_engine(
        DATABASE_URL,
        echo=_should_echo_sql(),
        connect_args=CONNECT_ARGS,
    )
    SessionMaker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
    engine = None  # type: ignore[assignment]
    SessionMaker = None  # type: ignore[assignment]
    _DB_IMPORT_ERROR = exc


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    if SessionMaker is None:
        raise RuntimeError(
            "Asynkron database-driver er ikke installert. "
            "Installer f.eks. 'aiosqlite' eller sett DATABASE_URL til en støttet driver."
        ) from _DB_IMPORT_ERROR
    async with SessionMaker() as session:
        yield session


async def init_models() -> None:
    """Create database tables if they do not already exist."""
    if engine is None:
        raise RuntimeError(
            "Kan ikke initialisere databasen uten asynkron driver. "
            "Installer nødvendig avhengighet først."
        ) from _DB_IMPORT_ERROR
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


def _ensure_users_schema(sync_conn) -> None:
    inspector = inspect(sync_conn)
    if not inspector.has_table("users"):
        return

    columns = {column["name"] for column in inspector.get_columns("users")}
    if "username" not in columns:
        sync_conn.execute(text("ALTER TABLE users ADD COLUMN username VARCHAR(150);"))
        sync_conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_username "
                "ON users (username) WHERE username IS NOT NULL;"
            )
        )
        columns.add("username")

    if "username_canonical" not in columns:
        sync_conn.execute(text("ALTER TABLE users ADD COLUMN username_canonical VARCHAR(150);"))
        sync_conn.execute(
            text(
                "UPDATE users SET username_canonical = LOWER(username) WHERE username IS NOT NULL;"
            )
        )
        sync_conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_username_canonical "
                "ON users (username_canonical) WHERE username_canonical IS NOT NULL;"
            )
        )

    if "is_email_verified" not in columns:
        sync_conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN is_email_verified BOOLEAN NOT NULL DEFAULT FALSE;"
            )
        )
        columns.add("is_email_verified")

    if "avatar_emoji" not in columns:
        sync_conn.execute(text("ALTER TABLE users ADD COLUMN avatar_emoji VARCHAR(16);"))
        columns.add("avatar_emoji")

    if "avatar_color" not in columns:
        sync_conn.execute(text("ALTER TABLE users ADD COLUMN avatar_color VARCHAR(16);"))
        columns.add("avatar_color")

    if "total_analyses" not in columns:
        sync_conn.execute(
            text("ALTER TABLE users ADD COLUMN total_analyses INTEGER NOT NULL DEFAULT 0;")
        )
        columns.add("total_analyses")


async def ensure_auth_schema() -> None:
    """Ensure backward compatible auth schema (e.g. username column)."""
    if engine is None:
        raise RuntimeError(
            "Kan ikke oppdatere auth-skjema uten asynkron database-driver."
        ) from _DB_IMPORT_ERROR
    async with engine.begin() as connection:
        await connection.run_sync(_ensure_users_schema)


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional scope around a series of operations."""
    if SessionMaker is None:
        raise RuntimeError(
            "Kan ikke opprette database-session uten asynkron driver."
        ) from _DB_IMPORT_ERROR
    async with SessionMaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
