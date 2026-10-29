"""Opponent-move sampler: determinism, legality, tier fallback, mode behaviour."""

from __future__ import annotations

from uuid import uuid4

import random

import chess
import pytest
from sqlalchemy import select

from chessmind.models import (
    MasterMoveStat,
    MoveEdge,
    OpponentPositionMove,
    OpponentProfile,
    Player,
    Position,
)
from chessmind.security import hash_password
from chessmind.services.canonical_fen import canonical_key
from chessmind.services.opening_graph import upsert_move_edge, upsert_position
from chessmind.services.sampler import (
    Candidate,
    SamplerConfig,
    SamplerMode,
    _choose,
    sample_opponent_move,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


@pytest.fixture
async def player(db) -> Player:
    p = Player(
        email=f"{uuid4().hex}@test.local.example.com",
        password_hash=hash_password("passwordpassword"),
        display_name="Sampler Tester",
        preferred_color="either",
        settings_json={},
    )
    db.add(p)
    await db.commit()
    return p


async def _seed_edge(db, from_board: chess.Board, uci: str, games: int, *, w=0, l=0, d=0) -> tuple[chess.Board, Position]:
    """Create Position for ``from_board``, the position after ``uci``, and a MoveEdge."""
    from_pos_id = await upsert_position(db, from_board)
    after = from_board.copy(stack=False)
    move = chess.Move.from_uci(uci)
    san = from_board.san(move)
    after.push(move)
    to_pos_id = await upsert_position(db, after)

    # Direct insert (not through upsert_move_edge, so we can set games exactly).
    db.add(MoveEdge(
        from_position_id=from_pos_id,
        to_position_id=to_pos_id,
        uci=uci,
        san=san,
        games_count=games,
        white_wins=w,
        black_wins=l,
        draws=d,
    ))
    await db.commit()
    row = (await db.execute(select(Position).where(Position.id == from_pos_id))).scalar_one()
    return after, row


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


async def test_sampler_is_seeded_deterministic(db, player):
    board = chess.Board()
    await _seed_edge(db, board, "e2e4", 100)
    await _seed_edge(db, board, "d2d4", 50)
    await _seed_edge(db, board, "c2c4", 10)

    cfg = SamplerConfig(mode=SamplerMode.MIX, seed=42, novelty_prob=0.0)
    pick_a = await sample_opponent_move(db, board=board, config=cfg)
    pick_b = await sample_opponent_move(db, board=board, config=cfg)
    assert pick_a.uci == pick_b.uci
    assert pick_a.source_tier == pick_b.source_tier

    # Different seeds produce (potentially) different picks over many trials.
    seen = set()
    for seed in range(20):
        cfg2 = SamplerConfig(mode=SamplerMode.MIX, seed=seed, novelty_prob=0.0)
        r = await sample_opponent_move(db, board=board, config=cfg2)
        seen.add(r.uci)
    assert len(seen) >= 2, "mix mode should visit multiple moves across seeds"


# --------------------------------------------------------------------------- #
# Mode behaviour
# --------------------------------------------------------------------------- #


async def test_mainline_mode_picks_top_frequency(db, player):
    board = chess.Board()
    await _seed_edge(db, board, "e2e4", 100)
    await _seed_edge(db, board, "d2d4", 50)
    await _seed_edge(db, board, "c2c4", 10)

    cfg = SamplerConfig(mode=SamplerMode.MAINLINE, seed=1, novelty_prob=0.0)
    r = await sample_opponent_move(db, board=board, config=cfg)
    assert r.uci == "e2e4"
    assert r.source_tier == "opening_graph"


async def test_surprise_mode_prefers_rare(db, player):
    board = chess.Board()
    await _seed_edge(db, board, "e2e4", 1000)
    await _seed_edge(db, board, "d2d4", 500)
    await _seed_edge(db, board, "c2c4", 1)

    hits = {"c2c4": 0, "e2e4": 0, "d2d4": 0}
    for seed in range(60):
        cfg = SamplerConfig(mode=SamplerMode.SURPRISE, seed=seed, novelty_prob=0.0)
        r = await sample_opponent_move(db, board=board, config=cfg)
        hits[r.uci] = hits.get(r.uci, 0) + 1
    assert hits["c2c4"] > hits["e2e4"], hits


async def test_opponent_twin_prefers_opponent_tier(db, player):
    """When opponent data exists, use it instead of opening_graph."""
    board = chess.Board()
    # Opening graph: e4 overwhelming.
    _, start = await _seed_edge(db, board, "e2e4", 1000)
    await _seed_edge(db, board, "d2d4", 1000)

    opp = OpponentProfile(
        owner_player_id=player.id,
        display_name="TwinTarget",
        aliases_json=[],
        games_imported=1,
    )
    db.add(opp)
    await db.commit()
    await db.refresh(opp)

    # Opponent plays Nf3 as their favorite (unseen by opening graph here except via c2c4).
    db.add(OpponentPositionMove(
        opponent_profile_id=opp.id,
        position_id=start.id,
        color="white",
        move_uci="g1f3",
        games_count=20,
        wins=10,
        losses=5,
        draws=5,
        recency_weight=1.0,
    ))
    await db.commit()

    cfg = SamplerConfig(mode=SamplerMode.OPPONENT_TWIN, seed=7, novelty_prob=0.0, min_games=1)
    r = await sample_opponent_move(db, board=board, config=cfg, opponent_profile_id=opp.id)
    assert r.source_tier == "opponent_profile"
    assert r.uci == "g1f3"


async def test_tier_fallback_master_then_graph(db, player):
    """Master tier covers a position where opening_graph is thin."""
    board = chess.Board()
    board.push_san("e4")
    # No MoveEdge rows for the position after e4; opening_graph tier returns nothing.
    pos_id = await upsert_position(db, board)
    db.add(MasterMoveStat(
        position_id=pos_id,
        source="lichess_masters",
        move_uci="e7e5",
        games_count=500,
    ))
    db.add(MasterMoveStat(
        position_id=pos_id,
        source="lichess_masters",
        move_uci="c7c5",
        games_count=400,
    ))
    await db.commit()

    cfg = SamplerConfig(mode=SamplerMode.MAINLINE, seed=1)
    r = await sample_opponent_move(db, board=board, config=cfg)
    assert r.source_tier == "master_move_stat"
    assert r.uci in {"e7e5", "c7c5"}


async def test_engine_fallback_when_nothing_in_tree(db, player):
    """Position never seen before: no opponent, no master, no graph → engine."""
    board = chess.Board("4k3/8/8/8/8/8/8/4K3 w - - 0 1")  # KvK
    cfg = SamplerConfig(mode=SamplerMode.MIX, seed=0, engine_depth=4, engine_multipv=2, novelty_prob=0.0)
    r = await sample_opponent_move(db, board=board, config=cfg)
    assert r.source_tier in {"engine_fallback", "legal_random"}
    # Picked move must be legal.
    assert chess.Move.from_uci(r.uci) in board.legal_moves


# --------------------------------------------------------------------------- #
# Legality guard
# --------------------------------------------------------------------------- #


async def test_illegal_tree_rows_are_filtered(db, player):
    """A stale MoveEdge row with an illegal UCI must be dropped, not played."""
    board = chess.Board("4k3/8/8/8/8/8/4P3/4K3 w - - 0 1")
    pos_id = await upsert_position(db, board)
    # Fake a stale edge from this position with an illegal move.
    after = board.copy()
    after.push_san("Kd2")  # legal king move → create a to_position
    to_pos_id = await upsert_position(db, after)
    db.add(MoveEdge(
        from_position_id=pos_id,
        to_position_id=to_pos_id,
        uci="e2e4",  # pawn to e4 — but there's a king on e1 blocking... actually legal: pawn on e2 can advance.
        san="e4",
        games_count=99,
    ))
    # And add a truly illegal edge: pawn to e5 (jumps 2 but blocked none — actually also legal).
    # Use a definitely illegal UCI by picking an occupied square: king to d1 then... let's use a
    # phantom move that can't legally exist from here: Ra1 (no rook). UCI "a1a2".
    db.add(MoveEdge(
        from_position_id=pos_id,
        to_position_id=to_pos_id,
        uci="a1a2",
        san="Ra2",
        games_count=1000,  # highest frequency, so mainline mode would normally pick it
    ))
    await db.commit()

    cfg = SamplerConfig(mode=SamplerMode.MAINLINE, seed=0, novelty_prob=0.0)
    r = await sample_opponent_move(db, board=board, config=cfg)
    assert r.uci != "a1a2"
    # The pick is a real legal move.
    assert chess.Move.from_uci(r.uci) in board.legal_moves


async def test_sampler_never_returns_illegal_in_mix(db, player):
    """Across many seeds and modes, every returned move is legal."""
    board = chess.Board()
    await _seed_edge(db, board, "e2e4", 100)
    await _seed_edge(db, board, "d2d4", 50)
    legal = {m.uci() for m in board.legal_moves}
    for seed in range(10):
        for mode in SamplerMode:
            cfg = SamplerConfig(mode=mode, seed=seed, novelty_prob=0.0,
                                engine_depth=4, engine_multipv=2)
            r = await sample_opponent_move(db, board=board, config=cfg)
            assert r.uci in legal, (seed, mode, r)


@pytest.mark.parametrize("mode", [SamplerMode.MIX, SamplerMode.SURPRISE, SamplerMode.MAINLINE])
def test_choose_is_order_independent(mode):
    ucis = ["e2e4", "d2d4", "g1f3", "c2c4", "b1c3"]
    cands = [Candidate(uci=u, weight=float(i + 1), source_tier="t", games=i + 1)
             for i, u in enumerate(ucis)]
    legal = set(ucis)
    for seed in range(25):
        a = _choose(cands, mode=mode, rng=random.Random(seed), legal=legal)
        b = _choose(list(reversed(cands)), mode=mode, rng=random.Random(seed), legal=legal)
        assert a == b
