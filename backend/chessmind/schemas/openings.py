"""Response shapes for /openings/tree."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OpeningEdge(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uci: str
    san: str
    to_position_id: UUID
    games_count: int
    white_wins: int
    black_wins: int
    draws: int


class OpeningNode(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    position_id: UUID
    canonical_key: str
    fen: str
    eco: str | None
    opening_name: str | None
    popularity: int
    edges: list[OpeningEdge] = Field(default_factory=list)


class OpeningTreeResponse(BaseModel):
    root: OpeningNode
    total_positions: int
    total_edges: int
