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
    elif driver and driver.startswith("sqlite"):
        database = url.database or ""
        if database:
            db_path = Path(database)
            if not db_path.is_absolute():
                project_root = Path(__file__).resolve().parents[3]
                db_path = (project_root / db_path).resolve()
                url = url.set(database=str(db_path))

    return url.render_as_string(hide_password=False), {}


def _resolve_database_url() -> Tuple[str, Dict[str, Any]]:
    url = os.getenv("DATABASE_URL")
    if url:
        return _normalise_database_url(url)

    sqlite_raw = os.getenv("LOCAL_SQLITE_PATH", "data/local.db")
    sqlite_path = Path(sqlite_raw)
    if not sqlite_path.is_absolute():
        project_root = Path(__file__).resolve().parents[3]
        sqlite_path = project_root / sqlite_path
    sqlite_path = sqlite_path.resolve()
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

    if "stripe_customer_id" not in columns:
        sync_conn.execute(text("ALTER TABLE users ADD COLUMN stripe_customer_id VARCHAR(255);"))
        sync_conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_stripe_customer_id "
                "ON users (stripe_customer_id) WHERE stripe_customer_id IS NOT NULL;"
            )
        )
        columns.add("stripe_customer_id")

    if "stripe_subscription_id" not in columns:
        sync_conn.execute(
            text("ALTER TABLE users ADD COLUMN stripe_subscription_id VARCHAR(255);")
        )
        sync_conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_stripe_subscription_id "
                "ON users (stripe_subscription_id) WHERE stripe_subscription_id IS NOT NULL;"
            )
        )
        columns.add("stripe_subscription_id")

    if "subscription_status" not in columns:
        sync_conn.execute(text("ALTER TABLE users ADD COLUMN subscription_status VARCHAR(64);"))
        columns.add("subscription_status")

    if "subscription_price_id" not in columns:
        sync_conn.execute(text("ALTER TABLE users ADD COLUMN subscription_price_id VARCHAR(255);"))
        columns.add("subscription_price_id")

    if "subscription_current_period_end" not in columns:
        sync_conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN subscription_current_period_end TIMESTAMPTZ;"
            )
        )
        columns.add("subscription_current_period_end")

    if "subscription_cancel_at_period_end" not in columns:
        sync_conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN subscription_cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE;"
            )
        )
        columns.add("subscription_cancel_at_period_end")

    if inspector.dialect.name == "postgresql":
        _ensure_lowercase_user_role_enum(sync_conn, inspector)


def _ensure_lowercase_user_role_enum(sync_conn, inspector) -> None:
    """Normalise legacy uppercase enum labels and stored role values."""

    enum_names = {enum.get("name") for enum in inspector.get_enums()}

    # Recover from a previous partial migration where the type temporarily
    # existed under an alternate name.
    if "user_role" not in enum_names:
        if "user_role_old" in enum_names:
            sync_conn.execute(text("ALTER TYPE user_role_old RENAME TO user_role"))
        else:
            return

    target_labels = {"user", "plus", "admin"}

    def _current_labels() -> set[str]:
        result = sync_conn.execute(
            text(
                "SELECT enumlabel "
                "FROM pg_enum e "
                "JOIN pg_type t ON e.enumtypid = t.oid "
                "WHERE t.typname = 'user_role'"
            )
        )
        return {row[0] for row in result}

    labels = _current_labels()
    lowercased = {label.lower() for label in labels}
    all_lowercase = all(label == label.lower() for label in labels)

    if lowercased != target_labels or not all_lowercase:
        _rebuild_user_role_enum(sync_conn)
    else:
        _normalize_user_roles(sync_conn)


def _quote_identifier(identifier: str) -> str:
    escaped = identifier.replace("\"", "\"\"")
    return "\"" + escaped + "\""


def _qualified_table(schema: str, table: str) -> str:
    table_ident = _quote_identifier(table)
    if not schema or schema == "public":
        return table_ident
    return f"{_quote_identifier(schema)}.{table_ident}"


