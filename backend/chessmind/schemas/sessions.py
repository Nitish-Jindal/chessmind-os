"""Request/response shapes for /sessions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


SessionMode = Literal["guided", "blindfold", "tournament", "visualization_q", "opponent"]
SamplerModeLit = Literal["mainline", "mix", "surprise", "opponent_twin"]
PlayerColor = Literal["white", "black"]
NextToMove = Literal["player", "opponent"]
SessionStatus = Literal["active", "finished"]


class SessionCreate(BaseModel):
    mode: SessionMode = "guided"
    player_color: PlayerColor = "white"
    sampler_mode: SamplerModeLit = "mix"
    seed: int | None = Field(default=None, description="Omit to generate; set for reproducible replays.")
    opponent_profile_id: UUID | None = None
    starting_fen: str | None = None
    opening_scope_json: dict[str, Any] = Field(default_factory=dict)
    novelty_prob: float = Field(default=0.1, ge=0.0, le=1.0)


class SessionState(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    player_id: UUID
    mode: SessionMode
    opponent_profile_id: UUID | None
    seed: int
    settings_json: dict[str, Any]
    state_json: dict[str, Any]
    opening_scope_json: dict[str, Any]
    started_at: datetime
    ended_at: datetime | None


class LegalMovesResponse(BaseModel):
    session_id: UUID
    current_fen: str
    next_to_move: NextToMove
    status: SessionStatus
    legal_moves_san: list[str]
    legal_moves_uci: list[str]
    reveals_used: int
    reveals_budget: int
    hints_used: int


class MoveRequest(BaseModel):
    """Player move. Either ``san`` or ``uci`` must be provided."""

    san: str | None = None
    uci: str | None = None
    latency_ms: int = Field(default=0, ge=0)


class EngineLine(BaseModel):
    line_rank: int
    cp: int | None
    mate: int | None
    pv_san: list[str]
    pv_uci: list[str]


class CoachingPayload(BaseModel):
    spoken_summary: str
    correction_san: str | None
    evidence_refs: list[str]
    board_features: dict[str, Any]


class MoveResponse(BaseModel):
    session_id: UUID
    attempt_id: UUID
    played_san: str
    played_uci: str
    classification: str
    eval_before_cp_white: int | None
    eval_after_cp_white: int | None
    eval_swing_cp: int
    player_relative_before_cp: int
    player_relative_after_cp: int
    mate_flip: str | None
    engine_version: str
    engine_depth: int
    engine_multipv: int
    best_lines: list[EngineLine]
    coaching: CoachingPayload
    opponent_move: dict[str, Any] | None
    current_fen: str
    status: SessionStatus


class RevealRequest(BaseModel):
    reason: str | None = None


class RevealResponse(BaseModel):
    session_id: UUID
    reveals_used: int
    reveals_budget: int
    hints_used: int
    current_fen: str
    was_free: bool


class FinishResponse(BaseModel):
    session_id: UUID
    status: SessionStatus
    ended_at: datetime | None
