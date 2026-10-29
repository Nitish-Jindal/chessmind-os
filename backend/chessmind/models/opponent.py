"""Opponent profile + materialized conditional move stats + master move stats.

OpponentPositionMove is the per-opponent conditional-frequency row rebuilt
after each opponent PGN import. MasterMoveStat is the ARCHITECTURE.md §8
"master play / Lichess masters" tier of the sampler fallback chain — Day-1
leaves it empty by default, Day-4 can seed it from a bulk master-games
import without touching the sampler code.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from chessmind.models.base import Base, Timestamped, UUIDPrimaryKey


class OpponentProfile(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "opponent_profile"

    owner_player_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("player.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    aliases_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    games_imported: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_import_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OpponentPositionMove(Base, UUIDPrimaryKey, Timestamped):
    """One row per (opponent, position, color, move) with aggregate stats."""

    __tablename__ = "opponent_position_move"

    opponent_profile_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("opponent_profile.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("position.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    color: Mapped[str] = mapped_column(String(5), nullable=False)  # "white" | "black"
    move_uci: Mapped[str] = mapped_column(String(10), nullable=False)
    games_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    losses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    draws: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recency_weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    sample_size_bucket: Mapped[str] = mapped_column(String(10), nullable=False, default="small")

    __table_args__ = (
        UniqueConstraint(
            "opponent_profile_id", "position_id", "color", "move_uci",
            name="uq_opponent_position_move",
        ),
    )


class MasterMoveStat(Base, UUIDPrimaryKey, Timestamped):
    """Master-level move frequencies. Seeded from a master DB post-MVP.

    Day-1 leaves the table empty; the sampler simply falls through to
    the opening_graph tier when there are no rows for a position.
    """

    __tablename__ = "master_move_stat"

    position_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("position.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="lichess_masters")
    move_uci: Mapped[str] = mapped_column(String(10), nullable=False)
    games_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    white_wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    black_wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    draws: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("position_id", "source", "move_uci", name="uq_master_move_stat"),
    )
