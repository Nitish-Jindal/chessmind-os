"""Schemas for /analysis/position."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AnalysisRequest(BaseModel):
    fen: str
    depth: int | None = Field(default=None, ge=1, le=40)
    multipv: int | None = Field(default=None, ge=1, le=10)


class AnalysisResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    position_id: UUID
    engine_version: str
    depth: int
    multipv: int
    nodes: int
    time_ms: int
    perspective: str
    results: list[dict[str, Any]]
    cached: bool
