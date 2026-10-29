"""Position questions asked during a session (player → board, or quiz → player).

``mode='ask'``: the player asked about the current board; ``answer_json``
holds the facts python-chess (and the cached Stockfish row) produced.
``mode='quiz'``: the system asked a visualization question; ``expected_json``
is the ground truth computed from the FEN at question time, and the
player's answer is scored against it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from chessmind.models.base import Base, Timestamped, UUIDPrimaryKey


class PositionQuestion(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "position_question"

    session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("training_session.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    fen: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)  # 'ask' | 'quiz'
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    expected_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    answer_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