def _rebuild_user_role_enum(sync_conn) -> None:
    """Replace legacy uppercase enum with a lowercase-only variant."""

    legacy_type_name = "user_role_legacy"

    # Clean up a stale legacy type left behind by an earlier run.
    existing_legacy = sync_conn.execute(
        text(
            "SELECT 1 FROM pg_type WHERE typname = :name"
        ),
        {"name": legacy_type_name},
    ).fetchone()
    if existing_legacy:
        # If the legacy type is still referenced somewhere we cannot drop it,
        # but the subsequent queries will target the active type anyway.
        dependencies = sync_conn.execute(
            text(
                "SELECT 1 "
                "FROM information_schema.columns "
                "WHERE udt_name = :name"
            ),
            {"name": legacy_type_name},
        ).fetchone()
        if not dependencies:
            sync_conn.execute(text(f"DROP TYPE {_quote_identifier(legacy_type_name)}"))

    sync_conn.execute(text(f"ALTER TYPE user_role RENAME TO {_quote_identifier(legacy_type_name)}"))
    sync_conn.execute(
        text("CREATE TYPE user_role AS ENUM ('user', 'plus', 'admin')")
    )

    columns = sync_conn.execute(
        text(
            "SELECT table_schema, table_name, column_name "
            "FROM information_schema.columns "
            "WHERE udt_name = :name"
        ),
        {"name": legacy_type_name},
    ).fetchall()

    for schema, table, column in columns:
        table_ident = _qualified_table(schema, table)
        column_ident = _quote_identifier(column)

        sync_conn.execute(
            text(
                f"ALTER TABLE {table_ident} "
                f"ALTER COLUMN {column_ident} DROP DEFAULT"
            )
        )
        sync_conn.execute(
            text(
                f"ALTER TABLE {table_ident} "
                f"ALTER COLUMN {column_ident} TYPE user_role "
                f"USING lower({column_ident}::text)::user_role"
            )
        )

    sync_conn.execute(text(f"DROP TYPE {_quote_identifier(legacy_type_name)}"))
    _normalize_user_roles(sync_conn)


def _normalize_user_roles(sync_conn) -> None:
    """Ensure stored values and defaults use lowercase enum labels."""

    sync_conn.execute(
        text(
            "UPDATE users "
            "SET role = CAST(lower(role::text) AS user_role) "
            "WHERE role::text != lower(role::text)"
        )
    )
    sync_conn.execute(
        text("ALTER TABLE users ALTER COLUMN role SET DEFAULT 'user'")
    )


def _ensure_saved_analyses_schema(sync_conn) -> None:
    inspector = inspect(sync_conn)
    if not inspector.has_table("saved_analyses"):
        return

    columns = {column["name"] for column in inspector.get_columns("saved_analyses")}
    if "analysis_snapshot" not in columns:
        sync_conn.execute(
            text("ALTER TABLE saved_analyses ADD COLUMN analysis_snapshot JSON")
        )
        columns.add("analysis_snapshot")
    if "prospectus_snapshot" not in columns:
        sync_conn.execute(
            text("ALTER TABLE saved_analyses ADD COLUMN prospectus_snapshot JSON")
        )
        columns.add("prospectus_snapshot")


async def ensure_auth_schema() -> None:
    """Ensure backward compatible auth schema (e.g. username column)."""
    if engine is None:
        raise RuntimeError(
            "Kan ikke oppdatere auth-skjema uten asynkron database-driver."
        ) from _DB_IMPORT_ERROR
    async with engine.begin() as connection:
        await connection.run_sync(_ensure_users_schema)


async def ensure_saved_analyses_schema() -> None:
    """Ensure new saved analyses columns exist for snapshots."""
    if engine is None:
        raise RuntimeError(
            "Kan ikke oppdatere analyses-skjema uten asynkron database-driver."
        ) from _DB_IMPORT_ERROR
    async with engine.begin() as connection:
        await connection.run_sync(_ensure_saved_analyses_schema)


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
