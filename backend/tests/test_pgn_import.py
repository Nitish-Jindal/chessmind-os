"""PGN import: recursive variation walk, mainline flag, comments/NAGs, transpositions."""

from __future__ import annotations

from uuid import uuid4

import chess
import chess.pgn
import io
import pytest
from sqlalchemy import func, select

from chessmind.models import Game, MoveEdge, Player, Position, RepertoireLine
from chessmind.services.pgn_import import import_pgn_sync, walk_game
from chessmind.security import hash_password


ITALIAN_PGN = """[Event "Italian"]
[White "A"]
[Black "B"]
[Result "1-0"]
[ECO "C50"]
[Opening "Italian Game"]

1. e4 {King's pawn} e5 $14 2. Nf3 Nc6 3. Bc4 (3. Bb5 {Spanish instead} a6 4. Ba4) 3... Bc5 1-0
"""

CARO_KANN_PGN = """[Event "Caro"]
[White "C"]
[Black "D"]
[Result "0-1"]
[ECO "B10"]

1. e4 c6 2. d4 d5 3. Nc3 dxe4 0-1
"""


# ---------------------------------------------------------------------------
# Pure-Python walker tests
# ---------------------------------------------------------------------------


def test_walker_marks_mainline_vs_variation():
    game = chess.pgn.read_game(io.StringIO(ITALIAN_PGN))
    visits = list(walk_game(game))

    mainlines = [(v.san, v.is_mainline) for v in visits]
    # Mainline: e4, e5, Nf3, Nc6, Bc4, Bc5
    main_sans = [v.san for v in visits if v.is_mainline]
    assert main_sans == ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"]

    # Variation 3.Bb5 a6 4.Ba4 — none should be mainline.
    variation_sans = [v.san for v in visits if not v.is_mainline]
    assert "Bb5" in variation_sans
    assert "a6" in variation_sans
    assert "Ba4" in variation_sans
    assert all(san != "Bc4" or ml for san, ml in mainlines if san != "Bb5")


def test_walker_preserves_comment_and_nag():
    game = chess.pgn.read_game(io.StringIO(ITALIAN_PGN))
    visits = {v.san: v for v in walk_game(game)}
    assert visits["e4"].comment == "King's pawn"
    assert visits["e5"].nags == (14,)  # $14 = ⩲
    assert visits["Bb5"].comment == "Spanish instead"


# ---------------------------------------------------------------------------
# DB-level import tests
# ---------------------------------------------------------------------------


