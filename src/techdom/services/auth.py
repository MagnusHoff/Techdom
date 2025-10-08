from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from techdom.domain.auth.models import (
    EmailVerificationToken,
    LoginAttempt,
    PasswordResetToken,
    User,
    UserRole,
)
from techdom.infrastructure.db import get_session
from techdom.infrastructure.security import (
    decode_access_token,
    hash_password,
    verify_password,
)
from techdom.infrastructure.email import (
    send_email_verification_email,
    send_password_reset_email,
)


logger = logging.getLogger(__name__)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)

USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9._]{3,20}$")
PASSWORD_PATTERN = re.compile(
    r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>/?]).{8,}$"
)


def _login_attempt_limit() -> int:
    raw = os.getenv("LOGIN_RATE_LIMIT_MAX_ATTEMPTS", "5")
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid LOGIN_RATE_LIMIT_MAX_ATTEMPTS value %s; falling back to 5", raw)
        return 5
    return value if value > 0 else 5


def _login_attempt_window() -> timedelta:
    raw = os.getenv("LOGIN_RATE_LIMIT_WINDOW_MINUTES", "15")
    try:
        minutes = int(raw)
    except ValueError:
        logger.warning("Invalid LOGIN_RATE_LIMIT_WINDOW_MINUTES value %s; falling back to 15", raw)
        minutes = 15
    return timedelta(minutes=max(minutes, 1))


class DuplicateEmailError(Exception):
    """Raised when attempting to register an email or username that already exists."""


class DuplicateUsernameError(Exception):
    """Raised when attempting to update a username that already exists."""


class UserNotFoundError(Exception):
    """Raised when a user lookup fails."""


class InvalidPasswordResetTokenError(Exception):
    """Raised when password reset token validation fails."""


class ExpiredPasswordResetTokenError(Exception):
    """Raised when a password reset token has expired."""


class InvalidCurrentPasswordError(Exception):
    """Raised when the provided current password does not match the stored hash."""


class InvalidUsernameError(Exception):
    """Raised when a username does not meet formatting requirements."""


class InvalidPasswordError(Exception):
    """Raised when a password does not meet complexity requirements."""


class InvalidEmailVerificationTokenError(Exception):
    """Raised when email verification token validation fails."""


class ExpiredEmailVerificationTokenError(Exception):
    """Raised when an email verification token has expired."""


def _hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _reset_token_ttl() -> timedelta:
    minutes = int(os.getenv("PASSWORD_RESET_TOKEN_TTL_MINUTES", "60"))
    return timedelta(minutes=minutes)


def _verification_token_ttl() -> timedelta:
    hours = int(os.getenv("EMAIL_VERIFICATION_TOKEN_TTL_HOURS", "24"))
    return timedelta(hours=hours)


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


def _verification_url_base() -> Optional[str]:
    base = os.getenv("EMAIL_VERIFICATION_URL_BASE") or os.getenv("FRONTEND_BASE_URL")
    if not base:
        return None
    return base.rstrip("/")


def build_email_verification_url(token: str) -> Optional[str]:
    base = _verification_url_base()
    if not base:
        return None

    path = os.getenv("EMAIL_VERIFICATION_URL_PATH", "/verify-email")
    if not path.startswith("/"):
        path = f"/{path}"
    query = urlencode({"token": token})
    return f"{base}{path}?{query}"


async def register_failed_login_attempt(
    session: AsyncSession, *, email: str, ip_address: str | None = None
) -> None:
    normalized_email = email.strip().lower()
    attempt = LoginAttempt(
        email=normalized_email,
        ip_address=ip_address or None,
        succeeded=False,
    )
    session.add(attempt)

    cutoff = datetime.now(timezone.utc) - _login_attempt_window()
    await session.execute(
        delete(LoginAttempt)
        .where(
            LoginAttempt.email == normalized_email,
            LoginAttempt.attempted_at < cutoff,
        )
        .execution_options(synchronize_session=False)
    )
    await session.commit()


async def clear_login_attempts(session: AsyncSession, *, email: str) -> None:
    normalized_email = email.strip().lower()
    await session.execute(delete(LoginAttempt).where(LoginAttempt.email == normalized_email))
    await session.commit()


async def is_login_rate_limited(
    session: AsyncSession, *, email: str, max_attempts: Optional[int] = None
) -> bool:
    normalized_email = email.strip().lower()
    cutoff = datetime.now(timezone.utc) - _login_attempt_window()

    stmt = (
        select(func.count())
        .select_from(LoginAttempt)
        .where(
            LoginAttempt.email == normalized_email,
            LoginAttempt.succeeded.is_(False),
            LoginAttempt.attempted_at >= cutoff,
        )
    )
    failures = await session.scalar(stmt)
    limit = max_attempts or _login_attempt_limit()
    return int(failures or 0) >= limit


def _normalize_username(username: str) -> str:
    return username.strip()


def _canonicalize_username(username: str) -> str:
    return _normalize_username(username).lower()


def _validate_username(username: str) -> str:
    normalized = _normalize_username(username)
    if not USERNAME_PATTERN.fullmatch(normalized):
        raise InvalidUsernameError(
            "Brukernavn må være 3-20 tegn og kan kun inneholde bokstaver, tall, punktum og understrek."
        )
    return normalized


