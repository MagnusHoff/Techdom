from __future__ import annotations

from datetime import datetime
from typing import Iterable

try:
    from pydantic import (  # type: ignore
        BaseModel,
        ConfigDict,
        Field,
        ValidationInfo,
        field_validator,
    )
except ImportError:  # pragma: no cover - compatibility with Pydantic v1
    from pydantic import BaseModel, Field, ValidationInfo, validator as field_validator  # type: ignore

    ConfigDict = None  # type: ignore[assignment]


def _trim(value: str | None, *, max_length: int | None = None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if max_length is not None and len(stripped) > max_length:
        return stripped[:max_length]
    return stripped


def _normalise_key(value: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        raise ValueError("analysis_key kan ikke være tom")
    return trimmed[:255]


class SavedAnalysisBase(BaseModel):
    analysis_key: str = Field(min_length=1, max_length=255)
    title: str | None = Field(default=None, max_length=255)
    address: str | None = Field(default=None, max_length=255)
    image_url: str | None = Field(default=None, max_length=500)
    total_score: int | None = Field(default=None, ge=0, le=100)
    risk_level: str | None = Field(default=None, max_length=120)
    price: int | None = Field(default=None, ge=0)
    finnkode: str | None = Field(default=None, max_length=32)
    summary: str | None = Field(default=None, max_length=4000)
    source_url: str | None = Field(default=None, max_length=500)

    @field_validator("analysis_key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        return _normalise_key(value)

    @field_validator(
        "title",
        "address",
        "image_url",
        "risk_level",
        "finnkode",
        "summary",
        "source_url",
        mode="before",
    )
    @classmethod
    def trim_optional_strings(cls, value: str | None, info: ValidationInfo) -> str | None:  # type: ignore[override]
        max_length = None
        field = info.field_name if isinstance(info.field_name, str) else None
        if field == "title" or field == "address":
            max_length = 255
        elif field == "image_url" or field == "source_url":
            max_length = 500
        elif field == "risk_level":
            max_length = 120
        elif field == "finnkode":
            max_length = 32
        elif field == "summary":
            max_length = 4000
        return _trim(value, max_length=max_length)

    @field_validator("total_score", "price", mode="before")
    @classmethod
    def normalise_numeric(cls, value: int | float | str | None, info: ValidationInfo) -> int | None:
        if value is None or value == "":
            return None
        if isinstance(value, (int,)):
            return value
        if isinstance(value, float):
            if not value == value:  # NaN check
                return None
            return round(value)
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            try:
                numeric = float(stripped)
            except ValueError as exc:  # pragma: no cover - defensive
                raise ValueError("Ugyldig tallverdi") from exc
            if not numeric == numeric:  # NaN check
                return None
            return round(numeric)
        return None


class SavedAnalysisCreate(SavedAnalysisBase):
    pass


class SavedAnalysisRead(SavedAnalysisBase):
    id: str
    saved_at: datetime

    if ConfigDict is not None:  # pragma: no branch - depends on pydantic version
        model_config = ConfigDict(from_attributes=True)
    else:  # pragma: no cover - Pydantic v1 fallback
        class Config:
            orm_mode = True


class SavedAnalysisCollection(BaseModel):
    items: list[SavedAnalysisRead] = Field(default_factory=list)

    @classmethod
    def from_iterable(cls, items: Iterable[SavedAnalysisRead | SavedAnalysisBase | object]) -> "SavedAnalysisCollection":
        return cls(items=[SavedAnalysisRead.model_validate(item) for item in items])
