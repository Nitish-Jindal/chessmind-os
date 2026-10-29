"""/openings/tree returns the root + edges ordered by games_count desc."""

from __future__ import annotations

from uuid import uuid4

import pytest

from chessmind.models import Player
from chessmind.security import hash_password
from chessmind.services.pgn_import import import_pgn_sync


ITALIAN_PGN = """[Event "a"]
[Result "1-0"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 1-0
"""
CARO_PGN = """[Event "b"]
[Result "0-1"]

1. e4 c6 2. d4 d5 0-1
"""
QG_PGN = """[Event "c"]
[Result "1/2-1/2"]

1. d4 d5 2. c4 e6 1/2-1/2
"""


@pytest.fixture
async def seeded_player(db):
    p = Player(
        email=f"{uuid4().hex}@test.local",
        password_hash=hash_password("passwordpassword"),
        display_name="Seeded",
        preferred_color="either",
        settings_json={},
    )
    db.add(p)
    await db.commit()
    for pgn in (ITALIAN_PGN, CARO_PGN, QG_PGN):
        await import_pgn_sync(db, player_id=p.id, pgn_text=pgn)
    await db.commit()
    return p


async def test_openings_tree_from_startpos(client, seeded_player):
    r = await client.get("/openings/tree")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["total_positions"] > 0
    assert body["total_edges"] > 0

    root = body["root"]
    sans = [e["san"] for e in root["edges"]]
    assert "e4" in sans
    assert "d4" in sans

    # Only first-move edges leave startpos: ITALIAN + CARO play 1.e4, QG plays 1.d4.
    e4 = next(e for e in root["edges"] if e["san"] == "e4")
    d4 = next(e for e in root["edges"] if e["san"] == "d4")
    assert e4["games_count"] == 2
    assert d4["games_count"] == 1
    # Ordering: desc by games_count, so e4 first.
    assert root["edges"][0]["san"] == "e4"


async def test_openings_tree_edge_counts_match_first_move(client, seeded_player):
    r = await client.get("/openings/tree")
    body = r.json()
    edges = {e["san"]: e for e in body["root"]["edges"]}
    # Three games, two open with 1.e4, one with 1.d4.
    assert edges["e4"]["games_count"] == 2
    assert edges["d4"]["games_count"] == 1
    # Results: e4 played in a 1-0 (Italian) and a 0-1 (Caro)
    assert edges["e4"]["white_wins"] == 1
    assert edges["e4"]["black_wins"] == 1
    assert edges["d4"]["draws"] == 1  # Queen's Gambit was 1/2-1/2


async def test_openings_tree_limit_param(client, seeded_player):
    r = await client.get("/openings/tree?limit=1")
    assert r.status_code == 200
    body = r.json()
    assert len(body["root"]["edges"]) == 1
    # The top edge by games_count is e4 (2 games).
    assert body["root"]["edges"][0]["san"] == "e4"


async def test_openings_tree_min_games_filter(client, seeded_player):
    r = await client.get("/openings/tree?min_games=2")
    body = r.json()
    sans = [e["san"] for e in body["root"]["edges"]]
    assert sans == ["e4"]  # only e4 reaches 2 games from the root


async def test_openings_tree_fen_query(client, seeded_player):
    """Request the position after 1.e4 and expect e5 + c6 as the two outgoing edges."""
    import chess

    board = chess.Board()
    board.push_san("e4")
    r = await client.get("/openings/tree", params={"fen": board.fen()})
    assert r.status_code == 200, r.text
    body = r.json()
    sans = {e["san"] for e in body["root"]["edges"]}
    assert sans == {"e5", "c6"}
