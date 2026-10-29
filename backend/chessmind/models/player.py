"""Player — the authenticated user and their preferences.

``settings_json`` is a free-form jsonb bag. Keys we rely on in Day 1:

- ``blindfold_free_reveals_per_session`` (int, default 2) — §11.3 budget.
- ``blindfold_reveal_penalty`` (float, default 0.15) — visualization score drop
  for each reveal beyond the budget.
- ``tts_voice`` ({"name": str, "lang": str}) — persisted voice choice (§11.5).

Settings are advisory from the model's POV; business logic reads them with
defaults so missing keys never crash a session.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import CITEXT, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from chessmind.models.base import Base, Timestamped, UUIDPrimaryKey


class Player(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "player"

    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    rating_fide: Mapped[int | None] = mapped_column(nullable=True)
    rating_lichess: Mapped[int | None] = mapped_column(nullable=True)
    rating_chesscom: Mapped[int | None] = mapped_column(nullable=True)
    preferred_color: Mapped[str] = mapped_column(String(10), nullable=False, default="either")
    age_band: Mapped[str | None] = mapped_column(String(20), nullable=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="player")
    settings_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
