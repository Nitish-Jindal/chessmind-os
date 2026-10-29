"""Opponent-move sampler.

Resolves the next opponent move from a four-tier fallback chain and a
seeded ``random.Random`` so a session replays byte-identically given the
same (seed, mode, position).

Tiers (first non-empty wins unless mode overrides):
  1. ``opponent_profile``   — OpponentPositionMove rows for this opponent.
  2. ``master_move_stat``   — curated master-level stats (empty by default).
  3. ``opening_graph``      — our own MoveEdge aggregates.
  4. ``engine_fallback``    — pick from Stockfish's top-N lines (never
                               always the #1 move; a seeded sampler picks
                               one of them with a soft bias toward the top).

Modes:
  ``mainline``       — top-frequency move at each step.
  ``mix``            — frequency-weighted sampling with a novelty quota
                       that occasionally picks a reasonable off-tree move
                       via the engine tier.
  ``surprise``       — invert frequencies to prefer rare lines.
  ``opponent_twin``  — tier 1 only; falls to engine if the opponent has
                       no history at this position.

Every selected move is validated against ``board.legal_moves`` before it
is returned; an illegal row is dropped and the next candidate is tried.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from enum import Enum
from typing import Literal
from uuid import UUID

import chess
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chessmind.models import MasterMoveStat, MoveEdge, OpponentPositionMove, Position
from chessmind.services.canonical_fen import canonical_key
from chessmind.services.engine import engine_client


class SamplerMode(str, Enum):
    MAINLINE = "mainline"
    MIX = "mix"
    SURPRISE = "surprise"
    OPPONENT_TWIN = "opponent_twin"


@dataclass(frozen=True)
class SamplerConfig:
    mode: SamplerMode = SamplerMode.MIX
    seed: int = 0
    novelty_prob: float = 0.1
    min_games: int = 1
    engine_depth: int = 8
    engine_multipv: int = 3


@dataclass(frozen=True)
class Candidate:
    uci: str
    weight: float  # non-negative; sampler normalises.
    source_tier: str
    games: int


@dataclass(frozen=True)
class SamplerResult:
    uci: str
    san: str
    source_tier: str
    confidence: float
    sample_size: int
    candidates_considered: int


# --------------------------------------------------------------------------- #
# Tier queries
# --------------------------------------------------------------------------- #


async def _opponent_candidates(
    session: AsyncSession,
    *,
    opponent_profile_id: UUID | None,
    position_id: UUID,
    opponent_color: Literal["white", "black"],
    min_games: int,
) -> list[Candidate]:
    if opponent_profile_id is None:
        return []
    rows = (
        await session.execute(
            select(OpponentPositionMove).where(
                OpponentPositionMove.opponent_profile_id == opponent_profile_id,
                OpponentPositionMove.position_id == position_id,
                OpponentPositionMove.color == opponent_color,
                OpponentPositionMove.games_count >= min_games,
            )
        )
    ).scalars().all()
    return [
        Candidate(uci=r.move_uci, weight=float(r.games_count) * r.recency_weight,
                  source_tier="opponent_profile", games=r.games_count)
        for r in rows
    ]


async def _master_candidates(
    session: AsyncSession, *, position_id: UUID, min_games: int
) -> list[Candidate]:
    rows = (
        await session.execute(
            select(MasterMoveStat).where(
                MasterMoveStat.position_id == position_id,
                MasterMoveStat.games_count >= min_games,
            )
        )
    ).scalars().all()
    return [
        Candidate(uci=r.move_uci, weight=float(r.games_count),
                  source_tier="master_move_stat", games=r.games_count)
        for r in rows
    ]


async def _opening_graph_candidates(
    session: AsyncSession, *, position_id: UUID, min_games: int
) -> list[Candidate]:
    rows = (
        await session.execute(
            select(MoveEdge).where(
                MoveEdge.from_position_id == position_id,
                MoveEdge.games_count >= min_games,
            )
        )
    ).scalars().all()
    return [
        Candidate(uci=r.uci, weight=float(r.games_count),
                  source_tier="opening_graph", games=r.games_count)
        for r in rows
    ]


async def _engine_candidates(
    session: AsyncSession,
    *,
    board: chess.Board,
    depth: int,
    multipv: int,
) -> list[Candidate]:
    """Top-N engine moves. Weight them 3/2/1/... so #1 is favoured but not forced."""
    from chessmind.services.engine import analyze_board

    result = await analyze_board(session, board=board, depth=depth, multipv=multipv)
    cands: list[Candidate] = []
    for i, line in enumerate(result.results):
        if not line.get("pv_uci"):
            continue
        weight = float(max(1, len(result.results) - i))
        cands.append(Candidate(uci=line["pv_uci"][0], weight=weight,
                               source_tier="engine_fallback", games=0))
    return cands


