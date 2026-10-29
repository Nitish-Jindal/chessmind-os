"""Mastery response shapes."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MasteryRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    position_id: UUID
    canonical_key: str
    fen: str
    opening_name: str | None
    state: str
    recall_score: float
    understanding_score: float
    visualization_score: float
    speed_score: float
    due_at: datetime
    last_tested_at: datetime | None
    ease_factor: float
    interval_days: float
    reps: int


class MasteryByState(BaseModel):
    untested: int = 0
    emerging: int = 0
    competent: int = 0
    mastered: int = 0
    decayed: int = 0


class MasteryDashboard(BaseModel):
    counts_by_state: MasteryByState
    due_now: int
    due_next_24h: int
    weakest: list[MasteryRow] = Field(default_factory=list)
    due_soon: list[MasteryRow] = Field(default_factory=list)


class NextDrillResponse(BaseModel):
    due_now: bool
    row: MasteryRow | None
    reason: str
