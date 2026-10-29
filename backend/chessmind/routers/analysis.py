"""POST /analysis/position — cache-first Stockfish analysis."""

from __future__ import annotations

import chess
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from chessmind.db import get_session
from chessmind.deps import get_current_player
from chessmind.models import Player
from chessmind.schemas.engine import AnalysisRequest, AnalysisResponse
from chessmind.services.engine import analyze_board

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.post("/position", response_model=AnalysisResponse)
async def analyze_position(
    payload: AnalysisRequest,
    _player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session),
) -> AnalysisResponse:
    try:
        board = chess.Board(payload.fen)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid FEN: {exc}") from exc
    if not board.is_valid():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "FEN describes an illegal position")

    result = await analyze_board(session, board=board, depth=payload.depth, multipv=payload.multipv)
    return AnalysisResponse(
        position_id=result.position_id,
        engine_version=result.engine_version,
        depth=result.depth,
        multipv=result.multipv,
        nodes=result.nodes,
        time_ms=result.time_ms,
        perspective=result.perspective,
        results=result.results,
        cached=result.cached,
    )
