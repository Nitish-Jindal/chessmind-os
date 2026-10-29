"""/sessions — guided/blindfold/tournament/opponent play with Stockfish grounding."""

from __future__ import annotations

import chess
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from chessmind.db import get_session
from chessmind.deps import get_current_player
from chessmind.models import Player, TrainingSession
from chessmind.schemas.sessions import (
    CoachingPayload,
    EngineLine,
    FinishResponse,
    LegalMovesResponse,
    MoveRequest,
    MoveResponse,
    RevealRequest,
    RevealResponse,
    SessionCreate,
    SessionState,
)
from chessmind.services.session import (
    apply_player_move,
    consume_reveal,
    create_session,
    finish_session,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])


async def _owned(session: AsyncSession, ts_id, player: Player) -> TrainingSession:
    ts = await session.get(TrainingSession, ts_id)
    if ts is None or ts.player_id != player.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    return ts


@router.post("", response_model=SessionState, status_code=status.HTTP_201_CREATED)
async def post_session(
    payload: SessionCreate,
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session),
) -> TrainingSession:
    try:
        ts, _opener = await create_session(
            session,
            player=player,
            mode=payload.mode,
            player_color=payload.player_color,
            sampler_mode=payload.sampler_mode,
            seed=payload.seed,
            opponent_profile_id=payload.opponent_profile_id,
            starting_fen=payload.starting_fen,
            opening_scope_json=payload.opening_scope_json,
            novelty_prob=payload.novelty_prob,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return ts


@router.get("/{session_id}", response_model=SessionState)
async def get_session_state(
    session_id,
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session),
) -> TrainingSession:
    return await _owned(session, session_id, player)


@router.get("/{session_id}/legal", response_model=LegalMovesResponse)
async def get_legal_moves(
    session_id,
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session),
) -> LegalMovesResponse:
    ts = await _owned(session, session_id, player)
    board = chess.Board(ts.state_json["current_fen"])
    sans = [board.san(m) for m in board.legal_moves]
    ucis = [m.uci() for m in board.legal_moves]
    settings = ts.settings_json or {}
    budget = int(settings.get("blindfold_free_reveals_per_session", ts.state_json.get("reveals_budget", 2)))
    return LegalMovesResponse(
        session_id=ts.id,
        current_fen=ts.state_json["current_fen"],
        next_to_move=ts.state_json.get("next_to_move", "player"),
        status=ts.state_json.get("status", "active"),
        legal_moves_san=sans,
        legal_moves_uci=ucis,
        reveals_used=int(ts.state_json.get("reveals_used", 0)),
        reveals_budget=budget,
        hints_used=int(ts.state_json.get("hints_used", 0)),
    )


@router.post("/{session_id}/move", response_model=MoveResponse)
async def post_move(
    session_id,
    payload: MoveRequest,
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session),
) -> MoveResponse:
    ts = await _owned(session, session_id, player)
    try:
        outcome = await apply_player_move(
            session,
            ts=ts,
            san=payload.san,
            uci=payload.uci,
            latency_ms=payload.latency_ms,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    engine_lines = [
        EngineLine(
            line_rank=line["line_rank"],
            cp=line.get("cp"),
            mate=line.get("mate"),
            pv_san=line.get("pv_san", []),
            pv_uci=line.get("pv_uci", []),
        )
        for line in outcome.best_lines
    ]
    coaching = CoachingPayload(
        spoken_summary=outcome.coaching["spoken_summary"],
        correction_san=outcome.coaching["correction_san"],
        evidence_refs=outcome.coaching["evidence_refs"],
        board_features=outcome.coaching["board_features"],
    )
    return MoveResponse(
        session_id=ts.id,
        attempt_id=outcome.attempt_id,
        played_san=outcome.played_san,
        played_uci=outcome.played_uci,
        classification=outcome.label.classification.value,
        eval_before_cp_white=outcome.label.eval_before_cp,
        eval_after_cp_white=outcome.label.eval_after_cp,
        eval_swing_cp=outcome.label.eval_swing_cp,
        player_relative_before_cp=outcome.label.player_relative_before,
        player_relative_after_cp=outcome.label.player_relative_after,
        mate_flip=outcome.label.mate_flip,
        engine_version=outcome.engine_version,
        engine_depth=outcome.engine_depth,
        engine_multipv=outcome.engine_multipv,
        best_lines=engine_lines,
        coaching=coaching,
        opponent_move=outcome.opponent_move,
        current_fen=outcome.current_fen,
        status=outcome.status,
    )


@router.post("/{session_id}/reveal", response_model=RevealResponse)
async def post_reveal(
    session_id,
    payload: RevealRequest,
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session),
) -> RevealResponse:
    ts = await _owned(session, session_id, player)
    out = await consume_reveal(session, ts=ts)
    return RevealResponse(session_id=ts.id, **out)


@router.post("/{session_id}/finish", response_model=FinishResponse)
async def post_finish(
    session_id,
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session),
) -> FinishResponse:
    ts = await _owned(session, session_id, player)
    await finish_session(session, ts=ts)
    return FinishResponse(session_id=ts.id, status="finished", ended_at=ts.ended_at)
