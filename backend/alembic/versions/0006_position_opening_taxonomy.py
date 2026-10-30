"""re-label position.eco / opening_name from the Lichess opening taxonomy

Before this revision, PGN imports copied a game's ECO/Opening headers onto every
position of that game, so the start position carried whichever opening was imported
first. Positions are now named only when they are a named position in
``chessmind/data/openings`` (see ``services/opening_taxonomy.py``); every other
position's name is cleared. Data-only; downgrade is a no-op.

Revision ID: 0006_position_opening_taxonomy
Revises: 0005_masters_cache_questions
Create Date: 2026-10-30

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_position_opening_taxonomy"
down_revision: Union[str, None] = "0005_masters_cache_questions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from chessmind.services.opening_taxonomy import taxonomy

    by_key = taxonomy().by_key
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE position SET eco = NULL, opening_name = NULL"))
    keys = [r[0] for r in conn.execute(sa.text("SELECT canonical_key FROM position"))]
    stmt = sa.text("UPDATE position SET eco = :eco, opening_name = :name WHERE canonical_key = :key")
    rows = [{"eco": by_key[k].eco, "name": by_key[k].name, "key": k} for k in keys if k in by_key]
    if rows:
        conn.execute(stmt, rows)


def downgrade() -> None:
    pass
