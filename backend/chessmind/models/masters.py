"""Cache of Lichess Masters opening-explorer responses, keyed by canonical FEN.

One row per position ever looked up. ``payload_json`` keeps the parsed,
python-chess-validated snapshot that the API serves; ``fetched_at`` drives
the TTL. Per-move counts are also written to ``master_move_stat`` so the
opponent sampler's master tier runs on the same real data.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from chessmind.models.base import Base, Timestamped, UUIDPrimaryKey


class MasterExplorerCache(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "master_explorer_cache"

    canonical_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    fen: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="lichess_masters")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    total_games: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
