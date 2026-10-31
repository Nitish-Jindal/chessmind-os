"""Opening taxonomy, announcements, quiet mode, illegal-move feedback and opening requests.

Real python-chess, real Postgres and real Stockfish (every player move is graded at depth 10).
The taxonomy is the vendored Lichess chess-openings data; nothing is stubbed.
"""

from __future__ import annotations

import chess
import pytest

from chessmind.models import Player
from chessmind.security import create_access_token, hash_password
from chessmind.services import opening_taxonomy as ot


def _board(sans: str) -> chess.Board:
    b = chess.Board()
    for s in sans.split():
        b.push_san(s)
    return b


# --------------------------------------------------------------------------- taxonomy


def test_lookup_names_exact_positions_only():
    assert ot.lookup(chess.Board()) is None
    ruy = ot.lookup(_board("e4 e5 Nf3 Nc6 Bb5"))
    assert (ruy.eco, ruy.name) == ("C60", "Ruy Lopez")
    assert ot.lookup(_board("e4 c6")).name == "Caro-Kann Defense"
    # A transposition reaches the same name.
    assert ot.lookup(_board("Nf3 Nc6 e4 e5 Bb5")).name == "Ruy Lopez"


def test_announcements_skip_generic_names_and_repeat_nothing():
    board, announced, out = chess.Board(), [], []
    for ply, san in enumerate("e4 e5 Nf3 Nc6 Bb5".split(), start=1):
        board.push_san(san)
        e = ot.announcement_for(board, announced)
        if e:
            announced.append(e.name)
            out.append((ply, e.name))
    assert out == [(5, "Ruy Lopez")]
    assert ot.announcement_for(board, announced) is None


@pytest.mark.parametrize("text, label", [
    ("play the London System against me", "London System"),
    ("play the london against me", "London System"),
    ("Play the Caro-Kann against me", "Caro-Kann Defense"),
    ("play the caro kann against me", "Caro-Kann Defense"),
    ("play the Spanish against me", "Ruy Lopez"),
    ("play the Ruy Lopez against me", "Ruy Lopez"),
    ("play the Italian against me", "Italian Game"),
    ("play the Queen's Gambit against me", "Queen's Gambit"),
    ("play the queens gambit against me", "Queen's Gambit"),
    ("play the Sicilian against me", "Sicilian Defense"),
    ("play the Sicilian Defence against me", "Sicilian Defense"),
    ("play the Dutch against me", "Dutch Defense"),
])
def test_parse_named_opening(text, label):
    r = ot.parse_opening_request(text)
    assert r["kind"] == "opening" and r["label"] == label
    assert r["eco"] and r["pgn"]


@pytest.mark.parametrize("text, san", [
    ("play d4 against me", "d4"),
    ("play d 4 against me", "d4"),
    ("play e four against me", "e4"),
    ("play knight f3 against me", "Nf3"),
    ("play Nf3 against me", "Nf3"),
    ("play c4 against me", "c4"),
])
def test_parse_move_request(text, san):
    assert ot.parse_opening_request(text) == {"kind": "move", "san_text": san}


def test_non_requests_and_unknown_names():
    assert ot.parse_opening_request("play d4") is None  # a plain move, not a command
    assert ot.parse_opening_request("knight f3") is None
    assert ot.parse_opening_request("play the banana gambit against me")["kind"] == "unknown"


def test_book_moves_follow_the_requested_family():
    caro = ot.parse_opening_request("play the Caro-Kann against me")
    assert [m.uci() for m in ot.book_moves(_board("e4"), caro)] == ["c7c6"]
    assert [m.uci() for m in ot.book_moves(chess.Board(), caro)] == ["e2e4"]
    london = ot.parse_opening_request("play the London against me")
    assert [m.uci() for m in ot.book_moves(chess.Board(), london)] == ["d2d4"]
    assert ot.book_moves(_board("e4"), london) == []  # left (never reached) the London lines


