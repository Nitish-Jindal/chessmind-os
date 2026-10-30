"""/sessions — guided/blindfold/tournament/opponent play with Stockfish grounding."""

from __future__ import annotations

from uuid import UUID

import chess
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from chessmind.db import get_session
from chessmind.deps import get_current_player
from chessmind.models import Player, PositionQuestion, TrainingSession
from chessmind.schemas.sessions import (
    AskResponse,
    CoachingPayload,
    EngineLine,
    FinishResponse,
    IllegalMoveDetail,
    LegalMovesResponse,
    MoveRequest,
    MoveResponse,
    OpeningAnnouncement,
    OpeningCommandResponse,
    QuestionTally,
    QuizAnswerResponse,
    QuizQuestion,
    QuizRequest,
    RevealRequest,
    RevealResponse,
    SessionCreate,
    SessionSettingsPatch,
    SessionState,
    UtteranceRequest,
)
from chessmind.services import position_questions as pq
from chessmind.services.opening_taxonomy import parse_opening_request
from chessmind.services.session import (
    ILLEGAL_MOVE_MESSAGE,
    IllegalMoveError,
    apply_player_move,
    consume_reveal,
    create_session,
    finish_session,
    set_opening_request,
    set_quiet,
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
    session: AsyncSession = Depends(get_session, scope="function"),
) -> TrainingSession:
    request = None
    if payload.opening_request:
        text = payload.opening_request.strip()
        if parse_opening_request(text) is None:
            text = f"play {text} against me"
        request = parse_opening_request(text)
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
            quiet=payload.quiet,
            opening_request=request,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return ts


@router.patch("/{session_id}/settings", response_model=SessionState)
async def patch_session_settings(
    session_id,
    payload: SessionSettingsPatch,
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session, scope="function"),
) -> TrainingSession:
    """Toggle quiet mode mid-session."""
    ts = await _owned(session, session_id, player)
    set_quiet(ts, payload.quiet)
    return ts


@router.post("/{session_id}/opening", response_model=OpeningCommandResponse)
async def post_opening_command(
    session_id,
    payload: UtteranceRequest,
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session, scope="function"),
) -> OpeningCommandResponse:
    """"Play <opening or move> against me": parsed deterministically, validated by python-chess."""
    ts = await _owned(session, session_id, player)
    out: dict | None = None
    error: str | None = None
    for text in payload.all_texts():
        try:
            out = await set_opening_request(session, ts=ts, text=text)
        except ValueError as exc:
            error = error or str(exc)
            continue
        if out["understood"]:
            break
    if out is None or not out["understood"]:
        # ``error``: the request parsed but cannot apply (e.g. that move is illegal here).
        return OpeningCommandResponse(
            session_id=ts.id, understood=error is not None, accepted=False,
            message=error or (out["message"] if out else "I didn't understand that."),
            current_fen=ts.state_json["current_fen"],
        )
    return OpeningCommandResponse(session_id=ts.id, accepted=True, current_fen=ts.state_json["current_fen"], **out)


@router.get("/{session_id}", response_model=SessionState)
async def get_session_state(
    session_id,
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session, scope="function"),
) -> TrainingSession:
    return await _owned(session, session_id, player)


@router.get("/{session_id}/legal", response_model=LegalMovesResponse)
async def get_legal_moves(
    session_id,
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session, scope="function"),
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
    session: AsyncSession = Depends(get_session, scope="function"),
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
    except IllegalMoveError as exc:
        # The board is unchanged; the client speaks and captions ``message``.
        detail = IllegalMoveDetail(message=ILLEGAL_MOVE_MESSAGE, input=exc.move_input, current_fen=exc.fen)
        raise HTTPException(422, detail.model_dump()) from exc
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
        quiet=outcome.quiet,
        announcements=[OpeningAnnouncement(**a) for a in outcome.announcements or []],
        opening=outcome.opening,
        opening_request=outcome.opening_request,
    )


@router.post("/{session_id}/reveal", response_model=RevealResponse)
async def post_reveal(
    session_id,
    payload: RevealRequest,
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session, scope="function"),
) -> RevealResponse:
    ts = await _owned(session, session_id, player)
    out = await consume_reveal(session, ts=ts)
    return RevealResponse(session_id=ts.id, **out)


@router.post("/{session_id}/finish", response_model=FinishResponse)
async def post_finish(
    session_id,
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session, scope="function"),
) -> FinishResponse:
    ts = await _owned(session, session_id, player)
    await finish_session(session, ts=ts)
    return FinishResponse(session_id=ts.id, status="finished", ended_at=ts.ended_at)


@router.post("/{session_id}/ask", response_model=AskResponse)
async def post_ask(
    session_id,
    payload: UtteranceRequest,
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session, scope="function"),
) -> AskResponse:
    """Answer a question about the current board from python-chess (+ cached Stockfish for eval)."""
    ts = await _owned(session, session_id, player)
    out = await pq.ask(session, ts=ts, alternatives=payload.all_texts())
    return AskResponse(session_id=ts.id, understood=out.understood, question_id=out.question_id,
                       kind=out.kind, answer_text=out.answer_text, facts=out.facts,
                       questions_asked=out.questions_asked)


@router.post("/{session_id}/quiz", response_model=QuizQuestion, status_code=status.HTTP_201_CREATED)
async def post_quiz(
    session_id,
    payload: QuizRequest,
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session, scope="function"),
) -> QuizQuestion:
    """Ask the player a visualization question about the current board (seeded)."""
    ts = await _owned(session, session_id, player)
    if (ts.settings_json or {}).get("quiet"):
        raise HTTPException(status.HTTP_409_CONFLICT, "quiet mode is on: quizzes are off")
    row = await pq.new_quiz(session, ts=ts, kind=payload.kind)
    return QuizQuestion(session_id=ts.id, question_id=row.id, kind=row.kind, prompt=row.prompt)


@router.post("/{session_id}/quiz/{question_id}/answer", response_model=QuizAnswerResponse)
async def post_quiz_answer(
    session_id,
    question_id: UUID,
    payload: UtteranceRequest,
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session, scope="function"),
) -> QuizAnswerResponse:
    """Score a spoken/typed answer against the board facts captured when the question was asked."""
    ts = await _owned(session, session_id, player)
    row = await session.get(PositionQuestion, question_id)
    if row is None or row.session_id != ts.id or row.mode != "quiz":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "question not found")
    try:
        out = await pq.answer_quiz(session, ts=ts, row=row, alternatives=payload.all_texts())
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return QuizAnswerResponse(
        session_id=ts.id, question_id=row.id, understood=out.understood, correct=out.correct,
        score=out.score, expected_text=out.expected_text, feedback=out.feedback,
        tally=QuestionTally(**await pq.tally(session, ts)),
    )


@router.get("/{session_id}/questions", response_model=QuestionTally)
async def get_question_tally(
    session_id,
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session, scope="function"),
) -> QuestionTally:
    ts = await _owned(session, session_id, player)
    return QuestionTally(**await pq.tally(session, ts))
