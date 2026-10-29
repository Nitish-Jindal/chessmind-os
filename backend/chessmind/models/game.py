"""Game, RepertoireLine, ImportJob — the ingest side of the model."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from chessmind.models.base import Base, Timestamped, UUIDPrimaryKey


class ImportJob(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "import_job"

    player_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("player.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # 'player_pgn' | 'corpus_pgn' | 'opponent_pgn' | 'seed'
    opponent_profile_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    # kept nullable; opponent_profile table lands in Phase 4
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="upload")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    # 'queued' | 'running' | 'succeeded' | 'failed' | 'partial'
    progress_games: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_games: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifacts_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Game(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "game"

    player_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("player.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="upload")
    pgn: Mapped[str] = mapped_column(Text, nullable=False)
    imported_from_batch: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("import_job.id", ondelete="SET NULL"), nullable=True
    )
    white_name: Mapped[str] = mapped_column(String(200), nullable=False, default="?")
    black_name: Mapped[str] = mapped_column(String(200), nullable=False, default="?")
    white_rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    black_rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result: Mapped[str] = mapped_column(String(8), nullable=False, default="*")
    date_played: Mapped[date | None] = mapped_column(Date, nullable=True)
    time_control: Mapped[str | None] = mapped_column(String(32), nullable=True)
    eco: Mapped[str | None] = mapped_column(String(5), nullable=True)
    provenance_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class RepertoireLine(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "repertoire_line"

    player_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("player.id", ondelete="CASCADE"), nullable=False, index=True
    )
    color: Mapped[str] = mapped_column(String(5), nullable=False)  # 'white' | 'black'
    from_position_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("position.id", ondelete="CASCADE"), nullable=False, index=True
    )
    preferred_uci: Mapped[str] = mapped_column(String(10), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    is_mainline: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source_game_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("game.id", ondelete="SET NULL"), nullable=True
    )
    source_variation_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    nags_json: Mapped[list[int]] = mapped_column(JSONB, nullable=False, default=list)

    __table_args__ = (
        UniqueConstraint(
            "player_id", "color", "from_position_id", "preferred_uci",
            name="uq_repertoire_line_player_color_from_uci",
        ),
    )
