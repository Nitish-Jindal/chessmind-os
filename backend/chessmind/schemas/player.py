"""Pydantic schemas for authentication and player profile."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class PlayerCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    display_name: str = Field(min_length=1, max_length=120)
    preferred_color: Literal["white", "black", "either"] = "either"


class PlayerLogin(BaseModel):
    email: EmailStr
    password: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class PlayerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    display_name: str
    rating_fide: int | None = None
    rating_lichess: int | None = None
    rating_chesscom: int | None = None
    preferred_color: Literal["white", "black", "either"]
    age_band: str | None = None
    role: str
    settings_json: dict[str, Any]


class PlayerUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    rating_fide: int | None = None
    rating_lichess: int | None = None
    rating_chesscom: int | None = None
    preferred_color: Literal["white", "black", "either"] | None = None
    age_band: str | None = Field(default=None, max_length=20)
    settings_json: dict[str, Any] | None = None

    @field_validator("rating_fide", "rating_lichess", "rating_chesscom")
    @classmethod
    def _rating_bounds(cls, v: int | None) -> int | None:
        if v is None:
            return v
        if v < 0 or v > 4000:
            raise ValueError("rating must be between 0 and 4000")
        return v
