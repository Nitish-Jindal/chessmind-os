"""Position questions: parsing, board-grounded answers, quiz scoring, and the API.

Answers are cross-checked against an independent scan of ``board.piece_map()``
rather than re-calling the code under test. API tests run real Postgres and
real Stockfish (evaluation answers come from a persisted engine_analysis row).
"""

from __future__ import annotations

import random

import chess
import pytest

from chessmind.models import Player
from chessmind.security import create_access_token, hash_password
from chessmind.services import position_questions as pq

# White: Ke1, Bc4 + Ne4 (both attacked by the d5 pawn, both undefended → hanging), pawns a2 h2.
# Black: Ke8, Qd8, pawn d5 (attacked by Bc4 only, defended by Qd8 → not hanging). White to move.
HANG_FEN = "3qk3/8/8/3p4/2B1N3/8/P6P/4K3 w - - 0 1"


def squares_of(board: chess.Board, piece_type: int, color: chess.Color) -> list[str]:
    return sorted(chess.square_name(sq) for sq, pc in board.piece_map().items()
                  if pc.piece_type == piece_type and pc.color == color)


@pytest.mark.parametrize("text,kind,extra", [
    ("where is my knight", "piece_location", {"piece": chess.KNIGHT, "side": "player"}),
    ("Where are your bishops?", "piece_location", {"piece": chess.BISHOP, "side": "opponent"}),
    ("can you tell me where the opponent's queen is", "piece_location", {"piece": chess.QUEEN, "side": "opponent"}),
    ("where is the black king", "piece_location", {"piece": chess.KING, "side": "black"}),
    ("where are my rocks", "piece_location", {"piece": chess.ROOK, "side": "player"}),
    ("what's on e four", "square_content", {"square": "e4"}),
    ("what is on see five", "square_content", {"square": "c5"}),
    ("is there a knight on f3", "square_content", {"square": "f3"}),
    ("anything hanging?", "hanging_pieces", {"side": "player"}),
    ("is your rook hanging", "hanging_pieces", {"side": "opponent"}),
    ("whose move is it", "side_to_move", {}),
    ("is it my turn", "side_to_move", {}),
    ("what's the eval", "evaluation", {}),
    ("who is better", "evaluation", {}),
])
def test_parse_question(text, kind, extra):
    q = pq.parse_question(text)
    assert q is not None and q.kind == kind
    for k, v in extra.items():
        assert getattr(q, k) == v


@pytest.mark.parametrize("text", ["e4", "knight f3", "castle kingside", "takes", "hello there"])
def test_moves_and_noise_are_not_questions(text):
    assert pq.parse_question(text) is None


def test_answers_match_independent_board_scan():
    board = chess.Board()
    board.push_san("e4"); board.push_san("e5"); board.push_san("Nf3"); board.push_san("Nc6")
    a = pq.answer_question(pq.Question("piece_location", piece=chess.KNIGHT, side="player"), board, "white")
    assert a.facts["squares"] == squares_of(board, chess.KNIGHT, chess.WHITE) == ["b1", "f3"]
    a = pq.answer_question(pq.Question("piece_location", piece=chess.KNIGHT, side="opponent"), board, "white")
    assert a.facts["squares"] == squares_of(board, chess.KNIGHT, chess.BLACK) == ["c6", "g8"]
    assert "c6 and g8" in a.text

    for sq in ["e4", "c6", "d4"]:
        a = pq.answer_question(pq.Question("square_content", square=sq), board, "white")
        pc = board.piece_map().get(chess.parse_square(sq))
        assert a.facts["piece"] == (chess.piece_name(pc.piece_type) if pc else None)

    a = pq.answer_question(pq.Question("side_to_move"), board, "black")
    assert a.facts == {"turn": "white", "player_to_move": False}


def test_hanging_answer():
    board = chess.Board(HANG_FEN)
    mine = pq.answer_question(pq.Question("hanging_pieces", side="player"), board, "white")
    assert [(h["square"], h["piece"], h["attackers"], h["defenders"]) for h in mine.facts["hanging"]] == [
        ("c4", "bishop", 1, 0), ("e4", "knight", 1, 0)]
    assert "bishop on c4" in mine.text and "knight on e4" in mine.text
    theirs = pq.answer_question(pq.Question("hanging_pieces", side="opponent"), board, "white")
    assert theirs.facts["hanging"] == [] and "None of the opponent's pieces" in theirs.text


