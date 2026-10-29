"""Request/response shapes for /opponents."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OpponentCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list)


class OpponentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_player_id: UUID
    display_name: str
    aliases_json: list[str]
    games_imported: int
    last_import_at: datetime | None
    created_at: datetime


class OpponentPgnImport(BaseModel):
    pgn: str = Field(min_length=1)
    source: str = Field(default="upload", max_length=64)


class OpponentTreeEdge(BaseModel):
    move_uci: str
    move_san: str
    games_count: int
    wins: int
    losses: int
    draws: int
    recency_weight: float
    sample_size_bucket: str
    confidence: float


class OpponentTreeResponse(BaseModel):
    opponent_profile_id: UUID
    position_canonical_key: str
    position_fen: str
    opponent_color: str
    edges: list[OpponentTreeEdge]
    sample_total: int
