"""Session FSM — start, resolve a player move, consume reveals, finish.

Every call is a self-contained transaction over ``training_session`` plus
whatever rows (``training_attempt``, ``mastery_state``) the move produced.
The authoritative board state lives on ``training_session.state_json``;
the client is never trusted with it.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

import chess
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from chessmind.models import (
    EngineAnalysis,
    MasteryState,
    MoveEdge,
    Player,
    Position,
    TrainingAttempt,
    TrainingSession,
)
from chessmind.services.coaching import coach_move
from chessmind.services.canonical_fen import canonical_key
from chessmind.services.engine import analyze_board
from chessmind.services.labeler import (
    Classification,
    EvalPoint,
    LabelResult,
    label_move,
)
from chessmind.services.opening_graph import upsert_position
from chessmind.services.sampler import (
    SamplerConfig,
    SamplerMode,
    sample_opponent_move,
)
from chessmind.services.sm2 import (
    SM2State,
    apply_review,
    classification_to_quality,
    mastery_state_label,
)

logger = logging.getLogger("chessmind.session")

DEFAULT_SESSION_DEPTH = 10
DEFAULT_SESSION_MULTIPV = 3


# --------------------------------------------------------------------------- #
# Session lifecycle
# --------------------------------------------------------------------------- #


async def create_session(
    session: AsyncSession,
    *,
    player: Player,
    mode: str,
    player_color: Literal["white", "black"],
    sampler_mode: str,
    seed: int | None,
    opponent_profile_id: UUID | None,
    starting_fen: str | None,
    opening_scope_json: dict[str, Any],
    novelty_prob: float,
) -> tuple[TrainingSession, dict[str, Any] | None]:
    """Create a new session, make the first opponent move if needed, return it."""
    seed = seed if seed is not None else secrets.randbits(60)

    try:
        board = chess.Board(starting_fen) if starting_fen else chess.Board()
    except ValueError as exc:
        raise ValueError(f"invalid starting_fen: {exc}") from exc
    if not board.is_valid():
        raise ValueError("starting_fen is not a legal chess position")

    settings = {
        **(player.settings_json or {}),
        "sampler_mode": sampler_mode,
        "novelty_prob": novelty_prob,
    }
    reveals_budget = int(settings.get("blindfold_free_reveals_per_session", 2))

    state_json: dict[str, Any] = {
        "current_fen": board.fen(),
        "move_history_san": [],
        "move_history_uci": [],
        "player_color": player_color,
        "next_to_move": "player" if _player_turn(board, player_color) else "opponent",
        "reveals_used": 0,
        "reveals_budget": reveals_budget,
        "hints_used": 0,
        "status": "active",
        "ply": 0,
        "last_opponent_move": None,
    }

    ts = TrainingSession(
        id=uuid4(),
        player_id=player.id,
        mode=mode,
        opening_scope_json=opening_scope_json,
        opponent_profile_id=opponent_profile_id,
        seed=seed,
        settings_json=settings,
        state_json=state_json,
        started_at=datetime.now(timezone.utc),
        ended_at=None,
    )
    session.add(ts)
    await session.flush()

    opener_payload: dict[str, Any] | None = None
    if state_json["next_to_move"] == "opponent":
        opener_payload = await _apply_opponent_move(session, ts, board)
        await session.flush()

    return ts, opener_payload


def _player_turn(board: chess.Board, player_color: Literal["white", "black"]) -> bool:
    return (board.turn == chess.WHITE and player_color == "white") or (
        board.turn == chess.BLACK and player_color == "black"
    )


# --------------------------------------------------------------------------- #
# Move resolution
# --------------------------------------------------------------------------- #


@dataclass
class MoveOutcome:
    attempt_id: UUID
    played_san: str
    played_uci: str
    label: LabelResult
    engine_version: str
    engine_depth: int
    engine_multipv: int
    best_lines: list[dict[str, Any]]
    coaching: dict[str, Any]
    opponent_move: dict[str, Any] | None
    current_fen: str
    status: str


async def apply_player_move(
    session: AsyncSession,
    *,
    ts: TrainingSession,
    san: str | None,
    uci: str | None,
    latency_ms: int,
) -> MoveOutcome:
    if ts.ended_at is not None or ts.state_json.get("status") != "active":
        raise ValueError("session is already finished")
    if ts.state_json.get("next_to_move") != "player":
        raise ValueError("it is not the player's turn")

    board = chess.Board(ts.state_json["current_fen"])
    player_color: Literal["white", "black"] = ts.state_json["player_color"]
    settings = ts.settings_json or {}

    move = _resolve_move_input(board, san=san, uci=uci)

    before_pos_id = await upsert_position(session, board)
    before_analysis = await analyze_board(
        session, board=board,
        depth=DEFAULT_SESSION_DEPTH,
        multipv=DEFAULT_SESSION_MULTIPV,
    )
    await session.flush()
    before_point = _eval_point_from_results(before_analysis.results)
    best_lines = before_analysis.results
    best_san_list = [line["pv_san"][0] for line in best_lines if line.get("pv_san")]
    best_pv_san = best_lines[0]["pv_san"] if best_lines and best_lines[0].get("pv_san") else []

    played_san = board.san(move)
    played_was_best = bool(best_lines) and best_lines[0]["pv_uci"][0] == move.uci()

    before_board = board.copy(stack=False)
    board.push(move)
    after_analysis = await analyze_board(
        session, board=board,
        depth=DEFAULT_SESSION_DEPTH,
        multipv=1,
    )
    await session.flush()
    after_point = _eval_point_from_results(after_analysis.results)

    label = label_move(
        player_color=player_color,
        before=before_point,
        after=after_point,
        played_was_best=played_was_best,
    )

    coaching = coach_move(
        board_before=before_board,
        board_after=board,
        player_color=player_color,
        played_san=played_san,
        label=label,
        best_san_list=best_san_list,
        best_pv_san=best_pv_san,
        position_id_ref=str(before_pos_id),
        engine_analysis_id_ref=str(before_analysis.position_id),
    )

    hints_charge = _hint_penalty_for_attempt(ts)
    visualization_score = max(0.0, 1.0 - hints_charge * float(settings.get("blindfold_reveal_penalty", 0.15)))

    attempt = TrainingAttempt(
        id=uuid4(),
        session_id=ts.id,
        position_id=before_pos_id,
        played_uci=move.uci(),
        best_uci_list_json=[line["pv_uci"][0] for line in best_lines if line.get("pv_uci")],
        classification=label.classification.value,
        eval_before_cp=label.eval_before_cp,
        eval_after_cp=label.eval_after_cp,
        eval_swing_cp=label.eval_swing_cp,
        mate_flip=label.mate_flip,
        latency_ms=latency_ms,
        hints_used=hints_charge,
        visualization_score=visualization_score,
        feedback_json={
            "spoken_summary": coaching.spoken_summary,
            "correction_san": coaching.correction_san,
            "evidence_refs": coaching.evidence_refs,
            "board_features": coaching.board_features,
            "player_relative_before": label.player_relative_before,
            "player_relative_after": label.player_relative_after,
        },
    )
    session.add(attempt)

    await _update_mastery_state(
        session,
        player_id=ts.player_id,
        position_id=before_pos_id,
        label=label,
        visualization_score=visualization_score,
        latency_ms=latency_ms,
    )

    # Reset per-move hint ledger: reveals_used stays, but the per-move
    # "charged against this attempt" counter clears to the current ledger.
    new_state = dict(ts.state_json)
    new_state["ply"] = int(new_state.get("ply", 0)) + 1
    new_state["move_history_san"].append(played_san)
    new_state["move_history_uci"].append(move.uci())
    new_state["current_fen"] = board.fen()
    new_state["hints_charged_last_move"] = hints_charge

    status = _game_status(board)
    opponent_payload: dict[str, Any] | None = None
    if status != "active":
        new_state["status"] = "finished"
        new_state["next_to_move"] = "player"
        ts.state_json = new_state
        ts.ended_at = datetime.now(timezone.utc)
    else:
        new_state["next_to_move"] = "opponent"
        ts.state_json = new_state
        opponent_payload = await _apply_opponent_move(session, ts, board)
        status = ts.state_json.get("status", "active")

    return MoveOutcome(
        attempt_id=attempt.id,
        played_san=played_san,
        played_uci=move.uci(),
        label=label,
        engine_version=before_analysis.engine_version,
        engine_depth=before_analysis.depth,
        engine_multipv=before_analysis.multipv,
        best_lines=best_lines,
        coaching={
            "spoken_summary": coaching.spoken_summary,
            "correction_san": coaching.correction_san,
            "evidence_refs": coaching.evidence_refs,
            "board_features": coaching.board_features,
        },
        opponent_move=opponent_payload,
        current_fen=ts.state_json["current_fen"],
        status=ts.state_json.get("status", status),
    )


def _resolve_move_input(board: chess.Board, *, san: str | None, uci: str | None) -> chess.Move:
    if not san and not uci:
        raise ValueError("either san or uci is required")
    if uci:
        try:
            move = chess.Move.from_uci(uci)
        except (ValueError, chess.InvalidMoveError) as exc:
            raise ValueError(f"invalid UCI: {exc}") from exc
        if move not in board.legal_moves:
            raise ValueError(f"illegal move {uci} in {board.fen()}")
        return move
    try:
        return board.parse_san(san)
    except (ValueError, chess.IllegalMoveError, chess.AmbiguousMoveError) as exc:
        raise ValueError(f"invalid SAN {san!r}: {exc}") from exc


def _eval_point_from_results(results: list[dict[str, Any]]) -> EvalPoint:
    if not results:
        return EvalPoint(cp=0, mate=None)
    top = results[0]
    return EvalPoint(cp=top.get("cp"), mate=top.get("mate"))


def _game_status(board: chess.Board) -> str:
    if board.is_game_over():
        return "finished"
    return "active"


def _hint_penalty_for_attempt(ts: TrainingSession) -> int:
    """Number of reveals that exceed the blindfold budget on this attempt."""
    state = ts.state_json or {}
    settings = ts.settings_json or {}
    budget = int(settings.get("blindfold_free_reveals_per_session", state.get("reveals_budget", 2)))
    reveals_used = int(state.get("reveals_used", 0))
    already_charged = int(state.get("hints_used", 0))
    extra = max(0, reveals_used - budget)
    uncharged = max(0, extra - already_charged)
    return uncharged


async def _apply_opponent_move(
    session: AsyncSession,
    ts: TrainingSession,
    board: chess.Board,
) -> dict[str, Any] | None:
    settings = ts.settings_json or {}
    mode = SamplerMode(settings.get("sampler_mode", "mix"))
    novelty = float(settings.get("novelty_prob", 0.1))
    cfg = SamplerConfig(
        mode=mode,
        seed=int(ts.seed),
        novelty_prob=novelty,
    )
    try:
        pick = await sample_opponent_move(
            session,
            board=board,
            config=cfg,
            opponent_profile_id=ts.opponent_profile_id,
            turn_index=int(ts.state_json.get("ply", 0)),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("sampler failed: %s", exc)
        return None

    try:
        move = chess.Move.from_uci(pick.uci)
    except (ValueError, chess.InvalidMoveError):
        return None
    if move not in board.legal_moves:
        return None

    san = board.san(move)
    board.push(move)

    new_state = dict(ts.state_json)
    new_state["ply"] = int(new_state.get("ply", 0)) + 1
    new_state["move_history_san"].append(san)
    new_state["move_history_uci"].append(pick.uci)
    new_state["current_fen"] = board.fen()
    new_state["last_opponent_move"] = {
        "san": san,
        "uci": pick.uci,
        "source_tier": pick.source_tier,
        "confidence": pick.confidence,
        "sample_size": pick.sample_size,
    }
    status = _game_status(board)
    if status != "active":
        new_state["status"] = "finished"
        new_state["next_to_move"] = "player"
        ts.state_json = new_state
        ts.ended_at = datetime.now(timezone.utc)
    else:
        new_state["next_to_move"] = "player"
        ts.state_json = new_state

    return new_state["last_opponent_move"]


# --------------------------------------------------------------------------- #
# Reveal / finish
# --------------------------------------------------------------------------- #


async def consume_reveal(session: AsyncSession, *, ts: TrainingSession) -> dict[str, Any]:
    state = dict(ts.state_json)
    settings = ts.settings_json or {}
    budget = int(settings.get("blindfold_free_reveals_per_session", state.get("reveals_budget", 2)))
    reveals_used = int(state.get("reveals_used", 0)) + 1
    state["reveals_used"] = reveals_used
    was_free = reveals_used <= budget
    ts.state_json = state
    return {
        "reveals_used": reveals_used,
        "reveals_budget": budget,
        "hints_used": int(state.get("hints_used", 0)),
        "current_fen": state["current_fen"],
        "was_free": was_free,
    }


async def finish_session(session: AsyncSession, *, ts: TrainingSession) -> TrainingSession:
    if ts.ended_at is None:
        ts.ended_at = datetime.now(timezone.utc)
    state = dict(ts.state_json)
    state["status"] = "finished"
    ts.state_json = state
    return ts


# --------------------------------------------------------------------------- #
# Mastery
# --------------------------------------------------------------------------- #


async def _update_mastery_state(
    session: AsyncSession,
    *,
    player_id: UUID,
    position_id: UUID,
    label: LabelResult,
    visualization_score: float,
    latency_ms: int,
) -> None:
    now = datetime.now(timezone.utc)
    existing = (
        await session.execute(
            select(MasteryState).where(
                MasteryState.player_id == player_id,
                MasteryState.position_id == position_id,
            )
        )
    ).scalar_one_or_none()

    quality = classification_to_quality(label.classification)
    prev = (
        SM2State(ease_factor=existing.ease_factor, interval_days=existing.interval_days, reps=existing.reps)
        if existing is not None
        else SM2State()
    )
    sm2 = apply_review(prev, quality=quality, now=now)
    state_label = mastery_state_label(sm2.reps, sm2.ease_factor, sm2.interval_days)

    recall = max(0.0, 1.0 - (label.eval_swing_cp / 500.0))
    recall = min(1.0, recall)
    understanding = recall  # Day-1 proxy; refine with concept tagging later.
    speed = _speed_score(latency_ms)

    if existing is None:
        session.add(
            MasteryState(
                id=uuid4(),
                player_id=player_id,
                position_id=position_id,
                recall_score=round(recall, 4),
                understanding_score=round(understanding, 4),
                visualization_score=round(visualization_score, 4),
                speed_score=round(speed, 4),
                state=state_label,
                due_at=sm2.due_at,
                last_tested_at=now,
                ease_factor=sm2.ease_factor,
                interval_days=sm2.interval_days,
                reps=sm2.reps,
            )
        )
    else:
        # Blended update — new score weighted 50/50 with the previous.
        existing.recall_score = round(0.5 * existing.recall_score + 0.5 * recall, 4)
        existing.understanding_score = round(0.5 * existing.understanding_score + 0.5 * understanding, 4)
        existing.visualization_score = round(0.5 * existing.visualization_score + 0.5 * visualization_score, 4)
        existing.speed_score = round(0.5 * existing.speed_score + 0.5 * speed, 4)
        existing.state = state_label
        existing.due_at = sm2.due_at
        existing.last_tested_at = now
        existing.ease_factor = sm2.ease_factor
        existing.interval_days = sm2.interval_days
        existing.reps = sm2.reps


def _speed_score(latency_ms: int) -> float:
    """Simple mapping: 0 ms → 1.0, 10 s → 0.5, 30 s → 0.0."""
    if latency_ms <= 0:
        return 1.0
    if latency_ms >= 30_000:
        return 0.0
    return max(0.0, 1.0 - latency_ms / 30_000)
