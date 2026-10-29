"""/openings/tree — read-only query over the opening graph."""

from __future__ import annotations

from uuid import UUID

import chess
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chessmind.db import get_session
from chessmind.models import MoveEdge, Position
from chessmind.schemas.openings import (
    MasterMoveOut,
    MastersBlock,
    OpeningEdge,
    OpeningNode,
    OpeningTreeResponse,
)
from chessmind.services.canonical_fen import canonical_key
from chessmind.services.lichess_explorer import MastersResult, explorer_web_url, get_masters

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
    masters: bool = Query(default=True, description="Attach real Lichess Masters stats for the root."),
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
        masters=await _masters_block(session, chess.Board(root.fen)) if masters else None,
    )


def _block(board: chess.Board, result: MastersResult) -> MastersBlock:
    snap = result.snapshot
    return MastersBlock(
        source=result.source,
        attribution=result.attribution,
        explorer_url=explorer_web_url(board),
        status=result.status,
        fetched_at=result.fetched_at,
        error=result.error,
        total_games=snap.total_games if snap else 0,
        white=snap.white if snap else 0,
        draws=snap.draws if snap else 0,
        black=snap.black if snap else 0,
        opening_eco=snap.opening_eco if snap else None,
        opening_name=snap.opening_name if snap else None,
        moves=[MasterMoveOut(**vars(m)) for m in snap.moves] if snap else [],
    )


async def _masters_block(session: AsyncSession, board: chess.Board) -> MastersBlock:
    return _block(board, await get_masters(session, board))


@router.get("/masters", response_model=MastersBlock)
async def masters_stats(
    fen: str = Query(description="Full FEN of any legal position, in the local graph or not."),
    session: AsyncSession = Depends(get_session, scope="function"),
) -> MastersBlock:
    """Real master statistics from the Lichess explorer (cached, with local-fallback status)."""
    try:
        board = chess.Board(fen)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid FEN: {exc}") from exc
    if not board.is_valid():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "FEN describes an illegal position")
    return await _masters_block(session, board)
