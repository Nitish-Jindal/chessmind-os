"""Request/response shapes for /imports."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ImportPgnRequest(BaseModel):
    pgn: str = Field(min_length=1, description="Raw PGN text; may contain many games.")
    source: str = Field(default="upload", max_length=64)


class ImportJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: str
    source: str
    status: str
    progress_games: int
    total_games: int
    error: str | None
    artifacts_json: dict[str, Any]
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
