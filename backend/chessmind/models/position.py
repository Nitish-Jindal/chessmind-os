"""Position and MoveEdge — the opening graph vertices and edges."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from chessmind.models.base import Base, Timestamped, UUIDPrimaryKey


class Position(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "position"

    canonical_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False, index=True)
    fen: Mapped[str] = mapped_column(Text, nullable=False)
    eco: Mapped[str | None] = mapped_column(String(5), nullable=True)
    opening_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    features_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    popularity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class MoveEdge(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "move_edge"

    from_position_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("position.id", ondelete="CASCADE"), nullable=False, index=True
    )
    to_position_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("position.id", ondelete="CASCADE"), nullable=False
    )
    uci: Mapped[str] = mapped_column(String(10), nullable=False)
    san: Mapped[str] = mapped_column(String(10), nullable=False)
    games_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    white_wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    black_wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    draws: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (UniqueConstraint("from_position_id", "uci", name="uq_move_edge_from_uci"),)
