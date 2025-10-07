from __future__ import annotations

import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from techdom.domain.auth.models import PasswordResetToken, User, UserRole
from techdom.infrastructure.db import get_session
from techdom.infrastructure.security import (
    decode_access_token,
    hash_password,
    verify_password,
)
from techdom.infrastructure.email import send_password_reset_email


logger = logging.getLogger(__name__)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


class DuplicateEmailError(Exception):
    """Raised when attempting to register an email that already exists."""


class UserNotFoundError(Exception):
    """Raised when a user lookup fails."""


class InvalidPasswordResetTokenError(Exception):
    """Raised when password reset token validation fails."""


class ExpiredPasswordResetTokenError(Exception):
    """Raised when a password reset token has expired."""


def _hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _reset_token_ttl() -> timedelta:
    minutes = int(os.getenv("PASSWORD_RESET_TOKEN_TTL_MINUTES", "60"))
    return timedelta(minutes=minutes)


def _reset_url_base() -> Optional[str]:
    base = os.getenv("PASSWORD_RESET_URL_BASE") or os.getenv("FRONTEND_BASE_URL")
    if not base:
        return None
    return base.rstrip("/")


def build_password_reset_url(token: str) -> Optional[str]:
    base = _reset_url_base()
    if not base:
        return None

    path = os.getenv("PASSWORD_RESET_URL_PATH", "/password-reset")
    if not path.startswith("/"):
        path = f"/{path}"
    query = urlencode({"token": token})
    return f"{base}{path}?{query}"


async def generate_password_reset_token(
    session: AsyncSession,
    *,
    email: str,
    expires_in: Optional[timedelta] = None,
) -> Optional[tuple[str, str]]:
    normalized_email = email.strip().lower()
    user = await get_user_by_email(session, email=normalized_email)
    if not user:
        return None

    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_reset_token(raw_token)
    expires_at = datetime.now(timezone.utc) + (expires_in or _reset_token_ttl())

    reset_token = PasswordResetToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=expires_at,
    )
    session.add(reset_token)
    await session.commit()

    return user.email, raw_token


async def request_password_reset(
    session: AsyncSession,
    *,
    email: str,
    expires_in: Optional[timedelta] = None,
) -> None:
    result = await generate_password_reset_token(session, email=email, expires_in=expires_in)
    if not result:
        return

    user_email, raw_token = result
    reset_url = build_password_reset_url(raw_token)
    if reset_url:
        send_password_reset_email(user_email, reset_url)
    else:
        logger.warning(
            "PASSWORD_RESET_URL_BASE is not configured; password reset token for %s: %s",
            user_email,
            raw_token,
        )


async def reset_password(
    session: AsyncSession,
    *,
    token: str,
    new_password: str,
) -> User:
    token_hash = _hash_reset_token(token)
    result = await session.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
    )
    reset_token = result.scalar_one_or_none()
    if not reset_token or reset_token.is_used():
        raise InvalidPasswordResetTokenError(token)

    now = datetime.now(timezone.utc)
    if reset_token.expires_at < now:
        raise ExpiredPasswordResetTokenError(token)

    user = await session.get(User, reset_token.user_id)
    if not user:
        raise UserNotFoundError(reset_token.user_id)

    user.hashed_password = hash_password(new_password)
    reset_token.used_at = now
    session.add_all([user, reset_token])
    await session.commit()
    await session.refresh(user)
    return user
async def get_user_by_email(
    session: AsyncSession, *, email: str
) -> Optional[User]:
    normalized_email = email.strip().lower()
    result = await session.execute(select(User).where(User.email == normalized_email))
    return result.scalar_one_or_none()


async def create_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    role: UserRole = UserRole.USER,
) -> User:
    normalized_email = email.strip().lower()
    user = User(email=normalized_email, hashed_password=hash_password(password), role=role)
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        logger.debug("IntegrityError while creating user", exc_info=exc)
        raise DuplicateEmailError(normalized_email) from exc
    await session.refresh(user)
    return user


async def authenticate_user(
    session: AsyncSession, *, email: str, password: str
) -> Optional[User]:
    user = await get_user_by_email(session, email=email)
    if not user:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    if not user.is_active:
        return None
    return user


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_session),
) -> User:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_access_token(token)
    except JWTError as exc:  # pragma: no cover - fast failure path
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    email = payload.get("sub") or payload.get("email")
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await get_user_by_email(session, email=email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


async def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


async def get_current_active_admin(
    current_user: User = Depends(get_current_active_user),
) -> User:
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


async def list_users(
    session: AsyncSession,
    *,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[User], int]:
    statement = select(User).order_by(User.created_at.desc())
    count_statement = select(func.count()).select_from(User)

    if search:
        term = f"%{search.strip()}%"
        statement = statement.where(User.email.ilike(term))
        count_statement = count_statement.where(User.email.ilike(term))

    statement = statement.limit(limit).offset(offset)

    result = await session.execute(statement)
    users = result.scalars().all()

    total = await session.scalar(count_statement)
    return users, int(total or 0)


async def update_user_role(
    session: AsyncSession,
    *,
    user_id: int,
    role: UserRole,
) -> User:
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise UserNotFoundError(user_id)

    user.role = role
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user
