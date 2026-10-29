"""SAN, castling, en passant, promotion basics.

These are python-chess's own guarantees — we assert them here anyway so a
future SAN wrapper or move-resolver layer cannot regress legality.
"""

import chess
import chess.pgn
import io
import pytest


def test_san_parsing_mainline():
    board = chess.Board()
    for san in ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4", "Nf6", "O-O"]:
        move = board.parse_san(san)
        board.push(move)
    # Short castle: K lands on g1, R on f1, h1 is empty.
    assert board.piece_at(chess.G1).symbol() == "K"
    assert board.piece_at(chess.F1).symbol() == "R"
    assert board.piece_at(chess.H1) is None


def test_castling_queenside():
    board = chess.Board()
    for san in ["d4", "d5", "Nc3", "Nc6", "Bf4", "Bf5", "Qd2", "Qd7", "O-O-O", "O-O-O"]:
        board.push_san(san)
    assert board.piece_at(chess.C1).symbol() == "K"
    assert board.piece_at(chess.C8).symbol() == "k"


def test_en_passant_capture():
    board = chess.Board()
    for san in ["e4", "Nf6", "e5", "d5"]:
        board.push_san(san)
    assert board.has_legal_en_passant()
    board.push_san("exd6")
    # The black d5 pawn has been removed via en passant.
    assert board.piece_at(chess.D5) is None
    assert board.piece_at(chess.D6).symbol() == "P"


def test_promotion_to_queen():
    # Force a quick promotion scenario.
    board = chess.Board("8/P7/8/8/8/8/k6K/8 w - - 0 1")
    board.push_san("a8=Q+")
    assert board.piece_at(chess.A8).symbol() == "Q"


def test_illegal_move_raises():
    board = chess.Board()
    with pytest.raises(chess.IllegalMoveError):
        board.push_san("Ke2")  # king can't move through pawn on first move


def test_pgn_parses_mainline_and_variation():
    pgn = """[Event "Test"]
[Result "*"]

1. e4 e5 2. Nf3 (2. d4 exd4 3. Nf3) 2... Nc6 3. Bb5 *
"""
    game = chess.pgn.read_game(io.StringIO(pgn))
    assert game is not None
    mainline = [n.san() for n in game.mainline()]
    assert mainline[:4] == ["e4", "e5", "Nf3", "Nc6"]

    # Walk variations: the second variation at node after 1...e5 should exist.
    node = game
    node = node.variations[0]  # 1.e4
    node = node.variations[0]  # 1...e5
    # node is the position after 1...e5; it has two children: 2.Nf3 (mainline) and 2.d4 (variation)
    assert len(node.variations) == 2
    assert node.variations[0].san() == "Nf3"
    assert node.variations[1].san() == "d4"