def test_eval_wording_is_white_centric_with_player_view():
    ev = pq.EvalEvidence(cp=-180, mate=None, engine_version="17.1", depth=10, cached=True, position_id="x")
    a = pq.answer_question(pq.Question("evaluation"), chess.Board(), "black", ev)
    assert "-1.80 for White" in a.text and "Black is better" in a.text and "+1.80 from your side" in a.text
    assert a.facts["bucket"] == "black"
    assert pq.eval_bucket(40, None) == "equal" and pq.eval_bucket(None, -3) == "black"


def test_quiz_is_deterministic_per_seed():
    board = chess.Board(HANG_FEN)
    a = [pq.make_quiz(board, "white", random.Random("7:quiz:0")).prompt for _ in range(3)]
    assert len(set(a)) == 1


@pytest.mark.parametrize("kind,expected,text,score", [
    ("piece_location", {"squares": ["b1", "f3"]}, "b1 and f3", 1.0),
    ("piece_location", {"squares": ["b1", "f3"]}, "bee one and eff three", 1.0),
    ("piece_location", {"squares": ["b1", "f3"]}, "f3", 0.5),
    ("piece_location", {"squares": ["b1", "f3"]}, "f3 and g5", 1 / 3),
    ("hanging_pieces", {"squares": []}, "none", 1.0),
    ("hanging_pieces", {"squares": ["e4"]}, "nothing is hanging", 0.0),
    ("hanging_pieces", {"squares": ["e4"]}, "my knight on e4", 1.0),
    ("square_content", {"square": "e4", "piece": "knight", "color": "white"}, "white knight", 1.0),
    ("square_content", {"square": "e4", "piece": "knight", "color": "white"}, "my knight", 1.0),
    ("square_content", {"square": "e4", "piece": "knight", "color": "white"}, "a knight", 0.5),
    ("square_content", {"square": "e4", "piece": "knight", "color": "white"}, "black bishop", 0.0),
    ("square_content", {"square": "d4", "piece": None, "color": None}, "it's empty", 1.0),
    ("side_to_move", {"turn": "white"}, "me", 1.0),
    ("side_to_move", {"turn": "white"}, "black", 0.0),
    ("evaluation", {"bucket": "equal"}, "about equal", 1.0),
    ("evaluation", {"bucket": "white"}, "I'm better", 1.0),
    ("evaluation", {"bucket": "black"}, "you are winning", 1.0),
    ("evaluation", {"bucket": "black"}, "white is winning", 0.0),
])
def test_score_answer(kind, expected, text, score):
    s = pq.score_answer(kind, expected, text, "white")
    assert s.understood
    assert s.score == pytest.approx(round(score, 3))


@pytest.mark.parametrize("kind,expected", [
    ("piece_location", {"squares": ["b1"]}),
    ("square_content", {"square": "e4", "piece": None, "color": None}),
    ("side_to_move", {"turn": "white"}),
    ("evaluation", {"bucket": "equal"}),
])
def test_unparseable_answer_is_not_scored(kind, expected):
    assert pq.score_answer(kind, expected, "umm let me think", "white").understood is False


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


async def _player(db, email: str) -> tuple[Player, dict]:
    p = Player(email=email, password_hash=hash_password("passwordpassword"), display_name="Q",
               preferred_color="white", settings_json={})
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p, {"Authorization": f"Bearer {create_access_token(p.id)}"}


async def _session(client, hdr, fen: str | None = None, mode: str = "blindfold") -> str:
    body = {"mode": mode, "player_color": "white", "sampler_mode": "mainline", "seed": 11}
    if fen:
        body["starting_fen"] = fen
    r = await client.post("/sessions", json=body, headers=hdr)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_ask_endpoint_answers_from_the_session_board(client, db):
    _, hdr = await _player(db, "ask@example.com")
    sid = await _session(client, hdr, HANG_FEN)

    r = await client.post(f"/sessions/{sid}/ask", json={"text": "where is my knight"}, headers=hdr)
    body = r.json()
    assert r.status_code == 200 and body["understood"] and body["kind"] == "piece_location"
    assert body["facts"]["squares"] == ["e4"] and body["answer_text"] == "Your knight is on e4."

    # Second STT alternative is used when the first does not parse.
    r = await client.post(f"/sessions/{sid}/ask", json={"text": "uh huh", "alternatives": ["anything hanging"]},
                          headers=hdr)
    assert r.json()["kind"] == "hanging_pieces"
    assert [h["square"] for h in r.json()["facts"]["hanging"]] == ["c4", "e4"]

    r = await client.post(f"/sessions/{sid}/ask", json={"text": "play the move"}, headers=hdr)
    assert r.json()["understood"] is False and "where is my knight" in r.json()["answer_text"]

    r = await client.post(f"/sessions/{sid}/ask", json={"text": "whose move is it"}, headers=hdr)
    assert r.json()["facts"] == {"turn": "white", "player_to_move": True}
    assert r.json()["questions_asked"] == 3


