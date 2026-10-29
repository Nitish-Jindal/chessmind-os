"""TrainingSession, TrainingAttempt, MasteryState."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from chessmind.models.base import Base, Timestamped, UUIDPrimaryKey


class TrainingSession(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "training_session"

    player_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("player.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    # 'guided' | 'blindfold' | 'tournament' | 'visualization_q' | 'opponent'
    opening_scope_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    opponent_profile_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("opponent_profile.id", ondelete="SET NULL"),
        nullable=True,
    )
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    settings_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # Live FSM blob: {current_fen, move_history_san, player_color, next_to_move,
    #                 reveals_used, hints_used, status}
    state_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TrainingAttempt(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "training_attempt"

    session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("training_session.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("position.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    played_uci: Mapped[str] = mapped_column(String(10), nullable=False)
    best_uci_list_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    classification: Mapped[str] = mapped_column(String(16), nullable=False)
    # 'best' | 'playable' | 'inaccuracy' | 'mistake' | 'blunder'
    eval_before_cp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    eval_after_cp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    eval_swing_cp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mate_flip: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # 'lost_mate' | 'allowed_mate' | None
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hints_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    visualization_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    feedback_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class MasteryState(Base, UUIDPrimaryKey, Timestamped):
    """SM-2 state per (player, position)."""

    __tablename__ = "mastery_state"

    player_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("player.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("position.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    recall_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    understanding_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    visualization_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    speed_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="untested")
    # 'untested' | 'emerging' | 'competent' | 'mastered' | 'decayed'
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # SM-2 state
    ease_factor: Mapped[float] = mapped_column(Float, nullable=False, default=2.5)
    interval_days: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    reps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("player_id", "position_id", name="uq_mastery_player_position"),
    )
