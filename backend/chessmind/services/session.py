"""Session FSM — start, resolve a player move, consume reveals, finish.

Every call is a self-contained transaction over ``training_session`` plus
whatever rows (``training_attempt``, ``mastery_state``) the move produced.
The authoritative board state lives on ``training_session.state_json``;
the client is never trusted with it.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, replace
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
from chessmind.services import llm_coach, opening_taxonomy
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
    SamplerResult,
    sample_book_move,
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

ILLEGAL_MOVE_MESSAGE = "That's an illegal move, try again."


class IllegalMoveError(ValueError):
    """The submitted move is not legal on the session board. The game does not advance."""

    def __init__(self, detail: str, *, move_input: str, fen: str) -> None:
        super().__init__(detail)
        self.move_input = move_input
        self.fen = fen


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
    quiet: bool = False,
    opening_request: dict[str, Any] | None = None,
) -> tuple[TrainingSession, dict[str, Any] | None]:
    """Create a new session, make the first opponent move if needed, return it.

    ``opening_request`` comes from ``opening_taxonomy.parse_opening_request`` and steers the
    opponent's moves (``_apply_opponent_move``) from the first move on.
    """
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
        "quiet": bool(quiet),
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
        "starting_fen": board.fen(),
        "player_moves": 0,
        "opening": opening_taxonomy.lookup(board).to_json() if opening_taxonomy.lookup(board) else None,
        "opening_announcements": [],
        "opening_request": _validated_request(board, opening_request, player_color) if opening_request else None,
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
    quiet: bool = False
    announcements: list[dict[str, Any]] | None = None
    opening: dict[str, Any] | None = None
    opening_request: dict[str, Any] | None = None


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
    quiet = bool(settings.get("quiet", False))

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

    coaching = await explain_move(
        quiet=quiet,
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
            "explanation_source": coaching.explanation_source,
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
    new_state["move_history_san"] = [*new_state["move_history_san"], played_san]
    new_state["move_history_uci"] = [*new_state["move_history_uci"], move.uci()]
    new_state["current_fen"] = board.fen()
    new_state["hints_charged_last_move"] = hints_charge
    new_state["player_moves"] = _player_moves(ts.state_json) + 1
    announcements: list[dict[str, Any]] = []
    player_ann = _note_opening(new_state, board, by="player", quiet=quiet)
    if player_ann:
        announcements.append(player_ann)

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
        if opponent_payload and opponent_payload.get("announcement"):
            announcements.append(opponent_payload["announcement"])

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
            "explanation_source": coaching.explanation_source,
        },
        opponent_move=opponent_payload,
        current_fen=ts.state_json["current_fen"],
        status=ts.state_json.get("status", status),
        quiet=quiet,
        announcements=announcements,
        opening=ts.state_json.get("opening"),
        opening_request=ts.state_json.get("opening_request"),
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
            raise IllegalMoveError(f"illegal move {uci} in {board.fen()}", move_input=uci, fen=board.fen())
        return move
    try:
        return board.parse_san(san)
    except chess.AmbiguousMoveError as exc:
        raise ValueError(f"ambiguous SAN {san!r}: {exc}") from exc
    except (ValueError, chess.IllegalMoveError) as exc:
        # Unparseable and illegal SAN both mean "no legal move matches what was sent".
        raise IllegalMoveError(f"illegal move {san!r}: {exc}", move_input=str(san), fen=board.fen()) from exc


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
    request = dict(ts.state_json["opening_request"]) if ts.state_json.get("opening_request") else None
    request_event: str | None = None
    pick: SamplerResult | None = None
    if request is not None:
        pick, request_event = await _requested_move(session, board, request, cfg, int(ts.state_json.get("ply", 0)))
    try:
        if pick is None:
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
    new_state["move_history_san"] = [*new_state["move_history_san"], san]
    new_state["move_history_uci"] = [*new_state["move_history_uci"], pick.uci]
    new_state["current_fen"] = board.fen()
    if request is not None:
        new_state["opening_request"] = request
    announcement = _note_opening(new_state, board, by="opponent", quiet=bool(settings.get("quiet", False)))
    new_state["last_opponent_move"] = {
        "san": san,
        "uci": pick.uci,
        "source_tier": pick.source_tier,
        "confidence": pick.confidence,
        "sample_size": pick.sample_size,
        "announcement": announcement,
        "request_event": request_event,
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


def _player_moves(state: dict[str, Any]) -> int:
    if "player_moves" in state:
        return int(state["player_moves"])
    # Sessions created before the counter existed: the player made every other move.
    history = len(state.get("move_history_san", []))
    opp_first = state.get("player_color") == "black"
    return (history - (1 if opp_first and history else 0) + 1) // 2


def _note_opening(state: dict[str, Any], board: chess.Board, *, by: str, quiet: bool) -> dict[str, Any] | None:
    """Track the named opening reached; return an announcement the first time a name is reached.

    Quiet mode records the opening but announces nothing.
    """
    entry = opening_taxonomy.lookup(board)
    if entry is not None:
        state["opening"] = entry.to_json()
    if quiet:
        return None
    announced = [a["name"] for a in state.get("opening_announcements", [])]
    ann = opening_taxonomy.announcement_for(board, announced)
    if ann is None:
        return None
    payload = {**ann.to_json(), "ply": int(state.get("ply", 0)), "by": by, "text": ann.name}
    state["opening_announcements"] = [*state.get("opening_announcements", []), payload]
    return payload


def _validated_request(
    board: chess.Board, request: dict[str, Any], player_color: Literal["white", "black"]
) -> dict[str, Any]:
    """Check a parsed opening request against the board; raise ValueError if it cannot apply.

    A move request must be legal for the opponent, either now (opponent to move) or after
    at least one legal player move. It applies to the opponent's next move only.
    """
    req = dict(request)
    if req["kind"] == "unknown":
        raise ValueError(f"I don't know an opening called {req['phrase']!r}.")
    if req["kind"] == "opening":
        req["status"] = "active"
        return req
    san_text = req["san_text"]
    opp_to_move = not _player_turn(board, player_color)
    boards = [board] if opp_to_move else []
    if not opp_to_move:
        for mv in board.legal_moves:
            b = board.copy(stack=False)
            b.push(mv)
            boards.append(b)
    for b in boards:
        try:
            mv = b.parse_san(san_text)
        except ValueError:
            continue
        req["san"] = b.san(mv)
        req["label"] = req["san"]
        req["status"] = "pending"
        return req
    who = "White" if (board.turn == chess.WHITE) == opp_to_move else "Black"
    raise ValueError(f"{san_text} is not a legal move for {who} here.")


async def _requested_move(
    session: AsyncSession,
    board: chess.Board,
    request: dict[str, Any],
    cfg: SamplerConfig,
    turn_index: int,
) -> tuple[SamplerResult | None, str | None]:
    """Follow the player's opening request if the position allows it; mutates ``request``.

    Returns the move to play (or None to sample normally) and an event name for the UI:
    ``played_requested_move``, ``in_book`` or ``out_of_book`` (sent once, when the game
    leaves the requested opening's lines).
    """
    if request["kind"] == "move":
        if request.get("status") != "pending":
            return None, None
        request["status"] = "done"
        try:
            mv = board.parse_san(request["san"])
        except ValueError:
            request["status"] = "not_legal"
            return None, "requested_move_not_legal"
        return SamplerResult(uci=mv.uci(), san=board.san(mv), source_tier="requested_move",
                             confidence=1.0, sample_size=0, candidates_considered=1), "played_requested_move"
    if request.get("status") != "active":
        return None, None
    moves = opening_taxonomy.book_moves(board, request)
    if not moves:
        request["status"] = "out_of_book"
        return None, "out_of_book"
    pick = await sample_book_move(session, board=board, moves=moves, config=cfg, turn_index=turn_index)
    return pick, "in_book"


async def set_opening_request(session: AsyncSession, *, ts: TrainingSession, text: str) -> dict[str, Any]:
    """Handle "play <opening or move> against me" during a session.

    If the opponent has moved but the player has not (player is Black and the engine
    opened), the game restarts from the starting position so the engine opens as asked.
    """
    parsed = opening_taxonomy.parse_opening_request(text)
    if parsed is None:
        return {"understood": False, "message": "Say it like: play the Caro-Kann against me, or play d4 against me."}
    state = dict(ts.state_json)
    if state.get("status") != "active":
        raise ValueError("session is already finished")
    player_color: Literal["white", "black"] = state["player_color"]
    restart = _player_moves(state) == 0 and len(state.get("move_history_uci", [])) > 0
    board = chess.Board(state.get("starting_fen") or chess.STARTING_FEN) if restart else chess.Board(state["current_fen"])
    request = _validated_request(board, parsed, player_color)

    if restart:
        state.update({
            "current_fen": board.fen(),
            "move_history_san": [],
            "move_history_uci": [],
            "ply": 0,
            "last_opponent_move": None,
            "next_to_move": "opponent",
            "opening": opening_taxonomy.lookup(board).to_json() if opening_taxonomy.lookup(board) else None,
            "opening_announcements": [],
        })
    state["opening_request"] = request
    ts.state_json = state
    opponent_move = await _apply_opponent_move(session, ts, board) if restart else None

    label = request["label"]
    if request["kind"] == "move":
        message = (f"Restarted. I play {label}." if restart
                   else f"After your move I'll play {label}.")
    else:
        where = f" ({request['eco']}: {request['pgn']})" if request.get("pgn") else ""
        in_book = ts.state_json.get("opening_request", {}).get("status") == "active" and (
            restart or _can_reach_book(chess.Board(ts.state_json["current_fen"]), request)
        )
        if restart:
            message = f"Restarted. I'll play the {label}{where}."
        elif in_book:
            message = f"I'll play the {label}{where} where the position allows."
        else:
            message = (f"This game has already left the {label}{where}. I'll play normally; "
                       f"start a new game to play it from move one.")
    return {
        "understood": True,
        "message": message,
        "restarted": restart,
        "opening_request": ts.state_json.get("opening_request"),
        "opponent_move": opponent_move,
    }


def _can_reach_book(board: chess.Board, request: dict[str, Any]) -> bool:
    return canonical_key(board) in opening_taxonomy.book_keys(request["match"], request["text"])


def set_quiet(ts: TrainingSession, quiet: bool) -> dict[str, Any]:
    ts.settings_json = {**(ts.settings_json or {}), "quiet": bool(quiet)}
    return ts.settings_json


async def explain_move(*, quiet: bool, **kwargs: Any):
    """Coaching for one move: the deterministic template, optionally reworded by the LLM layer.

    Quiet mode returns an empty summary and no correction (the attempt is still graded) and
    never calls the LLM. With the LLM layer off or failing validation, the template is used.
    """
    coaching = coach_move(**kwargs)
    if quiet:
        return replace(coaching, spoken_summary="", correction_san=None)
    if not llm_coach.is_enabled():
        return coaching
    label: LabelResult = kwargs["label"]
    evidence = llm_coach.Evidence(
        fen_before=kwargs["board_before"].fen(),
        player_color=kwargs["player_color"],
        played_san=kwargs["played_san"],
        classification=label.classification.value,
        eval_swing_cp=label.eval_swing_cp,
        eval_before_cp_white=label.eval_before_cp,
        eval_after_cp_white=label.eval_after_cp,
        best_san_list=list(kwargs["best_san_list"]),
        best_pv_san=list(kwargs["best_pv_san"]),
        board_features=coaching.board_features,
        template_text=coaching.spoken_summary,
    )
    out = await llm_coach.explain(evidence)
    refs = coaching.evidence_refs + [f"llm_fact:{c}" for c in out.cited_facts]
    return replace(coaching, spoken_summary=out.text, explanation_source=out.source, evidence_refs=refs)


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
    state_label = mastery_state_label(sm2.reps, sm2.ease_factor, sm2.interval_days, tested=True)

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
