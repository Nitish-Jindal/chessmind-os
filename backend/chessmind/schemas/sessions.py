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
    quiet: bool = Field(default=False, description="Quiet mode: no coaching prose, quizzes or opening announcements.")
    opening_request: str | None = Field(
        default=None, max_length=200,
        description='Opening for the opponent to play: "Caro-Kann", "London System", "d4", '
                    'or a full phrase such as "play the Sicilian against me".',
    )


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


class OpeningAnnouncement(BaseModel):
    eco: str
    name: str
    pgn: str
    ply: int
    by: Literal["player", "opponent"]
    text: str


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
    quiet: bool = False
    announcements: list[OpeningAnnouncement] = Field(default_factory=list)
    opening: dict[str, Any] | None = None
    opening_request: dict[str, Any] | None = None


class IllegalMoveDetail(BaseModel):
    """``detail`` of the 422 returned when no legal move matches the submitted move."""

    code: Literal["illegal_move"] = "illegal_move"
    message: str
    input: str
    current_fen: str


class SessionSettingsPatch(BaseModel):
    quiet: bool


class OpeningCommandResponse(BaseModel):
    session_id: UUID
    understood: bool
    accepted: bool
    message: str
    restarted: bool = False
    opening_request: dict[str, Any] | None = None
    opponent_move: dict[str, Any] | None = None
    current_fen: str


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


# --------------------------------------------------------------------------- #
# Position questions (handoff §3.3 voice questions)
# --------------------------------------------------------------------------- #

QuestionKindLit = Literal["piece_location", "square_content", "hanging_pieces", "side_to_move", "evaluation"]


class UtteranceRequest(BaseModel):
    """What the player said or typed. ``alternatives`` are extra STT hypotheses, best first."""

    text: str = Field(min_length=1, max_length=500)
    alternatives: list[str] = Field(default_factory=list, max_length=10)

    def all_texts(self) -> list[str]:
        return [self.text, *[a for a in self.alternatives if a and a != self.text]]


class QuestionTally(BaseModel):
    asked: int
    quizzed: int
    answered: int
    correct: int
    mean_score: float | None


class AskResponse(BaseModel):
    session_id: UUID
    understood: bool
    question_id: UUID | None
    kind: QuestionKindLit | None
    answer_text: str
    facts: dict[str, Any]
    questions_asked: int


class QuizRequest(BaseModel):
    kind: QuestionKindLit | None = Field(default=None, description="Omit for a seeded random kind.")


class QuizQuestion(BaseModel):
    session_id: UUID
    question_id: UUID
    kind: QuestionKindLit
    prompt: str


class QuizAnswerResponse(BaseModel):
    session_id: UUID
    question_id: UUID
    understood: bool
    correct: bool
    score: float | None
    expected_text: str | None
    feedback: str
    tally: QuestionTally
