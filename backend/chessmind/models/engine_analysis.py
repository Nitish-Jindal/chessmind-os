"""EngineAnalysis — cached Stockfish MultiPV results, always White-centric."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from chessmind.models.base import Base, Timestamped, UUIDPrimaryKey


class EngineAnalysis(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "engine_analysis"

    position_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("position.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    engine: Mapped[str] = mapped_column(String(32), nullable=False, default="stockfish")
    engine_version: Mapped[str] = mapped_column(String(32), nullable=False)
    depth: Mapped[int] = mapped_column(Integer, nullable=False)
    multipv: Mapped[int] = mapped_column(Integer, nullable=False)
    nodes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    time_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    perspective: Mapped[str] = mapped_column(String(5), nullable=False, default="white")
    # results_json: [{line_rank, cp, mate, pv_uci[], pv_san[]}, ...]
    results_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    cached_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index(
            "ix_engine_analysis_pos_version_depth",
            "position_id",
            "engine_version",
            "depth",
        ),
    )