# --------------------------------------------------------------------------- #
# Mode-aware selection
# --------------------------------------------------------------------------- #


def _choose(
    candidates: list[Candidate],
    *,
    mode: SamplerMode,
    rng: random.Random,
    legal: set[str],
) -> Candidate | None:
    """Pick a candidate under ``mode``. Illegal rows are dropped silently."""
    legal_cands = [c for c in candidates if c.uci in legal]
    if not legal_cands:
        return None

    if mode is SamplerMode.MAINLINE:
        legal_cands.sort(key=lambda c: (-c.weight, c.uci))
        return legal_cands[0]

    weights = [c.weight for c in legal_cands]
    if mode is SamplerMode.SURPRISE:
        max_w = max(weights) + 1.0
        weights = [max(0.5, max_w - w) for w in weights]

    total = sum(weights)
    if total <= 0:
        return rng.choice(legal_cands)
    pick = rng.random() * total
    cum = 0.0
    for cand, w in zip(legal_cands, weights):
        cum += w
        if pick <= cum:
            return cand
    return legal_cands[-1]


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #


async def sample_opponent_move(
    session: AsyncSession,
    *,
    board: chess.Board,
    config: SamplerConfig,
    opponent_profile_id: UUID | None = None,
    opponent_color: Literal["white", "black"] | None = None,
    position_id: UUID | None = None,
    turn_index: int = 0,
) -> SamplerResult:
    """Pick the next opponent move given ``board``.

    The RNG is seeded from ``(config.seed, turn_index, canonical_key)`` so
    the same session seed and move number always picks the same move.
    """
    legal_ucis = {m.uci() for m in board.legal_moves}
    if not legal_ucis:
        raise ValueError("no legal moves — game is over")

    opp_color = opponent_color or ("white" if board.turn == chess.WHITE else "black")

    key = canonical_key(board)
    if position_id is None:
        pos = (
            await session.execute(select(Position).where(Position.canonical_key == key))
        ).scalar_one_or_none()
        position_id = pos.id if pos else None

    seed_bytes = f"{config.seed}:{turn_index}:{key}".encode("utf-8")
    seed_int = int.from_bytes(hashlib.sha256(seed_bytes).digest()[:8], "big")
    rng = random.Random(seed_int)

    tiers: list[list[Candidate]] = []
    if config.mode is SamplerMode.OPPONENT_TWIN:
        if position_id is not None:
            tiers.append(await _opponent_candidates(
                session,
                opponent_profile_id=opponent_profile_id,
                position_id=position_id,
                opponent_color=opp_color,
                min_games=config.min_games,
            ))
    else:
        if position_id is not None and opponent_profile_id is not None:
            tiers.append(await _opponent_candidates(
                session,
                opponent_profile_id=opponent_profile_id,
                position_id=position_id,
                opponent_color=opp_color,
                min_games=config.min_games,
            ))
        if position_id is not None:
            tiers.append(await _master_candidates(session, position_id=position_id, min_games=config.min_games))
            tiers.append(await _opening_graph_candidates(session, position_id=position_id, min_games=config.min_games))

    # Novelty injection: with probability novelty_prob (mix mode only), skip the
    # tree tiers and go straight to engine so we don't always replay the book.
    if config.mode is SamplerMode.MIX and rng.random() < config.novelty_prob:
        tiers = []

    candidates_considered = 0
    for tier in tiers:
        candidates_considered += len(tier)
        pick = _choose(tier, mode=config.mode, rng=rng, legal=legal_ucis)
        if pick is not None:
            move = chess.Move.from_uci(pick.uci)
            san = board.san(move)
            total_games = sum(c.games for c in tier) or 1
            confidence = pick.games / total_games if total_games else 0.0
            return SamplerResult(
                uci=pick.uci, san=san,
                source_tier=pick.source_tier,
                confidence=round(confidence, 4),
                sample_size=pick.games,
                candidates_considered=candidates_considered,
            )

    # Engine tier always runs last; this is the "legal fallback".
    engine_cands = await _engine_candidates(
        session, board=board, depth=config.engine_depth, multipv=config.engine_multipv
    )
    candidates_considered += len(engine_cands)
    pick = _choose(engine_cands, mode=SamplerMode.MIX, rng=rng, legal=legal_ucis)
    if pick is None:
        # Last-resort: a random legal move.
        uci = rng.choice(sorted(legal_ucis))
        move = chess.Move.from_uci(uci)
        return SamplerResult(
            uci=uci, san=board.san(move),
            source_tier="legal_random", confidence=0.0, sample_size=0,
            candidates_considered=candidates_considered,
        )

    move = chess.Move.from_uci(pick.uci)
    return SamplerResult(
        uci=pick.uci, san=board.san(move),
        source_tier="engine_fallback", confidence=0.0, sample_size=0,
        candidates_considered=candidates_considered,
    )
