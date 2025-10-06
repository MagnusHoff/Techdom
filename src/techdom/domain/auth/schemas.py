from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field

from techdom.domain.auth.models import UserRole


class UserBase(BaseModel):
    email: EmailStr


class UserCreate(UserBase):
    password: str = Field(min_length=8)


class UserRead(UserBase):
    id: int
    role: UserRole
    is_active: bool

    class Config:
        from_attributes = True


class UserLogin(UserBase):
    password: str = Field(min_length=1)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AuthResponse(Token):
    user: UserRead