def _validate_password(password: str) -> str:
    if not PASSWORD_PATTERN.fullmatch(password):
        raise InvalidPasswordError(
            "Passordet må være minst 8 tegn og inneholde store og små bokstaver, tall og spesialtegn."
        )
    return password


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

    _validate_password(new_password)
    user.hashed_password = hash_password(new_password)
    reset_token.used_at = now
    session.add_all([user, reset_token])
    await session.commit()
    await session.refresh(user)
    return user


async def generate_email_verification_token(
    session: AsyncSession,
    *,
    user_id: int,
    expires_in: Optional[timedelta] = None,
) -> Optional[tuple[str, str]]:
    user = await session.get(User, user_id)
    if not user:
        raise UserNotFoundError(user_id)

    if user.is_email_verified:
        return None

    await session.execute(delete(EmailVerificationToken).where(EmailVerificationToken.user_id == user_id))

    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_reset_token(raw_token)
    expires_at = datetime.now(timezone.utc) + (expires_in or _verification_token_ttl())

    verification_token = EmailVerificationToken(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=expires_at,
    )
    session.add(verification_token)
    await session.commit()

    return user.email, raw_token


async def verify_email_token(session: AsyncSession, *, token: str) -> User:
    token_hash = _hash_reset_token(token)
    result = await session.execute(
        select(EmailVerificationToken).where(EmailVerificationToken.token_hash == token_hash)
    )
    verification_token = result.scalar_one_or_none()
    if not verification_token or verification_token.is_used():
        raise InvalidEmailVerificationTokenError(token)

    now = datetime.now(timezone.utc)
    if verification_token.expires_at < now:
        raise ExpiredEmailVerificationTokenError(token)

    user = await session.get(User, verification_token.user_id)
    if not user:
        raise UserNotFoundError(verification_token.user_id)

    user.is_email_verified = True
    verification_token.used_at = now
    session.add_all([user, verification_token])
    await session.execute(
        delete(EmailVerificationToken).where(
            EmailVerificationToken.user_id == user.id,
            EmailVerificationToken.id != verification_token.id,
        )
    )
    await session.commit()
    await session.refresh(user)
    return user


async def update_username(
    session: AsyncSession,
    *,
    user_id: int,
    username: str,
) -> User:
    normalized_username = _validate_username(username)
    canonical_username = _canonicalize_username(normalized_username)

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise UserNotFoundError(user_id)

    if user.username == normalized_username:
        return user

    conflict_stmt = select(User.id).where(
        User.username_canonical == canonical_username,
        User.id != user_id,
    )
    conflict = await session.execute(conflict_stmt)
    if conflict.scalar_one_or_none():
        raise DuplicateUsernameError(normalized_username)

    user.username = normalized_username
    user.username_canonical = canonical_username
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise DuplicateUsernameError(normalized_username) from exc

    await session.refresh(user)
    return user


async def change_password(
    session: AsyncSession,
    *,
    user_id: int,
    current_password: str,
    new_password: str,
) -> User:
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise UserNotFoundError(user_id)

    try:
        password_matches = verify_password(current_password, user.hashed_password)
    except ValueError as exc:  # pragma: no cover - defensive guard for corrupted hashes
        logger.exception("Invalid password hash for user %s", user.email)
        raise InvalidCurrentPasswordError(user.email) from exc

    if not password_matches:
        raise InvalidCurrentPasswordError(user.email)

    _validate_password(new_password)
    user.hashed_password = hash_password(new_password)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def set_user_password(
    session: AsyncSession,
    *,
    user_id: int,
    new_password: str,
) -> User:
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise UserNotFoundError(user_id)

    _validate_password(new_password)
    user.hashed_password = hash_password(new_password)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def delete_user(
    session: AsyncSession,
    *,
    user_id: int,
) -> None:
    user = await session.get(User, user_id)
    if not user:
        raise UserNotFoundError(user_id)

    await session.delete(user)
    await session.commit()


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
    username: str,
    password: str,
    role: UserRole = UserRole.USER,
) -> User:
    normalized_email = email.strip().lower()
    normalized_username = _validate_username(username)
    canonical_username = _canonicalize_username(normalized_username)

    existing_username_stmt = select(User.id).where(User.username_canonical == canonical_username)
    existing_username = await session.execute(existing_username_stmt)
    if existing_username.scalar_one_or_none():
        raise DuplicateUsernameError(normalized_username)

    _validate_password(password)
    hashed_password = hash_password(password)
    user = User(
        email=normalized_email,
        username=normalized_username,
        username_canonical=canonical_username,
        hashed_password=hashed_password,
        role=role,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        logger.debug("IntegrityError while creating user", exc_info=exc)
        message = str(getattr(exc, "orig", exc)).lower()
        if "username" in message:
            raise DuplicateUsernameError(normalized_username) from exc
        raise DuplicateEmailError(normalized_email) from exc
    await session.refresh(user)
    return user


async def authenticate_user(
    session: AsyncSession, *, email: str, password: str
) -> Optional[User]:
    user = await get_user_by_email(session, email=email)
    if not user:
        return None
    try:
        if not verify_password(password, user.hashed_password):
            return None
    except ValueError:
        logger.exception("Invalid password hash for user %s", user.email)
        return None
    except Exception:  # pragma: no cover - defensive guard
        logger.exception("Unexpected error while verifying password for %s", user.email)
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
        statement = statement.where(
            or_(User.email.ilike(term), User.username.ilike(term))
        )
        count_statement = count_statement.where(
            or_(User.email.ilike(term), User.username.ilike(term))
        )

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
