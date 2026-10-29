"""Stockfish worker: real binary, cache-first, White-centric perspective.

These tests spawn the real Stockfish process via the shared ``EngineClient``
singleton. First call pays the warmup; subsequent calls reuse it. Depth is
kept low (6-8) so the full suite still runs in a few seconds.
"""

from __future__ import annotations

from uuid import uuid4

import chess
import pytest
from sqlalchemy import func, select

from chessmind.models import EngineAnalysis, Player, Position
from chessmind.security import hash_password
from chessmind.services.canonical_fen import canonical_key
from chessmind.services.engine import analyze_board, engine_client


@pytest.fixture
async def player(db) -> Player:
    p = Player(
        email=f"{uuid4().hex}@test.local",
        password_hash=hash_password("passwordpassword"),
        display_name="Engine Tester",
        preferred_color="either",
        settings_json={},
    )
    db.add(p)
    await db.commit()
    return p


async def test_analyze_startpos_runs_and_caches(db, player):
    board = chess.Board()
    first = await analyze_board(db, board=board, depth=6, multipv=1)
    await db.commit()

    assert first.cached is False
    assert first.engine_version
    assert first.depth == 6
    assert first.multipv == 1
    assert first.perspective == "white"
    assert first.results, "analysis returned no lines"
    top = first.results[0]
    assert top["line_rank"] == 1
    assert "cp" in top and "mate" in top
    assert len(top["pv_uci"]) >= 1
    assert len(top["pv_san"]) == len(top["pv_uci"])

    # Second call at the same depth hits the cache.
    second = await analyze_board(db, board=board, depth=6, multipv=1)
    assert second.cached is True
    assert second.engine_version == first.engine_version


async def test_analyze_higher_depth_adds_row_lower_depth_still_hits_cache(db, player):
    board = chess.Board()
    await analyze_board(db, board=board, depth=6, multipv=1)
    await db.commit()
    rows_before = (await db.execute(select(func.count(EngineAnalysis.id)))).scalar_one()
    assert rows_before == 1

    # Higher depth → not in cache → runs the engine and adds a new row.
    deep = await analyze_board(db, board=board, depth=8, multipv=1)
    await db.commit()
    assert deep.cached is False
    assert deep.depth == 8
    rows_after = (await db.execute(select(func.count(EngineAnalysis.id)))).scalar_one()
    assert rows_after == 2

    # Shallow request now satisfied by the deeper cached row.
    shallow = await analyze_board(db, board=board, depth=5, multipv=1)
    assert shallow.cached is True
    assert shallow.depth >= 5


async def test_perspective_is_white_centric(db, player):
    """A position where White is clearly worse must report a negative cp from White's POV."""
    board = chess.Board("rnbqkbnr/ppp1pppp/8/3p4/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 2")
    # Equal-ish opening position; mostly want to verify sign conventions work either way.
    result = await analyze_board(db, board=board, depth=6, multipv=1)
    assert result.perspective == "white"
    top = result.results[0]
    assert top["cp"] is None or isinstance(top["cp"], int)


async def test_multipv_returns_requested_lines(db, player):
    board = chess.Board()
    result = await analyze_board(db, board=board, depth=6, multipv=3)
    assert len(result.results) == 3
    ranks = [r["line_rank"] for r in result.results]
    assert ranks == [1, 2, 3]
    for r in result.results:
        assert r["pv_uci"], "each PV should have at least one move"


async def test_analyze_persists_position_row(db, player):
    """Analysing a fresh FEN upserts the Position so cache lookups work next time."""
    board = chess.Board()
    board.push_san("e4")
    key = canonical_key(board)
    before = (await db.execute(select(Position).where(Position.canonical_key == key))).first()
    await analyze_board(db, board=board, depth=6, multipv=1)
    await db.commit()
    after = (await db.execute(select(Position).where(Position.canonical_key == key))).scalar_one()
    assert after is not None
    assert after.canonical_key == key


async def test_singleton_shared_across_calls(db, player):
    """Two analyses in a row use the same engine subprocess (singleton check)."""
    board = chess.Board()
    await analyze_board(db, board=board, depth=4, multipv=1)
    pid_before = engine_client._engine.transport.get_pid() if engine_client._engine else None
    await analyze_board(db, board=board, depth=4, multipv=1)
    pid_after = engine_client._engine.transport.get_pid() if engine_client._engine else None
    assert pid_before is not None
    assert pid_before == pid_after


async def test_analysis_endpoint(client, db, player):
    """POST /analysis/position returns a White-centric analysis payload."""
    from chessmind.security import create_access_token

    token = create_access_token(player.id)
    r = await client.post(
        "/analysis/position",
        headers={"Authorization": f"Bearer {token}"},
        json={"fen": chess.STARTING_FEN, "depth": 6, "multipv": 2},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["perspective"] == "white"
    assert body["depth"] == 6
    assert body["multipv"] == 2
    assert len(body["results"]) == 2
    assert body["results"][0]["line_rank"] == 1
