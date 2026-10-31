"""Upsert helpers for Position, MoveEdge, and RepertoireLine.

Postgres ``INSERT ... ON CONFLICT`` keeps these idempotent so re-importing
the same PGN does not create duplicates or inflate counts.
"""

from __future__ import annotations

from typing import Iterable
from uuid import UUID, uuid4

import chess
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from chessmind.models import MoveEdge, Position, RepertoireLine
from chessmind.services import opening_taxonomy
from chessmind.services.canonical_fen import canonical_key


async def upsert_position(
    session: AsyncSession,
    board: chess.Board,
    *,
    eco: str | None = None,
    opening_name: str | None = None,
) -> UUID:
    """Return the id of the Position row for ``board``, creating it if needed.

    ``eco`` / ``opening_name`` come from the opening taxonomy when the position is a named
    one. The arguments are only a fallback for positions the taxonomy does not name, and
    callers pass them only when they describe this exact position (not a whole game).
    """
    key = canonical_key(board)
    named = opening_taxonomy.lookup_key(key)
    if named is not None:
        eco, opening_name = named.eco, named.name
    stmt = (
        pg_insert(Position)
        .values(
            id=uuid4(),
            canonical_key=key,
            fen=board.fen(),
            eco=eco,
            opening_name=opening_name,
            features_json={},
            popularity=0,
        )
        .on_conflict_do_nothing(index_elements=["canonical_key"])
        .returning(Position.id)
    )
    result = await session.execute(stmt)
    row = result.first()
    if row is not None:
        return row[0]
    # Already existed — fetch its id.
    existing = await session.execute(select(Position.id).where(Position.canonical_key == key))
    return existing.scalar_one()


async def upsert_move_edge(
    session: AsyncSession,
    *,
    from_position_id: UUID,
    to_position_id: UUID,
    uci: str,
    san: str,
    result: str | None,
) -> UUID:
    """Idempotent upsert that also bumps the aggregate counts for ``result``."""
    counts = _result_to_counts(result)
    stmt = (
        pg_insert(MoveEdge)
        .values(
            id=uuid4(),
            from_position_id=from_position_id,
            to_position_id=to_position_id,
            uci=uci,
            san=san,
            games_count=1,
            white_wins=counts["white_wins"],
            black_wins=counts["black_wins"],
            draws=counts["draws"],
        )
        .on_conflict_do_update(
            index_elements=["from_position_id", "uci"],
            set_={
                "games_count": MoveEdge.__table__.c.games_count + 1,
                "white_wins": MoveEdge.__table__.c.white_wins + counts["white_wins"],
                "black_wins": MoveEdge.__table__.c.black_wins + counts["black_wins"],
                "draws": MoveEdge.__table__.c.draws + counts["draws"],
            },
        )
        .returning(MoveEdge.id)
    )
    result_row = await session.execute(stmt)
    return result_row.scalar_one()


async def bump_position_popularity(session: AsyncSession, position_ids: Iterable[UUID]) -> None:
    """Increment popularity on each position touched by this import."""
    ids = list(position_ids)
    if not ids:
        return
    await session.execute(
        update(Position).where(Position.id.in_(ids)).values(popularity=Position.popularity + 1)
    )


async def upsert_repertoire_line(
    session: AsyncSession,
    *,
    player_id: UUID,
    color: str,
    from_position_id: UUID,
    preferred_uci: str,
    is_mainline: bool,
    source_game_id: UUID | None,
    source_variation_path: str | None,
    comments: str | None,
    nags: list[int],
) -> None:
    """Upsert a (player, color, from_position, uci) tuple.

    is_mainline is OR'd across every occurrence: once a move is seen in a
    mainline anywhere, we record it as a mainline move for the player.
    """
    stmt = (
        pg_insert(RepertoireLine)
        .values(
            id=uuid4(),
            player_id=player_id,
            color=color,
            from_position_id=from_position_id,
            preferred_uci=preferred_uci,
            priority=100,
            is_mainline=is_mainline,
            source_game_id=source_game_id,
            source_variation_path=source_variation_path,
            comments=comments,
            nags_json=nags,
        )
        .on_conflict_do_update(
            index_elements=["player_id", "color", "from_position_id", "preferred_uci"],
            set_={
                "is_mainline": RepertoireLine.__table__.c.is_mainline | is_mainline,
            },
        )
    )
    await session.execute(stmt)


def _result_to_counts(result: str | None) -> dict[str, int]:
    if result == "1-0":
        return {"white_wins": 1, "black_wins": 0, "draws": 0}
    if result == "0-1":
        return {"white_wins": 0, "black_wins": 1, "draws": 0}
    if result == "1/2-1/2":
        return {"white_wins": 0, "black_wins": 0, "draws": 1}
    return {"white_wins": 0, "black_wins": 0, "draws": 0}
