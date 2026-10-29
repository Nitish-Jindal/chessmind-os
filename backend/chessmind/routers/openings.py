"""/openings/tree — read-only query over the opening graph."""

from __future__ import annotations

from uuid import UUID

import chess
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chessmind.db import get_session
from chessmind.models import MoveEdge, Position
from chessmind.schemas.openings import OpeningEdge, OpeningNode, OpeningTreeResponse
from chessmind.services.canonical_fen import canonical_key

router = APIRouter(prefix="/openings", tags=["openings"])


async def _resolve_root(
    session: AsyncSession,
    *,
    position_id: UUID | None,
    canonical: str | None,
    fen: str | None,
) -> Position:
    if position_id is not None:
        pos = await session.get(Position, position_id)
        if pos is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "position not found")
        return pos

    key: str | None = canonical
    if key is None and fen is not None:
        try:
            board = chess.Board(fen)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid FEN: {exc}") from exc
        key = canonical_key(board)

    if key is None:
        key = canonical_key(chess.Board())  # starting position fallback

    row = (await session.execute(select(Position).where(Position.canonical_key == key))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "position not in graph yet")
    return row


@router.get("/tree", response_model=OpeningTreeResponse)
async def opening_tree(
    position_id: UUID | None = Query(default=None),
    canonical: str | None = Query(default=None, description="EPD-style canonical key"),
    fen: str | None = Query(default=None, description="Full FEN; normalized to canonical_key"),
    limit: int = Query(default=20, ge=1, le=100, description="Max edges returned from the root."),
    min_games: int = Query(default=0, ge=0, description="Only return edges with at least this many games."),
    session: AsyncSession = Depends(get_session, scope="function"),
) -> OpeningTreeResponse:
    """Return the root position and its outgoing edges, ordered by games_count desc.

    §11.4 resolution: no cursor pagination — a bounded ``limit`` + ``min_games``
    filter is the entire pagination story. The top of an opening tree is
    narrow enough that one bounded payload is what every consumer wants.
    """
    root = await _resolve_root(session, position_id=position_id, canonical=canonical, fen=fen)

    edges_stmt = (
        select(MoveEdge)
        .where(MoveEdge.from_position_id == root.id, MoveEdge.games_count >= min_games)
        .order_by(MoveEdge.games_count.desc(), MoveEdge.san.asc())
        .limit(limit)
    )
    edges = (await session.execute(edges_stmt)).scalars().all()

    total_positions = (await session.execute(select(func.count(Position.id)))).scalar_one()
    total_edges = (await session.execute(select(func.count(MoveEdge.id)))).scalar_one()

    return OpeningTreeResponse(
        root=OpeningNode(
            position_id=root.id,
            canonical_key=root.canonical_key,
            fen=root.fen,
            eco=root.eco,
            opening_name=root.opening_name,
            popularity=root.popularity,
            edges=[
                OpeningEdge(
                    uci=e.uci,
                    san=e.san,
                    to_position_id=e.to_position_id,
                    games_count=e.games_count,
                    white_wins=e.white_wins,
                    black_wins=e.black_wins,
                    draws=e.draws,
                )
                for e in edges
            ],
        ),
        total_positions=total_positions,
        total_edges=total_edges,
    )
