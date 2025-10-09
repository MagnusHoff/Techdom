from __future__ import annotations

import os
import warnings
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

try:
    from jose import jwt  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    jwt = None  # type: ignore
from passlib.context import CryptContext


_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _resolve_secret_key() -> str:
    secret = os.getenv("AUTH_SECRET_KEY")
    if secret:
        return secret

    fallback = "dev-secret-change-me"
    warnings.warn(
        "AUTH_SECRET_KEY is not set. Using an insecure development key. "
        "Set AUTH_SECRET_KEY in production!",
        RuntimeWarning,
        stacklevel=2,
    )
    return fallback


SECRET_KEY = _resolve_secret_key()
ALGORITHM = os.getenv("AUTH_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return _pwd_context.verify(plain_password, hashed_password)


def create_access_token(*, data: Dict[str, Any], expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    if jwt is None:
        raise RuntimeError("python-jose er ikke installert. Kan ikke generere JWT.")
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Dict[str, Any]:
    if jwt is None:
        raise RuntimeError("python-jose er ikke installert. Kan ikke dekode JWT.")
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
