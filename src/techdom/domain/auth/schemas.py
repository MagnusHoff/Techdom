from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

try:
    from pydantic import (  # type: ignore
        BaseModel,
        ConfigDict,
        EmailStr,
        Field,
        field_validator,
    )
except ImportError:  # pragma: no cover - compatibility with Pydantic v1
    from pydantic import BaseModel, EmailStr, Field, validator as field_validator  # type: ignore

    ConfigDict = None  # type: ignore[assignment]

from techdom.domain.auth.models import UserRole
from techdom.domain.auth.constants import normalise_avatar_emoji, normalise_avatar_color


class UserBase(BaseModel):
    email: EmailStr


class UserCreate(UserBase):
    username: str = Field(min_length=3, max_length=20)
    password: str = Field(min_length=8)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        stripped = value.strip()
        pattern = re.compile(r"^[a-zA-Z0-9._]{3,20}$")
        if not pattern.fullmatch(stripped):
            raise ValueError(
                "Brukernavn må være 3-20 tegn og kan kun inneholde bokstaver, tall, punktum og understrek."
            )
        return stripped

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        pattern = re.compile(
            r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>/?]).{8,}$"
        )
        if not pattern.fullmatch(value):
            raise ValueError(
                "Passordet må være minst 8 tegn og inneholde store og små bokstaver, tall og spesialtegn."
            )
        return value


class UserRead(UserBase):
    id: int
    username: str | None = None
    avatar_emoji: str | None = None
    avatar_color: str | None = None
    role: UserRole
    is_active: bool
    is_email_verified: bool
    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None
    subscription_status: str | None = None
    subscription_price_id: str | None = None
    subscription_current_period_end: datetime | None = None
    subscription_cancel_at_period_end: bool = False
    created_at: datetime
    updated_at: datetime
    total_analyses: int = 0

    if ConfigDict is not None:  # pragma: no branch - depends on pydantic version
        model_config = ConfigDict(from_attributes=True)
    else:  # pragma: no cover - Pydantic v1 fallback
        class Config:
            orm_mode = True


class UserLogin(UserBase):
    password: str = Field(min_length=1)


class UserCollection(BaseModel):
    total: int
    items: list[UserRead]


class UpdateUserRole(BaseModel):
    role: UserRole


class UpdateUsername(BaseModel):
    username: str = Field(min_length=3, max_length=20)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        stripped = value.strip()
        pattern = re.compile(r"^[a-zA-Z0-9._]{3,20}$")
        if not pattern.fullmatch(stripped):
            raise ValueError(
                "Brukernavn må være 3-20 tegn og kan kun inneholde bokstaver, tall, punktum og understrek."
            )
        return stripped


class UpdateAvatar(BaseModel):
    avatar_emoji: str | None = None
    avatar_color: str | None = None

    @field_validator("avatar_emoji")
    @classmethod
    def validate_avatar(cls, value: str | None) -> str | None:
        normalised = normalise_avatar_emoji(value)
        if value is None or normalised is not None:
            return normalised
        raise ValueError("Ugyldig emoji-valg. Velg en av de tilgjengelige alternativene.")

    @field_validator("avatar_color")
    @classmethod
    def validate_avatar_color(cls, value: str | None) -> str | None:
        normalised = normalise_avatar_color(value)
        if value is None or normalised is not None:
            return normalised
        raise ValueError("Ugyldig bakgrunnsfarge.")


class ChangePassword(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8)

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        pattern = re.compile(
            r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>/?]).{8,}$"
        )
        if not pattern.fullmatch(value):
            raise ValueError(
                "Passordet må være minst 8 tegn og inneholde store og små bokstaver, tall og spesialtegn."
            )
        return value



class AdminUpdateUser(BaseModel):
    username: str = Field(min_length=3, max_length=20)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        stripped = value.strip()
        pattern = re.compile(r"^[a-zA-Z0-9._]{3,20}$")
        if not pattern.fullmatch(stripped):
            raise ValueError(
                "Brukernavn må være 3-20 tegn og kan kun inneholde bokstaver, tall, punktum og understrek."
            )
        return stripped


class AdminChangeUserPassword(BaseModel):
    new_password: str = Field(min_length=8)

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        pattern = re.compile(
            r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>/?]).{8,}$"
        )
        if not pattern.fullmatch(value):
            raise ValueError(
                "Passordet må være minst 8 tegn og inneholde store og små bokstaver, tall og spesialtegn."
            )
        return value



class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AuthResponse(Token):
    user: UserRead


class UserStatus(BaseModel):
    total_user_analyses: int
    total_last_7_days: int
    last_run_at: datetime | None


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=1)
    password: str = Field(min_length=8)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        pattern = re.compile(
            r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>/?]).{8,}$"
        )
        if not pattern.fullmatch(value):
            raise ValueError(
                "Passordet må være minst 8 tegn og inneholde store og små bokstaver, tall og spesialtegn."
            )
        return value


class EmailVerificationConfirm(BaseModel):
    token: str = Field(min_length=1)


class SubscriptionCheckoutRequest(BaseModel):
    billing_interval: Literal["monthly", "yearly"]


class SubscriptionCheckoutResponse(BaseModel):
    checkout_url: str = Field(min_length=1)


class SubscriptionPortalResponse(BaseModel):
    portal_url: str = Field(min_length=1)


class EmailVerificationResend(BaseModel):
    email: EmailStr


class IncrementAnalyses(BaseModel):
    increment: int = Field(default=1, ge=1, le=1000)
