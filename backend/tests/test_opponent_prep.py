"""Opponent prep report over real games, and a blindfold session against the opponent's twin.

Fixture: the first 40 games of ``scripts/data/opponent_firouzja_150.pgn`` (real games of
Alireza Firouzja, 2021-2022, from the PgnMentor collection). Every count in the report is
cross-checked against an independent python-chess scan of the same PGN text; suggestions
are checked against the persisted ``engine_analysis`` row. Real Postgres and Stockfish.
"""

from __future__ import annotations

import io
from collections import Counter
from pathlib import Path

import chess
import chess.pgn
from sqlalchemy import select

from chessmind.models import EngineAnalysis, OpponentProfile, Player, Position
from chessmind.security import create_access_token, hash_password
from chessmind.services.canonical_fen import canonical_key
from chessmind.services.opponent_import import import_opponent_pgn_text, refresh_sample_buckets

FIXTURE = Path(__file__).resolve().parents[2] / "scripts" / "data" / "opponent_firouzja_150.pgn"
N_GAMES = 40


def _games(n: int) -> list[chess.pgn.Game]:
    fh, out = io.StringIO(FIXTURE.read_text()), []
    while len(out) < n and (g := chess.pgn.read_game(fh)) is not None:
        out.append(g)
    return out


def _color(g: chess.pgn.Game) -> str:
    return "white" if "Firouzja" in g.headers["White"] else "black"


async def _setup(db, email: str):
    p = Player(email=email, password_hash=hash_password("passwordpassword"), display_name="P",
               preferred_color="white", settings_json={})
    db.add(p)
    await db.flush()
    prof = OpponentProfile(owner_player_id=p.id, display_name="Firouzja", aliases_json=[], games_imported=0)
    db.add(prof)
    await db.flush()
    games = _games(N_GAMES)
    stats = await import_opponent_pgn_text(db, owner_player_id=p.id, profile=prof,
                                           pgn_text="\n\n".join(str(g) for g in games))
    assert stats.games == N_GAMES and not stats.errors
    await refresh_sample_buckets(db, prof.id)
    prof.games_imported = stats.games
    await db.commit()
    return p, prof, games, {"Authorization": f"Bearer {create_access_token(p.id)}"}


async def test_prep_report_matches_an_independent_scan(client, db):
    _, prof, games, hdr = await _setup(db, "prep@example.com")
    r = await client.get(f"/opponents/{prof.id}/prep", params={"plies": 8, "lines": 4, "depth": 10}, headers=hdr)
    assert r.status_code == 200, r.text
    rep = r.json()

    for color in ("white", "black"):
        side = rep[f"opponent_as_{color}"]
        mine = [g for g in games if _color(g) == color]
        assert side["games"] == len(mine)
        assert side["you_play"] == ("black" if color == "white" else "white")

        # First moves: same counts as a direct tally.
        firsts = Counter(g.board().san(next(iter(g.mainline_moves()))) for g in mine)
        assert {m["san"]: m["games"] for m in side["first_moves"]} == dict(firsts.most_common(6))

        prefixes = Counter()
        for g in mine:
            b, sans = g.board(), []
            for mv in list(g.mainline_moves())[:8]:
                sans.append(b.san(mv))
                b.push(mv)
                prefixes[tuple(sans)] += 1

        assert side["frequent_lines"], f"no frequent lines as {color}"
        for line in side["frequent_lines"]:
            assert line["games"] == prefixes[tuple(line["san"])] >= side["min_games_for_line"]
            # Every prefix of a frequent line is itself frequent.
            assert all(prefixes[tuple(line["san"][:i])] >= side["min_games_for_line"]
                       for i in range(1, len(line["san"]) + 1))
            sug = line["suggested_response"]
            if sug is None:
                continue
            board = chess.Board(sug["fen"])
            assert board.turn == (chess.WHITE if side["you_play"] == "white" else chess.BLACK)
            board.parse_san(sug["best"]["san"])  # legal (raises otherwise)
            pos = (await db.execute(select(Position).where(Position.canonical_key == canonical_key(board)))).scalar_one()
            row = (await db.execute(select(EngineAnalysis).where(
                EngineAnalysis.position_id == pos.id, EngineAnalysis.depth == 10))).scalars().first()
            assert row is not None and row.results_json[0]["pv_san"][0] == sug["best"]["san"]
            assert sug["best"]["cp_white"] == row.results_json[0].get("cp")

        for br in side["likely_branches"]:
            assert len(br["options"]) >= 2
            assert br["games"] == (prefixes[tuple(br["after_san"])] if br["after_san"] else len(mine))
            for opt in br["options"]:
                assert opt["games"] == prefixes[tuple([*br["after_san"], opt["san"]])] >= side["min_games_for_line"]

    # Owner-only, like the rest of /opponents.
    other = Player(email="stranger@example.com", password_hash=hash_password("passwordpassword"),
                   display_name="S", preferred_color="white", settings_json={})
    db.add(other)
    await db.commit()
    stranger = {"Authorization": f"Bearer {create_access_token(other.id)}"}
    assert (await client.get(f"/opponents/{prof.id}/prep", headers=stranger)).status_code == 404



async def test_blindfold_session_against_the_twin(client, db):
    _, prof, games, hdr = await _setup(db, "twin-blind@example.com")
    r = await client.post("/sessions", json={
        "mode": "blindfold", "player_color": "black", "sampler_mode": "opponent_twin",
        "opponent_profile_id": str(prof.id), "seed": 3,
    }, headers=hdr)
    assert r.status_code == 201, r.text
    s = r.json()
    om = s["state_json"]["last_opponent_move"]
    as_white = {g.board().san(next(iter(g.mainline_moves()))) for g in games if _color(g) == "white"}
    assert s["mode"] == "blindfold"
    assert om["source_tier"] == "opponent_profile" and om["san"] in as_white