# --------------------------------------------------------------------------- API helpers


async def _player(db, email: str) -> dict:
    p = Player(email=email, password_hash=hash_password("passwordpassword"), display_name="O",
               preferred_color="white", settings_json={})
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return {"Authorization": f"Bearer {create_access_token(p.id)}"}


async def _start(client, hdr, **kw) -> dict:
    body = {"mode": "blindfold", "player_color": "white", "sampler_mode": "mainline", "seed": 7, **kw}
    r = await client.post("/sessions", json=body, headers=hdr)
    assert r.status_code == 201, r.text
    return r.json()


async def _move(client, hdr, sid, san) -> dict:
    r = await client.post(f"/sessions/{sid}/move", json={"san": san}, headers=hdr)
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- API: announcements


async def test_ruy_lopez_announced_once_after_bb5(client, db):
    hdr = await _player(db, "ruy@example.com")
    s = await _start(client, hdr, opening_request="Ruy Lopez")
    sid = s["id"]
    r1 = await _move(client, hdr, sid, "e4")
    assert r1["opponent_move"]["san"] == "e5" and r1["opponent_move"]["source_tier"] == "requested_opening"
    assert r1["announcements"] == []  # King's Pawn Game is generic
    r2 = await _move(client, hdr, sid, "Nf3")
    assert r2["opponent_move"]["san"] == "Nc6"
    assert r2["announcements"] == []
    r3 = await _move(client, hdr, sid, "Bb5")
    first = r3["announcements"][0]
    assert (first["name"], first["eco"], first["by"], first["ply"]) == ("Ruy Lopez", "C60", "player", 5)
    assert first["text"] == "Ruy Lopez"
    names = [a["name"] for a in r3["announcements"]]
    assert names.count("Ruy Lopez") == 1
    state = (await client.get(f"/sessions/{sid}", headers=hdr)).json()["state_json"]
    assert [a["name"] for a in state["opening_announcements"]].count("Ruy Lopez") == 1


# --------------------------------------------------------------------------- API: quiet mode


async def test_quiet_mode_suppresses_coaching_quizzes_and_announcements(client, db):
    hdr = await _player(db, "quiet@example.com")
    sid = (await _start(client, hdr, quiet=True, opening_request="Ruy Lopez"))["id"]
    for san in ("e4", "Nf3", "g4"):  # g4 is a real mistake: the template would comment on it
        r = await _move(client, hdr, sid, san)
        assert r["quiet"] is True
        assert r["coaching"]["spoken_summary"] == "" and r["coaching"]["correction_san"] is None
        assert r["announcements"] == []
        assert r["opponent_move"]["san"]  # the opponent's move is still announced by the client
    assert (await client.post(f"/sessions/{sid}/quiz", json={}, headers=hdr)).status_code == 409
    # Illegal moves are still rejected in quiet mode.
    bad = await client.post(f"/sessions/{sid}/move", json={"san": "Ke3"}, headers=hdr)
    assert bad.status_code == 422

    s = (await client.patch(f"/sessions/{sid}/settings", json={"quiet": False}, headers=hdr)).json()
    assert s["settings_json"]["quiet"] is False
    legal = (await client.get(f"/sessions/{sid}/legal", headers=hdr)).json()
    r = await _move(client, hdr, sid, legal["legal_moves_san"][0])
    assert r["quiet"] is False and r["coaching"]["spoken_summary"]


# --------------------------------------------------------------------------- API: illegal moves


@pytest.mark.parametrize("payload", [{"san": "Ke2"}, {"san": "Nf6"}, {"uci": "e2e5"}, {"san": "banana"}])
async def test_illegal_move_is_rejected_and_game_does_not_advance(client, db, payload):
    hdr = await _player(db, "illegal@example.com")
    sid = (await _start(client, hdr))["id"]
    r = await client.post(f"/sessions/{sid}/move", json=payload, headers=hdr)
    assert r.status_code == 422
    d = r.json()["detail"]
    assert d["code"] == "illegal_move" and d["message"] == "That's an illegal move, try again."
    assert d["current_fen"] == chess.STARTING_FEN
    state = (await client.get(f"/sessions/{sid}", headers=hdr)).json()["state_json"]
    assert state["move_history_san"] == [] and state["next_to_move"] == "player"