async def test_eval_answer_uses_persisted_stockfish_row(client, db):
    _, hdr = await _player(db, "eval@example.com")
    sid = await _session(client, hdr, HANG_FEN, mode="guided")
    first = (await client.post(f"/sessions/{sid}/ask", json={"text": "what's the eval"}, headers=hdr)).json()
    second = (await client.post(f"/sessions/{sid}/ask", json={"text": "who is better"}, headers=hdr)).json()
    f1, f2 = first["facts"], second["facts"]
    assert first["kind"] == "evaluation" and f1["depth"] == pq.EVAL_DEPTH
    assert f1["engine_version"] and f1["position_id"]
    assert f2["cached"] is True and f2["cp_white"] == f1["cp_white"]  # served from engine_analysis
    assert f"depth {pq.EVAL_DEPTH}" in first["answer_text"]


async def test_quiz_scoring_and_tally(client, db):
    _, hdr = await _player(db, "quiz@example.com")
    sid = await _session(client, hdr, HANG_FEN)

    q = (await client.post(f"/sessions/{sid}/quiz", json={"kind": "piece_location"}, headers=hdr)).json()
    assert q["kind"] == "piece_location" and q["prompt"]
    board = chess.Board(HANG_FEN)
    # Work out the truth independently from the prompt wording.
    color = chess.WHITE if "your opponent" not in q["prompt"] else chess.BLACK
    piece = next(pt for pt in chess.PIECE_TYPES if f" {chess.piece_name(pt)}s." in q["prompt"])
    truth = " and ".join(squares_of(board, piece, color))

    r = await client.post(f"/sessions/{sid}/quiz/{q['question_id']}/answer", json={"text": "umm"}, headers=hdr)
    assert r.json()["understood"] is False  # still open, can be answered
    r = await client.post(f"/sessions/{sid}/quiz/{q['question_id']}/answer", json={"text": truth}, headers=hdr)
    a = r.json()
    assert a["correct"] is True and a["score"] == 1.0 and a["feedback"].startswith("Correct")

    r = await client.post(f"/sessions/{sid}/quiz/{q['question_id']}/answer", json={"text": truth}, headers=hdr)
    assert r.status_code == 409

    q2 = (await client.post(f"/sessions/{sid}/quiz", json={"kind": "square_content"}, headers=hdr)).json()
    a2 = (await client.post(f"/sessions/{sid}/quiz/{q2['question_id']}/answer",
                            json={"text": "it's empty"}, headers=hdr)).json()
    sq = q2["prompt"].split()[3].rstrip("?")
    assert a2["correct"] is (board.piece_at(chess.parse_square(sq)) is None)

    q3 = (await client.post(f"/sessions/{sid}/quiz", json={"kind": "hanging_pieces"}, headers=hdr)).json()
    a3 = (await client.post(f"/sessions/{sid}/quiz/{q3['question_id']}/answer",
                            json={"text": "none"}, headers=hdr)).json()
    # Mine: c4 + e4 hang; theirs: nothing does — so "none" is right only for the opponent.
    assert a3["correct"] is ("your opponent" in q3["prompt"])

    tally = (await client.get(f"/sessions/{sid}/questions", headers=hdr)).json()
    assert tally["quizzed"] == 3 and tally["answered"] == 3
    assert tally["correct"] == 1 + int(a2["correct"]) + int(a3["correct"])


async def test_seeded_quiz_sequence_replays(client, db):
    _, hdr = await _player(db, "seedq@example.com")
    prompts = []
    for _ in range(2):
        sid = await _session(client, hdr, HANG_FEN)
        prompts.append([(await client.post(f"/sessions/{sid}/quiz", json={}, headers=hdr)).json()["prompt"]
                        for _ in range(4)])
    assert prompts[0] == prompts[1]


async def test_questions_are_private_to_the_session_owner(client, db):
    _, hdr = await _player(db, "own1@example.com")
    _, other = await _player(db, "own2@example.com")
    sid = await _session(client, hdr)
    q = (await client.post(f"/sessions/{sid}/quiz", json={"kind": "side_to_move"}, headers=hdr)).json()
    assert (await client.post(f"/sessions/{sid}/ask", json={"text": "whose move"}, headers=other)).status_code == 404
    r = await client.post(f"/sessions/{sid}/quiz/{q['question_id']}/answer", json={"text": "white"}, headers=other)
    assert r.status_code == 404
