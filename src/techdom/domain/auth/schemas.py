from __future__ import annotations

import re

from pydantic import BaseModel, EmailStr, Field, field_validator

from techdom.domain.auth.models import UserRole


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
    role: UserRole
    is_active: bool
    is_email_verified: bool

    class Config:
        from_attributes = True


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


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AuthResponse(Token):
    user: UserRead


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