@pytest.fixture
async def player(db) -> Player:
    p = Player(
        email=f"{uuid4().hex}@test.local",
        password_hash=hash_password("passwordpassword"),
        display_name="Importer",
        preferred_color="either",
        settings_json={},
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def test_import_single_game_persists_graph(db, player):
    stats = await import_pgn_sync(db, player_id=player.id, pgn_text=ITALIAN_PGN)
    await db.commit()

    assert stats.games == 1
    assert stats.errors == []

    games = (await db.execute(select(Game))).scalars().all()
    assert len(games) == 1
    assert games[0].eco == "C50"

    # Positions: startpos + 6 mainline + 3 sideline (after 3.Bb5, 3...a6, 4.Ba4) = at least 10,
    # but shared prefixes mean we expect specifically: start, after e4, after e5, after Nf3,
    # after Nc6, after Bc4, after Bc5 (mainline, 7 positions), then after Bb5, after a6, after Ba4
    # (variation, 3 new positions). Total: 10.
    positions = (await db.execute(select(func.count(Position.id)))).scalar_one()
    assert positions == 10

    # Edges: 6 mainline + 3 variation = 9
    edges = (await db.execute(select(func.count(MoveEdge.id)))).scalar_one()
    assert edges == 9


async def test_variation_rows_flagged_not_mainline(db, player):
    await import_pgn_sync(db, player_id=player.id, pgn_text=ITALIAN_PGN)
    await db.commit()

    # The Bb5 move (3.Bb5) should produce a repertoire_line with is_mainline=False.
    rows = (await db.execute(select(RepertoireLine))).scalars().all()
    by_uci = {r.preferred_uci: r for r in rows}
    assert "f1b5" in by_uci  # 3.Bb5
    assert by_uci["f1b5"].is_mainline is False
    # And the mainline 3.Bc4 is True.
    assert "f1c4" in by_uci
    assert by_uci["f1c4"].is_mainline is True


async def test_comment_and_nag_land_on_repertoire_row(db, player):
    await import_pgn_sync(db, player_id=player.id, pgn_text=ITALIAN_PGN)
    await db.commit()

    rows = (await db.execute(select(RepertoireLine))).scalars().all()
    by_uci = {r.preferred_uci: r for r in rows}
    assert by_uci["e2e4"].comments == "King's pawn"
    assert by_uci["e7e5"].nags_json == [14]
    assert by_uci["f1b5"].comments == "Spanish instead"


async def test_transposition_converges(db, player):
    """1.e4 c6 2.d4 d5 and 1.d4 d5 2.e4 c6 reach the same position after move 2."""
    pgn_order_a = """[Event "a"]
[Result "*"]

1. e4 c6 2. d4 d5 *
"""
    pgn_order_b = """[Event "b"]
[Result "*"]

1. d4 d5 2. e4 c6 *
"""
    await import_pgn_sync(db, player_id=player.id, pgn_text=pgn_order_a)
    await import_pgn_sync(db, player_id=player.id, pgn_text=pgn_order_b)
    await db.commit()

    # The target FEN after both lines is the same board.
    target_board = chess.Board()
    for san in ["e4", "c6", "d4", "d5"]:
        target_board.push_san(san)
    from chessmind.services.canonical_fen import canonical_key
    target_key = canonical_key(target_board)

    rows = (await db.execute(
        select(Position).where(Position.canonical_key == target_key)
    )).scalars().all()
    assert len(rows) == 1, "transposition should map to exactly one Position row"


async def test_import_bumps_edge_counts_across_games(db, player):
    """Importing a game that replays 1.e4 e5 2.Nf3 Nc6 bumps those edges' games_count."""
    await import_pgn_sync(db, player_id=player.id, pgn_text=ITALIAN_PGN)
    await db.commit()

    # Grab the e2e4 edge count after import 1.
    from chessmind.services.canonical_fen import canonical_key as ck
    startpos = chess.Board()
    key = ck(startpos)
    start_pos_id = (await db.execute(
        select(Position.id).where(Position.canonical_key == key)
    )).scalar_one()

    e4_edge = (await db.execute(
        select(MoveEdge).where(MoveEdge.from_position_id == start_pos_id, MoveEdge.uci == "e2e4")
    )).scalar_one()
    assert e4_edge.games_count == 1

    # Import a second game that also plays 1.e4.
    await import_pgn_sync(db, player_id=player.id, pgn_text=CARO_KANN_PGN)
    await db.commit()
    await db.refresh(e4_edge)
    assert e4_edge.games_count == 2
    assert e4_edge.white_wins == 1  # ITALIAN result 1-0
    assert e4_edge.black_wins == 1  # CARO result 0-1


async def test_pgn_import_endpoint_async(client, player):
    """POST /imports/pgn creates an ImportJob and completes in the background."""
    # Log in
    from chessmind.security import create_access_token
    token = create_access_token(player.id)
    headers = {"Authorization": f"Bearer {token}"}

    r = await client.post(
        "/imports/pgn",
        headers=headers,
        json={"pgn": ITALIAN_PGN, "source": "test"},
    )
    assert r.status_code == 202, r.text
    body = r.json()
    job_id = body["id"]
    assert body["status"] in ("queued", "running", "succeeded")

    # Poll until done.
    import asyncio
    for _ in range(40):
        r = await client.get(f"/imports/{job_id}", headers=headers)
        assert r.status_code == 200
        body = r.json()
        if body["status"] in ("succeeded", "failed", "partial"):
            break
        await asyncio.sleep(0.1)
    assert body["status"] == "succeeded", body
    assert body["artifacts_json"]["games"] == 1


async def _run_import(client, headers, body):
    import asyncio

    r = await client.post("/imports/pgn", headers=headers, json=body)
    assert r.status_code == 202, r.text
    job_id = r.json()["id"]
    for _ in range(60):
        r = await client.get(f"/imports/{job_id}", headers=headers)
        if r.json()["status"] in ("succeeded", "failed", "partial"):
            break
        await asyncio.sleep(0.1)
    return r.json()


async def test_corpus_intent_feeds_graph_but_not_repertoire(db, client, player):
    from chessmind.security import create_access_token

    headers = {"Authorization": f"Bearer {create_access_token(player.id)}"}
    job = await _run_import(client, headers, {"pgn": ITALIAN_PGN, "intent": "corpus"})
    assert job["status"] == "succeeded", job
    assert job["kind"] == "corpus_pgn"
    assert job["artifacts_json"]["games"] == 1
    assert job["artifacts_json"]["repertoire_lines"] == 0
    assert (await db.execute(select(func.count()).select_from(MoveEdge))).scalar_one() > 0
    assert (await db.execute(select(func.count()).select_from(RepertoireLine))).scalar_one() == 0


async def test_repertoire_intent_creates_repertoire_lines(db, client, player):
    from chessmind.security import create_access_token

    headers = {"Authorization": f"Bearer {create_access_token(player.id)}"}
    job = await _run_import(client, headers, {"pgn": ITALIAN_PGN, "intent": "repertoire"})
    assert job["status"] == "succeeded", job
    assert job["kind"] == "player_pgn"
    assert (await db.execute(select(func.count()).select_from(RepertoireLine))).scalar_one() > 0


async def test_opponent_intent_routes_to_opponent_profile(db, client, player):
    from chessmind.models import OpponentPositionMove, OpponentProfile
    from chessmind.security import create_access_token

    headers = {"Authorization": f"Bearer {create_access_token(player.id)}"}
    body = {"pgn": ITALIAN_PGN, "intent": "opponent"}

    r = await client.post("/imports/pgn", headers=headers, json=body)
    assert r.status_code == 422  # opponent_profile_id is required
    r = await client.post(
        "/imports/pgn", headers=headers, json={**body, "opponent_profile_id": str(uuid4())}
    )
    assert r.status_code == 404

    r = await client.post("/opponents", headers=headers, json={"display_name": "A"})
    assert r.status_code == 201, r.text
    opp_id = r.json()["id"]
    job = await _run_import(client, headers, {**body, "opponent_profile_id": opp_id})
    assert job["kind"] == "opponent_pgn"
    assert job["status"] == "succeeded", job
    assert (await db.execute(select(func.count()).select_from(RepertoireLine))).scalar_one() == 0
    profile = await db.get(OpponentProfile, opp_id)
    await db.refresh(profile)
    assert profile.games_imported == 1
    assert (
        await db.execute(select(func.count()).select_from(OpponentPositionMove))
    ).scalar_one() > 0
