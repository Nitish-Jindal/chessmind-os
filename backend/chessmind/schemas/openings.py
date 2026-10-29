"""Response shapes for /openings/tree."""

from __future__ import annotations

from datetime import datetime
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


class MasterMoveOut(BaseModel):
    uci: str
    san: str
    games: int
    white: int
    draws: int
    black: int
    average_rating: int | None


class MastersBlock(BaseModel):
    """Real master-game statistics for the root position, kept separate from local counts."""

    source: str
    attribution: str
    explorer_url: str
    status: str  # live | cached | stale | unavailable | disabled
    fetched_at: datetime | None
    error: str | None
    total_games: int
    white: int
    draws: int
    black: int
    opening_eco: str | None
    opening_name: str | None
    moves: list[MasterMoveOut] = Field(default_factory=list)


LOCAL_STATS_NOTE = (
    "Local counts: games imported into this ChessMind instance, including the seed set, "
    "whose results are assigned rather than real."
)


class OpeningTreeResponse(BaseModel):
    root: OpeningNode
    total_positions: int
    total_edges: int
    local_stats_note: str = LOCAL_STATS_NOTE
    masters: MastersBlock | None = None
