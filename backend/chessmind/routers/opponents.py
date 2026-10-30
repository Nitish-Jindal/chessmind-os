"""/opponents — profile CRUD, PGN import, opening-tree queries."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID, uuid4

import chess
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chessmind.db import get_session
from chessmind.deps import get_current_player
from chessmind.models import (
    ImportJob,
    OpponentPositionMove,
    OpponentProfile,
    Player,
    Position,
)
from chessmind.schemas.imports import ImportJobRead
from chessmind.schemas.opponents import (
    OpponentCreate,
    OpponentPgnImport,
    OpponentRead,
    OpponentTreeEdge,
    OpponentTreeResponse,
)
from chessmind.services.canonical_fen import canonical_key
from chessmind.services.opponent_import import run_opponent_import_job
from chessmind.services.opponent_prep import build_prep_report

router = APIRouter(prefix="/opponents", tags=["opponents"])


async def _owned(session: AsyncSession, opponent_id: UUID, player: Player) -> OpponentProfile:
    row = await session.get(OpponentProfile, opponent_id)
    if row is None or row.owner_player_id != player.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "opponent not found")
    return row


@router.post("", response_model=OpponentRead, status_code=status.HTTP_201_CREATED)
async def create_opponent(
    payload: OpponentCreate,
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session, scope="function"),
) -> OpponentProfile:
    row = OpponentProfile(
        owner_player_id=player.id,
        display_name=payload.display_name,
        aliases_json=list(payload.aliases),
        games_imported=0,
    )
    session.add(row)
    await session.flush()
    return row


@router.get("", response_model=list[OpponentRead])
async def list_opponents(
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session, scope="function"),
) -> list[OpponentProfile]:
    rows = (
        await session.execute(
            select(OpponentProfile)
            .where(OpponentProfile.owner_player_id == player.id)
            .order_by(OpponentProfile.created_at.desc())
        )
    ).scalars().all()
    return list(rows)


@router.post("/{opponent_id}/import", response_model=ImportJobRead, status_code=status.HTTP_202_ACCEPTED)
async def import_opponent_pgn(
    opponent_id: UUID,
    payload: OpponentPgnImport,
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session, scope="function"),
) -> ImportJob:
    profile = await _owned(session, opponent_id, player)
    job = ImportJob(
        player_id=player.id,
        kind="opponent_pgn",
        opponent_profile_id=profile.id,
        source=payload.source,
        status="queued",
        progress_games=0,
        total_games=0,
        artifacts_json={},
    )
    session.add(job)
    await session.flush()
    job_id = job.id
    await session.commit()
    asyncio.create_task(run_opponent_import_job(job_id, payload.pgn))
    return job


@router.get("/{opponent_id}/tree", response_model=OpponentTreeResponse)
async def opponent_tree(
    opponent_id: UUID,
    fen: str | None = Query(default=None),
    canonical_key_q: str | None = Query(default=None, alias="canonical"),
    limit: int = Query(default=20, ge=1, le=100),
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session, scope="function"),
) -> OpponentTreeResponse:
    """§11.4 shape — ``?limit=`` top-N default (20), no cursor pagination."""
    profile = await _owned(session, opponent_id, player)

    if fen:
        try:
            board = chess.Board(fen)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid FEN: {exc}") from exc
    elif canonical_key_q:
        # Build a dummy board from the canonical key by appending zeroed counters.
        try:
            board = chess.Board(canonical_key_q + " 0 1")
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid canonical: {exc}") from exc
    else:
        board = chess.Board()

    key = canonical_key(board)
    pos = (
        await session.execute(select(Position).where(Position.canonical_key == key))
    ).scalar_one_or_none()
    if pos is None:
        return OpponentTreeResponse(
            opponent_profile_id=profile.id,
            position_canonical_key=key,
            position_fen=board.fen(),
            opponent_color="white" if board.turn == chess.WHITE else "black",
            edges=[],
            sample_total=0,
        )

    opp_color = "white" if board.turn == chess.WHITE else "black"
    rows = (
        await session.execute(
            select(OpponentPositionMove)
            .where(
                OpponentPositionMove.opponent_profile_id == profile.id,
                OpponentPositionMove.position_id == pos.id,
                OpponentPositionMove.color == opp_color,
            )
            .order_by(OpponentPositionMove.games_count.desc())
            .limit(limit)
        )
    ).scalars().all()
    total = sum(r.games_count for r in rows) or 1
    edges: list[OpponentTreeEdge] = []
    for r in rows:
        move = chess.Move.from_uci(r.move_uci)
        san = board.san(move) if move in board.legal_moves else r.move_uci
        edges.append(
            OpponentTreeEdge(
                move_uci=r.move_uci,
                move_san=san,
                games_count=r.games_count,
                wins=r.wins,
                losses=r.losses,
                draws=r.draws,
                recency_weight=r.recency_weight,
                sample_size_bucket=r.sample_size_bucket,
                confidence=round(r.games_count / total, 4),
            )
        )
    return OpponentTreeResponse(
        opponent_profile_id=profile.id,
        position_canonical_key=key,
        position_fen=board.fen(),
        opponent_color=opp_color,
        edges=edges,
        sample_total=total if rows else 0,
    )


@router.get("/{opponent_id}/prep")
async def opponent_prep(
    opponent_id: UUID,
    plies: int = Query(default=12, ge=2, le=24),
    lines: int = Query(default=5, ge=1, le=12),
    depth: int = Query(default=14, ge=6, le=20),
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session, scope="function"),
) -> dict[str, Any]:
    """Prep report: per colour, the opponent's most frequent lines (first ``plies`` plies of their
    games), likely branches, and Stockfish's suggested response at the end of each line."""
    profile = await _owned(session, opponent_id, player)
    return await build_prep_report(session, profile=profile, max_plies=plies, max_lines=lines,
                                   engine_depth=depth)