# --------------------------------------------------------------------------- API: opening requests


async def test_caro_kann_requested_as_white(client, db):
    hdr = await _player(db, "caro@example.com")
    sid = (await _start(client, hdr))["id"]
    r = await client.post(f"/sessions/{sid}/opening", json={"text": "play the Caro-Kann against me"}, headers=hdr)
    body = r.json()
    assert body["accepted"] and not body["restarted"]
    assert "Caro-Kann Defense" in body["message"] and "1. e4 c6" in body["message"]
    m = await _move(client, hdr, sid, "e4")
    assert m["opponent_move"]["san"] == "c6"
    assert m["opponent_move"]["source_tier"] == "requested_opening"
    assert [a["name"] for a in m["announcements"]] == ["Caro-Kann Defense"]
    assert m["announcements"][0]["by"] == "opponent"


async def test_black_says_play_d4_and_engine_reopens(client, db):
    hdr = await _player(db, "black-d4@example.com")
    s = await _start(client, hdr, player_color="black")
    assert len(s["state_json"]["move_history_san"]) == 1  # engine opened as White
    r = (await client.post(f"/sessions/{s['id']}/opening", json={"text": "play d4 against me"}, headers=hdr)).json()
    assert r["accepted"] and r["restarted"]
    assert r["opponent_move"]["san"] == "d4" and r["opponent_move"]["source_tier"] == "requested_move"
    state = (await client.get(f"/sessions/{s['id']}", headers=hdr)).json()["state_json"]
    assert state["move_history_san"] == ["d4"] and state["next_to_move"] == "player"


async def test_black_requests_london_then_leaves_book(client, db):
    hdr = await _player(db, "black-london@example.com")
    s = await _start(client, hdr, player_color="black", opening_request="play the London System against me")
    assert s["state_json"]["move_history_san"] == ["d4"]
    m = await _move(client, hdr, s["id"], "d5")
    assert m["opponent_move"]["source_tier"] == "requested_opening"
    assert m["opponent_move"]["san"] in {"Bf4", "Nf3"}
    m2 = await _move(client, hdr, s["id"], "h5")  # no London line continues with ...h5
    assert m2["opponent_move"]["request_event"] == "out_of_book"
    assert m2["opponent_move"]["source_tier"] != "requested_opening"
    assert m2["opening_request"]["status"] == "out_of_book"


async def test_request_validated_by_python_chess(client, db):
    hdr = await _player(db, "bad-request@example.com")
    sid = (await _start(client, hdr))["id"]
    r = (await client.post(f"/sessions/{sid}/opening", json={"text": "play king e7 against me"}, headers=hdr)).json()
    assert r["understood"] and not r["accepted"] and "not a legal move" in r["message"]
    r = (await client.post(f"/sessions/{sid}/opening", json={"text": "play the banana gambit against me"},
                           headers=hdr)).json()
    assert not r["accepted"] and "banana gambit" in r["message"]
    bad = await client.post("/sessions", json={"opening_request": "banana gambit"}, headers=hdr)
    assert bad.status_code == 400


async def test_requested_session_replays_deterministically(client, db):
    hdr = await _player(db, "replay@example.com")
    runs = []
    for _ in range(2):
        s = await _start(client, hdr, player_color="black", sampler_mode="mix", seed=99,
                         opening_request="Queen's Gambit")
        m = await _move(client, hdr, s["id"], "d5")
        runs.append((s["state_json"]["move_history_san"], m["opponent_move"]["san"]))
    assert runs[0] == runs[1] == (["d4"], "c4")
